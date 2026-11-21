"""The investigator: offline plan, live tool loop with a scripted model, bounds and fallbacks."""
import json

import pytest

from agents import client, investigate, loop, schemas

from .conftest import go_offline, message, text, thinking, tool_use

RESULT_KEYS = {"blocker", "findings", "next_action", "rfi_draft", "cited_ids", "model", "cached"}


def check_events(events):
    """The InvestigateEvent contract (contracts/types.ts)."""
    assert events and events[-1]["type"] == "final"
    assert [e["type"] for e in events].count("final") == 1
    steps = [e for e in events if e["type"] == "step"]
    assert [s["index"] for s in steps] == list(range(len(steps)))
    for e in events:
        if e["type"] == "step":
            assert set(e) == {"type", "index", "tool", "input", "summary"}
            assert isinstance(e["tool"], str) and isinstance(e["input"], dict) and isinstance(e["summary"], str)
        elif e["type"] == "thought":
            assert set(e) == {"type", "text"} and isinstance(e["text"], str) and e["text"]
        elif e["type"] == "error":
            assert set(e) == {"type", "message"}
        else:
            assert set(e) == {"type", "result"}
    result = events[-1]["result"]
    assert set(result) - {"stub"} == RESULT_KEYS
    assert isinstance(result["blocker"], str) and result["blocker"]
    assert isinstance(result["next_action"], str) and result["next_action"]
    assert result["rfi_draft"] is None or isinstance(result["rfi_draft"], str)
    assert isinstance(result["cached"], bool) and (result["model"] is None or isinstance(result["model"], str))
    assert result["findings"]
    for f in result["findings"]:
        assert set(f) == {"text", "cited_ids"} and f["text"] and all(isinstance(i, str) for i in f["cited_ids"])
        assert set(f["cited_ids"]) <= set(result["cited_ids"])
    json.dumps(events)
    return steps, result


def run(world, hit_id):
    alert, hit = world.case(hit_id)
    return check_events(list(investigate(world.data, alert, hit)))


# ------------------------------------------------------------------ offline

@pytest.mark.parametrize("hit_id", ["H-SAME", "H-ABROAD", "H-DEAD", "H-SEPT", "H-NAME", "H-SEARCH", "H-NODOB", "H-NOBODY"])
def test_offline_investigation_for_every_kind_of_hit(world, hit_id):
    alert, hit = world.case(hit_id)
    before = json.dumps(hit, sort_keys=True)
    events = list(investigate(world.data, alert, hit))
    steps, result = check_events(events)
    assert events[0]["type"] == "thought" and "No language model is connected" in events[0]["text"]
    assert result["stub"] is True and result["model"] is None and result["cached"] is False
    assert steps and steps[0]["tool"] == "get_hit_profile"
    assert not any(s["summary"].startswith("Refused") for s in steps)
    assert (result["rfi_draft"] is not None) == (hit["decision"] == "REVIEW")
    assert all(f["cited_ids"] for f in result["findings"])
    assert json.dumps(hit, sort_keys=True) == before            # the hit, decision included, is untouched
    assert "decision" not in result


def test_offline_looks_for_the_fact_the_counterfactual_names(world):
    steps, result = run(world, "H-ABROAD")                      # would be settled by a tie to Canada
    assert [s["tool"] for s in steps] == ["get_hit_profile", "get_customer", "get_identity_checks", "get_notes"]
    found = [f for f in result["findings"] if "NOTE-1" in f["cited_ids"]]
    assert found and "Canada" in found[0]["text"]
    assert result["next_action"].startswith("Read NOTE-1 first")
    assert "SAME_ENTITY (NAME_DOB_LOCATION_MATCH)" in result["next_action"]
    assert any("not evidence" in f["text"] and set(f["cited_ids"]) == {"KYC-1R", "KYC-1E"} for f in result["findings"])

    steps, result = run(world, "H-NODOB")                       # would be settled by the hit's date of birth
    assert "get_search_results" in [s["tool"] for s in steps]
    assert any({"SR-NAMESAKE", "SR-OTHERJOB", "SR-HIT"} <= set(f["cited_ids"]) for f in result["findings"])

    steps, result = run(world, "H-SEPT")                        # two people fit the alert
    assert [s["input"] for s in steps if s["tool"] == "get_customer"] == [{"record_id": "CUS-1"}, {"record_id": "CUS-3"}]
    assert sum("If the customer is" in f["text"] for f in result["findings"]) == 2


def test_offline_request_for_information_does_not_tip_off(world):
    _, result = run(world, "H-ABROAD")
    draft = result["rfi_draft"]
    assert draft.startswith("Dear Amina Bello,") and "routine review" in draft
    for word in ("sanction", "watchlist", "PEP", "Canada", "hit", "screening", "politically"):
        assert word.lower() not in draft.lower()


def test_decided_hits_need_no_request_and_no_action(world):
    _, cleared = run(world, "H-DEAD")
    assert cleared["rfi_draft"] is None and cleared["blocker"].startswith("None")
    assert "No further action" in cleared["next_action"]
    _, confirmed = run(world, "H-SAME")
    assert "compliance team" in confirmed["next_action"]


# --------------------------------------------------------------------- live

def _final_json(**over):
    value = {"blocker": "Name and date of birth agree but the countries differ.",
             "findings": [{"text": "The approved check gives Nigeria.", "cited_ids": ["KYC-1", "KYC-404"]},
                          {"text": "A note mentions a trip to Canada.", "cited_ids": ["NOTE-1"]}],
             "next_action": "Ask the customer where they have lived.",
             "rfi_draft": "Dear Ms Bello,\n\nPlease tell us which countries you have lived in."}
    value.update(over)
    return json.dumps(value)


