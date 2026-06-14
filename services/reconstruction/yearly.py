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
from services.llm_extractor import (
    is_gemini_available,
    llm_extract_document,
)

logger = logging.getLogger("credit_review")

def _preferred_fy_for_period(period: str) -> int | None:
    import re
    m = re.search(r"(20\d{2})$", period)
    if m:
        year = int(m.group(1))
        # Preferred source is the NEXT year's report
        # e.g. 31.03.2023 → prefer FY2024 report (has 2023 as comparative)
        #      31.03.2024 → prefer FY2025 report (has 2024 as comparative)
        #      31.03.2025 → prefer FY2026 report OR FY2025 report
        return year + 1
    return None


def _ensure_docs_indexed(docs: list[DocumentContext]) -> None:
    """Build page indexes before sorting — fy_hint lives on DocumentContext."""
    for doc in docs:
        if not doc.norm_text_by_page:
            doc.build_indexes()


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
    from data.metric_logic import is_obvious_schedule_noise, score_yearly_extraction_hit

    if is_obvious_schedule_noise(new_hit.metric, new_hit):
        return False
    if getattr(new_hit, "source_section", "") == "text_regex":
        return False
    if current is None:
        return True
    if is_obvious_schedule_noise(current.metric, current):
        return True

    preferred_fy = _preferred_fy_for_period(period) or doc_fy
    new_score = score_yearly_extraction_hit(
        new_hit, period=period, preferred_doc_fy=preferred_fy
    )
    cur_score = score_yearly_extraction_hit(
        current, period=period, preferred_doc_fy=preferred_fy
    )
    if new_score > cur_score + 0.03:
        return True
    if new_hit.confidence > current.confidence + 0.05:
        return True
    if new_hit.standalone_section and not current.standalone_section:
        return True
    if new_hit.from_table and not current.from_table:
        return True
    return False


