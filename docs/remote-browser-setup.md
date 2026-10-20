# Remote Browser Support Setup Guide

This document describes how to set up the speech-to-speech demo for remote browser
access over HTTPS with Caddy as a TLS-terminating reverse proxy.

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │           Caddy (:443)                   │
                    │   TLS termination + reverse proxy        │
                    │                                          │
browser ──HTTPS──→  │  /api/*, /*  ──→ demo server :7860       │
                    │  /v1/*       ──→ s2s backend :8765 (WS)  │
                    └──────────────────────────────────────────┘
                                     │
                      ┌──────────────┴──────────────┐
                      │                             │
              demo server :7860          s2s backend :8765
              (plain HTTP)               (plain WebSocket)
```

Key principle: **Caddy terminates TLS**. The demo and s2s servers speak plain
HTTP/WS internally. Only the browser talks HTTPS to Caddy.

## Prerequisites

1. Two servers running on the same machine:
   - **Demo server** on port 7860 (plain HTTP)
   - **s2s backend** on port 8765 (plain WebSocket)

2. Caddy installed:
   ```bash
   curl -s https://caddyserver.com/api/v1/install?os=linux | sudo sh
   ```

3. Self-signed certificate (generated once):
   ```bash
   python scripts/generate_cert.py
   ```

## Configuration

### Caddyfile (`cert/Caddyfile`)

```
:443 {
    tls /home/dennis/projects/speech-to-speech/cert/cert.pem /home/dennis/projects/speech-to-speech/cert/key.pem

    # WebSocket to s2s backend (must match /v1/* paths)
    reverse_proxy /v1/* 127.0.0.1:8765

    # Everything else to demo server
    reverse_proxy 127.0.0.1:7860
}
```

**Important notes:**
- Path matchers matter: `/v1/*` routes to the s2s backend, everything else to demo.
- Without path matchers, `reverse_proxy 127.0.0.1:7860` (the broader one) catches
  `/v1/realtime` and sends it to the demo server instead of the s2s backend.
- Use `127.0.0.1` (not `localhost`) to force IPv4 — Caddy may resolve `localhost`
  to `::1` (IPv6) while uvicorn only binds IPv4.
- The wildcard host (`:443` without a hostname) matches any Host header, so you
  can access the server by IP, `localhost`, or any hostname in `/etc/hosts`.

### Environment Variables

**Demo server** (on the same machine):
```bash
export SPEECH_TO_SPEECH_URL=wss://<server-ip>/v1/realtime
```
This points the demo through Caddy (port 443, WSS), not directly to the s2s backend.

```bash
export SEARXNG_URL=http://localhost:8888
```
Use `localhost` — the public IP may not be reachable from the demo process.

**Demo server** (plain HTTP, no TLS flags):
```bash
uvicorn --app-dir demo server:app --host 0.0.0.0 --port 7860
```

**s2s backend** (plain WebSocket, no WSS):
```bash
uv run speech-to-speech serve --stt parakeet-tdt --llm_backend transformers \
  --tts kokoro --model_name "Qwen/Qwen3-4B-Instruct-2507" --llm_device mps \
  --enable_live_transcription
```

## Running Caddy

```bash
caddy run --config cert/Caddyfile
```

Caddy listens on port 443 and handles all TLS. If you need to use a different
port, change `:443` to `:8443` and access via `https://<ip>:8443`.

## Client Setup

On each client machine, add an `/etc/hosts` entry if using a hostname:
```
100.69.175.215  asus
```

Then open `https://asus` (or `https://100.69.175.215`) in the browser.

The browser will show a certificate warning for the self-signed cert — accept it
to proceed.

## Persona Hot-Reload

After editing personas in the persona editor, go to the **demo app** and click
the **reload button** (🔄 icon next to the stop button). This:

1. Reloads the demo server's persona cache from disk
2. Forwards to the s2s server's `/admin/reload` endpoint
3. Updates all pipeline units' persona registries
4. Rebuilds TTS voice-clone prompts for voice-cloned personas
5. Refreshes the voice dropdown in the demo app

No restart needed. The persona editor itself has no reload functionality — it
just writes files to the `Personas/` directory.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Empty responses from demo server | IPv6 resolution issue | Use `127.0.0.1` not `localhost` |
| 403 on WebSocket | Caddy not proxying `/v1/*` | Add path matcher: `reverse_proxy /v1/*` |
| "insecure WebSocket" error | Page HTTPS + WS backend | Point `SPEECH_TO_SPEECH_URL` through Caddy (`wss://`) |
| Search doesn't work | `SEARXNG_URL` uses public IP | Change to `http://localhost:8888` |
| Certificate not trusted | Self-signed cert | Install Caddy's CA or use mkcert |

## File Locations

| File | Purpose |
|------|---------|
| `cert/Caddyfile` | Caddy reverse proxy config |
| `cert/cert.pem` | TLS certificate |
| `cert/key.pem` | TLS private key |
| `scripts/generate_cert.py` | Script to generate self-signed certs |
