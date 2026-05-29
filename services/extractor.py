"""
Financial extraction entry point (V2 — Deterministic Report Reconstruction Engine).

Implementation lives in ``services.reconstruction``; this module re-exports the
public API used by ``app.py`` and tests.
"""

from services.reconstruction import (
    FinancialExtractionResult,
    build_pivot_dataframe,
    build_provenance_dataframe,
    run_financial_extraction,
)

__all__ = [
    "FinancialExtractionResult",
    "run_financial_extraction",
    "build_pivot_dataframe",
    "build_provenance_dataframe",
]
