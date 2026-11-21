"""Unusual cases: an IsolationForest per decision group over the numeric feature row.

`anomaly` is 0..1 within a hit's own decision group: 1 is the most unusual hit
of the group, 0 the most ordinary. It flags cases worth a second look; it says
nothing about whether the decision is right. No tuning.
"""
import numpy as np
from sklearn.ensemble import IsolationForest

from . import SEED

_OUTCOME = {"agree": 1.0, "unknown": 0.0, "contradict": -1.0}
_NUMBERS = ["n_candidates", "alert_hit_count", "hit_alias_count", "hit_dob_count", "n_customer_countries",
            "n_hit_countries", "n_shared_countries", "n_search_results", "n_search_about_hit"]
_MAYBE_MISSING = ["match_score", "customer_birth_year", "min_year_gap"]
_FLAGS = ["company", "name_verified", "dob_verified", "hit_has_profile_notes", "has_dated_report", "type_mismatch"]
_OUTCOMES = ["name_outcome", "dob_outcome", "location_outcome", "time_outcome"]
_MIN_GROUP = 5


def _row(f, name_similarity):
    row = [float(f.get(k) or 0) for k in _NUMBERS]
    for k in _MAYBE_MISSING:
        v = f.get(k)
        row += [np.nan if v is None else float(v), float(v is None)]
    row += [float(bool(f.get(k))) for k in _FLAGS]
    row += [_OUTCOME.get(f.get(k), 0.0) for k in _OUTCOMES]
    row.append(np.nan if name_similarity is None else float(name_similarity))
    return row


def anomaly_scores(results, name_similarity, seed=SEED):
    """{hit_id: 0..1 or None}. None when the hit has no feature row or its group is too small."""
    out = {r["hit_id"]: None for r in results}
    groups = {}
    for r in results:
        if r.get("features"):
            groups.setdefault(r["decision"], []).append(r)
    for decision in sorted(groups):
        members = groups[decision]
        if len(members) < _MIN_GROUP:
            continue
        X = np.array([_row(r["features"], name_similarity.get(r["hit_id"])) for r in members])
        X[:, np.isnan(X).all(axis=0)] = 0.0                     # a value nobody in the group has
        X = np.where(np.isnan(X), np.nanmedian(X, axis=0), X)   # unknowns take the group median
        forest = IsolationForest(n_estimators=200, random_state=seed).fit(X)
        s = -forest.score_samples(X)          # higher = more unusual
        spread = s.max() - s.min()
        scaled = (s - s.min()) / spread if spread > 0 else np.zeros(len(s))
        for r, v in zip(members, scaled):
            out[r["hit_id"]] = round(float(v), 4)
    return out
