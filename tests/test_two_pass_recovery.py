import csv
import json

import main


def test_fast_and_deep_profiles_have_expected_depth():
    fast = main._recheck_profile_values("fast")
    deep = main._recheck_profile_values("deep")
    assert fast["max_queries_per_row"] == 2
    assert fast["max_verify_per_row"] == 1
    assert fast["verify_max_pages"] == 2
    assert fast["max_row_seconds"] == 40
    assert fast["rename_recovery_enabled"] is False
    assert deep["max_verify_per_row"] >= fast["max_verify_per_row"]
    assert deep["verify_max_pages"] > fast["verify_max_pages"]
    assert deep["max_row_seconds"] > fast["max_row_seconds"]
    assert deep["rename_recovery_enabled"] is True


def test_deep_review_queue_is_selective():
    clean = {"Website Status": "no_candidate_after_completed_search"}
    assert main._deep_review_reason(clean, [], "fast") == ""

    nominated = [{"Decision": "nominated_not_verified", "Candidate URL": "https://example.org"}]
    assert "plausible candidate" in main._deep_review_reason(clean, nominated, "fast")

    unreachable = {"Website Status": "candidate_site_unreachable"}
    assert "could not be fetched" in main._deep_review_reason(unreachable, [], "fast")

    # A deep pass does not recursively enqueue itself.
    assert main._deep_review_reason(unreachable, [], "deep") == ""


def test_checkpoint_writes_deep_review_input(tmp_path):
    rd = tmp_path / "recheck_source"
    rd.mkdir()
    main._recheck_initialize_outputs(rd)
    result = {
        "NGO Name": "Example Trust",
        "State": "Karnataka",
        "District": "Bengaluru",
        "Darpan ID": "KA/1",
        "Website": "https://example.org",
        "Website Status": "possible_site_manual_review",
        "Email": "hello@example.org",
        "Deep Review Recommended": "yes",
        "Deep Review Reason": "candidate identity evidence was incomplete",
    }
    main._recheck_append_checkpoint(rd, result, [], "fast")
    path = rd / main.RECHECK_OUTPUTS["deep_review_input"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["name"] == "Example Trust"
    assert rows[0]["source_run_id"] == "recheck_source"


def test_start_deep_review_creates_child_run(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "recheck_threads", {})
    monkeypatch.setattr(main, "recheck_cancel_flags", {})
    monkeypatch.setattr(main, "_run_deep_recovery_job", lambda run_id, event: None)

    source = tmp_path / "recheck_source"
    source.mkdir()
    main._recheck_initialize_outputs(source)
    main._write_recheck_status(source, run_id="recheck_source", strategy="fast", run_status="complete", stage="results_ready", processed=1, total=1)
    queue = source / main.RECHECK_OUTPUTS["deep_review_input"]
    with queue.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=main.DEEP_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerow({"name": "Example Trust", "state": "Karnataka", "deep_review_reason": "manual verification"})

    response = main.recheck_start_deep_review("recheck_source")
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["strategy"] == "deep"
    child = tmp_path / payload["run_id"]
    assert (child / "uploaded_input.csv").exists()
    status = json.loads((child / main.RECHECK_OUTPUTS["status"]).read_text(encoding="utf-8"))
    assert status["parent_run_id"] == "recheck_source"
    assert status["strategy"] == "deep"
    main.recheck_threads[payload["run_id"]].join(timeout=2)


def test_old_result_schema_is_migrated_before_resume(tmp_path):
    rd = tmp_path / "recheck_old"
    rd.mkdir()
    old_fields = [field for field in main.RECHECK_FIELDS if field not in {"Recovery Strategy", "Deep Review Recommended", "Deep Review Reason"}]
    result_path = rd / main.RECHECK_OUTPUTS["results"]
    with result_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"NGO Name": "Old Trust", "Website Status": "probable_official_site"})
    main._recheck_ensure_output_schema(rd)
    with result_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert reader.fieldnames == main.RECHECK_FIELDS
    assert rows[0]["NGO Name"] == "Old Trust"
    assert rows[0]["Deep Review Recommended"] == ""


def test_resume_can_switch_legacy_smart_run_to_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "recheck_threads", {})
    monkeypatch.setattr(main, "recheck_cancel_flags", {})
    monkeypatch.setattr(main, "_run_fast_recovery_job", lambda run_id, event: None)

    run_id = "recheck_legacy_smart"
    rd = tmp_path / run_id
    rd.mkdir()
    (rd / "uploaded_input.csv").write_text("name\nExample Trust\n", encoding="utf-8")
    main._recheck_initialize_outputs(rd)
    main._write_recheck_status(rd, run_id=run_id, strategy="smart", run_status="paused", stage="paused", processed=0, total=1)

    response = main.recheck_resume(run_id, strategy_override="fast")
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["strategy"] == "fast"
    main.recheck_threads[run_id].join(timeout=2)
    status = json.loads((rd / main.RECHECK_OUTPUTS["status"]).read_text(encoding="utf-8"))
    assert status["strategy"] == "fast"
    assert status["recovery_profile"]["max_verify_per_row"] == 1


def test_fast_profile_enables_selective_firecrawl_when_configured(monkeypatch):
    monkeypatch.setattr(main, "SMART_RECHECK_USE_FIRECRAWL", True)
    monkeypatch.setattr(main, "FAST_RECHECK_USE_FIRECRAWL", True)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    profile = main._recheck_profile_values("fast")
    assert profile["use_firecrawl"] is True


def test_fast_row_passes_selective_firecrawl_to_candidate_verifier(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "SMART_RECHECK_USE_FIRECRAWL", True)
    monkeypatch.setattr(main, "FAST_RECHECK_USE_FIRECRAWL", True)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(main._recheck_runtime, "strategy", "fast", raising=False)
    calls = []

    def fake_verify(url, row, route, evidence_urls=None, counter=None, firecrawl_recovery=False):
        calls.append(firecrawl_recovery)
        return {"grade": "A", "fetch_status": "fetched"}

    monkeypatch.setattr(main, "_smart_verify_candidate", fake_verify)
    result = main._smart_process_row(
        {"name": "Example Trust", "state": "Karnataka", "website": "https://example.org"},
        tmp_path,
        [],
        {"queries": 0},
    )
    assert result["Website Status"] == "confirmed_official_site"
    assert calls == [True]


def test_cancel_is_distinct_and_preserves_checkpoint_state(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "_jobs")
    main.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "recheck_cancel_test"
    rd = tmp_path / run_id
    rd.mkdir()
    main._recheck_initialize_outputs(rd)
    main._write_recheck_status(rd, run_id=run_id, strategy="fast", run_status="running", stage="fast_recovery", processed=3, total=10)

    class AliveThread:
        def is_alive(self):
            return True

    event = main.threading.Event()
    monkeypatch.setattr(main, "recheck_threads", {run_id: AliveThread()})
    monkeypatch.setattr(main, "recheck_cancel_flags", {run_id: event})
    response = main.recheck_cancel(run_id)
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["run_status"] == "cancel_requested"
    assert main._recheck_cancel_path(rd).exists()
    assert event.is_set()
    status = json.loads((rd / main.RECHECK_OUTPUTS["status"]).read_text(encoding="utf-8"))
    assert status["run_status"] == "cancel_requested"
