"""
Vision-based financial statement extraction using Gemini.
Converts PDF pages to images and extracts metrics via computer vision.
Used as fallback when pdfplumber extraction fails or has low confidence.
"""

from __future__ import annotations
import base64
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("credit_review")

VISION_SYSTEM_PROMPT = """You are a financial data extraction specialist.
You will be given an image of a financial statement page from an Indian 
bank or NBFC annual report or investor presentation.

Extract the following metrics and their values for ALL periods/years shown:
- NII (Net Interest Income) — for banks: Interest Earned minus Interest Expended
- Total Income (Net Total Income / Total Revenue)
- PAT (Profit After Tax / Net Profit)
- Total Assets
- Deposits
- Borrowings
- Investments
- Advances (Loans)
- Capital Adequacy Ratio (CAR / CRAR)
- Tier I Capital Ratio (CET-1)
- GNPA% (Gross NPA Ratio)
- NNPA% (Net NPA Ratio)
- ROA (Return on Assets)
- ROE (Return on Equity)

Rules:
1. Return ONLY valid JSON, no other text
2. Use the EXACT numbers shown — never calculate or estimate
3. Detect the unit from the page (thousands/lakhs/crores) and 
   convert ALL currency values to crore
4. For ratios (CAR, GNPA%, ROA, ROE) return the percentage value as-is
5. If a metric is not on this page return null for it
6. Include the unit you detected in the response
7. For period labels use exactly what is shown on the page:
   - Annual: "31.03.2025", "31.03.2024", "30.06.2025" etc.
   - Half-year: "H1FY26", "H1FY25", "H1FY24" etc.
   - Quarterly: "Q1FY26", "Q2FY26", "Q1FY25", "Q2FY25" etc.
   - If column says "Sep-25" or "September 2025" use "H1FY26"
   - If column says "Sep-24" or "September 2024" use "H1FY25"
8. For half-year P&L values (NII, PAT, Total Income) — 
   these are cumulative H1 figures, not single quarter
9. For balance sheet values (Total Assets, Deposits etc.) — 
   these are period-end values

Return this exact JSON structure:
{
  "unit_detected": "thousand|lakh|crore|unknown",
  "periods": ["H1FY26", "H1FY25"],
  "metrics": {
    "NII": {"H1FY26": 14570.0, "H1FY25": 13862.0},
    "PAT": {"H1FY26": 6535.0, "H1FY25": 6864.0},
    "Total Assets": {"H1FY26": 706967.0, "H1FY25": 623208.0},
    "Capital Adequacy Ratio": {"H1FY26": 22.1, "H1FY25": 22.6},
    "GNPA%": {"H1FY26": 1.39, "H1FY25": 1.49}
  }
}"""


def _pdf_page_to_base64(pdf_bytes: bytes, page_num: int) -> str | None:
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[page_num - 1]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()
        return base64.standard_b64encode(img_bytes).decode("utf-8")
    except Exception as exc:
        logger.warning("Failed to convert page %d to image: %s", page_num, exc)
        return None


def _call_gemini_vision(image_base64: str, api_key: str, period_hint: str) -> dict | None:
    import urllib.request
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{
            "parts": [
                {"text": VISION_SYSTEM_PROMPT + "\n\nContext: " + period_hint},
                {"inline_data": {
                    "mime_type": "image/png",
                    "data": image_base64,
                }}
            ]
        }],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text)
            return json.loads(text.strip())
    except Exception as exc:
        logger.warning("Gemini vision API call failed: %s", exc)
        return None


def extract_financials_from_page_image(
    pdf_bytes: bytes,
    page_num: int,
    api_key: str,
    provider: str = "gemini",
    period_hint: str = "",
) -> dict | None:
    logger.info("[vision] Extracting from page %d using %s", page_num, provider)
    image_base64 = _pdf_page_to_base64(pdf_bytes, page_num)
    if not image_base64:
        return None
    if provider == "gemini":
        return _call_gemini_vision(image_base64, api_key, period_hint)
    return None


def vision_extract_for_document(
    pdf_bytes: bytes,
    missing_metrics: list[str],
    candidate_pages: list[int],
    api_key: str,
    provider: str = "gemini",
    table_kind: str = "yearly",
) -> dict[tuple[str, str], float]:
    results: dict[tuple[str, str], float] = {}

    # Add period type hint to help Gemini return correct period labels
    period_hint = (
        "Focus on annual year-end periods like 31.03.2025."
        if table_kind == "yearly"
        else "Focus on half-year periods like H1FY26, H1FY25, "
             "or quarterly periods like Q1FY26, Q2FY26."
    )

    for i, page_num in enumerate(candidate_pages[:5]):
        # Rate limit: wait 4 seconds between calls (max 15/min on free tier)
        if i > 0:
            time.sleep(4)

        extracted = extract_financials_from_page_image(
            pdf_bytes, page_num, api_key, provider, period_hint=period_hint
        )
        if not extracted:
            continue
        metrics_data = extracted.get("metrics", {})
        for metric, period_values in metrics_data.items():
            if metric not in missing_metrics:
                continue
            if not isinstance(period_values, dict):
                continue
            for period, value in period_values.items():
                if value is None:
                    continue
                try:
                    key = (metric, period)
                    if key not in results:
                        results[key] = float(value)
                        logger.info(
                            "[vision] Extracted %s %s = %s crore",
                            metric, period, value,
                        )
                except (TypeError, ValueError):
                    pass
        found_metrics = {m for m, _ in results}
        if all(m in found_metrics for m in missing_metrics):
            break

    return results
