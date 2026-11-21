"""Decide every hit in a data folder.

Usage: python3 run.py <data_folder> [-o decisions.jsonl] [--workbench casebook.html]

decisions.jsonl has one line per hit: the decision, one reason code, the evidence
IDs and the resolved customer record. --workbench also writes a self-contained
page for analysts, with the trail and source records behind every decision.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from casebook.engine import decide_all  # noqa: E402
from casebook.export import build_casebook, write_decisions, write_workbench  # noqa: E402
from casebook.load import Data  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="folder with the JSON Lines files (alerts, hits, customers, ...)")
    ap.add_argument("-o", "--out", default="decisions.jsonl")
    ap.add_argument("--workbench", metavar="HTML", help="also write the analyst workbench page")
    ap.add_argument("--casebook", metavar="JSON", help="also write the full trail as JSON")
    args = ap.parse_args()

    data = Data(args.folder)
    results = decide_all(data, on_error=lambda a, e: print(
        f"warning: {a.get('alert_id')} sent to REVIEW after {type(e).__name__}: {e}", file=sys.stderr))
    write_decisions(results, args.out)

    if args.workbench or args.casebook:
        casebook = build_casebook(data, results)
        if args.casebook:
            with open(args.casebook, "w", encoding="utf-8") as f:
                json.dump(casebook, f, ensure_ascii=False)
        if args.workbench:
            write_workbench(casebook, args.workbench)

    counts = {}
    for r in results:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1
    print(f"{len(results)} hits decided: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
          file=sys.stderr)


if __name__ == "__main__":
    main()
