"""Partial dates: a year, optionally a month, optionally a day."""
import re
from typing import NamedTuple, Optional

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"]
    )
}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

MONTH_RE = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
)


class PDate(NamedTuple):
    y: int
    m: Optional[int] = None
    d: Optional[int] = None

    def iso(self):
        s = f"{self.y:04d}"
        if self.m:
            s += f"-{self.m:02d}"
            if self.d:
                s += f"-{self.d:02d}"
        return s

    def earliest(self):
        return (self.y, self.m or 1, self.d or 1)

    def latest(self):
        return (self.y, self.m or 12, self.d or 31)


def _valid(y, m=None, d=None):
    if not (1800 <= y <= 2100):
        return None
    if m is not None and not (1 <= m <= 12):
        return None
    if d is not None and not (1 <= d <= 31):
        return None
    if d is not None and m is not None and d > (29 if m == 2 else 30 if m in (4, 6, 9, 11) else 31):
        return None   # 31 February is a misread, not a date
    return PDate(y, m, d)


_PATTERNS = [
    # 1989-02-07, 1989-02, 1989
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), lambda g: (int(g[0]), int(g[1]), int(g[2]))),
    (re.compile(r"^(\d{4})-(\d{1,2})$"), lambda g: (int(g[0]), int(g[1]), None)),
    (re.compile(r"^(\d{4})$"), lambda g: (int(g[0]), None, None)),
    # 19/05/2004 (day first, as the bank writes it)
    (re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$"), lambda g: (int(g[2]), int(g[1]), int(g[0]))),
    # 21 May 1995
    (re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?,?\s+(\d{4})$"),
     lambda g: (int(g[2]), MONTHS.get(g[1].lower()), int(g[0]))),
    # February 19, 2002
    (re.compile(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$"),
     lambda g: (int(g[2]), MONTHS.get(g[0].lower()), int(g[1]))),
    # May 1995
    (re.compile(r"^([A-Za-z]+)\.?,?\s+(\d{4})$"), lambda g: (int(g[1]), MONTHS.get(g[0].lower()), None)),
]


_SLASH = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")


def parse_date(s, day_first=True) -> Optional[PDate]:
    """Parse one date written in any of the formats the data uses. None if unreadable.

    `day_first=True` is how the bank writes 19/05/2004. Watchlist providers write
    dates their own way (6/20/1955), so for their text pass `day_first=None`: a
    numeric date is then read in full only when it can be read one way (one of
    the two numbers is above 12). Otherwise it gives its year only: reading day
    and month on a guess could turn an agreeing date into a contradiction and
    clear a true match.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    s = re.sub(r"T\d{2}:\d{2}.*$", "", s)
    m = _SLASH.match(s)
    if m and day_first is None:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        full = None
        if a > 12 >= b:
            full = _valid(y, b, a)
        elif b > 12 >= a:
            full = _valid(y, a, b)
        return full or _valid(y)
    for rx, fn in _PATTERNS:
        m = rx.match(s)
        if m:
            y, mo, d = fn(m.groups())
            if mo is None and rx.pattern.count("[A-Za-z]"):
                return None
            return _valid(y, mo, d)
    return None


# Dates embedded in running text, most specific first.
_TEXT_DATE = re.compile(
    r"(?P<iso>\b\d{4}-\d{2}-\d{2}\b)"
    r"|(?P<dmy>\b\d{1,2}/\d{1,2}/\d{4}\b)"
    rf"|(?P<d_mon_y>\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_RE}\.?,?\s+\d{{4}}\b)"
    rf"|(?P<mon_d_y>\b{MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b)"
    rf"|(?P<mon_y>\b{MONTH_RE}\.?,?\s+(?:of\s+)?\d{{4}}\b)"
    rf"|(?P<mon_py>\b{MONTH_RE}\s*\(\s*\d{{4}}\s*\))"
    rf"|(?P<y_mon>\b\d{{4}}\s*[-/]\s*{MONTH_RE}\b)"
)


def find_dates(text, day_first=True):
    """All dates in a text, in order, as (PDate, start, end)."""
    out = []
    for m in _TEXT_DATE.finditer(text or ""):
        raw = m.group(0).replace(" of ", " ")
        d = parse_date(raw, day_first)
        if d is None and (m.group("mon_py") or m.group("y_mon")):
            year = re.search(r"\d{4}", raw)
            month = re.search(MONTH_RE, raw)
            d = _valid(int(year.group(0)), MONTHS.get(month.group(0).lower()))
        if d:
            out.append((d, m.start(), m.end()))
    return out


def dob_contradicts(cust: PDate, hit: PDate) -> bool:
    """Policy 6: the birth year is more than 1 year outside, or both give a month
    in the same year and the months (or days) differ."""
    if abs(cust.y - hit.y) > 1:
        return True
    if cust.y == hit.y and cust.m and hit.m:
        if cust.m != hit.m:
            return True
        if cust.d and hit.d and cust.d != hit.d:
            return True
    return False


def dob_agrees(cust: PDate, hit: PDate) -> bool:
    """Policy 7: same birth year and every part both give is equal."""
    if cust.y != hit.y:
        return False
    if cust.m and hit.m and cust.m != hit.m:
        return False
    if cust.m and hit.m and cust.d and hit.d and cust.d != hit.d:
        return False
    return True


def same_in_given_parts(a: PDate, b: PDate) -> bool:
    """Equal in every part both give (used to fit a record to the screened details)."""
    return dob_agrees(a, b)


def max_age_at(birth: PDate, when: PDate) -> int:
    """The oldest the person could have been on a date, given partial dates."""
    by, bm, bd = birth.earliest()
    wy, wm, wd = when.latest()
    age = wy - by
    if (wm, wd) < (bm, bd):
        age -= 1
    return age
