"""Per-document context for V2 extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.normalizer import UnitType, detect_unit, normalize_text
from data.metric_aliases import (
    CONSOLIDATED_KEYWORDS,
    STANDALONE_SECTION_KEYWORDS,
)
from data.metric_logic import detect_company_type


@dataclass
class DocumentContext:
    pdf_bytes: bytes
    filename: str
    doc_type: str
    pages: list[dict[str, Any]]
    table_count_by_page: dict[int, int] = field(default_factory=dict)

    text_by_page: dict[int, str] = field(default_factory=dict)
    norm_text_by_page: dict[int, str] = field(default_factory=dict)
    standalone_pages: list[int] = field(default_factory=list)
    standalone_page_set: set[int] = field(default_factory=set)
    section_pages: dict[str, list[int]] = field(default_factory=dict)
    page_unit: dict[int, UnitType] = field(default_factory=dict)
    page_tables: dict[int, list[list[list[Any]]]] = field(default_factory=dict)
    fiscal_year_hint: int | None = None
    vision_api_key: str = ""
    company_type: str = "nbfc"

    def build_indexes(self) -> None:
        for item in self.pages:
            p = int(item["page"])
            text = item.get("text") or ""
            self.text_by_page[p] = text
            self.norm_text_by_page[p] = normalize_text(text)
            if self.page_unit.get(p) is None:
                self.page_unit[p] = detect_unit(text)

        self.standalone_pages = self._find_standalone_pages()
        self.standalone_page_set = set(self.standalone_pages)
        self.fiscal_year_hint = _infer_fiscal_year(
            self.filename, self.norm_text_by_page
        )
        page_texts = [item.get("text") or "" for item in self.pages[:10]]
        self.company_type = detect_company_type(page_texts)

    def _find_standalone_pages(self) -> list[int]:
        """Pages in standalone financial statement sections (+ neighbours)."""
        import re

        keyword_anchor: set[int] = set()
        pnl_table_anchor: set[int] = set()
        for page_num, norm in self.norm_text_by_page.items():
            if re.search(
                r"standalone\s+(?:statement\s+of\s+profit|balance\s+sheet|financial\s+results?)",
                norm,
            ):
                keyword_anchor.add(page_num)
            elif any(kw in norm for kw in STANDALONE_SECTION_KEYWORDS):
                if "schedule" in norm and not re.search(
                    r"standalone\s+(?:statement|balance|financial)", norm
                ):
                    continue
                keyword_anchor.add(page_num)
            if re.search(r"particulars.{0,80}31\.03\.20\d{2}", norm):
                if any(
                    phrase in norm
                    for phrase in (
                        "standalone statement",
                        "statement of profit",
                        "standalone balance sheet",
                        "standalone financial",
                    )
                ):
                    pnl_table_anchor.add(page_num)

        expanded: set[int] = set()
        max_page = max(self.text_by_page.keys()) if self.text_by_page else 0
        for p in keyword_anchor:
            expanded.add(p)
            for d in (-1, 1):
                np = p + d
                if 1 <= np <= max_page:
                    expanded.add(np)
        for p in pnl_table_anchor:
            expanded.add(p)
            for d in (-1, 1):
                np = p + d
                if 1 <= np <= max_page:
                    expanded.add(np)
        return sorted(expanded)

    def is_consolidated_only(self, page_num: int) -> bool:
        norm = self.norm_text_by_page.get(page_num, "")
        if not norm:
            return False
        has_cons = any(k in norm for k in CONSOLIDATED_KEYWORDS)
        has_standalone = any(k in norm for k in STANDALONE_SECTION_KEYWORDS)
        return has_cons and not has_standalone


def _infer_fiscal_year(
    filename: str,
    norm_by_page: dict[int, str] | None = None,
) -> int | None:
    import re

    name = filename.lower()
    m = re.search(r"\b(?:fy\s*)?['`]?(2[4-6])\b", name)
    if m:
        return 2000 + int(m.group(1))
    m = re.search(r"\b(202[3-6])\b", name)
    if m:
        return int(m.group(1))
    if " 25" in name or "25.pdf" in name:
        return 2025
    if " 24" in name or "24.pdf" in name:
        return 2024

    if not norm_by_page:
        return None

    head = " ".join(
        norm_by_page[p]
        for p in sorted(norm_by_page)
        if p <= 20
    )
    cover_years: list[int] = []
    for pat in (
        r"financial year\s*20(\d{2})",
        r"for the (?:financial )?year ended.*?20(\d{2})",
        r"annual report\s*20(\d{2})",
        r"year ending\s*31.*?20(\d{2})",
        r"fy\s*20(\d{2})",
    ):
        for match in re.finditer(pat, head, re.I):
            cover_years.append(2000 + int(match.group(1)))
    if cover_years:
        return max(cover_years)

    statement_years: list[int] = []
    for page_num in sorted(norm_by_page):
        if page_num > 150:
            break
        norm = norm_by_page[page_num]
        if not re.search(
            r"standalone\s+(?:statement|balance|financial)|statement of profit",
            norm,
        ):
            continue
        raw = " ".join(
            norm_by_page.get(p, "")
            for p in range(page_num, min(page_num + 2, max(norm_by_page) + 1))
        )
        for yy in re.findall(r"31\.03\.20(\d{2})", raw):
            statement_years.append(2000 + int(yy))
    if statement_years:
        return max(statement_years)

    return None
