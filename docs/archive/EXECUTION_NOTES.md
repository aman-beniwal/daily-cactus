# Execution Notes — v2 Plan, Phases 0-2

> Written by the executing agent. Humans/reviewers only — not loaded by the routine.

## Phase 0 — sync & diff summary

Diffed this working copy against the read-only clone of the live repo
(`amanbeni/daily-cactus`, `main`, clean at commit `3f2e14e`).

- **Scripts, `sources.yaml`, `ROUTINE_PROMPT.md`, `CLAUDE.md` all differed** —
  local copies were newer (Jul 2026 B9 breadth raise: caps up, India Deep Tech
  section, Down To Earth agri feed) and were kept as the base per instructions.
- **`.github/workflows/fetch.yml` and `publish.yml` were byte-identical.**
  `automerge.yml` exists on the live repo but NOT locally — see "Open
  questions" below; recommend deletion, per V2_PLAN's own note, but I have no
  git/push access to act on it from this local copy.
- **Real data copied in from the live clone** for testing: `drafts/*.json`
  (11 real drafts, 2026-06-24 through 2026-07-10), `feeds/latest.json`
  (was a 236-byte placeholder locally). `feeds/digest.json`/`refs.json` were
  live placeholders too (99/2 bytes) — not copied in as-is since the new
  scripts regenerate them with a different schema; used as scratch reference
  then deleted.
- **Confirmed the vanishing-editions bug by inspection** (no Actions log
  access from this sandbox, so confirmed via code + a live regression test
  instead — see "Regression test" below): `assemble_edition.py` re-processed
  ALL `drafts/*.json` every run against a single daily-overwritten
  `feeds/refs.json` keyed by positional ids (`ai-1`, `ai-2`...). Running the
  OLD assemble logic against the 11 real old drafts with only today's refs
  produced 0 resolved story slots for every one of them — i.e. every past
  edition would have been gutted on the next publish. This is now fixed (A1-A3).
- **Routine schedule:** could not confirm manual vs. scheduled from this
  sandbox (no GitHub UI/API access) — HANDOFF.md says manual; ask the owner
  to confirm, no code change needed either way.
