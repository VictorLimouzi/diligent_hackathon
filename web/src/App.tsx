import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { MOCK } from "./api/client";
import type { Summary } from "./api/types";
import { useRequest } from "./api/useRequest";
import { CommandPalette } from "./components/CommandPalette";
import {
  ErrorMessage,
  Loading,
  pct,
  RecordsProvider,
} from "./components/common";
import { Queue } from "./pages/Queue";
import { Case } from "./pages/Case";
import { QA } from "./pages/QA";
function SummaryStrip() {
  const { data, loading, error, reload } = useRequest<Summary>("/summary");
  return (
    <section className="summary-strip" aria-label="Screening summary">
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && (
        <>
          <div>
            <small>Alerts cleared</small>
            <b>
              {data.alert_status.cleared ?? 0} <span>/ {data.alerts}</span>
            </b>
          </div>
          <div>
            <small>Hit decisions · {data.hits} total</small>
            <span>
              <b>{data.decisions.DIFFERENT_ENTITY ?? 0}</b> different ·{" "}
              <b>{data.decisions.SAME_ENTITY ?? 0}</b> same ·{" "}
              <b>{data.decisions.REVIEW ?? 0}</b> review
            </span>
          </div>
          <div>
            <small>Review rate</small>
            <b>{pct(data.review_rate)}</b>
          </div>
          <div>
            <small>Practice-label score</small>
            <b>
              {data.labels.n
                ? `${data.labels.correct}/${data.labels.n}`
                : "No labels"}
              <span> · {data.labels.false_clears} false clears</span>
            </b>
          </div>
          <div>
            <small>Advisory connections</small>
            <span>
              ML: <b>{data.ml.trained ? "Connected" : "Not connected"}</b>
            </span>
            <span>
              LLM:{" "}
              <b>
                {data.llm.live && !data.llm.stub
                  ? "Connected"
                  : "Not connected"}
              </b>{" "}
              · {data.llm.second_opinions} opinions
            </span>
          </div>
        </>
      )}
    </section>
  );
}
export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
  const location = useLocation();
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
      }
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);
  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById("main-content")?.focus();
  }, [location.pathname]);
  return (
    <RecordsProvider>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <Link className="brand" to="/">
          <span className="brand-mark">C</span>
          <span>
            Casebook<small>Screening workbench</small>
          </span>
        </Link>
        <nav aria-label="Primary">
          <NavLink to="/" end>
            Queue
          </NavLink>
          <NavLink to="/qa">Quality assurance</NavLink>
        </nav>
        <button className="search-trigger" onClick={() => setSearchOpen(true)}>
          Search <kbd>⌘ K</kbd>
        </button>
      </header>
      <SummaryStrip />
      {MOCK && (
        <div className="mock-banner">
          <b>Fixture sample</b> · 11 example alerts; summary totals describe the
          full dataset. Model values and replayed responses are sample data.
          Reviews reset on reload.{" "}
          <Link to="/alerts/A-0DF328/H-ADF453">
            Open the investigation example →
          </Link>
        </div>
      )}
      <div id="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/alerts/:alertId/:hitId?" element={<Case />} />
          <Route path="/qa" element={<QA />} />
          <Route
            path="*"
            element={
              <main className="page">
                <h1>Page not found</h1>
                <Link to="/">Return to the queue</Link>
              </main>
            }
          />
        </Routes>
      </div>
      <footer className="footer">
        Casebook · Deterministic decisions, traceable evidence
      </footer>
      {searchOpen && <CommandPalette close={() => setSearchOpen(false)} />}
    </RecordsProvider>
  );
}
