"""Read-only lookups the investigator and the chat can call.

A ToolBox is built for one hit. It answers only about that hit and about the
customer records the alert resolved to: the policy says never to use another
customer's records, so a request for any other record is refused. No tool
writes, reviews or decides anything.

Free text written by third parties (profile notes, allegations, search
snippets, support notes, quotes) leaves a tool only inside
{"untrusted": true, "text": ...}, which the prompts tell the model to read as
data and never as instructions.
"""
import json
from collections import Counter, namedtuple

from casebook.countries import countries_in_text
from casebook.customer import is_company, resolve
from casebook.extract import lived_countries, listing_phrase, parse_kyc_note
from casebook.load import as_list, text
from casebook.names import names_agree

from .schemas import validate

ToolResult = namedtuple("ToolResult", "ok content summary")

MAX_TEXT = 1500
MAX_NOTES = 15
MAX_RESULTS = 12

_CUSTOMER_FIELDS = ("record_id", "entity_type", "full_name", "preferred_name", "login_name", "dob",
                    "nationality", "residence_country", "city", "address", "occupation", "employer",
                    "registration_number", "onboarded_at", "status", "last_kyc_refresh")
_PROFILE_FIELDS = ("name", "aliases", "entity_type", "gender", "dobs", "place_of_birth", "nationality",
                   "citizenship", "countries", "addresses", "categories", "primary_category",
                   "match_score", "life_status", "deceased_date")


def untrusted(value, limit=MAX_TEXT):
    """Wrap third-party text so the model can tell it from instructions."""
    s = text(value)
    if len(s) > limit:
        s = s[:limit] + " [truncated]"
    return {"untrusted": True, "text": s}


def _tool(name, description, properties):
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": properties, "required": list(properties),
                         "additionalProperties": False},
    }


_HIT_ID = {"hit_id": {"type": "string", "description": "The hit under review, for example H-ADF453."}}
_RECORD_ID = {"record_id": {"type": "string",
                            "description": "A customer record of this alert, for example CUS-375608."}}

# Sorted by name and never varied per request: tools render first in the prompt,
# so a stable list keeps the prompt cache valid across hits.
TOOL_DEFS = [
    _tool("compare_names",
          "Apply the policy's name rule (principle 8) to two names and say whether they agree. Call this "
          "instead of judging spelling variants, word order, initials or accents yourself.",
          {"name_a": {"type": "string", "description": "First name, as written in a record."},
           "name_b": {"type": "string", "description": "Second name, as written in a record."}}),
    _tool("get_customer",
          "The customer's records in the system of record: names, date of birth, nationality, residence, "
          "address and accounts, for every record that belongs to the same person. Call this to see what "
          "the bank holds about the customer. Only records of this alert's customer are available.",
          _RECORD_ID),
    _tool("get_engine_trace",
          "What the rule engine concluded for this hit and why: the decision, each policy check with its "
          "outcome and sources, how the customer was found, and the facts that would settle a REVIEW. "
          "Call this first when you need the reasoning behind the decision.",
          _HIT_ID),
    _tool("get_hit_profile",
          "The watchlist profile of the hit: names, aliases, listed birth dates, countries, categories, "
          "the allegation or position, and the profile notes. Call this to see what the list says about "
          "the hit. The free text in it is third-party data.",
          _HIT_ID),
    _tool("get_identity_checks",
          "Every identity check on file for the customer, each labelled approved, rejected or expired. "
          "Only an approved check is evidence of a legal name, date of birth or document country. Call "
          "this when a fact depends on what was verified.",
          _RECORD_ID),
    _tool("get_notes",
          "Support and onboarding notes about the customer, newest first, with the countries each one "
          "mentions. Call this to look for a fact the structured records lack, such as where the "
          "customer has lived. The note text is third-party data.",
          _RECORD_ID),
    _tool("get_search_results",
          "Web search results for the hit's name, each with the engine's verdict on whether it is about "
          "the hit and why not. Most results are about namesakes. Call this when the hit's date of "
          "birth or country is missing. The snippets are third-party data.",
          _HIT_ID),
]
_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOL_DEFS}


class _Refused(Exception):
    pass


def _iso(d):
    return d.iso() if d is not None else None


