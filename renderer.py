"""
renderer.py -- HTML template injection + Playwright PNG rendering.

Pipeline
--------
  1. inject_slide(index, slide, total)  -> HTML string
  2. render_slides(slides)              -> (png_paths, run_id)

Template mapping
----------------
  index 0          -> slide-first.html
  index 1 to n-2   -> slide-content.html   ({{NUMBER}} = "01" ... "05")
  index n-1        -> slide-last.html

Placeholders
------------
  {{TEXT}}   -- raw HTML; <strong> intentionally NOT escaped
  {{NUMBER}} -- zero-padded content-slide number ("01"–"05")

Output
------
  PNG files are written to /tmp/renders/<run_id>/slide-{n}.png.
  The run_id is returned so the caller can build /renders/<run_id>/slide-n.png URLs.
"""

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("carousel.renderer")

_ROOT       = Path(__file__).parent          # project root (logo.png lives here)
_STYLES_DIR = _ROOT / "slide_styles"         # template styles live here
# Supports up to 8 content slides (10 total - hook - cta = 8)
_CONTENT_NUMS = ["01", "02", "03", "04", "05", "06", "07", "08"]


# ---------------------------------------------------------------------------
# Markdown bold → HTML
# ---------------------------------------------------------------------------

def _md_bold_to_html(text: str) -> str:
    """Convert **word** markers to <strong>word</strong>.

    Leaves text without markers at normal weight (font-weight: 300 in templates).
    If no markers are present the text is returned unchanged — nothing is
    auto-bolded.
    """
    return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)


def _strip_bold(text: str) -> str:
    """Remove **..** markers, returning plain text without HTML tags.

    Used for heading elements rendered in Anton — the font is already
    display-weight so bold markers add no visual value and, when converted
    to <strong>, cause a mid-string font fallback to Inter via the * rule.
    """
    return re.sub(r'\*\*(.*?)\*\*', r'\1', text)


# ---------------------------------------------------------------------------
# Step 1: HTML injection
# ---------------------------------------------------------------------------

def inject_slide(
    index: int,
    slide: dict,
    total: int,
    template_style: str = "text_only",
    image_data: Optional[dict] = None,
) -> str:
    """Inject slide data into the appropriate HTML template.

    Parameters
    ----------
    index          : int           0-based position in the slide list.
    slide          : dict          Must contain "heading"; "body" is used for heading styles.
    total          : int           Total number of slides (determines which index is the last).
    template_style : str           One of "text_only", "headings_and_text", "headings_text_image".
    image_data     : dict | None   Lummi image metadata.  When provided for the first slide of
                                   headings_text_image, focal_x/focal_y are applied to the image
                                   via inline CSS object-position.
    """
    heading = (slide.get("heading") or "").strip()
    body    = (slide.get("body")    or "").strip()

    last_index = total - 1

    if index == 0:
        template_name = "slide-first.html"
        number        = None
    elif index == last_index:
        template_name = "slide-last.html"
        number        = None
    else:
        template_name = "slide-content.html"
        content_index = index - 1          # 0-based among content slides
        number        = _CONTENT_NUMS[content_index]

    template_dir  = _STYLES_DIR / template_style
    template_path = template_dir / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}. "
            f"Ensure {template_name} exists in slide_styles/{template_style}/."
        )

    html = template_path.read_text(encoding="utf-8")

    # Fix logo paths that use relative "../../logo.png" (designed for direct file
    # browsing in slide_styles/).  The HTML is written to a temp dir so we swap
    # them for the absolute path to the project-root logo.png.
    logo_abs = str(_ROOT / "logo.png")
    html = html.replace('src="../../logo.png"', f'src="{logo_abs}"')
    html = html.replace("src='../../logo.png'", f"src='{logo_abs}'")

    # Apply focal-point object-position to the first slide of the image template.
    # Always inject as an inline style on the <img> tag — deterministic and immune
    # to CSS specificity or whitespace variations in the template.
    if template_style == "headings_text_image" and index == 0:
        focal_x = float((image_data or {}).get("focal_x") or 0.5)
        focal_y = float((image_data or {}).get("focal_y") or 0.5)
        obj_pos = f"{focal_x * 100:.1f}% {focal_y * 100:.1f}%"
        html = html.replace(
            '<img src="image.png" alt="visual">',
            f'<img src="image.png" alt="visual" style="object-position: {obj_pos};">',
        )
        logger.debug("Applied focal-point object-position: %s", obj_pos)

    if template_style == "text_only":
        # Single {{TEXT}} field: concatenate heading + body (legacy behaviour)
        if body:
            text = f"{_md_bold_to_html(heading)}<br>{_md_bold_to_html(body)}"
        else:
            text = _md_bold_to_html(heading)
        html = html.replace("{{TEXT}}", text)

    else:
        # Heading styles:
        # - slide-first.html only has {{TEXT}} → inject heading there
        # - slide-last.html has {{TEXT}} (heading) + {{BODY}} (body) → inject separately
        # - slide-content.html has {{HEADING}} + {{TEXT}} → inject separately
        if index == 0:
            html = html.replace("{{TEXT}}", _md_bold_to_html(heading))
        elif index == last_index:
            html = html.replace("{{TEXT}}", _md_bold_to_html(heading))
            html = html.replace("{{BODY}}", _md_bold_to_html(body))
        else:
            # Content template: two separate zones.
            # Heading uses Anton exclusively — strip bold markers rather than
            # converting to <strong>, which would trigger a mid-string font
            # fallback to Inter via the universal * rule.
            html = html.replace("{{HEADING}}", _strip_bold(heading))
            html = html.replace("{{TEXT}}",    _md_bold_to_html(body))

    if number is not None:
        html = html.replace("{{NUMBER}}", number)

    return html


