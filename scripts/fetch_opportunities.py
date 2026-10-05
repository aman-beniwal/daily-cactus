#!/usr/bin/env python3
"""P7 — real event/opportunity sources -> feeds/opportunities.json.

WHY THIS EXISTS
---------------
The Opportunities & Events section used to be fed entirely by Google News
queries. Verified live (2026-07-18): those queries return only RETROSPECTIVE
coverage ("... Summit concludes ...") and exam-prep recaps — zero forward-
dated items with a registration link, because news feeds structurally can't
supply upcoming events. Three consecutive editions published zero
opportunities as a result (see ISSUES_BACKLOG.md P7).

This script queries four REAL event/opportunity sources instead and writes
feeds/opportunities.json in the same candidate shape build_digest.py already
understands (title/link/source/published/summary/image_url), PLUS structured
`event_date`/`deadline` ISO fields so build_digest.py can hard-drop anything
already expired on a REAL date rather than a title-regex guess:

  1. Unstop  (primary) — JSON API, no key. India's dominant opportunity board.
  2. 10times.com — HTML scrape of two category pages (city pages are
     Cloudflare-403'd — do not use those).
  3. Lu.ma — the __NEXT_DATA__ JSON embedded in each city's discover page.
  4. Devpost — JSON API, requires a desktop Chrome UA or it 403s.

Runs on GitHub Actions (.github/workflows/fetch.yml), before build_digest.py.

ALL FOUR ARE UNDOCUMENTED / REVERSE-ENGINEERED ENDPOINTS EXCEPT DEVPOST'S
PUBLIC API — they WILL break eventually. Every source fetch is wrapped so one
source's failure can never affect another's, or crash the pipeline; per-source
counts are always printed so breakage is visible immediately instead of
silently going to zero (exactly how the all-Google-News version's zero-
opportunity days went unnoticed for 3 runs).
"""
import datetime
import html as htmllib
import json
import os
import pathlib
import re
import ssl
import urllib.parse
import urllib.request
import feedparser

import editorial as ed

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "feeds" / "opportunities.json"

TIMEOUT = 15
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None


def _get(url, headers=None, timeout=TIMEOUT):
    """Return a public page body; callers isolate source failures."""
    h = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def _get_json(url, headers=None, timeout=TIMEOUT):
    body = _get(url, headers=headers, timeout=timeout)
    return json.loads(body) if body else None


def _iso(dt_str):
    """Best-effort coercion of a source's date string to a bare ISO date
    (YYYY-MM-DD). Returns None rather than raising on anything unparseable."""
    if not dt_str:
        return None
    s = str(dt_str).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return m.group(0)


# ---------------------------------------------------------------------------
# 1. Unstop
# ---------------------------------------------------------------------------
UNSTOP_TYPES = ("hackathons", "competitions")
UNSTOP_SEARCH_TERMS = ("AI", "deep tech", "drone", "quantum", "climate", "startup")

# Quality filter (backlog: "require a meaningful prize, a credible organizer,
# or a domain-matching title") — Unstop's own corpus skews heavily toward
# college quiz/contest noise, so this is a real gate, not a formality.
_NOISE_TITLE_TERMS = ("quiz", "trivia", "general knowledge", "rangoli",
                      "poster making", "essay writing", "treasure hunt",
                      "meme making", "reel making", "photography contest")
_CREDIBLE_ORG_TERMS = ("iit", "iim", "nit ", "bits ", "government", "ministry",
                       "aicte", "isro", "drdo", "startup india", "nasscom",
                       "google", "microsoft", "amazon", "meta", "nvidia",
                       "ieee", "unesco", "world bank", "niti aayog")
_DOMAIN_TITLE_TERMS = ("ai", "artificial intelligence", "machine learning",
                       "deep tech", "deeptech", "drone", "quantum", "climate",
                       "startup", "genai", "gen ai", "agentic", "robotics",
                       "space tech", "semiconductor")


