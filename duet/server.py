"""F5-TTS persona duet server — a minimal, Hermes-driven 'versus'.

Replaces the OmniVoice-versus stack with the F5-TTS worker protocol used by
the Hermes f5tts plugin, so a persona duel runs on the RTX 5070 with the
same cloned voices. One FastAPI process:

  POST /api/duet   {"a": "Churchill", "b": "Genghis", "topic": "...", "turns": 6}
  GET  /api/stream server-sent events: hello/transcript deltas, turn events
                   with per-turn audio (base64 wav), done.
  GET  /api/personas
  GET  /           the player page (auto-plays turns in order)

GPU policy: exactly ONE F5 worker may be resident. On startup this server
kills the Hermes gateway's f5tts worker (the gateway respawns it on demand
once this server exits) and starts its own, gated to cuda like the plugin.
The duet loop mirrors src/speech_to_speech/persona_sim/engine.py semantics
(alternating turns, per-persona histories, brief scaffolding).
"""

import asyncio
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PERSONAS_BASE = Path(os.environ.get("PERSONAS_BASE", REPO / "Personas"))
F5_VENV_PY = os.environ.get("F5_VENV_PY", "/home/dennis/projects/f5tts-venv/bin/python")
F5_WORKER = Path(os.environ.get("F5_WORKER", Path.home() / ".hermes/plugins/f5tts/f5tts_worker.py"))
LLM_URL = os.environ.get("SIM_LLM_URL", "http://localhost:8080").rstrip("/")
MODEL = os.environ.get("SIM_LLM_MODEL", "")
MAX_TOKENS = int(os.environ.get("SIM_MAX_TOKENS", "256"))
TEMPERATURE = float(os.environ.get("SIM_TEMPERATURE", "0.8"))
PORT = int(os.environ.get("DUET_PORT", "7862"))

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

READY_RE = re.compile(r"worker ready on (\w+) in (\d+)")


