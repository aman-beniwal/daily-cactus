#!/usr/bin/env python3
"""Fetch every RSS/Atom feed in sources.yaml -> feeds/latest.json

WHY THIS EXISTS
---------------
Claude Code routines run in a sandboxed cloud VM whose outbound network is
filtered to a small allowlist (package registries etc.). Fetching news feeds
from inside that sandbox returns 403 for every publisher domain. This script
runs on GitHub Actions instead, which has full internet, fetches the feeds,
and commits the results into the repo. The routine then simply READS this
file -- it never needs network access at all.

GitHub does the dumb, reliable plumbing. Claude does only the editing.

Single source of truth: this reads the SAME sources.yaml the routine uses,
so you only ever edit your feed list in one place.
"""
import json
import time
import re
import html
import ssl
import datetime
import pathlib
import urllib.request
import feedparser
import yaml

# B7 audit (Jul 2026) fix: some feed parse failures logged as "invalid XML"
# turned out to be a TLS handshake/cert issue in disguise — feedparser's bozo
# exception doesn't distinguish "the runner's default trust store rejected
# this cert chain" from "the XML itself is malformed," and a handshake that
# dies mid-response can leave feedparser holding a truncated, unparseable
# document. Force a current, complete CA bundle (certifi's) as the process
# default rather than trusting whatever the runner ships, so a feed's real
# health isn't confounded by the environment's trust store.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
    ssl._create_default_https_context = lambda: _SSL_CTX
except ImportError:
    _SSL_CTX = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.yaml"
OUT = ROOT / "feeds" / "latest.json"

# A realistic UA — many publishers 403 a blank/python UA but allow a browser-like one.
UA = ("Mozilla/5.0 (compatible; DailyCactusBot/1.0; "
      "+https://amanbeni.github.io/daily-cactus/)")

MAX_PER_FEED = 25      # generous cap; Claude does the real editorial selection.
                       # 8 -> 12 (owner, Jul 2026): sections looked starved
                       # (2-story agritech days) — candidate breadth protects
                       # against missing a good story buried lower in a feed.
                       # 12 -> 25 (29 Jul 2026): at 12, THIRTY-FIVE of 51 feeds
                       # (69%) were hitting the cap — Guardian Tech serves 29
                       # items and contributed 12. The narrowest point in the
                       # whole pipeline was its very first step, and stories
                       # were being discarded before anything had looked at
                       # them. This costs NO model tokens: what the routine
                       # actually reads is bounded separately by
                       # SECTION_HARD_CAP and TOKEN_BUDGET_CHARS in
                       # build_digest.py. It buys more choice at the same spend.
SUMMARY_CHARS = 200    # lighter candidates -> fewer input tokens, no stories lost
                       # (the editor only needs the gist to rank; it rewrites anyway)
HOURS_WINDOW = 36      # keep recent-ish items; routine applies a stricter 24h filter

_IMG_IN_HTML = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def first_img_in_html(*blobs) -> str | None:
    for blob in blobs:
        if not blob:
            continue
        m = _IMG_IN_HTML.search(blob)
        if m:
            return m.group(1)
    return None


def extract_image(entry) -> str | None:
    """Best-effort thumbnail URL, trying the common feed image conventions in
    priority order. Returns None if the entry carries no usable image."""
    # 1. <media:thumbnail> — usually small, ideal for a sidebar thumb
    for thumb in entry.get("media_thumbnail", []) or []:
        if thumb.get("url"):
            return thumb["url"]
    # 2. <media:content> — images only (can also carry video/audio)
    for c in entry.get("media_content", []) or []:
        medium = (c.get("medium") or "").lower()
        ctype = (c.get("type") or "").lower()
        if c.get("url") and (medium == "image" or ctype.startswith("image")):
            return c["url"]
    # 3. <enclosure> with an image MIME type
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("image"):
            if link.get("href"):
                return link["href"]
    # 4. first <img> inside content:encoded / description HTML
    content_html = ""
    if entry.get("content"):
        content_html = entry["content"][0].get("value", "")
    return first_img_in_html(content_html, entry.get("summary", ""))


def entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
    return None


def outlet_name(parsed, fallback: str) -> str:
    return parsed.get("feed", {}).get("title") or fallback


def gnews_fields(entry, title: str):
    """Google News titles look like 'Headline - Publisher' and carry the real
    publisher in a <source> tag. Pull the outlet out and clean the headline so
    the paper attributes the original source, not 'Google News'."""
    outlet = None
    src = entry.get("source")
    if isinstance(src, dict):
        outlet = src.get("title")
    # Always strip a trailing ' - Publisher' from the headline when present.
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if 0 < len(tail) <= 45:
            if not outlet:
                outlet = tail.strip()
            title = head.strip()
    return title, (outlet or "Google News")