# P11 (2026-07-19 review): the first cut of this filter kept 74% of Unstop and
# the section filled with school/campus contests — "U-19 AI Olympics", "AI
# Hackathon For Schools", "Newgen x AI Club IITM", "Freshers Party Planning
# Challenge". Cause: a bare `domain_match` (title contains "ai") was enough to
# pass on its own, and virtually every one of these has "AI" in the title.
# The reader is a professional pivoting to founder's-office / AI-generalist
# roles — student competitions are noise for him. Two changes: a HARD reject
# for campus/student framing, and domain-match alone no longer passes.
_STUDENT_NOISE_TERMS = (
    "school", "freshers", "fresher", "u-19", "u19", "u-16", "u16",
    "class 9", "class 10", "class 11", "class 12", "11th", "12th",
    "intra-college", "inter-college", "intra college", "inter college",
    "college club", "student chapter", "campus ambassador", "olympiad",
    "cultural fest", "college fest", "annual fest",
    # student bodies that run campus events under a professional-sounding name
    "ai club", "coding club", "tech club", "robotics club", "e-cell",
    "entrepreneurship cell", "student council",
)
MIN_MEANINGFUL_PRIZE = 25_000      # INR — below this it's a campus giveaway

# P11: 10times' India pages carry a long tail of near-identical "International
# Conference on <two buzzwords>" listings — the WASET-style academic mill
# circuit (three landed in the 2026-07-18 digest at once). They are not events
# this reader would ever attend. The genuinely useful 10times items are named
# industry events (DataHack Summit, Automation & Robotics Expo, BFSI Innovation
# Summit), which this pattern leaves untouched.
_CONFERENCE_MILL_RE = re.compile(
    r"^\s*(international|world|global)\s+(conference|congress|symposium)\s+on\b", re.I)


def _is_conference_mill(title: str) -> bool:
    return bool(_CONFERENCE_MILL_RE.match(title or ""))


def _unstop_quality_ok(title, prizes, organisation):
    tl = (title or "").lower()
    org = organisation or {}
    org_name = (org.get("name") or "").lower()
    blob = f"{tl} {org_name}"
    if any(t in tl for t in _NOISE_TITLE_TERMS):
        return False
    if any(t in blob for t in _STUDENT_NOISE_TERMS):
        return False                                   # hard reject, no appeal
    best_prize = max((p.get("cash") or 0)
                     for p in (prizes or []) if isinstance(p, dict)) if prizes else 0
    meaningful_prize = best_prize >= MIN_MEANINGFUL_PRIZE
    credible_org = any(t in org_name for t in _CREDIBLE_ORG_TERMS)
    domain_match = any(t in tl for t in _DOMAIN_TITLE_TERMS)
    # domain_match is a BONUS, never a pass on its own: it must be backed by
    # real money or a credible (non-campus) organiser.
    return meaningful_prize or credible_org or (domain_match and best_prize > 0)


