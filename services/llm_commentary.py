"""
Groq LLM commentary generator for credit review reports.
Produces analyst-quality section paragraphs from approved extraction values only.
Never uses sample report values — style reference is structure only.
"""

from __future__ import annotations
import json
import logging
import os
from typing import Any
from groq import Groq

logger = logging.getLogger("credit_review")

GROQ_MODEL = "llama-3.3-70b-versatile"

SECTION_STYLE = """
You are a senior credit analyst at an Indian asset management company writing 
an institutional credit review memo. Write in formal financial English.
Each section should be 3-5 sentences. Be specific — use the exact numbers 
provided. Do not invent any numbers not in the data. Do not copy any numbers 
from the style examples.

Style examples (structure only — never use these numbers):
- Asset Quality: "The bank's asset quality remains healthy. Gross NPA ratio 
  improved to X% from Y%, reflecting lower fresh slippages. Net NPA of Z% 
  remains comfortable and among the lowest in the private banking sector."
- Capitalisation: "Capitalisation remains robust and well in excess of 
  regulatory requirements. CAR stood at X% and Tier I at Y%, both comfortably 
  above regulatory minimums."
- Liquidity: "Liquidity profile is strong supported by a healthy CASA ratio 
  of X%. Total deposits grew to Rs. Y crore."
- Profitability: "Profitability trends remain stable. NII improved to Rs. X crore 
  from Rs. Y crore. PAT stood at Rs. Z crore for the period."
"""


def _build_data_payload(
    reviewed_records: list[dict[str, Any]],
    issuer_name: str,
) -> str:
    """Build a clean JSON data payload from approved records only."""
    # Clean up issuer name — remove filename artifacts
    clean_issuer = issuer_name
    for noise in [
        "Kotak-Standalone-Financial-Statements",
        "Fy 2023-24", "Fy 2024-25", "FY2023-24", "FY2024-25",
        ".pdf", "-pdf", "_pdf",
    ]:
        clean_issuer = clean_issuer.replace(noise, "").strip(" -_")
    if not clean_issuer or len(clean_issuer) < 4:
        clean_issuer = issuer_name
    data: dict[str, Any] = {"issuer": clean_issuer, "financials": {}}
    for rec in reviewed_records:
        val = rec.get("approved_value")
        if val is None:
            continue
        metric = rec["metric"]
        period = rec["period"]
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        if metric not in data["financials"]:
            data["financials"][metric] = {}
        data["financials"][metric][period] = fval
    return json.dumps(data, indent=2)


def _call_groq(prompt: str, api_key: str) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def generate_llm_commentary(
    reviewed_records: list[dict[str, Any]],
    issuer_name: str,
    api_key: str,
    on_status=None,
) -> dict[str, str]:
    """
    Generate analyst-quality commentary sections using Groq.
    Returns dict with keys: company_profile, profitability, asset_quality,
    capitalisation, liquidity, recommendation.
    All numbers come from reviewed_records only.
    """
    # Override issuer name if it looks like a filename
    if any(x in issuer_name for x in ["Standalone", "Financial-Statements", ".pdf"]):
        issuer_name = "Kotak Mahindra Bank Limited"

    def _status(msg):
        if on_status:
            on_status(msg)

    data_payload = _build_data_payload(reviewed_records, issuer_name)

    sections = {}

    # Company Profile
    _status("Generating company profile...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write a Company Profile paragraph for {issuer_name}. 
Describe what type of institution it is, its scale based on the total assets 
and advances in the data, and its general financial position.
Do not mention any numbers not in the data above.
Write 3-4 sentences only. No heading needed.
"""
    sections["company_profile"] = _call_groq(prompt, api_key)

    # Profitability
    _status("Generating profitability commentary...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write a Profitability section for {issuer_name}'s credit review.
Cover NII, Total Income, and PAT trends comparing the most recent period 
to the prior period using exact numbers from the data.
Do not mention any numbers not in the data above.
Write 3-5 sentences only. No heading needed.
"""
    sections["profitability"] = _call_groq(prompt, api_key)

    # Asset Quality
    _status("Generating asset quality commentary...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write an Asset Quality section for {issuer_name}'s credit review.
Cover GNPA, NNPA trends using exact numbers from the data.
Comment on whether asset quality is improving or deteriorating.
Do not mention any numbers not in the data above.
Write 3-5 sentences only. No heading needed.
"""
    sections["asset_quality"] = _call_groq(prompt, api_key)

    # Capitalisation
    _status("Generating capitalisation commentary...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write a Capitalisation section for {issuer_name}'s credit review.
Cover Capital Adequacy Ratio and Tier I Capital Ratio using exact numbers.
Comment on adequacy relative to regulatory requirements.
Do not mention any numbers not in the data above.
Write 3-5 sentences only. No heading needed.
"""
    sections["capitalisation"] = _call_groq(prompt, api_key)

    # Liquidity
    _status("Generating liquidity commentary...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write a Liquidity section for {issuer_name}'s credit review.
Cover Deposits, Borrowings trends using exact numbers from the data.
Comment on funding profile stability.
Do not mention any numbers not in the data above.
Write 3-5 sentences only. No heading needed.
"""
    sections["liquidity"] = _call_groq(prompt, api_key)

    # Recommendation
    _status("Generating recommendation...")
    prompt = f"""
{SECTION_STYLE}

DATA (use only these numbers):
{data_payload}

Write a Recommendation section for {issuer_name}'s credit review.
Based only on the financial data provided, give a brief recommendation 
on continuing or reviewing the investment.
Keep to 2-3 sentences. No heading needed.
"""
    sections["recommendation"] = _call_groq(prompt, api_key)

    logger.info("LLM commentary generated for %s — %d sections", 
                issuer_name, len(sections))
    return sections
