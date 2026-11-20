# v11: Threads (our own three layers) — 20 Nov 2026

**Goal (Aman, 20 Nov):** don't copy the reel's L1/L2/L3. Build three layers that add value:
1. **The news** — fact cards (unchanged).
2. **The Big Picture** — 2-4 things today's stories add up to; a NEW item needs ≥2 stories;
   each may carry a `connects` line ("Energy: …") linking the change to another field.
3. **Threads** — long-running storylines with a STABLE id, so renaming can't break memory.
   Status from code: New / Building (≥3 days in 14) / Established (seen in ≥3 different weeks
   within 28 days) / Returning (seen before, below Building). Sunday: "The week's threads"
   (rising, steady, faded), built by code from drafts, no model call.
Plus the masthead tagline: "The headlines are just the prickles."

Answers Aman's question "after 2-3 weeks, do we see if the same pattern repeats?" — yes, via
thread status + days counted, including quiet days where a story only *touches* a thread.

## Design
- No new state file / no workflow change: the ledger is derived from committed `drafts/` +
  `trial/drafts/` (60 days). Old patterns without ids are clustered by name into threads.
- Writer input: THREADS list (id | name | status | days | last said). Writer output per
  pattern: `thread` (existing id, or omit for new), `connects`; plus `threads_touched`
  [{thread, evidence}] for stories that add to a thread without a Big Picture item.
- Code (newsroom.validate_draft + annotate_threads): unknown id -> new; new item with <2
  stories dropped; a "new" whose name matches an existing thread is attached to it; mints ids;
  stores `thread: {id, status, days, weeks, first}` on each pattern; Sunday adds `week_threads`.
- assemble_edition passes `thread`, `connects`, `week_threads` through.
- Renderer: status chip instead of "Update"; "Connects to" line; Sunday threads block.

## Steps
- [x] Tagline in masthead
- [x] newsroom.py: ledger, ids, status, writer input, validation, week_threads
- [x] prompts/writer.md: threads rules (short)
- [x] assemble_edition.py pass-through
- [x] renderer
- [x] tests (test_v10.py + new), offline run on recent drafts, old papers render
- [x] push, HANDBOOK + memory
