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
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

Image.MAX_IMAGE_PIXELS = 500_000_000

# Legacy version tag from the single-tier era. Rows still carrying this
# version are treated as Tier-2 (raw_content populated).
EXTRACTOR_VERSION_LEGACY = "ingest.py-v1"
EXTRACTOR_VERSION_TIER1 = "ingest.py-v1-tier1"
EXTRACTOR_VERSION_TIER2 = "ingest.py-v1-tier2"

# v4-trimmed vocabulary. Authoritative list lives in
# docs/classifier-system-prompt-v1.md (Appendix A). The classifier may
# also emit `uncategorized` for documents it can't confidently tag.
VALID_CATEGORIES = {
    "installation-guides",
    "service-procedures",
    "parts-catalog",
    "specifications",
    "operation-manuals",
    "safety-warnings",
    "warranty-documentation",
    "marketing-brochure",
    "manufacturer-training",
    "technical-bulletin",
    "site-survey",
    "compliance-regulations",
    "procurement-process",
    "industry-reference",
    "rfp-received",
    "compliance-template",
    "install-handoff-sop",
    "service-workflow-sop",
    "contract-reporting-sop",
    "sales-playbook",
    "capability-statement",
    "cold-outreach-template",
    "voice-samples",
    "sales-collateral",
    "case-study",
    "liftnow-internal-training",
    "liftnow-credentials",
    "insurance-policy",
    "bond-instrument",
    "rfp-response",
    "customer-quote-history",
    "customer-po",
    "customer-invoice",
    "customer-contract",
    "customer-account-setup",
    "vendor-onboarding-completed",
    "vendor-po",
    "vendor-invoice",
    "vendor-agreement",
    "subcontract-agreement",
    "vendor-cost-pricing",
    "list-pricing",
    "service-record",
    "install-record",
    "payment-record",
    "damage-claim",
    "certified-payroll",
    "contract-reporting-record",
    "bid-protest",
    "change-order",
    "competitive-intelligence",
    "win-loss-debrief",
    "financial-statement",
    "commission-report",
    "employment-document",
    "regulatory-update",
    "uncategorized",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_DATA_ROOT = REPO_ROOT / "data" / "product_data"
ERROR_LOG = REPO_ROOT / "data" / "extraction-errors.log"

# The classifier system prompt is authored in docs/ and loaded at module
# import time. We don't inline its 500+ lines here — single source of
# truth, and the file is what ships in the PR diff.
CLASSIFIER_PROMPT_PATH = REPO_ROOT / "docs" / "classifier-system-prompt-v2.1.md"


def _load_classifier_prompt() -> str:
    try:
        return CLASSIFIER_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Classifier prompt missing: {CLASSIFIER_PROMPT_PATH}. "
            "This file is required for the v4-trimmed multi-tag classifier."
        ) from e


CLASSIFIER_SYSTEM_PROMPT_V1 = _load_classifier_prompt()


