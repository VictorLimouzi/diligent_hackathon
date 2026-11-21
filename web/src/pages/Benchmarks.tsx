import { useRequest } from "../api/useRequest";
import { Empty, ErrorMessage, Loading, pct } from "../components/common";

// GET /api/benchmark: written by `make benchmark` (evals/benchmark.py). Kept
// here, not in api/types.ts, because that file mirrors the frozen contract.
interface System {
  id: string;
  name: string;
  group: "casebook" | "baseline" | "ablation";
  note: string;
  right: number;
  unsafe: number;
  false_confirms: number;
  unneeded_reviews: number;
  cost: number;
  score: number;
  decisions: Record<string, number>;
  review_rate: number;
  changed_vs_casebook: number;
  seconds?: number | null;
}
interface Benchmark {
  available: boolean;
  labels: number;
  hits: number;
  all_review_cost: number;
  systems: System[];
  operations: {
    seconds: number;
    runs: number;
    runs_identical: number;
    matches_committed_output: boolean;
    python: { version: string; identical: boolean }[];
    robustness: {
      experiments: number;
      with_changes: number;
      with_new_clears: number;
    } | null;
    wording: {
      rounds: number;
      sentences: number;
      unread: number;
      wrong: number;
    } | null;
  };
}

const GROUPS: [System["group"], string][] = [
  ["casebook", "Casebook"],
  ["baseline", "Simple alternatives"],
  ["ablation", "Casebook with one source of evidence removed"],
];

function Unsafe({ n }: { n: number }) {
  return n ? (
    <span className="flag">
      <span aria-hidden="true">▲</span> {n} unsafe
    </span>
  ) : (
    <span className="flag ok">
      <span aria-hidden="true">✓</span> none unsafe
    </span>
  );
}

function ScoreChart({
  systems,
  labels,
}: {
  systems: System[];
  labels: number;
}) {
  return (
    <figure style={{ margin: 0 }}>
      <div className="bars">
        {GROUPS.map(([group, title]) => {
          const rows = systems.filter((s) => s.group === group);
          if (!rows.length) return null;
          return [
            <p className="group" key={group}>
              {title}
            </p>,
            ...rows.flatMap((s) => [
              <span
                key={`${s.id}-n`}
                className={group === "casebook" ? "name lead" : "name"}
              >
                {s.name}
              </span>,
              <div
                key={`${s.id}-t`}
                className="track"
                title={`${s.name}: score ${s.score}, ${s.right} of ${labels} right, ${s.unsafe} unsafe clearances, cost ${s.cost}. ${s.note}`}
              >
                <div className="bar" style={{ width: `${s.score}%` }} />
              </div>,
              <span key={`${s.id}-v`} className="value">
                <b>{s.score.toFixed(1)}</b>{" "}
                <small>
                  · {s.right}/{labels}
                </small>{" "}
                <Unsafe n={s.unsafe} />
              </span>,
            ]),
          ];
        })}
      </div>
      <div className="axis" aria-hidden="true">
        <div className="ticks">
          <span>0</span>
          <span>25</span>
          <span>50</span>
          <span>75</span>
          <span>100</span>
        </div>
      </div>
      <figcaption className="footnote">
        Decision score: the share of cost saved compared with sending every hit
        to review, on the {labels} practice labels. An unsafe clearance costs
        60; an unneeded review costs 2 or 3. A system that clears one true match
        can score 0 however many others it gets right.
      </figcaption>
    </figure>
  );
}

