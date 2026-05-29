"""Flow B — Half-year financials from investor presentations only."""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from services.reconstruction.schema import ExtractionHit

import pdfplumber

from data.metric_aliases import APPROVED_METRICS, TABLE2_PERIODS
from services.reconstruction.document import DocumentContext
from services.reconstruction.extractor_core import (
    FAILURE_REASON,
    extract_metric_on_document,
    prepare_document,
)
from services.reconstruction.schema import missing_record
from utils.constants import DOC_TYPE_INVESTOR_PRESENTATION

logger = logging.getLogger("credit_review")


def _prefer_half_year_hit(
    current: ExtractionHit | None, new_hit: ExtractionHit
) -> bool:
    if current is None:
        return True
    if new_hit.row_score >= 0.99 and current.row_score < 0.95:
        return True
    if "profit and loss" in (new_hit.source_section or "") and new_hit.confidence >= current.confidence:
        return True
    if new_hit.confidence > current.confidence + 0.08:
        return True
    return False


def extract_half_year_financials(
    investor_presentations: list[DocumentContext],
) -> list[dict[str, Any]]:
    """Extract Table 2 (H1FY26 / H1FY25) from investor presentation PDFs only."""
    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    best: dict[tuple[str, str], Any] = {}

    if not investor_presentations:
        for metric in APPROVED_METRICS:
            for period in TABLE2_PERIODS:
                records.append(
                    missing_record(
                        table="half_year",
                        metric=metric,
                        period=period,
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                        failure_reason="no investor presentation uploaded",
                    )
                )
        return records

    for doc in investor_presentations:
        prepare_document(doc, "half_year")
        logger.info("[half_year] Processing %s (%d pages)", doc.filename, len(doc.pages))
        try:
            with pdfplumber.open(io.BytesIO(doc.pdf_bytes)) as pdf:
                for metric in APPROVED_METRICS:
                    hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=metric,
                        periods=TABLE2_PERIODS,
                        table_kind="half_year",
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                    )
                    for period, hit in hits.items():
                        key = (metric, period)
                        cur = best.get(key)
                        if _prefer_half_year_hit(cur, hit):
                            best[key] = hit
        except Exception as exc:
            logger.exception("[half_year] Failed on %s: %s", doc.filename, exc)

    for metric in APPROVED_METRICS:
        for period in TABLE2_PERIODS:
            hit = best.get((metric, period))
            if hit:
                records.append(hit.to_record())
            else:
                records.append(
                    missing_record(
                        table="half_year",
                        metric=metric,
                        period=period,
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                        failure_reason=FAILURE_REASON,
                    )
                )

    logger.info(
        "[half_year] Complete in %.2fs — %d records, %d extracted",
        time.perf_counter() - t0,
        len(records),
        sum(1 for r in records if r["status"] == "extracted"),
    )
    return records
