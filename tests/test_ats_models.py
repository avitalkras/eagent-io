"""
Tests for eagent.ats.models — the Pydantic contract (requirement #1) and the
anti-hallucination guard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eagent.ats.models import (
    ATSAnalysisResult,
    HallucinationError,
    TailoredResume,
    validate_no_hallucination,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "eagent" / "ats" / "ats_analysis_result.schema.json"
)


def _valid_payload() -> dict:
    return {
        "ats_score": 82.5,
        "matched_keywords": ["Python", "python", "SQL"],  # dup-after-lowercasing on purpose
        "missing_hard_skills": ["Snowflake"],
        "tailored_resume": {
            "summary": "Data engineer focused on streaming pipelines.",
            "prioritized_skills": ["python", "kafka"],
            "bullet_points": [{"bullet_id": "acme-2022-01", "tailored_text": "Did a thing."}],
        },
    }


# --- ATSAnalysisResult validation -------------------------------------------


def test_valid_payload_parses():
    result = ATSAnalysisResult.model_validate(_valid_payload())
    assert result.ats_score == 82.5
    assert result.tailored_resume.bullet_points[0].bullet_id == "acme-2022-01"


def test_keyword_lists_are_deduped_and_lowercased():
    result = ATSAnalysisResult.model_validate(_valid_payload())
    assert result.matched_keywords == ["python", "sql"]


@pytest.mark.parametrize("bad_score", [-1, 100.1, 500])
def test_ats_score_out_of_range_is_rejected(bad_score):
    payload = _valid_payload()
    payload["ats_score"] = bad_score
    with pytest.raises(ValidationError):
        ATSAnalysisResult.model_validate(payload)


def test_missing_required_field_is_rejected():
    payload = _valid_payload()
    del payload["tailored_resume"]
    with pytest.raises(ValidationError):
        ATSAnalysisResult.model_validate(payload)


def test_empty_summary_is_rejected():
    payload = _valid_payload()
    payload["tailored_resume"]["summary"] = ""
    with pytest.raises(ValidationError):
        ATSAnalysisResult.model_validate(payload)


# --- exported JSON Schema stays in sync with the model ----------------------


def test_exported_schema_matches_model():
    exported = json.loads(SCHEMA_PATH.read_text())
    current = ATSAnalysisResult.model_json_schema()
    assert exported == current, (
        "src/eagent/ats/ats_analysis_result.schema.json is out of date — "
        "run `python scripts/export_ats_schema.py` after changing ATSAnalysisResult."
    )


# --- anti-hallucination guard ------------------------------------------------


def _all_bullet_points(master_resume, tailored_text_suffix="") -> list:
    return [
        {"bullet_id": b.bullet_id, "tailored_text": b.text + tailored_text_suffix}
        for exp in master_resume.experience
        for b in exp.bullets
    ]


def test_full_valid_reranking_passes(master_resume):
    result = ATSAnalysisResult(
        ats_score=90,
        matched_keywords=["python"],
        missing_hard_skills=[],
        tailored_resume=TailoredResume(
            summary="Tailored summary.",
            prioritized_skills=["python", "airflow"],
            bullet_points=list(reversed(_all_bullet_points(master_resume))),  # reordered, that's fine
        ),
    )
    validate_no_hallucination(result, master_resume)  # must not raise


def test_unknown_bullet_id_is_rejected(master_resume):
    bullets = _all_bullet_points(master_resume)
    bullets[0]["bullet_id"] = "totally-invented-id"
    result = ATSAnalysisResult(
        ats_score=90, matched_keywords=[], missing_hard_skills=[],
        tailored_resume=TailoredResume(summary="s", prioritized_skills=[], bullet_points=bullets),
    )
    with pytest.raises(HallucinationError, match="unknown bullet_id"):
        validate_no_hallucination(result, master_resume)


def test_missing_bullet_id_is_rejected(master_resume):
    bullets = _all_bullet_points(master_resume)[:-1]  # drop the last real bullet
    result = ATSAnalysisResult(
        ats_score=90, matched_keywords=[], missing_hard_skills=[],
        tailored_resume=TailoredResume(summary="s", prioritized_skills=[], bullet_points=bullets),
    )
    with pytest.raises(HallucinationError, match="missing bullet_id"):
        validate_no_hallucination(result, master_resume)


def test_duplicate_bullet_id_is_rejected(master_resume):
    bullets = _all_bullet_points(master_resume)
    bullets.append(bullets[0])  # repeat one
    result = ATSAnalysisResult(
        ats_score=90, matched_keywords=[], missing_hard_skills=[],
        tailored_resume=TailoredResume(summary="s", prioritized_skills=[], bullet_points=bullets),
    )
    with pytest.raises(HallucinationError, match="duplicate bullet_id"):
        validate_no_hallucination(result, master_resume)


def test_invented_skill_is_rejected(master_resume):
    result = ATSAnalysisResult(
        ats_score=90, matched_keywords=[], missing_hard_skills=[],
        tailored_resume=TailoredResume(
            summary="s",
            prioritized_skills=["python", "quantum-computing"],  # not a real skill
            bullet_points=_all_bullet_points(master_resume),
        ),
    )
    with pytest.raises(HallucinationError, match="quantum-computing"):
        validate_no_hallucination(result, master_resume)
