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
import tempfile
import uuid
from pathlib import Path
from typing import Generator, List, Optional
from urllib.parse import quote
import random

import anthropic as _anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from utils import setup_logging
from generator import (
    generate_slides,
    generate_caption,
    _build_system_prompt,
    _VISUAL_STYLES,
    _finalise_slides,
    _strip_markdown,
    italicise_one_word,
)
from renderer import render_slides

STYLE_MAP = {
    "dark": "dark_core",
    "light": "light_image",
}

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("carousel.api")
print("Starting FastAPI server...")

app = FastAPI(title="Carousel Generator API", version="2.0.0")

DIST        = Path(__file__).parent / "frontend" / "dist"
RENDERS_DIR = Path("/tmp/renders")
RENDERS_DIR.mkdir(parents=True, exist_ok=True)
_UPLOADS_DIR = Path("/tmp/uploads")
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

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
_NL_RE     = _re.compile(r'[\n\r]+')

def _strip_html_tags(text: str) -> str:
    return _re.sub(r"<[^>]+>", "", text)

def _strip_citations(text: str) -> str:
    return _CITE_RE.sub(r'\1', text)

def _strip_markdown(text: str) -> str:
    return _MD_RE.sub('', text)

def _strip_newlines(text: str) -> str:
    return _NL_RE.sub(' ', text).strip()

_SENTENCE_TERM_RE = _re.compile(r'[.!?]["\')>]?\s*$')
_SENTENCE_SPLIT_RE = _re.compile(r'(?<=[.!?])\s+')

def _ensure_complete_sentences(text: str) -> str:
    """Drop any trailing fragment that does not end with . ! or ?

    Final validation step applied after all other stripping.
    """
    if not text:
        return text
    text = text.strip()
    if _SENTENCE_TERM_RE.search(text):
        return text
    parts = _SENTENCE_SPLIT_RE.split(text)
    complete = [p.strip() for p in parts if _SENTENCE_TERM_RE.search(p.strip())]
    return " ".join(complete)


def _clean_topic(text: str) -> str:
    """Strip whitespace and zero-width Unicode characters from topic input."""
    return _ZW_CHARS.sub('', text).strip()


def _claude(prompt: str, max_tokens: int = 1024, system: Optional[str] = None) -> str:
    """Single-turn Claude call, returns response text."""
    client = _anthropic.Anthropic()
    model  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    msg = client.messages.create(**kwargs)
    return msg.content[0].text


def _parse_json(text: str):
    text = text.strip()

    # Remove markdown fences
    text = _re.sub(r"^```(?:json)?\s*", "", text)
    text = _re.sub(r"\s*```$", "", text.strip())

    # Remove leading "json" (Claude sometimes does this)
    if text.lower().startswith("json"):
        text = text[4:].strip()

    # Try full parse first (fast path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first JSON object OR array
    start_obj = text.find("{")
    start_arr = text.find("[")

    if start_arr != -1 and (start_arr < start_obj or start_obj == -1):
        start = start_arr
        end = text.rfind("]") + 1
    else:
        start = start_obj
        end = text.rfind("}") + 1

    if start == -1 or end == -1:
        raise ValueError(f"No JSON found in response:\n{text[:200]}")

    json_str = text[start:end]

    return json.loads(json_str)


def _arc_position(slide_number: int, total: int) -> str:
    """Map a 1-based slide number to its arc stage name."""
    if total <= 1:
        return _ARC[0]
    idx = round((slide_number - 1) / max(total - 1, 1) * (len(_ARC) - 1))
    return _ARC[min(idx, len(_ARC) - 1)]

