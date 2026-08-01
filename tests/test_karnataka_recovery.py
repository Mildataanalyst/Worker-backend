import csv
import time
from pathlib import Path

import pytest

import karnataka_recovery as kr


class FakeSerperPool:
    def __init__(self, payload):
        self.payload = payload
        self.provider_attempts = 1

    def search(self, query, timeout=20):
        return self.payload, [{"attempt": 1, "key": "...test", "status": 200, "body": ""}]


def base_row(**changes):
    row = {
        "name": "Example Children Foundation",
        "district": "Bengaluru Urban",
        "state": "Karnataka",
        "source_record_id": "KA-TEST-1",
        "source_fingerprint": "abc",
        "source_row_number": 2,
        "registration_reference": "REG-123",
        "registered_address": "Singasandra Bengaluru 560068",
        "pincode": "560068",
        "referral_name": "",
        "public_name": "",
        "project_name": "",
        "parent_organisation": "",
        "email": "",
        "phone": "",
        "sector_tags": "Children",
        "queue_action": "",
        "failed_query_passes": "",
        "recovery_mode": "enhanced_search",
    }
    row.update(changes)
    return row


def owned_fetch(url, name, text="", *, site_name=None, h1=None, footer=None, org_names=None):
    site_name = site_name or name
    h1 = h1 or name
    footer = footer or f"Copyright {name}. All rights reserved."
    org_names = org_names or [name]
    body = text or f"Welcome to {name}. {name} is a registered organisation."
    return {
        "ok": True,
        "text": body,
        "page_title": name,
        "meta_description": body,
        "mailto": "",
        "tel": "",
        "metadata": {
            "h1_text": h1,
            "footer_text": footer,
            "og_site_name": site_name,
            "jsonld_org_names": org_names,
            "jsonld_names": org_names,
            "jsonld_types": ["organization"],
            "article_metadata": False,
        },
        "url": url,
        "status": 200,
        "fetch_status": "direct_ok",
        "error": "",
        "firecrawl_recommended": False,
    }


def test_input_preserves_same_name_same_district_source_rows(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text(
        "source_record_id,name,district,state,registration_reference,registered_address\n"
        "SRC-1,Same Name Trust,Bengaluru Urban,Karnataka,REG-1,Address one\n"
        "SRC-2,Same Name Trust,Bengaluru Urban,Karnataka,REG-2,Address two\n",
        encoding="utf-8",
    )
    rows = kr.read_input_csv(path, "identity_collision")
    assert len(rows) == 2
    assert [row["source_record_id"] for row in rows] == ["SRC-1", "SRC-2"]
    assert rows[0]["registration_reference"] != rows[1]["registration_reference"]


def test_zero_query_mode_never_needs_serper(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(website="https://examplechildren.org", recovery_mode="known_url_identity")
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: owned_fetch(
        "https://examplechildren.org/", "Example Children Foundation",
        "Welcome to Example Children Foundation. Bengaluru Urban 560068 REG-123.",
    ))
    result, _ = service._process_row(
        row, "known_url_identity", None, None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 0},
        False, 60,
    )
    assert result["Discovery Status"] == "verified_owned_site"
    assert result["Logical Queries Used"] == 0
    assert result["Searched"] == "no"


def test_missing_query_only_runs_exactly_one_logical_query(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(recovery_mode="missing_query_only", failed_query_passes="public_brand_geo", public_name="Example Learning Home")
    payload = {"organic": [{"position": 1, "title": "Example Learning Home", "link": "https://examplechildren.org", "snippet": "Example Children Foundation Bengaluru Urban 560068"}]}
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: owned_fetch(
        "https://examplechildren.org/", "Example Children Foundation",
        "Welcome to Example Children Foundation. Example Learning Home Bengaluru Urban 560068 REG-123.",
    ))
    shared = {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}
    result, _ = service._process_row(row, "missing_query_only", FakeSerperPool(payload), None, shared, False, 60)
    assert result["Discovery Status"] == "verified_owned_site"
    assert result["Logical Queries Used"] == 1
    assert shared["logical_queries"] == 1


def test_directory_is_carrier_and_pipeline_continues_to_owned_site(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(name="Deenabandhu", district="Chamarajanagar", pincode="571313", registration_reference="REG-DEENA")
    payload = {"organic": [
        {"position": 1, "title": "Deenabandhu NGO directory", "link": "https://www.oneindia.com/ngos-in-chamarajanagar", "snippet": "Deenabandhu Chamarajanagar"},
        {"position": 2, "title": "Deenabandhu official", "link": "https://deenabandhu.org", "snippet": "Deenabandhu Chamarajanagar 571313"},
    ]}
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: owned_fetch(
        url, "Deenabandhu", "Welcome to Deenabandhu. Chamarajanagar 571313 REG-DEENA children home.",
    ))
    result, audit = service._process_row(
        row, "enhanced_search", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}, False, 60,
    )
    assert result["Website"].startswith("https://deenabandhu.org")
    assert result["Discovery Status"] == "verified_owned_site"
    assert any(event.decision == "carrier_only_continue" and event.page_type == "directory_or_registry" for event in audit)


