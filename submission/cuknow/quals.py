"""Qualifier parsing shared by table rows and questions (rule R5).

A "slot" is family + segment + variant flags + term + tier + measure. The same parser runs on
both sides so that a question and a table row land on the same slot even when worded
differently ("six month share certificate" vs "6 Month" under a "Certificates" heading).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text import EXTRA_STOP, words_to_digits

INF = float("inf")

# (family, regex, strong?) in priority order. Strong rules name a product explicitly;
# weak rules only hint (used when nothing strong matched anywhere in the context chain).
FAMILY_RULES: list[tuple[str, str, bool]] = [
    ("hsa", r"\bhealth savings\b|\bhsa\b", True),
    ("money_market", r"money market|investor'?s choice", True),
    ("high_yield_savings", r"high[- ]?yield", True),
    ("bright_savings", r"bright savings|online[- ]only savings|online savings", True),
    ("launchpad_savings", r"launch ?pad", True),
    ("holiday_club", r"holiday club", True),
    ("you_name_it", r"you[- ]?name[- ]?it", True),
    ("share_savings", r"share savings", True),
    ("ira", r"\biras?\b|\broth\b|\btraditional ?:", True),
    ("certificate", r"certificates?\b|\bcds?\b|term[- ]share|certificate of deposit", True),
    ("first_rate_checking", r"\b1st rate checking|\bfirst rate checking", True),
    ("free_checking", r"\bfree checking", True),
    ("heloc", r"\bheloc\b|home equity line", True),
    ("home_equity_loan", r"home equity loan", True),
    ("home_improvement", r"home improvement", True),
    ("student_loan", r"student loan", True),
    ("construction_loan", r"construction loan|construction lending|construction-to-perm", True),
    ("mortgage", r"mortgage|home loans?\b|fixed conventional|\bconventional\b|\barm\b|adjustable[- ]rate|\d+/\d+ arm|year fixed", True),
    ("boat_rv", r"\bboats?\b|\brvs?\b|recreational", True),
    ("atv", r"\batvs?\b|snowmobile|jet ski|powersport", True),
    ("auto", r"\bauto\b|motorcycle|\bvehicles?\b|\bcars?\b|\btrucks?\b", True),
    ("personal_loan", r"personal loan|signature loan", True),
    ("credit_card", r"credit cards?|\bvisa\b", True),
    # weak hints
    ("certificate", r"^\s*\d{1,3}\s*-?\s*months?\s*$", False),   # a deposit row labelled only by its term
    ("auto", r"model[- ]year", False),
    ("ira", r"retirement|\btraditional\b", False),
    ("share_savings", r"\bsavings\b", False),
    ("checking", r"\bchecking\b", False),
    ("certificate", r"\bspecials?\b", False),
]
_FAMILY_RX = [(f, re.compile(rx, re.I), s) for f, rx, s in FAMILY_RULES]

LOAN_FAMILIES = {
    "mortgage", "heloc", "home_equity_loan", "home_improvement", "auto", "boat_rv", "atv",
    "personal_loan", "student_loan", "construction_loan", "credit_card",
}
DEPOSIT_FAMILIES = {
    "hsa", "money_market", "high_yield_savings", "bright_savings", "launchpad_savings",
    "holiday_club", "you_name_it", "share_savings", "ira", "certificate", "first_rate_checking",
    "free_checking", "checking",
}
# A question family that stands for several table families.
FAMILY_GROUPS = {
    "checking": {"checking", "free_checking", "first_rate_checking"},
}

FLAG_RX = {
    "jumbo": r"\bjumbo\b",
    "special": r"\bspecials?\b|\bpromo(?:tional)?\b",
    "roth": r"\broth\b",
    "traditional": r"\btraditional\b",
    "purchase": r"\bpurchase\b|\bbuy(?:ing)?\b",
    "refinance": r"\brefinanc\w*|\brefi\b",
    "intro": r"\bintro(?:ductory)?\b|balance transfer",
    "fixed": r"\bfixed\b",
    "variable": r"\bvariable\b",
    "arm": r"\barms?\b|adjustable|\b\d/\d\b",
    "platinum": r"\bplatinum\b",
    "rewards": r"\brewards?\b",
}
_FLAG_RX = {k: re.compile(v, re.I) for k, v in FLAG_RX.items()}

# Flags where the question must match the row exactly when the row carries the flag.
# (A silent question means the base product: non-jumbo, purchase, standard, not intro.)
BASE_EXCLUDED = ("jumbo", "intro", "refinance", "arm")
# Flags the row must carry if the question names them.
REQUIRED_IF_ASKED = ("jumbo", "special", "roth", "traditional", "refinance", "intro", "arm",
                     "platinum", "rewards", "purchase")
OPPOSITES = (("fixed", "variable"), ("fixed", "arm"), ("purchase", "refinance"),
             ("roth", "traditional"), ("platinum", "rewards"))


@dataclass(frozen=True)
class Quals:
    family: str | None = None
    family_strong: bool = False
    segment: str = "personal"
    flags: frozenset = field(default_factory=frozenset)
    term: int | None = None            # months
    tier_kind: str | None = None       # 'money' | 'model_year'
    lo: float | None = None
    hi: float | None = None


def norm(text: str) -> str:
    t = words_to_digits((text or "").lower())
    t = t.replace("&", " and ").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", t)


def detect_family(chain: list[str]) -> tuple[str | None, bool]:
    """First strong family over the chain (row label, context, heading, title), else first weak."""
    for strong_pass in (True, False):
        for text in chain:
            if not text:
                continue
            t = norm(text)
            for fam, rx, strong in _FAMILY_RX:
                if strong == strong_pass and rx.search(t):
                    return fam, strong
    return None, False


_NAME_IGNORE = {
    "regular", "standard", "traditional", "basic", "personal", "business", "new", "used", "term", "share",
    "the", "a", "an", "my", "your", "our", "and", "or", "of", "for", "with", "fixed",
    "variable", "jumbo", "special", "month", "months", "year", "years", "purchase", "refinance", "intro",
    "introductory", "platinum", "rewards", "roth", "ira", "online", "what", "is", "whats", "s", "on", "rate",
    "rates", "apy", "apr", "current", "today", "account", "accounts", "how", "much", "does", "do", "pay", "earn",
}


def product_name(text: str) -> str | None:
    """A named variant in front of the family noun ('Achieve' Money Market) is its own product:
    the capitalized word directly before the family noun, unless it is a qualifier word."""
    raw = (text or "").replace("\u2019", "'")
    for fam, rx, strong in _FAMILY_RX:
        if not strong:
            continue
        m = rx.search(raw)
        if not m:
            continue
        before = re.findall(r"[A-Za-z][A-Za-z']*", raw[: m.start()])
        if not before or not raw[: m.start()].rstrip().endswith(before[-1]):
            return None
        w = before[-1]
        key = w.lower().replace("'", "")
        if not w[0].isupper() or key in _NAME_IGNORE or key in EXTRA_STOP or len(key) < 3:
            return None
        return "n:" + key
    return None


def detect_flags(text: str) -> frozenset:
    t = norm(text)
    return frozenset(k for k, rx in _FLAG_RX.items() if rx.search(t))


_MY_RANGE = re.compile(r"(?:model[- ]year\s*)?\b(19\d\d|20\d\d)\s*-\s*(\d{2,4})\b(?:\s*model[- ]year)?")
_MY_OLDER = re.compile(r"\b(19\d\d|20\d\d)\s*model[- ]year\s*\(?and older\)?")
_MY_SINGLE = re.compile(r"\b(19\d\d|20\d\d)\s*model[- ]year|model[- ]year\s*(19\d\d|20\d\d)\b")
_TERM_MO = re.compile(r"\b(\d{1,3})\s*-?\s*(?:months?|mos?\.?)(?![a-z])")
_TERM_YR = re.compile(r"\b(\d{1,2})\s*-?\s*(?:years?|yrs?)\b(?!\s*-?\s*old)")


def _expand_year(a: int, b_raw: str) -> int:
    if len(b_raw) == 4:
        return int(b_raw)
    return (a // 100) * 100 + int(b_raw)


def parse_model_year(t: str) -> tuple[tuple[float, float] | None, str]:
    """Model-year tier from a row label. Returns ((lo, hi) or None, text with it removed)."""
    m = _MY_OLDER.search(t)
    if m:
        return (1900.0, float(m.group(1))), t[: m.start()] + " " + t[m.end():]
    if "model" in t:
        m = _MY_RANGE.search(t)
        if m:
            a = int(m.group(1))
            b = _expand_year(a, m.group(2))
            lo, hi = sorted((a, b))
            return (float(lo), float(hi)), t[: m.start()] + " " + t[m.end():]
        m = _MY_SINGLE.search(t)
        if m:
            y = float(m.group(1) or m.group(2))
            return (y, y), t[: m.start()] + " " + t[m.end():]
    return None, t


def parse_term(t: str) -> int | None:
    m = _TERM_MO.search(t)
    if m:
        return int(m.group(1))
    m = _TERM_YR.search(t)
    if m:
        return int(m.group(1)) * 12
    return None


_MONEY_NUM = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(k\b|m\b|million|thousand)?"


def _mv(m: re.Match, i: int) -> float:
    v = float(m.group(i).replace(",", "") + ("." + m.group(i + 1) if m.group(i + 1) else ""))
    mult = (m.group(i + 2) or "").lower()
    if mult in ("k", "thousand"):
        v *= 1000
    elif mult in ("m", "million"):
        v *= 1_000_000
    return v


_TIER_RANGE = re.compile(_MONEY_NUM + r"\s*(?:-|to|and)\s*\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(k\b|m\b|million|thousand)?")
_TIER_PLUS = re.compile(_MONEY_NUM + r"\s*(?:\+|or more|and (?:up|above|over)|or higher)")
_TIER_LESS = re.compile(_MONEY_NUM + r"\s*(?:or less|and (?:under|below|less))")
_TIER_UPTO = re.compile(r"(?:up to|less than|under|below|at most|no more than)\s*" + _MONEY_NUM)
_TIER_OVER = re.compile(r"(?:over|above|more than|at least|greater than)\s*" + _MONEY_NUM)
_TIER_POINT = re.compile(_MONEY_NUM)


def parse_money_tier(t: str, question: bool = False) -> tuple[float, float] | None:
    m = _TIER_RANGE.search(t)
    if m:
        a = _mv(m, 1)
        b = _mv(m, 4)
        if b >= a:
            return (a, b)
    m = _TIER_PLUS.search(t)
    if m:
        return (_mv(m, 1), INF)
    m = _TIER_LESS.search(t)
    if m:
        return (0.0, _mv(m, 1))
    m = _TIER_UPTO.search(t)
    if m:
        return (0.0, _mv(m, 1))
    m = _TIER_OVER.search(t)
    if m:
        return (_mv(m, 1), INF)
    if question:
        m = _TIER_POINT.search(t)
        if m:
            v = _mv(m, 1)
            return (v, v)
    return None


def parse_row(label: str, context_chain: list[str], url_path: str = "") -> Quals:
    """Qualifiers for a table row or a 'label: value' line.

    label: the row's non-value cells joined; context_chain: nearest label line, heading,
    table header, page title (most specific first).
    """
    t = norm(label)
    my, t_wo = parse_model_year(t)
    fam, strong = detect_family([label] + context_chain)
    ctx = " ".join(c for c in context_chain[:2] if c)
    flags = set(detect_flags(label))
    # Context flags matter for jumbo/special/IRA type/variable; not for purchase/refi (row says it).
    for f in detect_flags(ctx):
        if f in ("jumbo", "special", "variable", "fixed", "platinum", "rewards", "intro", "roth", "traditional", "refinance", "purchase") and not _conflicting(f, flags):
            flags.add(f)
    term = parse_term(t_wo)
    name = product_name(label) if strong else None
    if name and fam not in ("credit_card", "auto", "mortgage", "certificate", "ira"):
        flags.add(name)
    tier_kind = lo = hi = None
    if my:
        tier_kind, (lo, hi) = "model_year", my
    else:
        mt = parse_money_tier(t_wo)
        if mt:
            tier_kind, (lo, hi) = "money", mt
    seg_text = norm(label + " " + ctx)
    segment = "business" if ("business" in seg_text or url_path.startswith(("/business", "business"))) else "personal"
    if fam == "credit_card" and "platinum" not in flags and "rewards" not in flags:
        f2 = detect_flags(" ".join(context_chain))
        for f in ("platinum", "rewards"):
            if f in f2:
                flags.add(f)
                break
    return Quals(fam, strong, segment, frozenset(flags), term, tier_kind, lo, hi)


def _conflicting(f: str, flags: set) -> bool:
    for a, b in OPPOSITES:
        if (f == a and b in flags) or (f == b and a in flags):
            return True
    return False


def overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> float:
    """Jaccard-style overlap of two closed ranges (points count as width 1 for years)."""
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    if hi < lo:
        return 0.0
    if a_hi == INF or b_hi == INF:
        return 1.0 if (a_hi == INF and b_hi == INF) else 0.5
    inter = hi - lo + 1
    union = max(a_hi, b_hi) - min(a_lo, b_lo) + 1
    return inter / union if union > 0 else 1.0


def tier_label(q: Quals) -> str:
    if q.tier_kind == "model_year" and q.lo is not None:
        if q.lo <= 1900:
            return "{} model year and older".format(int(q.hi))
        if q.lo == q.hi:
            return "{} model year".format(int(q.lo))
        return "{}-{} model years".format(int(q.lo), str(int(q.hi))[-2:])
    if q.tier_kind == "money" and q.lo is not None:
        if q.hi == INF:
            return "${:,.0f}+".format(q.lo)
        if q.lo == 0:
            return "${:,.0f} or less".format(q.hi)
        hi = "{:,.2f}".format(q.hi) if q.hi != int(q.hi) else "{:,.0f}".format(q.hi)
        lo = "{:,.2f}".format(q.lo) if q.lo != int(q.lo) else "{:,.0f}".format(q.lo)
        return "${}-${}".format(lo, hi)
    return ""


def term_label(months: int | None, family: str | None) -> str:
    if not months:
        return ""
    if family == "mortgage" and months % 12 == 0:
        return "{}-year".format(months // 12)
    return "{}-month".format(months)
