"""Diligent's screening SOP as code: clear only on a contradiction, confirm only on agreement."""
from dataclasses import dataclass, field
from typing import List, Optional

from .customer import Picture
from .dates import PDate, dob_agrees, dob_contradicts, max_age_at
from .hit import HitFacts
from .load import Data
from .names import is_latin, names_agree

DIFFERENT, SAME, REVIEW = "DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"

# Order in which a main reason is picked when several clear a hit: the facts
# that rest on the most reliable sources first.
CLEAR_ORDER = ["ENTITY_TYPE_MISMATCH", "DOB_MISMATCH", "NAME_MISMATCH_VERIFIED",
               "TEMPORAL_IMPOSSIBILITY", "DECEASED", "LOCATION_MISMATCH"]


@dataclass
class Check:
    name: str            # type | name | dob | location | time | death
    outcome: str         # agree | contradict | unknown
    detail: str
    sources: List[str] = field(default_factory=list)
    blocks_confirm: bool = False   # unknown, but too doubtful to confirm a match on


# The rule behind each reason code, as the closing sentence of a written reason.
CLOSING = {
    "ENTITY_TYPE_MISMATCH": "A person and a company cannot be the same entity, so the hit is cleared.",
    "DOB_MISMATCH": "Every date the hit gives is contradicted, so the hit is a different entity.",
    "NAME_MISMATCH_VERIFIED": "The verified legal name outranks the name on record and the screened name, "
                              "so the hit is cleared.",
    "TEMPORAL_IMPOSSIBILITY": "The customer could not have done what the profile reports, so the hit is cleared.",
    "DECEASED": "A customer screened after that date cannot be the listed party, so the hit is cleared.",
    "LOCATION_MISMATCH": "No country is shared, and name and date of birth do not both agree, "
                         "so the hit is cleared.",
    "NAME_DOB_LOCATION_MATCH": "Nothing in the customer's records contradicts the hit, so it is confirmed "
                               "as the customer.",
    "NAME_DOB_MATCH": "Nothing in the customer's records contradicts the hit, so it is confirmed as the customer.",
    "NAME_LOCATION_MATCH": "Nothing in the company's records contradicts the hit, so it is confirmed "
                           "as the customer.",
}


@dataclass
class Decision:
    decision: str
    reason_codes: List[str]
    evidence: List[str]
    summary: str
    checks: List[Check]
    reason_parts: List[str] = field(default_factory=list)   # two to four sentences

    @property
    def reason(self):
        return " ".join(self.reason_parts)

    @property
    def reason_code(self):
        return self.reason_codes[0]


def _fmt_dates(ds):
    return ", ".join(d.iso() for d in ds)


def _name_check(data: Data, pic: Picture, hit: HitFacts) -> Check:
    company = bool(pic.company)
    hit_parts = [(n, data.parts(n, company)) for n in hit.names]
    hit_parts = [(n, p) for n, p in hit_parts if p]
    if not hit_parts:
        return Check("name", "unknown", "The hit gives no usable name.")

    def agreeing(name):
        parts = data.parts(name, company)
        for n, p in hit_parts:
            if names_agree(parts, p, data.rules):
                return n
        return None

    if pic.verified_name:
        v = pic.verified_name
        if not company and len(data.parts(v.value, company)) < 2:
            return Check("name", "unknown",
                         f"The verified name {v.value} ({v.source}) has fewer than two real parts.", [v.source])
        n = agreeing(v.value)
        if n:
            # Agreement on name is not cited on its own: the practice labels cite
            # the name's source only when the verified name clears the hit.
            return Check("name", "agree",
                         f"The customer's verified name {v.value} ({v.source}) agrees with the hit's name {n}.")
        latin = is_latin(data.parts(v.value, company))
        if not any(is_latin(p) == latin for _, p in hit_parts):
            # Every name the hit gives is in another script: nothing was compared, so nothing differs.
            return Check("name", "unknown",
                         f"The hit's names are written in a different script from the verified name "
                         f"{v.value} ({v.source}), so they cannot be compared.", [v.source])
        return Check("name", "contradict",
                     f"The customer's verified name is {v.value} ({v.source}), which does not match "
                     f"the hit's name {hit.names[0]}.", [v.source])

    usable = [f for f in pic.declared_names if company or len(data.parts(f.value, company)) >= 2]
    if not usable:
        return Check("name", "unknown", "The customer's names have fewer than two real parts.")
    for f in usable:
        n = agreeing(f.value)
        if n:
            return Check("name", "agree",
                         f"The customer's name {f.value} agrees with the hit's name {n}.")
    # A declared name that differs is not a contradiction: only a verified name clears.
    return Check("name", "unknown",
                 f"The name on record does not agree with the hit's name {hit.names[0]}, "
                 f"but it is not verified, so it cannot clear the hit.")


