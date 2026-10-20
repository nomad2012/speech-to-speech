"""Persona registry: load personas from the ``Personas/`` directory.

Each persona is a subdirectory containing:

* ``prompt.md`` — YAML frontmatter (metadata) + body (system prompt)
* ``language.txt`` — language code (e.g. ``en``)
* ``ref.txt`` — reference text for voice cloning (optional, required for Omnivoice)
* ``ref.wav`` — reference audio for voice cloning (optional, required for Omnivoice)
* ``image.jpg`` / ``image.jpeg`` — optional avatar image

Usage::

    from speech_to_speech.personas import load_personas, Persona

    personas = load_personas("Personas")
    einstein = personas["Einstein"]
    print(einstein.prompt)          # system prompt text
    print(einstein.language)        # "en"
    print(einstein.ref_text)        # voice clone reference text
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PERSONAS_DIR = "Personas"

# Supported frontmatter keys and their expected types.
# Extra keys are silently ignored (forward-compatibility).
_SUPPORTED_KEYS: dict[str, type | None] = {
    "description": str,
    "router_hints": str,
    "avatar_color": str,
    "allow_tool_calls": bool,
    "memory_size": int,
}

# Minimum required files per persona.
_REQUIRED_FILES = ("prompt.md", "language.txt")

# Optional voice-clone files needed for Omnivoice personas.
_VOICE_FILES = ("ref.txt", "ref.wav")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Persona:
    """A single persona definition loaded from a directory."""

    #: Directory name (persona name / identifier).
    name: str
    #: Path to the persona directory.
    path: Path
    #: System prompt body text from ``prompt.md`` (after frontmatter).
    prompt: str
    #: Language code from ``language.txt``.
    language: str
    #: Human-readable description from frontmatter (may be empty).
    description: str = ""
    #: Comma-separated routing hints from frontmatter (may be empty).
    router_hints: str = ""
    #: Hex color for avatar UI from frontmatter (may be empty).
    avatar_color: str = ""
    #: Whether tool calls are enabled from frontmatter.
    allow_tool_calls: bool = True
    #: Context window size in tokens from frontmatter (may be 0).
    memory_size: int = 0
    #: Reference text for voice cloning from ``ref.txt`` (may be empty).
    ref_text: str = ""
    #: Path to reference audio from ``ref.wav`` (may be None).
    ref_audio_path: Path | None = None
    #: Path to avatar image (may be None).
    avatar_path: Path | None = None

    @property
    def has_reference_audio(self) -> bool:
        """Whether this persona has a reference audio file for voice cloning."""
        return self.ref_audio_path is not None and self.ref_audio_path.is_file()

    @property
    def has_reference_text(self) -> bool:
        """Whether this persona has a reference text for voice cloning."""
        return bool(self.ref_text.strip())

    @property
    def is_voice_cloned(self) -> bool:
        """Whether this persona has both ref audio and ref text."""
        return self.has_reference_audio and self.has_reference_text


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and body from a markdown document.

    Returns ``(metadata_dict, body_text)``.
    If no frontmatter is found, returns ``({}, full_text)``.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    raw_yaml = match.group(1)
    body = text[match.end():]

    try:
        metadata = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML frontmatter: %s", exc)
        metadata = {}

    if not isinstance(metadata, dict):
        logger.warning("Frontmatter is not a mapping; ignoring.")
        metadata = {}

    return metadata, body


def _read_text_file(path: Path) -> str:
    """Read a UTF-8 text file, returning empty string on any error."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        logger.warning("Cannot read %s", path)
        return ""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_personas(personas_dir: str | Path | None = None) -> dict[str, Persona]:
    """Load all personas from the given directory.

    Scans *personas_dir* for subdirectories, each representing one persona.
    Requires ``prompt.md`` and ``language.txt`` in each subdirectory.
    Optional files: ``ref.txt``, ``ref.wav``, ``image.jpg``, ``image.jpeg``.

    Args:
        personas_dir: Path to the personas directory. Defaults to
            ``Personas/`` relative to this package's location; if not found
            there, falls back to ``Personas/`` at the project root
            (one level above ``src/``).

    Returns:
        A dictionary mapping persona name to :class:`Persona` instance.
        Only personas that pass validation are included.
    """
    if personas_dir is None:
        # Resolve relative to this package's directory.
        package_dir = Path(__file__).parent
        personas_dir = package_dir.parent / DEFAULT_PERSONAS_DIR
        # If not found under ``src/``, try the project root (one level above ``src/``).
        if not Path(personas_dir).is_dir():
            personas_dir = package_dir.parent.parent / DEFAULT_PERSONAS_DIR

    personas_path = Path(personas_dir)
    if not personas_path.is_dir():
        logger.warning("Personas directory not found: %s", personas_path)
        return {}

    personas: dict[str, Persona] = {}

    for entry in sorted(personas_path.iterdir()):
        if not entry.is_dir():
            continue

        persona_name = entry.name

        # --- Required files ------------------------------------------------
        missing = [f for f in _REQUIRED_FILES if not (entry / f).is_file()]
        if missing:
            logger.warning(
                "Skipping persona %r: missing required files %s",
                persona_name,
                ", ".join(f"'{f}'" for f in missing),
            )
            continue

        # --- Parse prompt.md -----------------------------------------------
        prompt_path = entry / "prompt.md"
        prompt_text = _read_text_file(prompt_path)
        if not prompt_text:
            logger.warning("Skipping persona %r: empty prompt.md", persona_name)
            continue

        metadata, body = _parse_frontmatter(prompt_text)
        system_prompt = body.strip()

        if not system_prompt:
            logger.warning("Skipping persona %r: no system prompt in prompt.md body", persona_name)
            continue

        # --- Parse language.txt --------------------------------------------
        language = _read_text_file(entry / "language.txt")
        if not language:
            logger.warning("Skipping persona %r: empty language.txt", persona_name)
            continue

        # --- Optional frontmatter fields -----------------------------------
        description = str(metadata.get("description") or "")
        router_hints = str(metadata.get("router_hints") or "")
        avatar_color = str(metadata.get("avatar_color") or "")
        allow_tool_calls = bool(metadata.get("allow_tool_calls", True))
        memory_size_val = metadata.get("memory_size")
        memory_size = int(memory_size_val) if isinstance(memory_size_val, int) and memory_size_val > 0 else 0

        # --- Optional voice-clone files ------------------------------------
        ref_text = _read_text_file(entry / "ref.txt")
        ref_audio = None
        ref_audio_candidate = entry / "ref.wav"
        if ref_audio_candidate.is_file():
            ref_audio = ref_audio_candidate

        # --- Optional avatar image -----------------------------------------
        avatar_path = None
        for ext in (".jpg", ".jpeg"):
            img = entry / ("image" + ext)
            if img.is_file():
                avatar_path = img
                break

        # --- Build persona -------------------------------------------------
        personas[persona_name] = Persona(
            name=persona_name,
            path=entry,
            prompt=system_prompt,
            language=language,
            description=description,
            router_hints=router_hints,
            avatar_color=avatar_color,
            allow_tool_calls=allow_tool_calls,
            memory_size=memory_size,
            ref_text=ref_text,
            ref_audio_path=ref_audio,
            avatar_path=avatar_path,
        )

        logger.info(
            "Loaded persona %r: prompt=%d chars, language=%s, "
            "voice_clone=%s",
            persona_name,
            len(system_prompt),
            language,
            "yes" if personas[persona_name].is_voice_cloned else "no",
        )

    logger.info("Loaded %d persona(s) from %s", len(personas), personas_path)
    return personas


def get_persona(personas: dict[str, Persona], name: str | None) -> Persona | None:
    """Look up a persona by name, returning ``None`` if not found or name is empty."""
    if not name:
        return None
    return personas.get(name)


# ---------------------------------------------------------------------------
# Hot-reload support
# ---------------------------------------------------------------------------

def reload_personas(personas_dir: str | Path | None = None) -> dict[str, Persona]:
    """Reload personas from disk, logging the result.

    Call this after the persona editor saves changes to refresh the persona
    registry without restarting the server.  Returns the newly loaded dict.
    """
    return load_personas(personas_dir)

