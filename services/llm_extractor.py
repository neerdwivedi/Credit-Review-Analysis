"""
LLM extractor — uses Gemini File API to upload full PDFs and extract
all financial metrics visually. Falls back to empty list if Gemini
unavailable so pdfplumber still runs as backup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from typing import TYPE_CHECKING, Any

from data.metric_aliases import APPROVED_METRICS
from services.normalizer import (
    canonicalize_table1_period,
    canonicalize_table2_period,
    is_ratio_metric,
    normalize_text,
)

if TYPE_CHECKING:
    from services.reconstruction.schema import ExtractionHit, TableKind

logger = logging.getLogger("credit_review")

# Prefer 2.5-flash (matches old report app); lite as quota fallback.
_GEMINI_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
)

_GEMINI_MAX_OUTPUT_TOKENS = 16384

_last_gemini_error: str | None = None


def get_last_gemini_error() -> str | None:
    return _last_gemini_error


def clear_last_gemini_error() -> None:
    global _last_gemini_error
    _last_gemini_error = None


def _set_gemini_error(message: str) -> None:
    global _last_gemini_error
    _last_gemini_error = message
    if "RESOURCE_EXHAUSTED" in message or "429" in message:
        logger.warning(
            "[llm_extractor] Gemini quota exceeded — only pdfplumber results will show. "
            "Wait and retry, or check https://ai.dev/rate-limit"
        )

_EXTRACTION_PROMPT = """You are a senior credit analyst extracting financial data from Indian company PDFs.

Extract ALL metrics for ALL visible periods (31.03.2025, 31.03.2024, 31.03.2023, H1FY26, H1FY25):

METRICS:
- Total Income / Revenue from Operations
- NII (Net Interest Income = Interest Income minus Finance Costs)
- PAT (Profit After Tax / Net Profit)
- Total Assets
- Loans / Advances / Loan Book
- Borrowings
- Investments
- Deposits
- Capital Adequacy Ratio / CRAR (%)
- Tier I Capital Ratio (%)
- GNPA / Gross Stage 3 (%)
- NNPA / Net Stage 3 (%)
- ROA (%)
- ROE (%)
- Interest Earned / Interest Income
- Interest Expended / Finance Costs

RULES:
1. STANDALONE figures only — never consolidated
2. All currency in Rs Crore. Convert: Lakhs÷100, Millions÷10
3. Ratios as percentage numbers (1.48 not 0.0148)
4. H1 flow metrics = Q1+Q2 sum. H1 balance sheet = Q2 value only.
5. Return ONLY valid JSON, no markdown
6. Use numbers from the PDF only — never copy example values below

