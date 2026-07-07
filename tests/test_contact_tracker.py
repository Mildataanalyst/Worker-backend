import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_contact", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def test_final_output_can_send_to_contact_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", tmp_path / "workstream_data.json")
    monkeypatch.setattr(main, "WORKSPACES_DIR", tmp_path / "workspaces")
    main.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    main._write_workstream_payload({
        "pms": {
            "Milan": {
                "tasks": [{
                    "ngo_name": "Tracker Test NGO",
                    "website": "https://example.org",
                    "background": "Runs a residential hostel for children.",
                    "lead_id": "lead_tracker_test",
                    "source_mix": "Human Referral",
                    "contact_number": "9999999999",
                    "referred_by": "Milan",
                    "one_line_understanding": "Runs a residential hostel for children.",
                }],
                "responses": {"0": {"submitted": True, "rank": 5, "reason": "Strong fit.", "submitted_at": "2026-07-05 10:00:00"}},
            }
        }
    })

    res = main.final_send_to_contact_tracker({"region": "Karnataka", "buckets": ["final_shortlist"]})
    assert res.status_code == 200
    rows = main._read_contact_tracker("Karnataka")
    assert len(rows) == 1
    assert rows[0]["ngo_name"] == "Tracker Test NGO"
    assert rows[0]["contact_status"] == "not_started"

    res2 = main.contact_tracker_update({"region": "Karnataka", "tracker_id": rows[0]["tracker_id"], "contact_status": "connected", "meeting_notes": "Call done."})
    assert res2.status_code == 200
    updated = main._read_contact_tracker("Karnataka")[0]
    assert updated["contact_status"] == "connected"
    assert updated["meeting_notes"] == "Call done."

    res3 = main.final_send_to_contact_tracker({"region": "Karnataka", "buckets": ["final_shortlist"]})
    assert res3.status_code == 200
    assert len(main._read_contact_tracker("Karnataka")) == 1
