import Anthropic from "@anthropic-ai/sdk";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const OUT_DIR = path.join(ROOT, "kb_extracted", "individual");
const MANUALS_DIR = path.join(
  ROOT,
  "data",
  "product_data",
  "mohawk",
  "operation-manuals"
);

const client = new Anthropic();

const EXTRACTION_PROMPT = `Extract all structured product/technical data from this PDF operation manual. Return valid JSON with these fields:

{
  "source_file": "<original filename>",
  "category": "operation-manual",
  "document_type": "operation-manual",
  "model": "<model number>",
  "variant": "<variant if any>",
  "product_type": "<type of lift/equipment>",
  "manufacturer": "Mohawk Lifts",
  "capacity": "<rated capacity with units>",
  "dimensions": { <all dimensions with units> },
  "specifications": { <all specs: motor, hydraulic, electrical, etc.> },
  "features": [ <key features> ],
  "safety_features": [ <safety features> ],
  "optional_equipment": [ <optional accessories/equipment> ],
  "certifications": [ <certifications like ALI, UL, etc.> ],
  "maintenance": [ <key maintenance items/intervals> ],
  "notes": [ <important notes, max 5> ]
}

Be thorough and precise with all numbers, units, and model numbers. Omit fields that have no data. Return ONLY valid JSON, no markdown.`;

async function extractPDF(pdfPath, filename) {
  const outName =
    filename.replace(/\.pdf$/i, "").replace(/[\s()]/g, "_") +
    "_operation-manual.json";
  const outPath = path.join(OUT_DIR, outName);

  if (fs.existsSync(outPath)) {
    console.log(`  SKIP (exists): ${outName}`);
    return { skipped: true, file: outName };
  }

  const pdfData = fs.readFileSync(pdfPath);

  // Check if it's an LFS pointer (not actual PDF content)
  const header = pdfData.slice(0, 50).toString("utf-8");
  if (header.includes("git-lfs") || header.includes("version https://")) {
    console.log(`  SKIP (LFS pointer): ${filename}`);
    return { skipped: true, file: filename, reason: "lfs-pointer" };
  }

  const base64 = pdfData.toString("base64");

  console.log(`  Extracting: ${filename}...`);

  const response = await client.messages.create({
    model: "claude-sonnet-4-20250514",
    max_tokens: 8192,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "document",
            source: { type: "base64", media_type: "application/pdf", data: base64 },
          },
          { type: "text", text: EXTRACTION_PROMPT },
        ],
      },
    ],
  });

  const textBlock = response.content.find((b) => b.type === "text");
  if (!textBlock || textBlock.type !== "text") {
    console.log(`  ERROR: No text response for ${filename}`);
    return { error: true, file: filename };
  }

  // Parse and clean the JSON
  let json;
  try {
    // Strip markdown code fences if present
    let raw = textBlock.text.trim();
    if (raw.startsWith("```")) {
      raw = raw.replace(/^```[a-z]*\n?/, "").replace(/\n?```$/, "");
    }
    json = JSON.parse(raw);
  } catch (e) {
    console.log(`  ERROR: Invalid JSON for ${filename}: ${e.message}`);
    fs.writeFileSync(outPath + ".raw.txt", textBlock.text);
    return { error: true, file: filename };
  }

  json.source_file = pdfPath;
  fs.writeFileSync(outPath, JSON.stringify(json, null, 2));
  console.log(`  OK: ${outName}`);
  return { ok: true, file: outName };
}

async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const pdfs = fs
    .readdirSync(MANUALS_DIR)
    .filter((f) => f.toLowerCase().endsWith(".pdf"));

  console.log(`Found ${pdfs.length} PDFs in operation-manuals/`);

  const results = { ok: 0, skipped: 0, errors: 0, lfsPointers: 0 };

  for (const pdf of pdfs) {
    const pdfPath = path.join(MANUALS_DIR, pdf);
    try {
      const result = await extractPDF(pdfPath, pdf);
      if (result.ok) results.ok++;
      else if (result.reason === "lfs-pointer") results.lfsPointers++;
      else if (result.skipped) results.skipped++;
      else results.errors++;
    } catch (e) {
      console.log(`  ERROR: ${pdf}: ${e.message}`);
      results.errors++;
    }
  }

  console.log("\nDone!", JSON.stringify(results, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
