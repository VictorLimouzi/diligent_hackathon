"""Run with: python3 -m evals.run DATA [--only NAME] [--seed N]."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import traceback

from casebook.engine import decide_all
from casebook.load import Data
from .perturb import CRASHES, REPRESENTATIONS, Corpus, Experiment, Mutation, make_experiment, record_id


CLEAR, SAME, REVIEW = "DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"
REPORT = Path(__file__).with_name("report.json")
CATEGORIES = ("hits_changed", "new_clears", "new_confirms", "lost_clears",
              "missing_results", "unexpected_results", "review_violations")


def score(folder):
    errors = []
    def on_error(alert, exc):
        errors.append({"kind": "caught_alert_error", "alert_id": alert.get("alert_id"),
                       "exception": type(exc).__name__, "message": str(exc),
                       "traceback": traceback.format_exc()})
    try:
        results = decide_all(Data(folder), on_error=on_error)
    except Exception as exc:
        errors.append({"kind": "escaped_exception", "exception": type(exc).__name__,
                       "message": str(exc), "traceback": traceback.format_exc()})
        return {}, errors
    indexed = {}
    for row in results:
        key = (row["alert_id"], row["hit_id"])
        if key in indexed:
            errors.append({"kind": "duplicate_output", "alert_id": key[0], "hit_id": key[1]})
        indexed[key] = row
    return indexed, errors


def brief(result):
    if result is None:
        return None
    return {key: result.get(key) for key in ("decision", "reason_code", "customer_record_id")}


def references(value):
    """IDs actually present in either trace, without guessing name matches."""
    found = set()
    if isinstance(value, dict):
        for v in value.values():
            found.update(references(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            found.update(references(v))
    elif isinstance(value, str):
        # A path string can contain several source record identifiers.
        import re
        found.update(re.findall(r"\b(?:CUS|KYC|NOTE|SR|ACC|APP|A|H)-[\w-]+", value))
    return found


def relevant_mutations(experiment, before, after, key):
    ids = references(before) | references(after) | set(key)
    return [m for m in experiment.mutations if m.record_id in ids]


def manifest(corpus, experiment):
    touched = defaultdict(set)
    for m in experiment.mutations:
        touched[m.file].add(m.record_id)
    for table in experiment.order or {}:
        touched[corpus.files[table]].update(record_id(r, i) for i, r in enumerate(corpus.rows[table]))
    return {"fields_changed": len(experiment.mutations),
            "records_touched": sum(len(ids) for ids in touched.values()),
            "record_ids": {f: sorted(ids) for f, ids in sorted(touched.items())},
            "renames": experiment.renames or {}, "removed_files": list(experiment.remove),
            "added_files": sorted(experiment.add or {}),
            "shuffled_tables": sorted(experiment.order or {})}


def compare(baseline, after, experiment):
    categories = {c: [] for c in CATEGORIES}
    affected = set(experiment.affected_hits)
    by_record = defaultdict(list)
    for m in experiment.mutations:
        by_record[m.record_id].append(m)
    for key in sorted(baseline.keys() | after.keys()):
        b, a = baseline.get(key), after.get(key)
        bd = b.get("decision") if b else None
        ad = a.get("decision") if a else None
        if b and a and bd == ad and key[1] not in affected:
            continue
        ids = references(b) | references(a) | set(key)
        row = {"alert_id": key[0], "hit_id": key[1], "before": brief(b), "after": brief(a),
               "perturbed_record_ids": sorted(ids & by_record.keys())}
        if not row["perturbed_record_ids"] and (experiment.renames or experiment.remove or experiment.add):
            row["perturbed_record_ids"] = ["@" + f for f in sorted(
                set(experiment.renames or {}) | set(experiment.remove) | set(experiment.add or {}))]
        # Synthetic unknown profiles are expected additions, not omitted old hits.
        if b is None and key[1] not in affected:
            categories["unexpected_results"].append(row)
        if a is None:
            categories["missing_results"].append(row)
        if b and a and bd != ad:
            categories["hits_changed"].append(row)
            if bd != CLEAR and ad == CLEAR:
                categories["new_clears"].append(row)
            if bd != SAME and ad == SAME:
                categories["new_confirms"].append(row)
            if bd == CLEAR and ad == REVIEW:
                categories["lost_clears"].append(row)
        if key[1] in affected and ad != REVIEW:
            categories["review_violations"].append(row)
    # Unknown IDs must appear even if an escaped exception yielded no results.
    for hit_id in sorted(affected - {key[1] for key in baseline.keys() | after.keys()}):
        categories["review_violations"].append({"hit_id": hit_id, "alert_id": None,
                                                "before": None, "after": None,
                                                "perturbed_record_ids": sorted({m.record_id for m in experiment.mutations})})
    return categories


def evaluate(corpus, baseline, experiment):
    row = {"name": experiment.name, "kind": experiment.kind, "skip": experiment.skip,
           "manifest": manifest(corpus, experiment)}
    if experiment.skip:
        row.update({c: 0 for c in CATEGORIES}, crashes=0, examples={}, findings=[])
        return row
    with tempfile.TemporaryDirectory(prefix="casebook-evals-") as folder:
        corpus.write(folder, experiment)
        after, errors = score(folder)
    cats = compare(baseline, after, experiment)
    row.update({c: len(v) for c, v in cats.items()})
    row["crashes"] = len(errors)
    row["examples"] = {c: v[:20] for c, v in cats.items()}
    row["examples"]["crashes"] = errors[:20]
    row["errors"] = errors
    # Uncapped adverse events: findings must not disappear behind the example cap.
    row["findings"] = [{"category": c, **v} for c in
                       ("new_clears", "new_confirms", "missing_results", "unexpected_results", "review_violations")
                       for v in cats[c]]
    for error in errors:
        keys = [k for k in baseline if not error.get("alert_id") or k[0] == error["alert_id"]]
        error["examples"] = [{"alert_id": k[0], "hit_id": k[1], "before": brief(baseline[k]),
                              "after": brief(after.get(k)),
                              "perturbed_record_ids": sorted({m.record_id for m in
                                  relevant_mutations(experiment, baseline[k], after.get(k), k)})}
                             for k in sorted(keys)[:20]]
    row["examples"]["crashes"] = [
        {**example, **{k: error[k] for k in ("kind", "exception", "message") if k in error}}
        for error in errors for example in error["examples"]][:20]
    return row


def failed(row):
    return any(row.get(k, 0) for k in ("new_clears", "crashes", "missing_results",
                                      "unexpected_results", "review_violations"))


def fingerprint(folder):
    result = {}
    for path in sorted(Path(folder).iterdir()):
        if path.is_file():
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def write_report(path, report):
    """Keep every touched ID without inflating the reviewable report by 60 MB."""
    all_ids = {}
    for row in report["experiments"]:
        ids = row["manifest"].pop("record_ids", {})
        all_ids[row["name"]] = ids
        row["manifest"]["record_id_examples"] = {filename: records[:20] for filename, records in ids.items()}
    manifest_path = path.with_suffix(".records.json.gz")
    payload = gzip.compress(json.dumps(all_ids, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8"), mtime=0)
    manifest_path.write_bytes(payload)
    report["record_manifest"] = {"path": manifest_path.name, "sha256": hashlib.sha256(payload).hexdigest(),
                                 "format": "gzip JSON: experiment -> filename -> all touched record IDs"}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_folder", type=Path)
    parser.add_argument("--only", choices=REPRESENTATIONS + CRASHES)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--replay", type=Path, help="JSON object containing one or more Mutation dictionaries")
    parser.add_argument("--report", type=Path, default=REPORT, help="report destination (default evals/report.json)")
    args = parser.parse_args(argv)
    source_hashes = fingerprint(args.data_folder)
    baseline, errors = score(args.data_folder)
    report = {"schema_version": 1, "seed": args.seed, "source": str(args.data_folder.resolve()),
              "source_sha256": source_hashes,
              "baseline": {"hits": len(baseline), "decisions": dict(sorted(Counter(
                  r["decision"] for r in baseline.values()).items())), "crashes": len(errors), "errors": errors},
              "experiments": []}
    if not errors:
        corpus = Corpus(args.data_folder)
        if args.replay:
            raw = json.loads(args.replay.read_text())
            experiments = [Experiment(raw.get("name", "replay"), raw.get("kind", "representation"),
                                      tuple(Mutation(**m) for m in raw["mutations"]),
                                      affected_hits=tuple(raw.get("affected_hits", ())))]
        else:
            names = [args.only] if args.only else REPRESENTATIONS + CRASHES
            experiments = (make_experiment(corpus, name, args.seed) for name in names)
        print("Baseline:", len(baseline), "hits", report["baseline"]["decisions"], flush=True)
        print("name                         changed new_clears new_confirms lost_clears crashes missing review_fail", flush=True)
        for experiment in experiments:
            row = evaluate(corpus, baseline, experiment)
            report["experiments"].append(row)
            print(f"{row['name']:28} {row['hits_changed']:7} {row['new_clears']:10} "
                  f"{row['new_confirms']:12} {row['lost_clears']:11} {row['crashes']:7} "
                  f"{row['missing_results']:7} {row['review_violations']:11}"
                  + (f" SKIP: {row['skip']}" if row["skip"] else ""), flush=True)
    report["source_unchanged"] = source_hashes == fingerprint(args.data_folder)
    report["failed"] = bool(errors) or not report["source_unchanged"] or any(failed(r) for r in report["experiments"])
    write_report(args.report, report)
    print(f"Report: {args.report}; source unchanged: {report['source_unchanged']}", flush=True)
    return int(report["failed"])


if __name__ == "__main__":
    raise SystemExit(main())
