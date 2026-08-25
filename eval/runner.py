import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import run_agent
from cases import CASES

RESULTS_DIR = Path(__file__).parent / "results"


def run_all(n_runs=3, case_ids=None):
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M")
    out_path = RESULTS_DIR / f"{stamp}.jsonl"

    cases = [c for c in CASES if case_ids is None or c["id"] in case_ids]

    with open(out_path, "a") as f:
        for case in cases:
            for i in range(n_runs):
                print(f"\n=== {case['id']} run {i + 1}/{n_runs} ===")
                started = time.time()
                try:
                    out = run_agent(case["query"])
                    record = {
                        "case_id": case["id"],
                        "run_index": i,
                        "final_answer": out["final_answer"],
                        "tool_calls": out["tool_calls"],
                        "turns": out["turns"],
                        "seconds": round(time.time() - started, 1),
                        "error": out.get("error"),
                    }
                except Exception:
                    record = {
                        "case_id": case["id"],
                        "run_index": i,
                        "final_answer": "",
                        "tool_calls": [],
                        "turns": 0,
                        "seconds": round(time.time() - started, 1),
                        "error": traceback.format_exc(),
                    }

                f.write(json.dumps(record) + "\n")
                f.flush()

    print(f"\nWrote {out_path}")
    return out_path


if __name__ == "__main__":
    run_all(n_runs=1)