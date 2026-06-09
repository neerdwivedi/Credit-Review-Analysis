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
LLM_CONFIDENCE = 0.72

_EXTRACTION_PROMPT = """You are a financial data extraction specialist for Indian banks and NBFCs.
You will receive text extracted from an annual report or investor presentation PDF.

Extract ONLY these metrics (use exact metric names as keys):
{metrics}

Target periods for this document (use EXACT period labels as shown below):
{periods}

Rules:
1. Return ONLY valid JSON — no markdown, no explanation
2. Use numbers exactly as shown; convert ALL currency amounts to Rs crore
3. Ratios (CAR, Tier I, GNPA, NNPA, ROA, ROE) stay as percentages
4. If a metric/period is not found, omit it — do not guess
5. For yearly docs use periods like "31.03.2025"
6. For half-year docs use periods like "H1FY26", "H1FY25"

Return this JSON structure:
{{
  "unit_detected": "crore|lakh|thousand|unknown",
  "metrics": {{
    "PAT": {{"31.03.2025": 1234.5}},
    "NII": {{"H1FY26": 5678.0}}
  }}
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
) -> list[ExtractionHit]:
    """Extract metrics from page text via Groq; returns ExtractionHit list."""
    if not pages or not groq_api_key:
        return []

    target_pages = _select_pages(pages, table_kind)
    if not target_pages:
        return []

    # One page at a time to stay under 6000 TPM
    batches = [[p] for p in target_pages]

    allowed_set = set(allowed_periods)
    hits: list[ExtractionHit] = []

    for batch in batches:
        page_item = batch[0]
        page_text = (page_item.get("text") or "").strip()
        if not page_text:
            continue
        page_text_trimmed = page_text[:2500]
        page_num = page_item.get("page", "?")
        cluster_text = f"\n--- Page {page_num} ---\n{page_text_trimmed}\n"

        prompt = _EXTRACTION_PROMPT.format(
            metrics=", ".join(APPROVED_METRICS),
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
            if not isinstance(period_values, dict):
                continue
            for period_raw, value in period_values.items():
                if value is None:
                    continue
                period = _canonicalize_period(str(period_raw), table_kind) or str(period_raw)
                if period not in allowed_set:
                    continue
                try:
                    val = float(value)
                except (TypeError, ValueError):
                    continue
                unit = "percent" if is_ratio_metric(metric) else "crore"
                page_num = int(batch[0].get("page", 0) or 0)
                hits.append(
                    ExtractionHit(
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
                )
                logger.info(
                    "[llm_extractor] %s | %s %s = %s",
                    source_file, metric, period, val,
                )

    return hits
