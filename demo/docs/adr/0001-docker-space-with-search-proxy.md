# Convert the static Space into a Docker app with a search proxy

To give the model a web search tool, the executor (which runs in the browser of a
public Space) needs a search endpoint. A `sdk: static` Space serves files as-is with no
runtime process, so it cannot proxy external requests.
We convert the Space from `sdk: static` to `sdk: docker`: a single container runs a
small server (FastAPI + uvicorn) that both serves the existing front-end *unchanged*
and exposes a same-origin `/search` proxy that forwards to a SearXNG instance. The
whole app lives in that one container; the s2s speech-to-speech backend stays the
separate load-balanced service it already is.

## Considered options

- **Stay static, direct client requests** — no server proxy; the browser talks to
  SearXNG directly. Rejected as the default because a public SearXNG instance without
  CORS middleware cannot be reached from the browser.
- **Separate proxy service** — same proxying, but splits the app across two deploys.
  Rejected: the app must live in one container.
- **Route through llama-server** — adds unnecessary middleware. Rejected: direct
  SearXNG access is simpler.

## Consequences

- Deployment is no longer static: there is a Dockerfile and a server process; the
  README front-matter changes from `sdk: static` to `sdk: docker`.
- The client calls `/search` same-origin; the server forwards to `SEARXNG_URL`.
- The front-end, audio pipeline, and s2s handshake are untouched — only the hosting
  shape and the new route are added.
