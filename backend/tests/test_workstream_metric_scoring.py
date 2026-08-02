import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_metric_scoring", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _configure(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", tmp_path / "workstream_data.json")
    monkeypatch.setattr(main, "_undo_snapshot_before", lambda paths: {})
    monkeypatch.setattr(main, "_undo_snapshot_after", lambda *args, **kwargs: None)
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    main._write_workstream_payload(main._default_workstream_payload())


def test_admin_evidence_and_metric_scores_persist_and_export(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    evidence = {
        "child_progression": {
            "text": "Repeated Class 10 completion and college transitions are reported.",
            "links": [{"label": "Annual report", "url": "https://example.org/report"}],
        },
        "learning_model": {
            "text": "Level-based remedial groups and monthly assessments are described.",
            "links": [{"label": "Programme page", "url": "https://example.org/programme"}],
        },
        "development_ecosystem": {
            "text": "Residential care includes nutrition, counselling and career guidance.",
            "links": [{"label": "Support page", "url": "https://example.org/support"}],
        },
    }
    updated = _payload(main.workstream_admin_update({
        "password": "secret",
        "pm": "Milan",
        "scoring_reference_url": "https://example.org/scoring-guide",
        "evidence_task_index": 0,
        "metric_evidence": evidence,
    }))
    assert updated["ok"] is True
    assert updated["data"]["scoring_reference_url"] == "https://example.org/scoring-guide"
    assert updated["data"]["pms"]["Milan"]["tasks"][0]["metric_evidence"]["child_progression"]["links"][0]["label"] == "Annual report"

    reasons = {
        "child_progression": "The organisation reports repeated school completion and transitions into college across multiple cohorts, with named progression examples and scholarship support.",
        "learning_model": "The teaching model uses level-based groups, remedial support, regular assessment and individual mentoring rather than relying on one-off workshops or vague claims.",
        "development_ecosystem": "Children receive sustained residential care, nutrition, counselling, sports exposure, career guidance and scholarship support that continues beyond classroom hours.",
    }
    saved = _payload(main.workstream_submit({
        "pm": "Milan",
        "task_index": 0,
        "decision": "4",
        "rank": 4,
        "rank_label": "Strong fit",
        "reason": "Strong overall fit.",
        "metric_scores": {
            key: {"rank": rank, "reason": reasons[key]}
            for key, rank in {
                "child_progression": 4,
                "learning_model": 5,
                "development_ecosystem": 4,
            }.items()
        },
    }))
    assert saved["ok"] is True
    response = saved["data"]["pms"]["Milan"]["responses"]["0"]
    assert response["metric_scores"]["learning_model"]["rank"] == 5
    assert response["metric_scores"]["development_ecosystem"]["reason"].startswith("Children receive")

    exported = main.workstream_export_csv()
    csv_text = exported.body.decode("utf-8")
    assert "ngo_id" in csv_text
    assert "DFP-NGO-" in csv_text
    assert "child_progression_rank" in csv_text
    assert "learning_model_reason" in csv_text
    assert "development_ecosystem_rank" in csv_text
    assert "The teaching model uses level-based groups" in csv_text


def test_metric_rescore_preserves_legacy_ranking_and_uses_one_exception_override(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    legacy = _payload(main.workstream_submit({
        "pm": "Milan",
        "task_index": 0,
        "decision": "4",
        "rank": 4,
        "rank_label": "Strong fit",
        "reason": "Earlier overall judgement that must remain locked and unchanged.",
    }))
    assert legacy["ok"] is True

    evidence = {
        "child_progression": {
            "text": "65 students were in higher education.\nTen completed higher education that year.",
            "links": [{"label": "Annual report · page 15", "url": "https://example.org/report#page=15"}],
            "ceiling_rank": 4,
            "ceiling_reason": "No complete multi-cohort employment picture is published.",
        },
        "learning_model": {
            "text": "Daily arts training is reported.",
            "links": [],
            "ceiling_rank": 4,
            "ceiling_reason": "The research pack recommends a maximum of 4.",
        },
        "development_ecosystem": {"text": "Residential care is reported.", "links": [], "ceiling_rank": 5},
    }
    admin = _payload(main.workstream_admin_update({
        "password": "secret",
        "pm": "Milan",
        "evidence_task_index": 0,
        "metric_evidence": evidence,
    }))
    assert admin["data"]["pms"]["Milan"]["tasks"][0]["metric_evidence"]["child_progression"]["ceiling_rank"] == 4

    reasons = {
        "child_progression": "The report provides aggregate higher-education numbers, completion evidence and named alumni destinations, but not a complete multi-cohort employment picture across most former students.",
        "learning_model": "The programme embeds intensive daily performing-arts training alongside formal academics, with regular practice, performance and a clearly structured progression pathway for students.",
        "development_ecosystem": "Children receive residential care, meals, healthcare, adult supervision, arts opportunities and continued higher-education support as one connected and sustained environment.",
    }

    blocked = _payload(main.workstream_submit_metrics({
        "pm": "Milan",
        "task_index": 0,
        "metric_scoring_version": "v1.2",
        "metric_scores": {
            "child_progression": {"rank": 4, "reason": reasons["child_progression"]},
            "learning_model": {"rank": 5, "reason": reasons["learning_model"]},
            "development_ecosystem": {"rank": 5, "reason": reasons["development_ecosystem"]},
        },
        "exception_override": {
            "enabled": True,
            "rank": 5,
            "reason": "The three dimensions do not capture the institution's exceptional strategic fit, long-term cultural pathway and unusually strong relevance to the programme's overall purpose.",
        },
    }))
    assert blocked["ok"] is False
    assert "ceiling" in blocked["error"].lower()

    exception_reason = "The three metric scores do not fully capture Kalkeri's exceptional overall strategic fit, the central role of food in its residential model, and the unusually coherent long-term pathway it offers children."
    rescored = _payload(main.workstream_submit_metrics({
        "pm": "Milan",
        "task_index": 0,
        "metric_scoring_version": "v1.2",
        "metric_scores": {
            "child_progression": {"rank": 4, "reason": reasons["child_progression"]},
            "learning_model": {"rank": 4, "reason": reasons["learning_model"]},
            "development_ecosystem": {"rank": 5, "reason": reasons["development_ecosystem"]},
        },
        "exception_override": {"enabled": True, "rank": 5, "reason": exception_reason},
    }))
    assert rescored["ok"] is True
    response = rescored["data"]["pms"]["Milan"]["responses"]["0"]
    assert response["rank"] == 4
    assert response["reason"] == "Earlier overall judgement that must remain locked and unchanged."
    assert response["metric_submitted"] is True
    assert response["metric_scores"]["learning_model"] == {"rank": 4, "reason": reasons["learning_model"]}
    assert response["exception_override"]["enabled"] is True
    assert response["exception_override"]["rank"] == 5
    assert response["exception_override"]["reason"].startswith("The three metric scores")

    exported = main.workstream_export_csv().body.decode("utf-8")
    assert "exception_override_rank" in exported
    assert "exception_override_reason" in exported
    assert "The three metric scores" in exported
    assert "learning_model_override" not in exported

    cleared = _payload(main.workstream_delete_metrics({"pm": "Milan", "task_index": 0}))
    response_after_clear = cleared["data"]["pms"]["Milan"]["responses"]["0"]
    assert response_after_clear["rank"] == 4
    assert response_after_clear["reason"] == "Earlier overall judgement that must remain locked and unchanged."
    assert "metric_scores" not in response_after_clear
    assert "metric_submitted" not in response_after_clear
    assert "exception_override" not in response_after_clear



def test_stale_edit_locks_are_ignored_and_removed(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    data = main._read_workstream_payload()
    data["edit_locks"] = {"all": True, "pms": {"Milan": True}}
    main._atomic_write_text(main.WORKSTREAM_DATA_FILE, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    reasons = {
        "child_progression": "The available evidence gives named and aggregate progression information that is sufficient for a defensible screening score under the current public-evidence protocol.",
        "learning_model": "The available evidence explains the teaching mechanisms, regularity and structure clearly enough to support an independent learning-model judgement by the reviewer.",
        "development_ecosystem": "The available evidence describes the support surrounding children, its continuity and how the different supports work together over time in the programme.",
    }
    saved = _payload(main.workstream_submit_metrics({
        "pm": "Milan",
        "task_index": 0,
        "metric_scores": {
            "child_progression": {"rank": 3, "reason": reasons["child_progression"]},
            "learning_model": {"rank": 3, "reason": reasons["learning_model"]},
            "development_ecosystem": {"rank": 3, "reason": reasons["development_ecosystem"]},
        },
        "exception_override": {"enabled": False, "rank": 3, "reason": ""},
    }))
    assert saved["ok"] is True
    assert "edit_locks" not in saved["data"]

    compatibility = _payload(main.workstream_admin_lock_edits({
        "password": "secret",
        "all_pms": True,
        "locked": True,
    }))
    assert compatibility["ok"] is True
    assert compatibility["locked"] is False
    assert compatibility["removed"] is True
    assert "edit_locks" not in compatibility["data"]


def test_named_ngo_evidence_presets_migrate_without_prefilling_rankings(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    data = main._read_workstream_payload()
    data["pms"]["Milan"]["tasks"] = [
        {
            "ngo_name": "SREE SIDDAGANGA MATH, TUMAKURU",
            "website": "https://siddagangamath.org/siddaganga/home.html",
            "background": "Existing assignment background.",
            "metric_evidence": {
                "child_progression": {
                    "text": "Earlier admin note that must remain available.",
                    "links": [{"label": "Earlier source", "url": "https://example.org/earlier"}],
                    "ceiling_rank": 5,
                    "ceiling_reason": "Earlier ceiling that must be removed.",
                }
            },
        },
        {"ngo_name": "SRI VISHWESHA DHAMA GURUKULAM", "website": "https://www.svdgurukulam.org/programs"},
        {"ngo_name": "TADIMETY RADHAKRISHNA CHARITABLE TRUST", "website": "https://www.trct.org/"},
        {
            "ngo_name": "SREE SIDDAGANGA MATH OLD V65 COPY",
            "metric_evidence_preset_id": "sree_siddaganga_math",
            "metric_evidence_preset_version": "v65-three-ngo-evidence-2026-07-17",
            "metric_evidence": {
                "child_progression": {
                    "text": "Old v65 generated evidence text that should be replaced.",
                    "links": [{"label": "Old generated source", "url": "https://example.org/old-generated"}],
                    "ceiling_rank": 2,
                    "ceiling_reason": "Old generated score guidance.",
                },
                "learning_model": {"text": "Old v65 learning text.", "links": [], "ceiling_rank": 2},
                "development_ecosystem": {"text": "Old v65 ecosystem text.", "links": [], "ceiling_rank": 4},
            },
        },
    ]
    data["pms"]["Milan"]["responses"] = {
        "0": {"rank": 2, "reason": "Existing overall response must remain untouched.", "submitted": True}
    }
    main._write_workstream_payload(data)

    migrated = main._read_workstream_payload()
    tasks = migrated["pms"]["Milan"]["tasks"]

    siddaganga = tasks[0]
    assert siddaganga["metric_evidence_preset_version"] == main.WORKSTREAM_EVIDENCE_PRESETS_VERSION
    assert "Old Boys Association" in siddaganga["metric_evidence"]["child_progression"]["text"]
    assert "Earlier admin note" in siddaganga["metric_evidence"]["child_progression"]["text"]
    assert any(link["url"] == "https://example.org/earlier" for link in siddaganga["metric_evidence"]["child_progression"]["links"])

    vishwesha = tasks[1]
    assert "Aditi and Aneesha" in vishwesha["metric_evidence"]["child_progression"]["text"]
    assert "six-year classical curriculum" in vishwesha["metric_evidence"]["learning_model"]["text"]

    trct = tasks[2]
    assert "more than 8,600" in trct["metric_evidence"]["child_progression"]["text"]

    old_v65 = tasks[3]
    assert "Old v65 generated evidence text" not in old_v65["metric_evidence"]["child_progression"]["text"]
    assert not any(link["url"] == "https://example.org/old-generated" for link in old_v65["metric_evidence"]["child_progression"]["links"])

    for task in tasks:
        for metric_key in main.WORKSTREAM_METRIC_KEYS:
            row = task["metric_evidence"][metric_key]
            assert row["ceiling_rank"] == 0
            assert row["ceiling_reason"] == ""

    assert migrated["pms"]["Milan"]["responses"]["0"]["rank"] == 2
    assert migrated["pms"]["Milan"]["responses"]["0"]["reason"] == "Existing overall response must remain untouched."
    migration = migrated["data_migrations"][main.WORKSTREAM_EVIDENCE_PRESETS_VERSION]
    assert migration["count"] == 4

    second_read = main._read_workstream_payload()
    assert second_read["data_migrations"][main.WORKSTREAM_EVIDENCE_PRESETS_VERSION]["count"] == 4


def test_all_neutral_evidence_packs_are_available_and_have_sources_only():
    expected = {
        "SREE SIDDAGANGA MATH": "sree_siddaganga_math",
        "SRI VISHWESHA DHAMA GURUKULAM": "sri_vishwesha_dhama_gurukulam",
        "TADIMETY RADHAKRISHNA CHARITABLE TRUST": "tadimety_radhakrishna_charitable_trust",
        "Sri Ananddhanamma Charitable Trust and Seva Foundation": "sri_ananddhanamma_trust",
        "Sri Sathya Sai Premaarpitham Foundation": "sri_sathya_sai_premaarpitham_foundation",
        "Sri Durga Foundation": "sri_durga_foundation",
        "Ten Academy / Hub Foundation Charitable Trust": "ten_academy_hub_foundation",
        "Don Bosco Child Labour Mission, Davangere": "don_bosco_child_labour_mission_davangere",
        "Vikasam Seva Foundation": "vikasam_seva_foundation",
        "Vision Life Foundation": "vision_life_foundation",
        "Vivekananda Girijana Kalyana Kendra — VGKK": "vivekananda_girijana_kalyana_kendra",
        "Vonisha Service Foundation": "vonisha_service_foundation",
        "ABAN EDUCATION SOCIETY": "aban_education_society",
        "AIKYA TRUST": "aikya_trust",
        "Ananda Suvarna Rural Development Trust": "ananda_suvarna_rural_development_trust",
        "BELAKOO": "belakoo_trust",
        "CHERYSH TRUST": "cherysh_trust",
        "Chethana Special School": "chethana_special_school",
        "Eka Educational and Charitable Trust": "eka_educational_charitable_trust",
        "HELPING HANDS TOGETHER": "helping_hands_together",
        "Inchara Foundation": "inchara_foundation",
        "Matoshree Ambubai Residential School": "matoshree_ambubai_residential_school",
        "PRACHODANA NGO - Open Shelter Programme": "prachodana_open_shelter",
        "Rebuild India Foundation": "rebuild_india_foundation",
        "SAMARPAN": "samarpan_foundation",
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
    assert len(main.WORKSTREAM_EVIDENCE_PRESETS) >= 88
    for ngo_name, expected_id in expected.items():
        preset_id, preset = main._workstream_find_evidence_preset(ngo_name)
        assert preset_id == expected_id
        assert preset is not None
        for metric_key in main.WORKSTREAM_METRIC_KEYS:
            raw_row = preset["metric_evidence"][metric_key]
            assert set(raw_row) == {"text", "links"}
            assert raw_row["text"].strip()
            assert raw_row["links"]
            assert all(link.get("url", "").startswith(("http://", "https://")) for link in raw_row["links"])

