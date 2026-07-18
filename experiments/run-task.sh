#!/usr/bin/env bash
set -euo pipefail

# Run an agent config on a task and save results to experiments/results.json
# Usage: ./experiments/run-task.sh <config-name> [task-path] [n-attempts]
#
# Examples:
#   ./experiments/run-task.sh opencode-deepseek          # vanilla on adaptix (real)
#   ./experiments/run-task.sh opencode-deepseek-dummy     # vanilla on dummy
#   ./experiments/run-task.sh opencode-deepseek-exp2-prompt-dummy  # structured prompt on dummy

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_FILE="$REPO_DIR/experiments/results.json"
PIER_CONFIGS="$REPO_DIR/pier-configs"

CONFIG="${1:?Usage: $0 <config-name> [task-path] [n-attempts]}"
TASK_PATH="${2:-$REPO_DIR/deep-swe/tasks/adaptix-name-mapping-aliases}"
N_ATTEMPTS="${3:-1}"
CONFIG_FILE="$PIER_CONFIGS/${CONFIG}.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Error: Config not found: $CONFIG_FILE"
  ls "$PIER_CONFIGS"/*.yaml | xargs -n1 basename | sed 's/\.yaml$//'
  exit 1
fi

if [ ! -d "$TASK_PATH" ]; then
  echo "Error: Task path not found: $TASK_PATH"
  exit 1
fi

TASK_NAME="$(basename "$TASK_PATH")"
echo "=== Running: $CONFIG on $TASK_NAME ==="
echo "Config: $CONFIG_FILE"
echo "Task: $TASK_PATH"
echo "Attempts: $N_ATTEMPTS"
echo ""

cd "$REPO_DIR"
pier run \
  --config "$CONFIG_FILE" \
  --path "$TASK_PATH" \
  --n-attempts "$N_ATTEMPTS" \
  --yes

LATEST_JOB=$(ls -t "$REPO_DIR/jobs/" | head -1)
if [ -z "$LATEST_JOB" ]; then
  echo "Error: No job output found"
  exit 1
fi

JOB_DIR="$REPO_DIR/jobs/$LATEST_JOB"
echo ""
echo "=== Job complete: $JOB_DIR ==="

python3 "$SCRIPT_DIR/_save_results.py" "$JOB_DIR" "$CONFIG" "$RESULTS_FILE"
