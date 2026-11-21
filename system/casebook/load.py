"""Read a data folder (JSON Lines files) and index it for lookups."""
import json
import os
import re
import unicodedata
from collections import defaultdict

from .dates import parse_date
from .names import NameRules, name_parts

# table -> words that identify its file name, most specific first
_FILES = [
    ("practice_labels", ("label",)),
    ("search_results", ("search",)),
    ("kyc_checks", ("kyc",)),
    ("onboarding", ("onboard",)),
    ("accounts", ("account",)),
    ("customers", ("customer",)),
    ("alerts", ("alert",)),
    ("hits", ("hit",)),
    ("notes", ("note",)),
]


def as_list(v):
    """A multi-valued field: a JSON list, or a string separated by ';'."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return [x.strip() for x in str(v).split(";") if x.strip()]


def text(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v if x is not None)
    return str(v).strip()


_SPACES = dict.fromkeys(map(ord, "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
                                 "\u200a\u202f\u205f\u3000"), " ")
_SPACES.update(dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None))


def clean(value):
    """Typographic noise out of every string: odd spaces become plain spaces,
    zero-width characters go, and composed and decomposed accents read the same."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.translate(_SPACES))
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def _read_jsonl(path):
    """Rows of a JSON Lines file. A line that is not valid JSON is skipped, not fatal."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(clean(row))
    return rows


def _find_files(folder):
    found = {}
    names = sorted(os.listdir(folder))
    for fn in names:
        low = fn.lower()
        if not low.endswith((".jsonl", ".ndjson")):
            continue
        for table, words in _FILES:
            if table not in found and any(w in low for w in words):
                found[table] = os.path.join(folder, fn)
                break
    return found


def _load_rules(folder):
    path = None
    for fn in os.listdir(folder):
        if "variant" in fn.lower() and fn.lower().endswith(".json"):
            path = os.path.join(folder, fn)
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "name_variants_default.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    groups, forms = [], []

    def walk(node, key=""):
        nonlocal forms
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                    if re.search(r"legal|form|compan|suffix", k, re.I):
                        forms = forms + list(v)
                    else:
                        # "muhammad": ["mohammed", ...] style, or a named group
                        groups.append(([k] if not re.search(r"variant|group|name", k, re.I) else []) + list(v))
                else:
                    walk(v, k)
        elif isinstance(node, list):
            if node and all(isinstance(x, str) for x in node):
                if re.search(r"legal|form|compan|suffix", key, re.I):
                    forms = forms + list(node)
                else:
                    groups.append(list(node))
            else:
                for x in node:
                    walk(x, key)

    walk(raw)
    return NameRules(groups, forms or None)


def phone_key(p):
    digits = re.sub(r"\D", "", str(p or ""))
    return digits or None


def name_keys(name):
    """Order-free keys for a name, used to look up search results by query.

    Search queries are the name in lower case without initials, and letters
    outside the basic alphabet may be transliterated or dropped, so a name
    gives a few candidate keys.
    """
    import unicodedata

    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(name or ""))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    translit = s.translate(str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ı": "i", "ß": "ss", "æ": "ae", "œ": "oe", "þ": "th", "ð": "d"}))
    dropped = "".join(c if ord(c) < 128 else "" for c in s)
    keys = set()
    for v in (s, translit, dropped):
        for apos in ("", " "):
            t = re.sub(r"['’`´]", apos, v)
            toks = [x for x in re.findall(r"[^\W\d_]+", t, re.UNICODE) if len(x) > 1]
            if toks:
                keys.add(" ".join(sorted(toks)))
    return keys


def name_key(name):
    keys = sorted(name_keys(name))
    return keys[0] if keys else ""


def _normalise_alert(alert):
    """Alerts carry the customer as screened in a nested `customer` object
    (name, entity_type, dob, country, city). Flatten it; a flat alert passes through."""
    cust = alert.get("customer")
    if not isinstance(cust, dict):
        if "customer_name" not in alert and isinstance(cust, str):
            alert = {**alert, "customer_name": cust}
        return alert
    flat = {k: v for k, v in alert.items() if k != "customer"}
    name = cust.get("name") or cust.get("customer_name") or cust.get("full_name")
    for key, value in (("customer_name", name), ("entity_type", cust.get("entity_type")),
                       ("dob", cust.get("dob")), ("country", cust.get("country")), ("city", cust.get("city"))):
        if flat.get(key) in (None, ""):
            flat[key] = value
    return flat


class Data:
    def __init__(self, folder):
        self.folder = folder
        files = _find_files(folder)
        missing = [t for t in ("alerts", "hits", "customers") if t not in files]
        if missing:
            raise FileNotFoundError(f"{folder}: no JSON Lines file for {', '.join(missing)}")
        self.files = files
        self.rules = _load_rules(folder)
        get = lambda t: _read_jsonl(files[t]) if t in files else []
        self.alerts = [_normalise_alert(a) for a in get("alerts") if isinstance(a, dict)]
        self.hits = {h["hit_id"]: h for h in get("hits") if isinstance(h, dict) and h.get("hit_id")}
        self.customers = {c["record_id"]: c for c in get("customers") if isinstance(c, dict) and c.get("record_id")}
        self.labels = get("practice_labels")
        self.hits_by_alert = defaultdict(list)
        for hid, h in self.hits.items():
            if h.get("alert_id"):
                self.hits_by_alert[h["alert_id"]].append(hid)

        self.cust_by_email = defaultdict(list)
        self.cust_dob = {}
        self.cust_by_year = defaultdict(list)
        for cid, c in self.customers.items():
            if c.get("email"):
                self.cust_by_email[text(c["email"]).lower()].append(cid)
            d = parse_date(c.get("dob"))
            self.cust_dob[cid] = d
            self.cust_by_year[d.y if d else None].append(cid)

        self.acc_by_id = defaultdict(list)
        self.acc_by_login = defaultdict(list)
        self.acc_by_cust = defaultdict(list)
        for a in get("accounts"):
            if a.get("account_id"):
                self.acc_by_id[text(a["account_id"])].append(a)
            if a.get("login_handle"):
                self.acc_by_login[text(a["login_handle"]).lower()].append(a)
            if a.get("customer_id"):
                self.acc_by_cust[a["customer_id"]].append(a)

        self.onb_by_cust = defaultdict(list)
        for o in get("onboarding"):
            if o.get("customer_id"):
                self.onb_by_cust[o["customer_id"]].append(o)

        self.by_id = {}   # any cited record, for showing the source behind a decision
        self.kyc_by_app = defaultdict(list)
        for k in get("kyc_checks"):
            if k.get("applicant_id"):
                self.kyc_by_app[k["applicant_id"]].append(k)
            if k.get("check_id"):
                self.by_id[k["check_id"]] = k

        self.notes_by_email = defaultdict(list)
        for n in get("notes"):
            if n.get("requester_email"):
                self.notes_by_email[text(n["requester_email"]).lower()].append(n)
            if n.get("note_id"):
                self.by_id[n["note_id"]] = n

        self.search_by_name = defaultdict(list)
        for s in get("search_results"):
            for k in name_keys(s.get("query") or ""):
                self.search_by_name[k].append(s)
            if s.get("result_id"):
                self.by_id[s["result_id"]] = s
        self.by_id.update(self.customers)

        self._cluster()
        self._parts_cache = {}

    # ------------------------------------------------------------ persons

    def _cluster(self):
        """One person, one picture (policy 3): same phone and full date of birth,
        or the same document number on approved checks; links are transitive."""
        parent = {cid: cid for cid in self.customers}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                if ra > rb:
                    ra, rb = rb, ra
                parent[rb] = ra

        by_phone_dob = {}
        for cid, c in self.customers.items():
            d = self.cust_dob.get(cid)
            if not (d and d.m and d.d):
                continue
            phones = {phone_key(c.get("phone"))}
            phones |= {phone_key(a.get("phone")) for a in self.acc_by_cust.get(cid, [])}
            for p in phones:
                if not p:
                    continue
                k = (p, d)
                if k in by_phone_dob:
                    union(cid, by_phone_dob[k])
                else:
                    by_phone_dob[k] = cid

        by_doc = {}
        for cid in self.customers:
            for o in self.onb_by_cust.get(cid, []):
                for k in self.kyc_by_app.get(o.get("kyc_applicant_id"), []):
                    if text(k.get("result")).lower() != "approved":
                        continue
                    num = re.sub(r"\s+", "", text(k.get("document_number"))).upper()
                    if not num:
                        continue
                    if num in by_doc:
                        union(cid, by_doc[num])
                    else:
                        by_doc[num] = cid

        members = defaultdict(list)
        for cid in self.customers:
            members[find(cid)].append(cid)
        self.person_of = {}
        for root, ids in members.items():
            ids = tuple(sorted(ids))
            for cid in ids:
                self.person_of[cid] = ids

    def person(self, cid):
        return self.person_of.get(cid, (cid,))

    def search_results(self, name):
        """Search results whose query is this name."""
        out, seen = [], set()
        for k in sorted(name_keys(name)):
            for s in self.search_by_name.get(k, []):
                if s.get("result_id") not in seen:
                    seen.add(s.get("result_id"))
                    out.append(s)
        return out

    def alert_hit_ids(self, alert):
        """The alert's hits: those it lists, plus any hit that names this alert itself."""
        ids = as_list(alert.get("hit_ids"))
        for hid in self.hits_by_alert.get(alert.get("alert_id"), []):
            if hid not in ids:
                ids.append(hid)
        return ids

    def parts(self, name, company=False):
        k = (name, company)
        if k not in self._parts_cache:
            self._parts_cache[k] = name_parts(name, company, self.rules)
        return self._parts_cache[k]
