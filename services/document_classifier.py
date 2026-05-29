"""Classify uploaded PDFs by filename keywords."""

from __future__ import annotations

import logging

from utils.constants import (
    ANNUAL_REPORT_KEYWORDS,
    CONCALL_KEYWORDS,
    DOC_TYPE_ANNUAL_REPORT,
    DOC_TYPE_CONCALL_TRANSCRIPT,
    DOC_TYPE_INVESTOR_PRESENTATION,
    DOC_TYPE_UNKNOWN,
    INVESTOR_PRESENTATION_KEYWORDS,
)

logger = logging.getLogger("credit_review")


def classify_document(filename: str, upload_slot: str | None = None) -> str:
    """
    Classify a document using filename keywords.

    The upload_slot hint (annual_report, investor_presentation, concall_transcript)
    is used when filename keywords are ambiguous.

    Args:
        filename: Original uploaded filename.
        upload_slot: Which UI slot the file was uploaded to (optional hint).

    Returns:
        One of: annual_report, investor_presentation, concall_transcript, unknown
    """
    name_lower = filename.lower().replace("_", " ").replace("-", " ")

    def matches(keywords: tuple[str, ...]) -> bool:
        return any(kw in name_lower for kw in keywords)

    # Check investor and concall before annual — filenames like "q2fy26"
    # contain "fy26" which would otherwise match annual keywords.
    if matches(CONCALL_KEYWORDS):
        doc_type = DOC_TYPE_CONCALL_TRANSCRIPT
    elif matches(INVESTOR_PRESENTATION_KEYWORDS):
        doc_type = DOC_TYPE_INVESTOR_PRESENTATION
    elif matches(ANNUAL_REPORT_KEYWORDS):
        doc_type = DOC_TYPE_ANNUAL_REPORT
    elif upload_slot:
        # Fall back to the slot the user chose in the UI
        doc_type = upload_slot
    else:
        doc_type = DOC_TYPE_UNKNOWN

    logger.info("Classification result: %s -> %s", filename, doc_type)
    return doc_type


def display_label(doc_type: str) -> str:
    """Human-readable label for a document type code."""
    labels = {
        DOC_TYPE_ANNUAL_REPORT: "Annual Report",
        DOC_TYPE_INVESTOR_PRESENTATION: "Investor Presentation",
        DOC_TYPE_CONCALL_TRANSCRIPT: "Concall Transcript",
        DOC_TYPE_UNKNOWN: "Unknown",
    }
    return labels.get(doc_type, doc_type)
