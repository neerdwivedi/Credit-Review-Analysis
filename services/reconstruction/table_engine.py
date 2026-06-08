"""Table-first extraction: pdfplumber → pandas → row/column match."""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd
import pdfplumber

from services.normalizer import (
    UnitType,
    canonicalize_table1_period,
    canonicalize_table2_period,
    convert_to_crore,
    detect_unit,
    is_ratio_metric,
    normalize_text,
    parse_numeric_value,
)
from services.reconstruction.schema import ExtractionHit, TableKind, compute_confidence
from services.reconstruction.similarity import score_row_match

logger = logging.getLogger("credit_review")

PDFPLUMBER_SETTINGS: list[dict[str, Any]] = [
    {},
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
]

_LABEL_PREFIX = re.compile(
    r"^(?:[ivxlc]+\.|[a-z]\.|\(?\d+\)?\.|\(\d+\)|\d+\s)\s*",
    re.IGNORECASE,
)


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


def _period_map_from_row(
    row: list[str],
    table_kind: TableKind,
    prev_row: list[str] | None = None,
) -> dict[int, tuple[str, float]]:
    canon = (
        canonicalize_table1_period
        if table_kind == "yearly"
        else canonicalize_table2_period
    )
    ff = _forward_fill(row)
    ff_prev = _forward_fill(prev_row) if prev_row else None
    mapping: dict[int, tuple[str, float]] = {}
    for col_idx, cell in enumerate(ff):
        if not cell or cell.lower() in {"particulars", "schedule", "no.", "sr no", "sr"}:
            continue
        period = canon(cell)
        col_score = 1.0 if period else 0.0
        if not period and ff_prev and col_idx < len(ff_prev) and ff_prev[col_idx]:
            combined = f"{ff_prev[col_idx]} {cell}".strip()
            period = canon(combined)
            col_score = 0.95 if period else 0.0
        if period:
            mapping[col_idx] = (period, col_score)
    return mapping


def _detect_period_anchor(
    table: list[list[Any]],
    table_kind: TableKind,
    max_rows: int = 10,
) -> tuple[dict[int, tuple[str, float]], int]:
    best: dict[int, tuple[str, float]] = {}
    best_idx = 0

    for idx in range(min(len(table), max_rows)):
        row = [str(c or "").strip() for c in table[idx]]
        prev = [str(c or "").strip() for c in table[idx - 1]] if idx > 0 else None
        mapping = _period_map_from_row(row, table_kind, prev_row=prev)

        # Also try combining current row with previous row
        # for multi-level headers like:
        # Row 1: "As at March 31, 2024" | "As at March 31, 2023"
        # Row 2: "No. of Shares" | "Equity share capital" | ...
        if idx > 0 and len(mapping) == 0:
            prev_row = [str(c or "").strip() for c in table[idx - 1]]
            combined_mapping = _period_map_from_row(
                prev_row, table_kind, prev_row=None
            )
            if len(combined_mapping) > len(best):
                best = combined_mapping
                best_idx = idx  # data starts at current row

        if len(mapping) > len(best):
            best = mapping
            best_idx = idx + 1

    if not best:
        return {}, 0
    return best, best_idx


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
        col_map, data_start = _detect_period_anchor(table, table_kind)
        if not col_map:
            continue
        period_cols = {
            idx: (p, sc)
            for idx, (p, sc) in col_map.items()
            if p in allowed
        }
        if not period_cols:
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
                raw_cell = row[col_idx]
                raw_text = str(raw_cell or "").strip()
                parsed = parse_numeric_value(raw_text)
                if parsed is None:
                    continue
                # Reject if value looks like a schedule/page number (small integers under 50)
                if parsed is not None and not is_ratio_metric(metric) and abs(parsed) < 50:
                    # Only reject if unit is not percent and value is suspiciously small
                    if unit not in ("percent",) and abs(parsed) < 50:
                        continue
                if is_ratio_metric(metric) and (
                    parsed > 100 or (1900 <= abs(parsed) <= 2035)
                ):
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
