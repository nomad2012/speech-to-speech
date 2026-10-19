"""SearXNG-backed web search tool for the packaged Realtime audio client."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from speech_to_speech.api.openai_realtime.audio_client import ToolResult

logger = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://fw16:8888").strip()
MAX_RESULTS = 5

TOOLS = [
    {
        "type": "function",
        "name": "web_search",
        "description": (
            "Search the web for current or factual information you do not already know, such as news, prices, "
            "facts, or documentation. Returns the top results with titles, snippets, and URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The web search query.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


async def execute_tool(name: str, arguments: dict[str, Any]) -> ToolResult:
    """Execute the declared web search tool and request a spoken follow-up."""

    if name != "web_search":
        raise ValueError(f"Unknown tool: {name}")

    query = str(arguments.get("query", "")).strip()
    if not query:
        return ToolResult({"error": "query must be a non-empty string"})
    if not SEARXNG_URL:
        return ToolResult(
            {
                "query": query,
                "error": "SEARXNG_URL is not set.",
            }
        )

    params = urlencode({"q": query, "format": "json", "number_results": MAX_RESULTS})
    search_url = f"{SEARXNG_URL}/search?{params}"

    logger.info("web_search query=%s max_results=%d url=%s", query, MAX_RESULTS, search_url)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(search_url)
    except httpx.RequestError as exc:
        logger.warning("SearXNG unreachable for %r: %s", query, exc)
        return ToolResult(
            {
                "query": query,
                "error": "Search provider is temporarily unreachable.",
            }
        )

    if response.status_code != 200:
        message = None
        try:
            data = response.json()
            message = data.get("message") if isinstance(data, dict) else None
        except ValueError:
            pass
        detail = f"Search provider error ({response.status_code})"
        if message:
            detail += f": {message}"
        logger.warning("SearXNG returned HTTP %d for %r", response.status_code, query)
        return ToolResult({"query": query, "error": detail})

    try:
        data = response.json()
    except ValueError:
        logger.warning("SearXNG returned invalid JSON for %r", query)
        return ToolResult({"query": query, "error": "Search provider returned an invalid response."})

    # SearXNG does not provide a dedicated answer box; check for answers/solutions anyway.
    answer = None
    for key in ("answers", "solutions", "question"):
        val = data.get(key)
        if isinstance(val, list) and val:
            answer = val[0]
            break
        if isinstance(val, str) and val:
            answer = val
            break

    return ToolResult(
        {
            "query": query,
            "searched_at": datetime.now(timezone.utc).date().isoformat(),
            "answer": answer,
            "results": [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                }
                for item in (data.get("results") or [])[:MAX_RESULTS]
            ],
        }
    )
