#!/usr/bin/env python3
"""Expand the routine's lean DRAFT into the full edition JSON the renderer reads.

WHY THIS EXISTS
---------------
The routine writes the smallest thing it possibly can: a draft of story IDs +
the prose it wrote (headline/summary/signal). It writes NO urls, NO image
links, NO source strings, NO colophon, NO edition number. This:
  * minimises the model's OUTPUT tokens, and
  * makes fabricated urls/images structurally impossible — the model never emits
    one; we inject them here, verbatim, from a refs snapshot (built on GitHub
    Actions during the fetch).

This script runs at PUBLISH time (GitHub Actions, free compute).

A2 — per-date refs. Each draft resolves against ITS OWN date's
`feeds/refs/<date>.json` snapshot first. If an id isn't found there (e.g. the
snapshot has aged out, or a very old draft predates the refs/ directory), it
falls back to the UNION of all available snapshots (safe because ids are
content-hash based — same id always means the same URL — see build_digest.py
`make_id`), then to the legacy rolling `feeds/refs.json`.

A3 — editions are immutable. By default this script assembles ONLY drafts
that do not already have a published edition (checked against
`EXISTING_EDITIONS_DIR`, a checkout of gh-pages' `editions/` the workflow
provides). A never-before-seen draft is written for the first time; a draft
whose edition already exists is left untouched. Pass `--force <date>` (one or
more, or `--force all`) to deliberately rebuild specific dates — the only way
past editions change.

Idempotent within its own rules: re-running with the same inputs and the same
`--force` set produces the same output. If a draft is malformed we FAIL LOUDLY
(non-zero exit) so the deploy step does not ship a broken paper. If the model
references an unknown id, that single story is dropped with a warning rather
than crashing the whole edition.
"""
import argparse
import json
import re
import sys
import datetime
import pathlib
import os

ROOT = pathlib.Path(__file__).resolve().parent.parent
DRAFTS = pathlib.Path(os.environ.get("DC_DRAFTS_DIR", str(ROOT / "drafts")))
REFS_DIR = ROOT / "feeds" / "refs"                     # A2 per-date snapshots
REFS_FILE_LEGACY = ROOT / "feeds" / "refs.json"        # legacy rolling union
LATEST = ROOT / "feeds" / "latest.json"
DIGEST = ROOT / "feeds" / "digest.json"
MARKETS_FILE = ROOT / "feeds" / "markets.json"         # B5, optional
OUT_DIR = pathlib.Path(os.environ.get("DC_OUT_DIR", str(ROOT / "site" / "editions")))

# A3: where the workflow checks out gh-pages' editions/ before we run, so we
# can tell which dates are already published. Override via env for testing.
EXISTING_EDITIONS_DIR = pathlib.Path(
    os.environ.get("EXISTING_EDITIONS_DIR", str(ROOT / "existing_editions" / "editions")))

# Edition numbering is deterministic — no stored counter to drift or corrupt.
# Seed: 2026-06-16 == edition 1 (so 2026-06-18 == edition 3, matching history).
EPOCH = datetime.date(2026, 6, 16)

# Wrong-link guard: if the editor's card shares NO significant word with the
# source title behind its id, the id was almost certainly mismatched — drop it.
#
# The check deliberately reads the WHOLE card (headline + summary + key_stat),
# not the headline alone. The prompt requires rewriting the outlet's clickbait
# into plain language, so a headline-only comparison punishes correct work: on
# 2026-07-22 it dropped the edition's entire lead because the editor's
# "Skyroot's Vikram-1 puts India in the private orbital-launch club" shares no
# 4+-letter non-stopword with SCMP's "SpaceX took 4 attempts. India's space
# start-up needed just 1" ("india" being a stopword here). A correct card
# always names the article's entities somewhere in its prose, so the wider
# comparison keeps the guard's real job — catching a mismatched id — while
# ending the false positives.
_WORD = re.compile(r"[a-z0-9]{4,}")
_STOP = {"with", "that", "this", "from", "have", "will", "into", "amid", "over",
         "after", "says", "said", "than", "then", "when", "what", "your", "their",
         "about", "more", "most", "also", "been", "could", "would", "year", "years",
         "india", "indian", "news", "report", "first", "global"}


