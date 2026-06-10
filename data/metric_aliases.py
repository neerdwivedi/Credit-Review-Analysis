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
    "Interest Earned",
    "Interest Expended",
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
    "Interest Earned":        "flow",
    "Interest Expended":      "flow",
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

MONTH_END_DAY: dict[str, tuple[str, str]] = {
    "March":     ("03", "31"),
    "June":      ("06", "30"),
    "September": ("09", "30"),
    "December":  ("12", "31"),
}


def get_table1_periods(
    fy_year: int,
    year_end_month: str = "March",
) -> tuple[str, ...]:
    month_num, day = MONTH_END_DAY.get(year_end_month, ("03", "31"))
    return (
        f"{day}.{month_num}.{fy_year}",
        f"{day}.{month_num}.{fy_year - 1}",
        f"{day}.{month_num}.{fy_year - 2}",
    )


def get_table2_periods(
    fy_year: int,
    year_end_month: str = "March",
) -> tuple[str, ...]:
    yy = fy_year % 100
    yy_prior = (fy_year - 1) % 100
    return (
        f"H1FY{yy:02d}",
        f"H1FY{yy_prior:02d}",
    )


def get_quarter_periods(
    fy_year: int,
    year_end_month: str = "March",
) -> tuple[str, ...]:
    yy = fy_year % 100
    yy_prior = (fy_year - 1) % 100
    return (
        f"Q1FY{yy:02d}",
        f"Q2FY{yy:02d}",
        f"Q1FY{yy_prior:02d}",
        f"Q2FY{yy_prior:02d}",
    )


TABLE1_PERIODS: tuple[str, ...] = get_table1_periods(2026, "March")
TABLE2_PERIODS: tuple[str, ...] = get_table2_periods(2026, "March")

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
    "h1 fy'26": "H1FY26",
    "half year fy26": "H1FY26", "half-year fy26": "H1FY26",
    "h1 fy25": "H1FY25", "h1fy25": "H1FY25", "h1 fy 25": "H1FY25",
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
        "Revenue from Operations", "Revenue From Operations",
        "Total Revenue from Operations",
    ],
    "NII": [
        "NII",
        "Net Interest Income",
        "Net Interest Income (NII)",
        "net interest income",
    ],
    "PAT": [
        "PAT", "Profit After Tax", "Net Profit",
        "Profit for the year", "Net profit for the year",
        "Profit/(Loss) for the year", "Profit after tax (PAT)",
        "Net Profit After Tax",
        "Profit after tax", "Profit After Tax (PAT)",
        "PAT (₹ Cr)", "PAT (Rs Cr)", "PAT (Rs. Cr)",
        "Profit for the period", "Net profit for the period",
    ],
    "Total Assets": [
        "Total Assets", "Balance Sheet Size", "Total Balance Sheet",
    ],
    "Borrowings": [
        "Borrowings",
        "Total Borrowings",
        "Borrowings (other than debt securities)",
        "Debt securities",
        "Total debt",
    ],
    "Investments": [
        "Investments", "Investment Portfolio", "Total Investments",
    ],
    "Advances": [
        "Advances",
        "Net Advances",
        "Gross Advances",
        "Loans and Advances",
        "Net Loans and Advances",
        "Loans",
        "Net Loans",
        "Loan Book",
        "Total Loans",
        "Retail loans",
        "Consol Book",
        "Consolidated Book",
        "Retail Book",
    ],
    "Deposits": [
        "Deposits",
        "Total Deposits",
        "Customer Deposits",
        "Public deposits",
        "Fixed deposits",
    ],
    "Capital Adequacy Ratio": [
        "Capital Adequacy Ratio",
        "Capital Adequacy ratio",
        "CAR",
        "CRAR",
        "Capital to Risk Weighted Assets Ratio",
        "Capital Adequacy Ratio (CRAR)",
        "Total Capital Adequacy Ratio",
        "Overall Capital Adequacy Ratio",
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
        "NS3%", "NS3", "Net Stage 3",
    ],
    "ROA": [
        "ROA", "Return on Assets", "Return on Average Assets",
    ],
    "ROE": [
        "ROE", "Return on Equity", "Return on Net Worth",
        "Return on Average Equity",
    ],
    "Interest Earned": [
        "Interest Earned",
        "Interest income",
        "Income from advances",
        "Interest and discount",
        "Schedule 13",
    ],
    "Interest Expended": [
        "Interest Expended",
        "Interest expense",
        "Interest paid",
        "Finance costs",
        "Finance Cost",
        "Cost of borrowings",
        "Interest and finance charges",
        "Schedule 15",
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
