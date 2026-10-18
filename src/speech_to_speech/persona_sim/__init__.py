"""Remote LLM + local STT + OmniVoice TTS persona-versus duet engine.

This package replaces the old WebSocket-based design (which drove a running
Realtime server) with a fully standalone approach:

* **LLM** — remote Chat Completions endpoint (OpenAI-compatible), streaming via
  Server-Sent Events (``httpx``).
* **STT** — local Whisper model for transcribing speaker audio input.
* **TTS** — OmniVoice model for synthesizing persona responses.

Usage::

    from speech_to_speech.persona_sim import SimConfig, build_engine

    config = SimConfig.from_env()
    engine = build_engine(config)

    async for turn in engine.run("Alice", "Bob", "Debate AI safety", turns=6):
        print(f"[{turn.speaker}] {turn.text}")
        # play or save turn.audio (int16 mono PCM, 16 kHz)
"""

from .config import SimConfig
from .engine import Turn, DuetEngine, build_engine, pcm16_to_wav

__all__ = ["SimConfig", "Turn", "DuetEngine", "build_engine", "pcm16_to_wav"]
