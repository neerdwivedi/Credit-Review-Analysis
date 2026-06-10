"""Core metric-period extraction with full fallback chain."""

from __future__ import annotations

import io
import logging
import time
from typing import Any

import pdfplumber

from data.metric_aliases import METRIC_ALIASES
from data.metric_logic import aliases_for_metric
from services.normalizer import normalize_text, parse_numeric_value
from services.reconstruction.document import DocumentContext
from services.reconstruction.page_index import (
    MAX_CANDIDATES_HALF_YEAR,
    MAX_CANDIDATES_YEARLY,
    detect_sections,
    priority_sections_for,
    select_candidate_pages,
)
from services.reconstruction.schema import ExtractionHit, TableKind, compute_confidence
from services.reconstruction.similarity import compact, score_row_match
from services.reconstruction.table_engine import (
    extract_metric_from_tables,
    extract_tables_from_page,
)
from services.reconstruction.text_standalone import (
    extract_from_h1_presentation_text,
    extract_from_standalone_text,
)
from data.metric_aliases import CURRENCY_METRICS, RATIO_METRICS
from data.metric_logic import is_value_in_range, metric_requires_pnl_table

logger = logging.getLogger("credit_review")

FAILURE_REASON = (
    "not explicitly found after priority search, fallback search, "
    "table retry, and text fallback"
)


def _yearly_standalone_pages(
    ctx: DocumentContext,
    priority: tuple[str, ...],
) -> list[int]:
    """Prefer detected standalone P&L/BS sections; avoid scanning the whole PDF."""
    section_pages: set[int] = set()
    for section in (
        "pnl_statement_table",
        "standalone_pnl",
        "standalone_bs",
        "standalone_results",
        *priority[:2],
    ):
        section_pages.update(ctx.section_pages.get(section, []))

    if section_pages:
        max_page = max(ctx.text_by_page.keys()) if ctx.text_by_page else 0
        expanded: set[int] = set(section_pages)
        for page_num in section_pages:
            for delta in (-1, 1):
                neighbor = page_num + delta
                if 1 <= neighbor <= max_page:
                    expanded.add(neighbor)
        return sorted(expanded)

    return list(ctx.standalone_pages)


def _scan_pages_for_metric(
    ctx: DocumentContext,
    pdf: pdfplumber.PDF,
    *,
    page_nums: list[int],
    metric: str,
    periods: tuple[str, ...],
    table_kind: TableKind,
    priority: tuple[str, ...],
    source_document: str,
    found: dict[str, ExtractionHit],
    all_hits: list[ExtractionHit],
) -> None:
    for page_num in page_nums:
        section = "standalone_sweep"
        for sec in priority:
            if page_num in ctx.section_pages.get(sec, []):
                section = sec
                break
        preferred = section in priority[:2]
        standalone = page_num in ctx.standalone_page_set

        _ensure_page_tables(ctx, page_num, pdf)
        tables = ctx.page_tables.get(page_num, [])
        page_unit = ctx.page_unit.get(page_num, "unknown")

        h1_only = (
            table_kind == "half_year"
            and bool(periods)
            and all(str(p).upper().startswith("H1FY") for p in periods)
        )
        hits = extract_metric_from_tables(
            tables,
            metric=metric,
            allowed_periods=periods,
            table_kind=table_kind,
            page_num=page_num,
            page_unit=page_unit,
            source_document=source_document,
            source_file=ctx.filename,
            source_section=section,
            preferred_source=preferred,
            standalone_section=standalone,
            h1_only=h1_only,
        )
        for period, hit in hits.items():
            all_hits.append(hit)
            if _should_store_hit(table_kind, metric, hit, found.get(period)):
                found[period] = hit

        page_text = ctx.text_by_page.get(page_num, "")
        if table_kind == "yearly":
            text_hits = extract_from_standalone_text(
                page_text,
                metric=metric,
                allowed_periods=periods,
                page_num=page_num,
                source_document=source_document,
                source_file=ctx.filename,
                source_section=section,
                preferred_source=preferred,
            )
            for period, hit in text_hits.items():
                all_hits.append(hit)
                if _should_store_hit(table_kind, metric, hit, found.get(period)):
                    found[period] = hit
        elif table_kind == "half_year" and metric_requires_pnl_table(
            metric, table_kind
        ):
            text_hits = extract_from_h1_presentation_text(
                page_text,
                metric=metric,
                allowed_periods=periods,
                page_num=page_num,
                source_document=source_document,
                source_file=ctx.filename,
                source_section=section,
                preferred_source=preferred,
            )
            for period, hit in text_hits.items():
                all_hits.append(hit)
                if _should_store_hit(table_kind, metric, hit, found.get(period)):
                    found[period] = hit


