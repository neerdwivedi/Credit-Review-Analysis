"""
Live status text for long-running Streamlit operations (ChatGPT-style progress).

Updates a placeholder on each step so users see what the system is doing now.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

StatusCallback = Callable[[str], None] | None


class LiveStatus:
    """Enterprise-style live status line with optional progress bar."""

    def __init__(self, heading: str = "Processing") -> None:
        self._heading = heading
        self._slot = st.empty()
        self._progress_bar: Any | None = None
        self.update("Starting…")

    def update(self, message: str) -> None:
        """Show current step (replaces previous text)."""
        self._slot.info(f"**{self._heading}** — {message}")

    def success(self, message: str) -> None:
        self._slot.success(f"**{self._heading}** — {message}")

    def error(self, message: str) -> None:
        self._slot.error(f"**{self._heading}** — {message}")

    def set_progress(self, value: float, text: str = "") -> None:
        if self._progress_bar is None:
            self._progress_bar = st.progress(0.0, text=text)
        else:
            self._progress_bar.progress(min(1.0, max(0.0, value)), text=text)

    def clear_progress(self) -> None:
        if self._progress_bar is not None:
            self._progress_bar.empty()
            self._progress_bar = None

    def callback(self) -> StatusCallback:
        """Return a callable suitable for service-layer ``on_status`` hooks."""
        return self.update


def noop_status(_message: str) -> None:
    """No-op callback when status UI is not used."""
    return None
