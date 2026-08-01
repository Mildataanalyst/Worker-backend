import threading

import pytest

import karnataka_recovery as kr


class FakeSerperPool:
    def __init__(self, payload):
        self.payload = payload
        self.provider_attempts = 1

    def search(self, query, timeout=20):
        return self.payload, [{"attempt": 1, "key": "...test", "status": 200, "body": ""}]


def row(name="Example Children Foundation", **changes):
    value = {
        "name": name,
        "referral_name": name,
        "public_name": "",
        "project_name": "",
        "parent_organisation": "",
        "district": "Bengaluru Urban",
        "state": "Karnataka",
        "source_record_id": "KA-GUARD-1",
        "source_fingerprint": "guard",
        "source_row_number": 1,
        "registration_reference": "",
        "registered_address": "",
        "pincode": "",
        "email": "",
        "phone": "",
        "sector_tags": "Children",
        "queue_action": "",
        "failed_query_passes": "",
        "recovery_mode": "enhanced_search",
    }
    value.update(changes)
    return value


def fetch(url, owner, text, *, article=False, publisher="", org_names=None):
    org_names = list(org_names if org_names is not None else [owner])
    return {
        "ok": True,
        "text": text,
        "page_title": owner,
        "meta_description": text,
        "mailto": "",
        "tel": "",
        "metadata": {
            "h1_text": owner,
            "footer_text": f"Copyright © {owner}. All rights reserved.",
            "og_site_name": owner,
            "publisher": publisher,
            "jsonld_org_names": org_names,
            "jsonld_names": org_names,
            "jsonld_types": ["article"] if article else ["organization"],
            "article_metadata": article,
        },
        "url": url,
        "status": 200,
        "fetch_status": "direct_ok",
        "error": "",
        "firecrawl_recommended": False,
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
    ("Garden City Jeevitha Anathashrama", "https://us.trip.com/travel-guide/attraction/bengaluru-urban/garden-city-jeevitha-anathashrama-141739151/"),
    ("Mithra Grameena Vikasa Samsthe", "https://wp.wpi.edu/capetown/projects/p2012/mgv/"),
])
def test_known_carrier_patterns_never_enter_verification(name, url):
    candidate_type = kr.page_type_for_candidate(url, row=row(name))
    assert candidate_type in {
        "directory_or_registry", "article_or_profile", "third_party_mention_candidate",
        "government_academic_or_document_reference", "wrong_entity",
    }


@pytest.mark.parametrize("source,url,owner,body", [
    (
        row("Shifting Orbits Foundation"),
        "https://www.northsouth.org/",
        "NorthSouth",
        "NorthSouth supported a changemaker working with Shifting Orbits Foundation.",
    ),
    (
        row("DIVINE MERCY CHARITABLE TRUST", referral_name="Divine Mercy Charitable Trust"),
        "https://www.divinemercydevotion.net/",
        "Divine Mercy Devotion",
        "Divine Mercy prayers and novena. Divine Mercy Charitable Trust is mentioned in one story.",
    ),
    (
        row("VISION INDIA FOUNDATION", referral_name="Vision India Trust"),
        "https://www.giftofvision.org/25-years-of-sankara-eye-foundation-usa",
        "Sankara Eye Foundation",
        "An anniversary article mentioning Vision India Foundation.",
    ),
])
def test_exact_name_or_mention_does_not_prove_ownership(source, url, owner, body):
    result = kr.identity_verification(
        source,
        url,
        body,
        "owned_site_candidate",
        fetch_meta=fetch(url, owner, body, article="article" in body.lower() or "anniversary" in body.lower(), publisher=owner, org_names=[owner]),
    )
    assert result["verified"] is False
    assert result["status"] not in kr.VERIFIED_STATUSES


def test_official_domain_requires_and_passes_page_self_identity():
    source = row("PRAGATHI CHARITABLE TRUST", referral_name="Pragathi Charitable Trust", district="Bengaluru Urban")
    body = "Welcome to Pragathi Charitable Trust. PRAGATHI CHARITABLE TRUST is a registered charitable trust in Bengaluru Urban."
    result = kr.identity_verification(
        source,
        "https://pragathitrust.org/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://pragathitrust.org/", "Pragathi Charitable Trust", body),
    )
    assert result["status"] == "verified_owned_site"
    assert result["ownership_gate"] == "domain_control_plus_page_self_identity"
    assert kr.verification_safety_failure(source, "https://pragathitrust.org/", result) == ""


