import sys
import os
import json
import copy
import importlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QSpinBox,
    QDoubleSpinBox, QComboBox, QCheckBox, QDialog, QTextEdit, QApplication,
    QFormLayout, QScrollArea, QFrame, QFileDialog, QInputDialog, QListWidget,
    QListWidgetItem, QMessageBox, QAbstractItemView, QSizePolicy, QSplitter
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import Qt, Signal, QThread, QTimer

from mixsplitr_ui_browser import choose_save_file
from mixsplitr_ui_effects import FloatingOverlayScrollBar
from mixsplitr_ui_inputs import _ContainedScrollListWidget, _NoScrollComboBox
from mixsplitr_ui_waveform import AudioPreviewThread
from mixsplitr_devtools import annotate_widget, format_inspector_location

# Backend modules (lazy-imported)
mixsplitr_core = None
mixsplitr_identify = None
mixsplitr_editor = None
mixsplitr_essentia = None
mixsplitr_session = None
mixsplitr_tagging = None
_BACKEND_IMPORT_ERRORS = {}


def _lazy_import_backend(attr_name, module_name=None):
    """Import backend module on demand and cache the module object."""
    global mixsplitr_core, mixsplitr_identify, mixsplitr_editor, mixsplitr_essentia

    target = str(module_name or attr_name).strip()
    current = globals().get(attr_name)
    if current is not None:
        return current
    try:
        module = importlib.import_module(target)
        globals()[attr_name] = module
        return module
    except Exception as exc:
        _BACKEND_IMPORT_ERRORS[attr_name] = exc
        globals()[attr_name] = None
        return None


try:
    import mixsplitr_core
except ImportError:
    mixsplitr_core = None

try:
    import mixsplitr_editor
except ImportError:
    mixsplitr_editor = None

try:
    import mixsplitr_session
except ImportError:
    mixsplitr_session = None

try:
    import mixsplitr_tagging
except ImportError:
    mixsplitr_tagging = None


class MusicBrainzCombinedSearchThread(QThread):
    search_finished = Signal(object)

    def __init__(self, query, parent=None):
        super().__init__(parent)
        self.query = str(query or "").strip()

    def run(self):
        payload = {
            "query": self.query,
            "tracks": [],
            "albums": [],
            "errors": [],
        }
        editor_module = mixsplitr_editor or _lazy_import_backend("mixsplitr_editor")
        if editor_module is None:
            payload["errors"].append("MusicBrainz integration is unavailable.")
            self.search_finished.emit(payload)
            return

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            if hasattr(editor_module, "musicbrainz_search_recordings"):
                futures[
                    executor.submit(
                        editor_module.musicbrainz_search_recordings,
                        query=self.query,
                        limit=20,
                        prefer_original=True,
                    )
                ] = "tracks"
            if hasattr(editor_module, "musicbrainz_search_releases"):
                futures[
                    executor.submit(
                        editor_module.musicbrainz_search_releases,
                        query=self.query,
                        limit=20,
                    )
                ] = "albums"

            for future in as_completed(futures):
                bucket = futures[future]
                try:
                    data = future.result() or []
                except Exception as exc:
                    payload["errors"].append(f"{bucket}: {exc}")
                    data = []
                if bucket == "tracks":
                    payload["tracks"] = data
                else:
                    payload["albums"] = data

        self.search_finished.emit(payload)


class MusicBrainzReleaseTracklistThread(QThread):
    tracklist_finished = Signal(object)

    def __init__(self, album, parent=None):
        super().__init__(parent)
        self.album = dict(album or {})

    def run(self):
        payload = {
            "album": dict(self.album or {}),
            "tracklist_data": {},
            "error": "",
        }
        release_id = str(self.album.get("release_id") or "").strip()
        if not release_id:
            payload["error"] = "Selected album has no release ID."
            self.tracklist_finished.emit(payload)
            return

        editor_module = mixsplitr_editor or _lazy_import_backend("mixsplitr_editor")
        if editor_module is None or not hasattr(editor_module, "musicbrainz_get_release_tracklist"):
            payload["error"] = "Tracklist lookup is unavailable."
            self.tracklist_finished.emit(payload)
            return

        try:
            payload["tracklist_data"] = editor_module.musicbrainz_get_release_tracklist(release_id) or {}
        except Exception as exc:
            payload["error"] = str(exc)
        self.tracklist_finished.emit(payload)


class MusicBrainzEnhancedMetadataThread(QThread):
    metadata_finished = Signal(object)

    def __init__(self, artist, title, recording_id=None, parent=None):
        super().__init__(parent)
        self.artist = str(artist or "").strip()
        self.title = str(title or "").strip()
        self.recording_id = recording_id

    def run(self):
        payload = {
            "artist": self.artist,
            "title": self.title,
            "recording_id": self.recording_id,
            "enhanced": {},
            "error": "",
        }
        editor_module = mixsplitr_editor or _lazy_import_backend("mixsplitr_editor")
        if editor_module is None or not hasattr(editor_module, "get_enhanced_metadata"):
            self.metadata_finished.emit(payload)
            return

        try:
            payload["enhanced"] = editor_module.get_enhanced_metadata(
                self.artist,
                self.title,
                recording_id=self.recording_id,
            ) or {}
        except Exception as exc:
            payload["error"] = str(exc)
        self.metadata_finished.emit(payload)


