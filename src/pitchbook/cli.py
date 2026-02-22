"""CLI entry point for the PitchBook integration.

Commands:
    pitchbook import   — Bulk-import companies by name or PitchBook ID
    pitchbook listen   — Start the polling listener for watched companies
    pitchbook query    — Ask a natural-language question about stored data
    pitchbook watch    — Add/remove companies from the watch list
    pitchbook status   — Show what's in the local database
    pitchbook refresh  — Re-import all stored companies with fresh data
    pitchbook serve    — Start the web dashboard
    pitchbook auth     — Manage authentication (status, test, cookies, probe)
"""

from __future__ import annotations

import asyncio
import logging
import sys

import click
from rich.console import Console
from rich.table import Table

from pitchbook.config import Settings
from pitchbook.store import PitchBookStore

console = Console()


def _get_settings(**overrides: str) -> Settings:
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print(
            "Set PITCHBOOK_API_KEY for API key auth, or use "
            "--auth=cookies to authenticate via Chrome cookies.\n"
            "See .env.examples for all options."
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option(
    "--auth",
    type=click.Choice(["auto", "api_key", "cookies"]),
    default=None,
    help="Override authentication mode",
)
@click.pass_context
def main(ctx: click.Context, verbose: bool, auth: str | None) -> None:
    """PitchBook data listener, importer, and agent query interface."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    ctx.ensure_object(dict)
    if auth:
        ctx.obj["auth_mode"] = auth


def _settings_from_ctx() -> Settings:
    """Build Settings, applying any CLI auth override from the click context."""
    ctx = click.get_current_context()
    overrides: dict[str, str] = {}
    auth_mode = ctx.obj.get("auth_mode") if ctx.obj else None
    if auth_mode:
        overrides["auth_mode"] = auth_mode
    return _get_settings(**overrides)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@main.command("import")
@click.argument("companies", nargs=-1, required=True)
@click.option("--by-id", is_flag=True, help="Treat arguments as PitchBook IDs, not names")
@click.option("--no-watch", is_flag=True, help="Don't add to watched companies list")
def import_cmd(companies: tuple[str, ...], by_id: bool, no_watch: bool) -> None:
    """Import historical data for one or more companies.

    Pass company names (default) or PitchBook IDs (with --by-id).
    """
    from pitchbook.importer import PitchBookImporter

    settings = _settings_from_ctx()

    async def _run() -> None:
        async with PitchBookImporter(settings) as importer:
            if by_id:
                stats = await importer.import_by_ids(list(companies), watch=not no_watch)
            else:
                stats = await importer.import_companies(list(companies), watch=not no_watch)

        console.print("\n[bold green]Import complete[/bold green]")
        console.print(f"  Companies: {stats.companies}")
        console.print(f"  Deals:     {stats.deals}")
        console.print(f"  Investors: {stats.investors}")
        console.print(f"  People:    {stats.people}")
        if stats.errors:
            console.print(f"\n[yellow]Warnings ({len(stats.errors)}):[/yellow]")
            for err in stats.errors:
                console.print(f"  - {err}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# listen
# ---------------------------------------------------------------------------


@main.command("listen")
def listen_cmd() -> None:
    """Start the listener that polls PitchBook for watched company changes."""
    from pitchbook.listener import PitchBookListener

    settings = _settings_from_ctx()
    listener = PitchBookListener(settings)

    def _on_change(event: object) -> None:
        console.print(f"[bold cyan]CHANGE:[/bold cyan] {event}")

    listener.on_change(_on_change)
    console.print("[bold]Starting PitchBook listener...[/bold]")
    console.print(f"Poll interval: {settings.poll_interval_seconds}s")
    console.print("Press Ctrl+C to stop.\n")

    try:
        asyncio.run(listener.run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Listener stopped.[/yellow]")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@main.command("query")
@click.argument("question")
def query_cmd(question: str) -> None:
    """Ask a natural-language question about PitchBook data.

    Uses Claude to retrieve and synthesize an answer from local data.
    """
    from pitchbook.agent_interface import PitchBookAgentInterface

    settings = _settings_from_ctx()
    interface = PitchBookAgentInterface(settings)

    async def _run() -> None:
        result = await interface.query(question)
        console.print(f"\n[bold]{result.answer}[/bold]")
        if result.sources:
            console.print(f"\n[dim]Sources: {', '.join(result.sources)}[/dim]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


@main.command("watch")
@click.argument("action", type=click.Choice(["add", "remove", "list"]))
@click.argument("args", nargs=-1)
def watch_cmd(action: str, args: tuple[str, ...]) -> None:
    """Manage the watched companies list.

    \b
    Examples:
        pitchbook watch list
        pitchbook watch add PB-ID "Company Name"
        pitchbook watch remove PB-ID
    """
    settings = _settings_from_ctx()
    store = PitchBookStore(settings.db_path)

    if action == "list":
        watched = store.list_watched_companies()
        if not watched:
            console.print("[dim]No watched companies.[/dim]")
            return
        table = Table(title="Watched Companies")
        table.add_column("PitchBook ID")
        table.add_column("Name")
        for pid, name in watched:
            table.add_row(pid, name)
        console.print(table)

    elif action == "add":
        if len(args) < 2:
            console.print("[red]Usage: pitchbook watch add <ID> <NAME>[/red]")
            return
        store.add_watched_company(args[0], " ".join(args[1:]))
        console.print(f"Added {args[0]} to watch list.")

    elif action == "remove":
        if not args:
            console.print("[red]Usage: pitchbook watch remove <ID>[/red]")
            return
        store.remove_watched_company(args[0])
        console.print(f"Removed {args[0]} from watch list.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@main.command("status")
def status_cmd() -> None:
    """Show a summary of what's in the local PitchBook database."""
    settings = _settings_from_ctx()
    store = PitchBookStore(settings.db_path)

    companies = store.list_companies()
    watched = store.list_watched_companies()
    changes = store.get_recent_changes(limit=5)

    console.print("\n[bold]Local Database Summary[/bold]")
    console.print(f"  Companies stored:  {len(companies)}")
    console.print(f"  Companies watched: {len(watched)}")

    if companies:
        table = Table(title="Stored Companies")
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Industry")
        table.add_column("Total Raised")
        for c in companies[:20]:
            raised = f"${c.total_raised_usd:,.0f}" if c.total_raised_usd else "—"
            table.add_row(c.pitchbook_id, c.name, c.status.value, c.primary_industry, raised)
        console.print(table)

    if changes:
        console.print("\n[bold]Recent Changes[/bold]")
        for ch in changes:
            console.print(f"  [{ch.detected_at:%Y-%m-%d %H:%M}] {ch.summary}")


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


@main.command("refresh")
def refresh_cmd() -> None:
    """Re-import fresh data for all companies in the local store."""
    from pitchbook.importer import PitchBookImporter

    settings = _settings_from_ctx()

    async def _run() -> None:
        async with PitchBookImporter(settings) as importer:
            stats = await importer.refresh_all()
        console.print(f"[bold green]Refresh complete[/bold green]: {stats.total} entities updated")
        if stats.errors:
            for err in stats.errors:
                console.print(f"  [yellow]Warning: {err}[/yellow]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@main.command("serve")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", "-p", default=8080, type=int, help="Port number")
def serve_cmd(host: str, port: int) -> None:
    """Start the web dashboard for viewing PitchBook data."""
    import uvicorn

    from pitchbook.web import create_app

    _settings_from_ctx()  # validate config early
    console.print(f"[bold]Starting PitchBook dashboard at http://{host}:{port}[/bold]")

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@main.group("auth")
def auth_group() -> None:
    """Manage PitchBook authentication."""


@auth_group.command("status")
def auth_status_cmd() -> None:
    """Show the current authentication configuration."""
    settings = _settings_from_ctx()
    console.print(f"[bold]Auth mode:[/bold] {settings.auth_mode.value}")
    if settings.api_key:
        masked = settings.api_key[:4] + "..." + settings.api_key[-4:]
        console.print(f"  API key:      {masked}")
    else:
        console.print("  API key:      [dim]not set[/dim]")
    console.print(f"  API base URL: {settings.api_base_url}")
    console.print(f"  Web base URL: {settings.web_base_url}")


@auth_group.command("test")
def auth_test_cmd() -> None:
    """Test authentication by making a simple API request."""
    from pitchbook.client import PitchBookClient

    settings = _settings_from_ctx()

    async def _run() -> None:
        async with PitchBookClient(settings) as client:
            try:
                results = await client.search_companies("test", limit=1)
                console.print("[bold green]Authentication successful![/bold green]")
                console.print(f"  Mode: {client._auth_mode.value}")
                if results:
                    console.print(f"  Sample result: {results[0].name}")
            except Exception as exc:
                console.print(f"[bold red]Authentication failed:[/bold red] {exc}")

    asyncio.run(_run())


@auth_group.command("cookies")
def auth_cookies_cmd() -> None:
    """Show PitchBook cookies found in Chrome (names only)."""
    from pitchbook.cookies import CookieExtractionError, extract_pitchbook_cookies

    settings = _settings_from_ctx()
    try:
        cookies = extract_pitchbook_cookies(settings.chrome_profile)
        console.print(f"[bold green]Found {len(cookies)} PitchBook cookies:[/bold green]")
        for name in sorted(cookies):
            console.print(f"  - {name}")
    except CookieExtractionError as exc:
        console.print(f"[bold red]Cookie extraction failed:[/bold red] {exc}")


@auth_group.command("probe")
def auth_probe_cmd() -> None:
    """Discover which API endpoints work with cookie authentication.

    Tries several known URL patterns to determine the correct base URL
    for cookie-based access.
    """
    import httpx as _httpx

    from pitchbook.cookies import (
        CookieExtractionError,
        cookies_to_httpx,
        extract_pitchbook_cookies,
    )

    settings = _settings_from_ctx()
    try:
        cookie_dict = extract_pitchbook_cookies(settings.chrome_profile)
    except CookieExtractionError as exc:
        console.print(f"[bold red]Cookie extraction failed:[/bold red] {exc}")
        return

    cookies = cookies_to_httpx(cookie_dict)

    candidate_bases = [
        "https://api.pitchbook.com/v2",
        "https://pitchbook.com/api/v2",
        "https://pitchbook.com/api",
        "https://pitchbook.com",
    ]
    test_path = "/companies/search"
    test_params = {"q": "test", "limit": "1"}

    async def _probe() -> None:
        for base in candidate_bases:
            url = base + test_path
            console.print(f"  Trying {url}...", end=" ")
            try:
                async with _httpx.AsyncClient(
                    cookies=cookies,
                    headers={"Accept": "application/json"},
                    timeout=10,
                    follow_redirects=True,
                ) as http:
                    resp = await http.get(url, params=test_params)
                    if resp.status_code == 200:
                        console.print(f"[green]OK ({resp.status_code})[/green]")
                        try:
                            data = resp.json()
                            console.print(f"    Response keys: {list(data.keys())}")
                        except Exception:
                            console.print("    [dim]Non-JSON response[/dim]")
                    else:
                        console.print(f"[yellow]{resp.status_code}[/yellow]")
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/red]")

    console.print("[bold]Probing PitchBook endpoints with cookies...[/bold]\n")
    asyncio.run(_probe())


if __name__ == "__main__":
    main()
