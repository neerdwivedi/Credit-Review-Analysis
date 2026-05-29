# Credit Review Report Generator — Phases 1–5

Finance-grade PDF upload, deterministic metric extraction, human review, commentary, and Word report export.

**Phase 1:** PDF upload, page-wise text, document classification, table preview, logging.

**Phase 2 (V2):** Deterministic **report reconstruction** engine — two isolated flows (`yearly` / `half_year`), table-first extraction with standalone text fallback for annual reports, fuzzy row matching, and structured provenance (no AI, no derived values).

**Phase 3:** Review screen with editable values, status tracking, re-validation, approval workflow, and CSV export.

**Phase 4:** Rule-based commentary from **approved values only** → `output/commentary.json`.

**Phase 5:** Analytical **credit_review_report.docx** (python-docx) with tables, commentary, validation notes, provenance, and CIO box.

**Phase 6:** Enterprise formatter — optional uploaded `.docx` template (Kotak / fund format) or default template; outputs **final_credit_review.docx** (+ PDF when Word/docx2pdf available). Preserves template styling.

**Not included yet:** Groq/LLM commentary rewrite.

---

## Project structure

```
project/
├── app.py                      # Streamlit UI
├── requirements.txt
├── README.md
├── services/
│   ├── pdf_reader.py           # PyMuPDF text + pdfplumber table preview
│   ├── document_classifier.py  # Filename-based classification
│   ├── extractor.py            # Phase 2 entry (re-exports V2 engine)
│   ├── reconstruction/         # V2: yearly + half-year flows, table/text parsers
│   ├── normalizer.py           # Units, periods, crore conversion
│   ├── validator.py            # Sanity checks + warnings
│   ├── review_manager.py       # Phase 3 review records + edit logic
│   ├── commentary_generator.py # Phase 4 deterministic commentary
│   ├── report_generator.py     # Phase 5 analytical DOCX
│   └── template_formatter.py   # Phase 6 enterprise template inject
├── data/
│   └── metric_aliases.py       # Approved metrics + aliases
├── utils/
│   ├── logger.py
│   └── constants.py
├── data/                       # Uploaded PDFs (saved on extraction)
├── templates/                  # enterprise_default.docx + uploaded templates
└── output/                     # commentary.json, credit_review_report.docx, final_credit_review.*
```

---

## Prerequisites

- Python 3.10 or newer
- Windows, macOS, or Linux

---

## Setup (exact steps)

### 1. Open a terminal in the project folder

```powershell
cd "c:\Users\neerd\OneDrive\Desktop\Credit Review 1.1\project"
```

### 2. Create a virtual environment (recommended)

```powershell
python -m venv venv
```

### 3. Activate the virtual environment

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
source venv/bin/activate
```

### 4. Install dependencies

```powershell
pip install -r requirements.txt
```

---

## Run the app

From the `project` folder (with the virtual environment activated):

```powershell
streamlit run app.py
```

Your browser should open to `http://localhost:8501`.

---

## How to use

1. Upload one or more **Annual Report PDFs** (required).
2. Upload one or more **Investor Presentation PDFs** (required).
3. Optionally upload one or more **Concall Transcript PDFs**.
4. Click **Run Extraction** — Phase 1 scans PDFs, Phase 2 extracts metrics, Phase 3 opens review.
5. Review **Yearly Financials** (31.03.2025 / 2024 / 2023 from annual report, standalone).
6. Review **Half-Year Financials** (H1FY26 / H1FY25 from investor presentation — half-year only, not Q2).
7. In the provenance editor, change **Approved Value** for any cell — its **Status** becomes `Manually Edited`.
8. Click **Save Reviewed Data** to persist edits, **Reset Edits** to revert to extracted values.
9. Click **Approve Extraction** when satisfied — re-validates and locks the dataset.
10. Click **Download Reviewed Extraction CSV** to export the audit-ready dataset.

After approval, the app stops at:

> Extraction approved. You can proceed to commentary/report generation in the next phase.

Uploaded files are copied to `data/`. Logs are written to `output/extraction.log`.

---

## Document classification

Classification uses **filename keywords** (case-insensitive):

| Type | Example keywords |
|------|------------------|
| Annual report | `annual`, `fy25`, `integrated report`, `annual report` |
| Investor presentation | `presentation`, `investor`, `q2fy26`, `earnings`, `ppt` |
| Concall transcript | `concall`, `earnings call`, `transcript` |

If the filename does not match keywords, the upload slot (which field you used) is used as a fallback.

---

## Validation warnings

| Warning | Meaning |
|---------|---------|
| No text extracted | PDF may be empty or unreadable |
| Possible scanned PDF | Very little text per page (image-based PDF) |
| No tables found | pdfplumber did not detect tables |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run from `project` folder; ensure `pip install -r requirements.txt` completed |
| Streamlit not found | Activate venv, then `pip install streamlit` |
| Extraction errors | See `output/extraction.log` for details |

---

## Phase 2 business rules (enforced)

| Rule | Detail |
|------|--------|
| Table 1 source | Annual report only |
| Table 1 periods | 31.03.2025, 31.03.2024, 31.03.2023 (March year-end) |
| Table 2 source | Investor presentation only |
| Table 2 periods | H1FY26, H1FY25 (half-year September — **not** Q1/Q2 unless explicit H1) |
| Statements | Standalone preferred; consolidated ignored if standalone exists |
| Values | As disclosed only — never derived or calculated |
| Units | Detected and converted to Rs crore (ratios stay as %) |

## Phase 3 review rules (enforced)

| Rule | Detail |
|------|--------|
| Editable | `Approved Value`, `Notes` — nothing else |
| Read-only | Source document, page number, original unit, confidence, extracted value |
| Manual edit | Approved Value differs from initial → `Status = Manually Edited` |
| Missing | Approved Value blank → `Status = Missing` |
| Warning | Approved Value fails sanity / cross-metric check → `Status = Warning` |
| Approval | Clicking Approve re-validates and upgrades clean rows to `Status = Approved` |
| Override | User may approve despite warnings; banner reminds them |

## Phase 4+ (not implemented)

- Groq / LLM (writing only)
- DOCX report generation
- Commentary
