import inspect
import os

from PySide6.QtCore import QObject, QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QWidget,
)

try:
    import shiboken6
except Exception:
    shiboken6 = None


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

ROLE_PROP = "_mixsplitr_dev_role"
SOURCE_FILE_PROP = "_mixsplitr_dev_source_file"
SOURCE_LINE_PROP = "_mixsplitr_dev_source_line"
SOURCE_MODULE_PROP = "_mixsplitr_dev_source_module"
SOURCE_FUNCTION_PROP = "_mixsplitr_dev_source_function"
OWNER_FILE_PROP = "_mixsplitr_dev_owner_file"
OWNER_LINE_PROP = "_mixsplitr_dev_owner_line"
OWNER_MODULE_PROP = "_mixsplitr_dev_owner_module"
OWNER_FUNCTION_PROP = "_mixsplitr_dev_owner_function"
OWNER_ROLE_PROP = "_mixsplitr_dev_owner_role"

_SOURCE_PROP_MAP = (
    (SOURCE_FILE_PROP, "file"),
    (SOURCE_LINE_PROP, "line"),
    (SOURCE_MODULE_PROP, "module"),
    (SOURCE_FUNCTION_PROP, "function"),
)
_OWNER_PROP_MAP = (
    (OWNER_FILE_PROP, "file"),
    (OWNER_LINE_PROP, "line"),
    (OWNER_MODULE_PROP, "module"),
    (OWNER_FUNCTION_PROP, "function"),
)
_GENERATED_OBJECT_NAMES = {
    "qt_scrollarea_viewport",
    "qt_scrollarea_hcontainer",
    "qt_scrollarea_vcontainer",
}
_CONTEXT_ROLE_TOKENS = ("page", "dialog", "window", "panel", "editor", "browser")


def _is_widget_alive(widget):
    if widget is None:
        return False
    if shiboken6 is None:
        return True
    try:
        return bool(shiboken6.isValid(widget))
    except Exception:
        return False


def _normalize_source(source):
    data = dict(source or {})
    line_value = data.get("line")
    try:
        data["line"] = int(line_value)
    except Exception:
        data["line"] = 0
    data["file"] = str(data.get("file") or "")
    data["module"] = str(data.get("module") or "")
    data["function"] = str(data.get("function") or "")
    return data


def capture_python_source(stacklevel=0):
    frame = inspect.currentframe()
    try:
        for _ in range(max(0, int(stacklevel)) + 1):
            if frame is None:
                return {}
            frame = frame.f_back
        while frame is not None:
            filename = os.path.abspath(str(frame.f_code.co_filename or ""))
            if filename != PROJECT_ROOT and filename != os.path.abspath(__file__):
                break
            if filename != os.path.abspath(__file__):
                break
            frame = frame.f_back
        if frame is None:
            return {}
        return {
            "file": os.path.abspath(str(frame.f_code.co_filename or "")),
            "line": int(getattr(frame, "f_lineno", 0) or 0),
            "module": str(frame.f_globals.get("__name__", "") or ""),
            "function": str(frame.f_code.co_name or ""),
        }
    finally:
        del frame


def _set_source_properties(widget, prop_map, source, overwrite=False):
    if not _is_widget_alive(widget):
        return
    clean = _normalize_source(source)
    for prop_name, key in prop_map:
        existing = widget.property(prop_name)
        if overwrite or existing in (None, "", 0):
            widget.setProperty(prop_name, clean.get(key))


def _get_source_properties(widget, prop_map):
    if not _is_widget_alive(widget):
        return {}
    data = {}
    for prop_name, key in prop_map:
        value = widget.property(prop_name)
        if value in (None, "", 0):
            continue
        data[key] = value
    return _normalize_source(data) if data else {}


def annotate_widget(widget, role=None, *, source=None, stacklevel=0, overwrite=False):
    if not _is_widget_alive(widget):
        return widget
    if role not in (None, "") and (overwrite or not str(widget.property(ROLE_PROP) or "").strip()):
        widget.setProperty(ROLE_PROP, str(role))
    src = _normalize_source(source or capture_python_source(stacklevel + 1))
    _set_source_properties(widget, _SOURCE_PROP_MAP, src, overwrite=overwrite)
    return widget


