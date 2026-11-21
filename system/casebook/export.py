"""Write the engine's results: the decisions file, and the casebook the workbench reads."""
import json
import os

from .load import Data, as_list, text

# One line per hit, in the order the brief lists the fields.
OUTPUT_FIELDS = ("hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "reason", "evidence")
# The part of a line that is the decision itself (the golden test hashes only this).
DECISION_FIELDS = ("hit_id", "alert_id", "decision", "reason_code", "customer_record_id", "evidence")
_PROFILE_FIELDS = ("name", "aliases", "entity_type", "gender", "dobs", "place_of_birth", "nationality",
                   "citizenship", "countries", "addresses", "categories", "primary_category",
                   "position_or_allegation", "match_score", "life_status", "deceased_date")


def write_decisions(results, path, fields=OUTPUT_FIELDS):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({k: r[k] for k in fields}, ensure_ascii=False) + "\n")


def _cited_ids(result):
    ids = set(result.get("evidence") or [])
    trace = result.get("trace") or {}
    ids.update(trace.get("hit", {}).get("dob_sources", []))
    for s in trace.get("hit", {}).get("search_considered", []):
        ids.add(s.get("result_id"))
    for c in trace.get("candidates", []):
        cust = c["customer"]
        ids.update(cust["records"])
        ids.add(cust.get("verified_check"))
        ids.add(cust.get("dob_source"))
        ids.update(f["source"] for f in cust["countries"])
        ids.update(c.get("evidence") or [])
    return {i for i in ids if i}


def build_casebook(data: Data, results):
    labels = {l["hit_id"]: l for l in data.labels}
    by_alert = {}
    for r in results:
        by_alert.setdefault(r["alert_id"], []).append(r)
    records, alerts = {}, []
    for a in data.alerts:
        hits = []
        for r in by_alert.get(a.get("alert_id"), []):
            h = data.hits.get(r["hit_id"], {})
            lab = labels.get(r["hit_id"])
            hits.append({
                **{k: v for k, v in r.items() if k != "features"},
                "ml": None,    # filled by the advisory models (see contracts/)
                "llm": None,   # filled by the second-opinion batch
                "profile": {k: text(h.get(k)) for k in _PROFILE_FIELDS if h.get(k)},
                "profile_notes": text(h.get("profile_notes"))[:2500],
                "label": {"decision": lab["decision"], "reason_codes": as_list(lab.get("reason_codes"))} if lab else None,
            })
            for i in _cited_ids(r):
                rec = data.by_id.get(i)
                if rec is not None and i not in records:
                    records[i] = {k: text(v) for k, v in rec.items() if v not in (None, "")}
        alerts.append({
            "alert": {k: text(v) for k, v in a.items() if v not in (None, "") and k != "hit_ids"},
            "hits": hits,
        })
    return {"alerts": alerts, "records": records, "source": os.path.basename(os.path.abspath(data.folder))}


def write_workbench(casebook, path):
    """One self-contained HTML page: the template with the casebook inlined."""
    template = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "workbench", "template.html")
    with open(template, encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(casebook, ensure_ascii=False).replace("</", "<\\/")
    marker = "/*__CASEBOOK__*/null"
    if marker not in html:
        raise ValueError("workbench template has no casebook marker")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html.replace(marker, blob))
