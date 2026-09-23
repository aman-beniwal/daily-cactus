# The Daily Cactus 🌵 — operational guide (v7)

A self-updating personal morning newspaper for one reader (Aman, Jaipur). A daily
Claude Code Routine curates pre-fetched RSS news into a JSON draft; GitHub Actions
publishes it to GitHub Pages.

> Humans: start with `HANDBOOK.md` (setup, operations, security, status).
> Keep this file SHORT. It is auto-loaded into the routine and re-sent every turn,
> so every line here costs tokens daily. Long-form history lives in `HANDOFF.md`
> (humans only — not loaded by the routine).

## Architecture (v6 — TWO routines: select, then write)
```
fetch.yml (GitHub Actions, ~05:00 IST, full internet)
  → scripts/fetch_feeds.py   → feeds/latest.json     (raw, ~500 stories)
  → scripts/build_digest.py  → feeds/digest_lean.json (id/title/source/teaser/buzz?/img? —
                                                        no urls — ROUTINE A's only news input)
                             → feeds/refs/YYYY-MM-DD.json  (this date's id→url/image/source snapshot)
                             → feeds/refs.json        (rolling union, back-compat; no routine reads either)
  → scripts/fetch_markets.py → feeds/markets.json     (Nifty/Sensex/USD-INR/Brent/BTC/Gold/Silver)
        │
        ▼
ROUTINE A — SELECT (~05:15 IST, ROUTINE_PROMPT_A.md)
  reads feeds/digest_lean.json + TASTE.md → writes selections/<date>.json
  (ids + structure ONLY — no prose), pushes to its claude/** branch
        │
        ▼
select.yml → scripts/fetch_selected.py → feeds/selected/<date>.json
  (full article text for every selected id; commits to main.
   text_source: full | digest-extract | none — only 60-76% reach "full")
        │
        ▼
ROUTINE B — WRITE (~06:00 IST, ROUTINE_PROMPT_B.md)
  reads feeds/selected/<date>.json + TASTE.md → writes drafts/<date>.json
  (headline/hook/points[3-5]/signal/key_stat?/editors_read?/brief/also).
  ==double equals== in headline+hook renders as the yellow marker pen.
  No HTML, no web search.
        │
        ▼
publish.yml (GitHub Actions)
  scripts/assemble_edition.py: for each NEW draft only (editions are IMMUTABLE —
    a date with an existing site/editions/<date>.json on gh-pages is skipped
    unless --force <date>) resolves ids against that date's refs snapshot
    (falling back to the union of all snapshots), injects url/image/source/
    colophon/edition/markets → site/editions/YYYY-MM-DD.json. Deploys site/ →
    gh-pages, then a post-deploy job rebuilds editions/index.json on gh-pages.
        │
        ▼
GitHub Pages renderer (site/index.html + app.js + style.css, committed in repo) renders the JSON
```

**Core rules that keep cost low (do not violate):**
1. Routine A writes **only `selections/<today>.json`** (ids, no prose); Routine B
   writes **only `drafts/<today>.json`** (ids + prose). Never HTML, never
   urls/images — those are injected on Actions from the refs snapshot, which
   also makes fabricated links impossible.
2. Each routine reads **exactly 2 files** and stops. No repo listing, no extra
   file reads, no self-verify, no web search. (This is the fix for the old
   read-before-write retry loop that burned ~1.5M tokens — see `HANDOFF.md`.)
3. The renderer (`site/index.html`, `app.js`, `style.css`) is committed once and
   never regenerated.
4. **Editions are immutable once published.** `assemble_edition.py` never
   rewrites a past `site/editions/<date>.json` by default — content-hash story
   ids (not positional) plus per-date refs snapshots make this safe. This is
   the fix for the "vanishing editions" bug (see `HANDOFF.md`/`V2_PLAN.md`).
5. Keep `CLAUDE.md`, the routine prompts, and `TASTE.md` short **and frozen**
   between runs (changing them busts the prompt cache).
6. **`assemble_edition.py` must never silently delete a story.** Its wrong-link
   check WARNS; it does not drop. The old headline-overlap drop killed 6 correct
   stories in 5 editions — including a whole edition's lead — and caught nothing.
7. Section-level `also` must survive assembly into `sections[].also`; the
   renderer reads `sec.also`. Dropping it silently discarded 49 fetched
   one-liners over 5 editions.

