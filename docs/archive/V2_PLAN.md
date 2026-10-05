# The Daily Cactus — v2 Plan (execution brief)

> Written 2026-07-12 after a full read of the v4 pipeline + owner discussion.
> This is the brief for the executing agent. It is self-contained: diagnosis,
> decisions already made by the owner, file-level changes, and acceptance tests.
> Owner decisions in this doc are settled — do not re-litigate them.

## Guardrails (inherited from v4 — NEVER violate)
- The daily routine does ONE read (`feeds/digest.json` + `TASTE.md`) and ONE
  write (`drafts/<today>.json`). No repo listing, no self-verify, no web search,
  no extra model passes. The 1.5M-token incident came from breaking this.
- All heavy lifting (fetch, extract, rank, dedup, assemble, markets, manifest)
  runs on GitHub Actions — free compute, zero tokens.
- The renderer (`site/index.html`, `app.js`, `style.css`) is committed code; the
  routine never touches it.
- Keep `CLAUDE.md` / `ROUTINE_PROMPT.md` / `TASTE.md` short and stable between
  runs (they ride every routine turn; edits bust the prompt cache).
- Daily token target: ≤ ~350k/run (owner budget is 30M/week; v4 ran ~240k).

## Phase 0 — Sync & verify (do this before touching anything)
1. This folder is a LOCAL COPY (no `.git`). Clone/sync the real GitHub repo
   first and diff against this snapshot; reconcile before editing.
2. **Confirm the vanishing-editions bug** (diagnosed below): open recent
   "Publish Daily Cactus" Actions logs and look for
   `warn: unknown story id … dropped` / `likely wrong id, dropped` lines while
   assembling PAST-dated drafts. Expect many.
3. Confirm whether the routine is scheduled or still manual "Run now"
   (HANDOFF.md says manual). Report to owner.

## Diagnosis (root causes — all traced in code)
1. **Past editions get gutted.** IDs are positional per fetch
   (`build_digest.py::make_id` → `ai-1`), `feeds/refs.json` is overwritten
   daily, and `assemble_edition.py::main` re-assembles EVERY `drafts/*.json`
   on every publish against TODAY's refs. Old drafts' IDs now point at
   different articles, the wrong-link guard (`build_story`, headline/title
   word-overlap check) correctly drops the mismatches, and the gutted edition
   overwrites the good one on gh-pages (`keep_files: true` can't help — the
   file is regenerated, not absent).
2. **Summaries have no numbers.** The editor's only input is ≤200 chars of RSS
   blurb (`SUMMARY_CHARS` in both `fetch_feeds.py` and `build_digest.py`), and
   Google News items get `summary=""` (fetch_feeds.py, gnews branch). Half the
   candidates are GNews → the editor writes from headlines alone. It cannot
   cite numbers it never saw.
3. **Stale stories.** (a) Undated entries bypass the recency cutoff
   (`if et and et < cutoff` keeps undated); (b) no cross-day dedup, so a stale
   survivor repeats daily; (c) nothing checks the EVENT date — a February
   summit republished in June passes every window (worst in Opportunities,
   45-day window).
4. **Weak lead choice.** `score()` = recency + keyword count. The strongest
   free importance signal — the same story appearing across many feeds — is
   deleted by dedup instead of counted.
5. **Misc.** `site/editions/2026-06-18.json` contains fabricated
   `example.com` URLs (pre-refs era) live on the site; Import AI, Entrackr,
   The Ken, Analytics India feeds are dead/blocked; section thumbnails are
   128×88px floats (the "small photos" complaint).

---

## Phase 1 — Pipeline integrity (all on Actions, zero token cost)

**A1. Content-addressed IDs.** `id = f"{slug}-{sha1(url).hexdigest()[:10]}"` in
`build_digest.py`. An ID means the same story forever. (Keep slug prefix so the
editor still sees the section.)

**A2. Per-date refs snapshots.** Write `feeds/refs/YYYY-MM-DD.json` (keep
~30 days; prune older in the fetch workflow). `assemble_edition.py` resolves
each draft against ITS OWN date's snapshot, falling back to the union of
recent snapshots for hash IDs (hash IDs are stable, so a union is safe).

