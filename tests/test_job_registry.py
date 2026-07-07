import importlib.util
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backend_main_jobs", ROOT / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


def test_job_registry_create_update_cancel():
    run_id = f"jobtest_{uuid.uuid4().hex[:8]}"
    main._job_create(run_id, "unit_test", status="queued", stage="queued", total=10, processed=0)
    job = main._read_job(run_id)
    assert job["run_id"] == run_id
    assert job["job_type"] == "unit_test"
    assert job["status"] == "queued"

    main._job_update(run_id, status="running", processed=4)
    assert main._read_job(run_id)["processed"] == 4

    main._job_request_cancel(run_id)
    assert main._job_cancel_requested(run_id) is True
    assert main._should_cancel(run_id) is True


def test_startup_reconcile_marks_orphan_active_job_interrupted():
    run_id = f"jobtest_{uuid.uuid4().hex[:8]}"
    main._job_create(run_id, "unit_test", status="running", stage="searching")
    assert main._read_job(run_id)["status"] == "running"

    main._reconcile_job_registry_startup()
    job = main._read_job(run_id)
    assert job["status"] == "interrupted"
    assert job["stage"] == "interrupted_restart"
