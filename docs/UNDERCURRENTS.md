# Undercurrents: how the top section works

This is the one reference for the Undercurrents section (it was called "The Big Picture" from 20 to 23 Nov 2026).
If code, prompt and this file disagree, fix them so they match. Keep this file short.

## 1. Why it exists
Based on a reel by Drishti Grover ("L1, L2, L3"):
- **L1, the headline:** what happened. You can read at L1, but you forget it.
- **L2, the pattern:** what many headlines are evidence of ("AI data centres are hitting power limits").
- **L3, the force:** what drives the pattern (technology, capital, geopolitics, regulation, demographics, climate, discovery).

You only *remember* at L2 and L3. So the paper asks "what is changing?", not just "what happened?".

## 2. The paper's three layers
| Layer | Where | What it holds |
|---|---|---|
| News | the cards | facts only (L1) |
| Undercurrents | top of the paper | 2-5 trends behind the news (L2) plus their force (L3) |
| Threads | memory, not a section | each trend gets a stable id, so the paper can tell when it repeats over days and weeks |

## 3. Who does what
| Job | Done by |
|---|---|
| Spot the trends in today's stories, write name / line / why / watch / connects | **Writer** (Sonnet, `prompts/writer.md`, section "Undercurrents: six rules") |
| Say which known thread an item continues (`thread` id) | Writer, choosing from the THREADS list |
| Remember past trends, keep ids stable, count days | **Code** (`scripts/newsroom.py`: `thread_ledger`, `validate_draft`, `annotate_threads`) |
| Decide the status label, counts and dates | Code (`thread_status`) |
| Show it all on the page | Renderer (`site/app.js`: `patternsHTML`, `statusLabel`, `backLead`) |

Rule of thumb: **the writer writes words, code does numbers.** The writer never counts days or writes past dates.

## 4. Rules for one item (the writer follows these, code enforces the caps)
1. One specific change, never a topic, one company or a catch-all. No "Separately, ..." add-ons.
2. 2-5 items a day. At most 2 about AI. At most 2 continuing a thread. Fewer on a thin day.
3. Evidence: a new trend needs 2 or more of today's stories; a continuing thread needs 1.
4. A continuing thread appears only with a real new development, and says only what is new.
5. Stories that add to a thread without earning an item go in `threads_touched` (counted, not shown).

Code drops: a new item with only 1 story, items over the AI or thread caps, anything past 5 items.
A "new" item whose name matches a known thread is attached to that thread.

## 5. Memory (threads)
- Built fresh each run from the committed `drafts/` of the last **60 days**. No separate state file.
- Each thread: id, name, days seen, and what was last said.
- The writer sees up to 25 threads (most recent first). Each row reads:
  `id | name | seen on N days since DATE | QUIET since DATE (if 5+ days quiet) | last said: ...`

## 6. Status labels (code decides, checked in this order)
| Status | Rule | Label on the page |
|---|---|---|
| new | first time ever | **New** |
| back | seen before, but quiet for 5+ days until today | **Back again**, and the line starts with "**4th time in 3 weeks** (last seen 18 Nov)." |
| ongoing | seen in 3 or more of the last 4 weeks | **Ongoing · 3 weeks** |
| growing | anything else (seen recently) | **Growing · 4 days in 2 weeks** |

"Back again" wins over "ongoing": a trend that went quiet and returned is the more useful signal.
Papers from before 23 Nov 2026 carry the old names. The renderer maps them: building → Growing, established → Ongoing, returning → "Seen before".

## 7. On the page
Each item shows its number, name, force chip and status chip, then the line, "Why it matters", optional "Connects to", optional "Watch", and the list of today's stories.
"Other takeaways" (big stories that fit no trend) sits underneath. On Sundays code adds **"The week's threads"**: the trends active this week, and the ones that went quiet (no model call).

## 8. Changing this safely
- Change the words in `prompts/writer.md` and the rules in `newsroom.py` together, then update this file.
- Run `python3 -m pytest -q tests` (thread rules are tested in `tests/test_v10.py`).
- Renderer changes must still render old papers (before 9 Nov there is no Undercurrents section at all).

## Change log
- 9 Nov 2026: "What's changing" block (v10).
- 20 Nov 2026: renamed "The Big Picture"; threads with stable ids (v11).
- 23 Nov 2026: renamed **Undercurrents**. Statuses simplified to New / Back again / Growing / Ongoing. "Nth time in N weeks" line for returning trends. Writer rules rewritten as six numbered rules.