def test_public_brand_domain_can_verify_breads_without_accepting_parent_article():
    source = row(
        "BANGALORE RURAL EDUCATIONAL AND DEVELOPMENT SOCIETY",
        referral_name="Don Bosco BREADS",
        public_name="BREADS",
    )
    body = "Welcome to BREADS. BREADS is the Bangalore Rural Educational and Development Society working in Karnataka."
    result = kr.identity_verification(
        source,
        "https://breads.org.in/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://breads.org.in/", "BREADS", body),
    )
    assert result["status"] == "verified_owned_site"

    article = kr.identity_verification(
        source,
        "https://www.indiancatholicmatters.org/acts-of-kindness-by-don-bosco-breads/",
        body,
        "article_or_profile",
        fetch_meta=fetch(
            "https://www.indiancatholicmatters.org/acts-of-kindness-by-don-bosco-breads/",
            "Indian Catholic Matters",
            body,
            article=True,
            publisher="Indian Catholic Matters",
            org_names=["Indian Catholic Matters"],
        ),
    )
    assert article["status"] not in kr.VERIFIED_STATUSES


def test_hosted_and_parent_project_pages_get_precise_statuses():
    hosted_row = row("SADHANA", referral_name="Sadhana Raichur", public_name="Sadhana Raichur", district="Raichur")
    hosted_body = "Welcome to Sadhana Raichur. Sadhana Raichur is a registered NGO working in Raichur."
    hosted = kr.identity_verification(
        hosted_row,
        "https://sadhana.1ngo.in/",
        hosted_body,
        "controlled_hosted_microsite_candidate",
        fetch_meta=fetch("https://sadhana.1ngo.in/", "Sadhana Raichur", hosted_body),
    )
    assert hosted["status"] == "verified_controlled_microsite"

    project_row = row(
        "Arsha Gokulam",
        referral_name="Arsha Gokulam",
        project_name="Arsha Gokulam",
        parent_organisation="Arsha Seva Kendram",
    )
    project_body = "Arsha Gokulam is a project of Arsha Seva Kendram."
    project = kr.identity_verification(
        project_row,
        "https://www.arshasevakendram.org/seva/arsha-gokulam/",
        project_body,
        "parent_or_project_candidate",
        fetch_meta=fetch("https://www.arshasevakendram.org/seva/arsha-gokulam/", "Arsha Seva Kendram", project_body),
    )
    assert project["status"] == "verified_parent_or_project_page"
    assert project["ownership"] == "verified_parent_project_relationship"


def test_historical_mismatch_label_is_not_required_for_rejection(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = row(
        "Shifting Orbits Foundation",
        referral_name="Shifting Orbits Foundation",
        website="https://www.northsouth.org/",
        previous_website_status="",
        recovery_mode="regression_test",
    )
    payload = {"organic": [{
        "position": 1,
        "title": "Shifting Orbits Foundation",
        "link": "https://shiftingorbits.org/",
        "snippet": "Official website of Shifting Orbits Foundation",
    }]}

    def fake_fetch(url, remaining):
        if "northsouth" in url:
            body = "NorthSouth supported a changemaker working with Shifting Orbits Foundation."
            return fetch(url, "NorthSouth", body, publisher="NorthSouth", org_names=["NorthSouth"])
        body = "Welcome to Shifting Orbits Foundation. Shifting Orbits Foundation is a registered foundation."
        return fetch(url, "Shifting Orbits Foundation", body)

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        source,
        "regression_test",
        FakeSerperPool(payload),
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 4},
        False,
        60,
    )
    assert result["Website"].startswith("https://shiftingorbits.org")
    assert result["Discovery Status"] == "verified_owned_site"
    assert any("northsouth" in event.candidate_url and event.decision == "rejected_after_fetch_ownership_unproven" for event in audit)


def test_final_safety_gate_rejects_forged_verified_result():
    source = row("Asha Kirana Seva Trust")
    forged = {
        "verified": True,
        "status": "verified_owned_site",
        "page_type": "owned_organisation_site",
        "ownership": "owned_organisation_site",
        "ownership_evidence": "exact NGO name only",
        "ownership_gate": "domain_control_plus_page_self_identity",
    }
    failure = kr.verification_safety_failure(
        source,
        "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust",
        forged,
    )
    assert failure


def test_zero_query_carrier_only_row_routes_to_enhanced_search(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = row(
        "Aadhar Education and Rural Development Society",
        website="https://www.myngos.in/ngo-details/aadhar-education-and-rural-development-society-in-karnataka",
        recovery_mode="known_url_identity",
    )
    result, _ = service._process_row(
        source,
        "known_url_identity",
        None,
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 0},
        False,
        60,
    )
    assert result["Discovery Status"] == "no_candidate_in_uploaded_row"
    assert result["Retry Required"] == "yes"

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    service._rewrite_csv(run_dir / kr.RESULT_FILES["results"], kr.RESULT_FIELDS, [result])
    service._write_derived_exports(run_dir)
    import csv
    with (run_dir / kr.RESULT_FILES["retry"]).open(encoding="utf-8-sig", newline="") as handle:
        retry = next(csv.DictReader(handle))
    assert retry["recovery_mode_override"] == "enhanced_search"


