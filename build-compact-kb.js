// Build a compact KB that fits within ~150K tokens (600KB)
// Reads from individual extractions, strips aggressively, deduplicates

const fs = require('fs');
const path = require('path');

// Load all sources
const entries = [];

// Individual extractions
const indDir = path.join(__dirname, 'kb_extracted', 'individual');
for (const f of fs.readdirSync(indDir).filter(f => f.endsWith('.json'))) {
  try {
    entries.push(JSON.parse(fs.readFileSync(path.join(indDir, f), 'utf-8')));
  } catch (e) {}
}

// Original KB
const kbDir = path.join(__dirname, 'kb_output');
for (const f of fs.readdirSync(kbDir).filter(f => f.endsWith('.json'))) {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(kbDir, f), 'utf-8'));
    d.source_file = d.source_file || f;
    entries.push(d);
  } catch (e) {}
}

// Operation manuals
const opsFile = path.join(__dirname, 'kb_extracted', 'operation-manuals.json');
if (fs.existsSync(opsFile)) {
  const ops = JSON.parse(fs.readFileSync(opsFile, 'utf-8'));
  entries.push(...ops);
}

console.log('Total raw entries:', entries.length);

// Strip to essential fields only
function compact(e) {
  const out = {};
  if (e.model && e.model !== 'Unknown' && e.model !== 'N/A') out.m = e.model;
  if (e.variant && e.variant !== 'N/A') out.v = e.variant;
  if (e.category) out.cat = e.category;
  if (e.product_type && e.product_type !== 'N/A') out.type = e.product_type;
  if (e.capacity && e.capacity !== 'N/A') out.cap = e.capacity;

  // Dimensions - flatten to key strings
  if (e.dimensions && typeof e.dimensions === 'object') {
    const dims = {};
    for (const [k, v] of Object.entries(e.dimensions)) {
      if (v && v !== 'N/A' && v !== 'Not specified' && v !== null && v !== '') {
        if (typeof v === 'object') {
          // Flatten nested dimension objects
          for (const [k2, v2] of Object.entries(v)) {
            if (v2 && v2 !== 'N/A' && v2 !== 'Not specified') dims[k + '_' + k2] = v2;
          }
        } else {
          dims[k] = v;
        }
      }
    }
    if (Object.keys(dims).length > 0) out.dims = dims;
  }

  if (e.weight && e.weight !== 'N/A') out.wt = e.weight;

  // Specs - flatten
  if (e.specifications && typeof e.specifications === 'object') {
    const specs = {};
    for (const [k, v] of Object.entries(e.specifications)) {
      if (v && v !== 'N/A' && v !== 'Not specified' && v !== null && v !== '') {
        specs[k] = v;
      }
    }
    if (Object.keys(specs).length > 0) out.specs = specs;
  }

  // Installation requirements
  if (e.installation_requirements && typeof e.installation_requirements === 'object') {
    const inst = {};
    for (const [k, v] of Object.entries(e.installation_requirements)) {
      if (v && v !== 'N/A' && v !== 'Not specified' && v !== null && v !== '') {
        inst[k] = v;
      }
    }
    if (Object.keys(inst).length > 0) out.install = inst;
  }

  // Arrays - keep only non-empty
  const arrFields = {
    features: 'feat', certifications: 'certs', safety_features: 'safety',
    optional_equipment: 'opts', notes: 'notes', compatible_models: 'compat'
  };
  for (const [src, dst] of Object.entries(arrFields)) {
    if (Array.isArray(e[src])) {
      const filtered = e[src].filter(v => v && v !== 'N/A' && v !== 'Not specified');
      if (filtered.length > 0) out[dst] = filtered;
    }
  }

  if (e.operation_summary && e.operation_summary !== 'N/A') out.ops = e.operation_summary;

  return out;
}

// Deduplicate by model+variant
const seen = new Map();
const deduped = [];

for (const entry of entries) {
  const c = compact(entry);
  if (Object.keys(c).length <= 2) continue; // Skip nearly-empty entries

  const model = (c.m || '').toLowerCase();
  const variant = (c.v || '').toLowerCase();
  const key = `${model}|${variant}`;

  if (seen.has(key) && model && model !== 'unknown') {
    // Merge: keep entry with more data
    const existing = seen.get(key);
    const existingSize = JSON.stringify(existing).length;
    const newSize = JSON.stringify(c).length;
    if (newSize > existingSize) {
      const idx = deduped.indexOf(existing);
      if (idx !== -1) deduped[idx] = c;
      seen.set(key, c);
    }
  } else {
    seen.set(key, c);
    deduped.push(c);
  }
}

console.log('After dedup + filter:', deduped.length, 'entries');

// Write
const outFile = path.join(__dirname, 'app', 'api', 'ask', 'knowledge-base.json');
fs.writeFileSync(outFile, JSON.stringify(deduped));

const sizeKB = (fs.statSync(outFile).size / 1024).toFixed(0);
const estimatedTokens = Math.round(fs.statSync(outFile).size / 4);
console.log('Size:', sizeKB, 'KB (~' + estimatedTokens + ' tokens)');

// Category breakdown
const cats = {};
for (const e of deduped) {
  const c = e.cat || 'unknown';
  cats[c] = (cats[c] || 0) + 1;
}
console.log('\nBy category:');
Object.entries(cats).sort((a, b) => b[1] - a[1]).forEach(([k, v]) => console.log('  ' + k + ': ' + v));
