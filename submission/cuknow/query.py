"""Question -> Intent. Uses the same qualifier parser as table rows (quals.py)."""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from . import quals as Q
from .text import EXTRA_STOP, find_dates, normalize_money, pct_values, tokens, words_to_digits

_URL = re.compile(r"\b(link|url|web ?site|web ?page|web address|which page)\b", re.I)
_ONLINE_ACTION = re.compile(r"\b(join|apply|open|enroll|sign up|become a member|register)\b.*\bonline\b|\bonline\b.*\b(join|apply|open|enroll|sign up|register)\b", re.I)
_COUNT = re.compile(r"\bhow many\b|\bnumber of\b", re.I)
_HOURS = re.compile(
    r"\bhours?\b|\bwhat time\b|\bopen (on|until|till|at)\b|\bclose[sd]? at\b|\bopening times?\b"
    r"|\bwhen (?:is|are|do|does)\b[^?]{0,60}\bopen\b",
    re.I,
)
_ADDRESS = re.compile(r"\baddress\b|\bwhere is\b|\bwhere's\b|\blocated\b|\blocation of\b", re.I)
_LIST = re.compile(
    r"\ball\b|\blist\b|\bevery\b|\bwhat (?:\w+ ){0,4}(?:do you|does \w+) (?:offer|have)\b|\bwhich (?:\w+ ){0,4}(?:do you|does \w+) (?:offer|have)\b"
    r"|\boptions\b|\bkinds?\b|\btypes?\b|\bread (?:back|out)\b|\brates for (?:the|your)\b|\b(?:certificate|cd|credit card|loan|savings|checking)s? rates\b",
    re.I,
)
_MAX = re.compile(r"\b(highest|best|top|maximum|max|most|largest|greatest)\b", re.I)
_MIN = re.compile(r"\b(lowest|cheapest|least|smallest)\b", re.I)
_YESNO = re.compile(r"^\s*(do|does|is|are|can|will|did|has|have|could)\b", re.I)
_BRANCH_NAME = re.compile(r"\b(?:the\s+)?((?:[A-Z][\w.'&-]*\s+){0,3}[A-Z][\w.'&-]*)\s+(?i:branch|office)\b")
_NOT_NAMES = {"The", "Your", "Our", "A", "Any", "Each", "Every", "My", "What", "Which", "Where", "Is", "Do", "Does", "Are", "Can"}
_FAMILY_PLURAL = re.compile(r"\b(credit cards|certificates|cds|loans|accounts|cards)\b", re.I)


@dataclass
class Intent:
    raw: str
    norm: str
    toks: list
    ref_date: _dt.date | None
    quals: Q.Quals
    segment_asked: bool
    measure: str | None
    kinds: set = field(default_factory=set)
    q_values: list = field(default_factory=list)   # percent values written in the question
    branch: str | None = None
    hours_kind: str | None = None


def _measure(t: str) -> str | None:
    if re.search(r"\bapy\b|annual percentage yield|\byield\b", t):
        return "apy"
    if re.search(r"\bdividends?\b", t):
        return "dividend"
    if re.search(r"\baprs?\b|annual percentage rate", t):
        return "apr"
    if re.search(r"minimum (?:deposit|balance|amount|to open|opening)|to open (?:a|an|the)\b", t):
        return "min_open"
    if re.search(r"\brates?\b|\binterest\b|\bearn\b|\bpays?\b|\bpaying\b|%", t):
        return "rate"
    if re.search(r"\bfees?\b|\bcharges?\b|\bcosts?\b|how much (?:is|does|do|will|would|for)|\bprice\b", t):
        return "fee"
    return None


