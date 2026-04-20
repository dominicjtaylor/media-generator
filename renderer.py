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

# Styles that require photo images
_IMAGE_STYLES = frozenset({"dark_core", "light_image"})


# ---------------------------------------------------------------------------
# Markdown bold helpers
# ---------------------------------------------------------------------------

def _md_bold_to_html(text: str) -> str:
    """Convert **word** markers to <strong>word</strong>."""
    return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)


def _strip_bold(text: str) -> str:
    """Remove **...** bold markers, keeping the inner text."""
    return re.sub(r'\*\*(.*?)\*\*', r'\1', text)


# ---------------------------------------------------------------------------
# Step 1: HTML injection
# ---------------------------------------------------------------------------

def inject_slide(
    index: int,
    slide: dict,
    total: int,
    template_style: str = "dark_core",
    image_data: Optional[dict] = None,
    slide_image_filename: Optional[str] = None,
) -> str:
    """Inject slide data into the appropriate HTML template.

    Parameters
    ----------
    index                : int           0-based position in the slide list.
    slide                : dict          Must contain "heading"; "body" for content slides.
    total                : int           Total number of slides.
    template_style       : str           "dark_core" or "light_image".
    image_data           : dict | None   For dark_core hook slide: focal_x/focal_y for object-position.
    slide_image_filename : str | None    For light_image content slides: per-slide image filename.
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
        content_index = index - 1
        number        = _CONTENT_NUMS[content_index]

    if template_style == "dark_core":
        template_dir = _STYLES_DIR / "headings_text_image" / "dark"
    elif template_style == "light_image":
        template_dir = _STYLES_DIR / "headings_text_image" / "light"
    else:
        template_dir = _STYLES_DIR / template_style
    template_path = template_dir / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}. "
            f"Ensure {template_name} exists in slide_styles/{template_style}/."
        )

    html = template_path.read_text(encoding="utf-8")

    # Fix logo paths — templates use relative paths; rewrite to absolute for Playwright.
    logo_abs       = str(_ROOT / "logo.png")
    logo_light_abs = str(_ROOT / "logo_light.png")
    for old, new in [
        ('src="../../../logo.png"',       f'src="{logo_abs}"'),
        ("src='../../../logo.png'",       f"src='{logo_abs}'"),
        ('src="../../../logo_light.png"', f'src="{logo_light_abs}"'),
        ("src='../../../logo_light.png'", f"src='{logo_light_abs}'"),
        ('src="../../logo.png"',          f'src="{logo_abs}"'),
        ("src='../../logo.png'",          f"src='{logo_abs}'"),
        ('src="../../logo_light.png"',    f'src="{logo_light_abs}"'),
        ("src='../../logo_light.png'",    f"src='{logo_light_abs}'"),
    ]:
        html = html.replace(old, new)

    # dark_core hook slide: apply focal-point object-position on the featured image.
    if template_style == "dark_core" and index == 0 and image_data:
        focal_x = float(image_data.get("focal_x") or 0.5)
        focal_y = float(image_data.get("focal_y") or 0.5)
        obj_pos = f"{focal_x * 100:.1f}% {focal_y * 100:.1f}%"
        html = html.replace(
            '<img src="image.png" alt="visual">',
            f'<img src="image.png" alt="visual" style="object-position: {obj_pos};">',
        )
        logger.debug("Applied focal-point object-position: %s", obj_pos)

    # light_image content slides: substitute per-slide image filename.
    if template_style == "light_image" and 0 < index < last_index and slide_image_filename:
        html = html.replace(
            '<img src="image.png" alt="visual">',
            f'<img src="{slide_image_filename}" alt="visual">',
        )

    # First and last slides: single {{TEXT}} placeholder.
    if index == 0 or index == last_index:
        html = html.replace("{{TEXT}}", _md_bold_to_html(heading))
    else:
        # Content slides: separate {{HEADING}} + {{TEXT}} zones.
        # Anton font is used for headings — strip bold markers to avoid font fallback.
        html = html.replace("{{HEADING}}", _strip_bold(heading))
        html = html.replace("{{TEXT}}",    _md_bold_to_html(body.replace('\n', '<br>')))
        tag = (slide.get("tag") or "").strip().upper()
        html = html.replace("{{TAG}}", tag)
        slide_counter = f"{str(index + 1).zfill(2)} / {str(total).zfill(2)}"
        html = html.replace("{{SLIDE_COUNTER}}", slide_counter)

    if number is not None:
        html = html.replace("{{NUMBER}}", number)

    return html


# ---------------------------------------------------------------------------
# Step 2: Playwright rendering
# ---------------------------------------------------------------------------

def render_slides(
    slides: list[dict],
    renders_base: str = "/tmp/renders",
    template_style: str = "dark_core",
    image_data: Optional[dict] = None,
    content_image_paths: Optional[list[str]] = None,
) -> tuple[list[str], str]:
    """
    Render slides to PNG files using Playwright (Chromium headless).

    Parameters
    ----------
    slides               : list[dict]        Slide dicts from generate_slides() or generate_light_slides().
    renders_base         : str               Base directory for render output.
    template_style       : str               "dark_core" or "light_image".
    image_data           : dict | None       For dark_core: Lummi/local image metadata with "local_path".
    content_image_paths  : list[str] | None  For light_image: one image path per content slide (index 1..n-2).

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

    # Copy logo files so absolute src paths in templates resolve correctly
    for logo_name in ("logo.png", "logo_light.png"):
        logo_src = _ROOT / logo_name
        if logo_src.exists():
            shutil.copy2(logo_src, out_dir / logo_name)
        else:
            logger.warning("%s not found at %s", logo_name, logo_src)

    # dark_core: copy the featured photo as image.png for the hook slide.
    if template_style == "dark_core" and image_data:
        src_image = Path(image_data["local_path"])
        if src_image.exists():
            shutil.copy2(src_image, out_dir / "image.png")
            logger.info("Copied image %s → %s/image.png", src_image.name, out_dir)
        else:
            logger.error(
                "Image file not found: %s — slide 1 image will be blank",
                src_image,
            )

    # light_image: copy per-slide content images; build a per-slide filename map.
    slide_image_filenames: list[Optional[str]] = [None] * n
    if template_style == "light_image" and content_image_paths:
        for ci, img_path_str in enumerate(content_image_paths):
            slide_idx = ci + 1  # content slides start at index 1
            if slide_idx >= n - 1:
                break
            src = Path(img_path_str)
            ext = src.suffix.lower() or ".jpg"
            dest_name = f"content-image-{ci}{ext}"
            if src.exists():
                shutil.copy2(src, out_dir / dest_name)
                slide_image_filenames[slide_idx] = dest_name
                logger.info("Copied content image %s → %s", src.name, dest_name)
            else:
                logger.error("Content image not found: %s", src)

    # Write HTML files
    html_paths: list[Path] = []
    for i, slide in enumerate(slides):
        html      = inject_slide(
            i, slide, total=n,
            template_style=template_style,
            image_data=image_data,
            slide_image_filename=slide_image_filenames[i],
        )
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