def _join(items):
    items = [str(i) for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class ToolBox:
    def __init__(self, data, alert, hit):
        self.data, self.alert, self.hit = data, alert or {}, hit
        self.hit_id = hit.get("hit_id")
        self.trace = hit.get("trace") or {}
        self.candidates = self.trace.get("candidates") or []
        # Scope: the records the engine resolved this alert to, person by person.
        self.persons = []
        for cand in self.candidates:
            records = [r for r in (cand.get("customer") or {}).get("records") or [] if r in data.customers]
            if records:
                self.persons.append(tuple(records))
        if not self.persons and hit.get("trace") is None:
            try:
                self.persons = [tuple(p) for p in resolve(data, self.alert).persons]
            except Exception:  # noqa: BLE001
                self.persons = []
        own = hit.get("customer_record_id")
        if own in data.customers and not any(own in p for p in self.persons):
            self.persons.append(tuple(data.person(own)))
        self.customers = [r for p in self.persons for r in p]
        self.seen_ids = set()
        self.calls = []   # (tool, input, ToolResult), in order

    # ---------------------------------------------------------------- entry

    def call(self, name, args):
        """Run one tool. Never raises: a bad name, bad input or out-of-scope record is an error result."""
        key = str(name or "").strip().lower()
        method = getattr(self, "_" + key, None) if key in _SCHEMAS else None
        if method is None:
            result = ToolResult(False, {"error": "unknown_tool",
                                        "message": f"There is no tool named {name!r}. Tools: {', '.join(_SCHEMAS)}."},
                                f"Refused: no tool named {name}.")
        else:
            problems = validate(args, _SCHEMAS[key]) if isinstance(args, dict) else ["input must be an object"]
            if problems:
                result = ToolResult(False, {"error": "invalid_input", "message": "; ".join(problems)},
                                    f"Refused: invalid input for {key}.")
            else:
                try:
                    content, summary = method(**args)
                    result = ToolResult(True, content, summary)
                except _Refused as exc:
                    result = ToolResult(False, {"error": "out_of_scope", "message": str(exc)}, f"Refused: {exc}")
                except Exception as exc:  # noqa: BLE001
                    result = ToolResult(False, {"error": "lookup_failed", "message": type(exc).__name__},
                                        f"The lookup failed ({type(exc).__name__}).")
        self.calls.append((key, args if isinstance(args, dict) else {}, result))
        return result

    @staticmethod
    def as_text(result):
        return json.dumps(result.content, ensure_ascii=False)

    # ---------------------------------------------------------------- scope

    def _own_hit(self, hit_id):
        if hit_id != self.hit_id:
            raise _Refused(f"{hit_id} is not the hit under review ({self.hit_id}).")
        self.seen_ids.add(self.hit_id)

    def _person(self, record_id):
        for person in self.persons:
            if record_id in person:
                self.seen_ids.update(person)
                return person
        own = _join(self.customers) or "none was found"
        raise _Refused(f"{record_id} is not one of this alert's customer records ({own}). "
                       "The policy forbids using another customer's records.")

    def _candidate(self, person):
        for cand in self.candidates:
            if set((cand.get("customer") or {}).get("records") or []) & set(person):
                return cand
        return None

    # ---------------------------------------------------------------- tools

    def _get_hit_profile(self, hit_id):
        self._own_hit(hit_id)
        profile = self.hit.get("profile") or {}
        out = {k: profile[k] for k in _PROFILE_FIELDS if profile.get(k)}
        if profile.get("position_or_allegation"):
            out["position_or_allegation"] = untrusted(profile["position_or_allegation"], 400)
        facts = self.trace.get("hit") or {}
        content = {
            "hit_id": hit_id,
            "profile": out,
            "profile_notes": untrusted(self.hit.get("profile_notes")),
            "engine_reading": {"names": facts.get("names") or [], "birth_dates": facts.get("dobs") or [],
                               "birth_date_origin": facts.get("dob_origin"),
                               "countries": facts.get("countries") or [],
                               "earliest_dated_report": facts.get("earliest_report"),
                               "date_of_death": facts.get("death")},
        }
        name = profile.get("name") or _join((facts.get("names") or [])[:1]) or hit_id
        category = profile.get("primary_category") or _join(as_list(profile.get("categories"))[:1])
        countries = facts.get("countries") or as_list(profile.get("countries"))
        dobs = facts.get("dobs") or []
        born = "born " + " or ".join(dobs) if dobs else "no date of birth listed"
        if dobs and facts.get("dob_origin") not in (None, "dobs field"):
            born += f" (from {facts['dob_origin']})"
        where = _join(countries) if countries else "no country listed"
        return content, ", ".join(p for p in (name, category, where, born) if p) + "."

    def _get_customer(self, record_id):
        person = self._person(record_id)
        records = []
        for cid in person:
            row = self.data.customers[cid]
            rec = {k: text(row.get(k)) for k in _CUSTOMER_FIELDS if row.get(k) not in (None, "")}
            rec["accounts"] = [{k: text(a.get(k)) for k in ("account_id", "login_handle", "opened_at", "status")
                                if a.get(k)} for a in self.data.acc_by_cust.get(cid, [])]
            records.append(rec)
        content = {"person_records": list(person), "records": records}
        cand = self._candidate(person)
        if cand:
            cust = cand["customer"]
            content["engine_picture"] = {
                "verified_name": cust.get("verified_name"), "verified_check": cust.get("verified_check"),
                "date_of_birth": cust.get("dob"), "date_of_birth_source": cust.get("dob_source"),
                "date_of_birth_verified": cust.get("dob_verified"), "countries": cust.get("countries") or []}
            for key in ("verified_check", "dob_source"):
                if cust.get(key):
                    self.seen_ids.add(cust[key])
        row = self.data.customers[record_id]
        parts = [text(row.get("full_name")) or record_id]
        if row.get("dob"):
            parts.append(f"born {text(row['dob'])}")
        res, nat = text(row.get("residence_country")), text(row.get("nationality"))
        if res and res == nat:
            parts.append(f"residence and nationality {res}")
        else:
            parts += [f"residence {res}" if res else "", f"nationality {nat}" if nat else ""]
        summary = ", ".join(p for p in parts if p) + "."
        others = [c for c in person if c != record_id]
        if others:
            summary += f" Same person as {_join(others)}."
        return content, summary

    def _get_identity_checks(self, record_id):
        person = self._person(record_id)
        cand = self._candidate(person)
        used = (cand or {}).get("customer", {}).get("verified_check")
        checks, seen = [], set()
        for cid in person:
            for onb in self.data.onb_by_cust.get(cid, []):
                for k in self.data.kyc_by_app.get(onb.get("kyc_applicant_id"), []):
                    if k.get("check_id") in seen:
                        continue
                    seen.add(k.get("check_id"))
                    status = text(k.get("result")).lower() or "unknown"
                    checks.append({
                        "check_id": k.get("check_id"), "source": "KYC provider", "status": status,
                        "is_evidence": status == "approved", "customer_record": cid,
                        "provider": text(k.get("provider")) or None,
                        "verified_name": text(k.get("verified_name")) or None,
                        "verified_dob": text(k.get("verified_dob")) or None,
                        "document_type": text(k.get("document_type")) or None,
                        "document_country": text(k.get("document_country")) or None,
                        "checked_at": text(k.get("checked_at")) or None})
            email = text(self.data.customers[cid].get("email")).lower()
            for n in self.data.notes_by_email.get(email, []) if email else []:
                if text(n.get("channel")).lower() != "kyc" or n.get("note_id") in seen:
                    continue
                r = parse_kyc_note(text(n.get("text")), n.get("created_at"))
                if not r:
                    continue
                seen.add(n.get("note_id"))
                status = "approved" if r["approved"] else "not approved"
                checks.append({
                    "check_id": n.get("note_id"), "source": "onboarding note", "status": status,
                    "is_evidence": bool(r["approved"] and (r["name"] or r["dob"])), "customer_record": cid,
                    "provider": None, "verified_name": r["name"], "verified_dob": _iso(r["dob"]),
                    "document_type": r["document_type"], "document_country": _join(r["countries"]) or None,
                    "checked_at": _iso(r["checked_at"]) or text(n.get("created_at")) or None})
        checks.sort(key=lambda c: (c["checked_at"] or "", c["check_id"] or ""), reverse=True)
        for c in checks:
            c["used_by_engine"] = c["check_id"] == used
            self.seen_ids.add(c["check_id"])
        counts = Counter(c["status"] for c in checks)
        content = {"checks": checks, "counts": dict(counts), "latest_approved_used_by_engine": used}
        if not checks:
            return content, "No identity check on file."
        bits = []
        approved = [c for c in checks if c["status"] == "approved"]
        if approved:
            best = next((c for c in approved if c["used_by_engine"]), approved[0])
            detail = ", ".join(p for p in (best["check_id"], best["checked_at"], best["verified_name"],
                                           f"born {best['verified_dob']}" if best["verified_dob"] else "",
                                           best["document_country"]) if p)
            bits.append(f"{len(approved)} approved ({detail})")
        for status, n in sorted(counts.items()):
            if status != "approved":
                ids = _join(c["check_id"] for c in checks if c["status"] == status)
                bits.append(f"{n} {status} ({ids}), not evidence")
        plural = "check" if len(checks) == 1 else "checks"
        return content, f"{len(checks)} identity {plural}: {'; '.join(bits)}."

    def _get_notes(self, record_id):
        person = self._person(record_id)
        notes, seen = [], set()
        for cid in person:
            email = text(self.data.customers[cid].get("email")).lower()
            for n in self.data.notes_by_email.get(email, []) if email else []:
                if n.get("note_id") in seen:
                    continue
                seen.add(n.get("note_id"))
                body = text(n.get("text"))
                notes.append({"note_id": n.get("note_id"), "created_at": text(n.get("created_at")) or None,
                              "channel": text(n.get("channel")).lower() or None, "customer_record": cid,
                              "countries_mentioned": countries_in_text(body),
                              "countries_lived_in": lived_countries(body),
                              "text": untrusted(body, 600)})
        notes.sort(key=lambda n: (n["created_at"] or "", n["note_id"] or ""), reverse=True)
        total = len(notes)
        notes = notes[:MAX_NOTES]
        for n in notes:
            self.seen_ids.add(n["note_id"])
        content = {"total": total, "notes": notes}
        if not notes:
            return content, "No notes on file."
        channels = Counter(n["channel"] or "other" for n in notes)
        mentioned = list(dict.fromkeys(c for n in notes for c in n["countries_mentioned"]))
        kinds = ", ".join(f"{n} {ch}" for ch, n in sorted(channels.items()))
        tail = f"countries mentioned: {_join(mentioned)}" if mentioned else "none mentions a country"
        plural = "note" if total == 1 else "notes"
        return content, f"{total} {plural} ({kinds}); {tail}."

    def _get_search_results(self, hit_id):
        self._own_hit(hit_id)
        facts = self.trace.get("hit") or {}
        profile = self.hit.get("profile") or {}
        phrase = listing_phrase(profile.get("primary_category"), as_list(profile.get("categories")))
        used = set(facts.get("dob_sources") or [])
        considered = facts.get("search_considered") or []
        assessed = bool(considered)
        if assessed:
            rows = [{"result_id": s.get("result_id"), "about_hit": bool(s.get("about_hit")),
                     "why_not": s.get("why_not"), "used_for_birth_date": s.get("result_id") in used,
                     "snippet": untrusted(s.get("snippet"), 400)} for s in considered]
        else:
            # The engine reads search results only when the profile gives no birth date.
            rows = [{"result_id": s.get("result_id"), "about_hit": None,
                     "why_not": "not assessed by the engine", "used_for_birth_date": False,
                     "snippet": untrusted(s.get("snippet"), 400)}
                    for s in self.data.search_results(profile.get("name"))]
        rows.sort(key=lambda r: not r["about_hit"])
        total = len(rows)
        about = [r for r in rows if r["about_hit"]]
        reasons = Counter(r["why_not"] for r in rows if r["about_hit"] is False and r["why_not"])
        rows = rows[:MAX_RESULTS]
        for r in rows:
            self.seen_ids.add(r["result_id"])
        content = {"expected_listing_phrase": phrase, "hit_countries": facts.get("countries") or [],
                   "total": total, "about_hit": len(about), "assessed_by_engine": assessed, "results": rows}
        plural = "result" if total == 1 else "results"
        if not total:
            return content, "No web search results for the hit's name."
        if not assessed:
            return content, (f"{total} web search {plural} for the name; the engine did not need them "
                             "for this hit.")
        if about:
            usable = [r["result_id"] for r in about if r["used_for_birth_date"]]
            tail = (f"used for the birth date: {_join(usable)}" if usable
                    else "none gives a readable age at a date")
            return content, f"{total} web search {plural}; {len(about)} about the hit ({_join(r['result_id'] for r in about)}); {tail}."
        why = "; ".join(f"{n} {reason}" for reason, n in reasons.most_common(3))
        return content, f"{total} web search {plural}; none is about the hit ({why})."

    def _compare_names(self, name_a, name_b):
        company = bool(is_company((self.hit.get("profile") or {}).get("entity_type")))
        a, b = name_a[:200], name_b[:200]
        parts_a, parts_b = self.data.parts(a, company), self.data.parts(b, company)
        agree = names_agree(parts_a, parts_b, self.data.rules)
        content = {"name_a": a, "name_b": b, "parts_a": list(parts_a), "parts_b": list(parts_b), "agree": agree,
                   "rule": "Names agree when at least two parts match, or every part of the shorter name matches."}
        verdict = "agree" if agree else "do not agree"
        summary = f"\"{a}\" and \"{b}\" {verdict} under the policy's name rule."
        if not company and min(len(parts_a), len(parts_b)) < 2:
            content["caveat"] = ("One name has fewer than two real parts. A customer name like that can neither "
                                 "confirm nor clear an individual; a hit's single-part alias can still agree.")
            summary += " One of them has fewer than two real parts, which limits what it can settle."
        return content, summary

    def _get_engine_trace(self, hit_id):
        self._own_hit(hit_id)
        hit, facts = self.hit, self.trace.get("hit") or {}
        candidates = []
        for cand in self.candidates:
            cust = cand.get("customer") or {}
            self.seen_ids.update(i for i in [*(cust.get("records") or []), cust.get("verified_check")] if i)
            candidates.append({
                "records": cust.get("records") or [], "verified_check": cust.get("verified_check"),
                "decision_if_this_is_the_customer": cand.get("decision"),
                "reason_codes": cand.get("reason_codes") or [],
                "checks": [{"name": c.get("name"), "outcome": c.get("outcome"), "detail": c.get("detail"),
                            "sources": c.get("sources") or []} for c in cand.get("checks") or []]})
        content = {
            "hit_id": hit_id, "decision": hit.get("decision"), "reason_code": hit.get("reason_code"),
            "reason_codes": hit.get("reason_codes") or [], "summary": hit.get("summary"),
            "blocker": hit.get("blocker"), "customer_record_id": hit.get("customer_record_id"),
            "how_the_customer_was_found": self.trace.get("resolution") or [],
            "candidates": candidates,
            "hit_facts": {"names": facts.get("names") or [], "birth_dates": facts.get("dobs") or [],
                          "birth_date_origin": facts.get("dob_origin"),
                          "birth_date_quote": untrusted(facts.get("dob_quote"), 300) if facts.get("dob_quote") else None,
                          "birth_date_sources": facts.get("dob_sources") or [],
                          "countries": facts.get("countries") or [],
                          "earliest_dated_report": facts.get("earliest_report"),
                          "earliest_report_line": (untrusted(facts.get("earliest_report_line"), 300)
                                                   if facts.get("earliest_report_line") else None),
                          "date_of_death": facts.get("death")},
            "would_settle_it": hit.get("counterfactuals") or [],
        }
        summary = f"Engine: {hit.get('decision')} ({hit.get('reason_code')})."
        if len(self.candidates) == 1:
            by = {}
            for c in self.candidates[0].get("checks") or []:
                by.setdefault(c.get("outcome"), []).append(c.get("name"))
            labels = (("agree", "Agrees"), ("contradict", "Contradicts"), ("unknown", "Unknown"))
            summary += "".join(f" {label}: {', '.join(by[o])}." for o, label in labels if by.get(o))
        elif self.candidates:
            summary += f" {len(self.candidates)} people fit the alert."
        else:
            summary += " No customer record was found."
        return content, summary
