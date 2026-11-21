"""Review ranking: how likely is a REVIEW hit to be the customer?

The engine's SAME / DIFFERENT labels are a function of its check outcomes, so
a model shown those outcomes only relearns the rules, and says nothing about
REVIEW hits, where the deciding fact is missing. So:

1. No check outcome and no fact derived from the hit's date of birth is a
   feature (`MASKED` below). The model sees soft signals only (`FEATURES`).
2. It is trained on the decided hits that *would have been REVIEW* had those
   facts been missing, as they are for the REVIEW hits: individuals whose name
   does not contradict, whose countries do not contradict, and whom only a
   date of birth (or a dated report) settled. `would_be_review` is that test.
   Hits the rules still settle after masking (a country or name contradiction,
   a company with name and country) are left out: on them a model would score
   well by relearning the rule, which is the leak this file exists to avoid.
3. It is validated by cross-validation grouped by alert on exactly those rows.

HistGradientBoostingClassifier, then isotonic calibration fitted on the
out-of-fold scores. `drivers` are permutation-style: each feature of a hit is
replaced in turn by the values seen in training, and the mean change in the
calibrated priority is that feature's signed effect.
"""
from collections import Counter

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

from casebook.load import text

from . import SEED
from .textutil import key, tokens

# Fields of the engine's feature row that the ranker must not see: the check
# outcomes, and everything that exists only when the hit's date of birth is known.
MASKED = ["name_outcome", "dob_outcome", "location_outcome", "time_outcome", "type_mismatch",
          "hit_dob_origin", "hit_dob_count", "min_year_gap", "n_search_about_hit", "n_search_results"]

# name, label shown to the analyst, kind
FEATURES = [
    ("name_similarity", "Learned similarity of the two names", "score"),
    ("match_score", "Screening match score", "number"),
    ("alert_hit_count", "Hits on this alert", "number"),
    ("n_candidates", "Customer records that fit the alert", "number"),
    ("hit_alias_count", "Aliases on the hit", "number"),
    ("n_customer_countries", "Countries tied to the customer", "number"),
    ("n_hit_countries", "Countries tied to the hit", "number"),
    ("n_shared_countries", "Countries in common", "number"),
    ("hit_has_profile_notes", "Hit has profile notes", "flag"),
    ("has_dated_report", "Hit has a dated report", "flag"),
    ("customer_birth_year", "Customer's year of birth", "year"),
    ("hit_category", "Listing category", "category"),
    ("hit_gender", "Gender on the hit", "category"),
    ("customer_name_count", "Customers with exactly this name", "of_customers"),
    ("customer_rarest_word", "Customers sharing the rarest word of the name", "of_customers"),
    ("hit_name_count", "Customers with exactly the hit's name", "of_customers"),
    ("hit_rarest_word", "Customers sharing the rarest word of the hit's name", "of_customers"),
    ("hit_search_results", "Search results for the hit's name", "number"),
    ("hit_namesakes", "Hits in this file with the same name", "number"),
    ("customer_words", "Words in the customer's name", "number"),
    ("hit_words", "Words in the hit's name", "number"),
]
NAMES = [f[0] for f in FEATURES]
_CATEGORICAL = [i for i, f in enumerate(FEATURES) if f[2] == "category"]
_MIN_ROWS = 40          # fewer review-like rows than this, or one class only: see `train`
_REPEATS = 5
_TOP_DRIVERS = 4
_MIN_EFFECT = 0.005


def would_be_review(result):
    """Would this hit be REVIEW if the hit's date of birth and dated reports were missing?

    Mirrors casebook/policy.py with the date-of-birth and time checks set to unknown.
    """
    f = result["features"]
    if f["type_mismatch"] or f["name_outcome"] == "contradict" or f["location_outcome"] == "contradict":
        return False                      # still cleared
    if "DECEASED" in result["reason_codes"]:
        return False                      # still cleared
    if f["company"] and f["name_outcome"] == "agree" and f["location_outcome"] == "agree":
        return False                      # still confirmed
    return True


