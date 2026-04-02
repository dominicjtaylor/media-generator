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
  CONTENTDRIPS_API_BASE     — Override base URL (default: https://api.contentdrips.com)
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
    return os.environ.get("CONTENTDRIPS_API_BASE", "https://api.contentdrips.com").rstrip("/")

# ┌─────────────────────────────────────────────────────────────────────────┐
# │  TODO: Verify these endpoint paths against Contentdrips API docs before │
# │  deploying. Update CONTENTDRIPS_API_BASE in .env if the base differs.   │
# └─────────────────────────────────────────────────────────────────────────┘
_RENDER_PATH = "/v1/renders"
_STATUS_PATH = "/v1/renders/{job_id}"


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

def request_render(carousel_payload: dict) -> str:
    """
    POST the render request to Contentdrips.
    Returns the job_id string.
    """
    body = {
        "template_id": _template_id(),
        "output":      "png",
        **carousel_payload,
    }

    logger.info("Sending Contentdrips render request (template: %s)", body["template_id"])
    logger.debug("Request body: %s", body)

    try:
        resp = httpx.post(
            f"{_base()}{_RENDER_PATH}",
            json=body,
            headers=_headers(),
            timeout=30,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"Failed to reach Contentdrips API: {exc}") from exc

    if not resp.is_success:
        raise RuntimeError(
            f"Contentdrips render request failed [{resp.status_code}]: {resp.text}"
        )

    data = resp.json()
    # Accept either "job_id" or "id" as the job identifier
    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(
            f"Contentdrips response did not contain a job_id. Response: {data}"
        )

    logger.info("Contentdrips job started: job_id=%s", job_id)
    return str(job_id)


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
