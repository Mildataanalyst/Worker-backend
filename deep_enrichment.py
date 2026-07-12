"""DFP 2.0 Deep Enrichment worker.

This module deliberately keeps enrichment and judgement separate:
- Firecrawl retrieves the official public website.
- Serper discovers external media and verification sources.
- The worker preserves structured evidence, exact excerpts, and source URLs.
- An optional Claude Haiku pass suggests categories and preliminary signals.
- No PM rating is overwritten and no final transformation decision is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse


CATEGORY_LABELS = {
    "uncategorised": "Uncategorised",
    "residential_education": "Long-horizon residential education",
    "childcare_transition_to_adulthood": "Childcare and transition to adulthood",
    "hostel_external_school": "Hostel linked to external schools",
    "full_day_intensive_school": "Full-day intensive school",
    "alternative_second_chance_education": "Alternative or second-chance education",
    "community_education": "Community education ecosystem",
    "scholarship_sponsorship": "Scholarship or sponsorship pathway",
    "sports_led_development": "Sports-led development",
    "arts_music_led_development": "Arts or music-led development",
    "disability_education_independence": "Disability education and independence",
    "vocational_employment_pathway": "Vocational and employment pathway",
    "girls_focused_education": "Girls-focused education and transition",
    "tribal_geographically_excluded": "Tribal or geographically excluded education",
    "child_protection_rehabilitation": "Child protection and rehabilitation",
    "aftercare_transition_age": "Aftercare and transition-age support",
    "nutrition_first_hybrid": "Nutrition-first education hybrid",
    "hybrid_unusual": "Hybrid or unusual model",
}

# Exact term matches are preserved in the evidence pack. These are discovery
# signals, not truth judgements or transformation scores.
TRIGGER_FAMILIES = [
    {
        "key": "global_recognition",
        "label": "Global or major recognition",
        "weight": 100,
        "patterns": [
            r"\bnobel(?: peace)? prize\b", r"\bramon magsaysay\b", r"\bpadma (?:shri|bhushan|vibhushan)\b",
            r"\bunesco\b", r"\bunicef\b", r"\bunited nations\b", r"\bglobal award\b",
            r"\binternational award\b", r"\bnational award\b",
        ],
    },
    {
        "key": "pioneering_distinction",
        "label": "Pioneering or unusual distinction",
        "weight": 85,
        "patterns": [
            r"\bfirst (?:of its kind|in india|in the state|ever)\b", r"\bonly (?:school|institution|organisation|organization|programme|program)\b",
            r"\bpioneer(?:ing|ed)?\b", r"\bgroundbreaking\b", r"\blargest\b", r"\boldest\b", r"\bunique model\b",
        ],
    },
    {
        "key": "sports_excellence",
        "label": "Sports excellence",
        "weight": 82,
        "patterns": [
            r"\brepresented india\b", r"\bindian (?:team|squad)\b", r"\bnational (?:team|champion|championship|medal)\b",
            r"\binternational (?:team|championship|medal|tournament)\b", r"\bolympic\b", r"\basian games\b",
            r"\bcommonwealth games\b", r"\bsports scholarship\b", r"\bprofessional (?:athlete|player)\b",
        ],
    },
    {
        "key": "education_progression",
        "label": "Education and scholarship progression",
        "weight": 76,
        "patterns": [
            r"\bscholarship\b", r"\buniversity admission\b", r"\bcollege admission\b", r"\bhigher education\b",
            r"\bjee\b", r"\bneet\b", r"\bupsc\b", r"\bboard exam\b", r"\bgraduat(?:e|ed|ion)\b",
        ],
    },
    {
        "key": "employment_aftercare",
        "label": "Employment or transition-to-adulthood outcomes",
        "weight": 74,
        "patterns": [
            r"\bplacement(?:s)?\b", r"\bemploy(?:ed|ment|ability)\b", r"\blivelihood\b", r"\baftercare\b",
            r"\bindependent living\b", r"\btransition to adulthood\b", r"\bcareer support\b", r"\bjob readiness\b",
        ],
    },
    {
        "key": "arts_excellence",
        "label": "Arts or music excellence",
        "weight": 72,
        "patterns": [
            r"\bnational (?:music|dance|arts?)\b", r"\binternational (?:music|dance|arts?)\b", r"\bprofessional musician\b",
            r"\bconcert\b", r"\borchestra\b", r"\barts scholarship\b", r"\bmusic scholarship\b",
        ],
    },
    {
        "key": "government_adoption",
        "label": "Government or institutional validation",
        "weight": 68,
        "patterns": [
            r"\bgovernment (?:recognised|recognized|adopted|award|partnership)\b", r"\bministry of\b",
            r"\bstate government\b", r"\bcentral government\b", r"\bmemorandum of understanding\b", r"\bcase study\b",
        ],
    },
    {
        "key": "scale_longevity",
        "label": "Scale or institutional longevity",
        "weight": 48,
        "patterns": [
            r"\b(?:10|15|20|25|30|40|50) years\b", r"\bsince (?:19|20)\d{2}\b", r"\bthousands of children\b",
            r"\b[1-9][0-9,]{3,}\+? (?:children|students|beneficiaries|alumni)\b",
        ],
    },
    {
        "key": "adverse_or_conflict",
        "label": "Possible adverse or contradictory information",
        "weight": 95,
        "patterns": [
            r"\bcontrovers(?:y|ial)\b", r"\bfraud\b", r"\bcomplaint\b", r"\binvestigat(?:e|ed|ion)\b",
            r"\bclosed down\b", r"\bprogramme discontinued\b", r"\bregistration cancelled\b", r"\bmisuse of funds\b",
        ],
    },
]

BASE_SEARCH_TEMPLATES = [
    '"{name}" news',
    '"{name}" media coverage',
    '"{name}" award recognition',
    '"{name}" achievements',
    '"{name}" students achievements',
    '"{name}" alumni outcomes',
    '"{name}" higher education scholarship',
    '"{name}" employment placement alumni',
    '"{name}" sports scholarship national team',
    '"{name}" arts music achievement',
    '"{name}" government recognition',
    '"{name}" case study impact',
    '"{name}" founder interview',
    '"{name}" corporate partner donor',
    '"{name}" annual report outcomes',
    '"{name}" independent review',
    '"{name}" controversy',
    '"{name}" complaint fraud',
    '"{name}" closure inactive',
    '"{name}" children after age 18',
    '"{name}" transition to adulthood',
    '"{name}" vocational livelihood',
    '"{name}" latest news',
    '"{name}" press coverage',
    '"{name}" newspaper profile',
    '"{name}" documentary',
    '"{name}" research paper',
    '"{name}" evaluation report',
    '"{name}" outcomes data',
    '"{name}" annual results students',
    '"{name}" notable alumni',
    '"{name}" alumni career',
    '"{name}" child success story',
    '"{name}" university alumni',
    '"{name}" innovation education model',
    '"{name}" replicated model',
    '"{name}" government adopted programme',
    '"{name}" international recognition',
    '"{name}" national recognition',
    '"{name}" award founder',
    '"{name}" student scholarship award',
    '"{name}" funding partner impact report',
    '"{name}" safeguarding',
    '"{name}" fee structure school',
    '"{name}" programme discontinued',
]


CATEGORY_SEARCH_TEMPLATES = {
    "residential_education": [
        '"{name}" residential school alumni', '"{name}" college employment graduates', '"{name}" board results',
    ],
    "childcare_transition_to_adulthood": [
        '"{name}" aftercare alumni employment', '"{name}" children home age 18', '"{name}" independent living',
    ],
    "hostel_external_school": [
        '"{name}" hostel school completion', '"{name}" tribal hostel students outcomes',
    ],
    "sports_led_development": [
        '"{name}" athlete medal championship', '"{name}" represented India', '"{name}" sports academy scholarship',
    ],
    "arts_music_led_development": [
        '"{name}" music students award', '"{name}" orchestra scholarship', '"{name}" alumni musician',
    ],
    "disability_education_independence": [
        '"{name}" disability vocational placement', '"{name}" special school employment', '"{name}" independent living',
    ],
    "alternative_second_chance_education": [
        '"{name}" dropout reintegration', '"{name}" child labour education outcomes',
    ],
    "vocational_employment_pathway": [
        '"{name}" vocational placement', '"{name}" job outcomes', '"{name}" entrepreneurship alumni',
    ],
    "girls_focused_education": [
        '"{name}" girls education scholarship', '"{name}" girls employment alumni', '"{name}" child marriage prevention',
    ],
    "tribal_geographically_excluded": [
        '"{name}" tribal students outcomes', '"{name}" remote school scholarship',
    ],
    "aftercare_transition_age": [
        '"{name}" aftercare employment housing', '"{name}" care leavers outcomes',
    ],
}

PAGE_TYPE_RULES = [
    ("awards", ("award", "recognition", "honour", "honor")),
    ("alumni", ("alumni", "graduate", "where are they now")),
    ("outcomes", ("impact", "outcome", "result", "placement", "scholarship")),
    ("stories", ("story", "stories", "testimonial", "journey")),
    ("programmes", ("programme", "program", "project", "what we do", "our work")),
    ("reports", ("annual report", "report", "publication", "newsletter")),
    ("partners", ("partner", "supporter", "donor", "csr")),
    ("leadership", ("team", "leadership", "founder", "board")),
    ("news", ("news", "media", "press", "blog")),
    ("about", ("about", "history", "who we are", "our story")),
]

LOW_VALUE_PATH_PARTS = {
    "privacy", "terms", "cookie", "donate", "donation", "checkout", "login", "signin", "wp-admin",
    "author", "tag", "category", "search", "feed", "cart", "account",
}

EXTERNAL_SKIP_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be", "x.com", "twitter.com",
    "google.com", "google.co.in", "justdial.com", "indiacsr.in",
}


class DeepEnrichmentRuntime:
    def __init__(
        self,
        *,
        runs_dir: Path,
        serper_post: Callable[[dict, int], dict],
        has_serper_keys: Callable[[], bool],
        safe_fetch_text: Callable[..., tuple[str, str]],
        validate_public_url: Callable[[str], str],
        make_soup: Callable[[str], Any],
        get_anthropic: Callable[[], Any],
        job_create: Callable[..., dict],
        job_update: Callable[..., dict],
        job_request_cancel: Callable[[str], dict],
        job_cancel_requested: Callable[[str], bool],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.serper_post = serper_post
        self.has_serper_keys = has_serper_keys
        self.safe_fetch_text = safe_fetch_text
        self.validate_public_url = validate_public_url
        self.make_soup = make_soup
        self.get_anthropic = get_anthropic
        self.job_create = job_create
        self.job_update = job_update
        self.job_request_cancel = job_request_cancel
        self.job_cancel_requested = job_cancel_requested
        self.utc_now_iso = utc_now_iso
        self.router = APIRouter()
        self.threads: dict[str, threading.Thread] = {}
        self.cancel_flags: dict[str, threading.Event] = {}
        # Active Firecrawl crawl IDs keep the API key that created them. Firecrawl
        # crawl jobs are account-scoped, so polling/cancelling/pagination must keep
        # using the same key for the full lifecycle of that crawl.
        self.firecrawl_jobs: dict[str, dict[str, str]] = {}
        self._firecrawl_lock = threading.RLock()
        self._firecrawl_cursor = 0
        self._firecrawl_disabled: dict[str, str] = {}
        self._status_lock = threading.RLock()
        self._register_routes()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    def _register_routes(self) -> None:
        self.router.add_api_route("/enrichment/start", self.start, methods=["POST"])
        self.router.add_api_route("/enrichment/status/{run_id}", self.status, methods=["GET"])
        self.router.add_api_route("/enrichment/results/{run_id}", self.results, methods=["GET"])
        self.router.add_api_route("/enrichment/export/{run_id}/{kind}", self.export, methods=["GET"])
        self.router.add_api_route("/enrichment/cancel/{run_id}", self.cancel, methods=["POST"])
        self.router.add_api_route("/enrichment/resume/{run_id}", self.resume, methods=["POST"])
        self.router.add_api_route("/enrichment/archive", self.archive, methods=["GET"])
        self.router.add_api_route("/enrichment/config", self.config, methods=["GET"])

    def _json(self, ok: bool, status_code: int = 200, **kwargs: Any) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"ok": ok, **kwargs})

    def config(self) -> JSONResponse:
        return self._json(
            True,
            firecrawl_configured=bool(self._firecrawl_keys()),
            firecrawl_key_count=len(self._firecrawl_keys()),
            firecrawl_enabled_key_count=len(self._enabled_firecrawl_keys()),
            firecrawl_disabled_keys=self._disabled_firecrawl_key_labels(),
            serper_configured=bool(self.has_serper_keys()),
            haiku_configured=bool(os.environ.get("ANTHROPIC_API_KEY")),
            categories=[{"key": key, "label": label} for key, label in CATEGORY_LABELS.items()],
            defaults=self._options({}),
        )

    def start(self, payload: dict | None = None) -> JSONResponse:
        payload = payload or {}
        try:
            ngos = self._normalise_ngos(payload.get("ngos") or payload.get("rows") or [])
        except ValueError as exc:
            return self._json(False, status_code=400, stage="invalid_input", error=str(exc))
        if not ngos:
            return self._json(False, status_code=400, stage="invalid_input", error="Select at least one NGO")
        if len(ngos) > int(os.environ.get("ENRICHMENT_MAX_NGOS_PER_RUN", "100")):
            return self._json(False, status_code=400, stage="too_many_ngos", error="A Deep Enrichment run supports at most 100 NGOs")
        if not self._firecrawl_keys():
            return self._json(
                False,
                status_code=503,
                stage="missing_firecrawl_key",
                error="FIRECRAWL_API_KEYS or FIRECRAWL_API_KEY is not configured on the Railway worker",
            )
        if not self.has_serper_keys():
            return self._json(False, status_code=503, stage="missing_serper_key", error="SERPER_API_KEY or SERPER_API_KEYS is not configured on the Railway worker")

        options = self._options(payload.get("options") or {})
        run_id = f"enrich_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        rd = self._run_dir(run_id)
        rd.mkdir(parents=True, exist_ok=True)
        input_payload = {
            "run_id": run_id,
            "region": str(payload.get("region") or "").strip(),
            "ngos": ngos,
            "options": options,
            "created_at": self.utc_now_iso(),
        }
        self._write_json(rd / "input.json", input_payload)
        status = self._initial_status(run_id, len(ngos), options)
        self._write_status(rd, status)
        self.job_create(run_id, "deep_enrichment", rd, status="queued", stage="queued", total=len(ngos), processed=0)

        event = threading.Event()
        self.cancel_flags[run_id] = event
        thread = threading.Thread(target=self._run, args=(run_id, event), daemon=True)
        self.threads[run_id] = thread
        thread.start()

        estimated = len(ngos) * (options["max_pages_per_site"] + options["external_firecrawl_fallbacks"])
        return self._json(
            True,
            run_id=run_id,
            stage="queued",
            selected_count=len(ngos),
            options=options,
            estimated_max_firecrawl_credits=estimated,
            status_url=f"/enrichment/status/{run_id}",
        )

    def status(self, run_id: str) -> JSONResponse:
        rd = self._run_dir(run_id)
        path = rd / "status.json"
        if not path.exists():
            return self._json(False, status_code=404, stage="run_not_found", error="Deep Enrichment run not found")
        data = self._read_json(path, {})
        data["live_state"] = self.live_state(run_id)
        return self._json(True, **data)

    def results(self, run_id: str, limit: int = 200) -> JSONResponse:
        rd = self._run_dir(run_id)
        if not rd.exists():
            return self._json(False, status_code=404, error="Deep Enrichment run not found")
        rows = self._read_json(rd / "master_summary.json", [])
        return self._json(True, run_id=run_id, count=len(rows), rows=rows[: max(1, min(limit, 500))])

    def export(self, run_id: str, kind: str) -> Any:
        rd = self._run_dir(run_id)
        if not rd.exists():
            return self._json(False, status_code=404, error="Deep Enrichment run not found")
        key = str(kind or "").strip().lower().replace("-", "_")
        mapping = {
            "zip": (rd / "deep_enrichment_export.zip", "application/zip"),
            "full": (rd / "deep_enrichment_export.zip", "application/zip"),
            "csv": (rd / "master_summary.csv", "text/csv"),
            "master_csv": (rd / "master_summary.csv", "text/csv"),
            "jsonl": (rd / "all_dossiers.jsonl", "application/x-ndjson"),
            "packet": (rd / "gpt_fable_packet.md", "text/markdown"),
            "markdown": (rd / "gpt_fable_packet.md", "text/markdown"),
            "report": (rd / "run_report.json", "application/json"),
        }
        target = mapping.get(key)
        if not target:
            return self._json(False, status_code=400, error="Unknown export kind. Use zip, csv, jsonl, packet, or report")
        path, media_type = target
        if not path.exists():
            return self._json(False, status_code=409, error="That export is not ready yet. Partial results remain available through the results endpoint")
        return FileResponse(path, media_type=media_type, filename=path.name)

    def cancel(self, run_id: str) -> JSONResponse:
        rd = self._run_dir(run_id)
        if not rd.exists():
            return self._json(False, status_code=404, error="Deep Enrichment run not found")
        event = self.cancel_flags.setdefault(run_id, threading.Event())
        event.set()
        self.job_request_cancel(run_id)
        firecrawl_job = self.firecrawl_jobs.get(run_id) or {}
        firecrawl_id = str(firecrawl_job.get("crawl_id") or "") if isinstance(firecrawl_job, dict) else str(firecrawl_job or "")
        firecrawl_key = str(firecrawl_job.get("api_key") or "") if isinstance(firecrawl_job, dict) else ""
        if firecrawl_id:
            try:
                self._firecrawl_request(
                    "DELETE",
                    f"/crawl/{firecrawl_id}",
                    timeout=30,
                    api_key=firecrawl_key or None,
                    allow_key_failover=False,
                )
            except Exception:
                pass
        self._patch_status(rd, stage="cancel_requested", run_status="cancelling", message="Cancellation requested")
        return self._json(True, run_id=run_id, stage="cancel_requested")

    def resume(self, run_id: str) -> JSONResponse:
        rd = self._run_dir(run_id)
        if not (rd / "input.json").exists():
            return self._json(False, status_code=404, error="Deep Enrichment run not found")
        thread = self.threads.get(run_id)
        if thread and thread.is_alive():
            return self._json(False, status_code=409, error="Run is already active")
        event = threading.Event()
        self.cancel_flags[run_id] = event
        self._patch_status(rd, stage="resuming", run_status="resuming", message="Resuming incomplete NGOs")
        self.job_update(run_id, status="resuming", stage="resuming", cancel_requested=False)
        thread = threading.Thread(target=self._run, args=(run_id, event), daemon=True)
        self.threads[run_id] = thread
        thread.start()
        return self._json(True, run_id=run_id, stage="resuming")

    def archive(self, limit: int = 100) -> JSONResponse:
        rows = []
        for rd in self.runs_dir.glob("enrich_*"):
            if not rd.is_dir():
                continue
            status = self._read_json(rd / "status.json", {})
            rows.append({
                "run_id": rd.name,
                "created_at": status.get("created_at") or "",
                "updated_at": status.get("updated_at") or "",
                "run_status": status.get("run_status") or "",
                "stage": status.get("stage") or "",
                "processed": status.get("processed") or 0,
                "total": status.get("total") or 0,
                "firecrawl_credits_used": status.get("firecrawl_credits_used") or 0,
                "serper_queries_used": status.get("serper_queries_used") or 0,
            })
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return self._json(True, count=len(rows), rows=rows[: max(1, min(limit, 500))])

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------
    def live_state(self, run_id: str) -> str:
        thread = self.threads.get(run_id)
        return "running" if thread and thread.is_alive() else "not_running"

    def request_cancel(self, run_id: str) -> None:
        self.cancel_flags.setdefault(run_id, threading.Event()).set()

    def _cancelled(self, run_id: str, event: threading.Event) -> bool:
        return bool(event.is_set() or self.job_cancel_requested(run_id))

    def _run(self, run_id: str, cancel_event: threading.Event) -> None:
        rd = self._run_dir(run_id)
        payload = self._read_json(rd / "input.json", {})
        ngos = payload.get("ngos") or []
        options = self._options(payload.get("options") or {})
        summaries = self._read_json(rd / "master_summary.json", [])
        completed_ids = {str(row.get("ngo_id") or "") for row in summaries if row.get("status") == "complete"}

        self._patch_status(rd, run_status="running", stage="starting", message="Starting Deep Enrichment")
        self.job_update(run_id, status="running", stage="starting", total=len(ngos), processed=len(completed_ids), cancel_requested=False)

        errors = []
        try:
            for index, ngo in enumerate(ngos):
                ngo_id = str(ngo.get("ngo_id") or "")
                if ngo_id in completed_ids:
                    continue
                if self._cancelled(run_id, cancel_event):
                    self._patch_status(rd, run_status="cancelled", stage="cancelled", message="Run cancelled; completed dossiers were preserved")
                    self.job_update(run_id, status="cancelled", stage="cancelled")
                    self._rebuild_aggregate_outputs(rd, payload)
                    return

                self._patch_status(
                    rd,
                    stage="website_crawling",
                    current_ngo=ngo.get("ngo_name"),
                    current_index=index + 1,
                    current_step="Preparing NGO",
                    message=f"Researching {ngo.get('ngo_name')}",
                )
                try:
                    dossier = self._process_ngo(run_id, rd, ngo, options, cancel_event)
                    summary = self._summary_from_dossier(dossier)
                except CancelledError:
                    self._patch_status(rd, run_status="cancelled", stage="cancelled", message="Run cancelled; completed dossiers were preserved")
                    self.job_update(run_id, status="cancelled", stage="cancelled")
                    self._rebuild_aggregate_outputs(rd, payload)
                    return
                except Exception as exc:
                    summary = {
                        "ngo_id": ngo_id,
                        "ngo_name": ngo.get("ngo_name"),
                        "website": ngo.get("website"),
                        "pm_reviewer": ngo.get("pm_reviewer"),
                        "pm_rating": ngo.get("pm_rating"),
                        "status": "failed",
                        "error": str(exc)[:500],
                    }
                    errors.append(summary)
                    self._write_json(self._ngo_dir(rd, ngo) / "error.json", summary)

                summaries = [row for row in summaries if row.get("ngo_id") != ngo_id] + [summary]
                summaries.sort(key=lambda row: str(row.get("ngo_name") or "").lower())
                self._write_json(rd / "master_summary.json", summaries)
                processed = sum(1 for row in summaries if row.get("status") in {"complete", "failed"})
                self._patch_status(
                    rd,
                    processed=processed,
                    failed=sum(1 for row in summaries if row.get("status") == "failed"),
                    completed=sum(1 for row in summaries if row.get("status") == "complete"),
                    current_step="NGO dossier saved",
                )
                self.job_update(run_id, status="running", stage="processing", processed=processed, total=len(ngos))
                self._rebuild_aggregate_outputs(rd, payload)

            self._rebuild_aggregate_outputs(rd, payload)
            final_status = "partial" if errors else "complete"
            final_stage = "partial_results_ready" if errors else "results_ready"
            self._patch_status(
                rd,
                run_status=final_status,
                stage=final_stage,
                processed=len(ngos),
                completed=sum(1 for row in summaries if row.get("status") == "complete"),
                failed=sum(1 for row in summaries if row.get("status") == "failed"),
                current_ngo="",
                current_step="Export bundle ready",
                message="Deep Enrichment completed" if not errors else "Deep Enrichment completed with some failed NGOs",
            )
            self.job_update(run_id, status="complete" if not errors else "partial", stage=final_stage, processed=len(ngos), total=len(ngos), error="" if not errors else f"{len(errors)} NGO(s) failed")
        except Exception as exc:
            self._patch_status(rd, run_status="error", stage="error", error=str(exc)[:1000], message="Deep Enrichment stopped unexpectedly")
            self.job_update(run_id, status="error", stage="error", error=str(exc)[:1000])
        finally:
            self.firecrawl_jobs.pop(run_id, None)

    # ------------------------------------------------------------------
    # Per-NGO processing
    # ------------------------------------------------------------------
    def _process_ngo(self, run_id: str, rd: Path, ngo: dict, options: dict, cancel_event: threading.Event) -> dict:
        ngo_dir = self._ngo_dir(rd, ngo)
        ngo_dir.mkdir(parents=True, exist_ok=True)
        website = str(ngo.get("website") or "").strip()
        serper_queries_used = 0
        discovered_website = False

        if not website:
            self._patch_status(rd, stage="website_discovery", current_step="Finding official website")
            website, discovery_record = self._discover_official_website(ngo["ngo_name"])
            serper_queries_used += 1
            self._write_json(ngo_dir / "website_discovery.json", discovery_record)
            discovered_website = bool(website)

        official_pages: list[dict] = []
        firecrawl_credits = 0
        crawl_notes: list[str] = []
        if website:
            self._patch_status(rd, stage="website_crawling", current_step="Crawling complete official website")
            try:
                website = self.validate_public_url(website)
                official_pages, firecrawl_credits, crawl_notes = self._crawl_official_site(
                    run_id, rd, website, options["max_pages_per_site"], cancel_event
                )
            except Exception as exc:
                crawl_notes.append(f"Official-site crawl failed: {exc}")
        else:
            crawl_notes.append("No official website could be identified automatically.")

        if self._cancelled(run_id, cancel_event):
            raise CancelledError()

        self._write_jsonl(ngo_dir / "official_pages.jsonl", official_pages)
        official_highlights = self._extract_highlights(official_pages, "ngo_website")
        self._write_json(ngo_dir / "official_highlights.json", official_highlights)

        category_hint = str(ngo.get("category") or "uncategorised")
        queries = self._build_queries(ngo["ngo_name"], category_hint, official_highlights, options["serper_queries_per_ngo"])
        self._patch_status(rd, stage="external_research", current_step=f"Running {len(queries)} media and evidence searches")
        search_records = []
        candidate_results: list[dict] = []
        for q_index, query in enumerate(queries):
            if self._cancelled(run_id, cancel_event):
                raise CancelledError()
            self._patch_status(rd, current_step=f"External search {q_index + 1}/{len(queries)}", current_search=query)
            try:
                response = self.serper_post({"q": query, "num": options["serper_results_per_query"], "gl": "in", "hl": "en"}, 35)
                serper_queries_used += 1
                organic = response.get("organic") or []
                record = {"query": query, "organic": organic[: options["serper_results_per_query"]]}
                search_records.append(record)
                for rank, item in enumerate(organic[: options["serper_results_per_query"]], start=1):
                    candidate_results.append({
                        "query": query,
                        "rank": rank,
                        "title": item.get("title") or "",
                        "url": item.get("link") or item.get("url") or "",
                        "snippet": item.get("snippet") or "",
                        "date": item.get("date") or "",
                        "sitelinks": item.get("sitelinks") or [],
                    })
            except Exception as exc:
                search_records.append({"query": query, "error": str(exc)[:300], "organic": []})
            time.sleep(float(os.environ.get("ENRICHMENT_SERPER_DELAY_SECONDS", "0.15")))

        self._write_jsonl(ngo_dir / "search_results.jsonl", search_records)
        external_candidates = self._dedupe_external_candidates(candidate_results, website)
        external_sources = []
        firecrawl_fallbacks = 0
        for source_index, candidate in enumerate(external_candidates[: options["max_external_sources"]]):
            if self._cancelled(run_id, cancel_event):
                raise CancelledError()
            self._patch_status(
                rd,
                stage="external_fetching",
                current_step=f"Reading external source {source_index + 1}/{min(len(external_candidates), options['max_external_sources'])}",
                current_url=candidate.get("url") or "",
            )
            source = self._fetch_external_source(candidate, ngo["ngo_name"])
            if source.get("fetch_status") == "failed" and firecrawl_fallbacks < options["external_firecrawl_fallbacks"]:
                try:
                    scraped = self._firecrawl_scrape(candidate.get("url") or "")
                    firecrawl_fallbacks += 1
                    firecrawl_credits += int(scraped.get("credits_used") or 1)
                    source.update({
                        "fetch_status": "firecrawl",
                        "final_url": scraped.get("url") or candidate.get("url"),
                        "full_text": scraped.get("markdown") or "",
                        "metadata": scraped.get("metadata") or {},
                        "fetch_error": "",
                    })
                    source["identity_match"] = self._identity_match(ngo["ngo_name"], " ".join([
                        source.get("title") or "", source.get("snippet") or "", source.get("full_text") or ""
                    ]))
                except Exception as exc:
                    source["firecrawl_fallback_error"] = str(exc)[:300]
            external_sources.append(source)

        self._write_jsonl(ngo_dir / "external_sources.jsonl", external_sources)
        external_highlights = self._extract_highlights(external_sources, "external")
        self._write_json(ngo_dir / "external_highlights.json", external_highlights)

        dossier = {
            "schema_version": "1.0",
            "ngo_id": ngo["ngo_id"],
            "ngo_name": ngo["ngo_name"],
            "website": website,
            "website_was_discovered": discovered_website,
            "pm_context": {
                "reviewer": ngo.get("pm_reviewer") or "",
                "rating": ngo.get("pm_rating"),
                "comment": ngo.get("pm_comment") or "",
                "one_line_understanding": ngo.get("one_line_understanding") or "",
            },
            "category_input": {
                "primary": category_hint,
                "primary_label": CATEGORY_LABELS.get(category_hint, CATEGORY_LABELS["uncategorised"]),
                "secondary": ngo.get("secondary_categories") or [],
            },
            "crawl": {
                "status": "complete" if official_pages else "limited",
                "pages_collected": len(official_pages),
                "page_types": dict(Counter(page.get("page_type") or "other" for page in official_pages)),
                "firecrawl_credits_used": firecrawl_credits,
                "notes": crawl_notes,
            },
            "website_pages": official_pages,
            "website_candidate_highlights": official_highlights,
            "external_research": {
                "queries": search_records,
                "queries_used": serper_queries_used,
                "sources": external_sources,
                "source_count": len(external_sources),
                "independently_fetched_count": sum(1 for source in external_sources if source.get("fetch_status") in {"direct", "firecrawl"}),
                "adverse_queries_completed": sum(1 for q in queries if any(term in q.lower() for term in ["controversy", "complaint", "fraud", "closure", "inactive"])),
            },
            "external_candidate_highlights": external_highlights,
            "preliminary_ai": {"enabled": False, "status": "not_run"},
            "generated_at": self.utc_now_iso(),
        }

        if options["use_haiku"]:
            self._patch_status(rd, stage="preliminary_ai", current_step="Optional Haiku categorisation and preliminary signals")
            dossier["preliminary_ai"] = self._run_haiku(dossier)

        dossier["model_ready_summary"] = self._model_ready_summary(dossier)
        self._write_json(ngo_dir / "evidence.json", dossier)
        self._write_text(ngo_dir / "dossier.md", self._dossier_markdown(dossier))
        self._write_sources_csv(ngo_dir / "sources.csv", dossier)
        self._increment_usage(rd, firecrawl_credits=firecrawl_credits, serper_queries=serper_queries_used, external_sources=len(external_sources), pages=len(official_pages))
        return dossier

    # ------------------------------------------------------------------
    # Firecrawl
    # ------------------------------------------------------------------
    def _firecrawl_keys(self) -> list[str]:
        """Return de-duplicated Firecrawl keys in configured order.

        FIRECRAWL_API_KEYS is the preferred multi-key variable. The legacy
        FIRECRAWL_API_KEY remains supported. Values may be comma or newline
        separated; whitespace around each key is ignored.
        """
        raw_multi = str(os.environ.get("FIRECRAWL_API_KEYS") or "").strip()
        raw_single = str(os.environ.get("FIRECRAWL_API_KEY") or "").strip()
        raw = raw_multi or raw_single
        keys: list[str] = []
        seen: set[str] = set()
        for value in re.split(r"[,\n\r]+", raw):
            key = value.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def _firecrawl_key_label(self, key: str) -> str:
        if not key:
            return "unconfigured"
        digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()[:8]
        tail = key[-4:] if len(key) >= 4 else key
        return f"key-{digest}-…{tail}"

    def _enabled_firecrawl_keys(self) -> list[str]:
        configured = self._firecrawl_keys()
        with self._firecrawl_lock:
            return [key for key in configured if key not in self._firecrawl_disabled]

    def _disabled_firecrawl_key_labels(self) -> list[dict]:
        configured = set(self._firecrawl_keys())
        with self._firecrawl_lock:
            return [
                {"key": self._firecrawl_key_label(key), "reason": reason}
                for key, reason in self._firecrawl_disabled.items()
                if key in configured
            ]

    def _disable_firecrawl_key(self, key: str, reason: str) -> None:
        if not key:
            return
        with self._firecrawl_lock:
            self._firecrawl_disabled[key] = str(reason or "unavailable")[:200]

    def _next_firecrawl_key(self, excluded: set[str] | None = None) -> str:
        excluded = excluded or set()
        with self._firecrawl_lock:
            keys = [
                key for key in self._firecrawl_keys()
                if key not in self._firecrawl_disabled and key not in excluded
            ]
            if not keys:
                configured = self._firecrawl_keys()
                if not configured:
                    raise RuntimeError("FIRECRAWL_API_KEYS or FIRECRAWL_API_KEY is not configured")
                disabled = ", ".join(item["key"] for item in self._disabled_firecrawl_key_labels())
                detail = f" Disabled keys: {disabled}." if disabled else ""
                raise RuntimeError(f"No usable Firecrawl API keys remain.{detail}")
            key = keys[self._firecrawl_cursor % len(keys)]
            self._firecrawl_cursor = (self._firecrawl_cursor + 1) % max(1, len(keys))
            return key

    def _firecrawl_request_on_key(
        self,
        method: str,
        path_or_url: str,
        *,
        api_key: str,
        payload: dict | None = None,
        timeout: int = 90,
    ) -> dict:
        if not api_key:
            raise RuntimeError("A Firecrawl API key is required")
        url = path_or_url if str(path_or_url).startswith("http") else f"https://api.firecrawl.dev/v2{path_or_url}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        last_error = ""
        for attempt in range(5):
            try:
                response = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt >= 4:
                    raise RuntimeError(f"Firecrawl request failed: {exc}")
                time.sleep(min(2 ** attempt, 12))
                continue
            if response.status_code in {200, 201, 202}:
                try:
                    return response.json()
                except Exception:
                    return {"success": True, "raw": response.text}
            body = response.text[:500]
            if response.status_code == 429:
                wait_for = int(response.headers.get("Retry-After") or min(2 ** attempt, 20))
                time.sleep(max(1, wait_for))
                last_error = body
                continue
            if response.status_code in {500, 502, 503, 504} and attempt < 4:
                time.sleep(min(2 ** attempt, 15))
                last_error = body
                continue
            if response.status_code in {401, 402, 403}:
                reason = {
                    401: "authentication rejected",
                    402: "credits exhausted or payment required",
                    403: "key forbidden",
                }[response.status_code]
                self._disable_firecrawl_key(api_key, reason)
                raise FirecrawlKeyUnavailable(response.status_code, reason, body)
            raise RuntimeError(f"Firecrawl failed {response.status_code}: {body}")
        raise RuntimeError(f"Firecrawl request failed: {last_error}")

    def _firecrawl_request_with_key(
        self,
        method: str,
        path_or_url: str,
        payload: dict | None = None,
        timeout: int = 90,
        *,
        api_key: str | None = None,
        allow_key_failover: bool = True,
    ) -> tuple[dict, str]:
        """Execute a Firecrawl request and return both response and key used.

        New independent requests may fail over across configured keys. Requests
        tied to an existing crawl must pass api_key and disable failover so the
        crawl remains attached to the account that created it.
        """
        if api_key:
            try:
                return self._firecrawl_request_on_key(
                    method, path_or_url, api_key=api_key, payload=payload, timeout=timeout
                ), api_key
            except FirecrawlKeyUnavailable:
                if not allow_key_failover:
                    raise

        attempted: set[str] = set()
        last_error: Exception | None = None
        while True:
            try:
                key = self._next_firecrawl_key(attempted)
            except Exception:
                if last_error is not None:
                    raise RuntimeError(str(last_error)) from last_error
                raise
            attempted.add(key)
            try:
                data = self._firecrawl_request_on_key(
                    method, path_or_url, api_key=key, payload=payload, timeout=timeout
                )
                return data, key
            except FirecrawlKeyUnavailable as exc:
                last_error = exc
                if not allow_key_failover:
                    raise
                continue

    def _firecrawl_request(
        self,
        method: str,
        path_or_url: str,
        payload: dict | None = None,
        timeout: int = 90,
        *,
        api_key: str | None = None,
        allow_key_failover: bool = True,
    ) -> dict:
        data, _ = self._firecrawl_request_with_key(
            method,
            path_or_url,
            payload,
            timeout,
            api_key=api_key,
            allow_key_failover=allow_key_failover,
        )
        return data

    def _crawl_official_site(self, run_id: str, rd: Path, website: str, limit: int, cancel_event: threading.Event) -> tuple[list[dict], int, list[str]]:
        exclude_paths = [
            ".*(?:/|^)(?:privacy|terms|cookie|donate|donation|checkout|login|signin|wp-admin|cart|account)(?:/|$).*",
            ".*(?:/|^)(?:tag|author|search|feed)(?:/|$).*",
        ]
        payload = {
            "url": website,
            "excludePaths": exclude_paths,
            "sitemap": "include",
            "ignoreQueryParameters": True,
            "limit": limit,
            "crawlEntireDomain": True,
            "allowExternalLinks": False,
            "allowSubdomains": bool(os.environ.get("ENRICHMENT_ALLOW_SUBDOMAINS", "").lower() in {"1", "true", "yes"}),
            "maxConcurrency": int(os.environ.get("FIRECRAWL_SITE_CONCURRENCY", "2")),
            "scrapeOptions": {
                "formats": ["markdown", "links"],
                "onlyMainContent": True,
                "onlyCleanContent": False,
                "removeBase64Images": True,
                "blockAds": True,
                "parsers": ["pdf"],
                "timeout": int(os.environ.get("FIRECRAWL_PAGE_TIMEOUT_MS", "60000")),
            },
        }
        # Starting a crawl may fail over to another configured key. Once the
        # crawl is created, every poll, pagination request and cancellation uses
        # the same key because Firecrawl crawl IDs are account-scoped.
        started, crawl_key = self._firecrawl_request_with_key("POST", "/crawl", payload, timeout=120)
        crawl_id = str(started.get("id") or "")
        if not crawl_id:
            raise RuntimeError(f"Firecrawl did not return a crawl ID: {str(started)[:300]}")
        key_label = self._firecrawl_key_label(crawl_key)
        self.firecrawl_jobs[run_id] = {"crawl_id": crawl_id, "api_key": crawl_key, "key_label": key_label}
        self._patch_status(rd, firecrawl_job_id=crawl_id, firecrawl_key=key_label)
        deadline = time.time() + int(os.environ.get("FIRECRAWL_CRAWL_TIMEOUT_SECONDS", "1800"))
        result = {}
        while time.time() < deadline:
            if self._cancelled(run_id, cancel_event):
                try:
                    self._firecrawl_request(
                        "DELETE",
                        f"/crawl/{crawl_id}",
                        timeout=30,
                        api_key=crawl_key,
                        allow_key_failover=False,
                    )
                except Exception:
                    pass
                raise CancelledError()
            result = self._firecrawl_request(
                "GET",
                f"/crawl/{crawl_id}",
                timeout=120,
                api_key=crawl_key,
                allow_key_failover=False,
            )
            crawl_status = str(result.get("status") or "").lower()
            self._patch_status(
                rd,
                firecrawl_job_id=crawl_id,
                firecrawl_key=key_label,
                current_step=f"Firecrawl: {result.get('completed', 0)}/{result.get('total', '?')} pages",
            )
            if crawl_status in {"completed", "failed", "cancelled", "canceled"}:
                break
            time.sleep(float(os.environ.get("FIRECRAWL_POLL_SECONDS", "4")))
        else:
            try:
                self._firecrawl_request(
                    "DELETE",
                    f"/crawl/{crawl_id}",
                    timeout=30,
                    api_key=crawl_key,
                    allow_key_failover=False,
                )
            except Exception:
                pass
            raise RuntimeError("Firecrawl site crawl exceeded the configured timeout")

        if str(result.get("status") or "").lower() == "failed":
            raise RuntimeError(str(result.get("error") or "Firecrawl crawl failed"))

        documents = list(result.get("data") or [])
        next_url = result.get("next")
        seen_next = set()
        while next_url and next_url not in seen_next:
            seen_next.add(next_url)
            page = self._firecrawl_request(
                "GET",
                str(next_url),
                timeout=120,
                api_key=crawl_key,
                allow_key_failover=False,
            )
            documents.extend(page.get("data") or [])
            next_url = page.get("next")

        pages = []
        seen_urls = set()
        seen_hashes = set()
        for doc in documents:
            metadata = doc.get("metadata") or {}
            url = str(metadata.get("sourceURL") or metadata.get("url") or doc.get("url") or "").strip()
            markdown = str(doc.get("markdown") or doc.get("content") or "").strip()
            if not url and not markdown:
                continue
            canonical = self._canonical_url(url)
            content_hash = hashlib.sha1(re.sub(r"\s+", " ", markdown).strip().encode("utf-8", errors="ignore")).hexdigest()
            if canonical and canonical in seen_urls:
                continue
            if content_hash in seen_hashes:
                continue
            if canonical:
                seen_urls.add(canonical)
            seen_hashes.add(content_hash)
            title = str(metadata.get("title") or self._title_from_url(url) or "Untitled page")
            pages.append({
                "url": url,
                "title": title,
                "page_type": self._page_type(url, title),
                "published_at": metadata.get("publishedTime") or metadata.get("article:published_time") or metadata.get("date") or "",
                "description": metadata.get("description") or "",
                "language": metadata.get("language") or "",
                "markdown": markdown,
                "links": doc.get("links") or [],
                "metadata": metadata,
                "content_hash": content_hash,
            })
        pages.sort(key=lambda page: (self._page_type_priority(page.get("page_type") or "other"), str(page.get("title") or "").lower()))
        notes = []
        if len(pages) >= limit:
            notes.append(f"Crawl reached the configured {limit}-page limit; the site may contain additional pages.")
        if result.get("status") == "cancelled":
            notes.append("Firecrawl reported a cancelled crawl.")
        self.firecrawl_jobs.pop(run_id, None)
        return pages, int(result.get("creditsUsed") or len(pages)), notes

    def _firecrawl_scrape(self, url: str) -> dict:
        url = self.validate_public_url(url)
        response, scrape_key = self._firecrawl_request_with_key("POST", "/scrape", {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "onlyCleanContent": False,
            "removeBase64Images": True,
            "blockAds": True,
            "parsers": ["pdf"],
            "timeout": int(os.environ.get("FIRECRAWL_PAGE_TIMEOUT_MS", "60000")),
        }, timeout=120)
        data = response.get("data") or response
        metadata = data.get("metadata") or {}
        return {
            "markdown": data.get("markdown") or "",
            "metadata": metadata,
            "url": metadata.get("sourceURL") or metadata.get("url") or url,
            "credits_used": int(response.get("creditsUsed") or data.get("creditsUsed") or 1),
            "firecrawl_key": self._firecrawl_key_label(scrape_key),
        }

    # ------------------------------------------------------------------
    # Search and external source collection
    # ------------------------------------------------------------------
    def _discover_official_website(self, name: str) -> tuple[str, dict]:
        query = f'"{name}" official website'
        response = self.serper_post({"q": query, "num": 10, "gl": "in", "hl": "en"}, 35)
        candidates = []
        for item in response.get("organic") or []:
            url = str(item.get("link") or item.get("url") or "").strip()
            if not url:
                continue
            domain = self._domain(url)
            if self._skip_external_domain(domain):
                continue
            score = self._name_token_coverage(name, " ".join([item.get("title") or "", item.get("snippet") or "", domain]))
            candidates.append({"url": url, "title": item.get("title") or "", "snippet": item.get("snippet") or "", "score": score})
        candidates.sort(key=lambda row: row.get("score", 0), reverse=True)
        picked = candidates[0]["url"] if candidates and candidates[0].get("score", 0) >= 0.34 else ""
        return picked, {"query": query, "picked": picked, "candidates": candidates[:10]}

    def _build_queries(self, name: str, category: str, highlights: list[dict], cap: int) -> list[str]:
        base_queries = [template.format(name=name) for template in BASE_SEARCH_TEMPLATES]
        # Keep the core media/outcome/adverse families first, then insert model-specific
        # searches before the lower-priority long tail so a 35-query cap still reflects
        # the NGO's actual operating model.
        queries = base_queries[:22]
        queries.extend(template.format(name=name) for template in CATEGORY_SEARCH_TEMPLATES.get(category, []))
        queries.extend(base_queries[22:])
        # High-signal terms found on the website become explicit verification queries.
        matched_terms = []
        for finding in sorted(highlights, key=lambda row: int(row.get("weight") or 0), reverse=True):
            term = str(finding.get("matched_text") or "").strip()
            if term and len(term) <= 100 and term.lower() not in {x.lower() for x in matched_terms}:
                matched_terms.append(term)
            if len(matched_terms) >= 10:
                break
        for term in matched_terms:
            queries.append(f'"{name}" "{term}"')
        unique = []
        seen = set()
        for query in queries:
            key = re.sub(r"\s+", " ", query.strip().lower())
            if key and key not in seen:
                seen.add(key)
                unique.append(query)
        return unique[:cap]

    def _dedupe_external_candidates(self, rows: list[dict], website: str) -> list[dict]:
        official_domain = self._domain(website)
        best: dict[str, dict] = {}
        for row in rows:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            try:
                self.validate_public_url(url)
            except Exception:
                continue
            domain = self._domain(url)
            if not domain or self._skip_external_domain(domain):
                continue
            if official_domain and (domain == official_domain or domain.endswith("." + official_domain)):
                continue
            canonical = self._canonical_url(url)
            if not canonical:
                continue
            existing = best.get(canonical)
            if not existing or int(row.get("rank") or 999) < int(existing.get("rank") or 999):
                best[canonical] = row
        values = list(best.values())
        values.sort(key=lambda row: (int(row.get("rank") or 999), -self._source_priority(self._domain(row.get("url") or ""))))
        return values

    def _fetch_external_source(self, candidate: dict, ngo_name: str) -> dict:
        url = str(candidate.get("url") or "")
        source = {
            **candidate,
            "domain": self._domain(url),
            "source_type": self._source_type(url),
            "fetch_status": "failed",
            "final_url": url,
            "full_text": "",
            "fetch_error": "",
            "identity_match": "uncertain",
        }
        try:
            final_url, html = self.safe_fetch_text(url, timeout=18, max_bytes=3_000_000)
            soup = self.make_soup(html)
            for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form"]):
                try:
                    tag.decompose()
                except Exception:
                    pass
            text = "\n".join(part.strip() for part in soup.stripped_strings if part.strip())
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if len(text) < 350:
                raise ValueError("Too little readable text")
            source.update({"fetch_status": "direct", "final_url": final_url, "full_text": text[:250_000]})
        except Exception as exc:
            source["fetch_error"] = str(exc)[:300]
        source["identity_match"] = self._identity_match(ngo_name, " ".join([
            source.get("title") or "", source.get("snippet") or "", source.get("full_text") or ""
        ]))
        return source

    # ------------------------------------------------------------------
    # Evidence extraction and optional Haiku
    # ------------------------------------------------------------------
    def _extract_highlights(self, records: list[dict], source_kind: str) -> list[dict]:
        findings = []
        seen = set()
        for record in records:
            text = str(record.get("markdown") or record.get("full_text") or record.get("snippet") or "")
            if not text:
                continue
            paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", text) if len(p.strip()) >= 35]
            for family in TRIGGER_FAMILIES:
                for pattern in family["patterns"]:
                    regex = re.compile(pattern, flags=re.I)
                    for paragraph in paragraphs:
                        match = regex.search(paragraph)
                        if not match:
                            continue
                        excerpt = paragraph[:1400]
                        dedupe = hashlib.sha1(re.sub(r"\W+", " ", excerpt.lower()).encode("utf-8")).hexdigest()
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        findings.append({
                            "family": family["key"],
                            "label": family["label"],
                            "weight": family["weight"],
                            "matched_text": match.group(0),
                            "exact_excerpt": excerpt,
                            "source_kind": source_kind,
                            "source_url": record.get("url") or record.get("final_url") or "",
                            "source_title": record.get("title") or record.get("metadata", {}).get("title") or "",
                            "publisher": record.get("domain") or "",
                            "publication_date": record.get("date") or record.get("published_at") or "",
                            "identity_match": record.get("identity_match") or ("official" if source_kind == "ngo_website" else "uncertain"),
                        })
        findings.sort(key=lambda row: (-int(row.get("weight") or 0), str(row.get("source_title") or "")))
        return findings[:150]

    def _run_haiku(self, dossier: dict) -> dict:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return {"enabled": False, "status": "skipped", "reason": "ANTHROPIC_API_KEY is not configured"}
        anthropic_mod = self.get_anthropic()
        if anthropic_mod is None:
            return {"enabled": False, "status": "skipped", "reason": "Anthropic SDK is unavailable"}
        compact = self._haiku_input(dossier)
        schema = {
            "primary_category": "one CATEGORY key",
            "secondary_categories": ["up to two CATEGORY keys"],
            "category_reason": "brief evidence-grounded explanation",
            "standout_value": {"value": "1-5 or null", "reason": "brief"},
            "transformation_potential_signal": {"value": "1-5 or null", "reason": "brief"},
            "demonstrated_transformation_signal": {"value": "1-5 or null", "reason": "brief"},
            "evidence_strength": {"value": "1-5 or null", "reason": "brief"},
            "dfp_fit_signal": {"value": "1-5 or null", "reason": "brief"},
            "most_noteworthy_findings": ["up to 8 concise findings"],
            "evidence_gaps": ["specific gaps"],
            "preliminary_view": "non-final interpretation",
        }
        prompt = f"""You are performing a preliminary, non-final structuring pass for DFP 2.0.
