# Credit Review Report Generator — Complete Project Documentation

This document explains **everything built so far** in the Credit Review Report Generator: what the system does, how each phase works, important design rules, recent fixes, and how to use it end to end.

For quick setup and run commands, see [README.md](README.md).

---

## Table of contents

1. [Purpose and design principles](#1-purpose-and-design-principles)
2. [High-level workflow](#2-high-level-workflow)
3. [Project structure](#3-project-structure)
4. [Phase 1 — PDF scan and classification](#4-phase-1--pdf-scan-and-classification)
5. [Phase 2 — Deterministic financial extraction (V2)](#5-phase-2--deterministic-financial-extraction-v2)
6. [Phase 3 — Human review and approval](#6-phase-3--human-review-and-approval)
7. [Phase 4 — Institutional commentary](#7-phase-4--institutional-commentary)
8. [Phase 5 — Analytical Word report](#8-phase-5--analytical-word-report)
9. [Phase 6 — Enterprise template formatter](#9-phase-6--enterprise-template-formatter)
10. [User experience — live status messages](#10-user-experience--live-status-messages)
11. [Approved metrics and tables](#11-approved-metrics-and-tables)
12. [Outputs and file locations](#12-outputs-and-file-locations)
13. [What is not included yet](#13-what-is-not-included-yet)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Purpose and design principles

The application helps credit analysts:

1. Upload bank/NBFC financial PDFs (annual report, investor presentation, optional concall).
2. **Extract** disclosed financial metrics deterministically (no AI guessing numbers).
3. **Review and approve** values in a spreadsheet-style UI.
4. Generate **rule-based commentary** and **Word reports** in an institutional format.
5. Optionally apply a **fund-specific enterprise template** while keeping layout and replacing all business content.

### Core rules (always enforced)

| Rule | Meaning |
|------|---------|
| **No invented values** | If a number is not explicitly found in a source PDF, it is shown as **Not Disclosed**. |
| **No derived metrics** | The system does not calculate ratios or balances from other line items. |
| **Two isolated tables** | Yearly (March year-end from annual report) and half-year (H1 from investor PPT) are separate flows. |
| **Standalone preferred** | When both standalone and consolidated statements exist, standalone is used. |
| **Approved values drive downstream** | Commentary and reports use **approved** values from the review screen only. |
| **Template = format only** | Uploaded enterprise `.docx` templates keep design; **content** is replaced from extraction. |

---

## 2. High-level workflow

```
Upload PDFs
        │
        ▼
Phase 1 — Scan PDFs (text, tables, classification)
        │
        ▼
Phase 2 — Extract metrics (yearly + half-year tables)
        │
        ▼
Phase 3 — Review, edit, save, approve
        │
        ▼
Phase 4 — Generate institutional commentary (JSON)
        │
        ▼
Phase 5 — Generate analytical report (credit_review_report.docx)
        │
        ▼
Phase 6 — Apply enterprise template → final_credit_review.docx (+ PDF if available)
```

The Streamlit app shows a **progress bar** across phases 1–6 and uses **live status text** during long operations so users know what is running.

---

## 3. Project structure

```
project/
├── app.py                          # Streamlit UI (all phases)
├── requirements.txt
├── README.md                       # Setup and quick start
├── PROJECT_DOCUMENTATION.md        # This file — full explanation
│
├── services/
│   ├── pdf_reader.py               # PyMuPDF text + pdfplumber table preview
│   ├── document_classifier.py      # Filename / upload-slot classification
│   ├── extractor.py                # Thin entry → V2 reconstruction engine
│   ├── normalizer.py               # Periods, units, crore conversion
│   ├── validator.py                # Sanity + cross-metric warnings
│   ├── review_manager.py           # Phase 3 review records + data editor
│   ├── commentary_generator.py     # Phase 4 section commentary
│   ├── report_generator.py         # Phase 5 analytical DOCX
│   ├── template_formatter.py       # Phase 6 template reconstruction
│   └── reconstruction/               # V2 extraction engine
│       ├── pipeline.py             # Orchestrates yearly + half-year
│       ├── yearly.py               # Annual report extraction
│       ├── half_year.py            # Investor PPT extraction
│       ├── table_engine.py         # pdfplumber tables + fuzzy rows
│       ├── text_standalone.py      # Text fallback (e.g. Kotak AR tables)
│       ├── page_index.py           # Section / page scoring
│       ├── document.py             # Document context
│       └── similarity.py           # Fuzzy metric row matching
│
├── data/
│   ├── metric_aliases.py           # Approved metrics, periods, aliases
│   └── metric_exclusions.py        # Rows to reject (subsidiary noise)
│
├── utils/
│   ├── constants.py                # Paths, document type constants
│   ├── logger.py                   # Logging to output/extraction.log
│   └── live_status.py              # ChatGPT-style progress messages in UI
│
├── scripts/
│   ├── test_v2_extraction.py       # Smoke test extraction on sample PDFs
│   └── test_template_reconstruction.py  # Smoke test Phase 6 replace-in-place
│
├── data/                           # Saved uploaded PDFs
├── templates/                      # Default + uploaded enterprise .docx
└── output/                         # Logs, JSON, DOCX, PDF outputs
```

---

## 4. Phase 1 — PDF scan and classification

**What happens**

- User uploads PDFs in three categories.
- Each file is saved under `data/`, pages are read with **PyMuPDF**, and tables are previewed with **pdfplumber**.
- Document type is assigned using **filename keywords** and the **upload slot** (which uploader was used).

**Document types**

| Upload field | Type | Used for metrics? |
|--------------|------|-------------------|
| Annual Report PDFs | `annual_report` | Yes — Table 1 (yearly) |
| Investor Presentation PDFs | `investor_presentation` | Yes — Table 2 (half-year) |
| Concall Transcript PDFs | `concall_transcript` | No (scan only) |

**Warnings**

- No text extracted (empty or unreadable PDF).
- Possible scanned PDF (very little text per page).
- No tables found.

Phase 1 results are stored in session and passed to Phase 2.

---

## 5. Phase 2 — Deterministic financial extraction (V2)

**Engine location:** `services/reconstruction/`

**Two separate flows**

| Flow | Source documents | Periods |
|------|------------------|---------|
| **Yearly** | Annual report(s) only | 31.03.2025, 31.03.2024, 31.03.2023 |
| **Half-year** | Investor presentation(s) only | H1FY26, H1FY25 |

Quarterly columns (Q1/Q2 only) are **rejected** for half-year unless explicit half-year / H1 wording is present.

**How extraction works**

1. **Page indexing** — Find pages likely containing standalone P&L / balance sheet / investor slides.
2. **Table-first** — pdfplumber extracts tables; rows matched to approved metrics via fuzzy aliases (`similarity.py`).
3. **Text fallback** — For some annual reports (e.g. Kotak) where tables collapse to one column, a standalone text parser reads line-based values.
4. **Unit detection** — Crore, lakh, thousand, million from headers/footnotes; values stored with `value_crore` for currency and raw % for ratios.
5. **Validation** — Sanity checks and cross-metric warnings (e.g. Tier I vs CAR).

**Output**

- Raw records merged into one list for Phase 3.
- Validation summary written to `output/validation_summary.txt`.

**Entry point:** `run_financial_extraction()` in `services/reconstruction/pipeline.py`, called via `services/extractor.py`.

---

## 6. Phase 3 — Human review and approval

**Module:** `services/review_manager.py`  
**UI:** Review screen in `app.py`

### What the analyst sees

- **Pivot tables** — Snapshot of approved values by metric and period (with page references where available).
- **Detailed provenance editor** — One row per (metric, period) with extracted value, approved value, unit, converted crore, source file, page, confidence, status, notes.

### Editable vs read-only

| Editable | Read-only |
|----------|-----------|
| Approved Value (Crore / %) | Metric, Period |
| Notes | Extracted Value, Original Unit, Converted Value (Crore) |
| | Source, Page, Confidence, Status |
| | **Analyst Override** (checkbox — auto-set, not clickable) |

### Approved Value and Converted Value sync

- **Approved Value** is always the **final normalized** figure: ₹ **crore** for currency metrics, **percent** for ratios (ROE, ROA, CAR, etc.).
- **Extracted Value** shows the number as printed in the PDF (often in lakh or other units).
- **Converted Value (Crore)** mirrors Approved Value after save — when the analyst changes Approved Value to `45678`, Converted Value becomes `45678` as well.
- Missing cells use `NaN` in the editor so empty rows remain **editable** (fixes broken NumberColumn behaviour with `None`).

### Analyst Override (formerly “Manual Edit”)

- The checkbox column is **disabled** on purpose (no broken click UX).
- If Approved Value ≠ initial extraction baseline → **Analyst Override** is checked and status becomes **Manually Edited**.

### Status priority

1. Missing — no approved value  
2. Warning — sanity or cross-metric failure  
3. Manually Edited — analyst changed value  
4. Low Confidence — confidence below threshold  
5. Extracted — clean auto-extraction  
6. Approved — after **Approve Extraction** (clean rows upgraded)

### Actions

| Button | Effect |
|--------|--------|
| **Save Reviewed Data** | Merges editor → session; re-validates warnings |
| **Reset Edits** | Rebuilds review records from raw extraction |
| **Approve Extraction** | Locks dataset; enables Phases 4–6 |
| **Download Reviewed Extraction CSV** | Audit export |

---

## 7. Phase 4 — Institutional commentary

**Module:** `services/commentary_generator.py`  
**Output:** `output/commentary.json`

### Design (not per-metric spam)

Commentary is **section-based professional paragraphs**, not one sentence per line item.

| Section | Content |
|---------|---------|
| Business Profile | Issuer context |
| Profitability | Up to two operating metrics (e.g. NII + PAT) in one paragraph |
| Capitalisation | CAR / Tier I or “not disclosed” wording |
| Liquidity | Deposit / borrowing trends when disclosed |

### Rules

- Uses **approved extraction values only**.
- Never invents, infers, or calculates missing metrics.
- Missing metrics mentioned briefly (e.g. “ROA was not disclosed.”).
- **Rule-based only** — no LLM in this phase.

### UI

- **Generate Commentary** with live status messages.
- Preview by section; download `commentary.json`.
- Regenerate clears downstream report / Phase 6 flags.

---

## 8. Phase 5 — Analytical Word report

**Module:** `services/report_generator.py`  
**Output:** `output/credit_review_report.docx`

**Sections typically included**

- Title page (issuer, date)
- Issuer overview
- Yearly financial table
- Half-year financial table
- Commentary (institutional sections)
- Validation notes
- Provenance table
- CIO / fund manager placeholder box

Built with **python-docx**. Uses approved pivot data and Phase 4 commentary.

---

## 9. Phase 6 — Enterprise template formatter

**Module:** `services/template_formatter.py`  
**Outputs:** `output/final_credit_review.docx`, optional `output/final_credit_review.pdf`

### Goal: format only, not stale content

Uploaded fund templates (e.g. Kotak, Aditya Birla style) often contain **placeholder numbers and old commentary**. Phase 6:

| Preserves | Replaces |
|-----------|----------|
| Layout, fonts, styles, borders, spacing | Company name, review date |
| Table structure and cell formatting | All financial table **values** |
| Heading styles | Commentary / narrative paragraphs |
| CIO box layout | Validation notes, profile text |

### How reconstruction works

1. **Placeholders** — `{{COMPANY_NAME}}`, `{{YEARLY_TABLE}}`, `{{COMMENTARY}}`, etc.
2. **Title metadata** — Issuer name and date in title block.
3. **All financial tables** — Scans every table in the document; matches rows by metric name and columns by period; overwrites cell values in place.
4. **Smart sections** — Headings like “Profitability”, “Capitalisation”, “Commentary” get body text cleared and replaced with Phase 4 content.

**Important:** Template PAT `2106` becomes extracted `13720` — stale template numbers are **never** kept.

Default template: `templates/enterprise_default.docx` (auto-created if missing).  
User may upload a custom `.docx` in the Phase 6 UI.

PDF export uses `docx2pdf` or Microsoft Word COM on Windows when available.

---

## 10. User experience — live status messages

**Module:** `utils/live_status.py`

During long operations the app shows **changing status lines** (similar to ChatGPT “thinking” text), not a static “Loading…”.

Examples:

| Stage | Example messages |
|-------|------------------|
| Extraction | “Reading uploaded annual report…”, “Extracting yearly financial metrics…”, “Validating extracted numbers…” |
| Commentary | “Generating profitability commentary…”, “Writing capitalisation analysis…” |
| Report | “Formatting yearly financial tables…”, “Injecting approved commentary…” |
| Enterprise | “Replacing financial tables…”, “Preparing DOCX export…” |

Completed steps show a green success line (e.g. “Extraction complete — review and approve tables below.”).

---

## 11. Approved metrics and tables

Defined in `data/metric_aliases.py`.

**Metrics (13)**

Total Income, NII, PAT, Total Assets, Borrowings, Investments, Advances, Deposits, Capital Adequacy Ratio, Tier I Capital Ratio, ROA, ROE.

**Ratio metrics (no crore conversion)**

Capital Adequacy Ratio, Tier I Capital Ratio, ROA, ROE.

**Table 1 periods (annual report)**

31.03.2025, 31.03.2024, 31.03.2023.

**Table 2 periods (investor PPT)**

H1FY26, H1FY25.

Aliases (e.g. “Net Interest Income” → NII) and row exclusions (subsidiary lines) are in the same data module.

---

## 12. Outputs and file locations

| File | Description |
|------|-------------|
| `output/extraction.log` | Detailed extraction log |
| `output/validation_summary.txt` | Human-readable validation after Phase 2 |
| `output/commentary.json` | Phase 4 institutional commentary |
| `output/credit_review_report.docx` | Phase 5 analytical report |
| `output/final_credit_review.docx` | Phase 6 enterprise-formatted report |
| `output/final_credit_review.pdf` | PDF if export available |
| `data/*.pdf` | Copies of uploaded PDFs |

---

## 13. What is not included yet

- **Grok / OpenRouter / LLM commentary** — Phase 4 is deterministic; LLM rewrite is listed as future work in README.
- **Automatic PDF classification from content** — Classification is filename + upload slot.
- **Concall-driven metrics** — Concall PDFs are scanned but not used for the two financial tables.

---

## 14. Troubleshooting

| Issue | What to check |
|-------|----------------|
| Many “Not Disclosed” on Kotak AR | Known pdfplumber table issue; V2 text fallback should run — see `extraction.log` |
| Half-year NII missing | Ensure investor PPT uploaded; check H1 period headers in log |
| Approved / Converted column out of sync | Click **Save Reviewed Data** after editing Approved Value |
| Enterprise report still has old numbers | Re-run Phase 6 after approval; ensure template has recognizable period headers |
| PDF export missing | Install Word or `pip install docx2pdf` on Windows |
| Module errors | Activate `venv`, run `pip install -r requirements.txt` from `project/` folder |

**Run the app**

```powershell
cd "path\to\project"
.\venv\Scripts\Activate.ps1
streamlit run app.py
```

---

## Document history

This file consolidates work through:

- V2 extraction engine (yearly / half-year, table + text fallback)
- Phases 3–6 (review, commentary, analytical report, enterprise template reconstruction)
- Institutional commentary format (section paragraphs, not per-metric lines)
- Template reconstruction (replace content, preserve design)
- Live status UX, review screen fixes (NaN editability, approved/crore sync, Analyst Override column)

For commit-level changes, refer to your git history and `output/extraction.log` during runs.

---

*Credit Review Report Generator — internal finance tooling. Values are only as good as disclosed sources and analyst approval.*
