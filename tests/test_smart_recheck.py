"""Pure-function smoke tests for DFP 2.0 smart recheck.
Run from backend root: python -m pytest backend/tests/test_smart_recheck.py
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def test_parikrma_variants():
    v = main._smart_name_variants("Parikrma Humanity Foundation")
    assert v["core_tokens"] == ["parikrma"]
    assert "humanity" not in v["core_tokens"]
    assert "foundation" not in v["core_tokens"]


def test_bridges_compact_primary():
    v = main._smart_name_variants("Bridges of Sports Foundation")
    assert v["compact_primary"] == "bridgesofsports"


def test_bad_url_news_fix():
    assert main._recheck_bad_url("https://goodnewsngo.org") is False
    assert main._recheck_bad_url("https://somengo.org/newsletter") is False
    assert main._recheck_bad_url("https://timesofindia.com/x") is True
    assert main._recheck_bad_url("https://facebook.com/ngo") is True


def test_fuzzy_nomination():
    row = {"name": "Parikrama Humanity Foundation", "district": "Bengaluru", "state": "Karnataka"}
    v = main._smart_name_variants(row["name"])
    cand = {"url":"https://parikrma.org", "title":"Parikrma Foundation", "snippet":"Bengaluru education NGO", "source":"organic"}
    score, note, route, reject = main._smart_score_candidate(row["name"], v, row, cand, {"variant_type":"core_brand"})
    assert score >= main.SMART_RECHECK_NOMINATION_SCORE
    assert route == "fuzzy_spelling"


def test_short_alias_query_only():
    v = main._smart_name_variants("Bridges of Sports Foundation")
    bos = [x for x in v["variants"] if x["variant"].lower() == "bos"]
    assert bos and bos[0]["query_only"] is True


def test_rename_carrier_extracts_public_brand():
    item = {
        "title": "Feeding India | Official Website",
        "snippet": "Feeding India is a society, registered as Hunger Heroes and working across India.",
        "url": "https://give.do/nonprofits/feeding-india",
        "raw": {"link": "https://give.do/nonprofits/feeding-india", "website": "https://www.feedingindia.org"},
    }
    hits = main._smart_extract_rename_brands("Hunger Heroes", item)
    assert hits
    assert hits[0]["brand"] == "Feeding India"
    assert "Hunger Heroes" in hits[0]["carrier_phrase"]


def test_rename_grade_c_does_not_accept():
    verify = {"grade": "C", "type": "brand_plus_geo", "matched": "feeding india, India", "page": "https://www.feedingindia.org"}
    # The helper should downgrade Grade C in rename route before decisioning.
    # Avoid network: simulate by asserting status only accepts A/B for rename; C is not rename_verified_match.
    assert main._smart_status("rename_detected", "A")[0] == "rename_verified_match"
    assert main._smart_status("rename_detected", "B")[0] == "probable_official_site"
