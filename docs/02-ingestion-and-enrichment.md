# Phase 2 — Ingestion & Recruiter Enrichment

> **Goal of this doc:** teach you *why* the Python code is structured the way
> it is — the design patterns, not just the syntax. If you can explain every
> section here in an interview, you understand this layer at a mid-level Data
> Engineer standard.

---

## 1. The pipeline this phase builds

```
RemoteOKScraper.fetch_jobs()        load_jobs()                RecruiterEnricher.enrich()
  (BaseScraper)          ──────▶  (idempotent upsert)  ──────▶  (Hunter.io or fallback)
        │                              │                              │
        ▼                              ▼                              ▼
  List[JobPosting]           dim_companies / dim_jobs           RecruiterContact
  (Pydantic, validated)      (Postgres, Phase 1 schema)         (Pydantic, validated)
```

`scripts/run_ingestion.py` wires all three stages together end-to-end.

---

## 2. `BaseScraper` — programming to an interface, not an implementation

```python
class BaseScraper(ABC):
    source_name: str

    @abstractmethod
    def fetch_jobs(self, limit=None) -> List[JobPosting]: ...
```

This is the **Dependency Inversion Principle** (the "D" in SOLID): high-level
code (the loader, the CLI) depends on the *abstraction* `BaseScraper`, never on
a concrete class like `RemoteOKScraper`. Concretely, that means:

- Adding a `GreenhouseScraper` or `LeverScraper` next sprint requires **zero**
  changes to `loader.py` or `scripts/run_ingestion.py`.
- `python`'s `abc.abstractmethod` makes this enforced, not just a convention —
  Python refuses to instantiate a subclass that doesn't implement
  `fetch_jobs()`.

> **Interview framing:** "We used an ABC so every scraper is interchangeable
> from the pipeline's point of view — the Strategy pattern applied to data
> sources."

---

## 3. Dependency Injection — the reason these tests never hit the network

Look at `RemoteOKScraper.__init__`:

```python
def __init__(self, http_get: Optional[HttpGet] = None, timeout: float = 10.0):
    self._get = http_get or requests.get
```

The HTTP function is a **constructor argument**, not hardcoded inside
`fetch_jobs()`. In production we omit it and get the real `requests.get`. In
`tests/test_remoteok_scraper.py` we pass a fake function that returns canned
JSON instantly:

```python
def fake_get(url, **kwargs):
    return FakeResponse(SAMPLE_PAYLOAD)

scraper = RemoteOKScraper(http_get=fake_get)
```

