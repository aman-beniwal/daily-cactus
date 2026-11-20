# Daily Cactus v10: wider news + the L1/L2/L3 layer (9 Nov 2026)

**Goal:** a paper that (1) is less narrow: general knowledge a citizen should know,
science and neuroscience, interests that change; fewer routine funding cards; and
(2) helps Aman *remember*: every headline is shown as evidence of a pattern, and
patterns are tracked across days.

Branch: `v10-editor-patterns`. Nothing reaches `main` (live) without Aman's OK.

## Why the paper got narrow (evidence, sample of 21 papers + 3 days of scores)
- Editor composite = Scale + Novelty + Consequence + **Fit** (equal weight); beats were
  AI > startups > deep tech. On 3 Nov and 7 Nov, **zero** picks had Fit <= 1. Skipped:
  measles resurgence, Assam mass evictions, pandemic preparedness, Iran strike doubts.
- "India" (national affairs) was not a beat, so Indian civic news scored Fit 1.
- **Funding boosted at source:** `editorial.INTEREST_TIERS` gives +1.3 to any headline
  with raises/funding/series/valuation/IPO, so rounds flood the shortlist. Rough count:
  2-3 funding cards/paper mid-Oct, 8-9 in Nov.
- Not the cause: 👍 votes (17 total, spread over 9 sections; TASTE auto-lines empty).

## The reel's idea (Drishti Grover, "L1, L2, L3")
L1 = headline (event). L2 = the pattern many events are evidence for ("AI infra is
scaling", "capital moving into hard tech"). L3 = the force under it (technology,
capital, geopolitics, regulation, demographics). You can *read* at L1 but only
*retain* at L2/L3. Ask "what is changing", not "what happened". Follow where capital
is going, not every round. Cross-domain thinking needs cached patterns.

## Plan
### A. What gets picked
- [x] A1 Source: remove funding words from the interest boost; flag routine rounds
      (`routine-round`) so they rank lower and the editor sees why.
- [x] A2 Editor: Fit counts half (composite = S + N + C + Fit/2), in prompt and code.
- [x] A3 Editor: "Citizen" picks: 4-5 of the day's most consequential stories
      regardless of beat (world, India national, public health, science).
- [x] A4 Editor: India national affairs becomes a beat; a funding round gets its own
      card only if it shows a shift (sector record, new category, strategic/state
      investor, down round); other rounds go to the "Where the money went" roll-up.
- [x] A5 Flexible size: usual 24-30 cards, up to 35 on a strong day; section sizes are
      a guide, +1-2 allowed for strong stories. One rule the model can follow.
- [x] A6 TASTE.md: a short "Curious about lately" list Aman edits by hand.
### B. Science + neuroscience
- [x] B1 New "Science & Mind" section: top science news + neuroscience; feeds
      live-checked before adding.
### C. Writing in layers (L1 -> L2 -> L3)
- [x] C1 Writer: each card gets `evidence_of` (L2 pattern, short) + `force` (L3).
      Points stay L1 facts; signal stays the "so what".
- [x] C2 Writer: a top-of-paper "What's changing" block: 3-5 L2 patterns, each with
      today's evidence stories and one line on what it means.
- [x] C3 Writer: "Where the money went" roll-up (company, amount, sector) under the
      capital pattern, instead of one card per round.
- [x] C4 Pattern memory: code keeps `feeds/patterns.jsonl`; the writer gets the last
      14 days' patterns so names repeat and it can say "4th sign in 2 weeks".
- [x] C5 Renderer: show the block, roll-up and per-card tag; old editions still render.
### D. Check
- [x] D1 Unit tests pass; renderer shows legacy 2026-06-18 + a new edition.
- [x] D2 Trial run (`--mode trial`, writes to `trial/`) on today's digest; compare.
- [ ] D3 Report to Aman; merge only on his OK; review after one week.

## Later (not now)
- Sunday "Week in patterns" (L2/L3 summary of the week).

## Progress notes
- 9 Nov: A1-A5 done (editorial.py ROUND_RX flag + no funding bonus; editor.md rules;
  newsroom.py comp = S+N+C+F/2, MAX_FULL_CARDS 35, writer input guard 340k). 51 tests pass.