JSON FORMAT (structure only — use real values from the document):
{
  "unit_detected": "crore",
  "Total Income": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "NII": {"31.03.2025": null, "31.03.2024": null, "H1FY26": null, "H1FY25": null},
  "PAT": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "Total Assets": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "Advances": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "Borrowings": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "Investments": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "Deposits": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "Capital Adequacy Ratio": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "Tier I Capital Ratio": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "GNPA": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "NNPA": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "ROA": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null},
  "ROE": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "Interest Earned": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null},
  "Interest Expended": {"31.03.2025": null, "31.03.2024": null, "31.03.2023": null, "H1FY26": null, "H1FY25": null}
}"""

_METRIC_KEY_ALIASES: dict[str, str] = {
    "revenue from operations": "Total Income",
    "total revenue from operations": "Total Income",
    "net interest income": "NII",
    "loans": "Advances",
    "loan book": "Advances",
    "net loans": "Advances",
    "loans and advances": "Advances",
    "profit after tax": "PAT",
    "net profit": "PAT",
    "profit for the year": "PAT",
    "interest income": "Interest Earned",
    "finance costs": "Interest Expended",
    "finance cost": "Interest Expended",
    "car": "Capital Adequacy Ratio",
    "crar": "Capital Adequacy Ratio",
    "gross stage 3": "GNPA",
    "gross npa": "GNPA",
    "net stage 3": "NNPA",
    "net npa": "NNPA",
    "tier 1 capital ratio": "Tier I Capital Ratio",
    "tier i capital ratio": "Tier I Capital Ratio",
}


def _looks_like_gemini_key(api_key: str | None) -> bool:
    """True for Google AI Studio keys (legacy AIza or new auth AQ. format)."""
    if not api_key:
        return False
    key = api_key.strip()
    return key.startswith("AIza") or key.startswith("AQ.")


def is_groq_available(api_key: str | None) -> bool:
    """True when a Gemini (or legacy Groq) API key is present for LLM extraction."""
    if not api_key:
        return False
    key = api_key.strip()
    return (
        key.startswith("gsk_")
        or key.startswith("sk-or-")
        or _looks_like_gemini_key(key)
    )


def is_gemini_available(api_key: str | None) -> bool:
    return _looks_like_gemini_key(api_key)


def _canonical_metric_name(raw: str) -> str | None:
    if raw in APPROVED_METRICS:
        return raw
    norm = normalize_text(raw)
    if norm in _METRIC_KEY_ALIASES:
        return _METRIC_KEY_ALIASES[norm]
    for approved in APPROVED_METRICS:
        if normalize_text(approved) == norm:
            return approved
    return None


def _canonical_period(raw: str) -> str | None:
    text = str(raw).strip()
    return canonicalize_table1_period(text) or canonicalize_table2_period(text) or text


def _file_state_name(gfile: Any) -> str:
    state = getattr(gfile, "state", None) or getattr(gfile, "state_", None)
    if state is None:
        return ""
    return state.name if hasattr(state, "name") else str(state)


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    msg = str(exc)
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg, re.IGNORECASE)
    if m:
        return float(m.group(1)) + 1.0
    return min(60.0, 5.0 * (attempt + 1))


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _parse_gemini_json(raw_text: str) -> dict[str, Any]:
    """Parse Gemini JSON like the old report app — extract block, json5 fallback."""
    clean = re.sub(r"```json\s*|```\s*", "", raw_text).strip()
    match = re.search(r"\{[\s\S]*\}", clean)
    if match:
        clean = match.group(0)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    try:
        import json5  # type: ignore[import-untyped]

        parsed = json5.loads(clean)
        if isinstance(parsed, dict):
            logger.info("[llm_extractor] Parsed JSON via json5 fallback")
            return parsed
    except ImportError:
        pass
    except Exception:
        pass
    # Salvage truncated JSON — close open string/object if response was cut off.
    for suffix in ("", '"}', '"}', '"}', '"}', "}"):
        try:
            parsed = json.loads(clean + suffix)
            if isinstance(parsed, dict) and parsed:
                logger.warning("[llm_extractor] Salvaged truncated JSON response")
                return parsed
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("Could not parse Gemini JSON", clean, 0)


def _generate_with_model_fallback(client: Any, gfile: Any, types: Any) -> str:
    last_exc: Exception | None = None
    for model in _GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[gfile, _EXTRACTION_PROMPT],
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=_GEMINI_MAX_OUTPUT_TOKENS,
                    ),
                )
                text = (response.text or "").strip()
                if text:
                    logger.info("[llm_extractor] Gemini model %s succeeded", model)
                    return text
            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc):
                    delay = _retry_delay_seconds(exc, attempt)
                    logger.warning(
                        "[llm_extractor] %s quota/rate limit (attempt %d) — retry in %.0fs",
                        model,
                        attempt + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("[llm_extractor] %s failed: %s", model, exc)
                break
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini returned empty response for all models")


def _gemini_extract_pdf(
    pdf_bytes: bytes,
    filename: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Upload PDF to Gemini File API; return raw hit dicts."""
    clear_last_gemini_error()
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        _set_gemini_error("google-genai package not installed — run: pip install google-genai")
        logger.warning("[llm_extractor] google-genai not installed")
        return []

    client = genai.Client(api_key=api_key.strip())
    tmp_path = None
    gfile = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        logger.info("[llm_extractor] Uploading %s to Gemini...", filename)
        gfile = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(
                display_name=filename,
                mime_type="application/pdf",
            ),
        )

        for _ in range(20):
            gfile = client.files.get(name=gfile.name)
            if _file_state_name(gfile) == "ACTIVE":
                break
            time.sleep(2)
        else:
            logger.warning("[llm_extractor] Gemini timed out for %s", filename)
            return []

        response_text = _generate_with_model_fallback(client, gfile, types)
        result = _parse_gemini_json(response_text)
        hits = _result_to_hits(result, filename)
        logger.info(
            "[llm_extractor] %s — Gemini extracted %d hits",
            filename,
            len(hits),
        )
        return hits

    except json.JSONDecodeError as exc:
        _set_gemini_error(f"Gemini JSON parse failed for {filename}")
        logger.warning("[llm_extractor] JSON parse failed for %s: %s", filename, exc)
        return []
    except Exception as exc:
        _set_gemini_error(str(exc))
        logger.warning("[llm_extractor] Gemini failed for %s: %s", filename, exc)
        return []
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if gfile:
            try:
                client.files.delete(name=gfile.name)
            except Exception:
                pass


