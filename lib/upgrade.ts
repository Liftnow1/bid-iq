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

export function tierOf(row: KnowledgeRowMinimal): 1 | 2 {
  return isTierTwo(row) ? 2 : 1;
}

// ---------------------------------------------------------------------------
// Python-availability probe (cached for the lifetime of the runtime)
// ---------------------------------------------------------------------------
//
// The upgrade flow shells out to `python3 -m bidiq.ingest`. On Vercel
// serverless there is no Python runtime, and the package isn't installed, so
// the shell-out would fail. We probe once and cache the result; callers
// degrade gracefully when the probe says it's unavailable.

let availabilityProbe: Promise<boolean> | null = null;

function probePythonUpgrade(): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };

    let child: ReturnType<typeof spawn>;
    try {
      child = spawn("python3", ["-c", "import bidiq"], {
        cwd: path.resolve(process.cwd()),
        env: { ...process.env },
        stdio: ["ignore", "ignore", "ignore"],
      });
    } catch {
      finish(false);
      return;
    }

    // Hard ceiling so a stuck environment can't hang the first request.
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch {}
      finish(false);
    }, 5_000);

    child.on("error", () => {
      clearTimeout(timer);
      finish(false);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      finish(code === 0);
    });
  });
}

/**
 * Returns true when `python3 -c "import bidiq"` succeeds in the current
 * runtime. Cached after the first call.
 */
export function isUpgradeAvailable(): Promise<boolean> {
  if (availabilityProbe === null) availabilityProbe = probePythonUpgrade();
  return availabilityProbe;
}

/** Test-only. Forces the next call to re-probe. */
export function _resetUpgradeAvailabilityForTests(): void {
  availabilityProbe = null;
}

export type UpgradeResult =
  | { ok: true; stdout: string }
  | { ok: false; error: string; stdout: string; stderr: string }
  | { ok: false; error: "upgrade_unavailable"; stdout: ""; stderr: "" };

/**
 * Shell out to `python -m bidiq.ingest --tier 2 --id <n>`.
 *
 * Returns `{ ok: false, error: "upgrade_unavailable" }` immediately when the
 * runtime probe says Python + bidiq aren't available — never crashes the
 * caller. Otherwise spawns the subprocess and waits for it to exit.
 */
export async function runPythonUpgrade(knowledgeItemId: number): Promise<UpgradeResult> {
  if (!(await isUpgradeAvailable())) {
    return { ok: false, error: "upgrade_unavailable", stdout: "", stderr: "" };
  }

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
 * (or already-Tier-2), false if the upgrade attempt failed or Python is
 * unavailable on this host.
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
