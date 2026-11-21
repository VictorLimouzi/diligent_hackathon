"""Seeded, independently replayable representation changes and malformed probes.

No identity fields are invented in the representation suite. Malformed probes
are deliberately separate: loss of evidence is *not* meaning-preserving.
"""
import copy
import datetime as dt
import json
import random
import re
import shutil
import unicodedata as ud
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


IDS = ("record_id", "hit_id", "check_id", "note_id", "result_id",
       "account_id", "applicant_id", "customer_id", "alert_id")
MULTI = {"hit_ids", "aliases", "dobs", "countries", "addresses", "categories"}
NAMES = {"customer_name", "full_name", "preferred_name", "verified_name", "name", "aliases"}
DATES = {"customers": {"dob", "onboarded_at", "last_kyc_refresh"},
         "kyc_checks": {"verified_dob", "checked_at"},
         "alerts": {"dob", "screened_at"}}
TEXT_FIELDS = NAMES | MULTI | set().union(*DATES.values()) | {
    "entity_type", "country", "city", "residence_country", "nationality", "citizenship",
    "document_country", "place_of_birth", "primary_category", "address", "deceased_date",
    "text", "snippet", "title", "profile_notes", "query", "result", "channel", "document_type",
    "position_or_allegation", "details", "relationships", "occupation", "employer", "provider"}
OPTIONAL = {"alerts": {"dob", "country", "city", "account_ref", "login_handle", "email"},
            "customers": {"preferred_name", "login_name", "email", "phone", "nationality",
                          "residence_country", "city", "address", "dob"},
            "hits": MULTI - {"hit_ids"} | {"deceased_date", "profile_notes", "primary_category"},
            "kyc_checks": {"verified_name", "verified_dob", "document_country", "checked_at"}}
MONTHS = ("January February March April May June July August September October November December").split()

# Explicit equivalences, independent of the engine's country normalizer.
COUNTRY_GROUPS = [s.split("|") for s in """
Germany|DE|German
Nigeria|NG|Nigerian
United Kingdom|GB|British
United States|US|American
France|FR|French
Spain|ES|Spanish
Italy|IT|Italian
Portugal|PT|Portuguese
Sweden|SE|Swedish
Norway|NO|Norwegian
Finland|FI|Finnish
Denmark|DK|Danish
Netherlands|NL|Dutch
Belgium|BE|Belgian
Austria|AT|Austrian
Switzerland|CH|Swiss
Poland|PL|Polish
Romania|RO|Romanian
Serbia|RS|Serbian
Greece|GR|Greek
Ireland|IE|Irish
Russia|RU|Russian
Ukraine|UA|Ukrainian
China|CN|Chinese
India|IN|Indian
Pakistan|PK|Pakistani
Bangladesh|BD|Bangladeshi
Japan|JP|Japanese
Singapore|SG|Singaporean
Malaysia|MY|Malaysian
Indonesia|ID|Indonesian
Thailand|TH|Thai
Australia|AU|Australian
New Zealand|NZ|New Zealander
Canada|CA|Canadian
Brazil|BR|Brazilian
Argentina|AR|Argentine
Mexico|MX|Mexican
Colombia|CO|Colombian
Peru|PE|Peruvian
Chile|CL|Chilean
South Africa|ZA|South African
Kenya|KE|Kenyan
Ghana|GH|Ghanaian
Egypt|EG|Egyptian
Morocco|MA|Moroccan
Algeria|DZ|Algerian
Tunisia|TN|Tunisian
Turkey|TR|Turkish
Israel|IL|Israeli
Saudi Arabia|SA|Saudi
United Arab Emirates|AE|Emirati
Iran|IR|Iranian
Iraq|IQ|Iraqi
Lebanon|LB|Lebanese
Jordan|JO|Jordanian
""".strip().splitlines()]
COUNTRIES = {v.casefold(): group for group in COUNTRY_GROUPS for v in group}


def record_id(row, index):
    return str(next((row[k] for k in IDS if row.get(k) is not None), f"row:{index + 1}"))


def values(value):
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [v.strip() for v in str(value).split(";") if v.strip()]


