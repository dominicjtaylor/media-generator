"""local_image.py — Local image fallback for the headings_text_image template.

Used when the Lummi API key is unavailable.  Images are sourced from
LOCAL_IMAGE_DIR; credit attribution is scraped lightly from Lummi search.

Swap point — in main.py / app.py:

    if os.getenv("LUMMI_API_KEY"):
        from image_fetcher import fetch_lummi_image
        image_data = fetch_lummi_image(topic)
    else:
        from local_image import get_image_for_heading_template
        image_data = get_image_for_heading_template(topic)
"""

import logging
import random
import re
import string
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("carousel.local_image")

LOCAL_IMAGE_DIR: Path = Path("assets/lummi_images")

_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Common words that add no signal for topic matching.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "of",
    "with", "how", "why", "what", "when", "your", "you", "is", "are",
    "use", "using", "make", "from", "about", "get", "tips", "best",
    "top", "guide", "claude", "ai",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split, and remove stopwords."""
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return [w for w in text.split() if w not in _STOPWORDS and len(w) > 2]


def _image_candidates() -> list[Path]:
    """Return all image files inside LOCAL_IMAGE_DIR."""
    if not LOCAL_IMAGE_DIR.is_dir():
        raise FileNotFoundError(
            f"Local image directory not found: {LOCAL_IMAGE_DIR}. "
            "Create it and add at least one image."
        )
    return [p for p in LOCAL_IMAGE_DIR.iterdir() if p.suffix.lower() in _IMAGE_EXTENSIONS]


# ---------------------------------------------------------------------------
# Public selection function
# ---------------------------------------------------------------------------

def select_relevant_image(topic: str) -> dict:
    """Return the most topically relevant image from LOCAL_IMAGE_DIR.

    Scores each candidate by keyword overlap between its filename
    (words separated by hyphens/underscores) and the topic text.
    Falls back to a random image when no overlap is found.

    Returns:
        {
            "file_path": str,    # path suitable for open() / shutil.copy2()
            "filename":  str,    # e.g. "ai-productivity-focus.jpg"
            "keywords":  list,   # tokens extracted from the filename stem
        }
    """
    candidates = _image_candidates()
    if not candidates:
        raise FileNotFoundError(f"No images found in {LOCAL_IMAGE_DIR}")

    topic_tokens = set(_tokenize(topic))

    best: Optional[Path] = None
    best_score = -1

    for path in candidates:
        stem_text = path.stem.replace("-", " ").replace("_", " ")
        file_tokens = set(_tokenize(stem_text))
        score = len(topic_tokens & file_tokens)
        if score > best_score:
            best_score = score
            best = path

    if best_score == 0:
        best = random.choice(candidates)
        logger.info(
            "No keyword overlap for topic %r — using random image %r",
            topic, best.name,
        )
    else:
        logger.info(
            "Selected image %r (overlap_score=%d) for topic %r",
            best.name, best_score, topic,
        )

    stem_text = best.stem.replace("-", " ").replace("_", " ")
    return {
        "file_path": str(best),
        "filename":  best.name,
        "keywords":  _tokenize(stem_text),
    }


# ---------------------------------------------------------------------------
# Lummi credit scraping
# ---------------------------------------------------------------------------

def fetch_lummi_credit(filename: str) -> dict:
    """Search Lummi for attribution info matching the given filename.

    Converts the filename stem to a search query, fetches the first
    result page, and attempts to extract designer name + URL.

    Note: Lummi is a JavaScript SPA; server-side rendering coverage varies.
    This function degrades gracefully — it never raises and returns None
    values if scraping is unsuccessful.

    Returns:
        {
            "designer_name": str | None,
            "designer_url":  str | None,
            "image_url":     str | None,
        }
    """
    stem = Path(filename).stem
    query = stem.replace("-", " ").replace("_", " ")
    result: dict = {"designer_name": None, "designer_url": None, "image_url": None}

    try:
        search_url = (
            "https://www.lummi.ai/search?q="
            + requests.utils.quote(query, safe="")
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = requests.get(search_url, headers=headers, timeout=8)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Attempt 1: anchor whose href looks like an image detail page.
        image_anchor = soup.find(
            "a", href=re.compile(r"/(photo|image|asset|illustration)/", re.I)
        )
        if image_anchor:
            href = image_anchor.get("href", "")
            result["image_url"] = (
                href if href.startswith("http") else "https://www.lummi.ai" + href
            )

            # Attempt to find a designer name close to the anchor.
            name_tag = image_anchor.find(
                ["span", "p", "div"],
                class_=re.compile(r"author|creator|designer|artist|user", re.I),
            )
            if not name_tag:
                # Walk up one level and search siblings.
                parent = image_anchor.parent
                if parent:
                    name_tag = parent.find(
                        ["span", "p", "div"],
                        class_=re.compile(r"author|creator|designer|artist|user", re.I),
                    )

            if name_tag:
                name_text = name_tag.get_text(strip=True)
                if name_text:
                    result["designer_name"] = name_text

            # Try to extract designer profile URL from a link near the image.
            profile_anchor = (image_anchor.parent or image_anchor).find(
                "a", href=re.compile(r"/(u|user|profile)/", re.I)
            )
            if profile_anchor:
                p_href = profile_anchor.get("href", "")
                result["designer_url"] = (
                    p_href
                    if p_href.startswith("http")
                    else "https://www.lummi.ai" + p_href
                )

        logger.info("Lummi credit for %r → %s", filename, result)

    except requests.exceptions.RequestException as exc:
        logger.warning("Network error in fetch_lummi_credit(%r): %s", filename, exc)
    except Exception as exc:
        logger.warning("Unexpected error in fetch_lummi_credit(%r): %s", filename, exc)

    return result


# ---------------------------------------------------------------------------
# Unified interface — swap this with fetch_lummi_image() when API is ready
# ---------------------------------------------------------------------------

def get_image_for_heading_template(topic: str) -> dict:
    """Select a local image and fetch its Lummi credit attribution.

    This is the single public interface for local image handling.
    Returns a dict that satisfies the renderer's image_data contract
    so it is a drop-in replacement for fetch_lummi_image().

    Returns:
        {
            "local_path":   str,    # absolute path; renderer copies this to image.png
            "author_name":  str,    # credit display name (empty string if unknown)
            "author_url":   str,    # credit link (empty string if unknown)
            "image_page":   str,    # Lummi image page URL (empty string if unknown)
            "focal_x":      float,  # horizontal focal point (0–1), default 0.5
            "focal_y":      float,  # vertical focal point   (0–1), default 0.5
        }
    """
    image_info = select_relevant_image(topic)
    credit = fetch_lummi_credit(image_info["filename"])

    return {
        "local_path":  str(Path(image_info["file_path"]).resolve()),
        "author_name": credit.get("designer_name") or "",
        "author_url":  credit.get("designer_url") or credit.get("image_url") or "",
        "image_page":  credit.get("image_url") or "",
        "focal_x":     0.5,
        "focal_y":     0.5,
    }
