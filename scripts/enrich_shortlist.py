#!/usr/bin/env python3
"""B1 — full-text enrichment of shortlisted candidates.

Runs AFTER build_digest.py has already deduped, ranked, and shortlisted (so we
fetch at most a few dozen–~100 pages, never all ~280 raw candidates). For each
survivor, fetches the article page and extracts the main body text with
`trafilatura` (fallback: `readability-lxml` + a crude tag-stripper). The result
is capped to ~2500 chars and attached as `extract` on the digest entry so the
editor can write numbers-first summaries instead of working from a bare
headline + 200-char RSS blurb. 2500 chars comfortably covers the lede + key
numbers of a typical news article (raised from 700 — see RUN_AUDIT).

Never blocks the digest: any fetch/parse failure for a given story simply
means it keeps its RSS `summary` and gets no `extract` field. A slow or dead
site cannot break the pipeline; a per-request timeout (with one retry) and an
overall time budget cap the worst case.

Fix 2 (RUN_AUDIT_2026-07-17): both extractors were sending a bot-flavoured UA
(the raw `trafilatura.fetch_url` default, and an explicit `DailyCactusBot/1.0`
string). Several publishers (verified: Indian Express) bot-block that and
serve nothing/a stub, so extraction silently failed for those sources even
though the site was perfectly reachable. Fetching is now centralised in
`_fetch_html`: a real current-Chrome User-Agent, one retry on failure, then
BOTH trafilatura and readability parse that same already-fetched HTML (no
second network round-trip on the common path).

P2(b) (ISSUES_BACKLOG.md — the "India-AI Impact Summit" bug): a feed's
`published` date can simply be WRONG (verified: newsonair.gov.in reports a
fresh date for a 5-month-old article). `extract_article_date()` tries to
recover the article's OWN publication date from its HTML — independent of the
feed — via <meta property="article:published_time"> (and common variants),
JSON-LD `datePublished`, <time datetime=...>, and a visible byline regex
("February 14, 2026" / "14 Feb 2026"). build_digest.py trusts this over the
feed date when they disagree by more than 7 days (see
apply_article_date_corrections). Best-effort and often unavailable (some
sites, including newsonair.gov.in, expose no machine-readable date at all) —
never raises, returns None on any failure.
"""
import re
import ssl
import time
import datetime
import urllib.request

EXTRACT_CHARS = 2500          # Fix 3: raised from 700 — captures lede + numbers
PER_REQUEST_TIMEOUT = 8       # seconds, per attempt
FETCH_RETRIES = 1             # Fix 2: one retry on a failed fetch
TOTAL_TIME_BUDGET = 240       # seconds — CI-friendly ceiling for the whole pass

# A full, current, realistic Chrome UA — several publishers (e.g. Indian
# Express) bot-block a generic/absent UA but let a browser-shaped one through.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_TAG = re.compile(r"<[^>]+>")

# P2(b): machine-readable date sources, tried in order of trustworthiness.
_META_DATE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']'
    r'(?:article:published_time|article:modified_time|og:article:published_time|'
    r'parsely-pub-date|publish-date|publication_date|date|dc\.date)["\']'
    r'[^>]+content=["\']([^"\']+)["\']', re.I)
_JSONLD_DATE_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I)
_TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_NAMES = "|".join(_MONTHS.keys())
# Visible byline formats: "February 14, 2026", "Posted On: 14 Feb 2026"
_BYLINE_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\.?\s+(20\d{{2}})\b", re.I)
_BYLINE_MDY_RE = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I)

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html or "")).strip()


