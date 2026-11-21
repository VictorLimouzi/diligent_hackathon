"""Shape, determinism and search tests on invented data. Run: python -m pytest ml/tests"""
import json

from casebook.load import Data
from ml.search import CustomerSearch
from ml.train import build

from .conftest import write_jsonl

SCORE_KEYS = {"priority", "name_similarity", "near_miss", "anomaly", "drivers"}
DRIVER_KEYS = {"feature", "label", "value", "effect"}


def unit(v):
    return isinstance(v, float) and 0.0 <= v <= 1.0


def check_scores(results, scores):
    """Every hit has an entry shaped like MlScores in contracts/types.ts."""
    assert set(scores) == set(results)
    for hid, s in scores.items():
        assert set(s) == SCORE_KEYS
        if results[hid]["decision"] == "REVIEW":
            assert unit(s["priority"])
        else:
            assert s["priority"] is None and s["drivers"] == []
        assert s["name_similarity"] is None or unit(s["name_similarity"])
        assert s["anomaly"] is None or unit(s["anomaly"])
        assert isinstance(s["near_miss"], bool)
        for d in s["drivers"]:
            assert set(d) == DRIVER_KEYS
            assert isinstance(d["feature"], str) and isinstance(d["label"], str) and isinstance(d["value"], str)
            assert isinstance(d["effect"], float) and -1.0 <= d["effect"] <= 1.0


def test_folder_covers_every_decision(built):
    results = built[0]
    assert {r["decision"] for r in results.values()} == {"SAME_ENTITY", "DIFFERENT_ENTITY", "REVIEW"}
    assert any("NAME_MISMATCH_VERIFIED" in r["reason_codes"] for r in results.values())


def test_scores_shape(built):
    results, scores, _, _ = built
    check_scores(results, scores)
    # the models really ran on this folder
    assert any(s["drivers"] for s in scores.values())
    assert all(s["anomaly"] is not None for s in scores.values())


def test_near_miss_only_where_rules_cleared_on_name(built):
    results, scores, metrics, _ = built
    for hid, s in scores.items():
        if s["near_miss"]:
            assert "NAME_MISMATCH_VERIFIED" in results[hid]["reason_codes"]
            assert s["name_similarity"] >= metrics["near_miss_threshold"]


def test_metrics_shape(built):
    _, _, metrics, _ = built
    assert metrics["name_model_trained"] and metrics["ranker_trained"]
    for k in ("name_model_accuracy", "name_model_auc", "ranker_masked_auc"):
        assert 0.0 <= metrics[k] <= 1.0
    assert metrics["name_model_names_on_both_sides"] == 0
    assert metrics["hits"] == 80
    # no check outcome is a ranker feature
    assert not set(metrics["ranker_masked_fields"]) & set(metrics["ranker_features"])


def test_routing_shape(built):
    routing = built[3]
    assert set(routing) == {"notes", "snippets"}
    for row in routing["notes"] + routing["snippets"]:
        assert set(row) == {"id", "text", "predicted", "reason"}
        assert all(isinstance(v, str) for v in row.values())


def test_deterministic(folder, built):
    _, scores, metrics, routing = built
    again = build(folder)
    dump = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)
    assert dump(again[0]) == dump(scores)
    assert dump(again[1]) == dump(metrics)
    assert dump(again[2]) == dump(routing)


def test_search_finds_a_name_with_a_typo(folder):
    data = Data(folder)
    search = CustomerSearch(data)
    name = data.customers["CUS-002"]["full_name"]
    first, last = name.split()
    wanted = {cid for cid, c in data.customers.items() if c["full_name"] == name}
    queries = [
        f"{first} {last[:2]}{last[3]}{last[2]}{last[4:]}",     # two letters swapped
        f"{first}{first[-1]} {last}",                          # a letter doubled
        f"{last.upper()}, {first.lower()}",                    # order swapped, other case
        f"{last[:-1]} {first}",                                # order swapped and a letter dropped
    ]
    assert name not in queries
    for q in queries:
        top = search.query(q, 10)
        assert top and set(top[0]) == {"record_id", "full_name", "dob", "country", "score"}
        assert top[0]["record_id"] in wanted, q
        assert all(top[i]["score"] >= top[i + 1]["score"] for i in range(len(top) - 1))
    assert search.query("", 10) == []
    assert len(search.query("Kofi", 3)) == 3


def test_search_details_only_order_equal_names(folder):
    data = Data(folder)
    search = CustomerSearch(data)
    plain = search.query("Amina Bello", 10)
    same = [r for r in plain if r["score"] == plain[0]["score"]]
    if len(same) > 1:
        last = data.customers[same[-1]["record_id"]]
        first = search.query("Amina Bello", 10, dob=last["dob"])[0]
        assert first["record_id"] == same[-1]["record_id"]
    assert [r["score"] for r in search.query("Amina Bello", 10, country="Ghana")] == [r["score"] for r in plain]


def test_too_little_data_falls_back(tmp_path):
    """One alert, one hit: nothing can be trained, and the output still has the contract's shape."""
    d = str(tmp_path)
    write_jsonl(d, "customers", [{"record_id": "CUS-1", "entity_type": "Individual", "full_name": "Amina Bello",
                                  "dob": "1980-05-02", "nationality": "Nigeria", "residence_country": "Nigeria"}])
    write_jsonl(d, "hits", [{"hit_id": "H-1", "entity_type": "Individual", "name": "Bello, Amina",
                             "countries": "Nigeria", "categories": "PEP"}])
    write_jsonl(d, "alerts", [{"alert_id": "A-1", "screened_at": "2026-11-01", "customer_name": "Amina Bello",
                               "entity_type": "Individual", "dob": "1980-05-02", "country": "Nigeria",
                               "hit_ids": "H-1; H-MISSING"}])
    scores, metrics, routing = build(d)
    assert set(scores) == {"H-1", "H-MISSING"}
    for s in scores.values():
        assert set(s) == SCORE_KEYS and s["drivers"] == [] and s["near_miss"] is False
    assert unit(scores["H-1"]["priority"])          # REVIEW: no birth date on the hit
    assert scores["H-MISSING"]["priority"] is None and scores["H-MISSING"]["anomaly"] is None
    assert metrics["ranker_trained"] is False and metrics["ranker_masked_auc"] is None
    assert routing == {"notes": [], "snippets": []}
