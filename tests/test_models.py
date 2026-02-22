"""Tests for data models."""

from __future__ import annotations

from datetime import date, datetime

from pitchbook.models import (
    ChangeEvent,
    Company,
    CompanyStatus,
    Deal,
    DealType,
    Fund,
    Investor,
    Person,
    QueryResult,
)


def test_company_defaults() -> None:
    c = Company(pitchbook_id="C-1", name="Acme Inc")
    assert c.status == CompanyStatus.ACTIVE
    assert c.description == ""
    assert c.total_raised_usd is None
    assert isinstance(c.fetched_at, datetime)


def test_company_full() -> None:
    c = Company(
        pitchbook_id="C-2",
        name="BigCo",
        status=CompanyStatus.PUBLIC,
        founded_date=date(2010, 1, 1),
        employee_count=5000,
        total_raised_usd=100_000_000.0,
        primary_industry="Software",
        hq_location="San Francisco, CA",
    )
    assert c.status == CompanyStatus.PUBLIC
    assert c.employee_count == 5000
    assert c.total_raised_usd == 100_000_000.0


def test_deal_defaults() -> None:
    d = Deal(pitchbook_id="D-1", company_id="C-1")
    assert d.deal_type == DealType.OTHER
    assert d.lead_investors == []
    assert d.deal_size_usd is None


def test_deal_serialization() -> None:
    d = Deal(
        pitchbook_id="D-2",
        company_id="C-1",
        deal_type=DealType.SERIES_A,
        deal_size_usd=50_000_000.0,
        lead_investors=["Sequoia", "a16z"],
    )
    data = d.model_dump(mode="json")
    assert data["deal_type"] == "series_a"
    assert data["lead_investors"] == ["Sequoia", "a16z"]


def test_investor_defaults() -> None:
    inv = Investor(pitchbook_id="I-1", name="Big VC")
    assert inv.investor_type == ""
    assert inv.notable_investments == []


def test_fund_defaults() -> None:
    f = Fund(pitchbook_id="F-1", name="Fund I", investor_id="I-1")
    assert f.fund_size_usd is None
    assert f.vintage_year is None


def test_person_defaults() -> None:
    p = Person(pitchbook_id="P-1", name="Jane Doe")
    assert p.title == ""
    assert p.company_id == ""


def test_change_event() -> None:
    ev = ChangeEvent(
        entity_type="company",
        entity_id="C-1",
        entity_name="Acme",
        change_type="status_change",
        summary="Acme went public",
        details={"old": "active", "new": "public"},
    )
    assert ev.change_type == "status_change"
    assert ev.details["old"] == "active"


def test_query_result() -> None:
    qr = QueryResult(
        query="What is Acme's valuation?",
        answer="Acme was last valued at $1B.",
        sources=["C-1", "D-2"],
        companies_referenced=["Acme"],
    )
    assert len(qr.sources) == 2
    assert qr.raw_data == {}