def test_builtin_ownership_guard_passes_and_is_exposed(tmp_path):
    guard = kr.run_ownership_self_test()
    assert guard["passed"] is True
    assert guard["failures"] == []
    assert guard["cases"] >= 18

    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    route = next(route for route in service.router.routes if getattr(route, "path", "") == "/karnataka-recovery/ownership-self-test")
    response = route.endpoint()
    import json
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["passed"] is True


def test_deep_page_is_not_verified_when_site_root_has_another_owner(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = row(
        "Shifting Orbits Foundation",
        referral_name="Shifting Orbits Foundation",
        recovery_mode="missing_query_only",
        failed_query_passes="legal_name_geo",
    )
    candidate_url = "https://shiftingorbitsstories.org/our-work/shifting-orbits"
    payload = {"organic": [{
        "position": 1,
        "title": "Shifting Orbits Foundation",
        "link": candidate_url,
        "snippet": "Welcome to Shifting Orbits Foundation",
    }]}

    def fake_fetch(url, remaining):
        if url.rstrip("/") == "https://shiftingorbitsstories.org":
            body = "Welcome to Stories Platform. Stories Platform publishes profiles of social organisations."
            return fetch(url, "Stories Platform", body, org_names=["Stories Platform"])
        body = "Welcome to Shifting Orbits Foundation. Shifting Orbits Foundation is a registered foundation."
        return fetch(url, "Shifting Orbits Foundation", body)

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        source,
        "missing_query_only",
        FakeSerperPool(payload),
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 1},
        False,
        60,
    )
    assert result["Discovery Status"] not in kr.VERIFIED_STATUSES
    assert any(event.stage == "site_root_ownership_check" and event.decision == "root_owner_conflict_rejected" for event in audit)


def test_official_deep_page_passes_independent_root_owner_check(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = row(
        "PRAGATHI CHARITABLE TRUST",
        referral_name="Pragathi Charitable Trust",
        district="Bengaluru Urban",
        recovery_mode="missing_query_only",
        failed_query_passes="legal_name_geo",
    )
    candidate_url = "https://pragathitrust.org/programmes/children"
    payload = {"organic": [{
        "position": 1,
        "title": "Pragathi Charitable Trust - Children Programme",
        "link": candidate_url,
        "snippet": "Official programme of Pragathi Charitable Trust",
    }]}

    def fake_fetch(url, remaining):
        body = "Welcome to Pragathi Charitable Trust. PRAGATHI CHARITABLE TRUST is a registered charitable trust in Bengaluru Urban."
        return fetch(url, "Pragathi Charitable Trust", body)

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        source,
        "missing_query_only",
        FakeSerperPool(payload),
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 1},
        False,
        60,
    )
    assert result["Discovery Status"] == "verified_owned_site"
    assert "site root independently identifies" in result["Ownership Evidence"]
    assert any(event.stage == "site_root_ownership_check" and event.decision == "root_owner_confirmed" for event in audit)


def test_parent_project_deep_page_requires_parent_domain_root_confirmation(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    source = row(
        "Arsha Gokulam",
        referral_name="Arsha Gokulam",
        project_name="Arsha Gokulam",
        parent_organisation="Arsha Seva Kendram",
        recovery_mode="missing_query_only",
        failed_query_passes="public_project_parent",
    )
    candidate_url = "https://arshasevakendram.org/seva/arsha-gokulam/"
    payload = {"organic": [{
        "position": 1,
        "title": "Arsha Gokulam",
        "link": candidate_url,
        "snippet": "Arsha Gokulam is a project of Arsha Seva Kendram",
    }]}

    def fake_fetch(url, remaining):
        if url.rstrip("/") == "https://arshasevakendram.org":
            return fetch(url, "Unrelated Publisher", "Welcome to Unrelated Publisher.", org_names=["Unrelated Publisher"])
        body = "Arsha Gokulam is a project of Arsha Seva Kendram."
        return fetch(url, "Arsha Seva Kendram", body)

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        source,
        "missing_query_only",
        FakeSerperPool(payload),
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 1},
        False,
        60,
    )
    assert result["Discovery Status"] not in kr.VERIFIED_STATUSES
    assert any(event.stage == "site_root_ownership_check" and event.decision == "root_owner_conflict_rejected" for event in audit)


