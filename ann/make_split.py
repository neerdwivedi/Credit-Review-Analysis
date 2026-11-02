"""Split ann/_data/pages.csv into train / test BY COMPANY.

Every page of a company (all its reports, all years) lands in exactly one
split, so the test metrics measure generalisation to unseen companies and
layouts rather than memorised page templates. Run from the project root
after prepare_dataset.py:

    python ann/make_split.py

Writes ann/_data/train.csv and ann/_data/test.csv (gitignored) with added
`company` and `auto_label` columns; `auto_label` keeps the rule-based label.

Test labels, in order of precedence:
1. ann/test_labels.csv (committed, no page text): the hand-verified labels.
   Every test page listed there gets its label; every unlisted test page
   is `other`. This pins the test set even if the labelling rules change.
2. Otherwise, hand corrections in ann/_data/test_review.csv (from
   make_review.py) are applied to the auto-labels.

After reviewing, save the result as ann/test_labels.csv with:

    python ann/make_split.py --save-test-labels
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "_data"
PINNED_LABELS = Path(__file__).resolve().parent / "test_labels.csv"
LABELS = ("pnl", "balance_sheet", "other")

# Filename -> company group, first match wins (lowercased file name).
# Parent and subsidiary share a group when their reports share a layout or
# overlap in content: Kotak Mahindra Bank + Kotak Mahindra Investments,
# Bajaj Finance + Bajaj Housing Finance. The four files with uninformative
# names were identified from their first pages.
COMPANY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\blic\b", "LIC Housing Finance"),
    (r"kotak|kmil", "Kotak group"),
    (r"l&t", "L&T Finance"),
    (r"^17th annual report\.pdf$", "Bajaj group"),  # Bajaj Housing Finance
    (r"^annual-report-fy2025\.pdf$", "Bajaj group"),  # Bajaj Finance
    (r"^180518285fcf4303b48cffcdeeda5a7d\.pdf$", "Can Fin Homes"),
    (r"^annualreportforfy2024-25\.pdf$", "Muthoot Finance"),
    (r"^annual-report-for-the-year-2024-2025\.pdf$", "Axis Bank"),
    (r"hdfc", "HDFC Bank"),
    (r"icici", "ICICI Bank"),
    (r"indusind", "IndusInd Bank"),
    (r"pnbhfl", "PNB Housing Finance"),
    (r"\bsbi\b", "State Bank of India"),
    (r"shriram", "Shriram Finance"),
)

# Held out entirely: two banks (Banking Regulation Act Form A/B statements)
# and three NBFC/HFCs (Ind AS statements), each a single-year report from a
# company with no related entity in train.
TEST_COMPANIES = frozenset(
    {
        "ICICI Bank",
        "State Bank of India",
        "Shriram Finance",
        "PNB Housing Finance",
        "Muthoot Finance",
    }
)


def company_for(source_file: str) -> str | None:
    name = source_file.rsplit("/", 1)[-1].lower()
    for pattern, company in COMPANY_PATTERNS:
        if re.search(pattern, name):
            return company
    return None


def apply_corrections(test: pd.DataFrame, review_file: Path) -> pd.DataFrame | None:
    """Replace test labels with the hand corrections in review_file.

    Returns None (after printing why) if the sheet has an invalid label or a
    page that is not in the test set.
    """
    review = pd.read_csv(review_file, keep_default_na=False, encoding="utf-8-sig")
    review["corrected_label"] = review["corrected_label"].str.strip().str.lower()
    fixes = review[review["corrected_label"] != ""]

    bad = fixes[~fixes["corrected_label"].isin(LABELS)]
    if not bad.empty:
        print(f"Invalid corrected_label in {review_file} (use {', '.join(LABELS)}):")
        for _, r in bad.iterrows():
            print(f"  {r.source_file} p{r.page_number}: {r.corrected_label!r}")
        return None

    keys = dict(zip(zip(fixes["source_file"], fixes["page_number"].astype(int)), fixes["corrected_label"]))
    test_keys = set(zip(test["source_file"], test["page_number"]))
    missing = sorted(k for k in keys if k not in test_keys)
    if missing:
        print(f"Rows in {review_file} not found in the test set:")
        for f, pg in missing:
            print(f"  {f} p{pg}")
        return None

    test = test.copy()
    test["label"] = [
        keys.get((f, pg), lab)
        for f, pg, lab in zip(test["source_file"], test["page_number"], test["label"])
    ]
    changed = test[test["label"] != test["auto_label"]]
    print(f"Applied {len(keys)} hand corrections from {review_file} ({len(changed)} labels changed):")
    for _, r in changed.iterrows():
        print(f"  {r.company:<22} p{r.page_number:<4} {r.auto_label} -> {r.label}")
    print()
    return test


def apply_pinned(test: pd.DataFrame, pinned_file: Path) -> pd.DataFrame | None:
    """Set test labels from pinned_file; unlisted pages are `other`.

    Returns None (after printing why) on an invalid label or a pinned page
    that is not in the test set.
    """
    pinned = pd.read_csv(pinned_file, keep_default_na=False)
    bad = pinned[~pinned["label"].isin(LABELS)]
    if not bad.empty:
        print(f"Invalid label in {pinned_file}: {sorted(bad['label'].unique())}")
        return None

    keys = dict(zip(zip(pinned["source_file"], pinned["page_number"].astype(int)), pinned["label"]))
    test_keys = set(zip(test["source_file"], test["page_number"]))
    missing = sorted(k for k in keys if k not in test_keys)
    if missing:
        print(f"Pages in {pinned_file} not found in the test set:")
        for f, pg in missing:
            print(f"  {f} p{pg}")
        return None

    test = test.copy()
    test["label"] = [keys.get(k, "other") for k in zip(test["source_file"], test["page_number"])]
    drift = test[test["label"] != test["auto_label"]]
    print(f"Test labels from {pinned_file} ({len(keys)} listed pages, rest `other`).")
    if not drift.empty:
        print(f"{len(drift)} pinned labels differ from the current auto-labels:")
        for _, r in drift.iterrows():
            print(f"  {r.company:<22} p{r.page_number:<4} auto={r.auto_label} pinned={r.label}")
    print()
    return test


def save_pinned(test: pd.DataFrame, review_file: Path, pinned_file: Path) -> None:
    """Write the reviewed test pages and every non-`other` test page."""
    review = pd.read_csv(review_file, keep_default_na=False, encoding="utf-8-sig")
    reviewed = set(zip(review["source_file"], review["page_number"].astype(int)))
    keep = [
        k in reviewed or lab != "other"
        for k, lab in zip(zip(test["source_file"], test["page_number"]), test["label"])
    ]
    out = test.loc[keep, ["source_file", "page_number", "label"]].sort_values(["source_file", "page_number"])
    out.to_csv(pinned_file, index=False)
    print(f"Saved {len(out)} hand-verified test labels to {pinned_file}")
    print(out["label"].value_counts().to_string())
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=Path, default=DATA_DIR / "pages.csv")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--review", type=Path, default=DATA_DIR / "test_review.csv")
    parser.add_argument("--pinned", type=Path, default=PINNED_LABELS)
    parser.add_argument(
        "--save-test-labels",
        action="store_true",
        help="apply the review sheet and save the result as the pinned test labels",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.pages, keep_default_na=False)
    df["company"] = df["source_file"].map(company_for)
    df["auto_label"] = df["label"]

    unmapped = sorted(df.loc[df["company"].isna(), "source_file"].unique())
    if unmapped:
        print("No company mapping for:")
        for f in unmapped:
            print(f"  {f}")
        print("Add them to COMPANY_PATTERNS.")
        return 1

    unknown_test = TEST_COMPANIES - set(df["company"])
    if unknown_test:
        print(f"TEST_COMPANIES not found in data: {sorted(unknown_test)}")
        return 1

    is_test = df["company"].isin(TEST_COMPANIES)
    train, test = df[~is_test], df[is_test]
    assert not set(train["company"]) & set(test["company"])

    if args.save_test_labels:
        if not args.review.exists():
            print(f"No review sheet at {args.review}; run make_review.py and review it first.")
            return 1
        test = apply_corrections(test, args.review)
        if test is None:
            return 1
        save_pinned(test, args.review, args.pinned)
    elif args.pinned.exists():
        test = apply_pinned(test, args.pinned)
    elif args.review.exists():
        test = apply_corrections(test, args.review)
    if test is None:
        return 1
    df = pd.concat([train, test])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    train.to_csv(args.out_dir / "train.csv", index=False)
    test.to_csv(args.out_dir / "test.csv", index=False)

    table = pd.crosstab(df["company"], df["label"]).reindex(columns=["pnl", "balance_sheet", "other"], fill_value=0)
    table.insert(0, "reports", df.groupby("company")["source_file"].nunique())
    table.insert(0, "split", ["test" if c in TEST_COMPANIES else "train" for c in table.index])
    table = table.sort_values(["split", "company"], ascending=[False, True])
    print(table.to_string())
    print()
    for name, part in (("train", train), ("test", test)):
        counts = part["label"].value_counts()
        print(
            f"{name:<5}  companies={part['company'].nunique():>2}  pages={len(part):>6}  "
            f"pnl={counts.get('pnl', 0):>3}  balance_sheet={counts.get('balance_sheet', 0):>3}  "
            f"other={counts.get('other', 0):>6}"
        )
    print(f"\nWrote {args.out_dir / 'train.csv'} and {args.out_dir / 'test.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