def _validate_slide(slide: dict) -> Optional[str]:
    if slide.get("type") == "pattern_break":
        return None if (slide.get("heading") or "").strip() else "Empty"

    text = (slide.get("description") or slide.get("body") or "").strip()

    if not text:
        return "Empty"

    # Must end with proper punctuation
    if not _re.search(r'[.!?]["\')>]?\s*$', text):
        return "Incomplete sentence"

    # Too short = likely broken
    if len(text.split()) < 4:
        return "Too short"

    # Common truncation endings
    bad_endings = (
        "to", "and", "or", "with", "the", "a", "an",
        "of", "for", "in", "on", "at", "by",
        "this", "that", "these", "those",
        "actually", "because"
    )

    last_word = text.rstrip(".!?").split()[-1].lower()
    if last_word in bad_endings:
        return "Truncated ending"

    return None

def _regenerate_slide_internal(
    slides: list[dict],
    idx: int,
    topic: str,
    hook: str,
    template_style: str
) -> dict:
    total = len(slides)
    arc = _arc_position(idx + 1, total)

    other_lines = []
    for i, s in enumerate(slides):
        if i == idx:
            continue
        s_arc = _arc_position(i + 1, total)
        heading = _strip_html_tags(s.get("heading", ""))
        body = s.get("description", s.get("body", ""))
        entry = f"Slide {i + 1} ({s_arc}): {heading}"
        if body:
            entry += f" — {body}"
        other_lines.append(entry)

    prompt = _REGEN_PROMPT.format(
        slide_num=idx + 1,
        total=total,
        topic=topic,
        hook=hook,
        arc=arc,
        other_slides="\n".join(other_lines),
        issue_block=""
    )

    system = _build_system_prompt(total, template_style)

    raw = _claude(prompt, max_tokens=512, system=system)
    new_slide = _parse_json(raw)

    heading = italicise_one_word(_strip_citations(new_slide.get("heading", "")))
    description = _ensure_complete_sentences(
        _strip_newlines(
            _strip_citations(new_slide.get("body", ""))
        )
    )

    return {
        "type": new_slide.get("type", "content"),
        "heading": heading,
        "description": description
    }

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    idea:      str
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
    template:       str = "dark"


class RegenerateRequest(BaseModel):
    topic:          str
    slide_index:    int
    hook:           str
    slides:         list[dict]
    issue:          str = ""
    suggestion:     str = ""
    template_style: str = "headings_and_text"


class RenderRequest(BaseModel):
    topic:          str
    slides:         list[dict]
    style:          str = "dark_core"
    image_filename: Optional[str] = None


class LightStructureRequest(BaseModel):
    topic:      str
    num_slides: int = 5


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _derive_topic_from_idea(idea: str) -> str:
    prompt = f"""
Convert this idea into a STRICT short topic label.

RULES (must follow):
- Maximum 4 words
- No punctuation
- No full sentences
- No explanations
- Output MUST be <= 4 words

Idea:
{idea}

Return ONLY the label.
"""
    try:
        topic = _claude(prompt, max_tokens=10).strip()

        # HARD ENFORCEMENT (this is the key)
        words = topic.split()
        return " ".join(words[:4])

    except Exception:
        return " ".join(idea.split()[:4])

