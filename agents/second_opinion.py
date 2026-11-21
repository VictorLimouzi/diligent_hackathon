"""An independent second opinion on every hit, recorded next to the engine's decision.

    python -m agents.second_opinion <data_folder> --out out/llm/second_opinion.json [--limit N]

The model gets the screening policy (a cached prompt prefix) and the facts the
engine gathered for the hit, but not the engine's decision, reason, checks or
summary, and returns a decision, a reason code and a one-paragraph rationale as
structured output. `agrees` is computed here against the engine. Nothing in
this file can change an engine decision: it only writes its own JSON file.

Resumable: hits already in the output file are skipped. Without a key the
entries are offline stand-ins marked `stub: true`; a later run with a key
replaces them.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import client, mock, prompts, schemas
from .tools import untrusted

MAX_SNIPPETS = 12
SAVE_EVERY = 25


def facts(alert, hit):
    """The facts for one hit, without anything the engine concluded from them."""
    trace = hit.get("trace") or {}
    gathered = trace.get("hit") or {}
    profile = hit.get("profile") or {}
    used = set(gathered.get("dob_sources") or [])
    considered = sorted(gathered.get("search_considered") or [], key=lambda s: s.get("result_id") not in used)
    customers = []
    for cand in trace.get("candidates") or []:
        cust = cand.get("customer") or {}
        customers.append({k: cust.get(k) for k in (
            "records", "primary", "entity_type", "verified_check", "verified_name", "declared_names",
            "dob", "dob_source", "dob_verified", "countries")})
    alert = alert or {}
    return {
        "hit_id": hit.get("hit_id"),
        "alert": {k: alert[k] for k in ("alert_id", "screened_at", "customer_name", "entity_type", "dob",
                                         "country", "city", "account_ref", "login_handle", "email") if alert.get(k)},
        "how_the_customer_was_found": trace.get("resolution") or [],
        "customers": customers,
        "hit": {
            "entity_type": profile.get("entity_type"),
            "categories": profile.get("categories"), "primary_category": profile.get("primary_category"),
            "names": gathered.get("names") or [],
            "dobs": gathered.get("dobs") or [], "dob_origin": gathered.get("dob_origin"),
            "dob_quote": untrusted(gathered["dob_quote"], 300) if gathered.get("dob_quote") else None,
            "dob_sources": gathered.get("dob_sources") or [],
            "countries": gathered.get("countries") or [],
            "earliest_report": gathered.get("earliest_report"),
            "earliest_report_line": (untrusted(gathered["earliest_report_line"], 300)
                                     if gathered.get("earliest_report_line") else None),
            "death": gathered.get("death"), "life_status": profile.get("life_status"),
            "search_results": [{"result_id": s.get("result_id"), "used_for_birth_date": s.get("result_id") in used,
                                "snippet": untrusted(s.get("snippet"), 400)} for s in considered[:MAX_SNIPPETS]],
        },
    }


def build_request(policy, alert, hit):
    case = json.dumps(facts(alert, hit), ensure_ascii=False, indent=1)
    return client.build_request(
        model=client.model_batch(), system=prompts.with_policy(prompts.SECOND_OPINION, policy),
        feature="second_opinion", schema=schemas.SECOND_OPINION,
        messages=[{"role": "user", "content": "Give your second opinion on this case.\n\n<case>\n" + case + "\n</case>"}])


def _entry(value, hit, model, stub=False):
    decision, code = value["decision"], value["reason_code"]
    out = {"decision": decision,
           # A code that does not belong to the decision is dropped rather than recorded.
           "reason_code": code if code in schemas.REASON_CODES[decision] else None,
           "rationale": value["rationale"].strip(),
           "agrees": decision == hit.get("decision"),
           "model": model}
    if stub:
        out["stub"] = True
    return out


def opinion(policy, alert, hit):
    """(SecondOpinion, None) or (None, why it could not be had)."""
    if policy:
        request = build_request(policy, alert, hit)
        try:
            message, _cached = client.complete(request, "second_opinion")
        except client.Unavailable:
            message = None
        except client.LLMError as exc:
            return None, str(exc)
        if message is not None:
            if message.get("stop_reason") in ("refusal", "max_tokens"):
                return None, f"the reply stopped with {message['stop_reason']}"
            value, errors = schemas.parse(client.text_of(message), schemas.SECOND_OPINION)
            if errors or not value["rationale"].strip():
                client.forget(request)   # do not replay an unusable answer; the next run asks again
                return None, "the reply did not fit the schema: " + "; ".join(errors or ["empty rationale"])
            return _entry(value, hit, client.model_batch()), None
    return _entry(mock.second_opinion(facts(alert, hit)), hit, "mock", stub=True), None


# ------------------------------------------------------------------- batch

def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(existing, dict):
        return {}
    return {k: v for k, v in existing.items()
            if isinstance(v, dict) and v.get("decision") in schemas.DECISIONS and isinstance(v.get("rationale"), str)}


def _save(path, entries):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def matrix(cases, entries):
    """{"ENGINE|MODEL": count} over the hits that have an opinion."""
    counts = {}
    for _alert, hit in cases:
        op = entries.get(hit["hit_id"])
        if op:
            key = f"{hit['decision']}|{op['decision']}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def format_matrix(counts):
    width = max(len(d) for d in schemas.DECISIONS) + 2
    head = "engine \\ second opinion".ljust(width + 8) + "".join(d.rjust(width) for d in schemas.DECISIONS)
    lines = [head]
    for engine in schemas.DECISIONS:
        row = "".join(str(counts.get(f"{engine}|{d}", 0)).rjust(width) for d in schemas.DECISIONS)
        lines.append(engine.ljust(width + 8) + row)
    return "\n".join(lines)


def run(folder, out, limit=None, concurrency=4, redo=False, log=print):
    """Compute missing second opinions, write the file, print the agreement matrix. Returns a summary dict."""
    from casebook.engine import decide_all
    from casebook.export import build_casebook
    from casebook.load import Data

    data = Data(folder)
    casebook = build_casebook(data, decide_all(data))
    cases = [(a["alert"], h) for a in casebook["alerts"] for h in a["hits"]]
    policy = prompts.read_policy(folder)
    live = client.is_live()
    if live and not policy:
        raise FileNotFoundError(f"{os.path.join(str(folder), 'POLICY.md')} not found: the second opinion needs the policy text")
    if not live:
        log("No API key: answers come from the response cache where it has them, otherwise from an offline "
            "stand-in (stub: true, model \"mock\") that is a crude re-reading of the facts, not model output.")

    entries = {} if redo else _load(out)
    todo = [(a, h) for a, h in cases
            if h["hit_id"] not in entries or (live and entries[h["hit_id"]].get("stub"))]
    if limit is not None:
        todo = todo[:max(0, limit)]
    written, errors, interrupted = [], [], False

    def work(case):
        alert, hit = case
        try:
            return (hit["hit_id"], *opinion(policy, alert, hit))
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the batch
            return hit["hit_id"], None, f"{type(exc).__name__}: {exc}"

    def take(result):
        hit_id, entry, why = result
        if entry is None:
            errors.append((hit_id, why))
            return
        entries[hit_id] = entry
        written.append(hit_id)
        if len(written) % SAVE_EVERY == 0:
            _save(out, entries)

    try:
        if todo:
            # One request first: it writes the prompt cache (policy prefix) that the parallel ones then read.
            take(work(todo[0]))
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for future in as_completed([pool.submit(work, case) for case in todo[1:]]):
                take(future.result())
    except KeyboardInterrupt:
        interrupted = True

    # File order follows the data; `agrees` is recomputed against the engine as it is now.
    by_hit = {h["hit_id"]: h for _a, h in cases}
    ordered = {}
    for hit_id in [*by_hit, *[k for k in entries if k not in by_hit]]:
        if hit_id in entries:
            entry = dict(entries[hit_id])
            if hit_id in by_hit:
                entry["agrees"] = entry["decision"] == by_hit[hit_id]["decision"]
            ordered[hit_id] = entry
    _save(out, ordered)

    counts = matrix(cases, ordered)
    compared = sum(counts.values())
    agree = sum(n for key, n in counts.items() if key.split("|")[0] == key.split("|")[1])
    stubs = sum(1 for h in by_hit if ordered.get(h, {}).get("stub"))
    log(f"{len(written)} second opinions written this run"
        + (" (interrupted)" if interrupted else "")
        + f"; {compared} of {len(cases)} hits have one in {out}"
        + (f", {stubs} of them offline stand-ins" if stubs else "") + ".")
    if errors:
        log(f"{len(errors)} could not be had and will be retried on the next run:")
        for hit_id, why in errors[:10]:
            log(f"  {hit_id}: {why}")
    if compared:
        log(f"Agreement with the engine: {agree}/{compared} ({100 * agree / compared:.1f}%)")
        log(format_matrix(counts))
    return {"written": len(written), "compared": compared, "agree": agree, "stubs": stubs,
            "errors": errors, "matrix": counts, "interrupted": interrupted}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agents.second_opinion", description=__doc__.split("\n")[0])
    parser.add_argument("data_folder")
    parser.add_argument("--out", default=os.path.join(client.ROOT, "out", "llm", "second_opinion.json"))
    parser.add_argument("--limit", type=int, default=None, help="at most this many new opinions (cheap trial run)")
    parser.add_argument("--concurrency", type=int, default=4, help="requests in flight at once")
    parser.add_argument("--redo", action="store_true", help="ignore the existing output file")
    args = parser.parse_args(argv)
    client.load_env()
    try:
        summary = run(args.data_folder, args.out, args.limit, args.concurrency, args.redo)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if summary["interrupted"]:
        return 130
    return 1 if summary["errors"] and not summary["written"] else 0


if __name__ == "__main__":
    sys.exit(main())