def build_shallow_extraction_prompt(brand_name: str) -> str:
    """Tier-1 prompt: cover + last page only, body extraction not in scope.

    Categorization is now handled by a separate classify_document() pass
    against the v4-trimmed vocabulary (see docs/classifier-system-prompt-v1.md).
    This prompt only collects the scaffolding fields.
    """
    return f"""You are extracting cover-page metadata from a PDF in Liftnow's knowledge base. The PDF is from manufacturer/brand: {brand_name}.

You are seeing only the FIRST and LAST page of the document — usually the cover and a revision/back page. Do not attempt to extract the full document body; that's a separate pass. Do NOT classify the document; a separate pass handles classification.

Extract:
- title: the document title as it appears on the cover (else a short descriptive title)
- summary: 2-3 sentences describing what the document is and what it covers, based on the cover and last page only
- tags: up to 15 short keyword tags surfaced from the cover (model numbers, product family, capacities, certifications, etc.). Always include the brand name as one tag.
- effective_date: ISO date (YYYY-MM-DD) if visible on cover or revision block, else null
- supersedes_previous: boolean inferred from any revision/version note visible on these pages

Return ONLY valid JSON in this exact shape:
{{
  "title": "<string>",
  "summary": "<string>",
  "tags": ["<string>", ...],
  "effective_date": "<ISO date or null>",
  "supersedes_previous": <boolean>
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
) -> Optional[tuple[int, Optional[datetime], Optional[str], bool]]:
    """Return (id, extracted_at, extractor_version, has_raw_content) or None.

    `has_raw_content` is True when the row's raw_content is non-empty — the
    canonical "this row is Tier-2" signal, including legacy rows written by
    the pre-tier-aware ingester.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, extracted_at, extractor_version,
                   raw_content IS NOT NULL AND length(raw_content) > 0
              FROM knowledge_items
             WHERE source_path = %s
             LIMIT 1
            """,
            (source_path,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return row[0], row[1], row[2], bool(row[3])


def existing_tier(
    extractor_version: Optional[str], has_raw_content: bool
) -> int:
    """Infer the effective tier of an existing row.

    Tier-2 if raw_content is populated OR the version tag explicitly says
    tier2. Otherwise Tier-1.
    """
    if has_raw_content:
        return 2
    if extractor_version == EXTRACTOR_VERSION_TIER2:
        return 2
    if extractor_version == EXTRACTOR_VERSION_LEGACY:
        # Legacy ingester always wrote raw_content; if it's empty here,
        # something odd happened — fall through to tier 1.
        return 1
    return 1


def is_up_to_date(
    pdf_path: Path, extracted_at: Optional[datetime]
) -> bool:
    if extracted_at is None:
        return False
    mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime, tz=timezone.utc)
    if extracted_at.tzinfo is None:
        extracted_at = extracted_at.replace(tzinfo=timezone.utc)
    return extracted_at > mtime


def count_pdf_pages(pdf_path: Path) -> int:
    """Page count via pdfinfo (no rasterization). Returns 0 on failure."""
    try:
        info = pdfinfo_from_path(str(pdf_path))
        return int(info.get("Pages") or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Vision extraction (pattern from enrich.py)
# ---------------------------------------------------------------------------


def _encode_page(page: "Image.Image") -> str:
    max_bytes = 4_500_000
    max_dim = 7900
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
            resized = page.resize(
                (int(w * scale), int(h * scale)), Image.LANCZOS
            )
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=70)
            scale *= 0.75
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def render_first_and_last_pages(
    pdf_path: Path, total_pages: int, dpi: int = 130
) -> list[tuple[int, str]]:
    """Tier-1 helper: render only page 1 and the final page.

    Renders pages 1 and N separately so a 200-page manual costs the same as
    a 2-page flyer. If the PDF is single-page, returns just that page.
    """
    if total_pages <= 0:
        return []
    encoded: list[tuple[int, str]] = []

    first = convert_from_path(
        str(pdf_path), dpi=dpi, fmt="jpeg", first_page=1, last_page=1
    )
    if first:
        encoded.append((1, _encode_page(first[0])))

    if total_pages > 1:
        last = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            fmt="jpeg",
            first_page=total_pages,
            last_page=total_pages,
        )
        if last:
            encoded.append((total_pages, _encode_page(last[0])))
    return encoded


def render_pdf_pages(pdf_path: str, dpi: int = 130) -> list[tuple[int, str]]:
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


def extract_shallow_with_vision(
    client: anthropic.Anthropic,
    pdf_name: str,
    brand_name: str,
    pages: list[tuple[int, str]],
    model: str,
) -> dict:
    """Tier-1 vision call: a single message with cover + last page only."""
    prompt = build_shallow_extraction_prompt(brand_name)
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Shallow classification for: {pdf_name}. "
                f"You will see {len(pages)} sample page(s) (first and last)."
            ),
        }
    ]
    for page_num, b64_data in pages:
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
                max_tokens=1024,
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
            return json.loads(response_text[start:end])
    except json.JSONDecodeError:
        pass
    return {"raw_response": response_text}


# ---------------------------------------------------------------------------
# Tier-2 chunked body extraction
# ---------------------------------------------------------------------------
#
# A 75-page manual sent in one mega-request takes ~10 minutes. Split into
# small page chunks, fan out to Claude in parallel, stitch the markdown
# back together in page order. Classification (title / category / tags /
# summary) is a separate single shallow pass on cover + last page so we
# don't pay for it N times.


def build_chunk_body_prompt(
    brand_name: str,
    chunk_pages: list[int],
    total_pages: int,
    include_page_summaries: bool,
) -> str:
    page_label = (
        f"pages {chunk_pages[0]}-{chunk_pages[-1]}"
        if len(chunk_pages) > 1
        else f"page {chunk_pages[0]}"
    )
    if include_page_summaries:
        json_shape = (
            '{\n'
            '  "content_markdown": "<full text of these pages as markdown>",\n'
            '  "pages_summary": [{"page": <int>, "description": "<string>"}]\n'
            '}'
        )
        page_summary_line = (
            "Also include a short description for each page in pages_summary."
        )
    else:
        json_shape = (
            '{\n'
            '  "content_markdown": "<full text of these pages as markdown>"\n'
            '}'
        )
        page_summary_line = "Do NOT include any pages_summary array."

    return f"""You are extracting the BODY content of a PDF in Liftnow's knowledge base. Brand: {brand_name}. You are seeing {page_label} of {total_pages} total.

