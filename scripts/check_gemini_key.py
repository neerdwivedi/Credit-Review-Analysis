"""Check whether Gemini extraction will run (no Streamlit required)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.llm_extractor import is_gemini_available

SECRETS = ROOT / ".streamlit" / "secrets.toml"


def _load_key() -> str:
    if not SECRETS.exists():
        return ""
    try:
        data = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        print(f"ERROR: {SECRETS} is invalid TOML: {exc}")
        print("Fix: wrap the key in double quotes, e.g. GEMINI_API_KEY = \"AIza...\"")
        return ""
    return str(data.get("GEMINI_API_KEY", "") or "").strip()


def main() -> None:
    key = _load_key()
    print("Key present:", bool(key))
    if key and not (key.startswith("AIza") or key.startswith("AQ.")):
        print(
            "Key format: unrecognized — expected AIza... (legacy) or AQ.... "
            "(new auth key from aistudio.google.com/apikey)"
        )
    print("Gemini will run:", is_gemini_available(key))


if __name__ == "__main__":
    main()