@lru_cache(maxsize=100000)
def full_date(value):
    """Strict, independent parser; do not turn a partial date into a full date."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    # Preserve time-of-day information by leaving timestamp values untouched.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def date_form(value, form):
    d = full_date(value)
    if d is None:
        return value
    return (d.isoformat(), f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{d.day} {MONTHS[d.month - 1]} {d.year}",
            f"{MONTHS[d.month - 1]} {d.day}, {d.year}")[form]


def fold(value):
    return "".join(c for c in ud.normalize("NFD", value) if not ud.combining(c)).casefold()


def name_noise(value, mode, rng, groups=()):
    if mode == "case":
        return value.upper() if value != value.upper() else value.lower()
    if mode == "accents_strip":
        return "".join(c for c in ud.normalize("NFD", value) if not ud.combining(c))
    if mode == "accents_add":
        accents = str.maketrans("aeiouAEIOU", "áéíóúÁÉÍÓÚ")
        return ud.normalize("NFC", value.translate(accents))
    if mode == "hyphens":
        # Only letter-to-letter separators; brackets and alias boundaries stay intact.
        return re.sub(r"(?<=[^\W\d_])[- ](?=[^\W\d_])",
                      lambda m: " " if m[0] == "-" else "-", value)
    if mode == "order":
        # Do not move tokens across a bracketed nickname or punctuation boundary.
        if not re.fullmatch(r"[^\W\d_]+(?: [^\W\d_]+)+", value, re.UNICODE):
            return value
        parts = value.split()
        return parts[-1].upper() + ", " + " ".join(parts[:-1])
    if mode == "variants":
        def replace(m):
            token = fold(m[0])
            if isinstance(groups, dict):
                choices = groups.get(token, ())
            else:
                choices = sorted({v for g in groups if token in {fold(x) for x in g}
                                  for v in g if fold(v) != token})
            return rng.choice(choices) if choices else m[0]
        # Bracketed text is retained verbatim, and legal forms are not variant groups.
        return "".join(part if part.startswith(("(", "[", "{")) else
                       re.sub(r"[^\W\d_]+", replace, part)
                       for part in re.split(r"(\([^)]*\)|\[[^]]*\]|\{[^}]*\})", value))
    raise ValueError(mode)


def rewrap(value):
    """Retain sentence and paragraph boundaries and English month capitalization."""
    return re.sub(r"(?<=\S)[ \t]+(?=\S)", "  ", value).rstrip().removesuffix(".")


@dataclass
class Mutation:
    file: str
    index: int
    record_id: str
    field: str
    before: object
    after: object
    missing: bool = False
    delete: bool = False

    def json(self):
        return vars(self).copy()


@dataclass
class Experiment:
    name: str
    kind: str = "representation"
    mutations: tuple = ()
    order: dict = None
    renames: dict = None
    remove: tuple = ()
    add: dict = None
    affected_hits: tuple = ()
    skip: str = None


class Corpus:
    """Source data is read-only. Every experiment writes an independent temp copy."""
    def __init__(self, folder):
        from casebook.load import _find_files
        self.folder = Path(folder).resolve()
        self.files = {table: Path(path).name for table, path in _find_files(self.folder).items()}
        self.rows = {table: [json.loads(line) for line in (self.folder / filename).read_text().splitlines()
                             if line.strip()] for table, filename in self.files.items()}
        self.rules_files = sorted(p.name for p in self.folder.iterdir()
                                  if "variant" in p.name.lower() and p.suffix.lower() == ".json")
        self.rules_path = self.folder / self.rules_files[-1] if self.rules_files else (
            Path(__file__).resolve().parents[1] / "casebook/name_variants_default.json")
        self.rules = json.loads(self.rules_path.read_text())

    def write(self, folder, experiment):
        folder = Path(folder)
        for path in self.folder.iterdir():
            if path.is_file():
                shutil.copyfile(path, folder / path.name)
        changed = {}
        for m in experiment.mutations:
            changed.setdefault(m.file, {})
            if m.index not in changed[m.file]:
                table = next(t for t, f in self.files.items() if f == m.file)
                changed[m.file][m.index] = copy.deepcopy(self.rows[table][m.index])
            row = changed[m.file][m.index]
            if record_id(row, m.index) != m.record_id:
                raise ValueError(f"replay record differs at {m.file}:{m.index + 1}")
            if m.field != "__key_order__" and (row.get(m.field) != m.before or (m.field not in row) != m.missing):
                raise ValueError(f"replay field differs: {m.record_id}.{m.field}")
            if m.field == "__key_order__":
                changed[m.file][m.index] = {k: row[k] for k in m.after}
            elif m.delete:
                row.pop(m.field, None)
            else:
                row[m.field] = m.after
        for table, filename in self.files.items():
            if filename not in changed and table not in (experiment.order or {}):
                continue
            order = (experiment.order or {}).get(table, range(len(self.rows[table])))
            with (folder / filename).open("w", encoding="utf-8") as f:
                for i in order:
                    f.write(json.dumps(changed.get(filename, {}).get(i, self.rows[table][i]),
                                       ensure_ascii=False) + "\n")
        for source, dest in (experiment.renames or {}).items():
            (folder / source).rename(folder / dest)
        for filename in experiment.remove:
            (folder / filename).unlink()
        for filename, content in (experiment.add or {}).items():
            (folder / filename).write_text(content, encoding="utf-8")

    def mutation(self, table, i, key, after=None, delete=False):
        row = self.rows[table][i]
        return Mutation(self.files[table], i, record_id(row, i), key, row.get(key), after,
                        key not in row, delete)


def groups_from(raw):
    """The documented list-of-lists variant format, excluding company suffixes."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name_variants"), list):
        return []
    return [g for g in raw["name_variants"] if isinstance(g, list)
            and all(isinstance(v, str) for v in g)]


