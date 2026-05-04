"""KMZ-to-markdown converter for the Liftnow service map.

Standalone, re-runnable. Operationally Paul re-runs this when the service
map .kmz changes (subcontractor coverage updates), then re-runs the
Pillar 3 ingest so the KB picks up the refresh.

Usage:
    python scripts/kmz_to_markdown.py "<path/to/file.kmz>"

Output path: alongside the input, with extension swapped to `.md` and
" - Subcontractor Coverage" appended to the basename. e.g.

    data/pillar3-staging/Tier 2 - Internal/New Service Map.kmz
    -> data/pillar3-staging/Tier 2 - Internal/New Service Map - Subcontractor Coverage.md

Implementation notes:
- Uses stdlib only (zipfile, xml.etree.ElementTree, html, re).
- KML namespace is stripped via local-name matching so we don't need
  to fight ElementTree's namespace plumbing on every tag.
- Description CDATA is HTML-stripped to plain text.
- Coverage-area annotations (Polygon, LineString) are summarized by
  count if present; the source map for Liftnow has none.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter
from typing import Optional


# ---------------------------------------------------------------------------
# KML helpers (namespace-agnostic via local-name matching)
# ---------------------------------------------------------------------------


def localname(tag: str) -> str:
    """Strip the {namespace} prefix ElementTree keeps on every tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def find_child(elem: ET.Element, name: str) -> Optional[ET.Element]:
    for c in elem:
        if localname(c.tag) == name:
            return c
    return None


def find_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if localname(c.tag) == name]


