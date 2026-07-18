#!/usr/bin/env python3
"""Tier A (research -> implement) on the first-10 deep-swe test set, via Modal.

Per task:
  Phase 1  research agent (perm-locked to docs/) writes docs/repository-analysis.md
           + docs/plan.json, commits them; captured as model.patch.
  Phase 2  docs injected into the task's Docker build context (COPY docs/ /app/docs/),
           implement agent reads docs/ and follows the plan; verifier scores it.

Only Phase 2 (which has F2P/P2P scores) is appended to experiments/results.json,
labelled tierA-implement-<task>.

Differs from run-tierA.py: loops the 10-task test set, uses Modal configs, and
works with the real tasks' git-clone Dockerfiles (no COPY src/, own base-SHA diff).

Usage:
    OPENCODE_API_KEY=... python3 experiments/run-tierA-modal-test10.py [--concurrency N]
"""
import subprocess, sys, shutil, tempfile, re, json, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "deep-swe/tasks"
CONFIGS = ROOT / "pier-configs"
RESULTS = ROOT / "experiments/results.json"
RESEARCH_CFG = CONFIGS / "opencode-deepseek-tierA-research-modal.yaml"
IMPLEMENT_CFG = CONFIGS / "opencode-deepseek-tierA-implement-modal.yaml"
TEST_SET = ROOT / "task-subsets/test10.json"
TEMP_PREFIX = "tierA-modal-"


def log(m):
    print(f"[tierA-modal] {m}", flush=True)


def run_pier(config, task_path, job_name):
    cmd = ["pier", "run", "--config", str(config), "--path", str(task_path),
           "--job-name", job_name, "--n-attempts", "1", "--yes"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"  pier rc={r.returncode} for {job_name}: {r.stderr[-300:] if r.stderr else ''}")
    return r.returncode == 0


def copy_task(task_name):
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    dst = tmp / task_name
    shutil.copytree(TASKS_DIR / task_name, dst, symlinks=True)
    return tmp, dst


def patch_pre_artifacts(task_dir):
    """Prepend add+commit before the task's existing base-SHA diff so the agent's
    work (docs or code) is captured even if it forgets to commit. Preserves the
    task's own base commit SHA."""
    pa = task_dir / "pre_artifacts.sh"
    text = pa.read_text() if pa.exists() else "#!/bin/bash\ncd /app || exit 0\n"
    safety = ['git config --global --add safe.directory /app 2>/dev/null || true',
              'git add -A 2>/dev/null || true',
              'git commit --allow-empty -m "tierA auto-commit" 2>/dev/null || true']
    out, inserted = [], False
    for ln in text.splitlines():
        if not inserted and ln.strip().startswith("git diff"):
            out.extend(safety)
            inserted = True
        out.append(ln)
    if not inserted:
        out.extend(safety)
    pa.write_text("\n".join(out) + "\n")
    pa.chmod(0o755)


