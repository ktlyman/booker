"""Tests for the FastAPI web server and REST API."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from pitchbook.models import (
    ChangeEvent,
    Company,
    Deal,
    DealType,
    Investor,
    Person,
)
from pitchbook.store import PitchBookStore
from pitchbook.web import create_app


@pytest.fixture()
def app(store: PitchBookStore) -> object:
    return create_app(store=store)


@pytest.fixture()
async def client(app: object) -> AsyncClient:
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_returns_html(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "PitchBook Dashboard" in resp.text
    assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Companies API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_companies_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/companies")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_companies(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-1", name="Acme"))
    store.upsert_company(Company(pitchbook_id="C-2", name="BigCo"))
    resp = await client.get("/api/companies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "Acme"


@pytest.mark.asyncio
async def test_search_companies(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-1", name="Anthropic"))
    store.upsert_company(Company(pitchbook_id="C-2", name="OpenAI"))
    resp = await client.get("/api/companies/search", params={"q": "Anthropic"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Anthropic"


@pytest.mark.asyncio
async def test_search_companies_requires_query(client: AsyncClient) -> None:
    resp = await client.get("/api/companies/search")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_company(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Acme", total_raised_usd=50_000_000.0)
    )
    resp = await client.get("/api/companies/C-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Acme"
    assert data["total_raised_usd"] == 50_000_000.0


@pytest.mark.asyncio
async def test_get_company_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/companies/missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_company_deals(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_deal(
        Deal(pitchbook_id="D-1", company_id="C-1", deal_type=DealType.SERIES_A)
    )
    resp = await client.get("/api/companies/C-1/deals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["deal_type"] == "series_a"


@pytest.mark.asyncio
async def test_get_company_people(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_person(
        Person(pitchbook_id="P-1", name="Jane", title="CEO", company_id="C-1")
    )
    resp = await client.get("/api/companies/C-1/people")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Jane"


# ---------------------------------------------------------------------------
# Investors API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_investors(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_investor(Investor(pitchbook_id="I-1", name="Sequoia Capital"))
    resp = await client.get("/api/investors/search", params={"q": "Sequoia"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Sequoia Capital"


# ---------------------------------------------------------------------------
# Watch List API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_list_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/watched")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_add_watched(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/watched",
        json={"pitchbook_id": "C-1", "name": "Acme"},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "added"

    # Verify it shows up in the list
    resp = await client.get("/api/watched")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["pitchbook_id"] == "C-1"


@pytest.mark.asyncio
async def test_remove_watched(client: AsyncClient, store: PitchBookStore) -> None:
    store.add_watched_company("C-1", "Acme")
    resp = await client.delete("/api/watched/C-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    resp = await client.get("/api/watched")
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Changes API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_changes(client: AsyncClient, store: PitchBookStore) -> None:
    store.record_change(
        ChangeEvent(
            entity_type="company",
            entity_id="C-1",
            entity_name="Acme",
            change_type="status_change",
            summary="Acme went public",
        )
    )
    resp = await client.get("/api/changes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["summary"] == "Acme went public"


@pytest.mark.asyncio
async def test_list_changes_with_limit(client: AsyncClient, store: PitchBookStore) -> None:
    for i in range(5):
        store.record_change(
            ChangeEvent(
                entity_type="company",
                entity_id=f"C-{i}",
                entity_name=f"Co{i}",
                change_type="update",
                summary=f"Update {i}",
            )
        )
    resp = await client.get("/api/changes", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_text_search(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Stripe", primary_industry="Fintech")
    )
    store.upsert_investor(Investor(pitchbook_id="I-1", name="Stripe Ventures"))
    resp = await client.get("/api/search", params={"q": "Stripe"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["companies"]) == 1
    assert len(data["investors"]) == 1


# ---------------------------------------------------------------------------
# Status API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status(client: AsyncClient, store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-1", name="Acme"))
    store.add_watched_company("C-1", "Acme")
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["companies_stored"] == 1
    assert data["companies_watched"] == 1
