import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import fetch_markets as markets
import editorial
import fetch_opportunities as opportunities
import build_digest as digest
import assemble_edition as assembler


def _ts(day):
    return int(datetime.datetime.fromisoformat(day + "T04:00:00+00:00").timestamp())


def test_market_uses_prior_trading_close_when_today_candle_is_missing():
    chart = {"meta": {"regularMarketPrice": 23140.5,
                      "regularMarketTime": _ts("2026-09-25"),
                      "exchangeTimezoneName": "Asia/Kolkata",
                      "chartPreviousClose": 24000},
             "timestamp": [_ts("2026-09-23"), _ts("2026-09-24"), _ts("2026-09-25")],
             "indicators": {"quote": [{"close": [23200, 23063.1, None]}]}}
    q = markets.quote_from_chart(chart, datetime.datetime.fromisoformat("2026-09-25T05:00:00+00:00"))
    assert q["pct"] == round((23140.5 / 23063.1 - 1) * 100, 2)
    closed = markets.quote_from_chart(chart, datetime.datetime.fromisoformat("2026-09-26T00:00:00+00:00"))
    assert closed["closed"] is True and closed["pct"] is None


def test_market_live_candle_uses_prior_date_and_metadata_fallback():
    chart = {"meta": {"regularMarketPrice": 101.81,
                      "regularMarketTime": _ts("2026-10-05"),
                      "exchangeTimezoneName": "America/New_York",
                      "chartPreviousClose": 103.53},
             "timestamp": [_ts("2026-10-01"), _ts("2026-10-02"), _ts("2026-10-05")],
             "indicators": {"quote": [{"close": [102.31, 102.25, 101.81]}]}}
    q = markets.quote_from_chart(chart, datetime.datetime.fromisoformat("2026-10-05T05:00:00+00:00"))
    assert q["pct"] == round((101.81 / 102.25 - 1) * 100, 2)
    assert q["closed"] is False


def test_market_uses_yahoo_daily_change_when_series_has_a_gap():
    chart = {"meta": {"regularMarketPrice": 85489.03,
                      "regularMarketTime": _ts("2026-10-05"),
                      "exchangeTimezoneName": "UTC",
                      "regularMarketChangePercent": 0.70},
             "timestamp": [_ts("2026-10-03"), _ts("2026-10-04"), _ts("2026-10-05")],
             "indicators": {"quote": [{"close": [84763.58, None, 85489.03]}]}}
    q = markets.quote_from_chart(chart, datetime.datetime.fromisoformat("2026-10-05T05:00:00+00:00"))
    assert q["pct"] == 0.70


def test_opportunity_rejects_student_distant_and_soon_events():
    today = datetime.date(2026, 10, 5)
    base = {"title": "AI builder meetup", "summary": "In-person in Jaipur",
            "event_date": "2026-10-10"}
    assert editorial.opportunity_rejection(base, today) is None
    assert editorial.opportunity_rejection({**base, "summary": "Hosted by IIT Delhi"}, today) == "student/college"
    assert editorial.opportunity_rejection({**base, "summary": "In-person in Mumbai"}, today) == "outside Jaipur/Delhi-NCR"
    assert editorial.opportunity_rejection({**base, "event_date": "2026-10-06"}, today) == "too soon/past"


def test_student_eligibility_and_fellowship_name_are_distinct():
    today = datetime.date(2026, 10, 5)
    fellowship = {"title": "beVisioneers: The Mercedes-Benz Fellowship",
                  "summary": "The DO School helps global environmental projects.",
                  "deadline": "2027-01-31", "kind": "fellowship"}
    assert editorial.opportunity_rejection(fellowship, today) is None
    assert editorial.opportunity_when(fellowship) == "apply by Sun 31 Jan"
    assert editorial.opportunity_rejection({**fellowship, "students_only": True}, today) == "student/college"
    assert editorial.opportunity_rejection({"title": "JAI 2026 Hackathon", "summary": "Hosted by JIIT Noida",
                                             "deadline": "2026-10-10"}, today) == "student/college"
    assert editorial.opportunity_when({"event_date": "2026-11-02", "event_end_date": "2026-11-04"}) == "2-4 Nov"
    assert opportunities._when_iso("register by 11 October 2026", 2026) == "2026-10-11"
    assert opportunities._when_iso("Submissions open October 5; due October 27", 2026) == "2026-10-27"


