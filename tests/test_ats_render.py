"""
Tests for eagent.ats.render.

`render_typst_source()` is a pure string-in/string-out function and is
fully testable without the `typst` binary. `compile_pdf()` is the only part
that shells out to it, so its test is skipped when `typst` isn't installed
on PATH — same "isolate the untestable boundary" pattern as the DB-gated
loader integration test (tests/test_loader_integration.py, Phase 2).
"""
from __future__ import annotations

import shutil

import pytest

from eagent.ats.models import TailoredBullet, TailoredResume
from eagent.ats.render import compile_pdf, render_typst_source


def _tailored_resume_for(master_resume, summary="Tailored summary.") -> TailoredResume:
    """A valid full re-ranking (every bullet present once) for render tests."""
    all_bullets = [
        TailoredBullet(bullet_id=b.bullet_id, tailored_text=f"{b.text} [tailored]")
        for exp in master_resume.experience
        for b in exp.bullets
    ]
    return TailoredResume(
        summary=summary,
        prioritized_skills=["python", "airflow"],
        bullet_points=list(reversed(all_bullets)),
    )


def test_render_includes_header_and_summary(master_resume):
    src = render_typst_source(master_resume, _tailored_resume_for(master_resume))
    assert "Jordan Rivera" in src
    assert "Tailored summary." in src
    assert "python, airflow" in src


def test_render_includes_every_experience_in_original_order(master_resume):
    src = render_typst_source(master_resume, _tailored_resume_for(master_resume))
    acme_pos = src.index("Acme Analytics")
    bright_pos = src.index("Bright Retail Co")
    assert acme_pos < bright_pos  # master resume's own (reverse-chronological) order preserved


def test_render_includes_tailored_bullet_text_not_original():
    from eagent.ats.models import MasterResume

    master = MasterResume(
        full_name="Test Candidate",
        email="test@example.com",
        summary="s",
        skills=["python"],
        experience=[{
            "company": "Acme",
            "title": "Engineer",
            "start_date": "2020-01",
            "end_date": None,
            "bullets": [{"bullet_id": "a1", "text": "Original bullet text."}],
        }],
    )
    tailored = TailoredResume(
        summary="s",
        prioritized_skills=["python"],
        bullet_points=[TailoredBullet(bullet_id="a1", tailored_text="Reworded bullet text.")],
    )

    src = render_typst_source(master, tailored)

    assert "Reworded bullet text." in src
    assert "Original bullet text." not in src


def test_render_escapes_typst_special_characters():
    from eagent.ats.models import MasterResume

    master = MasterResume(
        full_name="A. Test",
        email="test@example.com",
        summary="s",
        skills=["c#"],
        experience=[{
            "company": "Acme",
            "title": "Engineer",
            "start_date": "2020-01",
            "end_date": None,
            "bullets": [{"bullet_id": "a1", "text": "orig"}],
        }],
    )
    tailored = TailoredResume(
        summary="Grew revenue *2x* using #hashtags and $budgets [brackets]",
        prioritized_skills=["c#"],
        bullet_points=[TailoredBullet(bullet_id="a1", tailored_text="orig")],
    )

    src = render_typst_source(master, tailored)

    # raw markup-significant characters must never appear unescaped in the
    # interpolated summary line
    summary_line = [l for l in src.splitlines() if "revenue" in l][0]
    assert "\\*2x\\*" in summary_line
    assert "\\#hashtags" in summary_line
    assert "\\$budgets" in summary_line
    assert "\\[brackets\\]" in summary_line


def test_render_omits_skills_section_when_none_prioritized(master_resume):
    tailored = _tailored_resume_for(master_resume)
    tailored = tailored.model_copy(update={"prioritized_skills": []})
    src = render_typst_source(master_resume, tailored)
    assert "== Skills" not in src


# --- compile_pdf: skipped unless the `typst` CLI is installed ---------------


requires_typst = pytest.mark.skipif(shutil.which("typst") is None, reason="typst CLI not installed")


@requires_typst
def test_compile_pdf_produces_a_real_pdf(master_resume, tmp_path):
    src = render_typst_source(master_resume, _tailored_resume_for(master_resume))
    out_path = tmp_path / "resume.pdf"

    result_path = compile_pdf(src, out_path)

    assert result_path == out_path
    assert out_path.exists()
    assert out_path.read_bytes().startswith(b"%PDF")


def test_compile_pdf_raises_clear_error_when_typst_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="typst"):
        compile_pdf("#set page()", tmp_path / "out.pdf")
