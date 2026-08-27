"""
HyperClaw TUI — Textual terminal user interface.
Screens: Dashboard, HyperState, Knowledge, Impact, Research, Audit
"""
from __future__ import annotations

from datetime import datetime


try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.screen import Screen
    from textual.widgets import (
        DataTable, Footer, Header, Label, Log, Markdown,
        Static, TabbedContent, TabPane,
    )

    BANNER = """\
 ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗  ██████╗██╗      █████╗ ██╗    ██╗
 ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔════╝██║     ██╔══██╗██║    ██║
 ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║     ██║     ███████║██║ █╗ ██║
 ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║     ██║     ██╔══██║██║███╗██║
 ██║  ██║   ██║   ██║     ███████╗██║  ██║╚██████╗███████╗██║  ██║╚███╔███╔╝
 ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝"""

    AGENTS = [
        ("ATLAS", "personal", "planning,scheduling,synthesis"),
        ("MIDAS", "personal", "analysis,finance,planning"),
        ("VITALS", "personal", "health,analysis,planning"),
        ("NOURISH", "personal", "health,planning,research"),
        ("NAVIGATOR", "personal", "planning,research,synthesis"),
        ("HEARTH", "personal", "planning,scheduling,quick_lookup"),
        ("STRATEGOS", "business", "analysis,research,planning,synthesis"),
        ("HERALD", "business", "synthesis,planning"),
        ("PIPELINE", "business", "research,synthesis,quick_lookup"),
        ("LEDGER", "business", "analysis,finance,synthesis"),
        ("COUNSEL", "business", "research,analysis,synthesis"),
        ("TALENT", "business", "analysis,research,planning"),
        ("MEDICUS", "scientific", "research,analysis,scientific,health"),
        ("COSMOS", "scientific", "research,analysis,scientific,code"),
        ("GAIA", "scientific", "research,analysis,scientific"),
        ("ORACLE", "scientific", "analysis,code,scientific,finance"),
        ("SCRIBE", "scientific", "research,synthesis,summarization"),
        ("AUTHOR", "creative", "synthesis,research,planning"),
        ("LENS", "creative", "research,quick_lookup,summarization"),
        ("SCOUT", "recursive", "research,quick_lookup,summarization"),
        ("ALCHEMIST", "recursive", "code,research,analysis"),
        ("CALIBRATOR", "recursive", "analysis,routing"),
        ("NEXUS", "recursive", "planning,routing,coordination"),
    ]

    class DashboardScreen(Screen):
        """Live agent grid + cost tracker + message feed."""

        def compose(self) -> ComposeResult:
            yield Header()
            with TabbedContent():
                with TabPane("Agents", id="agents-tab"):
                    table = DataTable(id="agent-table")
                    table.add_columns("Agent", "Domain", "Task Types", "Status")
                    for agent_id, domain, task_types in AGENTS:
                        table.add_row(agent_id, domain, task_types[:40], "✓ Ready")
                    yield table
                with TabPane("Messages", id="messages-tab"):
                    yield Log(id="message-log", highlight=True)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — Dashboard"
            log = self.query_one("#message-log", Log)
            log.write_line(f"[{datetime.now().strftime('%H:%M:%S')}] HyperClaw swarm initialized")
            log.write_line(f"[{datetime.now().strftime('%H:%M:%S')}] 23 agents registered and ready")
            log.write_line(f"[{datetime.now().strftime('%H:%M:%S')}] Recursive Growth Engine: active")

    class HyperStateScreen(Screen):
        """Active HyperStates list and inspection."""

        def compose(self) -> ComposeResult:
            yield Header()
            yield Label("HyperState Manager — Active states will appear here when tasks are running.", id="state-info")
            yield Static("Use `hyperclaw state list` to view states in CLI mode.", classes="dim")
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — HyperState"

    class KnowledgeScreen(Screen):
        """HyperMemory stats."""

        def compose(self) -> ComposeResult:
            yield Header()
            with ScrollableContainer():
                yield Markdown("""
# HyperMemory Knowledge Graph

| Metric | Value |
|--------|-------|
| Knowledge Nodes | 0 (connect DB to populate) |
| Causal Edges | 0 |
| Certified Skills | 0 |
| Discoveries This Week | 0 |

## Recent Causal Edges
No edges recorded yet. Run tasks to populate the knowledge graph.
                """)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — Knowledge"

    class ImpactScreen(Screen):
        """Impact records by domain."""

        def compose(self) -> ComposeResult:
            yield Header()
            yield Markdown("""
# Impact Dashboard

No impact records yet. Records are created when NEXUS completes measurable tasks.

Run: `hyperclaw swarm run "your goal"` to start generating impact data.
            """)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — Impact"

    class ResearchScreen(Screen):
        """SCOUT discoveries, ALCHEMIST queue, certified skills."""

        def compose(self) -> ComposeResult:
            yield Header()
            with ScrollableContainer():
                yield Markdown("""
# Recursive Research

## SCOUT Status
- Sources monitored: arXiv cs.AI, arXiv q-bio, arXiv astro-ph, GitHub trending, PubMed
- Last sweep: Never (run `hyperclaw research sweep` to trigger)

## ALCHEMIST Queue
No pending discoveries.

## Certified Skills
No skills certified yet.
                """)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — Research"

    class AuditScreen(Screen):
        """HyperShield audit log."""

        def compose(self) -> ComposeResult:
            yield Header()
            yield Log(id="audit-log", highlight=True)
            yield Footer()

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw — Audit"
            log = self.query_one("#audit-log", Log)
            log.write_line("HyperShield audit log — events will appear here during operation.")

    class HyperClawTUI(App):
        """HyperClaw Terminal User Interface."""

        CSS = """
        Screen { background: #0d1117; }
        Header { background: #161b22; color: #58a6ff; }
        Footer { background: #161b22; }
        DataTable { height: 1fr; }
        Log { height: 1fr; border: solid #30363d; }
        Label#state-info { padding: 1 2; color: #58a6ff; }
        .dim { color: #8b949e; padding: 0 2; }
        """

        BINDINGS = [
            Binding("d", "switch_mode('dashboard')", "Dashboard"),
            Binding("s", "switch_mode('hyperstate')", "HyperState"),
            Binding("k", "switch_mode('knowledge')", "Knowledge"),
            Binding("i", "switch_mode('impact')", "Impact"),
            Binding("r", "switch_mode('research')", "Research"),
            Binding("a", "switch_mode('audit')", "Audit"),
            Binding("q", "quit", "Quit"),
            Binding("?", "help", "Help"),
        ]

        MODES = {
            "dashboard": DashboardScreen,
            "hyperstate": HyperStateScreen,
            "knowledge": KnowledgeScreen,
            "impact": ImpactScreen,
            "research": ResearchScreen,
            "audit": AuditScreen,
        }

        def on_mount(self) -> None:
            self.title = "⚡ HyperClaw"
            self.switch_mode("dashboard")

except ImportError:
    # Textual not available — provide a no-op stub
    class HyperClawTUI:  # type: ignore
        """Textual not installed — TUI unavailable. Install with: pip install textual"""

        def run(self) -> None:
            print("TUI unavailable. Install textual: pip install 'hyperclaw[tui]'")

    BANNER = "HyperClaw v0.1.0-alpha"
    AGENTS = []
