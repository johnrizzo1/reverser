"""Modal: confirm marking the session completed (terminal)."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class DoneConfirmModal(ModalScreen[bool]):
    """Yes/No: mark session completed; terminal — won't appear in resume list."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    DoneConfirmModal {
        align: center middle;
    }

    #done-dialog {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    #done-dialog Label {
        margin-bottom: 1;
    }

    #done-buttons {
        height: 3;
        align: center middle;
    }

    #done-buttons Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="done-dialog"):
            yield Label("Mark session completed?", classes="modal-title")
            yield Static(
                "Completed sessions are TERMINAL — they don't appear in the "
                "default resume list. Use this when the engagement is truly done."
            )
            with Horizontal(id="done-buttons"):
                yield Button("Yes (y)", id="yes", variant="warning")
                yield Button("No (n)", id="no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
