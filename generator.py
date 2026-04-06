"""
generator.py — LLM-powered slide generation for Instagram carousels.

Supports both Anthropic (default) and OpenAI backends, selected via the
LLM_PROVIDER env var ("anthropic" | "openai").

Public API
----------
generate_slides(topic) → (list[dict], str)
    Returns (slides, caption) where slides is a list of dicts with
    "type", "heading", and "description" keys (4–7 slides), and caption
    is a ready-to-post Instagram caption string.
    "type" is one of: "hook", "content", "cta".
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
    "content": 18,   # raised from 15 to allow examples and "because" explanations
    "cta":     12,
}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(num_slides: int) -> str:
    """Return the generation system prompt with the exact slide count baked in."""
    content_count = num_slides - 2
    return f"""\
You are an API that generates Instagram carousel slides about Claude AI for beginners.

Return ONLY valid JSON. No text outside JSON. No markdown. No code blocks. No explanation.

---

SLIDE COUNT (CRITICAL):

Generate EXACTLY {num_slides} slides. There are NO other slide count rules.
Ignore any prior assumptions about slide counts.

Structure:
- Slide 1 = hook
- Slides 2 to {num_slides - 1} = content  ({content_count} content slide{"s" if content_count != 1 else ""})
- Slide {num_slides} = cta

---

REAL PROMPT EXAMPLE (MANDATORY):

At least ONE content slide must include a real, usable Claude prompt a beginner can copy.

Accepted formats:
  Instead of: "Explain this" → Try: "Explain this simply with 3 examples"
  Bad: "Summarise this" / Better: "Summarise this in bullet points with key takeaways"
  Or a direct quoted prompt: "Act as a teacher and explain X step by step"

---

QUALITY RULES:

- Hook: complete sentence, contrast or curiosity, max 8 words
- Content: one idea with example, explanation, or comparison — 12–18 words
- CTA: action-oriented, max 12 words
- Use **word** to bold 1–2 key words per slide (outcomes, contrasts, actions only)
- No filler, no empty claims — show HOW or WHY

---

OUTPUT FORMAT:

{{
  "slides": [
    {{"type": "hook",    "text": "..."}},
    {{"type": "content", "text": "..."}},
    {{"type": "cta",     "text": "..."}}
  ]
}}

The "slides" array MUST contain EXACTLY {num_slides} objects.
If the output is not valid JSON, regenerate internally until it is.\
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
# Real prompt example validation
# ---------------------------------------------------------------------------

# Verbs that appear in actual Claude prompts a beginner would type
_PROMPT_VERBS = (
    "explain", "summarise", "summarize", "list", "write", "describe",
    "compare", "give me", "create", "show", "tell", "help", "act as",
    "step-by-step", "in bullet", "with examples", "with takeaways",
)


def _has_real_prompt_example(slides: list[dict]) -> bool:
    """Return True if at least one content slide contains a real, quoted prompt.

    Looks for text in single or double quotes that contains a recognisable
    prompt verb — the hallmark of a concrete, copy-pasteable Claude prompt.
    """
    for slide in slides:
        if slide["type"] != "content":
            continue
        text = slide["heading"]
        # Find all quoted strings (single or double quotes)
        quoted = re.findall(r'["\u201c\u201d]([^"]+)["\u201c\u201d]|\'([^\']+)\'', text)
        for groups in quoted:
            q = (groups[0] or groups[1]).lower()
            if any(verb in q for verb in _PROMPT_VERBS):
                return True
    return False


# ---------------------------------------------------------------------------
# Depth validation — at least one example and one insight per carousel
# ---------------------------------------------------------------------------

# Markers for a concrete "Instead of → Try" or micro-example slide
_EXAMPLE_MARKERS = ("instead of", "→", "->", "try ", "e.g.", "for example", "act as")

# Markers for an insight/explanation slide
_INSIGHT_MARKERS = ("because", "—", " — ", "=", "≠", "means ", "so ", "which ")


