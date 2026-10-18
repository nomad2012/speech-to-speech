# Persona Versus Duet

A standalone web app that lets you pick **two personas** and watch/listen to them
talk to each other. You type a topic or prompt, and the personas begin debating,
discussing, or riffing on whatever you said.

Each persona speaks with their own cloned voice (via OmniVoice TTS). The
conversation flows A → B → A → B for a fixed number of turns, and you hear
every reply in real time.

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

The versus app is a **separate server** (`versus/server.py`) that runs on its
own port (default 7861). It is not part of the original realtime server. They
are isolated processes but share infrastructure: the OmniVoice TTS handler.

## Quick Start

### Prerequisites

- Python 3.10+
- A remote LLM exposing an OpenAI-compatible Chat Completions API (e.g.
  `http://dldsky:8080`)
- Persona definitions in a `Personas/` directory

### 1. Create personas

Each persona is a subdirectory containing:

| File         | Required | Description                         |
|-------------|----------|-------------------------------------|
| `prompt.md` | Yes      | YAML frontmatter + system prompt    |
| `language.txt` | Yes   | Language code (e.g. `en`, `es`)    |
| `ref.txt`   | Optional | Voice clone reference text          |
| `ref.wav`   | Optional | Voice clone reference audio         |
| `image.jpg` | Optional | Avatar image                        |

Example persona directory:

```
Personas/
├── Einstein/
│   ├── prompt.md
│   ├── language.txt
│   ├── ref.txt
│   └── ref.wav
└── Bohr/
    ├── prompt.md
    ├── language.txt
    ├── ref.txt
    └── ref.wav
```

### 2. Configure

Set environment variables (or use the defaults):

| Variable              | Default               | Description                          |
|----------------------|-----------------------|--------------------------------------|
| `SIM_LLM_URL`        | `http://dldsky:8080`  | Remote LLM Chat Completions URL      |
| `SIM_LLM_MODEL`      | `gpt-4o`              | Model name for LLM requests          |
| `SIM_TTS_MODEL`      | `k2-fsa/OmniVoice`    | OmniVoice model                      |
| `SIM_TTS_DEVICE`     | `auto`                | Device for TTS inference             |
| `SIM_TTS_DTYPE`      | `float16`             | Torch dtype for TTS                  |
| `SIM_CHAT_SIZE`      | `20`                  | Max conversation history entries     |
| `SIM_PERSONAS_DIR`   | `Personas`            | Directory with persona definitions  |
| `SIM_SHARED_LANGUAGE`| *(none)*              | Override language for TTS            |
| `SIM_MAX_REPLY_TOKENS`| `256`                | Max tokens per LLM response          |

### 3. Install dependencies

```bash
pip install -r versus/requirements.txt
```

### 4. Run the server

```bash
uv run uvicorn --app-dir versus server:app --port 7861
```

Then open `http://localhost:7861` in your browser.

## How It Works

### Text Prompt (v1)

1. Open the web app.
2. Select **Persona A** and **Persona B** from the dropdowns.
3. Type a topic or prompt in the text field.
4. Click **Start Duet**.

### Simulation Loop

1. Server sends Turn 1 (Persona A's response with audio).
2. Browser plays the audio.
3. When playback ends, browser sends `playback_done`.
4. Server generates Turn 2 (Persona B's response).
5. Steps 1-4 repeat for the configured number of turns.
6. Final `done` message signals the end.

### WebSocket Protocol

| Direction | Message | Description |
|-----------|---------|-------------|
| Client → Server | *(simulation created via POST)* | No message needed on WS |
| Server → Client | `{"type": "turn", "turn": N, "speaker": "...", "text": "...", "audio": "<base64>", "format": "wav/16000"}` | Turn data |
| Client → Server | `{"type": "playback_done", "turn": N}` | Audio finished playing |
| Client → Server | `{"type": "stop"}` | Stop the simulation |
| Server → Client | `{"type": "done", "reason": "completed"\|"stopped"\|"error"}` | Simulation ended |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/personas` | List available personas |
| POST | `/api/simulations` | Create and start a new simulation |
| GET | `/api/simulations/{id}` | Get simulation status |
| WS | `/api/simulations/{id}/stream` | WebSocket turn-by-turn stream |

## Technical Details

### persona_sim Package

The `src/speech_to_speech/persona_sim/` package provides the core engine:

- **`SimConfig`** — configuration dataclass loaded from environment variables
- **`DuetEngine`** — orchestrates the A → B → A conversation loop
- **`build_engine()`** — constructs and initializes the engine (loads models)
- **`pcm16_to_wav()`** — wraps PCM audio in a WAV container

### Engine Flow

```
Text prompt (topic)
    ↓
LLM call → Remote Chat Completions → response text
    ↓
TTS synthesis → OmniVoice → PCM audio
    ↓
Yield Turn(speaker, text, audio) → WebSocket → Browser → Play → playback_done
```

### Remote LLM

The engine calls the remote LLM via the OpenAI-compatible Chat Completions API
(`/v1/chat/completions`). Each turn builds a conversation from:

1. Persona's system prompt (system message)
2. Accumulated chat history
3. Current user text (user message)

The response is streamed via Server-Sent Events and collected into the final
text.

### TTS

Each turn uses OmniVoice TTS with the speaker's pre-built voice clone prompt.
The TTS handler is initialized at engine startup with all personas' voice
prompts pre-built for fast switching.

## Future Enhancements

- **STT trigger**: Replace text input with microphone capture + STT transcription
  (likely using Parakeet STT from the realtime server)
- **Overlap optimization**: Prefetch LLM(N+1) while playing audio(N)

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Engine fails to start | No personas loaded | Check `SIM_PERSONAS_DIR` contains valid persona dirs |
| TTS fails | GPU out of memory | Try `SIM_TTS_DTYPE=float32` or a smaller model |
| LLM returns errors | Wrong URL/model | Verify `SIM_LLM_URL` and `SIM_LLM_MODEL` |
| Audio doesn't play | Browser policy | Ensure user interaction precedes audio play |