def sig_tokens(s):
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP}


def coerce_points(v):
    """v6 bullet summaries. A list of 3-5 short strings; anything else becomes
    an empty list so an older draft (summary-only) still assembles cleanly."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


# --- v6.1: derive hook + points server-side when the draft didn't supply them.
#
# The routine prompt lives in the Claude Code Routine's own configuration, NOT
# in this repo, so editing ROUTINE_PROMPT_B.md changes nothing until a human
# re-pastes it. That dependency silently produced two consecutive old-format
# editions (29 Jul, 01 Aug 2026). The format must not hinge on a manual step.
#
# So: if a story arrives with a `summary` and no `points`, split it here. A
# model-written hook/points is better prose and still wins when the prompt IS
# current — this is the floor, not the ceiling.
# Mirrors _SRC_FLAG_RE in site/app.js — the editor's provenance flags.
_SRC_FLAG_RE = re.compile(
    r"\((?:source unreachable[^)]*|summary from limited source text)\)\s*$", re.I)

_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“‘])')
# Deliberately not splitting on "$1.5B" or "Rs. 5" — the lookahead requires a
# capital letter or quote after the space, so a decimal or digit never splits.

# --- emphasis engine (v6.2) --------------------------------------------------
#
# WHAT gets marked, and WHY. The old fallback highlighted the first bare number
# it found, independently in the headline AND the hook — so "$75M" lit up twice
# on the same card, and a lone "2025" or "30" got the pen for no reason. That
# is not emphasis, it is noise. The rule now is editorial, not lexical:
#
#   YELLOW highlight  = THE CLAIM. The single phrase that says why the story
#                       matters — a milestone ("India's first private orbital
#                       launch"), or a number WELDED TO WHAT IT MEASURES
#                       ("8,133 GWh of solar power", "hits $5tn"). A bare
#                       number, a lone percentage, or a year never qualifies,
#                       because a number with no noun tells the reader nothing.
#   BLACK underline   = THE CONSEQUENCE. One forward-looking, QUANTIFIED
#                       implication drawn from the bullets ("will add 26.3 GW
#                       of demand", "projected to reach 320 million people").
#                       The number is the precision gate: a vague "now stands"
#                       clause is skipped rather than marked badly.
#
# ONE of each per story, never the same fact twice. The highlight goes in the
# headline when the headline carries the claim, otherwise the hook — never
# both. When nothing clears the bar, nothing is marked; a clean card beats a
# decorated one. Validated against 113 real stories: ~35% highlighted, ~7%
# underlined, every mark reading as deliberate.
#
# This is the FLOOR. A current ROUTINE_PROMPT_B.md, where the model does the
# real reasoning, still wins — its ==/__ markers are respected and this never
# overrides them.

# A milestone: a superlative bound to the noun it heads.
_SUPER = re.compile(
    r"\b(?:world's |india's |asia's |china's |country's |its |a )?"
    r"(?:first|largest|biggest|smallest|record(?:-\w+)?|record[ -]low|"
    r"record[ -]high|highest|lowest|fastest|slowest|worst|maiden|"
    r"three-month low|all-time (?:high|low)|\w+-month (?:low|high))"
    r"(?:[ -](?:its |the |a )?[a-z][\w'-]*){1,4}", re.I)

# A number welded to its unit/noun. Bare integers, lone percentages, and years
# are deliberately NOT matched here — they were the whole source of the noise.
_CUR   = r"[$₹€£]\s?\d[\d,.]*\s?(?:tn|bn|trillion|billion|million|crore|lakh|k|m|b)?"
_UNIT  = r"\d[\d,.]*\s?(?:GWh|TWh|GW|MW|kWh|Wh/km|km|kg|tonnes?|/month|/mo|-a-month)"
_PCTOF = r"\d[\d,.]*%\s+of\s+[a-z][\w'-]*(?:[ -][a-z][\w'-]*){0,2}"
_NUMNOUN = re.compile(
    rf"(?:(?:hits?|hit|raised?|raises?|valued at|worth|reached|tops?|topped|"
    rf"secured|posted|crosses?|closes?|closed)\s+)?"
    rf"(?:{_CUR}|{_UNIT})(?:\s+(?:of\s+)?[a-z][\w'-]*){{0,2}}"
    rf"|{_PCTOF}", re.I)

# A quantified consequence for the underline.
_CONSEQ = re.compile(
    r"\b(?:could|will|would|expects? to|plans? to|aims? to|set to|means|"
    r"risks?|threatens? to|projected to|forecast to)\b"
    r"(?:[ -][\w'’%$₹.-]+(?:,\d{3})*){1,7}", re.I)

_STOP_EDGE = {"and", "the", "in", "on", "to", "for", "its", "by", "as", "at",
              "that", "a", "an", "with", "after", "ahead", "even", "amid",
              "over", "from", "but", "while", "of", "their", "his", "her",
              "our", "into", "than", "this", "when", "where"}


def _trim_phrase(phrase: str) -> str:
    """Strip leading/trailing filler and cut a trailing 'as …'/'after …' clause
    so a mark is a phrase, not half a sentence."""
    words = phrase.split()
    # cut a trailing subordinate clause ("hits $5tn AS AI-stock sell-off …")
    for i in range(1, len(words)):
        if words[i].lower().strip(".,;:") in {"as", "after", "while", "amid",
                                              "even", "ahead", "following"}:
            words = words[:i]
            break
    while words and words[0].lower().strip(".,;:") in _STOP_EDGE:
        words.pop(0)
    while words and words[-1].lower().strip(".,;:") in _STOP_EDGE:
        words.pop()
    return " ".join(words).strip(" .,;:—-")


def highlight_phrase(text: str):
    """The claim to highlight, or None. Milestone first, then number-with-noun."""
    t = (text or "").strip()
    if not t or _SRC_FLAG_RE.search(t):
        return None
    m = _SUPER.search(t)
    if m:
        p = _trim_phrase(m.group(0))
        if len(p.split()) >= 2:
            return p
    m = _NUMNOUN.search(t)
    if m:
        p = _trim_phrase(m.group(0))
        # reject a bare year and anything that lost its noun to the trim
        if re.fullmatch(r"(?:19|20)\d\d", p):
            return None
        has_word = any(re.search(r"[a-zA-Z]", w) and
                       not re.fullmatch(r"[\d,.$₹€£%/-]+", w) and
                       not re.fullmatch(r"[$₹€£]?[\d,.]+(?:tn|bn|b|m|k|mn|cr|crore|lakh|"
                                        r"trillion|billion|million|%)?[,.;:]?", w, re.I)
                       for w in p.split())
        has_num = bool(re.search(r"\d", p))
        if has_word and has_num:
            return p
    return None


def underline_phrase(points, avoid: str = ""):
    """A quantified consequence from the bullets, or None. Skips a clause that
    just restates the highlighted claim."""
    avoid_toks = set(re.findall(r"[a-z0-9]+", (avoid or "").lower()))
    for p in points or []:
        for m in _CONSEQ.finditer(p):
            span = _trim_phrase(m.group(0))
            toks = span.split()
            if len(toks) < 3 or not re.search(r"\d", span):
                continue                       # quantified consequences only
            span_toks = set(re.findall(r"[a-z0-9]+", span.lower()))
            if span_toks and span_toks <= avoid_toks:
                continue                       # don't re-mark the claim
            return span
    return None


def _wrap_first(text: str, phrase: str, left: str, right: str) -> str:
    """Wrap the first literal occurrence of `phrase` in `text`. No-op if the
    field is already marked or the phrase isn't found verbatim."""
    if not phrase or left in text:
        return text
    i = text.find(phrase)
    if i < 0:
        return text
    return f"{text[:i]}{left}{phrase}{right}{text[i + len(phrase):]}"