REPRESENTATIONS = (
    "dates_iso", "dates_slash", "dates_day_month", "dates_month_day",
    "country_codes", "country_nationalities", "names_case", "names_accents_strip",
    "names_accents_add", "names_hyphens", "names_order", "names_variants",
    "shape_lists", "shape_numbers", "shape_empty", "shape_missing", "shape_keys",
    "shape_rows", "filenames_short", "filenames_long", "rules_absent", "rules_present",
    "filenames_short_rules_absent", "filenames_long_rules_absent",
    "whitespace_edges", "whitespace_nbsp", "unicode_apostrophes", "unicode_nfc",
    "unicode_nfd", "text_rewrap",
)
CRASHES = ("crash_missing_hit_ids", "crash_unknown_hit", "crash_null_names",
           "crash_invalid_dates", "crash_missing_account")


def representation(corpus, name, seed, record_ids=None):
    rng = random.Random(f"{seed}:{name}")
    edits, order = [], {}
    groups = {}
    for group in groups_from(corpus.rules):
        for token in group:
            groups.setdefault(fold(token), set()).update(v for v in group if fold(v) != fold(token))
    groups = {k: sorted(v) for k, v in groups.items()}
    if name == "shape_rows":
        for table, rows in sorted(corpus.rows.items()):
            indices = list(range(len(rows)))
            rng.shuffle(indices)
            order[table] = indices
        return Experiment(name, order=order)
    if name.startswith("filenames_"):
        short = name.startswith("filenames_short")
        renames = {}
        for table, base in (("kyc_checks", "kyc"), ("search_results", "search")):
            if table in corpus.files:
                target = (base if short else table) + ".jsonl"
                if corpus.files[table] != target:
                    if (corpus.folder / target).exists():
                        return Experiment(name, skip=f"rename would overwrite {target}")
                    renames[corpus.files[table]] = target
        without_rules = representation(corpus, "rules_absent", seed) if name.endswith("rules_absent") else None
        return Experiment(name, renames=renames,
                          remove=without_rules.remove if without_rules else (),
                          skip=without_rules.skip if without_rules else None)
    if name.startswith("rules_"):
        if name == "rules_present":
            return Experiment(name, add={} if corpus.rules_files else
                              {"name_variants.json": corpus.rules_path.read_text()})
        # The absence test is invalid if it would change the actual policy rules.
        from casebook.load import _load_rules
        import tempfile
        with tempfile.TemporaryDirectory(prefix="casebook-rules-") as empty:
            default = _load_rules(empty)
        actual = _load_rules(corpus.folder)
        if actual.groups != default.groups or actual.legal_forms != default.legal_forms:
            return Experiment(name, skip="supplied and fallback rules differ; removal changes semantics")
        return Experiment(name, remove=tuple(corpus.rules_files))
    for table, rows in sorted(corpus.rows.items()):
        if table == "practice_labels":
            continue
        for i, row in enumerate(rows):
            if record_ids is not None and record_id(row, i) not in record_ids:
                continue
            if name == "shape_keys":
                keys = list(row)
                rng.shuffle(keys)
                if keys != list(row):
                    # A synthetic whole-row change, handled as an order below.
                    edits.append(corpus.mutation(table, i, "__key_order__", keys))
                continue
            if name == "shape_empty":
                for key in sorted(OPTIONAL.get(table, ()) - row.keys()):
                    edits.append(corpus.mutation(table, i, key, ""))
            for key, before in sorted(row.items()):
                after = before
                delete = False
                if name.startswith("dates_") and key in DATES.get(table, ()):
                    after = date_form(before, REPRESENTATIONS.index(name))
                elif name.startswith("country_") and table == "customers" and key in {"residence_country", "nationality"}:
                    group = COUNTRIES.get(str(before).strip().casefold())
                    if group:
                        after = group[1 if name == "country_codes" else 2]
                elif name.startswith("names_") and key in NAMES and isinstance(before, (str, list)):
                    mode = name.removeprefix("names_")
                    after = ([name_noise(v, mode, rng, groups) for v in before] if isinstance(before, list)
                             else "; ".join(name_noise(v, mode, rng, groups) for v in before.split(";")))
                elif name == "shape_lists" and key in MULTI and isinstance(before, str):
                    after = values(before)
                elif name == "shape_numbers" and key in {"match_score", "hit_count"} and isinstance(before, str):
                    if re.fullmatch(r"\d+(?:\.\d+)?", before):
                        after = float(before) if "." in before else int(before)
                elif name == "shape_empty" and before is None and key in OPTIONAL.get(table, ()):
                    after = ""
                elif name == "shape_missing" and (before is None or before == "") and key in OPTIONAL.get(table, ()):
                    delete = True
                elif isinstance(before, str) and key in TEXT_FIELDS and key != "hit_ids":
                    if name == "whitespace_edges" and before:
                        after = "  " + before + "  "
                    elif name == "whitespace_nbsp":
                        after = before.replace(" ", "\u00a0")
                    elif name == "unicode_apostrophes":
                        after = before.replace("'", "’")
                    elif name == "unicode_nfc":
                        after = ud.normalize("NFC", before)
                    elif name == "unicode_nfd":
                        after = ud.normalize("NFD", before)
                    elif name == "text_rewrap" and table in {"notes", "search_results"} and key in {"text", "snippet"}:
                        after = rewrap(before)
                if delete or after != before:
                    edits.append(corpus.mutation(table, i, key, after, delete))
    return Experiment(name, mutations=tuple(edits), order=order)


