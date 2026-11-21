"""Learned name similarity: string-distance features into a calibrated logistic regression.

The pairs come from the data itself (no human labels): names the records say
belong to one person or one listed entity are positives; names of different
people are negatives, half of them chosen to share exactly one word.
The model scores how alike two names are written. It never decides a hit.
"""
import random
from collections import defaultdict

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from casebook.load import text
from casebook.names import split_aliases

from . import SEED
from .textutil import edit_ratio, is_latin, jaro_winkler, key, tokens

FEATURES = [
    "char_ngram_cosine",     # TF-IDF cosine over character 2- and 3-grams
    "edit_ratio",            # 1 - edit distance / longer length, after lining the words up
    "jaro_winkler",          # after lining the words up
    "token_overlap",         # token-set overlap: words that match closely or as listed variants / all words
    "token_soft",            # mean similarity of the shorter name's words to their partners
    "shorter_covered",       # share of the shorter name's words that have a close partner
    "variant_words",         # share of the shorter name's words matched only through a listed variant
    "length_gap",            # difference in length / longer length
    "word_count_gap",        # difference in number of words / larger number
]
_CLOSE = 0.92                # Jaro-Winkler from which two words count as a close match
_MIN_PAIRS = 40              # below this the model is not trained and scores fall back to a blend


def _unique(words):
    return tuple(dict.fromkeys(words))


def _line_up(short, long_, rules):
    """Pair each word of the shorter name with one word of the longer, best pairs first.

    Returns [(word, partner, similarity, listed_variant)] in the shorter name's order.
    """
    scored = []
    for i, w in enumerate(short):
        for j, v in enumerate(long_):
            listed = w != v and bool(rules) and rules.variants(w, v)
            scored.append((1.0 if listed else jaro_winkler(w, v), listed, i, j))
    scored.sort(key=lambda t: (-t[0], t[2], t[3]))
    partner, used = {}, set()
    for sim, listed, i, j in scored:
        if i not in partner and j not in used:
            partner[i] = (j, sim, listed)
            used.add(j)
    return [(short[i], long_[partner[i][0]], partner[i][1], partner[i][2]) for i in range(len(short))]


def _string_features(ta, tb, rules):
    """Every feature but the first, for two token tuples."""
    ta, tb = _unique(ta), _unique(tb)
    if not ta or not tb:
        return [0.0] * (len(FEATURES) - 1)
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    pairs = _line_up(short, long_, rules)
    partners = [v for _, v, _, _ in pairs]
    left = " ".join(short)
    right = " ".join(partners + [v for v in long_ if v not in partners])
    close = sum(1 for _, _, sim, _ in pairs if sim >= _CLOSE)
    n = len(short)
    longest = max(len(left), len(right))
    return [
        edit_ratio(left, right),
        jaro_winkler(left, right),
        close / (len(ta) + len(tb) - close),
        sum(sim for _, _, sim, _ in pairs) / n,
        close / n,
        sum(1 for _, _, _, listed in pairs if listed) / n,
        abs(len(left) - len(right)) / longest,
        (len(long_) - n) / len(long_),
    ]