def fetch_unstop():
    items, seen_ids = [], set()
    fetched = kept = 0
    for opp_type in UNSTOP_TYPES:
        for term in UNSTOP_SEARCH_TERMS:
            url = ("https://unstop.com/api/public/opportunity/search-result"
                   f"?opportunity={opp_type}&page=1&per_page=30"
                   f"&searchTerm={urllib.parse.quote(term)}")
            try:
                data = _get_json(url)
            except Exception as ex:                        # noqa: BLE001
                print(f"fetch_opportunities: unstop [{opp_type}/{term}] failed: {ex!r}")
                continue
            rows = ((data or {}).get("data") or {}).get("data") or []
            fetched += len(rows)
            for it in rows:
                oid = it.get("id")
                if oid is None or oid in seen_ids:
                    continue
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                if not _unstop_quality_ok(title, it.get("prizes"), it.get("organisation")):
                    continue
                seo_url = it.get("seo_url") or (
                    f"https://unstop.com/{it['public_url']}" if it.get("public_url") else None)
                if not seo_url:
                    continue
                regn = it.get("regnRequirements") or {}
                eligibility = regn.get("eligibility") or ""
                if isinstance(eligibility, str):
                    try:
                        eligibility = json.loads(eligibility)
                    except ValueError:
                        eligibility = {}
                sectors = eligibility.get("sector") or [] if isinstance(eligibility, dict) else []
                if "students" in sectors and not any(x in sectors for x in ("professionals", "working professionals")):
                    continue
                deadline = _iso(regn.get("end_regn_dt"))
                festival = it.get("festival") or {}
                event_date = _iso(festival.get("start_date")) if isinstance(festival, dict) else None
                org_name = (it.get("organisation") or {}).get("name", "")
                when_bits = []
                if event_date:
                    when_bits.append(f"runs {event_date}")
                if deadline:
                    when_bits.append(f"register by {deadline}")
                when_txt = "; ".join(when_bits) or "dates on registration page"
                place = regn.get("work_location_type") or ""
                summary = f"{opp_type.rstrip('s').title()} hosted by {org_name or 'organizer TBC'}. {place}. {when_txt}."
                items.append({
                    "title": title,
                    "link": seo_url,
                    "source": "Unstop",
                    "published": None,
                    "summary": summary,
                    "image_url": None,
                    "feed_url": f"opportunities:unstop:{opp_type}:{term}",
                    "event_date": event_date,
                    "deadline": deadline,
                    "organizer": org_name,
                })
                seen_ids.add(oid)
                kept += 1
    print(f"fetch_opportunities: unstop — {fetched} fetched, {kept} kept after quality filter")
    return items


# ---------------------------------------------------------------------------
# 2. 10times.com
# ---------------------------------------------------------------------------
TENTIMES_PAGES = (
    ("https://10times.com/india/technology", "10times (India Tech)"),
    ("https://10times.com/india/artificial-intelligence", "10times (India AI)"),
)


def _parse_10times(html_text):
    """Each event lives inside one 'event-card event_<id>' block. We bound
    each block from its own marker to the NEXT marker so we never pick up
    matches from an unrelated 'related events' widget elsewhere on the page
    (verified live: that widget carries its own date/url/label triples with
    no 'event-card event_' wrapper at all)."""
    events = []
    starts = [m.start() for m in re.finditer(r"event-card event_\d+", html_text)]
    starts.append(len(html_text))
    for i in range(len(starts) - 1):
        block = html_text[starts[i]:starts[i + 1]]
        date_m = re.search(r'data-start-date="([\d/]+)"', block)
        url_m = re.search(r"onclick=\"window\.open\('([^']+)'\)\"", block)
        label_m = re.search(r'data-ga-label="To ([^"]+)"', block)
        if not (date_m and url_m and label_m):
            continue
        events.append({
            "title": htmllib.unescape(label_m.group(1)),
            "link": url_m.group(1),
            "event_date": date_m.group(1).replace("/", "-"),
        })
    return events


def fetch_10times():
    items = []
    fetched = kept = 0
    for url, label in TENTIMES_PAGES:
        try:
            html_text = _get(url)
        except Exception as ex:                            # noqa: BLE001
            print(f"fetch_opportunities: 10times [{url}] failed: {ex!r}")
            continue
        if not html_text:
            print(f"fetch_opportunities: 10times [{url}] returned empty body")
            continue
        events = _parse_10times(html_text)
        fetched += len(events)
        for e in events:
            if _is_conference_mill(e["title"]):
                continue          # P11: skip predatory/no-name academic mills
            ed = _iso(e["event_date"])
            items.append({
                "title": e["title"],
                "link": e["link"],
                "source": label,
                "published": None,
                "summary": f"Listed on 10times. {'Runs ' + ed if ed else 'Date on event page'}.",
                "image_url": None,
                "feed_url": f"opportunities:10times:{url}",
                "event_date": day,
                "deadline": None,
            })
            kept += 1
    print(f"fetch_opportunities: 10times — {fetched} fetched, {kept} kept")
    return items


