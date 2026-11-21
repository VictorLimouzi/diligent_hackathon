"""Benchmark Casebook against simple baselines and against itself with one source removed.

Usage: python3 -m evals.benchmark <data folder> [--out out/benchmark.json]

Every system is scored on the practice labels with the cost matrix from the
challenge brief, and counted over every hit in the folder. Casebook is always
run through `system/run`, the same command that is scored: the "Casebook
without ..." rows run it on a temporary folder that links every real file and
replaces one with an empty or edited copy. Nothing in the engine is switched
off, and the temporary folders are removed afterwards.

Prints and writes numbers and system names only: no names, no record
contents, no hit IDs. Python 3 standard library only.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "system", "run")
COMMITTED = os.path.join(ROOT, "output", "dev.jsonl")
# The folder the other batch commands write to and the server reads from (server/state.py).
OUT = os.environ.get("CASEBOOK_OUT") or os.path.join(ROOT, "out")

DIFFERENT, SAME, REVIEW = "DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"
DECISIONS = (DIFFERENT, SAME, REVIEW)
# cost[truth][said]; a right answer costs nothing
COST = {DIFFERENT: {SAME: 3, REVIEW: 3}, SAME: {DIFFERENT: 60, REVIEW: 2}, REVIEW: {DIFFERENT: 60, SAME: 2}}
INTERPRETERS = ("/usr/bin/python3", "/usr/local/bin/python3.11", "/opt/homebrew/bin/python3.12", "python3")
RUNS = 5


# ------------------------------------------------------------------ reading

def read_jsonl(path):
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
                rows.append(row)
    return rows


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ scoring

def cost_of(truth, said):
    return 0 if truth == said else COST[truth][said]


def measure(decisions, labels, hit_ids, casebook=None):
    """The numbers for one system. `decisions` maps hit_id -> decision; a hit
    with no decision counts as REVIEW, the same rule `system/run` applies."""
    said = lambda h: decisions.get(h, REVIEW)
    right = unsafe = false_confirms = unneeded = cost = 0
    for hit_id, truth in labels:
        got = said(hit_id)
        cost += cost_of(truth, got)
        right += got == truth
        unsafe += got == DIFFERENT and truth != DIFFERENT
        false_confirms += got == SAME and truth != SAME
        unneeded += got == REVIEW and truth != REVIEW
    base = sum(cost_of(truth, REVIEW) for _, truth in labels)
    counts = {d: 0 for d in DECISIONS}
    for h in hit_ids:
        counts[said(h)] += 1
    return {
        "right": right, "unsafe": unsafe, "false_confirms": false_confirms, "unneeded_reviews": unneeded,
        "cost": cost, "score": round(max(0.0, 1 - cost / base) * 100, 1) if base else 0.0,
        "decisions": counts,
        "review_rate": round(counts[REVIEW] / len(hit_ids), 3) if hit_ids else 0.0,
        "changed_vs_casebook": 0 if casebook is None else sum(
            1 for h in hit_ids if said(h) != casebook.get(h, REVIEW)),
    }


# ------------------------------------------------------------------ running the engine

def run_engine(folder, out_path, python=None, timeout=1800):
    """One `system/run` in its own process. Returns its decisions, the bytes it
    wrote, the wall time, and what it reported on stderr (as counts only)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    start = time.perf_counter()
    try:
        proc = subprocess.run([python or sys.executable, RUN, folder, out_path], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
        code, err, timed_out = proc.returncode, proc.stderr.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        code, err, timed_out = -1, "", True
    except OSError as exc:
        code, err, timed_out = -1, "error: %s:" % type(exc).__name__, False
    seconds = time.perf_counter() - start
    try:
        with open(out_path, "rb") as f:
            raw = f.read()
    except OSError:
        raw = None
    decisions = {}
    if raw is not None:
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("hit_id") and row.get("decision") in DECISIONS:
                decisions[row["hit_id"]] = row["decision"]
    # stderr can quote record contents, so keep only what kind of failure it was
    fell_back = None
    alert_errors = 0
    for line in err.splitlines():
        if line.startswith("error: "):
            fell_back = line[len("error: "):].split(":")[0].split(" ")[0] or "error"
        elif line.startswith("warning: "):
            alert_errors += 1
    return {"decisions": decisions, "raw": raw, "seconds": seconds, "exit": code, "timed_out": timed_out,
            "timeout": timeout, "fell_back": fell_back, "alert_errors": alert_errors}


def trouble(run):
    """A plain sentence when a run did not go normally, else ''."""
    if run["timed_out"]:
        return (" The engine did not finish on this folder within %d seconds and was stopped, so every hit is "
                "counted as left for review." % run["timeout"])
    if run["raw"] is None:
        return " The engine wrote no output on this folder (exit code %d), so every hit is counted as left for review." % run["exit"]
    if run["fell_back"]:
        return (" The engine could not read this folder as expected (%s) and fell back to sending every hit to review."
                % run["fell_back"])
    if run["alert_errors"]:
        return " The engine raised an error on %d alerts and sent their hits to review." % run["alert_errors"]
    return ""


# ------------------------------------------------------------------ edited copies of the folder

def _empty(_real, path):
    open(path, "w", encoding="utf-8").close()


def _alerts_without_links(real, path):
    with open(path, "w", encoding="utf-8") as out:
        for alert in read_jsonl(real):
            for key in ("account_ref", "login_handle", "email"):
                alert[key] = None
            out.write(json.dumps(alert, ensure_ascii=False) + "\n")


def _variants_without_groups(real, path):
    """The name file with every list of name variants emptied; other keys
    (the company legal forms, the description) are kept as they are."""
    raw = read_json(real)
    if isinstance(raw, dict):
        raw = {k: ([] if ("group" in k.lower() or "variant" in k.lower()) and isinstance(v, (list, dict)) else v)
               for k, v in raw.items()}
    with open(path, "w", encoding="utf-8") as out:
        json.dump(raw, out, ensure_ascii=False)


def variant_folder(folder, replace):
    """A temporary folder that links every file of `folder`, except the files
    named in `replace` (file name -> writer), which are written afresh."""
    tmp = tempfile.mkdtemp(prefix="casebook-benchmark-")
    for name in sorted(os.listdir(folder)):
        real = os.path.realpath(os.path.join(folder, name))
        if name in replace:
            replace[name](real, os.path.join(tmp, name))
        else:
            os.symlink(real, os.path.join(tmp, name))
    return tmp


WITHOUT = [
    ("no_notes", "Casebook without onboarding and support notes",
     "Casebook run with the notes file emptied, so nothing a customer wrote or told staff is read.",
     {"notes.jsonl": _empty}),
    ("no_kyc_checks", "Casebook without identity-document checks",
     "Casebook run with the identity-check file emptied, so no verified name, date of birth or document is read.",
     {"kyc_checks.jsonl": _empty}),
    ("no_notes_no_checks", "Casebook on declared details only",
     "Casebook run with both the notes and the identity checks emptied, leaving only what the customer declared.",
     {"notes.jsonl": _empty, "kyc_checks.jsonl": _empty}),
    ("no_search_results", "Casebook without web search results",
     "Casebook run with the search-results file emptied, so nothing is known about the listed person beyond the hit itself.",
     {"search_results.jsonl": _empty}),
    ("no_alert_links", "Casebook without the alert's account, login and email",
     "Casebook run with each alert's account reference, login handle and email removed, so the customer must be found from the screened details alone.",
     {"alerts.jsonl": _alerts_without_links}),
    ("no_customer_records", "Casebook on screened details only",
     "Casebook run with the customer, account, onboarding, identity-check and notes files emptied, leaving only the details on the alert.",
     {"customers.jsonl": _empty, "accounts.jsonl": _empty, "onboarding.jsonl": _empty,
      "kyc_checks.jsonl": _empty, "notes.jsonl": _empty}),
    ("no_name_variants", "Casebook without the name-variant list",
     "Casebook run with the list of name spellings that count as the same name emptied; the company legal forms are kept.",
     {"name_variants.json": _variants_without_groups}),
]


# ------------------------------------------------------------------ baselines

def best_threshold(hits, labels):
    """The match-score threshold with the best decision score on the labels.
    Ties go to the lowest threshold (the one that clears least)."""
    scores = sorted({h["match_score"] for h in hits if isinstance(h.get("match_score"), (int, float))
                     and not isinstance(h.get("match_score"), bool)})
    if not scores:
        return None
    hit_ids = [h["hit_id"] for h in hits]
    best = None
    for t in scores + [scores[-1] + 1]:
        m = measure(by_match_score(hits, t), labels, hit_ids)
        key = (-m["cost"], -t)
        if best is None or key > best[0]:
            best = (key, t)
    return best[1]


def by_match_score(hits, threshold):
    out = {}
    for h in hits:
        s = h.get("match_score")
        ok = isinstance(s, (int, float)) and not isinstance(s, bool)
        out[h["hit_id"]] = DIFFERENT if ok and s < threshold else REVIEW
    return out


# ------------------------------------------------------------------ operations

def python_version(python):
    try:
        proc = subprocess.run([python, "-c", "import platform; print(platform.python_version())"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = proc.stdout.decode("utf-8", "replace").strip()
    return version if proc.returncode == 0 and version else None


def interpreters_agree(folder, tmp, committed):
    found = []
    for python in INTERPRETERS:
        version = python_version(python)
        if version and version not in [v for v, _ in found]:
            found.append((version, python))

    def one(item):
        version, python = item
        run = run_engine(folder, os.path.join(tmp, "python-%s.jsonl" % version), python=python)
        return {"version": version, "identical": committed is not None and run["raw"] == committed}

    with ThreadPoolExecutor(max_workers=len(found) or 1) as pool:
        rows = list(pool.map(one, found))
    return sorted(rows, key=lambda r: [int(x) if x.isdigit() else 0 for x in r["version"].split(".")])


def robustness():
    report = read_json(os.path.join(ROOT, "evals", "report.json"))
    experiments = report.get("experiments") if isinstance(report, dict) else None
    if not isinstance(experiments, list):
        return None
    ran = [e for e in experiments if isinstance(e, dict) and not e.get("skip")]
    return {"experiments": len(ran),
            "with_changes": sum(1 for e in ran if e.get("hits_changed")),
            "with_new_clears": sum(1 for e in ran if e.get("new_clears"))}


def wording(out_dir):
    total = {"rounds": 0, "sentences": 0, "unread": 0, "wrong": 0}
    for name in ("report.json", "report.r2.json", "report.r3.json"):
        report = read_json(os.path.join(out_dir, "wording", name))
        if not isinstance(report, dict):
            continue
        total["rounds"] += 1
        for kind in report.values():
            if isinstance(kind, dict):
                for key in ("sentences", "unread", "wrong"):
                    total[key] += int(kind.get(key) or 0)
    return total if total["rounds"] else None


# ------------------------------------------------------------------ the benchmark

def benchmark(folder, out_dir, jobs=2, timeout=1800):
    folder = os.path.abspath(folder)
    hits = [h for h in read_jsonl(os.path.join(folder, "hits.jsonl")) if h.get("hit_id")]
    hit_ids = [h["hit_id"] for h in hits]
    known = set(hit_ids)
    labels = [(r["hit_id"], r["decision"]) for r in read_jsonl(os.path.join(folder, "practice_labels.jsonl"))
              if r.get("hit_id") in known and r.get("decision") in DECISIONS]
    try:
        with open(COMMITTED, "rb") as f:
            committed = f.read()
    except OSError:
        committed = None

    systems = []
    tmp = tempfile.mkdtemp(prefix="casebook-benchmark-out-")
    try:
        # Casebook itself, five times: the first run is the row, all five are the repeat check.
        runs = [run_engine(folder, os.path.join(tmp, "run-%d.jsonl" % i)) for i in range(RUNS)]
        first = runs[0]
        casebook = first["decisions"]

        def add(sid, name, group, note, decisions, seconds=None):
            row = {"id": sid, "name": name, "group": group, "note": note}
            row.update(measure(decisions, labels, hit_ids, casebook))
            row["seconds"] = None if seconds is None else round(seconds, 1)
            systems.append(row)

        add("casebook", "Casebook", "casebook",
            "The rule engine as submitted, run on the full data folder." + trouble(first),
            casebook, first["seconds"])

        add("review_everything", "Send everything to review", "baseline",
            "Answers REVIEW for every hit: never unsafe, but resolves nothing.",
            {h: REVIEW for h in hit_ids})
        add("clear_everything", "Clear everything", "baseline",
            "Answers DIFFERENT_ENTITY for every hit: no analyst work, and every real match is cleared.",
            {h: DIFFERENT for h in hit_ids})
        t = best_threshold(hits, labels)
        if t is not None:
            add("match_score", "Screening match score only", "baseline",
                "Clears a hit when the screening tool's own match score is below %s and sends the rest to review. "
                "The threshold %s was chosen on the %d labels themselves, so this row flatters this baseline."
                % (t, t, len(labels)), by_match_score(hits, t))
        opinions = read_json(os.path.join(out_dir, "llm", "second_opinion.json"))
        if isinstance(opinions, dict):
            llm = {h: o["decision"] for h, o in opinions.items()
                   if isinstance(o, dict) and o.get("decision") in DECISIONS}
            unanswered = sum(1 for h in hit_ids if h not in llm)
            add("llm_second_opinion", "Language model second opinion", "baseline",
                "Claude Haiku 5.5 given the policy and the same gathered facts."
                + (" It gave no usable answer for %d hits, which are counted as left for review." % unanswered
                   if unanswered else ""), llm)

        # The edited folders run two at a time by default: one of them takes minutes (see
        # its note), and more at once on a small machine inflates every row's time.
        def without(item):
            sid, _name, _note, replace = item
            variant = variant_folder(folder, replace)
            try:
                return run_engine(variant, os.path.join(tmp, sid + ".jsonl"), timeout=timeout)
            finally:
                shutil.rmtree(variant, ignore_errors=True)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            without_runs = list(pool.map(without, WITHOUT))
        present = set(os.listdir(folder))
        for (sid, name, note, replace), run in zip(WITHOUT, without_runs):
            missing = sorted(set(replace) - present)
            if missing:
                note += " The folder has no file named %s, so that part of this run changed nothing." % ", ".join(missing)
            if run["raw"] is not None and not run["fell_back"] and run["seconds"] > 4 * max(first["seconds"], 1.0):
                note += " This run took several times as long as Casebook on the full folder."
            add(sid, name, "ablation", note + trouble(run), run["decisions"], run["seconds"])

        operations = {
            "seconds": round(statistics.median(r["seconds"] for r in runs), 1),
            "runs": RUNS,
            "runs_identical": sum(1 for r in runs if r["raw"] is not None and r["raw"] == first["raw"]),
            "matches_committed_output": committed is not None and first["raw"] == committed,
            "python": interpreters_agree(folder, tmp, committed),
            "robustness": robustness(),
            "wording": wording(out_dir),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "available": True,
        "labels": len(labels), "hits": len(hit_ids),
        "all_review_cost": sum(cost_of(truth, REVIEW) for _, truth in labels),
        "cost_matrix": COST,
        "systems": systems,
        "operations": operations,
    }


def table(result):
    head = ["System", "Right", "Unsafe clearances", "False confirms", "Unneeded reviews", "Cost", "Score",
            "Different / Same / Review", "Review rate", "Changed vs Casebook", "Seconds"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] + ["---:"] * (len(head) - 1)) + "|"]
    for s in result["systems"]:
        d = s["decisions"]
        lines.append("| " + " | ".join([
            s["name"], "%d/%d" % (s["right"], result["labels"]), str(s["unsafe"]), str(s["false_confirms"]),
            str(s["unneeded_reviews"]), str(s["cost"]), "%.1f" % s["score"],
            "%d / %d / %d" % (d[DIFFERENT], d[SAME], d[REVIEW]), "%.1f%%" % (100 * s["review_rate"]),
            str(s["changed_vs_casebook"]), "-" if s["seconds"] is None else "%.1f" % s["seconds"]]) + " |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--out", default=os.path.join(OUT, "benchmark.json"))
    ap.add_argument("--jobs", type=int, default=2, help="edited folders to run at the same time (default 2)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds allowed for one run (default 1800)")
    args = ap.parse_args(argv)
    out_path = os.path.abspath(args.out)
    result = benchmark(args.folder, OUT, jobs=max(1, args.jobs), timeout=args.timeout)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
