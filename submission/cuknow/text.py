"""Text utilities: cleaning crawled markdown, tokenizing, number words, dates.

Everything here is pure and deterministic. The same normalizers run on page text and on
questions, so "six month share certificate" and "6 Month Certificate" meet in the middle.
"""
from __future__ import annotations

import datetime as _dt
import html
import re

# ---------------------------------------------------------------- cleaning

_LITERAL_CRLF = re.compile(r"(?:\\+r)?\\+n")
_DATA_URI_IMG = re.compile(r"!\[[^\]]*\]\((?:data:[^)]*|[^)]*)\)")
_DATA_URI = re.compile(r"data:[a-z]+/[a-z0-9.+-]+[;,][^\s)\]]*", re.I)
_LINK = re.compile(r"\[([^\]\n]{0,300})\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MD_ESCAPE = re.compile(r"\\([*_\[\]()#+\-.!$`~>])")
_BR = re.compile(r"<br\s*/?>", re.I)
_TAGS = re.compile(r"</?(?:span|div|p|strong|em|b|i|u|sup|sub|small)[^>]*>", re.I)
_BOLD = re.compile(r"(\*\*|__)")
_WS = re.compile(r"[ \t\u00a0\u2009\u202f]+")
_FLAG_EMOJI = re.compile("[\U0001F1E6-\U0001F1FF]{2}")


def clean_markdown(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (clean text, links). Links are (anchor text, href) in page order.

    Handles the noise seen in the crawls: double-escaped HTML entities (&amp;#45;),
    literal "\\r\\n" sequences, markdown escapes, inline images and data-URIs, <br>.
    """
    s = md or ""
    s = html.unescape(html.unescape(s))
    s = _LITERAL_CRLF.sub("\n", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _BR.sub("\n", s)
    s = _TAGS.sub("", s)
    s = _DATA_URI_IMG.sub("", s)
    s = _DATA_URI.sub("", s)
    links: list[tuple[str, str]] = []

    def _link(m: re.Match) -> str:
        text = m.group(1).strip()
        href = m.group(2).strip()
        links.append((_BOLD.sub("", text), href))
        return text

    # Links can nest one level (image inside link was already removed).
    s = _LINK.sub(_link, s)
    s = _MD_ESCAPE.sub(r"\1", s)
    s = _BOLD.sub("", s)
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = _WS.sub(" ", s)
    lines = []
    for line in s.split("\n"):
        line = line.strip()
        if _FLAG_EMOJI.search(line):        # phone country pickers: noise, not content
            continue
        # re-join soft-wrapped prose: previous line has no end punctuation, this one starts lowercase
        if (lines and line and line[0].islower() and lines[-1] and not lines[-1].startswith(("|", "#", "-", "*"))
                and not re.search(r"[.!?:;|]$", lines[-1])):
            lines[-1] = lines[-1] + " " + line
            continue
        lines.append(line)
    s = "\n".join(lines)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s, links


def strip_footnote(cell: str) -> str:
    """'5.39% APR1' -> '5.39% APR'; '0.20%1' -> '0.20%'; '$5.00*' -> '$5.00'."""
    c = cell.strip()
    c = re.sub(r"(%|APR|APY)\s*\d{1,2}\b", r"\1", c)
    c = re.sub(r"(\d)\s*[*\u00b9\u00b2\u00b3\u2020\u2021]+", r"\1", c)
    c = re.sub(r"[\u00b9\u00b2\u00b3]", "", c)
    return c.strip()


# ---------------------------------------------------------------- numbers

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMWORD = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(k for k in _UNITS if _UNITS[k] < 10 and _UNITS[k] > 0) + r"))?"
    r"|(" + "|".join(_UNITS) + r"))\b",
    re.I,
)
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}


def words_to_digits(text: str) -> str:
    """'six month' -> '6 month', 'eighty-four' -> '84', 'a year' stays."""

    def _rep(m: re.Match) -> str:
        if m.group(3):
            return str(_UNITS[m.group(3).lower()])
        val = _TENS[m.group(1).lower()]
        if m.group(2):
            val += _UNITS[m.group(2).lower()]
        return str(val)

    return _NUMWORD.sub(_rep, text)


_SCALED = re.compile(
    r"(\$\s?)?\b(\d+(?:\.\d+)?)(?:\s+hundred(?:\s+(?:and\s+)?(\d+))?)?\s*(k|thousand|m|mil|million)\b(?:\s*(?:dollars|bucks|usd))?",
    re.I,
)
_DOLLARS = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(?:dollars|bucks|usd)\b", re.I)


def normalize_money(text: str) -> str:
    """Questions write money many ways: '15k', '15 thousand dollars' (after number words),
    'one hundred thousand', '500 dollars' -> '$15000', '$100000', '$500'."""

    def _scaled(m: re.Match) -> str:
        base = float(m.group(2))
        if re.search(r"hundred", m.group(0), re.I):
            base = base * 100 + float(m.group(3) or 0)
        unit = m.group(4).lower()
        mult = 1_000 if unit in ("k", "thousand") else 1_000_000
        return "${:.0f}".format(base * mult)

    text = _SCALED.sub(_scaled, text)
    return _DOLLARS.sub(lambda m: "$" + m.group(1) + ("." + m.group(2) if m.group(2) else ""), text)


_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(k|m|million|thousand)?\b", re.I)


def parse_money(s: str) -> float | None:
    m = _MONEY.search(s)
    if not m:
        return None
    return _money_val(m)


def _money_val(m: re.Match) -> float:
    v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
    mult = (m.group(3) or "").lower()
    if mult in ("k", "thousand"):
        v *= 1000
    elif mult in ("m", "million"):
        v *= 1_000_000
    return v


def money_values(s: str) -> list[float]:
    return [_money_val(m) for m in _MONEY.finditer(s)]


_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def pct_values(s: str) -> list[str]:
    return [m.group(1) for m in _PCT.finditer(s)]


def num_key(v: str) -> float:
    """Value key used to compare '6.250%' with '6.25%' or '$2.5' with '$2.50'."""
    try:
        return round(float(v.replace(",", "").replace("$", "").replace("%", "").strip()), 4)
    except ValueError:
        return float("nan")


# ---------------------------------------------------------------- tokens

STOP = frozenset(
    """a an the and or of to for in on at by with from as is are was were be been being do does did
    doing have has had what whats which who whom whose when where why how i me my we our ours you your
    yours it its this that these those can could would should will shall may might must there here
    please tell give get let know about any some all much many more most also just than then so if
    into over under up down out off very really current currently right now today still
    credit union cu like want need am im id ive youre us said say says use using add make
    going go looking trying wondering thinking planning interested anyone someone something
    again repeat remind""".split()
)


_SUFFIXES = (
    ("ational", "ate"), ("ization", "ize"), ("ibility", ""), ("ability", ""), ("ility", ""),
    ("ition", ""), ("ation", ""), ("ments", ""), ("ment", ""), ("ness", ""), ("ized", ""),
    ("izes", ""), ("ize", ""), ("ible", ""), ("able", ""), ("ing", ""), ("ies", "y"),
    ("sses", "ss"), ("xes", "x"), ("ches", "ch"), ("shes", "sh"), ("ed", ""), ("ly", ""),
)


# Site words that carry no meaning in questions (the brand name), registered at ingest from the
# data itself (kb.py), never hard-coded.
EXTRA_STOP: set = set()


def register_site_words(words) -> None:
    for w in words:
        w = (w or "").lower().strip()
        if len(w) >= 4:
            EXTRA_STOP.add(w)
            EXTRA_STOP.add(w + "s")


def stem(w: str) -> str:
    """Light suffix stripper: 'recognition'/'recognized' -> 'recogn', 'rates' -> 'rate'.
    The same function runs on questions and passages, so over-stemming is harmless."""
    if len(w) <= 3 or w[0].isdigit():
        return w
    for suf, rep in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) + len(rep) >= 4:
            return w[: -len(suf)] + rep
    if w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 4:
        return w[:-1]
    return w


_TOK = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    t = words_to_digits(text.lower()).replace("'s", "").replace("$", " ")
    out = []
    for w in _TOK.findall(t):
        w = w.replace("'", "")
        if not keep_stop and (w in STOP or w in EXTRA_STOP):
            continue
        out.append(stem(w))
    return out


# ---------------------------------------------------------------- dates

MONTHS = {
    m: i + 1
    for i, ms in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ]
    )
    for m in ms
}
_MONTH_RE = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
FULL_DATE = re.compile(_MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d|19\d\d)", re.I)
MONTH_YEAR = re.compile(_MONTH_RE + r",?\s+(20\d\d)\b", re.I)
SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
ISO_DATE = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")


def _safe_date(y: int, m: int, d: int) -> _dt.date | None:
    try:
        return _dt.date(y, m, d)
    except ValueError:
        return None


def month_end(y: int, m: int) -> _dt.date:
    nxt = _dt.date(y + (m // 12), m % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def find_dates(text: str) -> list[tuple[int, _dt.date, str]]:
    """All explicit dates in text: (position, date, matched text). Month-year -> month end."""
    out = []
    taken = []
    for m in FULL_DATE.finditer(text):
        d = _safe_date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        if d:
            out.append((m.start(), d, m.group(0)))
            taken.append((m.start(), m.end()))
    for m in ISO_DATE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append((m.start(), d, m.group(0)))
            taken.append((m.start(), m.end()))
    for m in SLASH_DATE.finditer(text):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        d = _safe_date(y, int(m.group(1)), int(m.group(2)))
        if d:
            out.append((m.start(), d, m.group(0)))
            taken.append((m.start(), m.end()))
    for m in MONTH_YEAR.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        out.append((m.start(), month_end(int(m.group(2)), MONTHS[m.group(1).lower()]), m.group(0)))
    out.sort(key=lambda x: x[0])
    return out


def parse_iso(s: str | None) -> _dt.date | None:
    if not s:
        return None
    m = ISO_DATE.search(s)
    if not m:
        return None
    return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def fmt_date(d: _dt.date) -> str:
    return d.strftime("%m/%d/%Y")


# ---------------------------------------------------------------- sentences

_ABBR = re.compile(
    r"\b(?:a\.m|p\.m|Ln|Dr|St|Ave|Rd|Blvd|Hwy|Wis|Ill|Ia|Inc|Co|Corp|Mr|Mrs|Ms|Jr|Sr|No|U\.S|e\.g|i\.e|vs|etc|approx|Mt|Ft|Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Oct|Nov|Dec)\.",
    re.I,
)


def split_sentences(par: str) -> list[str]:
    """Split a paragraph into sentences without breaking 'a.m.', 'Ln.', 'Wis.', decimals."""
    protected = _ABBR.sub(lambda m: m.group(0).replace(".", "\u2024"), par)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(\[])", protected)
    return [p.replace("\u2024", ".").strip() for p in parts if p.strip()]


def mask_template(text: str) -> str:
    """Template key for near-duplicate statements: numbers, ordinals and stop words removed,
    so 'It was the fourth time Forbes recognized Dupaco' and 'This was the fifth time ...'
    share one key and only the newest is kept (rule R4)."""
    t = text.lower()
    t = re.sub(r"\$?\d[\d,]*(?:\.\d+)?%?", "#", t)
    t = re.sub(r"\b(" + "|".join(_ORDINALS) + r")\b", "#", t)
    t = re.sub(r"\b(" + "|".join(list(_UNITS) + list(_TENS)) + r")\b", "#", t)
    words = [w for w in re.sub(r"[^a-z#]+", " ", t).split() if w not in STOP and w not in EXTRA_STOP]
    return " ".join(words)
