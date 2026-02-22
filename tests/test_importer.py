"""Tests for the historical data importer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pitchbook.config import Settings
from pitchbook.importer import PitchBookImporter
from pitchbook.models import Company, CompanyStatus, Deal, DealType, Investor, Person
from pitchbook.store import PitchBookStore


@pytest.fixture()
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture()
def importer(
    settings: Settings, store: PitchBookStore, mock_client: AsyncMock
) -> PitchBookImporter:
    return PitchBookImporter(settings=settings, store=store, client=mock_client)


@pytest.mark.asyncio
async def test_import_companies_by_name(
    importer: PitchBookImporter, mock_client: AsyncMock, store: PitchBookStore
) -> None:
    mock_client.search_companies.return_value = [
        Company(pitchbook_id="C-1", name="Acme Corp", status=CompanyStatus.ACTIVE)
    ]
    mock_client.get_company_deals.return_value = [
        Deal(pitchbook_id="D-1", company_id="C-1", deal_type=DealType.SEED)
    ]
    mock_client.get_company_investors.return_value = [
        Investor(pitchbook_id="I-1", name="Big VC")
    ]
    mock_client.get_company_people.return_value = [
        Person(pitchbook_id="P-1", name="Jane CEO", title="CEO", company_id="C-1")
    ]

    stats = await importer.import_companies(["Acme Corp"])

    assert stats.companies == 1
    assert stats.deals == 1
    assert stats.investors == 1
    assert stats.people == 1
    assert stats.errors == []

    # Verify stored
    assert store.get_company("C-1") is not None
    assert len(store.get_deals_for_company("C-1")) == 1

    # Should be in watched list
    watched = store.list_watched_companies()
    assert any(w[0] == "C-1" for w in watched)


@pytest.mark.asyncio
async def test_import_by_ids(
    importer: PitchBookImporter, mock_client: AsyncMock, store: PitchBookStore
) -> None:
    mock_client.get_company.return_value = Company(
        pitchbook_id="C-99", name="IdCo", status=CompanyStatus.ACTIVE
    )
    mock_client.get_company_deals.return_value = []
    mock_client.get_company_investors.return_value = []
    mock_client.get_company_people.return_value = []

    stats = await importer.import_by_ids(["C-99"])

    assert stats.companies == 1
    assert store.get_company("C-99") is not None


@pytest.mark.asyncio
async def test_import_no_match(
    importer: PitchBookImporter, mock_client: AsyncMock
) -> None:
    mock_client.search_companies.return_value = []
    stats = await importer.import_companies(["NonexistentCo"])
    assert stats.companies == 0
    assert len(stats.errors) == 1
    assert "No PitchBook match" in stats.errors[0]


@pytest.mark.asyncio
async def test_import_no_watch(
    importer: PitchBookImporter, mock_client: AsyncMock, store: PitchBookStore
) -> None:
    mock_client.search_companies.return_value = [
        Company(pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE)
    ]
    mock_client.get_company_deals.return_value = []
    mock_client.get_company_investors.return_value = []
    mock_client.get_company_people.return_value = []

    await importer.import_companies(["Acme"], watch=False)
    watched = store.list_watched_companies()
    assert len(watched) == 0


@pytest.mark.asyncio
async def test_import_handles_api_error(
    importer: PitchBookImporter, mock_client: AsyncMock
) -> None:
    mock_client.search_companies.side_effect = Exception("API down")
    stats = await importer.import_companies(["Acme"])
    assert stats.companies == 0
    assert len(stats.errors) == 1
    assert "API down" in stats.errors[0]


@pytest.mark.asyncio
async def test_refresh_all(
    importer: PitchBookImporter, mock_client: AsyncMock, store: PitchBookStore
) -> None:
    # Seed an existing company
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Acme", status=CompanyStatus.ACTIVE)
    )
    mock_client.get_company_deals.return_value = []
    mock_client.get_company_investors.return_value = []
    mock_client.get_company_people.return_value = []

    stats = await importer.refresh_all()
    assert stats.companies == 1
