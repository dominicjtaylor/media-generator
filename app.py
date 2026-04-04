"""
app.py — FastAPI carousel generator + React frontend.

API routes
  POST /generate  { "topic": "..." }  →  { "images_url", "slides", "csv" }
  GET  /healthz   → { "status": "ok" }
  GET  /docs      → Swagger UI

Frontend (SPA)
  GET  /          → React app (index.html)
  GET  /*         → React app (client-side routing fallback)
  GET  /assets/*  → JS/CSS bundles (served as static files)

Pipeline
  1. Claude generates slides (CSV validated internally)
  2. If CONTENTDRIPS_API_KEY is set:
       format slides → POST to Contentdrips → poll → return images_url
  3. Else: return CSV as fallback (useful during development)
"""

import json
import logging
import os
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import generate_slides
from contentdrips import format_for_contentdrips, request_render

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")

app = FastAPI(title="Carousel Generator API", version="1.0.0")

DIST = Path(__file__).parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str


# ---------------------------------------------------------------------------
# API routes  — registered before static file mounts
# ---------------------------------------------------------------------------

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream(topic: str) -> Generator[str, None, None]:
    """Sync generator that emits SSE events at each real pipeline stage."""
    # ── Step 1: Generate slides via Claude ───────────────────────────────
    logger.info("Generating slides for topic: %r", topic)
    yield _sse({"step": "generating", "message": "Generating carousel content..."})

    try:
        slides, csv_text = generate_slides(topic)
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    logger.info("Generated %d slides", len(slides))
    slide_models = [{"heading": s["heading"], "description": s["description"]} for s in slides]

    # ── Step 2: Send to Contentdrips (if API key is configured) ──────────
    yield _sse({"step": "designing", "message": "Designing slides..."})

    if os.environ.get("CONTENTDRIPS_API_KEY"):
        buffered_events: list[dict] = []

        def on_progress(event: dict) -> None:
            buffered_events.append(event)

        try:
            carousel_payload = format_for_contentdrips(slides)
            yield _sse({"step": "rendering", "message": "Rendering images..."})

            image_urls, _ = request_render(carousel_payload, progress_callback=on_progress)

            # Emit any progress events collected during polling
            for ev in buffered_events:
                yield _sse(ev)

            logger.info("Carousel ready: %d images", len(image_urls))
            yield _sse({"step": "complete", "images": image_urls, "slides": slide_models})

        except (RuntimeError, TimeoutError) as exc:
            logger.error("Contentdrips pipeline failed: %s", exc)
            yield _sse({"step": "error", "message": str(exc)})

    else:
        # ── Step 3: CSV fallback (no Contentdrips key) ───────────────────
        logger.info("CONTENTDRIPS_API_KEY not set — returning CSV fallback")
        yield _sse({"step": "complete", "images": [], "slides": slide_models, "csv": csv_text})


@app.post("/generate", tags=["carousel"])
def generate(req: GenerateRequest):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    return StreamingResponse(
        _stream(topic),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Static assets (/assets/index-abc123.js, /assets/index-abc123.css)
# ---------------------------------------------------------------------------

_assets = DIST / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    logger.info("Serving frontend assets from %s", _assets)
else:
    logger.warning("frontend/dist/assets not found — run: cd frontend && npm run build")


# ---------------------------------------------------------------------------
# SPA catch-all
# ---------------------------------------------------------------------------

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse(
        content=(
            "<h2>Frontend not built</h2>"
            "<p>Run <code>cd frontend &amp;&amp; npm run build</code> "
            "then restart the server.</p>"
            "<p>API docs: <a href='/docs'>/docs</a></p>"
        ),
        status_code=503,
    )


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
