# Plan: Replace Serper Search Tool with SearXNG

## Goal
Replace the existing Serper search tool integration with a standalone SearXNG instance at `http://fw16:8888`, keeping the same tool contract (`web_search`) and result format to minimize downstream changes.

## User Decisions
1. **SearXNG setup**: Standalone instance at `http://fw16:8888` — direct integration, no llama-server routing.
2. **Result format**: Keep the current `ToolResult` shape (map SearXNG results into it).
3. **Scope**: Full replacement — no intention to keep Serper. Same tool name, same function signature.

## Files to Modify
- `/home/dennis/projects/speech-to-speech/examples/realtime_web_search_tool.py` — Primary tool definition + execution logic
- `/home/dennis/projects/speech-to-speech/demo/server.py` — Browser demo proxy endpoint
- `/home/dennis/projects/speech-to-speech/tests/openai_realtime/test_web_search_tool_example.py` — Unit tests
- `/home/dennis/projects/speech-to-speech/src/speech_to_speech/api/openai_realtime/README.md` — Documentation
- `/home/dennis/projects/speech-to-speech/demo/docs/adr/0001-docker-space-with-search-proxy.md` — ADR documentation

## Current Architecture (Serper)
- **Tool definition** (`realtime_web_search_tool.py`): JSON Schema with `type: "function"`, `name: "web_search"`
- **Execution**: Async POST to `https://google.serper.dev/search` with `X-API-KEY` header and JSON body `{"q": query, "num": 5}`
- **Config**: `SERPER_API_KEY` env var, `SERPER_URL = "https://google.serper.dev/search"`
- **Result shape**:
  ```json
  {
    "query": "...",
    "searched_at": "...",
    "answer": "..." | null,
    "results": [{"title": "...", "snippet": "...", "url": "..."}]
  }
  ```
- **Tool loading**: `audio_client.py` uses `load_realtime_tool_module()` to import `TOOLS` list and `execute_tool()` function from the module.

## Planned Changes

### 1. `examples/realtime_web_search_tool.py`
- Replace `SERPER_API_KEY` and `SERPER_URL` with `SEARXNG_URL` (default: `"http://fw16:8888"`)
- Change API call from JSON POST to SearXNG parameter-based JSON API:
  ```
  GET http://fw16:8888/search?q=<query>&number_results=5&format=json
  ```
- Parse SearXNG response (`results[].title`, `results[].url`, `results[].content`) and map to existing `ToolResult` structure
- SearXNG does not provide a direct answer box; `answer` field would always be `None`

### 2. `demo/server.py`
- Same config replacement: `SERPER_API_KEY` → `SEARXNG_URL`, update proxy endpoint
- Remove optional user-supplied fallback Serper key (no longer needed)
- Update proxy to call `SEARXNG_URL/search` with parameter-based format

### 3. `tests/openai_realtime/test_web_search_tool_example.py`
- Update mock responses to use SearXNG response format
- Update environment variable references

### 4. Documentation
- Update `README.md` references from Serper to SearXNG
- Update ADR references from Serper to SearXNG
- Update env var name from `SERPER_API_KEY` to `SEARXNG_URL`

## SearXNG API Details
- **Endpoint**: `GET /search?format=json&q=<query>&number_results=5`
- **Response fields**:
  - `results[].title` → `results[].title`
  - `results[].url` → `results[].url`
  - `results[].content` → `results[].snippet`

## Success Criteria
- `web_search` tool works via CLI (`--tool-module examples.realtime_web_search_tool`)
- `POST /api/search` proxy endpoint works via browser demo
- All existing tests pass with updated mocks
- No Serper references remain in the codebase
