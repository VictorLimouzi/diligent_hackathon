# ml/ notes

Advisory models around the engine. None of them decides a hit, and nothing here is imported by `casebook/`.

## Run and test

```sh
VENV=.venv/bin/python
$VENV -m ml.train <data_folder> --out out/ml     # scores.json, metrics.json, routing.json
$VENV -m pytest ml/tests
```

`ml.train` takes about 30 seconds on the dev data (load and decide 5 s, name model 5 s, search 4 s, ranker 6 to 9 s, router 8 s). It is deterministic: two runs on the dev data gave byte-identical files.

## What is here

| File | Job |
|---|---|
| `textutil.py` | Name folding, edit distance, Jaro-Winkler |
| `names.py` | Name similarity: 9 string features into a logistic regression with sigmoid calibration |
| `search.py` | `CustomerSearch`: TF-IDF character 2- and 3-grams, sparse cosine |
| `ranker.py` | Review priority: `HistGradientBoostingClassifier` + isotonic calibration, permutation-style drivers |
| `router.py` | Notes and snippets the regex extractors did not read: TF-IDF + logistic regression on weak labels |
| `anomaly.py` | `IsolationForest` per decision group, min-max scaled to 0..1 |
| `train.py` | The one command |
| `tests/` | 9 tests on an invented folder of 80 customers |

## Metrics on the dev data (`out/ml/metrics.json`)

| Number | Value |
|---|---|
| `name_model_accuracy` (held out) | 0.9751 |
| `name_model_auc` (held out) | 0.9747 |
| `name_model_brier` | 0.0231 |
| `near_miss_count` | 4 of 137 hits cleared on name, threshold 0.5 |
| `ranker_masked_auc` | 0.7307 (repeats 0.706 to 0.772; 95% interval 0.654 to 0.824) |
| `ranker_all_decided_auc` | 0.9567, inflated by leakage, not to be quoted |
| `ranker_review_vs_training_auc` | 0.8933 |
| `resolver_recall_at_10` | 0.5068 |
| `resolver_recall_at_10_counting_ties` | 0.9863 |
| `resolver_recall_at_10_with_screened_details` | 0.9863 |
| `router_notes_routed` / `router_snippets_routed` | 5 / 5 |

## Each model, frankly

### Name similarity: useful

- **Pairs.** 4,913 positives from the five sources in the brief, an equal number of random negatives and of negatives that share exactly one word. Names are grouped so that no name is on both sides of the split (`name_model_names_on_both_sides` is 0). Trained on 10,761 pairs, measured on 3,978, then refitted on all.
- **Where it is weak.** Hit name against alias: 0.30 accuracy on 122 held-out pairs. Many aliases are a different name altogether, which no string model should call alike. Aliases in another script, nicknames, trading names and acronyms are left out of training for the same reason. The headline accuracy moves with how many alias pairs land in the test side.
- **Known label noise.** About 19 of the 2,900 verified-versus-declared positives are two different people on one record. I left them in.
- **What the score is.** How alike two names are written, relative to a training mix that is one third positives. It is not the probability that two people are the same.
- **Near-miss threshold 0.5.** Scores of the 137 hits cleared on name are bimodal: 133 are below 0.47 and 4 are above 0.56. The four pair a full name with a short form of it (three customers, one with two hits). On held-out pairs, 0.3% of one-shared-word negatives score above 0.5.
- **Caveat.** I changed the feature set twice after looking at scores on the dev hits (initials dropped; word alignment so a typo does not reorder the name). The held-out pair metrics were not used for those choices, but I had seen the near-miss list.

### Customer search: useful

- Build is about 1 second for 72,265 records; a query takes about 6 ms (10 ms worst of 200).
- Typos and swapped order work.
- Name-only recall@10 is 0.51 because many customers carry exactly the same name, so the engine's record is often tied out of the top 10. Counting ties it is 0.99.
- `query(q, k)` is the contract. Optional `dob`, `country`, `city` only reorder records whose name scores are equal; with the screened details the engine's record is in the top 10 for 144 of 146 alerts. The two misses have a typo in the screened name that puts closer-spelled namesakes first (`A-6EA26E`, `A-F1312C`).
- The server does not pass those details today. See requests below.

### Review ranker: modest signal, large caveats

