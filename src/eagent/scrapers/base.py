"""
BaseScraper — the abstract interface every job-board scraper implements.

WHY AN ABSTRACT BASE CLASS (ABC)?
    We're going to add more scrapers over time (Greenhouse, Lever, LinkedIn...).
    Everything downstream — the loader, the enrichment step, the CLI — should
    be able to treat "a scraper" as one interchangeable thing: "give me a
    list of JobPosting objects." That's the Dependency Inversion Principle
    (the 'D' in SOLID): high-level code (the pipeline) depends on an
    abstraction (BaseScraper), not on a concrete class (RemoteOKScraper).

    Concretely, this means `python -m eagent.pipeline` never needs an
    `if source == "remoteok": ... elif source == "greenhouse": ...` chain.
    It just calls `scraper.fetch_jobs()` on whichever scraper it was given.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from eagent.models import JobPosting


class BaseScraper(ABC):
    """Contract: turn a job board's API response into normalized JobPostings."""

    #: Short, stable identifier for this source. Used as `external_source`
    #: on every JobPosting it produces, and as the idempotency key's prefix.
    source_name: str

    @abstractmethod
    def fetch_jobs(self, limit: Optional[int] = None) -> List[JobPosting]:
        """Fetch postings from the source and return them as JobPosting models.

        Args:
            limit: optional cap on how many postings to return (useful for
                local testing so you don't pull a whole board every run).

        Returns:
            A list of validated JobPosting objects. Malformed rows from the
            source should be logged and skipped, never raised — one bad row
            shouldn't crash an entire ingestion run.
        """
        raise NotImplementedError
