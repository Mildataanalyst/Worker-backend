import importlib.util
import sys
import types
from pathlib import Path


def _load_engine(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper")
    monkeypatch.setenv("AVIKA_RULE_PREFILTER", "true")
    monkeypatch.setenv("AVIKA_CLASSIFICATION_ONLY", "true")
    monkeypatch.setenv("AVIKA_SITE_TEXT_CHARS", "1500")
    monkeypatch.setenv("AVIKA_MAX_TOKENS", "120")

    fake = types.ModuleType("anthropic")

    class FakeAnthropic:
        def __init__(self, *args, **kwargs):
            pass

    fake.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    path = Path(__file__).resolve().parents[1] / "engine" / "dfp2_engine_safe_v5_live_status.py"
    spec = importlib.util.spec_from_file_location("dfp2_engine_compact_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prefilter_rejects_obvious_nonfits(monkeypatch):
    engine = _load_engine(monkeypatch)
    cases = [
        (
            {"name": "ABC Degree College"},
            "https://abc.edu",
            "Undergraduate and postgraduate degree programmes, semesters and campus placements.",
            "higher_education",
        ),
        (
            {"name": "Sunrise International School"},
            "https://sunrise.edu",
            "Admissions open. Fee structure. CBSE and Cambridge curriculum. Smart classrooms and world class campus.",
            "fee_charging_school_likely",
        ),
        (
            {"name": "Old Age Welfare Trust"},
            "https://oldage.org",
            "We operate an old age home with elderly and geriatric care.",
            "elderly_care",
        ),
        (
            {"name": "Example NGO"},
            "https://ngosindia.org/example-ngo",
            "Directory listing for an NGO.",
            "wrong_source_or_blog",
        ),
    ]
    for ngo, url, text, expected in cases:
        result = engine.deterministic_avika_exclusion(ngo, url, text)
        assert result and result["reason_code"] == expected


def test_prefilter_never_auto_approves_and_keeps_plausible_child_ngo(monkeypatch):
    engine = _load_engine(monkeypatch)
    result = engine.deterministic_avika_exclusion(
        {"name": "Hope Children Trust"},
        "https://hopechildren.org",
        "We provide free education and daily nutrition to underprivileged children in rural Karnataka.",
    )
    assert result is None


def test_religious_body_with_real_child_program_is_not_rule_rejected(monkeypatch):
    engine = _load_engine(monkeypatch)
    result = engine.deterministic_avika_exclusion(
        {"name": "Sri Seva Mutt"},
        "https://srisevamutt.org",
        "The trust runs a free residential school and children's home for underprivileged rural children.",
    )
    assert result is None


def test_compact_prompt_and_response(monkeypatch):
    engine = _load_engine(monkeypatch)
    item = {
        "id": "ngo-1",
        "name": "Hope Trust",
        "district": "Bengaluru",
        "state": "Karnataka",
        "website": "https://hope.org",
        "site_text": ("Navigation and donation information. " * 300)
        + "We provide free education and nutrition to underprivileged children.",
    }
    prompt = engine._ai_prompt_for_item(item)
    assert len(prompt) < 3000
    assert engine._ai_max_tokens() == 120
    assert "20-35 word plain-english description" in prompt.lower()
    assert len(prompt) < 3000
    profile = engine.normalize_avika_profile(
        {"m": "yes", "d": "maybe", "c": "medium", "r": "fees_unclear", "x": "Provides free learning and nutrition support to underserved children, though the website does not clearly explain programme scale or access criteria."}
    )
    assert profile["official_website_match"] == "yes"
    assert profile["decision"] == "maybe"
    assert profile["reason_code"] == "fees_unclear"
    assert profile["summary"].startswith("Provides free learning")
    assert profile["partners_found"] == []
