"""The client: status, request shape, the disk cache, and the real SDK path against an in-process transport."""
import json
import os

import pytest

from agents import client, llm_status, schemas

from .conftest import go_offline, message, text


def _request(model=None, user="hello"):
    return client.build_request(model=model or client.model_interactive(), system="You are a test.",
                                messages=[{"role": "user", "content": user}], feature="note", schema=schemas.NOTE)


def test_status_without_a_key_says_stub():
    assert llm_status() == {"live": False, "model_interactive": "claude-sonnet-5-5",
                            "model_batch": "claude-haiku-5-5", "stub": True}


def test_status_follows_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CASEBOOK_LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("CASEBOOK_LLM_BATCH_MODEL", "claude-sonnet-5-5")
    assert llm_status() == {"live": True, "model_interactive": "claude-opus-5-5", "model_batch": "claude-sonnet-5-5"}
    monkeypatch.setenv("CASEBOOK_LLM_MOCK", "1")
    assert llm_status()["live"] is False


def test_request_shape_fits_the_current_api():
    request = _request()
    assert not {"temperature", "top_p", "top_k"} & set(request)           # rejected by the current models
    assert request["messages"][-1]["role"] == "user"                       # no assistant prefill
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["output_config"]["effort"] in ("low", "medium", "high")
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": schemas.NOTE}
    assert "thinking" not in request and "tool_choice" not in request


def test_no_key_and_no_cache_is_unavailable():
    with pytest.raises(client.Unavailable):
        client.complete(_request())


def test_cache_round_trip_and_offline_replay(fake_model, monkeypatch):
    model = fake_model([message(text('{"body": "one"}'))])
    request = _request()
    first, cached = client.complete(request, "note")
    assert cached is False and client.text_of(first) == '{"body": "one"}'
    files = os.listdir(client.cache_dir())
    assert files == [client.cache_key(request) + ".json"]
    with open(os.path.join(client.cache_dir(), files[0]), encoding="utf-8") as f:
        entry = json.load(f)
    assert entry["model"] == request["model"] and entry["request"] == request

    again, cached = client.complete(request, "note")       # the script is used up: a live call would fail
    assert cached is True and again == first and len(model.requests) == 1

    go_offline(monkeypatch)                                 # a demo machine with the cache and no key
    replay, cached = client.complete(request, "note")
    assert cached is True and replay == first
    pieces = [item[1] for item in client.stream(request) if item[0] == "text"]
    assert "".join(pieces) == '{"body": "one"}'


def test_cache_key_covers_model_and_request(fake_model, monkeypatch):
    fake_model([message(text('{"body": "one"}'))])
    client.complete(_request(), "note")
    go_offline(monkeypatch)
    for other in (_request(model="claude-haiku-5-5"), _request(user="something else")):
        with pytest.raises(client.Unavailable):
            client.complete(other)


def test_refusals_and_cut_off_replies_are_not_cached(fake_model, monkeypatch):
    fake_model([message(text(""), stop="refusal"), message(text('{"bo'), stop="max_tokens")])
    assert client.complete(_request(), "note")[0]["stop_reason"] == "refusal"
    assert client.complete(_request(), "note")[0]["stop_reason"] == "max_tokens"
    assert not os.path.isdir(client.cache_dir()) or not os.listdir(client.cache_dir())


def test_refresh_ignores_the_cache(fake_model, monkeypatch):
    model = fake_model([message(text('{"body": "one"}')), message(text('{"body": "two"}'))])
    client.complete(_request(), "note")
    monkeypatch.setenv("CASEBOOK_LLM_REFRESH", "1")
    second, cached = client.complete(_request(), "note")
    assert cached is False and client.text_of(second) == '{"body": "two"}' and len(model.requests) == 2


# ---------------------------------------------------------------- the SDK path
# The real anthropic client, with its HTTP layer replaced in-process: no socket is opened.

