"""Interactive TUI application for the reverser agent."""

import asyncio
import atexit
import os
import signal
import sys
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)


def _apply_selection_style(strip: Strip, style, start: int, end: int) -> Strip:
    """Apply a Rich style to a character range within a Strip.

    Args:
        strip: The Strip to modify.
        style: Rich Style to apply for the selection highlight.
        start: Start column (inclusive).
        end: End column (exclusive), or -1 for end of line.
    """
    from rich.segment import Segment
    from rich.style import Style as RichStyle

    segments = list(strip._segments)
    new_segments = []
    col = 0
    for seg in segments:
        seg_len = len(seg.text)
        seg_end = col + seg_len
        effective_end = seg_end if end == -1 else end

        if col >= effective_end or seg_end <= start:
            # Entirely outside selection
            new_segments.append(seg)
        elif col >= start and seg_end <= effective_end:
            # Entirely inside selection
            new_segments.append(Segment(seg.text, (seg.style or RichStyle()) + style, seg.control))
        else:
            # Partially overlapping — split the segment
            sel_start = max(start - col, 0)
            sel_end = seg_len if end == -1 else min(end - col, seg_len)
            if sel_start > 0:
                new_segments.append(Segment(seg.text[:sel_start], seg.style, seg.control))
            new_segments.append(Segment(
                seg.text[sel_start:sel_end],
                (seg.style or RichStyle()) + style,
                seg.control,
            ))
            if sel_end < seg_len:
                new_segments.append(Segment(seg.text[sel_end:], seg.style, seg.control))
        col = seg_end

    return Strip(new_segments, strip.cell_length)


class SelectableRichLog(RichLog):
    """RichLog with text selection and smart auto-scroll.

    RichLog is a ScrollView whose rendering pipeline doesn't support
    Textual's built-in text selection.  Three things are needed:

    1. ``apply_offsets`` on each rendered strip so the compositor can map
       mouse positions to text coordinates.
    2. Selection highlight styling applied to strips that overlap the
       current selection span.
    3. Cache invalidation when the selection changes so highlights repaint.

    This mirrors what ``textual.widgets.Log`` does internally.
    """

    def render_line(self, y: int) -> Strip:
        """Render a line with offset metadata and selection highlighting."""
        scroll_x, scroll_y = self.scroll_offset
        content_y = scroll_y + y
        strip = super().render_line(y)

        # Apply selection highlight if active
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(content_y)
            if span is not None:
                start, end = span
                sel_style = self.screen.get_component_rich_style("screen--selection")
                strip = _apply_selection_style(strip, sel_style, start, end)

        return strip.apply_offsets(scroll_x, content_y)

    def selection_updated(self, selection):
        """Clear render cache and refresh when selection changes."""
        self._line_cache.clear()
        self.refresh()

    def get_selection(self, selection):
        """Return log text so the selection system can extract from it."""
        text = "\n".join(line.text for line in self.lines)
        return selection.extract(text), "\n"

    def write(self, content=None, width=None, expand=False, shrink=True, scroll_end=None, animate=False):
        # Only auto-scroll if the user is already at the bottom.
        # This lets users scroll back to read history without being
        # yanked to the end on every new write.
        if scroll_end is None:
            scroll_end = self.is_vertical_scroll_end
        return super().write(content, width=width, expand=expand, shrink=shrink, scroll_end=scroll_end, animate=animate)


