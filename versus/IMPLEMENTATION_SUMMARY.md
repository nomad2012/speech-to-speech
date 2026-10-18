# Persona Versus Duet — Implementation Summary

## Overview

Built a **two-persona conversation simulation engine** with a browser-based
real-time client. Users select two personas, provide a topic or prompt, and
watch/listen to the personas debate, discuss, or riff on the topic in real time.

Each persona speaks with their own cloned voice (via OmniVoice TTS). The
conversation alternates A → B → A → B for a fixed number of turns.

## Files Added or Modified

### New Files

| File | Description |
|------|-------------|
| `src/speech_to_speech/persona_sim/__init__.py` | Package init, exports `SimConfig`, `DuetEngine`, `build_engine` |
| `src/speech_to_speech/persona_sim/config.py` | `SimConfig` dataclass — configuration via env vars |
| `src/speech_to_speech/persona_sim/engine.py` | `DuetEngine` class — the core simulation loop |
| `versus/README.md` | User-facing documentation |
| `versus/server.py` | FastAPI server with REST + WebSocket endpoints |
| `versus/app.js` | Browser client — persona selection, WebSocket handling, audio playback |
| `versus/index.html` | UI with persona selectors, topic input, conversation display |
| `versus/style.css` | Dark-themed responsive styling |
| `versus/requirements.txt` | Python dependencies (FastAPI, httpx, etc.) |

### Modified Files

| File | Change |
|------|--------|
| `Personas/Harvey/prompt.md` | Updated system prompt (brevity constraint) |
| `Personas/Marvin/prompt.md` | Updated system prompt (brevity constraint) |

## Architecture

```
Browser              Versus Server           Speech-to-Speech Package
   |                      |                           |
   |  type prompt text ───►│                           │
   |                      │                           │
   |  WS stream ─────────►│  Remote LLM (Chat Comp.)  │
   |  ◄── turn + audio ───│  OmniVoice TTS (local)    │
   |                      │◄──────────────────────────│
```

The versus app is a **separate process** (`versus/server.py`) running on its own
port (default 7861). It is not part of the original realtime server. Both
processes share infrastructure: the OmniVoice TTS handler.

## Core Components

### 1. `SimConfig` (config.py)

Configuration dataclass loaded from environment variables with defaults:

| Env Var | Default | Description |
|---------|---------|-------------|
| `SIM_LLM_URL` | `http://dldsky:8080` | Remote LLM Chat Completions URL |
| `SIM_LLM_MODEL` | `gpt-4o` | Model name for LLM requests |
| `SIM_TTS_MODEL` | `k2-fsa/OmniVoice` | OmniVoice model |
| `SIM_TTS_DEVICE` | `auto` | Device for TTS inference |
| `SIM_TTS_DTYPE` | `float16` | Torch dtype |
| `SIM_CHAT_SIZE` | `20` | Max conversation history entries |
| `SIM_PERSONAS_DIR` | `Personas` | Persona definitions directory |
| `SIM_MAX_REPLY_TOKENS` | `256` | Max tokens per LLM response |

### 2. `DuetEngine` (engine.py)

The core engine orchestrating A → B → A conversations:

- **Two separate conversation histories** — one per persona
- **Cross-over**: each persona's assistant reply goes to their own history as
  `"assistant"` and to the other persona's history as `"user"`
- **Brevity constraint** (100 words max) injected into system prompts
- **Dynamic "You are having a conversation with X"** note added to each
  persona's system prompt
- **Reasoning model support**: collects `content`, `reasoning`, and
  `reasoning_content` fields; sets `reasoning_effort=none`
- **Per-persona voice cloning** via OmniVoice TTS with `switch_persona()`
- **SSE streaming** from LLM with fallback to non-streaming call
- **Turn timeout** (180s) and cancellation support via `stop()`

### 3. FastAPI Server (versus/server.py)

Endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/personas` | List available personas |
| POST | `/api/simulations` | Create and start a simulation |
| GET | `/api/simulations/{id}` | Get simulation status |
| WS | `/api/simulations/{id}/stream` | WebSocket turn-by-turn stream |

Serves static files (HTML/JS/CSS) from the `versus/` directory.

### 4. Browser Client (versus/app.js)

- Loads personas from `GET /api/personas`
- User selects two different personas + topic
- `POST /api/simulations` to start
- Opens WebSocket, receives turns, plays audio, sends `playback_done`
- Waits for server's `done` message

## WebSocket Protocol

| Direction | Message | Description |
|-----------|---------|-------------|
| Server → Client | `{"type": "turn", "turn": N, "speaker": "...", "text": "...", "audio": "<base64>", "format": "wav/16000"}` | Turn data |
| Client → Server | `{"type": "playback_done", "turn": N}` | Audio finished playing |
| Client → Server | `{"type": "stop"}` | Stop the simulation |
| Server → Client | `{"type": "done", "reason": "completed"\|"stopped"\|"error"}` | Simulation ended |

Audio is encoded as base64 WAV containers (PCM16 mono, 16 kHz).

## Simulation Flow

1. Server sends Turn 1 (Persona A's response with audio)
2. Browser plays the audio
3. When playback ends, browser sends `playback_done`
4. Server generates Turn 2 (Persona B's response)
5. Steps 1-4 repeat for the configured number of turns
6. Final `done` message signals the end

## Running It

```bash
pip install -r versus/requirements.txt
uv run uvicorn --app-dir versus server:app --port 7861
```

Then open `http://localhost:7861` in a browser.

## Future Enhancements

- **STT trigger**: Replace text input with microphone capture + STT
  transcription (likely using Parakeet STT from the realtime server)
- **Overlap optimization**: Prefetch LLM(N+1) while playing audio(N)

## Key Design Decisions

1. **Separate process**: The versus app is isolated from the realtime server
   to avoid interference with existing functionality.
2. **OmniVoice TTS**: Only TTS backend supported (reasoning: best voice
   cloning quality for persona impersonation).
3. **Two separate histories**: Each persona maintains their own conversation
   history, enabling natural context for each speaker.
4. **Synchronous turn-by-turn**: The server waits for `playback_done` before
   generating the next turn, ensuring audio playback stays in sync with
   conversation flow.
5. **WAV container encoding**: Base64-encoded WAV files for browser-compatible
   audio (Web Audio API `Audio` element).
