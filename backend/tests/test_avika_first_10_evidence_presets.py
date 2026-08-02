import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_avika_first_10", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


EXPECTED = {
    "AGASTYA INTERNATIONAL FOUNDATION": "agastya_international_foundation",
    "Angels Orphanage": "angels_orphanage_bengaluru",
    "ANUGRAHA EDUCATIONAL AND SOCIAL TRUST": "anugraha_educational_and_social_trust",
    "BHARATH SEVA SANGH": "bharath_seva_sangh_sampatthu",
    "BOSCO": "bosco_bengaluru",
    "Child Empowerment Foundation India": "child_empowerment_foundation_bal_utsav",
    "Christ Special School": "christ_special_school",
    "FAMIN EDUCATIONAL AND SOCIAL WELFARE TRUST": "famin_educational_and_social_welfare_trust",
    "HAZARI PRASAD FOUNDATION": "hazari_prasad_foundation",
    "INTERNATIONAL HUMAN DEVELOPMENT AND UPLIFTMENT ACADEMY": "international_human_development_and_upliftment_academy",
}


def test_avika_first_10_have_source_grounded_neutral_evidence_packs():
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
        for row in preset["metric_evidence"].values():
            assert set(row) == {"text", "links"}
            assert row["text"].strip()
            assert row["links"]
            assert not (set(row) & forbidden_keys)
            assert all(link.get("label") and link.get("url", "").startswith(("http://", "https://")) for link in row["links"])


def test_development_ecosystem_rows_apply_strict_exclusions():
    for ngo_name in EXPECTED:
        _, preset = main._workstream_find_evidence_preset(ngo_name)
        text = preset["metric_evidence"]["development_ecosystem"]["text"].lower()
        # Every pack either provides concrete opportunity evidence or explicitly says
        # that baseline care / generic activity is not enough.
        has_concrete_opportunity = any(term in text for term in (
            "leadership", "competition", "public", "mentor", "performance",
            "exposure", "child-led", "project", "sport", "arts", "artistic",
        ))
        has_strict_exclusion = any(term in text for term in (
            "not counted", "not sufficient", "not development environment",
            "excluded", "does not demonstrate", "no public evidence",
        ))
        assert has_concrete_opportunity
        assert has_strict_exclusion
