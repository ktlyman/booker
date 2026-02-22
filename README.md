# Booker

A PitchBook data integration tool that imports company, deal, investor, and people data from the PitchBook API, monitors watched companies for changes, and provides an AI-powered natural-language query interface backed by Claude.

## Features

- **Data Import** - Bulk import companies by name or PitchBook ID with related deals, investors, and key people
- **Change Detection** - Poll watched companies at configurable intervals and detect new deals, status changes, metric updates, and team changes
- **AI Query Interface** - Ask natural-language questions about your data using Claude with tool-calling (RAG-style)
- **Web Dashboard** - FastAPI-powered single-page app for browsing data, managing watch lists, and searching across entities
- **Rich CLI** - Full-featured command-line interface with formatted terminal output

## Quick Start

### Prerequisites

- Python 3.11+
- A [PitchBook API](https://pitchbook.com) key
- An [Anthropic API](https://console.anthropic.com) key (for the query interface)

### Installation

```bash
pip install -e .
```

### Configuration

Copy `.env.examples` to `.env` and fill in your API keys:

```bash
cp .env.examples .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `PITCHBOOK_API_KEY` | Yes | - | PitchBook API key |
| `PITCHBOOK_ANTHROPIC_API_KEY` | Yes | - | Anthropic API key for Claude |
| `PITCHBOOK_API_BASE_URL` | No | `https://api.pitchbook.com/v2` | API base URL |
| `PITCHBOOK_API_TIMEOUT` | No | `30` | Request timeout (seconds) |
| `PITCHBOOK_API_MAX_RETRIES` | No | `3` | Max retry attempts |
| `PITCHBOOK_DB_PATH` | No | `pitchbook_data.db` | SQLite database path |
| `PITCHBOOK_POLL_INTERVAL_SECONDS` | No | `300` | Listener poll interval (seconds) |
| `PITCHBOOK_CLAUDE_MODEL` | No | `claude-sonnet-4-20250514` | Claude model for queries |

### Usage

```bash
# Import a company and its related data
pitchbook import "Anthropic"

# Watch a company for changes
pitchbook watch add <company-id>

# Start the change listener
pitchbook listen

# Ask a question about your data
pitchbook query "What is Anthropic's latest funding round?"

# Check database status
pitchbook status

# Re-import all companies with fresh data
pitchbook refresh

# Start the web dashboard
pitchbook serve --port 8080
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --tb=short

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## Architecture

```
src/pitchbook/
├── config.py           # Settings via pydantic-settings
├── models.py           # Pydantic data models (Company, Deal, Investor, Fund, Person)
├── client.py           # Async PitchBook API client with retries
├── store.py            # SQLAlchemy ORM + SQLite persistence
├── listener.py         # Change detection polling loop
├── importer.py         # Bulk import orchestration
├── agent_interface.py  # Claude tool-calling query interface
├── cli.py              # Click CLI with Rich output
├── web.py              # FastAPI REST API + dashboard
└── static/index.html   # Single-page dashboard
```
