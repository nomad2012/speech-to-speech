# Versus App — Streaming Audio Queue & Incremental Text Summary

## Overview
This document summarizes the enhancements made to the persona-versus duet web app for
real-time audio streaming and incremental chat bubble text updates.

## Changes

### 1. Sequential Audio Queue (Browser)
**Problem:** Previously, all audio chunks for a turn were accumulated and concatenated
into one WAV file, then played at the end. This delayed audio playback until the full
turn was synthesized (~5–10 seconds).

**Solution:** Replaced accumulation with a FIFO audio queue. Each chunk is a
self-contained base64-encoded WAV file that plays sequentially:
- `enqueueAudioChunk(b64, turn)` — adds chunk to queue, starts playing if idle
- `playNextFromQueue()` — checks `currentAudioEl !== null` guard to avoid overlap
- `playSingleChunk(b64, turn)` — creates `<audio>` element, auto-plays next on `onended`
- `sendPlaybackDone(turn)` — sends acknowledgment after queue drains

**State variables:**
- `audioQueue` — FIFO array of `{b64, turn}` objects
- `currentAudioEl` — currently playing `<Audio>` element (null when idle)
- `pendingPlaybackDoneTurn` — turn number waiting for queue to drain before sending `playback_done`

### 2. Incremental Text in Chat Bubbles
**Problem:** Only the first sentence text appeared in the chat bubble.

**Solution:** Server now includes partial text in each `audio_chunk` message. Browser
appends text to the existing turn element as each sentence arrives.

**Protocol change:** `audio_chunk` messages now include `"text": full_text_so_far`.

**Browser implementation:**
- `showTurn()` creates turn element with `data-turn` attribute
- `appendTurnText(turnNumber, text)` finds element by data attribute, updates `<p>` content
- Each chunk replaces full text (not appended) since server sends accumulated text

### 3. Sentence Spacing Fix (Engine)
**Problem:** `_run_turn_streaming` joined sentences with `"".join(full_text_parts)` —
no space between sentences, resulting in "Hello.World." text.

**Fix:** Changed to `" ".join(full_text_parts)` in `engine.py` line ~467.

### 4. Server Protocol Update
**File:** `versus/server.py`

`audio_chunk` messages now include text:
```json
{
    "type": "audio_chunk",
    "turn": 1,
    "chunk": "<base64 wav>",
    "text": "Hello. This is a second sentence. And a third."
}
```

## Files Changed
| File | Change |
|------|--------|
| `versus/app.js` | Audio queue system, streaming protocol handler, `appendTurnText()` |
| `versus/server.py` | `audio_chunk` includes `text` field |
| `src/speech_to_speech/persona_sim/engine.py` | Sentence join with space (`" ".join`) |
| `tests/test_persona_sim_engine.py` | Tests for new engine interface |
| `versus/IMPLEMENTATION_SUMMARY.md` | Documentation |

## Testing
- Audio plays sequentially: each sentence starts after the previous finishes
- No overlapping audio (queue guard prevents concurrent playback)
- Chat bubbles show text incrementally with proper spacing
- `playback_done` sent after all audio for a turn has played

## Commit
`5b77f83` — "Enhance versus app: streaming audio queue and sentence spacing"