export function Benchmarks() {
  const { data, loading, error, reload } = useRequest<Benchmark>("/benchmark");
  const casebook = data?.systems?.find((s) => s.group === "casebook");
  const ops = data?.operations;
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Benchmarks</p>
          <h1>How Casebook measures up</h1>
          <p>
            Every system below is scored the way the organisers score: the cost
            matrix on the practice labels, the only hits with known answers.
          </p>
        </div>
      </div>
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && !data.available && (
        <Empty>
          No benchmark has been run yet. Run <code>make benchmark</code> and
          reload.
        </Empty>
      )}
      {data?.available && casebook && ops && (
        <>
          <div className="stat-tiles">
            <section className="panel stat-tile" aria-label="Decision score">
              <small>Decision score</small>
              <b className="big">{casebook.score.toFixed(0)}</b>
              <span className="sub">
                {casebook.right} of {data.labels} practice decisions right
              </span>
            </section>
            <section className="panel stat-tile" aria-label="Unsafe clearances">
              <small>Unsafe clearances</small>
              <b className="big">{casebook.unsafe}</b>
              <span className="sub">true matches or open cases cleared</span>
            </section>
            <section className="panel stat-tile" aria-label="Run time">
              <small>Run time</small>
              <b className="big">{ops.seconds.toFixed(1)} s</b>
              <span className="sub">
                {data.hits} hits, no model, no network
              </span>
            </section>
            <section className="panel stat-tile" aria-label="Repeatability">
              <small>Repeatability</small>
              <b className="big">
                {ops.runs_identical}/{ops.runs}
              </b>
              <span className="sub">runs byte-identical</span>
            </section>
          </div>

          <section className="panel" aria-label="Decision score by system">
            <h2>Decision score by system</h2>
            <ScoreChart systems={data.systems} labels={data.labels} />
          </section>

          <section className="panel">
            <h2>The numbers</h2>
            <div
              className="table-scroll full"
              role="region"
              aria-label="Benchmark results table"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th>System</th>
                    <th className="num">Right of {data.labels}</th>
                    <th className="num">Unsafe clearances</th>
                    <th className="num">Wrong confirmations</th>
                    <th className="num">Unneeded reviews</th>
                    <th className="num">Cost</th>
                    <th className="num">Score</th>
                    <th className="num">Review rate, all hits</th>
                    <th className="num">Decisions that differ</th>
                  </tr>
                </thead>
                <tbody>
                  {data.systems.map((s) => (
                    <tr key={s.id}>
                      <th scope="row">
                        {s.name}
                        <small style={{ display: "block", fontWeight: 400 }}>
                          {s.note}
                        </small>
                      </th>
                      <td className="num">{s.right}</td>
                      <td className="num">{s.unsafe}</td>
                      <td className="num">{s.false_confirms}</td>
                      <td className="num">{s.unneeded_reviews}</td>
                      <td className="num">{s.cost}</td>
                      <td className="num">
                        <b>{s.score.toFixed(1)}</b>
                      </td>
                      <td className="num">{pct(s.review_rate)}</td>
                      <td className="num">{s.changed_vs_casebook}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="footnote">
              "Decisions that differ" counts, over all {data.hits} hits, how
              many decisions are not Casebook's. Sending everything to review
              costs {data.all_review_cost} on the labels; that is score 0.
            </p>
          </section>

          <div className="stat-tiles">
            {ops.wording && (
              <section className="panel stat-tile" aria-label="New wordings">
                <small>New wordings read wrongly</small>
                <b className="big">{ops.wording.wrong}</b>
                <span className="sub">
                  of {ops.wording.sentences.toLocaleString()} generated
                  sentences in {ops.wording.rounds} rounds; {ops.wording.unread}{" "}
                  left unread, which only costs a review
                </span>
              </section>
            )}
            {ops.robustness && (
              <section className="panel stat-tile" aria-label="Rewritten data">
                <small>Rewrites that add no clear</small>
                <b className="big">
                  {ops.robustness.experiments - ops.robustness.with_new_clears}/
                  {ops.robustness.experiments}
                </b>
                <span className="sub">
                  ways of rewriting the data without changing its meaning
                  {ops.robustness.with_new_clears > 0 &&
                    ". The exception swaps a name for a listed variant of it: Casebook applies the name rule literally, as the practice labels do"}
                </span>
              </section>
            )}
            <section className="panel stat-tile" aria-label="Python versions">
              <small>Python versions, same output</small>
              <b className="big">
                {ops.python.filter((p) => p.identical).length}/
                {ops.python.length}
              </b>
              <span className="sub">
                {ops.python.map((p) => p.version).join(", ")}
              </span>
            </section>
            <section className="panel stat-tile" aria-label="Committed output">
              <small>Matches the committed output</small>
              <b className="big">
                {ops.matches_committed_output ? "Yes" : "No"}
              </b>
              <span className="sub">output/dev.jsonl, byte for byte</span>
            </section>
          </div>

          <p className="footnote">
            Read this with care: {data.labels} labelled cases is a small sample.
            The match-score row uses a threshold chosen on these same labels,
            which flatters it. The second-opinion row was given the facts
            Casebook gathered, so it measures judgement on those facts, not a
            separate system.
          </p>
        </>
      )}
    </main>
  );
}
