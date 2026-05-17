#!/usr/bin/env python3
"""
Populate products.image_url for the 442 non-SVI products by walking each
brand's sitemap, matching URLs to SKUs, and scraping og:image meta tags
from the resulting product pages.

Why sitemaps instead of search engines: Bing/DDG/Google all serve
Cloudflare-Turnstile captchas to scripted requests in 2026. Sitemaps
are the manufacturer's own published index, no rate limiting, no
captchas. The trade-off is per-brand path patterns (BendPak embeds
SKU as a slug; Hunter uses /equipment-and-tools/<category>/<sku>/;
etc.), so we accept some brand-specific tuning.

Strategy per product:
  1. Resolve the brand to its sitemap roots (handle sitemap indexes
     recursively).
  2. Build a normalized-SKU pool from sku + variant_skus.
  3. Scan every sitemap URL; keep ones whose last path segment matches
     the normalized SKU (case-insensitive, hyphens/underscores stripped).
  4. Sort matches by best-fit (exact > prefix); pick the first.
  5. Fetch that URL, parse og:image / twitter:image meta tag.
  6. Write image_url + image_source_url + image_fetched_at to the DB.

Skips SVI-imported products entirely. Idempotent — re-running only
re-fetches rows where image_url IS NULL (or --force to overwrite).
"""

from __future__ import annotations
import argparse
import gzip
import io
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

REPO_ROOT = Path(__file__).resolve().parent.parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Per-brand URL-path allowlist regexes. When set, the sitemap matcher
# only considers URLs whose path matches one of these patterns. This
# kills false positives where a brand's sitemap mixes blog posts,
# accessory listings, and product pages all containing SKU substrings.
# BendPak's URLs are flat (product pages live at /<slug>/) so it has
# no filter and accepts every URL.
BRAND_URL_PATH_FILTERS: dict[str, list[str]] = {
    "challenger": [r"/car_lift/"],
    "rotary":     [r"/lift/", r"/products?/"],
    "mahle":      [r"/products/"],
    "hunter":     [r"/equipment-and-tools/", r"/products/"],
    "mohawk":     [r"/lifts?/", r"/products/"],
    "forward":    [r"/products?/", r"/lifts?/"],
    "stertil-koni": [r"/products?/", r"/lifts?/"],
}


# Per-brand direct URL patterns. {sku} and {sku_lower} are the substitutions.
# When a brand's sitemap is empty/useless, we fall back to trying these
# directly. The first one that returns 200 with HTML wins. Order matters —
# put the most specific / likely-correct pattern first.
BRAND_DIRECT_URL_PATTERNS: dict[str, list[str]] = {
    "challenger": [
        "https://challengerlifts.com/{sku}/",
        "https://challengerlifts.com/{sku}",
        "https://www.challengerlifts.com/{sku}",
    ],
    "rotary": [
        "https://www.rotarylift.com/products/{sku}/",
        "https://www.rotarylift.com/{sku}/",
    ],
    "mohawk": [
        "https://mohawklifts.com/products/{sku_lower}/",
        "https://mohawklifts.com/lift/{sku_lower}/",
    ],
}

# Per-brand sitemap roots. Some brands have a single big sitemap; others
# use a sitemap index that points at many topic-specific sitemaps. The
# walker follows indexes one level deep.
BRAND_SITEMAPS: dict[str, list[str]] = {
    "bendpak": ["https://www.bendpak.com/sitemap.xml"],
    "rotary": [
        "https://www.rotarylift.com/sitemap_index.xml",
        "https://www.rotarylift.com/sitemap.xml",
    ],
    "challenger": ["https://challengerlifts.com/sitemap_index.xml"],
    "mahle": ["https://www.servicesolutions.mahle.com/sitemap.xml"],
    "hunter": ["https://www.hunter.com/sitemap.xml"],
    "mohawk": [
        "https://mohawklifts.com/sitemap_index.xml",
        "https://mohawklifts.com/sitemap.xml",
    ],
    "pks": ["https://www.pkslifts.com/sitemap.xml"],
    "coats": ["https://www.coatsgarage.com/sitemap.xml"],
    "ari-hetra": ["https://www.arihetra.com/sitemap.xml"],
    "stertil-koni": ["https://stertil-koni.com/sitemap_index.xml"],
    "forward": ["https://www.forwardlift.com/sitemap_index.xml"],
    "gray": ["https://www.grayinc.com/sitemap.xml"],
}


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


