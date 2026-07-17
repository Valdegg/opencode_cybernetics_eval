#!/usr/bin/env python3
"""Tier B: Task Decomposition Loop wrapper.

Two-phase orchestration:
  Phase 0: Planning agent (perm-locked, docs/ only) → plan.json
  Phase 1-N: Per-step implement → verify → review → repair → persist

Usage:
    python3 experiments/run-tierB.py
    python3 experiments/run-tierB.py --cleanup
    python3 experiments/run-tierB.py --check
"""
import subprocess, sys, os, shutil, tempfile, re, json, time, textwrap
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
    job_dirs = sorted([d for d in jobs_dir.iterdir() if d.is_dir() and d[0].isdigit()])
    if not job_dirs:
        return None
    latest = job_dirs[-1]
    if before is not None and latest == before:
        return None
    return latest


def copy_task_base():
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    shutil.copytree(TASK_DIR, tmp, dirs_exist_ok=True, symlinks=True)
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


def inject_step_verifier(task_dir, tests):
    """Replace the standard test runner with a step-specific one."""
    test_sh = task_dir / "tests" / "test.sh"
    test_dockerfile = task_dir / "tests" / "Dockerfile"
    test_names = " ".join(tests)

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
    python -m pytest {test_names} -v --junitxml=/logs/verifier/results.xml 2>&1 | tee /logs/verifier/step.log
    rc=$?
    set -e

    passed=$(python3 -c "
    import xml.etree.ElementTree as ET
    tree = ET.parse('/logs/verifier/results.xml')
    print(sum(1 for tc in tree.iter('testcase') if not any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
    ")
    failed=$(python3 -c "
    import xml.etree.ElementTree as ET
    tree = ET.parse('/logs/verifier/results.xml')
    print(sum(1 for tc in tree.iter('testcase') if any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
    ")
    total=$((passed + failed))

    python3 -c "
    import json
    out = {{
        'reward': 1 if {len(test_names)} > 0 and $failed == 0 else 0,
        'f2p_total': $total,
        'f2p_passed': $passed,
        'p2p_total': 0,
        'p2p_passed': 0,
        'f2p': $passed / $total if $total > 0 else 0.0,
        'p2p': 1.0,
        'partial': 1.0 if $failed == 0 else 0.0,
    }}
    with open('/logs/verifier/reward.json', 'w') as f:
        json.dump(out, f)
    print(json.dumps(out))
    "
    """)

    test_sh.write_text(new_test_sh)
    test_sh.chmod(0o755)

    new_df = textwrap.dedent(f"""\
    FROM python:3.12-slim
    RUN apt-get update -qq && apt-get install -y -qq git && rm -rf /var/lib/apt/lists/*
    WORKDIR /app
    ENV PYTHONPATH=/app/src
    COPY src/ /app/src/
    COPY test.sh /tests/test.sh
    RUN pip install --no-cache-dir pytest && \\
        git init && \\
        git config user.email "dev@example.com" && \\
        git config user.name "Developer" && \\
        git add -A && \\
        git commit -m "Baseline" && \\
        git tag _baseline && \\
        chmod +x /tests/test.sh
    """)
    test_dockerfile.write_text(new_df)


def extract_patch_trial(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    patch_file = trials[0] / "artifacts" / "model.patch"
    return patch_file if patch_file.exists() else None


def read_trial_verifier_result(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    reward_file = trials[0] / "verifier" / "reward.json"
    if not reward_file.exists():
        return None
    return json.loads(reward_file.read_text())


def read_file_from_patch(patch_path, file_pattern):
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
    """Apply model.patch to src/ files in a task directory copy."""
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
    log(f"Applied {applied} files from patch to reviewer task dir")
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
    # PHASE 0: Planning
    # ================================================================
    log("=" * 60)
    log("PHASE 0: Planning agent — analyze and produce plan.json")
    log("=" * 60)

    before_job = find_latest_job_dir()
    plan_task_dir = copy_task_base()
    write_pre_artifacts(plan_task_dir)
    log(f"Task dir: {plan_task_dir}")

    success = run_pier(PLAN_CONFIG, plan_task_dir)
    phase0_job = find_latest_job_dir(before=before_job)

    if not phase0_job:
        log("No job dir for Phase 0 — aborting")
        shutil.rmtree(plan_task_dir, ignore_errors=True)
        sys.exit(1)

    patch_file = extract_patch_trial(phase0_job)
    plan_json_str = read_file_from_patch(patch_file, r'docs/plan\.json')
    if not plan_json_str:
        log("plan.json not found in Phase 0 output — aborting")
        shutil.rmtree(plan_task_dir, ignore_errors=True)
        sys.exit(1)

    plan = json.loads(plan_json_str)
    steps = plan.get("steps", [])
    log(f"Plan parsed: {len(steps)} steps")

    save_results(phase0_job, "tierB-plan", "Phase 0")
    shutil.rmtree(plan_task_dir, ignore_errors=True)

    if not steps:
        log("Plan has no steps — nothing to implement")
        sys.exit(0)

    # ================================================================
    # PHASE 1-N: Per-step loop
    # ================================================================
    accumulated_patch = None
    learnings = []

    for step in steps:
        step_id = step["id"]
        log("=" * 60)
        log(f"STEP {step_id}: {step['objective']}")
        log("=" * 60)

        # --- Implement + verify loop ---
        implement_ok = False
        for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
            log(f"--- Implement attempt {attempt}/{MAX_REPAIR_ATTEMPTS} ---")

            task_dir = copy_task_base()
            docs_dir = task_dir / "environment" / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Write step details
            (docs_dir / "current-step.json").write_text(json.dumps(step, indent=2))

            # If repair, write repair feedback
            if attempt > 1:
                (docs_dir / "repair-feedback.json").write_text(json.dumps({
                    "step": step,
                    "attempt": attempt,
                    "previous_failure": f"Tests failed on attempt {attempt - 1}"
                }))

            inject_docs_into_dockerfile(task_dir)
            inject_step_verifier(task_dir, step.get("tests", []))
            write_pre_artifacts(task_dir)

            before_impl = find_latest_job_dir()
            run_pier(IMPLEMENT_CONFIG, task_dir)
            impl_job = find_latest_job_dir(before=before_impl)

            if impl_job:
                vr = read_trial_verifier_result(impl_job)
                log(f"Step {step_id} attempt {attempt}: f2p={vr.get('f2p_passed', 0)}/{vr.get('f2p_total', 0)}")

                save_results(impl_job, f"tierB-step{step_id}-impl", f"Step {step_id} impl attempt {attempt}")

                # Check step-level tests passed
                if vr and vr.get("f2p_total", 0) > 0 and vr.get("f2p_passed") == vr.get("f2p_total"):
                    log(f"Step {step_id} passed on attempt {attempt}")
                    accumulated_patch = extract_patch_trial(impl_job)
                    implement_ok = True
                    shutil.rmtree(task_dir, ignore_errors=True)
                    break
                elif vr and vr.get("f2p_total", 0) == 0:
                    log(f"No step-specific tests found — treating as passed")
                    accumulated_patch = extract_patch_trial(impl_job)
                    implement_ok = True
                    shutil.rmtree(task_dir, ignore_errors=True)
                    break

            shutil.rmtree(task_dir, ignore_errors=True)

        if not implement_ok:
            log(f"Step {step_id} failed after {MAX_REPAIR_ATTEMPTS} attempts — continuing")
            continue

        # --- Review ---
        log(f"--- Reviewing step {step_id} ---")

        review_dir = copy_task_base()
        docs_dir = review_dir / "environment" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "current-step.json").write_text(json.dumps(step, indent=2))

        # Apply accumulated patch so reviewer sees the code post-changes
        if accumulated_patch:
            apply_patch_to_src(review_dir, accumulated_patch)

        inject_docs_into_dockerfile(review_dir)
        write_pre_artifacts(review_dir)

        before_review = find_latest_job_dir()
        run_pier(REVIEW_CONFIG, review_dir)
        review_job = find_latest_job_dir(before=before_review)

        if review_job:
            save_results(review_job, f"tierB-step{step_id}-review", f"Step {step_id} review")

            review_patch = extract_patch_trial(review_job)
            review_json_str = read_file_from_patch(review_patch, r'docs/review\.json')
            if review_json_str:
                try:
                    review = json.loads(review_json_str)
                    log(f"Review: approved={review.get('approved')}, rework={review.get('requires_rework')}")

                    if not review.get("approved"):
                        log(f"Step {step_id} not approved: {review.get('feedback', '')[:200]}")
                        if review.get("requires_rework"):
                            # TODO: re-enter implement loop for this step
                            pass

                    if review.get("plan_updates"):
                        log(f"Plan updates suggested: {review['plan_updates'][:200]}")
                except json.JSONDecodeError:
                    log(f"Review JSON unparseable — continuing")

        shutil.rmtree(review_dir, ignore_errors=True)

        # --- Persist ---
        learnings.append({
            "step": step_id,
            "attempts": attempt if implement_ok else MAX_REPAIR_ATTEMPTS,
            "passed": implement_ok,
            "review": review_json_str if review_job and review_json_str else None
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
        log(f"  Step {l['step']}: {'PASS' if l['passed'] else 'FAIL'} ({l['attempts']} attempts)")

    # Write learnings
    learnings_file = PROJECT_ROOT / "experiments" / "tierB-learnings.json"
    learnings_file.write_text(json.dumps(learnings, indent=2))
    log(f"Learnings written to {learnings_file}")


if __name__ == "__main__":
    main()
