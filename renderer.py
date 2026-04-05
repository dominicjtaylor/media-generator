"""
renderer.py -- HTML template injection + Playwright PNG rendering.

Pipeline
--------
  1. inject_slide(index, slide)  -> HTML string
  2. render_slides(slides)       -> (png_paths, run_id)

Template mapping
----------------
  index 0   -> slide-first.html
  index 1-3 -> slide-content.html   ({{NUMBER}} = "01" ... "03")
  index 4   -> slide-last.html

Placeholders
------------
  {{TEXT}}   -- raw HTML; <strong> and <br> intentionally NOT escaped
  {{NUMBER}} -- zero-padded content-slide number ("01", "02", "03")

Output
------
  PNG files are written to /tmp/renders/<run_id>/slide-{n}.png.
  The run_id is returned so the caller can build /renders/<run_id>/slide-n.png URLs.
"""

import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger("carousel.renderer")

_ROOT         = Path(__file__).parent   # project root (templates live here)
_CONTENT_NUMS = ["01", "02", "03"]      # for slide indices 1, 2, 3


# ---------------------------------------------------------------------------
# Step 1: HTML injection
# ---------------------------------------------------------------------------

def inject_slide(index: int, slide: dict) -> str:
    """Inject slide data into the appropriate HTML template."""
    heading     = (slide.get("heading")     or "").strip()
    description = (slide.get("description") or "").strip()

    if index == 0:
        template_name = "slide-first.html"
        text   = f"<strong>{heading}</strong><br>{description}"
        number = None
    elif index == 4:
        template_name = "slide-last.html"
        text   = f"<strong>{heading}</strong><br>{description}"
        number = None
    else:
        template_name = "slide-content.html"
        text   = f"<strong>{heading}</strong><br>{description}"
        number = _CONTENT_NUMS[index - 1]

    template_path = _ROOT / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}. "
            "Ensure slide-first.html, slide-content.html, and slide-last.html "
            "exist in the project root."
        )

    html = template_path.read_text(encoding="utf-8")
    html = html.replace("{{TEXT}}", text)           # raw -- no escaping
    if number is not None:
        html = html.replace("{{NUMBER}}", number)

    return html


# ---------------------------------------------------------------------------
# Step 2: Playwright rendering
# ---------------------------------------------------------------------------

def render_slides(
    slides: list[dict],
    renders_base: str = "/tmp/renders",
) -> tuple[list[str], str]:
    """
    Render slides to PNG files using Playwright (Chromium headless).

    Returns
    -------
    png_paths : list[str]   Absolute paths to the rendered PNGs, in order.
    run_id    : str         UUID subdir name (for building /renders/<id>/ URLs).
    """
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    if len(slides) != 5:
        raise ValueError(f"Expected exactly 5 slides, got {len(slides)}")

    run_id  = uuid.uuid4().hex
    out_dir = Path(renders_base) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Render run %s -> %s", run_id, out_dir)

    # Copy logo.png so relative src="logo.png" in templates resolves correctly
    logo_src = _ROOT / "logo.png"
    if logo_src.exists():
        shutil.copy2(logo_src, out_dir / "logo.png")
    else:
        logger.warning("logo.png not found at %s -- brand logo will be missing", logo_src)

    # Write HTML files
    html_paths: list[Path] = []
    for i, slide in enumerate(slides):
        html_path = out_dir / f"slide-{i + 1}.html"
        html_path.write_text(inject_slide(i, slide), encoding="utf-8")
        html_paths.append(html_path)
        logger.debug("Wrote HTML slide %d", i + 1)

    # Screenshot each slide
    png_paths: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 1080, "height": 1350},
                    device_scale_factor=2,
                )
                for i, html_path in enumerate(html_paths):
                    page = context.new_page()
                    try:
                        page.goto(
                            f"file://{html_path.absolute()}",
                            wait_until="networkidle",
                            timeout=15_000,
                        )
                        png_path = out_dir / f"slide-{i + 1}.png"
                        page.screenshot(path=str(png_path), full_page=False)
                        png_paths.append(str(png_path))
                        logger.info("Rendered slide %d/%d", i + 1, len(slides))
                    except PlaywrightError as exc:
                        raise RuntimeError(f"Playwright error on slide {i + 1}: {exc}") from exc
                    finally:
                        page.close()
            finally:
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Rendering failed: {exc}") from exc

    # Clean up HTML intermediates (keep only PNGs)
    for hp in html_paths:
        try:
            hp.unlink()
        except OSError:
            pass

    logger.info("Render complete: %d PNGs in run %s", len(png_paths), run_id)
    return png_paths, run_id