def extract_patch_file(job_name):
    jd = ROOT / "jobs" / job_name
    if not jd.exists():
        return None
    trials = [d for d in jd.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    p = trials[0] / "artifacts" / "model.patch"
    return p if p.exists() else None


def extract_docs_from_patch(patch_path, target_env_dir):
    """Reconstruct docs/* files from a model.patch into <env>/docs/ (build context)."""
    content = patch_path.read_text()
    count = 0
    for diff in re.split(r'^diff --git ', content, flags=re.MULTILINE):
        if not diff.strip():
            continue
        m = re.match(r'^a/(docs/\S+) b/\S+', diff)
        if not m:
            continue
        filepath = m.group(1)
        extracted, in_hunk = [], False
        for line in diff.split('\n'):
            line = line.rstrip('\r')
            if line.startswith('@@ '):
                in_hunk = True
                continue
            if line.startswith(('--- ', '+++ ', '\\ ')):
                continue
            if in_hunk and line[:1] in ('+', ' '):
                extracted.append(line[1:])
        if extracted:
            full = target_env_dir / filepath
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text('\n'.join(extracted) + '\n')
            count += 1
    return count


def inject_docs_copy(task_dir):
    """Insert `COPY docs/ /app/docs/` just before the final CMD in the Dockerfile."""
    df = task_dir / "environment" / "Dockerfile"
    lines = df.read_text().splitlines()
    if any("COPY docs/" in l for l in lines):
        return
    cmd_idx = max((i for i, l in enumerate(lines) if l.startswith("CMD")), default=None)
    inject = "COPY docs/ /app/docs/"
    if cmd_idx is None:
        lines.append(inject)
    else:
        lines.insert(cmd_idx, inject)
    df.write_text("\n".join(lines) + "\n")


def save_results(job_name, label):
    subprocess.run(["python3", str(ROOT / "experiments/_save_results.py"),
                    str(ROOT / "jobs" / job_name), label, str(RESULTS)],
                   cwd=ROOT, capture_output=True, text=True)


def process_task(task_name):
    tmp1 = tmp2 = None
    try:
        # ---- Phase 1: research ----
        log(f"[{task_name}] Phase 1 (research)")
        tmp1, tdir1 = copy_task(task_name)
        patch_pre_artifacts(tdir1)
        rjob = f"tierA-research-{task_name}"
        run_pier(RESEARCH_CFG, tdir1, rjob)
        patch = extract_patch_file(rjob)
        if not patch or patch.stat().st_size == 0:
            log(f"[{task_name}] research produced NO docs — skipping implement")
            return {"task": task_name, "status": "no-docs", "research_job": rjob, "implement_job": None}

        # ---- Phase 2: implement ----
        tmp2, tdir2 = copy_task(task_name)
        n_docs = extract_docs_from_patch(patch, tdir2 / "environment")
        inject_docs_copy(tdir2)
        patch_pre_artifacts(tdir2)
        log(f"[{task_name}] Phase 2 (implement) — {n_docs} doc file(s) injected")
        ijob = f"tierA-implement-{task_name}"
        run_pier(IMPLEMENT_CFG, tdir2, ijob)
        save_results(ijob, f"tierA-implement-{task_name}")
        log(f"[{task_name}] done")
        return {"task": task_name, "status": "ok", "research_job": rjob,
                "implement_job": ijob, "n_docs": n_docs}
    except Exception as e:  # noqa: BLE001
        log(f"[{task_name}] ERROR: {e}")
        return {"task": task_name, "status": f"error: {e}", "research_job": None, "implement_job": None}
    finally:
        for t in (tmp1, tmp2):
            if t:
                shutil.rmtree(t, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=2, help="tasks processed in parallel")
    ap.add_argument("--tasks", nargs="*", help="override task list")
    args = ap.parse_args()

    for cfg in (RESEARCH_CFG, IMPLEMENT_CFG):
        if not cfg.exists():
            log(f"Missing config: {cfg}")
            sys.exit(1)

    tasks = args.tasks or json.loads(TEST_SET.read_text())
    log(f"Running Tier A (research->implement) on {len(tasks)} tasks, concurrency={args.concurrency}")

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(process_task, t): t for t in tasks}
        for f in as_completed(futs):
            results.append(f.result())

    ok = [r for r in results if r["status"] == "ok"]
    nodocs = [r for r in results if r["status"] == "no-docs"]
    errs = [r for r in results if r["status"].startswith("error")]
    log("=" * 60)
    log(f"TIER A COMPLETE: {len(ok)} implemented, {len(nodocs)} no-docs, {len(errs)} errored")
    for r in sorted(results, key=lambda x: x["task"]):
        log(f"  {r['task']}: {r['status']}")
    (ROOT / "experiments" / "tierA-modal-test10-summary.json").write_text(
        json.dumps(results, indent=2))
    log("Per-task implement scores appended to experiments/results.json (tierA-implement-<task>)")


if __name__ == "__main__":
    main()
