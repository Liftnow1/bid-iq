/**
 * Agent-tier filtering constants for the v4-trimmed vocabulary.
 *
 * These are NOT enforced anywhere yet — `/api/ask` is currently the
 * bid-agent-equivalent and Paul is the only user (via the MCP server),
 * so all rows are visible. The lists are committed here as the canonical
 * mapping so the email agent and content engine can import them when
 * they wire up their own retrieval paths.
 *
 * Vocabulary source: docs/classifier-system-prompt-v1.md, Appendix A.
 */

export const PAUL_ONLY: ReadonlyArray<string> = [
  "financial-statement",
  "commission-report",
  "employment-document",
];

/** Bid agent (today's `/api/ask`) sees almost everything. */
export const ALWAYS_EXCLUDE_FOR_BID_AGENT: ReadonlyArray<string> = [...PAUL_ONLY];

/**
 * Email agent — drafts external correspondence, so it must never see
 * cost data, customer financials, or anything competitively sensitive.
 */
export const EMAIL_AGENT_EXCLUDE: ReadonlyArray<string> = [
  ...PAUL_ONLY,
  "vendor-cost-pricing",
  "customer-invoice",
  "insurance-policy",
  "bond-instrument",
  "vendor-po",
  "vendor-invoice",
  "payment-record",
  "certified-payroll",
  "contract-reporting-record",
  "bid-protest",
  "change-order",
  "competitive-intelligence",
  "win-loss-debrief",
];

/**
 * Content engine — public-facing surface. Allow-list, not deny-list.
 */
export const CONTENT_ENGINE_ALLOW: ReadonlyArray<string> = [
  "installation-guides",
  "service-procedures",
  "parts-catalog",
  "specifications",
  "operation-manuals",
  "safety-warnings",
  "warranty-documentation",
  "marketing-brochure",
  "manufacturer-training",
  "technical-bulletin",
  "site-survey",
  "compliance-regulations",
  "procurement-process",
  "industry-reference",
  "capability-statement",
  "sales-collateral",
  "case-study",
  "list-pricing",
  "regulatory-update",
  "customer-quote-history",
  "voice-samples",
];
