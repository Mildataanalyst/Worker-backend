import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_integrated", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


def _configure_storage(tmp_path, monkeypatch):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", tmp_path / "workstream_data.json")
    monkeypatch.setattr(main, "WORKSPACES_DIR", workspaces)
    monkeypatch.setattr(main, "_undo_snapshot_before", lambda paths: {})
    monkeypatch.setattr(main, "_undo_snapshot_after", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_workspace_log", lambda *args, **kwargs: None)


def test_combined_review_can_send_any_rated_ngo_and_edit_final_text(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    main._write_workstream_payload({
        "pms": {
            "Reviewer A": {
                "tasks": [{
                    "lead_id": "lead-001",
                    "ngo_name": "Example Learning Trust",
                    "website": "https://example.org",
                    "background": "Original profile",
                    "one_line_understanding": "Original understanding",
                    "source_mix": "Human Referral",
                }],
                "responses": {"0": {"submitted": True, "rank": 3, "reason": "Promising but needs context", "submitted_at": "2026-07-13 08:00:00"}},
            }
        }
    })

    compiled = _payload(main.ranking_compiled_review("Karnataka"))
    row = compiled["grouped_by_rating"]["3"][0]
    assert row["ngo_ref"] == "lead-001"
    assert row["selected_for_final"] is False

    selected = _payload(main.ranking_final_selection({
        "region": "Karnataka",
        "ngo_refs": ["lead-001"],
        "final_bucket": "highest_transformation_potential",
    }))
    assert selected["ok"] is True
    assert selected["sent_count"] == 1

    board = _payload(main.ranking_final_board("Karnataka"))
    final_row = next(r for r in board["rows"] if r["ngo_ref"] == "lead-001")
    assert final_row["selected_for_final"] is True
    assert final_row["effective_bucket"] == "highest_transformation_potential"

    edited = _payload(main.ranking_final_override_update({
        "region": "Karnataka",
        "ngo_ref": "lead-001",
        "display_name": "Example Learning Centre",
        "profile_text": "Edited profile text",
        "final_comment": "Edited final view",
        "final_bucket": "great_ngos",
    }))
    assert edited["ok"] is True

    board = _payload(main.ranking_final_board("Karnataka"))
    final_row = next(r for r in board["rows"] if r["ngo_ref"] == "lead-001")
    assert final_row["ngo_name"] == "Example Learning Centre"
    assert final_row["one_line_understanding"] == "Edited profile text"
    assert final_row["final_comment"] == "Edited final view"
    assert final_row["effective_bucket"] == "great_ngos"


def test_human_leads_archive_keeps_old_sent_and_rated_referrals_visible(tmp_path, monkeypatch):
    _configure_storage(tmp_path, monkeypatch)
    main._write_workstream_payload({
        "pms": {
            "Reviewer A": {
                "tasks": [{
                    "lead_id": "human-001",
                    "ngo_name": "Old Human Lead",
                    "website": "https://oldhumanlead.org",
                    "background": "Old referral already reviewed",
                    "source_mix": "Human Referral",
                    "referred_by": "Regional connector",
                }],
                "responses": {"0": {"submitted": True, "rank": 4, "reason": "Strong institution", "submitted_at": "2026-07-12 10:00:00"}},
            }
        }
    })
    main._write_lead_pool("Karnataka", [{
        "lead_id": "human-001",
        "region": "Karnataka",
        "district": "Bengaluru",
        "ngo_name": "Old Human Lead",
        "website": "https://oldhumanlead.org",
        "source_type": "Human Referral",
        "source_mix": "Human Referral",
        "source_tag": "Human Referral",
        "referred_by": "Regional connector",
        "shortlisting_comment": "Uploaded months ago",
        "curation_status": "already_rated",
        "ranking_status": "Already Rated",
        "updated_at": "2026-07-12 10:00:00",
    }])

    archive = _payload(main.workspace_human_leads_archive("Karnataka"))
    assert archive["ok"] is True
    assert archive["count"] == 1
    row = archive["rows"][0]
    assert row["ngo_name"] == "Old Human Lead"
    assert row["archive_status"] == "Rated"
    assert row["pm_rating"] == 4
    assert row["shortlisting_comment"] == "Uploaded months ago"
