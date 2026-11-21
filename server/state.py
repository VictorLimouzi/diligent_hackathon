"""Everything the server knows: the data, the engine's decisions and the advisory layers.

The engine's decisions are computed once at start-up and never altered here.
ML scores and LLM second opinions are read from files written by their own
batch commands and attached next to the decision, never into it.
"""
import json
import os
import sqlite3
import threading
import time

from casebook.engine import decide_all
from casebook.export import build_casebook
from casebook.load import Data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FOLDER = os.environ.get("CASEBOOK_DATA") or os.path.join(ROOT, "data", "screening", "dev")
OUT = os.environ.get("CASEBOOK_OUT") or os.path.join(ROOT, "out")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def alert_status(hits):
    decisions = [h["decision"] for h in hits]
    if decisions and all(d == "DIFFERENT_ENTITY" for d in decisions):
        return "cleared"
    if any(d == "SAME_ENTITY" for d in decisions):
        return "compliance"
    return "open"


class State:
    def __init__(self, folder=DATA_FOLDER, out=OUT):
        self.folder, self.out = folder, out
        self.data = Data(folder)
        results = decide_all(self.data)
        self.features = {r["hit_id"]: r.get("features") for r in results}
        self.casebook = build_casebook(self.data, results)
        self.alerts = {a["alert"]["alert_id"]: a for a in self.casebook["alerts"]}
        self.hits = {h["hit_id"]: h for a in self.casebook["alerts"] for h in a["hits"]}
        self.ml_metrics = None
        self.attach_advisory()
        os.makedirs(out, exist_ok=True)
        self._db_lock = threading.RLock()
        self._db = sqlite3.connect(os.path.join(out, "audit.db"), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL, hit_id TEXT, "
                         "alert_id TEXT, analyst TEXT, decision TEXT, reason_code TEXT, comment TEXT, "
                         "engine_decision TEXT, engine_reason_code TEXT)")
        self._db.commit()

    # ------------------------------------------------------------ advisory

    def attach_advisory(self):
        """(Re)read ML scores and LLM second opinions from disk."""
        scores = _read_json(os.path.join(self.out, "ml", "scores.json")) or {}
        self.ml_metrics = _read_json(os.path.join(self.out, "ml", "metrics.json"))
        opinions = _read_json(os.path.join(self.out, "llm", "second_opinion.json")) or {}
        for hid, h in self.hits.items():
            h["ml"] = scores.get(hid)
            op = opinions.get(hid)
            h["llm"] = {"second_opinion": op} if op else None

    # --------------------------------------------------------------- views

    def alert_row(self, a):
        hits = a["hits"]
        counts = {}
        for h in hits:
            counts[h["decision"]] = counts.get(h["decision"], 0) + 1
        prios = [h["ml"]["priority"] for h in hits
                 if h["decision"] == "REVIEW" and h.get("ml") and h["ml"].get("priority") is not None]
        al = a["alert"]
        return {
            "alert_id": al.get("alert_id"), "customer_name": al.get("customer_name"),
            "entity_type": al.get("entity_type"), "screened_at": al.get("screened_at"),
            "status": alert_status(hits), "hit_count": len(hits), "decisions": counts,
            "priority": max(prios) if prios else None,
            "labelled": any(h.get("label") for h in hits),
            "blockers": sorted({h["blocker"] for h in hits if h.get("blocker")}),
            "hit_ids": [h["hit_id"] for h in hits],
            "hit_names": [h["profile"].get("name", "") for h in hits],
        }

    def label_metrics(self):
        labelled = [h for h in self.hits.values() if h.get("label")]
        return {
            "n": len(labelled),
            "correct": sum(1 for h in labelled if h["label"]["decision"] == h["decision"]),
            "false_clears": sum(1 for h in labelled if h["decision"] == "DIFFERENT_ENTITY"
                                and h["label"]["decision"] != "DIFFERENT_ENTITY"),
            "false_confirms": sum(1 for h in labelled if h["decision"] == "SAME_ENTITY"
                                  and h["label"]["decision"] != "SAME_ENTITY"),
            "reason_code_accepted": sum(1 for h in labelled if h["label"]["decision"] == h["decision"]
                                        and h["reason_code"] in h["label"]["reason_codes"]),
        }

    def summary(self, llm_status):
        hits = list(self.hits.values())
        counts, reasons = {}, {}
        for h in hits:
            counts[h["decision"]] = counts.get(h["decision"], 0) + 1
            reasons[h["reason_code"]] = reasons.get(h["reason_code"], 0) + 1
        statuses = {}
        for a in self.alerts.values():
            s = alert_status(a["hits"])
            statuses[s] = statuses.get(s, 0) + 1
        return {
            "source": self.casebook["source"],
            "alerts": len(self.alerts), "alert_status": statuses,
            "hits": len(hits), "decisions": counts, "reason_codes": reasons,
            "review_rate": (counts.get("REVIEW", 0) / len(hits)) if hits else 0,
            "labels": self.label_metrics(),
            "ml": {"trained": any(h.get("ml") for h in hits), "metrics": self.ml_metrics},
            "llm": {**llm_status, "second_opinions": sum(1 for h in hits if h.get("llm"))},
        }

    def qa(self):
        hits = list(self.hits.values())
        row = lambda h, **extra: {
            "hit_id": h["hit_id"], "alert_id": h["alert_id"], "hit_name": h["profile"].get("name", ""),
            "decision": h["decision"], "reason_code": h["reason_code"], **extra}
        disagreements = [
            row(h, llm_decision=h["llm"]["second_opinion"]["decision"],
                llm_reason_code=h["llm"]["second_opinion"].get("reason_code"),
                rationale=h["llm"]["second_opinion"].get("rationale", ""))
            for h in hits if h.get("llm") and h["llm"]["second_opinion"]
            and h["llm"]["second_opinion"]["decision"] != h["decision"]]
        near = [row(h, name_similarity=h["ml"].get("name_similarity"),
                    customer_name=(self.features.get(h["hit_id"]) or {}).get("customer_name", ""))
                for h in hits if h.get("ml") and h["ml"].get("near_miss")]
        anomalies = sorted((row(h, anomaly=h["ml"]["anomaly"]) for h in hits
                            if h.get("ml") and h["ml"].get("anomaly") is not None
                            and h["decision"] == "DIFFERENT_ENTITY"),
                           key=lambda r: -r["anomaly"])[:25]
        blockers = {}
        for h in hits:
            if h.get("blocker"):
                blockers.setdefault(h["blocker"], []).append(h["hit_id"])
        label_rows = [row(h, label_decision=h["label"]["decision"], label_reason_codes=h["label"]["reason_codes"],
                          match=h["label"]["decision"] == h["decision"]) for h in hits if h.get("label")]
        matrix = {}
        for h in hits:
            if h.get("llm") and h["llm"]["second_opinion"]:
                k = f'{h["decision"]}|{h["llm"]["second_opinion"]["decision"]}'
                matrix[k] = matrix.get(k, 0) + 1
        return {
            "label_metrics": self.label_metrics(), "labels": label_rows,
            "second_opinion": {"compared": sum(matrix.values()), "matrix": matrix, "disagreements": disagreements},
            "near_misses": near, "anomalies": anomalies,
            "blockers": [{"blocker": b, "count": len(ids), "hit_ids": ids}
                         for b, ids in sorted(blockers.items(), key=lambda kv: -len(kv[1]))],
            "ml_metrics": self.ml_metrics,
        }

    def record(self, rid):
        rec = self.data.by_id.get(rid) or self.data.hits.get(rid)
        if rec is None:
            return None
        kinds = {"CUS": "Customer record", "KYC": "Identity check", "NOTE": "Note",
                 "SR": "Search result", "H": "Watchlist profile"}
        return {"id": rid, "kind": kinds.get(rid.split("-")[0], "Record"),
                "fields": {k: ("; ".join(map(str, v)) if isinstance(v, list) else str(v))
                           for k, v in rec.items() if v not in (None, "")}}

    # --------------------------------------------------------------- audit

    def add_review(self, hit_id, analyst, decision, reason_code, comment):
        h = self.hits[hit_id]
        entry = (time.time(), hit_id, h["alert_id"], analyst, decision, reason_code, comment,
                 h["decision"], h["reason_code"])
        with self._db_lock:
            cur = self._db.execute("INSERT INTO audit (at, hit_id, alert_id, analyst, decision, reason_code, "
                                   "comment, engine_decision, engine_reason_code) VALUES (?,?,?,?,?,?,?,?,?)", entry)
            self._db.commit()
            return self.audit(entry_id=cur.lastrowid)[0]

    def audit(self, hit_id=None, entry_id=None, limit=200):
        q, args = "SELECT id, at, hit_id, alert_id, analyst, decision, reason_code, comment, " \
                  "engine_decision, engine_reason_code FROM audit", []
        if hit_id:
            q, args = q + " WHERE hit_id = ?", [hit_id]
        elif entry_id:
            q, args = q + " WHERE id = ?", [entry_id]
        with self._db_lock:
            rows = self._db.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        keys = ("id", "at", "hit_id", "alert_id", "analyst", "decision", "reason_code", "comment",
                "engine_decision", "engine_reason_code")
        return [{**dict(zip(keys, r)), "overrides_engine": r[5] != r[8]} for r in rows]