def test_known_official_fetch_failure_is_preserved_over_directory_search_result(monkeypatch, tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    official = "https://ashakiranasevatrust.org/"
    source = row(
        "Asha Kirana Seva Trust",
        referral_name="Asha Kirana Seva Trust",
        website=official,
        recovery_mode="regression_test",
    )
    payload = {"organic": [{
        "position": 1,
        "title": "Asha Kirana Seva Trust",
        "link": "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust",
        "snippet": "Asha Kirana Seva Trust NGO profile",
    }]}

    def fake_fetch(url, remaining):
        if "ashakiranasevatrust.org" in url:
            return {
                "ok": False,
                "text": "",
                "url": official,
                "status": "failed",
                "fetch_status": "direct_failed",
                "error": "SSL certificate verification failed",
                "firecrawl_recommended": True,
            }
        raise AssertionError("Directory carrier must never be fetched")

    monkeypatch.setattr(kr, "fetch_direct", fake_fetch)
    result, audit = service._process_row(
        source,
        "regression_test",
        FakeSerperPool(payload),
        None,
        {"lock": threading.RLock(), "logical_queries": 0, "query_cap": 4},
        False,
        60,
    )
    assert result["Discovery Status"] == "candidate_fetch_pending"
    assert "ashakiranasevatrust.org" in result["Website"]
    assert "helpyourngo.com" not in result["Website"]
    assert any(event.decision == "carrier_only_continue" and "helpyourngo.com" in event.candidate_url for event in audit)


def test_checkpoint_final_export_guard_downgrades_forged_verified_carrier(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    service._init_outputs(run_dir)
    source = row("Asha Kirana Seva Trust")
    result = kr.blank_result(source, "known_url_identity")
    result.update({
        "Website": "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust",
        "Discovery Status": "verified_owned_site",
        "Website Status": "verified_owned_site",
        "Page Type": "owned_organisation_site",
        "Ownership Class": "owned_organisation_site",
        "Ownership Evidence": "exact name appears on page",
        "Ownership Gate": "domain_control_plus_page_self_identity",
    })
    service._checkpoint(run_dir, source, "known_url_identity", result, [])
    rows = service._load_results(run_dir)
    assert rows[0]["Discovery Status"] == "plausible_site_identity_review"
    assert rows[0]["Retry Reason"] == "final_export_ownership_guard"
    assert not (run_dir / kr.RESULT_FILES["avika_input"]).exists() or (run_dir / kr.RESULT_FILES["avika_input"]).stat().st_size == 0


def test_checkpoint_requires_site_root_confirmation_for_deep_verified_url(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    service._init_outputs(run_dir)
    source = row("PRAGATHI CHARITABLE TRUST", referral_name="Pragathi Charitable Trust")
    result = kr.blank_result(source, "missing_query_only")
    result.update({
        "Website": "https://pragathitrust.org/programmes/children",
        "Discovery Status": "verified_owned_site",
        "Website Status": "verified_owned_site",
        "Page Type": "owned_organisation_site",
        "Ownership Class": "owned_organisation_site",
        "Ownership Evidence": "domain carries name plus legal form: pragathitrust; page self-identifies",
        "Ownership Gate": "domain_control_plus_page_self_identity",
    })
    service._checkpoint(run_dir, source, "missing_query_only", result, [])
    rows = service._load_results(run_dir)
    assert rows[0]["Discovery Status"] == "plausible_site_identity_review"
    assert "site-root" in rows[0]["Identity Conflicts"]


def test_commercial_same_brand_domain_is_not_auto_verified_for_ngo():
    source = row("PUSHPAM FOUNDATION", district="Bengaluru Urban")
    body = "Pushpam Realty is a real estate developer building residential projects in Bengaluru. Pushpam Foundation supports community initiatives."
    result = kr.identity_verification(
        source,
        "https://pushpamrealty.com/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://pushpamrealty.com/", "Pushpam Realty", body, org_names=["Pushpam Realty"]),
    )
    assert result["status"] not in kr.VERIFIED_STATUSES


def test_foreign_same_name_domain_requires_source_specific_identity():
    source = row("SAHANA FOUNDATION", district="Koppal", state="Karnataka")
    body = "Welcome to Sahana Foundation. Sahana Foundation supports communities in the United Kingdom."
    result = kr.identity_verification(
        source,
        "https://sahanafoundation.org.uk/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://sahanafoundation.org.uk/", "Sahana Foundation", body),
    )
    assert result["status"] not in kr.VERIFIED_STATUSES
    assert "foreign_domain_without_source_specific_identity" in result["risk_flags"]


def test_csr_page_without_supplied_parent_is_not_owned_ngo_site():
    source = row("VATSALYA SEVA FOUNDATION", district="Mysuru")
    body = "Vatsalya Seva Foundation is a CSR initiative of Chittara Media Networks."
    result = kr.identity_verification(
        source,
        "https://chittaramedia.in/vatsalya-seva-foundation/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://chittaramedia.in/vatsalya-seva-foundation/", "Vatsalya Seva Foundation", body, org_names=["Chittara Media Networks"]),
    )
    assert result["status"] not in kr.VERIFIED_STATUSES


