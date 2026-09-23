#!/usr/bin/env python3
"""Plain-python (no pytest) unit tests for the v2 pipeline changes.

Run directly:  python3 tests/test_pipeline.py
Exits non-zero (and prints FAIL lines) on any failure, so it's CI-usable
without adding a pytest dependency.
"""
import datetime
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_digest as bd  # noqa: E402
import assemble_edition as ae  # noqa: E402

FAILURES = []


def check(name, cond):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILURES.append(name)


# --- A1: hash-id stability -------------------------------------------------
def test_hash_id_stable():
    url = "https://example.com/some-article-slug"
    id1 = bd.make_id("ai", url)
    id2 = bd.make_id("ai", url)
    check("hash id is deterministic for the same URL", id1 == id2)
    check("hash id carries the section slug prefix", id1.startswith("ai-"))
    id3 = bd.make_id("ai", url + "?utm_source=x")
    check("hash id differs for a different URL", id1 != id3)


# --- A5: date hygiene -------------------------------------------------------
def test_max_age_hard_cutoff():
    now = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    stale = {"title": "Old story", "summary": "", "published":
             (now - datetime.timedelta(hours=200)).isoformat()}
    fresh = {"title": "Fresh story", "summary": "", "published":
             (now - datetime.timedelta(hours=2)).isoformat()}
    check("a story older than max_age_hours scores -inf (hard cut)",
          bd.score(stale, now, max_age_hours=96) == float("-inf"))
    check("a fresh story within max_age_hours scores a real number",
          bd.score(fresh, now, max_age_hours=96) > 0)


def test_undated_penalized_not_dropped():
    now = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    undated = {"title": "No date story", "summary": ""}
    fresh = {"title": "Fresh story", "summary": "", "published": now.isoformat()}
    s_undated = bd.score(undated, now, max_age_hours=96)
    s_fresh = bd.score(fresh, now, max_age_hours=96)
    check("undated entries are NOT hard-dropped by max_age (no crash / -inf)",
          s_undated != float("-inf"))
    check("undated entries score lower than an equally-fresh dated one",
          s_undated < s_fresh)


def test_event_date_extraction_drops_past_events():
    ref_year = 2026
    now = datetime.date(2026, 7, 14)
    past = bd.extract_event_date("Join us on 12 February for the big summit", ref_year)
    future = bd.extract_event_date("Join us on 20 August for the big summit", ref_year)
    check("extract_event_date parses a past date", past == datetime.date(2026, 2, 12))
    check("extract_event_date parses a future date", future == datetime.date(2026, 8, 20))
    check("past event date is before 'now' (would be hard-dropped in shortlist)",
          past < now)
    check("future event date is not before 'now'", not (future < now))


def test_event_date_extraction_month_first_format():
    d = bd.extract_event_date("The conference runs July 20, 2026 in Delhi", 2026)
    check("extract_event_date parses 'Month D, YYYY' format", d == datetime.date(2026, 7, 20))


def test_event_date_no_match_returns_none():
    d = bd.extract_event_date("A story with no date mentioned at all", 2026)
    check("extract_event_date returns None when nothing matches", d is None)


# --- A4: cross-day dedup building blocks ------------------------------------
def test_title_key_dedup_matching():
    a = bd.title_key("OpenAI announces GPT-6 with major upgrades to reasoning")
    b = bd.title_key("OpenAI announces GPT-6 with major upgrades to reasoning capability today")
    check("title_key matches on shared first-8-word prefix", a == b)


# --- B2: buzz + source weight sanity ----------------------------------------
def test_buzz_bonus_capped():
    now = datetime.datetime(2026, 7, 14, tzinfo=datetime.timezone.utc)
    base = {"title": "x", "summary": "", "published": now.isoformat(), "_buzz": 1}
    buzzy = {"title": "x", "summary": "", "published": now.isoformat(), "_buzz": 20}
    check("higher buzz scores at least as high as no buzz",
          bd.score(buzzy, now) >= bd.score(base, now))
    # v7: capped at min(outlets-1, 4) * 0.9 == 3.6 max bonus
    check("buzz bonus is capped (huge buzz != unbounded score)",
          bd.score(buzzy, now) - bd.score(base, now) <= 3.61)


