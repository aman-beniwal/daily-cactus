#!/usr/bin/env python3
"""Turn the raw fetch (feeds/latest.json) into a LEAN digest the routine reads.

WHY THIS EXISTS
---------------
The Claude Code routine is billed by tokens, and in an agentic loop the ENTIRE
context — including whatever news file it reads — is re-sent as input on every
turn. This script does all the cheap, deterministic work HERE, on GitHub
Actions (free compute, no token cost):

  * conservative dedup (exact URL + near-identical title only),
  * cross-day dedup (drop stories already used in the last 7 days' editions),
  * buzz counting (how many outlets are carrying the same story) BEFORE dedup,
  * a gentle recall-oriented rank (recency + buzz + source weight + interest),
  * per-section max-age hard cutoff (date hygiene, redundant to fetch's own
    cutoff — catches undated items and section-specific staleness),
  * event-date extraction + hard-drop for past-dated Opportunities,
  * a per-section shortlist capped to ~2x what the paper can publish,
  * optional full-text enrichment of the shortlisted survivors (B1), and
  * trimming each candidate to {id, title, source, summary, extract?} — NO
    url, NO image.

It writes THREE kinds of files:

  feeds/digest.json        -> what the ROUTINE reads. Lean. No url, no image.
  feeds/refs/<date>.json   -> id -> {url, image, source, title, published}
                              snapshot for THIS date only (content-hash ids are
                              stable, but the snapshot makes "resolve against
                              your own date first" possible — see A2/A3).
  feeds/refs.json          -> rolling union of the last 30 days of snapshots,
                              kept for convenience / back-compat. The model
                              NEVER reads this file.

Design rule (from the v4 review): heuristics are a RECALL filter, not a
precision one. The model still makes every editorial call from the shortlist.

Fail-safe: if anything goes wrong, we degrade to a minimal empty digest rather
than crashing CI. A broken digest must never become a broken/looping routine.
"""
import glob
import hashlib
import json
import os
import re
import ssl
import datetime
import pathlib
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editorial as ed                                      # v7 editorial layer

try:
    from resolve_gnews import resolve as resolve_gnews_url   # Fix 1
except Exception:                                             # noqa: BLE001
    def resolve_gnews_url(url):                                # never block
        return url

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
LATEST = ROOT / "feeds" / "latest.json"
OPPORTUNITIES_FEED = ROOT / "feeds" / "opportunities.json"   # P7: fetch_opportunities.py output
DIGEST = ROOT / "feeds" / "digest.json"
REFS = ROOT / "feeds" / "refs.json"           # rolling union, back-compat only
REFS_DIR = ROOT / "feeds" / "refs"            # per-date snapshots (A2)
DRAFTS_DIR = ROOT / "drafts"
FEED_STATS = ROOT / "feeds" / "feed_stats.jsonl"   # B8: per-feed audit log, appended each run

SUMMARY_CHARS = 200          # enough for the editor to judge relevance; it
                             # rewrites anyway. (120 risked too-thin context.)
BUFFER_MULT = 2              # keep ~2x max_stories per section as recall buffer
SECTION_HARD_CAP = 14        # absolute ceiling per section, keeps input bounded
MAX_PER_SOURCE = 3           # diversity: no single outlet dominates a shortlist
NEAR_DUP_JACCARD = 0.6       # title token-overlap above this = same story
RECENCY_HORIZON_H = 168      # gentle 7-day recency decay
CROSS_DAY_WINDOW_DAYS = 7    # A4: don't repeat a story used in the last N days
REFS_RETAIN_DAYS = 30        # A2: keep this many days of per-date ref snapshots
DEFAULT_MAX_AGE_HOURS = 96   # A5: fallback hard cutoff when a section doesn't
                             # carry its own window_hours (belt-and-braces vs.
                             # fetch_feeds' own per-section cutoff)

# P1 fix: drafts/*.json only exist on claude/* branches and never reach main
# (automerge.yml, which used to merge them, was deleted — see ISSUES_BACKLOG.md),
# so load_recent_used() below was a silent no-op. PUBLISHED EDITIONS on
# gh-pages are the one thing that's always current and always true, so we read
# the last CROSS_DAY_WINDOW_DAYS of them over plain HTTP instead. This is
# additive: load_recent_used() (drafts/*.json) still runs too, for local runs
# where drafts exist in the working tree.
# v7: derived from the repo, never hard-coded. The hard-coded
# "amanbeni.github.io" URL started returning 404 when the GitHub account was
# renamed (first failing run: 27 Aug 2026) and dedup silently switched itself
# off for a month — 156 repeated stories. fetch.yml now also checks out the
# gh-pages branch and points EDITIONS_DIR at it, so dedup reads the editions
# straight from git and does not depend on Pages DNS at all; HTTP is the
# fallback, and a total failure prints DEDUP_BROKEN (-> a health issue).
_REPO = os.environ.get("GITHUB_REPOSITORY", "aman-beniwal/daily-cactus")
_OWNER, _NAME = (_REPO.split("/", 1) + ["daily-cactus"])[:2]
EDITIONS_BASE_URL = f"https://{_OWNER.lower()}.github.io/{_NAME}/editions"
EDITIONS_DIR = os.environ.get("EDITIONS_DIR", "")   # local gh-pages checkout, preferred
SHORTLIST_LOG = ROOT / "feeds" / "shortlist_log.jsonl"   # v7: per-source shortlist counts
SELECTED_DIR = ROOT / "feeds" / "selected"
EDITIONS_FETCH_TIMEOUT = 8   # seconds — never let a slow/dead Pages host stall CI

# P2(d): domains verified to re-date their RSS feed — i.e. report a fresh
# `published` timestamp for old content. Confirmed live (2026-07-18):
# newsonair.gov.in served `published: 2026-07-17` (today) for an article whose
# real byline is 2026-02-14, about a summit that already happened. A story
# from one of these sources is required to carry a recovered `article_date`;
# lacking one, relative-date phrasing ("this month" etc.) in its extract is
# grounds to drop it (see apply_article_date_corrections). Easy to extend —
# just add the domain (matched by substring against the story's URL).
DISTRUSTED_SOURCE_DOMAINS = {
    "newsonair.gov.in",
}

