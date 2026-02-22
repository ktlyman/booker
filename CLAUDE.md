# CLAUDE.md

## Project Overview

Booker (package name: `pitchbook-listener`) is a PitchBook data integration tool. It imports company/deal/investor data from the PitchBook API, detects changes on watched companies, and provides a Claude-powered natural-language query interface. Includes a FastAPI web dashboard.

## Tech Stack

- **Python 3.11+** with async-first design (httpx, aiosqlite, FastAPI)
- **SQLite** via SQLAlchemy ORM for local data storage
- **Claude API** (Anthropic) for the agent query interface with tool-calling
- **Click + Rich** for CLI, **FastAPI + Uvicorn** for web server

## Project Structure

```
src/pitchbook/       # All source code
  config.py          # Pydantic settings (env vars), AuthMode enum
  models.py          # Pydantic data models
  client.py          # PitchBook API async client (API key + cookie auth)
  cookies.py         # Chrome cookie extraction via rookiepy
  store.py           # SQLAlchemy/SQLite persistence
  listener.py        # Change detection polling
  importer.py        # Bulk data import
  agent_interface.py # Claude RAG query interface
  cli.py             # Click CLI commands
  web.py             # FastAPI web server
  static/index.html  # Dashboard SPA
tests/               # pytest test suite
```

## Development Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with cookie auth support only
pip install -e ".[cookies]"

# Run tests
pytest tests/ -v --tb=short

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Run the CLI
pitchbook --help
```

## Authentication

Two auth modes (set via `PITCHBOOK_AUTH_MODE` or `--auth` flag):

- **api_key** — uses `PITCHBOOK_API_KEY` with PitchBook API v2
- **cookies** — extracts session cookies from Chrome (requires `rookiepy`)
- **auto** (default) — uses API key if set, otherwise falls back to cookies

Useful commands: `pitchbook auth status`, `pitchbook auth test`, `pitchbook auth cookies`, `pitchbook auth probe`

## Environment Variables

Required (at least one auth method):
- `PITCHBOOK_API_KEY` - PitchBook API key (required for `api_key` mode)
- `PITCHBOOK_ANTHROPIC_API_KEY` - Anthropic API key (for query interface)

Optional:
- `PITCHBOOK_AUTH_MODE` - Auth mode: `auto`, `api_key`, or `cookies` (default: `auto`)
- `PITCHBOOK_WEB_BASE_URL` - PitchBook website URL for cookie auth (default: `https://pitchbook.com`)
- `PITCHBOOK_DB_PATH` - SQLite database path (default: `pitchbook_data.db`)
- `PITCHBOOK_POLL_INTERVAL_SECONDS` - Listener poll interval (default: `300`)
- `PITCHBOOK_CLAUDE_MODEL` - Claude model to use (default: `claude-sonnet-4-20250514`)

## Key Conventions

- Async everywhere: all I/O uses async/await
- Pydantic models for all API data and settings
- Tests use `respx` for HTTP mocking and `aiosqlite` with temp databases
- `pyproject.toml` is the single config file (no setup.py/cfg)
- Ruff for linting (rules: E, F, I, N, W, UP), mypy strict mode
- Line length: 100 characters
