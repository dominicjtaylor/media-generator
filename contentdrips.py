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
    Map slides to the Contentdrips carousel structure for template 161759.

    Expects exactly 5 slides:
      slides[0] → intro_slide  { heading }
      slides[1] → slides[0]   { description }
      slides[2] → slides[1]   { description }
      slides[3] → slides[2]   { description }
      slides[4] → ending_slide { description }
    """
    if not slides:
        raise ValueError("Cannot format an empty slides list")

    if len(slides) != 5:
        raise ValueError(
            f"Template 161759 requires exactly 5 slides, got {len(slides)}. "
            "Check the LLM prompt slide count."
        )

    if not slides[0].get("heading", "").strip():
        raise ValueError("slides[0].heading is missing or empty")

    for i in range(1, 5):
        if not slides[i].get("description", "").strip():
            raise ValueError(f"slides[{i}].description is missing or empty")

    carousel = {
        "intro_slide": {
            "heading": slides[0]["heading"].strip(),
        },
        "slides": [
            {"description": slides[1]["description"].strip()},
            {"description": slides[2]["description"].strip()},
            {"description": slides[3]["description"].strip()},
        ],
        "ending_slide": {
            "description": slides[4]["description"].strip(),
        },
    }

    logger.info("Carousel payload (template 161759, 5 slides):")
    logger.info("  intro_slide:  %s", carousel["intro_slide"])
    for i, s in enumerate(carousel["slides"]):
        logger.info("  slide[%d]:     %s", i, s)
    logger.info("  ending_slide: %s", carousel["ending_slide"])

    return carousel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Field names that carry a final image/download URL
_EXPORT_URL_KEYS = ("export_url", "url", "download_url", "image_url", "file_url", "result_url")

# Field names that carry a polling URL provided BY the API
_POLLING_URL_KEYS = ("check_status_url", "status_url", "polling_url", "result_url", "check_url")

def _extract_export_url(data: dict) -> str | None:
    for key in _EXPORT_URL_KEYS:
        if data.get(key):
            return str(data[key])
    # Check inside a "links" object if present
    links = data.get("links") or {}
    for key in _EXPORT_URL_KEYS + _POLLING_URL_KEYS:
        if links.get(key):
            return str(links[key])
    return None

def _extract_polling_url(data: dict) -> str | None:
    for key in _POLLING_URL_KEYS:
        if data.get(key):
            return str(data[key])
    links = data.get("links") or {}
    for key in _POLLING_URL_KEYS:
        if links.get(key):
            return str(links[key])
    return None


# ---------------------------------------------------------------------------
# Step 2: Submit render request → export_url (sync or async)
# ---------------------------------------------------------------------------

def request_render(carousel_payload: dict) -> tuple[str, dict]:
    """
    POST to Contentdrips and return (export_url, raw_response).

    Handles both response shapes:
      Sync  — export_url present immediately → return it.
      Async — job_id present + polling URL provided by API → poll it.

    Never constructs a polling URL manually; only uses URLs from the response.
    Raises RuntimeError with the full response if neither path is possible.
    """
    url  = f"{_base()}{_RENDER_PATH}"
    body = {
        "template_id": _template_id(),
        "output":      "png",
        "carousel":    carousel_payload,
    }

    logger.info("─── Contentdrips request ───────────────────────────────")
    logger.info("POST %s", url)
    logger.info("Payload: %s", body)

    try:
        resp = httpx.post(url, json=body, headers=_headers(), timeout=60)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error reaching Contentdrips ({url}): {exc}") from exc

    logger.info("─── Contentdrips response ──────────────────────────────")
    logger.info("Status:           %s", resp.status_code)
    logger.info("Response headers: %s", dict(resp.headers))
    logger.info("Response body:    %s", resp.text[:2000])

    if "token not found" in resp.text.lower() or resp.status_code == 403:
        logger.error(
            "Auth failed — token not received by API. "
            "Status: %s | Body: %s | "
            "Check CONTENTDRIPS_API_KEY in Railway environment variables.",
            resp.status_code, resp.text,
        )

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

    # ── Path A: synchronous — export URL already in response ─────────────
    export_url = _extract_export_url(data)
    if export_url:
        logger.info("Sync response — export URL: %s", export_url)
        return export_url, data

    # ── Path B: async — job_id present, look for a polling URL ───────────
    job_id = data.get("job_id") or data.get("id")
    if job_id:
        logger.info("Async response — job_id: %s", job_id)
        polling_url = _extract_polling_url(data)

        if not polling_url:
            raise RuntimeError(
                f"Async job returned (job_id={job_id}) but no polling URL found in response. "
                f"Keys present: {list(data.keys())} | Full response: {data}"
            )

        # Resolve relative paths (e.g. "/job/<id>/status" → full URL)
        if polling_url.startswith("/"):
            polling_url = _base() + polling_url

        logger.info("Polling URL (resolved): %s", polling_url)
        return _poll(polling_url, job_id), data

    # ── Neither path matched ──────────────────────────────────────────────
    raise RuntimeError(
        f"Contentdrips response contained no export URL and no job_id. "
        f"Keys present: {list(data.keys())} | Full response: {data}"
    )


# ---------------------------------------------------------------------------
# Step 3: Poll a URL provided by the API (never constructed manually)
# ---------------------------------------------------------------------------

def _poll(
    polling_url: str,
    job_id: str,
    poll_interval: int = 3,
    max_retries: int = 40,
) -> str:
    """
    Poll *polling_url* (given by the API) until the job completes.
    Returns the export URL. Default timeout: 4s × 30 = 120s.
    """
    for attempt in range(1, max_retries + 1):
        logger.info("Polling attempt %d/%d — GET %s", attempt, max_retries, polling_url)

        try:
            resp = httpx.get(polling_url, headers=_headers(), timeout=15)
        except httpx.RequestError as exc:
            logger.warning("Network error on poll attempt %d: %s", attempt, exc)
            time.sleep(poll_interval)
            continue

        logger.info("Poll response [%s]: %s", resp.status_code, resp.text[:500])

        if not resp.is_success:
            raise RuntimeError(
                f"Poll failed [{resp.status_code}] for job {job_id}: {resp.text}"
            )

        data   = resp.json()
        status = (data.get("status") or "").lower()
        logger.info("Job %s status: %s", job_id, status)

        if status in ("queued", "processing"):
            if attempt < max_retries:
                time.sleep(poll_interval)
            continue

        if status == "completed":
            return _fetch_result(job_id)

        if status == "failed":
            reason = data.get("error") or data.get("message") or "no reason given"
            raise RuntimeError(f"Job {job_id} failed: {reason}")

        if attempt < max_retries:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Job {job_id} did not complete after {max_retries} polls "
        f"({max_retries * poll_interval}s)."
    )


def _fetch_result(job_id: str) -> str:
    """GET /job/{job_id}/result — called once polling confirms completed."""
    url = f"{_base()}/job/{job_id}/result"
    logger.info("Fetching result — GET %s", url)

    try:
        resp = httpx.get(url, headers=_headers(), timeout=15)
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error fetching result for job {job_id}: {exc}") from exc

    logger.info("Result response [%s]: %s", resp.status_code, resp.text[:2000])
    logger.info("Result keys: %s", list(resp.json().keys()) if resp.is_success else "n/a")

    if resp.status_code == 404:
        raise RuntimeError(f"Result endpoint not found (404) for job {job_id}: {url}")

    if not resp.is_success:
        raise RuntimeError(
            f"Result fetch failed [{resp.status_code}] for job {job_id}: {resp.text}"
        )

    data = resp.json()
    export_url = _extract_export_url(data)
    if not export_url:
        raise RuntimeError(
            f"Job {job_id} result had no export URL. "
            f"Keys present: {list(data.keys())} | Full response: {data}"
        )

    logger.info("Job %s → export URL: %s", job_id, export_url)
    return export_url
