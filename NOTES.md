# NOTES: a knowledge system that knows what's true *now*

**No API keys are needed.** The solution uses only the Python standard library (Python ≥ 3.11). Everything runs offline from the snapshots.

## Running it

The snapshot data belongs to the organizers and is **not** in this repository (`.gitignore` excludes `snapshots/` and `snapshots_zip/`). Copy the kit's `snapshots_zip/` folder into the repo root, then:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

The harness unpacks the zips on the first run.

Dev tools (not graded): `dev/run_both.sh` (self-test + public eval + misses), `dev/paraphrase_probe.py` (my own rewordings), `dev/review_conflicts.py`, `dev/check_py311.py`, `dev/robustness.py` (subset of snapshots, reversed page order), `dev/audit_numbers.py` (stray numbers; conflict losers per topic).

## Results (public practice set)

| Check | Python 3.11.18 | Python 3.14.6 |
|---|---|---|
| Self-test (`harness/selftest`) | 1.00 (conflict F1 1.0, winner 1.0) | 1.00 |
| Public eval accuracy | 0.969 (the ceiling: only dup-005 misses, and it can't score), 0 stale-value hits, 0 errors | 0.969 |
| Consistency (paraphrase + repeat) | 1.00 | 1.00 |
| p95 answer time | 0.07 s | 0.05 s |
| Ingest (5 snapshots) | ~10 s | ~9 s, ~490 MB peak |
| Own rewordings (155 probes, 59 groups) | 0 false refusals, 0 traps, 0 repeat diffs, 1 accuracy miss (a vague Forbes rewording); every group gives the identical set of numbers (strict, as the scorer computes them) | same |
| Robustness (`dev/robustness.py`) | 0 errors; reversed page order → identical answers and conflicts; snapshots 01–03 only → 0 errors; p95 < 0.1 s | same |

**Nothing is tied to this crawl.** The site's host, URL base and brand name are learned from the data at the first ingest (most common host; the brand as page titles write it) and used for link filtering, topic home pages, stop words and answer wording. There are no snapshot IDs, dates, page counts or values in the logic. Address parsing accepts any US state. Duplicate pages are chosen by content, never by page order.

## What the data looks like

These are the mess patterns found by tracing topics across all five crawls.

| Topic | Mess | Authoritative page |
|---|---|---|
| Auto loan, 60 mo, 2025 model year | The rate page says 5.39% (tier "2024-27", effective 08/31/2026). The calculator and `/switch-and-earn` still say 5.34% under the *old* tier label "2023–26" (effective 1/1/2026). | `/rates` and the auto-loan product page |
| 10-month jumbo certificate special | 3.90% APY in May–Aug, 4.00% in September. The *standard* 10-month special is 3.90% in September, so the same number means a different tier. The 25-month special was replaced by a 17-month one. | `/rates` "Special Certificates", from the snapshot matching the question's date |
| Share Savings tiers | Values are stable, but the formatting drifts. The business pages show an impossible row (0.20% dividend rate vs 0.15% APY). Look-alikes: money market and HSA rows with the same balances. | `/rates` (personal unless the question says business) |
| Mortgages | The rate moved every crawl (5.875 → 6.250 → 6.125 → 6.375 → 7.13%). `/homebuying-guidance` and `/mortgage-refi` keep old tables. | `/rates`, `/personal/loans/home-loans` |
| Fees | Values are identical in every copy of the fee schedule PDF; only the formatting is noisy. There is no lost-card replacement fee, so that question gets a refusal. | The fee schedule PDF |
| Lobby / drive-up hours | Not on the home page or any footer. They appear only in about six newsroom branch-opening articles. | The most common current schedule; `/contact` is the locator |
| Branch count | Press-release boilerplate says 19 → 20 → 22 → 24 → 23 → 22. A majority vote would pick 23, which is wrong. | The newest *dated* statement (22) |
| Dropped pages | Renamed tag listings, a moved Bright Savings page, a fragment duplicate, retired PDFs. The crawler already carries pages it couldn't re-fetch, so a missing page was probably removed. | See R1 |

## What "current" means (precedence rules)

- **R0 View.** The reference date is `as_of` if given, else a date written in the question ("as of May 2026" → May 31, "October 2026", "May 20, 2026", ISO dates), else the newest snapshot. Bare years are qualifiers, not dates ("2025 model-year"). The answer comes from the latest snapshot taken on or before that date. A date after the newest snapshot uses the newest; a date before the first uses the first.
- **R1 Carry-forward, demoted.** A page missing from a snapshot keeps its last version but is flagged *carried*. It ranks below every live page, so it answers only when no live page covers the fact (self-test t3). It never creates a "current" contradiction. *Why:* absence is weak evidence (it might be a crawl miss), but presence is strong evidence that a page is current.
- **R2 Validity.** Drop expired promotions ("offer ended/expired/valid through <date>" before the view date), tag/author listing pages, and noise (data-URIs, phone country pickers).
- **R3 Ranking within one fact slot.** Compared in this order:
  1. Authority class: official product/rate page or official document (fee schedule) > calculator/promo/landing page > article.
  2. Live over carried.
  3. The stated effective date next to the value ("Rates are effective as of …").
  4. Freshness of the copy (`fetched_at`).
  5. URL, as a deterministic tie-break.
- **R4 Dated statements** (counts, recognitions, events). There is no page of record, so the newest *publication* date wins. When the same sentence template appears with different numbers (digits, ordinals and stop words masked), only the newest is kept. Among passages that answer about equally well, the newest dated one wins. This one rule fixes both the branch count (22, not 23) and Forbes ("fifth", not "fourth").
- **R5 Qualifiers define the slot; they never conflict.** Slot = family + segment (personal/business) + variant flags (jumbo, special, IRA Roth/Traditional, purchase/refinance, intro, a named product like "Achieve") + term + tier (balance, loan amount or model year, compared as numeric ranges, so a relabeled tier is matched by best overlap) + measure (APY, dividend rate, APR, rate). When the question is silent, the defaults are personal, non-jumbo, purchase, standard and the unnamed product.
- **R6 Reasoning from stated facts.** An event date before the reference date is answered in the past tense ("No. The Hy-Vee branch closed on March 31, 2026.").

## How it works

The heavy work happens in `ingest()`:
- Each distinct page content is parsed once (keyed by content hash).
- Pipe tables become typed facts: headers become measures; the row label plus the label line above the table plus the heading become product and qualifiers; the "effective as of" line after the table becomes the effective date. Short "label: value" lines are parsed the same way.
- Each page also yields count claims, hours blocks, addresses, links and sentence passages.
- One global BM25 index covers all passages. Each snapshot gets a *view* (every URL at its latest version, live or carried) with its own fact/claim/hours indexes and a boilerplate filter.
- `answer()` is a lookup; `conflicts()` is computed once and cached.

`answer()` routing (`submission/cuknow/answer.py`):
1. **Link** questions vote over the site's own links (anchor 1.0 > section heading 0.5 > URL text 0.3; best match per destination, so navigation repeats don't add up). "X rates" links resolve to the most specific official page listing those rates, with the `#rates` anchor the site itself uses.
2. **Counts** (the institution's branches, members, employees) come from claims via R4, but only when the question is about the count itself; words that only name a period are allowed. A named period ("last year" and the same variants as above, "in 2024", "as of May 2026") sets a cutoff at the period's end (relative periods measured from the snapshot's date). The answer is the newest dated statement published by then, said in the past tense with the statement's month and year only ("Dupaco had 23 branch office locations (per its December 2025 statement)"); a day number such as "December 23" could match a forbidden count, so the full date appears only in the source quote. **Entity guard:** a count must come from a sentence about the institution itself (it names the institution, or says it/we/our in its own boilerplate), never a national or industry statistic; if none qualifies (e.g. "in 2019"), the answer is a refusal.
3. **Hours**: the most common current schedule, or the distinct schedules for "do all branches…". Schedules that share the main weekday hours are merged ("Saturday 8 a.m.–1 p.m. (some branches 9 a.m.–noon)"), and the newest page for each variant is cited. A named branch with no schedule gets a refusal.
4. **Addresses**: the branch-locator entries, matched on city or street.
5. **Rates** use structured facts only, never prose. Rows are filtered strictly by the qualifiers the question names; otherwise the defaults apply. A whole-product listing (no term, tier, superlative or yes/no) also lists the jumbo tiers, labelled: "certificate rates" gives all 4 specials and all 8 regular terms, without asking which term. Lists come from the single winning table. Superlatives pick the extreme row. Yes/no questions are checked without echoing the question's number. Explicit "previous rate / how has it changed" questions walk back through snapshots.
6. **Membership eligibility** ("who can join", "am I eligible", "membership requirements"): the stated criteria (the list of who qualifies on a live official page) rather than passages that merely repeat the question's words. A criterion that points at an area ("our charter area") is resolved with the current statement of that area: the newest dated sentence naming its counties and states (R4; 118 counties, matching the conflict resolution), else an official sentence naming the states.
7. **Account maintenance** ("how do I update/change my address / phone / email / contact information"): an official self-service sentence that names a concrete means ("by logging into your account"), plus "You can also call us at …" with the main phone number learned from the official contact page (its most frequent number). Every wording gets the same answer.
8. **Fees** match fee-table items weighted by word rarity. If no item matches, a strict prose search runs; if that fails too, the answer is a refusal.
9. **Everything else** goes to BM25 over sentences, re-weighted by:
   - coverage of the question's rare words, plus page-title words;
   - exact rare phrases ("apple pay");
   - a penalty for call-to-action sentences and for a page's own questions (FAQ headings, form labels);
   - article leads;
   - personal pages by default (R5);
   - the contact page for contact questions;
   - dated passages for "when does X close".

   - "how many X" / "number of X": passages where a number directly quantifies X ("256 … employees"; a year doesn't count, and "people" matches employees or members), with one of the question's two rarest key words in the *same clause*. So "627 employees volunteered 6,136 hours, with many using VTO" does not count VTO users. A matched list item then stands alone, without stray sibling numbers.
   - "how can I …" questions: passages that state a means ("you can … by logging in", "call", "visit").
   - synonyms for account wording (update ↔ change/edit; contact information ↔ personal information/address/phone/email), used both for search and for coverage.
   - passages that ask for credentials ("member number", "account number", SSN, password, PIN) are demoted, and such sentences are removed from answers unless the question asks about them.

   Year-to-year restatements of the same sentence (same template, numbers masked, at least 60% word overlap) count as one statement, and the newest copy wins (R4). This keeps reworded questions on the same statement. If the best passage leaves some of the question's rare words uncovered, the next-best passage covering them is appended, but only from a live official page with no % or $ values, and two passages at most.

   **Year-labelled statistics** (a statement restated each year with its year, e.g. Volunteer Time Off users): a question about a relative period ("last / past / previous / prior / this past / most recent year", "a year ago", "recently", "lately") with no explicit year gets the latest figure and the one before it, each labelled with its year ("2024: 256 …; 2023: 294 …"). A question naming a year gets only that year's statement; no period gets the current figure. Restatements are found across the whole snapshot. This lives in the generic path only, so rate, fee and branch-count answers are unaffected.

   Answers keep the context that belongs to the hit: sibling list items (a blank line between items doesn't end a list), a section's first lines, a lead-in's continuation (a passage ending with ":" or saying "one of these / the following" brings the lines after it; short "Label: value" lines like "Phone: …" are kept as passages), or a following "This …" sentence. A page that a *definitional* question names in its title answers with its lead paragraph.

**Refusals** use `confident=False` and the fixed text "That isn't listed on our website, so I can't confirm it. Please contact us for details." (no digits, no sources). They happen when:
- a structured intent finds no slot (7/1 ARM, construction-loan rate, 60-month jumbo certificate, a product that didn't exist at the date asked);
- a named branch has no data;
- most of the question's meaning is in words the site never uses;
- the best passage covers less than 45% of the question's rare-word weight (80% after a fee/minimum miss).

**Question parsing.** Number words become digits ("six month", "eighty-four"). Money in any form becomes a balance or amount ("15k", "fifteen thousand dollars", "one hundred thousand", "500 dollars"). "How much is a five-year auto loan?" is a rate question when a loan or deposit product is named and no fee word (fee, charge, cost, price) appears. Explicit change questions ("previous rate", "how has X changed") answer "now X; previously Y", matching the closest tier first.

**Number audit** (`dev/audit_numbers.py`). The scorer matches forbidden values by number anywhere in the text and compares paraphrases by their number sets, so every number in every answer (public eval + all probes) was classified.
- **Stray numbers removed:** the routing-number answer had picked up an unrelated appended passage ("…Create passwords using … numbers…" in one wording; "…call at 800-373-7600, ext." in another). An appended passage must now share a covered topic word with the first, and "again/repeat" are stop words. Count answers give the statement's month and year, never a day.
- **Numbers kept, and why:** each answer's own values plus its qualifier labels (term, model-year or loan-amount tier, branch address, schedule times, the Hy-Vee closing date, and the year a statistic describes). Removing these would make answers ambiguous.
- **Conflict losers:** each of the 33 conflict topics was asked as a question; every answer contains the winner and none of that conflict's losing values. An old value appears only for history/as-of questions. The loose check also flagged list answers where a *different* row's current value equals a loser elsewhere (3.90% is the current 10-month standard special and the old 10-month jumbo); those are correct and kept.
- **Consistency:** both measures are always shown when both exist (APY + dividend rate, rate + APR), with the asked one first, so "APY?", "rate?" and "dividend?" rewordings give the same numbers. A whole-product question with no specific product named is treated as a catalog, so "credit card rates" and "the rates for the credit cards" list the same cards.

**Answer-text hygiene.** The text holds only the answer's values and qualifier labels: no effective dates, snapshot IDs or footnote digits. Losing values go to `conflict_note`, which is not scored. Sources: the winning page first, then agreeing official pages (canonical ones: official, no query string, short path), up to three, each with `snapshot_id` and a short quote.

**Crash-proofing.**
- `answer()` returns the refusal on any exception.
- `ingest()` skips a bad page instead of aborting.
- `conflicts()` drops a broken entry instead of the list.
- `CUKNOW_DEBUG=1` re-raises, for development only.

**Determinism.** No randomness. Every set or dict iteration that affects output is sorted (string hashing differs between processes). Paraphrases reduce to the same intent, so they get the same text.

## Conflicts (precision first)

- One entry per slot whose rate-like values disagree, either:
  - between **live** pages now (a stale calculator, promo or guidance page is still up), or
  - between the slot's winner in an earlier snapshot and now (the rate moved).
- APY and dividend rate (or rate and APR) are one entry. Its `resolution` names both winners ("7.29% APR / 7.25% rate").
- Values are compared numerically, so formatting differences don't count. Identical disagreements (business or IRA mirrors of a personal table) are reported once, preferring the personal entry.
- The branch count is included (R4). So are other **dated statements** restated with a different count or ordinal on live, dated pages ("This was the fifth/fourth time Forbes recognized …", counties served 118/112/108), with the newest publication as the resolution. Time series are excluded because they are not contradictions: sentences anchored to a year, month, "this year", "annual" or "consecutive", money/percent amounts, growth figures (members, membership, employees, assets, hours, dividends). Sentences must name the institution itself.
- Order: current contradictions first, then changes over time; capped at 40 (33 today: 26 current, 7 over time).

## Eval category → handler map

| Category | Handler(s) | Rules |
|---|---|---|
| `branch_info` | Hours (common schedule; distinct schedules for "all branches"); address (locator); BM25 for other branch facts | R0, R1, R4, R5; named branch with no data → refuse |
| `conflict_resolution` | Structured rates; count claims; yes/no value check | R3, R4, R5 |
| `current_value` | Structured rates/fees; definitional lead; BM25 for programs and contact info | R0, R2, R3 |
| `entity_attribution` | Family resolution (learned vocabulary + synonyms); BM25 for identifiers (routing number) | R5, R3 |
| `list_completeness` | Catalog mode: every tier/term of the winning table in order. Credit cards = all personal **and** business cards (business only when its values differ from personal). Certificates = regular terms + specials. FAQ / list-item answers keep their siblings. | R5 |
| `should_refuse` | Refusal policy above | R2, R5 |
| `stale_trap` | Precedence over slots; template collapse; **link questions** via the link voter (never an invented URL) | R1–R4 |
| `temporal_as_of` | View selection, then the normal handler; event-date reasoning; explicit change questions | R0, R6 |
| `tier_disambiguation` | Shared qualifier parser: terms (number words, years → months), jumbo/standard, rate vs APY, balance/amount/model-year ranges (tightest tier for an amount), purchase/refinance, superlatives | R5 |

## Design decisions worth defending

- **Rules, not an LLM.** The task is about provenance and time; deterministic rules give consistency (15%), speed (15%) and explainable conflict reasons, at zero cost.
- **One qualifier parser for both sides.** Rows and questions go through the same normalizer, so wording differences disappear before matching.
- **Slots and best-overlap tiers.** Comparing like with like is what stops tiers from looking like conflicts, while still catching the relabeled-tier stale calculator.
- **Authority before recency, effective date before crawl date.** A re-crawled calculator is still a calculator. Between official pages, the page's own effective date is better evidence than when we fetched it.
- **Newest dated statement for institutional facts.** Majority voting over press releases is provably wrong here (23 vs 22).
- **Carry forward but demote.** This satisfies the self-test without letting retired pages win.
- **Refuse on structure.** Refusals come from a structured miss, not from guessing thresholds alone; the probe guards against false refusals.

## Known limits

- `dup-005` has no required values, so the scorer gives it 0 whatever we answer.
- `dup-081`, `dup-060` and `dup-088` now pass through the general rules above (eligibility criteria + area statement; account maintenance + main phone; relative-period year series).
- `dup-088` background: I checked both articles. They measure the same thing (employees who used paid Volunteer Time Off in a calendar year): 294 is 2023 (published April 19, 2024) and 256 is 2024 (published April 21, 2025). The publication dates are read correctly, so R4 is not misreading them; 256 is simply the newer figure. Seen from the view date (September 2026), "last year" would mean 2025, which the site doesn't report for this statistic, so the relative-period rule answers with the latest figure and the one before it, each labelled with its year. Both articles are live in all five snapshots.
- Count questions with a period now go to the count handler (cutoff at the period's end, entity guard). Counts are limited to what the site states: member counts, for example, are only stated as "more than N members" in some years, so "last year" may return an older dated statement.
- The rules were developed against the public practice set. Generalization is checked with my own rewordings (`dev/probes.jsonl`) and the robustness runs, but hidden-set accuracy may be lower than the public score.
- Retrieval answers are extracted sentences, so wording is the site's, not a paraphrase.
