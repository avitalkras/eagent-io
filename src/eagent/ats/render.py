"""
Renders a TailoredResume into a minimalist, ATS-compliant single-column PDF
using Typst — requirement #3.

WHY TYPST INSTEAD OF LATEX?
    Both compile to a real PDF, but Typst's CLI is a single static binary
    (`typst compile in.typ out.pdf`, no TeX distribution to install) and its
    markup is simpler to generate programmatically from Python strings. For
    a portfolio project that needs to run in plain GitHub Actions/CI without
    a multi-GB LaTeX install, that's a meaningful trade-off in Typst's favor.

WHY "ATS-COMPLIANT SINGLE-COLUMN"?
    Applicant Tracking Systems parse resumes by extracting text in reading
    order. Multi-column layouts, tables, text boxes, and icons often get
    scrambled or dropped by that extraction — a two-column resume can come
    out of an ATS parser as word salad. So the template deliberately avoids
    all of that: one column, plain headings, plain bullet lists, a
    common system font.

THE TWO-FUNCTION SPLIT (render_typst_source / compile_pdf):
    `render_typst_source()` is a pure function — data in, markup string out.
    It's fully unit-testable with plain string assertions and needs no
    external binary. `compile_pdf()` is the only part that shells out to the
    `typst` CLI, so it's the only part a test needs to skip when `typst`
    isn't installed. Same "isolate the untestable boundary" principle as the
    HTTP injection in scrapers/enrichment (Phase 2).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from eagent.ats.models import MasterResume, MasterResumeExperience, TailoredBullet, TailoredResume

# Characters with special meaning in Typst markup. Escaping them prevents
# resume/job-description text (which we don't control) from being
# misinterpreted as Typst syntax — e.g. a bullet mentioning "C#" must not
# open a Typst code block, and "*3x growth*" must not turn into bold text.
_TYPST_SPECIAL_CHARS = set("\\*_#$@<>[]`")


def _escape_typst(text: str) -> str:
    """Escape Typst markup special characters and flatten embedded newlines."""
    out: List[str] = []
    for ch in text:
        if ch == "\n":
            out.append(" ")
        elif ch in _TYPST_SPECIAL_CHARS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _group_bullets_by_experience(
    master_resume: MasterResume, tailored: TailoredResume
) -> List[Tuple[MasterResumeExperience, List[TailoredBullet]]]:
    """Re-associate the LLM's flat, re-ranked bullet list back to each job.

    Assumes `tailored` already passed `validate_no_hallucination()` (called
    by `analyze_and_tailor()` before this is ever invoked), so every
    bullet_id is guaranteed to belong to exactly one experience. Experience
    order follows the Master Resume (real chronology is never reordered —
    only the bullets *within* a job are re-ranked); bullet order within each
    group follows the LLM's re-ranking.
    """
    bullet_id_to_experience: Dict[str, MasterResumeExperience] = {
        bullet.bullet_id: exp for exp in master_resume.experience for bullet in exp.bullets
    }

    groups: Dict[int, List[TailoredBullet]] = {i: [] for i in range(len(master_resume.experience))}
    exp_index_by_id = {id(exp): i for i, exp in enumerate(master_resume.experience)}

    for tb in tailored.bullet_points:
        exp = bullet_id_to_experience.get(tb.bullet_id)
        if exp is None:
            continue  # defensive only; validate_no_hallucination should already guarantee this can't happen
        groups[exp_index_by_id[id(exp)]].append(tb)

    return [(exp, groups[i]) for i, exp in enumerate(master_resume.experience) if groups[i]]


def render_typst_source(master_resume: MasterResume, tailored: TailoredResume) -> str:
    """Build the full Typst document source for a tailored resume.

    Pure function: same inputs always produce the same markup string, no I/O.
    """
    lines: List[str] = [
        '#set page(margin: (x: 0.75in, y: 0.6in))',
        '#set text(font: ("Arial", "Helvetica", "Liberation Sans"), size: 10.5pt)',
        '#set par(justify: false, leading: 0.55em)',
        '',
    ]

    contact_parts = [_escape_typst(str(master_resume.email))]
    if master_resume.phone:
        contact_parts.append(_escape_typst(master_resume.phone))
    if master_resume.location:
        contact_parts.append(_escape_typst(master_resume.location))
    if master_resume.linkedin_url:
        contact_parts.append(_escape_typst(str(master_resume.linkedin_url)))

    lines += [
        '#align(center)[',
        f'  #text(size: 16pt, weight: "bold")[{_escape_typst(master_resume.full_name)}] \\',
        f'  {"  ".join(contact_parts)}',
        ']',
        '',
        '#line(length: 100%)',
        '',
        '== Summary',
        _escape_typst(tailored.summary),
        '',
    ]

    if tailored.prioritized_skills:
        lines += [
            '== Skills',
            _escape_typst(", ".join(tailored.prioritized_skills)),
            '',
        ]

    lines.append('== Experience')
    lines.append('')

    for exp, bullets in _group_bullets_by_experience(master_resume, tailored):
        date_range = f"{exp.start_date} -- {exp.end_date or 'Present'}"
        lines.append(
            f'*{_escape_typst(exp.title)}*, {_escape_typst(exp.company)} '
            f'#h(1fr) {_escape_typst(date_range)}'
        )
        for tb in bullets:
            lines.append(f'- {_escape_typst(tb.tailored_text)}')
        lines.append('')

    return "\n".join(lines)


def compile_pdf(typst_source: str, output_pdf_path: "str | Path") -> Path:
    """Compile Typst markup into a PDF at `output_pdf_path` via the `typst` CLI.

    Raises:
        RuntimeError: if the `typst` binary isn't on PATH, or compilation fails
            (the Typst compiler's own error message is included verbatim).
    """
    if shutil.which("typst") is None:
        raise RuntimeError(
            "The `typst` CLI is not installed or not on PATH. Install it with "
            "`brew install typst` (macOS) or see https://github.com/typst/typst "
            "for other platforms."
        )

    output_pdf_path = Path(output_pdf_path)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = Path(tmp_dir) / "resume.typ"
        source_path.write_text(typst_source, encoding="utf-8")

        # check=False (explicit): we check result.returncode ourselves below to
        # raise a RuntimeError carrying typst's own stderr, rather than letting
        # subprocess raise its own less-informative CalledProcessError.
        result = subprocess.run(
            ["typst", "compile", str(source_path), str(output_pdf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"typst compile failed:\n{result.stderr}")

    return output_pdf_path
