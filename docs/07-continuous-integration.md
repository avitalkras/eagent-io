# Phase 6 — Continuous Integration

> **Goal of this doc:** teach you *why* the CI pipeline is shaped the way it
> is — especially the one thing CI can do that this dev sandbox never could:
> run every integration test for real, every time.

> **A different kind of honesty note than Phases 5/5b/6-DAX:** Power BI
> Desktop genuinely cannot run in this environment — that was unverifiable
> by construction. GitHub Actions is different: I can't *execute* the
> workflow locally either (no Docker/`act` in this sandbox — verified by
> trying), but once pushed, it runs for real on GitHub's own infrastructure
> and I can watch the actual result. This doc was written, then the
> workflow was pushed and its real run was checked — see the PR for the
> actual run link and outcome, not just a description of what should happen.

---

## 1. Why CI is the first time this project's integration tests actually run

Every phase since Phase 2 has written a `@pytest.mark.integration` test
module — `test_loader_integration.py`, `test_ats_loader_integration.py`,
`test_workflow_integration.py`, `test_api_integration.py` — each proving
something a mock can't: real `ON CONFLICT` semantics, real `SELECT ... FOR
UPDATE` row locking, a real HTTP request landing in a real Postgres
transaction. Every one of those modules has been **skipping** in this dev
sandbox the entire time, because there's no local Postgres available here
(no package manager to install one — see `docs/02` and every integration
test's own docstring for "how to run it locally").

CI closes that gap for the first time in this project's history:

```yaml
services:
  postgres:
    image: postgres:16
    ...
env:
  DATABASE_URL: postgresql+psycopg2://eagent:eagent@localhost:5432/eagent_test
```

A GitHub Actions **service container** is a real Postgres instance running
alongside the job, exposed on `localhost:5432`. With `DATABASE_URL` set, the
integration tests' own skip guard (`if not DATABASE_URL: pytest.skip(...)`)
never fires — they run for real, on every push and every PR, checking
exactly the guarantees that were previously only checked by reading the
code carefully. **This is the single biggest thing this phase adds**: not
"tests now exist" (they always did), but "the tests that were always
sitting there, skipped, now actually run."

---

## 2. What actually gets set up before the tests run

```yaml
- name: Apply migrations
  run: |
    PGPASSWORD=eagent psql -h localhost -U eagent -d eagent_test -f sql/01_schema.sql
    PGPASSWORD=eagent psql -h localhost -U eagent -d eagent_test -f sql/02_populate_dim_dates.sql
    PGPASSWORD=eagent psql -h localhost -U eagent -d eagent_test -f sql/03_ats_outreach_columns.sql
    PGPASSWORD=eagent psql -h localhost -U eagent -d eagent_test -f sql/04_interviewed_at_column.sql
