"""Ask Casebook: questions about one hit, answered from its records.

Same tools and scope as the investigator. The conversation history comes in as
plain text turns; the answer streams out as ChatEvents and ends with one "done"
event listing the record IDs the answer cites.
"""
import re

from . import client, loop, mock, prompts, schemas
from .tools import ToolBox

MAX_HISTORY = 20
MAX_MESSAGE = 4000


def chat(data, alert, hit, messages):
    """Yield ChatEvents; the last one is always {"type": "done", ...}."""
    try:
        yield from _chat(data, alert, hit, messages)
    except Exception as exc:  # noqa: BLE001 - a request must never crash
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        yield {"type": "done", "cited_ids": [], "stub": True}


def _history(messages):
    turns = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            turns.append({"role": m["role"], "content": content.strip()[:MAX_MESSAGE]})
    turns = turns[-MAX_HISTORY:]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def _chat(data, alert, hit, messages):
    turns = _history(messages)
    if not turns or turns[-1]["role"] != "user":
        yield {"type": "error", "message": "The conversation must end with a question from the analyst."}
        yield {"type": "done", "cited_ids": [], "stub": True}
        return
    question = turns[-1]["content"]
    toolbox = ToolBox(data, alert, hit)
    # The case block rides in front of the first turn, so the prefix is the same on every turn of a conversation.
    api_messages = [dict(t) for t in turns]
    api_messages[0]["content"] = loop.case_block(alert, hit) + "\n\n" + api_messages[0]["content"]

    state = {"steps": 0, "text": []}
    why = None
    try:
        outcome = yield from loop.events(
            loop.run(toolbox, system=loop.system_blocks(data, prompts.CHAT), messages=api_messages,
                     feature="chat", stream_text=True), state)
        said = "".join(state["text"])
        if outcome.error is None and said.strip():
            known = schemas.hit_ids(hit) | toolbox.seen_ids
            yield {"type": "done", "cited_ids": [i for i in schemas.find_ids(said) if i in known]}
            return
        why = outcome.error or "it gave no answer"
    except client.Unavailable:
        why = None
    except client.LLMError as exc:
        why = str(exc)

    # Offline: real lookups chosen from the question, templated wording.
    first = state["steps"]
    results = [(tool, args, toolbox.call(tool, args)) for tool, args in mock.chat_plan(hit, question)]
    for i, (tool, args, res) in enumerate(results):
        yield {"type": "step", "index": first + i, "tool": tool, "input": args, "summary": res.summary}
    answer = mock.chat_answer(hit, question, results)
    if why:
        answer = f"The language model could not answer ({why}). " + answer
    if state["text"]:
        answer = "\n\n" + answer
    for piece in re.findall(r"\S+\s*|\s+", answer):
        yield {"type": "text", "delta": piece}
    known = schemas.hit_ids(hit) | toolbox.seen_ids
    said = "".join(state["text"]) + answer
    yield {"type": "done", "cited_ids": [i for i in schemas.find_ids(said) if i in known], "stub": True}
