"""
generator.py — LLM-powered slide generation for Instagram carousels.

Supports both Anthropic (default) and OpenAI backends, selected via the
LLM_PROVIDER env var ("anthropic" | "openai").

Public API
----------
generate_slides(topic) → (list[dict], str)
    Returns (slides, caption) where slides is a list of dicts with
    "type", "heading", and "body" keys (4–7 slides), and caption
    is a ready-to-post Instagram caption string.
    "type" is one of: "hook", "content", "cta".
"""

import json
import logging
import math
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("carousel.generator")

# ---------------------------------------------------------------------------
# Small string utilities (must be defined before any function that calls them)
# ---------------------------------------------------------------------------

_MD_RE = re.compile(r'\*{1,2}|_{1,2}|~~')

def _strip_markdown(text: str) -> str:
    """Remove markdown formatting markers (**, *, _, ~~) from plain-text fields."""
    return _MD_RE.sub('', text)

# ---------------------------------------------------------------------------
# Template styles and word limits
# ---------------------------------------------------------------------------

TEMPLATE_STYLES: list[str] = [
    "dark_core",
    "light_image",
]

# Styles that require photo images
_VISUAL_STYLES: frozenset[str] = frozenset({"dark_core", "light_image"})

QUALITY_THRESHOLD = 0.7


# Per-style word limits (heading + body fields rendered separately)
WORD_LIMITS: dict[str, dict[str, int]] = {
    "dark_core": {
        "hook_heading":    8,
        "content_heading": 8,
        "content_body":    20,
        "cta_heading":     8,
        "cta_body":        12,
    },
    "light_image": {
        "hook_heading":    8,
        "content_heading": 8,
        "content_body":    20,
        "cta_heading":     8,
        "cta_body":        12,
    },
}

# ---------------------------------------------------------------------------
# Hook style catalogue
# Each entry: (NAME, instruction, example)
# One is picked at random per generation call so hooks vary across posts.
# ---------------------------------------------------------------------------

_HOOK_STYLES: list[tuple[str, str, str]] = [
    (
        "CONTRARIAN",
        "Challenge a belief about this specific topic. Use a keyword from the topic. Make the reader feel they've been doing it wrong.",
        'Topic "building apps with Claude Code" → "Stop building everything at once in Claude Code"',
    ),
    (
        "CURIOSITY",
        "Tease a surprising insight tied directly to this topic. Use a keyword from the topic. End with a gap the reader wants to close.",
        'Topic "Claude prompting tips" → "Most Claude prompts fail before you even **start**"',
    ),
    (
        "MISTAKE",
        "Call out a specific mistake the reader is probably making with this topic right now. Use a keyword from the topic.",
        'Topic "writing prompts for Claude" → "Your Claude prompts are missing **one** critical thing"',
    ),
    (
        "OUTCOME",
        "Lead with the desirable result specific to this topic. Use a keyword from the topic. Make the benefit immediate and concrete.",
        'Topic "step-by-step projects with Claude" → "Build complete projects step-by-step with Claude"',
    ),
    (
        "SPECIFIC",
        "Name one precise change or insight about this specific topic. Use a keyword from the topic. Specificity creates credibility.",
        'Topic "Claude for beginners" → "This one change makes Claude **far** more useful"',
    ),
]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_carousel_arc(num_slides: int) -> str:
    """Return a numbered slide-by-slot arc for *num_slides* slides.

    The arc is injected into the system prompt so the LLM always has a concrete
    map of what each slide should accomplish — not just a vague "flow" note.
    """
    content_count = num_slides - 2  # hook + cta bookend the content

    # Named content slots — ordered so the carousel reads as a progression
    _SLOT_POOL = [
        'Core principle — WHY this approach works (no transition word needed here)',
        'Step 1 — first concrete action; open with "First…"',
        'Step 2 — builds directly on Step 1; open with "Then…" or "Next…"',
        'Step 3 — the real prompt example goes here; open with "Now…"',
        'Tip — one specific tweak that improves the outcome; open with "Try…" or "Add…"',
        'Common mistake to avoid — name it clearly and explain why it fails',
        'Extra insight — deepen one earlier point',
        'Extra step — add one more concrete action',
    ]

    slots: list[str] = []
    # Always start with the core principle
    for slot in _SLOT_POOL:
        slots.append(slot)
        if len(slots) == content_count - 1:
            break
    # Last content slot is always the outcome
    slots.append('Outcome — concrete result the reader gets; open with "Finally…"')

    lines = ["Slide 1: Hook — creates tension or curiosity about the specific topic"]
    for i, label in enumerate(slots, start=2):
        lines.append(f"Slide {i}: {label}")
    lines.append(f"Slide {num_slides}: CTA — direct call to action")
    return "\n".join(lines)


def _word_limits_section(template_style: str) -> str:
    return "WORD LIMITS: Hook heading ≤12 w | Content heading ≤8 w | Content body ≤20 w | CTA heading ≤14 w\n"


def _output_format_section(num_slides: int, template_style: str) -> str:
    """Return the OUTPUT FORMAT block injected at the end of the system prompt."""
    return f"""\
OUTPUT FORMAT (strict JSON, exactly {num_slides} slides):
Fields: "heading" (title), "tag" (content only: TIP/FACT/INSIGHT/EXAMPLE/WORKFLOW/STAT/TOOL/MISTAKE), "text" (body, \\n-separated; "" for hook/cta).
{{
  "slides": [
    {{"type": "hook",    "heading": "Hook heading here",       "text": ""}},
    {{"type": "content", "heading": "Content heading", "tag": "TIP", "text": "First sentence. Second sentence."}},
    {{"type": "cta",     "heading": "We show you how to [x] every day.", "text": ""}}
  ]
}}
Array MUST have exactly {num_slides} items. "heading" and "text" required on every slide."""


