"""Tests for the local SQLite data store."""

from __future__ import annotations

from datetime import date

from pitchbook.models import ChangeEvent, Company, CompanyStatus, Deal, DealType, Investor, Person
from pitchbook.store import PitchBookStore


def test_upsert_and_get_company(store: PitchBookStore) -> None:
    company = Company(
        pitchbook_id="C-100",
        name="TestCo",
        status=CompanyStatus.ACTIVE,
        primary_industry="SaaS",
        total_raised_usd=10_000_000.0,
    )
    store.upsert_company(company)
    result = store.get_company("C-100")
    assert result is not None
    assert result.name == "TestCo"
    assert result.total_raised_usd == 10_000_000.0


def test_upsert_updates_existing(store: PitchBookStore) -> None:
    store.upsert_company(
        Company(pitchbook_id="C-200", name="OldName", total_raised_usd=1_000_000.0)
    )
    store.upsert_company(
        Company(pitchbook_id="C-200", name="NewName", total_raised_usd=5_000_000.0)
    )
    result = store.get_company("C-200")
    assert result is not None
    assert result.name == "NewName"
    assert result.total_raised_usd == 5_000_000.0


def test_search_companies(store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-1", name="Anthropic"))
    store.upsert_company(Company(pitchbook_id="C-2", name="OpenAI"))
    store.upsert_company(Company(pitchbook_id="C-3", name="Anthropic Labs"))

    results = store.search_companies("Anthropic")
    assert len(results) == 2
    names = {r.name for r in results}
    assert "Anthropic" in names
    assert "Anthropic Labs" in names


def test_list_companies(store: PitchBookStore) -> None:
    store.upsert_company(Company(pitchbook_id="C-A", name="Alpha"))
    store.upsert_company(Company(pitchbook_id="C-B", name="Beta"))
    result = store.list_companies()
    assert len(result) == 2
    assert result[0].name == "Alpha"  # sorted by name


def test_upsert_and_get_deals(store: PitchBookStore) -> None:
    deal = Deal(
        pitchbook_id="D-1",
        company_id="C-1",
        deal_type=DealType.SERIES_A,
        deal_size_usd=25_000_000.0,
        lead_investors=["Sequoia"],
        deal_date=date(2024, 6, 15),
    )
    store.upsert_deal(deal)
    deals = store.get_deals_for_company("C-1")
    assert len(deals) == 1
    assert deals[0].deal_type == DealType.SERIES_A
    assert deals[0].lead_investors == ["Sequoia"]


def test_upsert_investor_and_search(store: PitchBookStore) -> None:
    store.upsert_investor(Investor(pitchbook_id="I-1", name="Sequoia Capital"))
    store.upsert_investor(Investor(pitchbook_id="I-2", name="Accel Partners"))
    results = store.search_investors("Sequoia")
    assert len(results) == 1
    assert results[0].name == "Sequoia Capital"


def test_upsert_person_and_get_for_company(store: PitchBookStore) -> None:
    store.upsert_person(
        Person(pitchbook_id="P-1", name="Jane CEO", title="CEO", company_id="C-1")
    )
    store.upsert_person(
        Person(pitchbook_id="P-2", name="Bob CTO", title="CTO", company_id="C-1")
    )
    people = store.get_people_for_company("C-1")
    assert len(people) == 2


def test_change_events(store: PitchBookStore) -> None:
    store.record_change(
        ChangeEvent(
            entity_type="company",
            entity_id="C-1",
            entity_name="TestCo",
            change_type="status_change",
            summary="TestCo went public",
        )
    )
    store.record_change(
        ChangeEvent(
            entity_type="deal",
            entity_id="D-1",
            entity_name="TestCo",
            change_type="new_deal",
            summary="New Series B for TestCo",
        )
    )
    changes = store.get_recent_changes(limit=10)
    assert len(changes) == 2
    assert changes[0].change_type == "new_deal"  # most recent first


def test_watched_companies(store: PitchBookStore) -> None:
    store.add_watched_company("C-1", "Acme")
    store.add_watched_company("C-2", "BigCo")
    watched = store.list_watched_companies()
    assert len(watched) == 2

    store.remove_watched_company("C-1")
    watched = store.list_watched_companies()
    assert len(watched) == 1
    assert watched[0][0] == "C-2"


def test_add_watched_company_idempotent(store: PitchBookStore) -> None:
    store.add_watched_company("C-1", "Acme")
    store.add_watched_company("C-1", "Acme")  # should not raise
    watched = store.list_watched_companies()
    assert len(watched) == 1


def test_full_text_search(store: PitchBookStore) -> None:
    store.upsert_company(
        Company(pitchbook_id="C-1", name="Stripe", primary_industry="Fintech")
    )
    store.upsert_investor(Investor(pitchbook_id="I-1", name="Stripe Ventures"))
    results = store.full_text_search("Stripe")
    assert len(results["companies"]) == 1
    assert len(results["investors"]) == 1