def test_verified_hosted_page_gets_controlled_microsite_status(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(name="Sadhana", district="Raichur", pincode="584128", phone="9876543210", website="https://sadhana.1ngo.in/", recovery_mode="known_url_identity")
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: owned_fetch(
        url, "Sadhana", "Welcome to Sadhana. Sadhana Raichur 584128 phone 9876543210.",
        site_name="Sadhana Raichur", h1="Sadhana Raichur", org_names=["Sadhana"],
    ))
    result, _ = service._process_row(row, "known_url_identity", None, None, {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 0}, False, 60)
    assert result["Discovery Status"] == "verified_controlled_microsite"
    assert result["Ownership Class"] == "controlled_hosted_presence"


def test_deadline_exception_carries_best_candidate_and_counters():
    ctx = kr.RowContext(row=base_row(), mode="enhanced_search", deadline_at=time.monotonic() - 1, max_queries=3)
    ctx.logical_queries_used = 2
    ctx.provider_attempts = 3
    ctx.best_candidate = {"url": "https://candidate.example.org", "page_type": "owned_site_candidate"}
    with pytest.raises(kr.RowDeadlineReached) as caught:
        ctx.check_deadline()
    assert caught.value.ctx.best_candidate["url"] == "https://candidate.example.org"
    assert caught.value.ctx.logical_queries_used == 2
    assert caught.value.ctx.provider_attempts == 3