def malformed(corpus, name, seed):
    """One alert per malformed probe, chosen reproducibly; require REVIEW output.

    Related customer, latest KYC, and profile fields are intentionally probed
    together for null/invalid sweeps. Single-field minimization is a separate step.
    """
    from casebook.load import as_list
    rng = random.Random(f"{seed}:{name}")
    eligible = [(i, a) for i, a in enumerate(corpus.rows["alerts"]) if as_list(a.get("hit_ids"))]
    if name == "crash_missing_account":
        eligible = [(i, a) for i, a in eligible if a.get("account_ref")]
    if not eligible:
        return Experiment(name, kind="malformed", skip="no eligible alert")
    i, alert = rng.choice(eligible)
    affected = tuple(as_list(alert["hit_ids"]))
    edits = []
    if name == "crash_missing_hit_ids":
        edits.append(corpus.mutation("alerts", i, "hit_ids", delete=True))
    elif name == "crash_unknown_hit":
        unknown = "H-EVAL-UNKNOWN"
        while any(h.get("hit_id") == unknown for h in corpus.rows["hits"]):
            unknown += "X"
        edits.append(corpus.mutation("alerts", i, "hit_ids", "; ".join(affected + (unknown,))))
        affected = (unknown,)
    elif name == "crash_missing_account":
        unknown = "ACC-EVAL-MISSING"
        while any(a.get("account_id") == unknown for a in corpus.rows.get("accounts", [])):
            unknown += "X"
        edits.append(corpus.mutation("alerts", i, "account_ref", unknown))
    else:
        if name == "crash_null_names":
            edits.append(corpus.mutation("alerts", i, "customer_name", None))
            field, value = "name", None
        elif name == "crash_invalid_dates":
            edits.extend(corpus.mutation("alerts", i, k, "0000-00-00") for k in ("dob", "screened_at"))
            field, value = "dobs", "0000-00-00"
        else:
            raise ValueError(name)
        for j, hit in enumerate(corpus.rows["hits"]):
            if hit.get("hit_id") in affected:
                edits.append(corpus.mutation("hits", j, field, value))
    return Experiment(name, kind="malformed", mutations=tuple(edits), affected_hits=affected)


def make_experiment(corpus, name, seed=0, record_ids=None):
    if name in REPRESENTATIONS:
        return representation(corpus, name, seed, record_ids)
    if name in CRASHES:
        return malformed(corpus, name, seed)
    raise ValueError(f"unknown experiment: {name}")
