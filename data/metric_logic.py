"""
Financial logic definitions — metric extraction rules by company type.

Defines what each metric IS in financial statement terms. The extractor uses
these rules to find labels, compute derived values, and reject implausible hits.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("credit_review")

from data.metric_aliases import APPROVED_METRICS

# ---------------------------------------------------------------------------
# Per-metric definitions: labels, computation, sections, sanity notes
# ---------------------------------------------------------------------------

METRIC_LOGIC: dict[str, dict[str, Any]] = {
    "Total Assets": {
        "notes": "Total of all assets on standalone balance sheet.",
        "primary_labels": [
            "Total Assets",
            "TOTAL ASSETS",
            "Total assets",
        ],
        "computed_from": {
            "sum": ["Total Financial Assets", "Total Non-Financial Assets"],
            "optional_sum": True,
        },
        "balance_sheet_section": "assets",
        "row_position": "last_in_section",
    },
    "Advances": {
        "notes": "Net loans / advances to customers.",
        "primary_labels": [
            "Loans",
            "Advances",
            "Net Advances",
            "Loans and Advances",
            "Net Loans",
            "Loans (net)",
            "Loan Book",
            "Consol Book",
            "Consolidated Book",
            "Retail Book",
        ],
        "balance_sheet_section": "financial_assets",
    },
    "Investments": {
        "notes": "Investment portfolio on balance sheet.",
        "primary_labels": [
            "Investments",
            "Investment Portfolio",
            "Total Investments",
            "Investments (net)",
        ],
        "balance_sheet_section": "financial_assets",
    },
    "Borrowings": {
        "notes": "Total funding excl. deposits; Ind-AS may split across lines.",
        "primary_labels": [
            "Total Borrowings",
            "Borrowings",
        ],
        "computed_from": {
            "sum": [
                "Debt Securities",
                "Borrowings (Other than Debt Securities)",
                "Borrowings Other than Debt Securities",
                "Subordinated Liabilities",
            ],
            "optional_sum": True,
        },
        "balance_sheet_section": "financial_liabilities",
    },
    "Deposits": {
        "notes": "Customer deposits; HFCs typically have none.",
        "primary_labels": [
            "Deposits",
            "Total Deposits",
            "Customer Deposits",
            "Public Deposits",
        ],
        "balance_sheet_section": "financial_liabilities",
        "hfc_default": None,
    },
    "Total Income": {
        "notes": "Total revenue on standalone P&L; equals Revenue from Operations when that is the disclosed line.",
        "primary_labels": [
            "Total Income",
            "Total Income (1+2)",
            "Total Income (1) + (2)",
            "Total Revenue",
            "Total Revenue from Operations",
            "Total Revenue (1+2)",
            "Total Income from Operations",
            "Revenue from Operations",
            "Revenue From Operations",
        ],
        "computed_from": {
            "formula": "Revenue from Operations",
            "source_component": "Revenue from Operations",
        },
        "pnl_section": "revenue",
        "row_position": "last_revenue_total",
    },
    "NII": {
        "notes": "Net Interest Income = Interest Income − Finance Costs.",
        "primary_labels": [
            "Net Interest Income",
            "NII",
            "Net Interest Income (NII)",
        ],
        "computed_from": {
            "formula": "Interest Earned - Interest Expended",
            "components": {
                "Interest Earned": [
                    "Interest Income",
                    "Interest Earned",
                    "Income from Advances",
                    "Interest and Discount",
                ],
                "Interest Expended": [
                    "Finance Costs",
                    "Finance costs",
                    "Interest Expense",
                    "Interest Expended",
                    "Interest Paid",
                    "Borrowing Costs",
                ],
            },
        },
        "pnl_section": "calculated",
    },
    "PAT": {
        "notes": "Profit after tax = Profit Before Tax − Tax on standalone P&L.",
        "primary_labels": [
            "Profit for the year",
            "Profit After Tax",
            "PAT",
            "Net Profit",
            "Profit for the Year",
            "Net Profit for the Year",
            "Profit/(Loss) for the year",
            "Profit after tax",
            "PAT (₹ Cr)",
            "PAT (Rs Cr)",
        ],
        "computed_from": {
            "formula": "Profit Before Tax - Tax",
            "components": ["Profit Before Tax", "Tax"],
        },
        "pnl_section": "profit",
        "row_position": "last_profit_line",
        "exclude_pages": ["cover", "highlights", "key_metrics"],
    },
    "Interest Earned": {
        "notes": "Gross interest income.",
        "primary_labels": [
            "Interest Income",
            "Interest Earned",
            "Income from Advances",
            "Interest and Discount",
            "Interest income",
        ],
        "pnl_section": "revenue",
    },
    "Interest Expended": {
        "notes": "Gross interest / finance expense.",
        "primary_labels": [
            "Finance Costs",
            "Finance costs",
            "Interest Expense",
            "Interest Expended",
            "Interest Paid",
            "Borrowing Costs",
            "Cost of Funds",
        ],
        "pnl_section": "expenses",
    },
    "Capital Adequacy Ratio": {
        "notes": "CRAR as %, not crore.",
        "primary_labels": [
            "Capital Adequacy Ratio",
            "CRAR",
            "CAR",
            "Capital to Risk Weighted Assets Ratio",
            "Capital Adequacy Ratio (CRAR)",
        ],
        "is_ratio": True,
        "valid_range": (5.0, 50.0),
    },
    "Tier I Capital Ratio": {
        "notes": "Tier I / CET1 as % of RWA.",
        "primary_labels": [
            "Tier I",
            "Tier-1",
            "Tier 1",
            "Tier I Capital Ratio",
            "CET1",
            "Common Equity Tier 1",
        ],
        "is_ratio": True,
        "valid_range": (4.0, 40.0),
    },
    "GNPA": {
        "notes": "Gross NPA ratio as %.",
        "primary_labels": [
            "Gross NPA Ratio",
            "GNPA Ratio",
            "GNPA %",
            "Gross NPA %",
            "Gross NPA",
            "GNPA",
        ],
        "is_ratio": True,
        "valid_range": (0.0, 30.0),
    },
    "NNPA": {
        "notes": "Net NPA / NS3 ratio as %.",
        "primary_labels": [
            "Net NPA Ratio",
            "NNPA Ratio",
            "NNPA %",
            "Net NPA %",
            "Net NPA",
            "NNPA",
            "NS3%",
            "NS3",
            "Net Stage 3",
        ],
        "is_ratio": True,
        "valid_range": (0.0, 20.0),
    },
    "ROA": {
        "notes": "Return on assets as %.",
        "primary_labels": [
            "Return on Assets",
            "ROA",
            "Return on Average Assets",
            "RoA",
        ],
        "is_ratio": True,
        "valid_range": (0.0, 5.0),
    },
    "ROE": {
        "notes": "Return on equity as %.",
        "primary_labels": [
            "Return on Equity",
            "ROE",
            "Return on Net Worth",
            "Return on Average Equity",
            "RoE",
        ],
        "is_ratio": True,
        "valid_range": (0.0, 40.0),
    },
}

SANITY_RANGES: dict[str, tuple[float, float]] = {
    "Capital Adequacy Ratio": (5.0, 50.0),
    "Tier I Capital Ratio": (4.0, 40.0),
    "GNPA": (0.0, 30.0),
    "NNPA": (0.0, 20.0),
    "ROA": (0.0, 5.0),
    "ROE": (0.0, 40.0),
    "PAT": (1.0, 500_000.0),
    "Total Assets": (100.0, 10_000_000.0),
    "Advances": (100.0, 10_000_000.0),
    "Borrowings": (10.0, 10_000_000.0),
    "Total Income": (1.0, 500_000.0),
    "NII": (1.0, 200_000.0),
    "Interest Earned": (1.0, 500_000.0),
    "Interest Expended": (1.0, 500_000.0),
    "Investments": (1.0, 10_000_000.0),
    "Deposits": (0.0, 10_000_000.0),
}

# Row labels used only to derive approved metrics (not shown in review table).
DERIVATION_COMPONENTS: dict[str, list[str]] = {
    "Revenue from Operations": [
        "Revenue from Operations",
        "Revenue From Operations",
        "Revenue from operations",
        "Total Revenue from Operations",
    ],
    "Profit Before Tax": [
        "Profit Before Tax",
        "Profit before tax",
        "PBT",
        "Profit Before Taxes",
        "Profit/(Loss) before tax",
        "Profit before exceptional items and tax",
        "Profit before tax and exceptional items",
    ],
    "Tax": [
        "Tax",
        "Tax Expense",
        "Taxation",
        "Income Tax",
        "Income tax expense",
        "Provision for Tax",
        "Provision for taxation",
        "Provision for income tax",
        "Tax on profit",
        "Total Tax",
        "Total tax expense",
        "Tax charge",
        "Tax charge for the period",
    ],
    "Current Tax": [
        "Current Tax",
        "Current tax",
        "Current tax expense",
        "Current income tax",
    ],
    "Deferred Tax": [
        "Deferred Tax",
        "Deferred tax",
        "Deferred tax expense",
        "Deferred tax charge",
    ],
}

DERIVATION_COMPONENT_NAMES: tuple[str, ...] = tuple(DERIVATION_COMPONENTS.keys())

COMPANY_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hfc": (
        "housing finance",
        "hfc",
        "national housing bank",
        "nhb",
        "individual home loan",
        "home loan",
    ),
    "bank": (
        "scheduled commercial bank",
        "banking company",
        "reserve bank of india",
        "rbi license",
        "casa",
        "savings deposits",
        "current deposits",
    ),
    "nbfc": (
        "non-banking financial",
        "nbfc",
        "systemically important",
        "rbi registered",
    ),
}


def detect_company_type(page_texts: list[str]) -> str:
    """Detect HFC / bank / NBFC from first pages of the PDF."""
    combined = " ".join(page_texts[:10]).lower()
    for company_type, keywords in COMPANY_TYPE_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return company_type
    return "nbfc"


def get_primary_labels(metric: str) -> list[str]:
    """Ordered row labels to search for a metric."""
    return list(METRIC_LOGIC.get(metric, {}).get("primary_labels", [metric]))


def get_valid_range(metric: str) -> tuple[float, float] | None:
    """Return (min, max) sanity range, or None."""
    logic = METRIC_LOGIC.get(metric, {})
    if "valid_range" in logic:
        return tuple(logic["valid_range"])
    return SANITY_RANGES.get(metric)


def is_ratio_metric_logic(metric: str) -> bool:
    return bool(METRIC_LOGIC.get(metric, {}).get("is_ratio"))


def is_value_in_range(metric: str, value: float) -> bool:
    """True if value passes sanity check for this metric."""
    bounds = get_valid_range(metric)
    if bounds is None:
        return True
    lo, hi = bounds
    if is_ratio_metric_logic(metric):
        return lo <= abs(value) <= hi
    return lo <= value <= hi


def get_computation_rule(metric: str) -> dict[str, Any] | None:
    """Return computed_from rule if metric may be calculated."""
    return METRIC_LOGIC.get(metric, {}).get("computed_from")


def explain_metric(metric: str) -> str:
    """Human-readable explanation of what this metric is."""
    return str(METRIC_LOGIC.get(metric, {}).get("notes", metric))


def aliases_for_metric(
    metric: str,
    extra_aliases: dict[str, list[str]] | None = None,
) -> list[str]:
    """Merge metric_logic primary labels with metric_aliases extras (deduped)."""
    if metric in DERIVATION_COMPONENTS:
        return list(DERIVATION_COMPONENTS[metric])
    extras = (extra_aliases or {}).get(metric, [])
    seen: list[str] = []
    for label in get_primary_labels(metric) + list(extras):
        if label and label not in seen:
            seen.append(label)
    return seen or [metric]


def _hit_value(best: dict[tuple[str, str], Any], metric: str, period: str) -> float | None:
    hit = best.get((metric, period))
    if hit is None:
        return None
    val = getattr(hit, "value_crore", None)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _lookup_component_value(
    best: dict[tuple[str, str], Any],
    components_best: dict[tuple[str, str], Any] | None,
    component: str,
    period: str,
) -> float | None:
    """Read a derivation component from components_best, then best."""
    if components_best:
        val = _hit_value(components_best, component, period)
        if val is not None:
            return val
    return _hit_value(best, component, period)


def _resolve_tax_for_period(
    best: dict[tuple[str, str], Any],
    components_best: dict[tuple[str, str], Any] | None,
    period: str,
) -> float | None:
    """Tax line, or Current Tax + Deferred Tax when split on the P&L."""
    tax = _lookup_component_value(best, components_best, "Tax", period)
    if tax is not None:
        return tax
    current = _lookup_component_value(best, components_best, "Current Tax", period)
    deferred = _lookup_component_value(best, components_best, "Deferred Tax", period)
    if current is not None and deferred is not None:
        return current + deferred
    return current if current is not None else deferred


def _make_derived_hit(
    *,
    table: str,
    metric: str,
    period: str,
    value: float,
    source_section: str,
    source_document: str,
    source_file: str,
    confidence: float = 0.90,
) -> Any:
    from services.normalizer import is_ratio_metric
    from services.reconstruction.schema import ExtractionHit

    return ExtractionHit(
        table=table,
        metric=metric,
        period=period,
        value_original=value,
        unit="percent" if is_ratio_metric(metric) else "crore",
        value_crore=value,
        page_number=0,
        source_document=source_document,
        source_file=source_file,
        source_section=source_section,
        confidence=confidence,
        row_label=metric,
        from_table=False,
        used_text_fallback=True,
    )


YEARLY_FLOW_METRICS: frozenset[str] = frozenset({
    "Total Income", "NII", "PAT", "Interest Earned", "Interest Expended",
})
YEARLY_BALANCE_METRICS: frozenset[str] = frozenset({
    "Total Assets", "Advances", "Borrowings", "Investments", "Deposits",
})


def metric_requires_pnl_table(metric: str, table_kind: str) -> bool:
    """Flow / P&L metrics must come from a P&L-shaped table (not highlights)."""
    if table_kind == "half_year":
        from data.metric_aliases import is_flow_metric

        return is_flow_metric(metric)
    return metric in YEARLY_FLOW_METRICS


def metric_requires_bs_table(metric: str, table_kind: str) -> bool:
    """Balance-sheet line items must come from a BS-shaped table."""
    return table_kind == "yearly" and metric in YEARLY_BALANCE_METRICS
_WEAK_YEARLY_SECTIONS = frozenset({
    "fallback", "text_regex", "fallback_text",
})


def score_yearly_extraction_hit(
    hit: Any,
    *,
    period: str = "",
    preferred_doc_fy: int | None = None,
) -> float:
    """Higher score = prefer this hit when multiple candidates exist."""
    score = float(getattr(hit, "confidence", 0.0) or 0.0)
    if getattr(hit, "standalone_section", False):
        score += 0.35
    if getattr(hit, "from_table", False):
        score += 0.30
    score += float(getattr(hit, "row_score", 0.0) or 0.0) * 0.20
    section = getattr(hit, "source_section", "") or ""
    if section in _WEAK_YEARLY_SECTIONS:
        score -= 0.35
    if section == "groq_llm":
        score -= 0.25
    if preferred_doc_fy and period:
        import re

        name = (getattr(hit, "source_file", "") or "").lower()
        m = re.search(r"\b(?:fy\s*)?['`]?(2[0-9])\b", name)
        file_fy = 2000 + int(m.group(1)) if m else None
        m2 = re.search(r"(20\d{2})$", period)
        period_fy = int(m2.group(1)) if m2 else None
        if file_fy and period_fy:
            if file_fy == period_fy + 1 or file_fy == period_fy:
                score += 0.12
            elif file_fy < period_fy:
                score -= 0.08
    return score


def is_obvious_schedule_noise(metric: str, hit: Any) -> bool:
    """
    Only reject clear schedule/note integers from fallback pages (e.g. 16, 26).
    Does NOT block legitimate table extractions.
    """
    section = getattr(hit, "source_section", "") or ""
    if section not in _WEAK_YEARLY_SECTIONS:
        return False
    val = getattr(hit, "value_crore", None)
    if val is None:
        return False
    try:
        fval = float(val)
    except (TypeError, ValueError):
        return False
    row_score = float(getattr(hit, "row_score", 0.0) or 0.0)
    if row_score >= 0.90:
        return False
    if fval != int(fval):
        return False
    if metric in YEARLY_FLOW_METRICS and abs(fval) < 100:
        return True
    if metric in YEARLY_BALANCE_METRICS and abs(fval) < 50:
        return True
    return False


def accept_yearly_extraction_hit(metric: str, hit: Any) -> bool:
    """Backward-compatible gate — only blocks obvious schedule noise."""
    if is_obvious_schedule_noise(metric, hit):
        return False
    return getattr(hit, "value_crore", None) is not None


def resolve_yearly_value_collisions(best: dict[tuple[str, str], Any]) -> int:
    """
    Drop weaker metric when the same page/value/period was assigned to 2+ metrics.
  (e.g. Advances and Interest Expended both 871.7 on page 263).
    """
    by_cell: dict[tuple[str, int, str, float], list[tuple[str, str, Any]]] = {}
    for (metric, period), hit in best.items():
        val = getattr(hit, "value_crore", None)
        if val is None:
            continue
        cell_key = (
            getattr(hit, "source_file", "") or "",
            int(getattr(hit, "page_number", 0) or 0),
            period,
            round(float(val), 2),
        )
        by_cell.setdefault(cell_key, []).append((metric, period, hit))

    removed = 0
    for items in by_cell.values():
        if len(items) <= 1:
            continue
        if len({m for m, _p, _h in items}) <= 1:
            continue
        ranked = sorted(
            items,
            key=lambda x: score_yearly_extraction_hit(x[2], period=x[1]),
            reverse=True,
        )
        winner_metric, winner_period, winner_hit = ranked[0]
        for metric, period, hit in items:
            if metric == winner_metric:
                continue
            if float(getattr(hit, "row_score", 0) or 0) >= 0.90:
                continue
            key = (metric, period)
            if key in best:
                del best[key]
                removed += 1
    return removed


def filter_untrusted_source_hits(best: dict[tuple[str, str], Any]) -> int:
    """Drop text_regex currency hits and low-confidence sweep/fallback rows."""
    from data.metric_aliases import CURRENCY_METRICS, RATIO_METRICS

    removed = 0
    for key, hit in list(best.items()):
        metric, _period = key
        section = getattr(hit, "source_section", "") or ""
        val = getattr(hit, "value_crore", None)
        if val is not None and not is_value_in_range(metric, float(val)):
            del best[key]
            removed += 1
            continue
        if section == "text_regex" and (
            metric in CURRENCY_METRICS or getattr(hit, "table", "") == "yearly"
        ):
            del best[key]
            removed += 1
            continue
        if section in ("standalone_sweep", "fallback", "fallback_text"):
            if metric in CURRENCY_METRICS and float(
                getattr(hit, "row_score", 0) or 0
            ) < 0.75:
                del best[key]
                removed += 1
                continue
            label = (getattr(hit, "row_label", "") or "").lower()
            if metric in CURRENCY_METRICS and (
                "note " in label or label.startswith("note")
            ):
                del best[key]
                removed += 1
    return removed


def filter_duplicate_cross_period_hits(best: dict[tuple[str, str], Any]) -> int:
    """Same file+page+value for 2+ periods on a flow metric → untrusted."""
    from data.metric_aliases import is_flow_metric

    by_cell: dict[tuple[str, int, float], list[tuple[str, str]]] = {}
    for (metric, period), hit in best.items():
        if not is_flow_metric(metric):
            continue
        val = getattr(hit, "value_crore", None)
        if val is None:
            continue
        cell = (
            getattr(hit, "source_file", "") or "",
            int(getattr(hit, "page_number", 0) or 0),
            round(float(val), 2),
        )
        by_cell.setdefault(cell, []).append((metric, period))

    removed = 0
    for items in by_cell.values():
        periods = {p for _m, p in items}
        if len(periods) < 2:
            continue
        for metric, period in items:
            key = (metric, period)
            if key in best:
                del best[key]
                removed += 1
    return removed


def filter_obvious_schedule_noise(best: dict[tuple[str, str], Any]) -> int:
    """Remove only clear fallback schedule integers — keeps real extractions."""
    removed = 0
    for key, hit in list(best.items()):
        metric, _period = key
        if is_obvious_schedule_noise(metric, hit):
            del best[key]
            removed += 1
    return removed


def filter_invalid_hits(best: dict[tuple[str, str], Any]) -> int:
    """Drop extracted hits outside sanity range. Returns count removed."""
    removed = 0
    for key, hit in list(best.items()):
        metric, period = key
        val = getattr(hit, "value_crore", None)
        if val is None:
            continue
        if not is_value_in_range(metric, float(val)):
            logger.info(
                "[sanity] Dropped %s %s value=%s (valid range %s)",
                metric,
                period,
                val,
                get_valid_range(metric),
            )
            del best[key]
            removed += 1
    return removed


def apply_yearly_derived_values(
    best: dict[tuple[str, str], Any],
    periods: tuple[str, ...],
    *,
    components_best: dict[tuple[str, str], Any] | None = None,
    company_type: str = "nbfc",
    source_file: str = "",
    source_document: str = "",
    table: str = "yearly",
) -> None:
    """
    Fill missing metrics using METRIC_LOGIC computation rules.

    Rules applied when the target metric is not already extracted:
    - Total Income ← Revenue from Operations
    - NII ← Interest Earned − Interest Expended
    - PAT ← Profit Before Tax − Tax
    """
    for period in periods:
        ti_key = ("Total Income", period)
        if ti_key not in best:
            rev = _lookup_component_value(
                best, components_best, "Revenue from Operations", period
            )
            if rev is not None and is_value_in_range("Total Income", rev):
                best[ti_key] = _make_derived_hit(
                    table=table,
                    metric="Total Income",
                    period=period,
                    value=rev,
                    source_section="derived:revenue_from_operations",
                    source_document=source_document,
                    source_file=source_file,
                )

        nii_key = ("NII", period)
        if nii_key not in best:
            earned = _hit_value(best, "Interest Earned", period)
            expended = _hit_value(best, "Interest Expended", period)
            if earned is not None and expended is not None:
                nii_val = earned - expended
                if is_value_in_range("NII", nii_val):
                    best[nii_key] = _make_derived_hit(
                        table=table,
                        metric="NII",
                        period=period,
                        value=nii_val,
                        source_section="derived:interest_earned_minus_expended",
                        source_document=source_document,
                        source_file=source_file,
                    )

        pat_key = ("PAT", period)
        if pat_key not in best:
            pbt = _lookup_component_value(
                best, components_best, "Profit Before Tax", period
            )
            tax = _resolve_tax_for_period(best, components_best, period)
            if pbt is not None and tax is not None:
                pat_val = pbt - tax
                if is_value_in_range("PAT", pat_val):
                    best[pat_key] = _make_derived_hit(
                        table=table,
                        metric="PAT",
                        period=period,
                        value=pat_val,
                        source_section="derived:profit_before_tax_minus_tax",
                        source_document=source_document,
                        source_file=source_file,
                    )

        if company_type == "hfc":
            dep_logic = METRIC_LOGIC.get("Deposits", {})
            if dep_logic.get("hfc_default") is None:
                best.pop(("Deposits", period), None)


def merge_component_hits(
    components_best: dict[tuple[str, str], Any],
    hits: dict[str, Any],
    component: str,
) -> None:
    """Keep highest-confidence hit per (component, period) in components_best."""
    for period, hit in hits.items():
        if hit is None:
            continue
        key = (component, period)
        cur = components_best.get(key)
        if cur is None or hit.confidence > getattr(cur, "confidence", 0):
            components_best[key] = hit
