"""General-purpose PDF ingester for the Liftnow / Bid IQ knowledge base.

Walks data/product_data/<brand>/ recursively, classifies and extracts each
PDF via Claude vision, and writes results into the knowledge_items Postgres
table. See bidiq/INGEST.md for usage.

This is the general sibling to bidiq/enrich.py (which remains as the
historical Mohawk-only extractor). Do not extend enrich.py.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import anthropic
import click
import psycopg
from pdf2image import convert_from_path
from PIL import Image

Image.MAX_IMAGE_PIXELS = 500_000_000

EXTRACTOR_VERSION = "ingest.py-v1"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
PRODUCT_DATA_DIRNAME = "data/product_data"
ERROR_LOG_PATH = "data/extraction-errors.log"

CATEGORIES = [
    "product-specifications",
    "competitive-intelligence",
    "pricing-data",
    "bid-history",
    "installation-guides",
    "manufacturer-info",
    "service-procedures",
    "compliance-certifications",
    "customer-intelligence",
    "general",
]


def repo_root() -> Path:
    """Return the bid-iq repo root (assumes script lives at <root>/bidiq/)."""
    return Path(__file__).resolve().parent.parent


def relative_source_path(pdf_path: Path) -> str:
    """Repo-root-relative POSIX path used as the natural key in knowledge_items."""
    return pdf_path.resolve().relative_to(repo_root()).as_posix()


def build_extraction_prompt(brand_name: str) -> str:
    return f"""You are analyzing a PDF from Liftnow's knowledge base. The PDF is from manufacturer/brand: {brand_name}.

First, classify this document into exactly ONE of these 10 categories:

- product-specifications: catalogs, brochures, spec sheets — describes what products exist and their technical specs
- competitive-intelligence: content about competitor products or market positioning (rare from vendor PDFs; more common in internal notes)
- pricing-data: price books, dealer discount sheets, promotional pricing flyers
- bid-history: past bid documents, awards, pricing submissions (rare from vendor PDFs)
- installation-guides: install manuals, anchor/pit/slab specs, drawings
- manufacturer-info: about-the-company content, corporate brochures, dealer program info
- service-procedures: service manuals, PM procedures, troubleshooting, parts diagrams
- compliance-certifications: ALI cert records, Buy America letters, warranty documents, ANSI compliance matrices
- customer-intelligence: notes about specific customers (rare from vendor PDFs)
- general: truly doesn't fit any of the above, or too mixed to classify

Then extract:
- A short descriptive title (what is this document?)
- A 2-3 sentence summary of the document's purpose and content
- An effective date in ISO format if visible, else null
- Whether the document explicitly indicates it supersedes a previous version (boolean)
- Up to 15 tags capturing: product categories mentioned, model numbers, certifications mentioned, capacity ratings, any other filter-worthy attributes. Always include the brand name as one tag.

Then extract the document body:
- All substantive text from the document, page by page
- Every model number, specification, price, part number, dimension visible
- Retain structure (tables, lists) as markdown where feasible
- Do NOT summarize — capture completeness, we'll summarize separately

