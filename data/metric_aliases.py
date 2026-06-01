"""
Approved financial metrics — calibrated to match fund credit review template row labels exactly.
GNPA and NNPA are ratio metrics in this template (expressed as %, e.g. 1.48, 0.34).
"""

from __future__ import annotations

APPROVED_METRICS: tuple[str, ...] = (
    "Total Income",
    "NII",
    "PAT",
    "Total Assets",
    "Borrowings",
    "Investments",
    "Advances",
    "Deposits",
    "Capital Adequacy Ratio",
    "Tier I Capital Ratio",
    "GNPA",
    "NNPA",
    "ROA",
    "ROE",
)

# flow   = P&L item, accumulates: Q1 + Q2 = H1
# snapshot = balance sheet, period-end only: never add across periods
# ratio  = expressed as %, never convert to crore, never add
METRIC_BEHAVIOUR: dict[str, str] = {
    "Total Income":           "flow",
    "NII":                    "flow",
    "PAT":                    "flow",
    "Total Assets":           "snapshot",
    "Borrowings":             "snapshot",
    "Investments":            "snapshot",
    "Advances":               "snapshot",
    "Deposits":               "snapshot",
    "Capital Adequacy Ratio": "ratio",
    "Tier I Capital Ratio":   "ratio",
    "GNPA":                   "ratio",   # template shows 1.48, 1.49 — percentage, not crore
    "NNPA":                   "ratio",   # template shows 0.34, 0.43 — percentage, not crore
    "ROA":                    "ratio",
    "ROE":                    "ratio",
}

RATIO_METRICS: frozenset[str] = frozenset(
    m for m, b in METRIC_BEHAVIOUR.items() if b == "ratio"
)
CURRENCY_METRICS: frozenset[str] = frozenset(
    m for m in APPROVED_METRICS if m not in RATIO_METRICS
)

def is_flow_metric(metric: str) -> bool:
    return METRIC_BEHAVIOUR.get(metric) == "flow"

def is_snapshot_metric(metric: str) -> bool:
    return METRIC_BEHAVIOUR.get(metric) == "snapshot"


NOT_DISCLOSED = "Not Disclosed"

TABLE1_PERIODS: tuple[str, ...] = ("31.03.2025", "31.03.2024", "31.03.2023")
TABLE2_PERIODS: tuple[str, ...] = ("H1FY26", "H1FY25")

TABLE1_PERIOD_ALIASES: dict[str, str] = {
    "31.03.2025": "31.03.2025", "31/03/2025": "31.03.2025",
    "31-03-2025": "31.03.2025", "31.3.2025": "31.03.2025",
    "march 31, 2025": "31.03.2025", "31 march 2025": "31.03.2025",
    "as at 31.03.2025": "31.03.2025",
    "for the year ended 31.03.2025": "31.03.2025",
    "fy25": "31.03.2025", "fy 25": "31.03.2025", "2024-25": "31.03.2025",
    "31.03.2024": "31.03.2024", "31/03/2024": "31.03.2024",
    "31-03-2024": "31.03.2024", "31.3.2024": "31.03.2024",
    "march 31, 2024": "31.03.2024", "31 march 2024": "31.03.2024",
    "fy24": "31.03.2024", "fy 24": "31.03.2024", "2023-24": "31.03.2024",
    "31.03.2023": "31.03.2023", "31/03/2023": "31.03.2023",
    "31-03-2023": "31.03.2023", "31.3.2023": "31.03.2023",
    "march 31, 2023": "31.03.2023", "31 march 2023": "31.03.2023",
    "fy23": "31.03.2023", "fy 23": "31.03.2023", "2022-23": "31.03.2023",
}

TABLE2_PERIOD_ALIASES: dict[str, str] = {
    "h1fy26": "H1FY26", "h1 fy26": "H1FY26", "h1 fy 26": "H1FY26",
    "half year fy26": "H1FY26", "half-year fy26": "H1FY26",
    "h1 fy25": "H1FY25", "h1fy25": "H1FY25",
    "half year fy25": "H1FY25",
    "sep 2025 half year": "H1FY26",
    "half year ended sep 2025": "H1FY26",
    "6 months ended 30 september 2025": "H1FY26",
    "six months ended 30 september 2025": "H1FY26",
    "sep 2024 half year": "H1FY25",
    "half year ended sep 2024": "H1FY25",
    "6 months ended 30 september 2024": "H1FY25",
}

TABLE2_REJECT_PATTERNS: tuple[str, ...] = (
    "q1fy", "q2fy", "q3fy", "q4fy",
    "q1 fy", "q2 fy",
    "quarter ended",
    "three months ended",
    "3 months ended",
)

METRIC_ALIASES: dict[str, list[str]] = {
    "Total Income": [
        "Total Income", "Net Total Income", "Total Net Income",
    ],
    "NII": [
        "NII",
        "Net Interest Income",
        "Net Interest Income (NII)",
        "net interest income",
        "Interest Earned",
        "Net Interest Earned",
        "Schedule 13",
        "Interest income net",
    ],
    "PAT": [
        "PAT", "Profit After Tax", "Net Profit",
        "Profit for the year", "Net profit for the year",
        "Profit/(Loss) for the year", "Profit after tax (PAT)",
        "Net Profit After Tax",
    ],
    "Total Assets": [
        "Total Assets", "Balance Sheet Size", "Total Balance Sheet",
    ],
    "Borrowings": [
        "Borrowings", "Total Borrowings",
    ],
    "Investments": [
        "Investments", "Investment Portfolio", "Total Investments",
    ],
    "Advances": [
        "Advances", "Net Advances", "Gross Advances",
        "Loans and Advances", "Net Loans and Advances",
    ],
    "Deposits": [
        "Deposits", "Total Deposits", "Customer Deposits",
    ],
    "Capital Adequacy Ratio": [
        "Capital Adequacy Ratio", "Capital Adequacy ratio",
        "CAR", "CRAR",
        "Capital to Risk Weighted Assets Ratio",
        "Capital Adequacy Ratio (CRAR)",
    ],
    "Tier I Capital Ratio": [
        "Tier I Capital Ratio", "Capital Adequacy ratio (Tier – I)",
        "Capital Adequacy ratio (Tier - I)",
        "Capital Adequacy Ratio (Tier I)",
        "Tier I", "Tier-1", "Tier 1 Capital Ratio",
        "CET-I", "CET1", "Common Equity Tier 1", "Tier I Ratio",
    ],
    "GNPA": [
        "GNPA", "Gross NPA", "Gross NPA Ratio", "GNPA Ratio",
        "Gross NPA %", "Gross Non Performing Assets",
        "Gross Non-Performing Assets",
    ],
    "NNPA": [
        "NNPA", "Net NPA", "Net NPA Ratio", "NNPA Ratio",
        "Net NPA %", "Net Non Performing Assets",
        "Net Non-Performing Assets",
    ],
    "ROA": [
        "ROA", "Return on Assets", "Return on Average Assets",
    ],
    "ROE": [
        "ROE", "Return on Equity", "Return on Net Worth",
        "Return on Average Equity",
    ],
}

STANDALONE_SECTION_KEYWORDS: tuple[str, ...] = (
    "standalone financial statements",
    "standalone balance sheet",
    "standalone statement of profit and loss",
    "standalone statement of profit & loss",
    "standalone financial results",
    "standalone profit and loss",
)

CONSOLIDATED_KEYWORDS: tuple[str, ...] = (
    "consolidated financial",
    "consolidated balance sheet",
    "consolidated statement of profit",
    "consolidated financial results",
)
