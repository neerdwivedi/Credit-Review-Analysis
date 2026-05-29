"""Quick V2 extraction smoke test against Kotak sample PDFs."""

import time
from pathlib import Path

from services.document_classifier import classify_document
from services.pdf_reader import extract_pages_from_pdf, preview_tables_in_pdf
from services.reconstruction.pipeline import run_financial_extraction


def load(name: str, upload_slot: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "data" / name
    with open(path, "rb") as f:
        pdf_bytes = f.read()
    return {
        "filename": path.name,
        "doc_type": classify_document(path.name, upload_slot=upload_slot),
        "pages": extract_pages_from_pdf(pdf_bytes, path.name),
        "pdf_bytes": pdf_bytes,
        # Skip full-doc table preview in smoke test (Phase 1 already does this in UI).
        "table_preview": [],
    }


def main() -> None:
    phase1 = [
        load("kotak mahindra BANK 25.pdf", "annual_report"),
        load("KOTAK MAHINDRA BANK 24.pdf", "annual_report"),
        load("kotak mahindra Ppt.pdf", "investor_presentation"),
    ]
    t0 = time.perf_counter()
    result = run_financial_extraction(phase1)
    elapsed = time.perf_counter() - t0
    t1 = sum(1 for r in result.table1_records if r["status"] == "extracted")
    t2 = sum(1 for r in result.table2_records if r["status"] == "extracted")
    print(f"Total {elapsed:.1f}s | yearly {t1}/36 | half-year {t2}/24")

    targets = {
        ("Total Income", "H1FY26"): 20239,
        ("Total Income", "H1FY25"): 19475,
        ("NII", "H1FY26"): 14570,
        ("NII", "H1FY25"): 13862,
        ("PAT", "H1FY26"): 6535,
        ("PAT", "H1FY25"): 6864,
        ("ROE", "H1FY26"): 10.69,
        ("ROE", "H1FY25"): 13.10,
    }
    print("\nHalf-year targets:")
    for r in result.table2_records:
        key = (r["metric"], r["period"])
        if key not in targets:
            continue
        exp = targets[key]
        got = r.get("value_original")
        ok = got is not None and abs(got - exp) < 1
        print(f"  {key}: got={got} exp={exp} {'OK' if ok else 'MISS'}")

    print("\nYearly extracted:")
    for r in result.table1_records:
        if r["status"] == "extracted":
            print(
                f"  {r['metric']} {r['period']}: {r['display_value']} "
                f"p.{r['page_number']} ({r['unit']})"
            )


if __name__ == "__main__":
    main()
