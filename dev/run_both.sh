#!/bin/sh
# Run the self-test and the public eval, then print the misses. Usage: sh dev/run_both.sh [python]
PY=${1:-python3}
cd "$(dirname "$0")/.." || exit 1
CUKNOW_DEBUG=${CUKNOW_DEBUG:-1} $PY harness/run_eval.py --submission submission --snapshots harness/selftest/snapshots \
  --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json --out results/selftest > results/selftest.log 2>&1
$PY -c "import json; r=json.load(open('results/selftest/report.json')); print('SELFTEST final', r['final_score'], 'qa', r['qa_score'], 'conflicts f1', r['conflicts']['f1'], 'errors', r['errors'])"
CUKNOW_DEBUG=${CUKNOW_DEBUG:-1} $PY harness/run_eval.py --submission submission --eval eval/public_eval.jsonl > results/public.log 2>&1
$PY - <<'PY'
import json
r = json.load(open('results/report.json'))
print('PUBLIC qa', r['qa_score'], 'final', r['final_score'], 'traps', r['stale_trap_hits'], 'errors', r['errors'],
      'p95', r['speed']['p95_s'], 'consistency', r['consistency']['score'], 'ingest_s', r['ingest_seconds'])
for c, v in r['by_category'].items():
    print('   %-22s %.3f (n=%d)' % (c, v['score'], v['n']))
for l in open('results/per_question.jsonl'):
    q = json.loads(l)
    if q['score'] < 0.999:
        print(q['id'], q['category'], q['score'], 'miss', q['missed'], 'trap', q['trap_hits'], 'src', q['source_hit'], '|',
              q['answer']['text'][:200].replace('\n', ' '), '|', q['answer']['confident'])
PY
