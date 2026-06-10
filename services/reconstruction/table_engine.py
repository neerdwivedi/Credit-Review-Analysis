"""Table-first extraction: pdfplumber → pandas → row/column match."""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd
import pdfplumber

from services.normalizer import (
    UnitType,
    canonicalize_quarter_period,
    canonicalize_table1_period,
    canonicalize_table2_period,
    convert_to_crore,
    detect_unit,
    find_all_table1_periods,
    is_ratio_metric,
    normalize_text,
    parse_numeric_value,
)
from services.reconstruction.schema import ExtractionHit, TableKind, compute_confidence
from services.reconstruction.similarity import score_row_match
from data.metric_logic import (
    is_value_in_range,
    metric_requires_bs_table,
    metric_requires_pnl_table,
)

logger = logging.getLogger("credit_review")

PDFPLUMBER_SETTINGS: list[dict[str, Any]] = [
    {},
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
]

_LABEL_PREFIX = re.compile(
    r"^(?:[ivxlc]+\.|[a-z]\.|\(?\d+\)?\.|\(\d+\)|\d+\s)\s*",
    re.IGNORECASE,
)

_SKIP_HEADER_CELLS = frozenset({
    "particulars", "schedule", "no.", "sr no", "sr", "var", "variance",
    "change", "%", "yoy", "qoq", "growth",
})


def _clean_label(text: str) -> str:
    s = normalize_text(text)
    s = _LABEL_PREFIX.sub("", s).strip()
    return re.sub(r"\((?:refer|note|schedule).*?\)", "", s).strip()


def _build_row_label(row: list[Any]) -> str:
    parts: list[str] = []
    for cell in row[:5]:
        cleaned = _clean_label(str(cell or ""))
        if not cleaned or (cleaned.isdigit() and len(cleaned) <= 3):
            continue
        parts.append(cleaned)
    return " ".join(parts).strip()


def _forward_fill(row: list[str]) -> list[str]:
    out: list[str] = []
    last = ""
    for cell in row:
        s = str(cell or "").strip()
        if s:
            last = s
        out.append(last)
    return out


_SKIP_HEADER_LABELS = frozenset({
    "particulars", "schedule", "no.", "sr no", "sr", "var", "variance", "%",
})


def _period_map_from_row(
    row: list[str],
    table_kind: TableKind,
    prev_row: list[str] | None = None,
    *,
    h1_only: bool = False,
) -> dict[int, tuple[str, float]]:
    if table_kind == "half_year" and h1_only:
        mapping: dict[int, tuple[str, float]] = {}
        for col_idx, cell in enumerate(row):
            raw = str(cell or "").strip()
            if not raw or normalize_text(raw) in _SKIP_HEADER_LABELS:
                continue
            if canonicalize_quarter_period(raw):
                continue
            period = canonicalize_table2_period(raw)
            if period:
                mapping[col_idx] = (period, 1.0)
        return mapping

    canon = (
        canonicalize_table1_period
        if table_kind == "yearly"
        else canonicalize_table2_period
    )
    ff = _forward_fill(row)
    ff_prev = _forward_fill(prev_row) if prev_row else None
    mapping: dict[int, tuple[str, float]] = {}
    for col_idx, cell in enumerate(ff):
        cell_norm = normalize_text(cell)
        if not cell or cell_norm in _SKIP_HEADER_CELLS:
            continue
        if table_kind == "yearly":
            multi = find_all_table1_periods(cell)
            if len(multi) > 1:
                for offset, period in enumerate(multi):
                    mapping[col_idx + offset] = (period, 1.0)
                continue
        if table_kind == "half_year":
            qperiod = canonicalize_quarter_period(cell)
            if qperiod:
                mapping[col_idx] = (qperiod, 0.85)
                continue
            h1period = canon(cell)
            if h1period:
                mapping[col_idx] = (h1period, 1.0)
                continue
        period = canon(cell)
        col_score = 1.0 if period else 0.0
        if not period and ff_prev and col_idx < len(ff_prev) and ff_prev[col_idx]:
            combined = f"{ff_prev[col_idx]} {cell}".strip()
            if table_kind == "yearly":
                multi = find_all_table1_periods(combined)
                if len(multi) > 1:
                    for offset, period in enumerate(multi):
                        mapping[col_idx + offset] = (period, 0.95)
                    continue
            period = canon(combined)
            col_score = 0.95 if period else 0.0
        if period:
            mapping[col_idx] = (period, col_score)
    return mapping


def _row_looks_like_data(row: list[str]) -> bool:
    """True when a row has large numeric values in 2+ columns (not period headers)."""
    numeric_cols = 0
    for cell in row[1:]:
        text = str(cell or "").strip()
        if not text:
            continue
        if find_all_table1_periods(text) or canonicalize_table1_period(text):
            continue
        val = parse_numeric_value(text)
        if val is not None and abs(val) >= 100:
            numeric_cols += 1
    return numeric_cols >= 2


