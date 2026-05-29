"""
Row-label exclusion patterns per metric (V2).

Applied after fuzzy matching to reject subsidiary / divestment / contribution rows.
"""

from __future__ import annotations

METRIC_ROW_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "PAT": (
        "pat on",
        "pat contribution",
        "total pat",
        "subsidiaries",
        "subsidiary",
        "divestment",
        "kgi",
        "shareholders",
    ),
    "Total Income": (
        "operating income before",
        "fee and services",
        "other income",
        "trading and mtm",
    ),
    "NII": (
        "non-interest",
        "fee income",
    ),
    "Deposits": (
        "deposit growth",
        "deposit mix",
    ),
    "Advances": (
        "advance growth",
    ),
}
