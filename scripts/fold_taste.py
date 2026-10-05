#!/usr/bin/env python3
"""B4 — fold a week of 👍/👎 GitHub-issue feedback into TASTE.md.

Zero model calls (plain heuristic), per the owner's choice. Reads a JSON list
of feedback events from stdin (produced by the taste.yml workflow's
github-script step, one entry per 'feedback' labeled issue):

    [{"story_id": "2026-07-14/ai/2", "vote": "up"}, ...]

The story id tells us the section. The renderer (site/app.js) emits feedback
ids as `<date>/<section-slug>/<tag>` (front-page/lead stories carry their
resolved home-section slug; only an unmatched lead falls back to `front`), so
the MIDDLE segment is the section. A legacy `<slug>-<hash>` id is also still
understood. Non-topic scopes (`front`, `opportunities`, `unknown`) are ignored.
Tallies votes per section over the batch, then REPLACES (never accumulates)
a single auto-generated line per section in TASTE.md's "More of"/"Less of"
sections, marked with an HTML comment so re-runs prune the old line instead
of piling up. Sections with a mixed/neutral signal get no line at all — this
is a nudge, not a rewrite of the owner's hand-written preferences (which stay
untouched above/below the auto block).

v9 (Oct 2026): the page now sends votes as ONE "feedback batch" issue (JSON
in the body) rather than one issue per vote, so the week's handful of votes
could never clear MIN_VOTES. Every vote is therefore appended to a ledger
(feeds/votes.jsonl, deduplicated) and the tally runs over the last
WINDOW_DAYS of votes. Front-page votes whose id still says `front`
(`<date>/front/lead`, `<date>/front/fp2`) are resolved to their section via
that day's editor selection (selections/<date>.json: lead / frontpage[n-1]).

Keeps TASTE.md's line budget: only sections with a CLEAR lean (>=70% one way,
>=3 votes) get a line, and at most 5 lines total, so the file cannot grow
without bound.
"""
import json
import re
import sys
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
TASTE = ROOT / "TASTE.md"

AUTO_START = "<!-- AUTO-FEEDBACK:START -->"
AUTO_END = "<!-- AUTO-FEEDBACK:END -->"
MIN_VOTES = 3
LEAN_THRESHOLD = 0.7
MAX_LINES = 5
WINDOW_DAYS = 90
LEDGER = ROOT / "feeds" / "votes.jsonl"
SELECTIONS = ROOT / "selections"


NON_TOPIC = {"front", "opportunities", "unknown", ""}


def section_of(story_id: str) -> str:
    """Section slug from a feedback id. New format `<date>/<slug>/<tag>` -> the
    middle segment; legacy `<slug>-<hash>` -> strip the hash. Non-topic scopes
    (front page with no resolved home section, opportunities) -> 'unknown'."""
    if not story_id:
        return "unknown"
    if "/" in story_id:
        parts = story_id.split("/")
        scope = parts[1] if len(parts) >= 3 else ""
        return scope if scope not in NON_TOPIC else "unknown"
    return re.sub(r"-[0-9a-f]{10}$", "", story_id)


def resolve_front(story_id: str) -> str:
    """`<date>/front/lead|fpN` -> `<date>/<section>/<tag>` using the editor's
    selection for that date (ids look like `ai-f13e6555ac`). Unresolvable ids
    are returned unchanged (and then count as 'unknown')."""
    parts = story_id.split("/")
    if len(parts) != 3 or parts[1] != "front":
        return story_id
    date, _, tag = parts
    try:
        sel = json.loads((SELECTIONS / f"{date}.json").read_text())
        if tag == "lead":
            pick = sel["lead"]
        elif tag.startswith("fp"):
            pick = sel["frontpage"][int(tag[2:]) - 1]
        else:
            return story_id
    except Exception:                                   # noqa: BLE001
        return story_id
    return f"{date}/{re.sub(r'-[0-9a-f]{10}$', '', pick)}/{tag}"


def normalise(e: dict) -> dict:
    """Accept the old per-issue shape {story_id, vote} and the batch shape
    {id, vote, date, at}."""
    sid = e.get("story_id") or e.get("id") or ""
    return {"story_id": resolve_front(sid), "vote": e.get("vote"),
            "date": e.get("date") or sid[:10], "at": e.get("at", "")}


def update_ledger(events: list) -> list:
    """Append new votes to the ledger; return the votes inside the window."""
    import datetime
    seen, rows = set(), []
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows.append(r)
                seen.add((r["story_id"], r.get("at", ""), r["vote"]))
    new = [e for e in map(normalise, events)
           if e["vote"] in ("up", "down") and (e["story_id"], e["at"], e["vote"]) not in seen]
    if new:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            for e in new:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        rows += new
    cutoff = (datetime.date.today() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    return [r for r in rows if (r.get("date") or "") >= cutoff]


def summarize(events):
    tally = defaultdict(lambda: {"up": 0, "down": 0})
    for e in events:
        sid = e.get("story_id", "")
        vote = e.get("vote")
        if vote not in ("up", "down"):
            continue
        tally[section_of(sid)][vote] += 1

    lines = []
    for section, counts in sorted(tally.items()):
        if section == "unknown":
            continue                      # non-topic votes: captured, not folded
        total = counts["up"] + counts["down"]
        if total < MIN_VOTES:
            continue
        up_ratio = counts["up"] / total
        if up_ratio >= LEAN_THRESHOLD:
            lines.append(f"- (feedback signal) more of **{section}** — "
                          f"{counts['up']}↑/{counts['down']}↓ in {WINDOW_DAYS} days")
        elif (1 - up_ratio) >= LEAN_THRESHOLD:
            lines.append(f"- (feedback signal) less of **{section}** — "
                          f"{counts['up']}↑/{counts['down']}↓ in {WINDOW_DAYS} days")
    return lines[:MAX_LINES]


def apply(taste_text: str, lines: list) -> str:
    block = "\n".join([AUTO_START, *lines, AUTO_END]) if lines else f"{AUTO_START}\n{AUTO_END}"
    if AUTO_START in taste_text and AUTO_END in taste_text:
        pattern = re.compile(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END), re.S)
        return pattern.sub(block, taste_text)
    # First run: append the block under "## More of" as a clearly-marked addendum.
    marker = "## More of"
    if marker in taste_text:
        return taste_text.replace(marker, f"{marker}\n{block}", 1)
    return taste_text.rstrip() + f"\n\n{block}\n"


def main():
    raw = sys.stdin.read()
    events = json.loads(raw) if raw.strip() else []
    window = update_ledger(events)
    lines = summarize(window)
    text = TASTE.read_text() if TASTE.exists() else "# TASTE\n\n## More of\n"
    updated = apply(text, lines)
    TASTE.write_text(updated)
    print(f"Folded {len(events)} new vote(s); {len(window)} in the {WINDOW_DAYS}-day window -> "
          f"{len(lines)} signal line(s) in TASTE.md.")


if __name__ == "__main__":
    main()
