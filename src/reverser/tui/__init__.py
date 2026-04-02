"""Interactive TUI for the reverser agent."""

# Work around a Textual bug where the XTerm parser crashes with
# ZeroDivisionError when the terminal reports pixel mouse events
# but returns zero for pixel dimensions (common in some terminals).
import textual._xterm_parser as _xp

_original_parse_mouse_code = _xp.XTermParser.parse_mouse_code


def _safe_parse_mouse_code(self, code):
    try:
        return _original_parse_mouse_code(self, code)
    except ZeroDivisionError:
        return None


_xp.XTermParser.parse_mouse_code = _safe_parse_mouse_code
