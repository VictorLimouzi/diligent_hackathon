"""The investigator: looks for the fact that would settle a hit and reports whether it is on file.

It starts from the hit's counterfactuals, which already name the fact, runs a
bounded tool-use loop over the alert's own records, and ends with one "final"
event. It reports and drafts; it never decides. Without a model it runs a
planned set of the same lookups and words the result from a template
(`stub: true`).
"""
from . import client, loop, mock, prompts, schemas
from .tools import ToolBox


def investigate(data, alert, hit):
    """Yield InvestigateEvents for one hit; the last one is always {"type": "final", ...}."""
    try:
        yield from _investigate(data, alert, hit)
    except Exception as exc:  # noqa: BLE001 - a request must never crash
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        yield {"type": "final", "result": _last_resort(hit)}


def _investigate(data, alert, hit):
    toolbox = ToolBox(data, alert, hit)
    messages = [{"role": "user", "content": "Investigate this hit.\n\n" + loop.case_block(alert, hit)}]
    state = {"steps": 0}
    result, why = None, None
    try:
        outcome = yield from loop.events(
            loop.run(toolbox, system=loop.system_blocks(data, prompts.INVESTIGATE), messages=messages,
                     feature="investigate", schema=schemas.INVESTIGATION), state)
        if outcome.error is None:
            result = _final(outcome, toolbox, alert, hit)
            if result is None and outcome.request is not None:
                client.forget(outcome.request)   # do not replay an answer that failed validation
        why = outcome.error or "its answer did not pass validation"
    except client.Unavailable:
        why = None
    except client.LLMError as exc:
        why = str(exc)
    if result is not None:
        yield {"type": "final", "result": result}
        return

    if state["steps"] == 0:
        offline = mock.investigation(toolbox, alert, hit)
        thought = offline["thought"]
        if why:
            thought = f"The language model could not be used ({why}). " + thought
        yield {"type": "thought", "text": thought}
        yield from offline["steps"]
        yield {"type": "final", "result": offline["result"]}
    else:
        reason = why or "no language model is connected for the rest of this investigation"
        yield {"type": "thought", "text": f"The language model did not finish ({reason}). The result below is "
                                          "built from the lookups above with a template."}
        yield {"type": "final", "result": mock.conclude(toolbox, alert, hit)}


def _final(outcome, toolbox, alert, hit):
    """The model's answer as an InvestigationResult, or None when it does not hold up."""
    value, errors = schemas.parse(outcome.text, schemas.INVESTIGATION)
    if errors:
        return None
    known = schemas.hit_ids(hit) | toolbox.seen_ids
    findings = []
    for f in value["findings"]:
        text_ = f["text"].strip()
        if text_:
            # An ID the tools never returned cannot be opened by the analyst: drop it.
            findings.append({"text": text_, "cited_ids": [i for i in dict.fromkeys(f["cited_ids"]) if i in known]})
    blocker, action = value["blocker"].strip(), value["next_action"].strip()
    if not (findings and blocker and action):
        return None
    draft = None
    if hit.get("decision") == "REVIEW":
        draft = (value["rfi_draft"] or "").strip()
        if not draft or mock.rfi_leaks(draft, hit):
            # No draft, or one that would tell the customer about the match: use the neutral template.
            draft = mock.rfi_draft(alert, hit)
    return {
        "blocker": blocker, "findings": findings, "next_action": action, "rfi_draft": draft,
        "cited_ids": list(dict.fromkeys(i for f in findings for i in f["cited_ids"])),
        "model": client.model_interactive(), "cached": outcome.cached,
    }


def _last_resort(hit):
    cfs = hit.get("counterfactuals") or []
    evidence = list(hit.get("evidence") or [])
    return {
        "blocker": hit.get("blocker") or "None: the engine decided this hit.",
        "findings": [{"text": hit.get("summary") or "", "cited_ids": evidence}],
        "next_action": cfs[0].get("question") if cfs else "No action needed.",
        "rfi_draft": None, "cited_ids": evidence, "model": None, "cached": False, "stub": True,
    }
