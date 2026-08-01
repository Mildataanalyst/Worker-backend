import asyncio
import csv
import io
import json
import threading
from pathlib import Path

from fastapi import UploadFile

import karnataka_recovery as kr


def _route(service: kr.KarnatakaRecoveryService, suffix: str):
    return next(route for route in service.router.routes if getattr(route, "path", "") == suffix)


def test_service_defaults_support_fifteen_thousand_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("KARNATAKA_AUTO_RESUME_ENABLED", "false")
    monkeypatch.setenv("KARNATAKA_MAX_ROWS_PER_RUN", "20000")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    service = kr.KarnatakaRecoveryService(tmp_path, 100_000_000)
    assert service.max_upload_bytes == 100_000_000
    assert service.max_rows_per_run == 20_000


def test_start_route_accepts_15000_row_csv(monkeypatch, tmp_path):
    monkeypatch.setenv("KARNATAKA_AUTO_RESUME_ENABLED", "false")
    monkeypatch.setenv("KARNATAKA_MAX_ROWS_PER_RUN", "20000")
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    service = kr.KarnatakaRecoveryService(tmp_path, 100_000_000)
    monkeypatch.setattr(service, "_run_job", lambda run_id: None)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["source_record_id", "ngo_id", "name", "district", "state", "recovery_mode_override"])
    for index in range(15_000):
        writer.writerow([f"SRC-{index}", f"DFP-NGO-{index:016X}", f"NGO {index}", "Bengaluru Urban", "Karnataka", "enhanced_search"])
    upload = UploadFile(filename="enhanced_15000.csv", file=io.BytesIO(buffer.getvalue().encode("utf-8")))

    endpoint = _route(service, "/karnataka-recovery/start").endpoint
    response = asyncio.run(endpoint(
        file=upload,
        mode="enhanced_search",
        concurrency=12,
        serper_concurrency=4,
        serper_per_key_concurrency=4,
        serper_credit_budget=59_000,
        query_cap=0,
        preflight=False,
        use_firecrawl=False,
        firecrawl_budget=0,
        firecrawl_proxy="basic",
        run_avika=False,
        row_deadline_seconds=90,
        auto_resume=True,
    ))
    payload = json.loads(response.body)
    assert response.status_code == 200, payload
    assert payload["ok"] is True
    assert payload["total"] == 15_000


def test_interrupted_run_is_eligible_and_auto_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("KARNATAKA_AUTO_RESUME_ENABLED", "false")
    service = kr.KarnatakaRecoveryService(tmp_path, 100_000_000)
    service.auto_resume_enabled = True
    run_id = "karnataka_interrupted_test"
    rd = service._run_dir(run_id)
    rd.mkdir(parents=True)
    (rd / kr.RESULT_FILES["input"]).write_text("source_record_id,name\nSRC-1,Example Trust\n", encoding="utf-8")
    service._write_json(rd / kr.RESULT_FILES["settings"], {
        "mode": "known_url_identity",
        "total_rows": 1,
        "auto_resume": True,
        "auto_resume_attempts": 0,
        "auto_resume_max_attempts": 5,
    })
    service._init_outputs(rd)
    service._write_status(rd, run_id=run_id, run_status="interrupted", stage="interrupted_restart", queries_used=0, can_resume=True)

    called = threading.Event()
    monkeypatch.setattr(service, "_run_job", lambda rid: called.set())
    ok, detail = service._resume_saved_run(run_id, automatic=True, reason="worker_restart")
    assert ok is True, detail
    assert called.wait(2)
    settings = service._read_json(rd / kr.RESULT_FILES["settings"])
    assert settings["auto_resume_attempts"] == 1
    assert settings["resume_count"] == 1


def test_explicit_user_pause_never_auto_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("KARNATAKA_AUTO_RESUME_ENABLED", "false")
    service = kr.KarnatakaRecoveryService(tmp_path, 100_000_000)
    service.auto_resume_enabled = True
    run_id = "karnataka_user_paused"
    rd = service._run_dir(run_id)
    rd.mkdir(parents=True)
    (rd / kr.RESULT_FILES["input"]).write_text("source_record_id,name\nSRC-1,Example Trust\n", encoding="utf-8")
    service._write_json(rd / kr.RESULT_FILES["settings"], {"mode": "known_url_identity", "total_rows": 1, "auto_resume": True})
    service._init_outputs(rd)
    status = service._write_status(rd, run_id=run_id, run_status="paused", stage="user_paused", can_resume=True)
    assert service._auto_resume_eligible(rd, status, service._read_json(rd / kr.RESULT_FILES["settings"])) is False


def test_provider_pause_gets_a_durable_auto_resume_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv("KARNATAKA_AUTO_RESUME_ENABLED", "false")
    service = kr.KarnatakaRecoveryService(tmp_path, 100_000_000)
    service.auto_resume_enabled = True
    run_id = "karnataka_provider_pause"
    rd = service._run_dir(run_id)
    rd.mkdir(parents=True)
    (rd / kr.RESULT_FILES["input"]).write_text("source_record_id,name\nSRC-1,Example Trust\n", encoding="utf-8")
    service._write_json(rd / kr.RESULT_FILES["settings"], {
        "mode": "enhanced_search",
        "total_rows": 1,
        "auto_resume": True,
        "auto_resume_attempts": 0,
        "auto_resume_max_attempts": 5,
    })
    service._init_outputs(rd)
    service._write_status(rd, run_id=run_id, run_status="paused", stage="provider_capacity_unavailable", can_resume=True)
    service._schedule_auto_resume(rd, "serper_preflight_unavailable", delay_seconds=30)
    status = service._status(rd)
    settings = service._read_json(rd / kr.RESULT_FILES["settings"])
    assert status["auto_resume_scheduled"] is True
    assert status["auto_resume_next_in_seconds"] == 30
    assert settings["auto_resume_next_epoch"] > 0
