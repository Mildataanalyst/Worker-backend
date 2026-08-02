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
    row = base_row(website="https://example.org", recovery_mode="known_url_identity")
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: {
        "ok": True, "text": "Example Children Foundation Bengaluru Urban 560068 REG-123",
        "url": "https://example.org/", "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
    })
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
    payload = {"organic": [{"position": 1, "title": "Example Learning Home", "link": "https://example.org", "snippet": "Example Children Foundation Bengaluru Urban 560068"}]}
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: {
        "ok": True, "text": "Example Children Foundation Example Learning Home Bengaluru Urban 560068 REG-123",
        "url": "https://example.org/", "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
    })
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
        {"position": 2, "title": "Deenabandhu official", "link": "https://deenabandhu.example.org", "snippet": "Deenabandhu Chamarajanagar 571313"},
    ]}
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: {
        "ok": True, "text": "Deenabandhu Chamarajanagar 571313 REG-DEENA children home",
        "url": url, "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
    })
    result, audit = service._process_row(
        row, "enhanced_search", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 10}, False, 60,
    )
    assert result["Website"].startswith("https://deenabandhu.example.org")
    assert result["Discovery Status"] == "verified_owned_site"
    assert any(event.decision == "carrier_only_continue" and event.page_type == "directory_or_registry" for event in audit)


