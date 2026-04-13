import json
import os
import shutil
import sys
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mixsplitr_ui_cards import GlassPage
from mixsplitr_ui_browser import choose_existing_directory, choose_open_file, choose_save_file
from mixsplitr_ui_effects import FloatingOverlayScrollBar
from mixsplitr_ui_workers import SessionEditorLoaderThread
from mixsplitr_devtools import annotate_widget

mixsplitr_manifest = None
mixsplitr_session = None
_lazy_import_backend = None


def install_history_methods(
    app_cls,
    *,
    manifest_module,
    session_module,
    lazy_import_backend,
):
    global mixsplitr_manifest, mixsplitr_session, _lazy_import_backend

    mixsplitr_manifest = manifest_module
    mixsplitr_session = session_module
    _lazy_import_backend = lazy_import_backend

    methods = (
        create_history_page,
        _set_history_status,
        _history_manifest_directory,
        _load_manifest_from_path,
        _history_mode_badge,
        _history_show_id_source_enabled,
        _history_id_source_badge,
        _history_preferred_source_value,
        _history_track_source_value,
        _history_manifest_source_badge,
        _selected_history_rows,
        _selected_history_row,
        _update_history_action_buttons,
        _refresh_session_history_list,
        _render_manifest_details_text,
        view_selected_session,
        open_selected_session_editor,
        _set_history_actions_enabled,
        _on_session_editor_loader_finished,
        compare_selected_sessions,
        preview_reorganize_selected_session,
        apply_reorganize_selected_session,
        preview_rollback_selected_session,
        apply_rollback_selected_session,
        apply_selected_session_safe,
        import_session_record,
        export_selected_session_record,
        _is_safe_history_record_path,
        delete_selected_session_record,
    )
    for method in methods:
        setattr(app_cls, method.__name__, method)


