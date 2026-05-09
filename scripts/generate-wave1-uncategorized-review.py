"""Generate logs/wave1-uncategorized-review.csv from the Wave 1 uncategorized rows.

Run from the repo root on any machine that can reach Neon:

    DATABASE_URL=<url> python scripts/generate-wave1-uncategorized-review.py

Or pass the URL as an argument:

    python scripts/generate-wave1-uncategorized-review.py \
        "postgresql://wave1_readonly:Wave1pass!2026@ep-gentle-shadow-adw4fmoz-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

The script writes logs/wave1-uncategorized-review.csv and prints a summary.
Do NOT run UPDATE/INSERT/DELETE — this script is read-only by design.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "logs"
OUT_CSV = LOGS_DIR / "wave1-uncategorized-review.csv"

QUERY = """
SELECT k.id, b.name AS brand, k.source_filename,
       length(coalesce(k.search_text, '')) AS char_count,
       substr(coalesce(k.search_text, ''), 1, 400) AS snippet
  FROM knowledge_items k
  JOIN brands b ON b.id = k.brand_id
 WHERE 'uncategorized' = ANY(k.category)
   AND k.source = 'ingested'
   AND k.source_type = 'ingested_pdf'
   AND k.extractor_version = 'ingest.py-v1-tier1'
 ORDER BY b.name, k.source_filename
