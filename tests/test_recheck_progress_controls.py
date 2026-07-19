import csv
import json
import threading
import time

import main


def test_recheck_eta_and_progress_metrics():
    progress = main._recheck_progress_payload(processed=50, total=100, active_elapsed_sec=600)
    assert progress["remaining"] == 50
    assert progress["progress_pct"] == 50.0
    assert progress["throughput_rows_per_min"] == 5.0
    assert progress["eta_seconds"] == 600
    assert progress["eta_quality"] == "live_estimate"


def test_eta_waits_for_enough_rows():
    progress = main._recheck_progress_payload(processed=4, total=100, active_elapsed_sec=120)
    assert progress["eta_seconds"] is None
    assert progress["eta_quality"] == "calculating"


def test_checkpoint_append_makes_partial_outputs_available(tmp_path):
    main._recheck_initialize_outputs(tmp_path)
    result = main._smart_result(
        {"name": "Example Trust", "district": "Mysuru", "state": "Karnataka", "darpan_id": "KA/1"},
        "https://example.org", "confirmed_official_site", "high", "serper", '"KA/1"', "confirmed", "direct",
        {"grade": "A", "type": "identifier", "matched": "KA/1", "page": "https://example.org/legal"},
        searched="yes", queries_used=1,
    )
    main._recheck_append_checkpoint(tmp_path, result, [])
    with (tmp_path / main.RECHECK_OUTPUTS["results"]).open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["NGO Name"] == "Example Trust"
    assert (tmp_path / main.RECHECK_OUTPUTS["avika_input"]).exists()


def test_pause_then_resume_skips_completed_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "RECHECK_PACE_SEC", 0)
    monkeypatch.setattr(main, "_smart_load_entity_register", lambda rd=None: ({}, ""))
    run_id = "recheck_pause_test"
    rd = tmp_path / run_id
    rd.mkdir(parents=True)
    (rd / "uploaded_input.csv").write_text(
        "name,district,state,darpan_id\nOne NGO,Mysuru,Karnataka,KA/1\nTwo NGO,Mysuru,Karnataka,KA/2\nThree NGO,Mysuru,Karnataka,KA/3\n",
        encoding="utf-8",
    )
    main._recheck_initialize_outputs(rd)
    main._write_recheck_status(
        rd, run_id=run_id, strategy="smart", run_status="starting", stage="queued",
        total=3, processed=0, started_at_epoch=time.time(), active_elapsed_sec=0,
    )
    calls = []

    def fake_process(row, rd_arg, audit_rows, counter):
        calls.append(row["name"])
        counter["queries"] += 1
        counter["serper_queries"] += 1
        if len(calls) == 1:
            main._recheck_pause_path(rd_arg).write_text("pause", encoding="utf-8")
        return main._smart_result(
            row, "", "no_candidate_after_completed_search", "low", "serper", "query", "none", "direct",
            searched="yes", queries_used=1,
        )

    monkeypatch.setattr(main, "_smart_process_row", fake_process)
    main._run_smart_recheck_job(run_id, threading.Event(), strategy_name="smart")
    paused = json.loads(main._recheck_status_path(rd).read_text(encoding="utf-8"))
    assert paused["run_status"] == "paused"
    assert paused["processed"] == 1
    assert calls == ["One NGO"]

    main._recheck_pause_path(rd).unlink(missing_ok=True)
    main._run_smart_recheck_job(run_id, threading.Event(), strategy_name="smart")
    complete = json.loads(main._recheck_status_path(rd).read_text(encoding="utf-8"))
    assert complete["run_status"] == "complete"
    assert complete["processed"] == 3
    assert calls == ["One NGO", "Two NGO", "Three NGO"]
    with (rd / main.RECHECK_OUTPUTS["results"]).open("r", encoding="utf-8-sig", newline="") as f:
        results = list(csv.DictReader(f))
    assert len(results) == 3
