import { NextResponse } from "next/server";
import { getSQL, ensureSchema } from "@/lib/db";
import fs from "fs";
import path from "path";

export const maxDuration = 60;

function buildSearchText(data: Record<string, unknown>): string {
  const parts: string[] = [];
  const fields = [
    "model", "variant", "product_type", "capacity", "category",
    "document_type", "source_file",
  ];
  for (const f of fields) {
    if (data[f] && typeof data[f] === "string") parts.push(data[f] as string);
  }

  // Add dimension keys and values
  if (data.dimensions && typeof data.dimensions === "object") {
    for (const [k, v] of Object.entries(data.dimensions as Record<string, unknown>)) {
      parts.push(k.replace(/_/g, " "));
      if (typeof v === "string" || typeof v === "number") parts.push(String(v));
    }
  }

  // Add spec keys and values
  if (data.specifications && typeof data.specifications === "object") {
    for (const [k, v] of Object.entries(data.specifications as Record<string, unknown>)) {
      parts.push(k.replace(/_/g, " "));
      if (typeof v === "string" || typeof v === "number") parts.push(String(v));
    }
  }

  // Add features
  if (Array.isArray(data.features)) {
    parts.push(...(data.features as string[]));
  }

  // Add certifications
  if (Array.isArray(data.certifications)) {
    parts.push(...(data.certifications as string[]));
  }

  // Add notes
  if (Array.isArray(data.notes)) {
    parts.push(...(data.notes as string[]).slice(0, 3));
  }

  return parts.join(" ").slice(0, 5000);
}

export async function POST() {
  try {
    await ensureSchema();
    const sql = getSQL();

    // Clear existing mohawk data
    await sql`DELETE FROM products WHERE manufacturer = 'mohawk'`;

    const entries: {
      manufacturer: string;
      category: string;
      model: string;
      variant: string;
      product_type: string;
      capacity: string;
      document_type: string;
      source_file: string;
      data: string;
      search_text: string;
    }[] = [];

    // Load individual extractions
    const indDir = path.join(process.cwd(), "kb_extracted", "individual");
    if (fs.existsSync(indDir)) {
      for (const f of fs.readdirSync(indDir).filter((f) => f.endsWith(".json"))) {
        try {
          const data = JSON.parse(fs.readFileSync(path.join(indDir, f), "utf-8"));
          entries.push({
            manufacturer: "mohawk",
            category: data.category || "unknown",
            model: data.model || "Unknown",
            variant: data.variant || "",
            product_type: data.product_type || "",
            capacity: data.capacity || "",
            document_type: data.document_type || "",
            source_file: data.source_file || f,
            data: JSON.stringify(data),
            search_text: buildSearchText(data),
          });
        } catch (e) {
          /* skip bad files */
        }
      }
    }

    // Load original KB
    const kbDir = path.join(process.cwd(), "kb_output");
    if (fs.existsSync(kbDir)) {
      for (const f of fs.readdirSync(kbDir).filter((f) => f.endsWith(".json"))) {
        try {
          const data = JSON.parse(fs.readFileSync(path.join(kbDir, f), "utf-8"));
          entries.push({
            manufacturer: "mohawk",
            category: data.category || data.product_type || "unknown",
            model: data.model || "Unknown",
            variant: data.variant || "",
            product_type: data.product_type || "",
            capacity: data.capacity || "",
            document_type: data.document_type || "",
            source_file: f,
            data: JSON.stringify(data),
            search_text: buildSearchText(data),
          });
        } catch (e) {
          /* skip */
        }
      }
    }

    // Load operation manuals
    const opsFile = path.join(process.cwd(), "kb_extracted", "operation-manuals.json");
    if (fs.existsSync(opsFile)) {
      const ops = JSON.parse(fs.readFileSync(opsFile, "utf-8"));
      for (const data of ops) {
        entries.push({
          manufacturer: "mohawk",
          category: "operation-manual",
          model: data.model || "Unknown",
          variant: data.variant || "",
          product_type: data.product_type || "",
          capacity: data.capacity || "",
          document_type: "operation-manual",
          source_file: data.source_file || "",
          data: JSON.stringify(data),
          search_text: buildSearchText(data),
        });
      }
    }

    // Insert in batches
    let inserted = 0;
    for (const entry of entries) {
      await sql`
        INSERT INTO products (manufacturer, category, model, variant, product_type, capacity, document_type, source_file, data, search_text)
        VALUES (${entry.manufacturer}, ${entry.category}, ${entry.model}, ${entry.variant}, ${entry.product_type}, ${entry.capacity}, ${entry.document_type}, ${entry.source_file}, ${entry.data}::jsonb, ${entry.search_text})
      `;
      inserted++;
    }

    return NextResponse.json({
      status: "ok",
      inserted,
      message: `Seeded ${inserted} Mohawk products`,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
