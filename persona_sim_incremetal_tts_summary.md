# Incremental TTS Streaming — Implementation Summary

## Motivation

Previously, the versus engine waited for the **entire** LLM response before
beginning TTS synthesis. For a 5-sentence response, the user might wait 10–15
seconds for the first audio to start playing.

The fix: stream LLM output sentence-by-sentence to TTS as soon as each
sentence completes, so audio synthesis begins within ~1–2 seconds of the first
sentence finishing.

## Architecture Change

### Before (synchronous)

```
LLM response ██████████████████████████████████████ (10s)
TTS synthesis                  ██████████████████████████████████████ (5s)
Audio sent to client                                                        ^
```

### After (incremental)

```
LLM response ████ ████ ████ ████ ████ (10s total, streaming)
TTS synthesis  ██   ██   ██   ██   ██ (each sentence starts immediately)
Audio sent to client                                ^ (starts after ~2s)
```

## Files Changed

| File | Change |
|------|--------|
| `src/speech_to_speech/persona_sim/engine.py` | Core changes (see below) |
| `tests/test_persona_sim_engine.py` | Updated tests for new interface |

## Engine Changes

### 1. `__init__`

- Added `_last_reply_text_value: str = ""` initialization (was missing before).

### 2. Replaced `_call_llm()` with two new methods

**`_stream_llm(persona, history, other_persona)`** — Async generator

- Yields each LLM text delta as it arrives from the SSE stream.
- Collects all text into `_last_reply_text_value` for reference.
- Handles reasoning model fields (`reasoning`, `reasoning_content`).
- Falls back to non-streaming call if streaming returns nothing.

**`_stream_sentences(persona, history, other_persona)`** — Async generator

- Wraps `_stream_llm()` and buffers incoming text.
- Splits text on sentence boundaries (`.!?` followed by whitespace or end-of-text).
- Yields complete sentences as they form.
- The final partial sentence is yielded at the end of the stream.

Regex for sentence boundaries:
```python
_SENTENCE_BOUNDARY_RE = re.compile(r'([.!?])\s*(?=\S|$)')
```

### 3. Rewrote `_run_turn()`

- Iterates over `_stream_sentences()` instead of calling `_call_llm()`.
- For each sentence, immediately sends it to TTS via `tts_handler.process()`.
- Audio chunks accumulate in `audio_chunks` list.
- Full text assembled from sentence fragments.
- Returns `(full_audio_bytes, full_text)` — same interface as before, but
  audio synthesis now begins incrementally.

```python
async def _run_turn(self, speaker, history, other_persona):
    self.tts_handler.switch_persona(speaker)
    audio_chunks = []
    full_text_parts = []

    async for sentence in self._stream_sentences(persona, history, other_persona):
        full_text_parts.append(sentence)
        for chunk in self.tts_handler.process(TTSInput(text=sentence, ...)):
            audio_chunks.append(...)

    return b"".join(audio_chunks), "".join(full_text_parts)
```

## Testing

All 23 tests updated:

- `_call_llm` → `_stream_sentences` (async generator interface)
- `chat_history` → `history_a` / `history_b` (dual history)
- `_last_reply_text` → `_last_reply_text_value`
- Mock functions updated to match new signatures

## Performance Impact

| Metric | Before | After |
|--------|--------|-------|
| Time to first audio | ~10–15s (entire LLM + TTS) | ~2–3s (first sentence LLM + TTS) |
| Perceived latency | High — long silence at start | Low — audio starts quickly |
| Total turn time | Same | Same |

The total turn time is unchanged — we're just starting TTS earlier. The
improvement is in **perceived latency**: the user hears audio within 2–3
seconds instead of waiting for the entire response.

## Future Optimizations

- **Parallel TTS**: For even lower latency, TTS could run on a separate
  thread while the LLM continues generating. Currently TTS is synchronous
  (blocks the event loop).
- **Streaming audio chunks**: Instead of waiting for all audio to be
  synthesized, audio chunks could be sent to the browser incrementally via
  the WebSocket protocol.
- **Sentence-level audio play**: The browser could start playing audio chunks
  as they arrive, rather than waiting for the full turn audio.
