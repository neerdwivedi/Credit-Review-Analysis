"""
Phase 3 — Review Manager.

Bridges Phase 2 raw extraction records to a human-in-the-loop review workflow:

* Convert raw extraction records to "review records" that hold both the
  originally extracted value and the human-approved value.
* Provide pandas helpers for the Streamlit data_editor.
* Detect manual edits, recompute row status, and re-validate after edits.
* Pivot to "Yearly Financials" and "Half-Year Financials" display tables.
* Export the reviewed dataset to CSV.

This module never invents values, never auto-corrects, and never derives
metrics. It only formalises the review state of each (metric, period) pair.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Iterable

import pandas as pd

from data.metric_aliases import (
    APPROVED_METRICS,
    NOT_DISCLOSED,
    TABLE1_PERIODS,
    TABLE2_PERIODS,
)
from services.normalizer import (
    format_crore_display,
    is_ratio_metric,
    parse_numeric_value,
)
from services.validator import (
    CONFIDENCE_WARNING_THRESHOLD,
    check_metric_sanity,
    validate_cross_metrics,
)

logger = logging.getLogger("credit_review")

STATUS_EXTRACTED = "Extracted"
STATUS_MISSING = "Missing"
STATUS_LOW_CONFIDENCE = "Low Confidence"
STATUS_WARNING = "Warning"
STATUS_MANUALLY_EDITED = "Manually Edited"
STATUS_APPROVED = "Approved"

# Column names used in the review DataFrame (kept stable for data_editor)
COL_METRIC = "Metric"
COL_PERIOD = "Period"
COL_EXTRACTED = "Extracted Value"
COL_APPROVED = "Approved Value"
COL_ORIGINAL_UNIT = "Original Unit"
COL_VALUE_CRORE = "Converted Value (Crore)"
COL_SOURCE_DOC = "Source Document"
COL_SOURCE_FILE = "Source File"
COL_PAGE = "Page Number"
COL_CONFIDENCE = "Confidence"
COL_STATUS = "Status"
COL_NOTES = "Notes"
COL_MANUAL_EDIT = "Analyst Override"

READONLY_COLUMNS: tuple[str, ...] = (
    COL_METRIC,
    COL_PERIOD,
    COL_EXTRACTED,
    COL_ORIGINAL_UNIT,
    COL_VALUE_CRORE,
    COL_SOURCE_DOC,
    COL_SOURCE_FILE,
    COL_PAGE,
    COL_CONFIDENCE,
    COL_MANUAL_EDIT,
)


def _coerce_number(value: Any) -> float | None:
    """Try to coerce a cell to float; return None if blank / not numeric."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN check
            return None
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"not disclosed", "n/a", "na", "-", "—", "–"}:
        return None
    return parse_numeric_value(text)


