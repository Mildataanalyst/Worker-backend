import json
from pathlib import Path

from deep_enrichment import DeepEnrichmentRuntime, CATEGORY_LABELS


def runtime(tmp_path: Path):
    return DeepEnrichmentRuntime(
        runs_dir=tmp_path,
        serper_post=lambda payload, timeout=25: {"organic": []},
        has_serper_keys=lambda: True,
        safe_fetch_text=lambda *args, **kwargs: (args[0], "<html><body>Example Foundation represented India at a national championship.</body></html>"),
        validate_public_url=lambda url: url,
        make_soup=lambda html: __import__("bs4").BeautifulSoup(html, "html.parser"),
        get_anthropic=lambda: None,
        job_create=lambda *args, **kwargs: {},
        job_update=lambda *args, **kwargs: {},
        job_request_cancel=lambda *args, **kwargs: {},
        job_cancel_requested=lambda *args, **kwargs: False,
        utc_now_iso=lambda: "2026-07-12T00:00:00Z",
    )


def test_normalises_and_deduplicates_ngos(tmp_path):
    rt = runtime(tmp_path)
    rows = rt._normalise_ngos([
        {"ngo_name": "Example Foundation", "website": "example.org", "rating": "4", "reviewer": "Rachit"},
        {"ngo_name": "Example Foundation", "website": "https://example.org", "rating": 4},
    ])
    assert len(rows) == 1
    assert rows[0]["website"] == "https://example.org"
    assert rows[0]["pm_rating"] == 4


def test_query_plan_contains_media_and_adverse_searches(tmp_path):
    rt = runtime(tmp_path)
    queries = rt._build_queries("Example Foundation", "sports_led_development", [], 35)
    joined = "\n".join(queries).lower()
    assert "media coverage" in joined
    assert "controversy" in joined
    assert "represented india" in joined
    assert len(queries) <= 35


def test_highlights_preserve_exact_excerpt(tmp_path):
    rt = runtime(tmp_path)
    findings = rt._extract_highlights([
        {
            "url": "https://example.org/awards",
            "title": "Awards",
            "markdown": "The organisation was nominated for the Nobel Peace Prize and later received a national award.",
        }
    ], "ngo_website")
    assert findings
    assert "Nobel Peace Prize" in findings[0]["exact_excerpt"]
    assert findings[0]["source_url"] == "https://example.org/awards"


def test_aggregate_exports_markdown_jsonl_and_csv(tmp_path):
    rt = runtime(tmp_path)
    rd = tmp_path / "enrich_test"
    ngo = rt._normalise_ngos([{"ngo_name": "Example Foundation", "website": "https://example.org", "rating": 4}])[0]
    ngo_dir = rt._ngo_dir(rd, ngo)
    ngo_dir.mkdir(parents=True, exist_ok=True)
    dossier = {
        "ngo_id": ngo["ngo_id"],
        "ngo_name": ngo["ngo_name"],
        "website": ngo["website"],
        "pm_context": {"reviewer": "", "rating": 4, "comment": "", "one_line_understanding": ""},
        "category_input": {"primary": "uncategorised"},
        "crawl": {"pages_collected": 1, "firecrawl_credits_used": 1, "notes": []},
        "website_pages": [{"title": "About", "url": "https://example.org", "page_type": "about", "markdown": "About the work."}],
        "website_candidate_highlights": [],
        "external_research": {"queries_used": 2, "source_count": 0, "sources": []},
        "external_candidate_highlights": [],
        "preliminary_ai": {"enabled": False, "status": "not_run"},
        "model_ready_summary": {"top_candidate_findings": [], "preliminary_category": "uncategorised"},
    }
    rt._write_json(ngo_dir / "evidence.json", dossier)
    rt._write_json(rd / "master_summary.json", [{
        "ngo_id": ngo["ngo_id"], "ngo_name": ngo["ngo_name"], "website": ngo["website"],
        "pm_rating": 4, "primary_category": "uncategorised", "primary_category_label": CATEGORY_LABELS["uncategorised"],
        "status": "complete",
    }])
    rt._write_json(rd / "status.json", {"created_at": "x", "updated_at": "x"})
    rt._rebuild_aggregate_outputs(rd, {"ngos": [ngo], "options": {}})
    assert (rd / "master_summary.csv").exists()
    assert (rd / "all_dossiers.jsonl").exists()
    assert (rd / "gpt_fable_packet.md").exists()
    assert (rd / "deep_enrichment_export.zip").exists()
    assert "Example Foundation" in (rd / "gpt_fable_packet.md").read_text()
