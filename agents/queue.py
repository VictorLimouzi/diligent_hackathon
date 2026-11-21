"""Two or three sentences on what is blocking the review queue, from the blocker groups."""
import json
import re

from . import client, mock, prompts, schemas

MAX_TEXT = 900


def queue_summary(groups):
    """{"text", "cached"}; plus "stub": true when no model wrote it."""
    try:
        return _summary(groups)
    except Exception:  # noqa: BLE001 - a request must never crash
        return _offline(groups)


def _clean(groups):
    out = []
    for g in groups or []:
        if isinstance(g, dict) and g.get("blocker") and isinstance(g.get("count"), int) and g["count"] > 0:
            out.append({"blocker": str(g["blocker"]), "count": g["count"]})
    return sorted(out, key=lambda g: -g["count"])


def _offline(groups):
    try:
        text_ = mock.queue_text(_clean(groups))
    except Exception:  # noqa: BLE001
        text_ = "The review queue could not be summarised."
    return {"text": text_, "cached": False, "stub": True}


def _usable(text_, blockers):
    """A summary may only use the numbers it was given: any other figure is a made-up one."""
    if not text_ or len(text_) > MAX_TEXT or schemas.find_ids(text_):
        return False
    total = sum(g["count"] for g in blockers)
    given = {g["count"] for g in blockers} | {total, len(blockers)}
    given |= {round(100 * g["count"] / total) for g in blockers}
    for number in re.findall(r"(?<![\w.])\d+(?![\w.])", text_):
        if int(number) > 3 and int(number) not in given:
            return False
    return True


def _summary(groups):
    blockers = _clean(groups)
    if not blockers:
        return _offline(groups)
    payload = {"hits_waiting": sum(g["count"] for g in blockers), "blockers": blockers}
    request = client.build_request(
        model=client.model_interactive(), system=prompts.QUEUE, feature="queue", schema=schemas.QUEUE,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}])
    try:
        message, cached = client.complete(request, "queue")
    except client.LLMError:
        return _offline(groups)
    if message.get("stop_reason") in ("refusal", "max_tokens"):
        return _offline(groups)
    value, errors = schemas.parse(client.text_of(message), schemas.QUEUE)
    text_ = "" if errors else value["text"].strip()
    if not _usable(text_, blockers):
        client.forget(request)
        return _offline(groups)
    return {"text": text_, "cached": cached}
