"""
Structured text parser for standalone P&L / Balance Sheet pages.

Used when pdfplumber returns broken single-column tables (common in Kotak ARs).
"""

from __future__ import annotations

import re

from services.normalizer import (
    UnitType,
    canonicalize_table1_period,
    convert_to_crore,
    detect_unit,
    find_all_table1_periods,
    is_ratio_metric,
    normalize_text,
    parse_numeric_value,
)
from services.reconstruction.schema import ExtractionHit, compute_confidence
from services.reconstruction.similarity import score_row_match

_NUMERIC_LINE = re.compile(r"^\s*\(?\s*-?[\d,]+(?:\.\d+)?\s*\)?\s*%?\s*$")
_SCHEDULE_LINE = re.compile(r"^\s*\d{1,2}\s*$")


def _is_standalone_financial_page(text: str) -> bool:
    norm = normalize_text(text)
    if "consolidated" in norm and "standalone" not in norm:
        return False
    return any(
        k in norm
        for k in (
            "standalone balance sheet",
            "standalone profit and loss",
            "standalone statement of profit",
            "standalone financial results",
        )
    )


def _collect_period_order(lines: list[str]) -> list[tuple[str, float]]:
    import re
    seen: list[str] = []
    for line in lines[:60]:
        clean = re.sub(
            r'\(refer\s+note\s+\d+\)', '', line, flags=re.IGNORECASE
        )
        clean = re.sub(r'\(note\s+\d+\)', '', clean, flags=re.IGNORECASE)
        clean = clean.strip()

        for period in find_all_table1_periods(clean):
            if period not in seen:
                seen.append(period)

        # Legacy single-line fallback
        period = canonicalize_table1_period(clean)
        if period and period not in seen:
            seen.append(period)

    return [(p, 1.0) for p in seen]


def _next_numeric_values(
    lines: list[str],
    start: int,
    count: int,
) -> list[float]:
    values: list[float] = []
    i = start
    while i < len(lines) and len(values) < count:
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        if _SCHEDULE_LINE.match(line) and len(values) == 0:
            continue
        if _NUMERIC_LINE.match(line.replace(" ", "")) or parse_numeric_value(line) is not None:
            val = parse_numeric_value(line)
            if val is not None:
                values.append(val)
        elif values:
            break
    return values


def extract_from_standalone_text(
    page_text: str,
    *,
    metric: str,
    allowed_periods: tuple[str, ...],
    page_num: int,
    source_document: str,
    source_file: str,
    source_section: str,
    preferred_source: bool,
) -> dict[str, ExtractionHit]:

    if not _is_standalone_financial_page(page_text):
        return {}

    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    period_order = _collect_period_order(lines)
    if not period_order:
        return {}

    periods = [p for p, _ in period_order if p in allowed_periods]
    if not periods:
        return {}

    unit = detect_unit(page_text)
    unit_detected = unit not in ("unknown",)
    found: dict[str, ExtractionHit] = {}

    for idx, line in enumerate(lines):
        row_score, matched_alias, _ = score_row_match(metric, line)
        if row_score <= 0:
            continue

        # Strategy 1: numbers on same line as label
        # Try to find numbers embedded in the label line itself
        import re
        inline_nums = re.findall(r'[\d,]+(?:\.\d+)?', line.replace(' ', ''))

        # Strategy 2: numbers on following lines (Kotak AR format)
        # Collect next few non-empty lines and parse numbers from them
        values = _next_numeric_values(lines, idx + 1, len(periods))

        # If strategy 2 found nothing, try skipping schedule number line
        if not values:
            values = _next_numeric_values(lines, idx + 2, len(periods))

        # Strategy 3: numbers are large (thousands format) —
        # skip small integers that are schedule numbers (< 20)
        filtered_values = []
        for v in values:
            if abs(v) > 100 or is_ratio_metric(metric):
                filtered_values.append(v)
            # Skip schedule numbers like 1, 2, 3... 18

        if not filtered_values:
            filtered_values = values  # fallback to unfiltered

        if not filtered_values:
            continue

        for period, raw_val in zip(periods, filtered_values[:len(periods)]):
            if is_ratio_metric(metric):
                value_original = raw_val
                value_crore = raw_val
                effective_unit: UnitType = "percent"
            else:
                value_original = raw_val
                converted = convert_to_crore(raw_val, unit)
                if converted is None:
                    continue
                value_crore = converted
                effective_unit = unit

            conf = compute_confidence(
                standalone_section=True,
                preferred_source=preferred_source,
                row_score=row_score,
                column_score=1.0,
                from_table=False,
                unit_detected=unit_detected,
                used_text_fallback=True,
            )

            hit = ExtractionHit(
                table="yearly",
                metric=metric,
                period=period,
                value_original=value_original,
                unit=effective_unit,
                value_crore=value_crore,
                page_number=page_num,
                source_document=source_document,
                source_file=source_file,
                source_section=source_section,
                confidence=conf,
                row_label=line,
                column_header=period,
                row_score=row_score,
                column_score=1.0,
                from_table=False,
                used_text_fallback=True,
                standalone_section=True,
                preferred_source=preferred_source,
                unit_detected=unit_detected,
                raw_text=str(raw_val),
                raw_text_unit=str(effective_unit),
            )
            cur = found.get(period)
            if cur is None or hit.confidence > cur.confidence:
                found[period] = hit

    return found
