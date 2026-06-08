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

def _preferred_fy_for_period(period: str) -> int | None:
    import re
    m = re.search(r"31\.03\.(20\d{2})", period)
    if m:
        year = int(m.group(1))
        # Preferred source is the NEXT year's report
        # e.g. 31.03.2023 → prefer FY2024 report (has 2023 as comparative)
        #      31.03.2024 → prefer FY2025 report (has 2024 as comparative)
        #      31.03.2025 → prefer FY2026 report OR FY2025 report
        return year + 1
    return None


def _sort_annual_docs(docs: list[DocumentContext]) -> list[DocumentContext]:
    return sorted(
        docs,
        key=lambda d: d.fiscal_year_hint or 0,
        reverse=True,
    )


def _should_prefer_hit(
    current,
    new_hit,
    period: str,
    doc_fy: int | None,
) -> bool:
    if current is None:
        return True

    preferred_fy = _preferred_fy_for_period(period)

    # Prefer hit from the correct source document
    if doc_fy and preferred_fy:
        cur_fy = _fy_from_filename(
            getattr(current, "source_file", "") or ""
        )
        # New hit is from preferred source, current is not
        if doc_fy == preferred_fy and cur_fy != preferred_fy:
            return True
        # New hit is from worse source than current
        if cur_fy == preferred_fy and doc_fy != preferred_fy:
            return False

    # Standard confidence comparison
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
    preferred_fy = _preferred_fy_for_period(period)
    primary = [d for d in docs if d.fiscal_year_hint == preferred_fy]
    secondary = [
        d for d in docs
        if d.fiscal_year_hint and d.fiscal_year_hint != preferred_fy
    ]
    rest = [d for d in docs if not d.fiscal_year_hint]
    return (
        primary +
        sorted(secondary, key=lambda d: d.fiscal_year_hint or 0, reverse=True) +
        rest
    )


def extract_yearly_financials(
    annual_reports: list[DocumentContext],
    periods: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """
    Extract Table 1 (yearly) from annual report PDFs only.

    FY25 values prefer FY25 AR; FY24/FY23 prefer FY24 AR then comparative columns in FY25 AR.
    """
    from data.metric_aliases import TABLE1_PERIODS as _DEFAULT_PERIODS
    if periods is None:
        periods = _DEFAULT_PERIODS

    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    docs = _sort_annual_docs(annual_reports)

    if not docs:
        for metric in APPROVED_METRICS:
            for period in periods:
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
                        periods=periods,
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

    # Single Gemini vision call for ALL missing yearly metrics
    vision_key = None
    for doc in docs:
        if getattr(doc, "vision_api_key", None):
            vision_key = doc.vision_api_key
            break

    if False and vision_key:
        still_missing = [
            metric for metric in APPROVED_METRICS
            if not any(
                best.get((metric, period))
                for period in periods
            )
        ]

        if still_missing and docs:
            logger.info(
                "[yearly] Vision fallback for %d missing metrics",
                len(still_missing),
            )
            doc = docs[0]
            from services.vision_extractor import vision_extract_for_document

            candidate_pages = []
            for page_num in doc.standalone_page_set:
                norm = doc.norm_text_by_page.get(page_num, "")
                score = 0
                if "profit and loss" in norm:
                    score += 10
                if "balance sheet" in norm:
                    score += 8
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
                    table_kind="yearly",
                )
                for (metric, period), val in vision_results.items():
                    key = (metric, period)
                    if key not in best:
                        from services.reconstruction.schema import ExtractionHit
                        hit = ExtractionHit(
                            table="yearly",
                            metric=metric,
                            period=period,
                            value_original=val,
                            unit="crore",
                            value_crore=val,
                            page_number=0,
                            source_document=DOC_TYPE_ANNUAL_REPORT,
                            source_file=doc.filename,
                            source_section="gemini_vision",
                            confidence=0.80,
                            row_label=metric,
                        )
                        best[key] = hit
                        logger.info(
                            "[yearly] Vision found %s %s = %s",
                            metric, period, val,
                        )

    for metric in APPROVED_METRICS:
        for period in periods:
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

    # ===== DEBUG START =====
    print("\n===== ALL PERIODS IN BEST DICT =====")
    all_periods_found = sorted(set(p for _, p in best.keys()))
    for p in all_periods_found:
        print(repr(p))

    print("\n===== ALL FY23 HITS =====")
    found_any = False
    for (metric, period), hit in best.items():
        if "2023" in str(period) or "23" in str(period).lower():
            print(
                f"metric={metric} | "
                f"period={repr(period)} | "
                f"value={hit.value_crore} | "
                f"source={hit.source_file}"
            )
            found_any = True
    if not found_any:
        print("NO FY23 HITS FOUND AT ALL")
    print("=====================================\n")
    # ===== DEBUG END =====

    # Calculate NII = Interest Earned - Interest Expended
    # for banks where NII is not a direct line item
    interest_earned = {}
    interest_expended = {}

    for rec in records:
        if rec.get("status") == "extracted":
            metric = rec.get("metric", "")
            period = rec.get("period", "")
            val = rec.get("value_crore")
            if metric == "Interest Earned" and val:
                interest_earned[period] = float(val)
            elif metric == "Interest Expended" and val:
                interest_expended[period] = float(val)

    # Replace missing NII records with calculated values
    for i, rec in enumerate(records):
        if rec.get("metric") == "NII" and rec.get("status") == "missing":
            period = rec.get("period", "")
            earned = interest_earned.get(period)
            expended = interest_expended.get(period)
            if earned is not None and expended is not None:
                nii_val = earned - expended
                from services.normalizer import format_crore_display
                records[i] = {
                    **rec,
                    "value_crore": nii_val,
                    "approved_value": nii_val,
                    "display_value": format_crore_display(nii_val),
                    "status": "extracted",
                    "source_section": "calculated_interest_earned_minus_expended",
                    "confidence": 0.90,
                    "failure_reason": None,
                    "notes": (
                        f"NII calculated: Interest Earned "
                        f"({format_crore_display(earned)}) - "
                        f"Interest Expended "
                        f"({format_crore_display(expended)})"
                    ),
                }
                logger.info(
                    "[yearly] NII calculated for %s: %s cr",
                    period, format_crore_display(nii_val),
                )

    logger.info(
        "[yearly] Complete in %.2fs — %d records, %d extracted",
        time.perf_counter() - t0,
        len(records),
        sum(1 for r in records if r["status"] == "extracted"),
    )
    return records
