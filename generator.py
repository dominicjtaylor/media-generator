"""
generator.py — LLM-powered CSV generation for Instagram carousels.

Supports both Anthropic (default) and OpenAI backends, selected via the
LLM_PROVIDER env var ("anthropic" | "openai").
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

from utils import sanitise_csv_text, save_csv, validate_csv, csv_path

logger = logging.getLogger("carousel.generator")

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an expert social media strategist with a specialization in "
    "high-engagement Instagram carousel content. You are also a CSV generator "
    "for Contentdrips.\n"
    "I want to create an Instagram carousel, exported as a real downloadable "
    "CSV file named csv_upload_feature.csv.\n\n"
    "HOW TO USE:\n"
    "After reading this prompt, the user will provide a single topic\n\n"
    "RULES:\n"
    "• If slide count not specified → create 5 slides total\n"
    "• Do not ask follow-up questions\n"
    "• Do not add commentary\n"
    "• Output ONLY CSV\n\n"
    'CSV COLUMNS:\n"Topic","Slide","Heading","Description"\n\n'
    "SLIDE STRUCTURE:\n"
    "Slide 1: Hook\n"
    "Slides 2–4: Content\n"
    "Slide 5: CTA\n\n"
    "CONTENT RULES:\n"
    "• Heading < 10 words\n"
    "• Description < 25 words\n"
    "• 20–30 words per slide\n"
    "• Professional, friendly, educational tone\n"
    "• Must be comma-safe CSV\n"
    "• Optimized for engagement"
)


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
# Public API
# ---------------------------------------------------------------------------

def generate_csv(
    topic: str,
    max_retries: int = 3,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Generate an Instagram carousel CSV for *topic* using the configured LLM.

    Parameters
    ----------
    topic:
        The carousel topic string.
    max_retries:
        Number of LLM call attempts before raising.
    output_path:
        Where to save the CSV. Defaults to csv_upload_feature.csv.

    Returns
    -------
    Path
        Path to the saved, validated CSV file.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    _backends = {
        "anthropic": _generate_anthropic,
        "openai": _generate_openai,
    }

    if provider not in _backends:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. Choose 'anthropic' or 'openai'."
        )

    backend = _backends[provider]
    dest = output_path or csv_path()

    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("CSV generation attempt %d/%d", attempt, max_retries)
            raw = backend(topic)
            cleaned = sanitise_csv_text(raw)
            save_csv(cleaned, dest)

            if validate_csv(dest):
                logger.info("CSV generated and validated successfully")
                return dest

            # Validation failed — log and retry
            logger.warning(
                "Generated CSV failed validation (attempt %d). Retrying…",
                attempt,
            )
            last_error = ValueError("CSV validation failed after generation")

        except Exception as exc:
            logger.warning("LLM call failed (attempt %d): %s", attempt, exc)
            last_error = exc

        if attempt < max_retries:
            wait = 2 ** attempt  # 2s, 4s, 8s …
            logger.info("Waiting %ds before retry…", wait)
            time.sleep(wait)

    raise RuntimeError(
        f"CSV generation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )
