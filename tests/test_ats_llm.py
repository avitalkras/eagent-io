"""
Tests for eagent.ats.llm — requirement #2 (strict JSON, temperature <= 0.2)
and the generate -> validate -> retry-with-feedback orchestration loop.

Two layers of fakes, matching the two layers of the code (same technique as
tests/test_enrichment.py in Phase 2):
  1. `FakeLLMProvider` implements the LLMProvider interface directly — used
     to test analyze_and_tailor()'s retry/validation logic in isolation.
  2. A fake `http_post` function — used to test GroqProvider/GeminiProvider's
     own request-building and response-parsing in isolation, with no network.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from eagent.ats.llm import MAX_TEMPERATURE, GeminiProvider, GroqProvider, LLMProvider, analyze_and_tailor


class FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


class FakeLLMProvider(LLMProvider):
    """Returns a scripted sequence of raw dicts, one per call — lets tests
    simulate "bad response, then good response" without any HTTP layer."""

    name = "fake"

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Dict[str, Any]:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature})
        if not self._responses:
            raise AssertionError("FakeLLMProvider ran out of scripted responses")
        return self._responses.pop(0)


def _good_payload(master_resume) -> Dict[str, Any]:
    return {
        "ats_score": 77,
        "matched_keywords": ["python", "sql"],
        "missing_hard_skills": ["snowflake"],
        "tailored_resume": {
            "summary": "Tailored summary.",
            "prioritized_skills": ["python"],
            "bullet_points": [
                {"bullet_id": b.bullet_id, "tailored_text": b.text}
                for exp in master_resume.experience
                for b in exp.bullets
            ],
        },
    }


# --- analyze_and_tailor orchestration ---------------------------------------


def test_analyze_and_tailor_succeeds_on_first_valid_response(master_resume):
    provider = FakeLLMProvider([_good_payload(master_resume)])

    result = analyze_and_tailor("some job description", master_resume, provider)

    assert result.ats_score == 77
    assert len(provider.calls) == 1


def test_analyze_and_tailor_retries_with_feedback_on_invalid_response(master_resume):
    bad = _good_payload(master_resume)
    bad["ats_score"] = 500  # out of range -> ValidationError
    good = _good_payload(master_resume)
    provider = FakeLLMProvider([bad, good])

    result = analyze_and_tailor("jd", master_resume, provider, max_attempts=2)

    assert result.ats_score == 77
    assert len(provider.calls) == 2
    # the retry prompt must include the specific validation error, not just
    # a generic "try again" — that's what makes the retry self-correcting.
    assert "previous response was invalid" in provider.calls[1]["user_prompt"]


def test_analyze_and_tailor_retries_on_hallucination(master_resume):
    hallucinated = _good_payload(master_resume)
    hallucinated["tailored_resume"]["bullet_points"][0]["bullet_id"] = "made-up-id"
    good = _good_payload(master_resume)
    provider = FakeLLMProvider([hallucinated, good])

    result = analyze_and_tailor("jd", master_resume, provider, max_attempts=2)

    assert result.ats_score == 77
    assert len(provider.calls) == 2


def test_analyze_and_tailor_raises_after_exhausting_attempts(master_resume):
    always_bad = {**_good_payload(master_resume), "ats_score": -1}
    provider = FakeLLMProvider([always_bad, always_bad])

    with pytest.raises(RuntimeError, match="failed to produce a valid ATS analysis"):
        analyze_and_tailor("jd", master_resume, provider, max_attempts=2)


def test_analyze_and_tailor_rejects_temperature_above_max(master_resume):
    provider = FakeLLMProvider([_good_payload(master_resume)])
    with pytest.raises(ValueError):
        analyze_and_tailor("jd", master_resume, provider, temperature=MAX_TEMPERATURE + 0.01)


# --- GroqProvider ------------------------------------------------------------


def test_groq_provider_sends_strict_json_mode_and_parses_content():
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": json.dumps({"ats_score": 50})}}]})

    provider = GroqProvider(api_key="fake-key", http_post=fake_post)
    result = provider.generate_json("system", "user", temperature=0.1)

    assert result == {"ats_score": 50}
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["temperature"] == 0.1
    assert captured["headers"]["Authorization"] == "Bearer fake-key"


def test_groq_provider_rejects_temperature_above_max():
    provider = GroqProvider(api_key="fake-key", http_post=lambda *a, **k: FakeResponse({}))
    with pytest.raises(ValueError):
        provider.generate_json("s", "u", temperature=0.9)


# --- GeminiProvider -----------------------------------------------------------


def test_gemini_provider_sends_response_schema_and_parses_text():
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured.update(kwargs)
        text = json.dumps({"ats_score": 60})
        return FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    provider = GeminiProvider(api_key="fake-key", http_post=fake_post)
    result = provider.generate_json("system", "user", temperature=0.15)

    assert result == {"ats_score": 60}
    gen_config = captured["json"]["generationConfig"]
    assert gen_config["response_mime_type"] == "application/json"
    assert gen_config["temperature"] == 0.15
    assert "response_schema" in gen_config
    assert captured["params"]["key"] == "fake-key"


def test_gemini_provider_rejects_temperature_above_max():
    provider = GeminiProvider(api_key="fake-key", http_post=lambda *a, **k: FakeResponse({}))
    with pytest.raises(ValueError):
        provider.generate_json("s", "u", temperature=1.0)
