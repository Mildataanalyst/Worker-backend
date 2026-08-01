# ============================================================================
#  DFP 2.0  —  NGO DISCOVERY ENGINE   (run this in Google Colab)
# ----------------------------------------------------------------------------
#  WHAT THIS DOES
#  Takes a list of NGO names (5,000+ is fine) and produces ONE clean CSV that
#  your website displays:  name, location, who they serve, a story, digital
#  presence, website, socials, partners, notes.
#  Also writes a safe donor-lite CSV from partner/funder names already found
#  on NGO websites. It adds no extra search/API calls, so it should not make
#  the repository engine materially more fragile.
#
#  Also writes dfp2_status.json while running. Your website/backend can poll
#  this file to show exactly what is currently being searched/fetched/profied.
#
#  HOW IT IS BUILT TO NOT BREAK (this is the whole point)
#  - Processes one NGO at a time and SAVES after every single one. If anything
#    stops it (closed tab, wifi drop, rate limit), you press Run again and it
#    SKIPS everything already done and continues. Nothing is ever lost.
#  - Every network call has a 15s timeout, so one dead website can never freeze
#    the run. That row is just skipped and logged.
#  - Retries with backoff on temporary errors; the AI step uses the Batch API,
#    which has far higher limits than a normal loop (this avoids the "429 /
#    30,000 tokens per minute" error a naive loop hits) and costs ~50% less.
#  - The worst thing that can happen anywhere is "a few fewer rows," never a
#    crash. Every error is written into the output so you can see exactly what
#    happened and where.
#
#  HOW TO RUN  (no coding needed — just edit the CONFIG block, then Runtime > Run all)
#    1. Put your NGO list in a file called  ngo_list.csv  with a column "name"
#       (optional columns: "district", "state"). Upload it to Colab (folder icon
#       on the left > upload).
#    2. Paste your two keys into the CONFIG block below.
#    3. Runtime menu > Run all. Watch the progress bar. When it finishes, it
#       writes  dfp2_repository_output.csv  — download it and drop it on the site.
#
#  SAFE LIMITS (my recommendation, you asked me to decide):
#    - 5,000 names is comfortable. This same setup scales to ~25,000 on one $50
#      Serper pack. Search is the only real cost; 5,000 ≈ a few hundred rupees.
#    - If you ever go above ~25,000, split the list in two and run twice. You
#      never need to change anything else.
# ============================================================================

# ----------------------------------------------------------------------------
# 0. INSTALL  (Colab: this runs once; safe to re-run)
# ----------------------------------------------------------------------------
# In Colab, run this line in its own cell first (remove the # ):
# !pip install anthropic requests tqdm beautifulsoup4 --quiet

import os, re, csv, json, time, html, hashlib, traceback, tempfile, threading, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
import ipaddress, socket
import requests
from bs4 import BeautifulSoup
from tqdm.auto import tqdm

# ============================================================================
# 1. CONFIG  —  the only part you edit
# ============================================================================
SERPER_API_KEY    = os.environ.get("SERPER_API_KEY", "").strip()      # the one funded serper.dev account
SERPER_API_KEYS_RAW = os.environ.get("SERPER_API_KEYS", "").strip()   # legacy variable; deliberately ignored
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()   # from console.anthropic.com / Render env


class ProviderPauseRequested(RuntimeError):
    def __init__(self, provider, reason, *, key_label="", status_code=None, detail=""):
        self.provider = str(provider or "provider").lower()
        self.reason = str(reason or "provider_capacity_exhausted")
        self.key_label = str(key_label or "")
        self.status_code = status_code
        self.detail = str(detail or "")[:500]
        label = f" ({self.key_label})" if self.key_label else ""
        code = f" HTTP {self.status_code}" if self.status_code is not None else ""
        super().__init__(f"{self.provider}{label}{code}: {self.reason}. {self.detail}".strip())


def _provider_error_reason(status_code, body):
    low = str(body or "").lower()
    credit_markers = (
        "insufficient credit", "insufficient credits", "credits exhausted",
        "credit balance", "credit_balance", "billing", "payment required",
        "usage limit reached", "spend limit", "monthly limit", "quota exhausted",
        "quota exceeded", "not enough credits",
    )
    if status_code == 402 or any(marker in low for marker in credit_markers):
        return "credits_exhausted"
    if status_code in {401, 403}:
        return "key_rejected"
    return "provider_capacity_exhausted"


def _anthropic_error_body(err):
    parts = [str(err or "")]
    try:
        response = getattr(err, "response", None)
        text = getattr(response, "text", None)
        if text:
            parts.append(str(text))
        body = getattr(response, "body", None)
        if body:
            parts.append(str(body))
    except Exception:
        pass
    return " | ".join(x for x in parts if x)[:1000]


def _anthropic_status_code_safe(err):
    try:
        return int(getattr(err, "status_code"))
    except Exception:
        pass
    try:
        response = getattr(err, "response", None)
        return int(getattr(response, "status_code"))
    except Exception:
        return None


def _raise_if_anthropic_capacity_error(err):
    code = _anthropic_status_code_safe(err)
    body = _anthropic_error_body(err)
    low = body.lower()
    markers = (
        "credit balance", "credit_balance", "insufficient credit",
        "credits exhausted", "payment required", "billing",
        "spend limit", "monthly limit", "usage limit reached",
        "quota exhausted", "not enough credits",
    )
    if code in {401, 402, 403} or any(marker in low for marker in markers):
        raise ProviderPauseRequested(
            "anthropic",
            _provider_error_reason(code, body),
            key_label=_mask_key(ANTHROPIC_API_KEY),
            status_code=code,
            detail=body,
        ) from err


def _serper_keys():
    # v73/v85 uses one transparent funded account. Old SERPER_API_KEYS pools
    # are ignored so an expired key cannot silently receive part of a run.
    return [SERPER_API_KEY] if SERPER_API_KEY else []

_SERPER_KEY_INDEX = 0
_SERPER_DISABLED_KEYS = set()
_SERPER_KEY_COOLDOWNS = {}
_SERPER_KEY_INFLIGHT = {}
_SERPER_KEY_USAGE = {}
_SERPER_CONDITION = threading.Condition(threading.Lock())


def _mask_key(key):
    return ("..." + key[-6:]) if key and len(key) > 6 else "..."


def _serper_per_key_concurrency():
    raw = os.environ.get("SERPER_CONCURRENCY", os.environ.get("SERPER_CONCURRENCY_PER_KEY", "4"))
    try:
        value = int(raw)
    except Exception:
        value = 4
    return max(1, min(value, 8))


def _serper_429_cooldown_seconds(response=None):
    default = max(2.0, float(os.environ.get("SERPER_429_COOLDOWN_SEC", "20")))
    try:
        return max(default, float((response.headers or {}).get("Retry-After", default)))
    except Exception:
        return default


def _serper_is_permanent_key_error(status_code, body):
    low = (body or "").lower()
    if status_code in {401, 402, 403}:
        return True
    permanent_markers = [
        "insufficient credit", "insufficient credits", "credits exhausted",
        "credit balance", "billing", "payment required", "invalid api key",
        "unauthorized", "forbidden",
    ]
    return any(x in low for x in permanent_markers)


def _lease_serper_key(wait_timeout=30.0):
    global _SERPER_KEY_INDEX
    deadline = time.monotonic() + max(1.0, float(wait_timeout or 30.0))
    with _SERPER_CONDITION:
        while True:
            keys = _serper_keys()
            if not keys:
                return None
            now = time.monotonic()
            limit = _serper_per_key_concurrency()
            for offset in range(len(keys)):
                idx = (_SERPER_KEY_INDEX + offset) % len(keys)
                key = keys[idx]
                if key in _SERPER_DISABLED_KEYS:
                    continue
                if float(_SERPER_KEY_COOLDOWNS.get(key, 0.0)) > now:
                    continue
                if int(_SERPER_KEY_INFLIGHT.get(key, 0)) >= limit:
                    continue
                _SERPER_KEY_INDEX = (idx + 1) % len(keys)
                _SERPER_KEY_INFLIGHT[key] = int(_SERPER_KEY_INFLIGHT.get(key, 0)) + 1
                _SERPER_KEY_USAGE[key] = int(_SERPER_KEY_USAGE.get(key, 0)) + 1
                return key
            live = [k for k in keys if k not in _SERPER_DISABLED_KEYS]
            if not live:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            next_ready = min([float(_SERPER_KEY_COOLDOWNS.get(k, now)) for k in live] or [now])
            wait_for = min(0.25, remaining)
            if next_ready > now:
                wait_for = min(max(0.05, next_ready - now), remaining)
            _SERPER_CONDITION.wait(timeout=wait_for)


def _release_serper_key(key):
    if not key:
        return
    with _SERPER_CONDITION:
        _SERPER_KEY_INFLIGHT[key] = max(0, int(_SERPER_KEY_INFLIGHT.get(key, 0)) - 1)
        _SERPER_CONDITION.notify_all()


def _disable_serper_key(key, reason="exhausted_or_invalid"):
    if not key:
        return
    with _SERPER_CONDITION:
        _SERPER_DISABLED_KEYS.add(key)
        _SERPER_KEY_COOLDOWNS.pop(key, None)
        _SERPER_CONDITION.notify_all()


def _cooldown_serper_key(key, seconds):
    if not key:
        return
    with _SERPER_CONDITION:
        _SERPER_KEY_COOLDOWNS[key] = max(float(_SERPER_KEY_COOLDOWNS.get(key, 0.0)), time.monotonic() + max(1.0, float(seconds or 1.0)))
        _SERPER_CONDITION.notify_all()


def _serper_key_stats():
    now = time.monotonic()
    with _SERPER_CONDITION:
        return [
            {
                "key": _mask_key(k),
                "requests_this_run": int(_SERPER_KEY_USAGE.get(k, 0)),
                "inflight": int(_SERPER_KEY_INFLIGHT.get(k, 0)),
                "disabled": k in _SERPER_DISABLED_KEYS,
                "cooldown_seconds": round(max(0.0, float(_SERPER_KEY_COOLDOWNS.get(k, 0.0)) - now), 1),
            }
            for k in _serper_keys()
        ]


def _has_serper_keys():
    return bool(_serper_keys())

INPUT_CSV   = "ngo_list.csv"                  # your list (column: name [, district, state])
OUTPUT_CSV  = "dfp2_repository_output.csv"    # reviewable shortlist only (Yes + Maybe)
PROGRESS_DB = "dfp2_progress.jsonl"           # checkpoint file — DO NOT delete mid-run
AI_PROFILE_DB = "dfp2_ai_profiles.jsonl"      # AI results checkpoint — makes AI stage resumable
AUDIT_CSV   = "dfp2_run_audit.csv"            # status of every input row, including skipped rows
DONOR_OUTPUT_CSV = "dfp2_donor_leads_lite.csv" # safe donor-lite output from partners_found; no extra web/API calls
REJECTED_CSV = "dfp2_rejected_audit.csv"       # filtered-out rows with internal reason codes
DUPLICATE_CANDIDATES_CSV = "dfp2_duplicate_candidates.csv"  # rows skipped only because the exact name+district+state key repeated
GLOBAL_SCAN_HISTORY = os.path.join(os.environ.get("RUNS_DIR", "."), "global_scan_history.csv")
FILTER_VERSION = os.environ.get("DFP_FILTER_VERSION", "avika_fit_v2")
ERROR_LOG   = "dfp2_errors.log"               # every error, with the exact message
STATUS_JSON = "dfp2_status.json"             # live machine-readable status for the website/API
AI_BATCH_DB = "dfp2_ai_batches.jsonl"        # saved Anthropic batch IDs so reruns do not duplicate cost
MAX_BATCH_WAIT_SEC = int(os.environ.get("MAX_BATCH_WAIT_SEC", "7200"))  # stop waiting after 2h; rerun resumes the same batch later

HAIKU_MODEL = os.environ.get("HAIKU_MODEL", "claude-haiku-4-5-20251001")     # cheap + fast; update if needed

# Reliability dials (sensible defaults — you don't need to touch these)
SEARCH_TIMEOUT   = 15      # seconds per Serper call
FETCH_TIMEOUT    = 15      # seconds per website fetch
MAX_PAGES_PER_NGO = 4      # homepage + about + programs + partners
MAX_RETRIES      = 4       # for temporary (429/5xx) errors
MAX_RESPONSE_BYTES = int(os.environ.get("MAX_RESPONSE_BYTES", "1500000"))
SERPER_PACE_SEC  = float(os.environ.get("SERPER_PACE_SEC", "0.20"))    # small gap between searches to stay under limits
SERPER_QUERIES_PER_NGO = int(os.environ.get("SERPER_QUERIES_PER_NGO", "1"))  # bulk-safe default: one exact query per NGO
AI_BATCH_SIZE    = int(os.environ.get("AI_BATCH_SIZE", "500"))     # NGOs profiled per AI batch job (keeps batches tidy)

