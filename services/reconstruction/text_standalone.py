"""
Structured text parser for standalone P&L / Balance Sheet pages.

Used when pdfplumber returns broken single-column tables (common in Kotak ARs).
"""

from __future__ import annotations

import re

from services.normalizer import (
    UnitType,
    canonicalize_table1_period,
    canonicalize_quarter_period,
    canonicalize_table2_period,
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
    if any(
        k in norm
        for k in (
            "standalone balance sheet",
            "standalone profit and loss",
            "standalone statement of profit",
            "standalone financial results",
        )
    ):
        return True
    if "particulars" in norm:
        dates = set(re.findall(r"31\.03\.20\d{2}", text))
        if len(dates) >= 2:
            return True
    if any(k in norm for k in ("statement of profit", "profit and loss", "profit & loss")):
        dates = set(re.findall(r"31\.03\.20\d{2}", text))
        if len(dates) >= 2:
            return True
    return False


def _is_h1_pnl_page(text: str) -> bool:
    norm = normalize_text(text)
    compact = re.sub(r"\s+", "", norm)
    has_h1 = bool(re.search(r"h1fy\d{2}", compact)) or "half year" in norm
    has_pnl = any(
        k in norm
        for k in (
            "profit and loss",
            "profit & loss",
            "statement of profit",
            "particulars",
            "financial performance",
            "total income",
        )
    )
    return has_h1 and has_pnl


def _collect_h1_period_order(lines: list[str]) -> list[tuple[str, float]]:
    seen: list[str] = []
    for line in lines[:60]:
        if canonicalize_quarter_period(line):
            continue
        period = canonicalize_table2_period(line)
        if period and period not in seen:
            seen.append(period)
        compact = re.sub(r"\s+", "", normalize_text(line))
        if re.search(r"q[12]fy\d{2}", compact):
            continue
        for m in re.finditer(r"h1fy(\d{2})", compact):
            label = f"H1FY{m.group(1)}"
            if label not in seen:
                seen.append(label)
    return [(p, 1.0) for p in seen]


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


_SKIP_LAYOUT_CELLS = frozenset({
    "particulars", "var", "variance", "change", "yoy", "qoq", "growth",
})


def _merge_period_tokens(tokens: list[str]) -> list[str]:
    """Join split headers like 'Q2' + 'FY26' or 'H1' + 'FY26'."""
    merged: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            pair = f"{tokens[i]} {tokens[i + 1]}"
            if canonicalize_quarter_period(pair) or canonicalize_table2_period(pair):
                merged.append(pair)
                i += 2
                continue
        compact = re.sub(r"\s+", "", tokens[i])
        if re.search(r"^(h1fy|q[12]fy)\d{2}$", compact, re.I):
            merged.append(tokens[i])
            i += 1
            continue
        merged.append(tokens[i])
        i += 1
    return merged


def _header_value_layout(
    header_line: str,
    allowed_periods: tuple[str, ...],
    *,
    h1_only: bool,
) -> dict[str, int]:
    """
    Map period label -> index among trailing numeric tokens (Var / Q2 / H1 aligned).
    """
    allowed = set(allowed_periods)
    tokens = _merge_period_tokens(header_line.split())
    if len(tokens) < 2:
        return {}

    layout: dict[str, int] = {}
    value_slot = 0
    for tok in tokens[1:]:
        q = canonicalize_quarter_period(tok)
        h = canonicalize_table2_period(tok)
        if h and h in allowed:
            layout[h] = value_slot
        elif q and q in allowed and not h1_only:
            layout[q] = value_slot
        value_slot += 1

    return layout


def _find_header_layout(
    lines: list[str],
    allowed_periods: tuple[str, ...],
    *,
    h1_only: bool,
) -> dict[str, int]:
    best: dict[str, int] = {}
    for line in lines[:45]:
        norm = normalize_text(line)
        if "particulars" not in norm and not re.search(r"h1fy|q[12]\s*fy", line, re.I):
            continue
        layout = _header_value_layout(line, allowed_periods, h1_only=h1_only)
        if len(layout) > len(best):
            best = layout
        if layout and all(p in layout for p in allowed_periods):
            return layout
    return best


def _split_label_and_value_tokens(line: str) -> tuple[str, list[str]]:
    """Split 'Revenue from Operations 5.2% 7175 ...' into label + value tokens."""
    m = re.search(r"(-?[\d,]+(?:\.\d+)?%?)", line)
    if not m:
        return line.strip(), []
    label = line[: m.start()].strip()
    values = re.findall(r"-?[\d,]+(?:\.\d+)?%?", line[m.start() :])
    return label, values


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


def extract_from_h1_presentation_text(
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
    """Text fallback for investor decks when pdfplumber tables are broken."""
    if not _is_h1_pnl_page(page_text):
        return {}

    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    periods = [p for p, _ in _collect_h1_period_order(lines) if p in allowed_periods]
    if not periods:
        periods = [p for p in allowed_periods if p.startswith("H1FY")]
    if not periods:
        return {}

    layout = _find_header_layout(lines, tuple(periods), h1_only=True)
    unit = detect_unit(page_text)
    unit_detected = unit not in ("unknown",)
    found: dict[str, ExtractionHit] = {}

    for line in lines:
        label, value_tokens = _split_label_and_value_tokens(line)
        row_score, matched_alias, _ = score_row_match(metric, label or line)
        if row_score <= 0:
            continue

        period_values: dict[str, float] = {}
        if layout and value_tokens:
            for period in periods:
                slot = layout.get(period)
                if slot is None or slot >= len(value_tokens):
                    continue
                raw = parse_numeric_value(value_tokens[slot])
                if raw is None:
                    continue
                if not is_ratio_metric(metric) and abs(raw) < 100 and not str(
                    value_tokens[slot]
                ).endswith("%"):
                    continue
                period_values[period] = raw
        else:
            nums = [
                parse_numeric_value(t)
                for t in value_tokens
                if parse_numeric_value(t) is not None
            ]
            filtered = [v for v in nums if abs(v) > 100 or is_ratio_metric(metric)]
            for period, raw_val in zip(periods, filtered or nums):
                period_values[period] = raw_val

        for period, raw_val in period_values.items():
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
                column_score=1.0 if layout else 0.75,
                from_table=False,
                unit_detected=unit_detected,
                used_text_fallback=True,
            )

            hit = ExtractionHit(
                table="half_year",
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
                row_label=label or line,
                column_header=period,
                row_score=row_score,
                column_score=1.0 if layout else 0.75,
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
