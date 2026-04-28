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
import json
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

    # Prefill technique: by starting the assistant turn with `[`, the
    # model is locked into completing a JSON array. It cannot emit
    # "Looking at this document, I need to..." prose because the
    # response is already mid-bracket. Pair with stop_sequences=["]"]
    # so generation stops cold after the closing bracket — keeps the
    # response under ~20 tokens and removes any "max_tokens cut off
    # mid-reasoning" failure mode that v2.1's worked-examples prompt
    # was triggering on Sonnet.
    response = None
    last_err = ""
    for attempt in range(CLASSIFIER_MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=64,
                system=system_prompt,
                stop_sequences=["]"],
                messages=[
                    {"role": "user", "content": document_text},
                    {"role": "assistant", "content": "["},
                ],
            )
            break
        except anthropic.RateLimitError as e:
            last_err = f"rate-limited: {e}"
            if attempt == CLASSIFIER_MAX_RETRIES - 1:
                return "ERROR", last_err[:200]
            wait = 2 ** (attempt + 1) * 15
            print(f"    rate-limited, sleeping {wait}s …")
            time.sleep(wait)
        except anthropic.APIError as e:
            return "ERROR", f"api-error: {e}"[:200]
        except Exception as e:  # noqa: BLE001
            return "ERROR", f"classify: {e}"[:200]

    if response is None:
        return "ERROR", last_err[:200] or "classifier returned no response"

    # With prefill + stop sequence, response.content[0].text is just the
    # body of the array (e.g., '"tier-1-public"'). Reconstruct the full
    # array for json.loads.
    raw_body = response.content[0].text.strip() if response.content else ""
    if not raw_body:
        return "uncategorized", "empty model response"
    raw_array = "[" + raw_body + "]"

    try:
        parsed = json.loads(raw_array)
    except json.JSONDecodeError as e:
        return "uncategorized", f"json decode: {e}; got {raw_body[:80]!r}"[:200]

    if not isinstance(parsed, list) or not parsed:
        return "uncategorized", f"unexpected shape: {parsed!r}"[:200]

    raw_tier = str(parsed[0]).strip()
    if raw_tier in VALID_TIERS:
        return raw_tier, ""
    return "uncategorized", f"unrecognized tier: {raw_tier!r}"


def process_file(
    file_path: Path,
    source_bucket: str,
    *,
    client: Any,
    system_prompt: str,
    excluded_dir: Path,
    model: str,
    staging_root: Path,
) -> dict:
    """Classify one file. Returns a CSV row dict. No DB writes."""
    ext = file_path.suffix.lower()
    is_pdf = ext in PDF_EXTS

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
        "reason": msg[:200],
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
    args = parser.parse_args()

    staging_root: Path = args.staging_root
    priority_dir = staging_root / PRIORITY_SUBDIR
    secondary_dir = staging_root / SECONDARY_SUBDIR
    excluded_dir = staging_root / EXCLUDED_SUBDIR
    sort_report_csv = staging_root / SORT_REPORT_NAME

    if not staging_root.exists():
        print(f"ERROR: staging root does not exist: {staging_root}")
        return 2

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
            system_prompt=CLASSIFIER_SYSTEM_PROMPT_V2,
            excluded_dir=excluded_dir,
            model=args.model,
            staging_root=staging_root,
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
