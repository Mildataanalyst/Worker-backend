import main


def test_firecrawl_default_budget_has_safety_buffer():
    assert main.SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET == 10000
    assert main.SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET == 7000
    assert main.SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET == 2000
    assert main.SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET + main.SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET <= main.SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET
    assert main.SMART_RECHECK_FIRECRAWL_PROXY == "basic"


def test_firecrawl_budget_stops_search_at_cap(monkeypatch):
    monkeypatch.setattr(main, "SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET", 4)
    monkeypatch.setattr(main, "SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET", 4)
    counter = main._smart_firecrawl_counter_init({})
    assert main._smart_firecrawl_reserve(counter, "search", 2)[0] is True
    assert main._smart_firecrawl_reserve(counter, "search", 2)[0] is True
    ok, note = main._smart_firecrawl_reserve(counter, "search", 2)
    assert ok is False
    assert "budget" in note.lower()
    assert counter["firecrawl_credits"] == 4


def test_firecrawl_per_domain_scrape_cap(monkeypatch):
    monkeypatch.setattr(main, "SMART_RECHECK_FIRECRAWL_MAX_SCRAPES_PER_DOMAIN", 2)
    counter = main._smart_firecrawl_counter_init({})
    assert main._smart_firecrawl_reserve(counter, "verify", 1, "example.org")[0]
    assert main._smart_firecrawl_reserve(counter, "verify", 1, "example.org")[0]
    ok, note = main._smart_firecrawl_reserve(counter, "verify", 1, "example.org")
    assert ok is False
    assert "per-domain" in note


def test_firecrawl_recovery_query_does_not_repeat_darpan_id():
    row = {"name": "Example Education Charitable Trust", "darpan_id": "KA/2020/1234567", "district": "Mysuru", "state": "Karnataka"}
    q = main._smart_firecrawl_recovery_query(row)
    assert "KA/2020/1234567" not in q["query"]
    assert "example" in q["query"].lower()
    assert q["provider"] == "firecrawl"


def test_standard_verification_does_not_spend_firecrawl(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_smart_fetch_page", lambda url, allow_firecrawl=True, counter=None: (calls.append(allow_firecrawl) or (url, "Example Trust Karnataka", "", "")))
    result = main._smart_verify_candidate("https://example.org", {"name":"Example Trust", "state":"Karnataka"}, "direct", counter={})
    assert calls
    assert all(value is False for value in calls)
    assert result["firecrawl_credits_used"] == 0


def test_firecrawl_input_export_includes_unresolved(tmp_path):
    rows = [{
        "NGO Name":"Missing NGO", "State":"Karnataka", "District":"Mysuru", "Darpan ID":"KA/1",
        "Email":"x@example.org", "Phone":"9876543210", "Registered Address":"Mysuru 570001",
        "Website":"", "Website Status":"no_candidate_after_completed_search",
    }]
    main._write_recheck_csvs(tmp_path, rows, [])
    output = (tmp_path / main.RECHECK_OUTPUTS["firecrawl_input"]).read_text(encoding="utf-8-sig")
    assert "previous_website_status" in output
    assert "no_candidate_after_completed_search" in output