def _is_untrusted_hit(table_kind: TableKind, metric: str, hit: ExtractionHit) -> bool:
    section = getattr(hit, "source_section", "") or ""
    val = getattr(hit, "value_crore", None)
    if val is not None and not is_value_in_range(metric, float(val)):
        return True
    if section == "text_regex":
        if table_kind == "yearly" or metric in CURRENCY_METRICS:
            return True
        if metric in RATIO_METRICS and float(hit.confidence or 0) < 0.45:
            return True
    if section in ("standalone_sweep", "fallback") and metric in CURRENCY_METRICS:
        if float(getattr(hit, "row_score", 0) or 0) < 0.75:
            return True
        if "note " in (getattr(hit, "row_label", "") or "").lower():
            return True
    return False


def _should_store_hit(
    table_kind: TableKind,
    metric: str,
    hit: ExtractionHit,
    current: ExtractionHit | None,
) -> bool:
    if _is_untrusted_hit(table_kind, metric, hit):
        return False
    if table_kind == "yearly":
        from data.metric_logic import is_obvious_schedule_noise, score_yearly_extraction_hit

        if is_obvious_schedule_noise(metric, hit):
            return False
        if current is None:
            return True
        if is_obvious_schedule_noise(metric, current):
            return True
        return score_yearly_extraction_hit(hit, period=hit.period) > (
            score_yearly_extraction_hit(current, period=current.period) + 0.02
        )
    if current is None:
        return True
    return hit.confidence > current.confidence


def _ensure_page_tables(
    ctx: DocumentContext,
    page_num: int,
    pdf: pdfplumber.PDF,
) -> None:
    if page_num in ctx.page_tables:
        return
    try:
        page = pdf.pages[page_num - 1]
        ctx.page_tables[page_num] = extract_tables_from_page(page)
    except Exception as exc:
        logger.warning("Table extract failed p%s %s: %s", page_num, ctx.filename, exc)
        ctx.page_tables[page_num] = []


def _text_regex_fallback(
    page_text: str,
    metric: str,
    period: str,
    table_kind: TableKind,
) -> float | None:
    """Last resort: alias on same line or next line as a number."""
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    aliases = [normalize_text(a) for a in aliases_for_metric(metric, METRIC_ALIASES)]

    for i, line in enumerate(lines):
        norm = normalize_text(line)
        if not any(a in norm or compact(a) in compact(line) for a in aliases):
            continue
        row_score, _, _ = score_row_match(metric, line)
        if row_score <= 0:
            continue
        for j in range(i, min(i + 4, len(lines))):
            val = parse_numeric_value(lines[j])
            if val is not None:
                return val
    return None


def _find_fallback_pages(ctx: DocumentContext, metric: str) -> list[int]:
    aliases = aliases_for_metric(metric, METRIC_ALIASES)
    scored: list[tuple[int, int]] = []
    for page_num, text in ctx.text_by_page.items():
        norm = normalize_text(text)
        cnorm = compact(text)
        hits = 0
        for alias in aliases:
            a = normalize_text(alias)
            if a in norm or compact(a) in cnorm:
                hits += 1
        if hits:
            scored.append((hits, page_num))
    scored.sort(reverse=True)
    pages: list[int] = []
    for _, p in scored[:25]:
        pages.append(p)
        for d in (-2, -1, 1, 2):
            np = p + d
            if np in ctx.text_by_page and np not in pages:
                pages.append(np)
    return pages[:30]


def _log_extract(
    *,
    table_kind: TableKind,
    metric: str,
    period: str,
    hit: ExtractionHit | None,
    candidate_pages: list[int],
    fallback: bool,
) -> None:
    if hit:
        logger.info(
            "[extract] table=%s metric=%s period=%s status=extracted "
            "source=%s file=%s page=%d section=%s row=%r score=%.2f "
            "col=%r col_score=%.2f value=%s unit=%s conf=%.2f fallback=%s",
            table_kind,
            metric,
            period,
            hit.source_document,
            hit.source_file,
            hit.page_number,
            hit.source_section,
            hit.row_label,
            hit.row_score,
            hit.column_header,
            hit.column_score,
            hit.value_original,
            hit.unit,
            hit.confidence,
            fallback,
        )
    else:
        logger.info(
            "[extract] table=%s metric=%s period=%s status=missing "
            "candidates=%s fallback=%s reason=%s",
            table_kind,
            metric,
            period,
            candidate_pages[:15],
            fallback,
            FAILURE_REASON,
        )


