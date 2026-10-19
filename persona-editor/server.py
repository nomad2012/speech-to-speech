"""FastAPI server for the Persona Editor app.

Endpoints:
- GET    /api/personas                  — list all personas
- POST   /api/personas                  — create a new persona (multipart/form-data)
- GET    /api/personas/{name}/detail    — get full persona detail
- PUT    /api/personas/{name}           — update an existing persona (multipart/form-data)
- DELETE /api/personas/{name}           — delete a persona
- POST   /api/personas/{name}/clone     — clone a persona
- GET    /api/personas/{name}/avatar    — serve persona avatar image
- GET    /api/personas/{name}/reference-audio — serve persona reference audio

Personas live in per-persona subdirectories of the configured Personas
directory (default: ../Personas relative to this file).
"""

import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import yaml
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SERVER_ROOT = Path(__file__).resolve().parent
# Personas directory: ../Personas relative to persona-editor/
PERSONAS_DIR = _SERVER_ROOT.parent / "Personas"

DEFAULT_MEMORY_SIZE = 8192
MAX_MEMORY_SIZE = 16384
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# ---------------------------------------------------------------------------
# File layout constants
# ---------------------------------------------------------------------------

PROMPT_FILENAME = "prompt.md"
LANGUAGE_FILENAME = "language.txt"
REFERENCE_AUDIO_FILENAME = "ref.wav"
TRANSCRIPT_FILENAME = "ref.txt"
IMAGE_BASENAME = "image"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
AUDIO_EXTENSION = ".wav"
DEFAULT_LANGUAGE = "en"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PersonaResponse(BaseModel):
    name: str
    description: str = ""
    avatar_color: str = "#888888"
    avatar_image: bool = False

class PersonaDetailResponse(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    router_hints: str = ""
    avatar_color: str = "#888888"
    avatar_image: bool = False
    reference_audio: bool = False
    reference_audio_transcript: Optional[str] = None
    reference_audio_language: str = "en"
    allow_tool_calls: bool = False
    memory_size: int = Field(default=DEFAULT_MEMORY_SIZE, ge=0, le=MAX_MEMORY_SIZE)

# ---------------------------------------------------------------------------
# Persona storage helpers (file-based, framework-agnostic)
# ---------------------------------------------------------------------------

_DIRNAME_STRIP_PATTERN = re.compile(r"[^a-zA-Z0-9 _-]")

def sanitize_persona_dirname(name: str) -> str:
    """Reduce a persona name to a safe directory name."""
    return _DIRNAME_STRIP_PATTERN.sub("", name)

def unique_persona_dirname(root: Path, base: str) -> str:
    """Pick base, or the first free base_2, base_3, ... in root."""
    candidate = base
    suffix = 2
    while (root / candidate).exists():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate

def parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Split a prompt.md into (frontmatter dict, system prompt body)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip("\n")
    for end, line in enumerate(lines[1:], start=1):
        if line.strip() != "---":
            continue
        try:
            data = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError as exc:
            logger.warning("Malformed prompt.md frontmatter; ignoring: %s", exc)
            return {}, text.strip("\n")
        if not isinstance(data, dict):
            return {}, text.strip("\n")
        return data, "\n".join(lines[end + 1:]).strip("\n")
    return {}, text.strip("\n")

def build_prompt_md(dir_name: str, **kwargs) -> str:
    """Serialize a persona's frontmatter + system prompt for prompt.md."""
    frontmatter = {}
    name = kwargs.get("name", dir_name)
    if name != dir_name:
        frontmatter["name"] = name
    frontmatter["description"] = kwargs.get("description", "")
    frontmatter["router_hints"] = kwargs.get("router_hints", "")
    frontmatter["avatar_color"] = kwargs.get("avatar_color", "#888888")
    frontmatter["allow_tool_calls"] = bool(kwargs.get("allow_tool_calls", False))
    memory_size = kwargs.get("memory_size")
    if memory_size is not None:
        frontmatter["memory_size"] = memory_size
    dumped = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True).strip()
    system_prompt = kwargs.get("system_prompt", "")
    return f"---\n{dumped}\n---\n\n{system_prompt}\n"