def _build_system_prompt(num_slides: int, template_style: str = "dark_core") -> str:
    """Return the generation system prompt with the exact slide count baked in.

    A hook style is chosen randomly at call time so every generation request
    produces a structurally different opening — preventing the repetitive
    "Claude is powerful — most people…" pattern.
    """
    content_count = num_slides - 2
    hook_name, hook_instruction, hook_example = random.choice(_HOOK_STYLES)
    carousel_arc = _build_carousel_arc(num_slides)

    voice_section = ""
    if template_style == "dark_core":
        voice_section = """\
VOICE — write as if speaking directly to ONE person:
- Tone: calm, slow, thoughtful. Warm and human. Grounded and clear.
- Use "you" naturally throughout.
- Explain what it means for the reader, not just what happened.
- Simplify complex ideas; add light interpretation — your perspective.
- Avoid: newsreader tone, corporate phrasing, generic summaries, hype or buzzwords.
- Each slide should loosely follow: (1) what changed or the key concept, (2) what it means for you.

"""

    return f"""\
{voice_section}Do not invent statistics. Prefer qualitative insights.

FACTUALITY RULES:
- Do NOT include statistics, percentages, or numbers unless you can cite a real source.
- Any statistic MUST include a source in the same sentence (e.g., "according to X").
- If no source is available, do not include the statistic.
- Prefer qualitative insights over unverifiable claims.
- Never invent reports, studies, or benchmarks.

SLIDES: Exactly {num_slides}. Slide 1 = hook | Slides 2–{num_slides - 1} = content | Slide {num_slides} = cta

HOOK (Slide 1) — {hook_name}:
Rule: {hook_instruction}
e.g. {hook_example}
- ≤12 words. Must include a keyword from the topic. No em-dash or arrow ending. Don't start with "Claude".

HEADINGS (all slides):
- Use sentence case (only the first word capitalised, except proper nouns like AI)
- Do NOT capitalise every word
- No em-dashes
- No transition word starts (First, Then, Next, Now, Finally)
- Content ≤6 words; hook ≤12
- Never end with a preposition or conjunction

CONTENT SLIDES (2–{num_slides - 1}):
- Body: exactly 2 sentences, each ≤12 words, total ≤20 words.
- Bold 1 impactful word (prefer numbers, results, contrast words). Skip if nothing earns it.
- Include: insight ("because…"), concrete example (real prompt in context), or outcome (specific result).
- Each slide self-contained — never split a pattern (e.g. "Instead of/Try") across slides.
- Tag: one of TIP, FACT, INSIGHT, EXAMPLE, WORKFLOW, STAT, TOOL, MISTAKE

CONTENT SLIDES MUST follow this format:
{{
  "type": "content",
  "heading": "...",
  "tag": "...",
  "text": "Sentence one. Sentence two."
}}
Hook and CTA are the ONLY slides allowed to have empty text.
If a content slide has empty text, the output is invalid.

ARC:
{carousel_arc}

EMPHASIS: 1–2 bold words per slide. Bold outcomes/contrasts/actions only. Never bold filler.

ERRORS — fix the specific issue before returning:
- Wrong slide count → exactly {num_slides}
- Bad CTA format → "We show you [specific thing] every day."
- Incomplete sentence → complete it on the same slide
- Invalid JSON → fix formatting

Before returning JSON, check:
- No content slide has empty text
If any slide fails, fix it before returning.

{_word_limits_section(template_style)}
{_output_format_section(num_slides, template_style)}\
"""


# ---------------------------------------------------------------------------
# Incomplete-ending detection — shared by enforce_word_limit and validators
# ---------------------------------------------------------------------------

# Tokens that signal genuinely broken/truncated output when they appear as the
# final word of a slide.  Used by both _is_complete_slide() (hard validation)
# and _compress_heading() / enforce_word_limit() (cut-point avoidance).
_INCOMPLETE_TERMINALS: frozenset[str] = frozenset({
    "→", "->",      # bare arrow: contrast started but never resolved
    "the", "a", "an",   # lone article: following noun was cut
    "because",      # reason belongs on THIS slide, not the next one
    "instead",      # "instead of" split — comparison goes to next slide
})


# ---------------------------------------------------------------------------
# Word-limit enforcement (applied in code — never rely on LLM to obey)
# ---------------------------------------------------------------------------

def enforce_word_limit(text: str, max_words: int) -> str:
    """Truncate *text* to at most *max_words* words, ending at a natural boundary.

    After a hard word-count cut the function walks backwards to find the last
    sentence-final punctuation (.!?"), and if not found, the last clause
    boundary (em-dash, colon, comma) that is at least 60% into the text.
    This prevents mid-clause truncation that makes slides feel cut off.

    Also strips any unclosed **marker so the HTML renderer never sees a
    dangling opening bold tag.
    """
    words = text.split()
    if len(words) <= max_words:
        return text

    truncated = " ".join(words[:max_words])

    # Fix unclosed **marker (odd count = dangling open tag)
    if truncated.count("**") % 2 != 0:
        truncated = truncated.rsplit("**", 1)[0].rstrip()

    # Fix unbalanced quotes after truncation
    if truncated.count('"') % 2 != 0:
        truncated = truncated.rsplit('"', 1)[0].rstrip()

    # If already ends with sentence-final punctuation, we're done
    stripped = truncated.rstrip()
    if stripped and stripped[-1] in '.!?"':
        return stripped

    # Try to end at the last clause boundary in the second half.
    # Only skip a cutpoint if it would leave a bare arrow (→ / ->) at the end,
    # which means a contrast was started but never resolved.
    min_pos = int(len(stripped) * 0.55)  # must keep at least 55% of the text
    for punct in ('—', ':', ','):
        last_pos = stripped.rfind(punct)
        if last_pos >= min_pos:
            candidate = stripped[:last_pos].rstrip()
            last_tok = candidate.split()[-1].lower().rstrip('.,!?:"\'') if candidate.split() else ""
            if last_tok not in ("→", "->"):
                return candidate

    return stripped