Capture every substantive piece of text visible on these pages:
- All paragraphs, captions, table cells, headings
- Every model number, specification, price, part number, dimension visible
- Retain structure (tables, lists, headings) as markdown where feasible
- DO NOT classify the document, produce a title, or summarize the document. A separate pass handles that.
- DO NOT collapse or summarize content; we need completeness.
- {page_summary_line}

Return ONLY valid JSON in this exact shape:
{json_shape}"""


def extract_chunk_body_with_vision(
    client: anthropic.Anthropic,
    pdf_name: str,
    brand_name: str,
    chunk: list[tuple[int, str]],
    total_pages: int,
    model: str,
    include_page_summaries: bool,
) -> dict:
    """Run body-only extraction on one chunk of pages."""
    chunk_page_numbers = [p for p, _ in chunk]
    prompt = build_chunk_body_prompt(
        brand_name, chunk_page_numbers, total_pages, include_page_summaries
    )

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Body extraction for {pdf_name}, "
                f"pages {chunk_page_numbers[0]}-{chunk_page_numbers[-1]} "
                f"of {total_pages}."
            ),
        }
    ]
    for page_num, b64_data in chunk:
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
                f"    Rate limited on {pdf_name} chunk "
                f"{chunk_page_numbers[0]}-{chunk_page_numbers[-1]}, "
                f"waiting {wait}s..."
            )
            time.sleep(wait)
    assert response is not None

    response_text = response.content[0].text
    try:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response_text[start:end])
    except json.JSONDecodeError:
        pass
    # Fall back: keep whatever the model returned so we don't drop a chunk.
    return {"content_markdown": response_text}


def extract_deep_with_vision_chunked(
    client: anthropic.Anthropic,
    pdf_name: str,
    brand_name: str,
    pages: list[tuple[int, str]],
    model: str,
    chunk_size: int,
    chunk_concurrency: int,
    include_page_summaries: bool,
) -> dict:
    """Tier-2 deep extraction via parallel chunks + a single classify pass.

    Steps:
      1. Run the shallow classifier on cover + last page (one API call).
      2. Split pages into chunks of `chunk_size` and dispatch body-only
         extraction in parallel (up to `chunk_concurrency` in flight).
      3. Stitch chunk markdown in page order.
      4. Combine classification + stitched body into the dict shape the
         row writer expects.
    """
    if not pages:
        return {}

    if chunk_size <= 0:
        raise ValueError("chunk_size must be >= 1")
    chunk_concurrency = max(1, chunk_concurrency)

    # 1) Classification pass on first + last page.
    classify_inputs = [pages[0]]
    if len(pages) > 1:
        classify_inputs.append(pages[-1])
    classification = extract_shallow_with_vision(
        client, pdf_name, brand_name, classify_inputs, model
    )

    # 2) Body chunks in parallel.
    chunks: list[list[tuple[int, str]]] = [
        pages[i : i + chunk_size] for i in range(0, len(pages), chunk_size)
    ]
    chunk_results: list[Optional[dict]] = [None] * len(chunks)

    def run_chunk(idx: int) -> tuple[int, dict]:
        return idx, extract_chunk_body_with_vision(
            client,
            pdf_name,
            brand_name,
            chunks[idx],
            len(pages),
            model,
            include_page_summaries,
        )

    with ThreadPoolExecutor(max_workers=chunk_concurrency) as pool:
        futures = [pool.submit(run_chunk, i) for i in range(len(chunks))]
        for future in as_completed(futures):
            idx, result = future.result()
            chunk_results[idx] = result

    # 3) Stitch in page order.
    body_parts: list[str] = []
    pages_summary: list[dict] = []
    for idx, result in enumerate(chunk_results):
        if result is None:
            continue
        body = result.get("content_markdown") or ""
        if isinstance(body, str) and body.strip():
            body_parts.append(body.strip())
        if include_page_summaries:
            ps = result.get("pages_summary") or []
            if isinstance(ps, list):
                pages_summary.extend(ps)

    return {
        "category": classification.get("category"),
        "title": classification.get("title"),
        "summary": classification.get("summary"),
        "tags": classification.get("tags"),
        "effective_date": classification.get("effective_date"),
        "supersedes_previous": classification.get("supersedes_previous"),
        "content_markdown": "\n\n".join(body_parts),
        "pages_summary": pages_summary,
    }


# ---------------------------------------------------------------------------
# Document classifier (v4-trimmed multi-tag vocabulary)
# ---------------------------------------------------------------------------


def _truncate_for_classifier(text: str, max_chars: int = 50_000) -> str:
    """Match the retag script's truncation strategy.

    The classifier doesn't need full text — head + tail captures cover,
    table of contents, and the back matter where most signal lives. For
    docs longer than max_chars we keep the first 25k + last 25k.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...truncated...]\n\n" + text[-half:]


