"""
HyperClaw TUI — terminal user interface built with Textual.
Stub for v0.1.0-alpha.
"""
from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Label

    class HyperClawTUI(App):
        """HyperClaw Terminal UI — v0.1.0-alpha stub."""

        CSS = """
        Screen { align: center middle; }
        Label { padding: 1 2; }
        """

        def compose(self) -> ComposeResult:
            yield Header()
            yield Label("⚡ HyperClaw v0.1.0-alpha — Full TUI coming in v0.2.0")
            yield Footer()

        def on_mount(self) -> None:
            self.title = "HyperClaw"
            self.sub_title = "Multi-Agent AI Swarm Platform"

except ImportError:
    class HyperClawTUI:  # type: ignore
        """Textual not installed — TUI unavailable."""
        pass