def parse_question(question: str) -> Intent:
    raw = (question or "").strip()
    dates = find_dates(raw)
    ref = dates[-1][1] if dates else None
    # Remove the date phrases before qualifier parsing so "May 2026" is not read as a tier/year.
    stripped = raw
    for _, _, s in sorted(dates, key=lambda x: -x[0]):
        stripped = stripped.replace(s, " ")
    stripped = normalize_money(words_to_digits(stripped))
    norm = Q.norm(stripped)
    t = norm
    fam, strong = Q.detect_family([stripped])
    flags = set(Q.detect_flags(stripped))
    pname = Q.product_name(stripped) if fam else None
    if pname and fam not in ("credit_card", "auto", "mortgage", "certificate", "ira"):
        flags.add(pname)
    term = Q.parse_term(re.sub(r"model[- ]year", " ", t))
    tier_kind = lo = hi = None
    my = re.search(r"\b((?:19|20)\d\d)\b(?=[^.?!]{0,25}\b(?:model|vehicle|car|truck|auto|motorcycle|suv)\b)|"
                   r"\b(?:model[- ]year|vehicle|car)\s+((?:19|20)\d\d)\b", t)
    if my:
        y = float(my.group(1) or my.group(2))
        tier_kind, lo, hi = "model_year", y, y
        if fam is None:
            fam, strong = "auto", True
    else:
        mt = Q.parse_money_tier(t, question=True)
        if mt:
            tier_kind, (lo, hi) = "money", mt
    seg_asked = bool(re.search(r"\bbusiness\b", t))
    quals = Q.Quals(fam, strong, "business" if seg_asked else "personal", frozenset(flags), term, tier_kind, lo, hi)
    measure = _measure(t)
    # "How much is a five-year auto loan?" asks for its rate, not a fee, unless a fee word is used
    if measure == "fee" and fam in (Q.LOAN_FAMILIES | Q.DEPOSIT_FAMILIES) \
            and not re.search(r"\b(fees?|charges?|costs?|price)\b", t):
        measure = "rate"
    kinds = set()
    if _URL.search(raw) or (re.search(r"\bonline\b", raw, re.I) and re.search(r"\b(see|find|view|check|look)\b", raw, re.I)):
        kinds.add("url")
    if _ONLINE_ACTION.search(raw):
        kinds.add("online")
    if _COUNT.search(raw):
        kinds.add("count")
    if _HOURS.search(raw) and not re.search(r"24[- ]hour", raw, re.I):
        kinds.add("hours")
    if _ADDRESS.search(raw):
        kinds.add("address")
    if _LIST.search(raw) or (fam and _FAMILY_PLURAL.search(raw) and not term and tier_kind is None):
        kinds.add("list")
    if _MAX.search(raw):
        kinds.add("max")
    if _MIN.search(raw) and measure != "min_open":
        kinds.add("min")
    if _YESNO.search(raw):
        kinds.add("yesno")
    # explicit questions about a change ("previous rate", "how has X changed") - never with now/current
    if re.search(r"\bprevious(?:ly)?\b|\bused to\b|\bold(?:er)? rate\b|\bprior rate\b|\bhow (?:has|have|did) [^?]{0,60}chang|\bwhat changed\b|\bbefore (?:it|the) (?:change|update|increase|decrease)", raw, re.I) \
            and not re.search(r"\b(current|currently|now|today)\b", raw, re.I):
        kinds.add("change")
    branch = None
    for m in _BRANCH_NAME.finditer(raw):
        name = m.group(1).strip()
        words = [w for w in name.split() if w not in _NOT_NAMES and w.lower().rstrip("'s") not in EXTRA_STOP]
        if words:
            branch = " ".join(words)
    hours_kind = None
    if re.search(r"drive[- ]?(?:up|thru|through)", raw, re.I):
        hours_kind = "drive-up"
    elif re.search(r"\blobb(?:y|ies)\b", raw, re.I):
        hours_kind = "lobby"
    q_values = [float(v) for v in pct_values(raw)]
    return Intent(raw, norm, tokens(stripped), ref, quals, seg_asked, measure, kinds, q_values, branch, hours_kind)
