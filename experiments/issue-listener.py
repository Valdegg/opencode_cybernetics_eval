#!/usr/bin/env python3
"""Issue listener — outer loop that triggers Tier B on new GitHub issues.

Two modes:
  --webhook PORT  : HTTP server (receives GitHub webhook POSTs)
  --poll INTERVAL : Polls GitHub for open issues (uses GITHUB_TOKEN env)

In both modes, unprocessed issues (no "running"/"done" label) are picked up,
the pipeline is spawned, and results are posted back as comments.

Usage:
  export GITHUB_TOKEN=ghp_...
  python experiments/issue-listener.py --webhook 8000
  python experiments/issue-listener.py --poll 60
"""
import subprocess, sys, os, json, threading, time, http.server, hmac, hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_TIER_B = [sys.executable, str(PROJECT_ROOT / "experiments" / "run-tierB.py")]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Valdegg/opencode_cybernetics_eval")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
LABEL_RUNNING = "running"
LABEL_DONE = "done"


def gh_api(method, path, data=None):
    """Call GitHub API and return parsed response."""
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


def set_status(issue_number, body):
    """Set the issue body to a status message."""
    gh_api("PATCH", f"/issues/{issue_number}", {"body": body})


def run_pipeline(issue):
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body", "")
    log(f"Issue #{number}: {title}")
    add_label(number, LABEL_RUNNING)
    post_comment(number, f"**Running Tier B pipeline** for task: {title}")
    cmd = RUN_TIER_B + ["--task", f"{title}: {body}" if body else title]
    start = time.time()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    elapsed = time.time() - start
    elapsed_str = f"{elapsed // 60:.0f}m {elapsed % 60:.0f}s"
    if result.returncode == 0:
        summary = "**Pipeline completed successfully**"
    else:
        summary = "**Pipeline failed or was aborted**"
    log_output = (result.stdout or "")[-3000:] + (result.stderr or "")[-1000:]
    comment = (
        f"{summary} ({elapsed_str})\n\n"
        f"```\n{log_output}\n```\n"
    )
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
