"""V2 orchestrator — wires yearly + half-year flows to Phase 1/3 UI."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

import pandas as pd

from data.metric_aliases import APPROVED_METRICS, NOT_DISCLOSED
from services.normalizer import format_crore_display
from services.reconstruction.document import DocumentContext
from services.reconstruction.half_year import extract_half_year_financials
from services.reconstruction.yearly import extract_yearly_financials
from utils.constants import DOC_TYPE_ANNUAL_REPORT, DOC_TYPE_INVESTOR_PRESENTATION

logger = logging.getLogger("credit_review")


@dataclass
class FinancialExtractionResult:
    table1_records: list[dict[str, Any]] = field(default_factory=list)
    table2_records: list[dict[str, Any]] = field(default_factory=list)
    table1_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    table2_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    table1_provenance: pd.DataFrame = field(default_factory=pd.DataFrame)
    table2_provenance: pd.DataFrame = field(default_factory=pd.DataFrame)
    table1_warnings: list[str] = field(default_factory=list)
    table2_warnings: list[str] = field(default_factory=list)
    validation_summary: str = ""


def _build_table_count_map(
    table_preview: list[dict[str, int]] | None,
) -> dict[int, int]:
    if not table_preview:
        return {}
    return {int(row["page"]): int(row.get("table_count", 0)) for row in table_preview}


def _context_from_phase1(res: dict[str, Any]) -> DocumentContext | None:
    pdf_bytes = res.get("pdf_bytes")
    if not pdf_bytes:
        return None
    return DocumentContext(
        pdf_bytes=pdf_bytes,
        filename=res["filename"],
        doc_type=res["doc_type"],
        pages=res["pages"],
        table_count_by_page=_build_table_count_map(res.get("table_preview")),
    )


def build_pivot_dataframe(
    records: list[dict[str, Any]],
    periods: tuple[str, ...],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for metric in APPROVED_METRICS:
        row: dict[str, Any] = {"Metric": metric}
        for period in periods:
            match = [r for r in records if r["metric"] == metric and r["period"] == period]
            if not match:
                row[period] = NOT_DISCLOSED
                continue
            rec = match[0]
            val = rec.get("display_value", NOT_DISCLOSED)
            page = rec.get("page_number")
            if page and rec.get("status") == "extracted":
                row[period] = f"{val} (p.{page})"
            else:
                row[period] = val
        rows.append(row)
    return pd.DataFrame(rows)


def build_provenance_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for rec in records:
        val_crore = rec.get("value_crore")
        if val_crore is not None:
            display_crore = format_crore_display(val_crore)
        else:
            display_crore = rec.get("display_value", NOT_DISCLOSED)
        rows.append(
            {
                "Metric": rec["metric"],
                "Period": rec["period"],
                "Value (Crore / %)": display_crore,
                "Original Unit": rec.get("unit") or "—",
                "Page Number": rec.get("page_number") or "—",
                "Source Document": rec.get("source_document", ""),
                "Source File": rec.get("source_file") or rec.get("source_filename") or "—",
                "Confidence": rec.get("confidence", 0),
                "Status": rec.get("status", ""),
            }
        )
    return pd.DataFrame(rows)


def run_financial_extraction(
    phase1_results: list[dict[str, Any]],
    *,
    on_status: Callable[[str], None] | None = None,
    source_map=None,
    fy_year: int = 2026,
    year_end_month: str = "March",
    h1_fy_year: int | None = None,
) -> FinancialExtractionResult:
    """Run V2 deterministic reconstruction on Phase 1 outputs."""
    from data.metric_aliases import get_table1_periods, get_table2_periods
    TABLE1_PERIODS = get_table1_periods(fy_year, year_end_month)
    h1_year = h1_fy_year if h1_fy_year else fy_year
    TABLE2_PERIODS = get_table2_periods(h1_year, year_end_month)

    from services.validator import (
        build_human_validation_summary,
        build_validation_warnings,
    )

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    t_total = time.perf_counter()
    annual: list[DocumentContext] = []
    investor: list[DocumentContext] = []

    _status("Preparing financial extraction from scanned documents…")
    for res in phase1_results:
        ctx = _context_from_phase1(res)
        if ctx is not None:
            ctx.vision_api_key = res.get("vision_api_key", "")
        if ctx is None:
            continue
        if res["doc_type"] == DOC_TYPE_ANNUAL_REPORT:
            annual.append(ctx)
        elif res["doc_type"] == DOC_TYPE_INVESTOR_PRESENTATION:
            investor.append(ctx)

    if annual:
        _status("Reading uploaded annual report…")
        _status("Scanning financial sections…")
        _status("Detecting standalone financial statements…")
    elif investor:
        _status("No annual report — using investor presentation for yearly metrics…")

    yearly_docs = annual if annual else investor
    logger.info(
        "[V2] Flow A — yearly (%d doc(s), source=%s)",
        len(yearly_docs),
        "annual_report" if annual else ("investor_presentation" if investor else "none"),
    )
    _status("Extracting yearly financial metrics…")
    _status("Matching PAT, NII, borrowings and deposits…")
    table1_records = extract_yearly_financials(
        yearly_docs,
        periods=TABLE1_PERIODS,
    )

    if investor:
        _status("Reading investor presentation…")
    logger.info("[V2] Flow B — half-year (%d presentations)", len(investor))
    _status("Extracting H1FY26 and H1FY25 values…")
    table2_records = extract_half_year_financials(
        investor,
        periods=TABLE2_PERIODS,
        h1_fy_year=h1_year,
        year_end_month=year_end_month,
    )

    _status("Validating extracted numbers…")
    table1_df = build_pivot_dataframe(table1_records, TABLE1_PERIODS)
    table2_df = build_pivot_dataframe(table2_records, TABLE2_PERIODS)
    table1_prov = build_provenance_dataframe(table1_records)
    table2_prov = build_provenance_dataframe(table2_records)

    table1_warnings = build_validation_warnings(table1_records, table_id=1)
    table2_warnings = build_validation_warnings(table2_records, table_id=2)
    summary = build_human_validation_summary(
        table1_warnings,
        table2_warnings,
        table1_records,
        table2_records,
    )
    _status("Preparing review table…")

    logger.info("[V2] Total time: %.2fs", time.perf_counter() - t_total)

    return FinancialExtractionResult(
        table1_records=table1_records,
        table2_records=table2_records,
        table1_df=table1_df,
        table2_df=table2_df,
        table1_provenance=table1_prov,
        table2_provenance=table2_prov,
        table1_warnings=table1_warnings,
        table2_warnings=table2_warnings,
        validation_summary=summary,
    )