def http_get(url: str, max_bytes: int = 2_000_000) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(max_bytes)
            if (r.headers.get("Content-Encoding") == "gzip") or url.endswith(".gz"):
                try:
                    data = gzip.decompress(data)
                except Exception:
                    pass
            return r.status, r.headers.get("Content-Type", ""), data
    except urllib.error.HTTPError as e:
        return e.code, "", b""
    except Exception as e:
        return 0, "", str(e).encode()


def walk_sitemap(roots: list[str], depth: int = 0, max_depth: int = 2) -> list[str]:
    """Return every leaf URL across a brand's sitemap, recursing into
    sitemap indexes."""
    urls: list[str] = []
    for root in roots:
        status, _ct, data = http_get(root)
        if status != 200 or not data:
            continue
        body = data.decode("utf-8", errors="replace")
        # An index has <sitemap><loc>...</loc></sitemap> children; a leaf
        # has <url><loc>...</loc></url>. Both are reachable via <loc>;
        # we tell them apart by whether the loc URL ends in .xml/.xml.gz.
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body)
        for loc in locs:
            if loc.endswith(".xml") or loc.endswith(".xml.gz"):
                if depth < max_depth:
                    urls.extend(walk_sitemap([loc], depth + 1, max_depth))
            else:
                urls.append(loc)
    return urls


def normalize_sku(s: str) -> str:
    return re.sub(r"[\s\-_./]+", "", (s or "").lower())


_OG_PATTERNS = [
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
    r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']twitter:image["\']',
]

# Filename patterns that indicate the URL is NOT a real product photo.
# We refuse to store these even if they're returned by og:image, because
# they're almost always the site's default placeholder for missing
# products or a generic logo on a fallback page.
_BAD_IMAGE_PATTERNS = re.compile(
    r"(?:placeholder|/logo[-_./]|favicon|icon-|cropped[-_]cropped[-_]"
    r"|/default/|default-thumb|store-thumbnail|custom[-_]thumb"
    r"|spinner|loading\.gif)",
    re.I,
)


def is_bad_image_url(u: str) -> bool:
    return bool(_BAD_IMAGE_PATTERNS.search(u))


def page_mentions_sku(html: str, sku_norms: set[str]) -> bool:
    """Cheap proof-of-page-relevance: at least one of our normalized SKUs
    appears somewhere in the page (after stripping HTML tags + normalizing
    runs of whitespace/hyphens/underscores). If the SKU is nowhere on the
    page, the page is almost certainly a fallback / 404 / homepage and we
    should not trust its og:image."""
    # Strip tags and normalize
    text = re.sub(r"<[^>]+>", " ", html)
    text_norm = re.sub(r"[\s\-_./]+", "", text.lower())
    for sn in sku_norms:
        if sn and len(sn) >= 3 and sn in text_norm:
            return True
    return False


def _walk_json_for_product_image(node) -> str | None:
    """Recursively scan a JSON-LD node for a Product with an image.
    Returns the first plausible product image URL string, else None."""
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
        # Recurse into known nesting keys
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


def _extract_jsonld_image(html: str) -> str | None:
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.I | re.S,
    ):
        body = m.group(1).strip()
        try:
            data = json.loads(body)
        except Exception:
            # Sometimes Magento double-encodes — strip HTML entities
            body2 = body.replace("&quot;", '"').replace("&amp;", "&")
            try:
                data = json.loads(body2)
            except Exception:
                continue
        img = _walk_json_for_product_image(data)
        if img:
            return img
    return None