class NameModel:
    """score(pairs) -> probability that two names are the same name written differently."""

    def __init__(self, rules=None, seed=SEED):
        self.rules = rules
        self.seed = seed
        self.vectorizer = None
        self.clf = None
        self.trained = False

    def _drop(self, company):
        return self.rules.legal_forms if (company and self.rules) else ()

    def _features(self, pairs, fit=False):
        toks = [(tokens(a, self._drop(c)), tokens(b, self._drop(c))) for a, b, c in pairs]
        left = [" ".join(ta) for ta, _ in toks]
        right = [" ".join(tb) for _, tb in toks]
        if fit:
            self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), sublinear_tf=True,
                                              dtype=np.float64)
            self.vectorizer.fit(sorted(set(left + right) - {""}) or ["a"])
        cos = np.asarray(self.vectorizer.transform(left).multiply(self.vectorizer.transform(right)).sum(axis=1)).ravel()
        rest = np.array([_string_features(ta, tb, self.rules) for ta, tb in toks], dtype=float).reshape(len(toks), -1)
        return np.column_stack([cos, rest])

    def fit(self, pairs, labels):
        """pairs: [(name_a, name_b, company)], labels: 1 same name, 0 different."""
        y = np.asarray(labels, dtype=int)
        X = self._features(pairs, fit=True)
        folds = min(5, int(np.bincount(y, minlength=2).min()))
        if len(y) < _MIN_PAIRS or folds < 2:
            self.trained = False    # too little data: score() blends the raw features instead
            return self
        base = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))
        cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=self.seed)
        self.clf = CalibratedClassifierCV(base, method="sigmoid", cv=cv)
        self.clf.fit(X, y)
        self.trained = True
        return self

    def score(self, pairs):
        if not pairs:
            return np.zeros(0)
        X = self._features(pairs)
        if not self.trained:
            # Plain average of cosine, edit ratio, Jaro-Winkler and soft word match.
            return np.clip(X[:, [0, 1, 2, 4]].mean(axis=1), 0.0, 1.0)
        return self.clf.predict_proba(X)[:, 1]

    def coefficients(self):
        """Mean standardised coefficient per feature over the calibration folds."""
        if not self.trained:
            return {}
        coefs = np.mean([c.estimator[-1].coef_[0] for c in self.clf.calibrated_classifiers_], axis=0)
        return {f: round(float(v), 3) for f, v in zip(FEATURES, coefs)}

    def best(self, name, others, company=False):
        """(score, other) for the most similar of `others`; (None, None) when nothing to compare."""
        others = [o for o in others if o and tokens(o)]
        if not name or not tokens(name) or not others:
            return None, None
        scores = self.score([(name, o, company) for o in others])
        i = int(np.argmax(scores))
        return float(scores[i]), others[i]


# ------------------------------------------------------------------ pairs

class _Groups:
    """Union-find over name keys: names that appear together in a positive pair share a group."""

    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra > rb:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _approved_names(data, cid):
    out = []
    for o in data.onb_by_cust.get(cid, []):
        for k in data.kyc_by_app.get(o.get("kyc_applicant_id"), []):
            if text(k.get("result")).lower() == "approved" and text(k.get("verified_name")):
                out.append(text(k["verified_name"]))
    return out


