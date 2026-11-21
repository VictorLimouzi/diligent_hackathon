"""One call per feature against the configured model, with a verdict for each.

    python -m agents.live_check <data_folder> [--hit H-ADF453] [--use-cache]

With a key this is the live acceptance check: every feature should report
"live" (or "cached" with --use-cache). A feature that reports "STUB" fell back
to its offline stand-in, and the reason is printed where the feature gives one.
Without a key it runs the same calls offline, which only proves the wiring.

It costs a handful of small requests. It writes nothing but cache entries.
"""
import argparse
import json
import os
import sys

from . import chat, client, extract_fallback, investigate, queue_summary, second_opinion, write_note
from . import prompts


def _verdict(result):
    if result.get("stub"):
        return "STUB"
    return "cached" if result.get("cached") else "live"


def run(folder, hit_id=None, log=print):
    from casebook.engine import decide_all
    from casebook.export import build_casebook
    from casebook.load import Data

    data = Data(folder)
    alerts = build_casebook(data, decide_all(data))["alerts"]
    cases = {h["hit_id"]: (a["alert"], h) for a in alerts for h in a["hits"]}
    if hit_id is None:
        hit_id = "H-ADF453" if "H-ADF453" in cases else next(
            (k for k, (_a, h) in cases.items() if h["decision"] == "REVIEW"), next(iter(cases)))
    if hit_id not in cases:
        raise KeyError(f"{hit_id} is not in {folder}")
    alert, hit = cases[hit_id]
    status = client.llm_status()
    log(f"status: {json.dumps(status)}")
    log(f"hit: {hit_id}, engine decision {hit['decision']} ({hit['reason_code']})")
    verdicts = {}

    note = write_note(hit)
    verdicts["note"] = _verdict(note)
    log(f"\n[note] {verdicts['note']}\n{note['note']}")

    blockers = {}
    for _a, h in cases.values():
        if h.get("blocker"):
            blockers.setdefault(h["blocker"], []).append(h["hit_id"])
    groups = [{"blocker": b, "count": len(ids), "hit_ids": ids} for b, ids in blockers.items()]
    summary = queue_summary(groups)
    verdicts["queue"] = _verdict(summary)
    log(f"\n[queue] {verdicts['queue']}\n{summary['text']}")

    log("\n[investigate]")
    final = None
    for event in investigate(data, alert, hit):
        if event["type"] == "step":
            log(f"  step {event['index']}: {event['tool']} {json.dumps(event['input'])} -> {event['summary']}")
        elif event["type"] == "final":
            final = event["result"]
        else:
            log(f"  {event['type']}: {event.get('text') or event.get('message')}")
    verdicts["investigate"] = _verdict(final)
    log(f"  {verdicts['investigate']}: {json.dumps(final, ensure_ascii=False, indent=1)}")

    log("\n[chat]")
    answer, done = [], {}
    for event in chat(data, alert, hit, [{"role": "user", "content": "What is missing to settle this hit, and is it in the records?"}]):
        if event["type"] == "text":
            answer.append(event["delta"])
        elif event["type"] == "step":
            log(f"  step {event['index']}: {event['tool']} {json.dumps(event['input'])} -> {event['summary']}")
        elif event["type"] == "done":
            done = event
        else:
            log(f"  error: {event['message']}")
    verdicts["chat"] = _verdict(done)
    log(f"  {verdicts['chat']}: {''.join(answer)}\n  cited: {done.get('cited_ids')}")

    # The batch commands catch per-item failures themselves; called directly, these two can raise.
    try:
        opinion, why = second_opinion.opinion(prompts.read_policy(folder), alert, hit)
    except Exception as exc:  # noqa: BLE001
        opinion, why = None, f"{type(exc).__name__}: {exc}"
    verdicts["second_opinion"] = _verdict(opinion) if opinion else f"FAILED ({why})"
    log(f"\n[second opinion] {verdicts['second_opinion']}\n{json.dumps(opinion, ensure_ascii=False, indent=1)}")

    sample = next((text for text in (str(n.get("text") or "") for n in data.by_id.values() if n.get("note_id")) if text), None)
    if sample:
        try:
            extraction = extract_fallback.extract("note", sample)
            verdicts["extract"] = _verdict(extraction)
        except Exception as exc:  # noqa: BLE001
            extraction, verdicts["extract"] = None, f"FAILED ({type(exc).__name__}: {exc})"
        log(f"\n[extract] {verdicts['extract']}\nsource: {sample}\n{json.dumps(extraction, ensure_ascii=False, indent=1)}")

    log("\n" + "  ".join(f"{k}={v}" for k, v in verdicts.items()))
    return verdicts


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agents.live_check", description=__doc__.split("\n")[0])
    parser.add_argument("data_folder")
    parser.add_argument("--hit", default=None)
    parser.add_argument("--use-cache", action="store_true", help="accept cached responses instead of forcing new calls")
    args = parser.parse_args(argv)
    client.load_env()
    if client.is_live() and not args.use_cache:
        os.environ["CASEBOOK_LLM_REFRESH"] = "1"
    try:
        verdicts = run(args.data_folder, args.hit)
    except (OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not client.is_live():
        print("No API key: this was an offline run. Set ANTHROPIC_API_KEY to check the live paths.")
        return 0
    bad = [k for k, v in verdicts.items() if v not in ("live", "cached")]
    print("All features answered from the model." if not bad else f"Fell back or failed: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
