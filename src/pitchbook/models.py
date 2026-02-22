"""Data models for PitchBook entities."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CompanyStatus(StrEnum):
    ACTIVE = "active"
    ACQUIRED = "acquired"
    MERGED = "merged"
    INACTIVE = "inactive"
    PUBLIC = "public"


class DealType(StrEnum):
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C = "series_c"
    SERIES_D_PLUS = "series_d_plus"
    SEED = "seed"
    ANGEL = "angel"
    GRANT = "grant"
    DEBT = "debt"
    IPO = "ipo"
    MERGER_ACQUISITION = "merger_acquisition"
    BUYOUT = "buyout"
    SECONDARY = "secondary"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Core entity models — mirrors PitchBook API v2 response shapes
# ---------------------------------------------------------------------------

class Company(BaseModel):
    """A company tracked in PitchBook."""

    pitchbook_id: str = Field(description="PitchBook unique company identifier")
    name: str
    description: str = ""
    status: CompanyStatus = CompanyStatus.ACTIVE
    website: str = ""
    founded_date: date | None = None
    employee_count: int | None = None
    primary_industry: str = ""
    primary_sector: str = ""
    hq_location: str = ""
    total_raised_usd: float | None = None
    last_financing_date: date | None = None
    last_financing_deal_type: str = ""
    last_financing_size_usd: float | None = None
    ownership_status: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Deal(BaseModel):
    """A financing deal or transaction."""

    pitchbook_id: str = Field(description="PitchBook unique deal identifier")
    company_id: str = Field(description="PitchBook company identifier")
    deal_type: DealType = DealType.OTHER
    deal_date: date | None = None
    deal_size_usd: float | None = None
    pre_money_valuation_usd: float | None = None
    post_money_valuation_usd: float | None = None
    lead_investors: list[str] = Field(default_factory=list)
    all_investors: list[str] = Field(default_factory=list)
    deal_status: str = ""
    deal_synopsis: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Investor(BaseModel):
    """An investor entity."""

    pitchbook_id: str = Field(description="PitchBook unique investor identifier")
    name: str
    investor_type: str = ""
    description: str = ""
    website: str = ""
    hq_location: str = ""
    assets_under_management_usd: float | None = None
    total_investments: int | None = None
    notable_investments: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Fund(BaseModel):
    """An investment fund."""

    pitchbook_id: str = Field(description="PitchBook unique fund identifier")
    name: str
    investor_id: str = Field(description="PitchBook investor identifier")
    fund_size_usd: float | None = None
    vintage_year: int | None = None
    fund_type: str = ""
    status: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Person(BaseModel):
    """A person tracked in PitchBook (executive, board member, etc.)."""

    pitchbook_id: str
    name: str
    title: str = ""
    company_id: str = ""
    company_name: str = ""
    bio: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Listener / change-tracking models
# ---------------------------------------------------------------------------

class ChangeEvent(BaseModel):
    """Represents a detected change for a watched entity."""

    entity_type: str  # "company", "deal", "investor", etc.
    entity_id: str
    entity_name: str
    change_type: str  # "new", "updated", "new_deal", "status_change", etc.
    summary: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Query / agent interface models
# ---------------------------------------------------------------------------

class QueryResult(BaseModel):
    """Result returned by the agent query interface."""

    query: str
    answer: str
    sources: list[str] = Field(
        default_factory=list,
        description="PitchBook entity IDs used to produce the answer",
    )
    companies_referenced: list[str] = Field(default_factory=list)
    raw_data: dict[str, object] = Field(default_factory=dict)
