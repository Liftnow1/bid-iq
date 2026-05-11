#!/usr/bin/env python3
"""
Upload non-Rotary PDFs to Cloudflare R2 and populate
knowledge_items.external_url with the resulting public URL.

The /api/documents/[id]/pdf route already 302-redirects to external_url
when it's populated. Today (post-iter19) external_url is set for the
1,444 Rotary/Forward KB items via Rotary's public S3 hosting. The other
~3,800 KB items — including all 649 product_documents from BendPak,
Mohawk, Challenger, Hunter, PKS, etc. — still have external_url=NULL
and return 503 from the route. This script fixes that.

For each KB item whose external_url is NULL and source_path resolves to
a real local PDF (not an LFS pointer), it uploads to R2 under a stable
key (kb/<ki_id>/<sanitized-filename>.pdf), constructs the public URL,
and writes it back to the DB. Idempotent: re-running skips KB items that
already have external_url set.

Required env (in .env or .env.local):
  CLOUDFLARE_R2_ACCOUNT_ID         the account UUID from R2 dashboard
  CLOUDFLARE_R2_ACCESS_KEY_ID      from "R2 → Manage R2 API Tokens"
  CLOUDFLARE_R2_SECRET_ACCESS_KEY
  CLOUDFLARE_R2_BUCKET             bucket name, e.g. "bid-iq-docs"
  CLOUDFLARE_R2_PUBLIC_BASE_URL    the public URL prefix, e.g.
                                   "https://pub-<hash>.r2.dev"  or
                                   "https://files.bid-iq.com"
                                   (NO trailing slash)

Usage:
  python scripts/upload-pdfs-to-r2.py --dry-run             # preview
  python scripts/upload-pdfs-to-r2.py --limit 5             # smoke test
  python scripts/upload-pdfs-to-r2.py                       # full upload
  python scripts/upload-pdfs-to-r2.py --brand bendpak       # one brand at a time
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

try:
    import boto3  # type: ignore
    from botocore.client import Config  # type: ignore
    from botocore.exceptions import ClientError  # type: ignore
except ImportError:
    print("ERROR: boto3 not installed. Run: pip install boto3", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"


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
            out[k.strip()] = v.strip().strip('"').strip("'")
    # env overrides file
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


def sanitize_for_object_key(name: str) -> str:
    """Make a filename safe for an R2 object key.

    R2 allows most characters but URL-encoding spaces and unicode at access
    time is annoying. We keep [A-Za-z0-9._-], replace everything else with
    underscore, and collapse runs of underscores.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "file.pdf"


def make_object_key(ki_id: int, source_path: str) -> str:
    """kb/<ki_id>/<sanitized-basename>"""
    base = Path(source_path).name
    return f"kb/{ki_id}/{sanitize_for_object_key(base)}"