class NameStats:
    """How common names and their words are among the customers and the hits."""

    def __init__(self, data):
        self.names, self.words = Counter(), Counter()
        for c in data.customers.values():
            t = tokens(text(c.get("full_name")))
            self.names[" ".join(sorted(t))] += 1
            self.words.update(set(t))
        self.hits = Counter(key(text(h.get("name"))) for h in data.hits.values())
        self.customers = len(data.customers)

    def name_count(self, name):
        return self.names[key(name)]

    def rarest_word(self, name):
        return min((self.words[w] for w in tokens(name)), default=0)


def soft_row(data, result, stats, name_similarity):
    """The ranker's view of one hit: raw values keyed by feature name. None is 'not known'."""
    f = result["features"]
    return {
        "name_similarity": name_similarity,
        "match_score": f["match_score"],
        "alert_hit_count": f["alert_hit_count"],
        "n_candidates": f["n_candidates"],
        "hit_alias_count": f["hit_alias_count"],
        "n_customer_countries": f["n_customer_countries"],
        "n_hit_countries": f["n_hit_countries"],
        "n_shared_countries": f["n_shared_countries"],
        "hit_has_profile_notes": bool(f["hit_has_profile_notes"]),
        "has_dated_report": bool(f["has_dated_report"]),
        "customer_birth_year": f["customer_birth_year"],
        "hit_category": f["hit_category"] or "",
        "hit_gender": f["hit_gender"] or "",
        "customer_name_count": stats.name_count(f["customer_name"]),
        "customer_rarest_word": stats.rarest_word(f["customer_name"]),
        "hit_name_count": stats.name_count(f["hit_name"]),
        "hit_rarest_word": stats.rarest_word(f["hit_name"]),
        # counted from the search file, not from the engine's trace: the engine only
        # looks at search results when the profile gives no date of birth
        "hit_search_results": len(data.search_results(f["hit_name"])),
        "hit_namesakes": stats.hits[key(f["hit_name"])],
        "customer_words": len(tokens(f["customer_name"])),
        "hit_words": len(tokens(f["hit_name"])),
    }


def show(name, value, n_customers):
    """A feature value as an analyst would read it."""
    kind = next(k for n, _, k in FEATURES if n == name)
    if value is None or value == "":
        return "not given"
    if kind == "flag":
        return "yes" if value else "no"
    if kind == "score":
        return f"{value:.2f}"
    if kind == "of_customers":
        return f"{int(value):,} of {n_customers:,}"
    if kind == "category":
        return str(value)
    if kind == "year":
        return str(int(value))
    return f"{value:g}"


class _Encoder:
    """Soft rows -> a float matrix. Categories become integer codes; unknowns become NaN."""

    def __init__(self, rows):
        self.codes = {}
        for i in _CATEGORICAL:
            values = sorted({r[NAMES[i]] for r in rows})
            self.codes[NAMES[i]] = {v: j for j, v in enumerate(values)}

    def matrix(self, rows):
        X = np.full((len(rows), len(NAMES)), np.nan)
        for r, row in enumerate(rows):
            for c, name in enumerate(NAMES):
                v = row[name]
                if name in self.codes:
                    v = self.codes[name].get(v)    # a category never seen in training is unknown
                if v is not None:
                    X[r, c] = float(v)
        return X


def _model(seed):
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.06, max_iter=120, min_samples_leaf=10, l2_regularization=1.0,
        categorical_features=_CATEGORICAL, early_stopping=False, random_state=seed)


def _grouped_folds(groups, seed, n_splits=5):
    """GroupKFold over a seeded shuffle of the groups (alerts)."""
    order = sorted(set(groups))
    np.random.RandomState(seed).shuffle(order)
    index = {g: i for i, g in enumerate(order)}
    ids = np.array([index[g] for g in groups])
    return GroupKFold(n_splits=min(n_splits, len(order))).split(ids, groups=ids)