## Files
| File | Role |
|---|---|
| `ROUTINE_PROMPT.md` | The routine's prompt. Paste as-is. Outputs a draft, not HTML. |
| `TASTE.md` | Short standing preferences (≤40 lines). Routine's 2nd read. Weekly feedback fold via `taste.yml`. |
| `sources.yaml` | Feed registry (15 sections incl. weekend Longform + Remainder). Edit by hand. |
| `scripts/fetch_feeds.py` | Fetch all feeds → `feeds/latest.json` (Actions only). |
| `scripts/build_digest.py` | Dedup/rank/shortlist/enrich → digest + refs snapshot + feed_stats (Actions only). |
| `scripts/editorial.py` | v7 news judgment: repeat memory, section fit, PR/format flags, outlet corroboration, opportunity scoring, learned source yield. |
| `.github/workflows/watchdog.yml` | 14:13 IST: opens a health issue if today's edition is missing. |
| `.github/workflows/guard.yml` | Alarm on any workflow-file change; token-expiry reminder. |
| `scripts/enrich_shortlist.py` | Full-text extraction for shortlisted stories (trafilatura/readability). |
| `scripts/fetch_markets.py` | Markets snapshot → `feeds/markets.json` (Actions only). |
| `scripts/assemble_edition.py` | Draft + refs snapshot → full edition JSON, immutable-by-default (Actions only). |
| `scripts/recover_editions.py` | One-time: restores gutted past editions from gh-pages git history. |
| `scripts/fold_taste.py` | Weekly heuristic fold of 👍/👎 issues into `TASTE.md` (no model calls). |
| `scripts/audit_feeds.py` | Monthly per-feed hit-rate report from `feeds/feed_stats.jsonl`. |
| `feeds/digest.json` | Model-facing lean candidates. Regenerated each fetch. |
| `feeds/refs/YYYY-MM-DD.json` | That date's id→url/image lookup (kept ~30 days). Model never reads it. |
| `drafts/YYYY-MM-DD.json` | The routine's daily output (IDs + prose). |
| `site/editions/YYYY-MM-DD.json` | Assembled edition the renderer reads. Immutable once published. |
| `site/editions/index.json` | Date manifest (rebuilt by a post-deploy job in `publish.yml`, on gh-pages). |
| `site/{index.html,app.js,style.css}` | The JS renderer. MUST stay committed; routine never touches it. |
| `.github/workflows/{fetch,publish,taste,audit}.yml` | The pipeline (a gh-pages-triggered manifest workflow can't run, so the manifest rebuild is folded into publish.yml). |

## v8 newsroom (built 24 Sep 2026, runs in TRIAL until switched)
`.github/workflows/newsroom.yml` runs after the fetch: `scripts/newsroom.py` makes
TWO plain model calls on the owner's subscription (`CLAUDE_CODE_OAUTH_TOKEN`):
editor (`prompts/editor.md`, scores every candidate, picks) -> full-text fetch ->
writer (`prompts/writer.md`). No agent loop, no laptop, prompts in the repo, token
usage logged to `feeds/newsroom_log.jsonl`. Repo variable `NEWSROOM_MODE`: `trial`
(writes `trial/`, viewable at `?trial=<date>`) or `live` (publishes; pause the
routines). The routines below keep running until the switch.

## Editorial contract (v7, 24 Sep 2026) — BOTH routines follow this
Applies whatever prompt is pasted in the routine; where a pasted prompt differs,
this wins. Evidence behind every rule: `EDITORIAL_STANDARDS.md` (not loaded).

**SELECT (Routine A)**
- Score each candidate on: impact (people/₹/$ affected), fit with the reader
  (AI > Indian startups > India deep tech > the rest), novelty (changes what an
  informed reader believes), consequence (changes a decision), substance
  (numbers, named independent sources), India lens. LEAD = the biggest
  impact × fit × novelty today, not the loudest.
- `seen` = already ran: re-pick ONLY for a genuinely new fact (verdict, number,
  reversal). Never the same lead two days running. One storyline = at most one
  front-page slot a day.
- `flags` = guilty until proven: skip press releases, company channels, stock
  tips, roundups/"weekly trackers", opinion — unless the release IS the event
  (round closed with amount, audited results, launch with price, regulator's
  order). Always skip MoUs, awards, "aims to/plans to", conference quotes.
- India sections need an Indian company or actor, not an Indian city name.
- READING BUDGET: at most 24 full cards (lead + front + section cards, each
  story counted once). Breadth goes into `also` one-liners: 2-4 per section.
- Opportunities: concrete date >= 2 days away; Jaipur/Delhi-NCR/online first;
  fellowships, AI/startup/policy events. No student college fests, NGO grants,
  foreign local festivals, exhibitions.

**WRITE (Routine B)**
- Every card: `hook` + `points`, never one `summary` paragraph (a pasted schema
  showing only `summary` is outdated). hook: ONE fact, <= 30 words, lead with the
  number. points: 2-4 bullets, <= 30 words, one fact each.
- Don't write a section card for a lead/front story (the page cross-links it).
- signal: 1-3 specific implications. Banned: "worth watching/tracking", "not
  personally actionable", "the real signal". Omit rather than pad; tie to the
  reader's career only when concrete.
- editors_read: lead + top two front stories only. key_stat whenever one number
  carries the story. Label vendor claims as claims ("the company says").
- text_source "none": one headline sentence + the flag, no signal. Text that
  opens "[Text below is X's report…]" is another outlet's coverage: use it.
- First-principles clarity (the reader's standard): fact → mechanism (why/how,
  one plain sentence) → consequence; every number gets a yardstick from the text
  ("up from X", "a tenth of Y"; never invented); define a specialist term once in a few words; concrete
  verbs, no "leverages/unlocks/ecosystem". Plain and precise, never dumbed down.
- Skip `brief` (the page no longer shows it). Plain punctuation, few em-dashes.

## Editorial intent
Signal over noise; numbers-first summaries; "why it matters" > "what happened";
India lens; no hype; honest about thin days (render fewer, never pad, never
web-search to fill, never a past-dated event, never a repeat of the last 7 days).
