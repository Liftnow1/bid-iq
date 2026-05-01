/**
 * Agent-tier filtering constants for the v2 3-tier access model.
 *
 * These are NOT enforced anywhere yet — `/api/ask` is currently the
 * bid-agent-equivalent and Paul is the only user (via the MCP server),
 * so all rows are visible. The lists are committed here as the canonical
 * mapping so the email agent, future bid tracker, and content engine
 * can import them when they wire up their own retrieval paths.
 *
 * Vocabulary source: docs/classifier-system-prompt-v2.md.
 */

/** Tier-3: Paul-only — no agent ever sees these. */
export const PAUL_ONLY: ReadonlyArray<string> = ["tier-3-paul-only"];

/**
 * Email agent (and future Bid Tracker) excludes only tier-3.
 * They can see tier-1-public + tier-2-internal.
 */
export const EMAIL_AGENT_EXCLUDE: ReadonlyArray<string> = ["tier-3-paul-only"];

/**
 * Content engine sees tier-1 only — public-safe surface.
 * Excludes tier-2-internal and tier-3-paul-only.
 */
export const CONTENT_ENGINE_ALLOW: ReadonlyArray<string> = ["tier-1-public"];

/** Convenience: the full ordered tier list (no `uncategorized`). */
export const ALL_TIERS: ReadonlyArray<string> = [
  "tier-1-public",
  "tier-2-internal",
  "tier-3-paul-only",
];
