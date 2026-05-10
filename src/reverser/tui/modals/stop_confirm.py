"""Modal: confirm stopping the session."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class StopConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation: stop the session and save snapshot."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    StopConfirmModal {
        align: center middle;
    }

    #stop-dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #stop-dialog Label {
        margin-bottom: 1;
    }

    #stop-buttons {
        height: 3;
        align: center middle;
    }

    #stop-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="stop-dialog"):
            yield Label("Stop session?", classes="modal-title")
            yield Static(
                "Snapshot will be saved as resumable. "
                "You can return to it later with `--resume`."
            )
            with Horizontal(id="stop-buttons"):
                yield Button("Yes (y)", id="yes", variant="primary")
                yield Button("No (n)", id="no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
