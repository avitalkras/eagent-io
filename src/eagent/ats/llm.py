"""
LLM orchestration for the ATS engine — requirement #2.

DESIGN PATTERN: same Strategy shape as BaseScraper (Phase 2) and
EnrichmentProvider (Phase 2): `LLMProvider` is an interface, `GroqProvider`
and `GeminiProvider` are interchangeable implementations, and the
orchestration function `analyze_and_tailor()` doesn't know or care which one
it's holding. Swapping providers — or substituting a `FakeLLMProvider` in
tests — never touches the validation/retry logic below.

WHY "STRICT JSON MODE" ISN'T ENOUGH BY ITSELF:
    Both Groq and Gemini offer a JSON-output mode, but that only guarantees
    *syntactically valid JSON* — it says nothing about whether the JSON has
    the fields we need, whether ats_score is in range, or whether the model
    invented a bullet_id that doesn't exist. So `generate_json()` returns a
    raw `dict`, and it's `analyze_and_tailor()`'s job to validate it against
    `ATSAnalysisResult` and the hallucination guard, retrying with the
    validation error fed back into the prompt if it fails. This "generate,
    validate, retry-with-feedback" loop is the standard pattern for getting
    reliable structured output from an LLM.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

import requests
from pydantic import ValidationError

from eagent.ats.models import ATSAnalysisResult, HallucinationError, MasterResume, validate_no_hallucination

logger = logging.getLogger(__name__)

MAX_TEMPERATURE = 0.2  # requirement #2: temperature <= 0.2 for reproducible scoring

HttpPost = Callable[..., "requests.Response"]


def _assert_valid_temperature(temperature: float) -> None:
    """Shared guard so both providers independently enforce requirement #2,
    without duplicating the check's logic (only its call site)."""
    if not (0.0 <= temperature <= MAX_TEMPERATURE):
        raise ValueError(
            f"temperature must be in [0.0, {MAX_TEMPERATURE}] for deterministic ATS "
            f"scoring, got {temperature}"
        )


# =============================================================================
# Provider interface (Strategy pattern)
# =============================================================================


class LLMProvider(ABC):
    """Contract: send a prompt, get back a parsed (but not yet validated) dict."""

    name: str

    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Dict[str, Any]:
        """Call the LLM in strict-JSON mode and return the parsed JSON object.

        Implementations are responsible for enabling the provider's JSON
        mode and enforcing `temperature <= MAX_TEMPERATURE`. They are NOT
        responsible for validating the JSON's *shape* — that's the caller's
        job (see analyze_and_tailor), so this method stays reusable for any
        JSON-shaped prompt, not just ATS analysis.
        """
        raise NotImplementedError


class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat completions API.

    Groq's JSON mode (`response_format={"type": "json_object"}`) guarantees
    syntactically valid JSON but does NOT accept a schema to constrain it —
    the shape has to be described in the prompt text itself. That's why
    `analyze_and_tailor()` builds an explicit, example-driven system prompt
    and validates the response with Pydantic afterward.
    """

    name = "groq"
    _API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        http_post: Optional[HttpPost] = None,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._model = model
        self._post: HttpPost = http_post or requests.post  # injectable for tests
        self._timeout = timeout

    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Dict[str, Any]:
        _assert_valid_temperature(temperature)

        response = self._post(
            self._API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "temperature": temperature,
                "response_format": {"type": "json_object"},  # strict JSON mode
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)


class GeminiProvider(LLMProvider):
    """Google Gemini's generateContent REST API with schema-constrained JSON output.

    Unlike Groq, Gemini can be given `response_schema` (an OpenAPI-3.0
    *subset* — no `$defs`/`$ref`, no `oneOf`) and will constrain decoding to
    match it directly, which is a stronger guarantee than "valid JSON" alone.
    That schema is intentionally hand-maintained as GEMINI_RESPONSE_SCHEMA
    below rather than derived from `ATSAnalysisResult.model_json_schema()`:
    Pydantic's exported schema uses `$defs`/`$ref` for nested models
    (TailoredResume, TailoredBullet), which Gemini's schema dialect doesn't
    support. Two schemas, one model — a deliberate trade-off, not drift.
    """

    name = "gemini"
    _API_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        http_post: Optional[HttpPost] = None,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._model = model
        self._post: HttpPost = http_post or requests.post
        self._timeout = timeout

    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Dict[str, Any]:
        _assert_valid_temperature(temperature)

        response = self._post(
            self._API_URL_TMPL.format(model=self._model),
            params={"key": self._api_key},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "response_mime_type": "application/json",  # strict JSON mode
                    "response_schema": GEMINI_RESPONSE_SCHEMA,  # schema-constrained decoding
                },
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


# Hand-maintained OpenAPI-subset schema for Gemini's response_schema — see the
# GeminiProvider docstring above for why this can't just be
# ATSAnalysisResult.model_json_schema().
GEMINI_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "ats_score": {"type": "NUMBER"},
        "matched_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "missing_hard_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tailored_resume": {
            "type": "OBJECT",
            "properties": {
                "summary": {"type": "STRING"},
                "prioritized_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                "bullet_points": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "bullet_id": {"type": "STRING"},
                            "tailored_text": {"type": "STRING"},
                        },
                        "required": ["bullet_id", "tailored_text"],
                    },
                },
            },
            "required": ["summary", "prioritized_skills", "bullet_points"],
        },
    },
    "required": ["ats_score", "matched_keywords", "missing_hard_skills", "tailored_resume"],
}


# =============================================================================
# Prompt construction
# =============================================================================


def _build_system_prompt(master_resume: MasterResume) -> str:
    """Describes the task, the output contract, and — critically — the exact
    whitelist of bullet_ids and skills the model is allowed to reference.

    Handing the model the whitelist explicitly (rather than just saying
    "don't hallucinate") turns the anti-hallucination rule into something the
    model can mechanically follow, and gives validate_no_hallucination() a
    fair shot at passing on the first try instead of relying on a retry.
    """
    bullets_listing = "\n".join(
        f"  - id={b.bullet_id!r}: {b.text}"
        for exp in master_resume.experience
        for b in exp.bullets
    )
    skills_listing = ", ".join(master_resume.skills)

    return f"""You are an ATS (Applicant Tracking System) resume-matching engine.
