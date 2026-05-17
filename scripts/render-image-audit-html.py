#!/usr/bin/env python3
"""
Generate an HTML audit page showing every product that has an image_url,
arranged as a thumbnail grid so Paul can visually verify the image
actually matches the product.

Each tile shows:
  - the thumbnail (loaded from image_url, lazy)
  - brand, sku, family_name
  - "View source page" link (image_source_url)
  - "Mark bad" link that copies an UPDATE SQL into the URL fragment

Group by brand, sort by sku within brand. Sticky brand headers.

Output: <repo>/data/.image-audit.html (open in a browser).
"""

from __future__ import annotations
import html
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg  # type: ignore
from psycopg.rows import dict_row  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / ".image-audit.html"


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


HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>bid-iq product image audit</title>
<style>
  :root { --fg:#1a1a1a; --muted:#666; --bd:#e2e2e2; --bg:#fafafa;
          --accent:#0a66c2; --bad:#c52e2e; --bad-bg:#fff1f1; }
  *,*::before,*::after { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         color:var(--fg); background:var(--bg); padding:0 24px 64px; }
  h1 { font-size:20px; margin:24px 0 4px; }
  .meta { color:var(--muted); margin-bottom:24px; }
  .toolbar { position:sticky; top:0; background:var(--bg); padding:12px 0;
             border-bottom:1px solid var(--bd); z-index:10;
             display:flex; gap:12px; align-items:center; }
  .toolbar input { font:14px/1.4 inherit; padding:6px 10px;
                   border:1px solid var(--bd); border-radius:6px; min-width:220px; }
  .brand-section { margin-top:24px; }
  .brand-header { position:sticky; top:55px; background:var(--bg); padding:8px 0 4px;
                  border-bottom:1px solid var(--bd); margin-bottom:12px;
                  font-weight:600; font-size:16px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));
          gap:16px; }
  .card { background:#fff; border:1px solid var(--bd); border-radius:8px;
          padding:10px; display:flex; flex-direction:column; min-height:300px; }
  .card.bad { background:var(--bad-bg); border-color:var(--bad); }
  .thumb { width:100%; aspect-ratio:1/1; object-fit:contain; background:#f0f0f0;
           border-radius:4px; margin-bottom:8px; }
  .thumb.broken { background:#fbe9e9; }
  .sku { font-weight:600; font-size:14px; word-break:break-all; }
  .family { color:var(--muted); font-size:12px; margin:2px 0 6px;
            display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
            overflow:hidden; }
  .links { margin-top:auto; display:flex; gap:8px; font-size:12px; padding-top:6px;
           border-top:1px solid var(--bd); }
  .links a { color:var(--accent); text-decoration:none; }
  .links a:hover { text-decoration:underline; }
  .flag-btn { background:none; border:none; color:var(--bad); cursor:pointer;
              padding:0; font:inherit; }
  .flag-btn:hover { text-decoration:underline; }
  .override { display:none; margin-top:6px; }
  .override input { width:100%; font:12px ui-monospace,Menlo,Consolas,monospace;
                    padding:4px 6px; border:1px solid var(--bd); border-radius:4px; }
  .override input::placeholder { color:#aaa; font-style:italic; }
  .card.bad .override { display:block; }
  .card.has-override { background:#fff8e6; border-color:#d6a300; }
  .copy-region { position:fixed; right:24px; bottom:24px; background:#222; color:#fff;
                 padding:16px; border-radius:8px; max-width:64ch; box-shadow:0 4px 24px rgba(0,0,0,0.2);
                 z-index:50; display:none; font-family:ui-monospace,Menlo,Consolas,monospace;
                 font-size:12px; line-height:1.5; }
  .copy-region.show { display:block; }
  .copy-region textarea { width:100%; height:160px; background:#111; color:#fff;
                          border:1px solid #444; border-radius:4px; padding:8px;
                          font:inherit; resize:vertical; }
  .copy-region .hint { color:#aaa; margin-bottom:8px; }
  .copy-region .tab-row { display:flex; gap:4px; margin-bottom:6px; }
  .copy-region .tab { background:#333; color:#bbb; border:0; padding:6px 10px; border-radius:4px 4px 0 0;
                      cursor:pointer; font:inherit; }
  .copy-region .tab.active { background:#444; color:#fff; }
  .copy-region button.btn { background:#444; color:#fff; border:0; padding:6px 12px;
                        border-radius:4px; cursor:pointer; margin-top:6px; margin-right:6px; }
  .copy-region button.btn:hover { background:#666; }
</style>
</head>
<body>
"""

TAIL = """\
<div class="copy-region" id="copyRegion">
  <div class="tab-row">
    <button class="tab active" id="tabSQL" onclick="showTab('sql')">SQL (clear &amp; rescrape)</button>
    <button class="tab" id="tabOver" onclick="showTab('over')">Overrides (Paul-supplied URLs)</button>
  </div>
  <div class="hint" id="hint">Flag products you want re-scraped, OR paste a better URL in the override field to give Claude a specific source.</div>
  <textarea id="copyText" readonly></textarea>
  <button class="btn" onclick="copyToClipboard()">Copy</button>
  <button class="btn" onclick="document.getElementById('copyRegion').classList.remove('show')">Close</button>
</div>
<script>
  const flaggedIds = new Set();
  const overrides = new Map();  // productId -> url
  let activeTab = 'sql';

  function flag(card, productId) {
    if (flaggedIds.has(productId)) {
      flaggedIds.delete(productId);
      overrides.delete(productId);
      card.classList.remove('has-override');
      const input = card.querySelector('.override input');
      if (input) input.value = '';
    } else {
      flaggedIds.add(productId);
    }
    card.classList.toggle('bad');
    updatePanel();
  }

  function setOverride(card, productId, url) {
    url = (url || '').trim();
    if (url) {
      overrides.set(productId, url);
      card.classList.add('has-override');
      if (!flaggedIds.has(productId)) {
        flaggedIds.add(productId);
        card.classList.add('bad');
      }
    } else {
      overrides.delete(productId);
      card.classList.remove('has-override');
    }
    updatePanel();
  }

  function showTab(t) {
    activeTab = t;
    document.getElementById('tabSQL').classList.toggle('active', t === 'sql');
    document.getElementById('tabOver').classList.toggle('active', t === 'over');
    updatePanel();
  }

  function updatePanel() {
    const region = document.getElementById('copyRegion');
    const txt = document.getElementById('copyText');
    const hint = document.getElementById('hint');
    if (flaggedIds.size === 0 && overrides.size === 0) {
      region.classList.remove('show');
      return;
    }
    region.classList.add('show');

    if (activeTab === 'sql') {
      // SQL tab: clear flagged-without-override rows so Claude can rescrape
      const idsToClear = Array.from(flaggedIds).filter(id => !overrides.has(id)).sort((a,b)=>a-b);
      hint.textContent = `${idsToClear.length} flagged-without-override product(s). Paste into Neon SQL editor:`;
      if (idsToClear.length === 0) {
        txt.value = `-- No flagged-without-override products. Switch to the Overrides tab.\n`;
      } else {
        txt.value =
          `-- Clear image_url on flagged products so the scraper picks them up again:\n` +
          `UPDATE products SET image_url=NULL, image_source_url=NULL, image_fetched_at=NULL\n` +
          `WHERE id IN (${idsToClear.join(',')});\n` +
          `-- ${idsToClear.length} flagged-without-override product(s)`;
      }
    } else {
      // Override tab: id<TAB>url pairs to feed apply-image-overrides.py
      hint.textContent = `${overrides.size} override(s). Save these lines to a file or paste to Claude:`;
      if (overrides.size === 0) {
        txt.value = `# No overrides supplied. Paste a better URL into any flagged tile's input.\n`;
      } else {
        const lines = ['# bid-iq image overrides — feed to scripts/apply-image-overrides.py',
                       '# Format: <product_id> <url>'];
        const pairs = Array.from(overrides.entries()).sort((a,b)=>a[0]-b[0]);
        for (const [id, url] of pairs) lines.push(`${id} ${url}`);
        txt.value = lines.join('\\n');
      }
    }
  }

  function copyToClipboard() {
    const txt = document.getElementById('copyText');
    txt.select(); document.execCommand('copy');
  }

  document.querySelectorAll('img.thumb').forEach(img => {
    img.addEventListener('error', () => img.classList.add('broken'));
  });

  document.getElementById('q').addEventListener('input', e => {
    const v = e.target.value.toLowerCase().trim();
    document.querySelectorAll('.card').forEach(c => {
      const t = c.dataset.search;
      c.style.display = !v || t.includes(v) ? '' : 'none';
    });
  });

  // Wire up override inputs
  document.querySelectorAll('.override input').forEach(inp => {
    inp.addEventListener('input', e => {
      const card = e.target.closest('.card');
      const pid = parseInt(card.dataset.productId, 10);
      setOverride(card, pid, e.target.value);
    });
  });
</script>
</body>
</html>
"""


def render(rows: list[dict]) -> str:
    out = [HEAD]
    out.append(f'<h1>Product image audit</h1>')
    out.append(
        f'<div class="meta">{len(rows)} products with images &middot; generated '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M")} &middot; click "flag bad" on any tile '
        f'that\'s wrong, then copy the SQL at the bottom-right to clear them and re-run '
        f'<code>scripts/populate-product-images-v2.py --force</code>.</div>'
    )
    out.append(
        '<div class="toolbar">'
        '<input id="q" type="text" placeholder="filter by brand / sku / family name..."/>'
        '</div>'
    )
    by_brand: dict[str, list[dict]] = {}
    for r in rows:
        by_brand.setdefault(r["brand"], []).append(r)
    for brand in sorted(by_brand):
        plist = by_brand[brand]
        out.append(f'<div class="brand-section">')
        out.append(f'<div class="brand-header">{html.escape(brand)} &middot; {len(plist)}</div>')
        out.append('<div class="grid">')
        for r in plist:
            sku = r["sku"] or ""
            fam = r["family_name"] or ""
            src = r["image_source_url"] or ""
            img = r["image_url"] or ""
            search_blob = f"{brand} {sku} {fam}".lower()
            out.append(
                f'<div class="card" data-search="{html.escape(search_blob)}" data-product-id="{r["id"]}">'
                f'<img class="thumb" loading="lazy" referrerpolicy="no-referrer" src="{html.escape(img)}" alt="{html.escape(sku)}">'
                f'<div class="sku">{html.escape(sku)}</div>'
                f'<div class="family">{html.escape(fam)}</div>'
                f'<div class="links">'
                f'<a href="{html.escape(src)}" target="_blank" rel="noopener">source page &#x2197;</a>'
                f'<a href="{html.escape(img)}" target="_blank" rel="noopener">image &#x2197;</a>'
                f'<button class="flag-btn" onclick="flag(this.closest(\'.card\'), {r["id"]})">flag bad</button>'
                f'</div>'
                f'<div class="override">'
                f'<input type="url" placeholder="paste better page URL or image URL..." />'
                f'</div>'
                f'</div>'
            )
        out.append('</div></div>')
    out.append(TAIL)
    return "".join(out)


def main() -> int:
    conn = psycopg.connect(load_db_url(), autocommit=True)
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT p.id, p.sku, p.family_name, p.image_url, p.image_source_url,
               lower(b.name) AS brand
        FROM products p JOIN brands b ON b.id = p.brand_id
        WHERE p.source <> 'svi-catalog' AND p.image_url IS NOT NULL
        ORDER BY lower(b.name), p.sku
    """)
    rows = cur.fetchall()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render(rows), encoding="utf-8")
    print(f"Wrote audit page: {OUT_PATH}")
    print(f"  {len(rows)} imaged products")
    print(f"  Open in browser: file:///{OUT_PATH.as_posix()}")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
