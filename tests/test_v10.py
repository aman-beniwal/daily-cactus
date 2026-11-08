"""v10 (9 Nov 2026): wider picks + the L1/L2/L3 layer."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import editorial as ed  # noqa: E402
import newsroom as nr  # noqa: E402


def test_funding_words_earn_no_interest_bonus():
    assert ed.interest_score("Acme raises $40M Series B funding at a higher valuation") == 0


def test_routine_round_flagged_but_rate_moves_are_not():
    flagged = lambda t: any(f.startswith("routine funding") for f in ed.format_flags({"title": t}))
    assert flagged("Zepto raises $450M in Series G")
    assert flagged("GalaxEye gets Rs 64 crore funding")
    assert not flagged("RBI raises repo rate by 25 bps")
    assert not flagged("Razorpay IPO gets SEBI nod")
    assert ed.format_penalty(["routine funding round (one company's raise: L1 data point)"]) == -2.0


def test_fit_counts_half():
    digest = {"date": "2026-11-09", "sections": [{"slug": "world", "stories": [
        {"id": "w1", "title": "big"}, {"id": "w2", "title": "small"}]}]}
    raw = {"lead": None, "frontpage": [], "sections": [],
           "scores": {"w1": [4, 3, 4, 0, ""], "w2": [2, 2, 2, 4, ""]}}
    sel = nr.validate_selection(raw, digest)
    assert sel["lead"] == "w1"          # 11 vs 8: the big off-beat story wins
    assert sel["_scores"]["w2"] == 8


def test_recent_patterns_and_streak(tmp_path):
    for d, name in [("2026-11-05", "Capital is moving into hard tech"),
                    ("2026-11-07", "capital moving into hard tech"),
                    ("2026-10-01", "Capital is moving into hard tech")]:   # outside 14 days
        (tmp_path / f"{d}.json").write_text(json.dumps({"patterns": [{"name": name, "force": "capital"}]}))
    recent = nr.recent_patterns(tmp_path, "2026-11-09")
    assert len(recent) == 1 and sorted(recent[0]["days"]) == ["2026-11-05", "2026-11-07"]
    draft = {"patterns": [{"name": "Capital is moving into hard tech", "line": "x", "evidence": ["a"]}]}
    nr.annotate_streaks(draft, recent)
    assert draft["patterns"][0]["streak"] == 3
    assert "RECENT PATTERNS" in nr.patterns_input(recent)


def test_validate_draft_money_patterns_forces():
    sel = {"date": "2026-11-09", "lead": "L", "frontpage": [], "opportunities": [], "longform": [],
           "sections": [{"slug": "indian-startups", "stories": ["C"], "also": ["R1", "R2"]}]}
    draft = {"lead": {"id": "L", "headline": "h", "force": "vibes", "evidence_of": "  "},
             "sections": [{"slug": "indian-startups",
                           "stories": [{"id": "C", "headline": "c", "force": "capital", "evidence_of": "Money into chips"}],
                           "also": [{"id": "R1", "line": "x"}, {"id": "R2", "line": "y"}]}],
             "money": [{"id": "R1", "company": "Acme", "amount": "$5M", "sector": "chips", "line": "z"},
                       {"id": "C", "company": "Dup", "amount": "$1M"},          # already a card
                       {"id": "NOPE", "company": "X", "amount": "$1"}],
             "patterns": [{"name": "Money into chips", "force": "capital", "line": "l", "evidence": ["C", "R1", "BAD"]},
                          {"name": "No evidence", "line": "l", "evidence": ["BAD"]}]}
    out = nr.validate_draft(draft, sel)
    assert [m["id"] for m in out["money"]] == ["R1"]
    assert [a["id"] for a in out["sections"][0]["also"]] == ["R2"]     # R1 moved to money
    assert "force" not in out["lead"] and "evidence_of" not in out["lead"]
    assert out["sections"][0]["stories"][0]["force"] == "capital"
    assert len(out["patterns"]) == 1 and out["patterns"][0]["evidence"] == ["C", "R1"]


def test_soft_budget_needs_strong_cards_past_30():
    ids = [f"s{i}" for i in range(40)]
    digest = {"date": "2026-11-09", "sections": [{"slug": "world", "stories": [{"id": i, "title": i} for i in ids]}]}
    scores = {i: ([4, 4, 4, 2, ""] if n < 32 else [2, 2, 2, 2, ""]) for n, i in enumerate(ids)}
    raw = {"lead": "s0", "frontpage": [], "sections": [{"slug": "world", "stories": ids}], "scores": scores}
    sel = nr.validate_selection(raw, digest)
    full = 1 + sum(len(x["stories"]) for x in sel["sections"])
    assert full == 32           # 32 strong cards kept, the 8 weak ones past 30 dropped