def positive_pairs(data, results, rng, cap_differing=2500, cap_identical=400):
    """[(a, b, company, source)] of names the data says are one person or one listed entity."""
    out = []

    def is_company(row):
        return text(row.get("entity_type")).lower().startswith("compan")

    # 1. the screened name and the names of the record the engine resolved the alert to
    alerts = {a.get("alert_id"): a for a in data.alerts}
    seen = set()
    for r in results:
        aid, rid = r.get("alert_id"), r.get("customer_record_id")
        if aid in seen or not rid or rid == "AMBIGUOUS" or rid not in data.customers:
            continue
        seen.add(aid)
        screened = text(alerts.get(aid, {}).get("customer_name"))
        for cid in data.person(rid):
            full = text(data.customers[cid].get("full_name"))
            if screened and full:
                out.append((screened, full, is_company(data.customers[cid]), "screened_vs_record"))

    # 2. verified name and declared name of one record (sampled: there are tens of thousands)
    differing, identical = [], []
    for cid in sorted(data.customers):
        c = data.customers[cid]
        full = text(c.get("full_name"))
        if not full:
            continue
        for v in _approved_names(data, cid):
            (identical if key(v) == key(full) else differing).append((v, full, is_company(c), "verified_vs_declared"))
    for pool, cap in ((differing, cap_differing), (identical, cap_identical)):
        out += pool if len(pool) <= cap else rng.sample(pool, cap)

    # 3. names across the records of one person
    for person in sorted(set(data.person_of.values())):
        if len(person) < 2:
            continue
        names = []
        for cid in person:
            full = text(data.customers[cid].get("full_name"))
            if full and full not in names:
                names.append(full)
        first = text(data.customers[person[0]].get("full_name"))
        for other in ([first] if len(names) == 1 else names[1:]):
            out.append((names[0], other, is_company(data.customers[person[0]]), "records_of_one_person"))

    # 4. a hit's name and its aliases written in the same script
    #    (nicknames, trading names and code forms are not spellings of the name)
    for hid in sorted(data.hits):
        h = data.hits[hid]
        name = text(h.get("name"))
        if not name or not is_latin(name):
            continue
        for alias in split_aliases(h.get("aliases")):
            low = alias.lower()
            if any(tag in low for tag in ("(nickname", "(dba", "(ccc", "(acronym")):
                continue
            if is_latin(alias) and key(alias):
                out.append((name, alias, is_company(h), "hit_vs_alias"))

    # 5. words listed as variants of each other, alone and in front of a family name
    families = sorted({tokens(text(c.get("full_name")))[-1] for c in list(data.customers.values())[:400]
                       if tokens(text(c.get("full_name")))})
    by_group = defaultdict(list)
    for form, ids in sorted(data.rules.groups.items()):
        for i in ids:
            by_group[i].append(form)
    for i in sorted(by_group):
        forms = sorted(by_group[i])
        for x in range(len(forms)):
            for y in range(x + 1, len(forms)):
                out.append((forms[x], forms[y], False, "variant_list"))
                if families:
                    fam = rng.choice(families)
                    out.append((f"{forms[x]} {fam}", f"{forms[y]} {fam}", False, "variant_list"))
    return out


def build_pairs(data, results, seed=SEED, test_share=0.25, extra_names=4000):
    """All training pairs, each assigned to 'train' or 'test' so that no name is on both sides.

    Returns [{"a", "b", "company", "label", "source", "side"}].
    """
    rng = random.Random(seed)
    positives = positive_pairs(data, results, rng)

    groups = _Groups()
    variant_root = {}
    for a, b, _, source in positives:
        ka, kb = key(a), key(b)
        if not ka or not kb:
            continue
        groups.union(ka, kb)
        if source == "variant_list":
            # every pair built from one variant group stays on one side
            first = tokens(a)[0]
            gid = min(data.rules.groups.get(first, {first}), key=str)
            groups.union(ka, variant_root.setdefault(gid, ka))

    # names for the negatives: those in the positives plus other customers and hits
    pool = {}   # key -> (name, company)
    for a, b, company, _ in positives:
        for n in (a, b):
            if key(n):
                pool.setdefault(key(n), (n, company))
    ids = sorted(data.customers)
    for cid in (ids if len(ids) <= extra_names else rng.sample(ids, extra_names)):
        c = data.customers[cid]
        n = text(c.get("full_name"))
        if key(n):
            pool.setdefault(key(n), (n, text(c.get("entity_type")).lower().startswith("compan")))
    for hid in sorted(data.hits):
        h = data.hits[hid]
        n = text(h.get("name"))
        if key(n) and is_latin(n):
            pool.setdefault(key(n), (n, text(h.get("entity_type")).lower().startswith("compan")))

    roots = sorted({groups.find(k) for k in pool})
    rng.shuffle(roots)
    n_test = int(round(len(roots) * test_share)) if len(roots) >= 8 else 0
    test_roots = set(roots[:n_test])
    side_of = lambda k: "test" if groups.find(k) in test_roots else "train"

    out = []
    for a, b, company, source in positives:
        if key(a) and key(b):
            out.append({"a": a, "b": b, "company": company, "label": 1, "source": source, "side": side_of(key(a))})

    for side in ("train", "test"):
        keys = sorted(k for k in pool if side_of(k) == side)
        n_pos = sum(1 for p in out if p["side"] == side and p["label"] == 1)
        if len(keys) < 2 or not n_pos:
            continue
        # random pairs of different names
        made, tries = 0, 0
        while made < n_pos and tries < n_pos * 20:
            tries += 1
            ka, kb = rng.choice(keys), rng.choice(keys)
            if ka == kb or groups.find(ka) == groups.find(kb):
                continue
            out.append({"a": pool[ka][0], "b": pool[kb][0], "company": pool[ka][1] and pool[kb][1],
                        "label": 0, "source": "random_pair", "side": side})
            made += 1
        # hard negatives: two names of different people that share exactly one word
        by_word = defaultdict(list)
        for k in keys:
            words = k.split()
            if len(words) >= 2 and not pool[k][1]:
                for w in words:
                    if len(w) > 1:
                        by_word[w].append(k)
        shared = sorted(w for w, ks in by_word.items() if len(ks) >= 2)
        made, tries = 0, 0
        while shared and made < n_pos and tries < n_pos * 40:
            tries += 1
            ka, kb = rng.sample(by_word[rng.choice(shared)], 2)
            wa, wb = set(ka.split()), set(kb.split())
            if groups.find(ka) == groups.find(kb) or len(wa & wb) != 1:
                continue
            # not a pair that differs only by a listed variant ("Mohammed Ali" / "Muhammad Ali")
            if any(data.rules.variants(x, y) for x in wa - wb for y in wb - wa):
                continue
            out.append({"a": pool[ka][0], "b": pool[kb][0], "company": False, "label": 0,
                        "source": "one_shared_word", "side": side})
            made += 1
    return out


