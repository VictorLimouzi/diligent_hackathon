"""Extraction fallback for the texts the router flagged.

    python -m agents.extract_fallback <data_folder> --routing out/ml/routing.json --out out/llm/extractions.json

`routing.json` lists notes and search snippets that probably carry a fact the
regex extractors did not read. For each, the model extracts the fact and copies
the words that state it. `verified` is true only when that quote occurs in the
source text, so a made-up fact cannot pass as read from the record.

The output is advisory. Nothing reads it back into the engine.
"""
import argparse
import json
import os
import re
import sys

from casebook.extract import lived_countries, parse_kyc_note, parse_snippet

from . import client, prompts, schemas

_EMPTY = {"kind": "none", "name": None, "date_of_birth": None, "countries": [], "approved": None,
          "listing_phrase": None, "age": None, "age_at_date": None}


def _squash(s):
    return " ".join(str(s or "").split())


def verified(quote, source):
    """True only when the quote is in the source, word for word (whitespace aside)."""
    q = _squash(quote)
    return bool(q) and q in _squash(source)


def _sentence_with(text_, needles):
    sentences = re.split(r"(?<=[.!?])\s+", text_.strip())
    for needle in needles:
        for s in sentences:
            if needle and str(needle).lower() in s.lower():
                return s
    return sentences[0] if sentences else ""


def offline(kind, text_):
    """What the regex extractors can read, as the stand-in when no model answers."""
    fact, quote = dict(_EMPTY), ""
    if kind == "note":
        r = parse_kyc_note(text_)
        if r and (r["dob"] or r["document_type"]) and r["name"]:
            fact.update(kind="identity_check", name=r["name"], date_of_birth=r["dob"].iso() if r["dob"] else None,
                        countries=list(r["countries"]), approved=bool(r["approved"]))
            quote = _sentence_with(text_, [r["name"]])
        else:
            lived = lived_countries(text_)
            if lived:
                fact.update(kind="residence", countries=list(lived))
                quote = _sentence_with(text_, lived)
    else:
        r = parse_snippet("", text_)
        if r["phrases"]:
            fact.update(kind="listing", listing_phrase=r["phrases"][0], countries=list(r["countries"]),
                        age=r["age"], age_at_date=r["date"].iso() if r["date"] else None)
            quote = _sentence_with(text_, [r["phrases"][0]])
    return {"fact": fact, "quote": quote, "verified": verified(quote, text_), "stub": True}


def extract(kind, text_):
    """One entry {"fact", "quote", "verified"} for a text; "stub": true when no model read it."""
    source = "support or onboarding note" if kind == "note" else "web search snippet"
    request = client.build_request(
        model=client.model_batch(), system=prompts.EXTRACT, feature="extract", schema=schemas.EXTRACTION,
        messages=[{"role": "user", "content": "Extract the fact from this text.\n\n"
                   + json.dumps({"source": source, "text": text_}, ensure_ascii=False)}])
    try:
        message, _cached = client.complete(request, "extract")
    except client.LLMError:
        return offline(kind, text_)
    if message.get("stop_reason") in ("refusal", "max_tokens"):
        return offline(kind, text_)
    value, errors = schemas.parse(client.text_of(message), schemas.EXTRACTION)
    if errors:
        client.forget(request)
        return offline(kind, text_)
    return {"fact": value["fact"], "quote": value["quote"], "verified": verified(value["quote"], text_)}


def _items(data, routing):
    """(text_id, kind, source text). The record in the data folder is the source when it exists."""
    for group, kind, field in (("notes", "note", "text"), ("snippets", "snippet", "snippet")):
        for item in routing.get(group) or []:
            if not isinstance(item, dict):
                continue
            text_id = item.get("id") or item.get("note_id") or item.get("result_id")
            record = data.by_id.get(text_id) if text_id else None
            text_ = str((record or {}).get(field) or item.get("text") or "").strip()
            if text_id and text_:
                yield str(text_id), kind, text_


def run(folder, routing_path, out, limit=None, log=print):
    from casebook.load import Data

    with open(routing_path, encoding="utf-8") as f:
        routing = json.load(f)
    data = Data(folder)
    items = list(_items(data, routing if isinstance(routing, dict) else {}))
    try:
        with open(out, encoding="utf-8") as f:
            done = json.load(f)
        done = done if isinstance(done, dict) else {}
    except (OSError, ValueError):
        done = {}
    live = client.is_live()
    if not live:
        log("No API key: texts are read by the regex extractors only (stub: true).")
    todo = [i for i in items if i[0] not in done or (live and done[i[0]].get("stub"))]
    if limit is not None:
        todo = todo[:max(0, limit)]
    for text_id, kind, text_ in todo:
        try:
            done[text_id] = extract(kind, text_)
        except Exception as exc:  # noqa: BLE001 - one bad text must not stop the run
            log(f"  {text_id}: {type(exc).__name__}: {exc}")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=1)
    os.replace(out + ".tmp", out)
    found = sum(1 for v in done.values() if v.get("fact", {}).get("kind") != "none")
    ok = sum(1 for v in done.values() if v.get("verified"))
    log(f"{len(todo)} texts read this run; {len(done)} in {out}: {found} with a fact, {ok} with a verified quote.")
    return {"read": len(todo), "total": len(done), "with_fact": found, "verified": ok}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agents.extract_fallback", description=__doc__.split("\n")[0])
    parser.add_argument("data_folder")
    parser.add_argument("--routing", default=os.path.join(client.ROOT, "out", "ml", "routing.json"))
    parser.add_argument("--out", default=os.path.join(client.ROOT, "out", "llm", "extractions.json"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    client.load_env()
    try:
        run(args.data_folder, args.routing, args.out, args.limit)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
