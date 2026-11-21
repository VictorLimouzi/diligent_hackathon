"""Facts from free text: onboarding notes, hit profile notes and web search snippets.

Every extractor returns None (or an empty result) when it cannot read the text
with confidence. A missing fact sends a hit to REVIEW; a wrong fact can clear a
true match, so nothing here guesses.
"""
import re

from .countries import countries_in_text
from .dates import MONTH_RE, MONTHS as MONTHS_BY_NAME, PDate, find_dates, parse_date

# ---------------------------------------------------------------- KYC notes

_CAPS_STOP = {"DOB", "ID", "KYC", "UK", "US", "USA", "UAE", "EU", "D.O.B", "D.O.B.", "NIN", "OK", "PEP", "AML"}
_CAPS_TOKEN = re.compile(r"[^\W\d_a-z]+(?:[-'’.][^\W\d_a-z]+)*\.?", re.UNICODE)
# "verified with no issues" is good news: such phrases are set aside before looking for bad news.
_NO_PROBLEM = re.compile(
    r"\b(?:no|without|zero|nil|free\s+of)\s+(?:\w+\s+){0,2}(?:issues?|problems?|concerns?|discrepanc\w+|flags?|"
    r"errors?|mismatch\w*|anomal\w+|irregularit\w+|adverse\w*|objections?|exceptions?|doubts?|"
    r"red\s+flags?|inconsistenc\w+|alerts?|hits?|findings?)\b", re.I)
_NOT_APPROVED = re.compile(
    r"\b(?:reject\w*|expir\w*|fail\w*|declin\w*|unsuccessful\w*|denied|deny|invalid\w*|lapsed|refus\w*|revoked|"
    r"withdrawn|unverified|unconfirmed|pending|incomplete|abandon\w*|cancel\w*|void\w*|superseded|"
    r"mismatch\w*|discrepan\w*|inconclusive|suspect\w*|suspicious|fraud\w*|forg\w*|tamper\w*|counterfeit|"
    r"fake|bounc\w*|negative|unable|cannot|unreadable|illegible|blurr\w*|problem\w*|error\w*|"
    r"block\w*|halt\w*|flagged|retry|resubmit\w*|re-?attempt\w*|timed\s+out|out\s+of\s+date|"
    r"not|no|never|nor|neither|without)\b|n['’]t\b",
    re.I,
)


def note_is_approved(text):
    """True unless the note reads as a check that did not pass. Any negation counts
    against it: a missed failure would make a failed check the customer's verified identity."""
    return _NOT_APPROVED.search(_NO_PROBLEM.sub(" ", text)) is None


_DOC_TYPES = ["passport", "national ID", "driving licence", "driver's licence", "driving license",
              "residence permit", "registry extract", "identity card", "ID card"]


def _caps_name(text):
    """The longest run of capitalised words: how notes write the verified name."""
    best = None
    run = []
    runs = []
    pos = 0
    for m in re.finditer(r"[^\s/|]+", text):
        tok = re.sub(r"['’][sS]$", "", m.group(0))
        core = tok.strip(",;:()[]\"“”—–-")
        if len(core) > 2:
            core = core.rstrip(".")
        is_caps = (
            bool(core)
            and _CAPS_TOKEN.fullmatch(core) is not None
            and core.upper() == core
            and core.lower() != core
            and core.strip(".") not in _CAPS_STOP
        )
        if is_caps:
            run.append(core)
            # a closing punctuation mark ends the name
            if m.group(0)[-1] in ",;:).—–" or m.group(0) != tok:
                runs.append(run)
                run = []
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)
    for r in runs:
        letters = sum(len(t) for t in r)
        # a lone initial or abbreviation is not a name
        if letters < 3:
            continue
        if best is None or letters > sum(len(t) for t in best):
            best = r
    return " ".join(best) if best else None


