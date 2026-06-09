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
from services.llm_extractor import (
    llm_extract_document,
    is_groq_available,
)
from data.financial_logic import derive_h1_values, METRIC_TYPE, MetricType
from data.metric_aliases import get_quarter_periods

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


def _derived_hits_to_best(
    derived: dict[str, dict],
    *,
    h1_period: str,
    source_file: str,
    source_document: str,
    current_best: dict[tuple[str, str], Any],
) -> None:
    """
    Write derived H1 values into current_best dict in-place.
    Only writes if no existing hit or derived confidence is higher.
    """
    from services.reconstruction.schema import ExtractionHit
    from services.normalizer import is_ratio_metric

    for metric, info in derived.items():
        value = info.get("value")
        if value is None:
            continue
        confidence = float(info.get("confidence", 0.8))
        method = info.get("method", "derived")
        key = (metric, h1_period)
        cur = current_best.get(key)
        if cur is not None and cur.confidence >= confidence:
            continue
        hit = ExtractionHit(
            table="half_year",
            metric=metric,
            period=h1_period,
            value_original=value,
            unit="percent" if is_ratio_metric(metric) else "crore",
            value_crore=value,
            page_number=0,
            source_document=source_document,
            source_file=source_file,
            source_section=f"derived:{method}",
            confidence=confidence,
            row_label=metric,
            column_header=h1_period,
            row_score=1.0,
            column_score=1.0,
            from_table=False,
            standalone_section=True,
            preferred_source=True,
            unit_detected=True,
            raw_text=str(value),
            raw_text_unit="crore",
        )
        current_best[key] = hit


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

    # ── LLM extraction pass ───────────────────────────────────────────────
    groq_key = None
    for doc in investor_presentations:
        k = getattr(doc, "vision_api_key", None) or ""
        if is_groq_available(k):
            groq_key = k
            break

    if groq_key:
        for doc in investor_presentations:
            prepare_document(doc, "half_year")
            llm_hits = llm_extract_document(
                doc.pages,
                groq_api_key=groq_key,
                table_kind="half_year",
                source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                source_file=doc.filename,
                allowed_periods=periods,
            )
            for hit in llm_hits:
                key = (hit.metric, hit.period)
                cur = best.get(key)
                if cur is None or hit.confidence > cur.confidence:
                    best[key] = hit
        logger.info(
            "[half_year] LLM pre-pass: %d values seeded",
            sum(1 for v in best.values() if v is not None),
        )
    # ── end LLM pass ─────────────────────────────────────────────────────

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

        # ── Q1/Q2 derivation pass ─────────────────────────────────────
        # Extract Q1 and Q2 separately, then use financial_logic rules
        # to build correct H1 values (flow=Q1+Q2, snapshot=Q2, ratios=Q2).
        # This is company-agnostic — works for any bank/NBFC/HFC.
        try:
            QUARTER_PERIODS_FY = get_quarter_periods(fy_year, year_end_month)
            # e.g. {"Q1FY26": "...", "Q2FY26": "...", "Q1FY25": "...", "Q2FY25": "..."}

            for h1_label, (q1_label, q2_label) in [
                (f"H1FY{fy_year % 100:02d}",
                 (f"Q1FY{fy_year % 100:02d}", f"Q2FY{fy_year % 100:02d}")),
                (f"H1FY{(fy_year-1) % 100:02d}",
                 (f"Q1FY{(fy_year-1) % 100:02d}", f"Q2FY{(fy_year-1) % 100:02d}")),
            ]:
                q1_vals: dict[str, float | None] = {}
                q2_vals: dict[str, float | None] = {}

                for metric in APPROVED_METRICS:
                    q1_hit = best.get((metric, q1_label))
                    q2_hit = best.get((metric, q2_label))
                    q1_vals[metric] = q1_hit.value_crore if q1_hit else None
                    q2_vals[metric] = q2_hit.value_crore if q2_hit else None

                # Only derive if we have at least some quarterly data
                has_data = any(
                    v is not None
                    for v in list(q1_vals.values()) + list(q2_vals.values())
                )
                if not has_data:
                    continue

                derived = derive_h1_values(q1_vals, q2_vals)
                _derived_hits_to_best(
                    derived,
                    h1_period=h1_label,
                    source_file=doc.filename,
                    source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                    current_best=best,
                )
                logger.info(
                    "[half_year] Q1/Q2 derivation for %s: %d metrics derived",
                    h1_label,
                    sum(1 for v in derived.values() if v.get("value") is not None),
                )
        except Exception as exc:
            logger.warning("[half_year] Q1/Q2 derivation failed: %s", exc)
        # ── end derivation pass ───────────────────────────────────────

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
