import json
import time

import main


def _write_saved_run(root, run_id, run_status, strategy="smart", processed=3, total=10, stage="stopped_partial"):
    rd = root / run_id
    rd.mkdir(parents=True)
    (rd / "uploaded_input.csv").write_text("name,darpan_id\nExample NGO,KA/1\n", encoding="utf-8")
    main._recheck_initialize_outputs(rd)
    main._write_recheck_status(
        rd,
        run_id=run_id,
        strategy=strategy,
        run_status=run_status,
        stage=stage,
        processed=processed,
        total=total,
        remaining=max(0, total - processed),
        progress_pct=(processed / total * 100 if total else 0),
        input_filename="ngo_batch.csv",
        updated_at="2026-07-18T10:00:00Z",
    )
    return rd


def test_resumable_dropdown_lists_stopped_cancelled_and_interrupted(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "recheck_threads", {})
    _write_saved_run(tmp_path, "recheck_stopped", "stopped")
    _write_saved_run(tmp_path, "recheck_cancelled", "cancelled", processed=4)
    _write_saved_run(tmp_path, "recheck_interrupted", "running", processed=5, stage="searching")
    _write_saved_run(tmp_path, "recheck_complete", "complete", processed=10, total=10, stage="complete")

    rows = main._recheck_resumable_rows(limit=100)
    by_id = {row["run_id"]: row for row in rows}
    assert set(by_id) == {"recheck_stopped", "recheck_cancelled", "recheck_interrupted"}
    assert by_id["recheck_stopped"]["processed"] == 3
    assert by_id["recheck_stopped"]["total"] == 10
    assert by_id["recheck_stopped"]["input_filename"] == "ngo_batch.csv"
    assert by_id["recheck_interrupted"]["run_status"] == "interrupted"
    assert all(row["can_resume"] for row in rows)


def test_resuming_old_cancelled_run_clears_stale_cancel_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "recheck_threads", {})
    monkeypatch.setattr(main, "recheck_cancel_flags", {})
    run_id = "recheck_old_cancelled"
    _write_saved_run(tmp_path, run_id, "cancelled")
    main._job_create(run_id, "no_website_recheck", tmp_path / run_id, status="cancelled", cancel_requested=True)
    monkeypatch.setattr(main, "_run_smart_recheck_job", lambda run_id, event: None)

    response = main.recheck_resume(run_id)
    payload = json.loads(response.body)
    assert payload["ok"] is True
    thread = main.recheck_threads[run_id]
    thread.join(timeout=2)
    job = main._read_job(run_id)
    assert job["cancel_requested"] is False
    assert job["status"] == "running"