def test_serper_pool_retries_transient_failure_on_same_account(monkeypatch):
    pool = kr.SerperPool(["single-funded-key"], 1, {})
    pool.states[0].state = "healthy"
    calls = []

    class Response:
        headers = {}
        def __init__(self, status_code, text, payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload or {}
        def json(self):
            return self._payload

    def fake_post(url, headers, json, timeout):
        calls.append(headers["X-API-KEY"])
        if len(calls) == 1:
            return Response(500, "temporary provider error")
        return Response(200, "", {"organic": []})

    monkeypatch.setattr(kr.requests, "post", fake_post)
    payload, attempts = pool.search("same logical query")
    assert payload == {"organic": []}
    assert calls == ["single-funded-key", "single-funded-key"]
    assert len(attempts) == 2


def test_capacity_route_reports_effective_single_account_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv('SERPER_API_KEY', 'key-one')
    monkeypatch.setenv('SERPER_API_KEYS', 'legacy-key-must-be-ignored')

    def fake_preflight(self, enabled=True):
        assert len(self.states) == 1
        assert self.states[0].key == 'key-one'
        self.states[0].state = 'healthy'
        return self.stats()

    monkeypatch.setattr(kr.SerperPool, 'preflight', fake_preflight)
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    route = next(route for route in service.router.routes if getattr(route, 'path', '') == '/karnataka-recovery/capacity')
    response = route.endpoint(serper_concurrency=2, include_firecrawl=False, firecrawl_budget=5000)
    payload = __import__('json').loads(response.body)
    assert payload['ok'] is True
    assert payload['healthy_serper_accounts'] == 1
    assert payload['recommended_max_concurrency'] == 2
    assert payload['configuration_warning'].startswith('SERPER_API_KEYS is ignored')


def test_status_route_handles_persisted_ok_field_without_500(tmp_path):
    import json

    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    run_id = "karnataka_test_status"
    rd = service._run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    service._write_json(
        rd / kr.RESULT_FILES["status"],
        {
            "ok": True,
            "run_id": run_id,
            "run_status": "completed",
            "stage": "results_ready",
            "processed": 44,
            "total": 44,
        },
    )
    route = next(
        route for route in service.router.routes
        if getattr(route, "path", "") == "/karnataka-recovery/status/{run_id}"
    )
    response = route.endpoint(run_id)
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["run_id"] == run_id
    assert payload["run_status"] == "completed"


def test_karnataka_recovery_paths_are_exempt_from_admin_password():
    import main

    assert main._mutation_auth_exempt_path('/karnataka-recovery/start') is True
    assert main._mutation_auth_exempt_path('/karnataka-recovery/pause/run-1') is True
    assert main._mutation_auth_exempt_path('/karnataka-recovery/cancel/run-1') is True
    assert main._mutation_auth_exempt_path('/karnataka-recovery/resume/run-1') is True
    assert main._mutation_auth_exempt_path('/repository/delete') is False


def test_karnataka_recovery_start_is_not_blocked_by_auth_middleware(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv('ADMIN_PASSWORD', 'still-protect-other-routes')
    app = FastAPI()

    @app.middleware('http')
    async def guard(request, call_next):
        return await main.mutation_auth_middleware(request, call_next)

    @app.post('/karnataka-recovery/start')
    async def recovery_start():
        return {'ok': True}

    @app.post('/repository/delete')
    async def protected_delete():
        return {'ok': True}

    client = TestClient(app)
    assert client.post('/karnataka-recovery/start').status_code == 200
    protected = client.post('/repository/delete')
    assert protected.status_code == 401
    assert protected.json()['error'] == 'Admin password required'


def test_missing_query_without_verified_site_routes_to_enhanced_search(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = base_row(
        recovery_mode="missing_query_only",
        failed_query_passes="public_brand_geo",
        public_name="Example Learning Home",
    )
    shared = {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}
    result, _ = service._process_row(
        source,
        "missing_query_only",
        FakeSerperPool({"organic": []}),
        None,
        shared,
        False,
        60,
    )
    assert result["Discovery Status"] == "enhanced_search_required"
    assert result["Retry Required"] == "yes"
    assert result["Retry Reason"] == "missing_query_completed_no_verified_site"
    assert result["Logical Queries Used"] == 1
    assert "not a no-site conclusion" in result["Note"]


def test_missing_query_handoff_is_exported_as_enhanced_search_retry(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    service._init_outputs(run_dir)
    source = base_row(
        recovery_mode="missing_query_only",
        failed_query_passes="public_brand_geo",
        public_name="Example Learning Home",
    )
    shared = {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}
    result, events = service._process_row(
        source,
        "missing_query_only",
        FakeSerperPool({"organic": []}),
        None,
        shared,
        False,
        60,
    )
    service._checkpoint(run_dir, source, "missing_query_only", result, events)
    derived = service._write_derived_exports(run_dir)
    assert derived["no_site"] == 0
    assert derived["retry"] == 1
    with (run_dir / kr.RESULT_FILES["retry"]).open(encoding="utf-8-sig", newline="") as handle:
        retry = next(csv.DictReader(handle))
    assert retry["previous_discovery_status"] == "enhanced_search_required"
    assert retry["recovery_mode_override"] == "enhanced_search"


def test_enhanced_search_without_verified_site_remains_terminal_no_site(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = base_row(recovery_mode="enhanced_search")
    shared = {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}
    result, _ = service._process_row(
        source,
        "enhanced_search",
        FakeSerperPool({"organic": []}),
        None,
        shared,
        False,
        60,
    )
    assert result["Discovery Status"] == "no_owned_site_after_enhanced_recovery"
    assert result["Retry Required"] == "no"


def test_missing_query_manual_candidate_hands_off_to_enhanced_search(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = base_row(
        name="Generic Hope Trust",
        registration_reference="",
        registered_address="",
        pincode="",
        recovery_mode="missing_query_only",
        failed_query_passes="public_brand_geo",
        public_name="Hope",
    )
    payload = {"organic": [{
        "position": 1,
        "title": "Hope",
        "link": "https://hopeexample.org/",
        "snippet": "Hope works with children in Bengaluru.",
    }]}
    monkeypatch.setattr(service, "_verify_one_candidate", lambda *args, **kwargs: {
        "url": "https://hopeexample.org/",
        "fetch_ok": True,
        "fetch_status": "direct_ok",
        "fetch_error": "",
        "verified": False,
        "status": "plausible_site_identity_review",
        "page_type": "owned_organisation_site",
        "ownership": "identity_review_required",
        "confidence": "low",
        "evidence": "name overlap",
        "ownership_evidence": "brand-only domain",
        "ownership_gate": "manual_ownership_insufficient",
        "risk_flags": "generic_without_source_specific_identity",
        "conflicts": "",
        "evidence_score": 6,
        "firecrawl_action": "",
        "firecrawl_credits": 0,
    })
    result, _ = service._process_row(
        source, "missing_query_only", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10},
        False, 60,
    )
    assert result["Discovery Status"] == "enhanced_search_required"
    assert result["Retry Required"] == "yes"
    assert result["Website"] == "https://hopeexample.org/"


def test_missing_query_unreachable_candidate_hands_off_to_enhanced_search(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = base_row(
        recovery_mode="missing_query_only",
        failed_query_passes="public_brand_geo",
        public_name="Example Learning Home",
    )
    payload = {"organic": [{
        "position": 1,
        "title": "Example Learning Home",
        "link": "https://examplechildren.org/",
        "snippet": "Example Children Foundation Bengaluru Urban 560068.",
    }]}
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: {
        "ok": False,
        "text": "",
        "url": url,
        "status": 403,
        "fetch_status": "blocked",
        "error": "HTTP 403",
        "firecrawl_recommended": True,
    })
    result, _ = service._process_row(
        source, "missing_query_only", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10},
        False, 60,
    )
    assert result["Discovery Status"] == "enhanced_search_required"
    assert result["Retry Required"] == "yes"
    assert result["Website"] == "https://examplechildren.org/"
    assert result["Retry Reason"] == "missing_query_completed_no_verified_site"
