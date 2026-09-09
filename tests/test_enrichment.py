"""
Unit tests for RecruiterEnricher and HunterIOProvider.

TEACHING POINT — two layers of mocking, matching the two layers of the code:
    1. `FakeProvider` implements the `EnrichmentProvider` interface directly
       (no HTTP at all) — used to test RecruiterEnricher's orchestration
       logic (provider-hit vs. provider-miss vs. provider-error -> fallback).
    2. `fake_get` fakes the raw HTTP call *inside* HunterIOProvider itself —
       used to test HunterIOProvider's own JSON-parsing logic in isolation.
    Testing each layer against its own boundary (not the network) is what
    makes the word "mockable" in the requirements concrete.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from eagent.enrichment import EnrichmentProvider, HunterIOProvider, RecruiterEnricher
from eagent.models import RecruiterContact


class FakeResponse:
    def __init__(self, payload: Dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._payload


class FakeProvider(EnrichmentProvider):
    """Test double satisfying EnrichmentProvider — no network, no HTTP layer."""

    name = "fake_provider"

    def __init__(self, result: Optional[RecruiterContact] = None, raises: bool = False):
        self._result = result
        self._raises = raises

    def find_contact(self, company_name: str, domain: str) -> Optional[RecruiterContact]:
        if self._raises:
            raise ConnectionError("simulated provider outage")
        return self._result


# --- RecruiterEnricher orchestration --------------------------------------


def test_enrich_returns_provider_result_when_found():
    expected = RecruiterContact(
        company_domain="acme.com",
        full_name="Jane Recruiter",
        email="jane@acme.com",
        enrichment_source="fake_provider",
        confidence_score=0.9,
    )
    enricher = RecruiterEnricher(provider=FakeProvider(result=expected))

    contact = enricher.enrich("Acme Corp", "acme.com")

    assert contact == expected


def test_enrich_falls_back_when_provider_finds_nothing():
    enricher = RecruiterEnricher(provider=FakeProvider(result=None))

    contact = enricher.enrich("Acme Corp", "acme.com")

    assert contact.enrichment_source == "generic_fallback"
    assert contact.email == "careers@acme.com"
    assert contact.confidence_score == pytest.approx(0.1)


def test_enrich_falls_back_when_provider_raises():
    """A flaky 3rd-party API must degrade gracefully, not crash the pipeline."""
    enricher = RecruiterEnricher(provider=FakeProvider(raises=True))

    contact = enricher.enrich("Acme Corp", "acme.com")

    assert contact.enrichment_source == "generic_fallback"


def test_enrich_falls_back_when_no_domain_known():
    enricher = RecruiterEnricher(provider=FakeProvider(result=None))

    contact = enricher.enrich("Weird Co, Inc.", domain=None)

    assert contact.company_domain == "weirdcoinc.com"
    assert contact.email == "careers@weirdcoinc.com"


def test_enrich_with_no_provider_configured_goes_straight_to_fallback():
    enricher = RecruiterEnricher(provider=None)

    contact = enricher.enrich("Acme Corp", "acme.com")

    assert contact.enrichment_source == "generic_fallback"


# --- HunterIOProvider JSON parsing -----------------------------------------


def test_hunter_provider_parses_top_match():
    payload = {
        "data": {
            "emails": [
                {
                    "value": "jane.doe@acme.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "confidence": 87,
                    "linkedin": None,
                },
            ]
        }
    }

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        assert kwargs["params"]["domain"] == "acme.com"
        return FakeResponse(payload)

    provider = HunterIOProvider(api_key="fake-key", http_get=fake_get)
    contact = provider.find_contact("Acme Corp", "acme.com")

    assert contact is not None
    assert contact.email == "jane.doe@acme.com"
    assert contact.full_name == "Jane Doe"
    assert contact.confidence_score == pytest.approx(0.87)
    assert contact.enrichment_source == "hunter_io"


def test_hunter_provider_returns_none_when_no_emails_found():
    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse({"data": {"emails": []}})

    provider = HunterIOProvider(api_key="fake-key", http_get=fake_get)
    assert provider.find_contact("Acme Corp", "acme.com") is None
