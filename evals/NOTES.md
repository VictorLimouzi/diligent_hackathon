# Evals handoff

## Built

Standard-library-only package confined to `evals/`:

- `perturb.py`: 30 deterministic representation experiments plus five malformed-data probes; independent date parsing and country equivalences; exact mutation identities and original-value guards; real temporary copies of the entire source folder.
- `run.py`: baseline and perturbed `decide_all(Data(folder))` runs, per-alert caught-error capture, escaped-error capture, decision diffs, omitted/extra result checks, and strict affected-hit `REVIEW` checks for malformed inputs. Matches by `(alert_id, hit_id)` to avoid collapsing distinct alert occurrences.
- `minimize.py`: searches eligible one-field changes against the relevant alert, restores exploratory edits, and verifies each accepted reproduction using the complete engine on a real perturbed temp folder. Exploration and real-folder verification run in separate memory phases.
- `test_evals.py`: 23 unit tests on a tiny invented JSONL folder, including transformation semantics, deterministic shuffles/variants, both filename directions, equivalent-rule removal, identifier preservation, source immutability, replay guards, error reporting, and compressed-manifest determinism.
- `report.json` and `report.records.json.gz`: final dev sweep and complete touched-record manifest. The report caps examples at 20 per category but never truncates adverse findings.
- `reproductions.json`, `reproductions/*.json`, `diagnostics.json`: verified original records, single-field replay specifications, and engine traces for all 13 distinct adverse outcomes. Some outcomes have two category-specific entries because they violate two assertions.
- `FINDINGS.md`: every new clear and confirm, malformed failures, reproduction commands, and source-level causes. `run.log`, `minimize.log`, and `runtime.json` retain execution evidence.

## Run

Use Python 3.10+ from the repository root; no installation or third-party packages are needed.

```sh
export CASEBOOK_DATA=data/screening/dev
python3 -m evals.run "$CASEBOOK_DATA" --seed 0
python3 -m evals.run "$CASEBOOK_DATA" --only names_variants --seed 0 \
  --report evals/names-report.json
python3 -m unittest discover -s evals

# Rebuild all minimal proofs from the report:
python3 -m evals.minimize "$CASEBOOK_DATA" --report evals/report.json

# Run one minimal proof:
python3 -m evals.run "$CASEBOOK_DATA" \
  --replay evals/reproductions/whitespace_nbsp-new_clears-H-A9AA55.json \
  --report evals/replay-report.json
```

`--only` choices are listed in `python3 -m evals.run --help`. A seed controls variant selections, record/key order, and the selected alert for each malformed probe; fixed-format transforms are deterministic without consuming randomness. The report defaults to `evals/report.json`; `--report` also determines the compressed companion's basename. Read a complete touched-record list with standard-library `gzip.open(..., "rt")` and `json.load`.

Exit 1 means a new clear, caught/escaped exception, duplicate output, omitted/unexpected hit, source modification, or malformed affected hit that is not `REVIEW`. New confirms and lost clears are reported without independently failing the run. An expected synthetic unknown hit is allowed as an extra output only when it appears as `REVIEW`. A baseline error is a failure and prevents misleading perturbation comparisons.

## Verified on the supplied dev data

- Baseline: all 440 alerts and 819 hits processed; 463 clears, 125 confirms, 231 reviews; no errors.
- `PYTHONDONTWRITEBYTECODE=1 python3 eval.py "$CASEBOOK_DATA"`: **50/50** decisions; 50 accepted reasons, 50 accepted customers, 50 exact evidence sets; zero false clears/confirms on practice labels.
- `CASEBOOK_DATA=... PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`: **31 tests passed**, including `Golden.test_decisions_unchanged`. The scored-output hash remains `e0b838e35a04bd1392c613b26b051efa5baa717b5985a91009c110a26b6eeeb2`.
- `python3 -m unittest discover -s evals`: **23 tests passed**.
- Full seed-0 sweep: **7 representation-induced new clears, 4 new confirms**, plus **1 malformed-date new clear** and **1 omitted hit**. No escaped or caught exceptions. Exit **1 is expected** and is a finding, not an unfinished run.
- All 13 distinct adverse hit/experiment outcomes have real-folder-verified one-field reproductions; the minimizer completed with exit 0 and no unresolved reproductions.
- SHA-256 hashes of every source file are recorded before the run and compared afterwards; the source folder is unchanged. Bytecode writing is disabled for imported engine modules. All generated datasets live in automatically cleaned temporary directories.

The final full run took **252.33 seconds (4.2 minutes)** and exited **1**, as recorded in `runtime.json`. All 33 experiments common to the initial and final sweeps had identical counts and adverse outcomes. A separate final-code `names_variants --seed 0` rerun matched its entire experiment report and complete touched-record manifest exactly. The one-field replay CLI independently produced exactly one new clear and the expected exit 1. `determinism.json`, `cli-checks.json`, `repeat.log`, and `replay.log` retain those checks. The report is deterministic for a given source, engine, Python environment, and seed; fixed seed is not a guarantee across future engine revisions. The initial/final comparison accounts for the added filename/rules combinations and the explicit restriction of typography edits to human-readable fields.

## Limits and integration requests

- The failures remain in the engine intentionally. No files outside `evals/` were edited, and nothing was committed, pushed, or switched.
- Malformed probes target one seeded alert each, not every possible corrupt row. Null names target the selected alert and its hit profiles; invalid dates target alert dates and the hit DOB fields. Customer/KYC loader-type corruption is not exhaustively enumerated. Error capture covers both loader exceptions and engine-caught exceptions, and both paths have unit tests.
- Country perturbations use 56 explicit country/code/nationality equivalences; other countries are retained. Numeric conversion is limited to `match_score` and `hit_count` so phone numbers and identifiers keep their semantics. Name variants consume the documented `name_variants` list-of-lists format; other custom rule-file schemas are not rewritten.
- Partial dates and timestamps are left intact. Text wrapping retains paragraph and sentence boundaries and English month capitalization. Typography never changes email/login identifiers, document IDs, URLs, or other join keys.
- Optional rule removal is skipped with an explicit explanation if supplied rules differ from the bundled fallback. With this dev folder, rules are equivalent. Canonical filename and rules-present experiments can be no-ops on already canonical input; invented-fixture tests exercise both directions and actual absence/presence.
- The minimizer searches eligible single-field changes that do not require rebuilding cached indexes during exploration, then validates them with a fully rebuilt `Data`. A future composite-only failure or loader exception may need manual minimization; it is reported as unresolved instead of receiving an invented proof. All findings in this run were minimized successfully.
- Results compare decisions; unchanged-decision reason/evidence changes are not counted as changed hits. The existing golden test protects the unperturbed scored output separately. Perturbation outcomes on unlabelled hits are stability evidence, not additional ground-truth labels.

Requests for the engine owner, **not implemented here**: normalize category/report whitespace before fact extraction; investigate listed variants combined with fuzzy spelling and length thresholds; reject malformed DOB evidence conservatively instead of removing the name-plus-DOB location safeguard; recover or explicitly review a profile when an alert's hit list is absent. Source functions and exact cases are in `FINDINGS.md`. No contract change is required by this harness.