This is **dependency injection**: the class receives its collaborators from
the outside instead of constructing them itself. It's what "mockable" means in
practice — no network call, no flakiness, no rate limits, no internet
required in CI. The exact same technique makes `HunterIOProvider` testable
(requirement #4) and `get_engine()` swappable for a test database.

---

## 4. Pydantic — validation as a contract, not a suggestion

`JobPosting` and `RecruiterContact` (in `models.py`) aren't just type hints —
Pydantic **enforces** them at runtime:

```python
class JobPosting(BaseModel):
    title: str = Field(..., min_length=1)
    ...
```

If a scraper hands us a row with an empty title, `JobPosting(...)` raises
`ValidationError` **immediately**, with a precise message about which field
failed. Compare that to what happens without validation: bad data flows
silently into Postgres until a `NOT NULL` constraint (or worse, nothing)
catches it three steps later, far from the code that caused it.

We also use a **custom validator** to normalize data on the way in:

```python
@field_validator("tech_stack", mode="before")
@classmethod
def _normalize_tech_stack(cls, value):
    return sorted({str(tag).strip().lower() for tag in value if str(tag).strip()})
```

`"Python"` and `"python"` from a messy API response become one canonical tag.
This is the same instinct as `CITEXT` on `dim_companies.domain` in Phase 1 —
normalize inconsistent real-world data at the boundary, once, instead of
`LOWER()`-ing it in every downstream query.

> **Defense in depth:** `RecruiterContact.confidence_score` is bounded
> `0.0–1.0` by Pydantic here — the *same* rule the database enforces with a
> `CHECK` constraint in `sql/01_schema.sql`. Two independent layers catching
> the same class of bug is a deliberate redundancy, not duplication.

---

## 5. The idempotent loader — SQLAlchemy Core, and the upsert-and-get-id pattern

### Why Core, not the ORM?

SQLAlchemy has two APIs: the full **ORM** (declarative model classes, a
`Session`, an identity map, lazy-loaded relationships) and **Core** (typed,
composable SQL statement building). We chose Core because:

- We're not modeling an object graph with relationships to traverse — we're
  inserting rows and reading ids back. That's what Core is *for*.
- Full ORM machinery (unit-of-work, session lifecycle) would be more
  complexity than this job needs.
- Core still gives us the two things that matter: **parameterized queries**
  (no SQL injection — never format user data into a SQL string) and
  **Postgres-specific `ON CONFLICT`** support.

### Two different upsert shapes, on purpose

**`dim_jobs` — `ON CONFLICT DO NOTHING`:**

```python
stmt = pg_insert(dim_jobs).values(...).on_conflict_do_nothing(
    index_elements=[dim_jobs.c.external_source, dim_jobs.c.external_id]
).returning(dim_jobs.c.job_id)
```

If the job already exists, do nothing — we don't need its id back, we just
need to *not* create a duplicate. Requirement #3, satisfied directly.

**`dim_companies` — `ON CONFLICT DO UPDATE ... RETURNING`:**

```python
stmt = pg_insert(dim_companies).values(...).on_conflict_do_update(
    index_elements=[dim_companies.c.domain],
    set_={"updated_at": func.now()},
).returning(dim_companies.c.company_id)
```

Here we *do* need something back: the `company_id` to use as the foreign key
on the job row we're about to insert. `DO NOTHING` returns **zero rows** on a
conflict — there'd be nothing to read the id from. `DO UPDATE` always
"touches" a row (new or existing), so `RETURNING` always gives us exactly one
id. This is the standard Postgres **upsert-and-get-id** pattern — memorize it,
you'll use it constantly.

### The whole batch is one transaction

```python
with engine.begin() as conn:
    for posting in postings:
        ...
```

`engine.begin()` opens **one transaction** for the entire batch. If posting
#47 out of 100 raises an exception, *nothing* in that call commits — you never
end up with a half-loaded batch. Same atomicity principle as the
`BEGIN; ... COMMIT;` wrapper around `sql/01_schema.sql` in Phase 1.

### A trade-off worth knowing (asked in interviews)

`get_or_create_company()` falls back to a plain "SELECT, then INSERT if
missing" when a company has no domain (nothing to `ON CONFLICT` on). That's
**not safe under concurrent writers** — two scraper processes racing at the
exact same instant could both miss the SELECT and both INSERT, creating a
duplicate row. Fine for a single-process scraper (what we have); a truly
concurrent system would need a partial unique index
(`UNIQUE(name) WHERE domain IS NULL`) or to serialize writes per company name.
Naming this trade-off out loud is exactly what separates "it works" from
"I understand what it doesn't handle yet."

---

## 6. `RecruiterEnricher` — the Strategy pattern, again

```python
class EnrichmentProvider(ABC):
    def find_contact(self, company_name, domain) -> Optional[RecruiterContact]: ...

class HunterIOProvider(EnrichmentProvider): ...
```

Same shape as `BaseScraper`: `RecruiterEnricher` doesn't know or care whether
it's holding a `HunterIOProvider`, an `ApolloProvider` we haven't written yet,
or a `FakeProvider` in a test. It just calls `.find_contact(...)`. This is
what makes requirement #4 — "mockable... with fallback" — clean to implement:

```python
def enrich(self, company_name, domain) -> RecruiterContact:
    if domain and self._provider is not None:
        try:
            contact = self._provider.find_contact(company_name, domain)
            if contact is not None:
                return contact
        except Exception:
            logger.warning("...", exc_info=True)  # degrade, don't crash
    return self._generic_fallback(company_name, domain)
```

Three things worth noticing:

1. **A flaky 3rd-party API degrades the pipeline instead of crashing it.**
   Enrichment providers *will* time out or rate-limit eventually — catching
   the exception and falling back is what makes the pipeline resilient rather
   than brittle.
2. **The fallback always returns something usable** — a low-confidence
   (`0.1`) generic `careers@domain` guess — instead of `None`. Downstream
   outreach logic never has to special-case "no contact found."
3. **Confidence score is the signal**, not a boolean "found/not found". A
   human reviewer (the Phase 4 approval gate) can filter or sort by it.

---

## 7. Testing strategy: three layers, three techniques

| What | How | File |
|---|---|---|
| `RemoteOKScraper` parsing logic | fake `http_get` function, no network | `tests/test_remoteok_scraper.py` |
| `RecruiterEnricher` orchestration | `FakeProvider` implementing the interface directly | `tests/test_enrichment.py` |
| `HunterIOProvider` JSON parsing | fake `http_get`, same technique as the scraper | `tests/test_enrichment.py` |
| `load_jobs()` idempotency | **real** Postgres, gated behind `DATABASE_URL` | `tests/test_loader_integration.py` |

The loader test is **not mocked** — it runs Postgres-dialect SQL
(`ON CONFLICT`, array columns) via SQLAlchemy, and faking that would mean
re-implementing Postgres's own conflict semantics in a test double, which
proves nothing. Instead it's marked `@pytest.mark.integration` and
**skipped automatically** when `DATABASE_URL` isn't set, so `pytest` stays
fast for everyday unit-test runs:

```bash
pytest                                    # unit tests only (fast, offline)
DATABASE_URL=postgresql+psycopg2://localhost/eagent_test pytest -m integration
```

> **Rule of thumb:** mock at a boundary you don't own (an HTTP API). Don't
> mock the thing you're actually testing the behavior of (Postgres's own
> upsert semantics) — test that for real, and gate it behind an environment
> check instead.

