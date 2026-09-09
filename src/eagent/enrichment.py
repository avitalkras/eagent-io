"""
Recruiter enrichment — find (or credibly guess) a contact for a company.

DESIGN PATTERN: Strategy.
    `EnrichmentProvider` is an interface any 3rd-party contact API can
    implement (Hunter.io here; Apollo, Clearbit, etc. would be siblings).
    `RecruiterEnricher` doesn't know or care which provider it's holding —
    it just calls `.find_contact(...)`. Swapping providers, or replacing one
    with a test double, never touches this orchestration logic. This is the
    same "depend on an abstraction" idea as BaseScraper (scrapers/base.py).

MOCKABILITY (requirement #4):
    Both the provider's HTTP calls (`http_get`) and the provider itself
    (`RecruiterEnricher(provider=...)`) are constructor-injected. Tests never
    touch the real network — see tests/test_enrichment.py for both a fake
    provider and a fake HTTP client used directly against HunterIOProvider.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Callable, Optional

import requests

from eagent.models import RecruiterContact

logger = logging.getLogger(__name__)

GENERIC_CAREER_PREFIX = "careers"

HttpGet = Callable[..., "requests.Response"]


class EnrichmentProvider(ABC):
    """Contract for a 3rd-party contact-enrichment API."""

    name: str

    @abstractmethod
    def find_contact(self, company_name: str, domain: str) -> Optional[RecruiterContact]:
        """Return the best contact found for a company, or None if none found."""
        raise NotImplementedError


class HunterIOProvider(EnrichmentProvider):
    """Looks up HR/recruiting contacts via Hunter.io's Domain Search API.

    Docs: https://hunter.io/api-documentation/v2#domain-search
    """

    name = "hunter_io"
    _API_URL = "https://api.hunter.io/v2/domain-search"

    def __init__(self, api_key: str, http_get: Optional[HttpGet] = None, timeout: float = 10.0):
        self._api_key = api_key
        self._get: HttpGet = http_get or requests.get  # injectable for tests
        self._timeout = timeout

    def find_contact(self, company_name: str, domain: str) -> Optional[RecruiterContact]:
        response = self._get(
            self._API_URL,
            params={"domain": domain, "api_key": self._api_key, "department": "hr"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        emails = (payload.get("data") or {}).get("emails") or []
        if not emails:
            return None

        # Hunter.io returns emails ranked best-first; take the top match.
        best = emails[0]
        full_name = " ".join(filter(None, [best.get("first_name"), best.get("last_name")])) or None

        return RecruiterContact(
            company_domain=domain,
            full_name=full_name,
            email=best.get("value"),
            linkedin_url=best.get("linkedin") or None,
            enrichment_source=self.name,
            # Hunter scores confidence 0-100; our model expects 0.0-1.0.
            confidence_score=(best.get("confidence") or 0) / 100,
        )


class RecruiterEnricher:
    """Finds a recruiter/HR contact for a company, with a safe fallback.

    Strategy:
      1. If we have a domain and a configured provider, ask it.
      2. If the provider finds nothing, errors, or we have no provider/domain
         at all, fall back to a generic `careers@domain` guess — clearly
         low-confidence (0.1), but it always gives the downstream outreach
         step *something* to work with instead of a null contact.
    """

    def __init__(self, provider: Optional[EnrichmentProvider] = None):
        self._provider = provider

    def enrich(self, company_name: str, domain: Optional[str]) -> RecruiterContact:
        if domain and self._provider is not None:
            try:
                contact = self._provider.find_contact(company_name, domain)
                if contact is not None:
                    return contact
            except Exception:
                # A flaky 3rd-party API should degrade the pipeline, not
                # crash it. Log loudly, then fall through to the fallback.
                logger.warning(
                    "Enrichment provider '%s' failed for domain=%s; using fallback",
                    self._provider.name, domain, exc_info=True,
                )

        return self._generic_fallback(company_name, domain)

    def _generic_fallback(self, company_name: str, domain: Optional[str]) -> RecruiterContact:
        fallback_domain = domain or f"{_slugify(company_name)}.com"
        return RecruiterContact(
            company_domain=fallback_domain,
            full_name=None,
            email=f"{GENERIC_CAREER_PREFIX}@{fallback_domain}",
            linkedin_url=None,
            enrichment_source="generic_fallback",
            confidence_score=0.1,  # a guess, not a match — kept deliberately low
        )


def _slugify(text: str) -> str:
    """'Acme, Inc.' -> 'acmeinc' — good enough for a fallback-domain guess."""
    return re.sub(r"[^a-z0-9]", "", text.lower())
