"""
generator.py — LLM-powered slide generation for Instagram carousels.

Supports both Anthropic (default) and OpenAI backends, selected via the
LLM_PROVIDER env var ("anthropic" | "openai").

Public API
----------
generate_slides(topic) → (list[dict], str)
    Returns (slides, "") where slides is a list of dicts with
    "type", "heading", and "description" keys (4–7 slides).
    "type" is one of: "hook", "content", "cta".
    csv_text is always "" (kept for API compatibility with callers that ignore it).
"""

import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger("carousel.generator")

# ---------------------------------------------------------------------------
# Word limits by slide type
# ---------------------------------------------------------------------------

WORD_LIMITS = {
    "hook":    8,
    "content": 15,
    "cta":     12,
}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You create high-performing Instagram carousel content.

RULES:

- Generate 4–7 slides
- Slide structure:
    Slide 1:      type "hook"    — stops the scroll; must be a COMPLETE thought
    Slides 2–n-1: type "content" — one insight or tip per slide
    Slide n:      type "cta"    — one clear call to action

- MUST include at least one actionable tip using the pattern:
    Instead of X → Try Y
  (e.g. "Instead of long prompts → Try one clear instruction")

- STRICT word limits (hard limits — never exceed):
    hook:    max 8 words
    content: max 15 words
    cta:     max 12 words

HOOK RULES (critical):
- The hook MUST be a complete sentence or complete contrast — never a trailing phrase
- It must create curiosity OR reveal a contrast
- BAD (incomplete): "Claude is powerful — if you know"
- BAD (no payoff):  "Most people get this wrong"  ← too vague
- GOOD (contrast):  "Claude is **powerful** — most people use it **wrong**"
- GOOD (curiosity): "You're using Claude **backwards**"
- GOOD (contrast):  "More prompts ≠ **better** results"

EMPHASIS RULES (selective bold using **word**):
- Bold 1–2 words per slide only — never more
- ONLY bold words that carry real meaning:
    ✓ Key outcomes:   **faster**, **smarter**, **dramatically**
    ✓ Key contrasts:  **wrong**, **mistake**, **backwards**
    ✓ Actionable words: **structured**, **specific**, **test**
- NEVER bold:
    ✗ Filler words: real, things, beginners, people, way
    ✗ Generic nouns: potential, examples, results, content
    ✗ Articles/conjunctions: the, a, and, or, if

- Style:
    - Short, punchy phrases only
    - No fluff, no filler (just, really, very, simply, basically)
    - No generic claims — be specific
    - Beginner-friendly language

Return ONLY a JSON array — no markdown, no code fences, no extra text:
[
  { "type": "hook",    "text": "You're using Claude **backwards**" },
  { "type": "content", "text": "Instead of long prompts → give **one** clear instruction" },
  { "type": "content", "text": "**Specific** context = dramatically better answers" },
  { "type": "cta",     "text": "**Save** this and fix your prompts today" }
]

If these rules cannot be followed for the given topic, still produce the
best possible output — the system will validate and retry automatically.\
"""


# ---------------------------------------------------------------------------
# Word-limit enforcement (applied in code — never rely on LLM to obey)
# ---------------------------------------------------------------------------

def enforce_word_limit(text: str, max_words: int) -> str:
    """Hard-truncate *text* to *max_words* words.

    Treats **word** markers as a single word token.
    After truncation, any dangling opening ** without a closing ** is stripped
    so the HTML renderer never sees an unclosed marker.
    """
    words = text.split()
    truncated = " ".join(words[:max_words])
    # Remove any unclosed **marker (odd number of ** occurrences)
    if truncated.count("**") % 2 != 0:
        truncated = truncated.rsplit("**", 1)[0].rstrip()
    return truncated


def _enforce_slide_limits(slides: list[dict]) -> list[dict]:
    """Ensure every slide's heading respects the word limit for its type."""
    result = []
    for slide in slides:
        slide_type = slide["type"]
        max_words  = WORD_LIMITS.get(slide_type, 15)
        heading    = slide["heading"]
        enforced   = enforce_word_limit(heading, max_words)
        if enforced != heading:
            logger.info(
                "Truncated %s slide from %d words to %d: %r → %r",
                slide_type, len(heading.split()), max_words, heading, enforced,
            )
        result.append({**slide, "heading": enforced})
    return result


# ---------------------------------------------------------------------------
# Bold phrase cap (max 3 per slide)
# ---------------------------------------------------------------------------