def find_avatar_file(persona_dir: Path) -> Optional[Path]:
    """Locate the persona's avatar image."""
    candidates = sorted(
        p for p in persona_dir.glob(f"{IMAGE_BASENAME}.*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return candidates[0] if candidates else None

def load_persona_from_dir(persona_dir: Path) -> PersonaDetailResponse:
    """Build a PersonaDetailResponse from one subdirectory."""
    prompt_path = persona_dir / PROMPT_FILENAME
    if not prompt_path.is_file():
        raise ValueError(f"missing {PROMPT_FILENAME}")
    text = prompt_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, system_prompt = parse_frontmatter(text)
    name = str(frontmatter.get("name") or "").strip() or persona_dir.name

    avatar_path = find_avatar_file(persona_dir)
    audio_path = persona_dir / REFERENCE_AUDIO_FILENAME
    transcript_path = persona_dir / TRANSCRIPT_FILENAME

    # Read transcript contents
    reference_transcript = None
    if transcript_path.is_file():
        try:
            content = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                reference_transcript = content
        except OSError:
            pass

    # Read language
    lang_path = persona_dir / LANGUAGE_FILENAME
    if lang_path.is_file():
        try:
            lang = lang_path.read_text(encoding="utf-8", errors="replace").strip()
            if len(lang) != 2:
                lang = DEFAULT_LANGUAGE
        except OSError:
            lang = DEFAULT_LANGUAGE
    else:
        lang = DEFAULT_LANGUAGE

    memory_size = frontmatter.get("memory_size", DEFAULT_MEMORY_SIZE)
    if not isinstance(memory_size, int) or isinstance(memory_size, bool):
        memory_size = DEFAULT_MEMORY_SIZE
    if memory_size < 0 or memory_size > MAX_MEMORY_SIZE:
        memory_size = DEFAULT_MEMORY_SIZE

    return PersonaDetailResponse(
        name=name,
        description=str(frontmatter.get("description") or ""),
        system_prompt=system_prompt,
        router_hints=str(frontmatter.get("router_hints") or ""),
        avatar_color=str(frontmatter.get("avatar_color") or "#888888"),
        avatar_image=bool(avatar_path),
        reference_audio=audio_path.is_file(),
        reference_audio_transcript=reference_transcript,
        reference_audio_language=lang,
        allow_tool_calls=bool(frontmatter.get("allow_tool_calls", False)),
        memory_size=memory_size,
    )

def scan_personas_directory(root: Path) -> List[PersonaDetailResponse]:
    """Load every persona subdirectory under root."""
    if not root.is_dir():
        return []
    personas = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        try:
            personas.append(load_persona_from_dir(entry))
        except Exception as exc:
            logger.warning("Skipping persona directory %s: %s", entry.name, exc)
    return personas

def write_persona_fields(persona_dir: Path, **kwargs) -> None:
    """Write persona fields to disk."""
    (persona_dir / PROMPT_FILENAME).write_text(
        build_prompt_md(persona_dir.name, **kwargs),
        encoding="utf-8",
    )
    # Write language file
    lang = kwargs.get("reference_audio_language", "en")
    (persona_dir / LANGUAGE_FILENAME).write_text(lang, encoding="utf-8")
    # Write transcript file
    transcript = kwargs.get("reference_audio_transcript", "")
    transcript_path = persona_dir / TRANSCRIPT_FILENAME
    if transcript.strip():
        transcript_path.write_text(transcript.strip(), encoding="utf-8")
    elif transcript_path.exists():
        transcript_path.unlink()

def write_avatar_file(persona_dir: Path, data: bytes, extension: str) -> Path:
    """Store a new avatar, replacing any existing one."""
    for p in persona_dir.glob(f"{IMAGE_BASENAME}.*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            p.unlink()
    target = persona_dir / f"{IMAGE_BASENAME}{extension.lower()}"
    target.write_bytes(data)
    return target

# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name may not be blank")
    if name.lower() == "user":
        raise HTTPException(status_code=422, detail="'user' is a reserved name and cannot be used.")
    return name

def _validate_image_upload(upload: UploadFile) -> Tuple[str, bytes]:
    filename = upload.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        allowed = ", ".join(ext.lstrip(".") for ext in IMAGE_EXTENSIONS)
        raise HTTPException(status_code=422, detail=f"Unsupported avatar image. Allowed: {allowed}.")
    content = upload.file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail=f"Avatar image exceeds the {MAX_IMAGE_BYTES // (1024*1024)}MB limit.")
    return extension, content

def _validate_audio_upload(upload: UploadFile) -> bytes:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != AUDIO_EXTENSION:
        raise HTTPException(status_code=422, detail=f"Only WAV audio is supported.")
    content = upload.file.read()
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail=f"Reference audio exceeds the {MAX_AUDIO_BYTES // (1024*1024)}MB limit.")
    return content

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=List[PersonaResponse])
def list_personas():
    """Return all personas (summary)."""
    personas = scan_personas_directory(PERSONAS_DIR)
    return [
        PersonaResponse(
            name=p.name,
            description=p.description,
            avatar_color=p.avatar_color,
            avatar_image=p.avatar_image,
        )
        for p in personas
    ]

