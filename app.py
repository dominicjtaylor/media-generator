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

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
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


class Slide(BaseModel):
    heading:     str
    description: str


class GenerateResponse(BaseModel):
    slides:     list[Slide]           = []    # structured slides (always present)
    images:     list[str]            = []    # direct PNG URLs (from Contentdrips S3)
    images_url: Optional[str]        = None  # Contentdrips export page URL (kept for compat)
    csv:        Optional[str]        = None  # raw CSV (fallback when no API key)
    debug:      Optional[dict]       = None  # temporary: raw Contentdrips response


# ---------------------------------------------------------------------------
# API routes  — registered before static file mounts
# ---------------------------------------------------------------------------

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse, tags=["carousel"])
def generate(req: GenerateRequest):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    # ── Step 1: Generate slides via Claude ───────────────────────────────
    logger.info("Generating slides for topic: %r", topic)
    try:
        slides, csv_text = generate_slides(topic)
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")

    logger.info("Generated %d slides", len(slides))
    logger.debug("Slides: %s", slides)

    slide_models = [Slide(heading=s["heading"], description=s["description"]) for s in slides]

    # ── Step 2: Send to Contentdrips (if API key is configured) ──────────
    if os.environ.get("CONTENTDRIPS_API_KEY"):
        try:
            carousel_payload         = format_for_contentdrips(slides)
            image_urls, raw_response = request_render(carousel_payload)

            logger.info("Carousel ready: %d images", len(image_urls))
            return GenerateResponse(
                slides=slide_models,
                images=image_urls,
                images_url=image_urls[0] if image_urls else None,
                csv=csv_text,
                debug={"raw_response": raw_response},
            )

        except (RuntimeError, TimeoutError) as exc:
            logger.error("Contentdrips pipeline failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc))

    # ── Step 3: CSV fallback (no Contentdrips key) ───────────────────────
    logger.info("CONTENTDRIPS_API_KEY not set — returning CSV fallback")
    return GenerateResponse(slides=slide_models, csv=csv_text)


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
