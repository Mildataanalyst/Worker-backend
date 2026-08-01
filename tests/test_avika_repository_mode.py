import asyncio
import io
import json
from pathlib import Path

from starlette.datastructures import UploadFile

import main


class FakeProcess:
    pid = 43210

    def poll(self):
        return None


def _csv_bytes(rows: int) -> bytes:
    lines = ["NGO Name,Website,NGO ID,Source Record ID"]
    for index in range(rows):
        lines.append(f"Example NGO {index},https://example{index}.org,DFP-NGO-{index:016X},SRC-{index}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_avika_mode_accepts_more_than_bulk_default_without_serper(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "REPO_LOCK_FILE", tmp_path / ".repository_active.lock")
    monkeypatch.setattr(main, "MAX_ROWS_PER_RUN", 1000)
    monkeypatch.setattr(main, "AVIKA_MAX_ROWS_PER_RUN", 10000)
    monkeypatch.setattr(main, "_active_run_ids", lambda: [])
    monkeypatch.setattr(main, "_acquire_repo_lock", lambda _run_id: (True, _run_id))
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    main.processes.clear()

    upload = UploadFile(filename="avika.csv", file=io.BytesIO(_csv_bytes(1500)))
    response = asyncio.run(main.start_repository(file=upload, mode="avika", run_type="avika_filter"))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["run_type"] == "avika_filter"
    assert payload["total"] == 1500

    run_dir = tmp_path / payload["run_id"]
    status = json.loads((run_dir / "dfp2_status.json").read_text(encoding="utf-8"))
    assert status["mode"] == "avika"
    assert status["total"] == 1500


def test_avika_mode_rejects_rows_above_dedicated_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "REPO_LOCK_FILE", tmp_path / ".repository_active.lock")
    monkeypatch.setattr(main, "AVIKA_MAX_ROWS_PER_RUN", 1000)
    monkeypatch.setattr(main, "_active_run_ids", lambda: [])
    main.processes.clear()

    upload = UploadFile(filename="too_many.csv", file=io.BytesIO(_csv_bytes(1001)))
    response = asyncio.run(main.start_repository(file=upload, mode="avika", run_type="avika_filter"))
    payload = json.loads(response.body)

    assert response.status_code == 400
    assert payload["stage"] == "too_many_rows_avika"
    assert payload["limit"] == 1000
