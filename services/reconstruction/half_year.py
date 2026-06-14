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
    is_gemini_available,
    llm_extract_document,
)
from data.financial_logic import derive_h1_values, METRIC_TYPE, MetricType
from data.metric_aliases import get_quarter_periods

logger = logging.getLogger("credit_review")

# P&L flow metrics: keep a good direct H1 column over partial Q1/Q2 sums.
_H1_FLOW_METRICS = frozenset({
    "PAT", "Total Income", "NII", "Interest Earned", "Interest Expended",
})


def _is_strong_direct_h1_hit(hit: ExtractionHit | None) -> bool:
    if hit is None:
        return False
    section = hit.source_section or ""
    if section.startswith("derived:"):
        return False
    if hit.from_table and hit.row_score >= 0.85:
        return True
    if hit.confidence >= 0.85 and section != "groq_llm":
        return True
    return hit.confidence >= 0.92


def _fy_year_from_period(period: str) -> int | None:
    import re

    m = re.search(r"FY(\d{2})\b", period.upper())
    if m:
        return 2000 + int(m.group(1))
    return None


def _fy_year_from_filename(filename: str) -> int | None:
    import re

    name = filename.lower()
    m = re.search(r"\bfy\s*(\d{2})\b", name)
    if m:
        return 2000 + int(m.group(1))
    m = re.search(r"q[12]fy(\d{2})", name)
    if m:
        return 2000 + int(m.group(1))
    return None


def _score_half_year_hit(hit: ExtractionHit, period: str) -> float:
    score = float(hit.confidence)
    if hit.from_table:
        score += 0.15
    score += hit.row_score * 0.1
    col_hdr = (hit.column_header or period or "").upper()
    if period.upper().startswith("H1FY") and not col_hdr.startswith("H1FY"):
        score -= 0.5
    if hit.metric in _H1_FLOW_METRICS and col_hdr.startswith("Q"):
        score -= 0.6
    if (hit.source_section or "") in ("groq_llm", "gemini_file_api"):
        score -= 0.35
    if (hit.source_section or "").startswith("derived:"):
        score -= 0.05
    if hit.page_number == 0 and not (hit.source_section or "").startswith("derived:"):
        score -= 0.1
    period_fy = _fy_year_from_period(period)
    file_fy = _fy_year_from_filename(hit.source_file or "")
    if period_fy and file_fy:
        if file_fy == period_fy:
            score += 0.2
        elif file_fy == period_fy + 1:
            score += 0.05
        else:
            score -= 0.1
    return score


def _merge_quarter_hit(
    q_best: dict[tuple[str, str], Any],
    metric: str,
    period: str,
    hit: ExtractionHit,
) -> None:
    key = (metric, period)
    cur = q_best.get(key)
    if cur is None or hit.confidence > cur.confidence:
        q_best[key] = hit


def _reject_h1_shadowing_q2(
    best: dict[tuple[str, str], Any],
    q_best: dict[tuple[str, str], Any],
    periods: tuple[str, ...],
) -> int:
    """
    Drop H1 flow hits that exactly match Q2 — classic column-misread signature.

    Cumulative H1 must exceed single-quarter Q2 for income/PAT lines.
    """
    removed = 0
    for h1_period in periods:
        if not str(h1_period).upper().startswith("H1FY"):
            continue
        fy_suffix = h1_period[-2:]
        q2_label = f"Q2FY{fy_suffix}"
        for metric in _H1_FLOW_METRICS:
            key = (metric, h1_period)
            h1_hit = best.get(key)
            q2_hit = q_best.get((metric, q2_label))
            if h1_hit is None or q2_hit is None:
                continue
            h1_val = getattr(h1_hit, "value_crore", None)
            q2_val = getattr(q2_hit, "value_crore", None)
            if h1_val is None or q2_val is None:
                continue
            tolerance = max(1.0, abs(float(q2_val)) * 0.005)
            if abs(float(h1_val) - float(q2_val)) <= tolerance:
                logger.info(
                    "[half_year] Dropped %s %s=%s — matches Q2 %s (column misread)",
                    metric,
                    h1_period,
                    h1_val,
                    q2_label,
                )
                del best[key]
                removed += 1
    return removed