# --- P1: cross-day dedup via published editions -----------------------------
def test_load_published_urls_graceful_on_network_failure():
    import datetime as _dt
    old_url = bd.EDITIONS_BASE_URL
    bd.EDITIONS_BASE_URL = "https://this-domain-should-not-resolve.invalid/editions"
    try:
        old_dir = bd.EDITIONS_DIR
        bd.EDITIONS_DIR = ""
        urls, keys, memory, ok = bd.load_published_urls(_dt.datetime.now(_dt.timezone.utc))
        bd.EDITIONS_DIR = old_dir
        check("load_published_urls degrades to empty sets on network failure "
              "(never raises)", urls == set() and keys == set() and len(memory) == 0)
        check("...and reports the failure (-> DEDUP_BROKEN health issue)", ok is False)
    finally:
        bd.EDITIONS_BASE_URL = old_url


# --- P2(e): same-event clustering --------------------------------------------
def test_event_signature_clusters_same_event():
    a = bd.event_signature("India-AI Impact Summit Kicks Off in New Delhi")
    b = bd.event_signature("PM Modi Inaugurates India-AI Impact Summit With 20 World Leaders")
    c = bd.event_signature("Day 2 of India-AI Impact Summit Focuses on AI Safety")
    check("differently-worded headlines about the same event share a signature",
          a == b == c and a != "")


def test_event_signature_distinct_for_unrelated_stories():
    sig1 = bd.event_signature("India-AI Impact Summit Kicks Off in New Delhi")
    sig2 = bd.event_signature("Sarvam AI Becomes Newest Unicorn With $234M Round")
    check("unrelated stories get a distinct (or empty) signature", sig1 != sig2)


def test_event_cap_caps_flooding_without_collapsing_unrelated():
    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    raw = [
        {"title": "India-AI Impact Summit Kicks Off in New Delhi", "link": "https://a.com/1", "source": "A", "summary": "", "published": now.isoformat()},
        {"title": "PM Modi Inaugurates India-AI Impact Summit With 20 World Leaders", "link": "https://a.com/2", "source": "B", "summary": "", "published": now.isoformat()},
        {"title": "Day 2 of India-AI Impact Summit Focuses on AI Safety", "link": "https://a.com/3", "source": "C", "summary": "", "published": now.isoformat()},
        {"title": "India-AI Impact Summit Closes With AI Commons Proposal", "link": "https://a.com/4", "source": "D", "summary": "", "published": now.isoformat()},
        {"title": "OpenAI Raises $40B At $300B Valuation", "link": "https://b.com/1", "source": "E", "summary": "", "published": now.isoformat()},
        {"title": "Sarvam AI Becomes Newest Unicorn With $234M Round", "link": "https://b.com/2", "source": "F", "summary": "", "published": now.isoformat()},
    ]
    section = {"slug": "ai", "name": "AI", "max_stories": 10, "window_hours": 96, "entries": raw}
    lean, refs, dropped, cross_day, feed_stats = bd.shortlist_section(section, now, set(), set(), 2026)
    kept_titles = {s["title"] for s in lean}
    check("4 same-event stories capped to EVENT_CAP_PER_SECTION",
          sum(1 for t in kept_titles if "India-AI Impact Summit" in t) == bd.EVENT_CAP_PER_SECTION)
    check("unrelated stories in the same batch are untouched",
          "OpenAI Raises $40B At $300B Valuation" in kept_titles and
          "Sarvam AI Becomes Newest Unicorn With $234M Round" in kept_titles)


# --- P2(c): article-date trust over feed date --------------------------------
def test_stale_article_date_drops_story():
    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    refs_today = {"india-x": {"url": "https://example.com/x", "published": now.isoformat(),
                               "source": "example.com", "title": "Old event, fresh feed date"}}
    digest_sections = [{"slug": "india", "stories": [
        {"id": "india-x", "title": "Old event, fresh feed date", "article_date": "2026-02-14"}
    ]}]
    dropped = bd.apply_article_date_corrections(digest_sections, refs_today, {"india": 96}, now)
    check("a story whose article_date is >7d older than the feed date, and now "
          "past the section's max-age cutoff, is dropped",
          dropped == 1 and digest_sections == [])
    check("the dropped story's ref is also removed (no dangling link)",
          "india-x" not in refs_today)


def test_fresh_article_date_agreement_keeps_story():
    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    refs_today = {"india-y": {"url": "https://example.com/y", "published": now.isoformat(),
                               "source": "example.com", "title": "Fresh story"}}
    digest_sections = [{"slug": "india", "stories": [
        {"id": "india-y", "title": "Fresh story", "article_date": now.date().isoformat()}
    ]}]
    dropped = bd.apply_article_date_corrections(digest_sections, refs_today, {"india": 96}, now)
    check("a story whose article_date agrees with the feed date is kept",
          dropped == 0 and len(digest_sections[0]["stories"]) == 1)
    check("article_date is stripped from the surviving story (not part of the "
          "lean digest contract the model reads)",
          "article_date" not in digest_sections[0]["stories"][0])


