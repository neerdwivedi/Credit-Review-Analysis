# Recent changes — LLM extraction & app state

*Last updated: 2026-06-10*

## What changed

### 1. `services/llm_extractor.py` — new approach (Gemini File API)

**Before:** Groq text LLM read 1–2 pages of extracted text per document and tried to fill missing metrics. Often failed JSON parse or returned values not on the page.

**Now:**
- Uploads the **full PDF** to **Google Gemini File API** (`gemini-2.0-flash`)
- Model reads the document visually (tables, slides, scanned layouts)
- Returns structured JSON for all metrics / periods it can see
- Runs **first** (Gemini → pdfplumber) to seed metric/period values from the full PDF
- **pdfplumber** runs afterward only for keys Gemini did not fill
- If no Gemini key (`AIza…`), no PDF bytes, or API error → returns `[]` and pdfplumber + derivation logic handle extraction

**Dependency added:** `google-generativeai` in `requirements.txt`

### 2. Yearly & half-year pipelines

- `yearly.py` / `half_year.py` call `llm_extract_document()` with `pdf_bytes` + `api_key` (Gemini)
- `is_gemini_available()` checks for `AIza` prefix
- Groq keys are **no longer** used for metric extraction (still used for commentary & screenshot OCR in `app.py`)

### 3. Hugging Face Spaces deployment removed

- `README.md` — local setup instructions (no HF Spaces YAML)
- `packages.txt` — deleted (HF Linux packages only)
- `streamlit` restored in `requirements.txt`

### 4. Extraction quality hardening (pdfplumber path)

Still active alongside Gemini:

- Content-based P&L page detection (`pnl_statement_table`)
- H1-only column mapping (no Q2 → H1 bleed)
- `text_regex` disabled for yearly currency metrics
- `filter_untrusted_source_hits`, sanity ranges, Q1+Q2 derivation (`financial_logic.py`)

---

## Current architecture (correct situation)

```
Phase 1 — Upload & classify PDFs
    ↓
Phase 2 — Extraction (per document)
    │
    ├─ Flow A: Annual reports → yearly.py
    │     1. Gemini File API (primary, if GEMINI_API_KEY set)
    │     2. pdfplumber fill-missing keys
    │     3. metric_logic derivations (TI, NII, PAT from components)
    │
    └─ Flow B: Investor presentations → half_year.py
          1. Gemini File API (primary, if key set)
          2. pdfplumber H1 fill-missing + Q1/Q2 columns
          3. financial_logic Q1+Q2 → H1 derivation
          4. metric_logic derivations
    ↓
Phase 3 — Human review & approve
    ↓
Phase 4–6 — Commentary (Groq) & Word report
```

| Component | Role |
|-----------|------|
| **llm_extractor.py** | Gemini visual primary extractor; pdfplumber fills gaps |
| **pdfplumber** | Secondary extractor — tables & standalone text for missing cells |
| **metric_logic.py** | Labels, sanity ranges, yearly derivations, filters |
| **financial_logic.py** | Half-year only: Q1+Q2 → H1, NII, PAT math |
| **Groq** | Commentary (`llm_commentary.py`) & screenshots (`screenshot_extractor.py`) — **not** metric table fill |
| **Gemini vision** (`vision_extractor.py`) | Legacy per-page vision — disabled in pipeline (`if False`) |

---

## What you need to run

1. **Install deps**
   ```powershell
   cd project
   .\venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. **Gemini key** (for visual extraction primary) in `.streamlit/secrets.toml`:
   ```toml
   GEMINI_API_KEY = "AIza..."
   ```

3. **Groq key** (optional — commentary & screenshot upload only):
   ```toml
   GROQ_API_KEY = "gsk_..."
   ```

4. **Run**
   ```powershell
   streamlit run app.py
   ```

---

## Known limitations

- Gemini prompt JSON example contains sample LIC numbers — model may echo similar values; sanity filter rejects out-of-range hits
- Gemini runs once per document first; pdfplumber only fills keys still empty afterward
- Requires `google-generativeai` and network access to Google APIs
- `groq` package still required for Phase 4 commentary, not for Phase 2 extraction

---

## Files touched in this change set

| File | Change |
|------|--------|
| `services/llm_extractor.py` | Replaced — Gemini File API |
| `services/reconstruction/yearly.py` | Gemini primary → pdfplumber gap-fill |
| `services/reconstruction/half_year.py` | Gemini primary → pdfplumber gap-fill |
| `app.py` | Pass Gemini key to extraction (not Groq override) |
| `requirements.txt` | `google-generativeai`, `streamlit` |
| `README.md` | Local dev instructions |
| `CHANGES.md` | This file |
