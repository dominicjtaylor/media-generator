"""
image_fetcher.py — Lummi API image fetcher for the headings_text_image template style.

Public API
----------
fetch_lummi_image(topic: str) -> dict
    Returns {"url": str, "local_path": str, "author_name": str, "author_url": str}
    Raises RuntimeError on failure — caller should catch and fall back.
"""

import json
import logging
import os
import tempfile
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

logger = logging.getLogger("carousel.image_fetcher")

_LUMMI_SEARCH_URL = "https://api.lummi.ai/v1/images/search"


def fetch_lummi_image(topic: str) -> dict:
    """Search Lummi for a photo matching *topic* and download the first result.

    Parameters
    ----------
    topic : str   The carousel topic — used as the image search query.

    Returns
    -------
    dict with keys:
        url         : str   Original Lummi CDN URL
        local_path  : str   Absolute path to the downloaded temp file (JPEG)
        author_name : str   Photographer display name
        author_url  : str   Photographer attribution URL (profile on Lummi)

    Raises
    ------
    RuntimeError   If the API key is missing, the search returns no results,
                   or the download fails.
    """
    api_key = os.environ.get("LUMMI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LUMMI_API_KEY not set in environment. "
            "Add LUMMI_API_KEY=... to your .env file."
        )

    # -------------------------------------------------------------------------
    # Step 1: Search for a relevant image
    # -------------------------------------------------------------------------
    params = urllib.parse.urlencode({
        "query":       topic,
        "perPage":     1,
        "imageType":   "photo",
        "orientation": "vertical",   # Instagram portrait format
    })
    search_url = f"{_LUMMI_SEARCH_URL}?{params}"
    logger.info("Fetching Lummi image for topic: %r", topic)

    req = urllib.request.Request(
        search_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept":        "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Lummi search API returned HTTP {exc.code}: {exc.reason}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Lummi search request failed: {exc}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Lummi API returned non-JSON response: {body[:200]}") from exc

    results = data.get("data") or []
    if not results:
        raise RuntimeError(
            f"Lummi search returned no results for query: {topic!r}"
        )

    photo = results[0]

    # -------------------------------------------------------------------------
    # Step 2: Extract metadata
    # -------------------------------------------------------------------------
    image_url   = photo.get("url") or ""
    author_obj  = photo.get("author") or {}
    author_name = author_obj.get("name") or "Lummi"
    author_url  = author_obj.get("attributionUrl") or photo.get("attributionUrl") or "https://lummi.ai"

    if not image_url:
        raise RuntimeError("Lummi photo object has no 'url' field")

    logger.debug("Lummi image URL: %s | author: %s", image_url, author_name)

    # -------------------------------------------------------------------------
    # Step 3: Download the image to a temp file
    # -------------------------------------------------------------------------
    try:
        img_req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "carousel-generator/1.0"},
        )
        with urllib.request.urlopen(img_req, timeout=30) as img_resp:
            img_bytes = img_resp.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to download Lummi image from {image_url}: {exc}") from exc

    # Write to a named temp file that persists until the caller is done with it
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(img_bytes)
        tmp.flush()
        local_path = tmp.name
    finally:
        tmp.close()

    logger.info(
        "Downloaded Lummi image → %s (%d bytes, author: %s)",
        local_path, len(img_bytes), author_name,
    )

    return {
        "url":         image_url,
        "local_path":  local_path,
        "author_name": author_name,
        "author_url":  author_url,
    }