**A3. Editions are immutable.** `assemble_edition.py` assembles only drafts
that do NOT already have a `site/editions/<date>.json` on gh-pages (or: only
today's draft). Add `--force <date>` for deliberate rebuilds. The past is
never rebuilt by default.

**A4. Cross-day dedup.** In `build_digest.py`: load the last 7 days of
`drafts/*.json` + their refs snapshots, collect used URLs (and normalized
titles), and drop matching candidates before shortlisting.

**A5. Date hygiene.**
- Drop undated Google News items in `fetch_feeds.py`; penalize (don't drop)
  undated items from trusted publisher feeds.
- Opportunities: extract the event/deadline date from title+summary where
  possible; hard-drop events whose date is in the past. Prompt reinforcement:
  "never list an event whose date has passed."
- Per-section `max_age_hours` hard cap enforced in build_digest scoring
  (score 0 → cut) regardless of feed behavior.

**A6. Health & recovery.**
- Fetch/publish failure or an empty digest → the workflow opens a GitHub issue
  (actions/github-script) so thin papers are never silent.
- **Recover gutted editions:** each edition JSON was correct in the gh-pages
  commit from its own publish day. Walk `gh-pages` history, restore each
  `editions/<date>.json` from the first commit where it appeared, redeploy
  once. Also fixes/flags the `example.com` links (restore what's restorable;
  delete editions that were always broken, and note it in the issue).
- Delete legacy `automerge.yml` double-publish if still present (HANDOFF says
  harmless but noisy) — confirm with owner in the PR description.

## Phase 2 — Content quality

**B1. Full-text enrichment (the big lever).** New step in `fetch.yml` after
shortlisting: for each SHORTLISTED candidate (~60–80), fetch the article page
and extract main text with `trafilatura` (fallback: readability-lxml). Digest
entry becomes:
```json
{ "id": "ai-3f9c2d81aa", "title": "...", "source": "...", "published": "...",
  "buzz": 4, "img": true,
  "extract": "~700 chars of real article text, numbers preserved" }
```
- Keep total digest ≤ ~25k tokens (measure; trim extract length to fit).
- Extraction failures degrade to the RSS summary — never block the digest.
- Order of operations in build_digest: dedup → rank → shortlist → enrich
  (enrich only survivors; never fetch 280 pages).

**B2. Ranking v2.**
- **Buzz:** count near-dup cluster size across ALL feeds/sections BEFORE
  dedup; carry `buzz: N` into the digest so the editor sees corroboration.
  Add a modest score bonus (capped) for buzz.
- Source-quality weights (small, hand-set map in build_digest).
- Section tier weights per the owner's review doc (Tier 1: AI, Climate,
  Indian Startups, Opportunities; Tier 2: Deep Tech, India, Global Econ;
  Tier 3: Science, Health, Music) — applied to shortlist SIZE, not to
  cross-section story stealing.
- Still recall-oriented: heuristics shortlist, the model edits.

**B3. Prompt overhaul (`ROUTINE_PROMPT.md` rewrite — keep it ~same length).**
- Summaries are **numbers-first**: open with the concrete fact/figure, not the
  setup. Every summary that CAN carry a number MUST (funding size, %, dates,
  scale). Ban headline restatement (kept from v1.1).
- **Chart mentions:** if the extract references a chart/graph/data finding,
  state the chart's conclusion in the summary ("a chart shows X down 40%
  since 2023"). No image fetching, no vision tokens — text-derived only.
- **Editor's Read (owner-requested):** for the lead + top 2 frontpage stories
  ONLY, add an `editors_read` field: 2–3 sentences of second-order analysis —
  strategic implications, what likely happens next, connection to the
  reader's pivot (AI-generalist / founder's-office, India lens). Explicitly
  allowed to reason beyond the article; must be flagged as interpretation by
  the renderer, not fact. Cheap: ~300 output tokens/day.
- **`key_stat` field** per story (optional, one short string like
  "$234M · new unicorn") — rendered as a stat chip.