def build_review_record(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Convert one Phase 2 raw record into a Phase 3 review record.

    Field meanings (kept aligned with the Phase 3 spec):
      * extracted_value : number as printed in the PDF, in the original unit
      * value_crore     : crore-converted (for currency) or percent (for ratios)
      * approved_value  : starts equal to value_crore; the intern can override it

    The user can later edit `approved_value`; the manual_edit flag detects that.
    """
    metric = raw.get("metric", "")
    period = raw.get("period", "")
    is_disclosed = (
        raw.get("status") == "extracted"
        or (
            raw.get("display_value") not in (NOT_DISCLOSED, None)
            and raw.get("value_crore") is not None
        )
    )

    extracted_original = (
        _coerce_number(raw.get("value_original")) if is_disclosed else None
    )
    value_crore = _coerce_number(raw.get("value_crore")) if is_disclosed else None
    # Approved Value is always normalized (₹ crore or %) — same as value_crore at load.
    initial_approved = value_crore

    return {
        "table": raw.get("table", ""),
        "metric": metric,
        "period": period,
        "extracted_value": extracted_original,
        "approved_value": initial_approved,
        "original_unit": raw.get("unit", "unknown"),
        "value_crore": value_crore,
        "initial_approved": initial_approved,  # baseline for manual-edit diff
        "page_number": raw.get("page_number"),
        "source_document": raw.get("source_document", ""),
        "source_filename": raw.get("source_filename", ""),
        "confidence": float(raw.get("confidence", 0.0) or 0.0),
        "status": "",
        "notes": "",
        "manual_edit": False,
    }


def build_review_records(raw_records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build review records and assign their initial status."""
    reviewed: list[dict[str, Any]] = []
    raw_list = list(raw_records)

    for raw in raw_list:
        reviewed.append(build_review_record(raw))

    apply_status_to_records(reviewed)
    return reviewed


def _record_has_warning(record: dict[str, Any], cross_warning_set: set[tuple[str, str]]) -> bool:
    """Check if a record fails sanity rules or is part of a cross-metric warning."""
    val = record.get("approved_value")
    if val is None:
        return False
    metric = record.get("metric", "")
    if check_metric_sanity(metric, float(val)):
        return True
    return (metric, record.get("period", "")) in cross_warning_set


def _collect_cross_warning_pairs(records: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """
    Identify (metric, period) pairs involved in a cross-metric warning,
    based on currently approved values.
    """
    synthetic = []
    for r in records:
        val = r.get("approved_value")
        if val is None:
            continue
        synthetic.append(
            {
                "metric": r["metric"],
                "period": r["period"],
                "value_crore": float(val),
                "display_value": format_crore_display(float(val)),
            }
        )

    cross_msgs = validate_cross_metrics(synthetic)
    pairs: set[tuple[str, str]] = set()

    for msg in cross_msgs:
        # Cross-warning currently only checks Tier I vs CAR; mark both rows.
        for r in records:
            if r["period"] in msg and r["metric"] in (
                "Tier I Capital Ratio",
                "Capital Adequacy Ratio",
            ):
                pairs.add((r["metric"], r["period"]))
    return pairs


def recompute_status(
    record: dict[str, Any],
    cross_warning_set: set[tuple[str, str]],
    *,
    final_approval: bool = False,
) -> str:
    """
    Compute the Status column for a single record.

    Priority:
      1. Missing      — no approved value
      2. Warning      — sanity / cross-metric check failed on approved value
      3. Manually Edited — user changed value vs extracted
      4. Low Confidence  — confidence < threshold
      5. Extracted    — auto-extracted value, looks fine
      6. Approved     — only after the user clicks "Approve Extraction"

    `final_approval=True` upgrades clean rows to "Approved" but preserves
    "Manually Edited" / "Warning" labels for the audit trail.
    """
    val = record.get("approved_value")
    if val is None:
        return STATUS_MISSING

    metric = record.get("metric", "")

    if check_metric_sanity(metric, float(val)) or (metric, record.get("period", "")) in cross_warning_set:
        return STATUS_WARNING

    if record.get("manual_edit"):
        return STATUS_MANUALLY_EDITED

    if float(record.get("confidence", 0.0)) < CONFIDENCE_WARNING_THRESHOLD:
        return STATUS_LOW_CONFIDENCE

    if final_approval:
        return STATUS_APPROVED
    return STATUS_EXTRACTED


def apply_status_to_records(
    records: list[dict[str, Any]],
    *,
    final_approval: bool = False,
) -> None:
    """Refresh the status column for every record in place."""
    cross_pairs = _collect_cross_warning_pairs(records)
    for rec in records:
        rec["status"] = recompute_status(rec, cross_pairs, final_approval=final_approval)


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the DataFrame that backs the Streamlit data_editor."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        rows.append(
            {
                COL_METRIC: rec["metric"],
                COL_PERIOD: rec["period"],
                COL_EXTRACTED: (
                    rec.get("value_crore")
                    if rec.get("value_crore") is not None
                    else float("nan")
                ),
                COL_APPROVED: (
                    rec["approved_value"]
                    if rec["approved_value"] is not None
                    else float("nan")
                ),
                COL_ORIGINAL_UNIT: rec["original_unit"],
                COL_VALUE_CRORE: (
                    rec["value_crore"]
                    if rec["value_crore"] is not None
                    else float("nan")
                ),
                COL_SOURCE_DOC: rec["source_document"],
                COL_SOURCE_FILE: rec["source_filename"],
                COL_PAGE: rec["page_number"] if rec["page_number"] is not None else "",
                COL_CONFIDENCE: round(float(rec.get("confidence", 0.0)), 2),
                COL_STATUS: rec["status"],
                COL_NOTES: rec.get("notes", ""),
                COL_MANUAL_EDIT: bool(rec.get("manual_edit", False)),
            }
        )
    return pd.DataFrame(rows)


def build_provenance_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    import re

    def _extract_year(period: str) -> str:
        m = re.search(r"20(\d{2})$", period)
        if m:
            return m.group(1)
        m = re.search(r"FY(\d{2})", period, re.IGNORECASE)
        if m:
            return m.group(1)
        return period

    rows = []
    # Sort records ascending by period
    sorted_records = sorted(
        records,
        key=lambda r: r.get("period", ""),
    )

    for rec in sorted_records:
        period = rec.get("period", "")
        year = _extract_year(period)

        val = rec.get("value_crore")
        raw = rec.get("raw_text") or "—"
        unit = rec.get("unit") or "—"

        if val is not None:
            from services.normalizer import format_crore_display, is_ratio_metric
            val_text = format_crore_display(float(val))
            if is_ratio_metric(rec.get("metric", "")):
                display = f"{val_text}%"
                conversion = f"ratio = {val_text}%"
            elif unit == "thousand":
                conversion = f"÷10,000 = ₹{val_text} cr"
                display = f"₹{val_text} cr"
            elif unit == "lakh":
                conversion = f"÷100 = ₹{val_text} cr"
                display = f"₹{val_text} cr"
            elif unit == "crore":
                conversion = f"direct = ₹{val_text} cr"
                display = f"₹{val_text} cr"
            else:
                conversion = f"= ₹{val_text} cr"
                display = f"₹{val_text} cr"
        else:
            display = "Not Disclosed"
            conversion = "—"

        rows.append({
            "Year":           year,
            "Date":           period,
            "Metric":         rec.get("metric", ""),
            "Raw Text":       raw,
            "Unit":           unit,
            "Conversion":     conversion,
            "Final Value":    display,
            "Confidence":     f"{float(rec.get('confidence', 0)):.2f}",
            "Status":         rec.get("status", ""),
            "Page":           str(rec.get("page_number") or "—"),
            "Source File":    str(rec.get("source_filename") or rec.get("source_file") or "—"),
            "Notes":          str(rec.get("notes") or ""),
        })

    return pd.DataFrame(rows)


def _values_match(a: float | None, b: float | None) -> bool:
    """Compare two numeric values tolerantly; treat both-None as equal."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-6


def dataframe_to_records(
    edited_df: pd.DataFrame,
    base_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge user edits from the data_editor DataFrame back into review records.

    * Approved Value and Notes are editable; other columns are ignored
      to prevent the user from changing provenance fields.
    * Approved Value is normalized (₹ crore or %); value_crore is kept in sync.
    * If the new Approved Value differs from the extraction baseline, manual_edit=True.
    * The Status column is fully recomputed; user changes to Status are not honoured.
    """
    by_key = {(r["metric"], r["period"]): r for r in base_records}
    updated: list[dict[str, Any]] = []

    for _, row in edited_df.iterrows():
        key = (row.get(COL_METRIC, ""), row.get(COL_PERIOD, ""))
        base = by_key.get(key)
        if base is None:
            continue
        new_record = dict(base)

        new_approved = _coerce_number(row.get(COL_APPROVED))
        baseline = base.get("initial_approved")
        new_record["approved_value"] = new_approved
        new_record["manual_edit"] = not _values_match(new_approved, baseline)
        if new_approved is None:
            new_record["value_crore"] = None
        else:
            new_record["value_crore"] = new_approved
        new_record["notes"] = str(row.get(COL_NOTES, "") or "")
        updated.append(new_record)

    # Preserve any records that somehow vanished from the editor (defensive)
    seen_keys = {(r["metric"], r["period"]) for r in updated}
    for r in base_records:
        if (r["metric"], r["period"]) not in seen_keys:
            updated.append(dict(r))

    apply_status_to_records(updated)
    return updated


def pivot_review_table(
    records: list[dict[str, Any]],
    periods: tuple[str, ...],
) -> pd.DataFrame:
    """
    Build a detailed provenance table matching the data_editor columns.
    Columns: Metric | Period | Extracted Value | Approved Value |
             Original Unit | Converted Value (Crore) | Source Document |
             Page Number | Confidence | Status | Analyst Override
    One row per metric per period, sorted by period ascending.
    """
    import re

    def _period_sort_key(period: str) -> int:
        m = re.search(r"20(\d{2})", period)
        if m:
            return int(m.group(1))
        m = re.search(r"FY(\d{2})", period, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0

    by_key = {(r["metric"], r["period"]): r for r in records}
    sorted_periods = sorted(periods, key=_period_sort_key)

    rows = []
    for period in sorted_periods:
        for metric in APPROVED_METRICS:
            rec = by_key.get((metric, period))

            if rec is None or rec.get("approved_value") is None:
                extracted = ""
                approved = NOT_DISCLOSED
                unit = "—"
                converted = NOT_DISCLOSED
                source_doc = "—"
                page = "—"
                confidence = ""
                status = rec.get("status", "missing") if rec else "missing"
                analyst_override = ""
            else:
                val = float(rec["approved_value"])
                val_text = format_crore_display(val)

                if is_ratio_metric(metric):
                    approved = f"{val_text}%"
                    converted = f"{val_text}%"
                else:
                    approved = f"₹{val_text} cr"
                    converted = f"₹{val_text} cr"

                ext_val = rec.get("extracted_value") or rec.get("value_original")
                if ext_val is not None:
                    try:
                        extracted = format_crore_display(float(ext_val))
                    except (TypeError, ValueError):
                        extracted = str(ext_val)
                else:
                    extracted = "—"

                unit = rec.get("original_unit") or rec.get("unit") or "—"
                source_doc = rec.get("source_document") or "—"
                page = str(rec.get("page_number") or "—")
                confidence = f"{float(rec.get('confidence', 0)):.2f}"
                status = rec.get("status", "")
                analyst_override = "Yes" if rec.get("manual_edit") else ""

            rows.append({
                "Metric": metric,
                "Period": period,
                "Extracted Value": str(extracted),
                "Approved Value (Crore/%)": str(approved),
                "Original Unit": str(unit),
                "Converted Value (Crore)": str(converted),
                "Source Document": str(source_doc),
                "Page Number": str(page),
                "Confidence": str(confidence),
                "Status": str(status),
                "Analyst Override": str(analyst_override),
            })

    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].astype(str)
    return df


def revalidate_approved(records: list[dict[str, Any]]) -> list[str]:
    """
    Re-run validation against the *approved* values (not the extracted ones).

    Returns a deduplicated list of human-readable warning strings.
    """
    warnings: list[str] = []
    cross_pairs = _collect_cross_warning_pairs(records)

    for rec in records:
        val = rec.get("approved_value")
        if val is None:
            warnings.append(f"{rec['metric']} for {rec['period']} is missing.")
            continue
        sanity = check_metric_sanity(rec["metric"], float(val))
        for s in sanity:
            warnings.append(f"{s} ({rec['period']})")
        if (rec["metric"], rec["period"]) in cross_pairs and rec["metric"] == "Tier I Capital Ratio":
            warnings.append(
                f"Tier I exceeds Capital Adequacy Ratio for period {rec['period']}."
            )

    seen: set[str] = set()
    unique: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique


def records_to_csv_bytes(records: list[dict[str, Any]]) -> bytes:
    """Return UTF-8 CSV bytes for the reviewed extraction dataset."""
    rows = []
    for rec in records:
        rows.append(
            {
                "metric": rec["metric"],
                "period": rec["period"],
                "extracted_value": rec.get("extracted_value"),
                "approved_value": rec.get("approved_value"),
                "original_unit": rec.get("original_unit"),
                "value_crore": rec.get("value_crore"),
                "page_number": rec.get("page_number"),
                "source_document": rec.get("source_document"),
                "source_filename": rec.get("source_filename"),
                "confidence": rec.get("confidence"),
                "manual_edit": rec.get("manual_edit"),
                "status": rec.get("status"),
                "notes": rec.get("notes", ""),
            }
        )
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def _is_half_year_period(period: str) -> bool:
    return bool(re.match(r"^H1FY\d{2}$", period or "", re.IGNORECASE))


def _period_sort_key(period: str) -> int:
    m = re.search(r"20(\d{2})$", period)
    if m:
        return int(m.group(1))
    m = re.search(r"FY(\d{2})", period, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


def periods_from_records(records: list[dict[str, Any]]) -> tuple[str, ...]:
    """Unique periods present in records, sorted chronologically."""
    periods = sorted(
        {r["period"] for r in records if r.get("period")},
        key=_period_sort_key,
    )
    return tuple(periods)


def split_records_by_table(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a flat record list into (table1, table2) by table kind or period shape."""
    table1: list[dict[str, Any]] = []
    table2: list[dict[str, Any]] = []
    for rec in records:
        table_kind = rec.get("table")
        if table_kind == "yearly":
            table1.append(rec)
        elif table_kind == "half_year":
            table2.append(rec)
        elif _is_half_year_period(rec.get("period", "")):
            table2.append(rec)
        else:
            table1.append(rec)
    return table1, table2


def merge_table_records(
    table1: list[dict[str, Any]],
    table2: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Concatenate Phase 2 table1 + table2 records into one flat list."""
    return list(table1) + list(table2)


def final_approval_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    """Counts by status — used in the final approval banner."""
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    return counts
