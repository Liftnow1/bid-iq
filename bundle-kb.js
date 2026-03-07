const fs = require('fs');
const path = require('path');

const allEntries = [];
const seen = new Set();

// 1. Load individual extractions
const indDir = path.join(__dirname, 'kb_extracted', 'individual');
const indFiles = fs.readdirSync(indDir).filter(f => f.endsWith('.json'));
for (const f of indFiles) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(indDir, f), 'utf-8'));
    const key = (data.source_file || f).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    allEntries.push(data);
  } catch (e) { console.error('Skip', f, e.message); }
}
console.log('Individual extractions:', indFiles.length);

// 2. Load operation manuals JSON
const opsFile = path.join(__dirname, 'kb_extracted', 'operation-manuals.json');
if (fs.existsSync(opsFile)) {
  const ops = JSON.parse(fs.readFileSync(opsFile, 'utf-8'));
  let added = 0;
  for (const entry of ops) {
    const key = (entry.source_file || '').toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    allEntries.push(entry);
    added++;
  }
  console.log('Operation manual stubs added:', added);
}

// 3. Load original KB entries for any not already covered
const kbDir = path.join(__dirname, 'kb_output');
if (fs.existsSync(kbDir)) {
  const kbFiles = fs.readdirSync(kbDir).filter(f => f.endsWith('.json'));
  let added = 0;
  for (const f of kbFiles) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(kbDir, f), 'utf-8'));
      data.source_file = data.source_file || f;
      data._source_category = 'original-kb';
      const key = f.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      allEntries.push(data);
      added++;
    } catch (e) {}
  }
  console.log('Original KB entries added:', added);
}

// Write bundled KB
const outFile = path.join(__dirname, 'app', 'api', 'ask', 'knowledge-base.json');
fs.writeFileSync(outFile, JSON.stringify(allEntries, null, 2));

const sizeKB = (fs.statSync(outFile).size / 1024).toFixed(0);
console.log('\nTotal KB entries:', allEntries.length);
console.log('Output size:', sizeKB, 'KB');

// Category breakdown
const cats = {};
for (const e of allEntries) {
  const c = e.category || e._source_category || 'unknown';
  cats[c] = (cats[c] || 0) + 1;
}
console.log('\nBy category:');
Object.entries(cats).sort((a, b) => b[1] - a[1]).forEach(([k, v]) => console.log('  ' + k + ': ' + v));
