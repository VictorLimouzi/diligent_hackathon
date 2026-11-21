# Brief: advisory models (`ml/`)

Read `docs/briefs/COMMON.md` and `contracts/python_interfaces.md` first. You own `ml/` only. scikit-learn, numpy, scipy, joblib are installed in the repo venv.

## Goal

A different algorithm for each task it suits. None of them decides a hit; they rank, flag and search. With only 50 human labels, every model here is trained on signals the data gives for free, and each must report an honest validation number.

## Deliverables (exact output shapes in `contracts/python_interfaces.md` and `contracts/types.ts::MlScores`)

1. **Name similarity** (`ml/names.py`): character n-gram TF-IDF cosine plus string-distance features (edit distance ratio, Jaro-Winkler, token-set overlap, length gap) into a calibrated logistic regression. Positive pairs from the data: alert screened name ↔ the resolved customer's names, verified name ↔ declared names of the same person, names across records of one person (`data.person`), hit name ↔ its aliases, pairs inside a `name_variants.json` group. Negatives: random pairs, and hard negatives that share exactly one token. Split by person/alert so the same name never sits on both sides. Report held-out accuracy and AUC.
   - `name_similarity` per hit = model score of the customer name used by the engine (`features["customer_name"]`) against the best of the hit's names.
   - `near_miss` = the engine's reason codes include `NAME_MISMATCH_VERIFIED` and the score is above a threshold you choose and justify from the score distribution.
2. **Customer retrieval** (`ml/search.py`): `CustomerSearch` with TF-IDF character n-grams and sparse cosine nearest neighbours over all customer names (72k). Must answer in well under 100 ms after a one-time build of a few seconds; tolerate typos and swapped order. Also report resolver recall: for alerts the engine resolved without a link, is the engine's record in your top 10?
3. **Review ranking** (`ml/ranker.py`): `HistGradientBoostingClassifier` + isotonic calibration, predicting SAME vs DIFFERENT on hits the engine decided, to score REVIEW hits (`priority`). The trap: the engine's labels are a function of the check outcomes, so a model given those just relearns the rules and says nothing about REVIEW hits, where the deciding fact is missing. Train with the deciding fields **masked the way they are missing in REVIEW hits** (look at the REVIEW feature rows first: typically `dob_outcome` unknown and no hit birth date), so it learns from soft signals: name rarity (how many customers share the name parts; how many search results exist for the hit name), learned name similarity, match score, category, alert size, number of shared countries, aliases. Validate with grouped cross-validation by alert on the masked rows and report AUC as `ranker_masked_auc`. If the AUC is near 0.5, say so in metrics and NOTES rather than dressing it up.
   - `drivers`: top signed contributions per hit, with human-readable labels (permutation-style or a companion linear model; keep it cheap and say which).
4. **Text routing** (`ml/router.py`): TF-IDF + linear classifier over notes and search snippets, weak labels from the regex extractors in `casebook/extract.py` (`parse_kyc_note`, `lived_countries`, `parse_snippet`): identity-check note / residence note / other; snippet with a listing phrase / benign. Output the texts the classifier is confident carry a fact but the extractor returned nothing for (`routing.json`). Expect very few on the dev data; that is a fine result.
5. **Unusual cases** (`ml/anomaly.py`): `IsolationForest` over numeric feature rows per decision group, `anomaly` scaled to 0..1.
6. `ml/train.py`: one command that builds everything and writes `scores.json`, `metrics.json`, `routing.json`. Deterministic (fixed seeds). Under two minutes on a laptop.

## Acceptance

- `$VENV -m ml.train <data_folder> --out out/ml` runs clean; `scores.json` has an entry for all 819 hits matching `MlScores` (priority only for REVIEW hits, null otherwise).
- `$VENV -m pytest ml/tests` passes: shape tests on a tiny invented folder (see `tests/test_casebook.py::Policy` for how to build one), a determinism test, and a test that `CustomerSearch` finds a name with a typo.
- `tests/test_casebook.py` still passes untouched.
- `ml/NOTES.md` as described in COMMON.md, with the metrics and a frank read of which models are useful and which are weak.
