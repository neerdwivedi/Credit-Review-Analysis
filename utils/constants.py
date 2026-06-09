"""Application-wide constants for the Credit Review Report Generator."""

from pathlib import Path

# Project root is one level above this utils package
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR = PROJECT_ROOT / "uploads"

# Allowed upload type
ALLOWED_EXTENSIONS = {".pdf"}

# Document type labels returned by the classifier
DOC_TYPE_ANNUAL_REPORT = "annual_report"
DOC_TYPE_INVESTOR_PRESENTATION = "investor_presentation"
DOC_TYPE_CONCALL_TRANSCRIPT = "concall_transcript"
DOC_TYPE_UNKNOWN = "unknown"

# Filename keyword groups for document classification (lowercase matching)
def _annual_report_keywords() -> tuple[str, ...]:
    """Generate FY keyword list dynamically for current and nearby years."""
    from datetime import date
    current_year = date.today().year
    fy_years = range(current_year - 3, current_year + 3)
    fy_keywords = tuple(
        f"fy{str(y)[-2:]}" for y in fy_years
    )
    return (
        "annual",
        "integrated report",
        "annual report",
    ) + fy_keywords


ANNUAL_REPORT_KEYWORDS = _annual_report_keywords()

INVESTOR_PRESENTATION_KEYWORDS = (
    "presentation",
    "investor",
    "q1fy",
    "q2fy",
    "q3fy",
    "q4fy",
    "earnings",
    "ppt",
)

CONCALL_KEYWORDS = (
    "concall",
    "earnings call",
    "transcript",
)

# Heuristic: if average characters per page is below this, PDF may be scanned/image-only
MIN_CHARS_PER_PAGE_FOR_TEXT_PDF = 50
