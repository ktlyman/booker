"""Agent-friendly query interface for PitchBook data.

Provides a RAG-like experience: an agent (or human) asks natural-language
questions about companies, deals, investors, etc. and this module:

1. Searches the local store for relevant entities
2. Optionally fetches fresh data from PitchBook API if not cached
3. Assembles context from matching records
4. Uses Claude to synthesise a grounded answer

This is analogous to how one might interact with Gemini + NotebookLM or
a vector-store RAG pipeline, but tuned for structured financial data.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from pitchbook.client import PitchBookClient
from pitchbook.config import Settings
from pitchbook.models import QueryResult
from pitchbook.store import PitchBookStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions that Claude can call to retrieve PitchBook data
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_companies",
        "description": (
            "Search the PitchBook database for companies matching a query string. "
            "Returns company profiles with funding, industry, and status information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company name or keyword to search for",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company_details",
        "description": (
            "Get the full profile for a specific company including description, "
            "funding history, employee count, industry, and headquarters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "PitchBook company ID",
                },
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "get_company_deals",
        "description": (
            "Get all financing deals / transactions for a company. "
            "Includes deal type, size, date, valuation, and investors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "PitchBook company ID",
                },
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "search_investors",
        "description": (
            "Search for investors by name or keyword. Returns investor profiles "
            "with AUM, investment count, and type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Investor name or keyword to search for",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company_people",
        "description": "Get key people (executives, board members) for a company.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_id": {
                    "type": "string",
                    "description": "PitchBook company ID",
                },
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "get_recent_changes",
        "description": (
            "Get recent change events detected by the listener for watched companies. "
            "Includes new deals, status changes, funding updates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of events to return (default 20)",
                },
            },
        },
    },
    {
        "name": "full_text_search",
        "description": (
            "Search across all PitchBook entity types (companies, deals, investors, "
            "people) for a keyword. Useful for broad queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search for across all entities",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_watched_companies",
        "description": "List all companies currently being monitored by the listener.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


SYSTEM_PROMPT = """\
You are a financial data analyst assistant with access to PitchBook data. \
You can look up companies, their financing history, investors, deals, and key people.

When answering questions:
- Use the available tools to retrieve relevant data before answering
- Cite specific data points (amounts, dates, investor names) from the retrieved records
- If data is not available in the local store, say so explicitly
- Present financial figures clearly with appropriate formatting
- When comparing companies, retrieve data for all of them before drawing conclusions