def _enforce_slide_limits(slides: list[dict], template_style: str = "dark_core") -> list[dict]:
    """Ensure every slide's heading (and body) respects the word limit for its type."""
    limits = WORD_LIMITS.get(template_style, WORD_LIMITS["dark_core"])
    result = []
    for slide in slides:
        slide_type = slide["type"]
        heading    = slide.get("heading", "")
        body       = slide.get("body", "")

        if slide_type == "hook":
            max_h      = limits["hook_heading"]
            enforced_h = enforce_word_limit(heading, max_h)
            if enforced_h != heading:
                logger.info("Truncated hook heading %d→%d words", len(heading.split()), max_h)
            result.append({**slide, "heading": enforced_h, "body": ""})
        elif slide_type == "cta":
            max_h = limits["cta_heading"]
            max_b = limits["cta_body"]
            enforced_h = enforce_word_limit(heading, max_h)
            enforced_b = enforce_word_limit(body, max_b)
            result.append({**slide, "heading": enforced_h, "body": enforced_b})
        else:  # content
            max_h = limits["content_heading"]
            max_b = limits["content_body"]
            enforced_h = enforce_word_limit(heading, max_h)
            enforced_b = enforce_word_limit(body, max_b)
            if enforced_h != heading:
                logger.info("Truncated content heading %d→%d words", len(heading.split()), max_h)
            if enforced_b != body:
                logger.info("Truncated content body %d→%d words", len(body.split()), max_b)
            result.append({**slide, "heading": enforced_h, "body": enforced_b})
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
        capped_h = _cap_bold_phrases(slide.get("heading", ""))
        capped_b = _cap_bold_phrases(slide.get("body", ""))
        if capped_h != slide.get("heading", ""):
            logger.info("Capped bold phrases on %s slide heading", slide["type"])
        result.append({**slide, "heading": capped_h, "body": capped_b})
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
# Actionable prompt example validation
# ---------------------------------------------------------------------------

# Actionable instruction signals — verbs that indicate the slide is telling
# the reader to DO something with a prompt (not contrast phrasing).
_ACTIONABLE_SIGNALS = (
    "use ", "add ", "ask ", "write ", "structure ", "format ",
    "act as ", "paste ", "include ", "specify ", "tell ", "give ",
)

# Contextual phrases that indicate Claude is being directed — looser than
# _ACTIONABLE_SIGNALS so natural phrasing ("ask claude to...") still passes.
_CONTEXTUAL_SIGNALS = (
    "ask claude", "tell claude", "have claude", "prompt claude",
)

# Matches text inside curly or straight double-quote pairs
_QUOTED_CONTENT = re.compile(r'["\u201c]([^"\u201c\u201d\n]+)["\u201d]')
# Matches straight single-quote pairs with 10+ chars inside (avoids contractions like "it's")
_QUOTED_CONTENT_SINGLE = re.compile(r"'([^'\n]{10,})'")


def _has_actionable_prompt_example(slides: list[dict]) -> bool:
    """Return True if at least one content slide contains BOTH:

    1. A quoted prompt (any straight or curly quotes), AND
    2. Either:
       (a) an actionable instruction verb (use / add / ask / write / format / etc.), OR
       (b) a contextual instruction phrase (ask claude / tell claude / have claude / prompt claude)

    A quoted prompt with no instruction context does not pass.
    Checks both heading and body fields to support two-field heading styles.
    """
    for slide in slides:
        if slide["type"] != "content":
            continue
        text = slide.get("heading", "") + " " + slide.get("body", "")
        text_lower = text.lower()

        quoted_strings = _QUOTED_CONTENT.findall(text) + _QUOTED_CONTENT_SINGLE.findall(text)
        if not quoted_strings:
            continue

        has_action = (
            any(signal in text_lower for signal in _ACTIONABLE_SIGNALS)
            or any(phrase in text_lower for phrase in _CONTEXTUAL_SIGNALS)
        )
        if has_action:
            return True

    return False


# ---------------------------------------------------------------------------
# Slide completeness validation
# ---------------------------------------------------------------------------

def _compress_heading(text: str, max_words: int = 6) -> str:
    """Shorten an overly long heading to at most *max_words* words.

    Tries to end at a natural boundary (em-dash, colon) in the second half of
    the text before hard-truncating, to preserve meaning.  Bold markers are
    preserved and unclosed markers are stripped.
    """
    plain_words = text.split()
    if len(plain_words) <= max_words:
        return text

    truncated = " ".join(plain_words[:max_words])
    if truncated.count("**") % 2 != 0:
        truncated = truncated.rsplit("**", 1)[0].rstrip()

    # Prefer a clause boundary in the second half so the phrase still reads well
    min_pos = int(len(truncated) * 0.5)
    for punct in ("—", ":"):
        pos = truncated.rfind(punct)
        if pos >= min_pos:
            candidate = truncated[:pos].rstrip()
            last = candidate.split()[-1].lower().rstrip('.,!?:"\'') if candidate.split() else ""
            if last not in _INCOMPLETE_TERMINALS:
                return candidate

    return truncated


def _is_complete_slide(text: str) -> bool:
    """Return True unless the slide body is clearly incomplete or cross-slide dependent.

    Catches:
      - empty string
      - ends with a bare arrow (contrast opened, never closed)
      - ends with a lone article (the / a / an) — truncation artefact
      - ends with "because" — the reason belongs on this slide, not the next
      - ends with "instead" or "instead of" — comparison was deferred
      - bare "→ Try:" at the very end — contrast opened but payload missing
      - contrast lead-in ("Instead of:") with no "→" / "try:" on the same slide
        — the response half was placed on a different slide
    """
    plain = re.sub(r'\*\*(.*?)\*\*', r'\1', text).strip()
    if not plain:
        return False

    words = plain.split()
    last_word = words[-1].lower().rstrip('.,!?:"\'\u2018\u2019')

    # Bare arrow — contrast started, never resolved
    if last_word in ("→", "->"):
        return False

    # Lone article — truncation artefact
    if last_word in ("the", "a", "an"):
        return False

    # Trailing "because" — reason deferred to next slide
    if last_word == "because":
        return False

    # "→ Try:" at very end with nothing after the colon
    if re.search(r'[→>]\s*[Tt]ry\s*:\s*$', plain):
        return False

    if not re.search(r'[.!?]$', plain):
        return False

    return True

def _validate_completeness(
    slides: list[dict],
    template_style: str = "dark_core",
) -> list[dict]:
    """Auto-correct minor issues and hard-fail only on genuinely broken output.

    What this does:
      - Auto-compress headings >10 words (never a hard failure)
      - Hard-fail only when _is_complete_slide() returns False

    Returns the (possibly auto-corrected) slides list.
    """
    corrected = []
    broken: list[str] = []

    for s in slides:
        heading = s.get("heading", "")
        body    = s.get("body", "")

        # Auto-correct oversized headings — never a hard failure
        heading_words = len(heading.split())
        if heading_words > 10:
            old = heading
            heading = _compress_heading(heading, max_words=6)
            logger.info(
                "Auto-compressed %s heading (%d→%d words): %r → %r",
                s["type"], heading_words, len(heading.split()), old, heading,
            )
        # Check body for genuinely broken output only
        if body and not _is_complete_slide(body):
            broken.append(f"[{s['type']} body] {body!r}")

        corrected.append({**s, "heading": heading, "body": body})

    if broken:
        raise ValueError(
            "Truncated slide content detected (incomplete or cross-slide dependent):\n"
            + "\n".join(broken)
        )

    return corrected


