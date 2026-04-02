"""
app.py — FastAPI carousel generator + React frontend.

API routes
  POST /generate  { "topic": "..." }  →  { "csv": "..." }
  GET  /healthz   → { "status": "ok" }
  GET  /docs      → Swagger UI

Frontend (SPA)
  GET  /          → React app (index.html)
  GET  /*         → React app (client-side routing fallback)
  GET  /assets/*  → JS/CSS bundles (served as static files)
"""

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import generate_csv

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")

# Docs are still reachable at /docs for debugging — they take priority
# over the SPA catch-all route because FastAPI registers them as explicit routes.
app = FastAPI(title="Carousel Generator API", version="1.0.0")

DIST = Path(__file__).parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str


class GenerateResponse(BaseModel):
    csv: str


# ---------------------------------------------------------------------------
# API routes  — registered first, always take priority over the SPA catch-all
# ---------------------------------------------------------------------------

@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse, tags=["carousel"])
def generate(req: GenerateRequest):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        csv_file = generate_csv(topic, output_path=tmp_path)
        content = csv_file.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Generation failed for topic %r: %s", topic, exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return GenerateResponse(csv=content)


# ---------------------------------------------------------------------------
# Static asset files  (/assets/index-abc123.js, /assets/index-abc123.css)
# Mounted before the catch-all so Starlette serves them as real files.
# ---------------------------------------------------------------------------

_assets = DIST / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    logger.info("Serving frontend assets from %s", _assets)
else:
    logger.warning("frontend/dist/assets not found — run: cd frontend && npm run build")


# ---------------------------------------------------------------------------
# SPA catch-all  — serves index.html for every path not matched above.
# FastAPI's explicit routes (/docs, /healthz, /generate, /assets/*) all take
# priority; this only fires for paths that nothing else matched.
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