def _cap_bold_phrases(text: str, max_bold: int = 2) -> str:
    """Strip **..** markers beyond the first *max_bold* occurrences."""
    count = 0
    def _replacer(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"**{m.group(1)}**" if count <= max_bold else m.group(1)
    return re.sub(r'\*\*(.*?)\*\*', _replacer, text)


def _enforce_bold_caps(slides: list[dict]) -> list[dict]:
    result = []
    for slide in slides:
        capped = _cap_bold_phrases(slide["heading"])
        if capped != slide["heading"]:
            logger.info("Capped bold phrases on %s slide: %r", slide["type"], slide["heading"])
        result.append({**slide, "heading": capped})
    return result


# ---------------------------------------------------------------------------
# Hook completeness validation
# ---------------------------------------------------------------------------

# Words that signal a dangling / unfinished thought when they land last
_DANGLING_ENDINGS = {
    "if", "but", "and", "or", "so", "yet", "when", "unless", "because",
    "although", "though", "while", "as", "since", "until", "than",
    "the", "a", "an", "to", "for", "of", "in", "on", "at", "by",
    "with", "about", "into", "know", "use", "do", "get", "have", "be",
}


def _is_complete_hook(text: str) -> bool:
    """Return True if the hook text reads as a complete thought.

    Rejects hooks that:
    - End with a dangling conjunction, preposition, or verb
    - End with an em-dash (—) suggesting a continuation that was cut off
    - Contain an em-dash but fewer than 2 words after it (payoff too thin)
    """
    # Strip **markers** for plain-text analysis
    plain = re.sub(r'\*\*(.*?)\*\*', r'\1', text).strip()

    # Ends with em-dash → clearly unfinished
    if plain.endswith("—") or plain.endswith("-"):
        return False

    # Last word is a dangling word
    last_word = re.split(r'[\s—]+', plain)[-1].lower().rstrip(".,!?")
    if last_word in _DANGLING_ENDINGS:
        return False

    # Em-dash present but payoff (words after it) is < 2 words → incomplete contrast
    if "—" in plain:
        after_dash = plain.split("—")[-1].strip()
        if len(after_dash.split()) < 2:
            return False

    return True


# ---------------------------------------------------------------------------
# Actionable tip validation
# ---------------------------------------------------------------------------

# Markers that indicate a concrete "Instead of X → Try Y" tip
_TIP_MARKERS = ("instead of", "→", "->", "try this", "stop ", "swap ")


def _has_actionable_tip(slides: list[dict]) -> bool:
    """Return True if at least one content slide contains a concrete tip."""
    for slide in slides:
        if slide["type"] != "content":
            continue
        text_lower = slide["heading"].lower()
        if any(marker in text_lower for marker in _TIP_MARKERS):
            return True
    return False


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

    print("=== ANTHROPIC DEBUG ===")
    print("KEY EXISTS:", api_key is not None)
    print("KEY LENGTH:", len(api_key) if api_key else None)
    print("KEY PREFIX:", api_key[:10] if api_key else None)
    print("=======================")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    logger.info("Calling Anthropic API for topic: %r", topic)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
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
            {"role": "user", "content": f"Topic: {topic}"},
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
        text = parts[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        slides = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}\nRaw output:\n{raw[:500]}") from exc

    if not isinstance(slides, list):
        raise ValueError(f"Expected JSON array, got {type(slides).__name__}")

    if not (4 <= len(slides) <= 7):
        raise ValueError(f"Expected 4–7 slides, got {len(slides)}")

    valid_types = {"hook", "content", "cta"}
    result = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            raise ValueError(f"Slide {i} is not a JSON object")

        slide_type = (s.get("type") or "").strip().lower()
        text_val   = (s.get("text") or "").strip()

        if slide_type not in valid_types:
            raise ValueError(
                f"Slide {i} has invalid type {slide_type!r}. "
                f"Must be one of: {sorted(valid_types)}"
            )
        if not text_val:
            raise ValueError(f"Slide {i} has empty 'text'")

        # Normalise to internal renderer format:
        #   heading = the full slide text; description = "" (unused with new concise format)
        result.append({
            "type":        slide_type,
            "heading":     text_val,
            "description": "",
        })

    # Validate structure: first=hook, last=cta
    if result[0]["type"] != "hook":
        raise ValueError(f"First slide must be 'hook', got {result[0]['type']!r}")
    if result[-1]["type"] != "cta":
        raise ValueError(f"Last slide must be 'cta', got {result[-1]['type']!r}")

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
        Each dict has "type", "heading", and "description" keys.
        "type" is "hook", "content", or "cta". 4–7 slides total.
        Word limits are enforced in code regardless of LLM output.
    csv_text : str
        Always "". Kept for API compatibility with callers that ignore it.
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
            slides = _enforce_slide_limits(slides)
            slides = _enforce_bold_caps(slides)
            hook_text = slides[0]["heading"]
            if not _is_complete_hook(hook_text):
                raise ValueError(
                    f"Hook is not a complete thought: {hook_text!r}. "
                    "Retrying for a hook with a full payoff."
                )
            if not _has_actionable_tip(slides):
                raise ValueError(
                    "No actionable tip found (expected 'Instead of X → Try Y' pattern). "
                    "Retrying to enforce content quality."
                )
            logger.info("Generated %d slides (word limits enforced, tip present)", len(slides))
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
