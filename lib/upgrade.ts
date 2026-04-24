import { spawn } from "node:child_process";
import path from "node:path";
import { getSQL } from "./db";

export type KnowledgeRowMinimal = {
  id: number;
  raw_content: string | null;
  extracted_data: Record<string, unknown> | null;
};

/**
 * A row is considered Tier-2 if either:
 *   - it has non-empty raw_content (covers legacy `ingest.py-v1` rows that
 *     predate the explicit `tier` field), or
 *   - extracted_data.tier === 2.
 *
 * Anything else (raw_content null/empty AND tier 1 or unset) is Tier-1.
 */
export function isTierTwo(row: KnowledgeRowMinimal): boolean {
  if (row.raw_content && row.raw_content.length > 0) return true;
  const tier = (row.extracted_data as { tier?: number } | null)?.tier;
  return tier === 2;
}

export type UpgradeResult =
  | { ok: true; stdout: string }
  | { ok: false; error: string; stdout: string; stderr: string };

/**
 * Shell out to `python -m bidiq.ingest --tier 2 --id <n>`.
 *
 * NOTE: This requires the runtime host to have Python 3, the `bidiq` package
 * importable, and Poppler installed. It does not work on Vercel serverless
 * (no Python runtime). Self-host or run on a worker for this to function.
 */
export function runPythonUpgrade(knowledgeItemId: number): Promise<UpgradeResult> {
  return new Promise((resolve) => {
    const cwd = path.resolve(process.cwd());
    const child = spawn(
      "python3",
      ["-m", "bidiq.ingest", "--tier", "2", "--id", String(knowledgeItemId)],
      {
        cwd,
        env: { ...process.env },
        stdio: ["ignore", "pipe", "pipe"],
      }
    );

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (err) => {
      resolve({ ok: false, error: err.message, stdout, stderr });
    });
    child.on("close", (code) => {
      if (code === 0) resolve({ ok: true, stdout });
      else
        resolve({
          ok: false,
          error: `python -m bidiq.ingest exited with code ${code}`,
          stdout,
          stderr,
        });
    });
  });
}

/**
 * Upgrade a single knowledge_items row in-place. Returns true on success
 * (or already-Tier-2), false if the upgrade attempt failed.
 */
export async function upgradeRowInPlace(id: number): Promise<boolean> {
  const sql = getSQL();
  const before = (await sql`
    SELECT id, raw_content, extracted_data
      FROM knowledge_items
     WHERE id = ${id}
     LIMIT 1
  `) as unknown as KnowledgeRowMinimal[];
  if (!before[0]) return false;
  if (isTierTwo(before[0])) return true;
  const result = await runPythonUpgrade(id);
  return result.ok;
}
