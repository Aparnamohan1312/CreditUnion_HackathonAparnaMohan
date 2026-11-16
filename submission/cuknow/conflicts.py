"""Conflicts: places where the website contradicts itself (precision first).

An entry is produced for a slot (R5) when its value disagrees
  (a) between live pages in the newest snapshot (a stale page is still up), or
  (b) between the slot's winning value in an earlier snapshot and now (the value moved).
Branch-count claims are included (R4). Growth counts (members, employees, assets) are not.
"""
from __future__ import annotations

import re
from collections import defaultdict

from contract import Conflict, Source  # harness module, put on sys.path by solution.py

from . import quals as Q
from .kb import KB, MIN_DATE, is_expired, rank_key
from .pages import CLASS_NAMES, LISTING, url_path

MEASURE_LABEL = {"apy": "APY", "apr": "APR", "rate": "rate", "dividend": "dividend rate"}
HEADLINE = ("apy", "apr", "rate", "dividend")
FAMILY_NOUN = {
    "certificate": "certificate", "ira": "IRA certificate", "share_savings": "Share Savings",
    "money_market": "Investor's Choice money market", "high_yield_savings": "High-Yield Savings",
    "hsa": "Health Savings Account", "bright_savings": "Bright Savings", "launchpad_savings": "Launchpad Savings",
    "holiday_club": "Holiday Club Savings", "you_name_it": "You Name It Savings",
    "first_rate_checking": "1st Rate Checking", "free_checking": "Free Checking", "checking": "checking",
    "auto": "auto loan", "mortgage": "mortgage", "heloc": "HELOC", "home_equity_loan": "home equity loan",
    "home_improvement": "home improvement loan", "boat_rv": "boat & RV loan", "atv": "ATV loan",
    "personal_loan": "personal loan", "credit_card": "credit card", "student_loan": "student loan",
}
MAX_CONFLICTS = 40


def _src(v, f) -> Source:
    return Source(url=v.url, snapshot_id=v.snapshot_id, quote=re.sub(r"\s+", " ", f.quote or "")[:200])


def topic_for(f) -> str:
    q = f.quals
    parts = []
    tl = Q.term_label(q.term, q.family)
    if tl:
        parts.append(tl)
    for fl in ("business", ):
        if q.segment == "business":
            parts.append("business")
    for fl in ("roth", "traditional", "jumbo", "platinum", "rewards", "intro", "refinance"):
        if fl in q.flags:
            parts.append({"intro": "introductory"}.get(fl, fl))
    noun = FAMILY_NOUN.get(q.family, q.family or "")
    names = [fl[2:] for fl in sorted(q.flags) if fl.startswith("n:")]
    if names:
        noun = "{} {}".format(names[0].capitalize(), noun.split()[-1] if q.family == "money_market" else noun)
        if q.family == "money_market":
            noun = noun.replace(" market", " money market")
    parts.append(noun)
    if "special" in q.flags:
        parts.append("special")          # "10-month jumbo certificate special", as the site names it
    parts.append(MEASURE_LABEL.get(f.measure, f.measure))
    t = " ".join(p for p in parts if p)
    tier = Q.tier_label(q)
    if tier:
        t += " ({})".format(tier)
    return t


def _why(win, wv, wlive, los, lv, llive) -> str:
    if not llive:
        return "{} is no longer on the site".format(url_path(lv.url))
    if wv.cls != lv.cls:
        return "{} {} outranks {} {}".format(CLASS_NAMES.get(wv.cls, "page"), url_path(wv.url), CLASS_NAMES.get(lv.cls, "page"), url_path(lv.url))
    if (win.effective or MIN_DATE) != (los.effective or MIN_DATE):
        return "newer effective date on {} ({}) than {} ({})".format(
            url_path(wv.url), win.effective.isoformat() if win.effective else "none",
            url_path(lv.url), los.effective.isoformat() if los.effective else "none")
    return "fresher copy of {} (fetched {})".format(url_path(wv.url), wv.fetched)


def _best_partner(f, canon: list):
    """Canonical row whose tier best overlaps f's tier (handles relabeled model-year tiers)."""
    q = f.quals
    if not q.tier_kind:
        for c in canon:
            if not c[0].quals.tier_kind:
                return c
        return None
    best, bo = None, 0.0
    for c in canon:
        cq = c[0].quals
        if cq.tier_kind != q.tier_kind:
            continue
        o = Q.overlap(cq.lo, cq.hi, q.lo, q.hi)
        if o > bo:
            best, bo = c, o
    return best if bo >= 0.3 else None


FAMILY_ORDER = ["certificate", "auto", "mortgage", "share_savings", "first_rate_checking", "free_checking",
                "credit_card", "money_market", "bright_savings", "high_yield_savings", "personal_loan", "heloc",
                "home_equity_loan", "home_improvement", "boat_rv", "atv", "ira", "hsa"]