def annotate_widget_owner(widget, role=None, *, source=None, stacklevel=0, overwrite=False):
    if not _is_widget_alive(widget):
        return widget
    if role not in (None, "") and (overwrite or not str(widget.property(OWNER_ROLE_PROP) or "").strip()):
        widget.setProperty(OWNER_ROLE_PROP, str(role))
    src = _normalize_source(source or capture_python_source(stacklevel + 1))
    _set_source_properties(widget, _OWNER_PROP_MAP, src, overwrite=overwrite)
    return widget


def copy_widget_owner_source(source_widget, target_widget, role=None, overwrite=False):
    if (not _is_widget_alive(source_widget)) or (not _is_widget_alive(target_widget)):
        return target_widget
    owner = get_widget_owner_source(source_widget) or get_widget_effective_source(source_widget)
    if owner:
        annotate_widget_owner(target_widget, role=role, source=owner, overwrite=overwrite)
    elif role not in (None, "") and (overwrite or not str(target_widget.property(OWNER_ROLE_PROP) or "").strip()):
        target_widget.setProperty(OWNER_ROLE_PROP, str(role))
    return target_widget


def get_widget_role(widget):
    if not _is_widget_alive(widget):
        return ""
    role = str(widget.property(ROLE_PROP) or "").strip()
    if role:
        return role
    owner_role = str(widget.property(OWNER_ROLE_PROP) or "").strip()
    if owner_role:
        return owner_role
    return ""


def get_widget_source(widget):
    return _get_source_properties(widget, _SOURCE_PROP_MAP)


def get_widget_owner_source(widget):
    return _get_source_properties(widget, _OWNER_PROP_MAP)


def get_widget_effective_source(widget):
    owner = get_widget_owner_source(widget)
    if owner:
        return owner
    return get_widget_source(widget)


def _widget_text(widget):
    if not _is_widget_alive(widget):
        return ""
    try:
        if isinstance(widget, QAbstractButton):
            return str(widget.text() or "").strip()
        if isinstance(widget, QLabel):
            return str(widget.text() or "").strip()
        if isinstance(widget, QLineEdit):
            text = str(widget.text() or "").strip()
            if text:
                return text
            return str(widget.placeholderText() or "").strip()
        if isinstance(widget, QTextEdit):
            return str(widget.toPlainText() or "").strip()
        if isinstance(widget, QComboBox):
            current = str(widget.currentText() or "").strip()
            if current:
                return current
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return str(widget.text() or "").strip()
        if hasattr(widget, "windowTitle"):
            title = str(widget.windowTitle() or "").strip()
            if title:
                return title
    except Exception:
        return ""
    return ""


def _widget_name_hint(widget):
    if not _is_widget_alive(widget):
        return ""
    role = get_widget_role(widget)
    if role:
        return role
    text = _widget_text(widget)
    if text:
        compact = " ".join(text.split())
        return compact[:160]
    object_name = str(widget.objectName() or "").strip()
    if object_name:
        return object_name
    try:
        return str(widget.metaObject().className() or widget.__class__.__name__)
    except Exception:
        return widget.__class__.__name__


def _is_generated_qt_child(widget):
    if not _is_widget_alive(widget):
        return False
    name = str(widget.objectName() or "").strip().lower()
    if name in _GENERATED_OBJECT_NAMES:
        return True
    if name.startswith("qt_"):
        return True
    return False


def _iter_widget_ancestors(widget):
    current = widget
    while _is_widget_alive(current):
        yield current
        try:
            parent = current.parentWidget()
        except Exception:
            parent = None
        current = parent if isinstance(parent, QWidget) else None


def _belongs_to_root(widget, root):
    if (not _is_widget_alive(widget)) or (not _is_widget_alive(root)):
        return False
    for candidate in _iter_widget_ancestors(widget):
        if candidate is root:
            return True
    return False


def resolve_inspector_widget(widget, ignored_roots=None):
    if not _is_widget_alive(widget):
        return None, False
    ignored = [root for root in list(ignored_roots or []) if _is_widget_alive(root)]
    current = widget
    fallback = False
    while _is_widget_alive(current):
        if any(_belongs_to_root(current, root) for root in ignored):
            return None, fallback
        source = get_widget_effective_source(current)
        role = get_widget_role(current)
        meaningful = bool(source) or bool(role)
        if meaningful and not _is_generated_qt_child(current):
            return current, fallback
        try:
            parent = current.parentWidget()
        except Exception:
            parent = None
        current = parent if isinstance(parent, QWidget) else None
        fallback = True
    return None, fallback


