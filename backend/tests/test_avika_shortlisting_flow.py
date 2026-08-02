import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_avika_shortlisting", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def _payload(response):
    return json.loads(response.body)


def _configure(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    workspaces = runs / "workspaces"
    runs.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    monkeypatch.setattr(main, "RUNS_DIR", runs)
    monkeypatch.setattr(main, "WORKSPACES_DIR", workspaces)
    monkeypatch.setattr(main, "WORKSTREAM_DATA_FILE", runs / "workstream_data.json")
    undo = runs / "undo_redo"
    undo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "UNDO_REDO_DIR", undo)
    monkeypatch.setattr(main, "UNDO_STACK_FILE", undo / "undo_stack.json")
    monkeypatch.setattr(main, "REDO_STACK_FILE", undo / "redo_stack.json")
    return runs, workspaces


def test_avika_import_preserves_batch_provenance_and_summary(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    response = main.workspace_import_leads("Karnataka", {
        "source_type": "Avika Filter",
        "rows": [{
            "ngo_id": "DFP-NGO-1234567890ABCDEF",
            "source_record_id": "KA-DARPAN-42",
            "ngo_name": "Example Children Trust",
            "district": "Mysuru",
            "state": "Karnataka",
            "website": "https://examplechildren.org",
            "batch_id": "avika_run_42",
            "batch_label": "Karnataka Recovery · Stage 1",
            "source_module": "avika_filter",
            "source_run": "run_42",
            "source_run_id": "run_42",
            "avika_decision": "yes",
            "avika_reason_code": "child_fit",
            "avika_summary": "Provides free residential education and daily care to underserved children from rural communities.",
            "avika_confidence": "high",
            "website_match": "yes",
            "fit_status": "Strong fit",
            "curation_status": "pending_review",
        }],
    })
    data = _payload(response)
    assert response.status_code == 200
    assert data["added"] == 1
    row = data["rows"][0]
    assert row["ngo_id"] == "DFP-NGO-1234567890ABCDEF"
    assert row["source_record_id"] == "KA-DARPAN-42"
    assert row["batch_id"] == "avika_run_42"
    assert row["batch_label"] == "Karnataka Recovery · Stage 1"
    assert row["source_module"] == "avika_filter"
    assert row["avika_decision"] == "yes"
    assert row["avika_summary"].startswith("Provides free residential")
    assert row["one_line_understanding"].startswith("Provides free residential")
    assert row["shortlisting_comment"].startswith("Avika YES")


def test_selected_avika_lead_can_be_sent_to_pm_without_password(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    imported = _payload(main.workspace_import_leads("Karnataka", {
        "source_type": "Avika Filter",
        "rows": [{
            "ngo_id": "DFP-NGO-FEDCBA0987654321",
            "source_record_id": "KA-DARPAN-99",
            "ngo_name": "Another Child Foundation",
            "district": "Bengaluru Urban",
            "website": "https://anotherchild.org",
            "batch_id": "avika_run_99",
            "batch_label": "Internet Discovery · Batch 99",
            "avika_decision": "maybe",
            "avika_reason_code": "fees_unclear",
            "avika_summary": "Runs a recurring child education programme, although access and fee information require confirmation.",
            "fit_status": "Needs review",
            "curation_status": "pending_review",
        }],
    }))
    lead_id = imported["rows"][0]["lead_id"]

    curated = main.workspace_curate_leads("Karnataka", {
        "lead_ids": [lead_id],
        "curation_status": "approved_with_comment",
        "actor": "Shortlisting Pool",
    })
    assert curated.status_code == 200

    sent = main.workspace_send_to_ranking("Karnataka", {
        "lead_ids": [lead_id],
        "pms": ["Milan"],
        "distribution": "specific_pm",
    })
    data = _payload(sent)
    assert sent.status_code == 200
    assert data["new_leads"] == 1
    assert data["new_tasks"] == 1

    workstream = json.loads(main.WORKSTREAM_DATA_FILE.read_text(encoding="utf-8"))
    task = next(
        task for task in workstream["pms"]["Milan"]["tasks"]
        if task.get("lead_id") == lead_id
    )
    assert task["ngo_id"] == "DFP-NGO-FEDCBA0987654321"
    assert task["source_record_id"] == "KA-DARPAN-99"
    assert task["source_batch_label"] == "Internet Discovery · Batch 99"
    assert task["avika_decision"] == "maybe"
    assert task["avika_reason_code"] == "fees_unclear"
