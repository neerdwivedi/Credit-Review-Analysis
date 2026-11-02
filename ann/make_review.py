"""Write a short hand-check sheet for the test set.

Rows: every test page auto-labelled pnl / balance_sheet, plus the test pages
labelled `other` that are most likely wrong. Fill in `corrected_label` only
where the auto-label is wrong (pnl, balance_sheet or other) and leave it
blank otherwise; make_split.py applies the corrections to test.csv. Run from
the project root after make_split.py:

    python ann/make_review.py

Writes ann/_data/test_review.csv (gitignored: it holds page text).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_dataset import (  # noqa: E402
    FULL_BALANCE_SHEET,
    FULL_PNL,
    LABELS,
    PNL_LINE_ITEMS,
    PROJECT_ROOT,
    TITLE_TOP_CHARS,
    _has_line_items,
    _title_classes,
)

sys.path.insert(0, str(PROJECT_ROOT))
from services.normalizer import normalize_text  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "_data"
REVIEW_FILE = DATA_DIR / "test_review.csv"
TEXT_CHARS = 300

# The two totals alone: what balance-sheet notes carry and the strict
# FULL_BALANCE_SHEET rule rejects, so such pages are worth a look.
BS_TOTALS = (
    (("total assets",), ("total liabilities", "equity and liabilities")),
    (("capital and liabilities",), ("advances",), ("investments",)),
)
# Share of a full statement's line items that makes an `other` page suspect.
NEAR_FULL = 4 / 6

REASONS = {
    "auto": "auto-labelled pnl / balance_sheet",
    "title": "statement title at top of page + statement totals",
    "items": "most of a full statement's line items",
    "next": "next to a labelled statement page (missed continuation?)",
}


def _full_share(norm: str, formats) -> float:
    return max(
        sum(any(item in norm for item in alts) for alts in fmt) / len(fmt)
        for fmt in formats
    )


def review_reasons(df: pd.DataFrame) -> pd.Series:
    labelled = set(zip(df.loc[df["auto_label"] != "other", "source_file"], df.loc[df["auto_label"] != "other", "page_number"]))

    def reasons(row) -> str:
        if row.auto_label != "other":
            return "auto"
        norm = normalize_text(row.page_text)
        out = []
        if _title_classes(norm[:TITLE_TOP_CHARS]) and (
            _has_line_items(norm, PNL_LINE_ITEMS) or _has_line_items(norm, BS_TOTALS)
        ):
            out.append("title")
        if max(_full_share(norm, FULL_BALANCE_SHEET), _full_share(norm, FULL_PNL)) >= NEAR_FULL:
            out.append("items")
        if (row.source_file, row.page_number - 1) in labelled or (row.source_file, row.page_number + 1) in labelled:
            out.append("next")
        return "+".join(out)

    return df.apply(reasons, axis=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--test", type=Path, default=DATA_DIR / "test.csv")
    parser.add_argument("--out", type=Path, default=REVIEW_FILE)
    parser.add_argument("--force", action="store_true", help="overwrite a sheet that already has corrections")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        existing = pd.read_csv(args.out, keep_default_na=False, encoding="utf-8-sig")
        if (existing.get("corrected_label", pd.Series(dtype=str)).str.strip() != "").any():
            print(f"{args.out} already has corrections; pass --force to overwrite it.")
            return 1

    df = pd.read_csv(args.test, keep_default_na=False)
    df["why"] = review_reasons(df)
    rows = df[df["why"] != ""].copy()
    rows["text_start"] = rows["page_text"].map(lambda t: normalize_text(t)[:TEXT_CHARS])
    rows["corrected_label"] = ""
    rows = rows.sort_values(["company", "source_file", "page_number"])
    sheet = rows[["company", "source_file", "page_number", "auto_label", "corrected_label", "why", "text_start"]]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows the rupee sign and other symbols correctly
    sheet.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"Wrote {len(sheet)} rows to {args.out}")
    print(sheet["why"].value_counts().to_string())
    print("\nwhy:")
    for code, text in REASONS.items():
        print(f"  {code:<6} {text}")
    print(f"\ncorrected_label: leave blank if the auto-label is right, else one of {', '.join(LABELS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
