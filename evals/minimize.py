"""Find and verify single-field reproductions for every adverse report event.

Exploration edits a loaded row temporarily, then restores it. Only fields that
are not index keys are eligible for this fast search. Every successful result
is verified again using a real temporary folder and decide_all(Data(folder)).
Run: python3 -m evals.minimize DATA [--report evals/report.json]
"""
import argparse
import copy
import gc
import json
from pathlib import Path
import tempfile

from casebook.engine import decide_all
from casebook.load import Data
from .perturb import Corpus, Experiment, make_experiment
from .run import REPORT, brief, fingerprint, score


SAFE_FIELDS = {"name", "aliases", "full_name", "preferred_name", "customer_name", "verified_name",
               "text", "snippet", "profile_notes", "dobs", "categories", "countries", "addresses",
               "nationality", "residence_country", "document_country", "verified_dob", "checked_at",
               "deceased_date", "primary_category", "entity_type", "country", "city", "dob", "screened_at",
               "account_ref", "hit_ids"}


def locate(data, corpus, m):
    table = next(t for t, f in corpus.files.items() if f == m.file)
    original = corpus.rows[table][m.index]
    if table == "alerts":
        return data.alerts[m.index]
    if table == "hits":
        return data.hits[original["hit_id"]]
    if table == "customers":
        return data.customers[original["record_id"]]
    return data.by_id.get(m.record_id)


def fast_check(data, corpus, mutation, key):
    if mutation.field not in SAFE_FIELDS:
        return None
    table = next(t for t, f in corpus.files.items() if f == mutation.file)
    # Customer DOB has cached indexes and person clusters. Use the real-folder
    # verifier for such fields instead of treating a stale cache as evidence.
    if table == "customers" and mutation.field == "dob":
        return None
    row = locate(data, corpus, mutation)
    if row is None:
        return None
    snapshot = row.copy()
    try:
        if mutation.delete:
            row.pop(mutation.field, None)
        else:
            row[mutation.field] = mutation.after
        view = copy.copy(data)
        view.alerts = [a for a in data.alerts if a.get("alert_id") == key[0]]
        result = next((r for r in decide_all(view) if (r["alert_id"], r["hit_id"]) == key), None)
        return brief(result) if result else {"decision": "MISSING"}
    finally:
        row.clear()
        row.update(snapshot)


def matches(event, actual):
    if actual is None:
        return False
    desired = (event.get("after") or {}).get("decision", "MISSING")
    return actual.get("decision") == desired


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_folder", type=Path)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("reproductions.json"))
    args = parser.parse_args(argv)
    hashes = fingerprint(args.data_folder)
    report = json.loads(args.report.read_text())
    corpus, data = Corpus(args.data_folder), Data(args.data_folder)
    output, verified, pending, experiment = [], {}, [], None
    for row in report["experiments"]:
        if not row["findings"] and not row.get("errors"):
            continue
        events = row["findings"] + [{"category": "crash", **e} for e in row.get("errors", [])]
        relevant = {rid for event in events for rid in event.get("perturbed_record_ids", [])}
        # Variants consume RNG draws for earlier rows; keep that stream intact.
        experiment = make_experiment(corpus, row["name"], report["seed"],
                                     None if row["name"] == "names_variants" else relevant)
        for event in events:
            key = (event.get("alert_id"), event.get("hit_id"))
            candidates = [m for m in experiment.mutations if m.record_id in event.get("perturbed_record_ids", [])]
            # Text parsing is the likeliest failure and gives the clearest repro.
            candidates.sort(key=lambda m: (m.field not in {"text", "snippet", "profile_notes"},
                                            m.field not in {"verified_name", "name", "full_name", "aliases"},
                                            m.file, m.index, m.field))
            possible = []
            for m in candidates:
                if matches(event, fast_check(data, corpus, m, key)):
                    possible.append(m)
            pending.append((row["name"], experiment.kind, event, possible))
            print(row["name"], event["category"], key[1], "candidates", len(possible), flush=True)
    # Never retain two full Data indexes at once: the dev corpus is 100 MB of
    # JSON and has a much larger Python object footprint.
    del data, experiment, report["experiments"]
    gc.collect()
    for name, kind, event, possible in pending:
        key = (event.get("alert_id"), event.get("hit_id"))
        found = None
        for m in possible:
            cache_key = json.dumps(m.json(), sort_keys=True)
            if cache_key not in verified:
                with tempfile.TemporaryDirectory(prefix="casebook-minimal-") as folder:
                    corpus.write(folder, Experiment("minimal", mutations=(m,)))
                    actual, errors = score(folder)
                verified[cache_key] = ({k: brief(v) for k, v in actual.items()}, errors)
                del actual
                gc.collect()
            actual, errors = verified[cache_key]
            result = actual.get(key) or {"decision": "MISSING"}
            if not errors and matches(event, result):
                found = {"mutation": m.json(), "after": actual.get(key), "verified": True}
                table = next(t for t, filename in corpus.files.items() if filename == m.file)
                found["original_record"] = corpus.rows[table][m.index]
                destination = args.out.with_suffix("")
                destination.mkdir(exist_ok=True)
                replay = destination / f"{name}-{event['category']}-{key[1]}.json"
                replay.write_text(json.dumps({"name": replay.stem, "kind": kind, "mutations": [m.json()],
                                              "affected_hits": [key[1]] if kind == "malformed" else []},
                                             ensure_ascii=False, indent=2) + "\n")
                found["replay_file"] = str(replay)
                break
        output.append({"experiment": name, "category": event["category"],
                       "alert_id": key[0], "hit_id": key[1], "before": event.get("before"),
                       "sweep_after": event.get("after"), "minimal": found})
        print(name, event["category"], key[1],
              "verified one field" if found else "NO SINGLE-FIELD REPRO FOUND", flush=True)
        args.out.write_text(json.dumps({"seed": report["seed"], "source_unchanged": hashes == fingerprint(args.data_folder),
                                        "reproductions": output}, ensure_ascii=False, indent=2) + "\n")
    if fingerprint(args.data_folder) != hashes:
        raise RuntimeError("source files changed")
    args.out.write_text(json.dumps({"seed": report["seed"], "source_unchanged": True,
                                    "reproductions": output}, ensure_ascii=False, indent=2) + "\n")
    return int(any(r["minimal"] is None for r in output))


if __name__ == "__main__":
    raise SystemExit(main())