def _is_valid_heading(text: str) -> bool:
    """Return True if the heading is usable — non-empty and not excessively long.

    Sentence-like headings, em-dashes, and comparative adjectives are all
    permitted.  Only genuinely broken cases are rejected:
      - empty string
      - more than 10 words (auto-compression is preferred; this is the last gate)
    """
    plain = re.sub(r'\*\*(.*?)\*\*', r'\1', text).strip()
    if not plain:
        return False
    if len(plain.split()) > 10:
        return False
    return True


def _clean_heading_punctuation(slides: list[dict]) -> list[dict]:
    """Replace em dashes in headings with commas for better readability."""
    result = []
    for slide in slides:
        heading = slide.get("heading", "")

        if "—" in heading:
            cleaned = re.sub(r'\s*—\s*', ', ', heading)
            cleaned = re.sub(r'\s+,', ',', cleaned)    # remove space before comma
            cleaned = re.sub(r',\s*,', ',', cleaned)   # collapse double commas
            cleaned = re.sub(r'\s{2,}', ' ', cleaned)  # collapse double spaces
            logger.info("Replaced em dash in heading: %r → %r", heading, cleaned)
            slide = {**slide, "heading": cleaned}

        result.append(slide)

    return result

# ---------------------------------------------------------------------------
# Depth validation — at least one example and one insight per carousel
# ---------------------------------------------------------------------------

# Markers for a concrete prompt example or use-case slide (no contrast required)
_EXAMPLE_MARKERS = ('"', "\u201c", "\u2018", "e.g.", "for example", "act as ", "ask claude")

# Markers for an insight/explanation slide
_INSIGHT_MARKERS = (
    "because", "so that", "this means", "which helps",
    "so ", "therefore", "as a result"
)


def _has_depth(slides):
    has_example = _has_actionable_prompt_example(slides)

    has_insight = any(
        "because" in (s["heading"] + " " + s["body"]).lower()
        or " — " in (s["heading"] + " " + s["body"])
        for s in slides if s["type"] == "content"
    )

    return has_example and has_insight

# ---------------------------------------------------------------------------
# Quality scoring (moves system from pass/fail → quality-based selection)
# ---------------------------------------------------------------------------

_WEAK_HOOKS   = ("improve", "better", "more effective", "tips", "guide")
_VAGUE_PHRASES = ("improve", "better", "optimize", "enhance", "more effective", "increase efficiency")
_ACTION_VERBS  = ("add", "use", "ask", "write", "give", "paste", "include", "specify")


def _score_slides(slides: list[dict]) -> float:
    """Return a quality score between 0 and 1 for a carousel.

    Scores based on:
    - Hook strength (specific, non-generic)
    - Presence of actionable prompt example (quoted + instruction)
    - Presence of insight/explanation (because / em-dash)
    - Specificity (avoids vague filler)
    - Structural variety (insight + action + example)
    """

    score = 0
    max_score = 5

    # --- 1. Hook strength ---
    hook = slides[0]["heading"].lower()
    if not any(w in hook for w in _WEAK_HOOKS) and len(hook.split()) >= 4:
        score += 1

    # --- 2. Actionable prompt example ---
    if _has_actionable_prompt_example(slides):
        score += 1

    # --- 3. Insight presence ---
    if any(
        "because" in (s["heading"] + s["body"]).lower()
        or "\u2014" in (s["heading"] + s["body"])
        or " — " in (s["heading"] + s["body"])
        for s in slides if s["type"] == "content"
    ):
        score += 1

    # --- 4. Specificity (penalise vague language) ---
    vague_count = sum(
        any(v in (s["heading"] + s["body"]).lower() for v in _VAGUE_PHRASES)
        for s in slides if s["type"] == "content"
    )
    if vague_count <= 1:
        score += 1

    # --- 5. Structural variety ---
    content_text = [(s["heading"] + " " + s["body"]).lower() for s in slides if s["type"] == "content"]
    patterns = {
        "insight": any("because" in t or " — " in t for t in content_text),
        "action":  any(
                       any(v in (s.get("heading", "") + " " + s.get("body", "")).lower()
                           for v in _ACTION_VERBS)
                       for s in slides if s["type"] == "content"
                   ),
        "example": any('"' in t or "\u201c" in t or "\u2018" in t for t in content_text),
    }
    if sum(patterns.values()) >= 2:
        score += 1

    return score / max_score

# ---------------------------------------------------------------------------
# Backend: Anthropic
# ---------------------------------------------------------------------------

