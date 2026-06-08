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
    periods: tuple[str, ...] | None = None,
    fy_year: int = 2026,
    year_end_month: str = "March",
) -> list[dict[str, Any]]:
    """Extract Table 2 (H1FY26 / H1FY25) from investor presentation PDFs only."""
    from data.metric_aliases import (
        TABLE2_PERIODS as _DEFAULT_PERIODS,
        get_quarter_periods,
    )
    if periods is None:
        periods = _DEFAULT_PERIODS
    QUARTER_PERIODS = get_quarter_periods(fy_year, year_end_month)

    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    best: dict[tuple[str, str], Any] = {}

    if not investor_presentations:
        for metric in APPROVED_METRICS:
            for period in periods:
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
                        periods=periods,
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

    # Single Gemini vision call for ALL missing H1 metrics at once
    vision_key = None
    for doc in investor_presentations:
        if getattr(doc, "vision_api_key", None):
            vision_key = doc.vision_api_key
            break

    if False and vision_key:
        still_missing = [
            metric for metric in APPROVED_METRICS
            if not any(
                best.get((metric, period))
                for period in TABLE2_PERIODS
            )
        ]

        if still_missing and investor_presentations:
            logger.info(
                "[half_year] Vision fallback for %d missing: %s",
                len(still_missing), still_missing,
            )
            doc = investor_presentations[0]
            from services.vision_extractor import vision_extract_for_document

            candidate_pages = []
            for page_num, norm in doc.norm_text_by_page.items():
                score = 0
                if "profit and loss" in norm or "h1fy26" in norm:
                    score += 10
                if "balance sheet" in norm or "total assets" in norm:
                    score += 8
                if "h1fy25" in norm or "half year" in norm:
                    score += 5
                if score > 0:
                    candidate_pages.append((score, page_num))

            candidate_pages.sort(reverse=True)
            top_pages = [p for _, p in candidate_pages[:4]]

            if top_pages:
                time.sleep(4)
                vision_results = vision_extract_for_document(
                    pdf_bytes=doc.pdf_bytes,
                    missing_metrics=still_missing,
                    candidate_pages=top_pages,
                    api_key=vision_key,
                    provider="gemini",
                    table_kind="half_year",
                )
                for (metric, period), val in vision_results.items():
                    key = (metric, period)
                    if key not in best:
                        from services.reconstruction.schema import ExtractionHit
                        hit = ExtractionHit(
                            table="half_year",
                            metric=metric,
                            period=period,
                            value_original=val,
                            unit="crore",
                            value_crore=val,
                            page_number=0,
                            source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                            source_file=doc.filename,
                            source_section="gemini_vision",
                            confidence=0.80,
                            row_label=metric,
                        )
                        best[key] = hit
                        logger.info(
                            "[half_year] Vision found %s %s = %s",
                            metric, period, val,
                        )

    for metric in APPROVED_METRICS:
        for period in periods:
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