class F5Worker:
    """JSON-lines client for the f5tts worker protocol (same as the plugin)."""

    def __init__(self) -> None:
        self._proc = None
        self._lock = asyncio.Lock()

    def _kill_gateway_workers(self) -> None:
        try:
            out = subprocess.run(
                ["pgrep", "-f", "plugins/f5tts/f5tts_worker.py"],
                capture_output=True, text=True, check=False).stdout.split()
            for pid in out:
                os.kill(int(pid), signal.SIGTERM)
                print(f"[duet] terminated gateway f5tts worker pid {pid}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[duet] gateway worker sweep failed: {exc}", flush=True)

    async def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._kill_gateway_workers()
        await asyncio.sleep(1.0)  # let the GPU reclaim
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("PYTHON") and k not in
               ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "UV_PYTHON", "CONDA_PREFIX", "CONDA_DEFAULT_ENV")}
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        env["F5_WORKER_DEVICE"] = "cuda"
        self._proc = subprocess.Popen(
            [F5_VENV_PY, str(F5_WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(os.path.expanduser("~/.hermes/logs/duet-f5-worker.log"), "ab"),
            text=True, bufsize=1, env=env, cwd=str(REPO),
        )
        # No stdout handshake: the worker prints readiness to stderr and only
        # ever writes JSON on stdout in reply to a request (same as the plugin).
        # The first synth() call simply blocks through model load (~1-2 min).
        print("[duet] f5 worker spawned; first synth will absorb model load", flush=True)

    async def synth(self, text: str, persona: str, speed: float | None = None) -> bytes:
        async with self._lock:
            await self.start()
            req: dict = {"text": text, "persona": persona}
            if speed is not None:
                req["speed"] = speed
            loop = asyncio.get_event_loop()

            def _rpc() -> dict:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                return json.loads(self._proc.stdout.readline())

            resp = await loop.run_in_executor(None, _rpc)
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error") or "f5 worker error")
            return base64.b64decode(resp["wav_b64"])

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self._proc.kill()


WORKER = F5Worker()


def persona_names() -> list[str]:
    if not PERSONAS_BASE.exists():
        return []
    return sorted(p.name for p in PERSONAS_BASE.iterdir()
                  if p.is_dir() and (p / "prompt.md").exists() and (p / "ref.wav").exists())


async def stream_llm(messages: list[dict]) -> str:
    import httpx
    body = {
        "model": MODEL or "default",
        "messages": messages,
        "stream": True,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "reasoning_effort": "none",
    }
    chunks: list[str] = []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", f"{LLM_URL}/v1/chat/completions", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "error" in ev:
                    raise RuntimeError(f"LLM stream error: {ev['error']}")
                choice = (ev.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning_content")
                if not piece:
                    msg = choice.get("message", {})
                    piece = msg.get("content") or msg.get("reasoning_content")
                if piece:
                    chunks.append(piece)
    return "".join(chunks).strip()


async def duet_turn(speaker: str, other: str, own: list[dict]) -> str:
    system = (PERSONAS_BASE / speaker / "prompt.md").read_text(encoding="utf-8").strip()
    system += (
        f"\n\nYou are having a conversation with {other}. "
        "Reply in character with your next turn only — brief, 100 words or less. "
        "No stage directions, no markdown, no reasoning traces; it will be read aloud."
    )
    messages = [{"role": "system", "content": system}]
    for who, txt in own:
        messages.append({"role": "assistant" if who == speaker else "user", "content": txt})
    return await stream_llm(messages)


class DuetRequest(BaseModel):
    a: str
    b: str
    topic: str
    turns: int = 6


SESSIONS: dict[str, dict] = {}
SUBSCRIBERS: set[asyncio.Queue] = set()


async def publish(event: dict) -> None:
    for q in list(SUBSCRIBERS):
        q.put_nowait(event)


async def run_duet(sid: str, req: DuetRequest) -> None:
    names = set(persona_names())
    sess = SESSIONS[sid]
    try:
        for speaker in [req.a, req.b] * ((req.turns + 1) // 2):
            if sess.get("cancelled"):
                break
            other = req.b if speaker == req.a else req.a
            own = sess["hist"][speaker]
            if not own:
                own.append(("user", f"Let us discuss: {req.topic}"))
            text = ""
            for attempt in range(2):  # llama-server is single-slot; a busy slot
                try:                # can return an empty stream — retry once
                    text = await duet_turn(speaker, other, own)
                except Exception as exc:  # noqa: BLE001
                    print(f"[duet] turn error (attempt {attempt+1}): {exc}", flush=True)
                if text:
                    break
                await asyncio.sleep(2)
            if sess.get("cancelled"):
                break
            if not text:
                await publish({"type": "error", "session": sid,
                               "message": "LLM returned empty turn; aborting"})
                break
            sess["hist"][speaker].append(("assistant", text))
            sess["hist"][other].append(("user", text))
            sess["transcript"].append({"speaker": speaker, "text": text})
            await publish({"type": "turn", "session": sid, "speaker": speaker, "text": text})
            sess["synthesizing"] = True
            try:
                wav = await WORKER.synth(text, speaker)
            finally:
                sess["synthesizing"] = False
            if sess.get("cancelled"):
                break
            await publish({"type": "audio", "session": sid, "speaker": speaker,
                           "wav_b64": base64.b64encode(wav).decode(),
                           "secs": len(wav) / 2 / 24000})
            print(f"[duet] {sid} {speaker}: {len(text)} chars, {len(wav)} wav bytes", flush=True)
    except Exception as exc:  # noqa: BLE001
        if not sess.get("cancelled"):
            await publish({"type": "error", "session": sid, "message": str(exc)})
    finally:
        sess["running"] = False
        await publish({"type": "done", "session": sid,
                       "stopped": bool(sess.get("cancelled"))})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    WORKER.stop()


app = FastAPI(title="duet", lifespan=lifespan)


@app.get("/api/personas")
async def get_personas():
    return {"personas": persona_names()}


@app.get("/avatar/{name}")
async def get_avatar(name: str):
    if name not in set(persona_names()):
        raise HTTPException(404, "no such persona")
    img = PERSONAS_BASE / name / "image.jpg"
    if not img.is_file():
        raise HTTPException(404, "no avatar for this persona")
    return FileResponse(img, media_type="image/jpeg")


@app.post("/api/duet")
async def start_duet(req: DuetRequest):
    names = set(persona_names())
    for n in (req.a, req.b):
        if n not in names:
            raise HTTPException(404, f"unknown persona {n!r}")
    for s in SESSIONS.values():
        if s.get("running"):
            raise HTTPException(409, "a duet is already running")
    sid = uuid.uuid4().hex[:8]
    SESSIONS[sid] = {"running": True, "cancelled": False, "a": req.a, "b": req.b,
                     "topic": req.topic,
                     "hist": {req.a: [], req.b: []}, "transcript": []}
    asyncio.create_task(run_duet(sid, req))
    await publish({"type": "start", "session": sid, "a": req.a, "b": req.b, "topic": req.topic})
    return {"session": sid}


@app.post("/api/stop")
async def stop_duet():
    interrupted = False
    for s in SESSIONS.values():
        s["cancelled"] = True
        if s.get("synthesizing"):
            interrupted = True
    if interrupted:
        # Abandon the in-flight synth so the stop takes effect immediately;
        # the worker respawns (and reloads) on the next duel.
        WORKER.stop()
    return {"ok": True, "interrupted": interrupted}


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    SUBSCRIBERS.add(q)

    async def gen():
        try:
            last = max(SESSIONS, key=lambda k: len(SESSIONS[k]["transcript"])) if SESSIONS else None
            hello = {"type": "hello",
                     "personas": persona_names(),
                     "running": any(s["running"] for s in SESSIONS.values())}
            if last and SESSIONS[last]["transcript"]:
                hello["resume"] = {"session": last,
                                   "transcript": SESSIONS[last]["transcript"]}
            yield f"data: {json.dumps(hello)}\n\n"
            while True:
                ev = await q.get()
                if ev["type"] == "audio":
                    ev = dict(ev)
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            SUBSCRIBERS.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
