"""
Groq LLM financial metric extraction from PDF page text.
Runs before pdfplumber; seeds ExtractionHit values at moderate confidence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import Groq

from data.metric_aliases import APPROVED_METRICS
from services.normalizer import (
    canonicalize_table1_period,
    canonicalize_table2_period,
    is_ratio_metric,
)
from services.reconstruction.schema import ExtractionHit, TableKind

logger = logging.getLogger("credit_review")

GROQ_MODEL = "llama-3.1-8b-instant"
LLM_CONFIDENCE = 0.58

# Values copied from old prompt examples — reject if LLM echoes them.
_PROMPT_ECHO_VALUES: frozenset[float] = frozenset({
    1234.5, 5678.0, 12345.0, 2345.0, 6789.0, 67890.0, 4125.0,
})

_EXTRACTION_PROMPT = """You are a financial data extraction specialist for Indian banks and NBFCs.
You will receive text extracted from an annual report or investor presentation PDF.

Extract ONLY these metrics (use exact metric names as keys):
{metrics}

Target periods for this document (use EXACT period labels as shown below):
{periods}

Rules:
1. Return ONLY valid JSON — no markdown, no explanation
2. Use numbers exactly as they appear in the text; convert ALL currency amounts to Rs crore
3. Ratios (CAR, Tier I, GNPA, NNPA, ROA, ROE) stay as percentages
4. If a metric/period is not found in the text, omit it entirely — never guess
5. For yearly docs use periods like "31.03.2025"
6. For half-year docs use periods like "H1FY26", "H1FY25"
7. NEVER invent numbers. Every value must be traceable to the document text below.

Return this JSON structure (use an empty "metrics" object when nothing is found):
{{
  "unit_detected": "crore|lakh|thousand|unknown",
  "metrics": {{}}
}}

Document text:
{text}
"""


def is_groq_available(api_key: str | None) -> bool:
    """True if api_key looks like a Groq key (not a Gemini vision key)."""
    if not api_key:
        return False
    key = api_key.strip()
    if key.startswith("gsk_"):
        return True
    if key.startswith("AIza"):
        return False
    return len(key) >= 20


def _canonicalize_period(header: str, table_kind: TableKind) -> str | None:
    if table_kind == "yearly":
        return canonicalize_table1_period(header)
    return canonicalize_table2_period(header)


def _select_pages(pages: list[dict[str, Any]], table_kind: TableKind) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in pages:
        text = (item.get("text") or "").lower()
        score = 0
        if "profit and loss" in text or "statement of profit" in text:
            score += 10
        if "balance sheet" in text or "financial position" in text:
            score += 8
        if table_kind == "half_year":
            if "h1fy" in text or "half year" in text or "half-year" in text:
                score += 6
        if "total income" in text or "net interest income" in text:
            score += 4
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:2]] or pages[:2]


def _build_text_bundle(pages: list[dict[str, Any]], max_chars: int = 100_000) -> str:
    parts: list[str] = []
    total = 0
    for item in pages:
        page_num = item.get("page", "?")
        text = (item.get("text") or "").strip()
        if not text:
            continue
        chunk = f"\n--- Page {page_num} ---\n{text}\n"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "".join(parts)


def _call_groq(prompt: str, api_key: str) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.0,
    )
    return response.choices[0].message.content.strip()


def _value_appears_in_text(value: float, text: str) -> bool:
    """Reject LLM hallucinations — number must appear on the source page."""
    compact = text.replace(",", "").replace(" ", "")
    if not compact:
        return False
    candidates: list[str] = []
    if value == int(value):
        candidates.append(str(int(value)))
    candidates.append(f"{value:.2f}".rstrip("0").rstrip("."))
    candidates.append(f"{value:.1f}".rstrip("0").rstrip("."))
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if cand in compact:
            return True
    return False


def _reject_llm_value(metric: str, value: float, page_text: str) -> bool:
    """True if this LLM value should be discarded."""
    from data.metric_logic import is_value_in_range

    if value in _PROMPT_ECHO_VALUES:
        return True
    if not is_value_in_range(metric, value):
        return True
    if not _value_appears_in_text(value, page_text):
        return True
    return False


def _parse_json_response(raw: str) -> dict[str, Any] | None:
    text = re.sub(r"```json\s*", "", raw)
    text = re.sub(r"```\s*", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[llm_extractor] Failed to parse LLM JSON response")
        return None


def llm_extract_document(
    pages: list[dict[str, Any]],
    *,
    groq_api_key: str,
    table_kind: TableKind,
    source_document: str,
    source_file: str,
    allowed_periods: tuple[str, ...],
    metrics_filter: tuple[str, ...] | None = None,
    only_missing_keys: set[tuple[str, str]] | None = None,
) -> list[ExtractionHit]:
    """Extract metrics from page text via Groq; returns validated ExtractionHit list."""
    if not pages or not groq_api_key:
        return []

    target_pages = _select_pages(pages, table_kind)
    if not target_pages:
        return []

    metrics_list = metrics_filter or APPROVED_METRICS
    allowed_set = set(allowed_periods)
    best_by_key: dict[tuple[str, str], ExtractionHit] = {}

    for page_item in target_pages:
        page_text = (page_item.get("text") or "").strip()
        if not page_text:
            continue
        page_text_trimmed = page_text[:2500]
        page_num = int(page_item.get("page", 0) or 0)
        cluster_text = f"\n--- Page {page_num} ---\n{page_text_trimmed}\n"

        prompt = _EXTRACTION_PROMPT.format(
            metrics=", ".join(metrics_list),
            periods=", ".join(allowed_periods),
            text=cluster_text,
        )

        try:
            raw = _call_groq(prompt, groq_api_key)
        except Exception as exc:
            logger.warning("[llm_extractor] Groq call failed for %s: %s", source_file, exc)
            continue

        parsed = _parse_json_response(raw)
        if not parsed:
            continue

        metrics_data = parsed.get("metrics", {})
        if not isinstance(metrics_data, dict):
            continue

        for metric, period_values in metrics_data.items():
            if metric not in APPROVED_METRICS:
                continue
            if metrics_filter and metric not in metrics_filter:
                continue
            if not isinstance(period_values, dict):
                continue
            for period_raw, value in period_values.items():
                if value is None:
                    continue
                period = _canonicalize_period(str(period_raw), table_kind) or str(period_raw)
                if period not in allowed_set:
                    continue
                key = (metric, period)
                if only_missing_keys is not None and key not in only_missing_keys:
                    continue
                try:
                    val = float(value)
                except (TypeError, ValueError):
                    continue
                if _reject_llm_value(metric, val, page_text):
                    logger.info(
                        "[llm_extractor] rejected %s | %s %s = %s (not in page text)",
                        source_file, metric, period, val,
                    )
                    continue
                unit = "percent" if is_ratio_metric(metric) else "crore"
                hit = ExtractionHit(
                    table=table_kind,
                    metric=metric,
                    period=period,
                    value_original=val,
                    unit=unit,
                    value_crore=val,
                    page_number=page_num,
                    source_document=source_document,
                    source_file=source_file,
                    source_section="groq_llm",
                    confidence=LLM_CONFIDENCE,
                    row_label=metric,
                    from_table=False,
                    used_text_fallback=True,
                )
                if key not in best_by_key:
                    best_by_key[key] = hit
                logger.info(
                    "[llm_extractor] %s | %s %s = %s (page %d)",
                    source_file, metric, period, val, page_num,
                )

    return list(best_by_key.values())
