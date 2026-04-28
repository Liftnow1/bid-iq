"""Classify-only pass over the Phase 2 staging buckets.

Walks the priority + secondary staging dirs, classifies each file with
the v2.1 prompt, and writes a CSV sort report at the staging root.
**No database writes.** Excluded-personal files are moved to
99-EXCLUDED-PERSONAL/ before classification so they never reach the
Claude API.

Usage:
  python scripts/sort_report/run_sort_report.py [--dry-run] [--limit N]
                                                [--staging-root DIR]
                                                [--model MODEL]
                                                [--shuffle [--seed N]]

Required env:
  ANTHROPIC_API_KEY            — used for the classifier call
  BIDIQ_EXCLUSION_STRINGS      — pipe-delimited PII strings (optional
                                 but strongly recommended; see README)
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.sort_report.exclusion_config import EXCLUSION_STRINGS  # noqa: E402

# `bidiq.ingest`, `anthropic`, and `pypdf` are imported lazily inside
# main() / extract_pdf_text() so `--dry-run` works in environments that
# don't have those installed (e.g., a code-review sandbox).

DEFAULT_STAGING_ROOT = Path(r"C:\Users\Paul\Desktop\bidiq-phase2-staging")
PRIORITY_SUBDIR = "02-PRIORITY-INGEST"
SECONDARY_SUBDIR = "03-SECONDARY-INGEST"
EXCLUDED_SUBDIR = "99-EXCLUDED-PERSONAL"
SORT_REPORT_NAME = "SORT-REPORT.csv"

# Pinned to the same Sonnet 4 snapshot the rest of the bid-iq codebase
# uses (bidiq/ingest.py, retag-existing-documents.py, /api/ask, /api/
# knowledge-base/ingest). Override with --model.
DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Valid 4-tier vocabulary the v2.1 prompt is supposed to emit. Mirrored
# locally so this script can validate classifier output without
# depending on bidiq.ingest's set.
VALID_TIERS = {
    "tier-1-public",
    "tier-2-internal",
    "tier-3-paul-only",
    "uncategorized",
}

# Forced-tool-use schema. The model is required to call this tool with
# `tier` set to one of the four enum values. Replaces the older
# "ask the model for JSON in a text response" approach, which Sonnet
# kept derailing into prose ("Looking at this document, I need to…",
# "Step 1: Is this a Liftnow document?"). Tool use guarantees
# structured output regardless of how chatty the system prompt makes
# the model want to be.
CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": (
        "Record the access-tier classification for the document. "
        "Apply the rules in the system prompt and pass exactly one "
        "value from the enum."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": [
                    "tier-1-public",
                    "tier-2-internal",
                    "tier-3-paul-only",
                    "uncategorized",
                ],
                "description": (
                    "Access tier per the system-prompt classifier rules. "
                    "Use 'uncategorized' for non-Liftnow content per "
                    "v2.1's Step 1 gate."
                ),
            }
        },
        "required": ["tier"],
    },
}

EXCLUSION_PREVIEW_PAGES = 5
CLASSIFIER_BODY_MAX_PAGES = 40
CLASSIFIER_MAX_CHARS = 50_000
CLASSIFIER_MAX_RETRIES = 5
# Flush often; even small spot-check runs (--limit 5/30) survive a
# crash or final-write PermissionError this way. Disk writes are cheap.
INCREMENTAL_FLUSH_EVERY = 5

# File-extension policy for body extraction. Anything not matched here
# falls back to filename-only classification (no I/O on the bytes).
PLAINTEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".eml",
}
PDF_EXTS = {".pdf"}

# Few-shot examples derived from Paul's hand-corrections of the
# 2026-04-28 spot-check (--limit 30 --shuffle --seed 42). Treated as
# ground truth — overrides v2.1 when they conflict. Three meta-rules
# that v2.1 alone misses are stated explicitly:
#
#   1. OCR-split fragments of one larger document are uncategorized,
#      not their underlying tier — each fragment is meaningless out
#      of context.
#   2. Internal Liftnow administrative paperwork with no bid-intel
#      value (meeting room agreements, generic NDA templates) is
#      uncategorized, not tier-2-internal.
#   3. The tier reflects access sensitivity, not operational value
#      for bidding. Don't downgrade a tier just because the document
#      isn't useful for bid intelligence.
#
# Wrapped in cache_control on every call so the ~3k extra input
# tokens are charged at the cached-rate (~10x cheaper) after the
# first hit.
PAUL_FEWSHOT_GUIDANCE = """\
PAUL'S VERDICTS ON A 30-FILE SPOT-CHECK (TREAT AS GROUND TRUTH; THESE
OVERRIDE v2.1 RULES IF THEY CONFLICT):

