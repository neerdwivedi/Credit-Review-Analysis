from __future__ import annotations
from enum import Enum
from typing import Any

class MetricType(Enum):
    FLOW = "flow"
    SNAPSHOT = "snapshot"
    RATIO = "ratio"
    DERIVED = "derived"

METRIC_TYPE = {
    "Total Income": MetricType.FLOW,
    "NII": MetricType.DERIVED,
    "PAT": MetricType.FLOW,
    "Total Assets": MetricType.SNAPSHOT,
    "Borrowings": MetricType.SNAPSHOT,
    "Investments": MetricType.SNAPSHOT,
    "Advances": MetricType.SNAPSHOT,
    "Deposits": MetricType.SNAPSHOT,
    "Net Worth": MetricType.SNAPSHOT,
    "GNPA": MetricType.RATIO,
    "NNPA": MetricType.RATIO,
    "Capital Adequacy Ratio": MetricType.RATIO,
    "Tier I Capital Ratio": MetricType.RATIO,
    "ROA": MetricType.DERIVED,
    "ROE": MetricType.DERIVED,
    "Interest Earned": MetricType.FLOW,
    "Interest Expended": MetricType.FLOW,
    # Derivation-only P&L components (Q1+Q2 for H1)
    "Revenue from Operations": MetricType.FLOW,
    "Profit Before Tax": MetricType.FLOW,
    "Tax": MetricType.FLOW,
    "Current Tax": MetricType.FLOW,
    "Deferred Tax": MetricType.FLOW,
}

def aggregate_h1(metric, q1, q2):
    """
    Build H1 from quarters.

    Flow metrics (PAT, Total Income, …) require BOTH quarters — a single Q2
    value is not cumulative H1. Snapshot/ratio metrics use Q2 (period-end).
    """
    mtype = METRIC_TYPE.get(metric)
    if mtype == MetricType.FLOW:
        if q1 is not None and q2 is not None:
            return q1 + q2
        return None
    if mtype in (MetricType.SNAPSHOT, MetricType.RATIO):
        return q2
    return None

def derive_h1_values(q1_values, q2_values):
    results = {}
    all_metrics = set(list(q1_values.keys()) + list(q2_values.keys()))
    for metric in all_metrics:
        mtype = METRIC_TYPE.get(metric, MetricType.SNAPSHOT)
        if mtype == MetricType.DERIVED:
            continue
        q1 = q1_values.get(metric)
        q2 = q2_values.get(metric)
        value = aggregate_h1(metric, q1, q2)
        both = q1 is not None and q2 is not None
        results[metric] = {
            "value": value,
            "method": "Q1+Q2" if mtype == MetricType.FLOW else "Q2",
            "confidence": 1.0 if both else (0.7 if value is not None else 0.0),
            "needs_review": not both,
        }
    ie = results.get("Interest Earned", {}).get("value")
    ix = results.get("Interest Expended", {}).get("value")
    nii = (ie - ix) if (ie is not None and ix is not None) else None
    results["NII"] = {
        "value": nii,
        "method": "IE-IX",
        "confidence": 0.9 if nii is not None else 0.0,
        "needs_review": nii is None,
    }
    pat_info = results.get("PAT", {})
    pat = pat_info.get("value")
    if pat is None or pat_info.get("needs_review"):
        pbt = aggregate_h1(
            "Profit Before Tax",
            q1_values.get("Profit Before Tax"),
            q2_values.get("Profit Before Tax"),
        )
        tax_q = aggregate_h1("Tax", q1_values.get("Tax"), q2_values.get("Tax"))
        tax_cur = aggregate_h1(
            "Current Tax", q1_values.get("Current Tax"), q2_values.get("Current Tax")
        )
        tax_def = aggregate_h1(
            "Deferred Tax", q1_values.get("Deferred Tax"), q2_values.get("Deferred Tax")
        )
        tax = tax_q
        if tax is None and tax_cur is not None and tax_def is not None:
            tax = tax_cur + tax_def
        elif tax is None:
            tax = tax_cur if tax_cur is not None else tax_def
        if pbt is not None and tax is not None:
            pat = pbt - tax
            results["PAT"] = {
                "value": pat,
                "method": "PBT-Tax",
                "confidence": 0.9,
                "needs_review": False,
            }

    ti_info = results.get("Total Income", {})
    ti = ti_info.get("value")
    if ti is None or ti_info.get("needs_review"):
        rev = aggregate_h1(
            "Revenue from Operations",
            q1_values.get("Revenue from Operations"),
            q2_values.get("Revenue from Operations"),
        )
        if rev is not None:
            results["Total Income"] = {
                "value": rev,
                "method": "RevenueFromOps",
                "confidence": 0.9,
                "needs_review": False,
            }
    pat = results.get("PAT", {}).get("value")
    a1 = q1_values.get("Total Assets")
    a2 = q2_values.get("Total Assets")
    avg_a = ((a1 if a1 is not None else a2) + a2) / 2 if a2 is not None else None
    roa = ((pat * 2) / avg_a * 100) if (pat is not None and avg_a) else None
    results["ROA"] = {
        "value": roa,
        "method": "PAT*2/AvgAssets",
        "confidence": 0.9 if roa is not None else 0.0,
        "needs_review": roa is None,
    }
    n1 = q1_values.get("Net Worth")
    n2 = q2_values.get("Net Worth")
    avg_n = ((n1 if n1 is not None else n2) + n2) / 2 if n2 is not None else None
    roe = ((pat * 2) / avg_n * 100) if (pat is not None and avg_n) else None
    results["ROE"] = {
        "value": roe,
        "method": "PAT*2/AvgNW",
        "confidence": 0.9 if roe is not None else 0.0,
        "needs_review": roe is None,
    }
    return results