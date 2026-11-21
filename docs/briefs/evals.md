# Brief: robustness harness (`evals/`)

Read `docs/briefs/COMMON.md` first. You own `evals/` only. Standard library Python only (the engine's own constraint); run with `python3`.

## Goal

The engine scores 50/50 on 50 practice labels, which says little about the unseen set it will be scored on. Build a harness that perturbs the dev data in ways the unseen set plausibly differs, re-runs the engine, and reports every case where a perturbation makes things **less safe**. The worst outcome is a false clear: a hit that was `SAME_ENTITY` or `REVIEW` becoming `DIFFERENT_ENTITY` although the perturbation did not change who anyone is.

## How

- Load a data folder, write a perturbed copy to a temp folder, run `casebook.engine.decide_all(casebook.load.Data(folder))` on both, and diff decisions per hit.
- Each perturbation must be **meaning-preserving** and deterministic (seeded), and say which records it touched. Suggested set:
  1. Date formats: rewrite customer, KYC and alert dates among `YYYY-MM-DD`, `DD/MM/YYYY`, `D Month YYYY`, `Month D, YYYY`.
  2. Country forms: swap a country name for its ISO code or nationality (`Germany` ↔ `DE` ↔ `German`) on customer records.
  3. Name noise the policy says must still match: case changes, accents added or stripped, hyphen ↔ space, token order (`First Last` ↔ `LAST, First`), a listed variant from `name_variants.json`.
  4. Field shape: multi-valued fields (`hit_ids`, `aliases`, `dobs`, `countries`, `addresses`, `categories`) as JSON lists instead of `;`-separated strings; numbers as numbers; empty strings instead of missing keys; shuffled key order; shuffled row order.
  5. File names: `kyc_checks.jsonl` ↔ `kyc.jsonl`, `search_results.jsonl` ↔ `search.jsonl`, with and without `name_variants.json` present.
  6. Whitespace and Unicode: leading/trailing spaces, non-breaking spaces, curly apostrophes, NFC ↔ NFD.
  7. Free text: re-wrap notes and snippets (double spaces, trailing period removed, sentence case of month names kept).
- Also a **crash** sweep: malformed rows (missing `hit_ids`, unknown hit id, null names, dates like `0000-00-00`, an alert pointing at a missing account). The engine must not raise; affected hits must come out `REVIEW`, never cleared.

## Output

- `python3 -m evals.run <data_folder> [--only NAME] [--seed N]` prints, per perturbation: hits changed, **new clears** (non-clear → `DIFFERENT_ENTITY`), new confirms, lost clears (became `REVIEW`), crashes; exit code 1 if any new clear or crash.
- `evals/report.json` with the same numbers plus up to 20 example hit ids per category, each with before/after decision and reason code and the perturbed record ids.
- `evals/FINDINGS.md`: every new clear, new confirm or crash you found, with a minimal reproduction (the one record and the one change) and your reading of the cause in the engine source (file and function). Do **not** fix the engine; findings are the deliverable.
- Unit tests for the perturbation functions themselves (`python3 -m unittest discover -s evals`), using a tiny invented folder like the one built in `tests/test_casebook.py::Policy`.

## Acceptance

- The harness runs end to end on the dev data in a few minutes and is deterministic for a given seed.
- Each perturbation is verified meaning-preserving by its own unit test (e.g. parse the rewritten date back and compare).
- `evals/NOTES.md` as described in COMMON.md.
