"""Bridges to the advisory packages (ml/, agents/).

Each bridge uses the real package when it is installed and working, and a
plain stand-in otherwise, so the server and the UI run at every stage of the
build. Stand-ins say they are stand-ins.
"""
import re


def llm_status():
    try:
        from agents import llm_status as real
        return real()
    except Exception:  # noqa: BLE001
        return {"live": False, "model_interactive": None, "model_batch": None, "stub": True}


def _cited(text_, known):
    return [i for i in dict.fromkeys(re.findall(r"\b(?:CUS|KYC|NOTE|SR|H)-[A-Z0-9]+\b", text_ or "")) if i in known]


def write_note(state, alert, hit):
    try:
        from agents import write_note as real
        return real(hit)
    except ImportError:
        note = f"{hit['hit_id']}: {hit['decision']} ({hit['reason_code']}). {hit['summary']}"
        return {"note": note, "cited_ids": hit.get("evidence", []), "cached": False, "model": None, "stub": True}


def investigate(state, alert, hit):
    try:
        from agents import investigate as real
    except ImportError:
        real = None
    if real:
        yield from real(state.data, alert, hit)
        return
    cust = hit.get("customer_record_id")
    yield {"type": "step", "index": 0, "tool": "get_hit_profile", "input": {"hit_id": hit["hit_id"]},
           "summary": f"Read the profile of {hit['profile'].get('name', hit['hit_id'])}."}
    if cust and cust != "AMBIGUOUS":
        yield {"type": "step", "index": 1, "tool": "get_customer", "input": {"record_id": cust},
               "summary": f"Read customer record {cust}."}
    cfs = hit.get("counterfactuals") or []
    yield {"type": "final", "result": {
        "blocker": hit.get("blocker") or "None: the engine decided this hit.",
        "findings": [{"text": hit["summary"], "cited_ids": hit.get("evidence", [])}],
        "next_action": cfs[0]["question"] if cfs else "No action needed.",
        "rfi_draft": None, "cited_ids": hit.get("evidence", []), "model": None, "cached": False, "stub": True}}


def chat(state, alert, hit, messages):
    try:
        from agents import chat as real
    except ImportError:
        real = None
    if real:
        yield from real(state.data, alert, hit, messages)
        return
    text_ = "The language model is not connected yet. The engine's summary for this hit: " + hit["summary"]
    for word in re.findall(r"\S+\s*", text_):
        yield {"type": "text", "delta": word}
    yield {"type": "done", "cited_ids": hit.get("evidence", []), "stub": True}


def queue_summary(state, groups):
    try:
        from agents import queue_summary as real
        return real(groups)
    except ImportError:
        total = sum(g["count"] for g in groups)
        parts = "; ".join(f"{g['count']} {g['blocker'].lower()}" for g in groups)
        return {"text": f"{total} hits are waiting for review: {parts}.", "cached": False, "stub": True}


_search = None


def search_customers(state, q, k=10):
    """Fuzzy customer search: the ML retrieval index when present, else a token scan."""
    global _search
    try:
        from ml.search import CustomerSearch
        if _search is None:
            _search = CustomerSearch(state.data)
        return _search.query(q, k)
    except ImportError:
        toks = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 1]
        out = []
        for cid, c in state.data.customers.items():
            name = str(c.get("full_name") or "").lower()
            score = sum(1 for t in toks if t in name) / max(len(toks), 1)
            if score > 0:
                out.append({"record_id": cid, "full_name": c.get("full_name"), "dob": c.get("dob"),
                            "country": c.get("residence_country"), "score": round(score, 3)})
        out.sort(key=lambda r: (-r["score"], r["record_id"]))
        return out[:k]
