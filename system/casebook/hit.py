"""The hit's facts, gathered wherever they are (policy 5)."""
from dataclasses import dataclass, field
from typing import List, Optional

from .countries import country, country_of_place, is_named_place
from .customer import is_company
from .dates import PDate, parse_date
from .extract import (birth_years_from_age, dated_reports, listing_phrase,
                      parse_snippet, profile_birth_dates)
from .load import Data, as_list, text
from .names import split_aliases


@dataclass
class HitFacts:
    hit_id: str
    company: Optional[bool]
    names: List[str]
    dobs: List[PDate]
    dob_origin: Optional[str]            # 'dobs field' | 'profile notes' | 'search results'
    dob_quote: Optional[str]
    dob_sources: List[str]               # search result ids used
    dobs_unreadable: bool                # the dobs field holds a value that is not a date
    countries: List[str]
    countries_unrecognised: List[str]    # place values whose country could not be told
    earliest_report: Optional[PDate]
    earliest_report_line: Optional[str]
    death: Optional[PDate]
    search_considered: List[dict] = field(default_factory=list)


def hit_countries(hit):
    """(countries tied to the hit, place values whose country could not be recognised)."""
    out, unknown = [], []

    def add(value, c):
        if c:
            if c not in out:
                out.append(c)
        elif is_named_place(value) and str(value) not in unknown:
            unknown.append(str(value))

    for key in ("countries", "nationality", "citizenship"):
        for v in as_list(hit.get(key)):
            add(v, country(v) or country_of_place(v))
    for key in ("addresses", "place_of_birth"):
        for v in as_list(hit.get(key)):
            add(v, country_of_place(v))
    return out, unknown


def gather(data: Data, hit) -> HitFacts:
    company = is_company(hit.get("entity_type"))
    names = [text(hit.get("name"))] + split_aliases(hit.get("aliases"))
    names = [n for n in names if n]
    countries, unrecognised = hit_countries(hit)
    notes = text(hit.get("profile_notes"))

    dobs, origin, quote, sources, considered = [], None, None, [], []
    unreadable = False
    for raw in as_list(hit.get("dobs")):
        d = parse_date(raw, day_first=None)
        if d and d not in dobs:
            dobs.append(d)
        elif d is None:
            unreadable = True
    if dobs:
        origin, quote = "dobs field", text(hit.get("dobs"))
    elif not company:
        # For companies only the dobs field is an incorporation date.
        dobs, quote = profile_birth_dates(notes)
        if dobs:
            origin = "profile notes"
        else:
            phrase = listing_phrase(text(hit.get("primary_category")), as_list(hit.get("categories")))
            for s in data.search_results(hit.get("name")):
                r = parse_snippet(text(s.get("title")), text(s.get("snippet")), text(s.get("query")))
                same_listing = phrase in r["phrases"]
                same_country = any(c in countries for c in r["countries"])
                about_hit = same_listing and same_country
                why = None
                if not same_listing:
                    why = f"not described as \"{phrase}\""
                elif not same_country:
                    why = f"in {', '.join(r['countries']) or 'no stated country'}, not a country tied to the hit"
                elif r["age"] is None:
                    why = "about the hit, but gives no readable age at a date"
                considered.append({
                    "result_id": s.get("result_id"), "snippet": text(s.get("snippet")),
                    "about_hit": about_hit, "why_not": why,
                })
                if about_hit and r["age"] is not None:
                    for d in birth_years_from_age(r["age"], r["date"]):
                        if d not in dobs:
                            dobs.append(d)
                    sources.append(s.get("result_id"))
            if dobs:
                origin = "search results"

    reports = dated_reports(notes)
    earliest, line = None, None
    if reports:
        earliest, line = min(reports, key=lambda r: r[0].earliest())

    return HitFacts(
        hit_id=hit.get("hit_id"), company=company, names=names, dobs=dobs, dob_origin=origin,
        dob_quote=quote, dob_sources=sources, dobs_unreadable=unreadable, countries=countries,
        countries_unrecognised=unrecognised,
        earliest_report=earliest, earliest_report_line=line,
        death=parse_date(hit.get("deceased_date"), day_first=None), search_considered=considered,
    )
