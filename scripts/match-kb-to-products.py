"""Match knowledge_items rows to products and populate product_documents.

For every tier-1-public PDF in knowledge_items whose brand is one of the
12 lift brands, find the best-matching product family by scanning filename
+ title for product SKUs (family + variants), and classify doc_type from
filename keywords.

Pass 1 — SKU matcher (free):
  - Build a per-brand map of {sku: product_id}
  - For each KB row, scan filename + title for SKU substrings
  - Pick the LONGEST match (handles prefix collisions like CL12A vs CL12)
  - Classify doc_type from filename keywords
  - Insert into product_documents

Pass 2 — Claude fallback (optional, --with-fallback):
  - For KB rows that didn't match in pass 1, send title + filename + first
    500 chars of body to Claude with the brand's full product list as
    context. Ask: "which product family does this document, and what
    type of doc is it?"
  - ~$0.005 per call. Cap with --max-fallback to bound spend.

Usage:
  python scripts/match-kb-to-products.py --dry-run
  python scripts/match-kb-to-products.py
  python scripts/match-kb-to-products.py --with-fallback --max-fallback 200
  python scripts/match-kb-to-products.py --rebuild   # wipe product_documents first
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists(): return
    pat = re.compile(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if m and not os.environ.get(m.group(1)):
                os.environ[m.group(1)] = m.group(2)


_load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Doc-type classification — regex match on filename (lowercase). Order
# matters; first hit wins. Tuned so install + service manuals don't
# collide on "manual".
DOC_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"\bparts[\s\-_]*list\b|\bparts[\s\-_]*diagram\b|\bexploded[\s\-_]*view\b|\bpart[\s\-_]*number\b|\bpartlist\b|\bexploded\b", "parts-diagram"),
    (r"\bservice[\s\-_]*manual\b|\bservice[\s\-_]*repair\b|\brepair[\s\-_]*manual\b|\btsb\b|\btechnical[\s\-_]*service[\s\-_]*bulletin\b", "service-manual"),
    (r"\biom\b|\binstall[\s\-_]*manual\b|\binstallation\b|\binstall[\s\-_]*operation\b|\boperation[\s\-_]*manual\b|\boperator[\s\-_]*manual\b|\b[\-_]?manual\b|\b[\-_]?-manual\b", "install-manual"),
    (r"\bspec[\s\-_]*sheet\b|\bspecification\b|\bdata[\s\-_]*sheet\b|\b[\-_]?spec\b|\bsalessheet\b", "spec-sheet"),
    (r"\bbrochure\b|\bcatalog\b|\bsales[\s\-_]*flyer\b|\bsales[\s\-_]*sheet\b|\boverview\b|\bone[\-_\s]*pager\b|\bsalesflyer\b", "brochure"),
    (r"\bprice[\s\-_]*sheet\b|\bprice[\s\-_]*list\b|\bpricelist\b|\bpricing\b", "price-sheet"),
]


def classify_doc_type(filename: str, title: str) -> str:
    blob = ((filename or "") + " " + (title or "")).lower()
    for pat, dtype in DOC_TYPE_PATTERNS:
        if re.search(pat, blob):
            return dtype
    return "other"


def build_sku_index(conn):
    """Returns {brand_id: [(sku_lower, product_id, is_variant)]}, sorted by sku
    length DESC so the longest match wins when scanning a KB row."""
    with conn.cursor() as cur:
        cur.execute("""
          SELECT id, brand_id, sku, coalesce(variant_skus,'[]'::jsonb)
          FROM products
        """)
        per_brand: dict[int, list[tuple[str, int, bool]]] = defaultdict(list)
        for pid, bid, sku, variants in cur.fetchall():
            sku_l = (sku or "").strip()
            if sku_l:
                per_brand[bid].append((sku_l.lower(), pid, False))
            v_list = variants if isinstance(variants, list) else []
            for v in v_list:
                vs = str(v).strip()
                if vs:
                    per_brand[bid].append((vs.lower(), pid, True))
    # Sort each brand's index by SKU length descending. Tie-breaker: family
    # SKUs first (is_variant=False) so a FAMILY match outranks a VARIANT match
    # of the same length.
    for bid, lst in per_brand.items():
        lst.sort(key=lambda x: (-len(x[0]), x[2]))
    return per_brand


def scan_for_sku(text: str, brand_skus: list[tuple[str, int, bool]]) -> Optional[tuple[str, int, bool]]:
    """Return the (sku, product_id, is_variant) of the LONGEST SKU found
    as a word-boundary-ish substring in `text` (already lowercase). The
    sku list is pre-sorted longest first."""
    for sku, pid, is_var in brand_skus:
        if len(sku) < 3:  # skip pathological 1-2 char SKUs
            continue
        # word boundary match — a SKU shouldn't be embedded in a longer
        # alphanumeric token (CL12 in CL12A would otherwise spuriously match)
        # We allow leading/trailing non-alphanum (space, hyphen, dot, slash, etc.)
        pat = r"(?:(?<=\W)|^)" + re.escape(sku) + r"(?=\W|$)"
        if re.search(pat, text):
            return (sku, pid, is_var)
    return None


def fetch_kb_rows(conn, lift_brand_ids: list[int]):
    """Returns KB rows we want to attempt to match: tier-1-public rows for
    the 12 lift brands, with PDF or doc-shaped extensions. We also pull
    the first 4K of body so SKU-scanning can hit on body content (many
    PDFs have descriptive titles but the model number lives in the body)."""
    with conn.cursor() as cur:
        cur.execute("""
          SELECT ki.id, ki.title, ki.source_filename, ki.source_path,
                 ki.brand_id, ki.category, ki.extractor_version,
                 left(coalesce(ki.raw_content, ki.search_text, ''), 4000) AS body_snippet
          FROM knowledge_items ki
          WHERE ki.brand_id = ANY(%s)
            AND (ki.extractor_version IS NULL OR ki.extractor_version != 'catalog-db-migration')
            AND (
              'tier-1-public' = ANY(ki.category)
              OR ki.source_filename ~* '\\.(pdf|docx|xlsx|pptx)$'
            )
          ORDER BY ki.id
        """, (lift_brand_ids,))
        return cur.fetchall()


SYSTEM_FALLBACK = """You match a single product PDF to one product family from a brand's catalog.