- **The leak, measured.** With soft signals only, a model on all 588 decided hits scores AUC 0.957. That number is the trap: most decided hits are settled by a country or name contradiction, and "countries in common" and name similarity give those away.
- **What I did instead.** Training rows are the decided hits on individuals that would have been REVIEW had the hit's date of birth and dated reports been missing (`would_be_review` in `ranker.py`): 155 rows, 84 SAME and 71 DIFFERENT, from 146 alerts. No check outcome and nothing derived from the hit's date of birth is a feature. `n_search_results` from the engine is also masked, because the engine only reads search results when the profile has no date of birth; the ranker counts results from the search file itself.
- **The honest number.** `ranker_masked_auc` is 0.73 on those rows, cross-validated by alert, five repeats. On 155 rows the interval is wide (0.65 to 0.82). Out-of-fold Brier is 0.214 against 0.248 for the base rate.
- **What carries it.** Name similarity (0.67 alone), match score (0.63 alone), listing category and customer birth year. Category and birth year may be artefacts of how the data was generated; I cannot tell.
- **Transfer is untested and doubtful.** The model is trained on hits that have a date of birth and applied to hits that do not. A classifier tells the two groups apart with AUC 0.89, so they differ a lot (REVIEW hits have fewer aliases, fewer profile notes, larger alerts). Nothing in the data can check whether the ranking holds across that gap. Treat `priority` as an ordering hint, not a probability.
- **Hits it does not represent.** 20 of the 231 REVIEW hits are outside the training population: 12 where name and birth date agree but countries differ, the rest ambiguous customers. They are scored anyway, and the score ignores the agreeing birth date, so it will undersell them. The blocker label is the better guide there.
- **Ties.** Isotonic calibration on 155 rows leaves 14 distinct priority values across 231 hits; 46 hits share the lowest (0.0175).
- **Drivers.** Permutation-style on the calibrated model: each feature of a hit is replaced by every training row's value in turn, and the mean change in priority is the effect. Top 4 per hit, effects under 0.005 dropped.
- **Hyperparameters** were set once and not tuned.
- **Small data.** Under 40 review-like rows the ranker trains on all decided hits and says so in `ranker_training_population`. Under 10 rows it is not trained: every REVIEW hit gets the share of SAME among decided hits, and `ranker_trained` is false.

### Text router: found real gaps, on very few texts

- **Notes.** 5 routed, all genuine: residence sentences without any of the extractor's verbs, for example "Canada was the location of the client's career and housing from 2010 to 2013." (`NOTE-415CEF`, `NOTE-C0836A`, `NOTE-092B78`, `NOTE-A053BB`, `NOTE-951476`). I read all five.
  None of the five customers is on a dev alert, so no dev decision is affected.
- **Snippets.** 5 routed, all with a listing phrase and a country but no age the extractor could read (`SR-7CD21B`, `SR-DA846E`, `SR-1FE05D`, `SR-6D9F2B`, `SR-CB4B6B`). `SR-6D9F2B` is the one the engine counts as about the hit for `H-8CC0BB`, a REVIEW hit blocked by "no birth date to compare"; reading its age (21 in October 1996) would give that hit a birth year.
- **The validation numbers measure agreement with the regexes, not truth.** Notes: accuracy 0.9994 but macro F1 0.67, because after grouping identical sentences only 14 everyday notes remain and most are misread out of fold. None of them was routed, since routing needs 0.9 confidence. Snippets: 1.0, which only says the classifier relearned the listing phrases.
- Class weighting raised note macro F1 to 0.81 but routed only 2 of the 5 genuine notes, so I left it off.

### Anomaly: minimal, unvalidated

One `IsolationForest` per decision group over the numeric feature row plus name similarity, unknowns set to the group median, no tuning. There is no ground truth to validate it against. Groups under 5 hits get `null`.

## Verified

- `ml.train` on the dev data: runs clean, 819 entries in `scores.json`, 231 with a priority (all REVIEW), 588 null.
- Determinism: two runs, identical SHA-256 for all three files.
- `$VENV -m pytest ml/tests`: 9 passed in 10 s (shapes, near-miss rule, metrics, routing keys, determinism, typo and swapped-order search, tie-break, a one-hit folder that forces every fallback).
- `CASEBOOK_DATA=<dev folder> python3 -m unittest discover -s tests`: 31 tests OK, including the golden hash.
- Only `ml/` was changed.

## Not done, not verified

- No model files are saved; the server only needs the JSON.
- Not run against the real data-room folder: this worktree's loader predates the fix for its nested alerts.
- The server integration (`server/advisory.py`, `server/state.py`) was read, not run.
- A REVIEW hit with no feature row (profile missing, or engine error) gets `priority: null`.

## Requests for other streams

- `server/advisory.py::search_customers` could pass the alert's `dob`, `country` and `city` to `CustomerSearch.query` when the search starts from an alert; that lifts top-10 recall from 0.51 to 0.99.
- `casebook/extract.py::_LIVED` misses "household", "career", "housing", "life and labor", "base for". The five routed notes show the phrasings. This matters beyond routing: a residence country the engine does not read can turn a shared country into a `LOCATION_MISMATCH`, which clears a hit. It changes nothing on the dev data (none of the five customers is on an alert), but it could on other data, so whoever owns the engine should look.
