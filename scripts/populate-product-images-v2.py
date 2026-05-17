#!/usr/bin/env python3
"""
Populate products.image_url by searching the open web for
"<brand display name> <sku>" and pulling og:image from the top result
that (a) actually mentions our SKU on the page, and (b) isn't a
placeholder/logo/favicon.

This is the v2 strategy after the manufacturer-sitemap approach hit walls
for everyone except BendPak. The realization: vendors who resell these
lifts (alltiresupply.com, candaequipment.net, garageappeal.com,
proformancesupply.com, etc.) maintain clean product pages with proper
og:image meta tags, and a DDG search reliably surfaces them in the top
3-5 results.

Pipeline per product:
  1. ddgs.text("<BrandDisplayName> <SKU> lift", max_results=10)
  2. Drop blocked domains (Wikipedia, ManualsLib, YouTube, social, etc.)
  3. For each remaining URL in order:
       - urllib GET the raw HTML
       - require SKU appears in page text (proves we're on the right page)
       - extract og:image / twitter:image / JSON-LD Product.image /
         Magento /media/catalog/product/ / WP /wp-content/uploads/
       - reject image_url matching placeholder/logo/favicon/thumb patterns
       - first valid wins
  4. Save (image_url, image_source_url, image_fetched_at) to the row.

Idempotent: re-running only fills NULL image_url unless --force.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

try:
    from ddgs import DDGS  # type: ignore
except ImportError:
    print("ERROR: ddgs not installed. Run: pip install ddgs", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# What to call each brand in the search query. The CASING and SPACING here
# is what makes the search engine return the right brand vs. confused
# results (e.g., "challenger" alone returns the car brand; "Challenger
# Lifts" returns our actual target).
BRAND_DISPLAY: dict[str, str] = {
    "ari-hetra":    "ARI-Hetra",
    "bendpak":      "BendPak",
    "challenger":   "Challenger Lifts",
    "coats":        "Coats",
    "forward":      "Forward Lift",
    "gray":         "Gray Manufacturing",
    "hunter":       "Hunter Engineering",
    "mahle":        "Mahle",
    "mohawk":       "Mohawk Lifts",
    "pks":          "PKS Lifts",
    "rotary":       "Rotary Lift",
    "stertil-koni": "Stertil-Koni",
}

# Domains to skip — they exist but rarely yield a clean product photo
# we can use, and parsing them is fragile.
BLOCKED_DOMAINS = {
    "en.wikipedia.org", "wikipedia.org",
    "manualslib.com", "manualzz.com", "manualzilla.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "facebook.com", "www.facebook.com", "twitter.com", "x.com",
    "instagram.com", "www.instagram.com",
    "linkedin.com", "www.linkedin.com",
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "quora.com", "pinterest.com", "tiktok.com",
    "amazon.com", "www.amazon.com", "amazon.ca", "amazon.co.uk",
    "ebay.com", "www.ebay.com",
    "alibaba.com", "www.alibaba.com", "aliexpress.com",
    "shopify.com", "myshopify.com",  # storefront subdomains stay, this is exact-match
    "google.com", "www.google.com",
    "bing.com", "duckduckgo.com",
}

_OG_PATTERNS = [
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
    r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']twitter:image["\']',
]

_BAD_IMAGE_PATTERNS = re.compile(
    r"(?:placeholder|/logo[-_./]|favicon|icon-|cropped[-_]cropped[-_]"
    r"|/default/|default-thumb|store-thumbnail|custom[-_]thumb"
    r"|spinner|loading\.gif|/avatar/|/svg/|\.svg(\?|$))",
    re.I,
)


def load_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return url
    for envfile in (".env.local", ".env"):
        ep = REPO_ROOT / envfile
        if not ep.exists():
            continue
        for line in ep.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            if k.strip() in ("DATABASE_URL", "POSTGRES_URL"):
                return v.strip().strip('"').strip("'")
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)


def http_get(url: str, max_bytes: int = 2_000_000, timeout: int = 12) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes)
            return r.status, r.headers.get("Content-Type", ""), data
    except urllib.error.HTTPError as e:
        return e.code, "", b""
    except Exception:
        return 0, "", b""


def normalize_for_match(s: str) -> str:
    return re.sub(r"[\s\-_./]+", "", (s or "").lower())


def page_mentions_sku(html: str, sku_norms: set[str]) -> bool:
    text = re.sub(r"<[^>]+>", " ", html)
    text_norm = normalize_for_match(text)
    for sn in sku_norms:
        if sn and len(sn) >= 3 and sn in text_norm:
            return True
    return False


def _walk_json_for_product_image(node):
    if isinstance(node, dict):
        atype = node.get("@type")
        if (
            (isinstance(atype, str) and atype.lower() == "product")
            or (isinstance(atype, list) and any(str(t).lower() == "product" for t in atype))
        ):
            img = node.get("image")
            if isinstance(img, str):
                return img
            if isinstance(img, list) and img:
                first = img[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    return first.get("url") or first.get("@id")
            if isinstance(img, dict):
                return img.get("url") or img.get("@id")
        for v in node.values():
            r = _walk_json_for_product_image(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _walk_json_for_product_image(v)
            if r:
                return r
    return None


def extract_image_from_html(html: str, sku_norms: set[str]) -> str | None:
    # 1) og:image / twitter:image
    for pat in _OG_PATTERNS:
        m = re.search(pat, html, re.I)
        if m:
            cand = m.group(1).strip()
            if cand and not _BAD_IMAGE_PATTERNS.search(cand):
                return cand
    # 2) JSON-LD Product.image
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
    ):
        body = m.group(1).strip()
        try:
            data = json.loads(body)
        except Exception:
            try:
                data = json.loads(body.replace("&quot;", '"').replace("&amp;", "&"))
            except Exception:
                continue
        img = _walk_json_for_product_image(data)
        if img and not _BAD_IMAGE_PATTERNS.search(img):
            return img
    # 3) Magento product gallery (BendPak / many e-comm)
    for m in re.findall(
        r'https?://[^\s"\'<>]+/media/catalog/product/(?!cache/)[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)',
        html, re.I,
    ):
        if not _BAD_IMAGE_PATTERNS.search(m):
            return m
    # 4) WordPress uploads — prefer those whose filename mentions a SKU
    wp_cands = re.findall(
        r'https?://[^\s"\'<>]+/wp-content/uploads/[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)',
        html, re.I,
    )
    if wp_cands:
        scored = []
        for u in wp_cands:
            if _BAD_IMAGE_PATTERNS.search(u):
                continue
            name = urllib.parse.urlparse(u).path.rsplit("/", 1)[-1].lower()
            name_norm = re.sub(r"[\s\-_.]+", "", name)
            hits = sum(1 for sn in sku_norms if sn and len(sn) >= 3 and sn in name_norm)
            scored.append((hits, u))
        scored.sort(key=lambda t: (-t[0], len(t[1])))
        if scored and scored[0][0] > 0:
            return scored[0][1]
    return None


def domain_ok(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    if host in BLOCKED_DOMAINS:
        return False
    # Block hostless / non-HTTP schemes
    return True


def search_candidates(brand_display: str, sku: str, max_results: int = 10) -> list[str]:
    """Return ordered list of candidate URLs from a web search.
    Adds "lift" to disambiguate from non-lift products with similar SKUs.
    """
    q = f"{brand_display} {sku} lift"
    try:
        out = list(DDGS().text(q, max_results=max_results))
    except Exception:
        # Rate-limited or transient — retry once after a short pause
        time.sleep(2.0)
        try:
            out = list(DDGS().text(q, max_results=max_results))
        except Exception:
            return []
    urls = []
    for r in out:
        u = r.get("href") or r.get("link") or ""
        if u and domain_ok(u):
            urls.append(u)
    return urls


def find_image_for_product(brand_display: str, sku: str, variant_skus: list[str], verbose: bool = False) -> tuple[str, str] | None:
    sku_norms: set[str] = {normalize_for_match(sku)}
    for vs in (variant_skus or []):
        sku_norms.add(normalize_for_match(vs))
    sku_norms.discard("")

    urls = search_candidates(brand_display, sku)
    if verbose:
        print(f"    candidates: {len(urls)}")
    for u in urls:
        status, ct, data = http_get(u)
        if status != 200 or "html" not in ct.lower():
            continue
        try:
            html = data.decode("utf-8", errors="replace")
        except Exception:
            continue
        if not page_mentions_sku(html, sku_norms):
            if verbose:
                print(f"    skip (no-sku)  {u[:90]}")
            continue
        img = extract_image_from_html(html, sku_norms)
        if not img:
            if verbose:
                print(f"    skip (no-img)  {u[:90]}")
            continue
        # Absolutize relative
        if img.startswith("//"):
            img = "https:" + img
        elif img.startswith("/"):
            origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(u))
            img = origin + img
        if _BAD_IMAGE_PATTERNS.search(img):
            if verbose:
                print(f"    skip (bad-img) {img[:90]}")
            continue
        if verbose:
            print(f"    HIT  {img[:90]}")
        return (img, u)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="only this brand (lowercase)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="re-search even if image_url is already set")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.8,
                    help="pause between searches (rate-limit politeness)")
    args = ap.parse_args()

    db_url = load_db_url()
    conn = psycopg.connect(db_url, autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    def reconnect():
        nonlocal conn, cur
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        conn = psycopg.connect(db_url, autocommit=False)
        cur = conn.cursor(row_factory=dict_row)

    def safe_update(img: str, src: str, pid: int):
        # Neon idles out SSL connections after a few minutes of inactivity.
        # Retry once with a fresh connection on OperationalError so a long
        # search run doesn't bail mid-way.
        for attempt in range(2):
            try:
                cur.execute(
                    "UPDATE products SET image_url=%s, image_source_url=%s, image_fetched_at=NOW() WHERE id=%s",
                    (img, src, pid),
                )
                conn.commit()
                return
            except (psycopg.OperationalError, psycopg.InterfaceError):
                if attempt == 0:
                    reconnect()
                else:
                    raise

    where_extra = "AND lower(b.name) = %s" if args.brand else ""
    params = [args.brand.lower()] if args.brand else []
    img_filter = "" if args.force else "AND p.image_url IS NULL"
    cur.execute(f"""
        SELECT p.id, p.sku, lower(b.name) AS brand,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.source <> 'svi-catalog'
          {img_filter}
          {where_extra}
        ORDER BY b.name, p.sku
    """, params)
    targets = cur.fetchall()
    if args.limit:
        targets = targets[: args.limit]
    print(f"Products to image-search: {len(targets)}")

    hits = 0
    misses = 0
    started = time.time()
    for i, p in enumerate(targets, 1):
        brand_display = BRAND_DISPLAY.get(p["brand"], p["brand"].title())
        result = find_image_for_product(
            brand_display, p["sku"], p["variant_skus"], args.verbose,
        )
        if result is None:
            misses += 1
            if args.verbose or i % 20 == 0:
                rate = (hits + misses) / max(1.0, (time.time() - started))
                print(f"  [{i:>3}/{len(targets)}] MISS  {brand_display:<22} {p['sku']:<22}  ({rate:.1f}/s, hits={hits})")
            time.sleep(args.sleep)
            continue
        img, src = result
        safe_update(img, src, p["id"])
        hits += 1
        if args.verbose or i % 20 == 0:
            rate = (hits + misses) / max(1.0, (time.time() - started))
            print(f"  [{i:>3}/{len(targets)}] HIT   {brand_display:<22} {p['sku']:<22}  ({rate:.1f}/s, hits={hits})")
        time.sleep(args.sleep)

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min   hits={hits}  misses={misses}  rate={hits/max(1,hits+misses)*100:.0f}%")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
