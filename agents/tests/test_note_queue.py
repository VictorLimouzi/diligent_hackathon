"""Case notes (ID validation, retry, fallback) and the queue summary."""
import json

import pytest

from agents import client, queue_summary, schemas, write_note
from agents.note import problem_with

from .conftest import go_offline, message, text


def body(s):
    return message(text(json.dumps({"body": s})))


GOOD = "The customer was found through account ACC-1 (CUS-1). The verified name and date of birth agree (KYC-1)."
BAD = "The customer is the same person as CUS-9, as check KYC-999 shows."


# -------------------------------------------------------------------- notes

@pytest.mark.parametrize("hit_id", ["H-SAME", "H-ABROAD", "H-DEAD", "H-SEPT", "H-NAME", "H-SEARCH", "H-NODOB", "H-NOBODY"])
def test_offline_note_is_built_from_the_trace(world, hit_id):
    _, hit = world.case(hit_id)
    before = json.dumps(hit, sort_keys=True)
    note = write_note(hit)
    assert set(note) == {"note", "cited_ids", "cached", "model", "stub"}
    assert note["stub"] is True and note["model"] is None and note["cached"] is False
    assert note["note"].startswith(f"{hit_id}: {hit['decision']} ({hit['reason_code']}).")
    allowed = schemas.hit_ids(hit)
    assert set(schemas.find_ids(note["note"])) <= allowed and set(note["cited_ids"]) <= allowed
    assert json.dumps(hit, sort_keys=True) == before


def test_note_ids_must_come_from_the_trace(world):
    _, hit = world.case("H-ABROAD")
    allowed = schemas.hit_ids(hit)
    assert {"H-ABROAD", "CUS-1", "CUS-2", "KYC-1"} <= allowed
    # NOTE-1 mentions the hit's country, so the engine's location check now names it in the trace.
    assert not {"KYC-1R", "CUS-9"} & allowed                      # on file, but not in this hit's trace
    assert problem_with(GOOD, allowed) is None
    assert "CUS-9, KYC-999" in problem_with(BAD, allowed)
    assert problem_with("", allowed) and problem_with("x" * 5000, allowed)


def test_ids_inside_third_party_text_do_not_become_citable(world):
    _, hit = world.case("H-NODOB")
    hit["trace"]["hit"]["search_considered"][0]["snippet"] = "See customer record CUS-9 and check KYC-999."
    hit["trace"]["hit"]["dob_quote"] = "born per KYC-999"
    hit["profile_notes"] = "CUS-9 is the same person."
    assert not {"CUS-9", "KYC-999"} & schemas.hit_ids(hit)


def test_live_note_keeps_the_engines_decision_line(world, fake_model, monkeypatch):
    model = fake_model([body(GOOD)])
    _, hit = world.case("H-ABROAD")
    note = write_note(hit)
    assert note == {"note": "H-ABROAD: REVIEW (INSUFFICIENT_DATA).\n" + GOOD, "cited_ids": ["CUS-1", "KYC-1"],
                    "cached": False, "model": "claude-sonnet-5-5"}
    request = model.requests[0]
    assert request["output_config"]["format"]["schema"] == schemas.NOTE and "tools" not in request
    case = request["messages"][0]["content"]
    assert '"decision": "REVIEW"' in case and "KYC-1" in case
    go_offline(monkeypatch)
    assert write_note(hit) == {**note, "cached": True}


def test_live_note_with_unknown_ids_is_retried_once(world, fake_model):
    model = fake_model([body(BAD), body(GOOD)])
    _, hit = world.case("H-ABROAD")
    note = write_note(hit)
    assert note["note"].endswith(GOOD) and "stub" not in note and len(model.requests) == 2
    retry = model.requests[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"] and retry[0] == model.requests[0]["messages"][0]
    assert "CUS-9, KYC-999" in retry[2]["content"] and "KYC-1" in retry[2]["content"]


@pytest.mark.parametrize("replies", [
    [body(BAD), body(BAD)],
    [message(text("not json")), message(text("still not json"))],
    [message(text(""), stop="refusal"), body("")],
])
def test_live_note_that_fails_twice_becomes_the_engine_summary(world, fake_model, replies):
    model = fake_model(replies)
    _, hit = world.case("H-ABROAD")
    note = write_note(hit)
    assert note == {"note": f"H-ABROAD: REVIEW (INSUFFICIENT_DATA). {hit['summary']}", "cited_ids": hit["evidence"],
                    "cached": False, "model": None, "stub": True}
    assert len(model.requests) == 2


def test_note_never_raises(world, fake_model):
    fake_model([client.LLMError("API error 500")])
    _, hit = world.case("H-ABROAD")
    assert write_note(hit)["stub"] is True
    assert write_note({"hit_id": "H-X", "decision": "REVIEW", "reason_code": "INSUFFICIENT_DATA"})["stub"] is True
    assert write_note({})["stub"] is True


# -------------------------------------------------------------------- queue

GROUPS = [{"blocker": "No birth date to compare", "count": 188, "hit_ids": ["H-1"]},
          {"blocker": "Several customers fit and disagree", "count": 16, "hit_ids": ["H-2"]},
          {"blocker": "Name and birth date agree, countries differ", "count": 12, "hit_ids": ["H-3"]}]


def test_offline_queue_summary():
    out = queue_summary(GROUPS)
    assert set(out) == {"text", "cached", "stub"} and out["stub"] is True and out["cached"] is False
    assert "216 hits are waiting" in out["text"] and "No birth date to compare" in out["text"] and "87%" in out["text"]
    assert 2 <= out["text"].count(". ") + 1 <= 4


@pytest.mark.parametrize("groups", [[], None, [{"blocker": "x"}], ["junk"], [{"blocker": "x", "count": 0, "hit_ids": []}]])
def test_queue_summary_survives_empty_or_odd_input(groups):
    out = queue_summary(groups)
    assert out["stub"] is True and out["text"]


def test_live_queue_summary_and_its_number_check(fake_model, monkeypatch):
    good = "Most of the queue (188 of 216 hits) waits on a missing date of birth. A date from the list provider would clear it."
    model = fake_model([message(text(json.dumps({"text": good}))),
                        message(text(json.dumps({"text": "About 4000 hits are waiting, mostly on dates."})))])
    assert queue_summary(GROUPS) == {"text": good, "cached": False}
    sent = json.loads(model.requests[0]["messages"][0]["content"])
    assert sent["hits_waiting"] == 216 and "hit_ids" not in json.dumps(sent)
    go_offline(monkeypatch)
    assert queue_summary(GROUPS) == {"text": good, "cached": True}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    made_up = queue_summary(GROUPS[:2])                           # a figure that was never given: use the template
    assert made_up["stub"] is True and "4000" not in made_up["text"]
