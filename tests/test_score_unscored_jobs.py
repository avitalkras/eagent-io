"""
Unit tests for scripts/score_unscored_jobs.py.

WHY THIS SCRIPT GETS DIRECT TESTS, UNLIKE run_ingestion.py/run_ats_pipeline.py:
    Those scripts are thin wiring over already-tested library functions
    (eagent.loader, eagent.ats.llm, ...) with no logic of their own worth
    testing separately. This script is different: build_provider()'s
    fail-fast provider/key validation and run_batch()'s per-row
    error-isolation loop are new, non-trivial logic introduced here —
    exactly the "proper error isolation" this script exists to provide —
    so they get their own coverage instead of inheriting it by proxy.

scripts/ is deliberately not an installed package (these are CLI entry
points, not library code), so the module is loaded here via importlib
rather than a normal import.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "score_unscored_jobs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("score_unscored_jobs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def suj(monkeypatch):
    # Clear provider env vars so tests are deterministic regardless of what
    # happens to be set on the machine actually running them.
    for var in ("LLM_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return _load_module()


# --- build_provider(): fails fast, before any DB/network work --------------


def test_build_provider_defaults_to_groq(suj, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    provider = suj.build_provider()
    assert provider.name == "groq"


def test_build_provider_missing_groq_key_raises_clearly(suj):
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        suj.build_provider()


def test_build_provider_selects_gemini_via_env(suj, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = suj.build_provider()
    assert provider.name == "gemini"


def test_build_provider_missing_gemini_key_raises_clearly(suj, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        suj.build_provider()


def test_build_provider_rejects_unknown_provider_name(suj, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        suj.build_provider()


# --- score_one_job(): wires the steps together, always with recruiter_id=None -----


def test_score_one_job_returns_the_score_and_upserts_with_no_recruiter(suj, monkeypatch):
    class FakeResult:
        ats_score = 91.5
        tailored_resume = object()

    monkeypatch.setattr(suj, "analyze_and_tailor", lambda *a, **k: FakeResult())
    monkeypatch.setattr(suj, "render_typst_source", lambda *a, **k: "#fake typst source")
    monkeypatch.setattr(suj, "compile_pdf", lambda *a, **k: None)

    upserted: Dict[str, Any] = {}
    monkeypatch.setattr(suj, "upsert_outreach_result", lambda engine, **kwargs: upserted.update(kwargs))

    score = suj.score_one_job(
        engine=object(), provider=object(), master_resume=object(),
        job={"job_id": 7, "title": "t", "raw_description": "a real JD"},
        output_dir=Path("/tmp"),
    )

    assert score == 91.5
    assert upserted["job_id"] == 7
    assert upserted["recruiter_id"] is None  # the documented, known gap


# --- run_batch(): the error-isolation loop itself ---------------------------


def _jobs(*descriptions: str):
    return [{"job_id": i, "title": f"job {i}", "raw_description": d} for i, d in enumerate(descriptions)]


def test_run_batch_scores_every_good_job(suj, monkeypatch):
    monkeypatch.setattr(suj, "score_one_job", lambda *a, **k: 80.0)

    summary = suj.run_batch(object(), object(), object(), _jobs("jd one", "jd two"), Path("/tmp"))

    assert summary == {"scored": 2, "skipped": 0, "failed": 0}


def test_run_batch_skips_jobs_with_no_description_without_calling_score_one_job(suj, monkeypatch):
    calls = []
    monkeypatch.setattr(suj, "score_one_job", lambda *a, **k: calls.append(1) or 80.0)

    summary = suj.run_batch(object(), object(), object(), _jobs("", "   ", "a real jd"), Path("/tmp"))

    assert summary == {"scored": 1, "skipped": 2, "failed": 0}
    assert len(calls) == 1  # score_one_job never called for the blank-description rows


def test_run_batch_isolates_a_failure_and_keeps_processing_the_rest(suj, monkeypatch):
    """The central guarantee this script exists to provide: one bad job
    (LLM outage, malformed JD, whatever) must not stop the batch."""
    call_log = []

    def fake_score_one_job(engine, provider, master_resume, job, output_dir):
        call_log.append(job["job_id"])
        if job["job_id"] == 1:
            raise RuntimeError("simulated LLM outage")
        return 80.0

    monkeypatch.setattr(suj, "score_one_job", fake_score_one_job)

    summary = suj.run_batch(object(), object(), object(), _jobs("jd 0", "jd 1 (fails)", "jd 2"), Path("/tmp"))

    assert summary == {"scored": 2, "skipped": 0, "failed": 1}
    assert call_log == [0, 1, 2]  # job 2 still ran despite job 1's failure


def test_run_batch_with_all_failures_still_reports_every_job(suj, monkeypatch):
    def always_fails(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(suj, "score_one_job", always_fails)

    summary = suj.run_batch(object(), object(), object(), _jobs("jd 0", "jd 1", "jd 2"), Path("/tmp"))

    assert summary == {"scored": 0, "skipped": 0, "failed": 3}
