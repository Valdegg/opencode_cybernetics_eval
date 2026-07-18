#!/usr/bin/env python3
"""Tier B: Verified State Transitions Loop wrapper.

Orchestration:
  Phase 0: Planning agent (perm-locked, docs/ only) → plan.json
  Phases 1-N: Per-step implement → verify → review → repair → persist
    Each step N builds on the accumulated state from steps 1..N-1.

Architecture:
  A `cumulative_dir` is maintained across steps. Each step's implement trial
  copies from it (so the Docker image already has previous steps' code).
  The model.patch from each trial = only that step's changes (since _baseline
  in the container includes previous steps). The step patch is applied back
  to cumulative_dir for the next step.

Usage:
    python3 experiments/run-tierB.py
    python3 experiments/run-tierB.py --cleanup
    python3 experiments/run-tierB.py --check
"""
import subprocess, sys, os, shutil, tempfile, re, json, textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = PROJECT_ROOT / "deep-swe/tasks/dummy-adaptix-alias"
PIER_CONFIGS = PROJECT_ROOT / "pier-configs"
RESULTS_FILE = PROJECT_ROOT / "experiments/results.json"

PLAN_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-plan-dummy.yaml"
IMPLEMENT_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-implement-dummy.yaml"
REVIEW_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-review-dummy.yaml"

TEMP_PREFIX = "tierB-task-"
MAX_REPAIR_ATTEMPTS = 3


def log(msg):
    print(f"[tierB] {msg}")


def check_prerequisites():
    missing = []
    for cmd in ["pier", "docker"]:
        if not shutil.which(cmd):
            missing.append(cmd)
    for cfg in [PLAN_CONFIG, IMPLEMENT_CONFIG, REVIEW_CONFIG]:
        if not cfg.exists():
            missing.append(str(cfg.name))
    if not TASK_DIR.exists():
        missing.append(str(TASK_DIR))
    if missing:
        log(f"Missing: {', '.join(missing)}")
        sys.exit(1)
    log("Prerequisites OK")


def run_pier(config_file, task_path, n_attempts=1):
    cmd = [
        "pier", "run",
        "--config", str(config_file),
        "--path", str(task_path),
        "--n-attempts", str(n_attempts),
        "--yes"
    ]
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        log(f"Pier exited with code {result.returncode}")
    return result.returncode == 0


def find_latest_job_dir(before=None):
    jobs_dir = PROJECT_ROOT / "jobs"
    if not jobs_dir.exists():
        return None
    job_dirs = sorted(
        [d for d in jobs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()]
    )
    if not job_dirs:
        return None
    latest = job_dirs[-1]
    if before is not None and latest == before:
        return None
    return latest


def copy_task_dir(source):
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    shutil.copytree(source, tmp, dirs_exist_ok=True, symlinks=True)
    return tmp


def write_pre_artifacts(task_dir):
    pre_art = task_dir / "pre_artifacts.sh"
    content = textwrap.dedent("""\
    #!/bin/bash
    set -uo pipefail
    cd /app || exit 0
    mkdir -p /logs/artifacts
    git config --global --add safe.directory /app 2>/dev/null || true
    git add -A 2>/dev/null || true
    git commit --allow-empty -m "pre_artifacts auto-commit" 2>/dev/null || true
    git diff --binary _baseline HEAD > /logs/artifacts/model.patch 2>/dev/null || true
    echo "[pre_artifacts] captured $(wc -c < /logs/artifacts/model.patch) bytes"
    """)
    pre_art.write_text(content)
    pre_art.chmod(0o755)


def inject_docs_into_dockerfile(task_dir):
    dockerfile = task_dir / "environment" / "Dockerfile"
    if not dockerfile.exists():
        log(f"Dockerfile not found: {dockerfile}")
        return False
    content = dockerfile.read_text()
    needle = "COPY src/ /app/src/"
    if needle not in content:
        log(f"Pattern {needle!r} not in Dockerfile")
        return False
    if "COPY docs/" not in content:
        content = content.replace(needle, needle + "\nCOPY docs/ /app/docs/")
        dockerfile.write_text(content)
    return True


