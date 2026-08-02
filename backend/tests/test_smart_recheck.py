"""Pure-function and isolated-flow tests for DFP 2.0 advanced website recovery."""
import csv
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


def test_bad_url_keeps_documents_and_hosted_sites():
    assert main._recheck_bad_url("https://goodnewsngo.org") is False
    assert main._recheck_bad_url("https://somengo.org/newsletter") is False
    assert main._recheck_bad_url("https://somengo.org/fcra/FC-4.pdf") is False
    assert main._recheck_bad_url("https://smallngo.wordpress.com") is False
    assert main._recheck_bad_url("https://smallngo.blogspot.com") is False
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


def test_darpan_query_is_first_and_exact():
    row = {
        "name": "Example Education Trust",
        "district": "Mysuru",
        "state": "Karnataka",
        "Darpan ID": "KA/2020/1234567",
        "email": "contact@example.org",
    }
    queries = main._smart_queries(row, main._smart_name_variants(row["name"]))
    assert queries[0]["pass"] == "identifier"
    assert queries[0]["query"] == '"KA/2020/1234567"'
    assert len(queries) == 2
    assert queries[1]["pass"] == "public_brand_geo"
    # Email domains are verified directly before paid search; no contact query is spent.
    assert not any(q["pass"] == "contact" for q in queries)


def test_identifier_result_can_be_nominated_without_name_in_domain():
    row = {"name": "Example Education Trust", "district": "Mysuru", "state": "Karnataka", "darpan_id": "KA/2020/1234567"}
    cand = {
        "url": "https://example.org/reports/fc4.pdf",
        "title": "FCRA Annual Return",
        "snippet": "Unique ID of VO/NGO KA/2020/1234567",
        "source": "organic",
    }
    qinfo = {"pass": "identifier", "variant": "KA/2020/1234567", "variant_type": "identifier:darpan_id"}
    score, note, route, reject = main._smart_score_candidate(row["name"], main._smart_name_variants(row["name"]), row, cand, qinfo)
    assert score >= main.SMART_RECHECK_NOMINATION_SCORE
    assert route == "identifier_search"


def test_evidence_grades_identifier_and_multi_attribute():
    row = {
        "name": "Example Education Trust",
        "darpan_id": "KA/2020/1234567",
        "phone": "9876543210",
        "state": "Karnataka",
        "district": "Mysuru",
    }
    grade_a = main._smart_evaluate_pages([("https://example.org/fc4.pdf", "Example Education Trust KA/2020/1234567")], row, [])
    assert grade_a["grade"] == "A"

    no_id_row = {k: v for k, v in row.items() if k != "darpan_id"}
    grade_b_plus = main._smart_evaluate_pages([("https://example.org/contact", "Example Education Trust phone 9876543210")], no_id_row, [])
    assert grade_b_plus["grade"] == "B+"


def test_grade_c_is_manual_and_rename_requires_stronger_closure():
    assert main._smart_status("direct", "C")[0] == "possible_site_manual_review"
    assert main._smart_accepts_automatically("C") is False
    assert main._smart_status("rename_detected", "A")[0] == "rename_verified_match"
    assert main._smart_status("rename_detected", "B+")[0] == "rename_verified_match"
    assert main._smart_status("rename_detected", "B")[0] == "probable_official_site"


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


def test_input_dedupes_by_darpan_and_preserves_fields(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text(
        "name,district,state,darpan_id,email\n"
        "Example Trust,Mysuru,Karnataka,KA/1,one@example.org\n"
        "Different Display Name,Bengaluru,Karnataka,KA/1,two@example.org\n"
        "Example Trust,Mysuru,Karnataka,KA/2,three@example.org\n",
        encoding="utf-8",
    )
    rows = main._read_recheck_input(path)
    assert len(rows) == 2
    assert rows[0]["darpan_id"] == "KA/1"
    assert rows[0]["email"] == "one@example.org"


def test_all_provider_failures_are_not_called_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_smart_provider_available", lambda provider: provider == "serper")
    monkeypatch.setattr(main, "_smart_search_provider", lambda provider, query: ([], "provider unavailable"))
    monkeypatch.setattr(main, "_smart_rename_recovery", lambda *args, **kwargs: None)
    row = {"name": "Example Education Trust", "district": "Mysuru", "state": "Karnataka", "darpan_id": "KA/2020/1234567"}
    counter = {"queries": 0, "serper_queries": 0, "brave_queries": 0}
    result = main._smart_process_row(row, tmp_path, [], counter)
    assert result["Website Status"] == "provider_failure"
    assert result["Website Status"] != "no_candidate_after_completed_search"


def test_brave_response_adapter():
    data = {"web": {"results": [{"url": "https://example.org", "title": "Example Trust", "description": "Official site", "extra_snippets": ["Mysuru"]}]}}
    cands = main._brave_candidates(data)
    assert cands[0]["url"] == "https://example.org"
    assert cands[0]["source"] == "brave_web"
    assert "Mysuru" in cands[0]["snippet"]