def _column_header_verified(
    table: list[list[Any]],
    col_idx: int,
    expected_period: str,
    data_start: int,
    *,
    h1_only: bool,
) -> bool:
    """H1 extraction: column must have an explicit H1 header, not Q2/Var bleed."""
    if not h1_only:
        return True
    saw_h1 = False
    saw_quarter = False
    for idx in range(min(data_start, 12)):
        row = [str(c or "").strip() for c in table[idx]]
        if col_idx >= len(row):
            continue
        cell = row[col_idx]
        if not cell or normalize_text(cell) in _SKIP_HEADER_LABELS:
            continue
        if canonicalize_quarter_period(cell):
            saw_quarter = True
        if canonicalize_table2_period(cell) == expected_period:
            saw_h1 = True
    if saw_quarter and not saw_h1:
        return False
    return saw_h1


def _detect_period_anchor(
    table: list[list[Any]],
    table_kind: TableKind,
    max_rows: int = 10,
    *,
    h1_only: bool = False,
) -> tuple[dict[int, tuple[str, float]], int]:
    """Merge period headers from all candidate header rows (supports split FY23)."""
    merged: dict[int, tuple[str, float]] = {}
    header_rows: list[int] = []

    for idx in range(min(len(table), max_rows)):
        row = [str(c or "").strip() for c in table[idx]]
        if _row_looks_like_data(row):
            break

        prev = [str(c or "").strip() for c in table[idx - 1]] if idx > 0 else None
        mapping = _period_map_from_row(
            row, table_kind, prev_row=prev, h1_only=h1_only
        )

        for col_idx, val in mapping.items():
            cur = merged.get(col_idx)
            if cur is None or val[1] >= cur[1]:
                merged[col_idx] = val
        if mapping:
            header_rows.append(idx)

    if not merged:
        return {}, 0
    data_start = max(header_rows) + 1 if header_rows else 0
    return merged, data_start


def _header_blob(table: list[list[Any]], max_rows: int = 8) -> str:
    parts: list[str] = []
    for row in table[:max_rows]:
        parts.extend(str(c or "") for c in row)
    return normalize_text(" ".join(parts))


def _periods_in_col_map(
    col_map: dict[int, tuple[str, float]],
    allowed: set[str],
) -> set[str]:
    return {p for p, _ in col_map.values() if p in allowed}


def _table_has_pnl_shape(
    table: list[list[Any]],
    table_kind: TableKind,
    col_map: dict[int, tuple[str, float]],
    allowed: set[str],
) -> bool:
    """P&L table: period headers + particulars-style layout."""
    periods = _periods_in_col_map(col_map, allowed)
    if not periods:
        return False

    header = _header_blob(table)
    if "particulars" in header:
        return True

    if table_kind == "half_year":
        return any(p.startswith("H1FY") for p in periods)

    return len(periods) >= 2


def _table_has_bs_shape(
    table: list[list[Any]],
    col_map: dict[int, tuple[str, float]],
    allowed: set[str],
) -> bool:
    """Balance-sheet table: period headers + BS wording or particulars."""
    periods = _periods_in_col_map(col_map, allowed)
    if not periods:
        return False
    header = _header_blob(table)
    if "particulars" in header:
        return True
    return any(
        k in header
        for k in ("balance sheet", "assets", "liabilities", "equity and liabilities")
    )


def _table_passes_shape_gate(
    table: list[list[Any]],
    metric: str,
    table_kind: TableKind,
    col_map: dict[int, tuple[str, float]],
    allowed: set[str],
) -> bool:
    if metric_requires_pnl_table(metric, table_kind):
        return _table_has_pnl_shape(table, table_kind, col_map, allowed)
    if metric_requires_bs_table(metric, table_kind):
        return _table_has_bs_shape(table, col_map, allowed)
    return True


def _table_quality_score(table: list[list[Any]], table_kind: TableKind) -> float:
    if not table or len(table) < 2:
        return 0.0
    col_map, _ = _detect_period_anchor(table, table_kind)
    if not col_map:
        return float(len(table))
    ncols = max(len(r) for r in table)
    return len(col_map) * 10.0 + min(len(table), 50) + ncols