def inject_step_verifier(task_dir, verification, tests_to_create=None):
    """Replace the standard test runner with one that processes the step's
    verification[] array. Handles existing_test, new_test, typecheck, build,
    execution types. Leaves tests/Dockerfile intact."""
    test_sh = task_dir / "tests" / "test.sh"

    if not verification:
        verification = []

    # Build the shell script that runs each verification entry
    cmds = []
    for i, v in enumerate(verification):
        vtype = v.get("type", "execution")
        cmd = v.get("command", "")
        reason = v.get("reason", f"verification {i}")
        safe_reason = reason.replace('"', '\\"')

        if vtype == "existing_test":
            cmds.append(textwrap.dedent(f'''\
            echo "[verif {i}] existing_test: {safe_reason}"
            echo "  Running: {cmd}"
            {cmd} --junitxml=/logs/verifier/v{i}.xml 2>&1 | tee /logs/verifier/v{i}.log
            rc=$?
            echo "  Exit code: $rc"
            '''))
        elif vtype == "new_test":
            cmds.append(textwrap.dedent(f'''\
            echo "[verif {i}] new_test: {safe_reason}"
            echo "  Running: {cmd}"
            {cmd} --junitxml=/logs/verifier/v{i}.xml 2>&1 | tee /logs/verifier/v{i}.log
            rc=$?
            echo "  Exit code: $rc"
            '''))
        else:
            cmds.append(textwrap.dedent(f'''\
            echo "[verif {i}] {vtype}: {safe_reason}"
            echo "  Running: {cmd}"
            eval {cmd} 2>&1 | tee /logs/verifier/v{i}.log
            rc=$?
            echo "  Exit code: $rc"
            '''))

    cmds_str = "\n".join(cmds)

    # Build tests_to_create verification inline
    ttc_checks = ""
    if tests_to_create:
        checks = []
        for t in tests_to_create:
            checks.append(f'''echo "[ttc] Checking test '{t}' exists in patch..."
if ! grep -q "{t}" /logs/artifacts/model.patch 2>/dev/null; then
  echo "[ttc] MISSING: test '{t}' not found in model.patch"
  TTC_FAILED=1
else
  echo "[ttc] FOUND: test '{t}' in model.patch"
fi''')
        ttc_checks = "\n".join(checks)

    new_test_sh = textwrap.dedent(f"""\
    #!/bin/bash
    set -uo pipefail
    trap 'mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt 2>/dev/null || true' EXIT
    cd /app || exit 6

    MODEL_PATCH="/logs/artifacts/model.patch"
    if [ -f "$MODEL_PATCH" ]; then
      if ! git apply "$MODEL_PATCH" 2>/dev/null; then
        echo '{{"reward": 0, "f2p_total": 0, "f2p_passed": 0, "p2p_total": 0, "p2p_passed": 0, "f2p": 0.0, "p2p": 0.0, "partial": 0.0}}' > /logs/verifier/reward.json
        exit 0
      fi
      git add -A && git commit -m "Apply model patch" 2>/dev/null || true
    fi

    set +e
    N_FAILED=0
    N_TOTAL=0
    {cmds_str}
    {ttc_checks}
    TTC_FAILED=${{TTC_FAILED:-0}}
    export TTC_FAILED
    export HAS_TTC={1 if tests_to_create else 0}
    set -e

    # Build reward.json from per-entry results
    """)
    new_test_sh += textwrap.dedent("""\
    python3 << 'PYEOF'
    import json, glob, xml.etree.ElementTree as ET, os
    results = {}
    for xml_file in sorted(glob.glob('/logs/verifier/v*.xml')):
        try:
            tree = ET.parse(xml_file)
            for tc in tree.iter('testcase'):
                name = tc.get('name', 'unknown')
                failed = any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)
                results[name] = 0 if failed else 1
        except Exception:
            pass
    if not results:
        for log_file in sorted(glob.glob('/logs/verifier/v*.log')):
            import re
            m = re.search(r'Exit code: (\\d+)', open(log_file).read())
            if m:
                results['verif_' + log_file.split('/')[-1].replace('.log','')] = 1 if m.group(1) == '0' else 0
    ttc_failed = int(os.environ.get('TTC_FAILED', '0'))
    ttc_ok = 0 if ttc_failed else 1
    has_ttc = int(os.environ.get('HAS_TTC', '0'))
    passed = sum(1 for v in results.values() if v == 1) + ttc_ok
    total = len(results) + has_ttc
    reward = 1 if total > 0 and passed == total else 0
    out = {
        'reward': reward,
        'f2p_total': total,
        'f2p_passed': passed,
        'p2p_total': 0,
        'p2p_passed': 0,
        'f2p': passed / total if total > 0 else 0.0,
        'p2p': 1.0,
        'partial': 1.0 if passed == total else 0.0,
        'detail': {'results': results, 'ttc_ok': bool(ttc_ok)}
    }
    with open('/logs/verifier/reward.json', 'w') as f:
        json.dump(out, f)
    print(json.dumps(out))
    PYEOF
    """)

    test_sh.write_text(new_test_sh)
    test_sh.chmod(0o755)


