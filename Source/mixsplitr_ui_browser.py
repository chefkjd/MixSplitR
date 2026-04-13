import sys
import os
import ctypes
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QCheckBox, QDialog, QFileDialog, QAbstractItemView,
    QApplication, QMessageBox, QStyle, QWidget
)
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtCore import Qt, Signal

from mixsplitr_devtools import annotate_widget

# ==========================================
# PATH UTILITIES
# ==========================================

AUDIO_BROWSER_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".aif",
    ".aiff",
    ".wma",
}
WINDOWS_DRIVE_UNKNOWN = 0
WINDOWS_DRIVE_NO_ROOT_DIR = 1
WINDOWS_DRIVE_REMOVABLE = 2
WINDOWS_DRIVE_FIXED = 3
WINDOWS_DRIVE_REMOTE = 4
WINDOWS_DRIVE_CDROM = 5
WINDOWS_DRIVE_RAMDISK = 6
WINDOWS_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
WINDOWS_ERROR_MORE_DATA = 234


def _normalize_existing_path(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if sys.platform == "win32" and len(text) == 2 and text[1] == ":" and text[0].isalpha():
        text = f"{text}\\"
    return os.path.abspath(os.path.expanduser(text))


def _normalize_existing_directory(value):
    normalized = _normalize_existing_path(value)
    if not normalized:
        return ""
    if sys.platform == "win32" and _windows_path_is_network(normalized):
        return ""
    if os.path.isdir(normalized):
        return normalized
    if os.path.isfile(normalized):
        parent = os.path.dirname(normalized)
        if parent and os.path.isdir(parent):
            return parent
    return ""


def _browser_default_directory(default_dir=""):
    candidates = []
    for candidate in (
        default_dir,
        os.path.expanduser("~"),
        os.environ.get("USERPROFILE", ""),
        os.path.join(os.path.expanduser("~"), "Documents"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
        os.getcwd(),
    ):
        normalized = _normalize_existing_directory(candidate)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    if sys.platform == "win32":
        system_drive = str(os.environ.get("SystemDrive", "") or "").strip()
        if system_drive:
            drive_root = _windows_drive_root(system_drive)
            if drive_root and _windows_drive_is_ready(drive_root) and not _windows_drive_is_mapped_network(drive_root):
                if drive_root not in candidates:
                    candidates.append(drive_root)

    if candidates:
        return candidates[0]
    return os.path.expanduser("~")


def _windows_drive_root(path):
    normalized = _normalize_existing_path(path)
    if not normalized:
        return ""
    drive, _tail = os.path.splitdrive(normalized)
    if not drive:
        return ""
    return drive.rstrip("\\/") + "\\"


def _windows_drive_is_mapped_network(path):
    if sys.platform != "win32":
        return False
    root = _windows_drive_root(path)
    if not root:
        return False
    local_name = root[:2]
    try:
        remote_name = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(len(remote_name))
        result = int(ctypes.windll.mpr.WNetGetConnectionW(local_name, remote_name, ctypes.byref(size)))
    except Exception:
        return False
    return result in (0, WINDOWS_ERROR_MORE_DATA)


def _windows_drive_is_ready(path):
    if sys.platform != "win32":
        return False
    root = _windows_drive_root(path)
    if not root:
        return False
    try:
        attrs = int(ctypes.windll.kernel32.GetFileAttributesW(root))
    except Exception:
        return False
    return attrs != WINDOWS_INVALID_FILE_ATTRIBUTES


def _windows_drive_type(path):
    if sys.platform != "win32":
        return WINDOWS_DRIVE_UNKNOWN
    root = _windows_drive_root(path)
    if not root:
        return WINDOWS_DRIVE_UNKNOWN
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except Exception:
        return WINDOWS_DRIVE_UNKNOWN


def _windows_drive_root_is_browsable(path):
    if sys.platform != "win32":
        return False
    root = _windows_drive_root(path)
    if not root:
        return False
    drive_type = _windows_drive_type(root)
    if drive_type in (
        WINDOWS_DRIVE_UNKNOWN,
        WINDOWS_DRIVE_NO_ROOT_DIR,
        WINDOWS_DRIVE_REMOTE,
    ):
        return False
    if drive_type not in (
        WINDOWS_DRIVE_FIXED,
        WINDOWS_DRIVE_REMOVABLE,
        WINDOWS_DRIVE_RAMDISK,
        WINDOWS_DRIVE_CDROM,
    ):
        return False
    if not _windows_drive_is_ready(root):
        return False
    try:
        return bool(os.path.isdir(root))
    except Exception:
        return False


def _windows_path_is_network(path):
    if sys.platform != "win32":
        return False
    raw_text = str(path or "").strip().replace("/", "\\")
    if raw_text.startswith("\\\\"):
        return True
    return _windows_drive_is_mapped_network(path)


def _list_windows_logical_drive_roots():
    if sys.platform != "win32":
        return []
    roots = []
    try:
        bitmask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except Exception:
        return roots
    for offset in range(26):
        if not (bitmask & (1 << offset)):
            continue
        root = f"{chr(ord('A') + offset)}:\\"
        if not _windows_drive_root_is_browsable(root):
            continue
        roots.append(root)
    return roots


def _preferred_windows_drive_roots(default_dir=""):
    if sys.platform != "win32":
        return []
    roots = list(_list_windows_logical_drive_roots())
    if roots:
        return roots

    fallback_roots = []
    seen = set()
    for candidate in (
        default_dir,
        os.path.expanduser("~"),
        os.environ.get("USERPROFILE", ""),
        os.environ.get("SystemDrive", ""),
    ):
        root = _windows_drive_root(candidate)
        if not root:
            continue
        key = os.path.normcase(root)
        if key in seen:
            continue
        seen.add(key)
        if not _windows_drive_root_is_browsable(root):
            continue
        fallback_roots.append(root)
    return fallback_roots


def _browser_sidebar_locations(default_dir=""):
    locations = []
    seen = set()

    def _add_location(label, path):
        normalized = _normalize_existing_directory(path)
        if not normalized:
            return
        key = os.path.normcase(normalized) if sys.platform == "win32" else normalized
        if key in seen:
            return
        seen.add(key)
        locations.append((str(label or normalized).strip() or normalized, normalized))

    def _add_drive_root(path):
        normalized = _windows_drive_root(path)
        if not normalized:
            return
        if sys.platform == "win32" and not _windows_drive_root_is_browsable(normalized):
            return
        key = os.path.normcase(normalized)
        if key in seen:
            return
        seen.add(key)
        locations.append((normalized, normalized))

    home_path = os.path.expanduser("~")
    _add_location("Home", home_path)
    for label, folder_name in (
        ("Desktop", "Desktop"),
        ("Documents", "Documents"),
        ("Downloads", "Downloads"),
        ("Music", "Music"),
    ):
        _add_location(label, os.path.join(home_path, folder_name))

    if sys.platform == "win32":
        # Avoid probing every logical drive during dialog startup on Windows.
        # Removable, offline, or slow shell-backed drives can stall the UI here.
        for drive_root in _preferred_windows_drive_roots(default_dir):
            _add_drive_root(drive_root)
    else:
        _add_location("Root", os.path.sep)
        if sys.platform == "darwin":
            _add_location("Volumes", "/Volumes")

    default_location = _normalize_existing_directory(default_dir)
    if default_location:
        _add_location("Current Folder", default_location)
        if sys.platform == "win32":
            default_root = _windows_drive_root(default_location)
            if default_root:
                _add_drive_root(default_root)

    return locations


def _file_dialog_options(*, show_dirs_only=False):
    options = QFileDialog.Options()
    if sys.platform == "win32":
        options |= QFileDialog.DontUseNativeDialog
    if show_dirs_only:
        options |= QFileDialog.ShowDirsOnly
    return options


def choose_existing_directory(parent=None, title="Select Folder", default_dir=""):
    selected = QFileDialog.getExistingDirectory(
        parent,
        title,
        _browser_default_directory(default_dir),
        options=_file_dialog_options(show_dirs_only=True),
    )
    return _normalize_existing_directory(selected)


def choose_open_file(parent=None, title="Open File", default_path="", file_filter="All Files (*)"):
    suggested_path = str(default_path or "").strip()
    initial_path = suggested_path if os.path.isfile(suggested_path) else _browser_default_directory(os.path.dirname(suggested_path) or suggested_path)
    selected_path, selected_filter = QFileDialog.getOpenFileName(
        parent,
        title,
        initial_path,
        file_filter,
        options=_file_dialog_options(),
    )
    return str(selected_path or "").strip(), str(selected_filter or "").strip()


def choose_save_file(parent=None, title="Save File", default_path="", file_filter="All Files (*)"):
    save_dir = _browser_default_directory(os.path.dirname(str(default_path or "").strip()))
    suggested_path = str(default_path or "").strip()
    if suggested_path and not os.path.isdir(suggested_path):
        initial_path = suggested_path
    else:
        initial_path = save_dir
    selected_path, selected_filter = QFileDialog.getSaveFileName(
        parent,
        title,
        initial_path,
        file_filter,
        options=_file_dialog_options(),
    )
    return str(selected_path or "").strip(), str(selected_filter or "").strip()


# ==========================================
# 1. DRAG & DROP AUDIO BROWSER
# ==========================================

class DragDropArea(QFrame):
    paths_selected = Signal(list)

    def __init__(self, parent=None, path_resolver=None):
        super().__init__(parent)
        annotate_widget(self, role="Audio Drop Area")
        self.setAcceptDrops(True)
        self.file_paths = []
        self.path_resolver = path_resolver
        self.setObjectName("DropZoneFrame")

        self.setStyleSheet("""
            QFrame#DropZoneFrame { background-color: #1F2328; border: 1px dashed #555D67; border-radius: 8px; }
            QFrame#DropZoneFrame:hover { border: 1px dashed #4D8DFF; background-color: #24292F; }
        """)
        layout = QVBoxLayout(self)
        self.label = QLabel("Drag & Drop audio mixes here\nor click to browse")
        annotate_widget(self.label, role="Audio Drop Area Label")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #C4C7C5; font-size: 14px; border: none;")
        layout.addWidget(self.label)

    def _picker_default_directory(self):
        for path in list(self.file_paths or []):
            normalized = os.path.abspath(os.path.expanduser(str(path or "").strip()))
            if not normalized:
                continue
            if os.path.isdir(normalized):
                return normalized
            parent = os.path.dirname(normalized)
            if parent and os.path.isdir(parent):
                return parent
        return _browser_default_directory()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _resolve_paths(self, raw_paths):
        paths = [str(p or "").strip() for p in list(raw_paths or []) if str(p or "").strip()]
        if callable(self.path_resolver):
            try:
                resolved = self.path_resolver(paths)
            except Exception:
                resolved = []
        else:
            resolved = [p for p in paths if os.path.isfile(p)]
        clean = []
        seen = set()
        for value in list(resolved or []):
            normalized = os.path.abspath(os.path.expanduser(str(value or "").strip()))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            clean.append(normalized)
        return clean

    def _apply_selected_paths(self, raw_paths):
        resolved = self._resolve_paths(raw_paths)
        self.file_paths = resolved
        if resolved:
            self.label.setText(f"{len(resolved)} file(s) ready for processing")
        else:
            self.label.setText("No supported audio files found\nDrag & Drop audio mixes here\nor click to browse")
        try:
            self.paths_selected.emit(list(resolved))
        except Exception:
            pass

    def _open_unified_browser(self):
        default_dir = self._picker_default_directory()
        dialog_parent = self.window() if isinstance(self.window(), QWidget) else self
        dialog = _AudioPathBrowserDialog(
            dialog_parent,
            title="Select Audio Files and/or Folders",
            default_dir=default_dir,
            audio_extensions=AUDIO_BROWSER_EXTENSIONS,
        )
        screen = None
        try:
            screen = dialog_parent.screen() if dialog_parent is not None else None
        except Exception:
            screen = None
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            min_w = max(980, int(available.width() * 0.58))
            min_h = max(620, int(available.height() * 0.58))
            target_w = max(min_w, int(available.width() * 0.60))
            target_h = max(min_h, int(available.height() * 0.60))
            max_w = max(min_w, int(available.width() * 0.96))
            max_h = max(min_h, int(available.height() * 0.92))
            target_w = min(target_w, max_w)
            target_h = min(target_h, max_h)
            dialog.setMinimumSize(min_w, min_h)
            dialog.resize(target_w, target_h)

        if dialog.exec() == QDialog.Accepted:
            selected_paths = list(getattr(dialog, "accepted_paths", []) or [])
            if selected_paths:
                self._apply_selected_paths(selected_paths)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            dropped_paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
            self._apply_selected_paths(dropped_paths)
            event.acceptProposedAction()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._open_unified_browser()


class _AudioPathBrowserDialog(QDialog):
    """Custom audio browser that avoids Qt's shell-backed file dialog on Windows."""

    def __init__(self, parent=None, title="Select Audio Files and/or Folders", default_dir="", audio_extensions=None):
        super().__init__(parent)
        annotate_widget(self, role="Audio Browser Dialog")
        self.setWindowTitle(title)
        self.accepted_paths = []
        self.audio_extensions = {str(ext or "").lower() for ext in list(audio_extensions or AUDIO_BROWSER_EXTENSIONS)}
        self.current_directory = _browser_default_directory(default_dir)
        self._history = []
        self._history_index = -1
        self._selected_keys = set()
        self._icon_cache = {}

        self._build_ui()
        self._populate_sidebar()
        self._set_current_directory(self.current_directory, push_history=True)

    def _build_ui(self):
        self.setModal(True)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #1D2024;
                color: #EBE7E1;
            }
            QLabel {
                color: #B4AEA4;
            }
            QLabel#BrowserStatusLabel {
                color: #8B857C;
            }
            QLineEdit, QListWidget {
                background-color: #1F2328;
                border: none;
                border-radius: 8px;
                color: #EBE7E1;
                padding: 8px 10px;
                selection-background-color: #2B4E7A;
                selection-color: #EBE7E1;
            }
            QListWidget::item {
                background-color: #22262B;
                border: none;
                border-radius: 6px;
                margin: 2px 0px;
                padding: 6px 8px;
            }
            QListWidget::item:hover:!selected {
                background-color: #292E34;
            }
            QListWidget::item:selected {
                background-color: #2B4E7A;
                border: none;
                color: #EBE7E1;
            }
            QPushButton {
                background-color: #22262B;
                border: none;
                border-radius: 8px;
                color: #EBE7E1;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background-color: #292E34;
            }
            QPushButton:pressed {
                background-color: #1F2328;
            }
            QPushButton:disabled {
                color: #7F7A72;
                background-color: #1C1F23;
                border: none;
            }
            QCheckBox {
                color: #B4AEA4;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self.back_btn = QPushButton("Back")
        self.up_btn = QPushButton("Up")
        self.home_btn = QPushButton("Home")
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Enter a folder or file path")
        self.go_btn = QPushButton("Go")
        self.refresh_btn = QPushButton("Refresh")
        nav_row.addWidget(self.back_btn)
        nav_row.addWidget(self.up_btn)
        nav_row.addWidget(self.home_btn)
        nav_row.addWidget(self.path_input, 1)
        nav_row.addWidget(self.go_btn)
        nav_row.addWidget(self.refresh_btn)
        root.addLayout(nav_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.show_all_files_checkbox = QCheckBox("Show non-audio files")
        self.location_label = QLabel("")
        self.location_label.setObjectName("BrowserStatusLabel")
        filter_row.addWidget(self.show_all_files_checkbox)
        filter_row.addStretch(1)
        filter_row.addWidget(self.location_label)
        root.addLayout(filter_row)

        content = QHBoxLayout()
        content.setSpacing(12)

        sidebar_layout = QVBoxLayout()
        sidebar_layout.setSpacing(8)
        sidebar_layout.addWidget(QLabel("Locations"))
        self.sidebar_list = QListWidget()
        self.sidebar_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sidebar_list.setMaximumWidth(240)
        sidebar_layout.addWidget(self.sidebar_list, 1)
        content.addLayout(sidebar_layout, 2)

        browser_layout = QVBoxLayout()
        browser_layout.setSpacing(8)
        browser_layout.addWidget(QLabel("Folder Contents"))
        self.entries_list = QListWidget()
        self.entries_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        browser_layout.addWidget(self.entries_list, 1)

        browse_button_row = QHBoxLayout()
        browse_button_row.setSpacing(8)
        self.open_btn = QPushButton("Open Folder")
        self.add_selected_btn = QPushButton("Add Selected")
        self.add_current_folder_btn = QPushButton("Add Current Folder")
        browse_button_row.addWidget(self.open_btn)
        browse_button_row.addWidget(self.add_selected_btn)
        browse_button_row.addWidget(self.add_current_folder_btn)
        browser_layout.addLayout(browse_button_row)
        content.addLayout(browser_layout, 5)

        selection_layout = QVBoxLayout()
        selection_layout.setSpacing(8)
        selection_layout.addWidget(QLabel("Selection"))
        self.selection_list = QListWidget()
        self.selection_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        selection_layout.addWidget(self.selection_list, 1)

        selection_button_row = QHBoxLayout()
        selection_button_row.setSpacing(8)
        self.remove_selected_btn = QPushButton("Remove")
        self.clear_selection_btn = QPushButton("Clear")
        selection_button_row.addWidget(self.remove_selected_btn)
        selection_button_row.addWidget(self.clear_selection_btn)
        selection_layout.addLayout(selection_button_row)
        content.addLayout(selection_layout, 4)

        root.addLayout(content, 1)

        footer_row = QHBoxLayout()
        footer_row.setSpacing(8)
        self.selection_summary_label = QLabel("No files or folders selected")
        self.selection_summary_label.setObjectName("BrowserStatusLabel")
        footer_row.addWidget(self.selection_summary_label)
        footer_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.select_btn = QPushButton("Select")
        self.select_btn.setDefault(True)
        footer_row.addWidget(self.cancel_btn)
        footer_row.addWidget(self.select_btn)
        root.addLayout(footer_row)

        self.back_btn.clicked.connect(self._go_back)
        self.up_btn.clicked.connect(self._go_up)
        self.home_btn.clicked.connect(self._go_home)
        self.go_btn.clicked.connect(self._go_to_entered_path)
        self.refresh_btn.clicked.connect(self._refresh_entries)
        self.path_input.returnPressed.connect(self._go_to_entered_path)
        self.show_all_files_checkbox.toggled.connect(self._refresh_entries)
        self.sidebar_list.itemActivated.connect(self._open_sidebar_item)
        self.sidebar_list.itemClicked.connect(self._open_sidebar_item)
        self.entries_list.itemDoubleClicked.connect(self._activate_entry_item)
        self.entries_list.itemSelectionChanged.connect(self._update_browser_buttons)
        self.selection_list.itemSelectionChanged.connect(self._update_selection_buttons)
        self.open_btn.clicked.connect(self._open_selected_folder)
        self.add_selected_btn.clicked.connect(self._add_selected_entries)
        self.add_current_folder_btn.clicked.connect(self._add_current_folder)
        self.remove_selected_btn.clicked.connect(self._remove_selected_paths)
        self.clear_selection_btn.clicked.connect(self._clear_selection)
        self.cancel_btn.clicked.connect(self.reject)
        self.select_btn.clicked.connect(self.accept)

        self._update_nav_buttons()
        self._update_browser_buttons()
        self._update_selection_buttons()
        self._update_selection_summary()

    def _standard_icon(self, icon_name, fallback_name=None):
        cache_key = (icon_name, fallback_name)
        cached = self._icon_cache.get(cache_key)
        if cached is not None:
            return cached
        style = self.style() or QApplication.style()
        icon = QIcon()
        icon_enum = getattr(QStyle, icon_name, None)
        if style is not None and icon_enum is not None:
            try:
                icon = style.standardIcon(icon_enum)
            except Exception:
                icon = QIcon()
        if icon.isNull() and fallback_name:
            fallback_enum = getattr(QStyle, fallback_name, None)
            if style is not None and fallback_enum is not None:
                try:
                    icon = style.standardIcon(fallback_enum)
                except Exception:
                    icon = QIcon()
        self._icon_cache[cache_key] = icon
        return icon

    def _path_key(self, path):
        normalized = _normalize_existing_path(path)
        if sys.platform == "win32":
            return os.path.normcase(normalized)
        return normalized

    def _icon_for_path(self, path, kind=""):
        normalized = _normalize_existing_path(path)
        drive_root = _windows_drive_root(normalized) if sys.platform == "win32" else ""
        if drive_root and self._path_key(drive_root) == self._path_key(normalized):
            return self._standard_icon("SP_DriveHDIcon", "SP_DirIcon")
        if kind == "dir" or os.path.isdir(normalized):
            return self._standard_icon("SP_DirIcon")
        return self._standard_icon("SP_FileIcon")

    def _icon_for_sidebar_label(self, label, path):
        text = str(label or "").strip().lower()
        if "home" in text:
            return self._standard_icon("SP_DirHomeIcon", "SP_DirIcon")
        normalized = _normalize_existing_path(path)
        drive_root = _windows_drive_root(normalized) if sys.platform == "win32" else ""
        if drive_root and self._path_key(drive_root) == self._path_key(normalized):
            return self._standard_icon("SP_DriveHDIcon", "SP_DirIcon")
        if normalized == os.path.sep:
            return self._standard_icon("SP_DriveHDIcon", "SP_DirIcon")
        return self._standard_icon("SP_DirIcon")

    def _populate_sidebar(self):
        current_key = os.path.normcase(self.current_directory) if sys.platform == "win32" else self.current_directory
        self.sidebar_list.clear()
        selected_row = -1
        for row, (label, path) in enumerate(_browser_sidebar_locations(self.current_directory)):
            item = QListWidgetItem(str(label))
            item.setData(Qt.UserRole, path)
            item.setIcon(self._icon_for_sidebar_label(label, path))
            self.sidebar_list.addItem(item)
            key = os.path.normcase(path) if sys.platform == "win32" else path
            if key == current_key:
                selected_row = row
        if selected_row >= 0:
            self.sidebar_list.setCurrentRow(selected_row)
        else:
            self.sidebar_list.clearSelection()

    def _set_current_directory(self, path, push_history=False):
        candidate = _normalize_existing_path(path)
        if sys.platform == "win32" and _windows_path_is_network(candidate):
            self.location_label.setText(f"Ignoring network drive: {candidate}")
            return False

        normalized = _normalize_existing_directory(candidate)
        if not normalized:
            self.location_label.setText("Folder not found")
            return False

        if push_history:
            key = self._path_key(normalized)
            current = ""
            if 0 <= self._history_index < len(self._history):
                current = self._path_key(self._history[self._history_index])
            if key != current:
                self._history = self._history[: self._history_index + 1]
                self._history.append(normalized)
                self._history_index = len(self._history) - 1
        elif self._history_index < 0:
            self._history = [normalized]
            self._history_index = 0

        self.current_directory = normalized
        self.path_input.setText(normalized)
        self._refresh_entries()
        self._populate_sidebar()
        self._update_nav_buttons()
        return True

    def _refresh_entries(self):
        self.entries_list.clear()
        folder_count = 0
        file_count = 0
        try:
            entries = []
            with os.scandir(self.current_directory) as it:
                for entry in it:
                    try:
                        is_dir = bool(entry.is_dir(follow_symlinks=False))
                    except Exception:
                        is_dir = False
                    try:
                        is_file = bool(entry.is_file(follow_symlinks=False))
                    except Exception:
                        is_file = False

                    if is_dir:
                        folder_count += 1
                        entries.append((0, entry.name.lower(), entry.name, entry.path, "dir"))
                        continue
                    if not is_file:
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if not self.show_all_files_checkbox.isChecked() and ext not in self.audio_extensions:
                        continue
                    file_count += 1
                    entries.append((1, entry.name.lower(), entry.name, entry.path, "file"))

            for _sort_group, _sort_name, display_name, full_path, kind in sorted(entries):
                item = QListWidgetItem(display_name)
                item.setData(Qt.UserRole, full_path)
                item.setData(Qt.UserRole + 1, kind)
                item.setIcon(self._icon_for_path(full_path, kind))
                self.entries_list.addItem(item)
            self.location_label.setText(
                f"{folder_count} folder(s), {file_count} visible file(s) in {self.current_directory}"
            )
        except Exception as exc:
            self.location_label.setText(f"Could not open {self.current_directory}: {exc}")

        self._update_browser_buttons()

    def _update_nav_buttons(self):
        self.back_btn.setEnabled(self._history_index > 0)
        parent_dir = os.path.dirname(os.path.normpath(self.current_directory))
        can_go_up = bool(parent_dir) and self._path_key(parent_dir) != self._path_key(self.current_directory)
        self.up_btn.setEnabled(can_go_up)

    def _update_browser_buttons(self):
        selected_items = list(self.entries_list.selectedItems() or [])
        has_selection = bool(selected_items)
        has_folder = any(str(item.data(Qt.UserRole + 1) or "") == "dir" for item in selected_items)
        self.open_btn.setEnabled(has_folder)
        self.add_selected_btn.setEnabled(has_selection)
        self.add_current_folder_btn.setEnabled(bool(self.current_directory and os.path.isdir(self.current_directory)))

    def _update_selection_buttons(self):
        has_selection = bool(self.selection_list.selectedItems())
        self.remove_selected_btn.setEnabled(has_selection)
        self.clear_selection_btn.setEnabled(self.selection_list.count() > 0)
        self.select_btn.setEnabled(self.selection_list.count() > 0)

    def _update_selection_summary(self):
        count = self.selection_list.count()
        if count <= 0:
            self.selection_summary_label.setText("No files or folders selected")
        elif count == 1:
            self.selection_summary_label.setText("1 item selected")
        else:
            self.selection_summary_label.setText(f"{count} items selected")
        self._update_selection_buttons()

    def _open_sidebar_item(self, item):
        path = str(item.data(Qt.UserRole) or "").strip() if item is not None else ""
        if path:
            self._set_current_directory(path, push_history=True)

    def _open_selected_folder(self):
        folders = []
        for item in list(self.entries_list.selectedItems() or []):
            if str(item.data(Qt.UserRole + 1) or "") == "dir":
                path = str(item.data(Qt.UserRole) or "").strip()
                if path:
                    folders.append(path)
        if folders:
            self._set_current_directory(folders[0], push_history=True)

    def _activate_entry_item(self, item):
        if item is None:
            return
        kind = str(item.data(Qt.UserRole + 1) or "")
        path = str(item.data(Qt.UserRole) or "").strip()
        if not path:
            return
        if kind == "dir":
            self._set_current_directory(path, push_history=True)
            return
        self._add_paths([path])

    def _add_paths(self, paths):
        changed = False
        for path in list(paths or []):
            normalized = _normalize_existing_path(path)
            if not normalized or not os.path.exists(normalized):
                continue
            if sys.platform == "win32" and _windows_path_is_network(normalized):
                continue
            key = self._path_key(normalized)
            if key in self._selected_keys:
                continue
            self._selected_keys.add(key)
            item = QListWidgetItem(normalized)
            item.setData(Qt.UserRole, normalized)
            item.setIcon(self._icon_for_path(normalized, "dir" if os.path.isdir(normalized) else "file"))
            self.selection_list.addItem(item)
            changed = True
        if changed:
            self._update_selection_summary()

    def _add_selected_entries(self):
        paths = []
        for item in list(self.entries_list.selectedItems() or []):
            path = str(item.data(Qt.UserRole) or "").strip()
            if path:
                paths.append(path)
        self._add_paths(paths)

    def _add_current_folder(self):
        if self.current_directory:
            self._add_paths([self.current_directory])

    def _remove_selected_paths(self):
        rows = sorted({self.selection_list.row(item) for item in list(self.selection_list.selectedItems() or [])}, reverse=True)
        for row in rows:
            item = self.selection_list.takeItem(row)
            if item is None:
                continue
            path = str(item.data(Qt.UserRole) or "").strip()
            key = self._path_key(path)
            self._selected_keys.discard(key)
        self._update_selection_summary()

    def _clear_selection(self):
        self.selection_list.clear()
        self._selected_keys.clear()
        self._update_selection_summary()

    def _go_back(self):
        if self._history_index <= 0:
            return
        self._history_index -= 1
        target = self._history[self._history_index]
        self.current_directory = target
        self.path_input.setText(target)
        self._refresh_entries()
        self._populate_sidebar()
        self._update_nav_buttons()

    def _go_up(self):
        parent_dir = os.path.dirname(os.path.normpath(self.current_directory))
        if not parent_dir or self._path_key(parent_dir) == self._path_key(self.current_directory):
            return
        self._set_current_directory(parent_dir, push_history=True)

    def _go_home(self):
        self._set_current_directory(os.path.expanduser("~"), push_history=True)

    def _go_to_entered_path(self):
        raw_path = str(self.path_input.text() or "").strip()
        normalized = _normalize_existing_path(raw_path)
        if not normalized:
            self.location_label.setText("Enter a folder or file path")
            return
        if sys.platform == "win32" and _windows_path_is_network(normalized):
            self.location_label.setText(f"Network drives are ignored: {normalized}")
            return
        if os.path.isdir(normalized):
            self._set_current_directory(normalized, push_history=True)
            return
        if os.path.isfile(normalized):
            parent_dir = os.path.dirname(normalized)
            if self._set_current_directory(parent_dir, push_history=True):
                self._select_entry_path(normalized)
            return
        self.location_label.setText(f"Path not found: {normalized}")

    def _select_entry_path(self, path):
        target_key = self._path_key(path)
        self.entries_list.clearSelection()
        for row in range(self.entries_list.count()):
            item = self.entries_list.item(row)
            item_path = str(item.data(Qt.UserRole) or "").strip()
            if self._path_key(item_path) == target_key:
                item.setSelected(True)
                self.entries_list.scrollToItem(item)
                break
        self._update_browser_buttons()

    def accept(self):
        if self.selection_list.count() <= 0 and self.entries_list.selectedItems():
            self._add_selected_entries()
        paths = []
        seen = set()
        for row in range(self.selection_list.count()):
            item = self.selection_list.item(row)
            if item is None:
                continue
            path = _normalize_existing_path(item.data(Qt.UserRole))
            if not path or not os.path.exists(path):
                continue
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        if not paths:
            QMessageBox.information(self, "Select Files or Folders", "Add one or more files or folders before continuing.")
            return
        self.accepted_paths = paths
        super().accept()