# P2(a)/(c): phrases that only make sense resolved against the ARTICLE's own
# publication date, never a (possibly lying) feed date. Used only to decide
# whether a distrusted-source story with no recoverable article_date is unsafe
# to publish — the actual ban on the model resolving these into calendar dates
# lives in ROUTINE_PROMPT.md / ROUTINE_PROMPT_B.md.
_RELATIVE_DATE_RE = re.compile(
    r"\b(this month|next week|next month|today|yesterday|tomorrow|last week|"
    r"this week|last month|this year|next year)\b", re.I)

# P2(e): same-event flooding — cap how many stories sharing an event signature
# survive a single section's shortlist. Conservative (2, not 1): the goal is
# to stop a 5-article pile-up, not to collapse two genuinely distinct angles
# on a multi-day event down to one.
EVENT_CAP_PER_SECTION = 2

# A quoted phrase ("..." / “...”) is usually a named thing (an event, a quote,
# a product) — the strongest possible clustering signal.
_QUOTED_RE = re.compile(r'["“‘]([^"”’]{4,60})["”’]')
_WORD_RE = re.compile(r"[A-Za-z0-9&-]+")
# Fallback: headlines are usually Title Case throughout, so capitalization
# alone doesn't distinguish a proper noun from an ordinary headline verb
# ("Kicks", "Raises", "Closes"). This stoplist of common headline
# verbs/connectors lets us find the longest run of capitalized words that
# ARE NOT one of these — in practice that run is the event/org/person name.
_HEADLINE_STOPWORDS = {
    "the", "a", "an", "in", "on", "at", "for", "with", "from", "of", "to",
    "and", "is", "are", "as", "by", "after", "before", "over", "under",
    "amid", "about", "into", "onto", "day", "days", "week", "weeks", "month",
    "year", "world", "global", "national", "kicks", "kicked", "off", "opens",
    "opened", "open", "closes", "closed", "close", "begins", "began", "begin",
    "ends", "ended", "end", "focuses", "focused", "focus", "inaugurates",
    "inaugurated", "hosts", "hosted", "holds", "held", "attends", "attended",
    "becomes", "became", "raises", "raised", "launches", "launched",
    "announces", "announced", "reveals", "revealed", "says", "said",
    "reports", "reported", "sees", "seen", "gets", "get", "makes", "made",
    "takes", "took", "wins", "won", "marks", "marked", "set", "sets", "plans",
    "planned", "eyes", "aims", "aimed", "seeks", "sought", "joins", "joined",
    "leads", "led", "unveils", "unveiled", "confirms", "confirmed", "second",
    "first", "final", "newest", "biggest", "largest", "top", "latest", "big",
    "new", "major",
}

TOKEN_BUDGET_CHARS = 25_000 * 4   # ~25k tokens, rough chars/4 heuristic (B1)

# Small, hand-set source-quality weights (B2). Modest — a nudge, not a filter.
# Matched case-insensitively against the entry's `source` string.
SOURCE_WEIGHTS = {
    "the economist": 1.5, "reuters": 1.5, "bloomberg": 1.3, "the guardian": 1.0,
    "ars technica": 1.0, "ieee spectrum": 1.2, "quanta magazine": 1.3,
    "nature": 1.3, "mit technology review": 1.2, "techcrunch": 0.6,
    "carbon brief": 1.0, "the hindu": 0.8, "indian express": 0.6,
    "harvard business review": 1.0, "stat": 1.0, "inc42": 0.5, "yourstory": 0.4,
    "google news": -0.4,   # syndicated aggregator listing, not an original outlet
}

# --- PR / press-release demotion (v6) --------------------------------------
# Measured, not guessed. Over 601 real non-opportunity candidates from the
# editions of 19-28 Jul 2026, marketing-phrase matching on TITLES fired on 9
# of them and was WRONG on the most important one (it flagged "India achieves
# milestone with launch of first private-sector orbital rocket" — that day's
# genuine lead). "Has no number anywhere" flagged 306 of 601 and barely
# discriminated (36.9% selected vs 43.1% for clean). So title keywords are the
# wrong instrument and are deliberately NOT used here.
#
# What DID discriminate cleanly was the SOURCE. Press-release wires and vendor
# blogs publish nothing but announcements, and the editor already rejects them
# on sight: PIB (the Indian government's press-release wire) supplied 5
# candidates and was selected 0 times; "LinkedIn" appeared as a source 6 times
# and was selected once. Demoting at the source level costs the shortlist
# nothing real and stops these crowding out reported journalism.
#
# This is a NUDGE inside score(), never a hard filter — a wire is occasionally
# the only carrier of a genuine story (a results announcement, a launch), and
# the editor still gets to see it, just lower down.
PR_SOURCE_PENALTY = -2.5
PR_SOURCES = (
    "pib", "press information bureau", "prnewswire", "pr newswire",
    "businesswire", "business wire", "globenewswire", "globe newswire",
    "einpresswire", "ein presswire", "prweb", "accesswire", "newswire",
    "openpr", "prlog", "linkedin", "medium.com", "substack.com/pub",
)
# Vendor/corporate blogs: an announcement about the vendor's own product,
# written by the vendor. Matched against the URL, since the feed's `source`
# for these is usually just the product name.
PR_URL_MARKERS = (
    "/newsroom/", "/press-release", "/press-releases/", "/media-release",
    "/company/news/", "blogs.nvidia.com", "blog.google", "openai.com/index/",
    "aws.amazon.com/blogs", "azure.microsoft.com/en-us/blog",
    "ibm.com/blog", "cloud.google.com/blog",
)