def test_generic_similar_ngo_domain_requires_source_specific_match():
    source = row("SNEHA EDUCATION TRUST", district="Bengaluru Urban")
    body = "Welcome to Sneha Charitable Trust. Sneha Charitable Trust works for children in Bengaluru."
    result = kr.identity_verification(
        source,
        "https://snehacharitabletrust.org/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch("https://snehacharitabletrust.org/", "Sneha Charitable Trust", body, org_names=["Sneha Charitable Trust"]),
    )
    assert result["status"] not in kr.VERIFIED_STATUSES


def test_batch_guard_downgrades_multiple_source_records_claiming_same_domain(tmp_path):
    service = kr.KarnatakaRecoveryService(tmp_path, 1_000_000)
    run_dir = tmp_path / "run-domain-collision"
    run_dir.mkdir()
    service._init_outputs(run_dir)
    source_one = row("PUSHPAM FOUNDATION", source_record_id="SRC-P1")
    source_two = row("PUSHPAM TRUST", source_record_id="SRC-P2")
    for source in [source_one, source_two]:
        result = kr.blank_result(source, "missing_query_only")
        result.update({
            "Website": "https://pushpamrealty.com/",
            "Discovery Status": "verified_owned_site",
            "Website Status": "verified_owned_site",
            "Page Type": "owned_organisation_site",
            "Ownership Class": "owned_organisation_site",
            "Ownership Gate": "domain_control_plus_page_self_identity",
            "Ownership Evidence": "domain carries public brand/acronym: PUSHPAM; page self-identifies as PUSHPAM",
        })
        service._append_csv(run_dir / kr.RESULT_FILES["results"], kr.RESULT_FIELDS, [result])
    derived = service._write_derived_exports(run_dir)
    results = service._load_results(run_dir)
    assert derived["safe_verified"] == 0
    assert derived["manual_review"] == 2
    assert {item["Discovery Status"] for item in results} == {"plausible_site_identity_review"}
    assert all("domain_claimed_by_multiple_source_records" in item["Risk Flags"] for item in results)

@pytest.mark.parametrize("url", [
    "https://housing.com/in/buy/projects/page/1-example",
    "https://www.nobroker.in/locality-iq/example-bangalore",
    "https://www.tripadvisor.com/Hotel_Review-example",
    "https://www.99acres.com/example-project-npxid-r1",
    "https://www.mappls.com/place-spas-near-example",
    "https://www.commonfloor.com/example-project",
    "https://www.squareyards.com/bangalore-residential-property/example",
])
def test_property_travel_map_domains_are_carriers_before_fetch(url):
    source = row("EXAMPLE CHARITABLE TRUST")
    assert kr.page_type_for_candidate(url, row=source) == "directory_or_registry"


def test_prefetch_commercial_same_name_result_is_rejected_before_fetch():
    source = row("HEBRON CHARITABLE TRUST", pincode="560076")
    candidate_type = kr.page_type_for_candidate(
        "https://www.hebronproperties.com/",
        "Hebron Properties - Luxury Real Estate Developers",
        "Luxury villas, plots and residential projects in Bangalore 560076.",
        source,
    )
    assert candidate_type == "third_party_mention_candidate"


def test_commercial_domain_not_verified_even_when_address_and_pincode_overlap():
    source = row(
        "PUSHPAM FOUNDATION",
        registered_address="No 191 1st Block Byrasandra Jayanagar East Bengaluru 560011",
        pincode="560011",
    )
    body = (
        "Pushpam Realty is a real estate developer. Address #191 1st Block "
        "Byrasandra Jayanagar East Bengaluru Karnataka 560011. Residential property sales."
    )
    result = kr.identity_verification(
        source,
        "https://pushpamrealty.com/",
        body,
        "owned_site_candidate",
        fetch_meta=fetch(
            "https://pushpamrealty.com/",
            "Pushpam Realty",
            body,
            org_names=["Pushpam Realty"],
        ),
    )
    assert result["status"] not in kr.VERIFIED_STATUSES
