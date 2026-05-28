/**
 * Typed constants for HubSpot ticket pipeline 0 (Agent Recommendations).
 *
 * Goal: stop sprinkling magic numbers like "1363043699" across Vercel API
 * routes, n8n Code nodes, and frontend code. Reference these by name so a
 * future stage renumber is a single-file change (and a future audit can
 * grep for STAGE_ usages).
 *
 * Pipeline 0 is the ONLY pipeline these agents touch. Pipelines 1+ are
 * reserved for human/CRM workflows — do not let agent code write there.
 *
 * Audit reference: `audit/06-stage-transition-anomalies.md` §Stage ID reference.
 */

export const PIPELINE_AGENT_TICKETS = "0" as const;

// Stage IDs — HubSpot returns them as strings, so keep them as strings here.
export const STAGE_PENDING_REVIEW = "1" as const; // agent filed, waiting on Paul
export const STAGE_APPROVED = "2" as const;        // Paul approved, AE will pick up
export const STAGE_DEFERRED = "3" as const;        // Paul deferred N days
export const STAGE_REJECTED = "4" as const;        // Paul rejected
export const STAGE_AUTO_APPLIED = "1363043699" as const; // AE successfully executed

export type StageId =
  | typeof STAGE_PENDING_REVIEW
  | typeof STAGE_APPROVED
  | typeof STAGE_DEFERRED
  | typeof STAGE_REJECTED
  | typeof STAGE_AUTO_APPLIED;

export const ALL_STAGES = [
  STAGE_PENDING_REVIEW,
  STAGE_APPROVED,
  STAGE_DEFERRED,
  STAGE_REJECTED,
  STAGE_AUTO_APPLIED,
] as const;

/** Open (still requires action by a person or AE) */
export const OPEN_STAGES = [STAGE_PENDING_REVIEW, STAGE_APPROVED] as const;

/** Closed (terminal — no further automated action) */
export const CLOSED_STAGES = [
  STAGE_DEFERRED,
  STAGE_REJECTED,
  STAGE_AUTO_APPLIED,
] as const;

/** Stages that the approvals dashboard "Done" bucket should show */
export const DONE_BUCKET_STAGES = [
  STAGE_DEFERRED,
  STAGE_REJECTED,
  STAGE_AUTO_APPLIED,
] as const;

/** Stages that the approvals dashboard "All" bucket should show */
export const ALL_BUCKET_STAGES = ALL_STAGES;

/** Stages that the approvals dashboard "Pending" bucket should show */
export const PENDING_BUCKET_STAGES = [STAGE_PENDING_REVIEW] as const;

export function isOpenStage(stage: string | null | undefined): boolean {
  return stage != null && (OPEN_STAGES as readonly string[]).includes(stage);
}

export function isClosedStage(stage: string | null | undefined): boolean {
  return stage != null && (CLOSED_STAGES as readonly string[]).includes(stage);
}

export function isAutoApplied(stage: string | null | undefined): boolean {
  return stage === STAGE_AUTO_APPLIED;
}

export function isPipelineAgentTickets(pipeline: string | null | undefined): boolean {
  return pipeline === PIPELINE_AGENT_TICKETS;
}

export function stageLabel(stage: string | null | undefined): string {
  switch (stage) {
    case STAGE_PENDING_REVIEW:
      return "Pending Review";
    case STAGE_APPROVED:
      return "Approved";
    case STAGE_DEFERRED:
      return "Deferred";
    case STAGE_REJECTED:
      return "Rejected";
    case STAGE_AUTO_APPLIED:
      return "Auto-Applied";
    default:
      return `Unknown(${stage ?? "null"})`;
  }
}

/** Bucket name -> stage list mapping for the agent-proposals webhook */
export const BUCKET_TO_STAGES: Record<string, readonly string[]> = {
  pending: PENDING_BUCKET_STAGES,
  done: DONE_BUCKET_STAGES,
  all: ALL_BUCKET_STAGES,
  view: PENDING_BUCKET_STAGES, // legacy alias seen in dashboard
};

export function stagesForBucket(bucket: string | null | undefined): readonly string[] {
  if (!bucket) return PENDING_BUCKET_STAGES;
  return BUCKET_TO_STAGES[bucket.toLowerCase()] ?? PENDING_BUCKET_STAGES;
}