def create_history_page(self):
    page = GlassPage()
    annotate_widget(page, role="Session History Page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 28, 28, 28)
    layout.setSpacing(20)
    self._track_layout_metrics(layout, base_margins=(28, 28, 28, 28), base_spacing=20)
    header_container = QWidget()
    header_row = QHBoxLayout(header_container)
    if sys.platform == "win32":
        header_row.setContentsMargins(0, 10, 0, 2)
    elif sys.platform == "darwin":
        header_row.setContentsMargins(0, 14, 0, 2)
    else:
        header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(0)
    header = QLabel("Session History")
    header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    self._track_scalable(header, 28, "font-weight: 400;")
    header_row.addWidget(header, 0, Qt.AlignLeft | Qt.AlignVCenter)
    header_row.addStretch(1)
    if sys.platform == "win32":
        header_container.setMinimumHeight(62)
    elif sys.platform == "darwin":
        header_container.setMinimumHeight(66)
    else:
        self._track_min_height(header_container, 52)
    layout.addWidget(header_container)
    if self.top_card_dragstrip_buffer > 0:
        layout.addSpacing(self.top_card_dragstrip_buffer)

    self.history_rows = []

    sessions_card = self.create_base_card(
        "Past Sessions",
        "View, compare, sort, undo, and manage your previous processing sessions.",
    )
    sessions_layout = QVBoxLayout()
    sessions_layout.setSpacing(12)
    self._track_layout_metrics(sessions_layout, base_margins=(0, 0, 0, 0), base_spacing=12)

    self.history_manifest_dir_label = QLabel("Manifest Folder: -")
    self.history_manifest_dir_label.setStyleSheet("color: #C4C7C5;")
    sessions_layout.addWidget(self.history_manifest_dir_label)

    history_list_cell = QFrame()
    history_list_cell.setObjectName("HistorySessionListCell")
    history_list_cell.setStyleSheet(
        """
        QFrame#HistorySessionListCell {
            background-color: #22262B;
            border: none;
            border-radius: 8px;
        }
        """
    )
    history_list_cell_layout = QVBoxLayout(history_list_cell)
    history_list_cell_layout.setContentsMargins(10, 10, 10, 10)
    history_list_cell_layout.setSpacing(0)
    self._track_layout_metrics(
        history_list_cell_layout,
        base_margins=(10, 10, 10, 10),
        base_spacing=0,
    )

    self.history_list = QListWidget()
    self.history_list.setObjectName("HistorySessionList")
    self.history_list.setSelectionMode(QListWidget.ExtendedSelection)
    self.history_list.setMinimumHeight(180)
    self._track_min_height(self.history_list, 180)
    self.history_list.setUniformItemSizes(True)
    self.history_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    self.history_list.setVerticalScrollBar(
        FloatingOverlayScrollBar(Qt.Vertical, self.history_list, always_visible=True)
    )
    self.history_list.verticalScrollBar().setSingleStep(20)
    self.history_list.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#34373C")
    self.history_list.setFrameShape(QFrame.NoFrame)
    self.history_list.setStyleSheet(
        """
        QListWidget#HistorySessionList {
            background-color: transparent;
            border: none;
            padding: 0px;
            color: #EBE7E1;
        }
        QListWidget#HistorySessionList::item {
            padding: 10px;
            border-radius: 6px;
        }
        QListWidget#HistorySessionList::item:selected {
            background-color: #2B4E7A;
            color: #EBE7E1;
        }
        QListWidget#HistorySessionList::item:hover:!selected {
            background-color: #292E34;
        }
        """
    )
    self.history_list.viewport().setAutoFillBackground(False)
    self.history_list.viewport().setStyleSheet("background: transparent;")
    history_list_cell_layout.addWidget(self.history_list)
    sessions_layout.addWidget(history_list_cell)

    history_row_1 = QHBoxLayout()
    history_row_1.setSpacing(10)
    self._track_layout_metrics(history_row_1, base_margins=(0, 0, 0, 0), base_spacing=10)
    self.history_refresh_btn = QPushButton("Refresh")
    self.history_refresh_btn.setProperty("class", "SecondaryButton")
    self.history_refresh_btn.setToolTip("Reload the list of past sessions")
    self.history_view_btn = QPushButton("View Details")
    self.history_view_btn.setProperty("class", "SecondaryButton")
    self.history_view_btn.setToolTip("See full details of the selected session")
    self.history_compare_btn = QPushButton("Compare Sessions")
    self.history_compare_btn.setProperty("class", "SecondaryButton")
    self.history_compare_btn.setToolTip("Select two sessions to see what changed between them")
    self.history_edit_session_btn = QPushButton("Edit Session")
    self.history_edit_session_btn.setProperty("class", "SecondaryButton")
    self.history_edit_session_btn.setToolTip("Open an interactive editor to modify session data")
    self.history_reorganize_preview_btn = QPushButton("Preview File Sort")
    self.history_reorganize_preview_btn.setProperty("class", "SecondaryButton")
    self.history_reorganize_preview_btn.setToolTip(
        "Preview how files would be sorted into Artist/Album folders without moving anything"
    )
    self.history_reorganize_apply_btn = QPushButton("Sort Files")
    self.history_reorganize_apply_btn.setProperty("class", "SecondaryButton")
    self.history_reorganize_apply_btn.setToolTip(
        "Move and rename files into Artist/Album folders based on session data"
    )
    history_row_1.addWidget(self.history_refresh_btn)
    history_row_1.addWidget(self.history_view_btn)
    history_row_1.addWidget(self.history_compare_btn)
    history_row_1.addWidget(self.history_edit_session_btn)
    history_row_1.addWidget(self.history_reorganize_preview_btn)
    history_row_1.addWidget(self.history_reorganize_apply_btn)
    sessions_layout.addLayout(history_row_1)

    history_row_2 = QHBoxLayout()
    history_row_2.setSpacing(10)
    self._track_layout_metrics(history_row_2, base_margins=(0, 0, 0, 0), base_spacing=10)
    self.history_rollback_preview_btn = QPushButton("Preview Undo")
    self.history_rollback_preview_btn.setProperty("class", "SecondaryButton")
    self.history_rollback_preview_btn.setToolTip(
        "Preview what would be undone — see which files would be moved back"
    )
    self.history_rollback_apply_btn = QPushButton("Undo Changes")
    self.history_rollback_apply_btn.setProperty("class", "SecondaryButton")
    self.history_rollback_apply_btn.setToolTip(
        "Undo a previous file sort — move files back to their original locations"
    )
    self.history_apply_safe_btn = QPushButton("Apply Session")
    self.history_apply_safe_btn.setProperty("class", "SecondaryButton")
    self.history_apply_safe_btn.setToolTip(
        "Safely re-apply a session's file operations (can be undone later)"
    )
    self.history_import_btn = QPushButton("Import Session")
    self.history_import_btn.setProperty("class", "SecondaryButton")
    self.history_import_btn.setToolTip("Load a session file from disk into your history")
    self.history_export_btn = QPushButton("Export Session")
    self.history_export_btn.setProperty("class", "SecondaryButton")
    self.history_export_btn.setToolTip("Save the selected session to a file for backup or sharing")
    self.history_delete_btn = QPushButton("Delete Session")
    self.history_delete_btn.setProperty("class", "SecondaryButton")
    self.history_delete_btn.setToolTip(
        "Remove the session record (your audio files are not affected)"
    )
    history_row_2.addWidget(self.history_rollback_preview_btn)
    history_row_2.addWidget(self.history_rollback_apply_btn)
    history_row_2.addWidget(self.history_apply_safe_btn)
    history_row_2.addWidget(self.history_import_btn)
    history_row_2.addWidget(self.history_export_btn)
    history_row_2.addWidget(self.history_delete_btn)
    sessions_layout.addLayout(history_row_2)

    history_action_buttons = [
        self.history_refresh_btn,
        self.history_view_btn,
        self.history_compare_btn,
        self.history_edit_session_btn,
        self.history_reorganize_preview_btn,
        self.history_reorganize_apply_btn,
        self.history_rollback_preview_btn,
        self.history_rollback_apply_btn,
        self.history_apply_safe_btn,
        self.history_import_btn,
        self.history_export_btn,
        self.history_delete_btn,
    ]
    for button in history_action_buttons:
        button.setProperty("historyActionCell", True)
        self._apply_history_action_cell_style(button)
        self._refresh_widget_style(button)

    self.history_status_label = QLabel("History: Ready")
    self.history_status_label.setStyleSheet("color: #C4C7C5;")
    sessions_layout.addWidget(self.history_status_label)

    sessions_card.layout().addLayout(sessions_layout)
    layout.addWidget(sessions_card)

    output_card = self.create_base_card(
        "Action Output",
        "Detailed results for session-history actions.",
    )
    self.history_output = QTextEdit()
    self.history_output.setReadOnly(True)
    self.history_output.setMinimumHeight(220)
    self._track_min_height(self.history_output, 220)
    output_card.layout().addWidget(self.history_output)
    layout.addWidget(output_card)

    self._apply_card_line_surfaces_on_page(page)
    return self._wrap_page_in_transparent_scroll(page)


def _set_history_status(self, text, error=False):
    color = "#C4C7C5" if not error else "#E45D5D"
    self.history_status_label.setStyleSheet(f"color: {color};")
    self.history_status_label.setText(f"History: {text}")


def _history_manifest_directory(self):
    configured = self._normalized_path(self.manifest_dir_input.text())
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured
    default_dir = self._default_manifest_directory()
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def _load_manifest_from_path(self, manifest_path):
    if mixsplitr_manifest and hasattr(mixsplitr_manifest, "load_manifest"):
        manifest = mixsplitr_manifest.load_manifest(manifest_path)
        if isinstance(manifest, dict):
            return manifest
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _history_mode_badge(self, mode_value):
    mode_text = str(mode_value or "").strip()
    if mode_text == "acrcloud":
        return "🎵"
    if mode_text == "split_only_no_id":
        return "✂️"
    if mode_text == "auto_tracklist_no_manual":
        return "🧾"
    return "🔍"


def _history_show_id_source_enabled(self):
    check = getattr(self, "show_id_source_check", None)
    if check is not None:
        try:
            return bool(check.isChecked())
        except Exception:
            pass
    return True


def _history_id_source_badge(self, source_value):
    if not self._history_show_id_source_enabled():
        return ""

    raw = str(source_value or "").strip().lower()
    if not raw:
        return ""
    normalized = raw.replace(" ", "_")
    if normalized.startswith("acrcloud"):
        normalized = "acrcloud"
    elif normalized.startswith("shazam"):
        normalized = "shazam"
    elif normalized in ("musicbrainz", "musicbrainz_only", "mb"):
        normalized = "musicbrainz"
    elif normalized.startswith("acoustid"):
        normalized = "acoustid"
    elif normalized in ("dual", "dual_best_match"):
        normalized = "dual"
    elif normalized in ("split_only", "split_only_no_id"):
        normalized = "split_only"
    elif normalized in ("filename_mb", "mb_text_search"):
        normalized = "filename_mb"
    elif normalized in ("session_editor",):
        normalized = "session_editor"
    elif normalized in ("unknown", "none"):
        return ""

    badge_map = {
        "acrcloud": "[ACR]",
        "shazam": "[SHA]",
        "musicbrainz": "[MB]",
        "acoustid": "[AID]",
        "dual": "[DUAL]",
        "split_only": "[SPLIT]",
        "filename_mb": "[FILE]",
        "session_editor": "[EDIT]",
    }
    return str(badge_map.get(normalized, "")).strip()


def _history_preferred_source_value(self, source_value, winner_src=None):
    raw_source = str(source_value or "").strip()
    raw_winner = str(winner_src or "").strip()
    if not raw_winner:
        return raw_source

    normalized = raw_source.lower().replace(" ", "_")
    if normalized in ("", "unknown", "none", "musicbrainz", "musicbrainz_only", "mb"):
        return raw_winner
    return raw_source


def _history_track_source_value(self, track):
    track_data = track if isinstance(track, dict) else {}
    identification = dict(track_data.get("identification") or {})
    backend_candidates = identification.get("backend_candidates") or {}
    source_value = (
        identification.get("chosen_source")
        or track_data.get("identification_method")
    )
    return self._history_preferred_source_value(
        source_value,
        backend_candidates.get("winner_src"),
    )


def _history_manifest_source_badge(self, manifest):
    if not self._history_show_id_source_enabled():
        return ""
    tracks = list((manifest or {}).get("tracks", []) or [])
    unique_badges = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        badge = self._history_id_source_badge(self._history_track_source_value(track))
        if badge and badge not in unique_badges:
            unique_badges.append(badge)
    if not unique_badges:
        return ""
    if len(unique_badges) == 1:
        return unique_badges[0]
    return "[MIXED]"


def _selected_history_rows(self):
    item_paths = [str(item.data(Qt.UserRole) or "") for item in self.history_list.selectedItems()]
    rows_by_path = {str(row.get("filepath") or ""): row for row in self.history_rows}
    selected = []
    for path in item_paths:
        row = rows_by_path.get(path)
        if row:
            selected.append(row)
    return selected


def _selected_history_row(self):
    rows = self._selected_history_rows()
    if len(rows) != 1:
        return None
    return rows[0]


def _update_history_action_buttons(self):
    selected_count = len(self.history_list.selectedItems())
    backend_ready = mixsplitr_manifest is not None
    editor_ready = bool(mixsplitr_session and hasattr(mixsplitr_session, "_build_session_editor_cache"))
    has_single = backend_ready and selected_count == 1
    has_pair = backend_ready and selected_count == 2

    self.history_refresh_btn.setEnabled(True)
    self.history_import_btn.setEnabled(bool(backend_ready))
    self.history_view_btn.setEnabled(has_single)
    self.history_compare_btn.setEnabled(has_pair)
    self.history_edit_session_btn.setEnabled(bool(editor_ready and has_single))
    self.history_reorganize_preview_btn.setEnabled(has_single)
    self.history_reorganize_apply_btn.setEnabled(has_single)
    self.history_rollback_preview_btn.setEnabled(has_single)
    self.history_rollback_apply_btn.setEnabled(has_single)
    self.history_apply_safe_btn.setEnabled(has_single)
    self.history_export_btn.setEnabled(has_single)
    self.history_delete_btn.setEnabled(has_single)


def _refresh_session_history_list(self, select_path=""):
    if not hasattr(self, "history_list"):
        return

    manifest_dir = self._history_manifest_directory()
    self.history_manifest_dir_label.setText(f"Manifest Folder: {manifest_dir}")

    desired_path = self._normalized_path(select_path) if isinstance(select_path, str) and select_path else ""
    entries = []
    try:
        for entry in os.scandir(manifest_dir):
            if not entry.is_file() or not entry.name.lower().endswith(".json"):
                continue
            stat = entry.stat()
            entries.append((stat.st_mtime, os.path.abspath(entry.path)))
    except Exception:
        entries = []
    entries.sort(key=lambda row: row[0], reverse=True)

    self.history_list.blockSignals(True)
    self.history_list.clear()
    self.history_rows = []
    selected_row = -1

    for _index, (_mtime, path) in enumerate(entries):
        manifest = self._load_manifest_from_path(path)
        if not manifest:
            continue

        timestamp = str(manifest.get("timestamp") or "")
        timestamp_display = timestamp[:19].replace("T", " ") if timestamp else "unknown time"
        session_name = str(manifest.get("session_name") or os.path.basename(path))
        mode_value = str(manifest.get("mode") or "")
        total_tracks = int((manifest.get("summary") or {}).get("total_tracks", 0) or 0)
        input_file = str((manifest.get("input") or {}).get("file") or "")
        input_name = os.path.basename(input_file) if input_file else "unknown input"

        row = {
            "filepath": path,
            "filename": os.path.basename(path),
            "session_name": session_name,
            "timestamp": timestamp,
            "mode": mode_value,
            "total_tracks": total_tracks,
            "input_file": input_file,
        }
        self.history_rows.append(row)

        source_badge = self._history_manifest_source_badge(manifest)
        source_suffix = f"   {source_badge}" if source_badge else ""

        display = (
            f"{self._history_mode_badge(mode_value)} {session_name}   "
            f"{timestamp_display}   {total_tracks} tracks   {input_name}{source_suffix}"
        )
        item = QListWidgetItem(display)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.history_list.addItem(item)
        if desired_path and os.path.abspath(path) == desired_path:
            selected_row = self.history_list.count() - 1

    if selected_row >= 0:
        self.history_list.setCurrentRow(selected_row)
    elif self.history_list.count() > 0:
        self.history_list.setCurrentRow(0)
    self.history_list.blockSignals(False)

    if self.history_list.count() == 0:
        self.history_output.setPlainText("No session records found in the configured Session History folder.")
        self._set_history_status("No session records found")
    else:
        self._set_history_status(f"Loaded {self.history_list.count()} session record(s)")
    self._update_history_action_buttons()


def _render_manifest_details_text(self, manifest):
    summary = manifest.get("summary") or {}
    lines = []
    lines.append("Session Details")
    lines.append("=" * 60)
    lines.append(f"Session:  {manifest.get('session_name', 'unknown')}")
    lines.append(f"Date:     {str(manifest.get('timestamp', ''))[:19].replace('T', ' ')}")
    lines.append(f"Mode:     {manifest.get('mode', 'unknown')}")
    source_badge = self._history_manifest_source_badge(manifest)
    if source_badge:
        lines.append(f"ID Source:{' '}{source_badge}")
    lines.append(f"Version:  {manifest.get('version', 'unknown')}")
    input_file = str((manifest.get("input") or {}).get("file") or "")
    lines.append(f"Input:    {input_file or 'unknown'}")
    lines.append("")
    lines.append(f"Tracks:   {summary.get('total_tracks', 0)} total")
    lines.append(f"          {summary.get('identified', 0)} identified")
    lines.append(f"          {summary.get('unidentified', 0)} unidentified")
    lines.append(f"          {summary.get('manual', 0)} manual")
    lines.append(f"          {summary.get('skipped', 0)} skipped")
    lines.append(f"Outputs:  {len(manifest.get('outputs', []) or [])} files")

    tracks = list(manifest.get("tracks", []) or [])
    if tracks:
        lines.append("")
        lines.append("Track Preview (first 20)")
        lines.append("-" * 60)
        for track in tracks[:20]:
            number = track.get("track_number", "?")
            artist = str(track.get("artist") or "").strip()
            title = str(track.get("title") or "").strip()
            status = str(track.get("status") or "identified").strip().lower()
            if status == "unidentified":
                lines.append(f"{number:>2}. [unidentified] {title or 'Unknown'}")
            elif status == "skipped":
                lines.append(f"{number:>2}. [skipped] {artist or 'Unknown'} - {title or 'Unknown'}")
            else:
                lines.append(f"{number:>2}. {artist or 'Unknown'} - {title or 'Unknown'}")
    return "\n".join(lines)


def view_selected_session(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to view", error=True)
        return
    manifest = self._load_manifest_from_path(row["filepath"])
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return
    self.history_output.setPlainText(self._render_manifest_details_text(manifest))
    self._set_history_status(f"Viewing {row.get('session_name', 'session')}")


def open_selected_session_editor(self):
    global mixsplitr_session

    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record for Session Editor", error=True)
        return

    manifest_path = str(row.get("filepath") or "")
    manifest = self._load_manifest_from_path(manifest_path)
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return

    mixsplitr_session = _lazy_import_backend("mixsplitr_session")
    if not mixsplitr_session or not hasattr(mixsplitr_session, "_build_session_editor_cache"):
        self._set_history_status("Session editor is unavailable", error=True)
        return

    panel = getattr(self, "track_editor_panel", None)
    if panel is None:
        self._set_history_status("Track Editor tab is unavailable", error=True)
        return
    if panel.has_unsaved_changes():
        proceed = QMessageBox.question(
            self,
            "Replace Track Editor Session",
            "Opening this session will replace unsaved Track Editor edits. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if proceed != QMessageBox.Yes:
            return

    self._session_editor_pending_manifest = manifest
    self._session_editor_pending_manifest_path = manifest_path
    self._session_editor_pending_session_name = row.get("session_name", "session")

    self._set_history_actions_enabled(False)
    track_count = len(manifest.get("tracks", []) or [])
    self._set_history_status(f"Loading session editor ({track_count} tracks)...")
    self.history_output.setPlainText(
        f"Building editor cache for {track_count} tracks.\n"
        "Please wait..."
    )

    loader = SessionEditorLoaderThread(
        manifest,
        manifest_path,
        session_module_getter=lambda: mixsplitr_session,
        parent=self,
    )
    loader.progress_update.connect(lambda msg: self._set_history_status(msg))
    loader.finished.connect(self._on_session_editor_loader_finished)
    self._session_editor_loader_thread = loader
    loader.start()


def _set_history_actions_enabled(self, enabled):
    for btn_attr in (
        "history_edit_session_btn",
        "history_view_btn",
        "history_compare_btn",
        "history_reorganize_preview_btn",
        "history_reorganize_apply_btn",
        "history_rollback_preview_btn",
        "history_rollback_apply_btn",
        "history_apply_safe_btn",
        "history_export_btn",
        "history_delete_btn",
        "history_import_btn",
    ):
        btn = getattr(self, btn_attr, None)
        if btn is not None:
            btn.setEnabled(bool(enabled))


def _on_session_editor_loader_finished(self, success, cache_data, error_message):
    loader = getattr(self, "_session_editor_loader_thread", None)
    if loader is not None:
        try:
            loader.deleteLater()
        except Exception:
            pass
        self._session_editor_loader_thread = None

    self._set_history_actions_enabled(True)
    self._update_history_action_buttons()

    if not success:
        self._set_history_status(str(error_message or "Session editor loading failed"), error=True)
        self.history_output.setPlainText(str(error_message or ""))
        return

    manifest = getattr(self, "_session_editor_pending_manifest", None)
    manifest_path = getattr(self, "_session_editor_pending_manifest_path", "")
    session_name = getattr(self, "_session_editor_pending_session_name", "session")

    panel = getattr(self, "track_editor_panel", None)
    if panel is None:
        self._set_history_status("Track Editor tab is unavailable", error=True)
        return

    panel.load_session_editor(cache_data, "", manifest, manifest_path)
    self.switch_page(getattr(self, "track_editor_page_index", 0))
    self._set_history_status(f"Editing session: {session_name}")
    self.status_label.setText("Status: Session record opened in Track Editor")


def compare_selected_sessions(self):
    rows = self._selected_history_rows()
    if len(rows) != 2:
        self._set_history_status("Select exactly two session records to compare", error=True)
        return
    if not mixsplitr_manifest or not hasattr(mixsplitr_manifest, "compare_manifests"):
        self._set_history_status("Session comparison is unavailable", error=True)
        return

    manifest_a = self._load_manifest_from_path(rows[0]["filepath"])
    manifest_b = self._load_manifest_from_path(rows[1]["filepath"])
    if not manifest_a or not manifest_b:
        self._set_history_status("Could not load one or both selected records", error=True)
        return

    diff = mixsplitr_manifest.compare_manifests(manifest_a, manifest_b)
    lines = []
    lines.append("Session Comparison")
    lines.append("=" * 60)
    lines.append(f"A: {rows[0].get('session_name', 'unknown')}")
    lines.append(f"B: {rows[1].get('session_name', 'unknown')}")
    lines.append("")
    lines.append(f"Metadata changes: {int(diff.get('metadata_changes', 0) or 0)}")
    lines.append(f"Files added:      {len(diff.get('files_added', []) or [])}")
    lines.append(f"Files removed:    {len(diff.get('files_removed', []) or [])}")

    changed = list(diff.get("tracks_changed", []) or [])
    if changed:
        lines.append("")
        lines.append("Changed tracks (first 20)")
        lines.append("-" * 60)
        for change in changed[:20]:
            lines.append(
                f"Track {change.get('track_number', '?')}: "
                f"{change.get('old', '')}  ->  {change.get('new', '')}"
            )
    self.history_output.setPlainText("\n".join(lines))
    self._set_history_status("Compared selected session records")


def preview_reorganize_selected_session(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record for reorganization preview", error=True)
        return
    if not mixsplitr_manifest or not hasattr(mixsplitr_manifest, "reorganize_from_manifest"):
        self._set_history_status("Reorganize tool is unavailable", error=True)
        return

    manifest = self._load_manifest_from_path(row["filepath"])
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return
    preview = mixsplitr_manifest.reorganize_from_manifest(manifest, dry_run=True)

    lines = []
    lines.append("Reorganize Preview (Dry Run)")
    lines.append("=" * 60)
    lines.append(f"Session: {row.get('session_name', 'unknown')}")
    lines.append(f"Would rename: {len(preview.get('changes', []) or [])}")
    lines.append(f"Would clean folders: {len(preview.get('cleaned', []) or [])}")
    lines.append(f"Issues: {len(preview.get('errors', []) or [])}")
    changes = list(preview.get("changes", []) or [])
    if changes:
        lines.append("")
        lines.append("Planned changes (first 20)")
        lines.append("-" * 60)
        for change in changes[:20]:
            old_path = str(change.get("old_path") or "")
            new_path = str(change.get("new_path") or "")
            lines.append(f"{os.path.basename(old_path)}  ->  {os.path.basename(new_path)}")
    errors = list(preview.get("errors", []) or [])
    if errors:
        lines.append("")
        lines.append("Issues (first 20)")
        lines.append("-" * 60)
        lines.extend(str(err) for err in errors[:20])

    self.history_output.setPlainText("\n".join(lines))
    self._set_history_status("Reorganize preview complete")


def apply_reorganize_selected_session(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to reorganize", error=True)
        return
    if not mixsplitr_manifest or not hasattr(mixsplitr_manifest, "reorganize_from_manifest"):
        self._set_history_status("Reorganize tool is unavailable", error=True)
        return

    manifest = self._load_manifest_from_path(row["filepath"])
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return

    preview = mixsplitr_manifest.reorganize_from_manifest(manifest, dry_run=True)
    change_count = len(preview.get("changes", []) or [])
    if change_count <= 0:
        self.history_output.setPlainText("No reorganization changes needed for this session record.")
        self._set_history_status("Nothing to reorganize")
        return

    confirm = QMessageBox.question(
        self,
        "Apply Reorganize",
        f"Apply reorganization for {change_count} file(s)?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        self._set_history_status("Reorganize cancelled")
        return

    results = mixsplitr_manifest.reorganize_from_manifest(manifest, dry_run=False)
    moved = len(results.get("changes", []) or [])
    cleaned = len(results.get("cleaned", []) or [])
    errors = list(results.get("errors", []) or [])

    lines = []
    lines.append("Reorganize Apply")
    lines.append("=" * 60)
    lines.append(f"Session: {row.get('session_name', 'unknown')}")
    lines.append(f"Moved: {moved}")
    lines.append(f"Cleaned folders: {cleaned}")
    lines.append(f"Errors: {len(errors)}")
    if errors:
        lines.append("")
        lines.append("Errors (first 20)")
        lines.append("-" * 60)
        lines.extend(str(err) for err in errors[:20])
    self.history_output.setPlainText("\n".join(lines))
    self._set_history_status(
        "Reorganize applied" if moved or cleaned else "Reorganize finished with no file moves"
    )


def preview_rollback_selected_session(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record for rollback preview", error=True)
        return
    if not mixsplitr_manifest or not hasattr(mixsplitr_manifest, "rollback_from_manifest"):
        self._set_history_status("Rollback tool is unavailable", error=True)
        return

    manifest = self._load_manifest_from_path(row["filepath"])
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return

    preview = mixsplitr_manifest.rollback_from_manifest(manifest, dry_run=True)
    would_delete = list(preview.get("would_delete", []) or [])
    manifest_files = list(preview.get("manifest_files", []) or [])

    lines = []
    lines.append("Rollback Preview (Dry Run)")
    lines.append("=" * 60)
    lines.append(f"Session: {row.get('session_name', 'unknown')}")
    lines.append(f"Manifest files tracked: {len(manifest_files)}")
    lines.append(f"Would delete extra files: {len(would_delete)}")
    if manifest_files:
        lines.append("")
        lines.append("Manifest files (first 20)")
        lines.append("-" * 60)
        lines.extend(os.path.basename(path) for path in manifest_files[:20])
    if would_delete:
        lines.append("")
        lines.append("Would delete (first 20)")
        lines.append("-" * 60)
        lines.extend(os.path.basename(path) for path in would_delete[:20])
    self.history_output.setPlainText("\n".join(lines))
    self._set_history_status("Rollback preview complete")


def apply_rollback_selected_session(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to rollback", error=True)
        return
    if not mixsplitr_manifest or not hasattr(mixsplitr_manifest, "rollback_from_manifest"):
        self._set_history_status("Rollback tool is unavailable", error=True)
        return

    manifest = self._load_manifest_from_path(row["filepath"])
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return

    preview = mixsplitr_manifest.rollback_from_manifest(manifest, dry_run=True)
    would_delete = list(preview.get("would_delete", []) or [])
    if not would_delete:
        self.history_output.setPlainText("Rollback found no extra files to delete for this session scope.")
        self._set_history_status("No rollback delete actions required")
        return

    confirm = QMessageBox.question(
        self,
        "Apply Rollback",
        f"Delete {len(would_delete)} file(s) not in this session record?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        self._set_history_status("Rollback cancelled")
        return

    applied = mixsplitr_manifest.rollback_from_manifest(manifest, dry_run=False)
    deleted = len(applied.get("deleted", []) or [])
    errors = list(applied.get("errors", []) or [])

    rollback_record_path = None
    if mixsplitr_session and hasattr(mixsplitr_session, "_save_rollback_session_record"):
        try:
            rollback_record_path = str(mixsplitr_session._save_rollback_session_record(manifest, applied) or "")
        except Exception:
            rollback_record_path = ""

    lines = []
    lines.append("Rollback Apply")
    lines.append("=" * 60)
    lines.append(f"Session: {row.get('session_name', 'unknown')}")
    lines.append(f"Deleted: {deleted}")
    lines.append(f"Errors: {len(errors)}")
    if rollback_record_path:
        lines.append(f"Rollback record: {rollback_record_path}")
    if errors:
        lines.append("")
        lines.append("Errors (first 20)")
        lines.append("-" * 60)
        lines.extend(str(err) for err in errors[:20])
    self.history_output.setPlainText("\n".join(lines))

    if rollback_record_path:
        self._refresh_session_history_list(select_path=rollback_record_path)
    else:
        self._refresh_session_history_list()
    self._set_history_status("Rollback applied")


def apply_selected_session_safe(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to apply", error=True)
        return
    if not mixsplitr_session:
        self._set_history_status("Session apply is unavailable", error=True)
        return
    if not hasattr(mixsplitr_session, "_build_safe_apply_plan") or not hasattr(
        mixsplitr_session,
        "_save_applied_session_record",
    ):
        self._set_history_status("Safe apply helpers unavailable in this build", error=True)
        return

    manifest_path = row["filepath"]
    manifest = self._load_manifest_from_path(manifest_path)
    if not manifest:
        self._set_history_status("Could not load selected session record", error=True)
        return

    default_target = self._normalized_path(self.output_dir_input.text()) or self._default_output_directory()
    target_dir = choose_existing_directory(
        self,
        "Select Target Output Folder",
        default_target,
    )
    if not target_dir:
        self._set_history_status("Apply session cancelled")
        return

    preview = mixsplitr_session._build_safe_apply_plan(manifest, manifest_path, target_dir)
    planned = list(preview.get("plan", []) or [])
    missing = list(preview.get("missing", []) or [])
    conflicts = list(preview.get("conflicts", []) or [])
    already_present = list(preview.get("already_present", []) or [])

    lines = []
    lines.append("Apply Session Record (Safe)")
    lines.append("=" * 60)
    lines.append(f"Session: {row.get('session_name', 'unknown')}")
    lines.append(f"Target: {target_dir}")
    lines.append(f"Total tracks: {int(preview.get('total_tracks', 0) or 0)}")
    lines.append(f"Ready to copy: {len(planned)}")
    lines.append(f"Already present: {len(already_present)}")
    lines.append(f"Missing source: {len(missing)}")
    lines.append(f"Conflicts: {len(conflicts)}")

    if not planned and (missing or conflicts):
        self.history_output.setPlainText("\n".join(lines))
        self._set_history_status("Safe apply blocked by missing/conflicting files", error=True)
        return
    if not planned:
        self.history_output.setPlainText("\n".join(lines + ["", "Nothing to copy."]))
        self._set_history_status("Nothing to apply")
        return

    partial_mode = bool(missing or conflicts)
    if partial_mode:
        confirm = QMessageBox.question(
            self,
            "Apply Session (Partial)",
            "Missing/conflicting files detected. Apply only resolvable tracks?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
    else:
        confirm = QMessageBox.question(
            self,
            "Apply Session",
            f"Apply session and copy {len(planned)} file(s) to target folder?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
    if confirm != QMessageBox.Yes:
        self.history_output.setPlainText("\n".join(lines + ["", "Cancelled."]))
        self._set_history_status("Apply session cancelled")
        return

    copied = []
    errors = []
    applied_paths_by_track = {}
    for row_plan in planned:
        try:
            os.makedirs(os.path.dirname(row_plan["dest_path"]), exist_ok=True)
            shutil.copy2(row_plan["src_path"], row_plan["dest_path"])
            copied.append(row_plan)
            applied_paths_by_track[row_plan["track_number"]] = row_plan["dest_path"]
        except Exception as exc:
            errors.append(f"Track {row_plan.get('track_number', '?')}: {exc}")

    for row_present in already_present:
        applied_paths_by_track[row_present["track_number"]] = row_present["dest_path"]

    saved_record = ""
    try:
        saved = mixsplitr_session._save_applied_session_record(
            manifest,
            manifest_path,
            applied_paths_by_track,
            apply_meta={
                "partial_apply": partial_mode,
                "target_dir": target_dir,
                "copied_count": len(copied),
                "already_present_count": len(already_present),
                "missing_count": len(missing),
                "conflict_count": len(conflicts),
                "error_count": len(errors),
            },
        )
        saved_record = str(saved or "")
    except Exception:
        saved_record = ""

    lines.append("")
    lines.append("Apply Result")
    lines.append("-" * 60)
    lines.append(f"Copied: {len(copied)}")
    lines.append(f"Already present: {len(already_present)}")
    if partial_mode:
        lines.append(f"Skipped missing: {len(missing)}")
        lines.append(f"Skipped conflicts: {len(conflicts)}")
    lines.append(f"Errors: {len(errors)}")
    if saved_record:
        lines.append(f"New session record: {saved_record}")
    if errors:
        lines.append("")
        lines.append("Errors (first 20)")
        lines.append("-" * 60)
        lines.extend(errors[:20])

    self.history_output.setPlainText("\n".join(lines))
    if saved_record:
        self._refresh_session_history_list(select_path=saved_record)
    else:
        self._refresh_session_history_list()
    self._set_history_status("Apply session complete")


def import_session_record(self):
    if not mixsplitr_manifest:
        self._set_history_status("Session record import is unavailable", error=True)
        return
    source_path, _ = choose_open_file(
        self,
        "Import Session Record",
        os.path.expanduser("~"),
        "JSON Files (*.json)",
    )
    if not source_path:
        self._set_history_status("Import cancelled")
        return

    source_path = self._normalized_path(source_path)
    if not source_path.lower().endswith(".json"):
        self._set_history_status("Only .json session records can be imported", error=True)
        return
    imported_manifest = self._load_manifest_from_path(source_path)
    if not imported_manifest:
        self._set_history_status("Import failed: invalid session record JSON", error=True)
        return

    manifest_dir = self._history_manifest_directory()
    if mixsplitr_session and hasattr(mixsplitr_session, "_build_import_destination"):
        dest_path = mixsplitr_session._build_import_destination(manifest_dir, source_path)
    else:
        filename = os.path.basename(source_path)
        dest_path = os.path.join(manifest_dir, filename)
        if os.path.exists(dest_path):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name, ext = os.path.splitext(filename)
            dest_path = os.path.join(manifest_dir, f"{name}_imported_{stamp}{ext or '.json'}")

    try:
        if os.path.realpath(source_path) == os.path.realpath(dest_path):
            self._set_history_status("Session record already in history folder")
            return
    except Exception:
        pass

    try:
        shutil.copy2(source_path, dest_path)
    except Exception as exc:
        self._set_history_status(f"Import failed: {exc}", error=True)
        return

    session_name = str(imported_manifest.get("session_name") or os.path.basename(dest_path))
    self.history_output.setPlainText(
        "\n".join(
            [
                "Import Session Record",
                "=" * 60,
                f"Session: {session_name}",
                f"Source: {source_path}",
                f"Saved:  {dest_path}",
            ]
        )
    )
    self._refresh_session_history_list(select_path=dest_path)
    self._set_history_status("Imported session record")


def export_selected_session_record(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to export", error=True)
        return
    src_path = str(row.get("filepath") or "")
    if not src_path or not os.path.exists(src_path):
        self._set_history_status("Selected session record file is missing", error=True)
        return

    default_name = str(row.get("filename") or f"{row.get('session_name', 'session')}.json")
    dest_path, _ = choose_save_file(
        self,
        "Export Session Record",
        os.path.join(os.path.expanduser("~"), default_name),
        "JSON Files (*.json)",
    )
    if not dest_path:
        self._set_history_status("Export cancelled")
        return

    dest_path = self._normalized_path(dest_path)
    if not dest_path.lower().endswith(".json"):
        dest_path = f"{dest_path}.json"
    try:
        shutil.copy2(src_path, dest_path)
    except Exception as exc:
        self._set_history_status(f"Export failed: {exc}", error=True)
        return

    self.history_output.setPlainText(
        "\n".join(
            [
                "Export Session Record",
                "=" * 60,
                f"Session: {row.get('session_name', 'unknown')}",
                f"Source: {src_path}",
                f"Export: {dest_path}",
            ]
        )
    )
    self._set_history_status("Exported session record")


def _is_safe_history_record_path(self, filepath):
    if mixsplitr_session and hasattr(mixsplitr_session, "_is_safe_session_record_path"):
        try:
            return bool(mixsplitr_session._is_safe_session_record_path(filepath))
        except Exception:
            return False
    try:
        target = os.path.realpath(str(filepath or ""))
        manifest_dir = os.path.realpath(self._history_manifest_directory())
        if not target.lower().endswith(".json"):
            return False
        return os.path.commonpath([manifest_dir, target]) == manifest_dir
    except Exception:
        return False


def delete_selected_session_record(self):
    row = self._selected_history_row()
    if not row:
        self._set_history_status("Select one session record to delete", error=True)
        return
    filepath = str(row.get("filepath") or "")
    if not filepath:
        self._set_history_status("Could not resolve selected record path", error=True)
        return
    if not self._is_safe_history_record_path(filepath):
        self._set_history_status("Delete blocked by session-history safety rules", error=True)
        return
    if not os.path.exists(filepath):
        self._refresh_session_history_list()
        self._set_history_status("Selected session record is already missing", error=True)
        return

    confirm = QMessageBox.question(
        self,
        "Delete Session Record",
        (
            f"Delete session record '{row.get('filename', os.path.basename(filepath))}'?\n\n"
            "This removes only the session-history JSON record, not exported audio files."
        ),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        self._set_history_status("Delete cancelled")
        return

    try:
        os.remove(filepath)
    except Exception as exc:
        self._set_history_status(f"Delete failed: {exc}", error=True)
        return

    self.history_output.setPlainText(
        "\n".join(
            [
                "Delete Session Record",
                "=" * 60,
                f"Deleted: {filepath}",
            ]
        )
    )
    self._refresh_session_history_list()
    self._set_history_status("Session record deleted")
