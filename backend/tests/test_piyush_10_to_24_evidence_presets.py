import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_piyush_10_to_24", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)

EXPECTED = {
    "JSS Karnataka Open School (JSS KOS)": "jss_karnataka_open_school",
    "NELE FOUNDATION": "nele_foundation",
    "Ramakrishna Mission Balakashrama": "ramakrishna_mission_balakashrama",
    "Samarthanam Trust for the Disabled": "samarthanam_trust_for_the_disabled",
    "Seva Bharathi": "seva_bharathi_mangalore",
    "SHRI LAXMISEN EDUCATION SOCIETY RAIBAG": "shri_laxmisen_education_society_raibag",
    "SportzVillage Foundation": "sportzvillage_foundation",
    "Sri Chayadevi Anathashrama Trust (SCAT)": "sri_chayadevi_anathashrama_trust",
    "SRI TAKSHASHILA GURUKUL": "sri_takshashila_gurukul",
    "SRI VENKATAPPA SHANTHAMMA EDUCATIONAL TRUST": "sri_venkatappa_shanthamma_educational_trust",
    "Sundar Bharat Foundation": "sundar_bharat_foundation",
    "SWASYA FOUNDATION": "swasya_foundation",
    "THE CAPUCHIN KRISHIK SEVA KENDRA": "capuchin_krishik_seva_kendra_daya",
    "VIDYOPAASANA EDUCATION TRUST": "vidyopaasana_education_trust",
    "Vivekananda Gurukulam (unit of Ramakrishna Yogashrama)": "vivekananda_gurukulam_ramakrishna_yogashrama",
}


def test_v69_piyush_10_to_24_have_neutral_source_grounded_packs():
    assert main.WORKSTREAM_EVIDENCE_PRESETS_VERSION.startswith("v")
    forbidden = {
        "score", "rank", "ranking", "recommended_score", "recommended_rank",
        "ceiling", "ceiling_rank", "ceiling_reason", "overall_decision",
    }
    for ngo_name, expected_id in EXPECTED.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id
        assert preset is not None
        assert set(preset) == {"aliases", "metric_evidence"}
        assert set(preset["metric_evidence"]) == set(main.WORKSTREAM_METRIC_KEYS)
        report_audit_text = " ".join(
            row["text"].lower() for row in preset["metric_evidence"].values()
        )
        assert "report" in report_audit_text
        assert any(term in report_audit_text for term in ("annual", "biennial"))
        for row in preset["metric_evidence"].values():
            assert set(row) == {"text", "links"}
            assert row["text"].strip()
            assert row["links"]
            assert not (set(row) & forbidden)
            assert all(
                link.get("label") and link.get("url", "").startswith(("http://", "https://"))
                for link in row["links"]
            )


def test_v69_development_environment_is_varied_or_explicitly_absent():
    opportunity_terms = (
        "competition", "public", "leadership", "mentor", "performance", "exposure",
        "community", "project", "sport", "arts", "creative", "educational visit",
        "civic", "responsibility", "customer", "club", "travel", "workplace",
    )
    guard_terms = (
        "not counted", "not sufficient", "not automatically", "does not demonstrate",
        "no recurring", "no varied", "no demonstrated", "must not", "limited",
    )
    for ngo_name in EXPECTED:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        assert any(term in text for term in opportunity_terms)
        assert any(term in text for term in guard_terms)
        if any(term in text for term in ("food", "shelter", "therapy", "healthcare", "counselling", "hostel", "residential")):
            assert any(term in text for term in ("not counted", "not automatically", "not sufficient", "must not"))


def test_v69_aliases_match_pm_variants():
    variants = {
        "JSS KOS": "jss_karnataka_open_school",
        "Nele Homes": "nele_foundation",
        "Balakashrama Mangalore": "ramakrishna_mission_balakashrama",
        "Samarthanam": "samarthanam_trust_for_the_disabled",
        "Seva Bharathi Mangaluru": "seva_bharathi_mangalore",
        "SMREMS Raibag": "shri_laxmisen_education_society_raibag",
        "Sportz Village": "sportzvillage_foundation",
        "SCAT Mysore": "sri_chayadevi_anathashrama_trust",
        "Takshashila Gurukul": "sri_takshashila_gurukul",
        "VSET Foundation": "sri_venkatappa_shanthamma_educational_trust",
        "SundarBharat Foundation": "sundar_bharat_foundation",
        "Swasya": "swasya_foundation",
        "DAYA Special School": "capuchin_krishik_seva_kendra_daya",
        "Vidyopasana": "vidyopaasana_education_trust",
        "Ramakrishna Yogashrama": "vivekananda_gurukulam_ramakrishna_yogashrama",
    }
    for name, expected_id in variants.items():
        preset_id, _ = main._workstream_find_evidence_preset(name)
        assert preset_id == expected_id
