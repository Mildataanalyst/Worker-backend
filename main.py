import csv
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import threading
import re
import ipaddress
import io
import socket
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests

def _make_soup(html: str):
    # Lazy import: BeautifulSoup is only needed when fetching/scoring pages.
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")
anthropic = None
_ANTHROPIC_IMPORT_FAILED = False

def _get_anthropic():
    """Lazy import Anthropic only when an AI route actually needs it.

    Keeping this out of process startup helps the 512MB Render instance stay alive
    for normal lead-pool / PM-review UI actions.
    """
    global anthropic, _ANTHROPIC_IMPORT_FAILED
    if anthropic is not None:
        return anthropic
    if _ANTHROPIC_IMPORT_FAILED:
        return None
    try:
        import anthropic as _anthropic
        anthropic = _anthropic
        return anthropic
    except Exception:
        _ANTHROPIC_IMPORT_FAILED = True
        return None

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

APP_NAME = "DFP 2.0 Backend"
RUNS_DIR = Path(os.environ.get("RUNS_DIR", "runs")).resolve()
ENGINE_FILE = Path(__file__).resolve().parent / "engine" / "dfp2_engine_safe_v5_live_status.py"
MAX_ROWS_PER_RUN = int(os.environ.get("MAX_ROWS_PER_RUN", "1000"))
RAPID_ROWS_LIMIT = int(os.environ.get("RAPID_ROWS_LIMIT", "20"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "8000000"))  # 25 MB default; enough for 10,000 NGO rows
CSV_NAME_HEADERS = {"name", "ngo_name", "ngo name", "organisation", "organization"}

OUTPUTS = {
    "repository": "dfp2_repository_output.csv",
    "donor-lite": "dfp2_donor_leads_lite.csv",
    "audit": "dfp2_run_audit.csv",
    "errors": "dfp2_errors.log",
    "rejected": "dfp2_rejected_audit.csv",
    "status": "dfp2_status.json",
    "duplicates": "dfp2_duplicate_candidates.csv",
}

STORY_OUTPUTS = {
    "stories": "dfp2_story_output.csv",
    "story_csv": "dfp2_story_output.csv",
    "audit": "dfp2_story_audit.csv",
    "rejected": "dfp2_story_rejected.csv",
    "errors": "dfp2_story_errors.log",
    "status": "dfp2_story_status.json",
    "candidates": "story_state_candidates.jsonl",
    "raw_candidates": "story_state_raw_candidates.jsonl",
    "queries": "story_state_queries.json",
}

RECHECK_OUTPUTS = {
    "results": "dfp2_no_website_recheck.csv",
    "audit": "dfp2_no_website_recheck_audit.csv",
    "avika_input": "dfp2_recovered_websites_for_avika_filter.csv",
    "firecrawl_input": "dfp2_firecrawl_recovery_input.csv",
    "errors": "dfp2_no_website_recheck_errors.log",
    "status": "dfp2_no_website_recheck_status.json",
    "summary": "dfp2_no_website_recheck_summary.json",
    "skipped": "dfp2_no_website_recheck_skipped_input.csv",
}

PRESENCE_OUTPUTS = {
    "results": "dfp2_ngo_presence_check.csv",
    "audit": "dfp2_ngo_presence_check_audit.csv",
    "errors": "dfp2_ngo_presence_check_errors.log",
    "status": "dfp2_ngo_presence_check_status.json",
    "summary": "dfp2_ngo_presence_check_summary.json",
}

RUNS_DIR.mkdir(parents=True, exist_ok=True)
processes: dict[str, subprocess.Popen] = {}
REPO_LOCK_FILE = RUNS_DIR / '.repository_active.lock'
GLOBAL_SCAN_HISTORY = RUNS_DIR / 'global_scan_history.csv'
DASHBOARD_DATA_FILE = RUNS_DIR / 'dashboard_data.json'
story_threads: dict[str, threading.Thread] = {}
story_cancel_flags: dict[str, threading.Event] = {}
recheck_threads: dict[str, threading.Thread] = {}
recheck_cancel_flags: dict[str, threading.Event] = {}
presence_threads: dict[str, threading.Thread] = {}
presence_cancel_flags: dict[str, threading.Event] = {}
deep_enrichment_runtime = None

app = FastAPI(title=APP_NAME)

# -----------------------------------------------------------------------------
# Serper key management
# -----------------------------------------------------------------------------
# Supports either:
#   SERPER_API_KEY=single_key
# or:
#   SERPER_API_KEYS=key1,key2,key3
# Default behavior is failover: use the current key until Serper says it is
# exhausted/limited/unauthorized, then automatically move to the next key.
# Use this only for legitimately owned/team API keys or paid project keys.
_SERPER_LOCK = threading.Lock()
_SERPER_KEY_INDEX = 0
_SERPER_DISABLED_KEYS: set[str] = set()

def _serper_keys() -> list[str]:
    raw_multi = os.environ.get("SERPER_API_KEYS", "") or ""
    raw_single = os.environ.get("SERPER_API_KEY", "") or ""
    parts = re.split(r"[,\n\s]+", raw_multi.strip()) if raw_multi.strip() else []
    if raw_single.strip():
        parts.append(raw_single.strip())
    keys: list[str] = []
    seen: set[str] = set()
    for k in parts:
        k = (k or "").strip()
        if k and k not in seen:
            keys.append(k)
            seen.add(k)
    return keys

def _has_serper_keys() -> bool:
    return bool(_serper_keys())

def _mask_key(key: str) -> str:
    key = key or ""
    return ("..." + key[-6:]) if len(key) > 6 else "..."

def _serper_is_key_error(status_code: int, body: str) -> bool:
    low = (body or "").lower()
    if status_code in {401, 402, 403, 429}:
        return True
    return any(x in low for x in ["quota", "credit", "credits", "limit exceeded", "insufficient", "billing", "payment", "exhausted", "unauthorized", "api key"])

def _next_live_serper_key() -> str | None:
    keys = _serper_keys()
    if not keys:
        return None
    for _ in range(len(keys)):
        k = keys[_SERPER_KEY_INDEX % len(keys)]
        if k not in _SERPER_DISABLED_KEYS:
            return k
    return None

def _disable_current_serper_key(key: str) -> None:
    global _SERPER_KEY_INDEX
    with _SERPER_LOCK:
        _SERPER_DISABLED_KEYS.add(key)
        keys = _serper_keys()
        if keys:
            try:
                idx = keys.index(key)
                if idx == (_SERPER_KEY_INDEX % len(keys)):
                    _SERPER_KEY_INDEX = (idx + 1) % len(keys)
            except ValueError:
                _SERPER_KEY_INDEX = (_SERPER_KEY_INDEX + 1) % len(keys)

def _serper_post(payload: dict, timeout: int = 25) -> dict:
    """POST to Serper with automatic multi-key failover.

    One search still equals one Serper query/credit. This only prevents a run
    from dying when one legitimate key/account is exhausted.
    """
    global _SERPER_KEY_INDEX
    if not _has_serper_keys():
        raise RuntimeError("SERPER_API_KEY or SERPER_API_KEYS must be set")
    errors: list[str] = []
    # Try each configured key at most once for this search.
    for _ in range(len(_serper_keys())):
        with _SERPER_LOCK:
            key = _next_live_serper_key()
        if not key:
            break
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as e:
            errors.append(f"{_mask_key(key)} request error: {e}")
            raise
        if r.status_code == 200:
            return r.json()
        body = r.text[:240]
        errors.append(f"{_mask_key(key)} status {r.status_code}: {body}")
        if _serper_is_key_error(r.status_code, body):
            _disable_current_serper_key(key)
            continue
        raise RuntimeError(f"Serper failed {r.status_code}: {body}")
    raise RuntimeError("All configured Serper keys failed or appear exhausted: " + " | ".join(errors[-4:]))

# CORS: default to local development only. In production, set FRONTEND_ORIGIN
# or FRONTEND_ORIGINS explicitly, e.g. https://your-vercel-app.vercel.app.
def _cors_origins() -> list[str]:
    raw = os.environ.get("FRONTEND_ORIGINS") or os.environ.get("FRONTEND_ORIGIN") or ""
    if not raw.strip():
        if os.environ.get("DFP2_PRODUCTION", "").lower() in {"1", "true", "yes"}:
            raise RuntimeError("FRONTEND_ORIGIN(S) must be set when DFP2_PRODUCTION=true")
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    origins = [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]
    if "*" in origins and os.environ.get("DFP2_ALLOW_WILDCARD_CORS", "").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("Wildcard CORS is disabled. Set explicit FRONTEND_ORIGIN(S), or DFP2_ALLOW_WILDCARD_CORS=true for local/demo only.")
    return origins or ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def all_exception_handler(request, exc):
    # Critical: never return plain text errors to frontend. Full internals should
    # stay in server logs. Set DFP2_DEBUG_ERRORS=true only for local debugging.
    request_id = uuid.uuid4().hex[:10]
    try:
        print(f"[api_exception:{request_id}] {repr(exc)}", file=sys.stderr)
    except Exception:
        pass
    if os.environ.get("DFP2_DEBUG_ERRORS", "").lower() in {"1", "true", "yes"}:
        public_error = str(exc)[:500]
    else:
        public_error = f"Internal server error. Reference: {request_id}"
    return JSONResponse(
        status_code=500,
        content={"ok": False, "stage": "api_exception", "error": public_error, "request_id": request_id},
    )


def _admin_secret() -> str:
    # Global mutation middleware uses a dedicated token only.
    # ADMIN_PASSWORD remains for explicit admin flows that read a password from the request body.
    return (os.environ.get("DFP2_ADMIN_TOKEN") or "").strip()

def _is_mutating_method(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


def _service_role() -> str:
    return (os.environ.get("DFP2_SERVICE_ROLE") or "search").strip().lower()

def _role_allows_path(role: str, path: str) -> bool:
    if path in {"/", "/health"} or path.startswith("/health"):
        return True
    if role in {"", "full", "all"}:
        return True
    if role == "core":
        blocked = ("/repository", "/story", "/discovery")
        return not path.startswith(blocked)
    if role == "search":
        allowed = ("/repository", "/enrichment", "/jobs")
        return path.startswith(allowed)
    if role in {"story", "ai", "story_ai"}:
        allowed = ("/story", "/discovery", "/jobs")
        return path.startswith(allowed)
    return True

@app.middleware("http")
async def service_role_middleware(request, call_next):
    role = _service_role()
    path = request.url.path or "/"
    if not _role_allows_path(role, path):
        return JSONResponse(status_code=404, content={
            "ok": False,
            "stage": "wrong_backend_service",
            "service_role": role,
            "error": f"This endpoint is not served by the {role} backend. Use the correct backend URL for this module."
        })
    return await call_next(request)

@app.middleware("http")
async def mutation_auth_middleware(request, call_next):
    # Optional backend-side guard. When DFP2_ADMIN_TOKEN or ADMIN_PASSWORD is set,
    # every mutating route must carry the matching token. This is still not a
    # replacement for real SSO/auth at the network boundary, but it prevents the
    # backend from being completely open if exposed.
    if _is_mutating_method(request.method):
        secret = _admin_secret()
        require = bool(secret) or os.environ.get("DFP2_REQUIRE_MUTATION_AUTH", "").lower() in {"1", "true", "yes"}
        if require:
            if not secret:
                return JSONResponse(status_code=503, content={"ok": False, "stage": "auth_misconfigured", "error": "DFP2_REQUIRE_MUTATION_AUTH is enabled but DFP2_ADMIN_TOKEN is not set"})
            supplied = (request.headers.get("x-dfp2-admin-token") or "").strip()
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                supplied = supplied or auth.split(" ", 1)[1].strip()
            if supplied != secret:
                return JSONResponse(status_code=401, content={"ok": False, "stage": "unauthorized", "error": "Admin authorization required"})
    return await call_next(request)



# -----------------------------------------------------------------------------
# V56 hard CORS guard
# -----------------------------------------------------------------------------
# Why this exists:
# Starlette/FastAPI CORS middleware can miss responses returned early by custom
# middleware (for example auth/service-role 401/404 responses). Browsers then
# report those as CORS failures instead of showing the real response. This guard
# is intentionally added after the other middleware definitions so it runs as the
# outermost middleware and stamps CORS headers on every response, including early
# auth failures and OPTIONS preflights.
def _cors_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    origin_clean = origin.strip().rstrip("/")
    try:
        origins = _cors_origins()
    except Exception:
        origins = []
    origins_clean = [str(o).strip().rstrip("/") for o in origins if str(o).strip()]
    return "*" in origins_clean or origin_clean in origins_clean


def _with_force_cors_headers(response: Response, request) -> Response:
    origin = request.headers.get("origin")
    if _cors_origin_allowed(origin):
        origin_clean = origin.strip().rstrip("/")
        response.headers["Access-Control-Allow-Origin"] = origin_clean
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        requested_headers = request.headers.get("access-control-request-headers")
        response.headers["Access-Control-Allow-Headers"] = requested_headers or "authorization,content-type,x-dfp2-admin-token"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


@app.middleware("http")
async def force_cors_headers_middleware(request, call_next):
    if request.method.upper() == "OPTIONS":
        return _with_force_cors_headers(Response(status_code=200), request)
    response = await call_next(request)
    return _with_force_cors_headers(response, request)

def _json(ok: bool, status_code: int = 200, **kwargs):
    payload = {"ok": ok, **kwargs}
    return JSONResponse(status_code=status_code, content=payload)


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def _safe_csv_cell(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    # Spreadsheet apps may ignore/trim leading whitespace before evaluating formulas.
    # Prefix the original value when its left-trimmed form starts with a formula sigil.
    stripped = text.lstrip(" \t\r\n")
    if stripped and stripped[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _safe_csv_row(row: dict) -> dict:
    return {k: _safe_csv_cell(v) for k, v in (row or {}).items()}


def _is_private_or_local_host(hostname: str) -> bool:
    if not hostname:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return True
    return False


def _validate_public_http_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    if not parsed.hostname or _is_private_or_local_host(parsed.hostname):
        raise ValueError("URL host is not public")
    if parsed.port and parsed.port not in {80, 443}:
        raise ValueError("Only standard HTTP/HTTPS ports are allowed")
    return parsed.geturl()


def _safe_fetch_text(url: str, *, headers: dict | None = None, timeout: int = 10, max_bytes: int = 1_500_000, max_redirects: int = 4) -> tuple[str, str]:
    current = _validate_public_http_url(url)
    headers = headers or {"User-Agent": "Mozilla/5.0 DFP2/1.0"}
    for _ in range(max_redirects + 1):
        resp = requests.get(current, headers=headers, timeout=timeout, allow_redirects=False, stream=True)
        if 300 <= resp.status_code < 400 and resp.headers.get("Location"):
            current = _validate_public_http_url(urljoin(current, resp.headers["Location"]))
            continue
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype and not any(x in ctype for x in ["text/html", "text/plain", "application/xhtml+xml"]):
            raise ValueError(f"Unsupported content type: {ctype[:80]}")
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Response exceeded safe fetch size limit")
            chunks.append(chunk)
        enc = resp.encoding or "utf-8"
        return current, b"".join(chunks).decode(enc, errors="replace")
    raise ValueError("Too many redirects")


def _run_dir(run_id: str) -> Path:
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "_-" )
    return RUNS_DIR / safe


# -----------------------------------------------------------------------------
# Durable job registry (v42)
# -----------------------------------------------------------------------------
# The in-memory dictionaries above remain the live owner handles for the current
# Python process, but they are no longer the only record of long-running work.
# Each long-running job gets a small JSON record under RUNS_DIR/_jobs so restarts
# can mark orphaned jobs as interrupted and partial outputs remain discoverable.
JOBS_DIR = RUNS_DIR / "_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
_JOB_LOCK = threading.RLock()
_JOB_ACTIVE_STATUSES = {"queued", "starting", "running", "resuming", "pause_requested", "cancelling"}
_JOB_TERMINAL_STATUSES = {"complete", "completed", "done", "finished", "success", "succeeded", "partial", "error", "failed", "fatal_error", "cancelled", "canceled", "interrupted"}


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _job_path(run_id: str) -> Path:
    safe = "".join(ch for ch in str(run_id or "") if ch.isalnum() or ch in "_-")
    return JOBS_DIR / f"{safe}.json"


def _read_job(run_id: str) -> dict:
    path = _job_path(run_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_job_record(data: dict) -> dict:
    run_id = str(data.get("run_id") or "").strip()
    if not run_id:
        return data
    data = dict(data)
    data.setdefault("created_at", _utc_now_iso())
    data["updated_at"] = _utc_now_iso()
    _atomic_write_text(_job_path(run_id), json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _job_create(run_id: str, job_type: str, rd: Path | None = None, **payload) -> dict:
    with _JOB_LOCK:
        current = _read_job(run_id)
        created_at = current.get("created_at") or _utc_now_iso()
        base = {
            "run_id": run_id,
            "job_type": job_type,
            "status": payload.pop("status", payload.get("run_status", "queued")),
            "stage": payload.pop("stage", "queued"),
            "run_dir": str((rd or _run_dir(run_id)).resolve()),
            "owner_pid": os.getpid(),
            "cancel_requested": bool(current.get("cancel_requested", False)),
            "created_at": created_at,
        }
        base.update(current)
        base.update(payload)
        base["run_id"] = run_id
        base["job_type"] = job_type
        base["run_dir"] = str((rd or _run_dir(run_id)).resolve())
        base["created_at"] = created_at
        return _write_job_record(base)


def _job_update(run_id: str, **payload) -> dict:
    with _JOB_LOCK:
        current = _read_job(run_id)
        if not current:
            current = {"run_id": run_id, "job_type": payload.pop("job_type", "unknown"), "created_at": _utc_now_iso(), "run_dir": str(_run_dir(run_id).resolve())}
        current.update(payload)
        current.setdefault("run_id", run_id)
        current.setdefault("run_dir", str(_run_dir(run_id).resolve()))
        return _write_job_record(current)


def _job_status_from_status_payload(data: dict) -> str:
    raw = str(data.get("run_status") or data.get("status") or "").strip().lower()
    stage = str(data.get("stage") or "").strip().lower()
    if raw in _JOB_TERMINAL_STATUSES:
        return "cancelled" if raw in {"cancelled", "canceled"} else ("complete" if raw in {"complete", "completed", "done", "finished", "success", "succeeded"} else raw)
    if raw in _JOB_ACTIVE_STATUSES:
        return "running" if raw not in {"queued", "starting", "resuming"} else raw
    if stage in {"results_ready", "partial_results_ready"}:
        return "complete"
    if stage in {"cancelled", "canceled"}:
        return "cancelled"
    if stage in {"error", "failed", "fatal_error", "process_exited"}:
        return "error"
    if stage in {"queued", "starting", "searching", "fetching", "reading_articles", "ai_batch_running", "resume_started"}:
        return "running"
    return raw or "unknown"


def _job_sync_from_status(run_id: str, job_type: str, rd: Path, status_data: dict) -> None:
    if not run_id:
        return
    try:
        payload = {
            "job_type": job_type,
            "status": _job_status_from_status_payload(status_data or {}),
            "run_status": (status_data or {}).get("run_status", ""),
            "stage": (status_data or {}).get("stage", ""),
            "processed": (status_data or {}).get("processed", (status_data or {}).get("done", "")),
            "total": (status_data or {}).get("total", (status_data or {}).get("row_count_uploaded", "")),
            "error": (status_data or {}).get("error", ""),
            "run_dir": str(rd.resolve()),
        }
        # Keep useful counters when present without making the registry huge.
        for k in ("current_item", "current_search", "current_url", "queries_used", "cap_hit", "mode", "run_type", "strategy", "module", "input_filename", "remaining", "progress_pct", "resume_count"):
            if k in (status_data or {}):
                payload[k] = (status_data or {}).get(k)
        if not _read_job(run_id):
            _job_create(run_id, job_type, rd, **payload)
        else:
            _job_update(run_id, **payload)
    except Exception as e:
        try:
            print(f"job sync failed for {run_id}: {e}", file=sys.stderr)
        except Exception:
            pass


def _job_request_cancel(run_id: str) -> dict:
    return _job_update(run_id, cancel_requested=True, cancel_requested_at=_utc_now_iso(), status="cancelling")


def _job_cancel_requested(run_id: str) -> bool:
    return bool(_read_job(run_id).get("cancel_requested"))


def _should_cancel(run_id: str, cancel_event: threading.Event | None = None) -> bool:
    return bool((cancel_event and cancel_event.is_set()) or _job_cancel_requested(run_id))


def _job_live_state(run_id: str) -> str:
    proc = processes.get(run_id)
    if proc:
        return "running" if proc.poll() is None else f"exited_{proc.returncode}"
    th = recheck_threads.get(run_id) or story_threads.get(run_id) or presence_threads.get(run_id)
    if th:
        return "running" if th.is_alive() else "not_running"
    runtime = globals().get("deep_enrichment_runtime")
    if runtime is not None:
        try:
            state = runtime.live_state(run_id)
            if state != "not_running":
                return state
        except Exception:
            pass
    return "not_running"


def _job_is_active(data: dict) -> bool:
    status = str(data.get("status") or data.get("run_status") or "").lower()
    stage = str(data.get("stage") or "").lower()
    return status in _JOB_ACTIVE_STATUSES or stage in {"queued", "starting", "searching", "fetching", "reading_articles", "ai_batch_running", "resume_started"}


def _job_records(limit: int = 200, job_type: str | None = None) -> list[dict]:
    items = []
    for path in JOBS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if job_type and data.get("job_type") != job_type:
                continue
            data["live_state"] = _job_live_state(str(data.get("run_id") or ""))
            items.append(data)
        except Exception:
            continue
    items.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
    return items[:max(1, min(int(limit or 200), 500))]


def _mark_run_status_interrupted(job: dict) -> None:
    run_id = str(job.get("run_id") or "")
    job_type = str(job.get("job_type") or "")
    rd = Path(job.get("run_dir") or _run_dir(run_id))
    try:
        if job_type in {"repository", "dedupe_recheck"} or run_id.startswith("run_"):
            _write_repo_status(rd, ok=False, run_id=run_id, run_status="interrupted", stage="interrupted_restart", current_item="Run interrupted by backend restart; partial exports remain available.")
            _release_repo_lock(run_id)
        elif job_type == "no_website_recheck" or run_id.startswith("recheck_"):
            _write_recheck_status(rd, ok=False, run_id=run_id, run_status="interrupted", stage="interrupted_restart", current_item="Re-check interrupted by backend restart; partial exports remain available.")
        elif job_type == "ngo_presence_check" or run_id.startswith("presence_"):
            _write_presence_status(rd, ok=False, run_id=run_id, run_status="interrupted", stage="interrupted_restart", current_item="Presence check interrupted by backend restart; partial exports remain available.")
        elif job_type in {"story", "discovery"} or run_id.startswith(("story", "discovery")):
            _write_story_status(rd, ok=False, run_id=run_id, run_status="interrupted", stage="interrupted_restart", current_item="Run interrupted by backend restart; partial exports remain available.")
    except Exception as e:
        try:
            print(f"failed to mark run status interrupted for {run_id}: {e}", file=sys.stderr)
        except Exception:
            pass


def _bootstrap_job_registry_from_runs() -> None:
    """Create job records for legacy run folders that do not yet have one."""
    try:
        for rd in RUNS_DIR.iterdir():
            if not rd.is_dir() or rd.name == "_jobs":
                continue
            run_id = rd.name
            if _read_job(run_id):
                continue
            if run_id.startswith("run_") and (rd / OUTPUTS["status"]).exists():
                try:
                    data = json.loads((rd / OUTPUTS["status"]).read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                _job_sync_from_status(run_id, str(data.get("run_type") or "repository"), rd, data)
            elif run_id.startswith("recheck_") and (rd / RECHECK_OUTPUTS["status"]).exists():
                try:
                    data = json.loads((rd / RECHECK_OUTPUTS["status"]).read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                _job_sync_from_status(run_id, "no_website_recheck", rd, data)
            elif run_id.startswith("presence_") and (rd / PRESENCE_OUTPUTS["status"]).exists():
                try:
                    data = json.loads((rd / PRESENCE_OUTPUTS["status"]).read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                _job_sync_from_status(run_id, "ngo_presence_check", rd, data)
            elif run_id.startswith(("story", "discovery")) and (rd / STORY_OUTPUTS["status"]).exists():
                try:
                    data = json.loads((rd / STORY_OUTPUTS["status"]).read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                inferred = "discovery" if run_id.startswith("discovery") or data.get("module") == "discovery" else "story"
                _job_sync_from_status(run_id, inferred, rd, data)
    except Exception as e:
        print(f"job registry bootstrap failed: {e}", file=sys.stderr)


def _reconcile_job_registry_startup() -> None:
    _bootstrap_job_registry_from_runs()
    for job in _job_records(limit=500):
        run_id = str(job.get("run_id") or "")
        if not run_id:
            continue
        if _job_is_active(job) and _job_live_state(run_id) != "running":
            _job_update(run_id, status="interrupted", stage="interrupted_restart", interrupted_at=_utc_now_iso(), live_state="not_running")
            _mark_run_status_interrupted({**job, "run_id": run_id})


def _run_not_found(module: str, run_id: str):
    return _json(
        False,
        status_code=404,
        run_id=run_id,
        stage="run_not_found",
        error=f"{module} run not found",
    )


def _csv_info(path: Path) -> tuple[int, list[str], Optional[str]]:
    """Return row count, fieldnames, and the detected NGO-name column.

    Validation happens before a subprocess starts, so bad CSVs fail immediately
    instead of starting a paid/long-running job that later crashes.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        name_key = None
        for k in fieldnames:
            if k and k.strip().lower() in CSV_NAME_HEADERS:
                name_key = k
                break
        row_count = sum(1 for row in reader if any(str(v or "").strip() for v in row.values()))
    return row_count, fieldnames, name_key


def _count_csv_rows(path: Path) -> int:
    row_count, _, _ = _csv_info(path)
    return row_count


def _save_upload_with_limit(upload: UploadFile, dst: Path, max_bytes: int = MAX_UPLOAD_BYTES) -> int:
    """Stream upload to disk with a hard byte cap to avoid filling Render disk."""
    total = 0
    with dst.open("wb") as f:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Uploaded file is too large. Limit is {max_bytes // (1024 * 1024)} MB.")
            f.write(chunk)
    return total


def _write_repo_status(rd: Path, **payload):
    """Write/merge repository status JSON from the API wrapper.

    Used for cancel/error states where the engine subprocess may not get a
    chance to write a final status itself.
    """
    path = rd / OUTPUTS["status"]
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(payload)
    current.setdefault("ok", True)
    current.setdefault("module", "repository")
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _job_sync_from_status(str(current.get("run_id") or rd.name), str(current.get("run_type") or current.get("module") or "repository"), rd, current)
    except Exception:
        pass


def _status_is_terminal(rd: Path) -> bool:
    path = rd / OUTPUTS["status"]
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    terminal = {"complete", "completed", "done", "finished", "success", "succeeded", "partial", "error", "failed", "cancelled", "canceled"}
    status = str(data.get("run_status") or "").lower()
    stage = str(data.get("stage") or "").lower()
    return status in terminal or stage in {"results_ready", "partial_results_ready", "cancelled", "process_exited"}


def _read_repo_lock() -> str:
    try:
        return REPO_LOCK_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _release_repo_lock(run_id: str | None = None):
    try:
        locked = _read_repo_lock()
        if REPO_LOCK_FILE.exists() and (not run_id or locked == run_id):
            REPO_LOCK_FILE.unlink()
    except Exception:
        pass


def _repo_lock_is_active() -> tuple[bool, str]:
    locked = _read_repo_lock()
    if not locked:
        return False, ""
    rd = _run_dir(locked)
    proc = processes.get(locked)
    if proc and proc.poll() is None:
        return True, locked
    if _status_is_terminal(rd) or _repo_outputs_ready(rd):
        _release_repo_lock(locked)
        return False, ""
    return True, locked


def _acquire_repo_lock(run_id: str) -> tuple[bool, str]:
    active, locked = _repo_lock_is_active()
    if active:
        return False, locked
    try:
        fd = os.open(str(REPO_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(run_id)
        return True, run_id
    except FileExistsError:
        return False, _read_repo_lock()
    except Exception:
        # Production-safe fail-closed: if the lock cannot be verified, do not
        # risk starting overlapping paid/long-running jobs.
        return False, "lock_unavailable"


def _repo_outputs_ready(rd: Path) -> bool:
    """Repository is display-ready when repository + audit + status exist.

    Donor-lite can fail independently. If it does, final status is partial and
    repository/audit output should still become available instead of making the
    frontend poll forever.
    """
    return all((rd / OUTPUTS[k]).exists() for k in ("repository", "audit", "status"))


def _limit_csv_rows(src: Path, dst: Path, limit: int):
    with src.open("r", encoding="utf-8-sig", newline="") as fin, dst.open("w", encoding="utf-8-sig", newline="") as fout:
        reader = csv.DictReader(fin)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        for i, row in enumerate(reader):
            if i >= limit:
                break
            writer.writerow(_safe_csv_row(row))


def _active_run_ids():
    live = []
    for run_id, proc in list(processes.items()):
        if proc.poll() is None:
            live.append(run_id)
    active, locked = _repo_lock_is_active()
    if active and locked and locked not in live:
        live.append(locked)
    return live


@app.get("/")
def root():
    return {"ok": True, "service": APP_NAME, "message": "Backend is running"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": APP_NAME,
        "runs_dir": str(RUNS_DIR),
        "engine_file_exists": ENGINE_FILE.exists(),
        "active_runs": _active_run_ids(),
        "jobs_dir": str(JOBS_DIR),
        "recent_jobs": len(_job_records(limit=500)),
    }


@app.get("/jobs")
def jobs_list(limit: int = 100, job_type: str = ""):
    return _json(True, rows=_job_records(limit=limit, job_type=(job_type or None)), count=len(_job_records(limit=limit, job_type=(job_type or None))))


@app.get("/jobs/{run_id}")
def jobs_get(run_id: str):
    job = _read_job(run_id)
    if not job:
        return _json(False, status_code=404, run_id=run_id, stage="job_not_found", error="Job record not found")
    job["live_state"] = _job_live_state(run_id)
    return _json(True, job=job)


@app.post("/jobs/{run_id}/cancel")
def jobs_cancel(run_id: str):
    job = _read_job(run_id)
    if not job:
        return _json(False, status_code=404, run_id=run_id, stage="job_not_found", error="Job record not found")
    _job_request_cancel(run_id)
    job_type = str(job.get("job_type") or "")
    if run_id in processes:
        return repository_cancel(run_id)
    if job_type == "no_website_recheck" or run_id.startswith("recheck_"):
        return recheck_cancel(run_id)
    if job_type in {"story", "discovery"} or run_id.startswith(("story", "discovery")):
        return story_cancel(run_id)
    if job_type == "deep_enrichment" or run_id.startswith("enrich_"):
        runtime = globals().get("deep_enrichment_runtime")
        if runtime is not None:
            return runtime.cancel(run_id)
    return _json(True, run_id=run_id, stage="cancel_requested", cancel_requested=True, live_state=_job_live_state(run_id))


def _default_dashboard_payload():
    return {
        "lastUpdated": "",
        "states": {},
        "pmStats": {},
        "_dashboardWarning": "No progress dashboard data has been published on the backend yet.",
    }


def _read_dashboard_payload():
    if not DASHBOARD_DATA_FILE.exists():
        return _default_dashboard_payload()
    try:
        data = json.loads(DASHBOARD_DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    payload = _default_dashboard_payload()
    payload["_dashboardWarning"] = "Progress dashboard storage exists but could not be read."
    return payload


@app.get("/dashboard")
def dashboard_get():
    return _json(True, data=_read_dashboard_payload())


@app.post("/dashboard/update")
def dashboard_update(payload: dict):
    if not os.environ.get("ADMIN_PASSWORD"):
        return _json(False, status_code=500, stage="missing_admin_password", error="ADMIN_PASSWORD must be set on Render before publishing progress data")
    password = str((payload or {}).get("password") or "")
    if password != os.environ.get("ADMIN_PASSWORD"):
        return _json(False, status_code=401, stage="wrong_password", error="Wrong password")
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return _json(False, status_code=400, stage="bad_dashboard_data", error="Dashboard data must be a JSON object")
    data = dict(data)
    data["lastUpdated"] = time.strftime("%d %b %Y, %I:%M %p")
    _atomic_write_text(DASHBOARD_DATA_FILE, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return _json(True, data=data)


@app.post("/repository/start")
async def start_repository(file: UploadFile = File(...), mode: str = "rapid", run_type: str = "repository"):
    """
    Upload ngo_list.csv and start the existing Python engine as a background process.
    mode=rapid is for small runs shown on-site, max RAPID_ROWS_LIMIT rows.
    mode=bulk is for CSV batch runs, max MAX_ROWS_PER_RUN rows.
    Backward-compatible aliases: test -> rapid, full -> bulk.
    """
    if not _has_serper_keys() or not os.environ.get("ANTHROPIC_API_KEY"):
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS, plus ANTHROPIC_API_KEY, must be set on Render")

    active = _active_run_ids()
    if active:
        return _json(False, status_code=409, stage="another_run_active", error="Another repository run is already active", active_runs=active)

    run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)

    uploaded = rd / "uploaded_input.csv"
    try:
        upload_bytes = _save_upload_with_limit(file, uploaded)
    except ValueError as e:
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="file_too_large", error=str(e))
        return _json(False, status_code=413, run_id=run_id, stage="file_too_large", error=str(e))

    try:
        row_count, fieldnames, name_key = _csv_info(uploaded)
    except Exception as e:
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="bad_csv", error=str(e))
        return _json(False, status_code=400, run_id=run_id, stage="bad_csv", error=f"Could not read CSV: {e}")

    if not name_key:
        msg = "CSV must contain a name column. Accepted headers: name, ngo_name, ngo name, organisation, organization."
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="missing_name_column", error=msg, fieldnames=fieldnames)
        return _json(False, status_code=400, run_id=run_id, stage="missing_name_column", error=msg, fieldnames=fieldnames)

    state_key = None
    for k in fieldnames:
        if k and k.strip().lower() == "state":
            state_key = k
            break
    if not state_key and run_type == "repository":
        msg = "CSV must contain a state column. Required format: name,state. District is optional."
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="missing_state_column", error=msg, fieldnames=fieldnames)
        return _json(False, status_code=400, run_id=run_id, stage="missing_state_column", error=msg, fieldnames=fieldnames)

    if row_count <= 0:
        msg = "CSV has no data rows."
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="empty_csv", error=msg, fieldnames=fieldnames)
        return _json(False, status_code=400, run_id=run_id, stage="empty_csv", error=msg, fieldnames=fieldnames)

    # User-facing modes for NGO Discovery.
    # rapid = small on-site result view. bulk = larger CSV batch.
    # Keep old aliases so older frontend/tests do not break.
    mode = (mode or "rapid").strip().lower()
    if mode == "test":
        mode = "rapid"
    if mode == "full":
        mode = "bulk"

    if mode not in {"rapid", "bulk"}:
        return _json(False, status_code=400, run_id=run_id, stage="bad_mode", error="mode must be rapid or bulk")

    run_type = (run_type or "repository").strip().lower().replace("-", "_")
    if run_type not in {"repository", "dedupe_recheck"}:
        return _json(False, status_code=400, run_id=run_id, stage="bad_run_type", error="run_type must be repository or dedupe_recheck")

    if mode == "rapid":
        if row_count > RAPID_ROWS_LIMIT:
            return _json(
                False,
                status_code=400,
                run_id=run_id,
                stage="too_many_rows_rapid",
                error=f"Rapid mode allows up to {RAPID_ROWS_LIMIT} NGOs. Use Bulk mode for larger CSVs.",
                row_count=row_count,
                limit=RAPID_ROWS_LIMIT,
            )
        rows_to_run = row_count
    else:
        if row_count > MAX_ROWS_PER_RUN:
            return _json(
                False,
                status_code=400,
                run_id=run_id,
                stage="too_many_rows_bulk",
                error=f"Bulk mode allows up to {MAX_ROWS_PER_RUN} NGOs per run. For 10k runs, keep this as an overnight/checkpointed run.",
                row_count=row_count,
                limit=MAX_ROWS_PER_RUN,
            )
        rows_to_run = row_count

    lock_ok, locked_run = _acquire_repo_lock(run_id)
    if not lock_ok:
        _write_repo_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="another_run_active", error="Another repository run is already active", active_run=locked_run)
        return _json(False, status_code=409, run_id=run_id, stage="another_run_active", error="Another repository run is already active", active_run=locked_run)

    input_csv = rd / "ngo_list.csv"
    _limit_csv_rows(uploaded, input_csv, rows_to_run)

    # Initial status file, so frontend immediately sees something.
    status = {
        "ok": True,
        "module": run_type,
        "run_type": run_type,
        "run_id": run_id,
        "run_status": "starting",
        "stage": "queued",
        "current_item": "Preparing engine",
        "current_search": "",
        "current_url": "",
        "processed": 0,
        "total": rows_to_run,
        "row_count_uploaded": row_count,
        "upload_bytes": upload_bytes,
        "mode": mode,
        "run_type": run_type,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write_text((rd / "dfp2_status.json"), json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    _job_create(run_id, run_type, rd, **status)

    env = os.environ.copy()
    env["DFP_RUN_MODE"] = mode
    env["DFP_RUN_TYPE"] = run_type
    # Rapid Mode should avoid Claude Batch queue time; Bulk Mode keeps Batch API
    # for cost/rate-limit safety. These can still be overridden in Render env.
    if mode == "rapid":
        env.setdefault("AI_PROFILE_MODE", "direct")
    else:
        env.setdefault("AI_PROFILE_MODE", "batch")
    proc = subprocess.Popen(
        [sys.executable, str(ENGINE_FILE)],
        cwd=str(rd),
        env=env,
        stdout=(rd / "stdout.log").open("w", encoding="utf-8"),
        stderr=(rd / "stderr.log").open("w", encoding="utf-8"),
    )
    processes[run_id] = proc
    _job_update(run_id, status="running", stage="engine_process_started", process_state="running", pid=proc.pid)

    return _json(True, run_id=run_id, stage="started", total=rows_to_run, uploaded_rows=row_count, mode=mode, run_type=run_type, module=run_type)


@app.get("/repository/status/{run_id}")
def repository_status(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Repository", run_id)
    status_path = rd / "dfp2_status.json"
    proc = processes.get(run_id)
    process_state = "unknown"
    if proc:
        process_state = "running" if proc.poll() is None else f"exited_{proc.returncode}"

    if not status_path.exists():
        return _json(False, status_code=404, run_id=run_id, stage="status_not_found", error="No status file found yet", process_state=process_state)

    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _json(False, run_id=run_id, stage="bad_status_json", error=str(e), process_state=process_state)

    data.setdefault("ok", True)
    data["run_id"] = run_id
    data["process_state"] = process_state
    data["downloads"] = {kind: (rd / filename).exists() for kind, filename in OUTPUTS.items()}
    data["downloads"]["history"] = GLOBAL_SCAN_HISTORY.exists()
    data["file_counts"] = _output_counts(rd, OUTPUTS)
    data["file_counts"]["history"] = _count_export_records(GLOBAL_SCAN_HISTORY)
    if _status_is_terminal(rd) or _repo_outputs_ready(rd):
        _release_repo_lock(run_id)

    # If the engine process died before writing final outputs, surface a real terminal
    # error instead of leaving the frontend polling a stale queued/running status forever.
    terminal_statuses = {
        "complete", "completed", "done", "finished", "success", "succeeded",
        "partial", "error", "failed", "fatal_error", "cancelled", "canceled",
    }
    current_status = str(data.get("run_status") or "").lower()
    current_stage = str(data.get("stage") or "").lower()
    if proc and proc.poll() is not None and current_status not in terminal_statuses and not _repo_outputs_ready(rd):
        data["ok"] = False
        data["run_status"] = "error"
        data["stage"] = "process_exited"
        data["error"] = f"Engine exited with code {proc.returncode} before final outputs were ready"
        data["result_quality"] = "failed"
        _release_repo_lock(run_id)
    elif proc and proc.poll() is not None and current_stage in {"queued", "starting", "searching", "fetching", "ai_batch_running"} and not _repo_outputs_ready(rd):
        data["ok"] = False
        data["run_status"] = "error"
        data["stage"] = "process_exited"
        data["error"] = f"Engine exited with code {proc.returncode} before final outputs were ready"
        data["result_quality"] = "failed"
        _release_repo_lock(run_id)

    counts = data.get("counts") or {}
    if isinstance(counts, dict):
        data.setdefault("processed", data.get("done") if data.get("done") is not None else counts.get("processed", 0))
        for k in ("ready_for_ai", "fetch_failed", "search_failed", "dropped_not_children", "filtered_rejected", "shortlisted", "maybe", "rejected", "skipped_error"):
            data.setdefault(k, counts.get(k, 0))
        data.setdefault("errors", int(counts.get("fetch_failed", 0) or 0) + int(counts.get("search_failed", 0) or 0) + int(counts.get("skipped_error", 0) or 0))
    _job_sync_from_status(run_id, str(data.get("run_type") or data.get("module") or "repository"), rd, data)
    data["job"] = _read_job(run_id)
    return JSONResponse(content=data)


def _read_csv_rows(path: Path, limit: int = 50):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append(row)
    return rows


def _count_export_records(path: Path) -> int:
    """Return user-facing row count for a CSV/log export.

    CSV counts exclude the header. Log counts are non-empty lines. Missing files
    return 0 so the frontend can safely show `Label (0)` instead of guessing.
    """
    if not path.exists() or not path.is_file():
        return 0
    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                # Skip header if present, then count remaining non-empty rows.
                try:
                    next(reader)
                except StopIteration:
                    return 0
                return sum(1 for row in reader if any(str(cell).strip() for cell in row))
        if path.suffix.lower() == ".log":
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for line in f if line.strip())
    except Exception:
        return 0
    return 0


def _output_counts(rd: Path, outputs: dict) -> dict:
    return {kind: _count_export_records(rd / filename) for kind, filename in outputs.items()}


def _read_progress_rows(path: Path, limit: int = 100):
    """Read latest row-level progress from dfp2_progress.jsonl.
    This is what the frontend should show while the final repository CSV is not ready yet.
    """
    if not path.exists():
        return []
    parsed = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for ln in lines[-limit:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
            parsed.append({
                "NGO Name": rec.get("name", ""),
                "State": rec.get("state", ""),
                "District": rec.get("district", ""),
                "Status": rec.get("status", ""),
                "Website": rec.get("website", ""),
                "Note": rec.get("note", ""),
            })
        except Exception:
            continue
    return parsed


@app.get("/repository/results/{run_id}")
def repository_results(run_id: str, limit: int = 50):
    """Return website-displayable results.

    During a run: returns live_progress rows from dfp2_progress.jsonl.
    After AI completes: returns final repository rows from dfp2_repository_output.csv.
    Donor-lite rows are included when available so Rapid Mode can show everything on-site.
    """
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Repository", run_id)
    repository_path = rd / OUTPUTS["repository"]
    donor_path = rd / OUTPUTS["donor-lite"]
    audit_path = rd / OUTPUTS["audit"]
    progress_path = rd / "dfp2_progress.jsonl"

    final_rows = _read_csv_rows(repository_path, limit=limit)
    donor_rows = _read_csv_rows(donor_path, limit=limit)
    audit_rows = _read_csv_rows(audit_path, limit=limit)
    live_rows = _read_progress_rows(progress_path, limit=max(limit, 100))

    status_data = {}
    status_path = rd / OUTPUTS["status"]
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status_data = {}
    downloads = {kind: (rd / filename).exists() for kind, filename in OUTPUTS.items()}
    downloads["history"] = GLOBAL_SCAN_HISTORY.exists()
    file_counts = _output_counts(rd, OUTPUTS)
    file_counts["history"] = _count_export_records(GLOBAL_SCAN_HISTORY)
    outputs_ready = _repo_outputs_ready(rd)
    if outputs_ready:
        _release_repo_lock(run_id)
    engine_stage = str(status_data.get("stage") or "")
    if outputs_ready and engine_stage in {"results_ready", "partial_results_ready"}:
        stage = engine_stage
    elif outputs_ready:
        stage = "outputs_ready"
    else:
        stage = "live_progress"
    return _json(
        True,
        run_id=run_id,
        stage=stage,
        results_ready=bool(outputs_ready),
        run_status=status_data.get("run_status", ""),
        result_quality=status_data.get("result_quality", ""),
        warning=status_data.get("warning", ""),
        ai_profiles_expected=status_data.get("ai_profiles_expected"),
        ai_profiles_completed=status_data.get("ai_profiles_completed"),
        rows=final_rows,
        donor_rows=donor_rows,
        audit_rows=audit_rows,
        live_rows=live_rows,
        count=len(final_rows),
        donor_count=len(donor_rows),
        live_count=len(live_rows),
        downloads=downloads,
        file_counts=file_counts,
    )


@app.get("/repository/export/{run_id}/{kind}")
def repository_export(run_id: str, kind: str):
    # Per-run exports plus a global scan-history export for tracking what has already been scanned.
    rd = _run_dir(run_id)
    if kind != "history" and not rd.exists():
        return _run_not_found("Repository", run_id)
    if kind == "history":
        path = GLOBAL_SCAN_HISTORY
    else:
        if kind not in OUTPUTS:
            raise HTTPException(status_code=404, detail="Unknown export kind")
        path = rd / OUTPUTS[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export not ready")
    media_type = "text/csv" if path.suffix == ".csv" else ("application/json" if path.suffix == ".json" else "text/plain")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/repository/history")
def repository_history():
    if not GLOBAL_SCAN_HISTORY.exists():
        raise HTTPException(status_code=404, detail="Scan history is not available yet")
    return FileResponse(GLOBAL_SCAN_HISTORY, media_type="text/csv", filename=GLOBAL_SCAN_HISTORY.name)


@app.get("/repository/runs")
def repository_runs(limit: int = 30):
    # Lightweight list: do not include internal workspace/undo/job folders, and
    # keep the default low so dashboard polling cannot spike memory on 512MB RAM.
    rows = []
    valid_prefixes = ("run_", "recheck_", "presence_", "story", "discovery")
    candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith(valid_prefixes)]
    for rd in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, 50))]:
        status_path = rd / OUTPUTS["status"]
        data = {}
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        rows.append({
            "run_id": rd.name,
            "updated_at": data.get("updated_at", ""),
            "run_status": data.get("run_status", ""),
            "stage": data.get("stage", ""),
            "total": data.get("total", ""),
            "processed": data.get("processed", data.get("done", "")),
            "shortlisted": data.get("shortlisted", ""),
            "maybe": data.get("maybe", ""),
            "rejected": data.get("rejected", ""),
            "mode": data.get("mode", ""),
            "run_type": data.get("run_type", ""),
        })

    return _json(True, rows=rows, count=len(rows))


@app.get("/repository/archive")
def repository_archive(limit: int = 30):
    """List prior repository and no-website recheck runs with export availability.

    This is a lightweight archive for the frontend so previous CSVs remain easy
    to download by date/run instead of relying on browser state.
    """
    items = []
    dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir()]
    dirs = sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, 300))]
    for rd in dirs:
        run_id = rd.name
        if run_id.startswith("run_"):
            module = "repository"
        elif run_id.startswith("recheck_"):
            module = "no_website_recheck"
        elif run_id.startswith("presence_"):
            module = "ngo_presence_check"
        else:
            module = "other"
        if module == "other":
            continue
        outputs = OUTPUTS if module == "repository" else (RECHECK_OUTPUTS if module == "no_website_recheck" else PRESENCE_OUTPUTS)
        status_path = rd / outputs["status"]
        data = {}
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        downloads = {kind: (rd / filename).exists() for kind, filename in outputs.items()}
        if os.environ.get("DFP2_LIGHTWEIGHT_ARCHIVE_COUNTS", "true").lower() in {"1", "true", "yes"}:
            file_counts = {kind: "" for kind in outputs.keys()}
        else:
            file_counts = _output_counts(rd, outputs)
        if module == "repository":
            downloads["history"] = GLOBAL_SCAN_HISTORY.exists()
            file_counts["history"] = _count_export_records(GLOBAL_SCAN_HISTORY)
        updated = data.get("updated_at", "")
        items.append({
            "run_id": run_id,
            "module": module,
            "label": ("Deduped NGO re-check" if data.get("run_type") == "dedupe_recheck" else ("NGO Discovery" if module == "repository" else ("NGO Presence Check" if module == "ngo_presence_check" else "No website re-check"))),
            "run_type": data.get("run_type", ""),
            "updated_at": updated,
            "run_status": data.get("run_status", ""),
            "stage": data.get("stage", ""),
            "total": data.get("total", data.get("row_count_uploaded", "")),
            "processed": data.get("processed", data.get("done", "")),
            "shortlisted": data.get("shortlisted", ""),
            "maybe": data.get("maybe", ""),
            "rejected": data.get("rejected", data.get("rejected_rows", "")),
            "website_found": data.get("website_found", ""),
            "no_official_website": data.get("no_official_website", ""),
            "errors": data.get("errors", ""),
            "downloads": downloads,
            "file_counts": file_counts,
            "repository_count": file_counts.get("repository", ""),
            "audit_count": file_counts.get("audit", ""),
            "rejected_count": file_counts.get("rejected", ""),
            "duplicates_count": file_counts.get("duplicates", ""),
            "donor_lite_count": file_counts.get("donor-lite", ""),
            "results_count": file_counts.get("results", ""),
            "errors_count": file_counts.get("errors", ""),
        })
    return _json(True, rows=items, count=len(items))


@app.post("/repository/resume/{run_id}")
def repository_resume(run_id: str):
    """Resume an interrupted repository run from its checkpoint files.

    This re-launches the same engine inside the existing run folder. The engine
    skips rows already present in dfp2_progress.jsonl and resumes AI profiles
    already saved in dfp2_ai_profiles.jsonl.
    """
    rd = _run_dir(run_id)
    if not rd.exists() or not (rd / "ngo_list.csv").exists():
        return _json(False, status_code=404, run_id=run_id, stage="run_not_found", error="No resumable run folder/input CSV found")
    active = _active_run_ids()
    if active:
        return _json(False, status_code=409, stage="another_run_active", error="Another repository run is already active", active_runs=active)
    lock_ok, locked_run = _acquire_repo_lock(run_id)
    if not lock_ok:
        return _json(False, status_code=409, run_id=run_id, stage="another_run_active", error="Another repository run is already active", active_run=locked_run)
    env = os.environ.copy()
    # Default to bulk-safe Batch mode on resume unless the previous status says rapid.
    mode = "bulk"
    status_path = rd / OUTPUTS["status"]
    if status_path.exists():
        try:
            old = json.loads(status_path.read_text(encoding="utf-8"))
            mode = str(old.get("mode") or mode).lower()
        except Exception:
            pass
    env["DFP_RUN_MODE"] = mode
    if mode == "rapid":
        env.setdefault("AI_PROFILE_MODE", "direct")
    else:
        env.setdefault("AI_PROFILE_MODE", "batch")
    _write_repo_status(rd, ok=True, run_id=run_id, run_status="resuming", stage="resume_started", current_item="Resuming from checkpoints")
    proc = subprocess.Popen(
        [sys.executable, str(ENGINE_FILE)],
        cwd=str(rd),
        env=env,
        stdout=(rd / "stdout_resume.log").open("a", encoding="utf-8"),
        stderr=(rd / "stderr_resume.log").open("a", encoding="utf-8"),
    )
    processes[run_id] = proc
    _job_update(run_id, status="running", stage="resume_process_started", process_state="running", pid=proc.pid)
    return _json(True, run_id=run_id, stage="resumed", mode=mode)


@app.post("/repository/cancel/{run_id}")
def repository_cancel(run_id: str):
    _job_request_cancel(run_id)
    proc = processes.get(run_id)
    if not proc or proc.poll() is not None:
        return _json(False, run_id=run_id, stage="not_running", error="Run is not active")
    proc.terminate()
    rd = _run_dir(run_id)
    _write_repo_status(rd, ok=True, run_id=run_id, run_status="cancelled", stage="cancelled", current_item="Cancelled safely")
    _job_update(run_id, status="cancelled", stage="cancelled", process_state="terminated")
    _release_repo_lock(run_id)
    return _json(True, run_id=run_id, run_status="cancelled", stage="cancelled")



# -----------------------------------------------------------------------------
# No Official Website Re-check — targeted recovery module
# -----------------------------------------------------------------------------
# Purpose: re-check rows that the main run marked no_official_website. This uses
# one Serper query per NGO by default, but has better official-site detection:
# Knowledge Panel website, top organic result, and forgiving domain matching
# such as "Bridges of Sports Foundation" -> bridgesofsports.org.
RECHECK_MAX_ROWS = int(os.environ.get("RECHECK_MAX_ROWS", "30000"))
RECHECK_SERPER_NUM = int(os.environ.get("RECHECK_SERPER_NUM", "10"))
RECHECK_PACE_SEC = float(os.environ.get("RECHECK_PACE_SEC", "0.20"))
SMART_RECHECK_MAX_QUERIES_PER_ROW = int(os.environ.get("SMART_RECHECK_MAX_QUERIES_PER_ROW", "2"))
SMART_RECHECK_MAX_TOTAL_QUERIES = int(os.environ.get("SMART_RECHECK_MAX_TOTAL_QUERIES", "58000"))
SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW = int(os.environ.get("SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW", "2"))
SMART_RECHECK_STOP_ON_HIGH_CONFIDENCE = os.environ.get("SMART_RECHECK_STOP_ON_HIGH_CONFIDENCE", "true").lower() != "false"
SMART_RECHECK_FUZZY_THRESHOLD = float(os.environ.get("SMART_RECHECK_FUZZY_THRESHOLD", "0.84"))
SMART_RECHECK_NOMINATION_SCORE = int(os.environ.get("SMART_RECHECK_NOMINATION_SCORE", "8"))
SMART_RECHECK_MAX_VERIFY_PER_ROW = int(os.environ.get("SMART_RECHECK_MAX_VERIFY_PER_ROW", "3"))
SMART_RECHECK_FETCH_TIMEOUT = int(os.environ.get("SMART_RECHECK_FETCH_TIMEOUT", "12"))
SMART_RECHECK_FETCH_RETRY_ATTEMPTS = max(1, int(os.environ.get("SMART_RECHECK_FETCH_RETRY_ATTEMPTS", "2")))
SMART_RECHECK_FETCH_RETRY_BACKOFF_SEC = max(0.0, float(os.environ.get("SMART_RECHECK_FETCH_RETRY_BACKOFF_SEC", "0.75")))
SMART_RECHECK_VERIFY_MAX_PAGES = int(os.environ.get("SMART_RECHECK_VERIFY_MAX_PAGES", "7"))
# Paid fallbacks are opt-in. The production default is Darpan-first Serper-only.
SMART_RECHECK_USE_BRAVE = os.environ.get("SMART_RECHECK_USE_BRAVE", "false").lower() not in {"0", "false", "no"}
SMART_RECHECK_USE_FIRECRAWL = os.environ.get("SMART_RECHECK_USE_FIRECRAWL", "false").lower() not in {"0", "false", "no"}
SMART_RECHECK_ENABLE_RENAME_RECOVERY = os.environ.get("SMART_RECHECK_ENABLE_RENAME_RECOVERY", "false").lower() not in {"0", "false", "no"}

# Firecrawl is a selective recovery layer, never the default search engine.
# Budgets are conservative per-run ceilings. Search is exposed as a separate
# strategy so the 2-credit search call is only spent on a user-selected queue.
SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET = max(0, int(os.environ.get("SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET", "10000")))
SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET = max(0, int(os.environ.get("SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET", "7000")))
SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET = max(0, int(os.environ.get("SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET", "2000")))
SMART_RECHECK_FIRECRAWL_RESERVE_CREDIT_BUDGET = max(0, int(os.environ.get("SMART_RECHECK_FIRECRAWL_RESERVE_CREDIT_BUDGET", "1000")))
SMART_RECHECK_FIRECRAWL_MAX_SCRAPES_PER_DOMAIN = max(1, int(os.environ.get("SMART_RECHECK_FIRECRAWL_MAX_SCRAPES_PER_DOMAIN", "3")))
SMART_RECHECK_FIRECRAWL_MAX_SEARCHES_PER_NGO = max(0, int(os.environ.get("SMART_RECHECK_FIRECRAWL_MAX_SEARCHES_PER_NGO", "1")))
SMART_RECHECK_FIRECRAWL_SEARCH_LIMIT = min(10, max(1, int(os.environ.get("SMART_RECHECK_FIRECRAWL_SEARCH_LIMIT", "10"))))
SMART_RECHECK_FIRECRAWL_SEARCH_MAX_VERIFY = max(1, int(os.environ.get("SMART_RECHECK_FIRECRAWL_SEARCH_MAX_VERIFY", "1")))
SMART_RECHECK_FIRECRAWL_PROXY = os.environ.get("SMART_RECHECK_FIRECRAWL_PROXY", "basic").strip().lower()
if SMART_RECHECK_FIRECRAWL_PROXY not in {"basic", "enhanced"}:
    SMART_RECHECK_FIRECRAWL_PROXY = "basic"
SMART_RECHECK_LOCAL_PDF_MAX_BYTES = max(1_000_000, int(os.environ.get("SMART_RECHECK_LOCAL_PDF_MAX_BYTES", "12000000")))
SMART_RECHECK_LOCAL_PDF_MAX_PAGES = max(1, int(os.environ.get("SMART_RECHECK_LOCAL_PDF_MAX_PAGES", "40")))

TIER_A_LEGAL = {"foundation","trust","society","sanstha","sansthan","samiti","samithi","samsthe","mission","ngo","association","organization","organisation","charitable","pratishthan"}
TIER_B_COMMON = {"seva","welfare","rural","development","education","educational","humanity","care","help","social","service","services","public","national","india","indian","group","committee","council","centre","center","institute","institution"}
GENERIC_BRAND_TOKENS = {"asha","seva","akshaya","sparsh","sparsha","prerana","jeevan","jyoti","disha","udaan","sahara","aasra","umang","kiran","hope","care","help"}
STOPWORDS = {"of","the","and","for","a","an"}
SMART_KNOWN_ALIASES = {
    "parikrma humanity foundation": ["parikrma", "parikrma foundation"],
    "parikrama humanity foundation": ["parikrma", "parikrma foundation"],
    "bridges of sports foundation": ["bridges of sports", "bridgesofsports", "bos"],
    "hunger heroes": ["feeding india", "hunger heroes feeding india", "feeding india hunger heroes"],
}
try:
    _alias_extra = json.loads(os.environ.get("SMART_RECHECK_ALIASES_JSON", "{}") or "{}")
    if isinstance(_alias_extra, dict):
        for _k, _v in _alias_extra.items():
            if isinstance(_v, list):
                SMART_KNOWN_ALIASES[str(_k).lower().strip()] = [str(x).strip() for x in _v if str(x).strip()]
except Exception as _alias_err:
    print(f"SMART_RECHECK_ALIASES_JSON ignored: {_alias_err}", file=sys.stderr)
RECHECK_FIELDS = [
    "NGO Name", "State", "District", "Darpan ID", "Email", "Phone", "Registered Address",
    "Website", "Website Status", "Confidence", "Source", "Search Provider", "Query", "Note",
    "Match Route", "Evidence Grade", "Evidence Type", "Evidence Matched Text", "Evidence Page URL",
    "Query Pass", "Variant Used", "Variant Type", "Searched", "Queries Used", "Successful Searches",
    "Failed Searches", "Fetch Status", "Fetch Errors", "Firecrawl Credits Used", "Firecrawl Action",
    "Duplicate Group Size"
]
RECHECK_AUDIT_FIELDS = [
    "NGO Name", "State", "District", "Darpan ID", "Provider", "Query", "Candidate URL", "Candidate Title",
    "Candidate Source", "Score", "Decision", "Note",
    "Query Pass", "Variant Used", "Variant Type", "Candidate Domain", "Candidate Snippet",
    "Match Route", "Evidence Grade", "Evidence Type", "Evidence Matched Text", "Evidence Page URL",
    "Fetch Status", "Fetch Errors", "Firecrawl Credits Used", "Firecrawl Action",
    "Reject Reason", "Carrier URL", "Carrier Phrase"
]
RECHECK_BAD_DOMAINS = (
    "ngodarpan", "darpan.gov", "csrbox", "ngobox", "justdial", "sulekha",
    "indiamart", "facebook.", "instagram.", "linkedin.", "twitter.", "x.com",
    "youtube.", "wikipedia.", "guidestar", "give.do", "globalgiving", "ngoadvisor",
)
RECHECK_LISTING_DOMAINS = (
    "thehindu", "timesofindia", "hindustantimes", "indianexpress", "deccanherald",
    "newindianexpress", "yourstory", "betterindia", "medium.com", "news", "magazine",
)
RECHECK_LEGAL_SUFFIXES = {
    "trust", "foundation", "society", "sanstha", "samiti", "mission", "ngo",
    "charitable", "welfare", "seva", "sangha", "association", "organization", "organisation",
}

RECHECK_URL_RE = re.compile(r"""https?://[^\s\]\)\}"'<>]+""", re.I)

def _recheck_flatten_text(value) -> str:
    parts = []
    if isinstance(value, dict):
        for v in value.values():
            parts.append(_recheck_flatten_text(v))
    elif isinstance(value, list):
        for v in value:
            parts.append(_recheck_flatten_text(v))
    elif value is not None:
        parts.append(str(value))
    return " ".join(p for p in parts if p)

def _recheck_extract_urls(value) -> list[str]:
    text = _recheck_flatten_text(value)
    urls = []
    for u in RECHECK_URL_RE.findall(text):
        u = u.rstrip(".,;:!?)]}")
        if u and u not in urls:
            urls.append(u)
    return urls


def _recheck_status_path(rd: Path) -> Path:
    return rd / RECHECK_OUTPUTS["status"]


def _write_recheck_status(rd: Path, **payload):
    current = {}
    path = _recheck_status_path(rd)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(payload)
    current.setdefault("ok", True)
    current.setdefault("module", "no_website_recheck")
    current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _job_sync_from_status(str(current.get("run_id") or rd.name), "no_website_recheck", rd, current)
    except Exception:
        pass


def _append_recheck_error(rd: Path, msg: str):
    with (rd / RECHECK_OUTPUTS["errors"]).open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")





def _recheck_pause_path(rd: Path) -> Path:
    return rd / ".recheck_pause_requested"


def _recheck_stop_path(rd: Path) -> Path:
    return rd / ".recheck_stop_requested"


def _recheck_read_all_csv(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _recheck_identity_key(row: dict, result_row: bool = False) -> str:
    if result_row:
        darpan = str(row.get("Darpan ID") or row.get("darpan_id") or "").strip()
        name = str(row.get("NGO Name") or row.get("name") or "").strip()
        district = str(row.get("District") or row.get("district") or "").strip()
        state = str(row.get("State") or row.get("state") or "").strip()
    else:
        darpan = _smart_primary_darpan(row)
        name = str(row.get("name") or row.get("NGO Name") or "").strip()
        district = str(row.get("district") or row.get("District") or "").strip()
        state = str(row.get("state") or row.get("State") or "").strip()
    if darpan:
        return "id:" + _smart_compact(darpan)
    return "name:" + "|".join([_smart_norm(name), _smart_norm(district), _smart_norm(state)])


def _recheck_control_action(rd: Path, run_id: str, cancel_event: threading.Event) -> str:
    if _recheck_stop_path(rd).exists() or _should_cancel(run_id, cancel_event):
        return "stop"
    if _recheck_pause_path(rd).exists():
        return "pause"
    return ""


def _recheck_progress_payload(processed: int, total: int, active_elapsed_sec: float) -> dict:
    processed = max(0, int(processed or 0))
    total = max(0, int(total or 0))
    elapsed = max(0.0, float(active_elapsed_sec or 0.0))
    remaining = max(0, total - processed)
    progress_pct = round((processed / total) * 100, 2) if total else 0.0
    rate_per_sec = (processed / elapsed) if processed > 0 and elapsed > 0 else 0.0
    rate_per_min = round(rate_per_sec * 60, 2) if rate_per_sec else 0.0
    eta_seconds = None
    eta_at = ""
    eta_quality = "calculating"
    # Avoid publishing a very noisy ETA from the first few rows.
    if processed >= 5 and rate_per_sec > 0:
        eta_seconds = max(0, int(round(remaining / rate_per_sec)))
        eta_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + eta_seconds))
        eta_quality = "live_estimate"
    if remaining == 0 and total:
        eta_seconds = 0
        eta_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        eta_quality = "complete"
    return {
        "processed": processed,
        "total": total,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "active_elapsed_sec": round(elapsed, 1),
        "throughput_rows_per_min": rate_per_min,
        "eta_seconds": eta_seconds,
        "eta_at": eta_at,
        "eta_quality": eta_quality,
    }


def _recheck_append_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(_safe_csv_row({k: row.get(k, "") for k in fieldnames}))


def _recheck_append_checkpoint(rd: Path, result: dict, audit_rows: list[dict]) -> None:
    """Append one completed NGO and its audit evidence without rewriting prior rows."""
    _recheck_append_csv(rd / RECHECK_OUTPUTS["results"], RECHECK_FIELDS, [result])
    _recheck_append_csv(rd / RECHECK_OUTPUTS["audit"], RECHECK_AUDIT_FIELDS, audit_rows)

    avika_fields = ["name", "district", "state", "darpan_id", "website", "website_recovery_status"]
    allowed = {"confirmed_official_site", "probable_official_site", "rename_verified_match"}
    if result.get("Website") and result.get("Website Status") in allowed:
        _recheck_append_csv(rd / RECHECK_OUTPUTS["avika_input"], avika_fields, [{
            "name": result.get("NGO Name", ""), "district": result.get("District", ""),
            "state": result.get("State", ""), "darpan_id": result.get("Darpan ID", ""),
            "website": result.get("Website", ""), "website_recovery_status": result.get("Website Status", ""),
        }])

    firecrawl_fields = ["name", "district", "state", "darpan_id", "email", "phone", "registered_address", "website", "previous_website_status"]
    firecrawl_statuses = {
        "candidate_site_unreachable", "possible_site_manual_review", "needs_manual_verification",
        "no_candidate_after_completed_search", "search_incomplete", "provider_failure",
    }
    if result.get("Website Status") in firecrawl_statuses:
        _recheck_append_csv(rd / RECHECK_OUTPUTS["firecrawl_input"], firecrawl_fields, [{
            "name": result.get("NGO Name", ""), "district": result.get("District", ""),
            "state": result.get("State", ""), "darpan_id": result.get("Darpan ID", ""),
            "email": result.get("Email", ""), "phone": result.get("Phone", ""),
            "registered_address": result.get("Registered Address", ""), "website": result.get("Website", ""),
            "previous_website_status": result.get("Website Status", ""),
        }])


def _recheck_initialize_outputs(rd: Path) -> None:
    # Make partial downloads available immediately, even before row 1 completes.
    _write_recheck_csvs(rd, [], [])
    _smart_write_skipped(rd, [])
    (rd / RECHECK_OUTPUTS["errors"]).touch(exist_ok=True)


def _recheck_load_counter(rd: Path, strategy_name: str) -> dict:
    status = {}
    summary = {}
    try:
        status = json.loads(_recheck_status_path(rd).read_text(encoding="utf-8"))
    except Exception:
        status = {}
    try:
        summary = json.loads((rd / RECHECK_OUTPUTS["summary"]).read_text(encoding="utf-8"))
    except Exception:
        summary = status.get("summary") or {}
    return _smart_firecrawl_counter_init({
        "queries": int(status.get("queries_used") or summary.get("total_queries") or 0),
        "serper_queries": int(summary.get("serper_queries") or 0),
        "brave_queries": int(summary.get("brave_queries") or 0),
        "firecrawl_credits": int(status.get("firecrawl_credits_used") or summary.get("firecrawl_credits_used") or 0),
        "firecrawl_verify_credits": int(summary.get("firecrawl_verify_credits") or 0),
        "firecrawl_search_credits": int(summary.get("firecrawl_search_credits") or 0),
        "firecrawl_scrapes": int(summary.get("firecrawl_scrapes") or 0),
        "firecrawl_searches": int(summary.get("firecrawl_searches") or 0),
        "strategy": strategy_name,
    })


def _read_recheck_input(path: Path) -> list[dict]:
    """Read CSV forgivingly and preserve every identity field supplied by the user.

    De-duplication prioritises Darpan/registration identifiers, then falls back to
    name + district + state. This prevents identifier-bearing rows from being
    collapsed merely because their display names are similar.
    """
    rows: list[dict] = []
    seen = set()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return rows
    sample = text.splitlines()[0].lower()
    has_header = any(h in sample for h in ["name", "ngo", "organisation", "organization"])
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        if has_header:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            name_key = None
            for k in fields:
                if k and k.strip().lower() in CSV_NAME_HEADERS:
                    name_key = k
                    break
            if not name_key and fields:
                name_key = fields[0]
            for r in reader:
                name = (r.get(name_key) or "").strip() if name_key else ""
                if not name:
                    continue
                clean = {str(k).strip(): (v or "").strip() for k, v in r.items() if k is not None}
                state = (clean.get("state") or clean.get("State") or "").strip()
                district = (clean.get("district") or clean.get("District") or "").strip()
                darpan = ""
                for k, v in clean.items():
                    lk = k.lower()
                    if "darpan" in lk or "unique id" in lk or "unique_id" in lk:
                        darpan = v.strip()
                        if darpan:
                            break
                if darpan:
                    norm = ("id", re.sub(r"[^a-z0-9]", "", darpan.lower()))
                else:
                    norm = ("name", re.sub(r"\s+", " ", name).lower(), district.lower(), state.lower())
                if norm in seen:
                    continue
                seen.add(norm)
                clean.update({
                    "name": re.sub(r"\s+", " ", name),
                    "state": state,
                    "district": district,
                })
                if darpan:
                    clean.setdefault("darpan_id", darpan)
                rows.append(clean)
        else:
            reader = csv.reader(f)
            for r in reader:
                if not r:
                    continue
                name = (r[0] or "").strip()
                if not name:
                    continue
                norm = ("name", re.sub(r"\s+", " ", name).lower(), "", "")
                if norm in seen:
                    continue
                seen.add(norm)
                rows.append({"name": re.sub(r"\s+", " ", name), "state": "", "district": ""})
    return rows

def _recheck_tokens(name: str) -> list[str]:
    return [
        t for t in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
        if len(t) > 2 and t not in RECHECK_LEGAL_SUFFIXES
    ]


def _recheck_domain(url: str) -> str:
    try:
        host = urlparse(str(url or "")).netloc.lower().split("@")[ -1].split(":")[0]
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _recheck_is_document_url(url: str) -> bool:
    path = (urlparse(str(url or "")).path or "").lower()
    return path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"))


def _recheck_bad_url(url: str) -> bool:
    """Reject third-party/listing domains, but keep official-domain documents.

    A PDF is not itself returned as the official homepage; it may nevertheless
    contain the Darpan/FCRA evidence that proves ownership of its parent domain.
    """
    low = (url or "").lower()
    dom = _recheck_domain(url)
    if not low.startswith(("http://", "https://")) or not dom:
        return True
    for b in RECHECK_BAD_DOMAINS:
        if b.lower() in dom:
            return True
    for b in RECHECK_LISTING_DOMAINS:
        b = b.lower()
        if b in {"news", "magazine"}:
            first = dom.split(".")[0]
            if first == b or first.startswith(b) or dom in {"news18.com", "magzter.com"}:
                return True
        elif b in dom:
            return True
    return False

def _recheck_query(row: dict) -> str:
    name = row.get("name", "")
    geo = " ".join(x for x in [row.get("district", ""), row.get("state", "")] if x)
    if geo:
        return f'"{name}" {geo} official website'
    return f'"{name}" official website'


def _serper_search_full(query: str) -> tuple[dict, str | None]:
    try:
        data = _serper_post({"q": query, "num": RECHECK_SERPER_NUM, "gl": "in"}, timeout=25)
        return data, None
    except Exception as e:
        return {}, str(e)




def _has_brave_key() -> bool:
    return bool((os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or "").strip())


def _brave_search_full(query: str) -> tuple[dict, str | None]:
    key = (os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or "").strip()
    if not key:
        return {}, "BRAVE_SEARCH_API_KEY is not configured"
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": key},
            params={"q": query, "country": "IN", "search_lang": "en", "count": min(20, RECHECK_SERPER_NUM), "extra_snippets": "true", "safesearch": "moderate"},
            timeout=25,
        )
        if r.status_code != 200:
            return {}, f"Brave failed {r.status_code}: {r.text[:220]}"
        return r.json(), None
    except Exception as e:
        return {}, str(e)


def _brave_candidates(data: dict) -> list[dict]:
    candidates = []
    for res in ((data.get("web") or {}).get("results") or []):
        snippets = [res.get("description") or ""] + list(res.get("extra_snippets") or [])
        candidates.append({
            "url": res.get("url") or "",
            "title": res.get("title") or "",
            "snippet": " ".join(str(x) for x in snippets if x),
            "source": "brave_web",
        })
    for res in ((data.get("locations") or {}).get("results") or []):
        url = res.get("url") or res.get("website") or ""
        if url:
            candidates.append({"url": url, "title": res.get("title") or "", "snippet": res.get("address") or "", "source": "brave_location"})
    return candidates


def _smart_provider_available(provider: str) -> bool:
    return _has_serper_keys() if provider == "serper" else (_has_brave_key() and SMART_RECHECK_USE_BRAVE)


def _smart_search_provider(provider: str, query: str) -> tuple[list[dict], str | None]:
    if provider == "brave":
        data, err = _brave_search_full(query)
        return (_brave_candidates(data or {}), err)
    data, err = _serper_search_full(query)
    return (_recheck_candidates(data or {}), err)


def _smart_reserve_query(counter: dict, provider: str) -> bool:
    if SMART_RECHECK_MAX_TOTAL_QUERIES and int(counter.get("queries", 0)) >= SMART_RECHECK_MAX_TOTAL_QUERIES:
        return False
    counter["queries"] = int(counter.get("queries", 0)) + 1
    counter[f"{provider}_queries"] = int(counter.get(f"{provider}_queries", 0)) + 1
    return True


def _smart_domain_key(url: str) -> str:
    dom = _recheck_domain(url)
    if not dom:
        return ""
    labels = dom.split(".")
    if len(labels) <= 2:
        return dom
    second_level_suffixes = {"co.in", "org.in", "net.in", "ac.in", "gov.in", "co.uk", "org.uk"}
    tail2 = ".".join(labels[-2:])
    if tail2 in second_level_suffixes and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail2


def _smart_site_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    dom = _recheck_domain(url)
    if not dom:
        return str(url or "")
    return f"{parsed.scheme or 'https'}://{dom}"


def _smart_dedupe_nominees(nominees: list[dict]) -> list[dict]:
    by_domain = {}
    order = []
    for cand in nominees:
        key = _smart_domain_key(cand.get("url", "")) or cand.get("url", "")
        if key not in by_domain:
            by_domain[key] = {**cand, "evidence_urls": [cand.get("url", "")]}
            order.append(key)
        else:
            current = by_domain[key]
            if cand.get("url") and cand.get("url") not in current["evidence_urls"]:
                current["evidence_urls"].append(cand.get("url"))
            if cand.get("score", 0) > current.get("score", 0):
                keep_urls = current["evidence_urls"]
                by_domain[key] = {**cand, "evidence_urls": keep_urls}
    return [by_domain[k] for k in order]

def _recheck_candidates(data: dict) -> list[dict]:
    candidates: list[dict] = []
    kg = data.get("knowledgeGraph") or {}
    if isinstance(kg, dict):
        website = kg.get("website") or kg.get("url")
        if website:
            candidates.append({
                "url": website,
                "title": kg.get("title", ""),
                "snippet": kg.get("description", ""),
                "source": "knowledge_graph",
            })
        for external_url in _recheck_extract_urls(kg):
            candidates.append({
                "url": external_url,
                "title": kg.get("title", ""),
                "snippet": kg.get("description", ""),
                "source": "external_url_in_result",
            })
    for res in data.get("organic", []) or []:
        title = res.get("title", "")
        snippet = res.get("snippet", "")
        candidates.append({
            "url": res.get("link", ""),
            "title": title,
            "snippet": snippet,
            "source": "organic",
        })
        # Listing/social pages are still rejected as candidates, but they often
        # disclose the real website in snippets/attributes/sitelinks. Use those
        # external URLs as separate candidates if the later scoring confirms the
        # domain matches the NGO name.
        for external_url in _recheck_extract_urls(res):
            if external_url != res.get("link", ""):
                candidates.append({
                    "url": external_url,
                    "title": title,
                    "snippet": snippet,
                    "source": "external_url_in_result",
                })
    # Some Serper responses include places/local results. Keep these low-priority candidates.
    for res in data.get("places", []) or []:
        website = res.get("website") or res.get("link") or ""
        if website:
            candidates.append({
                "url": website,
                "title": res.get("title", ""),
                "snippet": res.get("address", ""),
                "source": "places",
            })
    # De-duplicate while preserving order and evidence.
    out, seen = [], set()
    for cand in candidates:
        key = (cand.get("url", "").strip().lower(), cand.get("source", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def _score_recheck_candidate(name: str, cand: dict) -> tuple[int, str]:
    url = cand.get("url", "")
    if _recheck_bad_url(url):
        return -999, "bad/social/listing/news/document URL"
    tokens = _recheck_tokens(name)
    if not tokens:
        return -999, "no usable name tokens"
    parsed = urlparse(url.lower())
    domain = parsed.netloc.replace("www.", "")
    root = domain.split(":")[0]
    compact_domain = re.sub(r"[^a-z0-9]", "", root)
    title = (cand.get("title") or "").lower()
    snippet = (cand.get("snippet") or "").lower()
    path_parts = [p for p in parsed.path.split("/") if p]
    hit_domain = sum(1 for t in tokens if t in compact_domain)
    hit_title = sum(1 for t in tokens if t in title)
    hit_text = sum(1 for t in tokens if t in title or t in snippet)
    is_homeish = len(path_parts) <= 1
    compact_name = "".join(tokens)
    compact_bonus = 5 if compact_name and compact_name in compact_domain else 0
    source_bonus = 6 if cand.get("source") == "knowledge_graph" else (4 if cand.get("source") == "external_url_in_result" else (2 if cand.get("source") == "places" else 0))
    score = source_bonus + compact_bonus + hit_domain * 8 + hit_title * 3 + hit_text + (2 if is_homeish else 0)
    # Reject weak one-word matches to generic domains unless title/text also supports it.
    if hit_domain == 0 and not compact_bonus and not (is_homeish and hit_text >= max(1, len(tokens)//2)):
        return -50, "name/domain/title match too weak"
    if hit_domain == 1 and len(tokens) >= 2 and hit_text == 0 and cand.get("source") != "knowledge_graph":
        return -20, "only one weak domain token matched"
    return score, f"domain_hits={hit_domain}; title_hits={hit_title}; text_hits={hit_text}; source={cand.get('source','')}"


def _pick_recheck_site(name: str, data: dict) -> tuple[str, str, str, str, list[dict]]:
    scored = []
    audit = []
    for cand in _recheck_candidates(data):
        score, note = _score_recheck_candidate(name, cand)
        row = {**cand, "score": score, "note": note}
        audit.append(row)
        if score >= 6:
            scored.append(row)
    if not scored:
        return "", "no_official_website", "low", "No owned website found after Knowledge Panel + organic re-check", audit
    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]
    conf = "high" if best["score"] >= 18 or best.get("source") == "knowledge_graph" else "medium"
    return best.get("url", ""), "website_found", conf, best.get("note", ""), audit


def _write_recheck_csvs(rd: Path, result_rows: list[dict], audit_rows: list[dict]):
    with (rd / RECHECK_OUTPUTS["results"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RECHECK_FIELDS)
        writer.writeheader()
        for row in result_rows:
            writer.writerow(_safe_csv_row({k: row.get(k, "") for k in RECHECK_FIELDS}))
    with (rd / RECHECK_OUTPUTS["audit"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RECHECK_AUDIT_FIELDS)
        writer.writeheader()
        for row in audit_rows:
            writer.writerow(_safe_csv_row({k: row.get(k, "") for k in RECHECK_AUDIT_FIELDS}))

    # Confirmed/probable websites can be uploaded directly into the normal
    # repository scan. That scan applies the existing Avika-fit classifier and,
    # because a website is supplied, does not spend another Serper query.
    avika_fields = ["name", "district", "state", "darpan_id", "website", "website_recovery_status"]
    allowed = {"confirmed_official_site", "probable_official_site", "rename_verified_match"}
    with (rd / RECHECK_OUTPUTS["avika_input"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=avika_fields)
        writer.writeheader()
        for row in result_rows:
            if row.get("Website") and row.get("Website Status") in allowed:
                writer.writerow(_safe_csv_row({
                    "name": row.get("NGO Name", ""),
                    "district": row.get("District", ""),
                    "state": row.get("State", ""),
                    "darpan_id": row.get("Darpan ID", ""),
                    "website": row.get("Website", ""),
                    "website_recovery_status": row.get("Website Status", ""),
                }))

    firecrawl_fields = ["name", "district", "state", "darpan_id", "email", "phone", "registered_address", "website", "previous_website_status"]
    firecrawl_statuses = {
        "candidate_site_unreachable", "possible_site_manual_review", "needs_manual_verification",
        "no_candidate_after_completed_search", "search_incomplete", "provider_failure",
    }
    with (rd / RECHECK_OUTPUTS["firecrawl_input"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=firecrawl_fields)
        writer.writeheader()
        for row in result_rows:
            if row.get("Website Status") in firecrawl_statuses:
                writer.writerow(_safe_csv_row({
                    "name": row.get("NGO Name", ""), "district": row.get("District", ""), "state": row.get("State", ""),
                    "darpan_id": row.get("Darpan ID", ""), "email": row.get("Email", ""), "phone": row.get("Phone", ""),
                    "registered_address": row.get("Registered Address", ""), "website": row.get("Website", ""),
                    "previous_website_status": row.get("Website Status", ""),
                }))


def _run_recheck_job(run_id: str, cancel_event: threading.Event):
    rd = _run_dir(run_id)
    result_rows: list[dict] = []
    audit_rows: list[dict] = []
    try:
        rows = _read_recheck_input(rd / "uploaded_input.csv")
        total = len(rows)
        if total > RECHECK_MAX_ROWS:
            rows = rows[:RECHECK_MAX_ROWS]
            total = len(rows)
        found = 0
        missing = 0
        _write_recheck_status(rd, run_id=run_id, run_status="running", stage="searching", total=total, processed=0, website_found=0, no_official_website=0)
        for i, row in enumerate(rows, start=1):
            if _should_cancel(run_id, cancel_event):
                _write_recheck_status(rd, run_status="cancelled", stage="cancelled", processed=i-1)
                return
            q = _recheck_query(row)
            _write_recheck_status(rd, stage="searching", current_item=row.get("name", ""), current_search=q, processed=i-1, total=total)
            data, err = _serper_search_full(q)
            if err:
                missing += 1
                _append_recheck_error(rd, f"{row.get('name','')} :: {err}")
                result_rows.append({
                    "NGO Name": row.get("name", ""), "State": row.get("state", ""), "District": row.get("district", ""),
                    "Website": "", "Website Status": "search_failed", "Confidence": "low", "Source": "serper",
                    "Query": q, "Note": err[:250],
                })
                continue
            website, status, confidence, note, cands = _pick_recheck_site(row.get("name", ""), data)
            if website:
                found += 1
            else:
                missing += 1
            result_rows.append({
                "NGO Name": row.get("name", ""), "State": row.get("state", ""), "District": row.get("district", ""),
                "Website": website, "Website Status": status, "Confidence": confidence,
                "Source": (cands[0].get("source", "") if cands else ""), "Query": q, "Note": note,
            })
            for cand in cands[:10]:
                audit_rows.append({
                    "NGO Name": row.get("name", ""), "State": row.get("state", ""), "District": row.get("district", ""),
                    "Query": q, "Candidate URL": cand.get("url", ""), "Candidate Title": cand.get("title", ""),
                    "Candidate Source": cand.get("source", ""), "Score": cand.get("score", ""),
                    "Decision": "accepted" if cand.get("url") == website and website else "reviewed",
                    "Note": cand.get("note", ""),
                })
            _write_recheck_csvs(rd, result_rows, audit_rows)
            _write_recheck_status(rd, processed=i, total=total, website_found=found, no_official_website=missing)
            time.sleep(RECHECK_PACE_SEC)
        _write_recheck_csvs(rd, result_rows, audit_rows)
        _write_recheck_status(
            rd, ok=True, run_status="complete", stage="results_ready", message="No official website re-check complete",
            processed=total, total=total, website_found=found, no_official_website=missing, errors=0,
            downloads={kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()},
        )
    except Exception as e:
        _append_recheck_error(rd, f"fatal recheck error: {e}")
        _write_recheck_status(rd, ok=False, run_status="error", stage="error", error=str(e)[:500])


# ---- Smart recovery rerun helpers -------------------------------------------------
def _smart_compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _smart_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _smart_tokens(value: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split() if t]


def _smart_token_tiers(name: str) -> dict:
    tokens = [t for t in _smart_tokens(name) if len(t) > 1]
    strong = [t for t in tokens if t not in TIER_A_LEGAL and t not in TIER_B_COMMON and t not in STOPWORDS]
    tier_b = [t for t in tokens if t in TIER_B_COMMON]
    tier_a = [t for t in tokens if t in TIER_A_LEGAL]
    degenerate = False
    if not strong and tier_b:
        strong = tier_b[:]
        degenerate = True
    return {"tokens": tokens, "strong": strong, "tier_b": tier_b, "tier_a": tier_a, "degenerate": degenerate}


def _smart_name_variants(name: str) -> dict:
    tiers = _smart_token_tiers(name)
    tokens = tiers["tokens"]
    stripped = [t for t in tokens if t not in TIER_A_LEGAL]
    while stripped and stripped[0] in STOPWORDS:
        stripped = stripped[1:]
    while stripped and stripped[-1] in STOPWORDS:
        stripped = stripped[:-1]
    legal_removed = " ".join(stripped) or name
    core_brand = " ".join(tiers["strong"]) or legal_removed
    aliases = SMART_KNOWN_ALIASES.get(_smart_norm(name), []) or []
    variants = []
    seen = set()
    def add(v, vt, hint="high", query_only=False):
        v = re.sub(r"\s+", " ", str(v or "")).strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            variants.append({"variant": v, "variant_type": vt, "confidence_hint": hint, "query_only": query_only})
    add(name, "exact_name")
    add(legal_removed, "legal_suffix_removed")
    add(core_brand, "core_brand")
    if len(tiers["strong"]) >= 2:
        add(_smart_compact(legal_removed), "compact_brand", "medium")
    for a in aliases:
        add(a, "known_alias", "medium", query_only=(len(_smart_compact(a)) < 5))
    return {
        "variants": variants,
        "core_tokens": tiers["strong"],
        "tier_b_tokens": tiers["tier_b"],
        "tier_a_tokens": tiers["tier_a"],
        "compact_primary": _smart_compact(legal_removed),
        "compact_secondary": _smart_compact(" ".join([t for t in tiers["strong"] if t not in STOPWORDS])),
        "legal_suffix_removed": legal_removed,
        "aliases": aliases,
        "degenerate": tiers["degenerate"],
    }


def _smart_primary_darpan(row: dict) -> str:
    for k, v in row.items():
        key = str(k or "").lower()
        val = str(v or "").strip()
        if val and ("darpan" in key or "unique id" in key or "unique_id" in key):
            return val
    return ""


def _smart_contact_values(row: dict) -> dict:
    found = {"email": "", "phone": "", "address": "", "pincode": ""}
    for k, v in row.items():
        key = str(k or "").lower()
        val = str(v or "").strip()
        if not val:
            continue
        if not found["email"] and "email" in key and "@" in val:
            found["email"] = val
        elif not found["phone"] and any(x in key for x in ["phone", "mobile", "telephone", "contact no"]):
            digits = re.sub(r"\D", "", val)
            if len(digits) >= 8:
                found["phone"] = val
        elif not found["pincode"] and any(x in key for x in ["pincode", "pin code", "postal"]):
            if len(re.sub(r"\D", "", val)) >= 6:
                found["pincode"] = val
        elif not found["address"] and "address" in key:
            found["address"] = val
    return found


def _smart_public_brand_candidates(row: dict, vinfo: dict) -> list[str]:
    """Return likely public-facing brands without spending a search request."""
    out: list[str] = []
    seen = set()
    def add(value):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        key = _smart_norm(value)
        if value and len(_smart_compact(value)) >= 5 and key not in seen and key != _smart_norm(row.get("name", "")):
            seen.add(key); out.append(value)
    for alias in vinfo.get("aliases") or []:
        add(alias)
    for key, value in row.items():
        lk = str(key or "").lower()
        if any(token in lk for token in ["public name", "brand", "programme", "program", "school", "home name", "institution", "centre name", "center name", "project name", "alias"]):
            add(value)
    core = " ".join(vinfo.get("core_tokens") or [])
    if core and _smart_norm(core) != _smart_norm(row.get("name", "")):
        add(core)
    legal_removed = vinfo.get("legal_suffix_removed") or ""
    if legal_removed and _smart_norm(legal_removed) != _smart_norm(row.get("name", "")):
        add(legal_removed)
    return out


def _smart_queries(row: dict, vinfo: dict) -> list[dict]:
    """Build a maximum-two-query Serper plan.

    Query 1 uses the exact Darpan ID when available. Query 2 deliberately seeks
    a public brand/programme name before repeating the registered legal name.
    This improves recall for schools, homes and initiatives operated by a trust.
    """
    name = row.get("name", "")
    district = row.get("district", "")
    state = row.get("state", "")
    geo = " ".join([x for x in [district, state] if x]).strip()
    public_brands = _smart_public_brand_candidates(row, vinfo)
    out: list[dict] = []

    def add(query, p, variant, vt):
        query = re.sub(r"\s+", " ", str(query or "")).strip()
        if query and query.lower() not in {x["query"].lower() for x in out}:
            out.append({"query": query, "pass": p, "variant": variant, "variant_type": vt})

    darpan = _smart_primary_darpan(row)
    if darpan:
        add(f'"{darpan}"', "identifier", darpan, "identifier:darpan")
        if public_brands:
            brand = public_brands[0]
            add(f'"{brand}" {geo} official website', "public_brand_geo", brand, "public_brand")
        else:
            add(f'"{name}" {geo} official website', "registered_name_geo", name, "exact_name")
    else:
        add(f'"{name}" {geo} official website', "registered_name_geo", name, "exact_name")
        if public_brands:
            brand = public_brands[0]
            add(f'"{brand}" {geo} NGO website', "public_brand_geo", brand, "public_brand")
        else:
            add(f'{name} {geo}', "registered_name_broad", name, "exact_name_broad")

    return out[:max(1, SMART_RECHECK_MAX_QUERIES_PER_ROW)]

def _smart_fuzzy_ratio(a: str, b: str) -> float:
    a = _smart_compact(a); b = _smart_compact(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _smart_fuzzy_in(token: str, haystack_tokens: list[str]) -> tuple[bool, float]:
    best = 0.0
    for h in haystack_tokens:
        best = max(best, _smart_fuzzy_ratio(token, h))
    best = max(best, _smart_fuzzy_ratio(token, "".join(haystack_tokens)))
    return best >= SMART_RECHECK_FUZZY_THRESHOLD, best


def _smart_score_candidate(name: str, vinfo: dict, row: dict, cand: dict, qinfo: dict) -> tuple[int, str, str, str]:
    url = cand.get("url", "")
    if _recheck_bad_url(url):
        return -999, "bad/social/listing/news/document URL", "rejected_bad_domain", "rejected_bad_domain"
    dom = _recheck_domain(url)
    compact_domain = _smart_compact(dom)
    domain_tokens = [t for t in re.split(r"[^a-z0-9]+", dom) if t]
    title = (cand.get("title") or "").lower()
    snippet = (cand.get("snippet") or "").lower()
    carrier = (cand.get("carrier_phrase") or "").lower()
    all_text = " ".join([title, snippet, carrier])
    identifier_variant = str(qinfo.get("variant") or "") if str(qinfo.get("pass")) == "identifier" else ""
    if identifier_variant:
        identifier_compact = _smart_compact(identifier_variant)
        candidate_compact = _smart_compact(" ".join([url, title, snippet]))
        if identifier_compact and identifier_compact in candidate_compact:
            return 30, "exact identifier appears in candidate URL/title/snippet", "identifier_search", ""
    core = vinfo.get("core_tokens") or []
    tier_b = vinfo.get("tier_b_tokens") or []
    score = 0
    route = "direct"
    domain_exact = 0
    domain_fuzzy = 0
    title_hits = 0
    snippet_hits = 0
    cp = vinfo.get("compact_primary") or ""
    cs = vinfo.get("compact_secondary") or ""
    if cp and len(cp) >= 5 and cp in compact_domain:
        score += 14; route = "compact_domain"
    elif cs and len(cs) >= 5 and cs in compact_domain:
        score += 10; route = "compact_domain"
    for t in core:
        if t in compact_domain:
            domain_exact += 1; score += 8
        else:
            ok, _ = _smart_fuzzy_in(t, domain_tokens)
            if ok and domain_fuzzy < 2:
                domain_fuzzy += 1; score += 6; route = "fuzzy_spelling"
        if t in title:
            title_hits += 1; score += 3
        if t in snippet:
            snippet_hits += 1; score += 1
    tier_b_hits = 0
    for t in tier_b:
        if t in compact_domain:
            tier_b_hits += 1; score += 2
        if t in title:
            tier_b_hits += 1; score += 1
    if cand.get("source") == "knowledge_graph":
        if _smart_fuzzy_ratio(" ".join(core), cand.get("title", "")) >= SMART_RECHECK_FUZZY_THRESHOLD:
            score += 6
    elif cand.get("source") == "external_url_in_result":
        if domain_exact >= 1 and any(t in all_text for t in core):
            score += 4; route = "carrier_extracted"
        else:
            return -25, "carrier page unrelated or no strong-token domain hit", route, "carrier_unrelated"
    elif cand.get("source") == "places":
        score += 2
    if len([p for p in urlparse(url).path.split("/") if p]) <= 1:
        score += 2
    geo = " ".join([row.get("district", ""), row.get("state", "")]).lower()
    if any(g and g in all_text for g in _smart_tokens(geo)):
        score += 2
    if qinfo.get("variant_type") == "legal_suffix_removed" and domain_exact:
        route = "brand_variant"
    if qinfo.get("variant_type") == "known_alias":
        route = "associated_entity" if domain_exact == 0 else "known_alias"
    if not core and tier_b_hits:
        return -20, "only Tier-B common-token hits", route, "only_tier_b"
    if core and domain_exact + domain_fuzzy == 0 and not (title_hits >= max(1, (len(core)+1)//2)):
        return -50, "zero domain evidence and insufficient title evidence", route, "zero_domain_evidence"
    if len(core) == 1:
        tok = core[0]
        if len(tok) < 5:
            return -20, "one-token core under 5 chars", route, "short_one_token"
        if tok in GENERIC_BRAND_TOKENS and not any(g and g in all_text for g in _smart_tokens(geo)):
            return -20, "generic one-token brand without location evidence", route, "generic_one_token"
    return score, f"domain_exact={domain_exact}; domain_fuzzy={domain_fuzzy}; title_hits={title_hits}; snippet_hits={snippet_hits}; source={cand.get('source','')}", route, ""


def _smart_html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html or "")
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _smart_fetch_text(url: str) -> tuple[str, str]:
    final_url, text, _raw, err = _smart_fetch_page(url)
    return text, err

def _smart_record_identifiers(row: dict) -> list[tuple[str,str]]:
    out = []
    for k, v in row.items():
        key = str(k or "").lower(); val = str(v or "").strip()
        if len(_smart_compact(val)) >= 5 and any(x in key for x in ["darpan", "pan", "80g", "12a", "fcra", "csr", "registration", "regn", "unique"]):
            out.append((key, val))
    return out


def _smart_firecrawl_keys() -> list[str]:
    raw = (os.environ.get("FIRECRAWL_API_KEYS") or os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    out = []
    for value in re.split(r"[,\n\r]+", raw):
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def _smart_firecrawl_counter_init(counter: dict | None = None) -> dict:
    counter = counter if counter is not None else {}
    counter.setdefault("firecrawl_credits", 0)
    counter.setdefault("firecrawl_verify_credits", 0)
    counter.setdefault("firecrawl_search_credits", 0)
    counter.setdefault("firecrawl_reserve_credits", 0)
    counter.setdefault("firecrawl_scrapes", 0)
    counter.setdefault("firecrawl_searches", 0)
    counter.setdefault("firecrawl_domain_scrapes", {})
    counter.setdefault("firecrawl_budget_exhausted", False)
    return counter


def _smart_firecrawl_reserve(counter: dict | None, bucket: str, cost: int, domain: str = "") -> tuple[bool, str]:
    counter = _smart_firecrawl_counter_init(counter)
    cost = max(0, int(cost or 0))
    total = int(counter.get("firecrawl_credits", 0))
    total_cap = SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET
    bucket_key = f"firecrawl_{bucket}_credits"
    bucket_cap = {
        "verify": SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET,
        "search": SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET,
        "reserve": SMART_RECHECK_FIRECRAWL_RESERVE_CREDIT_BUDGET,
    }.get(bucket, total_cap)
    if total_cap and total + cost > total_cap:
        counter["firecrawl_budget_exhausted"] = True
        return False, "Firecrawl total credit budget exhausted"
    if bucket_cap and int(counter.get(bucket_key, 0)) + cost > bucket_cap:
        counter["firecrawl_budget_exhausted"] = True
        return False, f"Firecrawl {bucket} credit budget exhausted"
    if bucket == "verify" and domain:
        uses = int((counter.get("firecrawl_domain_scrapes") or {}).get(domain, 0))
        if uses >= SMART_RECHECK_FIRECRAWL_MAX_SCRAPES_PER_DOMAIN:
            return False, "Firecrawl per-domain scrape limit reached"
    counter["firecrawl_credits"] = total + cost
    counter[bucket_key] = int(counter.get(bucket_key, 0)) + cost
    if bucket == "verify":
        counter["firecrawl_scrapes"] = int(counter.get("firecrawl_scrapes", 0)) + 1
        if domain:
            counter["firecrawl_domain_scrapes"][domain] = int(counter["firecrawl_domain_scrapes"].get(domain, 0)) + 1
    elif bucket == "search":
        counter["firecrawl_searches"] = int(counter.get("firecrawl_searches", 0)) + 1
    return True, ""


def _smart_firecrawl_refund(counter: dict | None, bucket: str, cost: int, domain: str = "") -> None:
    if counter is None:
        return
    counter = _smart_firecrawl_counter_init(counter)
    cost = max(0, int(cost or 0))
    counter["firecrawl_credits"] = max(0, int(counter.get("firecrawl_credits", 0)) - cost)
    key = f"firecrawl_{bucket}_credits"
    counter[key] = max(0, int(counter.get(key, 0)) - cost)
    if bucket == "verify":
        counter["firecrawl_scrapes"] = max(0, int(counter.get("firecrawl_scrapes", 0)) - 1)
        if domain:
            counter["firecrawl_domain_scrapes"][domain] = max(0, int(counter["firecrawl_domain_scrapes"].get(domain, 0)) - 1)
    elif bucket == "search":
        counter["firecrawl_searches"] = max(0, int(counter.get("firecrawl_searches", 0)) - 1)


def _smart_firecrawl_scrape(url: str, counter: dict | None = None, bucket: str = "verify") -> tuple[str, list[str], str, int]:
    if not SMART_RECHECK_USE_FIRECRAWL:
        return "", [], "Firecrawl disabled", 0
    keys = _smart_firecrawl_keys()
    if not keys:
        return "", [], "No Firecrawl key configured", 0
    domain = _smart_domain_key(url)
    reserved, budget_error = _smart_firecrawl_reserve(counter, bucket, 1, domain=domain)
    if not reserved:
        return "", [], budget_error, 0
    errors = []
    for key in keys:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "url": url,
                    "formats": ["markdown", "links"],
                    "onlyMainContent": False,
                    "removeBase64Images": True,
                    "blockAds": True,
                    "proxy": SMART_RECHECK_FIRECRAWL_PROXY,
                    "parsers": [],
                    "timeout": 60000,
                },
                timeout=90,
            )
            if r.status_code in {200, 201, 202}:
                payload = r.json()
                data = payload.get("data") or payload
                markdown = str(data.get("markdown") or "")
                links = [str(x) for x in (data.get("links") or []) if str(x).startswith(("http://", "https://"))]
                return markdown, links, "", 1
            errors.append(f"{r.status_code}: {r.text[:180]}")
            if r.status_code not in {401, 402, 403, 429}:
                break
        except Exception as e:
            errors.append(str(e))
    # Firecrawl says failed requests are normally not billed; release the local reservation.
    _smart_firecrawl_refund(counter, bucket, 1, domain=domain)
    return "", [], " | ".join(errors) if errors else "Firecrawl scrape failed", 0


def _smart_firecrawl_search(query: str, counter: dict | None = None) -> tuple[list[dict], str, int]:
    if not SMART_RECHECK_USE_FIRECRAWL:
        return [], "Firecrawl disabled", 0
    keys = _smart_firecrawl_keys()
    if not keys:
        return [], "No Firecrawl key configured", 0
    reserved, budget_error = _smart_firecrawl_reserve(counter, "search", 2)
    if not reserved:
        return [], budget_error, 0
    errors = []
    for key in keys:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v2/search",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "query": query,
                    "limit": SMART_RECHECK_FIRECRAWL_SEARCH_LIMIT,
                    "country": "IN",
                    "location": "India",
                },
                timeout=60,
            )
            if r.status_code in {200, 201, 202}:
                payload = r.json()
                data = payload.get("data") or {}
                web = data.get("web") if isinstance(data, dict) else []
                if web is None and isinstance(data, list):
                    web = data
                candidates = []
                for item in web or []:
                    candidates.append({
                        "url": item.get("url") or "",
                        "title": item.get("title") or "",
                        "snippet": item.get("description") or item.get("snippet") or "",
                        "source": "firecrawl_search",
                    })
                return candidates, "", 2
            errors.append(f"{r.status_code}: {r.text[:180]}")
            if r.status_code not in {401, 402, 403, 429}:
                break
        except Exception as e:
            errors.append(str(e))
    _smart_firecrawl_refund(counter, "search", 2)
    return [], " | ".join(errors) if errors else "Firecrawl search failed", 0


def _smart_fetch_pdf_text(url: str) -> tuple[str, str, str]:
    """Download and parse a PDF locally, consuming no Firecrawl credits."""
    current = _validate_public_http_url(url)
    headers = {"User-Agent": "Mozilla/5.0 DFP2SmartRecovery/4.0"}
    for _ in range(5):
        resp = requests.get(current, headers=headers, timeout=SMART_RECHECK_FETCH_TIMEOUT, allow_redirects=False, stream=True)
        if 300 <= resp.status_code < 400 and resp.headers.get("Location"):
            current = _validate_public_http_url(urljoin(current, resp.headers["Location"]))
            continue
        resp.raise_for_status()
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > SMART_RECHECK_LOCAL_PDF_MAX_BYTES:
                raise ValueError("PDF exceeded local size limit")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw.startswith(b"%PDF") and "pdf" not in (resp.headers.get("content-type") or "").lower():
            raise ValueError("Response was not a PDF")
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise RuntimeError("pypdf is not installed") from exc
        reader = PdfReader(io.BytesIO(raw))
        texts = []
        for page in list(reader.pages)[:SMART_RECHECK_LOCAL_PDF_MAX_PAGES]:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                continue
        text = re.sub(r"\s+", " ", " ".join(texts)).strip()
        if not text:
            raise ValueError("PDF contained no extractable text")
        return current, text, ""
    raise ValueError("Too many PDF redirects")


def _smart_is_challenge_text(text: str) -> bool:
    low = (text or "").lower()
    markers = ["just a moment", "checking your browser", "verify you are human", "enable javascript and cookies", "cf-chl", "access denied"]
    return any(marker in low for marker in markers)

def _smart_fetch_variants(url: str) -> list[str]:
    """Generate conservative live-site variants without using another search query."""
    raw = str(url or "").strip()
    if not raw:
        return []
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    variants = [raw]
    if host:
        alt_host = host[4:] if host.startswith("www.") else "www." + host
        variants.append(f"https://{alt_host}{path}")
        if parsed.scheme == "https":
            variants.append(f"http://{host}{path}")
    out = []
    for candidate in variants:
        if candidate not in out:
            out.append(candidate)
    return out


def _smart_fetch_page(url: str, allow_firecrawl: bool = True, counter: dict | None = None) -> tuple[str, str, str, str]:
    """Return final URL, visible text, raw HTML, and a combined error string.

    Direct HTML and local PDF extraction run first. Firecrawl is spent only after
    those free methods fail or produce a browser-challenge shell.
    """
    errors = []
    variants = _smart_fetch_variants(url) or [url]
    for variant_index, candidate in enumerate(variants):
        attempts = SMART_RECHECK_FETCH_RETRY_ATTEMPTS if variant_index == 0 else 1
        for attempt in range(attempts):
            try:
                if urlparse(candidate).path.lower().endswith(".pdf"):
                    final_url, visible, _ = _smart_fetch_pdf_text(candidate)
                    return final_url, visible, "", ""
                final_url, raw = _safe_fetch_text(
                    candidate,
                    headers={"User-Agent": "Mozilla/5.0 DFP2SmartRecovery/4.0"},
                    timeout=SMART_RECHECK_FETCH_TIMEOUT,
                    max_bytes=2_500_000,
                )
                visible = _smart_html_to_text(raw)
                if visible and not _smart_is_challenge_text(visible):
                    return final_url, visible, raw, ""
                errors.append(f"{candidate} attempt {attempt + 1}: empty/challenge response")
            except Exception as direct_error:
                # Some PDF endpoints lack a .pdf suffix but advertise PDF content.
                if "unsupported content type" in str(direct_error).lower() and "pdf" in str(direct_error).lower():
                    try:
                        final_url, visible, _ = _smart_fetch_pdf_text(candidate)
                        return final_url, visible, "", ""
                    except Exception as pdf_error:
                        errors.append(f"{candidate} PDF parse: {pdf_error}")
                else:
                    errors.append(f"{candidate} attempt {attempt + 1}: {direct_error}")
            if attempt + 1 < attempts and SMART_RECHECK_FETCH_RETRY_BACKOFF_SEC:
                time.sleep(SMART_RECHECK_FETCH_RETRY_BACKOFF_SEC * (attempt + 1))

    if allow_firecrawl and not urlparse(url).path.lower().endswith(".pdf"):
        markdown, links, fc_error, _credits = _smart_firecrawl_scrape(url, counter=counter, bucket="verify")
        if markdown:
            raw_links = "".join(f'<a href="{u}"></a>' for u in links[:100])
            return url, markdown, raw_links, ""
        if fc_error:
            errors.append("firecrawl=" + fc_error)
    return url, "", "", " | ".join(errors)[:1800]

def _smart_target_links(raw_html: str, base_url: str) -> list[str]:
    if not raw_html:
        return []
    root_domain = _recheck_domain(base_url)
    keywords = ("about", "contact", "legal", "compliance", "governance", "annual", "report", "financial", "audit", "fcra", "registration", "80g", "12a", "trustee")
    links = []
    for match in re.finditer(r"(?is)href\s*=\s*[\"']([^\"'#]+)[\"']", raw_html):
        href = match.group(1).strip()
        full = urljoin(base_url, href)
        if _recheck_domain(full) != root_domain:
            continue
        low = full.lower()
        if any(k in low for k in keywords) or _recheck_is_document_url(full):
            if full not in links:
                links.append(full)
    return links[:8]


def _smart_record_attributes(row: dict) -> list[tuple[str, str, str]]:
    attrs = []
    for k, v in row.items():
        key = str(k or "").lower()
        val = str(v or "").strip()
        if not val:
            continue
        kind = ""
        normalized = ""
        if "email" in key and "@" in val:
            kind, normalized = "email", val.lower()
        elif any(x in key for x in ["phone", "mobile", "telephone", "contact no"]):
            digits = re.sub(r"\D", "", val)
            if len(digits) >= 8:
                kind, normalized = "phone", digits[-10:]
        elif any(x in key for x in ["pincode", "pin code", "postal"]):
            digits = re.sub(r"\D", "", val)
            if len(digits) >= 6:
                kind, normalized = "pincode", digits[-6:]
        elif "address" in key and len(_smart_compact(val)) >= 10:
            kind, normalized = "address", _smart_compact(val)
        elif any(x in key for x in ["founder", "trustee", "chairperson", "secretary"]):
            if len(_smart_compact(val)) >= 5:
                kind, normalized = "person", _smart_compact(val)
        if kind:
            attrs.append((kind, val, normalized))
    return attrs


def _smart_evaluate_pages(page_texts: list[tuple[str, str]], row: dict, errors: list[str]) -> dict:
    all_text = " ".join(t for _, t in page_texts).lower()
    compact_all = _smart_compact(all_text)
    for key, val in _smart_record_identifiers(row):
        cv = _smart_compact(val)
        if cv and cv in compact_all:
            page = next((u for u, t in page_texts if cv in _smart_compact(t)), "")
            return {"grade": "A", "type": "identifier:" + key, "matched": val, "page": page, "errors": errors}

    matched_attrs = []
    for kind, original, normalized in _smart_record_attributes(row):
        if kind == "email":
            present = normalized in all_text
        elif kind in {"phone", "pincode"}:
            present = normalized in re.sub(r"\D", "", all_text)
        else:
            present = normalized in compact_all
        if present:
            matched_attrs.append(f"{kind}:{original}")

    legal = row.get("name", "")
    legal_hit = bool(legal and _smart_compact(legal) in compact_all)
    if legal_hit and matched_attrs:
        page = next((u for u, t in page_texts if _smart_compact(legal) in _smart_compact(t)), "")
        return {"grade": "B+", "type": "legal_name_plus_attribute", "matched": legal + " | " + " | ".join(matched_attrs[:3]), "page": page, "errors": errors}
    if legal_hit:
        page = next((u for u, t in page_texts if _smart_compact(legal) in _smart_compact(t)), "")
        return {"grade": "B", "type": "registered_legal_name", "matched": legal, "page": page, "errors": errors}

    vinfo = _smart_name_variants(row.get("name", ""))
    brand_hits = [t for t in vinfo.get("core_tokens", []) if len(t) >= 3 and t in compact_all]
    geo_hits = [g for g in [row.get("district", ""), row.get("state", "")] if g and _smart_compact(g) in compact_all]
    if brand_hits and matched_attrs:
        return {"grade": "B+", "type": "brand_plus_matching_attribute", "matched": ", ".join(brand_hits + matched_attrs[:2]), "page": page_texts[0][0] if page_texts else "", "errors": errors}
    if brand_hits and geo_hits:
        return {"grade": "C", "type": "brand_plus_geo", "matched": ", ".join(brand_hits + geo_hits), "page": page_texts[0][0] if page_texts else "", "errors": errors}
    return {"grade": "D", "type": "", "matched": "", "page": "", "errors": errors}


def _smart_verify_candidate(url: str, row: dict, route: str, evidence_urls: list[str] | None = None, counter: dict | None = None, firecrawl_recovery: bool = False) -> dict:
    errors: list[str] = []
    counter = _smart_firecrawl_counter_init(counter)
    fc_before = int(counter.get("firecrawl_credits", 0))
    page_texts: list[tuple[str, str]] = []
    attempted_urls: list[str] = []
    root = _smart_site_url(url)
    queue = []
    for u in list(evidence_urls or []) + [url, root]:
        if u and u not in queue:
            queue.append(u)

    raw_by_url = {}
    while queue and len(page_texts) < min(2, SMART_RECHECK_VERIFY_MAX_PAGES):
        u = queue.pop(0)
        attempted_urls.append(u)
        final_url, text, raw, err = _smart_fetch_page(u, allow_firecrawl=firecrawl_recovery, counter=counter)
        if err:
            errors.append(f"{u}: {err}")
        if text:
            page_texts.append((final_url or u, text))
            raw_by_url[final_url or u] = raw
        time.sleep(RECHECK_PACE_SEC)

    verify = _smart_evaluate_pages(page_texts, row, errors)
    if firecrawl_recovery and verify.get("grade") not in {"A", "B+"} and root:
        markdown, links, fc_error, _credits = _smart_firecrawl_scrape(root, counter=counter, bucket="verify")
        if markdown:
            page_texts.append((root, markdown))
            raw_by_url[root] = "".join(f'<a href="{u}"></a>' for u in links[:100])
            verify = _smart_evaluate_pages(page_texts, row, errors)
        elif fc_error:
            errors.append(f"{root}: firecrawl recovery: {fc_error}")
    # When even the nominated page/homepage cannot be reached, do not hammer
    # guessed subpaths. Preserve the candidate for a later direct retry.
    if attempted_urls and not page_texts:
        verify.update({
            "fetch_attempted": len(attempted_urls),
            "fetch_succeeded": 0,
            "fetch_failed": len(attempted_urls),
            "all_fetches_failed": True,
            "fetch_status": "unreachable",
            "fetch_errors": " | ".join(errors)[:1800],
            "firecrawl_credits_used": max(0, int(counter.get("firecrawl_credits", 0)) - fc_before),
            "firecrawl_action": "candidate_verification" if int(counter.get("firecrawl_credits", 0)) > fc_before else "",
        })
        return verify

    if verify.get("grade") not in {"A", "B+"}:
        target_urls: list[tuple[str, bool]] = []
        seen_targets = set()
        for source_url, raw in raw_by_url.items():
            for link in _smart_target_links(raw, source_url):
                if link not in seen_targets:
                    seen_targets.add(link); target_urls.append((link, True))
        for path in ["/about", "/about-us", "/contact", "/contact-us", "/compliance", "/annual-reports", "/reports"]:
            candidate = urljoin(root.rstrip("/") + "/", path.lstrip("/"))
            if candidate not in seen_targets:
                seen_targets.add(candidate); target_urls.append((candidate, False))

        for u, discovered in target_urls:
            if len(page_texts) >= SMART_RECHECK_VERIFY_MAX_PAGES:
                break
            if any(existing == u for existing, _ in page_texts):
                continue
            attempted_urls.append(u)
            final_url, text, _raw, err = _smart_fetch_page(u, allow_firecrawl=firecrawl_recovery, counter=counter)
            if err:
                errors.append(f"{u}: {err}")
            if text:
                page_texts.append((final_url or u, text))
                verify = _smart_evaluate_pages(page_texts, row, errors)
                if verify.get("grade") in {"A", "B+"}:
                    break
            time.sleep(RECHECK_PACE_SEC)

    verify = _smart_evaluate_pages(page_texts, row, errors)
    verify.update({
        "fetch_attempted": len(attempted_urls),
        "fetch_succeeded": len(page_texts),
        "fetch_failed": max(0, len(attempted_urls) - len(page_texts)),
        "all_fetches_failed": bool(attempted_urls and not page_texts),
        "fetch_status": "unreachable" if attempted_urls and not page_texts else ("partial" if errors else "fetched"),
        "fetch_errors": " | ".join(errors)[:1800],
        "firecrawl_credits_used": max(0, int(counter.get("firecrawl_credits", 0)) - fc_before),
        "firecrawl_action": "candidate_verification" if int(counter.get("firecrawl_credits", 0)) > fc_before else "",
    })
    return verify

def _smart_status(route: str, grade: str) -> tuple[str, str]:
    if grade == "A":
        return ("rename_verified_match", "high") if route == "rename_detected" else ("confirmed_official_site", "high")
    if grade == "B+":
        return ("rename_verified_match", "high") if route == "rename_detected" else ("confirmed_official_site", "high")
    if grade == "B":
        return "probable_official_site", "medium"
    if grade == "C":
        return "possible_site_manual_review", "low"
    return "needs_manual_verification", "low"


def _smart_accepts_automatically(grade: str) -> bool:
    return grade in {"A", "B+"}


def _smart_is_reviewable(grade: str) -> bool:
    return grade in {"A", "B+", "B", "C"}

def _smart_result(row: dict, website: str, status: str, confidence: str, source: str, query: str, note: str, route: str, verify: dict | None = None, qinfo: dict | None = None, searched: str = "yes", queries_used: int = 0, successful_searches: int = 0, failed_searches: int = 0) -> dict:
    verify = verify or {}
    qinfo = qinfo or {}
    contacts = _smart_contact_values(row)
    return {
        "NGO Name": row.get("name", ""), "State": row.get("state", ""), "District": row.get("district", ""),
        "Darpan ID": _smart_primary_darpan(row), "Email": contacts.get("email", ""), "Phone": contacts.get("phone", ""),
        "Registered Address": contacts.get("address", ""),
        "Website": _smart_site_url(website) if website else "", "Website Status": status, "Confidence": confidence, "Source": source or "",
        "Search Provider": qinfo.get("provider", ""), "Query": query or "", "Note": note or "", "Match Route": route or "", "Evidence Grade": verify.get("grade", ""),
        "Evidence Type": verify.get("type", ""), "Evidence Matched Text": verify.get("matched", ""), "Evidence Page URL": verify.get("page", ""),
        "Query Pass": qinfo.get("pass", ""), "Variant Used": qinfo.get("variant", ""), "Variant Type": qinfo.get("variant_type", ""),
        "Searched": searched, "Queries Used": queries_used, "Successful Searches": successful_searches, "Failed Searches": failed_searches,
        "Fetch Status": verify.get("fetch_status", ""), "Fetch Errors": verify.get("fetch_errors", ""),
        "Firecrawl Credits Used": verify.get("firecrawl_credits_used", 0), "Firecrawl Action": verify.get("firecrawl_action", ""),
        "Duplicate Group Size": 1,
    }

def _smart_audit(row: dict, qinfo: dict, cand: dict | None = None, decision: str = "reviewed", note: str = "", reject: str = "", verify: dict | None = None) -> dict:
    cand = cand or {}; verify = verify or {}
    return {
        "NGO Name": row.get("name", ""), "State": row.get("state", ""), "District": row.get("district", ""), "Darpan ID": _smart_primary_darpan(row),
        "Provider": qinfo.get("provider", ""), "Query": qinfo.get("query", ""), "Query Pass": qinfo.get("pass", ""),
        "Variant Used": qinfo.get("variant", ""), "Variant Type": qinfo.get("variant_type", ""),
        "Candidate URL": cand.get("url", ""), "Candidate Domain": _recheck_domain(cand.get("url", "")), "Candidate Title": cand.get("title", ""),
        "Candidate Snippet": cand.get("snippet", ""), "Candidate Source": cand.get("source", ""), "Score": cand.get("score", ""),
        "Decision": decision, "Reject Reason": reject, "Note": note or cand.get("note", ""), "Match Route": cand.get("route", ""),
        "Evidence Grade": verify.get("grade", ""), "Evidence Type": verify.get("type", ""), "Evidence Matched Text": verify.get("matched", ""),
        "Evidence Page URL": verify.get("page", ""), "Fetch Status": verify.get("fetch_status", ""), "Fetch Errors": verify.get("fetch_errors", ""),
        "Firecrawl Credits Used": verify.get("firecrawl_credits_used", 0), "Firecrawl Action": verify.get("firecrawl_action", ""),
        "Carrier URL": cand.get("carrier_url", ""), "Carrier Phrase": cand.get("carrier_phrase", ""),
    }

def _smart_entity_keys(row: dict) -> list[str]:
    keys = []
    for _k, value in _smart_record_identifiers(row):
        compact = _smart_compact(value)
        if compact:
            keys.append("id:" + compact)
    name_key = "name:" + "|".join([_smart_norm(row.get("name", "")), _smart_norm(row.get("district", "")), _smart_norm(row.get("state", ""))])
    keys.append(name_key)
    keys.append("name:" + _smart_norm(row.get("name", "")))
    return list(dict.fromkeys(keys))


def _smart_load_entity_register(rd: Path | None = None) -> tuple[dict, str]:
    path = Path(__file__).resolve().parent / "data" / "entity_register.json"
    try:
        if not path.exists():
            return {}, ""
        raw = path.read_bytes(); sha = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw.decode("utf-8")); good = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if not (isinstance(v, dict) and all(v.get(x) for x in ["source", "approved_by", "approved_on"])):
                    print(f"entity_register entry ignored: {k}", file=sys.stderr)
                    continue
                aliases = [str(k), v.get("registered_name", ""), v.get("name", "")]
                for ident_key in ["darpan_id", "darpan", "registration_id", "fcra_number"]:
                    if v.get(ident_key):
                        good["id:" + _smart_compact(v.get(ident_key))] = v
                for alias in aliases:
                    if alias:
                        good["name:" + _smart_norm(alias)] = v
        if rd:
            (rd / "entity_register_snapshot.json").write_bytes(raw)
        return good, sha
    except Exception as e:
        print(f"entity_register ignored: {e}", file=sys.stderr)
        return {}, ""


def _smart_entity_lookup(row: dict, register: dict) -> dict | None:
    for key in _smart_entity_keys(row):
        if key in register:
            return register[key]
    return None

def _smart_write_summary(rd: Path, result_rows: list[dict], audit_rows: list[dict] | int, query_count: int, start_ts: float, cap_hit: bool, prepass: dict, entity_sha: str, errors: int = 0, counter: dict | None = None):
    counts = {}
    grades = {}
    for r in result_rows:
        counts[r.get("Website Status", "")] = counts.get(r.get("Website Status", ""), 0) + 1
        grades[r.get("Evidence Grade", "")] = grades.get(r.get("Evidence Grade", ""), 0) + 1
    searched = sum(1 for r in result_rows if str(r.get("Searched", "")).lower() == "yes")
    skipped = sum(1 for r in result_rows if str(r.get("Searched", "")).lower() == "no")
    counter = counter or {}
    summary = {
        "prepass": prepass, "status_counts": counts, "evidence_grade_counts": grades,
        "total_queries": query_count, "serper_queries": int(counter.get("serper_queries", 0)), "brave_queries": int(counter.get("brave_queries", 0)),
        "firecrawl_credits_used": int(counter.get("firecrawl_credits", 0)), "firecrawl_verify_credits": int(counter.get("firecrawl_verify_credits", 0)),
        "firecrawl_search_credits": int(counter.get("firecrawl_search_credits", 0)), "firecrawl_scrapes": int(counter.get("firecrawl_scrapes", 0)), "firecrawl_searches": int(counter.get("firecrawl_searches", 0)),
        "avg_queries_per_searched_row": round(query_count / max(1, searched), 3),
        "rows_searched": searched, "rows_skipped": skipped, "audit_rows": (audit_rows if isinstance(audit_rows, int) else len(audit_rows)),
        "env": {
            "SMART_RECHECK_MAX_QUERIES_PER_ROW": SMART_RECHECK_MAX_QUERIES_PER_ROW,
            "SMART_RECHECK_MAX_TOTAL_QUERIES": SMART_RECHECK_MAX_TOTAL_QUERIES,
            "SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW": SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW,
            "SMART_RECHECK_FETCH_RETRY_ATTEMPTS": SMART_RECHECK_FETCH_RETRY_ATTEMPTS,
            "SMART_RECHECK_FUZZY_THRESHOLD": SMART_RECHECK_FUZZY_THRESHOLD,
            "use_brave": SMART_RECHECK_USE_BRAVE, "use_firecrawl": SMART_RECHECK_USE_FIRECRAWL,
            "rename_recovery_enabled": SMART_RECHECK_ENABLE_RENAME_RECOVERY,
            "firecrawl_total_budget": SMART_RECHECK_FIRECRAWL_TOTAL_CREDIT_BUDGET,
            "firecrawl_verify_budget": SMART_RECHECK_FIRECRAWL_VERIFY_CREDIT_BUDGET,
            "firecrawl_search_budget": SMART_RECHECK_FIRECRAWL_SEARCH_CREDIT_BUDGET,
            "firecrawl_proxy": SMART_RECHECK_FIRECRAWL_PROXY,
            "brave_configured": _has_brave_key(), "firecrawl_configured": bool(_smart_firecrawl_keys()),
        },
        "entity_register_sha256": entity_sha, "run_duration_sec": round(time.time() - start_ts, 1), "cap_hit": cap_hit, "error_count": errors,
    }
    _atomic_write_text((rd / RECHECK_OUTPUTS["summary"]), json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary

def _smart_write_skipped(rd: Path, rows: list[dict]):
    with (rd / RECHECK_OUTPUTS["skipped"]).open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["name", "district", "state", "darpan_id", "email", "phone", "registered_address"]
        w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()
        for r in rows:
            w.writerow(_safe_csv_row({k: r.get(k, "") for k in fieldnames}))



# ---- Carrier-phrase rename detection -----------------------------------------
RENAME_CARRIER_PHRASES = [
    "registered as", "formerly known as", "now known as", "rebranded as",
    "became", "merged with", "acquired by", "joined", "to become"
]


def _smart_trim_brand(value: str) -> str:
    """Conservative cleanup for a brand extracted from a carrier snippet/title."""
    v = re.sub(r"\s+", " ", str(value or "")).strip(" \t\n\r:-–—|,.;()[]{}\"")
    v = re.sub(r"^(the\s+)", "", v, flags=re.I).strip()
    v = re.sub(r"\b(is|was|are|were|has been|have been|society|trust|foundation|ngo|registered|organisation|organization)\b.*$", "", v, flags=re.I).strip(" :-–—|,.;")
    # Search-result titles often look like "Feeding India - Give.do" or "Feeding India | Official Website".
    for sep in [" | ", " - ", " – ", " — ", ":"]:
        if sep in v:
            v = v.split(sep)[0].strip()
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", v) if t]
    if not toks or len("".join(toks)) < 5 or len(toks) > 6:
        return ""
    # Avoid returning the original phrase marker as a brand.
    if _smart_norm(v) in {"registered", "formerly known", "now known", "rebranded"}:
        return ""
    return " ".join(toks)


def _smart_original_present(name: str, text: str) -> bool:
    c_name = _smart_compact(name)
    c_text = _smart_compact(text)
    if c_name and c_name in c_text:
        return True
    name_tokens = [t for t in _smart_tokens(name) if t not in TIER_A_LEGAL and t not in STOPWORDS]
    if len(name_tokens) >= 2:
        return all(t in c_text for t in name_tokens[:3])
    return False


def _smart_carrier_items(data: dict) -> list[dict]:
    """Return carrier search-result texts, including listing/news/social pages as evidence carriers only."""
    items: list[dict] = []
    kg = data.get("knowledgeGraph") or {}
    if isinstance(kg, dict):
        items.append({
            "title": kg.get("title", ""),
            "snippet": kg.get("description", ""),
            "url": kg.get("website") or kg.get("url") or "",
            "raw": kg,
            "source": "knowledge_graph",
        })
    for res in data.get("organic", []) or []:
        items.append({
            "title": res.get("title", ""),
            "snippet": res.get("snippet", ""),
            "url": res.get("link", ""),
            "raw": res,
            "source": "organic_carrier",
        })
    for res in data.get("places", []) or []:
        items.append({
            "title": res.get("title", ""),
            "snippet": res.get("address", ""),
            "url": res.get("website") or res.get("link") or "",
            "raw": res,
            "source": "places_carrier",
        })
    return items


def _smart_extract_rename_brands(name: str, item: dict) -> list[dict]:
    """Extract possible public brand names from snippets that explicitly connect to the original registered name.

    This intentionally nominates only. It never accepts. The target site must later verify Grade A/B
    against the original record.
    """
    title = str(item.get("title") or "")
    snippet = str(item.get("snippet") or "")
    text = re.sub(r"\s+", " ", f"{title}. {snippet}").strip()
    low = text.lower()
    if not text or not any(p in low for p in RENAME_CARRIER_PHRASES):
        return []
    if not _smart_original_present(name, text):
        return []

    brands: list[str] = []
    original_compact = _smart_compact(name)
    title_brand = _smart_trim_brand(title)

    # Pattern A: public brand before phrase, original/legal name after phrase.
    # Example: "Feeding India is a society, registered as Hunger Heroes".
    for phrase in ["registered as", "formerly known as"]:
        idx = low.find(phrase)
        if idx >= 0:
            after = text[idx + len(phrase): idx + len(phrase) + 140]
            if _smart_original_present(name, after):
                before = text[max(0, idx - 120):idx]
                candidates = [title_brand, _smart_trim_brand(before.split(".")[-1]), _smart_trim_brand(before)]
                brands.extend([b for b in candidates if b])

    # Pattern B: original/legal name before phrase, public brand after phrase.
    # Example: "Hunger Heroes rebranded as Feeding India".
    for phrase in ["now known as", "rebranded as", "became", "merged with", "acquired by", "to become"]:
        for m in re.finditer(re.escape(phrase), low):
            before = text[max(0, m.start() - 140):m.start()]
            after = text[m.end():m.end() + 120]
            if _smart_original_present(name, before):
                b = _smart_trim_brand(after)
                if b:
                    brands.append(b)
            elif _smart_original_present(name, after):
                b = title_brand or _smart_trim_brand(before)
                if b:
                    brands.append(b)

    # Pattern C: title is the public brand, snippet carries the original registered name + phrase.
    if title_brand and _smart_compact(title_brand) != original_compact:
        brands.append(title_brand)

    out = []
    seen = set()
    carrier_phrase = text[:360]
    urls = []
    for u in _recheck_extract_urls(item.get("raw", {})):
        if u not in urls:
            urls.append(u)
    # Include KG/places website only as possible direct URL; normal organic listing URL remains carrier evidence.
    if item.get("source") in {"knowledge_graph", "places_carrier"} and item.get("url"):
        urls.insert(0, item.get("url"))
    for b in brands:
        nb = _smart_norm(b)
        if not nb or nb == _smart_norm(name) or nb in seen:
            continue
        seen.add(nb)
        out.append({"brand": b, "carrier_url": item.get("url", ""), "carrier_phrase": carrier_phrase, "external_urls": urls})
    return out[:3]


def _smart_verify_rename_nominee(url: str, row: dict) -> dict:
    verify = _smart_verify_candidate(url, row, "rename_detected")
    # Rename route is intentionally strict: legal-name-only or brand+geo is not enough.
    if verify.get("grade") in {"B", "C"}:
        verify = {**verify, "grade": "D", "type": "rename_requires_identifier_or_matching_attribute", "matched": verify.get("matched", "")}
    return verify


def _smart_rename_recovery(row: dict, audit_rows: list[dict], counter: dict) -> dict | None:
    name = row.get("name", "")
    if not name:
        return None
    if SMART_RECHECK_MAX_TOTAL_QUERIES and counter["queries"] >= SMART_RECHECK_MAX_TOTAL_QUERIES:
        return None
    qinfo = {"query": f'"{name}" registered OR society OR trust OR foundation', "pass": "rename", "variant": name, "variant_type": "rename_carrier", "provider": "serper"}
    if not _smart_reserve_query(counter, "serper"):
        return None
    data, err = _serper_search_full(qinfo["query"])
    if err:
        audit_rows.append(_smart_audit(row, qinfo, decision="search_failed", note="rename carrier search failed: " + err[:220]))
        return None
    carrier_hits: list[dict] = []
    for item in _smart_carrier_items(data or {}):
        carrier_hits.extend(_smart_extract_rename_brands(name, item))
    if not carrier_hits:
        audit_rows.append(_smart_audit(row, qinfo, decision="no_candidate", note="Rename carrier pass found no phrase linking original name to a public brand"))
        return None

    best_manual = None
    seen_brands = set()
    for hit in carrier_hits[:4]:
        brand = hit.get("brand", "")
        if _smart_norm(brand) in seen_brands:
            continue
        seen_brands.add(_smart_norm(brand))
        carrier_url = hit.get("carrier_url", "")
        carrier_phrase = hit.get("carrier_phrase", "")

        # First try any direct official-looking URL exposed by the carrier result.
        direct_urls = [u for u in hit.get("external_urls", []) if u and not _recheck_bad_url(u)]
        for u in direct_urls[:2]:
            cand = {"url": u, "title": brand, "snippet": carrier_phrase, "source": "rename_carrier_url", "score": "", "route": "rename_detected", "carrier_url": carrier_url, "carrier_phrase": carrier_phrase, "note": f"rename carrier extracted brand {brand}"}
            verify = _smart_verify_rename_nominee(u, row)
            audit_rows.append(_smart_audit(row, qinfo, cand, decision="accepted" if verify.get("grade") in {"A","B+"} else "nominated_not_verified", verify=verify, note="direct URL from carrier phrase"))
            if verify.get("grade") in {"A", "B+"}:
                status, conf = _smart_status("rename_detected", verify.get("grade"))
                return _smart_result(row, u, status, conf, "rename_carrier_url", qinfo["query"], f"rename detected via carrier phrase; extracted brand: {brand}", "rename_detected", verify, qinfo, searched="yes", queries_used=1)
            if not best_manual:
                best_manual = {"cand": cand, "verify": verify, "brand": brand, "qinfo": qinfo}

        # If no direct URL closed the loop, search the extracted brand's official website.
        if SMART_RECHECK_MAX_TOTAL_QUERIES and counter["queries"] >= SMART_RECHECK_MAX_TOTAL_QUERIES:
            break
        target_qinfo = {"query": f'"{brand}" official website', "pass": "rename_target", "variant": brand, "variant_type": "rename_detected", "provider": "serper"}
        if not _smart_reserve_query(counter, "serper"):
            break
        target_data, target_err = _serper_search_full(target_qinfo["query"])
        if target_err:
            audit_rows.append(_smart_audit(row, target_qinfo, decision="search_failed", note="rename target search failed: " + target_err[:220]))
            continue
        brand_vinfo = _smart_name_variants(brand)
        cands = _recheck_candidates(target_data or {})
        if not cands:
            audit_rows.append(_smart_audit(row, target_qinfo, decision="no_candidate", note="Rename target search returned no candidates"))
            continue
        nominees = []
        brand_row = {**row, "name": brand}
        for cand0 in cands[:10]:
            score, note, route, reject = _smart_score_candidate(brand, brand_vinfo, brand_row, cand0, target_qinfo)
            cand = {**cand0, "score": score, "note": note + f"; rename carrier brand={brand}", "route": "rename_detected", "carrier_url": carrier_url, "carrier_phrase": carrier_phrase}
            if score >= SMART_RECHECK_NOMINATION_SCORE:
                nominees.append(cand)
                audit_rows.append(_smart_audit(row, target_qinfo, cand, decision="nominated_not_verified"))
            else:
                audit_rows.append(_smart_audit(row, target_qinfo, cand, decision="rejected_bad_domain" if score == -999 else "rejected_weak_match", reject=reject, note=note))
        nominees.sort(key=lambda x: x.get("score", 0), reverse=True)
        for cand in nominees[:SMART_RECHECK_MAX_VERIFY_PER_ROW]:
            verify = _smart_verify_rename_nominee(cand.get("url", ""), row)
            audit_rows.append(_smart_audit(row, target_qinfo, cand, decision="accepted" if verify.get("grade") in {"A","B+"} else "nominated_not_verified", verify=verify))
            if verify.get("grade") in {"A", "B+"}:
                status, conf = _smart_status("rename_detected", verify.get("grade"))
                return _smart_result(row, cand.get("url", ""), status, conf, cand.get("source", ""), target_qinfo["query"], f"rename detected via carrier phrase; extracted brand: {brand}", "rename_detected", verify, target_qinfo, searched="yes", queries_used=2)
            if not best_manual:
                best_manual = {"cand": cand, "verify": verify, "brand": brand, "qinfo": target_qinfo}
        time.sleep(RECHECK_PACE_SEC)
    if best_manual:
        cand = best_manual["cand"]
        return _smart_result(row, cand.get("url", ""), "needs_manual_verification", "low", cand.get("source", "rename_recovery"), best_manual["qinfo"].get("query", ""), f"rename carrier suggested brand {best_manual.get('brand')}, but target site did not verify original registered identity", "rename_detected", best_manual.get("verify"), best_manual.get("qinfo"), searched="yes", queries_used=2)
    return None


def _smart_process_row(row: dict, rd: Path, audit_rows: list[dict], counter: dict) -> dict:
    q_used = 0
    successful_searches = 0
    failed_searches = 0
    best_review = None
    best_unreachable = None

    # Direct candidates from explicit website fields or an organisational email domain.
    direct_urls = []
    source_url = row.get("website") or row.get("Website") or row.get("url") or row.get("URL") or row.get("Website / Source") or ""
    if source_url and str(source_url).startswith(("http://", "https://")):
        direct_urls.append((str(source_url), "source_registry"))
    email = _smart_contact_values(row).get("email") or ""
    if "@" in email:
        domain = email.rsplit("@", 1)[-1].strip().lower()
        if domain and domain not in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com"}:
            direct_urls.append((f"https://{domain}", "email_domain"))

    for direct_url, direct_route in direct_urls:
        verify = _smart_verify_candidate(direct_url, row, direct_route, counter=counter)
        grade = verify.get("grade", "D")
        if verify.get("all_fetches_failed"):
            best_unreachable = {
                "url": direct_url, "source": direct_route, "query": "", "route": direct_route,
                "verify": verify, "qinfo": {}, "score": 100,
            }
            if str(row.get("previous_website_status") or "").lower() == "candidate_site_unreachable":
                return _smart_result(
                    row, direct_url, "candidate_site_unreachable", "low", direct_route, "",
                    "Candidate domain is still unreachable after direct retry; no additional Serper query was used.",
                    direct_route, verify, searched="no", queries_used=0,
                )
        elif _smart_is_reviewable(grade):
            status, conf = _smart_status(direct_route, grade)
            result = _smart_result(row, direct_url, status, conf, direct_route, "", "Direct identity candidate verified on-site", direct_route, verify, searched="no", queries_used=0)
            if _smart_accepts_automatically(grade) or grade == "B":
                return result
            best_review = {"result": result, "score": 100 if grade == "C" else 0}

    vinfo = _smart_name_variants(row.get("name", ""))
    all_queries = _smart_queries(row, vinfo)
    provider_plan = []
    if _smart_provider_available("serper"):
        provider_plan.append(("serper", all_queries))
    if _smart_provider_available("brave"):
        brave_queries = []
        for q in all_queries:
            if q.get("pass") in {"identifier", 1, 2} and len(brave_queries) < SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW:
                brave_queries.append(q)
        if not brave_queries:
            brave_queries = all_queries[:SMART_RECHECK_BRAVE_MAX_QUERIES_PER_ROW]
        provider_plan.append(("brave", brave_queries))

    if not provider_plan:
        return _smart_result(row, "", "provider_failure", "low", "", "", "Neither Serper nor Brave is configured", "", searched="no")

    for provider, queries in provider_plan:
        for base_qinfo in queries:
            if not _smart_reserve_query(counter, provider):
                return _smart_result(row, "", "skipped_query_cap", "low", provider, "", f"run query cap reached at {counter.get('queries', 0)} queries", "", searched="no", queries_used=q_used, successful_searches=successful_searches, failed_searches=failed_searches)
            q_used += 1
            qinfo = {**base_qinfo, "provider": provider}
            cands, err = _smart_search_provider(provider, qinfo["query"])
            if err:
                failed_searches += 1
                audit_rows.append(_smart_audit(row, qinfo, decision="search_failed", note=err[:250]))
                continue
            successful_searches += 1
            if not cands:
                audit_rows.append(_smart_audit(row, qinfo, decision="no_candidate", note=f"{provider} returned zero candidate URLs"))
                continue

            nominees = []
            for cand0 in cands[:12]:
                score, note, route, reject = _smart_score_candidate(row.get("name", ""), vinfo, row, cand0, qinfo)
                cand = {**cand0, "score": score, "note": note, "route": route}
                if score >= SMART_RECHECK_NOMINATION_SCORE:
                    nominees.append(cand)
                    audit_rows.append(_smart_audit(row, qinfo, cand, decision="nominated_not_verified"))
                else:
                    audit_rows.append(_smart_audit(row, qinfo, cand, decision="rejected_bad_domain" if score == -999 else "rejected_weak_match", reject=reject, note=note))

            nominees.sort(key=lambda x: x.get("score", 0), reverse=True)
            nominees = _smart_dedupe_nominees(nominees)
            for cand in nominees[:SMART_RECHECK_MAX_VERIFY_PER_ROW]:
                verify = _smart_verify_candidate(cand.get("url", ""), row, cand.get("route", "direct"), cand.get("evidence_urls"), counter=counter)
                grade = verify.get("grade", "D")
                decision = "accepted" if _smart_accepts_automatically(grade) else ("probable" if grade == "B" else "nominated_not_verified")
                audit_rows.append(_smart_audit(row, qinfo, cand, decision=decision, verify=verify))
                if verify.get("all_fetches_failed"):
                    if not best_unreachable or cand.get("score", 0) > best_unreachable.get("score", 0):
                        best_unreachable = {
                            "url": cand.get("url", ""), "source": cand.get("source", ""),
                            "query": qinfo.get("query", ""), "route": cand.get("route", "direct"),
                            "verify": verify, "qinfo": qinfo, "score": cand.get("score", 0),
                        }
                elif _smart_is_reviewable(grade):
                    status, conf = _smart_status(cand.get("route", "direct"), grade)
                    result = _smart_result(row, cand.get("url", ""), status, conf, cand.get("source", ""), qinfo.get("query", ""), cand.get("note", ""), cand.get("route", "direct"), verify, qinfo, searched="yes", queries_used=q_used, successful_searches=successful_searches, failed_searches=failed_searches)
                    if _smart_accepts_automatically(grade) or grade == "B":
                        return result
                    if not best_review or cand.get("score", 0) > best_review.get("score", 0):
                        best_review = {"result": result, "score": cand.get("score", 0)}
            time.sleep(RECHECK_PACE_SEC)

    # Rename recovery is available only as an explicit paid opt-in because it
    # can exceed the two-query budget.
    if SMART_RECHECK_ENABLE_RENAME_RECOVERY and _smart_provider_available("serper"):
        rename_result = _smart_rename_recovery(row, audit_rows, counter)
        if rename_result:
            return rename_result

    if best_review:
        return best_review["result"]
    if best_unreachable:
        return _smart_result(
            row, best_unreachable.get("url", ""), "candidate_site_unreachable", "low",
            best_unreachable.get("source", ""), best_unreachable.get("query", ""),
            "A plausible candidate domain was found, but all direct fetch retries failed. Retry later; this is not a no-website conclusion.",
            best_unreachable.get("route", "direct"), best_unreachable.get("verify"), best_unreachable.get("qinfo"),
            searched="yes" if q_used else "no", queries_used=q_used,
            successful_searches=successful_searches, failed_searches=failed_searches,
        )
    if successful_searches == 0 and failed_searches > 0:
        return _smart_result(row, "", "provider_failure", "low", "serper/brave", "", "All configured search-provider requests failed; no website conclusion was made", "", searched="yes", queries_used=q_used, successful_searches=successful_searches, failed_searches=failed_searches)
    if failed_searches > 0:
        return _smart_result(row, "", "search_incomplete", "low", "serper/brave", "", "Some required searches failed; rerun before treating this NGO as not found", "", searched="yes", queries_used=q_used, successful_searches=successful_searches, failed_searches=failed_searches)
    return _smart_result(row, "", "no_candidate_after_completed_search", "low", "serper/brave", "", "No confirmed candidate located after completed staged searches", "", searched="yes", queries_used=q_used, successful_searches=successful_searches, failed_searches=failed_searches)

def _smart_firecrawl_recovery_query(row: dict) -> dict:
    vinfo = _smart_name_variants(row.get("name", ""))
    brands = _smart_public_brand_candidates(row, vinfo)
    brand = brands[0] if brands else (vinfo.get("legal_suffix_removed") or row.get("name", ""))
    geo = " ".join(x for x in [row.get("district", ""), row.get("state", "")] if x).strip()
    query = re.sub(r"\s+", " ", f'"{brand}" {geo} NGO school trust').strip()
    return {"query": query, "pass": "firecrawl_public_brand", "variant": brand, "variant_type": "firecrawl_recovery", "provider": "firecrawl"}


def _smart_process_firecrawl_row(row: dict, rd: Path, audit_rows: list[dict], counter: dict) -> dict:
    """Zero-Serper recovery: verify a known domain or spend one Firecrawl Search."""
    start_credits = int(counter.get("firecrawl_credits", 0))

    def finish(result: dict, action: str) -> dict:
        result["Firecrawl Credits Used"] = max(0, int(counter.get("firecrawl_credits", 0)) - start_credits)
        result["Firecrawl Action"] = action
        return result

    direct_url = row.get("website") or row.get("Website") or row.get("url") or row.get("URL") or ""
    if direct_url and str(direct_url).startswith(("http://", "https://")):
        verify = _smart_verify_candidate(str(direct_url), row, "firecrawl_candidate_recovery", counter=counter, firecrawl_recovery=True)
        grade = verify.get("grade", "D")
        if _smart_is_reviewable(grade):
            status, conf = _smart_status("firecrawl_candidate_recovery", grade)
            return finish(_smart_result(
                row, str(direct_url), status, conf, "firecrawl", "",
                "Known candidate rechecked with direct fetch, local PDF parsing and selective Firecrawl.",
                "firecrawl_candidate_recovery", verify, {"provider":"firecrawl","pass":"candidate_recovery"},
                searched="no", queries_used=0,
            ), "candidate_verification")
        if verify.get("all_fetches_failed"):
            status = "firecrawl_budget_exhausted" if counter.get("firecrawl_budget_exhausted") else "candidate_site_unreachable"
            note = "Firecrawl credit budget was exhausted before candidate verification completed." if status == "firecrawl_budget_exhausted" else "Candidate remains unreachable after selective recovery."
            return finish(_smart_result(
                row, str(direct_url), status, "low", "firecrawl", "", note,
                "firecrawl_candidate_recovery", verify, {"provider":"firecrawl","pass":"candidate_recovery"},
                searched="no", queries_used=0,
            ), "candidate_verification")
        return finish(_smart_result(
            row, str(direct_url), "needs_manual_verification", "low", "firecrawl", "",
            "Candidate was readable but identity could not be closed.", "firecrawl_candidate_recovery",
            verify, {"provider":"firecrawl","pass":"candidate_recovery"}, searched="no", queries_used=0,
        ), "candidate_verification")

    if SMART_RECHECK_FIRECRAWL_MAX_SEARCHES_PER_NGO <= 0:
        return finish(_smart_result(
            row, "", "firecrawl_search_disabled", "low", "firecrawl", "",
            "No candidate domain was supplied and Firecrawl Search is disabled.",
            "firecrawl_search", searched="no", queries_used=0,
        ), "none")

    qinfo = _smart_firecrawl_recovery_query(row)
    candidates, error, _credits = _smart_firecrawl_search(qinfo["query"], counter=counter)
    if error:
        status = "firecrawl_budget_exhausted" if "budget" in error.lower() else "firecrawl_provider_failure"
        audit_rows.append(_smart_audit(row, qinfo, decision="search_failed", note=error))
        return finish(_smart_result(
            row, "", status, "low", "firecrawl", qinfo["query"], error,
            "firecrawl_search", qinfo=qinfo, searched="yes", queries_used=0,
        ), "search")
    if not candidates:
        audit_rows.append(_smart_audit(row, qinfo, decision="no_candidate", note="Firecrawl Search returned zero URLs"))
        return finish(_smart_result(
            row, "", "no_candidate_after_firecrawl_search", "low", "firecrawl", qinfo["query"],
            "No candidate located after the selected Firecrawl recovery search.",
            "firecrawl_search", qinfo=qinfo, searched="yes", queries_used=0,
        ), "search")

    vinfo = _smart_name_variants(row.get("name", ""))
    nominees = []
    for candidate in candidates[:SMART_RECHECK_FIRECRAWL_SEARCH_LIMIT]:
        score, note, route, reject = _smart_score_candidate(row.get("name", ""), vinfo, row, candidate, qinfo)
        candidate = {**candidate, "score": score, "note": note, "route": route or "firecrawl_search"}
        if score >= SMART_RECHECK_NOMINATION_SCORE:
            nominees.append(candidate)
            audit_rows.append(_smart_audit(row, qinfo, candidate, decision="nominated_not_verified"))
        else:
            audit_rows.append(_smart_audit(row, qinfo, candidate, decision="rejected_weak_match", reject=reject, note=note))
    nominees = _smart_dedupe_nominees(sorted(nominees, key=lambda x: x.get("score", 0), reverse=True))
    best_review = None
    best_unreachable = None
    for candidate in nominees[:SMART_RECHECK_FIRECRAWL_SEARCH_MAX_VERIFY]:
        verify = _smart_verify_candidate(
            candidate.get("url", ""), row, "firecrawl_search", candidate.get("evidence_urls"),
            counter=counter, firecrawl_recovery=True,
        )
        grade = verify.get("grade", "D")
        audit_rows.append(_smart_audit(
            row, qinfo, candidate,
            decision="accepted" if _smart_accepts_automatically(grade) else "nominated_not_verified",
            verify=verify,
        ))
        if _smart_is_reviewable(grade):
            status, conf = _smart_status("firecrawl_search", grade)
            result = _smart_result(
                row, candidate.get("url", ""), status, conf, "firecrawl_search", qinfo["query"],
                candidate.get("note", ""), "firecrawl_search", verify, qinfo,
                searched="yes", queries_used=0,
            )
            if _smart_accepts_automatically(grade) or grade == "B":
                return finish(result, "search_and_verify")
            best_review = result
        elif verify.get("all_fetches_failed"):
            best_unreachable = (candidate, verify)
    if best_review:
        return finish(best_review, "search_and_verify")
    if best_unreachable:
        candidate, verify = best_unreachable
        return finish(_smart_result(
            row, candidate.get("url", ""), "candidate_site_unreachable", "low", "firecrawl_search",
            qinfo["query"], "Firecrawl Search found a plausible candidate, but the site could not be verified.",
            "firecrawl_search", verify, qinfo, searched="yes", queries_used=0,
        ), "search_and_verify")
    if counter.get("firecrawl_budget_exhausted"):
        return finish(_smart_result(
            row, "", "firecrawl_budget_exhausted", "low", "firecrawl", qinfo["query"],
            "Firecrawl credit budget was exhausted.", "firecrawl_search", qinfo=qinfo,
            searched="yes", queries_used=0,
        ), "search")
    return finish(_smart_result(
        row, "", "no_candidate_after_firecrawl_search", "low", "firecrawl", qinfo["query"],
        "Firecrawl Search completed but no candidate passed identity nomination.",
        "firecrawl_search", qinfo=qinfo, searched="yes", queries_used=0,
    ), "search")


def _run_smart_recheck_job(run_id: str, cancel_event: threading.Event, strategy_name: str = "smart"):
    rd = _run_dir(run_id)
    session_start = time.time()
    previous_status = {}
    try:
        previous_status = json.loads(_recheck_status_path(rd).read_text(encoding="utf-8"))
    except Exception:
        previous_status = {}
    first_start_ts = float(previous_status.get("started_at_epoch") or session_start)
    active_elapsed_before = float(previous_status.get("active_elapsed_sec") or 0.0)
    result_rows: list[dict] = _recheck_read_all_csv(rd / RECHECK_OUTPUTS["results"])
    processed_keys = {_recheck_identity_key(r, result_row=True) for r in result_rows}
    audit_count = _count_export_records(rd / RECHECK_OUTPUTS["audit"])
    skipped_rows: list[dict] = _recheck_read_all_csv(rd / RECHECK_OUTPUTS["skipped"])
    cap_hit = bool(previous_status.get("cap_hit")); errors = int(previous_status.get("errors") or 0)
    counter = _recheck_load_counter(rd, strategy_name)

    try:
        rows = _read_recheck_input(rd / "uploaded_input.csv")[:RECHECK_MAX_ROWS]
        total = len(rows)
        entity_register, entity_sha = _smart_load_entity_register(rd)
        prepass = {
            "uploaded_rows": total,
            "entity_register_hits": sum(1 for r in result_rows if str(r.get("Source") or "") == "entity_register"),
            "unique_search_rows": 0,
        }
        search_rows = []
        for row in rows:
            if _recheck_identity_key(row) in processed_keys:
                continue
            ent = _smart_entity_lookup(row, entity_register)
            if ent:
                prepass["entity_register_hits"] += 1
                result = _smart_result(
                    row, ent.get("website", ""), "alias_or_associated_entity_needs_review", "medium",
                    "entity_register", "", ent.get("notes", "Entity register match; review before ranking"),
                    "entity_register", {"grade":"register", "type": ent.get("relation", ""), "matched": ent.get("source", ""), "page": ent.get("source", "")},
                    searched="no", queries_used=0,
                )
                result_rows.append(result)
                processed_keys.add(_recheck_identity_key(result, result_row=True))
                _recheck_append_checkpoint(rd, result, [])
            else:
                search_rows.append(row)
        if strategy_name == "firecrawl":
            priority = {
                "candidate_site_unreachable": 0, "possible_site_manual_review": 1, "needs_manual_verification": 1,
                "search_incomplete": 2, "provider_failure": 2, "no_candidate_after_completed_search": 3,
            }
            search_rows.sort(key=lambda r: priority.get(str(r.get("previous_website_status") or r.get("Website Status") or "").lower(), 4))
        prepass["unique_search_rows"] = total - prepass["entity_register_hits"]

        active_elapsed = active_elapsed_before + (time.time() - session_start)
        progress = _recheck_progress_payload(len(result_rows), total, active_elapsed)
        _write_recheck_status(
            rd, run_id=run_id, run_status="running", stage="firecrawl_recovery" if strategy_name == "firecrawl" else "smart_recovery",
            strategy=strategy_name, prepass=prepass, first_started_at_epoch=first_start_ts,
            started_at_epoch=first_start_ts, **progress,
        )

        for row in search_rows:
            action = _recheck_control_action(rd, run_id, cancel_event)
            if action:
                active_elapsed = active_elapsed_before + (time.time() - session_start)
                progress = _recheck_progress_payload(len(result_rows), total, active_elapsed)
                summary = _smart_write_summary(rd, result_rows, audit_count, counter["queries"], first_start_ts, cap_hit, prepass, entity_sha, errors, counter)
                if action == "pause":
                    _write_recheck_status(
                        rd, ok=True, run_status="paused", stage="paused", current_item="Paused safely after the last completed NGO.",
                        strategy=strategy_name, summary=summary, queries_used=counter["queries"], firecrawl_credits_used=counter.get("firecrawl_credits", 0),
                        downloads={kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()}, **progress,
                    )
                    _job_update(run_id, status="paused", stage="paused")
                else:
                    _write_recheck_status(
                        rd, ok=True, run_status="stopped", stage="stopped_partial", current_item="Search ended safely. Partial outputs are ready and the run can be resumed.",
                        strategy=strategy_name, summary=summary, queries_used=counter["queries"], firecrawl_credits_used=counter.get("firecrawl_credits", 0),
                        downloads={kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()}, **progress,
                    )
                    _job_update(run_id, status="stopped", stage="stopped_partial")
                return

            if strategy_name != "firecrawl" and SMART_RECHECK_MAX_TOTAL_QUERIES and counter["queries"] >= SMART_RECHECK_MAX_TOTAL_QUERIES:
                cap_hit = True
                for rem in search_rows[search_rows.index(row):]:
                    if _recheck_identity_key(rem) in processed_keys:
                        continue
                    skipped_rows.append(rem)
                    result = _smart_result(rem, "", "skipped_query_cap", "low", "serper", "", f"run query cap reached at {counter['queries']} queries", "", searched="no", queries_used=0)
                    result_rows.append(result)
                    processed_keys.add(_recheck_identity_key(result, result_row=True))
                    _recheck_append_checkpoint(rd, result, [])
                _smart_write_skipped(rd, skipped_rows)
                break

            active_elapsed = active_elapsed_before + (time.time() - session_start)
            progress = _recheck_progress_payload(len(result_rows), total, active_elapsed)
            _write_recheck_status(
                rd, stage="firecrawl_recovery" if strategy_name == "firecrawl" else "smart_recovery",
                current_item=row.get("name", ""), current_search="", queries_used=counter["queries"],
                firecrawl_credits_used=counter.get("firecrawl_credits", 0), strategy=strategy_name, **progress,
            )
            row_audit: list[dict] = []
            try:
                result = _smart_process_firecrawl_row(row, rd, row_audit, counter) if strategy_name == "firecrawl" else _smart_process_row(row, rd, row_audit, counter)
            except Exception as e:
                errors += 1
                _append_recheck_error(rd, f"{row.get('name','')} smart error: {e}")
                result = _smart_result(row, "", "firecrawl_provider_failure" if strategy_name == "firecrawl" else "search_failed", "low", strategy_name, "", str(e)[:250], "", searched="yes", queries_used=0)
            if result.get("Website Status") == "skipped_query_cap":
                cap_hit = True
                skipped_rows.append(row)
            result_rows.append(result)
            processed_keys.add(_recheck_identity_key(result, result_row=True))
            audit_count += len(row_audit)
            _recheck_append_checkpoint(rd, result, row_audit)
            _smart_write_skipped(rd, skipped_rows)
            active_elapsed = active_elapsed_before + (time.time() - session_start)
            progress = _recheck_progress_payload(len(result_rows), total, active_elapsed)
            summary = _smart_write_summary(rd, result_rows, audit_count, counter["queries"], first_start_ts, cap_hit, prepass, entity_sha, errors, counter)
            _write_recheck_status(
                rd, run_status="running", queries_used=counter["queries"], cap_hit=cap_hit,
                summary=summary, errors=errors, firecrawl_credits_used=counter.get("firecrawl_credits", 0), strategy=strategy_name, **progress,
            )
            time.sleep(RECHECK_PACE_SEC)

        active_elapsed = active_elapsed_before + (time.time() - session_start)
        progress = _recheck_progress_payload(len(result_rows), total, active_elapsed)
        summary = _smart_write_summary(rd, result_rows, audit_count, counter["queries"], first_start_ts, cap_hit, prepass, entity_sha, errors, counter)
        _write_recheck_status(
            rd, ok=True, run_status="complete", stage="results_ready_partial" if cap_hit else "results_ready",
            message="Firecrawl recovery complete" if strategy_name == "firecrawl" else "Smart website recovery complete",
            queries_used=counter["queries"], cap_hit=cap_hit, summary=summary, errors=errors,
            firecrawl_credits_used=counter.get("firecrawl_credits", 0), strategy=strategy_name,
            downloads={kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()}, **progress,
        )
        _recheck_pause_path(rd).unlink(missing_ok=True)
        _recheck_stop_path(rd).unlink(missing_ok=True)
    except Exception as e:
        active_elapsed = active_elapsed_before + (time.time() - session_start)
        _append_recheck_error(rd, f"fatal smart recheck error: {e}")
        _write_recheck_status(rd, ok=False, run_status="error", stage="error", error=str(e)[:500], strategy=strategy_name, **_recheck_progress_payload(len(result_rows), len(_read_recheck_input(rd / "uploaded_input.csv")), active_elapsed))


def _run_firecrawl_recovery_job(run_id: str, cancel_event: threading.Event):
    return _run_smart_recheck_job(run_id, cancel_event, strategy_name="firecrawl")


@app.post("/repository/recheck/start")
async def recheck_start(file: UploadFile = File(...), strategy: str = "smart"):
    strategy = (strategy or "smart").strip().lower()
    if strategy not in {"classic", "smart", "firecrawl"}:
        return _json(False, status_code=400, stage="bad_strategy", error="strategy must be classic, smart or firecrawl")
    if strategy == "classic" and not _has_serper_keys():
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS must be set")
    if strategy == "smart" and not (_has_serper_keys() or (_has_brave_key() and SMART_RECHECK_USE_BRAVE)):
        return _json(False, status_code=500, stage="missing_env", error="Configure SERPER_API_KEY(S). Brave is optional and disabled by default.")
    if strategy == "firecrawl" and (not SMART_RECHECK_USE_FIRECRAWL or not _smart_firecrawl_keys()):
        return _json(False, status_code=503, stage="firecrawl_not_configured", error="Firecrawl recovery requires SMART_RECHECK_USE_FIRECRAWL=true and FIRECRAWL_API_KEY(S)")
    active = [rid for rid, th in list(recheck_threads.items()) if th.is_alive()]
    if active:
        return _json(False, status_code=409, stage="another_recheck_active", error="Another no-website re-check is already active", active_runs=active)
    run_id = f"recheck_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    uploaded = rd / "uploaded_input.csv"
    try:
        upload_bytes = _save_upload_with_limit(file, uploaded, max_bytes=MAX_UPLOAD_BYTES)
        rows = _read_recheck_input(uploaded)
    except Exception as e:
        _write_recheck_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="bad_csv", error=str(e))
        return _json(False, status_code=400, run_id=run_id, stage="bad_csv", error=str(e))
    if not rows:
        msg = "Upload a CSV with NGO names. Header 'name' is preferred, but a one-column file also works."
        _write_recheck_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="empty_csv", error=msg, strategy=strategy)
        return _json(False, status_code=400, run_id=run_id, stage="empty_csv", error=msg)
    if len(rows) > RECHECK_MAX_ROWS:
        msg = f"Re-check allows up to {RECHECK_MAX_ROWS} rows per run. Split this file."
        _write_recheck_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="too_many_rows", error=msg, row_count=len(rows), strategy=strategy)
        return _json(False, status_code=400, run_id=run_id, stage="too_many_rows", error=msg, row_count=len(rows))
    _recheck_initialize_outputs(rd)
    _recheck_pause_path(rd).unlink(missing_ok=True)
    _recheck_stop_path(rd).unlink(missing_ok=True)
    ev = threading.Event()
    recheck_cancel_flags[run_id] = ev
    _write_recheck_status(
        rd, ok=True, run_id=run_id, module="no_website_recheck", run_status="starting", stage="queued",
        row_count_uploaded=len(rows), upload_bytes=upload_bytes, total=len(rows), processed=0, remaining=len(rows), progress_pct=0.0,
        active_elapsed_sec=0.0, throughput_rows_per_min=0.0, eta_seconds=None, eta_at="", eta_quality="calculating",
        started_at_epoch=time.time(), resume_count=0, input_filename=Path(file.filename or "uploaded_input.csv").name,
        message=f"Queued {'smart ' if strategy == 'smart' else ''}no official website re-check", strategy=strategy,
    )
    target = _run_firecrawl_recovery_job if strategy == "firecrawl" else (_run_smart_recheck_job if strategy == "smart" else _run_recheck_job)
    th = threading.Thread(target=target, args=(run_id, ev), daemon=True)
    recheck_threads[run_id] = th
    th.start()
    _job_update(run_id, status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="started", total=len(rows), module="no_website_recheck", strategy=strategy)


@app.get("/repository/recheck/status/{run_id}")
def recheck_status(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    path = _recheck_status_path(rd)
    th = recheck_threads.get(run_id)
    process_state = "running" if th and th.is_alive() else "not_running"
    if not path.exists():
        return _json(False, status_code=404, run_id=run_id, stage="status_not_found", error="No re-check status found", process_state=process_state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _json(False, run_id=run_id, stage="bad_status_json", error=str(e), process_state=process_state)
    data.setdefault("ok", True)
    data["run_id"] = run_id
    data["process_state"] = process_state
    data["downloads"] = {kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()}
    data["file_counts"] = _output_counts(rd, RECHECK_OUTPUTS)
    run_status = str(data.get("run_status") or "").lower()
    if process_state != "running" and run_status in {"queued", "starting", "running", "resuming", "pause_requested", "stop_requested", "cancelling"}:
        # A backend/worker restart may leave the last persisted status active even
        # though no thread owns the run. Present it as resumable interruption.
        run_status = "interrupted"
        data["run_status"] = "interrupted"
        data["stage"] = "interrupted_restart"
        data["current_item"] = data.get("current_item") or "Run interrupted before completion; resume from the saved checkpoint."
    data["can_pause"] = process_state == "running" and run_status not in {"pause_requested", "stop_requested"}
    data["can_stop"] = process_state == "running" and run_status != "stop_requested"
    data["can_resume"] = process_state != "running" and (run_status in {"paused", "stopped", "interrupted", "error", "cancelled", "canceled"} or str(data.get("stage") or "").lower() in {"stopped_partial", "interrupted_restart"})
    data["partial_outputs_available"] = bool(data["downloads"].get("results") or data["downloads"].get("audit"))
    _job_sync_from_status(run_id, "no_website_recheck", rd, data)
    data["job"] = _read_job(run_id)
    return JSONResponse(content=data)


@app.get("/repository/recheck/results/{run_id}")
def recheck_results(run_id: str, limit: int = 100):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    status_data = {}
    path = _recheck_status_path(rd)
    if path.exists():
        try:
            status_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            status_data = {}
    rows = _read_csv_rows(rd / RECHECK_OUTPUTS["results"], limit=limit)
    downloads = {kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()}
    file_counts = _output_counts(rd, RECHECK_OUTPUTS)
    return _json(True, run_id=run_id, stage=status_data.get("stage", "live_progress"), run_status=status_data.get("run_status", ""), rows=rows, count=len(rows), downloads=downloads, file_counts=file_counts)


@app.get("/repository/recheck/export/{run_id}/{kind}")
def recheck_export(run_id: str, kind: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    if kind not in RECHECK_OUTPUTS:
        raise HTTPException(status_code=404, detail="Unknown re-check export kind")
    path = rd / RECHECK_OUTPUTS[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Re-check export not ready")
    media_type = "text/csv" if path.suffix == ".csv" else ("application/json" if path.suffix == ".json" else "text/plain")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/repository/recheck/remaining/{run_id}")
def recheck_remaining(run_id: str, include_statuses: str = "skipped_query_cap,search_failed,provider_failure,search_incomplete,candidate_site_unreachable"):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("recheck", run_id)
    uploaded = rd / "uploaded_input.csv"
    if not uploaded.exists():
        return _json(False, status_code=404, run_id=run_id, stage="input_missing", error="Original upload is missing")
    input_rows = _read_recheck_input(uploaded)
    statuses = {s.strip().lower() for s in include_statuses.split(",") if s.strip()}
    result_map = {}
    path = rd / RECHECK_OUTPUTS["results"]
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                darpan_key = _smart_compact(r.get("Darpan ID", ""))
                if darpan_key:
                    k = "id:" + darpan_key
                else:
                    k = "name:" + "|".join([_smart_norm(r.get("NGO Name", "")), _smart_norm(r.get("District", "")), _smart_norm(r.get("State", ""))])
                result_map[k] = r
    remaining = []
    for row in input_rows:
        darpan_key = _smart_compact(_smart_primary_darpan(row))
        if darpan_key:
            k = "id:" + darpan_key
        else:
            k = "name:" + "|".join([_smart_norm(row.get("name", "")), _smart_norm(row.get("district", "")), _smart_norm(row.get("state", ""))])
        r = result_map.get(k)
        if not r or (r.get("Website Status", "").lower() in statuses):
            retry_row = dict(row)
            if r and r.get("Website"):
                retry_row["website"] = r.get("Website")
                retry_row["previous_website_status"] = r.get("Website Status", "")
            remaining.append(retry_row)

    preferred = ["name", "district", "state", "darpan_id", "email", "phone", "registered_address"]
    extras = []
    for row in remaining:
        for key in row.keys():
            if key not in preferred and key not in extras:
                extras.append(key)
    fieldnames = preferred + extras
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in remaining:
        writer.writerow(_safe_csv_row({k: row.get(k, "") for k in fieldnames}))
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{run_id}_remaining.csv"'})


_RECHECK_RESUMABLE_STATUSES = {"paused", "stopped", "interrupted", "error", "cancelled", "canceled"}
_RECHECK_RESUMABLE_STAGES = {"stopped_partial", "interrupted_restart"}


def _recheck_resumable_rows(limit: int = 100) -> list[dict]:
    """Return durable, checkpoint-backed website-recovery runs that can be resumed."""
    rows: list[dict] = []
    try:
        candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("recheck_")]
    except Exception:
        candidates = []
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for rd in candidates[:max(1, min(int(limit or 100), 300))]:
        status_path = _recheck_status_path(rd)
        uploaded = rd / "uploaded_input.csv"
        if not status_path.exists() or not uploaded.exists():
            continue
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except Exception:
            continue
        run_id = rd.name
        live_state = _job_live_state(run_id)
        run_status = str(data.get("run_status") or data.get("status") or "").strip().lower()
        stage = str(data.get("stage") or "").strip().lower()
        # Old runs sometimes retain a stale active status after a restart. If no
        # process owns them, expose them as interrupted rather than hiding them.
        if live_state != "running" and run_status in {"queued", "starting", "running", "resuming", "pause_requested", "stop_requested", "cancelling"}:
            run_status = "interrupted"
            stage = "interrupted_restart"
        strategy = str(data.get("strategy") or "smart").strip().lower()
        if strategy not in {"smart", "firecrawl"}:
            continue
        can_resume = live_state != "running" and (run_status in _RECHECK_RESUMABLE_STATUSES or stage in _RECHECK_RESUMABLE_STAGES)
        if not can_resume:
            continue
        total = int(data.get("total") or data.get("row_count_uploaded") or 0)
        processed = int(data.get("processed") or 0)
        remaining = int(data.get("remaining") if data.get("remaining") not in {None, ""} else max(0, total - processed))
        pct = data.get("progress_pct")
        if pct in {None, ""}:
            pct = round((processed / total * 100.0), 2) if total else 0.0
        rows.append({
            "run_id": run_id,
            "run_status": run_status,
            "stage": stage,
            "strategy": strategy,
            "processed": processed,
            "total": total,
            "remaining": remaining,
            "progress_pct": pct,
            "updated_at": data.get("updated_at", ""),
            "started_at": data.get("started_at", data.get("created_at", "")),
            "input_filename": data.get("input_filename", ""),
            "current_item": data.get("current_item", ""),
            "queries_used": data.get("queries_used", data.get("summary", {}).get("total_queries", 0) if isinstance(data.get("summary"), dict) else 0),
            "firecrawl_credits_used": data.get("firecrawl_credits_used", data.get("summary", {}).get("firecrawl_credits_used", 0) if isinstance(data.get("summary"), dict) else 0),
            "resume_count": int(data.get("resume_count") or 0),
            "live_state": live_state,
            "can_resume": True,
            "downloads": {kind: (rd / filename).exists() for kind, filename in RECHECK_OUTPUTS.items()},
        })
    return rows


@app.get("/repository/recheck/resumable")
def recheck_resumable(limit: int = 100):
    rows = _recheck_resumable_rows(limit=limit)
    active = [rid for rid, th in recheck_threads.items() if th.is_alive()]
    return _json(True, rows=rows, count=len(rows), active_runs=active)


@app.post("/repository/recheck/pause/{run_id}")
def recheck_pause(run_id: str):
    rd = _run_dir(run_id)
    th = recheck_threads.get(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    if not th or not th.is_alive():
        return _json(False, status_code=409, run_id=run_id, stage="not_running", error="This recovery run is not currently active")
    _recheck_pause_path(rd).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    _write_recheck_status(rd, ok=True, run_id=run_id, run_status="pause_requested", stage="pause_requested", current_item="Pause requested. The current NGO will finish, then the run will pause safely.")
    _job_update(run_id, status="pause_requested", stage="pause_requested")
    return _json(True, run_id=run_id, run_status="pause_requested", stage="pause_requested")


@app.post("/repository/recheck/stop/{run_id}")
def recheck_stop(run_id: str):
    rd = _run_dir(run_id)
    th = recheck_threads.get(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    if not th or not th.is_alive():
        # A paused run can be formally ended without losing its checkpoint.
        try:
            status = json.loads(_recheck_status_path(rd).read_text(encoding="utf-8"))
        except Exception:
            status = {}
        if str(status.get("run_status") or "").lower() == "paused":
            _write_recheck_status(rd, ok=True, run_status="stopped", stage="stopped_partial", current_item="Search ended. Partial outputs remain available and the run can be resumed.")
            _job_update(run_id, status="stopped", stage="stopped_partial")
            return _json(True, run_id=run_id, run_status="stopped", stage="stopped_partial")
        return _json(False, status_code=409, run_id=run_id, stage="not_running", error="This recovery run is not currently active")
    _recheck_stop_path(rd).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    _write_recheck_status(rd, ok=True, run_id=run_id, run_status="stop_requested", stage="stop_requested", current_item="End requested. The current NGO will finish, then partial outputs will be finalised.")
    _job_update(run_id, status="stop_requested", stage="stop_requested")
    return _json(True, run_id=run_id, run_status="stop_requested", stage="stop_requested")


@app.post("/repository/recheck/resume/{run_id}")
def recheck_resume(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("No website re-check", run_id)
    if not (rd / "uploaded_input.csv").exists():
        return _json(False, status_code=404, run_id=run_id, stage="input_missing", error="The saved input CSV is missing, so this old search cannot be resumed")
    th = recheck_threads.get(run_id)
    if th and th.is_alive():
        return _json(False, status_code=409, run_id=run_id, stage="already_running", error="This recovery run is already active")
    other_active = [rid for rid, thread in recheck_threads.items() if rid != run_id and thread.is_alive()]
    if other_active:
        return _json(False, status_code=409, run_id=run_id, stage="another_recheck_active", error="Another website-recovery run is active", active_runs=other_active)
    try:
        old = json.loads(_recheck_status_path(rd).read_text(encoding="utf-8"))
    except Exception:
        old = {}
    strategy = str(old.get("strategy") or "smart").lower()
    if strategy not in {"smart", "firecrawl"}:
        return _json(False, status_code=400, run_id=run_id, stage="resume_not_supported", error="Checkpoint resume is supported for smart and Firecrawl recovery runs")
    total = int(old.get("total") or old.get("row_count_uploaded") or 0)
    processed = int(old.get("processed") or 0)
    if total and processed >= total and str(old.get("run_status") or "").lower() == "complete":
        return _json(True, run_id=run_id, stage="already_complete", processed=processed, total=total)
    _recheck_pause_path(rd).unlink(missing_ok=True)
    _recheck_stop_path(rd).unlink(missing_ok=True)
    ev = threading.Event()
    recheck_cancel_flags[run_id] = ev
    resume_count = int(old.get("resume_count") or 0) + 1
    _write_recheck_status(rd, ok=True, run_id=run_id, run_status="resuming", stage="resume_started", current_item="Resuming from the last completed NGO", resume_count=resume_count)
    target = _run_firecrawl_recovery_job if strategy == "firecrawl" else _run_smart_recheck_job
    thread = threading.Thread(target=target, args=(run_id, ev), daemon=True)
    recheck_threads[run_id] = thread
    thread.start()
    _job_update(run_id, status="running", stage="resume_started", thread_alive=True, cancel_requested=False, cancel_requested_at="")
    return _json(True, run_id=run_id, stage="resumed", strategy=strategy, processed=processed, total=total, resume_count=resume_count)


@app.post("/repository/recheck/cancel/{run_id}")
def recheck_cancel(run_id: str):
    # Backward-compatible alias. The UI now calls this "End and save outputs".
    return recheck_stop(run_id)


@app.on_event("startup")
def _reconcile_recheck_startup():
    if os.environ.get("DFP2_SKIP_STARTUP_RECONCILE", "true").lower() in {"1", "true", "yes"}:
        return
    try:
        for rd in RUNS_DIR.iterdir():
            if not rd.is_dir() or not rd.name.startswith("recheck_"):
                continue
            sp = rd / RECHECK_OUTPUTS["status"]
            if not sp.exists():
                continue
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("run_status") == "running" and rd.name not in recheck_threads:
                _write_recheck_status(rd, run_status="interrupted", stage="interrupted_restart", active=False)
    except Exception as e:
        print(f"recheck startup reconciliation failed: {e}", file=sys.stderr)


# -----------------------------------------------------------------------------
# NGO Presence Check — exact-identity website + digital presence assessment
# -----------------------------------------------------------------------------
PRESENCE_MAX_ROWS = int(os.environ.get("PRESENCE_MAX_ROWS", "1000"))
PRESENCE_MAX_TOTAL_QUERIES = int(os.environ.get("PRESENCE_MAX_TOTAL_QUERIES", "2000"))
PRESENCE_FETCH_TIMEOUT = int(os.environ.get("PRESENCE_FETCH_TIMEOUT", "10"))
PRESENCE_FIELDS = [
    "Source Row", "NGO Name", "Center Name", "State", "Official Website",
    "Website Confidence", "Official Site Match", "Website Strength", "Presence Score",
    "Digital Presence Assessment", "Evidence", "Search Channels Found", "Query Used",
    "Queries Used", "Duplicate Group Size"
]
PRESENCE_SUMMARY_KEYS = ["total_input_rows", "unique_ngo_state_groups", "processed_groups", "queries_used", "high_confidence_sites", "medium_confidence_sites", "needs_manual_verification", "no_confirmed_website", "errors"]

_PRESENCE_STATE_NAMES = {
    "andhra pradesh","arunachal pradesh","assam","bihar","chhattisgarh","goa","gujarat","haryana","himachal pradesh","jharkhand","karnataka","kerala","madhya pradesh","maharashtra","manipur","meghalaya","mizoram","nagaland","odisha","punjab","rajasthan","sikkim","tamil nadu","telangana","tripura","uttar pradesh","uttarakhand","west bengal","andaman and nicobar islands","chandigarh","dadra and nagar haveli and daman and diu","delhi","jammu and kashmir","ladakh","lakshadweep","puducherry","pondicherry"
}


def _presence_status_path(rd: Path) -> Path:
    return rd / PRESENCE_OUTPUTS["status"]


def _write_presence_status(rd: Path, **payload):
    current = {}
    path = _presence_status_path(rd)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(payload)
    current.setdefault("ok", True)
    current.setdefault("module", "ngo_presence_check")
    current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _job_sync_from_status(str(current.get("run_id") or rd.name), "ngo_presence_check", rd, current)
    except Exception:
        pass


def _append_presence_error(rd: Path, msg: str):
    with (rd / PRESENCE_OUTPUTS["errors"]).open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def _presence_pick(row: dict, *names: str) -> str:
    lowered = {str(k or "").strip().lower(): k for k in row.keys()}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None and str(row.get(key) or "").strip():
            return str(row.get(key) or "").strip()
    return ""


def _read_presence_input(path: Path) -> list[dict]:
    """Accepts CSV with NGO name + State required, Center/Centre optional.

    Headered preferred: ngo_name,state,center_name. Headerless fallback assumes:
    column 1 = NGO name, column 2 = state, column 3 = optional center name.
    """
    rows: list[dict] = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return rows
    first = (text.splitlines()[0] if text.splitlines() else "").lower()
    has_header = any(x in first for x in ["ngo", "name", "organisation", "organization", "state", "center", "centre"])
    if has_header:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for idx, raw in enumerate(reader, start=2):
                row = {str(k or "").strip(): (v or "").strip() for k, v in (raw or {}).items() if k is not None}
                name = _presence_pick(row, "ngo_name", "ngo name", "name", "organisation", "organization", "organization name", "organisation name")
                state = _presence_pick(row, "state", "State", "region")
                center = _presence_pick(row, "center_name", "centre_name", "center name", "centre name", "center", "centre", "site", "school", "institution", "location")
                if not name and not state and not center:
                    continue
                if not name:
                    raise ValueError(f"Row {idx}: NGO name is required")
                if not state:
                    raise ValueError(f"Row {idx}: state is required")
                rows.append({"source_row": idx, "name": re.sub(r"\s+", " ", name).strip(), "state": re.sub(r"\s+", " ", state).strip(), "center_name": re.sub(r"\s+", " ", center).strip()})
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            for idx, cells in enumerate(reader, start=1):
                if not cells or not any(str(c or "").strip() for c in cells):
                    continue
                name = str(cells[0] if len(cells) > 0 else "").strip()
                state = str(cells[1] if len(cells) > 1 else "").strip()
                center = str(cells[2] if len(cells) > 2 else "").strip()
                if not name:
                    raise ValueError(f"Row {idx}: NGO name is required")
                if not state:
                    raise ValueError(f"Row {idx}: state is required")
                rows.append({"source_row": idx, "name": re.sub(r"\s+", " ", name).strip(), "state": re.sub(r"\s+", " ", state).strip(), "center_name": re.sub(r"\s+", " ", center).strip()})
    return rows


def _presence_group_key(row: dict) -> str:
    return "|".join([_smart_norm(row.get("name", "")), _smart_norm(row.get("state", ""))])


def _presence_groups(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        key = _presence_group_key(row)
        g = grouped.setdefault(key, {"name": row.get("name", ""), "state": row.get("state", ""), "rows": [], "centers": []})
        g["rows"].append(row)
        c = str(row.get("center_name") or "").strip()
        if c and c not in g["centers"]:
            g["centers"].append(c)
    return list(grouped.values())


def _presence_channel(url: str) -> str:
    dom = _recheck_domain(url)
    low = (url or "").lower()
    if not dom:
        return "unknown"
    social = ["facebook.", "instagram.", "linkedin.", "youtube.", "twitter.", "x.com"]
    if any(x in dom for x in social):
        return "social"
    listing = ["ngodarpan", "darpan.gov", "csrbox", "ngobox", "justdial", "sulekha", "guidestar", "give.do", "globalgiving", "ngoadvisor"]
    if any(x in dom for x in listing):
        return "directory/listing"
    news = ["thehindu", "timesofindia", "hindustantimes", "indianexpress", "deccanherald", "newindianexpress", "yourstory", "betterindia", "news18", "medium.com"]
    if any(x in dom for x in news) or low.endswith(".pdf"):
        return "news/article/document"
    if _recheck_bad_url(url):
        return "other third-party"
    return "official-site-candidate"


def _presence_channels_from_audit(audit_rows: list[dict], name: str, state: str) -> dict:
    target = _smart_norm(name)
    state_norm = _smart_norm(state)
    buckets: dict[str, set[str]] = {"official-site-candidate": set(), "social": set(), "directory/listing": set(), "news/article/document": set(), "other third-party": set(), "unknown": set()}
    for r in audit_rows:
        if _smart_norm(r.get("NGO Name", "")) != target:
            continue
        if state_norm and _smart_norm(r.get("State", "")) != state_norm:
            continue
        url = str(r.get("Candidate URL") or "").strip()
        if not url:
            continue
        buckets.setdefault(_presence_channel(url), set()).add(_recheck_domain(url) or url)
    return {k: sorted(v) for k, v in buckets.items() if v}


def _presence_channel_summary(channels: dict) -> str:
    parts = []
    label_map = {"official-site-candidate": "site candidates", "social": "social", "directory/listing": "directories", "news/article/document": "articles/docs", "other third-party": "other third-party", "unknown": "unknown"}
    for key in ["official-site-candidate", "social", "directory/listing", "news/article/document", "other third-party", "unknown"]:
        vals = channels.get(key) or []
        if vals:
            parts.append(f"{label_map.get(key, key)}: {', '.join(vals[:4])}")
    return " | ".join(parts) if parts else "No meaningful web channels found"


def _presence_site_profile(url: str, name: str) -> dict:
    profile = {"score": 0, "strength": "No confirmed site", "evidence": [], "error": ""}
    if not url or _recheck_bad_url(url):
        return profile
    try:
        final_url, html = _safe_fetch_text(url, headers={"User-Agent": "Mozilla/5.0 DFP2PresenceCheck/1.0"}, timeout=PRESENCE_FETCH_TIMEOUT, max_bytes=1_500_000)
    except Exception as e:
        profile["error"] = str(e)[:160]
        profile["score"] = 18
        profile["strength"] = "Weak"
        profile["evidence"].append("site candidate found but homepage could not be fetched")
        return profile
    soup = _make_soup(html or "")
    text = _smart_html_to_text(html)
    dom = _recheck_domain(final_url or url)
    score = 25
    evidence = ["official website candidate was fetchable"]
    if str(final_url or url).startswith("https://"):
        score += 6; evidence.append("HTTPS")
    if dom and not any(x in dom for x in ["wordpress.com", "blogspot.com", "wixsite.com", "weebly.com", "sites.google.com"]):
        score += 8; evidence.append("own/custom domain")
    if len(text) > 1800:
        score += 12; evidence.append("substantial homepage content")
    elif len(text) > 700:
        score += 6; evidence.append("some homepage content")
    compact_text = _smart_compact(text[:25000])
    if _smart_compact(name) and _smart_compact(name) in compact_text:
        score += 12; evidence.append("legal/name evidence on site")
    # Presence Check intentionally does NOT score child/program relevance.
    # The only scoring emphasis here is: confirmed NGO identity + digital presence quality.
    href_text = " ".join([str(a.get("href") or "") + " " + a.get_text(" ", strip=True) for a in soup.find_all("a")[:250]]).lower()
    link_signals = {
        "about page": ["about"],
        "work/content page": ["program", "our work", "what we do", "projects"],
        "impact/report page": ["impact", "annual", "report"],
        "contact page": ["contact", "reach us"],
        "donate/partner page": ["donate", "partner", "support us"],
        "team/governance page": ["team", "trustee", "board", "governance"],
    }
    for label, needles in link_signals.items():
        if any(n in href_text for n in needles):
            score += 6 if label not in {"donate/partner page", "team/governance page"} else 4
            evidence.append(label)
    score = max(0, min(100, score))
    if score >= 75:
        strength = "Strong"
    elif score >= 50:
        strength = "Moderate"
    elif score >= 25:
        strength = "Weak"
    else:
        strength = "Minimal"
    profile.update({"score": score, "strength": strength, "evidence": evidence[:10], "final_url": final_url})
    return profile


def _presence_channel_points(channels: dict) -> int:
    """Small digital-footprint bonus from non-official-site channels.

    This is intentionally capped so third-party noise never overwhelms the main question:
    did we find the correct official NGO website, and how strong is that website/presence?
    """
    points = 0
    if channels.get("social"):
        points += min(8, 4 + len(channels.get("social") or []))
    if channels.get("directory/listing"):
        points += min(5, 2 + len(channels.get("directory/listing") or []))
    if channels.get("news/article/document"):
        points += min(7, 3 + len(channels.get("news/article/document") or []))
    if channels.get("other third-party"):
        points += min(3, len(channels.get("other third-party") or []))
    return min(points, 15)


def _presence_overall_score(profile: dict, channels: dict, confidence: str) -> int:
    base = int(profile.get("score") or 0)
    bonus = _presence_channel_points(channels)
    if str(confidence or "").lower() in {"high", "medium"}:
        return max(0, min(100, base + bonus))
    # Without a confirmed official website, keep the score conservative even if directories/social exist.
    return max(0, min(40, bonus * 2))


def _presence_assessment(result: dict, profile: dict, channels: dict, overall_score: int | None = None) -> str:
    conf = str(result.get("Confidence") or "").lower()
    status = str(result.get("Website Status") or "").lower().replace("_", " ")
    website = str(result.get("Website") or "").strip()
    strength = profile.get("strength") or "No confirmed site"
    site_score = profile.get("score", 0)
    score = overall_score if overall_score is not None else site_score
    channel_bits = []
    if channels.get("social"):
        channel_bits.append("social profiles")
    if channels.get("directory/listing"):
        channel_bits.append("NGO/directories")
    if channels.get("news/article/document"):
        channel_bits.append("articles/documents")
    channel_text = ", ".join(channel_bits) if channel_bits else "limited third-party signal"
    if website and conf in {"high", "medium"}:
        return f"{strength} digital presence. Official NGO website match is {conf}-confidence ({status or 'verified match'}); site quality score {site_score}/100 and overall presence score {score}/100. Additional web signal: {channel_text}."
    if website:
        return f"Needs manual verification. A possible website was found, but NGO identity confidence is low; do not treat it as the official site without checking. Additional web signal: {channel_text}."
    if channel_bits:
        return f"No confirmed official NGO website found. The NGO has some online footprint through {channel_text}, but the digital presence is not strong enough to confirm an official site."
    return "No confirmed official NGO website or meaningful digital footprint found from the searched channels."


def _presence_result_for_group(group: dict, rd: Path, audit_rows: list[dict], counter: dict) -> dict:
    centers = group.get("centers") or []
    district_hint = " ; ".join(centers[:2])
    row = {"name": group.get("name", ""), "state": group.get("state", ""), "district": district_hint}
    if PRESENCE_MAX_TOTAL_QUERIES and counter.get("queries", 0) >= PRESENCE_MAX_TOTAL_QUERIES:
        smart = _smart_result(row, "", "skipped_query_cap", "low", "serper", "", f"presence-check query cap reached at {counter.get('queries', 0)} queries", "", searched="no", queries_used=0)
    else:
        before = len(audit_rows)
        smart = _smart_process_row(row, rd, audit_rows, counter)
        # If center context was too narrow and we only got low/no result, retry once without center hints.
        status = str(smart.get("Website Status") or "").lower()
        conf = str(smart.get("Confidence") or "").lower()
        if centers and conf == "low" and status in {"no_candidate_found", "needs_manual_verification"} and (not PRESENCE_MAX_TOTAL_QUERIES or counter.get("queries", 0) < PRESENCE_MAX_TOTAL_QUERIES):
            fallback_row = {"name": group.get("name", ""), "state": group.get("state", ""), "district": ""}
            fallback = _smart_process_row(fallback_row, rd, audit_rows, counter)
            if str(fallback.get("Confidence") or "").lower() in {"high", "medium"} or (not smart.get("Website") and fallback.get("Website")):
                smart = fallback
    channels = _presence_channels_from_audit(audit_rows, group.get("name", ""), group.get("state", ""))
    website = str(smart.get("Website") or "").strip()
    conf = str(smart.get("Confidence") or "").lower()
    profile = _presence_site_profile(website, group.get("name", "")) if website and conf in {"high", "medium"} else {"score": 0, "strength": "No confirmed site", "evidence": []}
    evidence_parts = []
    if smart.get("Evidence Grade"):
        evidence_parts.append(f"identity evidence {smart.get('Evidence Grade')}: {smart.get('Evidence Type') or ''}".strip())
    if smart.get("Evidence Matched Text"):
        evidence_parts.append(str(smart.get("Evidence Matched Text"))[:140])
    if profile.get("evidence"):
        evidence_parts.extend(profile.get("evidence")[:7])
    if profile.get("error"):
        evidence_parts.append("site fetch issue: " + str(profile.get("error"))[:120])
    overall_score = _presence_overall_score(profile, channels, conf)
    return {
        "ngo_name": group.get("name", ""),
        "state": group.get("state", ""),
        "website": website if conf in {"high", "medium"} else (website or ""),
        "website_confidence": smart.get("Confidence") or "low",
        "official_site_match": smart.get("Website Status") or "no_candidate_found",
        "website_strength": profile.get("strength") or "No confirmed site",
        "presence_score": overall_score,
        "assessment": _presence_assessment(smart, profile, channels, overall_score),
        "evidence": " | ".join([x for x in evidence_parts if x])[:900],
        "channels": _presence_channel_summary(channels),
        "query": smart.get("Query") or "",
        "queries_used": smart.get("Queries Used") or 0,
        "duplicate_group_size": len(group.get("rows") or []),
    }


def _write_presence_csvs(rd: Path, result_rows: list[dict], audit_rows: list[dict]) -> None:
    with (rd / PRESENCE_OUTPUTS["results"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRESENCE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in result_rows:
            writer.writerow(_safe_csv_row(r))
    # Keep the smart candidate audit intact, plus a channel column for easier review.
    audit_fields = list(RECHECK_AUDIT_FIELDS) + ["Channel"]
    with (rd / PRESENCE_OUTPUTS["audit"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=audit_fields, extrasaction="ignore")
        writer.writeheader()
        for r in audit_rows:
            rr = dict(r)
            rr["Channel"] = _presence_channel(rr.get("Candidate URL", ""))
            writer.writerow(_safe_csv_row(rr))


def _presence_write_summary(rd: Path, rows: list[dict], groups: list[dict], counter: dict, start_ts: float, errors: int) -> dict:
    high = sum(1 for r in rows if str(r.get("Website Confidence") or "").lower() == "high")
    med = sum(1 for r in rows if str(r.get("Website Confidence") or "").lower() == "medium")
    manual = sum(1 for r in rows if "manual" in str(r.get("Official Site Match") or "").lower())
    no_site = sum(1 for r in rows if not str(r.get("Official Website") or "").strip())
    summary = {
        "total_input_rows": len(rows),
        "unique_ngo_state_groups": len(groups),
        "processed_groups": len({str(r.get("NGO Name"))+'|'+str(r.get("State")) for r in rows}),
        "queries_used": counter.get("queries", 0),
        "high_confidence_sites": high,
        "medium_confidence_sites": med,
        "needs_manual_verification": manual,
        "no_confirmed_website": no_site,
        "errors": errors,
        "scoring_basis": "official_ngo_website_identity_and_digital_presence_only_no_child_or_program_fit_scoring",
        "elapsed_sec": round(time.time() - start_ts, 2),
    }
    _atomic_write_text((rd / PRESENCE_OUTPUTS["summary"]), json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _run_presence_job(run_id: str, cancel_event: threading.Event):
    rd = _run_dir(run_id)
    result_rows: list[dict] = []
    audit_rows: list[dict] = []
    start_ts = time.time()
    errors = 0
    counter = {"queries": 0}
    try:
        input_rows = _read_presence_input(rd / "uploaded_input.csv")[:PRESENCE_MAX_ROWS]
        groups = _presence_groups(input_rows)
        _write_presence_status(rd, ok=True, run_id=run_id, run_status="running", stage="checking_presence", total=len(groups), processed=0, row_count_uploaded=len(input_rows), queries_used=0, message="NGO presence check running")
        group_results: dict[str, dict] = {}
        for i, group in enumerate(groups, start=1):
            if _should_cancel(run_id, cancel_event):
                _write_presence_status(rd, run_status="cancelled", stage="cancelled", processed=i-1, total=len(groups), queries_used=counter.get("queries", 0))
                return
            _write_presence_status(rd, stage="checking_presence", current_item=f"{group.get('name','')} — {group.get('state','')}", processed=i-1, total=len(groups), queries_used=counter.get("queries", 0))
            try:
                group_result = _presence_result_for_group(group, rd, audit_rows, counter)
            except Exception as e:
                errors += 1
                _append_presence_error(rd, f"{group.get('name','')} presence error: {e}")
                group_result = {"ngo_name": group.get("name", ""), "state": group.get("state", ""), "website": "", "website_confidence": "low", "official_site_match": "search_failed", "website_strength": "No confirmed site", "presence_score": 0, "assessment": str(e)[:250], "evidence": "", "channels": "", "query": "", "queries_used": 0, "duplicate_group_size": len(group.get("rows") or [])}
            group_results[_presence_group_key({"name": group.get("name", ""), "state": group.get("state", "")})] = group_result
            # Emit one output row per original CSV row so repeated centres remain reviewable.
            result_rows = []
            for src in input_rows:
                gr = group_results.get(_presence_group_key(src))
                if not gr:
                    continue
                result_rows.append({
                    "Source Row": src.get("source_row", ""),
                    "NGO Name": src.get("name", ""),
                    "Center Name": src.get("center_name", ""),
                    "State": src.get("state", ""),
                    "Official Website": gr.get("website", ""),
                    "Website Confidence": gr.get("website_confidence", ""),
                    "Official Site Match": gr.get("official_site_match", ""),
                    "Website Strength": gr.get("website_strength", ""),
                    "Presence Score": gr.get("presence_score", ""),
                    "Digital Presence Assessment": gr.get("assessment", ""),
                    "Evidence": gr.get("evidence", ""),
                    "Search Channels Found": gr.get("channels", ""),
                    "Query Used": gr.get("query", ""),
                    "Queries Used": gr.get("queries_used", ""),
                    "Duplicate Group Size": gr.get("duplicate_group_size", ""),
                })
            _write_presence_csvs(rd, result_rows, audit_rows)
            summary = _presence_write_summary(rd, result_rows, groups, counter, start_ts, errors)
            _write_presence_status(rd, processed=i, total=len(groups), rows_ready=len(result_rows), queries_used=counter.get("queries", 0), summary=summary, downloads={kind: (rd / filename).exists() for kind, filename in PRESENCE_OUTPUTS.items()})
            time.sleep(RECHECK_PACE_SEC)
        _write_presence_csvs(rd, result_rows, audit_rows)
        summary = _presence_write_summary(rd, result_rows, groups, counter, start_ts, errors)
        _write_presence_status(rd, ok=True, run_status="complete", stage="results_ready", message="NGO presence check complete", processed=len(groups), total=len(groups), rows_ready=len(result_rows), queries_used=counter.get("queries", 0), summary=summary, errors=errors, downloads={kind: (rd / filename).exists() for kind, filename in PRESENCE_OUTPUTS.items()})
    except Exception as e:
        _append_presence_error(rd, f"fatal presence check error: {e}")
        _write_presence_status(rd, ok=False, run_status="error", stage="error", error=str(e)[:500])


@app.post("/repository/presence/start")
async def presence_start(file: UploadFile = File(...)):
    if not _has_serper_keys():
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS must be set on Render")
    active = [rid for rid, th in list(presence_threads.items()) if th.is_alive()]
    if active:
        return _json(False, status_code=409, stage="another_presence_check_active", error="Another NGO presence check is already active", active_runs=active)
    run_id = f"presence_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    uploaded = rd / "uploaded_input.csv"
    try:
        upload_bytes = _save_upload_with_limit(file, uploaded, max_bytes=MAX_UPLOAD_BYTES)
        rows = _read_presence_input(uploaded)
    except Exception as e:
        _write_presence_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="bad_csv", error=str(e))
        return _json(False, status_code=400, run_id=run_id, stage="bad_csv", error=str(e))
    if not rows:
        msg = "Upload a CSV with ngo_name and state. center_name is optional."
        _write_presence_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="empty_csv", error=msg)
        return _json(False, status_code=400, run_id=run_id, stage="empty_csv", error=msg)
    if len(rows) > PRESENCE_MAX_ROWS:
        msg = f"NGO Presence Check allows up to {PRESENCE_MAX_ROWS} rows per run. Split this file."
        _write_presence_status(rd, ok=False, run_id=run_id, run_status="blocked", stage="too_many_rows", error=msg, row_count=len(rows))
        return _json(False, status_code=400, run_id=run_id, stage="too_many_rows", error=msg, row_count=len(rows))
    groups = _presence_groups(rows)
    ev = threading.Event()
    presence_cancel_flags[run_id] = ev
    _write_presence_status(rd, ok=True, run_id=run_id, module="ngo_presence_check", run_status="starting", stage="queued", row_count_uploaded=len(rows), total=len(groups), processed=0, upload_bytes=upload_bytes, message="Queued NGO presence check")
    th = threading.Thread(target=_run_presence_job, args=(run_id, ev), daemon=True)
    presence_threads[run_id] = th
    th.start()
    _job_update(run_id, job_type="ngo_presence_check", status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="started", total=len(groups), row_count_uploaded=len(rows), module="ngo_presence_check")


@app.get("/repository/presence/status/{run_id}")
def presence_status(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("NGO presence check", run_id)
    path = _presence_status_path(rd)
    th = presence_threads.get(run_id)
    process_state = "running" if th and th.is_alive() else "not_running"
    if not path.exists():
        return _json(False, status_code=404, run_id=run_id, stage="status_not_found", error="No presence-check status found", process_state=process_state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _json(False, run_id=run_id, stage="bad_status_json", error=str(e), process_state=process_state)
    data.setdefault("ok", True)
    data["run_id"] = run_id
    data["process_state"] = process_state
    data["downloads"] = {kind: (rd / filename).exists() for kind, filename in PRESENCE_OUTPUTS.items()}
    data["file_counts"] = _output_counts(rd, PRESENCE_OUTPUTS)
    _job_sync_from_status(run_id, "ngo_presence_check", rd, data)
    data["job"] = _read_job(run_id)
    return JSONResponse(content=data)


@app.get("/repository/presence/results/{run_id}")
def presence_results(run_id: str, limit: int = 100):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("NGO presence check", run_id)
    status_data = {}
    path = _presence_status_path(rd)
    if path.exists():
        try:
            status_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            status_data = {}
    rows = _read_csv_rows(rd / PRESENCE_OUTPUTS["results"], limit=limit)
    downloads = {kind: (rd / filename).exists() for kind, filename in PRESENCE_OUTPUTS.items()}
    file_counts = _output_counts(rd, PRESENCE_OUTPUTS)
    return _json(True, run_id=run_id, stage=status_data.get("stage", "live_progress"), run_status=status_data.get("run_status", ""), rows=rows, count=len(rows), downloads=downloads, file_counts=file_counts)


@app.get("/repository/presence/export/{run_id}/{kind}")
def presence_export(run_id: str, kind: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("NGO presence check", run_id)
    if kind not in PRESENCE_OUTPUTS:
        raise HTTPException(status_code=404, detail="Unknown presence-check export kind")
    path = rd / PRESENCE_OUTPUTS[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Presence-check export not ready")
    media_type = "text/csv" if path.suffix == ".csv" else ("application/json" if path.suffix == ".json" else "text/plain")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/repository/presence/cancel/{run_id}")
def presence_cancel(run_id: str):
    _job_request_cancel(run_id)
    ev = presence_cancel_flags.get(run_id)
    th = presence_threads.get(run_id)
    if not ev or not th or not th.is_alive():
        return _json(False, run_id=run_id, stage="not_running", error="NGO presence check is not active")
    ev.set()
    rd = _run_dir(run_id)
    _write_presence_status(rd, ok=True, run_id=run_id, run_status="cancelled", stage="cancelled", current_item="Cancelled safely")
    _job_update(run_id, status="cancelled", stage="cancelled")
    return _json(True, run_id=run_id, run_status="cancelled", stage="cancelled")


@app.on_event("startup")
def _reconcile_presence_startup():
    if os.environ.get("DFP2_SKIP_STARTUP_RECONCILE", "true").lower() in {"1", "true", "yes"}:
        return
    try:
        for rd in RUNS_DIR.iterdir():
            if not rd.is_dir() or not rd.name.startswith("presence_"):
                continue
            sp = rd / PRESENCE_OUTPUTS["status"]
            if not sp.exists():
                continue
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("run_status") == "running" and rd.name not in presence_threads:
                _write_presence_status(rd, run_status="interrupted", stage="interrupted_restart", active=False)
    except Exception as e:
        print(f"presence startup reconciliation failed: {e}", file=sys.stderr)


@app.on_event("startup")
def _reconcile_jobs_startup():
    if os.environ.get("DFP2_SKIP_STARTUP_RECONCILE", "true").lower() in {"1", "true", "yes"}:
        return
    try:
        _reconcile_job_registry_startup()
    except Exception as e:
        print(f"job startup reconciliation failed: {e}", file=sys.stderr)


# -----------------------------------------------------------------------------
# Story Discovery — lightweight capped backend module
# -----------------------------------------------------------------------------
STORY_MAX_QUERIES = int(os.environ.get("STORY_MAX_QUERIES", "8"))
STORY_RESULTS_PER_QUERY = int(os.environ.get("STORY_RESULTS_PER_QUERY", "5"))
STORY_MAX_ARTICLES = int(os.environ.get("STORY_MAX_ARTICLES", "15"))
STORY_FETCH_TIMEOUT = int(os.environ.get("STORY_FETCH_TIMEOUT", "15"))
STORY_MODEL = os.environ.get("STORY_MODEL", "claude-haiku-4-5-20251001")

STORY_FIELDS = [
    "Organisation", "Website / Source", "Location", "Pathway", "Why It Belongs",
    "Status", "Output Tier", "Transformation / Distinctiveness Signal", "Source Quality", "State Gate",
    "NGO Name", "State", "District", "Story Category", "Story Type", "Story Title", "Story Summary",
    "Why NGO Is Interesting", "Public Name", "Repository Status", "Traced Place",
    "Source", "Source URL", "Article URL", "Confidence", "Discovery Query", "Notes"
]
STORY_AUDIT_FIELDS = ["Query", "Category", "Status", "URL", "Title", "Note", "Query Family", "Score", "Source", "Snippet"]
STORY_REJECTED_FIELDS = ["Query", "Category", "Reject Reason", "URL", "Title", "Note"]


def _story_status_path(rd: Path) -> Path:
    return rd / STORY_OUTPUTS["status"]


def _write_story_status(rd: Path, **payload):
    current = {}
    path = _story_status_path(rd)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(payload)
    current.setdefault("ok", True)
    current.setdefault("module", "story")
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _job_sync_from_status(str(current.get("run_id") or rd.name), str(current.get("module") or ("discovery" if rd.name.startswith("discovery") else "story")), rd, current)
    except Exception:
        pass


def _append_story_audit(rd: Path, row: dict):
    path = rd / STORY_OUTPUTS["audit"]
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STORY_AUDIT_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(_safe_csv_row({k: row.get(k, "") for k in STORY_AUDIT_FIELDS}))


def _append_story_rejected(rd: Path, row: dict):
    path = rd / STORY_OUTPUTS["rejected"]
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STORY_REJECTED_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(_safe_csv_row({
            "Query": row.get("Query", ""),
            "Category": row.get("Category", ""),
            "Reject Reason": row.get("Reject Reason") or row.get("Status", ""),
            "URL": row.get("URL", ""),
            "Title": row.get("Title", ""),
            "Note": row.get("Note", ""),
        }))


def _append_story_error(rd: Path, msg: str):
    with (rd / STORY_OUTPUTS["errors"]).open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def _write_story_rows(rd: Path, rows: list[dict]):
    path = rd / STORY_OUTPUTS["stories"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=STORY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_safe_csv_row({k: row.get(k, "") for k in STORY_FIELDS}))


def _story_queries(state: str, district: str) -> list[str]:
    base = [
        f'{district} {state} student scholarship NGO story',
        f'{district} {state} child education NGO success story',
        f'{district} {state} rural student exam success NGO',
        f'{district} {state} child rescue NGO story',
        f'{district} {state} child labour rescue NGO',
        f'{district} {state} sports academy child success NGO',
        f'{district} {state} girl education NGO story',
        f'{district} {state} nutrition child welfare NGO story',
    ]
    return base[:max(1, min(STORY_MAX_QUERIES, len(base)))]


def _serper_search(query: str) -> list[dict]:
    data = _serper_post({"q": query, "num": STORY_RESULTS_PER_QUERY}, timeout=20)
    return data.get("organic", []) or []


def _fetch_article_text(url: str) -> tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 DFP2StoryDiscovery/1.0"}
    _final_url, html = _safe_fetch_text(url, headers=headers, timeout=STORY_FETCH_TIMEOUT, max_bytes=1_500_000)
    soup = _make_soup(html)
    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:220]
    chunks = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt and len(txt) > 25:
            chunks.append(txt)
    text = "\n".join(chunks)
    return title, text[:9000]


def _clean_json_from_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
    raise ValueError("Could not parse model JSON")


def _fallback_story_row(state: str, district: str, title: str, snippet: str, url: str, source: str) -> dict | None:
    hay = f"{title} {snippet}".lower()
    signals = ["ngo", "foundation", "trust", "student", "child", "children", "school", "scholarship", "rescue", "nutrition", "sports"]
    if not any(s in hay for s in signals):
        return None
    return {
        "State": state,
        "District": district,
        "Story Type": "needs_review",
        "Story Title": title[:180] or "Public transformation story",
        "Story Summary": snippet[:500] or "Potential public story found. Needs manual review.",
        "Public Name": "",
        "NGO Name": "needs_check",
        "Repository Status": "needs_check",
        "Traced Place": district,
        "Source": source or urlparse(url).netloc,
        "Article URL": url,
        "Confidence": "low",
        "Notes": "Heuristic fallback; model extraction was unavailable or inconclusive.",
    }


def _extract_story_with_claude(state: str, district: str, url: str, title: str, snippet: str, source: str, article_text: str) -> dict | None:
    if _get_anthropic() is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_story_row(state, district, title, snippet, url, source)
    prompt = f"""
You are helping Feeding India build Story Discovery.
Use ONLY the public text below. Identify whether this page contains a real transformation story about a child/student/young person/community member and trace it to the NGO/foundation behind it.

Return ONLY JSON. No markdown.
Schema:
{{
  "is_relevant": true/false,
  "story_type": "education|nutrition|rescue|sports|scholarship|health|other|needs_review",
  "story_title": "short title",
  "story_summary": "2 sentence respectful summary",
  "public_name": "person name only if clearly public, else empty string",
  "ngo_name": "NGO/foundation behind the story, else needs_check",
  "repository_status": "new_lead|matched_existing|needs_check",
  "traced_place": "place mentioned",
  "confidence": "high|medium|low",
  "notes": "short note"
}}

State: {state}
District: {district}
Source title: {title}
Search snippet: {snippet}
URL: {url}
Page text:
{article_text[:7000]}
""".strip()
    client = _get_anthropic().Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=STORY_MODEL,
        max_tokens=700,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    content = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    data = _clean_json_from_text(content)
    if not data.get("is_relevant"):
        return None
    return {
        "State": state,
        "District": district,
        "Story Category": data.get("story_type", "needs_review"),
        "Story Type": data.get("story_type", "needs_review"),
        "Story Title": data.get("story_title", title)[:220],
        "Story Summary": data.get("story_summary", snippet)[:800],
        "Why NGO Is Interesting": data.get("notes", ""),
        "Public Name": data.get("public_name", ""),
        "NGO Name": data.get("ngo_name", "needs_check") or "needs_check",
        "Repository Status": data.get("repository_status", "needs_check") or "needs_check",
        "Traced Place": data.get("traced_place", district) or district,
        "Source": source or urlparse(url).netloc,
        "Article URL": url,
        "Confidence": data.get("confidence", "low"),
        "Discovery Query": "",
        "Notes": data.get("notes", ""),
    }


def _run_story_job(run_id: str, state: str, district: str, cancel_event: threading.Event):
    rd = _run_dir(run_id)
    stories: list[dict] = []
    seen_urls: set[str] = set()
    queries = _story_queries(state, district)
    total_articles_seen = 0
    try:
        _write_story_status(
            rd, ok=True, run_id=run_id, run_status="running", stage="searching",
            current_item="Preparing story search", current_search="", current_url="",
            processed=0, total=len(queries), links_found=0, articles_read=0, stories_found=0,
            state=state, district=district,
        )
        candidates: list[dict] = []
        for idx, query in enumerate(queries, start=1):
            if _should_cancel(run_id, cancel_event):
                _write_story_status(rd, run_status="cancelled", stage="cancelled", current_item="Cancelled safely")
                return
            _write_story_status(rd, stage="searching", current_search=query, processed=idx-1, total=len(queries))
            try:
                results = _serper_search(query)
            except Exception as e:
                _append_story_error(rd, f"search failed query={query!r}: {e}")
                _append_story_audit(rd, {"Query": query, "Status": "search_failed", "URL": "", "Title": "", "Note": str(e)[:250]})
                continue
            for item in results:
                url = item.get("link") or ""
                if not url or url in seen_urls:
                    continue
                if not url.startswith(("http://", "https://")):
                    continue
                seen_urls.add(url)
                candidates.append({
                    "query": query,
                    "url": url,
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "source": item.get("source", ""),
                })
            _write_story_status(rd, processed=idx, links_found=len(candidates))
            time.sleep(0.15)
        candidates = candidates[:STORY_MAX_ARTICLES]
        for i, item in enumerate(candidates, start=1):
            if _should_cancel(run_id, cancel_event):
                _write_story_status(rd, run_status="cancelled", stage="cancelled", current_item="Cancelled safely")
                return
            total_articles_seen = i
            url = item["url"]
            _write_story_status(
                rd, stage="reading_articles", current_url=url, current_item=item.get("title", ""),
                articles_read=i, links_found=len(candidates), stories_found=len(stories),
            )
            try:
                page_title, article_text = _fetch_article_text(url)
                merged_title = page_title or item.get("title", "")
                row = _extract_story_with_claude(
                    state, district, url, merged_title, item.get("snippet", ""), item.get("source", ""), article_text
                )
                if row:
                    stories.append(row)
                    _append_story_audit(rd, {"Query": item.get("query", ""), "Status": "story_found", "URL": url, "Title": merged_title, "Note": row.get("NGO Name", "")})
                    # Write partial output after every story so downloads are useful even if interrupted.
                    _write_story_rows(rd, stories)
                else:
                    _append_story_audit(rd, {"Query": item.get("query", ""), "Status": "not_relevant", "URL": url, "Title": merged_title, "Note": "No traceable NGO story found"})
            except Exception as e:
                _append_story_error(rd, f"article failed url={url!r}: {e}")
                _append_story_audit(rd, {"Query": item.get("query", ""), "Status": "article_failed", "URL": url, "Title": item.get("title", ""), "Note": str(e)[:250]})
            _write_story_status(rd, stories_found=len(stories), articles_read=i)
        _write_story_rows(rd, stories)
        _write_story_status(
            rd, ok=True, run_status="complete", stage="results_ready", current_item="Story Discovery complete",
            current_search="", current_url="", processed=len(queries), total=len(queries),
            links_found=len(candidates), articles_read=total_articles_seen, stories_found=len(stories),
            downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()},
        )
    except Exception as e:
        _append_story_error(rd, f"fatal story job error: {e}")
        _write_story_status(rd, ok=False, run_status="error", stage="error", error=str(e)[:500])


@app.post("/story/start")
def story_start(state: str, district: str):
    if not state.strip() or not district.strip():
        return _json(False, status_code=400, stage="missing_location", error="State and district are required")
    if not _has_serper_keys():
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS must be set on Render")
    # Anthropic is preferred. If missing, the module still runs with lower-quality fallback rows.
    active_story = [rid for rid, th in list(story_threads.items()) if th.is_alive()]
    if active_story:
        return _json(False, status_code=409, stage="another_story_run_active", error="Another Story Discovery run is already active", active_runs=active_story)

    run_id = f"story_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    cancel_event = threading.Event()
    story_cancel_flags[run_id] = cancel_event
    _write_story_status(
        rd, ok=True, run_id=run_id, module="story", run_status="starting", stage="queued",
        state=state, district=district, current_item="Queued", processed=0, total=STORY_MAX_QUERIES,
        links_found=0, articles_read=0, stories_found=0,
    )
    th = threading.Thread(target=_run_story_job, args=(run_id, state.strip(), district.strip(), cancel_event), daemon=True)
    story_threads[run_id] = th
    th.start()
    _job_update(run_id, status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="started", state=state, district=district, total=STORY_MAX_QUERIES)


@app.get("/story/status/{run_id}")
def story_status(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Story Discovery", run_id)
    path = _story_status_path(rd)
    th = story_threads.get(run_id)
    process_state = "running" if th and th.is_alive() else "not_running"
    if not path.exists():
        return _json(False, status_code=404, run_id=run_id, stage="status_not_found", error="No Story Discovery status found", process_state=process_state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _json(False, run_id=run_id, stage="bad_status_json", error=str(e), process_state=process_state)
    data.setdefault("ok", True)
    data["run_id"] = run_id
    data["process_state"] = process_state
    data["downloads"] = {kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()}
    _job_sync_from_status(run_id, str(data.get("module") or ("discovery" if run_id.startswith("discovery") else "story")), rd, data)
    data["job"] = _read_job(run_id)
    return JSONResponse(content=data)


def _story_complete(rd: Path) -> tuple[bool, dict]:
    """Story output is final only after the story thread writes complete/results_ready.

    The job may write partial story CSV rows during article processing, but those
    must not be reported as final results because the frontend stops polling on
    results_ready.
    """
    status_path = _story_status_path(rd)
    status_data = {}
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status_data = {}
    complete = (
        str(status_data.get("stage") or "").lower() == "results_ready"
        and str(status_data.get("run_status") or "").lower() in {"complete", "completed", "done", "success", "succeeded"}
    )
    return complete, status_data


@app.get("/story/results/{run_id}")
def story_results(run_id: str, limit: int = 50):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Story Discovery", run_id)
    story_path = rd / STORY_OUTPUTS["stories"]
    audit_path = rd / STORY_OUTPUTS["audit"]

    complete, status_data = _story_complete(rd)
    stories = _read_csv_rows(story_path, limit=limit) if story_path.exists() else []
    audit_rows = _read_csv_rows(audit_path, limit=max(limit, 100))
    rejected_rows = _read_csv_rows(rd / STORY_OUTPUTS["rejected"], limit=max(limit, 100))
    stage = "results_ready" if complete else "live_progress"

    downloads = {kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()}
    downloads["stories"] = bool(story_path.exists())
    downloads["story_csv"] = bool(story_path.exists())

    return _json(
        True,
        run_id=run_id,
        stage=stage,
        run_status=status_data.get("run_status", ""),
        stories=stories,
        rows=stories,
        live_rows=audit_rows,
        audit_rows=audit_rows,
        rejected_rows=rejected_rows,
        count=len(stories),
        stories_found=status_data.get("stories_found", len(stories)),
        links_found=status_data.get("links_found", 0),
        articles_read=status_data.get("articles_read", 0),
        downloads=downloads,
    )


@app.get("/story/export/{run_id}/{kind}")
def story_export(run_id: str, kind: str):
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Story Discovery", run_id)
    if kind not in STORY_OUTPUTS:
        raise HTTPException(status_code=404, detail="Unknown story export kind")
    path = rd / STORY_OUTPUTS[kind]
    # Story CSV is now checkpointed and exportable even while paused/running if rows exist.
    if not path.exists():
        raise HTTPException(status_code=404, detail="Story export not ready")
    media_type = "text/csv" if path.suffix == ".csv" else ("application/json" if path.suffix == ".json" else "text/plain")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/story/cancel/{run_id}")
def story_cancel(run_id: str):
    _job_request_cancel(run_id)
    ev = story_cancel_flags.get(run_id)
    th = story_threads.get(run_id)
    if not ev or not th or not th.is_alive():
        return _json(False, run_id=run_id, stage="not_running", error="Story run is not active")
    ev.set()
    rd = _run_dir(run_id)
    _write_story_status(rd, ok=True, run_id=run_id, run_status="cancelled", stage="cancelled", current_item="Cancelled safely")
    _job_update(run_id, status="cancelled", stage="cancelled")
    return _json(True, run_id=run_id, run_status="cancelled", stage="cancelled")


@app.post("/story/pause/{run_id}")
def story_pause(run_id: str):
    _job_request_cancel(run_id)
    rd = _run_dir(run_id)
    if not rd.exists():
        return _run_not_found("Story Discovery", run_id)
    _story_pause_path(rd).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    ev = story_cancel_flags.get(run_id)
    if ev:
        ev.set()
    _write_story_status(rd, ok=True, run_id=run_id, run_status="pause_requested", stage="pause_requested", current_item="Pause requested. The current request will finish, then the run will pause safely.")
    _job_update(run_id, status="pause_requested", stage="pause_requested")
    return _json(True, run_id=run_id, stage="pause_requested")


@app.post("/story/resume/{run_id}")
def story_resume(run_id: str):
    rd = _run_dir(run_id)
    if not rd.exists() or not _story_status_path(rd).exists():
        return _run_not_found("Story Discovery", run_id)
    active_story = [rid for rid, th in list(story_threads.items()) if th.is_alive()]
    if active_story:
        return _json(False, status_code=409, stage="another_story_run_active", error="Another Story Discovery run is already active", active_runs=active_story)
    try:
        old = json.loads(_story_status_path(rd).read_text(encoding="utf-8"))
    except Exception:
        old = {}
    state = str(old.get("state") or "Karnataka")
    categories = old.get("categories") or _normalise_story_categories("")
    if isinstance(categories, str):
        categories = _normalise_story_categories(categories)
    budget = int(old.get("query_budget") or old.get("total") or STORY_STATE_QUERY_BUDGET)
    _story_pause_path(rd).unlink(missing_ok=True)
    cancel_event = threading.Event()
    story_cancel_flags[run_id] = cancel_event
    _write_story_status(rd, ok=True, run_id=run_id, run_status="resuming", stage="resume_started", current_item="Resuming Story Discovery from checkpoint")
    th = threading.Thread(target=_run_story_state_job, args=(run_id, state, categories, budget, cancel_event), daemon=True)
    story_threads[run_id] = th
    th.start()
    _job_update(run_id, status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="resumed", story_mode=old.get("story_mode", "statewide"), state=state, categories=categories, query_budget=budget)


@app.get("/story/archive")
def story_archive(limit: int = 100):
    items = []
    dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith("story")]
    dirs = sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, 300))]
    for rd in dirs:
        run_id = rd.name
        status_path = _story_status_path(rd)
        data = {}
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        items.append({
            "run_id": run_id,
            "module": "story",
            "label": "Story Discovery",
            "updated_at": data.get("updated_at", ""),
            "run_status": data.get("run_status", ""),
            "stage": data.get("stage", ""),
            "state": data.get("state", ""),
            "total": data.get("total", ""),
            "processed": data.get("processed", ""),
            "links_found": data.get("links_found", ""),
            "articles_read": data.get("articles_read", ""),
            "stories_found": data.get("stories_found", ""),
            "downloads": {kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()},
        })
    return _json(True, rows=items, count=len(items))


# -----------------------------------------------------------------------------
# General Discovery — child pathway institution discovery
# -----------------------------------------------------------------------------
DISCOVERY_DEFAULT_BUDGET = int(os.environ.get("DISCOVERY_DEFAULT_BUDGET", "200"))
DISCOVERY_MAX_BUDGET = int(os.environ.get("DISCOVERY_MAX_BUDGET", "5500"))
DISCOVERY_EXTENDED_WARNING_BUDGET = int(os.environ.get("DISCOVERY_EXTENDED_WARNING_BUDGET", "4000"))
DISCOVERY_MAX_ARTICLES = int(os.environ.get("DISCOVERY_MAX_ARTICLES", "900"))

DISCOVERY_PATHWAYS = {
    "residential_life_system": [
        "residential school underprivileged children", "free boarding rural students", "children's home education",
        "girls hostel free education", "student hostel rural students", "chatralaya students",
        "orphanage school children", "tribal residential school students", "HIV affected children home education",
    ],
    "full_day_alternative": [
        "free school underprivileged children", "alternative school school dropouts", "open school school dropouts",
        "NIOS underprivileged children", "bridge school child labour", "whole child education underprivileged",
        "first generation learners school", "school dropouts mainstreaming NGO",
    ],
    "child_protection_rehab": [
        "child labour rehabilitation education", "street children shelter NGO", "open shelter children NGO",
        "rescued children education home", "trafficking survivors children rehabilitation", "CWC children home education",
        "girls home rehabilitation NGO", "abandoned children SSLC NGO",
    ],
    "disability_special_needs": [
        "special school children NGO", "blind school free boarding", "hearing impaired children school",
        "autism school underprivileged", "intellectual disability children school", "children with disabilities residential school",
        "special school life skills children", "disabled children inclusive education NGO",
    ],
    "sports_arts_stem_vocational": [
        "sports academy underprivileged children", "football slum children academy", "athletics rural children NGO",
        "girls sports underprivileged NGO", "rural athletes nutrition NGO", "surfing underprivileged children",
        "boxing slum children NGO", "kabaddi rural children NGO", "skating underprivileged children",
        "music underprivileged children NGO", "theatre children rural NGO", "drama underprivileged children NGO",
        "performing arts low income children", "dance underserved children", "arts education underprivileged children",
        "STEM underprivileged students NGO", "robotics rural children NGO", "coding government school students NGO",
        "maker lab rural students", "adolescent girls vocational education", "media design adolescents NGO",
    ],
    "exceptional_community_pathway": [
        "community school underprivileged children", "migrant children bridge school daily",
        "construction site children school NGO", "first generation learners school NGO",
        "slum children full day school NGO",
    ],
}

DISCOVERY_PATHWAY_LABELS = {
    "residential_life_system": "Residential / life-system",
    "full_day_alternative": "Full-day / alternative education",
    "child_protection_rehab": "Child protection / rehabilitation",
    "disability_special_needs": "Disability / special needs",
    "sports_arts_stem_vocational": "Sports / arts / STEM / vocational",
    "exceptional_community_pathway": "Exceptional community pathway",
}

# Benchmark taste anchors supplied during DFP 2.0 calibration.
# Important: these names are NOT used as Serper search queries. They exist only
# for post-processing/status if a benchmark organisation naturally appears.
# Discovery searches the underlying pathway grammar, not the organisation names.
DISCOVERY_SEED_ORGS = [
    "Christel House India", "Parikrma Humanity Foundation", "Mahesh Foundation",
    "Bridges of Sports Foundation", "Swami Vivekananda Youth Movement", "SVYM",
    "Kalkeri Sangeet Vidyalaya", "Kaliyuva Mane", "Divya Deepa Trust",
    "Shishu Mandir", "Samarthanam Trust", "Agastya International Foundation",
    "Namma Bhoomi", "Building Blocks", "Shanti Bhavan", "Belakoo",
    "Nele Foundation", "Don Bosco Makkalalaya",
]


# Pathway-pattern queries derived from the benchmark organisations.
# These deliberately avoid benchmark organisation names. The point is to find
# lookalikes, not to force known examples into the result set.
DISCOVERY_PATTERN_QUERIES = {
    "full_day_alternative": [
        '"whole child" "underprivileged children" school Bengaluru NGO',
        '"low income children" "school to college" Bengaluru NGO',
        '"urban poor children" "long term education" Bengaluru NGO',
        '"children from slums" "higher education" school NGO Bengaluru',
        '"free school" "healthcare" "meals" children Karnataka',
        '"underprivileged children" "career guidance" school NGO Karnataka',
        '"first generation learners" "college" "school" Karnataka NGO',
        '"out of school children" "alternative education" Karnataka NGO',
        '"school dropouts" "SSLC" "alternative school" Karnataka NGO',
        '"bridge school" "child labour" "mainstreaming" Karnataka NGO',
        '"NIOS" "underprivileged children" Karnataka NGO',
        '"children from low income communities" "life skills" school Bengaluru NGO',
    ],
    "residential_life_system": [
        '"HIV affected children" "education" "home" Karnataka NGO',
        '"medically vulnerable children" "education" Karnataka NGO',
        '"tribal students" "residential school" Karnataka NGO',
        '"rural students" "residential education" Karnataka NGO',
        '"student hostel" "first generation learners" Karnataka NGO',
        '"girls hostel" "higher education" rural Karnataka NGO',
        '"children home" "SSLC" "PUC" Karnataka NGO',
        '"residential school" "life skills" underprivileged children Karnataka',
        '"rural children" "hostel" "college" Karnataka trust',
        '"tribal children" "hostel" "education" Karnataka trust',
        '"children without parental care" "education" Karnataka NGO',
    ],
    "sports_arts_stem_vocational": [
        '"sports for development" children Karnataka NGO',
        '"rural athletes" nutrition Karnataka NGO',
        '"girls sports" underprivileged Karnataka NGO',
        '"athletics" "rural children" Karnataka NGO',
        '"football" "slum children" academy Bengaluru NGO',
        '"music" "underprivileged children" Karnataka NGO',
        '"performing arts" "underprivileged children" Karnataka NGO',
        '"theatre" "rural children" Karnataka NGO',
        '"drama" "underprivileged children" Bengaluru NGO',
        '"STEM center" "underserved children" Karnataka NGO',
        '"mobile science lab" "rural children" Karnataka NGO',
        '"hands on science" "rural students" Karnataka NGO',
        '"robotics" "rural students" Karnataka NGO',
        '"maker lab" "underprivileged children" Karnataka NGO',
    ],
    "child_protection_rehab": [
        '"rescued girls" "education" "home" Karnataka NGO',
        '"child labourers" "mainstreamed" school Karnataka NGO',
        '"street children" "shelter" "education" Karnataka NGO',
        '"abandoned children" "SSLC" Karnataka NGO',
        '"children in need of care and protection" "education" Karnataka NGO',
        '"open shelter" children education Karnataka NGO',
    ],
    "disability_special_needs": [
        '"special school" "life skills" "vocational" children Karnataka NGO',
        '"children with disabilities" "arts" "sports" education Karnataka NGO',
        '"blind students" "hostel" "education" Karnataka NGO',
        '"hearing impaired children" "residential" education Karnataka NGO',
        '"special needs children" "therapy" "school" low income Karnataka',
    ],
}

def _contains_benchmark_name(text: str) -> bool:
    t = _norm_text(text)
    return any(_norm_text(seed) in t for seed in DISCOVERY_SEED_ORGS)

# Terms used to enforce Karnataka proof without relying on the query string itself.
KARNATAKA_GEO_TERMS = [
    "karnataka", "bengaluru", "bangalore", "bengaluru urban", "bengaluru rural", "mysuru", "mysore",
    "belagavi", "belgaum", "koppal", "raichur", "kalaburagi", "gulbarga", "dakshina kannada",
    "mangaluru", "mangalore", "udupi", "ballari", "bellary", "vijayanagara", "hospet", "hosapete",
    "bidar", "dharwad", "hubballi", "hubli", "haveri", "mandya", "tumakuru", "tumkur",
    "kolar", "shivamogga", "shimoga", "chitradurga", "yadgir", "vijayapura", "bijapur",
    "bagalkot", "gadag", "hassan", "ramanagara", "chikkaballapur", "chamarajanagar",
    "kodagu", "coorg", "uttara kannada", "karwar", "chikkamagaluru", "chikmagalur", "davanagere",
    "sullia", "sulia", "saragur", "h d kote", "h d kote", "kalkeri", "devanahalli", "kgf",
]

WRONG_STATE_GEO_TERMS = [
    "andhra pradesh", "madanapalle", "madnapalle", "kuppam", "chittoor", "tirupati", "annamayya",
    "tamil nadu", "coimbatore", "cbe", "chennai", "madurai", "hosur",
    "maharashtra", "mumbai", "pune", "nagpur", "nanded",
    "assam", "baragolai", "tinsukia", "dibrugarh",
    "dehradun", "uttarakhand", "himachal", "dharamshala", "clement town",
    "kerala", "telangana", "hyderabad", "delhi", "gurgaon", "gurugram",
]

WEAK_SOURCE_DOMAINS = [
    "give.do", "donatekart.com", "ketto.org", "milaap.org", "impactguru.com", "gofundme.com",
    "grokipedia.com", "autismconnect.com", "justdial.com", "sulekha.com", "ngosindia.com",
    "ngofeed.com", "indiangoslist.com", "zaubacorp.com", "indiamart.com", "yellowpages",
]

# Search-level garbage blocking. This prevents Serper credits being wasted on
# predictable source noise that we already know should not enter the main output.
# Can be overridden from Render if needed. Keep as a single Google-compatible
# string so the query preview shows exactly what will be searched.
DISCOVERY_QUERY_NEGATIVE_FILTERS = os.environ.get(
    "DISCOVERY_QUERY_NEGATIVE_FILTERS",
    "-site:facebook.com -site:instagram.com -site:linkedin.com -site:x.com -site:twitter.com "
    "-site:justdial.com -site:sulekha.com -site:give.do -site:donatekart.com -site:ketto.org "
    "-site:milaap.org -site:impactguru.com -site:gofundme.com -site:grokipedia.com "
    "-site:autismconnect.com -site:ngosindia.com -site:ngofeed.com -site:indiangoslist.com "
    "-site:zaubacorp.com -site:wikipedia.org -site:researchgate.net -site:academia.edu "
    "-site:scribd.com -site:slideshare.net"
).strip()

def _apply_discovery_query_filters(q: str) -> str:
    q = re.sub(r"\s+", " ", (q or "").strip())
    if not q or not DISCOVERY_QUERY_NEGATIVE_FILTERS:
        return q
    if "-site:facebook.com" in q or "-site:justdial.com" in q:
        return q
    return f"{q} {DISCOVERY_QUERY_NEGATIVE_FILTERS}".strip()

def _strip_discovery_query_filters(q: str) -> str:
    q = re.sub(r"\s+", " ", (q or "").strip())
    if not q:
        return q
    return re.sub(r"\s+-site:[^\s]+", "", q).strip()

CREDIBLE_NEWS_DOMAINS = [
    "thehindu.com", "deccanherald.com", "newindianexpress.com", "timesofindia.indiatimes.com",
    "indianexpress.com", "starofmysore.com", "mangalorean.com", "mangaloretoday.com",
    "thebetterindia.com", "yourstory.com", "citizenmatters.in", "edexlive.com",
    "educationworld.in", "hindustantimes.com", "deccanchronicle.com",
]

DISTINCTIVENESS_TERMS = [
    "whole child", "k-12", "college", "career", "job", "alumni", "sslc", "puc", "higher education",
    "mainstream", "mainstreaming", "dropout", "out of school", "first generation", "life skills", "vocational",
    "rehabilitation", "reintegration", "therapy", "counselling", "counseling", "residential", "hostel",
    "tribal", "rural", "hiv", "medically", "rescued", "abandoned", "child labour", "sports", "athlete",
    "music", "arts", "theatre", "drama", "dance", "stem", "science", "robotics", "innovation", "maker",
    "yoga", "media", "covered by", "award", "success story", "scholarship", "transition", "employment",
]

def _norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())

def _domain_of(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""

def _is_seed_org_name(name: str) -> bool:
    n = _norm_text(name)
    return any(_norm_text(seed) in n or n in _norm_text(seed) for seed in DISCOVERY_SEED_ORGS)

def _normalise_org_key(name: str) -> str:
    n = _norm_text(name)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\b(the|india|indian|foundation|trust|society|samsthe|samiti|charitable|educational|ngo|organisation|organization|school|academy|home|hostel)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n or _norm_text(name)


# Output tiers for General Discovery. Benchmark names are calibration/reference only;
# they can remain visible if they naturally surface, but they are not counted as
# fresh discovery. Manual-check promising rows protect the run from a catastrophic
# 0-output outcome when strict gates are too harsh.
DISCOVERY_STATUS_FRESH = "Fresh strong lead"
DISCOVERY_STATUS_MANUAL = "Manual-check promising"
DISCOVERY_STATUS_BENCHMARK = "Benchmark reference"

def _discovery_status_for_org(name: str, manual: bool = False) -> str:
    if _is_seed_org_name(name):
        return DISCOVERY_STATUS_BENCHMARK
    return DISCOVERY_STATUS_MANUAL if manual else DISCOVERY_STATUS_FRESH

def _is_actionable_discovery_row(row: dict) -> bool:
    status = _norm_text(row.get("Status") or row.get("Repository Status") or row.get("Output Tier") or "")
    return bool(status and "benchmark" not in status and "reject" not in status)

def _discovery_min_output_rows(budget: int) -> int:
    # Failsafe, not a quality target. It only kicks in after article review, and
    # only promotes already-reviewed/search-vetted candidates as manual-check.
    env = os.environ.get("DISCOVERY_MIN_OUTPUT_ROWS")
    if env:
        try:
            return max(0, int(env))
        except Exception:
            pass
    try:
        b = int(budget or 0)
    except Exception:
        b = 0
    if b >= 1500:
        return 20
    if b >= 800:
        return 12
    if b >= 300:
        return 8
    return 5

def _source_quality(url: str) -> str:
    d = _domain_of(url)
    if not d:
        return "unknown"
    if any(w in d for w in WEAK_SOURCE_DOMAINS):
        return "weak_directory_or_donation"
    if any(w in d for w in CREDIBLE_NEWS_DOMAINS):
        return "credible_news"
    if any(w in d for w in ["facebook.com", "instagram.com", "linkedin.com", "youtube.com", "x.com", "twitter.com"]):
        return "social_only"
    return "official_or_primary_candidate"

def _is_pdf_url(url: str) -> bool:
    path = urlparse(url or "").path.lower()
    return path.endswith(".pdf") or ".pdf" in path

def _is_useful_discovery_pdf(url: str, text: str) -> bool:
    """Allow official-domain annual/impact/brochure PDFs as evidence.

    The old flow skipped every PDF, which hid useful NGO annual reports. We still
    reject random government/research PDFs, but keep reports/brochures that look
    like primary evidence for a child pathway.
    """
    if not _is_pdf_url(url):
        return False
    d = _domain_of(url)
    t = _norm_text(" ".join([url, text or ""]))
    if any(bad in d for bad in ["researchgate", "academia", "scribd", "slideshare", "wikipedia"]):
        return False
    if any(gov in d for gov in [".gov", "gov.in", "karnataka.gov", "nic.in"]):
        return False
    report_signal = any(x in t for x in [
        "annual report", "annual-report", "impact report", "impact-report", "brochure",
        "prospectus", "report 202", "annual_report", "impact_report", "newsletter",
        "children", "students", "school", "education", "hostel", "residential", "rehabilitation",
    ])
    org_signal = any(x in t for x in ["ngo", "foundation", "trust", "society", "samsthe", "seva", "school", "vidyalaya", "children"])
    return bool(report_signal and org_signal)

def _source_is_search_noise(url: str) -> bool:
    sq = _source_quality(url)
    return sq in {"weak_directory_or_donation", "social_only"}

def _has_karnataka_proof(text: str) -> bool:
    t = _norm_text(text)
    return any(term in t for term in KARNATAKA_GEO_TERMS)

def _has_wrong_state_signal(text: str) -> bool:
    t = _norm_text(text)
    return any(term in t for term in WRONG_STATE_GEO_TERMS)

def _state_gate_for_discovery(state: str, location: str, title: str, snippet: str, article_text: str) -> tuple[bool, str]:
    if _norm_text(state) != "karnataka":
        return True, "state_gate_not_configured_for_this_state"
    evidence = " ".join([location or "", title or "", snippet or "", (article_text or "")[:7000]])
    has_ka = _has_karnataka_proof(evidence)
    has_wrong = _has_wrong_state_signal(" ".join([location or "", title or "", snippet or "", (article_text or "")[:3500]]))
    if not has_ka and has_wrong:
        return False, "wrong_state_no_karnataka_proof"
    if not has_ka:
        return False, "no_karnataka_centre_or_program_proof"
    if has_wrong:
        return True, "karnataka_proof_with_wrong_state_caveat_manual_check"
    return True, "karnataka_proof_found"

def _distinctiveness_found(text: str) -> bool:
    t = _norm_text(text)
    return any(term in t for term in DISTINCTIVENESS_TERMS)

ORG_NAME_RE = re.compile(
    r"([A-Z][A-Za-z&.'’()\- ]{2,95}\s(?:Foundation|Trust|Society|Samsthe|Samiti|Sanstha|Mission|Seva|Organisation|Organization|NGO|School|Academy|Home|Hostel|Vidyalaya|Kendra|Mandir|Centre|Center))"
)

def _extract_traceable_org_name(title: str, snippet: str, url: str, article_text: str) -> str:
    text = " ".join([title or "", snippet or "", (article_text or "")[:5000]])
    m = ORG_NAME_RE.search(text)
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip(" -,.:|/()")
        if len(name) >= 4:
            return name
    # Domain fallback for official-looking domains. This catches pages like
    # svym.org/institutions/ or agastya.org where the org name is obvious from
    # the domain but Claude may have been over-strict.
    d = _domain_of(url)
    if d and _source_quality(url) == "official_or_primary_candidate":
        stem = d.split(".")[0]
        if stem and stem not in {"www", "org", "ngo", "india"} and len(stem) >= 4:
            if stem.isupper() or len(stem) <= 5:
                return stem.upper()
            return re.sub(r"[-_]+", " ", stem).title()
    return ""

def _has_child_pathway_signal(text: str) -> bool:
    t = _norm_text(text)
    child = any(x in t for x in ["children", "child", "students", "student", "school", "girls", "boys", "adolescent", "youth"])
    pathway = any(x in t for x in [
        "residential", "hostel", "home", "education", "school", "whole child", "k-12",
        "dropout", "out of school", "mainstream", "first generation", "tribal", "rural",
        "hiv", "rehabilitation", "special school", "disability", "sports", "athlete",
        "music", "arts", "theatre", "drama", "stem", "science", "robotics", "life skills",
        "vocational", "counselling", "therapy", "sslc", "puc", "college", "career",
    ])
    return bool(child and pathway)

def _manual_traceable_discovery_row(state: str, category: str, query: str, url: str, title: str, snippet: str, source: str, article_text: str) -> dict | None:
    source_quality = _source_quality(url)
    if source_quality in {"weak_directory_or_donation", "social_only"}:
        return None
    if _is_pdf_url(url) and not _is_useful_discovery_pdf(url, " ".join([title, snippet, article_text[:2000]])):
        return None
    geo_ok, geo_note = _state_gate_for_discovery(state, "", title, snippet, article_text)
    if not geo_ok and state.strip().lower() == "karnataka":
        return None
    combined = " ".join([title or "", snippet or "", (article_text or "")[:5000]])
    if not _has_child_pathway_signal(combined):
        return None
    ngo = _extract_traceable_org_name(title, snippet, url, article_text)
    if not ngo:
        return None
    if _looks_generic_low_value(category, ngo, combined, article_text):
        return None
    pathway_label = DISCOVERY_PATHWAY_LABELS.get(category, category)
    return {
        "Organisation": ngo,
        "NGO Name": ngo,
        "Website / Source": url,
        "Source URL": url,
        "Article URL": url,
        "Location": state,
        "State": state,
        "District": "Statewide",
        "Pathway": pathway_label,
        "Story Category": pathway_label,
        "Story Type": pathway_label,
        "Why It Belongs": "Traceable Karnataka child-pathway source found, but classifier could not confidently validate the full transformation standard. Needs manual review.",
        "Transformation / Distinctiveness Signal": "Manual-check fallback from traceable source; verify depth, distinctiveness, and direct pathway ownership before outreach.",
        "Story Title": (title or ngo)[:220],
        "Story Summary": (snippet or "Traceable child-pathway source found. Needs manual review.")[:900],
        "Why NGO Is Interesting": "Potentially relevant child-pathway organisation; moved from not-traceable into manual check because the organisation/source is traceable.",
        "Repository Status": DISCOVERY_STATUS_MANUAL,
        "Status": DISCOVERY_STATUS_MANUAL,
        "Output Tier": DISCOVERY_STATUS_MANUAL,
        "Traced Place": state,
        "Source": source or urlparse(url).netloc,
        "Source Quality": source_quality,
        "State Gate": geo_note,
        "Confidence": "low",
        "Discovery Query": query,
        "Notes": "Fallback row: do not treat as a final clean lead until manually verified.",
    }

def _looks_generic_low_value(pathway: str, name: str, why: str, article_text: str) -> bool:
    combined = _norm_text(" ".join([name, why, article_text[:4000]]))
    if _is_seed_org_name(name):
        return False
    # Generic special schools and generic CCIs/orphanages should not dominate main output.
    generic_disability = pathway == "disability_special_needs" and any(x in combined for x in ["special school", "autism school", "disabled children", "children with disabilities"]) and not _distinctiveness_found(combined)
    generic_care = pathway in {"residential_life_system", "child_protection_rehab"} and any(x in combined for x in ["orphanage", "children's home", "childrens home", "child care institution", "cci"]) and not _distinctiveness_found(combined)
    govt_support_only = any(x in combined for x in ["government school support", "supports government schools", "school transformation", "teacher training", "volunteers teach", "volunteer teaching"]) and not any(x in combined for x in ["runs", "residential", "own school", "campus", "hostel", "full day", "full-day"])
    return bool(generic_disability or generic_care or govt_support_only)

def _normalise_discovery_pathways(pathways: str | None) -> list[str]:
    default = ["residential_life_system", "full_day_alternative", "child_protection_rehab", "disability_special_needs", "sports_arts_stem_vocational"]
    if not pathways:
        return default
    picked: list[str] = []
    aliases = {
        "residential": "residential_life_system", "residential_life_system": "residential_life_system", "life_system": "residential_life_system",
        "full_day": "full_day_alternative", "alternative": "full_day_alternative", "full_day_alternative": "full_day_alternative",
        "child_protection": "child_protection_rehab", "rehab": "child_protection_rehab", "child_protection_rehab": "child_protection_rehab",
        "disability": "disability_special_needs", "special_needs": "disability_special_needs", "disability_special_needs": "disability_special_needs",
        "sports": "sports_arts_stem_vocational", "arts": "sports_arts_stem_vocational", "stem": "sports_arts_stem_vocational", "vocational": "sports_arts_stem_vocational", "sports_arts_stem_vocational": "sports_arts_stem_vocational",
        "community": "exceptional_community_pathway", "exceptional_community_pathway": "exceptional_community_pathway",
    }
    for raw in re.split(r"[,|;]+", pathways or ""):
        key = re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
        key = aliases.get(key, key)
        if key in DISCOVERY_PATHWAYS and key not in picked:
            picked.append(key)
    return picked or default

DISCOVERY_CORE_DISTRICTS = {
    "karnataka": [
        "Bengaluru", "Mysuru", "Belagavi", "Koppal", "Raichur", "Kalaburagi",
        "Dakshina Kannada", "Udupi", "Ballari", "Vijayanagara", "Bidar", "Dharwad",
        "Haveri", "Mandya", "Tumakuru", "Kolar", "Shivamogga", "Chitradurga",
        "Yadgir", "Vijayapura", "Bagalkot", "Gadag", "Hassan", "Ramanagara",
        "Chikkaballapur", "Chamarajanagar", "Kodagu", "Uttara Kannada", "Chikkamagaluru",
        "Davanagere", "Bengaluru Rural",
    ]
}

# Query-budget precedence for General Discovery.
# For the default selected 5 pathways and a 5,500-query Extended Run, this yields:
# Residential/life-system: 1,700 | Full-day/alternative: 1,400 | Sports/arts/STEM/niche: 1,000
# Child protection/rehab: 700 | Disability/special needs: 700.
# This is query allocation, not an NGO score.
DISCOVERY_WEIGHTS = {
    # Updated after output review: do not over-surface generic CCI/special-school rows.
    # Increase whole-child/alternative and niche sports/arts/STEM discovery, while keeping
    # child protection and disability present but lower unless distinctive.
    "residential_life_system": 1400,
    "full_day_alternative": 1500,
    "sports_arts_stem_vocational": 1300,
    "child_protection_rehab": 700,
    "disability_special_needs": 600,
    "exceptional_community_pathway": 100,
}

DISCOVERY_QUERY_TEMPLATES = {
    "residential_life_system": [
        '"{place}" "residential school" "underprivileged children" NGO',
        '"{place}" "free boarding" "rural students" trust',
        '"{place}" "children\'s home" "education" NGO',
        '"{place}" "girls hostel" "free education" NGO',
        '"{place}" "student hostel" "rural students" trust',
        '"{place}" "chatralaya" "students" "meals"',
        '"{place}" "orphanage" "school" "children" NGO',
        '"{place}" "tribal residential school" "students"',
        '"{place}" "HIV affected children" "home" "education"',
        '"{place}" "residential special school" children',
    ],
    "full_day_alternative": [
        '"{place}" "free school" "underprivileged children" NGO',
        '"{place}" "alternative school" "school dropouts"',
        '"{place}" "open school" "school dropouts" NGO',
        '"{place}" "NIOS" "underprivileged children" NGO',
        '"{place}" "school dropouts" "mainstreaming" NGO',
        '"{place}" "whole child education" "underprivileged"',
        '"{place}" "first generation learners" "school" NGO',
        '"{place}" "free school" "meals" "health" children',
    ],
    "child_protection_rehab": [
        '"{place}" "child labour" "rehabilitation" "education" NGO',
        '"{place}" "street children" "shelter" NGO',
        '"{place}" "open shelter" "children" NGO',
        '"{place}" "rescued children" "education" "home"',
        '"{place}" "trafficking survivors" children rehabilitation NGO',
        '"{place}" "CWC" "children home" "education"',
        '"{place}" "girls home" "rehabilitation" NGO',
        '"{place}" "abandoned children" "SSLC" NGO',
        '"{place}" "child labourers" "school" rehabilitation',
    ],
    "disability_special_needs": [
        '"{place}" "special school" "underprivileged children" NGO',
        '"{place}" "blind school" "free boarding"',
        '"{place}" "hearing impaired" children school hostel',
        '"{place}" "autism school" "underprivileged"',
        '"{place}" "intellectual disability" children school NGO',
        '"{place}" "children with disabilities" "residential school"',
        '"{place}" "special school" "life skills" children',
        '"{place}" "disabled children" "vocational training" NGO',
    ],
    "sports_arts_stem_vocational": [
        '"{place}" "sports academy" "underprivileged children"',
        '"{place}" "rural athletes" "nutrition" NGO',
        '"{place}" "football" "slum children" academy',
        '"{place}" "girls sports" "underprivileged" NGO',
        '"{place}" "surfing" "underprivileged children"',
        '"{place}" "boxing" "slum children" NGO',
        '"{place}" "kabaddi" "rural children" NGO',
        '"{place}" "music" "underprivileged children" NGO',
        '"{place}" "theatre" "underprivileged children" NGO',
        '"{place}" "drama" "underprivileged children" NGO',
        '"{place}" "performing arts" "low income children"',
        '"{place}" "dance" "underserved children" NGO',
        '"{place}" "STEM" "underprivileged students" NGO',
        '"{place}" "robotics" "rural children" NGO',
        '"{place}" "coding" "government school students" NGO',
        '"{place}" "maker lab" "rural students"',
        '"{place}" "adolescent girls" vocational education NGO',
    ],
    "exceptional_community_pathway": [
        '"{place}" "community school" "underprivileged children" NGO',
        '"{place}" "migrant children" "bridge school" daily NGO',
        '"{place}" "construction site children" "school" NGO',
        '"{place}" "first generation learners" "school" NGO',
        '"{place}" "slum children" "full day school" NGO',
    ],
}

DISCOVERY_STATEWIDE_TEMPLATES = {
    "residential_life_system": [
        '"{state}" "residential school" "underprivileged children" NGO',
        '"{state}" "children\'s home" "education" NGO',
        '"{state}" "free hostel" "rural students" trust',
        '"{state}" "girls hostel" "free education"',
    ],
    "full_day_alternative": [
        '"{state}" "alternative school" "school dropouts" NGO',
        '"{state}" "free school" "underprivileged children" NGO',
        '"{state}" "open school" "school dropouts" NGO',
    ],
    "child_protection_rehab": [
        '"{state}" "child labour" "rehabilitation" "education" NGO',
        '"{state}" "street children" "shelter" NGO',
        '"{state}" "rescued children" "education" "home"',
    ],
    "disability_special_needs": [
        '"{state}" "special school" "underprivileged children" NGO',
        '"{state}" "blind school" "free boarding"',
        '"{state}" "children with disabilities" "residential school"',
    ],
    "sports_arts_stem_vocational": [
        '"{state}" "sports academy" "underprivileged children"',
        '"{state}" "rural athletes" "nutrition" NGO',
        '"{state}" "girls sports" "underprivileged" NGO',
        '"{state}" "music" "underprivileged children" NGO',
        '"{state}" "theatre" "underprivileged children" NGO',
        '"{state}" "drama" "underprivileged children" NGO',
        '"{state}" "arts education" "underprivileged children"',
        '"{state}" "STEM" "underprivileged students" NGO',
        '"{state}" "robotics" "rural children" NGO',
    ],
    "exceptional_community_pathway": [
        '"{state}" "community school" "underprivileged children" NGO',
    ],
}

DISCOVERY_OUTCOME_TEMPLATES = {
    "residential_life_system": [
        '"{place}" "children\'s home" "SSLC" NGO',
        '"{place}" "orphanage" "PUC" students',
        '"{place}" "girls hostel" "higher education"',
    ],
    "full_day_alternative": [
        '"{place}" "school dropouts" "mainstreamed" school NGO',
        '"{place}" "underprivileged children" "college" "school" NGO',
    ],
    "child_protection_rehab": [
        '"{place}" "rescued girls" "education" home NGO',
        '"{place}" "street children" "mainstreamed" school NGO',
    ],
    "disability_special_needs": [
        '"{place}" "blind students" hostel education',
        '"{place}" "special school" "life skills" children',
    ],
    "sports_arts_stem_vocational": [
        '"{place}" "rural athletes" nutrition NGO',
        '"{place}" "girls sports" underprivileged NGO',
        '"{place}" "sports" "first generation" children',
        '"{place}" "music school" "free education" children',
        '"{place}" "theatre in education" underprivileged children',
        '"{place}" "coding" "government school students" NGO',
        '"{place}" "maker lab" "rural students"',
    ],
    "exceptional_community_pathway": [],
}

def _balanced_discovery_districts(state_clean: str) -> list[str]:
    base = STORY_STATE_DISTRICTS.get(state_clean.lower(), [])
    preferred = DISCOVERY_CORE_DISTRICTS.get(state_clean.lower(), [])
    out: list[str] = []
    for d in preferred + base:
        if d and d not in out:
            out.append(d)
    return out or [state_clean]

def _allocate_discovery_budget(pathways: list[str], budget: int) -> dict[str, int]:
    selected = [p for p in pathways if p in DISCOVERY_PATHWAY_LABELS]
    if not selected:
        selected = _normalise_discovery_pathways("")
    weights = {p: DISCOVERY_WEIGHTS.get(p, 10) for p in selected}
    # In small test runs, ensure every selected main pathway is actually tested.
    if budget <= 300 and "exceptional_community_pathway" not in selected:
        base = {p: 1 for p in selected}
        if len(selected) <= 5:
            # Keep calibration aligned with the full-run precedence, but still
            # ensure every selected pathway gets tested.
            rough = {
                "residential_life_system": 51,
                "full_day_alternative": 55,
                "sports_arts_stem_vocational": 48,
                "child_protection_rehab": 25,
                "disability_special_needs": 21,
            }
            total = sum(rough.get(p, 20) for p in selected)
            return {p: max(1, round(budget * rough.get(p, 20) / total)) for p in selected}
    total_weight = max(1, sum(weights.values()))
    allocated = {p: max(1, int(budget * weights[p] / total_weight)) for p in selected}
    # Fix rounding so allocation equals budget.
    while sum(allocated.values()) < budget:
        p = max(selected, key=lambda x: weights[x])
        allocated[p] += 1
    while sum(allocated.values()) > budget:
        p = max(selected, key=lambda x: allocated[x])
        if allocated[p] > 1:
            allocated[p] -= 1
        else:
            break
    return allocated

def _build_discovery_pool_for_pathway(state_clean: str, pathway: str, districts: list[str]) -> list[dict]:
    """Build a large, ordered query pool for both small tests and extended runs.

    The previous version produced a good 200-query calibration mix, but its unique
    query pool was too small for a 5k+ extended Karnataka run. This version keeps
    the high-precision district/pathway queries first, then adds controlled
    source-type and weak-digital variants so an extended run can go deep without
    falling back to generic NGO searches.
    """
    pool: list[dict] = []
    seen: set[str] = set()

    def add(q: str, family: str, district: str = ""):
        q = re.sub(r"\s+", " ", q).strip()
        # Safety guard: general discovery must not burn Serper credits searching
        # literal benchmark names. Benchmarks are taste anchors, not search terms.
        if _contains_benchmark_name(_strip_discovery_query_filters(q)):
            return
        q = _apply_discovery_query_filters(q)
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        pool.append({
            "query": q,
            "category": pathway,
            "pathway": pathway,
            "pathway_label": DISCOVERY_PATHWAY_LABELS.get(pathway, pathway),
            "query_family": family,
            "district": district,
        })

    # 0) Pathway-pattern anchors derived from known good examples.
    # Do NOT search benchmark organisation names here. These queries describe
    # the underlying pathway: long-horizon school, dropout mainstreaming,
    # tribal/rural residential education, serious sports/arts/STEM, etc.
    for q in DISCOVERY_PATTERN_QUERIES.get(pathway, []):
        add(q, "pathway_pattern", "Pattern")

    # 1) High-confidence statewide seeds.
    for t in DISCOVERY_STATEWIDE_TEMPLATES.get(pathway, []):
        add(t.format(state=state_clean), "statewide", "Statewide")

    templates = DISCOVERY_QUERY_TEMPLATES.get(pathway, [])
    outcome_templates = DISCOVERY_OUTCOME_TEMPLATES.get(pathway, [])
    phrase_bank = DISCOVERY_PATHWAYS.get(pathway, [])

    # 2) District/pathway floor. This is what small test runs should mostly use.
    for i, district in enumerate(districts):
        chosen = templates[i % len(templates):] + templates[:i % len(templates)] if templates else []
        for t in chosen[:5 if pathway != "exceptional_community_pathway" else 2]:
            add(t.format(place=district, state=state_clean), "district_pathway", district)
        for t in outcome_templates[:3]:
            add(t.format(place=district, state=state_clean), "transformation_signal", district)

    # 3) Local/weak-digital terms. These matter most outside Bengaluru and for
    # residential/child-care institutions that do not have polished websites.
    local_terms_by_pathway = {
        "residential_life_system": [
            '"{place}" "chatralaya" students',
            '"{place}" "anathashrama" children school',
            '"{place}" "makkala" education trust',
            '"{place}" "gurukula" girls free education',
            '"{place}" "balakara mane" school',
            '"{place}" "balakiyara mane" education',
            '"{place}" "student home" rural students',
        ],
        "child_protection_rehab": [
            '"{place}" "makkala sahaya" children NGO',
            '"{place}" "open shelter" children',
            '"{place}" "children home" CWC',
        ],
        "disability_special_needs": [
            '"{place}" "vishesha makkala" school',
            '"{place}" "special children" school trust',
        ],
    }
    for district in districts:
        for t in local_terms_by_pathway.get(pathway, []):
            add(t.format(place=district, state=state_clean), "local_weak_digital", district)

    # 4) Extended-run variants. These are only reached when the budget is large.
    # They deliberately search source-types where serious institutions appear:
    # official pages, donor pages, annual reports, CSR mentions, local news and
    # contact/program pages. This is still pathway-specific; it is not generic NGO search.
    variant_suffixes = [
        "NGO", "trust", "foundation", "charitable trust", "official website",
        "annual report", "CSR", "donor", "partners", "contact", "admissions",
        "students", "children", "education", "meals", "nutrition", "hostel",
        "school", "program", "success story", "SSLC", "PUC", "higher education",
    ]
    # Keep exceptional community very tight even in extended runs.
    if pathway == "exceptional_community_pathway":
        variant_suffixes = ["NGO", "community school", "daily", "full day school", "official website"]

    for district in districts:
        for phrase in phrase_bank:
            clean_phrase = phrase.replace('"', '').strip()
            if not clean_phrase:
                continue
            # Use multiple forms because overly quoted long phrases can be too strict.
            add(f'"{district}" "{state_clean}" "{clean_phrase}"', "extended_phrase", district)
            for suffix in variant_suffixes:
                add(f'"{district}" "{clean_phrase}" "{suffix}"', "extended_source_type", district)
                add(f'"{district}" "{state_clean}" "{clean_phrase}" "{suffix}"', "extended_state_anchored", district)

    # 5) Statewide source-type variants for hidden/non-obvious institutions.
    for phrase in phrase_bank:
        clean_phrase = phrase.replace('"', '').strip()
        if not clean_phrase:
            continue
        for suffix in ["official website", "annual report", "CSR", "donor", "news", "children", "students"]:
            add(f'"{state_clean}" "{clean_phrase}" "{suffix}"', "statewide_source_type", "Statewide")

    return pool

def _discovery_state_queries(state: str, pathways: list[str], budget: int) -> list[dict]:
    state_clean = re.sub(r"\s+", " ", state.strip())
    districts = _balanced_discovery_districts(state_clean)
    selected = [p for p in pathways if p in DISCOVERY_PATHWAY_LABELS]
    if not selected:
        selected = _normalise_discovery_pathways("")
    allocation = _allocate_discovery_budget(selected, budget)
    pools = {p: _build_discovery_pool_for_pathway(state_clean, p, districts) for p in selected}
    picked_by_pathway: dict[str, list[dict]] = {}
    for pathway, count in allocation.items():
        pool = pools.get(pathway, [])
        rows: list[dict] = []
        idx = 0
        while len(rows) < count and pool:
            item = dict(pool[idx % len(pool)])
            if item not in rows:
                rows.append(item)
            idx += 1
            if idx > count * 10:
                break
        picked_by_pathway[pathway] = rows[:count]
    # Interleave, so a 200-query calibration tests every pathway instead of exhausting one bucket first.
    final: list[dict] = []
    pointers = {p: 0 for p in selected}
    while len(final) < budget and any(pointers[p] < len(picked_by_pathway.get(p, [])) for p in selected):
        for p in selected:
            rows = picked_by_pathway.get(p, [])
            if pointers[p] < len(rows):
                final.append(rows[pointers[p]])
                pointers[p] += 1
                if len(final) >= budget:
                    break
    # If a pool was too short, fill with adaptive but still balanced district-specific variants.
    seen_q = {x["query"].lower() for x in final}
    idx = 0
    while len(final) < budget and selected:
        p = selected[idx % len(selected)]
        district = districts[idx % len(districts)]
        label = DISCOVERY_PATHWAY_LABELS.get(p, p)
        q = _apply_discovery_query_filters(f'"{district}" "{state_clean}" "{label}" NGO children pathway')
        if q.lower() not in seen_q and not _contains_benchmark_name(_strip_discovery_query_filters(q)):
            seen_q.add(q.lower())
            final.append({"query": q, "category": p, "pathway": p, "pathway_label": label, "query_family": "adaptive_balanced", "district": district})
        idx += 1
        if idx > budget * 5:
            break
    return final[:budget]

# -----------------------------------------------------------------------------
# Statewide Story Discovery v2 — 2k query capped, NGO-centric discovery
# -----------------------------------------------------------------------------
# This is separate from the older district story endpoint. It searches a whole
# state across multiple categories and only emits rows where the story can be
# traced back to a specific NGO/foundation/trust/society/non-profit.
STORY_STATE_QUERY_BUDGET = int(os.environ.get("STORY_STATE_QUERY_BUDGET", "2000"))
STORY_STATE_MAX_BUDGET = int(os.environ.get("STORY_STATE_MAX_BUDGET", "5500"))
STORY_STATE_RESULTS_PER_QUERY = int(os.environ.get("STORY_STATE_RESULTS_PER_QUERY", "3"))
DISCOVERY_RESULTS_PER_QUERY = int(os.environ.get("DISCOVERY_RESULTS_PER_QUERY", "5"))
STORY_STATE_MAX_ARTICLES = int(os.environ.get("STORY_STATE_MAX_ARTICLES", "500"))
STORY_STATE_PACE_SEC = float(os.environ.get("STORY_STATE_PACE_SEC", "0.10"))

STORY_STATE_CATEGORIES = {
    "sports": ["sports", "athlete", "football", "medal", "rural sports", "children sports"],
    "music_arts": ["music", "classical music", "dance", "arts", "theatre", "performing arts"],
    "stem_science": ["science", "STEM", "robotics", "mobile science lab", "innovation", "coding"],
    "academic_outcomes": ["student achievement", "scholarship", "JEE", "NEET", "UPSC", "board topper"],
    "child_rescue_rehab": ["child labour rescue", "street children", "rehabilitation", "child shelter", "dropout children"],
    "girls_education": ["girls education", "first generation girl learners", "adolescent girls", "rural girls"],
    "special_needs": ["special needs children", "learning disability", "autism", "blind children", "disability education"],
    "tribal_rural": ["tribal children", "rural children", "Adivasi children", "government school", "slum children"],
    "alumni_outcomes": ["alumni", "career outcome", "first generation learners", "college admission", "jobs"],
    "nutrition_health_recovery": ["nutrition", "malnutrition", "anganwadi", "early childhood", "midday meal"],
}

STORY_STATE_DISTRICTS = {
    "karnataka": ["Bengaluru", "Bengaluru Rural", "Mysuru", "Mandya", "Ramanagara", "Kolar", "Chikkaballapur", "Tumakuru", "Hassan", "Chamarajanagar", "Kodagu", "Dakshina Kannada", "Udupi", "Uttara Kannada", "Shivamogga", "Chikkamagaluru", "Davanagere", "Chitradurga", "Ballari", "Vijayanagara", "Koppal", "Raichur", "Yadgir", "Kalaburagi", "Bidar", "Vijayapura", "Bagalkot", "Belagavi", "Dharwad", "Gadag", "Haveri"],
    "tamil nadu": ["Chennai", "Coimbatore", "Madurai", "Salem", "Tiruchirappalli", "Tirunelveli", "Ranipet", "Nagapattinam"],
    "telangana": ["Hyderabad", "Warangal", "Karimnagar", "Medak", "Nalgonda"],
    "andhra pradesh": ["Anantapur", "Vijayawada", "Guntur", "Visakhapatnam", "Tirupati"],
    "maharashtra": ["Mumbai", "Pune", "Nagpur", "Nashik", "Aurangabad", "Kolhapur"],
}

def _normalise_story_categories(categories: str | None) -> list[str]:
    if not categories:
        return list(STORY_STATE_CATEGORIES.keys())
    picked = []
    for raw in re.split(r"[,|;]+", categories):
        key = re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
        aliases = {
            "music": "music_arts", "arts": "music_arts", "music_arts": "music_arts",
            "stem": "stem_science", "science": "stem_science", "stem_science": "stem_science",
            "academic": "academic_outcomes", "academics": "academic_outcomes", "academic_outcomes": "academic_outcomes",
            "rescue": "child_rescue_rehab", "rehab": "child_rescue_rehab", "child_rescue_rehab": "child_rescue_rehab",
            "girls": "girls_education", "girls_education": "girls_education",
            "special": "special_needs", "special_needs": "special_needs", "disability": "special_needs",
            "tribal": "tribal_rural", "rural": "tribal_rural", "tribal_rural": "tribal_rural",
            "alumni": "alumni_outcomes", "alumni_outcomes": "alumni_outcomes",
            "nutrition": "nutrition_health_recovery", "health": "nutrition_health_recovery", "nutrition_health_recovery": "nutrition_health_recovery",
            "sports": "sports",
        }
        key = aliases.get(key, key)
        if key in STORY_STATE_CATEGORIES and key not in picked:
            picked.append(key)
    return picked or list(STORY_STATE_CATEGORIES.keys())

def _story_state_queries(state: str, categories: list[str], budget: int) -> list[dict]:
    state_clean = re.sub(r"\s+", " ", state.strip())
    districts = STORY_STATE_DISTRICTS.get(state_clean.lower(), [])
    base_templates = [
        '"{state}" {kw} NGO children story',
        '{state} {kw} NGO underprivileged children',
        '{state} {kw} foundation children impact',
        '{state} {kw} trust children success story',
        '{state} {kw} non profit children outcome',
        '{state} {kw} NGO student achievement',
        '{state} {kw} NGO rural children',
        '{state} {kw} NGO slum children',
    ]
    district_templates = [
        '"{district}" "{state}" {kw} NGO children',
        '"{district}" "{state}" {kw} foundation children',
        '"{district}" "{state}" {kw} trust student story',
        '"{district}" "{state}" {kw} NGO impact',
    ]
    rows: list[dict] = []
    seen = set()
    def add(q: str, cat: str):
        q = re.sub(r"\s+", " ", q).strip()
        if q.lower() in seen:
            return
        seen.add(q.lower())
        rows.append({"query": q, "category": cat})
    for cat in categories:
        kws = STORY_STATE_CATEGORIES.get(cat, [cat])
        for kw in kws:
            for t in base_templates:
                add(t.format(state=state_clean, district="", kw=kw), cat)
            for district in districts:
                for t in district_templates:
                    add(t.format(state=state_clean, district=district, kw=kw), cat)
    outcome_words = ["award", "media story", "success story", "case study", "impact report", "children outcome", "government school", "low income children", "first generation learners", "dropout children"]
    idx = 0
    while len(rows) < budget and categories:
        cat = categories[idx % len(categories)]
        kw = STORY_STATE_CATEGORIES[cat][(idx // max(1, len(categories))) % len(STORY_STATE_CATEGORIES[cat])]
        ow = outcome_words[idx % len(outcome_words)]
        district = districts[idx % len(districts)] if districts else ""
        if district:
            add(f'"{district}" "{state_clean}" {kw} {ow} NGO', cat)
        else:
            add(f'"{state_clean}" {kw} {ow} NGO', cat)
        idx += 1
        if idx > budget * 5:
            break
    return rows[:budget]

def _serper_story_state_search(query: str, num: int | None = None) -> list[dict]:
    data = _serper_post({"q": query, "num": int(num or STORY_STATE_RESULTS_PER_QUERY), "gl": "in"}, timeout=25)
    out = []
    for res in data.get("organic", []) or []:
        out.append({"url": res.get("link", ""), "title": res.get("title", ""), "snippet": res.get("snippet", ""), "source": res.get("source", "")})
    kg = data.get("knowledgeGraph") or {}
    if isinstance(kg, dict) and (kg.get("website") or kg.get("url")):
        out.append({"url": kg.get("website") or kg.get("url"), "title": kg.get("title", ""), "snippet": kg.get("description", ""), "source": "knowledge_graph"})
    return out

def _score_story_candidate(item: dict) -> int:
    text = " ".join([item.get("title", ""), item.get("snippet", ""), item.get("category", "")]).lower()
    score = 0
    if any(w in text for w in ["ngo", "foundation", "trust", "society", "non-profit", "nonprofit", "organisation", "organization"]): score += 8
    if any(w in text for w in ["children", "child", "student", "girl", "school", "youth", "anganwadi"]): score += 5
    if any(w in text for w in ["underprivileged", "low income", "rural", "tribal", "slum", "migrant", "dropout", "orphan", "special needs", "government school"]): score += 5
    if any(w in text for w in ["story", "award", "success", "achievement", "rescued", "rehabilitation", "impact", "case study", "medal", "alumni"]): score += 4
    bad = ["admission", "fees", "fee structure", "jobs", "recruitment", "tender", "pdf", "wikipedia"]
    if any(w in text for w in bad): score -= 4
    url = (item.get("url") or "").lower()
    if any(b in url for b in ["facebook.", "instagram.", "youtube.", "linkedin.", "justdial", "sulekha", "ngodarpan"]): score -= 10
    return score


DISCOVERY_STRONG_SIGNALS = [
    "residential school", "free boarding", "hostel", "children's home", "childrens home", "child care institution",
    "cci", "open shelter", "street children", "child labour", "rehabilitation", "rescued children", "girls home",
    "special school", "blind school", "hearing impaired", "autism", "intellectual disability", "children with disabilities",
    "underprivileged children", "rural students", "tribal students", "dropout", "school dropouts", "alternative school",
    "free school", "sports academy", "rural athletes", "music", "theatre", "stem", "robotics", "vocational",
]
DISCOVERY_BAD_TERMS = [
    "volunteer opportunity", "volunteer with us", "weekend volunteering", "one day camp", "one-day camp",
    "awareness campaign", "scholarship application", "competition", "prize distribution", "donation drive",
    "clothes distribution", "blood donation", "adult skilling", "women shg", "farmer training", "elderly care",
    "environment campaign", "internship", "fellowship", "job vacancy", "recruitment", "admission open",
    "fee structure", "college admission", "nss camp",
]
DISCOVERY_BAD_DOMAINS = [
    "instagram.com", "facebook.com", "x.com", "twitter.com", "linkedin.com", "youtube.com", "wikipedia.org",
    "researchgate.net", "academia.edu", "scribd.com", "slideshare.net", "careers", "naukri.com",
]

def _score_discovery_candidate(item: dict) -> int:
    text = _norm_text(" ".join([item.get("title", ""), item.get("snippet", ""), item.get("category", ""), item.get("query", "")]))
    url = (item.get("url") or "").lower()
    score = 0
    if any(w in text for w in ["whole child", "k-12", "career", "college", "job", "alumni", "mainstream", "school dropouts", "out of school", "first generation"]):
        score += 18
    if any(w in text for w in ["sports for development", "rural athletes", "music school", "theatre", "drama", "stem", "robotics", "maker lab", "mobile science lab"]):
        score += 18
    if any(w in text for w in ["tribal", "hiv affected", "medically vulnerable", "rescued girls", "child labour", "abandoned children"]):
        score += 14
    if any(w in text for w in ["ngo", "foundation", "trust", "society", "samsthe", "seva", "mission", "non-profit", "nonprofit"]):
        score += 8
    if any(w in text for w in ["children", "child", "student", "students", "girls", "school", "youth", "adolescent"]):
        score += 6
    if any(w in text for w in DISCOVERY_STRONG_SIGNALS):
        score += 8
    if any(w in text for w in DISTINCTIVENESS_TERMS):
        score += 8
    # Generic special-school/CCI/orphanage language is not enough by itself.
    if any(w in text for w in ["special school", "orphanage", "children's home", "child care institution", "cci"]):
        score -= 3
    if any(w in text for w in DISCOVERY_BAD_TERMS):
        score -= 18
    sq = _source_quality(url)
    if sq == "weak_directory_or_donation":
        score -= 25
    if sq in {"credible_news", "official_or_primary_candidate"}:
        score += 6
    if any(d in url for d in DISCOVERY_BAD_DOMAINS):
        score -= 12
    if _is_pdf_url(url):
        if _is_useful_discovery_pdf(url, text):
            score += 8
        else:
            score -= 16
    return score

def _discovery_prefilter_candidate(item: dict) -> tuple[bool, str]:
    text = _norm_text(" ".join([item.get("title", ""), item.get("snippet", ""), item.get("query", "")]))
    url = (item.get("url") or "").lower()
    if not url.startswith(("http://", "https://")):
        return False, "invalid_url"
    if any(term in text for term in DISCOVERY_BAD_TERMS):
        return False, "obvious_non_pathway_result"
    sq = _source_quality(url)
    if sq == "weak_directory_or_donation":
        # These were a major source of bad main-output rows. Keep them in raw audit,
        # but do not send to Claude as primary evidence.
        return False, "weak_directory_or_donation_source_skipped"
    if sq == "social_only":
        return False, "social_result_skipped"
    if any(d in url for d in ["researchgate.net", "academia.edu", "wikipedia.org", "scribd.com", "slideshare.net"]):
        return False, "research_or_reference_result"
    if _is_pdf_url(url):
        if _is_useful_discovery_pdf(url, text):
            return True, "kept_official_report_pdf"
        return False, "non_useful_pdf_skipped"
    return True, "kept"

def _fallback_state_story_row(state: str, category: str, query: str, title: str, snippet: str, url: str, source: str) -> dict | None:
    text = f"{title} {snippet}"
    m = re.search(r"([A-Z][A-Za-z&.' -]{2,90}\s(?:Foundation|Trust|Society|Sanstha|Samsthe|Samiti|Mission|NGO|Organisation|Organization|School|Academy|Home|Hostel))", text)
    if not m:
        return None
    ngo = re.sub(r"\s+", " ", m.group(1)).strip(" -,.:|/")
    if len(ngo) < 5:
        return None
    label = DISCOVERY_PATHWAY_LABELS.get(category, category)
    return {
        "Organisation": ngo,
        "NGO Name": ngo,
        "Website / Source": url,
        "Source URL": url,
        "Article URL": url,
        "Location": state,
        "State": state,
        "District": "Statewide",
        "Pathway": label,
        "Story Category": label,
        "Story Type": label,
        "Why It Belongs": "Potential child pathway institution found from public result text. Needs manual verification.",
        "Status": _discovery_status_for_org(ngo, manual=True),
        "Output Tier": _discovery_status_for_org(ngo, manual=True),
        "Repository Status": _discovery_status_for_org(ngo, manual=True),
        "Source Quality": _source_quality(url),
        "Transformation / Distinctiveness Signal": "Fallback extraction; requires manual review against pathway-pattern standard.",
        "Story Title": title[:220] or "Potential child pathway institution",
        "Story Summary": snippet[:900] or "Potential child pathway institution found. Needs manual verification.",
        "Why NGO Is Interesting": "Potential child pathway institution found; verify source before outreach.",
        "Source": source or urlparse(url).netloc,
        "Confidence": "low",
        "Discovery Query": query,
        "Notes": "Fallback extraction; verify manually.",
    }

def _extract_state_story_with_claude(state: str, category: str, query: str, url: str, title: str, snippet: str, source: str, article_text: str) -> dict | None:
    pathway_label = DISCOVERY_PATHWAY_LABELS.get(category, category)
    source_quality = _source_quality(url)

    # Hard geography/source sanity before paying Claude attention.
    geo_ok, geo_note = _state_gate_for_discovery(state, "", title, snippet, article_text)
    if not geo_ok and state.strip().lower() == "karnataka":
        return None

    if _get_anthropic() is None or not os.environ.get("ANTHROPIC_API_KEY"):
        row = _fallback_state_story_row(state, category, query, title, snippet, url, source)
        if not row:
            return None
        row["Source Quality"] = source_quality
        row["State Gate"] = geo_note
        return row

    client = _get_anthropic().Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""
You are building Feeding India's Karnataka NGO Discovery map.
Use ONLY the public text below.

The goal is NOT to find generic child NGOs, and it is NOT to force known benchmark names into the output. The goal is to find Karnataka organisations with the same underlying pathway grammar as the best child-trajectory institutions: long-horizon schools, dropout-to-mainstream pathways, tribal/rural residential education, specific vulnerable-cohort life systems, and serious sports/arts/STEM pathways.

Core standard:
Include only organisations that directly run or deeply hold a vulnerable child's pathway long enough to materially move their life trajectory.

Good signals:
- whole-child / long-horizon school, K-12, college/career/job transition, alumni outcomes
- alternative education for dropouts/out-of-school children, NIOS/open-school/mainstreaming
- tribal/rural residential education, first-generation learners, rural girls hostels
- specific vulnerable-cohort life systems: HIV-affected children, medically vulnerable children, rescued girls, child labour survivors, abandoned children
- serious sports/arts/music/theatre/drama/STEM/science/robotics pathway with repeated attendance and progression
- residential/full-day/long-term cohort model with a distinctive layer: outcomes, media coverage, therapy, life-skills, yoga/dance/arts/sports/STEM, vocational transition, counselling, alumni, SSLC/PUC/college

Lower priority / reject unless distinctive:
- generic special school with no distinctiveness beyond being a special school
- generic CCI/orphanage/children's home with no education/outcome/transformation signal
- government school or government-esque school-support organisation that does not directly hold the child pathway
- generic food support claim where the only logic is "food could help"

Hard rejects:
- wrong state or no Karnataka centre/campus/program proof for a Karnataka run
- volunteer-only/weekend teaching, camps, workshops, scholarship-only, awareness-only, adult livelihood, generic tuition
- donation-page-only or directory-only evidence as primary proof

Known/current Feeding India partners are allowed to remain visible. Do not reject something just because it is already known.

Important output discipline:
- If it clearly passes the standard, include it.
- If it has Karnataka proof and a plausible child pathway but evidence is incomplete, set include=true and manual_check=true. Do not make the sheet empty just because the case needs verification.
- Benchmark/reference organisations are calibration anchors, not the discovery target. If they naturally appear, they may remain visible only as reference/benchmark, not as fresh discovery.

Return JSON only:
{{
  "include": true/false,
  "manual_check": true/false,
  "organisation": "specific organisation name",
  "location": "city/district/state if clear",
  "pathway": "{pathway_label}",
  "why_it_belongs": "one simple line about the transformation/pathway signal; do NOT just say food could help",
  "confidence": "high|medium|low",
  "distinctiveness_signal": "what makes it more than generic child care/special school/tuition",
  "notes": "short caveat or reject reason"
}}

State: {state}
Pathway attempted: {pathway_label}
Discovery query: {query}
URL: {url}
Source quality: {source_quality}
Search title: {title}
Search snippet: {snippet}

Public text:
{article_text[:11000]}
"""
    msg = client.messages.create(
        model=STORY_MODEL,
        max_tokens=900,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    content = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    data = _clean_json_from_text(content)
    if not data.get("include"):
        return _manual_traceable_discovery_row(state, category, query, url, title, snippet, source, article_text)

    ngo = str(data.get("organisation") or "").strip()
    if not ngo or ngo.lower() in {"needs_check", "unknown", "not clear", "n/a", "none"} or len(ngo) < 4:
        return _manual_traceable_discovery_row(state, category, query, url, title, snippet, source, article_text)

    conf = str(data.get("confidence") or "low").lower()
    location = str(data.get("location") or state).strip()
    why = str(data.get("why_it_belongs") or data.get("distinctiveness_signal") or data.get("notes") or "Child pathway institution found for manual review.").strip()
    distinctiveness = str(data.get("distinctiveness_signal") or "").strip()

    # Hard post-Claude gates.
    geo_ok, geo_note = _state_gate_for_discovery(state, location, title, snippet, article_text)
    if not geo_ok:
        return None

    if source_quality == "weak_directory_or_donation":
        return None

    if _looks_generic_low_value(category, ngo, " ".join([why, distinctiveness]), article_text):
        return None

    # If the text has Karnataka proof but also strong wrong-state signal, keep only as manual check.
    manual = bool(data.get("manual_check")) or conf == "low" or "manual_check" in geo_note
    if source_quality in {"social_only", "unknown"}:
        manual = True

    status = _discovery_status_for_org(ngo, manual=manual)

    if conf not in {"high", "medium", "low"}:
        conf = "low"
    if source_quality != "official_or_primary_candidate" and conf == "high":
        conf = "medium"

    return {
        "Organisation": ngo,
        "NGO Name": ngo,
        "Website / Source": url,
        "Source URL": url,
        "Article URL": url,
        "Location": location,
        "State": state,
        "District": location if location and location != state else "Statewide",
        "Pathway": pathway_label,
        "Story Category": pathway_label,
        "Story Type": pathway_label,
        "Why It Belongs": why[:900],
        "Transformation / Distinctiveness Signal": distinctiveness[:700],
        "Story Title": str(data.get("story_title") or title or ngo)[:220],
        "Story Summary": why[:900],
        "Why NGO Is Interesting": why[:900],
        "Repository Status": status,
        "Status": status,
        "Output Tier": status,
        "Traced Place": location,
        "Source": source or urlparse(url).netloc,
        "Source Quality": source_quality,
        "State Gate": geo_note,
        "Confidence": conf,
        "Discovery Query": query,
        "Notes": str(data.get("notes") or "")[:500],
    }


def _candidate_is_reasonable_manual_check(state: str, item: dict) -> tuple[bool, str]:
    """Looser safety-net gate used only after strict article review.

    This does not accept weak/donation/social junk. It only prevents a 1,500-query
    run from producing a blank sheet when there are plausible Karnataka child-
    pathway candidates that need manual review.
    """
    url = item.get("url") or ""
    if not url.startswith(("http://", "https://")):
        return False, "invalid_url"
    sq = _source_quality(url)
    if sq in {"weak_directory_or_donation", "social_only"}:
        return False, sq
    if any(d in url.lower() for d in ["researchgate.net", "academia.edu", "wikipedia.org", "scribd.com", "slideshare.net"]):
        return False, "research_or_reference_result"
    text = " ".join([item.get("title", ""), item.get("snippet", ""), item.get("query", ""), url])
    # For the safety net, the query itself is allowed as weak geography context,
    # but explicit wrong-state signals still block the row unless Karnataka is
    # also visible in the result text/url.
    result_text = " ".join([item.get("title", ""), item.get("snippet", ""), url])
    if _norm_text(state) == "karnataka":
        if _has_wrong_state_signal(result_text) and not _has_karnataka_proof(result_text):
            return False, "wrong_state_signal_in_result"
        if not (_has_karnataka_proof(result_text) or _has_karnataka_proof(item.get("query", ""))):
            return False, "weak_karnataka_context"
    if not _has_child_pathway_signal(text) and not _distinctiveness_found(text):
        return False, "weak_child_pathway_signal"
    if int(item.get("score") or 0) < int(os.environ.get("DISCOVERY_MANUAL_FALLBACK_MIN_SCORE", "8")):
        return False, "low_candidate_score"
    return True, "reasonable_manual_check_candidate"


def _manual_row_from_candidate(state: str, item: dict) -> dict | None:
    ok, reason = _candidate_is_reasonable_manual_check(state, item)
    if not ok:
        return None
    url = item.get("url") or ""
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    category = item.get("category") or "other"
    query = item.get("query") or ""
    source = item.get("source") or _domain_of(url)
    org = _extract_traceable_org_name(title, snippet, url, "")
    if not org:
        # Conservative fallback: use a cleaned title only if it is not just a generic listicle.
        cleaned = re.sub(r"\s+[-|:].*$", "", title).strip()
        cleaned = re.sub(r"\b(best|top|list of|ngos? in|schools? in)\b", "", cleaned, flags=re.I).strip(" -:|")
        if len(cleaned) < 5 or len(cleaned) > 90:
            return None
        org = cleaned
    label = DISCOVERY_PATHWAY_LABELS.get(category, category)
    status = _discovery_status_for_org(org, manual=True)
    return {
        "Organisation": org,
        "NGO Name": org,
        "Website / Source": url,
        "Source URL": url,
        "Article URL": url,
        "Location": state,
        "State": state,
        "District": "Statewide",
        "Pathway": label,
        "Story Category": label,
        "Story Type": label,
        "Why It Belongs": "Promising Karnataka child-pathway candidate surfaced by search; not enough evidence for a strong lead yet, so keep for human review instead of rejecting.",
        "Transformation / Distinctiveness Signal": "Manual-check safety net: query/result matched child-trajectory pathway grammar. Verify direct pathway ownership, distinctiveness, and source quality before outreach.",
        "Story Title": title[:220] or org,
        "Story Summary": snippet[:900] or "Promising candidate from search result; needs manual verification.",
        "Why NGO Is Interesting": "Potentially relevant child-pathway organisation; included to avoid a blank discovery sheet when strict gates are uncertain.",
        "Repository Status": status,
        "Status": status,
        "Output Tier": status,
        "Traced Place": state,
        "Source": source,
        "Source Quality": _source_quality(url),
        "State Gate": "manual_check_query_or_result_karnataka_context",
        "Confidence": "low",
        "Discovery Query": query,
        "Notes": f"Safety-net manual-check row. Candidate score={item.get('score', '')}; reason={reason}. Not a final recommendation.",
    }


def _top_up_manual_check_outputs(rd: Path, state: str, categories: list[str], budget: int, stories: list[dict], seen_ngo_cat: set[str]) -> int:
    if not str(rd.name).startswith("discovery"):
        return 0
    min_rows = _discovery_min_output_rows(budget)
    actionable = sum(1 for r in stories if _is_actionable_discovery_row(r))
    if actionable >= min_rows:
        return 0
    needed = min_rows - actionable
    added = 0
    candidates = sorted(_story_load_candidates(rd), key=lambda x: x.get("score", 0), reverse=True)
    seen_urls = set(r.get("Source URL") or r.get("Article URL") or r.get("Website / Source") or "" for r in stories)
    for item in candidates:
        if added >= needed:
            break
        url = item.get("url") or ""
        if url in seen_urls:
            continue
        row = _manual_row_from_candidate(state, item)
        if not row:
            continue
        # Safety-net rows are meant to protect against blank output. Do not use
        # benchmark/reference organisations to satisfy the minimum actionable count.
        if "benchmark" in _norm_text(row.get("Status") or ""):
            continue
        key = _normalise_org_key(row.get("NGO Name", "") or row.get("Organisation", ""))
        if not key or key in seen_ngo_cat:
            continue
        seen_ngo_cat.add(key)
        seen_urls.add(url)
        stories.append(row)
        added += 1
        _append_story_audit(rd, {
            "Query": item.get("query", ""),
            "Category": item.get("category", ""),
            "Status": "manual_check_safety_net_added",
            "URL": url,
            "Title": item.get("title", ""),
            "Note": row.get("NGO Name", ""),
            "Query Family": item.get("query_family", ""),
            "Score": item.get("score", ""),
            "Source": item.get("source", ""),
            "Snippet": item.get("snippet", "")[:500],
        })
    if added:
        _write_story_rows(rd, stories)
        _append_story_audit(rd, {
            "Query": "",
            "Category": "discovery_safety",
            "Status": "manual_check_safety_net_summary",
            "URL": "",
            "Title": "Manual-check top-up applied",
            "Note": f"Added {added} manual-check promising rows because actionable output was below {min_rows}. Benchmarks are not counted as fresh/actionable discovery.",
            "Query Family": "safety_net",
            "Score": "",
            "Source": "",
            "Snippet": "",
        })
    return added

def _discovery_reject_reason_after_article(state: str, category: str, url: str, title: str, snippet: str, article_text: str) -> str:
    sq = _source_quality(url)
    if sq == "weak_directory_or_donation":
        return "weak_source_only"
    if sq == "social_only":
        return "social_result_skipped"
    if _is_pdf_url(url) and not _is_useful_discovery_pdf(url, " ".join([title, snippet, article_text[:2000]])):
        return "non_useful_pdf_skipped"
    geo_ok, geo_note = _state_gate_for_discovery(state, "", title, snippet, article_text)
    if not geo_ok:
        return geo_note
    combined = " ".join([title or "", snippet or "", (article_text or "")[:5000]])
    org = _extract_traceable_org_name(title, snippet, url, article_text)
    if not org:
        return "not_ngo_traceable"
    if not _has_child_pathway_signal(combined):
        return "not_direct_child_pathway"
    if _looks_generic_low_value(category, org, combined, article_text):
        return "generic_special_school_or_cci_no_distinctiveness"
    return "does_not_pass_trajectory_pathway_gate"

def _story_pause_path(rd: Path) -> Path:
    return rd / ".story_pause_requested"


def _story_queries_path(rd: Path) -> Path:
    return rd / "story_state_queries.json"


def _story_candidates_path(rd: Path) -> Path:
    return rd / "story_state_candidates.jsonl"


def _story_raw_candidates_path(rd: Path) -> Path:
    return rd / "story_state_raw_candidates.jsonl"


def _story_progress_path(rd: Path) -> Path:
    return rd / "story_state_progress.json"


def _story_read_progress(rd: Path) -> dict:
    path = _story_progress_path(rd)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _story_write_progress(rd: Path, **payload):
    current = _story_read_progress(rd)
    current.update(payload)
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_text(_story_progress_path(rd), json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def _story_load_existing_rows(rd: Path) -> list[dict]:
    path = rd / STORY_OUTPUTS["stories"]
    if not path.exists():
        return []
    return _read_csv_rows(path, limit=100000)


def _story_load_candidates(rd: Path) -> list[dict]:
    path = _story_candidates_path(rd)
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _story_append_candidate(rd: Path, item: dict):
    with _story_candidates_path(rd).open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _story_append_raw_candidate(rd: Path, item: dict):
    with _story_raw_candidates_path(rd).open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _story_should_pause(rd: Path, cancel_event: threading.Event) -> bool:
    return _should_cancel(rd.name, cancel_event) or _story_pause_path(rd).exists()


def _story_mark_paused(rd: Path, **extra):
    _write_story_status(rd, ok=True, run_status="paused", stage="paused", current_item="Paused safely. Resume later to continue from checkpoint.", **extra)

def _run_story_state_job(run_id: str, state: str, categories: list[str], budget: int, cancel_event: threading.Event):
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    _story_pause_path(rd).unlink(missing_ok=True)
    stories: list[dict] = _story_load_existing_rows(rd)
    seen_ngo_cat: set[str] = set(_normalise_org_key(r.get("NGO Name", "") or r.get("Organisation", "")) for r in stories if (r.get("NGO Name") or r.get("Organisation")))

    qpath = _story_queries_path(rd)
    if qpath.exists():
        try:
            queries = json.loads(qpath.read_text(encoding="utf-8"))
        except Exception:
            queries = _story_state_queries(state, categories, budget)
            _atomic_write_text(qpath, json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        queries = _story_state_queries(state, categories, budget)
        _atomic_write_text(qpath, json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")

    # Discovery safety repair: older deployments/runs may have cached a query plan
    # that literally searched benchmark organisation names. That was the wrong
    # interpretation of seed-pattern discovery. For any discovery run, sanitize
    # the cached query plan before searching, and top it back up with fresh
    # pathway-pattern queries so the requested budget is preserved.
    if run_id.startswith("discovery"):
        original_count = len(queries) if isinstance(queries, list) else 0
        cleaned_queries = []
        seen_clean_q = set()
        dropped_literal = 0
        for q in queries if isinstance(queries, list) else []:
            qtext = str((q or {}).get("query") or "")
            base_qtext = _strip_discovery_query_filters(qtext)
            if _contains_benchmark_name(base_qtext):
                dropped_literal += 1
                continue
            q = dict(q or {})
            q["query"] = _apply_discovery_query_filters(base_qtext)
            key = q["query"].lower().strip()
            if key and key not in seen_clean_q:
                seen_clean_q.add(key)
                cleaned_queries.append(q)
        if dropped_literal or len(cleaned_queries) < min(budget, DISCOVERY_MAX_BUDGET):
            fresh = _discovery_state_queries(state, categories, budget)
            for q in fresh:
                qtext = str((q or {}).get("query") or "")
                base_qtext = _strip_discovery_query_filters(qtext)
                q = dict(q or {})
                q["query"] = _apply_discovery_query_filters(base_qtext)
                key = q["query"].lower().strip()
                if not key or key in seen_clean_q or _contains_benchmark_name(base_qtext):
                    continue
                seen_clean_q.add(key)
                cleaned_queries.append(q)
                if len(cleaned_queries) >= min(budget, DISCOVERY_MAX_BUDGET):
                    break
            queries = cleaned_queries[:min(budget, DISCOVERY_MAX_BUDGET)]
            _atomic_write_text(qpath, json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
            if dropped_literal:
                _append_story_audit(rd, {
                    "Query": "",
                    "Category": "discovery_safety",
                    "Status": "literal_seed_queries_removed",
                    "URL": "",
                    "Title": "Cached query plan sanitized",
                    "Note": f"Removed {dropped_literal} literal benchmark-name queries from cached plan; rebuilt with pathway-pattern queries. Original={original_count}, final={len(queries)}",
                    "Query Family": "safety_guard",
                    "Score": "",
                    "Source": "",
                    "Snippet": "",
                })

    progress = _story_read_progress(rd)
    search_done = int(progress.get("search_done", 0) or 0)
    article_done = int(progress.get("article_done", 0) or 0)

    try:
        _write_story_status(
            rd, ok=True, run_id=run_id, module=("discovery" if run_id.startswith("discovery") else "story"), story_mode="statewide", run_status="running",
            stage="searching" if search_done < len(queries) else "reading_articles",
            current_item="Continuing statewide story discovery from checkpoint", current_search="", current_url="",
            state=state, district="Statewide", categories=categories, query_budget=budget,
            processed=search_done, total=len(queries), links_found=len(_story_load_candidates(rd)),
            articles_read=article_done, stories_found=len(stories), downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()},
        )

        seen_urls = set((item.get("url") or "") for item in _story_load_candidates(rd))
        search_processed = search_done
        window_start_idx = search_done
        window_start_urls = len(seen_urls)
        smart_stop_min_new = int(os.environ.get("DISCOVERY_SMART_STOP_MIN_NEW_URLS", "25"))
        for idx, q in enumerate(queries, start=1):
            if idx <= search_done:
                continue
            if _story_should_pause(rd, cancel_event):
                _story_mark_paused(rd, processed=idx-1, total=len(queries), links_found=len(seen_urls), articles_read=article_done, stories_found=len(stories))
                return
            query = q["query"]
            category = q["category"]
            if run_id.startswith("discovery"):
                query = _apply_discovery_query_filters(_strip_discovery_query_filters(query))
            if run_id.startswith("discovery") and _contains_benchmark_name(_strip_discovery_query_filters(query)):
                audit_row = {"Query": query, "Category": category, "Status": "literal_seed_query_skipped", "URL": "", "Title": "", "Note": "Runtime guard skipped benchmark-name query; discovery must search pathways, not known names.", "Query Family": q.get("query_family", ""), "Score": "", "Source": "", "Snippet": ""}
                _append_story_audit(rd, audit_row)
                _append_story_rejected(rd, {**audit_row, "Reject Reason": "literal_seed_query_skipped"})
                _story_write_progress(rd, search_done=idx)
                continue
            _write_story_status(rd, run_status="running", stage="searching", current_search=query, processed=idx-1, total=len(queries), category=category, stories_found=len(stories))
            try:
                results = _serper_story_state_search(query, DISCOVERY_RESULTS_PER_QUERY if run_id.startswith("discovery") else STORY_STATE_RESULTS_PER_QUERY)
            except Exception as e:
                _append_story_error(rd, f"search failed query={query!r}: {e}")
                row = {"Query": query, "Category": category, "Status": "search_failed", "URL": "", "Title": "", "Note": str(e)[:250]}
                _append_story_audit(rd, row)
                _append_story_rejected(rd, {**row, "Reject Reason": "search_failed"})
                _story_write_progress(rd, search_done=idx)
                continue
            for item in results:
                url = item.get("url") or ""
                item["query"] = query
                item["category"] = category
                item["query_family"] = q.get("query_family", "")
                item["pathway_label"] = q.get("pathway_label", DISCOVERY_PATHWAY_LABELS.get(category, category))
                _story_append_raw_candidate(rd, item)
                if not url or url in seen_urls:
                    continue
                if run_id.startswith("discovery"):
                    keep, reason = _discovery_prefilter_candidate(item)
                    item["prefilter_reason"] = reason
                    item["score"] = _score_discovery_candidate(item)
                    if not keep:
                        audit_row = {"Query": query, "Category": category, "Status": "prefilter_rejected", "URL": url, "Title": item.get("title", ""), "Note": reason, "Query Family": item.get("query_family", ""), "Score": item.get("score", 0), "Source": item.get("source", ""), "Snippet": item.get("snippet", "")[:500]}
                        _append_story_audit(rd, audit_row)
                        _append_story_rejected(rd, {**audit_row, "Reject Reason": reason})
                        continue
                else:
                    if not url.startswith(("http://", "https://")):
                        continue
                    item["score"] = _score_story_candidate(item)
                seen_urls.add(url)
                _story_append_candidate(rd, item)
            _story_write_progress(rd, search_done=idx)
            search_processed = idx
            if run_id.startswith("discovery") and idx >= 1000 and (idx - window_start_idx) >= 500:
                new_urls_in_window = len(seen_urls) - window_start_urls
                if new_urls_in_window < smart_stop_min_new:
                    _append_story_audit(rd, {"Query": query, "Category": category, "Status": "smart_stop", "URL": "", "Title": "", "Note": f"Stopped early after {idx} searches because the last 500 queries produced only {new_urls_in_window} new sources."})
                    _write_story_status(rd, stage="search_smart_stopped", current_item=f"Stopped search early after {idx} queries due to low new-source yield", processed=idx, total=len(queries), links_found=len(seen_urls), stories_found=len(stories))
                    break
                window_start_idx = idx
                window_start_urls = len(seen_urls)
            if STORY_STATE_PACE_SEC:
                time.sleep(STORY_STATE_PACE_SEC)
            if idx % 20 == 0:
                _write_story_status(rd, processed=idx, links_found=len(seen_urls), stories_found=len(stories), downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()})

        max_articles = DISCOVERY_MAX_ARTICLES if run_id.startswith("discovery") else STORY_STATE_MAX_ARTICLES
        candidates = sorted(_story_load_candidates(rd), key=lambda x: x.get("score", 0), reverse=True)[:max_articles]
        for i, item in enumerate(candidates, start=1):
            if i <= article_done:
                continue
            if _story_should_pause(rd, cancel_event):
                _story_mark_paused(rd, processed=search_processed, total=len(queries), links_found=len(candidates), articles_read=i-1, stories_found=len(stories), downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()})
                return
            url = item["url"]
            _write_story_status(rd, run_status="running", stage="reading_articles", current_url=url, current_item=item.get("title", ""), processed=search_processed, total=len(queries), articles_read=i, links_found=len(candidates), stories_found=len(stories), category=item.get("category", ""), downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()})
            try:
                page_title, article_text = _fetch_article_text(url)
                title = page_title or item.get("title", "")
                row = _extract_state_story_with_claude(state, item.get("category", "other"), item.get("query", ""), url, title, item.get("snippet", ""), item.get("source", ""), article_text)
                if row:
                    key = _normalise_org_key(row.get("NGO Name", "") or row.get("Organisation", ""))
                    if key and key not in seen_ngo_cat:
                        seen_ngo_cat.add(key)
                        stories.append(row)
                        _write_story_rows(rd, stories)
                        _append_story_audit(rd, {"Query": item.get("query", ""), "Category": item.get("category", ""), "Status": "story_found", "URL": url, "Title": title, "Note": row.get("NGO Name", ""), "Query Family": item.get("query_family", ""), "Score": item.get("score", ""), "Source": item.get("source", ""), "Snippet": item.get("snippet", "")[:500]})
                else:
                    reason = _discovery_reject_reason_after_article(state, item.get("category", ""), url, title, item.get("snippet", ""), article_text) if run_id.startswith("discovery") else "not_ngo_traceable"
                    reject = {"Query": item.get("query", ""), "Category": item.get("category", ""), "Status": reason, "URL": url, "Title": title, "Note": f"Dropped after article read: {reason}", "Query Family": item.get("query_family", ""), "Score": item.get("score", ""), "Source": item.get("source", ""), "Snippet": item.get("snippet", "")[:500]}
                    _append_story_audit(rd, reject)
                    _append_story_rejected(rd, {**reject, "Reject Reason": reason})
            except Exception as e:
                row = {"Query": item.get("query", ""), "Category": item.get("category", ""), "Status": "article_failed", "URL": url, "Title": item.get("title", ""), "Note": str(e)[:250], "Query Family": item.get("query_family", ""), "Score": item.get("score", ""), "Source": item.get("source", ""), "Snippet": item.get("snippet", "")[:500]}
                _append_story_error(rd, f"article failed url={url!r}: {e}")
                _append_story_audit(rd, row)
                _append_story_rejected(rd, {**row, "Reject Reason": "article_failed"})
            _story_write_progress(rd, article_done=i)
            if i % 10 == 0:
                _write_story_status(rd, stories_found=len(stories), articles_read=i, downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()})

        safety_added = _top_up_manual_check_outputs(rd, state, categories, budget, stories, seen_ngo_cat) if run_id.startswith("discovery") else 0
        _write_story_rows(rd, stories)
        actionable_stories = sum(1 for r in stories if _is_actionable_discovery_row(r)) if run_id.startswith("discovery") else len(stories)
        benchmark_rows = sum(1 for r in stories if "benchmark" in _norm_text(r.get("Status") or "")) if run_id.startswith("discovery") else 0
        _write_story_status(
            rd, ok=True, run_status="complete", stage="results_ready", current_item=("General Discovery complete" if run_id.startswith("discovery") else "Statewide Story Discovery complete"),
            current_search="", current_url="", processed=search_processed, total=len(queries), links_found=len(candidates),
            articles_read=len(candidates), stories_found=len(stories), actionable_stories=actionable_stories, benchmark_rows=benchmark_rows, safety_net_added=safety_added, story_mode="statewide", categories=categories,
            downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()},
        )
    except Exception as e:
        _append_story_error(rd, f"fatal statewide story job error: {e}")
        _write_story_status(rd, ok=False, run_status="error", stage="error", error=str(e)[:500], downloads={kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()})



@app.get("/discovery/preview-queries")
def discovery_preview_queries(state: str = "Karnataka", pathways: str = "", budget: int = DISCOVERY_DEFAULT_BUDGET, limit: int = 80):
    """Preview General Discovery query plan without spending Serper credits."""
    picked = _normalise_discovery_pathways(pathways)
    safe_budget = max(1, min(int(budget or DISCOVERY_DEFAULT_BUDGET), DISCOVERY_MAX_BUDGET))
    queries = _discovery_state_queries(state.strip() or "Karnataka", picked, safe_budget)
    literal = [q for q in queries if _contains_benchmark_name(_strip_discovery_query_filters(str(q.get("query") or "")))]
    category_counts = {}
    family_counts = {}
    for q in queries:
        category_counts[q.get("category", "")] = category_counts.get(q.get("category", ""), 0) + 1
        family_counts[q.get("query_family", "")] = family_counts.get(q.get("query_family", ""), 0) + 1
    return _json(
        True,
        state=state.strip() or "Karnataka",
        pathways=picked,
        requested_budget=safe_budget,
        total=len(queries),
        unique=len({str(q.get("query") or "").lower() for q in queries}),
        literal_benchmark_queries=len(literal),
        category_counts=category_counts,
        family_counts=family_counts,
        sample=queries[:max(1, min(int(limit or 80), 300))],
    )

@app.post("/discovery/start")
def discovery_start(state: str, pathways: str = "", budget: int = DISCOVERY_DEFAULT_BUDGET, run_mode: str = "test"):
    if not state.strip():
        return _json(False, status_code=400, stage="missing_state", error="State is required")
    if not _has_serper_keys():
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS must be set on Render")
    active_story = [rid for rid, th in list(story_threads.items()) if th.is_alive()]
    if active_story:
        return _json(False, status_code=409, stage="another_discovery_run_active", error="Another discovery run is already active", active_runs=active_story)
    picked = _normalise_discovery_pathways(pathways)
    safe_budget = max(1, min(int(budget or DISCOVERY_DEFAULT_BUDGET), DISCOVERY_MAX_BUDGET))
    run_id = f"discovery_state_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    queries = _discovery_state_queries(state.strip(), picked, safe_budget)
    _atomic_write_text(_story_queries_path(rd), json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    cancel_event = threading.Event()
    story_cancel_flags[run_id] = cancel_event
    _write_story_status(
        rd, ok=True, run_id=run_id, module="discovery", story_mode="statewide", run_status="starting", stage="queued",
        state=state.strip(), district="Statewide", categories=picked, pathways=picked, pathway_labels=[DISCOVERY_PATHWAY_LABELS.get(x, x) for x in picked],
        query_budget=safe_budget, run_mode=run_mode, current_item="Queued", processed=0, total=len(queries), links_found=0, articles_read=0, stories_found=0,
    )
    th = threading.Thread(target=_run_story_state_job, args=(run_id, state.strip(), picked, safe_budget, cancel_event), daemon=True)
    story_threads[run_id] = th
    th.start()
    _job_update(run_id, status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="started", module="discovery", state=state.strip(), pathways=picked, total=len(queries), query_budget=safe_budget, run_mode=run_mode)

@app.get("/discovery/status/{run_id}")
def discovery_status(run_id: str):
    return story_status(run_id)

@app.get("/discovery/results/{run_id}")
def discovery_results(run_id: str, limit: int = 100):
    return story_results(run_id, limit=limit)

@app.get("/discovery/export/{run_id}/{kind}")
def discovery_export(run_id: str, kind: str):
    aliases = {"leads": "stories", "accepted": "stories", "manual": "stories", "audit": "audit", "rejected": "rejected", "errors": "errors", "status": "status", "candidates": "candidates", "raw_candidates": "raw_candidates", "raw": "raw_candidates", "queries": "queries"}
    return story_export(run_id, aliases.get(kind, kind))

@app.post("/discovery/pause/{run_id}")
def discovery_pause(run_id: str):
    return story_pause(run_id)

@app.post("/discovery/resume/{run_id}")
def discovery_resume(run_id: str):
    return story_resume(run_id)

@app.post("/discovery/cancel/{run_id}")
def discovery_cancel(run_id: str):
    return story_cancel(run_id)

@app.get("/discovery/archive")
def discovery_archive(limit: int = 100):
    """List General Discovery runs, including older Story Discovery runs.

    Important: older deployments wrote statewide discovery runs with run IDs
    starting with ``story``. The new UI should not hide those historical audits.
    This endpoint therefore includes both ``discovery_*`` and legacy
    ``story*`` run directories.
    """
    items = []
    max_items = max(1, min(limit, 300))
    dirs = [
        p for p in RUNS_DIR.iterdir()
        if p.is_dir() and (p.name.startswith("discovery") or p.name.startswith("story"))
    ]
    dirs = sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)[:max_items]
    for rd in dirs:
        run_id = rd.name
        data = {}
        status_path = _story_status_path(rd)
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        is_legacy = run_id.startswith("story")
        items.append({
            "run_id": run_id,
            "module": "legacy_story" if is_legacy else "discovery",
            "label": "Legacy Story Discovery" if is_legacy else "General Discovery",
            "updated_at": data.get("updated_at", ""),
            "run_status": data.get("run_status", ""),
            "stage": data.get("stage", ""),
            "state": data.get("state", ""),
            "run_mode": data.get("run_mode", data.get("story_mode", "")),
            "total": data.get("total", ""),
            "processed": data.get("processed", ""),
            "links_found": data.get("links_found", ""),
            "articles_read": data.get("articles_read", ""),
            "stories_found": data.get("stories_found", ""),
            "downloads": {kind: (rd / filename).exists() for kind, filename in STORY_OUTPUTS.items()},
        })
    return _json(True, rows=items, count=len(items))

@app.post("/story/state/start")
def story_state_start(state: str, categories: str = "", budget: int = STORY_STATE_QUERY_BUDGET):
    if not state.strip():
        return _json(False, status_code=400, stage="missing_state", error="State is required")
    if not _has_serper_keys():
        return _json(False, status_code=500, stage="missing_env", error="SERPER_API_KEY or SERPER_API_KEYS must be set on Render")
    active_story = [rid for rid, th in list(story_threads.items()) if th.is_alive()]
    if active_story:
        return _json(False, status_code=409, stage="another_story_run_active", error="Another Story Discovery run is already active", active_runs=active_story)
    picked = _normalise_story_categories(categories)
    budget = max(1, min(int(budget or STORY_STATE_QUERY_BUDGET), STORY_STATE_MAX_BUDGET))
    run_id = f"story_state_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    cancel_event = threading.Event()
    story_cancel_flags[run_id] = cancel_event
    queries = _story_state_queries(state.strip(), picked, budget)
    _write_story_status(
        rd, ok=True, run_id=run_id, module=("discovery" if run_id.startswith("discovery") else "story"), story_mode="statewide", run_status="starting", stage="queued",
        state=state.strip(), district="Statewide", categories=picked, query_budget=budget,
        current_item="Queued", processed=0, total=len(queries), links_found=0, articles_read=0, stories_found=0,
    )
    th = threading.Thread(target=_run_story_state_job, args=(run_id, state.strip(), picked, budget, cancel_event), daemon=True)
    story_threads[run_id] = th
    th.start()
    _job_update(run_id, status="running", stage="thread_started", thread_alive=True)
    return _json(True, run_id=run_id, stage="started", story_mode="statewide", state=state.strip(), categories=picked, total=len(queries), query_budget=budget)



# -----------------------------------------------------------------------------
# Workstream tracker storage + lightweight AI review
# -----------------------------------------------------------------------------
WORKSTREAM_DATA_FILE = RUNS_DIR / "workstream_data.json"
WORKSTREAM_PM_NAMES = ["Milan", "Rachit", "Ipshita", "Avika", "Kamran", "Piyush", "Tanishq"]
DEFAULT_WORKSTREAM_RULES = """Review only expression quality. Do not critique the NGO, the rank, the pathway, the cohort, the source, or whether the PM is right. Do not mention specific NGO facts. Check whether the PM expressed their own judgement clearly enough for consolidation: is there enough length, is the thought process understandable, and does it capture what went through their head? Hinglish, fragments, rough English, spelling mistakes, missing punctuation, and stream-of-consciousness notes are all acceptable. More depth is encouraged. Rank explanations should capture what went through the reviewer’s head, but the review should only ask for more expression, not judge content."""
DEFAULT_WORKSTREAM_TASKS = [
    {"ngo_name": "Aina Trust", "website": "www.ainatrust.in/about-aina.html", "background": "Early childhood care centres, anganwadi strengthening, nutrition support and education programs for vulnerable young children."},
    {"ngo_name": "Cerebloom Academy", "website": "https://cerebloom.org/", "background": "Rural science education and mentorship program for underserved students. Review regularity and depth."},
    {"ngo_name": "Don Bosco Child Labour Mission", "website": "dbclm.org", "background": "Child labour rehabilitation, bridge schooling, open shelter and prevention work. Verify current scale and pathway depth."},
]
DEFAULT_TANISHQ_TASKS = [
    {"ngo_name": "Referral NGO 1", "website": "", "background": "Capture NGO details, POC contact number, and referral source."},
    {"ngo_name": "Referral NGO 2", "website": "", "background": "Capture NGO details, POC contact number, and referral source."},
    {"ngo_name": "Referral NGO 3", "website": "", "background": "Capture NGO details, POC contact number, and referral source."},
]


def _default_workstream_payload():
    now = int(time.time())
    pms = {}
    for i, name in enumerate(WORKSTREAM_PM_NAMES):
        is_details = name == "Tanishq"
        pms[name] = {
            "name": name,
            "deadline": time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + (18 + i) * 3600)),
            "responsibility": ("Complete NGO descriptions and referral / POC details cleanly." if is_details else "Review assigned NGOs and capture your judgement clearly. Stream of consciousness is fine; useful reasoning matters more than polished sentences."),
            "task_type": "ngo_details" if is_details else "shortlisting",
            "tasks": DEFAULT_TANISHQ_TASKS if is_details else DEFAULT_WORKSTREAM_TASKS,
            "responses": {},
            "first_five_reviewed": False,
            "global_saved_at": "",
            "global_saved_count": 0,
            "deadline_note": "Once everyone submits, we compare rankings, identify strong cohorts, resolve overlaps, and move to human lead follow-ups. This needs to close by Wednesday so the lead list can be wrapped by the end of the week.",
        }
    return {
        "review_rules": DEFAULT_WORKSTREAM_RULES,
        "pms": pms,
        "global_log": [],
        "ai_log": [],
        "edit_locks": {"all": False, "pms": {}},
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _read_workstream_payload():
    if not WORKSTREAM_DATA_FILE.exists():
        data = _default_workstream_payload()
        _atomic_write_text(WORKSTREAM_DATA_FILE, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        data = json.loads(WORKSTREAM_DATA_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("workstream data is not an object")
    except Exception:
        data = _default_workstream_payload()
    data.setdefault("review_rules", DEFAULT_WORKSTREAM_RULES)
    data.setdefault("pms", {})
    data.setdefault("global_log", [])
    data.setdefault("ai_log", [])
    locks = data.get("edit_locks")
    if not isinstance(locks, dict):
        locks = {}
        data["edit_locks"] = locks
    locks.setdefault("all", False)
    if not isinstance(locks.get("pms"), dict):
        locks["pms"] = {}
    for name, default_pm in _default_workstream_payload()["pms"].items():
        cur = data["pms"].setdefault(name, {})
        for k, v in default_pm.items():
            cur.setdefault(k, v)
        if not isinstance(cur.get("tasks"), list):
            cur["tasks"] = default_pm["tasks"]
        if not isinstance(cur.get("responses"), dict):
            cur["responses"] = {}
    return data


def _write_workstream_payload(data: dict):
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_text(WORKSTREAM_DATA_FILE, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _workstream_check_admin(payload: dict):
    if not os.environ.get("ADMIN_PASSWORD"):
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD must be set on Render")
    password = str((payload or {}).get("password") or "")
    if password != os.environ.get("ADMIN_PASSWORD"):
        raise HTTPException(status_code=401, detail="Wrong password")


def _workstream_lock_state(data: dict) -> dict:
    locks = data.get("edit_locks")
    if not isinstance(locks, dict):
        locks = {}
        data["edit_locks"] = locks
    locks["all"] = bool(locks.get("all"))
    if not isinstance(locks.get("pms"), dict):
        locks["pms"] = {}
    return locks


def _workstream_pm_locked(data: dict, pm_name: str) -> bool:
    locks = _workstream_lock_state(data)
    pm_locks = locks.get("pms") if isinstance(locks.get("pms"), dict) else {}
    return bool(locks.get("all")) or bool(pm_locks.get(pm_name))


def _workstream_recount_pm(pm: dict) -> None:
    responses = pm.get("responses") if isinstance(pm.get("responses"), dict) else {}
    count = sum(1 for r in responses.values() if isinstance(r, dict) and r.get("submitted"))
    pm["global_saved_count"] = count
    latest_idx = ""
    latest_at = ""
    for k, r in responses.items():
        if not isinstance(r, dict) or not r.get("submitted"):
            continue
        submitted_at = str(r.get("submitted_at") or r.get("global_saved_at") or "")
        if submitted_at >= latest_at:
            latest_at = submitted_at
            latest_idx = str(k)
    if latest_idx != "":
        try:
            pm["last_submitted_task_index"] = int(latest_idx)
        except Exception:
            pm["last_submitted_task_index"] = latest_idx
        pm["last_submitted_at"] = latest_at
        pm["global_saved_at"] = latest_at or pm.get("global_saved_at") or ""
    else:
        pm["last_submitted_task_index"] = ""
        pm["last_submitted_at"] = ""


def _workstream_reindex_responses_after_task_removal(responses: dict, removed_indices: set[int]) -> dict:
    out: dict[str, dict] = {}
    sorted_removed = sorted(removed_indices)
    for key, value in (responses or {}).items():
        try:
            old_idx = int(key)
        except Exception:
            continue
        if old_idx in removed_indices:
            continue
        shift = sum(1 for idx in sorted_removed if idx < old_idx)
        out[str(old_idx - shift)] = value
    return out


# -----------------------------------------------------------------------------
# Admin undo / redo journal
# -----------------------------------------------------------------------------
# Covers the destructive/irreversible-feeling workflow moves in the UI:
#   1) Lead Pool decisions/imports/deletes and send-to-ranking
#   2) PM shortlisting responses/tasks that feed Combined Review
#   3) Final Ranking rows sent into Contact Tracker
# Each journal entry stores the before/after text of only internal workspace files.
# Undo restores the previous file contents; redo restores the after state again.
UNDO_REDO_DIR = RUNS_DIR / "undo_redo"
UNDO_REDO_DIR.mkdir(parents=True, exist_ok=True)
UNDO_STACK_FILE = UNDO_REDO_DIR / "undo_stack.json"
REDO_STACK_FILE = UNDO_REDO_DIR / "redo_stack.json"
UNDO_REDO_LOCK = threading.RLock()
MAX_UNDO_ENTRIES = int(os.environ.get("DFP2_MAX_UNDO_ENTRIES", "15"))


def _undo_read_stack(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _undo_write_stack(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(rows[-MAX_UNDO_ENTRIES:], ensure_ascii=False, indent=2), encoding="utf-8")


def _undo_file_text(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return None


def _undo_snapshot_before(paths: list[Path]) -> dict[str, str | None]:
    return {str(Path(p).resolve()): _undo_file_text(Path(p).resolve()) for p in paths}


def _undo_snapshot_after(action: str, label: str, region: str, paths: list[Path], before: dict[str, str | None]) -> None:
    files = []
    changed = False
    for p in paths:
        rp = Path(p).resolve()
        key = str(rp)
        after = _undo_file_text(rp)
        before_text = before.get(key)
        if before_text != after:
            changed = True
        files.append({"path": key, "before": before_text, "after": after})
    if not changed:
        return
    entry = {
        "id": uuid.uuid4().hex[:12],
        "action": action,
        "label": label or action,
        "region": region or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": files,
    }
    with UNDO_REDO_LOCK:
        undo = _undo_read_stack(UNDO_STACK_FILE)
        undo.append(entry)
        _undo_write_stack(UNDO_STACK_FILE, undo)
        _undo_write_stack(REDO_STACK_FILE, [])


def _restore_undo_entry(entry: dict, state_key: str) -> None:
    base = RUNS_DIR.resolve()
    for f in entry.get("files") or []:
        raw_path = str(f.get("path") or "")
        try:
            path = Path(raw_path).resolve()
        except Exception:
            continue
        # Snapshots are generated internally; this guard prevents path surprises.
        if not str(path).startswith(str(base)):
            continue
        text = f.get(state_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            continue
        _atomic_write_text(path, str(text), encoding="utf-8")


def _undo_latest_for_region(stack: list[dict], region: str = "") -> tuple[int, dict | None]:
    wanted = str(region or "").strip().lower()
    for i in range(len(stack) - 1, -1, -1):
        entry_region = str(stack[i].get("region") or "").strip().lower()
        if not wanted or not entry_region or entry_region == wanted:
            return i, stack[i]
    return -1, None


def _undo_status_payload(region: str = "") -> dict:
    undo = _undo_read_stack(UNDO_STACK_FILE)
    redo = _undo_read_stack(REDO_STACK_FILE)
    _, undo_entry = _undo_latest_for_region(undo, region)
    _, redo_entry = _undo_latest_for_region(redo, region)
    return {
        "can_undo": bool(undo_entry),
        "can_redo": bool(redo_entry),
        "undo": undo_entry,
        "redo": redo_entry,
        "undo_count": len(undo),
        "redo_count": len(redo),
    }


@app.get("/admin/undo-redo/status")
def admin_undo_redo_status(region: str = ""):
    return _json(True, **_undo_status_payload(region))


@app.post("/admin/undo")
def admin_undo(payload: dict | None = None):
    payload = payload or {}
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    region = str(payload.get("region") or "")
    with UNDO_REDO_LOCK:
        undo = _undo_read_stack(UNDO_STACK_FILE)
        redo = _undo_read_stack(REDO_STACK_FILE)
        idx, entry = _undo_latest_for_region(undo, region)
        if entry is None:
            return _json(False, status_code=400, error="Nothing to undo")
        undo.pop(idx)
        _restore_undo_entry(entry, "before")
        redo.append(entry)
        _undo_write_stack(UNDO_STACK_FILE, undo)
        _undo_write_stack(REDO_STACK_FILE, redo)
    return _json(True, restored="before", entry=entry, **_undo_status_payload(region))


@app.post("/admin/redo")
def admin_redo(payload: dict | None = None):
    payload = payload or {}
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    region = str(payload.get("region") or "")
    with UNDO_REDO_LOCK:
        undo = _undo_read_stack(UNDO_STACK_FILE)
        redo = _undo_read_stack(REDO_STACK_FILE)
        idx, entry = _undo_latest_for_region(redo, region)
        if entry is None:
            return _json(False, status_code=400, error="Nothing to redo")
        redo.pop(idx)
        _restore_undo_entry(entry, "after")
        undo.append(entry)
        _undo_write_stack(UNDO_STACK_FILE, undo)
        _undo_write_stack(REDO_STACK_FILE, redo)
    return _json(True, restored="after", entry=entry, **_undo_status_payload(region))


def _workstream_rows(data: dict, only_global: bool = False):
    rows = []
    for pm_name, pm in (data.get("pms") or {}).items():
        tasks = pm.get("tasks") or []
        responses = pm.get("responses") or {}
        for idx, response in responses.items():
            if not isinstance(response, dict) or not response.get("submitted"):
                continue
            if only_global and not response.get("global_saved"):
                continue
            try:
                i = int(idx)
            except Exception:
                i = 0
            task = tasks[i] if i < len(tasks) else {}
            rows.append({
                "pm": pm_name,
                "task_type": pm.get("task_type", "shortlisting"),
                "task_index": i,
                "ngo_name": task.get("ngo_name") or task.get("name") or "",
                "website": task.get("website") or "",
                "background": task.get("background") or "",
                "decision": response.get("decision") or "",
                "rank": response.get("rank") or response.get("decision") or "",
                "rank_label": response.get("rank_label") or "",
                "reason": response.get("reason") or "",
                "ngo_description": response.get("ngo_description") or "",
                "contact_number": response.get("contact_number") or response.get("referral_poc") or task.get("contact_number") or "",
                "referral_source": response.get("referral_source") or task.get("source_mix") or task.get("source") or "",
                "referral_poc": response.get("referral_poc") or task.get("referred_by") or "",
                "lead_id": task.get("lead_id") or "",
                "source_mix": task.get("source_mix") or task.get("source") or "",
                "one_line_understanding": task.get("one_line_understanding") or "",
                "submitted_at": response.get("submitted_at") or "",
                "global_saved": response.get("global_saved", False),
                "global_saved_at": response.get("global_saved_at") or "",
                "deadline": pm.get("deadline") or "",
            })
    return rows


def _json_text_from_msg(msg):
    return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


def _fallback_workstream_review(mode: str, rows: list[dict], rules: str, pm: str = ""):
    def words(x): return len(str(x or "").split())
    counts = []
    for r in rows:
        text = r.get("reason") or r.get("ngo_description") or ""
        counts.append(words(text))
    total = len(counts)
    avg = round(sum(counts) / total, 1) if total else 0
    very_short = sum(1 for n in counts if n <= 2)
    thin = sum(1 for n in counts if 3 <= n < 10)
    solid = sum(1 for n in counts if n >= 18)
    if total == 0:
        headline = "Nothing to review yet. Submit a response first."
    elif very_short and avg < 8:
        headline = "Too thin overall — add more raw thought, not better English."
    elif avg >= 18:
        headline = "Good depth — the judgement is being captured clearly."
    else:
        headline = "Usable, but a little more detail would help consolidation."
    flags = [
        f"{total} response(s) reviewed",
        f"Average length: {avg} words",
    ]
    if very_short:
        flags.append(f"{very_short} very short response(s)")
    if thin:
        flags.append(f"{thin} response(s) could use more thought captured")
    if solid:
        flags.append(f"{solid} detailed response(s)")
    suggestions = [
        "Write more of what went through your head.",
        "Fragments, Hinglish, spelling mistakes and messy notes are fine.",
        "Do not spend time making it sound polished.",
        "The goal is usable judgement, not perfect sentences.",
    ]
    return {
        "headline": headline,
        "quality_flags": flags[:4],
        "suggestions": suggestions[:4],
        "pace_comment": f"{total} submitted for {pm or 'this reviewer'}.",
        "encouragement": "Keep typing naturally. The consolidation team needs your instinct, not a polished essay.",
        "mix": {"total": total, "average_words": avg, "very_short": very_short, "thin": thin, "solid": solid},
        "source": "fallback",
    }

def _call_workstream_ai(mode: str, rows: list[dict], rules: str, pm: str = ""):
    if _get_anthropic() is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_workstream_review(mode, rows, rules, pm)
    if not rows:
        return _fallback_workstream_review(mode, rows, rules, pm)
    model = os.environ.get("WORKSTREAM_AI_MODEL") or os.environ.get("HAIKU_MODEL") or os.environ.get("STORY_MODEL") or "claude-haiku-4-5-20251001"
    # Keep input small and content-agnostic: review expression quality only, not NGO substance.
    compact_rows = []
    for r in rows[:40]:
        text = str(r.get("reason") or r.get("ngo_description") or "")[:900]
        compact_rows.append({
            "pm": r.get("pm"),
            "task_index": r.get("task_index"),
            "rank": r.get("rank") or r.get("decision"),
            "response_text": text,
            "word_count": len(text.split()),
        })
    prompt = f"""
You are reviewing DFP 2.0 PM workstream responses.

IMPORTANT: This is NOT a review of the NGO and NOT a review of whether the PM's 1–5 rank is correct. Do not evaluate the organisation, cohort, pathway, source, bridge-program claim, geography, or fit. Do not mention specific NGO facts. Do not ask for contact, referral, POC, source, geography, cohort, operational proof, or extra NGO details. Do not say things like "clear Yes on underserved group", "bridge-program needs proof", or "missing contact/referral details". That is content critique and is forbidden.

Review ONLY expression quality:
- Did the PM write enough words to capture their judgement?
- Is their thought process understandable enough for later consolidation?
- Are they giving raw instinct rather than trying to write polished English?
- Should they type more? If yes, say that simply.
- Never ask them to add NGO facts; only ask them to express their own thinking more clearly.

Hinglish, fragments, spelling mistakes, no punctuation, rough English, and stream-of-consciousness notes are all acceptable. Encourage more depth without asking for polished sentences. Be warm, crisp, and lightly funny. Use no more than 90 words total across all fields.

Return ONLY JSON with this schema:
{{
  "headline": "one crisp sentence about expression quality only",
  "quality_flags": ["max 3 short flags about length/clarity only"],
  "suggestions": ["max 3 short nudges about typing more / being natural only"],
  "pace_comment": "one short line",
  "encouragement": "one short warm/funny line"
}}

Admin rules, to be interpreted ONLY as expression-review rules, not content judgement:
{rules[:1200]}

Review mode: {mode}
PM: {pm}
Submitted rows:
{json.dumps(compact_rows, ensure_ascii=False)}
""".strip()
    try:
        client = _get_anthropic().Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=model,
            max_tokens=int(os.environ.get("WORKSTREAM_AI_MAX_TOKENS", "220")),
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        content = _json_text_from_msg(msg)
        try:
            data = _clean_json_from_text(content)
        except Exception:
            data = json.loads(content)
        if isinstance(data, dict):
            data["source"] = "haiku"
            return data
    except Exception as e:
        out = _fallback_workstream_review(mode, rows, rules, pm)
        out["ai_error"] = str(e)[:300]
        return out
    return _fallback_workstream_review(mode, rows, rules, pm)


@app.get("/workstream")
def workstream_get():
    return _json(True, data=_read_workstream_payload())


@app.post("/workstream/admin/update")
def workstream_admin_update(payload: dict):
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    review_rules = str((payload or {}).get("review_rules") or data.get("review_rules") or DEFAULT_WORKSTREAM_RULES)
    data["review_rules"] = review_rules
    pm_name = str((payload or {}).get("pm") or "")
    if pm_name and pm_name in data["pms"]:
        pm = data["pms"][pm_name]
        if "deadline" in payload: pm["deadline"] = str(payload.get("deadline") or pm.get("deadline") or "")
        if "deadline_note" in payload: pm["deadline_note"] = str(payload.get("deadline_note") or pm.get("deadline_note") or "")
        if "responsibility" in payload: pm["responsibility"] = str(payload.get("responsibility") or "")
        if "task_type" in payload: pm["task_type"] = str(payload.get("task_type") or pm.get("task_type") or "shortlisting")
        tasks = payload.get("tasks")
        if isinstance(tasks, list) and tasks:
            clean = []
            for t in tasks[:250]:
                if not isinstance(t, dict):
                    continue
                clean.append({
                    "ngo_name": str(t.get("ngo_name") or t.get("name") or "Untitled NGO")[:240],
                    "website": str(t.get("website") or "")[:500],
                    "background": str(t.get("background") or "")[:1500],
                })
            if clean:
                existing = pm.get("tasks") if isinstance(pm.get("tasks"), list) else []
                pm["tasks"] = existing + clean
                # Gearbox only appends tasks. It does not delete existing tasks or reset existing responses.
    _write_workstream_payload(data)
    _undo_snapshot_after("workstream_admin_update", "PM view settings/tasks updated", "", paths, undo_before)
    return _json(True, data=data)


@app.post("/workstream/admin/transfer-tasks")
def workstream_admin_transfer_tasks(payload: dict):
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    payload = payload or {}
    from_pm = str(payload.get("from_pm") or payload.get("source_pm") or "").strip()
    to_pm = str(payload.get("to_pm") or payload.get("target_pm") or "").strip()
    if not from_pm or not to_pm:
        return _json(False, status_code=400, error="from_pm and to_pm are required")
    if from_pm == to_pm:
        return _json(False, status_code=400, error="Source and target PM must be different")

    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    pms = data.setdefault("pms", {})
    if from_pm not in pms:
        return _json(False, status_code=400, error=f"Unknown source PM: {from_pm}")
    if to_pm not in pms:
        pms[to_pm] = {
            "name": to_pm,
            "deadline": time.strftime("%Y-%m-%dT%H:%M"),
            "deadline_note": DEFAULT_WORKSTREAM_RULES if False else "Once everyone submits, we compare rankings, identify strong cohorts, resolve overlaps, and move to human lead follow-ups. This needs to close by Wednesday so the lead list can be wrapped by the end of the week.",
            "responsibility": "Review assigned NGOs.",
            "task_type": "shortlisting",
            "tasks": [],
            "responses": {},
            "active": True,
        }

    source = pms[from_pm]
    target = pms[to_pm]
    source_tasks = source.get("tasks") if isinstance(source.get("tasks"), list) else []
    task_count = len(source_tasks)
    zero_based = bool(payload.get("zero_based"))
    raw_indices = payload.get("task_indices")
    indices: list[int] = []
    if isinstance(raw_indices, list) and raw_indices:
        for x in raw_indices:
            try:
                idx = int(x)
                indices.append(idx if zero_based else idx - 1)
            except Exception:
                continue
    else:
        try:
            start = int(payload.get("start_index") or payload.get("start") or payload.get("task_index"))
        except Exception:
            return _json(False, status_code=400, error="Provide task_index or start_index/end_index")
        try:
            end = int(payload.get("end_index") or payload.get("end") or start)
        except Exception:
            end = start
        if not zero_based:
            start -= 1
            end -= 1
        lo, hi = sorted([start, end])
        indices = list(range(lo, hi + 1))
    indices = sorted(set(indices))
    bad = [i for i in indices if i < 0 or i >= task_count]
    if not indices or bad:
        return _json(False, status_code=400, error="Transfer range is outside the source PM shortlist", task_count=task_count, bad_indices=[i + 1 for i in bad])

    move_responses = payload.get("move_responses") is not False
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    source_responses = source.get("responses") if isinstance(source.get("responses"), dict) else {}
    target_tasks = target.setdefault("tasks", [])
    target_responses = target.setdefault("responses", {})
    moved_response_count = 0
    moved = []
    for old_idx in indices:
        task = dict(source_tasks[old_idx] or {})
        task["transferred_from"] = from_pm
        task["transferred_to"] = to_pm
        task["transferred_at"] = now_s
        task["original_task_index"] = old_idx + 1
        new_idx = len(target_tasks)
        target_tasks.append(task)
        resp = source_responses.get(str(old_idx))
        if move_responses and isinstance(resp, dict) and resp.get("submitted"):
            copied = dict(resp)
            copied["transferred_from"] = from_pm
            copied["transferred_at"] = now_s
            target_responses[str(new_idx)] = copied
            moved_response_count += 1
        moved.append({"ngo_name": task.get("ngo_name") or task.get("name") or "", "from_index": old_idx + 1, "to_index": new_idx + 1})

    remove_set = set(indices)
    source["tasks"] = [task for idx, task in enumerate(source_tasks) if idx not in remove_set]
    source["responses"] = _workstream_reindex_responses_after_task_removal(source_responses, remove_set)
    _workstream_recount_pm(source)
    _workstream_recount_pm(target)

    summary = f"Transferred {len(indices)} shortlist item(s) from {from_pm} to {to_pm}."
    data.setdefault("global_log", []).insert(0, {"summary": summary, "at": now_s, "transfer": {"from_pm": from_pm, "to_pm": to_pm, "count": len(indices), "responses_moved": moved_response_count, "items": moved[:50]}})
    data["global_log"] = data["global_log"][:200]
    data = _write_workstream_payload(data)
    _undo_snapshot_after("workstream_transfer_tasks", summary, "", paths, undo_before)
    return _json(True, data=data, transferred=len(indices), responses_moved=moved_response_count, from_pm=from_pm, to_pm=to_pm, moved=moved)


@app.post("/workstream/admin/lock-edits")
def workstream_admin_lock_edits(payload: dict):
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    payload = payload or {}
    locked = bool(payload.get("locked"))
    target_all = bool(payload.get("all_pms") or payload.get("all") or str(payload.get("scope") or "").strip().lower() == "all")
    selected = [str(x).strip() for x in (payload.get("pms") or payload.get("pm_names") or []) if str(x).strip()]
    if not target_all and not selected:
        return _json(False, status_code=400, error="Select all PMs or at least one PM")

    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    locks = _workstream_lock_state(data)
    if target_all:
        locks["all"] = locked
        target_label = "all PMs"
    else:
        pm_locks = locks.setdefault("pms", {})
        for pm_name in selected:
            pm_locks[pm_name] = locked
        target_label = ", ".join(selected)
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = f"{'Locked' if locked else 'Unlocked'} edits for {target_label}."
    data.setdefault("global_log", []).insert(0, {"summary": summary, "at": now_s, "edit_locks": locks})
    data["global_log"] = data["global_log"][:200]
    data = _write_workstream_payload(data)
    _undo_snapshot_after("workstream_lock_edits", summary, "", paths, undo_before)
    return _json(True, data=data, edit_locks=locks, locked=locked, target="all" if target_all else selected)


@app.post("/workstream/submit")
def workstream_submit(payload: dict):
    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    pm_name = str((payload or {}).get("pm") or "")
    if pm_name not in data["pms"]:
        return _json(False, status_code=400, error="Unknown PM")
    if _workstream_pm_locked(data, pm_name):
        return _json(False, status_code=423, error=f"Edits are locked for {pm_name}. Ask admin to unlock from the gear panel.")
    pm = data["pms"][pm_name]
    try:
        idx = int((payload or {}).get("task_index"))
    except Exception:
        return _json(False, status_code=400, error="task_index is required")
    if idx < 0 or idx >= len(pm.get("tasks") or []):
        return _json(False, status_code=400, error="task_index is out of range")
    response = {
        "decision": str((payload or {}).get("decision") or ""),
        "rank": (payload or {}).get("rank") or (payload or {}).get("decision") or "",
        "rank_label": str((payload or {}).get("rank_label") or ""),
        "reason": str((payload or {}).get("reason") or ""),
        "ngo_description": str((payload or {}).get("ngo_description") or ""),
        "contact_number": str((payload or {}).get("contact_number") or ""),
        "referral_source": str((payload or {}).get("referral_source") or ""),
        "referral_poc": str((payload or {}).get("referral_poc") or (payload or {}).get("contact_number") or ""),
        "submitted": True,
        "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "global_saved": True,
        "global_saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    pm["responses"][str(idx)] = response
    pm["last_submitted_task_index"] = idx
    pm["last_submitted_at"] = response["submitted_at"]
    submitted_count = sum(1 for r in pm.get("responses", {}).values() if isinstance(r, dict) and r.get("submitted"))
    pm["global_saved_count"] = submitted_count
    pm["global_saved_at"] = response["global_saved_at"]
    data = _write_workstream_payload(data)
    _undo_snapshot_after("workstream_submit", f"PM response submitted: {pm_name}", "", paths, undo_before)
    return _json(True, data=data, submitted_count=submitted_count, first_five_due=(submitted_count <= 5))


@app.post("/workstream/delete-response")
def workstream_delete_response(payload: dict):
    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    pm_name = str((payload or {}).get("pm") or "")
    if pm_name not in data["pms"]:
        return _json(False, status_code=400, error="Unknown PM")
    if _workstream_pm_locked(data, pm_name):
        return _json(False, status_code=423, error=f"Edits are locked for {pm_name}. Ask admin to unlock from the gear panel.")
    try:
        idx = int((payload or {}).get("task_index"))
    except Exception:
        return _json(False, status_code=400, error="task_index is required")
    pm = data["pms"][pm_name]
    pm.setdefault("responses", {}).pop(str(idx), None)
    _workstream_recount_pm(pm)
    pm["global_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_workstream_payload(data)
    _undo_snapshot_after("workstream_delete_response", f"PM response deleted: {pm_name}", "", paths, undo_before)
    return _json(True, data=data)


@app.post("/workstream/global-save")
def workstream_global_save(payload: dict):
    paths = [WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    data = _read_workstream_payload()
    pm_name = str((payload or {}).get("pm") or "")
    if pm_name not in data["pms"]:
        return _json(False, status_code=400, error="Unknown PM")
    pm = data["pms"][pm_name]
    count = 0
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    for response in pm.get("responses", {}).values():
        if isinstance(response, dict) and response.get("submitted"):
            response["global_saved"] = True
            response["global_saved_at"] = now_s
            count += 1
    pm["global_saved_count"] = count
    pm["global_saved_at"] = now_s
    summary = f"{pm_name} saved {count}/{len(pm.get('tasks') or [])} responses globally."
    data.setdefault("global_log", []).insert(0, {"pm": pm_name, "count": count, "summary": summary, "at": now_s})
    data["global_log"] = data["global_log"][:100]
    _write_workstream_payload(data)
    _undo_snapshot_after("workstream_global_save", f"PM responses saved globally: {pm_name}", "", paths, undo_before)
    return _json(True, data=data, summary=summary)


@app.post("/workstream/ai/review")
def workstream_ai_review(payload: dict):
    data = _read_workstream_payload()
    mode = str((payload or {}).get("mode") or "so-far")
    pm_name = str((payload or {}).get("pm") or "")
    supplied_rows = (payload or {}).get("submitted_rows")
    if isinstance(supplied_rows, list) and supplied_rows:
        # Used immediately after submit so the mandatory first-five review never races backend storage.
        rows = []
        for r in supplied_rows[:40]:
            if not isinstance(r, dict):
                continue
            rows.append({
                "pm": str(r.get("pm") or pm_name),
                "task_index": int(r.get("task_index") or 0),
                "decision": str(r.get("decision") or ""),
                "rank": r.get("rank") or r.get("decision") or "",
                "reason": str(r.get("reason") or ""),
                "ngo_description": str(r.get("ngo_description") or ""),
                "submitted_at": str(r.get("submitted_at") or ""),
            })
    else:
        all_rows = _workstream_rows(data, only_global=(mode == "admin")) if mode == "admin" else _workstream_rows(data, only_global=False)
        rows = all_rows
        if mode != "admin":
            if pm_name not in data["pms"]:
                return _json(False, status_code=400, error="Unknown PM")
            rows = [r for r in all_rows if r.get("pm") == pm_name]
            if mode == "first-five":
                rows = sorted(rows, key=lambda r: r.get("task_index", 0))[:5]
                data["pms"][pm_name]["first_five_reviewed"] = True
            if mode == "selected":
                try:
                    idx = int((payload or {}).get("task_index"))
                    rows = [r for r in rows if int(r.get("task_index", -1)) == idx]
                except Exception:
                    rows = []
    review = _call_workstream_ai(mode, rows, data.get("review_rules", DEFAULT_WORKSTREAM_RULES), pm_name)
    data.setdefault("ai_log", []).insert(0, {"mode": mode, "pm": pm_name, "review": review, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    data["ai_log"] = data["ai_log"][:100]
    _write_workstream_payload(data)
    return _json(True, review=review, data=data)


@app.get("/workstream/export.csv")
def workstream_export_csv(global_only: bool = False):
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=global_only)
    headers = ["pm", "task_type", "task_index", "ngo_name", "website", "background", "decision", "rank", "rank_label", "reason", "ngo_description", "contact_number", "referral_source", "referral_poc", "submitted_at", "global_saved", "global_saved_at", "deadline"]
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(_safe_csv_row(row))
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=dfp_workstream_log.csv"})

# -----------------------------------------------------------------------------
# DFP 2.0 Discovery workspace / Lead Pool bridge
# -----------------------------------------------------------------------------
# This layer intentionally sits above the existing discovery/repository/story and
# workstream endpoints. It does not replace old outputs, archives or logs. It
# gives the revamped UI one persistent region workspace and a controlled bridge
# into PM ranking.

WORKSPACES_DIR = RUNS_DIR / "workspaces"
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

LEAD_POOL_HEADERS = [
    "lead_id", "region", "district", "ngo_name", "normalized_name", "website",
    "phone", "email", "source_type", "source_mix", "source_run", "source_run_date",
    "referred_by", "contact_number", "notes", "one_line_understanding",
    "background_summary", "evidence_summary", "confidence", "status", "information_status",
    "fit_status", "source_tag", "send_for_shortlisting", "shortlisting_comment",
    "curation_status", "curation_comment", "approved_by", "approved_at",
    "decided_by", "decided_at", "ranking_status", "duplicate_of", "existing_ranking_ref",
    "reviewer_comments", "created_at", "updated_at",
]

APPROVED_CURATION_STATUSES = {"approved_for_ranking", "approved_with_comment"}
_LEAD_POOL_LOCK = threading.RLock()

LEAD_POOL_CURATION_STATUSES = {
    "pending_review", "approved_for_ranking", "approved_with_comment", "needs_follow_up",
    "duplicate", "already_rated", "hold", "sent_back_to_pool",
}


def _slug(value: str) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = raw.strip("_")
    return raw or "workspace"


def _workspace_dir(region: str) -> Path:
    path = WORKSPACES_DIR / _slug(region or "karnataka")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lead_pool_path(region: str) -> Path:
    return _workspace_dir(region) / "lead_pool.csv"


def _workspace_log_path(region: str) -> Path:
    return _workspace_dir(region) / "workspace_log.jsonl"


def _normalise_lead_name(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b(public|charitable|educational|education|seva|social|welfare)\b", " ", text)
    text = re.sub(r"\b(trust|society|foundation|samsthe|sanstha|ngo|organization|organisation)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _lead_key(row: dict) -> str:
    name = _normalise_lead_name(row.get("ngo_name") or row.get("name") or row.get("Organisation") or row.get("NGO Name") or "")
    district = str(row.get("district") or row.get("District") or row.get("Location") or "").strip().lower()
    website = str(row.get("website") or row.get("Website") or row.get("url") or "").strip().lower()
    if website:
        try:
            domain = urlparse(website if website.startswith(("http://", "https://")) else "https://" + website).netloc.lower().replace("www.", "")
            if domain:
                return f"domain:{domain}"
        except Exception:
            pass
    return f"name:{name}|district:{district}"


def _read_lead_pool(region: str) -> list[dict]:
    path = _lead_pool_path(region)
    if not path.exists():
        return []
    with _LEAD_POOL_LOCK:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))


def _write_lead_pool(region: str, rows: list[dict]) -> None:
    path = _lead_pool_path(region)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LEAD_POOL_LOCK:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=LEAD_POOL_HEADERS, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    clean = {h: row.get(h, "") for h in LEAD_POOL_HEADERS}
                    writer.writerow(_safe_csv_row(clean))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass


def _workspace_log(region: str, event: str, payload: dict | None = None) -> None:
    row = {"event": event, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "payload": payload or {}}
    with _workspace_log_path(region).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _coalesce(*values) -> str:
    for value in values:
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


SOURCE_TAG_OPTIONS = {
    "internet discovery": "Internet Discovery",
    "internet": "Internet Discovery",
    "general discovery": "Internet Discovery",
    "bulk discovery": "Bulk Discovery",
    "bulk": "Bulk Discovery",
    "human referral": "Human Referral",
    "referral": "Human Referral",
    "archive import": "Archive Import",
    "archive": "Archive Import",
    "smart recovery": "Smart Recovery",
    "recovery": "Smart Recovery",
    "manual add": "Manual Add",
    "manual": "Manual Add",
}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "send", "sent", "approve", "approved", "shortlist", "shortlisted", "x", "✓", "✔"}


def _source_tag(row: dict) -> str:
    """Return the canonical source tag for Lead Pool / PM assignment.

    Existing PM tasks from before this version may not have source tags. This
    function is only used for new Lead Pool decisions and new PM assignments;
    it never backfills or mutates old PM workstream tasks.
    """
    raw = _coalesce(
        row.get("source_tag"), row.get("Source Tag"), row.get("source_mix"),
        row.get("source_type"), row.get("Source"), row.get("source"), row.get("module"), ""
    )
    if not raw:
        return ""
    low = str(raw).strip().lower()
    for needle, label in SOURCE_TAG_OPTIONS.items():
        if needle in low:
            return label
    return str(raw).strip()


def _shortlisting_comment(row: dict) -> str:
    """Return the shortlisting comment for new PM assignment.

    Older PM tasks/responses may have no such field. That is valid legacy data.
    This accessor is intentionally read-only and only gates new leads being sent
    forward from Lead Pool after v46/v47.
    """
    return _coalesce(
        row.get("shortlisting_comment"), row.get("Shortlisting Comment"),
        row.get("curation_comment"), row.get("Curation Comment"),
        row.get("reviewer_comments"), row.get("Reviewer Comments"),
        row.get("comments"), row.get("Comments"), row.get("notes"), row.get("Notes"), ""
    )


def _first_sentence(text: str, limit: int = 180) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    m = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)[0]
    return (m[:limit-1].rstrip() + "…") if len(m) > limit else m


def _lead_from_any(row: dict, region: str, source_type: str = "") -> dict:
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    name = _coalesce(row.get("ngo_name"), row.get("NGO Name"), row.get("Organisation"), row.get("organization"), row.get("name"), row.get("input_name"), "Untitled NGO")
    district = _coalesce(row.get("district"), row.get("District"), row.get("Location"), row.get("location"), "")
    website = _coalesce(row.get("website"), row.get("Website"), row.get("Official Website"), row.get("url"), row.get("Website / Source"), row.get("Source URL"), "")
    source = _coalesce(source_type, row.get("source_type"), row.get("source_mix"), row.get("Source"), row.get("module"), "Internet Discovery")
    evidence = _coalesce(row.get("evidence_summary"), row.get("Evidence"), row.get("Why It Belongs"), row.get("Story Summary"), row.get("Digital Presence Assessment"), row.get("background_summary"), row.get("background"), "")
    notes = _coalesce(row.get("notes"), row.get("Notes"), row.get("comments"), row.get("Comments"), row.get("Why NGO Is Interesting"), row.get("Story Summary"), row.get("Digital Presence Assessment"), row.get("background"), "")
    one_line = _coalesce(row.get("one_line_understanding"), row.get("one_line"), row.get("One-line Understanding"), _first_sentence(evidence or notes or row.get("background") or ""))
    background = _coalesce(row.get("background_summary"), row.get("background"), row.get("Background"), evidence, notes)
    explicit_info_status = _coalesce(row.get("information_status"), row.get("Information Status"), row.get("info_status"), "")
    if explicit_info_status:
        information_status = explicit_info_status
    elif str(source).lower().startswith("human referral") or str(source).lower().startswith("referral"):
        information_status = "Needs Follow-up" if not (website or evidence or notes) else "Sufficient"
    elif website or evidence:
        information_status = "Sufficient"
    else:
        information_status = "Insufficient"
    curation_status = _coalesce(row.get("curation_status"), row.get("compiled_decision"), row.get("Curation Status"), "pending_review")
    if curation_status not in LEAD_POOL_CURATION_STATUSES:
        curation_status = "pending_review"
    contact_number = _coalesce(row.get("contact_number"), row.get("Contact Number"), row.get("phone"), row.get("Phone"), "")
    return {
        "lead_id": str(row.get("lead_id") or uuid.uuid4().hex[:12]),
        "region": _coalesce(row.get("region"), row.get("State"), row.get("state"), region),
        "district": district,
        "ngo_name": name,
        "normalized_name": _normalise_lead_name(name),
        "website": website,
        "phone": _coalesce(row.get("phone"), row.get("Phone"), contact_number, ""),
        "email": _coalesce(row.get("email"), row.get("Email"), ""),
        "source_type": source,
        "source_mix": _coalesce(row.get("source_mix"), source),
        "source_run": _coalesce(row.get("source_run"), row.get("source_run_id"), row.get("run_id"), row.get("Run ID"), "manual_output"),
        "source_run_date": _coalesce(row.get("source_run_date"), row.get("updated_at"), row.get("created_at"), ""),
        "referred_by": _coalesce(row.get("referred_by"), row.get("Referred By"), row.get("referral_source"), ""),
        "contact_number": contact_number,
        "notes": notes,
        "one_line_understanding": one_line,
        "background_summary": background,
        "evidence_summary": evidence,
        "confidence": _coalesce(row.get("confidence"), row.get("Confidence"), row.get("AI Confidence"), ""),
        "status": _coalesce(row.get("status"), row.get("Status"), row.get("Output Tier"), row.get("Repository Status"), "New"),
        "information_status": information_status,
        "fit_status": _coalesce(row.get("fit_status"), row.get("Fit Status"), row.get("fit"), "Unknown"),
        "source_tag": _source_tag({**row, "source_type": source}),
        "send_for_shortlisting": _coalesce(row.get("send_for_shortlisting"), row.get("Send For Shortlisting"), row.get("send_for_approval"), row.get("Send For Approval"), ""),
        "shortlisting_comment": _shortlisting_comment(row),
        "curation_status": curation_status,
        "curation_comment": _coalesce(row.get("curation_comment"), row.get("compiled_comment"), row.get("reviewer_comments"), row.get("comments"), row.get("shortlisting_comment"), ""),
        "approved_by": _coalesce(row.get("approved_by"), ""),
        "approved_at": _coalesce(row.get("approved_at"), ""),
        "decided_by": _coalesce(row.get("decided_by"), ""),
        "decided_at": _coalesce(row.get("decided_at"), ""),
        "ranking_status": _coalesce(row.get("ranking_status"), "Not Sent"),
        "duplicate_of": _coalesce(row.get("duplicate_of"), ""),
        "existing_ranking_ref": _coalesce(row.get("existing_ranking_ref"), ""),
        "reviewer_comments": _coalesce(row.get("reviewer_comments"), row.get("Reviewer Comments"), row.get("call_comments"), row.get("comments"), ""),
        "created_at": _coalesce(row.get("created_at"), now_s),
        "updated_at": now_s,
    }


def _merge_source(existing: str, incoming: str) -> str:
    parts = []
    for value in [existing, incoming]:
        for piece in re.split(r"[+/;,]", str(value or "")):
            piece = piece.strip()
            if piece and piece not in parts:
                parts.append(piece)
    return " + ".join(parts) if parts else incoming or existing


def _merge_lead(existing: dict, incoming: dict) -> dict:
    out = dict(existing)
    merged_source = _merge_source(existing.get("source_mix") or existing.get("source_type", ""), incoming.get("source_mix") or incoming.get("source_type", ""))
    out["source_type"] = merged_source
    out["source_mix"] = merged_source
    out["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    for key in LEAD_POOL_HEADERS:
        if key in {"lead_id", "created_at", "source_type", "source_mix", "normalized_name", "curation_status", "ranking_status"}:
            continue
        if not out.get(key) and incoming.get(key):
            out[key] = incoming.get(key)
    # Keep human referral notes/contact fresh because those are usually manually sourced.
    if "referral" in str(incoming.get("source_type", "")).lower() or "referral" in str(incoming.get("source_mix", "")).lower():
        for key in ("referred_by", "contact_number", "phone", "notes", "reviewer_comments", "curation_comment"):
            if incoming.get(key):
                if key in {"notes", "reviewer_comments", "curation_comment"} and out.get(key) and incoming.get(key) not in str(out.get(key)):
                    out[key] = str(out.get(key)) + " | " + str(incoming[key])
                else:
                    out[key] = incoming[key]
    if not out.get("information_status"):
        out["information_status"] = incoming.get("information_status") or "Insufficient"
    if not out.get("curation_status"):
        out["curation_status"] = incoming.get("curation_status") or "pending_review"
    if not out.get("ranking_status"):
        out["ranking_status"] = incoming.get("ranking_status") or "Not Sent"
    return out


def _rating_existing_keys(data: dict) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    domains: set[str] = set()
    for pm_data in (data.get("pms") or {}).values():
        tasks = pm_data.get("tasks") or []
        for task in tasks:
            nm = _normalise_lead_name(task.get("ngo_name") or task.get("name") or "")
            if nm:
                names.add(nm)
            site = str(task.get("website") or "").strip()
            if site:
                try:
                    d = urlparse(site if site.startswith(("http://", "https://")) else "https://" + site).netloc.lower().replace("www.", "")
                    if d:
                        domains.add(d)
                except Exception:
                    pass
    return names, domains


def _lead_matches_existing_ranking(row: dict, existing_names: set[str], existing_domains: set[str]) -> bool:
    nm = _normalise_lead_name(row.get("ngo_name") or "")
    if nm and nm in existing_names:
        return True
    site = str(row.get("website") or "").strip()
    if site:
        try:
            d = urlparse(site if site.startswith(("http://", "https://")) else "https://" + site).netloc.lower().replace("www.", "")
            if d and d in existing_domains:
                return True
        except Exception:
            pass
    return False


def _mark_lead_as_existing_ranking(row: dict, reason: str = "already_rated_or_assigned") -> dict:
    """Mark a Lead Pool row that already exists in PM ranking/review data.

    This is deliberately done before curation/assignment so archive imports, old
    history runs and referrals cannot accidentally re-enter the PM review queue.
    It preserves all useful contact/source/comment fields and only updates the
    curation/ranking overlay fields.
    """
    out = dict(row)
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    out["curation_status"] = "already_rated"
    out["ranking_status"] = "Already Rated"
    out["existing_ranking_ref"] = out.get("existing_ranking_ref") or _normalise_lead_name(out.get("ngo_name") or "")
    out["decided_by"] = out.get("decided_by") or "system"
    out["decided_at"] = out.get("decided_at") or now_s
    out["updated_at"] = now_s
    note = "Already exists in PM ranking/review data"
    if reason:
        note += f" ({reason})"
    existing_comment = str(out.get("curation_comment") or "").strip()
    if note not in existing_comment:
        out["curation_comment"] = f"{existing_comment} | {note}" if existing_comment else note
    return out


def _annotate_existing_ranking_leads(rows: list[dict]) -> tuple[list[dict], int]:
    """Apply already-rated/assigned overlay to rows matching existing ranking data."""
    data = _read_workstream_payload()
    existing_names, existing_domains = _rating_existing_keys(data)
    marked = 0
    annotated: list[dict] = []
    for row in rows:
        if _lead_matches_existing_ranking(row, existing_names, existing_domains):
            current = str(row.get("curation_status") or "").strip().lower()
            rank_status = str(row.get("ranking_status") or "").strip().lower()
            if current != "already_rated" or rank_status not in {"already rated", "already assigned", "finalized"}:
                marked += 1
            annotated.append(_mark_lead_as_existing_ranking(row))
        else:
            annotated.append(row)
    return annotated, marked


def _lead_one_line(row: dict) -> str:
    return _coalesce(row.get("one_line_understanding"), _first_sentence(row.get("evidence_summary") or row.get("background_summary") or row.get("notes") or row.get("reviewer_comments") or ""), "Potential DFP lead; review context before ranking.")


@app.get("/workspace/{region}/lead-pool")
def workspace_lead_pool(region: str):
    rows = _read_lead_pool(region)
    return _json(True, region=region, count=len(rows), rows=rows)


@app.post("/workspace/{region}/lead-pool/import")
def workspace_import_leads(region: str, payload: dict):
    source_type = str((payload or {}).get("source_type") or "")
    incoming_rows = (payload or {}).get("rows") or []
    if not isinstance(incoming_rows, list):
        return _json(False, status_code=400, error="rows must be a list")
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    existing = _read_lead_pool(region)
    by_key = {_lead_key(row): row for row in existing}
    added = 0
    updated = 0
    for raw in incoming_rows:
        if not isinstance(raw, dict):
            continue
        incoming = _lead_from_any(raw, region, source_type=source_type)
        key = _lead_key(incoming)
        if key in by_key:
            by_key[key] = _merge_lead(by_key[key], incoming)
            updated += 1
        else:
            by_key[key] = incoming
            added += 1
    rows = list(by_key.values())
    rows, already_rated_marked = _annotate_existing_ranking_leads(rows)
    rows.sort(key=lambda r: (str(r.get("source_type") or ""), str(r.get("district") or ""), str(r.get("ngo_name") or "")))
    _write_lead_pool(region, rows)
    # "updated" here means the NGO was already present in Lead Pool and was merged,
    # not inserted again. Keep explicit aliases so the frontend can show a clear
    # "added vs already there" confirmation without implying overwrite.
    already_existing_count = updated
    _workspace_log(region, "lead_pool_import", {"source_type": source_type, "incoming": len(incoming_rows), "added": added, "updated": updated, "already_existing_count": already_existing_count, "already_rated_marked": already_rated_marked})
    _undo_snapshot_after("lead_pool_import", f"Lead Pool import: {added} added, {updated} existing", region, paths, undo_before)
    return _json(
        True,
        region=region,
        count=len(rows),
        incoming=len(incoming_rows),
        added=added,
        updated=updated,
        merged=updated,
        already_existing_count=already_existing_count,
        not_added_existing_count=already_existing_count,
        already_rated_marked=already_rated_marked,
        message=f"{added} added. {already_existing_count} already existed and were not duplicated.",
        rows=rows,
    )


@app.post("/workspace/{region}/lead-pool/update")
def workspace_update_lead(region: str, payload: dict):
    payload = payload or {}
    lead_id = str(payload.get("lead_id") or "").strip()
    if not lead_id:
        return _json(False, status_code=400, error="lead_id is required")
    allowed = {"ngo_name", "district", "website", "contact_number", "referred_by", "notes", "one_line_understanding", "background_summary", "evidence_summary", "confidence", "status", "information_status", "fit_status", "source_tag", "send_for_shortlisting", "shortlisting_comment", "curation_status", "curation_comment", "reviewer_comments", "ranking_status", "duplicate_of", "existing_ranking_ref"}
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_lead_pool(region)
    updated = False
    for row in rows:
        if str(row.get("lead_id") or "") == lead_id:
            for key in allowed:
                if key in payload:
                    row[key] = str(payload.get(key) or "")
            row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            row["normalized_name"] = _normalise_lead_name(row.get("ngo_name") or "")
            updated = True
            break
    if not updated:
        return _json(False, status_code=404, error="Lead not found")
    _write_lead_pool(region, rows)
    _workspace_log(region, "lead_pool_update", {"lead_id": lead_id, "fields": sorted([k for k in allowed if k in payload])})
    _undo_snapshot_after("lead_pool_update", "Lead Pool row edited", region, paths, undo_before)
    return _json(True, region=region, count=len(rows), rows=rows)


@app.post("/workspace/{region}/lead-pool/curate")
def workspace_curate_leads(region: str, payload: dict):
    payload = payload or {}
    ids = {str(x).strip() for x in (payload.get("lead_ids") or ([payload.get("lead_id")] if payload.get("lead_id") else [])) if str(x).strip()}
    status = str(payload.get("curation_status") or payload.get("decision") or "").strip().lower()
    status = status.replace(" ", "_").replace("-", "_")
    if status not in LEAD_POOL_CURATION_STATUSES:
        return _json(False, status_code=400, error="Invalid curation_status")
    if not ids:
        return _json(False, status_code=400, error="lead_id or lead_ids is required")
    comment = str(payload.get("curation_comment") or payload.get("comment") or payload.get("shortlisting_comment") or "").strip()
    source_tag_payload = str(payload.get("source_tag") or payload.get("tag") or "").strip()
    actor = str(payload.get("actor") or payload.get("decided_by") or payload.get("approved_by") or "Admin").strip() or "Admin"
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_lead_pool(region)
    changed = 0
    blocked = []
    existing_names, existing_domains = _rating_existing_keys(_read_workstream_payload())
    for row in rows:
        if str(row.get("lead_id") or "") not in ids:
            continue
        existing_tag = _source_tag(row)
        existing_comment = _shortlisting_comment(row)
        final_tag = source_tag_payload or existing_tag
        final_comment = comment or existing_comment
        if status in APPROVED_CURATION_STATUSES and _lead_matches_existing_ranking(row, existing_names, existing_domains):
            # Old PM shortlist/review work may not have the new metadata fields.
            # Do not require them or re-send the NGO; only mark this Lead Pool row.
            row.update(_mark_lead_as_existing_ranking(row, "already_rated_or_assigned_from_curation"))
            changed += 1
            continue
        if status in APPROVED_CURATION_STATUSES and (not final_tag or not final_comment):
            blocked.append({"lead_id": row.get("lead_id"), "ngo_name": row.get("ngo_name"), "missing_source_tag": not bool(final_tag), "missing_comment": not bool(final_comment)})
            continue
        row["curation_status"] = status
        if final_tag:
            row["source_tag"] = final_tag
        if final_comment:
            row["shortlisting_comment"] = final_comment
        if comment:
            row["curation_comment"] = comment
            row["reviewer_comments"] = comment
        row["decided_by"] = actor
        row["decided_at"] = now_s
        if status in APPROVED_CURATION_STATUSES:
            row["approved_by"] = actor
            row["approved_at"] = now_s
            row["ranking_status"] = row.get("ranking_status") or "Not Sent"
        if status == "needs_follow_up":
            row["information_status"] = "Needs Follow-up"
        if status in {"duplicate", "already_rated", "hold", "sent_back_to_pool"}:
            row["ranking_status"] = status.replace("_", " ").title()
        row["updated_at"] = now_s
        changed += 1
    if blocked and not changed:
        return _json(False, status_code=422, error="Source tag and shortlisting comment are required before sending for shortlisting", blocked=blocked, blocked_count=len(blocked))
    if not changed:
        return _json(False, status_code=404, error="No matching leads found")
    _write_lead_pool(region, rows)
    _workspace_log(region, "lead_pool_curate", {"lead_ids": list(ids), "curation_status": status, "changed": changed})
    _undo_snapshot_after("lead_pool_curate", f"Lead Pool decision changed: {status}", region, paths, undo_before)
    return _json(True, region=region, changed=changed, rows=rows)



@app.post("/workspace/{region}/lead-pool/import-decisions")
def workspace_import_lead_pool_decisions(region: str, payload: dict):
    """Apply Excel/CSV shortlisting decisions to existing Lead Pool rows.

    Expected row fields can include:
    lead_id, ngo_name, website, send_for_shortlisting, source_tag,
    shortlisting_comment. Rows marked TRUE/YES/1/shortlist in
    send_for_shortlisting are approved for PM shortlisting only if both a source
    tag and a shortlisting comment are present.
    """
    payload = payload or {}
    incoming_rows = payload.get("rows") or []
    if not isinstance(incoming_rows, list):
        return _json(False, status_code=400, error="rows must be a list")
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_lead_pool(region)
    by_id = {str(r.get("lead_id") or ""): r for r in rows if str(r.get("lead_id") or "")}
    by_key = {_lead_key(r): r for r in rows}
    changed = 0
    approved = 0
    metadata_updated = 0
    skipped_existing = 0
    blocked = []
    not_found = []
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    actor = str(payload.get("actor") or "Excel Import").strip() or "Excel Import"
    existing_names, existing_domains = _rating_existing_keys(_read_workstream_payload())
    for raw in incoming_rows:
        if not isinstance(raw, dict):
            continue
        incoming = _lead_from_any(raw, region, source_type=str(raw.get("source_type") or raw.get("source_mix") or ""))
        rid = str(raw.get("lead_id") or raw.get("Lead ID") or "").strip()
        row = by_id.get(rid) if rid else None
        if row is None:
            row = by_key.get(_lead_key(incoming))
        if row is None:
            not_found.append({"lead_id": rid, "ngo_name": incoming.get("ngo_name")})
            continue
        final_tag = str(raw.get("source_tag") or raw.get("Source Tag") or row.get("source_tag") or row.get("source_mix") or row.get("source_type") or "").strip()
        final_comment = str(raw.get("shortlisting_comment") or raw.get("Shortlisting Comment") or raw.get("comment") or raw.get("Comment") or row.get("shortlisting_comment") or row.get("curation_comment") or row.get("reviewer_comments") or "").strip()
        should_send = _truthy(raw.get("send_for_shortlisting") or raw.get("Send For Shortlisting") or raw.get("send_for_approval") or raw.get("Send For Approval"))
        if final_tag:
            row["source_tag"] = final_tag
        if final_comment:
            row["shortlisting_comment"] = final_comment
            row["curation_comment"] = final_comment
            row["reviewer_comments"] = final_comment
        if should_send and _lead_matches_existing_ranking(row, existing_names, existing_domains):
            # Legacy PM tasks may not have tags/comments. Do not block or alter
            # them; just keep this Lead Pool row out of the new assignment queue.
            marked = _mark_lead_as_existing_ranking(row, "already_rated_or_assigned_from_excel")
            row.update(marked)
            skipped_existing += 1
            changed += 1
            continue
        if should_send:
            if not final_tag or not final_comment:
                blocked.append({"lead_id": row.get("lead_id"), "ngo_name": row.get("ngo_name"), "missing_source_tag": not bool(final_tag), "missing_comment": not bool(final_comment)})
                continue
            row["send_for_shortlisting"] = "TRUE"
            row["curation_status"] = "approved_with_comment"
            row["approved_by"] = actor
            row["approved_at"] = now_s
            row["decided_by"] = actor
            row["decided_at"] = now_s
            row["ranking_status"] = row.get("ranking_status") or "Not Sent"
            approved += 1
        elif final_tag or final_comment:
            metadata_updated += 1
        row["updated_at"] = now_s
        changed += 1
    if changed:
        rows, already_rated_marked = _annotate_existing_ranking_leads(rows)
        _write_lead_pool(region, rows)
    else:
        already_rated_marked = 0
    result = {
        "region": region,
        "incoming": len(incoming_rows),
        "changed": changed,
        "approved_for_shortlisting": approved,
        "metadata_updated": metadata_updated,
        "blocked_count": len(blocked),
        "not_found_count": len(not_found),
        "skipped_existing_count": skipped_existing,
        "already_rated_marked": already_rated_marked,
        "blocked": blocked[:100],
        "not_found": not_found[:100],
        "message": f"Excel decisions applied: {approved} sent for shortlisting, {metadata_updated} updated, {skipped_existing} already assigned/rated skipped, {len(blocked)} blocked, {len(not_found)} not found.",
        "rows": rows,
    }
    _workspace_log(region, "lead_pool_import_decisions", result)
    _undo_snapshot_after("lead_pool_import_decisions", f"Shortlisting decisions imported: {approved} approved", region, paths, undo_before)
    return _json(True, **result)

@app.get("/workspace/{region}/approved-leads")
def workspace_approved_leads(region: str):
    rows = [r for r in _read_lead_pool(region) if str(r.get("curation_status") or "").strip().lower() in APPROVED_CURATION_STATUSES and str(r.get("ranking_status") or "").strip().lower() not in {"sent to ranking", "already assigned", "already rated", "finalized"}]
    return _json(True, region=region, count=len(rows), rows=rows)


@app.post("/workspace/{region}/approved-leads/delete-all")
def workspace_delete_all_approved_leads(region: str, payload: dict | None = None):
    payload = payload or {}
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))

    # Require an explicit confirmation flag so scripts cannot wipe approved
    # Lead Pool memory by accidentally hitting the endpoint with only a password.
    if payload.get("confirm") is not True:
        return _json(False, status_code=400, error="Confirmation required before deleting all approved leads")

    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_lead_pool(region)
    approved_statuses = {str(x).strip().lower() for x in APPROVED_CURATION_STATUSES}
    remaining = [
        row for row in rows
        if str(row.get("curation_status") or "").strip().lower() not in approved_statuses
    ]
    deleted_rows = len(rows) - len(remaining)
    _write_lead_pool(region, remaining)
    _workspace_log(region, "approved_leads_delete_all", {"deleted": deleted_rows})
    _undo_snapshot_after("approved_leads_delete_all", f"Approved Leads delete all: {deleted_rows} row(s)", region, paths, undo_before)
    return _json(True, region=region, deleted=deleted_rows, count=len(remaining), rows=remaining)


@app.get("/workspace/{region}/funnel-metrics")
def workspace_funnel_metrics(region: str):
    rows = _read_lead_pool(region)
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        st = str(row.get("curation_status") or "pending_review") or "pending_review"
        by_status[st] = by_status.get(st, 0) + 1
        src = str(row.get("source_mix") or row.get("source_type") or "Unknown")
        by_source[src] = by_source.get(src, 0) + 1
    data = _read_workstream_payload()
    rated = len(_workstream_rows(data, only_global=False))
    return _json(True, region=region, leads_entered_pool=len(rows), approved_for_ranking_count=sum(by_status.get(x,0) for x in APPROVED_CURATION_STATUSES), status_counts=by_status, source_counts=by_source, rated_count=rated)


@app.post("/workspace/{region}/lead-pool/delete")
def workspace_delete_leads(region: str, payload: dict):
    payload = payload or {}
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_lead_pool(region)
    before = len(rows)
    if payload.get("all") is True:
        remaining = []
    else:
        ids = {str(x).strip() for x in (payload.get("lead_ids") or []) if str(x).strip()}
        status = str(payload.get("information_status") or "").strip().lower()
        source = str(payload.get("source_type") or "").strip().lower()
        def keep(row: dict) -> bool:
            if ids and str(row.get("lead_id") or "") in ids:
                return False
            if status and str(row.get("information_status") or "").strip().lower() == status:
                return False
            if source and source in str(row.get("source_type") or "").strip().lower():
                return False
            return True
        remaining = [row for row in rows if keep(row)]
    deleted = before - len(remaining)
    _write_lead_pool(region, remaining)
    _workspace_log(region, "lead_pool_delete", {"deleted": deleted, "payload": payload})
    _undo_snapshot_after("lead_pool_delete", f"Lead Pool delete: {deleted} row(s)", region, paths, undo_before)
    return _json(True, region=region, deleted=deleted, count=len(remaining), rows=remaining)


@app.get("/workspace/{region}/lead-pool/export.csv")
def workspace_lead_pool_export(region: str):
    path = _lead_pool_path(region)
    if not path.exists():
        _write_lead_pool(region, [])
    filename = f"{_slug(region)}_lead_pool.csv"
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.post("/workspace/{region}/send-to-ranking")
def workspace_send_to_ranking(region: str, payload: dict | None = None):
    payload = payload or {}
    # Sending approved leads into PM review is an admin action. This protects the
    # existing PM shortlist work from accidental overwrites or bulk assignment.
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))

    paths = [_lead_pool_path(region), WORKSTREAM_DATA_FILE]
    undo_before = _undo_snapshot_before(paths)
    all_rows = _read_lead_pool(region)
    lead_ids = {str(x).strip() for x in (payload.get("lead_ids") or []) if str(x).strip()}
    rows = all_rows
    if lead_ids:
        rows = [r for r in rows if str(r.get("lead_id") or "") in lead_ids]
    else:
        rows = [r for r in rows if str(r.get("curation_status") or "").strip().lower() in APPROVED_CURATION_STATUSES]
    if not rows:
        return _json(False, status_code=400, error="No matching approved Lead Pool rows to send")

    blocked = [r for r in rows if str(r.get("curation_status") or "").strip().lower() not in APPROVED_CURATION_STATUSES]
    if blocked:
        return _json(False, status_code=422, error="Only Approved Leads can be sent to ranking", blocked_not_approved_count=len(blocked), blocked=[{"lead_id": r.get("lead_id"), "ngo_name": r.get("ngo_name"), "curation_status": r.get("curation_status")} for r in blocked[:50]])

    # Do not force new v46 metadata onto old PM work. Some NGOs may already
    # exist in the legacy PM shortlist/review memory without source tags or
    # shortlisting comments. Those should be skipped as already assigned/rated,
    # not blocked for missing metadata. Metadata is required only for genuinely
    # new leads that are about to create new PM tasks.

    pms = payload.get("pms") or payload.get("pm_names") or [payload.get("pm") or "Milan"]
    pms = [str(p).strip() for p in pms if str(p).strip()]
    if not pms:
        return _json(False, status_code=400, error="At least one PM is required")

    distribution_raw = str(payload.get("distribution") or payload.get("assignment_mode") or "split_evenly").strip().lower()
    assign_to_each = distribution_raw in {"assign_to_each", "everyone", "all", "all_pms", "send_to_everyone"}

    data = _read_workstream_payload()
    for pm in pms:
        data["pms"].setdefault(pm, {
            "name": pm, "deadline": time.strftime("%Y-%m-%dT%H:%M"), "responsibility": "Review assigned NGOs.", "task_type": "shortlisting", "tasks": [], "responses": {}, "active": True
        })
    existing_names, existing_domains = _rating_existing_keys(data)
    new_rows = []
    skipped = []
    for row in rows:
        if _lead_matches_existing_ranking(row, existing_names, existing_domains):
            row["ranking_status"] = "Already Rated"
            row["existing_ranking_ref"] = _normalise_lead_name(row.get("ngo_name") or "")
            # Preserve old PM work exactly as-is; only annotate this Lead Pool row.
            skipped.append({"lead_id": row.get("lead_id"), "ngo_name": row.get("ngo_name"), "reason": "already_rated_or_assigned"})
        elif str(row.get("ranking_status") or "").strip().lower() in {"sent to ranking", "already assigned", "already rated", "finalized"}:
            skipped.append({"lead_id": row.get("lead_id"), "ngo_name": row.get("ngo_name"), "reason": row.get("ranking_status")})
        else:
            new_rows.append(row)

    missing_metadata = []
    for r in new_rows:
        tag = _source_tag(r)
        comment = _shortlisting_comment(r)
        if not tag or not comment:
            missing_metadata.append({"lead_id": r.get("lead_id"), "ngo_name": r.get("ngo_name"), "missing_source_tag": not bool(tag), "missing_comment": not bool(comment)})
    if missing_metadata:
        return _json(False, status_code=422, error="Every new lead needs a source tag and shortlisting comment before sending to PM shortlisting. Existing assigned/rated NGOs are skipped and not disturbed.", blocked_missing_metadata_count=len(missing_metadata), blocked=missing_metadata[:100], skipped_existing_count=len(skipped), skipped=skipped[:100])

    assignments = {pm: 0 for pm in pms}
    batch_id = f"ranking_{_slug(region)}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def build_task(row: dict) -> dict:
        source = _source_tag(row) or row.get("source_mix") or row.get("source_type") or "Lead Pool"
        district = row.get("district") or ""
        one_line = _lead_one_line(row)
        shortlist_comment = _shortlisting_comment(row)
        background_parts = [
            one_line,
            str(row.get("background_summary") or "").strip(),
            f"Source tag: {source}",
            f"District: {district}" if district else "",
            f"Referred by: {row.get('referred_by')}" if row.get("referred_by") else "",
            f"Contact: {row.get('contact_number') or row.get('phone')}" if (row.get("contact_number") or row.get("phone")) else "",
            f"Shortlisting comment: {shortlist_comment}" if shortlist_comment else "",
        ]
        return {
            "ngo_name": row.get("ngo_name") or "Untitled NGO",
            "website": row.get("website") or "",
            "background": " | ".join([p for p in background_parts if p]),
            "lead_id": row.get("lead_id") or "",
            "source_mix": row.get("source_mix") or row.get("source_type") or source,
            "source_tag": source,
            "shortlisting_comment": shortlist_comment,
            "one_line_understanding": one_line,
            "contact_number": row.get("contact_number") or row.get("phone") or "",
            "referred_by": row.get("referred_by") or "",
            "batch_id": batch_id,
        }

    new_tasks = 0
    for idx, row in enumerate(new_rows):
        if assign_to_each:
            target_pms = pms
        else:
            target_pms = [pms[idx % len(pms)]]
        for pm in target_pms:
            data["pms"][pm].setdefault("tasks", []).append(build_task(row))
            assignments[pm] += 1
            new_tasks += 1
        row["ranking_status"] = "Sent to Ranking"
        row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # Persist updated ranking statuses back into Lead Pool without deleting or
    # resetting any existing lead metadata.
    by_id = {str(r.get("lead_id") or ""): r for r in all_rows}
    for row in rows:
        rid = str(row.get("lead_id") or "")
        if rid in by_id:
            by_id[rid].update(row)
    _write_lead_pool(region, list(by_id.values()))

    batch = {
        "batch_id": batch_id,
        "region": region,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows_requested": len(rows),
        "sent_count": len(new_rows),
        "new_leads": len(new_rows),
        "new_tasks": new_tasks,
        "blocked_not_approved_count": 0,
        "skipped_duplicate_count": len(skipped),
        "already_rated_count": len(skipped),
        "already_assigned_count": len(skipped),
        "not_sent_existing_count": len(skipped),
        "pms": pms,
        "assignments": assignments,
        "distribution": "assign_to_each" if assign_to_each else "split_evenly",
        "skipped": skipped[:100],
        "errors": [],
        "message": f"{new_tasks} PM task(s) created from {len(new_rows)} lead(s). {len(skipped)} already existed and were not sent again.",
    }
    data.setdefault("ranking_batches", []).insert(0, batch)
    data["ranking_batches"] = data["ranking_batches"][:100]
    data.setdefault("global_log", []).insert(0, {"summary": batch["message"], "at": batch["created_at"], "batch": batch})
    data["global_log"] = data["global_log"][:200]
    _write_workstream_payload(data)
    _workspace_log(region, "send_to_ranking", batch)
    _undo_snapshot_after("send_to_ranking", f"Sent to PM shortlisting: {new_tasks} task(s)", region, paths, undo_before)
    return _json(True, **batch, data=data)

def _rating_to_bucket(rating: int) -> str:
    if rating >= 5:
        return "Final Shortlist"
    if rating == 4:
        return "Strong Maybe"
    if rating == 3:
        return "Needs Follow-up"
    return "Reject"


@app.get("/ranking/compiled-review")
def ranking_compiled_review():
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=False)
    grouped = {str(i): [] for i in [5,4,3,2,1]}
    grouped["pending"] = []
    pm_counts: dict[str, dict] = {}
    for pm_name, pm in (data.get("pms") or {}).items():
        pm_counts.setdefault(pm_name, {"5":0,"4":0,"3":0,"2":0,"1":0,"total":0})
        tasks = pm.get("tasks") or []
        responses = pm.get("responses") or {}
        for idx, task in enumerate(tasks):
            resp = responses.get(str(idx)) or {}
            base_row = {
                "ngo_name": task.get("ngo_name") or task.get("name") or "",
                "reviewer": pm_name,
                "website": task.get("website") or "",
                "one_line_understanding": task.get("one_line_understanding") or _first_sentence(task.get("background") or ""),
                "background": task.get("background") or "",
                "source": task.get("source_mix") or "",
                "task_index": idx,
            }
            if isinstance(resp, dict) and resp.get("submitted"):
                try: rating = int(float(str(resp.get("rank") or resp.get("decision") or "0")))
                except Exception: rating = 0
                base_row.update({"rating": rating, "comment": resp.get("reason") or resp.get("ngo_description") or "", "submitted_at": resp.get("submitted_at") or ""})
                if str(rating) in grouped:
                    grouped[str(rating)].append(base_row)
                    pm_counts[pm_name][str(rating)] += 1
                    pm_counts[pm_name]["total"] += 1
            else:
                grouped["pending"].append(base_row)
    return _json(True, grouped_by_rating=grouped, pm_counts=pm_counts, pending_count=len(grouped["pending"]), total_rated=sum(v["total"] for v in pm_counts.values()), total_assigned=sum(len((pm.get("tasks") or [])) for pm in (data.get("pms") or {}).values()))


@app.get("/ranking/final-board")
def ranking_final_board():
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=False)
    out = []
    for row in rows:
        try: rating = int(float(str(row.get("rank") or row.get("decision") or "0")))
        except Exception: rating = 0
        out.append({
            "ngo_name": row.get("ngo_name"), "website": row.get("website"), "background": row.get("background"),
            "pm_reviewer": row.get("pm"), "pm_rating": rating, "pm_comment": row.get("reason"),
            "effective_bucket": _rating_to_bucket(rating), "is_override": False, "final_rank": "", "final_comment": "",
        })
    out.sort(key=lambda r: (r.get("pm_rating") or 0, r.get("ngo_name") or ""), reverse=True)
    return _json(True, count=len(out), rows=out)


@app.get("/ranking/final-summary")
def ranking_final_summary():
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=False)
    rating_counts = {str(i): 0 for i in [5,4,3,2,1]}
    bucket_counts: dict[str, int] = {}
    for row in rows:
        try: rating = int(float(str(row.get("rank") or row.get("decision") or "0")))
        except Exception: rating = 0
        if str(rating) in rating_counts:
            rating_counts[str(rating)] += 1
        bucket = _rating_to_bucket(rating)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    return _json(True, total_reviewed=len(rows), rating_distribution=rating_counts, final_buckets=bucket_counts, total_assigned=sum(len((pm.get("tasks") or [])) for pm in (data.get("pms") or {}).values()))



# -----------------------------------------------------------------------------
# Contact Tracker — outreach execution layer after Final Output
# -----------------------------------------------------------------------------

CONTACT_TRACKER_HEADERS = [
    "tracker_id", "region", "ngo_ref", "lead_id", "ngo_name", "district", "final_rank",
    "final_bucket", "website", "source_mix", "poc_name", "contact_number", "referred_by",
    "contact_status", "outreach_owner", "meeting_date", "meeting_time", "meeting_notes",
    "next_follow_up_date", "tracker_comment", "pm_reviewer", "pm_rating", "pm_comment",
    "background", "one_line_understanding",
    # Contact Supporter fields. Stored as pipe-separated values where there can be many.
    "all_emails", "selected_to_emails", "selected_cc_emails", "all_phones", "selected_phone",
    "linkedin_org_urls", "linkedin_people_urls", "contact_form_urls", "contact_source_urls",
    "best_contact_route", "contact_confidence", "contact_confidence_reason",
    "email_subject", "email_body", "linkedin_message", "website_detail", "reviewer_line",
    "outreach_locked", "manual_review_needed", "query_mode", "queries_used", "contact_generated_at",
    "outreach_template_name",
    "sent_from_final_at", "created_at", "updated_at",
]

CONTACT_STATUSES = {
    "not_started", "contacted", "connected", "not_connected", "meeting_scheduled",
    "meeting_done", "follow_up_needed", "not_interested", "on_hold",
}

_CONTACT_TRACKER_LOCK = threading.RLock()


def _contact_tracker_path(region: str) -> Path:
    return _workspace_dir(region or "Karnataka") / "contact_tracker.csv"


def _read_contact_tracker(region: str) -> list[dict]:
    path = _contact_tracker_path(region)
    if not path.exists():
        return []
    with _CONTACT_TRACKER_LOCK:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))


def _write_contact_tracker(region: str, rows: list[dict]) -> None:
    path = _contact_tracker_path(region)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CONTACT_TRACKER_LOCK:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CONTACT_TRACKER_HEADERS, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    clean = {h: row.get(h, "") for h in CONTACT_TRACKER_HEADERS}
                    writer.writerow(_safe_csv_row(clean))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass


def _bucket_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    aliases = {
        "final_shortlist": "final_shortlist", "shortlist": "final_shortlist",
        "strong_maybe": "strong_maybe", "maybe": "strong_maybe",
        "needs_follow_up": "needs_follow_up", "follow_up": "needs_follow_up",
        "hold": "hold", "reject": "reject", "rejected": "reject",
    }
    return aliases.get(text, text)


def _bucket_label(value: str) -> str:
    key = _bucket_key(value)
    return {
        "final_shortlist": "Final Shortlist",
        "strong_maybe": "Strong Maybe",
        "needs_follow_up": "Needs Follow-up",
        "hold": "Hold",
        "reject": "Reject",
    }.get(key, str(value or ""))


def _row_ngo_ref(row: dict) -> str:
    return str(row.get("ngo_ref") or row.get("lead_id") or _normalise_lead_name(row.get("ngo_name") or "") or row.get("ngo_name") or "").strip()


def _domain_from_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if raw.startswith(("http://", "https://")) else "https://" + raw)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _final_rows_for_tracker(region: str = "Karnataka") -> list[dict]:
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=False)
    lead_rows = _read_lead_pool(region)
    by_lead_id = {str(r.get("lead_id") or ""): r for r in lead_rows if r.get("lead_id")}
    by_name = {_normalise_lead_name(r.get("ngo_name") or ""): r for r in lead_rows if _normalise_lead_name(r.get("ngo_name") or "")}
    out = []
    rank_by_bucket: dict[str, int] = {}
    for row in rows:
        try:
            rating = int(float(str(row.get("rank") or row.get("decision") or "0")))
        except Exception:
            rating = 0
        if not rating:
            continue
        lead = by_lead_id.get(str(row.get("lead_id") or "")) or by_name.get(_normalise_lead_name(row.get("ngo_name") or "")) or {}
        bucket = _rating_to_bucket(rating)
        bkey = _bucket_key(bucket)
        rank_by_bucket[bkey] = rank_by_bucket.get(bkey, 0) + 1
        out.append({
            "ngo_ref": _row_ngo_ref(row),
            "lead_id": row.get("lead_id") or lead.get("lead_id") or "",
            "ngo_name": row.get("ngo_name") or lead.get("ngo_name") or "",
            "district": lead.get("district") or "",
            "final_rank": str(rank_by_bucket[bkey]),
            "final_bucket": bucket,
            "website": row.get("website") or lead.get("website") or "",
            "source_mix": row.get("source_mix") or lead.get("source_mix") or lead.get("source_type") or row.get("referral_source") or "",
            "contact_number": row.get("contact_number") or lead.get("contact_number") or lead.get("phone") or "",
            "referred_by": row.get("referral_poc") or lead.get("referred_by") or "",
            "pm_reviewer": row.get("pm") or "",
            "pm_rating": str(rating),
            "pm_comment": row.get("reason") or row.get("ngo_description") or "",
            "background": row.get("background") or lead.get("background_summary") or "",
            "one_line_understanding": row.get("one_line_understanding") or lead.get("one_line_understanding") or _first_sentence(row.get("background") or lead.get("background_summary") or ""),
        })
    out.sort(key=lambda r: (_bucket_key(r.get("final_bucket")) != "final_shortlist", -(int(r.get("pm_rating") or 0)), int(r.get("final_rank") or 999999)))
    return out


def _contact_tracker_key(row: dict) -> str:
    ref = str(row.get("ngo_ref") or "").strip()
    if ref:
        return f"ref:{ref}"
    domain = _domain_from_url(row.get("website") or "")
    if domain:
        return f"domain:{domain}"
    return f"name:{_normalise_lead_name(row.get('ngo_name') or '')}|district:{str(row.get('district') or '').strip().lower()}"




def _split_multi_value(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_parts = [str(x or "") for x in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        raw_parts = []
        if text[:1] in "[{":
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    raw_parts = [str(x or "") for x in data]
                elif isinstance(data, dict):
                    raw_parts = [str(x or "") for x in data.values()]
            except Exception:
                raw_parts = []
        if not raw_parts:
            raw_parts = re.split(r"[|;\n]+", text)
    out: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        item = re.sub(r"\s+", " ", str(part or "").strip().strip(","))
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _join_multi_value(values) -> str:
    return " | ".join(_split_multi_value(values))


_EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
_PHONE_RE = re.compile(r"(?:(?:\+91|0)?[\s\-.]?[6-9]\d{2}[\s\-.]?\d{3}[\s\-.]?\d{4}|0\d{2,4}[\s\-.]?\d{6,8})")


def _extract_emails(text: str) -> list[str]:
    bad_ext = {"png", "jpg", "jpeg", "gif", "webp", "svg", "pdf", "css", "js"}
    out: list[str] = []
    seen: set[str] = set()
    for m in _EMAIL_RE.findall(str(text or "")):
        email = m.strip().strip(".,;:()[]{}<>\"'").lower()
        if not email or "example." in email or email.endswith("@domain.com"):
            continue
        tld = email.rsplit(".", 1)[-1].lower()
        if tld in bad_ext:
            continue
        if any(x in email for x in ["sentry.", "wixpress", "wordpress", "schema.org"]):
            continue
        if email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _clean_phone(value: str) -> str:
    raw = str(value or "")
    digits = re.sub(r"\D+", "", raw)
    if digits.startswith("91") and len(digits) == 12:
        return "+91 " + digits[2:7] + " " + digits[7:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91 " + digits[:5] + " " + digits[5:]
    if len(digits) >= 8:
        return raw.strip().strip(".,;:()[]{}")
    return ""


def _extract_phones(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _PHONE_RE.findall(str(text or "")):
        phone = _clean_phone(m)
        if not phone:
            continue
        key = re.sub(r"\D+", "", phone)
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        out.append(phone)
    return out


def _contact_queries(ngo_name: str, region: str, mode: str) -> list[str]:
    name = str(ngo_name or "").strip()
    reg = str(region or "Karnataka").strip() or "Karnataka"
    mode = str(mode or "balanced").lower()
    cheap = [
        f'"{name}" {reg} NGO official website contact email phone',
        f'"{name}" founder director trustee LinkedIn {reg}',
    ]
    balanced = [
        f'"{name}" {reg} NGO official website',
        f'"{name}" contact email phone',
        f'"{name}" founder director trustee secretary',
        f'site:linkedin.com "{name}" {reg}',
    ]
    deep = [
        f'"{name}" {reg} NGO official website',
        f'"{name}" contact email phone',
        f'"{name}" annual report email phone',
        f'"{name}" founder director trustee secretary',
        f'site:linkedin.com/company "{name}"',
        f'site:linkedin.com/in "{name}" founder director trustee',
    ]
    if mode == "cheap":
        return cheap
    if mode == "deep":
        return deep
    return balanced


def _is_low_value_contact_url(url: str) -> bool:
    u = str(url or "").lower()
    bad = [
        "ngodarpan", "darpan", "csrbox", "justdial", "sulekha", "zaubacorp", "tofler",
        "facebook.com", "instagram.com", "twitter.com", "x.com/", "youtube.com", "youtu.be",
        "guidestar", "give.do", "giveindia", "impactguru", "wikipedia.org", "wikimedia.org",
    ]
    return any(x in u for x in bad)


def _is_linkedin_org(url: str) -> bool:
    u = str(url or "").lower()
    return "linkedin.com/company" in u or "linkedin.com/school" in u


def _is_linkedin_person(url: str) -> bool:
    return "linkedin.com/in/" in str(url or "").lower()


def _rank_emails(emails: list[str]) -> list[str]:
    def score(email: str) -> tuple[int, str]:
        e = email.lower()
        local = e.split("@", 1)[0]
        s = 50
        if any(x in local for x in ["director", "founder", "trustee", "secretary", "ceo", "head", "program", "programme"]):
            s -= 30
        if any(x in local for x in ["info", "contact", "admin", "office", "hello"]):
            s -= 18
        if any(x in local for x in ["noreply", "no-reply", "support", "careers", "hr"]):
            s += 50
        if any(e.endswith("@" + d) for d in ["gmail.com", "yahoo.com", "rediffmail.com", "hotmail.com", "outlook.com"]):
            s += 6
        return (s, e)
    return sorted(_split_multi_value(emails), key=score)


def _fill_contact_template(template: str, variables: dict) -> str:
    text = str(template or "")
    for key, value in variables.items():
        text = text.replace("[" + key + "]", str(value or ""))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _default_contact_templates(settings: dict | None = None) -> dict:
    settings = settings or {}
    return {
        "sender_name": settings.get("sender_name") or "Milan",
        "email_subject_template": settings.get("email_subject_template") or "Quick Feeding India conversation",
        "email_body_template": settings.get("email_body_template") or (
            "Hi [contact_name_or_team],\n\n"
            "I’m [sender_name] from Feeding India, by Eternal Foundation.\n\n"
            "Your organization stood out in our Karnataka review. We went through your public work, especially [website_detail]. [reviewer_line]\n\n"
            "Feeding India’s Daily Feeding Program supports child-focused institutions through daily nutritious meals or ration support.\n\n"
            "Could we speak for 5 minutes to understand your work and see if there may be a fit? You can also reply here with your current food/ration support needs.\n\n"
            "Regards,\n[sender_name]"
        ),
        "linkedin_template": settings.get("linkedin_template") or (
            "Hi [contact_name_or_team], I’m [sender_name] from Feeding India, by Eternal Foundation. "
            "We came across [ngo_name] during our Karnataka review and wanted to understand your work better. "
            "Would you be open to a quick 5-minute conversation on possible food/ration support?"
        ),
        "feeding_india_website": settings.get("feeding_india_website") or "",
        "annual_report_link": settings.get("annual_report_link") or "",
        "social_links": settings.get("social_links") or "",
        "max_first_wave_emails": int(settings.get("max_first_wave_emails") or 3),
        "template_name": settings.get("template_name") or "default",
    }


def _fallback_contact_copy(row: dict, evidence: dict, settings: dict | None = None) -> dict:
    cfg = _default_contact_templates(settings)
    reviewer_raw = str(row.get("pm_comment") or row.get("tracker_comment") or row.get("one_line_understanding") or "").strip()
    reviewer_line = ""
    if reviewer_raw:
        reviewer_line = _first_sentence(reviewer_raw, 140)
        if reviewer_line and not reviewer_line.lower().startswith("our"):
            reviewer_line = "Our reviewer noted: " + reviewer_line
    detail = evidence.get("website_detail") or _first_sentence(row.get("one_line_understanding") or row.get("background") or "", 130) or "your work with children"
    variables = {
        "ngo_name": row.get("ngo_name") or "your organization",
        "contact_name_or_team": row.get("poc_name") or "Team",
        "sender_name": cfg["sender_name"],
        "website_detail": detail,
        "reviewer_line": reviewer_line,
        "category": row.get("final_bucket") or "",
        "rating": row.get("pm_rating") or "",
        "program_name": "Daily Feeding Program",
        "feeding_india_website": cfg["feeding_india_website"],
        "annual_report_link": cfg["annual_report_link"],
        "social_links": cfg["social_links"],
    }
    return {
        "website_detail": detail,
        "reviewer_line": reviewer_line,
        "email_subject": _fill_contact_template(cfg["email_subject_template"], variables),
        "email_body": _fill_contact_template(cfg["email_body_template"], variables),
        "linkedin_message": _fill_contact_template(cfg["linkedin_template"], variables),
        "outreach_template_name": cfg["template_name"],
        "source": "fallback",
    }


def _call_contact_haiku(row: dict, evidence: dict, settings: dict | None = None) -> dict:
    cfg = _default_contact_templates(settings)
    fallback = _fallback_contact_copy(row, evidence, settings)
    if _get_anthropic() is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback
    model = os.environ.get("CONTACT_AI_MODEL") or os.environ.get("HAIKU_MODEL") or os.environ.get("STORY_MODEL") or "claude-haiku-4-5-20251001"
    prompt = f"""
You generate extremely brief outreach copy for Feeding India's Daily Feeding Program.
Use ONLY the provided reviewer note, website/search snippets, and template variables. Do not invent facts. No fluff. Avoid: amazing, incredible, synergy, best.
Return ONLY JSON with: website_detail, reviewer_line, email_subject, email_body, linkedin_message.

Rules:
- website_detail: max 16 words, factual, lower-case phrase is okay.
- reviewer_line: max 16 words. Use only if positive and useful; else empty.
- email_body: fill the user's template exactly in structure. Keep it short.
- linkedin_message: max 55 words.

Template variables:
{json.dumps({
    "sender_name": cfg["sender_name"],
    "ngo_name": row.get("ngo_name") or "",
    "contact_name_or_team": row.get("poc_name") or "Team",
    "email_subject_template": cfg["email_subject_template"],
    "email_body_template": cfg["email_body_template"],
    "linkedin_template": cfg["linkedin_template"],
    "feeding_india_website": cfg["feeding_india_website"],
    "annual_report_link": cfg["annual_report_link"],
    "social_links": cfg["social_links"],
}, ensure_ascii=False)}

Reviewer/context:
{json.dumps({
    "pm_rating": row.get("pm_rating"),
    "pm_comment": row.get("pm_comment"),
    "background": row.get("background"),
    "one_line_understanding": row.get("one_line_understanding"),
    "tracker_comment": row.get("tracker_comment"),
}, ensure_ascii=False)}

Search evidence:
{json.dumps(evidence, ensure_ascii=False)[:5000]}
""".strip()
    try:
        client = _get_anthropic().Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model=model,
            max_tokens=int(os.environ.get("CONTACT_AI_MAX_TOKENS", "700")),
            temperature=0.05,
            messages=[{"role": "user", "content": prompt}],
        )
        content = _json_text_from_msg(msg)
        try:
            data = _clean_json_from_text(content)
        except Exception:
            data = json.loads(content)
        if isinstance(data, dict):
            out = dict(fallback)
            for k in ["website_detail", "reviewer_line", "email_subject", "email_body", "linkedin_message"]:
                if str(data.get(k) or "").strip():
                    out[k] = str(data.get(k) or "").strip()
            out["outreach_template_name"] = cfg["template_name"]
            out["source"] = "haiku"
            return out
    except Exception as e:
        fallback["ai_error"] = str(e)[:240]
    return fallback


def _discover_public_contacts(row: dict, region: str, query_mode: str) -> dict:
    if not _has_serper_keys():
        raise RuntimeError("SERPER_API_KEY or SERPER_API_KEYS must be set")
    ngo_name = str(row.get("ngo_name") or "").strip()
    if not ngo_name:
        raise ValueError("NGO name is missing")
    queries = _contact_queries(ngo_name, region, query_mode)
    emails: list[str] = []
    phones: list[str] = []
    linkedin_org: list[str] = []
    linkedin_people: list[str] = []
    contact_forms: list[str] = []
    source_urls: list[str] = []
    snippets: list[dict] = []
    fetch_candidates: list[str] = []
    for q in queries:
        data = _serper_post({"q": q, "num": 10, "gl": "in"}, timeout=25)
        for item in (data.get("organic") or [])[:10]:
            link = str(item.get("link") or "").strip()
            title = str(item.get("title") or "")
            snippet = str(item.get("snippet") or "")
            hay = "\n".join([title, snippet, link])
            emails.extend(_extract_emails(hay))
            phones.extend(_extract_phones(hay))
            if link:
                if _is_linkedin_org(link):
                    linkedin_org.append(link); source_urls.append(link)
                elif _is_linkedin_person(link):
                    linkedin_people.append(link); source_urls.append(link)
                elif "contact" in link.lower() and not _is_low_value_contact_url(link):
                    contact_forms.append(link); source_urls.append(link)
                if not _is_low_value_contact_url(link) and link.startswith(("http://", "https://")):
                    fetch_candidates.append(link)
            if title or snippet:
                snippets.append({"title": title[:180], "snippet": snippet[:320], "url": link[:240]})
    # Fetch only a few likely official/contact pages; Serper is for discovery, fetch is for extracting public contacts.
    fetched = 0
    for url in _split_multi_value(fetch_candidates)[:6]:
        if fetched >= 4:
            break
        try:
            final_url, html = _safe_fetch_text(url, timeout=8, max_bytes=800_000)
            fetched += 1
            soup = _make_soup(html)
            text = soup.get_text(" ", strip=True)
            merged = "\n".join([final_url, text[:120000], " ".join(a.get("href") or "" for a in soup.find_all("a", href=True)[:300])])
            found_e = _extract_emails(merged)
            found_p = _extract_phones(merged)
            if found_e or found_p or "contact" in final_url.lower():
                source_urls.append(final_url)
            emails.extend(found_e)
            phones.extend(found_p)
            for a in soup.find_all("a", href=True)[:300]:
                href = urljoin(final_url, a.get("href") or "")
                if _is_linkedin_org(href):
                    linkedin_org.append(href); source_urls.append(href)
                elif _is_linkedin_person(href):
                    linkedin_people.append(href); source_urls.append(href)
                elif "contact" in href.lower() and href.startswith(("http://", "https://")) and not _is_low_value_contact_url(href):
                    contact_forms.append(href)
            if not row.get("website") and final_url and not _is_low_value_contact_url(final_url):
                row["website"] = final_url
        except Exception:
            continue
    ranked_emails = _rank_emails(emails)
    all_emails = _split_multi_value(ranked_emails)
    all_phones = _split_multi_value(phones)
    org_urls = _split_multi_value(linkedin_org)
    people_urls = _split_multi_value(linkedin_people)
    form_urls = _split_multi_value(contact_forms)
    source_urls = _split_multi_value(source_urls)
    if all_emails and (all_phones or org_urls or people_urls):
        conf, reason = "High", "Email plus another public contact route found."
    elif all_emails:
        conf, reason = "High", "At least one public email found."
    elif org_urls or people_urls or all_phones or form_urls:
        conf, reason = "Medium", "No email, but another public contact route found."
    else:
        conf, reason = "Low", "No usable public contact route found."
    route_parts = []
    if all_emails: route_parts.append("email")
    if all_phones: route_parts.append("phone")
    if org_urls or people_urls: route_parts.append("LinkedIn")
    if form_urls: route_parts.append("contact form")
    return {
        "queries": queries,
        "queries_used": len(queries),
        "all_emails": all_emails,
        "all_phones": all_phones,
        "linkedin_org_urls": org_urls,
        "linkedin_people_urls": people_urls,
        "contact_form_urls": form_urls,
        "contact_source_urls": source_urls,
        "best_contact_route": " + ".join(route_parts) if route_parts else "manual search needed",
        "contact_confidence": conf,
        "contact_confidence_reason": reason,
        "website_detail": _first_sentence(" ".join([x.get("snippet") or x.get("title") or "" for x in snippets[:4]]), 130),
        "search_snippets": snippets[:12],
    }


def _generate_contact_supporter_row(row: dict, region: str, query_mode: str, settings: dict | None = None) -> dict:
    evidence = _discover_public_contacts(row, region, query_mode)
    cfg = _default_contact_templates(settings)
    max_first = max(1, min(int(cfg.get("max_first_wave_emails") or 3), 8))
    all_emails = _split_multi_value(evidence.get("all_emails"))
    selected_to = all_emails[:1]
    selected_cc = all_emails[1:max_first]
    copy = _call_contact_haiku(row, evidence, settings)
    manual_review_needed = "yes" if evidence.get("contact_confidence") == "Low" else ""
    if not all_emails and not (evidence.get("linkedin_org_urls") or evidence.get("linkedin_people_urls")):
        manual_review_needed = "yes"
    return {
        "all_emails": _join_multi_value(all_emails),
        "selected_to_emails": _join_multi_value(selected_to),
        "selected_cc_emails": _join_multi_value(selected_cc),
        "all_phones": _join_multi_value(evidence.get("all_phones") or []),
        "selected_phone": _split_multi_value(evidence.get("all_phones") or [""])[0] if _split_multi_value(evidence.get("all_phones") or []) else "",
        "linkedin_org_urls": _join_multi_value(evidence.get("linkedin_org_urls") or []),
        "linkedin_people_urls": _join_multi_value(evidence.get("linkedin_people_urls") or []),
        "contact_form_urls": _join_multi_value(evidence.get("contact_form_urls") or []),
        "contact_source_urls": _join_multi_value(evidence.get("contact_source_urls") or []),
        "best_contact_route": evidence.get("best_contact_route") or "manual search needed",
        "contact_confidence": evidence.get("contact_confidence") or "Low",
        "contact_confidence_reason": evidence.get("contact_confidence_reason") or "",
        "email_subject": copy.get("email_subject") or "Quick Feeding India conversation",
        "email_body": copy.get("email_body") or "",
        "linkedin_message": copy.get("linkedin_message") or "",
        "website_detail": copy.get("website_detail") or evidence.get("website_detail") or "",
        "reviewer_line": copy.get("reviewer_line") or "",
        "manual_review_needed": manual_review_needed,
        "query_mode": query_mode,
        "queries_used": str(evidence.get("queries_used") or 0),
        "contact_generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "outreach_template_name": copy.get("outreach_template_name") or cfg.get("template_name") or "default",
    }


def _contact_summary(rows: list[dict]) -> dict:
    status_counts = {s: 0 for s in sorted(CONTACT_STATUSES)}
    by_bucket: dict[str, int] = {}
    by_source: dict[str, int] = {}
    meetings_this_week = 0
    overdue_followups = 0
    today = time.strftime("%Y-%m-%d")
    for row in rows:
        status = str(row.get("contact_status") or "not_started").strip().lower() or "not_started"
        status_counts[status] = status_counts.get(status, 0) + 1
        bucket = row.get("final_bucket") or "Unbucketed"
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        source = row.get("source_mix") or "Unknown"
        by_source[source] = by_source.get(source, 0) + 1
        mtg = str(row.get("meeting_date") or "")[:10]
        if mtg >= today and mtg <= time.strftime("%Y-%m-%d", time.localtime(time.time() + 7*86400)):
            meetings_this_week += 1
        nf = str(row.get("next_follow_up_date") or "")[:10]
        if nf and nf < today and status not in {"meeting_done", "not_interested", "on_hold"}:
            overdue_followups += 1
    emails_found = sum(1 for r in rows if _split_multi_value(r.get("all_emails") or r.get("selected_to_emails") or ""))
    linkedin_found = sum(1 for r in rows if _split_multi_value(r.get("linkedin_org_urls") or "") or _split_multi_value(r.get("linkedin_people_urls") or ""))
    ready_to_email = sum(1 for r in rows if _split_multi_value(r.get("selected_to_emails") or r.get("all_emails") or "") and str(r.get("manual_review_needed") or "").lower() not in {"1", "true", "yes"})
    needs_review = sum(1 for r in rows if str(r.get("manual_review_needed") or "").lower() in {"1", "true", "yes"})
    locked_rows = sum(1 for r in rows if str(r.get("outreach_locked") or "").lower() in {"1", "true", "yes"})
    return {
        "total_in_tracker": len(rows),
        "emails_found_count": emails_found,
        "linkedin_found_count": linkedin_found,
        "ready_to_email_count": ready_to_email,
        "needs_review_count": needs_review,
        "locked_rows_count": locked_rows,
        "status_counts": status_counts,
        "not_started_count": status_counts.get("not_started", 0),
        "contacted_count": status_counts.get("contacted", 0),
        "connected_count": status_counts.get("connected", 0),
        "meeting_scheduled_count": status_counts.get("meeting_scheduled", 0),
        "meeting_done_count": status_counts.get("meeting_done", 0),
        "follow_up_needed_count": status_counts.get("follow_up_needed", 0),
        "not_interested_count": status_counts.get("not_interested", 0),
        "on_hold_count": status_counts.get("on_hold", 0),
        "by_final_bucket": by_bucket,
        "by_source_mix": by_source,
        "meetings_this_week": meetings_this_week,
        "overdue_followups": overdue_followups,
    }


@app.post("/ranking/final/send-to-contact-tracker")
def final_send_to_contact_tracker(payload: dict | None = None):
    payload = payload or {}
    region = str(payload.get("region") or "Karnataka")
    requested_refs = {str(x).strip() for x in (payload.get("ngo_refs") or []) if str(x).strip()}
    requested_buckets = {_bucket_key(x) for x in (payload.get("buckets") or []) if str(x).strip()}
    paths = [_contact_tracker_path(region)]
    undo_before = _undo_snapshot_before(paths)
    if not requested_refs and not requested_buckets:
        requested_buckets = {"final_shortlist"}
    final_rows = _final_rows_for_tracker(region)
    chosen = []
    for row in final_rows:
        if requested_refs and _row_ngo_ref(row) in requested_refs:
            chosen.append(row)
        elif requested_buckets and _bucket_key(row.get("final_bucket")) in requested_buckets:
            chosen.append(row)
    if not chosen:
        return _json(False, status_code=400, error="No matching final-output rows to send")

    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    existing = _read_contact_tracker(region)
    by_key = {_contact_tracker_key(r): r for r in existing}
    skipped = []
    sent = 0
    for row in chosen:
        key = _contact_tracker_key(row)
        if key in by_key:
            existing_row = by_key[key]
            for field in ("contact_number", "referred_by", "source_mix", "website"):
                if not existing_row.get(field) and row.get(field):
                    existing_row[field] = row.get(field)
            existing_row["updated_at"] = now_s
            skipped.append({"ngo_name": row.get("ngo_name"), "reason": "already_in_contact_tracker"})
            continue
        tracker_row = {h: "" for h in CONTACT_TRACKER_HEADERS}
        tracker_row.update(row)
        tracker_row.update({
            "tracker_id": uuid.uuid4().hex[:12],
            "region": region,
            "contact_status": "not_started",
            "sent_from_final_at": now_s,
            "created_at": now_s,
            "updated_at": now_s,
        })
        existing.append(tracker_row)
        by_key[key] = tracker_row
        sent += 1
    _write_contact_tracker(region, existing)
    _workspace_log(region, "sent_to_contact_tracker", {"sent_count": sent, "skipped_existing_count": len(skipped), "buckets": sorted(requested_buckets), "ngo_refs": sorted(requested_refs)[:50]})
    _undo_snapshot_after("final_send_to_contact_tracker", f"Final Ranking sent to tracker: {sent} row(s)", region, paths, undo_before)
    return _json(True, sent_count=sent, skipped_existing_count=len(skipped), errors=[], skipped=skipped[:100], summary=_contact_summary(existing))


@app.get("/contact-tracker")
def contact_tracker(region: str = "Karnataka"):
    rows = _read_contact_tracker(region)
    return _json(True, region=region, count=len(rows), rows=rows, summary=_contact_summary(rows))




@app.post("/contact-tracker/generate-outreach")
def contact_tracker_generate_outreach(payload: dict | None = None):
    payload = payload or {}
    region = str(payload.get("region") or "Karnataka")
    query_mode = str(payload.get("query_mode") or "balanced").lower()
    if query_mode not in {"cheap", "balanced", "deep"}:
        query_mode = "balanced"
    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    force = bool(payload.get("force"))
    all_rows = bool(payload.get("all"))
    tracker_ids = {str(x).strip() for x in (payload.get("tracker_ids") or []) if str(x).strip()}
    one_id = str(payload.get("tracker_id") or "").strip()
    if one_id:
        tracker_ids.add(one_id)
    if not all_rows and not tracker_ids:
        return _json(False, status_code=400, error="tracker_id, tracker_ids, or all=true is required")
    if not _has_serper_keys():
        return _json(False, status_code=500, error="SERPER_API_KEY or SERPER_API_KEYS must be set before contact discovery can run")

    max_rows = int(payload.get("limit") or os.environ.get("CONTACT_GENERATE_MAX_ROWS", "25") or 25)
    max_rows = max(1, min(max_rows, 100))
    paths = [_contact_tracker_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_contact_tracker(region)
    generated = 0
    skipped: list[dict] = []
    errors: list[dict] = []
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        if not all_rows and str(row.get("tracker_id") or "") not in tracker_ids:
            continue
        if generated >= max_rows:
            skipped.append({"ngo_name": row.get("ngo_name"), "reason": "limit_reached"})
            continue
        if str(row.get("outreach_locked") or "").lower() in {"1", "true", "yes"} and not force:
            skipped.append({"ngo_name": row.get("ngo_name"), "reason": "locked"})
            continue
        try:
            patch = _generate_contact_supporter_row(row, region, query_mode, settings)
            for k, v in patch.items():
                row[k] = v
            row["updated_at"] = now_s
            generated += 1
        except Exception as e:
            row["manual_review_needed"] = "yes"
            row["contact_confidence"] = row.get("contact_confidence") or "Low"
            row["contact_confidence_reason"] = f"Generation failed: {str(e)[:220]}"
            row["updated_at"] = now_s
            errors.append({"ngo_name": row.get("ngo_name"), "error": str(e)[:300]})
    if generated or errors:
        _write_contact_tracker(region, rows)
        _workspace_log(region, "contact_supporter_generated", {"generated_count": generated, "errors": len(errors), "query_mode": query_mode})
        _undo_snapshot_after("contact_tracker_generate_outreach", f"Contact Supporter generated: {generated} row(s)", region, paths, undo_before)
    return _json(True, generated_count=generated, skipped=skipped[:100], errors=errors[:100], summary=_contact_summary(rows), rows=rows)


@app.post("/contact-tracker/update")
def contact_tracker_update(payload: dict | None = None):
    payload = payload or {}
    region = str(payload.get("region") or "Karnataka")
    paths = [_contact_tracker_path(region)]
    undo_before = _undo_snapshot_before(paths)
    tracker_id = str(payload.get("tracker_id") or "").strip()
    ngo_ref = str(payload.get("ngo_ref") or "").strip()
    if not tracker_id and not ngo_ref:
        return _json(False, status_code=400, error="tracker_id or ngo_ref is required")
    rows = _read_contact_tracker(region)
    allowed = {
        "poc_name", "contact_number", "contact_status", "outreach_owner", "meeting_date",
        "meeting_time", "meeting_notes", "next_follow_up_date", "tracker_comment",
        "all_emails", "selected_to_emails", "selected_cc_emails", "all_phones", "selected_phone",
        "linkedin_org_urls", "linkedin_people_urls", "contact_form_urls", "contact_source_urls",
        "best_contact_route", "contact_confidence", "contact_confidence_reason",
        "email_subject", "email_body", "linkedin_message", "website_detail", "reviewer_line",
        "outreach_locked", "manual_review_needed", "query_mode", "queries_used", "contact_generated_at",
        "outreach_template_name",
    }
    updated = None
    for row in rows:
        if (tracker_id and row.get("tracker_id") == tracker_id) or (ngo_ref and row.get("ngo_ref") == ngo_ref):
            for field in allowed:
                if field in payload:
                    value = str(payload.get(field) or "")
                    if field == "contact_status":
                        value = value.strip().lower() or "not_started"
                        if value not in CONTACT_STATUSES:
                            return _json(False, status_code=400, error="Invalid contact_status")
                    row[field] = value
            row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            updated = row
            break
    if updated is None:
        return _json(False, status_code=404, error="Tracker row not found")
    _write_contact_tracker(region, rows)
    _workspace_log(region, "tracker_status_updated", {"tracker_id": updated.get("tracker_id"), "ngo_name": updated.get("ngo_name"), "status": updated.get("contact_status")})
    _undo_snapshot_after("contact_tracker_update", "Contact Tracker row updated", region, paths, undo_before)
    return _json(True, row=updated, summary=_contact_summary(rows))


@app.post("/contact-tracker/remove")
def contact_tracker_remove(payload: dict | None = None):
    payload = payload or {}
    region = str(payload.get("region") or "Karnataka")
    paths = [_contact_tracker_path(region)]
    undo_before = _undo_snapshot_before(paths)
    ids = {str(x).strip() for x in (payload.get("tracker_ids") or []) if str(x).strip()}
    one_id = str(payload.get("tracker_id") or "").strip()
    if one_id:
        ids.add(one_id)
    if not ids:
        return _json(False, status_code=400, error="tracker_id is required")
    rows = _read_contact_tracker(region)
    kept = [r for r in rows if str(r.get("tracker_id") or "") not in ids]
    removed = len(rows) - len(kept)
    _write_contact_tracker(region, kept)
    _workspace_log(region, "tracker_row_removed", {"removed_count": removed})
    _undo_snapshot_after("contact_tracker_remove", f"Contact Tracker remove: {removed} row(s)", region, paths, undo_before)
    return _json(True, removed_count=removed, count=len(kept), summary=_contact_summary(kept))




@app.post("/contact-tracker/import-csv")
async def contact_tracker_import_csv(region: str = "Karnataka", file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw or b"") > int(os.environ.get("CONTACT_IMPORT_MAX_BYTES", "6000000")):
        return _json(False, status_code=400, error="CSV is too large")
    text = raw.decode("utf-8-sig", errors="replace")
    incoming = list(csv.DictReader(text.splitlines()))
    if not incoming:
        return _json(False, status_code=400, error="No rows found in CSV")
    paths = [_contact_tracker_path(region)]
    undo_before = _undo_snapshot_before(paths)
    rows = _read_contact_tracker(region)
    by_id = {str(r.get("tracker_id") or "").strip(): r for r in rows if str(r.get("tracker_id") or "").strip()}
    by_name = {_normalise_lead_name(r.get("ngo_name") or ""): r for r in rows if _normalise_lead_name(r.get("ngo_name") or "")}
    aliases = {re.sub(r"[^a-z0-9]+", "_", h.lower()).strip("_"): h for h in CONTACT_TRACKER_HEADERS}
    aliases.update({
        # Minimum initial upload headers
        "ngo": "ngo_name", "ngo_name": "ngo_name", "ngo_name_input": "ngo_name", "name": "ngo_name", "organization": "ngo_name", "organisation": "ngo_name",
        "category": "final_bucket", "bucket": "final_bucket", "final_category": "final_bucket", "outreach_category": "final_bucket",
        "rating": "pm_rating", "review_rating": "pm_rating", "pm_rating": "pm_rating",
        "pm": "pm_reviewer", "reviewer": "pm_reviewer", "pm_reviewer": "pm_reviewer", "reviewed_by": "pm_reviewer",
        "reviewer_note": "pm_comment", "review_note": "pm_comment", "pm_note": "pm_comment", "pm_comment": "pm_comment", "comments": "pm_comment",
        "website_url": "website", "official_website": "website",
        "poc": "poc_name", "poc_name": "poc_name", "contact_name": "poc_name",
        "contact_number": "contact_number", "phone_number": "contact_number",
        "owner": "outreach_owner", "outreach_owner": "outreach_owner",
        # Edited/exported tracker headers
        "best_email": "selected_to_emails", "primary_email": "selected_to_emails", "to_emails": "selected_to_emails",
        "cc_emails": "selected_cc_emails", "backup_email": "selected_cc_emails",
        "emails_found": "all_emails", "all_emails_found": "all_emails",
        "phone": "selected_phone", "phones_found": "all_phones",
        "linkedin": "linkedin_org_urls", "linkedin_org": "linkedin_org_urls", "linkedin_people": "linkedin_people_urls",
        "subject": "email_subject", "body": "email_body", "email": "email_body",
        "linkedin_text": "linkedin_message", "linkedin_message": "linkedin_message",
        "confidence": "contact_confidence", "status": "contact_status",
    })
    updated = 0
    appended = 0
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    allowed = set(CONTACT_TRACKER_HEADERS) - {"created_at"}
    for raw_row in incoming:
        mapped = {}
        for k, v in (raw_row or {}).items():
            kk = re.sub(r"[^a-z0-9]+", "_", str(k or "").lower()).strip("_")
            field = aliases.get(kk)
            if field in allowed:
                mapped[field] = str(v or "")
        target = None
        tid = str(mapped.get("tracker_id") or "").strip()
        if tid and tid in by_id:
            target = by_id[tid]
        if target is None:
            nm = _normalise_lead_name(mapped.get("ngo_name") or raw_row.get("NGO Name") or raw_row.get("NGO") or "")
            if nm and nm in by_name:
                target = by_name[nm]
        if target is None:
            target = {h: "" for h in CONTACT_TRACKER_HEADERS}
            target.update(mapped)
            target["tracker_id"] = target.get("tracker_id") or uuid.uuid4().hex[:12]
            target["region"] = region
            target["contact_status"] = target.get("contact_status") or "not_started"
            target["created_at"] = now_s
            rows.append(target)
            by_id[target["tracker_id"]] = target
            if _normalise_lead_name(target.get("ngo_name") or ""):
                by_name[_normalise_lead_name(target.get("ngo_name") or "")] = target
            appended += 1
        for k, v in mapped.items():
            if k in allowed:
                target[k] = v
        # For initial uploads, keep input lightweight but still compatible with tracker/generation.
        if not target.get("ngo_ref") and target.get("ngo_name"):
            target["ngo_ref"] = _normalise_lead_name(target.get("ngo_name") or "") or target.get("ngo_name")
        if not target.get("contact_status"):
            target["contact_status"] = "not_started"
        if target.get("contact_number") and not target.get("selected_phone"):
            target["selected_phone"] = target.get("contact_number")
        if target.get("contact_number") and not target.get("all_phones"):
            target["all_phones"] = target.get("contact_number")
        target["updated_at"] = now_s
        updated += 1
    _write_contact_tracker(region, rows)
    _workspace_log(region, "contact_tracker_csv_imported", {"updated_count": updated, "appended_count": appended})
    _undo_snapshot_after("contact_tracker_import_csv", f"Contact Tracker CSV import: {updated} row(s)", region, paths, undo_before)
    return _json(True, updated_count=updated, appended_count=appended, count=len(rows), summary=_contact_summary(rows))


@app.get("/contact-tracker/sample-input.csv")
def contact_tracker_sample_input():
    import io
    headers = [
        "NGO Name", "Category", "Rating", "PM Reviewer", "Reviewer Note",
        "Website", "POC Name", "Contact Number", "Outreach Owner"
    ]
    sample_rows = [
        {
            "NGO Name": "Example Education Trust",
            "Category": "Residential school / long-term education pathway",
            "Rating": "4",
            "PM Reviewer": "Avika",
            "Reviewer Note": "Strong residential education model; food support likely relevant.",
            "Website": "",
            "POC Name": "",
            "Contact Number": "",
            "Outreach Owner": "",
        },
        {
            "NGO Name": "Example Children Foundation",
            "Category": "Full-day school / low-income education",
            "Rating": "3",
            "PM Reviewer": "Ipshita",
            "Reviewer Note": "Clear child focus and education pathway; needs contact validation.",
            "Website": "",
            "POC Name": "",
            "Contact Number": "",
            "Outreach Owner": "",
        },
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in sample_rows:
        writer.writerow(row)
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=dfp2_contact_supporter_sample_input.csv"})


@app.get("/contact-tracker/export.csv")
def contact_tracker_export(region: str = "Karnataka"):
    rows = _read_contact_tracker(region)
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CONTACT_TRACKER_HEADERS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(_safe_csv_row({h: row.get(h, "") for h in CONTACT_TRACKER_HEADERS}))
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=dfp2_contact_tracker_{_slug(region)}.csv"})


@app.get("/contact-tracker/summary")
def contact_tracker_summary(region: str = "Karnataka"):
    rows = _read_contact_tracker(region)
    return _json(True, region=region, **_contact_summary(rows))

@app.get("/ranking/final-output")
def ranking_final_output():
    data = _read_workstream_payload()
    rows = _workstream_rows(data, only_global=False)
    grouped: dict[str, dict] = {}
    for row in rows:
        key = _normalise_lead_name(row.get("ngo_name") or "") or str(row.get("ngo_name") or "")
        item = grouped.setdefault(key, {
            "ngo_name": row.get("ngo_name") or "",
            "website": row.get("website") or "",
            "background": row.get("background") or "",
            "reviewers": 0,
            "ratings": [],
            "comments": [],
        })
        try:
            rating = int(float(str(row.get("rank") or row.get("decision") or "0")))
        except Exception:
            rating = 0
        if rating:
            item["ratings"].append(rating)
        item["reviewers"] += 1
        if row.get("reason"):
            item["comments"].append({"pm": row.get("pm"), "reason": row.get("reason")})
    output = []
    for item in grouped.values():
        ratings = item.pop("ratings")
        avg = round(sum(ratings) / len(ratings), 2) if ratings else 0
        max_rating = max(ratings) if ratings else 0
        spread = (max(ratings) - min(ratings)) if len(ratings) > 1 else 0
        output.append({**item, "avg_rating": avg, "max_rating": max_rating, "spread": spread})
    output.sort(key=lambda r: (r.get("avg_rating", 0), r.get("max_rating", 0), r.get("reviewers", 0)), reverse=True)
    return _json(True, count=len(output), rows=output)


# -----------------------------------------------------------------------------
# Lead Pool import from historical runs
# -----------------------------------------------------------------------------
# Keeps old logs/archives intact, but lets the new workflow pull any completed
# General Discovery / legacy Story Discovery / Bulk Repository / Re-check run
# into the regional Lead Pool.

def _read_csv_dicts_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]

def _run_rows_for_lead_pool_import(run_id: str, module_name: str = "", kind: str = "") -> tuple[list[dict], str, Path | None]:
    module = str(module_name or "").strip().lower()
    requested = str(kind or "").strip().lower()
    rd = _run_dir(run_id)
    if not rd.exists() or not rd.is_dir():
        return [], "missing", None

    candidates: list[tuple[str, Path]] = []
    if requested:
        for mapping in (STORY_OUTPUTS, OUTPUTS, RECHECK_OUTPUTS, PRESENCE_OUTPUTS):
            if requested in mapping:
                candidates.append((requested, rd / mapping[requested]))

    if module in {"discovery", "legacy_story", "story", "story_discovery"} or run_id.startswith(("discovery", "story")):
        candidates.extend([
            ("stories", rd / STORY_OUTPUTS["stories"]),
            ("story_csv", rd / STORY_OUTPUTS["story_csv"]),
        ])
    elif module in {"no_website_recheck", "recheck", "recovery"}:
        candidates.extend([
            ("results", rd / RECHECK_OUTPUTS["results"]),
            ("repository", rd / OUTPUTS["repository"]),
        ])
    elif module in {"ngo_presence_check", "presence", "presence_check"} or run_id.startswith("presence_"):
        candidates.extend([
            ("results", rd / PRESENCE_OUTPUTS["results"]),
        ])
    else:
        candidates.extend([
            ("repository", rd / OUTPUTS["repository"]),
            ("results", rd / RECHECK_OUTPUTS["results"]),
            ("presence_results", rd / PRESENCE_OUTPUTS["results"]),
            ("stories", rd / STORY_OUTPUTS["stories"]),
        ])

    seen_paths: set[str] = set()
    for resolved_kind, path in candidates:
        key = str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        rows = _read_csv_dicts_safe(path)
        if rows:
            return rows, resolved_kind, path
    return [], "empty", None

@app.post("/workspace/{region}/lead-pool/import-run")
def workspace_import_run_to_lead_pool(region: str, payload: dict):
    payload = payload or {}
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return _json(False, status_code=400, error="run_id is required")
    module_name = str(payload.get("module") or "").strip()
    source_type = str(payload.get("source_type") or "Archive Import").strip() or "Archive Import"
    kind = str(payload.get("kind") or "").strip()

    rows_from_run, resolved_kind, source_path = _run_rows_for_lead_pool_import(run_id, module_name=module_name, kind=kind)
    paths = [_lead_pool_path(region)]
    undo_before = _undo_snapshot_before(paths)
    if not rows_from_run:
        return _json(
            False,
            status_code=404,
            error="No importable output CSV found for this run yet",
            run_id=run_id,
            module=module_name,
            kind=resolved_kind,
        )

    existing = _read_lead_pool(region)
    by_key = {_lead_key(row): row for row in existing}
    added = 0
    updated = 0
    imported = 0
    for raw in rows_from_run:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["source_run"] = run_id
        row["run_id"] = run_id
        row["source_type"] = source_type
        incoming = _lead_from_any(row, region, source_type=source_type)
        key = _lead_key(incoming)
        if key in by_key:
            by_key[key] = _merge_lead(by_key[key], incoming)
            updated += 1
        else:
            by_key[key] = incoming
            added += 1
        imported += 1

    rows = list(by_key.values())
    rows, already_rated_marked = _annotate_existing_ranking_leads(rows)
    rows.sort(key=lambda r: (str(r.get("source_type") or ""), str(r.get("district") or ""), str(r.get("ngo_name") or "")))
    _write_lead_pool(region, rows)
    _workspace_log(region, "lead_pool_import_run", {
        "run_id": run_id,
        "module": module_name,
        "kind": resolved_kind,
        "source_type": source_type,
        "source_path": str(source_path) if source_path else "",
        "imported": imported,
        "added": added,
        "updated": updated,
        "already_rated_marked": already_rated_marked,
    })
    _undo_snapshot_after("lead_pool_import_run", f"Run sent to Lead Pool: {added} added, {updated} existing", region, paths, undo_before)
    # Low-memory response: do not send the entire lead pool back after importing a run.
    # The frontend can refresh /workspace/{region}/lead-pool if it needs the full table.
    light_responses = os.environ.get("DFP2_LIGHT_RESPONSES", "true").lower() in {"1", "true", "yes"}
    out_rows = rows[:200] if light_responses else rows
    return _json(
        True,
        region=region,
        run_id=run_id,
        module=module_name,
        kind=resolved_kind,
        imported=imported,
        added=added,
        updated=updated,
        already_rated_marked=already_rated_marked,
        count=len(rows),
        rows=out_rows,
        rows_truncated=bool(light_responses and len(rows) > len(out_rows)),
        full_rows_endpoint=f"/workspace/{_slug(region)}/lead-pool",
    )



# -----------------------------------------------------------------------------
# Low-memory maintenance helpers (v54)
# -----------------------------------------------------------------------------
def _protected_lead_pool_run_ids() -> set[str]:
    """Run folders that have been sent/imported to Lead Pool must never be scrubbed.

    Sources checked:
    - source_run/run_id columns in every workspace lead_pool.csv
    - lead_pool_import_run entries in workspace_log.jsonl
    """
    protected: set[str] = set()
    try:
        for workspace in WORKSPACES_DIR.iterdir():
            if not workspace.is_dir():
                continue
            lp = workspace / "lead_pool.csv"
            if lp.exists():
                try:
                    with lp.open("r", encoding="utf-8-sig", newline="") as f:
                        for row in csv.DictReader(f):
                            for key in ("source_run", "run_id"):
                                val = str(row.get(key) or "").strip()
                                if val:
                                    protected.add(val)
                except Exception:
                    pass
            logp = workspace / "workspace_log.jsonl"
            if logp.exists():
                try:
                    with logp.open("r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if "lead_pool_import_run" not in line and "run_id" not in line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            payload = obj.get("payload") if isinstance(obj, dict) else None
                            if isinstance(payload, dict):
                                val = str(payload.get("run_id") or "").strip()
                                if val:
                                    protected.add(val)
                except Exception:
                    pass
    except Exception:
        pass
    return protected

@app.get("/admin/maintenance/protected-runs")
def admin_protected_runs(password: str = ""):
    _workstream_check_admin({"password": password})
    protected = sorted(_protected_lead_pool_run_ids())
    return _json(True, protected_count=len(protected), protected_runs=protected[:500])

@app.post("/admin/maintenance/cleanup-runs")
def admin_cleanup_old_runs(payload: dict):
    """Low-memory cleanup that never deletes Lead-Pool-imported runs.

    Default is dry-run. It only deletes when confirm=true.
    Always keeps:
    - /workspaces
    - undo_redo unless delete_undo=true
    - any run_id/source_run found in lead_pool.csv or lead_pool_import_run logs
    """
    payload = payload or {}
    _workstream_check_admin(payload)
    confirm = bool(payload.get("confirm"))
    keep_latest = int(payload.get("keep_latest") or 10)
    keep_latest = max(0, min(keep_latest, 100))
    delete_undo = bool(payload.get("delete_undo"))
    protect_imported = str(payload.get("protect_imported", "true")).lower() not in {"0", "false", "no"}
    protected = _protected_lead_pool_run_ids() if protect_imported else set()
    prefixes = ("run_", "recheck_", "presence_", "story", "discovery", "enrich_")
    candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir() and p.name.startswith(prefixes)]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    protected_candidates = [p for p in candidates if p.name in protected]
    deletable_candidates = [p for p in candidates if p.name not in protected]
    to_delete = deletable_candidates[keep_latest:]
    deleted = []
    if confirm:
        for path in to_delete:
            try:
                shutil.rmtree(path, ignore_errors=True)
                deleted.append(path.name)
            except Exception:
                pass
        if delete_undo:
            try:
                shutil.rmtree(UNDO_REDO_DIR, ignore_errors=True)
                UNDO_REDO_DIR.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        try:
            REPO_LOCK_FILE.unlink(missing_ok=True)
            # Do not remove global_scan_history/dashboard unless explicitly requested.
            if bool(payload.get("delete_dashboard_cache")):
                GLOBAL_SCAN_HISTORY.unlink(missing_ok=True)
                DASHBOARD_DATA_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return _json(
        True,
        stage="cleanup_dry_run" if not confirm else "cleanup_complete",
        dry_run=not confirm,
        confirm_required=not confirm,
        candidates_count=len(candidates),
        protected_imported_count=len(protected_candidates),
        protected_imported_runs=[p.name for p in protected_candidates[:200]],
        deletable_count=len(to_delete),
        deletable_preview=[p.name for p in to_delete[:100]],
        deleted_count=len(deleted),
        deleted=deleted[:100],
        kept_latest=keep_latest,
        deleted_undo=bool(confirm and delete_undo),
        note="Lead-Pool-imported runs are protected and were not deleted." if protect_imported else "Imported-run protection disabled by payload.",
    )


# -----------------------------------------------------------------------------
# V60 Deep Enrichment — Firecrawl multi-key failover + Serper evidence collection
# -----------------------------------------------------------------------------
# Registered at the end so the module can reuse the hardened persistence, SSRF,
# Serper failover, job registry, CORS, and mutation-auth helpers above.
import importlib.util as _importlib_util

_deep_spec = _importlib_util.spec_from_file_location("dfp2_deep_enrichment", Path(__file__).resolve().parent / "deep_enrichment.py")
if _deep_spec is None or _deep_spec.loader is None:
    raise RuntimeError("Could not load deep_enrichment.py")
_deep_module = _importlib_util.module_from_spec(_deep_spec)
_deep_spec.loader.exec_module(_deep_module)

deep_enrichment_runtime = _deep_module.register_deep_enrichment(
    app,
    runs_dir=RUNS_DIR,
    serper_post=_serper_post,
    has_serper_keys=_has_serper_keys,
    safe_fetch_text=_safe_fetch_text,
    validate_public_url=_validate_public_http_url,
    make_soup=_make_soup,
    get_anthropic=_get_anthropic,
    job_create=_job_create,
    job_update=_job_update,
    job_request_cancel=_job_request_cancel,
    job_cancel_requested=_job_cancel_requested,
    utc_now_iso=_utc_now_iso,
)


# =============================================================================
# RUN DELETION PATCH (v67) — in-app run deletion + disk usage
# Added below; reuses existing _run_dir/_job_path/_job_live_state/_json/
# _workstream_check_admin helpers defined earlier in this file.
# =============================================================================
import shutil

# Prefixes we recognise as deletable run folders. Anything else is refused.
_DELETABLE_RUN_PREFIXES = (
    "run_", "recheck_", "presence_", "discovery", "discovery_state",
    "story", "story_state", "enrich", "repair", "pre_count_rebuild",
)

# Names that must NEVER be deleted even if someone passes them.
_PROTECTED_RUN_NAMES = {"_jobs", "undo_redo", "workspaces", "lost+found", "runs"}


def _is_deletable_run_dir(run_id: str) -> tuple[bool, str]:
    """Validate that run_id names a real, safe-to-delete run directory.
    Returns (ok, reason_if_not)."""
    name = str(run_id or "").strip()
    if not name:
        return False, "empty run id"
    if name in _PROTECTED_RUN_NAMES:
        return False, f"'{name}' is a protected system folder"
    rd = _run_dir(name)  # already strips to alnum/_/- and joins under RUNS_DIR
    try:
        rd_resolved = rd.resolve()
        runs_resolved = RUNS_DIR.resolve()
    except Exception:
        return False, "could not resolve path"
    # Must be strictly inside RUNS_DIR (defence against traversal).
    if runs_resolved not in rd_resolved.parents:
        return False, "path escapes runs directory"
    if not rd_resolved.is_dir():
        return False, "run directory not found"
    if not name.startswith(_DELETABLE_RUN_PREFIXES):
        return False, "not a recognised run folder"
    # Never delete a run that is still executing.
    if _job_live_state(name) == "running":
        return False, "run is still active; stop or cancel it first"
    return True, ""


def _delete_one_run(run_id: str) -> dict:
    """Delete a single run directory and its job-registry record.
    Returns a per-run result dict."""
    ok, reason = _is_deletable_run_dir(run_id)
    if not ok:
        return {"run_id": run_id, "deleted": False, "reason": reason}
    rd = _run_dir(run_id)
    freed_bytes = 0
    try:
        for p in rd.rglob("*"):
            if p.is_file():
                try:
                    freed_bytes += p.stat().st_size
                except Exception:
                    pass
        shutil.rmtree(rd)
    except Exception as e:
        return {"run_id": run_id, "deleted": False, "reason": f"delete failed: {e}"}
    # Best-effort remove the durable job record too.
    try:
        jp = _job_path(run_id)
        if jp.exists():
            jp.unlink()
    except Exception:
        pass
    return {"run_id": run_id, "deleted": True, "freed_bytes": freed_bytes}


@app.post("/repository/runs/delete")
def repository_delete_run(payload: dict | None = None):
    """Delete ONE run by id.  Body: {password, confirm: true, run_id}."""
    payload = payload or {}
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    if payload.get("confirm") is not True:
        return _json(False, status_code=400, error="Confirmation required before deleting a run")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        return _json(False, status_code=400, error="run_id is required")
    result = _delete_one_run(run_id)
    if not result.get("deleted"):
        return _json(False, status_code=400, error=result.get("reason", "could not delete"), result=result)
    return _json(True, result=result)


@app.post("/repository/runs/delete-many")
def repository_delete_runs(payload: dict | None = None):
    """Delete MANY runs.  Body: {password, confirm: true, run_ids: [...]}."""
    payload = payload or {}
    try:
        _workstream_check_admin(payload)
    except HTTPException as e:
        return _json(False, status_code=e.status_code, error=str(e.detail))
    if payload.get("confirm") is not True:
        return _json(False, status_code=400, error="Confirmation required before deleting runs")
    run_ids = payload.get("run_ids") or []
    if not isinstance(run_ids, list) or not run_ids:
        return _json(False, status_code=400, error="run_ids (non-empty list) is required")
    results = [_delete_one_run(str(r).strip()) for r in run_ids]
    deleted = [r for r in results if r.get("deleted")]
    freed = sum(r.get("freed_bytes", 0) for r in deleted)
    return _json(
        True,
        deleted_count=len(deleted),
        failed_count=len(results) - len(deleted),
        freed_bytes=freed,
        freed_mb=round(freed / (1024 * 1024), 1),
        results=results,
    )


@app.get("/repository/runs/disk-usage")
def repository_disk_usage():
    """Report volume usage so the UI can show how full RUNS_DIR is."""
    import shutil as _sh
    try:
        total, used, free = _sh.disk_usage(str(RUNS_DIR))
        runs_bytes = 0
        for p in RUNS_DIR.rglob("*"):
            if p.is_file():
                try:
                    runs_bytes += p.stat().st_size
                except Exception:
                    pass
        return _json(
            True,
            runs_dir=str(RUNS_DIR),
            volume_total_mb=round(total / (1024 * 1024), 1),
            volume_used_mb=round(used / (1024 * 1024), 1),
            volume_free_mb=round(free / (1024 * 1024), 1),
            volume_used_pct=round(used / total * 100, 1) if total else 0,
            runs_data_mb=round(runs_bytes / (1024 * 1024), 1),
        )
    except Exception as e:
        return _json(False, status_code=500, error=str(e))
