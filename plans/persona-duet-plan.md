# Plan: Two-Persona Conversation "Versus" App

Status: implemented (v1). Additive only — no changes to `demo/`, the Realtime server, or `pyproject.toml`.
The web app lives in the `versus/` directory (persona A vs. persona B).

## Architecture
The app runs as a **separate server** (`versus/server.py`), not part of the original realtime server.
The original realtime server uses the OpenAI Realtime WebSocket protocol; this app uses a remote LLM
exposing an OpenAI-compatible Chat Completions API (`http://dldsky:8080/v1/chat/completions`).
They are isolated processes but share infrastructure pieces (OmniVoice TTS handler).

## Goal
A standalone web app that lets you pick **two personas** and watch/listen to them talk to each
other about a topic you initiate. Each persona's reply is fed as the other persona's user message,
looping A → B → A → B … for a fixed number of turns. You **hear** each persona speak (TTS).

## Core technique (reuses existing machinery)
**Text prompt (v1 — simple start):**
1. User types a prompt text in the browser UI.
2. That text becomes the first user message → Persona A's system prompt.

**Future enhancement: STT trigger:**
1. Browser captures microphone audio.
2. Browser sends raw audio to server → server runs **local STT** (Parakeet or Whisper)
   using Persona A's language → returns text prompt.
3. That text becomes the first user message → Persona A's system prompt.
*(STT is planned but not in v1 — the text input lets you test the full flow without any STT model.)*

**Per turn (after prompt):**
1. `tts.switch_persona(speaker)` — select the speaker's cloned voice (OmniVoice persona prompts).
2. Build LLM request: persona system prompt + chat history (text bridge) → call remote LLM at
   `SIM_LLM_URL/v1/chat/completions` (streaming).
3. Collect LLM response text.
4. `tts.process(TTSInput(text=reply, language_code=...))` → int16 PCM @ 16 kHz.
5. Emit `{speaker, text, audio}` to the browser. The reply **text** becomes the next speaker's input.

**Key primitives reused:**
- `src/speech_to_speech/personas.py`: `load_personas()`, `Persona`.
- `src/speech_to_speech/TTS/omnivoice_handler.py`: `switch_persona`, persona voice prompts.
- `src/speech_to_speech/backend_registry.py`: `create_backend_handler`, `BackendSelection`, `HandlerContext`.
- **New**: OpenAI-compatible HTTP client (`httpx`) for remote LLM calls.

**Key primitives NOT used:**
- `src/speech_to_speech/LLM/language_model.py`: `BaseLanguageModelHandler` (no local LLM loading).
- `src/speech_to_speech/LLM/chat.py`: `Chat` (remote LLM handles conversation history).
- `src/speech_to_speech/api/openai_realtime/runtime_config.py`: `RuntimeConfig`.

## Agreed decisions
1. **Text-bridge:** each persona reads the other's text reply (no STT/VAD in the loop). TTS is for listening only.
2. **OmniVoice-only** TTS (per-persona cloned voices). Other TTS backends are not supported by this app.
3. **Text prompt (v1):** the duet is started by a text prompt typed in the browser UI. STT trigger is
   planned as a future enhancement (likely using Parakeet STT, since it's already working in the realtime server).
4. **Turn-gated, fully sequential (v1):** speaker B is generated only after the browser sends
   `playback_done` for speaker A's audio. B cannot start before A finishes.
5. **Per-persona language:** each persona speaks its own `language.txt`; optional shared language.
6. **Remote LLM:** connects to a configurable URL (default `http://dldsky:8080`) via Chat Completions API.
7. **Additive:** no changes to the existing demo/server.

## Flow
```
Browser                     Server
   |                           |
   |  type prompt text ────────►│
   |                           │
   |  POST /api/simulations ──►│  create engine, start turn 1
   |  receive sim id           │
   |                          │
   |  WS open ────────────────►│
   |                           │  generate turn 1 (A)
   |  ← {turn, speaker, text, audio}
   |  [play audio]
   |  playback_done ─────────►│  generate turn 2 (B)
   |                           │
   |  ← {turn, speaker, text, audio}
   |  [play audio]
   |  playback_done ─────────►│  ...
   |                           │
   |  ← {type:"done"} ◄───────│  (all turns complete or stop)
```

## Turn-gated WebSocket protocol
Client → Server: `{type:"playback_done", turn:int}`, `{type:"stop"}`.
Server → Client: `{type:"turn", turn, speaker, text, audio:<base64 WAV>, format:"wav/16000"}`,
                  `{type:"done", reason:"completed"|"stopped"|"error"}`.
Flow:
1. User types prompt text in browser.
2. `POST /api/simulations` (with prompt text, personas, turns) → id.
3. Open `WS /api/simulations/{id}/stream`.
4. Server sends turn 1 immediately; client plays it and sends `playback_done` on audio end;
   server then produces turn 2; … until `turns` is reached, `stop`, or `error`.

## Future optimization (NOT in v1)
Overlap LLM(N+1) "thinking" with playback(N): `_llm_turn`/`_tts_turn` are kept as separate seams so we
can prefetch LLM(N+1) after LLM(N) and still gate TTS(N+1) on `playback_done(N)`. Only LLM compute
overlaps playback; at most one LLM `process()` and one TTS `process()` run at a time (no model concurrency).

## Files
Create:
- src/speech_to_speech/persona_sim/__init__.py
- src/speech_to_speech/persona_sim/engine.py
- src/speech_to_speech/persona_sim/config.py
- versus/server.py
- versus/index.html
- versus/app.js
- versus/style.css
- versus/requirements.txt
- versus/README.md
- tests/test_persona_sim_engine.py
Modify: none. Delete: none.

## Configuration (env vars)
SIM_LLM_URL (default `http://dldsky:8080`) — OpenAI-compatible chat completions endpoint
SIM_LLM_MODEL (default `gpt-4o`) — model name for chat completions requests
SIM_TTS_BACKEND (must be `omnivoice`), SIM_TTS_MODEL, SIM_TTS_DEVICE, SIM_TTS_DTYPE
SIM_CHAT_SIZE, SIM_PERSONAS_DIR, SIM_SHARED_LANGUAGE
SIM_MAX_REPLY_TOKENS (default 256) — max tokens per LLM response

## Run
uv run uvicorn --app-dir versus server:app --port 7861