class TrackEditorPanel(QWidget):
    workflow_action = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        annotate_widget(self, role="Track Editor Panel")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setWindowTitle("Track Editor")
        self.cache_data = {}
        self.cache_path = ""
        self.preview_temp_folder = ""
        self.editor_mode = "standalone"
        self.session_manifest = None
        self.session_manifest_path = ""
        self.tracks = []
        self._baseline_tracks = []
        self.filtered_indices = []
        self.changes_made = False
        self._pending_track_edits = {}
        self._bulk_checked_track_indices = set()
        self._displayed_track_index = -1
        self.preview_thread = None
        self._bulk_check_all_syncing = False
        self._musicbrainz_busy = False
        self._musicbrainz_search_thread = None
        self._musicbrainz_tracklist_thread = None
        self._musicbrainz_enhanced_thread = None

        self._build_ui()
        self._set_editor_mode("standalone")
        self._refresh_track_list()
        self._set_title()

    def has_unsaved_changes(self):
        return bool(self.changes_made or self._pending_track_edits or self._bulk_form_has_values())

    def _show_id_source_badges_enabled(self):
        parent = self.parent()
        while parent is not None:
            check = getattr(parent, "show_id_source_check", None)
            if check is not None:
                try:
                    return bool(check.isChecked())
                except Exception:
                    break
            try:
                parent = parent.parent()
            except Exception:
                parent = None

        snapshot = {}
        if isinstance(self.cache_data, dict):
            snapshot = dict(self.cache_data.get("config_snapshot") or {})
        if "show_id_source" in snapshot:
            try:
                return bool(snapshot.get("show_id_source"))
            except Exception:
                return True

        if mixsplitr_core is not None and hasattr(mixsplitr_core, "get_config"):
            try:
                return bool((mixsplitr_core.get_config() or {}).get("show_id_source", True))
            except Exception:
                pass
        return True

    def _id_source_badge_text(self, source_value):
        if not self._show_id_source_badges_enabled():
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

    def _preferred_id_source_value(self, source_value, winner_src=None):
        raw_source = str(source_value or "").strip()
        raw_winner = str(winner_src or "").strip()
        if not raw_winner:
            return raw_source

        normalized = raw_source.lower().replace(" ", "_")
        if normalized in ("", "unknown", "none", "musicbrainz", "musicbrainz_only", "mb"):
            return raw_winner
        return raw_source

    def _track_badge_source_value(self, track, include_timestamp=False):
        track_data = track if isinstance(track, dict) else {}
        backend_candidates = track_data.get("backend_candidates") or {}
        source_value = self._preferred_id_source_value(
            track_data.get("identification_source"),
            backend_candidates.get("winner_src"),
        )
        if not str(source_value or "").strip() and include_timestamp:
            source_value = track_data.get("timestamp_source")
        return str(source_value or "").strip()

    def _set_editor_mode(self, mode):
        normalized = str(mode or "").strip().lower()
        if normalized not in ("preview", "timestamp_preview", "standalone", "session"):
            normalized = "standalone"
        self.editor_mode = normalized
        self._sync_editor_mode_ui()

    def load_preview_session(self, cache_data, cache_path, temp_folder=""):
        self.cache_data = copy.deepcopy(cache_data if isinstance(cache_data, dict) else {})
        self.cache_path = str(cache_path or "")
        self.preview_temp_folder = str(temp_folder or "")
        self.tracks = list(self.cache_data.get("tracks", []) or [])
        is_timestamp = bool(
            (self.cache_data.get("config_snapshot") or {}).get("timestamp_editor_mode")
            or self.cache_data.get("timestamp_sessions")
        )
        if is_timestamp:
            self._set_editor_mode("timestamp_preview")
            self._normalize_timestamp_tracks()
        else:
            self._set_editor_mode("preview")
        self._baseline_tracks = copy.deepcopy(self.tracks)
        self.filtered_indices = []
        self.changes_made = False
        self._pending_track_edits = {}
        self._bulk_checked_track_indices = set()
        self._displayed_track_index = -1
        self._refresh_track_list()
        self._clear_bulk_form()
        self._set_title()
        if self.tracks:
            if is_timestamp:
                self.preview_status_label.setText(f"Loaded timestamp review: {len(self.tracks)} segment(s)")
            else:
                self.preview_status_label.setText(f"Loaded preview session: {len(self.tracks)} track(s)")
        else:
            self.preview_status_label.setText("Preview session is empty")

    def load_session_editor(self, cache_data, cache_path, manifest, manifest_path):
        self.cache_data = copy.deepcopy(cache_data if isinstance(cache_data, dict) else {})
        self.cache_path = str(cache_path or "")
        self.preview_temp_folder = ""
        self.session_manifest = manifest
        self.session_manifest_path = str(manifest_path or "")
        self.tracks = list(self.cache_data.get("tracks", []) or [])
        self._baseline_tracks = copy.deepcopy(self.tracks)
        self.filtered_indices = []
        self.changes_made = False
        self._pending_track_edits = {}
        self._bulk_checked_track_indices = set()
        self._displayed_track_index = -1
        self._set_editor_mode("session")
        self._refresh_track_list()
        self._clear_bulk_form()
        self._set_title()
        count = len(self.tracks)
        if count:
            self.preview_status_label.setText(f"Editing session record: {count} track(s)")
        else:
            self.preview_status_label.setText("Session record has no tracks")

    def _filename_guess_metadata(self, file_path):
        stem = os.path.splitext(os.path.basename(str(file_path or "")))[0].strip()
        cleaned = stem.replace("_", " ").strip()
        for sep in (" - ", " – ", " — "):
            if sep in cleaned:
                artist, title = cleaned.split(sep, 1)
                artist = artist.strip()
                title = title.strip()
                if artist and title:
                    return artist, title
        if " - " in stem:
            artist, title = stem.split(" - ", 1)
            artist = artist.strip()
            title = title.strip()
            if artist and title:
                return artist, title
        return "", ""

    def load_standalone_files(self, file_paths):
        unique_paths = []
        seen = set()
        for value in list(file_paths or []):
            normalized = os.path.abspath(os.path.expanduser(str(value or "").strip()))
            if not normalized or normalized in seen or not os.path.isfile(normalized):
                continue
            seen.add(normalized)
            unique_paths.append(normalized)

        tracks = []
        for path in unique_paths:
            artist, title = self._filename_guess_metadata(path)
            status = "identified" if (artist and title) else "unidentified"
            track = {
                "status": status,
                "artist": artist,
                "title": title,
                "album": "Unknown Album" if status == "identified" else "",
                "enhanced_metadata": {},
                "original_file": path,
                "unidentified_filename": os.path.basename(path),
            }
            if status == "identified":
                track["expected_filename"] = self._safe_filename(artist, title)
            tracks.append(track)

        self.cache_data = {
            "tracks": tracks,
            "config_snapshot": {"standalone_track_editor": True},
        }
        self.cache_path = ""
        self.preview_temp_folder = ""
        self.tracks = tracks
        self._baseline_tracks = copy.deepcopy(self.tracks)
        self.filtered_indices = []
        self.changes_made = False
        self._pending_track_edits = {}
        self._bulk_checked_track_indices = set()
        self._displayed_track_index = -1
        self._set_editor_mode("standalone")
        self._refresh_track_list()
        self._clear_bulk_form()
        self._set_title()
        if tracks:
            self.preview_status_label.setText(f"Loaded {len(tracks)} track(s) from files")
        else:
            self.preview_status_label.setText("No valid audio files were loaded")
        return len(tracks)

    def _build_ui(self):
        self.setObjectName("TrackEditorPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        card_top_margin = 18 if sys.platform == "darwin" else 24

        workspace_cell = QFrame()
        workspace_cell.setObjectName("TrackEditorWorkspaceGroupCell")
        workspace_cell.setStyleSheet(
            """
            QFrame#TrackEditorWorkspaceGroupCell {
                background-color: #22262B;
                border: none;
                border-radius: 8px;
            }
            """
        )
        workspace_layout = QVBoxLayout(workspace_cell)
        workspace_layout.setContentsMargins(12, 6, 12, 12)
        workspace_layout.setSpacing(12)

        def _apply_history_button_style(button, primary=False):
            if button is None:
                return
            button.setProperty("historyActionCell", True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            if primary:
                button.setStyleSheet(
                    """
                    QPushButton {
                        background-color: #4D8DFF;
                        border: none;
                        border-radius: 8px;
                        padding: 6px 12px;
                        color: #F6FAFF;
                    }
                    QPushButton:hover {
                        background-color: #63A1FF;
                    }
                    QPushButton:pressed {
                        background-color: #3F78DF;
                    }
                    QPushButton:disabled {
                        background-color: #2A2F36;
                        border: none;
                        color: #9C978D;
                    }
                    """
                )
                return
            button.setStyleSheet(
                """
                QPushButton {
                    background-color: #2C3239;
                    border: none;
                    border-radius: 8px;
                    padding: 6px 12px;
                    color: #F2EEE8;
                }
                QPushButton:hover {
                    background-color: #353C45;
                }
                QPushButton:pressed {
                    background-color: #262C32;
                }
                QPushButton:disabled {
                    background-color: #24292F;
                    border: none;
                    color: #7F7A72;
                }
                """
            )

        def _apply_history_combo_style(combo):
            if combo is None:
                return
            combo.setCursor(Qt.PointingHandCursor)
            combo.setMinimumHeight(32)
            combo.setStyleSheet(
                """
                QComboBox {
                    background-color: #2C3239;
                    border: none;
                    border-radius: 8px;
                    padding: 6px 28px 6px 12px;
                    color: #F2EEE8;
                }
                QComboBox:hover {
                    background-color: #353C45;
                }
                QComboBox:focus {
                    background-color: #353C45;
                }
                QComboBox:disabled {
                    background-color: #24292F;
                    color: #7F7A72;
                }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 24px;
                    border: none;
                    background: transparent;
                }
                QComboBox QAbstractItemView {
                    background-color: #22262B;
                    border: 1px solid #30353C;
                    border-radius: 8px;
                    padding: 4px;
                    color: #EBE7E1;
                    selection-background-color: #2B4E7A;
                    selection-color: #EBE7E1;
                }
                """
            )

        summary_card = QWidget()
        summary_card.setProperty("trackEditorFadeLayer", True)
        summary_card.setProperty("topFadeInsetPx", float(card_top_margin))
        summary_card.setAttribute(Qt.WA_StyledBackground, False)
        summary_card.setAutoFillBackground(False)
        summary_card.setStyleSheet("background: transparent; border: none;")
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(10)

        top_row = QHBoxLayout()
        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        top_row.addWidget(self.stats_label, 1)

        filter_label = QLabel("Filter:")
        top_row.addWidget(filter_label)
        self.filter_combo = _NoScrollComboBox()
        self.filter_combo.setMinimumWidth(170)
        self.filter_combo.addItem("All Tracks", "all")
        self.filter_combo.addItem("Identified", "identified")
        self.filter_combo.addItem("Unidentified", "unidentified")
        self.filter_combo.addItem("Missing Genre", "no_genre")
        self.filter_combo.addItem("Missing BPM", "no_bpm")
        self.filter_combo.addItem("Skipped", "skipped")
        self.filter_combo.currentIndexChanged.connect(self._refresh_track_list)
        _apply_history_combo_style(self.filter_combo)
        top_row.addWidget(self.filter_combo)
        summary_layout.addLayout(top_row)
        workspace_layout.addWidget(summary_card)

        editor_card = QWidget()
        editor_card.setProperty("trackEditorFadeLayer", True)
        editor_card.setProperty("topFadeInsetPx", float(card_top_margin))
        editor_card.setAttribute(Qt.WA_StyledBackground, False)
        editor_card.setAutoFillBackground(False)
        editor_card.setStyleSheet("background: transparent; border: none;")
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(12)

        self.body_splitter = QSplitter(Qt.Horizontal, self)
        # Fade this internal content layer directly during top drag-strip
        # transitions; the outer card mask alone can miss splitter children.
        self.body_splitter.setProperty("trackEditorFadeLayer", True)
        self.body_splitter.setProperty("topFadeInsetPx", float(card_top_margin))
        self.body_splitter.setAttribute(Qt.WA_StyledBackground, False)
        self.body_splitter.setAutoFillBackground(False)
        self.body_splitter.setStyleSheet("background: transparent; border: none;")
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(8)
        editor_layout.addWidget(self.body_splitter, 1)
        workspace_layout.addWidget(editor_card, 1)

        left_container = QWidget()
        left_container.setProperty("trackEditorFadeLayer", True)
        left_container.setProperty("topFadeInsetPx", float(card_top_margin))
        left_container.setAttribute(Qt.WA_StyledBackground, False)
        left_container.setAutoFillBackground(False)
        left_container.setStyleSheet("background: transparent; border: none;")
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_container.setLayout(left_col)
        left_container.setMinimumWidth(260)

        tracks_header_row = QHBoxLayout()
        tracks_header_row.setContentsMargins(0, 0, 0, 0)
        tracks_header_row.setSpacing(8)
        tracks_label = QLabel("Tracks")
        tracks_header_row.addWidget(tracks_label)
        tracks_header_row.addStretch(1)
        self.bulk_check_all_checkbox = QCheckBox("Check All")
        self.bulk_check_all_checkbox.setCursor(Qt.PointingHandCursor)
        self.bulk_check_all_checkbox.setStyleSheet(
            """
            QCheckBox {
                color: #C8D1DC;
                font-size: 11px;
                spacing: 6px;
                background: transparent;
                border: none;
                padding: 0px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
            }
            """
        )
        self.bulk_check_all_checkbox.toggled.connect(self._on_bulk_check_all_toggled)
        tracks_header_row.addWidget(self.bulk_check_all_checkbox, 0, Qt.AlignRight)
        left_col.addLayout(tracks_header_row)
        track_list_cell = QFrame()
        track_list_cell.setObjectName("TrackEditorTrackListCell")
        track_list_cell.setProperty("trackEditorFadeLayer", True)
        track_list_cell.setProperty("topFadeInsetPx", float(card_top_margin))
        track_list_cell.setAttribute(Qt.WA_StyledBackground, True)
        track_list_cell.setAutoFillBackground(True)
        track_list_cell.setStyleSheet(
            """
            QFrame#TrackEditorTrackListCell {
                background-color: #1E2228;
                border: none;
                border-radius: 10px;
            }
            """
        )
        track_list_cell_layout = QVBoxLayout(track_list_cell)
        track_list_cell_layout.setContentsMargins(10, 10, 10, 10)
        track_list_cell_layout.setSpacing(8)
        self.track_list_hint_label = QLabel("Click list to start scrolling")
        self.track_list_hint_label.setStyleSheet(
            "color: #95A1B0; font-size: 11px; background: transparent; border: none; padding: 0px 2px;"
        )
        track_list_cell_layout.addWidget(self.track_list_hint_label, 0, Qt.AlignLeft)
        self.track_list = _ContainedScrollListWidget()
        self.track_list.setProperty("trackEditorFadeLayer", True)
        self.track_list.setProperty("topFadeInsetPx", float(card_top_margin))
        self.track_list.setObjectName("TrackEditorTrackList")
        self.track_list.setSelectionMode(QListWidget.SingleSelection)
        self.track_list.setMinimumHeight(420)
        self.track_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.track_list.setTextElideMode(Qt.ElideRight)
        self.track_list.setUniformItemSizes(True)
        self.track_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.track_list.setVerticalScrollBar(FloatingOverlayScrollBar(Qt.Vertical, self.track_list, always_visible=True))
        self.track_list.verticalScrollBar().setSingleStep(20)
        self.track_list.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#1E2228")
        self.track_list.setFrameShape(QFrame.NoFrame)
        self.track_list.setStyleSheet(
            """
            QListWidget#TrackEditorTrackList {
                background-color: transparent;
                border: none;
                padding: 0px;
                color: #EBE7E1;
                font-size: 12px;
            }
            QListWidget#TrackEditorTrackList::item {
                background-color: rgba(18, 24, 33, 204);
                border: none;
                border-radius: 9px;
                margin: 3px 1px;
                padding: 6px 8px;
            }
            QListWidget#TrackEditorTrackList::item:selected {
                background-color: #2B4E7A;
                color: #EBE7E1;
            }
            QListWidget#TrackEditorTrackList::item:hover:!selected {
                background-color: #292E34;
            }
            """
        )
        self.track_list.viewport().setProperty("trackEditorFadeLayer", True)
        self.track_list.viewport().setProperty("topFadeInsetPx", float(card_top_margin))
        self.track_list.viewport().setAutoFillBackground(False)
        self.track_list.viewport().setStyleSheet("background: transparent;")
        self.track_list.itemSelectionChanged.connect(self._on_track_selected)
        self.track_list.itemChanged.connect(self._on_track_item_changed)
        track_list_cell_layout.addWidget(self.track_list)
        left_col.addWidget(track_list_cell, 1)

        right_container = QWidget()
        right_container.setProperty("trackEditorFadeLayer", True)
        right_container.setProperty("topFadeInsetPx", float(card_top_margin))
        right_container.setAttribute(Qt.WA_StyledBackground, False)
        right_container.setAutoFillBackground(False)
        right_container.setStyleSheet("background: transparent; border: none;")
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(10)
        right_container.setLayout(right_col)
        right_container.setMinimumWidth(320)
        right_container.setMinimumHeight(0)

        self.detail_status_label = QLabel("Status: -")
        right_col.addWidget(self.detail_status_label)

        self.detail_file_label = QLabel("Source: -")
        self.detail_file_label.setWordWrap(True)
        right_col.addWidget(self.detail_file_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setHorizontalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.file_name_input = QLineEdit()
        self.artist_input = QLineEdit()
        self.title_input = QLineEdit()
        self.album_input = QLineEdit()
        self.genre_input = QLineEdit()
        self.bpm_input = QLineEdit()
        self.year_input = QLineEdit()
        self.isrc_input = QLineEdit()
        self.start_time_input = QLineEdit()
        self.end_time_input = QLineEdit()
        self.duration_input = QLineEdit()
        self.identification_source_input = QLineEdit()
        self._editor_form_fields = []
        self._editor_form_labels = []
        self._editor_form_row_map = {}

        def _add_editor_form_row(key, label_text, field):
            label_widget = QLabel(label_text)
            label_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.addRow(label_widget, field)
            field.setMinimumHeight(32)
            self._editor_form_fields.append(field)
            self._editor_form_labels.append(label_widget)
            self._editor_form_row_map[str(key or "")] = (label_widget, field)

        _add_editor_form_row("file_name", "File:", self.file_name_input)
        _add_editor_form_row("artist", "Artist:", self.artist_input)
        _add_editor_form_row("title", "Title:", self.title_input)
        _add_editor_form_row("album", "Album:", self.album_input)
        _add_editor_form_row("genre", "Genre(s):", self.genre_input)
        _add_editor_form_row("bpm", "BPM:", self.bpm_input)
        _add_editor_form_row("year", "Year:", self.year_input)
        _add_editor_form_row("isrc", "ISRC:", self.isrc_input)
        _add_editor_form_row("start_time", "Start:", self.start_time_input)
        _add_editor_form_row("end_time", "End:", self.end_time_input)
        _add_editor_form_row("duration", "Duration:", self.duration_input)
        _add_editor_form_row("identification_source", "Source:", self.identification_source_input)
        for field in list(self._editor_form_fields):
            field.textEdited.connect(self._on_track_form_text_edited)
            field.setContextMenuPolicy(Qt.CustomContextMenu)
            field.customContextMenuRequested.connect(
                lambda pos, widget=field: self._show_track_editor_context_menu(widget, pos)
            )
        self.end_time_input.setReadOnly(True)
        self.duration_input.setReadOnly(True)
        self.identification_source_input.setReadOnly(True)
        right_col.addLayout(form)

        self.bulk_edit_title_label = QLabel("Bulk Editing (0 selected)")
        right_col.addWidget(self.bulk_edit_title_label)

        self.bulk_edit_hint_label = QLabel(
            "Bulk editor to group albums and artists and more."
        )
        self.bulk_edit_hint_label.setWordWrap(True)
        self.bulk_edit_hint_label.setStyleSheet("color: #AEB8C7; font-size: 11px;")
        right_col.addWidget(self.bulk_edit_hint_label)

        bulk_form = QFormLayout()
        bulk_form.setContentsMargins(0, 0, 0, 0)
        bulk_form.setSpacing(8)
        bulk_form.setHorizontalSpacing(12)
        bulk_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bulk_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        bulk_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.bulk_artist_input = QLineEdit()
        self.bulk_album_input = QLineEdit()
        self.bulk_genre_input = QLineEdit()
        self.bulk_year_input = QLineEdit()
        self._bulk_form_fields = []
        self._bulk_form_labels = []

        def _add_bulk_form_row(label_text, field):
            label_widget = QLabel(label_text)
            label_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bulk_form.addRow(label_widget, field)
            field.setMinimumHeight(32)
            self._bulk_form_fields.append(field)
            self._bulk_form_labels.append(label_widget)

        _add_bulk_form_row("Artist:", self.bulk_artist_input)
        _add_bulk_form_row("Album:", self.bulk_album_input)
        _add_bulk_form_row("Genre(s):", self.bulk_genre_input)
        _add_bulk_form_row("Year:", self.bulk_year_input)
        for field in list(self._bulk_form_fields):
            field.textEdited.connect(self._on_bulk_form_text_edited)
            field.setContextMenuPolicy(Qt.CustomContextMenu)
            field.customContextMenuRequested.connect(
                lambda pos, widget=field: self._show_track_editor_context_menu(widget, pos)
            )
        right_col.addLayout(bulk_form)

        action_row_a = QHBoxLayout()
        action_row_a.setContentsMargins(0, 0, 0, 0)
        action_row_a.setSpacing(8)
        self.apply_edits_btn = QPushButton("Apply Edits")
        self.apply_edits_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.apply_edits_btn.clicked.connect(self._apply_form_edits)
        _apply_history_button_style(self.apply_edits_btn)
        action_row_a.addWidget(self.apply_edits_btn)

        self.clear_fields_btn = QPushButton("Clear Fields")
        self.clear_fields_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.clear_fields_btn.clicked.connect(self._clear_selected_track_form_fields)
        _apply_history_button_style(self.clear_fields_btn)
        action_row_a.addWidget(self.clear_fields_btn)

        self.mb_search_btn = QPushButton("MusicBrainz Match")
        self.mb_search_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mb_search_btn.clicked.connect(self._musicbrainz_match_selected_track)
        _apply_history_button_style(self.mb_search_btn)
        action_row_a.addWidget(self.mb_search_btn)
        right_col.addLayout(action_row_a)

        action_row_b = QHBoxLayout()
        action_row_b.setContentsMargins(0, 0, 0, 0)
        action_row_b.setSpacing(8)
        self.preview_btn = QPushButton("Preview Audio")
        self.preview_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preview_btn.clicked.connect(self._preview_selected_track_audio)
        _apply_history_button_style(self.preview_btn)
        action_row_b.addWidget(self.preview_btn)

        self.delete_restore_btn = QPushButton("Delete/Restore")
        self.delete_restore_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.delete_restore_btn.clicked.connect(self._toggle_delete_selected_track)
        _apply_history_button_style(self.delete_restore_btn)
        action_row_b.addWidget(self.delete_restore_btn)

        self.import_tracklist_btn = QPushButton("Import Tracklist")
        self.import_tracklist_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_tracklist_btn.clicked.connect(self._import_tracklist)
        _apply_history_button_style(self.import_tracklist_btn)
        action_row_b.addWidget(self.import_tracklist_btn)
        right_col.addLayout(action_row_b)

        self.preview_status_label = QLabel("")
        self.preview_status_label.setWordWrap(True)
        right_col.addWidget(self.preview_status_label)
        right_col.addStretch(1)

        self.body_splitter.addWidget(left_container)
        self.body_splitter.addWidget(right_container)
        self.body_splitter.setStretchFactor(0, 8)
        self.body_splitter.setStretchFactor(1, 4)

        actions_card = QWidget()
        actions_card.setProperty("trackEditorFadeLayer", True)
        actions_card.setProperty("topFadeInsetPx", float(card_top_margin))
        actions_card.setAttribute(Qt.WA_StyledBackground, False)
        actions_card.setAutoFillBackground(False)
        actions_card.setStyleSheet("background: transparent; border: none;")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(8)
        self.save_btn = QPushButton("Save")
        self.save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_btn.clicked.connect(self._save_only)
        _apply_history_button_style(self.save_btn)
        bottom_row.addWidget(self.save_btn)

        self.apply_export_btn = QPushButton("Save and Export")
        self.apply_export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.apply_export_btn.clicked.connect(self._save_and_apply)
        _apply_history_button_style(self.apply_export_btn, primary=True)

        self.save_exit_btn = QPushButton("Save & Close")
        self.save_exit_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_exit_btn.clicked.connect(self._save_and_close)
        _apply_history_button_style(self.save_exit_btn)
        bottom_row.addWidget(self.save_exit_btn)

        self.discard_btn = QPushButton("Discard")
        self.discard_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.discard_btn.clicked.connect(self._discard_and_close)
        _apply_history_button_style(self.discard_btn)
        bottom_row.addWidget(self.discard_btn)
        bottom_row.addWidget(self.apply_export_btn)
        actions_layout.addLayout(bottom_row)
        workspace_layout.addWidget(actions_card)
        root.addWidget(workspace_cell, 1)
        self._sync_editor_mode_ui()
        self._sync_track_editor_density()
        QTimer.singleShot(0, lambda: self._set_track_editor_layout_mode(force=True))

    def _sync_track_editor_density(self):
        width_hint = max(1, self.width())
        compact = width_hint < 1220

        track_font_px = 11 if compact else 12
        track_vpad_px = 5 if compact else 6
        track_hpad_px = 8 if compact else 10
        self.track_list.setStyleSheet(
            f"""
            QListWidget#TrackEditorTrackList {{
                background-color: transparent;
                border: none;
                padding: 0px;
                color: #EBE7E1;
                font-size: {track_font_px}px;
            }}
            QListWidget#TrackEditorTrackList::item {{
                background-color: rgba(18, 24, 33, 204);
                border: none;
                border-radius: 9px;
                margin: 3px 1px;
                padding: {track_vpad_px}px {track_hpad_px}px;
            }}
            QListWidget#TrackEditorTrackList::item:selected {{
                background-color: #2B4E7A;
                color: #EBE7E1;
            }}
            QListWidget#TrackEditorTrackList::item:hover:!selected {{
                background-color: #292E34;
            }}
            """
        )

        field_font_px = 12 if compact else 13
        field_min_h = 30 if compact else 34
        field_qss = (
            f"QLineEdit {{"
            f"background-color: #1F2328;"
            f"border: none;"
            f"border-radius: 8px;"
            f"padding: 7px 12px;"
            f"color: #EBE7E1;"
            f"font-size: {field_font_px}px;"
            f"}}"
            f"QLineEdit:focus {{"
            f"background-color: #273449;"
            f"border: none;"
            f"}}"
            f"QLineEdit:disabled {{"
            f"background-color: #1A1D21;"
            f"border: none;"
            f"color: #7F7A72;"
            f"}}"
        )
        label_qss = (
            f"QLabel {{"
            f"background-color: #22262B;"
            f"border: none;"
            f"border-radius: 8px;"
            f"padding: 6px 12px;"
            f"color: #EBE7E1;"
            f"font-size: {field_font_px}px;"
            f"}}"
        )
        form_fields = list(getattr(self, "_editor_form_fields", [])) + list(getattr(self, "_bulk_form_fields", []))
        form_labels = list(getattr(self, "_editor_form_labels", [])) + list(getattr(self, "_bulk_form_labels", []))
        for field in form_fields:
            field.setMinimumHeight(field_min_h)
            field.setProperty("settingsBubbleField", True)
            field.setStyleSheet(field_qss)
        for label in form_labels:
            label.setMinimumHeight(field_min_h)
            label.setStyleSheet(label_qss)

    def _set_track_editor_layout_mode(self, force=False):
        splitter = getattr(self, "body_splitter", None)
        if splitter is None:
            return
        orientation = Qt.Vertical if self.width() < 1120 else Qt.Horizontal
        if not force and splitter.orientation() == orientation:
            return
        splitter.setOrientation(orientation)
        width_hint = max(1, self.width())
        height_hint = max(1, self.height())
        if orientation == Qt.Horizontal:
            self.setMinimumHeight(720)
            splitter.setSizes(
                [
                    max(340, int(width_hint * 0.52)),
                    max(300, int(width_hint * 0.48)),
                ]
            )
        else:
            # In stacked mode, request a taller workspace so fields stay fully
            # visible without introducing nested inner scroll panes.
            self.setMinimumHeight(1020)
            splitter.setSizes(
                [
                    max(380, int(height_hint * 0.62)),
                    max(260, int(height_hint * 0.38)),
                ]
            )

    def showEvent(self, event):
        super().showEvent(event)
        # Force layout settlement on every show — the deferred singleShot(0)
        # from _build_ui may have run before geometry was valid (especially on
        # macOS where the NSView hierarchy settles after showEvent).
        self._sync_track_editor_density()
        self._set_track_editor_layout_mode(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_track_editor_density()
        self._set_track_editor_layout_mode()

    def _style_standard_context_menu(
        self,
        menu,
        *,
        background="#22262B",
        border="transparent",
        text="#EBE7E1",
        disabled="#7F7A72",
        highlight="#2B4E7A",
        highlight_text="#EBE7E1",
    ):
        if menu is None:
            return
        try:
            menu.setAttribute(Qt.WA_StyledBackground, True)
            menu.setAttribute(Qt.WA_TranslucentBackground, False)
            menu.setAutoFillBackground(True)
            menu.setWindowOpacity(1.0)
        except Exception:
            pass

        try:
            palette = menu.palette()
            bg_color = QColor(str(background))
            text_color = QColor(str(text))
            disabled_color = QColor(str(disabled))
            highlight_color = QColor(str(highlight))
            highlight_text_color = QColor(str(highlight_text))
            palette.setColor(QPalette.Window, bg_color)
            palette.setColor(QPalette.Base, bg_color)
            palette.setColor(QPalette.Button, bg_color)
            palette.setColor(QPalette.WindowText, text_color)
            palette.setColor(QPalette.Text, text_color)
            palette.setColor(QPalette.ButtonText, text_color)
            palette.setColor(QPalette.Highlight, highlight_color)
            palette.setColor(QPalette.HighlightedText, highlight_text_color)
            palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_color)
            palette.setColor(QPalette.Disabled, QPalette.Text, disabled_color)
            palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_color)
            menu.setPalette(palette)
        except Exception:
            pass

        menu.setStyleSheet(
            f"""
            QMenu {{
                background: {background};
                background-color: {background};
                border: 1px solid {border};
                border-radius: 8px;
                color: {text};
                padding: 6px;
            }}
            QMenu::item {{
                background-color: transparent;
                padding: 7px 14px;
                border-radius: 4px;
                color: {text};
            }}
            QMenu::item:selected {{
                background-color: {highlight};
                color: {highlight_text};
            }}
            QMenu::item:disabled {{
                background-color: transparent;
                color: {disabled};
            }}
            QMenu::separator {{
                height: 1px;
                background: #30353C;
                margin: 4px 8px;
            }}
            """
        )

    def _show_styled_text_context_menu(self, widget, pos, *, editor_style=False):
        if widget is None:
            return
        try:
            menu = widget.createStandardContextMenu()
        except Exception:
            menu = None
        if menu is None:
            return

        if editor_style:
            self._style_standard_context_menu(
                menu,
                background="#1F2631",
                border="#8FA3C2",
                text="#F1F6FF",
                disabled="#9AA7BA",
                highlight="#436B97",
                highlight_text="#FFFFFF",
            )
        else:
            self._style_standard_context_menu(menu)
        menu.exec(widget.mapToGlobal(pos))

    def _show_track_editor_context_menu(self, widget, pos):
        self._show_styled_text_context_menu(widget, pos, editor_style=True)

    def _install_standard_text_context_menus(self):
        editor_fields = set(getattr(self, "_editor_form_fields", []) or []) | set(getattr(self, "_bulk_form_fields", []) or [])
        for widget in self.findChildren(QLineEdit):
            if widget in editor_fields:
                continue
            if bool(widget.property("mixsplitrStyledContextMenu")):
                continue
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, target=widget: self._show_styled_text_context_menu(target, pos)
            )
            widget.setProperty("mixsplitrStyledContextMenu", True)

        for widget in self.findChildren(QTextEdit):
            if bool(widget.property("mixsplitrStyledContextMenu")):
                continue
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, target=widget: self._show_styled_text_context_menu(target, pos)
            )
            widget.setProperty("mixsplitrStyledContextMenu", True)

    def _set_title(self):
        suffix = " *Unsaved" if self.has_unsaved_changes() else ""
        self.setWindowTitle(f"Track Editor{suffix}")

    def _is_preview_editor_mode(self):
        return str(getattr(self, "editor_mode", "") or "") in ("preview", "timestamp_preview")

    def _is_timestamp_editor_mode(self):
        return str(getattr(self, "editor_mode", "") or "") == "timestamp_preview"

    def _timestamp_session_map(self):
        sessions = {}
        cache_data = self.cache_data if isinstance(self.cache_data, dict) else {}
        for key, value in dict(cache_data.get("timestamp_sessions") or {}).items():
            normalized = os.path.abspath(str(key or "").strip())
            if not normalized:
                continue
            sessions[normalized] = dict(value or {})
        return sessions

    def _timestamp_track_label(self, track, fallback_index=None):
        if not isinstance(track, dict):
            fallback = int(fallback_index or 1)
            return f"Track {fallback:02d}"
        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        if title:
            return title
        existing = str(track.get("timeline_label") or track.get("unidentified_filename") or "").strip()
        if existing:
            return existing
        fallback = int(fallback_index or (int(track.get("index", 0) or 0) + 1))
        return f"Track {fallback:02d}"

    def _format_timestamp_editor_time(self, seconds):
        try:
            total_seconds = max(0.0, float(seconds or 0.0))
        except Exception:
            total_seconds = 0.0
        whole_seconds = int(total_seconds)
        hours = whole_seconds // 3600
        minutes = (whole_seconds % 3600) // 60
        seconds_component = whole_seconds % 60
        fractional = total_seconds - float(whole_seconds)
        if abs(fractional) >= 0.05:
            return f"{hours:02d}:{minutes:02d}:{seconds_component + fractional:04.1f}"
        return f"{hours:02d}:{minutes:02d}:{seconds_component:02d}"

    def _parse_timestamp_editor_time(self, raw_value):
        text = str(raw_value or "").strip()
        if not text:
            raise ValueError("Enter a start time.")
        if ":" not in text:
            try:
                parsed = float(text)
            except Exception as exc:
                raise ValueError("Time must be seconds or HH:MM:SS.") from exc
            if parsed < 0:
                raise ValueError("Time cannot be negative.")
            return parsed
        parts = [part.strip() for part in text.split(":")]
        if len(parts) not in (2, 3) or any(part == "" for part in parts):
            raise ValueError("Use MM:SS or HH:MM:SS.")
        try:
            if len(parts) == 2:
                hours = 0.0
                minutes = float(parts[0])
                seconds = float(parts[1])
            else:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
        except Exception as exc:
            raise ValueError("Time contains invalid numbers.") from exc
        if hours < 0 or minutes < 0 or seconds < 0:
            raise ValueError("Time cannot be negative.")
        if minutes >= 60 or seconds >= 60:
            raise ValueError("Minutes and seconds must be under 60.")
        return (hours * 3600.0) + (minutes * 60.0) + seconds

    def _set_editor_form_row_visible(self, key, visible):
        row_map = getattr(self, "_editor_form_row_map", {}) or {}
        row = row_map.get(str(key or ""))
        if not row:
            return
        label_widget, field = row
        try:
            label_widget.setVisible(bool(visible))
        except Exception:
            pass
        try:
            field.setVisible(bool(visible))
        except Exception:
            pass

    def _sync_editor_mode_ui(self):
        is_timestamp = self._is_timestamp_editor_mode()
        is_preview = self._is_preview_editor_mode()
        is_session = self.editor_mode == "session"

        self.apply_export_btn.setVisible(is_preview)
        self.save_exit_btn.setVisible(is_preview or is_session)
        if is_session:
            self.discard_btn.setText("Discard")
            self.save_btn.setText("Save to Session")
            self.save_exit_btn.setText("Save && Close")
            self.apply_export_btn.setText("Save and Export")
        elif is_timestamp:
            self.discard_btn.setText("Discard")
            self.save_btn.setText("Save")
            self.save_exit_btn.setText("Save && Close")
            self.apply_export_btn.setText("Save and Export Tracklist")
        elif is_preview:
            self.discard_btn.setText("Discard")
            self.save_btn.setText("Save")
            self.save_exit_btn.setText("Save && Close")
            self.apply_export_btn.setText("Save and Export")
        else:
            self.discard_btn.setText("Reset")
            self.save_btn.setText("Save Session")
            self.apply_export_btn.setText("Save and Export")

        self.preview_btn.setText("Preview Segment" if is_timestamp else "Preview Audio")
        for key in ("file_name", "album", "genre", "bpm", "year", "isrc"):
            self._set_editor_form_row_visible(key, not is_timestamp)
        for key in ("start_time", "end_time", "duration", "identification_source"):
            self._set_editor_form_row_visible(key, is_timestamp)

        self.bulk_edit_title_label.setVisible(not is_timestamp)
        self.bulk_edit_hint_label.setVisible(not is_timestamp)
        self.bulk_check_all_checkbox.setVisible(not is_timestamp)
        for label in list(getattr(self, "_bulk_form_labels", []) or []):
            label.setVisible(not is_timestamp)
        for field in list(getattr(self, "_bulk_form_fields", []) or []):
            field.setVisible(not is_timestamp)

        self.start_time_input.setReadOnly(False)
        self.end_time_input.setReadOnly(True)
        self.duration_input.setReadOnly(True)
        self.identification_source_input.setReadOnly(True)

    def _normalize_timestamp_tracks(self):
        if not self._is_timestamp_editor_mode():
            return
        session_map = self._timestamp_session_map()
        grouped = {}
        for track in list(self.tracks or []):
            if not isinstance(track, dict):
                continue
            source_file = os.path.abspath(str(track.get("original_file") or "").strip())
            grouped.setdefault(source_file, []).append(track)

        ordered_tracks = []
        for source_file, group in sorted(
            grouped.items(),
            key=lambda item: (
                int((session_map.get(item[0]) or {}).get("source_file_index", (item[1][0] or {}).get("source_file_index", 0)) or 0),
                str(item[0] or "").lower(),
            ),
        ):
            group.sort(
                key=lambda track: (
                    float((track or {}).get("start_time", 0.0) or 0.0),
                    int((track or {}).get("index", 0) or 0),
                )
            )
            session_info = dict(session_map.get(source_file) or {})
            try:
                duration_seconds = float(session_info.get("duration_seconds", 0.0) or 0.0)
            except Exception:
                duration_seconds = 0.0
            if duration_seconds <= 0.0:
                duration_seconds = max(
                    [
                        float((track or {}).get("end_time", 0.0) or 0.0)
                        for track in group
                    ]
                    + [
                        float((track or {}).get("start_time", 0.0) or 0.0)
                        for track in group
                    ]
                    + [0.0]
                )

            source_name = os.path.splitext(os.path.basename(source_file))[0] or "Unknown Source"
            for idx, track in enumerate(group):
                try:
                    start_sec = max(0.0, float(track.get("start_time", 0.0) or 0.0))
                except Exception:
                    start_sec = 0.0
                if duration_seconds > 0.0:
                    start_sec = min(start_sec, duration_seconds)
                if idx + 1 < len(group):
                    try:
                        next_start = max(start_sec, float(group[idx + 1].get("start_time", start_sec) or start_sec))
                    except Exception:
                        next_start = start_sec
                else:
                    next_start = duration_seconds if duration_seconds > 0.0 else start_sec
                if duration_seconds > 0.0:
                    next_start = min(duration_seconds, next_start)
                end_sec = max(start_sec, next_start)
                duration_sec = max(0.0, end_sec - start_sec)
                label = self._timestamp_track_label(track, idx + 1)

                track["source_file_index"] = int(
                    track.get("source_file_index", session_info.get("source_file_index", 0)) or 0
                )
                track["source_name"] = str(track.get("source_name") or source_name)
                track["index"] = idx
                track["chunk_index"] = idx
                track["start_time"] = start_sec
                track["end_time"] = end_sec
                track["duration_sec"] = duration_sec
                track["start_timestamp"] = self._format_timestamp_editor_time(start_sec)
                track["end_timestamp"] = self._format_timestamp_editor_time(end_sec)
                track["timeline_label"] = label
                if not str(track.get("unidentified_filename") or "").strip():
                    track["unidentified_filename"] = label
                track.setdefault("enhanced_metadata", {})
                track["enhanced_metadata"]["timeline_start"] = track["start_timestamp"]
                track["enhanced_metadata"]["timeline_end"] = track["end_timestamp"]
                track["enhanced_metadata"]["timeline_duration_sec"] = duration_sec
                ordered_tracks.append(track)

        self.tracks = ordered_tracks

    def _validate_timestamp_session_tracks(self, *, require_active=False):
        if not self._is_timestamp_editor_mode():
            return True
        grouped = {}
        active_count = 0
        for idx, track in enumerate(list(self.tracks or [])):
            if not isinstance(track, dict):
                continue
            source_file = os.path.abspath(str(track.get("original_file") or "").strip())
            grouped.setdefault(source_file, []).append((idx, track))
            if str(track.get("status") or "").strip().lower() != "skipped":
                active_count += 1
        if require_active and active_count <= 0:
            QMessageBox.warning(self, "Export Tracklist", "Restore at least one segment before exporting.")
            return False
        for _source_file, rows in grouped.items():
            ordered = sorted(
                rows,
                key=lambda item: (
                    float((item[1] or {}).get("start_time", 0.0) or 0.0),
                    int((item[1] or {}).get("index", 0) or 0),
                ),
            )
            prev_start = None
            for track_index, track in ordered:
                try:
                    start_sec = float(track.get("start_time", 0.0) or 0.0)
                except Exception:
                    start_sec = 0.0
                if prev_start is not None and start_sec <= (prev_start + 1e-6):
                    self._select_track_by_index(track_index)
                    QMessageBox.warning(
                        self,
                        "Invalid Timestamps",
                        "Each segment start time must be later than the previous one for the same source file.",
                    )
                    return False
                prev_start = start_sec
        return True

    def _reset_to_empty_standalone_editor(self):
        self.cache_data = {"tracks": [], "config_snapshot": {"standalone_track_editor": True}}
        self.cache_path = ""
        self.preview_temp_folder = ""
        self.session_manifest = None
        self.session_manifest_path = ""
        self.tracks = []
        self._baseline_tracks = []
        self.filtered_indices = []
        self.changes_made = False
        self._pending_track_edits = {}
        self._bulk_checked_track_indices = set()
        self._displayed_track_index = -1
        self._set_editor_mode("standalone")
        self._clear_bulk_form()
        self._refresh_track_list()
        self._set_title()
        self.preview_status_label.setText("Track Editor ready")

    def _track_form_state(self, track):
        if not isinstance(track, dict):
            return {
                "file_name": "",
                "artist": "",
                "title": "",
                "album": "",
                "genre": "",
                "bpm": "",
                "year": "",
                "isrc": "",
                "start_time": "",
            }
        if self._is_timestamp_editor_mode():
            return {
                "file_name": "",
                "artist": str(track.get("artist") or "").strip(),
                "title": str(track.get("title") or "").strip(),
                "album": "",
                "genre": "",
                "bpm": "",
                "year": "",
                "isrc": "",
                "start_time": self._format_timestamp_editor_time(track.get("start_time", 0.0)),
            }
        enhanced = track.get("enhanced_metadata") or {}
        genres = enhanced.get("genres") or []
        return {
            "file_name": self._track_filename_display(track),
            "artist": str(track.get("artist") or "").strip(),
            "title": str(track.get("title") or "").strip(),
            "album": str(track.get("album") or "").strip(),
            "genre": ", ".join([str(g).strip() for g in genres if str(g).strip()]),
            "bpm": str(enhanced.get("bpm") or "").strip(),
            "year": str(enhanced.get("release_date") or "").strip(),
            "isrc": str(enhanced.get("isrc") or "").strip(),
            "start_time": "",
        }

    def _track_form_display_state(self, track):
        state = dict(self._track_form_state(track))
        if self._is_timestamp_editor_mode():
            state["end_time"] = self._format_timestamp_editor_time((track or {}).get("end_time", 0.0))
            state["duration"] = self._format_timestamp_editor_time((track or {}).get("duration_sec", 0.0))
            state["identification_source"] = self._track_badge_source_value(track, include_timestamp=True)
        return state

    def _read_track_form_state(self):
        if self._is_timestamp_editor_mode():
            return {
                "file_name": "",
                "artist": self.artist_input.text().strip(),
                "title": self.title_input.text().strip(),
                "album": "",
                "genre": "",
                "bpm": "",
                "year": "",
                "isrc": "",
                "start_time": self.start_time_input.text().strip(),
            }
        return {
            "file_name": self.file_name_input.text().strip(),
            "artist": self.artist_input.text().strip(),
            "title": self.title_input.text().strip(),
            "album": self.album_input.text().strip(),
            "genre": self.genre_input.text().strip(),
            "bpm": self.bpm_input.text().strip(),
            "year": self.year_input.text().strip(),
            "isrc": self.isrc_input.text().strip(),
            "start_time": "",
        }

    def _read_bulk_form_state(self):
        return {
            "artist": self.bulk_artist_input.text().strip(),
            "album": self.bulk_album_input.text().strip(),
            "genre": self.bulk_genre_input.text().strip(),
            "year": self.bulk_year_input.text().strip(),
        }

    def _clearable_track_form_keys(self):
        if self._is_timestamp_editor_mode():
            return ("artist", "title")
        return ("artist", "title", "album", "genre", "bpm", "year", "isrc")

    def _clear_selected_track_form_fields(self):
        idx, track = self._current_track()
        if idx < 0 or not isinstance(track, dict):
            return
        if str(track.get("status") or "").strip().lower() == "skipped":
            self.preview_status_label.setText("Restore this track before clearing metadata.")
            return

        form_state = self._track_form_display_state(track)
        form_state.update(dict(self._pending_track_edits.get(idx) or {}))
        for key in self._clearable_track_form_keys():
            form_state[key] = ""
        self._populate_track_form(form_state)
        self._store_form_state_for_track(idx)
        self.preview_status_label.setText("Cleared current track metadata fields.")
        self.artist_input.setFocus()

    def _bulk_form_has_values(self):
        if not hasattr(self, "bulk_artist_input"):
            return False
        return any(bool(str(value or "").strip()) for value in self._read_bulk_form_state().values())

    def _clear_bulk_form(self):
        if not hasattr(self, "bulk_artist_input"):
            return
        self.bulk_artist_input.setText("")
        self.bulk_album_input.setText("")
        self.bulk_genre_input.setText("")
        self.bulk_year_input.setText("")
        self._sync_bulk_edit_ui()
        self._sync_apply_edits_button()
        self._set_title()

    def _sync_bulk_edit_ui(self):
        if not hasattr(self, "bulk_edit_title_label"):
            return
        checked_count = len(self._checked_track_indices())
        self.bulk_edit_title_label.setText(f"Bulk Edit Checked Tracks ({checked_count} selected)")
        self._sync_bulk_check_all_ui()

    def _populate_track_form(self, form_state):
        state = dict(form_state or {})
        self.file_name_input.setText(str(state.get("file_name") or ""))
        self.artist_input.setText(str(state.get("artist") or ""))
        self.title_input.setText(str(state.get("title") or ""))
        self.album_input.setText(str(state.get("album") or ""))
        self.genre_input.setText(str(state.get("genre") or ""))
        self.bpm_input.setText(str(state.get("bpm") or ""))
        self.year_input.setText(str(state.get("year") or ""))
        self.isrc_input.setText(str(state.get("isrc") or ""))
        self.start_time_input.setText(str(state.get("start_time") or ""))
        self.end_time_input.setText(str(state.get("end_time") or ""))
        self.duration_input.setText(str(state.get("duration") or ""))
        self.identification_source_input.setText(str(state.get("identification_source") or ""))

    def _checked_track_indices(self):
        return sorted(
            idx
            for idx in list(self._bulk_checked_track_indices)
            if isinstance(idx, int) and 0 <= idx < len(self.tracks)
        )

    def _pending_apply_track_indices(self):
        pending = {
            idx
            for idx in self._pending_track_edits.keys()
            if isinstance(idx, int) and 0 <= idx < len(self.tracks)
        }
        if self._bulk_form_has_values():
            pending.update(self._checked_track_indices())
        return sorted(pending)

    def _set_track_form_enabled(self, enabled):
        for field in (
            self.file_name_input,
            self.artist_input,
            self.title_input,
            self.album_input,
            self.genre_input,
            self.bpm_input,
            self.year_input,
            self.isrc_input,
            self.start_time_input,
            self.end_time_input,
            self.duration_input,
            self.identification_source_input,
        ):
            field.setEnabled(bool(enabled))

    def _sync_apply_edits_button(self):
        count = len(self._pending_apply_track_indices())
        if count > 0:
            self.apply_edits_btn.setText(f"Apply Edits ({count})")
        else:
            self.apply_edits_btn.setText("Apply Edits")
        self.apply_edits_btn.setEnabled(bool(self._current_track_index() >= 0 or count > 0))
        self._sync_clear_fields_button()

    def _sync_clear_fields_button(self):
        button = getattr(self, "clear_fields_btn", None)
        if button is None:
            return
        _idx, track = self._current_track()
        enabled = (
            isinstance(track, dict)
            and str(track.get("status") or "").strip().lower() != "skipped"
        )
        button.setEnabled(enabled)

    def _sync_current_form_to_track(self, track_index):
        if track_index < 0 or track_index >= len(self.tracks):
            return
        if track_index != self._displayed_track_index:
            return
        self._populate_track_form(self._track_form_display_state(self.tracks[track_index]))

    def _store_form_state_for_track(self, track_index):
        if track_index < 0 or track_index >= len(self.tracks):
            self._sync_apply_edits_button()
            self._set_title()
            return
        track = self.tracks[track_index]
        form_state = self._read_track_form_state()
        if form_state == self._track_form_state(track):
            self._pending_track_edits.pop(track_index, None)
        else:
            self._pending_track_edits[track_index] = form_state
        self._sync_apply_edits_button()
        self._set_title()

    def _clear_pending_track_edit(self, track_index):
        self._pending_track_edits.pop(track_index, None)
        self._sync_apply_edits_button()
        self._set_title()

    def _on_track_form_text_edited(self, _text=""):
        if self._displayed_track_index < 0:
            return
        self._store_form_state_for_track(self._displayed_track_index)

    def _on_bulk_form_text_edited(self, _text=""):
        self._sync_apply_edits_button()
        self._set_title()

    def _on_track_item_changed(self, item):
        if item is None:
            return
        try:
            track_index = int(item.data(Qt.UserRole))
        except Exception:
            return
        if track_index < 0 or track_index >= len(self.tracks):
            return
        if item.checkState() == Qt.Checked:
            self._bulk_checked_track_indices.add(track_index)
        else:
            self._bulk_checked_track_indices.discard(track_index)
        self._sync_bulk_edit_ui()
        self._sync_apply_edits_button()
        self._set_title()

    def _visible_bulk_track_indices(self):
        if self._is_timestamp_editor_mode():
            return []
        return [
            idx
            for idx in list(self.filtered_indices or [])
            if isinstance(idx, int) and 0 <= idx < len(self.tracks)
        ]

    def _sync_bulk_check_all_ui(self):
        checkbox = getattr(self, "bulk_check_all_checkbox", None)
        if checkbox is None:
            return
        visible_indices = self._visible_bulk_track_indices()
        all_visible_checked = bool(visible_indices) and all(
            idx in self._bulk_checked_track_indices for idx in visible_indices
        )
        self._bulk_check_all_syncing = True
        try:
            checkbox.setEnabled(bool(visible_indices))
            checkbox.setChecked(bool(all_visible_checked))
        finally:
            self._bulk_check_all_syncing = False

    def _on_bulk_check_all_toggled(self, checked):
        if self._bulk_check_all_syncing:
            return
        visible_indices = set(self._visible_bulk_track_indices())
        if not visible_indices:
            return
        if checked:
            self._bulk_checked_track_indices.update(visible_indices)
        else:
            self._bulk_checked_track_indices.difference_update(visible_indices)
        self.track_list.blockSignals(True)
        try:
            for row in range(self.track_list.count()):
                item = self.track_list.item(row)
                if item is None:
                    continue
                try:
                    track_index = int(item.data(Qt.UserRole))
                except Exception:
                    continue
                if track_index in visible_indices:
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.track_list.blockSignals(False)
        self._sync_bulk_edit_ui()
        self._sync_apply_edits_button()
        self._set_title()

    def _safe_filename(self, artist, title):
        value = f"{artist} - {title}.flac"
        return value.translate(str.maketrans("", "", '<>:"/\\|?*'))

    def _track_editable_filename(self, track):
        if not isinstance(track, dict):
            return ""
        status = str(track.get("status") or "")
        if status == "identified":
            primary = track.get("expected_filename")
            fallback = track.get("unidentified_filename")
        else:
            primary = track.get("unidentified_filename")
            fallback = track.get("expected_filename")
        candidate = str(primary or fallback or "").strip()
        if not candidate:
            candidate = os.path.basename(str(track.get("original_file") or "").strip())
        return os.path.basename(candidate).strip()

    def _track_filename_display(self, track):
        full_name = self._track_editable_filename(track)
        if not full_name:
            return ""
        return os.path.splitext(os.path.basename(full_name))[0]

    def _sanitize_track_filename(self, track, raw_name):
        value = os.path.basename(str(raw_name or "").strip())
        if not value:
            return ""
        value = value.translate(str.maketrans("", "", '<>:"/\\|?*')).strip()
        value = value.rstrip(". ").strip()
        if value in ("", ".", ".."):
            return ""
        base, ext = os.path.splitext(value)

        source_ext = ""
        if str(getattr(self, "editor_mode", "") or "") == "standalone":
            source_file = str((track or {}).get("original_file") or "").strip()
            source_ext = os.path.splitext(os.path.basename(source_file))[1]
            if source_ext:
                if not ext:
                    ext = source_ext
                elif ext.lower() != source_ext.lower():
                    ext = source_ext

        if not ext:
            current_ext = os.path.splitext(self._track_editable_filename(track))[1]
            if not current_ext and str((track or {}).get("status") or "") == "identified":
                current_ext = ".flac"
            ext = current_ext

        if ext:
            value = f"{base}{ext}"
        return value

    def _track_counts(self):
        identified = len([t for t in self.tracks if t.get("status") == "identified"])
        unidentified = len([t for t in self.tracks if t.get("status") == "unidentified"])
        skipped = len([t for t in self.tracks if t.get("status") == "skipped"])
        no_genre = len(
            [
                t
                for t in self.tracks
                if t.get("status") == "identified" and not (t.get("enhanced_metadata") or {}).get("genres")
            ]
        )
        no_bpm = len(
            [
                t
                for t in self.tracks
                if t.get("status") == "identified" and not (t.get("enhanced_metadata") or {}).get("bpm")
            ]
        )
        return identified, unidentified, skipped, no_genre, no_bpm

    def _refresh_stats(self):
        identified, unidentified, skipped, no_genre, no_bpm = self._track_counts()
        if self._is_timestamp_editor_mode():
            source_count = len(
                {
                    os.path.abspath(str((track or {}).get("original_file") or "").strip())
                    for track in list(self.tracks or [])
                    if isinstance(track, dict)
                }
            )
            active = max(0, len(self.tracks) - skipped)
            self.stats_label.setText(
                f"Segments: {len(self.tracks)}    Active: {active}    "
                f"Identified: {identified}    Unnamed: {unidentified}    "
                f"Skipped: {skipped}    Files: {source_count}"
            )
            return
        self.stats_label.setText(
            f"Identified: {identified}    Unidentified: {unidentified}    "
            f"Skipped: {skipped}    Missing Genre: {no_genre}    Missing BPM: {no_bpm}"
        )

    def _track_passes_filter(self, track, filter_key):
        status = str(track.get("status") or "")
        enhanced = track.get("enhanced_metadata") or {}
        if filter_key == "identified":
            return status == "identified"
        if filter_key == "unidentified":
            return status == "unidentified"
        if filter_key == "no_genre":
            return status == "identified" and not enhanced.get("genres")
        if filter_key == "no_bpm":
            return status == "identified" and not enhanced.get("bpm")
        if filter_key == "skipped":
            return status == "skipped"
        return True

    def _track_row_text(self, track_index, track):
        status = str(track.get("status") or "unknown")
        source_badge = self._id_source_badge_text(
            self._track_badge_source_value(track, include_timestamp=self._is_timestamp_editor_mode())
        )
        badge_suffix = f"    {source_badge}" if source_badge else ""
        if self._is_timestamp_editor_mode():
            start_text = self._format_timestamp_editor_time((track or {}).get("start_time", 0.0))
            end_text = self._format_timestamp_editor_time((track or {}).get("end_time", 0.0))
            label = self._timestamp_track_label(track, track_index + 1)
            source_text = ""
            if len(self._timestamp_session_map()) > 1:
                source_name = str((track or {}).get("source_name") or "")
                if not source_name:
                    source_name = os.path.splitext(os.path.basename(str((track or {}).get("original_file") or "").strip()))[0]
                if source_name:
                    source_text = f"[{source_name}] "
            if status == "skipped":
                return f"{track_index + 1:03d}  ⏭️  {source_text}{start_text} - {end_text}    {label}{badge_suffix}"
            icon = "✅" if status == "identified" else "🕒"
            return f"{track_index + 1:03d}  {icon}  {source_text}{start_text} - {end_text}    {label}{badge_suffix}"
        if status == "identified":
            artist = str(track.get("artist") or "Unknown Artist")
            title = str(track.get("title") or "Unknown Title")
            genre_list = (track.get("enhanced_metadata") or {}).get("genres") or []
            genre = genre_list[0] if genre_list else "---"
            bpm = (track.get("enhanced_metadata") or {}).get("bpm") or "---"
            return f"{track_index + 1:03d}  ✅  {artist} - {title}    G:{genre}    B:{bpm}{badge_suffix}"
        if status == "unidentified":
            filename = str(track.get("unidentified_filename") or "Unidentified")
            bpm = track.get("detected_bpm")
            bpm_text = f"~{bpm}" if bpm else "---"
            return f"{track_index + 1:03d}  ❓  {filename}    BPM:{bpm_text}{badge_suffix}"
        reason = str(track.get("reason") or "skipped")
        return f"{track_index + 1:03d}  ⏭️  Skipped ({reason}){badge_suffix}"

    def _current_track_index(self):
        item = self.track_list.currentItem()
        if item is None:
            return -1
        try:
            value = int(item.data(Qt.UserRole))
        except Exception:
            return -1
        if value < 0 or value >= len(self.tracks):
            return -1
        return value

    def _current_track(self):
        idx = self._current_track_index()
        if idx < 0:
            return -1, None
        return idx, self.tracks[idx]

    def _refresh_track_list(self):
        selected_track_idx = self._current_track_index()
        filter_key = str(self.filter_combo.currentData() or "all")
        timestamp_mode = self._is_timestamp_editor_mode()
        if timestamp_mode:
            self._bulk_checked_track_indices.clear()
        self._bulk_checked_track_indices = {
            idx for idx in self._bulk_checked_track_indices
            if isinstance(idx, int) and 0 <= idx < len(self.tracks)
        }

        self.track_list.blockSignals(True)
        self.track_list.clear()
        self.filtered_indices = []
        selected_row = -1
        for idx, track in enumerate(self.tracks):
            if not self._track_passes_filter(track, filter_key):
                continue
            text = self._track_row_text(idx, track)
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, idx)
            if not timestamp_mode:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if idx in self._bulk_checked_track_indices else Qt.Unchecked)
            self.track_list.addItem(item)
            self.filtered_indices.append(idx)
            if idx == selected_track_idx:
                selected_row = self.track_list.count() - 1

        if selected_row >= 0:
            self.track_list.setCurrentRow(selected_row)
        elif self.track_list.count() > 0:
            self.track_list.setCurrentRow(0)
        self.track_list.blockSignals(False)
        self._refresh_stats()
        self._sync_bulk_edit_ui()
        self._on_track_selected()

    def _on_track_selected(self):
        self._store_form_state_for_track(self._displayed_track_index)
        idx, track = self._current_track()
        self._displayed_track_index = idx
        if track is None:
            self.detail_status_label.setText("Status: No track selected")
            self.detail_file_label.setText("Source: -")
            self._populate_track_form({})
            self._set_track_form_enabled(False)
            self._sync_apply_edits_button()
            return

        self._set_track_form_enabled(True)

        status = str(track.get("status") or "unknown")
        reason = str(track.get("reason") or "").strip()
        source_name = os.path.basename(str(track.get("original_file") or "").strip())
        if not source_name:
            source_name = self._track_editable_filename(track)
        if self._is_timestamp_editor_mode():
            source_bits = [f"Source: {source_name or '-'}"]
            source_bits.append(f"Start: {self._format_timestamp_editor_time(track.get('start_time', 0.0))}")
            source_bits.append(f"End: {self._format_timestamp_editor_time(track.get('end_time', 0.0))}")
            source_bits.append(f"Duration: {self._format_timestamp_editor_time(track.get('duration_sec', 0.0))}")
            self.detail_file_label.setText("    ".join(source_bits))
            source_name_text = self._track_badge_source_value(track, include_timestamp=True)
            confidence = track.get("confidence")
            detail_parts = [f"Status: {status}"]
            if source_name_text:
                detail_parts.append(f"via {source_name_text}")
            if confidence not in (None, ""):
                try:
                    detail_parts.append(f"({float(confidence):.2f})")
                except Exception:
                    pass
            if reason:
                detail_parts.append(f"[{reason}]")
            self.detail_status_label.setText(" ".join(detail_parts))
        else:
            if reason:
                self.detail_status_label.setText(f"Status: {status} ({reason})")
            else:
                self.detail_status_label.setText(f"Status: {status}")
            self.detail_file_label.setText(f"Source: {source_name or '-'}")
        display_state = self._track_form_display_state(track)
        pending = dict(self._pending_track_edits.get(idx) or {})
        display_state.update(pending)
        self._populate_track_form(display_state)

        is_skipped = status == "skipped"
        self.mb_search_btn.setEnabled((not self._musicbrainz_busy) and status in ("unidentified", "identified"))
        self.delete_restore_btn.setText("Restore" if is_skipped and reason == "user_deleted" else "Delete/Restore")
        self._sync_apply_edits_button()

    def _mark_changed(self):
        self.changes_made = True
        self._set_title()

    def _apply_enhanced_metadata(self, track, enhanced):
        if not isinstance(enhanced, dict):
            return
        track.setdefault("enhanced_metadata", {})
        for key in ("genres", "release_date", "label", "isrc", "bpm"):
            if key in enhanced and enhanced.get(key):
                track["enhanced_metadata"][key] = enhanced.get(key)

    def _apply_standalone_track_metadata_to_source(self, track):
        """Write current metadata edits into the loaded source audio file."""
        source_file = str((track or {}).get("original_file") or "").strip()
        if not source_file:
            return False, "No source file is attached to this track."
        if not os.path.exists(source_file):
            return False, "The source file no longer exists."

        status = str((track or {}).get("status") or "")
        if status != "identified":
            return False, "Identify this track before applying metadata."

        artist = str((track or {}).get("artist") or "").strip()
        title = str((track or {}).get("title") or "").strip()
        if not artist or not title:
            return False, "Artist and title are required."

        if mixsplitr_tagging is None or not hasattr(mixsplitr_tagging, "retag_file"):
            return False, "Tagging is unavailable."

        enhanced_metadata = (track or {}).get("enhanced_metadata") or {}
        album = (track or {}).get("album") or ""

        try:
            mixsplitr_tagging.retag_file(
                source_file,
                artist,
                title,
                album=album,
                enhanced_metadata=enhanced_metadata,
            )
            return True, f"Updated {os.path.basename(source_file)}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _rename_standalone_track_source_file(self, track, edited_file_name):
        """Rename the loaded standalone source audio file on disk."""
        source_file = str((track or {}).get("original_file") or "").strip()
        if not source_file:
            return False, False, "No source file is attached to this track."
        source_file = os.path.abspath(source_file)
        if not os.path.exists(source_file):
            return False, False, "The source file no longer exists."

        target_name = self._sanitize_track_filename(track, edited_file_name)
        target_name = str(target_name or "").strip()
        if not target_name:
            return False, False, "Enter a valid file name."

        target_path = os.path.abspath(os.path.join(os.path.dirname(source_file), target_name))
        if source_file == target_path:
            return True, False, ""

        target_exists = os.path.exists(target_path)
        same_file_target = False
        if target_exists:
            try:
                same_file_target = os.path.samefile(source_file, target_path)
            except Exception:
                same_file_target = False
            if not same_file_target:
                return (
                    False,
                    False,
                    f"A file named '{os.path.basename(target_path)}' already exists in this folder.",
                )

        rename_error = None
        try:
            os.rename(source_file, target_path)
        except Exception as exc:
            rename_error = exc

        if rename_error is not None and same_file_target:
            temp_path = f"{source_file}.mixsplitr_tmp_{int(time.time() * 1000)}"
            try:
                os.rename(source_file, temp_path)
                os.rename(temp_path, target_path)
                rename_error = None
            except Exception as temp_exc:
                rename_error = temp_exc
                try:
                    if os.path.exists(temp_path) and not os.path.exists(source_file):
                        os.rename(temp_path, source_file)
                except Exception:
                    pass

        if rename_error is not None:
            return False, False, f"{type(rename_error).__name__}: {rename_error}"

        track["original_file"] = target_path
        track["unidentified_filename"] = os.path.basename(target_path)
        return True, True, f"Renamed to {os.path.basename(target_path)}"

    def _track_apply_label(self, track, form_state=None):
        state = dict(form_state or {})
        artist = str(state.get("artist") or (track or {}).get("artist") or "").strip()
        title = str(state.get("title") or (track or {}).get("title") or "").strip()
        if artist and title:
            return f"{artist} - {title}"
        if title:
            return title
        file_name = str(state.get("file_name") or self._track_filename_display(track) or "").strip()
        if file_name:
            return file_name
        source_name = os.path.basename(str((track or {}).get("original_file") or "").strip())
        return source_name or "Track"

    def _compose_apply_form_state(self, track_index, bulk_state=None):
        if track_index < 0 or track_index >= len(self.tracks):
            return {}
        track = self.tracks[track_index]
        base_state = self._track_form_state(track)
        state = dict(base_state)
        bulk_values = dict(bulk_state or {})
        if bulk_values and track_index in self._bulk_checked_track_indices:
            for key, value in bulk_values.items():
                value_text = str(value or "").strip()
                if value_text:
                    state[key] = value_text
        pending = self._pending_track_edits.get(track_index)
        if pending:
            for key, value in dict(pending).items():
                if str(value or "") != str(base_state.get(key) or ""):
                    state[key] = value
        return state

    def _apply_form_state_to_track(self, track, form_state, *, apply_source_changes=True):
        result = {
            "applied": False,
            "source_applied": False,
            "source_renamed": False,
            "warnings": [],
            "validation_title": "",
            "validation_message": "",
        }
        if not isinstance(track, dict):
            return result

        status = str(track.get("status") or "")
        if status == "skipped":
            result["validation_title"] = "Track Skipped"
            result["validation_message"] = "Restore this track before editing metadata."
            return result

        if self._is_timestamp_editor_mode():
            state = dict(form_state or {})
            artist = str(state.get("artist") or "").strip()
            title = str(state.get("title") or "").strip()
            start_time_input = str(state.get("start_time") or "").strip()
            if artist and not title:
                result["validation_title"] = "Missing Title"
                result["validation_message"] = "Enter a title when setting an artist for a timestamp segment."
                return result
            try:
                parsed_start_time = self._parse_timestamp_editor_time(start_time_input)
            except ValueError as exc:
                result["validation_title"] = "Invalid Start Time"
                result["validation_message"] = str(exc)
                return result

            current_artist = str(track.get("artist") or "").strip()
            current_title = str(track.get("title") or "").strip()
            try:
                current_start = float(track.get("start_time", 0.0) or 0.0)
            except Exception:
                current_start = 0.0

            applied = False
            if artist != current_artist:
                track["artist"] = artist
                applied = True
            if title != current_title:
                track["title"] = title
                applied = True
            if abs(parsed_start_time - current_start) > 0.0005:
                track["start_time"] = parsed_start_time
                applied = True

            track.setdefault("enhanced_metadata", {})
            track["album"] = str(track.get("album") or "Timestamping")
            if artist and title:
                if str(track.get("status") or "").strip().lower() != "identified":
                    applied = True
                track["status"] = "identified"
                track.pop("reason", None)
                track["expected_filename"] = self._safe_filename(artist, title)
            else:
                if str(track.get("status") or "").strip().lower() != "unidentified":
                    applied = True
                track["status"] = "unidentified"
                track.pop("reason", None)
                track.pop("expected_filename", None)

            if artist and title:
                label = f"{artist} - {title}"
            else:
                label = title
            track["timeline_label"] = label
            track["unidentified_filename"] = label
            if title and not artist:
                track["enhanced_metadata"]["output_filename_base"] = title
                track["enhanced_metadata"]["output_title_only_filename"] = True
            elif "output_title_only_filename" in track["enhanced_metadata"]:
                track["enhanced_metadata"].pop("output_title_only_filename", None)

            result["applied"] = bool(applied)
            return result

        state = dict(form_state or {})
        artist = str(state.get("artist") or "").strip()
        title = str(state.get("title") or "").strip()
        album = str(state.get("album") or "").strip()
        genre_text = str(state.get("genre") or "").strip()
        bpm_text = str(state.get("bpm") or "").strip()
        year = str(state.get("year") or "").strip()
        isrc = str(state.get("isrc") or "").strip()
        file_name_input = str(state.get("file_name") or "").strip()
        edited_file_name = self._sanitize_track_filename(track, file_name_input)
        if file_name_input and not edited_file_name:
            result["validation_title"] = "Invalid Filename"
            result["validation_message"] = "Enter a valid file name."
            return result

        metadata_entered = bool(artist or title or album or genre_text or bpm_text or year or isrc)
        if metadata_entered and (not artist or not title):
            result["validation_title"] = "Missing Metadata"
            result["validation_message"] = "Artist and title are required whenever metadata is present."
            return result

        promoted_from_unidentified = False
        if status == "unidentified" and artist and title:
            track["status"] = "identified"
            track.pop("reason", None)
            track["artist"] = artist
            track["title"] = title
            track["album"] = album or "Unknown Album"
            promoted_from_unidentified = True

        current_artist = str(track.get("artist") or "").strip()
        current_title = str(track.get("title") or "").strip()
        current_album = str(track.get("album") or "").strip()
        current_enhanced = track.get("enhanced_metadata") or {}
        current_genres = current_enhanced.get("genres") or []
        current_genre_text = ", ".join([str(g).strip() for g in current_genres if str(g).strip()])
        current_bpm = str(current_enhanced.get("bpm") or "")
        current_year = str(current_enhanced.get("release_date") or "")
        current_isrc = str(current_enhanced.get("isrc") or "")

        edits = {}
        if artist != current_artist:
            edits["artist"] = artist
        if title != current_title:
            edits["title"] = title
        if album != current_album:
            edits["album"] = album
        if genre_text != current_genre_text:
            edits["genre"] = genre_text
        if bpm_text != current_bpm:
            edits["bpm"] = bpm_text
        if year != current_year:
            edits["year"] = year
        if isrc != current_isrc:
            edits["isrc"] = isrc
        if file_name_input and edited_file_name:
            edits["filename"] = edited_file_name

        has_metadata_edits = any(k in edits for k in {"artist", "title", "album", "genre", "bpm", "year", "isrc"})

        applied = False
        if mixsplitr_editor and hasattr(mixsplitr_editor, "apply_track_edits"):
            try:
                applied = bool(mixsplitr_editor.apply_track_edits(track, edits))
            except Exception:
                applied = False
        if not applied:
            track.setdefault("enhanced_metadata", {})
            if "artist" in edits:
                track["artist"] = edits["artist"]
            if "title" in edits:
                track["title"] = edits["title"]
            if "album" in edits:
                track["album"] = edits["album"]
            if "genre" in edits:
                genres = [g.strip() for g in edits["genre"].split(",") if g.strip()]
                if genres:
                    track["enhanced_metadata"]["genres"] = genres
                else:
                    track["enhanced_metadata"].pop("genres", None)
            if "year" in edits:
                if edits["year"]:
                    track["enhanced_metadata"]["release_date"] = edits["year"]
                else:
                    track["enhanced_metadata"].pop("release_date", None)
            if "isrc" in edits:
                if edits["isrc"]:
                    track["enhanced_metadata"]["isrc"] = edits["isrc"]
                else:
                    track["enhanced_metadata"].pop("isrc", None)
            if "bpm" in edits:
                if edits["bpm"]:
                    try:
                        track["enhanced_metadata"]["bpm"] = int(edits["bpm"])
                    except Exception:
                        pass
                else:
                    track["enhanced_metadata"].pop("bpm", None)
            if track.get("artist") and track.get("title"):
                track["expected_filename"] = self._safe_filename(track.get("artist"), track.get("title"))
            else:
                track.pop("expected_filename", None)
            applied = bool(edits or promoted_from_unidentified)

        if not artist and not title and not metadata_entered:
            if str(track.get("status") or "").strip().lower() != "unidentified":
                track["status"] = "unidentified"
                track.pop("reason", None)
                applied = True
            track.pop("expected_filename", None)
            existing_unidentified = str(track.get("unidentified_filename") or "").strip()
            if not existing_unidentified:
                fallback_name = edited_file_name or os.path.basename(
                    str(track.get("original_file") or "").strip()
                )
                if fallback_name:
                    track["unidentified_filename"] = os.path.basename(fallback_name)

        should_apply_to_source_file = bool(
            self.editor_mode == "standalone"
            and has_metadata_edits
            and artist
            and title
        )
        should_rename_source_file = bool(self.editor_mode == "standalone" and edited_file_name)
        if edited_file_name:
            target_key = "expected_filename" if str(track.get("status") or "") == "identified" else "unidentified_filename"
            if str(track.get(target_key) or "").strip() != edited_file_name:
                track[target_key] = edited_file_name
                track.setdefault("enhanced_metadata", {})
                filename_base = os.path.splitext(edited_file_name)[0].strip()
                if filename_base:
                    track["enhanced_metadata"]["output_filename_base"] = filename_base
                elif "output_filename_base" in track.get("enhanced_metadata", {}):
                    track["enhanced_metadata"].pop("output_filename_base", None)
                applied = True

        if applied and apply_source_changes:
            track_label = self._track_apply_label(track, form_state)
            if should_rename_source_file:
                rename_ok, source_renamed, rename_result = self._rename_standalone_track_source_file(
                    track,
                    edited_file_name,
                )
                result["source_renamed"] = bool(source_renamed)
                if not rename_ok:
                    result["warnings"].append(f"{track_label}: {rename_result or 'Unknown source-rename error'}")
            if should_apply_to_source_file:
                source_applied, source_apply_error = self._apply_standalone_track_metadata_to_source(track)
                result["source_applied"] = bool(source_applied)
                if not source_applied and source_apply_error:
                    result["warnings"].append(f"{track_label}: {source_apply_error}")

        result["applied"] = bool(applied)
        return result

    def _apply_form_edits(self, show_feedback=True):
        self._store_form_state_for_track(self._displayed_track_index)
        bulk_state = self._read_bulk_form_state()
        bulk_target_indices = set(self._checked_track_indices()) if self._bulk_form_has_values() else set()
        apply_indices = self._pending_apply_track_indices()
        if not apply_indices:
            self._sync_apply_edits_button()
            if show_feedback:
                if self._bulk_form_has_values() and not bulk_target_indices:
                    self.preview_status_label.setText("Bulk fields entered, but no tracks are checked")
                else:
                    self.preview_status_label.setText("No staged edits to apply")
            return True

        for track_idx in apply_indices:
            track = self.tracks[track_idx]
            form_state = self._compose_apply_form_state(track_idx, bulk_state)
            validation = self._apply_form_state_to_track(
                copy.deepcopy(track),
                form_state,
                apply_source_changes=False,
            )
            if validation.get("validation_message"):
                self._select_track_by_index(track_idx)
                dialog = QMessageBox.information if validation.get("validation_title") == "Track Skipped" else QMessageBox.warning
                dialog(
                    self,
                    str(validation.get("validation_title") or "Apply Edits"),
                    str(validation.get("validation_message") or "Unable to apply staged edits."),
                )
                return False

        selected_track_idx = self._current_track_index()
        selected_track_ref = self.tracks[selected_track_idx] if selected_track_idx >= 0 else None
        warnings = []
        applied_count = 0
        source_apply_count = 0
        source_rename_count = 0

        for track_idx in apply_indices:
            track = self.tracks[track_idx]
            form_state = self._compose_apply_form_state(track_idx, bulk_state)
            result = self._apply_form_state_to_track(track, form_state, apply_source_changes=True)
            if result.get("applied"):
                applied_count += 1
            if result.get("source_applied"):
                source_apply_count += 1
            if result.get("source_renamed"):
                source_rename_count += 1
            warnings.extend(list(result.get("warnings") or []))
            self._pending_track_edits.pop(track_idx, None)

        bulk_applied = bool(bulk_target_indices)
        if bulk_applied:
            self._bulk_checked_track_indices.clear()
            self._clear_bulk_form()

        if applied_count > 0:
            self._mark_changed()
        else:
            self._set_title()

        if self._is_timestamp_editor_mode():
            self._normalize_timestamp_tracks()
            if selected_track_ref in self.tracks:
                selected_track_idx = self.tracks.index(selected_track_ref)

        self._refresh_track_list()
        if selected_track_idx >= 0:
            self._select_track_by_index(selected_track_idx)
        self._sync_apply_edits_button()

        if warnings:
            warning_lines = warnings[:8]
            if len(warnings) > len(warning_lines):
                warning_lines.append(f"... and {len(warnings) - len(warning_lines)} more warning(s).")
            QMessageBox.warning(self, "Apply Edits", "\n".join(warning_lines))

        if show_feedback:
            if applied_count <= 0:
                self.preview_status_label.setText("Staged edits matched existing track data")
            elif self.editor_mode == "standalone":
                parts = [f"Applied edits to {applied_count} track(s)"]
                if source_rename_count:
                    parts.append(f"{source_rename_count} source file(s) renamed")
                if source_apply_count:
                    parts.append(f"{source_apply_count} source file(s) retagged")
                if bulk_applied:
                    parts.append(f"{len(bulk_target_indices)} bulk target(s)")
                if warnings:
                    parts.append(f"{len(warnings)} warning(s)")
                self.preview_status_label.setText("; ".join(parts))
            else:
                parts = [f"Applied edits to {applied_count} track(s)"]
                if bulk_applied:
                    parts.append(f"{len(bulk_target_indices)} bulk target(s)")
                if warnings:
                    parts.append(f"{len(warnings)} warning(s)")
                self.preview_status_label.setText("; ".join(parts))
        return True

    def _select_track_by_index(self, track_index):
        self.track_list.blockSignals(True)
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item is None:
                continue
            try:
                item_track_idx = int(item.data(Qt.UserRole))
            except Exception:
                continue
            if item_track_idx == track_index:
                self.track_list.setCurrentRow(row)
                break
        self.track_list.blockSignals(False)
        self._on_track_selected()

    def _choose_from_items(self, title, label, items):
        if not items:
            return -1
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumSize(860, 520)

        layout = QVBoxLayout(dialog)
        prompt = QLabel(f"{label} ({len(items)} results)")
        layout.addWidget(prompt)

        list_widget = QListWidget(dialog)
        list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        for idx, text in enumerate(items):
            list_widget.addItem(f"{idx + 1}. {text}")
        list_widget.setCurrentRow(0)
        list_widget.itemDoubleClicked.connect(lambda _item: dialog.accept())
        layout.addWidget(list_widget, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", dialog)
        select_btn = QPushButton("Select", dialog)
        select_btn.setDefault(True)
        cancel_btn.clicked.connect(dialog.reject)
        select_btn.clicked.connect(dialog.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(select_btn)
        layout.addLayout(button_row)

        if dialog.exec() != QDialog.Accepted:
            return -1

        index = int(list_widget.currentRow())
        if index < 0 or index >= len(items):
            return -1
        return index

    def _musicbrainz_available(self):
        editor_module = mixsplitr_editor or _lazy_import_backend("mixsplitr_editor")
        if not editor_module:
            return False
        if hasattr(editor_module, "is_musicbrainz_available"):
            try:
                return bool(editor_module.is_musicbrainz_available())
            except Exception:
                return False
        return False

    def _set_musicbrainz_busy(self, busy, message=None, button_text=None):
        self._musicbrainz_busy = bool(busy)
        button = getattr(self, "mb_search_btn", None)
        if button is not None:
            if busy:
                button.setEnabled(False)
                button.setText(str(button_text or "Searching..."))
            else:
                button.setText("MusicBrainz Match")
                _idx, track = self._current_track()
                status = str((track or {}).get("status") or "").strip().lower()
                button.setEnabled(status in ("identified", "unidentified"))
        if message is not None and hasattr(self, "preview_status_label"):
            self.preview_status_label.setText(str(message))

    def _cleanup_musicbrainz_search_thread(self, thread=None):
        active_thread = thread or self._musicbrainz_search_thread
        if active_thread is self._musicbrainz_search_thread:
            self._musicbrainz_search_thread = None
        if active_thread is not None:
            try:
                active_thread.deleteLater()
            except Exception:
                pass

    def _cleanup_musicbrainz_tracklist_thread(self, thread=None):
        active_thread = thread or self._musicbrainz_tracklist_thread
        if active_thread is self._musicbrainz_tracklist_thread:
            self._musicbrainz_tracklist_thread = None
        if active_thread is not None:
            try:
                active_thread.deleteLater()
            except Exception:
                pass

    def _cleanup_musicbrainz_enhanced_thread(self, thread=None):
        active_thread = thread or self._musicbrainz_enhanced_thread
        if active_thread is self._musicbrainz_enhanced_thread:
            self._musicbrainz_enhanced_thread = None
        if active_thread is not None:
            try:
                active_thread.deleteLater()
            except Exception:
                pass

    def _musicbrainz_pick_from_loaded_album(self, album, tracklist_data):
        tracks = list((tracklist_data or {}).get("tracks") or [])
        if not tracks:
            QMessageBox.information(self, "MusicBrainz", "No tracks found for selected album.")
            return None

        track_display = []
        for t in tracks:
            pos = t.get("position")
            position = f"[{pos}] " if pos else ""
            title = str(t.get("title") or "Unknown")
            artist = str(t.get("artist") or "")
            artist_text = f" - {artist}" if artist else ""
            track_display.append(f"{position}{title}{artist_text}")
        track_index = self._choose_from_items("Album Tracks", "Choose a track:", track_display)
        if track_index < 0:
            return None
        selected_track = tracks[track_index]
        chosen_artist = str(selected_track.get("artist") or "").strip()
        if not chosen_artist:
            chosen_artist = ", ".join(album.get("artists") or ["Unknown Artist"])
        return {
            "artist": chosen_artist,
            "title": str(selected_track.get("title") or ""),
            "album": str(tracklist_data.get("title") or album.get("title") or ""),
            "recording_id": selected_track.get("recording_id"),
        }

    def _musicbrainz_pick_from_album(self, album):
        release_id = str(album.get("release_id") or "")
        if not release_id:
            QMessageBox.warning(self, "MusicBrainz", "Selected album has no release ID.")
            return None

        if not hasattr(mixsplitr_editor, "musicbrainz_get_release_tracklist"):
            return None
        try:
            tracklist_data = mixsplitr_editor.musicbrainz_get_release_tracklist(release_id) or {}
        except Exception as exc:
            QMessageBox.warning(self, "MusicBrainz", f"Tracklist lookup failed: {exc}")
            return None
        return self._musicbrainz_pick_from_loaded_album(album, tracklist_data)

    def _musicbrainz_choose_two_column_match(self, tracks, albums):
        dialog = QDialog(self)
        dialog.setWindowTitle("MusicBrainz Matches")
        dialog.setModal(True)
        dialog.setMinimumSize(1220, 620)

        root = QVBoxLayout(dialog)
        root.addWidget(
            QLabel(
                f"Choose a result ({len(tracks)} tracks, {len(albums)} albums). "
                "Selecting an album opens its tracklist next."
            )
        )

        columns = QHBoxLayout()

        track_col = QVBoxLayout()
        track_col.addWidget(QLabel(f"Tracks ({len(tracks)})"))
        track_list = QListWidget(dialog)
        track_list.setSelectionMode(QAbstractItemView.SingleSelection)
        for row in tracks:
            artist = str(row.get("artist") or "Unknown")
            title = str(row.get("title") or "Unknown")
            album = str(row.get("album") or "")
            score = row.get("score")
            score_text = f"score {score}" if score is not None else "no score"
            album_text = f" | {album}" if album else ""
            track_list.addItem(f"{artist} - {title}{album_text} ({score_text})")
        if not tracks:
            track_list.addItem("No track results")
            track_list.setEnabled(False)
        track_col.addWidget(track_list, 1)
        columns.addLayout(track_col, 1)

        album_col = QVBoxLayout()
        album_col.addWidget(QLabel(f"Albums ({len(albums)})"))
        album_list = QListWidget(dialog)
        album_list.setSelectionMode(QAbstractItemView.SingleSelection)
        for alb in albums:
            title = str(alb.get("title") or "Unknown")
            artists = ", ".join(alb.get("artists") or ["Unknown"])
            year = str(alb.get("date") or "")[:4]
            year_text = f" ({year})" if year else ""
            score = alb.get("score")
            score_text = f"score {score}" if score is not None else "no score"
            album_list.addItem(f"{title}{year_text} - {artists} ({score_text})")
        if not albums:
            album_list.addItem("No album results")
            album_list.setEnabled(False)
        album_col.addWidget(album_list, 1)
        columns.addLayout(album_col, 1)

        root.addLayout(columns, 1)

        selected = {"kind": None, "index": -1}

        def _set_track_selected(row):
            if row < 0:
                return
            if album_list.currentRow() >= 0:
                album_list.blockSignals(True)
                album_list.clearSelection()
                album_list.blockSignals(False)
            selected["kind"] = "track"
            selected["index"] = int(row)

        def _set_album_selected(row):
            if row < 0:
                return
            if track_list.currentRow() >= 0:
                track_list.blockSignals(True)
                track_list.clearSelection()
                track_list.blockSignals(False)
            selected["kind"] = "album"
            selected["index"] = int(row)

        track_list.currentRowChanged.connect(_set_track_selected)
        album_list.currentRowChanged.connect(_set_album_selected)

        track_list.itemDoubleClicked.connect(lambda _item: dialog.accept())
        album_list.itemDoubleClicked.connect(lambda _item: dialog.accept())

        if tracks:
            track_list.setCurrentRow(0)
            selected["kind"] = "track"
            selected["index"] = 0
        elif albums:
            album_list.setCurrentRow(0)
            selected["kind"] = "album"
            selected["index"] = 0

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", dialog)
        select_btn = QPushButton("Select", dialog)
        select_btn.setDefault(True)

        def _on_select():
            kind = selected.get("kind")
            idx = int(selected.get("index", -1))
            if kind == "track" and 0 <= idx < len(tracks):
                dialog.accept()
                return
            if kind == "album" and 0 <= idx < len(albums):
                dialog.accept()
                return
            QMessageBox.information(dialog, "MusicBrainz", "Select a track or album first.")

        cancel_btn.clicked.connect(dialog.reject)
        select_btn.clicked.connect(_on_select)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(select_btn)
        root.addLayout(button_row)

        if dialog.exec() != QDialog.Accepted:
            return None, None

        kind = selected.get("kind")
        idx = int(selected.get("index", -1))
        if kind == "track" and 0 <= idx < len(tracks):
            return "track", tracks[idx]
        if kind == "album" and 0 <= idx < len(albums):
            return "album", albums[idx]
        return None, None

    def _start_musicbrainz_combined_search(self, track_index, query):
        if self._musicbrainz_search_thread is not None:
            return
        thread = MusicBrainzCombinedSearchThread(query, self)
        thread.search_finished.connect(
            lambda payload, _idx=track_index, _thread=thread: self._on_musicbrainz_combined_search_finished(
                _idx,
                payload,
                _thread,
            )
        )
        self._musicbrainz_search_thread = thread
        self._set_musicbrainz_busy(
            True,
            message=f"Searching MusicBrainz for \"{query}\"...",
            button_text="Searching...",
        )
        thread.start()

    def _on_musicbrainz_combined_search_finished(self, track_index, payload, thread=None):
        self._cleanup_musicbrainz_search_thread(thread)
        self._set_musicbrainz_busy(False)

        tracks = list((payload or {}).get("tracks") or [])
        albums = list((payload or {}).get("albums") or [])
        search_errors = list((payload or {}).get("errors") or [])
        if not tracks and not albums:
            if search_errors:
                QMessageBox.warning(self, "MusicBrainz", f"Search failed: {'; '.join(search_errors)}")
                self.preview_status_label.setText("MusicBrainz search failed.")
            else:
                QMessageBox.information(self, "MusicBrainz", "No results found.")
                self.preview_status_label.setText("MusicBrainz search returned no results.")
            return

        track_choices = tracks[:20]
        album_choices = albums[:20]
        picked_type, picked = self._musicbrainz_choose_two_column_match(track_choices, album_choices)
        if not picked:
            self.preview_status_label.setText("MusicBrainz search cancelled.")
            return
        if picked_type == "track":
            self._apply_musicbrainz_match_to_track(track_index, picked)
            return
        self._start_musicbrainz_album_tracklist_lookup(track_index, picked)

    def _start_musicbrainz_album_tracklist_lookup(self, track_index, album):
        if self._musicbrainz_tracklist_thread is not None:
            return
        thread = MusicBrainzReleaseTracklistThread(album, self)
        thread.tracklist_finished.connect(
            lambda payload, _idx=track_index, _thread=thread: self._on_musicbrainz_album_tracklist_finished(
                _idx,
                payload,
                _thread,
            )
        )
        self._musicbrainz_tracklist_thread = thread
        album_title = str((album or {}).get("title") or "selected album").strip()
        self._set_musicbrainz_busy(
            True,
            message=f"Loading MusicBrainz tracklist for \"{album_title}\"...",
            button_text="Loading...",
        )
        thread.start()

    def _on_musicbrainz_album_tracklist_finished(self, track_index, payload, thread=None):
        self._cleanup_musicbrainz_tracklist_thread(thread)
        self._set_musicbrainz_busy(False)

        payload = dict(payload or {})
        error_text = str(payload.get("error") or "").strip()
        album = dict(payload.get("album") or {})
        if error_text:
            QMessageBox.warning(self, "MusicBrainz", f"Tracklist lookup failed: {error_text}")
            self.preview_status_label.setText("MusicBrainz tracklist lookup failed.")
            return

        picked = self._musicbrainz_pick_from_loaded_album(
            album,
            payload.get("tracklist_data") or {},
        )
        if not picked:
            self.preview_status_label.setText("MusicBrainz album selection cancelled.")
            return
        self._apply_musicbrainz_match_to_track(track_index, picked)

    def _start_musicbrainz_enhanced_metadata_lookup(self, track_index, track, artist, title, recording_id=None):
        if self._musicbrainz_enhanced_thread is not None:
            return False
        editor_module = mixsplitr_editor or _lazy_import_backend("mixsplitr_editor")
        if editor_module is None or not hasattr(editor_module, "get_enhanced_metadata"):
            return False

        thread = MusicBrainzEnhancedMetadataThread(artist, title, recording_id=recording_id, parent=self)
        thread.metadata_finished.connect(
            lambda payload, _idx=track_index, _track=track, _artist=artist, _title=title, _thread=thread: self._on_musicbrainz_enhanced_metadata_finished(
                _idx,
                _track,
                _artist,
                _title,
                payload,
                _thread,
            )
        )
        self._musicbrainz_enhanced_thread = thread
        self._set_musicbrainz_busy(
            True,
            message=f"Loading MusicBrainz metadata for \"{artist} - {title}\"...",
            button_text="Loading...",
        )
        thread.start()
        return True

    def _on_musicbrainz_enhanced_metadata_finished(self, track_index, track, artist, title, payload, thread=None):
        self._cleanup_musicbrainz_enhanced_thread(thread)
        self._set_musicbrainz_busy(False)

        if track_index < 0 or track_index >= len(self.tracks):
            return
        if self.tracks[track_index] is not track:
            return
        if str(track.get("artist") or "").strip() != str(artist or "").strip():
            return
        if str(track.get("title") or "").strip() != str(title or "").strip():
            return

        enhanced = (payload or {}).get("enhanced") or {}
        if isinstance(enhanced, dict) and enhanced:
            self._apply_enhanced_metadata(track, enhanced)
            self._refresh_track_list()
            self._select_track_by_index(track_index)

        self.preview_status_label.setText(f"Matched: {artist} - {title}")

    def _apply_musicbrainz_match_to_track(self, track_index, picked):
        if track_index < 0 or track_index >= len(self.tracks):
            return
        track = self.tracks[track_index]
        if not isinstance(track, dict):
            return

        artist = str(picked.get("artist") or "").strip()
        title = str(picked.get("title") or "").strip()
        if not artist or not title:
            QMessageBox.warning(self, "MusicBrainz", "Selected result is missing artist/title.")
            return

        self._clear_pending_track_edit(track_index)
        track["status"] = "identified"
        track.pop("reason", None)
        track["artist"] = artist
        track["title"] = title
        track["album"] = str(picked.get("album") or track.get("album") or "Unknown Album")
        track.setdefault("enhanced_metadata", {})

        recording_id = picked.get("recording_id")
        if hasattr(mixsplitr_editor, "get_enhanced_metadata"):
            try:
                enhanced = mixsplitr_editor.get_enhanced_metadata(artist, title, recording_id=recording_id) or {}
                self._apply_enhanced_metadata(track, enhanced)
            except Exception:
                pass

        if not (track.get("enhanced_metadata") or {}).get("bpm") and track.get("detected_bpm"):
            try:
                track["enhanced_metadata"]["bpm"] = int(track.get("detected_bpm"))
            except Exception:
                pass

        track["expected_filename"] = self._safe_filename(artist, title)
        metadata_lookup_started = self._start_musicbrainz_enhanced_metadata_lookup(
            track_index,
            track,
            artist,
            title,
            recording_id=recording_id,
        )
        self._sync_current_form_to_track(track_index)
        self._mark_changed()
        self._refresh_track_list()
        self._select_track_by_index(track_index)
        if not metadata_lookup_started:
            self.preview_status_label.setText(f"Matched: {artist} - {title}")

    def _musicbrainz_match_selected_track(self):
        idx, track = self._current_track()
        if track is None:
            return
        if self._musicbrainz_busy:
            return
        if not self._musicbrainz_available():
            QMessageBox.warning(self, "MusicBrainz", "MusicBrainz integration is unavailable in this environment.")
            return
        if str(track.get("status") or "") == "skipped":
            QMessageBox.information(self, "Track Skipped", "Restore this track before searching.")
            return

        unidentified_name = str(track.get("unidentified_filename") or "").strip()
        if unidentified_name:
            unidentified_name = os.path.splitext(unidentified_name)[0]
        default_query = unidentified_name.replace("_", " ").replace("-", " ").strip()
        if not default_query:
            default_query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
        query, ok = QInputDialog.getText(self, "MusicBrainz Search", "Search query:", text=default_query)
        if not ok:
            return
        query = str(query or "").strip()
        if not query:
            return
        self._start_musicbrainz_combined_search(idx, query)

    def _resolve_preview_path(self, track):
        for key in ("temp_chunk_path", "temp_flac", "unidentified_path", "original_file"):
            candidate = str(track.get(key) or "").strip()
            if candidate and os.path.exists(candidate):
                return candidate
        return ""

    def _set_preview_button_state(self, mode="idle"):
        mode_name = str(mode or "idle").strip().lower()
        if mode_name == "playing":
            self.preview_btn.setText("Stop Preview")
            self.preview_btn.setEnabled(True)
        elif mode_name == "stopping":
            self.preview_btn.setText("Stopping...")
            self.preview_btn.setEnabled(False)
        else:
            self.preview_btn.setText("Preview Segment" if self._is_timestamp_editor_mode() else "Preview Audio")
            self.preview_btn.setEnabled(True)

    def _preview_selected_track_audio(self):
        if self.preview_thread and self.preview_thread.isRunning():
            try:
                self.preview_thread.stop_playback()
            except Exception:
                pass
            self.preview_status_label.setText("Stopping preview playback...")
            self._set_preview_button_state("stopping")
            return

        _idx, track = self._current_track()
        if track is None:
            return
        preview_path = self._resolve_preview_path(track)
        if not preview_path:
            QMessageBox.information(
                self,
                "Preview Audio",
                "No preview audio is available for this item."
                if self._is_timestamp_editor_mode()
                else "No preview audio is available for this track. Use Full Preview mode to cache chunks.",
            )
            return

        self.preview_thread = AudioPreviewThread(preview_path, duration_seconds=30)
        self.preview_thread.status_update.connect(self.preview_status_label.setText)
        self.preview_thread.finished_update.connect(self._on_preview_audio_finished)
        self.preview_status_label.setText(f"Preview: {os.path.basename(preview_path)}")
        self._set_preview_button_state("playing")
        self.preview_thread.start()

    def _on_preview_audio_finished(self, success, message):
        if success:
            self.preview_status_label.setText(message)
        else:
            self.preview_status_label.setText(f"Preview error: {message}")
        self._set_preview_button_state("idle")
        thread = self.preview_thread
        self.preview_thread = None
        if thread is not None:
            try:
                thread.deleteLater()
            except Exception:
                pass

    def _toggle_delete_selected_track(self):
        idx, track = self._current_track()
        if track is None:
            return

        status = str(track.get("status") or "")
        reason = str(track.get("reason") or "")
        if status == "skipped" and reason == "user_deleted":
            self._clear_pending_track_edit(idx)
            if track.get("artist") and track.get("title"):
                track["status"] = "identified"
            else:
                track["status"] = "unidentified"
            track.pop("reason", None)
            self._sync_current_form_to_track(idx)
            self._mark_changed()
            self._refresh_track_list()
            self._select_track_by_index(idx)
            self.preview_status_label.setText("Track restored")
            return

        confirm = QMessageBox.question(
            self,
            "Delete Track",
            "Exclude this track from export?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        self._clear_pending_track_edit(idx)
        track["status"] = "skipped"
        track["reason"] = "user_deleted"
        self._sync_current_form_to_track(idx)
        self._mark_changed()
        self._refresh_track_list()
        self._select_track_by_index(idx)
        self.preview_status_label.setText("Track marked as skipped")

    def _import_tracklist(self):
        if not mixsplitr_editor or not hasattr(mixsplitr_editor, "parse_tracklist"):
            QMessageBox.warning(self, "Import Tracklist", "Tracklist parser unavailable.")
            return

        text, ok = QInputDialog.getMultiLineText(
            self,
            "Import Tracklist",
            "Paste tracklist or cue sheet:",
            "",
        )
        if not ok:
            return
        text = str(text or "").strip()
        if not text:
            return

        try:
            tracklist = mixsplitr_editor.parse_tracklist(text)
        except Exception as exc:
            QMessageBox.warning(self, "Import Tracklist", f"Tracklist parse failed: {exc}")
            return
        if not tracklist:
            QMessageBox.information(self, "Import Tracklist", "No track entries were parsed.")
            return

        if not hasattr(mixsplitr_editor, "match_tracklist_to_tracks"):
            QMessageBox.warning(self, "Import Tracklist", "Tracklist matching unavailable.")
            return
        try:
            matches = mixsplitr_editor.match_tracklist_to_tracks(tracklist, self.tracks)
        except Exception as exc:
            QMessageBox.warning(self, "Import Tracklist", f"Tracklist match failed: {exc}")
            return
        if not matches:
            QMessageBox.information(self, "Import Tracklist", "No matching tracks found.")
            return

        preview_text = ""
        if hasattr(mixsplitr_editor, "format_tracklist_preview"):
            try:
                preview_text = str(mixsplitr_editor.format_tracklist_preview(tracklist) or "")
            except Exception:
                preview_text = ""
        preview_lines = preview_text.splitlines()
        preview_block = "\n".join(preview_lines[:12]) if preview_lines else ""
        confirm = QMessageBox.question(
            self,
            "Apply Tracklist",
            f"Apply metadata to {len(matches)} matched track(s)?\n\n{preview_block}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        applied_count = 0
        for track_idx, entry in matches:
            if track_idx < 0 or track_idx >= len(self.tracks):
                continue
            track = self.tracks[track_idx]
            artist = str(entry.get("artist") or "").strip()
            title = str(entry.get("title") or "").strip()
            if not artist or not title:
                continue

            track["status"] = "identified"
            track.pop("reason", None)
            track["artist"] = artist
            track["title"] = title
            track["album"] = str(entry.get("album") or track.get("album") or "Unknown Album")
            track["expected_filename"] = self._safe_filename(artist, title)
            self._clear_pending_track_edit(track_idx)
            self._sync_current_form_to_track(track_idx)
            applied_count += 1

        if applied_count > 0:
            self._mark_changed()
            self._refresh_track_list()
            self.preview_status_label.setText(f"Tracklist imported: {applied_count} track(s) updated")
        else:
            self.preview_status_label.setText("Tracklist import made no changes")

    def _save_cache(self):
        payload = dict(self.cache_data)
        payload["tracks"] = self.tracks

        if self.editor_mode == "session":
            return self._save_session_to_manifest()

        if not self.cache_path:
            default_name = f"mixsplitr_track_editor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            default_path = os.path.join(os.path.expanduser("~"), default_name)
            selected_path, _filter = choose_save_file(
                self,
                "Save Track Editor Session",
                default_path,
                "JSON Files (*.json);;All Files (*)",
            )
            selected_path = str(selected_path or "").strip()
            if not selected_path:
                return False
            if not selected_path.lower().endswith(".json"):
                selected_path += ".json"
            self.cache_path = selected_path

        saved = False
        if mixsplitr_editor and hasattr(mixsplitr_editor, "save_preview_cache"):
            try:
                saved = bool(mixsplitr_editor.save_preview_cache(payload, self.cache_path))
            except Exception:
                saved = False
        if not saved:
            try:
                with open(self.cache_path, "w", encoding="utf-8") as cache_file:
                    json.dump(payload, cache_file)
                saved = True
            except Exception as exc:
                QMessageBox.warning(self, "Save Failed", str(exc))
                return False

        self.cache_data = payload
        self._baseline_tracks = copy.deepcopy(self.tracks)
        self.changes_made = False
        self._set_title()
        self.preview_status_label.setText("Session saved")
        return True

    def _save_session_to_manifest(self):
        if not self.session_manifest or not self.session_manifest_path:
            QMessageBox.warning(self, "Save Failed", "No session manifest is loaded for editing.")
            return False
        try:
            updated_cache = dict(self.cache_data)
            updated_cache["tracks"] = self.tracks
            mixsplitr_session._apply_session_editor_cache_to_manifest(
                self.session_manifest, updated_cache
            )
            mixsplitr_session._save_manifest_in_place(
                self.session_manifest, self.session_manifest_path
            )
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save session record: {exc}")
            return False
        self.cache_data["tracks"] = self.tracks
        self._baseline_tracks = copy.deepcopy(self.tracks)
        self.changes_made = False
        self._set_title()
        self.preview_status_label.setText("Session record saved")
        return True

    def _apply_pending_edits_for_save(self):
        if self.editor_mode not in ("preview", "timestamp_preview", "session"):
            return True
        if self._bulk_form_has_values() and not self._checked_track_indices():
            QMessageBox.warning(
                self,
                "Bulk Edit Pending",
                "Bulk edit fields have values, but no tracks are checked. Check tracks or clear the bulk fields before saving.",
            )
            return False
        if not self._pending_track_edits:
            if not self._bulk_form_has_values():
                return self._validate_timestamp_session_tracks()
        if not self._apply_form_edits(show_feedback=False):
            return False
        return self._validate_timestamp_session_tracks()

    def _save_only(self):
        if not self._apply_pending_edits_for_save():
            return False
        return self._save_cache()

    def _save_and_apply(self):
        if self.editor_mode not in ("preview", "timestamp_preview"):
            self.preview_status_label.setText("Save and Export is only available for preview sessions")
            return
        if not self._apply_pending_edits_for_save():
            return
        if self._is_timestamp_editor_mode() and not self._validate_timestamp_session_tracks(require_active=True):
            return
        if self.changes_made and not self._save_cache():
            return
        self.workflow_action.emit("export_timestamps" if self._is_timestamp_editor_mode() else "apply")

    def _save_and_close(self):
        if self.editor_mode not in ("preview", "timestamp_preview", "session"):
            self._save_only()
            return
        if not self._apply_pending_edits_for_save():
            return
        if self.changes_made and not self._save_cache():
            return
        self.workflow_action.emit("done")

    def _discard_and_close(self):
        if self.editor_mode == "standalone":
            if self.has_unsaved_changes():
                confirm = QMessageBox.question(
                    self,
                    "Reset Changes",
                    "Reset unsaved edits to the last loaded/saved state?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if confirm != QMessageBox.Yes:
                    return
            self.tracks = copy.deepcopy(self._baseline_tracks)
            self.filtered_indices = []
            self.changes_made = False
            self._pending_track_edits = {}
            self._bulk_checked_track_indices = set()
            self._displayed_track_index = -1
            self._clear_bulk_form()
            self._set_title()
            self._refresh_track_list()
            self.preview_status_label.setText("Edits reset")
            return

        if self.has_unsaved_changes():
            confirm = QMessageBox.question(
                self,
                "Discard Changes",
                "Discard unsaved changes?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        self.workflow_action.emit("quit")
        if self.editor_mode == "timestamp_preview":
            self._reset_to_empty_standalone_editor()

    def closeEvent(self, event):
        if self.preview_thread and self.preview_thread.isRunning():
            try:
                self.preview_thread.stop_playback()
                self.preview_thread.wait(1200)
            except Exception:
                pass
        super().closeEvent(event)


class DebugReadoutDialog(QDialog):
    unlock_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        annotate_widget(self, role="Debug Readout Dialog")
        self.setWindowTitle("Debug Readout")
        self.resize(920, 540)
        self._inspector_payload = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        inspector_panel = QWidget(self)
        annotate_widget(inspector_panel, role="Developer Inspector Panel")
        inspector_layout = QVBoxLayout(inspector_panel)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(10)

        inspector_title = QLabel("Live Inspector")
        inspector_title.setStyleSheet("color: #E9EAEC; font-weight: 600;")
        inspector_layout.addWidget(inspector_title)

        inspector_form = QFormLayout()
        inspector_form.setContentsMargins(0, 0, 0, 0)
        inspector_form.setSpacing(8)
        inspector_form.setHorizontalSpacing(12)
        self.inspector_status_value = self._create_inspector_value_field()
        self.inspector_element_value = self._create_inspector_value_field()
        self.inspector_class_value = self._create_inspector_value_field()
        self.inspector_object_value = self._create_inspector_value_field()
        self.inspector_context_value = self._create_inspector_value_field()
        self.inspector_module_value = self._create_inspector_value_field()
        self.inspector_function_value = self._create_inspector_value_field()
        self.inspector_source_value = self._create_inspector_value_field()
        self.inspector_ancestors_value = QTextEdit()
        self.inspector_ancestors_value.setReadOnly(True)
        self.inspector_ancestors_value.setMinimumHeight(72)
        self.inspector_ancestors_value.setMaximumHeight(110)
        self.inspector_ancestors_value.setLineWrapMode(QTextEdit.WidgetWidth)
        self.inspector_ancestors_value.setStyleSheet(self._inspector_field_qss())

        inspector_form.addRow("Status:", self.inspector_status_value)
        inspector_form.addRow("Element:", self.inspector_element_value)
        inspector_form.addRow("Widget Class:", self.inspector_class_value)
        inspector_form.addRow("Object Name:", self.inspector_object_value)
        inspector_form.addRow("Context:", self.inspector_context_value)
        inspector_form.addRow("Module:", self.inspector_module_value)
        inspector_form.addRow("Function:", self.inspector_function_value)
        inspector_form.addRow("Location:", self.inspector_source_value)
        inspector_form.addRow("Ancestors:", self.inspector_ancestors_value)
        inspector_layout.addLayout(inspector_form)

        inspector_button_row = QHBoxLayout()
        inspector_button_row.addStretch()
        self.copy_location_btn = QPushButton("Copy Location")
        self.unlock_btn = QPushButton("Unlock")
        self.copy_location_btn.clicked.connect(self._copy_location)
        self.unlock_btn.clicked.connect(self.unlock_requested.emit)
        inspector_button_row.addWidget(self.copy_location_btn)
        inspector_button_row.addWidget(self.unlock_btn)
        inspector_layout.addLayout(inspector_button_row)

        self.splitter.addWidget(inspector_panel)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QTextEdit.NoWrap)
        self.output.setStyleSheet(
            "QTextEdit {"
            "background-color: #121417;"
            "color: #E9EAEC;"
            "border: 1px solid #3A3D40;"
            "border-radius: 12px;"
            "font-family: Menlo, Consolas, 'Courier New', monospace;"
            "font-size: 12px;"
            "}"
        )
        self.splitter.addWidget(self.output)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 380])

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.copy_btn = QPushButton("Copy All")
        self.close_btn = QPushButton("Close")
        self.copy_btn.clicked.connect(self._copy_all)
        self.close_btn.clicked.connect(self.hide)
        button_row.addWidget(self.copy_btn)
        button_row.addWidget(self.close_btn)
        layout.addLayout(button_row)
        self.set_inspector_payload({})

    def _inspector_field_qss(self):
        return (
            "QLineEdit, QTextEdit {"
            "background-color: #121417;"
            "color: #E9EAEC;"
            "border: 1px solid #3A3D40;"
            "border-radius: 10px;"
            "font-family: Menlo, Consolas, 'Courier New', monospace;"
            "font-size: 12px;"
            "padding: 6px 8px;"
            "}"
        )

    def _create_inspector_value_field(self):
        field = QLineEdit()
        field.setReadOnly(True)
        field.setStyleSheet(self._inspector_field_qss())
        return field

    def set_inspector_payload(self, payload):
        data = dict(payload or {})
        self._inspector_payload = data
        self.inspector_status_value.setText(str(data.get("status") or "Live"))
        self.inspector_element_value.setText(str(data.get("element_label") or ""))
        self.inspector_class_value.setText(str(data.get("widget_class") or ""))
        self.inspector_object_value.setText(str(data.get("object_name") or ""))
        self.inspector_context_value.setText(str(data.get("page_context") or ""))
        self.inspector_module_value.setText(str(data.get("module") or ""))
        self.inspector_function_value.setText(str(data.get("function") or ""))
        source_path = str(data.get("source_path") or "")
        line_number = str(data.get("line_number") or "")
        location_text = f"{source_path}:{line_number}".rstrip(":")
        self.inspector_source_value.setText(location_text)
        self.inspector_ancestors_value.setPlainText(str(data.get("ancestor_summary") or ""))
        has_target = bool(data.get("has_target"))
        self.copy_location_btn.setEnabled(has_target)
        self.unlock_btn.setEnabled(str(data.get("status") or "").lower() == "locked")

    def load_lines(self, lines):
        text = "\n".join([str(line or "").rstrip("\r\n") for line in list(lines or []) if str(line or "").strip()])
        self.output.setPlainText(text)
        bar = self.output.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def append_line(self, text):
        line = str(text or "").rstrip("\r\n")
        if not line:
            return
        self.output.append(line)
        bar = self.output.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _copy_all(self):
        QApplication.clipboard().setText(self.output.toPlainText())

    def _copy_location(self):
        QApplication.clipboard().setText(format_inspector_location(self._inspector_payload))

    def closeEvent(self, event):
        self.hide()
        event.ignore()
