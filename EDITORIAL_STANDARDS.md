# Editorial standards — The Daily Cactus (v7, 24 Sep 2026)

Humans only; the routines get the short version in `CLAUDE.md` ("Editorial
contract"). This file is the evidence and reasoning behind it.

## 1. How the paper decides what matters

Adapted from the newsroom frameworks (Galtung & Ruge 1965; Harcup & O'Neill
2001/2016; AP and Reuters news-value guidance; NYT page-one practice) to one
reader: an AI-generalist / Chief-of-Staff candidate in Jaipur whose priorities
are AI > Indian startups > India deep tech > climate, economy, world.

| Dimension | 0 | 3 |
|---|---|---|
| Impact | trivial | changes things for a country, a market or the reader's core beat |
| Fit | off-beat | hits 2+ of the reader's domains |
| Novelty | rehash | changes what an informed reader believes |
| Consequence | no decision changes | changes a decision the reader (or his future employer) makes |
| Substance | reads like a press release | numbers + independent named sources |
| India lens | none | India is the story or is materially affected |

**Hard rejects:** paid/sponsored copy; a repeat with no new fact; MoU / award /
"aims to" / "vision" pieces; conference quote-pieces; listicles and weekly
roundups as front-page news; single-day market blips with no cause.
**Lead:** highest impact × fit × novelty among stories that pass the rejects.
**Follow-ups:** a developing story earns a slot again only with a new fact
(verdict, number, reversal) — never for continued interest; one storyline gets
at most one front-page slot a day.

## 2. Spotting PR and "churnalism" (implemented in `scripts/editorial.py`)

Ranked by precision. Tier 1 is dropped; tiers 2-3 are demoted and shown to the
selector as `flags`.

- **Tier 1 — paid content:** URL/label markers `brand-connect`, `spotlight`,
  `partner-content`, `sponsored`, `advertorial`, `impact-feature`, "Consumer
  Connect Initiative", "provided by NewsVoir/PNN". (India markers verified via
  Newslaundry's investigations of ANI syndication and disguised advertorials.)
- **Tier 2 — the source is the company or a PR/stock-tip mill:** PR wires,
  company newsrooms and vendor blogs, TradingView/simplywall.st/Barchart-style
  stock explainers, UPSC-prep sites, NGO grant listings, social posts.
- **Tier 3 — lexical:** "today announced", "pleased/thrilled to announce",
  "leading provider", superlative density, "about the company", "forward-looking
  statements", "(ANI)/(IANS)", MoU/award/"recognised as", and an intent headline
  ("aims to / plans to") with no number.
- **Format:** weekly roundups, trackers, "The Download", digests, opinion and
  editorials, machine-summarised copy, routine corporate filings.

Legit announcements stay: a round that closed (amount + investors), audited
results, a launch with price/specs, a regulator's order.

## 3. What a good morning briefing does (Axios, Semafor, NYT The Morning, Techmeme)
Finite and short; one clear lead; numbers before narrative; fact separated from
take (the Editor's Read); developing stories only with "what changed"; breadth
through one-liners instead of more full cards; a stable, recognisable structure.
Hence the 24-card budget and the `also` rail.

## 4. The v7 audit (24 Aug – 23 Sep 2026) — what was wrong and what changed

| Finding (measured) | Fix |
|---|---|
| Cross-day dedup read the pre-rename Pages URL, 404 from 27 Aug: 156 repeated items (3 before), lead repeated 3 times | Repo-derived URL + read editions from the gh-pages checkout; same-story memory labels `seen`; `DEDUP_BROKEN` opens an issue |
| Writer routine on a stale prompt all month: every card a paragraph, chopped by a splitter that broke on "CM N." / "Capt." | Contract in CLAUDE.md (auto-loaded); abbreviation-safe splitter; stale prompt now opens an issue |
| 24% of selected stories had no body text (Reuters 40/40, Bloomberg 49/49, YourStory, NDTV) | Recovery chain in fetch_selected: reader proxy → same story from an allow-listed reputable outlet → snippet (8 of 18 test failures recovered) |
| Opportunities ranked by post date; real dated events never shortlisted | `score_opportunity`: when, where (Jaipur/NCR/India/online), topic, no student fests/NGO grants |
| Section misfits: 441 in the month's shortlists | `section_fit` (India sections need an Indian actor; Agritech needs farming): 99 in replay |
| PR-flavoured shortlist items: 338 | `pr_cues` + learned source yield: 127 in replay |
| Key stories cut before the selector (Skyroot Vikram-1 on 24 of 26 days, DRDO 14 of 16) | whole-word, reader-tiered interest terms ('ai' used to match 'said'); two-pass source cap: shortlisted 17/26 and 11/16 in replay |
| Filler signals ("worth watching" 40+ in 12 editions), invented commentary on headline-only cards, Editor's Read beyond the top 3 | Copy-desk guardrails in assemble_edition |
| 21 Sep: no paper, no alert | `watchdog.yml` at 14:13 IST |
| Coverage gaps: geopolitics 22% of top stories, India business/markets and careers/visas had no home | Economy & Markets, World & Geopolitics, new Work & Careers; BYB reroutes India business/careers |
| 6 dead feeds; no Indian business papers; thin defence/space/semis | 6 dead removed, 20 verified feeds added (Entrackr, ET Startups, Mint, BusinessLine, MediaNama, SpaceNews, EE Times, Breaking Defense, ThePrint Defence, Livefist, Mercom, Heatmap, The Diplomat, Al Jazeera, Semafor, Science, ET HealthWorld, Poets&Quants, Simon Willison, The Verge AI) |
| ~6,500 words / 28-minute paper | 24-full-card budget, breadth via one-liners |

## 5. How stories are picked (v8, 24 Sep 2026)

Evidence: News Minimalist's LLM significance scoring (scale, impact, novelty,
potential, credibility; 0-10), Techmeme and Google News (cross-outlet coverage and
source authority as prominence), Hacker News time decay, and an arXiv 2025 study
(2509.25491) showing LLM newsworthiness scores agree with editors within +-1 but
not exactly, so scores should cut and order, and code should enforce the rules.

1. **Code: prominence and hygiene** (free): independent-outlet count, recency,
   learned source authority, 7-day memory, PR/format/section-fit flags, diversity caps.
2. **Editor model: score everything** — Scale, Novelty, Consequence, Fit (0-4) +
   a 10-word "why", for every candidate (the scores are saved next to the
   selection, so picks can be audited and tuned).
3. **Lead by head-to-head** between the top six, in both orders: "which would most
   change what this reader believes or does this week?"
4. **Code enforces** the budget (24 full cards), front-page caps, no repeats
   across places, and "never lead with a story we could not read".

Guards against the known biases: loudness (buzz is evidence, not importance),
recency (structural beats breaking), PR, Anglophone skew (India lens raises Fit),
sameness (one storyline per front-page slot).

## 6. Sources (117 feeds, all live-checked 23-24 Sep 2026)
Each beat now carries its best global outlets alongside Indian ones: AI (MIT TR,
Bloomberg Tech, FT Tech, Reuters tech, Ars AI, The Decoder, Wired, NYT Tech, The
Verge), startups (Inc42, Entrackr, ET Startups, TechCrunch Venture, Crunchbase
News, Sifted, Rest of World), deep tech (IEEE, SpaceNews, Payload, EE Times,
Breaking Defense, Defense News, ThePrint Defence, Livefist), climate (Carbon
Brief, Canary, Heatmap, Inside Climate News, Grist, Mercom), health (STAT,
Endpoints, Fierce Biotech, NYT Health, ET HealthWorld), agri (AgFunderNews,
Reuters agri wire), economy (Bloomberg, FT, Economist, NYT Business, Reuters
business, Mint, BusinessLine, Project Syndicate), world (Reuters, AP, NYT, BBC,
Guardian, DW, Al Jazeera, SCMP, The Diplomat, Semafor, FT World), careers
(Poets&Quants, Fast Company, MIT SMR), science and culture (Science, Scientific
American, Nature, New Scientist, Guardian Books/Music).
Blocked or stale when checked (skipped): Business Standard, WSJ feeds, Nikkei,
VentureBeat, Tech in Asia, Civil Eats, IMF blog, HBR, Business Insider.
