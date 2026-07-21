#!/usr/bin/env python3
"""Codebase audit agent — scans for improvements and creates GitHub issues.

Usage:
    export GITHUB_TOKEN=ghp_...
    python3 experiments/run-audit.py                 # audit default task dir
    python3 experiments/run-audit.py --task /path    # audit specific dir
    python3 experiments/run-audit.py --dry-run        # show findings, no issues
    python3 experiments/run-audit.py --poll 3600      # re-audit every hour
"""
import subprocess, sys, os, json, tempfile, shutil, re, time, textwrap, uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_CONFIG = PROJECT_ROOT / "pier-configs" / "opencode-deepseek-audit-dummy.yaml"
TASK_DIR = None  # set dynamically below
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Valdegg/opencode_cybernetics_eval")
TEMP_PREFIX = "audit-task-"

# Track already-filed improvement titles to avoid duplicates
ALREADY_FILED = set()


def log(msg):
    print(f"[audit] {msg}")


def gh_api(method, path, data=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}{path}"
    cmd = ["curl", "-s", "-X", method, url,
           "-H", f"Authorization: token {GITHUB_TOKEN}",
           "-H", "Accept: application/vnd.github.v3+json"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def load_filed_issues():
    """Load titles of existing open issues to avoid duplicates."""
    global ALREADY_FILED
    issues = gh_api("GET", "/issues?state=open&per_page=100")
    if isinstance(issues, list):
        ALREADY_FILED = {i["title"].strip().lower() for i in issues if "pull_request" not in i}


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


def extract_patch_file(job_dir):
    trials = [d for d in job_dir.iterdir() if d.is_dir() and "__" in d.name] if job_dir else []
    if not trials:
        return None
    patch_file = trials[0] / "artifacts" / "model.patch"
    return patch_file if patch_file.exists() else None


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


def run_audit(task_dir):
    """Run the audit agent and return list of improvements."""
    log(f"Auditing: {task_dir}")
    before_job = find_latest_job_dir()
    audit_dir = copy_task_dir(task_dir)

    cmd = [
        "pier", "run",
        "--config", str(AUDIT_CONFIG),
        "--path", str(audit_dir),
        "--n-attempts", "1",
        "--disable-verification",
        "--yes"
    ]
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    audit_job = find_latest_job_dir(before=before_job)
    improvements = []
    if audit_job:
        patch_file = extract_patch_file(audit_job)
        raw = read_file_from_patch(patch_file, r'docs/improvements\.json')
        if raw:
            try:
                improvements = json.loads(raw)
                log(f"Found {len(improvements)} improvements")
            except json.JSONDecodeError as e:
                log(f"Failed to parse improvements.json: {e}")
        else:
            log("improvements.json not found in audit output")
    else:
        log("No job dir produced")

    shutil.rmtree(audit_dir, ignore_errors=True)
    return improvements


def create_issue(improvement):
    """Create a GitHub issue from an improvement suggestion."""
    title = improvement["title"][:200]
    title_lower = title.strip().lower()
    if title_lower in ALREADY_FILED:
        log(f"Skipping duplicate: {title}")
        return False
    imp_type = improvement.get("type", "refactor")
    severity = improvement.get("severity", "medium")
    description = improvement.get("description", "")
    files = improvement.get("files", [])
    suggestion = improvement.get("suggestion", "")
    body = f"""## {imp_type.upper()} ({severity})

{description}

**Suggested approach:** {suggestion}

**Relevant files:**
""" + "\n".join(f"- `{f}`" for f in files) + f"""

---
*Generated by codebase audit agent*
"""
    result = gh_api("POST", "/issues", {
        "title": title,
        "body": body,
        "labels": [f"audit-{imp_type}", f"severity-{severity}"]
    })
    if result.get("html_url"):
        log(f"Created issue: {result['html_url']}")
        ALREADY_FILED.add(title_lower)
        return True
    else:
        log(f"Failed to create issue for '{title}': {result.get('message', 'unknown')}")
        return False


def write_improvements_to_docs(improvements):
    """Write found improvements back to docs/ for the next run to consider."""
    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    existing = []
    existing_file = docs_dir / "improvements-filed.json"
    if existing_file.exists():
        try:
            existing = json.loads(existing_file.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.extend(improvements)
    existing_file.write_text(json.dumps(existing, indent=2))
    log(f"Wrote {len(improvements)} improvements to {existing_file}")


def make_self_audit_task():
    """Create a temp task directory that wraps the project root for self-audit."""
    tmp = Path(tempfile.mkdtemp(prefix="self-audit-"))
    env_dir = tmp / "environment"
    env_dir.mkdir(parents=True)
    # Copy the project source (excluding .git and noise) into environment/src/
    src_dir = env_dir / "src"
    shutil.copytree(PROJECT_ROOT, src_dir, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc",
                                                   ".venv", "node_modules", "jobs", ".opencode"))
    (src_dir / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    (env_dir / "Dockerfile").write_text(f"""FROM python:3.12
RUN apt-get update -qq && apt-get install -y -qq git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY src/ /app/
RUN pip install --no-cache-dir pytest pyyaml && \\
    echo "__pycache__/" > /app/.gitignore && \\
    echo "*.pyc" >> /app/.gitignore && \\
    git init && git config user.email "dev@example.com" && \\
    git config user.name "Developer" && \\
    git add -A 2>/dev/null; git commit -m "init" --allow-empty && git tag _baseline
CMD ["/bin/bash"]""")
    # tests/ dir (needed for is_valid check even though verifier is disabled)
    tests_dir = tmp / "tests"
    tests_dir.mkdir()
    (tests_dir / "test.sh").write_text("#!/bin/bash\nexit 0\n")
    (tests_dir / "test.sh").chmod(0o755)
    (tmp / "instruction.md").write_text("""# Audit the codebase

Analyze this experiment framework repository thoroughly.
Look for code quality issues, missing features, test gaps,
security concerns, and maintainability improvements.

Focus on the experiment framework code (experiments/, pier-configs/,
deep-swe/tasks/dummy-adaptix-alias/) not the benchmark task directories.

Write findings to /app/docs/improvements.json.
""")
    task_toml = """schema_version = "1.1"
artifacts = ["/logs/artifacts/model.patch"]
[task]
name = "datacurve/self-audit"
description = "Codebase audit of the experiment framework"
authors = []
keywords = []
[metadata]
ext_id = "self-audit"
task_id = "self-audit"
display_title = "Self audit"
display_description = "Analyzes the experiment framework for improvements"
category = "audit"
language = "python"
repository_url = ""
base_commit_hash = "HEAD"
[verifier]
environment_mode = "separate"
timeout_sec = 60.0
[verifier.env]
[verifier.environment]
build_timeout_sec = 120.0
cpus = 1
memory_mb = 512
storage_mb = 1024
allow_internet = false
[agent]
timeout_sec = 600.0
[environment]
build_timeout_sec = 120.0
cpus = 1
memory_mb = 512
storage_mb = 1024
allow_internet = false
mcp_servers = []
[environment.env]
[solution.env]"""
    (tmp / "task.toml").write_text(task_toml)
    (tmp / "pre_artifacts.sh").write_text(textwrap.dedent("""\
    #!/bin/bash
    set -uo pipefail
    cd /app || exit 0
    mkdir -p /logs/artifacts
    git config --global --add safe.directory /app 2>/dev/null || true
    git add -A 2>/dev/null || true
    git commit --allow-empty -m "pre_artifacts auto-commit" 2>/dev/null || true
    git diff --binary _baseline HEAD > /logs/artifacts/model.patch 2>/dev/null || true
    echo "[pre_artifacts] captured $(wc -c < /logs/artifacts/model.patch) bytes"
    """))
    (tmp / "pre_artifacts.sh").chmod(0o755)
    return tmp


def main():
    global TASK_DIR
    dry_run = "--dry-run" in sys.argv
    poll_interval = None

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--task" and i < len(sys.argv):
            TASK_DIR = Path(sys.argv[i + 1])
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--poll":
            if i < len(sys.argv):
                try:
                    poll_interval = int(sys.argv[i + 1])
                except ValueError:
                    pass

    if TASK_DIR is None:
        TASK_DIR = make_self_audit_task()
        log(f"Created self-audit task at {TASK_DIR}")
        cleanup_self = True
    else:
        cleanup_self = False

    if poll_interval:
        log(f"Polling every {poll_interval}s (dry_run={dry_run})")
        while True:
            load_filed_issues()
            improvements = run_audit(TASK_DIR)
        if improvements:
            write_improvements_to_docs(improvements)
            if not dry_run:
                for imp in improvements:
                    create_issue(imp)
            else:
                log(f"[DRY-RUN] Would create {len(improvements)} issues:")
                for imp in improvements:
                    log(f"  - [{imp.get('severity','?')}] {imp['title'][:100]}")
        else:
            log("No improvements found")
        time.sleep(poll_interval)
    else:
        load_filed_issues()
        improvements = run_audit(TASK_DIR)
        if improvements:
            write_improvements_to_docs(improvements)
            if not dry_run:
                for imp in improvements:
                    create_issue(imp)
            else:
                log(f"[DRY-RUN] Would create {len(improvements)} issues:")
                for imp in improvements:
                    log(f"  - [{imp.get('severity','?')}] {imp['title'][:100]}")
        else:
            log("No improvements found")

    if cleanup_self and TASK_DIR and TASK_DIR.name.startswith("self-audit-"):
        shutil.rmtree(TASK_DIR, ignore_errors=True)
        log(f"Cleaned up self-audit task at {TASK_DIR}")


if __name__ == "__main__":
    main()
