"""
Normalize periods, parse numbers, detect units, and convert currency values to Rs crore.
"""

from __future__ import annotations

import re
from typing import Literal

from data.metric_aliases import (
    RATIO_METRICS,
    TABLE1_PERIOD_ALIASES,
    TABLE1_PERIODS,
    TABLE2_PERIOD_ALIASES,
    TABLE2_PERIODS,
    TABLE2_REJECT_PATTERNS,
)

UnitType = Literal["crore", "lakh", "thousand", "million", "unknown", "percent"]

# Loose number pattern: optional minus, digits with optional commas/decimals, optional %
_NUMBER_RE = re.compile(
    r"^\s*\(?\s*(-?[\d,]+(?:\.\d+)?)\s*\)?\s*%?\s*$"
)


def normalize_text(value: str | None) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise for matching."""
    if not value:
        return ""
    text = str(value).lower().strip()
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def canonicalize_table1_period(header: str | None) -> str | None:
    """
    Map a table column header to an allowed March year-end period, or None if invalid.

    Accepts a wide variety of bank/NBFC P&L and balance-sheet headers:
      31.03.2025 / 31/03/2025 / 31-03-2025
      March 31, 2025 / 31 March 2025 / 31st March 2025
      March 2025 / Mar 2025
      FY25 / FY 25 / FY 2025 / FY24-25 / 2024-25
      "Year ended 31.03.2025", "As at 31.03.2025", "For the year ended ..."
    Returns the canonical "31.03.YYYY" string or None.
    """
    if not header:
        return None
    norm = normalize_text(header)

    # Substring rejection for any Table-2 quarter wording leaking into Table 1
    if "h1" in norm or "half year" in norm or "half-year" in norm:
        return None

    if norm in TABLE1_PERIOD_ALIASES:
        return TABLE1_PERIOD_ALIASES[norm]

    # Numeric dd-mm-yyyy
    m = re.search(r"\b31[\s\./\-]+0?3[\s\./\-]+(2023|2024|2025)\b", norm)
    if m:
        return f"31.03.{m.group(1)}"

    # "31 March 2025" / "31st March 2025" / "March 31, 2025"
    m = re.search(r"\b31\s*(?:st)?\s*mar(?:ch)?\b[\s\.,-]*(2023|2024|2025)\b", norm)
    if m:
        return f"31.03.{m.group(1)}"
    m = re.search(r"\bmar(?:ch)?\s*31[\s\.,-]*(2023|2024|2025)\b", norm)
    if m:
        return f"31.03.{m.group(1)}"

    # "March 2025" or "Mar 2025" (no day)
    m = re.search(r"\bmar(?:ch)?\b[\s\.,-]*(2023|2024|2025)\b", norm)
    if m:
        return f"31.03.{m.group(1)}"

    # "FY24-25", "2024-25", "2023-24", "2022-23"
    m = re.search(r"\b(?:fy\s*)?(202[2-4])\s*[-/]\s*(2[3-5])\b", norm)
    if m:
        return f"31.03.20{m.group(2)}"

    # FY25 / FY 25 / FY'25 / FY 2025
    m = re.search(r"\bfy\s*['`]?\s*(?:20)?(2[3-5])\b", norm)
    if m:
        return f"31.03.20{m.group(1)}"

    return None


def is_table2_period_rejected(header: str | None) -> bool:
    """True if header looks like quarterly data (Q1/Q2) without half-year context."""
    if not header:
        return False
    norm = normalize_text(header)
    for pattern in TABLE2_REJECT_PATTERNS:
        if pattern in norm:
            # Allow if explicitly half-year / H1 cumulative wording
            if "h1" in norm or "half year" in norm or "half-year" in norm:
                return False
            if "6 month" in norm or "six month" in norm:
                return False
            return True
    return False


def canonicalize_table2_period(header: str | None) -> str | None:
    """
    Map investor presentation column header to H1FY26 / H1FY25, or None if invalid.
    Rejects Q1/Q2-only columns per business rules.
    """
    if not header:
        return None
    if is_table2_period_rejected(header):
        return None

    norm = normalize_text(header)
    if norm in TABLE2_PERIOD_ALIASES:
        return TABLE2_PERIOD_ALIASES[norm]

    if re.search(r"\bh1\s*fy\s*26\b", norm) or re.search(r"\bh1fy26\b", norm):
        return "H1FY26"
    if re.search(r"\bh1\s*fy\s*25\b", norm) or re.search(r"\bh1fy25\b", norm):
        return "H1FY25"

    # H1 2026 / H1 2025 — pure year notation
    if re.search(r"\bh1\s*2026\b", norm):
        return "H1FY26"
    if re.search(r"\bh1\s*2025\b", norm):
        return "H1FY25"

    # Half year ended September 2025 / 2024
    if "half" in norm and "sep" in norm and "2025" in norm:
        return "H1FY26"
    if "half" in norm and "sep" in norm and "2024" in norm:
        return "H1FY25"
    if "september 2025" in norm and ("half" in norm or "6 month" in norm or "six month" in norm):
        return "H1FY26"
    if "september 2024" in norm and ("half" in norm or "6 month" in norm or "six month" in norm):
        return "H1FY25"
    if "half year fy26" in norm or "half-year fy26" in norm or "halfyear fy26" in norm:
        return "H1FY26"
    if "half year fy25" in norm or "half-year fy25" in norm or "halfyear fy25" in norm:
        return "H1FY25"

    return None


def detect_unit(text: str) -> UnitType:
    """
    Detect reporting unit from table title, footnote, or page text.
    Never assumes crore if not stated.
    """
    norm = normalize_text(text)
    if "%" in text and ("ratio" in norm or "roa" in norm or "roe" in norm or "car" in norm):
        return "percent"

    # Crore — many spellings: "in crore", "₹ crore", "Rs. crore", "Rs Cr", "(₹ Cr)"
    if (
        re.search(r"\b(in\s+)?crores?\b", norm)
        or re.search(r"\b(?:rs\.?|₹|inr)\s*(?:in\s+)?crores?\b", norm)
        or re.search(r"\brs\.?\s*cr\b", norm)
        or re.search(r"₹\s*cr\b", norm)
        or re.search(r"₹cr\b", norm)
        or "(₹ cr)" in norm
        or "(rs cr)" in norm
        or norm.endswith(" cr") and ("₹" in norm or "rs" in norm)
    ):
        return "crore"
    if re.search(r"\b(in\s+)?lakhs?\b", norm) or re.search(r"₹\s*(?:in\s+)?lakhs?", norm):
        return "lakh"
    if re.search(r"\b(in\s+)?thousands?\b", norm):
        return "thousand"
    if re.search(r"\b(in\s+)?millions?\b", norm) or re.search(r"₹\s*(?:in\s+)?millions?", norm):
        return "million"

    return "unknown"


def parse_numeric_value(raw: str | None) -> float | None:
    """
    Parse a cell value to float. Returns None if not a valid number.
    Does not invent or estimate missing values.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"-", "—", "–", "na", "n/a", "nil", "*"}:
        return None

    match = _NUMBER_RE.match(text.replace(" ", ""))
    if not match:
        # Try extracting first number from noisy cell
        inner = re.search(r"\(?\s*(-?[\d,]+(?:\.\d+)?)\s*\)?", text.replace(" ", ""))
        if not inner:
            return None
        num_str = inner.group(1)
    else:
        num_str = match.group(1)

    num_str = num_str.replace(",", "")
    try:
        value = float(num_str)
    except ValueError:
        return None

    if "(" in text and ")" in text and value > 0:
        value = -value
    return value


def convert_to_crore(value: float, unit: UnitType) -> float | None:
    """
    Convert currency amounts to Rs crore per strict rules.
    Ratios and percent are returned unchanged by caller logic.
    """
    if unit == "crore":
        return value
    if unit == "lakh":
        return value / 100.0
    if unit == "thousand":
        return value / 10_000.0
    if unit == "million":
        return value * 0.1
    return None


def format_crore_display(value: float | None) -> str:
    """Format crore value for display; preserve precision for audit."""
    if value is None:
        return ""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def match_metric_in_text(cell_text: str, aliases: list[str]) -> bool:
    """True if cell text matches any alias (case-insensitive, partial line match)."""
    norm = normalize_text(cell_text)
    for alias in aliases:
        alias_norm = normalize_text(alias)
        if alias_norm == norm or alias_norm in norm:
            return True
    return False


def allowed_periods_for_table(table_id: int) -> tuple[str, ...]:
    if table_id == 1:
        return TABLE1_PERIODS
    return TABLE2_PERIODS


def is_ratio_metric(metric: str) -> bool:
    return metric in RATIO_METRICS
