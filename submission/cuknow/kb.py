"""Knowledge base: ingest snapshots into versions and views; precedence ranking (R0-R3).

All heavy work happens here, at ingest time: parsing (once per distinct content), the search
index, per-view fact/claim/hours indexes, and boilerplate detection.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from .facts import parse_page
from .pages import EDITORIAL, LISTING, OFFICIAL, TOOL, Version, View, canon_url, fetched_date, page_class, url_path
from .search import CARRIED_WEIGHT, CLASS_WEIGHT, SearchIndex
from .text import parse_iso, register_site_words

MIN_DATE = _dt.date(1900, 1, 1)
CARRIED_CLASS = 0.5   # a carried page ranks below every live page (R1)


class ViewIndex:
    """Per-view lookups, built once at ingest."""

    def __init__(self) -> None:
        self.facts_by_family: dict[str, list] = defaultdict(list)   # family -> [(fact, version, live)]
        self.fees: list = []
        self.hours: list = []
        self.claims: list = []
        self.addresses: list = []
        self.links: list = []
        self.allowed: dict[int, tuple] = {}     # doc id -> (weight, date, version)
        self.skip_text: set = set()             # boilerplate passage texts
        self.by_key: dict[str, tuple] = {}      # canonical url -> (version, live)


class KB:
    def __init__(self) -> None:
        self.snaps: list[tuple[str, _dt.date]] = []
        self.versions: list[Version] = []
        self.latest: dict[str, Version] = {}
        self.doc_cache: dict[tuple, object] = {}
        self.doc_ids: dict[int, int] = {}       # id(doc) -> doc id
        self.views: list[View] = []
        self.vidx: list[ViewIndex] = []
        self.search = SearchIndex()
        self.page_errors = 0
        # Site identity, derived from the crawled URLs and titles (never hard-coded).
        self.site_host = ""        # canonical host, e.g. "example.com"
        self.site_base = ""        # scheme + original netloc, e.g. "https://www.example.com"
        self.brand = ""            # display name, e.g. "Example"

    # ------------------------------------------------------------ ingest

    def _learn_site(self, pages) -> None:
        hosts = Counter()
        bases = Counter()
        for p in pages:
            parts = urlsplit(p.url if "//" in p.url else "https://" + p.url)
            if parts.netloc:
                bases[parts.scheme + "://" + parts.netloc] += 1
                hosts[canon_url(p.url).split("/")[0]] += 1
        if not hosts:
            return
        self.site_host = sorted(hosts.items(), key=lambda x: (-x[1], x[0]))[0][0]
        self.site_base = sorted((b for b in bases if canon_url(b).split("/")[0] == self.site_host),
                                key=lambda b: (-bases[b], b))[0]
        label = self.site_host.split(".")[0]
        # display form of the brand: how page titles write it ("| Example")
        forms = Counter()
        for p in pages:
            for w in re.findall(r"[A-Za-z][A-Za-z'-]*", p.title or ""):
                if w.lower() == label:
                    forms[w] += 1
        self.brand = sorted(forms.items(), key=lambda x: (-x[1], x[0]))[0][0] if forms else ""
        register_site_words([label] + label.split("-"))

    def ingest(self, snapshot) -> None:
        idx = len(self.snaps)
        if not self.site_host:
            self._learn_site(snapshot.pages)
        taken = parse_iso(snapshot.taken_on) or MIN_DATE
        self.snaps.append((snapshot.snapshot_id, taken))
        # One copy per canonical URL: the freshest (fragment duplicates like #iracompare collapse).
        chosen: dict[str, object] = {}
        for p in snapshot.pages:
            try:
                key = canon_url(p.url)
                prev = chosen.get(key)
                # freshest copy wins; ties broken by content, never by page order
                if prev is None or (p.fetched_at or "", "#" not in p.url, len(p.markdown or ""), p.url, p.markdown or "") > (
                    prev.fetched_at or "", "#" not in prev.url, len(prev.markdown or ""), prev.url, prev.markdown or ""
                ):
                    chosen[key] = p
            except Exception:
                self.page_errors += 1
        for key in sorted(chosen):
            p = chosen[key]
            try:
                md = p.markdown or ""
                h = hashlib.sha1(md.encode("utf-8", "ignore")).hexdigest()
                cls = page_class(p.url, p.title or "")
                ck = (key, h, cls)
                doc = self.doc_cache.get(ck)
                if doc is None:
                    doc = parse_page(p.url, p.title or "", md, cls)
                    self.doc_cache[ck] = doc
                    did = len(self.doc_ids)
                    self.doc_ids[id(doc)] = did
                    self.search.add_doc(did, doc.passages)
                v = Version(len(self.versions), key, p.url.split("#")[0] if "#" in p.url else p.url,
                            p.title or "", idx, snapshot.snapshot_id, p.fetched_at or "",
                            fetched_date(p.fetched_at or "") or taken, cls, doc)
                self.versions.append(v)
                self.latest[key] = v
            except Exception:
                self.page_errors += 1
        view = View(idx, snapshot.snapshot_id, taken)
        for key in sorted(self.latest):
            view.pages[key] = (self.latest[key], key in chosen)
        self.views.append(view)
        self.vidx.append(self._build_view_index(view))

    def _build_view_index(self, view: View) -> ViewIndex:
        vi = ViewIndex()
        text_pages: Counter = Counter()
        n_live = 0
        for key in sorted(view.pages):
            v, live = view.pages[key]
            vi.by_key[key] = (v, live)
            doc = v.doc
            if v.cls == LISTING:
                continue
            n_live += 1 if live else 0
            entry = (v, live)
            for f in doc.facts:
                if f.quals.family == "fee":
                    vi.fees.append((f,) + entry)
                else:
                    vi.facts_by_family[f.quals.family].append((f,) + entry)
            for h in doc.hours:
                vi.hours.append((h,) + entry)
            for c in doc.claims:
                vi.claims.append((c,) + entry)
            for a in doc.addresses:
                vi.addresses.append((a,) + entry)
            if live:
                for l in doc.links:
                    vi.links.append(l + (v,))
            did = self.doc_ids.get(id(doc))
            if did is not None:
                w = CLASS_WEIGHT.get(v.cls, 0.5) * (1.0 if live else CARRIED_WEIGHT)
                d = doc.pub or v.fetched or MIN_DATE
                prev = vi.allowed.get(did)
                if prev is None or prev[0] < w:
                    vi.allowed[did] = (w, d, v)
                if live:
                    for t in set(p.text for p in doc.passages):
                        text_pages[t] += 1
        # Boilerplate: identical passage text on a large share of live pages (nav, footer).
        thresh = max(25, int(0.3 * max(n_live, 1)))
        vi.skip_text = {t for t, c in text_pages.items() if c >= thresh}
        return vi

    # ------------------------------------------------------------ views

    def view_for(self, d: _dt.date | None) -> int:
        """R0: latest snapshot taken on or before d; after newest -> newest; before first -> first."""
        if not self.views:
            return -1
        if d is None:
            return len(self.views) - 1
        best = 0
        for i, (_, taken) in enumerate(self.snaps):
            if taken <= d:
                best = i
        return best


def rank_key(fact, v: Version, live: bool):
    """R3: authority class (carried below every live page) > stated effective date >
    copy freshness (fetched date) > shorter path > URL. Higher sorts first when reversed."""
    cls = v.cls if live else CARRIED_CLASS
    eff = fact.effective or MIN_DATE if fact is not None else MIN_DATE
    return (cls, eff, v.fetched or MIN_DATE, -len(url_path(v.url)), v.url)


def is_expired(fact, v: Version, view: View) -> bool:
    if fact.ends_on and fact.ends_on < view.taken_on:
        return True
    if v.doc.ends_on is not None:
        return True
    return False
