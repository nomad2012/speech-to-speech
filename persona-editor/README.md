# Persona Editor

A standalone web app for managing personas in the speech-to-speech project.

## Features

- **List** all personas in a responsive grid
- **Create** new personas with name, description, system prompt, and more
- **Edit** existing personas
- **Clone** personas to create variations
- **Delete** personas with confirmation
- **Upload** avatar images (png, jpg, gif, webp)
- **Upload/Play/Remove** reference audio (WAV)
- **Configure** language, memory size, tool call permissions, and more

## Setup

```bash
cd persona-editor
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

Then open http://localhost:8765 in your browser.

## Personas Directory

The app reads personas from the `Personas/` directory (one level up from this app).
Each persona lives in its own subdirectory:

```
Personas/
  Name/
    prompt.md         — YAML frontmatter + system prompt
    language.txt      — 2-letter language code (e.g. "en")
    ref.wav           — Reference audio for voice cloning
    ref.txt           — Transcript of reference audio
    image.png         — Avatar image (optional)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/personas` | List all personas (summary) |
| POST | `/api/personas` | Create a new persona (multipart/form-data) |
| GET | `/api/personas/{name}/detail` | Get full persona detail |
| PUT | `/api/personas/{name}` | Update a persona (multipart/form-data) |
| DELETE | `/api/personas/{name}` | Delete a persona |
| POST | `/api/personas/{name}/clone` | Clone a persona |
| GET | `/api/personas/{name}/avatar` | Serve avatar image |
| GET | `/api/personas/{name}/reference-audio` | Serve reference audio |

## Port

Default: `8765`. Change the port in `server.py` if needed.
