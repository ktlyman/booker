"""Tests for the agent query interface (tool dispatch logic)."""

from __future__ import annotations

from pitchbook.agent_interface import PitchBookAgentInterface
from pitchbook.config import Settings
from pitchbook.models import (
    ChangeEvent,
    Company,
    CompanyStatus,
    Deal,
    DealType,
    Investor,
    Person,
)
from pitchbook.store import PitchBookStore


def test_execute_tool_search_companies(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-1", name="Acme Corp"))
    store.upsert_company(Company(pitchbook_id="C-2", name="Acme Labs"))

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("search_companies", {"query": "Acme"})

    assert len(result["companies"]) == 2
    assert "C-1" in result["_sources"]


def test_execute_tool_get_company_details(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_company(
        Company(
            pitchbook_id="C-1",
            name="Acme",
            total_raised_usd=50_000_000.0,
            primary_industry="SaaS",
        )
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("get_company_details", {"company_id": "C-1"})

    assert result["company"]["name"] == "Acme"
    assert result["company"]["total_raised_usd"] == 50_000_000.0


def test_execute_tool_get_company_details_missing(
    settings: Settings, store: PitchBookStore
) -> None:
    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("get_company_details", {"company_id": "missing"})
    assert "error" in result


def test_execute_tool_get_company_deals(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_deal(
        Deal(
            pitchbook_id="D-1",
            company_id="C-1",
            deal_type=DealType.SERIES_A,
            deal_size_usd=10_000_000.0,
        )
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("get_company_deals", {"company_id": "C-1"})
    assert len(result["deals"]) == 1


def test_execute_tool_search_investors(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_investor(Investor(pitchbook_id="I-1", name="Sequoia Capital"))

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("search_investors", {"query": "Sequoia"})
    assert len(result["investors"]) == 1


def test_execute_tool_get_company_people(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_person(
        Person(pitchbook_id="P-1", name="Jane", title="CEO", company_id="C-1")
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("get_company_people", {"company_id": "C-1"})
    assert len(result["people"]) == 1


def test_execute_tool_get_recent_changes(settings: Settings, store: PitchBookStore) -> None:
    store.record_change(
        ChangeEvent(
            entity_type="company",
            entity_id="C-1",
            entity_name="Acme",
            change_type="status_change",
            summary="Acme went public",
        )
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("get_recent_changes", {"limit": 10})
    assert len(result["changes"]) == 1


def test_execute_tool_full_text_search(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Stripe", primary_industry="Fintech")
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("full_text_search", {"query": "Stripe"})
    assert len(result["companies"]) == 1


def test_execute_tool_list_watched(settings: Settings, store: PitchBookStore) -> None:
    store.add_watched_company("C-1", "Acme")
    store.add_watched_company("C-2", "BigCo")

    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("list_watched_companies", {})
    assert len(result["watched"]) == 2


def test_execute_tool_unknown(settings: Settings, store: PitchBookStore) -> None:
    interface = PitchBookAgentInterface(settings=settings, store=store)
    result = interface._execute_tool("nonexistent_tool", {})
    assert "error" in result


def test_get_company_summary(settings: Settings, store: PitchBookStore) -> None:
    store.upsert_company(
        Company(
            pitchbook_id="C-1",
            name="Acme",
            status=CompanyStatus.ACTIVE,
            primary_industry="SaaS",
            total_raised_usd=100_000_000.0,
            employee_count=500,
            hq_location="SF",
            website="https://acme.com",
        )
    )
    store.upsert_deal(
        Deal(
            pitchbook_id="D-1",
            company_id="C-1",
            deal_type=DealType.SERIES_B,
            deal_size_usd=50_000_000.0,
            deal_date="2024-01-15",
        )
    )
    store.upsert_person(
        Person(pitchbook_id="P-1", name="Jane", title="CEO", company_id="C-1")
    )

    interface = PitchBookAgentInterface(settings=settings, store=store)
    summary = interface.get_company_summary("C-1")

    assert summary is not None
    assert "Acme" in summary
    assert "$100,000,000" in summary
    assert "series_b" in summary
    assert "Jane" in summary


def test_get_company_summary_missing(settings: Settings, store: PitchBookStore) -> None:
    interface = PitchBookAgentInterface(settings=settings, store=store)
    assert interface.get_company_summary("missing") is None
