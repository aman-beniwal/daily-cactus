# Option B — two-stage "select → full-extract → write" pipeline

Whole-article summaries without a token blow-up. Built + verified 2026-07-18.
ADDITIVE: the existing single-routine flow keeps working untouched until the
owner switches to the two routines below.

## The flow
```
fetch.yml (05:00 IST, Actions)
  fetch_feeds → build_digest → digest.json (rich, 2500-char extracts — old flow)
                             → digest_lean.json (title + 300-char teaser + buzz/img — for A)
                             → feeds/refs/<date>.json, markets.json
        │
        ▼
Routine A "Selector" (05:30 IST, subscription)
  reads digest_lean.json + TASTE.md → writes selections/<date>.json (IDs + structure only)
        │  (pushes to a claude/* branch)
        ▼
select.yml (Actions, triggered by the selections/** push)
  scripts/fetch_selected.py: for each selected id → resolve url → fetch WHOLE
  article (browser UA, ≤12000 chars); on block, fall back to the digest's
  2500-char extract, then RSS summary → feeds/selected/<date>.json → commit to main
        │
        ▼
Routine B "Writer" (06:00 IST, subscription)
  reads feeds/selected/<date>.json + TASTE.md → writes drafts/<date>.json
  (full prose from whole-article text). GUARD: if the selected file is missing
  or stale, it falls back to reading digest.json (degraded, never a broken paper).
        │  (pushes to a claude/* branch)
        ▼
publish.yml (unchanged) → assemble_edition → site/editions/<date>.json → gh-pages
```

## Measured (live test, 15-story selection, 2026-07-17 data)
- fetch_selected: **9/15 full article text, 3 digest-extract fallback, 3 headline-only**
  (the 3 "none" were fabricated test ids not in refs; a real Routine A only picks
  digest ids, which all have refs — so real coverage is higher).
- selected file: ~78k chars ≈ **~18k tokens for 15 stories** → ~30–40k tokens for a
  full 25–30 story edition. Routine B input stays well under budget.
- digest_lean.json: 72KB (~18k tok) vs the rich digest.json 92KB (~23k tok) — A reads the lean one.
- Estimated daily total: Routine A (~lean digest × few select turns) + Routine B
  (~selected × few write turns) ≈ **~250–300k/day WITH whole articles** — vs ~450k
  today with 2500-char snippets. Better on quality AND cost.

## Known limitation
**YourStory and Moneycontrol hard-block scrapers** (JS/anti-bot), so even the
fallback only recovers their ~200-char RSS summary — thin for the Indian-startups
beat. Follow-up idea: capture YourStory's `content:encoded` in fetch_feeds if it's
richer than the RSS `summary`, or add a JS-rendering fetch for a small allowlist.
Not blocking — those stories still get their headline + RSS text, just not full body.

## OWNER SETUP (one-time, when you're ready to switch)
The code is deployed and DORMANT (select.yml only fires when Routine A pushes a
selections file). To go live:
1. **Repo Settings → Actions → General → Workflow permissions → "Read and write"**
   (select.yml commits feeds/selected to main; also needed for the health-issue
   workflows). Still pending from the v2 deploy.
2. In claude.ai → Routines, create **two** routines:
   - **Routine A "Selector":** prompt = `ROUTINE_PROMPT_A.md`, schedule ~05:30 IST.
   - **Routine B "Writer":** prompt = `ROUTINE_PROMPT_B.md`, schedule ~06:00 IST.
   Confirm each may read its two files (A: digest_lean.json + TASTE.md;
   B: feeds/selected/<date>.json + TASTE.md).
3. **Retire / pause the current single routine** so you're not running both.
4. Keep the 30-min A→B gap so select.yml has time to fetch full text between them.

## Failure modes + guards (verified in design)
- select.yml slow/failed → Routine B's GUARD falls back to digest.json (short
  extracts) → a complete, if less deep, paper. Never empty.
- A picks an id that fails full fetch → digest-extract fallback (≥ RSS summary).
- selections file with unknown ids → assemble_edition already drops unknowns.
- select.yml does NOT trigger publish.yml (publish watches drafts/** + site/**;
  selections/** is neither — verified).

## Files
- NEW: ROUTINE_PROMPT_A.md, ROUTINE_PROMPT_B.md, scripts/fetch_selected.py,
  .github/workflows/select.yml
- CHANGED (additive): scripts/build_digest.py (writes digest_lean.json too)