# ---------------------------------------------------------------------------
# Step 2: Playwright rendering
# ---------------------------------------------------------------------------

def render_slides(
    slides: list[dict],
    renders_base: str = "/tmp/renders",
    template_style: str = "text_only",
    image_data: Optional[dict] = None,
) -> tuple[list[str], str]:
    """
    Render slides to PNG files using Playwright (Chromium headless).

    Parameters
    ----------
    slides         : list[dict]   Slide dicts from generator.generate_slides().
    renders_base   : str          Base directory for render output.
    template_style : str          One of "text_only", "headings_and_text", "headings_text_image".
    image_data     : dict | None  Lummi image metadata (only used for headings_text_image).
                                  Must contain "local_path" pointing to downloaded image.

    Returns
    -------
    png_paths : list[str]   Absolute paths to the rendered PNGs, in order.
    run_id    : str         UUID subdir name (for building /renders/<id>/ URLs).
    """
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    n = len(slides)
    if not (4 <= n <= 10):
        raise ValueError(f"Expected 4–10 slides, got {n}")

    run_id  = uuid.uuid4().hex
    out_dir = Path(renders_base) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Render run %s (style=%s) -> %s", run_id, template_style, out_dir)

    # Copy logo.png so relative src="logo.png" in text_only templates resolves correctly
    logo_src = _ROOT / "logo.png"
    if logo_src.exists():
        shutil.copy2(logo_src, out_dir / "logo.png")
    else:
        logger.warning("logo.png not found at %s -- brand logo will be missing", logo_src)

    # For the image template, copy the downloaded photo so slide-first.html can
    # resolve <img src="image.png"> from the render directory.
    if template_style == "headings_text_image" and image_data:
        src_image = Path(image_data["local_path"])
        if src_image.exists():
            shutil.copy2(src_image, out_dir / "image.png")
            logger.info("Copied image %s → %s/image.png", src_image.name, out_dir)
        else:
            logger.error(
                "Image file not found at resolved path: %s (absolute: %s) — "
                "slide 1 image will be blank",
                src_image,
                src_image.resolve(),
            )

    # Write HTML files
    html_paths: list[Path] = []
    for i, slide in enumerate(slides):
        html      = inject_slide(i, slide, total=n, template_style=template_style, image_data=image_data)
        html_path = out_dir / f"slide-{i + 1}.html"
        html_path.write_text(html, encoding="utf-8")
        html_paths.append(html_path)
        slide_role = "first" if i == 0 else ("last" if i == n - 1 else "content")
        logger.debug(
            "Slide %d/%d (%s) — type: %s | text: %r | file: %s",
            i + 1, n, slide_role,
            slide.get("type", "unknown"),
            slide.get("heading", "")[:60],
            html_path.name,
        )

    # Screenshot each slide
    png_paths: list[str] = []
    try:
        with sync_playwright() as pw:
            # --no-sandbox / --disable-setuid-sandbox: required in Railway/Docker
            #   because the process runs as root inside the container.
            # --disable-dev-shm-usage: /dev/shm is often only 64 MB in containers;
            #   without this flag Chromium crashes on memory-intensive pages.
            browser = pw.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1080, "height": 1350},
                    device_scale_factor=2,
                )
                for i, html_path in enumerate(html_paths):
                    page = context.new_page()
                    try:
                        url = f"file://{html_path.absolute()}"
                        logger.debug("Loading slide %d — %s", i + 1, url)
                        # Use domcontentloaded (not networkidle) so we don't block on the
                        # Google Fonts CDN request.  The CDN URL contains @ and ; which
                        # trigger Playwright's internal URL pattern matcher and cause
                        # "The string did not match the expected pattern".
                        page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                        # Wait for fonts — resolves even if the CDN is unreachable,
                        # falling back to the system font stack defined in the templates.
                        try:
                            page.evaluate("document.fonts.ready")
                        except Exception as font_exc:
                            logger.warning(
                                "Slide %d: font loading skipped (%s) — "
                                "fallback fonts will be used",
                                i + 1, font_exc,
                            )
                        png_path = out_dir / f"slide-{i + 1}.png"
                        logger.debug("Screenshotting slide %d → %s", i + 1, png_path.name)
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
