"""v7 editorial layer for build_digest.py — the news judgment a desk editor
applies BEFORE the selector ever sees a story.

WHY THIS EXISTS (audit of 24 Aug - 23 Sep 2026, 31 editions, ~4,800 candidates)
-------------------------------------------------------------------------------
The old ranker was recency + keyword hits + a few hand weights. Measured
against what the editor actually picked, it failed in five repeatable ways:

1. REPEATS. The cross-day dedup read editions from the pre-rename GitHub Pages
   URL, got a 404 from 27 Aug onward, and silently skipped. 156 published
   items in the month were re-runs of a story from the previous 7 days (vs 3
   in the three days before the break); the lead itself repeated on 14/15,
   16/17 and 19/20 Sep. Exact-URL dedup alone would also miss the ~25% of
   repeats that came back via a different outlet. -> `RecentMemory`.
2. SECTION MISFITS. Mongabay conservation pieces filled Agritech (82 shortlist
   slots in a month), general India news and Bangladeshi/EU startups filled
   Indian Startups ("India | The Guardian": 28 slots, 0 picked). -> `section_fit`.
3. PR / SPONSORED COPY reached the selector with nothing marking it: company
   newsrooms, vendor research, stock-tip explainers, "(ANI)"-wire rewrites.
   -> `pr_cues` (cheap, explainable signals only; see the research notes in
   EDITORIAL_STANDARDS.md).
4. OPPORTUNITIES were ranked by the date an item was POSTED. The ~170 real,
   dated events Unstop/Luma/Devpost supply every day carry no post date, so
   they scored as "undated" and lost every day to foreign film festivals and
   NGO grant listings. -> `score_opportunity` ranks by WHEN IT HAPPENS, WHERE,
   and whether this reader would go.
5. CORROBORATION was measured by identical 8-word title prefixes, which only
   fires for syndicated copies. -> `corroboration` counts distinct outlets
   carrying the same story under different headlines (the Techmeme signal).

Plus one self-correcting prior: `SourceYield` learns, from the last 30 days,
how often the editor actually picks each source once shortlisted, and nudges
chronic never-picked sources down (and reliably-picked ones up). It is a
nudge (clipped), rate-based (so a demoted source drifts back to neutral as its
sample shrinks), and it never removes a source — widening the fetch stays
the policy; this only decides who gets the scarce shortlist slots.

Everything here is deterministic, free (runs on GitHub Actions), and a
RECALL-SAFE nudge: nothing but sponsored/paid content and exact repeats is
dropped outright. The selector still makes every editorial call.
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import re
from collections import Counter, defaultdict

# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------
_STOP = set("""
the a an of to in on for and as with its is at by from after over says said
amid into new up how why what will could may be are has have was were it this
that than then about more most also been would year years first global news
report reports week day days just not but out who all can her his their they
our your you we us one two three per via vs set sets gets get make makes back
""".split())
_TOK = re.compile(r"[a-z0-9₹$€£%.]+")
_SUFFIX = re.compile(r"\s+[-|–—]\s+[^-|–—]{2,60}$")      # " - Reuters" / " | Mint"


def clean_title(t: str) -> str:
    return _SUFFIX.sub("", (t or "").replace("==", "").replace("__", "")).strip()


def sig_tokens(t: str) -> set:
    t = clean_title(t).lower().replace("’", "'")
    out = set()
    for w in _TOK.findall(t):
        w = w.strip(".")
        if len(w) < 3 and not any(c.isdigit() for c in w):
            continue
        if w in _STOP:
            continue
        out.add(w)
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_story(a: set, b: set) -> float:
    """Similarity in [0,1] tuned for 'same event, different headline'.
    Plain Jaccard is too strict across outlets (headlines differ in length),
    so a strong raw overlap on a short headline also counts."""
    if not a or not b:
        return 0.0
    j = jaccard(a, b)
    ov = len(a & b)
    small = min(len(a), len(b))
    containment = ov / small if small else 0.0
    if ov >= 4 and containment >= 0.6:
        return max(j, 0.55)
    return j


def word_re(terms) -> re.Pattern:
    """Whole-word matcher. The old INTEREST_TERMS used substring matching, so
    'ai' fired on 'said', 'again', 'claim' — nearly every story got the AI
    bonus. Word boundaries fix that."""
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(terms) + r")(?![a-z0-9])", re.I)


# --------------------------------------------------------------------------
# 1. interest (whole-word, reader-weighted)
# --------------------------------------------------------------------------
# Tiered: the reader's main domain and target ecosystem count more.
INTEREST_TIERS = [
    (1.6, word_re([r"ai", r"a\.i\.", r"artificial intelligence", r"genai",
                   r"generative ai", r"llms?", r"openai", r"anthropic", r"deepmind",
                   r"gemini", r"chatgpt", r"claude", r"nvidia", r"gpus?",
                   r"agentic", r"ai agents?", r"foundation models?", r"sarvam",
                   r"krutrim", r"indiaai"])),
    (1.5, word_re([r"isro", r"drdo", r"in-space", r"inspace", r"spacetech",
                   r"space[- ]tech", r"satellites?", r"launch vehicle", r"rocket",
                   r"skyroot", r"agnikul", r"pixxel", r"dhruva", r"semiconductors?",
                   r"chips?", r"fab", r"semicon", r"osat", r"drones?", r"uavs?",
                   r"defence[- ]tech", r"defense[- ]tech", r"robotics?", r"humanoids?",
                   r"quantum", r"deep ?tech", r"ideaforge", r"tonbo"])),
    (1.3, word_re([r"startups?", r"founders?", r"unicorns?", r"funding",
                   r"series [a-f]", r"seed round", r"raises?", r"raised",
                   r"valuation", r"ipo", r"venture", r"vcs?", r"acquires?",
                   r"acquisition", r"layoffs?", r"d2c", r"quick commerce"])),
    (1.2, word_re([r"india", r"indian", r"indias", r"bharat", r"modi", r"rbi",
                   r"sebi", r"meity", r"niti aayog", r"upi", r"ondc", r"aadhaar",
                   r"digital public", r"bengaluru", r"bangalore", r"jaipur",
                   r"rajasthan", r"delhi", r"mumbai"])),
    (1.0, word_re([r"climate", r"renewables?", r"solar", r"wind power",
                   r"batter(?:y|ies)", r"energy storage", r"green hydrogen",
                   r"evs?", r"electric vehicles?", r"carbon", r"emissions",
                   r"grid", r"nuclear", r"heatwave", r"monsoon"])),
    (1.0, word_re([r"economy", r"gdp", r"inflation", r"tariffs?", r"trade deal",
                   r"interest rates?", r"central bank", r"fed", r"recession",
                   r"exports?", r"sanctions", r"fdi", r"capex"])),
    (0.8, word_re([r"health ?tech", r"digital health", r"medtech", r"biotech",
                   r"drug", r"clinical trial", r"fda", r"cdsco", r"neuroscience",
                   r"brain", r"agritech", r"agtech", r"farmers?", r"crops?"])),
    (0.7, word_re([r"policy", r"regulation", r"regulator", r"law", r"court",
                   r"ruling", r"ban", r"antitrust", r"strategy"])),
]


def interest_score(text: str) -> float:
    s = 0.0
    for weight, rx in INTEREST_TIERS:
        if rx.search(text or ""):
            s += weight
    return min(s, 5.0)


# --------------------------------------------------------------------------
# 2. section fit
# --------------------------------------------------------------------------
INDIA_CUE = word_re([
    r"india", r"indian", r"indias", r"bharat", r"modi", r"isro", r"drdo", r"iit\w*",
    r"iim\w*", r"rbi", r"sebi", r"meity", r"dpiit", r"nasscom", r"niti aayog",
    r"upi", r"ondc", r"crore", r"lakh", r"₹", r"rs\.?", r"inr", r"bengaluru",
    r"bangalore", r"mumbai", r"delhi", r"new delhi", r"gurugram", r"gurgaon",
    r"noida", r"hyderabad", r"chennai", r"pune", r"kolkata", r"ahmedabad",
    r"jaipur", r"kochi", r"chandigarh", r"lucknow", r"indore", r"coimbatore",
    r"tata", r"reliance", r"jio", r"infosys", r"wipro", r"hcl\w*", r"adani",
    r"mahindra", r"zomato", r"eternal", r"swiggy", r"zepto", r"flipkart",
    r"paytm", r"phonepe", r"ola", r"byju'?s", r"nykaa", r"meesho", r"razorpay",
    r"cred", r"groww", r"zerodha", r"sarvam", r"krutrim", r"skyroot", r"agnikul",
    r"pixxel", r"ideaforge"])
INDIA_SOURCES = ("inc42", "yourstory", "entrackr", "the hindu", "times of india",
                 "economic times", "et ", "livemint", "mint", "moneycontrol",
                 "business standard", "indian express", "ndtv", "india today",
                 "hindustan times", "theprint", "the print", "medianama",
                 "analytics india", "indian startup news", "vccircle", "the ken",
                 "morning context", "businessline", "financial express",
                 "deccan herald", "news on air", "newsonair", "firstpost",
                 "scroll", "the wire", "outlook")
AGRI_CUE = word_re([
    r"agri\w*", r"agtech", r"farm\w*", r"crops?", r"harvest\w*", r"fertili[sz]er\w*",
    r"irrigation", r"monsoon", r"kharif", r"rabi", r"msp", r"mandis?", r"dairy",
    r"milk", r"livestock", r"poultry", r"cattle", r"fisher\w*", r"aquaculture",
    r"soil", r"seeds?", r"food", r"foods", r"foodtech", r"grains?", r"wheat",
    r"rice", r"paddy", r"pulses", r"sugar\w*", r"cotton", r"tractors?",
    r"pesticides?", r"drought", r"horticulture", r"plantations?", r"tea",
    r"coffee", r"cocoa", r"orchards?", r"greenhouse", r"vertical farm\w*",
    r"alternative protein", r"cultivat\w*", r"yield", r"agroforestry",
    r"food security", r"edible oil", r"onion", r"tomato", r"potato"])

# Outlets whose startup/deep-tech coverage is Indian by construction. General
# Indian newsrooms (India Today, TOI…) also run foreign stories, so for the
# India startup sections only the text itself or these outlets prove the fit.
INDIA_STARTUP_SOURCES = ("inc42", "yourstory", "entrackr", "indian startup news",
                         "indian startup times", "vccircle", "et startups", "the ken",
                         "morning context", "medianama")
# slug -> (cue regex, also-accepted sources, penalty when neither matches)
SECTION_FIT = {
    "indian-startups": (INDIA_CUE, INDIA_STARTUP_SOURCES, -1.5),   # India first; global mega-moves may compete
    "india-deep-tech": (INDIA_CUE, INDIA_STARTUP_SOURCES, -4.0),
    "india": (INDIA_CUE, INDIA_SOURCES, -3.0),
    "agritech": (AGRI_CUE, ("agfundernews", "agfunder"), -4.0),
}


def section_fit(slug: str, entry) -> float:
    rule = SECTION_FIT.get(slug)
    if not rule:
        return 0.0
    rx, ok_sources, penalty = rule
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    if rx.search(text):
        return 0.0
    src = (entry.get("source") or "").lower()
    if any(s in src for s in ok_sources):
        return 0.0
    return penalty


# --------------------------------------------------------------------------
# 3. PR / sponsored / churnalism
# --------------------------------------------------------------------------
# Tier 1 — PAID content dressed as news. Dropped outright: an advertorial is
# not a candidate at any score. Markers verified for Indian outlets by
# Newslaundry's investigations (see EDITORIAL_STANDARDS.md).
SPONSORED_URL = re.compile(
    r"/(?:brand-?connect|brandconnect|spotlight|et-spotlight|partner-content|"
    r"partnered|sponsored|advertorial|impact-feature|brand-studio|brandstudio|"
    r"paid-content|brand-post|brandpost|promoted|consumer-connect)(?:/|-|$)", re.I)
SPONSORED_TEXT = re.compile(
    r"\b(?:advertorial|sponsored (?:content|post|feature)|brand connect|"
    r"consumer connect initiative|impact feature|partner content|paid content|"
    r"this (?:story|article) is provided by newsvoir|provided by (?:pnn|newsvoir)|"
    r"ht brand studio|et spotlight|mediawire)\b", re.I)

# Tier 2 — press-release wires, company newsrooms, vendor blogs. Strong demote.
PR_SOURCES = (
    "pib", "press information bureau", "prnewswire", "pr newswire",
    "businesswire", "business wire", "globenewswire", "globe newswire",
    "einpresswire", "ein presswire", "prweb", "accesswire", "newswire",
    "openpr", "prlog", "newsvoir", "pnn", "ani news", "ani", "linkedin", "instagram",
    "instagram.com", "facebook", "x.com", "twitter", "medium.com",
    "substack.com/pub", "simplywall.st", "simply wall st", "barchart",
    "marketbeat", "zacks", "the motley fool", "motley fool", "benzinga",
    "tradingview", "stock titan", "stocktitan", "globalbankingandfinance",
    "pulse 2.0", "ai insider", "the ai insider", "quantum computing report",
    "fundsforngos", "global south opportunities", "msme africa",
    "tradingkey", "traders union", "gktoday", "insights ias", "vajiram",
    "drishti ias", "nextias", "autopunditz", "todaypress", "ua.news",
    "konsulteer", "devdiscourse")
PR_URL = re.compile(
    r"(?:/newsroom/|/press-?releases?/|/press/|/media-release|/company/news/|"
    r"/news-releases?/|/investor-relations|/ir/|/pressroom|"
    r"blogs\.nvidia\.com|blog\.google|openai\.com/index/|aws\.amazon\.com/blogs|"
    r"azure\.microsoft\.com/[a-z-]+/blog|ibm\.com/blog|cloud\.google\.com/blog|"
    r"news\.microsoft\.com|about\.fb\.com/news|newsroom\.|press\.|ir\.)", re.I)
# Company-owned domains that feed Google News as if they were outlets.
COMPANY_SOURCE = re.compile(
    r"^(?:xpeng|tata electronics|tata|openai|anthropic|google|microsoft|nvidia|"
    r"meta|amazon|apple|infosys|wipro|reliance|adani|hcl\w*|ibm|intel|amd|"
    r"qualcomm|samsung|united nations in india|paris peace forum|"
    r"apollo global management|akamai)(?:\.com)?$", re.I)

# Tier 2/3 lexical cues. Each hit is weak alone; they add up.
PR_PHRASES = re.compile(
    r"\b(?:today announced|announced today|is (?:pleased|proud|thrilled|excited) "
    r"to (?:announce|launch|unveil|partner)|(?:thrilled|proud|excited|delighted) "
    r"to (?:announce|launch|unveil|partner|share)|leading provider of|"
    r"industry[- ]leading|best[- ]in[- ]class|world[- ]class|cutting[- ]edge|"
    r"state[- ]of[- ]the[- ]art|first[- ]of[- ]its[- ]kind|game[- ]chang\w+|"
    r"revolutioni[sz]\w+|seamless(?:ly)?|empower(?:s|ing)? (?:businesses|"
    r"enterprises|users|customers)|end[- ]to[- ]end solution|one[- ]stop|"
    r"in line with (?:our|its) vision|forward[- ]looking statements|"
    r"for more information,? (?:please )?(?:visit|contact)|media contact|"
    r"about (?:the )?company|signs? (?:an? )?mou|memorandum of understanding|"
    r"strategic partnership|wins? (?:the )?award|bags? (?:the )?award|"
    r"recogni[sz]ed as|felicitat\w+|conferred|how (?:investors|to trade)|"
    r"stocks? to (?:buy|watch)|should you buy|price target|"
    r"top \d+ (?:stocks|ways|tips|reasons)|everything (?:we|you) know|"
    r"\(ani\)|\(ians\)|\bani\b:|ians:)\b", re.I)
VAGUE_INTENT = re.compile(
    r"\b(?:aims? to|plans? to|set to explore|mulls?|eyes|to explore|"
    r"envisions?|vision for|roadmap for|calls? for|urges?)\b", re.I)
_HAS_NUMBER = re.compile(r"[$₹€£]\s?\d|\d[\d,.]*\s?(?:%|crore|lakh|bn|billion|"
                         r"million|mn|tn|trillion|gw|mw|km|tonnes?)", re.I)


def is_sponsored(entry) -> bool:
    url = entry.get("link") or entry.get("url") or ""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    return bool(SPONSORED_URL.search(url) or SPONSORED_TEXT.search(text))


def pr_cues(entry, body: str = "") -> list:
    """Explainable reasons a candidate reads like PR, strongest first.
    `body` is the enrichment extract when available (post-shortlist)."""
    cues = []
    src = (entry.get("source") or "").strip().lower()
    url = (entry.get("link") or entry.get("url") or "").lower()
    title = entry.get("title", "")
    text = f"{title} {entry.get('summary', '')} {body[:2500]}"
    for key in PR_SOURCES:
        if src == key or src.startswith(key + " ") or f" {key} " in f" {src} " \
                or (("." in key or "/" in key) and key in url):
            cues.append(f"PR/stock-tip/listing source ({entry.get('source')})")
            break
    if PR_URL.search(url):
        cues.append("company newsroom / vendor blog URL")
    if COMPANY_SOURCE.match(src):
        cues.append(f"the company's own channel ({entry.get('source')})")
    hits = sorted({m.group(0).lower() for m in PR_PHRASES.finditer(text)})
    if hits:
        cues.append("press-release phrasing: " + ", ".join(f"'{h}'" for h in hits[:3]))
    if VAGUE_INTENT.search(title) and not _HAS_NUMBER.search(title):
        cues.append("intent, not an event (aims/plans/MoU, no number)")
    return cues


def pr_penalty(cues: list) -> float:
    if not cues:
        return 0.0
    p = 0.0
    for c in cues:
        if c.startswith(("PR/stock-tip", "company newsroom", "the company's own")):
            p -= 2.5
        elif c.startswith("press-release phrasing"):
            p -= 1.0 + 0.5 * min(2, c.count(","))
        else:
            p -= 1.0
    return max(p, -6.0)


# --------------------------------------------------------------------------
# 4. corroboration: distinct outlets carrying the same story
# --------------------------------------------------------------------------
LOW_CREDIT_SOURCES = ("news on air", "newsonair", "pib", "ani", "ians",
                      "google news", "yahoo", "msn", "dailyhunt", "newsbreak")


def outlet_key(entry) -> str:
    s = (entry.get("source") or "").lower()
    s = re.sub(r"^(?:ai|world|business|technology|environment|india|tech|"
               r"science)\s*[-|(]?.*?\|\s*", "", s)          # "World news | The Guardian"
    for brand in ("guardian", "bloomberg", "reuters", "scmp", "south china morning post",
                  "techcrunch", "mit technology review", "the hindu", "times of india",
                  "economic times", "indian express", "hindustan times", "ndtv",
                  "business standard", "livemint", "moneycontrol", "inc42", "yourstory",
                  "bbc", "cnbc", "financial times", "wsj", "wall street journal",
                  "new york times", "economist", "nature", "new scientist"):
        if brand in s:
            return brand
    return s.strip()


def corroboration(all_entries: list, threshold: float = 0.5) -> None:
    """Set e['_outlets'] = number of DISTINCT outlets carrying the same story
    (same-event headline similarity >= threshold), across every section.
    Wire/press-release relays count at most once in total. O(n^2) over ~900
    short token sets: ~1s on Actions."""
    toks = [sig_tokens(e.get("title", "")) for e in all_entries]
    # inverted index on tokens keeps it fast
    index = defaultdict(list)
    for i, t in enumerate(toks):
        for w in t:
            index[w].append(i)
    for i, e in enumerate(all_entries):
        cand = Counter()
        for w in toks[i]:
            for j in index[w]:
                if j != i:
                    cand[j] += 1
        outlets = {outlet_key(e)}
        low = 0
        for j, n in cand.items():
            if n < 2:
                continue
            if same_story(toks[i], toks[j]) >= threshold:
                k = outlet_key(all_entries[j])
                if any(x in k for x in LOW_CREDIT_SOURCES):
                    low = 1
                    continue
                outlets.add(k)
        e["_outlets"] = len(outlets) + low


# --------------------------------------------------------------------------
# 5. recent memory (the paper's own last 7 days)
# --------------------------------------------------------------------------
class RecentMemory:
    """What the paper already ran. Exact URL -> hard drop (a true repeat).
    Same-story-different-URL -> kept but DEMOTED and annotated with what ran,
    so the selector can judge 'genuinely new development' vs 'rerun'."""

    def __init__(self):
        self.urls = set()
        self.items = []          # (date, headline, token set)

    def add(self, date: str, headline: str, url: str | None, source_title: str = ""):
        if url:
            self.urls.add(url)
        head = clean_title(headline)
        toks = sig_tokens(head) | sig_tokens(source_title)
        if toks:
            self.items.append((date, head, sig_tokens(head), sig_tokens(source_title)))

    def match(self, title: str, threshold: float = 0.5):
        """(date, headline) of the closest prior story, or None."""
        t = sig_tokens(title)
        if len(t) < 3:
            return None
        best, best_s = None, 0.0
        for date, head, htoks, stoks in self.items:
            s = max(same_story(t, htoks), same_story(t, stoks))
            if s > best_s:
                best, best_s = (date, head), s
        return best if best_s >= threshold else None

    def __len__(self):
        return len(self.items)


def edition_items(edition: dict):
    """(headline, url) for every published item in an edition JSON."""
    out = []
    if isinstance(edition.get("lead"), dict):
        out.append(edition["lead"])
    out += [x for x in edition.get("frontpage") or [] if isinstance(x, dict)]
    for sec in edition.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for st in sec.get("stories") or []:
            if isinstance(st, dict):
                out.append(st)
                out += [a for a in st.get("also") or [] if isinstance(a, dict)]
        out += [a for a in sec.get("also") or [] if isinstance(a, dict)]
    for x in edition.get("opportunities") or []:
        if isinstance(x, dict):
            out.append(x)
    return [((x.get("headline") or x.get("name") or x.get("line") or ""), x.get("url"))
            for x in out]


# --------------------------------------------------------------------------
# 6. opportunities: rank by when/where/would-he-go, not by post date
# --------------------------------------------------------------------------
OPP_TOPIC = word_re([
    r"ai", r"artificial intelligence", r"genai", r"llms?", r"machine learning",
    r"ml", r"data", r"startups?", r"founders?", r"entrepreneur\w*", r"venture",
    r"product", r"strategy", r"policy", r"public policy", r"tech policy",
    r"governance", r"fellowships?", r"hackathons?", r"buildathon", r"climate",
    r"sustainability", r"energy", r"deep ?tech", r"space", r"semiconductors?",
    r"robotics", r"drones?", r"quantum", r"fintech", r"health ?tech", r"design",
    r"leadership", r"innovation", r"summit", r"conference", r"accelerator",
    r"incubat\w+", r"cohort", r"residency", r"mba", r"scholarship",
    r"venture capital", r"vc", r"investors?", r"demo day", r"pitch", r"admissions?",
    r"info session", r"volunteer\w*", r"ngo", r"environment\w*", r"screening",
    r"agri\w*", r"meetup", r"builders?", r"agents?"])
OPP_LOCAL = word_re([r"jaipur", r"rajasthan"])
OPP_NCR = word_re([r"delhi", r"new delhi", r"gurugram", r"gurgaon", r"noida", r"ncr"])
OPP_INDIA = word_re([r"india", r"indian", r"bengaluru", r"bangalore", r"mumbai",
                     r"hyderabad", r"pune", r"chennai", r"kolkata", r"ahmedabad",
                     r"iit\w*", r"iim\w*", r"isb"])
OPP_REMOTE = word_re([r"online", r"virtual", r"remote", r"global", r"international",
                      r"worldwide", r"open to all"])
OPP_BAD = word_re([
    r"kids?", r"children", r"teens?", r"school students?", r"class \d+",
    r"ngos?", r"csos?", r"civil society organi[sz]ations", r"grants? for organi[sz]ations",
    r"africa\w*", r"nigeria\w*", r"kenya\w*", r"ghana\w*", r"(?:united states|us) only",
    r"theat(?:er|re) festival", r"food festival", r"fall festival",
    r"ride presale", r"early bird drawing", r"kids festival", r"football club",
    r"latino", r"perth", r"tashkent", r"chisinau", r"gainesville", r"houston"])
# Student-run college hackathons (most of Unstop) are not for a working
# professional — unless the host is a flagship institute or a real company.
COLLEGE_HOST = re.compile(r"hosted by [^.]*(?:universit|college|institute of "
                          r"engineering|school of|polytechnic|vidyapeeth)", re.I)
FLAGSHIP_HOST = word_re([r"iit\w*", r"iim\w*", r"isb", r"iisc", r"ashoka", r"bits"])
_ISO = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def _as_date(v):
    if not v or str(v) in ("None", "null"):
        return None
    m = _ISO.search(str(v))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def score_opportunity(entry, today: datetime.date, text_date=None) -> float:
    """Higher = more worth this reader's Saturday. -inf = drop (past or
    obviously not for him). `text_date` = a date parsed from the title/summary
    when no structured one exists."""
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    when = _as_date(entry.get("deadline")) or _as_date(entry.get("event_date")) or text_date
    s = 0.0
    if when:
        days = (when - today).days
        if days < 0:
            return float("-inf")
        if days <= 2:
            s += 2.0          # tomorrow is still actionable, barely
        elif days <= 45:
            s += 6.0
        elif days <= 120:
            s += 3.5
        else:
            s += 1.0
    else:
        s -= 2.0              # the bar says a concrete date is non-negotiable
    if OPP_LOCAL.search(text):
        s += 5.0
    elif OPP_NCR.search(text):
        s += 3.5
    elif OPP_INDIA.search(text):
        s += 2.5
    elif OPP_REMOTE.search(text):
        s += 1.0
    topics = {m.group(0).lower() for m in OPP_TOPIC.finditer(text)}
    s += min(len(topics), 4) * 1.2
    if OPP_BAD.search(text):
        s -= 6.0
    if COLLEGE_HOST.search(text) and not FLAGSHIP_HOST.search(text):
        s -= 3.0
    return s


# --------------------------------------------------------------------------
# 7. source yield: what the editor actually picks, once shortlisted
# --------------------------------------------------------------------------
class SourceYield:
    """Rate-based prior from the last N days of (shortlisted, selected)
    counts per source. log-odds vs the overall rate, smoothed toward neutral,
    clipped. A source with few recent shortlist appearances sits near 0."""

    PRIOR_WEIGHT = 8.0       # pseudo-observations at the base rate
    SCALE = 1.4
    CLIP = (-2.5, 1.5)
    MIN_ROWS = 50            # below this much history the prior is off

    def __init__(self, shortlisted: Counter, selected: Counter):
        self.short = shortlisted
        self.sel = selected
        tot_short = sum(shortlisted.values())
        self.base = (sum(selected.values()) / tot_short) if tot_short else 0.0
        self.enabled = tot_short >= self.MIN_ROWS and 0.02 < self.base < 0.9

    @staticmethod
    def key(source: str) -> str:
        return (source or "").strip().lower()

    def bonus(self, source: str) -> float:
        if not self.enabled:
            return 0.0
        k = self.key(source)
        n, s = self.short.get(k, 0), self.sel.get(k, 0)
        rate = (s + self.PRIOR_WEIGHT * self.base) / (n + self.PRIOR_WEIGHT)
        b = self.SCALE * math.log(max(rate, 1e-3) / self.base)
        return max(self.CLIP[0], min(self.CLIP[1], b))

    @classmethod
    def load(cls, shortlist_log: pathlib.Path, selected_dir: pathlib.Path,
             refs_dir: pathlib.Path, today: datetime.date, days: int = 30):
        short, sel = Counter(), Counter()
        cutoff = today - datetime.timedelta(days=days)
        try:
            with open(shortlist_log, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        d = datetime.date.fromisoformat(row["date"])
                    except Exception:                            # noqa: BLE001
                        continue
                    if cutoff <= d < today:
                        short[cls.key(row.get("source"))] += int(row.get("n", 0))
        except FileNotFoundError:
            pass
        for f in sorted(pathlib.Path(selected_dir).glob("*.json")) if pathlib.Path(selected_dir).exists() else []:
            try:
                d = datetime.date.fromisoformat(f.stem)
            except ValueError:
                continue
            if not (cutoff <= d < today):
                continue
            try:
                data = json.loads(f.read_text())
            except Exception:                                    # noqa: BLE001
                continue
            for st in (data.get("stories") or {}).values():
                sel[cls.key(st.get("source"))] += 1
        # sources in feeds/selected carry the refs `source` string, which is the
        # same string the shortlist log records — keys line up by construction.
        return cls(short, sel)


# --------------------------------------------------------------------------
# 8. format flags: roundups, digests, opinion — not news events
# --------------------------------------------------------------------------
ROUNDUP_RX = re.compile(
    r"\b(?:weekly|this week(?:'s)?|week'?s? (?:top|biggest)|round-?up|wrap-?up|"
    r"tracker|the download|morning digest|evening digest|daily digest|newsletter|"
    r"recap|everything (?:we|you) (?:know|need to know)|top \d+|\d+ (?:startups|"
    r"stocks|things|companies|ways|charts)|caught our eye|charting the|"
    r"week ahead|funding (?:report|roundup))\b", re.I)
OPINION_RX = re.compile(r"(?:\bopinion\b|\beditorial\b|\bop-ed\b|\bcolumn\b|"
                        r"\bview(?:point)?:|\bguest post\b|\| view\b|"
                        r"^the hindu editorial|\bon the .{3,40} culture\b)", re.I)
OPINION_URL = re.compile(r"/(?:opinion|opinions|editorial|editorials|columns?|"
                         r"blogs?|op-ed|commentary|views)/", re.I)
FILING_RX = re.compile(r"\b(?:allots?|allotment of|esos|esop allotment|record date|"
                       r"board meeting|intimation|outcome of board|trading window|"
                       r"postal ballot|agm notice|joins .{3,40} (?:to lead|as (?:head|chief|vp|"
                       r"director))|appoints? .{3,40} as)\b", re.I)
AI_SUMMARY_RX = re.compile(r"summari[sz]ed by ai|ai-generated summary|"
                           r"may make mistakes", re.I)
JUNK_TEXT_RX = re.compile(
    r"please wait while your request is being verified|verify you are human|"
    r"checking your browser|enable javascript|are you a robot|access denied|"
    r"requires? a paid subscription|subscribe to (?:continue|read)|"
    r"to continue reading|this content is for subscribers|403 forbidden|"
    r"page not found|we use cookies to|off the wire press releases", re.I)


def format_flags(entry) -> list:
    title = entry.get("title", "")
    url = entry.get("link") or entry.get("url") or ""
    text = f"{title} {entry.get('summary', '')}"
    out = []
    if ROUNDUP_RX.search(title):
        out.append("roundup/digest format, not a single news event")
    if OPINION_RX.search(title) or OPINION_URL.search(url):
        out.append("opinion/editorial, not reporting")
    if FILING_RX.search(title):
        out.append("routine corporate filing / people move")
    if AI_SUMMARY_RX.search(text):
        out.append("machine-summarised copy")
    return out


def format_penalty(flags: list) -> float:
    p = 0.0
    for f in flags:
        p -= 2.5 if f.startswith("roundup") else 1.5 if f.startswith("opinion") else 3.0
    return p


# --------------------------------------------------------------------------
# 9. Beyond-Your-Beat rerouting: big India business/careers stories that the
#    generic top-stories feed catches belong in a real section.
# --------------------------------------------------------------------------
MARKETS_RX = word_re([r"ipo", r"sensex", r"nifty", r"sebi", r"rbi", r"shares?",
                      r"stocks?", r"market cap", r"listing", r"results",
                      r"profit", r"revenue", r"merger", r"acquisition", r"acquires",
                      r"stake", r"conglomerate", r"banks?", r"rupee", r"inflation",
                      r"gdp", r"fiscal", r"tax", r"gst", r"tariffs?", r"exports?"])
CAREERS_RX = word_re([r"h-1b", r"visas?", r"layoffs?", r"lays off", r"hiring",
                      r"jobs", r"employees", r"workforce", r"salar(?:y|ies)",
                      r"gcc", r"campus placements?"])


def reroute_slug(entry) -> str | None:
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    if CAREERS_RX.search(text) and (INDIA_CUE.search(text)):
        return "work-careers"
    if MARKETS_RX.search(text) and INDIA_CUE.search(text):
        return "global-economics"
    return None