"""

CSV_FIELDS = ["id", "brand", "filename", "char_count", "recommended_tier", "reasoning", "paul_decision"]

# ---------------------------------------------------------------------------
# Tier decision rules (v2.1 classifier — same logic as classifier-system-prompt-v2.md)
# Decision order: tier-3 triggers → tier-2 triggers → tier-1 → uncategorized
# ---------------------------------------------------------------------------

import re

# Keywords that force tier-3 regardless of anything else
TIER3_STRONG = re.compile(
    r"\b(cost\s+sheet|dealer\s+cost|list\s+x|invoice|commission|payroll|"
    r"salary|wage|W-?9|profit|margin|markup|discount\s+file|"
    r"acquisition|NDA|non.disclosure|letter\s+of\s+intent|"
    r"financial\s+statement|P\s*&\s*L|audit|loss\s+run|"
    r"bid\s+protest|change\s+order|demand\s+letter|attorney|"
    r"ACH|routing\s+number|account\s+number|bank\s+statement|"
    r"performance\s+bond|bid\s+bond|payment\s+bond|"
    r"win.loss|debrief|competitor\s+analysis|spend\s+data)\b",
    re.IGNORECASE,
)

# Patterns that suggest tier-2 (operational / internal)
TIER2_STRONG = re.compile(
    r"\b(purchase\s+order|service\s+ticket|inspection\s+report|"
    r"install\s+schedule|site\s+survey|subcontract|vendor\s+agreement|"
    r"dealer\s+agreement|rebate\s+program|freight\s+program|"
    r"SOP|standard\s+operating|RFP|quote\s+history|credit\s+application|"
    r"tax.exempt|COI|certificate\s+of\s+insurance|"
    r"W-9|SAM\.gov|bonding\s+capacity|"
    r"buy\s+america\s+cert|davis.bacon|EEO|PWCR|E-?Verify|"
    r"quarterly\s+report|admin\s+fee|sourcing\s+fee|"
    r"core\s+sample|electrical\s+readiness|damage\s+claim|"
    r"cold\s+outreach|cadence|SalesLoft)\b",
    re.IGNORECASE,
)

# Patterns that suggest tier-1 (public-safe)
TIER1_SIGNALS = re.compile(
    r"\b(spec\s+sheet|specification|data\s+sheet|brochure|catalog|"
    r"installation\s+guide|operation\s+manual|service\s+manual|"
    r"parts\s+catalog|safety\s+bulletin|warranty|technical\s+bulletin|"
    r"APTA|NIGP|GovFleet|ALI\s+registry|OSHA|FTA|"
    r"Sourcewell|NASPO|GSA|FSA|Buy\s+America\s+Act|"
    r"capability\s+statement|line\s+card|case\s+study|"
    r"MSRP|list\s+price|manufacturer.*pric)\b",
    re.IGNORECASE,
)

# Filename heuristics for tier-3
FILENAME_TIER3 = re.compile(
    r"(invoice|cost|margin|discount|commission|payroll|salary|"
    r"financial|NDA|protest|change.order|demand|bond|insurance.policy|"
    r"acquisition|debrief|competitor)",
    re.IGNORECASE,
)

# Filename heuristics for tier-2
FILENAME_TIER2 = re.compile(
    r"(quote|PO|purchase.order|service.ticket|install.schedule|"
    r"site.survey|subcontract|dealer.agreement|vendor.agreement|"
    r"buy.america|davis.bacon|EEO|quarterly.report|cold.outreach|"
    r"RFP|credit.app|onboarding|damage.claim|COI|W-9|SAM|bonding)",
    re.IGNORECASE,
)

# Filename heuristics for tier-1
FILENAME_TIER1 = re.compile(
    r"(spec|data.sheet|brochure|catalog|install|operation|service.manual|"
    r"parts|safety|warranty|bulletin|msrp|list.price|capability|"
    r"IOM|installation.drawing|drawing|dimension|layout)",
    re.IGNORECASE,
)


def classify(row: dict) -> tuple[str, str]:
    """Return (recommended_tier, reasoning) for one DB row."""
    filename = row["filename"] or ""
    snippet = row["snippet"] or ""
    char_count = int(row["char_count"] or 0)

    # --- Flag broken extractions first ---
    if char_count < 200:
        return "RE-EXTRACT", f"char_count={char_count} — extraction too short to classify; re-run ingest.py"

    # Cheap garble check: if >40% of chars are non-ASCII or repeated artifacts
    non_alpha = sum(1 for c in snippet if not (c.isalpha() or c.isspace()))
    if len(snippet) > 0 and non_alpha / len(snippet) > 0.40:
        return "RE-EXTRACT", "snippet appears garbled (high non-alphabetic ratio); re-run ingest.py"

    # --- Apply tier-3 triggers (most restrictive first) ---
    t3_match = TIER3_STRONG.search(snippet) or TIER3_STRONG.search(filename)
    if t3_match:
        return "tier-3-paul-only", f"snippet/filename contains restricted signal '{t3_match.group(0).lower()}' → tier-3"

    fn_t3 = FILENAME_TIER3.search(filename)
    if fn_t3:
        return "tier-3-paul-only", f"filename pattern '{fn_t3.group(0).lower()}' indicates restricted material → tier-3"

    # --- Tier-2 triggers ---
    t2_match = TIER2_STRONG.search(snippet) or TIER2_STRONG.search(filename)
    if t2_match:
        return "tier-2-internal", f"snippet/filename contains operational signal '{t2_match.group(0).lower()}' → tier-2"

    fn_t2 = FILENAME_TIER2.search(filename)
    if fn_t2:
        return "tier-2-internal", f"filename pattern '{fn_t2.group(0).lower()}' indicates internal operational doc → tier-2"

    # --- Tier-1 signals ---
    t1_match = TIER1_SIGNALS.search(snippet)
    if t1_match:
        return "tier-1-public", f"snippet contains public-safe signal '{t1_match.group(0).lower()}' → tier-1"

    fn_t1 = FILENAME_TIER1.search(filename)
    if fn_t1:
        return "tier-1-public", f"filename pattern '{fn_t1.group(0).lower()}' matches manufacturer/product doc → tier-1"

    # --- Genuinely ambiguous ---
    return "leave uncategorized", "no decisive tier signal in snippet or filename; Paul should review manually"


def main() -> None:
    db_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("DATABASE_URL", "")
    )
    if not db_url:
        print("Usage: DATABASE_URL=<url> python scripts/generate-wave1-uncategorized-review.py", file=sys.stderr)
        sys.exit(1)

    try:
        import psycopg
    except ImportError:
        print("psycopg not installed — run: pip install 'psycopg[binary]'", file=sys.stderr)
        sys.exit(1)

    print("Connecting to Neon…", flush=True)
    with psycopg.connect(db_url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = cur.fetchall()

    print(f"Fetched {len(rows)} uncategorized rows.", flush=True)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    tier_counts: dict[str, int] = {}
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            r = {
                "id": str(row[0]),
                "brand": row[1],
                "filename": row[2],
                "char_count": row[3],
                "snippet": row[4],
            }
            recommended_tier, reasoning = classify(r)
            tier_counts[recommended_tier] = tier_counts.get(recommended_tier, 0) + 1
            writer.writerow({
                "id": r["id"],
                "brand": r["brand"],
                "filename": r["filename"],
                "char_count": r["char_count"],
                "recommended_tier": recommended_tier,
                "reasoning": reasoning,
                "paul_decision": "",
            })

    print(f"\nWrote {OUT_CSV}")
    print("\nSummary:")
    for tier, count in sorted(tier_counts.items()):
        print(f"  {tier:<25s} {count}")
    print(f"\nDone. Paul fills in column 7 (paul_decision) then runs UPDATE.")


if __name__ == "__main__":
    main()