You have access to both a local cache of PitchBook data and can search for \
new information. The local data comes from the PitchBook API v2 and may have \
been imported in bulk or collected by the real-time listener.\
"""


class PitchBookAgentInterface:
    """Conversational interface for querying PitchBook data.

    An agent sends natural language questions and gets grounded answers
    backed by data from the local store (and optionally live API).

    This class uses Claude with tool-use to implement a retrieval-augmented
    generation pattern over structured PitchBook data.

    Usage::

        interface = PitchBookAgentInterface(settings)
        result = await interface.query("What is Anthropic's latest funding round?")
        print(result.answer)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: PitchBookStore | None = None,
        client: PitchBookClient | None = None,
    ) -> None:
        self._settings = settings or Settings()  # type: ignore[call-arg]
        self._store = store or PitchBookStore(self._settings.db_path)
        self._api_client = client
        self._anthropic = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)

    async def query(self, question: str) -> QueryResult:
        """Ask a natural-language question about PitchBook data.

        The method runs an agentic loop:
        1. Sends the question to Claude with PitchBook tools
        2. Claude calls tools to retrieve data
        3. Tool results are fed back to Claude
        4. Claude produces a final grounded answer
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        sources: list[str] = []
        companies_referenced: list[str] = []
        raw_data: dict[str, Any] = {}

        # Agentic tool-use loop (max 10 rounds to prevent runaway)
        for _ in range(10):
            response = self._anthropic.messages.create(
                model=self._settings.claude_model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                # Extract text answer
                answer = ""
                for block in response.content:
                    if block.type == "text":
                        answer += block.text
                return QueryResult(
                    query=question,
                    answer=answer,
                    sources=sources,
                    companies_referenced=companies_referenced,
                    raw_data=raw_data,
                )

            if response.stop_reason == "tool_use":
                # Process tool calls
                assistant_content: list[dict[str, Any]] = []
                tool_results: list[dict[str, Any]] = []

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                        result = self._execute_tool(block.name, block.input)
                        sources.extend(result.get("_sources", []))
                        companies_referenced.extend(result.get("_companies", []))
                        raw_data[f"{block.name}_{block.id[:8]}"] = result
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })

                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return QueryResult(
            query=question,
            answer="Unable to produce an answer within the allowed iterations.",
            sources=sources,
            companies_referenced=companies_referenced,
            raw_data=raw_data,
        )

    def _execute_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call to the appropriate store/client method."""
        if name == "search_companies":
            companies = self._store.search_companies(params["query"])
            return {
                "companies": [c.model_dump(mode="json") for c in companies],
                "_sources": [c.pitchbook_id for c in companies],
                "_companies": [c.name for c in companies],
            }

        if name == "get_company_details":
            company = self._store.get_company(params["company_id"])
            if company:
                return {
                    "company": company.model_dump(mode="json"),
                    "_sources": [company.pitchbook_id],
                    "_companies": [company.name],
                }
            return {"error": f"Company {params['company_id']} not found in local store"}

        if name == "get_company_deals":
            deals = self._store.get_deals_for_company(params["company_id"])
            return {
                "deals": [d.model_dump(mode="json") for d in deals],
                "_sources": [d.pitchbook_id for d in deals],
                "_companies": [],
            }

        if name == "search_investors":
            investors = self._store.search_investors(params["query"])
            return {
                "investors": [i.model_dump(mode="json") for i in investors],
                "_sources": [i.pitchbook_id for i in investors],
                "_companies": [],
            }

        if name == "get_company_people":
            people = self._store.get_people_for_company(params["company_id"])
            return {
                "people": [p.model_dump(mode="json") for p in people],
                "_sources": [p.pitchbook_id for p in people],
                "_companies": [],
            }

        if name == "get_recent_changes":
            limit = params.get("limit", 20)
            changes = self._store.get_recent_changes(limit=limit)
            return {
                "changes": [c.model_dump(mode="json") for c in changes],
                "_sources": [c.entity_id for c in changes],
                "_companies": [c.entity_name for c in changes],
            }

        if name == "full_text_search":
            results = self._store.full_text_search(params["query"])
            all_sources = []
            for entity_list in results.values():
                all_sources.extend(item.get("id", "") for item in entity_list)
            return {**results, "_sources": all_sources, "_companies": []}

        if name == "list_watched_companies":
            watched = self._store.list_watched_companies()
            return {
                "watched": [{"id": w[0], "name": w[1]} for w in watched],
                "_sources": [w[0] for w in watched],
                "_companies": [w[1] for w in watched],
            }

        return {"error": f"Unknown tool: {name}"}

    # ------------------------------------------------------------------
    # Convenience methods for direct (non-conversational) access
    # ------------------------------------------------------------------

    def get_company_summary(self, company_id: str) -> str | None:
        """Return a plain-text summary of a company from local data."""
        company = self._store.get_company(company_id)
        if not company:
            return None
        deals = self._store.get_deals_for_company(company_id)
        people = self._store.get_people_for_company(company_id)

        lines = [
            f"# {company.name}",
            f"Status: {company.status.value}",
            f"Industry: {company.primary_industry}",
            f"HQ: {company.hq_location}",
            f"Website: {company.website}",
        ]
        if company.founded_date:
            lines.append(f"Founded: {company.founded_date}")
        if company.employee_count:
            lines.append(f"Employees: {company.employee_count:,}")
        if company.total_raised_usd:
            lines.append(f"Total raised: ${company.total_raised_usd:,.0f}")
        if company.description:
            lines.append(f"\n{company.description}")

        if deals:
            lines.append(f"\n## Financing History ({len(deals)} deals)")
            for d in deals[:10]:
                size = f"${d.deal_size_usd:,.0f}" if d.deal_size_usd else "undisclosed"
                lines.append(f"- {d.deal_date or '?'}: {d.deal_type.value} — {size}")

        if people:
            lines.append(f"\n## Key People ({len(people)})")
            for p in people[:10]:
                lines.append(f"- {p.name}, {p.title}")

        return "\n".join(lines)
