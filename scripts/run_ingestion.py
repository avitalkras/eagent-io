#!/usr/bin/env python3
"""
Wires the Phase 2 pieces together end-to-end: scrape -> load -> enrich.

This is a thin CLI, not a library — it demonstrates how BaseScraper,
load_jobs, and RecruiterEnricher compose. Real scheduling later (Phase 2/3
GitHub Actions cron) would call the same functions.

Usage:
    python scripts/run_ingestion.py --limit 25
"""
from __future__ import annotations

import argparse
import logging

from eagent.config import get_engine, get_hunter_api_key
from eagent.enrichment import HunterIOProvider, RecruiterEnricher
from eagent.loader import load_jobs
from eagent.scrapers.remoteok import RemoteOKScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_ingestion")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape RemoteOK and load into the warehouse.")
    parser.add_argument("--limit", type=int, default=25, help="Max postings to fetch.")
    args = parser.parse_args()

    scraper = RemoteOKScraper()
    postings = scraper.fetch_jobs(limit=args.limit)
    logger.info("Fetched %d postings from %s", len(postings), scraper.source_name)

    engine = get_engine()
    summary = load_jobs(engine, postings)
    logger.info("Loaded: %s", summary)

    hunter_key = get_hunter_api_key()
    provider = HunterIOProvider(api_key=hunter_key) if hunter_key else None
    enricher = RecruiterEnricher(provider=provider)

    # Enrich each distinct company we just saw (demo: dedupe by name+domain).
    seen = {(p.company_name, p.company_domain) for p in postings}
    for company_name, domain in seen:
        contact = enricher.enrich(company_name, domain)
        logger.info(
            "Enriched %-30s -> %s (source=%s, confidence=%.2f)",
            company_name, contact.email, contact.enrichment_source, contact.confidence_score,
        )


if __name__ == "__main__":
    main()
