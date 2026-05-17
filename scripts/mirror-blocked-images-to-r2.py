#!/usr/bin/env python3
"""
Mirror product images that are blocked by Cross-Origin-Resource-Policy
(or other cross-origin embed restrictions) to our Cloudflare R2 bucket
so they render from any consuming app.

Detection:
  HEAD each products.image_url that isn't already on our R2 bucket.
  If the response carries Cross-Origin-Resource-Policy: same-origin
  (or =same-site, in some cases), the image cannot be embedded by
  the portal even though it returns 200 from curl. Mark it for mirror.

Mirroring:
  Stream the image bytes to a new R2 key:
    images/products/<product_id>/<sanitized-original-basename>
  Public URL becomes:
    {CLOUDFLARE_R2_PUBLIC_BASE_URL}/images/products/<pid>/<basename>
  Then UPDATE products SET image_url = <r2 url> WHERE id = <pid>.
  image_source_url is preserved so we keep the audit trail to the
  original page.

Idempotent: skips rows whose image_url is already on our R2 host
unless --force.

Requires the same five CLOUDFLARE_R2_* env vars used by
scripts/upload-pdfs-to-r2.py.

Run:
    python scripts/mirror-blocked-images-to-r2.py --dry-run
    python scripts/mirror-blocked-images-to-r2.py
"""

from __future__ import annotations
import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

try:
    import boto3  # type: ignore
    from botocore.client import Config  # type: ignore
except ImportError:
    print("ERROR: boto3 not installed. Run: pip install boto3", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Content-Type sniff -> file extension, used when the URL has no extension.
CT_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
}


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for envfile in (".env.local", ".env"):
        ep = REPO_ROOT / envfile
        if not ep.exists():
            continue
        for line in ep.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in (
        "DATABASE_URL", "POSTGRES_URL",
        "CLOUDFLARE_R2_ACCOUNT_ID",
        "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_R2_BUCKET",
        "CLOUDFLARE_R2_PUBLIC_BASE_URL",
    ):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def sanitize_basename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "image"


def head_url(url: str) -> tuple[int, dict[str, str]]:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "*/*"}, method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        # Some servers don't allow HEAD — try a Range GET 0-0 instead
        try:
            req2 = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-0"},
            )
            with urllib.request.urlopen(req2, timeout=10) as r2:
                return r2.status, {k.lower(): v for k, v in r2.headers.items()}
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def is_cross_origin_blocked(headers: dict[str, str]) -> bool:
    """True if the image's response headers indicate a browser will
    refuse to embed it cross-origin."""
    corp = headers.get("cross-origin-resource-policy", "").strip().lower()
    if corp in ("same-origin", "same-site"):
        return True
    # If the server insists on a strict referer (`X-Frame-Options: DENY`
    # affects iframes, not <img>, so we don't check that). Some hotlink
    # protection schemes return 403 on no-referer GET — we'd catch those
    # later when we try to download. For now just CORP.
    return False


