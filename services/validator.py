"""
Finance sanity checks and human-readable validation warnings.

Never auto-corrects values — only flags suspicious extractions for review.
"""

from __future__ import annotations

from typing import Any

from data.metric_aliases import (
    APPROVED_METRICS,
    NOT_DISCLOSED,
    TABLE1_PERIODS,
    TABLE2_PERIODS,
)
from data.metric_logic import get_valid_range, is_value_in_range
from services.normalizer import is_ratio_metric

CONFIDENCE_WARNING_THRESHOLD = 0.60


def _numeric_value(record: dict[str, Any]) -> float | None:
    val = record.get("value_crore")
    if val is None or record.get("display_value") == NOT_DISCLOSED:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def check_metric_sanity(metric: str, value: float) -> list[str]:
    warnings: list[str] = []

    if not is_value_in_range(metric, value):
        bounds = get_valid_range(metric)
        if bounds:
            lo, hi = bounds
            warnings.append(
                f"{metric} value {value:.2f} is outside expected range {lo}–{hi}."
            )

    if metric in ("Total Assets", "Borrowings", "Investments",
                  "Advances", "Deposits", "NII", "Total Income") and value < 0:
        warnings.append(
            f"{metric} {value:.2f} cr is negative — likely an extraction error."
        )

    return warnings


def validate_cross_metrics(records: list[dict[str, Any]]) -> list[str]:
    """
    Cross-metric checks for the same period (e.g. Tier I <= CAR).
    """
    warnings: list[str] = []
    by_period: dict[str, dict[str, float]] = {}

    for rec in records:
        period = rec.get("period", "")
        metric = rec.get("metric", "")
        val = _numeric_value(rec)
        if val is None:
            continue
        by_period.setdefault(period, {})[metric] = val

    for period, metrics in by_period.items():
        tier1 = metrics.get("Tier I Capital Ratio")
        car = metrics.get("Capital Adequacy Ratio")
        if tier1 is not None and car is not None and tier1 > car:
            warnings.append(
                f"Tier I ({tier1}) exceeds Capital Adequacy Ratio ({car}) for period {period}."
            )

        gnpa = metrics.get("GNPA")
        nnpa = metrics.get("NNPA")
        if gnpa is not None and nnpa is not None and nnpa > gnpa:
            warnings.append(
                f"NNPA ({nnpa:.2f}%) exceeds GNPA ({gnpa:.2f}%) for {period} — verify."
            )

    return warnings


def build_validation_warnings(
    records: list[dict[str, Any]],
    table_id: int,
) -> list[str]:
    """
    Aggregate all validation warnings for human review summary.
    Uses periods actually present in the records — not hardcoded constants —
    so it works for any company and any FY year setting.
    """
    warnings: list[str] = []

    # Derive periods from the records themselves
    seen_periods: list[str] = []
    for rec in records:
        p = rec.get("period", "")
        if p and p not in seen_periods:
            seen_periods.append(p)
    # Fall back to constants only if records are empty
    if not seen_periods:
        seen_periods = list(TABLE1_PERIODS if table_id == 1 else TABLE2_PERIODS)

    for period in seen_periods:
        for metric in APPROVED_METRICS:
            match = [
                r for r in records
                if r.get("metric") == metric and r.get("period") == period
            ]
            if not match:
                warnings.append(f"Missing {metric} for {period}.")
                continue
            rec = match[0]
            if rec.get("display_value") == NOT_DISCLOSED:
                if table_id == 2:
                    warnings.append(
                        f"{period} not explicitly disclosed for {metric}."
                    )
                else:
                    warnings.append(f"Missing {metric} for {period}.")
                continue

            if rec.get("unit") == "unknown" and not is_ratio_metric(metric):
                warnings.append(
                    f"Unit not detected for {metric} ({period})."
                )

            if rec.get("confidence", 0) < CONFIDENCE_WARNING_THRESHOLD:
                warnings.append(
                    f"Low confidence ({rec.get('confidence', 0):.2f}) "
                    f"for {metric} ({period})."
                )

            if rec.get("page_number") is None:
                warnings.append(
                    f"Page number missing for {metric} ({period})."
                )

            val = _numeric_value(rec)
            if val is not None:
                warnings.extend(check_metric_sanity(metric, val))

    warnings.extend(validate_cross_metrics(records))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique


def build_human_validation_summary(
    table1_warnings: list[str],
    table2_warnings: list[str],
    table1_records: list[dict[str, Any]],
    table2_records: list[dict[str, Any]],
) -> str:
    """Plain-text summary for finance intern review."""
    lines = ["=== Human Validation Summary ===", ""]

    disclosed_t1 = sum(
        1 for r in table1_records if r.get("display_value") != NOT_DISCLOSED
    )
    disclosed_t2 = sum(
        1 for r in table2_records if r.get("display_value") != NOT_DISCLOSED
    )
    total_t1 = len(table1_records)
    total_t2 = len(table2_records)

    lines.append(
        f"Table 1 (March year-end): {disclosed_t1}/{total_t1} metric-period values extracted."
    )
    lines.append(
        f"Table 2 (Half-year Sep): {disclosed_t2}/{total_t2} metric-period values extracted."
    )
    lines.append("")

    if table1_warnings:
        lines.append("Table 1 warnings:")
        for w in table1_warnings:
            lines.append(f"  - {w}")
        lines.append("")

    if table2_warnings:
        lines.append("Table 2 warnings:")
        for w in table2_warnings:
            lines.append(f"  - {w}")
        lines.append("")

    if not table1_warnings and not table2_warnings:
        lines.append("No validation warnings. Please still verify against source PDFs.")

    lines.append("All values are extracted as disclosed — nothing was derived or calculated.")
    return "\n".join(lines)
