"""Duet engine: remote LLM + OmniVoice TTS.

Each turn flows through two stages:

1. **LLM call** — remote Chat Completions endpoint generates a persona response.
2. **TTS synthesis** — OmniVoice converts the response text into PCM audio.

The engine supports **incremental TTS**: LLM output is streamed sentence-by-
sentence to TTS as soon as each sentence completes, so audio synthesis begins
within ~1–2 seconds rather than waiting for the full LLM response.

The engine yields :class:`Turn` objects that carry the speaker identity,
text, and synthesized audio (int16 mono PCM at 16 kHz).

The conversation is seeded with a text prompt (v1). An STT trigger
(microphone-to-text) is planned as a future enhancement.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Event

import httpx
import numpy as np

from speech_to_speech.personas import Persona, load_personas
from speech_to_speech.pipeline.cancel_scope import CancelScope
from speech_to_speech.pipeline.messages import TTSInput
from speech_to_speech.TTS.omnivoice_handler import OmniVoiceTTSHandler

from .config import SimConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPELINE_SAMPLE_RATE = 16000
_TURN_TIMEOUT_S = 180.0

# Sentence boundary regex: period, question mark, or exclamation mark
# followed by whitespace, newline, or end-of-string.
_SENTENCE_BOUNDARY_RE = re.compile(r'([.!?])\s*(?=\S|$)')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """A single conversation turn produced by the duet engine.

    Attributes:
        speaker: Persona name of the speaking agent (e.g. ``"Alice"``, ``"Bob"``).
        text:  The spoken text (LLM-generated response).
        audio: Synthesized PCM audio as raw bytes — mono int16, 16 kHz.
        sample_rate: Audio sample rate (always 16000 for this engine).
    """

    speaker: str
    text: str
    audio: bytes  # int16 PCM, mono
    sample_rate: int = PIPELINE_SAMPLE_RATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def pcm16_to_wav(pcm: "bytes | np.ndarray", sample_rate: int = PIPELINE_SAMPLE_RATE) -> bytes:
    """Wrap int16 mono PCM samples (bytes or ndarray) in a minimal WAV container.

    Args:
        pcm: Raw mono int16 PCM data, or a NumPy array of dtype ``int16``.
        sample_rate: Sample rate in Hz (default 16000).

    Returns:
        A bytes object containing a valid WAV file.
    """
    if isinstance(pcm, np.ndarray):
        pcm = np.asarray(pcm, dtype=np.int16).reshape(-1).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Duet engine
# ---------------------------------------------------------------------------


class DuetEngine:
    """Orchestrates A → B → A persona conversations.

    For each turn the engine:

    1. Calls a remote Chat Completions endpoint with the persona's system
       prompt + conversation history + incoming text.
    2. Synthesizes the LLM response text into audio using OmniVoice.

    With **incremental TTS** (enabled by default), the LLM output is streamed
    sentence-by-sentence to TTS as soon as each sentence completes, so audio
    synthesis begins within ~1–2 seconds rather than waiting for the full LLM
    response.

    Attributes:
        personas:   Loaded persona definitions keyed by name.
        llm_url:    Remote LLM endpoint base URL.
        llm_model:  Model name for chat completions requests.
        tts_handler: Pre-initialized OmniVoice TTS handler.
        chat_history: Running conversation history for the LLM context.
        chat_size:  Maximum history entries to retain.
        max_reply_tokens: Max tokens in LLM responses.
        _stop:      Threading event for cancellation.
    """

    def __init__(
        self,
        personas: dict[str, Persona],
        *,
        llm_url: str,
        llm_model: str,
        tts_handler,
        chat_size: int = 20,
        max_reply_tokens: int = 256,
        shared_language: str | None = None,
    ) -> None:
        self.personas = personas
        self.llm_url = llm_url.rstrip("/")
        self.llm_model = llm_model
        self.tts_handler = tts_handler
        self.chat_size = max(0, chat_size)
        self.max_reply_tokens = max_reply_tokens
        self.shared_language = shared_language
        # Two separate histories — one for each persona.
        self.history_a: list[dict] = []
        self.history_b: list[dict] = []
        self._stop = Event()
        self._last_reply_text_value: str = ""

    # ── LLM streaming ─────────────────────────────────────────────────

    async def _stream_llm(
        self,
        persona: Persona,
        history: list[dict],
        other_persona: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield each LLM text delta as it arrives from the SSE stream.

        Raises:
            RuntimeError: If the LLM call fails.
        """
        system_content = (
            f"{persona.prompt}\n\n"
            "Keep your response brief — 100 words or less. "
            "Do not include reasoning, thinking traces, or internal monologue."
        )
        if other_persona:
            system_content += f"\n\nYou are having a conversation with {other_persona}."

        messages: list[dict] = [
            {"role": "system", "content": system_content},
        ] + history

        payload: dict = {
            "model": self.llm_model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.max_reply_tokens,
            "reasoning_effort": "none",
        }

        full_response_parts: list[str] = []

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.llm_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"LLM call failed with status {resp.status_code}"
                        )

                    async for line in resp.aiter_lines():
                        data_str = line
                        if data_str.startswith("data: "):
                            data_str = data_str[len("data: "):]
                        data_str = data_str.strip()

                        if not data_str or data_str == "[DONE]":
                            continue

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if not isinstance(event, dict):
                            continue

                        choices = event.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})

                        if isinstance(delta, dict):
                            content = delta.get("content") or delta.get("text")
                            reasoning = delta.get("reasoning")
                            reasoning_content = delta.get("reasoning_content")
                            if content is not None and isinstance(content, str) and content.strip():
                                full_response_parts.append(content)
                                yield content
                            if reasoning is not None and isinstance(reasoning, str) and reasoning.strip():
                                full_response_parts.append(reasoning)
                            if reasoning_content is not None and isinstance(reasoning_content, str) and reasoning_content.strip():
                                full_response_parts.append(reasoning_content)
                            continue

                        message = choice.get("message", {})
                        if isinstance(message, dict):
                            content = message.get("content") or message.get("text")
                            if content is not None and isinstance(content, str):
                                full_response_parts.append(content)
                            continue

                        content = choice.get("content") or choice.get("text")
                        if content is not None and isinstance(content, str):
                            full_response_parts.append(content)
                            continue

        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM call network error: {exc}") from exc

        reply = "".join(full_response_parts).strip()

        # Fallback to non-streaming if streaming returned nothing
        if not reply:
            logger.debug("Streaming yielded no content; falling back to non-streaming call")
            payload["stream"] = False
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        f"{self.llm_url}/v1/chat/completions",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        msg = data.get("choices", [{}])[0].get("message", {})
                        reply = msg.get("content", "") or msg.get("text", "") or ""
                        reasoning = msg.get("reasoning")
                        reasoning_content = msg.get("reasoning_content")
                        if reasoning:
                            reply = (reply + " " + reasoning).strip()
                        if reasoning_content:
                            reply = (reply + " " + reasoning_content).strip()
                    else:
                        raise RuntimeError(f"Non-streaming call failed with status {resp.status_code}")
            except httpx.HTTPError as exc:
                logger.warning("Fallback non-streaming call failed: %s", exc)

        if not reply:
            raise RuntimeError("LLM returned empty response")

        self._last_reply_text_value = reply
        logger.info("LLM replied (%d chars): %r", len(reply), reply[:120])

    async def _stream_sentences(
        self,
        persona: Persona,
        history: list[dict],
        other_persona: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream LLM output, yielding complete sentences as they form.

        Buffers LLM text chunks and yields a sentence whenever a sentence
        boundary (``.``, ``!?``, followed by whitespace or end-of-text) is
        detected. The final partial sentence is yielded at the end.

        Yields:
            Complete sentence strings.
        """
        buffer = ""
        async for chunk in self._stream_llm(persona, history, other_persona):
            buffer += chunk
            # Split on sentence boundaries
            parts = _SENTENCE_BOUNDARY_RE.split(buffer)
            # parts = [text_before_first, boundary_char, text_after, ...]
            i = 0
            while i < len(parts) - 1:
                sentence_text = parts[i].strip()
                boundary = parts[i + 1]
                if sentence_text:
                    yield sentence_text + boundary
                i += 2
            buffer = parts[-1] if parts else ""

        remainder = buffer.strip()
        if remainder:
            yield remainder

    # ── single turn (incremental TTS) ─────────────────────────────────

    async def _run_turn(
        self,
        speaker: str,
        history: list[dict],
        other_persona: str | None = None,
    ) -> tuple[bytes, str]:
        """Execute one persona turn with incremental TTS streaming.

        LLM output is streamed sentence-by-sentence to TTS as soon as each
        sentence completes. Audio synthesis begins within ~1-2 seconds rather
        than waiting for the full LLM response.

        Args:
            speaker:       Persona name to use.
            history:       The conversation history list for this persona.
            other_persona: Name of the other participant (optional).

        Returns:
            Tuple of ``(pcm_audio_bytes, reply_text)``.

        Raises:
            RuntimeError: If TTS synthesis fails or LLM returns empty.
        """
        persona = self.personas[speaker]
        language = self.shared_language or persona.language

        # Switch to the speaker's voice
        self.tts_handler.switch_persona(speaker)

        # Stream sentences and synthesize TTS incrementally
        audio_chunks: list[bytes] = []
        full_text_parts: list[str] = []
        sentence_count = 0

        try:
            async for sentence in self._stream_sentences(
                persona, history, other_persona
            ):
                full_text_parts.append(sentence)
                logger.debug("TTS sentence %d: %r", sentence_count + 1, sentence[:80])

                # Synthesize TTS for this sentence immediately
                try:
                    for chunk in self.tts_handler.process(
                        TTSInput(text=sentence, language_code=language)
                    ):
                        audio_chunks.append(
                            np.asarray(chunk, dtype=np.int16).tobytes()
                        )
                except Exception as exc:
                    logger.error(
                        "TTS synthesis failed for sentence %d, "
                        "speaker %r, text %r: %s",
                        sentence_count + 1,
                        speaker,
                        sentence[:50],
                        exc,
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"TTS synthesis failed for sentence {sentence_count + 1}: {exc}"
                    ) from exc

                sentence_count += 1

        except RuntimeError:
            raise

        reply_text = "".join(full_text_parts).strip()

        if not reply_text:
            raise RuntimeError("LLM returned empty response")

        audio = b"".join(audio_chunks)
        logger.info(
            "TTS produced %d bytes from %d sentence(s) for speaker %r",
            len(audio),
            sentence_count,
            speaker,
        )
        if not audio:
            logger.error("TTS produced empty audio for speaker %r", speaker)
            raise RuntimeError(f"TTS produced no audio for speaker {speaker!r}")
        return audio, reply_text

    async def _run_turn_streaming(
        self,
        speaker: str,
        history: list[dict],
        other_persona: str | None = None,
    ) -> AsyncIterator[tuple[str, bytes]]:
        """Execute one turn, yielding (text, audio_chunk) incrementally.

        Yields the full text and accumulated audio after each sentence
        completes, allowing the server to stream audio to the client as
        it's synthesized.

        Yields:
            Tuples of ``(text_so_far, audio_chunk_bytes)``.
        """
        persona = self.personas[speaker]
        language = self.shared_language or persona.language

        self.tts_handler.switch_persona(speaker)

        audio_chunks: list[bytes] = []
        full_text_parts: list[str] = []
        sentence_count = 0

        try:
            async for sentence in self._stream_sentences(
                persona, history, other_persona
            ):
                full_text_parts.append(sentence)
                logger.debug("TTS sentence %d: %r", sentence_count + 1, sentence[:80])

                # Synthesize TTS for this sentence immediately
                sentence_audio = b""
                try:
                    for chunk in self.tts_handler.process(
                        TTSInput(text=sentence, language_code=language)
                    ):
                        int16_bytes = np.asarray(chunk, dtype=np.int16).tobytes()
                        audio_chunks.append(int16_bytes)
                        sentence_audio += int16_bytes
                except Exception as exc:
                    logger.error(
                        "TTS synthesis failed for sentence %d, "
                        "speaker %r, text %r: %s",
                        sentence_count + 1,
                        speaker,
                        sentence[:50],
                        exc,
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"TTS synthesis failed for sentence {sentence_count + 1}: {exc}"
                    ) from exc

                sentence_count += 1
                # Yield the accumulated text and audio for this sentence
                yield " ".join(full_text_parts), sentence_audio

        except RuntimeError:
            raise

    # ── main loop ─────────────────────────────────────────────────────

    async def run(
        self,
        persona_a: str,
        persona_b: str,
        topic: str,
        turns: int,
    ) -> AsyncIterator[Turn]:
        """Run a two-persona conversation, yielding one :class:`Turn` at a time.

        The speakers alternate: A, B, A, B, …  The first turn is seeded with
        the given *topic*; subsequent turns use the previous response as context.

        Args:
            persona_a: Name of the first persona.
            persona_b: Name of the second persona.
            topic:     Seed text for the opening turn (the prompt).
            turns:     Maximum number of turns to produce.

        Yields:
            :class:`Turn` objects for each speaker's response.

        Raises:
            ValueError: If either persona name is unknown.
        """
        # Validate personas
        if persona_a not in self.personas:
            raise ValueError(
                f"Unknown persona {persona_a!r}; available: {sorted(self.personas)}"
            )
        if persona_b not in self.personas:
            raise ValueError(
                f"Unknown persona {persona_b!r}; available: {sorted(self.personas)}"
            )
        if turns < 1:
            return

        # Two separate histories — one per persona.
        self.history_a: list[dict] = []
        self.history_b: list[dict] = []

        for index in range(turns):
            if self._stop.is_set():
                return

            if index % 2 == 0:
                speaker = persona_a
            else:
                speaker = persona_b

            if index % 2 == 0:
                history = self.history_a
                cross_history = self.history_b
                other_persona = persona_b
            else:
                history = self.history_b
                cross_history = self.history_a
                other_persona = persona_a

            try:
                audio, reply_text = await asyncio.wait_for(
                    self._run_turn(speaker, history, other_persona),
                    timeout=_TURN_TIMEOUT_S,
                )
            except (RuntimeError, Exception) as exc:
                logger.error("Turn %d (%s) failed: %s", index + 1, speaker, exc)
                return

            if not reply_text.strip():
                return

            history.append({"role": "assistant", "content": reply_text})
            cross_history.append({"role": "user", "content": reply_text})

            if len(self.history_a) > self.chat_size:
                self.history_a = self.history_a[-self.chat_size:]
            if len(self.history_b) > self.chat_size:
                self.history_b = self.history_b[-self.chat_size:]

            yield Turn(
                speaker=speaker,
                text=reply_text,
                audio=audio,
                sample_rate=PIPELINE_SAMPLE_RATE,
            )

            if index == turns - 1:
                return

    def stop(self) -> None:
        """Signal the running conversation to stop after the current turn."""
        self._stop.set()


# ---------------------------------------------------------------------------
# Build function
# ---------------------------------------------------------------------------


def build_engine(config: SimConfig) -> DuetEngine:
    """Construct a :class:`DuetEngine` from a :class:`SimConfig`.

    Loads personas and initializes the OmniVoice TTS handler.
    All models are loaded eagerly during this call.

    Args:
        config: Configuration carrying model paths, device, and persona dir.

    Returns:
        A fully-initialized :class:`DuetEngine`.

    Raises:
        ValueError: If ``config.tts_backend`` is not ``"omnivoice"``.
        RuntimeError: If model loading fails.
    """
    if config.tts_backend != "omnivoice":
        raise ValueError(
            f"persona_sim only supports 'omnivoice' TTS backend, got: {config.tts_backend!r}"
        )

    personas = load_personas(config.personas_dir)
    if not personas:
        raise RuntimeError(
            f"No personas loaded from {config.personas_dir!r}; "
            "check that the directory contains persona sub-directories "
            "with prompt.md and language.txt files."
        )
    logger.info("Loaded %d persona(s)", len(personas))

    cancel_scope = CancelScope()
    should_listen = Event()

    tts_handler = OmniVoiceTTSHandler(
        stop_event=Event(),
        queue_in=None,
        queue_out=None,
        setup_args=(should_listen,),
        setup_kwargs={
            "model_name": config.tts_model,
            "device": config.tts_device,
            "dtype": config.tts_dtype,
            "personas": personas,
            "num_steps": config.tts_num_steps,
            "speed": config.tts_speed,
            "cancel_scope": cancel_scope,
        },
    )
    logger.info("OmniVoice TTS handler initialized (model=%s)", config.tts_model)

    return DuetEngine(
        personas=personas,
        llm_url=config.llm_url,
        llm_model=config.llm_model,
        tts_handler=tts_handler,
        chat_size=config.chat_size,
        max_reply_tokens=config.max_reply_tokens,
        shared_language=config.shared_language,
    )
