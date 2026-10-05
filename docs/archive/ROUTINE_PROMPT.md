# ROUTINE_PROMPT.md — RETIRED. Do not paste this file.

This was the prompt for the OLD single-routine flow (v5). The paper now runs
**two** routines, and pasting this file into either of them silently produces
old-format editions: one-paragraph summaries, no bullets, no highlighter.

That is exactly what happened on 2026-07-29 and 2026-08-01.

## Paste these instead

| Routine | Paste this file | It writes |
|---|---|---|
| **A — SELECT** (runs first) | `ROUTINE_PROMPT_A.md` | `selections/<date>.json` — story ids only, no prose |
| **B — WRITE** (runs after) | `ROUTINE_PROMPT_B.md` | `drafts/<date>.json` — the actual paper |

The bullets (`hook` + `points`) and the `==highlight==` marker are defined in
**`ROUTINE_PROMPT_B.md`**. If the paper comes out as paragraphs, Routine B is
the one running a stale prompt.

## You will be told when this happens

`scripts/assemble_edition.py` now derives bullets from a paragraph summary when
the draft doesn't carry them, so the format is correct either way — and it
prints a loud `!! Routine B is running a STALE PROMPT` line in the publish log
so a stale prompt can never be silent again.
