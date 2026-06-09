"""
Screenshot-based extraction fallback.

When pdfplumber misses a page (scanned PDFs, image-based tables,
or specific years not in uploaded PDFs), the analyst can upload
a screenshot of that page. This module sends it to Groq Vision
and returns extraction results in the same format as llm_extractor.py.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from groq import Groq

logger = logging.getLogger("credit_review")

GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

_SCREENSHOT_PROMPT = """You are a financial data extraction specialist for Indian companies.

Look at this screenshot of a financial statement page and extract ALL visible metrics.

METRICS TO FIND (accept any name the company uses):
- PAT: Profit After Tax, Net Profit, Profit for the year
- NII: Net Interest Income. If not shown, calculate Interest Income minus Finance Costs.
- Total Income: Total Income, Total Revenue
- Interest Earned / Interest Income
- Interest Expended / Finance Costs
- Total Assets
- Advances / Loans
- Deposits
- Borrowings
- Investments
- Capital Adequacy Ratio (CAR/CRAR)
- Tier I Capital Ratio
- GNPA % (Gross NPA Ratio)
- NNPA % (Net NPA Ratio)
- ROA (Return on Assets)
- ROE (Return on Equity)

RULES:
1. Return ONLY valid JSON — no explanation, no markdown
2. Detect the unit (crore/lakh/thousand) and convert ALL currency to crore
3. Ratios return as percentage number (e.g. 1.48 not 0.0148)
4. Use period format "31.03.2024" for annual, "H1FY26" for half-year
5. Return ALL years/periods visible on the page
6. If a metric is not visible return null

JSON FORMAT:
{
  "unit_detected": "crore",
  "page_description": "Standalone P&L for year ended March 2024",
  "PAT": {"31.03.2024": 4765.41, "31.03.2023": 2891.03},
  "NII": {"31.03.2024": 8650.89, "31.03.2023": null},
  "Total Income": {"31.03.2024": 27234.64, "31.03.2023": null},
  "Interest Earned": {"31.03.2024": 27041.55, "31.03.2023": null},
  "Interest Expended": {"31.03.2024": 18390.66, "31.03.2023": null},
  "Total Assets": {"31.03.2024": 291204.63, "31.03.2023": null},
  "Advances": {"31.03.2024": 280589.79, "31.03.2023": null},
  "Deposits": {"31.03.2024": null, "31.03.2023": null},
  "Borrowings": {"31.03.2024": null, "31.03.2023": null},
  "Investments": {"31.03.2024": 6277.03, "31.03.2023": null},
  "Capital Adequacy Ratio": {"31.03.2024": 22.1, "31.03.2023": null},
  "Tier I Capital Ratio": {"31.03.2024": 20.1, "31.03.2023": null},
  "GNPA": {"31.03.2024": 1.39, "31.03.2023": null},
  "NNPA": {"31.03.2024": 0.34, "31.03.2023": null},
  "ROA": {"31.03.2024": 2.40, "31.03.2023": null},
  "ROE": {"31.03.2024": 14.52, "31.03.2023": null}
}"""


def image_to_base64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")


def extract_from_screenshot(
    image_bytes: bytes,
    groq_api_key: str,
    image_format: str = "jpeg",
) -> dict[str, Any] | None:
    """
    Send a screenshot to Groq Vision and return extracted financial data.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, or WEBP)
        groq_api_key: Groq API key
        image_format: "jpeg", "png", or "webp"

    Returns:
        Dict of {metric: {period: value}} or None on failure
    """
    client = Groq(api_key=groq_api_key)
    image_b64 = image_to_base64(image_bytes)
    mime = f"image/{image_format}"

    try:
        response = client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{image_b64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": _SCREENSHOT_PROMPT,
                        },
                    ],
                }
            ],
            max_tokens=1500,
            temperature=0.0,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        return json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.warning("[screenshot_extractor] JSON parse failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[screenshot_extractor] Groq vision call failed: %s", exc)
        return None


def screenshot_to_review_records(
    result: dict[str, Any],
    *,
    source_file: str,
    allowed_periods: tuple[str, ...],
) -> list[dict[str, Any]]:
    """
    Convert Groq Vision output to review records that can be merged
    into the existing reviewed_records in session_state.

    Returns list of dicts with same structure as build_review_record() output.
    """
    from services.normalizer import is_ratio_metric, format_crore_display

    records = []
    allowed_set = set(allowed_periods)

    for metric, period_vals in result.items():
        if metric in ("unit_detected", "page_description"):
            continue
        if not isinstance(period_vals, dict):
            continue

        for period, value in period_vals.items():
            if value is None:
                continue
            if period not in allowed_set:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue

            records.append({
                "metric": metric,
                "period": period,
                "extracted_value": num,
                "approved_value": num,
                "original_unit": "percent" if is_ratio_metric(metric) else "crore",
                "value_crore": num,
                "initial_approved": num,
                "page_number": "screenshot",
                "source_document": "screenshot",
                "source_filename": source_file,
                "confidence": 0.88,
                "status": "Extracted",
                "notes": f"Extracted from uploaded screenshot: {source_file}",
                "manual_edit": False,
            })

    return records
