"""V2 — Deterministic Report Reconstruction Engine."""

__all__ = [
    "FinancialExtractionResult",
    "run_financial_extraction",
    "build_pivot_dataframe",
    "build_provenance_dataframe",
]


def __getattr__(name: str):
    if name in __all__:
        from services.reconstruction import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