# Avika cost controls. Fast Recovery enables these explicitly in the parent worker.
# Bulk Discovery keeps its existing rich profile unless these variables are set.
AVIKA_RULE_PREFILTER = os.environ.get("AVIKA_RULE_PREFILTER", "false").strip().lower() in {"1", "true", "yes", "on"}
AVIKA_CLASSIFICATION_ONLY = os.environ.get("AVIKA_CLASSIFICATION_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}
AVIKA_SITE_TEXT_CHARS = max(600, int(os.environ.get("AVIKA_SITE_TEXT_CHARS", "1500")))
AVIKA_MAX_TOKENS = max(48, int(os.environ.get("AVIKA_MAX_TOKENS", "80")))
# Rapid Mode speed-up: small runs should not wait for Claude Batch queueing.
# auto = direct for rapid/small runs, batch for bulk.
AI_PROFILE_MODE = os.environ.get("AI_PROFILE_MODE", "auto").strip().lower()
DIRECT_AI_MAX_ITEMS = int(os.environ.get("DIRECT_AI_MAX_ITEMS", "25"))
DIRECT_AI_CONCURRENCY = max(1, int(os.environ.get("DIRECT_AI_CONCURRENCY", "1")))
BULK_SEARCH_CONCURRENCY = max(1, int(os.environ.get("BULK_SEARCH_CONCURRENCY", "16")))
DFP_RUN_MODE = os.environ.get("DFP_RUN_MODE", "").strip().lower()
CHECKPOINT_EVERY = 1       # save after every NGO (most crash-proof setting)

# ============================================================================
# 2. SMALL HELPERS
# ============================================================================
def log_error(ngo_id, name, stage, err):
    """Never raise — just record exactly what went wrong, for diagnostics."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] id={ngo_id} stage={stage} name={name!r} :: {err}"
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return line

def ngo_id_for(name, district="", state=""):
    # Keep same-name NGOs in different places independent for checkpoint/resume.
    key = "|".join([
        re.sub(r"\s+", " ", (name or "").strip().lower()),
        re.sub(r"\s+", " ", (district or "").strip().lower()),
        re.sub(r"\s+", " ", (state or "").strip().lower()),
    ])
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]

def load_done_ids():
    """Resume: read the checkpoint and skip anything already finished."""
    done = {}
    if os.path.exists(PROGRESS_DB):
        with open(PROGRESS_DB, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    done[row["id"]] = row
                except Exception:
                    pass
    return done

def checkpoint(row):
    with open(PROGRESS_DB, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def load_ai_profiles():
    """Resume AI stage too. Without this, a rerun after search/fetch would skip
    the rows but lose their AI profile fields in the final CSV."""
    profiles = {}
    if os.path.exists(AI_PROFILE_DB):
        with open(AI_PROFILE_DB, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    profiles[row["id"]] = row["profile"]
                except Exception:
                    pass
    return profiles

def checkpoint_ai_profile(ngo_id, profile):
    with open(AI_PROFILE_DB, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": ngo_id, "profile": profile}, ensure_ascii=False) + "\n")

# ============================================================================
# 2.1 LIVE STATUS HELPERS — always writes valid JSON for the website
# ----------------------------------------------------------------------------
#  The website/API should read this file and return it as JSON. This prevents
#  the old frontend error: Unexpected token 'A', because even failures are saved
#  as JSON objects, never plain text like "An error occurred...".
# ============================================================================
def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _status_counts(done=None):
    done = done or load_done_ids()
    counts = {
        "processed": len(done),
        "ready_for_ai": 0,
        "no_official_website": 0,
        "fetch_failed": 0,
        "search_failed": 0,
        "dropped_not_children": 0,
        "prefilter_rejected": 0,
        "filtered_rejected": 0,
        "shortlisted": 0,
        "maybe": 0,
        "skipped_error": 0,
    }
    for r in done.values():
        s = r.get("status") or "unknown"
        if s in counts:
            counts[s] += 1
    return counts

def write_status(stage, message="", *, module="repository", ok=True, total=None, done=None,
                 current_item="", current_search="", current_url="", run_status="running",
                 error="", extra=None):
    """Write one atomic, always-valid JSON status file. Never raises.

    The frontend reads top-level metrics, so keep both the detailed counts object
    and flattened counters in the same payload.
    """
    counts = _status_counts()
    processed = done if done is not None else counts.get("processed", 0)
    payload = {
        "ok": bool(ok),
        "run_status": run_status,
        "module": module,
        "stage": stage,
        "message": str(message or ""),
        "current_item": str(current_item or ""),
        "current_search": str(current_search or ""),
        "current_url": str(current_url or ""),
        "total": total,
        "done": done,
        "processed": processed,
        "ready_for_ai": counts.get("ready_for_ai", 0),
        "no_official_website": counts.get("no_official_website", 0),
        "fetch_failed": counts.get("fetch_failed", 0),
        "search_failed": counts.get("search_failed", 0),
        "dropped_not_children": counts.get("dropped_not_children", 0),
        "prefilter_rejected": counts.get("prefilter_rejected", 0),
        "filtered_rejected": counts.get("filtered_rejected", 0),
        "shortlisted": counts.get("shortlisted", 0),
        "maybe": counts.get("maybe", 0),
        "skipped_error": counts.get("skipped_error", 0),
        "errors": counts.get("fetch_failed", 0) + counts.get("search_failed", 0) + counts.get("skipped_error", 0),
        "counts": counts,
        "error": str(error or ""),
        "updated_at": _now_iso(),
    }
    if extra and isinstance(extra, dict):
        payload.update(extra)
    try:
        folder = os.path.dirname(os.path.abspath(STATUS_JSON)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".status_", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_JSON)
    except Exception:
        # Status must never break the actual engine.
        pass

def safe_error_payload(stage, err, *, module="repository", current_item=""):
    """Use this shape in any API wrapper too: never return raw text errors."""
    return {
        "ok": False,
        "run_status": "error",
        "module": module,
        "stage": stage,
        "current_item": str(current_item or ""),
        "error": str(err),
        "updated_at": _now_iso(),
    }

def _batch_signature(items):
    ids = sorted(str(it.get("id", "")) for it in items)
    # Include model and a prompt-version marker so a future prompt/model change
    # does not accidentally resume an old batch with incompatible outputs.
    prompt_version = os.environ.get("DFP_AI_PROMPT_VERSION", FILTER_VERSION)
    material = "|".join([HAIKU_MODEL, prompt_version] + ids)
    return hashlib.md5(material.encode("utf-8")).hexdigest()[:16]

def load_ai_batches():
    batches = {}
    if os.path.exists(AI_BATCH_DB):
        with open(AI_BATCH_DB, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    batches[row["signature"]] = row
                except Exception:
                    pass
    return batches

def checkpoint_ai_batch(signature, batch_id, item_count):
    with open(AI_BATCH_DB, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "signature": signature,
            "batch_id": batch_id,
            "item_count": item_count,
            "created_at": _now_iso(),
        }, ensure_ascii=False) + "\n")

def _is_safe_public_url(url: str) -> bool:
    """Allow only normal public http/https URLs. Blocks localhost/private IPs.

    This reduces SSRF risk when fetching search-result URLs.
    """
    try:
        p = urlparse(str(url or ""))
        if p.scheme not in {"http", "https"}:
            return False
        host = p.hostname
        if not host:
            return False
        if p.port and p.port not in {80, 443}:
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


def _response_text_limited(r, max_bytes=MAX_RESPONSE_BYTES) -> str:
    """Read at most max_bytes from a response; avoids huge downloads."""
    if r is None:
        return ""
    chunks, total = [], 0
    try:
        for chunk in r.iter_content(chunk_size=65536, decode_unicode=False):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                chunk = chunk[: max_bytes - (total - len(chunk))]
                chunks.append(chunk)
                break
            chunks.append(chunk)
    except Exception:
        return ""
    raw = b"".join(chunks)
    enc = getattr(r, "encoding", None) or "utf-8"
    return raw.decode(enc, errors="replace")


def _safe_csv_cell(v):
    s = "" if v is None else str(v)
    if s.startswith(("=", "+", "-", "@")):
        return "'" + s
    return s


def _safe_csv_row(row):
    return [_safe_csv_cell(x) for x in row]


def http_get(url, timeout, headers=None):
    """GET with retry + exponential backoff, URL safety and response-size cap."""
    if not _is_safe_public_url(url):
        return None
    headers = headers or {"User-Agent": "Mozilla/5.0 (DFP2 discovery bot)"}
    delay = 1.5
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(
                url,
                timeout=timeout,
                headers=headers,
                allow_redirects=True,
                stream=True,
            )
            # Block private/internal redirect targets too.
            final_url = getattr(r, "url", url)
            if not _is_safe_public_url(final_url):
                try: r.close()
                except Exception: pass
                return None
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", delay))
                try: r.close()
                except Exception: pass
                time.sleep(min(wait, 30)); delay *= 2; continue
            return r
        except requests.RequestException:
            time.sleep(delay); delay *= 2
    return None

# ============================================================================
# 3. STAGE 0  —  CLEAN & DEDUPE
# ============================================================================
LEGAL_SUFFIXES = ("trust", "foundation", "society", "sanstha", "samiti", "mission",
                  "ngo", "charitable", "welfare", "seva", "sangha")

DUPLICATE_COLUMNS = ["NGO Name", "State", "District", "Duplicate Key", "Kept NGO Name", "Reason"]


def _dedupe_key(name, district, state):
    # Dedupe only exact repeats of the same org in the same geography.
    # Same name in a different district/state must remain a separate row.
    return "|".join([
        re.sub(r"\s+", " ", (name or "").strip().lower()),
        re.sub(r"\s+", " ", (district or "").strip().lower()),
        re.sub(r"\s+", " ", (state or "").strip().lower()),
    ])


def _write_duplicate_candidates(rows):
    tmp = DUPLICATE_CANDIDATES_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DUPLICATE_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(_safe_csv_dict({k: row.get(k, "") for k in DUPLICATE_COLUMNS}))
    os.replace(tmp, DUPLICATE_CANDIDATES_CSV)


def _safe_item_id(value, fallback):
    """Return a stable Anthropic/checkpoint-safe row identifier.

    Source-record IDs are preferred so same-name rows are never collapsed. The
    original NGO/source IDs remain in the row metadata and exports.
    """
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    if cleaned:
        return cleaned[:72]
    return fallback


def clean_and_load():
    rows, seen, duplicates = [], {}, []
    with open(INPUT_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        name_key = None
        for k in (reader.fieldnames or []):
            if k and k.strip().lower() in ("name", "ngo_name", "ngo name", "organisation", "organization"):
                name_key = k
                break
        if name_key is None:
            raise ValueError("Your CSV needs a column called 'name'. Found: " + str(reader.fieldnames))

        for index, r in enumerate(reader, start=1):
            raw = (r.get(name_key) or "").strip()
            if not raw:
                continue
            name = re.sub(r"\s+", " ", raw).strip()
            district = (r.get("district") or r.get("District") or r.get("Location") or "").strip()
            state = (r.get("state") or r.get("State") or "").strip()
            supplied_website = (r.get("website") or r.get("Website") or r.get("url") or r.get("URL") or "").strip()
            darpan_id = (r.get("darpan_id") or r.get("Darpan ID") or r.get("NGO Darpan ID") or "").strip()
            provided_ngo_id = (r.get("ngo_id") or r.get("NGO ID") or r.get("DFP NGO ID") or r.get("dfp_ngo_id") or "").strip()
            source_record_id = (r.get("source_record_id") or r.get("Source Record ID") or "").strip()
            batch_id = (r.get("batch_id") or r.get("Batch ID") or "").strip()
            batch_label = (r.get("batch_label") or r.get("Batch Label") or "").strip()
            source_type = (r.get("source_type") or r.get("Source Type") or r.get("Source") or "").strip()
            source_run_id = (r.get("source_run_id") or r.get("source_run") or r.get("Run ID") or "").strip()
            website_recovery_status = (r.get("website_recovery_status") or r.get("Website Recovery Status") or r.get("discovery_status") or "").strip()

            # Preserve source rows. Only rows carrying the exact same source ID
            # (or, when absent, the exact same NGO ID) are treated as duplicates.
            if source_record_id:
                dedupe_key = "source:" + source_record_id.lower()
            elif provided_ngo_id:
                dedupe_key = "ngo:" + provided_ngo_id.lower()
            else:
                dedupe_key = "legacy:" + _dedupe_key(name, district, state)

            if dedupe_key in seen:
                kept = seen[dedupe_key]
                duplicates.append({
                    "NGO Name": name,
                    "State": state,
                    "District": district,
                    "Duplicate Key": dedupe_key,
                    "Kept NGO Name": kept.get("name", name),
                    "Reason": "Exact repeated source/NGO identifier. Same-name rows with different source IDs are preserved.",
                })
                continue

            fallback_id = ngo_id_for(name, district, state)
            item_id = _safe_item_id(source_record_id or provided_ngo_id, fallback_id)
            # Guard against repeated/blank custom IDs after sanitisation.
            if any(existing.get("id") == item_id for existing in rows):
                item_id = _safe_item_id(f"{source_record_id or provided_ngo_id or fallback_id}_{index}", f"{fallback_id}_{index}")

            row = {
                "id": item_id,
                "ngo_id": provided_ngo_id,
                "source_record_id": source_record_id,
                "batch_id": batch_id,
                "batch_label": batch_label,
                "source_type": source_type,
                "source_run_id": source_run_id,
                "website_recovery_status": website_recovery_status,
                "name": name,
                "district": district,
                "state": state,
                "website": supplied_website,
                "darpan_id": darpan_id,
            }
            seen[dedupe_key] = row
            rows.append(row)
    _write_duplicate_candidates(duplicates)
    return rows

# ============================================================================
# 4. STAGE 1  —  SEARCH (Serper)
# ============================================================================
REJECT_DOMAINS = ("ngodarpan", "darpan.gov", "csrbox", "ngobox", "justdial",
                  "sulekha", "indiamart", "facebook.", "instagram.", "linkedin.",
                  "twitter.", "x.com", "youtube.", "wikipedia.", "indiacsr",
                  "guidestar", "give.do", "globalgiving", "ngoadvisor")

NEWS_OR_LISTING_DOMAINS = ("thehindu", "timesofindia", "hindustantimes", "indianexpress",
                           "deccanherald", "deccanchronicle", "newindianexpress",
                           "yourstory", "betterindia", "medium.com", "wordpress.com",
                           "blogspot.com", "news", "magazine")

URL_RE = re.compile(r"""https?://[^\s\]\)\}"'<>]+""", re.I)

def _flatten_search_result_text(value):
    """Collect title/snippet/attributes/sitelinks text from a Serper result.

    Some high-signal pages such as LinkedIn, Give.do or Google knowledge snippets
    mention the real official website even though the page itself is a social/listing
    domain. We still reject those pages, but we can safely use the external URL
    they disclose when its domain matches the NGO name.
    """
    parts = []
    if isinstance(value, dict):
        for v in value.values():
            parts.append(_flatten_search_result_text(v))
    elif isinstance(value, list):
        for v in value:
            parts.append(_flatten_search_result_text(v))
    elif value is not None:
        parts.append(str(value))
    return " ".join(p for p in parts if p)

def _extract_urls_from_search_result(res):
    text = _flatten_search_result_text(res)
    urls = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?)]}")
        if u and u not in urls:
            urls.append(u)
    return urls

