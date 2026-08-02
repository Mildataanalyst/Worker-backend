import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_leadpool", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def test_archive_import_marks_existing_ranking_without_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", tmp_path / "workstream_data.json")
    main._write_workstream_payload({
        "pms": {
            "Milan": {
                "tasks": [{"ngo_name": "Ayna Trust", "website": "https://ayna.org", "background": "Already reviewed"}],
                "responses": {"0": {"submitted": True, "rank": 4, "reason": "Strong fit"}},
            }
        }
    })
    rows = [{
        "ngo_name": "AYNA Charitable Trust",
        "district": "Bengaluru",
        "website": "https://ayna.org",
        "source_type": "Archive Import",
        "curation_status": "pending_review",
        "ranking_status": "Not Sent",
    }]
    out, marked = main._annotate_existing_ranking_leads(rows)
    assert marked == 1
    assert out[0]["curation_status"] == "already_rated"
    assert out[0]["ranking_status"] == "Already Rated"
    assert out[0]["existing_ranking_ref"]
    assert "Already exists" in out[0]["curation_comment"]
