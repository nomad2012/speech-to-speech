"""Configuration for the persona-versus duet app.

Uses these environment variables:

* ``SIM_LLM_URL`` (default ``http://dldsky:8080``) — OpenAI-compatible chat
  completions endpoint (e.g. ``http://localhost:8000`` or ``https://api.openai.com``).
* ``SIM_LLM_MODEL`` (default ``gpt-4o``) — model name to pass in the
  ``/v1/chat/completions`` request.
* ``SIM_TTS_BACKEND`` (must be ``omnivoice``) — TTS backend identifier.
* ``SIM_TTS_MODEL`` — Hugging Face model ID or local path for OmniVoice
  (same as ``speech-to-speech serve --tts-backend omnivoice``).
* ``SIM_TTS_DEVICE`` — device for OmniVoice inference (e.g. ``auto``, ``cuda``,
  ``cpu``; same as ``speech-to-speech serve``).
* ``SIM_TTS_DTYPE`` — torch dtype for OmniVoice (``float16``, ``bfloat16``, or
  ``float32``; default ``float16``).
* ``SIM_TTS_NUM_STEPS`` — diffusion decoding steps for OmniVoice (default 32).
* ``SIM_TTS_SPEED`` — speaking-speed factor (default 1.0).
* ``SIM_CHAT_SIZE`` — maximum number of conversation-history turns to keep
  around for the LLM context window (default 20).
* ``SIM_PERSONAS_DIR`` — directory with persona definitions (``prompt.md``,
  ``language.txt``, optional ``ref.txt`` / ``ref.wav`` per persona).
* ``SIM_SHARED_LANGUAGE`` — optional shared language code; when set the engine
  overrides per-persona language for TTS.
* ``SIM_MAX_REPLY_TOKENS`` — maximum tokens in the LLM response (default 256).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

_DEFAULT_PERSONAS_DIR = "Personas"
_DEFAULT_LLM_URL = "http://dldsky:8080"
_DEFAULT_LLM_MODEL = "gpt-4o"
_DEFAULT_TTS_MODEL = "k2-fsa/OmniVoice"
_DEFAULT_TTS_DEVICE = "auto"
_DEFAULT_TTS_DTYPE = "float16"
_DEFAULT_TTS_NUM_STEPS = 32
_DEFAULT_TTS_SPEED = 1.0
_DEFAULT_CHAT_SIZE = 20
_DEFAULT_MAX_REPLY_TOKENS = 256


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is not None:
        return int(value)
    return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is not None:
        return float(value)
    return default


@dataclass
class SimConfig:
    """Configuration for the persona-versus duet engine.

    All fields have sensible defaults that can be overridden via environment
    variables (see module docstring).
    """

    # ── LLM ───────────────────────────────────────────────────────────
    #: Remote Chat Completions endpoint (e.g. ``http://dldsky:8080``).
    llm_url: str = _DEFAULT_LLM_URL

    #: Model name to include in the ``/v1/chat/completions`` request body.
    llm_model: str = _DEFAULT_LLM_MODEL

    #: Maximum tokens in the LLM response (response length limit).
    max_reply_tokens: int = _DEFAULT_MAX_REPLY_TOKENS

    # ── TTS (OmniVoice) ──────────────────────────────────────────────
    #: TTS backend — must be ``"omnivoice"``.
    tts_backend: str = "omnivoice"

    #: Hugging Face model ID or local path for OmniVoice.
    tts_model: str = _DEFAULT_TTS_MODEL

    #: Torch device for the TTS model.
    tts_device: str = _DEFAULT_TTS_DEVICE

    #: Torch dtype for the TTS model.
    tts_dtype: str = _DEFAULT_TTS_DTYPE

    #: Diffusion decoding steps for OmniVoice generation.
    tts_num_steps: int = _DEFAULT_TTS_NUM_STEPS

    #: Speaking-speed factor (``>1`` faster, ``<1`` slower).
    tts_speed: float = _DEFAULT_TTS_SPEED

    # ── Shared settings ──────────────────────────────────────────────
    #: Maximum number of chat-history entries to keep in the LLM context.
    chat_size: int = _DEFAULT_CHAT_SIZE

    #: Directory containing persona sub-directories.
    personas_dir: str = _DEFAULT_PERSONAS_DIR

    #: Shared language code (e.g. ``"en"``) that overrides per-persona
    #: language for TTS.  ``None`` means use each persona's declared language.
    shared_language: Optional[str] = None

    @classmethod
    def from_mapping(cls, data: dict) -> "SimConfig":
        """Build a config from a mapping (e.g. parsed YAML or JSON).

        Missing keys fall back to environment variables or built-in defaults.
        """
        return cls(
            llm_url=data.get("llm_url") or _env_str("SIM_LLM_URL", _DEFAULT_LLM_URL),
            llm_model=data.get("llm_model") or _env_str("SIM_LLM_MODEL", _DEFAULT_LLM_MODEL),
            max_reply_tokens=int(
                data.get("max_reply_tokens") or _env_int("SIM_MAX_REPLY_TOKENS", _DEFAULT_MAX_REPLY_TOKENS)
            ),
            tts_backend=data.get("tts_backend") or "omnivoice",
            tts_model=data.get("tts_model") or _env_str("SIM_TTS_MODEL", _DEFAULT_TTS_MODEL),
            tts_device=data.get("tts_device") or _env_str("SIM_TTS_DEVICE", _DEFAULT_TTS_DEVICE),
            tts_dtype=data.get("tts_dtype") or _env_str("SIM_TTS_DTYPE", _DEFAULT_TTS_DTYPE),
            tts_num_steps=int(data.get("tts_num_steps") or _env_int("SIM_TTS_NUM_STEPS", _DEFAULT_TTS_NUM_STEPS)),
            tts_speed=float(data.get("tts_speed") or _env_float("SIM_TTS_SPEED", _DEFAULT_TTS_SPEED)),
            chat_size=int(data.get("chat_size") or _env_int("SIM_CHAT_SIZE", _DEFAULT_CHAT_SIZE)),
            personas_dir=data.get("personas_dir") or _env_str("SIM_PERSONAS_DIR", _DEFAULT_PERSONAS_DIR),
            shared_language=data.get("shared_language") or _env_str("SIM_SHARED_LANGUAGE", None),
        )

    @classmethod
    def from_env(cls) -> "SimConfig":
        """Build a config purely from environment variables (with defaults)."""
        return cls(
            llm_url=_env_str("SIM_LLM_URL", _DEFAULT_LLM_URL),
            llm_model=_env_str("SIM_LLM_MODEL", _DEFAULT_LLM_MODEL),
            max_reply_tokens=_env_int("SIM_MAX_REPLY_TOKENS", _DEFAULT_MAX_REPLY_TOKENS),
            tts_backend="omnivoice",
            tts_model=_env_str("SIM_TTS_MODEL", _DEFAULT_TTS_MODEL),
            tts_device=_env_str("SIM_TTS_DEVICE", _DEFAULT_TTS_DEVICE),
            tts_dtype=_env_str("SIM_TTS_DTYPE", _DEFAULT_TTS_DTYPE),
            tts_num_steps=_env_int("SIM_TTS_NUM_STEPS", _DEFAULT_TTS_NUM_STEPS),
            tts_speed=_env_float("SIM_TTS_SPEED", _DEFAULT_TTS_SPEED),
            chat_size=_env_int("SIM_CHAT_SIZE", _DEFAULT_CHAT_SIZE),
            personas_dir=_env_str("SIM_PERSONAS_DIR", _DEFAULT_PERSONAS_DIR),
            shared_language=_env_str("SIM_SHARED_LANGUAGE", None) or None,
        )
