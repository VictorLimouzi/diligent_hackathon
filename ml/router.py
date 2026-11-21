"""Text routing: which notes and search snippets carry a fact the regex extractors did not read.

Two TF-IDF + logistic-regression classifiers, trained on weak labels that the
extractors in `casebook/extract.py` give for free:

  notes     identity_check (parse_kyc_note reads a name and a date of birth)
            residence      (lived_countries reads a country)
            other
  snippets  listing        (parse_snippet finds a listing phrase)
            benign

A text is routed when the classifier, predicting out of fold, is confident it
belongs to a fact-carrying class while the extractor came back without one of
that class's facts. The routed texts go to `routing.json` for the LLM fallback;
nothing here feeds the engine.
"""
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold

from casebook.extract import lived_countries, parse_kyc_note, parse_snippet
from casebook.load import text

from . import SEED

CONFIDENT = 0.9     # out-of-fold probability from which a text is routed
_CAPS_RUN = re.compile(r"\b[^\W\d_a-z]{2,}(?:[\s'’.-]+[^\W\d_a-z]{2,})*\b", re.UNICODE)
_DIGITS = re.compile(r"\d")


def note_shape(note_text):
    """A note with its specifics blanked: names in capitals and digits, so templates group."""
    s = _CAPS_RUN.sub(" capsname ", note_text or "")
    return " ".join(_DIGITS.sub("0", s).lower().split())


def snippet_shape(snippet, query=""):
    """A snippet without the words of the name that was searched, digits blanked."""
    s = snippet or ""
    for tok in sorted(set(re.findall(r"[^\W\d_]{2,}", query or "", re.UNICODE)), key=len, reverse=True):
        s = re.sub(rf"(?<![^\W\d_]){re.escape(tok)}(?![^\W\d_])", " person ", s, flags=re.I | re.UNICODE)
    return " ".join(_DIGITS.sub("0", s).lower().split())


def _out_of_fold(shapes, labels, classes, seed):
    """Per distinct shape: out-of-fold class probabilities, plus held-out accuracy and macro F1.

    Distinct shapes, not texts: thousands of notes are the same sentence, and a
    held-out copy of a training sentence would measure nothing.
    """
    distinct = {}
    for s, l in zip(shapes, labels):
        distinct.setdefault(s, l)   # a shape has one weak label: the extractors are deterministic
    keys = sorted(distinct)
    y = np.array([classes.index(distinct[k]) for k in keys], dtype=int)
    proba = np.full((len(keys), len(classes)), np.nan)
    counts = np.bincount(y, minlength=len(classes))
    present = [c for c in range(len(classes)) if counts[c] > 0]
    folds = min(5, int(min(counts[c] for c in present))) if present else 0
    info = {"distinct_texts": len(keys), "classes": {classes[c]: int(counts[c]) for c in range(len(classes))},
            "accuracy": None, "macro_f1": None, "trained": False}
    if len(present) < 2 or folds < 2:
        return {}, info     # one class only, or too few texts: nothing to learn or to route
    for train, test in StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(keys, y):
        vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        clf = LogisticRegression(C=10.0, max_iter=2000)
        clf.fit(vec.fit_transform([keys[i] for i in train]), y[train])
        p = clf.predict_proba(vec.transform([keys[i] for i in test]))
        proba[np.ix_(test, clf.classes_)] = p
    proba = np.nan_to_num(proba)
    pred = proba.argmax(axis=1)
    info.update(accuracy=round(float(accuracy_score(y, pred)), 4),
                macro_f1=round(float(f1_score(y, pred, average="macro", labels=present)), 4), trained=True)
    return {k: proba[i] for i, k in enumerate(keys)}, info


def route_notes(data, seed=SEED):
    notes = sorted((n for n in data.by_id.values() if n.get("note_id")), key=lambda n: n["note_id"])
    classes = ["identity_check", "residence", "other"]
    shapes, labels, parsed = [], [], []
    for n in notes:
        t = text(n.get("text"))
        r = parse_kyc_note(t, n.get("created_at")) or {}
        lived = lived_countries(t)
        parsed.append((r, lived))
        labels.append("identity_check" if (r.get("name") and r.get("dob")) else "residence" if lived else "other")
        shapes.append(note_shape(t))
    proba, info = _out_of_fold(shapes, labels, classes, seed)
    routed = []
    for n, shape, (r, lived) in zip(notes, shapes, parsed):
        p = proba.get(shape)
        if p is None:
            continue
        missing = None
        if p[0] >= CONFIDENT:
            lacks = [what for what, got in (("name", r.get("name")), ("date of birth", r.get("dob"))) if not got]
            if lacks:
                predicted, conf = "identity_check", p[0]
                missing = " or ".join(lacks)
        elif p[1] >= CONFIDENT and not lived:
            predicted, conf, missing = "residence", p[1], "country"
        if missing:
            routed.append((-conf, n["note_id"], {
                "id": n["note_id"], "text": text(n.get("text")), "predicted": predicted,
                "reason": f"Reads as {'an identity-check' if predicted == 'identity_check' else 'a residence'} "
                          f"note (confidence {conf:.2f}), but the extractor read no {missing}."}))
    return [row for _, _, row in sorted(routed, key=lambda t: t[:2])], info


def route_snippets(data, seed=SEED):
    results = sorted((s for s in data.by_id.values() if s.get("result_id")), key=lambda s: s["result_id"])
    classes = ["listing", "benign"]
    shapes, labels, parsed = [], [], []
    for s in results:
        r = parse_snippet(text(s.get("title")), text(s.get("snippet")), text(s.get("query")))
        parsed.append(r)
        labels.append("listing" if r["phrases"] else "benign")
        shapes.append(snippet_shape(text(s.get("snippet")), text(s.get("query"))))
    proba, info = _out_of_fold(shapes, labels, classes, seed)
    routed = []
    for s, shape, r in zip(results, shapes, parsed):
        p = proba.get(shape)
        if p is None or p[0] < CONFIDENT:
            continue
        lacks = [what for what, got in (("listing phrase", r["phrases"]), ("country", r["countries"]),
                                        ("age at a date", r["age"] is not None)) if not got]
        if lacks:
            routed.append((-p[0], s["result_id"], {
                "id": s["result_id"], "text": text(s.get("snippet")), "predicted": "listing",
                "reason": f"Reads as a snippet about a listed person (confidence {p[0]:.2f}), "
                          f"but the extractor read no {' and no '.join(lacks)}."}))
    return [row for _, _, row in sorted(routed, key=lambda t: t[:2])], info


def route(data, seed=SEED):
    """(routing, metrics): the routing.json content and the validation numbers."""
    notes, note_info = route_notes(data, seed)
    snippets, snippet_info = route_snippets(data, seed)
    metrics = {
        "router_confidence_threshold": CONFIDENT,
        "router_notes": note_info, "router_snippets": snippet_info,
        "router_notes_routed": len(notes), "router_snippets_routed": len(snippets),
    }
    return {"notes": notes, "snippets": snippets}, metrics