def parse_kyc_note(text, created_at=None):
    """An onboarding note that records an identity check.

    Returns dict(name, dob, countries, checked_at, approved, document_type) or None
    when the note does not read as an identity check.
    """
    if not text:
        return None
    name = _caps_name(text)
    dates = [d for d, _, _ in find_dates(text)]
    created = parse_date(created_at)
    checked = created
    # The check date is the note's own date; any other date is a candidate birth date.
    rest = []
    for d in dates:
        if (created and d == created) or d in rest:
            continue
        rest.append(d)
    dob = None
    if len(rest) == 1:
        dob = rest[0]
    elif len(rest) > 1:
        # Several candidates: take the one written next to a birth-date word, before or after it.
        near = []
        for m in re.finditer(r"DOB|D\.O\.B\.?|\bborn\b|birth", text, re.I):
            for d, s_, e_ in find_dates(text):
                if d in rest and (0 <= s_ - m.end() <= 25 or 0 <= m.start() - e_ <= 25) and d not in near:
                    near.append(d)
        if len(near) == 1:
            dob = near[0]
    if dob is None and not rest:
        # A birth year on its own: "DOB 1960", "born 1966", "a date of birth of 1974".
        m = re.search(r"(?:DOB|D\.O\.B\.?|born|birth)\D{0,25}\b((?:18|19|20)\d\d)\b(?![-/]\d)", text, re.I)
        if m:
            dob = PDate(int(m.group(1)))
    if created is None and dates:
        others = [d for d in dates if d != dob]
        checked = others[-1] if others else None
    # An identity document is not issued to a child of the check's own decade: a "birth date"
    # that close to the check date is the check date misread, so it is not used.
    if dob and checked and checked.y - dob.y < 10:
        dob = None
    scrub = text
    if name:
        scrub = scrub.replace(name, " ")
    found = countries_in_text(scrub)
    doc_type = next((t for t in _DOC_TYPES if t.lower() in text.lower()), None)
    return {
        "name": name,
        "dob": dob,
        # Every country the note names: leaving one out could fake a location mismatch.
        "countries": found,
        "checked_at": checked,
        "approved": note_is_approved(text),
        "document_type": doc_type,
    }


# ---------------------------------------------------------- residence notes

_LIVED = re.compile(
    r"\b(resid\w*|liv(?:ed|ing|e|es)\b|life\b|lives\b|work\w*|employ\w*|based|home\w*|hous(?:e|ed|ing|ehold)\w*|"
    r"stay\w*|dwell\w*|settl\w*|job\w*|profession\w*|career\w*|labou?r\w*|domicil\w*|relocat\w*|moved|"
    r"tenan\w*|rent\w*|stud(?:y|ied|ying|ent)\w*|school\w*|raised|grew\s+up|born|station\w*|posted|"
    r"citizen\w*|national\w*|expat\w*|\w*migrat\w*|address\w*|located|occup\w*)\b",
    re.I,
)


def lived_countries(text):
    """Countries a support note says the customer lived or worked in."""
    return note_countries(text)[0]


def note_countries(text):
    """Countries a support note names, as (lived, mentioned).

    `lived`: the note speaks of living or working there, so the country is tied
    to the customer. `mentioned`: the note names the country some other way (a
    trip, a transfer). A mentioned country is not a tie, but it might be one, so
    it stops a location clear without ever confirming a match.
    """
    if not text:
        return [], []
    found = countries_in_text(text, loose=True)
    if not found:
        return [], []
    return (found, []) if _LIVED.search(text) else ([], found)


# ------------------------------------------------------------ ages at dates

_AGE_EXPLICIT = [
    re.compile(r"\baged?\s+(?:of\s+)?(\d{1,3})\b", re.I),
    re.compile(r"\bage\s+of\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})[\s-]years?[\s-]old\b", re.I),
    re.compile(r"\b(\d{1,3})\s+years\s+of\s+age\b", re.I),
    re.compile(r"\bat\s+(\d{1,3})\s+years\b", re.I),
    re.compile(r"\b(?:was|is|turned|being)\s+(\d{1,3})\b(?!\s*(?:%|per|years?\s+in|months?|days?|weeks?))", re.I),
    re.compile(r",\s*(\d{1,3})\s*,"),
    re.compile(r"\(\s*(\d{1,3})\s*\)"),
    re.compile(r"\bat\s+(\d{1,3})\b(?!\s*(?:%|per|:|\.\d|am|pm|years?\s+in))", re.I),
]


