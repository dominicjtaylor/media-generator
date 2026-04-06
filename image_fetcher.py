"""
image_fetcher.py — Lummi API image fetcher for the headings_text_image template style.

Pipeline
--------
  1. _build_visual_query(topic)   → 2–4 visual keywords
  2. _search_lummi(query, key)    → photo metadata dict (id, author, focal, …)
  3. _get_download_url(id, key)   → signed downloadUrl from POST /download
  4. _download_image(url)         → bytes written to temp file
  5. fetch_lummi_image(topic)     → structured result dict

Public API
----------
fetch_lummi_image(topic: str) -> dict
    Returns {local_path, author_name, author_url, image_page, width, height, focal_x, focal_y}
    Raises RuntimeError on any failure — caller decides fallback behaviour.
"""

import json
import logging
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger("carousel.image_fetcher")

_LUMMI_BASE        = "https://api.lummi.ai/v1"
_LUMMI_SEARCH_URL  = f"{_LUMMI_BASE}/images/search"
_LUMMI_DL_TEMPLATE = f"{_LUMMI_BASE}/images/{{image_id}}/download"

_VISUAL_QUERY_SYSTEM = """\
You convert Instagram carousel topics into short, visual image search queries.

Rules:
- Return ONLY 2-4 keywords — nothing else, no punctuation, no explanation
- Describe something visually representable (objects, scenes, styles)
- Prefer: laptop, robot, server, workspace, desk, neon, futuristic, dark office
- Avoid: abstract ideas, jargon, adjectives like "efficient" or "powerful"

Examples:
  Topic: "Why context matters in Claude Code" → programming laptop dark
  Topic: "Automate your workflow"             → coding automation setup
  Topic: "AI replacing jobs"                 → robot computer office
  Topic: "Better prompting for Claude"       → typing keyboard workspace
  Topic: "Build faster with AI tools"        → developer screen setup"""


# ---------------------------------------------------------------------------
# Step 1: Visual query generation
# ---------------------------------------------------------------------------

# Visual anchor words guaranteed to return results on any stock-photo API.
# Used in the fallback query so even a failed LLM call produces a searchable result.
_VISUAL_ANCHORS = ["coding", "laptop", "workspace", "computer"]

# Stop-words stripped when building a fallback query from the raw topic.
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "up",
    "about", "into", "through", "during", "before", "after", "above",
    "below", "and", "but", "or", "nor", "so", "yet", "both", "either",
    "neither", "not", "why", "how", "what", "when", "where", "which",
    "who", "that", "this", "these", "those", "your", "you", "my", "we",
    "our", "their", "it", "its", "more", "most", "very", "just", "also",
})


def _visual_fallback_query(topic: str) -> str:
    """Build a guaranteed-visual fallback query without an LLM.

    Takes up to 2 meaningful words from the topic (skipping stop-words)
    and appends 2 visual anchors so the query always describes something
    photographable even when the topic is abstract.
    """
    words = [w.lower().strip(".,!?\"'()") for w in topic.split()]
    meaningful = [w for w in words if w and w not in _STOP_WORDS][:2]
    anchors = _VISUAL_ANCHORS[:2]
    return " ".join(meaningful + anchors) if meaningful else " ".join(anchors)


def _build_visual_query(topic: str) -> str:
    """Convert a carousel topic into a short, visual image search query.

    Uses the configured LLM (Anthropic by default, OpenAI fallback).
    Falls back to a guaranteed-visual query constructed from the topic
    plus hardcoded visual anchors — never falls back to raw abstract text.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

    try:
        if provider == "anthropic":
            return _visual_query_anthropic(topic)
        else:
            return _visual_query_openai(topic)
    except Exception as exc:
        fallback = _visual_fallback_query(topic)
        logger.warning(
            "Visual query generation failed (%s) — using fallback: %r", exc, fallback
        )
        return fallback


def _visual_query_anthropic(topic: str) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",   # cheapest model — simple classification task
        max_tokens=20,
        system=_VISUAL_QUERY_SYSTEM,
        messages=[{"role": "user", "content": f"Topic: {topic}"}],
    )
    return msg.content[0].text.strip()


def _visual_query_openai(topic: str) -> str:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=20,
        messages=[
            {"role": "system", "content": _VISUAL_QUERY_SYSTEM},
            {"role": "user",   "content": f"Topic: {topic}"},
        ],
    )
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Step 2: Lummi search
# ---------------------------------------------------------------------------

def _lummi_request(url: str, api_key: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    """Make an authenticated request to the Lummi API and return parsed JSON."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Lummi API {method} {url} returned HTTP {exc.code}: {exc.reason} — {body_text}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Lummi API request failed ({method} {url}): {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Lummi API returned non-JSON ({method} {url}): {raw[:200]}"
        ) from exc