# ---------------------------------------------------------------- v6 -------

def test_pr_penalty_hits_wires_and_vendor_blogs():
    for src in ("PIB", "PR Newswire", "Business Wire", "LinkedIn"):
        check(f"{src} is demoted as a press-release carrier",
              bd.pr_penalty({"source": src, "url": "https://x.example/a"}) < 0)
    check("a vendor newsroom URL is demoted even when the source looks normal",
          bd.pr_penalty({"source": "Acme", "url": "https://acme.com/newsroom/launch"}) < 0)


def test_pr_penalty_leaves_real_outlets_alone():
    for src in ("Reuters", "The Hindu", "Bloomberg.com", "Tech - South China Morning Post"):
        check(f"{src} is NOT demoted",
              bd.pr_penalty({"source": src, "url": "https://x.example/a"}) == 0.0)
    check("'pib' inside a longer word does not trigger the wire match",
          bd.pr_penalty({"source": "Pibworth Media", "url": "https://x.example/a"}) == 0.0)


def test_assemble_never_silently_drops_a_rewritten_headline():
    """The 2026-07-22 regression: SCMP's clickbait title shares no significant
    token with the editor's plain-language rewrite, and the whole lead vanished."""
    refs = {"india-deep-tech-f0c122f524": {
        "url": "https://scmp.com/x", "source": "SCMP",
        "title": "SpaceX took 4 attempts. India's space start-up needed just 1"}}
    item = {"id": "india-deep-tech-f0c122f524",
            "headline": "Skyroot's Vikram-1 puts India in the private orbital-launch club",
            "summary": "Skyroot's Vikram-1 reached orbit on its first attempt."}
    warnings = []
    story = ae.build_story(item, refs, warnings)
    check("a correctly-rewritten headline is kept, not dropped", story is not None)
    check("the mismatch is still surfaced as a warning for a human to check",
          any("CHECK THE LINK" in w for w in warnings))


def test_assemble_carries_section_level_also_rail():
    refs = {"ai-1": {"url": "https://a.example/1", "source": "A", "title": "One"},
            "ai-2": {"url": "https://a.example/2", "source": "B", "title": "Two"}}
    rail = ae.build_also_rail(
        [{"id": "ai-1", "line": "First thing"},
         {"id": "ai-2", "line": "Second thing"},
         {"id": "ai-missing", "line": "Unknown id"},
         {"id": "ai-1", "line": ""}], refs, [])
    check("known also ids resolve to rail entries with urls",
          len(rail) == 2 and rail[0]["url"] == "https://a.example/1")
    check("an unknown also id is dropped rather than published linkless",
          all(r["line"] != "Unknown id" for r in rail))
    check("an also entry with no line is dropped", all(r["line"] for r in rail))


def test_assemble_passes_through_bullet_summaries():
    refs = {"ai-1": {"url": "https://a.example/1", "source": "A", "title": "Chips rise"}}
    story = ae.build_story({"id": "ai-1", "headline": "Chips rise",
                            "hook": "Chip profits rose ==2,580%==.",
                            "points": ["First point.", "Second point.", " "]}, refs, [])
    check("hook survives assembly", story["hook"].startswith("Chip profits"))
    check("points survive assembly and blanks are stripped", story["points"] ==
          ["First point.", "Second point."])
    legacy = ae.build_story({"id": "ai-1", "headline": "Chips rise",
                             "summary": "One paragraph."}, refs, [])
    check("a pre-v6 summary-only draft still assembles with no points key",
          "points" not in legacy and legacy["summary"] == "One paragraph.")


# ------------------------------------------------- v6.1 stale-prompt floor ---

def test_summary_splits_into_hook_and_points():
    s = ("Skyroot's Vikram-1 reached orbit on its first attempt. "
         "The rocket carried 350kg to a 450km orbit. "
         "It makes India the third nation with a private orbital launch. "
         "The company has raised $95M to date.")
    hook, pts = ae.split_summary(s)
    check("the first sentence becomes the hook", hook.startswith("Skyroot"))
    check("the rest become 3 bullets", len(pts) == 3)
    check("no bullet repeats the hook", all(p != hook for p in pts))


def test_summary_split_does_not_break_on_decimals():
    hook, pts = ae.split_summary(
        "Funding hit $1.5B this quarter. Rs. 400 crore came from SIDBI. "
        "The round closed in June.")
    check("a decimal like $1.5B never splits a sentence",
          hook == "Funding hit $1.5B this quarter." and len(pts) == 2)