def _small_numbers(text, date_spans):
    """Standalone integers that are not part of a date, a year or an amount."""
    out = []
    for m in re.finditer(r"(?<![\w.,/-])(\d{1,3})(?![\w/]|[.,]\d|-\d)", text):
        if any(s <= m.start() < e for s, e in date_spans):
            continue
        out.append(int(m.group(1)))
    return out


def age_at_date(sentence, loose=False):
    """(age, date) when a sentence gives one age and one date; else None.

    `loose` also accepts a lone small number as the age, for short one-sentence
    texts that are known to state an age.
    """
    found = find_dates(sentence)
    if not found:
        # "Back in 2019, around March, ..." or just "in 2019": one year, at most one month.
        years = [(int(m.group(0)), m.start(), m.end())
                 for m in re.finditer(r"(?<![\w/-])(?:18|19|20)\d\d(?![\w/]|-\d)", sentence)]
        if len({y for y, _, _ in years}) != 1:
            return None
        months = {MONTHS_BY_NAME[m.group(0).lower()] for m in re.finditer(rf"\b{MONTH_RE}\b", sentence)}
        month = months.pop() if len(months) == 1 else None
        found = [(PDate(years[0][0], month), s, e) for _, s, e in years]
    distinct = []
    for d, _, _ in found:
        if d not in distinct:
            distinct.append(d)
    if len(distinct) != 1:
        return None
    spans = [(s, e) for _, s, e in found]
    ages = set()
    for rx in _AGE_EXPLICIT:
        for m in rx.finditer(sentence):
            if any(s <= m.start(1) < e for s, e in spans):
                continue
            ages.add(int(m.group(1)))
    if not ages and loose:
        ages = set(_small_numbers(sentence, spans))
    if len(ages) != 1:
        return None
    age = ages.pop()
    # A listed adult: anything else is more likely a misread number than an age.
    if not (16 <= age <= 100):
        return None
    return age, distinct[0]


def birth_years_from_age(age, when: PDate):
    """Someone aged 45 in March 2019 was born in 1973 or 1974."""
    return [PDate(when.y - age - 1), PDate(when.y - age)]


# ------------------------------------------------------ hit profile notes

_BORN = re.compile(r"\b(?:born|date\s+of\s+birth|birth\s*date|birthdate|birthday|DOB|D\.O\.B\.?|b\.)(?![a-z])"
                   r"[^.\n;]{0,90}", re.I)
_BIRTH_WORD = re.compile(r"\b(?:born|birth\w*|natal|DOB|D\.O\.B\.?|b\.)|into\s+(?:the|this)\s+world|"
                         r"entered\s+the\s+world|first\s+saw\s+(?:the\s+)?(?:day)?light", re.I)
# Words for anything else a profile dates: with one of these present, a lone date may not be the birth date.
_EVENT_WORD = re.compile(r"sanction|arrest|charg|convict|sentenc|appoint|indict|detain|custody|designat|"
                         r"report|died|death|deceas|fined|accus|investigat|question|elect|served|"
                         r"prosecut|extradit|jail|prison|trial|court|fraud|launder|bribe|listed\s+(?:on|in|since)", re.I)
_REPORT = re.compile(
    rf"^According to .+?;\s*({MONTH_RE}\s+\d{{1,2}},\s+\d{{4}}):", re.M
)


def first_paragraph(notes):
    if not notes:
        return ""
    return re.split(r"\n\s*\n", notes.strip(), maxsplit=1)[0].strip()