MIN_POINTS = 2          # below this a bullet list is sillier than a paragraph
MAX_POINTS = 5

# Counts stories whose bullets this script had to derive. A non-zero count at
# the end of a run is the signal that Routine B is running a stale prompt.
DERIVED = {"count": 0, "total": 0}


# v7: the old splitter broke on every initial or title — "Andhra Pradesh CM
# N." / "Chandrababu Naidu has approved…", "Capt." / "Tim Hawkins said…",
# "Lindsey O." / "Graham Sanctioning Russia…" (13 broken hooks in 12 audited
# editions). Re-join any split whose left side ends in an abbreviation.
_ABBREV_END = re.compile(
    r"(?:\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|Prof|Capt|Col|Gen|Lt|Maj|Sgt|Adm|Gov|Sen|Rep|"
    r"St|Jr|Sr|Inc|Ltd|Co|Corp|No|vs|Rs|Mt|Ft|approx|est|Jan|Feb|Mar|Apr|Jun|Jul|"
    r"Aug|Sep|Sept|Oct|Nov|Dec|U\.S|U\.K|U\.N|E\.U|a\.m|p\.m|i\.e|e\.g|Bros|Hon|Smt|Shri))\.$")


def split_sentences(text: str):
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p.strip()]
    out = []
    for p in parts:
        if out and _ABBREV_END.search(out[-1]):
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def split_summary(summary: str):
    """(hook, points) from a single-paragraph summary, or (None, []) if it is
    too short or too thin to be worth splitting."""
    text = (summary or "").strip()
    if not text or _SRC_FLAG_RE.search(text):
        # Provenance-flagged summaries ("source unreachable — headline only")
        # are one honest sentence by design. Never bullet them.
        return None, []
    parts = split_sentences(text)
    if len(parts) < MIN_POINTS + 1:
        return None, []
    hook, rest = parts[0], parts[1:]
    if len(rest) > MAX_POINTS:
        # Fold the tail into the last bullet rather than dropping any of it —
        # never silently lose text the editor wrote.
        rest = rest[:MAX_POINTS - 1] + [" ".join(rest[MAX_POINTS - 1:])]
    return hook, rest