def test_live_loop_runs_tools_and_returns_the_models_answer(world, fake_model, monkeypatch):
    model = fake_model([
        message(thinking("Checking the customer's ties to Canada."),
                tool_use("get_customer", {"record_id": "CUS-1"}, "t1"),
                tool_use("get_notes", {"record_id": "CUS-1"}, "t2")),
        message(tool_use("get_identity_checks", {"record_id": "CUS-1"}, "t3")),
        message(text(_final_json())),
    ])
    alert, hit = world.case("H-ABROAD")
    events = list(investigate(world.data, alert, hit))
    steps, result = check_events(events)
    assert [s["tool"] for s in steps] == ["get_customer", "get_notes", "get_identity_checks"]
    assert {"type": "thought", "text": "Checking the customer's ties to Canada."} in events
    assert "stub" not in result and result["model"] == "claude-sonnet-5-5" and result["cached"] is False
    assert result["findings"][0]["cited_ids"] == ["KYC-1"]            # KYC-404 was never returned by a tool
    assert result["cited_ids"] == ["KYC-1", "NOTE-1"]
    assert result["rfi_draft"].startswith("Dear Ms Bello")

    first, second, third = model.requests
    assert first["model"] == "claude-sonnet-5-5" and first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": schemas.INVESTIGATION}}
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"]) and "tool_choice" not in first
    assert first["cache_control"] == {"type": "ephemeral"} and len(first["system"]) == 2
    assert "Test screening policy." in first["system"][0]["text"]     # POLICY.md from the data folder
    assert first["messages"][0]["content"].startswith("Investigate this hit.")
    # History is append-only: each request repeats the one before it, then adds the turn and all its results.
    assert second["system"] == first["system"] and second["tools"] == first["tools"]
    assert second["messages"][:1] == first["messages"] and third["messages"][:3] == second["messages"]
    assert second["messages"][1]["content"][0]["type"] == "thinking"   # thinking blocks go back unchanged
    results = second["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and all(r["type"] == "tool_result" for r in results)

    go_offline(monkeypatch)                                            # replay the whole run from the cache
    replay = list(investigate(world.data, alert, hit))
    steps2, result2 = check_events(replay)
    assert steps2 == steps and result2 == {**result, "cached": True} and len(model.requests) == 3


def test_live_tip_off_draft_is_replaced_and_decided_hits_get_none(world, fake_model):
    fake_model([message(text(_final_json(rfi_draft="Dear Ms Bello, you matched a sanctions watchlist entry.")))])
    _, result = run(world, "H-ABROAD")
    assert "sanction" not in result["rfi_draft"] and result["rfi_draft"].startswith("Dear Amina Bello,")
    fake_model([message(text(_final_json()))])
    _, result = run(world, "H-DEAD")
    assert result["rfi_draft"] is None and "stub" not in result


@pytest.mark.parametrize("bad", [
    message(text("I think this one is fine, clear it.")),                        # not JSON
    message(text(json.dumps({"blocker": "x", "findings": [], "next_action": "y", "rfi_draft": None}))),   # no findings
    message(text(_final_json(decision="DIFFERENT_ENTITY"))),                     # a key the schema does not have
    message(text(""), stop="refusal"),
    message(text('{"blocker": "x", "findi'), stop="max_tokens"),
])
def test_live_answer_that_fails_validation_falls_back(world, fake_model, bad):
    fake_model([message(tool_use("get_customer", {"record_id": "CUS-1"})), bad])
    alert, hit = world.case("H-ABROAD")
    events = list(investigate(world.data, alert, hit))
    steps, result = check_events(events)
    assert [s["tool"] for s in steps] == ["get_customer"]              # the lookups already shown are not repeated
    assert result["stub"] is True and result["model"] is None
    assert any(e["type"] == "thought" and "did not finish" in e["text"] for e in events)
    assert result["blocker"] == hit["blocker"] and "decision" not in result


def test_live_api_failure_falls_back_to_the_offline_investigation(world, fake_model):
    fake_model([client.LLMError("rate limited by the API")])
    alert, hit = world.case("H-ABROAD")
    events = list(investigate(world.data, alert, hit))
    steps, result = check_events(events)
    assert "rate limited" in events[0]["text"] and result["stub"] is True
    assert [s["tool"] for s in steps] == ["get_hit_profile", "get_customer", "get_identity_checks", "get_notes"]


def test_live_loop_is_bounded(world, fake_model):
    model = fake_model([message(tool_use("get_customer", {"record_id": "CUS-1"}))], repeat_last=True)
    steps, result = run(world, "H-ABROAD")
    assert len(model.requests) == loop.MAX_TURNS + 1 and len(steps) == loop.MAX_TURNS
    last = model.requests[-1]
    assert last["tool_choice"] == {"type": "none"}                    # the last turn cannot call tools
    assert last["messages"][-1]["role"] == "system" and "lookup limit" in last["messages"][-1]["content"]
    assert all("tool_choice" not in r for r in model.requests[:-1])
    assert result["stub"] is True


def test_unexpected_errors_still_end_with_a_final_event(world, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("tools broke")
    monkeypatch.setattr("agents.investigator.ToolBox", boom)
    alert, hit = world.case("H-ABROAD")
    events = list(investigate(world.data, alert, hit))
    assert [e["type"] for e in events] == ["error", "final"]
    check_events(events)