def _reorder_yearly_candidates(
    ctx: DocumentContext,
    metric: str,
    candidates: list[int],
) -> list[int]:
    """Put standalone P&L/BS tables with year columns ahead of schedule pages."""
    import re

    from data.metric_logic import METRIC_LOGIC

    def sort_key(page_num: int) -> tuple[int, int]:
        norm = ctx.norm_text_by_page.get(page_num, "")
        score = 0
        if page_num in ctx.section_pages.get("standalone_pnl", []):
            score += 200
        if page_num in ctx.section_pages.get("standalone_bs", []):
            score += 150
        if re.search(r"particulars.{0,80}31\.03\.20\d{2}", norm):
            score += 80
        if "standalone statement of profit" in norm:
            score += 100
        logic = METRIC_LOGIC.get(metric, {})
        if logic.get("pnl_section") and "profit" in norm:
            score += 50
        if logic.get("balance_sheet_section") and "balance sheet" in norm:
            score += 50
        return (-score, page_num)

    return sorted(candidates, key=sort_key)


def _reorder_half_year_candidates(
    ctx: DocumentContext,
    metric: str,
    candidates: list[int],
) -> list[int]:
    """Put H1 P&L / highlights pages first so subsidiary tables do not win."""

    def sort_key(page_num: int) -> tuple[int, int]:
        norm = ctx.norm_text_by_page.get(page_num, "")
        score = 0
        if "profit and loss" in norm or "profit & loss" in norm:
            score += 100
        if "bank highlights" in norm or "key metrics" in norm:
            score += 40
        if "h1fy26" in norm or "h1 fy26" in norm:
            score += 30
        if metric in ("PAT", "NII", "Total Income", "ROE") and "pat contribution" in norm:
            score -= 50
        return (-score, page_num)

    return sorted(candidates, key=sort_key)


def _vision_fallback(
    ctx,
    missing_metrics: list[str],
    vision_api_key: str | None,
) -> dict[tuple[str, str], Any]:
    if not vision_api_key or not missing_metrics:
        return {}

    from services.vision_extractor import vision_extract_for_document
    candidate_pages: set[int] = set()
    for metric in missing_metrics:
        aliases = aliases_for_metric(metric, METRIC_ALIASES)
        for page_num, norm in ctx.norm_text_by_page.items():
            if any(alias.lower() in norm for alias in aliases):
                candidate_pages.add(page_num)
                candidate_pages.add(page_num - 1)
                candidate_pages.add(page_num + 1)
    candidate_pages.update(ctx.standalone_page_set)
    valid_pages = sorted(
        p for p in candidate_pages if p in ctx.text_by_page
    )[:8]

    if not valid_pages:
        return {}

    logger.info(
        "[vision_fallback] Trying vision for %s on pages %s",
        missing_metrics, valid_pages,
    )
    return vision_extract_for_document(
        pdf_bytes=ctx.pdf_bytes,
        missing_metrics=missing_metrics,
        candidate_pages=valid_pages,
        api_key=vision_api_key,
        provider="gemini",
    )


