import json
import os
import time
from pathlib import Path

import main


def _set_paths(monkeypatch, tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    lock = runs / ".repository_active.lock"
    monkeypatch.setattr(main, "RUNS_DIR", runs)
    monkeypatch.setattr(main, "REPO_LOCK_FILE", lock)
    monkeypatch.setattr(main, "REPO_LOCK_STALE_SECONDS", 30)
    main.processes.clear()
    return runs, lock


def test_stale_repository_lock_is_cleared_and_run_is_preserved(monkeypatch, tmp_path):
    runs, lock = _set_paths(monkeypatch, tmp_path)
    run_id = "run_stale_1"
    rd = runs / run_id
    rd.mkdir()
    (rd / "ngo_list.csv").write_text("NGO Name,Website\nExample,https://example.org\n", encoding="utf-8")
    (rd / main.OUTPUTS["status"]).write_text(json.dumps({"run_id": run_id, "run_status": "running", "stage": "ai_batch_running"}), encoding="utf-8")
    lock.write_text(run_id, encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))

    active, locked = main._repo_lock_is_active()

    assert active is False
    assert locked == ""
    assert not lock.exists()
    status = json.loads((rd / main.OUTPUTS["status"]).read_text(encoding="utf-8"))
    assert status["run_status"] == "interrupted"
    assert status["resumable"] is True
    assert (rd / "ngo_list.csv").exists()


def test_fresh_lock_is_not_cleared_during_start_grace(monkeypatch, tmp_path):
    runs, lock = _set_paths(monkeypatch, tmp_path)
    run_id = "run_fresh_1"
    rd = runs / run_id
    rd.mkdir()
    (rd / main.OUTPUTS["status"]).write_text(json.dumps({"run_id": run_id, "run_status": "starting", "stage": "queued"}), encoding="utf-8")
    lock.write_text(run_id, encoding="utf-8")

    active, locked = main._repo_lock_is_active()

    assert active is True
    assert locked == run_id
    assert lock.exists()


def test_startup_reconcile_clears_orphan_immediately(monkeypatch, tmp_path):
    runs, lock = _set_paths(monkeypatch, tmp_path)
    run_id = "run_restart_1"
    rd = runs / run_id
    rd.mkdir()
    (rd / "ngo_list.csv").write_text("NGO Name,Website\nExample,https://example.org\n", encoding="utf-8")
    (rd / main.OUTPUTS["status"]).write_text(json.dumps({"run_id": run_id, "run_status": "running", "stage": "searching"}), encoding="utf-8")
    lock.write_text(run_id, encoding="utf-8")

    main._reconcile_repository_lock_startup()

    assert not lock.exists()
    status = json.loads((rd / main.OUTPUTS["status"]).read_text(encoding="utf-8"))
    assert status["stage"] == "interrupted_restart"