Three meta-rules from Paul's labels:

(1) OCR-split page fragments from a single larger document are
    `uncategorized`, not their underlying tier. If the filename
    contains a project ID followed by a page-range or section name
    (e.g. `20260127-00-3yqkjv_<section>_<page-range> - Page <n>.pdf`),
    return `uncategorized` — each fragment is meaningless out of
    context.

(2) Internal Liftnow administrative paperwork with no bid-intelligence
    value is `uncategorized`, not `tier-2-internal`. Examples: meeting
    room agreements, generic NDA templates Liftnow signs as a third
    party, internal housekeeping forms.

(3) The tier reflects access sensitivity, not operational value for
    bidding. Customer POs, RFPs, invoices, contracts → tier-2-internal.
    Personal financial / banking documents → tier-3-paul-only.

Worked examples from the spot-check:

  Capabilities Statement Liftnow.pdf                           → tier-1-public
  Parts list - 820 RHS.xlsx                                    → tier-1-public
  670-90758-ctr-20.pdf                                         → tier-1-public
  OMER Chassis Lifting Beam for MCO.pdf                        → tier-1-public

  POGSD_GSD0014660_0.pdf                                       → tier-2-internal
  jpwcreditapp.pdf                                             → tier-2-internal
  IFB 2024-007 Replace Air Compressor.pdf                      → tier-2-internal
  City of Baton Rouge - Lift Inspections - 121223-LFT.pdf      → tier-2-internal
  City of Cleveland Inspections - Sourcewell 013020-LFT.pdf    → tier-2-internal
  Addendum_3_Vehicle_Lifts_with_Garage_and_Fleet_Maintenance_Equipment_RFP013020.pdf → tier-2-internal
  Combined - IFB 2024-013.pdf                                  → tier-2-internal
  7917 Sample of Services Contract 5.13.19.pdf                 → tier-2-internal
  Invoice Sample-Invoice.pdf                                   → tier-2-internal
  2025-11-13_Fw_ Invoice from Keen Contracting_Invoice-466.pdf → tier-2-internal
  2024-10-11_Fw_ Open Invoices_Estimate # 2331.pdf             → tier-2-internal
  Post - Sale Process Nicole and Sherry.docx                   → tier-2-internal
  City of Tulsa Supplier Registration Form.pdf                 → tier-2-internal
  Copy of BLANK AIA.xlsx                                       → tier-2-internal
  DMP957710R1 Submission Instructions.pdf                      → tier-2-internal
  2024-11 Lift Now Pricing (11-01-24).pdf                      → tier-2-internal

  20240430-statements-2834-.pdf                                → tier-3-paul-only

  Liftnow Meeting Room Agreement 2-1-22.pdf                    → uncategorized
  LESP - Confidentiality and NDA Agreement - For Third Party Use (v. 2019.06.10).pdf → uncategorized

  20260127-00-3yqkjv_285319 Emergency Responders Radio System Errs_1742_1755 - Page 1747.pdf → uncategorized
  20260127-00-3yqkjv_083613 Sectional Doors_658_677 - Page 658.pdf                          → uncategorized
  20260127-00-3yqkjv_028220 Hazardous Materials Abatement_420_443 - Page 434.pdf            → uncategorized
  20260127-00-3yqkjv_Geotechnical Report_121_128 - Page 125.pdf                             → uncategorized
  20260127-00-3yqkjv_312500 Erosion and Sediment Control_1789_1808 - Page 1795.pdf          → uncategorized
  20260127-00-3yqkjv_OM-S-600 Schedules - Office maintenance Building - Page 84.pdf         → uncategorized
  20260127-00-3yqkjv_262713 Electricity Metering_1511_1513 - Page 1511.pdf                  → uncategorized