def _dob_check(pic: Picture, hit: HitFacts) -> Check:
    what = "incorporation date" if pic.company else "date of birth"
    if not pic.dob:
        return Check("dob", "unknown", f"The customer's {what} is not known.")
    if not hit.dobs:
        return Check("dob", "unknown", f"The hit gives no {what} to compare.")
    c: PDate = pic.dob.value
    src = [pic.dob.source] if not str(pic.dob.source).startswith("A-") else []
    src = src + list(hit.dob_sources)
    dates = _fmt_dates(hit.dobs)
    noun = "incorporation dates" if pic.company else "birth dates"
    source = {"dobs field": f"the {noun} listed on the hit", "profile notes": f"the {noun} in the hit's profile notes",
              "search results": f"the {noun} in search results about the hit"}.get(hit.dob_origin, f"the hit's {noun}")
    if any(dob_agrees(c, h) for h in hit.dobs):
        return Check("dob", "agree",
                     f"The customer's {what} {c.iso()} ({pic.dob.source}) agrees with {source} ({dates}).", src)
    if hit.dobs_unreadable:
        return Check("dob", "unknown",
                     f"The hit lists a {what} that cannot be read as a date, so not every date it gives "
                     f"can be compared with the customer's {c.iso()}.", src)
    if all(dob_contradicts(c, h) for h in hit.dobs):
        return Check("dob", "contradict",
                     f"The customer's {what} is {c.iso()} ({pic.dob.source}), which contradicts {source} ({dates}).", src)
    return Check("dob", "unknown",
                 f"The customer's {what} {c.iso()} neither agrees with nor contradicts {source} ({dates}).", src)


def _location_check(pic: Picture, hit: HitFacts) -> Check:
    cust = []
    for f in pic.countries:
        if f.value not in cust:
            cust.append(f.value)
    if not cust:
        return Check("location", "unknown", "No country is tied to the customer.")
    if not hit.countries:
        return Check("location", "unknown", "No country is tied to the hit.")
    shared = [c for c in cust if c in hit.countries]
    if shared:
        srcs = list(dict.fromkeys(f.source for f in pic.countries if f.value in shared))
        return Check("location", "agree", f"Both the customer and the hit are linked to {', '.join(shared)}.", srcs)
    maybe = [f for f in pic.countries_mentioned if f.value in hit.countries]
    if maybe:
        return Check("location", "unknown",
                     f"No country the customer lived in is shared with the hit, but a note ({maybe[0].source}) "
                     f"mentions {maybe[0].value} without saying the customer lived there, so location can "
                     f"neither clear nor confirm the hit.", [maybe[0].source], blocks_confirm=True)
    unknown = list(pic.countries_unrecognised) + list(hit.countries_unrecognised)
    if unknown:
        return Check("location", "unknown",
                     f"No country is shared, but the country behind {unknown[0]!r} could not be recognised, "
                     f"so location cannot clear the hit.")
    if not pic.records:
        return Check("location", "unknown",
                     "No customer record was found, so the customer's countries are not fully known "
                     "and location cannot clear the hit.")
    srcs = list(dict.fromkeys(f.source for f in pic.countries))
    return Check("location", "contradict",
                 f"The customer is linked only to {', '.join(sorted(cust))} but the hit is linked only to "
                 f"{', '.join(sorted(hit.countries))}.", srcs)


def _time_check(pic: Picture, hit: HitFacts) -> Check:
    if not hit.earliest_report or not pic.dob:
        return Check("time", "unknown", "No dated report to set against the customer's age.")
    c, r = pic.dob.value, hit.earliest_report
    src = [pic.dob.source] if not str(pic.dob.source).startswith("A-") else []
    if pic.company:
        if c.earliest() > r.latest():
            return Check("time", "contradict",
                         f"The company was incorporated on {c.iso()} ({pic.dob.source}), after the hit's "
                         f"earliest dated report of {r.iso()}.", src)
        return Check("time", "agree", f"The company existed at the earliest dated report ({r.iso()}).")
    age = max_age_at(c, r)
    if age < 16:
        return Check("time", "contradict",
                     f"The customer was born in {c.y} ({pic.dob.source}), so they were at most {max(age, 0)} "
                     f"when the hit's profile reports activity on {r.iso()}.", src)
    return Check("time", "agree", f"The customer was at least 16 at the earliest dated report ({r.iso()}).")


