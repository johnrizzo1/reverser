"""Modal: read-only display of session metadata."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from reverser.sessions import SessionSnapshot


class SessionInfoModal(ModalScreen[None]):
    """Show session_id, target, profile, started, last_active, cost, turns, state."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    SessionInfoModal {
        align: center middle;
    }

    #info-dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #info-dialog Static {
        margin-bottom: 0;
    }

    #info-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, snapshot: SessionSnapshot, total_cost: float, turns: int):
        super().__init__()
        self._snap = snapshot
        self._total_cost = total_cost
        self._turns = turns

    def compose(self) -> ComposeResult:
        s = self._snap
        with Vertical(id="info-dialog"):
            yield Label("Session info", classes="modal-title")
            yield Static(f"[bold]Session ID:[/]  {s.session_id}")
            yield Static(f"[bold]Target:[/]      {s.target}")
            yield Static(f"[bold]Profile:[/]     {s.config.profile}")
            yield Static(f"[bold]State:[/]       {s.state}")
            yield Static(f"[bold]Started:[/]     {s.started_at}")
            yield Static(f"[bold]Last active:[/] {s.last_active_at}")
            yield Static(
                f"[bold]Cost:[/]        ${self._total_cost:.4f} / ${s.config.budget:.2f}"
            )
            yield Static(
                f"[bold]Turns:[/]       {self._turns} / {s.config.max_turns}"
            )
            yield Static(f"[bold]Log:[/]         {s.log_path}")
            with Vertical(id="info-buttons"):
                yield Button("Close (q)", id="close")

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