def build_conflicts(kb: KB) -> list:
    if not kb.views:
        return []
    n = len(kb.views) - 1
    vx = kb.vidx[n]
    entries = []
    for fam in sorted(vx.facts_by_family):
        try:
            entries.extend(_family_conflicts(kb, n, fam))
        except Exception:
            continue
    fam_rank = {f: i for i, f in enumerate(FAMILY_ORDER)}
    entries.sort(key=lambda e: (-e["prio"], e["segment"] != "personal", fam_rank.get(e["family"], 99), e["topic"]))
    out = []
    try:
        bc = _branch_count(kb, n)
        if bc is not None:
            out.append(bc)
    except Exception:
        pass
    try:
        out.extend(_statement_conflicts(kb, n))
    except Exception:
        pass
    seen = set()
    for e in entries:
        # the same disagreement shown again (business or IRA mirror of a personal table) counts once
        if e["sig"] in seen:
            continue
        seen.add(e["sig"])
        try:
            out.append(Conflict(topic=e["topic"], values=e["values"], sources=e["sources"][: len(e["values"])],
                                resolution=e["resolution"], reason="; ".join(e["reasons"][:3])))
        except Exception:
            continue
    return out[:MAX_CONFLICTS]


def _slot(f):
    q = f.quals
    return (q.family, q.segment, tuple(sorted(q.flags)), q.term, q.tier_kind)


def _canonical(rows, view):
    """The winning page's rows for a slot group: {tier (lo, hi): {measure: fact}}."""
    live_valid = [c for c in rows if c[2] and not is_expired(c[0], c[1], view)]
    if not live_valid:
        return None, {}
    best = max(live_valid, key=lambda c: rank_key(c[0], c[1], c[2]))
    page = best[1]
    canon = defaultdict(dict)
    for f, v, live in live_valid:
        if v is page:
            canon[(f.quals.lo, f.quals.hi)].setdefault(f.measure, (f, v, live))
    return page, canon


def _partner(f, tiers):
    q = f.quals
    if not q.tier_kind:
        return (None, None) if (None, None) in tiers else None
    best, bo = None, 0.0
    for t in tiers:
        if t[0] is None:
            continue
        o = Q.overlap(t[0], t[1], q.lo, q.hi)
        if o > bo:
            best, bo = t, o
    return best if bo >= 0.3 else None


def _family_conflicts(kb: KB, n: int, fam: str) -> list:
    view = kb.views[n]
    rows = [c for c in kb.vidx[n].facts_by_family[fam]
            if c[1].cls != LISTING and c[0].measure in HEADLINE and c[0].key]
    groups = defaultdict(list)
    for c in rows:
        groups[_slot(c[0])].append(c)
    out = []
    for gk in sorted(groups, key=str):
        gc = groups[gk]
        page, canon = _canonical(gc, view)
        if page is None:
            continue
        history = []
        for k in range(n - 1, -1, -1):
            hrows = [c for c in kb.vidx[k].facts_by_family.get(fam, [])
                     if c[1].cls != LISTING and c[0].measure in HEADLINE and c[0].key and _slot(c[0]) == gk]
            hpage, hcanon = _canonical(hrows, kb.views[k])
            if hpage is not None:
                history.append((k, hpage, hcanon))
        for tier in sorted(canon, key=str):
            winners = canon[tier]
            win_keys = {m: wf[0].key for m, wf in winners.items()}
            losers = []      # (value, key, version, fact, reason)
            current = False
            for f, v, live in gc:
                if v is page or not live or f.measure not in winners:
                    continue
                if _partner(f, list(canon)) != tier or f.key == win_keys[f.measure]:
                    continue
                wf, wv, wl = winners[f.measure]
                reason = ("expired promotion on {}".format(url_path(v.url)) if is_expired(f, v, view)
                          else _why(wf, wv, wl, f, v, live))
                losers.append((f.value, f.key, v, f, reason))
                current = True
            for k, hpage, hcanon in history:
                anchor = next(iter(winners.values()))[0]
                ht = _partner(anchor, list(hcanon))
                if ht is None:
                    continue
                for m, (hf, hv, hl) in sorted(hcanon[ht].items()):
                    if m in win_keys and hf.key != win_keys[m]:
                        losers.append((hf.value, hf.key, hv, hf, "value changed: {} {} in snapshot {} ({}), {} now".format(
                            hf.value, MEASURE_LABEL.get(m, m), kb.views[k].snapshot_id, url_path(hv.url), winners[m][0].value)))
            if not losers:
                continue
            ms = [m for m in HEADLINE if m in winners]
            head = winners[ms[0]][0]
            values, keys = [], set()
            for m in ms:
                f0 = winners[m][0]
                if f0.key not in keys:
                    keys.add(f0.key)
                    values.append(f0.value)
            reasons, sources = [], [_src(winners[ms[0]][1], head)]
            for val, key, v, f, why in losers:
                if key in keys:
                    continue
                keys.add(key)
                values.append(val)
                if why not in reasons:
                    reasons.append(why)
                sources.append(_src(v, f))
            if len(values) < 2:
                continue
            labels = [MEASURE_LABEL.get(m, m) for m in ms]
            topic = topic_for(head)
            if len(ms) > 1:
                topic = topic.replace(" " + MEASURE_LABEL.get(head.measure, head.measure), " " + " and ".join(labels), 1)
            out.append({
                "prio": 2 if current else 1, "segment": gk[1], "family": fam, "topic": topic,
                "values": values, "sources": sources, "reasons": reasons,
                "resolution": " / ".join("{} {}".format(winners[m][0].value, MEASURE_LABEL.get(m, m)) for m in ms),
                "sig": (gk[3], tuple(sorted(keys))),
            })
    return out


