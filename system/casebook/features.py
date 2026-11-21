"""Deterministic add-ons around a decision: what would settle a REVIEW, and a
feature row per hit for the advisory models.

Nothing here changes a decision. Counterfactuals re-run the policy on
hypothetical facts and report what it would say.
"""
from dataclasses import replace

from .customer import Fact, Picture
from .dates import PDate
from .hit import HitFacts
from .load import Data, as_list, text
from .policy import REVIEW, decide


def _outcome(data, pic, hit, screened_at):
    d = decide(data, pic, hit, screened_at)
    return {"decision": d.decision, "reason_code": d.reason_code}


def counterfactuals(data: Data, pictures, facts: HitFacts, per_person, screened_at, final_decision):
    """For a REVIEW hit: the single facts that would settle it, and what each would lead to.

    Each item is {fact, question, outcomes: [{if, decision, reason_code}]}.
    """
    if final_decision != REVIEW:
        return []
    out = []

    if len(pictures) > 1:
        out.append({
            "fact": "customer_record",
            "question": "Which of the records that fit the alert is the customer? "
                        "An account reference or login on the alert would settle it.",
            "outcomes": [{"if": f"the customer is {pic.primary}", "decision": d.decision,
                          "reason_code": d.reason_code} for pic, d in zip(pictures, per_person)],
        })
        return out

    pic: Picture = pictures[0]
    checks = {c.name: c for c in per_person[0].checks}
    what = "incorporation date" if pic.company else "date of birth"

    # The hit's date of birth is missing, or too vague to compare.
    if pic.dob and checks["dob"].outcome == "unknown":
        c: PDate = pic.dob.value
        same = replace(facts, dobs=[c], dob_origin="dobs field", dob_sources=[])
        far = replace(facts, dobs=[PDate(c.y - 10)], dob_origin="dobs field", dob_sources=[])
        vague = bool(facts.dobs)
        out.append({
            "fact": "hit_dob",
            "question": (f"Find the hit's exact {what}: the profile only narrows it to "
                         f"{', '.join(d.iso() for d in facts.dobs)}." if vague
                         else f"Find the hit's {what}: the profile, its notes and the search results give none."),
            "outcomes": [
                {"if": f"it is {c.iso()}, the customer's", **_outcome(data, pic, same, screened_at)},
                {"if": "it is more than a year away from the customer's", **_outcome(data, pic, far, screened_at)},
            ],
        })

    # The customer's date of birth is not known.
    if not pic.dob and facts.dobs:
        h = facts.dobs[0]
        same = replace(pic, dob=Fact(h, "hypothetical"))
        far = replace(pic, dob=Fact(PDate(h.y - 10), "hypothetical"))
        out.append({
            "fact": "customer_dob",
            "question": f"Verify the customer's {what}: no approved check or record gives one.",
            "outcomes": [
                {"if": f"it is {h.iso()}, as the hit lists", **_outcome(data, same, facts, screened_at)},
                {"if": "it is more than a year away from the hit's", **_outcome(data, far, facts, screened_at)},
            ],
        })

    # The customer's name cannot be used (placeholders), or is not verified.
    if checks["name"].outcome == "unknown" and facts.names:
        agree = replace(pic, verified_name=Fact(facts.names[0], "hypothetical"))
        differ = replace(pic, verified_name=Fact("Zzyqx Qvwzk", "hypothetical"))
        out.append({
            "fact": "customer_name",
            "question": "Verify the customer's legal name with an approved identity check: "
                        "the names on file cannot settle the comparison.",
            "outcomes": [
                {"if": f"the verified name agrees with {facts.names[0]}", **_outcome(data, agree, facts, screened_at)},
                {"if": "the verified name agrees with none of the hit's names", **_outcome(data, differ, facts, screened_at)},
            ],
        })

    # Name and date of birth agree but the countries differ: only a shared country confirms.
    if (checks["location"].outcome == "contradict" or checks["location"].blocks_confirm) and facts.countries:
        tied = replace(pic, countries=pic.countries + [Fact(facts.countries[0], "hypothetical")])
        out.append({
            "fact": "customer_country",
            "question": f"Ask whether the customer has ever lived in or held the nationality of "
                        f"{', '.join(facts.countries)}.",
            "outcomes": [
                {"if": f"they have a tie to {facts.countries[0]}", **_outcome(data, tied, facts, screened_at)},
                {"if": "they have none", "decision": REVIEW, "reason_code": "INSUFFICIENT_DATA"},
            ],
        })

    # A company with an agreeing name needs a shared country to confirm.
    if pic.company and checks["location"].outcome == "unknown" and checks["name"].outcome == "agree":
        cust_countries = [f.value for f in pic.countries]
        if cust_countries and not facts.countries:
            tied = replace(facts, countries=[cust_countries[0]])
            apart = replace(facts, countries=["Atlantis"])
            out.append({
                "fact": "hit_country",
                "question": "Find the country the listed company is registered or operates in.",
                "outcomes": [
                    {"if": f"it is {cust_countries[0]}", **_outcome(data, pic, tied, screened_at)},
                    {"if": "it is a country the customer has no tie to", **_outcome(data, pic, apart, screened_at)},
                ],
            })

    # Location is unknown for a person: a country on either side could still clear it.
    if not pic.company and checks["location"].outcome == "unknown" and not facts.countries \
            and checks["dob"].outcome != "agree" and pic.countries:
        apart = replace(facts, countries=["Atlantis"])
        out.append({
            "fact": "hit_country",
            "question": "Find a country tied to the hit: the profile lists none.",
            "outcomes": [
                {"if": "it is a country the customer has no tie to", **_outcome(data, pic, apart, screened_at)},
                {"if": "it is one of the customer's countries", "decision": REVIEW, "reason_code": "INSUFFICIENT_DATA"},
            ],
        })
    return out


