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
import time

import httpx

logger = logging.getLogger("carousel.contentdrips")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _base() -> str:
    return os.environ.get("CONTENTDRIPS_API_BASE", "https://generate.contentdrips.com").rstrip("/")

# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Endpoint paths — verified against Contentdrips docs.                   │
# │  Override base via CONTENTDRIPS_API_BASE env var if needed.             │
# └─────────────────────────────────────────────────────────────────────────┘
_RENDER_PATH = "/render"
_STATUS_PATH = "/render/{job_id}"


def _api_key() -> str:
    key = os.environ.get("CONTENTDRIPS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CONTENTDRIPS_API_KEY is not set. "
            "Add it to your .env file or Railway environment variables."
        )
    return key


def _template_id() -> str:
    tid = os.environ.get("CONTENTDRIPS_TEMPLATE_ID", "").strip()
    if not tid:
        raise RuntimeError(
            "CONTENTDRIPS_TEMPLATE_ID is not set. "
            "Add the template ID from your Contentdrips account to .env."
        )
    return tid


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
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

    Handles edge cases:
      1 slide  → intro == ending, no middle slides
      2 slides → first is intro, second is ending, no middle
      3+ slides → normal split
    """
    if not slides:
        raise ValueError("Cannot format an empty slides list")

    def _slide(s: dict) -> dict:
        return {
            "heading":     s.get("heading", "").strip(),
            "description": s.get("description", "").strip(),
        }

    if len(slides) == 1:
        intro  = _slide(slides[0])
        middle = []
        ending = _slide(slides[0])          # duplicate single slide as fallback
    elif len(slides) == 2:
        intro  = _slide(slides[0])
        middle = []
        ending = _slide(slides[1])
    else:
        intro  = _slide(slides[0])
        middle = [_slide(s) for s in slides[1:-1]]
        ending = _slide(slides[-1])

    payload = {
        "intro_slide":  intro,
        "slides":       middle,
        "ending_slide": ending,
    }

    logger.debug("Contentdrips payload: %s", payload)
    return payload


# ---------------------------------------------------------------------------
# Step 2: Submit render request → job_id
# ---------------------------------------------------------------------------

def request_render(carousel_payload: dict) -> tuple[str | None, dict]:
    """
    POST the render request to Contentdrips.

    Returns (job_id, raw_response) where:
      - job_id is None if the render completed synchronously
      - raw_response is the full decoded JSON body (useful for debugging)

    Raises RuntimeError with full context on any failure.
    """
    url  = f"{_base()}{_RENDER_PATH}"
    body = {
        "template_id": _template_id(),
        "output":      "png",
        **carousel_payload,
    }

    # ── Full request logging ──────────────────────────────────────────────
    logger.info("POST %s", url)
    logger.info("Request payload: %s", body)

    try:
        resp = httpx.post(url, json=body, headers=_headers(), timeout=30)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error reaching Contentdrips ({url}): {exc}") from exc

    # ── Full response logging ─────────────────────────────────────────────
    logger.info("Response status: %s", resp.status_code)
    logger.info("Response body:   %s", resp.text[:2000])   # cap at 2 KB to avoid log spam

    # ── HTML guard — means wrong endpoint or auth wall ────────────────────
    content_type = resp.headers.get("content-type", "")
    if "html" in content_type or resp.text.lstrip().startswith("<"):
        raise RuntimeError(
            f"Contentdrips returned HTML instead of JSON — endpoint is likely wrong. "
            f"URL tried: {url}  |  Status: {resp.status_code}  |  "
            f"Set CONTENTDRIPS_API_BASE in .env to override the base URL."
        )

    if not resp.is_success:
        raise RuntimeError(
            f"Contentdrips render request failed [{resp.status_code}]: {resp.text}"
        )

    data = resp.json()

    # Accept "job_id" or "id" as the async job token
    job_id = data.get("job_id") or data.get("id")

    if job_id:
        logger.info("Contentdrips job started: job_id=%s", job_id)
    else:
        # Some plans return export_url synchronously — check for that
        export_url = data.get("export_url") or data.get("url") or data.get("download_url")
        if export_url:
            logger.info("Contentdrips returned synchronous export_url: %s", export_url)
        else:
            logger.warning("Response had no job_id or export_url — raw: %s", data)

    return (str(job_id) if job_id else None), data


# ---------------------------------------------------------------------------
# Step 3: Poll job → export_url
# ---------------------------------------------------------------------------

def poll_job(
    job_id: str,
    poll_interval: int = 4,
    max_retries: int = 30,
) -> str:
    """
    Poll the Contentdrips job status endpoint until the render completes.

    Returns the export_url on success.
    Raises RuntimeError on failure or TimeoutError if max_retries is exceeded.

    Default timeout: 4s × 30 = 120 seconds.
    """
    url = f"{_base()}{_STATUS_PATH.format(job_id=job_id)}"

    for attempt in range(1, max_retries + 1):
        logger.info("Polling job %s — attempt %d/%d", job_id, attempt, max_retries)

        try:
            resp = httpx.get(url, headers=_headers(), timeout=15)
        except httpx.RequestError as exc:
            logger.warning("Network error polling job %s: %s", job_id, exc)
            time.sleep(poll_interval)
            continue

        if not resp.is_success:
            raise RuntimeError(
                f"Contentdrips status check failed [{resp.status_code}]: {resp.text}"
            )

        data   = resp.json()
        status = (data.get("status") or "").lower()

        logger.info("Job %s status: %s", job_id, status)

        if status == "completed":
            export_url = data.get("export_url") or data.get("url") or data.get("download_url")
            if not export_url:
                raise RuntimeError(
                    f"Job {job_id} completed but response had no export URL. Response: {data}"
                )
            logger.info("Job %s complete → %s", job_id, export_url)
            return str(export_url)

        if status == "failed":
            reason = data.get("error") or data.get("message") or "no reason given"
            raise RuntimeError(f"Contentdrips job {job_id} failed: {reason}")

        # Still pending/processing — wait and retry
        if attempt < max_retries:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Contentdrips job {job_id} did not complete after "
        f"{max_retries} polls ({max_retries * poll_interval}s). "
        "Check your Contentdrips dashboard for the job status."
    )
