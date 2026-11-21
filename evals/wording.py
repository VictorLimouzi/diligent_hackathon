"""Wording stress test for the text extractors.

The unseen set words its free text differently. This asks a language model for
many differently worded sentence *templates* per kind of fact, with
placeholders only (no development record is sent anywhere), fills them with
invented values, runs the engine's extractors, and compares what they read
with what was written.

Two failure kinds are counted separately:
  unread: the extractor returned nothing        -> costs at most a review
  WRONG:  the extractor returned something else -> could clear a true match

Usage: .venv/bin/python -m evals.wording [--n 60] [--refresh]
Templates are cached in out/wording/ so reruns are free and repeatable.
"""
import argparse
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from casebook.dates import PDate  # noqa: E402
from casebook.extract import (note_countries, parse_kyc_note, parse_snippet,  # noqa: E402
                              profile_birth_dates)

OUT = os.environ.get("CASEBOOK_WORDING_OUT") or os.path.join(ROOT, "out", "wording")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
COUNTRIES = [("Germany", "German"), ("Nigeria", "Nigerian"), ("United Kingdom", "British"),
             ("Bangladesh", "Bangladeshi"), ("Spain", "Spanish"), ("United States", "American"),
             ("Italy", "Italian"), ("France", "French"), ("Kenya", "Kenyan"), ("Brazil", "Brazilian"),
             ("Pakistan", "Pakistani"), ("Netherlands", "Dutch"), ("South Africa", "South African"),
             ("Poland", "Polish"), ("Ireland", "Irish"), ("Sweden", "Swedish")]
FIRST = ["Amina", "Tomasz", "Chloe", "Rajesh", "Elena", "Kofi", "Sofia", "Nils", "Hadiza", "Marco",
         "Jordan", "Georgia", "Chad", "Florence", "Israel", "Ireland"]   # some are also place names
LAST = ["Bello", "Drozd", "Green", "Kumar", "Romano", "Mensah", "Rossi", "Hansen", "Lawal", "Weber"]
PROVIDERS = ["Sumsub", "Veriff", "Persona", "Onfido"]
DOCS = ["passport", "national ID", "driving licence"]
LISTINGS = ["a sanctioned individual", "a politically exposed person", "the subject of enforcement action",
            "named in adverse media", "a state-owned enterprise official", "named on a watchlist"]
JOBS = ["a hospital nurse", "a taxi driver", "a youth football coach", "a software developer"]

KINDS = {
    "kyc_ok": (
        "a short onboarding note recording that an identity-check provider APPROVED a customer's identity",
        "{PROVIDER} {NAME} {DOB} {DOC} {COUNTRY_OR_DEMONYM} {CHECK_DATE}",
        "Every template must contain {NAME}, {DOB} and {CHECK_DATE}, and either {COUNTRY} or {DEMONYM} "
        "(the adjective, as in 'his {DEMONYM} passport'). {PROVIDER} and {DOC} are optional."),
    "kyc_bad": (
        "a short onboarding note recording that an identity check was NOT successful (rejected, failed, expired, "
        "could not be completed, document unreadable, and so on)",
        "{PROVIDER} {NAME} {DOB} {DOC} {COUNTRY} {CHECK_DATE}",
        "Every template must contain {NAME}, {DOB} and {CHECK_DATE}. Make clear the check did not pass, in many "
        "different ways."),
    "residence": (
        "a short support note recording where a customer used to live and work",
        "{COUNTRY} {FROM_YEAR} {TO_YEAR}",
        "Every template must contain {COUNTRY} (or {DEMONYM} as an adjective) and both years. Do not name the "
        "customer. Vary the vocabulary a lot: living, housing, career, family, schooling, postings and so on."),
    "snippet_listing": (
        "a one or two sentence web search snippet about a person who appears on a watchlist",
        "{NAME} {AGE} {MONTH} {YEAR} {COUNTRY} {LISTING}",
        "Every template must contain {NAME}, {AGE}, {MONTH}, {YEAR}, {LISTING}, and either {COUNTRY} or "
        "{DEMONYM}. {LISTING} is a fixed noun phrase such as 'a politically exposed person' or 'named in "
        "adverse media': keep it intact and make it read naturally. {AGE} is the person's age at {MONTH} {YEAR}."),
    "snippet_ordinary": (
        "a one or two sentence web search snippet about an ordinary person in local news",
        "{NAME} {AGE} {MONTH} {YEAR} {COUNTRY} {JOB}",
        "Every template must contain {NAME}, {AGE}, {MONTH}, {YEAR}, {JOB}, and either {COUNTRY} or {DEMONYM}. "
        "{JOB} is a noun phrase such as 'a taxi driver'."),
    "profile_born": (
        "one sentence from a watchlist profile stating when the listed person was born",
        "{NAME} {BIRTH}",
        "Every template must contain {NAME} and {BIRTH}. {BIRTH} is a date or a bare year, so word the sentence "
        "to work with both ('was born on/in', 'date of birth', 'born', and other natural ways)."),
    "profile_age": (
        "one sentence from a watchlist profile giving the listed person's age at the time of an event",
        "{NAME} {AGE} {MONTH} {YEAR}",
        "Every template must contain {NAME}, {AGE}, {MONTH} and {YEAR}. The event can be an arrest, a charge, "
        "a sentence, an appointment, a sanction and so on. One sentence, no other numbers."),
}