def _generate_anthropic(
    topic: str,
    num_slides: int,
    error_context: str = "",
    template_style: str = "text_only",
    hook: Optional[str] = None,
) -> str:
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
    logger.info(
        "Calling Anthropic API for topic: %r (num_slides=%d, style=%s)",
        topic, num_slides, template_style,
    )

    user_content = f"Topic: {topic}"
    if hook:
        user_content += f"\n\nOpening hook (must be the first slide heading, verbatim): {hook}"
    if error_context:
        user_content += f"\n\nPREVIOUS ATTEMPT FAILED — fix this error:\n{error_context}"

    system_prompt = _build_system_prompt(num_slides, template_style)
    logger.info("System prompt chars: %d", len(system_prompt))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    logger.info("API call input tokens: %d", message.usage.input_tokens)

    for block in message.content:
        logger.info("BLOCK TYPE: %s", getattr(block, "type", None))

    text_blocks = [b for b in message.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        raise ValueError("No text content in API response")
    return text_blocks[-1].text


# ---------------------------------------------------------------------------
# Backend: OpenAI
# ---------------------------------------------------------------------------

def _generate_openai(
    topic: str,
    num_slides: int,
    error_context: str = "",
    template_style: str = "text_only",
    hook: Optional[str] = None,
) -> str:
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
    logger.info(
        "Calling OpenAI API for topic: %r (num_slides=%d, style=%s)",
        topic, num_slides, template_style,
    )

    user_content = f"Topic: {topic}"
    if hook:
        user_content += f"\n\nOpening hook (must be the first slide heading, verbatim): {hook}"
    if error_context:
        user_content += f"\n\nPREVIOUS ATTEMPT FAILED — fix this error:\n{error_context}"

    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": _build_system_prompt(num_slides, template_style)},
            {"role": "user", "content": user_content},
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json_slides(
    raw: str,
    num_slides: int = 5,
    template_style: str = "text_only",
) -> list[dict]:
    """Parse LLM JSON output into a validated list of slide dicts.

    For text_only: reads the "text" field → stored as heading, body="".
    For heading styles: reads "heading" + "text" → stored as heading + body.
    """
    text = raw.strip()

    # Strip markdown code fences if the model wrapped output anyway
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        parsed = _parse_json(text)
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

    if not (4 <= len(slides) <= 10):
        raise ValueError(f"Expected 4–10 slides, got {len(slides)}")
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

        if slide_type not in valid_types:
            raise ValueError(
                f"Slide {i} has invalid type {slide_type!r}. "
                f"Must be one of: {sorted(valid_types)}"
            )

        # Heading styles: separate heading and body fields
        heading_val = (s.get("heading") or "").strip()
        body_val    = (s.get("text") or "").strip()

        if not heading_val:
            raise ValueError(f"Slide {i} has empty 'heading'")

        if not body_val and slide_type != "hook":
            logger.debug(
                "Slide %d (%s) has empty body — acceptable for hook, warning for others",
                i, slide_type,
            )

        tag_val = (s.get("tag") or "").strip().upper()
        result.append({
            "type":    slide_type,
            "heading": heading_val,
            "body":    body_val,
            "tag":     tag_val,
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

def _build_review_prompt(template_style: str = "dark_core") -> str:
    """Build a review prompt for the given template style."""
    return_format = """\
Return ONLY a JSON array with two fields per slide — no extra text:
[
  { "type": "hook",    "heading": "...", "text": "" },
  { "type": "content", "heading": "...", "text": "..." },
  { "type": "cta",     "heading": "...", "text": "..." }
]"""
    word_limits = (
        "hook heading: max 8 words | "
        "content heading: max 8 words | content body (text): max 20 words | "
        "cta heading: max 8 words | cta body (text): max 12 words"
    )

    return f"""\
You are an expert Instagram content strategist. You will receive a set of carousel
slides and return an IMPROVED version that is sharper, clearer, and more valuable.

APPLY THESE FIXES:

1. HOOK — must be a complete sentence with contrast, curiosity, or tension
   BAD:  "Claude AI is powerful — most beginners waste"   ← cut off
   GOOD: "Claude AI is powerful — most beginners use it **wrong**"

2. REMOVE generic filler: "Welcome to...", "we post...", "our followers..."
   Replace with direct insights or statements about the user.

3. DEPTH — every content slide must do ONE of these (no empty claims):
   A) Insight+reason: "[claim] — because [short reason]"
      e.g. "**Specific** prompts work better — because Claude knows exactly what to do"
   B) Concrete example: a real Claude prompt shown in action
      e.g. "Ask Claude: 'Explain X in 3 steps with examples' — output improves **immediately**"
   C) Concrete outcome: the specific result, quantified or made tangible
      e.g. "Structured prompts cut editing time — answers arrive **ready** to use"
   D) Contrast (ONLY if the slide is about correcting a specific mistake):
      e.g. 'Instead of "explain this" → Try "explain this simply with 3 examples"'
      Do NOT add contrast to slides that are already clear — prefer A, B, or C.
   Carousel must include at least one A (insight) AND one B (example).
   NEVER write vague claims like "this improves results" without showing HOW.

4. STRUCTURE: Hook → Problem → Insight → Tip → Outcome → CTA

5. WORD LIMITS (hard): {word_limits}

6. EMPHASIS — use **word** markdown bold for 1–2 words per slide only:
   ✓ Bold: outcomes (better, faster), contrasts (wrong, mistake), actions (specific, structured)
   ✗ Never bold: filler (real, things), generic nouns (examples, potential)

7. STYLE: short punchy phrases, no fluff, beginner-friendly, high clarity

{return_format}"""


def _slides_to_review_input(slides: list[dict]) -> str:
    """Format slides into a numbered list for the review LLM."""
    lines = ["Current carousel slides:"]
    for i, s in enumerate(slides):
        body_part = f" | {s['body']}" if s.get("body") else ""
        lines.append(f"  {i + 1}. [{s['type'].upper()}] {s['heading']}{body_part}")
    return "\n".join(lines)


def _review_anthropic(slides: list[dict], template_style: str = "dark_core") -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=_build_review_prompt(template_style),
        messages=[{"role": "user", "content": _slides_to_review_input(slides)}],
    )
    logger.info("API call input tokens: %d", msg.usage.input_tokens)
    return msg.content[0].text.strip()


def _review_openai(slides: list[dict], template_style: str = "dark_core") -> str:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": _build_review_prompt(template_style)},
            {"role": "user",   "content": _slides_to_review_input(slides)},
        ],
        max_tokens=1024,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def review_and_improve(slides: list[dict], template_style: str = "dark_core") -> list[dict]:
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
        logger.info("Running review pass on %d slides (style=%s)", len(slides), template_style)
        raw      = review_fn(slides, template_style)
        # Pass the original count so the validator enforces the same slide count
        # as the primary generation pass — without this the default of 5 silently
        # replaces a correctly-generated 7-slide carousel with a 5-slide one.
        improved = _parse_json_slides(raw, num_slides=len(slides), template_style=template_style)
        improved = _enforce_slide_limits(improved, template_style)
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
You write high-performing Instagram captions for carousel posts about AI.

Given the carousel slides below, write a caption that matches this EXACT format:

---
[Hook line — rephrase or reinforce the first slide]

[Insight or problem line]
[Insight or problem line]

[Value or takeaway line]
[Value or takeaway line]

Follow @focuslabs.ai for more AI content

#AI #ClaudeAI #AItools #Productivity #ChatGPT
---

SPACING RULES (critical):
  - Separate each thematic block with ONE blank line (\\n\\n)
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
  - Use: #AI #ClaudeAI #AItools #Productivity #ChatGPT or similar

OUTPUT:
  Return ONLY the caption text — no JSON, no quotes, no extra commentary.\
"""


def _build_caption_user_message(slides: list[dict]) -> str:
    """Format slides into a compact message for the caption LLM call."""
    lines = ["Carousel slides:"]
    for i, s in enumerate(slides):
        # Strip **markers** so the caption LLM sees clean text
        heading_plain = re.sub(r'\*\*(.*?)\*\*', r'\1', s.get("heading", ""))
        body_plain    = re.sub(r'\*\*(.*?)\*\*', r'\1', s.get("body", ""))
        slide_text    = f"{heading_plain} — {body_plain}" if body_plain else heading_plain
        lines.append(f"  {i + 1}. [{s['type'].upper()}] {slide_text}")
    return "\n".join(lines)


def _generate_caption_anthropic(slides: list[dict]) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=CAPTION_PROMPT,
        messages=[{"role": "user", "content": _build_caption_user_message(slides)}],
    )
    logger.info("API call input tokens: %d", message.usage.input_tokens)
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
    - CTA line (contains @focuslabs.ai) is ensured to be on its own line
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
    if "@focuslabs.ai" not in lower:
        raise ValueError("Caption missing @focuslabs.ai CTA — retrying.")
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
    return "Follow @focuslabs.ai for more AI content 🤖\n\n#ClaudeAI #AItools #Productivity"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

STOPWORDS = {
    "the", "a", "an", "that", "this", "these", "those",
    "is", "are", "was", "were", "be", "to", "of", "and",
    "in", "on", "for", "with", "at", "by", "from",
}

PRIORITY_WORDS = {
    "better", "faster", "clearer", "stronger", "simple", "powerful",
    "wrong", "right", "mistake", "instead", "actually",
    "build", "write", "structure", "system", "prompt", "task",
    "workflow", "tools", "output",
}

WEAK_WORDS = {
    "good", "bad", "new", "old", "big", "small",
    "use", "make", "do", "get", "give", "take",
    "know", "think", "want", "need",
    "really", "very", "quite", "just", "even",
    "every", "most", "many", "some", "all",
}

CTA_OPTIONS = [
    "We show you what <span class=\"serif\">matters</span> in AI every day.",
    "We show you the best AI <span class=\"serif\">tools</span> every day.",
    "We show you what’s <span class=\"serif\">new</span> in AI every day.",
    "We show you what <span class=\"serif\">works</span> in AI every day.",
    "We show you real AI <span class=\"serif\">insights</span> every day.",
    "We show you AI that’s <span class=\"serif\">worth</span> it every day.",
    "We show you the <span class=\"serif\">latest</span> in AI every day.",
    "We show you <span class=\"serif\">useful</span> AI every day.",
    "We show you AI without <span class=\"serif\">noise</span> every day.",
    "We show you AI that <span class=\"serif\">delivers</span> every day.",
]

def _format_cta() -> str:
    return random.choice(CTA_OPTIONS)

def _finalise_slides(slides: list[dict], topic: str) -> list[dict]:
    # Strip markdown from hook + CTA
    slides[0]  = {**slides[0],  "heading": _strip_markdown(slides[0]["heading"])}
    slides[-1] = {**slides[-1], "heading": _strip_markdown(slides[-1]["heading"])}

    # Force CTA (no conditions, no model control)
    slides[-1] = {
        **slides[-1],
        "heading": _format_cta(),
        "body": ""
    }

    # Apply italics LAST
    for s in slides:
        if s["type"] == "cta":
            continue
        if s["type"] == "pattern_break":
            s["heading"] = italicise_one_word(s["heading"])
        else:
            s["heading"] = italicise_one_word(s["heading"])

    return slides

def italicise_one_word(text: str) -> str:
    if "<span" in text:
        return text

    words = text.split()
    if len(words) < 3:
        return text

    max_span_words = 2  # 👈 NEW

    def clean_word(w):
        return w.strip(".,!?").lower()

    def score_span(start, length):
        span = words[start:start+length]
        score = 0

        for i, w in enumerate(span):
            clean = clean_word(w)

            if clean in STOPWORDS:
                return -10  # kill bad spans early

            if any(c.isdigit() for c in clean):
                score += 5

            if clean in PRIORITY_WORDS:
                score += 4

            if clean in WEAK_WORDS:
                score -= 4

            if clean.endswith("ly") or clean.endswith("ed"):
                score -= 2

            score += min(len(clean), 10) * 0.2

        # slight end-weight bias
        score += start / len(words)

        # penalty so 2-word spans only win when both words are clearly strong
        if length == 2:
            score -= 1.5

        return score

    best = (None, -1)  # (start_idx, length)

    for i in range(1, len(words)):
        for length in range(1, max_span_words + 1):
            if i + length > len(words):
                continue
            s = score_span(i, length)
            if s > best[1]:
                best = ((i, length), s)

    (start, length), _ = best

    span = " ".join(words[start:start+length])
    words[start:start+length] = [f'<span class="serif">{span}</span>']

    return " ".join(words)


def _generate_pattern_break_text(topic: str) -> str:
    """Generate a short (≤8 word) transition phrase for the pattern break slide."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    prompt = (
        f"Write exactly ONE short transition phrase (maximum 8 words) for a mid-carousel "
        f"pattern break slide. Topic: {topic}\n\n"
        f"Rules:\n"
        f"- Acts as a pivot between the first and second half of the carousel\n"
        f"- Reinforces the mechanism, not the topic\n"
        f"- Start with: 'This is where', 'Now', or 'Here is the shift'\n"
        f"- No punctuation chains, no slogans, no generic motivational phrases\n"
        f"- Good: 'This is where the approach changes', 'Now the pattern becomes clear'\n"
        f"- Bad: 'This changes everything', 'Work smarter not harder'\n"
        f"- Output ONLY the phrase, nothing else"
    )
    try:
        if provider == "anthropic":
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            msg = client.messages.create(
                model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text.strip().strip("\"'")
        else:
            import openai as _openai
            client = _openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            resp = client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip().strip("\"'")
        words = text.split()
        if len(words) > 8:
            text = " ".join(words[:8])
        return text
    except Exception as exc:
        logger.warning("Pattern break LLM call failed (%s) — using fallback", exc)
        return "This is where it becomes clear"


def insert_pattern_break(
    slides: list[dict], topic: str, template_style: str
) -> list[dict]:
    """Insert one pattern_break slide at the midpoint of content slides.

    Only runs for dark_core. Position: after hook, before CTA, centred within content.
    content_count = total_slides - 2
    insert_index  = 1 + ceil(content_count / 2)
    """
    if template_style != "dark_core":
        return slides

    content_count = len(slides) - 2
    insert_index = 1 + math.ceil(content_count / 2)

    heading_text = _generate_pattern_break_text(topic)
    pattern_break = {
        "type": "pattern_break",
        "heading": heading_text,
        "body": "",
    }

    return slides[:insert_index] + [pattern_break] + slides[insert_index:]


def generate_slides(
    topic: str,
    num_slides: int = 5,
    max_retries: int = 3,
    template_style: Optional[str] = None,
    hook: Optional[str] = None,
) -> tuple[list[dict], str]:
    """
    Generate carousel slides for *topic* using the configured LLM.

    Parameters
    ----------
    topic : str                 The subject of the carousel (user input).
    num_slides : int            Exact number of slides to generate (4–10). Default 5.
    template_style : str | None One of TEMPLATE_STYLES.  None defaults to "text_only"
                                for backward compatibility.

    Returns
    -------
    slides : list[dict]
        Each dict has "type", "heading", and "body" keys.
        Word limits are enforced in code regardless of LLM output.
    caption : str
        Ready-to-post Instagram caption aligned with the slides.
        Falls back to a minimal CTA string if caption generation fails.
    """
    slides: list[dict] = []
    if not (4 <= num_slides <= 10):
        raise ValueError(f"num_slides must be between 4 and 10, got {num_slides}")

    if template_style is None:
        template_style = "dark_core"
    if template_style not in TEMPLATE_STYLES:
        raise ValueError(
            f"Unknown template_style {template_style!r}. "
            f"Choose one of: {TEMPLATE_STYLES}"
        )

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    backends = {
        "anthropic": _generate_anthropic,
        "openai":    _generate_openai,
    }

    if provider not in backends:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. Choose 'anthropic' or 'openai'."
        )

    logger.info("Using hook: %s", hook or "(none)")

    backend      = backends[provider]
    last_error: Optional[Exception] = None
    error_context: str              = ""

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Slide generation attempt %d/%d (num_slides=%d, style=%s)",
                attempt, max_retries, num_slides, template_style,
            )
            raw = backend(topic, num_slides, error_context, template_style, hook)

            slides = _parse_json_slides(raw, num_slides, template_style)

            hook_text = slides[0]["heading"]
            if len(hook_text.split()) > 16:
                hook_text = enforce_word_limit(hook_text, 12)

            slides = _enforce_slide_limits(slides, template_style)
            slides = _enforce_bold_caps(slides)
            slides = _clean_heading_punctuation(slides)
            slides = [
                {**s, "heading": _compress_heading(s["heading"])}
                if len(s.get("heading", "").split()) > 10
                else s
                for s in slides
            ]

            score = _score_slides(slides)
            logger.info("Slide score: %.2f", score)

            # For text_only the full sentence check still applies.
            hook_is_heading_style = True  # both dark_core and light_image use heading style
            hook_text = slides[0]["heading"]
            hook_broken = (
                hook_text.rstrip().endswith("—")
                or hook_text.rstrip().endswith("→")
                or (not hook_is_heading_style and not _is_complete_hook(hook_text))
            )
            if hook_broken:
                raise ValueError(
                    f"Hook is incomplete: {hook_text!r}. "
                    "Retrying for a complete hook."
                )
            if not _has_depth(slides):
                logger.warning("Missing depth — continuing anyway")
            try:
                slides = _validate_completeness(slides, template_style)
            except ValueError as e:
                logger.warning("Completeness validation failed — continuing: %s", e)
            logger.info(
                "Generated %d slides (validated: completeness, prompt example, depth)",
                len(slides),
            )
            # Auto-compress any remaining oversized headings before review
            slides = [
                {**s, "heading": _compress_heading(s["heading"])}
                if not _is_valid_heading(s["heading"])
                else s
                for s in slides
            ]
            # if score < QUALITY_THRESHOLD:
            #     time.sleep(2)
            #     slides = review_and_improve(slides, template_style)
            #     slides = _clean_heading_punctuation(slides)
            if score < QUALITY_THRESHOLD:
                logger.warning("Low quality score: %.2f — accepting anyway", score)

            # Restore original hook verbatim (survives review_and_improve + _compress_heading)
            if hook:
                slides[0] = {**slides[0], "heading": hook}

            slides = insert_pattern_break(slides, topic, template_style)
            slides = _finalise_slides(slides, topic)

            caption = generate_caption(slides)
            if os.environ.get("DEBUG", "false").lower() == "true":
                caption += f"\n\n[DEBUG] template={template_style}"
            # caption = (
            #     f"{caption}\n\n"
            #     f"[DEBUG] template={template_style} | image_enabled={_IMAGE_ENABLED}"
            # )
            for s in slides:
                logger.info("FINAL HEADING [%s]: %s", s.get("type"), s.get("heading"))
            return slides, caption
        except Exception as exc:
            logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, exc)
            logger.info("FAIL_REASON: %s", str(exc))
            last_error    = exc
            error_context = str(exc)   # fed back into the next LLM call
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info("Waiting %ds before retry…", wait)
                time.sleep(wait)

    logger.error("Returning last generated slides despite errors: %s", last_error)

    # if not slides:
    #     slides = [
    #         {"type": "hook", "heading": topic, "body": ""},
    #         {"type": "content", "heading": "Something went wrong", "body": "Please try again."},
    #         {"type": "cta", "heading": _format_cta(), "body": ""}
    #     ]
    if not slides:
        raise RuntimeError("Slide generation failed completely")

    slides = insert_pattern_break(slides, topic, template_style)
    slides = _finalise_slides(slides, topic)
    caption = generate_caption(slides)
    for s in slides:
        logger.info("FINAL HEADING [%s]: %s", s.get("type"), s.get("heading"))
    return slides, caption


