#!/usr/bin/env python3
"""Exp3b: Two-phase research → implement wrapper.

Usage:
    python3 experiments/run-exp3b.py               # runs on dummy task
    python3 experiments/run-exp3b.py --cleanup      # removes all temp dirs
    python3 experiments/run-exp3b.py --check        # verify prerequisites
"""
import subprocess, sys, os, shutil, tempfile, re, time, textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = PROJECT_ROOT / "deep-swe/tasks/dummy-adaptix-alias"
PIER_CONFIGS = PROJECT_ROOT / "pier-configs"
RESULTS_FILE = PROJECT_ROOT / "experiments/results.json"

RESEARCH_CONFIG = PIER_CONFIGS / "opencode-deepseek-exp3b-research-dummy.yaml"
IMPLEMENT_CONFIG = PIER_CONFIGS / "opencode-deepseek-exp3b-implement-dummy.yaml"

TEMP_PREFIX = "exp3b-task-"

def log(msg):
    print(f"[exp3b] {msg}")

def check_prerequisites():
    missing = []
    for cmd in ["pier", "docker"]:
        if not shutil.which(cmd):
            missing.append(cmd)
    if missing:
        log(f"Missing prerequisites: {', '.join(missing)}")
        sys.exit(1)
    for cfg in [RESEARCH_CONFIG, IMPLEMENT_CONFIG]:
        if not cfg.exists():
            log(f"Config not found: {cfg}")
            sys.exit(1)
    if not TASK_DIR.exists():
        log(f"Task not found: {TASK_DIR}")
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
    job_dirs = sorted([d for d in jobs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()])
    if not job_dirs:
        return None
    latest = job_dirs[-1]
    if before is not None and latest == before:
        return None
    return latest