def _relative_source_path(path_value):
    path = os.path.abspath(str(path_value or ""))
    if not path:
        return ""
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except Exception:
        return path


def _ancestor_summary(widget, ignored_roots=None, limit=6):
    parts = []
    ignored = [root for root in list(ignored_roots or []) if _is_widget_alive(root)]
    for candidate in _iter_widget_ancestors(widget):
        if any(candidate is root for root in ignored):
            break
        label = _widget_name_hint(candidate)
        if label:
            parts.append(label)
        if len(parts) >= int(limit):
            break
    return " > ".join(parts)


def _page_context(widget, ignored_roots=None):
    ignored = [root for root in list(ignored_roots or []) if _is_widget_alive(root)]
    for candidate in _iter_widget_ancestors(widget):
        if any(candidate is root for root in ignored):
            break
        role = get_widget_role(candidate)
        role_lower = role.lower()
        if role and any(token in role_lower for token in _CONTEXT_ROLE_TOKENS):
            return role
        if isinstance(candidate, (QDialog, QMainWindow)):
            title = _widget_text(candidate)
            if title:
                return title
    top_level = widget.window() if _is_widget_alive(widget) else None
    return _widget_name_hint(top_level) if _is_widget_alive(top_level) else ""


def format_inspector_location(payload):
    data = dict(payload or {})
    if not data.get("has_target"):
        return "No developer-inspector target."
    lines = [
        f"Status: {data.get('status', '')}".strip(),
        f"Element: {data.get('element_label', '')}".strip(),
        f"Widget Class: {data.get('widget_class', '')}".strip(),
        f"Object Name: {data.get('object_name', '')}".strip(),
        f"Context: {data.get('page_context', '')}".strip(),
        f"Module: {data.get('module', '')}".strip(),
        f"Function: {data.get('function', '')}".strip(),
        f"Location: {data.get('source_path', '')}:{data.get('line_number', '')}".rstrip(":"),
        f"Ancestors: {data.get('ancestor_summary', '')}".strip(),
    ]
    if data.get("fallback_target"):
        lines.append("Resolution: Ancestor fallback")
    return "\n".join([line for line in lines if line.split(": ", 1)[-1]])


def build_inspector_payload(widget, *, ignored_roots=None, locked=False, raw_widget=None):
    status = "Locked" if locked else "Live"
    if not _is_widget_alive(widget):
        return {
            "status": status,
            "has_target": False,
            "element_label": "",
            "widget_class": "",
            "object_name": "",
            "page_context": "",
            "module": "",
            "function": "",
            "source_path": "",
            "line_number": "",
            "ancestor_summary": "",
            "fallback_target": False,
            "copy_text": "No developer-inspector target.",
        }
    source = get_widget_effective_source(widget)
    role = _widget_name_hint(widget)
    if raw_widget is not None and raw_widget is not widget:
        role = f"{role} [ancestor fallback]"
    try:
        widget_class = str(widget.metaObject().className() or widget.__class__.__name__)
    except Exception:
        widget_class = widget.__class__.__name__
    object_name = str(widget.objectName() or "").strip()
    payload = {
        "status": status,
        "has_target": True,
        "element_label": role,
        "widget_class": widget_class,
        "object_name": object_name,
        "page_context": _page_context(widget, ignored_roots=ignored_roots),
        "module": str(source.get("module") or ""),
        "function": str(source.get("function") or ""),
        "source_path": _relative_source_path(source.get("file")),
        "line_number": str(source.get("line") or ""),
        "ancestor_summary": _ancestor_summary(widget, ignored_roots=ignored_roots),
        "fallback_target": bool(raw_widget is not None and raw_widget is not widget),
    }
    payload["copy_text"] = format_inspector_location(payload)
    return payload


