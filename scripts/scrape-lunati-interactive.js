/**
 * Lunati Garage Interactive PDF Scraper
 *
 * This version works with Salesforce's dynamic rendering by interacting
 * with the live DOM instead of fetching raw HTML.
 *
 * USAGE:
 * 1. Log into https://lunatigarage.vsgdover.com/ in Chrome
 * 2. Open DevTools (F12) -> Console tab
 * 3. Paste STEP 0 first to scan the current page structure
 * 4. Based on what you see, use STEP 1 to crawl, STEP 2 to download
 */

// ============================================================
// STEP 0: PAGE SCANNER - Paste this first to see what's on the page
// ============================================================

(function scanPage() {
  const log = (msg) => console.log(`%c[Scanner] ${msg}`, 'color: #aa5500; font-weight: bold;');

  log('=== PAGE STRUCTURE SCAN ===');
  log(`URL: ${window.location.href}`);
  log(`Title: ${document.title}`);

  // Find all links
  const allLinks = [...document.querySelectorAll('a[href]')];
  log(`\nTotal links on page: ${allLinks.length}`);

  // Categorize links
  const categories = { pdf: [], internal: [], external: [], anchor: [] };

  allLinks.forEach(a => {
    const href = a.href;
    const text = (a.textContent || '').trim().substring(0, 80);
    const lower = href.toLowerCase();

    if (lower.endsWith('.pdf') || lower.includes('download') ||
        lower.includes('servlet.shepherd') || lower.includes('ContentDocument')) {
      categories.pdf.push({ text, href });
    } else if (href.startsWith(window.location.origin)) {
      categories.internal.push({ text, href });
    } else if (href.startsWith('#')) {
      categories.anchor.push({ text, href });
    } else {
      categories.external.push({ text, href });
    }
  });

  log(`\n--- PDF/Download Links (${categories.pdf.length}) ---`);
  categories.pdf.forEach(l => log(`  ${l.text} -> ${l.href}`));

  log(`\n--- Internal Links (${categories.internal.length}) ---`);
  // Group by path pattern
  const pathGroups = {};
  categories.internal.forEach(l => {
    try {
      const path = new URL(l.href).pathname.split('/').slice(0, 3).join('/');
      if (!pathGroups[path]) pathGroups[path] = [];
      pathGroups[path].push(l);
    } catch (e) {}
  });

  Object.entries(pathGroups).forEach(([path, links]) => {
    log(`  ${path} (${links.length} links)`);
    links.slice(0, 5).forEach(l => log(`    ${l.text} -> ${l.href}`));
    if (links.length > 5) log(`    ... and ${links.length - 5} more`);
  });

  // Look for Salesforce-specific elements
  log('\n--- Salesforce Components ---');

  const auraComponents = document.querySelectorAll('[data-component-id], [data-aura-rendered-by]');
  log(`Aura components: ${auraComponents.length}`);

  const lwcComponents = document.querySelectorAll('[lwc\\:host], [is]');
  log(`LWC components: ${lwcComponents.length}`);

  // Look for navigation elements
  const navElements = document.querySelectorAll('nav, [role="navigation"], .slds-nav, .comm-navigation');
  log(`Navigation elements: ${navElements.length}`);
  navElements.forEach(nav => {
    const navLinks = nav.querySelectorAll('a');
    log(`  Nav with ${navLinks.length} links:`);
    navLinks.forEach(a => log(`    ${(a.textContent || '').trim()} -> ${a.href}`));
  });

  // Look for knowledge article elements
  const articleElements = document.querySelectorAll(
    '[class*="article"], [class*="knowledge"], [class*="document"], ' +
    '[class*="Article"], [class*="Knowledge"], [class*="Document"], ' +
    '.cKnowledgeArticle, .knowledgeArticle'
  );
  log(`\nArticle-related elements: ${articleElements.length}`);

  // Look for clickable items that might lead to PDFs
  const buttons = document.querySelectorAll('button, [role="button"], .slds-button');
  log(`\nButtons: ${buttons.length}`);
  buttons.forEach(b => {
    const text = (b.textContent || '').trim();
    if (text.toLowerCase().includes('download') || text.toLowerCase().includes('pdf') ||
        text.toLowerCase().includes('view') || text.toLowerCase().includes('document')) {
      log(`  Button: "${text}"`);
    }
  });

  // Look for file/attachment related elements
  const fileElements = document.querySelectorAll(
    '[class*="file"], [class*="attach"], [class*="File"], [class*="Attach"], ' +
    '.fileCardBody, .slds-file, .contentDocument'
  );
  log(`\nFile/attachment elements: ${fileElements.length}`);

  // Look for iframes
  const iframes = document.querySelectorAll('iframe');
  log(`\nIframes: ${iframes.length}`);
  iframes.forEach(f => log(`  ${f.src || f.getAttribute('data-src') || 'no src'}`));

  // Look for search functionality
  const searchInputs = document.querySelectorAll(
    'input[type="search"], input[placeholder*="search" i], ' +
    '[class*="search" i] input, .slds-input'
  );
  log(`\nSearch inputs: ${searchInputs.length}`);

  // Dump unique class names that might hint at structure
  const interestingClasses = new Set();
  document.querySelectorAll('*').forEach(el => {
    const cls = el.className;
    if (typeof cls === 'string') {
      cls.split(/\s+/).forEach(c => {
        if (c.match(/article|knowledge|document|file|download|pdf|content|product|category|catalog/i)) {
          interestingClasses.add(c);
        }
      });
    }
  });
  log(`\nInteresting CSS classes:`);
  interestingClasses.forEach(c => log(`  .${c}`));

  log('\n=== SCAN COMPLETE ===');
  log('Review the output above, then share it with Claude for a tailored scraping script.');
  log('You can also copy everything with: right-click console -> Save as...');

  return {
    pdfLinks: categories.pdf,
    internalLinks: categories.internal,
    pathGroups,
    totalLinks: allLinks.length
  };
})();


