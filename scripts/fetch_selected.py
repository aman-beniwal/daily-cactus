#!/usr/bin/env python3
"""Option B, Stage 1.5 — full-text fetch for the SELECTED shortlist only.

WHY THIS EXISTS
---------------
The single-routine pipeline (build_digest.py -> ROUTINE_PROMPT.md) caps every
story to a ~2500-char extract because the editor sees ALL ~150 shortlisted
candidates in one read — full text for all of them would blow the token
budget. Option B splits the job in two: Routine A (ROUTINE_PROMPT_A.md) reads
the same lean digest and SELECTS ~25-40 ids worth a full read; THIS script
then fetches the FULL article text for just those, so Routine B
(ROUTINE_PROMPT_B.md) writes summaries from whole articles instead of
snippets, without re-reading hundreds of pages.

Runs on GitHub Actions (.github/workflows/select.yml), triggered by the push
of `selections/<date>.json` -- free compute, not token-billed.

REUSE, NOT REINVENT: the actual fetch (browser UA, retry, SSL context) and
the trafilatura/readability extraction live in scripts/enrich_shortlist.py
already (Fix 2/3 of RUN_AUDIT_2026-07-17). This script imports
`fetch_extract` from there with a much larger `max_chars` -- enrich_shortlist
itself is untouched apart from that one optional parameter (default
preserves its existing 2500-char behavior for the still-running single-
routine flow). `resolve_gnews.resolve` is reused too, as a safety net -- by
the time a story reaches refs/<date>.json its URL should already be resolved
(build_digest.py does that before writing refs), but a story pulled from the
UNION fallback could in principle predate that fix, so we resolve again here;
it's a no-op (returns the url unchanged) for anything that isn't a
news.google.com/rss/articles/... link.

SCHEMA NOTE (deliberate, additive extension of the brief): the task's minimum
schema for feeds/selected/<date>.json is `{"date":..., "stories": {...}}`. We
also carry the STRUCTURE fields (`lead`, `frontpage`, `sections`,
`opportunities`, `longform`) through verbatim from selections/<date>.json.
Without them, Routine B would have only a flat id->fulltext map and no way to
know which section/role each id was selected for (an id's own prefix reveals
its ORIGINAL digest section slug, e.g. "ai-xxx", but not whether Routine A
picked it as the lead, frontpage, a plain section story, an also-rail
one-liner, an opportunity, or a longform pick). Carrying the structure keeps
Routine B a genuine two-file read (feeds/selected + TASTE.md) instead of
needing to re-read selections/<date>.json itself.

Never crashes the pipeline: a story whose fetch fails still gets a `stories`
entry with `fulltext: ""` (Routine B falls back to headline/whatever prose it
can write for that one story). A missing/unparsable selections file writes a
placeholder feeds/selected/<date>.json with an empty `stories` map and exits
cleanly -- Routine B's own GUARD (see ROUTINE_PROMPT_B.md) handles that case
by falling back to feeds/digest.json.
"""
import datetime
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SELECTIONS_DIR = ROOT / "selections"
REFS_DIR = ROOT / "feeds" / "refs"
REFS_LEGACY = ROOT / "feeds" / "refs.json"
SELECTED_DIR = ROOT / "feeds" / "selected"
DIGEST = ROOT / "feeds" / "digest.json"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from enrich_shortlist import fetch_extract, fetch_extract_with_image   # reuse browser-UA fetch + extractors

try:
    from resolve_gnews import resolve as resolve_gnews_url   # safety net, see module docstring
except Exception:                                             # noqa: BLE001
    def resolve_gnews_url(url):
        return url