def apply_emphasis(story):
    """Add ONE yellow highlight (the claim) and ONE black underline (a
    quantified consequence) to a story, unless the editor already marked it.
    Coordinated so the same fact is never marked twice. Mutates `story`."""
    already_hl = "==" in (story.get("headline", "") + (story.get("hook") or ""))
    already_ul = "__" in ((story.get("hook") or "") +
                          "".join(story.get("points") or []))

    claim = ""
    if not already_hl:
        # Prefer the headline; fall back to the hook. Never both.
        hp = highlight_phrase(story.get("headline", ""))
        if hp:
            story["headline"] = _wrap_first(story["headline"], hp, "==", "==")
            claim = hp
        elif story.get("hook"):
            kp = highlight_phrase(story["hook"])
            if kp:
                story["hook"] = _wrap_first(story["hook"], kp, "==", "==")
                claim = kp
    if not already_ul and story.get("points"):
        up = underline_phrase(story["points"], avoid=claim)
        if up:
            story["points"] = [_wrap_first(p, up, "__", "__") if up in p else p
                               for p in story["points"]]
    return story


# --- v7 copy-desk guardrails --------------------------------------------------
# Deterministic, run on every draft whatever prompt the routine is on. Each one
# is a measured failure from the 24 Aug-23 Sep audit (12 editions, ~450 cards).
HOOK_MAX_WORDS = 38
POINT_MAX_WORDS = 50
# "worth watching/tracking" closed 40+ signal bullets in 12 editions — the
# single strongest machine-written tell. A bullet that is ONLY that goes; a
# bullet that says WHAT to watch keeps its content and loses the filler.
_FILLER_ONLY = re.compile(
    r"^\s*(?:(?:this is |it'?s |a story |one )?(?:worth|one worth) (?:watching|tracking|"
    r"following|remembering|a follow-up|keeping an eye on)|useful context,? not "
    r"(?:personally )?actionable|not (?:personally )?actionable|the (?:real )?signal "
    r"is (?:clear|here)|watch this space)[.!]?\s*$", re.I)
