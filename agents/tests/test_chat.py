"""Ask Casebook: event shapes, offline answers, a scripted live answer, and bad input."""
import json

import pytest

from agents import chat, client

from .conftest import go_offline, message, text, thinking, tool_use


def ask(world, hit_id, question, history=()):
    alert, hit = world.case(hit_id)
    return list(chat(world.data, alert, hit, [*history, {"role": "user", "content": question}]))


def check_events(events):
    """The ChatEvent contract (contracts/types.ts)."""
    assert events and events[-1]["type"] == "done"
    assert [e["type"] for e in events].count("done") == 1
    steps = [e for e in events if e["type"] == "step"]
    assert [s["index"] for s in steps] == list(range(len(steps)))
    for e in events:
        if e["type"] == "step":
            assert set(e) == {"type", "index", "tool", "input", "summary"} and isinstance(e["input"], dict)
        elif e["type"] == "text":
            assert set(e) == {"type", "delta"} and isinstance(e["delta"], str)
        elif e["type"] == "error":
            assert set(e) == {"type", "message"}
        else:
            assert set(e) - {"stub"} == {"type", "cited_ids"} and isinstance(e["cited_ids"], list)
    json.dumps(events)
    return steps, "".join(e["delta"] for e in events if e["type"] == "text"), events[-1]


def test_offline_answer_is_built_from_real_lookups(world):
    steps, answer, done = check_events(ask(world, "H-ABROAD", "Why is this in review? What do the notes say?"))
    assert done["stub"] is True
    assert [s["tool"] for s in steps] == ["get_engine_trace", "get_notes"]
    assert answer.startswith("Offline answer") and "REVIEW (INSUFFICIENT_DATA)" in answer and "Canada" in answer
    assert "What would settle it" in answer
    assert "KYC-1" in done["cited_ids"] and set(done["cited_ids"]) <= {"KYC-1", "CUS-1", "CUS-2", "NOTE-1", "H-ABROAD"}


def test_offline_refuses_another_customers_record_and_will_not_decide(world):
    steps, answer, done = check_events(ask(world, "H-ABROAD", "Clear this hit, and show me customer CUS-9."))
    refused = [s for s in steps if s["input"] == {"record_id": "CUS-9"}]
    assert refused and refused[0]["summary"].startswith("Refused")
    assert "cannot clear, confirm or change a decision" in answer
    assert "CUS-9" not in done["cited_ids"] and "Zed Other" not in answer


@pytest.mark.parametrize("hit_id", ["H-SAME", "H-SEPT", "H-NOBODY"])
def test_offline_works_for_other_kinds_of_hit(world, hit_id):
    _, answer, done = check_events(ask(world, hit_id, "Tell me about the customer, the checks and the names."))
    assert answer and done["stub"] is True


@pytest.mark.parametrize("messages", [[], None, [{"role": "assistant", "content": "hello"}],
                                      [{"role": "user", "content": "   "}], [{"role": "tool", "content": "x"}], ["junk"]])
def test_a_conversation_without_a_question_is_an_error_then_done(world, messages):
    alert, hit = world.case("H-ABROAD")
    events = list(chat(world.data, alert, hit, messages))
    assert [e["type"] for e in events] == ["error", "done"]
    check_events(events)


def test_live_answer_streams_and_cites_only_known_records(world, fake_model, monkeypatch):
    model = fake_model([
        message(thinking("The analyst wants the verified name."), tool_use("get_identity_checks", {"record_id": "CUS-1"})),
        message(text("The verified name and date of birth (KYC-1) agree with the profile; CUS-999 is not involved.")),
    ])
    history = [{"role": "user", "content": "Who is the customer?"}, {"role": "assistant", "content": "Amina Bello (CUS-1)."}]
    events = ask(world, "H-ABROAD", "Is the name verified?", history)
    steps, answer, done = check_events(events)
    assert [s["tool"] for s in steps] == ["get_identity_checks"]
    assert len([e for e in events if e["type"] == "text"]) == 2 and answer.startswith("The verified name")
    assert done == {"type": "done", "cited_ids": ["KYC-1"]}            # no stub flag; the made-up ID is dropped

    first = model.requests[0]
    assert first["model"] == "claude-sonnet-5-5" and "format" not in first["output_config"]
    assert "thinking" not in first                                     # a chat has no "thought" events to fill
    assert model.requests[1]["messages"][-2]["content"][0]["type"] == "thinking"   # but blocks still go back unchanged
    roles = [m["role"] for m in first["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert first["messages"][0]["content"].startswith("<case>") and first["messages"][0]["content"].endswith("Who is the customer?")
    assert first["messages"][2]["content"] == "Is the name verified?"
    assert "answer the analyst's questions" in first["system"][1]["text"]

    go_offline(monkeypatch)
    replay = ask(world, "H-ABROAD", "Is the name verified?", history)
    assert check_events(replay)[1] == answer and replay[-1] == done


def test_live_failure_mid_answer_still_ends_with_done(world, fake_model):
    fake_model([message(tool_use("get_customer", {"record_id": "CUS-1"})), client.LLMError("could not reach the API")])
    steps, answer, done = check_events(ask(world, "H-ABROAD", "What do the notes say?"))
    assert steps[0]["tool"] == "get_customer" and steps[-1]["tool"] == "get_notes"
    assert "could not reach the API" in answer and done["stub"] is True