# --- v7: recovery chain for sites that block the direct fetch --------------
# Audit 24 Aug-23 Sep 2026: of 1,205 selected stories, 293 (24%) reached the
# writer with no real body text — Reuters 40/40, Bloomberg 49/49, YourStory
# 38/38 (~150-char stub), NDTV 10/10, Indian Express 9/9 — i.e. precisely the
# highest-value outlets (see memory: fix extraction, never drop the source).
# When the direct fetch fails or returns a paywall/bot-wall stub:
#   1. a public reader proxy (r.jina.ai) — verified 23 Sep 2026 to return the
#      body for Bloomberg (lede + first paragraphs), YourStory, NDTV and
#      Firstpost. Only the PUBLIC article URL is sent; no keys, no user data.
#   2. SIBLING COVERAGE — the same story as reported by another outlet, found
#      with a Google News search on the headline. Reuters is blocked for both
#      the direct fetch and the reader, but its wire copy is republished and
#      re-reported widely. The card still links to the original outlet; the
#      text is clearly marked as coming from the other outlet.
#   3. the digest snippet (unchanged fallback).
READER_PREFIX = "https://r.jina.ai/"
READER_TIMEOUT = 25
MIN_BODY_CHARS = 600            # below this an "extract" is a stub, not an article
RECOVERY_TIME_BUDGET = 300      # seconds on top of TOTAL_TIME_BUDGET
# Borrowed text must come from a real newsroom — an open Google News search
# also returns content-scraper sites (verified 23 Sep 2026: a restaurant
# domain republishing a Reuters story). Allowlist, matched on the domain.
SIBLING_OK = (
    "thehindu.com", "thehindubusinessline.com", "indianexpress.com", "hindustantimes.com",
    "livemint.com", "economictimes.indiatimes.com", "timesofindia.indiatimes.com",
    "business-standard.com", "moneycontrol.com", "ndtv.com", "ndtvprofit.com",
    "indiatoday.in", "businesstoday.in", "theprint.in", "scroll.in", "firstpost.com",
    "news18.com", "deccanherald.com", "financialexpress.com", "tribuneindia.com",
    "telegraphindia.com", "thewire.in", "inc42.com", "yourstory.com", "entrackr.com",
    "medianama.com", "moneylife.in", "outlookbusiness.com", "usnews.com",
    "investing.com", "marketscreener.com", "finance.yahoo.com", "cnbc.com", "bbc.com",
    "bbc.co.uk", "theguardian.com", "aljazeera.com", "apnews.com", "cnn.com",
    "washingtonpost.com", "scmp.com", "straitstimes.com", "japantimes.co.jp",
    "dw.com", "france24.com", "arabnews.com", "thenationalnews.com", "fortune.com",
    "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com", "semafor.com",
    "axios.com", "politico.com", "politico.eu", "businessinsider.com", "qz.com",
    "statnews.com", "fiercebiotech.com", "spacenews.com", "theregister.com",
    "inkl.com", "tradingview.com", "nikkei.com", "asia.nikkei.com", "channelnewsasia.com",
    "euronews.com", "independent.co.uk", "npr.org", "latimes.com", "time.com",
    "forbes.com", "forbesindia.com", "thediplomat.com", "carbonbrief.org",
    "pv-tech.org", "mercomindia.com", "energy-storage.news")
BLOCKED_DOMAINS = ("reuters.com", "bloomberg.com", "wsj.com", "ft.com",
                   "economist.com", "nytimes.com", "the-ken.com")
FULLTEXT_CHARS = 12_000        # generous: whole news articles, only truncates
                                # pathological longform (per the brief).
TOTAL_TIME_BUDGET = 480        # seconds, CI-friendly ceiling for the whole pass
                                # (fewer stories than enrich_shortlist.py's full
                                # shortlist, but each fetch can be a longer page).


def load_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:                                          # noqa: BLE001
        return default


