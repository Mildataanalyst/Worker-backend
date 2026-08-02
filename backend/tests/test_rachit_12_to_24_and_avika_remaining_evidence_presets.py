import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_v70", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)

RACHIT_12_TO_24 = {
    "PRACHODANA NGO - Open Shelter Programme": "prachodana_open_shelter",
    "Rebuild India Foundation": "rebuild_india_foundation",
    "SAMARPAN": "samarpan_foundation",
    "SATGUNA SANGRAHA TRUST": "satguna_sangraha_trust",
    "SHISHU VIHAR": "malleshwaram_shishu_vihar",
    "Sneha Shikshana Samsthe Sullia": "sneha_shikshana_samsthe_sullia",
    "SRI KUNCHITIGARA MAHASAMSTHANA MATTADA CHARITABLE TRUST (R)": "sri_kunchitigara_mahasamsthana_mattada_charitable_trust",
    "Sri Sathya Sai Loka Seva Educational Institutions (SSSLST), Muddenahalli campus": "sri_sathya_sai_loka_seva_educational_institutions_muddenahalli",
    "Srivali Trust": "srivali_trust",
    "StandUp India Foundation": "standup_india_foundation",
    "THE MAQBOOLIYA MEMORIAL EDUCATIONAL WELFARE CHARITABLE TRUST": "maqbooliya_memorial_educational_welfare_charitable_trust",
    "UPKRITI NGO": "upkriti_ngo",
    "VISHVAKANNADA FOUNDATION": "vishvakannada_foundation",
}

AVIKA_REMAINING = {
    "Trust for Rural Upliftment STrategies (TRUST)": "trust_for_rural_upliftment_strategies",
    "VIMUKTI TRUST": "vimukti_trust",
    "Vishvakshema Trust": "vishvakshema_trust",
    "Vyakti Vikas Kendra India": "vyakti_vikas_kendra_india",
}

EXPECTED = {**RACHIT_12_TO_24, **AVIKA_REMAINING}


def test_v70_requested_ngos_have_neutral_source_grounded_evidence_packs():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION.startswith("v")
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
        assert "report" in combined
        assert "annual" in combined
        assert not any(phrase in combined for phrase in forbidden_phrases)

        for row in preset["metric_evidence"].values():
            assert set(row) == {"text", "links"}
            assert row["text"].strip()
            assert row["links"]
            assert not (set(row) & forbidden_keys)
            assert all(
                link.get("label") and link.get("url", "").startswith(("http://", "https://"))
                for link in row["links"]
            )


def test_v70_development_environment_is_strict_and_not_baseline_welfare():
    opportunity_terms = (
        "competition", "public", "leadership", "mentor", "performance", "exposure",
        "community", "project", "sport", "arts", "creative", "educational visit",
        "civic", "responsibility", "club", "travel", "advocacy", "presentation",
    )
    guard_terms = (
        "not counted", "not sufficient", "should not", "does not establish",
        "does not demonstrate", "no recurring", "no reliable evidence", "limited",
        "insufficient", "must not", "not proof",
    )
    baseline_terms = (
        "food", "shelter", "therapy", "healthcare", "counselling", "hostel",
        "residential", "meals", "boarding",
    )

    for ngo_name in EXPECTED:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        assert any(term in text for term in opportunity_terms), ngo_name
        assert any(term in text for term in guard_terms), ngo_name
        if any(term in text for term in baseline_terms):
            assert any(
                term in text
                for term in ("not counted", "not automatically", "not sufficient", "should not", "must not")
            ), ngo_name


def test_v70_aliases_match_common_pm_variants():
    variants = {
        "Sai Gurukul School": "satguna_sangraha_trust",
        "MSV School": "malleshwaram_shishu_vihar",
        "Sphurti Skill Development Centre Sullia": "sneha_shikshana_samsthe_sullia",
        "Kunchitigara Matha": "sri_kunchitigara_mahasamsthana_mattada_charitable_trust",
        "SSSLST Muddenahalli": "sri_sathya_sai_loka_seva_educational_institutions_muddenahalli",
        "Srivali High School": "srivali_trust",
        "SIF Foundation": "standup_india_foundation",
        "Maqbooliya Trust": "maqbooliya_memorial_educational_welfare_charitable_trust",
        "Upkriti Organization": "upkriti_ngo",
        "Vishvakannada Programming": "vishvakannada_foundation",
        "TRUST India Child Labour School": "trust_for_rural_upliftment_strategies",
        "Vimukti Charitable Trust": "vimukti_trust",
        "Shree Parashara Gurukulam": "vishvakshema_trust",
        "Art of Living Free Tribal Schools": "vyakti_vikas_kendra_india",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id, name