def _death_check(hit: HitFacts, screened_at: Optional[PDate]) -> Check:
    if not hit.death or not screened_at:
        return Check("death", "unknown", "The profile gives no date of death.")
    if hit.death.latest() < screened_at.earliest():
        return Check("death", "contradict",
                     f"The hit's profile gives a date of death of {hit.death.iso()}, before the customer "
                     f"was screened on {screened_at.iso()}.")
    return Check("death", "unknown", "The date of death is not before the screening date.")


def decide(data: Data, pic: Picture, hit: HitFacts, screened_at: Optional[PDate]) -> Decision:
    checks = []
    if pic.company is not None and hit.company is not None and pic.company != hit.company:
        kinds = ("a company", "a person") if pic.company else ("a person", "a company")
        checks.append(Check("type", "contradict", f"The customer is {kinds[0]} and the hit is {kinds[1]}.",
                            [pic.primary] if pic.primary else []))
    name = _name_check(data, pic, hit)
    dob = _dob_check(pic, hit)
    loc = _location_check(pic, hit)
    time = _time_check(pic, hit)
    death = _death_check(hit, screened_at)
    checks += [name, dob, loc, time, death]

    by = {c.name: c for c in checks}
    name_dob_agree = name.outcome == "agree" and dob.outcome == "agree"
    # A date that is present but unreadable might agree: then country alone must not clear.
    dob_might_agree = dob.outcome == "agree" or (hit.dobs_unreadable and pic.dob is not None)
    clears = {}
    if "type" in by:
        clears["ENTITY_TYPE_MISMATCH"] = by["type"]
    if dob.outcome == "contradict":
        clears["DOB_MISMATCH"] = dob
    if name.outcome == "contradict":
        clears["NAME_MISMATCH_VERIFIED"] = name
    if time.outcome == "contradict":
        clears["TEMPORAL_IMPOSSIBILITY"] = time
    if death.outcome == "contradict":
        clears["DECEASED"] = death
    # A country difference does not clear a hit whose name and date of birth agree.
    if loc.outcome == "contradict" and not (name.outcome == "agree" and dob_might_agree):
        clears["LOCATION_MISMATCH"] = loc

    def cite(used):
        out = []
        for c in used:
            for s in c.sources:
                if s and s not in out:
                    out.append(s)
        return out

    if clears:
        codes = [c for c in CLEAR_ORDER if c in clears]
        used = [clears[c] for c in codes]
        return Decision(DIFFERENT, codes, cite(used), " ".join(c.detail for c in used), checks,
                        [c.detail for c in used][:3] + [CLOSING[codes[0]]])

    if not pic.company and name_dob_agree:
        if loc.blocks_confirm:
            closing = "A possible tie to the hit's country is not a tie, so a person must review it."
            return Decision(REVIEW, ["INSUFFICIENT_DATA"], cite([name, dob]),
                            f"{dob.detail} {name.detail} {loc.detail} {closing}", checks,
                            [dob.detail, name.detail, loc.detail, closing])
        if loc.outcome == "contradict":
            closing = "A country difference does not clear a hit whose name and date of birth agree, " \
                      "so a person must review it."
            return Decision(REVIEW, ["INSUFFICIENT_DATA"], cite([name, dob, loc]),
                            f"{dob.detail} {name.detail} {loc.detail} {closing}", checks,
                            [dob.detail, name.detail, loc.detail, closing])
        if loc.outcome == "agree":
            return Decision(SAME, ["NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH"], cite([dob, loc, name]),
                            f"{dob.detail} {loc.detail} {name.detail}", checks,
                            [dob.detail, loc.detail, name.detail, CLOSING["NAME_DOB_LOCATION_MATCH"]])
        return Decision(SAME, ["NAME_DOB_MATCH"], cite([dob, name]), f"{dob.detail} {name.detail}", checks,
                        [dob.detail, name.detail, CLOSING["NAME_DOB_MATCH"]])

    if pic.company and name.outcome == "agree" and loc.outcome == "agree":
        return Decision(SAME, ["NAME_LOCATION_MATCH"], cite([loc, name]), f"{loc.detail} {name.detail}", checks,
                        [loc.detail, name.detail, CLOSING["NAME_LOCATION_MATCH"]])

    agreed = [c for c in (loc, name, dob) if c.outcome == "agree"]
    missing = []
    if name.outcome != "agree":
        missing.append(name.detail)
    if pic.company:
        if loc.outcome != "agree":
            missing.append(loc.detail)
    elif dob.outcome != "agree":
        missing.append(dob.detail)
    closing = "Nothing contradicts the hit and there is not enough agreement to confirm it."
    parts = [c.detail for c in agreed] + missing
    summary = " ".join(parts + [closing])
    return Decision(REVIEW, ["INSUFFICIENT_DATA"], cite(agreed), summary, checks, parts[:3] + [closing])