# ---------------------------------------------------------------------------
# 3. Lu.ma
# ---------------------------------------------------------------------------
LUMA_CITIES = ("new-delhi",)
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_luma():
    items = []
    fetched = kept = 0
    for city in LUMA_CITIES:
        url = f"https://lu.ma/{city}"
        try:
            html_text = _get(url)
        except Exception as ex:                            # noqa: BLE001
            print(f"fetch_opportunities: luma [{city}] failed: {ex!r}")
            continue
        m = _NEXT_DATA_RE.search(html_text or "")
        if not m:
            print(f"fetch_opportunities: luma [{city}] — no __NEXT_DATA__ found")
            continue
        try:
            data = json.loads(m.group(1))
            events = data["props"]["pageProps"]["initialData"]["data"]["events"]
        except Exception as ex:                            # noqa: BLE001
            print(f"fetch_opportunities: luma [{city}] — unexpected JSON shape: {ex!r}")
            continue
        fetched += len(events)
        for wrapper in events:
            ev = (wrapper or {}).get("event") or {}
            name = ev.get("name")
            slug = ev.get("url")
            start_at = ev.get("start_at")
            if not (name and slug and start_at) or not ed.OPP_TOPIC.search(name):
                continue
            day = _iso(start_at)
            items.append({
                "title": name,
                "link": f"https://lu.ma/{slug}",
                "source": f"Luma ({city.replace('-', ' ').title()})",
                "published": None,
                "summary": f"In-person in {city.replace('-', ' ').title()} via Luma. {'On ' + day if day else 'Date on event page'}.",
                "image_url": ev.get("cover_url"),
                "feed_url": f"opportunities:luma:{city}",
                "event_date": day,
                "deadline": None,
            })
            kept += 1
    print(f"fetch_opportunities: luma — {fetched} fetched, {kept} kept")
    return items


# ---------------------------------------------------------------------------
# 4. Devpost
# ---------------------------------------------------------------------------
def fetch_devpost():
    url = "https://devpost.com/api/hackathons?status[]=upcoming&order_by=recently-added"
    try:
        # REQUIRES a full desktop Chrome UA or it 403s (backlog note, verified).
        data = _get_json(url, headers={"User-Agent": BROWSER_UA})
    except Exception as ex:                                # noqa: BLE001
        print(f"fetch_opportunities: devpost failed: {ex!r}")
        return []
    rows = (data or {}).get("hackathons") or []
    items = []
    for h in rows:
        title = (h.get("title") or "").strip()
        link = h.get("url")
        if not (title and link):
            continue
        submission_dates = h.get("submission_period_dates") or ""
        place = ((h.get("displayed_location") or {}).get("location") or "").strip()
        organizer = (h.get("organization_name") or "").strip()
        online = place.lower() in ("online", "")
        items.append({
            "title": title,
            "link": link,
            "source": "Devpost",
            "published": None,
            "summary": f"{'Online' if online else 'In-person at ' + place} hackathon via Devpost. {submission_dates or 'Dates on event page'}.",
            "organizer": organizer,
            "image_url": ("https:" + h["thumbnail_url"]) if h.get("thumbnail_url", "").startswith("//") else h.get("thumbnail_url"),
            "feed_url": "opportunities:devpost",
            "event_date": None,   # Devpost gives a free-text date range, not ISO — leave
            "deadline": None,     # unset rather than guess; build_digest's regex fallback
                                  # (extract_event_date over title+summary) still applies.
        })
    print(f"fetch_opportunities: devpost — {len(rows)} fetched, {len(items)} kept")
    return items


def _plain(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _named_date(s):
    m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d\d)\b", s or "", re.I)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(0).replace(",", ""), "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def _when_iso(s, year):
    m = re.search(r"20\d\d-\d\d-\d\d", s or "")
    if m:
        return m.group(0)
    named = _named_date(s)
    if named:
        return named
    m = re.search(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d\d)\b", s or "", re.I)
    if m:
        try:
            return datetime.datetime.strptime(m.group(0), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(?:due|apply by)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b", s or "", re.I)
    if m:
        try:
            return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {year}", "%B %d %Y").date().isoformat()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", s or "", re.I)
    if m:
        try:
            return datetime.datetime.strptime(f"{m.group(1)} {m.group(2)} {year}", "%d %b %Y").date().isoformat()
        except ValueError:
            pass
    return None