def extract_tables_from_page(page: pdfplumber.page.Page) -> list[list[list[Any]]]:
    """Try multiple pdfplumber strategies; return deduplicated best tables."""
    candidates: list[list[list[Any]]] = []
    for settings in PDFPLUMBER_SETTINGS:
        try:
            tables = page.extract_tables(table_settings=settings) or []
            candidates.extend(tables)
        except Exception:
            continue
    seen: set[int] = set()
    unique: list[list[list[Any]]] = []
    for t in candidates:
        if not t:
            continue
        key = hash(tuple(tuple(str(c or "") for c in r) for r in t[:3]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)
    return unique


def table_to_dataframe(table: list[list[Any]]) -> pd.DataFrame:
    if not table:
        return pd.DataFrame()
    max_cols = max(len(r) for r in table)
    rows = []
    for row in table:
        padded = list(row) + [None] * (max_cols - len(row))
        rows.append([str(c or "").strip() for c in padded])
    return pd.DataFrame(rows)


def _detect_unit_in_table(table: list[list[Any]], fallback: UnitType) -> UnitType:
    if fallback not in ("unknown", "percent"):
        return fallback
    for row in table[:12]:
        for cell in row or []:
            if cell:
                u = detect_unit(str(cell))
                if u not in ("unknown", "percent"):
                    return u
        joined = " ".join(str(c or "") for c in row)
        u = detect_unit(joined)
        if u not in ("unknown", "percent"):
            return u
    return fallback


def extract_metric_from_tables(
    tables: list[list[list[Any]]],
    *,
    metric: str,
    allowed_periods: tuple[str, ...],
    table_kind: TableKind,
    page_num: int,
    page_unit: UnitType,
    source_document: str,
    source_file: str,
    source_section: str,
    preferred_source: bool,
    standalone_section: bool,
    h1_only: bool = False,
) -> dict[str, ExtractionHit]:
    """Scan all tables on a page for one metric; return best hit per period."""
    found: dict[str, ExtractionHit] = {}
    allowed = set(allowed_periods)

    sorted_tables = sorted(
        tables,
        key=lambda t: _table_quality_score(t, table_kind),
        reverse=True,
    )

    for table in sorted_tables:
        if not table or len(table) < 2:
            continue
        col_map, data_start = _detect_period_anchor(
            table, table_kind, h1_only=h1_only
        )
        if not col_map:
            continue
        period_cols = {
            idx: (p, sc)
            for idx, (p, sc) in col_map.items()
            if p in allowed
        }
        if not period_cols:
            continue

        if not _table_passes_shape_gate(
            table, metric, table_kind, col_map, allowed
        ):
            continue

        unit = _detect_unit_in_table(table, page_unit)
        unit_detected = unit not in ("unknown",)

        for row in table[data_start:]:
            if not row:
                continue
            label = _build_row_label([str(c or "") for c in row])
            row_score, matched_alias, is_exact = score_row_match(metric, label)
            if row_score <= 0:
                continue

            for col_idx, (period, col_score) in period_cols.items():
                if col_idx >= len(row):
                    continue
                if table_kind == "half_year" and not period.startswith("H1FY"):
                    continue
                if not _column_header_verified(
                    table,
                    col_idx,
                    period,
                    data_start,
                    h1_only=h1_only,
                ):
                    continue
                raw_cell = row[col_idx]
                raw_text = str(raw_cell or "").strip()
                parsed = parse_numeric_value(raw_text)
                if parsed is None:
                    continue
                # Reject schedule reference integers under 20 (Note 4, page 12).
                if (
                    parsed is not None
                    and not is_ratio_metric(metric)
                    and unit not in ("percent",)
                    and parsed != 0
                    and abs(parsed) < 20
                    and parsed == int(parsed)
                ):
                    continue
                if is_ratio_metric(metric) and (
                    parsed > 100 or (1900 <= abs(parsed) <= 2039)
                ):
                    continue
                if is_ratio_metric(metric) and metric in ("GNPA", "NNPA"):
                    if abs(parsed) > 30 and "%" not in raw_text:
                        continue
                if is_ratio_metric(metric) and "%" not in raw_text:
                    bounds = (5.0, 50.0) if "capital" in metric.lower() else (0.0, 40.0)
                    if abs(parsed) > bounds[1]:
                        continue

                if is_ratio_metric(metric):
                    value_crore = parsed
                    value_original = parsed
                    effective_unit: UnitType = "percent"
                else:
                    effective_unit = unit
                    value_original = parsed
                    converted = convert_to_crore(parsed, effective_unit)
                    if converted is None:
                        continue
                    value_crore = converted

                sanity_val = value_crore if not is_ratio_metric(metric) else parsed
                if not is_value_in_range(metric, float(sanity_val)):
                    continue

                conf = compute_confidence(
                    standalone_section=standalone_section,
                    preferred_source=preferred_source,
                    row_score=row_score,
                    column_score=col_score,
                    from_table=True,
                    unit_detected=unit_detected,
                    used_text_fallback=False,
                )

                hit = ExtractionHit(
                    table=table_kind,
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
                    row_label=label or (matched_alias or ""),
                    column_header=period,
                    row_score=row_score,
                    column_score=col_score,
                    from_table=True,
                    standalone_section=standalone_section,
                    preferred_source=preferred_source,
                    unit_detected=unit_detected,
                    raw_text=raw_text,
                    raw_text_unit=str(effective_unit),
                )

                cur = found.get(period)
                if cur is None or hit.confidence > cur.confidence:
                    found[period] = hit

    return found
