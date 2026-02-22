"""PitchBook listener — polls for changes to watched companies.

Runs on a configurable interval and detects:
- New deals / financing rounds
- Status changes (e.g. company went public, acquired)
- Key metric updates (employee count, valuation)
- New team members
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pitchbook.client import PitchBookClient
from pitchbook.config import Settings
from pitchbook.models import ChangeEvent, Company
from pitchbook.store import PitchBookStore

logger = logging.getLogger(__name__)

ChangeCallback = Callable[[ChangeEvent], None]


class PitchBookListener:
    """Continuously monitors watched companies for changes.

    Usage::

        listener = PitchBookListener(settings)
        listener.on_change(my_callback)  # register handlers
        await listener.run()             # blocks, polling forever
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: PitchBookStore | None = None,
        client: PitchBookClient | None = None,
    ) -> None:
        self._settings = settings or Settings()  # type: ignore[call-arg]
        self._store = store or PitchBookStore(self._settings.db_path)
        self._client = client or PitchBookClient(self._settings)
        self._callbacks: list[ChangeCallback] = []
        self._running = False

    def on_change(self, callback: ChangeCallback) -> None:
        """Register a callback invoked for every detected change."""
        self._callbacks.append(callback)

    async def run(self) -> None:
        """Start the polling loop. Blocks until cancelled."""
        self._running = True
        logger.info(
            "PitchBook listener started (poll interval: %ds)",
            self._settings.poll_interval_seconds,
        )
        try:
            while self._running:
                await self._poll_cycle()
                await asyncio.sleep(self._settings.poll_interval_seconds)
        finally:
            await self._client.close()

    def stop(self) -> None:
        self._running = False

    async def _poll_cycle(self) -> None:
        """Run one full check across all watched companies."""
        watched = self._store.list_watched_companies()
        if not watched:
            logger.debug("No watched companies — skipping poll cycle")
            return

        logger.info("Polling %d watched companies", len(watched))
        for company_id, company_name in watched:
            try:
                await self._check_company(company_id, company_name)
            except Exception:
                logger.exception("Error checking company %s (%s)", company_id, company_name)

    async def _check_company(self, company_id: str, company_name: str) -> None:
        """Compare fresh API data against stored snapshot and emit changes."""
        # Fetch latest from API
        fresh = await self._client.get_company(company_id)
        stored = self._store.get_company(company_id)

        if stored is None:
            # First time seeing this company — store and emit
            self._store.upsert_company(fresh)
            self._emit(
                ChangeEvent(
                    entity_type="company",
                    entity_id=company_id,
                    entity_name=company_name,
                    change_type="new",
                    summary=f"Initial data captured for {company_name}",
                )
            )
        else:
            self._detect_company_changes(stored, fresh)
            self._store.upsert_company(fresh)

        # Check for new deals
        await self._check_deals(company_id, company_name)

    def _detect_company_changes(self, old: Company, new: Company) -> None:
        """Compare two snapshots and emit change events."""
        if old.status != new.status:
            self._emit(
                ChangeEvent(
                    entity_type="company",
                    entity_id=new.pitchbook_id,
                    entity_name=new.name,
                    change_type="status_change",
                    summary=f"{new.name} status changed: {old.status.value} → {new.status.value}",
                    details={"old_status": old.status.value, "new_status": new.status.value},
                )
            )

        if (
            new.total_raised_usd is not None
            and old.total_raised_usd is not None
            and new.total_raised_usd != old.total_raised_usd
        ):
            self._emit(
                ChangeEvent(
                    entity_type="company",
                    entity_id=new.pitchbook_id,
                    entity_name=new.name,
                    change_type="funding_update",
                    summary=(
                        f"{new.name} total raised changed: "
                        f"${old.total_raised_usd:,.0f} → ${new.total_raised_usd:,.0f}"
                    ),
                    details={
                        "old_total_raised": old.total_raised_usd,
                        "new_total_raised": new.total_raised_usd,
                    },
                )
            )

        if (
            new.employee_count is not None
            and old.employee_count is not None
            and new.employee_count != old.employee_count
        ):
            self._emit(
                ChangeEvent(
                    entity_type="company",
                    entity_id=new.pitchbook_id,
                    entity_name=new.name,
                    change_type="employee_count_change",
                    summary=(
                        f"{new.name} employee count changed: "
                        f"{old.employee_count:,} → {new.employee_count:,}"
                    ),
                    details={
                        "old_count": old.employee_count,
                        "new_count": new.employee_count,
                    },
                )
            )

        if new.last_financing_date and new.last_financing_date != old.last_financing_date:
            self._emit(
                ChangeEvent(
                    entity_type="company",
                    entity_id=new.pitchbook_id,
                    entity_name=new.name,
                    change_type="new_financing",
                    summary=(
                        f"{new.name} new financing: {new.last_financing_deal_type} "
                        f"on {new.last_financing_date}"
                    ),
                    details={
                        "deal_type": new.last_financing_deal_type,
                        "date": str(new.last_financing_date),
                        "size_usd": new.last_financing_size_usd,
                    },
                )
            )

    async def _check_deals(self, company_id: str, company_name: str) -> None:
        """Detect new deals that aren't in the local store."""
        api_deals = await self._client.get_company_deals(company_id)
        stored_deals = self._store.get_deals_for_company(company_id)
        stored_ids = {d.pitchbook_id for d in stored_deals}

        for deal in api_deals:
            if deal.pitchbook_id not in stored_ids:
                self._store.upsert_deal(deal)
                self._emit(
                    ChangeEvent(
                        entity_type="deal",
                        entity_id=deal.pitchbook_id,
                        entity_name=company_name,
                        change_type="new_deal",
                        summary=(
                            f"New {deal.deal_type.value} deal for {company_name}"
                            + (f": ${deal.deal_size_usd:,.0f}" if deal.deal_size_usd else "")
                        ),
                        details=deal.model_dump(mode="json"),
                    )
                )

    def _emit(self, event: ChangeEvent) -> None:
        """Persist the event and invoke all registered callbacks."""
        logger.info("Change detected: %s", event.summary)
        self._store.record_change(event)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                logger.exception("Error in change callback")