# Gentle interest nudge for ranking ONLY (never a hard filter).
INTEREST_TERMS = (
    "india", "indian", "ai", "artificial intelligence", "startup", "funding",
    "climate", "energy", "renewable", "semiconductor", "chip", "robot",
    "quantum", "agritech", "health", "biotech", "neuroscience", "economy",
    "founder", "strategy", "policy",
    "upi", "ondc", "aadhaar", "digital public", "venture", "d2c", "quick commerce",
    "drone", "defence", "space", "materials",
    "carbon", "biodiversity", "water",
    "concert", "tour", "festival", "album", "music", "exhibition", "summit",
    "fellowship", "hackathon",
)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
# "12 July 2026", "12 July", "July 12, 2026", "July 12"
_EVENT_DATE_RE = re.compile(
    r"\b(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})(?:\s+(?P<y1>20\d{2}))?"
    r"|(?P<m2>[A-Za-z]{3,9})\s+(?P<d2>\d{1,2})(?:,?\s+(?P<y2>20\d{2}))?)\b"
)


def norm_title(t: str) -> str:
    t = (t or "").lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def title_key(t: str) -> str:
    words = norm_title(t).split()
    return " ".join(words[:8])


def title_tokens(t: str) -> set:
    return set(norm_title(t).split())


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def make_id(slug: str, url: str) -> str:
    """A1: content-addressed id. Stable forever for a given URL, so a story's
    id never drifts across fetches — an old draft's id still resolves."""
    h = hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{h}"


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def extract_event_date(text: str, ref_year: int):
    """A5: best-effort event/deadline date extraction for Opportunities.
    Returns a date() or None. Picks the FIRST plausible month+day match and
    assumes ref_year unless the text names a year; if the resulting date is
    >45 days in the past relative to ref_year, tries ref_year+1 (handles
    "12 January" events announced/republished late in the previous year)."""
    m = _EVENT_DATE_RE.search(text or "")
    if not m:
        return None
    if m.group("m1"):
        day, mon_s, yr_s = m.group("d1"), m.group("m1"), m.group("y1")
    else:
        day, mon_s, yr_s = m.group("d2"), m.group("m2"), m.group("y2")
    mon = MONTHS.get(mon_s.lower())
    if not mon or not day:
        return None
    try:
        day = int(day)
        year = int(yr_s) if yr_s else ref_year
        return datetime.date(year, mon, day)
    except ValueError:
        return None


