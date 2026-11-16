"""Pages: URL canonicalization, authority class, publication date, versions and views.

Rule R1 (carry-forward, demoted) lives here: a view holds every URL seen up to that
snapshot, each at its latest version, flagged live (present in the snapshot) or carried.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .text import FULL_DATE, MONTHS, find_dates, parse_iso

# Authority classes (R3.1). Higher is more authoritative.
OFFICIAL = 3      # product, rate, service, contact pages; official documents (fee schedule)
TOOL = 2          # calculators, landing/offer/promo pages, other PDFs
EDITORIAL = 1     # blog, newsroom, business resources, dated paths
LISTING = 0       # tag/author/category listings: never used as evidence

CLASS_NAMES = {OFFICIAL: "official page", TOOL: "calculator/promo page", EDITORIAL: "article", LISTING: "listing page"}


def canon_url(url: str) -> str:
    """Host without www + path without trailing slash + query; fragment dropped."""
    p = urlsplit(url if "//" in url else "https://" + url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    q = "?" + p.query if p.query else ""
    return host + path.lower() + q


def url_path(url: str) -> str:
    p = urlsplit(url if "//" in url else "https://" + url)
    return (p.path.rstrip("/") or "/").lower()


_LISTING = re.compile(r"/(tag|tags|author|category|categories|page)/")
_EDITORIAL = re.compile(r"/(learn|newsroom|news|blog|business/resources|press|stories)(/|$)|/20\d\d/\d\d/\d\d/")
_TOOL = re.compile(r"calculator|-lp$|/lp/|landing|/promo|promo$|offer")
_OFFICIAL_DOC = re.compile(r"fee[-_]schedule|disclosure|rate[-_]sheet|truth[-_]in|agreement|terms[-_]and|schedule[-_]of")
_DOC_EDITORIAL = re.compile(r"owners[-_]manual|newsletter|webinar|presentation|annual[-_]report")


def page_class(url: str, title: str) -> int:
    path = url_path(url)
    if _LISTING.search(path + "/"):
        return LISTING
    if "/hubfs/" in path or re.search(r"\.(pdf|csv|docx?)$", path):
        if _OFFICIAL_DOC.search(path):
            return OFFICIAL
        if _DOC_EDITORIAL.search(path):
            return EDITORIAL
        return TOOL
    if _EDITORIAL.search(path):
        return EDITORIAL
    if _TOOL.search(path):
        return TOOL
    return OFFICIAL


def pub_date(url: str, text: str, cls: int) -> _dt.date | None:
    """Publication date of an article: the first full date in the header (before or just
    after the H1), else a /YYYY/MM/DD/ path. Non-articles have no publication date."""
    m = re.search(r"/(20\d\d)/(\d\d)/(\d\d)/", url)
    if m:
        try:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if cls != EDITORIAL:
        return None
    head = text[:700]
    m = FULL_DATE.search(head)
    if m:
        try:
            return _dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None


def page_effective(text: str) -> _dt.date | None:
    """Page-level 'Effective <date>' near the top (fee schedules, disclosures)."""
    m = re.search(r"effective(?: as of)?:?\s+(.{0,40})", text[:1500], re.I)
    if not m:
        return None
    ds = find_dates(m.group(1))
    return ds[0][1] if ds else None


@dataclass
class Version:
    """One crawled copy of one URL."""
    vid: int
    key: str                 # canonical URL
    url: str                 # original URL (for citations)
    title: str
    snap_idx: int
    snapshot_id: str
    fetched_at: str
    fetched: _dt.date | None
    cls: int
    doc: object              # ParsedDoc (facts.py), shared between identical copies


@dataclass
class View:
    """The website as it stood at one snapshot (R0/R1)."""
    idx: int
    snapshot_id: str
    taken_on: _dt.date
    pages: dict = field(default_factory=dict)   # key -> (Version, live: bool)

    def live(self, key: str) -> bool:
        e = self.pages.get(key)
        return bool(e and e[1])


def fetched_date(s: str) -> _dt.date | None:
    return parse_iso(s)
