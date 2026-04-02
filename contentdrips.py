"""
contentdrips.py — Contentdrips API integration.

Handles:
  1. Formatting slides into the Contentdrips carousel payload
  2. Sending the render request
  3. Polling until the job completes and returning the export URL

Environment variables required:
  CONTENTDRIPS_API_KEY      — Bearer token (required)
  CONTENTDRIPS_TEMPLATE_ID  — Template to render with (required)

Optional:
  CONTENTDRIPS_API_BASE     — Override base URL (default: https://generate.contentdrips.com)
"""

import logging
import os

import httpx

logger = logging.getLogger("carousel.contentdrips")

# ---------------------------------------------------------------------------
# Startup environment diagnostic — runs once at import time.
# Helps confirm Railway is actually injecting the expected variables.
# ---------------------------------------------------------------------------

def _log_env_diagnostic() -> None:
    env_name = os.environ.get("ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT") or "unknown"
    logger.info("Running in environment: %s", env_name)

    key_raw = os.environ.get("CONTENTDRIPS_API_KEY")      # no .strip() yet — want raw value
    tid_raw = os.environ.get("CONTENTDRIPS_TEMPLATE_ID")

    if key_raw is None:
        logger.error("CONTENTDRIPS_API_KEY — NOT FOUND in environment")
        logger.info("All env keys present: %s", sorted(os.environ.keys()))
    else:
        key_stripped = key_raw.strip()
        logger.info(
            "CONTENTDRIPS_API_KEY — exists: True | raw length: %d | stripped length: %d | prefix: %s",
            len(key_raw),
            len(key_stripped),
            key_stripped[:7] + "…" if len(key_stripped) >= 7 else "(too short)",
        )
        if len(key_raw) != len(key_stripped):
            logger.warning(
                "CONTENTDRIPS_API_KEY has leading/trailing whitespace! "
                "Raw len=%d, stripped len=%d. This will be stripped before use.",
                len(key_raw), len(key_stripped),
            )

    if tid_raw is None:
        logger.error("CONTENTDRIPS_TEMPLATE_ID — NOT FOUND in environment")
    else:
        logger.info(
            "CONTENTDRIPS_TEMPLATE_ID — exists: True | value: %s", tid_raw.strip()
        )

_log_env_diagnostic()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _base() -> str:
    return os.environ.get("CONTENTDRIPS_API_BASE", "https://generate.contentdrips.com").rstrip("/")

_RENDER_PATH = "/render"


def _api_key() -> str:
    raw = os.environ.get("CONTENTDRIPS_API_KEY")
    if raw is None:
        raise RuntimeError("CONTENTDRIPS_API_KEY not found in environment")
    key = raw.strip()
    if not key:
        raise RuntimeError("CONTENTDRIPS_API_KEY is set but empty")
    return key


def _template_id() -> str:
    raw = os.environ.get("CONTENTDRIPS_TEMPLATE_ID")
    if raw is None:
        raise RuntimeError("CONTENTDRIPS_TEMPLATE_ID not found in environment")
    tid = raw.strip()
    if not tid:
        raise RuntimeError("CONTENTDRIPS_TEMPLATE_ID is set but empty")
    return tid


def _headers() -> dict:
    key = _api_key()
    masked = key[:5] + ("*" * max(0, len(key) - 5))
    auth_value = f"Bearer {key}"
    logger.info(
        "Authorization header — format: 'Bearer <token>' | masked: 'Bearer %s' | total length: %d",
        masked,
        len(auth_value),
    )
    return {
        "Authorization": auth_value,
        "Content-Type":  "application/json",
    }


# ---------------------------------------------------------------------------
# Step 1: Format slides → Contentdrips payload
# ---------------------------------------------------------------------------

def format_for_contentdrips(slides: list[dict]) -> dict:
    """
    Map a flat slides list to the Contentdrips carousel structure:
      first  → intro_slide
      middle → slides[]
      last   → ending_slide
    """
    if not slides:
        raise ValueError("Cannot format an empty slides list")

    def _slide(s: dict) -> dict:
        return {
            "heading":     s.get("heading", "").strip(),
            "description": s.get("description", "").strip(),
        }

    if len(slides) == 1:
        intro, middle, ending = _slide(slides[0]), [], _slide(slides[0])
    elif len(slides) == 2:
        intro, middle, ending = _slide(slides[0]), [], _slide(slides[1])
    else:
        intro  = _slide(slides[0])
        middle = [_slide(s) for s in slides[1:-1]]
        ending = _slide(slides[-1])

    return {"intro_slide": intro, "slides": middle, "ending_slide": ending}


# ---------------------------------------------------------------------------
# Step 2: Submit render request → export_url
# ---------------------------------------------------------------------------

def request_render(carousel_payload: dict) -> tuple[str, dict]:
    """
    POST the render request to Contentdrips and return the image URL directly.

    Contentdrips renders synchronously — the export URL is in the response body.
    No polling required.

    Returns (export_url, raw_response).
    Raises RuntimeError with full context on any failure.
    """
    url  = f"{_base()}{_RENDER_PATH}"
    body = {
        "template_id": _template_id(),
        "output":      "png",
        **carousel_payload,
    }

    headers = _headers()

    logger.info("─── Contentdrips request ───────────────────────────────")
    logger.info("POST %s", url)
    logger.info("Payload: %s", body)

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=60)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error reaching Contentdrips ({url}): {exc}") from exc

    logger.info("─── Contentdrips response ──────────────────────────────")
    logger.info("Status:           %s", resp.status_code)
    logger.info("Response headers: %s", dict(resp.headers))
    logger.info("Response body:    %s", resp.text[:2000])

    # ── Auth failure detection ────────────────────────────────────────────
    if "token not found" in resp.text.lower() or resp.status_code == 403:
        logger.error(
            "Auth failed — token not received by API. "
            "Status: %s | Body: %s | "
            "Check CONTENTDRIPS_API_KEY in Railway environment variables.",
            resp.status_code, resp.text,
        )

    # ── HTML guard ────────────────────────────────────────────────────────
    content_type = resp.headers.get("content-type", "")
    if "html" in content_type or resp.text.lstrip().startswith("<"):
        raise RuntimeError(
            f"Contentdrips returned HTML instead of JSON (wrong endpoint or auth wall). "
            f"URL: {url} | Status: {resp.status_code}"
        )

    if not resp.is_success:
        raise RuntimeError(
            f"Contentdrips render request failed [{resp.status_code}]: {resp.text}"
        )

    data = resp.json()
    logger.info("Response keys: %s", list(data.keys()))

    # ── Extract image URL from response ──────────────────────────────────
    export_url = (
        data.get("export_url")
        or data.get("url")
        or data.get("download_url")
        or data.get("image_url")
        or data.get("file_url")
    )

    if export_url:
        logger.info("Export URL: %s", export_url)
        return str(export_url), data

    # job_id-only response — no polling endpoint exists
    if data.get("job_id") or data.get("id"):
        raise RuntimeError(
            f"Contentdrips returned a job_id with no polling endpoint available. "
            f"Full response: {data}"
        )

    raise RuntimeError(
        f"Contentdrips response contained no image URL. "
        f"Keys present: {list(data.keys())} | Full response: {data}"
    )