def _derive_cta_topic(idea: str) -> str:
    prompt = f"""
What is the core topic of this?

Write a short noun phrase that describes what this content is really about,
not what it literally says.

It must fit naturally in:
"We show you ___ every day."

Rules:
- Max 4 words
- Must be a category or theme
- Prefer abstraction over detail
- Do NOT summarise the sentence
- Do NOT include verbs like "announced", "released"

Idea:
{idea}

Return ONLY the phrase.
"""
    try:
        out = _claude(prompt, max_tokens=10).strip()

        # 🔒 HARD GUARDRAILS (this is what makes it “regardless of input”)
        words = out.split()[:4]
        cleaned = " ".join(words)

        # fallback if model still does something weird
        if len(words) == 0 or len(cleaned) > 40:
            raise ValueError("Bad CTA topic")

        return cleaned

    except Exception:
        # deterministic fallback (always grammatical)
        words = idea.split()[:3]
        return " ".join(words) + " updates"

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream(topic: str, num_slides: int) -> Generator[str, None, None]:
    """Sync generator that emits SSE events at each real pipeline stage."""
    print("HTML PIPELINE ACTIVE")

    style = "dark_core"
    logger.info("Template style: %s", style)

    # Step 1: Generate slides via Claude
    logger.info("Generating slides for topic: %r (style=%s)", topic, style)
    yield _sse({"step": "generating", "message": "Generating and refining carousel content..."})

    try:
        slides, caption = generate_slides(topic, num_slides=num_slides, template_style=style)
        if not isinstance(slides, list):
            logger.error("Slides is not a list: %r", slides)
            yield _sse({"step": "error", "message": "Invalid slide data returned"})
            return
        for i in range(len(slides)):
            if i == 0:
                continue
            if slides[i].get("type") == "pattern_break":
                continue

            issue = _validate_slide(slides[i])
            if issue:
                slides[i] = _regenerate_slide_internal(slides,i,topic,hook,style)
    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    logger.info("Generated %d slides", len(slides))

    # Step 2: Fetch image for dark_core hook slide
    image_data = None
    yield _sse({"step": "fetching_image", "message": "Fetching image..."})
    try:
        if os.getenv("LUMMI_API_KEY"):
            from image_fetcher import fetch_lummi_image
            image_data = fetch_lummi_image(topic)
        else:
            from local_image import get_image_for_heading_template
            image_data = get_image_for_heading_template(topic)
        if image_data and image_data.get("author_name"):
            author_url = image_data.get("author_url", "")
            credit = image_data["author_name"]
            if author_url:
                credit += f" ({author_url})"
            caption += f"\n\nImage credit: {credit}"
    except Exception as exc:
        logger.warning("Image fetch failed (%s) — proceeding without image", exc)
        image_data = None

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
        for s in (slides or [])
    ]

    image_urls = [f"/renders/{run_id}/slide-{i + 1}.png" for i in range(len(png_paths))]
    logger.info("Carousel ready: %d images for run %s (style=%s)", len(image_urls), run_id, style)

    yield _sse({"step": "complete", "images": image_urls, "slides": slide_models, "caption": caption})


# ---------------------------------------------------------------------------
# API routes  -- registered before static file mounts
# ---------------------------------------------------------------------------

_DARK_HOOK_PROMPT = """\
Generate 4 hooks for a carousel about: {topic}

Each hook must create curiosity through controlled vagueness — specific about \
the domain, vague about the insight. The reader should recognise the context \
immediately but not yet understand the full idea.

Use these 4 styles, one each:

1. Curiosity — implies the reader is missing a specific step or element
   Pattern: "You're missing the step that makes [domain keyword] work"
             "You're skipping the part that actually matters in [domain keyword]"

2. Mistake — names a wrong approach, anchored to the topic domain
   Pattern: "You're using [domain keyword] the wrong way"
             "You're starting [domain keyword] in the wrong place"

3. Contrarian — challenges how most people approach this topic
   Pattern: "Most people use [domain keyword] backwards"
             "The way you're thinking about [domain keyword] is the problem"

4. Value — signals a better way exists, without explaining it
   Pattern: "There's a step in [domain keyword] most people skip"
             "You're one [domain keyword] change away from better results"

VAGUENESS RULES (follow strictly):
- Be vague about the insight — do NOT name or explain the actual tip
- Be specific about the domain — use a keyword from the topic so context is clear
- The reader should think "this might be about me" but not yet know what's coming
- Do not over-explain — the hook leads into slide 2 where clarity is introduced

Structural rules:
- 6–12 words, one complete sentence
- Address the reader as "you" where natural
- No ellipsis, no fragments, no questions
- No dramatic filler ("Stop scrolling", "You won't believe", "This feels illegal")
- No hype words (game-changer, unlock, unleash, discover)
- No fully generic hooks ("You're doing this wrong", "This changes everything") — \
  these lack context and reduce relevance

Return as a JSON array of 4 objects:
[
  {{"type": "curiosity",   "hook": "..."}},
  {{"type": "mistake",     "hook": "..."}},
  {{"type": "contrarian",  "hook": "..."}},
  {{"type": "value",       "hook": "..."}}
]"""

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
Write complete sentences only. Every sentence MUST end with a full stop, exclamation mark, \
or question mark. No fragments. No sentences that trail off.

