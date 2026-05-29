"""Flow A — Yearly financials from annual reports only."""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from services.reconstruction.schema import ExtractionHit

import pdfplumber

from data.metric_aliases import APPROVED_METRICS, TABLE1_PERIODS
from services.reconstruction.document import DocumentContext
from services.reconstruction.extractor_core import (
    FAILURE_REASON,
    extract_metric_on_document,
    prepare_document,
)
from services.reconstruction.schema import missing_record
from utils.constants import DOC_TYPE_ANNUAL_REPORT

logger = logging.getLogger("credit_review")

# Period -> preferred fiscal year of source annual report
PERIOD_PREFERRED_FY: dict[str, int] = {
    "31.03.2025": 2025,
    "31.03.2024": 2024,
    "31.03.2023": 2023,
}


def _sort_annual_docs(docs: list[DocumentContext]) -> list[DocumentContext]:
    return sorted(
        docs,
        key=lambda d: d.fiscal_year_hint or 0,
        reverse=True,
    )


def _should_prefer_hit(
    current: ExtractionHit | None,
    new_hit: ExtractionHit,
    period: str,
    doc_fy: int | None,
) -> bool:
    """Prefer hits from the annual report that matches the period's fiscal year."""
    if current is None:
        return True
    preferred_fy = PERIOD_PREFERRED_FY.get(period)
    if doc_fy == preferred_fy and getattr(current, "source_file", ""):
        cur_fy = _fy_from_filename(current.source_file)
        if cur_fy and cur_fy != preferred_fy:
            return True
    if new_hit.confidence > current.confidence + 0.05:
        return True
    if new_hit.confidence >= current.confidence and new_hit.standalone_section:
        return not current.standalone_section
    return False


def _fy_from_filename(filename: str) -> int | None:
    import re

    name = filename.lower()
    m = re.search(r"\b(?:fy\s*)?['`]?(2[4-6])\b", name)
    if m:
        return 2000 + int(m.group(1))
    if " 25" in name or "25.pdf" in name:
        return 2025
    if " 24" in name or "24.pdf" in name:
        return 2024
    return None


def _doc_order_for_period(
    docs: list[DocumentContext],
    period: str,
) -> list[DocumentContext]:
    preferred_fy = PERIOD_PREFERRED_FY.get(period, 2025)
    primary = [d for d in docs if d.fiscal_year_hint == preferred_fy]
    secondary = [d for d in docs if d.fiscal_year_hint and d.fiscal_year_hint != preferred_fy]
    rest = [d for d in docs if not d.fiscal_year_hint]
    return primary + sorted(secondary, key=lambda d: d.fiscal_year_hint or 0, reverse=True) + rest


def extract_yearly_financials(
    annual_reports: list[DocumentContext],
) -> list[dict[str, Any]]:
    """
    Extract Table 1 (yearly) from annual report PDFs only.

    FY25 values prefer FY25 AR; FY24/FY23 prefer FY24 AR then comparative columns in FY25 AR.
    """
    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    docs = _sort_annual_docs(annual_reports)

    if not docs:
        for metric in APPROVED_METRICS:
            for period in TABLE1_PERIODS:
                records.append(
                    missing_record(
                        table="yearly",
                        metric=metric,
                        period=period,
                        source_document=DOC_TYPE_ANNUAL_REPORT,
                        failure_reason="no annual report uploaded",
                    )
                )
        return records

    best: dict[tuple[str, str], Any] = {}

    for doc in docs:
        prepare_document(doc, "yearly")
        logger.info(
            "[yearly] Processing %s (fy_hint=%s, standalone_pages=%d)",
            doc.filename,
            doc.fiscal_year_hint,
            len(doc.standalone_pages),
        )
        try:
            with pdfplumber.open(io.BytesIO(doc.pdf_bytes)) as pdf:
                for metric in APPROVED_METRICS:
                    hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=metric,
                        periods=TABLE1_PERIODS,
                        table_kind="yearly",
                        source_document=DOC_TYPE_ANNUAL_REPORT,
                    )
                    for period, hit in hits.items():
                        key = (metric, period)
                        cur = best.get(key)
                        if _should_prefer_hit(
                            cur, hit, period, doc.fiscal_year_hint
                        ):
                            best[key] = hit
        except Exception as exc:
            logger.exception("[yearly] Failed on %s: %s", doc.filename, exc)

    for metric in APPROVED_METRICS:
        for period in TABLE1_PERIODS:
            key = (metric, period)
            hit = best.get(key)
            if hit:
                records.append(hit.to_record())
            else:
                records.append(
                    missing_record(
                        table="yearly",
                        metric=metric,
                        period=period,
                        source_document=DOC_TYPE_ANNUAL_REPORT,
                        failure_reason=FAILURE_REASON,
                    )
                )

    logger.info(
        "[yearly] Complete in %.2fs — %d records, %d extracted",
        time.perf_counter() - t0,
        len(records),
        sum(1 for r in records if r["status"] == "extracted"),
    )
    return records