class HistoryInput(Input):
    """Input widget with up/down arrow history navigation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""

    def add_to_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_index = -1
        self._draft = ""

    async def _on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            event.prevent_default()
            if not self._history:
                return
            if self._history_index == -1:
                self._draft = self.value
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            self.value = self._history[self._history_index]
            self.cursor_position = len(self.value)
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            if self._history_index == -1:
                return
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.value = self._history[self._history_index]
            else:
                self._history_index = -1
                self.value = self._draft
            self.cursor_position = len(self.value)

from rich.markup import escape as markup_escape

from ..profiles import Profile, Skill, get_profile, list_profiles
from ..backends import AgentEvent
from ..tools._common import is_url
from .session import AgentSession, _WEB_PROFILES


# ── Modal screens ───────────────────────────────────────────────────


class ProfileScreen(ModalScreen[str]):
    """Modal for selecting an agent profile."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    DEFAULT_CSS = """
    ProfileScreen {
        align: center middle;
    }
    #profile-dialog {
        width: 70;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #profile-dialog Label {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #profile-list {
        height: auto;
        max-height: 20;
    }
    #profile-list > ListItem {
        padding: 0 1;
    }
    """

    def __init__(self, current_key: str):
        super().__init__()
        self.current_key = current_key

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-dialog"):
            yield Label("Select Profile")
            yield ListView(
                *[
                    ListItem(
                        Static(self._format_profile(p)),
                        id=f"profile-{p.key}",
                    )
                    for p in list_profiles()
                ],
                id="profile-list",
            )

    def _format_profile(self, p: Profile) -> str:
        marker = " *" if p.key == self.current_key else "  "
        return f"{marker} [{p.key}] {p.name} — {p.description}"

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        key = item_id.replace("profile-", "")
        self.dismiss(key)


class SkillScreen(ModalScreen[str]):
    """Modal for selecting a skill to execute."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    DEFAULT_CSS = """
    SkillScreen {
        align: center middle;
    }
    #skill-dialog {
        width: 70;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #skill-dialog Label {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #skill-list {
        height: auto;
        max-height: 20;
    }
    #skill-list > ListItem {
        padding: 0 1;
    }
    """

    def __init__(self, skills: list[Skill]):
        super().__init__()
        self.skills = skills

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-dialog"):
            yield Label("Skills")
            yield ListView(
                *[
                    ListItem(
                        Static(f"  [{s.key}] {s.name} — {s.description}"),
                        id=f"skill-{s.key}",
                    )
                    for s in self.skills
                ],
                id="skill-list",
            )

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        key = item_id.replace("skill-", "")
        self.dismiss(key)


class SudoPasswordScreen(ModalScreen[str]):
    """Modal for entering the sudo password (masked input)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    DEFAULT_CSS = """
    SudoPasswordScreen {
        align: center middle;
    }
    #sudo-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #sudo-dialog Label {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #sudo-hint {
        width: 100%;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="sudo-dialog"):
            yield Label("Sudo Password")
            yield Static(
                "Required for privileged scans (nmap SYN/UDP, OS detection, etc.)",
                id="sudo-hint",
            )
            yield Input(
                placeholder="Enter sudo password...",
                password=True,
                id="sudo-password-input",
            )

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class LoadTargetScreen(ModalScreen[str]):
    """Simple input for loading a binary path or target URL."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    DEFAULT_CSS = """
    LoadTargetScreen {
        align: center middle;
    }
    #load-dialog {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #load-dialog Label {
        width: 100%;
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, current_path: str = ""):
        super().__init__()
        self.current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical(id="load-dialog"):
            yield Label("Load Target")
            yield Input(
                value=self.current_path,
                placeholder="Path to binary or target URL...",
                id="target-input",
            )

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


# ── Main application ────────────────────────────────────────────────


