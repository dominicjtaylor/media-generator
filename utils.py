"""
utils.py — Shared helpers: slugify, file handling, CSV validation, logging.
"""

import csv
import io
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logger and return a named logger for the caller."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(
        level=numeric,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("carousel")


logger = logging.getLogger("carousel.utils")


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert *text* to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "topic"


# ---------------------------------------------------------------------------
# Output directory helpers
# ---------------------------------------------------------------------------

def output_dir(topic: str, base: str = "output") -> Path:
    """Return (and create) the output directory for *topic*."""
    slug = slugify(topic)
    path = Path(base) / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def csv_path() -> Path:
    """Return the canonical local CSV path."""
    return Path("csv_upload_feature.csv")


# ---------------------------------------------------------------------------
# CSV validation
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {"Topic", "Slide", "Heading", "Description"}


def validate_csv(path: Path) -> bool:
    """
    Validate the carousel CSV at *path*.

    Checks:
    - File exists and is non-empty
    - Required columns present
    - At least one data row
    - No row has blank Heading or Description
    """
    if not path.exists():
        logger.error("CSV not found: %s", path)
        return False

    if path.stat().st_size == 0:
        logger.error("CSV is empty: %s", path)
        return False

    try:
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            headers = set(reader.fieldnames or [])
            missing = REQUIRED_COLUMNS - headers
            if missing:
                logger.error("CSV missing columns: %s", missing)
                return False

            rows = list(reader)
            if not rows:
                logger.error("CSV has no data rows")
                return False

            for i, row in enumerate(rows, start=2):
                if not row.get("Heading", "").strip():
                    logger.error("Row %d: blank Heading", i)
                    return False
                if not row.get("Description", "").strip():
                    logger.error("Row %d: blank Description", i)
                    return False

        logger.info("CSV validation passed (%d slides)", len(rows))
        return True

    except Exception as exc:
        logger.error("CSV validation error: %s", exc)
        return False


def sanitise_csv_text(raw: str) -> str:
    """
    Strip any leading/trailing noise that LLMs sometimes add around CSV.

    Handles:
    - ```csv ... ``` fences
    - Leading explanatory prose before the header line
    """
    # Remove markdown code fences
    raw = re.sub(r"```(?:csv)?\s*", "", raw, flags=re.IGNORECASE)
    raw = raw.strip()

    # Find the header line
    lines = raw.splitlines()
    header_idx = None
    for idx, line in enumerate(lines):
        stripped = line.strip().strip('"')
        if stripped.lower().startswith("topic"):
            header_idx = idx
            break

    if header_idx is None:
        # Return as-is and let validation catch the issue
        return raw

    return "\n".join(lines[header_idx:]).strip()


def save_csv(content: str, path: Optional[Path] = None) -> Path:
    """Write *content* to *path* (defaults to csv_upload_feature.csv)."""
    dest = path or csv_path()
    dest.write_text(content, encoding="utf-8")
    logger.info("CSV saved → %s", dest)
    return dest


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def rename_downloaded_images(directory: Path) -> None:
    """
    Rename images in *directory* to slide_1.png … slide_N.png,
    sorted by their original filename (alphabetical / numeric order).
    """
    extensions = {".png", ".jpg", ".jpeg", ".webp"}
    images = sorted(
        [f for f in directory.iterdir() if f.suffix.lower() in extensions]
    )

    if not images:
        logger.warning("No images found in %s to rename", directory)
        return

    for idx, img in enumerate(images, start=1):
        new_name = directory / f"slide_{idx}.png"
        if img != new_name:
            img.rename(new_name)
            logger.debug("Renamed %s → %s", img.name, new_name.name)

    logger.info("Renamed %d images in %s", len(images), directory)