- **`site/editions/2026-06-18.json`** (already present locally, pre-dates this
  work) contains fabricated `example.com` URLs, confirming the plan's
  diagnosis #5. Left untouched per the "do not touch existing
  site/editions/*.json" guardrail; `scripts/recover_editions.py` is written
  to fix this for real once pointed at the actual gh-pages checkout (a dry
  run against a mocked repo is included below — a real run against gh-pages
  is owner/Actions work, not something I can do from this sandbox).

## Environment notes (local testing only, not a pipeline change)
This machine's Python 3.14 framework build has no CA bundle wired up by
default (`SSL_CERTIFICATE_VERIFY_FAILED` on every `https://` call, `feedparser`
included). Installed `certifi` and used `SSL_CERT_FILE=$(python3 -c "import
certifi; print(certifi.where())")` for local test runs; also wired `certifi`
as a first-choice SSL context (falling back to the interpreter default) into
`fetch_markets.py` and `enrich_shortlist.py`'s urllib fallback so the same
fix travels with the code. GitHub Actions runners ship with a working CA
store, so this should be a non-issue in CI — flagging so it isn't mistaken
for a real bug if it resurfaces in another local dev environment.

## Phase 1 — status

| Item | Status | Notes |
|---|---|---|
| A1 content-hash ids | **Done** | `build_digest.py::make_id` — `slug-sha1(url)[:10]`. Unit-tested (deterministic, section-prefixed, collision-free across URLs) in `tests/test_pipeline.py`. |
| A2 per-date refs snapshots | **Done** | `feeds/refs/YYYY-MM-DD.json` written each build; `assemble_edition.py::load_refs_for_date` resolves own-date first, falls back to the union of all snapshots, then legacy `feeds/refs.json`. Pruned to 30 days in `build_digest.py::prune_old_refs` (runs inside build_digest, not a separate workflow step — simpler, same effect). |
| A3 immutable editions | **Done** | `assemble_edition.py` now takes `--force [DATE... | all]`; skips any draft whose date already has a `site/editions/<date>.json` per `EXISTING_EDITIONS_DIR` (env-overridable; workflow checks out gh-pages' `editions/` into `existing_editions/` first). **Tested**: mocked an `existing_editions/editions/` with 11 "already published" dates → all 11 skipped, only the new draft assembled; `--force <date>` correctly rebuilt one on demand and left the rest immutable. |
| A4 cross-day dedup | **Done** | `build_digest.py::load_recent_used` reads the last 7 days of `drafts/*.json`, resolves each id against that date's refs snapshot, and drops matching URLs/title-keys before shortlisting. **Tested**: planted a synthetic "used yesterday" story (real hash id + matching refs snapshot) and confirmed it was excluded from today's digest on rebuild, with another candidate backfilling the slot. NOTE: this only works going forward — the 11 real old drafts predate the refs snapshot directory, so their ids don't resolve and they contribute nothing to the cross-day-used set yet. That's expected, not a bug. |
| A5 date hygiene | **Done** | (a) `fetch_feeds.py` now drops undated Google News items outright (they bypassed the recency cutoff entirely before); undated items from trusted feeds are merely penalized (score, not dropped) — see `build_digest.py::score`. (b) Opportunities: `extract_event_date` regex-parses a month+day (+optional year) from title/summary and hard-drops events whose date is in the past — unit-tested for both past and future dates and a couple of formats. It's a heuristic (documented limitation below). (c) Per-section `max_age_hours` hard cutoff added to `score()` (returns `-inf`, filtered out) using `window_hours` now carried through from `sources.yaml` via `fetch_feeds.py`'s output (previously silently dropped). |
| A6 health signal + recovery | **Done** | `fetch.yml`/`publish.yml` open a `health`-labeled GitHub issue via `actions/github-script` on fetch failure, digest-build failure, an empty digest (`build_digest.py` prints a `DIGEST_EMPTY` marker the workflow greps for), or an assemble failure. `scripts/recover_editions.py` walks `git log --follow --diff-filter=A` per edition file to find its first-published commit and restore that content; dry-run by default, `--apply` to write. **Unit-tested against a mocked git repo** (3 commits simulating exactly the described bug: day-1 and day-2 editions correctly published, then a day-3 publish gutting both) — dry run correctly identified 2 restorable editions, `--apply` correctly restored their original content byte-for-byte, and correctly recognized the untouched day-3 edition needed no change. Running it for real against the live gh-pages branch is owner/Actions work (out of this sandbox's reach — no push access, no live git remote here). |

## Phase 2 — status

| Item | Status | Notes |
|---|---|---|
| B1 full-text enrichment | **Done** | `scripts/enrich_shortlist.py`, called from `build_digest.py` after shortlisting (never before — only ~148 survivors get fetched, not the ~500 raw). `trafilatura.fetch_url`/`extract` first, `readability-lxml` + a tag-stripper fallback, degrades to the existing RSS `summary` on any failure (never blocks). Capped at 700 chars/extract, 8s/request, 240s total budget. `trim_digest_to_budget` shaves extracts if the whole digest would exceed the ~25k-token heuristic. **Tested live**: 45/148 stories got a real extract, 103 fell back cleanly, one run took ~2m40s (well inside a GitHub Actions job budget). |
| B2 ranking v2 | **Done** | `compute_buzz` counts title-key clusters across ALL sections before any dedup; carried into the digest as `buzz: N` (only when >1) and into `score()` as a capped bonus (`min(buzz-1,4)*0.8`, unit-tested). Small hand-set `SOURCE_WEIGHTS` map added (Economist/Reuters/Bloomberg/etc. nudged up; bare "Google News" nudged down since it's an aggregator listing, not an original outlet). Section tier weights (Tier 1/2/3 sizing) were NOT added as a separate mechanism — `max_stories`/`SECTION_HARD_CAP` per section in `sources.yaml` already encodes this (AI/Startups/Deep Tech/Opportunities sized larger than Science/Health/Music), so a second tier-weight layer looked redundant; flagging as a judgment call for the reviewer rather than silently deciding it wasn't wanted. |
| B3 prompt overhaul | **Done** | `ROUTINE_PROMPT.md` rewritten: 2 reads (digest + TASTE.md), numbers-first summary rule, chart-mention rule, `editors_read` restricted to lead + top-2 frontpage, `key_stat`, `brief` (8-12 one-liners with ids), per-section `also` rail (2-4 one-liners), longform Saturday note, all thin-day/no-repeat/no-past-event rules carried over. Grew from 6,776 → 8,186 bytes (~21% longer) to document the new fields — flagged since the plan said "keep it roughly its current length"; I judged documenting 4 new schema fields clearly was worth the size over compressing it back down and risking the model missing a rule. Reviewer call if this needs trimming further. |
| B4 TASTE.md + feedback loop | **Done** | `TASTE.md` seeded (33 lines) from HANDOFF.md/V2_PLAN.md owner-preference notes. `scripts/fold_taste.py`: zero-model heuristic, tallies weekly 👍/👎 events by section (derived from the story id's slug prefix), writes at most 5 auto-generated lines into a clearly-delimited `<!-- AUTO-FEEDBACK:START/END -->` block that's REPLACED (not appended to) each run. **Tested**: fed synthetic vote batches twice and confirmed the second run's output replaced the first's, not stacked. `.github/workflows/taste.yml` runs weekly (Sunday), collects `feedback`-labeled issues via `actions/github-script`, folds, commits, closes the issues. NOTE: the renderer-side 👍/👎 issue links are Phase 3 (site/app.js) — out of my scope; `taste.yml`'s issue-title parser assumes the convention documented in its own comment (`👍 <story-id>` / `👎 <story-id>`) which the Phase-3 implementer needs to match. |
| B5 markets | **Done** | `scripts/fetch_markets.py`. **stooq.com's CSV endpoint returned "page does not exist" for every symbol I tried from this sandbox** (indices, FX, futures) — used Yahoo Finance's public, unauthenticated `query1.finance.yahoo.com/v8/finance/chart/<symbol>` JSON endpoint instead (the same one `yfinance` wraps; hit directly via stdlib `urllib` to skip the dependency). All 7 instruments (Nifty 50, Sensex, USD/INR, Brent, BTC, Gold, Silver) fetched successfully with live 1-day % change. Gold/Silver reported as **USD spot ($/oz, COMEX futures)**, not Indian ₹/10g convention — I didn't find a clean free INR-bullion source and didn't want to fabricate a shaky troy-oz→gram→"Indian retail price" conversion without a real premium/duty adjustment; this is the plan's own documented fallback ("otherwise USD spot with a clear label"), but flagging as a judgment call in case the owner has a specific INR bullion source in mind. `assemble_edition.py` injects `feeds/markets.json` verbatim as `edition.markets` (optional field). |
| B6 weekend longform | **Done** | Verified all 4 candidate feeds parse (Aeon, Guardian Long Read, Longreads, Nautilus — none dropped). Added as a `weekend_only: true` section in `sources.yaml`; `fetch_feeds.py` now carries `weekend_only` through to `latest.json` (it wasn't being carried through before — a gap I found and fixed while wiring this up, since without it the gate in `build_digest.py` would have silently done nothing); `build_digest.py::main` computes IST weekday and filters weekend-only sections out on any day but Saturday. **Tested**: simulated both a Tuesday (excluded) and a Saturday (included) date and confirmed the gate. |
| B8 source audit | **Done** | `fetch_feeds.py` now carries each entry's originating `feed_url`; `build_digest.py` tallies fetched-vs-shortlisted per feed URL and appends to `feeds/feed_stats.jsonl` every run (never overwritten, accumulates). `scripts/audit_feeds.py` aggregates the last N days into a markdown table + flags 0%-hit-rate feeds with ≥10 samples as prune candidates. `.github/workflows/audit.yml` runs monthly, posts the table as an `audit`-labeled issue. **Tested** against real feed_stats.jsonl rows generated during this session's test runs. |
| B7 source repairs (folded into B8 audit while I was in `sources.yaml`) | **Partial** | Live-tested every feed: confirmed dead — `analyticsindiamag.com/feed` (XML parse failure) and `entrackr.com/feed` (404) — both removed. Confirmed **still working, contrary to HANDOFF's old notes** — `importai.substack.com` and `the-ken.com/feed` — both kept. Added **Rest of World** (`restofworld.org/feed/latest/`) to Indian Startups for breadth (verified working). Did NOT add Semafor/Mint/Ben's Bites: Ben's Bites' documented feed URL 404s (would need finding the correct one — didn't want to guess), Moneycontrol's tech RSS 403s from this sandbox (may work from an Actions runner — untested), Mint's tech feed works but I stopped at one addition per the plan's "no net explosion of queries." Remaining live parse failures (feed-side XML malformation, not dead feeds) not fixed: Down To Earth agriculture, Inc42 deeptech vertical, The Economist finance RSS, Nature.rss, HBR (SSL — may be a sandbox-only transient, worth re-checking from Actions). |

## Test results (actual numbers, most recent full local run)
- `python3 tests/test_pipeline.py`: **16/16 checks pass** (hash-id stability, max-age hard cutoff, undated penalty-not-drop, event-date extraction past/future/format/no-match, title-key dedup matching, buzz cap).
- `python3 scripts/fetch_feeds.py`: **56/61 feeds ok** (91.8%), 537 raw entries, 5 failures (all feed-side XML issues, see B7 table above — down from 7 failures/58 feeds before the B7 fixes).
- `python3 scripts/build_digest.py`: **148 candidates** across 13 sections (Remainder is intentionally empty — no feeds assigned), **~17,392 estimated tokens** (chars/4 heuristic) — well under the plan's ~25k digest budget and the ≤350k/run routine ceiling. Enrichment: 45/148 (30%) got a real full-text extract, 103 degraded cleanly to the RSS summary, 0 skipped on the time budget. Run time ~2m40s.
- `python3 scripts/assemble_edition.py`: immutability verified — with a mocked "11 dates already published" `existing_editions/`, all 11 were skipped and only a new draft was assembled; `--force <date>` correctly rebuilt exactly the requested date.
- `python3 scripts/recover_editions.py --repo <mock-ghpages> --apply`: correctly restored 2 gutted editions to their original first-published content, left a genuinely-current one alone.
- `python3 scripts/fetch_markets.py`: **7/7 quotes fetched** (Nifty 50, Sensex, USD/INR, Brent, BTC, Gold, Silver), each with a computed 1-day % change.
- No `example.com` URLs anywhere except the one known pre-existing `site/editions/2026-06-18.json` (untouched per guardrail — fix is `recover_editions.py`, run for real once pointed at gh-pages).

## Things needing owner action (I have no access to do these from this sandbox)
1. **Push these changes** to a branch and open a PR (or push directly, owner's call) — this sandbox has no git remote/push access.
2. **`automerge.yml`**: exists on the live repo, not locally. V2_PLAN itself flags this as needing a yes/no from the owner before deletion — I left it alone.
3. **Run `scripts/recover_editions.py --repo <real gh-pages checkout> --apply`** for real once Phase 1 is deployed, to actually fix the gutted past editions and the `example.com` edition (or confirm the latter has no earlier good commit and should just be deleted — the script will tell you which).
4. **Secrets/permissions**: `fetch.yml`/`publish.yml`/`taste.yml`/`audit.yml` all now request `issues: write` (previously only `contents: write`) for the A6 health checks and B4/B8 issue automation — confirm the repo's default `GITHUB_TOKEN` permissions allow this (Settings → Actions → Workflow permissions), or the health/audit/taste-fold steps will silently fail.
5. **Swap in the new `ROUTINE_PROMPT.md`** for the next scheduled/manual routine run, and confirm the routine is pointed at reading `TASTE.md` as its second file (its harness config, if any, may need updating to allow that second read — I only changed the prompt text).
6. **Gold/Silver INR convention** (B5) — if you have a specific free INR bullion source in mind, point me at it; current implementation reports clearly-labeled USD spot.

## Open questions for the reviewer
- B2's "section tier weights" — I judged the existing per-section `max_stories`/cap sizing already encodes this and didn't duplicate it as a separate score multiplier. Flag if you wanted an explicit tier-weight term in `score()` as well.
- B3 prompt length grew ~21% to document 4 new fields (`brief`, `key_stat`, `editors_read`, `also`) clearly — happy to compress if that's judged too costly for the daily prompt-cache.
- A5 event-date extraction is regex-based (month name + day, optional year) — it will miss relative phrasing ("next Tuesday", "this weekend") and non-English date formats. Flagging as a known heuristic limit, not a bug; the plan said "where possible."
- A4 cross-day dedup can't retroactively cover the 11 real pre-v2 drafts (their ids don't resolve against any refs snapshot) — only bites starting from the first post-deploy digest build. Fine for a personal paper; flagging in case that's surprising.
