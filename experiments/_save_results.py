#!/usr/bin/env python3
"""Extract trial results from a Pier job directory and append to results.json."""
import json, sys, os, glob
from datetime import datetime

job_dir = sys.argv[1]
experiment = sys.argv[2]
results_file = sys.argv[3]

trial_dirs = sorted(glob.glob(os.path.join(job_dir, "*__*/")))
if not trial_dirs:
    print("No trial directories found")
    sys.exit(1)

if os.path.exists(results_file):
    with open(results_file) as f:
        results = json.load(f)
else:
    results = []

agg_input = 0
agg_output = 0
agg_errors = 0
agg_start = None
agg_end = None
f2p_sum = 0.0
f2p_total = 0
p2p_sum = 0
p2p_total = 0
n_trials = 0

for trial_dir in sorted(trial_dirs):
    trial_name = os.path.basename(trial_dir.rstrip("/"))
    result_path = os.path.join(trial_dir, "result.json")
    if not os.path.exists(result_path):
        print(f"  Skipping {trial_name}: no result.json")
        continue

    with open(result_path) as f:
        trial = json.load(f)

    vr = trial.get("verifier_result", {}).get("rewards", {})
    ar = trial.get("agent_result", {})
    ae = trial.get("agent_execution", {})
    exc = trial.get("exception_info")

    entry = {
        "experiment": experiment,
        "eval_name": f"opencode__{experiment}",
        "attempt": n_trials + 1,
        "reward": vr.get("reward"),
        "partial": vr.get("partial"),
        "f2p_passed": vr.get("f2p_passed"),
        "f2p_total": vr.get("f2p_total"),
        "f2p_ratio": vr.get("f2p"),
        "p2p_passed": vr.get("p2p_passed"),
        "p2p_total": vr.get("p2p_total"),
        "p2p_ratio": vr.get("p2p"),
        "n_agent_steps": ar.get("n_agent_steps"),
        "input_tokens": ar.get("n_input_tokens"),
        "output_tokens": ar.get("n_output_tokens"),
        "cost_usd": ar.get("cost_usd"),
        "exception": exc.get("exception_type") if exc else None,
        "started_at": ae.get("started_at"),
        "finished_at": ae.get("finished_at"),
    }
    results.append(entry)
    n_trials += 1

    # Aggregate
    if exc:
        agg_errors += 1
    else:
        f2p_sum += (vr.get("f2p_passed") or 0)
        f2p_total += (vr.get("f2p_total") or 0)
        p2p_sum += (vr.get("p2p_passed") or 0)
        p2p_total += (vr.get("p2p_total") or 0)
    agg_input += ar.get("n_input_tokens") or 0
    agg_output += ar.get("n_output_tokens") or 0

    s = ae.get("started_at")
    e = ae.get("finished_at")
    if s and (agg_start is None or s < agg_start):
        agg_start = s
    if e and (agg_end is None or e > agg_end):
        agg_end = e

    status = "OK" if not exc else f"ERROR({exc['exception_type']})"
    print(f"  Trial {trial_name}: {vr.get('f2p_passed',0)}/{vr.get('f2p_total',0)} f2p, {vr.get('p2p_passed',0)}/{vr.get('p2p_total',0)} p2p [{status}]")

if n_trials == 0:
    print("No trials with results found")
    sys.exit(1)

agg = {
    "experiment": experiment,
    "attempt": "aggregate",
    "n_trials": n_trials,
    "n_errors": agg_errors,
    "f2p_passed": f2p_sum,
    "f2p_total": f2p_total,
    "f2p_ratio": f2p_sum / f2p_total if f2p_total else None,
    "p2p_passed": p2p_sum,
    "p2p_total": p2p_total,
    "p2p_ratio": p2p_sum / p2p_total if p2p_total else None,
    "input_tokens": agg_input,
    "output_tokens": agg_output,
    "started_at": agg_start,
    "finished_at": agg_end,
}
results.append(agg)

with open(results_file, "w") as f:
    json.dump(results, f, indent=2)

print(f"\nAggregate: {f2p_sum}/{f2p_total} f2p ({f2p_sum/f2p_total*100:.1f}%), {p2p_sum}/{p2p_total} p2p")
print(f"Saved results to {results_file}")