# ---------------------------------------------------------------------------
# Light image pipeline — vision-driven slide generation
# ---------------------------------------------------------------------------

def _parse_json(text: str):
    text = text.strip()

    # Remove markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text.strip())

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

def _safe_json_load(raw: str):
    # normalise quotes
    raw = raw.replace("“", '"').replace("”", '"').replace("’", "'")

    # quote keys
    raw = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', raw)

    # remove trailing commas
    raw = re.sub(r',\s*([}\]])', r'\1', raw)

    return json.loads(raw)

def _generate_single_image_slide(client, topic, img_bytes, img_type, retries=3):
    import base64, json, re, time

    b64 = base64.standard_b64encode(img_bytes).decode("utf-8")

    for attempt in range(1, retries + 1):
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=_VISION_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Generate slide content for this image. Topic context: {topic}",
                    },
                ],
            }],
        )

        raw = msg.content[0].text.strip()

        # clean code fences
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw.strip())

        try:
            logger.error("RAW MODEL OUTPUT:\n%s", raw)
            slide_data = _safe_json_load(raw)
        except json.JSONDecodeError:
            if attempt == retries:
                raise
            time.sleep(1.5 * attempt)
            continue

        # --- HARD VALIDATION (this is key) ---
        heading = slide_data.get("heading", "")
        body = slide_data.get("body") or slide_data.get("text") or ""

        text = (heading + " " + body).lower()

        GENERIC_PATTERNS = [
            r"this (step|part) is",
            r"follow along",
            r"part of (the )?workflow",
            r"part of (the )?process",
            r"this shows (the )?(step|process)",
        ]

        # 1. Reject generic filler
        if any(re.search(p, text) for p in GENERIC_PATTERNS):
            if attempt == retries:
                raise ValueError("Generic filler detected after retries")
            time.sleep(1.5 * attempt)
            continue

        # 2. Reject weak headings
        if len(heading.split()) < 3:
            if attempt == retries:
                raise ValueError("Heading too vague")
            time.sleep(1.5 * attempt)
            continue

        # 3. Reject non-actionable slides
        ACTION_WORDS = (
            "add", "use", "export", "click", "run", "generate",
            "create", "build", "write", "send", "open",
            "build", "create", "generate", "run",
        )

        has_action = any(v in text for v in ACTION_WORDS)

        has_insight = any(w in text for w in [
            "is", "are", "means", "shows", "reveals", "confirms",
            "contains", "exists", "carries", "exposes",
            "shifts", "transforms", "enables", "allows", "supports", "moves", "turns",
        ])

        has_outcome = any(w in text for w in [
            "result", "risk", "faster", "improves", "fix", "prevents",
            "improves", "reduces", "faster", "better", "simpler", "replaces", "automates",
        ])

        if not (has_action or has_insight or has_outcome):

            print("---- ACTIONABLE DEBUG ----")
            print("HEADING:", heading)
            print("BODY:", body)
            print("TEXT:", text)
            print("ACTION WORD MATCH:", [v for v in ACTION_WORDS if v in text])
            print("--------------------------")

            # Fallback: accept any slide with sufficient content
            body_sentences = [s.strip() for s in re.split(r'[.!?]+', body) if s.strip()]
            if len(body_sentences) >= 2 and len(text.split()) > 10:
                return slide_data

            if attempt == retries:
                raise ValueError("No actionable content")
            time.sleep(1.5 * attempt)
            continue

        return slide_data

    raise RuntimeError("Unreachable")