- Explicit lead criteria: magnitude × India angle × relevance to pivot × buzz.
- **The Brief (owner-requested):** draft gains a `brief` array — 8–12
  one-liner strings, each with its key number and the story id it points to.
  As concise as possible; covers the whole edition.
- Lead/frontpage counts stay honest — fewer on thin days, never pad.

**B4. TASTE.md + feedback loop (owner chose: taste file + thumbs).**
- `TASTE.md` at repo root, ≤40 lines: "more of / less of / never / opportunity
  bar / tone notes." Routine reads it along with the digest (2 reads total —
  update guardrail wording accordingly). Seed from known prefs: no hype,
  numbers over narratives, India lens, founder's-office pivot, allergic to
  padded sections and stale events.
- Renderer: 👍/👎 links per story → prefilled GitHub **issue** links
  (`.../issues/new?title=👍 <story-id>&body=<headline>+<date>` + labels
  `feedback,up|down`). Static-site compatible, no backend, no tokens.
- Weekly Actions job (cron, Sunday): collect the week's feedback issues,
  produce a small summary comment, and (via one cheap scheduled Claude Code
  run OR a plain heuristic append) fold durable patterns into `TASTE.md`,
  then close the issues. Keep TASTE.md short — replace, don't accumulate.

**B5. Markets snapshot (owner-selected, zero tokens).** New step in
`fetch.yml`: pull free quotes (executor picks the source — stooq.com CSV
endpoints or yfinance). LINEUP (locked 13 Jul): **Nifty 50, Sensex, USD/INR,
Brent, BTC, Gold, Silver** — all seven, 1-day change each. Gold/silver in the
Indian convention (₹ per 10g / ₹ per kg) if a reliable free source exists;
otherwise USD spot with a clear label. Write `feeds/markets.json`;
`assemble_edition.py` injects it verbatim as `edition.markets`; renderer shows
a compact stat bar under the masthead. The model never sees it.

**B6. Weekend longform (owner-selected).** New `longform` section in
`sources.yaml` (Aeon, The Guardian Long Read, Longreads, Nautilus — executor
verifies feeds work), `weekend_only: true`: fetched daily but included in the
digest only when today (IST) is Saturday. Editor picks 1–2 with a "why this
is worth 20 minutes" line. Skip silently when thin.

**B7. Source repairs.** Remove/replace dead feeds (Import AI, Entrackr,
The Ken, Analytics India). Candidate replacements (verify before adding):
Rest of World, Semafor tech/India, Mint or Moneycontrol RSS, Ben's Bites.
No net explosion of queries — the owner's review doc stands: fewer, broader,
better-ranked. Owner said "fix depth first" — do not add new topic sections
beyond B5/B6.

**B9. Section breadth (owner, 13 Jul: "in agritech I see only two articles —
we're missing things").** Root cause is BOTH ends of the funnel. Fix both.
> STATUS: caps, charter softening, the new "India Deep Tech" section, and a
> Down To Earth agri feed are ALREADY EDITED into this repo's sources.yaml /
> fetch_feeds.py / build_digest.py / ROUTINE_PROMPT.md (13 Jul) — push them.
> Owner priorities: AI 6→10 (main domain), Indian Startups 6→9, India Deep
> Tech NEW at 6. Still executor work: the "Also worth knowing" rail (approved
> by owner), verifying the two [TRY] feeds, and the B1 full-text enrichment.
- Pipeline caps up: `MAX_PER_FEED` 8→12; per-section `max_stories` +2 across
  the board (agritech 4→6, AI 6→8, etc.); `SECTION_HARD_CAP` 10→14. Digest
  grows ~40% — still well inside budget (~400k/run ceiling).
- Thin-section recall: audit sections with <3 feeds (Agritech, Health) and add
  1–2 verified feeds each (e.g. agritech: Down To Earth, ICAR/PIB-agri GNews
  query) so the shortage isn't upstream.
- Editor charter softened: keep "cut weak stories" but add "a section that had
  real news must not be starved — fill up to the cap when stories genuinely
  clear the bar."
- **"Also worth knowing" rail (new):** per section, after the full story cards,
  the editor may list 2–4 one-liners (`also: [{id, line}]` in the draft schema)
  — headline-grade facts worth knowing that don't merit a full card. Renderer
  shows them as a compact list with links. Breadth without bloat: a section
  now carries 3 cards + 4 one-liners ≈ 7 stories where v1 showed 2.

## Phase 3 — Site v2 (renderer rewrite)

Owner decisions:
- **Modern newsletter cards (Axios-like), but in a 2-column grid on desktop**
  (not single-column), 1 column on mobile. Big card images (top of card),
  not 128px floats.
- **Body carries the full crux** — not clipped to N lines: summary with
  bolded numbers, key-stat chip, The Signal bullets, Editor's Read (lead/top
  stories, visually marked as analysis), source + age + read-original link.
