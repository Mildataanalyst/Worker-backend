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