def profile_birth_dates(notes):
    """Birth dates stated in the first paragraph of a hit's profile notes.

    Returns (dates, sentence). A stated birth date wins over an age at a date.
    Paragraphs that open a dated report ("According to …;") describe the
    listing, not the person's age, so they are not read for ages.
    """
    para = first_paragraph(notes)
    if not para:
        return [], None
    # 1. A birth word followed by its date: "born on 17 July 1939", "DOB: 1952".
    for m in _BORN.finditer(para):
        seg = m.group(0)
        found = find_dates(seg, day_first=None)
        if found:
            return [found[0][0]], _sentence_around(para, m.start())
        y = re.search(r"\b(1[89]\d\d|20\d\d)\b", seg)   # also the year of a date that cannot be read whole
        if y:
            return [PDate(int(y.group(1)))], _sentence_around(para, m.start())
    short = len(para) <= 240
    # 2. A short paragraph about a birth with exactly one date in it, wherever the date sits:
    #    "1980 is the recorded birth date of …". Not used when the paragraph also speaks of
    #    some other event, whose date could be the one found.
    if short and _BIRTH_WORD.search(para) and not _EVENT_WORD.search(para):
        found = find_dates(para, day_first=None)
        spans = [(s_, e_) for _, s_, e_ in found]
        dates = []
        for d, _, _ in found:
            if d not in dates:
                dates.append(d)
        for y in re.finditer(r"(?<![\w/-])(1[89]\d\d|20\d\d)(?![\w/]|-\d)", para):
            if not any(s_ <= y.start() < e_ for s_, e_ in spans) and PDate(int(y.group(1))) not in dates:
                dates.append(PDate(int(y.group(1))))
        if len(dates) == 1:
            return dates, para.strip()
    if re.match(r"\s*according\b", para, re.I):
        return [], None
    # 3. An age at a date, sentence by sentence, then the short paragraph as a whole.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", para) if s.strip()]
    for s in sentences:
        got = age_at_date(s, loose=short and len(sentences) == 1)
        if got:
            return birth_years_from_age(*got), s.strip()
    if short and len(sentences) > 1:
        got = age_at_date(para, loose=True)
        if got:
            return birth_years_from_age(*got), para.strip()
    return [], None


def _sentence_around(text, pos):
    start = max(text.rfind(".", 0, pos), text.rfind("\n", 0, pos)) + 1
    end = text.find(".", pos)
    end = len(text) if end < 0 else end + 1
    return text[start:end].strip()


def dated_reports(notes):
    """Dates of the report lines 'According to …; September 10, 2025: …'."""
    out = []
    for m in _REPORT.finditer(notes or ""):
        d = parse_date(m.group(1))
        if d:
            out.append((d, m.group(0)))
    return out


# -------------------------------------------------------- search snippets

LISTING_PHRASES = {
    "sanctions": "sanctioned individual",
    "pep": "politically exposed person",
    "enforcement": "subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "state-owned enterprise official",
}
OTHER_LISTING = "named on a watchlist"
# Categories by their letters only, so spacing, case and hyphens do not matter.
_CATEGORY_PHRASE = {
    "sanctions": LISTING_PHRASES["sanctions"], "sanction": LISTING_PHRASES["sanctions"],
    "pep": LISTING_PHRASES["pep"], "politicallyexposedperson": LISTING_PHRASES["pep"],
    "enforcement": LISTING_PHRASES["enforcement"],
    "adversemedia": LISTING_PHRASES["adverse media"],
    "soe": LISTING_PHRASES["soe"], "stateownedenterprise": LISTING_PHRASES["soe"],
}


def listing_phrase(primary_category, categories=None):
    """The wording a search result uses for the hit's kind of listing."""
    cat = (primary_category or "").strip()
    if not cat and categories:
        cat = categories[0].strip() if categories else ""
    key = re.sub(r"[^a-z]", "", cat.lower())
    return _CATEGORY_PHRASE.get(key, OTHER_LISTING)


def parse_snippet(title, snippet, query=None):
    """Facts in one search result: listing phrases, countries, and the age at a date."""
    text = snippet or ""
    person = re.split(r"\s+[-–—|]\s+", title or "")[0].strip() if title else ""
    scrub = text
    tokens = set(re.findall(r"[^\W\d_]{2,}", person, re.UNICODE))
    tokens |= set(re.findall(r"[^\W\d_]{2,}", query or "", re.UNICODE))
    for tok in sorted(tokens, key=len, reverse=True):
        scrub = re.sub(rf"(?<![^\W\d_]){re.escape(tok)}(?![^\W\d_])", " ", scrub, flags=re.I | re.UNICODE)
    low = re.sub(r"\s+", " ", text.lower().replace("‑", "-"))
    phrases = [p for p in list(LISTING_PHRASES.values()) + [OTHER_LISTING] if p in low]
    got = age_at_date(text, loose=True)
    return {
        "phrases": phrases,
        "countries": countries_in_text(scrub),
        "age": got[0] if got else None,
        "date": got[1] if got else None,
    }