def _extract_magento_image(html: str) -> str | None:
    """Magento sites (BendPak) store product images at
    /media/catalog/product/<a>/<b>/<filename>.{jpg,png,webp}.
    The /cache/ subpath holds resized variants — prefer the non-cached
    original. Skip thumbnails (filename ending in -small, _thumb, etc.)."""
    pat = re.compile(
        r'https?://[^\s"\'<>]+/media/catalog/product/(?!cache/)[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)',
        re.I,
    )
    cands = pat.findall(html)
    if not cands:
        return None
    # Prefer ones without "-small" / "_thumb"
    cands.sort(key=lambda u: ("small" in u.lower() or "thumb" in u.lower(), len(u)))
    return cands[0]


def _extract_wp_uploads_image(html: str, sku_norms: set[str]) -> str | None:
    """WordPress sites usually serve product images from
    /wp-content/uploads/<year>/<month>/<filename>. If a filename mentions
    the SKU, that's our best bet."""
    pat = re.compile(
        r'https?://[^\s"\'<>]+/wp-content/uploads/[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp)',
        re.I,
    )
    cands = pat.findall(html)
    if not cands:
        return None
    # Prefer ones whose filename slug matches a SKU
    def score(u: str) -> tuple:
        name = urllib.parse.urlparse(u).path.rsplit("/", 1)[-1].lower()
        sku_hit = any(sn and sn in re.sub(r"[\s\-_]+", "", name) for sn in sku_norms)
        is_logo = "logo" in name or "icon" in name
        return (not sku_hit, is_logo, len(u))
    cands.sort(key=score)
    return cands[0]


def extract_og_image(html: str, sku_norms: set[str] | None = None) -> str | None:
    # 1) og:image / twitter:image — the cleanest
    for pat in _OG_PATTERNS:
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    # 2) JSON-LD Product.image — Magento (BendPak) and many e-comm platforms
    img = _extract_jsonld_image(html)
    if img:
        return img
    # 3) Magento product gallery path (covers BendPak even when JSON-LD parsing fails)
    img = _extract_magento_image(html)
    if img:
        return img
    # 4) WordPress uploads (Mohawk, Challenger fallback)
    if sku_norms:
        img = _extract_wp_uploads_image(html, sku_norms)
        if img:
            return img
    return None


