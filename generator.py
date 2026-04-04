"""
generator.py — LLM-powered slide generation for Instagram carousels.

Supports both Anthropic (default) and OpenAI backends, selected via the
LLM_PROVIDER env var ("anthropic" | "openai").

Public API
----------
generate_slides(topic) → (list[dict], str)
    Returns (slides, csv_text) where slides is a list of dicts with
    "heading" and "description" keys, and csv_text is empty (kept for
    backward compatibility with callers that ignore it).
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("carousel.generator")

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert social media strategist specializing in high-engagement Instagram carousel posts.
Generate EXACTLY 5 slides for the topic the user provides.

Return EXACTLY this JSON structure — no extra text, no markdown, no code fences:
[
  { "heading": "...", "description": "..." },
  { "heading": "...", "description": "..." },
  { "heading": "...", "description": "..." },
  { "heading": "...", "description": "..." },
  { "heading": "...", "description": "..." }
]

Rules:
- Slide 1: Hook — grab attention immediately
- Slides 2–4: Content — deliver value
- Slide 5: CTA — call to action
- heading: under 10 words
- description: under 25 words
- Professional, friendly, educational tone
- Do not add any extra text.\
"""


# ---------------------------------------------------------------------------
# Backend: Anthropic
# ---------------------------------------------------------------------------

def _generate_anthropic(topic: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        ) from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    logger.info("Calling Anthropic API for topic: %r", topic)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": topic}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Backend: OpenAI
# ---------------------------------------------------------------------------

def _generate_openai(topic: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package not installed. Run: pip install openai"
        ) from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key)
    logger.info("Calling OpenAI API for topic: %r", topic)

    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": topic},
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_slides(raw: str) -> list[dict]:
    """Parse LLM JSON output into a validated list of slide dicts."""
    text = raw.strip()

    # Strip markdown code fences if the model wrapped output anyway
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the content between first pair of fences
        text = parts[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        slides = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}\nRaw output:\n{raw[:500]}") from exc

    if not isinstance(slides, list):
        raise ValueError(f"Expected JSON array, got {type(slides).__name__}")

    if len(slides) != 5:
        raise ValueError(f"Expected exactly 5 slides, got {len(slides)}")

    result = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            raise ValueError(f"Slide {i} is not a JSON object")
        heading     = (s.get("heading")     or "").strip()
        description = (s.get("description") or "").strip()
        if not heading:
            raise ValueError(f"Slide {i} is missing a non-empty 'heading'")
        result.append({"heading": heading, "description": description})

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_slides(
    topic: str,
    max_retries: int = 3,
) -> tuple[list[dict], str]:
    """
    Generate carousel slides for *topic* using the configured LLM.

    Returns
    -------
    slides : list[dict]
        Each dict has "heading" and "description" keys. Always 5 items.
    csv_text : str
        Empty string (kept for API compatibility with callers that ignore it).
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    backends = {
        "anthropic": _generate_anthropic,
        "openai":    _generate_openai,
    }

    if provider not in backends:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. Choose 'anthropic' or 'openai'."
        )

    backend    = backends[provider]
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Slide generation attempt %d/%d", attempt, max_retries)
            raw    = backend(topic)
            logger.debug("Raw LLM output: %s", raw[:500])
            slides = _parse_json_slides(raw)
            logger.info("Parsed %d slides from JSON response", len(slides))
            return slides, ""
        except Exception as exc:
            logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, exc)
            last_error = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info("Waiting %ds before retry…", wait)
                time.sleep(wait)

    raise RuntimeError(
        f"Slide generation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )
