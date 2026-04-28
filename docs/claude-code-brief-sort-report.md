# Claude Code Brief: Pre-Ingest Sort Report (Classify-Only Pass)

**Audience:** Claude Code
**Repo:** github.com/Liftnow1/bid-iq
**Branch:** `claude/sort-report-classify-only`
**Estimated effort:** ~1-2 hours code + ~3-5 hours classifier runtime
**Estimated cost:** $25-50 in Claude API calls

---

## TL;DR

The Phase 2 mining run produced ~14,000 files staged at `C:\Users\Paul\Desktop\bidiq-phase2-staging\`. The keyword filter that triaged them into priority/secondary buckets was too generous — Paul confirms both buckets are full of junk (stale web downloads, marketing PDFs, third-party content unrelated to Liftnow operations).

**Build a classify-but-don't-ingest pipeline that:**
1. Updates the classifier prompt to v2.1 (tighter — see `docs/classifier-system-prompt-v2.1.md`)
2. Applies a hard exclusion list from a local config (PII strings — never goes in git)
3. Runs the classifier against all priority + secondary files
4. Writes a CSV sort report — does NOT write to the bid-iq database
5. Paul reviews the CSV in Excel before deciding what to actually ingest

**No database writes in this pipeline.** Sort report is the only output.

---

## What changes

### Step 1: Add classifier prompt v2.1

The new file `docs/classifier-system-prompt-v2.1.md` is provided separately. Paul will commit it. It supersedes v2.0.

After it's committed:
- Mark v2.0 as deprecated by adding a banner header: `> **DEPRECATED 2026-04-27** — superseded by v2.1 (tighter Liftnow-document gate + aggressive uncategorized fallback for non-Liftnow content).`
- Update `bidiq/ingest.py` constant: `CLASSIFIER_PROMPT_PATH = "docs/classifier-system-prompt-v2.1.md"`

The VALID_CATEGORIES set stays the same (4 values: `tier-1-public`, `tier-2-internal`, `tier-3-paul-only`, `uncategorized`).

### Step 2: Build local exclusion config

Create new file `scripts/sort_report/exclusion_config.py`:

```python
"""
Local-only exclusion list. NEVER COMMIT THIS FILE WITH REAL VALUES.

Files matching ANY of these strings (in filename OR first 5 pages of content)
are excluded from classification entirely. They get moved to a separate folder
and never sent to the Claude API.
"""
import os

# Read from environment variable or local config
# DO NOT hardcode values into git
EXCLUSION_STRINGS = [
    s.strip() for s in os.environ.get("BIDIQ_EXCLUSION_STRINGS", "").split("|")
    if s.strip()
]

if not EXCLUSION_STRINGS:
    print("WARNING: BIDIQ_EXCLUSION_STRINGS not set. No exclusions will be applied.")
```

Add `BIDIQ_EXCLUSION_STRINGS` to `.env` and `.env.example`:

```
# .env (local only — gitignored)
BIDIQ_EXCLUSION_STRINGS=77 Mercer|23 Rock Shelter

# .env.example (committed, no real values)
BIDIQ_EXCLUSION_STRINGS=string1|string2|string3
```

Verify `.env` is in `.gitignore`. Add a note to `README.md` explaining the exclusion mechanism without showing real values.

### Step 3: Build the sort-report script

Create `scripts/sort_report/run_sort_report.py`:

```python
"""
Classify-only pass over Phase 2 staging buckets.
Outputs CSV sort report. No database writes.

Usage:
  python scripts/sort_report/run_sort_report.py [--dry-run] [--limit N]

Reads from:
  - C:\\Users\\Paul\\Desktop\\bidiq-phase2-staging\\02-PRIORITY-INGEST\\
  - C:\\Users\\Paul\\Desktop\\bidiq-phase2-staging\\03-SECONDARY-INGEST\\

Writes to:
  - C:\\Users\\Paul\\Desktop\\bidiq-phase2-staging\\SORT-REPORT.csv
  - C:\\Users\\Paul\\Desktop\\bidiq-phase2-staging\\99-EXCLUDED-PERSONAL\\ (moved files)
"""

import os
import csv
import shutil
from pathlib import Path
from bidiq.ingest import classify_document, extract_pdf_text  # reuse existing
from scripts.sort_report.exclusion_config import EXCLUSION_STRINGS

STAGING_ROOT = Path(r"C:\Users\Paul\Desktop\bidiq-phase2-staging")
PRIORITY_DIR = STAGING_ROOT / "02-PRIORITY-INGEST"
SECONDARY_DIR = STAGING_ROOT / "03-SECONDARY-INGEST"
EXCLUDED_DIR = STAGING_ROOT / "99-EXCLUDED-PERSONAL"
SORT_REPORT_CSV = STAGING_ROOT / "SORT-REPORT.csv"

EXCLUDED_DIR.mkdir(exist_ok=True)


def is_excluded(filename: str, content_preview: str) -> tuple[bool, str]:
    """Check filename and content preview for exclusion strings."""
    haystack = f"{filename}\n{content_preview}".lower()
    for needle in EXCLUSION_STRINGS:
        if needle.lower() in haystack:
            return True, needle
    return False, ""


def process_file(file_path: Path, source_bucket: str) -> dict:
    """Process one file. Returns row dict for CSV."""
    try:
        # Extract first 5 pages of text for exclusion check + classification
        content_preview = extract_pdf_text(file_path, max_pages=5)
        
        # Hard exclusion check FIRST
        excluded, matched_string = is_excluded(file_path.name, content_preview)
        if excluded:
            target = EXCLUDED_DIR / file_path.name
            shutil.move(str(file_path), str(target))
            return {
                "filename": file_path.name,
                "source_bucket": source_bucket,
                "source_path": str(file_path),
                "tier": "EXCLUDED-PERSONAL",
                "confidence": "N/A",
                "reason": f"matched exclusion string (redacted)",
                "moved_to": str(target),
            }
        
        # Classify (full content for accuracy)
        full_content = extract_pdf_text(file_path)
        classification = classify_document(full_content)
        tier = classification[0] if classification else "uncategorized"
        
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "tier": tier,
            "confidence": "auto",
            "reason": "",
            "moved_to": "",
        }
    except Exception as e:
        return {
            "filename": file_path.name,
            "source_bucket": source_bucket,
            "source_path": str(file_path),
            "tier": "ERROR",
            "confidence": "N/A",
            "reason": str(e)[:200],
            "moved_to": "",
        }