def _branch_count(kb: KB, n: int):
    claims = [c for c in kb.vidx[n].claims if c[0].noun == "branch" and c[2]]
    if len({c[0].value for c in claims}) < 2:
        return None

    def key(c):
        cl, v, live = c
        return (v.doc.pub is not None, v.doc.pub or MIN_DATE, v.cls, v.fetched or MIN_DATE, v.url)

    claims.sort(key=key, reverse=True)
    win = claims[0]
    counts = defaultdict(int)
    first = {}
    for c in claims:
        counts[c[0].value] += 1
        first.setdefault(c[0].value, c)
    others = sorted((v for v in counts if v != win[0].value), key=lambda v: (-counts[v], v))
    values = [str(win[0].value)] + [str(v) for v in others]
    srcs = [Source(url=win[1].url, snapshot_id=win[1].snapshot_id, quote=win[0].sentence[:200])]
    for v in others:
        c = first[v]
        srcs.append(Source(url=c[1].url, snapshot_id=c[1].snapshot_id, quote=c[0].sentence[:200]))
    pub = win[1].doc.pub.isoformat() if win[1].doc.pub else "undated"
    reason = "newest dated statement ({}) wins; older press releases and blog posts repeat earlier counts".format(pub)
    return Conflict(topic="branch count (number of branch office locations)", values=values, sources=srcs,
                    resolution=str(win[0].value), reason=reason)


# R4 statements: the same sentence template restated on several live, dated pages with a different
# count or ordinal ("This was the fifth time Forbes recognized ..." vs "... fourth time ...").
# Dated events and growth figures are time series, not contradictions, so they are excluded.
_ORDINAL = r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b"
_NOT_A_STANDING_FACT = re.compile(
    r"[$%]|\b(19|20)\d\d\b|\b(january|february|march|april|may|june|july|august|september|october|november|"
    r"december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b|\bthis year\b|\bannual\b|\bconsecutive\b|"
    r"\banniversary\b|\btoday\b|\bmembers?\b|\bmembership\b|member-owners?|\bemployees?\b|\bassets?\b|"
    r"\bbillion\b|\bmillion\b|\bstaff\b|\bhours?\b|\bdividends?\b|\bbranch",
    re.I,
)


def _statement_conflicts(kb: KB, n: int) -> list:
    from .text import mask_template
    brand = (kb.brand or "").lower()
    if not brand:
        return []
    groups = defaultdict(list)
    for key in sorted(kb.vidx[n].by_key):
        v, live = kb.vidx[n].by_key[key]
        if not live or v.doc.pub is None or v.cls == LISTING:
            continue
        for p in v.doc.passages:
            t = p.text
            if p.kind not in ("p", "li") or len(t) > 220 or brand not in t.lower() or _NOT_A_STANDING_FACT.search(t):
                continue
            vals = tuple(x.lower() for x in re.findall(r"\b\d[\d,]*\b", t) + re.findall(_ORDINAL, t, re.I))
            if vals:
                groups[mask_template(t)].append((v.doc.pub, v.url, vals, t, v))
    out = []
    for k in sorted(groups):
        g = groups[k]
        if len({x[2] for x in g}) < 2 or len({x[1] for x in g}) < 2:
            continue
        g.sort(key=lambda x: (x[0], x[1]), reverse=True)
        win = g[0]
        values, srcs, seen = [], [], set()
        for pub, url, vals, t, v in g:
            val = " ".join(vals)
            if val in seen:
                continue
            seen.add(val)
            values.append(val)
            srcs.append(Source(url=v.url, snapshot_id=v.snapshot_id, quote=t[:200]))
        topic = re.sub(r"\b\d[\d,]*\b|" + _ORDINAL, "N", win[3], flags=re.I).rstrip(". ")
        out.append(Conflict(topic=topic, values=values, sources=srcs, resolution=values[0],
                            reason="newest dated statement ({}) wins; older articles repeat the earlier figure".format(win[0].isoformat())))
    return out
