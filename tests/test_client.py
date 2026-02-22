"""Tests for the PitchBook API client."""

from __future__ import annotations

import httpx
import pytest
import respx

from pitchbook.client import PitchBookAPIError, PitchBookClient, _parse_company, _parse_deal
from pitchbook.config import Settings
from pitchbook.models import CompanyStatus, DealType


@pytest.fixture()
def client_settings(tmp_path) -> Settings:
    return Settings(
        api_key="test-key",
        api_base_url="https://api.pitchbook.com/v2",
        db_path=tmp_path / "test.db",
        anthropic_api_key="test",
    )


@pytest.fixture()
def client(client_settings: Settings) -> PitchBookClient:
    return PitchBookClient(client_settings)


# ---------------------------------------------------------------------------
# Parser unit tests (no network)
# ---------------------------------------------------------------------------


def test_parse_company_minimal() -> None:
    raw = {"companyId": "C-1", "companyName": "Acme"}
    company = _parse_company(raw)
    assert company.pitchbook_id == "C-1"
    assert company.name == "Acme"
    assert company.status == CompanyStatus.ACTIVE


def test_parse_company_full() -> None:
    raw = {
        "companyId": "C-2",
        "companyName": "BigCo",
        "description": "A big company",
        "businessStatus": "Went Public",
        "website": "https://bigco.com",
        "employees": 10000,
        "primaryIndustryCode": "Software",
        "primarySector": "Technology",
        "hqLocation": "NYC",
        "totalRaised": 500_000_000.0,
    }
    company = _parse_company(raw)
    assert company.status == CompanyStatus.PUBLIC
    assert company.employee_count == 10000
    assert company.total_raised_usd == 500_000_000.0


def test_parse_deal_minimal() -> None:
    raw = {"dealId": "D-1"}
    deal = _parse_deal(raw, "C-1")
    assert deal.pitchbook_id == "D-1"
    assert deal.company_id == "C-1"
    assert deal.deal_type == DealType.OTHER


def test_parse_deal_series_a() -> None:
    raw = {
        "dealId": "D-2",
        "dealType": "Series A",
        "dealSize": 30_000_000.0,
        "dealDate": "2024-03-15",
        "leadInvestors": ["Sequoia"],
        "investors": ["Sequoia", "a16z"],
    }
    deal = _parse_deal(raw, "C-1")
    assert deal.deal_type == DealType.SERIES_A
    assert deal.deal_size_usd == 30_000_000.0
    assert deal.lead_investors == ["Sequoia"]


# ---------------------------------------------------------------------------
# Client integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_search_companies(client: PitchBookClient) -> None:
    respx.get("https://api.pitchbook.com/v2/companies/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"companyId": "C-1", "companyName": "Acme Corp"},
                    {"companyId": "C-2", "companyName": "Acme Labs"},
                ]
            },
        )
    )
    results = await client.search_companies("Acme")
    assert len(results) == 2
    assert results[0].name == "Acme Corp"
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_get_company(client: PitchBookClient) -> None:
    respx.get("https://api.pitchbook.com/v2/companies/C-1").mock(
        return_value=httpx.Response(
            200,
            json={"companyId": "C-1", "companyName": "Acme", "employees": 500},
        )
    )
    company = await client.get_company("C-1")
    assert company.pitchbook_id == "C-1"
    assert company.employee_count == 500
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_get_company_deals(client: PitchBookClient) -> None:
    respx.get("https://api.pitchbook.com/v2/companies/C-1/deals").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"dealId": "D-1", "dealType": "Seed Round", "dealSize": 5_000_000.0},
                ]
            },
        )
    )
    deals = await client.get_company_deals("C-1")
    assert len(deals) == 1
    assert deals[0].deal_type == DealType.SEED
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_api_error_raises(client: PitchBookClient) -> None:
    respx.get("https://api.pitchbook.com/v2/companies/bad-id").mock(
        return_value=httpx.Response(404, text="Not found")
    )
    with pytest.raises(PitchBookAPIError) as exc_info:
        await client.get_company("bad-id")
    assert exc_info.value.status_code == 404
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_search_investors(client: PitchBookClient) -> None:
    respx.get("https://api.pitchbook.com/v2/investors/search").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"investorId": "I-1", "investorName": "Big VC"}]},
        )
    )
    results = await client.search_investors("Big")
    assert len(results) == 1
    assert results[0].name == "Big VC"
    await client.close()
