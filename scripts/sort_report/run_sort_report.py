"""Classify-only pass over the Phase 2 staging buckets.

Walks the priority + secondary staging dirs, runs each PDF (or other
text-bearing file) through the v2.1 classifier in `bidiq.ingest`, and
writes a CSV sort report at the staging root. **No database writes.**
Excluded-personal files are moved to 99-EXCLUDED-PERSONAL/ before
classification so they never reach the Claude API.

Usage:
  python scripts/sort_report/run_sort_report.py [--dry-run] [--limit N]
                                                [--staging-root DIR]
                                                [--model MODEL]

Required env:
  ANTHROPIC_API_KEY            — used for the classifier call
  BIDIQ_EXCLUSION_STRINGS      — pipe-delimited PII strings (optional
                                 but strongly recommended; see README)
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.sort_report.exclusion_config import EXCLUSION_STRINGS  # noqa: E402

# `bidiq.ingest` and `anthropic` are imported lazily inside main() so
# `--dry-run` works in environments that don't have the Anthropic SDK
# installed (e.g., a code-review sandbox).

# Default staging root targets Paul's local Windows path. Override with
# --staging-root when running anywhere else.
DEFAULT_STAGING_ROOT = Path(r"C:\Users\Paul\Desktop\bidiq-phase2-staging")
PRIORITY_SUBDIR = "02-PRIORITY-INGEST"
SECONDARY_SUBDIR = "03-SECONDARY-INGEST"
EXCLUDED_SUBDIR = "99-EXCLUDED-PERSONAL"
SORT_REPORT_NAME = "SORT-REPORT.csv"

DEFAULT_MODEL = "claude-sonnet-4-5"
EXCLUSION_PREVIEW_PAGES = 5
CLASSIFIER_BODY_MAX_PAGES = 40
INCREMENTAL_FLUSH_EVERY = 50

CSV_FIELDS = [
    "filename",
    "source_bucket",
    "source_path",
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
    from pypdf import PdfReader  # noqa: PLC0415  (lazy import — see top)

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


def is_excluded(filename: str, content_preview: str) -> tuple[bool, str]:
    """Return (excluded, matched_string). Case-insensitive substring match."""
    if not EXCLUSION_STRINGS:
        return False, ""
    haystack = f"{filename}\n{content_preview}".lower()
    for needle in EXCLUSION_STRINGS:
        if needle.lower() in haystack:
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


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def process_file(
    file_path: Path,
    source_bucket: str,
    *,
    client: Any,
    classify_fn: Any,
    excluded_dir: Path,
    model: str,
) -> dict:
    """Classify one file. Returns a CSV row dict. No DB writes."""
    is_pdf = file_path.suffix.lower() == ".pdf"

    if is_pdf:
        try:
            content_preview = extract_pdf_text(
                file_path, max_pages=EXCLUSION_PREVIEW_PAGES
            )
        except Exception as e:  # noqa: BLE001
            return _error_row(file_path, source_bucket, f"preview-extract: {e}")
    else:
        # Non-PDFs: use the filename for exclusion matching only; we
        # don't try to extract text from arbitrary file types in this
        # pass.
        content_preview = ""

    excluded, matched = is_excluded(file_path.name, content_preview)
    if excluded:
        excluded_dir.mkdir(exist_ok=True)
        target = excluded_dir / file_path.name
        # Disambiguate name collisions inside 99-EXCLUDED-PERSONAL/
        suffix = 1
        while target.exists():
            target = excluded_dir / f"{file_path.stem}__{suffix}{file_path.suffix}"
            suffix += 1
        try:
            shutil.move(str(file_path), str(target))
        except Exception as e:  # noqa: BLE001
            return _error_row(
                file_path, source_bucket, f"move-to-excluded failed: {e}"
            )
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "tier": "EXCLUDED-PERSONAL",
            "confidence": "N/A",
            "reason": "matched exclusion string (redacted)",
            "moved_to": str(target),
        }

    if not is_pdf:
        # Brief is PDF-focused; non-PDFs (e.g., .docx, .xlsx, .msg) get
        # flagged but not classified, so Paul can decide manually.
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "tier": "SKIPPED-NON-PDF",
            "confidence": "N/A",
            "reason": f"non-PDF extension: {file_path.suffix or '(none)'}",
            "moved_to": "",
        }

    try:
        body_text = extract_pdf_text(
            file_path, max_pages=CLASSIFIER_BODY_MAX_PAGES
        )
    except Exception as e:  # noqa: BLE001
        return _error_row(file_path, source_bucket, f"body-extract: {e}")

    if not body_text.strip():
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "tier": "uncategorized",
            "confidence": "auto",
            "reason": "no extractable text (likely image-only / scanned)",
            "moved_to": "",
        }

    try:
        tags = classify_fn(
            client,
            title=file_path.stem,
            summary="",
            body_text=body_text,
            model=model,
        )
    except Exception as e:  # noqa: BLE001
        return _error_row(file_path, source_bucket, f"classify: {e}")

    tier = tags[0] if tags else "uncategorized"
    return {
        "filename": file_path.name,
        "source_bucket": source_bucket,
        "source_path": str(file_path),
        "tier": tier,
        "confidence": "auto",
        "reason": "",
        "moved_to": "",
    }


def _error_row(file_path: Path, source_bucket: str, msg: str) -> dict:
    return {
        "filename": file_path.name,
        "source_bucket": source_bucket,
        "source_path": str(file_path),
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
    args = parser.parse_args()

    staging_root: Path = args.staging_root
    priority_dir = staging_root / PRIORITY_SUBDIR
    secondary_dir = staging_root / SECONDARY_SUBDIR
    excluded_dir = staging_root / EXCLUDED_SUBDIR
    sort_report_csv = staging_root / SORT_REPORT_NAME

    if not staging_root.exists():
        print(f"ERROR: staging root does not exist: {staging_root}")
        return 2

    if not EXCLUSION_STRINGS:
        print(
            "WARNING: BIDIQ_EXCLUSION_STRINGS is empty. No PII exclusions "
            "will be applied. Set it in .env.local before a real run."
        )

    files = collect_files(priority_dir, secondary_dir)
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
        for i, (file_path, bucket) in enumerate(files, 1):
            print(f"  [DRY] {i:>5}/{len(files)} [{bucket}] {file_path}")
        print("\nDry run complete. No files were moved or classified.")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        return 2

    # Defer heavy imports until we know we're really classifying. Keeps
    # --dry-run usable without anthropic / pdf2image in the env.
    import anthropic  # noqa: PLC0415

    from bidiq.ingest import classify_document  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=api_key)

    rows: list[dict] = []
    started = time.time()
    for i, (file_path, bucket) in enumerate(files, 1):
        row = process_file(
            file_path,
            bucket,
            client=client,
            classify_fn=classify_document,
            excluded_dir=excluded_dir,
            model=args.model,
        )
        rows.append(row)

        if i % 10 == 0 or i == len(files):
            elapsed = time.time() - started
            rate = i / elapsed if elapsed else 0
            print(
                f"  {i:>5}/{len(files)}  ({100 * i / len(files):5.1f}%)  "
                f"{rate:.1f} files/sec  last: {row['tier']:<22} "
                f"{file_path.name}"
            )

        if i % INCREMENTAL_FLUSH_EVERY == 0:
            write_csv(sort_report_csv, rows)

    write_csv(sort_report_csv, rows)

    print("\n=== SUMMARY ===")
    tier_counts = Counter(r["tier"] for r in rows)
    for tier, count in tier_counts.most_common():
        print(f"  {tier:<24} {count:>6}")
    print(f"\nReport written to: {sort_report_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