def fetch_bytes(url: str, max_bytes: int = 20_000_000) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = r.headers.get("Content-Type", "")
            return r.status, ct, r.read(max_bytes)
    except urllib.error.HTTPError as e:
        return e.code, "", b""
    except Exception:
        return 0, "", b""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--brand", help="restrict to one brand (lowercase)")
    ap.add_argument("--force", action="store_true",
                    help="re-mirror even if image_url already on our R2 host")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    env = load_env()
    db_url = env.get("DATABASE_URL") or env.get("POSTGRES_URL")
    if not db_url:
        print("ERROR: DATABASE_URL missing", file=sys.stderr)
        return 2

    public_base = (env.get("CLOUDFLARE_R2_PUBLIC_BASE_URL") or "").rstrip("/")
    if not args.dry_run:
        for k in (
            "CLOUDFLARE_R2_ACCOUNT_ID",
            "CLOUDFLARE_R2_ACCESS_KEY_ID",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
            "CLOUDFLARE_R2_BUCKET",
            "CLOUDFLARE_R2_PUBLIC_BASE_URL",
        ):
            if not env.get(k):
                print(f"ERROR: {k} missing", file=sys.stderr)
                return 2

    conn = psycopg.connect(db_url, autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    where_brand = "AND lower(b.name) = %s" if args.brand else ""
    params = [args.brand.lower()] if args.brand else []
    not_on_r2 = ""
    if not args.force and public_base:
        not_on_r2 = f"AND p.image_url NOT LIKE %s"
        params.append(f"{public_base}/%")
    cur.execute(f"""
        SELECT p.id, p.sku, lower(b.name) AS brand, p.image_url, p.image_source_url
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.image_url IS NOT NULL
          {not_on_r2}
          {where_brand}
        ORDER BY b.name, p.sku
    """, params)
    rows = cur.fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"Candidates: {len(rows)}")

    blocked: list[dict] = []
    open_ok = 0
    for r in rows:
        status, hdrs = head_url(r["image_url"])
        if status not in (200, 206):
            if args.verbose:
                print(f"  HEAD-FAIL {status}  {r['image_url']}")
            continue
        if is_cross_origin_blocked(hdrs):
            blocked.append(r)
            if args.verbose:
                print(f"  CORP-BLOCKED  pid={r['id']}  {r['image_url']}")
        else:
            open_ok += 1

    print(f"  CORP-blocked: {len(blocked)}")
    print(f"  open (no mirror needed): {open_ok}")

    if args.dry_run:
        for r in blocked[:20]:
            print(f"    would mirror pid={r['id']}  {r['brand']:<14} {r['sku']:<20}")
            print(f"      from: {r['image_url']}")
        print("[DRY RUN] No uploads.")
        cur.close(); conn.close()
        return 0

    if not blocked:
        print("Nothing to mirror.")
        cur.close(); conn.close()
        return 0

    # R2 client
    endpoint = f"https://{env['CLOUDFLARE_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=env["CLOUDFLARE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", region_name="auto",
                      retries={"max_attempts": 4, "mode": "standard"}),
    )
    bucket = env["CLOUDFLARE_R2_BUCKET"]

    mirrored = 0
    failed = 0
    for i, r in enumerate(blocked, 1):
        url = r["image_url"]
        status, ct, data = fetch_bytes(url)
        if status not in (200, 206) or not data:
            print(f"  [{i}/{len(blocked)}] FETCH-FAIL  pid={r['id']}  status={status}  {url}")
            failed += 1
            continue

        # Pick a sensible basename
        path = urllib.parse.urlparse(url).path
        basename = path.rsplit("/", 1)[-1] or f"img-{r['id']}"
        if "." not in basename:
            ext = CT_TO_EXT.get(ct.split(";", 1)[0].strip().lower(), ".jpg")
            basename = basename + ext
        key = f"images/products/{r['id']}/{sanitize_basename(basename)}"

        try:
            s3.put_object(
                Bucket=bucket, Key=key, Body=data,
                ContentType=ct or "image/jpeg",
                CacheControl="public, max-age=31536000, immutable",
            )
        except Exception as e:
            print(f"  [{i}/{len(blocked)}] R2-FAIL  pid={r['id']}  {type(e).__name__}: {e}")
            failed += 1
            continue

        new_url = f"{public_base}/{key}"
        cur.execute(
            "UPDATE products SET image_url = %s, image_fetched_at = NOW() WHERE id = %s",
            (new_url, r["id"]),
        )
        conn.commit()
        mirrored += 1
        if args.verbose or i % 20 == 0:
            kb = len(data) / 1024
            print(f"  [{i:>3}/{len(blocked)}] {r['brand']:<14} {r['sku']:<22} -> {key}  ({kb:.0f} KB)")
        time.sleep(0.1)

    print()
    print(f"Mirrored: {mirrored}")
    print(f"Failed:   {failed}")
    cur.close(); conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
