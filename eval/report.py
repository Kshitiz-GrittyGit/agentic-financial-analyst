import argparse
import json
from collections import defaultdict
from pathlib import Path

from cases import CASES
from scorers import (score_correctness, score_number_groundedness,
                     score_tool_routing)

CASE_BY_ID = {c["id"]: c for c in CASES}
RESULTS_DIR = Path(__file__).parent / "results"


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def report(path):
    records = load(path)
    stats = defaultdict(lambda: defaultdict(list))
    failures = []

    for r in records:
        case = CASE_BY_ID.get(r["case_id"])
        if case is None:
            continue
        cid = r["case_id"]

        if r.get("error"):
            stats[cid]["completed"].append(False)
            failures.append((cid, r["run_index"], "error", r["error"][:120]))
            continue
        stats[cid]["completed"].append(True)

        for name, fn in (
            ("correctness", lambda: score_correctness(r, case)),
            ("grounded", lambda: score_number_groundedness(r)),
            ("routing", lambda: score_tool_routing(r, case)),
        ):
            passed, detail = fn()
            stats[cid][name].append(passed)
            if not passed:
                failures.append((cid, r["run_index"], name, detail))

        stats[cid]["turns"].append(r["turns"])
        stats[cid]["seconds"].append(r["seconds"])
        stats[cid]["tool_calls"].append(len(r["tool_calls"]))

    def rate(vals):
        return f"{sum(vals)}/{len(vals)}" if vals else "-"

    def mean(vals):
        return f"{sum(vals) / len(vals):.1f}" if vals else "-"

    print(f"\n{path.name}  ({len(records)} runs)\n")
    header = f"{'case':<26}{'done':>7}{'correct':>9}{'grounded':>10}{'routing':>9}{'turns':>7}{'calls':>7}{'secs':>7}"
    print(header)
    print("-" * len(header))
    for cid, s in stats.items():
        print(f"{cid:<26}{rate(s['completed']):>7}{rate(s['correctness']):>9}"
              f"{rate(s['grounded']):>10}{rate(s['routing']):>9}"
              f"{mean(s['turns']):>7}{mean(s['tool_calls']):>7}{mean(s['seconds']):>7}")

    if failures:
        print("\nFailures:")
        for cid, idx, kind, detail in failures:
            print(f"  {cid} run{idx} [{kind}] {detail}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file", nargs="?", help="results jsonl; defaults to latest")
    args = p.parse_args()

    path = Path(args.file) if args.file else max(
        RESULTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    report(path)