def test_dated_public_source_parsers(monkeypatch):
    rss = '<rss><channel><item><title>Climate Fellowship</title><link>https://example.org/fellow</link><description>Deadline: January 31, 2027. Global environmental projects.</description></item></channel></rss>'
    monkeypatch.setattr(opportunities, "_get", lambda url, **kw: rss)
    fellows = opportunities.fetch_opportunity_desk()
    assert len(fellows) == 1 and fellows[0]["deadline"] == "2027-01-31"
    event = {"@type": "Event", "name": "Jaipur Climate Summit", "url": "https://meetup.com/e/1",
             "startDate": "2026-10-26T09:00:00Z", "location": {"address": {"addressLocality": "Jaipur"}}}
    page = '<script type="application/ld+json">' + json.dumps([event]) + '</script>'
    monkeypatch.setattr(opportunities, "_get", lambda url, **kw: page)
    jaipur = opportunities.fetch_meetup_jaipur()
    assert len(jaipur) == 1 and jaipur[0]["event_date"] == "2026-10-26"
    booth_html = '<tr><th>Zoom in on Booth (Virtual)</th><td></td><td>Zoom</td><td>October 14, 2026</td><td><a href="https://example.org/register">Register</a></td></tr>'
    monkeypatch.setattr(opportunities, "_get", lambda url, **kw: booth_html)
    booth = opportunities.fetch_booth()
    assert len(booth) == 1 and booth[0]["kind"] == "mba"


def test_opportunity_kind_and_when_reach_published_card():
    entry = {"title": "Jaipur Climate Summit", "summary": "In-person in Jaipur",
             "link": "https://example.org/summit", "source": "Meetup", "event_date": "2026-10-26"}
    section = {"slug": "opportunities", "name": "Opportunities", "max_stories": 4,
               "entries": [entry]}
    now = datetime.datetime.fromisoformat("2026-10-05T05:00:00+00:00")
    lean, refs, *_ = digest.shortlist_section(section, now, set(), set(), 2026)
    assert lean[0]["kind"] == "climate" and lean[0]["when"] == "Mon 26 Oct"
    card = assembler.build_opp({"id": lean[0]["id"], "name": "Jaipur Climate Summit",
                                "when": "October 26, 2026"}, refs, [])
    assert card["kind"] == "climate" and card["when"] == "Mon 26 Oct"


def test_recent_published_opportunities_use_only_published_papers(tmp_path, monkeypatch):
    edition = {"opportunities": [{"name": "Mumbai Meets AI 06", "when": "October 10, 2026",
                                  "url": "https://lu.ma/mumbai-ai"}]}
    (tmp_path / "2026-10-03.json").write_text(json.dumps(edition))
    monkeypatch.setenv("EDITIONS_DIR", str(tmp_path))
    urls, titles = opportunities.recent_published(datetime.date(2026, 10, 5))
    assert "https://lu.ma/mumbai-ai" in urls
    assert ("mumbaimeetsai06", "2026-10-10") in titles


def test_luma_fetch_does_not_shadow_editorial_module(monkeypatch):
    page = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"initialData": {"data": {"events": [
        {"event": {"name": "AI builders meetup", "url": "x1", "start_at": "2026-10-20T12:00:00Z"}}]}}}}}) + "</script>"
    monkeypatch.setattr(opportunities, "_get", lambda *a, **k: page)
    items = opportunities.fetch_luma()
    assert items and items[0]["event_date"] == "2026-10-20"


def test_physical_event_outside_region_rejected_even_without_date():
    today = datetime.date(2026, 10, 5)
    e = {"title": "SomeHack", "summary": "In-person at Swearingen Engineering Center hackathon via Devpost.", "organizer": "Kappa"}
    assert editorial.opportunity_rejection(e, today) == "outside Jaipur/Delhi-NCR"
    e2 = {"title": "OpenHack", "summary": "Online hackathon via Devpost.", "organizer": "Self"}
    assert editorial.opportunity_rejection(e2, today) == "no concrete date"