def _result_to_hits(result: dict[str, Any], source_file: str) -> list[dict[str, Any]]:
    from data.metric_logic import is_value_in_range

    hits: list[dict[str, Any]] = []
    skip_keys = {"unit_detected", "periods_found", "metrics"}
    for metric_raw, period_vals in result.items():
        if metric_raw in skip_keys or not isinstance(period_vals, dict):
            continue
        metric = _canonical_metric_name(str(metric_raw))
        if metric is None:
            continue
        for period_raw, value in period_vals.items():
            if value is None:
                continue
            period = _canonical_period(str(period_raw))
            if not period:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if not is_value_in_range(metric, num):
                logger.info(
                    "[llm_extractor] Rejected %s %s=%s (sanity)",
                    metric,
                    period,
                    num,
                )
                continue
            hits.append(
                {
                    "metric": metric,
                    "period": period,
                    "value_original": num,
                    "value_crore": num,
                    "unit": "percent" if is_ratio_metric(metric) else "crore",
                    "page_number": 0,
                    "source_filename": source_file,
                    "source_section": "gemini_file_api",
                    "row_label": metric,
                    "column_header": period,
                    "confidence": 0.92,
                }
            )
    return hits


def _dict_to_hit(
    item: dict[str, Any],
    *,
    table_kind: "TableKind",
    source_document: str,
    source_file: str,
) -> "ExtractionHit":
    from services.reconstruction.schema import ExtractionHit

    unit = item.get("unit", "crore")
    return ExtractionHit(
        table=table_kind,
        metric=item["metric"],
        period=item["period"],
        value_original=item["value_original"],
        unit=unit,
        value_crore=item["value_crore"],
        page_number=int(item.get("page_number") or 0),
        source_document=source_document,
        source_file=source_file or item.get("source_filename", ""),
        source_section=item.get("source_section", "gemini_file_api"),
        confidence=float(item.get("confidence", 0.92)),
        row_label=item.get("row_label", item["metric"]),
        column_header=item.get("column_header", item["period"]),
        from_table=False,
        used_text_fallback=False,
        standalone_section=True,
        preferred_source=True,
    )


def llm_extract_document(
    pages: list[Any],
    pdf_bytes: bytes | None = None,
    filename: str = "",
    api_key: str | None = None,
    fy_hint: int | None = None,
    *,
    groq_api_key: str | None = None,
    table_kind: "TableKind" = "yearly",
    source_document: str = "",
    source_file: str = "",
    allowed_periods: tuple[str, ...] | None = None,
    metrics_filter: tuple[str, ...] | None = None,
    only_missing_keys: set[tuple[str, str]] | None = None,
) -> list["ExtractionHit"]:
    """
    Upload PDF to Gemini File API and extract financial metrics visually.

    Returns empty list if Gemini is unavailable — pdfplumber fills gaps afterward.
    """
    from services.reconstruction.schema import ExtractionHit

    _ = pages, fy_hint  # reserved for future page hints
    key = (api_key or groq_api_key or "").strip()
    if not _looks_like_gemini_key(key):
        logger.debug("[llm_extractor] No Gemini key — skipping for %s", filename or source_file)
        return []

    if not pdf_bytes:
        logger.warning("[llm_extractor] No PDF bytes for %s", filename or source_file)
        return []

    src_name = source_file or filename
    allowed = set(allowed_periods or ())
    raw_hits = _gemini_extract_pdf(pdf_bytes, src_name, key)

    out: list[ExtractionHit] = []
    for item in raw_hits:
        key_tuple = (item["metric"], item["period"])
        if allowed and item["period"] not in allowed:
            continue
        if metrics_filter and item["metric"] not in metrics_filter:
            continue
        if only_missing_keys is not None and key_tuple not in only_missing_keys:
            continue
        out.append(
            _dict_to_hit(
                item,
                table_kind=table_kind,
                source_document=source_document,
                source_file=src_name,
            )
        )
    return out