def _run_q1_q2_derivation(
    best: dict[tuple[str, str], Any],
    q_best: dict[tuple[str, str], Any],
    components_best: dict[tuple[str, str], Any],
    *,
    h1_fy_year: int,
    source_file: str,
    source_document: str,
) -> None:
    from data.metric_logic import DERIVATION_COMPONENT_NAMES

    for h1_label, (q1_label, q2_label) in [
        (f"H1FY{h1_fy_year % 100:02d}",
         (f"Q1FY{h1_fy_year % 100:02d}", f"Q2FY{h1_fy_year % 100:02d}")),
        (f"H1FY{(h1_fy_year - 1) % 100:02d}",
         (f"Q1FY{(h1_fy_year - 1) % 100:02d}", f"Q2FY{(h1_fy_year - 1) % 100:02d}")),
    ]:
        q1_vals: dict[str, float | None] = {}
        q2_vals: dict[str, float | None] = {}
        quarter_metrics = (*APPROVED_METRICS, *DERIVATION_COMPONENT_NAMES)
        for metric in quarter_metrics:
            q1_hit = q_best.get((metric, q1_label))
            q2_hit = q_best.get((metric, q2_label))
            if q1_hit is None and metric in DERIVATION_COMPONENT_NAMES:
                q1_hit = components_best.get((metric, q1_label))
            if q2_hit is None and metric in DERIVATION_COMPONENT_NAMES:
                q2_hit = components_best.get((metric, q2_label))
            q1_vals[metric] = q1_hit.value_crore if q1_hit else None
            q2_vals[metric] = q2_hit.value_crore if q2_hit else None

        has_data = any(
            v is not None for v in list(q1_vals.values()) + list(q2_vals.values())
        )
        if not has_data:
            continue

        derived = derive_h1_values(q1_vals, q2_vals)
        _derived_hits_to_best(
            derived,
            h1_period=h1_label,
            source_file=source_file,
            source_document=source_document,
            current_best=best,
        )
        logger.info(
            "[half_year] Q1/Q2 derivation for %s: %d metrics derived",
            h1_label,
            sum(1 for v in derived.values() if v.get("value") is not None),
        )


