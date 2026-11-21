"""Who the customer is: resolve the alert to bank records, then build one picture per person."""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .countries import country, is_named_place
from .dates import PDate, parse_date, same_in_given_parts
from .extract import note_countries, parse_kyc_note
from .load import Data, text
from .names import names_agree


def _plain(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.casefold()).strip()


def is_company(entity_type):
    """True for a company, False for a person, None when the value says neither.

    A type that is not recognised (a vessel, "unknown") cannot clear a hit as a type mismatch.
    """
    t = text(entity_type).lower()
    if not t:
        return None
    if t.startswith(("indiv", "person", "natural", "human")):
        return False
    if t.startswith(("compan", "organi", "corporat", "business", "legal", "entity", "firm", "institution")):
        return True
    return None


# ------------------------------------------------------------- resolution

@dataclass
class Resolution:
    persons: List[Tuple[str, ...]]   # each person is the tuple of their record ids
    primary: dict                    # person -> the record the alert led to
    path: List[str] = field(default_factory=list)


def fits(data: Data, cid, alert, need_name=True):
    """Does a record fit what was screened (policy 2)?"""
    c = data.customers[cid]
    company = bool(is_company(alert.get("entity_type") or c.get("entity_type")))
    if need_name:
        screened = data.parts(text(alert.get("customer_name")), company)
        ok = False
        for key in ("full_name", "preferred_name", "login_name"):
            value = text(c.get(key))
            if key == "login_name":
                value = re.sub(r"\d+", " ", value)   # logins carry digits: "markusm5942"
            if value and names_agree(screened, data.parts(value, company), data.rules):
                ok = True
                break
        if not ok:
            return False
    a_dob = parse_date(alert.get("dob"))
    c_dob = data.cust_dob.get(cid)
    if a_dob and c_dob and not same_in_given_parts(a_dob, c_dob):
        return False
    a_country = country(alert.get("country"))
    if a_country:
        tied = {country(c.get("residence_country")), country(c.get("nationality"))}
        if a_country not in tied:
            return False
    if alert.get("city"):
        city = _plain(alert["city"])
        if city and city not in _plain(c.get("city")) and city not in _plain(c.get("address")):
            return False
    return True


def _search(data: Data, alert):
    """No link: find the records that fit what was screened."""
    a_dob = parse_date(alert.get("dob"))
    if a_dob:
        pool = list(data.cust_by_year.get(a_dob.y, [])) + list(data.cust_by_year.get(None, []))
    else:
        pool = list(data.customers)
    a_type = is_company(alert.get("entity_type"))
    out = []
    for cid in pool:
        c_type = is_company(data.customers[cid].get("entity_type"))
        if a_type is not None and c_type is not None and a_type != c_type:
            continue
        if fits(data, cid, alert):
            out.append(cid)
    return out


def resolve(data: Data, alert) -> Resolution:
    path = []
    linked = []
    has_link = False
    if alert.get("account_ref"):
        has_link = True
        ref = text(alert["account_ref"])
        ids = [a["customer_id"] for a in data.acc_by_id.get(ref, []) if a.get("customer_id") in data.customers]
        linked += ids
        path.append(f"account_ref {ref} -> accounts -> {', '.join(dict.fromkeys(ids)) or 'no customer'}")
    if alert.get("login_handle"):
        has_link = True
        h = text(alert["login_handle"])
        accs = data.acc_by_login.get(h.lower(), [])
        ids = [a["customer_id"] for a in accs if a.get("customer_id") in data.customers]
        linked += ids
        path.append(f"login_handle {h} -> accounts -> {', '.join(dict.fromkeys(ids)) or 'no customer'}")
    if alert.get("email"):
        has_link = True
        e = text(alert["email"])
        ids = list(data.cust_by_email.get(e.lower(), []))
        linked += ids
        path.append(f"email {e} -> customers -> {', '.join(dict.fromkeys(ids)) or 'no customer'}")
    linked = list(dict.fromkeys(linked))

    candidates = linked
    if len({data.person(c) for c in linked}) > 1:
        # A link can lead to more than one person; the screened details pick between them.
        picked = [c for c in linked if fits(data, c, alert)]
        if not picked:
            picked = [c for c in linked if fits(data, c, alert, need_name=False)
                      and (alert.get("dob") or alert.get("country") or alert.get("city"))]
        if picked:
            path.append(f"screened details fit {', '.join(picked)}")
            candidates = picked
        else:
            path.append("screened details do not pick between them")
    elif not linked:
        candidates = _search(data, alert)
        what = "name, date of birth, country and city" if not has_link else "link led nowhere; name and screened details"
        path.append(f"{what} -> customers -> {', '.join(candidates) or 'no record fits'}")

    persons, primary = [], {}
    for cid in candidates:
        p = data.person(cid)
        if p not in primary:
            primary[p] = cid
            persons.append(p)
    for p in persons:
        if len(p) > 1:
            path.append(f"same phone and date of birth, or same document number -> one person: {', '.join(p)}")
    if len(persons) > 1:
        path.append(f"{len(persons)} different people fit; each is decided separately")
    return Resolution(persons, primary, path)


