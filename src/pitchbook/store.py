"""SQLite-backed local data store for PitchBook entities.

Provides persistent caching of company, deal, investor, fund, and person data
so the agent interface can query locally without hitting the API on every request.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from pitchbook.models import ChangeEvent, Company, Deal, Fund, Investor, Person

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "companies"

    pitchbook_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, default="")
    status = Column(String, default="active")
    website = Column(String, default="")
    founded_date = Column(String, nullable=True)
    employee_count = Column(Integer, nullable=True)
    primary_industry = Column(String, default="")
    primary_sector = Column(String, default="")
    hq_location = Column(String, default="")
    total_raised_usd = Column(Float, nullable=True)
    last_financing_date = Column(String, nullable=True)
    last_financing_deal_type = Column(String, default="")
    last_financing_size_usd = Column(Float, nullable=True)
    ownership_status = Column(String, default="")
    fetched_at = Column(DateTime, default=datetime.utcnow)
    raw_json = Column(Text, default="{}")


class DealRow(Base):
    __tablename__ = "deals"

    pitchbook_id = Column(String, primary_key=True)
    company_id = Column(String, index=True, nullable=False)
    deal_type = Column(String, default="other")
    deal_date = Column(String, nullable=True)
    deal_size_usd = Column(Float, nullable=True)
    pre_money_valuation_usd = Column(Float, nullable=True)
    post_money_valuation_usd = Column(Float, nullable=True)
    lead_investors = Column(Text, default="[]")
    all_investors = Column(Text, default="[]")
    deal_status = Column(String, default="")
    deal_synopsis = Column(Text, default="")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class InvestorRow(Base):
    __tablename__ = "investors"

    pitchbook_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, index=True)
    investor_type = Column(String, default="")
    description = Column(Text, default="")
    website = Column(String, default="")
    hq_location = Column(String, default="")
    assets_under_management_usd = Column(Float, nullable=True)
    total_investments = Column(Integer, nullable=True)
    notable_investments = Column(Text, default="[]")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class FundRow(Base):
    __tablename__ = "funds"

    pitchbook_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, index=True)
    investor_id = Column(String, index=True)
    fund_size_usd = Column(Float, nullable=True)
    vintage_year = Column(Integer, nullable=True)
    fund_type = Column(String, default="")
    status = Column(String, default="")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class PersonRow(Base):
    __tablename__ = "people"

    pitchbook_id = Column(String, primary_key=True)
    name = Column(String, nullable=False, index=True)
    title = Column(String, default="")
    company_id = Column(String, index=True, default="")
    company_name = Column(String, default="")
    bio = Column(Text, default="")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class ChangeEventRow(Base):
    __tablename__ = "change_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, nullable=False, index=True)
    entity_name = Column(String, default="")
    change_type = Column(String, nullable=False)
    summary = Column(Text, default="")
    detected_at = Column(DateTime, default=datetime.utcnow)
    details_json = Column(Text, default="{}")


class WatchedCompanyRow(Base):
    """Tracks which companies the listener should monitor."""

    __tablename__ = "watched_companies"

    pitchbook_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class PitchBookStore:
    """Manages the local SQLite database of PitchBook data."""

    def __init__(self, db_path: Path | str = "pitchbook_data.db") -> None:
        url = f"sqlite:///{db_path}"
        self._engine = create_engine(url, echo=False)
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)

    def _session(self) -> Session:
        return self._session_factory()

    # ---- Companies --------------------------------------------------------

    def upsert_company(self, company: Company) -> None:
        with self._session() as session:
            row = session.get(CompanyRow, company.pitchbook_id)
            data = company.model_dump()
            data["raw_json"] = json.dumps(data, default=str)
            if row:
                for key, value in data.items():
                    if key != "pitchbook_id" and hasattr(row, key):
                        v = json.dumps(value) if isinstance(value, list) else value
                        setattr(row, key, v)
            else:
                for key in list(data.keys()):
                    if isinstance(data[key], list):
                        data[key] = json.dumps(data[key])
                session.add(CompanyRow(**{k: v for k, v in data.items() if hasattr(CompanyRow, k)}))
            session.commit()

    def get_company(self, pitchbook_id: str) -> Company | None:
        with self._session() as session:
            row = session.get(CompanyRow, pitchbook_id)
            if not row:
                return None
            return Company(
                pitchbook_id=row.pitchbook_id,
                name=row.name,
                description=row.description or "",
                status=row.status or "active",
                website=row.website or "",
                founded_date=row.founded_date,
                employee_count=row.employee_count,
                primary_industry=row.primary_industry or "",
                primary_sector=row.primary_sector or "",
                hq_location=row.hq_location or "",
                total_raised_usd=row.total_raised_usd,
                last_financing_date=row.last_financing_date,
                last_financing_deal_type=row.last_financing_deal_type or "",
                last_financing_size_usd=row.last_financing_size_usd,
                ownership_status=row.ownership_status or "",
                fetched_at=row.fetched_at or datetime.utcnow(),
            )

    def search_companies(self, query: str) -> list[Company]:
        with self._session() as session:
            rows = (
                session.query(CompanyRow)
                .filter(CompanyRow.name.ilike(f"%{query}%"))
                .limit(50)
                .all()
            )
            return [self._row_to_company(r) for r in rows]

    def list_companies(self) -> list[Company]:
        with self._session() as session:
            rows = session.query(CompanyRow).order_by(CompanyRow.name).all()
            return [self._row_to_company(r) for r in rows]

    @staticmethod
    def _row_to_company(row: CompanyRow) -> Company:
        return Company(
            pitchbook_id=row.pitchbook_id,
            name=row.name,
            description=row.description or "",
            status=row.status or "active",
            website=row.website or "",
            founded_date=row.founded_date,
            employee_count=row.employee_count,
            primary_industry=row.primary_industry or "",
            primary_sector=row.primary_sector or "",
            hq_location=row.hq_location or "",
            total_raised_usd=row.total_raised_usd,
            last_financing_date=row.last_financing_date,
            last_financing_deal_type=row.last_financing_deal_type or "",
            last_financing_size_usd=row.last_financing_size_usd,
            ownership_status=row.ownership_status or "",
            fetched_at=row.fetched_at or datetime.utcnow(),
        )

    # ---- Deals ------------------------------------------------------------

    def upsert_deal(self, deal: Deal) -> None:
        with self._session() as session:
            row = session.get(DealRow, deal.pitchbook_id)
            data = deal.model_dump()
            data["lead_investors"] = json.dumps(data["lead_investors"])
            data["all_investors"] = json.dumps(data["all_investors"])
            if row:
                for key, value in data.items():
                    if key != "pitchbook_id" and hasattr(row, key):
                        setattr(row, key, value)
            else:
                session.add(DealRow(**{k: v for k, v in data.items() if hasattr(DealRow, k)}))
            session.commit()

    def get_deals_for_company(self, company_id: str) -> list[Deal]:
        with self._session() as session:
            rows = (
                session.query(DealRow)
                .filter(DealRow.company_id == company_id)
                .order_by(DealRow.deal_date.desc())
                .all()
            )
            return [self._row_to_deal(r) for r in rows]

    @staticmethod
    def _row_to_deal(row: DealRow) -> Deal:
        return Deal(
            pitchbook_id=row.pitchbook_id,
            company_id=row.company_id,
            deal_type=row.deal_type or "other",
            deal_date=row.deal_date,
            deal_size_usd=row.deal_size_usd,
            pre_money_valuation_usd=row.pre_money_valuation_usd,
            post_money_valuation_usd=row.post_money_valuation_usd,
            lead_investors=json.loads(row.lead_investors) if row.lead_investors else [],
            all_investors=json.loads(row.all_investors) if row.all_investors else [],
            deal_status=row.deal_status or "",
            deal_synopsis=row.deal_synopsis or "",
            fetched_at=row.fetched_at or datetime.utcnow(),
        )

    # ---- Investors --------------------------------------------------------

    def upsert_investor(self, investor: Investor) -> None:
        with self._session() as session:
            row = session.get(InvestorRow, investor.pitchbook_id)
            data = investor.model_dump()
            data["notable_investments"] = json.dumps(data["notable_investments"])
            if row:
                for key, value in data.items():
                    if key != "pitchbook_id" and hasattr(row, key):
                        setattr(row, key, value)
            else:
                session.add(
                    InvestorRow(**{k: v for k, v in data.items() if hasattr(InvestorRow, k)})
                )
            session.commit()

    def search_investors(self, query: str) -> list[Investor]:
        with self._session() as session:
            rows = (
                session.query(InvestorRow)
                .filter(InvestorRow.name.ilike(f"%{query}%"))
                .limit(50)
                .all()
            )
            return [self._row_to_investor(r) for r in rows]

    @staticmethod
    def _row_to_investor(row: InvestorRow) -> Investor:
        return Investor(
            pitchbook_id=row.pitchbook_id,
            name=row.name,
            investor_type=row.investor_type or "",
            description=row.description or "",
            website=row.website or "",
            hq_location=row.hq_location or "",
            assets_under_management_usd=row.assets_under_management_usd,
            total_investments=row.total_investments,
            notable_investments=json.loads(row.notable_investments)
            if row.notable_investments
            else [],
            fetched_at=row.fetched_at or datetime.utcnow(),
        )

    # ---- Funds ------------------------------------------------------------

    def upsert_fund(self, fund: Fund) -> None:
        with self._session() as session:
            row = session.get(FundRow, fund.pitchbook_id)
            data = fund.model_dump()
            if row:
                for key, value in data.items():
                    if key != "pitchbook_id" and hasattr(row, key):
                        setattr(row, key, value)
            else:
                session.add(FundRow(**{k: v for k, v in data.items() if hasattr(FundRow, k)}))
            session.commit()

    # ---- People -----------------------------------------------------------

    def upsert_person(self, person: Person) -> None:
        with self._session() as session:
            row = session.get(PersonRow, person.pitchbook_id)
            data = person.model_dump()
            if row:
                for key, value in data.items():
                    if key != "pitchbook_id" and hasattr(row, key):
                        setattr(row, key, value)
            else:
                session.add(PersonRow(**{k: v for k, v in data.items() if hasattr(PersonRow, k)}))
            session.commit()

    def get_people_for_company(self, company_id: str) -> list[Person]:
        with self._session() as session:
            rows = (
                session.query(PersonRow)
                .filter(PersonRow.company_id == company_id)
                .all()
            )
            return [
                Person(
                    pitchbook_id=r.pitchbook_id,
                    name=r.name,
                    title=r.title or "",
                    company_id=r.company_id or "",
                    company_name=r.company_name or "",
                    bio=r.bio or "",
                    fetched_at=r.fetched_at or datetime.utcnow(),
                )
                for r in rows
            ]

    # ---- Change events ----------------------------------------------------

    def record_change(self, event: ChangeEvent) -> None:
        with self._session() as session:
            session.add(
                ChangeEventRow(
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    entity_name=event.entity_name,
                    change_type=event.change_type,
                    summary=event.summary,
                    detected_at=event.detected_at,
                    details_json=json.dumps(event.details, default=str),
                )
            )
            session.commit()

    def get_recent_changes(self, limit: int = 100) -> list[ChangeEvent]:
        with self._session() as session:
            rows = (
                session.query(ChangeEventRow)
                .order_by(ChangeEventRow.detected_at.desc())
                .limit(limit)
                .all()
            )
            return [
                ChangeEvent(
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    entity_name=r.entity_name,
                    change_type=r.change_type,
                    summary=r.summary,
                    detected_at=r.detected_at,
                    details=json.loads(r.details_json) if r.details_json else {},
                )
                for r in rows
            ]

    # ---- Watched companies ------------------------------------------------

    def add_watched_company(self, pitchbook_id: str, name: str) -> None:
        with self._session() as session:
            existing = session.get(WatchedCompanyRow, pitchbook_id)
            if not existing:
                session.add(WatchedCompanyRow(pitchbook_id=pitchbook_id, name=name))
                session.commit()

    def remove_watched_company(self, pitchbook_id: str) -> None:
        with self._session() as session:
            row = session.get(WatchedCompanyRow, pitchbook_id)
            if row:
                session.delete(row)
                session.commit()

    def list_watched_companies(self) -> list[tuple[str, str]]:
        with self._session() as session:
            rows = session.query(WatchedCompanyRow).all()
            return [(r.pitchbook_id, r.name) for r in rows]

    # ---- Full-text search across all entities ----------------------------

    def full_text_search(self, query: str) -> dict[str, list[dict[str, str]]]:
        """Search across all entity tables and return matching summaries."""
        results: dict[str, list[dict[str, str]]] = {
            "companies": [],
            "deals": [],
            "investors": [],
            "people": [],
        }
        q = f"%{query}%"
        with self._session() as session:
            for row in session.query(CompanyRow).filter(
                (CompanyRow.name.ilike(q))
                | (CompanyRow.description.ilike(q))
                | (CompanyRow.primary_industry.ilike(q))
            ).limit(20):
                results["companies"].append(
                    {"id": row.pitchbook_id, "name": row.name, "industry": row.primary_industry}
                )
            for row in session.query(DealRow).filter(
                DealRow.deal_synopsis.ilike(q)
            ).limit(20):
                results["deals"].append(
                    {"id": row.pitchbook_id, "company_id": row.company_id, "type": row.deal_type}
                )
            for row in session.query(InvestorRow).filter(
                (InvestorRow.name.ilike(q)) | (InvestorRow.description.ilike(q))
            ).limit(20):
                results["investors"].append(
                    {"id": row.pitchbook_id, "name": row.name, "type": row.investor_type}
                )
            for row in session.query(PersonRow).filter(
                (PersonRow.name.ilike(q)) | (PersonRow.title.ilike(q))
            ).limit(20):
                results["people"].append(
                    {"id": row.pitchbook_id, "name": row.name, "title": row.title}
                )
        return results
