"""
uploader.py — Playwright automation for Contentdrips CSV upload + download.

Flow:
  1. Login to https://app.contentdrips.com
  2. Navigate to Bulk / CSV upload section
  3. Upload csv_upload_feature.csv
  4. Select pre-existing template
  5. Trigger generation
  6. Wait for render completion
  7. Download all carousel images
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("carousel.uploader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://app.contentdrips.com"
LOGIN_URL = f"{BASE_URL}/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"

# Selectors — these are best-effort based on the Contentdrips UI patterns.
# If the site updates its DOM, adjust these selectors.
SELECTORS = {
    # Login page
    "email_input": 'input[type="email"], input[name="email"], #email',
    "password_input": 'input[type="password"], input[name="password"], #password',
    "login_button": 'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")',
    # Navigation — bulk/CSV upload entry points
    "bulk_upload_nav": (
        'a:has-text("Bulk"), a:has-text("CSV"), '
        'button:has-text("Bulk"), button:has-text("CSV Upload"), '
        '[href*="bulk"], [href*="csv"]'
    ),
    # Upload area
    "file_input": 'input[type="file"]',
    "upload_button": (
        'button:has-text("Upload"), button:has-text("Import"), '
        'button:has-text("Generate"), button[type="submit"]'
    ),
    # Template selection
    "template_item": '.template-card, .template-item, [class*="template"]',
    # Generation trigger
    "generate_button": (
        'button:has-text("Generate"), button:has-text("Create"), '
        'button:has-text("Export"), button:has-text("Download")'
    ),
    # Download
    "download_button": (
        'a[download], button:has-text("Download"), '
        'a:has-text("Download"), [aria-label*="download"]'
    ),
    # Progress / done indicators
    "progress_bar": '[role="progressbar"], .progress, [class*="progress"]',
    "done_indicator": (
        ':has-text("Complete"), :has-text("Done"), :has-text("Ready"), '
        ':has-text("Finished"), [class*="complete"], [class*="done"]'
    ),
}

TIMEOUTS = {
    "navigation": 30_000,   # 30 s
    "login": 15_000,
    "upload": 20_000,
    "render": 120_000,      # 2 min — rendering can be slow
    "download": 60_000,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_and_download(
    csv_path: Path,
    output_dir: Path,
    headless: bool = True,
    template_name: Optional[str] = None,
    max_retries: int = 3,
) -> list[Path]:
    """
    Upload *csv_path* to Contentdrips, generate a carousel, and download the
    resulting images to *output_dir*.

    Parameters
    ----------
    csv_path:
        Local CSV file to upload.
    output_dir:
        Directory where downloaded images will be saved.
    headless:
        Run browser headlessly (default True).
    template_name:
        Partial name of the template to select (case-insensitive substring
        match). If None, the first available template is selected.
    max_retries:
        Number of full upload-cycle retries before raising.

    Returns
    -------
    list[Path]
        Paths to the downloaded image files.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError as exc:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && "
            "playwright install chromium"
        ) from exc

    email = os.environ.get("CONTENTDRIPS_EMAIL", "")
    password = os.environ.get("CONTENTDRIPS_PASSWORD", "")

    if not email or not password:
        raise RuntimeError(
            "CONTENTDRIPS_EMAIL and CONTENTDRIPS_PASSWORD must be set in .env"
        )

    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        logger.info("Upload attempt %d/%d", attempt, max_retries)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()

                _login(page, email, password)
                _navigate_to_bulk_upload(page)
                _upload_csv(page, csv_path)
                _select_template(page, template_name)
                _trigger_generation(page)
                downloaded = _wait_and_download(page, output_dir)

                context.close()
                browser.close()
                return downloaded

        except Exception as exc:
            logger.warning("Upload attempt %d failed: %s", attempt, exc)
            last_error = exc

        if attempt < max_retries:
            wait = 2 ** attempt
            logger.info("Waiting %ds before retry…", wait)
            time.sleep(wait)

    raise RuntimeError(
        f"Upload failed after {max_retries} attempts. Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Internal steps
# ---------------------------------------------------------------------------

def _login(page, email: str, password: str) -> None:
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Navigating to login page…")
    page.goto(LOGIN_URL, timeout=TIMEOUTS["navigation"])
    page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])

    logger.info("Filling login credentials…")
    try:
        page.fill(SELECTORS["email_input"], email, timeout=TIMEOUTS["login"])
        page.fill(SELECTORS["password_input"], password, timeout=TIMEOUTS["login"])
        page.click(SELECTORS["login_button"], timeout=TIMEOUTS["login"])
    except PwTimeout as exc:
        raise RuntimeError(
            "Login form not found. The Contentdrips login UI may have changed. "
            f"Selector tried: {SELECTORS['email_input']}"
        ) from exc

    # Wait for redirect away from login URL
    try:
        page.wait_for_url(
            lambda url: "/login" not in url,
            timeout=TIMEOUTS["login"],
        )
        logger.info("Login successful — redirected to %s", page.url)
    except PwTimeout as exc:
        # Check for error messages on page
        error_text = page.locator('[class*="error"], [class*="alert"]').first.text_content(timeout=2000) or ""
        raise RuntimeError(
            f"Login failed or timed out. Page error: {error_text!r}"
        ) from exc


