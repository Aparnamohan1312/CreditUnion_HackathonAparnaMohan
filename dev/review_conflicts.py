#!/usr/bin/env python3
"""Print conflicts() for reading (the public eval has no conflicts gold file).

Usage: python3 dev/review_conflicts.py [--snapshots snapshots] [--submission submission] [-v]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from run_eval import load_snapshots, load_submission  # noqa: E402
from scoring import contains  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"))
    ap.add_argument("--submission", default=str(ROOT / "submission"))
    ap.add_argument("-v", "--verbose", action="store_true", help="also print sources")
    args = ap.parse_args()
    os.environ.setdefault("CUKNOW_DEBUG", "1")
    ks = load_submission(Path(args.submission))
    for s in load_snapshots(Path(args.snapshots)):
        ks.ingest(s)
    cs = ks.conflicts()
    bad = 0
    for i, c in enumerate(cs, 1):
        ok = bool(c.resolution) and bool(c.reason) and len(c.values) >= 2 and contains(c.resolution, c.values[0])
        bad += 0 if ok else 1
        print("%2d. %s%s\n    values=%s resolution=%s\n    reason: %s" % (
            i, "" if ok else "[INCOMPLETE] ", c.topic, c.values, c.resolution, c.reason))
        if args.verbose:
            for s in c.sources:
                print("      - %s [%s] %s" % (s.url, s.snapshot_id, (s.quote or "")[:90]))
    print("\n%d conflicts, %d incomplete" % (len(cs), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
