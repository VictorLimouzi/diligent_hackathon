// Casebook API contract. Frozen: only the main session changes this file.
// Every shape here is what the server (server/app.py) returns today.
// Example payloads for every endpoint are in contracts/fixtures/.

export type Decision = "DIFFERENT_ENTITY" | "SAME_ENTITY" | "REVIEW";
export type Outcome = "agree" | "contradict" | "unknown";
export type AlertStatus = "open" | "compliance" | "cleared";
// open: nothing confirmed, at least one hit in REVIEW
// compliance: at least one hit is SAME_ENTITY
// cleared: every hit is DIFFERENT_ENTITY

export type ReasonCode =
  | "TEMPORAL_IMPOSSIBILITY" | "DOB_MISMATCH" | "LOCATION_MISMATCH" | "NAME_MISMATCH_VERIFIED"
  | "ENTITY_TYPE_MISMATCH" | "DECEASED"                                   // DIFFERENT_ENTITY
  | "NAME_DOB_LOCATION_MATCH" | "NAME_DOB_MATCH" | "NAME_LOCATION_MATCH"  // SAME_ENTITY
  | "INSUFFICIENT_DATA" | "AMBIGUOUS_CUSTOMER_RECORD";                    // REVIEW

/** Record IDs look like CUS-…, KYC-…, NOTE-…, SR-…, H-…; fetch any with GET /api/records/{id}. */
export type RecordId = string;

export interface SourcedValue { value: string; source: RecordId }

/** One policy check of a customer against a hit. */
export interface Check {
  name: "type" | "name" | "dob" | "location" | "time" | "death";
  outcome: Outcome;
  detail: string;            // one sentence, safe to show as is
  sources: RecordId[];
}

/** The customer as the engine pictured them (one per person that fits the alert). */
export interface CustomerPicture {
  records: RecordId[];
  primary: RecordId | null;            // null when no record was found
  entity_type: "Individual" | "Company" | null;
  verified_check: RecordId | null;     // latest approved identity check (KYC-… or NOTE-…)
  verified_name: string | null;
  declared_names: SourcedValue[];      // source is a CUS-… id, or the alert id for the screened name
  dob: string | null;                  // partial ISO: "1979-08-14", "1968-02" or "1980"
  dob_source: RecordId | null;
  dob_verified: boolean;
  countries: SourcedValue[];
  path: string[];                      // how the verified check was reached
}

export interface Candidate {
  customer: CustomerPicture;
  decision: Decision;
  reason_codes: ReasonCode[];
  evidence: RecordId[];
  summary: string;
  checks: Check[];
}

export interface SearchResultConsidered {
  result_id: RecordId;
  snippet: string;
  about_hit: boolean;
  why_not: string | null;              // why it was excluded, or why it gave no birth date
}

export interface HitFacts {
  names: string[];                     // name first, then aliases
  dobs: string[];                      // partial ISO dates
  dob_origin: "dobs field" | "profile notes" | "search results" | null;
  dob_quote: string | null;
  dob_sources: RecordId[];             // SR-… ids used
  countries: string[];
  earliest_report: string | null;
  earliest_report_line: string | null;
  death: string | null;
  search_considered: SearchResultConsidered[];
}

/** A single fact that would settle a REVIEW hit, and what each answer leads to. Computed by the engine. */
export interface Counterfactual {
  fact: "hit_dob" | "customer_dob" | "customer_name" | "customer_country" | "hit_country" | "customer_record";
  question: string;
  outcomes: { if: string; decision: Decision; reason_code: ReasonCode }[];
}

/** Advisory model scores. null until the models have been trained (python -m ml.train). */
export interface MlScores {
  priority: number | null;             // 0..1, likelihood a REVIEW hit is the customer; null for decided hits
  name_similarity: number | null;      // 0..1, learned similarity of customer name and best hit name
  near_miss: boolean;                  // rules cleared on name, but the names look alike to the model
  anomaly: number | null;              // 0..1, how unusual this case is among its decision group
  drivers: { feature: string; label: string; value: string; effect: number }[]; // effect: signed push on priority
}

export interface SecondOpinion {
  decision: Decision;
  reason_code: ReasonCode | null;
  rationale: string;
  agrees: boolean;                     // with the engine's decision
  model: string;
}

export interface Hit {
  hit_id: RecordId;
  alert_id: string;
  decision: Decision;                  // the engine's decision; nothing else ever sets it
  reason_code: ReasonCode;
  reason_codes: ReasonCode[];          // every code that applies; reason_code is the first
  customer_record_id: RecordId | "AMBIGUOUS" | null;
  evidence: RecordId[];
  summary: string;
  blocker: string | null;              // short label of what keeps a REVIEW open
  counterfactuals: Counterfactual[];   // empty unless decision is REVIEW
  trace: {
    resolution: string[];              // how the customer was found; contains record ids
    hit: HitFacts;
    candidates: Candidate[];           // length > 1 when several people fit the alert
  } | null;
  ml: MlScores | null;
  llm: { second_opinion: SecondOpinion | null } | null;
  profile: Record<string, string>;     // name, aliases, entity_type, dobs, countries, addresses, categories, primary_category, position_or_allegation, match_score, ...
  profile_notes: string;
  label: { decision: Decision; reason_codes: ReasonCode[] } | null;  // practice label, when one exists
}

