import { useEffect, useRef, useState, type FormEvent } from "react";
import type { AuditEntry, Decision, Hit, ReasonCode } from "../api/types";
import { errorText, MOCK, post } from "../api/client";
import { useRequest } from "../api/useRequest";
import {
  DecisionChip,
  decisions,
  Empty,
  ErrorMessage,
  Loading,
  Rich,
  words,
} from "./common";

export const reasons: Record<Decision, ReasonCode[]> = {
  DIFFERENT_ENTITY: [
    "TEMPORAL_IMPOSSIBILITY",
    "DOB_MISMATCH",
    "LOCATION_MISMATCH",
    "NAME_MISMATCH_VERIFIED",
    "ENTITY_TYPE_MISMATCH",
    "DECEASED",
  ],
  SAME_ENTITY: [
    "NAME_DOB_LOCATION_MATCH",
    "NAME_DOB_MATCH",
    "NAME_LOCATION_MATCH",
  ],
  REVIEW: ["INSUFFICIENT_DATA", "AMBIGUOUS_CUSTOMER_RECORD"],
};
export function ReviewPanel({ hit }: { hit: Hit }) {
  const [decision, setDecision] = useState(hit.decision);
  const [reason, setReason] = useState<ReasonCode>(hit.reason_code);
  const [comment, setComment] = useState("");
  const [analyst, setAnalyst] = useState("analyst");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const audit = useRequest<AuditEntry[]>(
    `/audit?hit_id=${encodeURIComponent(hit.hit_id)}`,
  );
  const history = audit.data?.slice().sort((a, b) => b.id - a.id);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const override = decision !== hit.decision;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setSaved("");
    const run = new AbortController();
    controller.current = run;
    try {
      await post<AuditEntry>(
        `/hits/${hit.hit_id}/review`,
        {
          decision,
          reason_code: reason,
          comment,
          analyst: analyst.trim() || "analyst",
        },
        run.signal,
      );
      if (!run.signal.aborted) {
        setSaved(
          MOCK
            ? "Review recorded for this mock session. Reloading resets it."
            : "Analyst review recorded.",
        );
        audit.reload();
      }
    } catch (e) {
      if (!run.signal.aborted) setError(errorText(e));
    } finally {
      if (!run.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel review-panel">
      <h2>Analyst decision</h2>
      <p className="quiet">
        Your review is stored in the audit log. The engine decision remains{" "}
        {words[hit.decision].toLowerCase()}.
      </p>
      <form onSubmit={submit}>
        <div className="review-controls">
          <label>
            Decision
            <select
              value={decision}
              onChange={(e) => {
                const next = e.target.value as Decision;
                setDecision(next);
                setReason(reasons[next][0]);
                setSaved("");
              }}
            >
              {decisions.map((d) => (
                <option value={d} key={d}>
                  {words[d]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Reason code
            <select
              value={reason}
              onChange={(e) => setReason(e.target.value as ReasonCode)}
            >
              {reasons[decision].map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
          <label>
            Analyst
            <input
              value={analyst}
              onChange={(e) => setAnalyst(e.target.value)}
              autoComplete="username"
            />
          </label>
        </div>
        <label>
          Review comment
          <textarea
            rows={3}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Explain the evidence supporting your review"
          />
        </label>
        {override && (
          <p className="warning">
            <b>Overrides engine decision.</b> Engine:{" "}
            <DecisionChip decision={hit.decision} /> Your review:{" "}
            <DecisionChip decision={decision} />
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "Saving…" : "Record analyst decision"}
        </button>
        <p role="status">{saved}</p>
        <ErrorMessage message={error} />
      </form>
      <h3>Audit history</h3>
      {audit.loading && <Loading />}
      <ErrorMessage message={audit.error} retry={audit.reload} />
      {audit.data?.length === 0 && (
        <Empty>No analyst decisions recorded for this hit.</Empty>
      )}
      {history?.map((entry) => (
        <article className="audit-entry" key={entry.id}>
          <div className="section-bar">
            <DecisionChip decision={entry.decision} />
            <small>
              {new Date(entry.at * 1000).toLocaleString()} ·{" "}
              <Rich>{entry.analyst}</Rich>
            </small>
          </div>
          <p>
            <code>{entry.reason_code}</code>
            {entry.overrides_engine && (
              <b className="override-marker">Overrides engine</b>
            )}
          </p>
          <p>
            <Rich>{entry.comment}</Rich>
          </p>
          <small>
            Engine: {words[entry.engine_decision]} · {entry.engine_reason_code}
          </small>
        </article>
      ))}
    </section>
  );
}
