#!/usr/bin/env python3
"""Tier A: Research + Planning → Implementation.

Phase 1: Research agent (perm-locked to docs/) produces repository-analysis.md + plan.json.
Phase 2: Implementation agent (prompted to read and follow docs/) implements the plan.
Final: Verifier scores the result.

Usage:
    python3 experiments/run-tierA.py
    python3 experiments/run-tierA.py --cleanup
    python3 experiments/run-tierA.py --check
"""
import subprocess, sys, os, shutil, tempfile, re, json, textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASK_DIR = PROJECT_ROOT / "deep-swe/tasks/dummy-adaptix-alias"
PIER_CONFIGS = PROJECT_ROOT / "pier-configs"
RESULTS_FILE = PROJECT_ROOT / "experiments/results.json"

RESEARCH_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierA-research-dummy.yaml"
IMPLEMENT_CONFIG = PIER_CONFIGS / "opencode-deepseek-tierA-implement-dummy.yaml"

TEMP_PREFIX = "tierA-task-"


def log(msg):
    print(f"[tierA] {msg}")


def check_prerequisites():
    missing = []
    for cmd in ["pier", "docker"]:
        if not shutil.which(cmd):
            missing.append(cmd)
    for cfg in [RESEARCH_CONFIG, IMPLEMENT_CONFIG]:
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


def extract_patch_file(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name]
    if not trials:
        return None
    patch_file = trials[0] / "artifacts" / "model.patch"
    return patch_file if patch_file.exists() else None


def extract_docs_from_patch(patch_path, target_dir):
    if not patch_path or patch_path.stat().st_size == 0:
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
            if line.startswith('--- ') or line.startswith('+++ ') or line.startswith('\\ '):
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
    log(f"Extracted {extracted_count} docs/ files")
    return extracted_count > 0


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

    # ============================================================
    # PHASE 1: Research + Planning
    # ============================================================
    log("=" * 60)
    log("PHASE 1: Research agent — explore, document, plan")
    log("=" * 60)

    before_job = find_latest_job_dir()
    research_dir = copy_task_base()
    write_pre_artifacts(research_dir)
    log(f"Research task dir: {research_dir}")

    run_pier(RESEARCH_CONFIG, research_dir)
    phase1_job = find_latest_job_dir(before=before_job)

    if not phase1_job:
        log("No job dir for Phase 1 — aborting")
        shutil.rmtree(research_dir, ignore_errors=True)
        sys.exit(1)

    patch_file = extract_patch_file(phase1_job)
    if not patch_file or patch_file.stat().st_size == 0:
        log("Research agent produced no output — aborting")
        shutil.rmtree(research_dir, ignore_errors=True)
        sys.exit(1)

    save_results(phase1_job, "tierA-research", "Phase 1")
    log(f"Phase 1 complete: {phase1_job.name} ({patch_file.stat().st_size} bytes)")

    # ============================================================
    # PHASE 2: Implementation
    # ============================================================
    log("=" * 60)
    log("PHASE 2: Implementation agent — read docs/ and follow plan")
    log("=" * 60)

    before_job2 = find_latest_job_dir()
    implement_dir = copy_task_base()

    # Extract docs from research patch into environment/ so they're
    # included in the Docker build context
    extract_docs_from_patch(patch_file, implement_dir / "environment")
    inject_docs_into_dockerfile(implement_dir)
    write_pre_artifacts(implement_dir)

    log(f"Implement task dir: {implement_dir}")

    run_pier(IMPLEMENT_CONFIG, implement_dir)
    phase2_job = find_latest_job_dir(before=before_job2)

    if phase2_job:
        save_results(phase2_job, "tierA-implement", "Phase 2")
        log(f"Phase 2 complete: {phase2_job.name}")
    else:
        log("No job dir for Phase 2 — implementation may have failed")

    # ============================================================
    # Summary
    # ============================================================
    log("=" * 60)
    log("TIER A COMPLETE")
    log(f"  Phase 1 (research): {phase1_job.name}")
    log(f"  Phase 2 (implement): {phase2_job.name if phase2_job else 'N/A'}")
    log("=" * 60)

    # Cleanup
    shutil.rmtree(research_dir, ignore_errors=True)
    shutil.rmtree(implement_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
