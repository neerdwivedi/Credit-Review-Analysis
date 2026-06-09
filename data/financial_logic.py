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
}

def aggregate_h1(metric, q1, q2):
    mtype = METRIC_TYPE.get(metric)
    if mtype == MetricType.FLOW:
        if q1 is not None and q2 is not None:
            return q1 + q2
        return q2 if q2 is not None else q1
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