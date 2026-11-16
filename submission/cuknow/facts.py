"""Parse one cleaned page into structured facts, claims and searchable passages.

Parsing depends only on the page content and URL, so identical copies across snapshots are
parsed once (ingest caches by content hash).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from . import quals as Q
from .pages import EDITORIAL, LISTING, OFFICIAL, TOOL, page_effective, pub_date, url_path
from .text import clean_markdown, find_dates, num_key, split_sentences, strip_footnote, tokens

# ---------------------------------------------------------------- data types

RATE_MEASURES = ("apy", "dividend", "apr", "rate")
MONEY_MEASURES = ("fee", "min_open", "min_earn", "payment", "amount")


@dataclass
class Fact:
    quals: Q.Quals
    measure: str            # apy | dividend | apr | rate | fee | points | payment | min_open | min_earn | amount
    value: str              # display string, e.g. "5.39%", "12.50%-19.50%", "$30.00 per item", "Free"
    key: tuple              # numeric identity, e.g. (5.39,) or (12.5, 19.5); () for text values
    unit: str               # '%' | '$' | ''
    label: str              # row label as shown on the page (product + qualifiers)
    product: str            # display product name (context label when the row lacks one)
    quote: str              # the row/line as text
    effective: _dt.date | None
    ends_on: _dt.date | None   # promotion end date stated next to it (R2)
    item: str = ""          # fee item tokens (fees only)
    order: int = 0          # position on the page (stable ordering)


@dataclass
class Hours:
    kind: str               # 'lobby' | 'drive-up'
    rows: tuple             # ((days, times), ...) as displayed
    key: tuple              # normalized for counting identical schedules
    hint: str               # text used to attribute the block to a branch


@dataclass
class Claim:
    noun: str               # 'branch' | 'atm' | 'employee' | 'member'
    value: int
    sentence: str


@dataclass
class Address:
    street: str
    city: str
    state: str
    zip: str
    kind: str               # 'branch' | 'atm' | ''
    raw: str


@dataclass
class Passage:
    text: str
    toks: list
    heading: str
    order: int
    block: int = 0          # contiguous paragraph/list/table id within the page
    kind: str = "p"         # 'p' sentence | 'li' list item | 'row' table row | 'h' heading
    own: int = 0            # own token count (inherited words don't make a passage "longer")


@dataclass
class ParsedDoc:
    title: str
    cls: int
    path: str
    pub: _dt.date | None
    page_eff: _dt.date | None
    facts: list = field(default_factory=list)
    hours: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    addresses: list = field(default_factory=list)
    links: list = field(default_factory=list)     # (anchor, href, context)
    passages: list = field(default_factory=list)
    ends_on: _dt.date | None = None                # page-level "offer ended/ends <date>"


# ---------------------------------------------------------------- helpers

_TABLE_LINE = re.compile(r"^\|.*\|")
_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^(#{1,6})\s*(.+)$")
_PCT_VAL = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%(?:\s*(?:-|–|—|to)\s*(\d{1,3}(?:\.\d+)?)\s*%)?")
_MONEY_VAL = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
_EFFECTIVE = re.compile(r"effective", re.I)
_ENDS = re.compile(
    r"\b(?:offer|promotion|promo|special|rate|deal)?[^.\n]{0,40}?\b(ended|expired|ends|expires|valid through|good through|through|until|thru)\b[:\s]+(?:on\s+)?",
    re.I,
)
_FEE_WORDS = re.compile(r"\bfees?\b|charge|\beach\b|\bper\b|replacement|drafts?|stop payment|overdraft|wire|notary|money order|check|statement|research|copy|coin|stamp|bag|card|atm|inquiry", re.I)


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def col_kind(h: str) -> str:
    h = h.lower().strip()
    if not h or re.fullmatch(r"col\d+", h):
        return "unknown"
    if re.search(r"\bafter\b|\bthereafter\b|\bpost-promo", h):
        return "later"          # the rate after a promotional period: not the headline value
    if "apy" in h or "annual percentage yield" in h or "yield" in h:
        return "apy"
    if "dividend" in h:
        return "dividend"
    if "apr" in h or "annual percentage rate" in h:
        return "apr"
    if "point" in h:
        return "points"
    if "payment" in h:
        return "payment"
    if "min" in h and "open" in h:
        return "min_open"
    if "min" in h and ("earn" in h or "bal" in h):
        return "min_earn"
    if re.fullmatch(r"(interest )?rates?( \(as low as\))?", h) or h == "rate (as low as)":
        return "rate"
    if "fee" in h or "charge" in h or "cost" in h or "price" in h:
        return "fee"
    return "label"


def parse_value(cell: str) -> tuple[str, tuple, str] | None:
    """Return (display, key, unit) for a value cell; None when it holds no value."""
    c = strip_footnote(cell)
    if not c or c in ("—", "-", "–") or c.lower() in ("n/a", "na"):
        return None
    m = _PCT_VAL.search(c)
    if m:
        a = m.group(1)
        if m.group(2):
            return ("{}%-{}%".format(a, m.group(2)), (num_key(a), num_key(m.group(2))), "%")
        return (a + "%", (num_key(a),), "%")
    m = _MONEY_VAL.search(c)
    if m:
        disp = c[m.start():].strip()
        disp = re.sub(r"\s+", " ", disp)
        disp = re.sub(r"\*", "", disp)
        disp = disp[:40].strip()
        val = m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")
        return (disp, (num_key(val),), "$")
    low = c.lower()
    if low.startswith(("free", "no charge", "no fee", "available at no charge")):
        return (c[:40], (0.0,), "$")
    return None


def _ends_on(text: str) -> _dt.date | None:
    for m in _ENDS.finditer(text):
        ds = find_dates(text[m.end(): m.end() + 40])
        if ds and ds[0][0] < 6:
            return ds[0][1]
    return None


def _is_label_line(line: str) -> bool:
    if not line or len(line) > 110:
        return False
    if _TABLE_LINE.match(line) or _EFFECTIVE.search(line):
        return False
    if re.match(r"^\d\s*[A-Z]", line):        # footnote like "1Savings Rates: ..."
        return False
    if line.startswith(("-", "*", "!")):
        return False
    return True


# ---------------------------------------------------------------- main parse

def parse_page(url: str, title: str, markdown: str, cls: int) -> ParsedDoc:
    text, links = clean_markdown(markdown)
    path = url_path(url)
    doc = ParsedDoc(title=title or "", cls=cls, path=path, pub=pub_date(url, text, cls),
                    page_eff=page_effective(text))
    lines = text.split("\n")
    n = len(lines)
    heading = ""
    title_text = (title or "").split("|")[0].strip()
    head_hint = (title_text + " " + text[:800]).replace("\n", " ")
    fee_page = bool(re.search(r"fee", title + " " + path, re.I))
    page_ends = None
    for m in re.finditer(r"(this|the) (offer|promotion|special)[^.\n]{0,30}(has )?(ended|expired)", text, re.I):
        page_ends = _dt.date(1900, 1, 1)
        break
    doc.ends_on = page_ends

    order = 0
    i = 0
    last_label_lines: list[str] = []
    link_iter = 0
    while i < n:
        line = lines[i]
        hm = _HEADING.match(line)
        if hm:
            heading = hm.group(2).strip()
            last_label_lines = [heading]
            i += 1
            continue
        if _TABLE_LINE.match(line):
            j = i
            while j < n and (_TABLE_LINE.match(lines[j]) or _SEP.match(lines[j])):
                j += 1
            try:
                order = _parse_table(doc, lines, i, j, heading, last_label_lines, title_text, order, fee_page)
            except Exception:
                pass
            i = j
            continue
        stripped = line.strip()
        if stripped:
            if _is_label_line(stripped):
                last_label_lines = [stripped] + last_label_lines[:2]
            if cls != LISTING:
                order = _line_facts(doc, stripped, heading, last_label_lines, title_text, order, fee_page)
        i += 1

    _parse_hours(doc, lines, head_hint)
    if cls != LISTING:
        _parse_claims_addresses_passages(doc, lines)
    # links with a little context
    for anchor, href in links:
        if href.startswith(("http", "/")) and not href.startswith("tel:"):
            doc.links.append((anchor, href, heading))
    return doc


def _context_chain(heading: str, label_lines: list[str], title_text: str, header: list[str]) -> list[str]:
    chain = []
    for l in label_lines[:2]:
        if l and l not in chain:
            chain.append(l)
    if heading and heading not in chain:
        chain.append(heading)
    hdr = " ".join(h for h in header if col_kind(h) == "label")
    if hdr:
        chain.append(hdr)
    if title_text:
        chain.append(title_text)
    return chain


def _parse_table(doc, lines, i, j, heading, label_lines, title_text, order, fee_page) -> int:
    rows = [l for l in lines[i:j] if not _SEP.match(l)]
    if not rows:
        return order
    has_sep = j - i >= 2 and _SEP.match(lines[i + 1] or "")
    header = _cells(rows[0]) if has_sep else []
    body = rows[1:] if has_sep else rows
    # A "header" that carries values is really data (PDF page-break tables).
    if header and any(parse_value(c) for c in header[1:]):
        body = [rows[0]] + body
        header = []
    kinds = [col_kind(h) for h in header]
    # effective date: first "effective" line after the table (before the next table)
    eff = None
    ends = None
    k = j
    seen = 0
    while k < len(lines) and seen < 8:
        l = lines[k].strip()
        if _TABLE_LINE.match(l):
            break
        if l:
            seen += 1
            if eff is None and _EFFECTIVE.search(l):
                ds = find_dates(l)
                if ds:
                    eff = ds[0][1]
            if ends is None:
                ends = _ends_on(l)
        k += 1
    if eff is None:
        eff = doc.page_eff
    chain = _context_chain(heading, label_lines, title_text, header)
    ctx_label = label_lines[0] if label_lines else heading
    for r in body:
        cells = _cells(r)
        if not any(cells):
            continue
        ks = kinds + ["unknown"] * (len(cells) - len(kinds)) if len(kinds) < len(cells) else kinds[: len(cells)]
        label_parts = []
        values = []
        for c, kd in zip(cells, ks):
            if not c:
                continue
            if kd in ("label",):
                label_parts.append(strip_footnote(c))
            elif kd == "unknown":
                pv = parse_value(c)
                if pv is None:
                    label_parts.append(strip_footnote(c))
                else:
                    values.append((kd, c, pv))
            else:
                pv = parse_value(c)
                if pv:
                    values.append((kd, c, pv))
        if not values:
            continue
        label = ", ".join(p for p in label_parts if p)
        if not label:
            continue
        quote = " | ".join(strip_footnote(c) for c in cells if c)
        is_fee_table = fee_page or any(kd == "fee" for kd, _, _ in values) or bool(re.search(r"fee|charge", " ".join(chain[:3]), re.I))
        for kd, raw, (disp, key, unit) in values:
            if kd in ("payment", "points", "later"):
                continue
            measure = kd
            if kd == "unknown":
                if unit == "%":
                    low = (label + " " + raw).lower()
                    measure = "apy" if "apy" in low else "apr" if "apr" in low else "dividend" if "dividend" in low else "rate"
                else:
                    measure = "fee" if is_fee_table else "amount"
            if measure == "fee" or (unit == "$" and is_fee_table and measure not in ("min_open", "min_earn", "payment")):
                if not (doc.cls in (OFFICIAL, TOOL) or doc.cls == EDITORIAL and fee_page):
                    continue
                item = " ".join(sorted(set(tokens(label))))
                qq = Q.Quals("fee", True, "business" if doc.path.startswith("/business") else "personal")
                doc.facts.append(Fact(qq, "fee", disp, key, unit, label, label, quote, eff, ends, item, order))
                order += 1
                continue
            qq = Q.parse_row(label, chain, doc.path)
            if not qq.family:
                continue
            product = _product_name(label, ctx_label, qq)
            doc.facts.append(Fact(qq, measure, disp, key, unit, label, product, quote, eff, ends, "", order))
            order += 1
    return order


def _product_name(label: str, ctx_label: str, qq: Q.Quals) -> str:
    fam_in_label, strong = Q.detect_family([label])
    if fam_in_label and strong:
        return label
    if ctx_label and ctx_label.lower() not in label.lower():
        return "{} {}".format(ctx_label, label)
    return label


_LV = re.compile(r"^(?P<label>[^|]{3,140}?)\s*(?::|—|–| - |\bis\b|\bat\b)?\s*(?P<val>\$\s?\d[\d,]*(?:\.\d{1,2})?|\d{1,3}(?:\.\d+)?\s*%)(?P<rest>.*)$")


def _line_facts(doc, line, heading, label_lines, title_text, order, fee_page) -> int:
    """Facts from short 'label: value' lines (fee schedule lines, simple rate lines)."""
    if len(line) > 170 or doc.cls in (EDITORIAL, LISTING):
        return order
    m = _LV.match(line)
    if not m:
        return order
    label = m.group("label").strip(" :-–—")
    rest = m.group("rest") or ""
    val = m.group("val")
    if len(label) < 3 or len(label) > 90 or _EFFECTIVE.search(label):
        return order
    # A label is a short name, not prose: no sentence break, no footnote-style "based on".
    if re.search(r"[a-z]\.\s+[A-Z]|\bbased on\b|\badd\b|\bif you\b", label):
        return order
    pv = parse_value(val + rest[:12])
    if not pv:
        return order
    disp, key, unit = pv
    ends = _ends_on(line)
    eff = doc.page_eff
    if unit == "$":
        # Dollar lines are facts only on fee pages or when the label says "fee".
        if not (fee_page or re.search(r"\bfees?\b", label, re.I)):
            return order
        tail = re.sub(r"\*", "", rest).strip()
        tail = re.split(r"[.;(]|\s\*", tail)[0].strip()
        if tail and len(tail) <= 30 and re.match(r"^(each|per\b|for\b|a\b|plus)", tail, re.I):
            disp = "{} {}".format(disp.split(" ")[0], tail)
        else:
            disp = disp.split(" ")[0]
        item = " ".join(sorted(set(tokens(label))))
        qq = Q.Quals("fee", True, "business" if doc.path.startswith("/business") else "personal")
        doc.facts.append(Fact(qq, "fee", disp, key, unit, label, label, line, eff, ends, item, order))
        return order + 1
    # percentage line: only when the label itself names a product and says which rate it is
    fam, strong = Q.detect_family([label])
    if not fam:
        return order
    if not re.search(r"\b(apy|apr|rate|dividend|yield)\b", (label + " " + rest[:15]).lower()):
        return order
    low = (label + " " + rest[:10]).lower()
    measure = "apy" if "apy" in low or "yield" in low else "apr" if "apr" in low else "dividend" if "dividend" in low else "rate"
    chain = [heading, title_text]
    qq = Q.parse_row(label, chain, doc.path)
    if not qq.family:
        return order
    doc.facts.append(Fact(qq, measure, disp, key, unit, label, label, line, eff, ends, "", order))
    return order + 1


# ---------------------------------------------------------------- hours

_DAY = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?"
_DAYS_LINE = re.compile(r"^(" + _DAY + r"(?:\s*(?:-|–|—|through|to|&|and)\s*" + _DAY + r")?)\s*:?\s*(.+)$", re.I)
_TIMEISH = re.compile(r"\d{1,2}(?::\d\d)?\s*[ap]\.?\s?m\.?|noon|closed|atm only|24 hours", re.I)
_HOURS_HEAD = re.compile(r"^(lobby|drive[- ]?up|drive[- ]?thru|drive[- ]?through)(?:\s+hours)?\s*:?\s*(.*)$", re.I)


def _norm_sched(s: str) -> str:
    s = s.lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return s.strip(" .")


def _parse_hours(doc, lines, hint):
    n = len(lines)
    i = 0
    while i < n:
        m = _HOURS_HEAD.match(lines[i].strip())
        if not m:
            i += 1
            continue
        kind = "lobby" if m.group(1).lower().startswith("lobby") else "drive-up"
        rows = []
        rest = m.group(2).strip()
        j = i + 1
        if rest:
            dm = _DAYS_LINE.match(rest)
            if dm and _TIMEISH.search(dm.group(2)):
                rows.append((dm.group(1).strip(), dm.group(2).strip()))
        blank = 0
        while j < n:
            l = lines[j].strip()
            if not l:
                blank += 1
                if blank > 1:
                    break
                j += 1
                continue
            blank = 0
            dm = _DAYS_LINE.match(l)
            if dm and _TIMEISH.search(dm.group(2)) and len(l) < 80:
                rows.append((dm.group(1).strip(), dm.group(2).strip()))
                j += 1
                continue
            break
        if rows:
            key = tuple((_norm_sched(a), _norm_sched(b)) for a, b in rows)
            doc.hours.append(Hours(kind, tuple(rows), key, hint))
        i = max(j, i + 1)


# ---------------------------------------------------------------- claims, addresses, passages

_COUNT = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*)\s+(branch office locations|branch offices|branch locations|branches|full-service branches)\b",
    re.I,
)
_COUNT2 = re.compile(r"\b(?:more than|over|nearly|about|approximately|almost)\s+(\d{1,3}(?:,\d{3})*)\s+(employees|members)\b", re.I)
_REFERS_BACK = re.compile(
    r"^(this|it|these|the (?:location|branch|office|program|initiative|service|account|loan|card|benefit|offer|change))\b",
    re.I,
)
_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|"
    "OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|"
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
    "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
    "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
    "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|"
    "Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming"
)
_ADDR = re.compile(
    r"(\d{2,6}\s+[A-Z0-9][^,\n|]{2,60}?),\s*([A-Z][A-Za-z .']{1,30}?),\s*(" + _STATES + r")\b,?\s*(\d{5})"
)


def _parse_claims_addresses_passages(doc, lines):
    heading = ""
    order = 0
    block = 0
    prev_kind = ""
    prev_toks: list = []
    prev_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue        # a blank line between two list items does not end the list
        hm = _HEADING.match(s)
        if hm:
            heading = hm.group(2).strip()
        for am in _ADDR.finditer(s):
            ctx = " ".join(prev_lines[-3:])
            kind = "atm" if re.search(r"\bATM\b", ctx) and not re.search(r"Branch", ctx) else ("branch" if re.search(r"branch", ctx, re.I) else "")
            doc.addresses.append(Address(am.group(1).strip(), am.group(2).strip(), am.group(3), am.group(4), kind, am.group(0)))
        prev_lines.append(s)
        if _TABLE_LINE.match(s):
            if prev_kind != "row":
                block += 1
            prev_kind = "row"
            cells = [strip_footnote(c) for c in _cells(s) if c and not _SEP.match(c)]
            if _SEP.match(s) or not cells:
                continue
            txt = " | ".join(cells)
            toks = tokens(txt + " " + heading)
            if toks:
                doc.passages.append(Passage(txt, toks, heading, order, block, "row"))
                order += 1
            continue
        is_li = bool(re.match(r"^[-*\u2022]\s+", s))
        kind = "h" if hm else ("li" if is_li else "p")
        if kind != prev_kind or kind == "p" or kind == "h":
            block += 1
        prev_kind = kind
        # short lines are noise, except "Label: value" fields (Phone: 800-..., Email: ...)
        if len(s) < 25 and kind == "p" and not re.match(r"^[A-Z][\w &/'-]{1,30}:\s*\S", s):
            continue
        body = hm.group(2) if hm else re.sub(r"^[-*\u2022]\s+", "", s)
        sents = [body] if kind in ("li", "h") else split_sentences(body)
        if kind == "h":
            prev_toks = []
        for sent in sents:
            if len(sent) < 12 and not re.match(r"^[A-Z][\w &/'-]{1,30}:\s*\S", sent):
                continue
            for cm in _COUNT.finditer(sent):
                n_br = int(cm.group(1).replace(",", ""))
                # a credit union's own branch count; not national or shared-network figures
                if n_br <= 500 and not re.search(r"united states|nationwide|shared|co-op|across the country|industry", sent, re.I):
                    doc.claims.append(Claim("branch", n_br, sent))
            for cm in _COUNT2.finditer(sent):
                doc.claims.append(Claim(cm.group(2).lower().rstrip("s"), int(cm.group(1).replace(",", "")), sent))
            # list items are findable by their heading's words ("Eligible to join" -> items)
            toks = tokens(sent + (" " + heading if kind == "li" else ""))
            own = len(toks)
            # "This location will close March 31, 2026." inherits the previous sentence's words
            if prev_toks and _REFERS_BACK.match(sent):
                toks = toks + [t for t in prev_toks if t not in toks]
            prev_toks = tokens(sent)
            if len(toks) >= 2:
                doc.passages.append(Passage(sent, toks, heading, order, block, kind, own))
                order += 1
