import csv
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_ngo_id_backfill", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_backfill_covers_historical_shortlisting_stores(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    workspaces = runs / "workspaces"
    runs.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    workstream = runs / "workstream_data.json"
    marker = runs / ".ngo_id_backfill_v1.json"

    monkeypatch.setattr(main, "RUNS_DIR", runs)
    monkeypatch.setattr(main, "WORKSPACES_DIR", workspaces)
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", workstream)
    monkeypatch.setattr(main, "NGO_ID_BACKFILL_MARKER", marker)

    workstream.write_text(json.dumps({
        "pms": {
            "Milan": {
                "tasks": [{
                    "ngo_name": "Historical Shortlist NGO",
                    "website": "https://historical.example.org",
                    "lead_id": "lead-historical-1",
                }],
                "responses": {},
            }
        }
    }), encoding="utf-8")

    workspace = workspaces / "karnataka"
    workspace.mkdir(parents=True)
    (workspace / "lead_pool.csv").write_text(
        "lead_id,ngo_name,district,website\n"
        "lead-historical-1,Historical Shortlist NGO,Mysuru,https://historical.example.org\n",
        encoding="utf-8",
    )
    (workspace / "contact_tracker.csv").write_text(
        "tracker_id,ngo_ref,lead_id,ngo_name,district,website\n"
        "tracker-1,lead-historical-1,lead-historical-1,Historical Shortlist NGO,Mysuru,https://historical.example.org\n",
        encoding="utf-8",
    )
    run_dir = runs / "repository-old"
    run_dir.mkdir()
    repository = run_dir / "dfp2_repository_output.csv"
    repository.write_text(
        "lead_id,NGO Name,Website,District\nlead-historical-1,Historical Shortlist NGO,https://historical.example.org,Mysuru\n",
        encoding="utf-8",
    )

    report = main._backfill_all_ngo_ids(force=True)
    assert report["ok"] is True
    assert marker.exists()

    payload = json.loads(workstream.read_text(encoding="utf-8"))
    task_id = payload["pms"]["Milan"]["tasks"][0]["ngo_id"]
    lead_id = _read_csv(workspace / "lead_pool.csv")[0]["ngo_id"]
    tracker_id = _read_csv(workspace / "contact_tracker.csv")[0]["ngo_id"]
    repository_id = _read_csv(repository)[0]["NGO ID"]

    assert task_id.startswith("DFP-NGO-")
    assert task_id == lead_id == tracker_id == repository_id


def test_backfill_never_drops_same_name_source_rows(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    path = runs / "dfp2_repository_output.csv"
    path.write_text(
        "source_record_id,NGO Name,District\n"
        "SRC-1,Same Name Trust,Bengaluru Urban\n"
        "SRC-2,Same Name Trust,Bengaluru Urban\n",
        encoding="utf-8",
    )
    result = main._ensure_csv_ngo_ids(path)
    rows = _read_csv(path)
    assert result["rows"] == 2
    assert len(rows) == 2
    assert rows[0]["NGO ID"] != rows[1]["NGO ID"]


def test_ngo_id_registry_actions_do_not_require_manual_password(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    workspaces = runs / "workspaces"
    workspaces.mkdir(parents=True)
    monkeypatch.setattr(main, "RUNS_DIR", runs)
    monkeypatch.setattr(main, "WORKSPACES_DIR", workspaces)
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", runs / "workstream_data.json")
    monkeypatch.setattr(main, "NGO_ID_BACKFILL_MARKER", runs / ".ngo_id_backfill_v1.json")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    result = main.admin_ngo_id_backfill()
    assert result.status_code == 200
    payload = json.loads(result.body)
    assert payload["ok"] is True

    export = main.admin_ngo_id_export()
    assert export.status_code == 200
    assert "text/csv" in export.media_type
