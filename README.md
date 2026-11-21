# Casebook: same person or not?

Casebook decides, for every watchlist hit, whether it is the customer: `DIFFERENT_ENTITY`, `SAME_ENTITY` or `REVIEW`, with a reason and the record IDs it rests on.

The repository has two parts:

| Part | What it is | Where |
|---|---|---|
| **The scored system** | A deterministic rule engine that applies Diligent's screening SOP. Python standard library only, no model, no network. | [`system/`](system/), [`output/dev.jsonl`](output/dev.jsonl) |
| **The analyst app** | A workbench around the engine's decisions: ML ranking, LLM agents and a review UI. Advisory only; it can never set or change a decision. | `server/`, `web/`, `ml/`, `agents/` |

## The scored system

```sh
system/run <data folder> <output file>
python3 data/screening/validate.py --data <data folder> --output <output file> --score <data folder>/practice_labels.jsonl
```

[`system/README.md`](system/README.md) explains the architecture, how the customer and the listed person are found, how evidence is represented, why results are identical from run to run, and three worked cases.

On the development set: 819 lines, 0 problems, 50 of 50 practice decisions right, no unsafe clearances, decision score 100. A run takes about five seconds.

## The analyst app

One rule shapes it: **the engine is the only thing that decides a hit.** Models rank, explain, flag and propose.

- **What would settle it.** For each `REVIEW`, the engine re-runs the policy on hypothetical facts and says which single fact would flip the hit, and to what.
- **ML, one algorithm per task** (`ml/`, scikit-learn):
  - name similarity (string features into a calibrated logistic regression), which flags name clears that look close;
  - customer search (character n-gram TF-IDF with sparse nearest neighbours);
  - review-queue ranking (gradient boosting, trained with the deciding fields masked);
  - text routing (a linear classifier that spots fact-bearing text the patterns did not read);
  - unusual-case sampling (isolation forest).
- **LLM agents** (`agents/`, Anthropic API): an investigator that looks through the customer's own records with read-only tools and drafts the request to the customer; case chat; a case note checked against the trace; and a second opinion on every hit whose disagreements go to the QA page. Watchlist text is passed to the model as untrusted data.
- **UI** (`web/`, React): review queue, case view with the comparison table and one-click source records, agent timeline, analyst decision with audit history, and a QA page.

### Run it

Needs Python 3.10+, Node 24+, and the development data in `data/screening/dev` (not in this repository).

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make ml                 # train the advisory models         -> out/ml/
make second-opinion     # LLM second opinions (optional)    -> out/llm/
make web                # build the frontend                -> web/dist/
make serve              # http://localhost:8000
```

Put `ANTHROPIC_API_KEY=...` in `.env` for live LLM output. Without it every LLM feature answers from its cache or from a labelled offline stand-in.

### Tests

```sh
make test                              # engine, server, models, agents
python3 -m evals.run data/screening/dev    # robustness sweep
```

`evals/` rewrites the data in ways that keep its meaning (date formats, country codes, name order, odd spaces, malformed rows) and reports any decision that becomes less safe. Its findings are in [`evals/FINDINGS.md`](evals/FINDINGS.md); the faults it found are fixed, and the one left on purpose is explained in `system/README.md`.

## Layout

```
system/     the scored system: run, README.md, casebook/ (the engine)
output/     dev.jsonl, the system's predictions on the development set
server/     FastAPI app serving decisions, trails, advisory layers and the audit log
web/        React frontend
ml/         advisory models
agents/     LLM analysis and agents
evals/      robustness harness
contracts/  API types shared by server and frontend
tests/      engine tests, including a hash that pins the development decisions
```