def _score_photo(photo: dict) -> float:
    """Score a Lummi photo object for suitability as a carousel background.

    Higher is better.  Criteria:
    - Aspect ratio 1.2–2.0 (portrait-ish, fills the image box without dead space): +2
    - Focal point near centre in both axes (0.3–0.7): +2
    - Resolution: multiplied by min(width, height) / 1000 for a continuous bonus
    """
    width  = int(photo.get("width")  or 0)
    height = int(photo.get("height") or 0)
    focal_x = float(photo.get("focalPositionX") or 0.5)
    focal_y = float(photo.get("focalPositionY") or 0.5)

    score = 0.0

    # Aspect ratio bonus
    if height > 0:
        ratio = width / height
        if 1.2 <= ratio <= 2.0:
            score += 2.0

    # Focal point bonus — prefer subjects roughly centred
    if 0.3 <= focal_x <= 0.7 and 0.3 <= focal_y <= 0.7:
        score += 2.0

    # Resolution multiplier (higher res = better quality crop)
    if width > 0 and height > 0:
        score *= (min(width, height) / 1000.0)

    return score


def _search_lummi(visual_query: str, api_key: str) -> dict:
    """Search Lummi for *visual_query* and return the best-scoring photo.

    Fetches up to 10 results and scores each on aspect ratio, focal point,
    and resolution.  Returns the highest-scoring candidate.

    Raises RuntimeError if no results are found.
    """
    params = urllib.parse.urlencode({
        "query":     visual_query,
        "perPage":   10,
        "imageType": "photo",
    })
    url  = f"{_LUMMI_SEARCH_URL}?{params}"
    data = _lummi_request(url, api_key)

    results = data.get("data") or []
    if not results:
        raise RuntimeError(
            f"Lummi search returned no results for visual query: {visual_query!r}"
        )

    best  = max(results, key=_score_photo)
    score = _score_photo(best)
    logger.debug(
        "Selected photo id=%s (score=%.2f) from %d results for query %r",
        best.get("id"), score, len(results), visual_query,
    )
    return best


# ---------------------------------------------------------------------------
# Step 3: Download URL via POST /download
# ---------------------------------------------------------------------------

def _get_download_url(image_id: str, api_key: str) -> str:
    """POST to the Lummi download endpoint and return the signed download URL.

    The search API's "url" field is NOT the download URL — this endpoint
    returns a short-lived signed URL intended for direct download.
    """
    url  = _LUMMI_DL_TEMPLATE.format(image_id=image_id)
    data = _lummi_request(url, api_key, method="POST", body={})

    download_url = data.get("downloadUrl") or data.get("url") or ""
    if not download_url:
        raise RuntimeError(
            f"Lummi download endpoint returned no downloadUrl for image {image_id!r}. "
            f"Response keys: {list(data.keys())}"
        )

    return download_url


# ---------------------------------------------------------------------------
# Step 4: Download image bytes
# ---------------------------------------------------------------------------

_MIN_IMAGE_BYTES = 5 * 1024   # 5 KB — anything smaller is corrupt/placeholder


