#!/usr/bin/env python3
"""
main.py — Entry point for the Instagram Carousel Automation Tool.

Usage:
    python main.py "your topic"
    python main.py "your topic" --headless
    python main.py --batch topics.txt
    python main.py "your topic" --skip-upload   # CSV generation only

Environment:
    Copy .env.example → .env and fill in credentials before running.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing any module that reads env vars
load_dotenv()

from utils import setup_logging
from generator import generate_slides


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="carousel",
        description="Generate & upload Instagram carousels to Contentdrips.",
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "topic",
        nargs="?",
        help="Single carousel topic (e.g. \"The benefits of daily journaling\")",
    )
    input_group.add_argument(
        "--batch",
        metavar="FILE",
        help="Path to a text file with one topic per line",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Show the browser window during automation",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Only generate the CSV; do not upload to Contentdrips",
    )
    parser.add_argument(
        "--template",
        default=None,
        metavar="NAME",
        help="Partial template name to select (case-insensitive substring match)",
    )
    parser.add_argument(
        "--output",
        default="output",
        metavar="DIR",
        help="Base output directory (default: ./output)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Max retries for LLM and upload steps (default: 3)",
    )

    return parser


# ---------------------------------------------------------------------------
# Core workflow
# ---------------------------------------------------------------------------

def process_topic(
    topic: str,
    *,
    headless: bool,
    skip_upload: bool,
    template: str | None,
    output_base: str,
    retries: int,
) -> None:
    logger = logging.getLogger("carousel.main")
    topic = topic.strip()
    if not topic:
        logger.error("Empty topic — skipping")
        return

    logger.info("=" * 60)
    logger.info("TOPIC: %s", topic)
    logger.info("=" * 60)

    # Step 1: Generate slides via LLM
    logger.info("Generating slides via LLM…")
    slides, _ = generate_slides(topic, max_retries=retries)
    logger.info("Generated %d slides", len(slides))

    # Step 2: Render slides to PNG
    from renderer import render_slides
    from pathlib import Path
    dest_dir = Path(output_base) / topic[:50].replace("/", "_")
    dest_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Rendering slides → %s", dest_dir)
    png_paths, run_id = render_slides(slides, renders_base=str(dest_dir))

    logger.info("Done! %d image(s) saved to %s", len(png_paths), dest_dir)


def generate_carousel(
    topic: str,
    headless: bool = True,
    skip_upload: bool = False,
    template: str | None = None,
    output_base: str = "output",
    retries: int = 3,
) -> None:
    """
    Callable API for programmatic use.

    Example
    -------
    from main import generate_carousel
    generate_carousel("The benefits of daily journaling")
    """
    load_dotenv()
    setup_logging()
    process_topic(
        topic,
        headless=headless,
        skip_upload=skip_upload,
        template=template,
        output_base=output_base,
        retries=retries,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger("carousel.main")

    # Resolve topics list
    topics: list[str] = []

    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            logger.error("Batch file not found: %s", batch_file)
            return 1
        topics = [
            line.strip()
            for line in batch_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        logger.info("Batch mode: %d topic(s) loaded from %s", len(topics), batch_file)

    elif args.topic:
        topics = [args.topic]

    else:
        # Interactive fallback
        try:
            topic = input("Enter carousel topic: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            logger.error("No topic provided. Exiting.")
            return 1
        if not topic:
            logger.error("Empty topic. Exiting.")
            return 1
        topics = [topic]

    errors: list[str] = []

    for topic in topics:
        try:
            process_topic(
                topic,
                headless=args.headless,
                skip_upload=args.skip_upload,
                template=args.template,
                output_base=args.output,
                retries=args.retries,
            )
        except Exception as exc:
            logger.error("Failed for topic %r: %s", topic, exc, exc_info=True)
            errors.append(f"{topic!r}: {exc}")

    if errors:
        logger.error("%d topic(s) failed:", len(errors))
        for e in errors:
            logger.error("  • %s", e)
        return 1

    return 0


# ---------------------------------------------------------------------------
# Web server entry point (Railway / Docker)
# ---------------------------------------------------------------------------
# Import the FastAPI app so uvicorn can find it as "main:app".
# This does NOT interfere with the CLI code above.
from app import app  # noqa: F401, E402

def _serve() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("=== MAIN.PY IS RUNNING ===")
    print(f"Starting server on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # If PORT is in the environment we are running as a web server (Railway).
    # Otherwise fall through to the CLI.
    if os.environ.get("PORT"):
        _serve()
    else:
        sys.exit(main())