def _out_of_fold(X, y, groups, seed):
    scores = np.full(len(y), np.nan)
    for train, test in _grouped_folds(groups, seed):
        if len(set(y[train])) < 2:
            scores[test] = y[train].mean()
            continue
        scores[test] = _model(seed).fit(X[train], y[train]).predict_proba(X[test])[:, 1]
    return scores


def _bootstrap_auc(y, scores, groups, seed, rounds=500):
    """95% interval of the AUC, resampling alerts."""
    rng = np.random.RandomState(seed)
    by_group = {}
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    keys = sorted(by_group)
    aucs = []
    for _ in range(rounds):
        idx = np.concatenate([by_group[keys[j]] for j in rng.randint(0, len(keys), len(keys))])
        if len(set(y[idx])) == 2:
            aucs.append(roc_auc_score(y[idx], scores[idx]))
    if not aucs:
        return None
    return [round(float(np.quantile(aucs, 0.025)), 3), round(float(np.quantile(aucs, 0.975)), 3)]


class Ranker:
    def __init__(self, seed=SEED):
        self.seed = seed
        self.trained = False
        self.prior = 0.5
        self.encoder = self.model = self.calibration = self.background = None

    def raw(self, X):
        return self.model.predict_proba(X)[:, 1]

    def priority(self, X):
        """Calibrated priority for encoded rows."""
        if not self.trained:
            return np.full(len(X), self.prior)
        return np.clip(self.calibration.predict(self.raw(X)), 0.0, 1.0)

    def drivers(self, rows, n_customers):
        """Per row: the features that move its priority most, with signed effects.

        Permutation-style: effect = priority(row) - mean priority(row with that one
        feature replaced by each training row's value).
        """
        if not self.trained or not rows:
            return [[] for _ in rows]
        X = self.encoder.matrix(rows)
        base = self.priority(X)
        B = self.background
        effects = np.zeros((len(rows), len(NAMES)))
        for c in range(len(NAMES)):
            swapped = np.repeat(X, len(B), axis=0)
            swapped[:, c] = np.tile(B[:, c], len(X))
            effects[:, c] = base - self.priority(swapped).reshape(len(X), len(B)).mean(axis=1)
        out = []
        for r, row in enumerate(rows):
            order = sorted(range(len(NAMES)), key=lambda c: (-abs(effects[r, c]), c))
            out.append([
                {"feature": NAMES[c], "label": FEATURES[c][1], "value": show(NAMES[c], row[NAMES[c]], n_customers),
                 "effect": round(float(effects[r, c]), 3)}
                for c in order[:_TOP_DRIVERS] if abs(effects[r, c]) >= _MIN_EFFECT])
        return out


