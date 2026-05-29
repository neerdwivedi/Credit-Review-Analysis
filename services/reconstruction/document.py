"""Per-document context for V2 extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.normalizer import UnitType, detect_unit, normalize_text
from data.metric_aliases import (
    CONSOLIDATED_KEYWORDS,
    METRIC_ALIASES,
    STANDALONE_SECTION_KEYWORDS,
)


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
        self.fiscal_year_hint = _infer_fiscal_year(self.filename)

    def _find_standalone_pages(self) -> list[int]:
        hits: list[int] = []
        for page_num, norm in self.norm_text_by_page.items():
            if any(kw in norm for kw in STANDALONE_SECTION_KEYWORDS):
                hits.append(page_num)
        return sorted(hits)

    def is_consolidated_only(self, page_num: int) -> bool:
        norm = self.norm_text_by_page.get(page_num, "")
        if not norm:
            return False
        has_cons = any(k in norm for k in CONSOLIDATED_KEYWORDS)
        has_standalone = any(k in norm for k in STANDALONE_SECTION_KEYWORDS)
        return has_cons and not has_standalone


def _infer_fiscal_year(filename: str) -> int | None:
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
    return None
