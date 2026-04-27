"""Re-tag existing ingested_pdf rows under the v4-trimmed vocabulary.

The old single-tag classifier wrote one of 10 categories per row. After
migration 0007 those values were wrapped into a one-element TEXT[] (e.g.
'installation-guides' -> {installation-guides}). This script replays each
ingested_pdf row through the new multi-tag classifier and rewrites the
category array.

Run from a host with both DATABASE_URL and ANTHROPIC_API_KEY available
(the Claude Code sandbox can't reach Neon — egress to *.neon.tech and
to TCP/5432 is blocked there).

Usage:
  DATABASE_URL=postgres://... \\
  ANTHROPIC_API_KEY=sk-ant-... \\
  python scripts/retag-existing-documents.py [--dry-run] [--limit N] [--id ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Make `bidiq` importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
import psycopg  # noqa: E402

from bidiq.ingest import (  # noqa: E402
    CLASSIFIER_SYSTEM_PROMPT_V1,
    VALID_CATEGORIES,
    _coerce_categories,
    classify_document,
)

DEFAULT_MODEL = "claude-sonnet-4-20250514"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the classifier and print proposed tags but don't UPDATE.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N rows (useful for spot-checking).",
    )
    p.add_argument(
        "--id",
        type=int,
        default=None,
        help="Re-tag a single knowledge_items row by id.",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default {DEFAULT_MODEL}).",
    )
    return p.parse_args()


def fetch_rows(conn: psycopg.Connection, limit: int | None, only_id: int | None):
    with conn.cursor() as cur:
        if only_id is not None:
            cur.execute(
                """
                SELECT id, title, summary, raw_content, search_text, category
                  FROM knowledge_items
                 WHERE id = %s
                """,
                (only_id,),
            )
        elif limit is not None:
            cur.execute(
                """
                SELECT id, title, summary, raw_content, search_text, category
                  FROM knowledge_items
                 WHERE source_type = 'ingested_pdf'
                 ORDER BY id
                 LIMIT %s
                """,
                (limit,),
            )
        else:
            cur.execute(
                """
                SELECT id, title, summary, raw_content, search_text, category
                  FROM knowledge_items
                 WHERE source_type = 'ingested_pdf'
                 ORDER BY id
                """
            )
        return cur.fetchall()


def main() -> int:
    args = parse_args()
    db_url = os.environ.get("DATABASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    missing = []
    if not db_url:
        missing.append("DATABASE_URL")
    if not api_key and not args.dry_run:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"Error: missing env var(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    print(f"Vocabulary: v4-trimmed ({len(VALID_CATEGORIES)} valid tags)")
    print(f"Classifier prompt chars: {len(CLASSIFIER_SYSTEM_PROMPT_V1)}")
    print(f"Model: {args.model}")
    print(f"Dry-run: {args.dry_run}")

    client = anthropic.Anthropic(api_key=api_key) if api_key else None

    failures: list[tuple[int, str]] = []
    counters = {"ok": 0, "skip": 0, "fail": 0}

    with psycopg.connect(db_url) as conn:
        rows = fetch_rows(conn, args.limit, args.id)
        print(f"Rows to consider: {len(rows)}")

        for i, (doc_id, title, summary, raw_content, search_text, current) in enumerate(rows, 1):
            body_text = raw_content or search_text or ""
            doc_title = title or ""
            doc_summary = summary or ""

            if not (doc_title or doc_summary or body_text):
                print(f"  [{i}/{len(rows)}] id={doc_id}: SKIP (empty)")
                counters["skip"] += 1
                continue

            if args.dry_run:
                print(f"  [{i}/{len(rows)}] id={doc_id}: would classify; current={current}")
                counters["ok"] += 1
                continue

            assert client is not None
            try:
                tags = classify_document(
                    client,
                    title=doc_title,
                    summary=doc_summary,
                    body_text=body_text,
                    model=args.model,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}/{len(rows)}] id={doc_id}: FAIL {type(e).__name__}: {e}")
                failures.append((doc_id, str(e)))
                counters["fail"] += 1
                # backoff between failures so a transient outage doesn't burn the run
                time.sleep(2)
                continue

            tags = _coerce_categories(tags)

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE knowledge_items SET category = %s WHERE id = %s",
                        (tags, doc_id),
                    )
                conn.commit()
                print(f"  [{i}/{len(rows)}] id={doc_id}: {tags}")
                counters["ok"] += 1
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                print(f"  [{i}/{len(rows)}] id={doc_id}: DB FAIL {type(e).__name__}: {e}")
                failures.append((doc_id, str(e)))
                counters["fail"] += 1

    print(
        f"Done. ok={counters['ok']} skipped={counters['skip']} failed={counters['fail']}"
    )
    if failures:
        print("Failures:")
        for fid, msg in failures[:50]:
            print(f"  id={fid}: {msg}")
        if len(failures) > 50:
            print(f"  ... +{len(failures) - 50} more")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