// ---- GET /api/summary
export interface Summary {
  source: string;
  alerts: number;
  alert_status: Partial<Record<AlertStatus, number>>;
  hits: number;
  decisions: Partial<Record<Decision, number>>;
  reason_codes: Partial<Record<ReasonCode, number>>;
  review_rate: number;                 // 0..1
  labels: LabelMetrics;
  ml: { trained: boolean; metrics: Record<string, unknown> | null };
  llm: { live: boolean; model_interactive: string | null; model_batch: string | null; second_opinions: number; stub?: boolean };
}
export interface LabelMetrics { n: number; correct: number; false_clears: number; false_confirms: number; reason_code_accepted: number }

// ---- GET /api/alerts?status=&q=&labelled=&sort=priority|file
export interface AlertRow {
  alert_id: string;
  customer_name: string;
  entity_type: string;
  screened_at: string;
  status: AlertStatus;
  hit_count: number;
  decisions: Partial<Record<Decision, number>>;
  priority: number | null;             // highest ml.priority among the alert's REVIEW hits
  labelled: boolean;
  blockers: string[];
  hit_ids: RecordId[];
  hit_names: string[];
}

// ---- GET /api/alerts/{alert_id}
export interface AlertDetail {
  alert: Record<string, string>;       // alert_id, screened_at, customer_name, entity_type, dob?, country?, city?, account_ref?, login_handle?, email?
  hits: Hit[];
}

// ---- GET /api/records/{id}
export interface SourceRecord { id: RecordId; kind: string; fields: Record<string, string> }

// ---- GET /api/search/customers?q=&k=
export interface CustomerMatch { record_id: RecordId; full_name: string; dob: string | null; country: string | null; score: number }

// ---- GET /api/qa
export interface QaRow { hit_id: RecordId; alert_id: string; hit_name: string; decision: Decision; reason_code: ReasonCode }
export interface Qa {
  label_metrics: LabelMetrics;
  labels: (QaRow & { label_decision: Decision; label_reason_codes: ReasonCode[]; match: boolean })[];
  second_opinion: {
    compared: number;
    matrix: Record<string, number>;    // key "ENGINE|LLM", e.g. "REVIEW|SAME_ENTITY"
    disagreements: (QaRow & { llm_decision: Decision; llm_reason_code: ReasonCode | null; rationale: string })[];
  };
  near_misses: (QaRow & { name_similarity: number; customer_name: string })[];
  anomalies: (QaRow & { anomaly: number })[];
  blockers: { blocker: string; count: number; hit_ids: RecordId[] }[];
  ml_metrics: Record<string, unknown> | null;
}

// ---- GET /api/queue/summary
export interface QueueSummary { groups: Qa["blockers"]; text: string; cached: boolean; stub?: boolean }

// ---- POST /api/hits/{hit_id}/note
export interface CaseNote { note: string; cited_ids: RecordId[]; cached: boolean; model: string | null; stub?: boolean }

// ---- POST /api/hits/{hit_id}/investigate   (text/event-stream; each event is `data: <json>\n\n`)
export type InvestigateEvent =
  | { type: "step"; index: number; tool: string; input: Record<string, unknown>; summary: string }
  | { type: "thought"; text: string }
  | { type: "final"; result: InvestigationResult }
  | { type: "error"; message: string };
export interface InvestigationResult {
  blocker: string;
  findings: { text: string; cited_ids: RecordId[] }[];
  next_action: string;
  rfi_draft: string | null;            // drafted request for information to the customer
  cited_ids: RecordId[];
  model: string | null;
  cached: boolean;
  stub?: boolean;
}

// ---- POST /api/hits/{hit_id}/chat   body: { messages: {role: "user"|"assistant"; content: string}[] }  (event-stream)
export type ChatEvent =
  | { type: "step"; index: number; tool: string; input: Record<string, unknown>; summary: string }
  | { type: "text"; delta: string }
  | { type: "done"; cited_ids: RecordId[]; stub?: boolean }
  | { type: "error"; message: string };

// ---- POST /api/hits/{hit_id}/review   body: { decision, reason_code, comment?, analyst? }
// ---- GET  /api/audit?hit_id=
export interface AuditEntry {
  id: number;
  at: number;                          // unix seconds
  hit_id: RecordId;
  alert_id: string;
  analyst: string;
  decision: Decision;
  reason_code: string;
  comment: string;
  engine_decision: Decision;
  engine_reason_code: ReasonCode;
  overrides_engine: boolean;
}
