"""
app.py — FastAPI carousel generator, serves the React frontend.

POST /generate  { "topic": "..." }  →  { "csv": "..." }
GET  /          → React SPA (built frontend)
GET  /healthz   → health check
GET  /docs      → Swagger UI (still available)
"""

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import generate_csv

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")

app = FastAPI(
    title="Carousel Generator API",
    version="1.0.0",
    # Keep docs accessible at /docs even after mounting static files
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str


class GenerateResponse(BaseModel):
    csv: str


# ---------------------------------------------------------------------------
# API routes  (must be registered BEFORE the static files mount)
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
# Serve the React frontend (built by `npm run build` in frontend/)
# Mounted last so all API routes above take priority.
# html=True → unknown paths return index.html (client-side routing support).
# ---------------------------------------------------------------------------

_frontend_dist = Path(__file__).parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
    logger.info("Serving frontend from %s", _frontend_dist)
else:
    logger.warning(
        "Frontend not built — run `npm --prefix frontend run build`. "
        "API-only mode active; visit /docs."
    )


# ---------------------------------------------------------------------------
# Local dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
