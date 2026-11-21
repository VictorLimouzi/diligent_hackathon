"""Deterministic stand-ins for when no model answers (no key and no cached response).

They are built from the hit's own trace and counterfactuals and from real
record lookups through the same tools the model would use, so a demo shows
true facts with templated wording. Everything produced here is marked
`stub: true` by the caller.
"""
import re

from casebook.countries import countries_in_text
from casebook.customer import is_company
from casebook.dates import dob_agrees, dob_contradicts, max_age_at, parse_date
from casebook.names import NameRules, name_parts, names_agree

from .schemas import find_ids, hit_ids

OFFLINE = "No language model is connected: the lookups are real, the wording is a template."

_QUEUE_ADVICE = {
    "no birth date to compare": "the watchlist profile gives no usable date of birth, so a date from the "
                                "list provider or another reliable source would settle them",
    "several customers fit and disagree": "the alert does not identify one customer, so an account "
                                          "reference or login on the alert would settle them",
    "name and birth date agree, countries differ": "only the customer can say whether they have a tie to "
                                                   "the hit's country, so a request for information would settle them",
    "customer name not usable or not verified": "the customer's legal name is not verified, so an approved "
                                                "identity check would settle them",
    "birth dates one year apart or too vague": "the hit's date of birth is too vague, so an exact date "
                                               "would settle them",
}