"""

CSV_FIELDS = [
    "filename",
    "source_bucket",
    "source_path",
    "extension",
    "tier",
    "confidence",
    "reason",
    "moved_to",
]


def extract_pdf_text(pdf_path: Path, max_pages: Optional[int] = None) -> str:
    """Plain-text extraction via pypdf. Returns "" on unreadable PDFs.

    Scanned/image-only PDFs typically yield empty text — those will land
    in `uncategorized` downstream, which is fine for a sort pass.
    """
    from pypdf import PdfReader  # noqa: PLC0415  (lazy — see top of module)

    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return ""
    pages = reader.pages
    if max_pages is not None:
        pages = pages[:max_pages]
    chunks: list[str] = []
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)


def extract_plaintext(path: Path, max_chars: int = 200_000) -> str:
    """Read a text-shaped file with permissive decoding. "" on failure."""
    try:
        with open(path, "rb") as f:
            raw = f.read(max_chars)
    except Exception:
        return ""
    for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def extract_text_for_file(path: Path, *, max_pages: int) -> str:
    """Best-effort extraction across file types.

    PDFs use pypdf; common text formats are read directly; everything
    else returns "" so the classifier sees the filename only via the
    title field.
    """
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return extract_pdf_text(path, max_pages=max_pages)
    if ext in PLAINTEXT_EXTS:
        return extract_plaintext(path)
    return ""


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_for_match(s: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single spaces.

    Lets `"77 Mercer"` match `"77_Mercer_Lease.pdf"`, `"77-mercer"`,
    `"77.Mercer.Lease"`, etc. Same normalization is applied to needle
    and haystack so the comparison is symmetric.
    """
    return _NORMALIZE_RE.sub(" ", s.lower()).strip()


def is_excluded(filename: str, content_preview: str) -> tuple[bool, str]:
    """Return (excluded, matched_string). Separator-normalized substring match."""
    if not EXCLUSION_STRINGS:
        return False, ""
    haystack = _normalize_for_match(f"{filename} {content_preview}")
    for needle in EXCLUSION_STRINGS:
        n = _normalize_for_match(needle)
        if n and n in haystack:
            return True, needle
    return False, ""


def load_skip_patterns_from_env() -> list[str]:
    """Pipe-delimited glob patterns from BIDIQ_SKIP_PATTERNS.

    Combined at runtime with patterns from --skip-pattern CLI flags.
    Files whose name matches any pattern (case-insensitive) get
    short-circuited to tier=uncategorized with no API call.
    """
    return [
        s.strip()
        for s in os.environ.get("BIDIQ_SKIP_PATTERNS", "").split("|")
        if s.strip()
    ]


def is_skipped(filename: str, patterns: list[str]) -> tuple[bool, str]:
    """Return (skipped, matched_pattern). fnmatch glob, case-insensitive.

    Patterns use shell-style globs (`*`, `?`, `[abc]`). Match is run
    against the bare filename, not the full path.
    """
    if not patterns:
        return False, ""
    name_lower = filename.lower()
    for pattern in patterns:
        if fnmatch.fnmatch(name_lower, pattern.lower()):
            return True, pattern
    return False, ""


