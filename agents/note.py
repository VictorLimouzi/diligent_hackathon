"""Case notes written from the hit's trace, and nothing else.

The first line (hit ID, decision, reason code) is written by code, so the note
can only ever state the engine's decision. The body comes from the model and is
checked before it is returned: every record ID in it must appear in the hit's
trace or evidence. One retry on failure, then the engine's own summary.
"""
import json

from . import client, mock, prompts, schemas
from .tools import untrusted

MAX_BODY = 2500


def write_note(hit):
    """{"note", "cited_ids", "cached", "model"}; plus "stub": true when no model wrote it."""
    try:
        return _write(hit)
    except Exception:  # noqa: BLE001 - a request must never crash
        return _engine_summary(hit)


def note_facts(hit):
    """What the model sees: the engine's result and trace. Third-party quotes are wrapped; snippets are left out."""
    trace = hit.get("trace") or {}
    facts = dict(trace.get("hit") or {})
    considered = facts.pop("search_considered", None) or []
    for key in ("dob_quote", "earliest_report_line"):
        if facts.get(key):
            facts[key] = untrusted(facts[key], 300)
    facts["search_results_read"] = len(considered)
    facts["search_results_about_hit"] = [s.get("result_id") for s in considered if s.get("about_hit")]
    return {
        "hit_id": hit.get("hit_id"), "alert_id": hit.get("alert_id"),
        "decision": hit.get("decision"), "reason_code": hit.get("reason_code"),
        "reason_codes": hit.get("reason_codes") or [], "customer_record_id": hit.get("customer_record_id"),
        "evidence": hit.get("evidence") or [], "summary": hit.get("summary"), "blocker": hit.get("blocker"),
        "counterfactuals": hit.get("counterfactuals") or [],
        "how_the_customer_was_found": trace.get("resolution") or [],
        "hit": facts,
        "candidates": trace.get("candidates") or [],
    }


def problem_with(body, allowed):
    """Why a note body cannot be used, or None when it can."""
    if not isinstance(body, str) or not body.strip():
        return "it is empty."
    if len(body) > MAX_BODY:
        return "it is too long."
    unknown = [i for i in schemas.find_ids(body) if i not in allowed]
    if unknown:
        return f"it cites record IDs that are not in the case: {', '.join(unknown)}."
    return None


def _result(hit, body, allowed, cached, model, stub=False):
    note = f"{mock.note_header(hit)}\n{body.strip()}"
    out = {"note": note, "cited_ids": [i for i in schemas.find_ids(body) if i in allowed],
           "cached": cached, "model": model}
    if stub:
        out["stub"] = True
    return out


def _engine_summary(hit):
    return {"note": mock.engine_note(hit), "cited_ids": list(hit.get("evidence") or []),
            "cached": False, "model": None, "stub": True}


def _write(hit):
    allowed = schemas.hit_ids(hit)
    messages = [{"role": "user", "content": "Write the note body for this hit.\n\n<case>\n"
                 + json.dumps(note_facts(hit), ensure_ascii=False, indent=1) + "\n</case>"}]
    all_cached, asked = True, []
    for _attempt in range(2):
        request = client.build_request(model=client.model_interactive(), system=prompts.NOTE,
                                       messages=messages, feature="note", schema=schemas.NOTE)
        asked.append(request)
        try:
            message, cached = client.complete(request, "note")
        except client.LLMError:
            # No model: the templated note, built from the same trace and checked the same way.
            body = mock.note_body(hit)
            if problem_with(body, allowed):
                return _engine_summary(hit)
            return _result(hit, body, allowed, False, None, stub=True)
        all_cached = all_cached and cached
        if message.get("stop_reason") in ("refusal", "max_tokens"):
            problem = "the reply was not completed."
        else:
            value, errors = schemas.parse(client.text_of(message), schemas.NOTE)
            problem = "it is not the JSON object asked for." if errors else problem_with(value["body"], allowed)
            if not problem:
                return _result(hit, value["body"], allowed, all_cached, client.model_interactive())
        # One retry, with the reason. The earlier turn goes back unchanged (append-only history).
        messages = messages + [
            {"role": "assistant", "content": message.get("content") or []},
            {"role": "user", "content": f"That note cannot be used: {problem} Write the body again, citing only "
                                        f"these record IDs: {', '.join(sorted(allowed))}."}]
    for request in asked:
        client.forget(request)   # two unusable answers are not worth replaying; a later call asks again
    return _engine_summary(hit)
