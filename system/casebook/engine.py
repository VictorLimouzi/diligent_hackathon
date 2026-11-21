"""Decide every hit of every alert, and keep the trail that led to each decision."""
from .customer import build_picture, resolve
from .features import blocker, counterfactuals, feature_row
from .dates import parse_date
from .hit import gather
from .load import Data, as_list, text
from .policy import REVIEW, Decision, decide


# One customer's decision in a few words, for a hit that several customers fit.
_IN_WORDS = {
    "ENTITY_TYPE_MISMATCH": "be cleared (a person against a company)",
    "DOB_MISMATCH": "be cleared (the date of birth contradicts the hit's)",
    "NAME_MISMATCH_VERIFIED": "be cleared (the verified name does not match)",
    "TEMPORAL_IMPOSSIBILITY": "be cleared (too young, or not yet incorporated, at the earliest report)",
    "DECEASED": "be cleared (the listed party died before the screening)",
    "LOCATION_MISMATCH": "be cleared (no country is shared)",
    "NAME_DOB_LOCATION_MATCH": "be confirmed (name, date of birth and country agree)",
    "NAME_DOB_MATCH": "be confirmed (name and date of birth agree)",
    "NAME_LOCATION_MATCH": "be confirmed (name and country agree)",
    "INSUFFICIENT_DATA": "go to review (nothing contradicts and too little agrees)",
}


def _picture_view(pic):
    return {
        "records": list(pic.records),
        "primary": pic.primary,
        "entity_type": None if pic.company is None else ("Company" if pic.company else "Individual"),
        "verified_check": pic.verified["id"] if pic.verified else None,
        "verified_name": pic.verified_name.value if pic.verified_name else None,
        "declared_names": [{"value": f.value, "source": f.source} for f in pic.declared_names],
        "dob": pic.dob.value.iso() if pic.dob else None,
        "dob_source": pic.dob.source if pic.dob else None,
        "dob_verified": pic.dob_verified,
        "countries": [{"value": f.value, "source": f.source} for f in pic.countries],
        "path": pic.path,
    }


def _hit_view(h):
    return {
        "names": h.names,
        "dobs": [d.iso() for d in h.dobs],
        "dob_origin": h.dob_origin,
        "dob_quote": h.dob_quote,
        "dob_sources": h.dob_sources,
        "countries": h.countries,
        "earliest_report": h.earliest_report.iso() if h.earliest_report else None,
        "earliest_report_line": h.earliest_report_line,
        "death": h.death.iso() if h.death else None,
        "search_considered": h.search_considered,
    }


def _decision_view(d: Decision):
    return {
        "decision": d.decision,
        "reason_codes": d.reason_codes,
        "evidence": d.evidence,
        "summary": d.summary,
        "checks": [{"name": c.name, "outcome": c.outcome, "detail": c.detail, "sources": c.sources}
                   for c in d.checks],
    }


def decide_alert(data: Data, alert):
    """One result per hit of the alert."""
    res = resolve(data, alert)
    screened_at = parse_date(alert.get("screened_at"))
    if res.persons:
        pictures = [build_picture(data, p, res.primary[p], alert) for p in res.persons]
    else:
        # No record found: only what was screened is known about the customer.
        pictures = [build_picture(data, (), None, alert)]

    results = []
    for hit_id in data.alert_hit_ids(alert):
        hit = data.hits.get(hit_id)
        if hit is None:
            results.append({
                "hit_id": hit_id, "alert_id": alert.get("alert_id"), "decision": REVIEW,
                "reason_code": "INSUFFICIENT_DATA", "reason_codes": ["INSUFFICIENT_DATA"],
                "customer_record_id": None, "evidence": [],
                "reason": "The hit's profile was not found in the data folder. Without it nothing can be "
                          "compared, so a person must review the hit.",
                "summary": "The hit's profile was not found.", "trace": None,
                "blocker": "Hit profile not found", "counterfactuals": [], "features": None,
            })
            continue
        facts = gather(data, hit)
        per_person = [decide(data, pic, facts, screened_at) for pic in pictures]

        if len(per_person) == 1:
            d = per_person[0]
            decision, codes, evidence, summary = d.decision, d.reason_codes, d.evidence, d.summary
            reason = d.reason
            record = pictures[0].primary
        else:
            record = "AMBIGUOUS"
            evidence = []
            for d in per_person:
                for e in d.evidence:
                    if e not in evidence:
                        evidence.append(e)
            if len({d.decision for d in per_person}) == 1:
                decision = per_person[0].decision
                shared = [c for c in per_person[0].reason_codes
                          if all(c in d.reason_codes for d in per_person)]
                codes = shared or per_person[0].reason_codes
                lead = f"{len(per_person)} customers fit the alert and each leads to the same decision."
                summary = lead + " " + per_person[0].summary
                first = per_person[0].reason_parts
                reason = " ".join([lead] + first[:2] + first[-1:]) if len(first) > 3 else " ".join([lead] + first)
            else:
                decision, codes = REVIEW, ["AMBIGUOUS_CUSTOMER_RECORD"]
                evidence = []
                for pic in pictures:
                    for r in pic.records:
                        if r not in evidence:
                            evidence.append(r)
                parts = [f"{pic.primary} would {_IN_WORDS.get(d.reason_code, 'go to review')}"
                         for pic, d in zip(pictures, per_person)]
                summary = f"{len(per_person)} customers fit the alert and lead to different decisions: " \
                          + "; ".join(parts) + "."
                reason = summary + (" The alert carries no account, login or email that picks one of them, "
                                    "so a person must review it.")

        result = {
            "hit_id": hit_id,
            "alert_id": alert.get("alert_id"),
            "decision": decision,
            "reason_code": codes[0],
            "reason_codes": codes,
            "customer_record_id": record,
            "evidence": evidence,
            "reason": reason,
            "summary": summary,
            "trace": {
                "resolution": res.path,
                "hit": _hit_view(facts),
                "candidates": [
                    {"customer": _picture_view(pic), **_decision_view(d)}
                    for pic, d in zip(pictures, per_person)
                ],
            },
        }
        # Advisory add-ons, computed after the decision and never feeding back into it.
        result["blocker"] = blocker(pictures, per_person, decision, codes[0])
        result["counterfactuals"] = counterfactuals(data, pictures, facts, per_person, screened_at, decision)
        # A review says what would settle it, as the last clause of its reason.
        settle = next((c["settle"] for c in result["counterfactuals"] if c.get("settle")), None)
        if settle and decision == REVIEW and len(per_person) == 1:
            result["reason"] = result["reason"].rstrip(".") + "; " + settle + "."
        result["features"] = feature_row(data, alert, hit, pictures, facts, per_person, result)
        results.append(result)
    return results


def decide_all(data: Data, on_error=None):
    """Decide every alert. An alert the engine cannot process goes to REVIEW:
    one bad record must not stop the run, and must never clear a hit."""
    out = []
    for alert in data.alerts:
        try:
            out.extend(decide_alert(data, alert))
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(alert, exc)
            for hit_id in data.alert_hit_ids(alert):
                out.append({
                    "hit_id": hit_id, "alert_id": alert.get("alert_id"), "decision": REVIEW,
                    "reason_code": "INSUFFICIENT_DATA", "reason_codes": ["INSUFFICIENT_DATA"],
                    "customer_record_id": None, "evidence": [],
                    "reason": "The system could not process this alert's records. Nothing was compared, "
                              "so a person must review the hit.",
                    "summary": f"The engine could not process this alert ({type(exc).__name__}).",
                    "trace": None, "blocker": "Engine error", "counterfactuals": [], "features": None,
                })
    return out