def _fy_from_filename(filename: str) -> int | None:
    import re

    name = filename.lower()
    m = re.search(r"\b(?:fy\s*)?['`]?(2[0-9])\b", name)
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
    import re

    preferred_fy = _preferred_fy_for_period(period)
    m = re.search(r"(20\d{2})$", period)
    period_year = int(m.group(1)) if m else None
    preferred_hints = {
        y for y in (preferred_fy, period_year, (period_year + 1) if period_year else None)
        if y
    }
    primary = [d for d in docs if d.fiscal_year_hint in preferred_hints]
    secondary = [
        d for d in docs
        if d.fiscal_year_hint and d.fiscal_year_hint not in preferred_hints
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
    Extract Table 1 (yearly) from annual reports, or investor presentations
    when no annual report was uploaded.

    FY25 values prefer FY25 AR; FY24/FY23 prefer FY24 AR then comparative columns in FY25 AR.
    """
    from data.metric_aliases import TABLE1_PERIODS as _DEFAULT_PERIODS
    if periods is None:
        periods = _DEFAULT_PERIODS

    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    docs = list(annual_reports)
    _ensure_docs_indexed(docs)
    docs = _sort_annual_docs(docs)

    if not docs:
        for metric in APPROVED_METRICS:
            for period in periods:
                records.append(
                    missing_record(
                        table="yearly",
                        metric=metric,
                        period=period,
                        source_document=DOC_TYPE_ANNUAL_REPORT,
                        failure_reason="no document uploaded for yearly extraction",
                    )
                )
        return records

    best: dict[tuple[str, str], Any] = {}

    gemini_key = None
    for doc in docs:
        k = getattr(doc, "vision_api_key", None) or ""
        if is_gemini_available(k):
            gemini_key = k
            break

    if gemini_key:
        gemini_filled = 0
        for doc in docs:
            source_document = doc.doc_type or DOC_TYPE_ANNUAL_REPORT
            llm_hits = llm_extract_document(
                doc.pages,
                pdf_bytes=doc.pdf_bytes,
                filename=doc.filename,
                api_key=gemini_key,
                fy_hint=doc.fiscal_year_hint,
                table_kind="yearly",
                source_document=source_document,
                source_file=doc.filename,
                allowed_periods=periods,
            )
            for hit in llm_hits:
                key = (hit.metric, hit.period)
                if best.get(key) is None:
                    best[key] = hit
                    gemini_filled += 1
        logger.info("[yearly] Gemini primary: %d values seeded", gemini_filled)

    for metric in APPROVED_METRICS:
        for period in periods:
            if best.get((metric, period)):
                continue
            ordered_docs = _doc_order_for_period(docs, period)
            for doc in ordered_docs:
                prepare_document(doc, "yearly")
                source_document = doc.doc_type or DOC_TYPE_ANNUAL_REPORT
                try:
                    with pdfplumber.open(io.BytesIO(doc.pdf_bytes)) as pdf:
                        hits = extract_metric_on_document(
                            doc,
                            pdf,
                            metric=metric,
                            periods=(period,),
                            table_kind="yearly",
                            source_document=source_document,
                        )
                        hit = hits.get(period)
                        if hit is None:
                            continue
                        key = (metric, period)
                        cur = best.get(key)
                        if _should_prefer_hit(
                            cur, hit, period, doc.fiscal_year_hint
                        ):
                            best[key] = hit
                            break
                except Exception as exc:
                    logger.exception(
                        "[yearly] Failed %s %s on %s: %s",
                        metric,
                        period,
                        doc.filename,
                        exc,
                    )

    if docs:
        logger.info(
            "[yearly] Processed %d annual report(s), fy_hints=%s",
            len(docs),
            [d.fiscal_year_hint for d in docs],
        )

    from data.metric_logic import (
        DERIVATION_COMPONENT_NAMES,
        apply_yearly_derived_values,
        filter_duplicate_cross_period_hits,
        filter_invalid_hits,
        filter_obvious_schedule_noise,
        filter_untrusted_source_hits,
        merge_component_hits,
        resolve_yearly_value_collisions,
    )

    components_best: dict[tuple[str, str], Any] = {}
    for doc in docs:
        source_document = doc.doc_type or DOC_TYPE_ANNUAL_REPORT
        try:
            with pdfplumber.open(io.BytesIO(doc.pdf_bytes)) as pdf:
                for component in DERIVATION_COMPONENT_NAMES:
                    hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=component,
                        periods=periods,
                        table_kind="yearly",
                        source_document=source_document,
                    )
                    merge_component_hits(components_best, hits, component)
        except Exception as exc:
            logger.warning(
                "[yearly] Component extraction failed on %s: %s",
                doc.filename,
                exc,
            )

    untrusted = filter_untrusted_source_hits(best)
    if untrusted:
        logger.info("[yearly] Dropped %d untrusted source hits", untrusted)
    dupes = filter_duplicate_cross_period_hits(best)
    if dupes:
        logger.info("[yearly] Dropped %d duplicate cross-period hits", dupes)
    noise = filter_obvious_schedule_noise(best)
    if noise:
        logger.info("[yearly] Dropped %d obvious schedule-note hits", noise)
    collisions = resolve_yearly_value_collisions(best)
    if collisions:
        logger.info("[yearly] Resolved %d duplicate page/value collisions", collisions)
    removed = filter_invalid_hits(best)
    if removed:
        logger.info("[yearly] Dropped %d sanity-failed hits", removed)

    company_type = getattr(docs[0], "company_type", "nbfc") if docs else "nbfc"
    yearly_source = (docs[0].doc_type if docs else None) or DOC_TYPE_ANNUAL_REPORT
    apply_yearly_derived_values(
        best,
        periods,
        components_best=components_best,
        company_type=company_type,
        source_file=docs[0].filename if docs else "",
        source_document=yearly_source,
    )

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

    extracted = sum(1 for r in records if r["status"] == "extracted")
    missing_metrics = [
        m
        for m in APPROVED_METRICS
        if not any(best.get((m, p)) for p in periods)
    ]
    if missing_metrics:
        logger.info(
            "[yearly] Still missing metrics: %s",
            ", ".join(missing_metrics),
        )

    logger.info(
        "[yearly] Complete in %.2fs — %d records, %d extracted",
        time.perf_counter() - t0,
        len(records),
        extracted,
    )
    return records