def train(results, rows, seed=SEED):
    """Fit the ranker.

    results: engine results that have a feature row; rows: their soft rows, same order.
    Returns (ranker, metrics).
    """
    decided = [i for i, r in enumerate(results) if r["decision"] != "REVIEW"]
    review = [i for i, r in enumerate(results) if r["decision"] == "REVIEW"]
    like = [i for i in decided if would_be_review(results[i]) and not results[i]["features"]["company"]]
    label = lambda i: int(results[i]["decision"] == "SAME_ENTITY")
    outside = [i for i in review if not would_be_review(results[i]) or results[i]["features"]["company"]]

    metrics = {
        "ranker_trained": False,
        "ranker_masked_auc": None,
        "ranker_masked_fields": MASKED,
        "ranker_features": NAMES,
        "ranker_decided_hits": len(decided),
        "ranker_training_rows": len(like),
        "ranker_training_same": sum(label(i) for i in like),
        "ranker_training_alerts": len({results[i]["alert_id"] for i in like}),
        "ranker_training_population": "decided hits on individuals that would be REVIEW with the hit's date "
                                      "of birth and dated reports masked",
        "ranker_review_hits": len(review),
        "ranker_review_hits_outside_training_population": len(outside),
    }
    ranker = Ranker(seed)
    population = like
    if len(like) < _MIN_ROWS or len({label(i) for i in like}) < 2:
        # Too few review-like rows to learn from: use every decided hit, and say so.
        population = decided
        metrics["ranker_training_population"] = ("all decided hits, soft signals only: too few review-like rows; "
                                                 "the model can lean on rules the masking leaves in place")
        metrics["ranker_training_rows"] = len(decided)
        metrics["ranker_training_same"] = sum(label(i) for i in decided)
        metrics["ranker_training_alerts"] = len({results[i]["alert_id"] for i in decided})
    y = np.array([label(i) for i in population], dtype=int)
    groups = [results[i]["alert_id"] for i in population]
    if decided:
        ranker.prior = round(float(np.mean([label(i) for i in population])) if len(population) else 0.5, 4)
    if len(population) < 10 or len(set(y)) < 2 or min(np.bincount(y)) < 3 or len(set(groups)) < 3:
        metrics["ranker_note"] = ("Not trained: too few decided hits of both kinds. Every REVIEW hit gets the "
                                  "share of SAME_ENTITY among the decided hits as its priority.")
        return ranker, metrics

    ranker.encoder = _Encoder([rows[i] for i in population])
    X = ranker.encoder.matrix([rows[i] for i in population])

    # Validation: grouped by alert, repeated over different fold assignments.
    oof = [_out_of_fold(X, y, groups, seed + rep) for rep in range(_REPEATS)]
    aucs = [roc_auc_score(y, s) for s in oof]
    metrics["ranker_masked_auc"] = round(float(np.mean(aucs)), 4)
    metrics["ranker_masked_auc_repeats"] = [round(float(a), 4) for a in aucs]
    metrics["ranker_masked_auc_ci95"] = _bootstrap_auc(y, oof[0], groups, seed)

    # Calibration on the out-of-fold scores, then the model on every row.
    ranker.calibration = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    ranker.calibration.fit(np.concatenate(oof), np.tile(y, _REPEATS))
    ranker.model = _model(seed).fit(X, y)
    ranker.background = X
    ranker.trained = True
    metrics["ranker_trained"] = True
    metrics["ranker_brier_out_of_fold"] = round(float(np.mean([brier_score_loss(y, s) for s in oof])), 4)
    metrics["ranker_brier_base_rate"] = round(float(brier_score_loss(y, np.full(len(y), y.mean()))), 4)

    # One feature at a time, out of fold: which soft signals carry the ranking.
    alone = {}
    for c, name in enumerate(NAMES):
        col = X[:, c]
        known = ~np.isnan(col)
        if name in ranker.encoder.codes or len(set(col[known])) < 2 or len(set(y[known])) < 2:
            continue
        alone[name] = round(float(roc_auc_score(y[known], col[known])), 3)
    metrics["ranker_single_feature_auc"] = alone

    # For contrast only: the same model and soft signals on every decided hit. Inflated by
    # leakage: most of these hits are settled by a country or name contradiction, which the
    # soft signals (countries in common, name similarity) give away. Not a measure of ranking.
    if population is like and len(decided) > len(like):
        ya = np.array([label(i) for i in decided], dtype=int)
        ga = [results[i]["alert_id"] for i in decided]
        Xa = _Encoder([rows[i] for i in decided]).matrix([rows[i] for i in decided])
        if len(set(ya)) == 2 and len(set(ga)) >= 3:
            metrics["ranker_all_decided_auc"] = round(float(roc_auc_score(ya, _out_of_fold(Xa, ya, ga, seed))), 4)
            metrics["ranker_all_decided_auc_note"] = ("inflated by leakage: the rules that settle most decided "
                                                      "hits can be read off the soft signals; do not quote it")

    # How unlike the training rows are the REVIEW hits? 0.5 = indistinguishable.
    if len(review) >= 10:
        Xr = ranker.encoder.matrix([rows[i] for i in review])
        both = np.vstack([X, Xr])
        which = np.array([0] * len(X) + [1] * len(Xr))
        both_groups = groups + [results[i]["alert_id"] for i in review]
        if len(set(both_groups)) >= 3:
            metrics["ranker_review_vs_training_auc"] = round(float(
                roc_auc_score(which, _out_of_fold(both, which, both_groups, seed))), 4)
    return ranker, metrics
