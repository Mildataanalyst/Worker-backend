import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_v71", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)

RACHIT_REMAINING = {
    "HSK HELPING HAND FOUNDATION": "hsk_helping_hand_foundation",
}

PIYUSH_REMAINING = {
    "AIM for Seva (Chatralaya system)": "aim_for_seva_chatralayam",
    "Aina Trust": "aina_trust",
    "ANNAPOORNA CHARITABLE TRUST": "annapoorna_charitable_trust_balya_home",
    "APPU ORPHAN CHILDREN EDUCATIONAL DEVELOPMENT TRUST": "appu_orphan_children_educational_development_trust",
    "Better World Charitable Trust": "better_world_charitable_trust",
    "CHIRANTHANA": "chiranthana",
    "CHRISTEL HOUSE INDIA": "christel_house_india",
    "GRG GRACE TRUST": "grg_grace_trust",
    "HOPE HOME FOUNDATION": "hope_home_foundation",
}

IPSHITA_FOUR = {
    "Ashakirana Education and Rehabilitation Society (AERS)": "ashakirana_education_and_rehabilitation_society",
    "Kalkeri Sangeet Vidyalaya (KSV)": "kalkeri_sangeet_vidyalaya",
    "PENIEL SOCIAL CHARITABLE TRUST": "peniel_social_charitable_trust",
    "RASHTROTTHANA PARISHAT": "rashtrotthana_parishat",
}

EXPECTED = {**RACHIT_REMAINING, **PIYUSH_REMAINING, **IPSHITA_FOUR}


def test_v71_requested_ngos_have_evidence_only_source_grounded_packs():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION.startswith("v")
    assert len(main.WORKSTREAM_EVIDENCE_PRESETS) >= 88

    forbidden_keys = {
        "score", "rank", "ranking", "recommended_score", "recommended_rank",
        "ceiling", "ceiling_rank", "ceiling_reason", "overall_decision",
    }
    forbidden_phrases = (
        "recommended score", "recommended rank", "overall rank", "why 2",
        "why 3", "why 4", "why 5", "shortlist decision",
    )

    for ngo_name, expected_id in EXPECTED.items():
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


def test_v71_development_environment_requires_demonstrated_variety():
    opportunity_terms = (
        "competition", "public", "leadership", "mentor", "performance", "exposure",
        "community", "project", "sport", "arts", "creative", "educational visit",
        "civic", "responsibility", "club", "travel", "governance", "presentation",
        "gardening", "animal-care", "youth parliament", "exhibition",
    )
    guard_terms = (
        "not counted", "not sufficient", "does not show", "does not demonstrate",
        "no documented", "no recurring", "cannot", "limited", "insufficient",
        "must not", "not proof", "should not", "does not establish",
    )
    baseline_terms = (
        "food", "shelter", "therapy", "healthcare", "counselling", "hostel",
        "residential", "meals", "boarding", "medical care",
    )

    for ngo_name in EXPECTED:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        assert any(term in text for term in opportunity_terms), ngo_name
        assert any(term in text for term in guard_terms), ngo_name
        if any(term in text for term in baseline_terms):
            assert any(
                term in text
                for term in (
                    "not counted", "not automatically", "not sufficient",
                    "must not", "should not", "not treated",
                )
            ), ngo_name


def test_v71_aliases_match_pm_and_programme_variants():
    variants = {
        "HSK Foundation": "hsk_helping_hand_foundation",
        "Chatralaya System": "aim_for_seva_chatralayam",
        "Aina Alternate Care": "aina_trust",
        "Balya Children's Home": "annapoorna_charitable_trust_balya_home",
        "Appu Trust": "appu_orphan_children_educational_development_trust",
        "Live in Better World Trust": "better_world_charitable_trust",
        "Chiranthana Special School": "chiranthana",
        "Christel House Bengaluru": "christel_house_india",
        "GRACE Open School": "grg_grace_trust",
        "HHF Children Home": "hope_home_foundation",
        "AERS": "ashakirana_education_and_rehabilitation_society",
        "KSV Kalkeri": "kalkeri_sangeet_vidyalaya",
        "Aseer Boys Home": "peniel_social_charitable_trust",
        "Tapas and Saadhana": "rashtrotthana_parishat",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id, name