def fetch_opportunity_desk():
    """RSS descriptions begin with an explicit deadline; other posts are skipped."""
    items, seen = [], set()
    for page in range(1, 4):
        url = f"https://opportunitydesk.org/category/fellowships/feed/?paged={page}"
        try:
            rows = feedparser.parse(_get(url)).entries
        except Exception as ex:                         # noqa: BLE001
            print(f"fetch_opportunities: Opportunity Desk page {page} failed: {ex!r}")
            continue
        for row in rows:
            title, link = _plain(row.get("title")), row.get("link")
            summary = _plain(row.get("summary"))
            deadline_m = re.search(r"\bDeadline:\s*([^.]*)", summary, re.I)
            deadline = _named_date(deadline_m.group(1)) if deadline_m else None
            if not (title and link and deadline) or link in seen:
                continue
            if not re.search(r"\b(fellowship|fellows)\b", title, re.I):
                continue
            if re.search(r"\b(undergraduate|PhD|postdoctoral|scholars|reporting)\b", title, re.I):
                continue
            if not re.search(r"\b(ai|governance|policy|climate|environment|sustainab|nature)\w*\b", title + " " + summary, re.I):
                continue
            if re.search(r"\b(New York City|Washington, DC|Africa's|Africa’s|African|PhD research|graduate students?)\b", summary, re.I):
                continue
            items.append({"title": title, "link": link, "source": "Opportunity Desk",
                          "published": row.get("published"), "summary": summary[:500],
                          "image_url": None, "feed_url": url, "event_date": None,
                          "deadline": deadline, "kind": "fellowship"})
            seen.add(link)
    print(f"fetch_opportunities: Opportunity Desk — {len(items)} dated fellowships")
    return items


def fetch_meetup_jaipur():
    """Meetup's public Jaipur page exposes schema.org Event JSON-LD."""
    page = _get("https://www.meetup.com/find/in--jaipur/")
    items, seen = [], set()
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for event in data if isinstance(data, list) else [data]:
            if not isinstance(event, dict) or event.get("@type") != "Event":
                continue
            title, link = event.get("name", "").strip(), event.get("url")
            if not (title and link) or link in seen or not ed.OPP_TOPIC.search(title):
                continue
            location = event.get("location") or {}
            city = (location.get("address") or {}).get("addressLocality", "") if isinstance(location, dict) else ""
            if city.lower() != "jaipur":
                continue
            end_date = _iso(event.get("endDate"))
            title_range = re.search(r"\b\d{1,2}\s+[A-Z][a-z]{2}\s*[-–]\s*(\d{1,2})\s+([A-Z][a-z]{2})\b", title)
            if title_range and _iso(event.get("startDate")):
                try:
                    end_date = datetime.datetime.strptime(
                        f"{title_range.group(1)} {title_range.group(2)} {_iso(event['startDate'])[:4]}",
                        "%d %b %Y").date().isoformat()
                except ValueError:
                    pass
            items.append({"title": title, "link": link, "source": "Meetup (Jaipur)",
                          "published": None, "summary": "In-person in Jaipur. " + _plain(event.get("description"))[:300],
                          "image_url": None, "feed_url": "opportunities:meetup:jaipur",
                          "event_date": _iso(event.get("startDate")),
                          "event_end_date": end_date, "deadline": None})
            seen.add(link)
    print(f"fetch_opportunities: Meetup Jaipur — {len(items)} dated relevant events")
    return items


