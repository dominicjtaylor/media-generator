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
import random
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("carousel.generator")

# ---------------------------------------------------------------------------
# Template styles and word limits
# ---------------------------------------------------------------------------

TEMPLATE_STYLES: list[str] = ["text_only", "headings_and_text", "headings_text_image"]

# Text-only pool used when no Lummi API key is present — prevents any
# image-fetch attempt or image-related fallback from being triggered.
_TEXT_ONLY_STYLES: list[str] = ["text_only", "headings_and_text"]

# Detected once at import time; restart the server after adding/removing the key.
# _IMAGE_ENABLED is True when EITHER the Lummi API key is present OR a populated
# local fallback directory exists (assets/lummi_images/ with at least one image).
_LOCAL_IMAGE_DIR: Path = Path("assets/lummi_images")
_LOCAL_IMAGE_ENABLED: bool = _LOCAL_IMAGE_DIR.is_dir() and any(
    p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    for p in _LOCAL_IMAGE_DIR.iterdir()
)
_IMAGE_ENABLED: bool = bool(os.getenv("LUMMI_API_KEY")) or _LOCAL_IMAGE_ENABLED

QUALITY_THRESHOLD = 0.6

def select_template_style() -> str:
    """Return a template style name based on current API availability.

    Without LUMMI_API_KEY: random choice from text-only styles only.
    With LUMMI_API_KEY:    full rotation including headings_text_image.
    """
    pool = TEMPLATE_STYLES if _IMAGE_ENABLED else _TEXT_ONLY_STYLES
    chosen = random.choice(pool)
    logger.info("Template style selected: %r  (image_enabled=%s)", chosen, _IMAGE_ENABLED)
    return chosen