class DeveloperInspectorController(QObject):
    inspector_changed = Signal(object)

    def __init__(self, parent=None, poll_interval_ms=90):
        super().__init__(parent)
        self._app = QApplication.instance()
        self._enabled = False
        self._locked = False
        self._locked_widget = None
        self._current_widget = None
        self._current_raw_widget = None
        self._ignored_roots = []
        self._last_payload_key = None
        self._last_payload = build_inspector_payload(None)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(max(40, int(poll_interval_ms or 90)))
        self._poll_timer.timeout.connect(self.refresh_from_cursor)
        if self._app is not None:
            self._app.installEventFilter(self)

    @property
    def current_payload(self):
        return dict(self._last_payload or {})

    def set_enabled(self, enabled):
        target = bool(enabled)
        self._enabled = target
        if target:
            self._poll_timer.start()
            self.refresh_from_cursor(force_emit=True)
            return
        self._poll_timer.stop()
        self._locked = False
        self._locked_widget = None
        self._current_widget = None
        self._current_raw_widget = None
        self._emit_payload(build_inspector_payload(None))

    def set_ignored_root(self, widget):
        if not _is_widget_alive(widget):
            return
        if any(existing is widget for existing in self._ignored_roots):
            return
        self._ignored_roots.append(widget)

    def unlock(self):
        if not self._locked:
            return
        self._locked = False
        self._locked_widget = None
        self.refresh_from_cursor(force_emit=True)

    def lock_current_target(self, widget=None):
        if not self._enabled:
            return
        raw = widget if _is_widget_alive(widget) else self._widget_at_cursor()
        resolved, _fallback = resolve_inspector_widget(raw, ignored_roots=self._ignored_roots)
        if not _is_widget_alive(resolved):
            return
        self._locked = True
        self._locked_widget = resolved
        self._current_widget = resolved
        self._current_raw_widget = raw
        self._emit_payload(build_inspector_payload(
            resolved,
            ignored_roots=self._ignored_roots,
            locked=True,
            raw_widget=raw,
        ))

    def _widget_at_cursor(self):
        if self._app is None:
            return None
        try:
            return self._app.widgetAt(QCursor.pos())
        except Exception:
            return None

    def refresh_from_cursor(self, force_emit=False):
        if not self._enabled:
            return
        if self._locked:
            if not _is_widget_alive(self._locked_widget):
                self.unlock()
                return
            if force_emit:
                self._emit_payload(build_inspector_payload(
                    self._locked_widget,
                    ignored_roots=self._ignored_roots,
                    locked=True,
                    raw_widget=self._current_raw_widget,
                ), force_emit=True)
            return
        raw = self._widget_at_cursor()
        resolved, _fallback = resolve_inspector_widget(raw, ignored_roots=self._ignored_roots)
        self._current_widget = resolved
        self._current_raw_widget = raw
        self._emit_payload(build_inspector_payload(
            resolved,
            ignored_roots=self._ignored_roots,
            locked=False,
            raw_widget=raw,
        ), force_emit=force_emit)

    def _emit_payload(self, payload, force_emit=False):
        data = dict(payload or {})
        key = (
            data.get("status"),
            data.get("element_label"),
            data.get("widget_class"),
            data.get("source_path"),
            data.get("line_number"),
            data.get("fallback_target"),
        )
        if (not force_emit) and key == self._last_payload_key:
            return
        self._last_payload_key = key
        self._last_payload = data
        self.inspector_changed.emit(data)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type == QEvent.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                annotate_widget(child, stacklevel=1)
        if not self._enabled:
            return False
        if event_type in (QEvent.MouseMove, QEvent.HoverMove, QEvent.Enter, QEvent.Leave, QEvent.Show, QEvent.WindowActivate):
            self.refresh_from_cursor()
        elif event_type == QEvent.MouseButtonPress:
            if getattr(event, "button", lambda: None)() == Qt.LeftButton:
                modifiers = getattr(event, "modifiers", lambda: Qt.NoModifier)()
                if bool(modifiers & Qt.AltModifier):
                    target = watched if isinstance(watched, QWidget) else self._widget_at_cursor()
                    self.lock_current_target(target)
        elif event_type == QEvent.KeyPress:
            if getattr(event, "key", lambda: None)() == Qt.Key_Escape:
                self.unlock()
        elif event_type == QEvent.Destroy and self._locked and watched is self._locked_widget:
            self.unlock()
        return False