Return as JSON:
{{
  "category": "<one of the 10>",
  "title": "<string>",
  "summary": "<string>",
  "effective_date": "<ISO date or null>",
  "supersedes_previous": <boolean>,
  "tags": ["<string>", ...],
  "content_markdown": "<full document body as markdown>",
  "pages_summary": [
    {{"page": 1, "description": "<what this page shows>"}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Filesystem walking
# ---------------------------------------------------------------------------

def discover_pdfs(brand_filter: str | None) -> list[Path]:
    """Return all PDFs under data/product_data/, optionally restricted to a brand."""
    root = repo_root() / PRODUCT_DATA_DIRNAME
    if not root.is_dir():
        raise click.ClickException(f"Missing directory: {root}")

    if brand_filter:
        scan_roots = [root / brand_filter]
        if not scan_roots[0].is_dir():
            raise click.ClickException(
                f"Brand folder not found: {scan_roots[0]} "
                f"(brand names are case-sensitive folder names)"
            )
    else:
        scan_roots = [p for p in sorted(root.iterdir()) if p.is_dir()]

    pdfs: list[Path] = []
    skipped_non_pdf = 0
    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() != ".pdf":
                skipped_non_pdf += 1
                continue
            pdfs.append(path)

    if skipped_non_pdf:
        click.echo(f"  (skipped {skipped_non_pdf} non-PDF files)")
    return sorted(pdfs)


def brand_for_pdf(pdf_path: Path) -> str:
    """Extract brand name from path: data/product_data/<brand>/..."""
    rel = pdf_path.resolve().relative_to(repo_root() / PRODUCT_DATA_DIRNAME)
    return rel.parts[0]


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------

def db_connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise click.ClickException(
            "DATABASE_URL is not set. Export your Neon/Postgres connection string."
        )
    return psycopg.connect(url)


def ensure_brands(conn: psycopg.Connection, brand_names: Iterable[str]) -> dict[str, int]:
    """Insert any unseen brands with safe defaults; return name->id map."""
    seen: dict[str, int] = {}
    with conn.cursor() as cur:
        for name in sorted(set(brand_names)):
            cur.execute("SELECT id FROM brands WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                seen[name] = row[0]
                continue
            cur.execute(
                """
                INSERT INTO brands (name, manufacturer_name, we_carry, relationship_type, notes)
                VALUES (%s, NULL, FALSE, 'unknown', %s)
                RETURNING id
                """,
                (name, "Auto-created by ingest.py"),
            )
            new_row = cur.fetchone()
            assert new_row is not None
            seen[name] = new_row[0]
            click.echo(f"  + new brand registered: {name} (id={new_row[0]})")
    conn.commit()
    return seen


def existing_extracted_at(conn: psycopg.Connection, source_path: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT extracted_at FROM knowledge_items WHERE source_path = %s",
            (source_path,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0]


def upsert_knowledge_item(
    conn: psycopg.Connection,
    *,
    title: str,
    category: str,
    tags: list[str],
    source_filename: str,
    source_path: str,
    source_pages_count: int,
    summary: str,
    raw_content: str,
    extracted_data: dict[str, Any],
    brand_id: int,
) -> int:
    """Insert or update a knowledge_items row keyed by source_path."""
    search_text = " ".join(
        part
        for part in (title, summary, " ".join(tags or []), raw_content[:5000])
        if part
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM knowledge_items WHERE source_path = %s",
            (source_path,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """
                UPDATE knowledge_items
                SET title = %s,
                    category = %s,
                    tags = %s,
                    content_type = 'pdf',
                    source = 'ingested',
                    source_filename = %s,
                    source_pages_count = %s,
                    summary = %s,
                    raw_content = %s,
                    extracted_data = %s,
                    search_text = %s,
                    brand_id = %s,
                    extracted_at = NOW(),
                    extractor_version = %s
                WHERE id = %s
                RETURNING id
                """,
                (
                    title,
                    category,
                    tags,
                    source_filename,
                    source_pages_count,
                    summary,
                    raw_content,
                    json.dumps(extracted_data),
                    search_text,
                    brand_id,
                    EXTRACTOR_VERSION,
                    row[0],
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO knowledge_items (
                    title, category, subcategory, tags, content_type, source,
                    source_filename, source_path, source_pages_count,
                    summary, raw_content, extracted_data, search_text,
                    brand_id, extracted_at, extractor_version
                ) VALUES (
                    %s, %s, NULL, %s, 'pdf', 'ingested',
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, NOW(), %s
                )
                RETURNING id
                """,
                (
                    title,
                    category,
                    tags,
                    source_filename,
                    source_path,
                    source_pages_count,
                    summary,
                    raw_content,
                    json.dumps(extracted_data),
                    search_text,
                    brand_id,
                    EXTRACTOR_VERSION,
                ),
            )
        result = cur.fetchone()
        assert result is not None
        new_id = result[0]
    conn.commit()
    return new_id


# ---------------------------------------------------------------------------
# PDF rasterization + Claude vision extraction (mirrors enrich.py)
# ---------------------------------------------------------------------------

def render_pdf_pages(pdf_path: Path, dpi: int) -> list[tuple[int, str]]:
    """Rasterize PDF -> list of (page_num, base64_jpeg) tuples within API limits."""
    pages = convert_from_path(str(pdf_path), dpi=dpi, fmt="jpeg")
    encoded: list[tuple[int, str]] = []
    max_bytes = 4_500_000
    max_dim = 7900
    for i, page in enumerate(pages):
        w, h = page.size
        if w > max_dim or h > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            page = page.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        page.save(buf, format="JPEG", quality=80)
        if buf.tell() > max_bytes:
            scale = 0.5
            w, h = page.size
            while buf.tell() > max_bytes and scale > 0.1:
                resized = page.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=70)
                scale *= 0.75
        encoded.append((i + 1, base64.standard_b64encode(buf.getvalue()).decode("ascii")))
    return encoded


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def extract_with_vision(
    client: anthropic.Anthropic,
    pdf_name: str,
    brand_name: str,
    pages: list[tuple[int, str]],
    model: str,
) -> dict[str, Any]:
    """Send page images to Claude vision; merge batches into a single result dict."""
    prompt = build_extraction_prompt(brand_name)
    batch_size = 20
    all_results: list[dict[str, Any]] = []

    for batch_start in range(0, len(pages), batch_size):
        batch = pages[batch_start : batch_start + batch_size]
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Analyzing PDF: {pdf_name} (pages {batch[0][0]}-{batch[-1][0]} "
                    f"of {len(pages)} total). Brand: {brand_name}."
                ),
            }
        ]
        for page_num, b64_data in batch:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64_data,
                    },
                }
            )
            content.append({"type": "text", "text": f"(Page {page_num})"})
        content.append({"type": "text", "text": prompt})

        max_retries = 5
        response = None
        for attempt in range(max_retries):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    messages=[{"role": "user", "content": content}],
                )
                break
            except anthropic.RateLimitError:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** (attempt + 1) * 15
                click.echo(f"    Rate limited on {pdf_name}, waiting {wait}s...")
                time.sleep(wait)
        assert response is not None

        text_block = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            "",
        )
        parsed = _parse_json_payload(text_block) or {"raw_response": text_block}
        all_results.append(parsed)

    if not all_results:
        return {}
    if len(all_results) == 1:
        return all_results[0]

    merged = dict(all_results[0])
    for extra in all_results[1:]:
        if "pages_summary" in extra:
            merged.setdefault("pages_summary", []).extend(extra.get("pages_summary") or [])
        if "tags" in extra:
            existing_tags = list(merged.get("tags") or [])
            for t in extra.get("tags") or []:
                if t not in existing_tags:
                    existing_tags.append(t)
            merged["tags"] = existing_tags
        if "content_markdown" in extra:
            merged["content_markdown"] = (
                (merged.get("content_markdown") or "")
                + "\n\n"
                + extra["content_markdown"]
            )
    return merged


# ---------------------------------------------------------------------------
# Per-PDF orchestration
# ---------------------------------------------------------------------------

def normalize_extraction(raw: dict[str, Any], brand_name: str, pdf_name: str) -> dict[str, Any]:
    """Coerce vision output into the fields we'll insert; supply safe fallbacks."""
    category = raw.get("category")
    if category not in CATEGORIES:
        category = "general"

    title = raw.get("title") or pdf_name
    summary = raw.get("summary") or ""

    tags_raw = raw.get("tags") or []
    if not isinstance(tags_raw, list):
        tags_raw = [str(tags_raw)]
    tags: list[str] = []
    for t in tags_raw:
        if t is None:
            continue
        s = str(t).strip()
        if s and s not in tags:
            tags.append(s)
    if brand_name not in tags:
        tags.append(brand_name)
    tags = tags[:20]

    content_markdown = raw.get("content_markdown")
    if not isinstance(content_markdown, str) or not content_markdown.strip():
        content_markdown = raw.get("raw_response") or ""

    return {
        "category": category,
        "title": str(title)[:500],
        "summary": str(summary),
        "tags": tags,
        "content_markdown": content_markdown,
        "extracted_data": raw,
    }


def log_error(pdf_path: Path, err: BaseException) -> None:
    log_path = repo_root() / ERROR_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{datetime.now(timezone.utc).isoformat()}\t"
        f"{pdf_path}\t{type(err).__name__}\t{err}\n"
    )
    with open(log_path, "a") as f:
        f.write(line)


def process_one_pdf(
    *,
    pdf_path: Path,
    brand_id: int,
    brand_name: str,
    client: anthropic.Anthropic,
    conn: psycopg.Connection,
    model: str,
    dpi: int,
) -> str:
    rel = relative_source_path(pdf_path)
    try:
        pages = render_pdf_pages(pdf_path, dpi=dpi)
    except Exception as e:
        log_error(pdf_path, e)
        return f"  FAIL  {rel} - render error: {e}"

    try:
        raw = extract_with_vision(
            client=client,
            pdf_name=pdf_path.name,
            brand_name=brand_name,
            pages=pages,
            model=model,
        )
    except Exception as e:
        log_error(pdf_path, e)
        return f"  FAIL  {rel} - extraction error: {e}"

    try:
        normalized = normalize_extraction(raw, brand_name=brand_name, pdf_name=pdf_path.name)
        new_id = upsert_knowledge_item(
            conn,
            title=normalized["title"],
            category=normalized["category"],
            tags=normalized["tags"],
            source_filename=pdf_path.name,
            source_path=rel,
            source_pages_count=len(pages),
            summary=normalized["summary"],
            raw_content=normalized["content_markdown"],
            extracted_data=normalized["extracted_data"],
            brand_id=brand_id,
        )
    except Exception as e:
        log_error(pdf_path, e)
        return f"  FAIL  {rel} - db error: {e}"

    return f"  OK    {rel} ({len(pages)} pages, category={normalized['category']}, id={new_id})"


# ---------------------------------------------------------------------------
# Run planning (decides which PDFs to (re)process)
# ---------------------------------------------------------------------------

def plan_run(
    conn: psycopg.Connection | None,
    pdfs: list[Path],
    limit: int | None,
) -> tuple[list[Path], list[Path]]:
    """Split discovered PDFs into (to_process, to_skip) honoring --limit."""
    to_process: list[Path] = []
    to_skip: list[Path] = []

    for pdf in pdfs:
        rel = relative_source_path(pdf)
        already: datetime | None = None
        if conn is not None:
            already = existing_extracted_at(conn, rel)
        if already is not None:
            try:
                pdf_mtime = datetime.fromtimestamp(pdf.stat().st_mtime, tz=timezone.utc)
            except OSError:
                pdf_mtime = None
            if pdf_mtime and already >= pdf_mtime:
                to_skip.append(pdf)
                continue
        to_process.append(pdf)
        if limit is not None and len(to_process) >= limit:
            break

    return to_process, to_skip


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

@click.command("ingest")
@click.option("--brand", default=None, help="Restrict to data/product_data/<brand>/ only.")
@click.option("--limit", default=None, type=int, help="Process only the first N unprocessed PDFs.")
@click.option("--dry-run", is_flag=True, help="List planned work; no API or DB writes.")
@click.option("--concurrency", default=3, type=int, help="Parallel worker count.")
@click.option("--dpi", default=150, type=int, help="DPI for pdf2image rasterization.")
@click.option("--model", default=DEFAULT_MODEL, help="Claude model for vision extraction.")
def ingest_cmd(
    brand: str | None,
    limit: int | None,
    dry_run: bool,
    concurrency: int,
    dpi: int,
    model: str,
) -> None:
    """Ingest PDFs from data/product_data/ into the knowledge_items table."""
    if not dry_run:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise click.ClickException("ANTHROPIC_API_KEY is not set.")
        if not os.environ.get("DATABASE_URL"):
            raise click.ClickException("DATABASE_URL is not set.")

    pdfs = discover_pdfs(brand)
    click.echo(f"Discovered {len(pdfs)} PDF(s) under {PRODUCT_DATA_DIRNAME}"
               + (f"/{brand}" if brand else ""))
    if not pdfs:
        return

    brand_names = sorted({brand_for_pdf(p) for p in pdfs})
    click.echo(f"Brands present: {', '.join(brand_names)}")

    conn: psycopg.Connection | None = None
    brand_ids: dict[str, int] = {}
    if not dry_run:
        conn = db_connect()
        brand_ids = ensure_brands(conn, brand_names)

    to_process, to_skip = plan_run(conn, pdfs, limit)
    click.echo(f"Planned: {len(to_process)} to process, {len(to_skip)} up-to-date skips")

    if dry_run:
        click.echo("--- DRY RUN: planned work ---")
        for p in to_process:
            click.echo(f"  PROCESS  {relative_source_path(p)}  brand={brand_for_pdf(p)}")
        for p in to_skip[:25]:
            click.echo(f"  SKIP     {relative_source_path(p)}  (up-to-date)")
        if len(to_skip) > 25:
            click.echo(f"  ... +{len(to_skip) - 25} more skips")
        click.echo("--- end dry run ---")
        return

    assert conn is not None
    api_key = os.environ["ANTHROPIC_API_KEY"]
    client = anthropic.Anthropic(api_key=api_key)

    click.echo(f"Model: {model}  DPI: {dpi}  Concurrency: {concurrency}")
    click.echo("-" * 60)

    # Each worker thread gets its own DB connection — psycopg connections are
    # not thread-safe across concurrent statements.
    db_url = os.environ["DATABASE_URL"]

    def worker(pdf: Path) -> str:
        brand_name = brand_for_pdf(pdf)
        brand_id = brand_ids[brand_name]
        try:
            with psycopg.connect(db_url) as worker_conn:
                return process_one_pdf(
                    pdf_path=pdf,
                    brand_id=brand_id,
                    brand_name=brand_name,
                    client=client,
                    conn=worker_conn,
                    model=model,
                    dpi=dpi,
                )
        except Exception as e:
            log_error(pdf, e)
            tb = traceback.format_exc().splitlines()[-1]
            return f"  FAIL  {relative_source_path(pdf)} - {tb}"

    ok = skip = fail = 0
    if concurrency <= 1:
        results = (worker(p) for p in to_process)
    else:
        pool = ThreadPoolExecutor(max_workers=concurrency)
        futures = {pool.submit(worker, p): p for p in to_process}
        results = (f.result() for f in as_completed(futures))

    for line in results:
        click.echo(line)
        if line.lstrip().startswith("OK"):
            ok += 1
        elif line.lstrip().startswith("SKIP"):
            skip += 1
        elif line.lstrip().startswith("FAIL"):
            fail += 1

    click.echo("-" * 60)
    click.echo(f"Done: {ok} processed, {skip} skipped, {fail} failed (errors -> {ERROR_LOG_PATH})")
    conn.close()


def main() -> None:
    """Allow `python -m bidiq.ingest` invocation."""
    ingest_cmd()


if __name__ == "__main__":
    main()
