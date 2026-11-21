import { Link } from "react-router-dom";
import type { AlertRow, Qa, QaRow } from "../api/types";
import { useRequest } from "../api/useRequest";
import {
  DecisionChip,
  decisions,
  Empty,
  ErrorMessage,
  Id,
  Loading,
  pct,
  Rich,
  words,
} from "../components/common";
function CaseLink({ row }: { row: QaRow }) {
  return (
    <>
      <Link
        to={`/alerts/${row.alert_id}/${row.hit_id}`}
        className="primary-link"
      >
        {row.hit_name || "Open case"} →
      </Link>
      <small>
        {row.alert_id} · <Id id={row.hit_id} />
      </small>
    </>
  );
}
export function QA() {
  const { data, loading, error, reload } = useRequest<Qa>("/qa");
  const alerts = useRequest<AlertRow[]>("/alerts");
  const alertByHit = new Map(
    alerts.data?.flatMap((a) =>
      a.hit_ids.map((id) => [id, a.alert_id] as const),
    ),
  );
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Quality assurance</p>
          <h1>Decision quality</h1>
          <p className="quiet">
            Practice labels and advisory disagreements for analyst inspection.
          </p>
        </div>
      </div>
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && (
        <>
          <div className="metric-grid">
            <div className="panel">
              <small>Practice decision match</small>
              <b>
                {data.label_metrics.correct} / {data.label_metrics.n}
              </b>
            </div>
            <div className="panel">
              <small>False clears</small>
              <b>{data.label_metrics.false_clears}</b>
            </div>
            <div className="panel">
              <small>False confirms</small>
              <b>{data.label_metrics.false_confirms}</b>
            </div>
            <div className="panel">
              <small>Accepted reason codes</small>
              <b>
                {data.label_metrics.reason_code_accepted} /{" "}
                {data.label_metrics.n}
              </b>
            </div>
          </div>
          <section className="panel">
            <h2>Practice labels</h2>
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Practice label comparison"
            >
              <table>
                <thead>
                  <tr>
                    {["Case", "Engine", "Practice label", "Result"].map((s) => (
                      <th key={s}>{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.labels.map((row) => (
                    <tr
                      key={row.hit_id}
                      className={!row.match ? "mismatch" : ""}
                    >
                      <td>
                        <CaseLink row={row} />
                      </td>
                      <td>
                        <DecisionChip decision={row.decision} />
                        <small>{row.reason_code}</small>
                      </td>
                      <td>
                        <DecisionChip decision={row.label_decision} />
                        <small>{row.label_reason_codes.join(", ")}</small>
                      </td>
                      <td>
                        {row.match ? (
                          "Match"
                        ) : row.decision === "DIFFERENT_ENTITY" &&
                          row.label_decision !== "DIFFERENT_ENTITY" ? (
                          <strong className="false-clear">
                            False clear · mismatch
                          </strong>
                        ) : (
                          <strong>Mismatch</strong>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!data.labels.length && (
              <Empty>No practice labels available.</Empty>
            )}
          </section>
          <section className="panel">
            <h2>Engine vs second opinion</h2>
            <p className="quiet">
              {data.second_opinion.compared} hits compared. Rows are the engine;
              columns are the second opinion.
            </p>
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Engine versus second opinion matrix"
            >
              <table className="matrix">
                <thead>
                  <tr>
                    <th>Engine / second opinion</th>
                    {decisions.map((d) => (
                      <th key={d}>{words[d]}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {decisions.map((engine) => (
                    <tr key={engine}>
                      <th>
                        <DecisionChip decision={engine} />
                      </th>
                      {decisions.map((llm) => (
                        <td
                          key={llm}
                          className={engine === llm ? "matrix-agreement" : ""}
                        >
                          {data.second_opinion.matrix[`${engine}|${llm}`] ?? 0}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>Disagreements</h3>
            {data.second_opinion.disagreements.length ? (
              <div
                className="table-scroll"
                tabIndex={0}
                role="region"
                aria-label="Second opinion disagreements"
              >
                <table>
                  <thead>
                    <tr>
                      <th>Case</th>
                      <th>Engine</th>
                      <th>Second opinion</th>
                      <th>Rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.second_opinion.disagreements.map((row) => (
                      <tr key={row.hit_id}>
                        <td>
                          <CaseLink row={row} />
                        </td>
                        <td>
                          <DecisionChip decision={row.decision} />
                        </td>
                        <td>
                          <DecisionChip decision={row.llm_decision} />
                          <small>
                            {row.llm_reason_code || "No reason code"}
                          </small>
                        </td>
                        <td>
                          <Rich>{row.rationale}</Rich>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>No disagreements available.</Empty>
            )}
          </section>
          <div className="qa-columns">
            <section className="panel">
              <h2>Near misses</h2>
              <p className="quiet">
                Cleared on name, with high model similarity.
              </p>
              {data.near_misses.map((row) => (
                <article className="qa-row" key={row.hit_id}>
                  <CaseLink row={row} />
                  <p>
                    <DecisionChip decision={row.decision} /> ·{" "}
                    {pct(row.name_similarity)} name similarity
                  </p>
                  <small>
                    Customer: <Rich>{row.customer_name}</Rich> ·{" "}
                    {row.reason_code}
                  </small>
                </article>
              ))}
              {!data.near_misses.length && (
                <Empty>No near misses available.</Empty>
              )}
            </section>
            <section className="panel">
              <h2>Unusual clears / anomalies</h2>
              {data.anomalies.map((row) => (
                <article className="qa-row" key={row.hit_id}>
                  <CaseLink row={row} />
                  <p>
                    <DecisionChip decision={row.decision} /> ·{" "}
                    {pct(row.anomaly)} unusualness
                  </p>
                  <small>{row.reason_code}</small>
                </article>
              ))}
              {!data.anomalies.length && <Empty>No anomalies available.</Empty>}
            </section>
          </div>
          <section className="panel">
            <h2>Review blockers</h2>
            {alerts.loading && <Loading />}
            <ErrorMessage message={alerts.error} retry={alerts.reload} />
            {data.blockers.map((group) => (
              <article className="qa-row" key={group.blocker}>
                <div className="section-bar">
                  <h3 className="question">
                    {group.blocker} · {group.count}
                  </h3>
                  <Link to={`/?blocker=${encodeURIComponent(group.blocker)}`}>
                    Filter queue →
                  </Link>
                </div>
                <div className="blocker-cases">
                  {group.hit_ids.map((id) => (
                    <span key={id}>
                      {alertByHit.has(id) && (
                        <Link
                          to={`/alerts/${alertByHit.get(id)}/${id}`}
                          aria-label={`Open case for ${id}`}
                        >
                          Case →{" "}
                        </Link>
                      )}
                      <Id id={id} />
                    </span>
                  ))}
                </div>
              </article>
            ))}
            {!data.blockers.length && <Empty>No review blockers.</Empty>}
          </section>
          {data.ml_metrics ? (
            <details className="panel">
              <summary>ML validation metrics</summary>
              <pre>{JSON.stringify(data.ml_metrics, null, 2)}</pre>
            </details>
          ) : (
            <p className="quiet">ML validation metrics not connected.</p>
          )}
        </>
      )}
    </main>
  );
}