def collect_files(priority_dir: Path, secondary_dir: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for d, label in [(priority_dir, "priority"), (secondary_dir, "secondary")]:
        if not d.exists():
            print(f"  warn: bucket missing, skipping: {d}")
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            out.append((p, label))
    out.sort(key=lambda t: str(t[0]).lower())
    return out


def _write_csv_to(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_csv(path: Path, rows: list[dict]) -> Path:
    """Write rows to `path`; on PermissionError fall back to a timestamped
    sibling so an Excel lock on the target never costs us the run.

    Returns the path that was actually written.
    """
    try:
        _write_csv_to(path, rows)
        return path
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fallback = path.with_name(f"{path.stem}-{ts}{path.suffix}")
        _write_csv_to(fallback, rows)
        print(
            f"  !! WARN: {path.name} is locked (likely open in Excel). "
            f"Wrote {fallback.name} instead.",
            file=sys.stderr,
        )
        return fallback


def _truncate_for_classifier(text: str, max_chars: int = CLASSIFIER_MAX_CHARS) -> str:
    """Head + tail kept; middle dropped. Mirrors bidiq.ingest's strategy."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...truncated...]\n\n" + text[-half:]


def classify_with_diagnostics(
    *,
    client: Any,
    system_prompt: str,
    title: str,
    body_text: str,
    model: str,
    relative_path: str,
) -> tuple[str, str]:
    """Run the v2.1 classifier. Returns (tier, reason).

    Unlike `bidiq.ingest.classify_document` (which silently coerces
    every error to ["uncategorized"]), this surfaces the real reason
    in the CSV so misclassifications and API errors are debuggable.
    """
    import anthropic  # noqa: PLC0415

    head = title or ""
    if relative_path and relative_path != title:
        head = f"{head}\n[path] {relative_path}" if head else f"[path] {relative_path}"
    body = _truncate_for_classifier(body_text or "")
    document_text = (head + "\n\n" + body).strip() if body else head
    if not document_text:
        return "uncategorized", "no title or extractable text"

    # Forced tool use: the model is required to call classify_document
    # with `tier` set to one of the four enum values. We don't parse
    # text output — the tool block carries the answer in structured
    # form. max_tokens covers any preamble text the model emits before
    # the tool call (we ignore that text).
    response = None
    last_err = ""
    for attempt in range(CLASSIFIER_MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                tools=[CLASSIFY_TOOL],
                tool_choice={"type": "tool", "name": "classify_document"},
                messages=[{"role": "user", "content": document_text}],
            )
            break
        except anthropic.RateLimitError as e:
            last_err = f"rate-limited: {e}"
            if attempt == CLASSIFIER_MAX_RETRIES - 1:
                return "ERROR", last_err[:300]
            wait = 2 ** (attempt + 1) * 15
            print(f"    rate-limited, sleeping {wait}s …")
            time.sleep(wait)
        except anthropic.APIError as e:
            return "ERROR", f"api-error: {e}"[:300]
        except Exception as e:  # noqa: BLE001
            return "ERROR", f"classify: {e}"[:300]

    if response is None:
        return "ERROR", last_err[:300] or "classifier returned no response"

    # Walk content blocks for the tool_use block. The model may emit a
    # text block first (its reasoning); we ignore it. Tool-choice
    # guarantees a tool_use block named classify_document is present.
    for block in response.content or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != "classify_document":
            continue
        tool_input = getattr(block, "input", {}) or {}
        tier = str(tool_input.get("tier", "")).strip()
        if tier in VALID_TIERS:
            return tier, ""
        return "uncategorized", (
            f"tool returned unknown tier: {tier!r}"
        )[:300]

    # No tool_use block found — shouldn't happen with forced tool_choice
    # but capture diagnostics if it does.
    text_blocks = [
        getattr(b, "text", "")
        for b in (response.content or [])
        if getattr(b, "type", None) == "text"
    ]
    preview = (text_blocks[0] if text_blocks else "")[:150]
    stop_reason = getattr(response, "stop_reason", "?")
    return "ERROR", (
        f"no tool_use block (stop_reason={stop_reason}); text={preview!r}"
    )[:300]


def process_file(
    file_path: Path,
    source_bucket: str,
    *,
    client: Any,
    system_prompt: Any,
    excluded_dir: Path,
    model: str,
    staging_root: Path,
    skip_patterns: list[str],
) -> dict:
    """Classify one file. Returns a CSV row dict. No DB writes."""
    ext = file_path.suffix.lower()
    is_pdf = ext in PDF_EXTS

    # Skip-pattern check FIRST — saves the body extraction and the
    # API call for files that match a known-noise pattern (e.g. an
    # OCR-split-by-page project). Runs before exclusion to keep
    # extraction cost off the patterns Paul has already labelled.
    skipped, matched_pattern = is_skipped(file_path.name, skip_patterns)
    if skipped:
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "extension": ext,
            "tier": "uncategorized",
            "confidence": "skip-pattern",
            "reason": f"filename-pattern: {matched_pattern!r}",
            "moved_to": "",
        }

    try:
        if is_pdf:
            content_preview = extract_pdf_text(
                file_path, max_pages=EXCLUSION_PREVIEW_PAGES
            )
        elif ext in PLAINTEXT_EXTS:
            content_preview = extract_plaintext(file_path)[:8000]
        else:
            content_preview = ""
    except Exception as e:  # noqa: BLE001
        return _error_row(file_path, source_bucket, ext, f"preview-extract: {e}")

    excluded, _matched = is_excluded(file_path.name, content_preview)
    if excluded:
        excluded_dir.mkdir(exist_ok=True)
        target = excluded_dir / file_path.name
        suffix = 1
        while target.exists():
            target = excluded_dir / f"{file_path.stem}__{suffix}{file_path.suffix}"
            suffix += 1
        try:
            shutil.move(str(file_path), str(target))
        except Exception as e:  # noqa: BLE001
            return _error_row(
                file_path, source_bucket, ext, f"move-to-excluded failed: {e}"
            )
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "extension": ext,
            "tier": "EXCLUDED-PERSONAL",
            "confidence": "N/A",
            "reason": "matched exclusion string (redacted)",
            "moved_to": str(target),
        }

    try:
        body_text = extract_text_for_file(
            file_path, max_pages=CLASSIFIER_BODY_MAX_PAGES
        )
    except Exception as e:  # noqa: BLE001
        return _error_row(file_path, source_bucket, ext, f"body-extract: {e}")

    try:
        relative_path = str(file_path.relative_to(staging_root))
    except ValueError:
        relative_path = str(file_path)

    tier, reason = classify_with_diagnostics(
        client=client,
        system_prompt=system_prompt,
        title=file_path.stem,
        body_text=body_text,
        model=model,
        relative_path=relative_path,
    )

    return {
        "filename": file_path.name,
        "source_bucket": source_bucket,
        "source_path": str(file_path),
        "extension": ext,
        "tier": tier,
        "confidence": "auto",
        "reason": reason,
        "moved_to": "",
    }


def _error_row(
    file_path: Path, source_bucket: str, ext: str, msg: str
) -> dict:
    return {
        "filename": file_path.name,
        "source_bucket": source_bucket,
        "source_path": str(file_path),
        "extension": ext,
        "tier": "ERROR",
        "confidence": "N/A",
        "reason": msg[:300],
        "moved_to": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=DEFAULT_STAGING_ROOT,
        help="override staging root (default: Paul's Windows desktop path)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model id (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help=(
            "randomize file order before applying --limit, so a small "
            "sample is drawn from across both buckets (use for spot-check "
            "runs; default order is deterministic alphabetical)"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed for --shuffle (omit for non-reproducible shuffle)",
    )
    parser.add_argument(
        "--skip-pattern",
        action="append",
        default=[],
        metavar="GLOB",
        help=(
            "shell-glob pattern matched against filenames (case-"
            "insensitive); matched files get tier=uncategorized with "
            "no API call. Repeatable. Combined with BIDIQ_SKIP_PATTERNS "
            "env var (pipe-delimited)."
        ),
    )
    args = parser.parse_args()

    skip_patterns = load_skip_patterns_from_env() + list(args.skip_pattern or [])

    staging_root: Path = args.staging_root
    priority_dir = staging_root / PRIORITY_SUBDIR
    secondary_dir = staging_root / SECONDARY_SUBDIR
    excluded_dir = staging_root / EXCLUDED_SUBDIR
    sort_report_csv = staging_root / SORT_REPORT_NAME

    if not staging_root.exists():
        print(f"ERROR: staging root does not exist: {staging_root}")
        return 2

    # Probe SORT-REPORT.csv before any API spend. If Excel has it open,
    # Windows refuses overwrites — better to bail right now and tell the
    # user to close Excel than to run for 30 minutes and dump the output
    # into a timestamped sibling they have to hunt down.
    if sort_report_csv.exists():
        try:
            with open(sort_report_csv, "r+b"):
                pass
        except PermissionError:
            print(
                f"\nERROR: {sort_report_csv} is locked (likely open in Excel).\n"
                "  Close it and rerun so output lands in SORT-REPORT.csv.\n"
                "  (If you want to keep the old CSV open for reference, "
                "rename it first.)",
                file=sys.stderr,
            )
            return 4

    if EXCLUSION_STRINGS:
        print(
            f"  exclusions      : {len(EXCLUSION_STRINGS)} string(s) loaded "
            f"from BIDIQ_EXCLUSION_STRINGS"
        )
    else:
        print(
            "  !! WARNING !!   : BIDIQ_EXCLUSION_STRINGS is empty.\n"
            "                    No PII exclusions will be applied. Files "
            "containing personal\n                    data will go to the "
            "classifier. Set the env var in .env.local\n                    "
            "(pipe-delimited) before a real run."
        )

    if skip_patterns:
        print(
            f"  skip-patterns   : {len(skip_patterns)} pattern(s) — files "
            f"matching go to uncategorized with no API call"
        )
        for p in skip_patterns:
            print(f"                    {p!r}")

    files = collect_files(priority_dir, secondary_dir)
    if args.shuffle:
        import random  # noqa: PLC0415

        rng = random.Random(args.seed) if args.seed is not None else random.Random()
        rng.shuffle(files)
    if args.limit is not None:
        files = files[: args.limit]

    print(f"Discovered {len(files)} file(s) for processing.")
    print(f"  priority bucket : {priority_dir}")
    print(f"  secondary bucket: {secondary_dir}")
    print(f"  excluded target : {excluded_dir}")
    print(f"  sort report     : {sort_report_csv}")
    print(f"  model           : {args.model}")
    print(f"  dry-run         : {args.dry_run}")

    if args.dry_run:
        ext_counts: Counter[str] = Counter(p.suffix.lower() for p, _ in files)
        for i, (file_path, bucket) in enumerate(files, 1):
            print(f"  [DRY] {i:>5}/{len(files)} [{bucket}] {file_path}")
        print("\nExtension breakdown:")
        for ext, count in ext_counts.most_common():
            print(f"  {ext or '(none)':<10} {count:>6}")
        print("\nDry run complete. No files were moved or classified.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        return 2

    # Defer heavy imports until we know we're really classifying.
    import anthropic  # noqa: PLC0415

    from bidiq.ingest import CLASSIFIER_SYSTEM_PROMPT_V2  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=api_key)

    # Build the cached system prompt once. v2.1's text + Paul's
    # spot-check verdicts as few-shot guidance, wrapped in
    # cache_control so the ~3k extra tokens are charged at the
    # cached-rate (~10x cheaper) on every call after the first.
    # Cache TTL is 5 min — well under our typical inter-call gap.
    classifier_system_prompt = [
        {
            "type": "text",
            "text": CLASSIFIER_SYSTEM_PROMPT_V2 + "\n\n" + PAUL_FEWSHOT_GUIDANCE,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # Preflight: verify auth + model with one tiny call BEFORE we burn
    # money on 30+ classifications. Bail out clearly if anything is
    # misconfigured (bad API key, retired model id, account spending
    # cap reached, network blocked).
    print("  preflight       : verifying API key + model …")
    try:
        client.messages.create(
            model=args.model,
            max_tokens=10,
            system="Reply with the single word: OK",
            messages=[{"role": "user", "content": "preflight"}],
        )
        print("  preflight       : OK")
    except anthropic.APIError as e:
        msg = str(e).lower()
        diagnosis = (
            "  Likely causes: invalid ANTHROPIC_API_KEY, retired model id "
            f"({args.model!r}), or no network egress to api.anthropic.com."
        )
        if "usage limit" in msg or "spending" in msg:
            diagnosis = (
                "  Cause: your Anthropic account has hit its configured "
                "spending cap.\n"
                "  Fix: wait until the reset date in the error above, or "
                "raise the cap at\n  https://console.anthropic.com/ → "
                "Settings → Plans & Billing → Usage limits."
            )
        elif "authentication" in msg or "api key" in msg or "401" in msg:
            diagnosis = (
                "  Cause: ANTHROPIC_API_KEY is missing, malformed, or "
                "revoked.\n  Fix: confirm the key in .env.local (or your "
                "shell session) matches the\n  active key in "
                "https://console.anthropic.com/settings/keys."
            )
        elif "not_found" in msg or "model" in msg and "404" in msg:
            diagnosis = (
                f"  Cause: model id {args.model!r} is not available to your "
                "account.\n  Fix: pass --model with a current id "
                "(e.g. claude-sonnet-4-20250514)."
            )
        print(f"\nERROR: preflight API call failed: {e}", file=sys.stderr)
        print(diagnosis, file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR: preflight failed: {e}", file=sys.stderr)
        return 3

    rows: list[dict] = []
    # Once a flush hits PermissionError on `sort_report_csv` (Excel
    # lock), write_csv falls back to a timestamped sibling and returns
    # it. We then write to THAT path for the rest of the run instead
    # of retrying the locked target every flush — otherwise each flush
    # creates a brand-new SORT-REPORT-{ts}.csv and the user ends up
    # with ten partial files instead of one cumulative one.
    csv_target = sort_report_csv
    first_error_printed = False
    started = time.time()
    for i, (file_path, bucket) in enumerate(files, 1):
        row = process_file(
            file_path,
            bucket,
            client=client,
            system_prompt=classifier_system_prompt,
            excluded_dir=excluded_dir,
            model=args.model,
            staging_root=staging_root,
            skip_patterns=skip_patterns,
        )
        rows.append(row)

        # Surface the first ERROR's reason loudly. Everything else lives
        # in the CSV, but if every file is failing the same way the user
        # needs to see it BEFORE the run finishes (and before the CSV
        # write potentially fails too).
        if row["tier"] == "ERROR" and not first_error_printed:
            print(
                f"  !! first ERROR  : {row['filename']}\n"
                f"     reason       : {row['reason']}",
                file=sys.stderr,
            )
            first_error_printed = True

        if i % 5 == 0 or i == len(files):
            elapsed = time.time() - started
            rate = i / elapsed if elapsed else 0
            reason_hint = ""
            if row["tier"] in ("ERROR", "uncategorized") and row.get("reason"):
                reason_hint = f"  ({row['reason'][:60]})"
            print(
                f"  {i:>5}/{len(files)}  ({100 * i / len(files):5.1f}%)  "
                f"{rate:.2f} files/sec  last: {row['tier']:<22} "
                f"{file_path.name}{reason_hint}"
            )

        if i % INCREMENTAL_FLUSH_EVERY == 0:
            csv_target = write_csv(csv_target, rows)

    last_csv_path = write_csv(csv_target, rows)

    print("\n=== TIER SUMMARY ===")
    tier_counts = Counter(r["tier"] for r in rows)
    for tier, count in tier_counts.most_common():
        print(f"  {tier:<24} {count:>6}")
    err_rows = [r for r in rows if r["tier"] == "ERROR"]
    if err_rows:
        print("\n=== ERROR SAMPLE (first 5) ===")
        for r in err_rows[:5]:
            print(f"  {r['filename']}: {r['reason']}")
    print(f"\nReport written to: {last_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
