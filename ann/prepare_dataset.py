"""Extract per-page text from the training annual reports and auto-label each page.

Output: ann/_data/pages.csv (gitignored) with columns
source_file, page_number, page_text, label.

Labels come from the keyword lists in services/reconstruction/page_index.py,
grouped into the three classifier classes. Investor presentations are skipped
by filename: the model covers annual reports only. Run from the project root:

    python ann/prepare_dataset.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.normalizer import normalize_text  # noqa: E402
from services.pdf_reader import extract_pages_from_pdf  # noqa: E402
from services.reconstruction.page_index import SECTION_KEYWORDS  # noqa: E402

DEFAULT_PDF_DIR = Path(r"C:\Users\neerd\Desktop\pdfs")
DEFAULT_OUT_DIR = PROJECT_ROOT / "ann" / "_data"

LABELS = ("pnl", "balance_sheet", "other")

# Investor presentations, earnings decks and other non-report files.
# Matched against the lowercased filename with "-" and "_" read as spaces.
PRESENTATION_NAME = re.compile(r"ppt|investor|presentation|earnings|q[1-4]\s*fy")

# page_index sections grouped into classifier classes. "standalone_results"
# covers both P&L and balance sheet, and "h1_section" is a period marker,
# so neither maps to a single class.
CLASS_SECTIONS: dict[str, tuple[str, ...]] = {
    "pnl": ("standalone_pnl", "h1_pnl"),
    "balance_sheet": ("standalone_bs", "h1_balance_sheet"),
}

# Only additions beyond page_index: the IFRS-style balance sheet title and
# the bank (Form B) P&L title.
EXTRA_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pnl": ("profit and loss account",),
    "balance_sheet": ("statement of financial position",),
}

# P&L wording used by page_index._detect_pnl_statement_table_pages.
PNL_TABLE_WORDS = (
    "statement of profit",
    "profit and loss",
    "profit & loss",
    "standalone financial results",
)
YEAR_END_DATE = re.compile(r"31\.03\.20\d{2}")

# Line items a real P&L page always carries; notes and narrative pages that
# merely mention "profit and loss" do not. Each entry is one accepted format:
# every inner tuple must have at least one hit.
PNL_LINE_ITEMS: tuple[tuple[tuple[str, ...], ...], ...] = (
    # Ind AS format ("before tax" also covers "profit/(loss) before tax")
    (("before tax",), ("total income", "total expenses")),
    # Banking Regulation Act Form B (banks)
    (("interest earned",), ("interest expended",), ("net profit",)),
)

# Most of a full statement. Used for pages whose title is missing from the
# extracted text (e.g. rendered as an image), for split statements, and for
# every balance sheet: notes such as "maturity analysis of assets and
# liabilities" or "fair value hierarchy" carry total assets / total
# liabilities too, but not the rest of the statement.
# First entry: Ind AS (NBFC / HFC). Second: Banking Regulation Act Form A / B.
FULL_BALANCE_SHEET: tuple[tuple[tuple[str, ...], ...], ...] = (
    (
        ("total assets",),
        ("total liabilities", "equity and liabilities"),
        ("equity share capital",),
        ("other equity",),
        ("cash and cash equivalents",),
        ("borrowings", "debt securities"),
    ),
    (
        ("capital and liabilities",),
        ("deposits",),
        ("borrowings",),
        ("advances",),
        ("investments",),
        ("fixed assets",),
        ("cash and balances with reserve bank",),
    ),
)
FULL_PNL: tuple[tuple[tuple[str, ...], ...], ...] = (
    (
        ("revenue from operations",),
        ("total income",),
        ("total expenses",),
        ("before tax",),
        ("tax expense",),
        ("earnings per",),
    ),
    (
        ("interest earned",),
        ("interest expended",),
        ("operating expenses",),
        ("provisions and contingencies",),
        ("net profit",),
        ("appropriations",),
    ),
)

# "(contd...)", "(continued)" on the second page of a split statement.
CONTINUED = re.compile(r"\bcont(?:d|inued)\b")
# A continuation page without that marker repeats the statement title as its
# page header, i.e. right at the top rather than somewhere in the body.
TITLE_TOP_CHARS = 250

# Section titles sit at the top of a page. Notes and narrative pages mention
# "statement of profit and loss" / "balance sheet" in passing all the time,
# so title keywords only count within this many normalised characters.
HEADING_CHARS = 600


def _class_keywords() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for label, sections in CLASS_SECTIONS.items():
        kws: list[str] = []
        for section in sections:
            kws.extend(SECTION_KEYWORDS.get(section, ()))
        kws.extend(EXTRA_KEYWORDS.get(label, ()))
        # dedupe, keep order ("balance sheet" appears in more than one section)
        out[label] = tuple(dict.fromkeys(kws))
    return out


CLASS_KEYWORDS = _class_keywords()


def _is_pnl_table(raw: str, norm: str) -> bool:
    """P&L table signatures that hold even without a title match."""
    if "revenue from operations" in norm and (
        "finance cost" in norm or "finance costs" in norm
    ):
        return True
    # page_index's two yearly signals combined: P&L wording, "particulars",
    # and at least two distinct year-end date columns.
    has_pnl_words = any(w in norm for w in PNL_TABLE_WORDS)
    return (
        has_pnl_words
        and "particulars" in norm
        and len(set(YEAR_END_DATE.findall(raw))) >= 2
    )


def _has_line_items(norm: str, formats: tuple[tuple[tuple[str, ...], ...], ...]) -> bool:
    return any(
        all(any(item in norm for item in alternatives) for alternatives in fmt)
        for fmt in formats
    )


def label_page(raw_text: str) -> str:
    """Assign one of LABELS to a page's text."""
    norm = normalize_text(raw_text)
    if not norm:
        return "other"

    has_bs_items = _has_line_items(norm, FULL_BALANCE_SHEET)
    has_pnl_items = _has_line_items(norm, PNL_LINE_ITEMS)
    statement_ok = {"balance_sheet": has_bs_items, "pnl": has_pnl_items}

    heading = norm[:HEADING_CHARS]
    # Earliest title keyword in the heading wins, so a page titled
    # "Balance Sheet" that later says "profit and loss" stays a balance sheet.
    # Statement titles only count when the page also carries the line items.
    best: tuple[int, str] | None = None
    for label, keywords in CLASS_KEYWORDS.items():
        if not statement_ok[label]:
            continue
        for kw in keywords:
            pos = heading.find(kw)
            if pos != -1 and (best is None or pos < best[0]):
                best = (pos, label)
    if best is not None:
        return best[1]

    if has_pnl_items and _is_pnl_table(raw_text, norm):
        return "pnl"
    return "other"