def test_short_or_flagged_summaries_are_left_alone():
    hook, pts = ae.split_summary("One short sentence only.")
    check("a one-sentence summary is not bulleted", hook is None and pts == [])
    hook, pts = ae.split_summary(
        "Nvidia is reportedly investing in SSI. It may be large. "
        "It is unclear. (source unreachable — headline only)")
    check("a source-unreachable summary is never bulleted",
          hook is None and pts == [])


def test_highlight_marks_the_claim_not_a_bare_number():
    check("a milestone phrase is the claim",
          ae.highlight_phrase("Modi meets founders after India's first private orbital launch")
          == "India's first private orbital launch")
    check("a number welded to its noun qualifies",
          ae.highlight_phrase("India curtailed 8,133 GWh of solar power in Q1")
          == "8,133 GWh of solar power")
    check("a bare number is NOT highlighted",
          ae.highlight_phrase("Pakistan accused of killing 30 protesters") is None)
    check("a lone year is NOT highlighted",
          ae.highlight_phrase("The plan was set out in 2025") is None)
    check("a lone percentage with no noun is NOT highlighted",
          ae.highlight_phrase("Shares fell 8.9% on the news") is None)
    check("a source-unreachable line is never highlighted",
          ae.highlight_phrase("Something happened (source unreachable — headline only)") is None)


def test_underline_is_a_quantified_consequence():
    check("a quantified forward-looking clause is underlined",
          ae.underline_phrase(["The plant will add 26.3 GW of demand by 2030."])
          == "will add 26.3 GW of demand by 2030")
    check("a vague modal clause with no number is skipped",
          ae.underline_phrase(["The market now stands lower."]) is None)
    check("no bullets means no underline",
          ae.underline_phrase([]) is None)


def test_emphasis_never_marks_the_same_fact_twice():
    story = {"headline": "Apple hits $5tn as sell-off deepens",
             "hook": "Apple briefly touched a $5tn market cap on Tuesday.",
             "points": ["It could rise another 12% analysts said."]}
    ae.apply_emphasis(story)
    hl_count = story["headline"].count("==") + story["hook"].count("==")
    check("exactly one field carries the yellow highlight (2 markers, one pair)",
          hl_count == 2)
    check("the highlight landed in the headline, not the hook",
          "==" in story["headline"] and "==" not in story["hook"])
    check("the consequence bullet is underlined", "__" in story["points"][0])


def test_old_format_draft_still_yields_bullets_and_marks():
    """The whole point: a stale routine prompt must not cost the reader bullets."""
    refs = {"ai-1": {"url": "https://a.example/1", "source": "A", "title": "Chips rise"}}
    story = ae.build_story({"id": "ai-1", "headline": "Chip firm raises $75M Series B",
                            "summary": "The firm raised $75M in a Series B round. "
                                       "It plans to hire 200 engineers by 2027. "
                                       "Exports drove most of the gain."}, refs, [])
    check("bullets are derived from an old-format summary", len(story["points"]) == 2)
    check("the paragraph is cleared so it is not shown twice", story["summary"] == "")
    check("the headline carries a claim highlight around the raise",
          "==" in story["headline"] and "$75M" in story["headline"]
          and story["headline"].index("==") < story["headline"].index("$75M"))


# --- v7: editorial layer + copy desk ----------------------------------------
def test_v7_editorial():
    import editorial as edl
    check("abbreviation-safe split keeps 'CM N. Chandrababu' together",
          ae.split_summary("Andhra Pradesh CM N. Chandrababu Naidu approved it. "
                           "It cost Rs 5 crore. More next year.")[0].endswith("approved it."))
    check("foreign startup in India Deep Tech is penalised",
          edl.section_fit("india-deep-tech", {"title": "Zipline raises $1B for drones",
                                              "source": "Bloomberg"}) < 0)
    check("Indian startup in India Deep Tech is not penalised",
          edl.section_fit("india-deep-tech", {"title": "Skyroot's Vikram-1 reaches orbit",
                                              "source": "Bloomberg"}) == 0)
    check("sponsored URL is detected", edl.is_sponsored(
        {"title": "x", "link": "https://economictimes.com/brand-connect/some-story"}))
    check("'ai' no longer matches inside 'said'", edl.interest_score("He said again") == 0)
    today = datetime.date(2026, 9, 23)
    check("past opportunity is dropped", edl.score_opportunity(
        {"title": "AI hackathon Delhi", "deadline": "2026-09-01"}, today) == float("-inf"))
    check("Jaipur AI fellowship outranks a foreign film festival",
          edl.score_opportunity({"title": "AI policy fellowship Jaipur", "deadline": "2026-10-10"}, today)
          > edl.score_opportunity({"title": "Latino film festival Houston", "event_date": "2026-10-10"}, today))
    check("filler-only signal bullet is removed",
          ae.clean_signal(["Worth watching.", "RBI's move lowers loan rates for MSMEs by 50bp."])
          == ["RBI's move lowers loan rates for MSMEs by 50bp."])
    mem = edl.RecentMemory()
    mem.add("2026-09-19", "US sanctions law threatens 100% tariffs on India over Russian oil", None)
    check("same story via a new headline is remembered",
          mem.match("Trump signs sanctions law opening door to 100% tariffs on India over Russian oil") is not None)


