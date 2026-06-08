"""V2 extraction record schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from data.metric_aliases import NOT_DISCLOSED
from services.normalizer import format_crore_display, is_ratio_metric

TableKind = Literal["yearly", "half_year"]
RecordStatus = Literal["extracted", "missing"]


@dataclass
class ExtractionHit:
    """Single extracted cell with provenance."""

    table: TableKind
    metric: str
    period: str
    value_original: float
    unit: str
    value_crore: float
    page_number: int
    source_document: str
    source_file: str
    source_section: str
    confidence: float
    row_label: str = ""
    column_header: str = ""
    row_score: float = 0.0
    column_score: float = 0.0
    from_table: bool = True
    used_text_fallback: bool = False
    standalone_section: bool = False
    preferred_source: bool = False
    unit_detected: bool = True
    raw_text: str = ""
    raw_text_unit: str = ""

    def to_record(self) -> dict[str, Any]:
        if is_ratio_metric(self.metric):
            display = format_crore_display(self.value_original)
            stored_crore = self.value_original
        else:
            display = format_crore_display(self.value_crore)
            stored_crore = self.value_crore

        return {
            "table": self.table,
            "metric": self.metric,
            "period": self.period,
            "value_original": self.value_original,
            "unit": self.unit,
            "value_crore": stored_crore,
            "display_value": display,
            "page_number": self.page_number,
            "source_document": self.source_document,
            "source_file": self.source_file,
            "source_filename": self.source_file,
            "source_section": self.source_section,
            "confidence": round(self.confidence, 2),
            "status": "extracted",
            "failure_reason": None,
            "from_table": self.from_table,
            "row_label": self.row_label,
            "column_header": self.column_header,
            "raw_text": self.raw_text,
            "raw_text_unit": self.raw_text_unit,
        }


def missing_record(
    *,
    table: TableKind,
    metric: str,
    period: str,
    source_document: str,
    failure_reason: str,
) -> dict[str, Any]:
    return {
        "table": table,
        "metric": metric,
        "period": period,
        "value_original": None,
        "unit": None,
        "value_crore": None,
        "display_value": NOT_DISCLOSED,
        "page_number": None,
        "source_document": source_document,
        "source_file": None,
        "source_filename": None,
        "source_section": None,
        "confidence": 0.0,
        "status": "missing",
        "failure_reason": failure_reason,
        "from_table": False,
    }


def compute_confidence(
    *,
    standalone_section: bool,
    preferred_source: bool,
    row_score: float,
    column_score: float,
    from_table: bool,
    unit_detected: bool,
    used_text_fallback: bool,
) -> float:
    score = 0.0
    if standalone_section:
        score += 0.30
    if preferred_source:
        score += 0.20
    if row_score >= 0.99:
        score += 0.20
    elif row_score >= 0.75:
        score += 0.12
    if column_score >= 0.99:
        score += 0.15
    elif column_score >= 0.75:
        score += 0.10
    if from_table:
        score += 0.10
    if unit_detected:
        score += 0.05
    if used_text_fallback:
        score = min(score, 0.65)
    if not unit_detected:
        score -= 0.10
    if not preferred_source:
        score -= 0.20
    return max(0.0, min(1.0, score))
