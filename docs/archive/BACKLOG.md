# The Daily Cactus — Backlog & Change Record

Running record of owner feedback so nothing is lost. Newest dump: 2026-06-29.

## v1.1 — BUILT 2026-06-29 (decisions locked, tested locally, pending push)
Owner decisions: merged block = **"The Signal"** (bullets) · Beyond Your Beat =
**own section** · cross-day de-dup = **deferred to v2** · DEVELOPING badge =
**kept, recoloured grey** (+ optional grey `badge` slot added).
Done: E1 richer summary, E2/E3 The Signal bullets + no headline-restatement,
E4 Beyond Your Beat section, U1 mandatory lead photo (img flag + cactus
placeholder), U2 cactus favicon, B1 wrong-link guard (assemble drops mismatched
ids + prompt reinforced). B2 (repeats) intentionally deferred to v2.

## 🐛 Bugs to fix (v1.1)
- **B1 — Wrong link.** 28 Jun: "India's AI Impact Summit" story linked to an SCMP
  "Ukraine strikes Russia" article. Cause: the model paired its prose with the
  wrong story `id`. Fix: assemble-time sanity check (drop/flag when the written
  headline shares ~no words with the ref title) + reinforce the prompt's id rule.
- **B2 — Stale event repeats.** A Feb-2026 "Global AI Summit / India AI Impact
  Summit" reappears almost daily ("next week"). Causes: (a) old/undated articles
  slip past the recency window; (b) no cross-day dedup. Fix: lightweight cross-day
  de-dup (skip stories already run in the last ~7 days) + prompt: drop past-dated
  events.

## ✏️ Editorial / UX changes (v1.1)
- **E1 — Richer summary.** Not a teaser: a compact full readout — all the
  important facts + insights from the article, can be longer. Mention key
  image/graph with a clickable link. Reader shouldn't need to open the source.
- **E2 — Merge "Key takeaway" + "Why it matters"** into ONE short bulleted block
  (2–4 bullets). No longer two separate labelled sections. (Name TBD.)
- **E3 — No headline-restatement.** Summary must add context/insight, not repeat
  the headline in sentence form (the $28bn climate example).
- **E4 — "Beyond Your Beat".** Don't be boxed by stated interests: surface a few
  genuinely important stories outside his domains. (Own section vs mixed — TBD.)
- **U1 — Lead photo mandatory.** Top headline must always show an image (right
  side is empty now). Prefer an image-bearing lead + placeholder fallback.
- **U2 — Cactus 🌵 favicon** in the browser tab.

## ❓ Answered (this session)
- **DEVELOPING tag** = a small "📈 DEVELOPING" badge the editor sets on a still-
  unfolding story. Decision pending: keep or remove.
- **Token usage** ~240k/run vs 30M/week budget → very comfortable; we can afford
  richer summaries.
- **Versioning** → updates never wipe past editions (they live on gh-pages, kept
  via keep_files; the date dropdown is rebuilt from them). Pushing changes only
  updates logic/look going forward. We'll tag releases (v1.1, etc.).

## 🔭 Deferred to v2 (owner agreed)
- **V1 — Feedback-to-curator loop:** a way to tell the curator "more of this / less
  of that" so it learns taste over time.
- **V2 — Audio overview:** ~10-min NotebookLM-style narration, skippable by
  section and by item, for workouts/chores.
- **V3 — Full cross-day memory** (if the lite de-dup in B2 proves insufficient).
