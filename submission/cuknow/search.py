"""BM25 passage search with authority and recency weighting (rules R1, R3, R4).

One global inverted index over the passages of every distinct parsed page; each view only
needs the set of documents it contains, so building five views costs one index.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .pages import EDITORIAL, LISTING, OFFICIAL, TOOL
from .text import mask_template

K1, B = 1.2, 0.75
CLASS_WEIGHT = {OFFICIAL: 1.0, TOOL: 0.85, EDITORIAL: 0.85, LISTING: 0.0}
CARRIED_WEIGHT = 0.6


class SearchIndex:
    def __init__(self) -> None:
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)  # tok -> [(pid, tf)]
        self.p_doc: list[int] = []        # pid -> doc id
        self.p_len: list[int] = []
        self.p_text: list[str] = []
        self.p_order: list[int] = []
        self.p_tmpl: list[str] = []
        self.p_block: list[int] = []
        self.p_kind: list[str] = []
        self.p_toks: list[frozenset] = []
        self.doc_pids: dict[int, list[int]] = {}
        self.total_len = 0

    def add_doc(self, doc_id: int, passages) -> None:
        if doc_id in self.doc_pids:
            return
        pids = []
        for p in passages:
            pid = len(self.p_doc)
            self.p_doc.append(doc_id)
            self.p_len.append(getattr(p, "own", 0) or len(p.toks))
            self.p_text.append(p.text)
            self.p_order.append(p.order)
            self.p_tmpl.append(mask_template(p.text))
            self.p_block.append(p.block)
            self.p_kind.append(p.kind)
            self.p_toks.append(frozenset(p.toks))
            self.total_len += getattr(p, "own", 0) or len(p.toks)
            tf: dict[str, int] = defaultdict(int)
            for t in p.toks:
                tf[t] += 1
            for t in sorted(tf):
                self.postings[t].append((pid, tf[t]))
            pids.append(pid)
        self.doc_pids[doc_id] = pids

    def df(self, tok: str) -> int:
        return len(self.postings.get(tok, ()))

    def idf(self, tok: str) -> float:
        n = len(self.p_doc) or 1
        df = len(self.postings.get(tok, ()))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, qtoks: list[str], allowed: dict[int, tuple], skip_text: set, limit: int = 40):
        """allowed: doc id -> (weight, recency date, version). Returns [(score, pid)] best first."""
        if not qtoks:
            return []
        avg = (self.total_len / len(self.p_doc)) if self.p_doc else 1.0
        scores: dict[int, float] = defaultdict(float)
        for t in sorted(set(qtoks)):
            plist = self.postings.get(t)
            if not plist:
                continue
            w = self.idf(t)
            for pid, tf in plist:
                d = self.p_doc[pid]
                if d not in allowed:
                    continue
                ln = self.p_len[pid]
                scores[pid] += w * tf * (K1 + 1) / (tf + K1 * (1 - B + B * ln / avg))
        out = []
        for pid, s in scores.items():
            if self.p_text[pid] in skip_text:
                continue
            out.append((s * allowed[self.p_doc[pid]][0], pid))
        out.sort(key=lambda x: (-x[0], x[1]))
        return out[: limit * 3]
