"""FastAPI server for the persona-versus duet app.

Endpoints:
- GET  /api/personas — list available personas (for UI picker)
- POST /api/simulations — create and start a new simulation
- WS   /api/simulations/{simulation_id}/stream — WebSocket stream for turn-by-turn duet
- GET  /api/simulations/{simulation_id} — get simulation status
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
import base64
import io
import logging
import wave
import uuid
from typing import Optional

from speech_to_speech.persona_sim import SimConfig, DuetEngine, build_engine

logger = logging.getLogger(__name__)

app = FastAPI(title="Persona Versus Duet")

# Store active simulations
simulations: dict[str, dict] = {}


def _pcm16_to_wav_base64(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """Convert raw int16 PCM bytes to base64-encoded WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class SimulationRequest(BaseModel):
    persona_a: str
    persona_b: str
    topic: str  # text prompt for the first turn
    turns: int = 6
    chat_size: Optional[int] = None


class PersonaInfo(BaseModel):
    name: str
    description: str
    language: str
    avatar_color: str = ""
    avatar_url: str = ""


class PersonasResponse(BaseModel):
    personas: list[PersonaInfo]


# --- Startup ---


@app.on_event("startup")
async def startup_event():
    """Build the engine from config on startup."""
    try:
        config = SimConfig.from_env()
        engine = build_engine(config)
        app.engine = engine
        logger.info("Persona engine initialized with %d personas", len(engine.personas))
    except Exception as e:
        logger.error("Failed to initialize persona engine: %s", e)
        raise RuntimeError(f"Failed to initialize persona engine: {e}") from e


# --- Endpoints ---


@app.get("/api/personas")
async def list_personas():
    """List all available personas for the UI picker."""
    if not hasattr(app, "engine"):
        raise HTTPException(status_code=503, detail="Engine not initialized")

    personas = app.engine.personas
    return PersonasResponse(
        personas=[
            PersonaInfo(
                name=name,
                description=p.description or name,
                language=p.language,
                avatar_color=p.avatar_color or "#888888",
                avatar_url=f"/api/personas/{name}/avatar" if p.avatar_path else "",
            )
            for name, p in personas.items()
        ]
    )


@app.get("/api/personas/{persona_name}/avatar")
async def persona_avatar(persona_name: str):
    """Serve the avatar image for a persona."""
    if not hasattr(app, "engine"):
        raise HTTPException(status_code=503, detail="Engine not initialized")

    persona = app.engine.personas.get(persona_name)
    if persona is None or persona.avatar_path is None:
        raise HTTPException(status_code=404, detail="No avatar for persona")

    avatar_path = persona.avatar_path
    if not avatar_path.is_file():
        raise HTTPException(status_code=404, detail="Avatar file not found")

    ext = avatar_path.suffix.lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    return FileResponse(avatar_path, media_type=media_type)


@app.post("/api/simulations", status_code=201)
async def create_simulation(request: SimulationRequest):
    """Create and start a new duet simulation."""
    if not hasattr(app, "engine"):
        raise HTTPException(status_code=503, detail="Engine not initialized")

    sim_id = uuid.uuid4().hex
    simulations[sim_id] = {
        "persona_a": request.persona_a,
        "persona_b": request.persona_b,
        "topic": request.topic,
        "turns": request.turns,
        "chat_size": request.chat_size or app.engine.chat_size,
        "status": "pending",
        "id": sim_id,
    }
    logger.info("Created simulation %s: %s vs %s — topic: %s",
                sim_id[:8], request.persona_a, request.persona_b, request.topic[:50])
    return {"id": sim_id}


@app.get("/api/simulations/{simulation_id}")
async def get_simulation(simulation_id: str):
    """Get simulation status."""
    if simulation_id not in simulations:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return simulations[simulation_id]


@app.websocket("/api/simulations/{simulation_id}/stream")
async def simulation_stream(websocket: WebSocket, simulation_id: str):
    """WebSocket stream for turn-by-turn duet conversation.

    Protocol:
    - Client sends nothing (simulation already started with topic from POST).
    - Server sends turns: {"type": "turn", "turn": N, "speaker": "...", "text": "...", "audio": "base64...", "format": "wav/16000"}
    - Client sends back between turns: {"type": "playback_done", "turn": N}
    - Client can stop: {"type": "stop"}
    - Server ends with: {"type": "done", "reason": "completed"|"stopped"|"error"}
    """
    if simulation_id not in simulations:
        await websocket.close(code=4004, reason="Simulation not found")
        return

    await websocket.accept()

    sim = simulations[simulation_id]

    try:
        engine = app.engine

        # Update simulation status
        sim["status"] = "running"
        logger.info("Simulation %s started with topic: %s", simulation_id[:8], sim["topic"][:100])

        # Step 1: Run the simulation turn by turn
        turn_number = 0
        async for turn in engine.run(
            persona_a=sim["persona_a"],
            persona_b=sim["persona_b"],
            topic=sim["topic"],
            turns=sim["turns"],
        ):
            turn_number += 1

            # Encode audio as base64 WAV
            audio_base64 = _pcm16_to_wav_base64(turn.audio, turn.sample_rate)

            # Send turn to client
            await websocket.send_json({
                "type": "turn",
                "turn": turn_number,
                "speaker": turn.speaker,
                "text": turn.text,
                "audio": audio_base64,
                "format": "wav/16000",
            })

            # Wait for playback_done from client
            response = await websocket.receive_json()
            if response.get("type") == "stop":
                engine.stop()
                await websocket.send_json({"type": "done", "reason": "stopped"})
                break
            # playback_done is expected — continue to next turn

        if sim["status"] == "running":
            sim["status"] = "completed"
            await websocket.send_json({"type": "done", "reason": "completed"})
            logger.info("Simulation %s completed", simulation_id[:8])

    except WebSocketDisconnect:
        engine.stop()
        sim["status"] = "stopped"
        logger.info("Simulation %s: client disconnected", simulation_id[:8])
    except Exception as e:
        logger.exception("Simulation error")
        try:
            await websocket.send_json({"type": "done", "reason": "error", "message": str(e)})
        except Exception:
            pass
        sim["status"] = "error"


# Serve the web app files
app.mount("/", StaticFiles(directory="versus", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    uvicorn.run(app, host="0.0.0.0", port=7861)