def _download_image(download_url: str) -> bytes:
    """Perform a plain GET to *download_url* and return the raw bytes.

    Raises RuntimeError if the download fails or the file is suspiciously
    small (< 5 KB), which indicates a corrupt or placeholder response.
    """
    req = urllib.request.Request(
        download_url,
        headers={"User-Agent": "carousel-generator/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            img_bytes = resp.read()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download image from {download_url}: {exc}"
        ) from exc

    if len(img_bytes) < _MIN_IMAGE_BYTES:
        raise RuntimeError(
            f"Downloaded image is suspiciously small ({len(img_bytes)} bytes < "
            f"{_MIN_IMAGE_BYTES} bytes minimum) — treating as corrupt."
        )

    return img_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_lummi_image(topic: str) -> dict:
    """Fetch a visually relevant image for *topic* from Lummi.

    Pipeline
    --------
    1. Convert *topic* to a 2–4 word visual search query via LLM.
    2. Search Lummi for the query and extract the first result.
    3. POST to /download to get a signed downloadUrl.
    4. Download the image and write it to a named temp file.
    5. Return structured metadata including focal point for CSS rendering.

    Parameters
    ----------
    topic : str   The carousel topic (e.g. "Why context matters in Claude Code").

    Returns
    -------
    dict with keys:
        local_path  : str   Absolute path to the downloaded image file.
        author_name : str   Photographer display name.
        author_url  : str   Photographer attribution URL (Lummi profile page).
        image_page  : str   Attribution URL for the image page on Lummi.
        width       : int   Original image width in pixels.
        height      : int   Original image height in pixels.
        focal_x     : float Horizontal focal point 0.0–1.0 (default 0.5 = centre).
        focal_y     : float Vertical focal point 0.0–1.0 (default 0.5 = centre).

    Raises
    ------
    RuntimeError   On any failure — caller decides fallback behaviour.
    """
    api_key = os.environ.get("LUMMI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LUMMI_API_KEY not set in environment. "
            "Add LUMMI_API_KEY=... to your .env file."
        )

    # -------------------------------------------------------------------------
    # Step 1: Build visual search query
    # -------------------------------------------------------------------------
    visual_query = _build_visual_query(topic)
    logger.info("Topic %r → visual query: %r", topic, visual_query)

    # -------------------------------------------------------------------------
    # Step 2: Search Lummi
    # -------------------------------------------------------------------------
    photo = _search_lummi(visual_query, api_key)

    image_id   = photo.get("id") or ""
    author_obj = photo.get("author") or {}
    author_name = author_obj.get("name") or "Lummi"
    author_url  = author_obj.get("attributionUrl") or "https://lummi.ai"
    image_page  = photo.get("attributionUrl") or "https://lummi.ai"
    width       = int(photo.get("width")  or 0)
    height      = int(photo.get("height") or 0)
    focal_x     = float(photo.get("focalPositionX") or 0.5)
    focal_y     = float(photo.get("focalPositionY") or 0.5)

    if not image_id:
        raise RuntimeError("Lummi search result has no 'id' field")

    logger.debug(
        "Search result — id: %s | author: %s | size: %dx%d | focal: (%.2f, %.2f)",
        image_id, author_name, width, height, focal_x, focal_y,
    )

    # -------------------------------------------------------------------------
    # Step 3: Get signed download URL via POST /download
    # -------------------------------------------------------------------------
    download_url = _get_download_url(image_id, api_key)
    logger.debug("Download URL obtained for image %s", image_id)

    # -------------------------------------------------------------------------
    # Step 4: Download image bytes
    # -------------------------------------------------------------------------
    img_bytes = _download_image(download_url)

    # -------------------------------------------------------------------------
    # Step 5: Save to named temp file
    # -------------------------------------------------------------------------
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(img_bytes)
        tmp.flush()
        local_path = tmp.name
    finally:
        tmp.close()

    logger.info(
        "Lummi image saved → %s (%d bytes | author: %s | focal: %.2f, %.2f)",
        local_path, len(img_bytes), author_name, focal_x, focal_y,
    )

    return {
        "local_path":  local_path,
        "author_name": author_name,
        "author_url":  author_url,
        "image_page":  image_page,
        "width":       width,
        "height":      height,
        "focal_x":     focal_x,
        "focal_y":     focal_y,
    }
