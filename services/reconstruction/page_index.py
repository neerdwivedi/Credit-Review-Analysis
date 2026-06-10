"""Section detection and page scoring for candidate retrieval."""

from __future__ import annotations

from data.metric_aliases import METRIC_ALIASES
from data.metric_logic import aliases_for_metric
from services.reconstruction.document import DocumentContext
from services.reconstruction.similarity import compact

try:
    from services.normalizer import (
        canonicalize_table1_period,
        canonicalize_table2_period,
        normalize_text,
    )
except ImportError as exc:
    raise RuntimeError("Period canonicalization unavailable") from exc

# Narrow section markers — no generic "financial" / "bank" / "assets"
SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "standalone_pnl": (
        "standalone statement of profit and loss",
        "standalone statement of profit & loss",
        "standalone profit and loss",
        "standalone profit & loss account",
    ),
    "standalone_bs": ("standalone balance sheet",),
    "standalone_results": (
        "standalone financial results",
        "standalone financial statements",
    ),
    "five_year_highlights": (
        "five year financial",
        "five-year financial",
        "ten year financial",
    ),
    "financial_highlights": (
        "financial highlights",
        "key financial highlights",
    ),
    "capital_adequacy": (
        "capital adequacy",
        "capital to risk weighted",
    ),
    "pillar3": ("pillar 3", "pillar iii"),
    "basel": ("basel iii", "basel-iii"),
    "ratios": ("key ratios", "key financial ratios", "key performance indicators"),
    "key_metrics": ("key metrics", "bank highlights"),
    "h1_pnl": (
        "profit and loss statement",
        "profit & loss statement",
        "statement of profit and loss",
    ),
    "h1_highlights": ("bank highlights", "key highlights", "key metrics"),
    "h1_section": (
        "half year ended",
        "half-year ended",
        "h1fy26",
        "h1 fy26",
    ),
    "h1_balance_sheet": (
        "balance sheet",
        "assets and liabilities",
        "key balance sheet",
        "balance sheet highlights",
    ),
    "pnl_statement_table": (),  # filled by content detection in detect_sections
}

YEARLY_METRIC_SECTIONS: dict[str, tuple[str, ...]] = {
    "Total Income": ("pnl_statement_table", "standalone_pnl", "standalone_results", "five_year_highlights"),
    "NII": ("pnl_statement_table", "standalone_pnl", "standalone_results", "five_year_highlights"),
    "PAT": ("pnl_statement_table", "standalone_pnl", "standalone_results", "five_year_highlights"),
    "Total Assets": ("standalone_bs", "standalone_results", "five_year_highlights", "financial_highlights"),
    "Borrowings": ("standalone_bs", "standalone_results", "five_year_highlights"),
    "Investments": ("standalone_bs", "standalone_results", "five_year_highlights"),
    "Advances": ("standalone_bs", "standalone_results", "five_year_highlights", "financial_highlights"),
    "Deposits": ("standalone_bs", "standalone_results", "five_year_highlights", "financial_highlights"),
    "Capital Adequacy Ratio": ("capital_adequacy", "pillar3", "basel", "ratios"),
    "Tier I Capital Ratio": ("capital_adequacy", "basel", "pillar3", "ratios"),
    "ROA": ("ratios", "five_year_highlights", "financial_highlights", "key_metrics"),
    "ROE": ("ratios", "five_year_highlights", "financial_highlights", "key_metrics"),
}