Write a new version of slide {slide_num} only. One idea. Exactly two complete sentences in \
the body. Each sentence under 12 words. Total body text under 20 words. If a sentence exceeds \
12 words, split it or cut it — no exceptions. \
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

    prompt = _DARK_HOOK_PROMPT.format(topic=topic)
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


_LIGHT_HOOK_PROMPT = """\
Write 3 carousel hook options for the topic: {topic}

Use these 3 formats, one each:

1. Specific promise — a concrete outcome or benefit
   e.g. "How to do X without Y"

2. Pattern interrupt — breaks an assumption or starts mid-thought
   e.g. "Tell me if I'm wrong...", "Stop scrolling if..."

3. Contrast — exposes a gap between belief and reality
   e.g. "You've been lied to about...", "The truth about..."

Rules for every hook:
- Maximum 8 words
- No full sentences — fragments and ellipses are fine
- No generic AI phrasing (unleash, discover, unlock, game-changer)
- Must create curiosity or a gap the reader wants to close

Return as a JSON array of 3 objects:
[
  {{"type": "specific_promise", "hook": "..."}},
  {{"type": "pattern_interrupt", "hook": "..."}},
  {{"type": "contrast", "hook": "..."}}
]"""


@app.post("/light-hooks", tags=["carousel"])
def light_hooks_route(req: LightStructureRequest):
    """Generate 3 hook options for the light template."""
    topic = _clean_topic(req.topic)
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")

    prompt = _LIGHT_HOOK_PROMPT.format(topic=topic)
    try:
        raw  = _claude(prompt, max_tokens=400)
        data = _parse_json(raw)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")
        hooks = [
            {"hook": str(h.get("hook", "")), "type": str(h.get("type", ""))}
            for h in data[:3]
        ]
    except Exception as exc:
        logger.error("Light hook generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Hook generation failed: {exc}")

    return {"hooks": hooks}


@app.post("/upload-cover-image", tags=["assets"])
async def upload_cover_image_route(file: UploadFile = File(...)):
    """Save an uploaded cover image and return a reference filename."""
    data = await file.read()
    ctype = file.content_type or "image/jpeg"
    ext = "." + (ctype.split("/")[-1] if "/" in ctype else "jpg")
    if ext == ".jpeg":
        ext = ".jpg"
    fname = uuid.uuid4().hex + ext
    (_UPLOADS_DIR / fname).write_bytes(data)
    return {"filename": f"__uploads__/{fname}", "url": f"/uploads/{fname}"}


@app.get("/uploads/{filename:path}", tags=["assets"])
def serve_uploaded_image(filename: str):
    """Serve a previously uploaded cover image."""
    img_path = _UPLOADS_DIR / filename
    if not img_path.exists() or not img_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded image not found")
    return FileResponse(str(img_path))


