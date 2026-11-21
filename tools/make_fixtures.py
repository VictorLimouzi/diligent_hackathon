"""Cut contract fixtures from the running server, so examples always match real output.

Usage: .venv/bin/python tools/make_fixtures.py   (needs the dev data)

Picks a small set of alerts covering every decision, reason code and blocker.
ML and LLM blocks do not exist until those streams land, so fixtures carry
clearly marked sample values for them ("fixture": true) to let the UI be built.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["CASEBOOK_OUT"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

import server.app as app_module  # noqa: E402

OUT = os.path.join(ROOT, "contracts", "fixtures")
MUST = ["A-0DF328", "A-3983D4", "A-AE9222", "A-957E63", "A-CAF975"]


def main():
    os.makedirs(OUT, exist_ok=True)
    c = TestClient(app_module.app)
    state = app_module.get_state()

    want, chosen = set(), list(MUST)
    for h in state.hits.values():
        want.add(("code", h["reason_code"]))
        if h.get("blocker"):
            want.add(("blocker", h["blocker"]))
    covered = set()

    def cover(aid):
        for h in state.alerts[aid]["hits"]:
            covered.add(("code", h["reason_code"]))
            if h.get("blocker"):
                covered.add(("blocker", h["blocker"]))

    for aid in chosen:
        cover(aid)
    for aid, a in state.alerts.items():
        if want <= covered:
            break
        new = {("code", h["reason_code"]) for h in a["hits"]} | \
              {("blocker", h["blocker"]) for h in a["hits"] if h.get("blocker")}
        if not new <= covered and len(a["hits"]) <= 4:
            chosen.append(aid)
            cover(aid)

    def sample_ml(h, i):
        review = h["decision"] == "REVIEW"
        return {"fixture": True, "priority": round(0.15 + (i * 37 % 80) / 100, 2) if review else None,
                "name_similarity": round(0.55 + (i * 13 % 45) / 100, 2),
                "near_miss": h["reason_code"] == "NAME_MISMATCH_VERIFIED" and i % 3 == 0,
                "anomaly": round((i * 29 % 100) / 100, 2),
                "drivers": [{"feature": "name_rarity", "label": "Name is rare among customers", "value": "3 of 72,265", "effect": 0.21},
                            {"feature": "match_score", "label": "Screening match score", "value": "96", "effect": 0.08},
                            {"feature": "alert_hit_count", "label": "Hits on this alert", "value": "10", "effect": -0.12}] if review else []}

    def sample_llm(h, i):
        agrees = i % 5 != 0
        other = "REVIEW" if h["decision"] != "REVIEW" else "SAME_ENTITY"
        return {"second_opinion": {"fixture": True, "decision": h["decision"] if agrees else other,
                                   "reason_code": h["reason_code"] if agrees else None,
                                   "rationale": "Sample rationale for building the UI; not model output.",
                                   "agrees": agrees, "model": "fixture"}}

    alerts = {}
    i = 0
    for aid in chosen:
        a = json.loads(json.dumps(c.get(f"/api/alerts/{aid}").json()))
        for h in a["hits"]:
            i += 1
            h["ml"], h["llm"] = sample_ml(h, i), sample_llm(h, i)
            if h["trace"]:
                h["trace"]["hit"]["search_considered"] = h["trace"]["hit"]["search_considered"][:8]
        alerts[aid] = a

    def dump(name, obj):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    rows = [r for r in c.get("/api/alerts").json() if r["alert_id"] in alerts]
    for r in rows:
        prios = [h["ml"]["priority"] for h in alerts[r["alert_id"]]["hits"] if h["ml"]["priority"] is not None]
        r["priority"] = max(prios) if prios else None
    dump("alerts.json", rows)
    dump("alert_details.json", alerts)
    dump("summary.json", c.get("/api/summary").json())

    ids = set()
    for a in alerts.values():
        for h in a["hits"]:
            ids.update(h["evidence"])
            ids.add(h["hit_id"])
            if h["trace"]:
                ids.update(s["result_id"] for s in h["trace"]["hit"]["search_considered"])
                for cand in h["trace"]["candidates"]:
                    ids.update(cand["customer"]["records"])
                    ids.update(x for x in [cand["customer"]["verified_check"], cand["customer"]["dob_source"]] if x)
                    ids.update(f["source"] for f in cand["customer"]["countries"])
    records = {}
    for rid in sorted(ids):
        r = c.get(f"/api/records/{rid}")
        if r.status_code == 200:
            records[rid] = r.json()
    dump("records.json", records)

    qa = c.get("/api/qa").json()
    hit_ids = {h["hit_id"] for a in alerts.values() for h in a["hits"]}
    fx_hits = [h for a in alerts.values() for h in a["hits"]]
    row = lambda h: {"hit_id": h["hit_id"], "alert_id": h["alert_id"], "hit_name": h["profile"].get("name", ""),
                     "decision": h["decision"], "reason_code": h["reason_code"]}
    qa["labels"] = [x for x in qa["labels"] if x["hit_id"] in hit_ids]
    qa["second_opinion"] = {
        "compared": len(fx_hits), "matrix": {},
        "disagreements": [{**row(h), "llm_decision": h["llm"]["second_opinion"]["decision"], "llm_reason_code": None,
                           "rationale": h["llm"]["second_opinion"]["rationale"]}
                          for h in fx_hits if not h["llm"]["second_opinion"]["agrees"]]}
    for h in fx_hits:
        k = f'{h["decision"]}|{h["llm"]["second_opinion"]["decision"]}'
        qa["second_opinion"]["matrix"][k] = qa["second_opinion"]["matrix"].get(k, 0) + 1
    qa["near_misses"] = [{**row(h), "name_similarity": h["ml"]["name_similarity"], "customer_name": "Sample Name"}
                         for h in fx_hits if h["ml"]["near_miss"]]
    qa["anomalies"] = sorted(({**row(h), "anomaly": h["ml"]["anomaly"]} for h in fx_hits
                              if h["decision"] == "DIFFERENT_ENTITY"), key=lambda r: -r["anomaly"])[:10]
    qa["ml_metrics"] = {"fixture": True, "ranker_masked_auc": 0.87, "name_model_accuracy": 0.96}
    dump("qa.json", qa)
    dump("queue_summary.json", c.get("/api/queue/summary").json())
    dump("search_customers.json", c.get("/api/search/customers", params={"q": "markus muller"}).json())

    hid = "H-ADF453"
    dump("note.json", c.post(f"/api/hits/{hid}/note").json())
    dump("investigate_events.json", [
        {"type": "thought", "text": "Name and birth date agree but the countries differ. I will check for any tie to the United States."},
        {"type": "step", "index": 0, "tool": "get_hit_profile", "input": {"hit_id": hid}, "summary": "Marcus Miller, Enforcement, United States, born 1979 or 1980."},
        {"type": "step", "index": 1, "tool": "get_customer", "input": {"record_id": "CUS-375608"}, "summary": "Markus Müller, residence and nationality Germany."},
        {"type": "step", "index": 2, "tool": "get_notes", "input": {"record_id": "CUS-375608"}, "summary": "3 notes, none mention living abroad."},
        {"type": "final", "result": {
            "blocker": "Name and birth date agree, countries differ",
            "findings": [{"text": "Verified name and date of birth match the profile.", "cited_ids": ["KYC-A932A4"]},
                         {"text": "No record ties the customer to the United States.", "cited_ids": ["CUS-375608"]}],
            "next_action": "Ask the customer whether they have lived or worked in the United States.",
            "rfi_draft": "Dear Mr Müller,\n\nAs part of a routine review we need to confirm the countries you have lived or worked in since 2015. ...",
            "cited_ids": ["KYC-A932A4", "CUS-375608"], "model": "fixture", "cached": False}}])
    dump("chat_events.json", [
        {"type": "step", "index": 0, "tool": "get_customer", "input": {"record_id": "CUS-375608"}, "summary": "Read customer record CUS-375608."},
        {"type": "text", "delta": "The hit cannot be cleared on country "},
        {"type": "text", "delta": "because the verified name and date of birth (KYC-A932A4) both agree with the profile."},
        {"type": "done", "cited_ids": ["KYC-A932A4"]}])
    entry = c.post(f"/api/hits/{hid}/review", json={"decision": "SAME_ENTITY", "reason_code": "NAME_DOB_MATCH",
                                                    "comment": "Customer confirmed a US address 2016-2019.", "analyst": "analyst"}).json()
    dump("audit.json", [entry])
    print(f"{len(alerts)} alerts, {len(fx_hits)} hits, {len(records)} records -> {OUT}")
    codes = sorted({h["reason_code"] for h in fx_hits})
    print("reason codes covered:", ", ".join(codes))


if __name__ == "__main__":
    main()