@router.post("", response_model=PersonaDetailResponse, status_code=201)
def create_persona(
    name: str = Form(..., max_length=25),
    description: str = Form(""),
    system_prompt: str = Form(..., min_length=1, max_length=8192),
    router_hints: str = Form(..., min_length=1, max_length=256),
    avatar_color: str = Form("#FF0000"),
    reference_audio_language: str = Form("en", min_length=2, max_length=2),
    allow_tool_calls: bool = Form(False),
    reference_audio_transcript: str = Form(""),
    memory_size: int = Form(DEFAULT_MEMORY_SIZE, ge=0, le=MAX_MEMORY_SIZE),
    avatar_image: Optional[UploadFile] = File(None),
    reference_audio: Optional[UploadFile] = File(None),
):
    """Create a new persona."""
    name = _validate_name(name)
    personas = scan_personas_directory(PERSONAS_DIR)
    if any(p.name.lower() == name.lower() for p in personas):
        raise HTTPException(status_code=409, detail=f"A persona named '{name}' already exists")

    dir_base = sanitize_persona_dirname(name)
    if not dir_base:
        raise HTTPException(status_code=422, detail="Name must contain at least one letter, number, space, hyphen or underscore")

    image = None
    if avatar_image and avatar_image.filename:
        image = _validate_image_upload(avatar_image)
    audio = None
    if reference_audio and reference_audio.filename:
        audio = _validate_audio_upload(reference_audio)

    persona_dir = PERSONAS_DIR / unique_persona_dirname(PERSONAS_DIR, dir_base)
    persona_dir.mkdir(parents=True)

    try:
        write_persona_fields(
            persona_dir,
            name=name,
            description=description or "",
            system_prompt=system_prompt,
            router_hints=router_hints,
            avatar_color=avatar_color,
            reference_audio_language=reference_audio_language,
            allow_tool_calls=allow_tool_calls,
            reference_audio_transcript=reference_audio_transcript,
            memory_size=memory_size,
        )
        if image:
            write_avatar_file(persona_dir, image[1], image[0])
        if audio:
            (persona_dir / REFERENCE_AUDIO_FILENAME).write_bytes(audio)
        result = load_persona_from_dir(persona_dir)
    except OSError as exc:
        shutil.rmtree(persona_dir, ignore_errors=True)
        logger.error("Failed to create persona '%s' in %s: %s", name, persona_dir, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create persona: {exc}") from exc

    return result

@router.get("/{name}/detail", response_model=PersonaDetailResponse)
def get_persona_detail(name: str):
    """Return full detail for a single persona."""
    personas = scan_personas_directory(PERSONAS_DIR)
    persona = next((p for p in personas if p.name == name), None)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{name}' not found")
    return persona

@router.put("/{name}", response_model=PersonaDetailResponse)
def update_persona(
    name: str,
    new_name: str = Form(..., max_length=25, alias="name"),
    description: str = Form(""),
    system_prompt: str = Form(..., min_length=1, max_length=8192),
    router_hints: str = Form(..., min_length=1, max_length=256),
    avatar_color: str = Form("#FF0000"),
    reference_audio_language: str = Form("en", min_length=2, max_length=2),
    allow_tool_calls: bool = Form(False),
    reference_audio_transcript: str = Form(""),
    memory_size: int = Form(..., ge=0, le=MAX_MEMORY_SIZE),
    avatar_image: Optional[UploadFile] = File(None),
    remove_avatar_image: bool = Form(False),
    reference_audio: Optional[UploadFile] = File(None),
    remove_reference_audio: bool = Form(False),
):
    """Update an existing persona."""
    personas = scan_personas_directory(PERSONAS_DIR)
    existing = next((p for p in personas if p.name == name), None)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Persona '{name}' not found")
    if existing.avatar_image:
        pass  # OK
    if existing.reference_audio:
        pass  # OK

    new_name = _validate_name(new_name)
    if new_name.lower() != name.lower() and any(p.name.lower() == new_name.lower() for p in personas if p.name != name):
        raise HTTPException(status_code=409, detail=f"A persona named '{new_name}' already exists")

    # Find persona dir by name match (need the actual directory)
    persona_dir = None
    for entry in PERSONAS_DIR.iterdir():
        if entry.is_dir():
            try:
                p = load_persona_from_dir(entry)
                if p.name == name:
                    persona_dir = entry
                    break
            except Exception:
                continue

    if persona_dir is None:
        raise HTTPException(status_code=500, detail=f"Persona '{name}' has no directory on disk")

    # Handle file uploads
    image = None
    if avatar_image and avatar_image.filename:
        image = _validate_image_upload(avatar_image)
    audio = None
    if reference_audio and reference_audio.filename:
        audio = _validate_audio_upload(reference_audio)

    # Write fields
    write_persona_fields(
        persona_dir,
        name=new_name,
        description=description or "",
        system_prompt=system_prompt,
        router_hints=router_hints,
        avatar_color=avatar_color,
        reference_audio_language=reference_audio_language,
        allow_tool_calls=allow_tool_calls,
        reference_audio_transcript=reference_audio_transcript,
        memory_size=memory_size,
    )

    # Handle avatar
    if image:
        write_avatar_file(persona_dir, image[1], image[0])
    elif remove_avatar_image:
        for p in persona_dir.glob(f"{IMAGE_BASENAME}.*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                p.unlink(missing_ok=True)

    # Handle audio
    if audio:
        (persona_dir / REFERENCE_AUDIO_FILENAME).write_bytes(audio)
    elif remove_reference_audio:
        audio_path = persona_dir / REFERENCE_AUDIO_FILENAME
        if audio_path.exists():
            audio_path.unlink()

    result = load_persona_from_dir(persona_dir)
    return result

@router.delete("/{name}", status_code=204)
def delete_persona(name: str):
    """Remove a persona's directory."""
    personas = scan_personas_directory(PERSONAS_DIR)
    persona = next((p for p in personas if p.name == name), None)
    if not persona:
        raise HTTPException(status_code=404, detail=f"Persona '{name}' not found")

    # Find the actual directory
    persona_dir = None
    for entry in PERSONAS_DIR.iterdir():
        if entry.is_dir():
            try:
                p = load_persona_from_dir(entry)
                if p.name == name:
                    persona_dir = entry
                    break
            except Exception:
                continue

    if persona_dir and persona_dir.is_dir():
        try:
            shutil.rmtree(persona_dir)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete persona: {exc}") from exc

@router.post("/{name}/clone", response_model=PersonaDetailResponse, status_code=201)
def clone_persona(name: str):
    """Clone an existing persona."""
    personas = scan_personas_directory(PERSONAS_DIR)
    source = next((p for p in personas if p.name == name), None)
    if not source:
        raise HTTPException(status_code=404, detail=f"Persona '{name}' not found")

    # Find source directory
    source_dir = None
    for entry in PERSONAS_DIR.iterdir():
        if entry.is_dir():
            try:
                p = load_persona_from_dir(entry)
                if p.name == name:
                    source_dir = entry
                    break
            except Exception:
                continue

    if source_dir is None:
        raise HTTPException(status_code=500, detail=f"Persona '{name}' has no directory on disk")

    existing_names = {p.name.lower() for p in personas}
    suffix = 2
    while f"{name}_{suffix}".lower() in existing_names:
        suffix += 1
    new_name = f"{name}_{suffix}"

    dir_base = sanitize_persona_dirname(new_name)
    new_dir = PERSONAS_DIR / unique_persona_dirname(PERSONAS_DIR, dir_base)
    try:
        shutil.copytree(source_dir, new_dir)
        # Rewrite prompt.md with the clone's name
        write_persona_fields(
            new_dir,
            name=new_name,
            description=source.description or "",
            system_prompt=source.system_prompt,
            router_hints=source.router_hints,
            avatar_color=source.avatar_color,
            reference_audio_language=source.reference_audio_language,
            allow_tool_calls=source.allow_tool_calls,
            reference_audio_transcript=source.reference_audio_transcript or "",
            memory_size=source.memory_size,
        )
        result = load_persona_from_dir(new_dir)
    except OSError as exc:
        shutil.rmtree(new_dir, ignore_errors=True)
        logger.error("Failed to clone persona '%s': %s", name, exc)
        raise HTTPException(status_code=500, detail=f"Failed to clone persona: {exc}") from exc

    return result

@router.get("/{name}/avatar")
async def get_avatar(name: str):
    """Serve a persona's avatar image."""
    for entry in PERSONAS_DIR.iterdir():
        if entry.is_dir():
            try:
                p = load_persona_from_dir(entry)
                if p.name == name and p.avatar_image:
                    # Find the actual image file
                    avatar_path = find_avatar_file(entry)
                    if avatar_path and avatar_path.is_file():
                        ext = avatar_path.suffix.lower()
                        media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
                        return FileResponse(str(avatar_path), media_type=media_type)
                    return Response(status_code=404, content="Avatar file not found")
            except Exception:
                continue
    return Response(status_code=404, content="No avatar configured")

@router.get("/{name}/reference-audio")
async def get_reference_audio(name: str):
    """Serve a persona's reference audio file."""
    for entry in PERSONAS_DIR.iterdir():
        if entry.is_dir():
            try:
                p = load_persona_from_dir(entry)
                if p.name == name and p.reference_audio:
                    audio_path = entry / REFERENCE_AUDIO_FILENAME
                    if audio_path.is_file():
                        return FileResponse(str(audio_path), media_type="audio/wav")
                    return Response(status_code=404, content="Reference audio file not found")
            except Exception:
                continue
    return Response(status_code=404, content="No reference audio configured")

# ---------------------------------------------------------------------------
# Static files mount
# ---------------------------------------------------------------------------

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

def create_app() -> "fastapi.FastAPI":  # type: ignore[name-defined]
    """Create the FastAPI application."""
    import fastapi
    app = fastapi.FastAPI(title="Persona Editor")

    # Mount static files
    static_dir = _SERVER_ROOT / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include router
    app.include_router(router, prefix="/api/personas")

    @app.get("/")
    async def index():
        return FileResponse(str(_SERVER_ROOT / "index.html"))

    return app

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8765)