- B1 feed check done (helper, 9 Nov). Working: thetransmitter.org/feed, neurosciencenews.com/feed
  (press releases), sciencedaily mind_brain/neuroscience + top/science (cap), news.mit.edu/rss/topic/neuroscience,
  knowablemagazine.org/rss, psyche.co/feed, elifesciences.org/rss/subject/neuroscience.xml.
  ALL nature.com RSS return a bot wall: existing nature.rss likely empty. Neuron/Trends stale.
- DONE 9 Nov: B1 (science-mind section, 13 feeds), A6, C1-C5 (memory derived from drafts/, no workflow change), tests/test_v10.py. 56 tests pass.
- (old note) Next: B1 add section to sources.yaml (check per-feed cap support in fetch_feeds.py),
  then C1-C5 (writer.md fields, newsroom.py writer_input patterns memory + validate_draft money ids,
  assemble_edition build_story must pass evidence_of/force + top-level patterns/money,
  newsroom.yml only commits selections/ feeds/selected/ drafts/ -> store pattern memory under drafts/ or
  derive it from drafts/*.json to avoid a workflow change (which raises the GUARD alarm)).
- 9 Nov trial (today's real news, local run): editor picked 35 cards (ceiling) incl. 6 below its own bar
  -> code now enforces SOFT_FULL_CARDS=30 / STRONG_COMP=10 (this day would land ~30). Front page wide
  (grid storage, AI-safety centre, Aramco attack, Maoists, FPIs, hiring, COP31, AI maths proof).
  Funding cards: 1 (Zipline $1.5B); money roll-up 1 row; 0 Indian-startup cards (watch this).
  5 patterns, 22 tagged cards. Writer output ~38k tokens (was 11-14k) and 264s: watch usage.
  Local token totals include ~155k of local CLI overhead (user's own Claude config), so the
  "spike" alert from that run is not representative of GitHub runs.
  Renderer checked: blocks render, no horizontal scroll, 2026-06-24 legacy JSON renders
  (2026-06-18 exists only as archive HTML). Card force now follows its pattern's force.
  Feed fixes: neurosciencenews.com dropped (malformed XML); nature.com/nature.rss fails (bot wall), left as-is.

## Side-by-side trial (from 9 Nov 2026, Aman's call: 2-3 days before merging)
- `main` gained ONLY `.github/workflows/trial_v10.yml` (commit 699dffb). Daily 09:00 IST it checks
  out this branch, fetches + ranks with v10 code, runs editor + writer in `--mode trial`, commits
  `trial/` to this branch, and deploys to gh-pages `/v10/` (keep_files). Live paper untouched.
- Read it at https://aman-beniwal.github.io/daily-cactus/v10/?trial=YYYY-MM-DD
- Cost: a second editor + writer call each day on the same subscription.
- When Aman says merge: (1) `git pull` this branch (the bot commits trial/ to it),
  (2) merge into main, (3) delete `.github/workflows/trial_v10.yml`, (4) watch the next live run.
  If abandoned: delete the workflow file from main; the /v10/ pages can stay or be removed.

## v10.1 (20 Nov 2026): review fixes + go-live (Aman's OK: build, test, publish, live uses v10 from now)
Goal: cards = information only; top section = all learning; no repeats; cleaner design.
- [x] W1 writer.md: no ==highlight==; no signal/editors_read on cards; patterns get `why` (+ optional `watch`);
      `takeaways` [{id,line}] for big one-off stories; memory option B (old pattern only with a real new
      development, written as the new bit; >=2 new patterns/day; <=2 AI patterns; no catch-all names).
- [x] C1 newsroom.py: card evidence_of/force DERIVED from pattern evidence lists (writer no longer tags);
      strip ==, signal, editors_read from cards; drop same story twice (id or near-identical headline);
      cap AI patterns at 2; recent-pattern memory gives last line said (so writer knows what's not new).
- [x] C2 assemble_edition.py: pass through pattern why/watch/update + takeaways.
- [x] R1 renderer: numbered-list "What's changing" (option 2) + "Other takeaways"; thread line after bullets
      (superscript n, name, superscript arrow, jumps up); icon-bar footer (Source/ChatGPT/Claude/Copy + thumbs);
      no highlight; legacy editions still render. Bump ?v=.
- [x] T1 tests + local render at 375/768/1280 on a v10 edition and a legacy edition.
- [ ] P1 push branch; run trial end-to-end; check /v10/ at phone/desktop.
- [ ] P2 merge into main; delete trial_v10.yml; next live run uses v10.
