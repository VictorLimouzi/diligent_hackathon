"""What the server relies on: the five exports, their signatures, and shapes that match the contract fixtures."""
import inspect
import json
import os

import pytest

import agents
from agents import chat, investigate, llm_status, queue_summary, write_note

from .conftest import ROOT

FIXTURES = os.path.join(ROOT, "contracts", "fixtures")
DEV_DATA = os.environ.get("CASEBOOK_DATA") or os.path.join(ROOT, "data", "dev_from_workbook")


def fixture(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.isfile(path):
        pytest.skip(f"{name} is not in this checkout")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_exports_and_signatures():
    assert sorted(agents.__all__) == ["chat", "investigate", "llm_status", "queue_summary", "write_note"]
    params = lambda fn: list(inspect.signature(fn).parameters)
    assert params(llm_status) == [] and params(write_note) == ["hit"] and params(queue_summary) == ["groups"]
    assert params(investigate) == ["data", "alert", "hit"] and params(chat) == ["data", "alert", "hit", "messages"]
    # Plain synchronous generators: the server wraps them in a streaming response.
    assert inspect.isgeneratorfunction(investigate) and inspect.isgeneratorfunction(chat)
    assert not inspect.isasyncgenfunction(investigate) and not inspect.iscoroutinefunction(write_note)


def test_shapes_match_the_contract_fixtures(world):
    alert, hit = world.case("H-ABROAD")
    by_type = lambda events: {e["type"]: e for e in events}

    ours, theirs = by_type(investigate(world.data, alert, hit)), by_type(fixture("investigate_events.json"))
    assert set(ours["step"]) == set(theirs["step"]) and set(ours["thought"]) == set(theirs["thought"])
    assert set(ours["final"]["result"]) - {"stub"} == set(theirs["final"]["result"])
    assert set(ours["final"]["result"]["findings"][0]) == set(theirs["final"]["result"]["findings"][0])

    ours = by_type(chat(world.data, alert, hit, [{"role": "user", "content": "Why is this in review?"}]))
    theirs = by_type(fixture("chat_events.json"))
    assert set(ours["step"]) == set(theirs["step"]) and set(ours["text"]) == set(theirs["text"])
    assert set(ours["done"]) - {"stub"} == set(theirs["done"])

    assert set(write_note(hit)) == set(fixture("note.json"))
    groups = fixture("queue_summary.json")["groups"]
    assert set(queue_summary(groups)) == set(fixture("queue_summary.json")) - {"groups"}
    assert set(llm_status()) - {"stub"} == {"live", "model_interactive", "model_batch"}


def test_server_bridge_uses_the_real_package(world):
    """server/advisory.py imports these five and falls back only on ImportError: nothing here may raise."""
    advisory = pytest.importorskip("server.advisory")

    class State:
        data = world.data

    alert, hit = world.case("H-ABROAD")
    assert advisory.llm_status()["model_interactive"] == "claude-sonnet-5-5"
    assert advisory.write_note(State, alert, hit)["note"].startswith("H-ABROAD: REVIEW")
    assert list(advisory.investigate(State, alert, hit))[-1]["result"]["findings"]
    assert list(advisory.chat(State, alert, hit, [{"role": "user", "content": "why?"}]))[-1]["type"] == "done"
    assert "waiting for review" in advisory.queue_summary(State, [{"blocker": "No birth date to compare", "count": 2, "hit_ids": []}])["text"]


@pytest.mark.skipif(not os.path.isdir(DEV_DATA), reason="dev data not on this machine")
def test_every_export_on_a_real_dev_hit():
    from casebook.engine import decide_all
    from casebook.export import build_casebook
    from casebook.load import Data

    data = Data(DEV_DATA)
    cases = {h["hit_id"]: (a["alert"], h) for a in build_casebook(data, decide_all(data))["alerts"] for h in a["hits"]}
    if "H-ADF453" not in cases:
        pytest.skip("H-ADF453 is not in this data folder")
    alert, hit = cases["H-ADF453"]                                # name and birth date agree, countries differ
    before = json.dumps(hit, sort_keys=True)
    events = list(investigate(data, alert, hit))
    result = events[-1]["result"]
    assert [e["tool"] for e in events if e["type"] == "step"] == ["get_hit_profile", "get_customer",
                                                                 "get_identity_checks", "get_notes"]
    assert result["stub"] is True and result["rfi_draft"] and "KYC-A932A4" in result["cited_ids"]
    assert any("United States" in f["text"] for f in result["findings"])
    assert write_note(hit)["note"].startswith("H-ADF453: REVIEW (INSUFFICIENT_DATA).")
    done = list(chat(data, alert, hit, [{"role": "user", "content": "Why can this not be cleared on country?"}]))[-1]
    assert done["type"] == "done" and "KYC-A932A4" in done["cited_ids"]
    assert json.dumps(hit, sort_keys=True) == before