def main(dry_run: bool = False, limit: int | None = None):
    rows = []
    
    all_files = []
    for d, label in [(PRIORITY_DIR, "priority"), (SECONDARY_DIR, "secondary")]:
        for p in d.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                all_files.append((p, label))
    
    if limit:
        all_files = all_files[:limit]
    
    print(f"Processing {len(all_files)} files...")
    
    for i, (file_path, bucket) in enumerate(all_files, 1):
        if dry_run:
            print(f"[DRY] {i}/{len(all_files)} {file_path.name}")
            continue
        
        row = process_file(file_path, bucket)
        rows.append(row)
        
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(all_files)} ({100*i/len(all_files):.1f}%)")
            # Incremental write — protects against runtime crashes
            with open(SORT_REPORT_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
    
    # Final write
    if rows:
        with open(SORT_REPORT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    
    # Summary
    from collections import Counter
    tier_counts = Counter(r["tier"] for r in rows)
    print("\n=== SUMMARY ===")
    for tier, count in tier_counts.most_common():
        print(f"  {tier}: {count}")
    print(f"\nReport: {SORT_REPORT_CSV}")


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    limit = None
    for arg in sys.argv:
        if arg.startswith("--limit"):
            limit = int(arg.split("=")[1] if "=" in arg else sys.argv[sys.argv.index(arg)+1])
    main(dry_run=dry_run, limit=limit)
```

Key design decisions:
- **Exclusion check happens BEFORE classification** — saves API costs on excluded files
- **Excluded files are MOVED, not copied** — they leave the staging buckets
- **Sort report writes incrementally every 50 files** — runtime crashes don't lose progress
- **No database writes anywhere** — this is purely a sort/preview pass

### Step 4: Add the runner script reference to README

Add a section to `README.md`:

```markdown
## Sort Report Mode

For pre-ingest review of staged documents, run:

```bash
python scripts/sort_report/run_sort_report.py
```

This classifies all files in the priority + secondary staging buckets, applies
the local exclusion config (BIDIQ_EXCLUSION_STRINGS env var), and outputs
SORT-REPORT.csv at the staging root. Paul reviews the CSV before deciding which
tiers to ingest.

Excluded files are moved to `99-EXCLUDED-PERSONAL/` and are never sent to the
Claude API or written to the database.
```

### Step 5: Verification

After all code changes:

1. **Syntax check:**
   ```bash
   python3 -m py_compile bidiq/ingest.py
   python3 -m py_compile scripts/sort_report/run_sort_report.py
   python3 -m py_compile scripts/sort_report/exclusion_config.py
   npx tsc --noEmit
   ```

2. **Exclusion config test:**
   ```bash
   # Set env var
   export BIDIQ_EXCLUSION_STRINGS="test_string|another_test"
   python -c "from scripts.sort_report.exclusion_config import EXCLUSION_STRINGS; print(EXCLUSION_STRINGS)"
   # Should print: ['test_string', 'another_test']
   ```

3. **Dry run:**
   ```bash
   python scripts/sort_report/run_sort_report.py --dry-run --limit 10
   ```
   Should list 10 file paths without classifying or moving anything.

4. **Live small run:**
   ```bash
   python scripts/sort_report/run_sort_report.py --limit 5
   ```
   Should classify 5 files, write 5 rows to SORT-REPORT.csv, move any excluded files. Paul reviews the 5 rows manually before greenlighting full run.

5. **After Paul greenlights:**
   ```bash
   python scripts/sort_report/run_sort_report.py
   ```
   Full run. Will take 3-5 hours depending on file count. ~$25-50 in API costs.

---

## What does NOT change

- The bid-iq database (no inserts in this pipeline)
- The existing ingester pipeline (still works for /api/knowledge-base/ingest)
- The Phase 2 staging structure (priority/secondary buckets stay where they are)
- The schema (TEXT[] still right)

This is a side pipeline that READS from staging and WRITES to a CSV. It does not touch the database.

---

## Coordination

- **Branch:** `claude/sort-report-classify-only`
- **PR title:** "Pre-ingest sort report — classify-only pass with local exclusion config"
- **PR description:** Note that this is a one-time analysis pipeline. Once Paul reviews the CSV and greenlights ingestion, we'll wire the actual ingest pass separately. This PR ships only the analysis machinery.

When the PR is open, ping Paul: "Sort report pipeline ready. Run `python scripts/sort_report/run_sort_report.py --limit 5` first. Review the 5 rows. If looks right, run without limit (3-5 hr, $25-50)."

---

## Why we're doing this

Quick context for the PR description:

The keyword pre-filter in Phase 2 was too generous — substring-matching "PO" and "invoice" pulled in too much non-Liftnow content (web articles, marketing PDFs, software receipts). Paul confirmed priority + secondary buckets are full of junk.

Rather than re-tune the keyword filter, we let the classifier (with v2.1's tighter Liftnow-document gate) be the actual quality filter. The sort report gives Paul a CSV he can review/sort/filter in Excel before any database writes happen. Anything classified `uncategorized` is the junk; anything in the three tiers is genuine Liftnow material.

The local exclusion config handles personal/PII strings without ever putting them in git. Files matching the exclusion list are moved to `99-EXCLUDED-PERSONAL/` before classification, so they never hit the API.

Done.
