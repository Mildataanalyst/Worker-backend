import io

from fastapi.testclient import TestClient

import main


def test_avika_start_is_not_blocked_by_admin_password_middleware(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "configured-but-not-required")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(main.app)
    response = client.post(
        "/repository/start?mode=avika&run_type=avika_filter",
        files={"file": ("avika.csv", io.BytesIO(b"NGO Name,Website\nExample,https://example.org\n"), "text/csv")},
    )
    # The route may reject missing Anthropic configuration, but the global
    # password middleware must never return 401 for Avika.
    assert response.status_code != 401
    assert response.json().get("stage") != "unauthorized"


def test_repository_namespace_is_password_free():
    for path in (
        "/repository/start",
        "/repository/recheck/start",
        "/repository/presence/start",
        "/repository/resume/run_1",
        "/repository/cancel/run_1",
    ):
        assert main._mutation_auth_exempt_path(path) is True
