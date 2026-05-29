"""Centralized logging configuration for the application."""

import logging
from pathlib import Path

from utils.constants import OUTPUT_DIR


def setup_logger(name: str = "credit_review") -> logging.Logger:
    """
    Create and configure a logger that writes to console and a log file.

    Args:
        name: Logger name (typically module or app name).

    Returns:
        Configured logger instance.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_file = OUTPUT_DIR / "extraction.log"

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if setup_logger is called more than once
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — INFO and above for cleaner Streamlit sessions
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler — DEBUG for full audit trail
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
