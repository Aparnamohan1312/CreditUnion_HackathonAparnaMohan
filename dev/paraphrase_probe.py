#!/usr/bin/env python3
"""Generalization probe: my own rewordings of the practice questions (dev/probes.jsonl).

HARD checks (exit non-zero on any failure):
  1. every answerable practice question and every rewording is NOT refused
     (guards the rare-term-coverage refusal rule);
  2. every should_refuse question and its rewordings IS refused, with no new digits;
  3. no must_not (stale / wrong-tier) value ever appears;
  4. asking twice returns identical text.
SOFT checks (reported as MISS, tracked by the eval score, do not fail the run):
  5. must values present;
  6. each paraphrase group gives the same set of numbers (or every member is right).

Usage: python3 dev/paraphrase_probe.py [--snapshots snapshots] [--submission submission]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from run_eval import load_snapshots, load_submission, to_answer  # noqa: E402
from scoring import REFUSAL_RE, _numbers, contains  # noqa: E402


def refused(ans) -> bool:
    return (not ans.confident) or bool(REFUSAL_RE.search(ans.text or ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"))
    ap.add_argument("--submission", default=str(ROOT / "submission"))
    ap.add_argument("--probes", default=str(ROOT / "dev" / "probes.jsonl"))
    ap.add_argument("--eval", default=str(ROOT / "eval" / "public_eval.jsonl"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    probes = [json.loads(l) for l in Path(args.probes).read_text().splitlines() if l.strip()]
    # The practice questions themselves join their groups.
    for l in Path(args.eval).read_text().splitlines():
        if not l.strip():
            continue
        it = json.loads(l)
        probes.append({
            "id": it["id"], "group": it["id"] if not it.get("group") else it["group"], "question": it["question"],
            "as_of": it.get("as_of"), "expect": "refuse" if it["category"] == "should_refuse" else "answer",
            "must": it.get("must_include", []), "must_not": it.get("must_not_include", []),
        })

    os.environ.setdefault("CUKNOW_DEBUG", "1")
    ks = load_submission(Path(args.submission))
    for s in load_snapshots(Path(args.snapshots)):
        ks.ingest(s)

    failures = []
    misses = []
    groups = defaultdict(list)
    worst = 0.0
    for p in probes:
        t = time.time()
        a1 = to_answer(ks.answer(p["question"], p.get("as_of")))
        worst = max(worst, time.time() - t)
        a2 = to_answer(ks.answer(p["question"], p.get("as_of")))
        problems = []
        soft = []
        if a1.text != a2.text:
            problems.append("repeat differs")
        if p["expect"] == "answer":
            if refused(a1):
                problems.append("FALSE REFUSAL")
        else:
            if not refused(a1):
                problems.append("should refuse")
            if _numbers(a1.text) - _numbers(p["question"]):
                problems.append("refusal has new digits")
        miss = [m for m in p["must"] if not contains(a1.text, m)]
        trap = [m for m in p["must_not"] if contains(a1.text, m)]
        if miss and not refused(a1):
            soft.append("missing %s" % miss)
        elif miss:
            problems.append("missing %s" % miss)
        if trap:
            problems.append("TRAP %s" % trap)
        right = not miss and not trap and (p["expect"] == "answer") != refused(a1)
        groups[p["group"]].append((p, a1, right))
        if problems:
            failures.append((p, a1, problems))
        elif soft:
            misses.append((p, a1, soft))
        elif args.verbose:
            print("ok   %-10s %s -> %s" % (p["id"], p["question"][:60], a1.text[:100]))

    for g, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        nums = {frozenset(_numbers(a.text)) for _, a, _ in members}
        if not all(r for _, _, r in members) and len(nums) > 1:
            misses.append(({"id": g, "question": "(group)"}, members[0][1], ["group numbers differ"]))

    # Strict view: number sets inside each group, exactly as the scorer computes them (_numbers),
    # reported even when every member is correct.
    strict = []
    for g, members in sorted(groups.items()):
        ans = [(p, a) for p, a, _ in members if p["expect"] == "answer"]
        if len(ans) < 2:
            continue
        sets = {}
        for p, a in ans:
            sets.setdefault(frozenset(_numbers(a.text)), []).append(p["id"])
        if len(sets) > 1:
            strict.append((g, sets))
    for g, sets in strict:
        print("STRICT %-12s %d different number sets:" % (g, len(sets)))
        for k, ids in sets.items():
            print("       %s  <- %s" % (sorted("%s%g" % (u, v) for u, v in k)[:10], ids[:6]))
    for p, a, problems in misses:
        print("MISS %-10s %s\n     %s\n     -> %s" % (p["id"], p["question"][:90], "; ".join(problems), (a.text or "")[:220]))
    for p, a, problems in failures:
        print("FAIL %-10s %s\n     %s\n     -> %s" % (p["id"], p["question"][:90], "; ".join(problems), (a.text or "")[:220]))
    n = len(probes)
    print("\n%d probes, %d groups: %d hard failures (refusal/trap/repeat), %d accuracy misses, "
          "%d groups with differing number sets (strict), slowest answer %.3fs"
          % (n, len(groups), len(failures), len(misses), len(strict), worst))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