def _fetch_feed(url: str):
    """B7 audit fix: fetch the feed ourselves via urllib and hand feedparser
    the raw bytes, instead of letting feedparser.parse(url) do its own fetch.

    Root cause found live (Jul 2026): several "invalid XML" bozo failures
    (nature.com confirmed by direct A/B test) were NOT malformed feeds at all
    — fetching the identical URL with plain urllib and feeding the resulting
    bytes to feedparser.parse(data) parsed cleanly (75 entries), while
    feedparser.parse(url) on the same feed intermittently corrupted a
    multi-byte UTF-8 sequence (a "©" byte pair) and failed with a bogus
    "not well-formed" error. That points at feedparser's own gzip-aware
    fetcher mishandling a compressed response from certain CDNs (nature.com
    sits behind Fastly/Varnish, whose response varies by Accept-Encoding).
    Fetching without requesting compression sidesteps that path entirely.

    Falls back to feedparser's own url-fetch if our urllib request raises
    (keeps existing behavior — e.g. odd redirect handling — as a safety net)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
            raw = resp.read()
        return feedparser.parse(raw)
    except Exception:
        return feedparser.parse(url, agent=UA)


def best_summary(entry) -> str:
    """Prefer the entry summary; fall back to content body (some feeds, e.g.
    Indian Express, ship empty summaries with the text in content)."""
    raw = entry.get("summary", "")
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return strip_html(raw)[:SUMMARY_CHARS]


def main() -> None:
    cfg = yaml.safe_load(SOURCES.read_text())
    now = datetime.datetime.now(datetime.timezone.utc)

    out_sections = []
    feeds_total = feeds_ok = feeds_failed = 0
    failed_feeds = []

    for section in cfg.get("sections", []):
        # Per-section recency window. News defaults to HOURS_WINDOW; sections like
        # Opportunities (events announced ahead of time) set a wider window_hours.
        window = section.get("window_hours", HOURS_WINDOW)
        cutoff = now - datetime.timedelta(hours=window)
        sec_entries, ok, failed = [], 0, 0
        for url in section.get("feeds", []) or []:
            feeds_total += 1
            try:
                if "news.google.com" in url:
                    # v8: ~25 Google News searches from one runner IP — pace
                    # them so Google doesn't answer 429/503 (a silent empty feed).
                    time.sleep(1.5)
                parsed = _fetch_feed(url)
                # bozo with zero entries = a real failure; bozo WITH entries is
                # usually just a sloppy-but-parseable feed, which we accept.
                if parsed.get("bozo") and not parsed.get("entries"):
                    raise RuntimeError(str(parsed.get("bozo_exception", "parse error")))
                src = outlet_name(parsed, section["name"])
                is_gnews = "news.google.com" in url
                count = 0
                for e in parsed.entries:
                    if count >= MAX_PER_FEED:
                        break
                    et = entry_time(e)
                    if et and et < cutoff:      # drop clearly-old items
                        continue
                    link = e.get("link")
                    if not link:
                        continue
                    title = strip_html(e.get("title", "")) or "(untitled)"
                    entry_src = src
                    if is_gnews:
                        if not et:
                            # A5: undated Google News items bypass the recency
                            # cutoff above entirely — drop rather than let a
                            # stale/undated item slip through unchecked. Trusted
                            # publisher feeds are NOT dropped here (their undated
                            # items are merely penalized downstream, since a
                            # missing date there is more likely a feed quirk
                            # than a sign the item is actually old).
                            continue
                        title, entry_src = gnews_fields(e, title)
                        summary = ""              # gnews summaries are noisy link-lists
                    else:
                        summary = best_summary(e)
                    sec_entries.append({
                        "title": title,
                        "link": link,
                        "source": entry_src,
                        "published": et.isoformat() if et else None,
                        "summary": summary,
                        "image_url": extract_image(e),
                        "feed_url": url,   # B8: source audit — which feed this came from
                    })
                    count += 1
                ok += 1
                feeds_ok += 1
            except Exception as ex:                       # noqa: BLE001
                failed += 1
                feeds_failed += 1
                failed_feeds.append({"url": url, "error": str(ex)[:200]})

        out_sections.append({
            "name": section["name"],
            "slug": section.get("slug"),
            "angle": section.get("angle", ""),
            "max_stories": section.get("max_stories"),
            "beta": section.get("beta", False),
            "weekend_only": section.get("weekend_only", False),  # B6
            "window_hours": window,   # A5: carried through so build_digest can
                                      # enforce a per-section hard age cutoff
                                      # redundant to this fetch-time cutoff.
            "feeds_ok": ok,
            "feeds_failed": failed,
            "entries": sec_entries,
        })

    payload = {
        "generated_at": now.isoformat(),
        "stats": {
            "feeds_total": feeds_total,
            "feeds_ok": feeds_ok,
            "feeds_failed": feeds_failed,
            "total_entries": sum(len(s["entries"]) for s in out_sections),
        },
        "failed_feeds": failed_feeds,
        "sections": out_sections,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"Feeds OK: {feeds_ok}/{feeds_total} | entries: {payload['stats']['total_entries']}")
    if failed_feeds:
        print("Failed feeds:")
        for f in failed_feeds:
            print(f"  - {f['url']}  ->  {f['error']}")


if __name__ == "__main__":
    main()