_FILLER_TAIL = re.compile(
    r"\s*[—–-]+\s*(?:worth (?:watching|tracking|following|remembering))[.!]?\s*$|"
    r"[;,]\s*(?:worth (?:watching|tracking|following))[.!]?\s*$", re.I)
_FILLER_LEAD = re.compile(r"^\s*(?:worth (?:watching|tracking)(?: is)?|the thing to watch "
                          r"is|the number to (?:remember|watch)(?: is)?)\s*[:—–-]?\s*", re.I)
OG_IMAGES = {}   # id -> article preview image (from feeds/selected), filled per edition
QUALITY = {"hooks_split": 0, "points_split": 0, "signal_filler": 0,
           "editors_read_stripped": 0, "headline_only_commentary": 0}


def _words(t):
    return len((t or "").split())


def tidy_hook_points(hook, points):
    """Split an overstuffed hook / bullet at its first semicolon clause."""
    if hook and _words(hook) > HOOK_MAX_WORDS and "; " in hook:
        head, tail = hook.split("; ", 1)
        if _words(head) >= 8 and _words(tail) >= 6:
            hook = head.rstrip(" ,") + "."
            points = [tail[:1].upper() + tail[1:]] + list(points or [])
            QUALITY["hooks_split"] += 1
    out = []
    for p in points or []:
        if _words(p) > POINT_MAX_WORDS and "; " in p:
            a, b = p.split("; ", 1)
            if _words(a) >= 8 and _words(b) >= 6:
                out += [a.rstrip(" ,") + ".", b[:1].upper() + b[1:]]
                QUALITY["points_split"] += 1
                continue
        out.append(p)
    return hook, out[:MAX_POINTS + 1]


def clean_signal(signal):
    out = []
    for b in signal or []:
        if _FILLER_ONLY.match(b):
            QUALITY["signal_filler"] += 1
            continue
        nb = _FILLER_LEAD.sub("", _FILLER_TAIL.sub(".", b)).strip()
        if nb != b.strip():
            QUALITY["signal_filler"] += 1
        if nb and _words(nb) >= 4:
            out.append(nb[:1].upper() + nb[1:])
    return out


