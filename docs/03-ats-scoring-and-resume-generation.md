# Phase 3 — ATS Scoring & Tailored Resume Generation

> **Goal of this doc:** teach you *why* the ATS engine is built this way —
> especially how it gets an LLM to produce reliable, non-fabricated output,
> which is the hardest and most interview-relevant part of this phase.

---

## 1. The pipeline this phase builds

```
Master Resume (JSON)  ─┐
                        ├──▶ analyze_and_tailor()  ──▶  ATSAnalysisResult
Job Description (text)─┘         (LLM, validated,          │
                                   hallucination-checked)    │
                                                              ▼
                                                    render_typst_source()
                                                              │
                                                              ▼
                                                       compile_pdf()
                                                              │
                                                              ▼
                                                  upsert_outreach_result()
                                                   (writes fact_outreach)
```

`scripts/run_ats_pipeline.py` wires all four stages together end-to-end.

---

## 2. The core problem: LLMs fabricate, and "just ask nicely" isn't a guardrail

If you prompt an LLM with "tailor this resume for this job" and paste in
plain resume text, two things go wrong in practice:

1. **The output isn't guaranteed to be valid JSON**, let alone the *right*
   JSON — missing fields, wrong types, an `ats_score` of `"very high"`.
2. **The model fabricates.** It might invent a metric ("increased revenue by
   40%") that was never in the source resume, because it sounds plausible
   and matches the job description. This is the single biggest risk in any
   resume-tailoring tool — a fabricated resume is worse than no resume.

Everything in this phase's design exists to make both failure modes
**mechanically impossible to ship silently**, not just "less likely."

---

## 3. Fix #1 for fabrication: a structured Master Resume with stable IDs

`MasterResume` (`src/eagent/ats/models.py`) gives every real bullet a
`bullet_id`:

```python
class MasterResumeBullet(BaseModel):
    bullet_id: str   # e.g. "acme-2022-01" — stable, human-assigned, never regenerated
    text: str
```

The LLM is never asked to *write* a new bullet from scratch. It's asked to
**select an id from a known list and lightly reword that bullet's text**.
This reframes "did the model hallucinate?" from a fuzzy language question
into a mechanical one: *is every `bullet_id` in the output a real id from the
input?* That's a set operation, not a judgment call.

---

## 4. Fix #2 for fabrication: `validate_no_hallucination()` — four checks, no LLM judge

The task said the tailored resume must have "re-ranked bullet points... **without
hallucination**." I read "re-ranked" literally: the LLM must return **every**
original bullet, reordered — not a hand-picked subset (which would silently
drop real experience) and not new ones (which would fabricate it). That
becomes four deterministic checks in `validate_no_hallucination()`:

| # | Check | Catches |
|---|---|---|
| 1 | No unknown `bullet_id` | Invented bullets, mangled ids |
| 2 | No **missing** `bullet_id` | Silently dropped real experience |
| 3 | No duplicate `bullet_id` | Padding the resume by repeating one bullet |
| 4 | Every `prioritized_skill` is a real skill | Claiming a skill the candidate doesn't have |

All four are plain Python set/multiset comparisons against `MasterResume` —
no second LLM call to "judge" the first one's output. That matters: an
LLM-as-judge is itself unreliable and expensive. A whitelist check is fast,
free, deterministic, and either passes or doesn't.

> **Interview framing:** "We treat hallucination detection as a data-integrity
> problem, not a language-understanding problem — the same instinct as a
> database `FOREIGN KEY` constraint. A bullet_id either exists in the source
> of truth or it doesn't."

---

## 5. Fix #1 for invalid JSON: Pydantic + provider-level strict JSON mode

Two independent layers, same "defense in depth" instinct as Phase 1/2:

- **Provider level** — `GroqProvider` sets `response_format={"type":
  "json_object"}`; `GeminiProvider` sets `response_mime_type:
  "application/json"` plus a `response_schema`. Both guarantee *syntactically*
  valid JSON at the API level.
- **Application level** — `ATSAnalysisResult.model_validate(raw)` checks the
  JSON actually has the right *shape*: `ats_score` bounded 0–100, required
  fields present, nested `TailoredResume` structurally valid. JSON-mode
  guarantees "parses as JSON"; it says nothing about "has an `ats_score`
  field that's a number between 0 and 100."

---

## 6. Fix #2 for invalid/hallucinated JSON: generate → validate → retry-with-feedback

`analyze_and_tailor()` (`src/eagent/ats/llm.py`) doesn't just validate once
and give up. It loops, feeding the *specific* validation error back into the
next prompt:

```python
for attempt in range(1, max_attempts + 1):
    if last_error is not None:
        user_prompt += f"\n\nYour previous response was invalid: {last_error}\n" \
                        "Return a corrected JSON object only, following the rules exactly."
    raw = provider.generate_json(system_prompt, user_prompt, temperature=temperature)
    try:
        result = ATSAnalysisResult.model_validate(raw)
        validate_no_hallucination(result, master_resume)
        return result
    except (ValidationError, HallucinationError) as exc:
        last_error = str(exc)
raise RuntimeError(...)
```

This is the standard **self-healing loop** pattern for structured LLM output:
telling the model *exactly* what was wrong ("missing bullet_id(s):
['bright-2019-02']") gets a corrected response far more reliably than hoping
attempt #1 is perfect, or than a generic "please fix your JSON" retry.

---

## 7. Why `temperature <= 0.2`, and why it's enforced in code, not just docs

ATS scoring needs to be **reproducible** — the same resume against the same
job description should score close to the same every time, or the number is
meaningless for tracking/comparison in the dashboard. Higher temperature
means more randomness in the model's token choices, which for a *scoring*
task is pure noise, not creativity you want.

Requirement #2 says temperature ≤ 0.2. Rather than just defaulting to a low
number and hoping nobody changes it, `_assert_valid_temperature()` is called
inside **both** `GroqProvider.generate_json()` and
`GeminiProvider.generate_json()` — the requirement is an executable
assertion, not a comment. Pass `temperature=0.5` and you get a `ValueError`
immediately, not a silently-noisier score three weeks later.

---

## 8. Two providers, one interface — and one deliberately duplicated schema

`LLMProvider` is the same Strategy-pattern shape as `BaseScraper` (Phase 2)
and `EnrichmentProvider` (Phase 2): `analyze_and_tailor()` doesn't know or
care whether it's holding a `GroqProvider` or a `GeminiProvider`.

But the two providers constrain output differently, which is a real,
worth-knowing trade-off:

- **Groq's JSON mode** guarantees valid JSON syntax only — no schema
  awareness. The shape has to be *described in the prompt text*
  (`_build_system_prompt()` includes a literal JSON skeleton).
- **Gemini's `response_schema`** actually constrains decoding to match a
  schema — a stronger guarantee. But it only accepts an **OpenAPI 3.0
  subset** (no `$defs`, no `$ref`, no `oneOf`), while
  `ATSAnalysisResult.model_json_schema()` (what Pydantic generates) uses
  `$defs`/`$ref` for the nested `TailoredResume`/`TailoredBullet` models.

  That's why `GEMINI_RESPONSE_SCHEMA` in `llm.py` is a **hand-maintained**
  dict, not derived from the Pydantic model. Two schemas describing the same
  shape isn't drift — it's the only way to satisfy two APIs with genuinely
  different schema dialects. (Contrast with the exported
  `ats_analysis_result.schema.json`, described next, which IS
  auto-generated — because nothing downstream of *that* file needs the
  OpenAPI subset.)

---

## 9. The JSON Schema artifact (requirement #1)

`src/eagent/ats/models.py`'s `ATSAnalysisResult` is the single source of
truth. `scripts/export_ats_schema.py` regenerates
`src/eagent/ats/ats_analysis_result.schema.json` from it via
`ATSAnalysisResult.model_json_schema()` — that's the literal JSON Schema
document the task asks for, checked into the repo as a generated artifact.

`tests/test_ats_models.py::test_exported_schema_matches_model` fails the
build if the model changes but nobody re-ran the export script — a drift
guard, same idea as never letting two sources of truth silently disagree.

---

## 10. Typst rendering: why single-column, and how the anti-hallucination guarantee survives rendering

**ATS-compliant single-column** (requirement #3): real Applicant Tracking
Systems parse resumes by extracting text in reading order. Multi-column
layouts, tables, and text boxes often get scrambled by that extraction — a
two-column resume can come out as word salad on the recruiter's side. The
Typst template in `render.py` deliberately uses only headings, paragraphs,
and bullet lists — nothing an ATS parser can misread.

**`render_typst_source()` is a pure function** — `(MasterResume,
TailoredResume) -> str`. No I/O, no subprocess, fully unit-testable with
plain string assertions (see `tests/test_ats_render.py`). All interpolated
text goes through `_escape_typst()` first, so a bullet mentioning `C#` or
`*3x growth*` can't be misread as Typst markup syntax.

**`compile_pdf()` is the only part that shells out** to the `typst` binary —
isolating the untestable boundary the same way `http_get`/`http_post`
injection isolates network calls in Phase 2's scrapers/providers. Its test is
skipped (not failed) when `typst` isn't on `PATH`.

**Bullets are re-associated to their original job** in
`_group_bullets_by_experience()`: the LLM returns a flat, re-ranked bullet
list, but the resume needs to show *which job* each bullet belongs to. Job
order always follows the Master Resume's real chronology — only the bullets
*within* a job are ever reordered, because reordering employers would
misrepresent the candidate's actual career timeline.

---

## 11. Requirement #4: updating `fact_outreach`, and a new migration file

`fact_outreach` (Phase 1) had no column for a PDF path, and no uniqueness
constraint to make writing to it idempotent. Rather than editing the
already-shipped `sql/01_schema.sql`, **Migration #3**
(`sql/03_ats_outreach_columns.sql`) adds both:

```sql
ALTER TABLE fact_outreach ADD COLUMN IF NOT EXISTS resume_pdf_path TEXT;
-- + a guarded ADD CONSTRAINT UNIQUE (job_id)
```

> **Why a new file instead of editing `01_schema.sql`?** Once a migration has
> actually run against a real database, editing it retroactively means
> anyone who already applied it silently diverges from anyone re-running the
> edited version. Every schema change after "day one" is a new, numbered
> migration applied in order — this is how real production systems evolve
> their schema, and it's why the file is named `03_...`, not a change to `01_...`.

**One outreach draft per job** (`UNIQUE(job_id)`) is a product decision, not
just a technical one: `upsert_outreach_result()` (`src/eagent/ats/loader.py`)
uses it exactly like Phase 2's `dim_jobs` idempotency key — re-scoring the
same job (JD changed, resume updated, retried after a crash) refreshes the
existing row instead of creating a duplicate outreach record.

**One subtlety worth naming:** the upsert's `ON CONFLICT DO UPDATE` clause
deliberately does **not** touch `outreach_status`, `is_approved`, or
`drafted_at`. If a human has already reviewed and approved an outreach,
re-running the ATS engine (say, because the job posting was edited) must
refresh the score and PDF without silently reverting their approval back to
`'Drafted'`. `tests/test_ats_loader_integration.py::test_upsert_does_not_revert_an_already_approved_status`
proves this directly. This is the same "the machine never overwrites a human
decision" boundary that Phase 4's human-in-the-loop approval gate will build on.

---

## 12. Interview checklist ✅

- [ ] Why is "the LLM returned valid JSON" not the same guarantee as "the
      LLM's output is safe to use"?
- [ ] How does giving every resume bullet a stable `bullet_id` turn
      hallucination detection into a mechanical check instead of a judgment call?
- [ ] What are the 4 checks in `validate_no_hallucination()`, and which
      failure mode does each one catch?
- [ ] What is the "generate → validate → retry-with-feedback" pattern, and
      why is it more reliable than a single validate-and-fail?
- [ ] Why is `temperature <= 0.2` enforced as code, not just documented?
- [ ] Why does `GeminiProvider` need a hand-written schema instead of
      `ATSAnalysisResult.model_json_schema()`, when Groq doesn't use a
      schema at all?
- [ ] Why is `render_typst_source()` split from `compile_pdf()` as two
      functions instead of one?
- [ ] Why does a new migration file exist instead of editing
      `sql/01_schema.sql`?
- [ ] Why doesn't `upsert_outreach_result()`'s `ON CONFLICT` clause touch
      `outreach_status`?

---

### Files in this phase
- `src/eagent/ats/models.py` — `MasterResume*` (input) and `ATSAnalysisResult`/`TailoredResume` (output) models, `validate_no_hallucination()`
- `src/eagent/ats/ats_analysis_result.schema.json` — generated JSON Schema artifact
- `src/eagent/ats/llm.py` — `LLMProvider`, `GroqProvider`, `GeminiProvider`, `analyze_and_tailor()`
- `src/eagent/ats/render.py` — `render_typst_source()`, `compile_pdf()`
- `src/eagent/ats/loader.py` — `upsert_outreach_result()`, `today_date_id()`
- `sql/03_ats_outreach_columns.sql` — Migration #3
- `scripts/run_ats_pipeline.py` — wires score → tailor → render → save end-to-end
- `scripts/export_ats_schema.py` — regenerates the JSON Schema artifact
- `data/master_resume.example.json` — sample Master Resume fixture (fabricated data)
- `tests/test_ats_*.py` — unit tests (including a real `typst compile` run) + a DB-gated integration test

**Next up → Phase 4:** the human-in-the-loop approval workflow — reviewing
and approving/rejecting drafted outreach before anything is sent.