def classify_document(
    client: anthropic.Anthropic,
    *,
    title: str,
    summary: str,
    body_text: str,
    model: str,
) -> list[str]:
    """Run the v4-trimmed classifier prompt and return validated tags.

    Always returns a non-empty list. On any error or unparseable response
    the row is tagged ['uncategorized'] so it gets surfaced for review.
    """
    head = title or ""
    if summary:
        head = head + "\n\n" + summary if head else summary
    body = _truncate_for_classifier(body_text or "")
    document_text = (head + "\n\n" + body).strip() if body else head
    if not document_text:
        return ["uncategorized"]

    max_retries = 5
    response = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=200,
                system=CLASSIFIER_SYSTEM_PROMPT_V1,
                messages=[{"role": "user", "content": document_text}],
            )
            break
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** (attempt + 1) * 15
            click.echo(
                f"    Rate limited classifying {title!r}, waiting {wait}s..."
            )
            time.sleep(wait)
        except anthropic.APIError:
            return ["uncategorized"]
    if response is None:
        return ["uncategorized"]

    raw_text = response.content[0].text.strip() if response.content else ""
    # Find the first bracketed JSON array in the response.
    start = raw_text.find("[")
    end = raw_text.rfind("]") + 1
    if start < 0 or end <= start:
        return ["uncategorized"]
    try:
        parsed = json.loads(raw_text[start:end])
    except json.JSONDecodeError:
        return ["uncategorized"]
    return _coerce_categories(parsed)


# ---------------------------------------------------------------------------
# Row writing
# ---------------------------------------------------------------------------


