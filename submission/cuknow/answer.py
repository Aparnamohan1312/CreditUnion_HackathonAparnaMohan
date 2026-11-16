"""Answer routing and templates.

Answer text carries only the answer's own values and qualifier labels (no effective dates,
snapshot IDs or footnote digits). Losing values go to conflict_note, which is not scored.
"""
from __future__ import annotations

import datetime as _dt
import re
from collections import Counter, defaultdict

from contract import Answer, Source  # harness module, put on sys.path by solution.py

from . import quals as Q
from .kb import KB, MIN_DATE, is_expired, rank_key
from .pages import CLASS_NAMES, EDITORIAL, OFFICIAL, TOOL, canon_url, url_path
from .query import Intent, parse_question
from .text import MONTHS, find_dates, month_end, parse_iso, tokens

REFUSAL = "That isn't listed on our website, so I can't confirm it. Please contact us for details."

HARD_PRODUCT_FLAGS = ("jumbo", "arm", "intro", "roth", "traditional", "refinance")
DESCRIPTIVE_FLAGS = ("special", "purchase", "fixed", "variable", "platinum", "rewards")
BASE_EXCLUDED = ("jumbo", "intro", "refinance", "arm")
RATE_MEASURES = ("apy", "dividend", "apr", "rate")
MAX_ROWS = 14
COVERAGE_MIN = 0.45      # share of the question's rare-term weight a generic answer must cover
RECENCY_BAND = 0.85      # passages within this share of the best score compete on date (R4)
COVERAGE_STRICT = 0.8    # after a structured miss (fee/minimum not in a table), prose must match closely

_CTA = re.compile(r"\b(for more information|learn more|click here|visit|apply today|call us|contact us|reach out|sign up|download)\b", re.I)
_ANAPHORA = re.compile(r"^(this|it|these|they|that|the (?:program|initiative|service|account|loan|card|benefit))\b", re.I)
_DEFINE_Q = re.compile(r"^\s*(?:what|who)\s+(?:is|are|was|were)\b|^\s*tell me about\b|^\s*explain\b", re.I)
# A question about one attribute of a thing is not a request for its definition.
_ATTRIBUTE_Q = re.compile(r"\b(date|when|time|rate|apy|apr|fee|cost|address|number|amount|hours?|phone|link|url|price|minimum|maximum|limit|deadline)\b", re.I)
# Small customer-service synonym map for search (question word -> extra words to look for).
SYNONYMS = {"phone": ("call",), "telephone": ("call", "phone"), "call": ("phone",), "email": ("mail",),
            "cd": ("certificate",), "certificate": ("cd",), "atm": ("atms",), "locat": ("address",),
            # account self-service wording
            "update": ("change", "chang", "edit"), "updat": ("change", "chang", "edit"),
            "change": ("update", "updat"), "chang": ("update", "updat"), "edit": ("change", "update"),
            "contact": ("personal", "address", "phone", "email")}
# A passage that asks the member for credentials is never part of an answer.
_CREDENTIALS = re.compile(r"\b(member number|account number|social security(?: number)?|ssn|password|pin(?: number)?)\b", re.I)
_CALL_US = re.compile(r"\b(call|contact|reach) us\b|\bgive us a call\b|\bcontact (?:\w+ ){0,4}(?:credit union|using)\b", re.I)
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-.]\d{4}\b")
# "How can I ...?" is answered by a passage that says how: a means or an instruction.
_HOW_Q = re.compile(r"^\s*(?:i \w+[^?]{0,40}[.,]\s*)?how (?:can|do|should|would) i\b", re.I)
_MEANS = re.compile(r"\b(you can|you may|by (?:logging|calling|visiting|signing|going|using|stopping)|log ?in(?:to)?|sign in|"
                    r"call us|calling|visit (?:us|a branch|any branch)|stop by|through (?:shine|online|mobile)|in shine)\b", re.I)
# Account maintenance: "how do I update/change my address / phone / email / name / contact information"
_ACCOUNT_CHANGE_Q = re.compile(
    r"\b(update|updating|change|changing|edit|correct|fix)\b[^?]{0,40}\b(address|phone|e-?mail|name|contact (?:info\w*|details)|personal (?:info\w*|details))\b",
    re.I,
)
_STRONG_MEANS = re.compile(r"\bby (?:logging|calling|visiting|signing|going|using)\b|\blog ?in(?:to)?\b|\bsign in\b|"
                           r"\bin (?:shine|online banking|mobile banking)\b|\bthrough (?:shine|online|mobile)\b", re.I)
_SELF_SERVICE = re.compile(r"\b(change|update|edit)\b[^.]{0,60}\b(personal information|address|phone|e-?mail|contact)\b", re.I)
# "the previous year", however it is said - shared by the year-series rule and count questions
_PREV_YEAR = r"\b(?:last|past|previous|prior|this past|most recent) year\b|\b(?:a|one|1) year ago\b"
_RELATIVE_PERIOD = re.compile(_PREV_YEAR + r"|\brecent(?:ly)?\b|\blately\b|\bthis past\b|\blast (?:12|twelve) months\b", re.I)
_LEAD_IN = re.compile(r":\s*$|\b(the following|one of these|as follows|below)\b", re.I)
_CONTACT_Q = re.compile(r"\b(phone|call|telephone|email|e-mail|contact|reach)\b", re.I)
# Membership eligibility ("who can join", "am I eligible to become a member"): answered with the
# stated criteria, not with passages that merely repeat the question's words.
_ELIG_Q = re.compile(
    r"\b(eligib\w*|who can (?:join|become|bank)|can (?:i|anyone|my \w+|someone) (?:join|become)|qualif\w*|"
    r"requirements?|criteria|who may join|open to)\b",
    re.I,
)
_MEMBERSHIP = re.compile(r"\b(join\w*|members?|membership)\b", re.I)
_CRITERION = re.compile(
    r"\b(live|lives|work|works|worship|attend|study|reside)\b[^.]{0,60}\b(area|count(?:y|ies)|community|state|region)\b"
    r"|\b(immediate )?family member\b|\bemployer\b|\bfield of membership\b",
    re.I,
)
_AREA_REF = re.compile(r"\b(charter|service|our|membership) area\b|\bfield of membership\b|\bcounties we serve\b", re.I)
_US_STATE_NAMES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|"
                   "Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|"
                   "Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|"
                   "New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|"
                   "South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|"
                   "Wisconsin|Wyoming")
_STATE_RX = re.compile(r"\b(" + _US_STATE_NAMES + r")\b")
# Questions whose answer is a value: passages without any digits are unlikely answers.
_VALUE_Q = re.compile(r"\b(number|how many|how much|phone|zip|date|amount|cost|fee|routing|percent|minimum)\b", re.I)
_EVENT_Q = re.compile(
    r"\bwhen (?:does|did|will|is|was)\b[^?]{0,60}\b(?:clos|open)|\bstill open\b|\bopen today\b|\bclosing date\b"
    r"|^\s*(?:is|was|has|did)\b[^?]{0,50}\b(?:closed|closing|close)\b",
    re.I,
)


_PEOPLE = {"people", "person", "folk", "individual"}
_HUMAN = {"employee", "member", "staff", "people", "person", "volunteer", "customer", "worker"}


