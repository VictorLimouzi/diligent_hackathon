"""Fuzzy customer search: TF-IDF over character n-grams, cosine nearest neighbours.

    CustomerSearch(data).query("Marcus Mueler", k=10)
    -> [{"record_id", "full_name", "dob", "country", "score"}]

Character n-grams are taken inside each word, so word order does not matter and
a typo only changes a few of a name's n-grams. Built once (about a second for
72k records); a query is one sparse matrix-vector product (a few milliseconds).
"""
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from casebook.countries import country as as_country
from casebook.dates import parse_date, same_in_given_parts
from casebook.load import text

from .textutil import fold


class CustomerSearch:
    def __init__(self, data):
        self.ids = sorted(data.customers)
        self.rows = [data.customers[cid] for cid in self.ids]
        docs = [self._doc(c) for c in self.rows]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True,
                                          dtype=np.float32)
        if any(docs):
            # Rows are L2-normalised, so a dot product is the cosine.
            self.matrix = self.vectorizer.fit_transform(docs).tocsr()
        else:
            self.matrix = None

    @staticmethod
    def _doc(c):
        """What a record is found by: its full name, plus a preferred name that adds a word."""
        full = fold(c.get("full_name"))
        words = set(full.split())
        extra = [w for w in fold(c.get("preferred_name")).split() if w not in words]
        return " ".join([full] + extra).strip()

    def _details_fit(self, c, dob, country, city):
        """How many of the given details a record agrees with (used only to order equal names)."""
        n = 0
        if dob:
            d = parse_date(c.get("dob"))
            n += bool(d and same_in_given_parts(dob, d))
        if country:
            n += country in (as_country(c.get("residence_country")), as_country(c.get("nationality")))
        if city:
            n += city in fold(c.get("city")) or city in fold(c.get("address"))
        return n

    def scores(self, q):
        """Cosine of every record's name with `q` (in `self.ids` order), or None for an empty query."""
        qn = fold(q)
        if not qn or self.matrix is None:
            return None
        scores = np.asarray((self.matrix @ self.vectorizer.transform([qn]).T).todense()).ravel()
        return np.round(np.minimum(scores, 1.0).astype(np.float64), 4)

    def query(self, q, k=10, dob=None, country=None, city=None):
        """The k records whose names are closest to `q`, best first.

        Many customers can carry exactly the same name. Records with equal scores
        come in record-id order, unless a date of birth, country or city is given:
        then those that agree with more of them come first. The details never
        outrank a closer name.
        """
        scores = self.scores(q)
        if scores is None or k <= 0:
            return []
        k = min(k, len(self.ids))
        if k < len(scores):
            # everything tied with the k-th score stays in, so the order below decides ties
            kth = np.partition(scores, len(scores) - k)[len(scores) - k]
            top = np.flatnonzero(scores >= kth)
        else:
            top = np.arange(len(scores))
        dob, country, city = parse_date(dob), as_country(country), fold(city)
        if dob or country or city:
            fit = np.array([self._details_fit(self.rows[i], dob, country, city) for i in top])
        else:
            fit = np.zeros(len(top))
        # ids are sorted, so index order is record-id order
        top = top[np.lexsort((top, -fit, -scores[top]))][:k]
        out = []
        for i in top:
            if scores[i] <= 0:
                break
            c = self.rows[i]
            out.append({
                "record_id": self.ids[i],
                "full_name": text(c.get("full_name")),
                "dob": text(c.get("dob")) or None,
                "country": text(c.get("residence_country")) or text(c.get("nationality")) or None,
                "score": round(float(scores[i]), 4),
            })
        return out


def resolver_recall(data, results, search, k=10):
    """For alerts the engine resolved from the screened details alone (no account, login or
    e-mail link): is the engine's record among the top k?

    Three readings, because a name is all the index compares and many customers
    share a name:
      - by name only, ties in record-id order (what `query(q, k)` returns);
      - by name only, counting a record tied with the k-th result as found;
      - by name, with the screened date of birth, country and city ordering equal names.
    """
    alerts = {a.get("alert_id"): a for a in data.alerts}
    engine = {}
    for r in results:
        aid = r.get("alert_id")
        if aid in engine or not r.get("trace"):
            continue
        a = alerts.get(aid, {})
        if a.get("account_ref") or a.get("login_handle") or a.get("email"):
            continue
        records = [c["customer"]["primary"] for c in r["trace"]["candidates"] if c["customer"]["primary"]]
        if records:
            engine[aid] = records
    found = tied = detailed = 0
    missed = []
    position = {cid: i for i, cid in enumerate(search.ids)}
    for aid in sorted(engine):
        a = alerts[aid]
        name = text(a.get("customer_name"))
        wanted = set(engine[aid])
        top = search.query(name, k)
        if wanted & {t["record_id"] for t in top}:
            found += 1
            tied += 1
        elif top:
            scores = search.scores(name)
            tied += any(scores[position[r]] >= top[-1]["score"] for r in wanted if r in position)
        with_details = search.query(name, k, dob=a.get("dob"), country=a.get("country"), city=a.get("city"))
        if wanted & {t["record_id"] for t in with_details}:
            detailed += 1
        else:
            missed.append(aid)
    total = len(engine)
    share = lambda n: round(n / total, 4) if total else None
    return {
        "resolver_alerts_without_link": total,
        "resolver_recall_at_10": share(found),
        "resolver_recall_at_10_counting_ties": share(tied),
        "resolver_recall_at_10_with_screened_details": share(detailed),
        "resolver_missed_with_details": missed[:20],
    }