- **The Brief** renders at the top: 8–12 one-liners, key numbers bolded,
  each an anchor link to its story card.
- Markets stat bar under the masthead.
- 👍/👎 per card (GitHub issue links, B4).
- Sticky section nav, dark mode, keyboard j/k as polish (cheap wins).
- **Visual identity (colors/fonts/exact look) is OPEN** — the owner will share
  reference photos. Build structure + a tasteful placeholder theme with CSS
  variables so re-theming is a variables-only change. Do NOT lock in a final
  visual identity without those references.

Schema note: edition JSON gains `brief`, `markets`, `key_stat`,
`editors_read`, `buzz` (optional per story). Renderer must stay
backward-compatible with old editions (fallbacks like the existing
takeaway/why path).

## Phase 4 — Rollout order & verification
1. Phase 0 (sync, confirm bug, snapshot gh-pages).
2. Phase 1 (integrity) — deploy, then verify over TWO consecutive days:
   day-2 publish must leave day-1's edition byte-identical on gh-pages.
3. Phase 2 (digest enrichment + prompt + taste) — one supervised routine run;
   check token count (≤350k), summaries carry numbers, no stale events.
4. Phase 3 (renderer) — can ship in parallel; verify old editions still render.
5. Recovery job (A6) once Phase 1 is live.

Acceptance checklist:
- [ ] Past editions never change after publish day (2-day test).
- [ ] Every story link matches its headline (spot-check 10).
- [ ] No `example.com` URLs anywhere on gh-pages.
- [ ] ≥80% of summaries contain at least one concrete number.
- [ ] No story older than its section's max age; no past-dated events.
- [ ] Same story never appears two days running.
- [ ] Routine run ≤350k tokens, exactly 2 reads + 1 write.
- [ ] Brief present, ≤12 items, all anchors resolve.
- [ ] Markets bar renders from feeds/markets.json.
- [ ] 👍/👎 opens a prefilled labeled issue.
- [ ] Mobile (375px) and external-monitor widths both clean.

## Photo policy (Phase 3 addendum)
- The LEAD always shows a big image (drawn-cactus placeholder if its source has none).
- Every OTHER story card shows its article image at card-top WHEN the source
  provides one (refs.json already carries image URLs). No image = clean text-only
  card. Never stock-photo filler.

## Source quality audit (Phase 2 addendum — B8)
- Zero-token, on Actions: log per-feed stats each fetch (fetched → shortlisted →
  actually published). A monthly job posts a hit-rate summary as a GitHub issue so
  dead-weight feeds get pruned and replacements trialed on evidence, not vibes.

## Open items (owner decisions still pending)
- Wordmark & fonts: pick a masthead direction and type pairing from
  cactus-type-options.html, then hand-draw the final lettering (one-time asset).
- Visual identity is otherwise LOCKED (13 Jul): "Craft Paper" — kraft-textured
  ground, warm cream story cards, construction-paper section tabs, single amber
  highlighter for key facts. Tokens + brief: `~/Claude/Projects/Design and
  Stuff/Daily Cactus Brand/` and the claude.ai/design project "Cactus Craft".
- automerge.yml: a leftover workflow in the live repo that auto-merges the
  routine's branch into main. Harmless, but it triggers a SECOND publish run every
  day (wasted Actions minutes, noisy logs). Recommendation: delete it. Needs a
  yes/no from the owner.
- Audio edition & collapsible cards: discussed, not selected — out of scope for v2.
