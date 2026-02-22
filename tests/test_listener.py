"""Tests for the PitchBook listener."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pitchbook.config import Settings
from pitchbook.listener import PitchBookListener
from pitchbook.models import ChangeEvent, Company, CompanyStatus, Deal, DealType
from pitchbook.store import PitchBookStore


@pytest.fixture()
def mock_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def listener(
    settings: Settings, store: PitchBookStore, mock_client: AsyncMock
) -> PitchBookListener:
    return PitchBookListener(settings=settings, store=store, client=mock_client)


def test_on_change_registers_callback(listener: PitchBookListener) -> None:
    cb = MagicMock()
    listener.on_change(cb)
    assert cb in listener._callbacks


@pytest.mark.asyncio
async def test_poll_cycle_no_watched(
    listener: PitchBookListener, store: PitchBookStore
) -> None:
    """If no companies are watched, the cycle should be a no-op."""
    await listener._poll_cycle()
    listener._client.get_company.assert_not_called()


@pytest.mark.asyncio
async def test_poll_cycle_new_company(
    listener: PitchBookListener, store: PitchBookStore, mock_client: AsyncMock
) -> None:
    """First time polling a company should emit a 'new' change event."""
    store.add_watched_company("C-1", "Acme")
    mock_client.get_company.return_value = Company(
        pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE
    )
    mock_client.get_company_deals.return_value = []

    changes: list[ChangeEvent] = []
    listener.on_change(lambda ev: changes.append(ev))
    await listener._poll_cycle()

    assert len(changes) == 1
    assert changes[0].change_type == "new"
    assert changes[0].entity_name == "Acme"

    # Company should now be stored
    assert store.get_company("C-1") is not None


@pytest.mark.asyncio
async def test_detect_status_change(
    listener: PitchBookListener, store: PitchBookStore, mock_client: AsyncMock
) -> None:
    """Status change should be detected between polls."""
    # Seed initial state
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE)
    )
    store.add_watched_company("C-1", "Acme")

    # API returns updated status
    mock_client.get_company.return_value = Company(
        pitchbook_id="C-1", name="Acme", status=CompanyStatus.PUBLIC
    )
    mock_client.get_company_deals.return_value = []

    changes: list[ChangeEvent] = []
    listener.on_change(lambda ev: changes.append(ev))
    await listener._poll_cycle()

    status_changes = [c for c in changes if c.change_type == "status_change"]
    assert len(status_changes) == 1
    assert "public" in status_changes[0].summary.lower()


@pytest.mark.asyncio
async def test_detect_new_deal(
    listener: PitchBookListener, store: PitchBookStore, mock_client: AsyncMock
) -> None:
    """New deal from API that isn't in store should emit new_deal event."""
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE)
    )
    store.add_watched_company("C-1", "Acme")

    mock_client.get_company.return_value = Company(
        pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE
    )
    mock_client.get_company_deals.return_value = [
        Deal(
            pitchbook_id="D-1",
            company_id="C-1",
            deal_type=DealType.SERIES_A,
            deal_size_usd=20_000_000.0,
        )
    ]

    changes: list[ChangeEvent] = []
    listener.on_change(lambda ev: changes.append(ev))
    await listener._poll_cycle()

    deal_changes = [c for c in changes if c.change_type == "new_deal"]
    assert len(deal_changes) == 1
    assert "$20,000,000" in deal_changes[0].summary


@pytest.mark.asyncio
async def test_existing_deal_not_re_emitted(
    listener: PitchBookListener, store: PitchBookStore, mock_client: AsyncMock
) -> None:
    """Deals already in the store should not trigger new events."""
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE)
    )
    store.upsert_deal(Deal(pitchbook_id="D-1", company_id="C-1", deal_type=DealType.SEED))
    store.add_watched_company("C-1", "Acme")

    mock_client.get_company.return_value = Company(
        pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE
    )
    mock_client.get_company_deals.return_value = [
        Deal(pitchbook_id="D-1", company_id="C-1", deal_type=DealType.SEED)
    ]

    changes: list[ChangeEvent] = []
    listener.on_change(lambda ev: changes.append(ev))
    await listener._poll_cycle()

    deal_changes = [c for c in changes if c.change_type == "new_deal"]
    assert len(deal_changes) == 0
