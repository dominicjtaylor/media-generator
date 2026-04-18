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

LOCAL_IMAGE_DIR: Path = Path(__file__).parent / "assets" / "lummi_images"

_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Common words that add no signal for topic matching.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "of",
    "with", "how", "why", "what", "when", "your", "you", "is", "are",
    "use", "using", "make", "from", "about", "get", "tips", "best",
    "top", "guide", "claude", "ai",
})

# Generic tech/AI terms that make an image broadly suitable as a fallback.
# Any image whose filename contains at least one of these is preferred over
# a fully random pick when exact/fuzzy matching finds no strong candidate.
_BROAD_TECH_TERMS: frozenset[str] = frozenset({
    "tech", "computer", "digital", "data", "code", "coding",
    "robot", "abstract", "network", "software", "circuit",
    "screen", "terminal", "keyboard", "interface", "future",
    "innovation", "machine", "algorithm", "cloud",
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

def _fuzzy_score(topic_tokens: set[str], file_tokens: list[str]) -> float:
    """Score topic-to-filename relevance using fuzzy matching.

    For each (topic_token, file_token) pair award:
      1.0  — exact match
      0.6  — one is a substring of the other (min length 4 to avoid noise,
             e.g. "code" in "coding")
      0.4  — shared prefix of ≥5 chars (catches stem variants like
             "computing" / "computer" → common prefix "comput")
    Returns the sum over all topic tokens of the best match found.
    """
    score = 0.0
    for t in topic_tokens:
        best = 0.0
        for f in file_tokens:
            if t == f:
                best = 1.0
                break                           # can't do better
            if len(t) >= 4 and len(f) >= 4:
                if t in f or f in t:
                    best = max(best, 0.6)
                elif len(t) >= 5 and len(f) >= 5:
                    # shared prefix length
                    pfx = next(
                        (i for i, (a, b) in enumerate(zip(t, f)) if a != b),
                        min(len(t), len(f)),
                    )
                    if pfx >= 5:
                        best = max(best, 0.4)
        score += best
    return score


def select_relevant_image(topic: str) -> dict:
    """Return the most topically relevant image from LOCAL_IMAGE_DIR.

    Selection uses a tiered strategy:

    1. Fuzzy match — score every image by substring overlap between its
       filename tokens and the topic tokens; pick the highest scorer.
    2. Category bias — if no image scores above zero, prefer images whose
       filenames contain a broad tech/AI term (_BROAD_TECH_TERMS) and
       pick randomly among the best-matching category images.
    3. Random fallback — if no category match exists, pick any image.

    Returns:
        {
            "file_path": str,    # path suitable for open() / shutil.copy2()
            "filename":  str,    # e.g. "Retro Blue Computer.png"
            "keywords":  list,   # tokens extracted from the filename stem
        }
    """
    candidates = _image_candidates()
    if not candidates:
        raise FileNotFoundError(f"No images found in {LOCAL_IMAGE_DIR}")

    topic_tokens = set(_tokenize(topic))

    # --- Tier 1: fuzzy scoring ---
    scored: list[tuple[float, Path]] = []
    for path in candidates:
        stem_text  = path.stem.replace("-", " ").replace("_", " ")
        file_tokens = _tokenize(stem_text)
        score = _fuzzy_score(topic_tokens, file_tokens)
        scored.append((score, path))

    best_score = max(s for s, _ in scored)

    if best_score > 0:
        # All images that share the top score compete equally — pick randomly.
        top_tier = [p for s, p in scored if s == best_score]
        chosen   = random.choice(top_tier)
        logger.info(
            "Selected image %r (fuzzy_score=%.2f, pool=%d) for topic %r",
            chosen.name, best_score, len(top_tier), topic,
        )
        tier = "fuzzy"

    else:
        # --- Tier 2: category bias ---
        category_matches = [
            path for path in candidates
            if any(
                term in path.stem.lower().replace("-", " ").replace("_", " ")
                for term in _BROAD_TECH_TERMS
            )
        ]
        if category_matches:
            chosen = random.choice(category_matches)
            logger.info(
                "No fuzzy match for topic %r — using category image %r (%d candidates)",
                topic, chosen.name, len(category_matches),
            )
            tier = "category"
        else:
            # --- Tier 3: random fallback ---
            chosen = random.choice(candidates)
            logger.info(
                "No match for topic %r — using random image %r",
                topic, chosen.name,
            )
            tier = "random"

    stem_text = chosen.stem.replace("-", " ").replace("_", " ")
    logger.debug("Image selection tier=%r file=%r", tier, chosen.name)
    return {
        "file_path": str(chosen),
        "filename":  chosen.name,
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

def get_image_for_heading_template(topic: str, image_filename: Optional[str] = None) -> dict:
    """Select a local image and fetch its Lummi credit attribution.

    If image_filename is provided the fuzzy-scoring logic is skipped entirely
    and that file is used directly — this is the path taken when the user has
    made an explicit selection in the image picker.

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
    if image_filename:
        logger.info("Using user-selected image: %s", image_filename)
        img_path = (LOCAL_IMAGE_DIR / image_filename).resolve()
        if not img_path.exists():
            logger.error(
                "Selected image not found on disk — path=%s  "
                "(LOCAL_IMAGE_DIR=%s, filename=%r)",
                img_path, LOCAL_IMAGE_DIR, image_filename,
            )
            raise FileNotFoundError(f"Selected image not found: {img_path}")
        return {
            "local_path":  str(img_path),
            "author_name": "",
            "author_url":  "",
            "image_page":  "",
            "focal_x":     0.5,
            "focal_y":     0.5,
        }

    image_info = select_relevant_image(topic)
    logger.info("No user selection — auto-selected image: %s", image_info["filename"])
    credit = fetch_lummi_credit(image_info["filename"])

    return {
        "local_path":  str(Path(image_info["file_path"]).resolve()),
        "author_name": credit.get("designer_name") or "",
        "author_url":  credit.get("designer_url") or credit.get("image_url") or "",
        "image_page":  credit.get("image_url") or "",
        "focal_x":     0.5,
        "focal_y":     0.5,
    }