def train_and_evaluate(data, results, seed=SEED):
    """Fit on the train side, measure on the test side, then refit on every pair.

    Returns (model, metrics).
    """
    pairs = build_pairs(data, results, seed)
    as_input = lambda rows: [(p["a"], p["b"], p["company"]) for p in rows]
    train = [p for p in pairs if p["side"] == "train"]
    test = [p for p in pairs if p["side"] == "test"]
    metrics = {
        "name_model_pairs_train": len(train), "name_model_pairs_test": len(test),
        "name_model_positive_sources": dict(sorted(_count(p["source"] for p in pairs if p["label"] == 1).items())),
        "name_model_accuracy": None, "name_model_auc": None, "name_model_brier": None,
        "name_model_trained": False,
    }
    overlap = {key(n) for p in train for n in (p["a"], p["b"])} & {key(n) for p in test for n in (p["a"], p["b"])}
    metrics["name_model_names_on_both_sides"] = len(overlap)

    y_test = np.array([p["label"] for p in test], dtype=int)
    if train and len(set(p["label"] for p in train)) == 2:
        held_out = NameModel(data.rules, seed).fit(as_input(train), [p["label"] for p in train])
        if held_out.trained and len(set(y_test)) == 2:
            s = held_out.score(as_input(test))
            metrics["name_model_accuracy"] = round(float(accuracy_score(y_test, s >= 0.5)), 4)
            metrics["name_model_auc"] = round(float(roc_auc_score(y_test, s)), 4)
            metrics["name_model_brier"] = round(float(brier_score_loss(y_test, s)), 4)
            by_source = {}
            for src in sorted({p["source"] for p in test}):
                idx = np.array([p["source"] == src for p in test])
                by_source[src] = {"n": int(idx.sum()),
                                  "accuracy": round(float(((s[idx] >= 0.5) == y_test[idx]).mean()), 4)}
            metrics["name_model_accuracy_by_source"] = by_source

    model = NameModel(data.rules, seed).fit(as_input(pairs), [p["label"] for p in pairs])
    metrics["name_model_trained"] = model.trained
    metrics["name_model_coefficients"] = model.coefficients()
    return model, metrics


def _count(items):
    out = defaultdict(int)
    for i in items:
        out[i] += 1
    return out
