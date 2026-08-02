import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_v72", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)

KAMRAN_NEW = {
    "AMATEUR SPORTS DEVELOPMENT FEDERATION": "amateur_sports_development_federation",
    "ARIVU TRUST": "arivu_trust",
    "Building Blocks (Morning Glory pre-primary school)": "building_blocks_morning_glory",
    "CEREBLOOM ACADEMY FOR RURAL EDUCATION AND SCIENCE": "cerebloom_academy",
    "Dream India network": "dream_india_network",
    "Dwarakamai Foundation": "dwarakamai_foundation",
    "HAPPY FEET TRUST": "happy_feet_trust",
    "Kaliyuva Mane (run by Divya Deepa Charitable Trust)": "kaliyuva_mane_divya_deepa",
    "Makkala Jagriti": "makkala_jagriti",
    "People's Movement for Self Reliance (PMSR)": "peoples_movement_for_self_reliance",
    "REACHING HAND": "reaching_hand",
    "SARASWATHI EDUCATION TRUST": "saraswathi_education_trust",
    "SEWACB": "sewacb",
    "SMILE FOUNDATION": "smile_foundation",
    "SNEHASHRAYA FOUNDATION": "snehashraya_foundation",
}

IPSHITA_NEW = {
    "AKHILA BHARATHA KURUHINA SETTY (NEKARA) VIDYARTHINI NILAYA CHARITABLE TRUST": "akhila_bharatha_kuruhina_setty_vidyarthini_nilaya_trust",
    "ASHA Charitable Trust": "asha_charitable_trust",
    "Ashwini Charitable Trust (ACT)": "ashwini_charitable_trust",
    "BHAVAN BANGALORE PRESS EDUCATIONAL TRUST": "bhavan_bangalore_press_educational_trust",
}

KAMRAN_COMPLETE = {
    **KAMRAN_NEW,
    "SRI ANANDDHANAMMA CHARITABLE TRUST AND SEVA FOUNDATION": "sri_ananddhanamma_trust",
    "SRI SATHYA SAI PREMAARPITHAM FOUNDATION": "sri_sathya_sai_premaarpitham_foundation",
    "SRIDURGA FOUNDATION": "sri_durga_foundation",
    "Ten Academy (Hub Foundation Charitable Trust)": "ten_academy_hub_foundation",
    "The Don Bosco Charitable Society": "don_bosco_child_labour_mission_davangere",
    "VIKASAM SEVA FOUNDATION": "vikasam_seva_foundation",
    "VISION LIFE FOUNDATION": "vision_life_foundation",
    "Vivekananda Girijana Kalyana Kendra": "vivekananda_girijana_kalyana_kendra",
    "VONISHA SERVICE FOUNDATION": "vonisha_service_foundation",
}

EXPECTED_NEW = {**KAMRAN_NEW, **IPSHITA_NEW}


def test_v72_requested_ngos_have_evidence_only_source_grounded_packs():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION.startswith("v")
    assert len(main.WORKSTREAM_EVIDENCE_PRESETS) >= 107

    forbidden_keys = {
        "score", "rank", "ranking", "recommended_score", "recommended_rank",
        "ceiling", "ceiling_rank", "ceiling_reason", "overall_decision",
    }
    forbidden_phrases = (
        "recommended score", "recommended rank", "overall rank", "why 2",
        "why 3", "why 4", "why 5", "shortlist decision",
    )

    for ngo_name, expected_id in EXPECTED_NEW.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id, ngo_name
        assert preset is not None
        assert set(preset) == {"aliases", "metric_evidence"}
        assert set(preset["metric_evidence"]) == set(main.WORKSTREAM_METRIC_KEYS)

        combined = "\n".join(
            row["text"] for row in preset["metric_evidence"].values()
        ).lower()
        assert "annual" in combined, ngo_name
        assert "report" in combined, ngo_name
        assert not any(phrase in combined for phrase in forbidden_phrases), ngo_name

        for row in preset["metric_evidence"].values():
            assert set(row) == {"text", "links"}
            assert row["text"].strip()
            assert row["links"]
            assert not (set(row) & forbidden_keys)
            assert all(
                link.get("label") and link.get("url", "").startswith(("http://", "https://"))
                for link in row["links"]
            )


def test_v72_kamran_is_complete_across_all_24_assignment_names():
    assert len(KAMRAN_COMPLETE) == 24
    for ngo_name, expected_id in KAMRAN_COMPLETE.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id, ngo_name
        assert preset is not None, ngo_name


def test_v72_development_environment_uses_opportunities_and_scope_guards():
    opportunity_terms = (
        "competition", "public", "leadership", "mentor", "performance", "exposure",
        "community", "project", "sport", "arts", "creative", "educational visit",
        "civic", "responsibility", "club", "travel", "governance", "presentation",
        "exhibition", "workplace", "practical",
    )
    guard_terms = (
        "not counted", "not sufficient", "does not show", "does not demonstrate",
        "no recurring", "cannot", "limited", "requires verification", "must not",
        "not established", "not proof", "should not", "not attributed", "not automatically",
        "do not", "does not document", "could not",
    )
    baseline_terms = (
        "food", "shelter", "therapy", "healthcare", "counselling", "hostel",
        "residential", "meals", "boarding", "medical care",
    )

    for ngo_name in EXPECTED_NEW:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        assert any(term in text for term in opportunity_terms), ngo_name
        assert any(term in text for term in guard_terms), ngo_name
        if any(term in text for term in baseline_terms):
            assert any(
                term in text for term in (
                    "not counted", "not automatically", "not sufficient", "must not",
                    "should not", "not attributed", "not treated",
                )
            ), ngo_name


def test_v72_aliases_match_pm_and_programme_variants():
    variants = {
        "ASDF India": "amateur_sports_development_federation",
        "Arivu Early Intervention Centre": "arivu_trust",
        "Morning Glory Learning Centre": "building_blocks_morning_glory",
        "CARES Cerebloom": "cerebloom_academy",
        "DIN Dream India Network": "dream_india_network",
        "Happy Feet CBE": "happy_feet_trust",
        "Divya Deepa Charitable Trust": "kaliyuva_mane_divya_deepa",
        "Makkala Jagruthi": "makkala_jagriti",
        "Karunalaya PMSR": "peoples_movement_for_self_reliance",
        "Lighthouse International Academy Reaching Hand": "reaching_hand",
        "SSSSET": "saraswathi_education_trust",
        "SEWAC-B": "sewacb",
        "Mission Education Smile Foundation": "smile_foundation",
        "Nekara Vidyarthini Nilaya": "akhila_bharatha_kuruhina_setty_vidyarthini_nilaya_trust",
        "ASHA Evening Tuition Centres": "asha_charitable_trust",
        "ACT Ashwini Charitable Trust": "ashwini_charitable_trust",
        "Bhavan's Bangalore Press School": "bhavan_bangalore_press_educational_trust",
        "Sridurga Foundation": "sri_durga_foundation",
        "Don Bosco Charitable Society": "don_bosco_child_labour_mission_davangere",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id, name
