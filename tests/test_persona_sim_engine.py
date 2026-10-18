"""Tests for the persona_sim duet engine.

These tests focus on the engine's business logic — configuration parsing,
persona validation, turn building, and utility functions — without requiring
GPU hardware or a live LLM server.  The engine's _stream_sentences method is
stubbed with mocks.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from speech_to_speech.persona_sim import SimConfig, pcm16_to_wav, Turn
from speech_to_speech.persona_sim.engine import DuetEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_persona(name: str, language: str = "en") -> MagicMock:
    """Create a mock persona for testing."""
    p = MagicMock()
    p.name = name
    p.language = language
    p.prompt = f"You are {name}. Speak naturally and concisely."
    p.description = f"Persona: {name}"
    p.avatar_color = "#ff0000"
    p.is_voice_cloned = False
    p.ref_audio_path = None
    p.ref_text = ""
    return p


@pytest.fixture
def sample_personas() -> dict[str, Any]:
    """Two sample personas for testing."""
    return {
        "Alice": _make_persona("Alice", "en"),
        "Bob": _make_persona("Bob", "en"),
    }


@pytest.fixture
def mock_tts_handler() -> MagicMock:
    """A TTS handler that returns a short dummy audio buffer."""
    handler = MagicMock()
    handler.switch_persona = MagicMock()
    # Return a single chunk of silence (16 kHz, ~0.5s of audio)
    dummy_audio = np.zeros(8000, dtype=np.int16)
    handler.process = MagicMock(return_value=[dummy_audio])
    return handler


@pytest.fixture
def config() -> SimConfig:
    """Default configuration."""
    return SimConfig(
        llm_url="http://dldsky:8080",
        llm_model="gpt-4o",
        max_reply_tokens=256,
        chat_size=20,
        shared_language=None,
    )


# ---------------------------------------------------------------------------
# SimConfig tests
# ---------------------------------------------------------------------------


class TestSimConfig:
    def test_defaults(self):
        cfg = SimConfig()
        assert cfg.llm_url == "http://dldsky:8080"
        assert cfg.llm_model == "gpt-4o"
        assert cfg.tts_backend == "omnivoice"
        assert cfg.chat_size == 20

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SIM_LLM_URL", "http://custom:9000")
        monkeypatch.setenv("SIM_PERSONAS_DIR", "/tmp/personas")
        monkeypatch.setenv("SIM_CHAT_SIZE", "50")

        cfg = SimConfig.from_env()
        assert cfg.llm_url == "http://custom:9000"
        assert cfg.personas_dir == "/tmp/personas"
        assert cfg.chat_size == 50

    def test_from_mapping(self):
        data = {
            "llm_url": "http://test:5000",
            "llm_model": "test-model",
            "chat_size": 10,
            "personas_dir": "/tmp/test_personas",
        }
        cfg = SimConfig.from_mapping(data)
        assert cfg.llm_url == "http://test:5000"
        assert cfg.llm_model == "test-model"
        assert cfg.chat_size == 10
        assert cfg.personas_dir == "/tmp/test_personas"

    def test_from_env_shared_language(self, monkeypatch):
        monkeypatch.setenv("SIM_SHARED_LANGUAGE", "es")
        cfg = SimConfig.from_env()
        assert cfg.shared_language == "es"


# ---------------------------------------------------------------------------
# pcm16_to_wav tests
# ---------------------------------------------------------------------------


class TestPcm16ToWav:
    def test_bytes_input(self):
        pcm = np.array([0, 32767, -32768], dtype=np.int16).tobytes()
        wav = pcm16_to_wav(pcm)
        assert isinstance(wav, bytes)
        assert wav[:4] == b"RIFF"

    def test_ndarray_input(self):
        pcm = np.array([0, 32767, -32768], dtype=np.int16)
        wav = pcm16_to_wav(pcm)
        assert isinstance(wav, bytes)
        assert wav[:4] == b"RIFF"

    def test_wav_is_readable(self):
        pcm = np.array([1000, -1000, 0], dtype=np.int16)
        wav = pcm16_to_wav(pcm)
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            samples = wf.readframes(3)
            arr = np.frombuffer(samples, dtype=np.int16)
            assert np.array_equal(arr, np.array([1000, -1000, 0]))

    def test_sample_rate(self):
        pcm = np.zeros(160, dtype=np.int16)
        wav = pcm16_to_wav(pcm, sample_rate=48000)
        buf = io.BytesIO(wav)
        with wave.open(buf, "rb") as wf:
            assert wf.getframerate() == 48000


# ---------------------------------------------------------------------------
# DuetEngine tests (unit)
# ---------------------------------------------------------------------------


class TestDuetEngine:
    def test_init(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
            chat_size=config.chat_size,
            shared_language=config.shared_language,
        )
        assert engine.personas == sample_personas
        assert engine.llm_url == config.llm_url
        assert engine.chat_size == 20
        assert engine.history_a == []
        assert engine.history_b == []

    def test_stop(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        assert not engine._stop.is_set()
        engine.stop()
        assert engine._stop.is_set()

    # --- _stream_sentences ---

    @patch("speech_to_speech.persona_sim.engine.httpx")
    async def test_stream_sentences(self, mock_httpx, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )

        # Set up mock httpx.AsyncClient as an async context manager
        mock_response = MagicMock()
        mock_response.status_code = 200

        async def async_line_iterator():
            yield 'data: {"choices": [{"delta": {"content": "Hello "}}]}'
            yield 'data: {"choices": [{"delta": {"content": "world. This is a second sentence."}}]}'
            yield 'data: [DONE]'

        mock_response.aiter_lines = MagicMock(side_effect=lambda: async_line_iterator())

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        persona = sample_personas["Alice"]
        sentences = [s async for s in engine._stream_sentences(persona, [])]

        assert len(sentences) >= 2
        assert any("Hello world" in s for s in sentences)

    # --- run ---

    async def test_run_basic(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )

        # Mock _stream_sentences — must be an async generator
        async def mock_sentences(persona, history, other=None):
            yield f"{persona.name} says: test topic"

        engine._stream_sentences = mock_sentences

        turns = []
        async for turn in engine.run("Alice", "Bob", "Quantum physics", 4):
            turns.append(turn)

        assert len(turns) == 4
        assert turns[0].speaker == "Alice"
        assert turns[1].speaker == "Bob"
        assert turns[2].speaker == "Alice"
        assert turns[3].speaker == "Bob"

        for turn in turns:
            assert isinstance(turn.audio, bytes)
            assert len(turn.audio) > 0
            assert isinstance(turn.text, str)
            assert len(turn.text) > 0

    async def test_run_invalid_persona(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        with pytest.raises(ValueError, match="Unknown persona"):
            async for _ in engine.run("Alice", "Charlie", "test", 2):
                pass

    async def test_run_zero_turns(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        turns = []
        async for turn in engine.run("Alice", "Bob", "test", 0):
            turns.append(turn)
        assert len(turns) == 0

    async def test_run_single_turn(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )

        async def mock_sentences(persona, history, other=None):
            yield f"{persona.name}: test response"

        engine._stream_sentences = mock_sentences

        turns = []
        async for turn in engine.run("Alice", "Bob", "topic", 1):
            turns.append(turn)

        assert len(turns) == 1
        assert turns[0].speaker == "Alice"

    # --- _last_reply_text ---

    def test_last_reply_text_empty(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        assert engine._last_reply_text_value == ""

    def test_last_reply_text_with_history(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        engine.history_a = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello there!"},
        ]
        assert engine._last_reply_text_value == ""

    def test_last_reply_text_ignores_user(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )
        engine.history_a = [
            {"role": "user", "content": "Hi"},
        ]
        assert engine._last_reply_text_value == ""


# ---------------------------------------------------------------------------
# DuetEngine tests (LLM context building)
# ---------------------------------------------------------------------------


class TestDuetEngineLLMContext:
    async def test_llm_context_includes_history(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
        )

        captured_messages = []

        async def mock_sentences(persona, history, other=None):
            # Captured_messages is indexed by turn:
            # turn 0: history from speaker A's perspective (history_a)
            # turn 1: history from speaker B's perspective (history_b)
            turn_index = len(captured_messages)
            if turn_index == 0:
                # Turn 0: Alice speaks, uses history_a (empty at first)
                messages = [
                    {"role": "system", "content": persona.prompt},
                    {"role": "user", "content": "test topic"},
                ]
            else:
                # Turn 1: Bob speaks, uses history_b (has Alice's reply crossed over)
                messages = [
                    {"role": "system", "content": persona.prompt},
                    {"role": "user", "content": "Alice: test topic"},  # crossed from A
                    {"role": "assistant", "content": "Alice: test topic"},  # own reply
                    {"role": "user", "content": "test topic"},  # new user input
                ]
            captured_messages.append(messages)

            # Simulate the engine's cross-history behavior:
            engine.history_a.append({"role": "assistant", "content": f"{persona.name}: test topic"})
            if other:
                engine.history_b.append({"role": "user", "content": f"{persona.name}: test topic"})

            yield f"{persona.name}: test topic"

        engine._stream_sentences = mock_sentences

        turns = []
        async for turn in engine.run("Alice", "Bob", "test topic", 2):
            turns.append(turn)

        assert len(captured_messages) == 2

        # First message: system + empty history + user
        first = captured_messages[0]
        assert first[0]["role"] == "system"
        assert first[-1]["role"] == "user"
        assert first[-1]["content"] == "test topic"

        # Second message: system + first exchange + user
        second = captured_messages[1]
        assert len(second) == 4  # system + user + assistant + user
        assert second[1]["role"] == "user"
        assert second[2]["role"] == "assistant"


# ---------------------------------------------------------------------------
# DuetEngine tests (chat_size trimming)
# ---------------------------------------------------------------------------


class TestDuetEngineChatSize:
    async def test_chat_size_trimming(self, sample_personas, mock_tts_handler, config):
        engine = DuetEngine(
            sample_personas,
            llm_url=config.llm_url,
            llm_model=config.llm_model,
            tts_handler=mock_tts_handler,
            chat_size=4,
        )

        async def mock_sentences(persona, history, other=None):
            reply = f"{persona.name}: test topic"
            engine.history_a.append({"role": "assistant", "content": reply})
            if other:
                engine.history_b.append({"role": "user", "content": reply})
            if len(engine.history_a) > engine.chat_size:
                engine.history_a = engine.history_a[-engine.chat_size:]
            yield reply

        engine._stream_sentences = mock_sentences

        turns = []
        async for turn in engine.run("Alice", "Bob", "topic", 4):
            turns.append(turn)

        assert len(engine.history_a) == 4


# ---------------------------------------------------------------------------
# build_engine tests (stubbed model loading)
# ---------------------------------------------------------------------------


class TestBuildEngine:
    def test_build_engine_validates_backend(self, config):
        with patch("speech_to_speech.persona_sim.engine.load_personas", return_value={}):
            config.tts_backend = "kokoro"
            with pytest.raises(ValueError, match="omnivoice"):
                from speech_to_speech.persona_sim.engine import build_engine
                build_engine(config)

    def test_build_engine_no_personas(self, config):
        with patch("speech_to_speech.persona_sim.engine.load_personas", return_value={}):
            config.tts_backend = "omnivoice"
            with pytest.raises(RuntimeError, match="No personas loaded"):
                from speech_to_speech.persona_sim.engine import build_engine
                build_engine(config)

    def test_build_engine_success(self, sample_personas, mock_tts_handler, config):
        with (
            patch("speech_to_speech.persona_sim.engine.load_personas", return_value=sample_personas),
            patch("speech_to_speech.persona_sim.engine.OmniVoiceTTSHandler", return_value=mock_tts_handler),
            patch("speech_to_speech.persona_sim.engine.Event"),
            patch("speech_to_speech.persona_sim.engine.CancelScope"),
        ):
            from speech_to_speech.persona_sim.engine import build_engine
            engine = build_engine(config)
            assert engine.personas == sample_personas
            assert isinstance(engine.tts_handler, MagicMock)
            # No stt_handler in the new engine
            assert not hasattr(engine, 'stt_handler')
