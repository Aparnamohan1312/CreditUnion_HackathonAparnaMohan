"""Submission entry point: build() -> a knowledge system that knows what's true *now*.

The logic lives in the `cuknow` package next to this file:
  text.py      cleaning, tokens, number words, dates
  quals.py     qualifier parser shared by table rows and questions (rule R5)
  pages.py     URL canonicalization, authority class, versions and views (R1)
  facts.py     tables / label-value lines / hours / counts / addresses / passages
  kb.py        ingest, per-view indexes, precedence ranking (R0-R3)
  search.py    BM25 passage search with template collapse (R4)
  query.py     question -> intent
  answer.py    routing, templates, refusal policy
  conflicts.py the contradiction list
See NOTES.md for the rules.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Import the contract the same way the starter does (the harness folder of the kit).
for _d in _HERE.parents:
    if (_d / "harness" / "contract.py").exists():
        sys.path.insert(0, str(_d / "harness"))
        break
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from contract import Answer, Conflict, Snapshot, Source  # noqa: E402,F401

from cuknow.answer import Answerer, refusal  # noqa: E402
from cuknow.conflicts import build_conflicts  # noqa: E402
from cuknow.kb import KB  # noqa: E402

DEBUG = os.environ.get("CUKNOW_DEBUG") == "1"


class SiteKnowledge:
    """ingest() does all heavy work; answer() is a fast lookup and never raises."""

    def __init__(self) -> None:
        self.kb = KB()
        self.answerer = Answerer(self.kb)
        self._conflicts = None
        self.errors: list[str] = []

    def ingest(self, snapshot: Snapshot) -> None:
        self.kb.ingest(snapshot)
        self._conflicts = None

    def answer(self, question: str, as_of: str | None = None) -> Answer:
        try:
            return self.answerer.answer(question, as_of)
        except Exception as e:  # one bad question must not count as an error
            if DEBUG:
                raise
            self.errors.append("{}: {}".format(type(e).__name__, e))
            return refusal()

    def conflicts(self) -> list:
        if self._conflicts is None:
            try:
                self._conflicts = build_conflicts(self.kb)
            except Exception as e:
                if DEBUG:
                    raise
                self.errors.append("{}: {}".format(type(e).__name__, e))
                self._conflicts = []
        return list(self._conflicts)


def build() -> SiteKnowledge:
    return SiteKnowledge()