def _navigate_to_bulk_upload(page) -> None:
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Looking for Bulk Upload navigation…")
    page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])

    # Try clicking a nav link first
    nav = page.locator(SELECTORS["bulk_upload_nav"]).first
    if nav.count() > 0:
        try:
            nav.click(timeout=5_000)
            page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])
            logger.info("Navigated via nav link to %s", page.url)
            return
        except PwTimeout:
            pass

    # Fallback: try known URL patterns
    for path in ["/bulk", "/csv-upload", "/bulk-upload", "/create/bulk"]:
        candidate = BASE_URL + path
        logger.info("Trying URL: %s", candidate)
        resp = page.goto(candidate, timeout=TIMEOUTS["navigation"])
        if resp and resp.status == 200:
            page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])
            logger.info("Reached bulk upload at %s", page.url)
            return

    raise RuntimeError(
        "Could not find the Bulk Upload section. "
        "Please verify the URL or selector for your Contentdrips plan."
    )


def _upload_csv(page, csv_path: Path) -> None:
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Uploading CSV: %s", csv_path)

    file_input = page.locator(SELECTORS["file_input"]).first
    try:
        file_input.wait_for(state="attached", timeout=TIMEOUTS["upload"])
    except PwTimeout as exc:
        raise RuntimeError(
            "File input not found on page. "
            "The upload UI may be inside a modal — check the selector."
        ) from exc

    file_input.set_input_files(str(csv_path.resolve()))
    logger.info("File set on input element")

    # Click an explicit upload/submit button if present
    upload_btn = page.locator(SELECTORS["upload_button"]).first
    if upload_btn.count() > 0:
        try:
            upload_btn.click(timeout=5_000)
            logger.info("Clicked upload/submit button")
        except PwTimeout:
            logger.debug("Upload button click timed out — may not be required")

    page.wait_for_load_state("networkidle", timeout=TIMEOUTS["upload"])


def _select_template(page, template_name: Optional[str]) -> None:
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Selecting template (name filter: %r)…", template_name)
    page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])

    templates = page.locator(SELECTORS["template_item"])
    try:
        templates.first.wait_for(state="visible", timeout=10_000)
    except PwTimeout:
        logger.warning(
            "No template cards visible. Skipping template selection — "
            "the site may select one automatically."
        )
        return

    count = templates.count()
    logger.info("Found %d template(s)", count)

    if template_name:
        for i in range(count):
            card = templates.nth(i)
            text = (card.text_content() or "").lower()
            if template_name.lower() in text:
                card.click()
                logger.info("Selected template matching %r", template_name)
                return
        logger.warning(
            "No template matched %r — selecting first available", template_name
        )

    templates.first.click()
    logger.info("Selected first available template")
    page.wait_for_load_state("networkidle", timeout=5_000)


def _trigger_generation(page) -> None:
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Triggering carousel generation…")

    gen_btn = page.locator(SELECTORS["generate_button"]).first
    try:
        gen_btn.wait_for(state="visible", timeout=10_000)
        gen_btn.click()
        logger.info("Clicked generate button")
    except PwTimeout:
        logger.warning(
            "Generate button not found. Generation may have started automatically."
        )


def _wait_and_download(page, output_dir: Path) -> list[Path]:
    """Wait for render completion, then download all carousel images."""
    from playwright.sync_api import TimeoutError as PwTimeout

    logger.info("Waiting for carousel render to complete…")

    # Strategy 1: wait for a 'done/complete' indicator
    try:
        page.locator(SELECTORS["done_indicator"]).first.wait_for(
            state="visible", timeout=TIMEOUTS["render"]
        )
        logger.info("Render complete indicator found")
    except PwTimeout:
        logger.warning(
            "Render complete indicator not found within timeout. "
            "Attempting download anyway…"
        )

    # Strategy 2: wait for download buttons to appear
    try:
        page.locator(SELECTORS["download_button"]).first.wait_for(
            state="visible", timeout=30_000
        )
    except PwTimeout:
        logger.warning("Download buttons not found — download may not be available yet")

    # Collect all download buttons
    dl_buttons = page.locator(SELECTORS["download_button"])
    count = dl_buttons.count()
    logger.info("Found %d download button(s)", count)

    if count == 0:
        raise RuntimeError(
            "No download buttons found after generation. "
            "Generation may have failed or the download UI changed."
        )

    downloaded_paths: list[Path] = []

    for i in range(count):
        btn = dl_buttons.nth(i)
        logger.info("Downloading image %d/%d…", i + 1, count)

        with page.expect_download(timeout=TIMEOUTS["download"]) as dl_info:
            btn.click()

        download = dl_info.value
        suggested = download.suggested_filename or f"slide_{i + 1}.png"
        dest = output_dir / suggested

        download.save_as(str(dest))
        downloaded_paths.append(dest)
        logger.info("Saved: %s", dest)

    logger.info("Downloaded %d image(s) to %s", len(downloaded_paths), output_dir)
    return downloaded_paths