```

All 4 migrations, in order, against a **fresh** database every run. This is
itself a real test that this project's other honesty note (Phase 1: *"the
whole script is one transaction... IF NOT EXISTS guards make it safe to
re-run"*) holds up in practice — if any migration had a typo, a missing
`IF NOT EXISTS`, or an ordering dependency on a previous run's state, CI
would fail here before a single Python test even starts. **This is also
this project's closest thing to a SQL linter** (see §4 for why a dedicated
one wasn't added).

`typst` is installed from the exact pinned release
(`typst-x86_64-unknown-linux-musl.tar.xz`, same `v0.15.1` verified locally
in Phase 3) so `test_ats_render.py::test_compile_pdf_produces_a_real_pdf`
compiles an actual PDF in CI too, not just skips like it would on a machine
without `typst` on `PATH`.

---

## 3. No secrets, anywhere, in this whole workflow

Nothing in `.github/workflows/ci.yml` references `GROQ_API_KEY`,
`GEMINI_API_KEY`, `HUNTER_API_KEY`, or any other real credential. That's
not an oversight — it's the direct payoff of a design decision made back in
Phase 2 and followed consistently ever since: **every external call in this
codebase is dependency-injected** (`http_get`/`http_post` parameters on
`RemoteOKScraper`, `HunterIOProvider`, `GroqProvider`, `GeminiProvider`) so
tests can substitute a fake and never touch the network at all. A codebase
that *didn't* do this would need real API keys stored as GitHub Actions
secrets just to run its test suite in CI — an extra layer of secret
management, and a real risk if a workflow ever accidentally logged one.
Here, there's nothing to leak because there's nothing to configure.

---

## 4. Linting: what's checked, what deliberately isn't, and why

**`ruff check .`** runs against the whole repository. The rule selection in
`pyproject.toml` is deliberate, not the tool's defaults:

```toml
select = ["E", "F", "I", "B", "DTZ", "EXE", "PLW", "RUF"]
```

Two choices worth explaining, because getting a linter's *scope* right
matters as much as running one at all:

- **`UP` (pyupgrade) is deliberately excluded.** Ruff's out-of-the-box
  default flagged 92 instances of `typing.Dict`/`List`/`Optional` wanting
  to be rewritten as `dict`/`list`/`X | None`. This codebase has used
  `typing.Dict`/`List`/`Optional` **consistently since Phase 2**, on
  purpose, for readability uniformity across every phase and file (and
  because `requires-python = ">=3.9"` — the older syntax is the one
  guaranteed to read identically on every supported version). That's a
  deliberate style convention, not a bug pyupgrade should "fix" — enabling
  `UP` here would mean either reformatting ~15 files for a style-only
  change with zero behavior difference, or fighting the linter forever.
  Turning off a rule *because it conflicts with a real, stated convention*
  is a legitimate lint decision — turning off rules because they're
  inconvenient generally is not. This doc exists partly to make that
  distinction checkable, not just asserted.
- **No dedicated SQL linter** (e.g. `sqlfluff`) was added, even though the
  original project roadmap's "Cross-cutting" section said *"lint SQL/Python
  in CI."* The migrations-apply step (§2) is arguably a **stronger**
  correctness check than a SQL style linter would be: it proves every
  `.sql` file is not just *stylistically conformant* but *actually valid
  and successfully applies* against a real Postgres server, on every run.
  A style linter would add a second tool, a second config to maintain, and
  — given this codebase's heavily-commented SQL style — likely a wall of
  style-only findings needing tuning, for less correctness signal than
  "did it actually run." Named here as a deliberate scope decision, not a
  silently dropped requirement.

**Two real findings the linter surfaced and what happened to each** — the
value of adding a linter isn't just "now nothing fails," it's finding
things worth a decision:

- `B008` flagged `Depends(get_db_engine)` as a function call in an argument
  default — normally a real Python footgun (mutable default arguments), but
  it's FastAPI's own documented dependency-injection pattern. Configured an
  explicit exemption (`flake8-bugbear.extend-immutable-calls =
  ["fastapi.Depends", "fastapi.Query"]`) rather than just suppressing the
  warning inline — the exemption is framework-wide and self-documenting.
- `DTZ011` flagged `date.today()` in `eagent/ats/loader.py`'s
  `today_date_id()` — a **real, pre-existing latent bug class**: the
  Python process's local timezone and Postgres's `CURRENT_DATE` (used to
  populate `dim_dates`) aren't guaranteed to agree, so `today_date_id()`
  could compute a `date_id` one day off from the server's own "today" right
  around midnight in either timezone. Properly fixing this means picking a
  timezone policy for the whole app (all-UTC, or read "today" from Postgres
  itself) — a real design decision touching more than one function, out of
  scope for "set up CI." **Documented and `# noqa`'d with the reasoning
  in-line**, not silently fixed or silently ignored. This is what "a linter
  found something you should look at but not everything it finds is this
  task's job to fix" looks like in practice.

---

## 5. Interview checklist ✅

- [ ] What's the difference between a test that's always been skipping and
      one that's never existed — and which category did this project's
      integration tests fall into before Phase 6?
- [ ] Why is a GitHub Actions "service container" the right tool for giving
      CI a real Postgres, instead of e.g. mocking the database in CI too?
- [ ] Why does this workflow need zero secrets, and what earlier design
      decision (from which phase) is that a direct consequence of?
- [ ] Why was `UP` (pyupgrade) excluded from the ruff rule selection —
      and what would make excluding a lint rule the *wrong* call instead?
- [ ] Why is "run every migration against a fresh Postgres" arguably a
      stronger correctness check than a SQL style linter, for this project?
- [ ] What's the difference between how `B008` and `DTZ011` were each
      handled, and why did they get different treatment?

---

### Files in this phase
- `.github/workflows/ci.yml` — the workflow itself
- `pyproject.toml` — `[tool.ruff]` / `[tool.ruff.lint]` configuration
- `docs/07-continuous-integration.md` — this doc

**Still open:** the `dim_jobs → dim_dates` gap from `docs/05` §1 — still
the one unclosed schema gap this project has surfaced.
