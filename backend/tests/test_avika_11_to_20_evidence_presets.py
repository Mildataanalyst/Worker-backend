import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_avika_11_to_20", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


EXPECTED = {
    "Namma Bhoomi — Concern for Working Children": "namma_bhoomi_concerned_for_working_children",
    "PRASHANTHI BALAMANDIRA TRUST": "prashanthi_balamandira_trust",
    "RIGHT TO PLAY FOUNDATION": "right_to_play_foundation_india",
    "Savera Homes": "savera_homes_basera_childrens_village",
    "SHRI B. D. TATTI MEMORIAL CHARITABLE TRUST": "shri_b_d_tatti_memorial_charitable_trust",
    "SNEHADEEP TRUST FOR THE DISABLED": "snehadeep_trust_for_the_disabled",
    "SPASTICS SOCIETY OF KARNATAKA": "spastics_society_of_karnataka",
    "SRI SURESH GURUJI SEVA TRUST": "sri_suresh_guruji_seva_trust",
    "SUKANKSHA CHARITABLE TRUST": "sukanksha_charitable_trust",
    "SWAPAKSH LEARNING FOUNDATION": "swapaksh_learning_foundation",
}


def test_v68_avika_11_to_20_have_source_grounded_neutral_evidence_packs():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION.startswith("v")
    forbidden_keys = {
        "score", "rank", "ranking", "recommended_score", "recommended_rank",
        "ceiling", "ceiling_rank", "ceiling_reason", "overall_decision",
    }
    for ngo_name, expected_id in EXPECTED.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id
        assert preset is not None
        assert set(preset) == {"aliases", "metric_evidence"}
        assert set(preset["metric_evidence"]) == set(main.WORKSTREAM_METRIC_KEYS)
        combined = "\n".join(row["text"] for row in preset["metric_evidence"].values()).lower()
        assert "annual" in combined and "report" in combined
        for row in preset["metric_evidence"].values():
            assert set(row) == {"text", "links"}
            assert row["text"].strip()
            assert row["links"]
            assert not (set(row) & forbidden_keys)
            assert all(
                link.get("label") and link.get("url", "").startswith(("http://", "https://"))
                for link in row["links"]
            )


def test_v68_development_environment_is_varied_and_strictly_bounded():
    opportunity_terms = (
        "leadership", "civic", "competition", "public", "mentor", "performance",
        "exposure", "child-led", "community", "project", "sport", "arts",
        "artistic", "educational visit", "travel", "advocacy", "responsibility",
        "creative", "customer", "field visit", "workplace",
    )
    exclusion_terms = (
        "not counted", "not sufficient", "does not demonstrate", "no recurring",
        "no demonstrated", "not automatically", "excluded", "generic",
    )
    for ngo_name in EXPECTED:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        assert any(term in text for term in opportunity_terms)
        assert any(term in text for term in exclusion_terms)
        # Baseline welfare should only appear with explicit exclusion/qualification.
        if any(term in text for term in ("food", "shelter", "therapy", "healthcare", "counselling", "residence")):
            assert any(term in text for term in ("not counted", "not automatically", "excluded"))


def test_v68_aliases_match_common_pm_task_variants():
    variants = {
        "Concerned for Working Children - Namma Bhoomi": "namma_bhoomi_concerned_for_working_children",
        "PBMT": "prashanthi_balamandira_trust",
        "Right2Play Foundation": "right_to_play_foundation_india",
        "Basera Children's Village": "savera_homes_basera_childrens_village",
        "BD Tatti Trust": "shri_b_d_tatti_memorial_charitable_trust",
        "Sneha Deep Trust for the Disabled": "snehadeep_trust_for_the_disabled",
        "SSK Bengaluru": "spastics_society_of_karnataka",
        "Sri Suresh Gurukula": "sri_suresh_guruji_seva_trust",
        "Sukanksha Madilu": "sukanksha_charitable_trust",
        "Swapaksh Foundation": "swapaksh_learning_foundation",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id
