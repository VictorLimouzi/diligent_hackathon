"""Score the engine against the practice labels in a data folder.

Usage: python3 eval.py <data_folder> [-v]

Reports the mistakes that matter most first: false clears (a hit that is the
customer, or needs review, decided DIFFERENT_ENTITY), then false confirms, the
review rate, and agreement on reason code, customer record and evidence.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from casebook.engine import decide_alert  # noqa: E402
from casebook.load import Data, as_list  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every disagreement in full")
    args = ap.parse_args()

    data = Data(args.folder)
    if not data.labels:
        sys.exit("no practice labels in this folder")
    alerts = {a["alert_id"]: a for a in data.alerts}
    cache = {}
    n = ok = code_ok = rec_ok = 0
    false_clear, false_confirm, other = [], [], []
    jaccard = []
    confusion = {}
    for lab in data.labels:
        aid = lab["alert_id"]
        if aid not in cache:
            cache[aid] = {r["hit_id"]: r for r in decide_alert(data, alerts[aid])}
        r = cache[aid].get(lab["hit_id"])
        n += 1
        want, got = lab["decision"], r["decision"]
        confusion[(want, got)] = confusion.get((want, got), 0) + 1
        want_codes = as_list(lab.get("reason_codes"))
        want_recs = as_list(lab.get("customer_record_ids"))
        want_ev = set(as_list(lab.get("evidence")))
        if want == got:
            ok += 1
            code_ok += r["reason_code"] in want_codes
            rec_ok += (r["customer_record_id"] in want_recs) or (not want_recs and r["customer_record_id"] is None)
            got_ev = set(r["evidence"])
            jaccard.append(len(want_ev & got_ev) / len(want_ev | got_ev) if (want_ev | got_ev) else 1.0)
        elif got == "DIFFERENT_ENTITY":
            false_clear.append((lab, r))
        elif got == "SAME_ENTITY":
            false_confirm.append((lab, r))
        else:
            other.append((lab, r))
        if args.verbose and (want != got or r["reason_code"] not in want_codes
                             or set(r["evidence"]) != want_ev):
            print(f"--- {lab['hit_id']} ({aid})")
            print(f"  want {want} {want_codes} rec={want_recs} ev={sorted(want_ev)}")
            print(f"  got  {got} {r['reason_codes']} rec={r['customer_record_id']} ev={sorted(r['evidence'])}")
            print(f"  label: {lab.get('path')}")
            print(f"  ours : {' -> '.join(r['trace']['resolution'])}")
            print(f"         {r['summary']}")

    print(f"labels: {n}")
    print(f"decision correct: {ok}/{n}")
    print(f"FALSE CLEARS: {len(false_clear)}  (worst mistake)")
    for lab, r in false_clear:
        print(f"   {lab['hit_id']}: label {lab['decision']}, engine DIFFERENT_ENTITY {r['reason_codes']}")
    print(f"false confirms: {len(false_confirm)}")
    for lab, r in false_confirm:
        print(f"   {lab['hit_id']}: label {lab['decision']}, engine SAME_ENTITY {r['reason_codes']}")
    print(f"sent to review though the label decides: {sum(1 for l, r in other if r['decision'] == 'REVIEW')}")
    for lab, r in other:
        print(f"   {lab['hit_id']}: label {lab['decision']}, engine {r['decision']} {r['reason_codes']}")
    if ok:
        print(f"of the {ok} correct: reason code accepted {code_ok}, customer record accepted {rec_ok}, "
              f"evidence overlap (Jaccard) {sum(jaccard) / len(jaccard):.2f}, "
              f"evidence exact {sum(1 for j in jaccard if j == 1.0)}")
    print("confusion (label -> engine):")
    for (w, g), c in sorted(confusion.items()):
        print(f"   {w:17s} -> {g:17s} {c}")


if __name__ == "__main__":
    main()
