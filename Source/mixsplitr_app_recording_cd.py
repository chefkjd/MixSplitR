import audioop
import html
import importlib
import os
import queue
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import (
        QAudioInput,
        QAudioSource,
        QMediaCaptureSession,
        QMediaDevices,
        QMediaFormat,
        QMediaRecorder,
    )
    QT_MEDIA_AVAILABLE = True
except Exception:
    QAudioInput = None
    QAudioSource = None
    QMediaCaptureSession = None
    QMediaDevices = None
    QMediaFormat = None
    QMediaRecorder = None
    QT_MEDIA_AVAILABLE = False

from mixsplitr_ui_cards import GlassPage
from mixsplitr_ui_effects import FloatingOverlayScrollBar
from mixsplitr_ui_inputs import _ChevronComboBox, _NoScrollComboBox
from mixsplitr_ui_waveform import NativeWaveformEditorDialog, NativeWaveformLoadThread
from mixsplitr_devtools import annotate_widget

mixsplitr_core = None
mixsplitr_process_capture = None
mixsplitr_cdrip = None
_resolve_bundled_resource_path = None
VB_CABLE_DOWNLOAD_URL = ""
BLACKHOLE_DOWNLOAD_URL = ""
BLACKHOLE_BREW_INSTALL_ACTION_URL = ""
_MIN_SAVED_RECORDING_BYTES = 1024
_WINDOWS_ES_CONTINUOUS = 0x80000000
_WINDOWS_ES_SYSTEM_REQUIRED = 0x00000001
_WINDOWS_ES_DISPLAY_REQUIRED = 0x00000002


def _is_saved_recording_path(path, minimum_size_bytes=_MIN_SAVED_RECORDING_BYTES):
    normalized = os.path.abspath(str(path or ""))
    if not normalized or not os.path.isfile(normalized):
        return False
    try:
        return int(os.path.getsize(normalized)) > int(minimum_size_bytes)
    except Exception:
        return False


def _cleanup_invalid_recording_paths(paths, minimum_size_bytes=_MIN_SAVED_RECORDING_BYTES):
    removed = []
    for path in list(paths or []):
        normalized = os.path.abspath(str(path or ""))
        if not normalized or not os.path.isfile(normalized):
            continue
        try:
            size_bytes = int(os.path.getsize(normalized))
        except Exception:
            continue
        if size_bytes > int(minimum_size_bytes):
            continue
        try:
            os.remove(normalized)
            removed.append(normalized)
        except Exception:
            pass
    return removed


def _recording_keep_screen_awake_enabled(self):
    control = getattr(self, "recording_keep_screen_awake_check", None)
    if control is not None:
        try:
            return bool(control.isChecked())
        except Exception:
            pass
    if mixsplitr_core is not None and hasattr(mixsplitr_core, "get_config"):
        try:
            return bool((mixsplitr_core.get_config() or {}).get("recording_keep_screen_awake", False))
        except Exception:
            pass
    return False