You'll get:
- The PDF's filename + title + first 500 chars of extracted body text
- A list of product families for the brand (sku, family_name, capacity_lbs)

Pick the SINGLE best-matching product family by sku, OR return null if no clear match exists. Also classify the doc type.

Output STRICT JSON: {"sku": "<family-sku or null>", "doc_type": "<one of: spec-sheet, install-manual, service-manual, parts-diagram, brochure, price-sheet, other>", "confidence": "<low|medium|high>", "reason": "<one short sentence>"}"""


def claude_match_one(client, brand_name: str, families: list[tuple[str, str, int | None]],
                     filename: str, title: str, body_snippet: str) -> dict:
    family_block = "\n".join(
        f"- {sku} | {family_name} | capacity={cap}"
        for sku, family_name, cap in families
    )
    user = f"""Brand: {brand_name}

Product families for this brand:
{family_block}

PDF to match:
- Filename: {filename!r}
- Title: {title!r}
- Body snippet (first 500 chars): {body_snippet!r}

Pick the family. JSON only."""
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        system=SYSTEM_FALLBACK,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"sku": None, "doc_type": "other", "confidence": "low",
                "reason": f"non-json: {text[:80]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="run match logic, print summary, but don't write to DB")
    ap.add_argument("--rebuild", action="store_true",
                    help="DELETE all existing product_documents rows before re-matching")
    ap.add_argument("--with-fallback", action="store_true",
                    help="also run Claude on KB rows that SKU-matching missed")
    ap.add_argument("--max-fallback", type=int, default=300,
                    help="cap the Claude fallback calls (default 300)")
    args = ap.parse_args()

    import psycopg
    LIFT_BRANDS = [
        "challenger", "bendpak", "mohawk", "rotary", "hunter", "stertil-koni",
        "ari-hetra", "pks", "gray", "coats", "mahle", "forward",
    ]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM brands WHERE lower(name) = ANY(%s)",
                        ([b.lower() for b in LIFT_BRANDS],))
            brand_id_to_name = {bid: name for bid, name in cur.fetchall()}
            lift_brand_ids = list(brand_id_to_name.keys())
        print(f"[match] {len(lift_brand_ids)} lift brands resolved")

        sku_index = build_sku_index(conn)
        total_skus = sum(len(v) for v in sku_index.values())
        print(f"[match] indexed {total_skus} SKUs across products+variants")

        kb_rows = fetch_kb_rows(conn, lift_brand_ids)
        print(f"[match] candidate KB rows: {len(kb_rows)}")

        # SKU pass — try filename+title+path first (fast, almost always
        # right when it hits). Fall back to scanning the body if nothing
        # matched in the metadata. Many product PDFs have generic titles
        # like "Installation, Operation & Maintenance Manual" but the
        # actual model number lives in the first page of body text.
        matches: list[dict] = []  # {kb_id, product_id, doc_type, sku_matched, is_variant}
        unmatched: list[tuple] = []
        for ki_id, title, sfn, spath, bid, cat, ver, body in kb_rows:
            meta_text = ((sfn or "") + " " + (title or "") + " " + (spath or "")).lower()
            res = scan_for_sku(meta_text, sku_index.get(bid, []))
            via = "metadata"
            if res is None and body:
                body_text = body.lower()
                res = scan_for_sku(body_text, sku_index.get(bid, []))
                via = "body"
            if res is None:
                unmatched.append((ki_id, title, sfn, bid))
                continue
            sku_matched, pid, is_var = res
            doc_type = classify_doc_type(sfn or "", title or "")
            matches.append({
                "ki_id": ki_id, "product_id": pid, "doc_type": doc_type,
                "sku_matched": sku_matched, "is_variant": is_var,
                "via": via,
            })

        print()
        print(f"[match] PASS 1 (SKU-only) results:")
        print(f"  matched:   {len(matches)}  (via metadata={sum(1 for m in matches if m.get('via')=='metadata')}, via body={sum(1 for m in matches if m.get('via')=='body')})")
        print(f"  unmatched: {len(unmatched)}")
        cnt = Counter(m["doc_type"] for m in matches)
        print(f"  doc_type distribution:")
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"    {v:>5}  {k}")

        # Pass 2 — Claude fallback (optional)
        fallback_matches: list[dict] = []
        if args.with_fallback and unmatched:
            import anthropic
            client = anthropic.Anthropic()
            # Pre-fetch family list per brand for the prompt context
            with conn.cursor() as cur:
                cur.execute("""
                  SELECT brand_id, id, sku, family_name, capacity_lbs
                  FROM products
                  WHERE brand_id = ANY(%s)
                  ORDER BY brand_id, capacity_lbs NULLS LAST, sku
                """, (lift_brand_ids,))
                fam_per_brand: dict[int, list[tuple[int, str, str, int]]] = defaultdict(list)
                sku_to_pid: dict[tuple[int, str], int] = {}
                for bid, pid, sku, fname, cap in cur.fetchall():
                    fam_per_brand[bid].append((pid, sku, fname, cap))
                    sku_to_pid[(bid, sku.lower())] = pid

            cap_n = min(args.max_fallback, len(unmatched))
            print(f"\n[match] PASS 2 (Claude) on {cap_n} of {len(unmatched)} unmatched (capped at --max-fallback)")
            for i, (ki_id, title, sfn, bid) in enumerate(unmatched[:cap_n]):
                # fetch body snippet
                with conn.cursor() as cur:
                    cur.execute("SELECT coalesce(left(raw_content, 500), '') FROM knowledge_items WHERE id=%s", (ki_id,))
                    snippet = cur.fetchone()[0] or ""
                families = [(sku, fname, cap) for _pid, sku, fname, cap in fam_per_brand.get(bid, [])]
                if not families:
                    continue
                brand_name = brand_id_to_name.get(bid, "?")
                try:
                    out = claude_match_one(client, brand_name, families, sfn or "", title or "", snippet)
                except Exception as e:
                    print(f"  [fallback {i+1}/{cap_n}] error: {e}")
                    continue
                sku_picked = (out.get("sku") or "").strip().lower()
                if not sku_picked or sku_picked in ("null", "none"):
                    continue
                pid = sku_to_pid.get((bid, sku_picked))
                if pid is None:
                    continue
                doc_type = out.get("doc_type") or "other"
                if doc_type not in {"spec-sheet","install-manual","service-manual","parts-diagram","brochure","price-sheet","other"}:
                    doc_type = "other"
                fallback_matches.append({
                    "ki_id": ki_id, "product_id": pid, "doc_type": doc_type,
                    "sku_matched": sku_picked, "is_variant": False,
                    "claude_confidence": out.get("confidence","?"),
                })
            print(f"  Claude resolved: {len(fallback_matches)} additional matches")

        all_matches = matches + fallback_matches
        print(f"\n[match] TOTAL matches to write: {len(all_matches)}")

        if args.dry_run:
            print("[match] DRY-RUN — not writing.")
            print("\nSample matches (first 10):")
            for m in all_matches[:10]:
                print(f"  ki={m['ki_id']} → pid={m['product_id']} via sku={m['sku_matched']!r} doc={m['doc_type']}")
            return 0

        # Write
        with conn.cursor() as cur:
            if args.rebuild:
                cur.execute("DELETE FROM product_documents")
                print(f"[match] WIPED {cur.rowcount} existing product_documents rows")

            inserted = 0
            updated = 0
            for m in all_matches:
                cur.execute(
                    """
                    INSERT INTO product_documents (
                      product_id, knowledge_item_id, doc_type, is_primary
                    ) VALUES (%s, %s, %s, FALSE)
                    ON CONFLICT (product_id, knowledge_item_id)
                    DO UPDATE SET doc_type = EXCLUDED.doc_type
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (m["product_id"], m["ki_id"], m["doc_type"]),
                )
                row = cur.fetchone()
                if row and row[0]:
                    inserted += 1
                else:
                    updated += 1
        conn.commit()
        print(f"[match] DB: inserted={inserted} updated={updated}")

        # Mark is_primary: for each (product_id, doc_type), set the LARGEST
        # by raw_content length as the primary doc of that type.
        with conn.cursor() as cur:
            cur.execute("""
              WITH ranked AS (
                SELECT pd.id,
                       ROW_NUMBER() OVER (
                         PARTITION BY pd.product_id, pd.doc_type
                         ORDER BY length(coalesce(ki.raw_content,'')) DESC, pd.id
                       ) AS rn
                FROM product_documents pd
                JOIN knowledge_items ki ON ki.id = pd.knowledge_item_id
              )
              UPDATE product_documents
              SET is_primary = (ranked.rn = 1)
              FROM ranked WHERE ranked.id = product_documents.id
            """)
        conn.commit()
        print(f"[match] is_primary marked: longest doc per (product, doc_type) wins")

        # Final summary
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM product_documents")
            total = cur.fetchone()[0]
            cur.execute("""
              SELECT b.name, count(*) FILTER (WHERE pd.id IS NOT NULL) AS docs,
                     count(*) FILTER (WHERE pd.id IS NULL) AS no_docs
              FROM products p JOIN brands b ON b.id = p.brand_id
              LEFT JOIN product_documents pd ON pd.product_id = p.id
              GROUP BY b.name ORDER BY docs DESC
            """)
            print()
            print(f"product_documents total: {total}")
            print(f"products with/without docs by brand:")
            for n, with_d, without in cur.fetchall():
                print(f"  {n:14s} with={with_d:>4}  rows-without-any-doc={without}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
