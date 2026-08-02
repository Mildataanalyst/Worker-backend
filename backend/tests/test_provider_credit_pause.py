import json
import threading
from pathlib import Path

import pytest

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_provider_pause", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


class FakeResponse:
    def __init__(self, status_code, text="", payload=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_serper_transient_failure_retries_same_funded_account(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "single-funded-key")
    monkeypatch.setenv("SERPER_API_KEYS", "legacy-dead-key,legacy-second-key")
    main._reset_provider_runtime_state("recheck_credit_test")
    calls = []

    def fake_post(*args, **kwargs):
        key = kwargs["headers"]["X-API-KEY"]
        calls.append(key)
        if len(calls) == 1:
            return FakeResponse(500, "temporary provider error")
        return FakeResponse(200, payload={"organic": []})

    monkeypatch.setattr(main.requests, "post", fake_post)
    monkeypatch.setattr(main.time, "sleep", lambda *_args, **_kwargs: None)
    with main._provider_run_context("recheck_credit_test"):
        result = main._serper_post({"q": "test"})

    assert result == {"organic": []}
    assert calls == ["single-funded-key", "single-funded-key"]
    assert main._serper_keys() == ["single-funded-key"]
    assert main._provider_pause_for_run("recheck_credit_test") is None


def test_serper_pauses_when_single_account_is_exhausted(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "single-funded-key")
    monkeypatch.setenv("SERPER_API_KEYS", "legacy-key-must-be-ignored")
    main._reset_provider_runtime_state("recheck_credit_test_all")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["headers"]["X-API-KEY"])
        return FakeResponse(400, "Not enough credits")

    monkeypatch.setattr(main.requests, "post", fake_post)
    with main._provider_run_context("recheck_credit_test_all"):
        with pytest.raises(main.ProviderPauseRequested) as caught:
            main._serper_post({"q": "test"})

    assert caught.value.provider == "serper"
    assert caught.value.reason == "credits_exhausted"
    assert calls == ["single-funded-key"]
    assert main._serper_keys() == ["single-funded-key"]
    details = main._provider_pause_for_run("recheck_credit_test_all")
    assert details["provider"] == "serper"


def test_firecrawl_local_budget_reached_requests_pause(monkeypatch):
    monkeypatch.setattr(main, "SMART_RECHECK_USE_FIRECRAWL", True)
    monkeypatch.setenv("FIRECRAWL_API_KEYS", "fc-one")
    monkeypatch.setattr(main, "SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET", 1)
    monkeypatch.setattr(main, "SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET", 1)
    counter = main._smart_firecrawl_counter_init({"firecrawl_credits": 1, "firecrawl_verify_credits": 1})
    main._clear_provider_pause("recheck_firecrawl_budget")

    with main._provider_run_context("recheck_firecrawl_budget"):
        with pytest.raises(main.ProviderPauseRequested) as caught:
            main._smart_firecrawl_scrape("https://example.org", counter=counter)

    assert caught.value.provider == "firecrawl"
    assert caught.value.reason == "configured_budget_reached"


def test_provider_pause_status_is_resumable_and_checkpoint_safe(tmp_path):
    run_id = "recheck_provider_pause"
    rd = tmp_path / run_id
    rd.mkdir()
    main.RECHECK_OUTPUTS["status"] = "status.json"
    main.RECHECK_OUTPUTS["results"] = "results.csv"
    main.RECHECK_OUTPUTS["audit"] = "audit.csv"
    (rd / "results.csv").write_text("NGO Name\nCompleted NGO\n", encoding="utf-8")
    summary = {"status_counts": {"confirmed_official_site": 1}}
    exc = main.ProviderPauseRequested(
        "anthropic", "credits_exhausted", key_label="...abc123", status_code=402, detail="credit balance too low", run_id=run_id
    )

    main._pause_recheck_for_provider(
        rd,
        run_id,
        exc,
        strategy_name="fast",
        result_rows=[{"NGO Name": "Completed NGO"}],
        total=10,
        active_elapsed=60,
        summary=summary,
        counter={"queries": 2, "firecrawl_credits": 0},
        errors=0,
        row_timeouts=0,
        workers=4,
        last_progress_epoch=1,
    )

    status = json.loads((rd / "status.json").read_text(encoding="utf-8"))
    assert status["run_status"] == "paused"
    assert status["stage"] == "provider_credit_exhausted"
    assert status["paused_provider"] == "anthropic"
    assert status["processed"] == 1
    assert status["remaining"] == 9


def test_avika_filter_promotes_engine_provider_pause(tmp_path, monkeypatch):
    rd = tmp_path / "recheck_avika_pause"
    rd.mkdir()
    avika_input = rd / main.RECHECK_OUTPUTS["avika_input"]
    avika_input.write_text("name,district,state,website\nNGO One,Bengaluru,Karnataka,https://example.org\n", encoding="utf-8")

    class Proc:
        returncode = 75

    def fake_run(*args, **kwargs):
        filter_dir = Path(kwargs["cwd"])
        status_path = filter_dir / main.OUTPUTS["status"]
        status_path.write_text(
            json.dumps({
                "run_status": "paused",
                "stage": "provider_credit_exhausted",
                "paused_provider": "anthropic",
                "pause_reason": "credits_exhausted",
                "paused_key": "...haiku1",
                "provider_status_code": 400,
                "provider_error_detail": "credit balance too low",
            }),
            encoding="utf-8",
        )
        return Proc()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    result = main._run_avika_filter_for_recheck(rd, "fast")

    assert result["filter_status"] == "paused_provider_exhausted"
    assert result["provider_pause"]["provider"] == "anthropic"
    assert result["provider_pause"]["reason"] == "credits_exhausted"


def test_fast_provider_pause_appears_in_resumable_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    run_id = "recheck_fast_provider_pause"
    rd = tmp_path / run_id
    rd.mkdir()
    (rd / "uploaded_input.csv").write_text("name\nNGO One\n", encoding="utf-8")
    (rd / main.RECHECK_OUTPUTS["status"]).write_text(
        json.dumps({
            "run_status": "paused",
            "stage": "provider_credit_exhausted",
            "strategy": "fast",
            "total": 10,
            "processed": 4,
            "remaining": 6,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "_job_live_state", lambda _run_id: "not_running")

    rows = main._recheck_resumable_rows()
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["strategy"] == "fast"
    assert rows[0]["can_resume"] is True
