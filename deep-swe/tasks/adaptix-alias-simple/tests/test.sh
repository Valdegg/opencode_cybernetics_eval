#!/bin/bash
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ] && [ ! -f /logs/verifier/reward.txt ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
log() { echo "[verifier] $*"; }
cd /app || { mkdir -p /logs/verifier; exit 6; }

python3 /tests/grader.py prepare || exit $?
[ -f /logs/verifier/reward.json ] && exit 0

set +e
python -m pytest tests/test_p2p_basic.py -q -p no:cacheprovider --junitxml=/logs/verifier/base.xml > /logs/verifier/base.log 2>&1
base_rc=$?
python -m pytest tests/integration/morphing/test_aliases_simple.py -q -p no:cacheprovider --junitxml=/logs/verifier/new.xml > /logs/verifier/new.log 2>&1
new_rc=$?
set -e
log "base rc=$base_rc; new rc=$new_rc"

echo "===== base output ====="
cat /logs/verifier/base.log 2>/dev/null
echo "===== new output ====="
cat /logs/verifier/new.log 2>/dev/null
echo "===== grade ====="

python3 /tests/grader.py grade
log "reward.json=$(cat /logs/verifier/reward.json 2>/dev/null)"

mkdir -p /logs/verifier/reports
for _f in /logs/verifier/*; do
  case "${_f##*/}" in
    reward.json|reward.txt|ctrf.json|run.log|test-stdout.txt|reports) continue ;;
  esac
  [ -f "$_f" ] && mv -f "$_f" /logs/verifier/reports/ 2>/dev/null
done
