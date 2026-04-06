/**
 * Lunati Garage (VSG Dover) Salesforce Knowledge Base PDF Scraper
 *
 * USAGE:
 * 1. Log into https://lunatigarage.vsgdover.com/ in Chrome
 * 2. Open DevTools (F12) -> Console tab
 * 3. Paste the STEP 1 script first to discover all PDFs
 * 4. Review the output, then paste STEP 2 to download them
 *
 * NOTE: This is designed for Salesforce Experience Cloud / Community sites
 * which typically use Aura/LWC components.
 */

// ============================================================
// STEP 1: DISCOVERY - Paste this into the browser console first
// ============================================================
// This will crawl the knowledge base and find all PDF links.
// It outputs a JSON array of {title, url} objects.
// ============================================================

(async function discoverPDFs() {
  const DELAY = 1500; // ms between requests to avoid rate limiting
  const visited = new Set();
  const pdfLinks = [];
  const articleLinks = [];
  const errors = [];

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function log(msg) { console.log(`%c[PDF Scraper] ${msg}`, 'color: #00aa00; font-weight: bold;'); }
  function warn(msg) { console.warn(`[PDF Scraper] ${msg}`); }

  // Collect all links from the current page
  function collectLinks() {
    const links = [];
    document.querySelectorAll('a[href]').forEach(a => {
      const href = a.href;
      const text = (a.textContent || '').trim();
      links.push({ href, text });
    });
    return links;
  }

  // Check if a URL points to a PDF
  function isPdfUrl(url) {
    const lower = url.toLowerCase();
    return lower.endsWith('.pdf') ||
           lower.includes('/sfc/servlet.shepherd/document/download/') ||
           lower.includes('/sfc/servlet.shepherd/version/download/') ||
           lower.includes('ContentDocument') ||
           lower.includes('/servlet/servlet.FileDownload');
  }

  // Check if URL is an internal article/page link
  function isInternalLink(url) {
    const base = window.location.origin;
    return url.startsWith(base) || url.startsWith('/');
  }

  // Get the base URL
  const BASE = window.location.origin;

  log('Starting PDF discovery...');
  log(`Base URL: ${BASE}`);

  // Phase 1: Collect all links from the current page (home/landing)
  log('Phase 1: Scanning current page for links...');

  const homeLinks = collectLinks();
  log(`Found ${homeLinks.length} links on current page`);

  // Separate into categories
  homeLinks.forEach(link => {
    if (isPdfUrl(link.href)) {
      pdfLinks.push({ title: link.text, url: link.href, source: 'home' });
    } else if (isInternalLink(link.href) && !visited.has(link.href)) {
      articleLinks.push(link);
    }
  });

  log(`Found ${pdfLinks.length} direct PDF links`);
  log(`Found ${articleLinks.length} internal links to explore`);

  // Phase 2: Visit each internal link to find PDFs
  log('Phase 2: Exploring internal links for PDFs...');

  // Filter to likely content pages (skip auth, profile, etc.)
  const skipPatterns = [
    '/login', '/logout', '/profile', '/settings',
    'javascript:', '#', 'mailto:', 'tel:',
    '/secur/', '/setup/', '/_ui/'
  ];

  const pagesToVisit = articleLinks.filter(link => {
    const lower = link.href.toLowerCase();
    return !skipPatterns.some(p => lower.includes(p));
  });

  log(`Will explore ${pagesToVisit.length} pages (after filtering)`);

  // Use fetch to visit pages and parse their HTML for PDF links
  let explored = 0;
  for (const page of pagesToVisit) {
    if (visited.has(page.href)) continue;
    visited.add(page.href);
    explored++;

    try {
      log(`[${explored}/${pagesToVisit.length}] Fetching: ${page.text || page.href}`);

      const resp = await fetch(page.href, { credentials: 'include' });
      if (!resp.ok) {
        warn(`HTTP ${resp.status} for ${page.href}`);
        continue;
      }

      const html = await resp.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');

      // Find PDF links in this page
      doc.querySelectorAll('a[href]').forEach(a => {
        let href = a.getAttribute('href');
        if (!href) return;

        // Make absolute
        if (href.startsWith('/')) href = BASE + href;

        const text = (a.textContent || '').trim();

        if (isPdfUrl(href)) {
          const existing = pdfLinks.find(p => p.url === href);
          if (!existing) {
            pdfLinks.push({
              title: text || page.text || 'Unknown',
              url: href,
              source: page.href
            });
            log(`  Found PDF: ${text || href}`);
          }
        }

        // Also look for nested article pages we haven't visited
        if (isInternalLink(href) && !visited.has(href) &&
            !skipPatterns.some(p => href.toLowerCase().includes(p))) {
          // Add to queue but don't recurse too deep
          if (explored < 500) { // safety limit
            pagesToVisit.push({ href, text });
          }
        }
      });

      // Also check for Salesforce-specific patterns
      // Look for ContentDocument IDs in the HTML
      const contentDocPattern = /069[A-Za-z0-9]{12,15}/g;
      const contentVersionPattern = /068[A-Za-z0-9]{12,15}/g;

      let match;
      while ((match = contentDocPattern.exec(html)) !== null) {
        const docId = match[0];
        const pdfUrl = `${BASE}/sfc/servlet.shepherd/document/download/${docId}`;
        if (!pdfLinks.find(p => p.url === pdfUrl)) {
          pdfLinks.push({
            title: `ContentDocument ${docId}`,
            url: pdfUrl,
            source: page.href
          });
          log(`  Found ContentDocument: ${docId}`);
        }
      }

      while ((match = contentVersionPattern.exec(html)) !== null) {
        const verId = match[0];
        const pdfUrl = `${BASE}/sfc/servlet.shepherd/version/download/${verId}`;
        if (!pdfLinks.find(p => p.url === pdfUrl)) {
          pdfLinks.push({
            title: `ContentVersion ${verId}`,
            url: pdfUrl,
            source: page.href
          });
          log(`  Found ContentVersion: ${verId}`);
        }
      }

      // Look for iframe or embed sources that might be PDFs
      doc.querySelectorAll('iframe[src], embed[src], object[data]').forEach(el => {
        let src = el.getAttribute('src') || el.getAttribute('data');
        if (!src) return;
        if (src.startsWith('/')) src = BASE + src;
        if (isPdfUrl(src) || src.toLowerCase().includes('pdf')) {
          if (!pdfLinks.find(p => p.url === src)) {
            pdfLinks.push({ title: `Embedded: ${page.text}`, url: src, source: page.href });
            log(`  Found embedded PDF: ${src}`);
          }
        }
      });

      await sleep(DELAY);
    } catch (err) {
      errors.push({ page: page.href, error: err.message });
      warn(`Error fetching ${page.href}: ${err.message}`);
    }
  }

  // Phase 3: Report results
  log('');
  log('========================================');
  log(`DISCOVERY COMPLETE`);
  log(`Pages explored: ${explored}`);
  log(`PDFs found: ${pdfLinks.length}`);
  log(`Errors: ${errors.length}`);
  log('========================================');

  if (pdfLinks.length > 0) {
    console.log('\nPDF Links found:');
    console.table(pdfLinks.map(p => ({ title: p.title, url: p.url })));
  }

  // Store results globally for the download step
  window.__LUNATI_PDFS = pdfLinks;
  window.__LUNATI_ERRORS = errors;

  log('');
  log('Results stored in window.__LUNATI_PDFS');
  log('Copy the data with: copy(JSON.stringify(window.__LUNATI_PDFS, null, 2))');
  log('Then paste STEP 2 to download all PDFs.');

  return pdfLinks;
})();


