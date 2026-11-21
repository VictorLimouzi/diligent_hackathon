"""API tests against the real dev data. Skipped when the data is not on this machine.

Run: .venv/bin/python -m pytest server/tests
"""
import json
import os
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("CASEBOOK_DATA") or os.path.join(ROOT, "data", "screening", "dev")
pytestmark = pytest.mark.skipif(not os.path.isdir(DATA), reason="dev data not present")


@pytest.fixture(scope="module")
def client():
    os.environ["CASEBOOK_DATA"] = DATA
    os.environ["CASEBOOK_OUT"] = tempfile.mkdtemp()
    from fastapi.testclient import TestClient

    import server.app as app_module
    app_module.state = None
    return TestClient(app_module.app)


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_summary_counts_add_up(client):
    s = client.get("/api/summary").json()
    assert sum(s["decisions"].values()) == s["hits"]
    assert sum(s["alert_status"].values()) == s["alerts"]
    assert s["labels"]["false_clears"] == 0
    assert s["labels"]["correct"] == s["labels"]["n"]


def test_alert_list_filters_and_order(client):
    rows = client.get("/api/alerts").json()
    order = {"open": 0, "compliance": 1, "cleared": 2}
    assert [order[r["status"]] for r in rows] == sorted(order[r["status"]] for r in rows)
    cleared = client.get("/api/alerts", params={"status": "cleared"}).json()
    assert cleared and all(r["status"] == "cleared" for r in cleared)
    assert all(set(r["decisions"]) == {"DIFFERENT_ENTITY"} for r in cleared)
    found = client.get("/api/alerts", params={"q": "H-ADF453"}).json()
    assert [r["alert_id"] for r in found] == ["A-0DF328"]


def test_alert_detail_matches_contract_keys(client):
    a = client.get("/api/alerts/A-0DF328").json()
    hit = next(h for h in a["hits"] if h["hit_id"] == "H-ADF453")
    for key in ("decision", "reason_code", "reason_codes", "customer_record_id", "evidence", "summary",
                "blocker", "counterfactuals", "trace", "ml", "llm", "profile", "profile_notes", "label"):
        assert key in hit
    assert "features" not in hit
    assert (hit["decision"], hit["reason_code"]) == ("REVIEW", "INSUFFICIENT_DATA")
    assert hit["blocker"] == "Name and birth date agree, countries differ"
    assert hit["counterfactuals"][0]["fact"] == "customer_country"
    assert hit["counterfactuals"][0]["outcomes"][0]["decision"] == "SAME_ENTITY"


def test_counterfactuals_only_on_review(client):
    for row in client.get("/api/alerts").json()[:120]:
        for h in client.get(f"/api/alerts/{row['alert_id']}").json()["hits"]:
            if h["decision"] != "REVIEW":
                assert h["counterfactuals"] == [] and h["blocker"] is None
            else:
                assert h["blocker"]


def test_records_and_unknown_ids(client):
    assert client.get("/api/records/KYC-A932A4").json()["kind"] == "Identity check"
    assert client.get("/api/records/H-ADF453").json()["kind"] == "Watchlist profile"
    assert client.get("/api/records/NOPE-1").status_code == 404
    assert client.get("/api/alerts/NOPE").status_code == 404
    assert client.post("/api/hits/NOPE/note").status_code == 404


def test_search(client):
    out = client.get("/api/search/customers", params={"q": "markus muller"}).json()
    assert out and {"record_id", "full_name", "dob", "country", "score"} <= set(out[0])
    assert client.get("/api/search/customers", params={"q": "  "}).json() == []


def test_streams_end_properly(client):
    ev = events(client.post("/api/hits/H-ADF453/investigate"))
    assert ev[-1]["type"] == "final"
    assert {"blocker", "findings", "next_action", "rfi_draft", "cited_ids"} <= set(ev[-1]["result"])
    ev = events(client.post("/api/hits/H-ADF453/chat", json={"messages": [{"role": "user", "content": "Why review?"}]}))
    assert ev[-1]["type"] == "done"
    assert any(e["type"] == "text" for e in ev)


def test_note(client):
    n = client.post("/api/hits/H-ADF453/note").json()
    assert n["note"] and isinstance(n["cited_ids"], list)


def test_review_is_logged_and_never_changes_the_engine_decision(client):
    before = client.get("/api/alerts/A-0DF328").json()
    entry = client.post("/api/hits/H-ADF453/review", json={
        "decision": "DIFFERENT_ENTITY", "reason_code": "LOCATION_MISMATCH", "comment": "test"}).json()
    assert entry["overrides_engine"] is True and entry["engine_decision"] == "REVIEW"
    assert client.get("/api/audit", params={"hit_id": "H-ADF453"}).json()[0]["id"] == entry["id"]
    assert client.get("/api/alerts/A-0DF328").json() == before
    assert client.post("/api/hits/H-ADF453/review", json={"decision": "CLEAR", "reason_code": "X"}).status_code == 422


def test_qa(client):
    qa = client.get("/api/qa").json()
    assert qa["label_metrics"]["n"] == len(qa["labels"]) == 50
    assert sum(b["count"] for b in qa["blockers"]) == client.get("/api/summary").json()["decisions"]["REVIEW"]
    summary = client.get("/api/queue/summary").json()
    assert summary["text"] and summary["groups"] == qa["blockers"]