def fetch_booth():
    """Official Booth admissions table, restricted to virtual MBA sessions."""
    url = "https://www.chicagobooth.edu/mba/full-time/admissions/events"
    page = _get(url)
    items = []
    for m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", page, re.S | re.I):
        block = m.group(1)
        title_m = re.search(r"<th\b[^>]*>(.*?)</th>", block, re.S | re.I)
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", block, re.S | re.I)
        link_m = re.search(r'<a\b[^>]*href="([^"]+)"', block, re.I)
        if not (title_m and len(cells) >= 3 and link_m):
            continue
        title = re.sub(r"\s+1\s*v$", "", _plain(title_m.group(1)))
        day = _named_date(_plain(cells[2]))
        if not day or "virtual" not in title.lower() or "deferred" in title.lower():
            continue
        items.append({"title": title, "link": htmllib.unescape(link_m.group(1)),
                      "source": "Chicago Booth", "published": None,
                      "summary": "Virtual MBA admissions event from Chicago Booth.",
                      "image_url": None, "feed_url": "opportunities:booth",
                      "event_date": day, "deadline": None, "kind": "mba"})
    print(f"fetch_opportunities: Chicago Booth — {len(items)} dated virtual sessions")
    return items


def recent_published(today):
    """Read the last seven published papers, locally on Actions or via Pages."""
    urls, titles = set(), set()
    local = pathlib.Path(os.environ.get("EDITIONS_DIR", "")) if os.environ.get("EDITIONS_DIR") else None
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "aman-beniwal/daily-cactus").split("/", 1)
    base = f"https://{owner_repo[0].lower()}.github.io/{owner_repo[-1]}/editions"
    loaded = 0
    for offset in range(8):
        day = (today - datetime.timedelta(days=offset)).isoformat()
        try:
            if local and local.is_dir():
                edition = json.loads((local / f"{day}.json").read_text())
            else:
                edition = _get_json(f"{base}/{day}.json", timeout=8)
        except Exception:                               # noqa: BLE001 — missing paper
            continue
        if not isinstance(edition, dict):
            continue
        loaded += 1
        for opp in edition.get("opportunities") or []:
            if not isinstance(opp, dict):
                continue
            if opp.get("url"):
                urls.add(opp["url"].split("?", 1)[0].rstrip("/"))
            if opp.get("name"):
                event_day = _when_iso(opp.get("when"), int(day[:4]))
                if event_day:
                    titles.add((re.sub(r"\W+", "", opp["name"].casefold()), event_day))
    print(f"fetch_opportunities: repeat memory — {loaded} published papers, {len(urls)} links")
    return urls, titles


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    today = (now + datetime.timedelta(hours=5, minutes=30)).date()
    past_urls, past_titles = recent_published(today)
    all_items = []
    counts = {}
    for name, fn in (("unstop", fetch_unstop), ("10times", fetch_10times),
                     ("luma", fetch_luma), ("devpost", fetch_devpost),
                     ("opportunity_desk", fetch_opportunity_desk),
                     ("meetup_jaipur", fetch_meetup_jaipur), ("booth", fetch_booth)):
        try:
            got = fn()
        except Exception as ex:                            # noqa: BLE001 — one source
            print(f"fetch_opportunities: {name} crashed unexpectedly: {ex!r}")
            got = []
        kept = []
        for item in got:
            item["kind"] = item.get("kind") or ed.opportunity_kind(item)
            item["when"] = ed.opportunity_when(item)
            reason = ed.opportunity_rejection(item, today)
            if reason == "no concrete date":            # build_digest re-checks with the date parsed from text
                reason = None
            url_key = item["link"].split("?", 1)[0].rstrip("/")
            title_key = re.sub(r"\W+", "", item["title"].casefold())
            event_day = item.get("deadline") or item.get("event_date")
            if reason is None and url_key not in past_urls and (title_key, event_day) not in past_titles:
                kept.append(item)
        if name == "booth":                            # a school's whole calendar would crowd out everything else
            kept = sorted(kept, key=lambda i: i["event_date"])[:4]
        counts[name] = len(kept)
        all_items.extend(kept)

    payload = {
        "generated_at": now.isoformat(),
        "source_counts": counts,
        "items": all_items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT}: {len(all_items)} total items "
          f"({', '.join(f'{k}={v}' for k, v in counts.items())})")


if __name__ == "__main__":
    main()
