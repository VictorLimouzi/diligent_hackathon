"""Build every advisory model and write the scores.

    python -m ml.train <data_folder> --out out/ml

Writes scores.json ({hit_id: MlScores}), metrics.json and routing.json.
Deterministic: fixed seeds, sorted inputs, no timings in the files.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

from casebook.engine import decide_all
from casebook.load import Data

from . import SEED
from .anomaly import anomaly_scores
from .names import train_and_evaluate
from .ranker import NameStats, soft_row
from .ranker import train as train_ranker
from .router import route
from .search import CustomerSearch, resolver_recall

NEAR_MISS_THRESHOLD = 0.5


def build(folder, seed=SEED, log=None):
    """(scores, metrics, routing) for a data folder."""
    say = log or (lambda *_: None)
    clock = time.time()

    def lap(what):
        nonlocal clock
        say(f"{what}: {time.time() - clock:.1f}s")
        clock = time.time()

    data = Data(folder)
    results = decide_all(data)
    usable = [r for r in results if r.get("features") and r.get("trace")]
    lap("load and decide")

    # 1. name similarity
    name_model, metrics = train_and_evaluate(data, results, seed)
    similarity = {}
    for r in usable:
        f = r["features"]
        s, _ = name_model.best(f["customer_name"], r["trace"]["hit"]["names"], bool(f["company"]))
        similarity[r["hit_id"]] = None if s is None else round(s, 4)
    cleared_on_name = [r for r in usable if "NAME_MISMATCH_VERIFIED" in r["reason_codes"]]
    near = {r["hit_id"] for r in cleared_on_name
            if similarity[r["hit_id"]] is not None and similarity[r["hit_id"]] >= NEAR_MISS_THRESHOLD}
    scores_cleared = [similarity[r["hit_id"]] for r in cleared_on_name if similarity[r["hit_id"]] is not None]
    metrics.update({
        "near_miss_threshold": NEAR_MISS_THRESHOLD,
        "near_miss_count": len(near),
        "near_miss_cleared_on_name": len(cleared_on_name),
        # ten equal bins from 0 to 1: where the hits the rules cleared on name sit
        "near_miss_score_histogram": [int(n) for n in np.histogram(scores_cleared, bins=10, range=(0, 1))[0]],
    })
    lap("name model")

    # 2. customer retrieval
    search = CustomerSearch(data)
    metrics["search_records"] = len(search.ids)
    metrics.update(resolver_recall(data, results, search))
    lap("search index and resolver recall")

    # 3. review ranking
    stats = NameStats(data)
    rows = [soft_row(data, r, stats, similarity[r["hit_id"]]) for r in usable]
    ranker, ranker_metrics = train_ranker(usable, rows, seed)
    metrics.update(ranker_metrics)
    review = [i for i, r in enumerate(usable) if r["decision"] == "REVIEW"]
    review_rows = [rows[i] for i in review]
    if ranker.trained:
        priorities = ranker.priority(ranker.encoder.matrix(review_rows))
    else:
        priorities = np.full(len(review), ranker.prior)
    drivers = ranker.drivers(review_rows, stats.customers)
    priority = {usable[i]["hit_id"]: (round(float(p), 4), d) for i, p, d in zip(review, priorities, drivers)}
    metrics["ranker_distinct_priorities"] = len({p for p, _ in priority.values()})
    metrics["ranker_drivers"] = "permutation-style on the calibrated model (see ml/ranker.py)"
    lap("ranker")

    # 4. text routing
    routing, router_metrics = route(data, seed)
    metrics.update(router_metrics)
    lap("router")

    # 5. unusual cases
    anomaly = anomaly_scores(usable, similarity, seed)
    lap("anomaly")

    scores = {}
    for r in results:
        hid = r["hit_id"]
        p, d = priority.get(hid, (None, []))
        scores[hid] = {
            "priority": p if r["decision"] == "REVIEW" else None,
            "name_similarity": similarity.get(hid),
            "near_miss": hid in near,
            "anomaly": anomaly.get(hid),
            "drivers": d,
        }
    metrics["hits"] = len(scores)
    metrics["decisions"] = {k: sum(1 for r in results if r["decision"] == k)
                            for k in sorted({r["decision"] for r in results})}
    metrics["seed"] = seed
    return scores, metrics, routing


def write(out, scores, metrics, routing):
    os.makedirs(out, exist_ok=True)
    for name, content in (("scores.json", scores), ("metrics.json", metrics), ("routing.json", routing)):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train the advisory models and write their scores.")
    ap.add_argument("folder", help="data folder with the JSON Lines files")
    ap.add_argument("--out", default="out/ml")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    start = time.time()
    scores, metrics, routing = build(args.folder, args.seed, log=lambda m: print(m, file=sys.stderr))
    write(args.out, scores, metrics, routing)
    print(f"{len(scores)} hits scored in {time.time() - start:.1f}s -> {args.out}")
    print(f"name model: accuracy {metrics['name_model_accuracy']}, AUC {metrics['name_model_auc']}; "
          f"near misses {metrics['near_miss_count']}")
    print(f"ranker masked AUC {metrics['ranker_masked_auc']} on {metrics['ranker_training_rows']} rows; "
          f"resolver recall@10 {metrics['resolver_recall_at_10']}")
    print(f"routed: {metrics['router_notes_routed']} notes, {metrics['router_snippets_routed']} snippets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