def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _reply(body_text, model):
    return _sse([
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                               "content": [], "stop_reason": None, "stop_sequence": None,
                                               "usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": body_text[:5]}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": body_text[5:]}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ])


@pytest.fixture
def sdk(monkeypatch):
    """Route the real SDK client through a handler; returns the list of requests it saw."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    seen = []

    def install(handler):
        def handle(request):
            body = json.loads(request.content)
            seen.append({"path": request.url.path, "beta": request.headers.get("anthropic-beta"), "body": body})
            return handler(body, httpx2)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(client, "_sdk_client", anthropic.Anthropic(
            api_key="test-key", max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle))))
        return seen
    return install


def _ok(body, httpx2):
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                           content=_reply('{"body": "from the wire"}', body["model"]))


def test_sdk_streaming_call_and_request_body(sdk):
    seen = sdk(_ok)
    request = _request()
    pieces, final = [], None
    for item in client.stream(request, "note"):
        if item[0] == "text":
            pieces.append(item[1])
        else:
            final = item
    assert "".join(pieces) == '{"body": "from the wire"}' and len(pieces) == 2
    _, msg, cached = final
    assert cached is False and msg["stop_reason"] == "end_turn"
    assert [b["type"] for b in msg["content"]] == ["thinking", "text"]     # read by type, not position
    assert client.text_of(msg) == '{"body": "from the wire"}'
    sent = seen[0]
    assert sent["path"] == "/v1/messages" and sent["body"]["stream"] is True
    assert sent["body"]["model"] == "claude-sonnet-5-5"
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"
    assert sent["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Sonnet 5.5: refusal fallback is asked for by default.
    assert sent["body"]["fallbacks"] == "default" and client.FALLBACK_BETA in sent["beta"]
    assert client.complete(request, "note")[1] is True and len(seen) == 1   # second time from the cache


def test_sdk_batch_model_gets_no_fallback_parameter(sdk):
    seen = sdk(_ok)
    client.complete(_request(model="claude-haiku-5-5"), "note")
    assert "fallbacks" not in seen[0]["body"] and not seen[0]["beta"]


def test_sdk_fallback_can_be_switched_off(sdk, monkeypatch):
    monkeypatch.setenv("CASEBOOK_LLM_FALLBACK", "0")
    seen = sdk(_ok)
    client.complete(_request(), "note")
    assert "fallbacks" not in seen[0]["body"]


def test_sdk_request_without_fallback_when_the_api_rejects_it(sdk):
    def handler(body, httpx2):
        if "fallbacks" in body:
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                         "message": "fallbacks: not supported"}})
        return _ok(body, httpx2)
    seen = sdk(handler)
    msg, _ = client.complete(_request(), "note")
    assert client.text_of(msg) == '{"body": "from the wire"}'
    assert ["fallbacks" in s["body"] for s in seen] == [True, False]
    assert client._fallback_rejected is True
    client.complete(_request(user="another"), "note")
    assert "fallbacks" not in seen[-1]["body"] and len(seen) == 3


@pytest.mark.parametrize("status,kind,expect", [
    (401, "authentication_error", "authentication failed"),
    (404, "not_found_error", "was not found"),
    (429, "rate_limit_error", "rate limited"),
    (529, "overloaded_error", "API error 529"),
])
def test_sdk_errors_become_llm_errors(sdk, monkeypatch, status, kind, expect):
    monkeypatch.setenv("CASEBOOK_LLM_FALLBACK", "0")
    sdk(lambda body, httpx2: httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": "no"}}))
    with pytest.raises(client.LLMError, match=expect):
        client.complete(_request(), "note")


def _tool_turn(model):
    return _sse([
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                               "content": [], "stop_reason": None, "stop_sequence": None,
                                               "usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Reading the customer record."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_customer", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"record_id\": "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"CUS-1\"}"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ])


def test_investigator_end_to_end_through_the_sdk(sdk, world):
    """The whole tool loop over the real client: every request we build must serialise and round-trip."""
    from agents import investigate

    final = {"blocker": "Countries differ.", "findings": [{"text": "Residence is Nigeria.", "cited_ids": ["CUS-1"]}],
             "next_action": "Ask the customer.", "rfi_draft": "Dear Amina Bello,\n\nPlease list where you have lived."}

    def handler(body, httpx2):
        turn = sum(1 for m in body["messages"] if m["role"] == "assistant")
        content = _tool_turn(body["model"]) if turn == 0 else _reply(json.dumps(final), body["model"])
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    seen = sdk(handler)
    alert, hit = world.case("H-ABROAD")
    events = list(investigate(world.data, alert, hit))
    assert [e["type"] for e in events] == ["thought", "step", "final"]
    assert events[0]["text"] == "Reading the customer record." and events[1]["input"] == {"record_id": "CUS-1"}
    assert events[2]["result"] == {**final, "cited_ids": ["CUS-1"], "model": "claude-sonnet-5-5", "cached": False}

    first, second = (s["body"] for s in seen)
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"} and first["stream"] is True
    assert first["cache_control"] == {"type": "ephemeral"} and first["fallbacks"] == "default"
    assert all(t["strict"] is True and "eager_input_streaming" not in t for t in first["tools"])
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & set(first)
    turn, results = second["messages"][1], second["messages"][2]
    assert [b["type"] for b in turn["content"]] == ["thinking", "tool_use"]
    assert turn["content"][0] == {"type": "thinking", "thinking": "Reading the customer record.", "signature": "sig"}
    assert turn["content"][1]["input"] == {"record_id": "CUS-1"}
    assert results["role"] == "user" and results["content"][0]["tool_use_id"] == "toolu_1"
    assert json.loads(results["content"][0]["content"])["person_records"] == ["CUS-1", "CUS-2"]