def text_of(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def strip_html(s: str) -> str:
    """KML descriptions are HTML in CDATA. Convert to plain text:
    <br> -> newline, drop other tags, decode entities, collapse runs."""
    if not s:
        return ""
    # Convert common break tags to newlines BEFORE stripping all tags.
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)<\s*/?\s*p\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # Collapse 3+ blank lines, trim each line.
    lines = [ln.strip() for ln in s.splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# KML extraction
# ---------------------------------------------------------------------------


def extract_extended_data(placemark: ET.Element) -> dict[str, str]:
    """Return the ExtendedData <Data name="X"><value>Y</value></Data> map."""
    result: dict[str, str] = {}
    ext = find_child(placemark, "ExtendedData")
    if ext is None:
        return result
    for d in find_children(ext, "Data"):
        name = d.get("name") or ""
        v = find_child(d, "value")
        val = text_of(v)
        if name and val:
            result[name] = val
    return result


def render_placemark(
    p: ET.Element, level: int, folder_path: list[str]
) -> tuple[str, dict]:
    """Render a single Placemark as markdown. Returns (text, stats).

    `folder_path` is the chain of enclosing KML folder names, root-first. We
    embed it in every placemark as `- Folder:` so retrieval chunks keep
    their source-folder context (the H2 heading alone gets lost when a
    chunk is pulled in isolation).
    """
    name = text_of(find_child(p, "name")) or "(unnamed placemark)"
    address = text_of(find_child(p, "address"))
    desc = strip_html(text_of(find_child(p, "description")))
    ext = extract_extended_data(p)
    has_point = find_child(p, "Point") is not None
    has_polygon = find_child(p, "Polygon") is not None
    has_linestring = find_child(p, "LineString") is not None

    parts = [f"**{name}**"]
    # Folder context travels per-placemark so chunked retrieval keeps the
    # "this came from <folder>" signal — H2 position alone is fragile.
    if folder_path:
        parts.append(f"- Folder: {' / '.join(folder_path)}")
    else:
        parts.append("- Folder: (top-level, no folder)")
    if address:
        parts.append(f"- Address: {address}")
    # Render ExtendedData if it contains anything not already in description.
    if ext:
        for k, v in ext.items():
            # Skip noisy Address line if we already printed an address.
            if address and k.lower() in {"address", "street address"} and v == address:
                continue
            parts.append(f"- {k}: {v}")
    if desc and not ext:
        # Only render free-text description when no structured ExtendedData;
        # otherwise the structured fields supersede the HTML body.
        parts.append(desc)
    if has_polygon:
        parts.append("- _coverage area: polygon_")
    if has_linestring:
        parts.append("- _coverage area: linestring_")
    return "\n".join(parts), {
        "placemarks": 1,
        "with_extdata": 1 if ext else 0,
        "with_point": 1 if has_point else 0,
        "with_polygon": 1 if has_polygon else 0,
        "with_linestring": 1 if has_linestring else 0,
    }


def render_folder(
    folder: ET.Element, level: int, parent_path: list[str]
) -> tuple[str, dict]:
    """Render a Folder and its descendants. level=2 -> H2 for top folders.

    `parent_path` is the chain of enclosing folder names; we append this
    folder's name and pass to children so each placemark knows its full
    folder path.
    """
    heading = text_of(find_child(folder, "name")) or "(unnamed folder)"
    desc = strip_html(text_of(find_child(folder, "description")))
    folder_path = parent_path + [heading]
    chunks = [f"{'#' * level} {heading}"]
    if desc:
        chunks.append(desc)

    placemark_count = 0
    sub_stats = Counter()
    placemarks: list[str] = []
    for child in folder:
        ln = localname(child.tag)
        if ln == "Folder":
            sub_md, sub_s = render_folder(child, level + 1, folder_path)
            chunks.append(sub_md)
            sub_stats.update(sub_s)
        elif ln == "Placemark":
            pm_md, pm_s = render_placemark(child, level + 1, folder_path)
            placemarks.append(pm_md)
            placemark_count += pm_s["placemarks"]
            sub_stats.update(pm_s)

    if placemarks:
        chunks.append(f"_{placemark_count} placemark(s) in this folder._")
        chunks.append("")
        chunks.append("\n\n".join(placemarks))

    return "\n\n".join(chunks), dict(sub_stats)


def render_document(doc: ET.Element, basename: str) -> tuple[str, dict]:
    """Render the top-level <Document>: H1 + intro + nested folders + orphan placemarks."""
    title = basename
    parts = [f"# {title}"]
    doc_name = text_of(find_child(doc, "name"))
    doc_desc = strip_html(text_of(find_child(doc, "description")))
    if doc_name and doc_name != basename:
        parts.append(f"Source map: {doc_name}")
    if doc_desc:
        parts.append(doc_desc)

    stats: Counter = Counter()
    folders = find_children(doc, "Folder")
    orphan_pms = find_children(doc, "Placemark")

    # Coverage stats summary first (if any), so a quick scan finds the count.
    parts.append(
        f"_Source: KMZ export. Folders: {len(folders)}. "
        f"Top-level placemarks: {len(orphan_pms)}._"
    )

    for f in folders:
        f_md, f_stats = render_folder(f, level=2, parent_path=[])
        parts.append(f_md)
        stats.update(f_stats)

    if orphan_pms:
        parts.append("## Top-level placemarks (no folder)")
        for pm in orphan_pms:
            pm_md, pm_s = render_placemark(pm, level=3, folder_path=[])
            parts.append(pm_md)
            stats.update(pm_s)

    return "\n\n".join(parts), dict(stats)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def derive_output_path(kmz_path: Path) -> Path:
    """`<dir>/<basename>.kmz` -> `<dir>/<basename> - Subcontractor Coverage.md`."""
    stem = kmz_path.stem
    return kmz_path.with_name(f"{stem} - Subcontractor Coverage.md")


def find_kml_member(zf: zipfile.ZipFile) -> str:
    """Standard kmz holds doc.kml at the root; fall back to first .kml."""
    if "doc.kml" in zf.namelist():
        return "doc.kml"
    for n in zf.namelist():
        if n.lower().endswith(".kml"):
            return n
    raise FileNotFoundError("No .kml file inside KMZ archive.")


def convert(kmz_path: Path, out_path: Optional[Path] = None) -> tuple[Path, dict]:
    if not kmz_path.exists():
        raise FileNotFoundError(f"KMZ not found: {kmz_path}")
    if kmz_path.suffix.lower() != ".kmz":
        raise ValueError(f"Expected .kmz file, got: {kmz_path}")

    out_path = out_path or derive_output_path(kmz_path)

    with zipfile.ZipFile(kmz_path) as zf:
        kml_name = find_kml_member(zf)
        with zf.open(kml_name) as kml_f:
            kml_bytes = kml_f.read()

    root = ET.fromstring(kml_bytes)
    # Some KML files use kml > Document, some go straight to Document.
    doc = find_child(root, "Document")
    if doc is None and localname(root.tag) == "Document":
        doc = root
    if doc is None:
        raise ValueError("KML missing <Document> element.")

    md, stats = render_document(doc, basename=kmz_path.stem)
    out_path.write_text(md + "\n", encoding="utf-8")
    return out_path, stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert a Google-Earth KMZ to structured markdown."
    )
    ap.add_argument("kmz_path", help="Path to the .kmz file")
    ap.add_argument(
        "--out", default=None,
        help="Override output path (default: alongside input, .md extension)",
    )
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    src = Path(args.kmz_path)
    out = Path(args.out) if args.out else None
    out_path, stats = convert(src, out)

    line_count = out_path.read_text(encoding="utf-8").count("\n")
    print(f"OK  wrote {out_path}")
    print(f"    lines: {line_count}")
    print(f"    placemarks: {stats.get('placemarks', 0)}")
    print(
        f"    with_extdata: {stats.get('with_extdata', 0)}  "
        f"with_point: {stats.get('with_point', 0)}  "
        f"polygons: {stats.get('with_polygon', 0)}  "
        f"linestrings: {stats.get('with_linestring', 0)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
