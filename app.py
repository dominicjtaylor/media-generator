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
import re as _re
from pathlib import Path
from typing import Generator, Optional
from urllib.parse import quote

import anthropic as _anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import generate_slides, select_template_style
from renderer import render_slides

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")
print("Starting FastAPI server...")

app = FastAPI(title="Carousel Generator API", version="2.0.0")

DIST        = Path(__file__).parent / "frontend" / "dist"
RENDERS_DIR = Path("/tmp/renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

# Arc labels used to tell Claude where in the narrative each slide sits.
_ARC = ["Problem", "Cost", "Shift", "System", "Proof", "Decision", "CTA"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Zero-width and invisible Unicode characters that browsers may append to
# text copied from web pages.  Strip these before passing to any LLM call.
_ZW_CHARS  = _re.compile(r'[\u200b\u200c\u200d\u200e\u200f\u00ad\ufeff]+')
_CITE_RE   = _re.compile(r'<cite[^>]*>(.*?)</cite>', _re.DOTALL | _re.IGNORECASE)
_MD_RE     = _re.compile(r'\*{1,2}|_{1,2}|~~')

def _strip_citations(text: str) -> str:
    return _CITE_RE.sub(r'\1', text)

def _strip_markdown(text: str) -> str:
    return _MD_RE.sub('', text)


def _clean_topic(text: str) -> str:
    """Strip whitespace and zero-width Unicode characters from topic input."""
    return _ZW_CHARS.sub('', text).strip()


def _claude(prompt: str, max_tokens: int = 1024) -> str:
    """Single-turn Claude call, returns response text."""
    client = _anthropic.Anthropic()
    model  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _parse_json(text: str):
    """Parse JSON from a Claude response, stripping markdown code fences."""
    text = text.strip()
    text = _re.sub(r"^```(?:json)?\s*", "", text)
    text = _re.sub(r"\s*```$", "", text.strip())
    return json.loads(text.strip())


def _arc_position(slide_number: int, total: int) -> str:
    """Map a 1-based slide number to its arc stage name."""
    if total <= 1:
        return _ARC[0]
    idx = round((slide_number - 1) / max(total - 1, 1) * (len(_ARC) - 1))
    return _ARC[min(idx, len(_ARC) - 1)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    topic:      str
    num_slides: int = 5

    def validate_num_slides(self) -> None:
        if not (4 <= self.num_slides <= 10):
            raise HTTPException(status_code=422, detail="num_slides must be between 4 and 10")


class HookRequest(BaseModel):
    topic:      str
    num_slides: int = 5


class SlidesRequest(BaseModel):
    topic:          str
    hook:           str
    num_slides:     int = 5
    image_filename: Optional[str] = None


class QcRequest(BaseModel):
    topic:  str
    slides: list[dict]


class RegenerateRequest(BaseModel):
    topic:       str
    slide_index: int
    hook:        str
    slides:      list[dict]
    issue:       str = ""
    suggestion:  str = ""


class RenderRequest(BaseModel):
    topic:          str
    slides:         list[dict]
    style:          str = "text_only"
    image_filename: Optional[str] = None


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream(topic: str, num_slides: int) -> Generator[str, None, None]:
    """Sync generator that emits SSE events at each real pipeline stage."""
    print("HTML PIPELINE ACTIVE")

    # Step 0: Select template style
    style = select_template_style()
    logger.info("Template style selected: %s", style)

    # Step 1: Generate slides via Claude
    logger.info("Generating slides for topic: %r (style=%s)", topic, style)
    yield _sse({"step": "generating", "message": "Generating and refining carousel content..."})

    try:
        slides, caption = generate_slides(topic, num_slides=num_slides, template_style=style)
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    logger.info("Generated %d slides", len(slides))

    # Step 2: Fetch image (only for headings_text_image)
    image_data = None
    if style == "headings_text_image":
        import os as _os
        yield _sse({"step": "fetching_image", "message": "Fetching image..."})
        try:
            if _os.getenv("LUMMI_API_KEY"):
                from image_fetcher import fetch_lummi_image
                image_data = fetch_lummi_image(topic)
                logger.info("Lummi image fetched: %s", image_data.get("local_path"))
            else:
                from local_image import get_image_for_heading_template
                logger.info("Using local image fallback for topic: %r", topic)
                image_data = get_image_for_heading_template(topic)

            if image_data.get("author_name"):
                author_url = image_data.get("author_url", "")
                credit = image_data["author_name"]
                if author_url:
                    credit += f" ({author_url})"
                caption += f"\n\nImage credit: {credit}"
        except Exception as exc:
            logger.warning("Image fetch failed (%s) — falling back to text_only", exc)
            style      = "text_only"
            image_data = None
            try:
                slides, caption = generate_slides(
                    topic, num_slides=num_slides, template_style=style
                )
            except Exception as exc2:
                logger.error("Fallback generation failed: %s", exc2)
                yield _sse({"step": "error", "message": f"Content generation failed: {exc2}"})
                return

    # Step 3: Render slides to PNG via Playwright
    print("Rendering slides via Playwright")
    yield _sse({"step": "rendering", "message": "Rendering slides..."})

    try:
        png_paths, run_id = render_slides(
            slides,
            renders_base=str(RENDERS_DIR),
            template_style=style,
            image_data=image_data,
        )
    except Exception as exc:
        logger.error("Rendering failed: %s", exc)
        yield _sse({"step": "error", "message": f"Rendering failed: {exc}"})
        return

    # Build slide_models for frontend — map internal "body" back to "description"
    slide_models = [
        {
            "type":        s.get("type", "content"),
            "heading":     s.get("heading", ""),
            "description": s.get("body", ""),
        }
        for s in slides
    ]

    image_urls = [f"/renders/{run_id}/slide-{i + 1}.png" for i in range(len(png_paths))]
    logger.info("Carousel ready: %d images for run %s (style=%s)", len(image_urls), run_id, style)

    yield _sse({"step": "complete", "images": image_urls, "slides": slide_models, "caption": caption})


# ---------------------------------------------------------------------------
# API routes  -- registered before static file mounts
# ---------------------------------------------------------------------------

_HOOK_PROMPT = """\
Write 4 carousel hooks for {topic} that make my ideal client think: \
if I don't read this, I'll stay stuck.

Use these 4 formats, one each:

1. Specific promise — a concrete outcome with a number or timeframe
   e.g. "How I fixed this in 3 days"

2. Pattern interrupt — starts mid-thought or breaks an assumption
   e.g. "Tell me if I'm wrong...", "Stop scrolling if you want to..."

3. Contrast — exposes a gap between what people believe and reality
   e.g. "You've been lied to about...", "This is the truth about..."

4. Named thing — makes the reader feel like they're missing something \
specific that already exists
   e.g. "This feels illegal to know", "This is all over Instagram"

Rules for every hook:
- Maximum 8 words
- No full sentences — fragments and ellipses are fine
- No generic AI phrasing (unleash, discover, unlock, game-changer)
- Must create a gap the reader needs to close by swiping
- Write for someone who is mid-scroll and slightly sceptical

Return as a JSON array of 4 objects:
[
  {{"type": "specific_promise", "hook": "..."}},
  {{"type": "pattern_interrupt", "hook": "..."}},
  {{"type": "contrast", "hook": "..."}},
  {{"type": "named_thing", "hook": "..."}}
]"""

_QC_PROMPT = """\
Read the following carousel slides, starting from slide 2. Flag any slides that \
repeat the same idea, feel too vague, or don't advance the argument. \
Do not evaluate slide 1. Suggest a replacement for each flagged slide.

IMPORTANT: Only flag issues based on the exact text provided below. Do not reference \
or quote any text that does not appear verbatim in the slide content given. If you \
cannot point to specific words in the slide that demonstrate the problem, do not flag it.

Return as a JSON array of objects with keys: slide_number, issue, \
replacement_heading, replacement_body. Return an empty array if no issues found.

Carousel:
{slides_json}"""

_REGEN_PROMPT = """\
Write like a smart 10 year old explaining something to a friend. \
Short words. Short sentences. Specific details. No jargon.

You are rewriting slide {slide_num} of {total} in an Instagram carousel about "{topic}".

The hook on slide 1 is: "{hook}"

This slide sits at the {arc} stage of the arc:
Problem → Cost → Shift → System → Proof → Decision → CTA

Here are all the other slides in the carousel for context — do NOT reproduce any of their \
ideas. Your rewrite must introduce a distinct idea that fits the {arc} stage and does not \
repeat anything already covered:

{other_slides}

{issue_block}\
Write complete sentences only. Every sentence must end with a full stop. No fragments. \
No sentences that trail off.

Write a new version of slide {slide_num} only. One idea. Two to three complete sentences. \
Return as JSON with keys "heading" and "body" — no type, no markdown, just the object:
{{"heading": "...", "body": "..."}}"""


@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}


@app.post("/hooks", tags=["carousel"])
def hooks_route(req: HookRequest):
    """Generate 4 hook options for a given topic."""
    topic = _clean_topic(req.topic)
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    prompt = _HOOK_PROMPT.format(topic=topic)
    try:
        raw  = _claude(prompt, max_tokens=512)
        data = _parse_json(raw)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")
        hooks = [
            {"hook": str(h.get("hook", "")), "type": str(h.get("type", ""))}
            for h in data[:4]
        ]
    except Exception as exc:
        logger.error("Hook generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Hook generation failed: {exc}")

    return {"hooks": hooks}


def _slides_stream(topic: str, hook: str, num_slides: int, image_filename: Optional[str] = None) -> Generator[str, None, None]:
    """SSE stream: generate slides using the selected hook."""
    # Validate selected image exists before spending time on generation
    if image_filename:
        _img_path = _LOCAL_IMAGE_DIR / image_filename
        if not _img_path.exists():
            logger.error("Selected image not found on disk: %s", _img_path)
            yield _sse({"step": "error", "message": f"Image not found: {image_filename}. Please go back and select a different image."})
            return

    # Use image template when the user has picked an image
    style = "headings_text_image" if image_filename else select_template_style()
    yield _sse({"step": "generating", "message": "Generating slides..."})

    try:
        slides, caption = generate_slides(
            topic, num_slides=num_slides, template_style=style, hook=hook
        )
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    image_data = None
    if style == "headings_text_image":
        yield _sse({"step": "fetching_image", "message": "Fetching image..."})
        try:
            if os.getenv("LUMMI_API_KEY"):
                from image_fetcher import fetch_lummi_image
                image_data = fetch_lummi_image(topic)
            else:
                from local_image import get_image_for_heading_template
                image_data = get_image_for_heading_template(topic, image_filename)
            if image_data and image_data.get("author_name"):
                author_url = image_data.get("author_url", "")
                credit = image_data["author_name"]
                if author_url:
                    credit += f" ({author_url})"
                caption += f"\n\nImage credit: {credit}"
        except Exception as exc:
            logger.warning("Image fetch failed (%s) — falling back to text_only", exc)
            style      = "text_only"
            image_data = None
            try:
                slides, caption = generate_slides(
                    topic, num_slides=num_slides, template_style=style, hook=hook
                )
            except Exception as exc2:
                yield _sse({"step": "error", "message": f"Content generation failed: {exc2}"})
                return

    slide_models = [
        {"type": s.get("type", "content"), "heading": s.get("heading", ""), "description": s.get("body", "")}
        for s in slides
    ]
    yield _sse({
        "step":    "complete",
        "slides":  slide_models,
        "caption": caption,
        "style":   style,
        **({"image_data": {"local_path": image_data.get("local_path", "")}} if image_data else {}),
    })


@app.post("/slides", tags=["carousel"])
def slides_route(req: SlidesRequest):
    """Generate carousel slides using the user-selected hook (SSE stream)."""
    topic = _clean_topic(req.topic)
    hook  = req.hook.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    if not hook:
        raise HTTPException(status_code=422, detail="hook must not be empty")
    if not (4 <= req.num_slides <= 10):
        raise HTTPException(status_code=422, detail="num_slides must be between 4 and 10")

    return StreamingResponse(
        _slides_stream(topic, hook, req.num_slides, req.image_filename),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _qc_flag_is_grounded(flag: dict, slides: list[dict]) -> bool:
    """Return False if the flag quotes text that doesn't appear in the slide.

    The QC LLM occasionally hallucinates issues that reference words or phrases
    not present in the actual slide.  If the issue field contains a quoted string
    (double quotes), we verify that string appears verbatim in the slide text.
    Flags without any quoted text are accepted as-is.
    """
    slide_num = flag.get("slide_number")
    if not isinstance(slide_num, int) or not (1 <= slide_num <= len(slides)):
        return False

    issue = flag.get("issue", "")
    quoted = _re.findall(r'"([^"]{4,})"', issue)   # only check substantive quotes (4+ chars)
    if not quoted:
        return True

    slide = slides[slide_num - 1]
    slide_text = (
        slide.get("heading", "") + " " + slide.get("description", slide.get("body", ""))
    ).lower()

    for q in quoted:
        if q.lower() not in slide_text:
            logger.info(
                "QC flag for slide %d discarded — quoted text %r not found in slide",
                slide_num, q,
            )
            return False

    return True


@app.post("/qc", tags=["carousel"])
def qc_route(req: QcRequest):
    """QC-check slides and return a list of flags."""
    topic = _clean_topic(req.topic)
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    if not req.slides:
        raise HTTPException(status_code=422, detail="slides must not be empty")

    slides_from_2 = req.slides[1:]   # slide 1 is the hook — never QC'd
    slides_json   = json.dumps(slides_from_2, indent=2)
    prompt        = _QC_PROMPT.format(slides_json=slides_json)
    try:
        raw   = _claude(prompt, max_tokens=1024)
        flags = _parse_json(raw)
        if not isinstance(flags, list):
            flags = []
        # Strip web-search citation tags and markdown from any replacement text
        for f in flags:
            if isinstance(f.get("replacement_heading"), str):
                f["replacement_heading"] = _strip_citations(f["replacement_heading"])
            if isinstance(f.get("replacement_body"), str):
                f["replacement_body"] = _strip_markdown(_strip_citations(f["replacement_body"]))
        # Discard any flag that quotes text not present in the actual slide
        before = len(flags)
        flags  = [f for f in flags if _qc_flag_is_grounded(f, req.slides)]
        if len(flags) < before:
            logger.info("QC: discarded %d hallucinated flag(s)", before - len(flags))
    except Exception as exc:
        logger.warning("QC parse failed (%s) — returning no flags", exc)
        flags = []

    return {"flags": flags}


@app.post("/regenerate", tags=["carousel"])
def regenerate_route(req: RegenerateRequest):
    """Regenerate a single slide at the given index."""
    topic = _clean_topic(req.topic)
    idx   = req.slide_index
    if not (0 <= idx < len(req.slides)):
        raise HTTPException(status_code=422, detail="slide_index out of range")

    slide      = req.slides[idx]
    total      = len(req.slides)
    arc        = _arc_position(idx + 1, total)
    slide_type = slide.get("type", "content")

    # Build context list of every OTHER slide with its arc position so the
    # LLM knows exactly what has already been said and won't repeat ideas.
    other_lines: list[str] = []
    for i, s in enumerate(req.slides):
        if i == idx:
            continue
        s_arc    = _arc_position(i + 1, total)
        heading  = s.get("heading", "")
        body     = s.get("description", s.get("body", ""))
        entry    = f"Slide {i + 1} ({s_arc}): {heading}"
        if body:
            entry += f" — {body}"
        other_lines.append(entry)
    other_slides = "\n".join(other_lines)

    issue_block = (
        f"Issue with the current slide: {req.issue}\nSuggestion: {req.suggestion}\n\n"
        if req.issue else ""
    )

    prompt = _REGEN_PROMPT.format(
        slide_num=idx + 1,
        total=total,
        topic=topic,
        hook=req.hook or topic,
        arc=arc,
        other_slides=other_slides,
        issue_block=issue_block,
    )
    try:
        raw       = _claude(prompt, max_tokens=512)
        new_slide = _parse_json(raw)
        if not isinstance(new_slide, dict):
            raise ValueError("Expected a JSON object")
        new_slide.setdefault("type", slide_type)
    except Exception as exc:
        logger.error("Regenerate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

    return {"slide": {"type": new_slide.get("type", slide_type),
                      "heading": _strip_citations(new_slide.get("heading", "")),
                      "description": _strip_markdown(_strip_citations(new_slide.get("body", new_slide.get("description", ""))))}}


def _render_stream(
    topic: str, slides: list[dict], style: str, image_filename: Optional[str] = None
) -> Generator[str, None, None]:
    """SSE stream: render approved slides to PNG."""
    yield _sse({"step": "rendering", "message": "Rendering slides..."})

    internal_slides = [
        {"type": s.get("type", "content"),
         "heading": _strip_citations(s.get("heading", "")),
         "body":    _strip_markdown(_strip_citations(s.get("description", "")))}
        for s in slides
    ]

    image_data = None
    if style == "headings_text_image":
        try:
            if os.getenv("LUMMI_API_KEY"):
                from image_fetcher import fetch_lummi_image
                image_data = fetch_lummi_image(topic)
            else:
                from local_image import get_image_for_heading_template
                image_data = get_image_for_heading_template(topic, image_filename)
        except Exception as exc:
            logger.warning("Image fetch failed for render (%s) — using text_only", exc)
            style = "text_only"

    try:
        png_paths, run_id = render_slides(
            internal_slides,
            renders_base=str(RENDERS_DIR),
            template_style=style,
            image_data=image_data,
        )
    except Exception as exc:
        logger.error("Rendering failed: %s", exc)
        yield _sse({"step": "error", "message": f"Rendering failed: {exc}"})
        return

    image_urls = [f"/renders/{run_id}/slide-{i + 1}.png" for i in range(len(png_paths))]
    logger.info("Render complete: %d images (run=%s style=%s)", len(image_urls), run_id, style)
    yield _sse({"step": "complete", "images": image_urls})


@app.post("/render", tags=["carousel"])
def render_route(req: RenderRequest):
    """Render approved slides to PNG (SSE stream)."""
    topic = _clean_topic(req.topic)
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    if not req.slides:
        raise HTTPException(status_code=422, detail="slides must not be empty")

    return StreamingResponse(
        _render_stream(topic, req.slides, req.style, req.image_filename),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/generate", tags=["carousel"])
def generate(req: GenerateRequest):
    print("=== NEW BACKEND RUNNING ===")
    print(f"Received topic={req.topic!r} num_slides={req.num_slides}")
    topic = _clean_topic(req.topic)
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    req.validate_num_slides()

    return StreamingResponse(
        _stream(topic, req.num_slides),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Local image library  (/api/images)
# ---------------------------------------------------------------------------

_LOCAL_IMAGE_DIR = Path(__file__).parent / "assets" / "lummi_images"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_THUMBS_DIR = Path("/tmp/thumbnails")
_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

_THUMB_MAX_W = 300
_THUMB_MAX_H = 200
_THUMB_QUALITY = 60


def _make_thumbnail(src: Path) -> Path:
    """Return path to a cached JPEG thumbnail for *src*, generating it if needed."""
    from PIL import Image as _PilImage

    # Thumbnails are always stored as JPEG; use stem so PNG → .jpg works too.
    thumb_path = _THUMBS_DIR / (src.stem + ".jpg")
    if thumb_path.exists():
        return thumb_path

    try:
        with _PilImage.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMB_MAX_W, _THUMB_MAX_H), _PilImage.LANCZOS)
            img.save(thumb_path, "JPEG", quality=_THUMB_QUALITY, optimize=True)
        logger.debug("Generated thumbnail: %s → %s", src.name, thumb_path.name)
    except Exception as exc:
        logger.warning("Thumbnail generation failed for %s: %s", src.name, exc)
        return src  # fall back to original

    return thumb_path


@app.get("/api/images", tags=["assets"])
def list_images():
    """Return the list of available local images with pre-generated thumbnails."""
    if not _LOCAL_IMAGE_DIR.exists():
        return {"images": []}
    images = []
    for p in sorted(_LOCAL_IMAGE_DIR.iterdir()):
        if not (p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS):
            continue
        thumb = _make_thumbnail(p)
        thumb_url = f"/thumbnails/{quote(thumb.name)}"
        images.append({
            "filename":      p.name,
            "url":           f"/api/images/{quote(p.name)}",
            "thumbnail_url": thumb_url,
        })
    return {"images": images}


@app.get("/api/images/{filename:path}", tags=["assets"])
def serve_image(filename: str):
    """Serve a single full-resolution image from the local image library."""
    img_path = _LOCAL_IMAGE_DIR / filename
    if not img_path.exists() or not img_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(img_path))


# ---------------------------------------------------------------------------
# Rendered PNGs  (/renders/<run_id>/slide-n.png)
# ---------------------------------------------------------------------------

app.mount("/renders",    StaticFiles(directory=str(RENDERS_DIR)), name="renders")
app.mount("/thumbnails", StaticFiles(directory=str(_THUMBS_DIR)), name="thumbnails")


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
    print(f"Starting server on port {port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port)