def _has_depth(slides: list[dict]) -> bool:
    """Return True if the carousel contains at least one example slide AND
    one insight/explanation slide among the content slides."""
    has_example = False
    has_insight = False
    for slide in slides:
        if slide["type"] != "content":
            continue
        text_lower = slide["heading"].lower()
        if any(m in text_lower for m in _EXAMPLE_MARKERS):
            has_example = True
        if any(m in text_lower for m in _INSIGHT_MARKERS):
            has_insight = True
    return has_example and has_insight


# ---------------------------------------------------------------------------
# Backend: Anthropic
# ---------------------------------------------------------------------------

def _generate_anthropic(topic: str, num_slides: int) -> str:
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
    logger.info("Calling Anthropic API for topic: %r (num_slides=%d)", topic, num_slides)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_build_system_prompt(num_slides),
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Backend: OpenAI
# ---------------------------------------------------------------------------

def _generate_openai(topic: str, num_slides: int) -> str:
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
    logger.info("Calling OpenAI API for topic: %r (num_slides=%d)", topic, num_slides)

    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": _build_system_prompt(num_slides)},
            {"role": "user", "content": f"Topic: {topic}"},
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_slides(raw: str, num_slides: int = 5) -> list[dict]:
    """Parse LLM JSON output into a validated list of slide dicts."""
    text = raw.strip()

    # Strip markdown code fences if the model wrapped output anyway
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}\nRaw output:\n{raw[:500]}") from exc

    # Accept both wrapper object {"slides": [...]} and bare array [...]
    if isinstance(parsed, dict):
        if "slides" not in parsed:
            raise ValueError(f"JSON object has no 'slides' key. Keys found: {list(parsed.keys())}")
        slides = parsed["slides"]
    elif isinstance(parsed, list):
        slides = parsed
    else:
        raise ValueError(f"Expected JSON object or array, got {type(parsed).__name__}")

    # Allow ±1 from requested count (LLM sometimes off by one), but enforce 4–7 hard limits
    if not (4 <= len(slides) <= 7):
        raise ValueError(f"Expected 4–7 slides, got {len(slides)}")
    if len(slides) != num_slides:
        raise ValueError(
            f"Requested {num_slides} slides but got {len(slides)}. Retrying for exact count."
        )

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
# Review & improve pass
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """\
You are an expert Instagram content strategist. You will receive a set of carousel
slides and return an IMPROVED version that is sharper, clearer, and more valuable.

APPLY THESE FIXES:

1. HOOK — must be a complete sentence with contrast, curiosity, or tension
   BAD:  "Claude AI is powerful — most beginners waste"   ← cut off
   GOOD: "Claude AI is powerful — most beginners use it **wrong**"

2. REMOVE generic filler: "Welcome to...", "we post...", "our followers..."
   Replace with direct insights or statements about the user.

3. DEPTH — every content slide must do ONE of these (no empty claims):
   A) Comparison: "Instead of X → Try **Y**"
      e.g. "Instead of 'explain this' → Try 'explain this **simply** with examples'"
   B) Insight+reason: "[claim] — because [short reason]"
      e.g. "**Specific** prompts work better — because Claude knows exactly what to do"
   C) Micro-example: short concrete before/after or concrete output
      e.g. "Add a role: 'Act as a teacher' — answers become **clearer** instantly"
   Carousel must include at least one A/C (example) AND one B (insight).
   NEVER write vague claims like "this improves results" without showing HOW.

4. STRUCTURE: Hook → Problem → Insight → Tip → Outcome → CTA

5. WORD LIMITS (hard):
   hook:    max 8 words
   content: max 18 words
   cta:     max 12 words

6. EMPHASIS — use **word** markdown bold for 1–2 words per slide only:
   ✓ Bold: outcomes (better, faster), contrasts (wrong, mistake), actions (specific, structured)
   ✗ Never bold: filler (real, things), generic nouns (examples, potential)

7. STYLE: short punchy phrases, no fluff, beginner-friendly, high clarity

Return ONLY a JSON array in the same format as the input — no extra text:
[
  { "type": "hook",    "text": "..." },
  { "type": "content", "text": "..." },
  { "type": "cta",     "text": "..." }
]\
"""


