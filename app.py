"""
app.py — FastAPI wrapper around the carousel CSV generator.

POST /generate  { "topic": "..." }  →  { "csv": "..." }
"""

import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import generate_csv

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")

app = FastAPI(title="Carousel Generator API", version="1.0.0")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic: str


class GenerateResponse(BaseModel):
    csv: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    # Use a temp file so the endpoint is stateless (safe on Railway / any ephemeral FS)
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
# Entry point (local dev)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
