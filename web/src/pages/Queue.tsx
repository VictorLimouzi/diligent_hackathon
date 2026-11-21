import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import type { AlertRow, AlertStatus, QueueSummary } from "../api/types";
import { useRequest } from "../api/useRequest";
import {
  Empty,
  ErrorMessage,
  Ids,
  Loading,
  pct,
  Rich,
} from "../components/common";

const statusWords: Record<AlertStatus, string> = {
  open: "Open",
  compliance: "To compliance",
  cleared: "Cleared",
};
const statusClass = {
  open: "REVIEW",
  compliance: "SAME_ENTITY",
  cleared: "DIFFERENT_ENTITY",
};
export function Queue() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const status = params.get("status") || "";
  const labelled = params.get("labelled") === "true";
  const blocker = params.get("blocker") || "";
  const update = (key: string, value: string) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  const pathParams = new URLSearchParams({ sort: "priority" });
  if (q.trim()) pathParams.set("q", q.trim());
  if (status) pathParams.set("status", status);
  if (labelled) pathParams.set("labelled", "true");
  const { data, error, loading, reload } = useRequest<AlertRow[]>(
    `/alerts?${pathParams}`,
  );
  const queue = useRequest<QueueSummary>("/queue/summary");
  const rows = useMemo(
    () =>
      (data || []).filter(
        (a) =>
          !blocker ||
          a.blockers.includes(blocker) ||
          a.hit_ids.some((id) =>
            queue.data?.groups
              .find((g) => g.blocker === blocker)
              ?.hit_ids.includes(id),
          ),
      ),
    [data, blocker, queue.data],
  );
  const [selected, setSelected] = useState(0);
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);
  const navigate = useNavigate();
  useEffect(() => {
    setSelected(0);
  }, [q, status, labelled, blocker]);
  useEffect(() => {
    const keydown = (e: KeyboardEvent) => {
      if (
        (e.target instanceof Element &&
          e.target.closest(
            "input, textarea, select, button, a, dialog, [contenteditable]",
          )) ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey
      )
        return;
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        setSelected((current) => {
          const next = Math.max(
            0,
            Math.min(rows.length - 1, current + (e.key === "j" ? 1 : -1)),
          );
          rowRefs.current[next]?.scrollIntoView({ block: "nearest" });
          return next;
        });
      }
      if (e.key === "Enter" && rows[selected]) {
        e.preventDefault();
        navigate(`/alerts/${rows[selected].alert_id}`);
      }
    };
    document.addEventListener("keydown", keydown);
    return () => document.removeEventListener("keydown", keydown);
  }, [rows, selected, navigate]);
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Analyst workbench</p>
          <h1>Screening queue</h1>
          <p className="quiet">
            Resolve the evidence. Keep the decision trace.
          </p>
        </div>
        <span className="quiet">
          <kbd>j</kbd> <kbd>k</kbd> move · <kbd>Enter</kbd> open
        </span>
      </div>
      <details className="panel queue-summary">
        <summary>What is keeping the queue open?</summary>
        {queue.loading && <Loading />}
        <ErrorMessage message={queue.error} retry={queue.reload} />
        {queue.data && (
          <>
            <p>
              <Rich>{queue.data.text}</Rich>
            </p>
            <p className="quiet">
              {queue.data.stub
                ? "LLM not connected · deterministic summary"
                : queue.data.cached
                  ? "Cached summary"
                  : "Queue summary"}
            </p>
            <div className="group-list">
              {queue.data.groups.map((g) => (
                <button
                  key={g.blocker}
                  aria-pressed={blocker === g.blocker}
                  onClick={() =>
                    update("blocker", blocker === g.blocker ? "" : g.blocker)
                  }
                >
                  <span>{g.blocker}</span>
                  <b>{g.count}</b>
                </button>
              ))}
            </div>
          </>
        )}
      </details>
      <section className="panel">
        <div className="filters">
          <label className="search-field">
            Search queue
            <input
              type="search"
              value={q}
              onChange={(e) => update("q", e.target.value)}
              placeholder="Customer, alert, hit name or ID"
            />
          </label>
          <label>
            Status
            <select
              value={status}
              onChange={(e) => update("status", e.target.value)}
            >
              <option value="">All statuses</option>
              {Object.entries(statusWords).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={labelled}
              onChange={(e) =>
                update("labelled", e.target.checked ? "true" : "")
              }
            />
            Practice labelled
          </label>
        </div>
        {blocker && (
          <div className="filter-note">
            Blocker: {blocker}{" "}
            <button onClick={() => update("blocker", "")}>
              Clear blocker filter
            </button>
          </div>
        )}
        <div className="section-bar">
          <span>
            {loading ? "Loading queue" : `${rows.length} alerts shown`}
          </span>
          <span className="quiet">
            Server order · open first, then priority
          </span>
        </div>
        {loading && <Loading />}
        <ErrorMessage message={error} retry={reload} />
        {data && !rows.length && <Empty>No alerts match these filters.</Empty>}
        {!!rows.length && (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Screening queue"
          >
            <table className="queue-table">
              <thead>
                <tr>
                  {[
                    "Customer / alert",
                    "Status",
                    "Hits",
                    "Blockers",
                    "Priority",
                  ].map((label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((a, i) => (
                  <tr
                    key={a.alert_id}
                    ref={(el) => {
                      rowRefs.current[i] = el;
                    }}
                    className={i === selected ? "selected" : ""}
                    onFocus={() => setSelected(i)}
                  >
                    <td>
                      <Link
                        className="primary-link"
                        to={`/alerts/${a.alert_id}`}
                      >
                        {a.customer_name}
                      </Link>
                      <small>
                        {a.alert_id} · {a.entity_type} · {a.screened_at}
                        {a.labelled ? " · Practice label" : ""}
                      </small>
                    </td>
                    <td>
                      <span className={`chip ${statusClass[a.status]}`}>
                        {statusWords[a.status]}
                      </span>
                    </td>
                    <td>
                      {a.hit_count}
                      <details>
                        <summary>View hit IDs</summary>
                        <Ids ids={a.hit_ids} />
                      </details>
                    </td>
                    <td>
                      {a.blockers.length ? (
                        a.blockers.map((b) => (
                          <div key={b}>
                            <Rich>{b}</Rich>
                          </div>
                        ))
                      ) : (
                        <span className="quiet">No blockers</span>
                      )}
                    </td>
                    <td>
                      {a.priority !== null ? (
                        <div className="priority">
                          <span>{pct(a.priority)}</span>
                          <progress
                            max={1}
                            value={a.priority}
                            aria-label={`${a.customer_name} priority`}
                          />
                        </div>
                      ) : (
                        <span className="quiet">Not scored</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
