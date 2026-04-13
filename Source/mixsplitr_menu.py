"""
MixSplitR Menu System - prompt_toolkit based interactive menus
Provides arrow-key navigation, type-to-filter, and cross-platform support
"""

import sys
import os
import re
import textwrap
import time
import unicodedata
import webbrowser
from typing import List, Optional, Callable, Any, Tuple

# Debug mode: set MIXSPLITR_DEBUG=1 to see key input diagnostics
DEBUG_MODE = os.environ.get('MIXSPLITR_DEBUG', '').lower() in ('1', 'true', 'yes')


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a bool-like env var with a safe default."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'y', 'on')


# Keep mouse handling enabled by default for clickable menus.
# If a terminal shows flicker/blanking, disable with MIXSPLITR_MOUSE_UI=0.
MOUSE_UI_ENABLED = _env_flag('MIXSPLITR_MOUSE_UI', default=True)
MOUSE_HOVER_ENABLED = _env_flag('MIXSPLITR_MOUSE_HOVER', default=True)

try:
    from prompt_toolkit import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FormattedTextControl
    from prompt_toolkit.formatted_text import HTML, FormattedText
    from prompt_toolkit.styles import Style as PTStyle
    from prompt_toolkit.widgets import Box, Frame
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.utils import get_cwidth
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    MouseEventType = None
    get_app = None
    get_cwidth = None

# Import existing Style class for consistency
try:
    from mixsplitr_core import Style
except ImportError:
    class Style:
        RESET = '\033[0m'
        BOLD = '\033[1m'
        DIM = '\033[2m'
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        # Indigo-blue accent used for dynamic UI labels in ANSI fallback rendering
        MAGENTA = '\033[38;5;69m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'


class MenuItem:
    """Represents a single menu item"""
    def __init__(self, key: str, icon: str, title: str, description: str = "",
                 enabled: bool = True, action: Optional[Callable] = None,
                 visible: bool = True, show_number: bool = False):
        self.key = key
        self.icon = icon
        self.title = title
        self.description = description
        self.enabled = enabled
        self.action = action
        self.visible = visible
        self.show_number = bool(show_number)


class MenuResult:
    """Result from menu selection"""
    def __init__(self, key: str, cancelled: bool = False, text_input: str = ""):
        self.key = key
        self.cancelled = cancelled
        self.text_input = text_input  # For drag-drop or typed path


