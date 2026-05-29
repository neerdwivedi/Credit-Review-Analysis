"""
Approved financial metrics and alias dictionary for deterministic extraction.

Only metrics listed here may be extracted. No derived or calculated fields.
"""

from __future__ import annotations

# Canonical display order for report tables
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
    "ROA",
    "ROE",
)

# Metrics expressed as percentages — never converted to Rs crore
RATIO_METRICS: frozenset[str] = frozenset(
    {
        "Capital Adequacy Ratio",
        "Tier I Capital Ratio",
        "ROA",
        "ROE",
    }
)

# Currency metrics — unit detection and crore conversion apply
CURRENCY_METRICS: frozenset[str] = frozenset(
    m for m in APPROVED_METRICS if m not in RATIO_METRICS
)

NOT_DISCLOSED = "Not Disclosed"

# Table 1 — annual report, March year-end only
TABLE1_PERIODS: tuple[str, ...] = (
    "31.03.2025",
    "31.03.2024",
    "31.03.2023",
)

# Table 2 — investor presentation, half-year September ended only
TABLE2_PERIODS: tuple[str, ...] = (
    "H1FY26",
    "H1FY25",
)

# Map various header spellings to canonical period labels
TABLE1_PERIOD_ALIASES: dict[str, str] = {
    "31.03.2025": "31.03.2025",
    "31/03/2025": "31.03.2025",
    "31-03-2025": "31.03.2025",
    "31.3.2025": "31.03.2025",
    "31/3/2025": "31.03.2025",
    "march 31, 2025": "31.03.2025",
    "31 march 2025": "31.03.2025",
    "as at 31.03.2025": "31.03.2025",
    "for the year ended 31.03.2025": "31.03.2025",
    "fy25": "31.03.2025",
    "fy 25": "31.03.2025",
    "2024-25": "31.03.2025",
    "31.03.2024": "31.03.2024",
    "31/03/2024": "31.03.2024",
    "31-03-2024": "31.03.2024",
    "31.3.2024": "31.03.2024",
    "march 31, 2024": "31.03.2024",
    "31 march 2024": "31.03.2024",
    "fy24": "31.03.2024",
    "fy 24": "31.03.2024",
    "2023-24": "31.03.2024",
    "31.03.2023": "31.03.2023",
    "31/03/2023": "31.03.2023",
    "31-03-2023": "31.03.2023",
    "31.3.2023": "31.03.2023",
    "march 31, 2023": "31.03.2023",
    "31 march 2023": "31.03.2023",
    "fy23": "31.03.2023",
    "fy 23": "31.03.2023",
    "2022-23": "31.03.2023",
}

TABLE2_PERIOD_ALIASES: dict[str, str] = {
    "h1fy26": "H1FY26",
    "h1 fy26": "H1FY26",
    "h1 fy 26": "H1FY26",
    "half year fy26": "H1FY26",
    "half year fy 26": "H1FY26",
    "half-year fy26": "H1FY26",
    "h1 fy25": "H1FY25",
    "h1fy25": "H1FY25",
    "half year fy25": "H1FY25",
    "half year fy 25": "H1FY25",
    "sep 2025 half year": "H1FY26",
    "september 2025 half year": "H1FY26",
    "half year ended sep 2025": "H1FY26",
    "half year ended september 2025": "H1FY26",
    "6 months ended 30 september 2025": "H1FY26",
    "six months ended 30 september 2025": "H1FY26",
    "sep 2024 half year": "H1FY25",
    "september 2024 half year": "H1FY25",
    "half year ended sep 2024": "H1FY25",
    "6 months ended 30 september 2024": "H1FY25",
}

# Period patterns that must be rejected for Table 2 (quarterly, not half-year)
TABLE2_REJECT_PATTERNS: tuple[str, ...] = (
    "q1fy",
    "q2fy",
    "q3fy",
    "q4fy",
    "q1 fy",
    "q2 fy",
    "quarter ended",
    "three months ended",
    "3 months ended",
)

# Metric alias dictionary — first entry is canonical name
METRIC_ALIASES: dict[str, list[str]] = {
    "Total Income": ["Total Income", "Net Total Income"],
    "NII": ["NII", "Net Interest Income"],
    "PAT": [
        "PAT",
        "Profit After Tax",
        "Net Profit",
        "Profit for the year",
        "Net profit for the year",
        "Profit/(Loss) for the year",
    ],
    "Total Assets": [
        "Total Assets",
        "Balance Sheet Size",
        "Total Balance Sheet",
    ],
    "Borrowings": ["Borrowings", "Total Borrowings"],
    "Investments": ["Investments", "Investment Portfolio"],
    "Advances": [
        "Advances",
        "Net Advances",
        "Gross Advances",
        "Loans and Advances",
    ],
    "Deposits": ["Deposits", "Total Deposits", "Customer Deposits"],
    "Capital Adequacy Ratio": [
        "Capital Adequacy Ratio",
        "CAR",
        "CRAR",
        "Capital to Risk Weighted Assets Ratio",
    ],
    "Tier I Capital Ratio": [
        "Tier I Capital Ratio",
        "Tier I",
        "Tier-1",
        "Tier 1 Capital Ratio",
        "CET-I",
        "CET1",
        "Common Equity Tier 1",
    ],
    "ROA": ["ROA", "Return on Assets", "Return on Average Assets"],
    "ROE": [
        "ROE",
        "Return on Equity",
        "Return on Net Worth",
    ],
}

# Keywords to locate standalone financial statement sections (lowercase)
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
