"""Name normalisation and string distances shared by the name model and the search index."""
import re
import unicodedata
from functools import lru_cache

_LETTERS = str.maketrans({"ß": "ss", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
                          "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ı": "i", "þ": "th", "ð": "d"})
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_APOSTROPHES = re.compile(r"['’`´]")
_NOT_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)


def fold(s):
    """Lower case, no accents, no bracketed tags, no punctuation; single spaces."""
    s = _BRACKETS.sub(" ", str(s or ""))
    s = unicodedata.normalize("NFKD", s.translate(_LETTERS))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = _APOSTROPHES.sub("", s)
    s = _NOT_WORD.sub(" ", s)
    return " ".join(s.split())


def tokens(name, drop=()):
    """The folded words of a name, in order, without initials; `drop` removes legal forms.

    A name made only of single letters keeps them, so it is never empty.
    """
    words = [t for t in fold(name).split() if t not in drop]
    full = [t for t in words if len(t) > 1]
    return tuple(full or words)


def key(name, drop=()):
    """Order-free form of a name: its folded words, sorted."""
    return " ".join(sorted(tokens(name, drop)))


def is_latin(name):
    """True when every letter of the name folds into the basic Latin alphabet."""
    letters = [c for c in fold(name) if c.isalpha()]
    return bool(letters) and all(c.isascii() for c in letters)


def edit_distance(a, b):
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def edit_ratio(a, b):
    """1 for equal strings, 0 when every character differs."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return 1.0 - edit_distance(a, b) / longest


def jaro(a, b):
    if a == b:
        return 1.0 if a else 0.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    hit_a, hit_b = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not hit_b[j] and b[j] == ca:
                hit_a[i] = hit_b[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    transpositions, j = 0, 0
    for i in range(la):
        if hit_a[i]:
            while not hit_b[j]:
                j += 1
            if a[i] != b[j]:
                transpositions += 1
            j += 1
    transpositions //= 2
    return (matches / la + matches / lb + (matches - transpositions) / matches) / 3.0


@lru_cache(maxsize=500000)
def jaro_winkler(a, b):
    j = jaro(a, b)
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return j + prefix * 0.1 * (1.0 - j)