def extract_metric_on_document(
    ctx: DocumentContext,
    pdf: pdfplumber.PDF,
    *,
    metric: str,
    periods: tuple[str, ...],
    table_kind: TableKind,
    source_document: str,
) -> dict[str, ExtractionHit]:
    """Run full pipeline for one metric across all requested periods on one PDF."""
    priority = priority_sections_for(metric, table_kind)
    max_cand = (
        MAX_CANDIDATES_YEARLY if table_kind == "yearly" else MAX_CANDIDATES_HALF_YEAR
    )
    candidates = select_candidate_pages(ctx, metric, table_kind, priority, max_cand)
    if table_kind == "half_year":
        candidates = _reorder_half_year_candidates(ctx, metric, candidates)

    found: dict[str, ExtractionHit] = {}
    all_hits: list[ExtractionHit] = []

    if table_kind == "yearly":
        standalone_pages = _yearly_standalone_pages(ctx, priority)
        seen: set[int] = set()
        ordered: list[int] = []
        for p in standalone_pages + candidates:
            if p not in seen:
                ordered.append(p)
                seen.add(p)
        candidates = _reorder_yearly_candidates(ctx, metric, ordered)

    _scan_pages_for_metric(
        ctx,
        pdf,
        page_nums=candidates,
        metric=metric,
        periods=periods,
        table_kind=table_kind,
        priority=priority,
        source_document=source_document,
        found=found,
        all_hits=all_hits,
    )

    if table_kind != "yearly" and len(found) >= len(periods):
        if not hasattr(ctx, "all_extraction_hits"):
            ctx.all_extraction_hits = []
        ctx.all_extraction_hits.extend(all_hits)
        return found

    missing = [p for p in periods if p not in found]
    if not missing:
        if not hasattr(ctx, "all_extraction_hits"):
            ctx.all_extraction_hits = []
        ctx.all_extraction_hits.extend(all_hits)
        return found

    fallback_pages = _find_fallback_pages(ctx, metric)
    for page_num in fallback_pages:
        if page_num in candidates:
            continue
        _ensure_page_tables(ctx, page_num, pdf)
        hits = extract_metric_from_tables(
            ctx.page_tables.get(page_num, []),
            metric=metric,
            allowed_periods=tuple(missing),
            table_kind=table_kind,
            page_num=page_num,
            page_unit=ctx.page_unit.get(page_num, "unknown"),
            source_document=source_document,
            source_file=ctx.filename,
            source_section="fallback",
            preferred_source=False,
            standalone_section=page_num in ctx.standalone_page_set,
        )
        for period, hit in hits.items():
            all_hits.append(hit)
            if _should_store_hit(table_kind, metric, hit, found.get(period)):
                found[period] = hit
                if period in missing:
                    missing.remove(period)

        if table_kind == "yearly":
            text_hits = extract_from_standalone_text(
                ctx.text_by_page.get(page_num, ""),
                metric=metric,
                allowed_periods=tuple(missing),
                page_num=page_num,
                source_document=source_document,
                source_file=ctx.filename,
                source_section="fallback_text",
                preferred_source=False,
            )
            for period, hit in text_hits.items():
                all_hits.append(hit)
                if _should_store_hit(table_kind, metric, hit, found.get(period)):
                    found[period] = hit
                    if period in missing:
                        missing.remove(period)

  # text_regex over full PDF produces false positives (same value all periods).
    if table_kind == "half_year" and metric in RATIO_METRICS:
        for period in list(missing):
            for page_num in candidates:
                text = ctx.text_by_page.get(page_num, "")
                val = _text_regex_fallback(text, metric, period, table_kind)
                if val is None:
                    continue
                from services.normalizer import convert_to_crore, detect_unit, is_ratio_metric

                unit = ctx.page_unit.get(page_num, detect_unit(text))
                if is_ratio_metric(metric):
                    vc = val
                else:
                    converted = convert_to_crore(val, unit)
                    if converted is None:
                        continue
                    vc = converted

                from services.reconstruction.schema import compute_confidence

                hit = ExtractionHit(
                    table=table_kind,
                    metric=metric,
                    period=period,
                    value_original=val,
                    unit=unit if not is_ratio_metric(metric) else "percent",
                    value_crore=vc,
                    page_number=page_num,
                    source_document=source_document,
                    source_file=ctx.filename,
                    source_section="text_regex",
                    confidence=compute_confidence(
                        standalone_section=page_num in ctx.standalone_page_set,
                        preferred_source=False,
                        row_score=0.8,
                        column_score=0.5,
                        from_table=False,
                        unit_detected=unit != "unknown",
                        used_text_fallback=True,
                    ),
                    row_label=metric,
                    used_text_fallback=True,
                    raw_text=str(val),
                    raw_text_unit=str(unit),
                )
                all_hits.append(hit)
                if _should_store_hit(table_kind, metric, hit, found.get(period)):
                    found[period] = hit
                    missing.remove(period)
                break

    # Vision fallback for still-missing periods
    VISION_ENABLED = False  # per-metric vision disabled
    # Vision now runs once per document in yearly.py and half_year.py

    if VISION_ENABLED:
        still_missing_periods = [p for p in periods if p not in found]
        if still_missing_periods:
            vision_key = getattr(ctx, "vision_api_key", None)
            if vision_key:
                vision_results = _vision_fallback(
                    ctx, [metric], vision_key
                )
                for (m, p), val in vision_results.items():
                    if p in still_missing_periods:
                        from services.normalizer import is_ratio_metric
                        hit = ExtractionHit(
                            table=table_kind,
                            metric=metric,
                            period=p,
                            value_original=val,
                            unit="crore",
                            value_crore=val,
                            page_number=0,
                            source_document=source_document,
                            source_file=ctx.filename,
                            source_section="vision_fallback",
                            confidence=0.80,
                            row_label=metric,
                            used_text_fallback=False,
                        )
                        all_hits.append(hit)
                        found[p] = hit

    if not hasattr(ctx, "all_extraction_hits"):
        ctx.all_extraction_hits = []
    ctx.all_extraction_hits.extend(all_hits)
    return found


def prepare_document(ctx: DocumentContext, table_kind: TableKind) -> None:
    ctx.build_indexes()
    detect_sections(ctx, table_kind)