def _is_bad_candidate_url(url):
    low = (url or "").lower()
    if not low.startswith(("http://", "https://")):
        return True
    if any(bad in low for bad in REJECT_DOMAINS):
        return True
    if any(bad in low for bad in NEWS_OR_LISTING_DOMAINS):
        return True
    if low.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
        return True
    return False

def _owned_site_score(name, url, title="", snippet="", source="organic"):
    tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
              if t not in LEGAL_SUFFIXES and len(t) > 2]
    if not tokens:
        return -999, "no usable name tokens"
    low = (url or "").lower()
    if _is_bad_candidate_url(low):
        return -999, "bad/social/listing/news/document URL"
    parsed = urlparse(low)
    domain = parsed.netloc.replace("www.", "")
    compact_domain = re.sub(r"[^a-z0-9]", "", domain)
    compact_name = "".join(tokens)
    path_parts = [p for p in parsed.path.split("/") if p]
    title = (title or "").lower()
    snippet = (snippet or "").lower()
    hit_domain = sum(1 for t in tokens if t in compact_domain)
    hit_title = sum(1 for t in tokens if t in title)
    hit_text = sum(1 for t in tokens if t in title or t in snippet)
    is_homeish = len(path_parts) <= 1
    compact_bonus = 4 if compact_name and compact_name in compact_domain else 0
    source_bonus = 3 if source == "external_url_in_result" else 0
    score = source_bonus + compact_bonus + hit_domain * 6 + hit_title * 2 + hit_text + (2 if is_homeish else 0)
    # Require either a domain match, or a shallow URL strongly supported by result text.
    if hit_domain >= 1 or compact_bonus or (is_homeish and hit_text >= max(1, len(tokens)//2)):
        return score, f"domain_hits={hit_domain}; title_hits={hit_title}; text_hits={hit_text}; source={source}"
    return -50, "name/domain/title match too weak"

def serper_search(query):
    """Serper search against the one funded account with bounded retries.

    HTTP 429 cools the account down; permanent credit/key errors pause safely.
    A provider attempt is separate from the NGO's logical search decision.
    """
    delay = 1.0
    last_err = None
    attempts = max(MAX_RETRIES, len(_serper_keys()) * 2)
    for attempt in range(attempts):
        key = _lease_serper_key(wait_timeout=max(SEARCH_TIMEOUT, 30))
        if not key:
            return None, "the configured Serper account is exhausted, invalid, cooling down, or busy"
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                data=json.dumps({"q": query, "num": 10, "gl": "in"}),
                timeout=SEARCH_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("organic", []), None
            body = r.text[:300]
            if r.status_code == 429:
                _cooldown_serper_key(key, _serper_429_cooldown_seconds(r))
                last_err = f"temporary Serper rate limit on {_mask_key(key)}"
                continue
            if _serper_is_permanent_key_error(r.status_code, body):
                _disable_serper_key(key)
                raise ProviderPauseRequested(
                    "serper",
                    _provider_error_reason(r.status_code, body),
                    key_label=_mask_key(key),
                    status_code=r.status_code,
                    detail=body,
                )
            if r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", delay))
                time.sleep(min(wait, 15)); delay = min(delay * 2, 15); continue
            return None, f"serper status {r.status_code}: {body}"
        except requests.RequestException as e:
            last_err = e
            time.sleep(min(delay, 10)); delay = min(delay * 2, 10)
        finally:
            _release_serper_key(key)
    return None, f"serper failed after bounded retries: {last_err}"

def find_official_site(ngo, total=None, done=None):
    """One exact query by default.

    Query shape intentionally avoids broad words like children/education/charity,
    because those create noisy false positives. Set SERPER_QUERIES_PER_NGO=2 only
    for special debugging/high-priority rescans.
    Returns (url, organic_results, note).
    """
    name, dist, state = ngo["name"], ngo["district"], ngo["state"]
    geo = " ".join(x for x in (dist, state) if x)
    q1 = f'"{name}" {geo} official website'.strip()
    write_status(
        "searching_official_site",
        "Searching official website",
        current_item=name,
        current_search=q1,
        total=total,
        done=done,
        extra={"serper_queries_per_ngo": SERPER_QUERIES_PER_NGO},
    )
    organic, err = serper_search(q1)
    time.sleep(SERPER_PACE_SEC)
    if err:
        return None, [], err
    url = pick_owned_site(name, organic)
    if not url and SERPER_QUERIES_PER_NGO > 1:
        q2 = f'"{name}" {state} NGO'.strip()
        write_status(
            "searching_official_site_fallback",
            "Optional fallback search for official website",
            current_item=name,
            current_search=q2,
            total=total,
            done=done,
        )
        organic2, err2 = serper_search(q2)
        time.sleep(SERPER_PACE_SEC)
        if not err2 and organic2:
            organic = organic2
            url = pick_owned_site(name, organic)
    return url, (organic or []), None

def pick_owned_site(name, organic):
    """Rule-based, no AI: keep only an NGO's OWN website.

    v12 fix: recover official URLs exposed inside trusted search snippets/listing
    metadata. Example: LinkedIn/Give.do may be rejected as a source, but their
    snippet can reveal https://www.bridgesofsports.org/. We use that external URL
    only if its own domain matches the NGO name.
    """
    if not organic:
        return None
    candidates = []
    # Use the full Serper organic page, not just the first seven results. This
    # is still one query, but avoids false negatives when the official site is
    # slightly below socials/listings.
    for res in organic[:10]:
        link = (res.get("link") or "")
        title = res.get("title") or ""
        snippet = res.get("snippet") or ""

        score, note = _owned_site_score(name, link, title, snippet, source="organic")
        if score >= 6:
            candidates.append((score, link, note))

        # Also inspect every URL mentioned by the result itself. This catches
        # official website fields exposed by LinkedIn/Give.do/knowledge snippets
        # without accepting those listing/social pages as the website.
        for external_url in _extract_urls_from_search_result(res):
            if external_url == link:
                continue
            ext_score, ext_note = _owned_site_score(
                name, external_url, title, snippet, source="external_url_in_result"
            )
            if ext_score >= 8:
                candidates.append((ext_score, external_url, ext_note))
    if not candidates:
        return None
    # Deterministic tie-breaker: higher score first, then shorter/home URL.
    candidates.sort(key=lambda x: (x[0], -len(x[1])), reverse=True)
    return candidates[0][1]

# ============================================================================
# 5. STAGE 3  —  FETCH a few pages
# ============================================================================
WANTED_PAGES = ("about", "who-we-are", "programs", "programmes", "what-we-do",
                "partners", "supporters", "donors", "our-work")

def _looks_like_html_response(r):
    ct = (r.headers.get("content-type") or "").lower()
    return ("text/html" in ct) or ("application/xhtml" in ct) or ct == ""

def _fetch_site_variants(url):
    raw = str(url or "").strip()
    if not raw:
        return []
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    host = (p.hostname or "").lower()
    path = p.path or "/"
    variants = [raw]
    if host:
        alt = host[4:] if host.startswith("www.") else "www." + host
        variants.append(f"https://{alt}{path}")
        if p.scheme == "https":
            variants.append(f"http://{host}{path}")
    return list(dict.fromkeys(variants))


def fetch_site_text(url):
    pages_text, socials = [], set()
    r = None
    fetched_url = url
    errors = []
    for candidate in _fetch_site_variants(url):
        rr = http_get(candidate, FETCH_TIMEOUT)
        if rr is not None and rr.status_code == 200 and _looks_like_html_response(rr):
            r = rr
            fetched_url = getattr(rr, "url", candidate) or candidate
            break
        code = getattr(rr, "status_code", "no_response") if rr is not None else "no_response"
        errors.append(f"{candidate}:{code}")
    if r is None:
        return "", [], f"could not fetch {url}; tried " + ", ".join(errors)
    base = re.match(r"^(https?://[^/]+)", fetched_url)
    base = base.group(1) if base else fetched_url
    soup = BeautifulSoup(_response_text_limited(r), "html.parser")
    pages_text.append(_visible(soup))
    socials |= _socials(soup)
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href_raw = a["href"].strip()
        href = href_raw.lower()
        if any(w in href for w in WANTED_PAGES):
            full = urljoin(base + "/", href_raw)
            if full not in seen and urlparse(full).netloc == urlparse(base).netloc:
                seen.add(full); links.append(full)
        if len(links) >= MAX_PAGES_PER_NGO - 1:
            break
    for ln in links:
        rr = http_get(ln, FETCH_TIMEOUT)
        if rr is not None and rr.status_code == 200 and _looks_like_html_response(rr):
            s2 = BeautifulSoup(_response_text_limited(rr), "html.parser")
            pages_text.append(_visible(s2))
            socials |= _socials(s2)
    return ("\n".join(pages_text))[:6000], sorted(socials), None

def _visible(soup):
    for t in soup(["script", "style", "noscript"]):
        t.extract()
    txt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    # also pull partner logos' alt text + filenames (partner walls are often images)
    alts = " ".join((img.get("alt") or "") + " " + (img.get("src") or "").split("/")[-1]
                    for img in soup.find_all("img"))
    return (txt + " " + alts)[:4000]

def _socials(soup):
    out = set()
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        for net in ("facebook.com", "instagram.com", "linkedin.com", "youtube.com", "twitter.com", "x.com"):
            if net in h:
                out.add(net.split(".")[0].capitalize())
    return out

# ============================================================================
# 6. STAGE 3.5  —  CHILDREN FILTER  (deliberately VERY generous)
# ----------------------------------------------------------------------------
#  Cheap pre-filter only. Keep plausible child rows for AI classification,
#  but drop obvious non-child pages early. The stricter Avika-fit decision
#  happens after AI profiling and is hidden from the reviewer-facing output.
# ============================================================================
CHILD_WORDS = ("child", "children", "kid", "boy", "girl", "student", "school",
               "education", "learning", "youth", "adolescent", "teen", "infant",
               "baby", "toddler", "orphan", "nutrition", "midday", "mid-day",
               "anganwadi", "scholarship", "exam", "tuition", "shiksha", "bal",
               "paediatric", "pediatric", "juvenile", "minor", "underprivileged",
               "slum", "first-generation", "drop-out", "dropout", "literacy",
               "sport", "athlete", "academy", "coaching", "talent", "vidyalaya",
               "creche", "crèche", "early childhood", "foster", "rescue",
               "trafficking", "labour", "labor", "marriage", "immunization",
               "vaccination", "malnutrition", "stunting")
NOT_CHILD_WORDS = ("old age", "old-age", "elderly", "senior citizen", "geriatric",
                   "vridha", "widow home", "destitute women only")

def is_about_children(text, cause_hint=""):
    blob = (text + " " + cause_hint).lower()
    has_child = any(w in blob for w in CHILD_WORDS)
    if has_child:
        return True, "child signal present"
    # only drop if it clearly reads as a non-child org AND has no child words
    if any(w in blob for w in NOT_CHILD_WORDS):
        return False, "obviously not about children (no child signal)"
    # ambiguous / thin text -> KEEP (generous, as instructed)
    return True, "ambiguous — kept on purpose"

# ============================================================================
# 6.5 HIGH-CONFIDENCE AVIKA RULE PREFILTER
# ----------------------------------------------------------------------------
# Rejects only obvious non-fits. It never auto-approves an NGO. Ambiguous and
# plausibly child-focused rows always continue to Haiku.
# ============================================================================

_AVIKA_WRONG_SOURCE_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be",
    "wikipedia.org", "justdial.com", "indiamart.com", "sulekha.com",
    "ngosindia.org", "ngo4you.com", "give.do", "globalgiving.org",
    "guidestarindia.org", "thebetterindia.com", "medium.com", "blogspot.com",
    "wordpress.com", "google.com", "google.co.in", "maps.google.com",
}

_AVIKA_CHILD_CORE_TERMS = (
    "underprivileged child", "underserved child", "vulnerable child", "orphan",
    "children's home", "childrens home", "child care institution", "cci",
    "street child", "child labour", "child labor", "school dropout", "dropout child",
    "government school", "anganwadi", "free education", "free school",
    "subsidised education", "subsidized education", "low income student",
    "rural child", "slum child", "special needs child", "children with disabilities",
    "residential school for", "shelter home for children", "malnutrition",
    "mid day meal", "mid-day meal", "nutrition for children", "learning centre",
    "learning center", "bridge school", "remedial education",
)

_AVIKA_CHILD_PROGRAM_TERMS = (
    "child", "children", "student", "school", "education", "learning", "orphan",
    "anganwadi", "adolescent", "youth", "special needs", "disability", "nutrition",
    "residential", "shelter", "creche", "crèche", "sports for", "arts for",
)

_AVIKA_EDUCATION_PROGRAM_TERMS = (
    "school", "education", "learning", "classroom", "teacher", "student",
    "special education", "vocational training for children", "remedial",
)

_AVIKA_REASON_LABELS = {
    "higher_education": "Higher-education or professional-institute identity",
    "health_only": "Hospital, clinic, medical fundraising, or health-only identity",
    "elderly_care": "Elderly or senior-citizen care without a child programme",
    "adult_livelihood_skilling": "Adult livelihood, SHG, farmer, or women-only skilling focus",
    "spiritual_religious": "Religious or spiritual body without a clear child programme",
    "cultural_community_association": "Community, caste, cultural, or membership association without a clear child programme",
    "fee_charging_school_likely": "Clearly private or premium fee-charging school",
    "wrong_source_or_blog": "Directory, social page, news/blog article, or other non-owned source",
}


def _avika_text(*parts):
    value = " ".join(str(x or "") for x in parts)
    value = html.unescape(value).lower()
    return re.sub(r"\s+", " ", value).strip()


def _avika_domain(url):
    try:
        return (urlparse(str(url or "")).netloc or "").lower().split(":", 1)[0].removeprefix("www.")
    except Exception:
        return ""


def _contains_any(blob, terms):
    return any(term in blob for term in terms)


def _count_terms(blob, terms):
    return sum(1 for term in terms if term in blob)


def deterministic_avika_exclusion(ngo, website, site_text):
    """Return an obvious rejection dict, or None when Haiku should decide.

    Rules are deliberately asymmetric: they can reject only high-confidence
    non-fits and can never shortlist/approve an NGO.
    """
    if not AVIKA_RULE_PREFILTER:
        return None

    name = _avika_text((ngo or {}).get("name", ""))
    text = _avika_text(site_text)
    blob = _avika_text(name, text)
    domain = _avika_domain(website)
    path = ""
    try:
        path = (urlparse(str(website or "")).path or "").lower()
    except Exception:
        pass

    child_core = _contains_any(blob, _AVIKA_CHILD_CORE_TERMS)
    child_program = child_core or _count_terms(blob, _AVIKA_CHILD_PROGRAM_TERMS) >= 2
    education_program = _contains_any(blob, _AVIKA_EDUCATION_PROGRAM_TERMS)

    if any(domain == d or domain.endswith("." + d) for d in _AVIKA_WRONG_SOURCE_DOMAINS):
        return {"reason_code": "wrong_source_or_blog", "reason": _AVIKA_REASON_LABELS["wrong_source_or_blog"]}
    if re.search(r"/(news|article|articles|directory|listing|listings|amp)(/|$)", path) and not child_core:
        return {"reason_code": "wrong_source_or_blog", "reason": _AVIKA_REASON_LABELS["wrong_source_or_blog"]}

    higher_ed_name = re.search(
        r"\b(university|degree college|engineering college|medical college|nursing college|law college|"
        r"polytechnic|pu college|pre university college|institute of technology|management institute|"
        r"business school|b\.?ed\.? college|pharmacy college|professional college)\b",
        name,
    )
    higher_ed_terms = (
        "undergraduate", "postgraduate", "bachelor degree", "master degree", "doctoral",
        "ph.d", "degree programmes", "degree programs", "university affiliation",
        "campus placements", "placement cell", "admission to courses", "semester",
    )
    if higher_ed_name or (_count_terms(text, higher_ed_terms) >= 3 and not child_core):
        return {"reason_code": "higher_education", "reason": _AVIKA_REASON_LABELS["higher_education"]}

    elderly_terms = (
        "old age home", "old-age home", "senior citizen home", "elderly care",
        "geriatric care", "home for the aged", "vriddhashrama", "vridha ashrama",
    )
    if _contains_any(blob, elderly_terms) and not child_program:
        return {"reason_code": "elderly_care", "reason": _AVIKA_REASON_LABELS["elderly_care"]}

    medical_name = re.search(
        r"\b(hospital|clinic|medical centre|medical center|diagnostic centre|diagnostic center|"
        r"cancer hospital|eye hospital|nursing home)\b",
        name,
    )
    health_terms = (
        "outpatient", "inpatient", "surgery", "surgeries", "diagnostic services",
        "hospital beds", "medical treatment", "patient care", "clinical services",
        "health camp", "blood donation", "dialysis", "ambulance service",
    )
    health_strength = _count_terms(text, health_terms)
    if (medical_name or health_strength >= 4) and not education_program and not child_core:
        return {"reason_code": "health_only", "reason": _AVIKA_REASON_LABELS["health_only"]}

    livelihood_terms = (
        "self help group", "self-help group", "shg", "farmer producer", "farmers",
        "agriculture training", "dairy farmers", "micro enterprise", "micro-enterprise",
        "women entrepreneurship", "women livelihood", "adult vocational training",
        "tailoring for women", "income generation for women", "rural livelihood",
    )
    if _count_terms(blob, livelihood_terms) >= 2 and not child_program:
        return {"reason_code": "adult_livelihood_skilling", "reason": _AVIKA_REASON_LABELS["adult_livelihood_skilling"]}

    religious_name = re.search(
        r"\b(temple|church|mosque|masjid|dargah|devasthan|mutt|matha|math|ashram|"
        r"spiritual mission|satsang|gurudwara|religious society)\b",
        name,
    )
    religious_text_terms = (
        "daily pooja", "daily puja", "religious discourse", "spiritual discourse",
        "devotees", "pilgrimage", "temple festival", "church ministry", "prayer meeting",
        "religious activities", "propagation of religion",
    )
    if religious_name and _contains_any(text, religious_text_terms) and not child_program:
        return {"reason_code": "spiritual_religious", "reason": _AVIKA_REASON_LABELS["spiritual_religious"]}

    community_name = re.search(
        r"\b(community association|welfare association|seva sangha|seva sangh|brahmana sangha|"
        r"samaja|samaj|caste association|residents association|merchant association|"
        r"professional association|employees association|youth club)\b",
        name,
    )
    community_terms = (
        "membership", "members of the community", "community hall", "annual gathering",
        "cultural festival", "marriage bureau", "association members", "office bearers",
    )
    if community_name and _contains_any(text, community_terms) and not child_program:
        return {"reason_code": "cultural_community_association", "reason": _AVIKA_REASON_LABELS["cultural_community_association"]}

    school_identity = re.search(r"\b(international school|public school|world school|global school|academy)\b", name)
    fee_terms = (
        "fee structure", "tuition fee", "annual fee", "admission fee", "apply for admission",
        "admissions open", "school fees", "transport fee", "hostel fee",
    )
    premium_terms = (
        "international curriculum", "cambridge curriculum", "ib curriculum", "state of the art",
        "state-of-the-art", "smart classrooms", "air conditioned", "air-conditioned",
        "world class campus", "premium boarding", "luxury", "cbse", "icse",
    )
    explicit_access_terms = (
        "free education", "no fees", "fully subsidised", "fully subsidized", "low income",
        "underprivileged", "economically weaker", "scholarship for poor", "rural poor",
    )
    if school_identity and _contains_any(text, fee_terms) and _count_terms(text, premium_terms) >= 2 and not _contains_any(text, explicit_access_terms):
        return {"reason_code": "fee_charging_school_likely", "reason": _AVIKA_REASON_LABELS["fee_charging_school_likely"]}

    return None


def compact_avika_evidence(site_text, max_chars=None):
    """Select high-signal snippets instead of paying to send the whole website."""
    text = re.sub(r"\s+", " ", html.unescape(str(site_text or ""))).strip()
    cap = max(600, int(max_chars or AVIKA_SITE_TEXT_CHARS))
    if len(text) <= cap:
        return text

    signals = tuple(dict.fromkeys(
        _AVIKA_CHILD_CORE_TERMS
        + _AVIKA_CHILD_PROGRAM_TERMS
        + (
            "fee", "admission", "college", "university", "hospital", "clinic", "elderly",
            "senior citizen", "self help group", "farmer", "livelihood", "religious",
            "spiritual", "community association", "free", "subsid", "government school",
        )
    ))
    sentences = re.split(r"(?<=[.!?])\s+|\s*[|•·]\s*", text)
    selected = []
    seen = set()

    def add(part):
        part = re.sub(r"\s+", " ", str(part or "")).strip()
        if not part:
            return
        key = part.lower()
        if key in seen:
            return
        seen.add(key)
        selected.append(part)

    add(text[:320])
    scored = []
    for idx, sentence in enumerate(sentences):
        low = sentence.lower()
        hits = sum(1 for term in signals if term in low)
        if hits:
            scored.append((hits, -idx, sentence))
    for _hits, _neg_idx, sentence in sorted(scored, reverse=True):
        add(sentence)
        if len(" ".join(selected)) >= cap:
            break
    joined = " ".join(selected)
    if len(joined) < min(cap, 900):
        joined = (joined + " " + text[320:cap]).strip()
    return joined[:cap]

# ============================================================================
# 7. STAGE 4  —  AI PROFILING via the Message Batches API (Claude Haiku)
# ----------------------------------------------------------------------------
#  Batches run on far higher rate limits than a normal loop and cost ~50% less.
#  Each request is tagged with the NGO id so results map back exactly.
# ============================================================================
from anthropic import Anthropic
client = Anthropic(api_key=ANTHROPIC_API_KEY)

PROFILE_PROMPT = '''You are filtering Indian NGOs for Feeding India's child-focused NGO discovery shortlist.
Your job is NOT to keep every organisation that mentions children. Your job is to decide whether this is a reviewable lead for an underserved-child education/care/nutrition partnerships team.

Return ONLY a JSON object, nothing else.

Decision logic:
- decision = yes only when underserved/vulnerable children are a core beneficiary group AND there is an ongoing education, learning, childcare, residential, shelter, special-needs, government-school/anganwadi, sport/arts/STEM-for-poor-children, or nutrition/care program where regular support could realistically plug in.
- decision = maybe when the direction is relevant but fees, access, impact, or program details are unclear. Keep fees-unclear rural schools, special-needs schools, CCIs/children's homes, orphanages, anganwadi/govt-school work, and thin-but-plausible underprivileged-child programs as maybe.
- decision = no when the primary identity is fee-charging/private school, premium academy, college/PU/university/professional institute, coaching centre, hospital/medical fundraising/health-only, elderly care, adult livelihood/SHG/farmers/women skilling, religious/spiritual/cultural/community association, trade/professional body, one-off scholarship/prize-only program, unrelated issue, wrong website, blog/news/listing-only source, placeholder/unfinished site, or broad charity where children are peripheral.
- Do not reject merely because fees are unclear. Reject when high fees, private/premium school indicators, or higher-ed/professional-fee model is clear.
- Low confidence due to bad source/wrong website/blog/listing/unrelated page should be no. Low confidence due to thin but plausible underserved-child signal can be maybe.

Return schema:
{{
 "official_website_match": "yes | no | maybe",
 "confidence": "high | medium | low",
 "decision": "yes | maybe | no",
 "reason_code": "free_low_income_school | govt_school_intervention | anganwadi_early_childhood | migrant_construction_children | child_labour_street_child | cci_orphanage_residential | special_needs_children | rural_slum_learning_centre | sports_arts_stem_underserved | food_nutrition_linked | fees_unclear | thin_impact_detail | child_program_unclear | broad_with_possible_child_arm | fee_charging_school_likely | higher_education | adult_livelihood_skilling | health_only | elderly_care | spiritual_religious | cultural_community_association | broad_charity_children_peripheral | scholarship_prize_only | vague_unfinished | wrong_source_or_blog | unrelated",
 "internal_reason": "short explanation of why it was yes/maybe/no",
 "fees_access_signal": "free_or_subsidised | underserved_explicit | fees_unclear | fee_charging_likely | high_fees | higher_ed_fee_model | not_applicable",
 "program_type": "free_school | private_school | govt_school_intervention | anganwadi | daycare_creche | cci_orphanage_residential | special_needs | learning_centre_tuition | sports_arts_stem | nutrition_food | broad_charity | health | adult_livelihood | elderly | religious_spiritual | cultural_community | college_higher_ed | scholarship_only | unrelated | unclear",
 "plug_in_potential": "high | medium | low | unknown",
 "serves": "who the NGO serves, short",
 "summary": "25-35 word description of what they do",
 "story": "one notable human or impact detail, or empty string",
 "cause_tags": ["children","education","nutrition","health","welfare","sport","..."],
 "digital_presence": "high | medium | low",
 "location": "city or town the NGO operates in, or empty string",
 "partners_found": [{{"name":"...","type":"corporate | corporate_foundation | foundation | service_club | government | unknown","relationship":"supporter | funder | partner","confidence":"high | medium | low"}}]
}}

NGO name: {name}
District/State hint: {geo}
Website text:
---BEGIN WEBSITE TEXT---
{text}
---END WEBSITE TEXT---'''

COMPACT_PROFILE_PROMPT = '''Classify this Indian NGO for Feeding India's Avika filter. Return JSON only.

YES: vulnerable/underserved children are a core group in an ongoing education, care, residential/shelter, special-needs, government-school/anganwadi, underserved sport/arts/STEM, or food/nutrition programme.
MAYBE: plausibly relevant but access, fees, programme depth, or evidence is unclear. Thin but plausible child evidence stays maybe.
NO: private/premium school; higher education/coaching-only; health-only; elderly; adult livelihood/SHG/farmers/women-only skilling; religious/community body; scholarship-only; broad charity with children peripheral; wrong/non-owned source; unrelated/unfinished site.

Return exactly: {{"m":"yes|maybe|no","d":"yes|maybe|no","c":"high|medium|low","r":"code","x":"20-35 word plain-English description"}}
m=official-site match; d=DFP-fit decision. r must be one of: child_fit, child_unclear, fees_unclear, private_school, higher_ed, health, elderly, adult_livelihood, religious_community, scholarship_only, broad_peripheral, bad_source, unrelated.
x must describe the organisation/programme, not repeat the decision and not invent evidence.

NGO: {name}
Location: {geo}
URL: {website}
Evidence: {text}'''


def _compact_reason_text(code):
    return _AVIKA_REASON_LABELS.get(str(code or "").strip().lower(), str(code or "").replace("_", " ").strip())


_COMPACT_REASON_CODE_MAP = {
    "child_fit": "thin_impact_detail",
    "child_unclear": "child_program_unclear",
    "fees_unclear": "fees_unclear",
    "private_school": "fee_charging_school_likely",
    "higher_ed": "higher_education",
    "health": "health_only",
    "elderly": "elderly_care",
    "adult_livelihood": "adult_livelihood_skilling",
    "religious_community": "cultural_community_association",
    "scholarship_only": "scholarship_prize_only",
    "broad_peripheral": "broad_charity_children_peripheral",
    "bad_source": "wrong_source_or_blog",
    "unrelated": "unrelated",
}


def normalize_avika_profile(profile):
    if not isinstance(profile, dict):
        return {"_error": "ai reply was not a JSON object"}
    if profile.get("_error"):
        return profile
    website_match = str(profile.get("official_website_match", profile.get("m", "")) or "").strip().lower()
    decision = str(profile.get("decision", profile.get("d", "")) or "").strip().lower()
    confidence = str(profile.get("confidence", profile.get("c", "")) or "").strip().lower()
    reason_code = str(profile.get("reason_code", profile.get("r", "")) or "").strip().lower()
    reason_code = _COMPACT_REASON_CODE_MAP.get(reason_code, reason_code)
    if website_match not in {"yes", "maybe", "no"}:
        website_match = "maybe"
    if decision not in {"yes", "maybe", "no"}:
        return {"_error": "ai decision missing or invalid"}
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if not reason_code:
        reason_code = "child_program_unclear" if decision == "maybe" else ("unrelated" if decision == "no" else "thin_impact_detail")
    normalized = dict(profile)
    brief = str(profile.get("summary") or profile.get("x") or "").strip()
    normalized.update({
        "official_website_match": website_match,
        "decision": decision,
        "confidence": confidence,
        "reason_code": reason_code,
        "internal_reason": str(profile.get("internal_reason") or _compact_reason_text(reason_code)),
        "summary": brief,
    })
    for key, default in {
        "serves": "", "story": "", "location": "",
        "digital_presence": "", "partners_found": [], "cause_tags": [],
    }.items():
        normalized.setdefault(key, default)
    return normalized


def _ai_prompt_for_item(it):
    if AVIKA_CLASSIFICATION_ONLY:
        return COMPACT_PROFILE_PROMPT.format(
            name=it.get("name", ""),
            geo=" ".join(x for x in (it.get("district", ""), it.get("state", "")) if x),
            website=it.get("website", ""),
            text=compact_avika_evidence(it.get("site_text", ""), AVIKA_SITE_TEXT_CHARS),
        )
    return PROFILE_PROMPT.format(
        name=it.get("name", ""),
        geo=" ".join(x for x in (it.get("district", ""), it.get("state", "")) if x),
        text=(it.get("site_text") or "")[:4000],
    )


def _ai_max_tokens():
    return AVIKA_MAX_TOKENS if AVIKA_CLASSIFICATION_ONLY else 700


def build_batch_requests(items):
    reqs = []
    for it in items:
        reqs.append({
            "custom_id": it["id"],
            "params": {
                "model": HAIKU_MODEL,
                "max_tokens": _ai_max_tokens(),
                "messages": [{"role": "user", "content": _ai_prompt_for_item(it)}]
            }
        })
    return reqs

def run_ai_batch(items):
    """Submit or resume one Claude batch, wait safely, return {id: parsed_json}.

    Safeguards:
    - Saves batch_id before waiting, so Colab reruns do not create duplicate paid batches.
    - Never polls forever; after MAX_BATCH_WAIT_SEC it returns partial/no results and rerun resumes.
    - Handles unexpected/terminal statuses by logging and returning safely.
    """
    if not items:
        return {}

    sig = _batch_signature(items)
    batches = load_ai_batches()
    batch_id = None

    if sig in batches:
        batch_id = batches[sig].get("batch_id")
        write_status(
            "ai_batch_resume",
            "Resuming existing Claude batch",
            current_item=f"{len(items)} NGO profiles",
            module="repository",
            extra={"batch_id": batch_id},
        )
    else:
        try:
            write_status(
                "ai_batch_create",
                "Creating Claude batch for qualified NGOs",
                current_item=f"{len(items)} NGO profiles",
                module="repository",
            )
            batch = client.messages.batches.create(requests=build_batch_requests(items))
            batch_id = batch.id
            checkpoint_ai_batch(sig, batch_id, len(items))
        except Exception as e:
            _raise_if_anthropic_capacity_error(e)
            for it in items:
                log_error(it["id"], it["name"], "ai_batch_create", e)
            write_status("ai_batch_create_failed", "Claude batch could not be created", ok=False, error=e, module="repository")
            return {}

    start = time.time()
    terminal_bad = {"errored", "expired", "canceled", "cancelled", "failed"}

    while True:
        try:
            b = client.messages.batches.retrieve(batch_id)
            status = getattr(b, "processing_status", "unknown")
        except Exception as e:
            _raise_if_anthropic_capacity_error(e)
            write_status("ai_batch_poll_retry", "Temporary error while checking Claude batch", error=e, module="repository", extra={"batch_id": batch_id})
            time.sleep(15)
            if time.time() - start > MAX_BATCH_WAIT_SEC:
                log_error(batch_id, "Claude batch", "ai_batch_poll_timeout", f"Timed out while polling after exception: {e}")
                return {}
            continue

        write_status(
            "ai_batch_polling",
            f"Claude batch status: {status}",
            current_item=f"{len(items)} NGO profiles",
            module="repository",
            extra={"batch_id": batch_id, "batch_status": status, "elapsed_seconds": int(time.time() - start)},
        )

        if status == "ended":
            break
        if str(status).lower() in terminal_bad:
            log_error(batch_id, "Claude batch", "ai_batch_terminal_status", f"Terminal status reached: {status}")
            write_status("ai_batch_terminal_status", f"Claude batch ended with status: {status}", ok=False, error=status, module="repository", extra={"batch_id": batch_id})
            return {}
        if time.time() - start > MAX_BATCH_WAIT_SEC:
            log_error(batch_id, "Claude batch", "ai_batch_wait_limit", f"Still not ended after {MAX_BATCH_WAIT_SEC}s; rerun will resume same batch")
            write_status("ai_batch_wait_limit", "Claude batch still running; rerun later to resume", ok=True, run_status="waiting", module="repository", extra={"batch_id": batch_id, "batch_status": status})
            return {}
        time.sleep(20)

    out = {}
    try:
        write_status("ai_batch_read_results", "Reading Claude batch results", module="repository", extra={"batch_id": batch_id})
        for result in client.messages.batches.results(batch_id):
            cid = result.custom_id
            try:
                if result.result.type == "succeeded":
                    txt = "".join(blk.text for blk in result.result.message.content if blk.type == "text")
                    out[cid] = normalize_avika_profile(_safe_json(txt))
                else:
                    out[cid] = {"_error": f"ai result {result.result.type}"}
            except Exception as e:
                out[cid] = {"_error": f"ai parse: {e}"}
    except Exception as e:
        _raise_if_anthropic_capacity_error(e)
        log_error(batch_id, "Claude batch", "ai_batch_results_read", e)
        write_status("ai_batch_results_read_failed", "Could not read Claude batch results", ok=False, error=e, module="repository", extra={"batch_id": batch_id})
    return out

def _anthropic_retry_delay(err, attempt):
    """Return a safe delay for Anthropic direct-call retries.

    Anthropic 429s usually include retry-after. The SDK exception shape can vary,
    so this reads headers defensively and falls back to exponential backoff.
    """
    fallback = min(2 ** attempt, 30)
    try:
        response = getattr(err, "response", None)
        headers = getattr(response, "headers", None) or getattr(err, "headers", None) or {}
        retry_after = None
        if hasattr(headers, "get"):
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            return max(1, min(int(float(retry_after)), 60))
    except Exception:
        pass
    return fallback

def _anthropic_status_code(err):
    try:
        return int(getattr(err, "status_code"))
    except Exception:
        pass
    try:
        response = getattr(err, "response", None)
        return int(getattr(response, "status_code"))
    except Exception:
        return None

def _one_direct_ai_profile(it):
    """Profile one NGO using normal Claude Messages API.

    Used only for Rapid Mode / very small runs. It honours 429 Retry-After and
    retries safely. Bulk Mode stays on Batch for cost + rate-limit safety.
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            msg = client.messages.create(
                model=HAIKU_MODEL,
                max_tokens=_ai_max_tokens(),
                messages=[{"role": "user", "content": _ai_prompt_for_item(it)}],
            )
            txt = "".join(blk.text for blk in msg.content if getattr(blk, "type", "") == "text")
            return it["id"], normalize_avika_profile(_safe_json(txt))
        except Exception as e:
            _raise_if_anthropic_capacity_error(e)
            last_err = e
            code = _anthropic_status_code(e)
            delay = _anthropic_retry_delay(e, attempt) if code == 429 else min(2 ** attempt, 12)
            write_status(
                "ai_direct_retry",
                f"Rapid AI retry {attempt}/{MAX_RETRIES}" + (" after rate limit" if code == 429 else ""),
                current_item=it.get("name", ""),
                module="repository",
                extra={"ai_profile_mode": "direct", "status_code": code, "sleep_seconds": delay},
            )
            time.sleep(delay)
    log_error(it.get("id", ""), it.get("name", ""), "ai_direct", last_err)
    return it["id"], {"_error": f"direct ai failed: {last_err}"}

def run_ai_direct(items):
    """Fast path for Rapid Mode. Returns {id: parsed_json}.

    Why this exists: Claude Batch is correct for large bulk runs, but for
    3-20 row Rapid Mode the batch queue/polling overhead can feel very slow.
    Direct calls are more expensive per token, but the row cap keeps cost tiny.
    """
    if not items:
        return {}
    out = {}
    workers = min(DIRECT_AI_CONCURRENCY, len(items))
    write_status(
        "ai_direct_start",
        f"Profiling {len(items)} NGOs directly for Rapid Mode",
        current_item=f"{len(items)} NGO profiles",
        module="repository",
        extra={"ai_profile_mode": "direct", "direct_ai_concurrency": workers},
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_one_direct_ai_profile, it): it for it in items}
        completed = 0
        for fut in as_completed(futures):
            it = futures[fut]
            completed += 1
            try:
                ngo_id, profile = fut.result()
                out[ngo_id] = profile
            except ProviderPauseRequested:
                raise
            except Exception as e:
                log_error(it.get("id", ""), it.get("name", ""), "ai_direct_future", e)
                out[it["id"]] = {"_error": f"direct ai future failed: {e}"}
            write_status(
                "ai_direct_polling",
                f"Rapid AI profiling {completed}/{len(items)}",
                current_item=it.get("name", ""),
                total=len(items),
                done=completed,
                module="repository",
                extra={"ai_profile_mode": "direct", "ai_profiles_completed_this_stage": completed},
            )
    write_status(
        "ai_direct_complete",
        "Rapid AI profiling complete",
        current_item=f"{len(out)} NGO profiles",
        total=len(items),
        done=len(out),
        module="repository",
        extra={"ai_profile_mode": "direct"},
    )
    return out

def _safe_json(txt):
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        return {"_error": "no json in ai reply"}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"_error": "ai json parse failed"}

# ============================================================================
# 8. RUN  —  the main loop (search + fetch + filter), checkpointing every NGO
# ============================================================================
def _search_fetch_one(ngo, total, processed_hint):
    rec = {"id": ngo["id"], "name": ngo["name"], "district": ngo["district"],
           "state": ngo["state"], "status": "", "website": "", "site_text": "",
           "socials": [], "note": ""}
    try:
        write_status("processing_ngo", "Starting NGO row", current_item=ngo["name"], total=total, done=processed_hint)
        supplied_url = str(ngo.get("website") or "").strip()
        if supplied_url:
            url, serr = supplied_url, None
            rec["note"] = "using supplied website; Serper search skipped"
        elif SERPER_QUERIES_PER_NGO <= 0:
            rec["status"] = "missing_supplied_website"
            rec["note"] = "Avika classification requires a supplied website; no Serper search was allowed"
            return rec, None
        else:
            url, _organic, serr = find_official_site(ngo, total=total, done=processed_hint)
        if serr:
            rec["status"] = "search_failed"; rec["note"] = log_error(ngo["id"], ngo["name"], "search", serr)
            return rec, None
        if not url:
            rec["status"] = "no_official_website"; rec["note"] = "no owned site in results"
            return rec, None
        rec["website"] = url
        write_status("fetching_website", "Fetching official website text", current_item=ngo["name"], current_url=url, total=total, done=processed_hint)
        text, socials, ferr = fetch_site_text(url)
        rec["socials"] = socials
        if ferr and not text:
            rec["status"] = "fetch_failed"; rec["note"] = log_error(ngo["id"], ngo["name"], "fetch", ferr)
            return rec, None
        exclusion = deterministic_avika_exclusion(ngo, url, text)
        if exclusion:
            rec["status"] = "prefilter_rejected"
            rec["prefilter_reason_code"] = exclusion.get("reason_code", "rule_rejected")
            rec["note"] = "Rule prefilter: " + exclusion.get("reason", "obvious non-fit")
            return rec, None
        keep, why = is_about_children(text)
        if not keep:
            rec["status"] = "dropped_not_children"; rec["note"] = why
            return rec, None
        rec["status"] = "ready_for_ai"; rec["site_text"] = text; rec["note"] = why
        return rec, {**ngo, "site_text": text, "socials": socials, "website": url}
    except ProviderPauseRequested:
        raise
    except Exception:
        rec["status"] = "skipped_error"
        rec["note"] = log_error(ngo["id"], ngo["name"], "loop", traceback.format_exc().splitlines()[-1])
        return rec, None

def run():
    print("Loading and de-duplicating the list ...")
    write_status("loading_input", "Loading and de-duplicating input CSV by NGO name + district + state", run_status="starting")
    ngos = clean_and_load()
    done = load_done_ids()
    retryable_statuses = {"fetch_failed", "search_failed", "skipped_error"}
    todo = []
    for ngo in ngos:
        previous = done.get(ngo["id"])
        if not previous:
            todo.append(ngo)
            continue
        if previous.get("status") in retryable_statuses:
            retry_ngo = dict(ngo)
            # A fetch retry should reuse the candidate already found and avoid
            # spending another Serper query. Search failures still run search again.
            if previous.get("status") == "fetch_failed" and previous.get("website"):
                retry_ngo["website"] = previous.get("website")
            todo.append(retry_ngo)
    settled = max(0, len(ngos) - len(todo))
    print(f"{len(ngos)} unique NGOs | {settled} settled | {len(todo)} new/retry rows to process")
    duplicate_count = 0
    try:
        if os.path.exists(DUPLICATE_CANDIDATES_CSV):
            with open(DUPLICATE_CANDIDATES_CSV, "r", encoding="utf-8-sig") as f:
                duplicate_count = max(0, sum(1 for _ in f) - 1)
    except Exception:
        duplicate_count = 0
    write_status("input_loaded", f"{len(ngos)} unique NGO+location rows loaded; {duplicate_count} exact duplicate rows skipped; {len(todo)} to process", total=len(ngos), done=len(done), extra={"duplicate_rows_skipped": duplicate_count})

    staged = []  # NGOs that passed filters and need AI profiling
    completed_search_rows = 0
    workers = min(max(1, BULK_SEARCH_CONCURRENCY), max(1, len(todo)))
    write_status(
        "search_fetch_parallel_start",
        f"Running search/fetch with {workers} concurrent workers",
        total=len(ngos), done=settled,
        extra={"bulk_search_concurrency": workers, "serper_key_stats": _serper_key_stats()},
    )
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_search_fetch_one, ngo, len(ngos), settled + idx): ngo
                for idx, ngo in enumerate(todo)
            }
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Search + fetch", unit="ngo"):
                ngo = futures[fut]
                try:
                    rec, staged_item = fut.result()
                except ProviderPauseRequested:
                    raise
                except Exception:
                    rec = {"id": ngo["id"], "name": ngo["name"], "district": ngo["district"],
                           "state": ngo["state"], "status": "skipped_error", "website": "",
                           "site_text": "", "socials": [],
                           "note": log_error(ngo["id"], ngo["name"], "future", traceback.format_exc().splitlines()[-1])}
                    staged_item = None
                checkpoint(rec)
                if staged_item:
                    staged.append(staged_item)
                completed_search_rows += 1
                write_status(
                    "search_fetch_parallel",
                    "Concurrent search/fetch in progress",
                    current_item=ngo["name"], current_url=rec.get("website", ""),
                    total=len(ngos), done=settled + completed_search_rows,
                    extra={"bulk_search_concurrency": workers, "serper_key_stats": _serper_key_stats()},
                )

    # ---- AI profiling in batches. This is now actually resumable. ----
    latest_done = load_done_ids()
    profiles = load_ai_profiles()
    ai_items = []
    for ngo in ngos:
        rec = latest_done.get(ngo["id"], {})
        if rec.get("status") == "ready_for_ai" and ngo["id"] not in profiles:
            ai_items.append({
                **ngo,
                "site_text": rec.get("site_text", ""),
                "socials": rec.get("socials", []),
                "website": rec.get("website", ""),
            })

    # Rapid mode should feel rapid: use normal Claude calls for tiny runs to avoid
    # Batch API queue/polling overhead. Bulk mode stays on Batch for cost/rate-limit safety.
    use_direct_ai = (
        AI_PROFILE_MODE == "direct"
        or (AI_PROFILE_MODE == "auto" and DFP_RUN_MODE == "rapid" and len(ai_items) <= DIRECT_AI_MAX_ITEMS)
    )

    if use_direct_ai:
        print(f"\nProfiling {len(ai_items)} qualified NGOs with Claude Haiku (direct Rapid Mode) ...")
        direct_profiles = run_ai_direct(ai_items)
        for ngo_id, profile in direct_profiles.items():
            checkpoint_ai_profile(ngo_id, profile)
            profiles[ngo_id] = profile
    else:
        print(f"\nProfiling {len(ai_items)} qualified NGOs with Claude Haiku (Batch API) ...")
        for i in range(0, len(ai_items), AI_BATCH_SIZE):
            chunk = ai_items[i:i + AI_BATCH_SIZE]
            print(f"  batch {i//AI_BATCH_SIZE + 1}: {len(chunk)} NGOs ...")
            write_status("ai_batch_start", f"Profiling batch {i//AI_BATCH_SIZE + 1}", current_item=f"{len(chunk)} NGOs", total=len(ai_items), done=i, extra={"ai_profile_mode": "batch"})
            batch_profiles = run_ai_batch(chunk)
            for ngo_id, profile in batch_profiles.items():
                checkpoint_ai_profile(ngo_id, profile)
                profiles[ngo_id] = profile

    latest_done = load_done_ids()
    ready_ids = [ngo["id"] for ngo in ngos if latest_done.get(ngo["id"], {}).get("status") == "ready_for_ai"]
    ai_expected = len(ready_ids)
    ai_completed = sum(1 for ngo_id in ready_ids if ngo_id in profiles)
    ai_missing = max(0, ai_expected - ai_completed)

    write_status("writing_audit", "Writing audit CSV", total=len(ngos), done=len(latest_done),
                 extra={"ai_profiles_expected": ai_expected, "ai_profiles_completed": ai_completed, "ai_profiles_missing": ai_missing})
    write_audit(ngos, latest_done, profiles)
    write_status("writing_repository_csv", "Writing shortlist repository CSV", total=len(ngos), done=len(latest_done),
                 extra={"ai_profiles_expected": ai_expected, "ai_profiles_completed": ai_completed, "ai_profiles_missing": ai_missing})
    output_counts = write_output(ngos, latest_done, profiles)
    rejected_rows = write_rejected_output(ngos, latest_done, profiles)
    donor_ok = True
    try:
        write_status("donor_lite_building", "Building donor-lite leads from already extracted partner names", module="donor_lite", total=len(ngos), done=len(latest_done),
                     extra={"ai_profiles_expected": ai_expected, "ai_profiles_completed": ai_completed, "ai_profiles_missing": ai_missing})
        write_donor_lite_output(ngos, latest_done, profiles)
    except Exception as e:
        # Donor-lite must never break the repository output, but final status must be honest.
        donor_ok = False
        log_error("donor-lite", "donor-lite", "write_donor_lite_output", traceback.format_exc().splitlines()[-1])

    downloads = {
        "repository": os.path.exists(OUTPUT_CSV),
        "donor-lite": os.path.exists(DONOR_OUTPUT_CSV),
        "audit": os.path.exists(AUDIT_CSV),
        "rejected": os.path.exists(REJECTED_CSV),
        "duplicates": os.path.exists(DUPLICATE_CANDIDATES_CSV),
        "errors": os.path.exists(ERROR_LOG),
        "status": os.path.exists(STATUS_JSON),
        "history": os.path.exists(GLOBAL_SCAN_HISTORY),
    }
    if ai_missing == 0 and donor_ok:
        write_status(
            "results_ready",
            "Repository run complete",
            run_status="complete",
            total=len(ngos),
            done=len(latest_done),
            extra={
                "result_quality": "complete",
                "ai_profiles_expected": ai_expected,
                "ai_profiles_completed": ai_completed,
                "ai_profiles_missing": ai_missing,
                "prefilter_rejected": _status_counts(latest_done).get("prefilter_rejected", 0),
                "avika_classification_only": AVIKA_CLASSIFICATION_ONLY,
                "avika_site_text_chars": AVIKA_SITE_TEXT_CHARS,
                "avika_max_tokens": _ai_max_tokens(),
                "shortlisted": output_counts.get("shortlisted", 0),
                "maybe": output_counts.get("maybe", 0),
                "rejected": output_counts.get("rejected", 0),
                "rejected_rows": rejected_rows,
                "history_appended": output_counts.get("history_appended", 0),
                "filter_version": FILTER_VERSION,
                "serper_queries_per_ngo": SERPER_QUERIES_PER_NGO,
                "downloads": downloads,
            },
        )
    else:
        warning_bits = []
        if ai_missing:
            warning_bits.append(f"{ai_missing} of {ai_expected} AI profiles were not completed")
        if not donor_ok:
            warning_bits.append("donor-lite generation failed")
        write_status(
            "partial_results_ready",
            "Repository outputs are available, but the run is partial/degraded",
            run_status="partial",
            ok=True,
            total=len(ngos),
            done=len(latest_done),
            extra={
                "result_quality": "partial",
                "warning": "; ".join(warning_bits),
                "ai_profiles_expected": ai_expected,
                "ai_profiles_completed": ai_completed,
                "ai_profiles_missing": ai_missing,
                "prefilter_rejected": _status_counts(latest_done).get("prefilter_rejected", 0),
                "avika_classification_only": AVIKA_CLASSIFICATION_ONLY,
                "avika_site_text_chars": AVIKA_SITE_TEXT_CHARS,
                "avika_max_tokens": _ai_max_tokens(),
                "shortlisted": output_counts.get("shortlisted", 0),
                "maybe": output_counts.get("maybe", 0),
                "rejected": output_counts.get("rejected", 0),
                "rejected_rows": rejected_rows,
                "history_appended": output_counts.get("history_appended", 0),
                "filter_version": FILTER_VERSION,
                "serper_queries_per_ngo": SERPER_QUERIES_PER_NGO,
                "downloads": downloads,
            },
        )
    print(f"\nDone. Wrote shortlist {OUTPUT_CSV}, duplicate audit {DUPLICATE_CANDIDATES_CSV}, rejected audit {REJECTED_CSV}, audit {AUDIT_CSV}, and donor-lite {DONOR_OUTPUT_CSV}. Any issues are in {ERROR_LOG}.")

# ============================================================================
# 8.8 HIDDEN SHORTLIST DECISION HELPERS
# ============================================================================
def _norm_decision(value):
    v = str(value or "").strip().lower()
    if v in {"yes", "y", "keep", "shortlist", "strong"}:
        return "yes"
    if v in {"maybe", "review", "needs_review", "needs review", "unclear"}:
        return "maybe"
    return "no"

NO_REASON_CODES = {
    "fee_charging_school_likely", "higher_education", "adult_livelihood_skilling",
    "health_only", "elderly_care", "spiritual_religious",
    "cultural_community_association", "broad_charity_children_peripheral",
    "scholarship_prize_only", "vague_unfinished", "wrong_source_or_blog", "unrelated",
}

def classify_profile(profile, rec):
    """Return hidden final action and internal reason.

    Main repository output shows only reviewable leads. This function decides what
    is reviewable while preserving filtered-out rows in audit/history files.
    """
    if not profile:
        return "rejected", "no_ai_profile", "AI profile not available"
    if profile.get("_error"):
        return "rejected", "ai_error", str(profile.get("_error"))[:300]
    website_match = str(profile.get("official_website_match") or "").lower().strip()
    if website_match == "no":
        return "rejected", "wrong_website", "AI says official website does not match"
    reason_code = str(profile.get("reason_code") or "").strip().lower()
    decision = _norm_decision(profile.get("decision"))
    conf = str(profile.get("confidence") or "").strip().lower()
    # Hard reject: if the model identifies an explicit Avika-style no pattern, keep it out of main output.
    if reason_code in NO_REASON_CODES or decision == "no":
        return "rejected", reason_code or "model_rejected", profile.get("internal_reason") or "Filtered out by Avika-fit classifier"
    # Low confidence is only allowed when the decision is clearly reviewable/maybe from owned-site text.
    if conf == "low" and decision not in {"yes", "maybe"}:
        return "rejected", reason_code or "low_confidence", profile.get("internal_reason") or "Low confidence and not reviewable"
    if decision == "yes":
        return "shortlisted", reason_code or "reviewable_yes", profile.get("internal_reason") or "Reviewable underserved-child program"
    if decision == "maybe":
        return "maybe", reason_code or "needs_review", profile.get("internal_reason") or "Potential lead; needs manual review"
    return "rejected", reason_code or "model_rejected", profile.get("internal_reason") or "Filtered out by classifier"

def _profile_reason(profile, rec):
    action, code, reason = classify_profile(profile, rec)
    return action, code, reason

def _csv_row_count(path):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return 0

def _append_global_history(rows):
    if not rows:
        return
    try:
        folder = os.path.dirname(os.path.abspath(GLOBAL_SCAN_HISTORY)) or "."
        os.makedirs(folder, exist_ok=True)
        exists = os.path.exists(GLOBAL_SCAN_HISTORY)
        fields = [
            "scan_date", "run_id", "filter_version", "model_version",
            "input_ngo_name", "normalized_ngo_name", "district", "state",
            "website_found", "website_domain", "final_action", "internal_reason_code",
            "internal_reason", "serper_queries_per_ngo"
        ]
        with open(GLOBAL_SCAN_HISTORY, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if not exists:
                w.writeheader()
            for row in rows:
                w.writerow(_safe_csv_dict({k: row.get(k, "") for k in fields}))
    except Exception as e:
        log_error("global-history", "global-history", "append", e)

def _safe_csv_dict(d):
    return {k: _safe_csv_cell(v) for k, v in d.items()}

# ============================================================================
# 9. WRITE THE CSV  (exactly the columns the website expects)
# ============================================================================
COLUMNS = [
    "NGO ID", "Source Record ID", "Batch ID", "Batch Label", "Source Type", "Source Run ID",
    "NGO Name", "Location", "State", "Website", "Avika Decision", "DFP Fit",
    "Avika Confidence", "Official Website Match", "Avika Reason Code", "Brief Description",
    "Serves", "Story", "Digital Presence", "Social Presence", "Partners", "Media Stories", "Notes",
]
REJECTED_COLUMNS = [
    "NGO ID", "Source Record ID", "Batch ID", "Batch Label", "Source Type", "Source Run ID",
    "NGO Name", "State", "District", "Website", "Avika Decision", "DFP Fit", "Brief Description",
    "Final Action", "Internal Reason Code", "Internal Reason", "Original Status",
    "AI Confidence", "Official Website Match",
]
AUDIT_COLUMNS = [
    "NGO ID", "Source Record ID", "Batch ID", "Batch Label", "Source Type", "Source Run ID",
    "NGO Name", "State", "District", "Status", "Website", "Avika Decision", "DFP Fit",
    "Brief Description", "Final Action", "Internal Reason Code", "Note",
]


def _run_id_from_cwd():
    try:
        return os.path.basename(os.getcwd())
    except Exception:
        return ""


def _domain_from_url(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _partners_text(profile):
    return "; ".join(
        f"{x.get('name','')} ({x.get('type','')})"
        for x in (profile.get("partners_found") or []) if x.get("name")
    )


def _note_for_reviewable(action, code, reason, rec, profile):
    notes = []
    if action == "maybe":
        notes.append("Needs review")
    if reason:
        notes.append(str(reason))
    if profile.get("confidence"):
        notes.append(f"conf={profile.get('confidence')}")
    # Keep the reviewer-facing CSV clean: the internal decision fields are not output columns.
    if rec.get("note") and "child signal" not in str(rec.get("note", "")).lower():
        notes.append(str(rec.get("note")))
    return "; ".join(notes)


def _final_decision_for(ngo, rec, profiles):
    status = rec.get("status", "")
    if status == "prefilter_rejected":
        code = rec.get("prefilter_reason_code") or "rule_rejected"
        reason = rec.get("note", "") or _compact_reason_text(code)
        return "rejected", code, reason, {}
    if status != "ready_for_ai":
        code = status or "not_processed"
        reason = rec.get("note", "") or status or "not processed"
        return "rejected", code, reason, {}
    profile = profiles.get(ngo["id"], {}) or {}
    action, code, reason = classify_profile(profile, rec)
    return action, code, reason, profile


def write_audit(ngos, done, profiles):
    tmp = AUDIT_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_COLUMNS)
        w.writeheader()
        for ngo in ngos:
            rec = done.get(ngo["id"], {})
            action, code, reason, profile = _final_decision_for(ngo, rec, profiles)
            decision = str((profile or {}).get("decision") or ("yes" if action == "shortlisted" else "maybe" if action == "maybe" else "no"))
            brief = str((profile or {}).get("summary") or reason or rec.get("note", ""))[:500]
            w.writerow(_safe_csv_dict({
                "NGO ID": ngo.get("ngo_id", ""),
                "Source Record ID": ngo.get("source_record_id", ""),
                "Batch ID": ngo.get("batch_id", ""),
                "Batch Label": ngo.get("batch_label", ""),
                "Source Type": ngo.get("source_type", ""),
                "Source Run ID": ngo.get("source_run_id", ""),
                "NGO Name": ngo["name"],
                "State": ngo.get("state", ""),
                "District": ngo.get("district", ""),
                "Status": rec.get("status", "not_processed"),
                "Website": rec.get("website", ""),
                "Avika Decision": decision,
                "DFP Fit": "Strong fit" if decision == "yes" else "Needs review" if decision == "maybe" else "Not fit",
                "Brief Description": brief,
                "Final Action": action,
                "Internal Reason Code": code,
                "Note": reason or rec.get("note", ""),
            }))
    os.replace(tmp, AUDIT_CSV)


def write_output(ngos, done, profiles):
    """Write reviewable Avika rows: Yes/shortlisted + Maybe."""
    counts = {"shortlisted": 0, "maybe": 0, "rejected": 0, "history_appended": 0}
    history_rows = []
    tmp = OUTPUT_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for ngo in ngos:
            rec = done.get(ngo["id"], {})
            action, code, reason, p = _final_decision_for(ngo, rec, profiles)
            decision = str(p.get("decision") or ("yes" if action == "shortlisted" else "maybe" if action == "maybe" else "no"))
            if action in {"shortlisted", "maybe"}:
                counts[action] += 1
                brief = str(p.get("summary") or reason or "").strip()
                w.writerow(_safe_csv_row([
                    ngo.get("ngo_id", ""),
                    ngo.get("source_record_id", ""),
                    ngo.get("batch_id", ""),
                    ngo.get("batch_label", ""),
                    ngo.get("source_type", ""),
                    ngo.get("source_run_id", ""),
                    ngo["name"],
                    p.get("location") or ngo.get("district") or "",
                    ngo.get("state", ""),
                    rec.get("website", ""),
                    decision,
                    "Strong fit" if decision == "yes" else "Needs review",
                    p.get("confidence", ""),
                    p.get("official_website_match", ""),
                    code,
                    brief,
                    p.get("serves", ""),
                    p.get("story", ""),
                    (p.get("digital_presence", "") or "").upper(),
                    "; ".join(rec.get("socials", [])),
                    _partners_text(p),
                    "",
                    _note_for_reviewable(action, code, reason, rec, p),
                ]))
            else:
                counts["rejected"] += 1
            history_rows.append({
                "scan_date": _now_iso(),
                "run_id": _run_id_from_cwd(),
                "filter_version": FILTER_VERSION,
                "model_version": HAIKU_MODEL,
                "input_ngo_name": ngo["name"],
                "normalized_ngo_name": re.sub(r"\s+", " ", ngo["name"].strip().lower()),
                "district": ngo.get("district", ""),
                "state": ngo.get("state", ""),
                "website_found": rec.get("website", ""),
                "website_domain": _domain_from_url(rec.get("website", "")),
                "final_action": action,
                "internal_reason_code": code,
                "internal_reason": reason,
                "serper_queries_per_ngo": SERPER_QUERIES_PER_NGO,
            })
    os.replace(tmp, OUTPUT_CSV)
    _append_global_history(history_rows)
    counts["history_appended"] = len(history_rows)
    return counts


def write_rejected_output(ngos, done, profiles):
    tmp = REJECTED_CSV + ".tmp"
    rows = 0
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REJECTED_COLUMNS)
        w.writeheader()
        for ngo in ngos:
            rec = done.get(ngo["id"], {})
            action, code, reason, p = _final_decision_for(ngo, rec, profiles)
            if action in {"shortlisted", "maybe"}:
                continue
            rows += 1
            decision = str((p or {}).get("decision") or "no")
            brief = str((p or {}).get("summary") or reason or rec.get("note", ""))[:500]
            w.writerow(_safe_csv_dict({
                "NGO ID": ngo.get("ngo_id", ""),
                "Source Record ID": ngo.get("source_record_id", ""),
                "Batch ID": ngo.get("batch_id", ""),
                "Batch Label": ngo.get("batch_label", ""),
                "Source Type": ngo.get("source_type", ""),
                "Source Run ID": ngo.get("source_run_id", ""),
                "NGO Name": ngo["name"],
                "State": ngo.get("state", ""),
                "District": ngo.get("district", ""),
                "Website": rec.get("website", ""),
                "Avika Decision": decision,
                "DFP Fit": "Not fit",
                "Brief Description": brief,
                "Final Action": action,
                "Internal Reason Code": code,
                "Internal Reason": reason,
                "Original Status": rec.get("status", "not_processed"),
                "AI Confidence": p.get("confidence", "") if p else "",
                "Official Website Match": p.get("official_website_match", "") if p else "",
            }))
    os.replace(tmp, REJECTED_CSV)
    return rows



# ============================================================================
# 9.5 DONOR DISCOVERY LITE  —  safe add-on, no extra network calls
# ----------------------------------------------------------------------------
#  This is intentionally conservative. It DOES NOT search Serper again, DOES NOT
#  call Claude again, and DOES NOT scrape CSR portals. It only uses the
#  partners_found array already produced during NGO profiling, then dedupes,
#  classifies, scores lightly, and writes a second CSV.
#
#  Why this is safe:
#  - If no partners are found, it writes an empty CSV with headers.
#  - If a bad partner row appears, that row is skipped.
#  - If donor-lite itself errors, run() catches it and the main repository CSV
#    remains complete.
# ============================================================================
DONOR_COLUMNS = [
    "Lead Name", "Lead Type", "Priority", "Score", "Evidence Count",
    "Theme Fit", "Geography Fit", "CSR Portal Signal", "Evidence Recency",
    "Source NGO(s)", "Evidence URL(s)", "Why This Lead Matters",
    "Recommended Contact Path", "Manual Merge Flag", "Raw Names", "Notes"
]

MANUAL_MERGE_HOUSES = (
    "tata", "birla", "reliance", "mahindra", "aditya birla", "hdfc", "icici",
    "infosys", "wipro", "jsw", "bajaj", "godrej", "kotak", "axis", "sbi",
    "vedanta", "jindal", "larsen", "toubro", "ltimindtree", "tech mahindra"
)

DONOR_NOISE_NAMES = {
    "partner", "partners", "supporter", "supporters", "donor", "donors",
    "csr", "government", "india", "about us", "contact", "privacy policy",
    "annual report", "board", "team", "volunteer", "volunteers"
}

# Strip company suffixes only. Do NOT strip foundation/trust/bank/fund/mission.
DONOR_COMPANY_SUFFIX_RE = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|llp|inc\.?|corp\.?|corporation|company|co\.?)\b",
    re.IGNORECASE,
)

def _donor_clean_key(name):
    """Matching key for clustering. Preserves entity words like Foundation.
    This keeps Infosys and Infosys Foundation separate."""
    s = html.unescape(str(name or "")).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = DONOR_COMPANY_SUFFIX_RE.sub(" ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _donor_display_name(raw_names):
    """Pick a human-looking display name from clustered raw names."""
    vals = [str(x).strip() for x in raw_names if str(x).strip()]
    if not vals:
        return ""
    # Prefer names containing an entity word, then prefer the longest clear name.
    entity_words = ("Foundation", "Trust", "Bank", "Fund", "Mission", "CSR", "Club")
    vals_sorted = sorted(vals, key=lambda v: (not any(w.lower() in v.lower() for w in entity_words), -len(v)))
    return vals_sorted[0]

def _manual_merge_flag(clean_key):
    return "Yes" if any(h in clean_key for h in MANUAL_MERGE_HOUSES) else "No"

def _classify_donor(name, ai_type=""):
    s = (str(name or "") + " " + str(ai_type or "")).lower()
    if "government" in s or "govt" in s or "municipal" in s or "department" in s or "ministry" in s:
        return "Government"
    if "rotary" in s or "lions club" in s or "round table" in s or "leo club" in s:
        return "Service Club"
    if "bank" in s:
        return "CSR / Corporate"
    if "corporate_foundation" in s:
        return "Corporate Foundation"
    if "foundation" in s or "trust" in s or "fund" in s or "mission" in s:
        return "Foundation / Grantmaker"
    if "school" in s or "vidyalaya" in s or "university" in s or "college" in s:
        return "School / Education Network"
    if "corporate" in s or "csr" in s or "limited" in s or "ltd" in s or "pvt" in s:
        return "CSR / Corporate"
    return "Unknown"

def _theme_fit_from_profile(profile):
    tags = [str(x).lower() for x in (profile.get("cause_tags") or [])]
    blob = " ".join(tags + [str(profile.get("serves", "")), str(profile.get("summary", "")), str(profile.get("story", ""))]).lower()
    fits = []
    if any(w in blob for w in ("nutrition", "malnutrition", "hunger", "midday", "mid-day", "food", "anganwadi")):
        fits.append("nutrition")
    if any(w in blob for w in ("education", "school", "student", "scholarship", "learning", "tuition")):
        fits.append("education")
    if any(w in blob for w in ("child", "children", "orphan", "youth", "adolescent", "protection", "welfare")):
        fits.append("children")
    if any(w in blob for w in ("health", "medical", "disability", "vaccination", "immunization")):
        fits.append("health")
    if any(w in blob for w in ("sport", "athlete", "coaching")):
        fits.append("sport")
    return " / ".join(dict.fromkeys(fits)) or "unclear"

def _priority(score):
    if score >= 70:
        return "High"
    if score >= 45:
        return "Medium"
    if score >= 25:
        return "Manual check"
    return "Low"

def _contact_path(name, lead_type):
    clean = str(name or "").strip()
    if not clean:
        return "Manual search needed"
    if lead_type == "Government":
        return f"Check official district/state department website for {clean} contact path"
    if lead_type == "Service Club":
        return f"Search Google/LinkedIn for \"{clean}\" president secretary CSR community service"
    return f"Search LinkedIn for \"{clean} CSR Manager India\" and check the official CSR/foundation contact page"

def _safe_join(values, sep="; ", max_items=8):
    out = []
    seen = set()
    for v in values:
        v = str(v or "").strip()
        if not v:
            continue
        k = v.lower()
        if k not in seen:
            seen.add(k); out.append(v)
        if len(out) >= max_items:
            break
    return sep.join(out)

def write_donor_lite_output(ngos, done, profiles):
    clusters = {}
    ngo_by_id = {n["id"]: n for n in ngos}

    for ngo in ngos:
        rec = done.get(ngo["id"], {})
        if rec.get("status") != "ready_for_ai":
            continue
        profile = profiles.get(ngo["id"], {}) or {}
        # Donor-lite should use every profiled NGO that reached the owned-site + AI stage,
        # including rows later filtered out of the main shortlist. Rejected NGOs can still
        # expose useful CSR/foundation/government partner names.
        if not profile or profile.get("_error"):
            continue
        partners = profile.get("partners_found") or []
        if not isinstance(partners, list):
            continue

        for p in partners:
            if not isinstance(p, dict):
                continue
            raw_name = str(p.get("name", "")).strip()
            if not raw_name:
                continue
            clean_key = _donor_clean_key(raw_name)
            if len(clean_key) < 3 or clean_key in DONOR_NOISE_NAMES:
                continue

            c = clusters.setdefault(clean_key, {
                "raw_names": set(),
                "source_ngos": set(),
                "evidence_urls": set(),
                "states": set(),
                "districts": set(),
                "ai_types": [],
                "relationships": [],
                "themes": [],
            })
            c["raw_names"].add(raw_name)
            c["source_ngos"].add(ngo["name"])
            if rec.get("website"):
                c["evidence_urls"].add(rec["website"])
            if ngo.get("state"):
                c["states"].add(ngo["state"])
            if ngo.get("district"):
                c["districts"].add(ngo["district"])
            if p.get("type"):
                c["ai_types"].append(str(p.get("type")))
            if p.get("relationship"):
                c["relationships"].append(str(p.get("relationship")))
            c["themes"].append(_theme_fit_from_profile(profile))

    rows = []
    for clean_key, c in clusters.items():
        display = _donor_display_name(c["raw_names"])
        if not display:
            continue
        ai_type = _safe_join(c["ai_types"], sep=" / ", max_items=3)
        lead_type = _classify_donor(display, ai_type)
        evidence_count = len(c["source_ngos"])
        theme_fit = _safe_join(c["themes"], sep=" / ", max_items=4) or "unclear"
        geography = _safe_join(list(c["districts"]) + list(c["states"]), sep="; ", max_items=6) or "from source NGOs"
        manual_merge = _manual_merge_flag(clean_key)

        # Conservative score: no CSR portal confirmation in lite mode.
        score = 0
        score += min(40, evidence_count * 12)
        if any(t in theme_fit.lower() for t in ("nutrition", "education", "children", "health")):
            score += 20
        if geography != "from source NGOs":
            score += 10
        if lead_type in ("Corporate Foundation", "Foundation / Grantmaker", "CSR / Corporate"):
            score += 15
        elif lead_type in ("Service Club", "Government"):
            score += 8
        if manual_merge == "Yes":
            score -= 5  # not bad, just needs human verification
        score = max(0, min(100, score))

        why = f"Found as a partner/funder/supporter across {evidence_count} NGO source(s). Theme fit: {theme_fit}."
        if manual_merge == "Yes":
            why += " Large-group name: verify exact cheque-writing entity before outreach."

        rows.append({
            "Lead Name": display,
            "Lead Type": lead_type,
            "Priority": _priority(score),
            "Score": score,
            "Evidence Count": evidence_count,
            "Theme Fit": theme_fit,
            "Geography Fit": geography,
            "CSR Portal Signal": "Not checked in lite mode",
            "Evidence Recency": "Undated website evidence",
            "Source NGO(s)": _safe_join(sorted(c["source_ngos"]), max_items=12),
            "Evidence URL(s)": _safe_join(sorted(c["evidence_urls"]), max_items=12),
            "Why This Lead Matters": why,
            "Recommended Contact Path": _contact_path(display, lead_type),
            "Manual Merge Flag": manual_merge,
            "Raw Names": _safe_join(sorted(c["raw_names"]), max_items=10),
            "Notes": "Donor-lite uses partner names found during NGO profiling, including shortlisted, maybe, and rejected profiled NGOs; no extra search/API calls.",
        })

    rows.sort(key=lambda r: (-int(r["Score"]), r["Lead Name"].lower()))

    tmp = DONOR_OUTPUT_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DONOR_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: _safe_csv_cell(v) for k, v in r.items()})
    os.replace(tmp, DONOR_OUTPUT_CSV)

    print(f"Donor-lite wrote {len(rows)} leads to {DONOR_OUTPUT_CSV} from all profiled NGOs, including rejected rows.")

# ============================================================================
# 10. GO
# ============================================================================
if __name__ == "__main__":
    if not _has_serper_keys() or not ANTHROPIC_API_KEY:
        msg = "SERPER_API_KEY and ANTHROPIC_API_KEY must be set in Railway environment variables."
        write_status("missing_api_keys", msg, ok=False, run_status="blocked", error=msg)
        print("⚠ " + msg)
    else:
        try:
            run()
        except ProviderPauseRequested as e:
            write_status(
                "provider_credit_exhausted",
                "Paused because a required provider key/account has no usable capacity. Add credits or replace/fix the key, then Resume the parent recovery run.",
                ok=True,
                run_status="paused",
                error=str(e),
                extra={
                    "pause_reason": e.reason,
                    "paused_provider": e.provider,
                    "paused_key": e.key_label,
                    "provider_status_code": e.status_code,
                    "provider_error_detail": e.detail,
                },
            )
            print(f"Paused safely: {e}")
            sys.exit(75)
        except Exception as e:
            # Last-resort guard: even catastrophic failures write valid JSON status.
            err = traceback.format_exc().splitlines()[-1]
            log_error("fatal", "fatal", "run", err)
            write_status("fatal_error", "Run stopped unexpectedly", ok=False, run_status="error", error=err)
            raise