# ---------------------------------------------------------------- picture

@dataclass
class Fact:
    value: object
    source: str   # record id the fact comes from


@dataclass
class Picture:
    records: Tuple[str, ...]
    primary: Optional[str]
    company: Optional[bool]
    verified: Optional[dict]          # the most recent approved identity check
    verified_name: Optional[Fact]
    declared_names: List[Fact]
    dob: Optional[Fact]
    dob_verified: bool
    countries: List[Fact]
    path: List[str] = field(default_factory=list)
    countries_unrecognised: List[str] = field(default_factory=list)
    countries_mentioned: List[Fact] = field(default_factory=list)   # named in a note, not as a place lived


def _approved_checks(data: Data, records):
    """Approved identity checks of a person: from the KYC provider and from onboarding notes."""
    checks = []
    for cid in records:
        for o in data.onb_by_cust.get(cid, []):
            for k in data.kyc_by_app.get(o.get("kyc_applicant_id"), []):
                if text(k.get("result")).lower() != "approved":
                    continue
                checks.append({
                    "id": k.get("check_id"),
                    "kind": "kyc",
                    "name": text(k.get("verified_name")) or None,
                    "dob": parse_date(k.get("verified_dob")),
                    "countries": [c for c in [country(k.get("document_country"))] if c],
                    "raw_country": k.get("document_country"),
                    "date": parse_date(k.get("checked_at")),
                    "via": f"{cid} -> onboarding {o.get('kyc_applicant_id')} -> approved check {k.get('check_id')}",
                })
        email = text(data.customers[cid].get("email")).lower()
        for n in data.notes_by_email.get(email, []) if email else []:
            if text(n.get("channel")).lower() != "kyc":
                continue
            r = parse_kyc_note(text(n.get("text")), n.get("created_at"))
            if not r or not r["approved"] or not (r["name"] or r["dob"]):
                continue
            checks.append({
                "id": n.get("note_id"),
                "kind": "note",
                "name": r["name"],
                "dob": r["dob"],
                "countries": r["countries"],
                "date": r["checked_at"],
                "via": f"{cid} email -> notes -> {n.get('note_id')}",
            })
    seen, out = set(), []
    for c in checks:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


def build_picture(data: Data, records, primary, alert) -> Picture:
    recs = [data.customers[c] for c in records]
    path = []
    kinds = [is_company(r.get("entity_type")) for r in recs if is_company(r.get("entity_type")) is not None]
    company = kinds[0] if kinds else is_company(alert.get("entity_type"))

    checks = _approved_checks(data, records)
    verified = None
    if checks:
        # Most recent approved check; on a tie the provider's own record wins over a note.
        verified = max(checks, key=lambda c: (c["date"].earliest() if c["date"] else (0, 0, 0), c["kind"] == "kyc"))
        path.append(f"latest approved check: {verified['via']}")

    verified_name = Fact(verified["name"], verified["id"]) if verified and verified["name"] else None
    declared = []
    for cid, r in zip(records, recs):
        for key in ("full_name", "preferred_name"):
            if r.get(key):
                declared.append(Fact(text(r[key]), cid))
    if alert.get("customer_name"):
        declared.append(Fact(text(alert["customer_name"]), alert.get("alert_id")))

    dob, dob_verified = None, False
    if verified and verified["dob"]:
        dob, dob_verified = Fact(verified["dob"], verified["id"]), True
    else:
        order = ([primary] if primary in records else []) + [c for c in records if c != primary]
        for cid in order:
            d = data.cust_dob.get(cid)
            if d:
                dob = Fact(d, cid)
                break
        if dob is None and parse_date(alert.get("dob")):
            dob = Fact(parse_date(alert.get("dob")), alert.get("alert_id"))

    countries = []

    def add(value, source):
        if value and not any(f.value == value and f.source == source for f in countries):
            countries.append(Fact(value, source))

    unrecognised, mentioned = [], []

    def unknown(value):
        if is_named_place(value) and str(value) not in unrecognised:
            unrecognised.append(str(value))

    for cid, r in zip(records, recs):
        for key in ("residence_country", "nationality"):
            add(country(r.get(key)), cid)
            unknown(r.get(key))
    if verified:
        for c in verified["countries"]:
            add(c, verified["id"])
        if not verified["countries"]:
            # The approved document's country could not be read, so it is unknown, not absent.
            unrecognised.append(f"document country of {verified['id']}")
    for cid, r in zip(records, recs):
        email = text(r.get("email")).lower()
        for n in data.notes_by_email.get(email, []) if email else []:
            if text(n.get("channel")).lower() == "kyc":
                continue
            lived, named = note_countries(text(n.get("text")))
            for c in lived:
                add(c, n.get("note_id"))
            for c in named:
                mentioned.append(Fact(c, n.get("note_id")))
    if not records:
        add(country(alert.get("country")), alert.get("alert_id"))

    return Picture(tuple(records), primary, company, verified, verified_name, declared,
                   dob, dob_verified, countries, path, unrecognised, mentioned)
