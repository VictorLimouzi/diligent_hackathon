"""The tool-use loop shared by the investigator and the chat.

A manual loop rather than the SDK's tool runner, because each turn has to be
answerable from the on-disk cache, every tool call has to be shown to the
analyst as it happens, and the number of steps is bounded here.

The conversation is append-only: each response's content blocks (thinking
blocks included) go back unchanged, which is what the current models require.
"""
import json
from collections import namedtuple

from . import client, prompts
from .tools import TOOL_DEFS, ToolBox

MAX_TURNS = 6        # model turns that may call tools
MAX_TOOL_CALLS = 12  # tool calls in total

Outcome = namedtuple("Outcome", "text cached steps error request")

_WRAP_UP = ("You have reached the lookup limit for this case. Answer now from what you have, "
            "without calling another tool.")


def _clip(text, limit=600):
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def system_blocks(data, task):
    """Shared role, rules and policy first (one cached prefix for every hit and both features), then the task."""
    policy = prompts.read_policy(getattr(data, "folder", "")) if data is not None else None
    return [prompts.with_policy(prompts.CASE_ROLE, policy), task]


def case_block(alert, hit):
    """What the model is told up front: the alert and the engine's result. No third-party free text."""
    case = {
        "alert": {k: v for k, v in (alert or {}).items() if k != "hit_count"},
        "hit_id": hit.get("hit_id"),
        "hit_name": (hit.get("profile") or {}).get("name"),
        "customer_record_id": hit.get("customer_record_id"),
        "customer_records": [r for c in (hit.get("trace") or {}).get("candidates") or []
                             for r in (c.get("customer") or {}).get("records") or []],
        "engine": {"decision": hit.get("decision"), "reason_code": hit.get("reason_code"),
                   "summary": hit.get("summary"), "blocker": hit.get("blocker"),
                   "counterfactuals": hit.get("counterfactuals") or []},
    }
    return "<case>\n" + json.dumps(case, ensure_ascii=False, indent=1) + "\n</case>"


def run(toolbox: ToolBox, *, system, messages, feature, schema=None, stream_text=False):
    """Generator: yields ("thought", str), ("text", str) and ("step", dict); returns an Outcome.

    With `stream_text` (the chat) the model's text is passed on as it arrives and
    no thoughts are produced; without it (the investigator) the text is the final
    structured answer, and thinking summaries and notes between tool calls come
    out as thoughts.

    Raises client.LLMError (Unavailable included) when a model turn cannot be had.
    `messages` is extended in place.
    """
    index, tool_calls, all_cached = 0, 0, True
    for turn in range(MAX_TURNS + 1):
        last = turn == MAX_TURNS or tool_calls >= MAX_TOOL_CALLS
        request = client.build_request(
            model=client.model_interactive(), system=system, messages=messages, feature=feature,
            tools=TOOL_DEFS, schema=schema, show_thinking=not stream_text, cache_tail=True,
            tool_choice={"type": "none"} if last else None)
        message, cached = None, False
        for item in client.stream(request, feature):
            if item[0] == "text":
                if stream_text:
                    yield ("text", item[1])
            else:
                _, message, cached = item
        all_cached = all_cached and cached
        content = message.get("content") or []
        stop = message.get("stop_reason")
        tool_uses = [b for b in content if b.get("type") == "tool_use"]

        for block in content if not stream_text else ():
            if block.get("type") == "thinking" and (block.get("thinking") or "").strip():
                yield ("thought", _clip(block["thinking"].strip()))
            elif block.get("type") == "text" and tool_uses:
                note = (block.get("text") or "").strip()
                if note and not note.startswith(("{", "[")):
                    yield ("thought", _clip(note))

        # A refusal or a truncated turn can carry a half-written tool call: never run it.
        if stop == "refusal":
            return Outcome(client.text_of(message), all_cached, index, "the model declined the request", request)
        if stop == "max_tokens":
            return Outcome(client.text_of(message), all_cached, index, "the reply was cut off", request)
        if not tool_uses:
            return Outcome(client.text_of(message), all_cached, index, None, request)
        if last:
            return Outcome("", all_cached, index, "the model kept calling tools past the limit", request)

        messages.append({"role": "assistant", "content": content})
        results = []
        for block in tool_uses:
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            result = toolbox.call(block.get("name"), block.get("input"))
            yield ("step", {"type": "step", "index": index, "tool": str(block.get("name")), "input": args,
                            "summary": result.summary})
            index += 1
            entry = {"type": "tool_result", "tool_use_id": block.get("id"), "content": toolbox.as_text(result)}
            if not result.ok:
                entry["is_error"] = True
            results.append(entry)
        tool_calls += len(tool_uses)
        wrap_up = turn + 1 == MAX_TURNS or tool_calls >= MAX_TOOL_CALLS
        if wrap_up and not client.takes_system_turns(request["model"]):
            results.append({"type": "text", "text": _WRAP_UP})
        # Every result of the turn goes back in one user message.
        messages.append({"role": "user", "content": results})
        if wrap_up and client.takes_system_turns(request["model"]):
            # A harness notice belongs in the operator channel, not next to tool output,
            # where the model could take it for injected text.
            messages.append({"role": "system", "content": _WRAP_UP})
    return Outcome("", all_cached, index, "no final answer", None)


def events(runner, state):
    """Turn the loop's items into contract events; returns the loop's Outcome.

    `state["steps"]` counts the steps shown and `state["text"]` collects the text streamed.
    """
    while True:
        try:
            kind, payload = next(runner)
        except StopIteration as stop:
            return stop.value
        if kind == "thought":
            yield {"type": "thought", "text": payload}
        elif kind == "text":
            state.setdefault("text", []).append(payload)
            yield {"type": "text", "delta": payload}
        else:
            state["steps"] = state.get("steps", 0) + 1
            yield payload