def _start_recording_awake_prevention(self):
    if not self._recording_keep_screen_awake_enabled():
        self._stop_recording_awake_prevention()
        return False

    current_state = getattr(self, "_recording_awake_prevention_state", None)
    if isinstance(current_state, dict) and current_state.get("platform"):
        return True

    if sys.platform == "win32":
        try:
            import ctypes

            result = ctypes.windll.kernel32.SetThreadExecutionState(
                _WINDOWS_ES_CONTINUOUS
                | _WINDOWS_ES_SYSTEM_REQUIRED
                | _WINDOWS_ES_DISPLAY_REQUIRED
            )
            if result:
                self._recording_awake_prevention_state = {"platform": "win32"}
                return True
        except Exception:
            return False
        return False

    if sys.platform == "darwin":
        caffeinate_path = shutil.which("caffeinate")
        if not caffeinate_path:
            return False
        try:
            proc = subprocess.Popen(
                [caffeinate_path, "-d", "-i"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._recording_awake_prevention_state = {
                "platform": "darwin",
                "process": proc,
            }
            return True
        except Exception:
            return False

    return False


def _stop_recording_awake_prevention(self):
    state = getattr(self, "_recording_awake_prevention_state", None)
    self._recording_awake_prevention_state = None
    if not isinstance(state, dict):
        return

    platform_name = str(state.get("platform") or "").strip().lower()
    if platform_name == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(_WINDOWS_ES_CONTINUOUS)
        except Exception:
            pass
        return

    if platform_name == "darwin":
        proc = state.get("process")
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    proc.kill()
        except Exception:
            pass


def _sync_recording_awake_prevention(self):
    if bool(getattr(self, "recording_active", False)) and self._recording_keep_screen_awake_enabled():
        return self._start_recording_awake_prevention()
    self._stop_recording_awake_prevention()
    return False


def install_recording_cd_methods(
    app_cls,
    *,
    core_module,
    process_capture_module,
    cdrip_module,
    resolve_bundled_resource_path,
    vb_cable_download_url,
    blackhole_download_url,
    blackhole_brew_install_action_url,
):
    global mixsplitr_core, mixsplitr_process_capture, mixsplitr_cdrip
    global _resolve_bundled_resource_path
    global VB_CABLE_DOWNLOAD_URL, BLACKHOLE_DOWNLOAD_URL, BLACKHOLE_BREW_INSTALL_ACTION_URL

    mixsplitr_core = core_module
    mixsplitr_process_capture = process_capture_module
    mixsplitr_cdrip = cdrip_module
    _resolve_bundled_resource_path = resolve_bundled_resource_path
    VB_CABLE_DOWNLOAD_URL = str(vb_cable_download_url or "").strip()
    BLACKHOLE_DOWNLOAD_URL = str(blackhole_download_url or "").strip()
    BLACKHOLE_BREW_INSTALL_ACTION_URL = str(blackhole_brew_install_action_url or "").strip()

    methods = (
        create_recording_page,
        create_cd_ripping_page,
        _can_use_windows_loopback_backend,
        _can_use_windows_process_capture_backend,
        _is_windows_process_capture_selection,
        _is_windows_loopback_selection,
        _start_windows_loopback_recording,
        _start_windows_process_capture_recording,
        _set_recording_status,
        _set_signal_status,
        _recording_background_mode_enabled,
        _is_likely_virtual_audio_device_name,
        _virtual_audio_device_channel_mode,
        _virtual_audio_input_device_channel_mode,
        _recording_device_channel_count,
        _sync_recording_background_device_ui,
        _recording_background_monitor_enabled,
        _sync_recording_background_monitor_ui,
        _refresh_loopback_recording_status,
        _on_recording_background_monitor_toggled,
        _refresh_recording_windows_capture_guidance,
        _recording_blackhole_install_link_html,
        _refresh_recording_help_text,
        _handle_recording_help_link,
        _run_blackhole_homebrew_install,
        _open_windows_recording_settings,
        _recording_runtime_temp_dir,
        _launch_windows_audio_service_restart,
        _restart_windows_audio_services,
        _on_recording_capture_mode_toggled,
        _on_recording_input_selection_changed,
        _on_recording_background_device_toggled,
        _selected_recording_device,
        _stop_audio_level_meter,
        _restart_audio_level_meter,
        _start_audio_level_meter,
        _extract_level_from_audio_bytes,
        _poll_audio_level_meter,
        _recording_stop_after_seconds,
        _sync_recording_stop_timer_ui,
        _maybe_auto_stop_for_duration,
        _on_recording_stop_after_toggled,
        _on_recording_stop_after_value_changed,
        _format_elapsed,
        _recording_keep_screen_awake_enabled,
        _start_recording_awake_prevention,
        _stop_recording_awake_prevention,
        _sync_recording_awake_prevention,
        _recording_force_wav_enabled,
        _recording_output_directory,
        _is_recording_audio_file,
        _selected_recording_path,
        _current_recording_target_file,
        _build_trimmed_recording_output_path,
        _trim_recording_audio_file,
        _update_recording_action_buttons,
        _on_recordings_selection_changed,
        _refresh_recordings_list,
        _maybe_auto_stop_for_silence,
        _populate_recording_inputs,
        _cleanup_recording_objects,
        _update_recording_timer_label,
        _on_recorder_error,
        start_recording,
        _finalize_recording_stop,
        stop_recording,
        toggle_recording,
        preview_recording_file,
        trim_selected_recording,
        delete_selected_recording,
        send_recording_to_splitter,
        _populate_cd_drives,
        _cd_rip_set_status,
        _format_cd_rip_failure_line,
        _set_cd_rip_failures,
        _reset_cd_rip_timeout_state,
        _show_cd_rip_skip_prompt,
        _request_cd_rip_skip_current_track,
        _checked_cd_rip_output_paths,
        _update_cd_rip_send_buttons,
        _start_cd_rip,
        _poll_cd_rip_progress,
        _on_cd_rip_finished,
        _on_cd_ripped_selection_changed,
        _cd_rip_send_to_identifier,
        _cd_rip_send_to_editor,
    )
    for method in methods:
        setattr(app_cls, method.__name__, method)

def create_recording_page(self):
    page = GlassPage()
    annotate_widget(page, role="Recording Page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 28, 28, 28)
    layout.setSpacing(20)
    self._track_layout_metrics(layout, base_margins=(28, 28, 28, 28), base_spacing=20)

    header_container = QWidget()
    header_row = QHBoxLayout(header_container)
    if sys.platform == "win32":
        # Keep the page title visually lower in compact Windows layouts.
        header_row.setContentsMargins(0, 10, 0, 2)
    elif sys.platform == "darwin":
        header_row.setContentsMargins(0, 14, 0, 2)
    else:
        header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(0)
    header = QLabel("Recording Mode")
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

    record_card = self.create_base_card("Capture Audio", "Record a mix directly into MixSplitR.")
    rec_layout = QVBoxLayout()

    self.recording_capture_mode_group = None
    self.recording_capture_mode_standard_radio = None
    self.recording_capture_mode_background_radio = None
    self.recording_background_device_group = None
    self.recording_background_stereo_radio = None
    self.recording_background_16ch_radio = None
    self.recording_background_device_row = None
    self.recording_background_monitor_check = None
    self.recording_stop_after_check = None
    self.recording_stop_after_time_input = None
    self.recording_stop_after_hint = None
    self.recording_windows_background_hint = None
    self.recording_windows_background_status = None
    self.recording_open_sound_settings_btn = None
    self.recording_open_volume_mixer_btn = None
    self.recording_restart_audio_services_btn = None
    if sys.platform == "win32":
        capture_mode_row = QHBoxLayout()
        capture_mode_row.setContentsMargins(0, 0, 0, 0)
        capture_mode_row.setSpacing(16)
        capture_mode_label = QLabel("Capture Mode:")
        capture_mode_label.setStyleSheet("color: #C4C7C5;")
        capture_mode_row.addWidget(capture_mode_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self.recording_capture_mode_group = QButtonGroup(self)
        self.recording_capture_mode_group.setExclusive(True)
        self.recording_capture_mode_standard_radio = QRadioButton("Standard")
        self.recording_capture_mode_background_radio = QRadioButton("Background (Virtual Device)")
        for button in (
            self.recording_capture_mode_standard_radio,
            self.recording_capture_mode_background_radio,
        ):
            button.setProperty("cardLineSurface", True)
            self._refresh_widget_style(button)
            capture_mode_row.addWidget(button, 0, Qt.AlignLeft | Qt.AlignVCenter)
            self.recording_capture_mode_group.addButton(button)
        self.recording_capture_mode_standard_radio.setChecked(True)
        capture_mode_row.addStretch(1)
        rec_layout.addLayout(capture_mode_row)

    self.recording_input_select = _ChevronComboBox()
    self._apply_clickable_combo_style(self.recording_input_select)
    rec_layout.addWidget(self.recording_input_select)

    if sys.platform == "win32":
        self.recording_background_device_row = QWidget()
        background_device_layout = QHBoxLayout(self.recording_background_device_row)
        background_device_layout.setContentsMargins(0, 0, 0, 0)
        background_device_layout.setSpacing(16)
        background_device_label = QLabel("Cable Type:")
        background_device_label.setStyleSheet("color: #C4C7C5;")
        background_device_layout.addWidget(background_device_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self.recording_background_device_group = QButtonGroup(self)
        self.recording_background_device_group.setExclusive(True)
        self.recording_background_stereo_radio = QRadioButton("Stereo")
        for button in (
            self.recording_background_stereo_radio,
        ):
            button.setProperty("cardLineSurface", True)
            self._refresh_widget_style(button)
            background_device_layout.addWidget(button, 0, Qt.AlignLeft | Qt.AlignVCenter)
            self.recording_background_device_group.addButton(button)
        background_device_layout.addStretch(1)
        self.recording_background_device_row.hide()
        rec_layout.addWidget(self.recording_background_device_row)

        self.recording_background_monitor_check = QCheckBox("Hear Through Speakers While Recording")
        self.recording_background_monitor_check.setProperty("cardLineSurface", True)
        self._refresh_widget_style(self.recording_background_monitor_check)
        self.recording_background_monitor_check.hide()
        rec_layout.addWidget(self.recording_background_monitor_check)

    recording_source_actions = QHBoxLayout()
    recording_source_actions.setContentsMargins(0, 0, 0, 0)
    recording_source_actions.setSpacing(10)
    self.recording_refresh_sources_btn = QPushButton("Refresh Sources")
    self.recording_refresh_sources_btn.setProperty("class", "SecondaryButton")
    self.recording_refresh_sources_btn.setProperty("historyActionCell", True)
    self.recording_refresh_sources_btn.setCursor(Qt.PointingHandCursor)
    self._apply_history_action_cell_style(self.recording_refresh_sources_btn)
    recording_source_actions.addWidget(self.recording_refresh_sources_btn, 0, Qt.AlignLeft)
    if sys.platform == "win32":
        self.recording_open_sound_settings_btn = QPushButton("Open Sound Settings")
        self.recording_open_sound_settings_btn.setProperty("class", "SecondaryButton")
        self.recording_open_sound_settings_btn.setProperty("historyActionCell", True)
        self.recording_open_sound_settings_btn.setCursor(Qt.PointingHandCursor)
        self._apply_history_action_cell_style(self.recording_open_sound_settings_btn)
        recording_source_actions.addWidget(self.recording_open_sound_settings_btn, 0, Qt.AlignLeft)

        self.recording_open_volume_mixer_btn = QPushButton("Open Volume Mixer")
        self.recording_open_volume_mixer_btn.setProperty("class", "SecondaryButton")
        self.recording_open_volume_mixer_btn.setProperty("historyActionCell", True)
        self.recording_open_volume_mixer_btn.setCursor(Qt.PointingHandCursor)
        self._apply_history_action_cell_style(self.recording_open_volume_mixer_btn)
        recording_source_actions.addWidget(self.recording_open_volume_mixer_btn, 0, Qt.AlignLeft)

        self.recording_restart_audio_services_btn = QPushButton("Restart Audio Services")
        self.recording_restart_audio_services_btn.setProperty("class", "SecondaryButton")
        self.recording_restart_audio_services_btn.setProperty("historyActionCell", True)
        self.recording_restart_audio_services_btn.setCursor(Qt.PointingHandCursor)
        self.recording_restart_audio_services_btn.setToolTip(
            "Restarts Windows Audio with an admin prompt. Use this if Windows audio goes silent \n"
            "after multiple output-device changes. (Its a bug with windows itself.)"
        )
        self._apply_history_action_cell_style(self.recording_restart_audio_services_btn)
        recording_source_actions.addWidget(self.recording_restart_audio_services_btn, 0, Qt.AlignLeft)
    recording_source_actions.addStretch(1)
    rec_layout.addLayout(recording_source_actions)

    self.recording_windows_app_capture_hint = None
    if sys.platform == "win32":
        self.recording_windows_app_capture_hint = QLabel(
            "To record all system audio, select your current output device with WASAPI. "
            "To record just one app, start audio in that app first, then click Refresh Sources to filter apps with active audio signal. "
            "Select your .exe and start recording."

        )
        self.recording_windows_app_capture_hint.setWordWrap(True)
        self.recording_windows_app_capture_hint.setStyleSheet(
            "color: #C4C7C5; font-size: 12px; margin: 2px 0 4px 0;"
        )
        rec_layout.addWidget(self.recording_windows_app_capture_hint)

        self.recording_windows_background_hint = QLabel(
            "Background Capture is for recording an app quietly in the background.<br>"
            "1. Choose Stereo here to match the cable device you want to use.<br>"
            "2. Click Open Volume Mixer.<br>"
            "3. In your app's output dropdown, send the app to that same cable device.<br>"
            "4. Turn on Hear Through Speakers While Recording if you still want to listen.<br>"
            "5. Come back here and start recording."
        )
        self.recording_windows_background_hint.setTextFormat(Qt.RichText)
        self.recording_windows_background_hint.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.recording_windows_background_hint.setOpenExternalLinks(True)
        self.recording_windows_background_hint.setWordWrap(True)
        self.recording_windows_background_hint.setStyleSheet(
            "color: #C4C7C5; font-size: 12px; margin: 2px 0 0 0;"
        )
        self.recording_windows_background_hint.hide()
        rec_layout.addWidget(self.recording_windows_background_hint)

        self.recording_windows_background_status = QLabel("")
        self.recording_windows_background_status.setTextFormat(Qt.RichText)
        self.recording_windows_background_status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.recording_windows_background_status.setOpenExternalLinks(True)
        self.recording_windows_background_status.setWordWrap(True)
        self.recording_windows_background_status.setStyleSheet(
            "color: #C4C7C5; font-size: 12px; margin: 0 0 4px 0;"
        )
        self.recording_windows_background_status.hide()
        rec_layout.addWidget(self.recording_windows_background_status)

    self.blackhole_hint = QLabel(
        f'⚠ BlackHole not detected. Install <b>BlackHole 2ch</b> to record system audio on macOS. '
        f'<a href="{BLACKHOLE_DOWNLOAD_URL}" style="color:#E04040;">Download BlackHole</a>'
    )
    self.blackhole_hint.setTextFormat(Qt.RichText)
    self.blackhole_hint.setTextInteractionFlags(Qt.TextBrowserInteraction)
    self.blackhole_hint.setOpenExternalLinks(False)
    self.blackhole_hint.linkActivated.connect(self._handle_recording_help_link)
    self.blackhole_hint.setWordWrap(True)
    self.blackhole_hint.setStyleSheet("color: #E8A848; font-size: 12px; margin: 4px 0;")
    self.blackhole_hint.hide()
    rec_layout.addWidget(self.blackhole_hint)

    self.recording_timer_label = QLabel("00:00:00")
    self.recording_timer_label.setAlignment(Qt.AlignCenter)
    self._track_scalable(
        self.recording_timer_label,
        48,
        (
            "font-weight: 400; color: #EBE7E1; "
            "background-color: #22262B; "
            "border: none; "
            "border-radius: 10px; "
            "padding: 12px 24px; "
            "margin: 18px 0;"
        ),
    )
    self.recording_timer_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    rec_layout.addWidget(self.recording_timer_label, 0, Qt.AlignCenter)

    stop_after_cell = QWidget()
    stop_after_cell.setObjectName("RecordingStopAfterCell")
    stop_after_cell.setStyleSheet(
        "QWidget#RecordingStopAfterCell {"
        "  background-color: #22262B;"
        "  border: none;"
        "  border-radius: 10px;"
        "}"
    )
    stop_after_cell.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    stop_after_layout = QVBoxLayout(stop_after_cell)
    stop_after_layout.setContentsMargins(12, 2, 12, 10)
    stop_after_layout.setSpacing(0)
    stop_after_row = QHBoxLayout()
    stop_after_row.setContentsMargins(0, 0, 0, 0)
    stop_after_row.setSpacing(8)
    self.recording_stop_after_check = QCheckBox("")
    self.recording_stop_after_check.setObjectName("RecordingStopAfterCheck")
    self.recording_stop_after_check.setStyleSheet(
        "QCheckBox#RecordingStopAfterCheck {"
        "  background: transparent;"
        "  border: none;"
        "  padding: 0px;"
        "  margin: 0px;"
        "}"
    )
    self.recording_stop_after_check.setFixedWidth(22)
    stop_after_row.addWidget(self.recording_stop_after_check, 0, Qt.AlignLeft | Qt.AlignVCenter)
    recording_stop_after_label = QLabel("Stop automatically after:")
    recording_stop_after_label.setStyleSheet("color: #EBE7E1;")
    stop_after_row.addWidget(recording_stop_after_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
    stop_after_row.addStretch(1)
    stop_after_layout.addLayout(stop_after_row)

    stop_after_time_widget = QWidget()
    stop_after_time_widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    stop_after_time_row = QHBoxLayout(stop_after_time_widget)
    stop_after_time_row.setContentsMargins(56, 0, 0, 0)
    stop_after_time_row.setSpacing(0)
    self.recording_stop_after_time_input = QLineEdit("00:30:00")
    self.recording_stop_after_time_input.setObjectName("RecordingStopAfterTimeInput")
    self.recording_stop_after_time_input.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    self.recording_stop_after_time_input.setInputMask("99:99:99;0")
    self.recording_stop_after_time_input.setFixedWidth(94)
    self.recording_stop_after_time_input.setFixedHeight(28)
    self.recording_stop_after_time_input.setProperty("settingsValueCapsule", "true")
    self._refresh_widget_style(self.recording_stop_after_time_input)
    self.recording_stop_after_time_input.setStyleSheet(
        "QLineEdit#RecordingStopAfterTimeInput {"
        "  background-color: #2A2F36;"
        "  border: 1px solid #343A43;"
        "  border-radius: 8px;"
        "  color: #EBE7E1;"
        "  padding: 0px 8px;"
        "  min-height: 0px;"
        "}"
        "QLineEdit#RecordingStopAfterTimeInput:focus {"
        "  border: 1px solid #4A90FF;"
        "}"
        "QLineEdit#RecordingStopAfterTimeInput:disabled {"
        "  background-color: #252A31;"
        "  border: 1px solid #2E343C;"
        "  color: #7F8896;"
        "}"
    )
    stop_after_time_row.addWidget(self.recording_stop_after_time_input, 0, Qt.AlignLeft | Qt.AlignVCenter)
    stop_after_layout.addWidget(stop_after_time_widget, 0, Qt.AlignLeft)
    rec_layout.addWidget(stop_after_cell, 0, Qt.AlignLeft)

    self.recording_stop_after_hint = QLabel(
        "Optional elapsed-time limit. Recording stops automatically when this timer is reached."
    )
    self.recording_stop_after_hint.setWordWrap(True)
    self.recording_stop_after_hint.setStyleSheet(
        "color: #8E8E93; font-size: 12px; margin: 0 0 10px 0;"
    )
    rec_layout.addWidget(self.recording_stop_after_hint)

    self.record_btn = QPushButton("Start Recording")
    self.record_btn.setStyleSheet(
        "QPushButton {"
        "  background-color: #22262B;"
        "  color: #EBE7E1;"
        "  font-weight: 500;"
        "  font-size: 14px;"
        "  border-radius: 8px;"
        "  padding: 10px 18px;"
        "  border: none;"
        "}"
        "QPushButton:hover { background-color: #292E34; }"
    )
    self.record_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    rec_layout.addWidget(self.record_btn, 0, Qt.AlignCenter)

    self.recording_signal_label = QLabel("Signal: waiting for input")
    self.recording_signal_label.setStyleSheet("color: #C4C7C5;")
    rec_layout.addWidget(self.recording_signal_label)

    self.recording_signal_bar = QProgressBar()
    self.recording_signal_bar.setRange(0, 100)
    self.recording_signal_bar.setValue(0)
    self.recording_signal_bar.setTextVisible(False)
    self.recording_signal_bar.setFixedHeight(8)
    rec_layout.addWidget(self.recording_signal_bar)

    self.recording_status_label = QLabel("Status: Ready")
    self.recording_status_label.setStyleSheet("color: #C4C7C5;")
    rec_layout.addWidget(self.recording_status_label)

    record_card.layout().addLayout(rec_layout)
    layout.addWidget(record_card)

    recordings_card = self.create_base_card(
        "Recorded Files",
        "Select a previous recording and send it directly to the Audio Splitter tab."
    )
    recordings_layout = QVBoxLayout()
    self.recordings_list = QListWidget()
    self.recordings_list.setMinimumHeight(180)
    self._track_min_height(self.recordings_list, 180)
    self.recordings_list.setUniformItemSizes(True)
    self.recordings_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    self.recordings_list.setVerticalScrollBar(FloatingOverlayScrollBar(Qt.Vertical, self.recordings_list, always_visible=True))
    self.recordings_list.verticalScrollBar().setSingleStep(20)
    self.recordings_list.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#34373C")
    recordings_layout.addWidget(self.recordings_list)
    self.recordings_empty_label = QLabel("No recordings found in the selected recording folder.")
    self.recordings_empty_label.setStyleSheet("color: #C4C7C5;")
    recordings_layout.addWidget(self.recordings_empty_label)
    recordings_card.layout().addLayout(recordings_layout)
    layout.addWidget(recordings_card)
    layout.addStretch()

    action_layout = QHBoxLayout()
    action_layout.addStretch()
    self.preview_recording_btn = QPushButton("Preview Recording")
    self.preview_recording_btn.setProperty("class", "SecondaryButton")
    self.preview_recording_btn.setProperty("historyActionCell", True)
    self.preview_recording_btn.setCursor(Qt.PointingHandCursor)
    self._apply_history_action_cell_style(self.preview_recording_btn)
    self.preview_recording_btn.setEnabled(False)
    action_layout.addWidget(self.preview_recording_btn)
    self.trim_recording_btn = QPushButton("Trim Selected")
    self.trim_recording_btn.setProperty("class", "SecondaryButton")
    self.trim_recording_btn.setProperty("historyActionCell", True)
    self.trim_recording_btn.setCursor(Qt.PointingHandCursor)
    self._apply_history_action_cell_style(self.trim_recording_btn)
    self.trim_recording_btn.setEnabled(False)
    action_layout.addWidget(self.trim_recording_btn)
    self.delete_recording_btn = QPushButton("Delete Selected")
    self.delete_recording_btn.setProperty("class", "SecondaryButton")
    self.delete_recording_btn.setProperty("historyActionCell", True)
    self.delete_recording_btn.setCursor(Qt.PointingHandCursor)
    self._apply_history_action_cell_style(self.delete_recording_btn)
    self.delete_recording_btn.setEnabled(False)
    action_layout.addWidget(self.delete_recording_btn)
    self.send_to_splitter_btn = QPushButton("Send to Splitter ➔")
    self.send_to_splitter_btn.setProperty("class", "ActionButton")
    self.send_to_splitter_btn.setProperty("startProcessCell", True)
    self.send_to_splitter_btn.setProperty("busyState", True)
    self.send_to_splitter_btn.setObjectName("SendToSplitterButton")
    self.send_to_splitter_btn.setCursor(Qt.PointingHandCursor)
    self.send_to_splitter_btn.setEnabled(False)
    self._set_rounded_action_button_style(self.send_to_splitter_btn, busy=True)
    action_layout.addWidget(self.send_to_splitter_btn)
    layout.addLayout(action_layout)

    self._apply_card_line_surfaces_on_page(page)
    return self._wrap_page_in_transparent_scroll(page)

def create_cd_ripping_page(self):
    page = GlassPage()
    annotate_widget(page, role="CD Ripping Page")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 28, 28, 28)
    layout.setSpacing(20)
    self._track_layout_metrics(layout, base_margins=(28, 28, 28, 28), base_spacing=20)

    # Header
    header_container = QWidget()
    header_row = QHBoxLayout(header_container)
    if sys.platform == "win32":
        header_row.setContentsMargins(0, 10, 0, 2)
    elif sys.platform == "darwin":
        header_row.setContentsMargins(0, 14, 0, 2)
    else:
        header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(0)
    header = QLabel("CD Ripping")
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

    # Card 1: Disc Source
    source_card = self.create_base_card(
        "Disc Source",
        "Select a detected CD drive or enter a path manually."
    )
    source_layout = QFormLayout()
    self._configure_settings_form_layout(source_layout)

    self.cd_drive_combo = _NoScrollComboBox()
    source_layout.addRow("Drive / Source:", self.cd_drive_combo)

    self.cd_manual_path_input = QLineEdit()
    self.cd_manual_path_input.setPlaceholderText("Optional: override with a custom path or drive letter")
    source_layout.addRow("Manual Path:", self.cd_manual_path_input)

    self._apply_settings_form_bubbles(source_layout)
    source_card.layout().addLayout(source_layout)

    source_actions = QHBoxLayout()
    source_actions.setContentsMargins(0, 0, 0, 0)
    source_actions.setSpacing(10)

    self.cd_rip_refresh_btn = QPushButton("⟳  Detect Drives")
    self.cd_rip_refresh_btn.setProperty("class", "SecondaryButton")
    self.cd_rip_refresh_btn.setProperty("historyActionCell", True)
    self.cd_rip_refresh_btn.setCursor(Qt.PointingHandCursor)
    self._apply_history_action_cell_style(self.cd_rip_refresh_btn)
    source_actions.addWidget(self.cd_rip_refresh_btn, 0, Qt.AlignLeft)

    self.cd_rip_browse_output_btn = QPushButton("Open Output Folder")
    self.cd_rip_browse_output_btn.setProperty("class", "SecondaryButton")
    self.cd_rip_browse_output_btn.setProperty("historyActionCell", True)
    self.cd_rip_browse_output_btn.setCursor(Qt.PointingHandCursor)
    self.cd_rip_browse_output_btn.setToolTip("Open the configured CD rip output folder in Finder or File Explorer")
    self._apply_history_action_cell_style(self.cd_rip_browse_output_btn)
    source_actions.addWidget(self.cd_rip_browse_output_btn, 0, Qt.AlignLeft)

    source_actions.addStretch(1)
    source_card.layout().addLayout(source_actions)
    layout.addWidget(source_card)

    # Card 2: Rip Progress
    progress_card = self.create_base_card(
        "Rip Progress",
        "Live progress as each CD track is converted. Output settings are configured in the Settings tab."
    )
    prog_layout = QVBoxLayout()

    self.cd_rip_status_label = QLabel("Status: Ready")
    self.cd_rip_status_label.setStyleSheet("color: #C4C7C5;")
    prog_layout.addWidget(self.cd_rip_status_label)

    self.cd_rip_track_label = QLabel("")
    self.cd_rip_track_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    self.cd_rip_track_label.hide()
    prog_layout.addWidget(self.cd_rip_track_label)

    self.cd_rip_progress_bar = QProgressBar()
    self.cd_rip_progress_bar.setRange(0, 100)
    self.cd_rip_progress_bar.setValue(0)
    self.cd_rip_progress_bar.setTextVisible(True)
    self.cd_rip_progress_bar.setFixedHeight(12)
    self.cd_rip_progress_bar.hide()
    prog_layout.addWidget(self.cd_rip_progress_bar)

    self.cd_rip_skip_actions_widget = QWidget()
    self.cd_rip_skip_actions_layout = QHBoxLayout(self.cd_rip_skip_actions_widget)
    self.cd_rip_skip_actions_layout.setContentsMargins(0, 0, 0, 0)
    self.cd_rip_skip_actions_layout.setSpacing(10)

    self.cd_rip_skip_track_btn = QPushButton("Skip Stuck Track")
    self.cd_rip_skip_track_btn.setProperty("class", "SecondaryButton")
    self.cd_rip_skip_track_btn.setProperty("historyActionCell", True)
    self.cd_rip_skip_track_btn.setCursor(Qt.PointingHandCursor)
    self.cd_rip_skip_track_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    self._apply_history_action_cell_style(self.cd_rip_skip_track_btn)
    self.cd_rip_skip_actions_layout.addWidget(self.cd_rip_skip_track_btn)

    self.cd_rip_skip_and_auto_btn = QPushButton("Skip + Auto-Skip Future Stuck Tracks")
    self.cd_rip_skip_and_auto_btn.setProperty("class", "SecondaryButton")
    self.cd_rip_skip_and_auto_btn.setProperty("historyActionCell", True)
    self.cd_rip_skip_and_auto_btn.setCursor(Qt.PointingHandCursor)
    self.cd_rip_skip_and_auto_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    self._apply_history_action_cell_style(self.cd_rip_skip_and_auto_btn)
    self.cd_rip_skip_actions_layout.addWidget(self.cd_rip_skip_and_auto_btn)

    self.cd_rip_skip_actions_widget.hide()
    prog_layout.addWidget(self.cd_rip_skip_actions_widget)

    progress_card.layout().addLayout(prog_layout)
    layout.addWidget(progress_card)

    # Card 3: Ripped Files
    files_card = self.create_base_card(
        "Ripped Files",
        "Tracks ripped in this session. Use the checkboxes to choose what gets sent to Identifier or Editor."
    )
    files_layout = QVBoxLayout()

    self.cd_ripped_list = QListWidget()
    self.cd_ripped_list.setMinimumHeight(160)
    self._track_min_height(self.cd_ripped_list, 160)
    self.cd_ripped_list.setUniformItemSizes(True)
    self.cd_ripped_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    self.cd_ripped_list.setVerticalScrollBar(FloatingOverlayScrollBar(Qt.Vertical, self.cd_ripped_list, always_visible=True))
    self.cd_ripped_list.verticalScrollBar().setSingleStep(20)
    self.cd_ripped_list.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#34373C")
    self.cd_ripped_list.setSelectionMode(QAbstractItemView.NoSelection)
    files_layout.addWidget(self.cd_ripped_list)

    self.cd_rip_empty_label = QLabel("No tracks ripped yet in this session.")
    self.cd_rip_empty_label.setStyleSheet("color: #C4C7C5;")
    files_layout.addWidget(self.cd_rip_empty_label)

    self.cd_rip_failed_label = QLabel("Failed Tracks")
    self.cd_rip_failed_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
    self.cd_rip_failed_label.hide()
    files_layout.addWidget(self.cd_rip_failed_label)

    self.cd_rip_failed_output = QTextEdit()
    self.cd_rip_failed_output.setReadOnly(True)
    self.cd_rip_failed_output.setMinimumHeight(110)
    self._track_min_height(self.cd_rip_failed_output, 110)
    self.cd_rip_failed_output.hide()
    files_layout.addWidget(self.cd_rip_failed_output)

    files_card.layout().addLayout(files_layout)
    layout.addWidget(files_card)

    layout.addStretch()

    # Action row
    action_layout = QHBoxLayout()
    action_layout.addStretch()
    self.cd_rip_start_btn = QPushButton("Start Ripping")
    self.cd_rip_start_btn.setProperty("class", "ActionButton")
    self.cd_rip_start_btn.setProperty("startProcessCell", True)
    self.cd_rip_start_btn.setProperty("busyState", False)
    self.cd_rip_start_btn.setObjectName("CdRipStartButton")
    self.cd_rip_start_btn.setCursor(Qt.PointingHandCursor)
    self._set_cd_rip_start_button_style(busy=False)
    action_layout.addWidget(self.cd_rip_start_btn)

    self.cd_rip_send_to_identifier_btn = QPushButton("Send to Identifier")
    self.cd_rip_send_to_identifier_btn.setProperty("class", "ActionButton")
    self.cd_rip_send_to_identifier_btn.setProperty("startProcessCell", True)
    self.cd_rip_send_to_identifier_btn.setProperty("busyState", True)
    self.cd_rip_send_to_identifier_btn.setObjectName("CdSendToIdentifierButton")
    self.cd_rip_send_to_identifier_btn.setCursor(Qt.PointingHandCursor)
    self.cd_rip_send_to_identifier_btn.setEnabled(False)
    self._set_rounded_action_button_style(self.cd_rip_send_to_identifier_btn, busy=True)
    action_layout.addWidget(self.cd_rip_send_to_identifier_btn)

    self.cd_rip_send_to_editor_btn = QPushButton("Send to Editor")
    self.cd_rip_send_to_editor_btn.setProperty("class", "ActionButton")
    self.cd_rip_send_to_editor_btn.setProperty("startProcessCell", True)
    self.cd_rip_send_to_editor_btn.setProperty("busyState", True)
    self.cd_rip_send_to_editor_btn.setObjectName("CdSendToEditorButton")
    self.cd_rip_send_to_editor_btn.setCursor(Qt.PointingHandCursor)
    self.cd_rip_send_to_editor_btn.setEnabled(False)
    self._set_rounded_action_button_style(self.cd_rip_send_to_editor_btn, busy=True)
    action_layout.addWidget(self.cd_rip_send_to_editor_btn)
    layout.addLayout(action_layout)

    self._apply_card_line_surfaces_on_page(page)
    return self._wrap_page_in_transparent_scroll(page)

def _can_use_windows_loopback_backend(self):
    if sys.platform != "win32":
        return False
    try:
        return all(
            importlib.util.find_spec(pkg_name) is not None
            for pkg_name in ("numpy", "soundcard", "soundfile")
        )
    except Exception:
        return False

def _can_use_windows_process_capture_backend(self):
    if sys.platform != "win32" or mixsplitr_process_capture is None:
        return False
    try:
        return bool(
            mixsplitr_process_capture.is_process_loopback_helper_available(
                resource_resolver=_resolve_bundled_resource_path
            )
        )
    except Exception:
        return False

def _is_windows_process_capture_selection(self, selection):
    if not isinstance(selection, dict):
        return False
    return str(selection.get("backend", "")).strip().lower() == "windows_process_capture"

def _is_windows_loopback_selection(self, selection):
    if not isinstance(selection, dict):
        return False
    return str(selection.get("backend", "")).strip().lower() == "wasapi_loopback"

def _start_windows_loopback_recording(self, output_base_path, selection, force_wav=False, monitor_requested=False):
    if not self._can_use_windows_loopback_backend():
        return False, "WASAPI loopback is unavailable."

    speaker_name = ""
    speaker_id = ""
    if isinstance(selection, dict):
        speaker_name = str(selection.get("speaker_name", "")).strip()
        speaker_id = str(selection.get("speaker_id", "")).strip()

    self.recording_loopback_error = ""
    self.recording_loopback_last_level = 0.0
    self.recording_monitor_warning = ""
    self.recording_monitor_output_name = ""
    self.recording_segment_files = []
    self.recording_output_format = ""
    self.recording_loopback_stop_event = threading.Event()
    force_wav = bool(force_wav)
    monitor_requested = bool(monitor_requested)
    monitor_request_event = threading.Event()
    if monitor_requested:
        monitor_request_event.set()
    self.recording_monitor_request_event = monitor_request_event

    def _worker():
        try:
            import numpy as _np  # type: ignore
            import soundcard as _sc  # type: ignore
            import soundfile as _sf  # type: ignore
        except Exception as exc:
            self.recording_loopback_error = f"Loopback tools failed to load: {exc}"
            return

        def _patch_numpy_fromstring_binary_mode():
            """
            SoundCard 0.4.x uses numpy.fromstring() in binary mode internally.
            NumPy >=2 removed that path; patch locally for this recording thread.
            """
            original = getattr(_np, "fromstring", None)
            if original is None:
                return None

            def _compat(obj, dtype=float, sep="", count=-1, like=None):  # noqa: ANN001
                try:
                    return original(obj, dtype=dtype, sep=sep, count=count, like=like)  # type: ignore
                except TypeError:
                    return original(obj, dtype=dtype, sep=sep, count=count)  # type: ignore
                except ValueError as exc:
                    message = str(exc)
                    if "binary mode of fromstring is removed" in message:
                        arr = _np.frombuffer(obj, dtype=dtype, count=count)  # type: ignore
                        return arr.copy()
                    raise

            _np.fromstring = _compat  # type: ignore
            return original

        def _candidate_samplerates(mic_device):
            candidates = []
            try:
                native_sr = int(float(getattr(mic_device, "samplerate", 0)))
                if native_sr > 0:
                    candidates.append(native_sr)
            except Exception:
                pass
            for sr in (48000, 44100):
                if int(sr) not in candidates:
                    candidates.append(int(sr))
            return [int(candidate) for candidate in candidates if int(candidate) > 0]

        def _mic_accepts_samplerate(mic_device, candidate):
            try:
                mic_device.record(numframes=512, samplerate=int(candidate))
                return True
            except Exception:
                return False

        def _speaker_accepts_samplerate(speaker_device, candidate):
            try:
                with speaker_device.player(samplerate=int(candidate), channels=2, blocksize=512):
                    return True
            except Exception:
                return False

        def _choose_samplerate(mic_device, monitor_speaker=None):
            candidates = _candidate_samplerates(mic_device)
            if not candidates:
                candidates = [48000, 44100]
            for candidate in candidates:
                if not _mic_accepts_samplerate(mic_device, candidate):
                    continue
                if monitor_speaker is not None and not _speaker_accepts_samplerate(monitor_speaker, candidate):
                    continue
                return int(candidate), monitor_speaker is not None
            for candidate in candidates:
                if _mic_accepts_samplerate(mic_device, candidate):
                    return int(candidate), False
            return int(candidates[0]), False

        def _mix_to_stereo(data):
            if data is None:
                return None
            if getattr(data, "ndim", 1) == 1:
                data = data.reshape(-1, 1)
            channels = int(data.shape[1]) if getattr(data, "ndim", 1) > 1 else 1
            if channels <= 1:
                return _np.column_stack([data[:, 0], data[:, 0]])
            if channels == 2:
                return data

            # Do not assume active audio is always on channels 1/2 for multichannel outputs.
            left_src = data[:, ::2]
            right_src = data[:, 1::2]
            left = _np.mean(left_src, axis=1)
            if getattr(right_src, "shape", (0, 0))[1] > 0:
                right = _np.mean(right_src, axis=1)
            else:
                right = left
            return _np.column_stack([left, right])

        orig_fromstring = _patch_numpy_fromstring_binary_mode()

        try:
            speakers = list(_sc.all_speakers())
        except Exception as exc:
            self.recording_loopback_error = f"Could not enumerate output devices: {exc}"
            if orig_fromstring is not None:
                _np.fromstring = orig_fromstring  # type: ignore
            return

        def _device_token(value):
            if value is None:
                return ""
            if isinstance(value, (bytes, bytearray)):
                try:
                    return bytes(value).decode("utf-8", errors="ignore").strip()
                except Exception:
                    return ""
            return str(value).strip()

        def _same_output_device(left, right):
            if left is None or right is None:
                return False
            left_id = _device_token(getattr(left, "id", ""))
            right_id = _device_token(getattr(right, "id", ""))
            if left_id and right_id:
                return left_id == right_id
            left_name = _device_token(getattr(left, "name", ""))
            right_name = _device_token(getattr(right, "name", ""))
            return bool(left_name and right_name and left_name == right_name)

        def _choose_monitor_speaker(all_speakers, selected_output):
            preferred = []
            try:
                default_output = _sc.default_speaker()
            except Exception:
                default_output = None
            if default_output is not None:
                preferred.append(default_output)
            preferred.extend(list(all_speakers or []))

            seen = set()
            for candidate in preferred:
                candidate_name = _device_token(getattr(candidate, "name", ""))
                candidate_id = _device_token(getattr(candidate, "id", ""))
                token = candidate_id or candidate_name.lower()
                if not token or token in seen:
                    continue
                seen.add(token)
                if _same_output_device(candidate, selected_output):
                    continue
                if self._is_likely_virtual_audio_device_name(candidate_name):
                    continue
                return candidate
            return None

        selected_speaker = None

        if speaker_id:
            for speaker in speakers:
                candidate_id = _device_token(getattr(speaker, "id", ""))
                if candidate_id and candidate_id == speaker_id:
                    selected_speaker = speaker
                    break

        if selected_speaker is None and speaker_name:
            for speaker in speakers:
                candidate_name = _device_token(getattr(speaker, "name", ""))
                if candidate_name == speaker_name:
                    selected_speaker = speaker
                    break

        if selected_speaker is None and speaker_name:
            wanted = speaker_name.lower()
            for speaker in speakers:
                candidate_name = _device_token(getattr(speaker, "name", "")).lower()
                if candidate_name and (wanted in candidate_name or candidate_name in wanted):
                    selected_speaker = speaker
                    break

        if selected_speaker is None:
            try:
                selected_speaker = _sc.default_speaker()
            except Exception:
                selected_speaker = speakers[0] if speakers else None

        if selected_speaker is None:
            self.recording_loopback_error = "No output device available for loopback capture."
            if orig_fromstring is not None:
                _np.fromstring = orig_fromstring  # type: ignore
            return

        try:
            mic_lookup = _device_token(getattr(selected_speaker, "id", "")) or _device_token(
                getattr(selected_speaker, "name", "")
            )
            mic = _sc.get_microphone(mic_lookup, include_loopback=True)
        except Exception as exc:
            self.recording_loopback_error = f"Could not open loopback device: {exc}"
            if orig_fromstring is not None:
                _np.fromstring = orig_fromstring  # type: ignore
            return

        preferred_monitor_speaker = None
        monitor_restart_allowed = True
        if monitor_requested:
            preferred_monitor_speaker = _choose_monitor_speaker(speakers, selected_speaker)
            if preferred_monitor_speaker is None:
                self.recording_monitor_warning = (
                    "Monitor is on, but no non-cable speaker was found. Recording will stay silent."
                )
                monitor_restart_allowed = False

        samplerate, monitor_ready = _choose_samplerate(
            mic,
            monitor_speaker=preferred_monitor_speaker,
        )
        if preferred_monitor_speaker is not None and not monitor_ready:
            monitor_name = _device_token(getattr(preferred_monitor_speaker, "name", "")) or "your speakers"
            self.recording_monitor_warning = (
                f"Could not monitor through {monitor_name}. Recording will stay silent."
            )
            preferred_monitor_speaker = None
            monitor_restart_allowed = False
        input_channels = int(getattr(mic, "channels", 2) or 2)
        input_channels = max(1, input_channels)
        base_output = str(output_base_path or "").strip()
        base_root, base_ext = os.path.splitext(base_output)
        if base_ext.lower() in (".wav", ".flac"):
            base_output = base_root
        if not base_output:
            self.recording_loopback_error = "Invalid output file path for loopback capture."
            if orig_fromstring is not None:
                _np.fromstring = orig_fromstring  # type: ignore
            return

        wav_split_max_bytes = int(3.5 * 1024 * 1024 * 1024)
        wav_bytes_per_frame = 4  # stereo PCM16
        wav_split_max_frames = max(1, wav_split_max_bytes // wav_bytes_per_frame)

        def _segment_path(fmt, index):
            if fmt == "flac":
                return f"{base_output}.flac"
            return f"{base_output}_part{int(index):03d}.wav"

        output_writer = None
        segment_files = []
        segment_index = 1
        segment_frames_written = 0
        output_format = "flac"
        monitor_queue = None
        monitor_thread = None
        monitor_player_error = ""
        chunk_frames = max(1, int(float(samplerate) * 0.1))

        def _stop_monitor():
            nonlocal monitor_queue, monitor_thread
            local_queue = monitor_queue
            local_thread = monitor_thread
            monitor_queue = None
            monitor_thread = None
            self.recording_monitor_output_name = ""
            if local_queue is not None:
                try:
                    if local_queue.full():
                        try:
                            local_queue.get_nowait()
                        except queue.Empty:
                            pass
                    local_queue.put_nowait(None)
                except Exception:
                    pass
            if local_thread is not None:
                try:
                    local_thread.join(timeout=1.5)
                except Exception:
                    pass

        def _start_monitor(preferred_speaker=None):
            nonlocal monitor_queue, monitor_thread, monitor_player_error
            nonlocal preferred_monitor_speaker, monitor_restart_allowed
            if monitor_queue is not None and monitor_thread is not None and monitor_thread.is_alive():
                return True

            chosen_monitor = preferred_speaker
            preferred_monitor_speaker = None
            if chosen_monitor is None:
                chosen_monitor = _choose_monitor_speaker(speakers, selected_speaker)
            if chosen_monitor is None:
                self.recording_monitor_warning = (
                    "Monitor is on, but no non-cable speaker was found. Recording will stay silent."
                )
                monitor_restart_allowed = False
                return False

            monitor_name = _device_token(getattr(chosen_monitor, "name", "")) or "your speakers"
            if not _speaker_accepts_samplerate(chosen_monitor, samplerate):
                self.recording_monitor_warning = (
                    f"Could not monitor through {monitor_name}. Recording will stay silent."
                )
                monitor_restart_allowed = False
                return False

            local_queue = queue.Queue(maxsize=24)
            monitor_player_error = ""
            self.recording_monitor_warning = ""
            self.recording_monitor_output_name = monitor_name

            def _monitor_worker():
                nonlocal monitor_player_error
                try:
                    with chosen_monitor.player(
                        samplerate=int(samplerate),
                        channels=2,
                        blocksize=int(chunk_frames),
                    ) as player:
                        while True:
                            item = local_queue.get()
                            if item is None:
                                break
                            player.play(item)
                except Exception as exc:
                    monitor_player_error = f"Monitor playback stopped: {exc}"

            local_thread = threading.Thread(
                target=_monitor_worker,
                name="MixSplitRBackgroundMonitor",
                daemon=True,
            )
            monitor_queue = local_queue
            monitor_thread = local_thread
            local_thread.start()
            return True

        try:

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=RuntimeWarning,
                    message=".*discontinuity.*",
                )

                if force_wav:
                    first_path = _segment_path("wav", 1)
                    output_writer = _sf.SoundFile(
                        first_path,
                        mode="w",
                        samplerate=int(samplerate),
                        channels=2,
                        format="WAV",
                        subtype="PCM_16",
                    )
                    output_format = "wav"
                    self.recording_output_format = "wav"
                else:
                    first_path = _segment_path("flac", 1)
                    try:
                        output_writer = _sf.SoundFile(
                            first_path,
                            mode="w",
                            samplerate=int(samplerate),
                            channels=2,
                            format="FLAC",
                            subtype="PCM_16",
                        )
                        output_format = "flac"
                        self.recording_output_format = "flac"
                    except Exception:
                        first_path = _segment_path("wav", 1)
                        output_writer = _sf.SoundFile(
                            first_path,
                            mode="w",
                            samplerate=int(samplerate),
                            channels=2,
                            format="WAV",
                            subtype="PCM_16",
                        )
                        output_format = "wav"
                        self.recording_output_format = "wav"
                        self.recording_loopback_error = (
                            "FLAC output unavailable; using WAV with auto-split segments."
                        )

                segment_files = [first_path]
                self.recording_current_file = first_path
                self.recording_segment_files = list(segment_files)

                with mic.recorder(samplerate=int(samplerate), channels=input_channels) as recorder:
                    if monitor_request_event.is_set() and monitor_restart_allowed:
                        _start_monitor(preferred_speaker=preferred_monitor_speaker)
                    while not self.recording_loopback_stop_event.is_set():
                        data = recorder.record(numframes=chunk_frames)
                        if data is None:
                            continue
                        data = _mix_to_stereo(data)
                        if data is None:
                            continue
                        try:
                            peak = float(_np.max(_np.abs(data)))
                        except Exception:
                            peak = 0.0
                        peak = max(0.0, min(1.0, peak))
                        self.recording_loopback_last_level = max(
                            peak,
                            float(self.recording_loopback_last_level) * 0.84,
                        )

                        monitor_enabled = bool(monitor_request_event.is_set())
                        if not monitor_enabled:
                            if monitor_queue is not None or monitor_thread is not None:
                                _stop_monitor()
                            monitor_player_error = ""
                            monitor_restart_allowed = True
                            self.recording_monitor_warning = ""
                        else:
                            if monitor_player_error:
                                self.recording_monitor_warning = monitor_player_error
                                _stop_monitor()
                                monitor_restart_allowed = False
                            if (
                                monitor_restart_allowed
                                and (monitor_queue is None or monitor_thread is None or not monitor_thread.is_alive())
                            ):
                                _start_monitor()

                        if monitor_queue is not None and not monitor_player_error:
                            try:
                                monitor_chunk = _np.asarray(data, dtype=_np.float32).copy()
                                if monitor_queue.full():
                                    try:
                                        monitor_queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                monitor_queue.put_nowait(monitor_chunk)
                            except queue.Full:
                                pass
                            except Exception as exc:
                                monitor_player_error = f"Monitor playback stopped: {exc}"

                        if output_format == "wav":
                            offset = 0
                            total_frames = int(data.shape[0])
                            while offset < total_frames:
                                frames_left = int(wav_split_max_frames - segment_frames_written)
                                if frames_left <= 0:
                                    try:
                                        output_writer.close()
                                    except Exception:
                                        pass
                                    output_writer = None
                                    segment_index += 1
                                    next_path = _segment_path("wav", segment_index)
                                    output_writer = _sf.SoundFile(
                                        next_path,
                                        mode="w",
                                        samplerate=int(samplerate),
                                        channels=2,
                                        format="WAV",
                                        subtype="PCM_16",
                                    )
                                    segment_files.append(next_path)
                                    self.recording_segment_files = list(segment_files)
                                    segment_frames_written = 0
                                    frames_left = int(wav_split_max_frames)

                                write_frames = min(frames_left, total_frames - offset)
                                output_writer.write(data[offset:offset + write_frames])
                                segment_frames_written += int(write_frames)
                                offset += int(write_frames)
                        else:
                            output_writer.write(data)
        except Exception as exc:
            self.recording_loopback_error = str(exc)
        finally:
            _stop_monitor()
            if monitor_player_error and not self.recording_monitor_warning:
                self.recording_monitor_warning = monitor_player_error
            if output_writer is not None:
                try:
                    output_writer.close()
                except Exception:
                    pass
            if orig_fromstring is not None:
                _np.fromstring = orig_fromstring  # type: ignore

    self.recording_loopback_thread = threading.Thread(
        target=_worker,
        name="MixSplitRWasapiLoopback",
        daemon=True,
    )
    self.recording_loopback_thread.start()
    self.recording_active_backend = "wasapi_loopback"
    return True, ""

def _start_windows_process_capture_recording(self, output_base_path, selection, force_wav=False):
    if not self._can_use_windows_process_capture_backend():
        return False, "Windows app capture is unavailable."

    process_id = 0
    process_label = ""
    if isinstance(selection, dict):
        process_id = int(selection.get("pid") or 0)
        process_label = str(selection.get("label") or selection.get("process_name") or "").strip()
    if process_id <= 0:
        return False, "No app process selected for capture."

    self.recording_process_capture_error = ""
    self.recording_process_capture_last_level = 0.0
    self.recording_segment_files = []
    self.recording_output_format = ""
    self.recording_process_capture_proc = None
    self.recording_process_capture_stop_event = threading.Event()
    force_wav = bool(force_wav)

    def _worker():
        try:
            import numpy as _np  # type: ignore
            import soundfile as _sf  # type: ignore
        except Exception as exc:
            self.recording_process_capture_error = f"App capture tools failed to load: {exc}"
            return

        helper_path = ""
        try:
            helper_path = mixsplitr_process_capture.resolve_process_loopback_helper(
                resource_resolver=_resolve_bundled_resource_path
            )
        except Exception:
            helper_path = ""
        if not helper_path:
            self.recording_process_capture_error = "App capture helper not found in this build."
            return

        base_output = str(output_base_path or "").strip()
        if not base_output:
            self.recording_process_capture_error = "Invalid output file path for app capture."
            return

        cmd = [helper_path, "--capture", str(process_id)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                bufsize=0,
            )
        except Exception as exc:
            self.recording_process_capture_error = f"Could not start app capture helper: {exc}"
            return

        self.recording_process_capture_proc = proc

        def _read_protocol_line():
            if proc.stderr is None:
                return ""
            try:
                raw = proc.stderr.readline()
            except Exception:
                raw = b""
            return raw.decode("utf-8", errors="replace").strip()

        protocol_line = ""
        for _ in range(8):
            protocol_line = _read_protocol_line()
            if not protocol_line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            if protocol_line.startswith("FORMAT|") or protocol_line.startswith("ERROR|"):
                break

        if not protocol_line.startswith("FORMAT|"):
            err_text = protocol_line[6:].strip() if protocol_line.startswith("ERROR|") else ""
            if not err_text and proc.poll() is not None:
                err_text = f"helper exited with code {proc.returncode}"
            self.recording_process_capture_error = err_text or "App capture helper did not initialize."
            try:
                proc.terminate()
            except Exception:
                pass
            self.recording_process_capture_proc = None
            return

        parts = protocol_line.split("|")
        try:
            samplerate = max(8000, int(parts[1]))
            channels = max(1, int(parts[2]))
            bits_per_sample = max(8, int(parts[3]))
        except Exception:
            self.recording_process_capture_error = f"Invalid app capture format: {protocol_line}"
            try:
                proc.terminate()
            except Exception:
                pass
            self.recording_process_capture_proc = None
            return

        def _stderr_pump():
            stderr_pipe = proc.stderr
            if stderr_pipe is None:
                return
            while True:
                try:
                    raw = stderr_pipe.readline()
                except Exception:
                    raw = b""
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").strip()
                if text.startswith("ERROR|"):
                    self.recording_process_capture_error = text[6:].strip()

        stderr_thread = threading.Thread(
            target=_stderr_pump,
            name="MixSplitRProcessCaptureStderr",
            daemon=True,
        )
        stderr_thread.start()

        wav_split_max_bytes = int(3.5 * 1024 * 1024 * 1024)
        wav_bytes_per_frame = max(1, channels * max(1, bits_per_sample // 8))
        wav_split_max_frames = max(1, wav_split_max_bytes // wav_bytes_per_frame)

        def _segment_path(fmt, index):
            if fmt == "flac":
                return f"{base_output}.flac"
            return f"{base_output}_part{int(index):03d}.wav"

        output_writer = None
        segment_files = []
        segment_index = 1
        segment_frames_written = 0
        output_format = "flac"
        frame_bytes = max(1, channels * max(1, bits_per_sample // 8))
        pending = b""

        try:
            if force_wav:
                first_path = _segment_path("wav", 1)
                output_writer = _sf.SoundFile(
                    first_path,
                    mode="w",
                    samplerate=int(samplerate),
                    channels=int(channels),
                    format="WAV",
                    subtype="PCM_16",
                )
                output_format = "wav"
                self.recording_output_format = "wav"
            else:
                first_path = _segment_path("flac", 1)
                try:
                    output_writer = _sf.SoundFile(
                        first_path,
                        mode="w",
                        samplerate=int(samplerate),
                        channels=int(channels),
                        format="FLAC",
                        subtype="PCM_16",
                    )
                    output_format = "flac"
                    self.recording_output_format = "flac"
                except Exception:
                    first_path = _segment_path("wav", 1)
                    output_writer = _sf.SoundFile(
                        first_path,
                        mode="w",
                        samplerate=int(samplerate),
                        channels=int(channels),
                        format="WAV",
                        subtype="PCM_16",
                    )
                    output_format = "wav"
                    self.recording_output_format = "wav"
                    self.recording_process_capture_error = (
                        "FLAC output unavailable; using WAV with auto-split segments."
                    )

            segment_files = [first_path]
            self.recording_current_file = first_path
            self.recording_segment_files = list(segment_files)

            stdout_pipe = proc.stdout
            if stdout_pipe is None:
                raise RuntimeError("App capture helper did not provide an audio stream.")

            while True:
                if self.recording_process_capture_stop_event is not None and self.recording_process_capture_stop_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                chunk = stdout_pipe.read(65536)
                if not chunk:
                    if proc.poll() is None:
                        time.sleep(0.01)
                        continue
                    break

                pending += chunk
                usable = len(pending) - (len(pending) % frame_bytes)
                if usable <= 0:
                    continue

                data_bytes = pending[:usable]
                pending = pending[usable:]
                data = _np.frombuffer(data_bytes, dtype=_np.int16)
                if data.size <= 0:
                    continue
                try:
                    data = data.reshape(-1, int(channels))
                except Exception:
                    continue

                try:
                    peak = float(_np.max(_np.abs(data))) / 32767.0
                except Exception:
                    peak = 0.0
                peak = max(0.0, min(1.0, peak))
                self.recording_process_capture_last_level = max(
                    peak,
                    float(self.recording_process_capture_last_level) * 0.84,
                )

                if output_format == "wav":
                    offset = 0
                    total_frames = int(data.shape[0])
                    while offset < total_frames:
                        frames_left = int(wav_split_max_frames - segment_frames_written)
                        if frames_left <= 0:
                            try:
                                output_writer.close()
                            except Exception:
                                pass
                            output_writer = None
                            segment_index += 1
                            next_path = _segment_path("wav", segment_index)
                            output_writer = _sf.SoundFile(
                                next_path,
                                mode="w",
                                samplerate=int(samplerate),
                                channels=int(channels),
                                format="WAV",
                                subtype="PCM_16",
                            )
                            segment_files.append(next_path)
                            self.recording_segment_files = list(segment_files)
                            segment_frames_written = 0
                            frames_left = int(wav_split_max_frames)

                        write_frames = min(frames_left, total_frames - offset)
                        output_writer.write(data[offset:offset + write_frames])
                        segment_frames_written += int(write_frames)
                        offset += int(write_frames)
                else:
                    output_writer.write(data)
        except Exception as exc:
            self.recording_process_capture_error = str(exc)
        finally:
            if output_writer is not None:
                try:
                    output_writer.close()
                except Exception:
                    pass
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.5)
            except Exception:
                pass
            self.recording_process_capture_proc = None

        stop_requested = bool(
            self.recording_process_capture_stop_event is not None
            and self.recording_process_capture_stop_event.is_set()
        )
        if proc.returncode not in (0, None) and not stop_requested and not self.recording_process_capture_error:
            self.recording_process_capture_error = f"App capture helper exited with code {proc.returncode}"

    thread_name = f"MixSplitRAppCapture_{process_id}"
    self.recording_process_capture_thread = threading.Thread(
        target=_worker,
        name=thread_name,
        daemon=True,
    )
    self.recording_process_capture_thread.start()
    self.recording_active_backend = "windows_process_capture"
    if process_label:
        self.recording_current_file = ""
    return True, ""

def _set_recording_status(self, text, error=False):
    color = "#C4C7C5" if not error else "#E45D5D"
    self.recording_status_label.setStyleSheet(f"color: {color};")
    self.recording_status_label.setText(f"Status: {text}")

def _set_signal_status(self, text, color="#C4C7C5"):
    self.recording_signal_label.setStyleSheet(f"color: {color};")
    self.recording_signal_label.setText(f"Signal: {text}")

def _recording_background_mode_enabled(self):
    return bool(
        sys.platform == "win32"
        and self.recording_capture_mode_background_radio is not None
        and self.recording_capture_mode_background_radio.isChecked()
    )

def _is_likely_virtual_audio_device_name(self, name):
    text = str(name or "").strip()
    if not text:
        return False
    if mixsplitr_process_capture is not None and hasattr(
        mixsplitr_process_capture, "is_likely_virtual_audio_device_name"
    ):
        try:
            return bool(mixsplitr_process_capture.is_likely_virtual_audio_device_name(text))
        except Exception:
            pass
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "vb-audio",
            "vb cable",
            "vb-cable",
            "virtual cable",
            "virtual audio cable",
            "voicemeeter",
            "hi-fi cable",
            "blackhole",
        )
    )

def _virtual_audio_device_channel_mode(self, name):
    text = str(name or "").strip()
    if not text:
        return ""
    if mixsplitr_process_capture is not None and hasattr(
        mixsplitr_process_capture, "virtual_audio_device_channel_mode"
    ):
        try:
            return str(mixsplitr_process_capture.virtual_audio_device_channel_mode(text) or "").strip().lower()
        except Exception:
            pass
    lowered = text.lower()
    if not self._is_likely_virtual_audio_device_name(lowered):
        return ""
    if any(token in lowered for token in ("16ch", "16 ch", "16-channel", "16 channel", "16 channels")):
        return "16ch"
    return "stereo"

def _virtual_audio_input_device_channel_mode(self, device, description):
    mode = self._virtual_audio_device_channel_mode(description)
    if mode == "16ch":
        return mode

    channel_count = self._recording_device_channel_count(device)
    if channel_count > 2:
        return "16ch"
    return mode or "stereo"

def _recording_device_channel_count(self, device):
    max_channels = 0
    try:
        if device is not None and hasattr(device, "maximumChannelCount"):
            max_channels = int(device.maximumChannelCount() or 0)
    except Exception:
        max_channels = 0

    preferred_channels = 0
    try:
        if device is not None and hasattr(device, "preferredFormat"):
            fmt = device.preferredFormat()
            if fmt is not None and hasattr(fmt, "channelCount"):
                preferred_channels = int(fmt.channelCount() or 0)
    except Exception:
        preferred_channels = 0

    return max(max_channels, preferred_channels)

def _sync_recording_background_device_ui(self):
    row = self.recording_background_device_row
    if row is None:
        return
    background_mode = self._recording_background_mode_enabled()
    indexes = dict(self.recording_background_device_indexes or {})
    has_choice = "stereo" in indexes
    show_simple_picker = background_mode and has_choice
    row.setVisible(show_simple_picker)
    self.recording_input_select.setVisible(not show_simple_picker)

    if not show_simple_picker:
        return

    stereo_radio = self.recording_background_stereo_radio
    if stereo_radio is not None:
        stereo_available = "stereo" in indexes
        stereo_radio.setEnabled(stereo_available and not self.recording_active)
        stereo_radio.setVisible(stereo_available)

    current_idx = self.recording_input_select.currentIndex()
    if current_idx >= 0:
        for mode_name, idx in indexes.items():
            if int(idx) == int(current_idx):
                if mode_name == "stereo" and stereo_radio is not None and not stereo_radio.isChecked():
                    stereo_radio.setChecked(True)
                return

    if "stereo" in indexes and stereo_radio is not None:
        stereo_radio.setChecked(True)
        self.recording_input_select.setCurrentIndex(int(indexes["stereo"]))

def _recording_background_monitor_enabled(self):
    return bool(
        sys.platform == "win32"
        and self.recording_background_monitor_check is not None
        and self._recording_background_mode_enabled()
        and self.recording_background_monitor_check.isChecked()
    )

def _sync_recording_background_monitor_ui(self):
    checkbox = self.recording_background_monitor_check
    if checkbox is None:
        return
    visible = self._recording_background_mode_enabled()
    checkbox.setVisible(visible)
    checkbox.setEnabled(visible)

def _refresh_loopback_recording_status(self):
    if not self.recording_active or self.recording_active_backend != "wasapi_loopback":
        return
    status_target = self.recording_current_file or self._current_recording_target_file()
    status_text = f"Recording… {os.path.basename(status_target)}" if status_target else "Recording…"
    monitor_requested = False
    monitor_event = getattr(self, "recording_monitor_request_event", None)
    if monitor_event is not None:
        try:
            monitor_requested = bool(monitor_event.is_set())
        except Exception:
            monitor_requested = False
    elif self._recording_background_monitor_enabled():
        monitor_requested = True
    if monitor_requested:
        status_text = f"{status_text} (monitor on)"
    self._set_recording_status(status_text)

def _on_recording_background_monitor_toggled(self, checked):
    self.recording_monitor_warning = ""
    self.recording_monitor_output_name = ""
    if not self.recording_active or self.recording_active_backend != "wasapi_loopback":
        return
    monitor_event = getattr(self, "recording_monitor_request_event", None)
    if monitor_event is not None:
        try:
            if checked:
                monitor_event.set()
            else:
                monitor_event.clear()
        except Exception:
            pass
    self._refresh_loopback_recording_status()

def _refresh_recording_windows_capture_guidance(self):
    if sys.platform != "win32":
        return
    app_hint = self.recording_windows_app_capture_hint
    bg_hint = self.recording_windows_background_hint
    bg_status = self.recording_windows_background_status
    if app_hint is None or bg_hint is None or bg_status is None:
        return

    background_mode = self._recording_background_mode_enabled()
    app_hint.setVisible(not background_mode)
    bg_hint.setVisible(background_mode)
    bg_status.setVisible(background_mode)

    if not background_mode:
        return

    output_names = [str(name or "").strip() for name in list(self.recording_virtual_output_names or []) if str(name or "").strip()]
    if output_names:
        output_summary = ", ".join(output_names[:2])
        if len(output_names) > 2:
            output_summary = f"{output_summary} +{len(output_names) - 2} more"
        output_summary_markup = (
            f'<span style="color:#E04040;">{html.escape(output_summary)}</span>'
        )
        status_text = (
            f"The detected cable devices are: {output_summary_markup}. "
            "In Windows Volume Mixer, in your apps dropdown, select the same cable device as the output device. "
            "(Some programs (Apple Music/Spotify) may need to be restarted before they will switch to the selected device.) Then come back here and record."
        )
        bg_status.setStyleSheet("color: #C4C7C5; font-size: 12px; margin: 0 0 4px 0;")
    else:
        status_text = (
            "MixSplitR could not find a cable-style device yet. "
            f'<a href="{VB_CABLE_DOWNLOAD_URL}" style="color:#E04040;">Download VB-Cable</a> '
            "or another compatible virtual audio device, then click Refresh Sources."
        )
        bg_status.setStyleSheet("color: #E8A848; font-size: 12px; margin: 0 0 4px 0;")
    bg_status.setText(status_text)

def _recording_blackhole_install_link_html(self):
    if sys.platform != "darwin":
        return ""
    return (
        f' or click <a href="{BLACKHOLE_BREW_INSTALL_ACTION_URL}" style="color:#E04040;">HERE</a> '
        "to install Homebrew and BlackHole 2ch automatically "
        "(quickest results, requires restart)"
    )

def _refresh_recording_help_text(self):
    brew_link_html = self._recording_blackhole_install_link_html()

    if hasattr(self, "blackhole_hint") and self.blackhole_hint is not None:
        self.blackhole_hint.setText(
            f'⚠ BlackHole not detected. Install <b>BlackHole 2ch</b> to record system audio on macOS. '
            f'<a href="{BLACKHOLE_DOWNLOAD_URL}" style="color:#E04040;">Download BlackHole</a>'
            f"{brew_link_html}."
        )

    help_label = getattr(self, "recording_setup_help_label", None)
    if help_label is None:
        return
    help_label.setText(
        "Windows: App Capture and Background Capture are Windows-only. Pick an entry labeled "
        "'(App Capture)' to record a specific app like Spotify "
        "or Apple Music. App Capture entries only appear for apps that are playing audio right "
        "now, so start playback first and then click Refresh Sources. Pick an entry labeled "
        "'(WASAPI loopback)' if you want to record everything you hear. For silent background "
        "recording, switch the recorder page to Background Capture, choose Stereo to "
        "match your cable device, use a cable-style device "
        f'such as <a href="{VB_CABLE_DOWNLOAD_URL}" style="color:#E04040;">VB-Cable</a>, '
        "then send the app to that same cable device in Windows Volume Mixer. If you still want "
        "to hear it, turn on Hear Through Speakers While Recording. "
        "Press Start Recording and watch the Signal meter to make sure audio is coming "
        "through. If the app or device still does not show up, refresh sources again or relaunch "
        "MixSplitR.<br><br>"
        f'macOS: App Capture and Background Capture are not available on macOS. Install '
        f'<a href="{BLACKHOLE_DOWNLOAD_URL}" style="color:#E04040;">BlackHole 2ch</a> '
        f"(recommended){brew_link_html}, "
        "then create a Multi-Output Device in Audio MIDI Setup that combines your speakers with "
        "BlackHole. Set that as your macOS output, pick BlackHole as the input in MixSplitR, "
        "and record. Switch your output back to normal when you're done."
    )

def _handle_recording_help_link(self, link):
    target = str(link or "").strip()
    if not target:
        return
    if target == BLACKHOLE_BREW_INSTALL_ACTION_URL:
        self._run_blackhole_homebrew_install()
        return
    if not QDesktopServices.openUrl(QUrl(target)):
        self._set_recording_status("Could not open help link.", error=True)

def _run_blackhole_homebrew_install(self):
    if sys.platform != "darwin":
        self._set_recording_status("BlackHole Homebrew install is only available on macOS.", error=True)
        return

    install_prompt = QMessageBox(self)
    install_prompt.setWindowTitle("Install Homebrew + BlackHole 2ch")
    install_prompt.setIcon(QMessageBox.Question)
    install_prompt.setText("How should MixSplitR run the installer?")
    install_prompt.setInformativeText(
        "MixSplitR will open Terminal, install or update Homebrew if needed, "
        "then install or upgrade BlackHole 2ch.\n\n"
        "Install + Restart is the quickest path and is recommended after a fresh install."
    )
    install_restart_button = install_prompt.addButton("Install + Restart", QMessageBox.AcceptRole)
    install_only_button = install_prompt.addButton("Install Only", QMessageBox.ActionRole)
    cancel_button = install_prompt.addButton("Cancel", QMessageBox.RejectRole)
    install_prompt.setDefaultButton(install_restart_button)
    install_prompt.exec()

    clicked_button = install_prompt.clickedButton()
    if clicked_button == cancel_button or clicked_button is None:
        return
    auto_restart_after_install = clicked_button == install_restart_button

    try:
        script_dir = self._recording_runtime_temp_dir()
        os.makedirs(script_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".sh",
            prefix="mixsplitr_install_blackhole_",
            dir=script_dir,
            delete=False,
        ) as script_file:
            script_path = os.path.abspath(script_file.name)
            script_file.write("#!/bin/zsh\n")
            script_file.write("AUTO_RESTART=${1:-0}\n")
            script_file.write("pause_and_exit() {\n")
            script_file.write("  local exit_code=${1:-0}\n")
            script_file.write("  echo\n")
            script_file.write('  echo "Press any key to close this Terminal window..."\n')
            script_file.write("  IFS= read -r -k 1 _mixsplitr_key\n")
            script_file.write('  echo ""\n')
            script_file.write("  exit \"$exit_code\"\n")
            script_file.write("}\n")
            script_file.write("run_or_pause() {\n")
            script_file.write("  \"$@\"\n")
            script_file.write("  local status=$?\n")
            script_file.write("  if [ $status -ne 0 ]; then\n")
            script_file.write("    echo\n")
            script_file.write('    echo "The command above failed with exit code $status."\n')
            script_file.write("    pause_and_exit $status\n")
            script_file.write("  fi\n")
            script_file.write("}\n")
            script_file.write("clear\n")
            script_file.write('echo "Preparing Homebrew + BlackHole 2ch install..."\n')
            script_file.write("echo\n")
            script_file.write("if command -v brew >/dev/null 2>&1; then\n")
            script_file.write('  BREW_BIN=\"$(command -v brew)\"\n')
            script_file.write('  echo \"Homebrew detected at $BREW_BIN\"\n')
            script_file.write("else\n")
            script_file.write('  echo \"Homebrew not found. Installing Homebrew first...\"\n')
            script_file.write('  run_or_pause /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n')
            script_file.write("  if [ -x /opt/homebrew/bin/brew ]; then\n")
            script_file.write('    BREW_BIN=\"/opt/homebrew/bin/brew\"\n')
            script_file.write("  elif [ -x /usr/local/bin/brew ]; then\n")
            script_file.write('    BREW_BIN=\"/usr/local/bin/brew\"\n')
            script_file.write("  else\n")
            script_file.write('    echo \"Homebrew install completed, but brew was not found in the default locations.\"\n')
            script_file.write("    pause_and_exit 1\n")
            script_file.write("  fi\n")
            script_file.write("fi\n")
            script_file.write("echo\n")
            script_file.write('eval \"$($BREW_BIN shellenv)\"\n')
            script_file.write('echo \"Updating Homebrew...\"\n')
            script_file.write('run_or_pause "$BREW_BIN" update\n')
            script_file.write("echo\n")
            script_file.write('if \"$BREW_BIN\" list --cask blackhole-2ch >/dev/null 2>&1; then\n')
            script_file.write('  echo \"BlackHole 2ch is already installed. Upgrading it...\"\n')
            script_file.write('  run_or_pause "$BREW_BIN" upgrade --cask blackhole-2ch\n')
            script_file.write("else\n")
            script_file.write('  echo \"Installing BlackHole 2ch...\"\n')
            script_file.write('  run_or_pause "$BREW_BIN" install --cask blackhole-2ch\n')
            script_file.write("fi\n")
            script_file.write("echo\n")
            script_file.write('echo \"BlackHole 2ch installation finished.\"\n')
            script_file.write("echo\n")
            script_file.write('if [ \"$AUTO_RESTART\" = \"1\" ]; then\n')
            script_file.write('  echo \"Restarting macOS automatically in 10 seconds...\"\n')
            script_file.write('  echo \"Press Control+C now if you need to cancel the restart.\"\n')
            script_file.write("  sleep 10\n")
            script_file.write("  if ! osascript -e 'tell application \"System Events\" to restart'; then\n")
            script_file.write('    echo \"Automatic restart failed.\"\n')
            script_file.write("    pause_and_exit 1\n")
            script_file.write("  fi\n")
            script_file.write("  exit 0\n")
            script_file.write("fi\n")
            script_file.write('echo \"Restart macOS before recording if BlackHole was newly installed.\"\n')
            script_file.write("pause_and_exit 0\n")

        os.chmod(script_path, 0o755)
    except Exception as exc:
        self._set_recording_status(f"Could not prepare Homebrew install script: {exc}", error=True)
        return

    command = f"/bin/zsh {shlex.quote(script_path)} {'1' if auto_restart_after_install else '0'}"
    escaped_command = command.replace("\\", "\\\\").replace('"', '\\"')
    osa_args = ["osascript"]
    for line in (
        'tell application "Terminal"',
        "activate",
        f'do script "{escaped_command}"',
        "end tell",
    ):
        osa_args.extend(["-e", line])

    try:
        subprocess.Popen(
            osa_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        if auto_restart_after_install:
            self._set_recording_status(
                "Opened Terminal to install/update Homebrew and BlackHole 2ch, then restart macOS."
            )
        else:
            self._set_recording_status(
                "Opened Terminal to install/update Homebrew and BlackHole 2ch."
            )
    except Exception as exc:
        self._set_recording_status(f"Could not launch Homebrew install: {exc}", error=True)

def _open_windows_recording_settings(self, uri, success_text="Opened Windows settings."):
    if sys.platform != "win32":
        return
    try:
        opened = QDesktopServices.openUrl(QUrl(str(uri)))
    except Exception:
        opened = False
    if opened:
        self._set_recording_status(success_text)
    else:
        self._set_recording_status("Could not open Windows audio settings.", error=True)

def _recording_runtime_temp_dir(self):
    if mixsplitr_core and hasattr(mixsplitr_core, "get_runtime_temp_directory"):
        try:
            return str(mixsplitr_core.get_runtime_temp_directory("recording"))
        except Exception:
            pass
    try:
        fallback_dir = os.path.join(tempfile.gettempdir(), "mixsplitr_recording")
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir
    except Exception:
        return tempfile.gettempdir()

def _launch_windows_audio_service_restart(self):
    if sys.platform != "win32":
        return False, "Windows audio service restart is only available on Windows."
    try:
        script_dir = self._recording_runtime_temp_dir()
        os.makedirs(script_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".cmd",
            prefix="mixsplitr_restart_audio_",
            dir=script_dir,
            delete=False,
        ) as script_file:
            script_path = os.path.abspath(script_file.name)
            script_file.write("@echo off\n")
            script_file.write("net stop audiosrv\n")
            script_file.write("net start audiosrv\n")
            script_file.write("del \"%~f0\" >nul 2>&1\n")

        try:
            import ctypes

            shell32 = ctypes.windll.shell32
            result = int(
                shell32.ShellExecuteW(
                    None,
                    "runas",
                    "cmd.exe",
                    f'/c "{script_path}"',
                    None,
                    0,
                )
            )
        except Exception as exc:
            return False, f"Could not request administrator privileges: {exc}"

        if result <= 32:
            return False, "Administrator approval was not granted, or Windows could not start the restart command."
        return True, ""
    except Exception as exc:
        return False, f"Could not prepare audio service restart: {exc}"

def _restart_windows_audio_services(self):
    if sys.platform != "win32":
        return
    if self.recording_active:
        QMessageBox.information(
            self,
            "Restart Audio Services",
            "Stop recording before restarting Windows audio services.",
        )
        return

    confirm = QMessageBox.question(
        self,
        "Restart Audio Services",
        "Restart Windows Audio now?\n\n"
        "This will briefly interrupt system audio and should trigger a Windows admin prompt.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        return

    ok, error_text = self._launch_windows_audio_service_restart()
    if not ok:
        self._set_recording_status(error_text or "Could not restart Windows audio services.", error=True)
        return

    self._set_recording_status(
        "Requested Windows Audio restart. Approve the admin prompt if it appears."
    )
    QTimer.singleShot(5000, self._populate_recording_inputs)
    QTimer.singleShot(6500, self._refresh_recording_windows_capture_guidance)

def _on_recording_capture_mode_toggled(self, mode_name, checked):
    if not checked:
        return
    self._sync_recording_background_monitor_ui()
    self._refresh_recording_windows_capture_guidance()
    if self.recording_active:
        return
    self._populate_recording_inputs()

def _on_recording_input_selection_changed(self, _idx):
    self._restart_audio_level_meter()
    self._refresh_recording_windows_capture_guidance()
    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()

def _on_recording_background_device_toggled(self, mode_name, checked):
    if not checked:
        return
    idx = self.recording_background_device_indexes.get(str(mode_name).strip().lower())
    if idx is None:
        return
    if self.recording_input_select.currentIndex() != int(idx):
        self.recording_input_select.setCurrentIndex(int(idx))

def _selected_recording_device(self):
    device = self.recording_input_select.currentData()
    if device is not None:
        return device
    if QT_MEDIA_AVAILABLE and QMediaDevices is not None:
        return QMediaDevices.defaultAudioInput()
    return None

def _stop_audio_level_meter(self):
    self.level_poll_timer.stop()
    if self.level_io_device is not None:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"Failed to disconnect .*readyRead",
                    category=RuntimeWarning,
                )
                self.level_io_device.readyRead.disconnect(self._poll_audio_level_meter)
        except Exception:
            pass
    if self.level_source is not None:
        try:
            self.level_source.stop()
        except Exception:
            pass
    self.level_source = None
    self.level_io_device = None
    self.level_current = 0.0
    self.recording_signal_bar.setValue(0)

def _restart_audio_level_meter(self):
    self._stop_audio_level_meter()
    if self.recording_active:
        self._start_audio_level_meter()
    else:
        self._set_signal_status("idle (starts on record)", color="#C4C7C5")

def _start_audio_level_meter(self):
    self.recording_signal_bar.setValue(0)
    self.level_last_active_seconds = time.monotonic()

    if self.recording_active_backend in ("wasapi_loopback", "windows_process_capture"):
        self.level_current = 0.0
        self.level_poll_timer.start()
        self._set_signal_status("listening...", color="#C4C7C5")
        return

    if not QT_MEDIA_AVAILABLE or QAudioSource is None:
        self._set_signal_status("meter unavailable in this build", color="#E45D5D")
        return

    device = self._selected_recording_device()
    if device is None:
        self._set_signal_status("no input device", color="#E45D5D")
        return

    try:
        self.level_source = QAudioSource(device)
        if hasattr(self.level_source, "setBufferSize"):
            self.level_source.setBufferSize(4096)
        self.level_io_device = self.level_source.start()
    except Exception:
        self.level_source = None
        self.level_io_device = None
        self._set_signal_status("meter unavailable", color="#E45D5D")
        return

    if self.level_io_device is None:
        self._set_signal_status("meter unavailable", color="#E45D5D")
        return

    try:
        fmt = self.level_source.format()
        width = int(fmt.bytesPerSample()) if hasattr(fmt, "bytesPerSample") else 2
    except Exception:
        width = 2
    self.level_sample_width = max(1, min(4, width))
    self.level_current = 0.0
    self.level_poll_timer.start()
    self._set_signal_status("listening...", color="#C4C7C5")

def _extract_level_from_audio_bytes(self, sample_bytes, sample_format_text, width):
    if not sample_bytes or width <= 0:
        return 0.0

    try:
        if "UInt8" in sample_format_text:
            usable = len(sample_bytes)
            if usable <= 0:
                return 0.0
            centered = bytes((b - 128) & 0xFF for b in sample_bytes[:usable])
            rms = float(audioop.rms(centered, 1))
            peak = float(max(abs((b - 128)) for b in sample_bytes[:usable])) if usable else 0.0
            return min(1.0, max((rms / 127.0) * 1.6, peak / 127.0))

        if "Int16" in sample_format_text or width == 2:
            usable = len(sample_bytes) - (len(sample_bytes) % 2)
            if usable <= 0:
                return 0.0
            chunk = sample_bytes[:usable]
            rms = float(audioop.rms(chunk, 2))
            peak = float(audioop.max(chunk, 2))
            return min(1.0, max((rms / 32767.0) * 1.7, peak / 32767.0))

        if "Int32" in sample_format_text or width == 4:
            usable = len(sample_bytes) - (len(sample_bytes) % 4)
            if usable <= 0:
                return 0.0
            values = struct.iter_unpack("<i", sample_bytes[:usable])
            abs_vals = [abs(v[0]) for v in values]
            if not abs_vals:
                return 0.0
            peak = float(max(abs_vals)) / 2147483647.0
            rms = (sum(v * v for v in abs_vals) / float(len(abs_vals))) ** 0.5 / 2147483647.0
            return min(1.0, max(rms * 1.7, peak))

        if "Float" in sample_format_text:
            usable = len(sample_bytes) - (len(sample_bytes) % 4)
            if usable <= 0:
                return 0.0
            values = struct.iter_unpack("<f", sample_bytes[:usable])
            abs_vals = [min(1.0, abs(v[0])) for v in values]
            if not abs_vals:
                return 0.0
            peak = max(abs_vals)
            rms = (sum(v * v for v in abs_vals) / float(len(abs_vals))) ** 0.5
            return min(1.0, max(rms * 1.7, peak))
    except Exception:
        return 0.0

    try:
        usable = len(sample_bytes) - (len(sample_bytes) % width)
        if usable <= 0:
            return 0.0
        rms = float(audioop.rms(sample_bytes[:usable], width))
        max_possible = float((1 << ((8 * width) - 1)) - 1)
        return min(1.0, max(0.0, rms / max_possible)) if max_possible > 0 else 0.0
    except Exception:
        return 0.0

def _poll_audio_level_meter(self):
    if self.recording_active_backend in ("wasapi_loopback", "windows_process_capture"):
        if self.recording_active_backend == "windows_process_capture":
            raw_level = max(0.0, min(1.0, float(self.recording_process_capture_last_level or 0.0)))
        else:
            raw_level = max(0.0, min(1.0, float(self.recording_loopback_last_level or 0.0)))
        level = max(0.0, min(1.0, raw_level * 2.4))
        if level > self.level_current:
            self.level_current = (self.level_current * 0.36) + (level * 0.64)
        else:
            self.level_current = (self.level_current * 0.82) + (level * 0.18)
        self.recording_signal_bar.setValue(int(max(0.0, min(1.0, self.level_current)) * 100))

        now = time.monotonic()
        if level > 0.03:
            self.level_last_active_seconds = now
            self._set_signal_status("receiving audio", color="#4D8DFF")
        elif now - self.level_last_active_seconds > 1.2:
            self._set_signal_status("silence", color="#E45D5D")
        else:
            self._set_signal_status("low signal", color="#C4C7C5")
        self._maybe_auto_stop_for_silence(now)
        return

    if self.level_io_device is None:
        return

    try:
        data = bytes(self.level_io_device.readAll())
    except Exception:
        data = b""

    if not data:
        decayed = self.level_current * 0.92
        self.level_current = decayed
        self.recording_signal_bar.setValue(int(max(0.0, min(1.0, decayed)) * 100))
        now = time.monotonic()
        if now - self.level_last_active_seconds > 1.2:
            self._set_signal_status("silence", color="#E45D5D")
        self._maybe_auto_stop_for_silence(now)
        return

    width = self.level_sample_width
    usable = len(data) - (len(data) % width)
    if usable <= 0:
        return
    sample_bytes = data[:usable]

    try:
        fmt = self.level_source.format() if self.level_source is not None else None
        sample_format_text = str(fmt.sampleFormat()) if (fmt is not None and hasattr(fmt, "sampleFormat")) else ""
        level = self._extract_level_from_audio_bytes(sample_bytes, sample_format_text, width)
    except Exception:
        level = 0.0

    if level > self.level_current:
        self.level_current = (self.level_current * 0.40) + (level * 0.60)
    else:
        self.level_current = (self.level_current * 0.82) + (level * 0.18)
    self.recording_signal_bar.setValue(int(self.level_current * 100))

    now = time.monotonic()
    if level > 0.035:
        self.level_last_active_seconds = now
        self._set_signal_status("receiving audio", color="#4D8DFF")
    elif now - self.level_last_active_seconds > 1.2:
        self._set_signal_status("silence", color="#E45D5D")
    else:
        self._set_signal_status("low signal", color="#C4C7C5")
    self._maybe_auto_stop_for_silence(now)

def _format_elapsed(self, seconds):
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def _recording_stop_after_seconds(self):
    checkbox = getattr(self, "recording_stop_after_check", None)
    if checkbox is None or not checkbox.isChecked():
        return 0
    widget = getattr(self, "recording_stop_after_time_input", None)
    if widget is None:
        return 0
    raw_text = str(widget.text() or "").strip()
    parts = raw_text.split(":")
    if len(parts) != 3:
        return 0
    try:
        hours = max(0, int(parts[0] or 0))
        minutes = max(0, int(parts[1] or 0))
        seconds = max(0, int(parts[2] or 0))
    except Exception:
        return 0
    total = (hours * 3600) + (minutes * 60) + seconds
    return max(0, int(total))

def _sync_recording_stop_timer_ui(self):
    checkbox = getattr(self, "recording_stop_after_check", None)
    enabled = bool(checkbox is not None and checkbox.isChecked())
    widget = getattr(self, "recording_stop_after_time_input", None)
    if widget is not None:
        widget.setEnabled(enabled)

def _maybe_auto_stop_for_duration(self, elapsed_seconds=None):
    if not self.recording_active or self.recording_auto_stop_triggered:
        return
    target_seconds = int(self._recording_stop_after_seconds())
    if target_seconds <= 0:
        return
    if elapsed_seconds is None:
        if not self.recording_elapsed.isValid():
            return
        elapsed_seconds = self.recording_elapsed.elapsed() / 1000.0
    if float(elapsed_seconds) + 0.01 >= float(target_seconds):
        self.recording_auto_stop_triggered = True
        self.stop_recording(reason=f"Auto-stopped at {self._format_elapsed(target_seconds)}")

def _on_recording_stop_after_toggled(self, checked):
    self._sync_recording_stop_timer_ui()
    if checked:
        self._maybe_auto_stop_for_duration()

def _on_recording_stop_after_value_changed(self, _value):
    if self.recording_active:
        self._maybe_auto_stop_for_duration()

def _recording_force_wav_enabled(self):
    try:
        return bool(self.recording_force_wav_check.isChecked())
    except Exception:
        return False

def _recording_output_directory(self):
    configured = self._normalized_path(self.recording_dir_input.text())
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured
    default_dir = self._default_recording_directory()
    os.makedirs(default_dir, exist_ok=True)
    return default_dir

def _is_recording_audio_file(self, file_name):
    lower = str(file_name or "").lower()
    return lower.endswith((".wav", ".flac", ".mp3", ".m4a", ".aac", ".aiff", ".ogg", ".opus"))

def _selected_recording_path(self):
    item = self.recordings_list.currentItem() if hasattr(self, "recordings_list") else None
    if item is None:
        return ""
    path = str(item.data(Qt.UserRole) or "")
    if path and os.path.exists(path):
        return path
    return ""

def _current_recording_target_file(self):
    selected = self._selected_recording_path()
    if selected:
        return selected
    if self.recording_last_file and os.path.exists(self.recording_last_file):
        return self.recording_last_file
    return ""

def _build_trimmed_recording_output_path(self, source_path):
    source_path = os.path.abspath(str(source_path or "").strip())
    directory = os.path.dirname(source_path)
    stem, ext = os.path.splitext(os.path.basename(source_path))
    ext = ext or ".wav"
    candidate = os.path.join(directory, f"{stem}_trimmed{ext}")
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_trimmed_{suffix:02d}{ext}")
        suffix += 1
    return candidate

def _trim_recording_audio_file(self, source_path, output_path, trim_start_seconds, trim_end_seconds):
    if mixsplitr_core is None or not hasattr(mixsplitr_core, "setup_ffmpeg"):
        raise RuntimeError("FFmpeg setup is unavailable in this build.")

    ffmpeg_path, _ = mixsplitr_core.setup_ffmpeg()
    ffmpeg_path = str(ffmpeg_path or "").strip()
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        raise RuntimeError("FFmpeg binary not found.")

    trim_start = max(0.0, float(trim_start_seconds or 0.0))
    trim_end = max(trim_start, float(trim_end_seconds or trim_start))
    trim_duration = max(0.001, trim_end - trim_start)
    output_ext = os.path.splitext(str(output_path or ""))[1].strip().lower()

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source_path,
        "-ss",
        f"{trim_start:.6f}",
        "-t",
        f"{trim_duration:.6f}",
        "-map_metadata",
        "0",
        "-vn",
    ]
    if output_ext == ".flac":
        command.extend(["-c:a", "flac"])
    elif output_ext == ".wav":
        command.extend(["-c:a", "pcm_s24le"])
    command.append(output_path)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    while proc.poll() is None:
        QApplication.processEvents()
        time.sleep(0.03)

    stderr_text = ""
    if proc.stderr is not None:
        try:
            stderr_text = str(proc.stderr.read() or "").strip()
        except Exception:
            stderr_text = ""
    if proc.returncode != 0:
        raise RuntimeError(stderr_text or f"FFmpeg exited with code {proc.returncode}")
    if not os.path.exists(output_path):
        raise RuntimeError("Trimmed output file was not created.")
    try:
        if int(os.path.getsize(output_path)) <= 0:
            raise RuntimeError("Trimmed output file is empty.")
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"Could not validate trimmed output: {exc}") from exc

def _update_recording_action_buttons(self):
    trim_busy = bool(getattr(self, "_recording_trim_busy", False))
    has_target = bool(self._current_recording_target_file())
    has_selected = bool(self._selected_recording_path())
    enabled = bool(has_target and not self.recording_active and not trim_busy)
    self.preview_recording_btn.setEnabled(enabled)
    trim_btn = getattr(self, "trim_recording_btn", None)
    if trim_btn is not None:
        trim_btn.setEnabled(enabled)
    self.send_to_splitter_btn.setEnabled(enabled)
    self._set_rounded_action_button_style(self.send_to_splitter_btn, busy=(not enabled))
    self.delete_recording_btn.setEnabled(bool(has_selected and not self.recording_active and not trim_busy))

def _on_recordings_selection_changed(self):
    self._update_recording_action_buttons()

def _refresh_recordings_list(self, select_path=""):
    if not hasattr(self, "recordings_list"):
        return

    desired = self._normalized_path(select_path) if select_path else self._selected_recording_path()
    try:
        recording_dir = self._recording_output_directory()
    except Exception:
        recording_dir = ""

    entries = []
    if recording_dir and os.path.isdir(recording_dir):
        try:
            for entry in os.scandir(recording_dir):
                if not entry.is_file():
                    continue
                if not self._is_recording_audio_file(entry.name):
                    continue
                stat = entry.stat()
                entries.append((stat.st_mtime, stat.st_size, os.path.abspath(entry.path)))
        except Exception:
            entries = []

    entries.sort(key=lambda row: row[0], reverse=True)

    self.recordings_list.blockSignals(True)
    self.recordings_list.clear()
    selected_row = -1
    for modified_at, size_bytes, path in entries:
        if size_bytes <= _MIN_SAVED_RECORDING_BYTES:
            continue
        visible_index = self.recordings_list.count()
        display_time = datetime.fromtimestamp(modified_at).strftime("%Y-%m-%d %H:%M")
        display_size_mb = float(size_bytes) / (1024.0 * 1024.0)
        item = QListWidgetItem(f"{os.path.basename(path)}   {display_time}   {display_size_mb:.1f} MB")
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.recordings_list.addItem(item)
        if desired and os.path.abspath(path) == desired:
            selected_row = visible_index

    if selected_row >= 0:
        self.recordings_list.setCurrentRow(selected_row)
    elif self.recordings_list.count() > 0:
        self.recordings_list.setCurrentRow(0)
    self.recordings_list.blockSignals(False)
    self.recordings_empty_label.setVisible(self.recordings_list.count() == 0)
    self._update_recording_action_buttons()

def _maybe_auto_stop_for_silence(self, now=None):
    if not self.recording_active or self.recording_auto_stop_triggered:
        return
    if now is None:
        now = time.monotonic()
    silence_seconds = max(0.0, now - self.level_last_active_seconds)
    if silence_seconds >= self.recording_auto_stop_silence_seconds:
        self.recording_auto_stop_triggered = True
        self.stop_recording(
            reason=f"Auto-stopped after {self.recording_auto_stop_silence_seconds:g}s of silence"
        )

def _populate_recording_inputs(self):
    self._stop_audio_level_meter()
    self._refresh_recording_help_text()
    self.recording_input_select.clear()
    self.recording_active_backend = ""
    loopback_probe_failed = False
    loopback_entry_count = 0
    process_capture_entry_count = 0
    selected_index = -1
    virtual_input_names = []
    virtual_output_names = []
    background_device_indexes = {}
    background_mode = self._recording_background_mode_enabled()
    windows_standard_mode = bool(sys.platform == "win32" and not background_mode)

    if hasattr(self, "blackhole_hint"):
        self.blackhole_hint.hide()

    if self._can_use_windows_loopback_backend():
        def _device_token(value):
            if value is None:
                return ""
            if isinstance(value, (bytes, bytearray)):
                try:
                    return bytes(value).decode("utf-8", errors="ignore").strip()
                except Exception:
                    return ""
            return str(value).strip()

        try:
            import soundcard as _sc  # type: ignore

            speakers = list(_sc.all_speakers())
        except Exception:
            speakers = []

        if speakers:
            default_idx = 0
            try:
                default_speaker = _sc.default_speaker()
            except Exception:
                default_speaker = None

            default_speaker_id = ""
            default_speaker_name = ""
            if default_speaker is not None:
                default_speaker_id = _device_token(getattr(default_speaker, "id", ""))
                default_speaker_name = _device_token(getattr(default_speaker, "name", ""))

            for idx, speaker in enumerate(speakers):
                speaker_name = _device_token(getattr(speaker, "name", "")) or f"Output {idx + 1}"
                speaker_id = _device_token(getattr(speaker, "id", ""))
                is_virtual_speaker = self._is_likely_virtual_audio_device_name(speaker_name)
                if is_virtual_speaker:
                    virtual_output_names.append(speaker_name)
                speaker_mode = self._virtual_audio_device_channel_mode(speaker_name) if is_virtual_speaker else ""
                if background_mode:
                    if not is_virtual_speaker:
                        continue
                    label = f"{speaker_name} (Cable Device)"
                else:
                    label = f"{speaker_name} (WASAPI loopback)"
                payload = {
                    "backend": "wasapi_loopback",
                    "speaker_name": speaker_name,
                    "speaker_id": speaker_id,
                }
                self.recording_input_select.addItem(label, payload)
                absolute_index = self.recording_input_select.count() - 1
                loopback_entry_count += 1
                if background_mode:
                    if speaker_mode == "stereo" and speaker_mode not in background_device_indexes:
                        background_device_indexes[speaker_mode] = absolute_index
                    if selected_index < 0:
                        selected_index = absolute_index
                elif default_speaker_id and speaker_id == default_speaker_id:
                    selected_index = absolute_index
                elif not default_speaker_id and default_speaker_name and speaker_name == default_speaker_name:
                    selected_index = absolute_index

        else:
            loopback_probe_failed = True

    if self._can_use_windows_process_capture_backend():
        try:
            capture_apps = mixsplitr_process_capture.list_running_process_capture_apps()
        except Exception:
            capture_apps = []
        if not background_mode:
            for entry in capture_apps:
                label = mixsplitr_process_capture.format_process_capture_label(entry)
                payload = {
                    "backend": "windows_process_capture",
                    "pid": int(entry.get("pid") or 0),
                    "process_name": str(entry.get("process_name") or "").strip(),
                    "exe_name": str(entry.get("exe_name") or "").strip(),
                    "window_title": str(entry.get("window_title") or "").strip(),
                    "label": label,
                }
                self.recording_input_select.addItem(label, payload)
                process_capture_entry_count += 1

    qt_device_count = 0
    qt_default_index = -1
    blackhole_index = -1
    blackhole_stereo_index = -1
    blackhole_multichannel_index = -1
    if QT_MEDIA_AVAILABLE:
        devices = list(QMediaDevices.audioInputs())
        default_device = QMediaDevices.defaultAudioInput()
        for idx, device in enumerate(devices):
            description = str(device.description() or f"Input {idx + 1}")
            is_virtual_input = sys.platform == "win32" and self._is_likely_virtual_audio_device_name(description)
            if background_mode:
                continue
            if is_virtual_input:
                channel_mode = self._virtual_audio_input_device_channel_mode(device, description)
                label = f"{description} (Cable Device)"
                virtual_input_names.append(description)
            elif windows_standard_mode:
                continue
            else:
                channel_mode = ""
                label = description if not process_capture_entry_count and not loopback_entry_count else f"{description} (Input)"
            if background_mode and not is_virtual_input:
                continue
            self.recording_input_select.addItem(label, device)
            absolute_index = self.recording_input_select.count() - 1
            qt_device_count += 1
            if is_virtual_input and channel_mode and channel_mode not in background_device_indexes:
                background_device_indexes[channel_mode] = absolute_index
            if device == default_device:
                qt_default_index = absolute_index
            if "blackhole" in description.lower() and blackhole_index < 0:
                blackhole_index = absolute_index
            if "blackhole" in description.lower():
                device_mode = self._virtual_audio_input_device_channel_mode(device, description)
                if device_mode == "stereo":
                    if blackhole_stereo_index < 0:
                        blackhole_stereo_index = absolute_index
                elif blackhole_multichannel_index < 0:
                    blackhole_multichannel_index = absolute_index

    self.recording_virtual_input_names = list(virtual_input_names)
    self.recording_virtual_output_names = list(virtual_output_names)
    self.recording_background_device_indexes = dict(background_device_indexes)

    if self.recording_input_select.count() <= 0:
        if background_mode:
            self.recording_input_select.addItem("No cable-style output found")
            self._set_recording_status("No cable-style output found yet", error=True)
            self._set_signal_status("waiting for cable output", color="#E45D5D")
        elif windows_standard_mode:
            self.recording_input_select.addItem("No app or system audio source found")
            self._set_recording_status("No app or system audio source found", error=True)
            self._set_signal_status("waiting for audio source", color="#E45D5D")
        elif QT_MEDIA_AVAILABLE:
            self.recording_input_select.addItem("No audio input or capture sources found")
            self._set_recording_status("No input or capture source available", error=True)
            self._set_signal_status("no input device", color="#E45D5D")
        else:
            self.recording_input_select.addItem("Recording unavailable")
            self._set_recording_status("Recording is unavailable in this build", error=True)
            self._set_signal_status("meter unavailable in this build", color="#E45D5D")
        self.record_btn.setEnabled(False)
        self.recordings_list.setEnabled(True)
        self._refresh_recordings_list(select_path=self.recording_last_file)
        self._refresh_recording_windows_capture_guidance()
        self._sync_recording_background_device_ui()
        self._sync_recording_background_monitor_ui()
        return

    if background_mode and "stereo" in background_device_indexes:
        self.recording_input_select.setCurrentIndex(int(background_device_indexes["stereo"]))
    elif background_mode and selected_index >= 0:
        self.recording_input_select.setCurrentIndex(selected_index)
    elif sys.platform == "darwin" and blackhole_stereo_index >= 0:
        self.recording_input_select.setCurrentIndex(blackhole_stereo_index)
    elif sys.platform == "darwin" and blackhole_multichannel_index >= 0:
        self.recording_input_select.setCurrentIndex(blackhole_multichannel_index)
    elif sys.platform == "darwin" and blackhole_index >= 0:
        self.recording_input_select.setCurrentIndex(blackhole_index)
    elif selected_index >= 0:
        self.recording_input_select.setCurrentIndex(selected_index)
    elif qt_default_index >= 0:
        self.recording_input_select.setCurrentIndex(qt_default_index)
    else:
        self.recording_input_select.setCurrentIndex(0)

    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()

    if hasattr(self, "blackhole_hint"):
        if sys.platform == "darwin" and blackhole_index < 0:
            self.blackhole_hint.show()
        else:
            self.blackhole_hint.hide()

    self.record_btn.setEnabled(True)
    self.recordings_list.setEnabled(True)
    self._refresh_recordings_list(select_path=self.recording_last_file)

    if background_mode and virtual_output_names:
        status_text = "Ready for background recording"
    elif background_mode:
        status_text = "Background recording needs a cable-style output"
    elif process_capture_entry_count and loopback_entry_count:
        status_text = "Ready. You can record one app or all system audio."
    elif process_capture_entry_count:
        status_text = "Ready. Start audio in the app you want, then record."
    elif loopback_entry_count:
        status_text = "Ready. This will record everything playing on this device."
    elif sys.platform == "win32" and (not self._can_use_windows_loopback_backend() or loopback_probe_failed):
        if loopback_probe_failed:
            status_text = "Ready, but no system-audio device was found"
        else:
            status_text = "Ready with basic input recording only"
    else:
        status_text = "Ready"
    self._set_recording_status(status_text, error=False)
    self._set_signal_status("idle (starts on record)", color="#C4C7C5")
    self._refresh_recording_windows_capture_guidance()

def _cleanup_recording_objects(self):
    self.recording_capture_session = None
    self.recording_audio_input = None
    self.recording_recorder = None
    thread = self.recording_loopback_thread
    if thread is not None and not thread.is_alive():
        self.recording_loopback_thread = None
    process_thread = self.recording_process_capture_thread
    if process_thread is not None and not process_thread.is_alive():
        self.recording_process_capture_thread = None
    if self.recording_active_backend != "wasapi_loopback":
        self.recording_loopback_stop_event = None
        self.recording_loopback_last_level = 0.0
        self.recording_monitor_output_name = ""
    if self.recording_active_backend != "windows_process_capture":
        self.recording_process_capture_stop_event = None
        self.recording_process_capture_proc = None
        self.recording_process_capture_last_level = 0.0

def _update_recording_timer_label(self):
    if self.recording_active and self.recording_elapsed.isValid():
        elapsed_seconds = self.recording_elapsed.elapsed() / 1000.0
        self.recording_timer_label.setText(self._format_elapsed(elapsed_seconds))
        self._maybe_auto_stop_for_duration(elapsed_seconds)

def _on_recorder_error(self, *args):
    error_text = ""
    if len(args) >= 2 and args[1]:
        error_text = str(args[1])
    elif self.recording_recorder is not None:
        try:
            error_text = str(self.recording_recorder.errorString())
        except Exception:
            error_text = ""

    _cleanup_invalid_recording_paths(
        [self.recording_current_file, *list(self.recording_segment_files or [])]
    )

    self.recording_active = False
    self._stop_recording_awake_prevention()
    self.recording_auto_stop_triggered = False
    self.recording_stop_reason = ""
    self.recording_timer.stop()
    self.recording_input_select.setEnabled(True)
    self.recordings_list.setEnabled(True)
    self.record_btn.setText("🔴 Start Recording")
    self._update_recording_action_buttons()
    self._set_recording_status(f"Recording failed: {error_text or 'unknown error'}", error=True)
    self.recording_active_backend = ""
    self.recording_segment_files = []
    self.recording_output_format = ""
    self.recording_loopback_stop_event = None
    self.recording_process_capture_stop_event = None
    self.recording_process_capture_proc = None
    self.recording_process_capture_error = ""
    self.recording_monitor_warning = ""
    self.recording_monitor_output_name = ""
    self._cleanup_recording_objects()
    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()
    self._restart_audio_level_meter()

def start_recording(self):
    if bool(getattr(self, "_recording_trim_busy", False)):
        self._set_recording_status("Wait for trim processing to finish before starting a new recording.", error=True)
        return
    if self.recording_active:
        return

    selected_device = self.recording_input_select.currentData()
    force_wav_recording = self._recording_force_wav_enabled()
    auto_force_wav_recording = False
    auto_force_wav_reason = ""

    output_dir = self._recording_output_directory()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_base = os.path.join(output_dir, f"MixSplitR_recording_{timestamp}")
    output_path = f"{output_base}.wav" if force_wav_recording else f"{output_base}.flac"

    try:
        self._cleanup_recording_objects()
        self.recording_current_file = ""
        self.recording_segment_files = []
        self.recording_output_format = ""
        self.recording_active_backend = ""
        self.recording_process_capture_error = ""
        self.recording_process_capture_proc = None
        self.recording_monitor_request_event = None
        self.recording_monitor_warning = ""
        self.recording_monitor_output_name = ""

        if self._is_windows_process_capture_selection(selected_device):
            ok, err = self._start_windows_process_capture_recording(
                output_base,
                selected_device,
                force_wav=force_wav_recording,
            )
            if not ok:
                raise RuntimeError(err or "Unable to start Windows app capture")
        elif self._is_windows_loopback_selection(selected_device):
            ok, err = self._start_windows_loopback_recording(
                output_base,
                selected_device,
                force_wav=force_wav_recording,
                monitor_requested=self._recording_background_monitor_enabled(),
            )
            if not ok:
                raise RuntimeError(err or "Unable to start WASAPI loopback recording")
        else:
            if not QT_MEDIA_AVAILABLE:
                raise RuntimeError("Recording is unavailable in this build")
            if selected_device is None:
                selected_device = QMediaDevices.defaultAudioInput()

            selected_device_desc = ""
            try:
                if selected_device is not None and hasattr(selected_device, "description"):
                    selected_device_desc = str(selected_device.description() or "").strip()
            except Exception:
                selected_device_desc = ""

            selected_device_channels = self._recording_device_channel_count(selected_device)
            if not force_wav_recording and selected_device_channels > 8:
                auto_force_wav_recording = True
                device_label = selected_device_desc or "selected input"
                auto_force_wav_reason = (
                    f"{device_label} exposes {selected_device_channels} channels, "
                    "so MixSplitR switched Recording Mode to WAV automatically."
                )

            self.recording_capture_session = QMediaCaptureSession()
            self.recording_audio_input = QAudioInput(selected_device)
            self.recording_recorder = QMediaRecorder()
            self.recording_capture_session.setAudioInput(self.recording_audio_input)
            self.recording_capture_session.setRecorder(self.recording_recorder)

            qt_record_ext = "wav"
            if QMediaFormat is not None and hasattr(self.recording_recorder, "setMediaFormat"):
                try:
                    file_enum = getattr(QMediaFormat, "FileFormat", None)
                    codec_enum = getattr(QMediaFormat, "AudioCodec", None)

                    def _enum_member(enum_obj, *candidates):
                        if enum_obj is None:
                            return None
                        lowered = {str(name).strip().lower() for name in candidates if str(name).strip()}
                        for candidate in candidates:
                            if hasattr(enum_obj, candidate):
                                return getattr(enum_obj, candidate)
                        for attr_name in dir(enum_obj):
                            if attr_name.lower() in lowered:
                                return getattr(enum_obj, attr_name)
                        return None

                    media_format = QMediaFormat()
                    if force_wav_recording or auto_force_wav_recording:
                        wav_file = _enum_member(file_enum, "Wave", "WAV", "WaveformAudio")
                        wav_codec = _enum_member(codec_enum, "Wave", "WAV", "Pcm", "PCM")
                        if wav_file is not None:
                            media_format.setFileFormat(wav_file)
                        if wav_codec is not None:
                            media_format.setAudioCodec(wav_codec)
                        qt_record_ext = "wav"
                    else:
                        flac_file = _enum_member(file_enum, "FLAC", "Flac")
                        flac_codec = _enum_member(codec_enum, "FLAC", "Flac")
                        if flac_file is not None:
                            media_format.setFileFormat(flac_file)
                        if flac_codec is not None:
                            media_format.setAudioCodec(flac_codec)
                        if flac_file is not None:
                            qt_record_ext = "flac"
                        else:
                            wav_file = _enum_member(file_enum, "Wave", "WAV", "WaveformAudio")
                            wav_codec = _enum_member(codec_enum, "Wave", "WAV", "Pcm", "PCM")
                            if wav_file is not None:
                                media_format.setFileFormat(wav_file)
                            if wav_codec is not None:
                                media_format.setAudioCodec(wav_codec)
                            qt_record_ext = "wav"
                    self.recording_recorder.setMediaFormat(media_format)
                except Exception:
                    qt_record_ext = "wav"

            output_path = f"{output_base}.{qt_record_ext}"
            self.recording_current_file = output_path
            self.recording_segment_files = [output_path]
            self.recording_output_format = qt_record_ext

            if hasattr(self.recording_recorder, "errorOccurred"):
                self.recording_recorder.errorOccurred.connect(self._on_recorder_error)

            self.recording_recorder.setOutputLocation(QUrl.fromLocalFile(output_path))
            self.recording_recorder.record()
            self.recording_active_backend = "qt"
    except Exception as exc:
        self._set_recording_status(f"Could not start recording: {exc}", error=True)
        self.recording_current_file = ""
        self.recording_segment_files = []
        self.recording_output_format = ""
        self.recording_active_backend = ""
        self.recording_loopback_stop_event = None
        self.recording_process_capture_stop_event = None
        self.recording_process_capture_proc = None
        self.recording_monitor_request_event = None
        self.recording_monitor_warning = ""
        self.recording_monitor_output_name = ""
        self._stop_recording_awake_prevention()
        self._cleanup_recording_objects()
        self._sync_recording_background_device_ui()
        self._sync_recording_background_monitor_ui()
        return

    self.recording_active = True
    self._start_recording_awake_prevention()
    self.recording_last_file = ""
    self.recording_auto_stop_triggered = False
    self.recording_stop_reason = ""
    self.recording_timer_label.setText("00:00:00")
    self.recording_elapsed.start()
    self.recording_timer.start()
    self.recording_input_select.setEnabled(False)
    self.recordings_list.setEnabled(False)
    self.record_btn.setText("⏹ Stop Recording")
    self._update_recording_action_buttons()
    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()
    status_target = self.recording_current_file or output_path
    status_text = f"Recording… {os.path.basename(status_target)}"
    if self._is_windows_process_capture_selection(selected_device):
        process_label = str((selected_device or {}).get("label") or "").strip()
        if process_label:
            status_text = f"Recording… {process_label}"
    elif self._is_windows_loopback_selection(selected_device) and self._recording_background_monitor_enabled():
        status_text = f"{status_text} (monitor on)"
    elif auto_force_wav_reason:
        status_text = f"{status_text} ({auto_force_wav_reason})"
    self._set_recording_status(status_text)
    self._set_signal_status("listening...", color="#C4C7C5")
    self._restart_audio_level_meter()

def _finalize_recording_stop(self):
    self._stop_recording_awake_prevention()
    if self.recording_active_backend == "wasapi_loopback":
        thread = self.recording_loopback_thread
        if thread is not None and thread.is_alive():
            if self.recording_finalize_attempts < 20:
                self.recording_finalize_attempts += 1
                QTimer.singleShot(120, self._finalize_recording_stop)
                return
            self._set_recording_status("Stopping loopback capture...", error=True)
    elif self.recording_active_backend == "windows_process_capture":
        thread = self.recording_process_capture_thread
        if thread is not None and thread.is_alive():
            if self.recording_finalize_attempts < 20:
                self.recording_finalize_attempts += 1
                QTimer.singleShot(120, self._finalize_recording_stop)
                return
            self._set_recording_status("Stopping app capture...", error=True)

    self.recording_input_select.setEnabled(True)
    self.recordings_list.setEnabled(True)
    self.record_btn.setText("🔴 Start Recording")
    self._cleanup_recording_objects()
    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()

    recorded_path = self.recording_current_file
    saved_segments = []
    candidate_segments = []
    seen_paths = set()
    for segment_path in list(self.recording_segment_files or []):
        normalized = os.path.abspath(str(segment_path or ""))
        if not normalized or normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        candidate_segments.append(normalized)
        if _is_saved_recording_path(normalized):
            saved_segments.append(normalized)

    if not saved_segments and recorded_path:
        normalized = os.path.abspath(str(recorded_path))
        candidate_segments.append(normalized)
        if _is_saved_recording_path(normalized):
            saved_segments.append(normalized)

    _cleanup_invalid_recording_paths(candidate_segments)

    if saved_segments:
        primary_path = saved_segments[0]
        self.recording_current_file = ""
        self.recording_last_file = primary_path
        self._refresh_recordings_list(select_path=primary_path)
        status = f"Saved: {os.path.basename(primary_path)}"
        if len(saved_segments) > 1:
            status = f"{status} (+{len(saved_segments) - 1} split files)"
        if self.recording_loopback_error:
            status = f"{status} (loopback warning: {self.recording_loopback_error})"
        if self.recording_process_capture_error:
            status = f"{status} (app capture warning: {self.recording_process_capture_error})"
        if self.recording_monitor_warning:
            status = f"{status} (monitor warning: {self.recording_monitor_warning})"
        if self.recording_stop_reason:
            status = f"{status} ({self.recording_stop_reason})"
        self._set_recording_status(status)
        self.recording_stop_reason = ""
        self.recording_auto_stop_triggered = False
        self.recording_active_backend = ""
        self.recording_loopback_error = ""
        self.recording_loopback_stop_event = None
        self.recording_loopback_thread = None
        self.recording_process_capture_error = ""
        self.recording_process_capture_stop_event = None
        self.recording_process_capture_proc = None
        self.recording_process_capture_thread = None
        self.recording_monitor_request_event = None
        self.recording_monitor_warning = ""
        self.recording_monitor_output_name = ""
        self.recording_segment_files = []
        self.recording_output_format = ""
        self._restart_audio_level_meter()
        return

    if self.recording_finalize_attempts < 8:
        self.recording_finalize_attempts += 1
        QTimer.singleShot(250, self._finalize_recording_stop)
        return

    self.recording_current_file = ""
    self.recording_last_file = ""
    loopback_error = str(self.recording_loopback_error or "").strip()
    process_capture_error = str(self.recording_process_capture_error or "").strip()
    self.recording_stop_reason = ""
    self.recording_auto_stop_triggered = False
    self.recording_active_backend = ""
    self.recording_loopback_error = ""
    self.recording_loopback_stop_event = None
    self.recording_loopback_thread = None
    self.recording_process_capture_error = ""
    self.recording_process_capture_stop_event = None
    self.recording_process_capture_proc = None
    self.recording_process_capture_thread = None
    self.recording_monitor_request_event = None
    self.recording_monitor_warning = ""
    self.recording_monitor_output_name = ""
    self.recording_segment_files = []
    self.recording_output_format = ""
    self._refresh_recordings_list()
    if loopback_error:
        self._set_recording_status(f"No recording was saved ({loopback_error})", error=True)
    elif process_capture_error:
        self._set_recording_status(f"No recording was saved ({process_capture_error})", error=True)
    else:
        self._set_recording_status("No recording was saved", error=True)
    self._sync_recording_background_device_ui()
    self._sync_recording_background_monitor_ui()
    self._restart_audio_level_meter()

def stop_recording(self, reason=""):
    if not self.recording_active:
        return

    self.recording_active = False
    self._stop_recording_awake_prevention()
    self.recording_stop_reason = str(reason or "").strip()
    self.recording_timer.stop()
    if self.recording_active_backend == "wasapi_loopback":
        monitor_event = getattr(self, "recording_monitor_request_event", None)
        if monitor_event is not None:
            try:
                monitor_event.clear()
            except Exception:
                pass
        if self.recording_loopback_stop_event is not None:
            try:
                self.recording_loopback_stop_event.set()
            except Exception:
                pass
    elif self.recording_active_backend == "windows_process_capture":
        if self.recording_process_capture_stop_event is not None:
            try:
                self.recording_process_capture_stop_event.set()
            except Exception:
                pass
        proc = self.recording_process_capture_proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
    elif self.recording_recorder is not None:
        try:
            self.recording_recorder.stop()
        except Exception:
            pass
    self.recording_finalize_attempts = 0
    QTimer.singleShot(250, self._finalize_recording_stop)

def toggle_recording(self):
    if self.recording_active:
        self.stop_recording()
    else:
        self.start_recording()

def preview_recording_file(self):
    if bool(getattr(self, "_recording_trim_busy", False)):
        self._set_recording_status("Wait for trim processing to finish before previewing.", error=True)
        return
    if self.recording_active:
        self._set_recording_status("Stop recording before previewing", error=True)
        return
    preview_path = self._current_recording_target_file()
    if not preview_path or not os.path.exists(preview_path):
        self._set_recording_status("No recording available to preview", error=True)
        return
    opened = False
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-a", "QuickTime Player", preview_path])
            opened = True
        except Exception:
            opened = False
    if not opened:
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(preview_path))

    if opened:
        self._set_recording_status(f"Opened preview: {os.path.basename(preview_path)}")
    else:
        self._set_recording_status("Could not open preview player", error=True)

def trim_selected_recording(self):
    if bool(getattr(self, "_recording_trim_busy", False)):
        return
    if self.recording_active:
        self._set_recording_status("Stop recording before trimming", error=True)
        return

    source_path = self._current_recording_target_file()
    if not source_path or not os.path.exists(source_path):
        self._set_recording_status("No recording available to trim", error=True)
        return

    previous_record_btn_enabled = bool(self.record_btn.isEnabled()) if hasattr(self, "record_btn") else True
    previous_input_enabled = bool(self.recording_input_select.isEnabled()) if hasattr(self, "recording_input_select") else True
    previous_list_enabled = bool(self.recordings_list.isEnabled()) if hasattr(self, "recordings_list") else True
    self._recording_trim_busy = True
    if hasattr(self, "record_btn"):
        self.record_btn.setEnabled(False)
    if hasattr(self, "recording_input_select"):
        self.recording_input_select.setEnabled(False)
    if hasattr(self, "recordings_list"):
        self.recordings_list.setEnabled(False)
    self._update_recording_action_buttons()

    load_thread = None
    try:
        result_payload = {
            "loaded": False,
            "error": "",
            "peaks": [],
            "duration": 0.0,
        }

        load_thread = NativeWaveformLoadThread(source_path, assisted_mode=False)

        def _handle_loaded(peaks, duration_seconds, _assisted_points):
            result_payload["loaded"] = True
            result_payload["peaks"] = list(peaks or [])
            result_payload["duration"] = float(duration_seconds or 0.0)

        def _handle_failed(message):
            result_payload["error"] = str(message or "").strip()

        def _handle_progress(percent, detail):
            try:
                pct = int(round(float(percent or 0.0)))
            except Exception:
                pct = 0
            suffix = f" - {str(detail or '').strip()}" if str(detail or "").strip() else ""
            self._set_recording_status(f"Preparing trim editor ({pct}%)...{suffix}")

        load_thread.loaded.connect(_handle_loaded)
        load_thread.failed.connect(_handle_failed)
        load_thread.progress.connect(_handle_progress)
        self._set_recording_status(f"Preparing trim editor... {os.path.basename(source_path)}")
        load_thread.start()
        while load_thread.isRunning():
            QApplication.processEvents()
            load_thread.wait(30)
        QApplication.processEvents()

        if not result_payload["loaded"]:
            if bool(getattr(load_thread, "result_loaded", False)):
                _handle_loaded(
                    getattr(load_thread, "result_peaks", []),
                    getattr(load_thread, "result_duration_seconds", 0.0),
                    getattr(load_thread, "result_assisted_points", []),
                )
            else:
                thread_error = str(getattr(load_thread, "result_error", "")).strip()
                if thread_error and not result_payload["error"]:
                    result_payload["error"] = thread_error

        if not result_payload["loaded"]:
            self._set_recording_status(
                f"Could not prepare trim editor: {result_payload['error'] or 'waveform load failed'}",
                error=True,
            )
            return

        dialog = NativeWaveformEditorDialog(
            self,
            source_path,
            result_payload["peaks"],
            result_payload["duration"],
            editor_mode="trim",
        )
        if dialog.exec() != QDialog.Accepted:
            self._set_recording_status("Trim cancelled")
            return

        trim_start = max(0.0, float(getattr(dialog, "trim_start_seconds", 0.0) or 0.0))
        trim_end = max(trim_start, float(getattr(dialog, "trim_end_seconds", result_payload["duration"]) or result_payload["duration"]))
        duration = max(0.0, float(result_payload["duration"] or 0.0))
        if duration > 0.0 and trim_start <= 0.0005 and trim_end >= (duration - 0.0005):
            self._set_recording_status("Trim skipped: the full recording range is still selected.")
            return

        output_path = self._build_trimmed_recording_output_path(source_path)
        self._set_recording_status(f"Trimming recording... {os.path.basename(output_path)}")
        try:
            self._trim_recording_audio_file(source_path, output_path, trim_start, trim_end)
        except Exception:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass
            raise

        self.recording_last_file = output_path
        self._refresh_recordings_list(select_path=output_path)
        self._set_recording_status(f"Trimmed copy saved: {os.path.basename(output_path)}")
    except Exception as exc:
        self._set_recording_status(f"Trim failed: {exc}", error=True)
    finally:
        if load_thread is not None:
            try:
                load_thread.deleteLater()
            except Exception:
                pass
        self._recording_trim_busy = False
        if hasattr(self, "record_btn"):
            self.record_btn.setEnabled(previous_record_btn_enabled)
        if hasattr(self, "recording_input_select"):
            self.recording_input_select.setEnabled(previous_input_enabled)
        if hasattr(self, "recordings_list"):
            self.recordings_list.setEnabled(previous_list_enabled)
        self._update_recording_action_buttons()

def delete_selected_recording(self):
    if bool(getattr(self, "_recording_trim_busy", False)):
        self._set_recording_status("Wait for trim processing to finish before deleting.", error=True)
        return
    if self.recording_active:
        self._set_recording_status("Stop recording before deleting", error=True)
        return

    target_path = self._selected_recording_path()
    if not target_path:
        self._set_recording_status("Select a recording to delete", error=True)
        return
    if not os.path.exists(target_path):
        self._refresh_recordings_list()
        self._set_recording_status("Selected recording no longer exists", error=True)
        return

    file_name = os.path.basename(target_path)
    confirm = QMessageBox.question(
        self,
        "Delete Recording",
        f"Delete '{file_name}'?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if confirm != QMessageBox.Yes:
        return

    try:
        os.remove(target_path)
    except Exception as exc:
        self._set_recording_status(f"Delete failed: {exc}", error=True)
        return

    if self.recording_last_file and os.path.abspath(self.recording_last_file) == os.path.abspath(target_path):
        self.recording_last_file = ""
    self._refresh_recordings_list(select_path=self.recording_last_file)
    self._set_recording_status(f"Deleted: {file_name}")

def send_recording_to_splitter(self):
    if bool(getattr(self, "_recording_trim_busy", False)):
        self._set_recording_status("Wait for trim processing to finish before sending to Splitter.", error=True)
        return
    if self.recording_active:
        self._set_recording_status("Stop recording before sending", error=True)
        return
    send_path = self._current_recording_target_file()
    if not send_path or not os.path.exists(send_path):
        self._set_recording_status("No recording available to send", error=True)
        return

    self.drop_zone.file_paths = [send_path]
    self.drop_zone.label.setText("1 file(s) ready for processing")
    self.switch_page(0)
    self.status_label.setText(f"Status: Loaded recording: {os.path.basename(send_path)}")
    self._set_recording_status("Sent to Audio Splitter")

def _populate_cd_drives(self):
    """Refresh the drive combo with currently detected CD sources."""
    self.cd_drive_combo.clear()
    drives = []
    if mixsplitr_cdrip:
        try:
            drives = mixsplitr_cdrip.list_cd_drives()
        except Exception:
            drives = []
    if drives:
        for drive in drives:
            self.cd_drive_combo.addItem(drive["label"], userData=drive["path"])
    else:
        self.cd_drive_combo.addItem("No drives detected — use Manual Path below", userData="")
    self.cd_rip_start_btn.setEnabled(bool(drives) or True)  # always allow; validation happens on start
    self._set_cd_rip_start_button_style(busy=(not self.cd_rip_start_btn.isEnabled()))

def _cd_rip_set_status(self, text, error=False):
    color = "#E05C5C" if error else "#C4C7C5"
    self.cd_rip_status_label.setStyleSheet(f"color: {color};")
    self.cd_rip_status_label.setText(f"Status: {text}")

def _format_cd_rip_failure_line(self, failure):
    index = int((failure or {}).get("index") or 0)
    title = str((failure or {}).get("title") or "").strip()
    reason = str((failure or {}).get("reason") or "").strip()
    source_path = str((failure or {}).get("source_path") or "").strip()
    source_name = os.path.basename(source_path)
    track_label = title or source_name or (f"Track {index:02d}" if index > 0 else "Unknown track")
    if source_name and source_name != track_label:
        track_label = f"{track_label} ({source_name})"
    prefix = f"{index:02d}. " if index > 0 else ""
    return f"{prefix}{track_label} - {reason or 'Rip failed'}"

def _set_cd_rip_failures(self, failures):
    normalized = []
    seen = set()
    for failure in list(failures or []):
        if not isinstance(failure, dict):
            continue
        entry = {
            "index": int(failure.get("index") or 0),
            "title": str(failure.get("title") or "").strip(),
            "source_path": str(failure.get("source_path") or "").strip(),
            "reason": str(failure.get("reason") or "").strip(),
        }
        key = (
            entry["index"],
            entry["title"],
            entry["source_path"],
            entry["reason"],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)

    self.cd_rip_failures = normalized
    if not normalized:
        self.cd_rip_failed_label.hide()
        self.cd_rip_failed_output.hide()
        self.cd_rip_failed_output.clear()
        return

    self.cd_rip_failed_label.setText(f"Failed Tracks ({len(normalized)})")
    self.cd_rip_failed_output.setPlainText("\n".join(self._format_cd_rip_failure_line(item) for item in normalized))
    self.cd_rip_failed_output.verticalScrollBar().setValue(0)
    self.cd_rip_failed_label.show()
    self.cd_rip_failed_output.show()

def _reset_cd_rip_timeout_state(self, reset_auto_skip=False):
    self._cd_rip_skip_current_track_requested = False
    self._cd_rip_timeout_track_index = 0
    self._cd_rip_timeout_track_title = ""
    if reset_auto_skip:
        self._cd_rip_auto_skip_stuck_tracks = False
    actions_widget = getattr(self, "cd_rip_skip_actions_widget", None)
    if actions_widget is not None:
        actions_widget.hide()
    skip_btn = getattr(self, "cd_rip_skip_track_btn", None)
    auto_btn = getattr(self, "cd_rip_skip_and_auto_btn", None)
    if skip_btn is not None:
        skip_btn.setEnabled(True)
        skip_btn.setText("Skip Stuck Track")
        skip_btn.setToolTip("")
    if auto_btn is not None:
        auto_btn.setEnabled(True)
        auto_btn.setText("Skip + Auto-Skip Future Stuck Tracks")
        auto_btn.setToolTip("")

def _show_cd_rip_skip_prompt(self, track_index, total, title):
    self._cd_rip_skip_current_track_requested = False
    self._cd_rip_timeout_track_index = int(track_index or 0)
    self._cd_rip_timeout_track_title = str(title or "").strip()
    label = self._cd_rip_timeout_track_title or f"Track {self._cd_rip_timeout_track_index:02d}"
    self.cd_rip_track_label.setText(f"Track {track_index}/{total}: {label} (still trying...)")
    skip_btn = getattr(self, "cd_rip_skip_track_btn", None)
    auto_btn = getattr(self, "cd_rip_skip_and_auto_btn", None)
    actions_widget = getattr(self, "cd_rip_skip_actions_widget", None)
    if skip_btn is None or auto_btn is None or actions_widget is None:
        return
    if self._cd_rip_auto_skip_stuck_tracks:
        self._request_cd_rip_skip_current_track(enable_auto_skip=True, automatic=True)
        return
    skip_btn.setText("Skip Stuck Track")
    skip_btn.setToolTip(f"Skip {label} and continue with the next track")
    auto_btn.setText("Skip + Auto-Skip Future Stuck Tracks")
    auto_btn.setToolTip(f"Skip {label} now and auto-skip future stuck tracks")
    skip_btn.setEnabled(True)
    auto_btn.setEnabled(True)
    actions_widget.show()
    self._cd_rip_set_status("Track is taking longer than expected. Skip once, enable auto-skip, or keep waiting.")

def _request_cd_rip_skip_current_track(self, enable_auto_skip=False, automatic=False):
    if not self.cd_ripping_active or self._cd_rip_timeout_track_index <= 0:
        return
    if enable_auto_skip:
        self._cd_rip_auto_skip_stuck_tracks = True
    self._cd_rip_skip_current_track_requested = True
    label = self._cd_rip_timeout_track_title or f"Track {self._cd_rip_timeout_track_index:02d}"
    skip_btn = getattr(self, "cd_rip_skip_track_btn", None)
    auto_btn = getattr(self, "cd_rip_skip_and_auto_btn", None)
    actions_widget = getattr(self, "cd_rip_skip_actions_widget", None)
    if skip_btn is not None:
        skip_btn.setEnabled(False)
        skip_btn.setText("Skipping...")
    if auto_btn is not None:
        auto_btn.setEnabled(False)
        auto_btn.setText("Auto-Skip Enabled" if enable_auto_skip else "Skip + Auto-Skip Future Stuck Tracks")
    if actions_widget is not None:
        actions_widget.hide()
    if enable_auto_skip:
        if automatic:
            self._cd_rip_set_status(f"Auto-skip is enabled. Skipping {label} and continuing automatically...")
        else:
            self._cd_rip_set_status(f"Auto-skip enabled. Skipping {label} and future stuck tracks automatically...")
    else:
        self._cd_rip_set_status(f"Skipping {label} and moving to the next track...")

def _checked_cd_rip_output_paths(self):
    paths = []
    seen = set()
    if not hasattr(self, "cd_ripped_list"):
        return paths
    for row in range(self.cd_ripped_list.count()):
        item = self.cd_ripped_list.item(row)
        if item is None or item.checkState() != Qt.Checked:
            continue
        path = os.path.abspath(str(item.data(Qt.UserRole) or "").strip())
        if not path or path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        paths.append(path)
    return paths

def _update_cd_rip_send_buttons(self):
    enabled = bool(self._checked_cd_rip_output_paths()) and not bool(self.cd_ripping_active)
    for button in (
        getattr(self, "cd_rip_send_to_identifier_btn", None),
        getattr(self, "cd_rip_send_to_editor_btn", None),
    ):
        if button is None:
            continue
        button.setEnabled(enabled)
        self._set_rounded_action_button_style(button, busy=(not enabled))

def _start_cd_rip(self):
    if self.cd_ripping_active:
        # Already ripping — treat as a cancel request.
        self._cd_rip_cancel_requested = True
        self._cd_rip_set_status("Cancelling after current track…")
        self.cd_rip_start_btn.setEnabled(False)
        self._set_cd_rip_start_button_style(busy=True)
        return
    if mixsplitr_cdrip is None:
        self._cd_rip_set_status("CD ripping module unavailable.", error=True)
        return

    # Resolve source: manual path takes priority over combo
    manual = self.cd_manual_path_input.text().strip()
    if manual:
        source = manual
    else:
        source = self.cd_drive_combo.currentData() or ""
    if not source:
        self._cd_rip_set_status("No drive or path selected. Use Detect Drives or enter a Manual Path.", error=True)
        return

    config = self._read_config_from_disk()
    output_dir = str(config.get("cd_rip_output_directory", "") or self._default_cd_output_directory())
    fmt = str(config.get("cd_rip_format", "flac")).strip().lower()
    auto_meta = bool(config.get("cd_rip_auto_metadata", True))
    eject = bool(config.get("cd_rip_eject_when_done", False))
    import time as _time
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    rip_folder = os.path.join(output_dir, f"CD_Rip_{stamp}")

    # Reset UI
    self._cd_rip_cancel_requested = False
    self._cd_rip_auto_skip_stuck_tracks = False
    self.cd_rip_output_files = []
    self._reset_cd_rip_timeout_state(reset_auto_skip=True)
    self._set_cd_rip_failures([])
    self.cd_ripped_list.blockSignals(True)
    self.cd_ripped_list.clear()
    self.cd_ripped_list.blockSignals(False)
    self.cd_rip_empty_label.show()
    self.cd_rip_progress_bar.setValue(0)
    self.cd_rip_progress_bar.show()
    self.cd_rip_track_label.show()
    self._update_cd_rip_send_buttons()
    self.cd_rip_start_btn.setText("Cancel Ripping")
    self.cd_rip_start_btn.setEnabled(True)
    self._set_cd_rip_start_button_style(busy=False)
    self._cd_rip_events.clear()
    self._cd_rip_set_status("Starting rip…")
    self.cd_ripping_active = True
    self._cd_rip_poll_timer.start()

    def _progress(done, total, label):
        pct = int(done / max(total, 1) * 100)
        self._cd_rip_events.append(("progress", done, total, pct, label))

    def _on_timeout(done, total, label):
        self._cd_rip_events.append(("timeout", done, total, label))

    def _on_failures(failures):
        self._cd_rip_events.append(("failures", failures))

    def _run():
        try:
            result = mixsplitr_cdrip.rip_disc_to_folder(
                source=source,
                output_folder=rip_folder,
                output_format=fmt,
                auto_metadata=auto_meta,
                eject_when_done=eject,
                progress_callback=_progress,
                cancel_flag=lambda: self._cd_rip_cancel_requested,
                timeout_strategy="manual_skip",
                timeout_callback=_on_timeout,
                skip_track_flag=lambda _idx, _title: bool(self._cd_rip_skip_current_track_requested),
                failure_callback=_on_failures,
            )
            self._cd_rip_events.append(("done", result, None))
        except Exception as exc:
            self._cd_rip_events.append(("done", [], str(exc)))

    self.cd_rip_thread = threading.Thread(target=_run, daemon=True)
    self.cd_rip_thread.start()

def _poll_cd_rip_progress(self):
    """Called every 200 ms from the main thread to drain the event queue."""
    events = list(self._cd_rip_events)
    self._cd_rip_events.clear()
    for event in events:
        kind = event[0]
        if kind == "progress":
            _, done, total, pct, label = event
            label_text = str(label or "").strip()
            lower_label = label_text.lower()
            if (
                self._cd_rip_timeout_track_index
                and (
                    int(done or 0) != int(self._cd_rip_timeout_track_index)
                    or "skipped" in lower_label
                    or "failed" in lower_label
                )
            ):
                self._reset_cd_rip_timeout_state()
            self.cd_rip_progress_bar.setValue(pct)
            self.cd_rip_track_label.setText(f"Track {done}/{total}: {label_text}")
            if "skipped" in lower_label or "failed" in lower_label:
                self._cd_rip_set_status(label_text, error=("failed" in lower_label))
            else:
                self._cd_rip_set_status(f"Ripping track {done} of {total}…")
        elif kind == "timeout":
            _, done, total, label = event
            self._show_cd_rip_skip_prompt(done, total, label)
        elif kind == "failures":
            _, failures = event
            self._set_cd_rip_failures(failures)
        elif kind == "done":
            _, output_files, error = event
            self._on_cd_rip_finished(output_files, error)
            return

def _on_cd_rip_finished(self, output_files, error):
    self._cd_rip_poll_timer.stop()
    was_cancelled = self._cd_rip_cancel_requested
    self.cd_ripping_active = False
    self._cd_rip_cancel_requested = False
    self._reset_cd_rip_timeout_state(reset_auto_skip=True)
    self.cd_rip_start_btn.setText("Start Ripping")
    self.cd_rip_start_btn.setEnabled(True)
    self._set_cd_rip_start_button_style(busy=False)

    failure_count = len(self.cd_rip_failures)
    if error and not was_cancelled:
        summary = f"Rip failed. {failure_count} track(s) failed." if failure_count else "Rip failed."
        detail = str(error or "").strip()
        if detail and detail != "Unable to rip tracks from selected source.":
            summary = f"{summary} {detail}"
        self._cd_rip_set_status(summary, error=True)
        self.cd_rip_progress_bar.hide()
        self.cd_rip_track_label.hide()
        self._update_cd_rip_send_buttons()
        return

    self.cd_rip_output_files = list(output_files or [])
    self.cd_rip_progress_bar.setValue(100)
    self.cd_rip_track_label.hide()
    n = len(self.cd_rip_output_files)
    if was_cancelled:
        status_text = f"Cancelled. {n} track(s) ripped before stopping."
        if failure_count:
            status_text = f"{status_text} {failure_count} failed."
        self._cd_rip_set_status(status_text, error=bool(failure_count))
    else:
        status_text = f"Done. {n} track(s) ripped."
        if failure_count:
            status_text = f"{status_text} {failure_count} failed."
        self._cd_rip_set_status(status_text, error=bool(failure_count))

    self.cd_ripped_list.blockSignals(True)
    self.cd_ripped_list.clear()
    for path in self.cd_rip_output_files:
        item = QListWidgetItem(os.path.basename(path))
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        item.setFlags((item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled) & ~Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked)
        self.cd_ripped_list.addItem(item)
    self.cd_ripped_list.blockSignals(False)
    if self.cd_rip_output_files:
        self.cd_rip_empty_label.hide()
    else:
        self.cd_rip_empty_label.show()
    self._update_cd_rip_send_buttons()

def _on_cd_ripped_selection_changed(self, *_args):
    self._update_cd_rip_send_buttons()

def _cd_rip_send_to_identifier(self):
    paths = self._checked_cd_rip_output_paths()
    if not paths:
        self._cd_rip_set_status("Check at least one ripped file to send to Identifier.", error=True)
        return
    n = len(paths)
    self.pending_splitter_identifier_workflow = None
    self.ident_drop_zone.file_paths = list(paths)
    self.ident_drop_zone.label.setText(f"{n} file(s) ready to identify")
    self.ident_progress_bar.setValue(0)
    self.ident_stage_label.setVisible(False)
    self.ident_status_label.setText(f"Status: Loaded {n} ripped track(s) from CD")
    self._set_ident_run_button_busy(False)
    self.switch_page(getattr(self, "identification_page_index", 1))
    self._cd_rip_set_status(f"Sent {n} track(s) to Identifier")

def _cd_rip_send_to_editor(self):
    paths = self._checked_cd_rip_output_paths()
    if not paths:
        self._cd_rip_set_status("Check at least one ripped file to send to Editor.", error=True)
        return
    loaded = int(self._load_track_editor_input_paths(paths) or 0)
    if loaded <= 0:
        return
    self.switch_page(getattr(self, "track_editor_page_index", 0))
    self._cd_rip_set_status(f"Sent {loaded} track(s) to Editor")