# Per-style word limits.  For heading styles the limits are split across the
# heading ({{HEADING}}) and body ({{TEXT}}) fields rendered separately.
WORD_LIMITS: dict[str, dict[str, int]] = {
    "text_only": {
        "hook":    8,
        "content": 15,
        "cta":     12,
    },
    "headings_and_text": {
        "hook_heading":    8,
        "content_heading": 8,
        "content_body":    20,
        "cta_heading":     8,
        "cta_body":        12,
    },
    "headings_text_image": {
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
    """Return the WORD LIMITS block for the system prompt body.

    For heading styles the full word limits are in _output_format_section;
    this returns a short placeholder to avoid duplication.
    """
    if template_style == "text_only":
        return """\
WORD LIMITS:

- Hook: max 8 words
- Content slides: max 15 words
- CTA: max 12 words

---"""
    # Heading styles: word limits are stated in the OUTPUT FORMAT section at the end.
    return """\
WORD LIMITS:

See the OUTPUT FORMAT section at the end of this prompt for per-field word limits.

---"""


def _output_format_section(num_slides: int, template_style: str) -> str:
    """Return the OUTPUT FORMAT block injected at the end of the system prompt.

    NOTE: This function is called from within an f-string expression in
    _build_system_prompt.  The returned string is inserted verbatim — it is NOT
    processed a second time for {{ }} escapes.  So use {{ }} here to get literal
    { } in the output (standard f-string escaping applied once).
    """
    if template_style == "text_only":
        return f"""\
OUTPUT FORMAT (STRICT JSON):

{{
  "slides": [
    {{"type": "hook",    "text": "You're prompting Claude **wrong** — here's why"}},
    {{"type": "content", "text": "Specific prompts work better — because Claude knows exactly what to do"}},
    {{"type": "content", "text": "Structured prompts cut editing time — Claude returns answers **ready** to use"}},
    {{"type": "content", "text": "Add your role upfront — 'Act as a teacher' changes every answer **instantly**"}},
    {{"type": "cta",     "text": "Follow @claudeinsights for more Claude tips"}}
  ]
}}

The "slides" array MUST contain EXACTLY {num_slides} objects.
If your output does not meet ALL rules, regenerate internally until it does."""

    # headings_and_text and headings_text_image share the same two-field format.
    image_hook_note = ""
    if template_style == "headings_text_image":
        image_hook_note = (
            "\nIMPORTANT: Slide 1 (hook) MUST have \"text\": \"\" — "
            "an image is injected into that slide automatically. "
            "Do NOT write body text for the hook.\n"
        )

    return f"""\
WORD LIMITS (two-field format — enforce strictly):

- Hook heading (Slide 1): max 8 words
- Content heading:         max 8 words  \u2190 the bold title shown large
- Content body (text):     max 20 words \u2190 the supporting explanation shown smaller
- CTA heading:             max 8 words
- CTA body (text):         max 12 words
{image_hook_note}
OUTPUT FORMAT (STRICT JSON — two fields per slide):

Each slide has TWO fields:
  "heading" \u2014 short, punchy title phrase (see word limits above)
  "text"    \u2014 supporting body sentence (empty string "" for hook slides)

{{
  "slides": [
    {{"type": "hook",    "heading": "Stop prompting Claude the **wrong** way",        "text": ""}},
    {{"type": "content", "heading": "Specific prompts work better",                    "text": "Because Claude needs clear instructions to respond accurately and completely"}},
    {{"type": "content", "heading": "Match the prompt to the task",                   "text": "Ask Claude to 'explain X in 3 steps with examples' — specificity shapes the output **directly**"}},
    {{"type": "content", "heading": "Add a role to every prompt",                      "text": "'Act as a teacher' changes every answer \u2014 Claude adjusts tone and depth **instantly**"}},
    {{"type": "cta",     "heading": "Follow @claudeinsights now",                     "text": "Get more Claude tips every week"}}
  ]
}}

The "slides" array MUST contain EXACTLY {num_slides} objects.
Both "heading" and "text" are required on every slide (use "" for empty text).
If your output does not meet ALL rules, regenerate internally until it does."""


def _build_system_prompt(num_slides: int, template_style: str = "text_only") -> str:
    """Return the generation system prompt with the exact slide count baked in.

    A hook style is chosen randomly at call time so every generation request
    produces a structurally different opening — preventing the repetitive
    "Claude is powerful — most people…" pattern.
    """
    content_count = num_slides - 2
    hook_name, hook_instruction, hook_example = random.choice(_HOOK_STYLES)
    carousel_arc = _build_carousel_arc(num_slides)

    return f"""\
You are an API that generates Instagram carousel slides about using Claude AI for beginners.

You MUST return ONLY valid JSON.
Do NOT include any text before or after the JSON.
Do NOT explain anything.
Do NOT use markdown or code blocks.

---

SLIDE COUNT (CRITICAL):

Generate EXACTLY {num_slides} slides. There are NO other slide count rules.
Do NOT default to 5 slides. Do NOT add extra slides.

Structure:
- Slide 1 = hook
- Slides 2 to {num_slides - 1} = content  ({content_count} content slide{"s" if content_count != 1 else ""})
- Slide {num_slides} = cta

---

HOOK STYLE — use this style for Slide 1:

Style: {hook_name}
Rule:  {hook_instruction}
e.g.   {hook_example}

REQUIREMENTS:
- MUST use a keyword or phrase directly from the topic — generic hooks are invalid
- Must be concise and scannable — target 4–8 words
- Do NOT end with a bare em-dash (—) or arrow (→)
- Do NOT start with "Claude" — the subject should reflect the topic
- Must create tension, curiosity, or contrast

BAD (generic — could apply to any topic):
  "Most people use Claude wrong"
GOOD (topic-specific — reader instantly recognises it's about their problem):
  "Stop building everything at once in Claude Code"

---

HEADING STYLE (if applicable):

HEADINGS MUST:
- Stand alone as a complete phrase
- Never end with words that require continuation (e.g. "with", "for", "a", "the")
- Read naturally if shown alone on a slide

HEADINGS MUST NOT start with transition words:
- Do NOT use: First, Then, Next, Now, Finally

Headings are standalone titles, not sentence transitions.

Transitions should appear in the body text ONLY.

HEADINGS MUST NOT use em dashes (—).
Use commas or split into clean phrases instead.

BAD: "Better prompts — better results"
GOOD: "Better prompts, better results"

---

CONTENT VARIETY (REQUIRED):

DEFAULT: Do NOT use the "Instead of X → Try Y" pattern by default.
Prefer direct explanations, insights, tips, and concrete examples.
Only use contrast when the slide is specifically about correcting a common mistake.

Content slides should include a mix of these styles:

  EXPLANATION  — state WHY something works, using "because" or a strong em-dash
    e.g. "Specific prompts work better — because Claude knows exactly what to do"

  TIP          — direct actionable advice starting with a verb (Add / Use / Ask)
    e.g. "Add your role upfront — 'Act as a teacher' changes every answer **instantly**"

  EXAMPLE      — a concrete prompt or use case shown in action
    e.g. "Ask Claude: 'Explain X in 3 steps with examples' — specificity shapes the output"

  OUTCOME      — the concrete result the reader gains, quantified or made tangible
    e.g. "Structured prompts cut editing time — answers arrive **ready** to use"

  INSIGHT      — a strong, standalone statement; no contrast needed
    e.g. "Modes define scope — scope defines output quality"

  CONTRAST     — use ONLY when the slide is about correcting a specific mistake
    e.g. 'Instead of "Summarise this" → Try "Summarise this in 5 bullet points"'
    Do NOT use this style unless fixing a mistake is the point of the slide.

Vary sentence openings — avoid starting every slide with "Claude".
Avoid repeating the same sentence structure more than once.

---

PROGRESSIVE FLOW — follow this exact arc:

{carousel_arc}

RULES FOR FLOW:
- Each slide must build on the previous one — "because of this → here's what to do next"
- Use transition words to signal progression:
    Slide 2: (no transition — state the core principle directly)
    Step slides:
        - Use transition words in the BODY text only (First…, Then…, Next…, Now…)
        - NEVER use transition words in headings
    Outcome slide: Finally…
- Do NOT write random, disconnected tips — every slide must earn the next one
- The real prompt example belongs in the middle of the carousel, not at the end

---

REAL PROMPT EXAMPLE (MANDATORY):

At least ONE content slide MUST include a concrete Claude prompt example.

PREFERRED format — prompt shown in context:
  "Ask Claude: 'Explain X in 3 steps with examples' — specificity shapes the output **directly**"
  "'Act as a teacher. Walk me through X step by step.' — Claude adjusts tone and depth **instantly**"

USE contrast formats ONLY when the slide is specifically about correcting a mistake:
  Instead of → Try:
     Instead of: "Explain this" → Try: "Explain this in 3 steps with examples"
  Bad → Better:
     Bad: "Summarise this" → Better: "Summarise this in 5 bullet points for a busy reader"

Do NOT default to contrast. If the topic is not about a common mistake,
show the prompt in action (preferred format above).

A short bare quote (1–3 words) with nothing around it is NOT valid.
A substantive quoted prompt (4+ words) shown with context IS valid without contrast.

---

DEPTH PER SLIDE:

Each content slide MUST include at least ONE of:
- a concrete example (a real Claude prompt shown in use)
- a short explanation using "because…" (the reason behind the advice)
- a concrete outcome (the specific result the reader gets)

Bad:  "Use better prompts"
Good: "Use structured prompts — because Claude needs clear instructions to respond **accurately**"

---

{_word_limits_section(template_style)}

COMPLETENESS:

HEADINGS ("heading" field):
- Should be concise and scannable — target 2–6 words, up to 8 maximum
- Sentence-like headings are fine if short and readable
- Do NOT end with a bare em-dash (—) or arrow (→)
- A phrase is valid: "Specific prompts work better" ✓

BODY TEXT ("text" field, or the full text in text_only):
- MUST be a complete, self-contained unit of meaning
- Must make sense when viewed in isolation — NOT as a continuation of the previous slide
- NEVER end with words that require a follow-up on the next slide:
  ✗ "Because"  ✗ "Instead of:"  ✗ "Instead"  ✗ "→ Try"  ✗ "Then"  ✗ "And"
- Do NOT start a slide body with "Because..." if the premise lives in the previous slide
- NEVER split a pattern across two slides:
  ✗ Split: Slide 3 = 'Instead of: "Explain this"'  +  Slide 4 = 'Try: "Explain this in 3 steps"'
  ✓ Same slide: 'Instead of: "Explain this" → Try: "Explain this in 3 steps with examples"'
- CONTRAST format MUST have BOTH sides written in full on the SAME slide:
  ✗ BAD:  Instead of: "Explain this" → Try
  ✓ GOOD: Instead of: "Explain this" → Try: "Explain this simply with examples"

---

CTA RULE (MANDATORY):

The final slide MUST include "@claudeinsights" and a clear action verb.

✓ VALID:   "Follow @claudeinsights for more Claude tips"
           "**Save** this — more tips at @claudeinsights"
           "Build smarter — follow @claudeinsights **now**"

✗ INVALID (missing handle): "Try this today" / "Save this and start now"

---

EMPHASIS (bold using **word**):

- 1–2 bold words per slide only
- Bold ONLY: outcomes (better, faster, clearer), contrasts (wrong, mistake), actions (specific, structured)
- NEVER bold filler words

---

SELF-CHECK (mandatory — do this before returning your output):

Review each slide independently. For every slide ask:
1. Does this slide make complete sense if shown alone, without the previous slide?
2. Does the body end on a complete thought? (not "because", "instead of", "and", "then")
3. If using "Instead of → Try" — are BOTH sides present on THIS slide?
4. Does the body start with "Because..." where the premise was stated in the previous slide?

If any slide fails → rewrite it to be self-contained before returning.

---

SELF-CORRECTION:

If you receive an error message with the topic, you MUST fix the specific issue.
Common errors:
- "Incorrect number of slides"         → regenerate EXACTLY {num_slides} slides
- "No actionable prompt example found" → add a slide with a concrete Claude prompt example (contrast format A/B, or a quoted prompt of 4+ words shown in context)
- "Slides lack depth"                  → add a because/insight slide AND a slide with a concrete prompt example
- "Truncated slide content"            → make the slide self-contained: complete the contrast on the same slide, or remove the dangling ending word
- "CTA slide is missing @claudeinsights" → add "@claudeinsights" to the final slide
- "Invalid JSON"                       → fix the JSON formatting
Do NOT repeat the same mistake.

---

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
    # NEVER truncate contrast slides — they must remain complete
    if "instead of" in text.lower() and "try" in text.lower():
        return text

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

    # Detect ANY incomplete contrast pattern
    if re.search(r'(→|->).*?try\s*:?\s*$', truncated.lower()):
        return text  # fallback

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


def _enforce_slide_limits(slides: list[dict], template_style: str = "text_only") -> list[dict]:
    """Ensure every slide's heading (and body) respects the word limit for its type."""
    limits = WORD_LIMITS[template_style]
    result = []
    for slide in slides:
        slide_type = slide["type"]
        heading    = slide.get("heading", "")
        body       = slide.get("body", "")

        if template_style == "text_only":
            max_h    = limits[slide_type]
            enforced = enforce_word_limit(heading, max_h)
            if not _is_valid_heading(enforced):
                logger.info("Reverting truncation (invalid heading): %r → %r", enforced, heading)
                enforced = heading
            if enforced != heading:
                logger.info(
                    "Truncated %s slide from %d→%d words: %r",
                    slide_type, len(heading.split()), max_h, heading,
                )
            result.append({**slide, "heading": enforced, "body": ""})
        else:
            # heading styles: enforce separate limits on heading and body
            if slide_type == "hook":
                max_h     = limits["hook_heading"]
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
    last_two  = " ".join(w.lower().rstrip('.,!?:"\'\u2018\u2019') for w in words[-2:]) if len(words) >= 2 else last_word

    # Bare arrow — contrast started, never resolved
    if last_word in ("→", "->"):
        return False

    # Lone article — truncation artefact
    if last_word in ("the", "a", "an"):
        return False

    # Trailing "because" — reason deferred to next slide
    if last_word == "because":
        return False

    # Trailing "instead of" or standalone "instead" — comparison deferred
    if last_two == "instead of" or last_word == "instead":
        return False

    # "→ Try:" at very end with nothing after the colon
    if re.search(r'[→>]\s*[Tt]ry\s*:\s*$', plain):
        return False

    # Contrast lead-in ("Instead of:") with no resolution on this slide.
    # Catches slides that open a contrast but put the "Try" half on the next slide.
    if re.search(r'(?i)^\s*instead\s+of\s*:', plain):
        if not re.search(r'(→|->|\btry\s*:)', plain, re.IGNORECASE):
            return False

    return True
def _validate_completeness(
    slides: list[dict],
    template_style: str = "text_only",
) -> list[dict]:
    """Auto-correct minor issues and hard-fail only on genuinely broken output.

    What this does:
      - Auto-compress headings >10 words (never a hard failure)
      - Hard-fail only when _is_complete_slide() returns False, which now only
        catches: empty text, bare arrow (arrow), lone article (a/an/the), or
        "arrow Try:" with no payload.

    What this does NOT do:
      - Reject fragments e.g. "Instead of: ..." or "First, match your task"
      - Reject text ending with "because", "instead", "then", "first", etc.
      - Enforce grammar or sentence structure

    Returns the (possibly auto-corrected) slides list.
    """
    is_heading_style = template_style in ("headings_and_text", "headings_text_image")
    corrected = []
    broken: list[str] = []

    for s in slides:
        heading = s.get("heading", "")
        body    = s.get("body", "")

        if is_heading_style:
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
        else:
            # text_only: heading IS the full text
            if not _is_complete_slide(heading):
                broken.append(f"[{s['type']}] {heading!r}")

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
# CTA handle validation
# ---------------------------------------------------------------------------

def _has_cta_handle(slides: list[dict]) -> bool:
    """Return True if the CTA slide contains @claudeinsights."""
    cta_slides = [s for s in slides if s["type"] == "cta"]
    if not cta_slides:
        return False
    return "@claudeinsights" in cta_slides[-1]["heading"]


# ---------------------------------------------------------------------------
# Depth validation — at least one example and one insight per carousel
# ---------------------------------------------------------------------------

# Markers for a concrete prompt example or use-case slide (no contrast required)
_EXAMPLE_MARKERS = ('"', "\u201c", "\u2018", "e.g.", "for example", "act as ", "ask claude")

# Markers for an insight/explanation slide
_INSIGHT_MARKERS = ("because", "—", " — ", "=", "≠", "means ", "so ", "which ")


def _has_depth(slides: list[dict]) -> bool:
    """Return True if the carousel contains at least one example slide AND
    one insight/explanation slide among the content slides.
    Checks both heading and body fields to support two-field heading styles."""
    has_example = False
    has_insight = False
    for slide in slides:
        if slide["type"] != "content":
            continue
        text_lower = (slide.get("heading", "") + " " + slide.get("body", "")).lower()
        if any(m in text_lower for m in _EXAMPLE_MARKERS):
            has_example = True
        if any(m in text_lower for m in _INSIGHT_MARKERS):
            has_insight = True
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
    if error_context:
        user_content += f"\n\nPREVIOUS ATTEMPT FAILED — fix this error:\n{error_context}"

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_build_system_prompt(num_slides, template_style),
        messages=[{"role": "user", "content": user_content}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Backend: OpenAI
# ---------------------------------------------------------------------------

def _generate_openai(
    topic: str,
    num_slides: int,
    error_context: str = "",
    template_style: str = "text_only",
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

        if template_style == "text_only":
            text_val = (s.get("text") or "").strip()
            if not text_val:
                raise ValueError(f"Slide {i} has empty 'text'")
            result.append({
                "type":    slide_type,
                "heading": text_val,
                "body":    "",
            })
        else:
            # heading styles: separate heading and body fields
            heading_val = (s.get("heading") or "").strip()
            body_val    = (s.get("text") or "").strip()

            if not heading_val:
                raise ValueError(f"Slide {i} has empty 'heading'")

            # Hook slides in headings_text_image have empty body by design
            # (the image occupies the lower half); all other slides need body
            if not body_val and not (
                slide_type == "hook"
                or (slide_type == "hook" and template_style == "headings_and_text")
            ):
                if slide_type != "hook":
                    logger.debug(
                        "Slide %d (%s) has empty body — acceptable for hook, warning for others",
                        i, slide_type,
                    )

            result.append({
                "type":    slide_type,
                "heading": heading_val,
                "body":    body_val,
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

def _build_review_prompt(template_style: str = "text_only") -> str:
    """Build a review prompt appropriate for the given template style."""
    if template_style == "text_only":
        return_format = """\
Return ONLY a JSON array in the same format as the input — no extra text:
[
  { "type": "hook",    "text": "..." },
  { "type": "content", "text": "..." },
  { "type": "cta",     "text": "..." }
]"""
        word_limits = "hook: max 8 words | content: max 15 words | cta: max 12 words"
    else:
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


def _review_anthropic(slides: list[dict], template_style: str = "text_only") -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_build_review_prompt(template_style),
        messages=[{"role": "user", "content": _slides_to_review_input(slides)}],
    )
    return msg.content[0].text.strip()


def _review_openai(slides: list[dict], template_style: str = "text_only") -> str:
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


def review_and_improve(slides: list[dict], template_style: str = "text_only") -> list[dict]:
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
    template_style: Optional[str] = None,
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
    if not (4 <= num_slides <= 10):
        raise ValueError(f"num_slides must be between 4 and 10, got {num_slides}")

    if template_style is None:
        template_style = "text_only"
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

    backend      = backends[provider]
    last_error: Optional[Exception] = None
    error_context: str              = ""

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Slide generation attempt %d/%d (num_slides=%d, style=%s)",
                attempt, max_retries, num_slides, template_style,
            )
            candidates = []
            for i in range(3):
                raw = backend(topic, num_slides, error_context, template_style)

                candidate_slides = _parse_json_slides(raw, num_slides, template_style)
                candidate_slides = _enforce_slide_limits(candidate_slides, template_style)
                candidate_slides = _enforce_bold_caps(candidate_slides)
                candidate_slides = _clean_heading_punctuation(candidate_slides)

                # Auto-compress any heading that is too long rather than
                # skipping the whole candidate.
                candidate_slides = [
                    {**s, "heading": _compress_heading(s["heading"])}
                    if len(s.get("heading", "").split()) > 10
                    else s
                    for s in candidate_slides
                ]

                score = _score_slides(candidate_slides)
                logger.info("Candidate %d score: %.2f", i + 1, score)

                candidates.append((score, candidate_slides))

            # Select best candidate
            best_score, slides = max(candidates, key=lambda x: x[0])

            logger.info("Best candidate score selected: %.2f", best_score)

            # --- Guard: detect truncated contrast (→ Try: with no payload) ---
            if any(
                re.search(r'(→|->)?\s*try\s*:?\s*$', (s["heading"] + " " + s.get("body", "")).lower())
                for s in slides
            ):
                raise ValueError("Truncated contrast detected (ends with 'Try:') — retrying")

            if best_score < QUALITY_THRESHOLD:
                raise ValueError(
                    f"Low quality slides (score={best_score:.2f} < {QUALITY_THRESHOLD}) — retrying"
                )

            hook_text = slides[0]["heading"]
            # For heading styles the hook is a short phrase, not a sentence —
            # only reject genuinely broken forms (trailing em-dash / bare arrow).
            # For text_only the full sentence check still applies.
            hook_is_heading_style = template_style in ("headings_and_text", "headings_text_image")
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
            if not _has_actionable_prompt_example(slides):
                raise ValueError(
                    "No actionable prompt example found. At least one content slide must include "
                    "a quoted Claude prompt AND a comparison (Instead of/Try/Bad/Better/→). "
                    "A quoted prompt alone is not enough — show why one phrasing is better."
                )
            if not _has_depth(slides):
                raise ValueError(
                    "Slides lack depth: need at least one example slide "
                    "(Instead of/Try/micro-example) AND one insight slide (because/—/=). "
                    "Retrying for more informative content."
                )
            slides = _validate_completeness(slides, template_style)  # auto-corrects headings; raises if body is broken
            if not _has_cta_handle(slides):
                raise ValueError(
                    "CTA slide is missing @claudeinsights. "
                    "The final slide MUST include '@claudeinsights'."
                )
            logger.info(
                "Generated %d slides (validated: completeness, CTA handle, prompt example, depth)",
                len(slides),
            )
            # Auto-compress any remaining oversized headings before review
            slides = [
                {**s, "heading": _compress_heading(s["heading"])}
                if not _is_valid_heading(s["heading"])
                else s
                for s in slides
            ]
            if best_score < 0.85:
                slides = review_and_improve(slides, template_style)
                slides = _clean_heading_punctuation(slides)
            caption = generate_caption(slides)
            if os.environ.get("DEBUG", "false").lower() == "true":
                caption += f"\n\n[DEBUG] template={template_style} | image_enabled={_IMAGE_ENABLED}"
            # caption = (
            #     f"{caption}\n\n"
            #     f"[DEBUG] template={template_style} | image_enabled={_IMAGE_ENABLED}"
            # )
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

    raise RuntimeError(
        f"Slide generation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    )
