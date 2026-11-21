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
import { Benchmarks } from "./pages/Benchmarks";
function SummaryStrip() {
  const { data, loading, error, reload } = useRequest<Summary>("/summary");
  const same = data?.decisions.SAME_ENTITY ?? 0;
  const different = data?.decisions.DIFFERENT_ENTITY ?? 0;
  const review = data?.decisions.REVIEW ?? 0;
  return (
    <section className="summary-strip" aria-label="Screening summary">
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && (
        <>
          <div>
            <small>Hits decided</small>
            <b className="big">{data.hits}</b>
            <span className="sub">
              {data.alerts} alerts · {data.alert_status.cleared ?? 0} fully
              cleared
            </span>
          </div>
          <div className="mix">
            <small>Decision mix</small>
            <div
              className="mixbar"
              role="img"
              aria-label={`${same} confirmed, ${different} cleared, ${review} for review`}
            >
              <span
                className="seg fill-same"
                style={{ flexGrow: same }}
                title={`Confirmed as the customer: ${same}`}
              />
              <span
                className="seg fill-different"
                style={{ flexGrow: different }}
                title={`Cleared as a different entity: ${different}`}
              />
              <span
                className="seg fill-review"
                style={{ flexGrow: review }}
                title={`Left for review: ${review}`}
              />
            </div>
            <ul className="legend">
              <li>
                <i className="sw fill-same" /> Confirmed <b>{same}</b>
              </li>
              <li>
                <i className="sw fill-different" /> Cleared <b>{different}</b>
              </li>
              <li>
                <i className="sw fill-review" /> Review <b>{review}</b>
              </li>
            </ul>
          </div>
          <div>
            <small>Review rate</small>
            <b className="big">{pct(data.review_rate)}</b>
            <span className="sub">of hits need a person</span>
          </div>
          <div className="good">
            <small>Practice labels</small>
            <b className="big">
              {data.labels.n
                ? `${data.labels.correct}/${data.labels.n}`
                : "No labels"}
            </b>
            <span className="sub">
              {data.labels.false_clears} unsafe clearances
            </span>
          </div>
          <div>
            <small>Advisory layers</small>
            <div className="conn-list">
              <span className={data.ml.trained ? "conn on" : "conn"}>
                ML models {data.ml.trained ? "connected" : "not connected"}
              </span>
              <span
                className={data.llm.live && !data.llm.stub ? "conn on" : "conn"}
              >
                LLM{" "}
                {data.llm.live && !data.llm.stub
                  ? "connected"
                  : "not connected"}{" "}
                · {data.llm.second_opinions} second opinions
              </span>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try {
      return localStorage.getItem("casebook-theme") === "light"
        ? "light"
        : "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("casebook-theme", theme);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")] as const;
}
export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
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
          <NavLink to="/benchmarks">Benchmarks</NavLink>
        </nav>
        <button className="search-trigger" onClick={() => setSearchOpen(true)}>
          Search <kbd>⌘ K</kbd>
        </button>
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          aria-label={`Use the ${theme === "dark" ? "light" : "dark"} theme`}
          title={`Use the ${theme === "dark" ? "light" : "dark"} theme`}
        >
          <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
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
          <Route path="/benchmarks" element={<Benchmarks />} />
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
