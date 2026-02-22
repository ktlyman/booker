"""Historical data importer for PitchBook.

Given a set of company names or PitchBook IDs, fetches and stores:
- Company profiles
- Full deal / financing history
- Investor details for each deal
- Key people

Supports both one-shot bulk imports and incremental refreshes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from pitchbook.client import PitchBookClient
from pitchbook.config import Settings
from pitchbook.models import Company
from pitchbook.store import PitchBookStore

logger = logging.getLogger(__name__)


@dataclass
class ImportStats:
    """Tracks what was imported during a run."""

    companies: int = 0
    deals: int = 0
    investors: int = 0
    people: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.companies + self.deals + self.investors + self.people


class PitchBookImporter:
    """Bulk-import historical PitchBook data for a portfolio of companies.

    Usage::

        importer = PitchBookImporter(settings)
        stats = await importer.import_companies(["Stripe", "Anthropic", "SpaceX"])
        print(stats)

        # Or by PitchBook IDs
        stats = await importer.import_by_ids(["12345-67", "89012-34"])
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: PitchBookStore | None = None,
        client: PitchBookClient | None = None,
        concurrency: int = 5,
    ) -> None:
        self._settings = settings or Settings()  # type: ignore[call-arg]
        self._store = store or PitchBookStore(self._settings.db_path)
        self._client = client or PitchBookClient(self._settings)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> PitchBookImporter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def import_companies(
        self,
        company_names: list[str],
        *,
        watch: bool = True,
    ) -> ImportStats:
        """Search for companies by name and import all associated data.

        Args:
            company_names: List of company names to search and import.
            watch: If True, add imported companies to the watched list
                   so the listener monitors them going forward.
        """
        stats = ImportStats()
        resolved: list[Company] = []

        # Resolve names to PitchBook entities
        for name in company_names:
            try:
                matches = await self._client.search_companies(name, limit=1)
                if matches:
                    resolved.append(matches[0])
                else:
                    msg = f"No PitchBook match for '{name}'"
                    logger.warning(msg)
                    stats.errors.append(msg)
            except Exception as exc:
                msg = f"Error searching for '{name}': {exc}"
                logger.error(msg)
                stats.errors.append(msg)

        # Import each resolved company concurrently
        tasks = [self._import_single(c, stats, watch=watch) for c in resolved]
        await asyncio.gather(*tasks)
        return stats

    async def import_by_ids(
        self,
        pitchbook_ids: list[str],
        *,
        watch: bool = True,
    ) -> ImportStats:
        """Import companies directly by their PitchBook IDs."""
        stats = ImportStats()
        companies: list[Company] = []
        for pid in pitchbook_ids:
            try:
                company = await self._client.get_company(pid)
                companies.append(company)
            except Exception as exc:
                msg = f"Error fetching company {pid}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)

        tasks = [self._import_single(c, stats, watch=watch) for c in companies]
        await asyncio.gather(*tasks)
        return stats

    async def refresh_all(self) -> ImportStats:
        """Re-import data for every company currently in the store."""
        stats = ImportStats()
        companies = self._store.list_companies()
        tasks = [self._import_single(c, stats, watch=False) for c in companies]
        await asyncio.gather(*tasks)
        return stats

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _import_single(
        self,
        company: Company,
        stats: ImportStats,
        *,
        watch: bool,
    ) -> None:
        async with self._semaphore:
            cid = company.pitchbook_id
            logger.info("Importing %s (%s)", company.name, cid)

            # Store company profile
            self._store.upsert_company(company)
            stats.companies += 1

            if watch:
                self._store.add_watched_company(cid, company.name)

            # Deals
            try:
                deals = await self._client.get_company_deals(cid)
                for deal in deals:
                    self._store.upsert_deal(deal)
                    stats.deals += 1
            except Exception as exc:
                msg = f"Error fetching deals for {company.name}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)

            # Investors from deals
            try:
                investors = await self._client.get_company_investors(cid)
                for inv in investors:
                    self._store.upsert_investor(inv)
                    stats.investors += 1
            except Exception as exc:
                msg = f"Error fetching investors for {company.name}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)

            # People
            try:
                people = await self._client.get_company_people(cid)
                for person in people:
                    self._store.upsert_person(person)
                    stats.people += 1
            except Exception as exc:
                msg = f"Error fetching people for {company.name}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)

            logger.info("Done importing %s", company.name)
