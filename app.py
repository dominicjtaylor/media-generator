"""
app.py — FastAPI carousel generator + React frontend.

API routes
  POST /generate  { "topic": "..." }  →  SSE stream of progress events
  GET  /healthz   → { "status": "ok" }

Frontend (SPA)
  GET  /          → React app (index.html)
  GET  /assets/*  → JS/CSS bundles
  GET  /renders/* → rendered slide PNGs (served from /tmp/renders/)

Pipeline
  1. Claude generates 5 slides (JSON output)          → SSE step: "generating"
  2. renderer.render_slides() → HTML + Playwright PNGs → SSE step: "rendering"
  3. SSE step: "complete" { images: ["/renders/…/slide-n.png"] }
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
from renderer import render_slides

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")

app = FastAPI(title="Carousel Generator API", version="2.0.0")

DIST        = Path(__file__).parent / "frontend" / "dist"
RENDERS_DIR = Path("/tmp/renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream(topic: str) -> Generator[str, None, None]:
    """Sync generator that emits SSE events at each real pipeline stage."""
    print("HTML PIPELINE ACTIVE")

    # Step 1: Generate slides via Claude
    logger.info("Generating slides for topic: %r", topic)
    yield _sse({"step": "generating", "message": "Generating carousel content..."})

    try:
        slides, _ = generate_slides(topic)
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    logger.info("Generated %d slides", len(slides))
    slide_models = [{"heading": s["heading"], "description": s["description"]} for s in slides]

    # Step 2: Render slides to PNG via Playwright
    print("Rendering slides via Playwright")
    yield _sse({"step": "rendering", "message": "Rendering slides..."})

    try:
        png_paths, run_id = render_slides(slides, renders_base=str(RENDERS_DIR))
    except Exception as exc:
        logger.error("Rendering failed: %s", exc)
        yield _sse({"step": "error", "message": f"Rendering failed: {exc}"})
        return

    image_urls = [f"/renders/{run_id}/slide-{i + 1}.png" for i in range(len(png_paths))]
    logger.info("Carousel ready: %d images for run %s", len(image_urls), run_id)

    yield _sse({"step": "complete", "images": image_urls, "slides": slide_models})


# ---------------------------------------------------------------------------
# API routes  -- registered before static file mounts
# ---------------------------------------------------------------------------

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


@app.post("/generate", tags=["carousel"])
def generate(req: GenerateRequest):
    print("=== NEW BACKEND RUNNING ===")
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    return StreamingResponse(
        _stream(topic),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Rendered PNGs  (/renders/<run_id>/slide-n.png)
# ---------------------------------------------------------------------------

app.mount("/renders", StaticFiles(directory=str(RENDERS_DIR)), name="renders")


# ---------------------------------------------------------------------------
# Frontend static assets (/assets/index-abc123.js, etc.)
# ---------------------------------------------------------------------------

_assets = DIST / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    logger.info("Serving frontend assets from %s", _assets)
else:
    logger.warning("frontend/dist/assets not found -- run: cd frontend && npm run build")


# ---------------------------------------------------------------------------
# SPA catch-all  (must be last -- after all mounts)
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
