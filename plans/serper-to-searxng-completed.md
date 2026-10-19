# Migration Complete: Serper → SearXNG

## Status: Completed
All changes applied successfully. Tests passing. No diagnostics.

## Summary of Changes

| File | Changes |
|------|---------|
| `examples/realtime_web_search_tool.py` | Full rewrite: Serper → SearXNG API, config, response parsing |
| `demo/server.py` | Proxy endpoint updated: Serper → SearXNG, removed user key fallback |
| `demo/README.md` | Updated env var names and tool description |
| `demo/docs/adr/0001-docker-space-with-search-proxy.md` | Updated ADR with SearXNG context |
| `src/speech_to_speech/api/openai_realtime/README.md` | Updated search example documentation |
| `tests/openai_realtime/test_web_search_tool_example.py` | Updated mocks and assertions for SearXNG format |
| `s2s-demo` | Updated convenience script with `SEARXNG_URL` |

## Key Changes
- **Config**: `SERPER_API_KEY` → `SEARXNG_URL` (default: `http://fw16:8888`)
- **API**: `POST` with JSON body → `GET` with query params (`?q=...&format=json&number_results=...`)
- **Result mapping**: `organic[].snippet/link` → `results[].content/url`
- **Answer field**: Google's `answerBox`/`knowledgeGraph` → SearXNG's `answers`/`solutions`/`question`
- **Tests**: All passing, no diagnostics

## Unchanged
- Tool schema (`web_search`) — same name, same parameters
- `ToolResult` output format — same shape for downstream compatibility
- Tool loading mechanism — `load_realtime_tool_module()` works as before

## Environment Variables
- **Old**: `SERPER_API_KEY` (required for Serper API access)
- **New**: `SEARXNG_URL` (defaults to `http://fw16:8888`)

## Remaining Serper References
- `demo/__pycache__/server.cpython-311.pyc` — cached bytecode (auto-regenerates)
- `demo/server.py.bak.20261011T121207` — old backup file (can be deleted)
- `plans/serper-to-searxng-replacement.md` — the migration plan (reference)
