#!/bin/bash
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ] && [ ! -f /logs/verifier/reward.txt ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
cd /app || exit 6

# Apply the agent's patch (model artifact) or the reference solution (oracle)
MODEL_PATCH="/logs/artifacts/model.patch"
SOLUTION_PATCH="/solution/solution.patch"

if [ -f "$MODEL_PATCH" ]; then
  if ! git apply "$MODEL_PATCH" 2>/dev/null; then
    echo "Patch failed to apply"
    echo '{"reward": 0, "f2p_total": 0, "f2p_passed": 0, "p2p_total": 0, "p2p_passed": 0, "f2p": 0.0, "p2p": 0.0, "partial": 0.0}' > /logs/verifier/reward.json
    exit 0
  fi
  git add -A && git commit -m "Apply model patch" 2>/dev/null || true
elif [ -f "$SOLUTION_PATCH" ]; then
  git apply "$SOLUTION_PATCH" 2>/dev/null || true
  git add -A && git commit -m "Apply solution" 2>/dev/null || true
fi

set +e
python -m pytest tests/test_p2p_basic.py -v --junitxml=/logs/verifier/base.xml 2>&1 | tee /logs/verifier/base.log
rc1=$?
python -m pytest tests/test_aliases_simple.py -v --junitxml=/logs/verifier/new.xml 2>&1 | tee /logs/verifier/new.log
rc2=$?
set -e
echo "p2p rc=$rc1 f2p rc=$rc2"

p2p_pass=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/logs/verifier/base.xml')
print(sum(1 for tc in tree.iter('testcase') if not any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
")
p2p_fail=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/logs/verifier/base.xml')
print(sum(1 for tc in tree.iter('testcase') if any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
")
f2p_pass=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/logs/verifier/new.xml')
print(sum(1 for tc in tree.iter('testcase') if not any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
")
f2p_fail=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/logs/verifier/new.xml')
print(sum(1 for tc in tree.iter('testcase') if any(ch.tag.endswith('failure') or ch.tag.endswith('error') for ch in tc)))
")

echo "p2p: $p2p_pass pass / $((p2p_pass + p2p_fail)) total"
echo "f2p: $f2p_pass pass / $((f2p_pass + f2p_fail)) total"

python3 -c "
import json
out = {
    'reward': 1 if $f2p_pass > 0 and $f2p_fail == 0 and $p2p_fail == 0 else 0,
    'f2p_total': $((f2p_pass + f2p_fail)),
    'f2p_passed': $f2p_pass,
    'p2p_total': $((p2p_pass + p2p_fail)),
    'p2p_passed': $p2p_pass,
    'f2p': $f2p_pass / ($f2p_pass + $f2p_fail) if ($f2p_pass + $f2p_fail) > 0 else 0.0,
    'p2p': $p2p_pass / ($p2p_pass + $p2p_fail) if ($p2p_pass + $p2p_fail) > 0 else 1.0,
}
t = $((p2p_pass + p2p_fail + f2p_pass + f2p_fail))
out['partial'] = ($f2p_pass + $p2p_pass) / t if t > 0 else 0.0
with open('/logs/verifier/reward.json', 'w') as f:
    json.dump(out, f)
print(json.dumps(out))
"

mkdir -p /logs/verifier/reports 2>/dev/null
for _f in /logs/verifier/*; do
  case "${_f##*/}" in
    reward.json|reward.txt|run.log|test-stdout.txt|reports) continue ;;
  esac
  [ -f "$_f" ] && mv -f "$_f" /logs/verifier/reports/ 2>/dev/null
done