class ReverserApp(App):
    """Interactive TUI for the reverser agent."""

    TITLE = "Reverser Agent"
    ALLOW_SELECT = True

    CSS = """
    #chat-log {
        height: 1fr;
        border: solid $primary-background;
        scrollbar-size: 1 1;
    }
    #status-bar {
        height: 3;
        padding: 0 1;
        background: $primary-background;
        color: $text;
    }
    #status-bar Static {
        width: 1fr;
    }
    #input-container {
        height: auto;
        dock: bottom;
    }
    #user-input {
        margin: 0 0;
    }
    """

    BINDINGS = [
        Binding("f1", "show_skills", "Skills", show=True),
        Binding("f2", "show_profiles", "Profile", show=True),
        Binding("f3", "load_binary", "Load", show=True),
        Binding("f4", "set_sudo", "Sudo", show=True),
        Binding("f5", "clear_log", "Clear", show=True),
        Binding("f6", "stop_session", "Stop", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True, priority=True),
    ]

    def __init__(
        self,
        binary_path: str = "",
        profile_key: str = "general",
        budget: float = 5.0,
        max_turns: int = 50,
        backend: str = "claude",
        model: str | None = None,
        api_base: str | None = None,
        resume_from=None,  # SessionSnapshot | None
    ):
        super().__init__()
        self.binary_path = binary_path
        self.profile_key = profile_key
        self.budget = budget
        self.max_turns = max_turns
        self.backend_name = backend
        self.model = model
        self.api_base = api_base
        self.profile = get_profile(profile_key)
        self.session: AgentSession | None = None
        self._resume_from = resume_from
        self._was_resumed = resume_from is not None

    @property
    def _is_web_profile(self) -> bool:
        return self.profile_key in _WEB_PROFILES

    @property
    def _is_web_target(self) -> bool:
        return is_url(self.binary_path) if self.binary_path else False

    def compose(self) -> ComposeResult:
        yield Header()
        yield SelectableRichLog(id="chat-log", highlight=False, markup=True, wrap=True)
        with Horizontal(id="status-bar"):
            yield Static(id="status-text")
        with Vertical(id="input-container"):
            yield HistoryInput(placeholder="Message the agent... (or /help)", id="user-input")
        yield Footer()

    def on_mount(self) -> None:
        self._update_status()
        log = self.query_one("#chat-log", SelectableRichLog)
        backend_info = f" via [yellow]{self.backend_name}[/yellow]"
        if self.model:
            backend_info += f" ([yellow]{self.model}[/yellow])"
        log.write(f"[bold]Reverser Agent[/bold] — Profile: [cyan]{self.profile.name}[/cyan]{backend_info}")
        log.write("")

        # Wire emergency snapshot hooks (best-effort save on shutdown)
        _register_emergency_hooks(self)

        if self.binary_path:
            self._init_session()
            if self._is_web_target:
                log.write(f"Target: [green]{self.binary_path}[/green]")
            else:
                log.write(f"Binary loaded: [green]{self.binary_path}[/green]")
            log.write(f"Session log: {self.session.log_path}")

            # Surface session info + replay conversation if resumed
            snap = getattr(self.session, "_snapshot", None)
            if snap is not None and getattr(self, "_was_resumed", False):
                log.write(
                    f"[bold yellow]Resumed session[/] {snap.session_id} "
                    f"({snap.stats.turns} turns, ${snap.stats.total_cost:.2f} spent)"
                )
                if snap.conversation:
                    log.write(f"Replaying {len(snap.conversation)} prior exchanges...")
                    for entry in snap.conversation:
                        # Escape user/agent text — it's untrusted (it's literally
                        # the agent's output, which can contain [...] markers
                        # like our own "[... truncated]" tail) and Rich treats
                        # bare [xxx] as markup, silently dropping malformed tags.
                        log.write(
                            f"[bold]You ({entry.timestamp})[/]: "
                            f"{markup_escape(entry.user)}"
                        )
                        log.write(f"[bold]Agent[/]: {markup_escape(entry.agent)}")
                        log.write("")
                if snap.in_flight is not None:
                    log.write(
                        f"[bold yellow]⚠ Previous session was stopped during dispatch "
                        f"to '{snap.in_flight.specialty}' "
                        f"(hypothesis #{snap.in_flight.hypothesis_id}). "
                        f"Hypothesis status is still 'testing'.[/]"
                    )
                    log.write("")
            elif snap is not None:
                log.write(f"[dim]Session: {snap.session_id} (new)[/dim]")
                log.write("")
        else:
            if self._is_web_profile:
                log.write("No target set. Press [bold]F3[/bold] to set one, or enter a URL.")
            else:
                log.write("No binary loaded. Press [bold]F3[/bold] to load one, or enter a path.")

        log.write(
            "Press [bold]F1[/bold] for skills, [bold]F2[/bold] to change profile. "
            "Type [bold]/help[/bold] for commands."
        )
        log.write("")
        self.query_one("#user-input", HistoryInput).focus()

        # Auto-prompt for sudo password when starting with pentest profile
        if self.profile_key == "pentest":
            self.action_set_sudo()

    def _init_session(self):
        if self.session:
            self.session.close()
        self.profile = get_profile(self.profile_key)
        self.session = AgentSession(
            binary_path=self.binary_path,
            profile=self.profile,
            budget=self.budget,
            max_turns=self.max_turns,
            backend_name=self.backend_name,
            model=self.model,
            api_base=self.api_base,
            resume_from=self._resume_from,
        )
        # Resume snapshot is consumed; clear it so subsequent _init_session
        # calls (e.g. profile switch) construct a fresh session.
        self._resume_from = None
        self._update_status()

    def _update_status(self):
        try:
            status = self.query_one("#status-text", Static)
        except NoMatches:
            return

        if self._is_web_target:
            from urllib.parse import urlparse
            parsed = urlparse(self.binary_path)
            target_name = parsed.hostname or self.binary_path or "(none)"
            target_label = "Target"
        else:
            target_name = Path(self.binary_path).name if self.binary_path else "(none)"
            target_label = "Binary"

        if self.session:
            s = self.session.stats
            cost_str = f"${s.total_cost:.4f}" if s.total_cost else "$0.00"
            status.update(
                f"{target_label}: {target_name}  |  Profile: {self.profile.name}  |  "
                f"Turns: {s.turns}/{s.max_turns}  |  Cost: {cost_str}  |  "
                f"Budget: ${s.budget:.2f}"
            )
        else:
            status.update(
                f"{target_label}: {target_name}  |  Profile: {self.profile.name}  |  "
                f"Budget: ${self.budget:.2f}"
            )

    # ── Input handling ──────────────────────────────────────────────

    @on(Input.Submitted, "#user-input")
    async def on_user_input(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        input_widget = self.query_one("#user-input", HistoryInput)
        input_widget.add_to_history(text)
        event.input.clear()
        log = self.query_one("#chat-log", SelectableRichLog)

        # Handle commands
        if text.startswith("/"):
            self._handle_command(text, log)
            return

        # Handle bare path/URL input when no target is loaded
        if not self.binary_path:
            if is_url(text):
                self.binary_path = text
                self._init_session()
                log.write(f"Target set: [green]{self.binary_path}[/green]")
                log.write(f"Session log: {self.session.log_path}")
                log.write("")
                return
            elif Path(text).is_file():
                self.binary_path = str(Path(text).resolve())
                self._init_session()
                log.write(f"Binary loaded: [green]{self.binary_path}[/green]")
                log.write(f"Session log: {self.session.log_path}")
                log.write("")
                return

        # Need a target loaded
        if not self.session:
            if self._is_web_profile:
                log.write("[red]No target set. Press F3 or enter a URL.[/red]")
            else:
                log.write("[red]No binary loaded. Press F3 or enter a file path.[/red]")
            return

        if self.session.is_running:
            log.write("[yellow]Agent is busy. Wait for it to finish.[/yellow]")
            return

        # Send to agent
        log.write(f"\n[bold blue]You:[/bold blue] {text}")
        self._run_agent(text)

    def _handle_command(self, text: str, log: RichLog) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            log.write("[bold]Commands:[/bold]")
            log.write("  /help           — Show this help")
            log.write("  /profile <key>  — Switch profile (or F2)")
            log.write("  /load <path|url> — Load a binary or set target URL (or F3)")
            log.write("  /skills         — Show available skills (or F1)")
            log.write("  /budget <amt>   — Set budget in USD")
            log.write("  /turns <n>      — Set max turns")
            log.write("  /status         — Show session stats and snapshot state")
            log.write("  /sudo           — Set sudo password for privileged scans (or F4)")
            log.write("  /clear          — Clear the chat log (or F5)")
            log.write("  /cancel         — Cancel running agent")
            log.write("  /stop           — Stop session, save snapshot, exit (or F6)")
            log.write("  /done           — Mark session completed (terminal), exit")
            log.write("")
            log.write("[bold]Profiles:[/bold]")
            for p in list_profiles():
                log.write(f"  {p.key:10s} — {p.name}: {p.description}")
            log.write("")

        elif cmd == "/profile":
            if arg:
                self._switch_profile(arg, log)
            else:
                self.action_show_profiles()

        elif cmd == "/load":
            if arg:
                self._load_target(arg, log)
            else:
                self.action_load_binary()

        elif cmd == "/skills":
            self.action_show_skills()

        elif cmd == "/budget":
            try:
                self.budget = float(arg)
                log.write(f"Budget set to ${self.budget:.2f}")
                if self.binary_path:
                    self._init_session()
            except ValueError:
                log.write("[red]Usage: /budget <amount>[/red]")

        elif cmd == "/turns":
            try:
                self.max_turns = int(arg)
                log.write(f"Max turns set to {self.max_turns}")
                if self.binary_path:
                    self._init_session()
            except ValueError:
                log.write("[red]Usage: /turns <number>[/red]")

        elif cmd == "/status":
            if self.session:
                s = self.session.stats
                snap = getattr(self.session, "_snapshot", None)
                log.write(f"Binary: {s.binary_path}")
                log.write(f"Profile: {self.profile.name} ({self.profile_key})")
                log.write(f"Turns: {s.turns}/{s.max_turns}")
                log.write(f"Cost: ${s.total_cost:.4f} / ${s.budget:.2f}")
                log.write(f"Log: {self.session.log_path}")
                if snap is not None:
                    log.write(f"Session ID: {snap.session_id}")
                    log.write(f"State: {snap.state}")
                    log.write(f"Started: {snap.started_at}")
            else:
                log.write("No active session.")

        elif cmd == "/clear":
            self.action_clear_log()

        elif cmd == "/sudo":
            self.action_set_sudo()

        elif cmd == "/cancel":
            if self.session and self.session.is_running:
                self.session.cancel()
                log.write("[yellow]Cancelling...[/yellow]")
            else:
                log.write("Nothing to cancel.")

        elif cmd == "/stop":
            self.action_stop_session()

        elif cmd == "/done":
            self._mark_done()

        else:
            log.write(f"[red]Unknown command: {cmd}. Type /help for commands.[/red]")

    def _switch_profile(self, key: str, log: RichLog) -> None:
        from ..profiles import PROFILES
        from ..tools._common import get_sudo_password
        if key not in PROFILES:
            log.write(f"[red]Unknown profile: {key}[/red]")
            log.write(f"Available: {', '.join(PROFILES.keys())}")
            return
        self.profile_key = key
        self.profile = get_profile(key)
        log.write(f"Profile switched to: [cyan]{self.profile.name}[/cyan]")
        if self.binary_path:
            self._init_session()
            log.write(f"Session restarted with new profile.")
        # Auto-prompt for sudo password when switching to pentest profile
        if key == "pentest" and get_sudo_password() is None:
            self.action_set_sudo()

    def _load_target(self, path: str, log: RichLog) -> None:
        if is_url(path):
            self.binary_path = path
            self._init_session()
            log.write(f"Target set: [green]{self.binary_path}[/green]")
            log.write(f"Session log: {self.session.log_path}")
        else:
            resolved = Path(path).expanduser().resolve()
            if not resolved.is_file():
                log.write(f"[red]File not found: {path}[/red]")
                return
            self.binary_path = str(resolved)
            self._init_session()
            log.write(f"Binary loaded: [green]{self.binary_path}[/green]")
            log.write(f"Session log: {self.session.log_path}")

    # ── Agent execution ─────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_agent(self, message: str) -> None:
        log = self.query_one("#chat-log", SelectableRichLog)
        input_widget = self.query_one("#user-input", HistoryInput)
        input_widget.placeholder = "Agent is working..."
        input_widget.disabled = True

        try:
            async for event in self.session.send(message):
                self._handle_event(event, log)
                # Yield to the event loop after every event so Textual can
                # process scroll / key / mouse messages.  Without this the
                # UI freezes while the backend streams events in rapid bursts.
                await asyncio.sleep(0)
        except Exception as e:
            log.write(f"\n[red bold]Error:[/red bold] {markup_escape(str(e))}")
        finally:
            input_widget.disabled = False
            input_widget.placeholder = "Message the agent... (or /help)"
            input_widget.focus()
            self._update_status()

    def _handle_event(self, event: AgentEvent, log: RichLog) -> None:
        if event.kind == "text":
            if event.content.strip():
                log.write(markup_escape(event.content), shrink=False)

        elif event.kind == "thinking":
            if event.content.strip():
                log.write(f"[dim italic]thinking: {markup_escape(event.content)}[/dim italic]")

        elif event.kind == "tool_call":
            name = event.tool_name.replace("mcp__re__", "")
            log.write(f"[cyan bold]> {name}[/cyan bold] [dim]{markup_escape(event.tool_input)}[/dim]")

        elif event.kind == "tool_result":
            color = "red" if event.is_error else "green"
            log.write(f"[{color} dim]{markup_escape(event.content)}[/{color} dim]")

        elif event.kind == "turn":
            log.write(f"[dim]── turn {event.turns} ──[/dim]")
            self._update_status()

        elif event.kind == "result":
            if event.cost:
                log.write(f"\n[dim]Cost: ${event.cost:.4f}[/dim]")
            if event.subtype and event.subtype != "success":
                log.write(f"[yellow]Agent stopped: {event.subtype}[/yellow]")
            self._update_status()

        elif event.kind == "error":
            log.write(f"[red bold]Error:[/red bold] {markup_escape(event.content)}")

    # ── Actions ─────────────────────────────────────────────────────

    def action_show_skills(self) -> None:
        if not self.session:
            log = self.query_one("#chat-log", SelectableRichLog)
            if self._is_web_profile:
                log.write("[red]Set a target URL first (F3).[/red]")
            else:
                log.write("[red]Load a binary first (F3).[/red]")
            return

        async def handle_skill(key: str) -> None:
            if not key:
                return
            for skill in self.profile.skills:
                if skill.key == key:
                    log = self.query_one("#chat-log", SelectableRichLog)
                    log.write(f"\n[bold magenta]Skill:[/bold magenta] {skill.name}")
                    self._run_agent(skill.prompt)
                    return

        self.push_screen(SkillScreen(self.profile.skills), handle_skill)

    def action_show_profiles(self) -> None:
        async def handle_profile(key: str) -> None:
            if not key:
                return
            log = self.query_one("#chat-log", SelectableRichLog)
            self._switch_profile(key, log)

        self.push_screen(ProfileScreen(self.profile_key), handle_profile)

    def action_load_binary(self) -> None:
        async def handle_path(path: str) -> None:
            if not path:
                return
            log = self.query_one("#chat-log", SelectableRichLog)
            self._load_target(path, log)

        self.push_screen(LoadTargetScreen(self.binary_path), handle_path)

    def on_text_selected(self, event) -> None:
        """Auto-copy selected text to clipboard when mouse selection ends."""
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Copied to clipboard", severity="information", timeout=2)

    def action_clear_log(self) -> None:
        log = self.query_one("#chat-log", SelectableRichLog)
        log.clear()
        log.write(f"[bold]Reverser Agent[/bold] — Profile: [cyan]{self.profile.name}[/cyan]")
        if self.binary_path:
            label = "Target" if is_network_target(self.binary_path) else "Binary"
            log.write(f"{label}: [green]{self.binary_path}[/green]")
        log.write("")

    def action_set_sudo(self) -> None:
        from ..tools._common import set_sudo_password

        async def handle_password(password: str) -> None:
            if password:
                set_sudo_password(password)
                log = self.query_one("#chat-log", SelectableRichLog)
                log.write("[green]Sudo password set.[/green]")

        self.push_screen(SudoPasswordScreen(), handle_password)

    def action_stop_session(self) -> None:
        """F6 / /stop — confirm and stop the session."""
        from .modals import StopConfirmModal
        log = self.query_one("#chat-log", SelectableRichLog)
        if self.session is None:
            log.write("[yellow]No active session to stop.[/yellow]")
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.session.stop()
                log.write("[yellow]Session stopped and snapshot saved.[/yellow]")
                log.write(f"Resume later with: reverser i {self.session.target} --resume")
                self.exit()

        self.push_screen(StopConfirmModal(), on_confirm)

    def _mark_done(self) -> None:
        """/done — confirm and mark the session completed (terminal)."""
        from .modals import DoneConfirmModal
        log = self.query_one("#chat-log", SelectableRichLog)
        if self.session is None:
            log.write("[yellow]No active session to mark completed.[/yellow]")
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.session.mark_completed()
                log.write("[green]Session marked completed.[/green]")
                self.exit()

        self.push_screen(DoneConfirmModal(), on_confirm)


def _emergency_snapshot(session) -> None:
    """Best-effort save on interpreter shutdown — runs even on crash/SIGTERM.

    Also auto-marks zero-turn active sessions as `abandoned`, so launching
    the TUI repeatedly without sending any messages doesn't accumulate
    ghost "active" snapshots forever. Sessions in `stopped` / `completed`
    are left untouched.

    Called from atexit and SIGTERM signal handler. Catches all exceptions
    because we're shutting down; nothing useful we can do if save fails.
    """
    if session is None:
        return
    try:
        from datetime import datetime, timezone
        from ..sessions import save as save_snapshot

        snap = session._snapshot
        if snap.state == "active" and snap.stats.turns == 0:
            snap.state = "abandoned"
            snap.stopped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snap.pid = None
        save_snapshot(snap)
    except Exception:
        pass


def _register_emergency_hooks(app) -> None:
    """Wire atexit + SIGTERM to emergency_snapshot of the app's current session.

    Idempotent — safe to call multiple times.
    """
    atexit.register(lambda: _emergency_snapshot(getattr(app, "session", None)))

    def _sigterm_handler(*_args):
        _emergency_snapshot(getattr(app, "session", None))
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    signal.signal(signal.SIGTERM, _sigterm_handler)


def _patch_textual_utf8_decoder() -> None:
    """Patch Textual's Linux driver to tolerate malformed UTF-8 from stdin.

    Textual's ``run_input_thread`` creates a strict incremental UTF-8
    decoder via ``getincrementaldecoder("utf-8")()``.  During rapid
    terminal resizes the emulator can split a multi-byte escape sequence
    across reads, causing a ``UnicodeDecodeError`` that crashes the app.

    We swap the module-level ``getincrementaldecoder`` reference in
    ``textual.drivers.linux_driver`` with a wrapper that returns a
    ``errors="replace"`` decoder for UTF-8, so malformed bytes become
    U+FFFD instead of exceptions.
    """
    try:
        import textual.drivers.linux_driver as _ld
        from codecs import getincrementaldecoder as _orig

        def _tolerant_getincrementaldecoder(encoding):
            cls = _orig(encoding)
            if encoding.lower().replace("-", "") == "utf8":
                # Return a factory whose instances default to replace
                _real_cls = cls

                class _ReplaceDecoder(_real_cls):
                    def __init__(self, errors="replace"):
                        super().__init__(errors)

                return _ReplaceDecoder
            return cls

        _ld.getincrementaldecoder = _tolerant_getincrementaldecoder
    except Exception:
        pass  # Non-Linux or incompatible Textual version — skip silently


def run_tui(
    binary_path: str = "",
    profile: str = "general",
    budget: float = 5.0,
    max_turns: int = 50,
    backend: str = "claude",
    model: str | None = None,
    api_base: str | None = None,
    resume_from=None,  # SessionSnapshot | None
):
    """Launch the interactive TUI."""
    _patch_textual_utf8_decoder()
    app = ReverserApp(
        binary_path=binary_path,
        profile_key=profile,
        budget=budget,
        max_turns=max_turns,
        backend=backend,
        model=model,
        api_base=api_base,
        resume_from=resume_from,
    )
    app.run()