FULL_STATEMENT = {"balance_sheet": FULL_BALANCE_SHEET, "pnl": FULL_PNL}


def _title_classes(heading: str) -> set[str]:
    return {
        label
        for label, keywords in CLASS_KEYWORDS.items()
        if any(kw in heading for kw in keywords)
    }


def label_document(texts: list[str]) -> list[str]:
    """Label every page of one PDF, using neighbouring pages where needed.

    On top of label_page:
    - a page with no statement title but a full set of line items is a
      statement (title rendered as an image);
    - a statement split over two pages, each carrying its title, counts when
      the two pages together carry a full statement's line items;
    - a page right after a statement page continues it when it is marked
      "(contd...)" or repeats the statement title in its page header.
    """
    labels = [label_page(t) for t in texts]
    base = list(labels)  # continuations only extend pages labelled on their own
    norms = [normalize_text(t) for t in texts]
    titles = [_title_classes(n[:HEADING_CHARS]) for n in norms]

    for i, norm in enumerate(norms):
        if labels[i] != "other" or not norm:
            continue
        for label in ("balance_sheet", "pnl"):
            if _has_line_items(norm, FULL_STATEMENT[label]):
                labels[i] = label
                break
        if labels[i] != "other":
            continue

        for label in ("balance_sheet", "pnl"):
            if label not in titles[i]:
                continue
            if i > 0 and base[i - 1] == label and (
                CONTINUED.search(norm[:HEADING_CHARS])
                or label in _title_classes(norm[:TITLE_TOP_CHARS])
            ):
                labels[i] = label
                break
            nxt = i + 1
            if (
                nxt < len(norms)
                and label in titles[nxt]
                and _has_line_items(norm + " " + norms[nxt], FULL_STATEMENT[label])
            ):
                labels[i] = label
                if labels[nxt] == "other":
                    labels[nxt] = label
                break
    return labels


