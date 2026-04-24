"""General-purpose PDF ingester for Liftnow's knowledge base.

Walks `data/product_data/<brand>/` recursively, classifies each PDF via Claude
vision, extracts structured content, and writes rows directly to the
`knowledge_items` Postgres table.

Scope note: this module is the generalized sibling to `bidiq/enrich.py` (which
remains as a Mohawk-only historical artifact). Use `ingest.py` for new work.
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
from typing import Optional

import anthropic
import click
import psycopg
from pdf2image import convert_from_path
from PIL import Image

Image.MAX_IMAGE_PIXELS = 500_000_000

EXTRACTOR_VERSION = "ingest.py-v1"

VALID_CATEGORIES = {
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
}

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_DATA_ROOT = REPO_ROOT / "data" / "product_data"
ERROR_LOG = REPO_ROOT / "data" / "extraction-errors.log"


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
# File discovery
# ---------------------------------------------------------------------------


def derive_brand(pdf_path: Path) -> str:
    """Brand = path segment directly under data/product_data/."""
    rel = pdf_path.relative_to(PRODUCT_DATA_ROOT)
    return rel.parts[0]


def repo_relative_path(pdf_path: Path) -> str:
    return str(pdf_path.resolve().relative_to(REPO_ROOT))


def discover_pdfs(brand_filter: Optional[str]) -> list[Path]:
    """Walk product_data/ and return all .pdf paths (skips hidden files)."""
    if not PRODUCT_DATA_ROOT.exists():
        return []

    root = PRODUCT_DATA_ROOT
    if brand_filter:
        root = PRODUCT_DATA_ROOT / brand_filter
        if not root.exists():
            click.echo(
                f"Warning: brand folder not found: {root}", err=True
            )
            return []

    results: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() == ".pdf":
            results.append(p)
        else:
            click.echo(f"  SKIP  non-PDF: {repo_relative_path(p)}")
    return sorted(results)


# ---------------------------------------------------------------------------
# Brand registry
# ---------------------------------------------------------------------------


def ensure_brand(conn: psycopg.Connection, brand_name: str) -> int:
    """Look up a brand row by case-insensitive name; insert if missing.

    Returns the brand id. Never updates or deletes existing rows — `we_carry`
    and `relationship_type` are Paul's to manage manually.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM brands WHERE lower(name) = lower(%s) LIMIT 1",
            (brand_name,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            INSERT INTO brands (name, manufacturer_name, we_carry, relationship_type, notes)
            VALUES (%s, NULL, FALSE, 'unknown', 'Auto-created by ingest.py')
            RETURNING id
            """,
            (brand_name,),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


# ---------------------------------------------------------------------------
# Already-processed check
# ---------------------------------------------------------------------------


def find_existing_row(
    conn: psycopg.Connection, source_path: str
) -> Optional[tuple[int, Optional[datetime]]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, extracted_at FROM knowledge_items WHERE source_path = %s LIMIT 1",
            (source_path,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0], row[1]


def is_up_to_date(
    pdf_path: Path, extracted_at: Optional[datetime]
) -> bool:
    if extracted_at is None:
        return False
    mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime, tz=timezone.utc)
    if extracted_at.tzinfo is None:
        extracted_at = extracted_at.replace(tzinfo=timezone.utc)
    return extracted_at > mtime


# ---------------------------------------------------------------------------
# Vision extraction (pattern from enrich.py)
# ---------------------------------------------------------------------------


def render_pdf_pages(pdf_path: str, dpi: int = 150) -> list[tuple[int, str]]:
    pages = convert_from_path(pdf_path, dpi=dpi, fmt="jpeg")
    encoded: list[tuple[int, str]] = []
    max_bytes = 4_500_000
    max_dim = 7900
    for i, page in enumerate(pages):
        w, h = page.size
        if w > max_dim or h > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            page = page.resize(
                (int(w * ratio), int(h * ratio)), Image.LANCZOS
            )
        buf = io.BytesIO()
        page.save(buf, format="JPEG", quality=80)
        if buf.tell() > max_bytes:
            scale = 0.5
            w, h = page.size
            while buf.tell() > max_bytes and scale > 0.1:
                resized = page.resize(
                    (int(w * scale), int(h * scale)), Image.LANCZOS
                )
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=70)
                scale *= 0.75
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        encoded.append((i + 1, b64))
    return encoded


def extract_with_vision(
    client: anthropic.Anthropic,
    pdf_name: str,
    brand_name: str,
    pages: list[tuple[int, str]],
    model: str,
) -> dict:
    batch_size = 20
    all_results: list[dict] = []
    prompt = build_extraction_prompt(brand_name)

    for batch_start in range(0, len(pages), batch_size):
        batch = pages[batch_start : batch_start + batch_size]
        content: list[dict] = [
            {
                "type": "text",
                "text": f"Analyzing PDF: {pdf_name} (pages {batch[0][0]}-{batch[-1][0]} of {len(pages)} total)",
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
                click.echo(
                    f"    Rate limited on {pdf_name}, waiting {wait}s..."
                )
                time.sleep(wait)
        assert response is not None

        response_text = response.content[0].text
        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                all_results.append(json.loads(response_text[start:end]))
            else:
                all_results.append({"raw_response": response_text})
        except json.JSONDecodeError:
            all_results.append({"raw_response": response_text})

    if len(all_results) == 1:
        return all_results[0]

    # Merge multi-batch: concatenate content, dedupe tags, take first title/category.
    merged = dict(all_results[0])
    merged_tags = list(merged.get("tags") or [])
    merged_pages: list = list(merged.get("pages_summary") or [])
    content_parts = [merged.get("content_markdown", "") or ""]
    for extra in all_results[1:]:
        content_parts.append(extra.get("content_markdown", "") or "")
        for t in extra.get("tags") or []:
            if t not in merged_tags:
                merged_tags.append(t)
        merged_pages.extend(extra.get("pages_summary") or [])
    merged["content_markdown"] = "\n\n".join(p for p in content_parts if p)
    merged["tags"] = merged_tags
    merged["pages_summary"] = merged_pages
    return merged


# ---------------------------------------------------------------------------
# Row writing
# ---------------------------------------------------------------------------


def _coerce_category(raw: Optional[str]) -> str:
    if raw and raw in VALID_CATEGORIES:
        return raw
    return "general"


def _coerce_tags(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        if isinstance(t, str) and t.strip():
            out.append(t.strip()[:100])
    return out[:15]


def _build_search_text(
    title: str, summary: str, tags: list[str], content: str
) -> str:
    parts = [title, summary, " ".join(tags), content[:5000]]
    return " ".join(p for p in parts if p)


def upsert_knowledge_item(
    conn: psycopg.Connection,
    *,
    source_path: str,
    source_filename: str,
    source_pages_count: int,
    brand_id: int,
    extraction: dict,
) -> int:
    category = _coerce_category(extraction.get("category"))
    title = (
        str(extraction.get("title") or source_filename).strip()[:500]
        or source_filename
    )
    summary = str(extraction.get("summary") or "").strip()
    tags = _coerce_tags(extraction.get("tags"))
    raw_content = str(extraction.get("content_markdown") or "")
    search_text = _build_search_text(title, summary, tags, raw_content)

    extracted_data = {
        "effective_date": extraction.get("effective_date"),
        "supersedes_previous": bool(extraction.get("supersedes_previous"))
        if extraction.get("supersedes_previous") is not None
        else None,
        "pages_summary": extraction.get("pages_summary") or [],
    }

    existing = find_existing_row(conn, source_path)
    with conn.cursor() as cur:
        if existing:
            row_id = existing[0]
            cur.execute(
                """
                UPDATE knowledge_items SET
                    title = %s,
                    category = %s,
                    subcategory = NULL,
                    tags = %s,
                    content_type = 'pdf',
                    source = 'ingested',
                    source_filename = %s,
                    source_path = %s,
                    source_pages_count = %s,
                    summary = %s,
                    raw_content = %s,
                    extracted_data = %s,
                    search_text = %s,
                    brand_id = %s,
                    extracted_at = NOW(),
                    extractor_version = %s
                WHERE id = %s
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
                    row_id,
                ),
            )
            conn.commit()
            return row_id

        cur.execute(
            """
            INSERT INTO knowledge_items (
                title, category, subcategory, tags, content_type, source,
                source_filename, source_path, source_pages_count,
                raw_content, extracted_data, summary, search_text,
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
                raw_content,
                json.dumps(extracted_data),
                summary,
                search_text,
                brand_id,
                EXTRACTOR_VERSION,
            ),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


# ---------------------------------------------------------------------------
# Error log
# ---------------------------------------------------------------------------


def log_error(pdf_path: Path, err: BaseException) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"{ts}\t{repo_relative_path(pdf_path)}\t"
        f"{type(err).__name__}\t{str(err).splitlines()[0] if str(err) else ''}\n"
    )
    with open(ERROR_LOG, "a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Per-PDF worker
# ---------------------------------------------------------------------------


def _connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)


def process_single_pdf(
    pdf_path: Path,
    database_url: str,
    api_key: str,
    model: str,
    dpi: int,
) -> str:
    source_path = repo_relative_path(pdf_path)
    try:
        brand_name = derive_brand(pdf_path)
    except ValueError:
        return f"  SKIP  {source_path} (not under data/product_data/)"

    conn = None
    try:
        conn = _connect(database_url)

        brand_id = ensure_brand(conn, brand_name)

        existing = find_existing_row(conn, source_path)
        if existing and is_up_to_date(pdf_path, existing[1]):
            return f"  SKIP  {source_path} (already processed)"

        pages = render_pdf_pages(str(pdf_path), dpi=dpi)
        if not pages:
            return f"  FAIL  {source_path} - 0 pages rendered"

        client = anthropic.Anthropic(api_key=api_key)
        extraction = extract_with_vision(
            client, pdf_path.name, brand_name, pages, model
        )

        row_id = upsert_knowledge_item(
            conn,
            source_path=source_path,
            source_filename=pdf_path.name,
            source_pages_count=len(pages),
            brand_id=brand_id,
            extraction=extraction,
        )
        action = "UPDATE" if existing else "INSERT"
        return (
            f"  OK    {source_path} ({len(pages)}p, brand={brand_name}) "
            f"-> knowledge_items.id={row_id} [{action}]"
        )
    except Exception as e:
        log_error(pdf_path, e)
        tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
        return f"  FAIL  {source_path} - {type(e).__name__}: {tb}"
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Dry-run planner
# ---------------------------------------------------------------------------


def plan_pdfs(
    pdfs: list[Path], database_url: Optional[str], limit: Optional[int]
) -> list[tuple[Path, str]]:
    """Return [(pdf, status)] pairs. status in {'process', 'skip-up-to-date'}.

    Stops adding 'process' entries once `limit` is hit.
    """
    conn = _connect(database_url) if database_url else None
    plan: list[tuple[Path, str]] = []
    to_process = 0
    try:
        for pdf in pdfs:
            source_path = repo_relative_path(pdf)
            status = "process"
            if conn is not None:
                existing = find_existing_row(conn, source_path)
                if existing and is_up_to_date(pdf, existing[1]):
                    status = "skip-up-to-date"
            if status == "process":
                if limit is not None and to_process >= limit:
                    continue
                to_process += 1
            plan.append((pdf, status))
    finally:
        if conn is not None:
            conn.close()
    return plan


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--brand",
    "brand_filter",
    default=None,
    help="Restrict to PDFs under data/product_data/<brand>/ only.",
)
@click.option(
    "--limit",
    default=None,
    type=int,
    help="Process only the first N unprocessed PDFs (useful for testing).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="List what would be processed; no API calls or DB writes.",
)
@click.option(
    "--concurrency",
    default=3,
    type=int,
    help="Parallel worker count (default 3).",
)
@click.option(
    "--dpi",
    default=150,
    type=int,
    help="DPI for rasterizing PDF pages (default 150).",
)
@click.option(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Claude model to use for vision extraction.",
)
def ingest(
    brand_filter: Optional[str],
    limit: Optional[int],
    dry_run: bool,
    concurrency: int,
    dpi: int,
    model: str,
) -> None:
    """Ingest PDFs from data/product_data/ into knowledge_items (Postgres)."""
    database_url = os.environ.get("DATABASE_URL")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not dry_run:
        missing = []
        if not database_url:
            missing.append("DATABASE_URL")
        if not api_key:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            click.echo(
                f"Error: missing required env var(s): {', '.join(missing)}",
                err=True,
            )
            sys.exit(1)

    pdfs = discover_pdfs(brand_filter)
    if not pdfs:
        click.echo(
            f"No PDFs found under {PRODUCT_DATA_ROOT}"
            + (f"/{brand_filter}" if brand_filter else "")
        )
        sys.exit(0)

    brands_seen = sorted({derive_brand(p) for p in pdfs})
    click.echo(f"Discovered {len(pdfs)} PDFs across brands: {', '.join(brands_seen)}")
    click.echo(f"Model: {model}  DPI: {dpi}  Concurrency: {concurrency}")
    if limit is not None:
        click.echo(f"Limit: {limit}")
    click.echo("-" * 60)

    plan = plan_pdfs(pdfs, database_url if not dry_run or database_url else None, limit)

    if dry_run:
        processable = [p for p, s in plan if s == "process"]
        skippable = [p for p, s in plan if s == "skip-up-to-date"]
        for pdf in processable:
            click.echo(f"  PLAN  process  {repo_relative_path(pdf)}  (brand={derive_brand(pdf)})")
        for pdf in skippable:
            click.echo(f"  PLAN  skip     {repo_relative_path(pdf)}  (already up-to-date)")
        click.echo("-" * 60)
        click.echo(
            f"Dry run: {len(processable)} would be processed, "
            f"{len(skippable)} would be skipped. No API calls, no DB writes."
        )
        return

    # Pre-register brands for the subset we'll process so workers don't race.
    assert database_url is not None
    with _connect(database_url) as conn:
        for brand in brands_seen:
            ensure_brand(conn, brand)

    to_process = [pdf for pdf, status in plan if status == "process"]
    skipped_upfront = [pdf for pdf, status in plan if status == "skip-up-to-date"]
    for pdf in skipped_upfront:
        click.echo(f"  SKIP  {repo_relative_path(pdf)} (already processed)")

    results: list[str] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(
                process_single_pdf,
                pdf,
                database_url,
                api_key,
                model,
                dpi,
            ): pdf
            for pdf in to_process
        }
        for future in as_completed(futures):
            line = future.result()
            click.echo(line)
            results.append(line)

    ok = sum(1 for r in results if r.startswith("  OK"))
    fail = sum(1 for r in results if r.startswith("  FAIL"))
    skip = len(skipped_upfront) + sum(1 for r in results if r.startswith("  SKIP"))
    click.echo("-" * 60)
    click.echo(f"Done: {ok} processed, {skip} skipped, {fail} failed")
    if fail:
        click.echo(f"See {ERROR_LOG} for error details.")


if __name__ == "__main__":
    ingest()
