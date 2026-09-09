"""
RemoteOKScraper — concrete BaseScraper for the public RemoteOK JSON API.

API: GET https://remoteok.com/api  (no auth key required)

Two things worth knowing about this API, both handled below:
  1. It 403s a default `python-requests` User-Agent — you must send a
     realistic one. Common gotcha with public APIs: they gate on headers,
     not just keys.
  2. The response is a JSON *array* whose first element is a legal/metadata
     notice, not a job — you have to filter it out.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import requests
from pydantic import ValidationError

from eagent.models import JobPosting
from eagent.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

REMOTEOK_API_URL = "https://remoteok.com/api"

# HttpGet is the shape of `requests.get`: a callable(url, **kwargs) -> Response.
# Naming it makes the constructor's type hint below self-documenting.
HttpGet = Callable[..., "requests.Response"]


class RemoteOKScraper(BaseScraper):
    """Fetches and normalizes postings from remoteok.com/api."""

    source_name = "remoteok"

    def __init__(self, http_get: Optional[HttpGet] = None, timeout: float = 10.0):
        # DEPENDENCY INJECTION: the HTTP function is a constructor argument,
        # not a hardcoded `requests.get` call inside fetch_jobs(). In
        # production we omit it and get the real `requests.get`. In tests
        # (see tests/test_remoteok_scraper.py) we pass a fake function that
        # returns canned JSON — no network call, no flakiness, no rate limits.
        self._get: HttpGet = http_get or requests.get
        self._timeout = timeout

    def fetch_jobs(self, limit: Optional[int] = None) -> List[JobPosting]:
        response = self._get(
            REMOTEOK_API_URL,
            headers={"User-Agent": "Eagent.io Job Scraper (educational project)"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload: List[Dict[str, Any]] = response.json()

        # Skip the leading legal-notice element (it has no "id" field).
        job_rows = [row for row in payload if isinstance(row, dict) and row.get("id")]
        if limit is not None:
            job_rows = job_rows[:limit]

        postings: List[JobPosting] = []
        for row in job_rows:
            posting = self._parse_job(row)
            if posting is not None:
                postings.append(posting)

        logger.info(
            "RemoteOKScraper: parsed %d/%d rows into valid JobPostings",
            len(postings), len(job_rows),
        )
        return postings

    def _parse_job(self, row: Dict[str, Any]) -> Optional[JobPosting]:
        """Map one raw RemoteOK row to a JobPosting, or None if it's unusable.

        A single malformed row (missing title, bad URL, ...) should never
        crash the whole scrape — we log it and move on.
        """
        try:
            return JobPosting(
                external_source=self.source_name,
                external_id=str(row["id"]),
                title=row.get("position") or row.get("title") or "",
                company_name=row.get("company") or "Unknown",
                url=row.get("url") or None,
                location=row.get("location") or None,
                tech_stack=row.get("tags") or [],
                raw_description=row.get("description"),
                posted_at=row.get("date"),
            )
        except ValidationError:
            logger.warning(
                "Skipping malformed RemoteOK row id=%s", row.get("id"), exc_info=True
            )
            return None
