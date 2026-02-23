"""Async HTTP client for the PitchBook API.

Supports two authentication modes:
- API key: Uses httpx against PitchBook API v2 (requires enterprise subscription)
- Cookies: Uses curl_cffi against PitchBook's web API with Chrome session cookies
  (bypasses Cloudflare TLS fingerprinting that blocks httpx)
"""

from __future__ import annotations

import asyncio
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
    """Async client wrapping the PitchBook REST API.

    Handles authentication (API key or Chrome cookies), pagination,
    rate-limit back-off, and deserialization into Pydantic models.

    In cookie mode, uses curl_cffi to bypass Cloudflare's TLS fingerprint
    checks that block standard Python HTTP clients.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._auth_mode = self._resolve_auth_mode()
        self._http: httpx.AsyncClient | None = None
        self._cookies: dict[str, str] = {}

        if self._auth_mode == AuthMode.API_KEY:
            self._http = self._build_httpx_client()
        else:
            self._init_cookie_auth()

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

    def _build_httpx_client(self) -> httpx.AsyncClient:
        """Construct the httpx client for API key auth."""
        return httpx.AsyncClient(
            base_url=self._settings.api_base_url,
            headers={
                "Authorization": f"PitchBook {self._settings.api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(self._settings.api_timeout),
        )

    def _init_cookie_auth(self) -> None:
        """Load cookies from Chrome for cookie-based auth."""
        from pitchbook.cookies import extract_pitchbook_cookies

        self._cookies = extract_pitchbook_cookies(self._settings.chrome_profile)
        logger.info("Loaded %d PitchBook cookies from Chrome", len(self._cookies))

    def _refresh_cookies(self) -> None:
        """Re-extract cookies from Chrome."""
        from pitchbook.cookies import extract_pitchbook_cookies

        logger.info("Refreshing PitchBook cookies from Chrome...")
        self._cookies = extract_pitchbook_cookies(self._settings.chrome_profile)

    async def close(self) -> None:
        if self._http:
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
    async def _api_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Make a request via httpx (API key mode)."""
        assert self._http is not None
        resp = await self._http.request(method, path, **kwargs)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited by PitchBook, retry after %ds", retry_after)
            raise httpx.TransportError(f"Rate limited, retry after {retry_after}s")
        if resp.status_code >= 400:
            raise PitchBookAPIError(resp.status_code, resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def _web_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a request via curl_cffi (cookie mode).

        Uses curl_cffi to impersonate Chrome's TLS fingerprint, which is
        required to bypass Cloudflare protection on my.pitchbook.com.
        Runs the sync curl_cffi call in a thread to stay async-compatible.
        """
        try:
            from curl_cffi import (
                requests as cffi_requests,  # type: ignore[import-untyped,import-not-found,unused-ignore]  # noqa: E501
            )
        except ImportError as exc:
            raise PitchBookAPIError(
                0,
                "curl_cffi is required for cookie-based auth. "
                "Install it with: pip install curl_cffi",
            ) from exc

        base_url = self._settings.web_base_url.rstrip("/")
        url = f"{base_url}{path}"

        def _do_request() -> Any:
            kwargs: dict[str, Any] = {
                "cookies": self._cookies,
                "impersonate": "chrome131",
                "timeout": self._settings.api_timeout,
                "allow_redirects": False,
            }
            if json_body is not None:
                kwargs["json"] = json_body
                kwargs["headers"] = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            else:
                kwargs["headers"] = {"Accept": "application/json"}
            if params:
                kwargs["params"] = params

            return cffi_requests.request(method, url, **kwargs)

        resp = await asyncio.to_thread(_do_request)

        # Handle auth failures: 302 to login or 401
        if resp.status_code in (302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "login" in location.lower():
                # Try refreshing cookies once
                logger.warning("Session expired (redirect to login), refreshing cookies...")
                await asyncio.to_thread(self._refresh_cookies)
                resp = await asyncio.to_thread(_do_request)
                if resp.status_code in (302, 303, 307, 308):
                    raise PitchBookAuthError(
                        "Session expired after cookie refresh. "
                        "Try closing and reopening Chrome, then retry. "
                        "Chrome must flush session cookies to disk before they can be read."
                    )

        if resp.status_code == 401:
            logger.warning("Auth failed (HTTP 401), refreshing cookies...")
            await asyncio.to_thread(self._refresh_cookies)
            resp = await asyncio.to_thread(_do_request)
            if resp.status_code == 401:
                raise PitchBookAuthError(
                    "Authentication failed after cookie refresh. "
                    "Try closing and reopening Chrome, then retry. "
                    "Chrome must flush session cookies to disk before they can be read."
                )

        if resp.status_code == 429:
            raise PitchBookAPIError(429, "Rate limited by Cloudflare or PitchBook")
        if resp.status_code >= 400:
            detail = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
            raise PitchBookAPIError(resp.status_code, detail)

        return resp.json()  # type: ignore[no-any-return]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._auth_mode == AuthMode.API_KEY:
            return await self._api_request("GET", path, params=params)
        return await self._web_request("GET", path, params=params)

    async def _post(
        self, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._auth_mode == AuthMode.API_KEY:
            return await self._api_request("POST", path, json=json_body)
        return await self._web_request("POST", path, json_body=json_body)

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
    # Web API search (cookie mode)
    # ------------------------------------------------------------------

    async def _web_search(
        self,
        query: str,
        *,
        limit: int = 15,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Execute a general search via the PitchBook web API."""
        body = {
            "transcriptSearchAllowed": True,
            "newsSearchAllowed": True,
            "conferencesSearchAllowed": True,
            "searchRequest": {
                "limit": limit,
                "offset": offset,
                "query": query,
            },
            "timeZoneOffset": "-08:00",
            "isDealSearchAllowed": True,
            "tprAllowed": True,
        }
        return await self._web_request(
            "POST", "/web-api/general-search/search/mixed", json_body=body
        )

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    async def search_companies(
        self,
        query: str,
        *,
        limit: int = 25,
    ) -> list[Company]:
        if self._auth_mode == AuthMode.API_KEY:
            data = await self._get(
                "/companies/search", params={"q": query, "limit": limit}
            )
            return [
                _parse_company(item)
                for item in data.get("items", data.get("results", []))
            ]

        # Cookie mode: use web API general search
        data = await self._web_search(query, limit=limit)
        companies: list[Company] = []
        for item in data.get("items", []):
            if item.get("type") == "COMPANY":
                companies.append(_parse_web_company(item["value"]))
        return companies

    async def get_company(self, company_id: str) -> Company:
        if self._auth_mode == AuthMode.API_KEY:
            data = await self._get(f"/companies/{company_id}")
            return _parse_company(data)

        # Cookie mode: get profile info then search for the company
        profile = await self._web_request(
            "GET", f"/web-api/profiles/{company_id}"
        )
        name = ""
        # Use the profile info to search for the company by ID
        data = await self._web_search(company_id, limit=5)
        for item in data.get("items", []):
            if item.get("type") == "COMPANY":
                pr = item["value"].get("profileResult", {})
                if pr.get("id") == company_id:
                    return _parse_web_company(item["value"])
                if not name:
                    name = pr.get("name", "")

        # Fallback: return minimal company data from the profile endpoint
        profile_types = profile.get("availableProfileTypes", [])
        type_desc = ""
        for pt in profile_types:
            if pt.get("code") == "COMPANY":
                type_desc = pt.get("description", "")
                break
        return Company(
            pitchbook_id=company_id,
            name=name or company_id,
            description=type_desc,
        )

    async def get_company_deals(self, company_id: str) -> list[Deal]:
        if self._auth_mode != AuthMode.API_KEY:
            logger.warning("get_company_deals not yet supported in cookie mode")
            return []
        items = await self._paginate(f"/companies/{company_id}/deals")
        return [_parse_deal(item, company_id) for item in items]

    async def get_company_investors(self, company_id: str) -> list[Investor]:
        if self._auth_mode != AuthMode.API_KEY:
            logger.warning("get_company_investors not yet supported in cookie mode")
            return []
        items = await self._paginate(f"/companies/{company_id}/investors")
        return [_parse_investor(item) for item in items]

    async def get_company_people(self, company_id: str) -> list[Person]:
        if self._auth_mode != AuthMode.API_KEY:
            logger.warning("get_company_people not yet supported in cookie mode")
            return []
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
# Parsing helpers — map raw API v2 JSON to Pydantic models
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


# ---------------------------------------------------------------------------
# Parsing helpers — map web API JSON to Pydantic models
# ---------------------------------------------------------------------------

def _parse_web_company(value: dict[str, Any]) -> Company:
    """Parse a company from PitchBook web API search results.

    The web API returns a nested structure:
    {
        "profileResult": {"id", "name", "description", "location", ...},
        "sparseData": {"ownershipStatus", "businessStatus", "primaryIndustry", ...},
        "website": "...",
        ...
    }
    """
    pr = value.get("profileResult", {})
    sd = value.get("sparseData", {})

    status_map = {
        "Generating Revenue": CompanyStatus.ACTIVE,
        "Operating": CompanyStatus.ACTIVE,
        "Startup": CompanyStatus.ACTIVE,
        "Acquired/Merged": CompanyStatus.ACQUIRED,
        "Went Public": CompanyStatus.PUBLIC,
        "Out of Business": CompanyStatus.INACTIVE,
    }

    return Company(
        pitchbook_id=pr.get("id", ""),
        name=pr.get("name", ""),
        description=pr.get("description", ""),
        status=status_map.get(sd.get("businessStatus", ""), CompanyStatus.ACTIVE),
        website=value.get("website", ""),
        founded_date=_safe_date(sd.get("yearFounded")),
        primary_industry=sd.get("primaryIndustry") or value.get("primaryIndustry", ""),
        hq_location=pr.get("location", ""),
        ownership_status=sd.get("ownershipStatus", ""),
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
