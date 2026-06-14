# Credit Review Report Generator

Streamlit app for credit analysts: upload annual reports and investor presentations, extract financial metrics, review and approve values, then generate commentary and Word reports.

## Setup (local)

```powershell
cd project
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional API keys in `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "gsk_..."
GEMINI_API_KEY = "..."   # optional vision fallback
```

## Run

```powershell
streamlit run app.py
```

Full documentation: [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md)