class InteractiveMenu:
    """
    Arrow-key navigable menu using prompt_toolkit
    Falls back to text input() if prompt_toolkit unavailable
    """

    def __init__(self, title: str, items: List[MenuItem],
                 subtitle: str = "", allow_text_input: bool = False,
                 text_input_hint: str = "", header_lines=None,
                 footer_lines=None, fallback_header: str = "",
                 fallback_footer: str = "", hotkeys: Optional[dict] = None,
                 paste_only_text_input: bool = False,
                 show_item_divider: bool = False,
                 animate_item_divider: bool = False,
                 wrap_selected_description: bool = False,
                 compact_rows: bool = False,
                 grid_columns: int = 1):
        self.title = title
        self.subtitle = subtitle
        self.items = [i for i in items if i.visible]
        self.allow_text_input = allow_text_input
        self.paste_only_text_input = bool(paste_only_text_input)
        self.text_input_hint = text_input_hint
        self.header_lines = header_lines or []  # FormattedText tuples for prompt_toolkit
        self.footer_lines = footer_lines or []  # FormattedText tuples shown near bottom
        self.fallback_header = fallback_header   # ANSI string for fallback mode
        self.fallback_footer = fallback_footer   # ANSI string shown near bottom in fallback
        self.hotkeys = hotkeys or {}
        self.show_item_divider = show_item_divider
        self.mouse_ui_enabled = MOUSE_UI_ENABLED
        self.mouse_hover_enabled = bool(self.mouse_ui_enabled and MOUSE_HOVER_ENABLED)
        # Windows Terminal can leave redraw artifacts with high-frequency animated text.
        self.animate_item_divider = bool(animate_item_divider and os.name != 'nt')
        self.wrap_selected_description = bool(wrap_selected_description)
        self.compact_rows = bool(compact_rows)
        self.grid_columns = max(1, int(grid_columns or 1))
        # Start without a highlighted row; selection appears on hover/click or
        # when the user navigates with keyboard arrows.
        self.selected_idx = -1
        # Per-item hover width (x cutoff) rebuilt every render.
        self._item_hit_min_x = {}
        self._item_hit_max_x = {}
        # Right-side padding (columns) added to the per-row hitbox.
        self._item_hit_padding = 2
        self.input_buffer = ""  # Unified buffer for all typed/pasted input
        self.result: Optional[MenuResult] = None
        self.subtitle_urls = self._extract_urls(subtitle)
        self.primary_subtitle_url = self.subtitle_urls[0] if self.subtitle_urls else None
        self._mouse_down_idx: Optional[int] = None
        # Swallow terminal ANSI escape sequences (arrows/mouse reports) so they
        # never leak into type-to-filter input.
        self._ansi_escape_mode = False
        # Handles terminals that emit arrow keys as "[" + "A/B/C/D" fragments.
        self._pending_bracket_escape = False

    def _drain_pending_input(self):
        """Best-effort flush of stale keystrokes before opening a new menu."""
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore
                while msvcrt.kbhit():
                    msvcrt.getwch()
                return
            if sys.stdin and getattr(sys.stdin, "isatty", lambda: False)():
                import termios
                termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except Exception:
            pass

    def _terminal_size(self) -> Tuple[int, int]:
        """Best-effort terminal size used for wrapping/spacing."""
        try:
            size = os.get_terminal_size()
            return max(60, size.columns), max(20, size.lines)
        except OSError:
            return 100, 30

    def _body_width(self) -> int:
        """Target content width for menu body text."""
        cols, _ = self._terminal_size()
        width = min(86, max(58, cols - 10))
        # If a wide logo/header block is present, keep body text within that visual width.
        if self.header_lines:
            width = min(width, 70)
        return width

    def _body_indent(self) -> str:
        """Left padding that keeps menu content visually centered."""
        cols, _ = self._terminal_size()
        target_width = self._body_width()
        pad = max(2, (cols - target_width) // 2)
        if self.header_lines:
            # Keep body tucked under the centered header block.
            pad = max(pad, 4)
        return " " * pad

    def _append_padded_fragments(self, target: List[tuple], fragments: List[tuple], pad: str):
        """Append formatted fragments while prefixing each rendered line with pad."""
        if not fragments:
            return
        at_line_start = True
        for frag in fragments:
            if len(frag) >= 3:
                style, text, handler = frag[0], frag[1], frag[2]
            else:
                style, text = frag[0], frag[1]
                handler = None
            text = "" if text is None else str(text)
            parts = text.split('\n')
            for idx, part in enumerate(parts):
                if at_line_start and part:
                    target.append(('', pad))
                if part:
                    if handler is not None:
                        target.append((style, part, handler))
                    else:
                        target.append((style, part))
                    at_line_start = False
                if idx < len(parts) - 1:
                    target.append(('', '\n'))
                    at_line_start = True

    def _print_padded_block(self, text_block: str, pad: str):
        """Print plain ANSI text block with consistent left padding per line."""
        if not text_block:
            return
        for line in text_block.splitlines():
            print(f"{pad}{line}")
        if text_block.endswith('\n'):
            print()

    def _wrap_text(self, text: str, width: int) -> List[str]:
        """Wrap text by terminal display width (safe for emoji/wide glyphs)."""
        if not text:
            return []
        width = max(16, width)
        wrapped_lines: List[str] = []
        # Preserve explicit line breaks from callers.
        for raw_line in text.splitlines():
            if not raw_line.strip():
                wrapped_lines.append("")
                continue
            current = ""
            for token in re.findall(r"\S+\s*", raw_line):
                candidate = f"{current}{token}" if current else token
                if self._display_width(candidate) <= width:
                    current = candidate
                    continue
                if current:
                    wrapped_lines.append(current.rstrip())
                    current = ""
                if self._display_width(token) <= width:
                    current = token.lstrip()
                    continue
                chunk = ""
                for ch in token:
                    next_chunk = f"{chunk}{ch}"
                    if chunk and self._display_width(next_chunk) > width:
                        wrapped_lines.append(chunk.rstrip())
                        chunk = ch
                    else:
                        chunk = next_chunk
                current = chunk
            if current:
                wrapped_lines.append(current.rstrip())
        return wrapped_lines or [text]

    def _fallback_cwidth(self, text: str) -> int:
        """Approximate terminal cell width when prompt_toolkit width helpers are unavailable."""
        width = 0
        for ch in text:
            code = ord(ch)
            if ch in ("\n", "\r"):
                continue
            if ch == "\t":
                width += 4
                continue
            if unicodedata.combining(ch):
                continue
            if 0xFE00 <= code <= 0xFE0F:
                # Variation selectors do not take extra terminal cells.
                continue
            if 0xE0100 <= code <= 0xE01EF:
                continue
            if unicodedata.category(ch) == "Cf":
                # Zero-width joiner and similar format chars.
                continue
            east = unicodedata.east_asian_width(ch)
            if east in ("W", "F"):
                width += 2
                continue
            if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF:
                # Most emoji/symbol blocks render double-width in terminal emulators.
                width += 2
                continue
            width += 1
        return width

    def _display_width(self, text: str) -> int:
        """Return terminal cell width for strings (handles wide glyphs/emoji)."""
        if not text:
            return 0
        if get_cwidth is not None:
            try:
                return max(0, int(get_cwidth(text)))
            except Exception:
                pass
        return self._fallback_cwidth(text)

    def _divider_width(self, cols: int, pad: str, body_width: int) -> int:
        """Compute divider width constrained to the menu body."""
        extra = 8 if self.animate_item_divider else 0
        target = body_width + extra
        return max(24, min(target, cols - len(pad) - 2))

    def _animated_divider_segments(self, width: int) -> List[Tuple[str, str]]:
        """Build a subtle animated waveform divider in gray + red."""
        if width <= 0:
            return []
        # Wider/slower waveform profile for calmer motion.
        wave = "▁▁▂▂▃▃▄▅▅▄▃▃▂▂▁▁"
        stretch = 2
        tick = int(time.monotonic() * 3)
        chars = [wave[((i // stretch) + tick) % len(wave)] for i in range(width)]
        swing = max(1, width - 1)
        step = int(time.monotonic() * 5) % (2 * swing)
        pos = step if step < width else (2 * swing - step)
        pos2 = width - 1 - pos

        segments: List[Tuple[str, str]] = []
        current_style = None
        current_text = ""
        for idx, ch in enumerate(chars):
            style = 'class:logo_r' if abs(idx - pos) <= 1 or abs(idx - pos2) <= 1 else 'class:logo_dim'
            if style != current_style and current_text:
                segments.append((current_style, current_text))
                current_text = ""
            current_style = style
            current_text += ch
        if current_text:
            segments.append((current_style, current_text))
        return segments

    def _is_divider(self, item: MenuItem) -> bool:
        """Return True if the item is a visual separator line."""
        return item.key.startswith("__divider__")

    def _extract_urls(self, text: str) -> List[str]:
        """Extract unique URLs from free-form subtitle text."""
        if not text:
            return []
        found = re.findall(r'https?://[^\s)]+', text)
        seen = set()
        urls = []
        for url in found:
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _open_url(self, url: str):
        """Best-effort browser open used by mouse and keyboard shortcuts."""
        if not url:
            return
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass

    def _build_url_mouse_handler(self, url: str):
        """Create a click handler for URL fragments."""
        def _handler(mouse_event):
            if not self.mouse_ui_enabled:
                return
            if (
                self.mouse_hover_enabled
                and MouseEventType
                and mouse_event.event_type == MouseEventType.MOUSE_MOVE
            ):
                self._clear_selection_on_mouse_off()
                return
            if MouseEventType and mouse_event.event_type == MouseEventType.MOUSE_UP:
                self._open_url(url)
        return _handler

    def _clear_selection_on_mouse_off(self):
        """Clear highlighted item when pointer moves away from menu options."""
        if self.selected_idx < 0 and self._mouse_down_idx is None:
            return
        self.selected_idx = -1
        self._mouse_down_idx = None
        try:
            app = get_app()
            app.invalidate()
        except Exception:
            pass

    def _build_clear_selection_mouse_handler(self):
        """Create hover handler for non-item fragments."""
        if not self.mouse_ui_enabled or not self.mouse_hover_enabled:
            return None

        def _handler(mouse_event):
            if not MouseEventType:
                return
            if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                self._clear_selection_on_mouse_off()

        return _handler

    def _build_item_mouse_handler(self, item_idx: int):
        """Create click handler for a menu row."""
        if not self.mouse_ui_enabled:
            return None

        def _handler(mouse_event):
            if not MouseEventType:
                return
            if mouse_event.event_type not in (
                MouseEventType.MOUSE_DOWN,
                MouseEventType.MOUSE_UP,
                MouseEventType.MOUSE_MOVE,
            ):
                return
            if item_idx < 0 or item_idx >= len(self.items):
                return

            event_x = None
            try:
                event_x = mouse_event.position.x
            except Exception:
                event_x = None
            hit_min_x = self._item_hit_min_x.get(item_idx)
            hit_max_x = self._item_hit_max_x.get(item_idx)
            if event_x is not None and hit_min_x is not None and event_x < hit_min_x:
                self._clear_selection_on_mouse_off()
                return
            if event_x is not None and hit_max_x is not None and event_x >= hit_max_x:
                self._clear_selection_on_mouse_off()
                return

            item = self.items[item_idx]
            if self._is_divider(item) or not item.enabled:
                return

            if mouse_event.event_type == MouseEventType.MOUSE_MOVE and not self.mouse_hover_enabled:
                return

            if mouse_event.event_type == MouseEventType.MOUSE_MOVE and self.selected_idx == item_idx:
                return

            # Preserve valid path input so accidental mouse hover does not
            # erase drag-and-drop text before Enter is pressed.
            had_input = bool(self.input_buffer)
            buffer_is_path = bool(
                self.input_buffer
                and self.allow_text_input
                and self._looks_like_path(self.input_buffer)
            )
            if had_input and not buffer_is_path:
                self.input_buffer = ""
            selection_changed = (self.selected_idx != item_idx)
            if selection_changed:
                self.selected_idx = item_idx

            if mouse_event.event_type == MouseEventType.MOUSE_MOVE and not selection_changed and not had_input:
                return

            try:
                app = get_app()
                if selection_changed or had_input:
                    app.invalidate()
            except Exception:
                app = None

            if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                return

            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                self._mouse_down_idx = item_idx
                return

            # Mouse up: activate the hovered/pressed item.
            if self._mouse_down_idx is not None and self._mouse_down_idx != item_idx:
                self._mouse_down_idx = None
                return
            self._mouse_down_idx = None

            self.result = MenuResult(key=item.key)
            try:
                if app is None:
                    app = get_app()
                app.exit()
            except Exception:
                pass
        return _handler

    def _get_filtered_items(self) -> List[Tuple[int, MenuItem]]:
        """Return visible menu items (keyboard filtering is disabled)."""
        return list(enumerate(self.items))

    def _looks_like_path(self, text: str) -> bool:
        """Check if text looks like a file path"""
        import os
        text = text.strip()
        # Strip quotes that terminals often add
        if (text.startswith('"') and text.endswith('"')) or \
           (text.startswith("'") and text.endswith("'")):
            text = text[1:-1]
        # Handle macOS escaped spaces (remove backslashes before spaces)
        cleaned = text.replace('\\ ', ' ')
        return (
            cleaned.startswith(('/', '\\', '~')) or  # Unix/Windows paths (incl single backslash)
            (len(cleaned) > 2 and cleaned[1] == ':') or  # Windows drive (C:\)
            os.path.exists(cleaned) or  # Actually exists
            ('/' in cleaned and len(cleaned) > 3) or  # Contains forward-slash path separator
            ('\\' in cleaned and len(cleaned) > 3)  # Contains backslash path separator
        )

    def show(self) -> MenuResult:
        """Display menu and return selection"""
        self._drain_pending_input()
        if PROMPT_TOOLKIT_AVAILABLE:
            return self._show_interactive()
        else:
            return self._show_fallback()

    def _show_interactive(self) -> MenuResult:
        """prompt_toolkit based interactive menu"""
        kb = KeyBindings()
        from prompt_toolkit.keys import Keys

        def _accept_path_from_buffer(app) -> bool:
            """Auto-submit a valid dropped/pasted path."""
            if not self.allow_text_input or not self.input_buffer:
                return False
            text = self.input_buffer.strip()
            if (text.startswith('"') and text.endswith('"')) or \
               (text.startswith("'") and text.endswith("'")):
                text = text[1:-1]
            cleaned_path = text.replace('\\ ', ' ')
            if not self._looks_like_path(text):
                return False
            self.result = MenuResult(key="__path__", text_input=cleaned_path)
            app.exit()
            return True

        def _selectable_filtered_positions(filtered_items: List[Tuple[int, MenuItem]]) -> List[int]:
            return [
                pos for pos, (_orig_idx, item) in enumerate(filtered_items)
                if item.enabled and not self._is_divider(item)
            ]

        def _selected_position(filtered_items: List[Tuple[int, MenuItem]], selectable_positions: List[int]) -> int:
            for sel_pos, filtered_pos in enumerate(selectable_positions):
                if filtered_items[filtered_pos][0] == self.selected_idx:
                    return sel_pos
            return -1

        def _set_selected_from_position(filtered_items: List[Tuple[int, MenuItem]],
                                        selectable_positions: List[int],
                                        sel_pos: int) -> bool:
            if 0 <= sel_pos < len(selectable_positions):
                filtered_pos = selectable_positions[sel_pos]
                self.selected_idx = filtered_items[filtered_pos][0]
                return True
            return False

        @kb.add('up')
        @kb.add('k')  # vim-style
        def move_up(event):
            filtered = self._get_filtered_items()
            if not filtered:
                return

            selectable_positions = _selectable_filtered_positions(filtered)
            if not selectable_positions:
                return

            current_pos = _selected_position(filtered, selectable_positions)
            if current_pos < 0:
                _set_selected_from_position(filtered, selectable_positions, len(selectable_positions) - 1)
                return

            step = self.grid_columns if self.grid_columns > 1 else 1
            _set_selected_from_position(filtered, selectable_positions, current_pos - step)

        @kb.add('down')
        @kb.add('j')  # vim-style
        def move_down(event):
            filtered = self._get_filtered_items()
            if not filtered:
                return

            selectable_positions = _selectable_filtered_positions(filtered)
            if not selectable_positions:
                return

            current_pos = _selected_position(filtered, selectable_positions)
            if current_pos < 0:
                _set_selected_from_position(filtered, selectable_positions, 0)
                return

            step = self.grid_columns if self.grid_columns > 1 else 1
            _set_selected_from_position(filtered, selectable_positions, current_pos + step)

        @kb.add('enter')
        def select(event):
            if DEBUG_MODE:
                import sys
                print(f"\n[DEBUG] Enter pressed. input_buffer={repr(self.input_buffer)}", file=sys.stderr)
                print(f"[DEBUG] allow_text_input={self.allow_text_input}", file=sys.stderr)

            # Check if input looks like a path
            if self.allow_text_input and self.input_buffer:
                text = self.input_buffer.strip()
                # Strip quotes
                if (text.startswith('"') and text.endswith('"')) or \
                   (text.startswith("'") and text.endswith("'")):
                    text = text[1:-1]
                # Handle macOS escaped spaces
                cleaned_path = text.replace('\\ ', ' ')

                is_path = self._looks_like_path(text)
                if DEBUG_MODE:
                    print(f"[DEBUG] _looks_like_path({repr(text)}) = {is_path}", file=sys.stderr)

                if is_path:
                    self.result = MenuResult(key="__path__", text_input=cleaned_path)
                    event.app.exit()
                    return

            # Regular menu selection
            filtered = self._get_filtered_items()
            if filtered and 0 <= self.selected_idx < len(self.items):
                item = self.items[self.selected_idx]
                if item.enabled and not self._is_divider(item):
                    if DEBUG_MODE:
                        print(f"[DEBUG] Selecting menu item: {item.key}", file=sys.stderr)
                    self.result = MenuResult(key=item.key)
                    event.app.exit()

        @kb.add('escape')
        @kb.add('q')
        def cancel(event):
            self.result = MenuResult(key="", cancelled=True)
            event.app.exit()

        @kb.add('c-c')
        def ctrl_c(event):
            self.result = MenuResult(key="", cancelled=True)
            event.app.exit()

        @kb.add('c-o')
        def open_primary_link(event):
            if self.primary_subtitle_url:
                self._open_url(self.primary_subtitle_url)

        @kb.add('left')
        def move_left(event):
            if self.grid_columns <= 1:
                return
            filtered = self._get_filtered_items()
            selectable_positions = _selectable_filtered_positions(filtered)
            if not selectable_positions:
                return
            current_pos = _selected_position(filtered, selectable_positions)
            if current_pos > 0:
                _set_selected_from_position(filtered, selectable_positions, current_pos - 1)

        @kb.add('right')
        def move_right(event):
            if self.grid_columns <= 1:
                return
            filtered = self._get_filtered_items()
            selectable_positions = _selectable_filtered_positions(filtered)
            if not selectable_positions:
                return
            current_pos = _selected_position(filtered, selectable_positions)
            if current_pos >= 0:
                _set_selected_from_position(filtered, selectable_positions, current_pos + 1)

        # Optional per-menu hotkeys (kept local to callers that opt in).
        for hotkey, spec in self.hotkeys.items():
            result_key = spec.get("result_key", "")
            use_selected = bool(spec.get("use_selected_key", False))

            @kb.add(hotkey)
            def _menu_hotkey(event, result_key=result_key, use_selected=use_selected):
                selected_key = ""
                if use_selected and 0 <= self.selected_idx < len(self.items):
                    selected_key = self.items[self.selected_idx].key
                self.result = MenuResult(key=result_key, text_input=selected_key)
                event.app.exit()

        # Handle bracketed paste (drag-and-drop, Cmd+V)
        @kb.add(Keys.BracketedPaste)
        def on_paste(event):
            pasted = event.data
            if DEBUG_MODE:
                print(f"[DEBUG] BracketedPaste: {repr(pasted)}", file=sys.stderr)
            # Add pasted content to input buffer
            for c in pasted:
                if c.isprintable() or c == ' ':
                    self.input_buffer += c
            if DEBUG_MODE:
                print(f"[DEBUG] After paste: input_buffer={repr(self.input_buffer)}", file=sys.stderr)
            _accept_path_from_buffer(event.app)

        # Type-to-filter (and path input)
        @kb.add('<any>')
        def on_key(event):
            char = event.data
            try:
                key_name = event.key_sequence[0].key if event.key_sequence else None
            except Exception:
                key_name = None
            if DEBUG_MODE:
                print(f"[DEBUG] Key: {repr(char)} (len={len(char)}, printable={char.isprintable() if len(char)==1 else 'N/A'})", file=sys.stderr)

            if not char:
                return

            # Mouse packets can arrive through <any> in some terminals; ignore them.
            if key_name == Keys.Vt100MouseEvent:
                return

            # Disable keyboard filtering. Only capture typed text when this menu
            # explicitly allows path/text input.
            if not self.allow_text_input:
                return

            if char == '\x1b':
                self._ansi_escape_mode = True
                self._pending_bracket_escape = False
                return

            if self._ansi_escape_mode:
                # Consume the full escape payload; terminate on common final byte.
                if len(char) > 1:
                    if re.fullmatch(r'\x1b?\[[0-9;<>]*[A-Za-z~]', char):
                        self._ansi_escape_mode = False
                    return
                if char.isalpha() or char in "~":
                    self._ansi_escape_mode = False
                return

            # Ignore common ANSI escape payloads from arrow/navigation keys.
            if len(char) > 1 and re.fullmatch(r'\x1b?\[[0-9;<>]*[A-Za-z~]', char):
                return

            # Handle multi-character input (fallback for non-bracketed paste)
            if len(char) > 1:
                # Ignore control/mouse escape payloads to prevent filter corruption.
                if '\x1b' in char:
                    return
                if re.search(r'[\x00-\x1f]', char):
                    return
                if '[<' in char and ';' in char:
                    return
                if not all(c.isalnum() or c in " _-./\\:~()[]{}@,+#" for c in char):
                    return
                for c in char:
                    if c.isprintable() or c == ' ':
                        self.input_buffer += c
                if DEBUG_MODE:
                    print(f"[DEBUG] After multi-char: input_buffer={repr(self.input_buffer)}", file=sys.stderr)
                _accept_path_from_buffer(event.app)
                return

            # Main-menu UX can opt into paste-only path input: block single-char typing.
            if self.paste_only_text_input:
                return

            if char == '[':
                self._pending_bracket_escape = True
                return

            if self._pending_bracket_escape:
                # Swallow any fragmented escape tail (arrows/mouse payload pieces).
                self._pending_bracket_escape = False
                return

            if char.isprintable() and len(char) == 1:
                if not (char.isalnum() or char in " _-./\\:~()[]{}@,+#"):
                    return
                self.input_buffer += char
                # Keep text input reserved for actual paths; drop stray fragments.
                if self.allow_text_input and len(self.input_buffer) > 2 and not self._looks_like_path(self.input_buffer):
                    self.input_buffer = ""

        @kb.add('backspace')
        def backspace(event):
            if self.input_buffer:
                self.input_buffer = self.input_buffer[:-1]

        def get_menu_content():
            lines = []
            clear_selection_handler = self._build_clear_selection_mouse_handler()
            self._item_hit_min_x = {}
            self._item_hit_max_x = {}
            cols, rows = self._terminal_size()
            pad = self._body_indent()
            pad_width = self._display_width(pad)
            body_width = self._body_width()
            # Keep one-column breathing room at the right edge to prevent
            # accidental soft-wrap artifacts during hover repaints.
            right_edge = max(24, cols - 2)
            subtitle_width = max(24, min(right_edge - pad_width, body_width))
            line_target_width = max(pad_width + 8, min(right_edge, pad_width + body_width))
            item_gap = '' if self.compact_rows else ('\n' if rows >= 30 else '')
            auto_show_divider = bool(self.title.strip() and self.subtitle.strip())
            show_divider = self.show_item_divider or auto_show_divider

            # Static header (logo) if provided
            if self.header_lines:
                self._append_padded_fragments(lines, self.header_lines, pad)

            # Title
            if self.title.strip():
                lines.append(('class:title', f"{pad}{self.title}\n"))
            if self.subtitle:
                for subtitle_line in self.subtitle.splitlines():
                    count_match = re.match(r'^\s*(\d+)(\s+audio file\(s\)\s+loaded)\s*$', subtitle_line, re.IGNORECASE)
                    if count_match:
                        count_suffix = " audio file(s) loaded"
                        lines.append(('class:subtitle', pad))
                        lines.append(('class:logo_r', count_match.group(1)))
                        lines.append(('class:subtitle', f"{count_suffix}\n"))
                        continue
                    stripped = subtitle_line.strip()
                    if stripped.startswith('http://') or stripped.startswith('https://'):
                        lines.append(('class:subtitle', pad))
                        lines.append(('class:link', stripped, self._build_url_mouse_handler(stripped)))
                        lines.append(('class:subtitle', '\n'))
                    else:
                        for wrapped in self._wrap_text(subtitle_line, subtitle_width):
                            lines.append(('class:subtitle', f"{pad}{wrapped}\n"))

            # Input indicator (path or filter)
            is_path_mode = self.input_buffer and self._looks_like_path(self.input_buffer)
            if self.input_buffer:
                if is_path_mode:
                    for wrapped in self._wrap_text(f"Path: {self.input_buffer}", subtitle_width):
                        lines.append(('class:input', f"{pad}{wrapped}\n"))
                    lines.append(('', '\n'))
                else:
                    for wrapped in self._wrap_text(f"Input: {self.input_buffer}", subtitle_width):
                        lines.append(('class:filter', f"{pad}{wrapped}\n"))
                    lines.append(('', '\n'))

            # Always show menu rows; avoid hide/flicker if terminals emit noisy input.
            show_items = True
            if show_divider and show_items:
                divider_width = self._divider_width(cols, pad, body_width)
                lines.append(('class:logo_dim', pad))
                if self.animate_item_divider:
                    lines.extend(self._animated_divider_segments(divider_width))
                else:
                    lines.append(('class:logo_dim', '─' * divider_width))
                lines.append(('class:logo_dim', '\n'))
                lines.append(('', '\n'))

            if show_items:
                filtered = self._get_filtered_items()
                display_num = 0
                min_grid_width = (self.grid_columns * 18) + ((self.grid_columns - 1) * 4)
                use_grid = (
                    self.grid_columns > 1
                    and (line_target_width - pad_width) >= min_grid_width
                )

                if use_grid:
                    numbered_total = sum(
                        1
                        for _, item in filtered
                        if (not self._is_divider(item)) and getattr(item, "show_number", True)
                    )
                    has_number_column = numbered_total > 0
                    num_width = len(str(numbered_total)) if has_number_column else 0
                    grid_sections = []
                    current_section = []
                    for orig_idx, item in filtered:
                        if self._is_divider(item):
                            if current_section:
                                grid_sections.append(("items", current_section))
                                current_section = []
                            grid_sections.append(("divider", item.title))
                            continue
                        item_number = None
                        if getattr(item, "show_number", True):
                            display_num += 1
                            item_number = display_num
                        current_section.append((orig_idx, item, item_number))
                    if current_section:
                        grid_sections.append(("items", current_section))

                    if grid_sections:
                        content_width = max(24, line_target_width - pad_width)
                        gutter_width = 4
                        selected_item = None

                        for section_type, payload in grid_sections:
                            if section_type == "divider":
                                divider_title = (payload or "").strip()
                                if not divider_title or all(ch in "─-" for ch in divider_title):
                                    divider_title = "─" * max(24, min(content_width, body_width))
                                lines.append(('class:logo_dim', f"{pad}{divider_title}\n"))
                                lines.append(('', '\n'))
                                continue

                            grid_items = payload
                            item_groups = []
                            numbered_entries = [entry for entry in grid_items if entry[2] is not None]
                            unnumbered_entries = [entry for entry in grid_items if entry[2] is None]
                            if numbered_entries:
                                item_groups.append(numbered_entries)
                            if unnumbered_entries:
                                item_groups.append(unnumbered_entries)

                            for group_items in item_groups:
                                col_count = min(self.grid_columns, max(1, len(group_items)))
                                col_width = max(18, (content_width - (gutter_width * (col_count - 1))) // col_count)
                                row_content_width = (col_width * col_count) + (gutter_width * (col_count - 1))
                                row_tail_fill = max(0, line_target_width - (pad_width + row_content_width))

                                for row_start in range(0, len(group_items), col_count):
                                    row_items = group_items[row_start:row_start + col_count]
                                    lines.append(('', pad))
                                    cursor_x = pad_width

                                    for col_idx in range(col_count):
                                        row_entry = row_items[col_idx] if col_idx < len(row_items) else None
                                        if row_entry is None:
                                            lines.append(('', ' ' * col_width))
                                            if col_idx < col_count - 1:
                                                lines.append(('', ' ' * gutter_width))
                                                cursor_x += gutter_width
                                            cursor_x += col_width
                                            continue

                                        orig_idx, item, item_number = row_entry
                                        is_selected = (orig_idx == self.selected_idx)
                                        if is_selected:
                                            selected_item = item
                                        style = 'class:selected' if is_selected else ('class:item' if item.enabled else 'class:disabled')
                                        item_mouse_handler = self._build_item_mouse_handler(orig_idx) if item.enabled else None

                                        marker = '\u25b6' if is_selected else ' '
                                        icon_width = self._display_width(item.icon)
                                        icon_pad = ' ' * max(1, 2 - icon_width)
                                        if has_number_column and item_number is not None:
                                            num_label = f"{item_number:>{num_width}}."
                                        elif has_number_column:
                                            num_label = " " * (num_width + 1)
                                        else:
                                            num_label = ""
                                        if num_label:
                                            cell_prefix = f"{marker} {num_label} {item.icon}{icon_pad} "
                                        else:
                                            cell_prefix = f"{marker} {item.icon}{icon_pad} "
                                        title_width = max(10, col_width - self._display_width(cell_prefix))
                                        short_title = textwrap.shorten(item.title, width=title_width, placeholder="...")
                                        cell_text = f"{cell_prefix}{short_title}"
                                        trailing_pad = max(0, col_width - self._display_width(cell_text))

                                        if item_mouse_handler:
                                            lines.append((style, cell_text, item_mouse_handler))
                                            if trailing_pad:
                                                lines.append((style, ' ' * trailing_pad, item_mouse_handler))
                                        else:
                                            lines.append((style, f"{cell_text}{' ' * trailing_pad}"))

                                        if item.enabled:
                                            self._item_hit_min_x[orig_idx] = min(max(0, cols - 1), cursor_x)
                                            self._item_hit_max_x[orig_idx] = min(
                                                max(0, cols - 1),
                                                cursor_x + col_width + self._item_hit_padding,
                                            )

                                        cursor_x += col_width
                                        if col_idx < col_count - 1:
                                            lines.append(('', ' ' * gutter_width))
                                            cursor_x += gutter_width

                                    if row_tail_fill:
                                        lines.append(('', ' ' * row_tail_fill))
                                    lines.append(('', '\n'))
                                    if row_start + col_count < len(group_items):
                                        lines.append(('', pad))
                                        for col_idx in range(col_count):
                                            row_entry = row_items[col_idx] if col_idx < len(row_items) else None
                                            if row_entry is None:
                                                lines.append(('', ' ' * col_width))
                                            else:
                                                orig_idx, item, _item_number = row_entry
                                                spacer_style = (
                                                    'class:selected' if orig_idx == self.selected_idx
                                                    else ('class:item' if item.enabled else 'class:disabled')
                                                )
                                                item_mouse_handler = self._build_item_mouse_handler(orig_idx) if item.enabled else None
                                                if item_mouse_handler:
                                                    lines.append((spacer_style, ' ' * col_width, item_mouse_handler))
                                                else:
                                                    lines.append((spacer_style, ' ' * col_width))
                                            if col_idx < col_count - 1:
                                                lines.append(('', ' ' * gutter_width))
                                        if row_tail_fill:
                                            lines.append(('', ' ' * row_tail_fill))
                                        lines.append(('', '\n'))

                        if selected_item and selected_item.description:
                            desc_width = max(18, min(content_width, body_width))
                            if self.wrap_selected_description:
                                desc_lines = self._wrap_text(selected_item.description, desc_width) or [""]
                            else:
                                desc_lines = [textwrap.shorten(selected_item.description, width=desc_width, placeholder="...")]
                            for desc_text in desc_lines:
                                lines.append(('class:description', f"{pad}    {desc_text}\n"))
                        elif not self.compact_rows:
                            lines.append(('class:description', f"{pad}\n"))
                else:
                    for orig_idx, item in filtered:
                        is_selected = (orig_idx == self.selected_idx)

                        if self._is_divider(item):
                            lines.append(('class:disabled', f"{pad}{item.title}\n"))
                            continue

                        item_number = None
                        if getattr(item, "show_number", True):
                            display_num += 1
                            item_number = display_num

                        if is_selected:
                            prefix = f'{pad}  \u25b6 '
                            style = 'class:selected'
                        else:
                            prefix = f'{pad}    '
                            style = 'class:item' if item.enabled else 'class:disabled'
                        item_mouse_handler = self._build_item_mouse_handler(orig_idx) if item.enabled else None
                        max_hit_text_len = 0

                        # Main line with item icon/title (wrapped if needed)
                        item_number_label = f"{item_number}. " if item_number is not None else ""
                        item_prefix = f"{prefix}{item_number_label}{item.icon}  "
                        item_prefix_width = self._display_width(item_prefix)
                        title_body_cap = body_width - max(0, item_prefix_width - pad_width)
                        title_width_cap = min(right_edge - item_prefix_width, title_body_cap)
                        title_width = max(8, title_width_cap)
                        title_lines = self._wrap_text(item.title, title_width)
                        first_title = f"{item_prefix}{title_lines[0]}"
                        max_hit_text_len = max(max_hit_text_len, self._display_width(first_title))
                        if item_mouse_handler:
                            lines.append((style, first_title, item_mouse_handler))
                            trailing_pad = max(0, line_target_width - self._display_width(first_title))
                            if trailing_pad:
                                lines.append(('', ' ' * trailing_pad))
                            lines.append(('', "\n"))
                        else:
                            lines.append((style, f"{first_title}\n"))
                        continuation_prefix = " " * item_prefix_width
                        for cont in title_lines[1:]:
                            cont_text = f"{continuation_prefix}{cont}"
                            max_hit_text_len = max(max_hit_text_len, self._display_width(cont_text))
                            if item_mouse_handler:
                                lines.append((style, cont_text, item_mouse_handler))
                                trailing_pad = max(0, line_target_width - self._display_width(cont_text))
                                if trailing_pad:
                                    lines.append(('', ' ' * trailing_pad))
                                lines.append(('', "\n"))
                            else:
                                lines.append((style, f"{cont_text}\n"))

                        # Show description below each row. When wrap_selected_description
                        # is enabled, selected rows can expand to multiple wrapped lines.
                        desc_prefix = f"{pad}      "
                        desc_prefix_width = self._display_width(desc_prefix)
                        desc_body_cap = body_width - max(0, desc_prefix_width - pad_width)
                        desc_width_cap = min(right_edge - desc_prefix_width, desc_body_cap)
                        desc_width = max(8, desc_width_cap)
                        desc_lines = []
                        if is_selected and item.description:
                            if self.wrap_selected_description:
                                desc_lines = self._wrap_text(item.description, max(12, desc_width)) or [""]
                            else:
                                desc_lines = [
                                    textwrap.shorten(
                                        item.description,
                                        width=max(12, desc_width),
                                        placeholder="...",
                                    )
                                ]
                        elif not self.compact_rows:
                            desc_lines = [""]
                        for desc_text in desc_lines:
                            desc_line = f"{desc_prefix}{desc_text}"
                            if item_mouse_handler:
                                lines.append(('class:description', desc_line, item_mouse_handler))
                            else:
                                lines.append(('class:description', desc_line))
                            trailing_pad = max(0, line_target_width - self._display_width(desc_line))
                            if trailing_pad:
                                lines.append(('', ' ' * trailing_pad))
                            lines.append(('', "\n"))
                        if item.enabled:
                            self._item_hit_min_x[orig_idx] = min(
                                max(0, cols - 1),
                                pad_width + 2,
                            )
                            self._item_hit_max_x[orig_idx] = min(
                                max(0, cols - 1),
                                max_hit_text_len + self._item_hit_padding,
                            )
                        if item_gap:
                            lines.append(('', ' ' * line_target_width))
                            lines.append(('', item_gap))

            # Text input hint
            if self.allow_text_input and not self.input_buffer:
                lines.append(('', '\n'))
                hint_lines = self.text_input_hint.splitlines() if self.text_input_hint else []
                if not hint_lines:
                    hint_lines = [""]
                for hint_line in hint_lines:
                    stripped_hint = hint_line.strip()
                    if not stripped_hint:
                        lines.append(('', '\n'))
                        continue
                    if stripped_hint.lower() == "__hint_divider__":
                        divider_width = self._divider_width(cols, pad, body_width)
                        lines.append(('class:logo_dim', pad))
                        lines.append(('class:logo_dim', '─' * divider_width))
                        lines.append(('class:logo_dim', '\n'))
                        continue
                    if stripped_hint.lower().startswith("__hint_red__"):
                        red_text = stripped_hint[len("__hint_red__"):].strip()
                        if not red_text:
                            continue
                        for wrapped in self._wrap_text(red_text, subtitle_width):
                            lines.append(('class:logo_r', f"{pad}{wrapped}\n"))
                        continue
                    for wrapped in self._wrap_text(hint_line, subtitle_width):
                        lines.append(('class:hint', f"{pad}{wrapped}\n"))

            # Help
            lines.append(('', '\n\n'))
            if self.grid_columns > 1:
                help_line = '↑/↓/←/→ Navigate  Enter Select  Esc Cancel'
            else:
                help_line = '↑/↓ Navigate  Enter Select  Esc Cancel'
            if self.allow_text_input:
                help_line += '  Paste Path' if self.paste_only_text_input else '  Type/Paste Path'
            if self.primary_subtitle_url:
                help_line += '  Ctrl+O Open Link'
            for hotkey, spec in self.hotkeys.items():
                help_label = spec.get("help")
                if help_label:
                    help_line += f"  {help_label}"
            lines.append(('class:help', f"{pad}{help_line}"))
            if self.footer_lines:
                lines.append(('', '\n'))
                lines.append(('', pad))
                lines.extend(self.footer_lines)

            if clear_selection_handler:
                clearable_lines = []
                for frag in lines:
                    # Preserve explicit handlers (menu rows, links, clickable footer).
                    if len(frag) >= 3:
                        clearable_lines.append(frag)
                        continue
                    frag_style, frag_text = frag
                    clearable_lines.append((frag_style, frag_text, clear_selection_handler))
                lines = clearable_lines

            return FormattedText(lines)

        style = PTStyle.from_dict({
            'title': 'bold #c6cad6',
            'subtitle': '#9ba1b3',
            'selected': 'bold bg:#252938 #ecefff',
            'item': '#c2c7d8',
            'disabled': '#666d82',
            'description': '#8e95aa italic',
            'filter': 'bold #8e95aa',
            'input': 'bold #8e95aa',
            'hint': '#72788d',
            'help': '#777d92',
            'link': '#8e95aa',
            'link_red': 'bold #ff0000',
            'logo': 'bold #c7ccd9',
            'logo_mix': 'bold #6c6c6c',
            'logo_split': 'bold #6c6c6c',
            'logo_r': 'bold #ff0000',
            'logo_accent': '#6c6c6c',
            'logo_dim': '#6c6c6c',
            'logo_keyword': 'bold #8e95aa',
        })

        layout = Layout(
            Window(
                content=FormattedTextControl(
                    get_menu_content,
                    focusable=True,
                    show_cursor=False,
                ),
                always_hide_cursor=True,
                wrap_lines=False,
            )
        )

        app_kwargs = dict(
            layout=layout,
            key_bindings=kb,
            style=style,
            # Fullscreen improves mouse packet handling in many terminals.
            full_screen=self.mouse_ui_enabled,
            mouse_support=self.mouse_ui_enabled,
        )
        if self.animate_item_divider:
            app_kwargs['refresh_interval'] = 0.1
        try:
            app = Application(**app_kwargs)
        except TypeError:
            app_kwargs.pop('refresh_interval', None)
            app = Application(**app_kwargs)

        # Avoid manual clear in alternate-screen mode; it can cause initial blank
        # frames in some terminals before the first repaint.
        if not app_kwargs.get('full_screen'):
            print('\033[2J\033[H', end='')

        def _prime_render():
            try:
                app.invalidate()
            except Exception:
                pass

        app.run(pre_run=_prime_render)

        # Check for path input (fallback if enter wasn't pressed properly)
        if self.input_buffer and self.result is None:
            if self._looks_like_path(self.input_buffer):
                cleaned = self.input_buffer.strip().replace('\\ ', ' ')
                if (cleaned.startswith('"') and cleaned.endswith('"')) or \
                   (cleaned.startswith("'") and cleaned.endswith("'")):
                    cleaned = cleaned[1:-1]
                return MenuResult(key="__path__", text_input=cleaned)

        return self.result or MenuResult(key="", cancelled=True)

    def _show_fallback(self) -> MenuResult:
        """Fallback menu when prompt_toolkit unavailable"""
        cols, rows = self._terminal_size()
        pad = self._body_indent()
        body_width = self._body_width()
        subtitle_width = max(26, min(cols - len(pad) - 2, body_width))
        item_gap = (rows >= 30) and (not self.compact_rows)
        auto_show_divider = bool(self.title.strip() and self.subtitle.strip())
        show_divider = self.show_item_divider or auto_show_divider

        # Print static header (logo) if provided
        if self.fallback_header:
            self._print_padded_block(self.fallback_header, pad)

        if self.title.strip():
            print(f"{pad}{Style.BOLD}{self.title}{Style.RESET}")
        if self.subtitle:
            for subtitle_line in self.subtitle.splitlines():
                count_match = re.match(r'^\s*(\d+)(\s+audio file\(s\)\s+loaded)\s*$', subtitle_line, re.IGNORECASE)
                if count_match:
                    count_suffix = " audio file(s) loaded"
                    print(
                        f"{pad}{Style.BOLD}\033[38;5;196m{count_match.group(1)}"
                        f"{Style.RESET}{Style.DIM}{count_suffix}{Style.RESET}"
                    )
                    continue
                for wrapped in self._wrap_text(subtitle_line, subtitle_width):
                    print(f"{pad}{Style.DIM}{wrapped}{Style.RESET}")
        print()
        if show_divider:
            divider_width = self._divider_width(cols, pad, body_width)
            print(f"{pad}{Style.DIM}{'─' * divider_width}{Style.RESET}")
            print()

        visible_items = [i for i in self.items if i.visible]
        selectable_items = []
        min_grid_width = (self.grid_columns * 18) + ((self.grid_columns - 1) * 4)
        use_grid = (
            self.grid_columns > 1
            and (body_width >= min_grid_width)
        )

        if use_grid:
            numbered_total = sum(
                1 for item in visible_items
                if (not self._is_divider(item)) and getattr(item, "show_number", True)
            )
            has_number_column = numbered_total > 0
            num_width = len(str(numbered_total)) if has_number_column else 0
            display_num = 0
            sections = []
            section_items = []
            for item in visible_items:
                if self._is_divider(item):
                    if section_items:
                        sections.append(("items", section_items))
                        section_items = []
                    sections.append(("divider", item.title))
                    continue
                item_number = None
                if getattr(item, "show_number", True):
                    display_num += 1
                    item_number = display_num
                section_items.append((item, item_number))
                selectable_items.append(item)
            if section_items:
                sections.append(("items", section_items))

            if sections:
                content_width = max(24, min(cols - len(pad) - 2, body_width))
                gutter_width = 4

                for section_type, payload in sections:
                    if section_type == "divider":
                        divider_title = (payload or "").strip()
                        if not divider_title or all(ch in "─-" for ch in divider_title):
                            divider_title = "─" * max(24, min(content_width, body_width))
                        print(f"{pad}{Style.DIM}{divider_title}{Style.RESET}")
                        print()
                        continue

                    section_rows = payload
                    item_groups = []
                    numbered_entries = [entry for entry in section_rows if entry[1] is not None]
                    unnumbered_entries = [entry for entry in section_rows if entry[1] is None]
                    if numbered_entries:
                        item_groups.append(numbered_entries)
                    if unnumbered_entries:
                        item_groups.append(unnumbered_entries)

                    for group_rows in item_groups:
                        col_count = min(self.grid_columns, max(1, len(group_rows)))
                        col_width = max(18, (content_width - (gutter_width * (col_count - 1))) // col_count)

                        for row_start in range(0, len(group_rows), col_count):
                            row_items = group_rows[row_start:row_start + col_count]
                            row_parts = []
                            for col_idx in range(col_count):
                                row_entry = row_items[col_idx] if col_idx < len(row_items) else None
                                if row_entry is None:
                                    row_parts.append(' ' * col_width)
                                    continue
                                item, item_number = row_entry
                                icon_width = self._display_width(item.icon)
                                icon_pad = ' ' * max(1, 2 - icon_width)
                                if has_number_column and item_number is not None:
                                    num_label = f"{item_number:>{num_width}}."
                                elif has_number_column:
                                    num_label = " " * (num_width + 1)
                                else:
                                    num_label = ""
                                if num_label:
                                    cell_prefix = f"{num_label} {item.icon}{icon_pad} "
                                else:
                                    cell_prefix = f"{item.icon}{icon_pad} "
                                title_width = max(10, col_width - self._display_width(cell_prefix))
                                short_title = textwrap.shorten(item.title, width=title_width, placeholder="...")
                                plain_cell = f"{cell_prefix}{short_title}"
                                trailing_pad = max(0, col_width - self._display_width(plain_cell))
                                if item.enabled and has_number_column and item_number is not None:
                                    rendered_cell = (
                                        f"{Style.CYAN}{num_label}{Style.RESET} {item.icon}{icon_pad} "
                                        f"{Style.BOLD}{short_title}{Style.RESET}"
                                    )
                                elif item.enabled and has_number_column:
                                    rendered_cell = (
                                        f"{Style.DIM}{num_label}{Style.RESET} {item.icon}{icon_pad} "
                                        f"{Style.BOLD}{short_title}{Style.RESET}"
                                    )
                                else:
                                    rendered_cell = f"{item.icon}{icon_pad} {short_title}{Style.RESET}" if item.enabled else f"{Style.DIM}{item.icon}{icon_pad} {short_title}{Style.RESET}"
                                row_parts.append(f"{rendered_cell}{' ' * trailing_pad}")
                            print(f"{pad}{(' ' * gutter_width).join(row_parts)}")
                            if row_start + col_count < len(group_rows):
                                print()
        else:
            display_num = 0
            for item in visible_items:
                if self._is_divider(item):
                    print(f"{pad}{Style.DIM}{item.title}{Style.RESET}")
                    continue
                selectable_items.append(item)
                item_number = None
                if getattr(item, "show_number", True):
                    display_num += 1
                    item_number = display_num
                item_number_label = f"{item_number}. " if item_number is not None else ""
                item_prefix = f"{item_number_label}{item.icon}  "
                title_body_cap = body_width - len(item_prefix)
                title_width = max(22, min(cols - len(pad) - len(item_prefix) - 2, title_body_cap))
                title_lines = self._wrap_text(item.title, title_width)
                if item.enabled and item_number is not None:
                    print(f"{pad}{Style.CYAN}{item_number}.{Style.RESET} {item.icon}  {Style.BOLD}{title_lines[0]}{Style.RESET}")
                elif item.enabled:
                    print(f"{pad}{item.icon}  {Style.BOLD}{title_lines[0]}{Style.RESET}")
                else:
                    if item_number is not None:
                        print(f"{pad}{Style.DIM}{item_number}. {item.icon}  {title_lines[0]}{Style.RESET}")
                    else:
                        print(f"{pad}{Style.DIM}{item.icon}  {title_lines[0]}{Style.RESET}")
                for cont in title_lines[1:]:
                    print(f"{pad}{' ' * len(item_prefix)}{Style.DIM}{cont}{Style.RESET}")
                if item.description and not self.compact_rows:
                    desc_prefix = " " * (len(item_prefix) + 1)
                    desc_body_cap = body_width - len(desc_prefix)
                    desc_width = max(22, min(cols - len(pad) - len(desc_prefix) - 2, desc_body_cap))
                    for desc_line in self._wrap_text(item.description, desc_width):
                        print(f"{pad}{desc_prefix}{Style.DIM}{desc_line}{Style.RESET}")
                if item_gap:
                    print()

        if self.fallback_footer:
            print()
            for footer_line in self.fallback_footer.splitlines():
                if footer_line.strip():
                    print(f"{pad}{footer_line.strip()}")
                else:
                    print()

        print()
        hint = ""
        if self.allow_text_input and self.text_input_hint:
            for raw_line in self.text_input_hint.splitlines():
                candidate = raw_line.strip()
                if not candidate or candidate.lower() == "__hint_divider__":
                    continue
                if candidate.lower().startswith("__hint_red__"):
                    candidate = candidate[len("__hint_red__"):].strip()
                if candidate:
                    hint = candidate
                    break
        if not selectable_items:
            return MenuResult(key="", cancelled=True)
        prompt = f"  {Style.BOLD}Select option{' or ' + hint if hint else ''}:{Style.RESET} "

        try:
            user_input = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            return MenuResult(key="", cancelled=True)

        if not user_input:
            return MenuResult(key="", cancelled=True)

        # Check for path/text input
        if self.allow_text_input:
            # Remove quotes
            if user_input.startswith('"') and user_input.endswith('"'):
                user_input = user_input[1:-1]
            import os
            if os.path.exists(user_input) or '/' in user_input or '\\' in user_input:
                return MenuResult(key="__path__", text_input=user_input)

        # Numeric selection
        try:
            choice = int(user_input)
            if 1 <= choice <= len(selectable_items):
                item = selectable_items[choice - 1]
                if item.enabled:
                    return MenuResult(key=item.key)
        except ValueError:
            pass

        # Key/title selection (case-insensitive) for non-numbered fallback menus.
        normalized = user_input.strip().lower()
        exact_matches = [
            item for item in selectable_items
            if item.enabled and (
                normalized == str(item.key).strip().lower()
                or normalized == str(item.title).strip().lower()
            )
        ]
        if len(exact_matches) == 1:
            return MenuResult(key=exact_matches[0].key)

        prefix_matches = [
            item for item in selectable_items
            if item.enabled and (
                str(item.key).strip().lower().startswith(normalized)
                or str(item.title).strip().lower().startswith(normalized)
            )
        ]
        if len(prefix_matches) == 1:
            return MenuResult(key=prefix_matches[0].key)

        return MenuResult(key="", cancelled=True)


def select_menu(title: str, items: List[MenuItem], subtitle: str = "",
                allow_text_input: bool = False, text_input_hint: str = "",
                header_lines=None, footer_lines=None,
                fallback_header: str = "", fallback_footer: str = "",
                hotkeys: Optional[dict] = None,
                paste_only_text_input: bool = False,
                show_item_divider: bool = False,
                animate_item_divider: bool = False,
                wrap_selected_description: bool = False,
                compact_rows: bool = False,
                grid_columns: int = 1) -> MenuResult:
    """Convenience function to show a menu"""
    menu = InteractiveMenu(title, items, subtitle, allow_text_input,
                          text_input_hint, header_lines, footer_lines,
                          fallback_header, fallback_footer, hotkeys=hotkeys,
                          paste_only_text_input=paste_only_text_input,
                          show_item_divider=show_item_divider,
                          animate_item_divider=animate_item_divider,
                          wrap_selected_description=wrap_selected_description,
                          compact_rows=compact_rows,
                          grid_columns=grid_columns)
    return menu.show()


def confirm_dialog(message: str, default: bool = False) -> bool:
    """Simple yes/no confirmation"""
    items = [
        MenuItem("yes", "✓", "Yes"),
        MenuItem("no", "✗", "No"),
    ]
    # Set default selection
    result = select_menu(message, items)
    if result.cancelled:
        return default
    return result.key == "yes"


def input_dialog(prompt: str, default: str = "", password: bool = False) -> Optional[str]:
    """Get text input from user"""
    if PROMPT_TOOLKIT_AVAILABLE:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style as PTStyle

        style = PTStyle.from_dict({'prompt': '#a4afea bold'})
        kb = KeyBindings()
        cancel_token = "__MIXSPLITR_ESC_CANCEL__"

        @kb.add('escape')
        def _cancel_with_escape(event):
            # Make Esc consistently cancel text input and return to caller.
            event.app.exit(result=cancel_token)

        @kb.add('c-c')
        def _cancel_with_ctrl_c(event):
            event.app.exit(result=cancel_token)

        try:
            result = pt_prompt(
                HTML(f'<prompt>  {prompt}</prompt> '),
                style=style,
                default=default,
                is_password=password,
                key_bindings=kb,
            )
            if result == cancel_token:
                return None
            return result
        except (KeyboardInterrupt, EOFError):
            return None
    else:
        # Fallback
        display_default = f" [{default}]" if default and not password else ""
        display_default = f" [****]" if default and password else display_default
        try:
            if password:
                import getpass
                result = getpass.getpass(f"  {prompt}{display_default}: ")
            else:
                result = input(f"  {prompt}{display_default}: ")
            return result.strip() or default
        except (KeyboardInterrupt, EOFError):
            return None


def wait_for_enter(message: str = "Press Enter to continue..."):
    """Wait for user to press Enter"""
    if PROMPT_TOOLKIT_AVAILABLE:
        from prompt_toolkit import prompt as pt_prompt
        try:
            pt_prompt(HTML(f'<style fg="#72788f">  {message}</style>'))
        except (KeyboardInterrupt, EOFError):
            pass
    else:
        try:
            input(f"  {message}")
        except (KeyboardInterrupt, EOFError):
            pass


def clear_screen():
    """Clear terminal screen"""
    print('\033[2J\033[H', end='', flush=True)


# Quick test
if __name__ == "__main__":
    items = [
        MenuItem("preview", "👁️", "Preview Mode", "Analyze files, identify tracks, review before saving"),
        MenuItem("direct", "⚡", "Direct Mode", "Process everything immediately"),
        MenuItem("cache", "📦", "Apply Cached Preview", "Apply previous preview session"),
        MenuItem("api", "🔑", "Manage API Keys", "View or update credentials"),
        MenuItem("exit", "🚪", "Exit", "Close the program"),
    ]

    result = select_menu(
        "MixSplitR",
        items,
        subtitle="What would you like to do?",
        allow_text_input=True,
        text_input_hint="drag files here"
    )

    print(f"\nSelected: {result.key}, cancelled: {result.cancelled}, text: {result.text_input}")