def best_url_for_skus(
    urls: list[str], sku_norms: set[str], path_filters: list[str] | None = None,
) -> str | None:
    """From a candidate URL pool, pick the URL whose last path segment
    best matches one of our SKU norms. Prefers exact equality over prefix
    over substring. If path_filters is set, only URLs whose path matches
    one of the regex patterns are considered."""
    exact: list[str] = []
    prefix: list[str] = []
    substr: list[str] = []
    filter_res = [re.compile(p, re.I) for p in (path_filters or [])]
    for u in urls:
        if filter_res:
            path = urllib.parse.urlparse(u).path
            if not any(rx.search(path) for rx in filter_res):
                continue
        # Take the slug from the path
        path = urllib.parse.urlparse(u).path.rstrip("/")
        if not path:
            continue
        slug = path.rsplit("/", 1)[-1]
        slug_norm = normalize_sku(slug)
        if not slug_norm:
            continue
        for sn in sku_norms:
            if len(sn) < 3:
                continue
            if slug_norm == sn:
                exact.append(u)
                break
            if slug_norm.startswith(sn) or sn.startswith(slug_norm):
                prefix.append(u)
                break
            if sn in slug_norm:
                substr.append(u)
                break
    # Within a category, prefer shorter URLs (less specific path = less
    # likely to be a sub-variant or accessory listing)
    def pick(cands: list[str]) -> str | None:
        if not cands:
            return None
        cands.sort(key=lambda u: (len(urllib.parse.urlparse(u).path), u))
        return cands[0]
    return pick(exact) or pick(prefix) or pick(substr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="restrict to one brand (lowercase)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even if image_url already set")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    conn = psycopg.connect(load_db_url(), autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    # Pull non-SVI products only — that's the user's scope today
    where_extra = "AND lower(b.name) = %s" if args.brand else ""
    params = [args.brand.lower()] if args.brand else []
    img_where = "" if args.force else "AND p.image_url IS NULL"
    cur.execute(f"""
        SELECT p.id, p.sku, lower(b.name) AS brand,
               coalesce(p.variant_skus, '[]'::jsonb) AS variant_skus
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.source <> 'svi-catalog'
          {img_where}
          {where_extra}
        ORDER BY b.name, p.sku
    """, params)
    products = cur.fetchall()
    if args.limit:
        products = products[: args.limit]
    print(f"Products to attempt: {len(products)}")
    by_brand: dict[str, list[dict]] = {}
    for p in products:
        by_brand.setdefault(p["brand"], []).append(p)

    overall_hits = 0
    overall_misses = 0

    for brand, plist in by_brand.items():
        sitemap_roots = BRAND_SITEMAPS.get(brand)
        direct_patterns = BRAND_DIRECT_URL_PATTERNS.get(brand, [])
        urls: list[str] = []
        if sitemap_roots:
            print(f"\n[{brand}]  walking sitemap...")
            urls = walk_sitemap(sitemap_roots)
            print(f"[{brand}]  {len(urls)} URLs in sitemap")
        if not urls and not direct_patterns:
            print(f"\n[{brand}]  no sitemap & no direct patterns — {len(plist)} skipped")
            overall_misses += len(plist)
            continue
        if not urls:
            print(f"\n[{brand}]  empty sitemap; trying direct URL patterns")

        brand_hits = 0
        brand_misses = 0
        for p in plist:
            sku_norms: set[str] = {normalize_sku(p["sku"])}
            for vs in (p["variant_skus"] or []):
                sku_norms.add(normalize_sku(vs))
            sku_norms.discard("")

            path_filters = BRAND_URL_PATH_FILTERS.get(brand)
            page_url = (
                best_url_for_skus(urls, sku_norms, path_filters)
                if urls else None
            )

            # Fallback: try direct URL patterns with the SKU substituted
            if not page_url and direct_patterns:
                sku = p["sku"]
                for pat in direct_patterns:
                    candidate = pat.format(sku=sku, sku_lower=sku.lower())
                    status, ct, _ = http_get(candidate, max_bytes=500)
                    if status == 200 and "html" in ct.lower():
                        page_url = candidate
                        break

            if not page_url:
                brand_misses += 1
                if args.verbose:
                    print(f"  MISS  {p['sku']}")
                continue

            # Fetch the page, extract og:image
            status, ct, data = http_get(page_url)
            if status != 200 or "html" not in ct.lower():
                brand_misses += 1
                if args.verbose:
                    print(f"  NO-HTML  {p['sku']:<25} {status}  {page_url}")
                continue
            html = data.decode("utf-8", errors="replace")

            # Page-level relevance check: the SKU must appear in the page
            # text. Skips homepage/fallback pages that emit a generic
            # og:image (their site logo) for unknown URLs.
            if not page_mentions_sku(html, sku_norms):
                brand_misses += 1
                if args.verbose:
                    print(f"  NO-SKU-ON-PAGE  {p['sku']:<22} {page_url}")
                continue

            img = extract_og_image(html, sku_norms)
            if not img:
                brand_misses += 1
                if args.verbose:
                    print(f"  NO-OG    {p['sku']:<25} {page_url}")
                continue
            # Absolutize relative og:image
            if img.startswith("/"):
                origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
                img = origin + img
            # Reject placeholders / logos / favicons by filename pattern
            if is_bad_image_url(img):
                brand_misses += 1
                if args.verbose:
                    print(f"  BAD-IMG  {p['sku']:<25} {img}")
                continue

            brand_hits += 1
            if args.verbose or brand_hits <= 3:
                print(f"  HIT      {p['sku']:<25} -> {img[:120]}")

            if not args.dry_run:
                cur.execute(
                    """UPDATE products
                       SET image_url = %s,
                           image_source_url = %s,
                           image_fetched_at = NOW()
                       WHERE id = %s""",
                    (img, page_url, p["id"]),
                )
                conn.commit()
            # Be polite — small pause between fetches
            time.sleep(0.3)

        print(f"[{brand}]  hits={brand_hits}, misses={brand_misses}")
        overall_hits += brand_hits
        overall_misses += brand_misses

    print()
    print(f"TOTAL hits:   {overall_hits}")
    print(f"TOTAL misses: {overall_misses}")
    if args.dry_run:
        print("[DRY RUN] No DB writes.")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
