import base64
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
import click
from pdf2image import convert_from_path
from PIL import Image

# Allow large images (some PDFs like engineering drawings are very high-res)
Image.MAX_IMAGE_PIXELS = 500_000_000

EXTRACTION_PROMPT = """\
You are analyzing pages from a product installation drawing / specification PDF \
for a vehicle lift manufacturer (Mohawk Lifts). Extract ALL useful data from these pages.

For each page, extract:
- Model number and variant (e.g. 75-30-F, flush mount vs surface mount)
- Product type/category (e.g. parallelogram lift, rolling jack, slab requirements)
- Dimensional data: lengths, widths, heights, weights, clearances (with units)
- Capacity ratings and load specifications
- Installation requirements: pit dimensions, slab thickness, anchor specs, utility requirements
- Bill of materials / parts lists with part numbers and quantities
- Assembly notes and special instructions
- Certification info (ALI/ETL, ANSI standards)
- Any text visible in title blocks, revision notes, or callouts

Return the result as JSON with this structure:
{
  "model": "<model number>",
  "variant": "<flush/surface/etc>",
  "product_type": "<lift type>",
  "capacity": "<rated capacity with units>",
  "dimensions": { ... all dimensional data ... },
  "installation_requirements": { ... pit, slab, anchors, utilities ... },
  "parts_list": [ { "part_number": "", "description": "", "qty": 0 } ],
  "certifications": [],
  "notes": [],
  "pages_summary": [
    { "page": 1, "description": "<what this page shows>", "extracted_text": "<all visible text>" }
  ]
}

Be thorough - capture every dimension, part number, and specification visible in the drawings. \
If a value is not visible or not applicable, omit it rather than guessing."""


def pdf_needs_vision(pdf_path: str) -> bool:
    """Check if a PDF is image-only (no extractable text)."""
    import subprocess

    try:
        result = subprocess.run(
            ["pdftotext", pdf_path, "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        text = result.stdout.strip()
        return len(text) < 50
    except Exception:
        return True


def render_pdf_pages(pdf_path: str, dpi: int = 150) -> list[tuple[int, str]]:
    """Render PDF pages to base64-encoded JPEG images."""
    pages = convert_from_path(pdf_path, dpi=dpi, fmt="jpeg")
    encoded = []
    max_bytes = 4_500_000  # Stay under 5MB API limit
    max_dim = 7900  # API limit is 8000px per dimension
    for i, page in enumerate(pages):
        # Cap dimensions first
        w, h = page.size
        if w > max_dim or h > max_dim:
            ratio = min(max_dim / w, max_dim / h)
            page = page.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        page.save(buf, format="JPEG", quality=80)
        # If image is too large in bytes, downscale further
        if buf.tell() > max_bytes:
            scale = 0.5
            w, h = page.size
            while buf.tell() > max_bytes and scale > 0.1:
                resized = page.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=70)
                scale *= 0.75
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        encoded.append((i + 1, b64))
    return encoded


def extract_with_vision(
    client: anthropic.Anthropic,
    pdf_name: str,
    pages: list[tuple[int, str]],
    model: str,
) -> dict:
    """Send PDF page images to Claude for data extraction."""
    # Process in batches of 20 pages max to stay within limits
    batch_size = 20
    all_results = []

    for batch_start in range(0, len(pages), batch_size):
        batch = pages[batch_start : batch_start + batch_size]
        content = []

        content.append(
            {
                "type": "text",
                "text": f"Analyzing PDF: {pdf_name} (pages {batch[0][0]}-{batch[-1][0]} of {len(pages)} total)",
            }
        )

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
            content.append(
                {"type": "text", "text": f"(Page {page_num})"}
            )

        content.append({"type": "text", "text": EXTRACTION_PROMPT})

        # Retry with exponential backoff for rate limits
        max_retries = 5
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
                wait = 2 ** (attempt + 1) * 15  # 30s, 60s, 120s, 240s
                click.echo(f"    Rate limited on {pdf_name}, waiting {wait}s...")
                time.sleep(wait)

        response_text = response.content[0].text

        # Try to parse JSON from response
        try:
            # Find JSON in the response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(response_text[start:end])
                all_results.append(parsed)
            else:
                all_results.append({"raw_response": response_text})
        except json.JSONDecodeError:
            all_results.append({"raw_response": response_text})

    if len(all_results) == 1:
        return all_results[0]

    # Merge multiple batch results
    merged = all_results[0]
    for extra in all_results[1:]:
        if "pages_summary" in extra:
            merged.setdefault("pages_summary", []).extend(extra["pages_summary"])
        if "parts_list" in extra:
            merged.setdefault("parts_list", []).extend(extra["parts_list"])
        if "notes" in extra:
            merged.setdefault("notes", []).extend(extra["notes"])
    return merged


def process_single_pdf(
    client: anthropic.Anthropic,
    pdf_path: Path,
    output_dir: Path,
    model: str,
    dpi: int,
) -> str:
    """Process a single PDF and write extracted data to JSON."""
    pdf_name = pdf_path.name
    output_file = output_dir / f"{pdf_path.stem}.json"

    if output_file.exists():
        return f"  SKIP  {pdf_name} (already processed)"

    needs_vision = pdf_needs_vision(str(pdf_path))

    if not needs_vision:
        # Still use vision for PDFs with some text, as they may have diagrams
        pass

    try:
        pages = render_pdf_pages(str(pdf_path), dpi=dpi)
    except Exception as e:
        return f"  FAIL  {pdf_name} - render error: {e}"

    try:
        result = extract_with_vision(client, pdf_name, pages, model)
        result["_source_file"] = pdf_name
        result["_pages_count"] = len(pages)
        result["_image_only"] = needs_vision
        result["_extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)

        return f"  OK    {pdf_name} ({len(pages)} pages) -> {output_file.name}"
    except anthropic.APIError as e:
        return f"  FAIL  {pdf_name} - API error: {e}"


def enrich_pdfs(
    input_dir: str,
    output_dir: str,
    model: str,
    max_concurrent: int,
    dpi: int,
):
    """Main enrichment entrypoint."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        click.echo("Error: ANTHROPIC_API_KEY environment variable is not set.", err=True)
        click.echo("Get your key at https://console.anthropic.com/settings/keys", err=True)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(input_path.glob("*.pdf"))
    if not pdfs:
        click.echo(f"No PDF files found in {input_path}")
        sys.exit(1)

    click.echo(f"Found {len(pdfs)} PDFs in {input_path}")
    click.echo(f"Output directory: {output_path}")
    click.echo(f"Model: {model}")
    click.echo(f"DPI: {dpi}")
    click.echo(f"Max concurrent: {max_concurrent}")
    click.echo("-" * 60)

    results = []
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(
                process_single_pdf, client, pdf, output_path, model, dpi
            ): pdf
            for pdf in pdfs
        }
        for future in as_completed(futures):
            result = future.result()
            click.echo(result)
            results.append(result)

    ok_count = sum(1 for r in results if r.startswith("  OK"))
    skip_count = sum(1 for r in results if r.startswith("  SKIP"))
    fail_count = sum(1 for r in results if r.startswith("  FAIL"))

    click.echo("-" * 60)
    click.echo(f"Done: {ok_count} processed, {skip_count} skipped, {fail_count} failed")