def _find_pdfs(pdf_dir: Path) -> list[Path]:
    return sorted(p for p in pdf_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


def _source_name(pdf_dir: Path, path: Path) -> str:
    """Folder name + path inside it, so files from different folders stay distinct."""
    return f"{pdf_dir.name}/{path.relative_to(pdf_dir).as_posix()}"


def is_presentation(filename: str) -> bool:
    name = re.sub(r"[-_]+", " ", filename.lower())
    return bool(PRESENTATION_NAME.search(name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        action="append",
        help=f"folder of PDFs; repeat for several folders (default: {DEFAULT_PDF_DIR})",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-chars", type=int, default=3000, help="truncate page_text to this length")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N PDFs (0 = all)")
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="process byte-identical PDFs again instead of skipping them",
    )
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    # pdf_reader logs every document; failures are reported below instead.
    logging.getLogger("credit_review").setLevel(logging.CRITICAL)

    pdf_dirs: list[Path] = args.pdf_dir or [DEFAULT_PDF_DIR]
    for pdf_dir in pdf_dirs:
        if not pdf_dir.is_dir():
            print(f"PDF folder not found: {pdf_dir}")
            return 1

    # (folder, file) pairs so source names keep their folder
    all_pdfs = [(d, p) for d in pdf_dirs for p in _find_pdfs(d)]
    presentations = [(d, p) for d, p in all_pdfs if is_presentation(p.name)]
    pdfs = [(d, p) for d, p in all_pdfs if not is_presentation(p.name)]
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"No annual reports found in {', '.join(map(str, pdf_dirs))}")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.out_dir / "pages.csv"
    failed_log = args.out_dir / "failed_pdfs.txt"
    types_log = args.out_dir / "file_types.txt"

    with types_log.open("w", encoding="utf-8") as fh:
        for d, p in all_pdfs:
            kind = "presentation" if is_presentation(p.name) else "report"
            fh.write(f"{kind}\t{_source_name(d, p)}\n")

    print(f"Found {len(all_pdfs)} PDFs in {', '.join(map(str, pdf_dirs))}")
    print(f"  annual reports: {len(pdfs)}   presentations skipped: {len(presentations)}  (see {types_log})")
    print(f"Writing {out_csv}")

    label_counts: Counter[str] = Counter()
    seen_hashes: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    pdfs_ok = 0
    empty_pages = 0
    t0 = time.perf_counter()

    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_file", "page_number", "page_text", "label"])

        for i, (pdf_dir, path) in enumerate(pdfs, start=1):
            rel = _source_name(pdf_dir, path)
            try:
                pdf_bytes = path.read_bytes()
                digest = hashlib.sha256(pdf_bytes).hexdigest()
                if digest in seen_hashes and not args.keep_duplicates:
                    duplicates.append((rel, seen_hashes[digest]))
                    continue
                seen_hashes.setdefault(digest, rel)

                pages = extract_pages_from_pdf(pdf_bytes, rel)
                del pdf_bytes

                labels = label_document([page["text"] for page in pages])
                for page, label in zip(pages, labels):
                    text = page["text"]
                    if not text:
                        empty_pages += 1
                    label_counts[label] += 1
                    writer.writerow([rel, page["page"], text[: args.max_chars], label])
                pdfs_ok += 1
            except Exception as exc:  # corrupt / encrypted / unreadable
                failed.append((rel, f"{type(exc).__name__}: {exc}"))
                print(f"  ! skipped {rel}: {type(exc).__name__}: {exc}")
            finally:
                # runs for skipped duplicates too
                if i % args.progress_every == 0 or i == len(pdfs):
                    fh.flush()
                    print(
                        f"  [{i}/{len(pdfs)}] processed={pdfs_ok} duplicates={len(duplicates)} "
                        f"failed={len(failed)} pages={sum(label_counts.values())} "
                        f"({time.perf_counter() - t0:.0f}s)"
                    )

    with failed_log.open("w", encoding="utf-8") as fh:
        for rel, err in failed:
            fh.write(f"{rel}\t{err}\n")

    total_pages = sum(label_counts.values())
    print()
    print("=== Summary ===")
    print(f"PDFs found:             {len(all_pdfs)}")
    print(f"Presentations skipped:  {len(presentations)}")
    print(f"Annual report files:    {len(pdfs)}")
    print(f"Reports processed:      {pdfs_ok}")
    print(f"Duplicates skipped:     {len(duplicates)}")
    print(f"PDFs failed:            {len(failed)}" + (f"  (see {failed_log})" if failed else ""))
    print(f"Total pages:            {total_pages}")
    print(f"Pages with no text:     {empty_pages}  (labelled 'other'; likely scanned/image pages)")
    print("Class distribution:")
    for label in LABELS:
        n = label_counts[label]
        pct = 100 * n / total_pages if total_pages else 0.0
        print(f"  {label:<18} {n:>7}  ({pct:5.1f}%)")
    print(f"Elapsed: {time.perf_counter() - t0:.0f}s")
    print(f"Output: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
