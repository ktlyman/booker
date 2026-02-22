"""Async HTTP client for the PitchBook API v2."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pitchbook.config import AuthMode, Settings
from pitchbook.models import Company, CompanyStatus, Deal, DealType, Fund, Investor, Person

logger = logging.getLogger(__name__)


class PitchBookAPIError(Exception):
    """Raised when the PitchBook API returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"PitchBook API error {status_code}: {detail}")


class PitchBookAuthError(PitchBookAPIError):
    """Raised when authentication fails (expired cookies, bad API key)."""

    def __init__(self, detail: str) -> None:
        super().__init__(401, detail)


class PitchBookClient:
    """Async client wrapping the PitchBook REST API v2.

    Handles authentication (API key or Chrome cookies), pagination,
    rate-limit back-off, and deserialization into Pydantic models.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._auth_mode = self._resolve_auth_mode()
        self._http = self._build_http_client()

    def _resolve_auth_mode(self) -> AuthMode:
        """Determine which auth mode to actually use."""
        mode = self._settings.auth_mode
        if mode == AuthMode.AUTO:
            if self._settings.api_key:
                logger.info("Auth mode: API key (auto-detected)")
                return AuthMode.API_KEY
            logger.info("Auth mode: Chrome cookies (no API key found)")
            return AuthMode.COOKIES
        return mode

    def _build_http_client(self) -> httpx.AsyncClient:
        """Construct the httpx client with appropriate auth config."""
        if self._auth_mode == AuthMode.API_KEY:
            return httpx.AsyncClient(
                base_url=self._settings.api_base_url,
                headers={
                    "Authorization": f"PitchBook {self._settings.api_key}",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(self._settings.api_timeout),
            )

        from pitchbook.cookies import cookies_to_httpx, extract_pitchbook_cookies

        cookie_dict = extract_pitchbook_cookies()
        cookies = cookies_to_httpx(cookie_dict)

        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }

        csrf = cookie_dict.get("csrftoken", "")
        if csrf:
            headers["X-CSRFToken"] = csrf

        return httpx.AsyncClient(
            base_url=self._settings.web_base_url,
            cookies=cookies,
            headers=headers,
            timeout=httpx.Timeout(self._settings.api_timeout),
            follow_redirects=True,
        )

    async def _refresh_cookies(self) -> None:
        """Re-extract cookies from Chrome and update the HTTP client."""
        from pitchbook.cookies import cookies_to_httpx, extract_pitchbook_cookies

        logger.info("Refreshing PitchBook cookies from Chrome...")
        cookie_dict = extract_pitchbook_cookies()
        self._http.cookies = cookies_to_httpx(cookie_dict)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PitchBookClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        reraise=True,
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = await self._http.request(method, path, **kwargs)

        # Handle auth failures with cookie refresh
        if resp.status_code in (401, 403) and self._auth_mode == AuthMode.COOKIES:
            logger.warning("Auth failed (HTTP %d), refreshing cookies...", resp.status_code)
            await self._refresh_cookies()
            resp = await self._http.request(method, path, **kwargs)
            if resp.status_code in (401, 403):
                raise PitchBookAuthError(
                    "Authentication failed after cookie refresh. "
                    "Please log into pitchbook.com in Chrome."
                )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited by PitchBook, retry after %ds", retry_after)
            raise httpx.TransportError(f"Rate limited, retry after {retry_after}s")
        if resp.status_code >= 400:
            raise PitchBookAPIError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None, max_pages: int = 50
    ) -> list[dict[str, Any]]:
        """Follow PitchBook pagination to collect all items."""
        params = dict(params or {})
        params.setdefault("limit", 100)
        all_items: list[dict[str, Any]] = []
        for _ in range(max_pages):
            data = await self._get(path, params=params)
            items = data.get("items", data.get("results", []))
            all_items.extend(items)
            next_cursor = data.get("nextCursor") or data.get("next")
            if not next_cursor or not items:
                break
            params["cursor"] = next_cursor
        return all_items

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    async def search_companies(
        self,
        query: str,
        *,
        limit: int = 25,
    ) -> list[Company]:
        data = await self._get("/companies/search", params={"q": query, "limit": limit})
        return [_parse_company(item) for item in data.get("items", data.get("results", []))]

    async def get_company(self, company_id: str) -> Company:
        data = await self._get(f"/companies/{company_id}")
        return _parse_company(data)

    async def get_company_deals(self, company_id: str) -> list[Deal]:
        items = await self._paginate(f"/companies/{company_id}/deals")
        return [_parse_deal(item, company_id) for item in items]

    async def get_company_investors(self, company_id: str) -> list[Investor]:
        items = await self._paginate(f"/companies/{company_id}/investors")
        return [_parse_investor(item) for item in items]

    async def get_company_people(self, company_id: str) -> list[Person]:
        items = await self._paginate(f"/companies/{company_id}/people")
        return [_parse_person(item) for item in items]

    # ------------------------------------------------------------------
    # Deals
    # ------------------------------------------------------------------

    async def get_deal(self, deal_id: str) -> Deal:
        data = await self._get(f"/deals/{deal_id}")
        return _parse_deal(data, data.get("companyId", ""))

    async def search_deals(
        self,
        *,
        company_id: str | None = None,
        deal_type: str | None = None,
        limit: int = 25,
    ) -> list[Deal]:
        params: dict[str, Any] = {"limit": limit}
        if company_id:
            params["companyId"] = company_id
        if deal_type:
            params["dealType"] = deal_type
        data = await self._get("/deals/search", params=params)
        return [
            _parse_deal(item, item.get("companyId", ""))
            for item in data.get("items", data.get("results", []))
        ]

    # ------------------------------------------------------------------
    # Investors
    # ------------------------------------------------------------------

    async def get_investor(self, investor_id: str) -> Investor:
        data = await self._get(f"/investors/{investor_id}")
        return _parse_investor(data)

    async def search_investors(self, query: str, *, limit: int = 25) -> list[Investor]:
        data = await self._get("/investors/search", params={"q": query, "limit": limit})
        return [_parse_investor(item) for item in data.get("items", data.get("results", []))]

    async def get_investor_funds(self, investor_id: str) -> list[Fund]:
        items = await self._paginate(f"/investors/{investor_id}/funds")
        return [_parse_fund(item, investor_id) for item in items]

    # ------------------------------------------------------------------
    # Funds
    # ------------------------------------------------------------------

    async def get_fund(self, fund_id: str) -> Fund:
        data = await self._get(f"/funds/{fund_id}")
        return _parse_fund(data, data.get("investorId", ""))


# ---------------------------------------------------------------------------
# Parsing helpers — map raw API JSON to Pydantic models
# ---------------------------------------------------------------------------

def _safe_date(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _parse_company(raw: dict[str, Any]) -> Company:
    status_map = {
        "Operating": CompanyStatus.ACTIVE,
        "Acquired/Merged": CompanyStatus.ACQUIRED,
        "Went Public": CompanyStatus.PUBLIC,
        "Out of Business": CompanyStatus.INACTIVE,
    }
    return Company(
        pitchbook_id=raw.get("companyId") or raw.get("pbId") or raw.get("id", ""),
        name=raw.get("companyName") or raw.get("name", ""),
        description=raw.get("description", ""),
        status=status_map.get(raw.get("businessStatus", ""), CompanyStatus.ACTIVE),
        website=raw.get("website", ""),
        founded_date=_safe_date(raw.get("yearFounded") or raw.get("foundedDate")),
        employee_count=raw.get("employees") or raw.get("employeeCount"),
        primary_industry=raw.get("primaryIndustryCode") or raw.get("primaryIndustry", ""),
        primary_sector=raw.get("primarySector", ""),
        hq_location=raw.get("hqLocation") or raw.get("city", ""),
        total_raised_usd=raw.get("totalRaised") or raw.get("totalRaisedUsd"),
        last_financing_date=_safe_date(raw.get("lastFinancingDate")),
        last_financing_deal_type=raw.get("lastFinancingDealType", ""),
        last_financing_size_usd=raw.get("lastFinancingSize"),
        ownership_status=raw.get("ownershipStatus", ""),
    )


def _parse_deal(raw: dict[str, Any], company_id: str) -> Deal:
    type_map: dict[str, DealType] = {
        "Series A": DealType.SERIES_A,
        "Series B": DealType.SERIES_B,
        "Series C": DealType.SERIES_C,
        "Seed Round": DealType.SEED,
        "Angel": DealType.ANGEL,
        "Grant": DealType.GRANT,
        "Debt": DealType.DEBT,
        "IPO": DealType.IPO,
        "M&A": DealType.MERGER_ACQUISITION,
        "Buyout/LBO": DealType.BUYOUT,
        "Secondary Transaction": DealType.SECONDARY,
    }
    raw_type = raw.get("dealType") or raw.get("dealType1", "")
    return Deal(
        pitchbook_id=raw.get("dealId") or raw.get("pbId") or raw.get("id", ""),
        company_id=company_id or raw.get("companyId", ""),
        deal_type=type_map.get(raw_type, DealType.OTHER),
        deal_date=_safe_date(raw.get("dealDate")),
        deal_size_usd=raw.get("dealSize") or raw.get("dealSizeUsd"),
        pre_money_valuation_usd=raw.get("preMoneyValuation"),
        post_money_valuation_usd=raw.get("postMoneyValuation"),
        lead_investors=raw.get("leadInvestors", []),
        all_investors=raw.get("investors", []),
        deal_status=raw.get("dealStatus", ""),
        deal_synopsis=raw.get("synopsis", ""),
    )


def _parse_investor(raw: dict[str, Any]) -> Investor:
    return Investor(
        pitchbook_id=raw.get("investorId") or raw.get("pbId") or raw.get("id", ""),
        name=raw.get("investorName") or raw.get("name", ""),
        investor_type=raw.get("investorType", ""),
        description=raw.get("description", ""),
        website=raw.get("website", ""),
        hq_location=raw.get("hqLocation", ""),
        assets_under_management_usd=raw.get("aum") or raw.get("assetsUnderManagement"),
        total_investments=raw.get("totalInvestments"),
        notable_investments=raw.get("notableInvestments", []),
    )


def _parse_fund(raw: dict[str, Any], investor_id: str) -> Fund:
    return Fund(
        pitchbook_id=raw.get("fundId") or raw.get("pbId") or raw.get("id", ""),
        name=raw.get("fundName") or raw.get("name", ""),
        investor_id=investor_id or raw.get("investorId", ""),
        fund_size_usd=raw.get("fundSize") or raw.get("fundSizeUsd"),
        vintage_year=raw.get("vintageYear"),
        fund_type=raw.get("fundType", ""),
        status=raw.get("status", ""),
    )


def _parse_person(raw: dict[str, Any]) -> Person:
    return Person(
        pitchbook_id=raw.get("personId") or raw.get("pbId") or raw.get("id", ""),
        name=raw.get("name") or raw.get("fullName", ""),
        title=raw.get("primaryTitle") or raw.get("title", ""),
        company_id=raw.get("companyId", ""),
        company_name=raw.get("companyName", ""),
        bio=raw.get("bio", ""),
    )
