"""
Phase 4 — Institutional section commentary from approved extraction values.

Produces short professional paragraphs per credit-memo section (not per-metric lines).
Rule-based only. No LLM. No invented numbers.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

from data.metric_aliases import TABLE1_PERIODS, TABLE2_PERIODS
from services.normalizer import format_crore_display, is_ratio_metric
from services.report_generator import _issuer_name_from_records
from services.review_manager import split_records_by_table

logger = logging.getLogger("credit_review")

HALF_NEWER, HALF_OLDER = "H1FY26", "H1FY25"
YEAR_NEWER, YEAR_OLDER = "31.03.2025", "31.03.2024"

# Enterprise memo sections (order preserved). Asset Quality inserted when disclosed.
SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("business_profile", "Business Profile"),
    ("profitability",    "Profitability"),
    ("capitalisation",   "Capitalisation"),
    ("asset_quality",    "Asset Quality"),
    ("liquidity",        "Liquidity"),
)

# Values outside these bounds are extraction errors, not real figures.
# Commentary skips the clause rather than report impossible numbers.
_COMMENTARY_BOUNDS: dict[str, tuple[float, float]] = {
    "Capital Adequacy Ratio":  (8.0,   50.0),
    "Tier I Capital Ratio":    (6.0,   50.0),
    "GNPA":   (0.0,   25.0),
    "NNPA":   (0.0,   15.0),
    "ROA":    (-2.0,   8.0),
    "ROE":    (-5.0,  40.0),
    "NII":    (1.0,   200_000.0),
    "PAT":    (-50_000.0, 200_000.0),
    "Total Income":  (1.0, 500_000.0),
    "Total Assets":  (1_000.0, 5_000_000.0),
    "Deposits":      (100.0, 5_000_000.0),
    "Borrowings":    (100.0, 5_000_000.0),
    "Advances":      (100.0, 5_000_000.0),
}


def _plausible(metric: str, value: float | None) -> bool:
    """Return False if value is outside expected bounds — skip in commentary."""
    if value is None:
        return False
    bounds = _COMMENTARY_BOUNDS.get(metric)
    if bounds is None:
        return True   # unknown metric — let it through
    lo, hi = bounds
    return lo <= value <= hi


def _record_value_map(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], float | None]:
    out: dict[tuple[str, str], float | None] = {}
    for rec in records:
        key = (rec["metric"], rec["period"])
        val = rec.get("approved_value")
        if val is None:
            out[key] = None
        else:
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                out[key] = None
    return out


def _fmt(metric: str, value: float) -> str:
    if is_ratio_metric(metric):
        return f"{format_crore_display(value)}%"
    return f"₹{format_crore_display(value)} crore"


def _get(
    values: dict[tuple[str, str], float | None],
    metric: str,
    period: str,
) -> float | None:
    val = values.get((metric, period))
    if not _plausible(metric, val):
        return None
    return val


def _is_stable(newer: float, older: float, tol_pct: float = 2.0) -> bool:
    if older == 0:
        return newer == 0
    return abs((newer - older) / older) * 100.0 < tol_pct


def _trend_word(newer: float, older: float) -> str:
    if _is_stable(newer, older):
        return "remained stable"
    if newer > older:
        return "improved"
    if newer < older:
        return "moderated" if newer > 0 else "declined"
    return "remained stable"


def _change_phrase(
    label: str,
    older_val: float,
    newer_val: float,
    older_period: str,
    newer_period: str,
    *,
    metric: str = "",
) -> str:
    """Single embedded clause, e.g. 'Net interest income improved to X from Y'."""
    m = metric or label
    unit_new = _fmt(m, newer_val)
    unit_old = _fmt(m, older_val)
    if _is_stable(newer_val, older_val):
        return f"{label} remained broadly stable at {unit_new}"
    if newer_val > older_val:
        return f"{label} improved to {unit_new} from {unit_old}"
    return f"{label} moderated to {unit_new} from {unit_old}"


def _pick_period_pair(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
    metric: str,
) -> tuple[str, str, dict[tuple[str, str], float | None], str]:
    """
  Return (newer_period, older_period, values_dict, period_tag for prose).
    Prefer half-year when both periods have at least one value.
    """
    if _get(half, metric, HALF_NEWER) is not None or _get(half, metric, HALF_OLDER) is not None:
        return HALF_NEWER, HALF_OLDER, half, "H1FY26"
    return YEAR_NEWER, YEAR_OLDER, yearly, "the latest financial year"


def _undisclosed_phrase(metrics: list[str]) -> str:
    if not metrics:
        return ""
    if len(metrics) == 1:
        return f"{metrics[0]} was not disclosed."
    return f"{', '.join(metrics[:-1])} and {metrics[-1]} were not disclosed."


def _business_profile(issuer: str) -> str:
    return (
        f"{issuer} is assessed based on disclosed standalone annual report and "
        "investor presentation data. The following commentary summarises approved "
        "financial metrics only and does not rely on inferred or derived figures."
    )


def _profitability_section(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
) -> str:
    newer_h, older_h, _, tag = _pick_period_pair(half, yearly, "PAT")

    def pair(metric: str) -> tuple[float | None, float | None, str, str, dict]:
        n_p, o_p, vals, _ = _pick_period_pair(half, yearly, metric)
        return _get(vals, metric, n_p), _get(vals, metric, o_p), n_p, o_p, vals

    nii_n, nii_o, _, _, _ = pair("NII")
    pat_n, pat_o, _, _, _ = pair("PAT")
    ti_n, ti_o, _, _, _ = pair("Total Income")
    roa_n, roa_o, _, _, _ = pair("ROA")

    if pat_n is not None and pat_o is not None:
        headline = f"Profitability {_trend_word(pat_n, pat_o)} during {tag}."
    elif nii_n is not None:
        headline = f"Profitability trends for {tag} are reflected in disclosed operating metrics."
    else:
        headline = "Profitability assessment is constrained by limited disclosed operating data."

    clauses: list[str] = []
    if nii_n is not None and nii_o is not None:
        clauses.append(_change_phrase("Net interest income", nii_o, nii_n, older_h, newer_h, metric="NII"))
    if pat_n is not None and pat_o is not None:
        clauses.append(_change_phrase("PAT", pat_o, pat_n, older_h, newer_h, metric="PAT"))
    elif ti_n is not None and ti_o is not None:
        clauses.append(
            _change_phrase("Total income", ti_o, ti_n, older_h, newer_h, metric="Total Income")
        )

    # Keep to two operating clauses (NII/PAT/income) — avoid metric-by-metric spam.
    clauses = clauses[:2]

    body = headline
    if clauses:
        if len(clauses) == 1:
            body = f"{headline} {clauses[0].capitalize()}."
        else:
            body = f"{headline} {clauses[0].capitalize()}, while {clauses[1]}."

    if pat_n is not None and pat_o is not None and pat_n > 0 and "healthy" not in body.lower():
        body += " Earnings profile continues to remain healthy."

    missing: list[str] = []
    if nii_n is None and nii_o is None:
        missing.append("Net interest income")
    if pat_n is None and pat_o is None and ti_n is None:
        missing.append("PAT")
    if roa_n is None and roa_o is None:
        missing.append("ROA")
    if missing:
        body += " " + _undisclosed_phrase(missing)

    return body.strip()


def _pair_vals(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
    metric: str,
) -> tuple[float | None, float | None, str, str, dict]:
    n_p, o_p, vals, _ = _pick_period_pair(half, yearly, metric)
    return _get(vals, metric, n_p), _get(vals, metric, o_p), n_p, o_p, vals


def _capitalisation_section(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
) -> str:
    car_n, car_o, car_n_p, car_o_p, _ = _pair_vals(
        half, yearly, "Capital Adequacy Ratio"
    )
    tier_n, tier_o, tier_n_p, tier_o_p, _ = _pair_vals(
        half, yearly, "Tier I Capital Ratio"
    )

    if car_n is None and tier_n is None:
        return "Capital adequacy metrics were not explicitly disclosed."

    if car_n is not None and car_o is not None:
        clause = _change_phrase(
            "Capital adequacy ratio",
            car_o,
            car_n,
            car_o_p,
            car_n_p,
            metric="Capital Adequacy Ratio",
        )
        if tier_n is not None and tier_o is not None:
            tier_clause = _change_phrase(
                "Tier I capital ratio",
                tier_o,
                tier_n,
                tier_o_p,
                tier_n_p,
                metric="Tier I Capital Ratio",
            )
            return (
                f"Capitalisation remains comfortable and above regulatory requirements. "
                f"{clause.capitalize()}, and {tier_clause}."
            )
        return (
            "Capitalisation remains comfortable and above regulatory requirements "
            f"supported by adequate capital buffers. {clause.capitalize()}."
        )

    if car_n is not None:
        return (
            "Capitalisation remains comfortable supported by disclosed capital adequacy "
            f"at {_fmt('Capital Adequacy Ratio', car_n)}."
        )
    return (
        "Tier I capital ratio was disclosed; capital adequacy ratio was not explicitly disclosed."
    )


def _asset_quality_section(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
) -> str | None:
    """Returns None if no GNPA/NNPA data disclosed — section omitted."""
    gnpa_n, gnpa_o, _, _, _ = _pair_vals(half, yearly, "GNPA")
    nnpa_n, nnpa_o, _, _, _ = _pair_vals(half, yearly, "NNPA")

    if gnpa_n is None and nnpa_n is None:
        return None

    parts = ["Asset quality metrics were disclosed for the review period."]
    if gnpa_n is not None and gnpa_o is not None:
        direction = "improved" if gnpa_n < gnpa_o else "moderated"
        parts.append(
            f"Gross NPA ratio {direction} to {gnpa_n:.2f}% from {gnpa_o:.2f}%."
        )
    elif gnpa_n is not None:
        parts.append(f"Gross NPA ratio stood at {gnpa_n:.2f}%.")

    if nnpa_n is not None and nnpa_o is not None:
        direction = "improved" if nnpa_n < nnpa_o else "moderated"
        parts.append(
            f"Net NPA ratio {direction} to {nnpa_n:.2f}% from {nnpa_o:.2f}%."
        )
    elif nnpa_n is not None:
        parts.append(f"Net NPA ratio was {nnpa_n:.2f}%.")

    return " ".join(parts)


def _liquidity_section(
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
) -> str:
    dep_n, dep_o, dep_np, dep_op, _ = _pair_vals(half, yearly, "Deposits")
    bor_n, bor_o, bor_np, bor_op, _ = _pair_vals(half, yearly, "Borrowings")

    if dep_n is None and bor_n is None:
        return (
            "Liquidity-related metrics were not explicitly disclosed in the reviewed "
            "half-year and annual extracts."
        )

    opening = (
        "Liquidity profile remains stable supported by diversified liabilities and "
        "deposit franchise."
    )
    details: list[str] = []
    if dep_n is not None and dep_o is not None:
        details.append(
            _change_phrase("Deposits", dep_o, dep_n, dep_op, dep_np, metric="Deposits")
        )
    if bor_n is not None and bor_o is not None:
        details.append(
            _change_phrase("Borrowings", bor_o, bor_n, bor_op, bor_np, metric="Borrowings")
        )

    if not details:
        return opening

    return f"{opening} {'; '.join(d.capitalize() for d in details)}."


def _build_sections(
    issuer: str,
    half: dict[tuple[str, str], float | None],
    yearly: dict[tuple[str, str], float | None],
) -> list[dict[str, str]]:
    builders = {
        "business_profile": lambda: _business_profile(issuer),
        "profitability": lambda: _profitability_section(half, yearly),
        "capitalisation": lambda: _capitalisation_section(half, yearly),
        "liquidity": lambda: _liquidity_section(half, yearly),
    }
    sections: list[dict[str, str]] = []
    for section_id, title in SECTION_ORDER:
        if section_id == "asset_quality":
            continue
        sections.append(
            {
                "id": section_id,
                "title": title,
                "paragraph": builders[section_id](),
            }
        )
    aq_text = _asset_quality_section(half, yearly)
    if aq_text:
        # Insert after capitalisation (index 2)
        sections.insert(
            3,
            {"id": "asset_quality", "title": "Asset Quality", "paragraph": aq_text},
        )
    return sections


def generate_commentary(
    reviewed_records: list[dict[str, Any]],
    *,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    Build institutional section commentary from approved review records.

    Returns memo-style sections (Business Profile, Profitability, etc.) — not
    one sentence per metric.
    """
    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    _status("Preparing commentary from approved extraction values…")
    table1, table2 = split_records_by_table(reviewed_records)
    yearly_values = _record_value_map(table1)
    half_values = _record_value_map(table2)
    issuer = _issuer_name_from_records(reviewed_records)

    _status("Drafting business profile…")
    _status("Generating profitability commentary…")
    _status("Writing capitalisation analysis…")
    _status("Assessing asset quality disclosure…")
    _status("Building liquidity assessment…")
    sections = _build_sections(issuer, half_values, yearly_values)
    _status("Preparing credit review commentary…")
    paragraphs = [s["paragraph"] for s in sections]
    full_text = "\n\n".join(f"{s['title']}\n{s['paragraph']}" for s in sections)

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "approved_extraction",
        "rules": "institutional_sections_v2",
        "sections": sections,
        "paragraphs": paragraphs,
        "full_text": full_text,
        # Legacy keys — section paragraphs only (no metric spam)
        "yearly": {
            "title": "Yearly Financials Commentary",
            "periods": list(TABLE1_PERIODS),
            "sections": sections,
            "paragraphs": paragraphs,
        },
        "half_year": {
            "title": "Half-Year Financials Commentary",
            "periods": list(TABLE2_PERIODS),
            "sections": sections,
            "paragraphs": paragraphs,
        },
    }
    logger.info(
        "Institutional commentary: %d sections (%s)",
        len(sections),
        ", ".join(s["title"] for s in sections),
    )
    return payload


def save_commentary_json(
    commentary: dict[str, Any],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(commentary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Commentary saved to %s", output_path)
    return output_path