def _slides_to_review_input(slides: list[dict]) -> str:
    """Format slides into a numbered list for the review LLM."""
    lines = ["Current carousel slides:"]
    for i, s in enumerate(slides):
        lines.append(f"  {i + 1}. [{s['type'].upper()}] {s['heading']}")
    return "\n".join(lines)


def _review_anthropic(slides: list[dict]) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=REVIEW_PROMPT,
        messages=[{"role": "user", "content": _slides_to_review_input(slides)}],
    )
    return msg.content[0].text.strip()


def _review_openai(slides: list[dict]) -> str:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user",   "content": _slides_to_review_input(slides)},
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def review_and_improve(slides: list[dict]) -> list[dict]:
    """Run a second LLM pass to improve slide quality.

    Returns the improved slides parsed back into internal dict format.
    On any failure returns the original slides unchanged so the pipeline
    always has output to render.

    Controlled by the REVIEW_ENABLED env var (default: true).
    Set REVIEW_ENABLED=false to skip this step and save an LLM call.
    """
    if os.environ.get("REVIEW_ENABLED", "true").lower() == "false":
        logger.info("Review pass disabled (REVIEW_ENABLED=false)")
        return slides

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    review_fn = _review_anthropic if provider == "anthropic" else _review_openai

    try:
        logger.info("Running review pass on %d slides", len(slides))
        raw      = review_fn(slides)
        improved = _parse_json_slides(raw)
        improved = _enforce_slide_limits(improved)
        improved = _enforce_bold_caps(improved)
        logger.info("Review pass complete — %d slides returned", len(improved))
        return improved
    except Exception as exc:
        logger.warning("Review pass failed (%s) — using original slides", exc)
        return slides


# ---------------------------------------------------------------------------
# Caption generation
# ---------------------------------------------------------------------------

CAPTION_PROMPT = """\
You write high-performing Instagram captions for carousel posts about Claude AI.

Given the carousel slides below, write a caption that matches this EXACT format:

---
[Hook line — rephrase or reinforce the first slide]

[Insight or problem line]
[Insight or problem line]

[Value or takeaway line]
[Value or takeaway line]

Follow @claudeinsights for more AI tips

#ClaudeAI #AItools #Productivity
---

SPACING RULES (critical):
  - Separate each thematic block with ONE blank line (\\n\\n)
  - The CTA line ("Follow @claudeinsights...") must be on its OWN line
  - Hashtags must be on their OWN line, separated from CTA by a blank line (\\n\\n)
  - NEVER place hashtags on the same line as the CTA
  - NEVER run hashtags directly after CTA without a blank line

STYLE:
  - One sentence per line
  - Separate EVERY line with a blank line (\\n\\n) — never single line breaks
  - Short, clear, beginner-friendly language
  - No fluff or filler words
  - 5–8 lines of body text (excluding hashtag line)

HASHTAGS:
  - 3–5 relevant hashtags
  - Use: #ClaudeAI #AItools #Productivity #ChatGPT #AITips or similar

OUTPUT:
  Return ONLY the caption text — no JSON, no quotes, no extra commentary.\
"""


def _build_caption_user_message(slides: list[dict]) -> str:
    """Format slides into a compact message for the caption LLM call."""
    lines = ["Carousel slides:"]
    for i, s in enumerate(slides):
        # Strip **markers** so the caption LLM sees clean text
        plain = re.sub(r'\*\*(.*?)\*\*', r'\1', s["heading"])
        lines.append(f"  {i + 1}. [{s['type'].upper()}] {plain}")
    return "\n".join(lines)


def _generate_caption_anthropic(slides: list[dict]) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=CAPTION_PROMPT,
        messages=[{"role": "user", "content": _build_caption_user_message(slides)}],
    )
    return message.content[0].text.strip()


