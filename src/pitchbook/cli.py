"""CLI entry point for the PitchBook integration.

Commands:
    pitchbook import   — Bulk-import companies by name or PitchBook ID
    pitchbook listen   — Start the polling listener for watched companies
    pitchbook query    — Ask a natural-language question about stored data
    pitchbook watch    — Add/remove companies from the watch list
    pitchbook status   — Show what's in the local database
    pitchbook refresh  — Re-import all stored companies with fresh data
    pitchbook serve    — Start the web dashboard
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


def _get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("Set PITCHBOOK_API_KEY and other env vars. See .env.example")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(verbose: bool) -> None:
    """PitchBook data listener, importer, and agent query interface."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


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

    settings = _get_settings()

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

    settings = _get_settings()
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

    settings = _get_settings()
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
    settings = _get_settings()
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
    settings = _get_settings()
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

    settings = _get_settings()

    async def _run() -> None:
        async with PitchBookImporter(settings) as importer:
            stats = await importer.refresh_all()
        console.print(f"[bold green]Refresh complete[/bold green]: {stats.total} entities updated")
        if stats.errors:
            for err in stats.errors:
                console.print(f"  [yellow]⚠ {err}[/yellow]")

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

    _get_settings()  # validate config early
    console.print(f"[bold]Starting PitchBook dashboard at http://{host}:{port}[/bold]")

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