_VISION_SYSTEM = """\
Analyze this image and generate Instagram carousel slide content.

Describe what is happening in the image as a specific action or result.

Focus on:
- what is being done
- what changes as a result
- what the user gains

Avoid generic descriptions that could apply to any image.

Each slide must represent a DISTINCT idea or benefit.
Do NOT use words like "Step", "Next", "Then", or imply progression between slides.
Even if images are similar, treat each slide independently.

Respond ONLY with valid JSON — no text before or after:
If unsure, prioritise valid JSON over stylistic perfection.
{
  "heading": "Short outcome-driven heading (max 6 words, no em-dashes)",
  "tag": "one of: TIP/FACT/INSIGHT/EXAMPLE/WORKFLOW/STAT/TOOL/MISTAKE",
  "body": "Two concise sentences tied to what is shown. Total max 20 words. Bold 1 key word with **word**."
}

STRICT JSON RULES:
- All keys MUST be in double quotes
- Do NOT use single quotes for keys
- Escape any quotes inside strings

Heading: action or outcome driven, max 6 words, no em-dashes.
Body: 1–2 sentences, ≤24 words total, no em-dashes."""

def generate_light_slides(
    topic: str,
    hook: str,
    image_bytes_list: list[bytes],
    image_types: list[str],
) -> dict:
    """Generate carousel slides for the light image-driven pipeline.

    Analyzes each uploaded image with Claude vision to infer heading + body,
    then builds: hook slide → one content slide per image → CTA slide.

    Returns dict with keys: template, hook, slides, cta.
    """
    import anthropic
    import base64

    if not (1 <= len(image_bytes_list) <= 8):
        raise ValueError(f"Expected 1–8 images, got {len(image_bytes_list)}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    content_slides = []
    for i, (img_bytes, img_type) in enumerate(zip(image_bytes_list, image_types)):
        slide_data = _generate_single_image_slide(client, topic, img_bytes, img_type)

        heading = slide_data.get("heading")
        body = slide_data.get("body") or slide_data.get("text") or ""

        if not heading or not body:
            raise ValueError("Missing heading or body from vision model")

        content_slides.append({
            "type": "content",
            "heading": _strip_markdown(heading),
            "body": body,
            "tag": (slide_data.get("tag") or "WORKFLOW").strip().upper(),
        })

    slides = [
        {"type": "hook", "heading": _strip_markdown(hook), "body": "", "tag": ""},
        *content_slides,
        {"type": "cta", "heading": "", "body": "", "tag": ""},
    ]

    slides = _enforce_slide_limits(slides, "light_image")
    slides = _enforce_bold_caps(slides)
    slides = _clean_heading_punctuation(slides)
    slides = [
        {**s, "heading": _compress_heading(s["heading"])}
        if not _is_valid_heading(s["heading"])
        else s
        for s in slides
    ]

    slides = _finalise_slides(slides, topic)

    return {
        "template": "light_image",
        "hook":     hook,
        "slides":   slides,
        "cta":      slides[-1]["heading"],
    }
