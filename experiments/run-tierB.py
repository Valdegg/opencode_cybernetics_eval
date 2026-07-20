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
import subprocess, sys, os, shutil, tempfile, re, json, textwrap, uuid
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
    safe_name = f"{TEMP_PREFIX}{uuid.uuid4().hex}"
    tmp = Path(tempfile.gettempdir()) / safe_name
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


def inject_step_verifier(task_dir, verification, tests_to_create=None, prior_patches=None):
    """Replace the standard test runner with one that processes the step's
    verification[] array. Handles existing_test, new_test, typecheck, build,
    execution types.

    prior_patches: ordered accepted step patches (steps 1..N-1). They are baked
    into the verifier image and applied (by test.sh) before the trial's
    model.patch, so the verifier reconstructs the cumulative state. The
    model.patch is only step N's increment and would not apply on the original
    verifier base for N>1."""
    test_sh = task_dir / "tests" / "test.sh"

    if not verification:
        verification = []

    # Bake accepted prior-step patches into the verifier image (test.sh applies
    # /tests/prior_*.patch before model.patch). Names are zero-padded so the
    # glob applies them in order.
    if prior_patches:
        tests_dir = task_dir / "tests"
        dockerfile = tests_dir / "Dockerfile"
        df = dockerfile.read_text() if dockerfile.exists() else ""
        copy_lines, idx = [], 0
        for pp in prior_patches:
            pp = Path(pp)
            if not pp.exists() or pp.stat().st_size == 0:
                continue
            name = f"prior_{idx:02d}.patch"
            shutil.copy2(pp, tests_dir / name)
            copy_lines.append(f"COPY {name} /tests/{name}")
            idx += 1
        needle = "COPY test.sh /tests/test.sh"
        if copy_lines and needle in df and "prior_00.patch" not in df:
            dockerfile.write_text(df.replace(needle, needle + "\n" + "\n".join(copy_lines)))

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

    # Reconstruct cumulative state (steps 1..N-1) before the step-N model.patch.
    for p in /tests/prior_*.patch; do
      [ -f "$p" ] || continue
      git apply "$p" 2>/dev/null || git apply --3way "$p" 2>/dev/null || echo "  [prior] $p did not apply cleanly"
    done

    MODEL_PATCH="/logs/artifacts/model.patch"
    if [ -f "$MODEL_PATCH" ]; then
      if   git apply "$MODEL_PATCH" 2>/dev/null; then :
      elif git apply --3way "$MODEL_PATCH" 2>/dev/null; then :
      elif git apply --recount --whitespace=nowarn "$MODEL_PATCH" 2>/dev/null; then :
      else
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
    
    Uses `git apply` in a temporary git repo rather than a hand-rolled parser.
    This correctly handles context lines, additions, removals, and lines
    outside hunk ranges — all of which the previous custom parser got wrong.
    """
    if not patch_path or patch_path.stat().st_size == 0:
        return False
    env_dir = task_dir / "environment"
    gitignore = env_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("__pycache__/\n*.pyc\n")
    subprocess.run(["git", "init"], cwd=env_dir, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=env_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "base", "--allow-empty"],
        cwd=env_dir, capture_output=True
    )
    result = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=env_dir, capture_output=True, text=True
    )
    shutil.rmtree(env_dir / ".git", ignore_errors=True)
    if result.returncode != 0:
        log(f"git apply failed: {result.stderr.strip()}")
        return False
    log("Applied patch via git apply")
    return True


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


def missing_test_functions(patch_file, tests_to_create):
    """Return the subset of tests_to_create that do NOT appear as an added
    `def <name>(...)` in the step's model.patch — i.e. tests the implementer
    was required to write but did not."""
    if not tests_to_create:
        return []
    if not patch_file or not patch_file.exists():
        return list(tests_to_create)
    added = "\n".join(l[1:] for l in patch_file.read_text().splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    missing = []
    for name in tests_to_create:
        if not re.search(r'^\s*def\s+' + re.escape(name) + r'\s*\(', added, re.M):
            missing.append(name)
    return missing


def write_repair_feedback(cumulative_dir, step, next_attempt, reason):
    """Write targeted repair feedback the next implement attempt will read."""
    repair_docs = cumulative_dir / "environment" / "docs"
    repair_docs.mkdir(parents=True, exist_ok=True)
    (repair_docs / "repair-feedback.json").write_text(json.dumps({
        "step": step, "attempt": next_attempt, "previous_failure": reason
    }))


def clear_repair_feedback(cumulative_dir):
    """Remove any stale repair feedback so it does not leak across steps."""
    fb = cumulative_dir / "environment" / "docs" / "repair-feedback.json"
    if fb.exists():
        fb.unlink()


def main():
    if "--modal" in sys.argv:
        global PLAN_CONFIG, IMPLEMENT_CONFIG, REVIEW_CONFIG
        PLAN_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-plan-dummy-modal.yaml"
        IMPLEMENT_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-implement-dummy-modal.yaml"
        REVIEW_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierB-review-dummy-modal.yaml"
        log("Using Modal configs (plan/implement/review)")

    if len(sys.argv) > 1:
        if sys.argv[1] == "--cleanup":
            cleanup_temp_dirs()
            return
        if sys.argv[1] == "--check":
            check_prerequisites()
            return
        if sys.argv[1] == "--task" and len(sys.argv) > 2:
            global TASK_DIR
            candidate = Path(sys.argv[2])
            if candidate.is_dir():
                TASK_DIR = candidate
            else:
                TASK_DIR = PROJECT_ROOT / "deep-swe" / "tasks" / candidate.name
            log(f"Task dir: {TASK_DIR}")
        elif sys.argv[1].startswith("--"):
            pass  # unknown flag, will be caught below

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
    accepted_patches = []  # ordered accepted step patches, layered into each verifier

    for step in steps:
        step_id = step["id"]
        step_patch_file = None  # patch for THIS step only

        log("=" * 60)
        log(f"STEP {step_id}: {step['objective']}")
        log("=" * 60)

        clear_repair_feedback(cumulative_dir)  # no stale feedback from prior step

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

            repair_docs = cumulative_dir / "environment" / "docs"
            repair_docs.mkdir(parents=True, exist_ok=True)
            repair_fb = repair_docs / "repair-feedback.json"
            if attempt > 1 and not repair_fb.exists():
                repair_fb.write_text(json.dumps({
                    "step": step,
                    "attempt": attempt,
                    "previous_failure": f"Step-specific tests failed on attempt {attempt - 1}"
                }))

            inject_docs_into_dockerfile(build_dir)
            inject_step_verifier(
                build_dir,
                step.get("verification", []),
                step.get("tests_to_create", []),
                prior_patches=accepted_patches,
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
                tests_to_create = step.get("tests_to_create", [])
                step_patch_file = extract_patch_file(impl_job)
                patch_ok = bool(step_patch_file and step_patch_file.stat().st_size > 0)

                if trial_errored:
                    log(f"Agent errored in attempt {attempt} — treating as failed")
                    write_repair_feedback(cumulative_dir, step, attempt + 1,
                        "The agent errored before finishing. Implement the step and "
                        "create every function listed in tests_to_create[].")
                elif tests_to_create:
                    # Step declares tests: they MUST be created AND must run.
                    missing = missing_test_functions(step_patch_file, tests_to_create)
                    if missing:
                        log(f"Step {step_id}: required tests NOT created: {missing} — failing")
                        write_repair_feedback(cumulative_dir, step, attempt + 1,
                            "You did NOT create these required test functions: "
                            f"{', '.join(missing)}. Add each as `def <name>(...)` in the "
                            "appropriate test file, then implement the code so they pass.")
                    elif not vr or vr.get("f2p_total", 0) == 0:
                        log(f"Step {step_id}: tests declared but 0 collected/ran — failing")
                        write_repair_feedback(cumulative_dir, step, attempt + 1,
                            "Your test functions exist but 0 tests were collected/run. Ensure "
                            "they are importable, in the right file, and free of import errors, "
                            "then make them pass.")
                    else:
                        step_passed = vr["f2p_passed"] == vr["f2p_total"]
                        if not step_passed:
                            write_repair_feedback(cumulative_dir, step, attempt + 1,
                                f"Only {vr['f2p_passed']}/{vr['f2p_total']} step tests passed. "
                                "Fix the failing ones.")
                elif vr and vr.get("f2p_total", 0) > 0:
                    step_passed = vr["f2p_passed"] == vr["f2p_total"]
                elif patch_ok:
                    log("No step tests declared but patch exists — treating as passed")
                    step_passed = True
                else:
                    log("No step tests declared and no changes made — treating as failed")

                if step_passed:
                    log(f"Step {step_id} passed implement on attempt {attempt}")
                    step_patch_file = extract_patch_file(impl_job)
                    # implement_ok is NOT set here — it is only set AFTER
                    # review approves below.  If implement passes but review
                    # rejects (or subsequent repair attempts error out),
                    # implement_ok stays False and the step is properly
                    # marked as failed.
                    shutil.rmtree(build_dir, ignore_errors=True)

                    # --- Review ---
                    # Patch is NOT yet applied to cumulative_dir.  It is only
                    # applied to the review copy so the reviewer can inspect it.
                    # If review passes, we apply to cumulative_dir below.
                    # If review requests rework, we apply before the continue
                    # so the next attempt builds on top.  If review finally
                    # rejects, the patch is discarded and cumulative_dir stays
                    # clean — no unverified state transition leaks forward.
                    log(f"--- Reviewing step {step_id} ---")
                    review_dir = copy_task_dir(cumulative_dir)
                    docs_dir = review_dir / "environment" / "docs"
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    (docs_dir / "current-step.json").write_text(json.dumps(step, indent=2))

                    if step_patch_file:
                        apply_patch_to_src(review_dir, step_patch_file)
                        shutil.copy2(step_patch_file, docs_dir / "step.patch")
                        log(f"Copied step patch to docs/step.patch ({step_patch_file.stat().st_size} bytes)")

                    inject_docs_into_dockerfile(review_dir)
                    write_pre_artifacts(review_dir)

                    before_review = find_latest_job_dir()
                    run_pier(REVIEW_CONFIG, review_dir)
                    review_job = find_latest_job_dir(before=before_review)

                    review_approved = True
                    review_data = None
                    if review_job:
                        save_results(review_job, f"tierB-step{step_id}-review", f"Step {step_id} review")
                        rp = extract_patch_file(review_job)
                        rj_str = read_file_from_patch(rp, r'docs/review\.json')
                        if rj_str:
                            try:
                                review_data = json.loads(rj_str)
                                log(f"Review: approved={review_data.get('approved')}, rework={review_data.get('requires_rework')}")
                                review_approved = review_data.get("approved", False)
                                if not review_approved:
                                    feedback = review_data.get("feedback", "No feedback")
                                    log(f"  Not approved: {feedback[:300]}")
                                    if review_data.get("requires_rework") and attempt < MAX_REPAIR_ATTEMPTS:
                                        log(f"  Step needs rework — re-entering repair loop")
                                        # Apply patch so the repair attempt builds on it
                                        if step_patch_file:
                                            apply_patch_to_src(cumulative_dir, step_patch_file)
                                        # Write repair feedback for next attempt
                                        repair = {"step": step, "attempt": attempt + 1,
                                                  "previous_failure": feedback}
                                        repair_docs = cumulative_dir / "environment" / "docs"
                                        repair_docs.mkdir(parents=True, exist_ok=True)
                                        (repair_docs / "repair-feedback.json").write_text(
                                            json.dumps(repair))
                                        shutil.rmtree(review_dir, ignore_errors=True)
                                        continue
                                    # Final rejection — exhausted repair attempts.
                                    # Patch is NOT applied to cumulative_dir.
                                    implement_ok = False
                                    if review_data.get("plan_updates"):
                                        log(f"  Plan update: {review_data['plan_updates'][:300]}")
                            except json.JSONDecodeError:
                                log("Review JSON unparseable — treating as approved")

                    shutil.rmtree(review_dir, ignore_errors=True)

                    if review_approved:
                        implement_ok = True
                        # Apply patch to cumulative state only after review passes
                        if step_patch_file:
                            apply_patch_to_src(cumulative_dir, step_patch_file)
                            accepted_patches.append(step_patch_file)
                        log(f"Step {step_id} fully approved")
                    else:
                        log(f"Step {step_id} rejected after {MAX_REPAIR_ATTEMPTS} attempts")
                    break
            else:
                log(f"No Pier job produced for attempt {attempt}")

            shutil.rmtree(build_dir, ignore_errors=True)

        if not implement_ok:
            log(f"Step {step_id} failed after {MAX_REPAIR_ATTEMPTS} attempts — continuing to next step")
            learnings.append({"step": step_id, "passed": False, "attempts": MAX_REPAIR_ATTEMPTS})
            continue

        # --- Persist step learnings ---
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
