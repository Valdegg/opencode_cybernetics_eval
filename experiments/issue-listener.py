#!/usr/bin/env python3
"""Issue listener — outer loop that triggers Tier B on new GitHub issues.

Two modes:
  --webhook PORT  : HTTP server (receives GitHub webhook POSTs)
  --poll INTERVAL : Polls GitHub for open issues (uses GITHUB_TOKEN env)

In both modes, unprocessed issues (no "running"/"done" label) are picked up.
For each issue, the listener:
  1. Clones the repo to a temp task directory
  2. Injects docs/learnings.md (accumulated knowledge from past runs)
  3. Writes the issue body as instruction.md
  4. Spawns run-tierB.py on that task directory
  5. Extracts new learnings from the run output and appends them
  6. Commits the updated learnings back to the repo
  7. Posts results as a comment on the issue

Usage:
  export GITHUB_TOKEN=ghp_...
  python experiments/issue-listener.py --webhook 8000
  python experiments/issue-listener.py --poll 60
"""
import subprocess, sys, os, json, threading, time, http.server, hmac, hashlib, tempfile, shutil, re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_TIER_B = [sys.executable, str(PROJECT_ROOT / "experiments" / "run-tierB.py")]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Valdegg/opencode_cybernetics_eval")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
CLONE_URL = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
REPO_NAME = GITHUB_REPO.split("/")[1]
LABEL_RUNNING = "running"
LABEL_DONE = "done"


def gh_api(method, path, data=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}{path}"
    cmd = ["curl", "-s", "-X", method, url,
           "-H", f"Authorization: token {GITHUB_TOKEN}",
           "-H", "Accept: application/vnd.github.v3+json"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def add_label(issue_number, label):
    gh_api("POST", f"/issues/{issue_number}/labels", {"labels": [label]})


def post_comment(issue_number, body):
    gh_api("POST", f"/issues/{issue_number}/comments", {"body": body})


def extract_learnings(stdout, stderr):
    """Parse pipeline output for review feedback and step summaries."""
    entries = []
    combined = stdout + "\n" + stderr
    # Find review feedback lines: "Not approved: ..."
    for m in re.finditer(r"Not approved: (.+)", combined):
        text = m.group(1).strip()[:500]
        if text:
            entries.append(f"- Review rejection: {text}")
    # Find plan updates
    for m in re.finditer(r"Plan update: (.+)", combined):
        text = m.group(1).strip()[:300]
        if text:
            entries.append(f"- Plan update: {text}")
    # Find step pass/fail
    for m in re.finditer(r"Step \d+: (PASS|FAIL)", combined):
        entries.append(f"- {m.group(0)}")
    return entries


def commit_learnings(clone_dir, issue_number, new_entries):
    """Append new learnings to docs/learnings.md and push."""
    learnings_file = clone_dir / "docs" / "learnings.md"
    if not learnings_file.exists():
        return
    if not new_entries:
        return
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    header = f"\n## Run for issue #{issue_number} ({ts})\n"
    body = "\n".join(new_entries)
    with open(learnings_file, "a") as f:
        f.write(header + body + "\n")
    result = subprocess.run(
        ["git", "add", "docs/learnings.md"],
        cwd=clone_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"git add failed: {result.stderr.strip()}")
        return
    result = subprocess.run(
        ["git", "commit", "-m", f"Auto-learnings from issue #{issue_number}"],
        cwd=clone_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"git commit failed: {result.stderr.strip()}")
        return
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=clone_dir, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        log(f"git push failed: {result.stderr.strip()}")
        return
    log(f"Committed {len(new_entries)} learnings to docs/learnings.md")


def create_task_dir(issue):
    """Clone the repo and inject issue + learnings into a temp task dir."""
    tmp = Path(tempfile.mkdtemp(prefix="issue-task-"))
    log(f"Cloning {GITHUB_REPO} into {tmp}")
    subprocess.run(
        ["git", "clone", "--depth=1", CLONE_URL, str(tmp / "src")],
        capture_output=True, text=True, timeout=120
    )
    clone_dir = tmp / "src"
    env_dir = tmp / "environment"
    env_dir.mkdir()
    (env_dir / "src").mkdir()
    shutil.move(str(clone_dir), str(env_dir / "src" / REPO_NAME))
    tests_dir = env_dir / "tests"
    tests_dir.mkdir()
    # Copy docs/learnings.md into the task's docs/ so planner can read it
    task_docs = tmp / "environment" / "docs"
    task_docs.mkdir(parents=True, exist_ok=True)
    src_learnings = env_dir / "src" / REPO_NAME / "docs" / "learnings.md"
    if src_learnings.exists():
        shutil.copy2(src_learnings, task_docs / "learnings.md")
        log("Injected learnings.md into task docs/")
    title = issue["title"]
    body = issue.get("body", "")
    dockerfile = f"""FROM python:3.12
RUN apt-get update -qq && apt-get install -y -qq git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY src/ /app/src/
RUN pip install --no-cache-dir pytest && \\
    echo "__pycache__/" > /app/.gitignore && \\
    echo "*.pyc" >> /app/.gitignore && \\
    git init && git config user.email "dev@example.com" && \\
    git config user.name "Developer" && \\
    git add -A && git commit -m "Initial commit" && git tag _baseline
CMD ["/bin/bash"]"""
    (env_dir / "Dockerfile").write_text(dockerfile)
    instruction = f"# {title}\n\n{body}\n\nIMPORTANT: Work in /app and commit all changes when done."
    (tmp / "instruction.md").write_text(instruction)
    task_toml = """[agent]
timeout_sec = 600.0
build_timeout_sec = 120.0
cpus = 1
memory_mb = 512"""
    (tmp / "task.toml").write_text(task_toml)
    return tmp, env_dir / "src" / REPO_NAME


def run_pipeline(issue):
    number = issue["number"]
    title = issue["title"]
    log(f"Issue #{number}: {title}")
    add_label(number, LABEL_RUNNING)
    post_comment(number, f"**Running Tier B pipeline** for: {title}")
    task_dir, clone_dir = create_task_dir(issue)
    try:
        start = time.time()
        cmd = RUN_TIER_B + ["--task", str(task_dir)]
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=7200)
        elapsed = time.time() - start
        elapsed_str = f"{elapsed // 60:.0f}m {elapsed % 60:.0f}s"
    except subprocess.TimeoutExpired:
        elapsed_str = ">2h"
        result = subprocess.CompletedProcess(cmd, -1, stdout="", stderr="TIMEOUT")
    except Exception as e:
        elapsed_str = "error"
        result = subprocess.CompletedProcess(cmd, -1, stdout="", stderr=str(e))
    # Extract learnings and commit back to repo
    new_learnings = extract_learnings(result.stdout or "", result.stderr or "")
    if new_learnings:
        commit_learnings(clone_dir, number, new_learnings)
    shutil.rmtree(task_dir, ignore_errors=True)
    if result.returncode == 0:
        summary = "**Pipeline completed successfully**"
    else:
        summary = "**Pipeline failed or was aborted**"
    log_output = (result.stdout or "")[-3000:] + (result.stderr or "")[-1000:]
    comment = f"{summary} ({elapsed_str})\n\n```\n{log_output}\n```\n"
    post_comment(number, comment)
    add_label(number, LABEL_DONE)
    log(f"Done with issue #{number} ({elapsed_str})")