// ============================================================
// STEP 1: CLICK-THROUGH CRAWLER (for dynamically loaded content)
// ============================================================
// Uncomment and paste this if the site loads content dynamically.
// It will click through navigation items and collect PDF links.
// ============================================================

/*
(async function clickCrawler() {
  const DELAY = 2000;
  const pdfLinks = [];
  const visited = new Set();

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function log(msg) { console.log(`%c[Crawler] ${msg}`, 'color: #00aa00; font-weight: bold;'); }

  // Find all navigation/category links
  const navLinks = document.querySelectorAll('nav a, [role="navigation"] a, .slds-nav a');

  log(`Found ${navLinks.length} navigation links`);

  // For each nav item, click it and wait for content to load
  for (const link of navLinks) {
    const text = (link.textContent || '').trim();
    const href = link.href;

    if (visited.has(href)) continue;
    visited.add(href);

    log(`Visiting: ${text}`);

    // Navigate to the page
    window.location.href = href;

    // Wait for page to load
    await new Promise(resolve => {
      if (document.readyState === 'complete') {
        setTimeout(resolve, DELAY);
      } else {
        window.addEventListener('load', () => setTimeout(resolve, DELAY), { once: true });
      }
    });

    // Scan for PDFs on this page
    document.querySelectorAll('a[href]').forEach(a => {
      const aHref = a.href;
      if (aHref.toLowerCase().endsWith('.pdf') ||
          aHref.includes('servlet.shepherd') ||
          aHref.includes('download')) {
        const aText = (a.textContent || '').trim();
        if (!pdfLinks.find(p => p.url === aHref)) {
          pdfLinks.push({ title: aText || text, url: aHref, source: href });
          log(`  Found PDF: ${aText || aHref}`);
        }
      }
    });
  }

  window.__LUNATI_PDFS = pdfLinks;
  log(`\nDone! Found ${pdfLinks.length} PDFs total.`);
  console.table(pdfLinks);
})();
*/


// ============================================================
// STEP 2: BATCH DOWNLOAD (same as other script)
// ============================================================

/*
(async function downloadPDFs() {
  const DELAY = 2000;
  const pdfs = window.__LUNATI_PDFS;

  if (!pdfs || pdfs.length === 0) {
    console.error('No PDFs found! Run discovery first.');
    return;
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function log(msg) { console.log(`%c[Downloader] ${msg}`, 'color: #0000aa; font-weight: bold;'); }
  function sanitize(name) {
    return name.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_').substring(0, 200);
  }

  log(`Downloading ${pdfs.length} PDFs...`);

  let ok = 0, fail = 0;

  for (const pdf of pdfs) {
    try {
      log(`[${ok + fail + 1}/${pdfs.length}] ${pdf.title}`);

      const resp = await fetch(pdf.url, { credentials: 'include' });
      if (!resp.ok) { console.warn(`  HTTP ${resp.status}`); fail++; continue; }

      let filename;
      const cd = resp.headers.get('content-disposition');
      if (cd) {
        const m = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (m) filename = m[1].replace(/['"]/g, '');
      }
      if (!filename) filename = sanitize(pdf.title || 'document') + '.pdf';

      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(a.href);

      ok++;
      log(`  OK: ${filename} (${(blob.size / 1024).toFixed(1)} KB)`);
      await sleep(DELAY);
    } catch (e) {
      console.error(`  Error: ${e.message}`);
      fail++;
    }
  }

  log(`\nDone! Downloaded: ${ok}, Failed: ${fail}`);
  log('Move files to: C:\\Users\\Paul\\bid-iq\\data\\product_data\\rotary\\');
})();
*/