def is_real_pdf(p: Path) -> bool:
    """Distinguish a real PDF from a Git-LFS pointer file."""
    try:
        with open(p, "rb") as f:
            head = f.read(128)
    except OSError:
        return False
    if not head:
        return False
    # Real PDFs start with %PDF (or sometimes have a leading BOM). LFS
    # pointers always start with "version https://git-lfs..."
    if head.startswith(LFS_POINTER_PREFIX):
        return False
    # Strict: require %PDF anywhere in the first 1KB
    try:
        with open(p, "rb") as f:
            return b"%PDF" in f.read(1024)
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload bid-iq PDFs to Cloudflare R2")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan only — don't upload or touch DB")
    ap.add_argument("--brand", default=None,
                    help="Only upload docs from this brand (lowercase)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap files per run (smoke test)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-file details")
    args = ap.parse_args()

    env = load_env()

    db_url = env.get("DATABASE_URL") or env.get("POSTGRES_URL")
    if not db_url:
        print("ERROR: DATABASE_URL missing", file=sys.stderr)
        return 2

    if not args.dry_run:
        required = [
            "CLOUDFLARE_R2_ACCOUNT_ID",
            "CLOUDFLARE_R2_ACCESS_KEY_ID",
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
            "CLOUDFLARE_R2_BUCKET",
            "CLOUDFLARE_R2_PUBLIC_BASE_URL",
        ]
        missing = [k for k in required if not env.get(k)]
        if missing:
            print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
            print("See scripts/upload-pdfs-to-r2.py docstring for what each is.",
                  file=sys.stderr)
            return 2

    account_id = env.get("CLOUDFLARE_R2_ACCOUNT_ID", "")
    bucket = env.get("CLOUDFLARE_R2_BUCKET", "")
    public_base = (env.get("CLOUDFLARE_R2_PUBLIC_BASE_URL") or "").rstrip("/")

    # --- DB: find candidates ---
    conn = psycopg.connect(db_url, autocommit=False)
    cur = conn.cursor(row_factory=dict_row)

    where_brand = ""
    params: list = []
    if args.brand:
        where_brand = "AND lower(b.name) = %s"
        params.append(args.brand.lower())

    cur.execute(f"""
        SELECT DISTINCT ki.id AS ki_id, b.name AS brand,
               ki.source_filename, ki.source_path
        FROM knowledge_items ki
        JOIN brands b ON b.id = ki.brand_id
        JOIN product_documents pd ON pd.knowledge_item_id = ki.id
        WHERE ki.external_url IS NULL
          AND ki.source_path IS NOT NULL
          {where_brand}
        ORDER BY b.name, ki.id
    """, params)
    candidates = cur.fetchall()
    print(f"KB items needing upload: {len(candidates)}")

    # Filter to ones with a real PDF on disk
    plan: list[dict] = []
    skipped_missing = 0
    skipped_lfs = 0
    total_bytes = 0
    for c in candidates:
        local = REPO_ROOT / c["source_path"]
        if not local.exists():
            skipped_missing += 1
            continue
        if not is_real_pdf(local):
            skipped_lfs += 1
            continue
        size = local.stat().st_size
        total_bytes += size
        plan.append({
            "ki_id": c["ki_id"],
            "brand": c["brand"],
            "filename": c["source_filename"] or local.name,
            "local_path": local,
            "object_key": make_object_key(c["ki_id"], str(local)),
            "size": size,
        })

    if args.limit:
        plan = plan[: args.limit]

    print(f"  ready to upload:    {len(plan)}  ({total_bytes/1024/1024:.0f} MB total)")
    print(f"  skipped (missing):  {skipped_missing}")
    print(f"  skipped (LFS only): {skipped_lfs}")

    if args.dry_run:
        from collections import Counter
        bc = Counter(p["brand"] for p in plan)
        print()
        print("Plan by brand:")
        for b, n in sorted(bc.items()):
            print(f"  {b:<14} {n:>4}")
        print()
        print("Sample object keys (first 5):")
        for p in plan[:5]:
            url = f"{public_base}/{p['object_key']}" if public_base else f"<bucket>/{p['object_key']}"
            print(f"  {url}")
            print(f"    from: {p['local_path']}")
        print()
        print("[DRY RUN] No uploads, no DB writes.")
        return 0

    if not plan:
        print("Nothing to upload. Done.")
        return 0

    # --- R2 client ---
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=env["CLOUDFLARE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            region_name="auto",
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )

    # --- upload + DB write loop ---
    uploaded = 0
    skipped_already = 0
    failed = 0
    bytes_sent = 0
    started = time.time()
    for i, p in enumerate(plan, start=1):
        key = p["object_key"]

        # Idempotent check: HEAD the object first. If it's already there
        # AND its size matches, skip the upload but still write the URL
        # to the DB (in case a previous run uploaded but failed the DB
        # update).
        already_there = False
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            if int(head.get("ContentLength", 0)) == p["size"]:
                already_there = True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchKey", "NotFound"):
                # Some other error — retry by uploading
                if args.verbose:
                    print(f"  [{i}/{len(plan)}] head_object error {code}, will try upload")

        if not already_there:
            try:
                s3.upload_file(
                    Filename=str(p["local_path"]),
                    Bucket=bucket,
                    Key=key,
                    ExtraArgs={"ContentType": "application/pdf"},
                )
                uploaded += 1
                bytes_sent += p["size"]
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(plan)}] FAIL {p['filename']}: {type(e).__name__}: {e}",
                      file=sys.stderr)
                continue
        else:
            skipped_already += 1

        public_url = f"{public_base}/{key}"
        cur.execute(
            "UPDATE knowledge_items SET external_url = %s WHERE id = %s",
            (public_url, p["ki_id"]),
        )
        conn.commit()

        if args.verbose or (i % 25 == 0):
            elapsed = time.time() - started
            rate = bytes_sent / max(1.0, elapsed) / 1024 / 1024
            size_tag = "(already-there)" if already_there else f"{p['size']/1024/1024:>5.1f}MB"
            print(
                f"  [{i:>4}/{len(plan)}] {p['brand']:<12} {size_tag} "
                f"-> {key}  ({rate:.1f} MB/s avg)"
            )

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  uploaded:           {uploaded}")
    print(f"  skipped (already):  {skipped_already}")
    print(f"  failed:             {failed}")
    print(f"  bytes sent:         {bytes_sent/1024/1024:.0f} MB")

    cur.close()
    conn.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
