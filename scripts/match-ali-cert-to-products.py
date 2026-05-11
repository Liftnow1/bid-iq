#!/usr/bin/env python3
"""
Match ALI Lift Inspector Directory records to our products table and
populate is_ali_certified + ali_cert_date.

Source: data/ali_lifts.json — 3,440 records scraped from the ALI directory.
Each record has brand, model, model_base, lift_type, capacity_lbs, and
certification_date (MM/DD/YYYY string).

Match strategy (per product row):
  1. Normalize the product's brand to the ALI brand vocabulary
     (e.g., 'rotary' -> 'rotary lift', 'forward' -> 'forward lift',
      'ari-hetra' -> 'ari', 'stertil-koni' -> 'stertil').
  2. Within that brand's ALI records, look for any record whose model OR
     model_base matches the product's family sku OR any of its variant_skus,
     case-insensitive, ignoring hyphens. Two match types are tried:
       a. Exact (normalized strings equal).
       b. Prefix: ALI placeholder wildcards like "RX10KX-YYZZ-S" or
          "SPOA10X" extend our clean family names, so if our SKU (>=4 chars)
          is a prefix of an ALI sku, that's a match. Also the reverse, in
          case our variant SKU is longer than the ALI base.
     (We intentionally do NOT use a capacity sanity gate. ALI reports
     mobile-column capacity as the full SYSTEM rating — 4 cols × 6,000 lb
     = 24,000 lb — while we track per-column capacity. Most other cert
     directories follow the same convention. The min-4-char prefix rule
     is sufficient on its own to prevent the kinds of false positives
     a capacity gate would catch.)
  3. If found, take the EARLIEST certification_date across matched records
     (a family was usually first certified the day the base model was, and
     later variants may carry later dates — earliest = original cert).
  4. Update is_ali_certified=TRUE, ali_cert_date=<parsed date>.

Run --dry-run first to see the diff. Re-run without it to apply.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
ALI_PATH = REPO_ROOT / "data" / "ali_lifts.json"


def load_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return url
    for envfile in (".env.local", ".env"):
        ep = REPO_ROOT / envfile
        if not ep.exists():
            continue
        for line in ep.read_text().splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DATABASE_URL", "POSTGRES_URL"):
                return v.strip().strip('"').strip("'")
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)


# Map "our brand name" (lowercased) -> list of acceptable ALI brand strings
# (also lowercased). Multiple ALI strings allowed because the directory is
# inconsistent ("snap on" vs "snap-on", "bendpak/ranger" vs "bendpak").
BRAND_ALIASES: dict[str, list[str]] = {
    "ari-hetra":    ["ari", "ari hetra", "ari-hetra"],
    "bendpak":      ["bendpak", "bendpak/ranger", "ranger"],
    "challenger":   ["challenger"],
    "coats":        ["coats"],
    "forward":      ["forward lift", "forward"],
    "gray":         ["gray", "gray manufacturing"],
    "hunter":       ["hunter", "hunter engineering"],
    "mahle":        ["mahle"],
    "mohawk":       ["mohawk"],
    "pks":          ["pks", "professional kar saver", "professional kar-saver"],
    "rotary":       ["rotary lift", "rotary"],
    "stertil-koni": ["stertil", "stertil-koni", "stertil koni"],
}


def normalize_sku(s: str) -> str:
    """Lowercase, strip hyphens/spaces/underscores so 'CL-12A' == 'cl12a'."""
    return re.sub(r"[\s\-_]+", "", (s or "").lower())


def parse_ali_date(s: str) -> str | None:
    """ALI dates come as MM/DD/YYYY. Return ISO yyyy-mm-dd or None."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ALI cert match")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change; do not write to DB")
    ap.add_argument("--verbose", action="store_true",
                    help="Print every matched row")
    args = ap.parse_args()

    # --- load ALI records, group by normalized brand ---
    if not ALI_PATH.exists():
        print(f"ERROR: {ALI_PATH} missing", file=sys.stderr)
        return 2
    ali_records = json.loads(ALI_PATH.read_text(encoding="utf-8"))
    print(f"ALI records loaded: {len(ali_records)}")

    # Build an index: brand_alias_lower -> list of (sku_norm, parsed_date, capacity, raw_record)
    ali_by_brand: dict[str, list[tuple[str, str | None, int | None, dict]]] = defaultdict(list)
    for r in ali_records:
        brand_lower = (r.get("brand") or "").lower().strip()
        if not brand_lower:
            continue
        cert = parse_ali_date(r.get("certification_date", ""))
        cap_raw = r.get("capacity_lbs")
        try:
            cap = int(cap_raw) if cap_raw is not None else None
        except (TypeError, ValueError):
            cap = None
        skus_to_index = set()
        for k in ("model", "model_base"):
            v = r.get(k)
            if v:
                skus_to_index.add(normalize_sku(v))
        for sku_norm in skus_to_index:
            if sku_norm:
                ali_by_brand[brand_lower].append((sku_norm, cert, cap, r))

    # --- load our products ---
    conn = psycopg.connect(load_db_url(), autocommit=False)
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT p.id, p.sku, p.family_name, p.brand_id,
               lower(b.name) AS brand_name,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus,
               p.is_ali_certified, p.ali_cert_date,
               p.capacity_lbs
        FROM products p
        JOIN brands b ON b.id = p.brand_id
        ORDER BY b.name, p.sku
    """)
    products = cur.fetchall()
    print(f"Products in DB: {len(products)}")

    # --- match ---
    updates: list[tuple[int, str, str | None, str]] = []  # (pid, sku, ali_date, source_skus)
    no_match: list[dict] = []
    per_brand_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"matched": 0, "unmatched": 0}
    )

    MIN_PREFIX_LEN = 4

    for p in products:
        brand = p["brand_name"]
        aliases = BRAND_ALIASES.get(brand, [brand])
        candidate_ali: list[tuple[str, str | None, int | None, dict]] = []
        for alias in aliases:
            candidate_ali.extend(ali_by_brand.get(alias, []))
        if not candidate_ali:
            no_match.append(p)
            per_brand_stats[brand]["unmatched"] += 1
            continue

        product_skus_norm: set[str] = set()
        product_skus_norm.add(normalize_sku(p["sku"]))
        for vs in (p["variant_skus"] or []):
            product_skus_norm.add(normalize_sku(vs))
        product_skus_norm.discard("")

        matched_dates: list[str] = []
        matched_skus: list[str] = []
        for ali_sku, cert_iso, _ali_cap, raw in candidate_ali:
            hit = False
            # (a) exact normalized match
            if ali_sku in product_skus_norm:
                hit = True
            else:
                # (b) prefix match either direction, min 4 chars
                for psn in product_skus_norm:
                    if len(psn) >= MIN_PREFIX_LEN and ali_sku.startswith(psn):
                        hit = True
                        break
                    if len(ali_sku) >= MIN_PREFIX_LEN and psn.startswith(ali_sku):
                        hit = True
                        break
            if hit:
                if cert_iso:
                    matched_dates.append(cert_iso)
                matched_skus.append(raw.get("model") or raw.get("model_base") or "?")

        if not matched_skus:
            no_match.append(p)
            per_brand_stats[brand]["unmatched"] += 1
            continue

        earliest = min(matched_dates) if matched_dates else None
        updates.append((p["id"], p["sku"], earliest, ", ".join(sorted(set(matched_skus)))[:80]))
        per_brand_stats[brand]["matched"] += 1

    # --- report ---
    print()
    print("Per-brand match counts:")
    print(f"  {'brand':<14} {'matched':>8} {'unmatched':>10} {'total':>6}")
    print(f"  {'-'*14} {'-'*8} {'-'*10} {'-'*6}")
    for brand in sorted(per_brand_stats):
        s = per_brand_stats[brand]
        tot = s["matched"] + s["unmatched"]
        print(f"  {brand:<14} {s['matched']:>8} {s['unmatched']:>10} {tot:>6}")
    print()
    print(f"TOTAL would-update: {len(updates)} of {len(products)} ({100*len(updates)//max(1,len(products))}%)")

    if args.verbose:
        print()
        print("First 30 matches:")
        for pid, sku, dt, sources in updates[:30]:
            print(f"  pid={pid:<4} sku={sku:<20} date={dt or 'n/a':<12} <- ALI: {sources}")

    if args.dry_run:
        print()
        print("[DRY RUN] No changes written.")
        return 0

    # --- apply ---
    applied = 0
    for pid, sku, dt, _ in updates:
        if dt is not None:
            cur.execute(
                "UPDATE products SET is_ali_certified=TRUE, ali_cert_date=%s WHERE id=%s",
                (dt, pid),
            )
        else:
            cur.execute(
                "UPDATE products SET is_ali_certified=TRUE WHERE id=%s",
                (pid,),
            )
        applied += 1
    conn.commit()
    print()
    print(f"Applied {applied} updates.")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