def _coerce_categories(raw) -> list[str]:
    """Validate a classifier output against the v4-trimmed vocabulary.

    Accepts a list of strings (the canonical shape) or a single string
    (defensive — old single-tag callers, raw classifier responses that
    forgot the array). Returns a validated list with at least one entry;
    falls back to ['uncategorized'] when nothing matches the vocabulary.
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ["uncategorized"]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        c = item.strip()
        if c in VALID_CATEGORIES and c not in seen:
            cleaned.append(c)
            seen.add(c)
    if not cleaned:
        return ["uncategorized"]
    return cleaned


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
    # Postgres FTS handles multi-megabyte tsvectors fine; truncating
    # raw_content here means long install manuals only get ~6% of their
    # body indexed and queries about specific specs (anchor pattern,
    # concrete depth, etc.) miss the right document.
    parts = [title, summary, " ".join(tags), content]
    return " ".join(p for p in parts if p)


def upsert_knowledge_item(
    conn: psycopg.Connection,
    *,
    source_path: str,
    source_filename: str,
    source_pages_count: int,
    brand_id: int,
    extraction: dict,
    tier: int,
) -> int:
    """Insert or update a knowledge_items row for the given tier.

    Tier 1: raw_content NULL, search_text from title+summary+tags only,
        extracted_data carries `tier=1` plus effective_date / supersedes flag.
    Tier 2: raw_content = full markdown body, search_text covers the FULL
        body (no truncation — Postgres FTS handles multi-MB documents),
        extracted_data adds pages_summary and `tier=2`.
    """
    if tier not in (1, 2):
        raise ValueError(f"tier must be 1 or 2, got {tier}")

    # `category` is a TEXT[] of v4-trimmed vocabulary tags (multi-tag).
    # The extraction dict carries it as a list of strings under
    # extraction['category']; psycopg3 maps Python list -> Postgres
    # TEXT[] natively.
    category = _coerce_categories(extraction.get("category"))
    title = (
        str(extraction.get("title") or source_filename).strip()[:500]
        or source_filename
    )
    summary = str(extraction.get("summary") or "").strip()
    tags = _coerce_tags(extraction.get("tags"))

    supersedes = extraction.get("supersedes_previous")
    extracted_data: dict = {
        "tier": tier,
        "effective_date": extraction.get("effective_date"),
        "supersedes_previous": bool(supersedes) if supersedes is not None else None,
    }

    if tier == 2:
        raw_content: Optional[str] = str(extraction.get("content_markdown") or "")
        extracted_data["pages_summary"] = extraction.get("pages_summary") or []
        search_text = _build_search_text(title, summary, tags, raw_content or "")
        version = EXTRACTOR_VERSION_TIER2
    else:
        raw_content = None
        search_text = " ".join(p for p in (title, summary, " ".join(tags)) if p)
        version = EXTRACTOR_VERSION_TIER1

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
                    version,
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
                version,
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
    tier: int,
    chunk_size: int = 5,
    chunk_concurrency: int = 5,
    include_page_summaries: bool = False,
) -> str:
    """Process one PDF in three phases so we never hold a Postgres
    connection across the long Claude vision call.

    Phase 1: open conn, register brand, decide whether to skip, close.
    Phase 2: rasterize + Claude vision with NO DB connection open.
             (Tier-2 manuals can spend 10+ min here, well past Neon's
             idle-in-transaction timeout.)
    Phase 3: fresh conn, upsert, commit, close.

    Between phases another worker may have written the same source_path —
    that's fine, upsert_knowledge_item is keyed on source_path and will
    UPDATE in place.
    """
    source_path = repo_relative_path(pdf_path)
    try:
        brand_name = derive_brand(pdf_path)
    except ValueError:
        return f"  SKIP  {source_path} (not under data/product_data/)"

    # ---- Phase 1: read state, decide, release connection ----
    try:
        with _connect(database_url) as conn:
            brand_id = ensure_brand(conn, brand_name)
            existing = find_existing_row(conn, source_path)
            if existing:
                _, extracted_at, ext_version, has_raw = existing
                current_tier = existing_tier(ext_version, has_raw)
                if tier == 1 and current_tier >= 1:
                    return f"  SKIP  {source_path} (already tier-{current_tier})"
                if (
                    tier == 2
                    and current_tier >= 2
                    and is_up_to_date(pdf_path, extracted_at)
                ):
                    return f"  SKIP  {source_path} (already tier-2, up-to-date)"
            existed_before = existing is not None
    except Exception as e:
        log_error(pdf_path, e)
        tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
        return f"  FAIL  {source_path} - {type(e).__name__}: {tb}"

    # ---- Phase 2: long-running work, no DB connection held ----
    try:
        client = anthropic.Anthropic(api_key=api_key)
        if tier == 1:
            total_pages = count_pdf_pages(pdf_path)
            sample_pages = render_first_and_last_pages(
                pdf_path, total_pages=total_pages, dpi=dpi
            )
            if not sample_pages:
                return f"  FAIL  {source_path} - 0 pages rendered"
            extraction = extract_shallow_with_vision(
                client, pdf_path.name, brand_name, sample_pages, model
            )
            pages_count = total_pages or len(sample_pages)
            classifier_body = ""  # No body at tier-1; classify on title+summary only.
        else:
            pages = render_pdf_pages(str(pdf_path), dpi=dpi)
            if not pages:
                return f"  FAIL  {source_path} - 0 pages rendered"
            extraction = extract_deep_with_vision_chunked(
                client,
                pdf_path.name,
                brand_name,
                pages,
                model,
                chunk_size=chunk_size,
                chunk_concurrency=chunk_concurrency,
                include_page_summaries=include_page_summaries,
            )
            pages_count = len(pages)
            classifier_body = str(extraction.get("content_markdown") or "")

        # Multi-tag classification against the v4-trimmed vocabulary. This
        # is a separate Claude call from the extraction pass — its output
        # overwrites whatever `category` the extraction prompt may have
        # produced.
        extraction["category"] = classify_document(
            client,
            title=str(extraction.get("title") or pdf_path.name),
            summary=str(extraction.get("summary") or ""),
            body_text=classifier_body,
            model=model,
        )
    except Exception as e:
        log_error(pdf_path, e)
        tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
        return f"  FAIL  {source_path} - {type(e).__name__}: {tb}"

    # ---- Phase 3: fresh connection, write, commit, close ----
    try:
        with _connect(database_url) as conn:
            row_id = upsert_knowledge_item(
                conn,
                source_path=source_path,
                source_filename=pdf_path.name,
                source_pages_count=pages_count,
                brand_id=brand_id,
                extraction=extraction,
                tier=tier,
            )
    except Exception as e:
        log_error(pdf_path, e)
        tb = traceback.format_exc(limit=2).strip().splitlines()[-1]
        return f"  FAIL  {source_path} - {type(e).__name__}: {tb}"

    action = "UPDATE" if existed_before else "INSERT"
    return (
        f"  OK    {source_path} ({pages_count}p, brand={brand_name}, tier={tier}) "
        f"-> knowledge_items.id={row_id} [{action}]"
    )


def lookup_pdf_path_for_id(
    database_url: str, knowledge_item_id: int
) -> Path:
    """Resolve a knowledge_items.id to the absolute path of its source PDF."""
    with _connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_path FROM knowledge_items WHERE id = %s",
            (knowledge_item_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            raise click.ClickException(
                f"knowledge_items.id={knowledge_item_id}: row not found or has no source_path"
            )
        rel = row[0]
    abs_path = (REPO_ROOT / rel).resolve()
    if not abs_path.exists():
        raise click.ClickException(f"PDF missing on disk: {abs_path}")
    return abs_path


# ---------------------------------------------------------------------------
# Dry-run planner
# ---------------------------------------------------------------------------


def plan_pdfs(
    pdfs: list[Path],
    database_url: Optional[str],
    limit: Optional[int],
    tier: int,
) -> list[tuple[Path, str]]:
    """Return [(pdf, status)] pairs.

    Status values: 'process' (will run), 'skip-up-to-date' (target tier
    already satisfied for this PDF). Stops adding 'process' entries once
    `limit` is hit.
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
                if existing:
                    _, extracted_at, ext_version, has_raw = existing
                    current_tier = existing_tier(ext_version, has_raw)
                    if tier == 1 and current_tier >= 1:
                        status = "skip-up-to-date"
                    elif (
                        tier == 2
                        and current_tier >= 2
                        and is_up_to_date(pdf, extracted_at)
                    ):
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
    "--tier",
    type=click.IntRange(1, 2),
    default=1,
    show_default=True,
    help="1 = shallow (cover + last page), 2 = deep (all pages).",
)
@click.option(
    "--brand",
    "brand_filter",
    default=None,
    help="Restrict to PDFs under data/product_data/<brand>/ only.",
)
@click.option(
    "--id",
    "knowledge_item_id",
    type=int,
    default=None,
    help=(
        "Run on a single knowledge_items row by id. Resolves the row's "
        "source_path to the on-disk PDF and processes only that PDF. "
        "Used by the on-demand upgrade endpoint."
    ),
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
    default=130,
    type=int,
    help="DPI for rasterizing PDF pages (default 130 — quality / speed sweet spot).",
)
@click.option(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Claude model to use for vision extraction.",
)
@click.option(
    "--chunk-size",
    default=5,
    type=int,
    help=(
        "Tier-2 only: pages per parallel body-extraction chunk (default 5). "
        "Smaller = more parallelism, more API calls."
    ),
)
@click.option(
    "--chunk-concurrency",
    default=5,
    type=int,
    help=(
        "Tier-2 only: max parallel chunk requests per PDF (default 5). "
        "Combined with --concurrency this caps total in-flight Claude calls."
    ),
)
@click.option(
    "--include-page-summaries",
    is_flag=True,
    default=False,
    help=(
        "Tier-2 only: also emit per-page descriptions into "
        "extracted_data.pages_summary. Off by default — slower without "
        "adding to raw_content."
    ),
)
def ingest(
    tier: int,
    brand_filter: Optional[str],
    knowledge_item_id: Optional[int],
    limit: Optional[int],
    dry_run: bool,
    concurrency: int,
    dpi: int,
    model: str,
    chunk_size: int,
    chunk_concurrency: int,
    include_page_summaries: bool,
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

    if knowledge_item_id is not None:
        if brand_filter or limit is not None:
            click.echo(
                "Warning: --brand and --limit are ignored when --id is set.",
                err=True,
            )
        if dry_run:
            click.echo(
                f"Dry run: would process knowledge_items.id={knowledge_item_id} "
                f"at tier={tier}. No API calls, no DB writes."
            )
            return
        assert database_url is not None and api_key is not None
        pdf_path = lookup_pdf_path_for_id(database_url, knowledge_item_id)
        click.echo(
            f"Single-row mode: id={knowledge_item_id} -> {repo_relative_path(pdf_path)} "
            f"(tier={tier})"
        )
        # Pre-register brand so workers don't race; not strictly needed for
        # single-row but keeps the code paths uniform.
        with _connect(database_url) as conn:
            ensure_brand(conn, derive_brand(pdf_path))
        line = process_single_pdf(
            pdf_path,
            database_url,
            api_key,
            model,
            dpi,
            tier,
            chunk_size=chunk_size,
            chunk_concurrency=chunk_concurrency,
            include_page_summaries=include_page_summaries,
        )
        click.echo(line)
        if line.lstrip().startswith("FAIL"):
            sys.exit(1)
        return

    pdfs = discover_pdfs(brand_filter)
    if not pdfs:
        click.echo(
            f"No PDFs found under {PRODUCT_DATA_ROOT}"
            + (f"/{brand_filter}" if brand_filter else "")
        )
        sys.exit(0)

    brands_seen = sorted({derive_brand(p) for p in pdfs})
    click.echo(f"Discovered {len(pdfs)} PDFs across brands: {', '.join(brands_seen)}")
    click.echo(f"Tier: {tier}  Model: {model}  DPI: {dpi}  Concurrency: {concurrency}")
    if tier == 2:
        click.echo(
            f"Chunk size: {chunk_size}  Chunk concurrency: {chunk_concurrency}  "
            f"Page summaries: {'on' if include_page_summaries else 'off'}"
        )
    if limit is not None:
        click.echo(f"Limit: {limit}")
    click.echo("-" * 60)

    plan = plan_pdfs(
        pdfs,
        database_url if not dry_run or database_url else None,
        limit,
        tier=tier,
    )

    if dry_run:
        processable = [p for p, s in plan if s == "process"]
        skippable = [p for p, s in plan if s == "skip-up-to-date"]
        for pdf in processable:
            click.echo(
                f"  PLAN  process  {repo_relative_path(pdf)}  "
                f"(brand={derive_brand(pdf)}, tier={tier})"
            )
        for pdf in skippable:
            click.echo(
                f"  PLAN  skip     {repo_relative_path(pdf)}  "
                f"(target tier-{tier} already satisfied)"
            )
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
        click.echo(f"  SKIP  {repo_relative_path(pdf)} (target tier already satisfied)")

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
                tier,
                chunk_size,
                chunk_concurrency,
                include_page_summaries,
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