---

## 8. Interview checklist ✅

- [ ] What problem does an ABC (`BaseScraper`) solve that a plain class
      wouldn't?
- [ ] What is dependency injection, and how does it make code testable
      without mocking libraries?
- [ ] Why validate with Pydantic *and* enforce the same rule with a DB
      `CHECK` constraint — isn't that redundant?
- [ ] `ON CONFLICT DO NOTHING` vs. `ON CONFLICT DO UPDATE ... RETURNING` —
      when do you need each?
- [ ] Why is the loader wrapped in a single `engine.begin()` transaction?
- [ ] What's the race condition in `get_or_create_company()`'s no-domain
      fallback, and how would you fix it for a concurrent system?
- [ ] Why does `RecruiterEnricher.enrich()` catch exceptions from the
      provider instead of letting them propagate?
- [ ] Why is the loader test not mocked, when the scraper and enricher tests
      are?

---

### Files in this phase
- `src/eagent/models.py` — Pydantic models (`JobPosting`, `RecruiterContact`)
- `src/eagent/scrapers/base.py` — `BaseScraper` ABC
- `src/eagent/scrapers/remoteok.py` — `RemoteOKScraper`
- `src/eagent/loader.py` — idempotent SQLAlchemy Core loader
- `src/eagent/enrichment.py` — `EnrichmentProvider`, `HunterIOProvider`, `RecruiterEnricher`
- `src/eagent/config.py` — env/config loading (`DATABASE_URL`, `HUNTER_API_KEY`)
- `scripts/run_ingestion.py` — wires scrape → load → enrich end-to-end
- `tests/` — unit tests (mocked) + one DB-gated integration test

**Next up → Phase 3/4:** LLM ATS scoring, resume tailoring, and the
human-in-the-loop approval workflow that writes into `fact_outreach`.
