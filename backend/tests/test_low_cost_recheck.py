import csv
from pathlib import Path

import main


def test_low_cost_defaults_disable_paid_fallbacks():
    assert main.SMART_RECHECK_MAX_QUERIES_PER_ROW == 2
    assert main.SMART_RECHECK_MAX_TOTAL_QUERIES == 58000
    assert main.SMART_RECHECK_USE_BRAVE is False
    assert main.SMART_RECHECK_USE_FIRECRAWL is False
    assert main.SMART_RECHECK_ENABLE_RENAME_RECOVERY is False


def test_darpan_query_plan_is_exactly_two_passes():
    row = {
        "name": "Example Education Trust",
        "district": "Mysuru",
        "state": "Karnataka",
        "darpan_id": "KA/2020/1234567",
        "pan": "ABCDE1234F",
    }
    queries = main._smart_queries(row, main._smart_name_variants(row["name"]))
    assert len(queries) == 2
    assert queries[0]["query"] == '"KA/2020/1234567"'
    assert queries[0]["pass"] == "identifier"
    assert queries[1]["pass"] == "public_brand_geo"
    assert "example" in queries[1]["query"].lower()
    assert "Mysuru Karnataka" in queries[1]["query"]
    assert "ABCDE1234F" not in " ".join(q["query"] for q in queries)


def test_unreachable_candidate_is_not_called_not_found(monkeypatch, tmp_path):
    row = {"name": "Example Trust", "district": "Mysuru", "state": "Karnataka", "darpan_id": "KA/2020/1234567"}
    monkeypatch.setattr(main, "_smart_provider_available", lambda provider: provider == "serper")
    monkeypatch.setattr(main, "_smart_search_provider", lambda provider, query: ([{
        "url": "https://example.org", "title": "Example Trust", "snippet": "Mysuru Karnataka", "source": "organic"
    }], None))
    monkeypatch.setattr(main, "_smart_score_candidate", lambda *args, **kwargs: (20, "strong candidate", "direct", ""))
    monkeypatch.setattr(main, "_smart_verify_candidate", lambda *args, **kwargs: {
        "grade": "D", "all_fetches_failed": True, "fetch_status": "unreachable",
        "fetch_errors": "timeout", "type": "", "matched": "", "page": ""
    })
    result = main._smart_process_row(row, tmp_path, [], {"queries": 0, "serper_queries": 0, "brave_queries": 0})
    assert result["Website Status"] == "candidate_site_unreachable"
    assert result["Website"] == "https://example.org"
    assert result["Fetch Status"] == "unreachable"
    assert result["Queries Used"] <= 2


def test_retry_of_unreachable_candidate_uses_no_search(monkeypatch, tmp_path):
    row = {
        "name": "Example Trust", "district": "Mysuru", "state": "Karnataka",
        "website": "https://example.org", "previous_website_status": "candidate_site_unreachable",
    }
    monkeypatch.setattr(main, "_smart_verify_candidate", lambda *args, **kwargs: {
        "grade": "D", "all_fetches_failed": True, "fetch_status": "unreachable",
        "fetch_errors": "still down", "type": "", "matched": "", "page": ""
    })
    monkeypatch.setattr(main, "_smart_provider_available", lambda provider: (_ for _ in ()).throw(AssertionError("search provider should not be consulted")))
    result = main._smart_process_row(row, tmp_path, [], {"queries": 0, "serper_queries": 0, "brave_queries": 0})
    assert result["Website Status"] == "candidate_site_unreachable"
    assert result["Queries Used"] == 0
    assert result["Searched"] == "no"


def test_avika_input_export_contains_only_verified_rows(tmp_path):
    rows = [
        {"NGO Name": "Confirmed NGO", "District": "Mysuru", "State": "Karnataka", "Darpan ID": "KA/1", "Website": "https://confirmed.org", "Website Status": "confirmed_official_site"},
        {"NGO Name": "Unreachable NGO", "District": "Mysuru", "State": "Karnataka", "Darpan ID": "KA/2", "Website": "https://down.org", "Website Status": "candidate_site_unreachable"},
    ]
    main._write_recheck_csvs(tmp_path, rows, [])
    with (tmp_path / main.RECHECK_OUTPUTS["avika_input"]).open("r", encoding="utf-8-sig", newline="") as f:
        exported = list(csv.DictReader(f))
    assert len(exported) == 1
    assert exported[0]["name"] == "Confirmed NGO"
    assert exported[0]["website"] == "https://confirmed.org"


def test_repository_engine_keeps_avika_filter_and_accepts_supplied_websites():
    engine = (Path(main.__file__).resolve().parent / "engine" / "dfp2_engine_safe_v5_live_status.py").read_text(encoding="utf-8")
    assert 'FILTER_VERSION = os.environ.get("DFP_FILTER_VERSION", "avika_fit_v2")' in engine
    assert 'supplied_website = (r.get("website")' in engine
    assert 'Serper search skipped' in engine
    assert 'Filtered out by Avika-fit classifier' in engine