def find_unprocessed_issues():
    issues = gh_api("GET", "/issues?state=open&per_page=10")
    if not isinstance(issues, list):
        return []
    result = []
    for issue in issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        if "pull_request" not in issue and LABEL_RUNNING not in labels and LABEL_DONE not in labels:
            result.append(issue)
    return result


def poll_loop(interval):
    log(f"Polling every {interval}s for new issues...")
    while True:
        try:
            for issue in find_unprocessed_issues():
                run_pipeline(issue)
        except Exception as e:
            log(f"Poll error: {e}")
        time.sleep(interval)


# --- Webhook mode ---

def verify_signature(payload, signature):
    if not WEBHOOK_SECRET:
        return True
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        sig = self.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(payload, sig):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"bad sig")
            return
        event = self.headers.get("X-GitHub-Event", "")
        if event != "issues":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ignored")
            return
        data = json.loads(payload)
        action = data.get("action", "")
        issue = data.get("issue", {})
        labels = [l["name"] for l in issue.get("labels", [])]
        if action in ("opened", "reopened") and "pull_request" not in issue:
            log(f"Webhook: issue #{issue['number']} {action}")
            threading.Thread(target=run_pipeline, args=(issue,), daemon=True).start()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):
        log(f"HTTP: {fmt % args}")


def run_webhook(port):
    server = http.server.HTTPServer(("0.0.0.0", port), WebhookHandler)
    log(f"Webhook listening on :{port}")
    server.serve_forever()


def log(msg):
    print(f"[listener] {msg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--cleanup":
        for issue in find_unprocessed_issues():
            log(f"Pending: #{issue['number']} {issue['title']}")
        sys.exit(0)
    if sys.argv[1] == "--webhook":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        run_webhook(port)
    elif sys.argv[1] == "--poll":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        poll_loop(interval)
    else:
        print(__doc__)
        sys.exit(1)
