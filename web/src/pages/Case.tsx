import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { AlertDetail, Hit } from "../api/types";
import { useRequest } from "../api/useRequest";
import { ComparisonTable } from "../components/ComparisonTable";
import {
  DecisionChip,
  Empty,
  ErrorMessage,
  Fields,
  Id,
  Ids,
  Loading,
  pct,
  Rich,
} from "../components/common";
import { Investigator, Chat, CaseNotePanel } from "../components/Advisory";
import { ReviewPanel } from "../components/ReviewPanel";

export function ModelSignals({ hit }: { hit: Hit }) {
  const opinion = hit.llm?.second_opinion;
  const drivers = hit.ml?.drivers || [];
  const maxEffect = Math.max(...drivers.map((d) => Math.abs(d.effect)), 0.01);
  return (
    <section className="panel">
      <h2>Model signals</h2>
      <p className="quiet">
        Advisory signals · engine decision stays visible above
      </p>
      {hit.ml ? (
        <>
          <div className="metric-line">
            <span>Review priority</span>
            <b>
              {hit.ml.priority === null
                ? "Not scored for this hit"
                : pct(hit.ml.priority)}
            </b>
          </div>
          {hit.ml.priority !== null && (
            <progress
              max={1}
              value={hit.ml.priority}
              aria-label="Hit review priority"
            />
          )}
          <div className="metric-line">
            <span>Name similarity</span>
            <b>
              {hit.ml.name_similarity === null
                ? "Not scored"
                : pct(hit.ml.name_similarity)}
            </b>
          </div>
          <div className="metric-line">
            <span>Unusualness</span>
            <b>
              {hit.ml.anomaly === null ? "Not scored" : pct(hit.ml.anomaly)}
            </b>
          </div>
          {hit.ml.near_miss && (
            <p className="warning">
              Near miss: the engine cleared this name, but the model finds it
              similar.
            </p>
          )}
          <h3>Priority drivers</h3>
          {drivers.length ? (
            <ul className="drivers">
              {drivers.map((d) => (
                <li key={d.feature}>
                  <span>
                    <Rich>{d.label}</Rich>
                    <small>
                      <Rich>{d.value}</Rich>
                    </small>
                  </span>
                  <div className="signed-track">
                    <span
                      className={d.effect >= 0 ? "positive" : "negative"}
                      style={{
                        width: `${(Math.abs(d.effect) / maxEffect) * 50}%`,
                      }}
                    />
                  </div>
                  <span>
                    {d.effect > 0 ? "+" : ""}
                    {d.effect.toFixed(3)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet">No driver contributions available.</p>
          )}
        </>
      ) : (
        <Empty>ML not connected for this hit.</Empty>
      )}
      <h3>Second opinion</h3>
      {opinion ? (
        <>
          <div className="inline">
            <DecisionChip decision={opinion.decision} />
            <b>
              {opinion.agrees ? "Agrees with engine" : "Disagrees with engine"}
            </b>
          </div>
          <p>
            <Rich>{opinion.rationale}</Rich>
          </p>
          <small>
            {opinion.reason_code || "No reason code"} · {opinion.model}
          </small>
        </>
      ) : (
        <p className="quiet">LLM second opinion not connected for this hit.</p>
      )}
    </section>
  );
}
// The six checks behind a decision, in the order the engine runs them.
const PATH_LABEL: Record<string, string> = {
  type: "Type",
  name: "Name",
  dob: "Date of birth",
  location: "Country",
  time: "Age at earliest report",
  death: "Date of death",
};
const PATH_OUTCOME: Record<string, [string, string]> = {
  agree: ["✓", "Agrees"],
  contradict: ["✕", "Contradicts"],
  unknown: ["–", "Cannot compare"],
};
function HitCase({ hit, screenedAt }: { hit: Hit; screenedAt: string }) {
  const [candidateIndex, setCandidateIndex] = useState(0);
  const candidates = hit.trace?.candidates || [];
  const candidate = candidates[candidateIndex];
  const used = new Set(hit.trace?.hit.dob_sources || []);
  return (
    <>
      <section
        className={`decision-banner ${hit.decision}`}
        aria-label="Engine decision"
      >
        <div className="section-bar">
          <div>
            <p className="eyebrow">Engine decision</p>
            <h2>
              <DecisionChip decision={hit.decision} />
            </h2>
          </div>
          <code>{hit.reason_codes.join(" · ")}</code>
        </div>
        <p>
          <Rich>{(hit as Hit & { reason?: string }).reason || hit.summary}</Rich>
        </p>
        {candidate && (
          <ol className="decision-path" aria-label="Decision path">
            {candidate.checks.map((check) => {
              const [mark, word] = PATH_OUTCOME[check.outcome] ?? ["–", "Cannot compare"];
              const label =
                check.name === "dob" &&
                candidate.customer.entity_type === "Company"
                  ? "Incorporation date"
                  : (PATH_LABEL[check.name] ?? check.name);
              return (
                <li key={check.name} className={check.outcome} title={check.detail}>
                  <span className="mark" aria-hidden="true">
                    {mark}
                  </span>
                  <small>{label}</small>
                  <b>{word}</b>
                </li>
              );
            })}
          </ol>
        )}
        <div className="inline">
          <span>Customer record:</span>
          {hit.customer_record_id === "AMBIGUOUS" ? (
            <b>Ambiguous</b>
          ) : hit.customer_record_id ? (
            <Id id={hit.customer_record_id} />
          ) : (
            <span>None found</span>
          )}
          <span>Evidence:</span>
          {hit.evidence.length ? <Ids ids={hit.evidence} /> : "None supplied"}
        </div>
        {hit.blocker && (
          <p>
            <b>Blocker:</b> <Rich>{hit.blocker}</Rich>
          </p>
        )}
        {hit.label && (
          <p className="label-note">
            Practice label: <DecisionChip decision={hit.label.decision} /> ·{" "}
            {hit.label.decision === hit.decision
              ? "Engine matches"
              : "Engine mismatch"}{" "}
            · {hit.label.reason_codes.join(", ")}
          </p>
        )}
      </section>
      <div className="case-layout">
        <div className="case-evidence">
          <section className="panel">
            <h2>Evidence comparison</h2>
            {candidates.length > 1 && (
              <>
                <p>
                  {candidates.length} people fit this alert. Select a candidate
                  to inspect their evidence.
                </p>
                <div className="candidates">
                  {candidates.map((c, i) => (
                    <div
                      key={i}
                      className={
                        i === candidateIndex ? "candidate active" : "candidate"
                      }
                    >
                      <button
                        className="candidate-select"
                        aria-pressed={i === candidateIndex}
                        onClick={() => setCandidateIndex(i)}
                      >
                        <b>
                          {c.customer.verified_name ||
                            c.customer.declared_names[0]?.value ||
                            "Unknown customer"}
                        </b>
                        <small>Born {c.customer.dob || "unknown"}</small>
                        <DecisionChip decision={c.decision} />
                        <small>{c.reason_codes.join(", ")}</small>
                      </button>
                      <Ids ids={c.customer.records} />
                    </div>
                  ))}
                </div>
                <p className="quiet">
                  {hit.reason_code === "AMBIGUOUS_CUSTOMER_RECORD"
                    ? "Candidates lead to different decisions. Link the alert to one customer to settle it."
                    : "Candidates lead to the same decision, so it stands."}
                </p>
              </>
            )}
            {candidate && hit.trace ? (
              <>
                <p className="quiet">
                  Customer <Id id={candidate.customer.primary} /> against hit{" "}
                  <Id id={hit.hit_id} />
                </p>
                <ComparisonTable
                  hit={hit}
                  candidate={candidate}
                  screenedAt={screenedAt}
                />
                <h3>How the customer was found</h3>
                <ol className="trail">
                  {[...hit.trace.resolution, ...candidate.customer.path].map(
                    (step, i) => (
                      <li key={i}>
                        <Rich>{step}</Rich>
                      </li>
                    ),
                  )}
                </ol>
                {!hit.trace.resolution.length &&
                  !candidate.customer.path.length && (
                    <p className="quiet">
                      No customer record was found; only screened details are
                      known.
                    </p>
                  )}
                <p>
                  Customer records considered:{" "}
                  <Ids ids={candidate.customer.records} />
                </p>
              </>
            ) : (
              <Empty>No comparison trace is available for this hit.</Empty>
            )}
            <details>
              <summary>
                Search results considered (
                {hit.trace?.hit.search_considered.length || 0})
              </summary>
              {hit.trace?.hit.search_considered.length ? (
                [...hit.trace.hit.search_considered]
                  .sort((a, b) => Number(b.about_hit) - Number(a.about_hit))
                  .map((s) => (
                    <div
                      className={`search-considered ${used.has(s.result_id) ? "used" : ""}`}
                      key={s.result_id}
                    >
                      <Id id={s.result_id} />
                      <b>
                        {used.has(s.result_id)
                          ? "Used for date of birth"
                          : s.about_hit
                            ? "About the hit"
                            : "Excluded"}
                      </b>
                      {s.why_not && (
                        <p className="quiet">
                          <Rich>{s.why_not}</Rich>
                        </p>
                      )}
                      <p>
                        <Rich>{s.snippet}</Rich>
                      </p>
                    </div>
                  ))
              ) : (
                <p className="quiet">No search results considered.</p>
              )}
            </details>
            <details>
              <summary>Watchlist profile</summary>
              <Id id={hit.hit_id} />
              <Fields fields={hit.profile} />
              <blockquote className="pre-wrap">
                <Rich>{hit.profile_notes || "No profile notes."}</Rich>
              </blockquote>
            </details>
          </section>
          <section className="panel">
            <h2>What would settle it</h2>
            {hit.counterfactuals.length ? (
              hit.counterfactuals.map((c, i) => (
                <div className="counterfactual" key={i}>
                  <h3 className="question">
                    <Rich>{c.question}</Rich>
                  </h3>
                  {c.outcomes.map((outcome, j) => (
                    <div className="outcome" key={j}>
                      <span>
                        <Rich>{outcome.if}</Rich>
                      </span>
                      <DecisionChip decision={outcome.decision} />
                      <small>{outcome.reason_code}</small>
                    </div>
                  ))}
                </div>
              ))
            ) : (
              <Empty>No additional fact requested by the engine.</Empty>
            )}
          </section>
          <ReviewPanel hit={hit} />
        </div>
        <aside className="case-tools">
          <ModelSignals hit={hit} />
          <Investigator hitId={hit.hit_id} />
          <Chat hitId={hit.hit_id} />
          <CaseNotePanel hitId={hit.hit_id} />
        </aside>
      </div>
    </>
  );
}
export function Case() {
  const { alertId, hitId } = useParams();
  const { data, loading, error, reload } = useRequest<AlertDetail>(
    `/alerts/${encodeURIComponent(alertId || "")}`,
  );
  const hit = hitId
    ? data?.hits.find((h) => h.hit_id === hitId)
    : data?.hits[0];
  return (
    <main className="page">
      <Link className="back-link" to="/">
        ← Screening queue
      </Link>
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && (
        <>
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                Alert {data.alert.alert_id} · screened{" "}
                {data.alert.screened_at || "date unknown"}
              </p>
              <h1>
                <Rich>{data.alert.customer_name}</Rich>
              </h1>
              <p className="quiet">
                {Object.entries(data.alert)
                  .filter(
                    ([key]) =>
                      ![
                        "alert_id",
                        "customer_name",
                        "screened_at",
                        "hit_count",
                      ].includes(key),
                  )
                  .map(([key, value]) => (
                    <span className="screened-field" key={key}>
                      {key.replaceAll("_", " ")}: <Rich>{value}</Rich>
                    </span>
                  ))}
              </p>
            </div>
            <span className="quiet">{data.hits.length} watchlist hits</span>
          </div>
          <nav className="hit-tabs" aria-label="Hits in this alert">
            {data.hits.map((h) => (
              <div
                className={
                  h.hit_id === hit?.hit_id ? "hit-tab active" : "hit-tab"
                }
                key={h.hit_id}
              >
                <Link
                  to={`/alerts/${alertId}/${h.hit_id}`}
                  aria-current={h.hit_id === hit?.hit_id ? "page" : undefined}
                >
                  <b>{h.profile.name || "Watchlist hit"}</b>
                  <DecisionChip decision={h.decision} />
                </Link>
                <Id id={h.hit_id} />
              </div>
            ))}
          </nav>
          {hit ? (
            <HitCase
              key={hit.hit_id}
              hit={hit}
              screenedAt={data.alert.screened_at}
            />
          ) : (
            <Empty>
              {hitId
                ? "The requested hit is not part of this alert. Select one above."
                : "This alert has no hits."}
            </Empty>
          )}
        </>
      )}
    </main>
  );
}