def create_research_task_copy():
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    shutil.copytree(TASK_DIR, tmp, dirs_exist_ok=True, symlinks=True)

    pre_art = tmp / "pre_artifacts.sh"
    custom_pre = textwrap.dedent("""\
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
    with open(pre_art, 'w') as f:
        f.write(custom_pre)
    os.chmod(pre_art, 0o755)
    return tmp

def extract_docs_from_patch(patch_path, target_dir):
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        log("Patch file is empty or missing")
        return False

    content = patch_path.read_text()
    file_diffs = re.split(r'^diff --git ', content, flags=re.MULTILINE)

    extracted_count = 0
    for diff in file_diffs:
        if not diff.strip():
            continue
        m = re.match(r'^a/(docs/\S+) b/\S+', diff)
        if not m:
            continue

        filepath = m.group(1)
        lines = diff.split('\n')

        in_hunk = False
        extracted = []
        for line in lines:
            line = line.rstrip('\r')
            if line.startswith('@@ '):
                in_hunk = True
                continue
            if line.startswith('--- ') or line.startswith('+++ '):
                continue
            if line.startswith('\\ '):
                continue
            if in_hunk:
                if line.startswith('+'):
                    extracted.append(line[1:])
                elif line.startswith(' '):
                    extracted.append(line[1:])

        if extracted:
            full_path = target_dir / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text('\n'.join(extracted) + '\n')
            log(f"Extracted: {filepath} ({len(extracted)} lines)")
            extracted_count += 1

    if extracted_count == 0:
        log("No docs/ files found in patch")
        return False

    log(f"Extracted {extracted_count} docs/ files from Phase 1")
    return True

def modify_dockerfile_for_docs(task_dir):
    dockerfile = task_dir / "environment" / "Dockerfile"
    if not dockerfile.exists():
        log(f"Dockerfile not found at {dockerfile}")
        return False

    content = dockerfile.read_text()
    needle = "COPY src/ /app/src/"
    if needle not in content:
        log("Could not find COPY src/ line in Dockerfile")
        return False

    new_content = content.replace(
        needle,
        "COPY src/ /app/src/\nCOPY docs/ /app/docs/"
    )
    dockerfile.write_text(new_content)
    log("Modified Dockerfile to include docs/")
    return True

def create_implement_task_copy(patch_file):
    tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    shutil.copytree(TASK_DIR, tmp, dirs_exist_ok=True, symlinks=True)

    # Extract docs into environment/ so they're included when pier copies
    # environment/ to the Docker build context (agent-build-context).
    # The task's environment/ already has src -> ../src symlink and tests/
    # that get materialized via shutil.copytree(symlinks=False).
    docs_ok = extract_docs_from_patch(patch_file, tmp / "environment")
    if not docs_ok:
        shutil.rmtree(tmp, ignore_errors=True)
        return None

    # Modify Dockerfile to COPY docs/ into the container
    modify_dockerfile_for_docs(tmp)
    return tmp

def save_results(job_dir, config_name, phase_label):
    save_script = PROJECT_ROOT / "experiments" / "_save_results.py"
    if not save_script.exists():
        log(f"Save script not found at {save_script}")
        return
    result = subprocess.run(
        ["python3", str(save_script), str(job_dir), config_name, str(RESULTS_FILE)],
        cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    if result.returncode == 0:
        log(f"Saved {phase_label} results to {RESULTS_FILE}")
    else:
        log(f"Failed to save {phase_label} results: {result.stderr}")

def cleanup_temp_dirs():
    tempdir = Path(tempfile.gettempdir())
    count = 0
    for d in tempdir.iterdir():
        if d.is_dir() and d.name.startswith(TEMP_PREFIX):
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    if count:
        log(f"Cleaned up {count} temp directories")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--cleanup":
            cleanup_temp_dirs()
            return
        if sys.argv[1] == "--check":
            check_prerequisites()
            return

    check_prerequisites()

    # ============================================================
    # PHASE 1: Research
    # ============================================================
    log("=" * 60)
    log("PHASE 1: Research agent — document the codebase")
    log("=" * 60)

    before_job = find_latest_job_dir()
    research_task_dir = create_research_task_copy()
    log(f"Created research task dir: {research_task_dir}")

    success = run_pier(RESEARCH_CONFIG, research_task_dir)

    phase1_job = find_latest_job_dir(before=before_job)
    if not phase1_job:
        log("No new job directory found for Phase 1 — aborting")
        shutil.rmtree(research_task_dir, ignore_errors=True)
        sys.exit(1)

    log(f"Phase 1 job: {phase1_job.name}")

    trial_dirs = [d for d in phase1_job.iterdir() if d.is_dir()]
    if not trial_dirs:
        log("No trial directory in Phase 1 job")
        shutil.rmtree(research_task_dir, ignore_errors=True)
        sys.exit(1)

    artifacts_dir = trial_dirs[0] / "artifacts"
    patch_file = artifacts_dir / "model.patch" if artifacts_dir.exists() else None
    if not patch_file or not patch_file.exists():
        log("model.patch not found — Research agent may not have produced output")
        shutil.rmtree(research_task_dir, ignore_errors=True)
        sys.exit(1)

    log(f"Phase 1 patch size: {patch_file.stat().st_size} bytes")

    # ============================================================
    # PHASE 2: Implementation
    # ============================================================
    log("=" * 60)
    log("PHASE 2: Implementation agent — implement with docs pre-populated")
    log("=" * 60)

    before_job2 = find_latest_job_dir()
    implement_task_dir = create_implement_task_copy(patch_file)

    if implement_task_dir is None:
        shutil.rmtree(research_task_dir, ignore_errors=True)
        log("Exp3b incomplete: no documentation was produced by Phase 1")
        sys.exit(1)

    log(f"Created implement task dir: {implement_task_dir}")

    success = run_pier(IMPLEMENT_CONFIG, implement_task_dir)

    phase2_job = find_latest_job_dir(before=before_job2)

    # Save results
    if phase1_job:
        save_results(phase1_job, "exp3b-research", "Phase 1")
    if phase2_job:
        save_results(phase2_job, "exp3b-implement", "Phase 2")

    # Cleanup
    shutil.rmtree(research_task_dir, ignore_errors=True)
    if implement_task_dir:
        shutil.rmtree(implement_task_dir, ignore_errors=True)

    log("=" * 60)
    log("Exp3b complete")
    log(f"  Phase 1 (research): {phase1_job.name}")
    log(f"  Phase 2 (implement): {phase2_job.name if phase2_job else 'N/A'}")
    log("=" * 60)

if __name__ == "__main__":
    main()