def _join(items, word="and"):
    items = [str(i) for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _n(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def candidates(hit):
    return (hit.get("trace") or {}).get("candidates") or []


def hit_facts(hit):
    return (hit.get("trace") or {}).get("hit") or {}


def first_counterfactual(hit):
    cfs = hit.get("counterfactuals") or []
    return cfs[0] if cfs else None


def primaries(hit):
    """One record per person that fits the alert, in the engine's order."""
    out = []
    for cand in candidates(hit):
        cust = cand.get("customer") or {}
        rec = cust.get("primary") or next(iter(cust.get("records") or []), None)
        if rec and rec not in out:
            out.append(rec)
    return out


def hit_name(hit):
    names = hit_facts(hit).get("names") or []
    return names[0] if names else (hit.get("profile") or {}).get("name") or hit.get("hit_id")


def customer_name(alert, hit):
    cands = candidates(hit)
    if len(cands) == 1:
        cust = cands[0].get("customer") or {}
        declared = [d.get("value") for d in cust.get("declared_names") or [] if d.get("value")]
        return cust.get("verified_name") or (declared[0] if declared else None) or (alert or {}).get("customer_name")
    return (alert or {}).get("customer_name")


def settle_sentence(cf):
    """The counterfactual as one line: the question, then what each answer leads to."""
    outcomes = " ".join(f"If {o.get('if')}: {o.get('decision')} ({o.get('reason_code')})."
                        for o in cf.get("outcomes") or [])
    return f"{(cf.get('question') or '').rstrip()} {outcomes}".strip()


# ------------------------------------------------------------ investigator

def settling_facts(hit):
    """The facts the engine says would settle the hit, in its order."""
    return list(dict.fromkeys(cf.get("fact") for cf in hit.get("counterfactuals") or [] if cf.get("fact")))


def plan(hit):
    """The lookups worth making for a hit, chosen from the facts its counterfactuals name."""
    hid = hit.get("hit_id")
    people = primaries(hit)[:3]
    first = people[:1]
    calls = [("get_hit_profile", {"hit_id": hid})]
    calls += [("get_customer", {"record_id": c}) for c in people]
    wanted = []
    facts = settling_facts(hit)
    for fact in facts:
        if fact == "customer_record":
            wanted += [("get_identity_checks", {"record_id": c}) for c in people]
        elif fact in ("hit_dob", "hit_country"):
            wanted.append(("get_search_results", {"hit_id": hid}))
            if fact == "hit_dob":
                wanted += [("get_identity_checks", {"record_id": c}) for c in first]
        elif fact in ("customer_country", "customer_dob"):
            for c in first:
                wanted += [("get_identity_checks", {"record_id": c}), ("get_notes", {"record_id": c})]
        elif fact == "customer_name":
            wanted += [("get_identity_checks", {"record_id": c}) for c in first]
            ours = customer_name(None, hit)
            if ours and hit_name(hit):
                wanted.append(("compare_names", {"name_a": ours, "name_b": hit_name(hit)}))
    if not facts:
        wanted += [("get_identity_checks", {"record_id": c}) for c in first]
        wanted.append(("get_engine_trace", {"hit_id": hid}))
    for call in wanted:
        if call not in calls:
            calls.append(call)
    return calls


def _contents(results, tool):
    return [r.content for name, _, r in results if name == tool and r.ok]


def _finding(text_, ids, known):
    return {"text": text_, "cited_ids": [i for i in dict.fromkeys(ids) if i in known]}


def _check_findings(hit, known):
    cands = candidates(hit)
    out = []
    if len(cands) == 1:
        wanted = {"DIFFERENT_ENTITY": ("contradict",), "SAME_ENTITY": ("agree",)}.get(
            hit.get("decision"), ("contradict", "agree"))
        for outcome in wanted:
            for check in cands[0].get("checks") or []:
                if check.get("outcome") == outcome and check.get("detail"):
                    # A check with no listed source rests on the hit's profile and the customer's birth date.
                    ids = (check.get("sources") or find_ids(check["detail"])
                           or [hit.get("hit_id"), (cands[0].get("customer") or {}).get("dob_source")])
                    out.append(_finding(check["detail"], ids, known))
    else:
        for cand in cands[:4]:
            cust = cand.get("customer") or {}
            code = (cand.get("reason_codes") or [""])[0]
            out.append(_finding(
                f"If the customer is {_join(cust.get('records') or [], 'or')}: {cand.get('decision')} ({code}). "
                f"{cand.get('summary') or ''}".strip(),
                [*(cust.get("records") or []), cust.get("verified_check"), *(cand.get("evidence") or [])], known))
    return out


def _fact_findings(alert, hit, fact, results, known):
    facts, cands = hit_facts(hit), candidates(hit)
    cust = (cands[0].get("customer") or {}) if cands else {}
    hid = hit.get("hit_id")
    out, lead = [], None
    checks = [c for content in _contents(results, "get_identity_checks") for c in content["checks"]]
    approved = [c for c in checks if c["status"] == "approved"]
    notes = [n for content in _contents(results, "get_notes") for n in content["notes"]]
    search = (_contents(results, "get_search_results") or [None])[0]

    if fact == "customer_country":
        targets = facts.get("countries") or []
        have = list(dict.fromkeys(c.get("value") for c in cust.get("countries") or [] if c.get("value")))
        mentions = [n for n in notes if set(n["countries_mentioned"]) & set(targets)]
        if mentions:
            ids = [n["note_id"] for n in mentions]
            verb = "mentions" if len(ids) == 1 else "mention"
            out.append(_finding(
                f"{_join(ids)} {verb} {_join(targets, 'or')}, but not as a place the customer lived or worked, "
                "so the engine did not count it as a tie.", ids, known))
            lead = f"Read {_join(ids)} first: it may already answer the question."
        else:
            read = [*(cust.get("records") or []), *[c["check_id"] for c in approved], *[n["note_id"] for n in notes[:5]]]
            out.append(_finding(
                f"No record ties the customer to {_join(targets, 'or')}: the customer records, "
                f"{_n(len(approved), 'approved identity check')} and {_n(len(notes), 'note')} show only "
                f"{_join(have) or 'no country'}.", read, known))

    elif fact == "hit_dob":
        dobs = facts.get("dobs") or []
        if dobs:
            out.append(_finding(
                f"The profile narrows the hit's date of birth to {_join(dobs, 'or')} "
                f"({facts.get('dob_origin') or 'profile'}), which does not settle the comparison.",
                [hid, *(facts.get("dob_sources") or [])], known))
        else:
            out.append(_finding(f"The watchlist profile of {hit_name(hit)} lists no date of birth, and its "
                                "notes give no age or birth date.", [hid], known))
        if search is not None:
            ids = [r["result_id"] for r in search["results"]][:5]
            total, about = search["total"], search["about_hit"]
            if not total:
                out.append(_finding("There are no web search results for the hit's name.", [hid], known))
            elif not search["assessed_by_engine"]:
                out.append(_finding(f"{_n(total, 'web search result')} exist for the name; the engine reads "
                                    "them only when the profile gives no birth date.", ids, known))
            elif not about:
                out.append(_finding(
                    f"{_n(total, 'web search result')} for the name were read; none describes the same kind of "
                    f"listing (\"{search['expected_listing_phrase']}\") in a country tied to the hit, so none "
                    "gives the hit's date of birth.", ids, known))
            else:
                mine = [r["result_id"] for r in search["results"] if r["about_hit"]]
                used = [r["result_id"] for r in search["results"] if r["used_for_birth_date"]]
                tail = (f"{_join(used)} gave the birth years above" if used
                        else "none states a readable age at a date")
                out.append(_finding(f"{about} of {_n(total, 'web search result')} are about the hit "
                                    f"({_join(mine)}); {tail}.", mine, known))
        if cust.get("dob"):
            source = str(cust.get("dob_source") or "")
            how = ("from an approved identity check" if cust.get("dob_verified")
                   else "as screened on the alert, not verified" if not find_ids(source) else "declared, not verified")
            out.append(_finding(f"The customer's date of birth is {cust['dob']} ({source}, {how}).", [source], known))

    elif fact == "hit_country":
        out.append(_finding("The watchlist profile lists no country, nationality, citizenship, address or "
                            "place of birth for the hit.", [hid], known))
        if search is not None and search["total"]:
            seen = list(dict.fromkeys(c for r in search["results"] for c in countries_in_text(r["snippet"]["text"])))
            ids = [r["result_id"] for r in search["results"]][:5]
            about = search["about_hit"]
            tail = ("none is confirmed to be about the hit, so they do not tie the hit to a country"
                    if not about else f"{about} are about the hit")
            out.append(_finding(f"{_n(search['total'], 'web search result')} for the name mention "
                                f"{_join(seen[:6]) or 'no country'}; {tail}.", ids, known))
        elif search is not None:
            out.append(_finding("There are no web search results for the hit's name.", [hid], known))
        have = list(dict.fromkeys(c.get("value") for c in cust.get("countries") or [] if c.get("value")))
        if have:
            out.append(_finding(f"The customer is tied to {_join(have)}.",
                                [c.get("source") for c in cust.get("countries") or []], known))

    elif fact in ("customer_name", "customer_dob"):
        if approved:
            best = approved[0]
            got = ", ".join(p for p in (best["verified_name"], f"born {best['verified_dob']}" if best["verified_dob"] else "") if p)
            out.append(_finding(f"Approved identity check {best['check_id']} gives {got or 'no name or date of birth'}.",
                                [best["check_id"]], known))
        else:
            out.append(_finding("No approved identity check is on file for the customer, so their legal name "
                                "and date of birth are not verified.", cust.get("records") or [], known))
        for check in (cands[0].get("checks") or []) if len(cands) == 1 else []:
            if check.get("name") == ("name" if fact == "customer_name" else "dob") and check.get("outcome") == "unknown":
                out.append(_finding(check.get("detail") or "", check.get("sources") or cust.get("records") or [], known))
        for content in _contents(results, "compare_names"):
            verdict = "agree" if content["agree"] else "do not agree"
            caveat = ("a name with fewer than two real parts can neither confirm nor clear an individual"
                      if len(content["parts_a"]) < 2 else "that name is not a verified legal name")
            if cust.get("verified_name") != content["name_a"] or len(content["parts_a"]) < 2:
                out.append(_finding(f"The customer's name \"{content['name_a']}\" and the hit's name "
                                    f"\"{content['name_b']}\" {verdict} under the policy's name rule, but {caveat}.",
                                    [*(cust.get("records") or []), hid], known))

    elif fact == "customer_record":
        links = [k for k in ("account_ref", "login_handle", "email") if (alert or {}).get(k)]
        records = [r for c in cands for r in (c.get("customer") or {}).get("records") or []]
        if links:
            text_ = (f"The alert's {_join(l.replace('_', ' ') for l in links)} leads to more than one person, "
                     "and the screened details do not pick between them.")
        else:
            text_ = ("The alert carries no account reference, login or email, so only the screened name and "
                     "details are left to pick between the people who fit.")
        out.append(_finding(text_, records, known))
    return out, lead


def _unusable_checks(results, known):
    """Rejected and expired checks are worth a line: they exist, and they are not evidence."""
    checks = [c for content in _contents(results, "get_identity_checks") for c in content["checks"]]
    others = list({c["check_id"]: c for c in checks if c["status"] != "approved"}.values())
    if not others:
        return []
    listed = _join(f"{c['check_id']} ({c['status']})" for c in others[:4])
    verb = "is" if len(others) == 1 else "are"
    return [_finding(f"{_n(len(others), 'identity check')} on file {verb} not evidence: {listed}.",
                     [c["check_id"] for c in others[:4]], known)]


def next_action(hit, lead=None):
    decision, code = hit.get("decision"), hit.get("reason_code")
    cfs = hit.get("counterfactuals") or []
    if decision == "REVIEW":
        if cfs:
            base = " Or: ".join(settle_sentence(cf) for cf in cfs[:2])
        else:
            base = f"A person must review this hit: {hit.get('blocker') or hit.get('summary') or 'the evidence cannot settle it'}."
        return f"{lead} {base}" if lead else base
    if decision == "SAME_ENTITY":
        return f"Refer the alert to the compliance team: the engine confirmed this hit ({code})."
    return (f"No further action on this hit: the engine cleared it as a different entity ({code}). "
            "The alert clears only when every hit in it is cleared.")


_LEAK = re.compile(r"\b(sanction\w*|watch\s?list\w*|politically exposed|pep|adverse media|enforcement|"
                   r"screening|namesake|hit|alert)\b", re.I)


def rfi_leaks(draft, hit):
    """True when a drafted customer request would tip the customer off about the match."""
    if _LEAK.search(draft or ""):
        return True
    ours = set(name_parts(customer_name(None, hit) or ""))
    theirs = {p for n in hit_facts(hit).get("names") or [] for p in name_parts(n)} - ours
    words = set(name_parts(draft or ""))
    return bool({p for p in theirs if len(p) >= 4} & words)


def salutation(alert, hit, company=False):
    """How to address the customer: by a name on file that is a real name, never a placeholder."""
    if company:
        return "Dear Sir or Madam,"
    cands = candidates(hit)
    names = []
    if len(cands) == 1:
        cust = cands[0].get("customer") or {}
        names += [d.get("value") for d in cust.get("declared_names") or [] if str(d.get("source", "")).startswith("CUS-")]
        names.append(cust.get("verified_name"))
    names.append((alert or {}).get("customer_name"))
    for name in names:
        if name and len(name_parts(name)) >= 2:
            return f"Dear {name.title() if name.isupper() else name},"
    return "Dear customer,"


def rfi_draft(alert, hit):
    """A neutral request for information for a REVIEW hit; None when the engine decided the hit.

    It never mentions the list, the hit or the hit's details: the customer must
    not learn that they matched a watchlist.
    """
    if hit.get("decision") != "REVIEW":
        return None
    cf = first_counterfactual(hit)
    fact = cf.get("fact") if cf else None
    company = bool(is_company((alert or {}).get("entity_type")))
    greeting = salutation(alert, hit, company)
    if fact == "customer_country":
        ask = ("Please list the countries you have lived in, worked in or held the nationality of, with the "
               "approximate years for each.")
    elif fact in ("customer_name", "customer_dob"):
        ask = ("Please send us a copy of the company's official registration document showing its registered "
               "name, registration number and date of incorporation." if company else
               "Please send us a copy of a valid government-issued photo identity document (a passport or "
               "national identity card) showing your full legal name and date of birth.")
    elif fact == "customer_record":
        ask = ("Please confirm the account number or login you use with us, together with your full legal "
               "name, date of birth and country of residence, so that we can match this review to the right profile.")
    else:
        ask = ("Please confirm your full legal name, any other names you have used, your date and place of "
               "birth, and the countries you have lived in or held the nationality of.")
    return (f"{greeting}\n\nAs part of a routine review of your account, we need to confirm some of the details "
            f"we hold for you. {ask}\n\nYou can reply to this message or contact us through your usual support "
            "channel. Thank you for your help.\n\nKind regards,\nCompliance Operations")


def investigation(toolbox, alert, hit):
    """The whole offline investigation: {"thought", "steps", "result"}."""
    cfs = hit.get("counterfactuals") or []
    decision, code = hit.get("decision"), hit.get("reason_code")
    if decision == "REVIEW":
        asks = " Or: ".join(cf.get("question") or "" for cf in cfs[:2])
        thought = (f"The engine left this hit in REVIEW ({code}): {hit.get('blocker') or 'the evidence cannot settle it'}. "
                   + (f"What would settle it: {asks} Looking for that in the alert's own records. " if asks else "")
                   + OFFLINE)
    else:
        thought = f"The engine decided this hit: {decision} ({code}). Checking the records behind that decision. {OFFLINE}"
    results = [(tool, args, toolbox.call(tool, args)) for tool, args in plan(hit)]
    steps = [{"type": "step", "index": i, "tool": tool, "input": args, "summary": res.summary}
             for i, (tool, args, res) in enumerate(results)]
    return {"thought": thought, "steps": steps, "result": conclude(toolbox, alert, hit, results)}


def conclude(toolbox, alert, hit, results=None):
    """The offline InvestigationResult, from the trace and whatever lookups were made."""
    results = toolbox.calls if results is None else results
    known = hit_ids(hit) | toolbox.seen_ids
    findings, lead, seen = [], None, set()
    per_fact = [_fact_findings(alert, hit, fact, results, known) for fact in settling_facts(hit)]
    for f in [*_check_findings(hit, known), *[f for out, _ in per_fact for f in out], *_unusable_checks(results, known)]:
        if f["text"] and f["text"] not in seen:
            seen.add(f["text"])
            findings.append(f)
    lead = next((l for _, l in per_fact if l), None)
    if hit.get("customer_record_id") is None:
        findings.insert(0, _finding("No customer record was found for the alert, so the comparison rests on the "
                                    "details as screened.", [], known))
    for f in findings:
        # Every finding is about this hit; one with no other record behind it cites the hit itself.
        if not f["cited_ids"] and hit.get("hit_id") in known:
            f["cited_ids"] = [hit["hit_id"]]
    if not findings:
        findings = [_finding(hit.get("summary") or "", hit.get("evidence") or [], known)]
    review = hit.get("decision") == "REVIEW"
    return {
        "blocker": (hit.get("blocker") or "The evidence cannot settle this hit.") if review
                   else "None: the engine decided this hit.",
        "findings": findings,
        "next_action": next_action(hit, lead),
        "rfi_draft": rfi_draft(alert, hit),
        "cited_ids": list(dict.fromkeys(i for f in findings for i in f["cited_ids"])),
        "model": None, "cached": False, "stub": True,
    }


# -------------------------------------------------------------------- chat

_INTENTS = (
    ("get_engine_trace", r"\b(why|reason\w*|decision|decided?|engine|trace|block\w*|settle\w*|clear\w*|review|confirm\w*|policy|rule\w*|missing)\b"),
    ("get_hit_profile", r"\b(profile|listing|listed|allegation\w*|watchlist|hit|who)\b"),
    ("get_customer", r"\b(customer|record|records|address|nationalit\w*|residen\w*|countr\w*|lived?|lives|born|birth|dob|age)\b"),
    ("get_identity_checks", r"\b(kyc|identity|verif\w*|checks?|document\w*|passport|approved|rejected|expired)\b"),
    ("get_notes", r"\b(notes?|support|ticket\w*|call\w*|said|told)\b"),
    ("get_search_results", r"\b(search\w*|web|snippet\w*|results?|article\w*|news|media)\b"),
    ("compare_names", r"\b(names?|alias\w*|spell\w*|nickname\w*)\b"),
)
_DECIDE = re.compile(r"\b(clear|close|confirm|approve|dismiss|mark|override|change|set)\b", re.I)


def chat_plan(hit, question):
    hid = hit.get("hit_id")
    people = primaries(hit)[:1]
    calls = []
    for tool, pattern in _INTENTS:
        if not re.search(pattern, question, re.I):
            continue
        if tool in ("get_engine_trace", "get_hit_profile", "get_search_results"):
            calls.append((tool, {"hit_id": hid}))
        elif tool == "compare_names":
            ours = customer_name(None, hit)
            if ours and hit_name(hit):
                calls.append((tool, {"name_a": ours, "name_b": hit_name(hit)}))
        else:
            calls += [(tool, {"record_id": c}) for c in people[:2]]
    if not calls:
        calls = [("get_engine_trace", {"hit_id": hid})] + [("get_customer", {"record_id": c}) for c in people[:1]]
    calls = calls[:5]
    for cid in [i for i in find_ids(question) if i.startswith("CUS-")][:2]:
        # A record the analyst names is always looked up, so one outside the alert is visibly refused.
        if ("get_customer", {"record_id": cid}) not in calls:
            calls.append(("get_customer", {"record_id": cid}))
    return calls


_LABELS = {"get_customer": "Customer record {id}: ", "get_identity_checks": "Identity checks for {id}: ",
           "get_notes": "Notes for {id}: ", "get_search_results": "Web search: ", "get_hit_profile": "Hit {id}: ",
           "compare_names": "Names: "}


def chat_answer(hit, question, results):
    decision, code = hit.get("decision"), hit.get("reason_code")
    parts = ["Offline answer (no language model is connected), built from the case records."]
    if _DECIDE.search(question or ""):
        parts.append("I cannot clear, confirm or change a decision: the engine's decision stands, and an "
                     "analyst's review is recorded next to it.")
    parts.append(f"The engine's decision is {decision} ({code}). {hit.get('summary') or ''}".strip())
    for tool, args, res in results:
        if tool == "get_engine_trace":
            detail = res.summary.split(". ", 1)
            if res.ok and len(detail) > 1:
                parts.append(detail[1])
            continue
        label = _LABELS.get(tool, "").format(id=args.get("record_id") or args.get("hit_id") or "")
        parts.append(label + res.summary)
    cf = first_counterfactual(hit)
    if decision == "REVIEW" and cf:
        parts.append("What would settle it: " + settle_sentence(cf))
    return " ".join(p for p in parts if p)


# -------------------------------------------------------------------- note

def note_header(hit):
    return f"{hit.get('hit_id')}: {hit.get('decision')} ({hit.get('reason_code')})."


def note_body(hit):
    cands, facts = candidates(hit), hit_facts(hit)
    resolution = (hit.get("trace") or {}).get("resolution") or []
    lines = []
    if len(cands) == 1 and (cands[0].get("customer") or {}).get("records"):
        cust = cands[0].get("customer") or {}
        who = customer_name(None, hit) or "the customer"
        line = f"Customer: {who} ({cust.get('primary') or _join(cust.get('records') or [])})"
        if resolution:
            line += f", found by {resolution[0]}"
        line += (f". Latest approved identity check: {cust['verified_check']}." if cust.get("verified_check")
                 else ". No approved identity check is on file.")
        lines.append(line)
    elif len(cands) == 1:
        tried = f" ({resolution[0]})" if resolution else ""
        lines.append(f"Customer: no customer record was found for the alert{tried}; the comparison uses the "
                     "details as screened.")
    elif cands:
        people = _join("/".join((c.get("customer") or {}).get("records") or []) for c in cands)
        lines.append(f"Customer: {len(cands)} people fit the alert ({people}); the engine decided the hit for each.")
    else:
        lines.append("Customer: no customer record was found for the alert.")
    names = facts.get("names") or [hit_name(hit)]
    dobs = facts.get("dobs") or []
    born = f"born {_join(dobs, 'or')} ({facts.get('dob_origin')})" if dobs else "no date of birth given"
    where = _join(facts.get("countries") or []) or "none listed"
    also = f" (also {_join(names[1:3])})" if len(names) > 1 else ""
    lines.append(f"Hit: {names[0]}{also}; {born}; countries: {where}.")
    if len(cands) == 1:
        for outcome, label in (("agree", "Agrees"), ("contradict", "Contradicts"), ("unknown", "Unknown")):
            details = [c["detail"] for c in cands[0].get("checks") or [] if c.get("outcome") == outcome and c.get("detail")]
            if details:
                lines.append(f"{label}: {' '.join(details)}")
    else:
        for cand in cands[:6]:
            cust = cand.get("customer") or {}
            lines.append(f"If the customer is {_join(cust.get('records') or [], 'or')}: {cand.get('decision')} "
                         f"({(cand.get('reason_codes') or [''])[0]}).")
    cf = first_counterfactual(hit)
    if hit.get("decision") == "REVIEW":
        tail = f"Open point: {hit.get('blocker') or 'the evidence cannot settle this hit'}."
        if cf:
            tail += " Next step: " + settle_sentence(cf)
        lines.append(tail)
    return "\n".join(lines)


def engine_note(hit):
    """The last resort: the engine's own summary."""
    return f"{note_header(hit)} {hit.get('summary') or ''}".strip()


# ------------------------------------------------------------------- queue

def queue_text(groups):
    groups = sorted((g for g in groups or [] if g.get("count")), key=lambda g: -g["count"])
    total = sum(g["count"] for g in groups)
    if not total:
        return "No hits are waiting for review."
    top = groups[0]
    share = round(100 * top["count"] / total)
    advice = _QUEUE_ADVICE.get(str(top["blocker"]).strip().lower())
    text_ = (f"{_n(total, 'hit')} {'is' if total == 1 else 'are'} waiting for review. The largest group is "
             f"\"{top['blocker']}\" ({_n(top['count'], 'hit')}, {share}% of the queue)")
    text_ += f": {advice}." if advice else "."
    rest = groups[1:]
    if rest:
        listed = "; ".join(f"{g['count']} {str(g['blocker'])[:1].lower()}{str(g['blocker'])[1:]}" for g in rest[:4])
        text_ += f" The rest: {listed}."
    return text_


# ---------------------------------------------------------- second opinion

_RULES = NameRules()


def _parts(name, company):
    return name_parts(name, company, _RULES)


def _opinion_for(facts, cust):
    """A simplified reading of the policy for one person. Not the engine: no name-variant
    list, no partial handling beyond what the date helpers give."""
    hit = facts.get("hit") or {}
    alert = facts.get("alert") or {}
    c_company, h_company = is_company(cust.get("entity_type") or alert.get("entity_type")), is_company(hit.get("entity_type"))
    if c_company is not None and h_company is not None and c_company != h_company:
        return "DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH", "one is a person and the other a company"
    company = bool(h_company)
    death, screened = parse_date(hit.get("death")), parse_date(alert.get("screened_at"))
    if death and screened and death.latest() < screened.earliest():
        return "DIFFERENT_ENTITY", "DECEASED", f"the profile gives a date of death ({hit.get('death')}) before screening"
    dob = parse_date(cust.get("dob"))
    report = parse_date(hit.get("earliest_report"))
    if dob and report and not company and max_age_at(dob, report) < 16:
        return "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", f"the customer was under 16 at the earliest dated report ({hit.get('earliest_report')})"
    if dob and report and company and dob.earliest() > report.latest():
        return "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", "the company was incorporated after the earliest dated report"
    hit_dobs = [d for d in (parse_date(x) for x in hit.get("dobs") or []) if d]
    dob_agree = bool(dob and any(dob_agrees(dob, d) for d in hit_dobs))
    if dob and hit_dobs and all(dob_contradicts(dob, d) for d in hit_dobs):
        return "DIFFERENT_ENTITY", "DOB_MISMATCH", f"the customer's date of birth {cust.get('dob')} contradicts every date the hit gives"
    verified = cust.get("verified_name")
    ours = [verified] if verified else [d.get("value") for d in cust.get("declared_names") or []]
    usable = [n for n in ours if n and (company or len(_parts(n, company)) >= 2)]
    hit_names = [n for n in hit.get("names") or [] if n]
    name_agree = any(names_agree(_parts(a, company), _parts(b, company), _RULES) for a in usable for b in hit_names)
    if verified and usable and hit_names and not name_agree:
        return "DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED", f"the verified name {verified} agrees with none of the hit's names"
    c_countries = {c.get("value") for c in cust.get("countries") or [] if c.get("value")}
    h_countries = set(hit.get("countries") or [])
    loc_agree = bool(c_countries & h_countries)
    loc_contra = bool(c_countries and h_countries and not loc_agree)
    if loc_contra and not (name_agree and dob_agree):
        return "DIFFERENT_ENTITY", "LOCATION_MISMATCH", "every country tied to the customer differs from every country tied to the hit"
    if company and name_agree and loc_agree:
        return "SAME_ENTITY", "NAME_LOCATION_MATCH", "name and country agree"
    if not company and name_agree and dob_agree and not loc_contra:
        code = "NAME_DOB_LOCATION_MATCH" if loc_agree else "NAME_DOB_MATCH"
        return "SAME_ENTITY", code, "name and date of birth agree and nothing contradicts them"
    missing = [label for label, ok in (("name", name_agree), ("date of birth", dob_agree)) if not ok]
    if missing:
        return "REVIEW", "INSUFFICIENT_DATA", f"nothing contradicts the hit and there is no agreement on {_join(missing)}"
    return "REVIEW", "INSUFFICIENT_DATA", "name and date of birth agree but the countries differ"


def second_opinion(facts):
    """An offline stand-in opinion from the gathered facts. Deliberately crude, and labelled as such."""
    people = facts.get("customers") or []
    if not people:
        decision, code, why = "REVIEW", "INSUFFICIENT_DATA", "no customer record was found for the alert"
    else:
        per = [_opinion_for(facts, c) for c in people]
        if len({p[0] for p in per}) > 1:
            decision, code = "REVIEW", "AMBIGUOUS_CUSTOMER_RECORD"
            why = f"{len(per)} people fit the alert and lead to different decisions"
        else:
            decision, code, why = per[0]
    return {"decision": decision, "reason_code": code,
            "rationale": f"Offline stand-in, not model output: a simplified reading of the gathered facts gives "
                         f"{decision} because {why}."}