def blocker(pictures, per_person, final_decision, reason_code):
    """A short label for what keeps a REVIEW hit open, for grouping the queue."""
    if final_decision != REVIEW:
        return None
    if reason_code == "AMBIGUOUS_CUSTOMER_RECORD":
        return "Several customers fit and disagree"
    checks = {c.name: c for c in per_person[0].checks}
    name, dob, loc = checks["name"].outcome, checks["dob"].outcome, checks["location"].outcome
    if name == "unknown":
        return "Customer name not usable or not verified"
    if name == "agree" and dob == "agree" and (loc == "contradict" or checks["location"].blocks_confirm):
        return "Name and birth date agree, countries differ"
    if dob == "unknown" and "neither agrees" in checks["dob"].detail:
        return "Birth dates one year apart or too vague"
    if dob == "unknown":
        return "No birth date to compare"
    if loc != "agree":
        return "No shared country to confirm"
    return "Not enough agreement"


def feature_row(data: Data, alert, hit, pictures, facts: HitFacts, per_person, result):
    """One flat row per hit, for the advisory models. Uses the first candidate's checks."""
    pic = pictures[0]
    checks = {c.name: c for c in per_person[0].checks}
    cust_countries = {f.value for f in pic.countries}
    cust_name = pic.verified_name.value if pic.verified_name else (
        pic.declared_names[0].value if pic.declared_names else text(alert.get("customer_name")))
    c_year = pic.dob.value.y if pic.dob else None
    gaps = [abs(c_year - d.y) for d in facts.dobs] if (c_year and facts.dobs) else []
    considered = facts.search_considered
    try:
        match_score = float(hit.get("match_score")) if hit.get("match_score") not in (None, "") else None
    except (TypeError, ValueError):
        match_score = None
    return {
        "hit_id": result["hit_id"],
        "alert_id": result["alert_id"],
        "decision": result["decision"],
        "reason_code": result["reason_code"],
        # outcomes of the policy checks: the fields a model must not simply copy
        "name_outcome": checks["name"].outcome,
        "dob_outcome": checks["dob"].outcome,
        "location_outcome": checks["location"].outcome,
        "time_outcome": checks["time"].outcome,
        "type_mismatch": "type" in checks,
        # soft signals
        "company": bool(pic.company),
        "n_candidates": len(pictures),
        "alert_hit_count": len(as_list(alert.get("hit_ids"))),
        "customer_name": cust_name,
        "screened_name": text(alert.get("customer_name")),
        "name_verified": pic.verified_name is not None,
        "hit_name": facts.names[0] if facts.names else "",
        "hit_alias_count": max(len(facts.names) - 1, 0),
        "match_score": match_score,
        "hit_category": text(hit.get("primary_category")) or (as_list(hit.get("categories")) or [""])[0],
        "hit_gender": text(hit.get("gender")),
        "hit_has_profile_notes": bool(hit.get("profile_notes")),
        "hit_dob_origin": facts.dob_origin,
        "hit_dob_count": len(facts.dobs),
        "customer_birth_year": c_year,
        "dob_verified": pic.dob_verified,
        "min_year_gap": min(gaps) if gaps else None,
        "n_customer_countries": len(cust_countries),
        "n_hit_countries": len(facts.countries),
        "n_shared_countries": len(cust_countries & set(facts.countries)),
        "n_search_results": len(considered),
        "n_search_about_hit": sum(1 for s in considered if s["about_hit"]),
        "has_dated_report": facts.earliest_report is not None,
    }