HALF_YEAR_METRIC_SECTIONS: dict[str, tuple[str, ...]] = {
    "Total Income": ("pnl_statement_table", "h1_pnl", "h1_section", "h1_highlights"),
    "NII": ("pnl_statement_table", "h1_pnl", "h1_section", "h1_highlights"),
    "PAT": ("pnl_statement_table", "h1_pnl", "h1_section", "h1_highlights"),
    "ROE": ("h1_highlights", "h1_section", "h1_pnl", "ratios"),
    "ROA": ("h1_highlights", "h1_section", "ratios", "financial_highlights"),
    "Total Assets": ("h1_highlights", "h1_section", "h1_pnl", "financial_highlights"),
    "Borrowings": ("h1_highlights", "h1_section", "financial_highlights"),
    "Investments": ("h1_highlights", "h1_section", "financial_highlights"),
    "Advances": ("h1_highlights", "h1_section", "financial_highlights"),
    "Deposits": ("h1_highlights", "h1_section", "financial_highlights"),
    "Capital Adequacy Ratio": ("capital_adequacy", "h1_highlights", "h1_section", "basel"),
    "Tier I Capital Ratio": ("capital_adequacy", "h1_highlights", "h1_section", "basel"),
    "GNPA": ("asset_quality", "h1_highlights", "h1_section"),
    "NNPA": ("asset_quality", "h1_highlights", "h1_section"),
}

SCORE_SECTION = 10
SCORE_METRIC_ALIAS = 5
SCORE_TABLE_PAGE = 5
SCORE_PREFERRED_SECTION = 10
SCORE_PERIOD = 5
SCORE_NEIGHBOR = 2

MAX_CANDIDATES_YEARLY = 80
MAX_CANDIDATES_HALF_YEAR = 28
NEIGHBOR_RADIUS = 2
PER_SECTION_CAP = 20


def _detect_pnl_statement_table_pages(
    ctx: DocumentContext,
    table_kind: str,
) -> list[int]:
    """
    Content-based P&L table pages (no fixed page numbers).

    Yearly: Particulars + 2+ year-end date columns (31.03.20xx).
    Half-year: P&L wording + H1 / half-year period markers.
    """
    import re

    anchor: set[int] = set()
    for page_num, norm in ctx.norm_text_by_page.items():
        raw = ctx.text_by_page.get(page_num, "")
        if table_kind == "yearly":
            dates = set(re.findall(r"31\.03\.20\d{2}", raw))
            has_particulars = "particulars" in norm
            has_pnl = any(
                k in norm
                for k in (
                    "statement of profit",
                    "profit and loss",
                    "profit & loss",
                    "standalone financial results",
                )
            )
            if has_particulars and len(dates) >= 2:
                anchor.add(page_num)
            elif has_pnl and len(dates) >= 2:
                anchor.add(page_num)
        else:
            compact = re.sub(r"\s+", "", norm)
            has_h1 = bool(re.search(r"h1fy\d{2}", compact)) or "half year" in norm
            has_pnl = any(
                k in norm
                for k in (
                    "profit and loss",
                    "profit & loss",
                    "statement of profit",
                    "particulars",
                    "financial performance",
                )
            )
            if has_pnl and has_h1:
                anchor.add(page_num)

    if not anchor:
        return []

    max_page = max(ctx.text_by_page.keys()) if ctx.text_by_page else 0
    expanded: set[int] = set()
    for page_num in anchor:
        expanded.add(page_num)
        for delta in (-1, 1):
            neighbor = page_num + delta
            if 1 <= neighbor <= max_page:
                expanded.add(neighbor)
    return sorted(expanded)


def detect_sections(ctx: DocumentContext, table_kind: str) -> None:
    max_page = max(ctx.text_by_page.keys()) if ctx.text_by_page else 0
    ctx.section_pages = {}

    for section, keywords in SECTION_KEYWORDS.items():
        if table_kind == "yearly" and section.startswith("h1_"):
            continue
        if table_kind == "half_year" and section.startswith("standalone_"):
            continue

        hits: list[int] = []
        for page_num, norm in ctx.norm_text_by_page.items():
            if any(kw in norm for kw in keywords):
                hits.append(page_num)
        hits = sorted(hits)[:PER_SECTION_CAP]

        expanded: set[int] = set()
        for p in hits:
            for d in range(-NEIGHBOR_RADIUS, NEIGHBOR_RADIUS + 1):
                np = p + d
                if 1 <= np <= max_page:
                    expanded.add(np)

        # Keep standalone section neighbours even when table preview missed a page.
        critical = section.startswith("standalone_")
        if ctx.table_count_by_page and not critical:
            expanded = {p for p in expanded if ctx.table_count_by_page.get(p, 0) > 0}

        ctx.section_pages[section] = sorted(expanded)

    ctx.section_pages["pnl_statement_table"] = _detect_pnl_statement_table_pages(
        ctx, table_kind
    )