def test_v8_newsroom():
    import newsroom as nr
    digest = {"date": "2026-09-23", "sections": [
        {"slug": "ai", "stories": [{"id": f"ai-{i}", "title": f"t{i}"} for i in range(30)]},
        {"slug": "opportunities", "stories": [{"id": "opportunities-1", "title": "x", "when": "on 2026-10-01"}]}]}
    raw = {"lead": "ai-0", "frontpage": [f"ai-{i}" for i in range(1, 12)],
           "sections": [{"slug": "ai", "stories": [f"ai-{i}" for i in range(0, 30)], "also": ["ai-29", "nope"]}],
           "opportunities": ["opportunities-1", "ai-5"], "scores": {f"ai-{i}": [1, 1, 1, i % 5, ""] for i in range(30)}}
    sel = nr.validate_selection(raw, digest)
    full = 1 + len(sel["frontpage"]) + sum(len(x["stories"]) for x in sel["sections"])
    check("newsroom: <= 24 full cards enforced in code", full <= 24)
    check("newsroom: front page capped at 8", len(sel["frontpage"]) <= 8)
    check("newsroom: lead never repeated in its section",
          all("ai-0" not in x["stories"] for x in sel["sections"]))
    check("newsroom: unknown ids and non-opportunity 'opportunities' dropped",
          sel["opportunities"] == ["opportunities-1"] and "nope" not in sel["sections"][0]["also"])
    check("newsroom: structured opportunity date carried to the writer",
          sel["opp_when"].get("opportunities-1") == "on 2026-10-01")
    selected = {"stories": {"ai-0": {"text_source": "digest-extract"}, "ai-3": {"text_source": "full"}}}
    sel["lead_contenders"] = ["ai-0", "ai-3"]
    nr.promote_readable_lead(sel, selected)
    check("newsroom: unreadable lead is swapped for a readable contender", sel["lead"] == "ai-3")
    check("newsroom: text cleaning drops page furniture, keeps sentences",
          nr.clean_text("Advertisement\nMenu\nThe RBI cut rates by 25bp on Friday.") == "The RBI cut rates by 25bp on Friday.")
    wire = nr.wire_edition(sel, {"stories": {"ai-3": {"headline": "H", "fulltext": "One. Two. Three."}}})
    check("newsroom: backup wire edition is marked and never blank",
          wire["backup"] and wire["lead"]["summary"] == "One. Two.")


def test_v82_usage_guard():
    import newsroom as nr, tempfile, pathlib, json as _j
    tmp = pathlib.Path(tempfile.mkdtemp())
    old_log, old_alert = nr.LOG, nr.ALERT_FILE
    nr.LOG, nr.ALERT_FILE = tmp / "log.jsonl", tmp / "alert.txt"
    rows = [{"date": f"2026-09-{d:02d}", "input_tokens": 50000, "output_tokens": 10000} for d in range(10, 17)]
    rows.append({"date": "2026-09-17", "input_tokens": 250000, "output_tokens": 20000})
    nr.LOG.write_text("\n".join(_j.dumps(r) for r in rows))
    nr.usage_check("2026-09-17")
    check("usage guard: a 4x spike raises an alert", nr.ALERT_FILE.exists())
    nr.ALERT_FILE.unlink()
    nr.usage_check("2026-09-16")
    check("usage guard: a normal day raises nothing", not nr.ALERT_FILE.exists())
    try:
        nr.call_claude("editor", "x", "y" * 200_000, "m", None, "2026-09-17")
        refused = False
    except RuntimeError as ex:
        refused = "usage guard" in str(ex)
    check("usage guard: an oversized input is refused before sending", refused)
    nr.LOG, nr.ALERT_FILE = old_log, old_alert


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{len(FAILURES)} failing check(s)" if FAILURES else "\nAll checks passed.")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