def test_verified_hosted_page_gets_controlled_microsite_status(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(name="Sadhana", district="Raichur", pincode="584128", phone="9876543210", website="https://sadhana.1ngo.in/", recovery_mode="known_url_identity")
    monkeypatch.setattr(kr, "fetch_direct", lambda url, remaining: {
        "ok": True, "text": "Sadhana Raichur 584128 phone 9876543210",
        "url": url, "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
    })
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


def metadata(title, site_name=None, footer=None):
    return {
        "page_title": title,
        "og_site_name": site_name if site_name is not None else title,
        "footer_text": footer if footer is not None else f"© {title}",
        "meta_description": "",
        "jsonld_org_names": [],
    }


@pytest.mark.parametrize("name,url", [
    ("Guardians of Dreams", "https://milaap.org/fundraisers/godreamskochi1"),
    ("Asha Kirana Seva Trust", "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust"),
    ("Vimochana Development Society", "https://www.myngos.in/ngo-details/vimochana-development-society-in-karnataka"),
    ("Shivashakthi Foundation", "https://www.tatanexarc.com/company/shree-shivashakthi-innovative-utn5150shr80xqx/"),
    ("Reach Rural Development Society", "https://www.ixigo.com/buses/hyderabad-yadgir-sts"),
    ("The Hope House", "https://pmc.ncbi.nlm.nih.gov/articles/PMC10615235/"),
    ("Auxilium Navajeevana Society", "https://orellsoft.com/client"),
    ("Deenabandhu", "https://rinatham.com/2018/03/13/deenabandhu-chamarajanagar-karnataka/"),
    ("Gonikoppal Higher Primary School", "https://abhyudayakkss.org/school-kit-distribution-at-ghps-kajuru-aigur-village/"),
])
def test_known_third_party_patterns_are_never_owned_candidates(name, url):
    row = base_row(name=name, referral_name=name)
    assert kr.page_type_for_candidate(url, row=row) in {
        "directory_or_registry", "article_or_profile", "third_party_mention_candidate",
        "government_academic_or_document_reference", "wrong_entity",
    }


@pytest.mark.parametrize("row,url,brand,body", [
    (
        base_row(name="Shifting Orbits Foundation", referral_name="Shifting Orbits Foundation", registration_reference="", registered_address="", pincode=""),
        "https://www.northsouth.org/", "NorthSouth",
        "NorthSouth supported a changemaker working with Shifting Orbits Foundation.",
    ),
    (
        base_row(name="DIVINE MERCY CHARITABLE TRUST", referral_name="Divine Mercy Charitable Trust", registration_reference="", registered_address="", pincode=""),
        "https://www.divinemercydevotion.net/", "Divine Mercy Devotion",
        "Divine Mercy prayers and novena. Divine Mercy Charitable Trust is mentioned in one story.",
    ),
    (
        base_row(name="VISION INDIA FOUNDATION", referral_name="Vision India Trust", registration_reference="", registered_address="", pincode=""),
        "https://www.giftofvision.org/25-years-of-sankara-eye-foundation-usa", "Sankara Eye Foundation",
        "An anniversary article that mentions Vision India Foundation.",
    ),
])
def test_exact_name_mention_without_ownership_is_rejected(row, url, brand, body):
    result = kr.identity_verification(
        row, url, body, "owned_site_candidate", fetch_metadata=metadata(brand)
    )
    assert result["status"] == "rejected_third_party_or_unowned"
    assert result["verified"] is False
    assert "no independent ownership proof" in result["conflicts"]


def test_official_domain_with_structured_owner_verifies():
    row = base_row(
        name="PRAGATHI CHARITABLE TRUST", referral_name="Pragathi Charitable Trust",
        registration_reference="", registered_address="", pincode="",
    )
    result = kr.identity_verification(
        row, "https://pragathitrust.org/",
        "PRAGATHI CHARITABLE TRUST is a registered charitable trust in Bengaluru Urban.",
        "owned_site_candidate", fetch_metadata=metadata("Pragathi Charitable Trust"),
    )
    assert result["status"] == "verified_owned_site"
    assert result["ownership"] == "owned_organisation_site"


def test_raw_legal_acronym_can_prove_official_domain():
    row = base_row(
        name="VIKAS DISABLED CHARITABLE TRUST", referral_name="Vikasa Special School",
        registration_reference="", registered_address="", pincode="",
    )
    assert kr.raw_acronym(row["name"]) == "VDCT"
    result = kr.identity_verification(
        row, "https://www.vdct.in/",
        "VIKAS DISABLED CHARITABLE TRUST operates Vikasa Special School.",
        "owned_site_candidate", fetch_metadata=metadata("VIKAS Disabled Charitable Trust"),
    )
    assert result["status"] == "verified_owned_site"


def test_named_hosted_microsite_verifies_but_opaque_host_stays_manual():
    hosted_row = base_row(
        name="SADHANA", referral_name="Sadhana Raichur", public_name="Sadhana Raichur",
        district="Raichur", registration_reference="", registered_address="", pincode="",
    )
    hosted = kr.identity_verification(
        hosted_row, "https://sadhana.1ngo.in/", "Sadhana Raichur is a registered NGO in Raichur.",
        "controlled_hosted_microsite_candidate", fetch_metadata=metadata("Sadhana Raichur"),
    )
    assert hosted["status"] == "verified_controlled_microsite"

    opaque_row = base_row(
        name="ANIKETHANA TRUST", referral_name="Anikethana PU College", public_name="Anikethana PU College",
        registration_reference="", registered_address="", pincode="",
    )
    opaque = kr.identity_verification(
        opaque_row, "https://60e40b6447e41.site123.me/",
        "ANIKETHANA TRUST operates Anikethana PU College.",
        "controlled_hosted_microsite_candidate", fetch_metadata=metadata("Anikethana PU College"),
    )
    assert opaque["status"] == "plausible_site_identity_review"
    assert opaque["verified"] is False


def test_explicit_project_parent_relationship_is_not_labeled_owned_domain():
    row = base_row(
        name="Arsha Gokulam", referral_name="Arsha Gokulam", project_name="Arsha Gokulam",
        parent_organisation="Arsha Seva Kendram", registration_reference="", registered_address="", pincode="",
    )
    result = kr.identity_verification(
        row, "https://www.arshasevakendram.org/seva/arsha-gokulam/",
        "Arsha Gokulam is a project of Arsha Seva Kendram.",
        "parent_or_project_candidate", fetch_metadata=metadata("Arsha Seva Kendram"),
    )
    assert result["status"] == "verified_parent_or_project_page"
    assert result["ownership"] == "verified_parent_project_relationship"


def test_historical_mismatch_url_is_revalidated_then_search_continues(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(
        name="Shifting Orbits Foundation", referral_name="Shifting Orbits Foundation",
        website="https://www.northsouth.org/", previous_website_status="mismatched_website",
        recovery_mode="regression_test", registration_reference="", registered_address="", pincode="",
    )
    payload = {"organic": [
        {"position": 1, "title": "Shifting Orbits Foundation", "link": "https://shiftingorbits.org/", "snippet": "Official website of Shifting Orbits Foundation"},
    ]}

    def fake_fetch(url, remaining):
        if "northsouth" in url:
            return {
                "ok": True, "text": "NorthSouth supported a changemaker working with Shifting Orbits Foundation.",
                "page_title": "NorthSouth", "og_site_name": "NorthSouth", "footer_text": "© NorthSouth",
                "meta_description": "", "jsonld_org_names": [], "mailto": "", "tel": "",
                "url": url, "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
            }
        return {
            "ok": True, "text": "Shifting Orbits Foundation is a registered foundation.",
            "page_title": "Shifting Orbits Foundation", "og_site_name": "Shifting Orbits Foundation", "footer_text": "© Shifting Orbits Foundation",
            "meta_description": "", "jsonld_org_names": [], "mailto": "", "tel": "",
            "url": url, "status": 200, "fetch_status": "direct_ok", "error": "", "firecrawl_recommended": False,
        }

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        row, "regression_test", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 4}, False, 60,
    )
    assert result["Website"].startswith("https://shiftingorbits.org")
    assert result["Discovery Status"] == "verified_owned_site"
    assert any(e.candidate_url.startswith("https://www.northsouth.org") and e.decision == "rejected_after_fetch_ownership_unproven" for e in audit)


def test_known_official_fetch_failure_wins_over_directory_search_result(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    row = base_row(
        name="Asha Kirana Seva Trust", referral_name="Asha Kirana Seva Trust",
        website="https://ashakiranasevatrust.org/", recovery_mode="regression_test",
        registration_reference="", registered_address="", pincode="",
    )
    payload = {"organic": [
        {"position": 1, "title": "Asha Kirana Seva Trust", "link": "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust", "snippet": "NGO profile"},
    ]}

    def fake_fetch(url, remaining):
        return {
            "ok": False, "text": "", "url": url, "status": "failed", "fetch_status": "direct_failed",
            "error": "SSL failure", "firecrawl_recommended": True,
        }

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, _ = service._process_row(
        row, "regression_test", FakeSerperPool(payload), None,
        {"lock": __import__("threading").RLock(), "logical_queries": 0, "query_cap": 4}, False, 60,
    )
    assert result["Discovery Status"] == "candidate_fetch_pending"
    assert "ashakiranasevatrust.org" in result["Website"]
    assert "helpyourngo" not in result["Website"]


def test_built_in_ownership_self_test_passes():
    result = kr.run_ownership_self_test()
    assert result["passed"] is True
    assert result["failures"] == []