def coerce_signal(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def load_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as ex:
        raise SystemExit(f"FATAL: {path} is not valid JSON: {ex}")


def edition_number(date_str: str) -> int:
    try:
        d = datetime.date.fromisoformat(date_str)
        return max(1, (d - EPOCH).days + 1)
    except ValueError:
        return 1


def slug_to_name(refs_digest):
    m = {}
    for s in (refs_digest or {}).get("sections", []) or []:
        if s.get("slug"):
            m[s["slug"]] = s.get("name", s["slug"].title())
    return m


def load_refs_for_date(date_str: str) -> dict:
    """A2: this date's own snapshot, falling back to the union of every other
    available snapshot, then the legacy rolling refs.json. Hash ids make the
    union safe — an id collision across dates would mean the same URL."""
    own = load_json(REFS_DIR / f"{date_str}.json", default=None)
    union = {}
    if REFS_DIR.exists():
        for f in sorted(REFS_DIR.glob("*.json")):
            snap = load_json(f, default={}) or {}
            union.update(snap)
    legacy = load_json(REFS_FILE_LEGACY, default={}) or {}
    merged = {}
    merged.update(legacy)
    merged.update(union)
    if own:
        merged.update(own)   # own-date snapshot takes precedence when present
    return merged


def build_story(item, refs, warnings, allow_read=True, text_source=None):
    """Merge model prose with the verbatim url/image/source from refs[id]."""
    text_source = text_source or {}
    og_images = OG_IMAGES
    sid = item.get("id")
    ref = refs.get(sid)
    if ref is None:
        warnings.append(f"unknown story id {sid!r} — dropped")
        return None
    headline = item.get("headline", ref.get("title", ""))
    card_text = " ".join(str(x) for x in (
        headline,
        item.get("summary", ""),
        " ".join(coerce_points(item.get("points"))),
        item.get("hook", ""),
        item.get("key_stat", ""),
    ) if x)
    ctok, rtok = sig_tokens(card_text), sig_tokens(ref.get("title", ""))
    # WARN, never drop. Replayed against 221 real story items from the five
    # editions of 19-28 Jul 2026, this check fired 6 times and was wrong all 6
    # — its precision on real data is zero, because an abstract source title
    # ("AI monetization, model efficiency, and India's infrastructure gap")
    # legitimately shares no token with a plain-language rewrite ("AI earnings
    # season shows real revenue"). Silently deleting a correct story — once an
    # entire edition's lead — is a far worse failure than shipping a warning.
    # The link itself is still safe by construction: urls come from the refs
    # snapshot, never from the model, so the worst case here is a card whose
    # id was mistyped, which this warning surfaces for a human to check.
    if ctok and len(rtok) >= 2 and not (ctok & rtok):
        warnings.append(f"id {sid!r}: card shares no word with its source title "
                        f"— CHECK THE LINK ({headline[:50]!r} vs "
                        f"{ref.get('title','')[:50]!r})")
    story = {
        "headline": headline,
        "summary": item.get("summary", ""),
        "signal": coerce_signal(item.get("signal")),
        "badge": (item.get("badge") or "").strip(),
        # legacy fields kept for older drafts that still send them
        "takeaway": item.get("takeaway", ""),
        "why": item.get("why", ""),
        "source": ref.get("source", ""),
        "url": ref.get("url"),
        "image": ref.get("image") or og_images.get(sid),      # v8.3: article's own preview image
        "developing": bool(item.get("developing", False)),
    }
    # v6 bullet summary — additive. `hook` is the one-line numbers-first
    # opener, `points` the 3-5 bullets under it. The renderer falls back to
    # `summary` whenever `points` is absent, so every already-published
    # edition keeps rendering exactly as it does today.
    hook = (item.get("hook") or "").strip()
    points = coerce_points(item.get("points"))
    if not points:
        # v6.1 fallback: the editor sent an old-format single-paragraph
        # summary. Split it here rather than shipping a wall of text.
        derived_hook, derived_points = split_summary(story["summary"])
        if derived_points:
            hook = hook or derived_hook
            points = derived_points
            story["summary"] = ""      # the bullets now carry it; don't double up
            DERIVED["count"] += 1
    hook, points = tidy_hook_points(hook, points)
    if hook:
        story["hook"] = hook
    if points:
        story["points"] = points
    story["signal"] = clean_signal(story["signal"])

    # Emphasis (yellow claim + black consequence) — must not depend on a
    # hand-pasted prompt, so it runs here after hook/points are settled, and
    # respects any ==/__ the editor already placed.
    apply_emphasis(story)

    # B3 optional fields — additive, renderer must degrade gracefully if absent.
    if item.get("key_stat"):
        story["key_stat"] = str(item["key_stat"]).strip()
    if item.get("editors_read"):
        if allow_read:
            story["editors_read"] = str(item["editors_read"]).strip()
        else:
            QUALITY["editors_read_stripped"] += 1
    # Headline-only card: the writer had NOTHING but a headline, so any
    # "so what" or analysis is invented (10 of 17 such cards carried one).
    flagged = _SRC_FLAG_RE.search(" ".join([story.get("summary", ""), story.get("hook", "")] +
                                           story.get("points", [])))
    if flagged or text_source.get(sid) == "none":
        if story.get("signal") or story.get("editors_read"):
            QUALITY["headline_only_commentary"] += 1
        story["signal"] = []
        story.pop("editors_read", None)
    rail = build_also_rail(item.get("also"), refs, warnings)
    if rail:
        story["also"] = rail
    return story


def build_also_rail(also, refs, warnings):
    """Resolve a list of {id, line} one-liners into rail entries with urls.
    Used for both the section-level rail (the shape the prompt asks for) and
    the legacy story-level one."""
    if not isinstance(also, list) or not also:
        return []
    out = []
    for a in also:
        aid = a.get("id") if isinstance(a, dict) else None
        aref = refs.get(aid) if aid else None
        if not aref:
            warnings.append(f"'also' id {aid!r} unknown — dropped from rail")
            continue
        line = (a.get("line") or "").strip()
        if not line:
            warnings.append(f"'also' id {aid!r} has no line — dropped from rail")
            continue
        out.append({
            "line": line,
            "source": aref.get("source", ""),
            "url": aref.get("url"),
        })
    return out


def build_opp(item, refs, warnings):
    sid = item.get("id")
    ref = refs.get(sid, {})
    if sid and ref == {}:
        warnings.append(f"unknown opportunity id {sid!r} — using draft fields only")
    return {
        "name": item.get("name", ref.get("title", "")),
        "when": item.get("when", ""),
        "summary": item.get("summary", ""),
        "source": ref.get("source", ""),
        "url": ref.get("url"),
    }


def existing_edition_dates() -> set:
    if not EXISTING_EDITIONS_DIR.exists():
        return set()
    return {f.stem for f in EXISTING_EDITIONS_DIR.glob("*.json") if f.stem != "index"}


def assemble_one(draft_path, names, colophon, markets, warnings_out=None):
    draft = load_json(draft_path)
    if not isinstance(draft, dict):
        raise SystemExit(f"FATAL: {draft_path} did not parse to an object")

    date = draft.get("date") or draft_path.stem
    refs = load_refs_for_date(date)
    warnings = []
    DERIVED["count"] = 0    # per-edition: main() processes multiple drafts in
                             # one run, and this must not accumulate across them

    for k in QUALITY:
        QUALITY[k] = 0
    sel = load_json(pathlib.Path(os.environ.get("DC_SELECTED_DIR", str(ROOT / "feeds" / "selected")))
                    / f"{date}.json", default={}) or {}
    ts = {k: (v or {}).get("text_source") for k, v in (sel.get("stories") or {}).items()}
    OG_IMAGES.clear()
    OG_IMAGES.update({k: v["og_image"] for k, v in (sel.get("stories") or {}).items()
                      if isinstance(v, dict) and v.get("og_image")})

    lead = None
    if draft.get("lead"):
        lead = build_story(draft["lead"], refs, warnings, True, ts)

    # editors_read: lead + the top two front-page stories only (violated on
    # every audited day).
    frontpage = [s for s in (
        build_story(x, refs, warnings, i < 2, ts)
        for i, x in enumerate(draft.get("frontpage", []) or [])
    ) if s]

    sections = []
    for sec in draft.get("sections", []) or []:
        slug = sec.get("slug", "")
        stories = [s for s in (
            build_story(x, refs, warnings, False, ts) for x in sec.get("stories", []) or []
        ) if s]
        if not stories:
            continue
        out_sec = {
            "name": names.get(slug, slug.replace("-", " ").title()),
            "slug": slug,
            "stories": stories,
        }
        # The draft carries `also` at SECTION level (see the schema in
        # ROUTINE_PROMPT_B.md) and site/app.js renders `sec.also`. Until v6
        # this loop never copied it across, so every also-rail the editor
        # wrote was silently discarded here — 49 of 49 over the five editions
        # audited on 2026-07-29, despite the full text having been fetched for
        # each one. build_story()'s story-level `also` handling is kept for
        # older drafts that put the rail on a story instead.
        rail = build_also_rail(sec.get("also"), refs, warnings)
        if rail:
            out_sec["also"] = rail
        sections.append(out_sec)

    opportunities = [build_opp(x, refs, warnings)
                     for x in draft.get("opportunities", []) or []]

    edition = {
        "date": date,
        "edition": edition_number(date),
        "colophon": colophon,
        "lead": lead,
        "frontpage": frontpage,
        "sections": sections,
        "opportunities": opportunities,
    }
    if draft.get("backup"):
        edition["backup"] = True
        edition["colophon"] = ("WIRE EDITION — the writer failed today, so these are the "
                               "editor's picks with each article's opening lines. " + (colophon or ""))
    brief = draft.get("brief")
    if isinstance(brief, list) and brief:
        brief_out = []
        for b in brief:
            bid = b.get("id") if isinstance(b, dict) else None
            bref = refs.get(bid) if bid else None
            if not bref:
                warnings.append(f"brief id {bid!r} unknown — dropped")
                continue
            brief_out.append({"line": (b.get("line") or "").strip(), "id": bid,
                               "url": bref.get("url")})
        if brief_out:
            edition["brief"] = brief_out
    if markets:
        edition["markets"] = markets   # B5, injected verbatim, optional

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{date}.json"
    out.write_text(json.dumps(edition, indent=2, ensure_ascii=False))
    n_stories = (1 if lead else 0) + len(frontpage) + sum(len(s["stories"]) for s in sections)
    print(f"  {os.path.relpath(out, ROOT)}  edition {edition['edition']}  "
          f"{n_stories} story slots, {len(opportunities)} opps")
    if DERIVED["count"]:
        print(f"    !! {DERIVED['count']} of {n_stories} stories arrived WITHOUT "
              f"hook/points — bullets were derived from the paragraph summary.")
        print(f"    !! That means Routine B is running a STALE PROMPT. Re-paste "
              f"ROUTINE_PROMPT_B.md into the WRITE routine to get model-written "
              f"bullets and highlights instead of derived ones.")
    print("    copy desk: " + ", ".join(f"{k}={v}" for k, v in QUALITY.items()))
    if DERIVED["count"] and n_stories and DERIVED["count"] >= max(3, n_stories // 4):
        # v7: the stale-prompt line used to go only to a log nobody reads —
        # it fired every single day for a month. publish.yml now turns this
        # file into a GitHub issue (-> email).
        (ROOT / "publish_health.txt").write_text(
            f"{date}: {DERIVED['count']} of {n_stories} cards arrived as paragraphs "
            f"(no hook/points). The WRITE routine's pasted prompt is out of date — "
            f"re-paste ROUTINE_PROMPT_B.md.\n")
    for w in warnings:
        print(f"    warn: {w}")
    if warnings_out is not None:
        warnings_out.extend(warnings)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", nargs="*", default=[],
                         help="Date(s) YYYY-MM-DD to rebuild even if already "
                              "published, or 'all' to rebuild every draft.")
    args = parser.parse_args()
    force_all = "all" in args.force
    force_dates = set(args.force)

    names = slug_to_name(load_json(DIGEST, default={}))
    digest = load_json(DIGEST, default={}) or {}
    colophon = digest.get("colophon") or ""
    markets = load_json(MARKETS_FILE, default=None)

    drafts = sorted(DRAFTS.glob("*.json")) if DRAFTS.exists() else []
    if not drafts:
        print("No drafts to assemble — nothing to do.")
        return

    published = existing_edition_dates()
    skipped = []
    to_run = []
    for d in drafts:
        date = d.stem
        if date in published and not force_all and date not in force_dates:
            skipped.append(date)
            continue
        to_run.append(d)

    if skipped:
        print(f"Skipping {len(skipped)} already-published edition(s) "
              f"(immutable by default — use --force <date> to rebuild): "
              f"{', '.join(skipped)}")
    if not to_run:
        print("Nothing new to assemble.")
        return

    print(f"Assembling {len(to_run)} draft(s)…")
    for d in to_run:
        assemble_one(d, names, colophon, markets)


if __name__ == "__main__":
    main()