def _generate_caption_openai(slides: list[dict]) -> str:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": CAPTION_PROMPT},
            {"role": "user",   "content": _build_caption_user_message(slides)},
        ],
        max_tokens=512,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def _format_caption(caption: str) -> str:
    """Enforce correct caption spacing regardless of LLM output.

    Rules applied deterministically:
    - Hashtag tokens (#word) are collected and moved to their own block
    - CTA line (contains @claudeinsights) is ensured to be on its own line
    - Body and hashtag block are separated by exactly two newlines
    - Trailing/leading whitespace is stripped
    """
    lines = [l.rstrip() for l in caption.splitlines()]

    # Separate hashtag lines from body lines
    hashtag_tokens: list[str] = []
    body_lines:     list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # A line is treated as a hashtag line if ALL non-empty tokens start with #
        tokens = stripped.split()
        if tokens and all(t.startswith('#') for t in tokens):
            hashtag_tokens.extend(tokens)
        else:
            # Inline hashtags mixed with text: split them out
            text_parts = [t for t in tokens if not t.startswith('#')]
            hash_parts = [t for t in tokens if t.startswith('#')]
            if text_parts:
                body_lines.append(" ".join(text_parts))
            hashtag_tokens.extend(hash_parts)

    # Deduplicate hashtags while preserving order
    seen: set[str] = set()
    unique_hashtags: list[str] = []
    for h in hashtag_tokens:
        if h.lower() not in seen:
            seen.add(h.lower())
            unique_hashtags.append(h)

    # Build final caption — double newline between every line for Instagram spacing
    body     = "\n\n".join(body_lines).strip()
    hashtags = " ".join(unique_hashtags)

    if hashtags:
        return f"{body}\n\n{hashtags}"
    return body


def _validate_caption(caption: str) -> None:
    """Raise ValueError if the caption is missing required elements."""
    lower = caption.lower()
    if "@claudeinsights" not in lower:
        raise ValueError("Caption missing @claudeinsights CTA — retrying.")
    lines = [l for l in caption.splitlines() if l.strip()]
    if len(lines) < 4:
        raise ValueError(f"Caption too short ({len(lines)} lines) — retrying.")


def generate_caption(slides: list[dict], max_retries: int = 2) -> str:
    """Generate an Instagram caption aligned with the given slides."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    gen_fn   = _generate_caption_anthropic if provider == "anthropic" else _generate_caption_openai
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            caption = gen_fn(slides)
            caption = _format_caption(caption)
            _validate_caption(caption)
            logger.info("Caption generated (%d lines)", len([l for l in caption.splitlines() if l.strip()]))
            return caption
        except Exception as exc:
            logger.warning("Caption attempt %d/%d failed: %s", attempt, max_retries, exc)
            last_err = exc
    # Non-fatal: return a safe fallback rather than crashing the whole pipeline
    logger.error("Caption generation failed after %d attempts — using fallback", max_retries)
    return "Follow @claudeinsights for more AI tips 🤖\n\n#ClaudeAI #AItools #Productivity"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_slides(
    topic: str,
    num_slides: int = 5,
    max_retries: int = 3,
) -> tuple[list[dict], str]:
    """
    Generate carousel slides for *topic* using the configured LLM.

    Parameters
    ----------
    topic : str      The subject of the carousel (user input).
    num_slides : int Exact number of slides to generate (4–7). Default 5.

    Returns
    -------
    slides : list[dict]
        Each dict has "type", "heading", and "description" keys.
        Word limits are enforced in code regardless of LLM output.
    caption : str
        Ready-to-post Instagram caption aligned with the slides.
        Falls back to a minimal CTA string if caption generation fails.
    """
    if not (4 <= num_slides <= 7):
        raise ValueError(f"num_slides must be between 4 and 7, got {num_slides}")

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
            logger.info("Slide generation attempt %d/%d (num_slides=%d)", attempt, max_retries, num_slides)
            raw    = backend(topic, num_slides)
            logger.debug("Raw LLM output: %s", raw[:500])
            slides = _parse_json_slides(raw, num_slides)
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
            if not _has_depth(slides):
                raise ValueError(
                    "Slides lack depth: need at least one example slide "
                    "(Instead of/Try/micro-example) AND one insight slide (because/—/=). "
                    "Retrying for more informative content."
                )
            if not _has_real_prompt_example(slides):
                raise ValueError(
                    "No real prompt example found. At least one content slide must include "
                    "a quoted, copy-pasteable Claude prompt. Retrying."
                )
            logger.info("Generated %d slides (validated: tip, depth, real prompt example)", len(slides))
            slides  = review_and_improve(slides)
            caption = generate_caption(slides)
            return slides, caption
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