def _quantifies(text: str, noun: str, keys=None) -> bool:
    """A number directly in front of the counted noun (up to two words between). When the question
    says what was counted ('took VTO'), its rarest word must be in the same clause as the number, so
    '627 employees volunteered 6,136 hours, with many using VTO' does not count VTO users.
    A generic 'people' matches any human noun (employees, members, staff)."""
    nouns = _HUMAN if noun in _PEOPLE else {noun}
    for clause in re.split(r"[,;]|(?<=[a-z)])\.\s", text):
        pt = tokens(clause, keep_stop=True)
        for i, t in enumerate(pt):
            if t in nouns:
                for j in range(max(0, i - 3), i):
                    if re.fullmatch(r"\d[\d,.]*", pt[j]) and not re.fullmatch(r"(19|20)\d\d", pt[j]):
                        if not keys or any(k in pt for k in keys):
                            return True   # a count (not a year) of the thing asked about
    return False


def refusal() -> Answer:
    return Answer(text=REFUSAL, sources=[], confident=False)


def _src(v, quote: str) -> Source:
    q = re.sub(r"\s+", " ", quote or "").strip()
    return Source(url=v.url, snapshot_id=v.snapshot_id, quote=q[:200])


def _clean_label(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,;:")


def _nums(text: str) -> set:
    return set(re.findall(r"\d[\d,]*(?:\.\d+)?", text))


class Answerer:
    def __init__(self, kb: KB) -> None:
        self.kb = kb
        self._title_toks: dict[int, frozenset] = {}

    # ------------------------------------------------------------ entry

    def answer(self, question: str, as_of: str | None) -> Answer:
        it = parse_question(question)
        d = parse_iso(as_of) if as_of else None
        if d is None:
            d = it.ref_date
        vi = self.kb.view_for(d)
        if vi < 0:
            return refusal()
        it.ref_date = d
        for handler in (self._url, self._count, self._hours, self._address, self._rate, self._fee, self._eligibility,
                        self._account_change):
            ans = handler(it, vi)
            if ans is not None:
                return ans
        # a fee/minimum question that no table answers: prose must match it closely, or refuse
        strict = it.measure in ("fee", "min_open")
        return self._generic(it, vi, strict)

    def _topic_home(self, vi: int, path: str):
        e = self.kb.vidx[vi].by_key.get(canon_url(self.kb.site_base + path))
        if e and e[1]:
            return e[0]
        return None

    def _ttoks(self, v) -> frozenset:
        t = self._title_toks.get(v.vid)
        if t is None:
            t = frozenset(tokens((v.title or "").split("|")[0]))
            self._title_toks[v.vid] = t
        return t

    # ------------------------------------------------------------ rates (R3, R5)

    def _rate(self, it: Intent, vi: int) -> Answer | None:
        q = it.quals
        if not q.family or q.family == "fee":
            return None
        want = it.measure if it.measure in RATE_MEASURES + ("min_open",) else None
        if want is None and not it.q_values:
            return None
        if it.kinds & {"hours", "address", "url"}:
            return None
        view = self.kb.views[vi]
        vx = self.kb.vidx[vi]
        fams = Q.FAMILY_GROUPS.get(q.family, {q.family})
        measures = ("min_open",) if want == "min_open" else RATE_MEASURES
        pool = []
        for fam in sorted(fams):
            pool.extend(c for c in vx.facts_by_family.get(fam, []) if c[0].measure in measures)
        pool = [c for c in pool if not is_expired(c[0], c[1], view)]
        if not pool:
            if want == "min_open":
                return None          # minimums often live in footnotes: let the search try
            return refusal() if q.family_strong else None
        whole_listing = (q.term is None and q.tier_kind is None and not it.q_values
                         and not (it.kinds & {"max", "min", "yesno", "change"}))
        # a whole-product question with no specific product named is a catalog (same answer for
        # "credit card rates" and "the rates for the credit cards")
        named = any(fl in q.flags for fl in ("platinum", "rewards")) or any(fl.startswith("n:") for fl in q.flags)
        catalog = not it.segment_asked and ("list" in it.kinds or (whole_listing and not named))
        fam_flags = set()
        has_term = has_my = False
        for f, v, live in pool:
            fam_flags |= f.quals.flags
            has_term |= f.quals.term is not None
            has_my |= f.quals.tier_kind == "model_year"
        # A product-defining flag that no row carries: the product isn't listed (7/1 ARM, ...).
        for fl in HARD_PRODUCT_FLAGS:
            if fl in q.flags and fl not in fam_flags:
                return refusal()
        excluded_variants = set()

        def ok(f) -> bool:
            fq = f.quals
            # named variants ("Achieve" Money Market) only when the question names them
            for fl in fq.flags:
                if fl.startswith("n:") and fl not in q.flags:
                    excluded_variants.add(fl)
                    return False
            for fl in q.flags:
                if fl.startswith("n:") and fl not in fq.flags:
                    return False
            if it.segment_asked:
                if fq.segment != "business":
                    return False
            elif fq.segment != "personal" and not catalog:
                return False
            for fl in HARD_PRODUCT_FLAGS:
                if fl in q.flags and fl not in fq.flags:
                    return False
            for fl in DESCRIPTIVE_FLAGS:
                if fl in q.flags and fl in fam_flags and fl not in fq.flags:
                    return False
            for a, b in Q.OPPOSITES:
                if a in q.flags and b in fq.flags and a not in fq.flags:
                    return False
            if q.term is not None:
                if fq.term is not None and fq.term != q.term:
                    return False
                if fq.term is None and has_term:
                    return False
            if q.tier_kind == "model_year":
                if fq.tier_kind == "model_year" and not (fq.lo <= q.lo <= fq.hi):
                    return False
                if fq.tier_kind != "model_year" and has_my:
                    return False
            if q.tier_kind == "money" and fq.tier_kind == "money":
                if Q.overlap(fq.lo, fq.hi, q.lo, q.hi) <= 0:
                    return False
            for fl in BASE_EXCLUDED:
                if fl in fq.flags and fl not in q.flags:
                    # a whole-product listing (no term, no tier, not a superlative or yes/no check)
                    # includes the jumbo tier; everything else defaults to the base product
                    if fl == "jumbo" and whole_listing:
                        continue
                    excluded_variants.add(fl)      # a base-variant row that otherwise matches
                    return False
            return True

        cands = [c for c in pool if ok(c[0])]
        if not cands:
            return refusal()
        groups = defaultdict(list)
        for c in cands:
            groups[(c[0].quals.family, c[0].quals.segment)].append(c)
        rows_out = []
        sources = []
        notes = []
        personal_sigs = set()
        for gk in sorted(groups, key=lambda k: (k[1] != "personal", k[0])):
            gc = groups[gk]
            best = max(gc, key=lambda c: rank_key(c[0], c[1], c[2]))
            page = best[1]
            page_rows = [c for c in gc if c[1] is page]
            if q.tier_kind == "money":
                money_rows = [c for c in page_rows if c[0].quals.tier_kind == "money"]
                if money_rows:
                    def fit(c):
                        fq = c[0].quals
                        return Q.overlap(fq.lo, fq.hi, q.lo, q.hi)
                    bf = max(fit(c) for c in money_rows)
                    page_rows = [c for c in page_rows if c[0].quals.tier_kind != "money" or fit(c) >= bf - 1e-9]
            slots = self._slots(page_rows)
            sig = tuple(sorted(tuple(sorted((m, f.key) for m, f in ms.items())) for _, ms in slots))
            if gk[1] == "business" and catalog and sig in personal_sigs:
                continue      # business mirror of the personal table adds nothing
            if gk[1] == "personal":
                personal_sigs.add(sig)
            if it.kinds & {"max", "min"} and slots and not it.q_values:
                slots = [self._extreme(slots, want, "max" in it.kinds)]
            rows_out.append((gk, page, slots))
            if len(sources) < 3 and slots:
                ms = slots[0][1]
                sources.append(_src(page, ms[sorted(ms)[0]].quote))
            notes.extend(self._conflict_notes(pool, page, slots))
        if not rows_out:
            return refusal()
        if it.q_values and "yesno" in it.kinds:
            return self._verify(it, rows_out, sources, want)
        if "change" in it.kinds and rows_out and rows_out[0][2]:
            ch = self._change(it, vi, rows_out[0][2][0], want)
            if ch is not None:
                return Answer(text=ch, sources=sources[:3])
        parts = []
        for gk, page, slots in rows_out:
            for label, ms in slots:
                if len(parts) >= MAX_ROWS:
                    break
                val = self._fmt_measures(ms, want)
                if val:
                    parts.append("{}: {}".format(label, val))
        if not parts:
            return refusal()
        text = "; ".join(parts) + "."
        if "jumbo" in excluded_variants and "list" not in it.kinds and len(parts) <= 2:
            text += " A jumbo version with a higher minimum deposit is also offered."
        sources = self._add_support(vi, rows_out, sources)
        note = "; ".join(sorted(set(notes)))[:600] or None
        return Answer(text=text, sources=sources[:3], confident=True, conflict_note=note)

    def _change(self, it, vi, slot, want):
        """'What was the previous rate?': walk back through earlier snapshots to the last different value."""
        label, ms = slot
        m = want if want in ms else next((x for x in ("apy", "apr", "rate", "dividend") if x in ms), None)
        if m is None:
            return None
        cur = ms[m]
        cq = cur.quals
        for k in range(vi - 1, -1, -1):
            best = None
            for f, v, live in self.kb.vidx[k].facts_by_family.get(cq.family, []):
                fq = f.quals
                if f.measure != m or fq.segment != cq.segment or fq.flags != cq.flags or fq.term != cq.term:
                    continue
                ov = 1.0
                if cq.tier_kind:
                    if fq.tier_kind != cq.tier_kind:
                        continue
                    ov = Q.overlap(fq.lo, fq.hi, cq.lo, cq.hi)
                    if ov < 0.3:
                        continue
                if is_expired(f, v, self.kb.views[k]):
                    continue
                # closest tier first (nested "or less" tiers all overlap), then authority
                k2 = (ov, rank_key(f, v, live))
                if best is None or k2 > best[3]:
                    best = (f, v, live, k2)
            if best is not None and best[0].key != cur.key:
                unit = {"apy": " APY", "apr": " APR", "dividend": " dividend rate"}.get(m, "")
                return "{}: now {}{}; previously {}{}.".format(label, cur.value, unit, best[0].value, unit)
        unit = {"apy": " APY", "apr": " APR", "dividend": " dividend rate"}.get(m, "")
        return "{}: {}{}, unchanged across the snapshots we have.".format(label, cur.value, unit)

    @staticmethod
    def _extreme(slots, want, is_max):
        def hv(s):
            ms = s[1]
            for m in (want, "apy", "apr", "rate", "dividend"):
                if m and m in ms and ms[m].key:
                    return ms[m].key[-1] if is_max else ms[m].key[0]
            return float("-inf") if is_max else float("inf")
        return max(slots, key=hv) if is_max else min(slots, key=hv)

    def _slots(self, rows):
        """Group a page's rows into (label, {measure: fact}) in page order."""
        order = []
        by = {}
        for f, v, live in sorted(rows, key=lambda c: c[0].order):
            qq = f.quals
            k = (qq.family, qq.segment, tuple(sorted(qq.flags)), qq.term, qq.tier_kind, qq.lo, qq.hi, f.label)
            if k not in by:
                by[k] = (_clean_label(f.product), {})
                order.append(k)
            ms = by[k][1]
            if f.measure not in ms:
                ms[f.measure] = f
        out = [by[k] for k in order]
        # loan-amount tiers: smallest amount first (the headline "as low as" tier), per term
        if out and rows and rows[0][0].quals.family in Q.LOAN_FAMILIES:
            first_pos = {}
            for k in order:
                first_pos.setdefault(k[3], len(first_pos))
            idx = sorted(range(len(order)), key=lambda i: (
                first_pos[order[i][3]], order[i][6] if (order[i][4] == "money" and order[i][6] is not None) else -1, i))
            out = [out[i] for i in idx]
        return out

    @staticmethod
    def _fmt_measures(ms: dict, want: str | None) -> str:
        """Both measures whenever both exist (APY + dividend rate, rate + APR), the asked one first:
        rewordings that ask for 'APY', 'rate' or 'dividend' then give the same set of numbers."""
        def g(m):
            f = ms.get(m)
            return f.value if f else None

        if want == "min_open" and g("min_open"):
            return "{} minimum to open".format(g("min_open"))
        apy, div, rate, apr = g("apy"), g("dividend"), g("rate"), g("apr")
        if apy and div:
            if want == "dividend":
                return "{} dividend rate ({} APY)".format(div, apy)
            return "{} APY ({} dividend rate)".format(apy, div)
        if apy:
            return "{} APY".format(apy)
        if rate and apr:
            if want == "apr":
                return "{} APR ({} rate)".format(apr, rate)
            return "{} rate ({} APR)".format(rate, apr)
        if apr:
            return "{} APR".format(apr)
        if div:
            return "{} dividend rate".format(div)
        if rate:
            return rate
        return ""

    def _verify(self, it, rows_out, sources, want):
        """Yes/no check of a value written in the question; never echoes that value."""
        target = it.q_values[0]
        hits = []
        allv = []
        for gk, page, slots in rows_out:
            for label, ms in slots:
                for m in ((want,) if want in ms else ("apy", "apr", "rate", "dividend")):
                    f = ms.get(m)
                    if f and f.key:
                        allv.append((f.key[-1], label, f, m))
                        if any(abs(k - target) < 1e-6 for k in f.key):
                            hits.append((label, f, m))
                        break
        if hits:
            label, f, m = hits[0]
            return Answer(text="Yes. {}: {}.".format(label, self._fmt_measures({m: f}, m)), sources=sources[:3])
        if not allv:
            return refusal()
        allv.sort(key=lambda x: (x[0], x[1]))
        lo, hi = allv[0], allv[-1]
        unit = {"apy": " APY", "apr": " APR", "dividend": " dividend rate"}.get(hi[3], "")
        text = "No. The rates range from {} to {}{}; the highest is {}: {}{}.".format(
            lo[2].value, hi[2].value, unit, hi[1], hi[2].value, unit)
        return Answer(text=text, sources=sources[:3])

    def _conflict_notes(self, pool, page, slots) -> list:
        notes = []
        if not slots:
            return notes
        label, ms = slots[0]
        for m, f in sorted(ms.items()):
            fq = f.quals
            for g, v, live in pool:
                gq = g.quals
                if v is page or g.measure != m or gq.family != fq.family or gq.segment != fq.segment or gq.term != fq.term:
                    continue
                if gq.flags != fq.flags:
                    continue
                if fq.tier_kind and (gq.tier_kind != fq.tier_kind or Q.overlap(gq.lo, gq.hi, fq.lo, fq.hi) < 0.6):
                    continue
                if g.key != f.key:
                    why = CLASS_NAMES.get(v.cls, "page") if live else "page no longer on the site"
                    eff = ", effective " + g.effective.isoformat() if g.effective else ""
                    notes.append("{} shows {} ({}{}); using {} from {}".format(
                        url_path(v.url), g.value, why, eff, f.value, url_path(page.url)))
        return notes

    def _add_support(self, vi, rows_out, sources):
        """Other live official pages showing the same first value (sources are path-matched)."""
        if not rows_out or not rows_out[0][2]:
            return sources
        gk, page, slots = rows_out[0]
        label, ms = slots[0]
        first = next((ms[m] for m in ("apy", "apr", "rate", "dividend", "min_open") if m in ms), None)
        have = {canon_url(s.url) for s in sources}
        if first is None:
            return sources
        support = []
        for f, v, live in self.kb.vidx[vi].facts_by_family.get(gk[0], []):
            if not live or v.cls != OFFICIAL or canon_url(v.url) in have:
                continue
            fq, sq = f.quals, first.quals
            if f.measure == first.measure and f.key == first.key and fq.term == sq.term and fq.flags == sq.flags \
                    and fq.lo == sq.lo and fq.hi == sq.hi and fq.segment == sq.segment:
                support.append((len(url_path(v.url)), v.url, v, f))
        for _, _, v, f in sorted(support, key=lambda x: (x[0], x[1])):
            if len(sources) >= 3:
                break
            if canon_url(v.url) not in have:
                sources.append(_src(v, f.quote))
                have.add(canon_url(v.url))
        return sources

    # ------------------------------------------------------------ fees

    _FEE_GENERIC = {"fee", "much", "cost", "charge", "price", "account", "pay", "member", "service"}

    def _fee(self, it: Intent, vi: int) -> Answer | None:
        if it.measure != "fee" or it.quals.family not in (None, "fee", "checking", "credit_card", "free_checking", "first_rate_checking"):
            return None
        qt = [t for t in it.toks if t not in self._FEE_GENERIC and not t.isdigit()]
        if not qt:
            return None
        view = self.kb.views[vi]
        srch = self.kb.search
        w = {t: srch.idf(t) for t in qt}
        mass = sum(w.values()) or 1.0
        best = None
        for f, v, live in self.kb.vidx[vi].fees:
            if is_expired(f, v, view):
                continue
            if it.segment_asked != (f.quals.segment == "business"):
                continue
            item = set(f.item.split())
            cov = sum(w[t] for t in qt if t in item) / mass   # rare words weigh more
            if cov < 0.6:
                continue
            k = (cov, rank_key(f, v, live), -len(item))
            if best is None or k > best[0]:
                best = (k, f, v)
        if best is None:
            return None              # not in a fee table: the search may find it in prose (or refuse)
        _, f, v = best
        return Answer(text="{}: {}.".format(_clean_label(f.label), f.value), sources=[_src(v, f.quote)])

    # ------------------------------------------------------------ counts (R4)

    _COUNT_WORDS = {"branch", "location", "office", "employee", "member", "many", "number", "total", "currently", "operate", "have", "open"}
    # words that only name a period ("last year", "in 2025", "as of May")
    _PERIOD_WORDS = {"last", "past", "previous", "previou", "prior", "year", "ago", "back", "then", "end", "had", "did",
                     "recent", "1",
                     "january", "february", "march", "april", "may", "june", "july", "august", "september",
                     "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
                     "sept", "oct", "nov", "dec"}

    def _count_cutoff(self, it: Intent, vi: int):
        """End of the period a count question names, or None for 'now'. Relative periods are
        measured from the snapshot's date (or the as_of date)."""
        ref = it.ref_date or self.kb.views[vi].taken_on
        raw = it.raw
        years = [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", raw)]
        if re.search(_PREV_YEAR, raw, re.I):
            return _dt.date(ref.year - 1, 12, 31)
        if it.ref_date is not None and find_dates(raw):
            return it.ref_date                            # "as of May 2026", "on June 1, 2026"
        m = re.search(r"\b(?:as of|in|during|by|end of)\s+(january|february|march|april|may|june|july|august|"
                      r"september|october|november|december)\b", raw, re.I)
        if m and not years:
            mo = MONTHS[m.group(1).lower()]
            y = ref.year if mo <= ref.month else ref.year - 1
            return month_end(y, mo)
        if years:
            return _dt.date(years[0], 12, 31)
        if it.ref_date is not None:
            return it.ref_date                            # as_of parameter
        return None

    def _about_self(self, sentence: str) -> bool:
        """Entity guard: the sentence is about the institution itself, not a national/industry figure."""
        brand = (self.kb.brand or "").lower()
        low = sentence.lower()
        if brand and brand in low:
            return True
        return bool(re.search(r"^\s*(it|we)\b|\b(our|we)\b", low))

    def _count(self, it: Intent, vi: int) -> Answer | None:
        if "count" not in it.kinds:
            return None
        t = it.norm
        noun = "branch" if re.search(r"branch|location|office", t) else "employee" if "employee" in t else \
            "member" if "member" in t else None
        if noun is None:
            return None
        # Only when the question is about the count itself ("how many employees used VTO" is not);
        # words that just name a period are allowed.
        s = self.kb.search
        extra = [w for w in it.toks if w not in self._COUNT_WORDS and w not in self._PERIOD_WORDS
                 and not re.fullmatch(r"(?:19|20)\d\d", w) and s.df(w) > 0]
        if extra:
            return None
        cutoff = self._count_cutoff(it, vi)
        claims = [c for c in self.kb.vidx[vi].claims if c[0].noun == noun and self._about_self(c[0].sentence)]
        if cutoff is not None:
            # R4 with the period's end as the cutoff: dated statements published by then
            claims = [c for c in claims if c[1].doc.pub is not None and c[1].doc.pub <= cutoff]
        if not claims:
            return refusal()           # no statement about the institution itself: never a national figure

        def key(c):
            cl, v, live = c
            return (live, v.doc.pub is not None, v.doc.pub or MIN_DATE, v.cls, v.fetched or MIN_DATE, v.url)

        claims.sort(key=key, reverse=True)
        cl, v, live = claims[0]
        n = cl.value
        who = self.kb.brand or "The credit union"
        verb = "had" if cutoff is not None else "has"
        if noun == "branch":
            text = "{} {} {} branch office locations".format(who, verb, n)
        elif noun == "employee":
            text = "{} {} more than {:,} employees".format(who, verb, n)
        else:
            text = "{} {} more than {:,} members".format(who, "served" if cutoff is not None else "serves", n)
        if cutoff is not None and v.doc.pub is not None:
            # month and year only: a day number ("December 23") could match a forbidden count
            text += " (per its {} statement)".format(v.doc.pub.strftime("%B %Y"))
        text += "."
        # the full publication date lives only in the source quote
        quote = cl.sentence
        if cutoff is not None and v.doc.pub is not None:
            quote = "{}: {}".format(v.doc.pub.strftime("%B %d, %Y").replace(" 0", " "), cl.sentence)
        sources = [_src(v, quote)]
        for c2, v2, l2 in claims[1:]:
            if len(sources) >= 3:
                break
            if c2.value == n and l2 and v2.url not in {x.url for x in sources}:
                sources.append(_src(v2, c2.sentence))
        others = sorted({c[0].value for c in claims if c[0].value != n})
        note = "Older pages state {} (newest dated statement wins)".format(", ".join(str(o) for o in others)) if others else None
        return Answer(text=text, sources=sources, conflict_note=note)

    # ------------------------------------------------------------ hours

    def _hours(self, it: Intent, vi: int) -> Answer | None:
        if "hours" not in it.kinds:
            return None
        blocks = self.kb.vidx[vi].hours
        kind = it.hours_kind or "lobby"
        live = [b for b in blocks if b[2]] or blocks
        cand = [b for b in live if b[0].kind == kind]
        if it.branch:
            name_toks = [w.lower() for w in re.findall(r"[A-Za-z]+", it.branch)]
            cand = [b for b in cand if all(w in b[0].hint.lower() for w in name_toks)]
            if not cand:
                return refusal()
        if not cand:
            return None
        cnt = Counter()
        pages = defaultdict(list)
        for h, v, l in cand:
            cnt[h.key] += 1
            pages[h.key].append((v.doc.pub or v.fetched or MIN_DATE, v.url, h, v))
        for k in pages:
            pages[k].sort(key=lambda x: (x[0], x[1]), reverse=True)
        ranked = sorted(cnt, key=lambda k: (-cnt[k], -pages[k][0][0].toordinal(), k))

        def fmt(h):
            return "; ".join("{}: {}".format(a, b) for a, b in h.rows)

        label = "Lobby hours" if kind == "lobby" else "Drive-up hours"
        top = pages[ranked[0]][0][2]
        sources = []
        for d, u, h, v in pages[ranked[0]][:2]:
            if u not in {s.url for s in sources}:
                sources.append(_src(v, fmt(h)))
        if "yesno" in it.kinds and re.search(r"\b(all|same|every)\b", it.raw, re.I):
            if len(ranked) == 1:
                text = "Yes. {}: {}.".format(label, fmt(top))
            else:
                others = [fmt(pages[k][0][2]) for k in ranked[1:]]
                text = "No. {} vary by branch. Most branches: {}. Other branches: {}. Which branch would you like?".format(
                    label, fmt(top), " | ".join(others))
        elif it.branch:
            text = "{} for the {} branch: {}.".format(label, it.branch, fmt(top))
        else:
            # Schedules that share the main weekday hours differ only on other days: say so,
            # and cite the newest page for each variant (each page then supports the answer).
            rows = list(top.rows)
            variants = [k for k in ranked[1:] if k[0] == ranked[0][0] and len(k) == len(ranked[0])]
            for i in range(1, len(rows)):
                alts = []
                for k in variants:
                    if k[i][0] == ranked[0][i][0] and k[i][1] != ranked[0][i][1]:
                        alt = pages[k][0][2].rows[i][1]
                        if alt not in alts:
                            alts.append(alt)
                if alts:
                    rows[i] = (rows[i][0], "{} (some branches {})".format(rows[i][1], " or ".join(alts)))
            body = "; ".join("{}: {}".format(a, b) for a, b in rows)
            text = "{} at most branches: {}. Hours vary by branch, so let me know which branch you mean.".format(label, body)
            if variants:
                d, u, h, v = pages[variants[0]][0]
                if u not in {x.url for x in sources}:
                    sources = sources[:1] + [_src(v, fmt(h))]
        contact = self._topic_home(vi, "/contact")
        if contact is not None:
            sources = sources[:2] + [Source(url=contact.url, snapshot_id=contact.snapshot_id, quote=(contact.title or "")[:200])]
        return Answer(text=text, sources=sources[:3])

    # ------------------------------------------------------------ addresses

    def _address(self, it: Intent, vi: int) -> Answer | None:
        if "address" not in it.kinds or not it.branch:
            return None
        toks = re.findall(r"[a-z]+", it.branch.lower())
        best = None
        for a, v, live in self.kb.vidx[vi].addresses:
            hay = (a.city + " " + a.street).lower()
            if all(t in hay for t in toks):
                k = (live, a.kind == "branch", v.cls, v.url)
                if best is None or k > best[0]:
                    best = (k, a, v)
        if best is None:
            return None
        _, a, v = best
        text = "The {} branch is at {}, {}, {} {}.".format(it.branch, a.street.rstrip(","), a.city, a.state, a.zip)
        return Answer(text=text, sources=[_src(v, a.raw)])

    # ------------------------------------------------------------ links

    _URL_STOP = {"link", "url", "websit", "website", "web", "site", "page", "use", "see", "go", "find", "online", "address", "check"}

    def _url(self, it: Intent, vi: int) -> Answer | None:
        if not ({"url", "online"} & it.kinds):
            return None
        vx = self.kb.vidx[vi]
        # "Link for X rates": the most specific official page that lists X rates,
        # with the '#rates' anchor when the site itself links to it that way.
        if it.quals.family and it.measure in RATE_MEASURES:
            fams = Q.FAMILY_GROUPS.get(it.quals.family, {it.quals.family})
            per_page = Counter()
            total = Counter()
            pages = {}
            for key, (v, live) in vx.by_key.items():
                if not live or v.cls != OFFICIAL:
                    continue
                for f in v.doc.facts:
                    if f.measure in RATE_MEASURES:
                        total[key] += 1
                        if f.quals.family in fams and f.quals.segment == ("business" if it.segment_asked else "personal"):
                            per_page[key] += 1
                            pages[key] = v
            ranked = sorted((k for k in per_page if per_page[k] >= 3),
                            key=lambda k: (-per_page[k] / total[k], -per_page[k], len(k), k))
            if ranked:
                v = pages[ranked[0]]
                base = v.url.split("#")[0]
                anchors = {h for _, h, _, _ in vx.links if h.split("#")[0].rstrip("/") == base.rstrip("/") and "#" in h}
                url = base + "#rates" if any(h.endswith("#rates") for h in anchors) else base
                return Answer(text="You can see current rates at {}".format(url),
                              sources=[Source(url=v.url, snapshot_id=v.snapshot_id, quote=v.title[:200])])
        qt = [t for t in it.toks if t not in self._URL_STOP]
        if not qt:
            return None
        wants_article = bool(re.search(r"\b(blog|article|news|story|press)\b", it.raw, re.I))
        # Per destination: the best weight each question word gets from any of its links
        # (anchor 1.0 > section heading 0.5 > URL text 0.3); navigation repeats don't add up.
        best_w = defaultdict(dict)
        count = Counter()
        first_seen = {}
        for anchor, href, ctx, v in vx.links:
            if not href.startswith("http") or self.kb.site_host not in canon_url(href).split("/")[0]:
                continue
            dest = url_path(href)
            if not wants_article and re.search(r"^/(learn|newsroom|blog|business/resources)/|/20\d\d/", dest):
                continue
            a_t = set(tokens(anchor))
            c_t = set(tokens(ctx))
            h_t = set(tokens(re.sub(r"[/#?=._-]", " ", href.split("//", 1)[-1])))
            h = href.split("?")[0]
            if "#" in h:
                base, frag = h.split("#", 1)
                h = base if (len(frag) > 15 or not frag.isalpha()) else h
            cls_w = 1.0 if v.cls == OFFICIAL else 0.5
            matched = False
            for t in qt:
                w = (1.0 if t in a_t else 0.5 if t in c_t else 0.3 if t in h_t else 0.0) * cls_w
                if w > best_w[h].get(t, 0.0):
                    best_w[h][t] = w
                matched = matched or w > 0
            if matched:
                count[h] += 1
                first_seen.setdefault(h, v)
        if not first_seen:
            return None
        app_host = "online" in it.kinds

        def total(h):
            sc = sum(best_w[h].values()) / len(qt)
            if app_host and canon_url(h).split("/")[0] != self.kb.site_host:
                sc += 0.1      # doing it online: the application itself, not the brochure page
            return sc + 0.01 * min(count[h], 10)

        best = sorted(first_seen, key=lambda h: (-total(h), len(h), h))[0]
        if total(best) < 0.6:
            return None
        dest = vx.by_key.get(canon_url(best))
        sv = dest[0] if dest and dest[1] else first_seen[best]
        return Answer(text="You can use {}".format(best),
                      sources=[Source(url=sv.url, snapshot_id=sv.snapshot_id, quote=(sv.title or best)[:200])])

    # ------------------------------------------------------------ account maintenance

    def _account_change(self, it: Intent, vi: int) -> Answer | None:
        if not (_HOW_Q.search(it.raw) or re.search(r"\b(can|may) i\b", it.raw, re.I)) or not _ACCOUNT_CHANGE_Q.search(it.raw):
            return None
        if it.quals.family or "branch" in it.raw.lower():
            return None
        vx = self.kb.vidx[vi]
        best = None
        for key in sorted(vx.by_key):
            v, live = vx.by_key[key]
            if not live or v.cls != OFFICIAL:
                continue
            for p in v.doc.passages:
                t = p.text
                if not (_SELF_SERVICE.search(t) and _STRONG_MEANS.search(t)) or _CREDENTIALS.search(t) or t.rstrip().endswith("?"):
                    continue
                k = (len(set(tokens(t)) & set(it.toks)), -len(t), v.url)
                if best is None or k > best[0]:
                    best = (k, t, v)
        phone = self._main_phone(vi)
        if best is None and phone is None:
            return None
        parts, sources = [], []
        if best is not None:
            parts.append(best[1].rstrip(". ") + ".")
            sources.append(_src(best[2], best[1]))
        if phone is not None:
            parts.append("You can also call us at {}.".format(phone[0]))
            sources.append(_src(phone[1], phone[2]))
        return Answer(text=" ".join(parts), sources=sources)

    # ------------------------------------------------------------ membership eligibility

    def _eligibility(self, it: Intent, vi: int) -> Answer | None:
        if not (_ELIG_Q.search(it.raw) and _MEMBERSHIP.search(it.raw)) or it.quals.family:
            return None
        vx = self.kb.vidx[vi]
        s = self.kb.search
        # 1. the criteria: a list (or sentence) of who qualifies, on a live official page
        best = None
        for key in sorted(vx.by_key):
            v, live = vx.by_key[key]
            if not live or v.cls != OFFICIAL:
                continue
            did = self.kb.doc_ids.get(id(v.doc))
            for pid in s.doc_pids.get(did, []):
                t = s.p_text[pid]
                if not _CRITERION.search(t) or re.search(r"[%$]", t) or t.rstrip().endswith("?"):
                    continue
                head = s.p_toks[pid] | self._ttoks(v)
                ctx = sum(1 for w in ("elig", "join", "member", "qualify") if w in head)
                k = (s.p_kind[pid] == "li", ctx, bool(re.search(r"join|member", url_path(v.url))), -len(url_path(v.url)), -pid)
                if best is None or k > best[0]:
                    best = (k, pid, v)
        if best is None:
            return None
        _, pid, v = best
        text = self._compose(pid)
        sources = [_src(v, s.p_text[pid])]
        # 2. a criterion that points at an area is resolved with the current statement of that area
        if _AREA_REF.search(text):
            area = self._area_statement(vi)
            if area is not None:
                at, av = area
                text = text.rstrip(". ") + ". " + at
                if av.url != v.url:
                    sources.append(_src(av, at))
        return Answer(text=text, sources=sources)

    def _area_statement(self, vi: int):
        """Where the institution operates: the newest dated statement naming its counties/states (R4),
        else an official page sentence naming two or more states."""
        vx = self.kb.vidx[vi]
        brand = (self.kb.brand or "").lower()
        dated, official = [], []
        for key in sorted(vx.by_key):
            v, live = vx.by_key[key]
            if not live or v.cls == 0:
                continue
            for p in v.doc.passages:
                t = p.text
                if p.kind not in ("p", "li") or len(t) > 220 or len(set(_STATE_RX.findall(t))) < 2:
                    continue
                if not re.search(r"\b(serve[sd]?|find us|located|locations|throughout|count(?:y|ies))\b", t, re.I):
                    continue
                if re.search(r"[%$]|\b(19|20)\d\d\b", t):
                    continue
                if v.doc.pub is not None and brand and brand in t.lower() and re.search(r"count(?:y|ies)", t, re.I):
                    dated.append((v.doc.pub, v.url, t, v))
                elif v.cls == OFFICIAL:
                    official.append((-len(url_path(v.url)), v.url, t, v))
        if dated:
            d, u, t, v = max(dated, key=lambda x: (x[0], x[1]))
            return t, v
        if official:
            _, u, t, v = max(official, key=lambda x: (x[0], x[1]))
            return t, v
        return None

    # ------------------------------------------------------------ generic search (R1, R3, R4, R6)

    def _generic(self, it: Intent, vi: int, strict: bool = False) -> Answer:
        vx = self.kb.vidx[vi]
        s = self.kb.search
        q = sorted(set(it.toks))
        if not q:
            return refusal()
        idf = {t: s.idf(t) for t in q}
        total = sum(idf.values())
        missing = sum(idf[t] for t in q if s.df(t) == 0)
        # Most of the question's meaning is in words the site never uses: don't guess.
        if total <= 0 or missing / total > 0.5:
            return refusal()
        present = [t for t in q if s.df(t) > 0]
        mass = sum(idf[t] for t in present) or 1.0
        eventq = bool(_EVENT_Q.search(it.raw))
        valueq = bool(_VALUE_Q.search(it.raw))
        howq = bool(_HOW_Q.search(it.raw))
        # "how many <noun>": the answer is a number that quantifies that noun ("256 Dupaco employees")
        counted = None
        hm = re.search(r"\b(?:how many|number of|count of|total number of) ((?:[a-z'-]+ ?){1,3})", it.raw.lower())
        if hm:
            words = tokens(hm.group(1))          # stop words (and the brand name) drop out
            counted = words[0] if words else None
        preds = None
        if counted:
            cand = [t for t in present if t != counted and not re.fullmatch(r"recent\w*|year|last|past|late\w*", t)]
            preds = tuple(sorted(cand, key=lambda t: (-idf[t], t))[:2])   # the two rarest key words
        seq = [t for t in it.toks if s.df(t) > 0]
        # exact phrases count only when they contain a rare word ("apple pay", not "loan option")
        bigrams = {(seq[i], seq[i + 1]) for i in range(len(seq) - 1) if max(s.idf(seq[i]), s.idf(seq[i + 1])) >= 5.0}
        qsearch = list(present)
        for t in present:
            for syn in SYNONYMS.get(t, ()):
                if syn not in qsearch and s.df(syn) > 0:
                    qsearch.append(syn)
        contactq = bool(_CONTACT_Q.search(it.raw))
        if eventq and "open" in present and "close" not in present:
            qsearch.append("close")     # "still open?" is answered by a closing date
        if _DEFINE_Q.search(it.raw) and not _ATTRIBUTE_Q.search(it.raw):
            ans = self._define(it, vi, present, idf, mass)
            if ans is not None:
                return ans
        hits = s.search(qsearch, vx.allowed, vx.skip_text, limit=60)
        if not hits:
            return refusal()
        scored = []
        for score, pid in hits:
            w, d, v = vx.allowed[s.p_doc[pid]]
            tt = self._ttoks(v)
            have = s.p_toks[pid] | tt
            cov = sum(idf[t] for t in present
                      if t in have or any(x in have for x in SYNONYMS.get(t, ()))) / mass
            tcov = sum(idf[t] for t in present if t in tt) / mass
            sc = score * (0.3 + 0.7 * cov * cov) * (1.0 + 0.3 * tcov)
            text = s.p_text[pid]
            if _CTA.search(text):
                sc *= 0.6
            if _CREDENTIALS.search(text) and not _CREDENTIALS.search(it.raw):
                sc *= 0.3
            if howq:
                sc *= 1.6 if _MEANS.search(text) else 0.8
            if valueq and not re.search(r"\d", text):
                sc *= 0.6
            if text.rstrip().endswith("?"):
                sc *= 0.7          # a question on the page (FAQ or form label) is rarely the answer
            path = url_path(v.url)
            if path.startswith("/business") != it.segment_asked:
                sc *= 0.8          # R5 default: personal unless the question says business
            if contactq and path == "/contact":
                sc *= 1.5          # the contact page is the home of contact details
            if bigrams:
                pt = tokens(text)
                pb = {(pt[i], pt[i + 1]) for i in range(len(pt) - 1)}
                sc *= 1.0 + 0.25 * len(bigrams & pb)        # exact phrases ("apple pay") count
            if s.p_order[pid] < 4:
                sc *= 1.0 + 0.05 * (4 - s.p_order[pid])     # article leads carry the main point
            if eventq:
                sc *= 2.0 if (find_dates(text) and re.search(r"\bclos|\bopen", text, re.I)) else 0.7
            if counted:
                sc *= 1.5 if _quantifies(text, counted, preds) else 0.8
            scored.append((sc, pid, d, v, cov))
        # R4: the same statement on several pages or restated year after year (same template,
        # numbers/ordinals masked, small wording drift allowed) -> one statement, newest copy
        clusters = []
        members = {}        # representative pid -> every (date, pid, version) restating that statement
        for sc, pid, d, v, cov in sorted(scored, key=lambda x: (-x[0], x[1]))[:80]:
            tset = set((s.p_tmpl[pid] or str(pid)).split())
            for c in clusters:
                inter = len(tset & c[5])
                if inter and inter / len(tset | c[5]) >= 0.6:
                    c[0] = max(c[0], sc)
                    c[6].append((d, pid, v))
                    if d > c[2]:
                        c[1], c[2], c[3], c[4] = pid, d, v, cov
                    break
            else:
                clusters.append([sc, pid, d, v, cov, tset, [(d, pid, v)]])
        for c in clusters:
            members[c[1]] = c[6]
        ranked = sorted((c[:5] for c in clusters), key=lambda x: (-x[0], -x[2].toordinal(), x[1]))
        # R4: among passages that answer about equally well, the newest dated statement wins.
        top = ranked[0][0]
        band = [r for r in ranked[:8] if r[0] >= RECENCY_BAND * top and r[4] >= ranked[0][4] - 1e-9]
        sc, pid, d, v, cov = max(band, key=lambda r: (r[2], r[0], -r[1]))
        if cov < (COVERAGE_STRICT if strict else COVERAGE_MIN):
            return refusal()
        text = s.p_text[pid]
        ev = self._event(it, text, vi) if eventq else None
        if ev is None and text.rstrip().endswith("?"):
            nxt = pid + 1
            if nxt < len(s.p_doc) and s.p_doc[nxt] == s.p_doc[pid] and not s.p_text[nxt].rstrip().endswith("?"):
                pid = nxt          # an FAQ question: the answer is what follows it
        series = None if ev else self._year_series(it, pid, members, vi)
        if series is not None:
            ev, pid, v = series
        if ev is None and counted and s.p_kind[pid] == "li" and _quantifies(s.p_text[pid], counted, preds):
            ev = s.p_text[pid]       # the item that answers "how many" stands alone (no stray sibling numbers)
        text = ev or self._compose(pid)
        if not _CREDENTIALS.search(it.raw):
            from .text import split_sentences
            kept = [x for x in re.split(r"(?<=[.;])\s+", text) if not _CREDENTIALS.search(x)]
            text = " ".join(kept).strip()
            if not text:
                return refusal()
        sources = [_src(v, s.p_text[pid])]
        if _CALL_US.search(text) and not _PHONE.search(text):
            ph = self._main_phone(vi)
            if ph is not None:
                text = text.rstrip() + " Call {}.".format(ph[0])
                sources.append(_src(ph[1], ph[2]))
        # The best passage leaves some of the question's key words uncovered: add the next-best
        # passage that covers them, from a live official page, with no rate/fee values (no stale
        # numbers from prose). Two passages at most.
        if ev is None:
            have1 = s.p_toks[pid] | self._ttoks(v)
            missing = [t for t in present if idf[t] >= 3.0 and t not in have1
                       and not any(x in have1 for x in SYNONYMS.get(t, ()))]
            if missing:
                for sc2, pid2, d2, v2, cov2 in ranked[1:15]:
                    e2 = vx.by_key.get(v2.key)
                    if v2.cls != OFFICIAL or not (e2 and e2[1]) or s.p_tmpl[pid2] == s.p_tmpl[pid]:
                        continue
                    t2 = s.p_text[pid2]
                    if re.search(r"[%$]", t2) or len(t2) > 300 or t2.rstrip().endswith("?") or t2 in text:
                        continue
                    have2 = s.p_toks[pid2] | self._ttoks(v2)
                    if not any(t in have2 for t in missing):
                        continue
                    # topical continuity: it must also share a covered key word with the first passage
                    if not any(t in have2 for t in present if t in have1):
                        continue
                    text = text.rstrip() + " " + t2
                    sources.append(_src(v2, t2))
                    break
        nums = _nums(s.p_text[pid])
        seen = {canon_url(v.url)}
        extra = []
        for sc2, pid2, d2, v2, cov2 in sorted(scored, key=lambda x: (-x[0], x[1]))[:60]:
            key2 = canon_url(v2.url)
            if key2 in seen:
                continue
            if s.p_tmpl[pid2] == s.p_tmpl[pid] or (nums and nums <= _nums(s.p_text[pid2])):
                seen.add(key2)
                # canonical pages first: official, no query string, shorter path
                extra.append((-v2.cls, "?" in v2.url, len(url_path(v2.url)), v2.url, v2, pid2))
        for _, _, _, _, v2, pid2 in sorted(extra)[:2]:
            sources.append(_src(v2, s.p_text[pid2]))
        return Answer(text=text, sources=sources)

    def _define(self, it, vi, present, idf, mass):
        """'What is X?': the page whose own title names X answers with its lead paragraph."""
        vx = self.kb.vidx[vi]
        s = self.kb.search
        if not present:
            return None
        subject = max(present, key=lambda t: (idf[t], t))   # the rarest word names the subject
        best = None
        for key, (v, live) in vx.by_key.items():
            if not live or v.cls == 0:
                continue
            tt = self._ttoks(v)
            if not tt:
                continue
            tcov = sum(idf[t] for t in present if t in tt) / mass
            if subject not in tt or tcov < 0.5:
                continue
            did = self.kb.doc_ids.get(id(v.doc))
            if did is None or did not in vx.allowed:
                continue
            k = (v.cls, v.doc.pub or v.fetched or MIN_DATE, -len(tt), v.url)
            if best is None or k > best[0]:
                best = (k, v, did)
        if best is None:
            return None
        _, v, did = best
        for pid in s.doc_pids.get(did, []):
            if s.p_kind[pid] == "p" and len(s.p_text[pid]) >= 60 and s.p_text[pid] not in vx.skip_text:
                if not any(t in s.p_toks[pid] for t in present):
                    continue
                return Answer(text=self._compose(pid), sources=[_src(v, s.p_text[pid])])
        return None

    def _restatements(self, vi, pid):
        """Every live passage in the view restating the same statement (template overlap >= 0.6)."""
        s = self.kb.search
        vx = self.kb.vidx[vi]
        base = set((s.p_tmpl[pid] or "").split())
        if not base:
            return []
        out = []
        for sc, p2 in s.search(sorted(s.p_toks[pid]), vx.allowed, vx.skip_text, limit=60):
            t2 = set((s.p_tmpl[p2] or "").split())
            if t2 and len(base & t2) / len(base | t2) >= 0.6:
                w, d, v2 = vx.allowed[s.p_doc[p2]]
                out.append((d, p2, v2))
        return out

    def _year_series(self, it, pid, members, vi=None):
        """Year-labelled statistics (R4 statement restated each year). A relative period ('last year',
        'recently') gets the latest figure and the one before it, each labelled with its year; an explicit
        year gets only that year's statement. Returns (text, pid, version) or None."""
        s = self.kb.search
        if not (_RELATIVE_PERIOD.search(it.raw) or re.search(r"\b(?:19|20)\d\d\b", it.raw)):
            return None
        group = list(members.get(pid) or [])
        if vi is not None:
            group += self._restatements(vi, pid)
        if not group:
            return None
        by_year = {}
        for d, p2, v2 in sorted(group, key=lambda x: (x[0], x[1]), reverse=True):
            t = s.p_text[p2]
            ys = re.findall(r"\b(?:19|20)\d\d\b", t)
            if len(set(ys)) != 1 or not re.search(r"\d", re.sub(r"\b(?:19|20)\d\d\b", "", t)):
                continue                       # exactly one year, plus a figure
            by_year.setdefault(int(ys[0]), (p2, v2, t))
        asked = [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", it.raw)]
        if asked:
            hit = by_year.get(asked[0])
            if hit is None:
                return None
            p2, v2, t = hit
            return (t, p2, v2)
        if not _RELATIVE_PERIOD.search(it.raw) or len(by_year) < 2:
            return None
        years = sorted(by_year, reverse=True)[:2]

        def short(t):
            return t if len(t) <= 220 else t[:220].rsplit(" ", 1)[0] + "..."

        text = " ".join("{}: {}".format(y, short(by_year[y][2]).rstrip()) for y in years)
        p2, v2, _ = by_year[years[0]]
        return (text, p2, v2)

    def _main_phone(self, vi: int):
        """The main phone number, learned from the official contact page (most frequent number there),
        else the most frequent number on live official pages."""
        cache = self.__dict__.setdefault("_phone_cache", {})
        if vi in cache:
            return cache[vi]
        vx = self.kb.vidx[vi]
        found = None
        contact = self._topic_home(vi, "/contact")
        pools = [[contact]] if contact is not None else []
        pools.append(sorted((v for v, live in vx.by_key.values() if live and v.cls == OFFICIAL), key=lambda v: v.url))
        for pool in pools:
            cnt = Counter()
            where = {}
            for v in pool:
                for p in v.doc.passages:
                    for m in _PHONE.finditer(p.text):
                        num = re.sub(r"[^\d]", "", m.group(0))
                        cnt[num] += 1
                        where.setdefault(num, (v, p.text))
            if cnt:
                num = sorted(cnt, key=lambda k: (-cnt[k], k))[0]
                found = ("{}-{}-{}".format(num[:3], num[3:6], num[6:]), where[num][0], where[num][1])
                break
        cache[vi] = found
        return found

    def _compose(self, pid: int) -> str:
        """Best passage plus what belongs to it: sibling list items, a section's first lines,
        or a following sentence that refers back to it ('This initiative provides ...')."""
        s = self.kb.search
        did, blk, kind = s.p_doc[pid], s.p_block[pid], s.p_kind[pid]
        text = s.p_text[pid]
        n = len(s.p_doc)
        if kind == "li":
            items = []
            j = pid
            while j - 1 >= 0 and s.p_doc[j - 1] == did and s.p_block[j - 1] == blk:
                j -= 1
            while j < n and s.p_doc[j] == did and s.p_block[j] == blk and len(items) < 6:
                items.append(s.p_text[j].rstrip(". "))
                j += 1
            text = "; ".join(items) + "."
        elif kind == "h":
            j = pid + 1
            extra = []
            while j < n and s.p_doc[j] == did and len(extra) < 4 and s.p_kind[j] != "h":
                extra.append(s.p_text[j].rstrip(". "))
                j += 1
            if extra:
                text = text.rstrip(":.") + ": " + "; ".join(extra) + "."
        elif _LEAD_IN.search(text):
            # a lead-in ("... using one of these channels:") brings what follows it
            j = pid + 1
            extra = []
            while j < n and s.p_doc[j] == did and len(extra) < 4 and s.p_kind[j] != "h":
                extra.append(s.p_text[j].rstrip(". "))
                j += 1
            if extra:
                text = text.rstrip(":. ") + ": " + "; ".join(extra) + "."
        else:
            j = pid + 1
            if j < n and s.p_doc[j] == did and s.p_block[j] == blk and _ANAPHORA.match(s.p_text[j]) and not re.search(r"\d", text):
                text = text.rstrip() + " " + s.p_text[j]
        cap = 650 if kind == "h" else 450
        if len(text) > cap:
            text = text[:cap].rsplit(". ", 1)[0].rstrip(".") + "."
        return text

    def _event(self, it: Intent, text: str, vi: int) -> str | None:
        m = re.search(r"\bclos\w*", text, re.I)
        if not m:
            return None
        ds = find_dates(text)
        if not ds:
            return None
        pos, d, ds_text = min(ds, key=lambda x: abs(x[0] - m.start()))
        ref = it.ref_date or self.kb.views[vi].taken_on
        subject = "The {} branch".format(it.branch) if it.branch else "It"
        if d < ref:
            prefix = "No. " if ("yesno" in it.kinds and re.search(r"\bstill\b|\bopen\b", it.raw, re.I)) else ""
            return "{}{} closed on {}.".format(prefix, subject, ds_text)
        return "{} is scheduled to close on {}.".format(subject, ds_text)