def _prefer_half_year_hit(
    current: ExtractionHit | None,
    new_hit: ExtractionHit,
    period: str,
) -> bool:
    if current is None:
        return True
    new_score = _score_half_year_hit(new_hit, period)
    cur_score = _score_half_year_hit(current, period)
    if new_score > cur_score + 0.03:
        return True
    if new_hit.row_score >= 0.99 and current.row_score < 0.95:
        return True
    if "profit and loss" in (new_hit.source_section or "") and new_hit.confidence >= current.confidence:
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

    from data.metric_logic import is_value_in_range

    for metric, info in derived.items():
        value = info.get("value")
        if value is None:
            continue
        if not is_value_in_range(metric, float(value)):
            continue
        if metric in _H1_FLOW_METRICS and abs(float(value)) < 500:
            continue
        if metric in _H1_FLOW_METRICS and info.get("needs_review"):
            continue
        confidence = float(info.get("confidence", 0.8))
        method = info.get("method", "derived")
        key = (metric, h1_period)
        cur = current_best.get(key)
        derived_from_quarters = method in (
            "Q1+Q2", "Q2", "IE-IX", "PAT*2/AvgAssets", "PAT*2/AvgNW",
            "PBT-Tax", "RevenueFromOps",
        )
        if cur is not None:
            if not derived_from_quarters and cur.confidence >= confidence:
                continue
            # Keep a strong direct H1 P&L row over partial / noisy Q1+Q2.
            if (
                derived_from_quarters
                and method in ("Q1+Q2", "Q2")
                and metric in _H1_FLOW_METRICS
                and _is_strong_direct_h1_hit(cur)
                and (info.get("needs_review") or confidence < 1.0)
            ):
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
    h1_fy_year: int = 2026,
    year_end_month: str = "March",
    *,
    fy_year: int | None = None,
) -> list[dict[str, Any]]:
    """Extract Table 2 (H1FYxx / prior H1) from investor presentation PDFs only."""
    from data.metric_aliases import (
        TABLE2_PERIODS as _DEFAULT_PERIODS,
        get_quarter_periods,
    )
    if fy_year is not None:
        h1_fy_year = fy_year
    if periods is None:
        periods = _DEFAULT_PERIODS
    quarter_periods = get_quarter_periods(h1_fy_year, year_end_month)

    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    best: dict[tuple[str, str], Any] = {}
    q_best: dict[tuple[str, str], Any] = {}
    components_best: dict[tuple[str, str], Any] = {}

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

    gemini_key = None
    for doc in investor_presentations:
        k = getattr(doc, "vision_api_key", None) or ""
        if is_gemini_available(k):
            gemini_key = k
            break

    if gemini_key:
        gemini_filled = 0
        for doc in investor_presentations:
            llm_hits = llm_extract_document(
                doc.pages,
                pdf_bytes=doc.pdf_bytes,
                filename=doc.filename,
                api_key=gemini_key,
                table_kind="half_year",
                source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                source_file=doc.filename,
                allowed_periods=periods,
            )
            for hit in llm_hits:
                key = (hit.metric, hit.period)
                if best.get(key) is None:
                    best[key] = hit
                    gemini_filled += 1
        logger.info("[half_year] Gemini primary: %d values seeded", gemini_filled)

    for doc in investor_presentations:
        prepare_document(doc, "half_year")
        logger.info("[half_year] Processing %s (%d pages)", doc.filename, len(doc.pages))
        try:
            with pdfplumber.open(io.BytesIO(doc.pdf_bytes)) as pdf:
                from data.metric_logic import (
                    DERIVATION_COMPONENT_NAMES,
                    merge_component_hits,
                )

                # H1 columns only — fill gaps Gemini missed.
                for metric in APPROVED_METRICS:
                    missing_h1 = [p for p in periods if not best.get((metric, p))]
                    if not missing_h1:
                        continue
                    hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=metric,
                        periods=tuple(missing_h1),
                        table_kind="half_year",
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                    )
                    for period, hit in hits.items():
                        key = (metric, period)
                        if best.get(key) is None:
                            best[key] = hit

                for component in DERIVATION_COMPONENT_NAMES:
                    comp_hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=component,
                        periods=periods,
                        table_kind="half_year",
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                    )
                    merge_component_hits(components_best, comp_hits, component)

                quarter_metrics = (*APPROVED_METRICS, *DERIVATION_COMPONENT_NAMES)
                for metric in quarter_metrics:
                    q_hits = extract_metric_on_document(
                        doc,
                        pdf,
                        metric=metric,
                        periods=quarter_periods,
                        table_kind="half_year",
                        source_document=DOC_TYPE_INVESTOR_PRESENTATION,
                    )
                    for period, hit in q_hits.items():
                        _merge_quarter_hit(q_best, metric, period, hit)
        except Exception as exc:
            logger.exception("[half_year] Failed on %s: %s", doc.filename, exc)

    shadow = _reject_h1_shadowing_q2(best, q_best, periods)
    if shadow:
        logger.info("[half_year] Removed %d H1 hits that shadowed Q2 values", shadow)

    try:
        _run_q1_q2_derivation(
            best,
            q_best,
            components_best,
            h1_fy_year=h1_fy_year,
            source_file=investor_presentations[0].filename if investor_presentations else "",
            source_document=DOC_TYPE_INVESTOR_PRESENTATION,
        )
    except Exception as exc:
        logger.warning("[half_year] Q1/Q2 derivation failed: %s", exc)

    from data.metric_logic import (
        apply_yearly_derived_values,
        filter_invalid_hits,
        filter_untrusted_source_hits,
    )

    if investor_presentations:
        apply_yearly_derived_values(
            best,
            periods,
            components_best=components_best,
            source_file=investor_presentations[0].filename,
            source_document=DOC_TYPE_INVESTOR_PRESENTATION,
            table="half_year",
        )

    untrusted = filter_untrusted_source_hits(best)
    if untrusted:
        logger.info("[half_year] Dropped %d untrusted source hits", untrusted)
    removed = filter_invalid_hits(best)
    if removed:
        logger.info("[half_year] Dropped %d sanity-failed hits", removed)

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