def extract_patch_file(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    patch_file = trials[0] / "artifacts" / "model.patch"
    return patch_file if patch_file.exists() else None


def has_trial_exception(job_dir):
    """Check if the first trial in the job has an exception.txt (agent error)."""
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return False
    return (trials[0] / "exception.txt").exists()

def read_trial_verifier_result(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    reward_file = trials[0] / "verifier" / "reward.json"
    if reward_file.exists():
        return json.loads(reward_file.read_text())
    # Fallback: read from trial result.json
    trial_result = trials[0] / "result.json"
    if trial_result.exists():
        r = json.loads(trial_result.read_text())
        vr = r.get("verifier_result") or {}
        rewards = vr.get("rewards") or {}
        reward = rewards.get("reward", -1)
        return {"f2p_total": 0, "f2p_passed": 0, "reward": reward}
    return None


def read_file_from_patch(patch_path, file_pattern):
    """Extract the full content of a file from a git patch."""
    if not patch_path or patch_path.stat().st_size == 0:
        return None
    content = patch_path.read_text()
    file_diffs = re.split(r'^diff --git ', content, flags=re.MULTILINE)
    for diff in file_diffs:
        if not diff.strip():
            continue
        m = re.match(r'^a/' + file_pattern + r' b/' + file_pattern, diff)
        if not m:
            continue
        lines = diff.split('\n')
        in_hunk = False
        extracted = []
        for line in lines:
            line = line.rstrip('\r')
            if line.startswith('@@ '):
                in_hunk = True
                continue
            if line.startswith('--- ') or line.startswith('+++ ') or line.startswith('\\ '):
                continue
            if in_hunk:
                extracted.append(line[1:] if line.startswith('+') else line[1:])
        return '\n'.join(extracted) + '\n' if extracted else None
    return None


def apply_patch_to_src(task_dir, patch_path):
    """Apply a git model.patch to environment/src/ in a task directory copy.
    
    The patch is a `git diff --binary _baseline HEAD`. We parse each file's
    final content (the '+' lines in each hunk) and write it to the matching
    path under environment/src/.
    """
    if not patch_path or patch_path.stat().st_size == 0:
        return False
    content = patch_path.read_text()
    file_diffs = re.split(r'^diff --git ', content, flags=re.MULTILINE)
    src_dir = task_dir / "environment" / "src"
    applied = 0
    for diff in file_diffs:
        if not diff.strip():
            continue
        m = re.match(r'^a/(\S+) b/\S+', diff)
        if not m:
            continue
        filepath = m.group(1)
        if not filepath.startswith("src/"):
            continue
        lines = diff.split('\n')
        in_hunk = False
        extracted = []
        for line in lines:
            line = line.rstrip('\r')
            if line.startswith('@@ '):
                in_hunk = True
                continue
            if line.startswith('--- ') or line.startswith('+++ ') or line.startswith('\\ '):
                continue
            if in_hunk:
                extracted.append(line[1:] if line.startswith('+') else line[1:])
        if extracted:
            target = src_dir / filepath[4:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('\n'.join(extracted) + '\n')
            applied += 1
    log(f"Applied {applied} files from patch to src/")
    return applied > 0


def save_results(job_dir, config_name, label):
    save_script = PROJECT_ROOT / "experiments" / "_save_results.py"
    if not save_script.exists():
        log(f"Save script not found: {save_script}")
        return
    subprocess.run(
        ["python3", str(save_script), str(job_dir), config_name, str(RESULTS_FILE)],
        cwd=PROJECT_ROOT, capture_output=True, text=True
    )


def cleanup_temp_dirs():
    tempdir = Path(tempfile.gettempdir())
    count = 0
    for d in tempdir.iterdir():
        if d.is_dir() and d.name.startswith(TEMP_PREFIX):
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    if count:
        log(f"Cleaned up {count} temp dirs")


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--cleanup":
            cleanup_temp_dirs()
            return
        if sys.argv[1] == "--check":
            check_prerequisites()
            return

    check_prerequisites()

    # ================================================================
    # PHASE 0: Planning agent → plan.json
    # ================================================================
    log("=" * 60)
    log("PHASE 0: Planning agent — analyze and produce plan.json")
    log("=" * 60)

    before_job = find_latest_job_dir()
    plan_dir = copy_task_dir(TASK_DIR)
    write_pre_artifacts(plan_dir)

    run_pier(PLAN_CONFIG, plan_dir)
    phase0_job = find_latest_job_dir(before=before_job)

    if not phase0_job:
        log("No job dir for Phase 0 — aborting")
        shutil.rmtree(plan_dir, ignore_errors=True)
        sys.exit(1)

    patch_file = extract_patch_file(phase0_job)
    plan_json_str = read_file_from_patch(patch_file, r'docs/plan\.json')
    if not plan_json_str:
        log("plan.json not found in Phase 0 output — aborting")
        shutil.rmtree(plan_dir, ignore_errors=True)
        sys.exit(1)

    plan = json.loads(plan_json_str)
    steps = plan.get("steps", [])
    log(f"Plan parsed: {len(steps)} steps")

    save_results(phase0_job, "tierB-plan", "Phase 0")
    shutil.rmtree(plan_dir, ignore_errors=True)

    if not steps:
        log("Plan has no steps — nothing to implement")
        sys.exit(0)

    # ================================================================
    # PHASES 1-N: Per-step loop
    #
    # cumulative_dir tracks the full code state after each step.
    # Each step's implement trial copies from it (so the Docker image
    # includes all previous steps' changes). The model.patch from the
    # trial = only the current step's changes (since _baseline in the
    # container already includes previous steps). We apply that patch
    # back to cumulative_dir for the next step.
    # ================================================================
    cumulative_dir = copy_task_dir(TASK_DIR)
    learnings = []

    for step in steps:
        step_id = step["id"]
        step_patch_file = None  # patch for THIS step only

        log("=" * 60)
        log(f"STEP {step_id}: {step['objective']}")
        log("=" * 60)

        # --- Implement + verify loop ---
        # Starts from cumulative_dir (has steps 1..N-1), so the agent
        # sees the full previous state. The Dockerfile's _baseline will
        # include previous steps; model.patch captures only step N.
        implement_ok = False

        for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
            log(f"--- Implement attempt {attempt}/{MAX_REPAIR_ATTEMPTS} ---")

            # Build from cumulative state
            build_dir = copy_task_dir(cumulative_dir)
            docs_dir = build_dir / "environment" / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)

            (docs_dir / "current-step.json").write_text(json.dumps(step, indent=2))

            if attempt > 1:
                (docs_dir / "repair-feedback.json").write_text(json.dumps({
                    "step": step,
                    "attempt": attempt,
                    "previous_failure": f"Step-specific tests failed on attempt {attempt - 1}"
                }))

            inject_docs_into_dockerfile(build_dir)
            inject_step_verifier(
                build_dir,
                step.get("verification", []),
                step.get("tests_to_create", [])
            )
            write_pre_artifacts(build_dir)

            before_impl = find_latest_job_dir()
            run_pier(IMPLEMENT_CONFIG, build_dir)
            impl_job = find_latest_job_dir(before=before_impl)

            if impl_job:
                vr = read_trial_verifier_result(impl_job)
                f2p = f"{vr.get('f2p_passed', 0)}/{vr.get('f2p_total', 0)}" if vr else "N/A"
                log(f"Step {step_id} attempt {attempt}: f2p={f2p}")

                save_results(impl_job, f"tierB-step{step_id}-impl", f"Step {step_id} impl attempt {attempt}")

                # Check step-level tests passed (all listed tests green)
                step_passed = False
                trial_errored = has_trial_exception(impl_job)

                if trial_errored:
                    log(f"Agent errored in attempt {attempt} — treating as failed")
                elif vr and vr.get("f2p_total", 0) > 0:
                    step_passed = vr["f2p_passed"] == vr["f2p_total"]
                elif vr and vr.get("f2p_total", 0) == 0:
                    step_patch_file = extract_patch_file(impl_job)
                    if step_patch_file and step_patch_file.stat().st_size > 0:
                        log("No step-specific tests listed but patch exists — treating as passed")
                        step_passed = True
                    else:
                        log("No step-specific tests listed and no changes made — treating as failed")

                if step_passed:
                    log(f"Step {step_id} passed on attempt {attempt}")
                    step_patch_file = extract_patch_file(impl_job)
                    implement_ok = True
                    shutil.rmtree(build_dir, ignore_errors=True)
                    break
            else:
                log(f"No Pier job produced for attempt {attempt}")

            shutil.rmtree(build_dir, ignore_errors=True)

        if not implement_ok:
            log(f"Step {step_id} failed after {MAX_REPAIR_ATTEMPTS} attempts — continuing to next step")
            learnings.append({"step": step_id, "passed": False, "attempts": MAX_REPAIR_ATTEMPTS})
            continue

        # --- Apply step patch to cumulative dir ---
        # After successful implementation, apply this step's changes
        # to cumulative_dir so the NEXT step builds on top of them.
        if step_patch_file:
            apply_patch_to_src(cumulative_dir, step_patch_file)

        # --- Review ---
        # Reviewer gets a fresh copy of cumulative_dir (pre-step state),
        # then we apply the step patch and write step.patch to docs/
        # so the reviewer can see exactly what changed.
        log(f"--- Reviewing step {step_id} ---")

        review_dir = copy_task_dir(cumulative_dir)
        docs_dir = review_dir / "environment" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        (docs_dir / "current-step.json").write_text(json.dumps(step, indent=2))

        # Apply step patch so reviewer sees the code post-changes
        if step_patch_file:
            apply_patch_to_src(review_dir, step_patch_file)

            # Also include the raw patch file so the reviewer can read the diff
            shutil.copy2(step_patch_file, docs_dir / "step.patch")
            log(f"Copied step patch to docs/step.patch ({step_patch_file.stat().st_size} bytes)")

        inject_docs_into_dockerfile(review_dir)
        write_pre_artifacts(review_dir)

        before_review = find_latest_job_dir()
        run_pier(REVIEW_CONFIG, review_dir)
        review_job = find_latest_job_dir(before=before_review)

        review_data = None
        if review_job:
            save_results(review_job, f"tierB-step{step_id}-review", f"Step {step_id} review")

            rp = extract_patch_file(review_job)
            rj_str = read_file_from_patch(rp, r'docs/review\.json')
            if rj_str:
                try:
                    review_data = json.loads(rj_str)
                    log(f"Review: approved={review_data.get('approved')}, rework={review_data.get('requires_rework')}")

                    if not review_data.get("approved"):
                        log(f"  Not approved: {review_data.get('feedback', '')[:300]}")
                        if review_data.get("requires_rework"):
                            log(f"  Step needs rework — would re-enter repair loop")
                        if review_data.get("plan_updates"):
                            log(f"  Plan update suggested: {review_data['plan_updates'][:300]}")
                except json.JSONDecodeError:
                    log("Review JSON unparseable")

        shutil.rmtree(review_dir, ignore_errors=True)

        # --- Persist ---
        learnings.append({
            "step": step_id,
            "passed": True,
            "attempts": attempt,
            "review": review_data,
        })

    # ================================================================
    # Summary
    # ================================================================
    log("=" * 60)
    log("TIER B COMPLETE")
    log(f"  Steps in plan: {len(steps)}")
    log(f"  Steps implemented: {sum(1 for l in learnings if l['passed'])}")
    log(f"  Steps failed: {sum(1 for l in learnings if not l['passed'])}")
    log("=" * 60)

    if phase0_job:
        log(f"  Phase 0 (plan):  {phase0_job.name}")
    for l in learnings:
        status = "PASS" if l["passed"] else "FAIL"
        log(f"  Step {l['step']}: {status} ({l['attempts']} attempts)")

    learnings_file = PROJECT_ROOT / "experiments" / "tierB-learnings.json"
    learnings_file.write_text(json.dumps(learnings, indent=2))
    log(f"Learnings written to {learnings_file}")

    shutil.rmtree(cumulative_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
