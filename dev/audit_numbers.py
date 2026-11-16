#!/usr/bin/env python3
"""Stray-number audit. The scorer matches forbidden values by number anywhere in the answer text
and judges paraphrase consistency by the set of numbers in each answer, so every number in an
answer is a liability unless it is part of the answer.

For every public-eval question and every probe this prints the numbers in the answer, grouped:
  rate/amount  values with % or $ (the answer's values)
  year         19xx/20xx
  day          the day in a 'Month D' date
  phone        nnn-nnn-nnnn
  other        any other bare number (counts, tier labels, terms, footnotes ...)
It then checks conflict losers: a value that lost in a conflict must not appear in an answer whose
text is about that conflict's topic, unless the question asks about history/change.

Usage: python3 dev/audit_numbers.py [--all]   (--all prints every answer, not only flagged ones)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
os.environ.setdefault("CUKNOW_DEBUG", "1")

from run_eval import load_snapshots, load_submission, to_answer  # noqa: E402
from scoring import _numbers, contains  # noqa: E402

MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
HISTORY_Q = re.compile(r"\b(previous|previously|used to|before|history|changed?|was|were|as of|in (?:19|20)\d\d|last year|"
                       r"past year|prior year|recent|ago|did)\b", re.I)


def classify(text: str) -> dict:
    out = {"rate/amount": [], "year": [], "day": [], "phone": [], "other": []}
    t = text
    for m in re.finditer(r"\(?\b\d{3}\)?[-. ]\d{3}[-.]\d{4}\b", t):
        out["phone"].append(m.group(0))
    t = re.sub(r"\(?\b\d{3}\)?[-. ]\d{3}[-.]\d{4}\b", " ", t)
    for m in re.finditer(MONTH + r"\.?\s+(\d{1,2})\b", t):
        out["day"].append(m.group(0))
    t = re.sub(MONTH + r"\.?\s+\d{1,2}\b", " ", t)
    for m in re.finditer(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*%", t):
        out["rate/amount"].append(m.group(0).strip())
    t = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*%", " ", t)
    for m in re.finditer(r"\b(?:19|20)\d\d\b", t):
        out["year"].append(m.group(0))
    t = re.sub(r"\b(?:19|20)\d\d\b", " ", t)
    out["other"] = re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", t)
    return out


def main() -> int:
    show_all = "--all" in sys.argv
    items = []
    for l in (ROOT / "eval" / "public_eval.jsonl").read_text().splitlines():
        if l.strip():
            it = json.loads(l)
            items.append((it["id"], it["question"], it.get("as_of")))
    for l in (ROOT / "dev" / "probes.jsonl").read_text().splitlines():
        if l.strip():
            it = json.loads(l)
            items.append((it["id"], it["question"], it.get("as_of")))
    ks = load_submission(ROOT / "submission")
    for s in load_snapshots(ROOT / "snapshots"):
        ks.ingest(s)
    conflicts = ks.conflicts()
    flagged = 0
    leaks = []
    for iid, q, d in items:
        a = to_answer(ks.answer(q, d))
        if not a.confident:
            continue
        c = classify(a.text)
        qnums = {n for _, n in _numbers(q)}
        extra = {k: v for k, v in c.items() if v and k != "rate/amount"}
        if extra or show_all:
            flagged += 1
            print("%-9s %s\n          -> %s" % (iid, q[:80], a.text[:170]))
            for k, v in c.items():
                if v:
                    print("          %-11s %s" % (k, v[:12]))
    print("\n%d answers with numbers beyond rates/amounts (shown above)" % flagged)
    # Slot-precise loser check: ask each conflict's own topic; the answer must contain the winner
    # and none of that conflict's losing values.
    for cf in conflicts:
        topic = cf.topic
        if topic.startswith("branch count"):
            q = "How many branches does {} have?".format(ks.kb.brand or "the credit union")
        elif "Forbes" in topic:
            q = "What recognition has {} received from Forbes?".format(ks.kb.brand or "the credit union")
        elif "counties" in topic:
            q = "How many counties does {} serve?".format(ks.kb.brand or "the credit union")
        else:
            q = "What is the {}?".format(re.sub(r"\s*\(([^)]*)\)", r" for \1", topic))
        a = to_answer(ks.answer(q, None))
        won = [v for v in cf.values if contains(cf.resolution or "", v)]
        lost = [v for v in cf.values if v not in won and not any(contains(w, v) for w in won)]
        hit = [v for v in lost if contains(a.text, v)]
        has_win = any(contains(a.text, w) for w in won)
        status = "LEAK" if hit else ("ok" if has_win else "no-winner")
        if hit or not has_win:
            leaks.append((status, topic, q, hit, a.text[:150]))
        print("  %-9s %s" % (status, topic[:80]))
    print("%d conflict topics with a leak or without the winner:" % len(leaks))
    for status, topic, q, hit, t in leaks:
        print("  %s [%s] Q: %s losers-in-answer=%s\n      -> %s" % (status, topic[:60], q[:80], hit, t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
