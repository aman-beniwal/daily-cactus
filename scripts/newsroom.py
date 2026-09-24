#!/usr/bin/env python3
"""The Daily Cactus newsroom (v8) — the whole editorial day in ONE GitHub
Actions job, on the owner's Claude subscription, with two model calls.

WHY (Sep 2026)
--------------
The v6/v7 paper ran as two Claude Code *agent* sessions (SELECT, WRITE) on a
laptop's scheduled tasks, glued together by git pushes and clock times:
  * an agent re-sends its whole context on every step (read file, read file,
    write, git add, commit, push …) — the owner measured 30-40% of a weekly
    Pro allowance for one paper a day;
  * the prompts lived in the routine settings, not the repo, and silently went
    stale for a month;
  * it depended on a laptop being awake (10-11 am), and when a run was
    skipped (21 Sep) nothing noticed.

Here the model does only what needs judgment, as two plain calls:
  1. EDITOR  (prompts/editor.md) — scores every shortlisted candidate on the
     news-value rubric and picks lead / front / sections. One call.
  2. WRITER  (prompts/writer.md) — writes the cards from the full text. One call.
Everything else — fetching, ranking, dedup, full-text recovery, budgets,
validation, publishing — is code. `claude -p` runs with its tools switched
off and our own system prompt, so each stage is a single request billed to
the subscription (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`), not an
agent loop. Exact token usage per stage is logged to feeds/newsroom_log.jsonl.

MODES
  trial — writes under trial/ (never the live archive); the page shows it at
          ?trial=<date>. Used to compare against the routine's paper.
  live  — writes drafts/<date>.json; the workflow assembles + publishes it.
If the WRITER fails twice, a no-AI "wire edition" is written instead (the
chosen stories with headline + opening sentences) so there is never a blank
morning; it is marked `backup: true`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PROMPTS = ROOT / "prompts"
DIGEST_LEAN = ROOT / "feeds" / "digest_lean.json"
TASTE = ROOT / "TASTE.md"
LOG = ROOT / "feeds" / "newsroom_log.jsonl"

# --- usage guard (v8.2) -----------------------------------------------------
# Hard ceilings per call: a runaway input (a bug, a huge page) is refused
# BEFORE it is sent, so one bad day can't eat the weekly allowance.
MAX_INPUT_CHARS = {"editor": 160_000, "writer": 260_000}   # ~40k / ~65k tokens
SPIKE_FACTOR = 2.0          # today vs the median of the last 7 runs
SPIKE_FLOOR_TOKENS = 90_000 # never alarm below this (normal day ~60k)
ALERT_FILE = ROOT / "newsroom_alert.txt"   # the workflow turns this into an issue

MAX_FULL_CARDS = 24
MAX_FRONT = 8
MAX_ALSO = 2          # per section (v8.3: 20-30 one-liners pulled the reader into clicking)
MAX_ALSO_TOTAL = 10
MAX_OPPS = 6


# ---------------------------------------------------------------- model call
def ist_today() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now + datetime.timedelta(hours=5, minutes=30)).date().isoformat()


def alert(title: str, body: str) -> None:
    with open(ALERT_FILE, "a", encoding="utf-8") as f:
        f.write(f"## {title}\n{body}\n\n")
    print(f"  !! ALERT: {title}")


def usage_check(date: str) -> None:
    """Compare today's newsroom tokens with the recent median; alarm on a spike.
    Only OUR runs are visible here — for the account as a whole, compare with
    claude.ai → Settings → Usage (HANDBOOK.md §4)."""
    rows = []
    try:
        rows = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        return
    def tot(r):
        return sum((r.get(k) or 0) for k in ("input_tokens", "cache_write_tokens", "output_tokens"))
    by_day = {}
    for r in rows:
        by_day[r["date"]] = by_day.get(r["date"], 0) + tot(r)
    today = by_day.get(date, 0)
    past = sorted(v for d, v in by_day.items() if d < date)[-7:]
    if not past:
        return
    median = sorted(past)[len(past) // 2]
    print(f"  usage: today {today:,} tokens vs recent median {median:,}")
    if today > SPIKE_FLOOR_TOKENS and today > SPIKE_FACTOR * median:
        alert("Newsroom token use spiked",
              f"{date}: {today:,} tokens vs a recent median of {median:,}. Check the run log; "
              "if your claude.ai usage page also shows use you don't recognise, rotate the token.")


def _parse_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(t[i:j + 1])


def call_claude(stage: str, system_prompt: str, user_text: str, model: str,
                effort: str | None, date: str, attempts: int = 2):
    """One non-agentic request via the Claude Code CLI. Returns parsed JSON."""
    if len(user_text) > MAX_INPUT_CHARS.get(stage, 200_000):
        raise RuntimeError(f"{stage} input {len(user_text)} chars exceeds the usage guard "
                           f"({MAX_INPUT_CHARS.get(stage)}); refusing to send")
    last_err = None
    for attempt in range(1, attempts + 1):
        # Attempt 1 replaces Claude Code's system prompt (leanest). If that is
        # refused for any reason, attempt 2 appends ours to the default instead.
        sp_flag = "--system-prompt" if attempt == 1 else "--append-system-prompt"
        cmd = ["claude", "-p", "--model", model, sp_flag, system_prompt,
               "--tools", "", "--output-format", "json", "--no-session-persistence",
               "--max-turns", "2"]
        if effort:
            cmd += ["--effort", effort]
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:   # no repo CLAUDE.md in scope
            proc = subprocess.run(cmd, input=user_text, capture_output=True,
                                  text=True, cwd=tmp, timeout=1500)
        dur = round(time.time() - t0, 1)
        try:
            meta = json.loads(proc.stdout)
        except Exception:                                        # noqa: BLE001
            last_err = f"CLI exit {proc.returncode}: {proc.stderr[-400:] or proc.stdout[-400:]}"
            print(f"  {stage} attempt {attempt}: {last_err}")
            continue
        usage = meta.get("usage") or {}
        row = {"date": date, "stage": stage, "model": model, "effort": effort,
               "attempt": attempt, "seconds": dur, "turns": meta.get("num_turns"),
               "input_tokens": usage.get("input_tokens"),
               "cache_read_tokens": usage.get("cache_read_input_tokens"),
               "cache_write_tokens": usage.get("cache_creation_input_tokens"),
               "output_tokens": usage.get("output_tokens"),
               "api_equivalent_usd": meta.get("total_cost_usd"),
               "input_chars": len(user_text), "is_error": meta.get("is_error")}
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        tin = sum((row.get(k) or 0) for k in ("input_tokens", "cache_write_tokens", "cache_read_tokens"))
        print(f"  {stage}: {tin:,} tokens in / {row['output_tokens']:,} out, {dur}s, turns={row['turns']}")
        if meta.get("is_error"):
            last_err = str(meta.get("result"))[:400]
            if re.search(r"auth|token|401|403|expired|invalid.*key|login", last_err, re.I):
                alert("Claude token rejected", f"The {stage} call was refused: {last_err}\n"
                      "The subscription token is expired, revoked or wrong — rotate it (HANDBOOK.md §4).")
            continue
        try:
            return _parse_json(meta.get("result", ""))
        except Exception as ex:                                  # noqa: BLE001
            last_err = f"unparseable output: {ex}"
            print(f"  {stage} attempt {attempt}: {last_err}")
    raise RuntimeError(f"{stage} failed: {last_err}")


# ---------------------------------------------------------------- editor
def recent_leads(days: int = 7):
    d = pathlib.Path(os.environ.get("EDITIONS_DIR", ""))
    out = []
    if d.is_dir():
        for f in sorted(d.glob("20*.json"))[-days:]:
            try:
                ed = json.loads(f.read_text())
                out.append(f"{f.stem}: {(ed.get('lead') or {}).get('headline', '').replace('==', '')}")
            except Exception:                                    # noqa: BLE001
                pass
    return out


def editor_input(digest: dict) -> str:
    parts = [f"DATE: {digest.get('date')}"]
    leads = recent_leads()
    if leads:
        parts.append("LEADS OF THE LAST 7 DAYS (do not repeat without a new fact):\n" + "\n".join(leads))
    parts.append("CANDIDATES (ranked best-first within each section by code):")
    for sec in digest.get("sections", []):
        parts.append(f"\n## {sec.get('name')} [slug: {sec.get('slug')}]")
        for s in sec.get("stories", []):
            bits = [s["id"], s.get("title", ""), s.get("source", "")]
            if s.get("buzz"):
                bits.append(f"buzz {s['buzz']}")
            if s.get("when"):
                bits.append(s["when"])
            if s.get("seen"):
                bits.append(f"SEEN {s['seen']}")
            if s.get("flags"):
                bits.append("FLAGS: " + "; ".join(s["flags"]))
            line = " | ".join(bits)
            teaser = (s.get("teaser") or "").strip()
            parts.append(line + (f"\n   {teaser}" if teaser else ""))
    try:
        parts.append("\nTASTE (the reader's standing notes):\n" + TASTE.read_text())
    except FileNotFoundError:
        pass
    return "\n".join(parts)


PRIORITY = ["ai", "indian-startups", "india-deep-tech", "deep-tech", "global-economics",
            "india", "world", "climate-energy", "work-careers", "health-tech", "agritech",
            "other-interests", "beyond-your-beat"]


def code_ranked_selection(digest: dict) -> dict:
    """Editor fallback: the digest is already ranked best-first per section by
    build_digest. Lead = top AI story; front = the top story of the next
    priority sections; two more per section as cards; skip anything flagged
    or already seen. Plain, but never a blank morning."""
    secs = {s["slug"]: [x for x in s.get("stories", []) if not x.get("seen") and not x.get("flags")]
            for s in digest.get("sections", [])}
    order = [p for p in PRIORITY if secs.get(p)]
    lead = secs[order[0]][0]["id"] if order else None
    front = [secs[p][0]["id"] for p in order[1:9]]
    sections = [{"slug": p, "stories": [x["id"] for x in secs[p][1:3]], "also": [x["id"] for x in secs[p][3:5]]}
                for p in order]
    return {"lead": lead, "frontpage": front, "sections": sections, "opportunities": [],
            "lead_reason": "editor unavailable — ranking-script order"}


def validate_selection(sel: dict, digest: dict) -> dict:
    """Enforce the rules in code, whatever the model returned: known ids only,
    one lead, front <= 8, no front/section repeats, <= 24 full cards, rails and
    opportunities capped. Trims the lowest-scored picks first."""
    known = {s["id"]: sec["slug"] for sec in digest.get("sections", []) for s in sec.get("stories", [])}
    whens = {s["id"]: s["when"] for sec in digest.get("sections", []) for s in sec.get("stories", [])
             if s.get("when")}
    scores = sel.get("scores") or {}

    def comp(i):
        v = scores.get(i)
        return sum(x for x in v[:4] if isinstance(x, (int, float))) if isinstance(v, list) else 0

    def ok(i):
        return isinstance(i, str) and i in known

    lead = sel.get("lead") if ok(sel.get("lead")) else None
    if not lead:
        pool = [i for i in known if known[i] != "opportunities"]
        lead = max(pool, key=comp) if pool else None
    front = [i for i in dict.fromkeys(sel.get("frontpage") or []) if ok(i) and i != lead][:MAX_FRONT]
    placed = {lead, *front}
    sections = []
    for s in sel.get("sections") or []:
        stories = [i for i in dict.fromkeys(s.get("stories") or []) if ok(i) and i not in placed]
        placed.update(stories)
        also = [i for i in dict.fromkeys(s.get("also") or []) if ok(i) and i not in placed][:MAX_ALSO]
        placed.update(also)
        if stories or also:
            sections.append({"slug": s.get("slug"), "stories": stories, "also": also})
    # reading budget: drop the lowest-scored section stories first
    full = [lead] + front + [i for s in sections for i in s["stories"]]
    over = len(full) - MAX_FULL_CARDS
    if over > 0:
        cut = set(sorted((i for s in sections for i in s["stories"]), key=comp)[:over])
        for s in sections:
            s["stories"] = [i for i in s["stories"] if i not in cut]
    total_also = 0
    for s in sections:                      # keep the best-scored one-liners overall
        s["also"] = sorted(s["also"], key=comp, reverse=True)
    for s in sorted(sections, key=lambda x: -max([comp(i) for i in x["also"]] or [0])):
        keep = max(0, min(len(s["also"]), MAX_ALSO_TOTAL - total_also))
        s["also"] = s["also"][:keep]
        total_also += keep
    sections = [s for s in sections if s["stories"] or s["also"]]
    opps = [i for i in dict.fromkeys(sel.get("opportunities") or [])
            if ok(i) and known[i] == "opportunities"][:MAX_OPPS]
    longform = [i for i in (sel.get("longform") or []) if ok(i)][:2]
    return {"date": digest.get("date"), "lead": lead, "frontpage": front, "sections": sections,
            "opportunities": opps, "longform": longform,
            "opp_when": {i: whens[i] for i in opps if i in whens},
            "lead_reason": sel.get("lead_reason", ""), "lead_contenders": sel.get("lead_contenders", []),
            "_scores": {i: comp(i) for i in known}, "_section_of": known}


def readable(st: dict) -> bool:
    return (st or {}).get("text_source") == "full" or \
        ((st or {}).get("text_source") == "digest-extract" and len((st or {}).get("fulltext") or "") >= 400)


def replace_unreadable_cards(sel: dict, selected: dict, fs, max_fetches: int = 8) -> None:
    """v8.3: a card with no readable text used to publish as a headline plus one
    line (24 Sep: Modal Labs, LG/Samsung, a Fed story). Now each such card is
    swapped for the best-scored unpicked story of the same section that CAN be
    read; if none, it becomes a one-liner instead of an empty card."""
    stories = selected.setdefault("stories", {})
    scores, section_of = sel.get("_scores", {}), sel.get("_section_of", {})
    used = {sel["lead"], *sel["frontpage"], *sel["opportunities"]}
    for s in sel["sections"]:
        used.update(s["stories"]); used.update(s["also"])
    refs = fs.load_refs_for_date(sel["date"])
    fallbacks = fs.load_digest_fallback()
    fetches, start = 0, time.time()

    def backfill(slug):
        nonlocal fetches
        pool = sorted((i for i, sec in section_of.items() if sec == slug and i not in used),
                      key=lambda i: -scores.get(i, 0))
        for cand in pool[:3]:
            if fetches >= max_fetches or scores.get(cand, 0) < 8:
                return None
            fetches += 1
            used.add(cand)
            st = fs.fetch_one(cand, refs, fallbacks, start)
            stories[cand] = st
            if readable(st):
                return cand
        return None

    for i, fid in enumerate(list(sel["frontpage"])):
        if not readable(stories.get(fid)):
            new = backfill(section_of.get(fid))
            if new:
                sel["frontpage"][i] = new
                print(f"  front: swapped unreadable {fid} -> {new}")
    for s in sel["sections"]:
        keep = []
        for sid in s["stories"]:
            if readable(stories.get(sid)):
                keep.append(sid)
                continue
            new = backfill(s["slug"])
            if new:
                keep.append(new)
                print(f"  {s['slug']}: swapped unreadable {sid} -> {new}")
            else:
                s["also"] = [sid] + s["also"]           # a line, not an empty card
                print(f"  {s['slug']}: {sid} unreadable, demoted to a one-liner")
        s["stories"] = keep
    sel["frontpage"] = [f for f in sel["frontpage"] if readable(stories.get(f))] or sel["frontpage"]


def promote_readable_lead(sel: dict, selected: dict) -> None:
    """Never lead with a story we could not actually read. If the lead's text
    fetch failed (paywall/bot-block), the best-ranked lead contender that DID
    come back in full takes the lead; the original moves to the front page.
    (Trial of 23 Sep 2026: the chosen lead yielded only its lede, and the
    lead card came out one thin bullet long.)"""
    st = selected.get("stories") or {}
    if readable(st.get(sel["lead"])):
        return
    for cand in sel.get("lead_contenders") or []:
        if cand != sel["lead"] and readable(st.get(cand)):
            old = sel["lead"]
            sel["frontpage"] = [old] + [i for i in sel["frontpage"] if i not in (old, cand)]
            for s in sel["sections"]:
                s["stories"] = [i for i in s["stories"] if i != cand]
            sel["lead"] = cand
            sel["lead_reason"] = (f"(promoted: original lead {old} could not be read in full) "
                                  + sel.get("lead_reason", ""))
            print(f"  lead swapped to {cand}: original lead's text was unavailable")
            return


# ---------------------------------------------------------------- writer
_NAV = re.compile(r"^(?:advertisement|read more|also read|subscribe|sign up|share this|"
                  r"follow us|related:?|recommended|image:|photo:|watch:|listen:)\b", re.I)


def clean_text(t: str) -> str:
    """Remove page furniture the extractor lets through (nav lines, repeated
    lines, 'Also read' links). Cleaning, NOT shortening: article sentences are
    never cut (see memory: no text caps)."""
    out, seen = [], set()
    for line in (t or "").splitlines():
        s = line.strip()
        if not s or s in seen or _NAV.match(s):
            continue
        if len(s) < 40 and not re.search(r"[.!?:”\"]$", s) and not re.search(r"\d", s):
            continue                                   # stray menu/caption fragment
        seen.add(s)
        out.append(s)
    return "\n".join(out)


def writer_input(sel: dict, selected: dict) -> str:
    stories = selected.get("stories") or {}
    parts = [f"DATE: {sel['date']}", "Write every story below in the place given.",
             "Output JSON only."]

    def block(role, sid, section=None, extra=""):
        s = stories.get(sid) or {}
        head = (f"\n=== {role}{' | section: ' + section if section else ''} | id: {sid}\n"
                f"headline: {s.get('headline', '')}\nsource: {s.get('source', '')} | "
                f"published: {s.get('published', '')} | text_source: {s.get('text_source', 'none')}{extra}")
        return head + "\n" + clean_text(s.get("fulltext", ""))

    parts.append(block("LEAD", sel["lead"]))
    for i in sel["frontpage"]:
        parts.append(block("FRONT", i))
    for sec in sel["sections"]:
        for i in sec["stories"]:
            parts.append(block("CARD", i, sec["slug"]))
        for i in sec["also"]:
            parts.append(block("ALSO (one line only)", i, sec["slug"]))
    for i in sel["opportunities"]:
        when = (sel.get("opp_when") or {}).get(i)
        # the listing site's structured date is authoritative; page text often omits it
        parts.append(block("OPPORTUNITY", i, extra=f" | date (from the listing, reliable): {when}" if when else ""))
    for i in sel["longform"]:
        parts.append(block("LONGFORM (write as a card in section 'longform', add why it is worth 20 minutes)", i))
    return "\n".join(parts)


def validate_draft(draft: dict, sel: dict) -> dict:
    allowed = {sel["lead"], *sel["frontpage"], *sel["opportunities"], *sel["longform"]}
    for s in sel["sections"]:
        allowed.update(s["stories"]); allowed.update(s["also"])
    draft["date"] = sel["date"]
    draft.pop("brief", None)
    if not isinstance(draft.get("lead"), dict) or draft["lead"].get("id") not in allowed:
        raise ValueError("draft has no valid lead")
    draft["frontpage"] = [x for x in draft.get("frontpage") or [] if x.get("id") in allowed]
    secs = []
    for s in draft.get("sections") or []:
        s["stories"] = [x for x in s.get("stories") or [] if x.get("id") in allowed]
        s["also"] = [x for x in s.get("also") or [] if x.get("id") in allowed and x.get("line")]
        if s["stories"] or s["also"]:
            secs.append(s)
    draft["sections"] = secs
    draft["opportunities"] = [x for x in draft.get("opportunities") or [] if x.get("id") in allowed]
    return draft


def wire_edition(sel: dict, selected: dict) -> dict:
    """No-AI backup: the editor's picks, each with its headline and the first
    two sentences of the article. Plain, honest, never blank."""
    stories = selected.get("stories") or {}

    def card(i):
        s = stories.get(i) or {}
        text = clean_text(s.get("fulltext", ""))
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text)[:2]
        summ = " ".join(sents).strip() if text else \
            f"{s.get('headline', '')} (source unreachable — headline only)"
        return {"id": i, "headline": s.get("headline", ""), "summary": summ[:600], "badge": "WIRE"}

    return {"date": sel["date"], "backup": True, "lead": card(sel["lead"]),
            "frontpage": [card(i) for i in sel["frontpage"]],
            "sections": [{"slug": s["slug"], "stories": [card(i) for i in s["stories"]],
                          "also": [{"id": i, "line": (stories.get(i) or {}).get("headline", "")}
                                   for i in s["also"]]} for s in sel["sections"]],
            "opportunities": []}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["trial", "live"], default=os.environ.get("NEWSROOM_MODE", "trial"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--editor-model", default=os.environ.get("EDITOR_MODEL") or "claude-opus-5-5")
    ap.add_argument("--writer-model", default=os.environ.get("WRITER_MODEL") or "claude-sonnet-5")
    ap.add_argument("--editor-effort", default=os.environ.get("EDITOR_EFFORT") or "low")
    ap.add_argument("--writer-effort", default=os.environ.get("WRITER_EFFORT") or "low")
    ap.add_argument("--stop-after", choices=["editor", "fetch", "writer"], default="writer")
    a = ap.parse_args()

    digest = json.loads(DIGEST_LEAN.read_text())
    date = a.date or ist_today()
    if digest.get("date") != date:
        print(f"note: digest is dated {digest.get('date')}, paper date {date}")
    digest["date"] = date
    base = ROOT / ("trial" if a.mode == "trial" else "")
    sel_dir = base / "selections"
    sel_dir.mkdir(parents=True, exist_ok=True)

    print(f"[newsroom] {date} mode={a.mode} editor={a.editor_model}/{a.editor_effort} "
          f"writer={a.writer_model}/{a.writer_effort}")

    # 1 — page-one meeting
    try:
        raw = call_claude("editor", (PROMPTS / "editor.md").read_text(), editor_input(digest),
                          a.editor_model, a.editor_effort, date)
    except Exception as ex:                                      # noqa: BLE001
        print(f"  !! editor failed ({ex}) — falling back to the ranking script's own order")
        raw = code_ranked_selection(digest)
    sel = validate_selection(raw, digest)
    (sel_dir / f"{date}.json").write_text(json.dumps(
        {k: v for k, v in sel.items() if not k.startswith("_")}, indent=2, ensure_ascii=False))
    (sel_dir / f"{date}.scores.json").write_text(json.dumps(raw.get("scores") or {}, ensure_ascii=False))
    n_full = 1 + len(sel["frontpage"]) + sum(len(s["stories"]) for s in sel["sections"])
    print(f"  editor picked lead + {len(sel['frontpage'])} front, {n_full} full cards, "
          f"{sum(len(s['also']) for s in sel['sections'])} one-liners, {len(sel['opportunities'])} opps")
    print(f"  lead: {sel['lead']} — {sel.get('lead_reason', '')}")
    if a.stop_after == "editor":
        return

    # 2 — full text for the picks (same recovery chain as select.yml)
    import fetch_selected as fs
    fs.SELECTIONS_DIR = sel_dir
    fs.SELECTED_DIR = base / "feeds" / "selected" if a.mode == "trial" else fs.SELECTED_DIR
    sys.argv = ["fetch_selected.py", date]
    fs.main()
    selected = json.loads((fs.SELECTED_DIR / f"{date}.json").read_text())
    promote_readable_lead(sel, selected)
    replace_unreadable_cards(sel, selected, fs)
    (fs.SELECTED_DIR / f"{date}.json").write_text(json.dumps(selected, indent=2, ensure_ascii=False))
    (sel_dir / f"{date}.json").write_text(json.dumps(
        {k: v for k, v in sel.items() if not k.startswith("_")}, indent=2, ensure_ascii=False))
    if a.stop_after == "fetch":
        return

    # 3 — writing
    drafts = base / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    try:
        draft = call_claude("writer", (PROMPTS / "writer.md").read_text(),
                            writer_input(sel, selected), a.writer_model, a.writer_effort, date)
        draft = validate_draft(draft, sel)
    except Exception as ex:                                      # noqa: BLE001
        print(f"  !! writer failed ({ex}) — publishing the no-AI wire edition instead")
        draft = wire_edition(sel, selected)
    (drafts / f"{date}.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
    usage_check(date)
    print(f"  wrote {(drafts / f'{date}.json').relative_to(ROOT)}"
          f"{' (BACKUP wire edition)' if draft.get('backup') else ''}")


if __name__ == "__main__":
    main()