def templates(kind, n, refresh=False):
    path = os.path.join(OUT, f"{kind}.json")
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    from agents import client
    client.load_env()
    what, slots, rules = KINDS[kind]
    prompt = (
        f"Write {n} templates for {what}.\n\n"
        f"Placeholders available: {slots}. Write placeholders exactly like that, in braces. {rules}\n\n"
        "Make the templates as different from each other as you can: different sentence structures, word order, "
        "voice, register (terse log line, formal record, casual note), vocabulary and punctuation. Put facts in "
        "unusual positions. Some may be two short sentences. Do not invent any other names, numbers or dates.\n\n"
        "Output one template per line and nothing else: no numbering, no quotes, no commentary.")
    request = client.build_request(
        model=client.model_interactive(), feature="queue",
        system="You write realistic, varied text for testing an information-extraction system.",
        messages=[{"role": "user", "content": prompt}])
    message, _ = client.complete(request, feature="wording")
    lines = [re.sub(r"^\s*(?:\d+[.)]\s*|[-*]\s*)", "", ln).strip().strip('"') for ln in client.text_of(message).splitlines()]
    out = [ln for ln in lines if ln and "{" in ln]
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def fmt_date(rng, y, m, d):
    return rng.choice([f"{y:04d}-{m:02d}-{d:02d}", f"{d:02d}/{m:02d}/{y:04d}", f"{d} {MONTHS[m - 1]} {y}",
                       f"{MONTHS[m - 1]} {d}, {y}"])


