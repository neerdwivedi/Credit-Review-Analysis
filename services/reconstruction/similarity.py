"""Fuzzy label matching for finance table rows."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from data.metric_aliases import METRIC_ALIASES
from data.metric_logic import aliases_for_metric
from data.metric_exclusions import METRIC_ROW_EXCLUSIONS
from services.normalizer import normalize_text

FUZZY_THRESHOLD = 0.75
_SHORT_ALIAS_LEN = 4


def compact(text: str) -> str:
    """Lowercase, strip spaces — handles 'Net InterestIncome' vs 'Net Interest Income'."""
    return re.sub(r"\s+", "", normalize_text(text))


def _is_excluded(metric: str, label: str) -> str | None:
    norm = normalize_text(label)
    compact_label = compact(label)
    for pattern in METRIC_ROW_EXCLUSIONS.get(metric, ()):
        if pattern in norm or pattern.replace(" ", "") in compact_label:
            return pattern
    return None


def score_row_match(metric: str, row_label: str) -> tuple[float, str | None, bool]:
    """
    Score how well row_label matches metric aliases.

    Returns (score 0–1, matched_alias_or_none, is_exact).
    """
    if not row_label:
        return 0.0, None, False

    exclusion = _is_excluded(metric, row_label)
    if exclusion:
        return 0.0, None, False

    norm_label = normalize_text(row_label)

    # "Interest income" is a substring of "Net Interest Income" — must not match IE.
    if metric == "Interest Earned" and "net interest" in norm_label:
        return 0.0, None, False
    if metric == "NII" and "net interest margin" in norm_label:
        return 0.0, None, False
    compact_label = compact(row_label)

    best_score = 0.0
    best_alias: str | None = None
    is_exact = False

    for alias in aliases_for_metric(metric, METRIC_ALIASES):
        norm_alias = normalize_text(alias)
        compact_alias = compact(alias)

        if norm_alias == norm_label or compact_alias == compact_label:
            return 1.0, alias, True

        if len(compact_alias) <= _SHORT_ALIAS_LEN:
            if re.search(rf"\b{re.escape(norm_alias)}\b", norm_label):
                score = 0.92
            else:
                continue
        elif compact_alias in compact_label or norm_alias in norm_label:
            score = max(0.85, len(compact_alias) / max(len(compact_label), 1))
        else:
            score = SequenceMatcher(None, compact_alias, compact_label).ratio()

        if score > best_score:
            best_score = score
            best_alias = alias
            is_exact = score >= 0.99

    if best_score < FUZZY_THRESHOLD:
        return 0.0, None, False
    return best_score, best_alias, is_exact