def _metric_aliases_compact(metric: str) -> list[str]:
    return [compact(a) for a in aliases_for_metric(metric, METRIC_ALIASES)]


def score_page_for_metric(
    ctx: DocumentContext,
    page_num: int,
    metric: str,
    table_kind: str,
    priority_sections: tuple[str, ...],
) -> int:
    norm = ctx.norm_text_by_page.get(page_num, "")
    if not norm:
        return 0

    score = 0
    if ctx.table_count_by_page.get(page_num, 0) > 0:
        score += SCORE_TABLE_PAGE

    for section in priority_sections:
        if page_num in ctx.section_pages.get(section, []):
            score += SCORE_PREFERRED_SECTION
            break

    for alias_c in _metric_aliases_compact(metric):
        if alias_c and alias_c in compact(norm):
            score += SCORE_METRIC_ALIAS
            break

    canon_fn = (
        canonicalize_table1_period
        if table_kind == "yearly"
        else canonicalize_table2_period
    )
    if canon_fn(norm) or canon_fn(ctx.text_by_page.get(page_num, "")):
        score += SCORE_PERIOD
    elif table_kind == "yearly":
        for token in ("fy25", "fy24", "fy23", "31.03.2025", "31.03.2024", "march 2025"):
            if canon_fn(token) or token.replace(".", "") in norm.replace(".", "").replace(" ", ""):
                score += SCORE_PERIOD
                break
    elif any(t in norm for t in ("h1fy26", "h1 fy26", "h1fy25", "half year")):
        score += SCORE_PERIOD

    if page_num in ctx.standalone_page_set and table_kind == "yearly":
        score += SCORE_SECTION

    return score


def select_candidate_pages(
    ctx: DocumentContext,
    metric: str,
    table_kind: str,
    priority_sections: tuple[str, ...],
    max_pages: int,
) -> list[int]:
    scored: list[tuple[int, int]] = []
    for page_num in ctx.text_by_page:
        s = score_page_for_metric(ctx, page_num, metric, table_kind, priority_sections)
        if s > 0:
            scored.append((s, page_num))

    scored.sort(reverse=True)
    selected: list[int] = []
    selected_set: set[int] = set()

    for _, page_num in scored:
        if page_num in selected_set:
            continue
        selected.append(page_num)
        selected_set.add(page_num)
        for d in (-NEIGHBOR_RADIUS, NEIGHBOR_RADIUS):
            np = page_num + d
            if np in ctx.text_by_page and np not in selected_set:
                selected.append(np)
                selected_set.add(np)
        if len(selected) >= max_pages:
            break

    for section in priority_sections:
        for p in ctx.section_pages.get(section, []):
            if p not in selected_set:
                selected.append(p)
                selected_set.add(p)

    if table_kind == "yearly":
        for section in (
            "pnl_statement_table",
            "standalone_pnl",
            "standalone_bs",
            "standalone_results",
        ):
            for p in ctx.section_pages.get(section, []):
                if p not in selected_set:
                    selected.insert(0, p)
                    selected_set.add(p)
    elif table_kind == "half_year":
        for p in ctx.section_pages.get("pnl_statement_table", []):
            if p not in selected_set:
                selected.insert(0, p)
                selected_set.add(p)

    return selected[:max_pages]


def priority_sections_for(metric: str, table_kind: str) -> tuple[str, ...]:
    if table_kind == "yearly":
        return YEARLY_METRIC_SECTIONS.get(metric, ("standalone_results",))
    return HALF_YEAR_METRIC_SECTIONS.get(metric, ("h1_pnl", "h1_highlights"))