Do not overwrite or imitate the PM rating. Do not produce a final ranking. Separate unusual prestige from child-transformation evidence. Missing documentation is not negative evidence.
Choose categories only from this library:\n{json.dumps(CATEGORY_LABELS, ensure_ascii=False)}
Return only valid JSON matching this shape:\n{json.dumps(schema, ensure_ascii=False)}

Evidence dossier:\n{compact}
"""
        try:
            client = anthropic_mod.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            response = client.messages.create(
                model=os.environ.get("ENRICHMENT_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=int(os.environ.get("ENRICHMENT_HAIKU_MAX_TOKENS", "2200")),
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text").strip()
            parsed = self._parse_json_object(text)
            if not isinstance(parsed, dict):
                raise ValueError("Haiku did not return a JSON object")
            parsed.update({
                "enabled": True,
                "status": "complete",
                "model": os.environ.get("ENRICHMENT_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
                "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", None),
                "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", None),
                "non_final": True,
            })
            return parsed
        except Exception as exc:
            return {"enabled": True, "status": "failed", "error": str(exc)[:500], "non_final": True}

    def _haiku_input(self, dossier: dict) -> str:
        official = dossier.get("website_candidate_highlights") or []
        external = dossier.get("external_candidate_highlights") or []
        pages = dossier.get("website_pages") or []
        sources = dossier.get("external_research", {}).get("sources") or []
        page_summaries = []
        for page in pages[:20]:
            page_summaries.append({
                "title": page.get("title"), "type": page.get("page_type"), "url": page.get("url"),
                "excerpt": str(page.get("markdown") or "")[:1800],
            })
        source_summaries = []
        for source in sources[:20]:
            source_summaries.append({
                "title": source.get("title"), "publisher": source.get("domain"), "url": source.get("url"),
                "identity_match": source.get("identity_match"),
                "excerpt": str(source.get("full_text") or source.get("snippet") or "")[:1600],
            })
        compact = {
            "ngo_name": dossier.get("ngo_name"),
            "pm_context": dossier.get("pm_context"),
            "input_category": dossier.get("category_input"),
            "official_highlights": official[:40],
            "external_highlights": external[:40],
            "key_pages": page_summaries,
            "external_sources": source_summaries,
        }
        return json.dumps(compact, ensure_ascii=False)[:110_000]

    # ------------------------------------------------------------------
    # Dossier and aggregate outputs
    # ------------------------------------------------------------------
    def _model_ready_summary(self, dossier: dict) -> dict:
        official = dossier.get("website_candidate_highlights") or []
        external = dossier.get("external_candidate_highlights") or []
        all_findings = sorted(official + external, key=lambda row: int(row.get("weight") or 0), reverse=True)
        return {
            "top_candidate_findings": all_findings[:25],
            "official_pages_collected": dossier.get("crawl", {}).get("pages_collected", 0),
            "external_sources_collected": dossier.get("external_research", {}).get("source_count", 0),
            "external_queries_used": dossier.get("external_research", {}).get("queries_used", 0),
            "preliminary_category": dossier.get("preliminary_ai", {}).get("primary_category") or dossier.get("category_input", {}).get("primary"),
            "preliminary_category_label": CATEGORY_LABELS.get(
                dossier.get("preliminary_ai", {}).get("primary_category") or dossier.get("category_input", {}).get("primary") or "uncategorised",
                CATEGORY_LABELS["uncategorised"],
            ),
        }

    def _summary_from_dossier(self, dossier: dict) -> dict:
        prelim = dossier.get("preliminary_ai") or {}
        summary = dossier.get("model_ready_summary") or {}
        top = summary.get("top_candidate_findings") or []
        return {
            "ngo_id": dossier.get("ngo_id"),
            "ngo_name": dossier.get("ngo_name"),
            "website": dossier.get("website"),
            "pm_reviewer": dossier.get("pm_context", {}).get("reviewer"),
            "pm_rating": dossier.get("pm_context", {}).get("rating"),
            "primary_category": prelim.get("primary_category") or dossier.get("category_input", {}).get("primary") or "uncategorised",
            "primary_category_label": CATEGORY_LABELS.get(prelim.get("primary_category") or dossier.get("category_input", {}).get("primary") or "uncategorised", CATEGORY_LABELS["uncategorised"]),
            "standout_value": self._signal_value(prelim, "standout_value"),
            "transformation_potential_signal": self._signal_value(prelim, "transformation_potential_signal"),
            "demonstrated_transformation_signal": self._signal_value(prelim, "demonstrated_transformation_signal"),
            "evidence_strength": self._signal_value(prelim, "evidence_strength"),
            "dfp_fit_signal": self._signal_value(prelim, "dfp_fit_signal"),
            "top_finding": top[0].get("exact_excerpt") if top else "",
            "pages_collected": dossier.get("crawl", {}).get("pages_collected", 0),
            "external_sources": dossier.get("external_research", {}).get("source_count", 0),
            "serper_queries_used": dossier.get("external_research", {}).get("queries_used", 0),
            "firecrawl_credits_used": dossier.get("crawl", {}).get("firecrawl_credits_used", 0),
            "haiku_status": prelim.get("status") or "not_run",
            "status": "complete",
            "error": "",
        }

    def _signal_value(self, prelim: dict, key: str) -> Any:
        value = prelim.get(key)
        if isinstance(value, dict):
            return value.get("value")
        return value if value is not None else ""

    def _dossier_markdown(self, dossier: dict, compact: bool = False) -> str:
        pm = dossier.get("pm_context") or {}
        crawl = dossier.get("crawl") or {}
        external = dossier.get("external_research") or {}
        prelim = dossier.get("preliminary_ai") or {}
        top_findings = dossier.get("model_ready_summary", {}).get("top_candidate_findings") or []
        lines = [
            f"# {dossier.get('ngo_name') or 'Untitled NGO'}",
            "",
            "> This is an evidence dossier, not a final transformation rating.",
            "",
            "## Original PM context",
            f"- Reviewer: {pm.get('reviewer') or '—'}",
            f"- PM rating: {pm.get('rating') if pm.get('rating') not in (None, '') else '—'}",
            f"- PM comment: {pm.get('comment') or '—'}",
            f"- Existing understanding: {pm.get('one_line_understanding') or '—'}",
            "",
            "## Research coverage",
            f"- Official website: {dossier.get('website') or 'Not identified'}",
            f"- Official pages collected: {crawl.get('pages_collected', 0)}",
            f"- External Serper queries: {external.get('queries_used', 0)}",
            f"- External sources collected: {external.get('source_count', 0)}",
            f"- Firecrawl credits used: {crawl.get('firecrawl_credits_used', 0)}",
            f"- Crawl notes: {'; '.join(crawl.get('notes') or []) or 'None'}",
            "",
            "## Candidate standout findings",
        ]
        if top_findings:
            for idx, finding in enumerate(top_findings[:25 if not compact else 12], start=1):
                lines.extend([
                    f"### {idx}. {finding.get('label') or 'Finding'}",
                    f"- Exact excerpt: {finding.get('exact_excerpt') or '—'}",
                    f"- Matched term: {finding.get('matched_text') or '—'}",
                    f"- Source: {finding.get('source_title') or 'Untitled'} — {finding.get('source_url') or '—'}",
                    f"- Source channel: {finding.get('source_kind') or '—'}; identity match: {finding.get('identity_match') or '—'}",
                    "",
                ])
        else:
            lines.extend(["No trigger-based standout excerpts were found.", ""])

        lines.extend(["## Official website index", ""])
        page_limit = 30 if not compact else 15
        for page in (dossier.get("website_pages") or [])[:page_limit]:
            excerpt = re.sub(r"\s+", " ", str(page.get("markdown") or "")).strip()[:1200 if not compact else 600]
            lines.extend([
                f"### {page.get('title') or 'Untitled page'}",
                f"- Type: {page.get('page_type') or 'other'}",
                f"- URL: {page.get('url') or '—'}",
                f"- Extract: {excerpt or '—'}",
                "",
            ])

        lines.extend(["## External media and evidence", ""])
        source_limit = 30 if not compact else 15
        for source in (external.get("sources") or [])[:source_limit]:
            excerpt = re.sub(r"\s+", " ", str(source.get("full_text") or source.get("snippet") or "")).strip()[:1500 if not compact else 700]
            lines.extend([
                f"### {source.get('title') or 'Untitled source'}",
                f"- Publisher/domain: {source.get('domain') or '—'}",
                f"- URL: {source.get('url') or '—'}",
                f"- Found through: {source.get('query') or '—'}",
                f"- Identity match: {source.get('identity_match') or 'uncertain'}",
                f"- Fetch status: {source.get('fetch_status') or '—'}",
                f"- Extract: {excerpt or '—'}",
                "",
            ])

        lines.extend([
            "## Preliminary categorisation and signals",
            "",
            "These fields are optional, non-final, and must not overwrite the PM rating.",
            "",
        ])
        if prelim.get("status") == "complete":
            lines.extend([
                f"- Primary category: {CATEGORY_LABELS.get(prelim.get('primary_category'), prelim.get('primary_category') or 'Uncategorised')}",
                f"- Secondary categories: {', '.join(CATEGORY_LABELS.get(x, x) for x in (prelim.get('secondary_categories') or [])) or '—'}",
                f"- Category reason: {prelim.get('category_reason') or '—'}",
                f"- Standout value: {self._format_signal(prelim.get('standout_value'))}",
                f"- Transformation potential signal: {self._format_signal(prelim.get('transformation_potential_signal'))}",
                f"- Demonstrated transformation signal: {self._format_signal(prelim.get('demonstrated_transformation_signal'))}",
                f"- Evidence strength: {self._format_signal(prelim.get('evidence_strength'))}",
                f"- DFP fit signal: {self._format_signal(prelim.get('dfp_fit_signal'))}",
                f"- Preliminary view: {prelim.get('preliminary_view') or '—'}",
                "",
                "### Evidence gaps",
            ])
            for gap in prelim.get("evidence_gaps") or []:
                lines.append(f"- {gap}")
            if not prelim.get("evidence_gaps"):
                lines.append("- None listed by the preliminary pass.")
        else:
            lines.append(f"Haiku pass: {prelim.get('status') or 'not run'} — {prelim.get('reason') or prelim.get('error') or 'No preliminary AI pass requested.'}")
        lines.extend(["", "## Source integrity note", "", "Website excerpts are self-reported unless separately corroborated. External sources retain their source URL and an identity-match flag. A trigger match is only a candidate finding; it is not proof that the claim is true or transformation-relevant.", ""])
        return "\n".join(lines)

    def _format_signal(self, value: Any) -> str:
        if isinstance(value, dict):
            return f"{value.get('value', '—')}/5 — {value.get('reason') or 'No explanation'}"
        return str(value or "—")

    def _write_sources_csv(self, path: Path, dossier: dict) -> None:
        rows = []
        for page in dossier.get("website_pages") or []:
            rows.append({
                "ngo_name": dossier.get("ngo_name"), "source_type": "ngo_website", "title": page.get("title"),
                "publisher": self._domain(page.get("url") or ""), "url": page.get("url"), "publication_date": page.get("published_at"),
                "fetch_status": "firecrawl", "identity_match": "official",
            })
        for source in dossier.get("external_research", {}).get("sources") or []:
            rows.append({
                "ngo_name": dossier.get("ngo_name"), "source_type": source.get("source_type"), "title": source.get("title"),
                "publisher": source.get("domain"), "url": source.get("url"), "publication_date": source.get("date"),
                "fetch_status": source.get("fetch_status"), "identity_match": source.get("identity_match"),
            })
        fields = ["ngo_name", "source_type", "title", "publisher", "url", "publication_date", "fetch_status", "identity_match"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: self._safe_csv(value) for key, value in row.items()})

    def _rebuild_aggregate_outputs(self, rd: Path, input_payload: dict) -> None:
        summaries = self._read_json(rd / "master_summary.json", [])
        self._write_master_csv(rd / "master_summary.csv", summaries)
        dossiers = []
        for ngo in input_payload.get("ngos") or []:
            path = self._ngo_dir(rd, ngo) / "evidence.json"
            if path.exists():
                data = self._read_json(path, {})
                if data:
                    dossiers.append(data)
        self._write_jsonl(rd / "all_dossiers.jsonl", dossiers)

        packet_parts = [
            "# DFP 2.0 — Deep Enrichment Research Packet",
            "",
            "Use this evidence to categorise and compare NGOs. Preserve the distinction between demonstrated transformation, transformation potential, evidence strength, and DFP operational fit. Do not treat weak online documentation as weak work.",
            "",
        ]
        for dossier in dossiers:
            packet_parts.append(self._dossier_markdown(dossier, compact=True))
            packet_parts.extend(["", "---", ""])
        self._write_text(rd / "gpt_fable_packet.md", "\n".join(packet_parts))

        packet_dir = rd / "gpt_fable_packets"
        if packet_dir.exists():
            shutil.rmtree(packet_dir, ignore_errors=True)
        packet_dir.mkdir(parents=True, exist_ok=True)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for dossier in dossiers:
            prelim = dossier.get("preliminary_ai") or {}
            category = prelim.get("primary_category") or dossier.get("category_input", {}).get("primary") or "uncategorised"
            grouped[str(category)].append(dossier)
        for category, category_dossiers in grouped.items():
            for batch_index in range(0, len(category_dossiers), 10):
                batch = category_dossiers[batch_index:batch_index + 10]
                parts = [
                    f"# {CATEGORY_LABELS.get(category, category)} — Deep Enrichment Packet",
                    "",
                    "Assess these NGOs first within their model. Separate demonstrated transformation, transformation potential, and evidence strength.",
                    "",
                ]
                for dossier in batch:
                    parts.append(self._dossier_markdown(dossier, compact=True))
                    parts.extend(["", "---", ""])
                filename = f"{self._slug(category)}-batch-{batch_index // 10 + 1:02d}.md"
                self._write_text(packet_dir / filename, "\n".join(parts))

        status = self._read_json(rd / "status.json", {})
        report = {
            "run_id": rd.name,
            "created_at": status.get("created_at"),
            "updated_at": status.get("updated_at"),
            "selected_count": len(input_payload.get("ngos") or []),
            "completed_count": sum(1 for row in summaries if row.get("status") == "complete"),
            "failed_count": sum(1 for row in summaries if row.get("status") == "failed"),
            "firecrawl_credits_used": status.get("firecrawl_credits_used", 0),
            "serper_queries_used": status.get("serper_queries_used", 0),
            "official_pages_collected": status.get("official_pages_collected", 0),
            "external_sources_collected": status.get("external_sources_collected", 0),
            "options": input_payload.get("options") or {},
            "categories": dict(Counter(row.get("primary_category_label") or "Uncategorised" for row in summaries if row.get("status") == "complete")),
        }
        self._write_json(rd / "run_report.json", report)
        self._build_zip(rd)

    def _write_master_csv(self, path: Path, rows: list[dict]) -> None:
        fields = [
            "ngo_id", "ngo_name", "website", "pm_reviewer", "pm_rating", "primary_category", "primary_category_label",
            "standout_value", "transformation_potential_signal", "demonstrated_transformation_signal", "evidence_strength",
            "dfp_fit_signal", "top_finding", "pages_collected", "external_sources", "serper_queries_used",
            "firecrawl_credits_used", "haiku_status", "status", "error",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: self._safe_csv(row.get(field, "")) for field in fields})

    def _build_zip(self, rd: Path) -> None:
        zip_path = rd / "deep_enrichment_export.zip"
        tmp_path = rd / ".deep_enrichment_export.zip.tmp"
        if tmp_path.exists():
            tmp_path.unlink()
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in rd.rglob("*"):
                if not path.is_file() or path in {zip_path, tmp_path}:
                    continue
                if path.name == "input.json":
                    # Input is included because it contains only NGO context, not API keys.
                    pass
                archive.write(path, arcname=str(path.relative_to(rd)))
        os.replace(tmp_path, zip_path)

    # ------------------------------------------------------------------
    # Status / persistence helpers
    # ------------------------------------------------------------------
    def _initial_status(self, run_id: str, total: int, options: dict) -> dict:
        return {
            "run_id": run_id,
            "module": "deep_enrichment",
            "run_status": "queued",
            "stage": "queued",
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "total": total,
            "current_ngo": "",
            "current_index": 0,
            "current_step": "Waiting for worker",
            "current_search": "",
            "current_url": "",
            "firecrawl_credits_used": 0,
            "serper_queries_used": 0,
            "official_pages_collected": 0,
            "external_sources_collected": 0,
            "options": options,
            "message": "Deep Enrichment queued",
            "error": "",
            "created_at": self.utc_now_iso(),
            "updated_at": self.utc_now_iso(),
        }

    def _increment_usage(self, rd: Path, *, firecrawl_credits: int, serper_queries: int, external_sources: int, pages: int) -> None:
        with self._status_lock:
            status = self._read_json(rd / "status.json", {})
            status["firecrawl_credits_used"] = int(status.get("firecrawl_credits_used") or 0) + int(firecrawl_credits or 0)
            status["serper_queries_used"] = int(status.get("serper_queries_used") or 0) + int(serper_queries or 0)
            status["external_sources_collected"] = int(status.get("external_sources_collected") or 0) + int(external_sources or 0)
            status["official_pages_collected"] = int(status.get("official_pages_collected") or 0) + int(pages or 0)
            self._write_status(rd, status)

    def _patch_status(self, rd: Path, **updates: Any) -> dict:
        with self._status_lock:
            status = self._read_json(rd / "status.json", {})
            status.update(updates)
            self._write_status(rd, status)
        try:
            self.job_update(
                rd.name,
                job_type="deep_enrichment",
                status=self._job_status(status),
                stage=status.get("stage"),
                processed=status.get("processed"),
                total=status.get("total"),
                current_item=status.get("current_ngo"),
                current_search=status.get("current_search"),
                current_url=status.get("current_url"),
                queries_used=status.get("serper_queries_used"),
                error=status.get("error"),
            )
        except Exception:
            pass
        return status

    def _write_status(self, rd: Path, status: dict) -> None:
        status["updated_at"] = self.utc_now_iso()
        self._write_json(rd / "status.json", status)

    def _job_status(self, status: dict) -> str:
        raw = str(status.get("run_status") or "").lower()
        if raw in {"complete", "partial", "cancelled", "error"}:
            return raw
        if raw in {"queued", "resuming"}:
            return raw
        return "running"

    # ------------------------------------------------------------------
    # Normalisation and utility helpers
    # ------------------------------------------------------------------
    def _normalise_ngos(self, rows: list[Any]) -> list[dict]:
        if not isinstance(rows, list):
            raise ValueError("ngos must be a list")
        output = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("ngo_name") or row.get("name") or row.get("NGO Name") or "").strip()
            if not name:
                continue
            website = str(row.get("website") or row.get("Website") or "").strip()
            if website and not website.startswith(("http://", "https://")):
                website = "https://" + website.lstrip("/")
            dedupe_key = (self._norm(name), self._domain(website))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            raw_rating = row.get("pm_rating", row.get("rating"))
            try:
                rating = int(float(str(raw_rating))) if raw_rating not in (None, "") else None
            except Exception:
                rating = None
            category = str(row.get("category") or row.get("primary_category") or "uncategorised").strip().lower().replace("-", "_").replace(" ", "_")
            if category not in CATEGORY_LABELS:
                category = "uncategorised"
            ngo_id = str(row.get("ngo_id") or row.get("ngo_ref") or "").strip()
            if not ngo_id:
                ngo_id = "ngo_" + hashlib.sha1(f"{name}|{website}|{row.get('reviewer') or row.get('pm_reviewer') or ''}".encode("utf-8")).hexdigest()[:12]
            output.append({
                "ngo_id": ngo_id,
                "ngo_name": name,
                "website": website,
                "pm_reviewer": str(row.get("pm_reviewer") or row.get("reviewer") or row.get("pm") or "").strip(),
                "pm_rating": rating,
                "pm_comment": str(row.get("pm_comment") or row.get("comment") or row.get("reason") or "").strip(),
                "one_line_understanding": str(row.get("one_line_understanding") or row.get("background") or row.get("summary") or "").strip(),
                "category": category,
                "secondary_categories": [x for x in (row.get("secondary_categories") or []) if x in CATEGORY_LABELS][:2],
            })
        return output

    def _options(self, value: dict) -> dict:
        value = value or {}
        return {
            "max_pages_per_site": self._clamp(value.get("max_pages_per_site"), 10, 100, int(os.environ.get("ENRICHMENT_MAX_PAGES_PER_SITE", "50"))),
            "serper_queries_per_ngo": self._clamp(value.get("serper_queries_per_ngo"), 10, 60, int(os.environ.get("ENRICHMENT_SERPER_QUERIES_PER_NGO", "35"))),
            "serper_results_per_query": self._clamp(value.get("serper_results_per_query"), 3, 10, int(os.environ.get("ENRICHMENT_SERPER_RESULTS_PER_QUERY", "5"))),
            "max_external_sources": self._clamp(value.get("max_external_sources"), 10, 60, int(os.environ.get("ENRICHMENT_MAX_EXTERNAL_SOURCES", "30"))),
            "external_firecrawl_fallbacks": self._clamp(value.get("external_firecrawl_fallbacks"), 0, 10, int(os.environ.get("ENRICHMENT_EXTERNAL_FIRECRAWL_FALLBACKS", "5"))),
            "use_haiku": bool(value.get("use_haiku", True)),
        }

    def _clamp(self, value: Any, low: int, high: int, default: int) -> int:
        try:
            number = int(value)
        except Exception:
            number = default
        return max(low, min(high, number))

    def _run_dir(self, run_id: str) -> Path:
        safe = "".join(ch for ch in str(run_id or "") if ch.isalnum() or ch in "_-")
        return self.runs_dir / safe

    def _ngo_dir(self, rd: Path, ngo: dict) -> Path:
        return rd / "ngo_research_packs" / f"{self._slug(ngo.get('ngo_name') or 'ngo')}-{str(ngo.get('ngo_id') or '')[-6:]}"

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _write_jsonl(self, path: Path, rows: list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _safe_csv(self, value: Any) -> str:
        text = "" if value is None else str(value).replace("\x00", "")
        if text.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + text
        return text

    def _slug(self, value: Any) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
        return slug[:80] or "uncategorised"

    def _norm(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    def _canonical_url(self, url: str) -> str:
        try:
            parsed = urlparse(str(url or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return ""
            path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
        except Exception:
            return ""

    def _domain(self, url: str) -> str:
        try:
            domain = (urlparse(str(url or "")).hostname or "").lower()
            return domain[4:] if domain.startswith("www.") else domain
        except Exception:
            return ""

    def _title_from_url(self, url: str) -> str:
        try:
            tail = (urlparse(url).path or "/").rstrip("/").split("/")[-1]
            return re.sub(r"[-_]+", " ", tail).strip().title() or "Home"
        except Exception:
            return "Untitled page"

    def _page_type(self, url: str, title: str) -> str:
        value = f"{url} {title}".lower()
        for label, terms in PAGE_TYPE_RULES:
            if any(term in value for term in terms):
                return label
        if str(url).lower().endswith(".pdf"):
            return "reports"
        return "other"

    def _page_type_priority(self, page_type: str) -> int:
        order = {"awards": 0, "outcomes": 1, "alumni": 2, "stories": 3, "programmes": 4, "reports": 5, "news": 6, "about": 7, "partners": 8, "leadership": 9, "other": 10}
        return order.get(page_type, 10)

    def _skip_external_domain(self, domain: str) -> bool:
        return any(domain == blocked or domain.endswith("." + blocked) for blocked in EXTERNAL_SKIP_DOMAINS)

    def _source_type(self, url: str) -> str:
        domain = self._domain(url)
        if domain.endswith(".gov.in") or domain.endswith(".gov") or ".nic.in" in domain:
            return "government"
        if domain.endswith(".edu") or domain.endswith(".ac.in"):
            return "university_or_academic"
        if any(token in domain for token in ["thehindu", "indianexpress", "timesofindia", "hindustantimes", "deccanherald", "newindianexpress", "bbc", "reuters", "ndtv", "scroll", "theprint", "news", "tribune"]):
            return "news_media"
        if any(token in domain for token in ["award", "foundation", "trust", "csr", "philanthropy"]):
            return "institutional_or_partner"
        return "other_external"

    def _source_priority(self, domain: str) -> int:
        source_type = self._source_type("https://" + domain if domain else "")
        return {"government": 5, "university_or_academic": 4, "news_media": 4, "institutional_or_partner": 3, "other_external": 1}.get(source_type, 1)

    def _meaningful_name_tokens(self, name: str) -> list[str]:
        stop = {"the", "and", "of", "for", "india", "trust", "foundation", "society", "organisation", "organization", "charitable", "educational", "education", "seva", "samiti", "sanstha"}
        return [token for token in self._norm(name).split() if len(token) >= 3 and token not in stop]

    def _name_token_coverage(self, name: str, text: str) -> float:
        tokens = self._meaningful_name_tokens(name)
        haystack = set(self._norm(text).split())
        if not tokens:
            return 1.0 if self._norm(name) and self._norm(name) in self._norm(text) else 0.0
        return sum(1 for token in tokens if token in haystack) / len(tokens)

    def _identity_match(self, name: str, text: str) -> str:
        norm_name = self._norm(name)
        norm_text = self._norm(text)
        if norm_name and norm_name in norm_text:
            return "likely"
        coverage = self._name_token_coverage(name, text)
        token_count = len(self._meaningful_name_tokens(name))
        if token_count >= 2 and coverage >= 0.67:
            return "likely"
        if coverage >= 0.34:
            return "possible"
        return "uncertain"

    def _parse_json_object(self, text: str) -> dict:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else {}
        except Exception:
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if match:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else {}
        return {}


class FirecrawlKeyUnavailable(RuntimeError):
    def __init__(self, status_code: int, reason: str, body: str = "") -> None:
        self.status_code = int(status_code)
        self.reason = str(reason or "Firecrawl key unavailable")
        self.body = str(body or "")[:500]
        super().__init__(f"Firecrawl key unavailable ({self.status_code}): {self.reason}")


class CancelledError(Exception):
    pass


def register_deep_enrichment(app: Any, **dependencies: Any) -> DeepEnrichmentRuntime:
    runtime = DeepEnrichmentRuntime(**dependencies)
    app.include_router(runtime.router)
    return runtime
