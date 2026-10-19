# Conversation Summary: Adding Logging for web_search Tool Call Trace

## Date
2025-01-XX

## User Request
The user reported that when using the speech-to-speech app to ask the LLM to do a web search, the assistant responds with a `tool_call` response (function=web_search), but the app doesn't execute the search — it just speaks the response. The user asked to add logging to trace the path of a tool_call response from the point it is received from the assistant to the point where a web_search is sent to the SearXNG server.

## Investigation

### Architecture Overview
The speech-to-speech app uses a three-part flow for web search:

1. **Server-side Python (speech-to-speech backend)**: The LLM generates a tool_call response containing a `web_search` function call. The `ResponseHandler` in `response.py` detects this and sends Realtime protocol events over WebSocket to the client.

2. **Client-side JavaScript (demo browser app)**: The OpenAI Agents SDK RealtimeSession receives the events and triggers the tool executor defined in `main.js`. The `runTool` function delegates to `execWebSearch`, which fetches the same-origin `/api/search` endpoint.

3. **Demo server (Python)**: The `/api/search` POST endpoint in `demo/server.py` proxies the request to a SearXNG instance via HTTP.

### Key Files Examined
- `demo/main.js` — Browser client with tool definitions and executor
- `demo/s2s-realtime-client.js` — OpenAI Agents SDK adapter
- `demo/server.py` — FastAPI demo server with SearXNG proxy
- `src/speech_to_speech/api/openai_realtime/handlers/response.py` — Server-side response handler
- `src/speech_to_speech/api/openai_realtime/audio_client.py` — Tool call coordinator
- `examples/realtime_web_search_tool.py` — Packaged web search tool

## Actions Taken

### 1. Added logging in `response.py` (Server-side tool call detection)

**Location:** `src/speech_to_speech/api/openai_realtime/handlers/response.py`

Added `logger.info` calls at four key points:

- **Tool call detection** (in `on_assistant_output`, when `isinstance(part, AssistantToolCallPart)`):
  ```python
  logger.info("Tool call detected: name=%s call_id=%s response_id=%s", ...)
  ```

- **Early tool call ready** (in `on_assistant_tool_call_ready`):
  ```python
  logger.info("Early tool call ready: name=%s call_id=%d output_sequence=%d", ...)
  ```

- **Flushing early tool calls** (in `_flush_early_tool_calls`):
  ```python
  logger.info("Flushing early tool call: name=%s call_id=%d output_sequence=%d", ...)
  ```

- **Response done** (in `on_assistant_response_done`):
  ```python
  logger.info("Response done: response_key=%s status=%s tool_call_ids=%s", ...)
  ```

### 2. Added logging in `demo/server.py` (SearXNG proxy endpoint)

**Location:** `demo/server.py`

Added `logger.info` calls in the `/api/search` POST endpoint:

- **Incoming request**:
  ```python
  logger.info("search: incoming query=%s", query)
  ```

- **SearXNG call preparation**:
  ```python
  logger.info("search: calling SearXNG url=%s params=%s", full_url, params)
  ```

- **Response summary**:
  ```python
  logger.info("search: done query=%s results=%d answer=%s", query, len(results), "yes" if answer else "no")
  ```

### 3. Added logging in `demo/main.js` (Browser tool execution)

**Location:** `demo/main.js`

Added `console.debug` calls with `[tool]` prefix:

- **Tool run start** (in `runTool`):
  ```js
  console.debug(`[tool] run name=%s callId=%s args=%s`, name, JSON.stringify(callId), argsJson)
  ```

- **Web search execution** (in `runTool`):
  ```js
  console.debug(`[tool] web_search: executing query=%s`, query)
  console.debug(`[tool] web_search: done, result_length=${result.output.length}`)
  ```

- **API fetch** (in `execWebSearch`):
  ```js
  console.debug(`[tool] execWebSearch: fetching api/search for query=%s`, query)
  console.debug(`[tool] execWebSearch: api/search response status=%d`, res.status)
  console.debug(`[tool] execWebSearch: got ${json.results?.length || 0} result(s), answer=${json.answer ? 'yes' : 'no'}`)
  ```

## Expected Log Output Sequence

When a web search is triggered, logs will appear in this order:

### Server logs (speech-to-speech backend + demo server):
```
INFO: Tool call detected: name=web_search call_id=call_... response_id=resp_...
INFO: Response done: response_key=... status=completed tool_call_ids=['call_...']
INFO: search: incoming query=what is the weather today
INFO: search: calling SearXNG url=http://fw16:8888/search params={'q': '...', 'format': 'json', 'number_results': 5}
INFO: search: done query=what is the weather today results=5 answer=no
```

### Browser console logs:
```
[tool] run name=web_search callId="call_..." args={"query":"what is the weather today"}
[tool] web_search: executing query=what is the weather today
[tool] execWebSearch: fetching api/search for query=what is the weather today
[tool] execWebSearch: api/search response status=200
[tool] execWebSearch: got 5 result(s), answer=no
[tool] web_search: done, result_length=420
```

## How to View Logs

1. **Server-side**: Set `log_level=info` in the speech-to-speech server config. Look for `INFO` level messages.

2. **Browser-side**: Open DevTools Console and ensure verbose logging is enabled. Messages are prefixed with `[tool]`.

## Files Modified

1. `src/speech_to_speech/api/openai_realtime/handlers/response.py` — Added 4 logger.info calls
2. `demo/server.py` — Added 3 logger.info calls
3. `demo/main.js` — Added 6 console.debug calls

All changes verified with no diagnostics/errors.

## Notes

- The existing `examples/realtime_web_search_tool.py` already had logging (`logger.info`) at the SearXNG call site, which was not modified.
- The demo server uses `logger = logging.getLogger("s2s.search")` for search-related logs.
- The Python logging uses `%s` formatting style consistent with the codebase.
- The browser logging uses `console.debug` format strings with `%s` placeholders (works in all modern browsers).