def _fetch_html(url: str) -> str | None:
    """Fetch a page's HTML with a real browser UA. One retry on any failure
    (network error, timeout, non-2xx). Returns None (never raises) if both
    attempts fail — callers degrade to the RSS summary."""
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(FETCH_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=PER_REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
                raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception:
            if attempt < FETCH_RETRIES:
                continue
            return None
    return None


def _ymd(year_s, mon_s, day_s) -> str | None:
    mon = _MONTHS.get((mon_s or "").lower())
    if not mon:
        return None
    try:
        return datetime.date(int(year_s), mon, int(day_s)).isoformat()
    except (ValueError, TypeError):
        return None


def _coerce_iso_date(s: str) -> str | None:
    """'2026-02-14T10:00:00+05:30' (or bare '2026-02-14') -> '2026-02-14',
    with a real validity check (rejects e.g. a mangled '2026-02-31')."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", (s or "").strip())
    if not m:
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return m.group(0)


def extract_article_date(html: str) -> str | None:
    """P2(b): best-effort recovery of the article's OWN publication date,
    independent of the RSS feed's (possibly false) `published` field. Tries,
    in order: <meta property="article:published_time"> and common variants,
    JSON-LD `datePublished`, <time datetime=...>, then a visible byline regex
    ("February 14, 2026" / "14 Feb 2026"). Returns an ISO date string
    (YYYY-MM-DD) or None — never raises. Note: some sites (verified:
    newsonair.gov.in) expose NONE of these — that's a real limitation this
    layer cannot close alone, which is why build_digest.py also carries a
    source-distrust list (P2/d) for exactly that case."""
    if not html:
        return None
    for rx in (_META_DATE_RE, _JSONLD_DATE_RE, _TIME_TAG_RE):
        m = rx.search(html)
        if m:
            iso = _coerce_iso_date(m.group(1))
            if iso:
                return iso
    m = _BYLINE_DMY_RE.search(html)
    if m:
        iso = _ymd(m.group(3), m.group(2), m.group(1))
        if iso:
            return iso
    m = _BYLINE_MDY_RE.search(html)
    if m:
        iso = _ymd(m.group(3), m.group(1), m.group(2))
        if iso:
            return iso
    return None


def _extract_trafilatura(html: str) -> str | None:
    import trafilatura
    return trafilatura.extract(html, include_comments=False,
                                include_tables=False, favor_recall=True)


def _extract_readability(html: str) -> str | None:
    from readability import Document
    doc = Document(html)
    return _strip_html(doc.summary())


def fetch_extract_and_date(url: str, max_chars: int = EXTRACT_CHARS):
    """One HTML fetch, two things out: the extracted body text (as
    `fetch_extract`) AND (P2/b) the article's own recovered publication date,
    if any. Returns (text_or_None, article_date_iso_or_None). Never raises."""
    if not url:
        return None, None
    html = _fetch_html(url)
    if not html:
        return None, None
    article_date = extract_article_date(html)
    text = None
    for fn in (_extract_trafilatura, _extract_readability):
        try:
            t = fn(html)
            if t and t.strip():
                text = t.strip()[:max_chars]
                break
        except Exception:
            continue
    return text, article_date


_OG_IMG = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_OG_IMG2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)


def fetch_extract_with_image(url: str, max_chars: int = EXTRACT_CHARS):
    """v8.3: (text, preview_image_url) from one fetch. The article's own
    og:image is what social cards show — used when the feed carried no
    image, so more cards get a photo."""
    if not url:
        return None, None
    html = _fetch_html(url)
    if not html:
        return None, None
    m = _OG_IMG.search(html) or _OG_IMG2.search(html)
    img = m.group(1).strip() if m and m.group(1).startswith("http") else None
    text = None
    for fn in (_extract_trafilatura, _extract_readability):
        try:
            t = fn(html)
            if t and t.strip():
                text = t.strip()[:max_chars]
                break
        except Exception:
            continue
    return text, img


def fetch_extract(url: str, max_chars: int = EXTRACT_CHARS) -> str | None:
    """max_chars is optional (Option B / scripts/fetch_selected.py reuses this
    same fetch+extract path with a much larger cap for full-article text);
    every existing call site omits it and keeps today's EXTRACT_CHARS behavior
    unchanged. Thin wrapper over fetch_extract_and_date for callers that only
    want the text."""
    text, _ = fetch_extract_and_date(url, max_chars)
    return text


def enrich_sections(digest_sections, refs_today):
    """Mutates and returns digest_sections, adding `extract` (and, when
    recoverable, `article_date` — P2/b) where possible. Time-boxed across the
    whole call so a run of dead/slow sites degrades gracefully instead of
    stalling the pipeline."""
    start = time.time()
    fetched = skipped = failed = dated = 0
    for section in digest_sections:
        for story in section.get("stories", []):
            if time.time() - start > TOTAL_TIME_BUDGET:
                skipped += 1
                continue
            ref = refs_today.get(story.get("id"), {})
            url = ref.get("url")
            extract, article_date = fetch_extract_and_date(url)
            if extract:
                story["extract"] = extract
                fetched += 1
            else:
                failed += 1
            if article_date:
                story["article_date"] = article_date
                dated += 1
    print(f"enrich_shortlist: {fetched} extracted, {failed} fell back to RSS "
          f"summary, {skipped} skipped (time budget), {dated} article_date recovered")
    return digest_sections


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.bbc.com/news"
    print(fetch_extract(url))
