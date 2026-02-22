"""FastAPI web server for the PitchBook frontend.

Serves a single-page HTML dashboard and REST API endpoints
for viewing data, managing watched companies, and searching.
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from pitchbook.config import Settings
from pitchbook.store import PitchBookStore

# ---------------------------------------------------------------------------
# Request models (module-level so FastAPI can introspect them)
# ---------------------------------------------------------------------------


class WatchRequest(BaseModel):
    pitchbook_id: str
    name: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

_store: PitchBookStore | None = None


def get_store() -> PitchBookStore:
    global _store
    if _store is None:
        settings = Settings()  # type: ignore[call-arg]
        _store = PitchBookStore(settings.db_path)
    return _store


def create_app(store: PitchBookStore | None = None) -> FastAPI:
    """Create the FastAPI application.

    If *store* is provided it will be used directly (useful for tests).
    Otherwise a store is created from environment settings on first request.
    """
    app = FastAPI(title="PitchBook Dashboard", version="0.1.0")

    if store is not None:
        global _store
        _store = store

    # ------------------------------------------------------------------
    # Frontend — serve the SPA
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        html_path = Path(__file__).parent / "static" / "index.html"
        return html_path.read_text()

    # ------------------------------------------------------------------
    # API — Companies
    # ------------------------------------------------------------------

    @app.get("/api/companies")
    async def list_companies() -> list[dict[str, Any]]:
        companies = get_store().list_companies()
        return [c.model_dump(mode="json") for c in companies]

    @app.get("/api/companies/search")
    async def search_companies(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
        companies = get_store().search_companies(q)
        return [c.model_dump(mode="json") for c in companies]

    @app.get("/api/companies/{company_id}")
    async def get_company(company_id: str) -> dict[str, Any]:
        company = get_store().get_company(company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        return company.model_dump(mode="json")

    @app.get("/api/companies/{company_id}/deals")
    async def get_company_deals(company_id: str) -> list[dict[str, Any]]:
        deals = get_store().get_deals_for_company(company_id)
        return [d.model_dump(mode="json") for d in deals]

    @app.get("/api/companies/{company_id}/people")
    async def get_company_people(company_id: str) -> list[dict[str, Any]]:
        people = get_store().get_people_for_company(company_id)
        return [p.model_dump(mode="json") for p in people]

    # ------------------------------------------------------------------
    # API — Investors
    # ------------------------------------------------------------------

    @app.get("/api/investors/search")
    async def search_investors(q: str = Query(..., min_length=1)) -> list[dict[str, Any]]:
        investors = get_store().search_investors(q)
        return [i.model_dump(mode="json") for i in investors]

    # ------------------------------------------------------------------
    # API — Watch list
    # ------------------------------------------------------------------

    @app.get("/api/watched")
    async def list_watched() -> list[dict[str, str]]:
        watched = get_store().list_watched_companies()
        return [{"pitchbook_id": w[0], "name": w[1]} for w in watched]

    @app.post("/api/watched", status_code=201)
    async def add_watched(body: WatchRequest) -> dict[str, str]:
        get_store().add_watched_company(body.pitchbook_id, body.name)
        return {"status": "added", "pitchbook_id": body.pitchbook_id, "name": body.name}

    @app.delete("/api/watched/{pitchbook_id}")
    async def remove_watched(pitchbook_id: str) -> dict[str, str]:
        get_store().remove_watched_company(pitchbook_id)
        return {"status": "removed", "pitchbook_id": pitchbook_id}

    # ------------------------------------------------------------------
    # API — Changes
    # ------------------------------------------------------------------

    @app.get("/api/changes")
    async def list_changes(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        changes = get_store().get_recent_changes(limit=limit)
        return [c.model_dump(mode="json") for c in changes]

    # ------------------------------------------------------------------
    # API — Search (cross-entity)
    # ------------------------------------------------------------------

    @app.get("/api/search")
    async def full_text_search(
        q: str = Query(..., min_length=1),
    ) -> dict[str, list[dict[str, str]]]:
        return get_store().full_text_search(q)

    # ------------------------------------------------------------------
    # API — Status
    # ------------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        s = get_store()
        companies = s.list_companies()
        watched = s.list_watched_companies()
        changes = s.get_recent_changes(limit=5)
        return {
            "companies_stored": len(companies),
            "companies_watched": len(watched),
            "recent_changes": [c.model_dump(mode="json") for c in changes],
        }

    return app