def fill(template, values):
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def run(kind, items, rng, caps_names=True):
    """Returns (n, unread, wrong, examples) for one kind."""
    n = unread = wrong = 0
    examples = {"unread": [], "wrong": []}

    def miss(which, text, detail):
        nonlocal unread, wrong
        if which == "wrong":
            wrong += 1
        else:
            unread += 1
        if len(examples[which]) < 12:
            examples[which].append((text, detail))

    for template in items:
        for _ in range(3):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            name = f"{first} {last}"
            country, demonym = rng.choice(COUNTRIES)
            by, bm, bd = rng.randint(1945, 2004), rng.randint(1, 12), rng.randint(1, 28)
            cy, cm, cd = rng.randint(2022, 2025), rng.randint(1, 12), rng.randint(1, 28)
            check = f"{cy:04d}-{cm:02d}-{cd:02d}"
            age = rng.randint(19, 78)
            ey, em = rng.randint(1975, 2025), rng.randint(1, 12)
            listing, job = rng.choice(LISTINGS), rng.choice(JOBS)
            birth_full = rng.random() < 0.6
            values = {
                "PROVIDER": rng.choice(PROVIDERS), "NAME": name.upper() if caps_names and kind.startswith("kyc") else name,
                "DOB": fmt_date(rng, by, bm, bd), "DOC": rng.choice(DOCS), "COUNTRY": country, "DEMONYM": demonym,
                "COUNTRY_OR_DEMONYM": country, "CHECK_DATE": check, "FROM_YEAR": 2008 + rng.randint(0, 6),
                "TO_YEAR": 2016 + rng.randint(0, 6), "AGE": age, "MONTH": MONTHS[em - 1], "YEAR": ey,
                "LISTING": listing, "JOB": job,
                "BIRTH": fmt_date(rng, by, bm, bd) if birth_full else str(by),
            }
            text = fill(template, values)
            if "{" in text:
                continue   # a placeholder this harness does not know
            n += 1

            if kind in ("kyc_ok", "kyc_bad"):
                r = parse_kyc_note(text, check)
                if kind == "kyc_bad":
                    if r["approved"]:
                        miss("wrong", text, "read as an approved check")
                    continue
                if not r["approved"]:
                    miss("unread", text, "read as not approved")
                    continue
                if r["dob"] is None:
                    miss("unread", text, "no birth date")
                elif r["dob"] != PDate(by, bm, bd):
                    miss("wrong", text, f"birth date {r['dob'].iso()}")
                got = re.sub(r"[^\w\s]", "", (r["name"] or "").lower())
                if not got:
                    miss("unread", text, "no name")
                elif set(got.split()) != set(name.lower().split()):
                    miss("wrong", text, f"name {r['name']!r}")
                if not r["countries"]:
                    miss("unread", text, "no country")
                elif r["countries"] != [country]:
                    miss("wrong", text, f"countries {r['countries']}")

            elif kind == "residence":
                lived, named = note_countries(text)
                if lived == [country]:
                    pass
                elif not lived and named == [country]:
                    miss("unread", text, "country read as mentioned, not lived in")
                elif not lived and not named:
                    miss("unread", text, "no country")   # would drop a tie: dangerous, see report
                else:
                    miss("wrong", text, f"lived {lived} mentioned {named}")

            elif kind.startswith("snippet"):
                r = parse_snippet(f"{name} - news", text, name.lower())
                want_phrase = re.sub(r"^(a|the) ", "", listing) if kind == "snippet_listing" else None
                if want_phrase and want_phrase not in r["phrases"]:
                    miss("unread", text, f"listing phrase not found {r['phrases']}")
                if not want_phrase and r["phrases"]:
                    miss("wrong", text, f"listing phrase {r['phrases']} in an ordinary snippet")
                if r["countries"] != [country]:
                    miss("wrong" if r["countries"] else "unread", text, f"countries {r['countries']}")
                if r["age"] is None:
                    miss("unread", text, "no age at a date")
                elif (r["age"], r["date"].y) != (age, ey):
                    miss("wrong", text, f"age {r['age']} at {r['date'].iso()}")

            elif kind == "profile_born":
                dates, _ = profile_birth_dates(text)
                want = PDate(by, bm, bd) if birth_full else PDate(by)
                if not dates:
                    miss("unread", text, "no birth date")
                elif dates != [want] and dates != [PDate(want.y)]:
                    # a numeric date read as its year alone is a deliberate partial read, not an error
                    miss("wrong", text, f"{[d.iso() for d in dates]} for {want.iso()}")

            elif kind == "profile_age":
                dates, _ = profile_birth_dates(text)
                want = [PDate(ey - age - 1), PDate(ey - age)]
                if not dates:
                    miss("unread", text, "no age at a date")
                elif dates != want:
                    miss("wrong", text, f"{[d.iso() for d in dates]} for {[d.iso() for d in want]}")
    return n, unread, wrong, examples


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=60, help="templates to ask for per kind")
    ap.add_argument("--refresh", action="store_true", help="ask for new templates")
    ap.add_argument("--title-case-names", action="store_true", help="write names in notes as 'Amina Bello', not capitals")
    ap.add_argument("--show", type=int, default=6, help="examples to print per failure kind")
    args = ap.parse_args()
    total_wrong = 0
    report = {}
    for kind in KINDS:
        items = templates(kind, args.n, args.refresh)
        n, unread, wrong, ex = run(kind, items, random.Random(7), caps_names=not args.title_case_names)
        total_wrong += wrong
        report[kind] = {"templates": len(items), "sentences": n, "unread": unread, "wrong": wrong}
        print(f"\n== {kind}: {len(items)} templates, {n} sentences, unread {unread}, WRONG {wrong}")
        for which in ("wrong", "unread"):
            for text, detail in ex[which][:args.show]:
                print(f"   {which.upper():6s} {detail}\n          {text}")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("\n" + json.dumps(report))
    return 1 if total_wrong else 0


if __name__ == "__main__":
    sys.exit(main())