def load_refs_for_date(date_str: str) -> dict:
    """Same precedence as assemble_edition.py::load_refs_for_date: legacy
    rolling union, then the union of all per-date snapshots, then THIS date's
    own snapshot taking final precedence. Hash ids make the union safe."""
    own = load_json(REFS_DIR / f"{date_str}.json", default={}) or {}
    union = {}
    if REFS_DIR.exists():
        for f in sorted(REFS_DIR.glob("*.json")):
            snap = load_json(f, default={}) or {}
            union.update(snap)
    legacy = load_json(REFS_LEGACY, default={}) or {}
    merged = {}
    merged.update(legacy)
    merged.update(union)
    merged.update(own)
    return merged


def load_digest_fallback() -> dict:
    """id -> best available snippet from the digest (the 2500-char `extract`,
    else the short RSS `summary`). Used when a full-article fetch is blocked
    (e.g. YourStory / Moneycontrol bot-block the scraper) so a PUBLISHED story
    still carries real body text instead of nothing — degraded, never empty."""
    d = load_json(DIGEST, default={}) or {}
    out = {}
    for sec in d.get("sections", []) or []:
        for st in sec.get("stories", []) or []:
            sid = st.get("id")
            if sid:
                out[sid] = st.get("extract") or st.get("summary") or ""
    return out


def collect_ids(sel: dict) -> tuple:
    """Flatten every id in the selection, de-duped and order-preserving.

    Returns (ids, lite_ids). `lite_ids` are the `also`-rail picks: they become
    ONE-LINERS in the paper, so fetching a whole article for them is wasted
    bandwidth and wasted writer context. They still get an entry (headline +
    the digest snippet) — just not the expensive full fetch. Anything that
    becomes a full card (lead, frontpage, section stories, opportunities,
    longform) still gets the whole article."""
    card_ids, also_ids, ids = set(), set(), []

    def add(i, is_also=False):
        if not i:
            return
        ids.append(i)
        (also_ids if is_also else card_ids).add(i)

    add(sel.get("lead"))
    for i in (sel.get("frontpage") or []):
        add(i)
    for sec in sel.get("sections", []) or []:
        for i in (sec.get("stories") or []):
            add(i)
        for i in (sec.get("also") or []):
            add(i, is_also=True)
    for i in (sel.get("opportunities") or []):
        add(i)
    for i in (sel.get("longform") or []):
        add(i)

    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    # lite = appears ONLY in an also rail. If the same id is also a full card
    # somewhere, it needs the whole article.
    lite = also_ids - card_ids
    return out, lite


import re as _re
import urllib.parse as _up
import urllib.request as _ur
from enrich_shortlist import _SSL_CTX, BROWSER_UA
try:
    from editorial import JUNK_TEXT_RX, sig_tokens, same_story
except Exception:                                              # noqa: BLE001
    JUNK_TEXT_RX = _re.compile(r"$^")
    sig_tokens = lambda t: set((t or "").lower().split())      # noqa: E731
    same_story = lambda a, b: 0.0                              # noqa: E731


def _usable(text: str) -> bool:
    t = (text or "").strip()
    return len(t) >= MIN_BODY_CHARS and not JUNK_TEXT_RX.search(t[:800])


def _via_reader(url: str) -> str:
    """Body text via the r.jina.ai reader, or ''. Never raises."""
    try:
        req = _ur.Request(READER_PREFIX + url, headers={
            "User-Agent": BROWSER_UA, "Accept": "text/plain", "X-Return-Format": "text"})
        with _ur.urlopen(req, timeout=READER_TIMEOUT, context=_SSL_CTX) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        return ""
    i = body.find("Markdown Content:")
    body = body[i + len("Markdown Content:"):] if i >= 0 else body
    # keep prose paragraphs; drop nav/link soup the reader passes through
    boiler = _re.compile(r"terms of service|cookie|by accepting|subscribe|sign in|"
                         r"newsletter|privacy policy|all rights reserved", _re.I)
    paras = [p.strip() for p in body.split("\n")
             if len(p.split()) >= 12 and p.count("](") < 2 and not boiler.search(p)
             and p.strip()[-1:] in '.”"?!)’']
    text = "\n".join(paras)
    if JUNK_TEXT_RX.search(text[:800]):
        return ""
    return text[:FULLTEXT_CHARS]


