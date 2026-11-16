#!/usr/bin/env python3
"""Robustness checks for the hidden evaluation (unseen questions, other snapshot sets).

Runs every practice question and every probe against four builds:
  full        all snapshots, normal page order
  reversed    all snapshots, pages in each snapshot reversed  -> answers/sources/conflicts must be identical
  subset      only the first three snapshots
  subset_rev  first three snapshots, reversed                 -> must equal subset
and reports, per build: swallowed exceptions (errors), refusal count, p95 / max answer time.
Exits non-zero on any error, any order-dependent difference, or p95 >= 2 s.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
os.environ["CUKNOW_DEBUG"] = "0"          # count swallowed errors like the graders would see them

from run_eval import load_snapshots, load_submission, to_answer  # noqa: E402


def questions():
    qs = []
    for l in (ROOT / "eval" / "public_eval.jsonl").read_text().splitlines():
        if l.strip():
            it = json.loads(l)
            qs.append((it["question"], it.get("as_of")))
    for l in (ROOT / "dev" / "probes.jsonl").read_text().splitlines():
        if l.strip():
            it = json.loads(l)
            qs.append((it["question"], it.get("as_of")))
    return qs


def run(snaps, qs):
    ks = load_submission(ROOT / "submission")
    t0 = time.time()
    for s in snaps:
        ks.ingest(s)
    ingest = time.time() - t0
    out, lat = [], []
    for q, d in qs:
        t = time.time()
        a = to_answer(ks.answer(q, d))
        lat.append(time.time() - t)
        out.append((a.text, a.confident, tuple((s.url, s.snapshot_id, s.quote) for s in a.sources)))
    cs = [(c.topic, tuple(c.values), c.resolution) for c in ks.conflicts()]
    lat.sort()
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
    return {"answers": out, "conflicts": cs, "errors": len(getattr(ks, "errors", [])), "p95": p95,
            "max": lat[-1], "ingest": ingest, "refused": sum(1 for x in out if not x[1])}


def reversed_pages(snaps):
    return [replace(s, pages=list(reversed(s.pages))) for s in snaps]


def main() -> int:
    snaps = load_snapshots(ROOT / "snapshots")
    qs = questions()
    builds = {
        "full": snaps, "reversed": reversed_pages(snaps),
        "subset": snaps[:3], "subset_rev": reversed_pages(snaps[:3]),
    }
    res = {}
    for name, ss in builds.items():
        r = run(ss, qs)
        res[name] = r
        print("%-10s snapshots=%d questions=%d errors=%d refused=%d conflicts=%d p95=%.3fs max=%.3fs ingest=%.1fs"
              % (name, len(ss), len(qs), r["errors"], r["refused"], len(r["conflicts"]), r["p95"], r["max"], r["ingest"]))
    bad = sum(r["errors"] for r in res.values()) + sum(1 for r in res.values() if r["p95"] >= 2.0)
    for a, b in (("full", "reversed"), ("subset", "subset_rev")):
        diffs = [i for i, (x, y) in enumerate(zip(res[a]["answers"], res[b]["answers"])) if x != y]
        cdiff = res[a]["conflicts"] != res[b]["conflicts"]
        print("%s vs %s: %d answer differences, conflicts %s" % (a, b, len(diffs), "DIFFER" if cdiff else "identical"))
        for i in diffs[:5]:
            print("   Q:", qs[i][0], "\n     ", res[a]["answers"][i][0][:120], "\n     ", res[b]["answers"][i][0][:120])
        bad += len(diffs) + (1 if cdiff else 0)
    print("\nRESULT:", "PASS" if bad == 0 else "FAIL (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
