"""PDF reading and table preview using PyMuPDF and pdfplumber."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pdfplumber

from utils.constants import MIN_CHARS_PER_PAGE_FOR_TEXT_PDF

logger = logging.getLogger("credit_review")


def extract_pages_from_pdf(pdf_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Extract text from every page of a PDF using PyMuPDF.

    Args:
        pdf_bytes: Raw PDF file content.
        filename: Original filename (used in logs).

    Returns:
        List of dicts: [{"page": 1, "text": "..."}, ...]
        One entry per page; empty string if no text on that page.
    """
    pages: list[dict[str, Any]] = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.error("Failed to open PDF '%s': %s", filename, exc)
        raise ValueError(f"Could not read PDF '{filename}': {exc}") from exc

    logger.info("Document loaded: %s (%d pages)", filename, len(doc))

    for page_index in range(len(doc)):
        page = doc[page_index]
        # page_index is 0-based; we store 1-based page numbers for humans
        text = page.get_text("text") or ""
        pages.append({"page": page_index + 1, "text": text.strip()})

    doc.close()
    logger.info("Pages extracted: %s — %d pages", filename, len(pages))

    return pages


def preview_tables_in_pdf(pdf_bytes: bytes, filename: str) -> list[dict[str, int]]:
    """
    Scan each page with pdfplumber and count tables (preview only, no full parse).

    Args:
        pdf_bytes: Raw PDF file content.
        filename: Original filename (used in logs).

    Returns:
        List of {"page": N, "table_count": M} for pages that have at least one table,
        plus pages with zero tables are still included with table_count=0.
    """
    results: list[dict[str, int]] = []
    total_tables = 0

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                tables = page.find_tables() or []
                count = len(tables)
                total_tables += count
                results.append({"page": page_index + 1, "table_count": count})
    except Exception as exc:
        logger.error("Table preview failed for '%s': %s", filename, exc)
        raise ValueError(f"Table preview failed for '{filename}': {exc}") from exc

    logger.info(
        "Table preview result: %s — %d tables across %d pages",
        filename,
        total_tables,
        len(results),
    )

    return results


def total_table_count(table_preview: list[dict[str, int]]) -> int:
    """Sum table counts from a table preview result."""
    return sum(row.get("table_count", 0) for row in table_preview)


def total_extracted_characters(pages: list[dict[str, Any]]) -> int:
    """Count total characters across all extracted page text."""
    return sum(len(p.get("text", "")) for p in pages)


def is_likely_scanned_pdf(pages: list[dict[str, Any]]) -> bool:
    """
    Heuristic: very little text per page often means a scanned/image PDF.

    Returns True if average chars per page is below the configured threshold.
    """
    if not pages:
        return True

    total_chars = total_extracted_characters(pages)
    avg_chars = total_chars / len(pages)
    return avg_chars < MIN_CHARS_PER_PAGE_FOR_TEXT_PDF


def has_no_extracted_text(pages: list[dict[str, Any]]) -> bool:
    """True if no meaningful text was extracted from any page."""
    return total_extracted_characters(pages) == 0


def save_uploaded_pdf(pdf_bytes: bytes, filename: str, data_dir: Path) -> Path:
    """
    Persist uploaded PDF to the data directory for audit/debugging.

    If the same filename already exists, appends _1, _2, etc. so uploads are not lost.

    Args:
        pdf_bytes: Raw file content.
        filename: Safe original filename.
        data_dir: Target directory.

    Returns:
        Path to saved file.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = data_dir / filename
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = data_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    dest.write_bytes(pdf_bytes)
    logger.debug("Saved upload to %s", dest)
    return dest
