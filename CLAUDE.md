# The Daily Cactus 🌵 — operational guide (v9, Oct 2026)

A self-updating personal morning newspaper for one reader (Aman, Jaipur).
GitHub Actions fetch and rank the news; two plain Claude calls on the owner's
subscription (editor, writer) pick and write it; code checks and publishes it
to GitHub Pages. No laptop, no agent loop.

> Humans: start with `HANDBOOK.md` (setup, operations, security, status).
> Editorial evidence: `EDITORIAL_STANDARDS.md`. Latest plan/audit: `docs/V9_PLAN.md`.
> History (routine era, v1-v7): `docs/archive/` — not how the system runs now.

## Pipeline
```
fetch.yml (cron 02:47 IST; GitHub starts it ~05:00-06:30)
  fetch_feeds.py -> build_digest.py (+ editorial.py, storymatch.py: dedup, repeats,
  section fit, PR flags, opportunity filters) -> feeds/digest_lean.json + refs/<date>.json
  fetch_markets.py -> feeds/markets.json · fetch_opportunities.py -> feeds/opportunities.json
        |
newsroom.yml -> scripts/newsroom.py
  EDITOR  prompts/editor.md   (one call: scores + picks; storymatch rejects duplicate picks)
  fetch_selected.py           (full text + recovery chain for blocked outlets)
  WRITER  prompts/writer.md   (one call: hook/points/signal/editors_read JSON)
  usage limit -> wait for reset, retry until 10:30 IST; still failing -> NO paper + alert issue
        |
publish.yml -> assemble_edition.py (guardrails, duplicate guard, grounding check,
  url/image/source injection) -> gh-pages editions/<date>.json -> renderer (site/)
```

## Rules that must hold
1. **No backup papers.** A day the models can't run gets no paper and an alert
   issue, never a no-AI substitute (owner's decision, 5 Oct 2026).
2. **Editions are immutable once published.** Deploys only add files
   (`keep_files: true`). Removing or replacing a paper is a manual, owner-approved act.
3. **Models never emit urls/images** — injected from the refs snapshot, so links
   can't be fabricated. Card facts must come from the card's own article
   (grounding check in code).
4. **Renderer changes must render both** the legacy edition `2026-06-18`
   (takeaway/why fields) and a recent one. Multi-column/flex packing; no JS masonry.
5. **Never cap article text** to save tokens; **fix extraction** for hard sources
   (Reuters/Bloomberg) instead of dropping them.
6. **Subscription only**, never an API key. Prompts live in `prompts/` and are live
   on the next run.
7. `assemble_edition.py` never silently deletes a correct story; guards WARN and log
   unless a rule above says otherwise (duplicates are dropped, logged).
8. Changing `.github/workflows/` raises the GUARD alarm issue by design.

## Settings (repo variables)
`NEWSROOM_MODE` (live|trial) · `EDITOR_MODEL` / `WRITER_MODEL` · `EDITOR_EFFORT` /
`WRITER_EFFORT` · `TOKEN_SET_ON`. Defaults live in `scripts/newsroom.py`.

## Feedback loop
Page 👍/👎 -> "send to GitHub" -> a "feedback batch" issue -> weekly `taste.yml`
-> `feeds/votes.jsonl` ledger (90-day window) -> auto lines in `TASTE.md` -> editor input.

## Editorial intent
Signal over noise; numbers-first; why it matters > what happened; India lens only
where real; no hype; thin days stay thin (never pad, never a past-dated event,
never a repeat of the last 7 days, never the same story twice in one paper).