You will be given a job description. Score how well the candidate's real
resume matches it, and produce a tailored version of the resume for this job.

STRICT RULES (do not break these):
1. Output ONLY a single JSON object matching the schema described below. No
   markdown, no commentary, no code fences.
2. `tailored_resume.bullet_points` MUST contain EVERY bullet_id listed below,
   EXACTLY ONCE each — reordered so the most relevant to this job come
   first. Never invent a new id, never omit one, never repeat one.
3. `tailored_resume.bullet_points[].tailored_text` must be a lightly reworded
   version of that bullet's original text — you may rephrase for concision or
   keyword alignment, but never add a metric, employer, tool, or outcome that
   isn't already present in the original text.
4. `tailored_resume.prioritized_skills` MUST be a subset of the candidate's
   real skills listed below — choose and order the most relevant ones for
   this job, never add a skill the candidate doesn't have.
5. `missing_hard_skills` are skills the JOB asks for that are NOT in the
   candidate's real skill list — this is the one place new terms (from the
   job description) are expected to appear.

CANDIDATE'S REAL BULLET POINTS (the only ones you may reference):
{bullets_listing}

CANDIDATE'S REAL SKILLS (the only ones you may prioritize):
{skills_listing}

OUTPUT JSON SCHEMA:
{{
  "ats_score": <number 0-100>,
  "matched_keywords": [<string>, ...],
  "missing_hard_skills": [<string>, ...],
  "tailored_resume": {{
    "summary": <string>,
    "prioritized_skills": [<string>, ...],
    "bullet_points": [{{"bullet_id": <string>, "tailored_text": <string>}}, ...]
  }}
}}"""


def _build_user_prompt(job_description: str) -> str:
    return f"JOB DESCRIPTION:\n{job_description.strip()}"


# =============================================================================
# Orchestration: generate -> validate -> retry-with-feedback
# =============================================================================


def analyze_and_tailor(
    job_description: str,
    master_resume: MasterResume,
    provider: LLMProvider,
    temperature: float = 0.1,
    max_attempts: int = 2,
) -> ATSAnalysisResult:
    """Score `job_description` against `master_resume` and produce a tailored resume.

    Validates the LLM's raw JSON against ATSAnalysisResult (requirement #1)
    and the hallucination guard (requirement #1's "without hallucination").
    On failure, retries up to `max_attempts` times with the specific error
    fed back into the prompt — a "self-healing" loop that's far more
    reliable than hoping the first response is perfect.

    Raises:
        ValueError: if `temperature` exceeds MAX_TEMPERATURE.
        RuntimeError: if no valid result was produced within max_attempts.
    """
    _assert_valid_temperature(temperature)

    system_prompt = _build_system_prompt(master_resume)
    base_user_prompt = _build_user_prompt(job_description)

    last_error: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        user_prompt = base_user_prompt
        if last_error is not None:
            user_prompt += (
                f"\n\nYour previous response was invalid: {last_error}\n"
                "Return a corrected JSON object only, following the rules exactly."
            )

        raw = provider.generate_json(system_prompt, user_prompt, temperature=temperature)

        try:
            result = ATSAnalysisResult.model_validate(raw)
            validate_no_hallucination(result, master_resume)
            return result
        except (ValidationError, HallucinationError) as exc:
            last_error = str(exc)
            logger.warning(
                "analyze_and_tailor: attempt %d/%d failed validation via %s: %s",
                attempt, max_attempts, provider.name, last_error,
            )

    raise RuntimeError(
        f"LLM provider '{provider.name}' failed to produce a valid ATS analysis "
        f"after {max_attempts} attempt(s). Last error: {last_error}"
    )
