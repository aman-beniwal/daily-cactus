# Open issues backlog — from the 2026-07-18 run review

## P11 — Unstop filter far too permissive (found in Batch A review, 2026-07-19)
`fetch_opportunities.py` keeps 245 of 332 Unstop items (74%), and the survivors
are dominated by SCHOOL/COLLEGE contest noise that is irrelevant to this reader
(a professional pivoting to founder's-office / AI-generalist roles). Real
examples kept by the current filter: "U-19 AI Olympics", "NERDS 1.0 — India's
First AI Hackathon For Schools", "Google Gemini AI Product Trial – Freshers
Party Planning Challenge", "Orchestrix: Newgen x AI Club IITM".
Unstop is 64% of all opportunity candidates (245/384), so this noise will
dominate the section and crowd out the genuinely good items.
By contrast **10times is excellent** and needs no filtering: "DataHack Summit",
"Automation and Robotics Expo", "BFSI Innovation & Technology Summit",
"Finance Transformation & Analytics India Summit" — real professional
conferences with clean forward dates.
**Fix:** tighten the Unstop quality filter hard —
 - exclude organizer/title matches for school/college/campus contests
   (`School`, `Freshers`, `U-19`, `Class`, `Campus`, `College Club`, quiz),
 - require a meaningful prize OR a credible non-campus organizer,
 - prefer items whose title/summary match the reader's domains,
 - and weight 10times/Luma/Devpost above Unstop in the shortlist ranking.
Re-measure the kept-count and eyeball the top 12 titles after tightening.

Diagnosed, NOT yet fixed (owner wants them fixed together). Ordered by impact.

## P1 — Cross-day dedup is DEAD (causes the "repeats from yesterday")
**Root cause:** `build_digest.load_recent_used()` reads `drafts/*.json` from the
repo working tree (i.e. `main`). The routine pushes drafts to `claude/*`
branches, which **never merge to main** — `automerge.yml` used to do that merge,
and it was deleted on 2026-07-17 (it was causing duplicate publishes).
**Evidence:** newest draft on `main` is `2026-07-10`; today is `2026-07-18`, so
the 7-day dedup window contains ZERO drafts. Dedup silently no-ops.
**Regression introduced by:** deleting automerge.yml without replacing the
draft→main path. (Owner authorized the deletion; the dependency was missed.)
**Fix options:** (a) read the last 7 days of PUBLISHED EDITIONS from `gh-pages`
instead of drafts — editions are the true record and always exist (preferred:
no main-branch coupling, no re-trigger risk); (b) have publish.yml commit the
draft back to main with a `paths-ignore`/skip-ci guard so it doesn't re-trigger
itself. Recommend (a).

## P2 — LYING FEED DATES + relative-date resolution = confidently wrong output
**This is the "6-month-old summit" loophole. Owner was right; my first pass was
wrong because I trusted the feed date — the same mistake the pipeline makes.**

Failure chain (all verified 2026-07-18 on the India-AI Impact Summit story):
1. The article's real byline is **February 14, 2026**; the summit ran **Feb
   16-20, 2026** — five months ago.
2. **newsonair.gov.in's RSS reports `published: 2026-07-17T22:08:52`** for it —
   a fresh date for a five-month-old article. The feed metadata is simply false.
   Our recency window, max-age cutoff and ranking ALL trust that field, so the
   story sails through every date check we have.
3. The page exposes **no reliable machine-readable date**: no
   `article:published_time`, no JSON-LD `datePublished`, no `<time datetime>`.
   The only ISO date in the HTML is *today's* date (dynamically injected).
4. The article body says the summit runs "from the 16th to the 20th of **this
   month**" — a RELATIVE reference. Our extract captures "this month" but NOT
   the word "February" (verified).
5. The editor resolves "this month" against the WRONG feed date (July) and
   publishes **"runs July 16-20"** — a confidently stated, factually wrong date
   for an event that already happened. This is the hallucination class the owner
   most cares about, produced without the model doing anything "wrong" given
   what it was handed.

**Fixes (layered — no single one is sufficient):**
- **(a) Ban relative-date resolution in the prompts.** If the source text says
  "this month / next week / today / yesterday / tomorrow" without an absolute
  date, NEVER convert it to a specific date — quote it as-is or omit the timing.
  This alone stops the wrong-date publication even when the feed lies.
- **(b) Extract the article's OWN date during enrichment** where available
  (meta tags / JSON-LD / visible byline regex) and, when it disagrees with the
  feed date by more than ~7 days, trust the ARTICLE and re-apply the max-age
  cutoff. Note: this specific site exposes none of those, so (b) helps broadly
  but would NOT have caught this one — hence (a) and (c).
- **(c) URL-level cross-edition suppression (see P1).** Even when we cannot tell
  an article is old, we CAN tell we have published that exact URL before. This
  is the practical fix for the recurrence.
- **(d) Source-level distrust list** for feeds caught re-dating (newsonair);
  optionally require a corroborating second source before publishing.
- **(e) Same-event flooding** (the original, lesser issue) still applies: a
  multi-day event yields many differently-titled fresh articles; near-dup only
  matches titles. Cluster by event/entity and cap 1-2 per event per edition.

## P3 — Full-article text never reaches the writer (weak summaries)
Fixed in commit cf0f5e9 (select.yml push bug + digest_lean commit), but the
2026-07-18 paper was written from 2500-char extracts, not whole articles.
**Symptom the owner spotted:** the Databricks summary states the funding facts
($188B, Coatue, close this summer, the three products) but not the *reasoning* —
why the raise, how it will be used, the Ghodsi "tokenmaxxing → valuemaxxing"
strategy quote, 20,000+ orgs / 70% of Fortune 500 scale. All of that sits BELOW
the 2500-char cut. Verified the full fetch returns it at 12k chars.
**Status:** should self-resolve on the next run; VERIFY on 2026-07-19.

## P4 — Layout wastes horizontal space
Page is capped at `max-width:1020px`; on a wide monitor there are large empty
margins left and right. Owner wants the full page used.
**Fix:** raise the wrap max-width (or make it fluid, e.g. `min(1600px, 94vw)`)
and let the card grid go to 3 columns above ~1400px, 2 columns mid, 1 on mobile.
Keep text line-length readable (~66-72ch) inside cards so it doesn't become a
wall of text.

## P5 — Empty "no photo" placeholder looks bad
When a story has no image, the lead renders a grey box with a cactus + "no
photo". Owner: remove it entirely rather than show an empty frame.
**Fix:** `figureHTML()` — if no image, render NOTHING (for lead too) and let the
text span the full card width. Applies to the image-error fallback as well.

## P6 — Unreachable stories are dropped, not flagged (owner decision: FLAG them)
63 selected → 54 published on 2026-07-18; the 9 without full text were silently
dropped. Owner wants them SHOWN with the `(source unreachable — headline only)`
flag instead.
**Fix:** ROUTINE_PROMPT_B — make flagging the default and dropping the rare
exception (only when the headline itself is meaningless).

## P7 — Opportunities section publishes zero (3 runs running) — SOURCES CHOSEN
Digest carries ~10 candidates/day; 0 published. Root cause CONFIRMED by testing:
the Google News query returns retrospective coverage ("India-Australia Summit
2026 concludes…") and UPSC exam-prep recaps — zero forward-dated items with a
registration link. News feeds structurally cannot supply upcoming events.
**Decision — replace with a real event pipeline (all endpoints verified live
2026-07-18, samples captured):**
1. **Unstop JSON** (primary) — `unstop.com/api/public/opportunity/search-result
   ?opportunity=hackathons|competitions&searchTerm=<AI|deep tech|drone|quantum|
   climate>` — no key, paginated, and carries a real deadline field
   (`regnRequirements.end_regn_dt`). India's dominant opportunity board.
   Needs a quality filter (prize/organizer) to exclude college quiz contests.
2. **10times.com** `/india/technology`, `/india/artificial-intelligence` —
   scrape the `data-name`/`data-url`/`data-date` attributes (no JS needed).
   National conferences/expos (DataHack Summit, India Drone Tech Expo).
   NOTE: city pages (`/jaipur-in`) are Cloudflare-403; only category pages work.
3. **Lu.ma** `new-delhi`, `mumbai`, `bengaluru` — parse the embedded
   `__NEXT_DATA__` JSON for the local Delhi/NCR bucket. No Jaipur page exists.
4. **Devpost API** for the global hackathon bucket — REQUIRES a desktop Chrome
   UA (403s otherwise).
Dropped: Opportunity Desk / Opportunities For Youth (real RSS but ~90% Africa/
global-development noise). Google News demoted to discovery-only — never the
sole source for a published opportunity.
**Caveat:** 3 of 4 are undocumented/reverse-engineered endpoints — they WILL
break eventually. Each needs graceful degradation + a line in the feed audit so
breakage is visible instead of silent (this is exactly how the zero-opportunity
days went unnoticed).
Untested/inconclusive, do NOT wire without a follow-up: HackerEarth API,
Devfolio API, Startup India / iDEX / BIRAC / DST notifications, Nasscom, TiE.

## P8 — "Also worth knowing" rail unused (0 items)
Routine A never populates `also`. Decision: KEEP the feature, make A populate it,
and EXCLUDE also-ids from the full-text fetch (a one-liner doesn't need a whole
article) — cheap breadth.

## P9 — Routine A over-selects
Selected 63 ids vs the 25-40 target, which would put ~70k tokens in front of the
writer every turn. Hard cap of 45 added in cf0f5e9 — VERIFY it holds next run.

## P10 — Minor / watch
- `feeds.hbr.org` still fails with an SSL EOF (only remaining feed failure).
- `claude/*` branches regenerate every run (2/day under Option B) — auto-prune
  workflow would keep the repo tidy.
- Only 31/54 summaries contained a number — expected to improve with full text
  (P3); re-measure after the next run before treating it as a defect.
- GITHUB_TOKEN.local.md still holds a live PAT — revoke.

---

# Batch A — P1, P2, P7 fixed (2026-07-18)

All three implemented and verified with a live run (real network, real feeds,
real gh-pages). Nothing committed/pushed — left staged for review.

## P1 — cross-day dedup rebuilt on published editions
`scripts/build_digest.py`: added `load_published_urls()` (HTTP GET
`.../editions/index.json` then each in-window `.../editions/<date>.json`,
8s timeout, every failure caught and logged, never raises) alongside the
existing `load_recent_used()` (drafts/*.json, kept as an additional local-run
source, not replaced). `shortlist_section()` now returns a separate
`cross_day_dropped` count so the cross-day-specific drop is visible instead of
folded into the general dropped total.
**Test result (live, 2026-07-18):** fetched 2/2 in-window published editions
from `https://amanbeni.github.io/daily-cactus/`, collected 87 urls / 95
title-keys, and **45 candidates** were dropped as cross-day repeats across
sections (AI 5, Deep Tech 6, Climate 5, Health Tech 3, Agritech 4, Indian
Startups 6, India Deep Tech 1, Global Econ 3, World 6, Other Interests 4,
Longform 2). Confirmed exclusion on a specific URL: TechCrunch's
"Agility Robotics plants its flag in Tesla's backyard" appeared as a fresh
candidate in today's raw fetch (it ran in the 2026-07-17 edition) and is
absent from `feeds/refs/2026-07-18.json` after the fix.

## P2 — lying feed dates + relative-date resolution (all 5 layers)
- **(a)** Added a "never resolve a relative date" hard rule to both
  `ROUTINE_PROMPT.md` and `ROUTINE_PROMPT_B.md` (under Hard rules): quote or
  attribute relative phrasing ("this month" etc.), never convert it to a
  calendar date, and never trust a feed's `published` field to do that
  resolution.
- **(b)** `scripts/enrich_shortlist.py`: added `extract_article_date()` —
  tries `<meta property="article:published_time">` (+ variants), JSON-LD
  `datePublished`, `<time datetime>`, then a visible byline regex ("February
  14, 2026" / "14 Feb 2026"). Wired through a new `fetch_extract_and_date()`
  (one HTML fetch, both outputs) so `enrich_sections()` sets `article_date` on
  each story without an extra network round-trip; `fetch_extract()` is kept
  as a thin back-compat wrapper (fetch_selected.py's import is unaffected).
- **(c)** `scripts/build_digest.py`: added `apply_article_date_corrections()`
  — when `article_date` disagrees with the feed's `published` by >7 days,
  re-applies the section's own max-age cutoff against the ARTICLE date and
  drops the story if stale, logging `stale-by-article-date: <title>`.
- **(d)** Added `DISTRUSTED_SOURCE_DOMAINS = {"newsonair.gov.in"}` (a
  clearly-commented, easy-to-extend constant). A distrusted-source story with
  no recoverable `article_date` AND relative-date phrasing in its extract
  (`_RELATIVE_DATE_RE`: "this month", "next week", "today", etc.) is dropped.
- **(e)** Added `event_signature()` — clusters same-event candidates whose
  titles share a quoted phrase or a common non-generic Title-Case word-run
  (headline verbs like "Kicks/Raises/Closes" are stoplisted so they don't
  pollute the signature), and caps survivors to `EVENT_CAP_PER_SECTION = 2`
  per section in `shortlist_section()`.

**Acceptance test (the summit URL, live fetch):** ran
`https://newsonair.gov.in/india-ai-impact-summit-2026-will-mark-historic-milestone-in-global-cooperation-on-artificial-intelligence/`
through `fetch_extract_and_date()` + `apply_article_date_corrections()`.
Contrary to this backlog's original diagnosis, the page DOES carry a visible
byline ("February 14, 2026 12:19 PM") that the byline regex recovers — so
layer (b)+(c) alone catches it: `article_date=2026-02-14` vs. a simulated
lying `published=2026-07-18` feed date → **154 days apart → DROPPED**
(`stale-by-article-date`). Layer (d) remains as a defense for sources with
truly no visible byline. This was independently confirmed on TODAY'S live run
(not a simulation): a *different* newsonair.gov.in story about the same
summit — "Top world leaders to attend India-AI Impact Summit in New Delhi
next week" — was caught and dropped the same way (article 2026-02-14 vs. feed
2026-07-17, 153 days apart), plus 3 unrelated lying-date catches on other
sources (a 2006 Sun Pharma page, a 2023 Orbbec story, a 2021 op-ed), all
re-dated fresh by their feeds. 120/156 shortlisted stories got a recovered
`article_date` this run; 5 were dropped by the correction pass.
**Prompt rule test:** confirmed present verbatim in both `ROUTINE_PROMPT.md`
and `ROUTINE_PROMPT_B.md` ("Never resolve a relative date...").
**Clustering test (synthetic):** 4 differently-worded headlines about one
event ("India-AI Impact Summit Kicks Off...", "...PM Modi Inaugurates...",
"...Day 2...Focuses on AI Safety", "...Closes With AI Commons Proposal") all
resolved to the same signature (`india ai impact summit`) and were capped to
2 survivors; two unrelated stories in the same batch ("OpenAI Raises $40B...",
"Sarvam AI Becomes Newest Unicorn...") got distinct (or no) signatures and
were both kept untouched — 6 in, 4 out, no unrelated story collapsed.

## P7 — opportunities: real event sources
Built `scripts/fetch_opportunities.py` querying Unstop (JSON API, hackathons +
competitions × 6 search terms, with a prize/organizer/domain-title quality
filter to cut college-quiz noise), 10times.com (`/india/technology` +
`/india/artificial-intelligence`, parsed by bounding each `event-card
event_<id>` block so an unrelated "related events" widget on the same page
can't leak in), Lu.ma (`new-delhi`/`mumbai`/`bengaluru`, parsing the
`__NEXT_DATA__` JSON), and Devpost (JSON API, requires a desktop Chrome UA).
Each source is wrapped independently (one crash never blocks another) and
prints its own fetched/kept counts. `build_digest.py`
(`merge_opportunities_feed()`) folds `feeds/opportunities.json` into the
`opportunities` section's candidates with the same shape as every other
candidate, and `_opportunity_is_past()` now drops anything whose structured
`event_date`/`deadline` (ISO, from Unstop/10times/Luma) is in the past —
falling back to the old title-regex guess only for legacy Google-News-sourced
items with no structured date. `sources.yaml`'s Opportunities section: 4 of 5
Google News queries removed; one kept, clearly commented discovery-only
(catches named literary/cultural festivals none of the 4 structured sources
cover) and never authoritative. Added a `fetch_opportunities` step to
`.github/workflows/fetch.yml` before `build_digest.py` (non-blocking:
`|| echo ... continuing`), and added `feeds/opportunities.json` to that
workflow's `git add` list.
**Test result (live, 2026-07-18):** unstop 332 fetched / 245 kept, 10times 80
fetched / 80 kept, luma 51 fetched / 51 kept, devpost 9 fetched / 9 kept — 385
total candidates merged. After build_digest's real-date drop + dedup + rank,
**10 opportunities survived into today's digest** (0 three runs running
before this fix), all forward-dated. 5 samples with links:
- *Orchestrix: Newgen x AI Club IITM* (Unstop, IIT Madras, register by
  2026-08-16) — https://unstop.com/hackathons/orchestrix-newgen-x-ai-club-iitm-iit-madras-1717439
- *NERDS 1.0 — India's First AI Hackathon For Schools* (Unstop, register by
  2026-07-31) — https://unstop.com/hackathons/nerds-10-indias-first-ai-hackathon-for-schools-kokos-ai-pvt-ltd-1718104
- *AI Revolution Summit — India* (10times, runs 2026-08-13) —
  https://10times.com/e1g0-f2z3-p7sh-h-ai-revolution-summit
- *Fireside Chat with Thejo Kote (Founder of Journal, Airbase & Automatic) at
  SPC India* (Luma Bengaluru, 2026-07-22) — local Luma event URL (slug-based,
  omitted here for length; present in feeds/opportunities.json)
- *IEEE ClimateChain Global Hackathon* (Devpost, Oct 05–25 2026) — Devpost
  hackathon URL (present in feeds/opportunities.json)

## Verification notes / caveats
- Full pipeline re-run live end-to-end (`fetch_feeds.py` → `fetch_
  opportunities.py` → `build_digest.py`) with real network; no crashes, no
  tracebacks, `tests/test_pipeline.py` still 16/16 passing after all changes.
- Unstop/10times/Luma are undocumented/reverse-engineered endpoints (flagged
  in their own docstrings) — WILL break eventually; each fails independently
  and logs its own counts so breakage is visible, per the brief.
- Not independently re-verified: whether the *exact* summit URL used for the
  acceptance test is still in any live RSS feed today (it wasn't — it's aged
  out of the feed windows) — the acceptance test therefore fed that URL
  through the enrichment+correction functions directly rather than via a full
  pipeline run; a *different* story about the same event WAS caught by the
  full live pipeline run today (see P2 test result above), which is the
  stronger evidence.
- `ROUTINE_PROMPT_A.md` (Option B's Stage-1 selector prompt) was NOT touched —
  the brief scoped the relative-date rule to `ROUTINE_PROMPT.md` and
  `ROUTINE_PROMPT_B.md` only.

---

## Batch B — DONE (2026-07-19), verified in-browser

**P4 layout (fixed).** `.wrap` was a hard `max-width:1060px`; now
`min(1600px,94vw)` with fluid padding. Grid is 1 col (<900px) → 2 col
(900-1400) → 3 col (>1400). The lead spans the full row and now stacks at
899px (was 720px) so it never sits in a cramped split.
Readability guard, MEASURED not assumed: a first attempt capped the image-less
lead at `78ch`, which rendered ~96 real characters per line at 1920px — Georgia's
`ch` (width of "0") is ~1.23x its average glyph width. Retuned to `58ch` = **73
real chars**. Section cards measure **58 chars**. Both inside the 66-75 target.
Verified: 1920px → 3 cols, 1440 → 3, 1100 → 2, 375 → 1; **no horizontal scroll
at any width**; wrap maxes at 1600px.

**P5 empty photo frame (fixed).** `figureHTML()` now renders NOTHING when a
story has no image (lead included), and `imgErr()` REMOVES a broken figure
instead of swapping in the grey "no photo" cactus box. An image-less lead gets
`.no-figure` and reflows to a single full-width text column. The cactus SVG is
retained for the masthead/colophon only. Verified: **0 empty figures** on both
the fixture and the real 2026-07-18 edition (54 stories).

**P6 unreachable flags (renderer done).** `summaryHTML()` detects the trailing
provenance flags and wraps them in `<span class="src-flag">` (muted + italic).
Escape-first-then-wrap, so it cannot inject markup. Unit-tested: both flags
wrap; `"Apple beats Nvidia (NYSE: AAPL)"` is NOT falsely flagged; an
`<img onerror=...>` payload is neutralised. Renders 2/2 flags in the fixture.
NOTE: the prompt side of P6 (make flagging the default, dropping the exception)
was already deployed earlier; the renderer now styles those flags properly.

**Back-compat:** the real published 2026-07-18 edition renders cleanly at 1440px
— 54 stories, 3 columns, 0 empty figures, no console errors.

## Batch C — DONE (2026-07-19)

**P8 also-rail (fixed, both ends).** (1) `fetch_selected.collect_ids()` now
returns `(ids, lite_ids)`: an id that appears ONLY in an `also` rail is "lite"
and SKIPS the full-article fetch — it becomes a one-liner, so a whole article
was pure waste of bandwidth and writer context. It still gets an entry from the
digest snippet. An id that is also a full card anywhere keeps its full fetch
(unit-tested). (2) `ROUTINE_PROMPT_A.md` now pushes the rail explicitly — it
returned EMPTY for every section on 2026-07-18, defeating its purpose; sections
with more worthwhile stories than card slots must put the remainder here rather
than drop them.

**P10 branch pile-up (fixed).** New `.github/workflows/prune_branches.yml`:
weekly (Sun 08:00 IST) + manual, deletes `claude/*` branches with no commit in
the last 7 days. Only ever matches `claude/*`; main and gh-pages are explicitly
protected; age is judged from the branch tip's commit date so an in-flight run
is never touched. Editions on gh-pages are immutable and independent of these
branches, so nothing of value is lost.

**P10 HBR feed** — left as-is, already labelled `[FLAKY]`. It is a real,
intermittent upstream SSL fault (not our bug) and the only remaining feed
failure; it degrades gracefully.

**Still open / to verify on the next run:**
- P3: confirm full-article text now reaches the writer (the Databricks-style
  "why" should appear). The select.yml push bug was fixed in cf0f5e9 but has
  not yet been exercised by a real run.
- P9: confirm Routine A honours the 45-id cap (it picked 63 on 2026-07-18).
- Re-measure the share of summaries containing a number (was 31/54) now that
  full text should be arriving.
