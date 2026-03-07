#!/usr/bin/env node

// Extract structured data from Mohawk Lifts PDFs using Claude API
// Processes PDFs in parallel batches

const Anthropic = require("@anthropic-ai/sdk");
const fs = require("fs");
const path = require("path");

const client = new Anthropic.default();

const EXTRACT_PROMPT = `Extract ALL product data from this Mohawk Lifts document into structured JSON. Be thorough and precise.

Return a JSON object with these fields (use "N/A" for missing string fields, [] for missing arrays):
{
  "source_file": "(I'll fill this in)",
  "category": "four-post-lift | two-post-lift | parallelogram-lift | mobile-column-lift | vertical-rise-lift | specialty-item | operation-manual | accessory | slab-requirements | truck-data",
  "document_type": "spec-sheet | installation-drawing | operation-manual | data-sheet | parts-list | brochure",
  "model": "model name/number",
  "variant": "specific variant if applicable",
  "product_type": "description of what this product is",
  "capacity": "rated capacity with units",
  "dimensions": {}, // ALL dimensions mentioned - heights, widths, lengths, clearances, pit sizes, etc. Use exact values with units
  "weight": "shipping/operating weight if mentioned",
  "installation_requirements": {}, // power, slab, pit, anchors, electrical, air supply, etc.
  "specifications": {}, // ALL technical specs - motor, hydraulic, speed, pressure, voltage, phases, etc.
  "features": [], // listed features and capabilities
  "optional_equipment": [], // optional accessories/add-ons
  "parts_list": [], // part numbers with descriptions if listed
  "certifications": [], // ALI, ANSI, ETL, etc.
  "safety_features": [], // locks, safeties, shutoffs, etc.
  "notes": [], // important notes, warnings, disclaimers
  "compatible_models": [], // what other models this works with (for accessories)
  "operation_summary": "" // for operation manuals, key operating procedures
}

Extract EVERY number, dimension, specification, and detail. Do not summarize or skip data.
Return ONLY valid JSON, no markdown fences.`;

async function extractPDF(pdfPath) {
  const bytes = fs.readFileSync(pdfPath);
  const base64 = bytes.toString("base64");
  const fileName = path.basename(pdfPath);
  const category = path.basename(path.dirname(pdfPath));

  try {
    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 8192,
      messages: [
        {
          role: "user",
          content: [
            {
              type: "document",
              source: {
                type: "base64",
                media_type: "application/pdf",
                data: base64,
              },
            },
            {
              type: "text",
              text: EXTRACT_PROMPT,
            },
          ],
        },
      ],
    });

    const text = response.content.find((b) => b.type === "text")?.text;
    if (!text) throw new Error("No text response");

    // Try to parse, handle potential JSON in markdown fences
    let jsonStr = text.trim();
    if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr.replace(/^```json?\n?/, "").replace(/\n?```$/, "");
    }

    const data = JSON.parse(jsonStr);
    data.source_file = fileName;
    data._source_category = category;
    return data;
  } catch (err) {
    console.error(`FAILED: ${fileName} - ${err.message}`);
    return {
      source_file: fileName,
      _source_category: category,
      _error: err.message,
      category: "unknown",
      document_type: "unknown",
      model: fileName.replace(".pdf", ""),
    };
  }
}

async function processBatch(files, concurrency = 5) {
  const results = [];
  for (let i = 0; i < files.length; i += concurrency) {
    const batch = files.slice(i, i + concurrency);
    const batchNum = Math.floor(i / concurrency) + 1;
    const totalBatches = Math.ceil(files.length / concurrency);
    console.log(
      `Batch ${batchNum}/${totalBatches} (${batch.length} files)...`
    );

    const batchResults = await Promise.all(batch.map((f) => extractPDF(f)));
    results.push(...batchResults);

    // Rate limit pause between batches
    if (i + concurrency < files.length) {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  return results;
}

async function main() {
  const dataDir = path.join(__dirname, "data", "product_data", "mohawk");
  const outputDir = path.join(__dirname, "kb_output");
  const outputFile = path.join(
    __dirname,
    "app",
    "api",
    "ask",
    "knowledge-base.json"
  );

  // Find all PDFs
  const categories = fs.readdirSync(dataDir).filter((d) => {
    const p = path.join(dataDir, d);
    return fs.statSync(p).isDirectory();
  });

  let allPDFs = [];
  for (const cat of categories) {
    const catDir = path.join(dataDir, cat);
    const pdfs = fs
      .readdirSync(catDir)
      .filter((f) => f.endsWith(".pdf"))
      .map((f) => path.join(catDir, f));
    allPDFs.push(...pdfs);
    console.log(`${cat}: ${pdfs.length} PDFs`);
  }

  console.log(`\nTotal: ${allPDFs.length} PDFs to process\n`);

  // Also include existing KB JSONs that aren't from PDF extraction
  const existingKB = [];
  if (fs.existsSync(outputDir)) {
    const existingFiles = fs
      .readdirSync(outputDir)
      .filter((f) => f.endsWith(".json"));
    for (const f of existingFiles) {
      const data = JSON.parse(
        fs.readFileSync(path.join(outputDir, f), "utf-8")
      );
      data.source_file = data.source_file || f;
      data._source_category = data._source_category || "existing-kb";
      existingKB.push(data);
    }
    console.log(`Existing KB entries: ${existingKB.length}\n`);
  }

  // Process all PDFs
  const extracted = await processBatch(allPDFs, 5);

  // Combine with existing KB, deduplicating by source_file
  const allData = [...existingKB];
  const existingFiles = new Set(existingKB.map((e) => e.source_file));
  for (const entry of extracted) {
    if (!existingFiles.has(entry.source_file)) {
      allData.push(entry);
    }
  }

  // Write bundled KB
  fs.writeFileSync(outputFile, JSON.stringify(allData, null, 2));
  console.log(
    `\nDone! ${allData.length} total KB entries written to ${outputFile}`
  );

  // Stats
  const errors = extracted.filter((e) => e._error);
  if (errors.length > 0) {
    console.log(`\nErrors: ${errors.length} files failed:`);
    errors.forEach((e) => console.log(`  - ${e.source_file}: ${e._error}`));
  }

  const successful = extracted.filter((e) => !e._error);
  console.log(`Successfully extracted: ${successful.length}/${allPDFs.length}`);
}

main().catch(console.error);
