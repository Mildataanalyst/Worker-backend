import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_v73", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)

IPSHITA_NEW = {
    "Christ International Ministries": "christ_international_ministries",
    "CRY (Child Rights and You)": "cry_child_rights_and_you",
    "Don Bosco Gulbarga (Salesian Province Bangalore - INK)": "don_bosco_gulbarga",
    "GREATER HOPE CHARITABLE TRUST": "greater_hope_charitable_trust",
    "IndiVillage Foundation": "indivillage_foundation",
    "SAMIKSHA FOUNDATION - CARING FOR CHILDREN WITH CANCER TRUST": "samiksha_foundation",
    "SEVALAYA CHARITABLE TRUST": "sevalaya_charitable_trust_bengaluru",
    "Shristi Special Academy": "shristi_special_academy",
    "Sri Sai Spiritual Centre Trust": "sri_sai_spiritual_centre_trust",
    "Vidyaranya": "vidyaranya",
    "Vijetha Residential Special School": "vijetha_residential_special_school",
    "VIKAS DISABLED CHARITABLE TRUST": "vikas_disabled_charitable_trust",
    "VIVEKANANDA VIDYAVARDHAKA SANGHA PUTTUR": "vivekananda_vidyavardhaka_sangha_puttur",
}

IPSHITA_COMPLETE = {
    "AKHILA BHARATHA KURUHINA SETTY (NEKARA) VIDYARTHINI NILAYA CHARITABLE TRUST": "akhila_bharatha_kuruhina_setty_vidyarthini_nilaya_trust",
    "ASHA Charitable Trust": "asha_charitable_trust",
    "Ashakirana Education and Rehabilitation Society (AERS)": "ashakirana_education_and_rehabilitation_society",
    "Ashwini Charitable Trust (ACT)": "ashwini_charitable_trust",
    "BHAVAN BANGALORE PRESS EDUCATIONAL TRUST": "bhavan_bangalore_press_educational_trust",
    **IPSHITA_NEW,
    "Kalkeri Sangeet Vidyalaya (KSV)": "kalkeri_sangeet_vidyalaya",
    "PENIEL SOCIAL CHARITABLE TRUST": "peniel_social_charitable_trust",
    "RASHTROTTHANA PARISHAT": "rashtrotthana_parishat",
    "SREE SIDDAGANGA MATH": "sree_siddaganga_math",
    "SRI VISHWESHA DHAMA GURUKULAM": "sri_vishwesha_dhama_gurukulam",
    "TADIMETY RADHAKRISHNA CHARITABLE TRUST": "tadimety_radhakrishna_charitable_trust",
}


def test_v73_new_ipshita_packs_are_evidence_only_and_source_grounded():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION == (
        "v73-ipshita-complete-evidence-packs-2026-07-17"
    )
    assert len(main.WORKSTREAM_EVIDENCE_PRESETS) == 120
    assert len(IPSHITA_NEW) == 13

    forbidden_keys = {
        "score", "rank", "ranking", "recommended_score", "recommended_rank",
        "ceiling", "ceiling_rank", "ceiling_reason", "overall_decision",
    }
    forbidden_phrases = (
        "recommended score", "recommended rank", "overall rank", "why 2",
        "why 3", "why 4", "why 5", "shortlist decision",
    )

    for ngo_name, expected_id in IPSHITA_NEW.items():
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


def test_v73_ipshita_is_complete_across_all_24_assignment_names():
    assert len(IPSHITA_COMPLETE) == 24
    for ngo_name, expected_id in IPSHITA_COMPLETE.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id, ngo_name
        assert preset is not None, ngo_name


def test_v73_development_environment_uses_demonstrated_opportunities_and_guards():
    opportunity_terms = (
        "competition", "public", "leadership", "mentor", "performance", "exposure",
        "community", "project", "sport", "arts", "creative", "educational visit",
        "civic", "responsibility", "club", "travel", "presentation", "exhibition",
        "workplace", "agency", "showcase",
    )
    guard_terms = (
        "not counted", "not sufficient", "does not show", "does not demonstrate",
        "cannot", "limited", "must not", "not established", "not proof",
        "should not", "not attributed", "not automatically", "do not",
        "no official evidence", "no demonstrated", "insufficient",
    )
    baseline_terms = (
        "food", "shelter", "therapy", "healthcare", "counselling", "hostel",
        "residential", "meals", "boarding", "medical",
    )

    for ngo_name in IPSHITA_NEW:
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


def test_v73_aliases_match_identity_and_programme_variants():
    variants = {
        "CIM Trust": "christ_international_ministries",
        "CRY Open School Programme": "cry_child_rights_and_you",
        "Don Bosco PYaR Gulbarga": "don_bosco_gulbarga",
        "Greater Hope Children's Orphanage": "greater_hope_charitable_trust",
        "Indi Village Foundation": "indivillage_foundation",
        "Caring for Children with Cancer Trust": "samiksha_foundation",
        "Sevalaya India Charitable Trust": "sevalaya_charitable_trust_bengaluru",
        "Srishti Special Academy": "shristi_special_academy",
        "Sri Sai Spiritual Center Trust": "sri_sai_spiritual_centre_trust",
        "Vidyaranya Trust": "vidyaranya",
        "Shri Gururaghavendra Seva Trust": "vijetha_residential_special_school",
        "VDCT": "vikas_disabled_charitable_trust",
        "VVS Puttur": "vivekananda_vidyavardhaka_sangha_puttur",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id, name


def test_v73_identity_scope_cautions_are_explicit():
    checks = {
        "don_bosco_gulbarga": ("bosco bengaluru", "davangere"),
        "sevalaya_charitable_trust_bengaluru": ("chennai", "sevalaya.org"),
        "cry_child_rights_and_you": ("national", "karnataka"),
        "indivillage_foundation": ("multi-location", "karnataka"),
        "vivekananda_vidyavardhaka_sangha_puttur": ("one", "network"),
    }
    for preset_id, terms in checks.items():
        preset = main.WORKSTREAM_EVIDENCE_PRESETS[preset_id]
        combined = "\n".join(
            row["text"] for row in preset["metric_evidence"].values()
        ).lower()
        for term in terms:
            assert term in combined, (preset_id, term)