def _gnews_search(headline: str):
    """(title, source, url) candidates for the same story from Google News."""
    q = _up.quote_plus(_re.sub(r"\s+[-|]\s+[^-|]+$", "", headline)[:140])
    feed = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        req = _ur.Request(feed, headers={"User-Agent": BROWSER_UA})
        with _ur.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            xml = r.read().decode("utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for item in _re.findall(r"<item>(.*?)</item>", xml, _re.S)[:12]:
        t = _re.search(r"<title>(.*?)</title>", item, _re.S)
        l = _re.search(r"<link>(.*?)</link>", item, _re.S)
        src = _re.search(r"<source[^>]*>(.*?)</source>", item, _re.S)
        if t and l:
            import html as _h
            out.append((_h.unescape(t.group(1)), _h.unescape(src.group(1)) if src else "",
                        l.group(1).strip()))
    return out


def _via_sibling(headline: str, own_url: str):
    """(text, outlet) of the same story from another, fetchable outlet."""
    own_dom = _up.urlparse(own_url).netloc.replace("www.", "")
    want = sig_tokens(headline)
    for title, src, link in _gnews_search(headline):
        if same_story(want, sig_tokens(title)) < 0.35:
            continue
        real = resolve_gnews_url(link)
        dom = _up.urlparse(real).netloc.replace("www.", "")
        if not dom or dom == own_dom or "news.google" in dom or \
                any(b in dom for b in BLOCKED_DOMAINS) or \
                not any(dom == ok or dom.endswith("." + ok) for ok in SIBLING_OK):
            continue
        try:
            text = fetch_extract(real, max_chars=FULLTEXT_CHARS) or ""
        except Exception:                                      # noqa: BLE001
            text = ""
        if _usable(text):
            return text, (src or dom)
    return "", ""


def _bing_items(headline: str):
    """(title, source, real_url, snippet) for the same story from Bing News RSS.
    v8.3 (24 Sep 2026): Bing returns each outlet's opening lines, e.g. for the
    Reuters-only 'Modal Labs at $15B' story: 'up from $4.65B four months ago',
    'rival Baseten eyes $26B' — enough for a real card when Reuters blocks us."""
    q = _up.quote_plus(_re.sub(r"\s+[-|]\s+[^-|]+$", "", headline)[:140])
    try:
        req = _ur.Request(f"https://www.bing.com/news/search?q={q}&format=rss",
                          headers={"User-Agent": BROWSER_UA})
        with _ur.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            xml = r.read().decode("utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        return []
    import html as _h
    out = []
    for item in _re.findall(r"<item>(.*?)</item>", xml, _re.S)[:10]:
        t = _re.search(r"<title>(.*?)</title>", item, _re.S)
        l = _re.search(r"<link>(.*?)</link>", item, _re.S)
        d = _re.search(r"<description>(.*?)</description>", item, _re.S)
        src = _re.search(r"<News:Source>(.*?)</News:Source>", item, _re.S)
        if not (t and l):
            continue
        link = _h.unescape(l.group(1).strip())
        m = _re.search(r"[?&]url=([^&]+)", link)
        real = _up.unquote(m.group(1)) if m else link
        snip = _re.sub(r"<[^>]+>", "", _h.unescape(d.group(1))).strip() if d else ""
        out.append((_h.unescape(t.group(1)), _h.unescape(src.group(1)) if src else "", real, snip))
    return out


def _via_bing(headline: str, own_url: str):
    """Full text of the same story from an allow-listed outlet found via Bing,
    else a short 'coverage from other outlets' digest of their opening lines."""
    own_dom = _up.urlparse(own_url).netloc.replace("www.", "")
    want = sig_tokens(headline)
    items = [it for it in _bing_items(headline) if same_story(want, sig_tokens(it[0])) >= 0.3]
    for title, src, real, snip in items:
        dom = _up.urlparse(real).netloc.replace("www.", "")
        if dom and dom != own_dom and not any(b in dom for b in BLOCKED_DOMAINS) and \
                any(dom == ok or dom.endswith("." + ok) for ok in SIBLING_OK):
            try:
                text = fetch_extract(real, max_chars=FULLTEXT_CHARS) or ""
            except Exception:                                  # noqa: BLE001
                text = ""
            if _usable(text):
                return (f"[Text below is {src or dom}'s report of the same story — the original "
                        f"outlet blocks automated reading. The link still points to the original.]\n" + text,
                        f"bing-sibling:{src or dom}", "full")
    snips = [f"- {src}: {snip}" for _, src, _, snip in items if len(snip) > 60][:5]
    if len(snips) >= 2:
        return ("[Coverage summary: opening lines of the same story from other outlets — "
                "write only what these lines state.]\n" + "\n".join(snips), "bing-snippets", "digest-extract")
    return "", "", ""


def recover_text(headline: str, url: str, deadline: float):
    """v7 recovery chain. Returns (text, via) or ('', '')."""
    if not url or time.time() > deadline:
        return "", ""
    dom = _up.urlparse(url).netloc
    lede = ""
    if "reuters.com" not in dom:              # reuters is blocked at the reader too
        t = _via_reader(url)
        if _usable(t):
            return t, "reader"
        lede = t if len(t) >= 250 else ""      # paywalled: lede paragraphs only
    if time.time() > deadline:
        return (lede, "reader-lede") if lede else ("", "")
    t, outlet = _via_sibling(headline, url)
    if t:
        note = (f"[Text below is {outlet}'s report of the same story — the original "
                f"outlet blocks automated reading. The link still points to the original.]\n")
        return note + (lede + "\n" if lede else "") + t, f"sibling:{outlet}"
    if time.time() < deadline:
        t, via, kind = _via_bing(headline, url)
        if t:
            if kind == "digest-extract":
                return (lede + "\n" + t) if lede else t, via
            return t, via
    return (lede, "reader-lede") if lede else ("", "")


def fetch_one(sid: str, refs: dict, fallbacks: dict, start: float, lite: bool = False) -> dict:
    """Never raises. Tries full article text; on failure degrades to the
    digest snippet (2500-char extract / RSS summary) rather than empty, so a
    published story is never left with only its headline. `text_source` records
    which won: "full" | "digest-extract" | "none"."""
    ref = refs.get(sid)
    if ref is None:
        return {"headline": "", "source": "", "url": "", "published": None,
                "image": None, "fulltext": "", "text_source": "none"}

    url = ref.get("url") or ""
    try:
        if url and "news.google.com/rss/articles" in url:
            url = resolve_gnews_url(url)
    except Exception:                                          # noqa: BLE001
        pass

    fulltext, source_kind, og_img = "", "none", None
    if lite:
        # also-rail one-liner: the digest snippet is plenty, skip the fetch
        snippet = (fallbacks.get(sid) or "").strip()
        return {
            "headline": ref.get("title", ""), "source": ref.get("source", ""),
            "url": url, "published": ref.get("published"), "image": ref.get("image"),
            "fulltext": snippet, "text_source": "digest-extract" if snippet else "none",
            "lite": True,
        }
    if url and (time.time() - start) < TOTAL_TIME_BUDGET:
        try:
            fulltext, og_img = fetch_extract_with_image(url, max_chars=FULLTEXT_CHARS)
            fulltext = fulltext or ""
        except Exception:                                      # noqa: BLE001
            fulltext, og_img = "", None
    via = ""
    if fulltext and not _usable(fulltext):
        fulltext = ""                          # paywall/bot-wall stub, not an article
    if not fulltext:
        fulltext, via = recover_text(ref.get("title", ""), url,
                                     start + TOTAL_TIME_BUDGET + RECOVERY_TIME_BUDGET)
    if fulltext and via in ("reader-lede", "bing-snippets"):
        source_kind = "digest-extract"         # honest: the writer must treat it as partial
    elif fulltext:
        # "full" keeps the pasted writer prompt's contract; `text_via` records
        # how it was obtained (the sibling note is also inside the text itself).
        source_kind = "full"
    else:
        snippet = (fallbacks.get(sid) or "").strip()
        if snippet:
            fulltext, source_kind = snippet, "digest-extract"

    return {
        "headline": ref.get("title", ""),
        "source": ref.get("source", ""),
        "url": url,
        "published": ref.get("published"),
        "image": ref.get("image"),
        "fulltext": fulltext,
        "text_source": source_kind,
        **({"text_via": via} if via else {}),
        **({"og_image": og_img} if og_img and not ref.get("image") else {}),
    }


def write_placeholder(date_str: str, reason: str) -> None:
    SELECTED_DIR.mkdir(parents=True, exist_ok=True)
    out = SELECTED_DIR / f"{date_str}.json"
    out.write_text(json.dumps(
        {"date": date_str, "stories": {}}, indent=2, ensure_ascii=False))
    print(f"fetch_selected: {reason} — wrote empty placeholder {out.relative_to(ROOT)}")


def main() -> None:
    date_arg = (sys.argv[1] if len(sys.argv) > 1
                else datetime.datetime.now(datetime.timezone.utc).date().isoformat())

    sel_path = SELECTIONS_DIR / f"{date_arg}.json"
    sel = load_json(sel_path, default=None)
    if not isinstance(sel, dict):
        write_placeholder(date_arg, f"no valid selections file at {sel_path.relative_to(ROOT)}")
        return

    date = sel.get("date") or date_arg
    refs = load_refs_for_date(date)
    ids, lite_ids = collect_ids(sel)

    if not ids:
        write_placeholder(date, "selections file had no ids")
        return

    fallbacks = load_digest_fallback()
    start = time.time()
    got_fulltext = got_fallback = got_none = 0
    stories = {}
    for sid in ids:
        entry = fetch_one(sid, refs, fallbacks, start, lite=(sid in lite_ids))
        if entry["text_source"] == "full":
            got_fulltext += 1
        elif entry["text_source"] == "digest-extract":
            got_fallback += 1
        else:
            got_none += 1
        stories[sid] = entry

    out = {
        "date": date,
        # structure carried through verbatim (see module docstring) so Routine
        # B knows placement without a second read.
        "lead": sel.get("lead"),
        "frontpage": sel.get("frontpage") or [],
        "sections": sel.get("sections") or [],
        "opportunities": sel.get("opportunities") or [],
        "longform": sel.get("longform") or [],
        "stories": stories,
    }

    SELECTED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SELECTED_DIR / f"{date}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    total = len(ids)
    size_chars = len(json.dumps(out, ensure_ascii=False))
    covered = got_fulltext + got_fallback
    print(f"fetch_selected: wrote {out_path.relative_to(ROOT)} — "
          f"{got_fulltext}/{total} full text, {got_fallback} digest-extract fallback, "
          f"{got_none} headline-only ({covered}/{total} with body text); "
          f"{size_chars} chars (~{size_chars // 4} est. tokens)")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:                                    # noqa: BLE001
        print(f"fetch_selected failed: {ex!r} — writing empty placeholder")
        fallback_date = (sys.argv[1] if len(sys.argv) > 1
                          else datetime.datetime.now(datetime.timezone.utc).date().isoformat())
        try:
            write_placeholder(fallback_date, "unhandled exception")
        except Exception:                                      # noqa: BLE001
            pass
        sys.exit(1)   # non-zero exit -> select.yml can flag a health issue