// ============================================================
// STEP 2: DOWNLOAD - Paste this AFTER step 1 completes
// ============================================================
// This downloads all discovered PDFs. They will go to your
// browser's default download directory.
// ============================================================

/*
(async function downloadPDFs() {
  const DELAY = 2000; // ms between downloads
  const pdfs = window.__LUNATI_PDFS;

  if (!pdfs || pdfs.length === 0) {
    console.error('No PDFs found! Run STEP 1 first.');
    return;
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function log(msg) { console.log(`%c[PDF Downloader] ${msg}`, 'color: #0000aa; font-weight: bold;'); }
  function sanitize(name) {
    return name.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_').substring(0, 200);
  }

  log(`Starting download of ${pdfs.length} PDFs...`);
  log('Files will save to your browser default download directory.');
  log('Move them to: C:\\Users\\Paul\\bid-iq\\data\\product_data\\rotary\\');

  let downloaded = 0;
  let failed = 0;

  for (const pdf of pdfs) {
    try {
      log(`[${downloaded + failed + 1}/${pdfs.length}] Downloading: ${pdf.title}`);

      const resp = await fetch(pdf.url, { credentials: 'include' });
      if (!resp.ok) {
        console.warn(`  HTTP ${resp.status} for ${pdf.url}`);
        failed++;
        continue;
      }

      // Get filename from Content-Disposition header or generate one
      let filename;
      const cd = resp.headers.get('content-disposition');
      if (cd) {
        const match = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (match) filename = match[1].replace(/['"]/g, '');
      }
      if (!filename) {
        filename = sanitize(pdf.title || 'document') + '.pdf';
      }

      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      downloaded++;
      log(`  Saved: ${filename} (${(blob.size / 1024).toFixed(1)} KB)`);

      await sleep(DELAY);
    } catch (err) {
      console.error(`  Error downloading ${pdf.title}: ${err.message}`);
      failed++;
    }
  }

  log('');
  log('========================================');
  log(`DOWNLOAD COMPLETE`);
  log(`Downloaded: ${downloaded}`);
  log(`Failed: ${failed}`);
  log('========================================');
  log('');
  log('Next: Move downloaded files to:');
  log('C:\\Users\\Paul\\bid-iq\\data\\product_data\\rotary\\');
})();
*/
