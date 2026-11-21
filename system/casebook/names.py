"""Name comparison, as policy principle 8 sets it out."""
import re
import unicodedata
from functools import lru_cache

PLACEHOLDERS = {
    "na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbc", "tbd",
    "unk", "xx", "xxxx", "testing", "tester", "dummy", "notknown", "notapplicable",
    "noname", "nan", "undefined", "anonymous", "anon",
}

DEFAULT_LEGAL_FORMS = {
    "ab", "ag", "as", "asa", "bv", "co", "company", "corp", "corporation", "gmbh",
    "inc", "kg", "limited", "llc", "llp", "lp", "ltd", "na", "nv", "oy", "oyj",
    "plc", "sa", "sarl", "sas", "se", "sf", "sl", "spa", "srl",
}


class NameRules:
    """Variant groups and legal forms from name_variants.json."""

    def __init__(self, groups=None, legal_forms=None):
        self.groups = {}
        for i, g in enumerate(groups or []):
            for form in g:
                self.groups.setdefault(_fold(form), set()).add(i)
        self.legal_forms = {_fold(f) for f in (legal_forms or DEFAULT_LEGAL_FORMS)}

    def variants(self, a, b):
        ga = self.groups.get(a)
        gb = self.groups.get(b)
        return bool(ga and gb and (ga & gb))


def _fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("ł", "l").replace("Ł", "L")
    s = s.replace("đ", "d").replace("Đ", "D").replace("æ", "ae").replace("Æ", "AE").replace("ı", "i")
    return s.casefold()


def name_parts(name, company=False, rules=None):
    """The real parts of a name: no brackets, accents, punctuation, initials or placeholders.

    Order is kept (needed for parts written together or apart); each distinct part counts once.
    """
    if not name:
        return ()
    s = str(name)
    # Placeholders written with punctuation, before punctuation is dropped.
    s = re.sub(r"(?i)\bn\s*/\s*a\b", " ", s)
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", s)
    s = _fold(s)
    s = re.sub(r"['’`´]", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s, flags=re.UNICODE)
    forms = (rules.legal_forms if rules else DEFAULT_LEGAL_FORMS) if company else ()
    out = []
    for tok in s.split():
        if len(tok) < 2:
            continue  # single letters are initials
        if any(ch.isdigit() for ch in tok):
            continue
        if not company and tok in PLACEHOLDERS:
            continue
        if company and (tok in forms or tok in PLACEHOLDERS - {"na"}):
            continue
        if tok not in out:
            out.append(tok)
    return tuple(out)


def is_latin(parts):
    """True when the parts are written in Latin letters (accents already folded away)."""
    return any(all(ord(ch) < 0x250 for ch in p) for p in parts or ())


@lru_cache(maxsize=200000)
def _edit_distance(a, b, limit):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            if v < best:
                best = v
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def parts_match(a, b, rules=None):
    """Two parts match when identical, listed variants, or within the edit allowance."""
    if a == b:
        return True
    if rules and rules.variants(a, b):
        return True
    shorter = min(len(a), len(b))
    if shorter < 4:
        return False
    allowed = 1 if shorter <= 6 else 2
    return _edit_distance(a, b, allowed) <= allowed


def names_agree(parts_a, parts_b, rules=None):
    """At least two parts match, or every part of the shorter name matches.

    Two adjacent parts of one name written together in the other count as one
    part that covers both, so a joined match shortens the name it joins.
    """
    pa, pb = tuple(parts_a or ()), tuple(parts_b or ())
    if not pa or not pb:
        return False
    na, nb = len(pa), len(pb)

    def options(i, used_b):
        for j in range(nb):
            if j in used_b:
                continue
            if parts_match(pa[i], pb[j], rules):
                yield 1, (j,)
                continue
            # a[i] and a[i+1] are written together as b[j]
            if i + 1 < na and parts_match(pa[i] + pa[i + 1], pb[j], rules):
                yield 2, (j,)
            # a[i] is b[j] and b[j+1] written together
            if j + 1 < nb and (j + 1) not in used_b and parts_match(pa[i], pb[j] + pb[j + 1], rules):
                yield 1, (j, j + 1)

    def go(i, used_b, matches, len_a, len_b):
        if matches >= 2:
            return True
        if i >= na:
            return matches >= 1 and matches >= min(len_a, len_b)
        for step, js in options(i, used_b):
            if go(i + step, used_b | set(js), matches + 1, len_a - (step - 1), len_b - (len(js) - 1)):
                return True
        return go(i + 1, used_b, matches, len_a, len_b)

    return go(0, frozenset(), 0, na, nb)


def split_aliases(aliases):
    """A hit's aliases field: separated by ';', each possibly tagged in brackets."""
    if not aliases:
        return []
    if isinstance(aliases, (list, tuple)):
        items = aliases
    else:
        items = str(aliases).split(";")
    return [a.strip() for a in items if a and str(a).strip()]