def _slides_stream(topic: str, hook: str, num_slides: int, image_filename: Optional[str] = None, template: str = "dark") -> Generator[str, None, None]:
    """SSE stream: generate slides using the selected hook."""
    # Validate selected image exists before spending time on generation
    if image_filename:
        if image_filename.startswith("__uploads__/"):
            _img_path = _UPLOADS_DIR / image_filename[len("__uploads__/"):]
        else:
            _img_path = _LOCAL_IMAGE_DIR / image_filename
        if not _img_path.exists():
            logger.error("Selected image not found on disk: %s", _img_path)
            yield _sse({"step": "error", "message": f"Image not found: {image_filename}. Please go back and select a different image."})
            return

    style = STYLE_MAP.get(template, "dark_core")
    yield _sse({"step": "generating", "message": "Generating slides..."})

    try:
        slides, caption = generate_slides(
            topic, num_slides=num_slides, template_style=style, hook=hook
        )

        # --- NEW: validate + fix ---
        for i in range(len(slides)):
            # never touch hook slide
            if i == 0:
                continue
            if slides[i].get("type") == "pattern_break":
                continue

            issue = _validate_slide(slides[i])
            if issue:
                logger.info(f"Regenerating slide {i+1}: {issue}")

                try:
                    slides[i] = _regenerate_slide_internal(slides,i,topic,hook,style)

                    # one re-check only (no loops)
                    if _validate_slide(slides[i]):
                        logger.warning(f"Slide {i+1} still invalid after regen")

                except Exception as exc:
                    logger.error(f"Regeneration failed for slide {i+1}: {exc}")

    except Exception as exc:
        logger.error("Slide generation failed: %s", exc)
        yield _sse({"step": "error", "message": f"Content generation failed: {exc}"})
        return

    image_data = None
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
        logger.warning("Image fetch failed (%s) — proceeding without image", exc)
        image_data = None

    slide_models = [
        {
            "type": s.get("type", "content"),
            "heading": s.get("heading", ""),
            "description": s.get("body", ""),
            "validation": _validate_slide({
                "description": s.get("body", "")
            }) or ""
        }
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
    print("TEMPLATE RECEIVED:", req.template)
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
        _slides_stream(topic, hook, req.num_slides, req.image_filename, req.template),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        heading  = _strip_html_tags(s.get("heading", ""))
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
    system = _build_system_prompt(total, req.template_style)
    try:
        raw       = _claude(prompt, max_tokens=512, system=system)
        new_slide = _parse_json(raw)
        if not isinstance(new_slide, dict):
            raise ValueError("Expected a JSON object")
        new_slide.setdefault("type", slide_type)
    except Exception as exc:
        logger.error("Regenerate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

    heading = _strip_citations(new_slide.get("heading", ""))
    heading = italicise_one_word(heading)
    description = _ensure_complete_sentences(_strip_newlines(_strip_citations(new_slide.get("body", new_slide.get("description", "")))))

    # Enforce "We show you … every day." on the final slide after regeneration
    is_last = (idx == total - 1)
    if is_last:
        h_lower = heading.strip().lower()
        if not h_lower.startswith("we show you"):
            heading = f"We show you {topic} every day."
            logger.info("Corrected final slide heading after regeneration")
        elif not h_lower.rstrip(".").rstrip().endswith("every day"):
            heading = heading.rstrip(".").rstrip() + " every day."
            logger.info("Corrected final slide heading after regeneration")

    return {"slide": {"type": new_slide.get("type", slide_type),
                      "heading": heading,
                      "description": description}}


def _render_stream(
    topic: str, slides: list[dict], style: str, image_filename: Optional[str] = None
) -> Generator[str, None, None]:
    """SSE stream: render approved slides to PNG."""
    yield _sse({"step": "rendering", "message": "Rendering slides..."})

    internal_slides = [
        {"type": s.get("type", "content"),
         "heading": _strip_citations(s.get("heading", "")),
         "body":    _ensure_complete_sentences(_strip_newlines(_strip_citations(s.get("description", ""))))}
        for s in slides
    ]

    image_data = None
    if style == "dark_core":
        try:
            if os.getenv("LUMMI_API_KEY"):
                from image_fetcher import fetch_lummi_image
                image_data = fetch_lummi_image(topic)
            else:
                from local_image import get_image_for_heading_template
                image_data = get_image_for_heading_template(topic, image_filename)
        except Exception as exc:
            logger.warning("Image fetch failed for render (%s) — proceeding without image", exc)
            image_data = None

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


def _generate_light_stream_full(
    idea: str,
    hook: str,
    slides_content: list[dict],
    content_temp_paths: list[str],
    image_filename: Optional[str] = None,
):
    """SSE stream: build light carousel from manual slide content, render, clean up."""
    yield _sse({"step": "building", "message": "Building slides…"})
    try:
        # Build slides from manual content — no LLM vision analysis
        content_slides = [
            {
                "type":    "content",
                "heading": _strip_markdown(s.get("heading", "").strip()),
                "body":    s.get("text", "").strip(),
                "tag":     "INSIGHT",
            }
            for s in slides_content
        ]
        slides = [
            {"type": "hook",    "heading": _strip_markdown(hook), "body": "", "tag": ""},
            *content_slides,
            {"type": "cta",     "heading": "", "body": "", "tag": ""},
        ]
        slides = _finalise_slides(slides, idea)

        caption = generate_caption(slides)

        first_image_data = None
        if image_filename:
            from local_image import get_image_for_heading_template
            short_topic = _derive_topic_from_idea(idea)
            first_image_data = get_image_for_heading_template(short_topic, image_filename)

    except Exception as exc:
        logger.error("Light slide build failed: %s", exc)
        yield _sse({"step": "error", "message": f"Slide build failed: {exc}"})
        for p in content_temp_paths:
            try: os.unlink(p)
            except OSError: pass
        return

    yield _sse({"step": "rendering", "message": "Rendering slides…"})
    try:
        png_paths, run_id = render_slides(
            slides,
            renders_base=str(RENDERS_DIR),
            template_style="light_image",
            content_image_paths=content_temp_paths,
            first_image_data=first_image_data,
        )
    except Exception as exc:
        logger.error("Light rendering failed: %s", exc)
        yield _sse({"step": "error", "message": f"Rendering failed: {exc}"})
        return
    finally:
        for p in content_temp_paths:
            try: os.unlink(p)
            except OSError: pass

    image_urls = [f"/renders/{run_id}/slide-{i + 1}.png" for i in range(len(png_paths))]
    logger.info("Light render complete: %d images (run=%s)", len(image_urls), run_id)
    yield _sse({"step": "complete", "images": image_urls, "caption": caption})


@app.post("/generate-light", tags=["carousel"])
async def generate_light_route(
    topic: str = Form(...),
    hook: str = Form(...),
    slides_content: Optional[str] = Form(None),   # optional — empty means no manual content
    images: List[UploadFile] = File(...),
    image_filename: Optional[str] = Form(None),
):
    """Generate and render a light image carousel from manual slide content (SSE stream).

    Accepts: topic, hook, slides_content (JSON array of {heading, text}), images (1–8 files).
    Streams SSE events: building → rendering → complete | error.
    """
    topic = _clean_topic(topic)
    hook  = hook.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be empty")
    if not hook:
        raise HTTPException(status_code=422, detail="hook must not be empty")
    if not images:
        raise HTTPException(status_code=422, detail="at least one content image required")
    if len(images) > 8:
        raise HTTPException(status_code=422, detail="maximum 8 images allowed")

    parsed_slides: list[dict] = []
    if slides_content:
        try:
            parsed_slides = json.loads(slides_content)
            if not isinstance(parsed_slides, list):
                raise ValueError("slides_content must be a JSON array")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid slides_content: {exc}")

    # Validate count only when manual content was provided
    if parsed_slides and len(images) != len(parsed_slides):
        raise HTTPException(
            status_code=422,
            detail=f"Number of images ({len(images)}) must match slide count ({len(parsed_slides)})"
        )

    content_temp_paths: list[str] = []
    for upload in images:
        data  = await upload.read()
        ctype = upload.content_type or "image/jpeg"
        ext   = "." + (ctype.split("/")[-1] if "/" in ctype else "jpg")
        if ext == ".jpeg":
            ext = ".jpg"
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.write(data)
        tmp.close()
        content_temp_paths.append(tmp.name)

    return StreamingResponse(
        _generate_light_stream_full(
            topic,
            hook,
            parsed_slides,
            content_temp_paths,
            image_filename,
        ),
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
        # no-cache so browsers always fetch the latest hashed JS/CSS references
        return FileResponse(
            str(index),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
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
