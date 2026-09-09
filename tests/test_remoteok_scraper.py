"""
Unit tests for RemoteOKScraper.

TEACHING POINT — why these tests never touch the network:
    RemoteOKScraper takes `http_get` as a constructor argument (dependency
    injection — see scrapers/remoteok.py). Here we pass a small fake function
    that returns a canned response object instead of `requests.get`. That
    makes these tests:
      * fast (no HTTP round trip),
      * deterministic (no flaky 3rd-party API, no rate limiting),
      * offline-friendly (CI doesn't need internet access).
    This is the standard way to unit test anything that calls an external API.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from eagent.models import JobPosting
from eagent.scrapers.remoteok import RemoteOKScraper


class FakeResponse:
    """Minimal stand-in for requests.Response — just what our code touches."""

    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


SAMPLE_PAYLOAD: List[Dict[str, Any]] = [
    {"legal": "This API is provided as-is..."},  # RemoteOK's leading notice row
    {
        "id": "1000001",
        "position": "Senior Data Engineer",
        "company": "Acme Corp",
        "url": "https://remoteok.com/remote-jobs/1000001",
        "location": "Worldwide",
        "tags": ["Python", "SQL", "Airflow", "python"],  # dup on purpose
        "description": "Build our data platform.",
        "date": "2026-09-01T00:00:00+00:00",
    },
    {
        # Malformed row: no id at all -> should be filtered before parsing.
        "position": "Ghost Row",
        "company": "Nobody",
    },
    {
        "id": "1000002",
        "position": "",  # invalid: JobPosting requires title min_length=1
        "company": "Broken Co",
    },
]


def make_scraper(payload: Any = SAMPLE_PAYLOAD) -> RemoteOKScraper:
    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        assert url == "https://remoteok.com/api"
        assert "User-Agent" in kwargs.get("headers", {})  # the 403-avoidance header
        return FakeResponse(payload)

    return RemoteOKScraper(http_get=fake_get)


def test_fetch_jobs_parses_valid_rows_and_skips_the_legal_notice():
    scraper = make_scraper()
    postings = scraper.fetch_jobs()

    # 4 raw rows in: 1 legal notice (no id, filtered pre-parse), 1 no-id ghost
    # row (filtered pre-parse), 1 empty-title row (fails Pydantic validation),
    # 1 good row. Only the good one should survive.
    assert len(postings) == 1
    posting = postings[0]
    assert isinstance(posting, JobPosting)
    assert posting.external_source == "remoteok"
    assert posting.external_id == "1000001"
    assert posting.title == "Senior Data Engineer"
    assert posting.company_name == "Acme Corp"


def test_tech_stack_is_lowercased_and_deduped():
    postings = make_scraper().fetch_jobs()
    # 'Python' and 'python' from the fixture must collapse into one entry.
    assert postings[0].tech_stack == ["airflow", "python", "sql"]


def test_fetch_jobs_respects_limit():
    payload = [SAMPLE_PAYLOAD[0]] + [
        {"id": str(i), "position": f"Job {i}", "company": "Acme", "tags": []}
        for i in range(5)
    ]
    scraper = make_scraper(payload)
    postings = scraper.fetch_jobs(limit=2)
    assert len(postings) == 2


def test_fetch_jobs_raises_on_http_error():
    def failing_get(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(payload=None, status_code=403)

    scraper = RemoteOKScraper(http_get=failing_get)
    with pytest.raises(RuntimeError):
        scraper.fetch_jobs()