def load_json_safe(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return default


def _http_get_json(url, timeout=EDITIONS_FETCH_TIMEOUT):
    """Best-effort HTTP GET -> parsed JSON. Never raises: any network error,
    non-2xx, or malformed JSON just logs a warning and returns None so callers
    degrade to 'skip this one' rather than crashing the digest build."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "DailyCactusBot/1.0 (cross-day dedup)"})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as ex:                                   # noqa: BLE001
        print(f"cross-day dedup: warning — failed to fetch {url}: {ex!r}")
        return None


def _unused_window(now, days):
    cutoff = now.date() - datetime.timedelta(days=days)
    return cutoff


def load_published_urls(now, days=CROSS_DAY_WINDOW_DAYS):
    """P1 + v7. The last `days` of PUBLISHED editions -> (used_urls, used_keys,
    memory, ok). Prefers a local gh-pages checkout (EDITIONS_DIR, set by
    fetch.yml), falls back to HTTP against the repo-derived Pages URL. `ok`
    is False when NOTHING could be loaded although editions should exist —
    the caller prints DEDUP_BROKEN so the workflow opens a health issue
    instead of silently publishing a month of repeats again."""
    used_urls, used_keys = set(), set()
    memory = ed.RecentMemory()
    cutoff = now.date() - datetime.timedelta(days=days)
    editions = []          # (date_str, edition dict)

    local = pathlib.Path(EDITIONS_DIR) if EDITIONS_DIR else None
    if local and local.is_dir():
        for f in sorted(local.glob("*.json")):
            try:
                d = datetime.date.fromisoformat(f.stem)
            except ValueError:
                continue
            if cutoff <= d < now.date() + datetime.timedelta(days=1):
                e = load_json_safe(f, default=None)
                if isinstance(e, dict):
                    editions.append((f.stem, e))
        src = f"local {local}"
    else:
        index = _http_get_json(f"{EDITIONS_BASE_URL}/index.json")
        entries = index.get("editions") if isinstance(index, dict) else None
        for e in entries or []:
            try:
                d = datetime.date.fromisoformat((e or {}).get("date", ""))
            except (ValueError, TypeError, AttributeError):
                continue
            if d >= cutoff:
                got = _http_get_json(f"{EDITIONS_BASE_URL}/{e['date']}.json")
                if isinstance(got, dict):
                    editions.append((e["date"], got))
        src = EDITIONS_BASE_URL

    # url -> the ORIGINAL source title, so a same-story match can use the
    # outlet's headline as well as the editor's rewrite.
    url_title = {}
    if REFS_DIR.exists():
        for f in sorted(REFS_DIR.glob("*.json")):
            for ref in (load_json_safe(f, default={}) or {}).values():
                if isinstance(ref, dict) and ref.get("url"):
                    url_title[ref["url"]] = ref.get("title", "")

    for date_str, edition in editions:
        for headline, url in ed.edition_items(edition):
            if url:
                used_urls.add(url)
            if headline:
                k = title_key(headline)
                if k:
                    used_keys.add(k)
            memory.add(date_str, headline, url, url_title.get(url, ""))

    ok = bool(editions)
    print(f"cross-day dedup: {len(editions)} published edition(s) from {src} "
          f"({days}-day window) -> {len(used_urls)} urls, {len(memory)} remembered stories")
    return used_urls, used_keys, memory, ok


def load_recent_used(now, days=CROSS_DAY_WINDOW_DAYS):
    """A4: collect URLs + normalized title-keys used in the last N days of
    LOCAL drafts/*.json (working-tree only — see load_published_urls for the
    real, always-on source), resolved against that draft's own refs snapshot
    (or the union fallback). Kept as an additional source so a local run with
    drafts on disk still benefits even without network access."""
    used_urls, used_keys = set(), set()
    if not DRAFTS_DIR.exists():
        return used_urls, used_keys

    cutoff = now.date() - datetime.timedelta(days=days)
    for draft_path in sorted(DRAFTS_DIR.glob("*.json")):
        date_str = draft_path.stem
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if d < cutoff:
            continue
        draft = load_json_safe(draft_path, default=None)
        if not isinstance(draft, dict):
            continue
        snap = load_json_safe(REFS_DIR / f"{date_str}.json", default=None)

        def resolve(sid):
            if snap and sid in snap:
                return snap[sid]
            return None

        ids = []
        if draft.get("lead", {}).get("id"):
            ids.append(draft["lead"]["id"])
        ids += [x.get("id") for x in draft.get("frontpage", []) or []]
        for sec in draft.get("sections", []) or []:
            ids += [x.get("id") for x in sec.get("stories", []) or []]
        ids += [x.get("id") for x in draft.get("opportunities", []) or []]

        for sid in ids:
            if not sid:
                continue
            ref = resolve(sid)
            if ref and ref.get("url"):
                used_urls.add(ref["url"])
            if ref and ref.get("title"):
                used_keys.add(title_key(ref["title"]))
    return used_urls, used_keys


def prune_old_refs(retain_days=REFS_RETAIN_DAYS, now=None):
    if not REFS_DIR.exists():
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now.date() - datetime.timedelta(days=retain_days)
    for f in REFS_DIR.glob("*.json"):
        try:
            d = datetime.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d < cutoff:
            f.unlink()


def build_refs_union(retain_days=REFS_RETAIN_DAYS):
    union = {}
    if REFS_DIR.exists():
        for f in sorted(REFS_DIR.glob("*.json")):
            snap = load_json_safe(f, default={}) or {}
            union.update(snap)   # later dates win on key collision (shouldn't happen: hash ids)
    return union


def source_weight(source: str) -> float:
    s = (source or "").strip().lower()
    for key, w in SOURCE_WEIGHTS.items():
        if key in s:
            return w
    return 0.0


def pr_penalty(entry) -> float:
    """v6: demote press-release wires and vendor blogs. See PR_SOURCES."""
    src = (entry.get("source") or "").strip().lower()
    for key in PR_SOURCES:
        # Word-ish match so "pib" doesn't fire on e.g. "Pibworth Media".
        if src == key or src.startswith(key + " ") or f" {key} " in f" {src} ":
            return PR_SOURCE_PENALTY
    url = (entry.get("url") or "").lower()
    if any(marker in url for marker in PR_URL_MARKERS):
        return PR_SOURCE_PENALTY
    return 0.0



def score(entry, now, max_age_hours=None):
    """Rank a candidate for the per-section shortlist (v7). Reads the
    annotations shortlist_section() puts on the entry first (_pr, _fit, _fmt,
    _seen, _yield, _outlets). NOT the editorial decision — a recall-oriented
    ordering the selector then judges."""
    dt = parse_dt(entry.get("published"))
    if max_age_hours is not None and dt:
        age_h = (now - dt).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return float("-inf")     # A5: hard per-section age cutoff

    s = 0.0
    if dt:
        age_h = max(0.0, (now - dt).total_seconds() / 3600.0)
        s += 10.0 * max(0.0, 1.0 - age_h / RECENCY_HORIZON_H)
    else:
        s += 1.5
    s += ed.interest_score(entry.get("title", "") + " " + entry.get("summary", ""))
    s += source_weight(entry.get("source", ""))
    s += ed.pr_penalty(entry.get("_pr") or [])
    s += ed.format_penalty(entry.get("_fmt") or [])
    s += entry.get("_fit", 0.0)
    s += entry.get("_yield", 0.0)
    outlets = entry.get("_outlets", entry.get("_buzz", 1)) or 1
    s += min(outlets - 1, 4) * 0.9            # corroborated by independent outlets
    if entry.get("_seen"):
        s -= 4.0                               # ran in the last 7 days
    return s

def event_signature(title: str) -> str:
    """P2(e): a lightweight signature to catch same-event flooding that plain
    title-jaccard near-dup misses (differently-worded headlines about the same
    multi-day event, e.g. 'Summit Opens in Delhi' vs 'PM Modi Inaugurates AI
    Summit' vs 'Leaders Arrive for Day 2 of AI Summit'). Prefers a quoted
    phrase (usually a named event/product); falls back to the longest
    Title-Case word-run (a proper-noun phrase). Returns '' when nothing
    distinctive is found — an empty signature never clusters anything, so this
    can only be conservative (a miss), never collapse unrelated stories."""
    if not title:
        return ""
    qm = _QUOTED_RE.search(title)
    if qm:
        return norm_title(qm.group(1))
    best, current = [], []
    for w in _WORD_RE.findall(title):
        is_entity_word = w[:1].isupper() and w.lower() not in _HEADLINE_STOPWORDS and len(w) > 1
        if is_entity_word:
            current.append(w)
            if len(current) > len(best):
                best = current[:]
        else:
            current = []
    return norm_title(" ".join(best)) if len(best) >= 2 else ""


def apply_article_date_corrections(digest_sections, refs_today, section_max_age, now):
    """P2(c)+(d): trust a recovered `article_date` (set during enrichment, see
    enrich_shortlist.py) over the feed's `published` date when they disagree
    by more than 7 days — this is exactly the 'lying feed date' bug (the
    India-AI Impact Summit story: feed said 2026-07-17, the real byline was
    2026-02-14, five months stale). When they disagree, re-apply the section's
    own max-age cutoff against the ARTICLE date and drop the story if it's now
    stale. Separately: a story from a DISTRUSTED_SOURCE_DOMAINS source with NO
    recoverable article_date, whose extract still shows relative-date phrasing
    ("this month" etc.), is dropped outright — we have no way to verify it and
    the source has already been caught fabricating dates. Mutates
    digest_sections + refs_today in place. Returns the number of stories
    dropped, for the caller to log."""
    dropped = 0
    for section in digest_sections:
        max_age_hours = section_max_age.get(section.get("slug"), DEFAULT_MAX_AGE_HOURS)
        kept = []
        for story in section.get("stories", []):
            sid = story.get("id")
            ref = refs_today.get(sid, {}) or {}
            url = ref.get("url") or ""
            article_date_s = story.get("article_date")

            if article_date_s:
                try:
                    a_date = datetime.date.fromisoformat(article_date_s)
                except (ValueError, TypeError):
                    a_date = None
                feed_dt = parse_dt(ref.get("published"))
                if a_date and feed_dt:
                    disagreement_days = (feed_dt.date() - a_date).days
                    if disagreement_days > 7:
                        age_hours = (now.date() - a_date).days * 24
                        if age_hours > max_age_hours:
                            print(f"stale-by-article-date: {story.get('title')!r} "
                                  f"(article {a_date.isoformat()} vs feed "
                                  f"{feed_dt.date().isoformat()}, {disagreement_days}d apart)")
                            dropped += 1
                            refs_today.pop(sid, None)
                            continue
            elif any(dom in url for dom in DISTRUSTED_SOURCE_DOMAINS):
                extract = story.get("extract") or ""
                if _RELATIVE_DATE_RE.search(extract):
                    print(f"distrust-drop (no article_date, relative-date phrasing, "
                          f"distrusted source): {story.get('title')!r}")
                    dropped += 1
                    refs_today.pop(sid, None)
                    continue
            # Internal decision signal only — never part of the lean digest
            # contract the model reads (CLAUDE.md: id/title/source/summary/
            # extract?/buzz?, nothing else). Drop it from the surviving story.
            story.pop("article_date", None)
            kept.append(story)
        section["stories"] = kept
    # A section can end up empty after corrections — drop it like the initial
    # shortlist step already does for a section with nothing.
    digest_sections[:] = [s for s in digest_sections if s.get("stories")]
    return dropped


def compute_buzz(sections):
    """B2 -> v7: corroboration = number of DISTINCT outlets carrying the same
    story under any headline (editorial.corroboration), computed over every
    section BEFORE dedup. The old version only counted identical 8-word title
    prefixes, i.e. syndicated copies of one article."""
    everything = [e for s in sections for e in (s.get("entries") or [])]
    ed.corroboration(everything)
    for e in everything:
        e["_buzz"] = e.get("_outlets", 1)

def _opportunity_is_past(e, ref_year, today):
    """P7: prefer REAL structured dates (event_date/deadline, ISO strings) —
    now supplied by scripts/fetch_opportunities.py's Unstop/10times/Luma/
    Devpost feeds — over the old regex guess against title/summary text. Falls
    back to the regex guess only when neither structured field is present
    (legacy Google-News-sourced candidates)."""
    had_structured = False
    for key in ("deadline", "event_date"):
        raw = e.get(key)
        if not raw:
            continue
        had_structured = True
        try:
            d = datetime.date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if d < today:
            return True
    if had_structured:
        return False
    ed = extract_event_date((e.get("title", "") or "") + " " + (e.get("summary", "") or ""), ref_year)
    return bool(ed and ed < today)


def shortlist_section(section, now, used_urls, used_keys, ref_year, memory=None, yield_prior=None):
    """Dedup (within-day + cross-day) + annotate + rank + diversity-cap one
    section. Returns (lean, refs, dropped_count, cross_day_dropped_count,
    feed_stats). v7: sponsored copy is dropped; repeats of the last 7 days are
    dropped on exact URL/title and DEMOTED + labelled on a same-story match;
    PR, format and section-fit problems are scored AND shown to the selector."""
    raw = section.get("entries", []) or []
    max_stories = section.get("max_stories") or 4
    keep = min(max_stories * BUFFER_MULT, SECTION_HARD_CAP)
    max_age_hours = section.get("window_hours") or DEFAULT_MAX_AGE_HOURS
    slug = section.get("slug") or "x"
    is_opportunities = (slug == "opportunities")
    today = now.date()

    seen_urls, seen_keys, pool = set(), set(), []
    cross_day_dropped = 0
    for e in raw:
        url = e.get("link") or e.get("url")
        if not url:
            continue
        k = title_key(e.get("title", ""))
        if url in seen_urls or (k and k in seen_keys):
            continue
        if url in used_urls or (k and k in used_keys):     # A4/P1 cross-day dedup
            cross_day_dropped += 1
            continue
        if ed.is_sponsored(e):                             # v7: paid copy is never news
            continue
        if ed.JUNK_TEXT_RX.search(e.get("summary") or ""):
            e["summary"] = ""                              # bot-wall text is not a teaser
        if is_opportunities and _opportunity_is_past(e, ref_year, today):  # A5/P7
            continue
        seen_urls.add(url)
        if k:
            seen_keys.add(k)
        # v7 annotations (read by score() and surfaced in the digest)
        e["_pr"] = ed.pr_cues(e)
        e["_fmt"] = ed.format_flags(e)
        e["_fit"] = ed.section_fit(slug, e)
        e["_yield"] = yield_prior.bonus(e.get("source", "")) if yield_prior else 0.0
        m = memory.match(e.get("title", "")) if memory is not None else None
        if m:
            e["_seen"] = f"{m[0]}: {m[1][:110]}"
        pool.append(e)

    if is_opportunities:
        def opp_score(e):
            td = extract_event_date((e.get("title", "") or "") + " " + (e.get("summary", "") or ""), ref_year)
            s = ed.score_opportunity(e, today, text_date=td)
            if s == float("-inf"):
                return s
            s += e["_yield"] + ed.pr_penalty([c for c in e["_pr"] if not c.startswith("PR/stock-tip")])
            return s - (5.0 if e.get("_seen") else 0.0)
        scored = [(opp_score(e), e) for e in pool]
    else:
        scored = [(score(e, now, max_age_hours), e) for e in pool]
    scored = [(s, e) for s, e in scored if s > float("-inf")]
    scored.sort(key=lambda x: x[0], reverse=True)
    pool = [e for _, e in scored]

    # Two passes: the first honours the per-source cap; the second lets
    # over-cap items fill slots that would otherwise stay EMPTY. (A hard cap
    # with nothing behind it was cutting e.g. the 4th Guardian AI story — the
    # Pentagon/Anthropic ruling — on four straight days while slots went to
    # weaker items.)
    chosen, chosen_tokens, per_source, event_counts = [], [], {}, {}
    chosen_sig = []

    def try_add(e, enforce_source_cap):
        toks = title_tokens(e.get("title", ""))
        if any(jaccard(toks, ct) >= NEAR_DUP_JACCARD for ct in chosen_tokens):
            return False
        # v7: same event, different outlet/headline -> one candidate (its
        # `buzz` already records how many outlets carried it).
        stoks = ed.sig_tokens(e.get("title", ""))
        if any(ed.same_story(stoks, c) >= 0.55 for c in chosen_sig):
            return False
        src = (e.get("source") or "").strip().lower()
        if enforce_source_cap and src and per_source.get(src, 0) >= MAX_PER_SOURCE:
            return False
        sig = event_signature(e.get("title", ""))            # P2(e) flooding cap
        if sig and event_counts.get(sig, 0) >= EVENT_CAP_PER_SECTION:
            return False
        chosen.append(e)
        chosen_tokens.append(toks)
        chosen_sig.append(stoks)
        per_source[src] = per_source.get(src, 0) + 1
        if sig:
            event_counts[sig] = event_counts.get(sig, 0) + 1
        return True

    for e in pool:
        if len(chosen) >= keep:
            break
        try_add(e, True)
    if len(chosen) < keep:
        for e in pool:
            if len(chosen) >= keep:
                break
            if e not in chosen and not e.get("_seen"):
                try_add(e, False)

    dropped = len(raw) - len(chosen)

    from collections import Counter
    fetched_by_feed = Counter(e.get("feed_url") for e in raw if e.get("feed_url"))
    shortlisted_by_feed = Counter(e.get("feed_url") for e in chosen if e.get("feed_url"))
    feed_stats = [
        {"feed_url": url, "section": slug, "fetched": n,
         "shortlisted": shortlisted_by_feed.get(url, 0)}
        for url, n in fetched_by_feed.items()
    ]

    lean, refs = [], {}
    for e in chosen:
        url = e.get("link") or e.get("url")
        if url and "news.google.com/rss/articles" in url and not os.environ.get("DC_OFFLINE"):
            url = resolve_gnews_url(url)
        sid = make_id(slug, url)
        summary = (e.get("summary") or "")[:SUMMARY_CHARS]
        source = e.get("source") or section.get("name", "")
        entry = {
            "id": sid,
            "title": e.get("title", "(untitled)"),
            "source": source,
            "summary": summary,
            "img": bool(e.get("image_url")),
        }
        outlets = e.get("_outlets", e.get("_buzz", 1)) or 1
        if outlets > 1:
            entry["buzz"] = outlets
        if is_opportunities:
            when = e.get("deadline") or e.get("event_date")
            if when and str(when) not in ("None", "null"):
                entry["when"] = f"{'apply by' if e.get('deadline') else 'on'} {str(when)[:10]}"
        if e.get("_seen"):
            entry["seen"] = e["_seen"]
        flags = (e.get("_pr") or []) + (e.get("_fmt") or [])
        if e.get("_fit", 0) < 0:
            flags.append(f"no clear fit with '{section.get('name', slug)}'")
        if flags:
            entry["flags"] = flags[:3]
        lean.append(entry)
        refs[sid] = {
            "url": url,
            "image": e.get("image_url"),
            "source": source,
            "title": e.get("title", ""),
            "published": e.get("published"),
        }
    return lean, refs, dropped, cross_day_dropped, feed_stats


def merge_opportunities_feed(sections_raw):
    """P7: fold scripts/fetch_opportunities.py's real-event-source candidates
    (Unstop/10times/Luma/Devpost — see feeds/opportunities.json) into the
    `opportunities` section's entries, in the same shape as every other
    candidate (title/link/source/published/summary/image_url/feed_url) plus
    the structured `event_date`/`deadline` ISO fields that let
    `_opportunity_is_past` drop expired items on REAL dates instead of a title
    regex guess. Graceful: a missing/malformed feed file just means this run
    falls back to whatever Opportunities candidates sources.yaml's feeds
    already produced (unchanged behavior)."""
    payload = load_json_safe(OPPORTUNITIES_FEED, default=None)
    if not isinstance(payload, dict):
        print("fetch_opportunities feed: none found or malformed — skipping merge")
        return
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        print("fetch_opportunities feed: empty — skipping merge")
        return
    for section in sections_raw:
        if section.get("slug") != "opportunities":
            continue
        entries = section.setdefault("entries", [])
        added = 0
        for it in items:
            if not isinstance(it, dict) or not it.get("link") or not it.get("title"):
                continue
            entries.append({
                "title": it.get("title"),
                "link": it.get("link"),
                "source": it.get("source") or "",
                "published": it.get("published"),
                "summary": it.get("summary") or "",
                "image_url": it.get("image_url"),
                "feed_url": it.get("feed_url") or f"opportunities:{it.get('source', 'unknown')}",
                "event_date": it.get("event_date"),
                "deadline": it.get("deadline"),
            })
            added += 1
        print(f"fetch_opportunities feed: merged {added} candidate(s) into 'opportunities' section")
        return
    print("fetch_opportunities feed: no 'opportunities' section found in sources.yaml — skipping merge")


def build_colophon(stats: dict) -> str:
    total = stats.get("feeds_total", 0)
    ok = stats.get("feeds_ok", 0)
    failed = stats.get("feeds_failed", 0)
    return f"{total} sources scanned · {ok} ok · {failed} failed"


def trim_digest_to_budget(digest_sections):
    """B1: if the digest (with extracts) exceeds the rough token budget, trim
    the longest extracts first rather than dropping whole stories."""
    def size_chars():
        return len(json.dumps(digest_sections, ensure_ascii=False))

    if size_chars() <= TOKEN_BUDGET_CHARS:
        return digest_sections, size_chars()

    # Trim extracts shortest-cut-last: repeatedly shave the longest extract.
    all_stories = [s for sec in digest_sections for s in sec.get("stories", [])]
    for target_len in (500, 350, 200, 0):
        for st in all_stories:
            if st.get("extract") and len(st["extract"]) > target_len:
                st["extract"] = st["extract"][:target_len] if target_len else None
                if not st["extract"]:
                    st.pop("extract", None)
        if size_chars() <= TOKEN_BUDGET_CHARS:
            break
    return digest_sections, size_chars()



def shortlist_all(sections_raw, now, ref_year, used_urls, used_keys, memory, yield_prior):
    """Everything between the raw fetch and enrichment, as one pure-ish step
    (no network when DC_OFFLINE is set) so scripts/replay_month.py can re-run
    past days through it."""
    # v7: Beyond Your Beat is a generic top-stories feed; big India business
    # and careers stories it catches belong in a real section.
    by_slug = {s.get("slug"): s for s in sections_raw}
    byb = by_slug.get("beyond-your-beat")
    if byb:
        stay = []
        for e in byb.get("entries") or []:
            target = ed.reroute_slug(e)
            if target and target in by_slug:
                by_slug[target].setdefault("entries", []).append(e)
            else:
                stay.append(e)
        byb["entries"] = stay

    compute_buzz(sections_raw)                      # B2/v7, before any dedup

    digest_sections, refs_today, drop_log, all_feed_stats = [], {}, [], []
    total_candidates = total_cross_day_dropped = 0
    for section in sections_raw:
        lean, refs, dropped, cross_day_dropped, feed_stats = shortlist_section(
            section, now, used_urls, used_keys, ref_year, memory, yield_prior)
        refs_today.update(refs)
        total_candidates += len(lean)
        total_cross_day_dropped += cross_day_dropped
        drop_log.append((section.get("name"), len(lean), dropped, cross_day_dropped))
        all_feed_stats.extend(feed_stats)
        if not lean:
            continue
        digest_sections.append({
            "name": section.get("name"),
            "slug": section.get("slug"),
            "beta": section.get("beta", False),
            "stories": lean,
        })
    return (digest_sections, refs_today, total_candidates, drop_log,
            all_feed_stats, total_cross_day_dropped)


def annotate_after_enrichment(digest_sections, refs_today):
    """v7: with the article body in hand, PR cues become far more reliable
    ('today announced', 'About X', company-only quotes). A bot-wall/paywall
    stub is not an extract — drop it so nobody writes from it."""
    for sec in digest_sections:
        for st in sec.get("stories", []):
            body = st.get("extract") or ""
            if body and ed.JUNK_TEXT_RX.search(body[:600]) and len(body) < 1500:
                st.pop("extract", None)
                body = ""
            if not body:
                continue
            ref = refs_today.get(st.get("id"), {}) or {}
            cues = ed.pr_cues({"title": st.get("title", ""), "source": st.get("source", ""),
                               "url": ref.get("url", "")}, body=body)
            cues += ed.format_flags({"title": st.get("title", ""), "url": ref.get("url", ""),
                                     "summary": body[:400]})
            flags = list(dict.fromkeys((st.get("flags") or []) + cues))
            if flags:
                st["flags"] = flags[:3]


DIGEST_NOTES = (
    "Fields beyond id/title/source: `buzz` = how many DISTINCT outlets carry the "
    "same story (corroboration). `seen` = the paper already ran this story on that "
    "date: pick it again ONLY if there is a genuinely new fact (a verdict, number, "
    "reversal), never for continued interest. `flags` = why a desk editor would be "
    "wary (press-release wording, company's own channel, roundup/opinion format, "
    "weak fit with the section): treat flagged items as guilty until the text "
    "proves a real event. `when` (opportunities) = the real deadline/event date. "
    "Ranked best-first within each section."
)

def main() -> None:
    data = json.loads(LATEST.read_text())
    now = datetime.datetime.now(datetime.timezone.utc)
    ref_year = now.year

    sections_raw = data.get("sections", [])

    # B6: weekend-only sections (e.g. Weekend Longform) only survive into the
    # digest on Saturday IST — fetched daily like everything else, gated here.
    ist_now = now + datetime.timedelta(hours=5, minutes=30)
    is_saturday_ist = ist_now.weekday() == 5   # Monday=0 .. Saturday=5
    if not is_saturday_ist:
        sections_raw = [s for s in sections_raw if not s.get("weekend_only")]

    merge_opportunities_feed(sections_raw)           # P7

    used_urls, used_keys = load_recent_used(now)              # A4: local drafts/*.json
    pub_urls, pub_keys, memory, dedup_ok = load_published_urls(now)   # P1/v7
    used_urls |= pub_urls
    used_keys |= pub_keys
    if not dedup_ok:
        # A6-style marker the workflow greps for -> health issue. This is the
        # line that would have caught the 27 Aug break on day one.
        print("DEDUP_BROKEN: no published editions could be loaded — repeats WILL leak")

    yield_prior = ed.SourceYield.load(SHORTLIST_LOG, SELECTED_DIR, REFS_DIR, now.date())
    print(f"source-yield prior: {'on' if yield_prior.enabled else 'off (not enough history)'}"
          f", base pick-rate {yield_prior.base:.0%}")

    (digest_sections, refs_today, total_candidates, drop_log, all_feed_stats,
     total_cross_day_dropped) = shortlist_all(
        sections_raw, now, ref_year, used_urls, used_keys, memory, yield_prior)

    # B1: full-text enrichment of shortlisted survivors only.
    try:
        if os.environ.get("DC_OFFLINE"):
            raise RuntimeError("DC_OFFLINE set (replay/test run)")
        from enrich_shortlist import enrich_sections
        digest_sections = enrich_sections(digest_sections, refs_today)
    except Exception as ex:                          # noqa: BLE001
        print(f"enrichment skipped: {ex!r}")

    # P2(c)/(d): now that enrichment may have recovered each story's own
    # article_date, trust it over a feed's `published` when they disagree —
    # this is the layer that would have caught the summit story even if (a)/
    # (e) hadn't already stopped it upstream.
    try:
        section_max_age = {s.get("slug"): (s.get("window_hours") or DEFAULT_MAX_AGE_HOURS)
                            for s in sections_raw}
        article_date_dropped = apply_article_date_corrections(
            digest_sections, refs_today, section_max_age, now)
    except Exception as ex:                          # noqa: BLE001
        print(f"article-date correction pass skipped: {ex!r}")
        article_date_dropped = 0

    annotate_after_enrichment(digest_sections, refs_today)   # v7

    digest_sections, size_chars = trim_digest_to_budget(digest_sections)

    digest = {
        "date": now.date().isoformat(),
        "colophon": build_colophon(data.get("stats", {})),
        "how_to_read": DIGEST_NOTES,
        "sections": digest_sections,
    }

    DIGEST.write_text(json.dumps(digest, indent=2, ensure_ascii=False))

    # Option B, Stage 1: a LEAN digest for Routine A (the selector). A only
    # needs to CHOOSE ids, so it gets titles + a ~300-char teaser + buzz/img —
    # not the 2500-char extracts. Keeps the selector run cheap. Additive: the
    # single-routine flow and Routine B are unaffected (they read the rich
    # digest.json / feeds/selected respectively).
    LEAN_TEASER = 300
    lean_sections = []
    for sec in digest_sections:
        lean_stories = []
        for s in sec.get("stories", []):
            teaser = (s.get("extract") or s.get("summary") or "")[:LEAN_TEASER]
            lean = {"id": s.get("id"), "title": s.get("title"),
                    "source": s.get("source"), "teaser": teaser}
            if s.get("buzz"):
                lean["buzz"] = s["buzz"]
            if s.get("img"):
                lean["img"] = True
            for extra in ("when", "seen", "flags"):
                if s.get(extra):
                    lean[extra] = s[extra]
            lean_stories.append(lean)
        lean_sections.append({"name": sec.get("name"), "slug": sec.get("slug"),
                              "stories": lean_stories})
    lean_digest = {"date": digest["date"], "colophon": digest["colophon"],
                   "how_to_read": DIGEST_NOTES,
                   "sections": lean_sections}
    (ROOT / "feeds" / "digest_lean.json").write_text(
        json.dumps(lean_digest, indent=2, ensure_ascii=False))

    # A2: per-date refs snapshot + rolling union (back-compat) + prune.
    REFS_DIR.mkdir(parents=True, exist_ok=True)
    (REFS_DIR / f"{now.date().isoformat()}.json").write_text(
        json.dumps(refs_today, ensure_ascii=False))
    prune_old_refs(now=now)
    REFS.write_text(json.dumps(build_refs_union(), ensure_ascii=False))

    # v7: per-source shortlist counts — the denominator of the source-yield
    # prior (the numerator is feeds/selected/<date>.json, written by select.yml).
    from collections import Counter as _C
    src_counts = _C(st.get("source", "") for sec in digest_sections
                    for st in sec.get("stories", []))
    with open(SHORTLIST_LOG, "a", encoding="utf-8") as f:
        for src, n in src_counts.items():
            f.write(json.dumps({"date": now.date().isoformat(), "source": src, "n": n},
                               ensure_ascii=False) + "\n")

    # B8: append this run's per-feed fetched/shortlisted counts.
    with open(FEED_STATS, "a", encoding="utf-8") as f:
        for row in all_feed_stats:
            row["date"] = now.date().isoformat()
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    est_tokens = size_chars // 4
    print(f"Wrote {DIGEST.relative_to(ROOT)} ({total_candidates} candidates, "
          f"~{est_tokens} est. tokens) and refs/{now.date().isoformat()}.json "
          f"({len(refs_today)} refs)")
    for name, kept, dropped, cross_day in drop_log:
        print(f"  {name:<18} kept {kept:>2}  dropped {dropped:>3}  (of which cross-day repeats: {cross_day:>3})")
    print(f"P1 cross-day dedup: {total_cross_day_dropped} candidate(s) dropped as repeats "
          f"of a story published in the last {CROSS_DAY_WINDOW_DAYS} days")
    if article_date_dropped:
        print(f"P2 article-date corrections: {article_date_dropped} stor"
              f"{'y' if article_date_dropped == 1 else 'ies'} dropped as stale-by-article-date "
              f"or distrusted-source-with-relative-dating")
    if total_candidates == 0:
        # A6: distinct marker the workflow greps for to open a health issue.
        print("DIGEST_EMPTY: no candidates survived shortlisting")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:                     # noqa: BLE001 — never crash CI
        print(f"build_digest failed: {ex!r} — writing minimal empty digest")
        now = datetime.datetime.now(datetime.timezone.utc)
        DIGEST.write_text(json.dumps(
            {"date": now.date().isoformat(),
             "colophon": "digest build failed — see CI log",
             "sections": []}, indent=2, ensure_ascii=False))
        REFS.write_text("{}")
        sys.exit(1)   # A6: non-zero exit -> workflow can flag an empty digest
