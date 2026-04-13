import sys
import os
import json
import html
import queue
import threading
import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
import audioop
import time
import struct
import math
import subprocess
import shutil
import base64
import copy
import array
import warnings
import tempfile
import shlex
from datetime import datetime
from ctypes import c_void_p
import importlib

try:
    import psutil
except Exception:
    psutil = None

MACOS_VIBRANCY_AVAILABLE = False
ENABLE_NATIVE_VIBRANCY = False
ENABLE_SIDEBAR_VIBRANCY = False
MACOS_VIBRANCY_IMPORT_ERROR = None
objc = None
NSVisualEffectView = None
NSVisualEffectMaterialSidebar = None
NSVisualEffectBlendingModeBehindWindow = None
NSVisualEffectStateActive = None
NSViewWidthSizable = None
NSViewHeightSizable = None
NSWindowBelow = None
NSWindowStyleMaskFullSizeContentView = None
NSWindowTitleHidden = None
NSWindowTitlebarSeparatorStyleNone = None
NSColor = None
if sys.platform == "darwin":
    try:
        import objc as _objc
        import AppKit as _AppKit
        from AppKit import (
            NSVisualEffectView as _NSVisualEffectView,
            NSVisualEffectMaterialSidebar as _NSVisualEffectMaterialSidebar,
            NSVisualEffectBlendingModeBehindWindow as _NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectStateActive as _NSVisualEffectStateActive,
            NSViewWidthSizable as _NSViewWidthSizable,
            NSViewHeightSizable as _NSViewHeightSizable,
            NSWindowBelow as _NSWindowBelow,
        )
        objc = _objc
        NSVisualEffectView = _NSVisualEffectView
        NSVisualEffectMaterialSidebar = _NSVisualEffectMaterialSidebar
        NSVisualEffectBlendingModeBehindWindow = _NSVisualEffectBlendingModeBehindWindow
        NSVisualEffectStateActive = _NSVisualEffectStateActive
        NSViewWidthSizable = _NSViewWidthSizable
        NSViewHeightSizable = _NSViewHeightSizable
        NSWindowBelow = _NSWindowBelow
        # Optional symbols vary by macOS/AppKit version; resolve defensively
        # so missing titlebar constants do not disable vibrancy entirely.
        NSWindowStyleMaskFullSizeContentView = getattr(
            _AppKit, "NSWindowStyleMaskFullSizeContentView", None
        )
        NSWindowTitleHidden = getattr(_AppKit, "NSWindowTitleHidden", None)
        NSWindowTitlebarSeparatorStyleNone = getattr(
            _AppKit, "NSWindowTitlebarSeparatorStyleNone", None
        )
        NSColor = getattr(_AppKit, "NSColor", None)
        MACOS_VIBRANCY_AVAILABLE = True
    except Exception as exc:
        MACOS_VIBRANCY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        MACOS_VIBRANCY_AVAILABLE = False

# --- Backend Imports ---
# Core and lightweight modules are loaded eagerly.
# Heavier processing stacks are loaded lazily to reduce UI startup cost.
mixsplitr_core = None
mixsplitr_processing = None
mixsplitr_autotracklist = None
mixsplitr_memory = None
mixsplitr_tagging = None
mixsplitr_identify = None
mixsplitr_editor = None
mixsplitr_pipeline = None
mixsplitr_artist_normalization = None
mixsplitr_manifest = None
mixsplitr_session = None
_BACKEND_IMPORT_ERRORS = {}
_BUNDLED_ROBOTO_BOLD_FAMILY = ""
VB_CABLE_DOWNLOAD_URL = "https://vb-audio.com/Cable/"
BLACKHOLE_DOWNLOAD_URL = "https://existential.audio/blackhole/"
BLACKHOLE_BREW_INSTALL_ACTION_URL = "mixsplitr://install-blackhole-2ch"


def _lazy_import_backend(attr_name, module_name=None):
    """Import backend module on demand and cache the module object."""
    cached = globals().get(attr_name)
    if cached is not None:
        return cached
    target = str(module_name or attr_name).strip()
    try:
        module = importlib.import_module(target)
        globals()[attr_name] = module
        return module
    except Exception as exc:
        _BACKEND_IMPORT_ERRORS[attr_name] = exc
        globals()[attr_name] = None
        return None


def _ensure_processing_backends():
    """Load heavy runtime backends needed for processing paths."""
    _lazy_import_backend("mixsplitr_identify")
    _lazy_import_backend("mixsplitr_processing")
    _lazy_import_backend("mixsplitr_autotracklist")
    _lazy_import_backend("mixsplitr_memory")
    _lazy_import_backend("mixsplitr_tagging")
    _lazy_import_backend("mixsplitr_artist_normalization")
    _lazy_import_backend("mixsplitr_pipeline")
    return bool(mixsplitr_processing is not None and mixsplitr_tagging is not None)


try:
    import mixsplitr_core
except ImportError:
    print("Warning: Processing modules not found. Ensure this script is in your MixSplitR source folder.")

try:
    import mixsplitr_memory
except ImportError:
    mixsplitr_memory = None

try:
    import mixsplitr_tagging
except ImportError:
    mixsplitr_tagging = None

try:
    import mixsplitr_editor
except ImportError:
    mixsplitr_editor = None

try:
    import mixsplitr_process_capture
except ImportError:
    mixsplitr_process_capture = None

try:
    import mixsplitr_pipeline
except ImportError:
    mixsplitr_pipeline = None

try:
    import mixsplitr_artist_normalization
except ImportError:
    mixsplitr_artist_normalization = None

try:
    import mixsplitr_manifest
except ImportError:
    mixsplitr_manifest = None

try:
    import mixsplitr_essentia
except ImportError:
    mixsplitr_essentia = None

try:
    import mixsplitr_session
except ImportError:
    mixsplitr_session = None

try:
    import mixsplitr_cdrip
except ImportError:
    mixsplitr_cdrip = None

try:
    from acrcloud.recognizer import ACRCloudRecognizer
except ImportError:
    ACRCloudRecognizer = None

try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
except ImportError:
    AudioSegment = None
    split_on_silence = None

from mixsplitr_ui_workers import (
    PreviewExportThread,
    ApiValidationThread,
    UpdateCheckThread,
    SessionEditorLoaderThread,
)
import mixsplitr_ui_settings as ui_settings
from mixsplitr_ui_waveform import (
    NativeWaveformLoadThread,
    NativeWaveformEditorDialog,
    AudioPreviewThread,
    configure_waveform_runtime,
)
from mixsplitr_ui_inputs import (
    _NoScrollSpinBox, _NoScrollDoubleSpinBox, _NoScrollComboBox,
    _ContainedScrollListWidget, _ChevronComboBox,
)
from mixsplitr_ui_effects import (
    RippleNavButton, FloatingOverlayScrollBar, TopFadeMaskEffect,
    SmoothScrollArea, PageRevealOverlay, TitlebarOverlay, TitlebarFadeOverlay,
)
from mixsplitr_ui_cards import GlossyCardFrame, GlassPage
from mixsplitr_ui_browser import (
    DragDropArea,
    _AudioPathBrowserDialog,
    choose_existing_directory,
)
from mixsplitr_ui_threads import ProcessingCancelledError, TimestampSeedLoadThread, ProcessingThread
from mixsplitr_ui_editor import TrackEditorPanel, DebugReadoutDialog
from mixsplitr_app_history import install_history_methods
from mixsplitr_app_editor_help import install_editor_help_methods
from mixsplitr_app_settings_ui import install_settings_ui_methods
from mixsplitr_app_recording_cd import install_recording_cd_methods
from mixsplitr_devtools import DeveloperInspectorController, annotate_widget, annotate_widget_owner

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
                               QGraphicsDropShadowEffect, QComboBox, QFormLayout, 
                               QSpinBox, QDoubleSpinBox, QFileDialog, QStackedWidget, QLineEdit,
                               QCheckBox, QRadioButton, QButtonGroup, QListWidget, QListWidgetItem,
                               QProgressBar, QMessageBox, QTextEdit, QDialog, QListView, QTreeView, QAbstractItemView, QAbstractScrollArea, QGraphicsOpacityEffect, QGraphicsEffect,
                               QInputDialog, QScrollBar, QStyle, QStyleOptionButton, QStyleOptionSlider, QSizePolicy, QSplashScreen, QSplitter, QStyledItemDelegate, QHeaderView)
from PySide6.QtGui import QColor, QIcon, QDesktopServices, QPainter, QPen, QPainterPath, QLinearGradient, QBrush, QSurfaceFormat, QPixmap, QRegion, QGuiApplication, QCursor, QFontDatabase, QFont, QPalette
from PySide6.QtCore import (
    Qt,
    QThread,
    Signal,
    QTimer,
    QElapsedTimer,
    QUrl,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QEvent,
    QEventLoop,
    QPropertyAnimation,
    QVariantAnimation,
    QParallelAnimationGroup,
    QEasingCurve,
)

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
WINDOWS_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
WINDOWS_ERROR_MORE_DATA = 234


class _TeeDebugStream:
    """Mirror a text stream to the terminal and the in-app debug readout."""

    def __init__(self, stream_name, original_stream, line_callback):
        self._stream_name = str(stream_name or "stdout").strip().lower() or "stdout"
        self._original_stream = original_stream
        self._line_callback = line_callback
        self._pending_text = ""
        self._lock = threading.Lock()
        self.encoding = getattr(original_stream, "encoding", "utf-8")
        self.errors = getattr(original_stream, "errors", "replace")

    def write(self, data):
        text = "" if data is None else str(data)
        original = self._original_stream
        written = len(text)
        if original is not None and hasattr(original, "write"):
            try:
                result = original.write(text)
                if isinstance(result, int):
                    written = result
            except Exception:
                written = len(text)
        self._capture_lines(text)
        return written

    def writelines(self, lines):
        if lines is None:
            return
        for line in lines:
            self.write(line)

    def flush(self):
        original = self._original_stream
        if original is not None and hasattr(original, "flush"):
            try:
                original.flush()
            except Exception:
                pass
        with self._lock:
            pending = self._pending_text
            self._pending_text = ""
        if pending:
            self._emit_line(pending)

    def isatty(self):
        original = self._original_stream
        if original is not None and hasattr(original, "isatty"):
            try:
                return bool(original.isatty())
            except Exception:
                return False
        return False

    def fileno(self):
        original = self._original_stream
        if original is None or not hasattr(original, "fileno"):
            raise OSError("Underlying stream does not expose fileno()")
        return original.fileno()

    def _capture_lines(self, text):
        if not text:
            return
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        with self._lock:
            combined = f"{self._pending_text}{normalized}"
            parts = combined.split("\n")
            self._pending_text = parts.pop() if parts else ""
        for line in parts:
            self._emit_line(line)

    def _emit_line(self, text):
        callback = self._line_callback
        if callback is None:
            return
        try:
            callback(text, self._stream_name)
        except Exception:
            pass

    def __getattr__(self, name):
        original = self._original_stream
        if original is None:
            raise AttributeError(name)
        return getattr(original, name)


_BOOTSTRAP_DEBUG_LINES = []
_BOOTSTRAP_DEBUG_MAX_LINES = 8000
_BOOTSTRAP_DEBUG_LINE_MAX_CHARS = 1600
_BOOTSTRAP_DEBUG_CAPTURE_ENABLED = False
_BOOTSTRAP_DEBUG_STDOUT_ORIGINAL = None
_BOOTSTRAP_DEBUG_STDERR_ORIGINAL = None
_BOOTSTRAP_DEBUG_STDOUT_CAPTURE = None
_BOOTSTRAP_DEBUG_STDERR_CAPTURE = None
_BOOTSTRAP_DEBUG_SINK = None


def _append_bootstrap_debug_buffer_line(message):
    text = str(message or "").rstrip()
    if not text:
        return
    max_chars = int(_BOOTSTRAP_DEBUG_LINE_MAX_CHARS or 1600)
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    _BOOTSTRAP_DEBUG_LINES.append(text)
    max_lines = int(_BOOTSTRAP_DEBUG_MAX_LINES or 8000)
    if len(_BOOTSTRAP_DEBUG_LINES) > max_lines:
        overflow = len(_BOOTSTRAP_DEBUG_LINES) - max_lines
        if overflow > 0:
            del _BOOTSTRAP_DEBUG_LINES[:overflow]


def _forward_bootstrap_debug_line(message, stream_name="stdout"):
    line = str(message or "").strip()
    if not line:
        return
    stream = str(stream_name or "stdout").strip().lower()
    formatted = f"[stderr] {line}" if stream == "stderr" else line
    _append_bootstrap_debug_buffer_line(formatted)
    sink = _BOOTSTRAP_DEBUG_SINK
    if callable(sink):
        try:
            sink(line, stream)
        except Exception:
            pass


def _emit_bootstrap_debug_status(message):
    text = str(message or "").rstrip()
    if not text:
        return
    _append_bootstrap_debug_buffer_line(text)
    sink = _BOOTSTRAP_DEBUG_SINK
    if callable(sink):
        try:
            sink(text, "stdout")
        except Exception:
            pass


def _set_bootstrap_debug_sink(callback):
    global _BOOTSTRAP_DEBUG_SINK
    _BOOTSTRAP_DEBUG_SINK = callback if callable(callback) else None


def _set_bootstrap_debug_capture_enabled(enabled):
    global _BOOTSTRAP_DEBUG_CAPTURE_ENABLED
    global _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL
    global _BOOTSTRAP_DEBUG_STDERR_ORIGINAL
    global _BOOTSTRAP_DEBUG_STDOUT_CAPTURE
    global _BOOTSTRAP_DEBUG_STDERR_CAPTURE

    target = bool(enabled)
    if target == bool(_BOOTSTRAP_DEBUG_CAPTURE_ENABLED):
        return

    if target:
        _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL = sys.stdout
        _BOOTSTRAP_DEBUG_STDERR_ORIGINAL = sys.stderr
        _BOOTSTRAP_DEBUG_STDOUT_CAPTURE = _TeeDebugStream(
            "stdout",
            _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL,
            _forward_bootstrap_debug_line,
        )
        _BOOTSTRAP_DEBUG_STDERR_CAPTURE = _TeeDebugStream(
            "stderr",
            _BOOTSTRAP_DEBUG_STDERR_ORIGINAL,
            _forward_bootstrap_debug_line,
        )
        sys.stdout = _BOOTSTRAP_DEBUG_STDOUT_CAPTURE
        sys.stderr = _BOOTSTRAP_DEBUG_STDERR_CAPTURE
        _BOOTSTRAP_DEBUG_CAPTURE_ENABLED = True
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _emit_bootstrap_debug_status(f"=== Debug capture enabled at {stamp} ===")
        return

    try:
        if _BOOTSTRAP_DEBUG_STDOUT_CAPTURE is not None:
            _BOOTSTRAP_DEBUG_STDOUT_CAPTURE.flush()
        if _BOOTSTRAP_DEBUG_STDERR_CAPTURE is not None:
            _BOOTSTRAP_DEBUG_STDERR_CAPTURE.flush()
    except Exception:
        pass
    finally:
        if _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL is not None:
            sys.stdout = _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL
        if _BOOTSTRAP_DEBUG_STDERR_ORIGINAL is not None:
            sys.stderr = _BOOTSTRAP_DEBUG_STDERR_ORIGINAL
        _BOOTSTRAP_DEBUG_STDOUT_CAPTURE = None
        _BOOTSTRAP_DEBUG_STDERR_CAPTURE = None
        _BOOTSTRAP_DEBUG_STDOUT_ORIGINAL = None
        _BOOTSTRAP_DEBUG_STDERR_ORIGINAL = None
        _BOOTSTRAP_DEBUG_CAPTURE_ENABLED = False
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _emit_bootstrap_debug_status(f"=== Debug capture disabled at {stamp} ===")


def _should_enable_bootstrap_debug_capture():
    try:
        config_path = ui_settings.get_config_path(mixsplitr_core)
        config = ui_settings.read_config_from_disk(config_path)
        return bool(config.get("debug_readout_enabled", False))
    except Exception:
        return False


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
        if _windows_drive_is_mapped_network(root):
            continue
        if not _windows_drive_is_ready(root):
            continue
        roots.append(root)
    return roots


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
        for drive_root in _list_windows_logical_drive_roots():
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

try:
    from PySide6.QtMultimedia import (
        QMediaCaptureSession,
        QAudioInput,
        QMediaRecorder,
        QMediaDevices,
        QAudioSource,
        QMediaFormat,
    )
    QT_MEDIA_AVAILABLE = True
except Exception:
    QMediaCaptureSession = None
    QAudioInput = None
    QMediaRecorder = None
    QMediaDevices = None
    QAudioSource = None
    QMediaFormat = None
    QT_MEDIA_AVAILABLE = False
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    QT_MEDIA_PLAYBACK_AVAILABLE = True
except Exception:
    QMediaPlayer = None
    QAudioOutput = None
    QT_MEDIA_PLAYBACK_AVAILABLE = False

configure_waveform_runtime(
    audio_segment_cls=AudioSegment,
    editor_module=mixsplitr_editor,
    media_player_cls=QMediaPlayer,
    audio_output_cls=QAudioOutput,
    media_playback_available=QT_MEDIA_PLAYBACK_AVAILABLE,
)


def _resolve_bundled_resource_path(relative_path):
    """Resolve bundled resource path for dev and PyInstaller runtime."""
    rel = str(relative_path or "").strip().replace("\\", "/")
    if not rel:
        return ""
    try:
        if mixsplitr_core is not None and hasattr(mixsplitr_core, "resource_path"):
            return mixsplitr_core.resource_path(rel)
    except Exception:
        pass
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def _register_bundled_roboto_fonts():
    """
    Register bundled Roboto font files from ./fonts.
    Returns:
      (
          sorted loaded family names,
          preferred Thin family name if available,
          preferred Light family name if available,
          preferred Bold family name if available,
      )
    """
    fonts_dir = _resolve_bundled_resource_path("fonts")
    if not fonts_dir or not os.path.isdir(fonts_dir):
        return ([], "", "", "")

    # Load variable fonts plus key static faces used by stylesheet aliases.
    candidate_paths = [
        os.path.join(fonts_dir, "Roboto-VariableFont_wdth,wght.ttf"),
        os.path.join(fonts_dir, "Roboto-Italic-VariableFont_wdth,wght.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-Thin.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-ThinItalic.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-Light.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-LightItalic.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-Bold.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-BoldItalic.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-Regular.ttf"),
        os.path.join(fonts_dir, "static", "Roboto-Italic.ttf"),
    ]

    # Backward-compatible fallback: include any top-level Roboto font files.
    for filename in sorted(os.listdir(fonts_dir)):
        lower = filename.lower()
        if not lower.startswith("roboto"):
            continue
        if not (lower.endswith(".ttf") or lower.endswith(".otf")):
            continue
        candidate_paths.append(os.path.join(fonts_dir, filename))

    loaded_families = []
    preferred_thin_family = ""
    preferred_light_family = ""
    preferred_bold_family = ""
    for font_path in dict.fromkeys(candidate_paths):
        if not os.path.isfile(font_path):
            continue
        font_id = QFontDatabase.addApplicationFont(font_path)
        if font_id == -1:
            print(f"Warning: failed to load bundled font: {font_path}")
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if (
            not preferred_thin_family
            and os.path.basename(font_path).lower() == "roboto-thin.ttf"
            and families
        ):
            preferred_thin_family = families[0]
        if (
            not preferred_light_family
            and os.path.basename(font_path).lower() == "roboto-light.ttf"
            and families
        ):
            preferred_light_family = families[0]
        if (
            not preferred_bold_family
            and os.path.basename(font_path).lower() == "roboto-bold.ttf"
            and families
        ):
            preferred_bold_family = families[0]
        loaded_families.extend(families)

    return (
        sorted(set(loaded_families)),
        preferred_thin_family,
        preferred_light_family,
        preferred_bold_family,
    )


# ==========================================
# 3. MAIN APPLICATION WINDOW
# ==========================================
class MixSplitRApp(QMainWindow):
    debug_stream_line = Signal(str)
    LONG_TRACK_PROMPT_THRESHOLD_MINUTES = 6.0
    TAB_SWITCH_CARD_REVEAL_DELAY_MS = 42
    SPLITTER_WORKFLOW_SPLIT_ONLY = "split_only_no_id"
    SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY = "split_and_identify"
    SPLITTER_WORKFLOW_TIMESTAMP = "timestamp"
    # Legacy aliases kept so saved configs / code references still resolve.
    SPLITTER_WORKFLOW_TIMESTAMP_NO_ID = "timestamp"
    SPLITTER_WORKFLOW_TIMESTAMP_WITH_ID = "timestamp"
    ESSENTIA_SIMPLE_PROFILE_KEYS = ("conservative", "balanced", "aggressive")
    ESSENTIA_SIMPLE_PROFILE_LABELS = {
        "conservative": "Conservative",
        "balanced": "Balanced",
        "aggressive": "Aggressive",
        "custom": "Custom",
    }
    TRANSITION_DETECTION_PROFILE_KEYS = ("balanced", "electronic", "rock")
    TRANSITION_DETECTION_PROFILE_LABELS = {
        "balanced": "Balanced",
        "electronic": "Electronic / DJ Mix",
        "rock": "Rock / Band Mix",
        "custom": "Custom",
    }
    ESSENTIA_SIMPLE_PROFILE_VALUES = {
        "conservative": {
            "auto_tracklist_essentia_min_confidence": 0.48,
            "auto_tracklist_essentia_max_points": 1600,
            "essentia_genre_enrichment_min_confidence": 0.42,
            "essentia_genre_enrichment_max_tags": 1,
        },
        "aggressive": {
            "auto_tracklist_essentia_min_confidence": 0.24,
            "auto_tracklist_essentia_max_points": 3600,
            "essentia_genre_enrichment_min_confidence": 0.26,
            "essentia_genre_enrichment_max_tags": 4,
        },
    }
    TRANSITION_DETECTION_PROFILE_VALUES = {
        "electronic": {
            "auto_tracklist_window_seconds": 16,
            "auto_tracklist_step_seconds": 10,
            "auto_tracklist_min_segment_seconds": 24,
            "auto_tracklist_min_confidence": 0.54,
            "auto_tracklist_boundary_backtrack_seconds": 0.0,
            "auto_tracklist_essentia_min_confidence": 0.28,
            "auto_tracklist_essentia_max_points": 3200,
        },
        "rock": {
            "auto_tracklist_window_seconds": 22,
            "auto_tracklist_step_seconds": 16,
            "auto_tracklist_min_segment_seconds": 45,
            "auto_tracklist_min_confidence": 0.68,
            "auto_tracklist_boundary_backtrack_seconds": 0.0,
            "auto_tracklist_essentia_min_confidence": 0.52,
            "auto_tracklist_essentia_max_points": 1200,
        },
    }

    def __init__(self):
        super().__init__()
        annotate_widget(self, role="Main Window")
        self.setWindowTitle("MixSplitR")
        if sys.platform == "win32":
            self._base_window_size = (1380, 888)
        elif sys.platform == "darwin":
            self._base_window_size = (1440, 840)
        else:
            self._base_window_size = (1420, 860)
        self._content_corner_radius = 22
        self._initial_screen_fit_applied = False
        self._scalable_text = []   # list of (widget, base_px, extra_style)
        self._scalable_layouts = []  # list of layout metric specs
        self._scalable_min_heights = []  # list of (widget, base_px)
        self._base_qss = ""        # stored so we can append scaled button rules
        # Denser default UI on Windows/macOS so more of each page fits on
        # screen without changing system-level DPI settings.
        if sys.platform == "win32":
            self._default_ui_density = 0.84
            self._layout_density = 0.78
        elif sys.platform == "darwin":
            self._default_ui_density = 0.79
            self._layout_density = 0.73
        else:
            self._default_ui_density = 1.0
            self._layout_density = 1.0
        self._mac_titlebar_glass_applied = False
        self._splitter_fit_applied = False
        self._apply_initial_window_size()
        self.enable_card_shadows = False
        self.nav_active_surface_color = "#121417"
        self.show_nav_selection_surface = False
        # Restore native macOS titlebar/window shell.
        self.use_default_macos_window_shell = (sys.platform == "darwin")
        self.use_invisible_macos_drag_strip = (
            sys.platform == "darwin" and not self.use_default_macos_window_shell
        )
        if sys.platform == "darwin" and self.use_default_macos_window_shell:
            # Keep native macOS shell/buttons, but remove window title text.
            self.setWindowTitle("")
        # Tight top-band fade: clear more of the outer shell under the strip.
        self.scroll_top_fade_height = 16 if self.use_invisible_macos_drag_strip else 0
        self.scroll_top_fade_cutoff_offset = 18 if self.use_invisible_macos_drag_strip else 0
        self.scroll_top_fade_strength = 2.8 if self.use_invisible_macos_drag_strip else 1.0
        self.top_card_dragstrip_buffer = 14 if self.use_invisible_macos_drag_strip else 0
        self._nav_surface_style_key = None
        self.sidebar_blur_enabled = False
        self._ns_sidebar_vibrancy_view = None
        self._ns_sidebar_native_view = None
        self.content_blur_enabled = False
        self._ns_content_vibrancy_view = None
        self._ns_content_native_view = None
        self.debug_readout_dialog = None
        self._developer_inspector_payload = {}
        self._debug_readout_lines = list(_BOOTSTRAP_DEBUG_LINES)
        self._debug_readout_max_lines = 8000
        self._debug_readout_line_max_chars = 1600
        self._debug_capture_enabled = bool(_BOOTSTRAP_DEBUG_CAPTURE_ENABLED)
        self._debug_stdout_original = None
        self._debug_stderr_original = None
        self._debug_stdout_capture = None
        self._debug_stderr_capture = None
        self._update_check_thread = None
        self._essentia_simple_syncing = False
        self._essentia_advanced_widgets = []
        self.essentia_advanced_card = None
        self._track_editor_scroll_refresh_ts = 0.0
        self._track_editor_scroll_deferred_refresh = set()
        self.debug_stream_line.connect(self._append_debug_readout_line)
        self.developer_inspector_controller = DeveloperInspectorController(self)
        self.developer_inspector_controller.inspector_changed.connect(
            self._on_developer_inspector_payload_changed
        )
        if sys.platform == "darwin":
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        if sys.platform == "darwin" and not self.use_default_macos_window_shell:
            try:
                self.setUnifiedTitleAndToolBarOnMac(True)
            except Exception:
                pass
            if hasattr(Qt, "WA_LayoutOnEntireRect"):
                self.setAttribute(Qt.WA_LayoutOnEntireRect, True)
            if hasattr(Qt, "WA_ContentsMarginsRespectsSafeArea"):
                self.setAttribute(Qt.WA_ContentsMarginsRespectsSafeArea, False)
        
        # Set the window / Dock / taskbar icon from the same PNG used in the sidebar.
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixsplitr_icon_512.png")
        if os.path.exists(_icon_path):
            _app_icon = QIcon(_icon_path)
            self.setWindowIcon(_app_icon)
            QApplication.instance().setWindowIcon(_app_icon)
        
        label_font_family_css = "Roboto"

        self.setStyleSheet("""
            QMainWindow { background: transparent; }
            QWidget#MainCentral { background: transparent; }
            QWidget#ContentContainer {
                background-color: #181A1E;
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                border-top-right-radius: 22px;
                border-bottom-right-radius: 22px;
            }
            QStackedWidget#ContentStack {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #23272E,
                    stop:1 #1D2127
                );
            }
            QWidget { font-family: __LABEL_FONT_FAMILY__; font-weight: 300; }
            QLabel { color: #E3E3E3; font-family: __LABEL_FONT_FAMILY__; }
            #Sidebar {
                background-color: rgba(36, 39, 44, 0.74);
                border-right: none;
                border-top-right-radius: 0px;
            }
            
            QPushButton.NavButton {
                background-color: rgba(255, 255, 255, 0.04);
                color: #E3E3E3;
                border: none;
                border-radius: 10px;
                padding: 12px 20px; font-size: 15px; font-weight: 700; text-align: left;
            }
            QPushButton.NavButton:!checked:hover { background-color: rgba(130, 136, 145, 0.18); }
            QPushButton.NavButton:checked {
                background-color: rgba(50, 53, 58, 0.90);
                color: #F1F6FF;
                border: none;
                border-radius: 10px;
            }
            QPushButton.NavButton:checked:hover {
                background-color: rgba(58, 62, 68, 0.93);
            }
            QPushButton.NavButton:checked:pressed {
                background-color: rgba(66, 71, 78, 0.95);
            }
            
            QPushButton.ActionButton {
                background-color: #69B97E; color: #131314; font-weight: 400; font-size: 14px;
                border-radius: 20px; padding: 12px 24px;
            }
            QPushButton.ActionButton:hover { background-color: #7BCB90; }
            QPushButton#SendToSplitterButton {
                background-color: #69B97E;
                color: #131314;
                font-weight: 400;
                font-size: 14px;
                border: none;
                border-radius: 20px;
                padding: 12px 24px;
                min-height: 34px;
            }
            QPushButton#SendToSplitterButton:hover {
                background-color: #7BCB90;
            }
            QPushButton#SendToSplitterButton:disabled {
                background-color: #444746;
                color: #C4C7C5;
                border: none;
                border-radius: 20px;
            }
            QPushButton[startProcessCell="true"],
            QPushButton[startProcessCell=true] {
                background-color: #69B97E;
                color: #131314;
                font-weight: 400;
                font-size: 14px;
                border: none;
                border-radius: 20px;
                padding: 12px 24px;
                min-height: 34px;
            }
            QPushButton[startProcessCell="true"]:hover,
            QPushButton[startProcessCell=true]:hover {
                background-color: #7BCB90;
            }
            QPushButton[startProcessCell="true"][busyState="true"],
            QPushButton[startProcessCell=true][busyState=true] {
                background-color: #444746;
                color: #C4C7C5;
                border: none;
                border-radius: 20px;
            }
            QPushButton[startProcessCell="true"]:disabled,
            QPushButton[startProcessCell=true]:disabled {
                background-color: rgba(56, 62, 72, 0.88);
                color: #AEB8C7;
                border: none;
                border-radius: 20px;
            }
            QPushButton.SecondaryButton {
                background-color: rgba(46, 52, 62, 0.62);
                color: #E3E3E3;
                font-weight: 400;
                font-size: 14px;
                border-radius: 20px;
                padding: 12px 24px;
                border: none;
            }
            QPushButton.SecondaryButton:hover { background-color: rgba(86, 92, 101, 0.52); }

            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
                background-color: rgba(48, 52, 58, 0.62);
                border: none;
                border-radius: 10px;
                padding: 8px 12px; color: #E3E3E3; font-size: 14px;
            }
            QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
                background-color: rgba(23, 27, 34, 0.54);
                color: #8793A3;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
                background-color: rgba(72, 78, 86, 0.72);
            }
            QTextEdit {
                background-color: rgba(24, 30, 38, 0.74);
                border: none;
                border-radius: 10px;
                padding: 10px; color: #E3E3E3; font-size: 13px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: rgba(30, 33, 40, 220);
                color: #E3E3E3;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
                padding: 6px 4px;
                selection-background-color: rgba(126, 133, 145, 0.40);
                selection-color: #FFFFFF;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                padding: 8px 12px;
                border-radius: 8px;
                margin: 2px 4px;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: rgba(126, 133, 145, 0.40);
            }
            QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; }
            QListWidget {
                background-color: rgba(23, 28, 35, 0.72);
                border: none;
                border-radius: 10px;
                padding: 6px; color: #E3E3E3; font-size: 13px;
            }
            QListWidget::item { padding: 8px; border-radius: 6px; }
            QListWidget::item:selected { background-color: rgba(126, 133, 145, 0.45); color: #EAF2FF; }
            QListWidget::item:hover:!selected { background-color: rgba(110, 116, 127, 0.35); }

            QMenu {
                background: #2B313B;
                background-color: #2B313B;
                border: 1px solid rgba(130, 145, 168, 0.78);
                border-radius: 10px;
                color: #E3E3E3;
                padding: 6px;
            }
            QMenu::item {
                background-color: transparent;
                color: #E3E3E3;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(126, 133, 145, 0.40);
                color: #F3F8FF;
            }
            QMenu::item:disabled {
                background-color: transparent;
                color: #98A3B2;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(130, 145, 168, 0.32);
                margin: 3px 0px;
            }

            QListWidget#TrackEditorTrackList {
                background-color: transparent;
                border: none;
                border-radius: 12px;
                padding: 0px;
            }
            QListWidget#TrackEditorTrackList::item {
                background-color: rgba(18, 24, 33, 204);
                border: none;
                border-radius: 9px;
                margin: 3px 1px;
                padding: 10px 12px;
            }
            QListWidget#TrackEditorTrackList::item:hover:!selected {
                background-color: rgba(53, 57, 64, 214);
                border: none;
            }
            QListWidget#TrackEditorTrackList::item:selected {
                background-color: rgba(99, 106, 118, 188);
                border: none;
                color: #EAF2FF;
            }

            QSplitter::handle {
                background-color: rgba(14, 18, 24, 150);
                border-radius: 4px;
            }
            QSplitter::handle:horizontal {
                width: 8px;
                margin: 3px 1px;
            }
            QSplitter::handle:vertical {
                height: 8px;
                margin: 1px 3px;
            }

            QScrollBar:vertical {
                background-color: #0F141B;
                width: 12px;
                margin: 4px 0 4px 0;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #7A808A;
                border: 1px solid #0B0E13;
                border-radius: 6px;
                min-height: 26px;
            }
            QScrollBar:horizontal {
                background-color: #0F141B;
                height: 12px;
                margin: 0 4px 0 4px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background-color: #7A808A;
                border: 1px solid #0B0E13;
                border-radius: 6px;
                min-width: 26px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
                height: 0px;
                border: none;
                background-color: #0F141B;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background-color: #0F141B;
            }
            
            QProgressBar { background-color: rgba(40, 47, 57, 0.6); border-radius: 4px; border: none; }
            QProgressBar::chunk { background-color: #7E8591; border-radius: 4px; }

            QCheckBox, QRadioButton { color: #E3E3E3; spacing: 10px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px;
                border: none; background-color: rgba(34, 40, 52, 0.94);
            }
            QRadioButton::indicator {
                width: 16px; height: 16px; border-radius: 8px;
                border: none; background-color: rgba(34, 40, 52, 0.94);
            }
            QCheckBox::indicator:checked { background-color: #7E8591; border: none; }
            QRadioButton::indicator:checked { background-color: #7E8591; border: none; }
            QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {
                color: #808A97;
            }
            QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
                background-color: rgba(30, 35, 45, 0.76);
            }

            QLabel[cardLineSurface="true"] {
                background-color: #353A42;
                border: 1px solid #5A6070;
                border-radius: 12px;
                padding: 10px 16px;
            }
            QLabel[cardLineSurface="true"][helpDescriptionCell="true"] {
                padding-top: 15px;
                padding-bottom: 15px;
            }
            QRadioButton[cardLineSurface="true"],
            QCheckBox[cardLineSurface="true"] {
                background-color: #343942;
                border: 1px solid #596071;
                border-radius: 12px;
                padding: 11px 17px;
            }
            QRadioButton[cardLineSurface="true"]:hover,
            QCheckBox[cardLineSurface="true"]:hover {
                background-color: #404753;
            }

            QLabel[settingsBubbleLabel="true"],
            QLabel[settingsBubbleLabel=true] {
                background-color: #373C45;
                border: 1px solid #5C6372;
                border-radius: 12px;
                padding: 8px 14px;
                color: #DFE8F6;
            }
            QLineEdit[settingsBubbleField="true"],
            QLineEdit[settingsBubbleField=true],
            QComboBox[settingsBubbleField="true"],
            QComboBox[settingsBubbleField=true],
            QSpinBox[settingsBubbleField="true"],
            QSpinBox[settingsBubbleField=true],
            QDoubleSpinBox[settingsBubbleField="true"],
            QDoubleSpinBox[settingsBubbleField=true],
            QTextEdit[settingsBubbleField="true"],
            QTextEdit[settingsBubbleField=true] {
                background-color: #333841;
                border: 1px solid #596071;
                border-radius: 12px;
                padding: 7px 12px;
            }
            QLineEdit[settingsBubbleField="true"]:focus,
            QLineEdit[settingsBubbleField=true]:focus,
            QComboBox[settingsBubbleField="true"]:focus,
            QComboBox[settingsBubbleField=true]:focus,
            QSpinBox[settingsBubbleField="true"]:focus,
            QSpinBox[settingsBubbleField=true]:focus,
            QDoubleSpinBox[settingsBubbleField="true"]:focus,
            QDoubleSpinBox[settingsBubbleField=true]:focus,
            QTextEdit[settingsBubbleField="true"]:focus,
            QTextEdit[settingsBubbleField=true]:focus {
                background-color: #3C434F;
                border: 1px solid #8E959F;
            }
            QLineEdit[settingsBubbleField="true"]:disabled,
            QLineEdit[settingsBubbleField=true]:disabled,
            QComboBox[settingsBubbleField="true"]:disabled,
            QComboBox[settingsBubbleField=true]:disabled,
            QSpinBox[settingsBubbleField="true"]:disabled,
            QSpinBox[settingsBubbleField=true]:disabled,
            QDoubleSpinBox[settingsBubbleField="true"]:disabled,
            QDoubleSpinBox[settingsBubbleField=true]:disabled,
            QTextEdit[settingsBubbleField="true"]:disabled,
            QTextEdit[settingsBubbleField=true]:disabled {
                background-color: #2A2E35;
                border: 1px solid #434955;
                color: #808A97;
            }
            QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"],
            QLineEdit[settingsBubbleField=true][settingsEditableCell=true],
            QComboBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QComboBox[settingsBubbleField=true][settingsEditableCell=true],
            QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QSpinBox[settingsBubbleField=true][settingsEditableCell=true],
            QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true],
            QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"],
            QTextEdit[settingsBubbleField=true][settingsEditableCell=true] {
                background-color: #2E3440;
                border: 1px solid #727D92;
                border-radius: 14px;
                padding: 7px 12px;
            }
            QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QComboBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:hover {
                background-color: #343C4A;
                border: 1px solid #8B919C;
            }
            QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QComboBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:focus {
                background-color: #3B4555;
                border: 1px solid #A1A8B3;
            }
            QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QComboBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:disabled {
                background-color: #2A2F39;
                border: 1px solid #485160;
                color: #808A97;
            }
            QWidget[settingsPage="true"] QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QLineEdit[settingsBubbleField=true][settingsEditableCell=true],
            QWidget[settingsPage="true"] QComboBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QComboBox[settingsBubbleField=true][settingsEditableCell=true],
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true],
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true],
            QWidget[settingsPage="true"] QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QTextEdit[settingsBubbleField=true][settingsEditableCell=true] {
                background-color: #31353B;
                border: none;
                border-radius: 14px;
                min-width: 220px;
                padding: 7px 12px;
                color: #F0F6FF;
            }
            QWidget[settingsPage="true"] QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QWidget[settingsPage=true] QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QWidget[settingsPage="true"] QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QWidget[settingsPage=true] QComboBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:hover,
            QWidget[settingsPage="true"] QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:hover,
            QWidget[settingsPage=true] QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:hover {
                background-color: #3B4047;
                border: none;
            }
            QWidget[settingsPage="true"] QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QWidget[settingsPage=true] QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QWidget[settingsPage="true"] QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QWidget[settingsPage=true] QComboBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:focus,
            QWidget[settingsPage="true"] QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:focus,
            QWidget[settingsPage=true] QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:focus {
                background-color: #454B54;
                border: none;
            }
            QWidget[settingsPage="true"] QLineEdit[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QWidget[settingsPage=true] QLineEdit[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QWidget[settingsPage="true"] QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QWidget[settingsPage=true] QComboBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]:disabled,
            QWidget[settingsPage="true"] QTextEdit[settingsBubbleField="true"][settingsEditableCell="true"]:disabled,
            QWidget[settingsPage=true] QTextEdit[settingsBubbleField=true][settingsEditableCell=true]:disabled {
                background-color: #2C3138;
                border: none;
                color: #808A97;
            }
            QWidget[settingsPage="true"] QComboBox[settingsBubbleField="true"][settingsEditableCell="true"]::drop-down,
            QWidget[settingsPage=true] QComboBox[settingsBubbleField=true][settingsEditableCell=true]::drop-down {
                border: none;
                width: 24px;
            }
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true],
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"],
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true] {
                padding: 0px 12px;
            }
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]::up-button,
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true]::up-button,
            QWidget[settingsPage="true"] QSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]::down-button,
            QWidget[settingsPage=true] QSpinBox[settingsBubbleField=true][settingsEditableCell=true]::down-button,
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]::up-button,
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]::up-button,
            QWidget[settingsPage="true"] QDoubleSpinBox[settingsBubbleField="true"][settingsEditableCell="true"]::down-button,
            QWidget[settingsPage=true] QDoubleSpinBox[settingsBubbleField=true][settingsEditableCell=true]::down-button {
                width: 0px;
            }
            QLabel[settingsEditableCellLabel="true"],
            QLabel[settingsEditableCellLabel=true] {
                background-color: #2E3440;
                border: 1px solid #727D92;
                border-radius: 14px;
                padding: 7px 14px;
                color: #DFE8F6;
            }
            QLabel[settingsValueCapsule="true"],
            QLabel[settingsValueCapsule=true],
            QComboBox[settingsValueCapsule="true"],
            QComboBox[settingsValueCapsule=true],
            QSpinBox[settingsValueCapsule="true"],
            QSpinBox[settingsValueCapsule=true],
            QDoubleSpinBox[settingsValueCapsule="true"],
            QDoubleSpinBox[settingsValueCapsule=true] {
                background-color: #31353B;
                border: 2px solid #5D6675;
                border-radius: 14px;
                padding: 7px 12px;
                color: #F0F6FF;
            }
            QComboBox[settingsValueCapsule="true"]:hover,
            QComboBox[settingsValueCapsule=true]:hover,
            QSpinBox[settingsValueCapsule="true"]:hover,
            QSpinBox[settingsValueCapsule=true]:hover,
            QDoubleSpinBox[settingsValueCapsule="true"]:hover,
            QDoubleSpinBox[settingsValueCapsule=true]:hover {
                background-color: #3B4047;
                border: 2px solid #727D8E;
            }
            QComboBox[settingsValueCapsule="true"]:focus,
            QComboBox[settingsValueCapsule=true]:focus,
            QSpinBox[settingsValueCapsule="true"]:focus,
            QSpinBox[settingsValueCapsule=true]:focus,
            QDoubleSpinBox[settingsValueCapsule="true"]:focus,
            QDoubleSpinBox[settingsValueCapsule=true]:focus {
                background-color: #454B54;
                border: 2px solid #8A97AC;
            }
            QComboBox[settingsValueCapsule="true"]::drop-down,
            QComboBox[settingsValueCapsule=true]::drop-down {
                border: none;
                width: 24px;
            }
            QCheckBox[settingsBubbleField="true"],
            QCheckBox[settingsBubbleField=true] {
                background-color: #333841;
                border: 1px solid #596071;
                border-radius: 12px;
                padding: 7px 11px;
            }
            QCheckBox[settingsBubbleField="true"]:disabled,
            QCheckBox[settingsBubbleField=true]:disabled {
                background-color: #2A2E35;
                border: 1px solid #434955;
                color: #808A97;
            }
            QPushButton[settingsBubbleField="true"],
            QPushButton[settingsBubbleField=true] {
                background-color: #353B45;
                border: 1px solid #5B6271;
                border-radius: 12px;
                padding: 7px 14px;
                color: #DDE3EB;
                font-size: 13px;
            }
            QPushButton[settingsBubbleField="true"]:hover,
            QPushButton[settingsBubbleField=true]:hover {
                background-color: #434B58;
            }
            QPushButton[settingsInlineCell="true"] {
                background-color: #262C35;
                border: 1px solid #434B58;
                border-radius: 14px;
                padding: 8px 16px;
                min-height: 34px;
                color: #E3E7ED;
            }
            QPushButton[settingsInlineCell="true"]:hover {
                background-color: #3C414A;
                border: 1px solid #6A707A;
            }
            QPushButton[settingsInlineCell="true"]:pressed {
                background-color: #343941;
            }
            QPushButton[settingsInlineCell="true"]:disabled {
                background-color: #1F242C;
                border: 1px solid #353C47;
                color: #7E8794;
            }
            QPushButton[historyActionCell="true"],
            QPushButton[historyActionCell=true] {
                background-color: #2C3138;
                border: none;
                border-radius: 14px;
                padding: 9px 16px;
                min-height: 34px;
                color: #E8EEF7;
            }
            QPushButton[historyActionCell="true"]:hover,
            QPushButton[historyActionCell=true]:hover {
                background-color: #3C414A;
                border: none;
            }
            QPushButton[historyActionCell="true"]:pressed,
            QPushButton[historyActionCell=true]:pressed {
                background-color: #343A43;
                border: none;
            }
            QPushButton[historyActionCell="true"]:disabled,
            QPushButton[historyActionCell=true]:disabled {
                background-color: #1D232C;
                border: none;
                color: #A8B6C8;
            }

        """.replace("__LABEL_FONT_FAMILY__", label_font_family_css))
        self._base_qss = self.styleSheet()
        self.setStyleSheet(self._base_qss + self._build_uncodixfy_theme_overrides(label_font_family_css))
        self._base_qss = self.styleSheet()

        central_widget = QWidget()
        central_widget.setObjectName("MainCentral")
        self.setCentralWidget(central_widget)
        self.main_layout = QHBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. Sidebar Setup
        self.setup_sidebar()
        self.main_layout.addWidget(self.sidebar)

        # 2. Stacked Widget (Pages)
        self.content_container = QWidget()
        annotate_widget(self.content_container, role="Content Container")
        self.content_container.setObjectName("ContentContainer")
        self.content_container.setAttribute(Qt.WA_StyledBackground, True)
        if sys.platform == "darwin":
            # WA_NativeWindow gives content_container its own NSView so that
            # GlassPage (WA_NativeWindow inside the scroll area) and
            # TitlebarFadeOverlay (WA_NativeWindow direct child) both land as
            # siblings under content_container's NSView.  Without this they walk
            # all the way to QMainWindow's NSView as their shared ancestor, making
            # geometry mapping unreliable and raise_() ordering unpredictable.
            self.content_container.setAttribute(Qt.WA_NativeWindow, True)
        self.content_layout = QVBoxLayout(self.content_container)
        content_top_inset = self._content_top_inset_height()
        self.content_layout.setContentsMargins(0, content_top_inset, 0, 0)
        self.content_layout.setSpacing(0)

        # Needed early because pages are created before titlebar overlays.
        self._titlebar_drag_strip_height = 10 if self.use_invisible_macos_drag_strip else 0
        self._titlebar_fade_height = 18 if self.use_invisible_macos_drag_strip else 0

        self.stacked_widget = QStackedWidget()
        annotate_widget(self.stacked_widget, role="Content Stack")
        self.stacked_widget.setObjectName("ContentStack")
        self.stacked_widget.setAutoFillBackground(False)
        self.stacked_widget.addWidget(self.create_splitter_page())    # Index 0
        self.stacked_widget.addWidget(self.create_identification_page())  # Index 1
        self.stacked_widget.addWidget(self.create_recording_page())       # Index 2
        self.stacked_widget.addWidget(self.create_cd_ripping_page())      # Index 3
        self.stacked_widget.addWidget(self.create_history_page())         # Index 4
        self.stacked_widget.addWidget(self.create_track_editor_page())    # Index 5
        self.stacked_widget.addWidget(self.create_settings_page())        # Index 6
        self.stacked_widget.addWidget(self.create_help_page())            # Index 7
        self.identification_page_index = 1
        self.track_editor_page_index = 5
        self.content_layout.addWidget(self.stacked_widget)

        self.titlebar_content_spacer = None
        self.titlebar_fade_overlay = None
        if self._titlebar_drag_strip_height > 0:
            drag_strip_color = "#000000" if self.use_invisible_macos_drag_strip else self.nav_active_surface_color
            drag_fade_color = "#000000" if self.use_invisible_macos_drag_strip else self.nav_active_surface_color
            self.titlebar_content_spacer = TitlebarOverlay(self.content_container, drag_strip_color)
            self.titlebar_content_spacer.setObjectName("TitlebarContentSpacer")
            self.titlebar_content_spacer.set_top_corner_radius(0.0)
            self.titlebar_fade_overlay = TitlebarFadeOverlay(self.content_container, drag_fade_color)
            self.titlebar_fade_overlay.setObjectName("TitlebarFadeOverlay")
            self.titlebar_content_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.titlebar_content_spacer.setCursor(Qt.OpenHandCursor)
            self.titlebar_content_spacer.show()
            self.titlebar_fade_overlay.show()
            self._layout_titlebar_drag_strip()
            self.titlebar_fade_overlay.raise_()
            self.titlebar_content_spacer.raise_()

        self.main_layout.addWidget(self.content_container)

        self.nav_page_connector = QFrame(central_widget)
        annotate_widget(self.nav_page_connector, role="Navigation Connector")
        self.nav_page_connector.setObjectName("NavPageConnector")
        self.nav_page_connector.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.nav_page_connector.setStyleSheet(
            "QFrame#NavPageConnector {"
            f"background-color: {self.nav_active_surface_color};"
            "border: none;"
            "border-top-left-radius: 0px;"
            "border-bottom-left-radius: 0px;"
            "border-top-right-radius: 12px;"
            "border-bottom-right-radius: 12px;"
            "}"
        )
        self.nav_page_connector.hide()
        self.nav_connector_anim = QPropertyAnimation(self.nav_page_connector, b"geometry", self)
        self.nav_connector_anim.setDuration(220)
        self.nav_connector_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.nav_connector_anim.valueChanged.connect(lambda _value: self._sync_nav_surface_style())
        self.nav_highlight_anim = QPropertyAnimation(self.nav_selection_highlight, b"geometry", self)
        self.nav_highlight_anim.setDuration(220)
        self.nav_highlight_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.nav_highlight_anim.valueChanged.connect(lambda _value: self._sync_nav_surface_style())

        self.page_reveal_overlay = PageRevealOverlay(self.stacked_widget)
        self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
        self.stacked_widget.installEventFilter(self)
        self.sidebar.installEventFilter(self)
        if self.titlebar_content_spacer is not None:
            self.titlebar_content_spacer.installEventFilter(self)
        self._manual_window_drag_active = False
        self._manual_window_drag_offset = QPoint(0, 0)
        self._card_fade_animations = []
        self._card_fade_token = 0
        self._initial_page_reveal_done = False
        self._initial_page_reveal_retry_count = 0
        self._initial_page_cards_primed = False
        self._prime_initial_page_cards_for_reveal()
        QTimer.singleShot(0, lambda: self._position_nav_connector(animated=False))
        QTimer.singleShot(0, lambda: self._position_nav_highlight(animated=False))
        QTimer.singleShot(0, self._sync_nav_surface_style)
        QTimer.singleShot(0, self._apply_macos_titlebar_glass_if_available)
        QTimer.singleShot(0, self._setup_content_blur_if_available)
        QTimer.singleShot(0, self._setup_sidebar_blur_if_available)
        QTimer.singleShot(0, self._enable_windows_blur)
        QTimer.singleShot(0, self._setup_content_corner_radius)
        # Start the first-page reveal shortly after the startup fitter kicks
        # in, then retry internally until geometry is actually ready.
        QTimer.singleShot(48, self._start_initial_page_reveal_when_ready)

        self._mode_syncing = False
        self.mode_dropdown.currentTextChanged.connect(self._sync_mode_from_splitter)
        self.mode_dropdown.currentTextChanged.connect(self._sync_mode_radio_from_combo)
        self.ident_mode_dropdown.currentTextChanged.connect(self._sync_ident_mode_radio_from_combo)
        self.fingerprint_probe_combo.currentIndexChanged.connect(lambda _idx: self._update_probe_hint())
        self.artist_normalization_mode_combo.currentIndexChanged.connect(
            lambda _idx: self._update_artist_normalization_ui_state()
        )
        self.split_mode_dropdown.currentIndexChanged.connect(lambda _idx: self._update_split_mode_hint())
        self.split_mode_dropdown.currentIndexChanged.connect(lambda _idx: self._sync_split_mode_radio_from_combo())
        self.split_mode_dropdown.currentIndexChanged.connect(
            lambda _idx: self._update_long_track_prompt_ui_state()
        )
        self.long_track_prompt_check.stateChanged.connect(
            lambda _state: self._update_long_track_prompt_ui_state()
        )
        self.long_track_threshold_spin.valueChanged.connect(
            lambda _value: self._update_long_track_prompt_ui_state()
        )
        self.recording_dir_input.editingFinished.connect(self._refresh_recordings_list)
        self.manifest_dir_input.editingFinished.connect(self._refresh_session_history_list)
        self.recording_silence_timeout_spin.valueChanged.connect(self._on_recording_silence_timeout_changed)
        self.recording_keep_screen_awake_check.stateChanged.connect(
            lambda _state: self._sync_recording_awake_prevention()
        )
        self.debug_readout_check.toggled.connect(self._on_debug_readout_toggled)
        self.developer_inspector_check.toggled.connect(self._on_developer_inspector_toggled)
        self.essentia_simple_assist_check.stateChanged.connect(
            lambda _state: self._on_essentia_simple_controls_changed()
        )
        self.essentia_simple_strength_combo.currentIndexChanged.connect(
            lambda _idx: self._on_essentia_simple_controls_changed()
        )
        self.transition_detection_profile_combo.currentIndexChanged.connect(
            lambda _idx: self._on_transition_detection_profile_changed()
        )
        self.essentia_simple_genre_help_check.stateChanged.connect(
            lambda _state: self._on_essentia_simple_controls_changed()
        )
        self.essentia_simple_when_missing_check.stateChanged.connect(
            lambda _state: self._on_essentia_simple_controls_changed()
        )
        self.essentia_show_advanced_check.stateChanged.connect(
            lambda _state: self._set_essentia_advanced_controls_visible(
                bool(self.essentia_show_advanced_check.isChecked())
            )
        )
        self.auto_tracklist_essentia_enabled_check.stateChanged.connect(
            lambda _state: self._sync_essentia_simple_from_advanced()
        )
        self.auto_tracklist_essentia_conf_spin.valueChanged.connect(
            lambda _value: self._sync_essentia_simple_from_advanced()
        )
        self.auto_tracklist_essentia_max_points_spin.valueChanged.connect(
            lambda _value: self._sync_essentia_simple_from_advanced()
        )
        self.window_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.step_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.min_segment_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.conf_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.boundary_backtrack_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.auto_tracklist_essentia_conf_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.auto_tracklist_essentia_max_points_spin.valueChanged.connect(
            lambda _value: self._sync_transition_detection_profile_from_advanced()
        )
        self.essentia_enabled_check.stateChanged.connect(
            lambda _state: self._sync_essentia_simple_from_advanced()
        )
        self.essentia_when_missing_check.stateChanged.connect(
            lambda _state: self._sync_essentia_simple_from_advanced()
        )
        self.essentia_conf.valueChanged.connect(
            lambda _value: self._sync_essentia_simple_from_advanced()
        )
        self.essentia_max_tags_spin.valueChanged.connect(
            lambda _value: self._sync_essentia_simple_from_advanced()
        )
        self._setup_api_validation_watchers()
        self._install_standard_text_context_menus()

        # --- Auto-save: persist settings to disk on every UI change ---
        # Debounce timer prevents disk thrashing when spinning a QSpinBox
        # rapidly — collects changes for 500ms then writes once.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(500)
        self._autosave_timer.timeout.connect(self._autosave_settings)

        # Combo boxes / dropdowns — save immediately on change
        for combo in [self.fingerprint_probe_combo,
                      self.split_mode_dropdown, self.cd_format_combo,
                      self.output_format_combo, self.artist_normalization_mode_combo,
                      self.split_seek_step_combo, self.transition_detection_profile_combo,
                      self.duplicate_policy_combo]:
            combo.currentIndexChanged.connect(lambda _idx: self._schedule_autosave())

        # Check boxes — save immediately on toggle
        for check in [self.shazam_enabled_check, self.show_id_source_check,
                      self.local_bpm_check, self.enable_album_search_check,
                      self.artist_normalization_collapse_backing_band_check,
                      self.artist_normalization_review_ambiguous_check,
                      self.deep_scan_check,
                      self.auto_no_identify_check,
                      self.auto_tracklist_essentia_enabled_check,
                      self.debug_readout_check, self.developer_inspector_check,
                      self.preserve_source_format_check,
                      self.recording_keep_screen_awake_check,
                      self.recording_force_wav_check,
                      self.essentia_enabled_check,
                      self.essentia_when_missing_check, self.long_track_prompt_check,
                      self.cd_auto_metadata_check,
                      self.cd_eject_check]:
            check.stateChanged.connect(lambda _state: self._schedule_autosave())
        self.show_id_source_check.stateChanged.connect(
            lambda _state: self._refresh_id_source_badge_visibility()
        )

        # Spin boxes — debounced (user may click-hold or scroll rapidly)
        for spin in [self.fingerprint_sample_spin, self.window_spin, self.step_spin,
                     self.min_segment_spin, self.fallback_interval_spin,
                     self.max_windows_spin, self.conf_spin,
                     self.boundary_backtrack_spin, self.auto_tracklist_essentia_conf_spin,
                     self.auto_tracklist_essentia_max_points_spin, self.essentia_conf,
                     self.essentia_max_tags_spin, self.essentia_analysis_seconds_spin,
                     self.recording_silence_timeout_spin, self.long_track_threshold_spin,
                     self.artist_normalization_strictness_spin]:
            spin.valueChanged.connect(lambda _val: self._schedule_autosave())

        # Text inputs — save when user finishes editing (tab/click away),
        # not on every keystroke.
        for line_edit in [self.output_dir_input, self.recording_dir_input,
                          self.temp_workspace_dir_input, self.manifest_dir_input, self.cd_output_dir_input,
                          self.acr_host_input, self.acr_access_key_input,
                          self.acr_secret_input, self.acoustid_key_input,
                          self.lastfm_key_input]:
            line_edit.editingFinished.connect(self._autosave_settings)

        self.history_refresh_btn.clicked.connect(self._refresh_session_history_list)
        self.history_view_btn.clicked.connect(self.view_selected_session)
        self.history_compare_btn.clicked.connect(self.compare_selected_sessions)
        self.history_edit_session_btn.clicked.connect(self.open_selected_session_editor)
        self.history_reorganize_preview_btn.clicked.connect(self.preview_reorganize_selected_session)
        self.history_reorganize_apply_btn.clicked.connect(self.apply_reorganize_selected_session)
        self.history_rollback_preview_btn.clicked.connect(self.preview_rollback_selected_session)
        self.history_rollback_apply_btn.clicked.connect(self.apply_rollback_selected_session)
        self.history_apply_safe_btn.clicked.connect(self.apply_selected_session_safe)
        self.history_import_btn.clicked.connect(self.import_session_record)
        self.history_export_btn.clicked.connect(self.export_selected_session_record)
        self.history_delete_btn.clicked.connect(self.delete_selected_session_record)
        self.history_list.itemSelectionChanged.connect(self._update_history_action_buttons)

        self.recording_active = False
        self.recording_last_file = ""
        self.recording_current_file = ""
        self.recording_segment_files = []
        self.recording_output_format = ""
        self.recording_capture_session = None
        self.recording_audio_input = None
        self.recording_recorder = None
        self.recording_active_backend = ""
        self.recording_loopback_thread = None
        self.recording_loopback_stop_event = None
        self.recording_loopback_error = ""
        self.recording_loopback_last_level = 0.0
        self.recording_process_capture_thread = None
        self.recording_process_capture_stop_event = None
        self.recording_process_capture_proc = None
        self.recording_process_capture_error = ""
        self.recording_process_capture_last_level = 0.0
        self.recording_monitor_warning = ""
        self.recording_monitor_output_name = ""
        self.recording_monitor_request_event = None
        self.recording_virtual_input_names = []
        self.recording_virtual_output_names = []
        self.recording_background_device_indexes = {}
        self.recording_finalize_attempts = 0
        self._recording_awake_prevention_state = None
        self.level_source = None
        self.level_io_device = None
        self.level_sample_width = 2
        self.level_current = 0.0
        self.level_last_active_seconds = 0.0
        self.pending_preview_open_editor = False
        self.pending_preview_cache_path = ""
        self.pending_preview_temp_folder = ""
        self.pending_splitter_identifier_workflow = None
        self.thread = None
        self.ident_thread = None
        self.preview_export_thread = None
        self._run_button_state = "idle"
        self._processing_result_handled = False
        self._processing_cancel_requested = False
        self.busy_label = ""
        self.busy_elapsed = QElapsedTimer()
        self.busy_timer = QTimer(self)
        self.busy_timer.setInterval(500)
        self.busy_timer.timeout.connect(self._update_busy_ui)
        # Ensure the run button starts in the same styled "ready" state
        # it uses after a completed session.
        self._set_run_button_busy(False)
        self.recording_auto_stop_silence_seconds = 10.0
        self.recording_auto_stop_triggered = False
        self.recording_stop_reason = ""
        self.level_poll_timer = QTimer(self)
        self.level_poll_timer.setInterval(60)
        self.level_poll_timer.timeout.connect(self._poll_audio_level_meter)
        self.recording_elapsed = QElapsedTimer()
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(250)
        self.recording_timer.timeout.connect(self._update_recording_timer_label)
        self.record_btn.clicked.connect(self.toggle_recording)
        self.recording_refresh_sources_btn.clicked.connect(self._populate_recording_inputs)
        if self.recording_open_sound_settings_btn is not None:
            self.recording_open_sound_settings_btn.clicked.connect(
                lambda: self._open_windows_recording_settings("ms-settings:sound", "Opened Windows Sound settings.")
            )
        if self.recording_open_volume_mixer_btn is not None:
            self.recording_open_volume_mixer_btn.clicked.connect(
                lambda: self._open_windows_recording_settings("ms-settings:apps-volume", "Opened Windows Volume Mixer.")
            )
        if self.recording_restart_audio_services_btn is not None:
            self.recording_restart_audio_services_btn.clicked.connect(self._restart_windows_audio_services)
        self.preview_recording_btn.clicked.connect(self.preview_recording_file)
        self.trim_recording_btn.clicked.connect(self.trim_selected_recording)
        self.delete_recording_btn.clicked.connect(self.delete_selected_recording)
        self.send_to_splitter_btn.clicked.connect(self.send_recording_to_splitter)
        self.recordings_list.itemSelectionChanged.connect(self._on_recordings_selection_changed)
        self.recording_input_select.currentIndexChanged.connect(self._on_recording_input_selection_changed)
        if self.recording_capture_mode_standard_radio is not None:
            self.recording_capture_mode_standard_radio.toggled.connect(
                lambda checked: self._on_recording_capture_mode_toggled("standard", checked)
            )
        if self.recording_capture_mode_background_radio is not None:
            self.recording_capture_mode_background_radio.toggled.connect(
                lambda checked: self._on_recording_capture_mode_toggled("background", checked)
            )
        if self.recording_background_stereo_radio is not None:
            self.recording_background_stereo_radio.toggled.connect(
                lambda checked: self._on_recording_background_device_toggled("stereo", checked)
            )
        if self.recording_background_16ch_radio is not None:
            self.recording_background_16ch_radio.toggled.connect(
                lambda checked: self._on_recording_background_device_toggled("16ch", checked)
            )
        if self.recording_background_monitor_check is not None:
            self.recording_background_monitor_check.toggled.connect(self._on_recording_background_monitor_toggled)
        if self.recording_stop_after_check is not None:
            self.recording_stop_after_check.toggled.connect(self._on_recording_stop_after_toggled)
        if getattr(self, "recording_stop_after_time_input", None) is not None:
            self.recording_stop_after_time_input.textChanged.connect(self._on_recording_stop_after_value_changed)
        self._populate_recording_inputs()
        self._sync_recording_background_monitor_ui()
        self._sync_recording_stop_timer_ui()
        self._configure_split_mode_dropdown()

        # CD Ripping state
        self.cd_ripping_active = False
        self._cd_rip_cancel_requested = False
        self._cd_rip_auto_skip_stuck_tracks = False
        self._cd_rip_skip_current_track_requested = False
        self._cd_rip_timeout_track_index = 0
        self._cd_rip_timeout_track_title = ""
        self.cd_rip_thread = None
        self.cd_rip_output_files = []
        self.cd_rip_failures = []
        self._cd_rip_events = []  # populated from rip thread, consumed on main thread via timer
        self._cd_rip_poll_timer = QTimer(self)
        self._cd_rip_poll_timer.setInterval(200)
        self._cd_rip_poll_timer.timeout.connect(self._poll_cd_rip_progress)
        self.cd_rip_start_btn.clicked.connect(self._start_cd_rip)
        self.cd_rip_refresh_btn.clicked.connect(self._populate_cd_drives)
        self.cd_rip_browse_output_btn.clicked.connect(self._browse_cd_rip_output_directory)
        self.cd_rip_skip_track_btn.clicked.connect(self._request_cd_rip_skip_current_track)
        self.cd_rip_skip_and_auto_btn.clicked.connect(
            lambda: self._request_cd_rip_skip_current_track(enable_auto_skip=True)
        )
        self.cd_ripped_list.itemChanged.connect(self._on_cd_ripped_selection_changed)
        self.cd_rip_send_to_identifier_btn.clicked.connect(self._cd_rip_send_to_identifier)
        self.cd_rip_send_to_editor_btn.clicked.connect(self._cd_rip_send_to_editor)
        self._populate_cd_drives()

        self.load_settings_from_config()
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
        self._force_styled_combobox_popups()
        QTimer.singleShot(350, self._start_sidebar_update_check)

    def _force_styled_combobox_popups(self):
        """Force all QComboBoxes to use Qt-rendered popups so stylesheets apply on macOS."""
        popup_style = (
            "QComboBoxPrivateContainer {"
            "  background: transparent;"
            "  border: none;"
            "}"
            "QFrame {"
            "  background: transparent;"
            "  border: none;"
            "}"
            "QListView {"
            "  background-color: #22262B;"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 6px;"
            "  color: #EBE7E1;"
            "  outline: none;"
            "}"
            "QListView::item {"
            "  padding: 8px 12px;"
            "  border-radius: 6px;"
            "}"
            "QListView::item:selected {"
            "  background-color: #2B4E7A;"
            "}"
        )
        for combo in self.findChildren(QComboBox):
            view = QListView()
            combo.setView(view)
            # Style the popup container to remove native frame
            container = view.parentWidget()
            if container is not None:
                container.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
                container.setAttribute(Qt.WA_TranslucentBackground, True)
                container.setStyleSheet(popup_style)
            view.setStyleSheet(popup_style)

    def _apply_initial_window_size(self):
        base_w, base_h = self._base_window_size
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(base_w, base_h)
            self._initial_screen_fit_applied = True
            return

        available = screen.availableGeometry()
        if sys.platform == "win32":
            max_w = max(720, int(available.width() * 0.992))
            max_h = max(560, int(available.height() * 0.982))
        elif sys.platform == "darwin":
            max_w = max(720, int(available.width() * 0.96))
            max_h = max(560, int(available.height() * 0.96))
        else:
            max_w = max(720, int(available.width() * 0.96))
            max_h = max(560, int(available.height() * 0.94))
        width = min(base_w, max_w)
        height = min(base_h, max_h)
        self.resize(width, height)

        if sys.platform == "win32":
            min_w = min(1100, max_w)
            min_h = min(560, max_h)
        elif sys.platform == "darwin":
            min_w = min(1080, max_w)
            min_h = min(540, max_h)
        else:
            min_w = min(1240, max_w)
            min_h = min(620, max_h)
        self.setMinimumSize(min_w, min_h)
        self._initial_screen_fit_applied = True

    def _fit_window_to_splitter_page_startup(self):
        """Size window to fit Audio Splitter content once on desktop platforms.

        This uses the splitter page's intrinsic size hint so key controls (like
        the Start Processing row) are visible by default without initial scroll.
        """
        if sys.platform not in ("win32", "darwin"):
            return
        if self._splitter_fit_applied:
            return
        if not hasattr(self, "stacked_widget") or self.stacked_widget.count() <= 0:
            return

        splitter_scroll = self.stacked_widget.widget(0)
        if splitter_scroll is None or not hasattr(splitter_scroll, "widget") or not hasattr(splitter_scroll, "viewport"):
            return
        splitter_page = splitter_scroll.widget()
        viewport = splitter_scroll.viewport()
        if splitter_page is None or viewport is None:
            return

        # Guard: if viewport hasn't been sized by the Qt layout engine yet
        # (width/height still 0 or 1), chrome dimensions will be wildly wrong.
        # Retry after a short delay so the layout has a chance to settle.
        if viewport.width() < 10 or viewport.height() < 10:
            retry_count = getattr(self, "_splitter_fit_retry_count", 0)
            if retry_count < 8:
                self._splitter_fit_retry_count = retry_count + 1
                QTimer.singleShot(60, self._fit_window_to_splitter_page_startup)
            return
        self._splitter_fit_retry_count = 0

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        if sys.platform == "win32":
            max_w = max(720, int(available.width() * 0.995))
            max_h = max(560, int(available.height() * 0.985))
        else:
            max_w = max(720, int(available.width() * 0.98))
            max_h = max(560, int(available.height() * 0.98))

        if sys.platform == "win32":
            min_density = 0.74
            min_layout_density = 0.66
            min_fit_ratio = 0.85
        else:
            min_density = 0.76
            min_layout_density = 0.68
            min_fit_ratio = 0.84

        # Iteratively tighten density until the splitter page fits, then resize.
        for _ in range(5):
            try:
                self._update_font_scale()
            except Exception:
                pass
            try:
                layout = splitter_page.layout()
                if layout is not None:
                    layout.activate()
            except Exception:
                pass

            min_hint = splitter_page.minimumSizeHint()
            size_hint = splitter_page.sizeHint()
            required_w = max(int(min_hint.width()), int(size_hint.width()), 1)
            required_h = max(int(min_hint.height()), int(size_hint.height()), 1)

            chrome_w = max(0, int(self.width()) - int(viewport.width()))
            chrome_h = max(0, int(self.height()) - int(viewport.height()))
            needed_w = required_w + chrome_w
            needed_h = required_h + chrome_h
            if needed_w <= max_w and needed_h <= max_h:
                break

            fit_ratio = min(float(max_w) / float(max(1, needed_w)), float(max_h) / float(max(1, needed_h)))
            fit_ratio = max(min_fit_ratio, min(0.99, fit_ratio * 0.97))
            self._default_ui_density = max(min_density, float(self._default_ui_density) * fit_ratio)
            self._layout_density = max(min_layout_density, float(self._layout_density) * fit_ratio)

        # Recompute one last time for final resize target.
        try:
            self._update_font_scale()
        except Exception:
            pass
        try:
            layout = splitter_page.layout()
            if layout is not None:
                layout.activate()
        except Exception:
            pass

        min_hint = splitter_page.minimumSizeHint()
        size_hint = splitter_page.sizeHint()
        required_w = max(int(min_hint.width()), int(size_hint.width()), 1)
        required_h = max(int(min_hint.height()), int(size_hint.height()), 1)
        chrome_w = max(0, int(self.width()) - int(viewport.width()))
        chrome_h = max(0, int(self.height()) - int(viewport.height()))
        target_w = required_w + chrome_w
        target_h = required_h + chrome_h

        if screen is not None:
            target_w = min(target_w, max_w)
            target_h = min(target_h, max_h)

        target_w = max(int(self.minimumWidth()), int(target_w))
        target_h = max(int(self.minimumHeight()), int(target_h))
        if sys.platform == "win32":
            # SizeHint underestimates clipped edge controls on some Windows DPI
            # mixes; keep a small startup safety margin.
            target_w = min(max_w, target_w + 36)
            target_h = min(max_h, target_h + 32)
        if sys.platform == "darwin":
            # Keep a small safety margin so bottom card/footer controls are not
            # visually tight against the window edge on compact mac layouts.
            target_h = min(max_h, target_h + 24)

        # Only expand the window if content overflows — never shrink it below the
        # size already set by _apply_initial_window_size.  Shrinking on startup
        # causes a visible snap-to-smaller after the window appears at its
        # initial size, which looks like a bug to the user.
        target_w = max(int(self.width()), target_w)
        target_h = max(int(self.height()), target_h)

        if target_w != int(self.width()) or target_h != int(self.height()):
            self.resize(int(target_w), int(target_h))

        # If startup fit leaves only a tiny residual overflow, compact layout
        # density slightly so the splitter page lands exactly at no-scroll.
        self._trim_splitter_startup_scroll_sliver(splitter_scroll, splitter_page)

        # Force a genuine QResizeEvent through the entire widget tree so the
        # scroll area's content widget re-flows to the settled viewport size.
        # Without this, macOS buffers the resize and child layouts don't
        # recalculate — the UI looks unsettled until the user manually resizes.
        # A tiny one-pixel nudge (with fallback axis/direction) forces a real
        # resize pass so scroll content reflows to the settled viewport.
        self._windows_layout_reflow()

        self._splitter_fit_applied = True

    def _trim_splitter_startup_scroll_sliver(self, splitter_scroll, splitter_page):
        if splitter_scroll is None or splitter_page is None:
            return
        bar = splitter_scroll.verticalScrollBar() if hasattr(splitter_scroll, "verticalScrollBar") else None
        if bar is None:
            return
        if sys.platform == "win32":
            min_density = 0.74
            min_layout_density = 0.66
        else:
            min_density = 0.76
            min_layout_density = 0.68

        # Only trim tiny startup overflow so we don't materially change layout.
        max_sliver_px = 18
        for _ in range(4):
            try:
                layout = splitter_page.layout()
                if layout is not None:
                    layout.activate()
            except Exception:
                layout = None
            try:
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            except Exception:
                pass

            overflow = int(bar.maximum())
            if overflow <= 0 or overflow > max_sliver_px:
                break

            prev_density = float(self._default_ui_density)
            prev_layout_density = float(self._layout_density)
            self._default_ui_density = max(min_density, prev_density * 0.992)
            self._layout_density = max(min_layout_density, prev_layout_density * 0.992)
            if (
                float(self._default_ui_density) == prev_density
                and float(self._layout_density) == prev_layout_density
            ):
                break
            try:
                self._update_font_scale()
            except Exception:
                pass
            try:
                splitter_page.updateGeometry()
                if layout is not None:
                    layout.activate()
            except Exception:
                pass

    def _windows_layout_reflow(self):
        """Force a genuine resizeEvent to fix clipped widgets on first show.

        On Windows, scroll areas with setWidgetResizable(True) report a
        viewport size to child layouts before DPI-aware font metrics have fully
        settled.  Recursive layout invalidation alone can't fix this because
        the scroll area controls its content widget's geometry directly.

        The only reliable fix is triggering a real QResizeEvent with an actual
        size change.  Prefer a +1px nudge, but fall back to another axis (or a
        -1px nudge) when the window is already constrained at startup.
        """
        old = self.size()
        old_w = int(old.width())
        old_h = int(old.height())
        min_w = int(self.minimumWidth())
        min_h = int(self.minimumHeight())
        max_w = int(self.maximumWidth())
        max_h = int(self.maximumHeight())

        candidates = []
        if old_w < max_w:
            candidates.append((old_w + 1, old_h))
        if old_h < max_h:
            candidates.append((old_w, old_h + 1))
        if old_w > min_w:
            candidates.append((old_w - 1, old_h))
        if old_h > min_h:
            candidates.append((old_w, old_h - 1))

        nudge = None
        for w, h in candidates:
            if w != old_w or h != old_h:
                nudge = (int(w), int(h))
                break
        if nudge is None:
            return

        self.resize(nudge[0], nudge[1])
        QTimer.singleShot(0, lambda ow=old_w, oh=old_h: self.resize(ow, oh))

    def _layout_titlebar_drag_strip(self):
        strip = getattr(self, "titlebar_content_spacer", None)
        fade = getattr(self, "titlebar_fade_overlay", None)
        container = getattr(self, "content_container", None)
        if container is None:
            return
        h = int(max(0, getattr(self, "_titlebar_drag_strip_height", 0)))
        width = max(1, container.width())

        if strip is not None:
            if h <= 0:
                strip.hide()
            else:
                strip.setGeometry(0, 0, width, h)
                strip.show()
        fade_h = int(max(0, getattr(self, "_titlebar_fade_height", 0)))
        if fade is not None:
            if h <= 0 or fade_h <= 0:
                if hasattr(fade, "set_top_clear_fraction"):
                    fade.set_top_clear_fraction(0.0)
                fade.hide()
            else:
                fade.setGeometry(0, h, width, fade_h)
                if hasattr(fade, "set_top_clear_fraction"):
                    fade.set_top_clear_fraction(0.0)
                fade.show()
                fade.raise_()

        if strip is not None and h > 0:
            strip.raise_()
        self._apply_content_top_inset_layout()
        self._refresh_scroll_top_fade_masks()

    def _content_top_inset_height(self):
        if sys.platform != "darwin" or not self.use_invisible_macos_drag_strip:
            return 0
        strip_h = int(max(0, getattr(self, "_titlebar_drag_strip_height", 0)))
        fade_h = int(max(0, getattr(self, "_titlebar_fade_height", 0)))
        if strip_h <= 0:
            return 0
        return strip_h + fade_h

    def _apply_content_top_inset_layout(self):
        layout = getattr(self, "content_layout", None)
        if layout is None:
            return
        top = int(max(0, self._content_top_inset_height()))
        try:
            margins = layout.contentsMargins()
            left = int(margins.left())
            current_top = int(margins.top())
            right = int(margins.right())
            bottom = int(margins.bottom())
        except Exception:
            # Fallback for bindings that expose tuple-style margins.
            vals = layout.getContentsMargins()
            left = int(vals[0])
            current_top = int(vals[1])
            right = int(vals[2])
            bottom = int(vals[3])
        if left == 0 and right == 0 and bottom == 0 and current_top == top:
            return
        # Reserve a real non-scrolling top lane so native scrolling layers
        # cannot overlap the drag strip/fade region during fast scrolling.
        layout.setContentsMargins(0, top, 0, 0)

    def _setup_content_corner_radius(self):
        """No-op: corner shaping is handled by Qt styles to keep seam geometry flat."""
        return

    def _build_uncodixfy_theme_overrides(self, font_family_css):
        family = str(font_family_css or "Roboto").strip() or "Roboto"
        return f"""
            QMainWindow {{
                background-color: #191B1F;
            }}
            QWidget#MainCentral {{
                background-color: #191B1F;
            }}
            QWidget#ContentContainer {{
                background-color: #1D2024;
                border-left: 0px;
                border-radius: 0px;
            }}
            QStackedWidget#ContentStack {{
                background: #1D2024;
            }}
            QWidget {{
                font-family: {family};
            }}
            QLabel {{
                color: #EBE7E1;
            }}
            #Sidebar {{
                background-color: #16181B;
                border-right: 1px solid #30353C;
                border-top-right-radius: 0px;
            }}
            QPushButton.NavButton {{
                background-color: transparent;
                color: #B4AEA4;
                border: none;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 500;
                text-align: left;
            }}
            QPushButton.NavButton:!checked:hover {{
                background-color: #20242A;
                color: #EBE7E1;
            }}
            QPushButton.NavButton:checked {{
                background-color: #24292F;
                color: #EBE7E1;
                border: none;
            }}
            QPushButton.NavButton:checked:hover {{
                background-color: #2A2F36;
            }}
            QPushButton.NavButton:checked:pressed {{
                background-color: #23282E;
            }}
            QPushButton.ActionButton,
            QPushButton#SendToSplitterButton,
            QPushButton[startProcessCell="true"],
            QPushButton[startProcessCell=true] {{
                background-color: #4D8DFF;
                color: #F6FAFF;
                font-weight: 500;
                font-size: 14px;
                border: none;
                border-radius: 8px;
                padding: 9px 18px;
                min-height: 34px;
            }}
            QPushButton.ActionButton:hover,
            QPushButton#SendToSplitterButton:hover,
            QPushButton[startProcessCell="true"]:hover,
            QPushButton[startProcessCell=true]:hover {{
                background-color: #63A1FF;
            }}
            QPushButton#SendToSplitterButton:disabled,
            QPushButton[startProcessCell="true"][busyState="true"],
            QPushButton[startProcessCell=true][busyState=true],
            QPushButton[startProcessCell="true"]:disabled,
            QPushButton[startProcessCell=true]:disabled {{
                background-color: #2A2F36;
                color: #9C978D;
                border: none;
            }}
            QPushButton.SecondaryButton,
            QPushButton[settingsInlineCell="true"],
            QPushButton[settingsInlineCell=true],
            QPushButton[historyActionCell="true"],
            QPushButton[historyActionCell=true] {{
                background-color: #22262B;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                min-height: 34px;
            }}
            QPushButton.SecondaryButton:hover,
            QPushButton[settingsInlineCell="true"]:hover,
            QPushButton[settingsInlineCell=true]:hover,
            QPushButton[historyActionCell="true"]:hover,
            QPushButton[historyActionCell=true]:hover {{
                background-color: #292E34;
            }}
            QPushButton.SecondaryButton:pressed,
            QPushButton[settingsInlineCell="true"]:pressed,
            QPushButton[settingsInlineCell=true]:pressed,
            QPushButton[historyActionCell="true"]:pressed,
            QPushButton[historyActionCell=true]:pressed {{
                background-color: #1F2328;
            }}
            QPushButton.SecondaryButton:disabled,
            QPushButton[settingsInlineCell="true"]:disabled,
            QPushButton[settingsInlineCell=true]:disabled,
            QPushButton[historyActionCell="true"]:disabled,
            QPushButton[historyActionCell=true]:disabled {{
                background-color: #1C1F23;
                color: #7F7A72;
                border: none;
            }}
            QListWidget,
            QListView,
            QTreeView,
            QTextEdit,
            QLineEdit,
            QComboBox,
            QSpinBox,
            QDoubleSpinBox {{
                background-color: #1F2328;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                selection-background-color: #2B4E7A;
                selection-color: #EBE7E1;
            }}
            QListWidget::item,
            QListView::item,
            QTreeView::item {{
                border-radius: 6px;
            }}
            QListWidget::item:selected,
            QListView::item:selected,
            QTreeView::item:selected {{
                background-color: #2B4E7A;
                color: #EBE7E1;
            }}
            QListWidget::item:hover:!selected,
            QListView::item:hover:!selected,
            QTreeView::item:hover:!selected {{
                background-color: #2A2F36;
            }}
            QLineEdit:hover,
            QComboBox:hover,
            QSpinBox:hover,
            QDoubleSpinBox:hover,
            QTextEdit:hover {{
                background-color: #24292F;
            }}
            QLineEdit:focus,
            QComboBox:focus,
            QComboBox:on,
            QSpinBox:focus,
            QDoubleSpinBox:focus,
            QTextEdit:focus {{
                background-color: #262C33;
                border: none;
            }}
            QLineEdit:disabled,
            QComboBox:disabled,
            QSpinBox:disabled,
            QDoubleSpinBox:disabled,
            QTextEdit:disabled {{
                background-color: #1A1D21;
                color: #7F7A72;
                border: none;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QSpinBox::up-button,
            QSpinBox::down-button,
            QDoubleSpinBox::up-button,
            QDoubleSpinBox::down-button {{
                width: 0px;
            }}
            QLabel[cardLineSurface="true"],
            QLabel[settingsBubbleLabel="true"],
            QLabel[settingsBubbleLabel=true],
            QLabel[settingsEditableCellLabel="true"],
            QLabel[settingsEditableCellLabel=true],
            QLabel[settingsValueCapsule="true"],
            QLabel[settingsValueCapsule=true],
            QRadioButton[cardLineSurface="true"],
            QCheckBox[cardLineSurface="true"],
            QCheckBox[settingsBubbleField="true"],
            QCheckBox[settingsBubbleField=true] {{
                background-color: #22262B;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
            }}
            QLineEdit[settingsBubbleField="true"],
            QLineEdit[settingsBubbleField=true],
            QComboBox[settingsBubbleField="true"],
            QComboBox[settingsBubbleField=true],
            QSpinBox[settingsBubbleField="true"],
            QSpinBox[settingsBubbleField=true],
            QDoubleSpinBox[settingsBubbleField="true"],
            QDoubleSpinBox[settingsBubbleField=true],
            QTextEdit[settingsBubbleField="true"],
            QTextEdit[settingsBubbleField=true],
            QComboBox[settingsValueCapsule="true"],
            QComboBox[settingsValueCapsule=true],
            QSpinBox[settingsValueCapsule="true"],
            QSpinBox[settingsValueCapsule=true],
            QDoubleSpinBox[settingsValueCapsule="true"],
            QDoubleSpinBox[settingsValueCapsule=true] {{
                background-color: #1F2328;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
            }}
            QProgressBar {{
                background-color: #20242A;
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: #4D8DFF;
                border-radius: 4px;
            }}
            QCheckBox,
            QRadioButton {{
                color: #EBE7E1;
                spacing: 10px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: none;
                background-color: #1A1D21;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: none;
                background-color: #1A1D21;
            }}
            QCheckBox::indicator:checked,
            QRadioButton::indicator:checked {{
                background-color: #4D8DFF;
            }}
            QScrollBar:vertical {{
                background-color: #1D2024;
                width: 12px;
                margin: 0px;
                border-radius: 0px;
            }}
            QScrollBar::handle:vertical {{
                background-color: #798390;
                border: none;
                border-radius: 4px;
                min-height: 28px;
            }}
            QScrollBar:horizontal {{
                background-color: #1D2024;
                height: 12px;
                margin: 0px;
                border-radius: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background-color: #798390;
                border: none;
                border-radius: 4px;
                min-width: 28px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: #1D2024;
            }}
            QSplitter::handle {{
                background-color: #22262B;
                border-radius: 0px;
            }}
            QSplitter::handle:horizontal {{
                width: 6px;
                margin: 0px;
            }}
            QSplitter::handle:vertical {{
                height: 6px;
                margin: 0px;
            }}
        """

    def _apply_macos_titlebar_glass_if_available(self):
        if self._mac_titlebar_glass_applied:
            return True
        if sys.platform != "darwin":
            return False
        if objc is None:
            return False
        try:
            _ = int(self.winId())
            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window is None:
                return False

            # Default mac shell mode: leave native titlebar/frame untouched.
            if getattr(self, "use_default_macos_window_shell", False):
                self._mac_titlebar_glass_applied = True
                return True

            if not MACOS_VIBRANCY_AVAILABLE:
                return False

            if NSWindowStyleMaskFullSizeContentView is not None:
                try:
                    current_mask = int(ns_window.styleMask())
                    full_size = int(NSWindowStyleMaskFullSizeContentView)
                    if (current_mask & full_size) == 0:
                        ns_window.setStyleMask_(current_mask | full_size)
                except Exception:
                    pass

            try:
                ns_window.setTitlebarAppearsTransparent_(True)
            except Exception:
                pass
            try:
                ns_window.setOpaque_(False)
            except Exception:
                pass
            try:
                if NSColor is not None and hasattr(ns_window, "setBackgroundColor_"):
                    ns_window.setBackgroundColor_(NSColor.clearColor())
            except Exception:
                pass

            try:
                if NSWindowTitleHidden is not None:
                    ns_window.setTitleVisibility_(int(NSWindowTitleHidden))
                else:
                    ns_window.setTitleVisibility_(1)
            except Exception:
                pass

            try:
                if NSWindowTitlebarSeparatorStyleNone is not None and hasattr(ns_window, "setTitlebarSeparatorStyle_"):
                    ns_window.setTitlebarSeparatorStyle_(int(NSWindowTitlebarSeparatorStyleNone))
            except Exception:
                pass

            try:
                ns_window.setMovableByWindowBackground_(True)
            except Exception:
                pass
            self._mac_titlebar_glass_applied = True
            return True
        except Exception:
            return False

    def setup_sidebar(self):
        self.sidebar = QWidget()
        annotate_widget(self.sidebar, role="Sidebar")
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setAutoFillBackground(False)
        if sys.platform == "darwin":
            self.sidebar.setAttribute(Qt.WA_TranslucentBackground, True)
        self.sidebar.setFixedWidth(252)
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(18, 18, 18, 14)
        self.sidebar_layout.setSpacing(6)

        self.nav_selection_highlight = QFrame(self.sidebar)
        annotate_widget(self.nav_selection_highlight, role="Navigation Highlight")
        self.nav_selection_highlight.setObjectName("NavSelectionHighlight")
        self.nav_selection_highlight.setStyleSheet(
            "QFrame#NavSelectionHighlight {"
            f"background-color: {self.nav_active_surface_color};"
            "border: none;"
            "border-top-left-radius: 12px;"
            "border-bottom-left-radius: 12px;"
            "border-top-right-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "}"
        )
        self.nav_selection_highlight.hide()
        self.nav_selection_highlight.lower()
        
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else self.devicePixelRatio()

        logo_label = QLabel()
        logo_label.setStyleSheet("background: transparent; border: none;")
        logo_label.hide()
        self._sidebar_logo_source = None

        wordmark_label = QLabel()
        wordmark_label.setStyleSheet("background: transparent; border: none;")
        wordmark_label.setAlignment(Qt.AlignCenter)
        wordmark_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixsplitr.png")
        wordmark_pixmap = QPixmap(wordmark_path)
        self._sidebar_wordmark_source = wordmark_pixmap if not wordmark_pixmap.isNull() else None
        if self._sidebar_wordmark_source is not None:
            wordmark_label.setFixedHeight(34)
            wordmark_label.setContentsMargins(0, 0, 0, 0)
            wordmark_label.setScaledContents(False)
        else:
            wordmark_label.setText("MixSplitR")
            self._track_scalable(wordmark_label, 22, "font-weight: 600; color: #EBE7E1;")

        header_row.addWidget(wordmark_label, 1, Qt.AlignVCenter | Qt.AlignCenter)

        header_container = QFrame()
        header_container.setLayout(header_row)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_container.setFixedHeight(40)
        header_container.setStyleSheet(
            "QFrame {"
            "  background: transparent;"
            "  border: none;"
            "}"
        )
        self.sidebar_layout.addWidget(header_container)
        self._sidebar_logo_label = logo_label
        self._sidebar_wordmark_label = wordmark_label
        self._sidebar_header_container = header_container
        self._update_sidebar_branding_scale()
        self.sidebar_layout.addSpacing(8)
        
        self.nav_buttons = []
        nav_items = [
            ("Splitter", 0),
            ("Identifier", 1),
            # Keep sidebar order stable while routing to actual stacked page indexes.
            ("Editor", 5),
            ("Recorder", 2),
            ("CD Ripping", 3),
            ("Session History", 4),
            ("Settings", 6),
            ("Help", 7),
        ]
        
        for text, index in nav_items:
            btn = RippleNavButton(text)
            btn.setProperty("class", "NavButton")
            btn.setProperty("page_index", int(index))
            btn.setCheckable(True)
            bold_family = str(_BUNDLED_ROBOTO_BOLD_FAMILY or "").strip()
            if bold_family:
                btn_font = QFont(btn.font())
                btn_font.setFamily(bold_family)
                btn_font.setStyleName("Bold")
                try:
                    btn_font.setWeight(QFont.Weight.Bold)
                except Exception:
                    btn_font.setWeight(700)
                btn.setFont(btn_font)
            # PySide can route either clicked() or clicked(bool) depending on
            # binding/runtime context; accept both to avoid dropped tab switches.
            btn.clicked.connect(lambda *_, idx=index: self.switch_page(idx))
            self.sidebar_layout.addWidget(btn)
            self.nav_buttons.append(btn)
            
        self.sidebar_layout.addStretch()
        current_version = str(getattr(mixsplitr_core, "CURRENT_VERSION", "8.0")).strip() or "8.0"
        self._sidebar_current_version_value = current_version
        kofi_url = str(
            getattr(mixsplitr_core, "KOFI_URL", "https://ko-fi.com/mixsplitr")
        ).strip() or "https://ko-fi.com/mixsplitr"
        footer_container = QWidget()
        footer_layout = QVBoxLayout(footer_container)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(2)
        self.sidebar_version_label = QLabel(
            f"<span>mixsplitr version {html.escape(current_version)} by </span>"
            f"<a href=\"{html.escape(kofi_url, quote=True)}\" "
            f"style=\"color: #D94A3A; text-decoration: none;\">chefkjd</a>"
        )
        self.sidebar_version_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.sidebar_version_label.setTextFormat(Qt.RichText)
        self.sidebar_version_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.sidebar_version_label.setOpenExternalLinks(True)
        self.sidebar_version_label.setStyleSheet(
            "color: #8B857C; font-size: 10px; padding-top: 0px;"
        )
        footer_layout.addWidget(self.sidebar_version_label)
        self.sidebar_update_label = QLabel("Checking updates...")
        self.sidebar_update_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.sidebar_update_label.setTextFormat(Qt.RichText)
        self.sidebar_update_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.sidebar_update_label.setOpenExternalLinks(True)
        self.sidebar_update_label.setStyleSheet(
            "color: #8B857C; font-size: 10px; padding-top: 0px;"
        )
        footer_layout.addWidget(self.sidebar_update_label)
        self.sidebar_layout.addWidget(footer_container, 0, Qt.AlignBottom)
        self.nav_buttons[0].setChecked(True)
        for btn in self.nav_buttons:
            if isinstance(btn, RippleNavButton):
                btn.set_selected_animated(self._nav_button_page_index(btn) == 0, animated=False)
        for btn in self.nav_buttons:
            btn.raise_()

    def _sidebar_current_version_text(self):
        stored = str(getattr(self, "_sidebar_current_version_value", "") or "").strip()
        if stored:
            return stored
        label = getattr(self, "sidebar_version_label", None)
        raw = str(label.text()).strip() if label is not None else ""
        prefix = "mixsplitr version"
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
        by_idx = raw.lower().find(" by ")
        if by_idx > 0:
            raw = raw[:by_idx].strip()
        if raw:
            return raw
        return str(getattr(mixsplitr_core, "CURRENT_VERSION", ""))

    def _start_sidebar_update_check(self):
        if not hasattr(self, "sidebar_update_label"):
            return
        if self._update_check_thread is not None and self._update_check_thread.isRunning():
            return

        self.sidebar_update_label.setStyleSheet(
            "color: #8B857C; font-size: 10px; padding-top: 0px;"
        )
        self.sidebar_update_label.setText("Checking updates...")
        thread = UpdateCheckThread(
            self._sidebar_current_version_text(),
            core_getter=lambda: mixsplitr_core,
            parent=self,
        )
        thread.result_ready.connect(self._handle_sidebar_update_check_result)
        thread.finished.connect(self._on_sidebar_update_check_finished)
        self._update_check_thread = thread
        thread.start()

    def _on_sidebar_update_check_finished(self):
        thread = self.sender()
        if thread is not None:
            try:
                thread.deleteLater()
            except Exception:
                pass
        if thread is self._update_check_thread:
            self._update_check_thread = None

    def _handle_sidebar_update_check_result(self, payload):
        if not hasattr(self, "sidebar_update_label"):
            return
        data = payload if isinstance(payload, dict) else {}
        status = str(data.get("status", "error")).strip().lower()
        latest = str(data.get("latest", "")).strip()
        current = str(data.get("current", "")).strip() or self._sidebar_current_version_text()
        release_page_url = str(data.get("release_page_url", "")).strip() or "https://github.com/chefkjd/MixSplitR/releases"

        if status == "update_available" and latest:
            # New release on GitHub — link directly to the releases page
            self.sidebar_update_label.setStyleSheet(
                "color: #8FB7FF; font-size: 10px; padding-top: 0px;"
            )
            self.sidebar_update_label.setText(
                f'<a href="{release_page_url}" style="color:#9DE0B1;">Update available: v{latest}</a>'
            )
            return

        if status == "ahead_of_release":
            # Local build is newer than latest GitHub release
            self.sidebar_update_label.setStyleSheet(
                "color: #B4AEA4; font-size: 10px; padding-top: 0px;"
            )
            self.sidebar_update_label.setText("Dev build \u2014 ahead of release")
            return

        if status == "up_to_date":
            # On the latest release — just show the current version
            self.sidebar_update_label.setStyleSheet(
                "color: #8B857C; font-size: 10px; padding-top: 0px;"
            )
            self.sidebar_update_label.setText(f"v{current}" if current else "Up to date")
            return

        # error / network failure — fall back to showing the current version quietly
        self.sidebar_update_label.setStyleSheet(
            "color: #8B857C; font-size: 10px; padding-top: 0px;"
        )
        self.sidebar_update_label.setText(f"v{current}" if current else "")

    def _update_sidebar_branding_scale(self):
        logo_label = getattr(self, "_sidebar_logo_label", None)
        wordmark_label = getattr(self, "_sidebar_wordmark_label", None)
        header_container = getattr(self, "_sidebar_header_container", None)
        if logo_label is None or wordmark_label is None or header_container is None:
            return

        wordmark_source = getattr(self, "_sidebar_wordmark_source", None)
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else self.devicePixelRatio()
        container_w = max(120, int(header_container.width()))
        content_w = max(80, int(container_w - 28))
        target_h = max(42, int(header_container.height()))

        # Icon is removed; hide it unconditionally.
        logo_label.hide()

        # Render the wordmark at a fixed large size (independent of cell
        # height).  It is layered on top and may be clipped by the cell.
        if wordmark_source is not None and not wordmark_source.isNull():
            logo_render_h = 110  # fixed — change this to resize the PNG
            draw_w = content_w - 4
            wordmark_px = wordmark_source.scaled(
                int(draw_w * dpr),
                int(logo_render_h * dpr),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            wordmark_px.setDevicePixelRatio(dpr)
            # Size the label to the full container so AlignCenter
            # places the pixmap dead-center within the cell.
            wordmark_label.setFixedSize(container_w, target_h)
            wordmark_label.setAlignment(Qt.AlignCenter)
            wordmark_label.setPixmap(wordmark_px)
            wordmark_label.show()

    def switch_page(self, index):
        if index < 0 or index >= self.stacked_widget.count():
            return
        current_index = int(self.stacked_widget.currentIndex())
        animate_page_transition = current_index != int(index)
        page = self.stacked_widget.widget(index)
        freeze_targets = self._page_reveal_freeze_targets(index=index)
        self._set_updates_enabled_for_widgets(freeze_targets, False)
        for btn in self.nav_buttons:
            selected = self._nav_button_page_index(btn) == int(index)
            btn.setChecked(selected)
            if isinstance(btn, RippleNavButton):
                btn.set_selected_animated(selected, animated=True)
        try:
            if animate_page_transition:
                # Prime the destination page before it becomes current so the
                # macOS/native first paint cannot flash the fully rendered
                # cards for a frame before the reveal animation takes over.
                self._prime_page_cards_for_reveal(index=index, require_geometry=False)
            self.stacked_widget.setCurrentIndex(index)
            # Keep page navigation resilient: visual chrome failures should not block
            # actual stacked-widget page changes in packaged/macOS edge cases.
            try:
                self._position_nav_highlight(index=index, animated=True)
                self._position_nav_connector(index=index, animated=True)
            except Exception:
                pass
            if hasattr(self, "page_reveal_overlay"):
                self.page_reveal_overlay.hide()
            # Force layout settlement before animating cards.  On macOS the
            # NSView hierarchy settles asynchronously, so widgets inside
            # newly-visible pages (especially TrackEditorPanel's splitter)
            # may still have zero geometry when _animate_page_cards captures
            # positions.  Activate the layout tree and process pending events
            # while updates are frozen so the page does not paint its final
            # state before the reveal starts.
            if page is not None:
                page.updateGeometry()
                if page.layout():
                    page.layout().activate()
                # Pages are wrapped in SmoothScrollArea — also activate the
                # inner GlassPage layout so nested widgets (TrackEditorPanel
                # splitter, cards) have valid geometry before animation.
                inner = getattr(page, "widget", lambda: None)()
                if inner is not None:
                    inner.updateGeometry()
                    if inner.layout():
                        inner.layout().activate()
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            if animate_page_transition:
                self._animate_page_cards(
                    index=index,
                    lead_delay_ms=int(getattr(self, "TAB_SWITCH_CARD_REVEAL_DELAY_MS", 45)),
                )
            else:
                self._clear_all_card_animation_artifacts()
        finally:
            self._set_updates_enabled_for_widgets(reversed(freeze_targets), True)
        if animate_page_transition and hasattr(self, "page_reveal_overlay"):
            try:
                self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
                self.page_reveal_overlay.prime(QPoint(0, 0), "#1D2024")
                self.page_reveal_overlay.play()
            except Exception:
                try:
                    self.page_reveal_overlay.hide()
                except Exception:
                    pass
        # On macOS, native NSViews settle asynchronously after the Qt
        # visibility transition.  Qt-level update()/repaint() only
        # invalidate the widget backing store — they don't force the
        # macOS compositor to redraw the native view tree.  We use two
        # complementary strategies:
        #  1. Scroll-nudge: shifts the viewport ±1px to force compositor
        #     redraw of the visible region (same path as manual scroll).
        #  2. NSView setNeedsDisplay_: marks every GlossyCardFrame's
        #     native view (and the GlassPage itself) as needing redraw,
        #     covering cards below the fold that the scroll nudge misses.
        if page is not None and sys.platform == "darwin":
            def _force_compositor_redraw(p=page):
                try:
                    scroll = p if isinstance(p, QScrollArea) else None
                    if scroll is None:
                        return
                    glass = scroll.widget()
                    # Scroll-nudge for visible viewport region.
                    bar = scroll.verticalScrollBar()
                    if bar is not None:
                        val = bar.value()
                        bar.setValue(val + 1)
                        bar.setValue(val)
                    # Invalidate GlassPage punchthrough path.
                    if isinstance(glass, GlassPage):
                        glass._punchthrough_path_dirty = True
                        glass.update()
                    # Force every native NSView in the page tree to
                    # redraw via the macOS compositor.  This catches
                    # cards that are below the visible viewport where
                    # the scroll nudge has no effect.  Also retry blur
                    # setup for cards where it failed on first show
                    # (superview was still None when the NSView tree
                    # was being constructed).
                    if glass is not None:
                        for card in glass.findChildren(GlossyCardFrame):
                            skip_native_refresh = bool(card.property("disableMacBlur"))
                            if hasattr(card, "_setup_mac_blur_if_available"):
                                try:
                                    card._setup_mac_blur_if_available()
                                    card._sync_mac_blur_geometry()
                                except Exception:
                                    pass
                            if objc is not None and not skip_native_refresh:
                                try:
                                    ns = objc.objc_object(
                                        c_void_p=int(card.winId())
                                    )
                                    ns.setNeedsDisplay_(True)
                                except Exception:
                                    pass
                        if objc is not None:
                            try:
                                ns_glass = objc.objc_object(
                                    c_void_p=int(glass.winId())
                                )
                                ns_glass.setNeedsDisplay_(True)
                            except Exception:
                                pass
                except Exception:
                    pass
                # Re-raise titlebar overlays after NSView operations that can
                # reset the macOS compositor z-order (winId(), setNeedsDisplay_).
                self._raise_titlebar_overlays_deferred()
            for delay in (80, 250, 500):
                QTimer.singleShot(delay, _force_compositor_redraw)
            # Mirror the same recovery flow that happens after focus/state
            # changes. This keeps initial page-open rendering consistent with
            # the "switch away and back" compositor refresh behavior.
            QTimer.singleShot(0, self._recover_after_window_state_change)
            QTimer.singleShot(120, self._recover_after_window_state_change)
            QTimer.singleShot(260, self._recover_after_window_state_change)
            # Immediate re-raise for the page switch itself, before the
            # deferred compositor redraws fire.
            QTimer.singleShot(0, self._raise_titlebar_overlays_deferred)

    def _nav_button_page_index(self, button):
        if button is None:
            return -1
        raw_index = button.property("page_index")
        if raw_index is None:
            return -1
        try:
            return int(raw_index)
        except Exception:
            return -1

    def _nav_button_for_index(self, index):
        if index is None:
            return None
        target = int(index)
        for btn in getattr(self, "nav_buttons", []):
            if self._nav_button_page_index(btn) == target:
                return btn
        if 0 <= target < len(self.nav_buttons):
            # Backward-compatible fallback if page_index is missing.
            return self.nav_buttons[target]
        return None

    def _setup_sidebar_blur_if_available(self):
        if not ENABLE_SIDEBAR_VIBRANCY:
            return False
        if sys.platform != "darwin":
            return False
        if not MACOS_VIBRANCY_AVAILABLE or objc is None or NSVisualEffectView is None:
            return False
        if self._ns_sidebar_vibrancy_view is not None:
            self._sync_sidebar_blur_geometry()
            return True
        try:
            _ = int(self.winId())
            _ = int(self.sidebar.winId())
            ns_sidebar_view = objc.objc_object(c_void_p=int(self.sidebar.winId()))
            ns_parent = ns_sidebar_view.superview()
            if ns_parent is None:
                return False

            effect = NSVisualEffectView.alloc().initWithFrame_(ns_sidebar_view.frame())
            effect.setAutoresizingMask_(int(NSViewHeightSizable))
            effect.setMaterial_(NSVisualEffectMaterialSidebar)
            effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(NSVisualEffectStateActive)
            ns_parent.addSubview_positioned_relativeTo_(effect, int(NSWindowBelow), ns_sidebar_view)
            self._ns_sidebar_vibrancy_view = effect
            self._ns_sidebar_native_view = ns_sidebar_view
            self.sidebar_blur_enabled = True
            self._sync_sidebar_blur_geometry()
            return True
        except Exception:
            self._ns_sidebar_vibrancy_view = None
            self._ns_sidebar_native_view = None
            self.sidebar_blur_enabled = False
            return False

    def _sync_sidebar_blur_geometry(self):
        if not self.sidebar_blur_enabled:
            return
        effect = self._ns_sidebar_vibrancy_view
        if effect is None:
            return
        try:
            ns_sidebar_view = self._ns_sidebar_native_view
            if ns_sidebar_view is None:
                ns_sidebar_view = objc.objc_object(c_void_p=int(self.sidebar.winId()))
                self._ns_sidebar_native_view = ns_sidebar_view
            effect.setFrame_(ns_sidebar_view.frame())
            effect.setHidden_(False)
        except Exception:
            self.sidebar_blur_enabled = False
            self._ns_sidebar_vibrancy_view = None
            self._ns_sidebar_native_view = None

    def _setup_content_blur_if_available(self):
        if not ENABLE_NATIVE_VIBRANCY:
            return False
        if sys.platform != "darwin":
            return False
        if not MACOS_VIBRANCY_AVAILABLE or objc is None or NSVisualEffectView is None:
            return False
        if self._ns_content_vibrancy_view is not None:
            self._sync_content_blur_geometry()
            return True
        try:
            _ = int(self.winId())
            _ = int(self.content_container.winId())
            ns_content_view = objc.objc_object(c_void_p=int(self.content_container.winId()))
            ns_parent = ns_content_view.superview()
            if ns_parent is None:
                return False

            effect = NSVisualEffectView.alloc().initWithFrame_(ns_content_view.frame())
            effect.setAutoresizingMask_(int(NSViewWidthSizable | NSViewHeightSizable))
            effect.setMaterial_(NSVisualEffectMaterialSidebar)
            effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(NSVisualEffectStateActive)
            ns_parent.addSubview_positioned_relativeTo_(effect, int(NSWindowBelow), ns_content_view)
            self._ns_content_vibrancy_view = effect
            self._ns_content_native_view = ns_content_view
            self.content_blur_enabled = True
            self._sync_content_blur_geometry()
            return True
        except Exception:
            self._ns_content_vibrancy_view = None
            self._ns_content_native_view = None
            self.content_blur_enabled = False
            return False

    def _sync_content_blur_geometry(self):
        if not self.content_blur_enabled:
            return
        effect = self._ns_content_vibrancy_view
        if effect is None:
            return
        try:
            ns_content_view = self._ns_content_native_view
            if ns_content_view is None:
                ns_content_view = objc.objc_object(c_void_p=int(self.content_container.winId()))
                self._ns_content_native_view = ns_content_view
            effect.setFrame_(ns_content_view.frame())
            effect.setHidden_(False)
        except Exception:
            self.content_blur_enabled = False
            self._ns_content_vibrancy_view = None
            self._ns_content_native_view = None

    def _enable_windows_blur(self):
        """Leave the window shell opaque and native."""
        return

    def _target_nav_connector_rect(self, index=None):
        button = self._nav_button_for_index(index if index is not None else self.stacked_widget.currentIndex())
        if button is None:
            return QRect()
        button_top_left = button.mapTo(self.centralWidget(), QPoint(0, 0))
        button_rect = QRect(button_top_left, button.size())
        # Slight 1px overlap prevents fractional-scale seams (seen on Windows)
        # so tab + connector always read as one continuous dark surface.
        x = int(button_rect.right())
        y = int(button_rect.top())
        height = int(button_rect.height())
        content_left = self.content_container.mapTo(self.centralWidget(), QPoint(0, 0)).x()
        # Extend into the page's dark gutter so the active tab reads as one
        # continuous bar emerging from the content background.
        connector_end = int(content_left + 36)
        width = max(10, connector_end - x + 1)
        central_height = max(1, self.centralWidget().height())
        y = max(0, min(central_height - height, y))
        return QRect(x, y, width, height)

    def _target_nav_highlight_rect(self, index=None):
        button = self._nav_button_for_index(index if index is not None else self.stacked_widget.currentIndex())
        if button is None:
            return QRect()
        rect = QRect(button.geometry())
        return rect

    def _interpolate_channel(self, start, end, ratio):
        return int(round(float(start) + (float(end) - float(start)) * float(ratio)))

    def _sync_nav_surface_style(self):
        if not hasattr(self, "nav_selection_highlight") or not hasattr(self, "nav_page_connector"):
            return
        strip_color = "#000000" if self.use_invisible_macos_drag_strip else self.nav_active_surface_color
        fade_color = "#000000" if self.use_invisible_macos_drag_strip else self.nav_active_surface_color
        if hasattr(self, "titlebar_content_spacer") and hasattr(self.titlebar_content_spacer, "set_base_color"):
            self.titlebar_content_spacer.set_base_color(strip_color)
        if hasattr(self, "titlebar_fade_overlay") and hasattr(self.titlebar_fade_overlay, "set_base_color"):
            self.titlebar_fade_overlay.set_base_color(fade_color)
            if self._titlebar_drag_strip_height > 0 and getattr(self, "_titlebar_fade_height", 0) > 0:
                self.titlebar_fade_overlay.show()
        if not self.show_nav_selection_surface:
            self.nav_selection_highlight.hide()
            self.nav_page_connector.hide()
            return

        rect = self.nav_selection_highlight.geometry()
        if not rect.isValid() or rect.height() <= 0:
            rect = self._target_nav_highlight_rect(self.stacked_widget.currentIndex())
        if not rect.isValid() or rect.height() <= 0:
            return

        style_key = self.nav_active_surface_color
        if self._nav_surface_style_key == style_key:
            return
        self._nav_surface_style_key = style_key

        # Keep selected-tab surface fixed to the content dark background color.
        base_css = f"background-color: {self.nav_active_surface_color}; border: none;"
        self.nav_selection_highlight.setStyleSheet(
            "QFrame#NavSelectionHighlight {"
            f"{base_css}"
            "border-top-left-radius: 12px;"
            "border-bottom-left-radius: 12px;"
            "border-top-right-radius: 0px;"
            "border-bottom-right-radius: 0px;"
            "}"
        )
        self.nav_page_connector.setStyleSheet(
            "QFrame#NavPageConnector {"
            f"{base_css}"
            "border-top-left-radius: 0px;"
            "border-bottom-left-radius: 0px;"
            "border-top-right-radius: 12px;"
            "border-bottom-right-radius: 12px;"
            "}"
        )

    def _position_nav_connector(self, index=None, animated=True):
        if not hasattr(self, "nav_page_connector"):
            return
        if not self.show_nav_selection_surface:
            self.nav_connector_anim.stop()
            self.nav_page_connector.hide()
            return
        target = self._target_nav_connector_rect(index=index)
        if target.isNull():
            self.nav_page_connector.hide()
            return
        self.nav_page_connector.show()
        self.nav_page_connector.raise_()
        if animated and self.nav_page_connector.geometry().isValid():
            self.nav_connector_anim.stop()
            self.nav_connector_anim.setStartValue(self.nav_page_connector.geometry())
            self.nav_connector_anim.setEndValue(target)
            self.nav_connector_anim.start()
        else:
            self.nav_page_connector.setGeometry(target)
        self._sync_nav_surface_style()

    def _position_nav_highlight(self, index=None, animated=True):
        if not hasattr(self, "nav_selection_highlight"):
            return
        if not self.show_nav_selection_surface:
            self.nav_highlight_anim.stop()
            self.nav_selection_highlight.hide()
            return
        target = self._target_nav_highlight_rect(index=index)
        if target.isNull():
            self.nav_selection_highlight.hide()
            return
        self.nav_selection_highlight.show()
        self.nav_selection_highlight.lower()
        for btn in getattr(self, "nav_buttons", []):
            btn.raise_()
        if animated and self.nav_selection_highlight.geometry().isValid():
            self.nav_highlight_anim.stop()
            self.nav_highlight_anim.setStartValue(self.nav_selection_highlight.geometry())
            self.nav_highlight_anim.setEndValue(target)
            self.nav_highlight_anim.start()
        else:
            self.nav_selection_highlight.setGeometry(target)
        self._sync_nav_surface_style()

    def _startup_reveal_cards_for_page(self, index=None, require_geometry=False):
        try:
            page_index = int(index if index is not None else self.stacked_widget.currentIndex())
        except Exception:
            return []
        page = self.stacked_widget.widget(page_index) if hasattr(self, "stacked_widget") else None
        if page is None:
            return []

        root = getattr(page, "widget", lambda: None)()
        if root is None:
            root = page

        cards = []
        for widget in root.findChildren(QFrame):
            if not bool(widget.property("is_card")):
                continue
            parent = widget.parentWidget()
            nested_inside_card = False
            while parent is not None and parent is not root:
                if bool(parent.property("is_card")):
                    nested_inside_card = True
                    break
                parent = parent.parentWidget()
            if nested_inside_card:
                continue
            if require_geometry and (widget.width() <= 0 or widget.height() <= 0):
                continue
            cards.append(widget)
        cards.sort(key=lambda widget: widget.geometry().top())
        return cards

    def _animation_cards_for_page(self, index=None, require_geometry=True, require_visible=True):
        try:
            page_index = int(index if index is not None else self.stacked_widget.currentIndex())
        except Exception:
            return []
        page = self.stacked_widget.widget(page_index) if hasattr(self, "stacked_widget") else None
        if page is None:
            return []

        def _should_include(widget):
            if not bool(widget.property("is_card")):
                return False
            if require_visible:
                return widget.isVisible()
            return widget.isVisibleTo(page)

        # Pages are wrapped in SmoothScrollArea, so page.widget() is the
        # GlassPage. On macOS, limit reveal effects to direct GlassPage cards
        # to avoid native-child redraw artifacts inside nested panels.
        glass_page = getattr(page, "widget", lambda: None)()
        if sys.platform == "darwin" and glass_page is not None:
            cards = [
                widget
                for widget in glass_page.findChildren(QFrame, options=Qt.FindDirectChildrenOnly)
                if _should_include(widget)
            ]
        else:
            cards = [
                widget
                for widget in page.findChildren(QFrame)
                if _should_include(widget)
            ]
        if require_geometry:
            cards = [card for card in cards if card.width() > 0 and card.height() > 0]
        cards.sort(key=lambda widget: widget.geometry().top())
        return cards

    def _prime_page_cards_for_reveal(self, index=None, require_geometry=False):
        cards = self._startup_reveal_cards_for_page(index=index, require_geometry=require_geometry)
        if not cards:
            return []

        for card in cards:
            target_pos = card.property("_anim_target_pos")
            if isinstance(target_pos, QPoint):
                try:
                    card.move(target_pos)
                except Exception:
                    pass
            card.setProperty("_anim_target_pos", None)
            try:
                card.show()
            except Exception:
                pass
            card.setProperty("_startup_reveal_hidden", True)

            effect = card.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                if effect is not None:
                    try:
                        if card.graphicsEffect() is effect:
                            card.setGraphicsEffect(None)
                        effect.deleteLater()
                    except Exception:
                        pass
                effect = QGraphicsOpacityEffect(card)
                card.setGraphicsEffect(effect)
            effect.setEnabled(True)
            effect.setOpacity(0.0)

        return cards

    def _prime_initial_page_cards_for_reveal(self):
        if getattr(self, "_initial_page_cards_primed", False):
            return
        cards = self._prime_page_cards_for_reveal(require_geometry=False)
        if not cards:
            return

        self._initial_page_cards_primed = True

    def _start_initial_page_reveal_when_ready(self):
        if getattr(self, "_initial_page_reveal_done", False):
            return
        if not self.isVisible():
            QTimer.singleShot(24, self._start_initial_page_reveal_when_ready)
            return

        current_index = int(self.stacked_widget.currentIndex()) if hasattr(self, "stacked_widget") else 0
        reveal_cards = self._startup_reveal_cards_for_page(index=current_index, require_geometry=True)
        needs_splitter_fit = (
            sys.platform in ("win32", "darwin")
            and current_index == 0
            and not bool(getattr(self, "_splitter_fit_applied", False))
        )
        if (needs_splitter_fit or not reveal_cards) and getattr(self, "_initial_page_reveal_retry_count", 0) < 8:
            self._initial_page_reveal_retry_count += 1
            QTimer.singleShot(28, self._start_initial_page_reveal_when_ready)
            return

        self._initial_page_reveal_done = True
        self._initial_page_reveal_retry_count = 0
        if not reveal_cards:
            self._clear_all_card_animation_artifacts()
            self._initial_page_cards_primed = False
            return

        freeze_targets = self._page_reveal_freeze_targets(index=current_index)
        self._set_updates_enabled_for_widgets(freeze_targets, False)
        try:
            page = self.stacked_widget.widget(current_index)
            if page is not None:
                page.updateGeometry()
                if page.layout():
                    page.layout().activate()
                inner = getattr(page, "widget", lambda: None)()
                if inner is not None:
                    inner.updateGeometry()
                    if inner.layout():
                        inner.layout().activate()
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            self._animate_page_cards(index=current_index, lead_delay_ms=20)
        finally:
            self._set_updates_enabled_for_widgets(reversed(freeze_targets), True)

        if hasattr(self, "page_reveal_overlay"):
            try:
                self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
                self.page_reveal_overlay.prime(QPoint(0, 0), "#1D2024")
                self.page_reveal_overlay.play()
            except Exception:
                try:
                    self.page_reveal_overlay.hide()
                except Exception:
                    pass
        self._initial_page_cards_primed = False

    def _page_reveal_freeze_targets(self, index=None):
        freeze_targets = []
        freeze_target_ids = set()
        if not hasattr(self, "stacked_widget"):
            return freeze_targets

        try:
            page_index = int(index if index is not None else self.stacked_widget.currentIndex())
        except Exception:
            return freeze_targets
        page = self.stacked_widget.widget(page_index)
        if page is None:
            return freeze_targets

        def _append_freeze_target(widget):
            if widget is None:
                return
            widget_id = id(widget)
            if widget_id in freeze_target_ids:
                return
            freeze_target_ids.add(widget_id)
            freeze_targets.append(widget)

        _append_freeze_target(page)
        if hasattr(page, "viewport"):
            _append_freeze_target(page.viewport())
        inner = getattr(page, "widget", lambda: None)()
        if inner is not None:
            _append_freeze_target(inner)
            if hasattr(inner, "viewport"):
                _append_freeze_target(inner.viewport())
        return freeze_targets

    def _set_updates_enabled_for_widgets(self, widgets, enabled):
        for widget in widgets:
            try:
                widget.setUpdatesEnabled(bool(enabled))
                if enabled:
                    widget.update()
            except Exception:
                pass

    def _animate_page_cards(self, index=None, lead_delay_ms=0):
        try:
            page_index = int(index if index is not None else self.stacked_widget.currentIndex())
        except Exception:
            return
        page = self.stacked_widget.widget(page_index) if hasattr(self, "stacked_widget") else None
        if page is None:
            return

        self._card_fade_token += 1
        token = self._card_fade_token
        for anim in list(self._card_fade_animations):
            try:
                anim.stop()
            except Exception:
                pass
        self._card_fade_animations = []
        self._clear_all_card_animation_artifacts()

        cards = self._animation_cards_for_page(index=page_index, require_geometry=True, require_visible=False)
        if not cards:
            return

        for order, card in enumerate(cards):
            target_pos = QPoint(card.pos())
            start_pos = QPoint(target_pos.x(), target_pos.y() + 10)
            card.setProperty("_anim_target_pos", QPoint(target_pos))

            effect = card.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                if effect is not None:
                    try:
                        if card.graphicsEffect() is effect:
                            card.setGraphicsEffect(None)
                        effect.deleteLater()
                    except Exception:
                        pass
                effect = QGraphicsOpacityEffect(card)
                card.setGraphicsEffect(effect)
            effect.setEnabled(True)
            effect.setOpacity(0.0)

            try:
                card.move(start_pos)
            except Exception:
                pass
            try:
                card.show()
            except Exception:
                pass
            card.setProperty("_startup_reveal_hidden", False)

            position_anim = QPropertyAnimation(card, b"pos", self)
            position_anim.setDuration(390)
            position_anim.setStartValue(start_pos)
            position_anim.setEndValue(target_pos)
            position_anim.setEasingCurve(QEasingCurve.OutCubic)

            opacity_anim = QPropertyAnimation(effect, b"opacity", self)
            opacity_anim.setDuration(270)
            opacity_anim.setStartValue(0.0)
            opacity_anim.setEndValue(1.0)
            opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

            anim = QParallelAnimationGroup(self)
            anim.addAnimation(position_anim)
            anim.addAnimation(opacity_anim)
            anim.finished.connect(
                lambda c=card, e=effect, p=QPoint(target_pos): self._finalize_card_animation(c, e, p)
            )
            self._card_fade_animations.append(anim)

            delay_ms = max(0, int(lead_delay_ms)) + (56 * order)
            QTimer.singleShot(
                delay_ms,
                lambda a=anim, e=effect, c=card, p=QPoint(target_pos), expected_token=token, expected_index=page_index: self._start_card_fade_animation(
                    a, e, c, p, expected_token, expected_index
                ),
            )

    def _start_card_fade_animation(self, animation, effect, card, end_pos, expected_token, expected_index):
        if expected_token != self._card_fade_token:
            self._finalize_card_animation(card, effect, end_pos)
            return
        if self.stacked_widget.currentIndex() != expected_index:
            self._finalize_card_animation(card, effect, end_pos)
            return
        animation.start()

    def _clear_all_card_animation_artifacts(self):
        if not hasattr(self, "stacked_widget"):
            return
        for page_index in range(self.stacked_widget.count()):
            page = self.stacked_widget.widget(page_index)
            if page is None:
                continue
            cards = [
                widget
                for widget in page.findChildren(QFrame)
                if bool(widget.property("is_card"))
            ]
            for card in cards:
                target_pos = card.property("_anim_target_pos")
                if isinstance(target_pos, QPoint):
                    try:
                        card.move(target_pos)
                    except Exception:
                        pass
                if bool(card.property("_startup_reveal_hidden")):
                    try:
                        card.show()
                    except Exception:
                        pass
                effect = card.graphicsEffect()
                if isinstance(effect, QGraphicsOpacityEffect):
                    try:
                        effect.setOpacity(1.0)
                        effect.setEnabled(False)
                    except Exception:
                        pass
                    if card.graphicsEffect() is effect:
                        try:
                            card.setGraphicsEffect(None)
                        except Exception:
                            pass
                    try:
                        effect.deleteLater()
                    except Exception:
                        pass
                card.setProperty("_startup_reveal_hidden", False)
                card.setProperty("_anim_target_pos", None)

    def _finalize_card_animation(self, card, effect, end_pos):
        try:
            if isinstance(end_pos, QPoint):
                card.move(end_pos)
        except Exception:
            pass
        if isinstance(effect, QGraphicsOpacityEffect):
            try:
                effect.setOpacity(1.0)
                effect.setEnabled(False)
            except Exception:
                pass
            try:
                if card.graphicsEffect() is effect:
                    card.setGraphicsEffect(None)
            except Exception:
                pass
            try:
                effect.deleteLater()
            except Exception:
                pass
        try:
            card.show()
        except Exception:
            pass
        try:
            card.setProperty("_startup_reveal_hidden", False)
        except Exception:
            pass
        try:
            card.setProperty("_anim_target_pos", None)
        except Exception:
            pass

    def _configure_scroll_top_fade_mask(self, scroll):
        if scroll is None:
            return
        viewport = scroll.viewport() if hasattr(scroll, "viewport") else None
        if viewport is None:
            return
        current_effect = viewport.graphicsEffect()
        # Viewport-level graphics effects can hide native child widgets on macOS.
        # Keep viewport effect-free and do per-card alpha fades instead.
        if isinstance(current_effect, TopFadeMaskEffect):
            viewport.setGraphicsEffect(None)
            try:
                current_effect.deleteLater()
            except Exception:
                pass

    def _update_content_stack_fade_mask(self):
        stack = getattr(self, "stacked_widget", None)
        if stack is None:
            return
        current = stack.graphicsEffect()
        # Do not mask stacked_widget directly: native child views can blank out
        # on macOS when an ancestor QGraphicsOpacityEffect is applied.
        if isinstance(current, QGraphicsOpacityEffect):
            current.setOpacity(1.0)
            current.setOpacityMask(QBrush())
            stack.setGraphicsEffect(None)
            try:
                current.deleteLater()
            except Exception:
                pass

    def _sync_page_top_fade_zone_for_scroll(self, scroll):
        if scroll is None:
            return
        page = scroll.widget() if hasattr(scroll, "widget") else None
        if page is None or not hasattr(page, "set_top_fade_zone"):
            return
        if not self.use_invisible_macos_drag_strip:
            try:
                page.set_top_fade_zone(0.0, 0.0, 0.0, 1.0)
            except Exception:
                pass
            return

        clear_h = float(getattr(self, "_titlebar_drag_strip_height", 0)) + float(
            getattr(self, "scroll_top_fade_cutoff_offset", 0)
        )
        clear_h = max(0.0, clear_h)
        fade_h = float(max(1.0, getattr(self, "scroll_top_fade_height", 1)))
        fade_strength = float(max(1.0, getattr(self, "scroll_top_fade_strength", 1.0)))
        # On macOS, use the scroll bar value for page offset.  valueChanged
        # fires before scrollContentsBy repositions the widget, so
        # page.pos().y() is still the pre-scroll position at this point.
        # The scroll bar value is already current.
        if sys.platform == "darwin":
            page_offset_y = float(-scroll.verticalScrollBar().value())
        else:
            page_offset_y = float(page.pos().y())
        try:
            page.set_top_fade_zone(clear_h, fade_h, page_offset_y, fade_strength)
        except Exception:
            pass

    def _update_card_fade_for_scroll(self, scroll):
        if scroll is None:
            return
        page = scroll.widget() if hasattr(scroll, "widget") else None
        viewport = scroll.viewport() if hasattr(scroll, "viewport") else None
        if page is None or viewport is None:
            return

        self._sync_page_top_fade_zone_for_scroll(scroll)
        self._update_content_stack_fade_mask()

        # Fade all top-level page layers (master shell + cards + top labels).
        base_targets = [
            widget
            for widget in page.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
            if widget.isVisible() and widget.height() > 0 and widget.width() > 0
        ]
        targets = []
        seen_target_ids = set()

        def _append_target(widget):
            if widget is None:
                return
            try:
                if not widget.isVisible() or widget.height() <= 0 or widget.width() <= 0:
                    return
            except Exception:
                return
            wid = id(widget)
            if wid in seen_target_ids:
                return
            seen_target_ids.add(wid)
            targets.append(widget)

        for widget in base_targets:
            _append_target(widget)

        # TrackEditorPanel may be nested inside a workspace card. Collect its
        # internal fade targets explicitly so they always dissolve under the
        # drag-strip/transition zone regardless of page layout refactors.
        track_editor_panels = [
            panel
            for panel in page.findChildren(TrackEditorPanel)
            if panel.isVisible() and panel.height() > 0 and panel.width() > 0
        ]
        for panel in track_editor_panels:
            _append_target(panel)
            nested_cards = [
                card
                for card in panel.findChildren(QFrame, options=Qt.FindDirectChildrenOnly)
                if bool(card.property("is_card"))
                and card.isVisible()
                and card.height() > 0
                and card.width() > 0
            ]
            for card in nested_cards:
                _append_target(card)
            # Track editor internals that opt into top-fade handling.
            # Exclude scroll viewports directly to avoid stale item paints.
            for layer in panel.findChildren(QWidget):
                if not bool(layer.property("trackEditorFadeLayer")):
                    continue
                if not layer.isVisible() or layer.height() <= 0 or layer.width() <= 0:
                    continue
                parent = layer.parentWidget()
                if (
                    isinstance(parent, QAbstractScrollArea)
                    and hasattr(parent, "viewport")
                    and parent.viewport() is layer
                ):
                    continue
                _append_target(layer)

        def _target_top_in_page(target_widget):
            # Prefer parent-chain y accumulation over mapTo() for native views.
            y = float(target_widget.y())
            parent = target_widget.parentWidget()
            while parent is not None and parent is not page:
                y += float(parent.y())
                parent = parent.parentWidget()
            if parent is page:
                return y
            try:
                return float(target_widget.mapTo(page, QPoint(0, 0)).y())
            except Exception:
                return y

        if not self.use_invisible_macos_drag_strip:
            if hasattr(page, "set_top_fade_zone"):
                try:
                    page.set_top_fade_zone(0.0, 0.0, 0.0, 1.0)
                except Exception:
                    pass
            for target in targets:
                if target.property("_anim_target_pos") is not None or bool(target.property("_startup_reveal_hidden")):
                    continue
                effect = target.graphicsEffect()
                if isinstance(effect, QGraphicsOpacityEffect):
                    effect.setOpacity(1.0)
                    effect.setOpacityMask(QBrush())
                    effect.setEnabled(False)
                    if target.graphicsEffect() is effect:
                        target.setGraphicsEffect(None)
                    try:
                        effect.deleteLater()
                    except Exception:
                        pass
                target.setProperty("_top_fade_state", "visible")
            return

        clear_h = float(getattr(self, "_titlebar_drag_strip_height", 0)) + float(
            getattr(self, "scroll_top_fade_cutoff_offset", 0)
        )
        clear_h = max(0.0, clear_h)
        fade_h = float(max(1.0, getattr(self, "scroll_top_fade_height", 1)))
        fade_strength = float(max(1.0, getattr(self, "scroll_top_fade_strength", 1.0)))
        # On macOS, use the scroll bar value — same reason as in
        # _sync_page_top_fade_zone_for_scroll: the signal fires before
        # scrollContentsBy moves the widget, so page.pos() is stale.
        if sys.platform == "darwin" and scroll is not None:
            page_offset_y = float(-scroll.verticalScrollBar().value())
        else:
            page_offset_y = float(page.pos().y())
        fade_end = clear_h + fade_h

        for target in targets:
            if target.property("_anim_target_pos") is not None or bool(target.property("_startup_reveal_hidden")):
                continue
            # Direct-child geometry is more stable than mapTo() for native
            # child views on macOS during long scroll ranges.
            try:
                top_inset = max(0.0, float(target.property("topFadeInsetPx") or 0.0))
            except Exception:
                top_inset = 0.0
            top = page_offset_y + _target_top_in_page(target) - top_inset
            h = max(1.0, float(target.height()))
            bottom = top + h
            effect = target.graphicsEffect()
            if effect is not None and not isinstance(effect, QGraphicsOpacityEffect):
                # Preserve non-opacity effects (if any) and skip fading this target.
                continue

            # ── macOS: skip per-card QGraphicsOpacityEffect entirely ──
            # On macOS, per-card fade via QGraphicsOpacityEffect causes:
            #   1) rounded-corner clipping (offscreen rectangular pixmap)
            #   2) one-frame lag (async update vs Core Animation)
            #   3) scroll performance hit (offscreen render per card)
            # The shell's DestinationIn fade in GlassPage.paintEvent already
            # handles the top-zone visual transition, so cards don't need
            # their own fade.  Just clean up any leftover effects.
            if sys.platform == "darwin":
                if isinstance(effect, QGraphicsOpacityEffect):
                    target.setGraphicsEffect(None)
                    try:
                        effect.deleteLater()
                    except Exception:
                        pass
                continue

            # ── Non-macOS path: keep QGraphicsOpacityEffect ──
            if bottom <= clear_h:
                if not isinstance(effect, QGraphicsOpacityEffect):
                    effect = QGraphicsOpacityEffect(target)
                    target.setGraphicsEffect(effect)
                state = target.property("_top_fade_state")
                if state == "hidden":
                    continue
                effect.setEnabled(True)
                effect.setOpacityMask(QBrush())
                effect.setOpacity(0.0)
                target.setProperty("_top_fade_state", "hidden")
                continue
            if top >= fade_end:
                if isinstance(effect, QGraphicsOpacityEffect):
                    effect.setOpacity(1.0)
                    effect.setOpacityMask(QBrush())
                    effect.setEnabled(False)
                target.setProperty("_top_fade_state", "visible")
                continue

            local_clear = max(0.0, min(1.0, (clear_h - top) / h))
            local_end = max(0.0, min(1.0, (fade_end - top) / h))
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(target)
                target.setGraphicsEffect(effect)
            fade_state = f"fade:{local_clear:.4f}:{local_end:.4f}:{fade_strength:.3f}:{int(h)}"
            if target.property("_top_fade_state") == fade_state:
                continue
            effect.setEnabled(True)
            grad = QLinearGradient(0.0, 0.0, 0.0, h)
            if local_end <= local_clear + 1e-4:
                alpha = 255 if top >= fade_end else 0
                grad.setColorAt(0.0, QColor(0, 0, 0, alpha))
                grad.setColorAt(1.0, QColor(0, 0, 0, alpha))
            else:
                mid = local_clear + ((local_end - local_clear) * 0.58)
                mid_alpha = int(round(255.0 * (0.58 ** fade_strength)))
                grad.setColorAt(0.0, QColor(0, 0, 0, 0))
                grad.setColorAt(local_clear, QColor(0, 0, 0, 0))
                grad.setColorAt(max(0.0, min(1.0, mid)), QColor(0, 0, 0, max(0, min(255, mid_alpha))))
                grad.setColorAt(local_end, QColor(0, 0, 0, 255))
                grad.setColorAt(1.0, QColor(0, 0, 0, 255))
            effect.setOpacity(1.0)
            effect.setOpacityMask(QBrush(grad))
            target.setProperty("_top_fade_state", fade_state)

    def _refresh_scroll_top_fade_masks(self):
        self._update_content_stack_fade_mask()
        for scroll in self.findChildren(SmoothScrollArea):
            self._configure_scroll_top_fade_mask(scroll)
            self._update_card_fade_for_scroll(scroll)

    def _is_track_editor_scroll(self, scroll):
        try:
            return bool(
                scroll is not None
                and hasattr(self, "stacked_widget")
                and hasattr(self, "track_editor_page_index")
                and self.stacked_widget.widget(int(self.track_editor_page_index)) is scroll
            )
        except Exception:
            return False

    def _refresh_macos_track_editor_compositor(self, scroll, force=False):
        if sys.platform != "darwin":
            return
        if not self._is_track_editor_scroll(scroll):
            return
        now = time.monotonic()
        last = float(getattr(self, "_track_editor_scroll_refresh_ts", 0.0))
        min_interval = 0.12 if not force else 0.0
        if (now - last) < min_interval:
            return
        self._track_editor_scroll_refresh_ts = now
        try:
            glass = scroll.widget() if hasattr(scroll, "widget") else None
        except Exception:
            glass = None
        if isinstance(glass, GlassPage):
            try:
                glass._punchthrough_path_dirty = True
            except Exception:
                pass
            try:
                glass.update()
            except Exception:
                pass
        if objc is not None and glass is not None:
            try:
                ns_glass = objc.objc_object(c_void_p=int(glass.winId()))
                ns_glass.setNeedsDisplay_(True)
            except Exception:
                pass
            for card in glass.findChildren(GlossyCardFrame):
                try:
                    card._setup_mac_blur_if_available()
                    card._sync_mac_blur_geometry()
                except Exception:
                    pass
                if bool(card.property("disableMacBlur")):
                    continue
                try:
                    ns_card = objc.objc_object(c_void_p=int(card.winId()))
                    ns_card.setNeedsDisplay_(True)
                except Exception:
                    pass
        self._raise_titlebar_overlays_deferred()

    def _schedule_card_fade_for_scroll(self, scroll):
        if scroll is None:
            return
        # Keep shell-top fade aligned with the viewport on every scroll tick.
        # Per-target opacity masks remain coalesced below for performance.
        self._sync_page_top_fade_zone_for_scroll(scroll)
        if sys.platform == "darwin":
            self._update_card_fade_for_scroll(scroll)
            self._raise_titlebar_overlays_deferred()
            try:
                if self._is_track_editor_scroll(scroll):
                    self._refresh_macos_track_editor_compositor(scroll, force=False)
                    deferred = getattr(self, "_track_editor_scroll_deferred_refresh", None)
                    key = id(scroll)
                    if isinstance(deferred, set) and key not in deferred:
                        deferred.add(key)

                        def _post_scroll_refresh(s=scroll, k=key):
                            ref = getattr(self, "_track_editor_scroll_deferred_refresh", None)
                            if isinstance(ref, set):
                                ref.discard(k)
                            self._refresh_macos_track_editor_compositor(s, force=True)

                        QTimer.singleShot(90, _post_scroll_refresh)
            except Exception:
                pass
            return
        pending = getattr(self, "_pending_card_fade_updates", None)
        if pending is None:
            pending = set()
            self._pending_card_fade_updates = pending
        key = id(scroll)
        if key in pending:
            return
        pending.add(key)

        def _run():
            active = getattr(self, "_pending_card_fade_updates", None)
            if isinstance(active, set):
                active.discard(key)
            self._update_card_fade_for_scroll(scroll)
            if sys.platform == "darwin":
                self._raise_titlebar_overlays_deferred()
                try:
                    if self._is_track_editor_scroll(scroll):
                        self._refresh_macos_track_editor_compositor(scroll, force=False)
                        deferred = getattr(self, "_track_editor_scroll_deferred_refresh", None)
                        if isinstance(deferred, set) and key not in deferred:
                            deferred.add(key)

                            def _post_scroll_refresh(s=scroll, k=key):
                                ref = getattr(self, "_track_editor_scroll_deferred_refresh", None)
                                if isinstance(ref, set):
                                    ref.discard(k)
                                self._refresh_macos_track_editor_compositor(s, force=True)

                            QTimer.singleShot(90, _post_scroll_refresh)
                except Exception:
                    pass

        QTimer.singleShot(0, _run)

    def _wrap_page_in_transparent_scroll(self, page):
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background-color: transparent;")
        scroll.setProperty("scrollbarSurfaceColor", "#1D2127")
        scroll.verticalScrollBar().setSingleStep(50)
        scroll.verticalScrollBar().setProperty("scrollbarSurfaceColor", "#1D2127")
        scroll.horizontalScrollBar().setProperty("scrollbarSurfaceColor", "#1D2127")
        vp = scroll.viewport()
        vp.setAutoFillBackground(False)
        vp.setStyleSheet("background-color: transparent;")
        self._configure_scroll_top_fade_mask(scroll)
        scroll.verticalScrollBar().valueChanged.connect(lambda _v, s=scroll: self._schedule_card_fade_for_scroll(s))
        scroll.horizontalScrollBar().valueChanged.connect(lambda _v, s=scroll: self._schedule_card_fade_for_scroll(s))
        scroll.setWidget(page)
        QTimer.singleShot(0, lambda s=scroll: self._schedule_card_fade_for_scroll(s))
        return scroll

    def create_splitter_page(self):
        page = GlassPage()
        annotate_widget(page, role="Splitter Page")
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
        header = QLabel("Audio Splitter")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._track_scalable(header, 28, "font-weight: 400;")
        header_row.addWidget(header, 0, Qt.AlignLeft | Qt.AlignVCenter)
        header_row.addStretch(1)
        # Keep the page title vertically centered even on dense Windows layouts.
        if sys.platform == "win32":
            header_container.setMinimumHeight(62)
        elif sys.platform == "darwin":
            header_container.setMinimumHeight(66)
        else:
            self._track_min_height(header_container, 52)
        layout.addWidget(header_container)
        if self.top_card_dragstrip_buffer > 0:
            layout.addSpacing(self.top_card_dragstrip_buffer)

        # Input Card
        input_card = self.create_splitter_card("Input Files", "Select the mixes you want to split.")
        self.drop_zone = DragDropArea(path_resolver=self._resolve_splitter_input_paths)
        self.drop_zone.setMinimumHeight(140)
        self._track_min_height(self.drop_zone, 140)
        input_card.layout().addWidget(self.drop_zone)
        layout.addWidget(input_card)

        # Mode Selection
        mode_card = self.create_splitter_card("Processing Mode", "Choose what the splitter should do.")
        self.mode_dropdown = _NoScrollComboBox()
        for workflow_index, (workflow_key, workflow_label) in enumerate(self._splitter_workflow_options()):
            self.mode_dropdown.addItem(workflow_label, workflow_key)
            self.mode_dropdown.setItemData(
                workflow_index,
                self._splitter_workflow_tooltip(workflow_key),
                Qt.ToolTipRole,
            )
        self.mode_dropdown.setVisible(False)
        mode_card.layout().addWidget(self.mode_dropdown)
        self.mode_radio_group = QButtonGroup(self)
        self.mode_radio_buttons = {}
        mode_radio_layout = QVBoxLayout()
        mode_radio_layout.setSpacing(8)
        self._track_layout_metrics(mode_radio_layout, base_margins=(0, 0, 0, 0), base_spacing=8)
        for workflow_key, workflow_label in self._splitter_workflow_options():
            button = QRadioButton(workflow_label)
            button.setToolTip(self._splitter_workflow_tooltip(workflow_key))
            self.mode_radio_group.addButton(button)
            self.mode_radio_buttons[workflow_key] = button
            button.toggled.connect(
                lambda checked, value=workflow_key: self._on_splitter_mode_radio_toggled(value, checked)
            )
            mode_radio_layout.addWidget(button)
        if self.SPLITTER_WORKFLOW_SPLIT_ONLY in self.mode_radio_buttons:
            self.mode_radio_buttons[self.SPLITTER_WORKFLOW_SPLIT_ONLY].setChecked(True)
        mode_card.layout().addLayout(mode_radio_layout)
        self.finish_preview_btn = QPushButton("Finish Unsaved Preview")
        self.finish_preview_btn.setProperty("class", "SecondaryButton")
        self.finish_preview_btn.setProperty("settingsInlineCell", "true")
        self._apply_history_action_cell_style(self.finish_preview_btn)
        self._refresh_widget_style(self.finish_preview_btn)
        self.finish_preview_btn.setVisible(True)
        self.finish_preview_btn.clicked.connect(self.finish_unsaved_preview_data)
        self.clear_preview_btn = QPushButton("Clear Unsaved Preview")
        self.clear_preview_btn.setProperty("class", "SecondaryButton")
        self.clear_preview_btn.setProperty("settingsInlineCell", "true")
        self._apply_history_action_cell_style(self.clear_preview_btn)
        self._refresh_widget_style(self.clear_preview_btn)
        self.clear_preview_btn.setVisible(True)
        self.clear_preview_btn.clicked.connect(self.clear_unsaved_preview_data)

        self.preview_btn_container = QWidget()
        self.preview_btn_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        preview_btn_row = QHBoxLayout(self.preview_btn_container)
        preview_btn_row.setContentsMargins(0, 0, 0, 0)
        preview_btn_row.setSpacing(8)
        preview_btn_row.addWidget(self.finish_preview_btn)
        preview_btn_row.addWidget(self.clear_preview_btn)
        preview_btn_row.addStretch(1)
        # Keep this row free of its own opacity effect so it composites with the
        # parent card reveal instead of popping in after the card animation.
        self._preview_btn_opacity_effect = None
        self._preview_btn_anim = None        # holds the running QParallelAnimationGroup
        self._preview_btn_natural_h = 0     # resolved on first animation
        self._preview_btn_visible_state = None  # last requested visibility (None until first sync)
        mode_card.layout().addWidget(self.preview_btn_container)
        mode_card.layout().addStretch(1)
        self.preview_mode_hint_label = QLabel(
            "Choose Full or Light preview before processing."
        )
        self.preview_mode_hint_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
        self.preview_mode_hint_label.setWordWrap(True)
        mode_card.layout().addWidget(self.preview_mode_hint_label)

        split_mode_card = self.create_splitter_card(
            "Mix Split Mode",
            "Choose how the splitter handles split points.",
        )
        self.split_mode_dropdown = _NoScrollComboBox()
        self.split_mode_dropdown.setVisible(False)
        split_mode_card.layout().addWidget(self.split_mode_dropdown)
        self.split_mode_radio_group = QButtonGroup(self)
        self.split_mode_radio_buttons = {}
        self.split_mode_radio_layout = QVBoxLayout()
        self.split_mode_radio_layout.setSpacing(8)
        self._track_layout_metrics(self.split_mode_radio_layout, base_margins=(0, 0, 0, 0), base_spacing=8)
        split_mode_card.layout().addLayout(self.split_mode_radio_layout)
        split_mode_card.layout().addStretch(1)
        self.split_mode_hint_label = QLabel("")
        self.split_mode_hint_label.setStyleSheet("color: #C4C7C5; font-size: 13px;")
        self.split_mode_hint_label.setWordWrap(True)
        split_mode_card.layout().addWidget(self.split_mode_hint_label)
        # Keep both mode cards visible in a single row to reduce vertical scroll.
        mode_cards_row = QHBoxLayout()
        mode_cards_row.setContentsMargins(0, 0, 0, 0)
        mode_cards_row.setSpacing(20)
        self._track_layout_metrics(mode_cards_row, base_margins=(0, 0, 0, 0), base_spacing=20)
        mode_card.setMinimumWidth(0)
        split_mode_card.setMinimumWidth(0)
        mode_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        split_mode_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        mode_cards_row.addWidget(mode_card, 1)
        mode_cards_row.addWidget(split_mode_card, 1)
        mode_cards_row.setStretch(0, 1)
        mode_cards_row.setStretch(1, 1)
        layout.addLayout(mode_cards_row)

        layout.addStretch()

        # Action Bar specific to this page
        action_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        action_layout.addWidget(self.progress_bar)

        self.stage_label = QLabel("")
        self.stage_label.setStyleSheet("color: #6E7270; font-size: 11px;")
        self.stage_label.setVisible(False)
        action_layout.addWidget(self.stage_label)

        bottom_row = QHBoxLayout()
        self.status_label = QLabel("Status: Ready to process")
        self.status_label.setStyleSheet("color: #C4C7C5;")
        
        self.run_btn = QPushButton("Start Processing")
        self.run_btn.setProperty("class", "ActionButton")
        self.run_btn.setProperty("startProcessCell", True)
        self.run_btn.setProperty("busyState", False)
        self.run_btn.setProperty("cancelState", False)
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.clicked.connect(self._on_run_button_clicked)
        self.run_btn.setStyleSheet(self._run_button_cell_style(busy=False))
        
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.run_btn)
        
        action_layout.addLayout(bottom_row)
        layout.addLayout(action_layout)

        self._apply_card_line_surfaces_on_page(page)
        splitter_scroll = self._wrap_page_in_transparent_scroll(page)
        splitter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return splitter_scroll

    def create_identification_page(self):
        page = GlassPage()
        annotate_widget(page, role="Identifier Page")
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
        header = QLabel("Identifier")
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

        input_card = self.create_base_card(
            "Input Files",
            "Select tracks that are already separate, or continue a split started in Splitter that still needs identification.",
        )
        self.ident_drop_zone = DragDropArea(path_resolver=self._resolve_splitter_input_paths)
        self.ident_drop_zone.setMinimumHeight(140)
        self._track_min_height(self.ident_drop_zone, 140)
        input_card.layout().addWidget(self.ident_drop_zone)
        layout.addWidget(input_card)

        mode_card = self.create_base_card(
            "Identification Method",
            "Choose how files are identified. Will automatically use Shazam if enabled and Last.fm if api key is valid.",
        )
        self.ident_mode_dropdown = _NoScrollComboBox()
        self.ident_mode_dropdown.addItems(
            [
                "MusicBrainz / AcoustID",
                "ACRCloud",
                "Dual (ACRCloud + AcoustID)",
            ]
        )
        self.ident_mode_dropdown.setVisible(False)
        mode_card.layout().addWidget(self.ident_mode_dropdown)

        self.ident_mode_radio_group = QButtonGroup(self)
        self.ident_mode_radio_buttons = {}
        mode_radio_layout = QVBoxLayout()
        mode_radio_layout.setSpacing(8)
        self._track_layout_metrics(mode_radio_layout, base_margins=(0, 0, 0, 0), base_spacing=8)
        for label in [
            "MusicBrainz / AcoustID",
            "ACRCloud",
            "Dual (ACRCloud + AcoustID)",
        ]:
            button = QRadioButton(label)
            self.ident_mode_radio_group.addButton(button)
            self.ident_mode_radio_buttons[label] = button
            button.toggled.connect(lambda checked, text=label: self._on_ident_mode_radio_toggled(text, checked))
            mode_radio_layout.addWidget(button)
        if "MusicBrainz / AcoustID" in self.ident_mode_radio_buttons:
            self.ident_mode_radio_buttons["MusicBrainz / AcoustID"].setChecked(True)
        mode_card.layout().addLayout(mode_radio_layout)

        ident_hint = QLabel(
            "Use Identifier for tracks that are already separate, or for runs queued from Splitter. "
            "Direct Identifier runs open the results in Track Editor for review, apply, and export. "
            "If you drop one long file here, MixSplitR can send it to Splitter first so you can choose a split type."
        )
        ident_hint.setWordWrap(True)
        ident_hint.setStyleSheet("color: #C4C7C5; font-size: 13px;")
        mode_card.layout().addWidget(ident_hint)
        layout.addWidget(mode_card)

        layout.addStretch()

        action_layout = QVBoxLayout()

        self.ident_progress_bar = QProgressBar()
        self.ident_progress_bar.setRange(0, 100)
        self.ident_progress_bar.setValue(0)
        self.ident_progress_bar.setTextVisible(False)
        self.ident_progress_bar.setFixedHeight(8)
        action_layout.addWidget(self.ident_progress_bar)

        self.ident_stage_label = QLabel("")
        self.ident_stage_label.setStyleSheet("color: #6E7270; font-size: 11px;")
        self.ident_stage_label.setVisible(False)
        action_layout.addWidget(self.ident_stage_label)

        bottom_row = QHBoxLayout()
        self.ident_status_label = QLabel("Status: Ready to identify and review")
        self.ident_status_label.setStyleSheet("color: #C4C7C5;")

        self.ident_run_btn = QPushButton(self._identifier_action_button_text())
        self.ident_run_btn.setProperty("class", "ActionButton")
        self.ident_run_btn.setProperty("startProcessCell", True)
        self.ident_run_btn.setProperty("busyState", False)
        self.ident_run_btn.setCursor(Qt.PointingHandCursor)
        self.ident_run_btn.clicked.connect(self.start_identification_processing)
        self.ident_run_btn.setStyleSheet(self._run_button_cell_style(busy=False))

        bottom_row.addWidget(self.ident_status_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.ident_run_btn)
        action_layout.addLayout(bottom_row)
        layout.addLayout(action_layout)

        self._apply_card_line_surfaces_on_page(page)
        ident_scroll = self._wrap_page_in_transparent_scroll(page)
        ident_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return ident_scroll


    def create_splitter_card(self, title, description):
        card = QFrame()
        annotate_widget(card, role=f"{title} Card")
        annotate_widget_owner(card, role=f"{title} Card", stacklevel=1)
        card.setObjectName("AudioSplitterCard")
        card.setProperty("is_card", True)
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setAutoFillBackground(True)
        card.setStyleSheet(
            """
            QFrame#AudioSplitterCard {
                background-color: #272B30;
                border: none;
                border-radius: 10px;
            }
            """
        )
        layout = QVBoxLayout(card)
        top_margin = 16 if sys.platform == "darwin" else 20
        layout.setContentsMargins(20, top_margin, 20, 18)
        layout.setSpacing(8)
        self._track_layout_metrics(layout, base_margins=(20, top_margin, 20, 18), base_spacing=8)

        title_label = QLabel(title)
        annotate_widget(title_label, role=f"{title} Card Title")
        annotate_widget_owner(title_label, role=f"{title} Card Title", stacklevel=1)
        title_label.setProperty("is_card_title", True)
        self._track_scalable(
            title_label,
            18,
            'font-family: Roboto; font-weight: 500; color: #EBE7E1; border: none;',
        )
        desc_label = QLabel(description)
        annotate_widget(desc_label, role=f"{title} Card Description")
        annotate_widget_owner(desc_label, role=f"{title} Card Description", stacklevel=1)
        desc_label.setProperty("is_card_description", True)
        desc_label.setTextFormat(Qt.PlainText)
        desc_label.setWordWrap(True)
        desc_label.setMinimumWidth(0)
        desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._track_scalable(
            desc_label,
            14,
            "font-weight: 400; color: #B4AEA4; background-color: transparent; border: none; padding: 0px;",
        )
        self._track_min_height(desc_label, 22)

        layout.addWidget(title_label)
        layout.addSpacing(6)
        layout.addWidget(desc_label)
        return card

    def create_base_card(self, title, description):
        card = QFrame()
        annotate_widget(card, role=f"{title} Card")
        annotate_widget_owner(card, role=f"{title} Card", stacklevel=1)
        card.setObjectName("AppBaseCard")
        card.setProperty("is_card", True)
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setAutoFillBackground(True)
        card.setStyleSheet(
            """
            QFrame#AppBaseCard {
                background-color: #272B30;
                border: none;
                border-radius: 10px;
            }
            """
        )
        layout = QVBoxLayout(card)
        top_margin = 16 if sys.platform == "darwin" else 20
        layout.setContentsMargins(20, top_margin, 20, 18)
        layout.setSpacing(8)
        self._track_layout_metrics(layout, base_margins=(20, top_margin, 20, 18), base_spacing=8)
        
        title_label = QLabel(title)
        annotate_widget(title_label, role=f"{title} Card Title")
        annotate_widget_owner(title_label, role=f"{title} Card Title", stacklevel=1)
        title_label.setProperty("is_card_title", True)
        self._track_scalable(
            title_label,
            18,
            'font-family: Roboto; font-weight: 500; color: #EBE7E1; border: none;',
        )
        desc_label = QLabel(description)
        annotate_widget(desc_label, role=f"{title} Card Description")
        annotate_widget_owner(desc_label, role=f"{title} Card Description", stacklevel=1)
        desc_label.setProperty("is_card_description", True)
        desc_label.setTextFormat(Qt.PlainText)
        desc_label.setWordWrap(True)
        desc_label.setMinimumWidth(0)
        desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._track_scalable(
            desc_label,
            14,
            "font-weight: 400; color: #B4AEA4; background-color: transparent; border: none; padding: 0px;",
        )
        self._track_min_height(desc_label, 22)
        
        layout.addWidget(title_label)
        layout.addSpacing(6)
        layout.addWidget(desc_label)
        return card

    def _apply_card_line_surfaces_on_page(self, page):
        if page is None:
            return
        cards = [
            widget
            for widget in page.findChildren(QFrame)
            if bool(widget.property("is_card"))
        ]
        for card in cards:
            widgets = card.findChildren(QWidget)
            for widget in widgets:
                if isinstance(widget, (QRadioButton, QCheckBox)):
                    widget.setProperty("cardLineSurface", "true")
                    self._refresh_widget_style(widget)
                    # On Windows, Qt does not recalculate size hints after
                    # stylesheet property changes that add padding (12px top
                    # + 12px bottom).  Set an explicit minimum height so the
                    # text is never clipped.  The font metrics give us the
                    # real text height; add the CSS padding on both sides.
                    fm_height = widget.fontMetrics().height()
                    widget.setMinimumHeight(fm_height + 24 + 4)  # 12+12 padding + 4 border
                    continue
                if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit)):
                    widget.setProperty("settingsBubbleField", "true")
                    if isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
                        widget.setMinimumHeight(max(widget.minimumHeight(), 34))
                    self._refresh_widget_style(widget)
                    continue
                if isinstance(widget, QLabel):
                    if isinstance(widget.parentWidget(), DragDropArea):
                        continue
                    if bool(widget.property("is_card_title")) or bool(widget.property("is_card_description")):
                        # Keep card heading text clean/aligned; the bubble-style
                        # surface padding makes titles look vertically offset in
                        # dense layouts.
                        widget.setProperty("cardLineSurface", "false")
                        self._refresh_widget_style(widget)
                        continue
                    if bool(widget.property("helpDescriptionCell")) or bool(widget.wordWrap()):
                        widget.setProperty("cardLineSurface", "false")
                        self._refresh_widget_style(widget)
                        continue
                    widget.setProperty("cardLineSurface", "true")
                    self._refresh_widget_style(widget)


    # ------------------------------------------------------------------ #
    #  CD Ripping helpers                                                  #
    # ------------------------------------------------------------------ #


    def _mode_values(self):
        return ui_settings.mode_values(mixsplitr_core)

    def _splitter_workflow_options(self):
        return (
            (self.SPLITTER_WORKFLOW_SPLIT_ONLY, "Split Only (No ID)"),
            (self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY, "Split and Identify"),
            (self.SPLITTER_WORKFLOW_TIMESTAMP, "Timestamping"),
        )

    def _splitter_workflow_tooltip(self, workflow_value):
        normalized = self._normalize_splitter_workflow_value(workflow_value)
        if normalized == self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY:
            return (
                "Split the mix into tracks, then send the run through Identifier so "
                "artist/title metadata can be added."
            )
        if normalized == self.SPLITTER_WORKFLOW_TIMESTAMP:
            return (
                "Scan the mix for track boundaries and export a timestamped tracklist. "
                "Uses the selected Identifier for accuracy. Enable 'No-ID Labels' in "
                "settings to use Track NN names instead of identified titles."
            )
        return (
            "Split the mix into separate Track NN files only. No identification "
            "lookups or API keys are required."
        )

    def _normalize_splitter_workflow_value(self, value):
        parsed = str(value or "").strip().lower()
        alias_map = {
            "split_only": self.SPLITTER_WORKFLOW_SPLIT_ONLY,
            "split_only_no_id": self.SPLITTER_WORKFLOW_SPLIT_ONLY,
            "split only (no id)": self.SPLITTER_WORKFLOW_SPLIT_ONLY,
            "split_and_identify": self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY,
            "split and identify": self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY,
            "timestamp": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamping": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamp_no_id": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamping_no_id": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamping (no id)": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamp_with_id": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamping_with_id": self.SPLITTER_WORKFLOW_TIMESTAMP,
            "timestamping (with id)": self.SPLITTER_WORKFLOW_TIMESTAMP,
        }
        if parsed in alias_map:
            return alias_map[parsed]
        allowed = {key for key, _label in self._splitter_workflow_options()}
        return parsed if parsed in allowed else self.SPLITTER_WORKFLOW_SPLIT_ONLY

    def _splitter_workflow_label(self, workflow_value):
        normalized = self._normalize_splitter_workflow_value(workflow_value)
        for key, label in self._splitter_workflow_options():
            if key == normalized:
                return label
        return self._splitter_workflow_options()[0][1]

    def _splitter_workflow_from_config(self, config):
        raw_workflow = str((config or {}).get("splitter_workflow", "") or "").strip()
        if raw_workflow:
            return self._normalize_splitter_workflow_value(raw_workflow)

        mode_value = str((config or {}).get("mode", "") or "").strip()
        if not mode_value:
            return self.SPLITTER_WORKFLOW_SPLIT_ONLY

        mode_split_only = getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id")
        mode_auto = getattr(mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual")
        if mode_value == mode_split_only:
            return self.SPLITTER_WORKFLOW_SPLIT_ONLY
        if mode_value == mode_auto:
            return self.SPLITTER_WORKFLOW_TIMESTAMP
        return self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY

    def _selected_splitter_workflow(self):
        if not hasattr(self, "mode_dropdown"):
            return self.SPLITTER_WORKFLOW_SPLIT_ONLY
        return self._normalize_splitter_workflow_value(self.mode_dropdown.currentData())

    def _splitter_workflow_requires_identifier(self, workflow_value):
        normalized = self._normalize_splitter_workflow_value(workflow_value)
        if normalized == self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY:
            return True
        if normalized == self.SPLITTER_WORKFLOW_TIMESTAMP:
            try:
                return not bool(
                    hasattr(self, "auto_no_identify_check")
                    and self.auto_no_identify_check.isChecked()
                )
            except Exception:
                return True
        return False

    def _splitter_workflow_is_timestamp(self, workflow_value):
        normalized = self._normalize_splitter_workflow_value(workflow_value)
        return normalized == self.SPLITTER_WORKFLOW_TIMESTAMP

    def _splitter_processing_mode_text(self, workflow_value, identifier_mode_text=None):
        normalized = self._normalize_splitter_workflow_value(workflow_value)
        if normalized == self.SPLITTER_WORKFLOW_SPLIT_ONLY:
            return "Split Only (No ID)"
        if self._splitter_workflow_is_timestamp(normalized):
            return "Timestamping Mode (Auto Tracklist)"
        selected_mode = str(identifier_mode_text or "").strip()
        if not selected_mode and hasattr(self, "ident_mode_dropdown"):
            selected_mode = str(self.ident_mode_dropdown.currentText() or "").strip()
        if not selected_mode:
            selected_mode = "MusicBrainz / AcoustID"
        return selected_mode

    def _identifier_action_button_text(self):
        pending = getattr(self, "pending_splitter_identifier_workflow", None)
        if isinstance(pending, dict):
            workflow_value = self._normalize_splitter_workflow_value(pending.get("workflow"))
            if workflow_value == self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY:
                return "Start Split + ID"
            if workflow_value == self.SPLITTER_WORKFLOW_TIMESTAMP:
                return "Start Timestamping"
        return "Identify + Review"

    def _queue_splitter_workflow_for_identifier(
        self,
        workflow_value,
        files_to_process,
        dest_folder,
        flow,
        split_points_map,
        advanced_settings,
    ):
        workflow_key = self._normalize_splitter_workflow_value(workflow_value)
        file_list = self._normalize_audio_path_list(files_to_process)
        self.pending_splitter_identifier_workflow = {
            "workflow": workflow_key,
            "files": file_list,
            "dest_folder": str(dest_folder or ""),
            "flow": dict(flow or {}),
            "split_points_map": dict(split_points_map or {}),
            "advanced_settings": dict(advanced_settings or {}),
        }

        if hasattr(self, "ident_drop_zone"):
            self.ident_drop_zone.file_paths = file_list
            self.ident_drop_zone.label.setText(
                f"{len(file_list)} splitter file(s) ready for method selection"
            )
        if hasattr(self, "ident_progress_bar"):
            self.ident_progress_bar.setValue(0)
        if hasattr(self, "ident_stage_label"):
            self.ident_stage_label.setVisible(False)
        if hasattr(self, "ident_status_label"):
            self.ident_status_label.setText(
                "Status: "
                f"{self._splitter_workflow_label(workflow_key)} queued. "
                "Choose an identification method, then start."
            )
        self._set_ident_run_button_busy(False)
        self.status_label.setText(
            "Status: Continue in Identifier to choose how these tracks should be identified."
        )
        self.switch_page(getattr(self, "identification_page_index", 1))

    def _normalize_audio_path_list(self, paths):
        normalized_paths = []
        for path in list(paths or []):
            normalized_path = os.path.abspath(str(path or "").strip())
            if normalized_path:
                normalized_paths.append(normalized_path)
        return list(dict.fromkeys(normalized_paths))

    def _refresh_pending_identifier_workflow_from_preview(self, preview_thread=None):
        pending = self.pending_splitter_identifier_workflow
        if not isinstance(pending, dict):
            return
        workflow_key = self._normalize_splitter_workflow_value(pending.get("workflow"))
        if workflow_key != self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY:
            return

        cache_data = getattr(preview_thread, "preview_cache_data", None)
        cache_path = str(getattr(preview_thread, "preview_cache_path", "") or "")
        if cache_data is None and cache_path:
            if mixsplitr_editor is None:
                _lazy_import_backend("mixsplitr_editor")
            if mixsplitr_editor and hasattr(mixsplitr_editor, "load_preview_cache"):
                try:
                    cache_data = mixsplitr_editor.load_preview_cache(cache_path)
                except Exception:
                    cache_data = None
        if not isinstance(cache_data, dict):
            return

        split_data = dict(cache_data.get("split_data") or {})
        if not split_data:
            return

        refreshed_split_points = {}
        refreshed_mix_strategy_overrides = {}
        for raw_path, raw_entry in split_data.items():
            entry = raw_entry if isinstance(raw_entry, dict) else {}
            normalized_path = os.path.abspath(
                str(entry.get("path") or raw_path or "").strip()
            )
            if not normalized_path:
                continue

            clean_points = []
            for point in list(entry.get("points_sec") or []):
                try:
                    point_value = float(point)
                except Exception:
                    continue
                if point_value > 0.0:
                    clean_points.append(round(point_value, 3))
            if clean_points:
                refreshed_split_points[normalized_path] = sorted(set(clean_points))

            method = str(entry.get("method") or "").strip().lower()
            if method == "transition":
                refreshed_mix_strategy_overrides[normalized_path] = "transition"

        if refreshed_split_points:
            pending["split_points_map"] = refreshed_split_points

        if refreshed_mix_strategy_overrides:
            advanced_settings = dict(pending.get("advanced_settings") or {})
            normalized_existing = {}
            for path, value in dict(
                advanced_settings.get("long_track_mix_strategy_overrides") or {}
            ).items():
                normalized_path = os.path.abspath(str(path or "").strip())
                normalized_value = str(value or "").strip().lower()
                if normalized_path and normalized_value in ("silence", "transition"):
                    normalized_existing[normalized_path] = normalized_value
            normalized_existing.update(refreshed_mix_strategy_overrides)
            advanced_settings["long_track_mix_strategy_overrides"] = normalized_existing
            pending["advanced_settings"] = advanced_settings

    def _ui_mode_for_internal(self, internal_mode):
        mode_map = self._mode_values()
        for ui_label, mode_value in mode_map.items():
            if mode_value == internal_mode:
                return ui_label
        return "Split Only (No ID)"

    def _internal_mode_for_ui(self, ui_label):
        return self._mode_values().get(ui_label, getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id"))

    def _split_sensitivity_bounds(self):
        return ui_settings.split_sensitivity_bounds(mixsplitr_core)

    def _normalize_split_sensitivity_value(self, value):
        return ui_settings.normalize_split_sensitivity_value(mixsplitr_core, value)

    def _split_sensitivity_offset(self):
        value = getattr(self, "split_sensitivity_value", 0)
        return self._normalize_split_sensitivity_value(value)

    def _set_split_sensitivity_offset(self, value, autosave=False):
        self.split_sensitivity_value = self._normalize_split_sensitivity_value(value)
        self._refresh_split_sensitivity_controls()
        if autosave:
            self._schedule_autosave()

    def _adjust_split_sensitivity_offset(self, delta):
        self._set_split_sensitivity_offset(self._split_sensitivity_offset() + int(delta), autosave=True)

    def _refresh_split_sensitivity_controls(self):
        if not hasattr(self, "split_sensitivity_value_label"):
            return
        offset = self._split_sensitivity_offset()
        effective = int(round(self._split_silence_threshold_for_offset(offset)))
        self.split_sensitivity_value_label.setText(f"{offset:+d} dB (effective {effective} dBFS)")
        min_db, max_db = self._split_sensitivity_bounds()
        if hasattr(self, "split_sensitivity_left_btn"):
            self.split_sensitivity_left_btn.setEnabled(offset > min_db)
        if hasattr(self, "split_sensitivity_right_btn"):
            self.split_sensitivity_right_btn.setEnabled(offset < max_db)

    def _split_seek_step_options(self):
        return ui_settings.split_seek_step_options(mixsplitr_core)

    def _normalize_split_seek_step_value(self, value):
        return ui_settings.normalize_split_seek_step_value(mixsplitr_core, value)

    def _duplicate_policy_options(self):
        return ui_settings.duplicate_policy_options(mixsplitr_core)

    def _normalize_duplicate_policy_value(self, value):
        return ui_settings.normalize_duplicate_policy_value(mixsplitr_core, value)

    def _artist_normalization_mode_options(self):
        return ui_settings.artist_normalization_mode_options(mixsplitr_core)

    def _normalize_artist_normalization_mode_value(self, value, legacy_normalize_artists=True):
        return ui_settings.normalize_artist_normalization_mode_value(
            mixsplitr_core,
            value,
            legacy_normalize_artists=legacy_normalize_artists,
        )

    def _normalize_artist_normalization_strictness_value(self, value):
        return ui_settings.normalize_artist_normalization_strictness_value(mixsplitr_core, value)

    def _update_artist_normalization_ui_state(self):
        mode = self._normalize_artist_normalization_mode_value(
            self.artist_normalization_mode_combo.currentData(),
            legacy_normalize_artists=True,
        )
        smart_mode = mode == "smart"
        self.artist_normalization_strictness_spin.setEnabled(smart_mode)
        self.artist_normalization_collapse_backing_band_check.setEnabled(smart_mode)
        self.artist_normalization_review_ambiguous_check.setEnabled(smart_mode)

    def _split_silence_threshold_for_offset(self, offset_value):
        return ui_settings.split_silence_threshold_for_offset(mixsplitr_core, offset_value)

    def _current_split_silence_threshold_db(self):
        if hasattr(self, "split_sensitivity_value"):
            try:
                return float(self._split_silence_threshold_for_offset(self._split_sensitivity_offset()))
            except Exception:
                pass
        return float(self._split_silence_threshold_for_offset(0))

    def _config_path(self):
        return ui_settings.get_config_path(mixsplitr_core)

    def _default_output_directory(self):
        return ui_settings.default_output_directory(mixsplitr_core)

    def _default_recording_directory(self):
        return ui_settings.default_recording_directory(mixsplitr_core)

    def _default_manifest_directory(self):
        return ui_settings.default_manifest_directory(mixsplitr_core)

    def _default_cd_output_directory(self):
        return ui_settings.default_cd_output_directory(mixsplitr_core)

    def _default_temp_workspace_directory(self):
        return ui_settings.default_temp_workspace_directory(mixsplitr_core)

    def _default_ui_config(self):
        return ui_settings.default_ui_config(
            mixsplitr_core,
            output_directory=self._default_output_directory(),
            recording_directory=self._default_recording_directory(),
            cd_output_directory=self._default_cd_output_directory(),
            temp_workspace_directory=self._default_temp_workspace_directory(),
        )

    def _read_config_from_disk(self):
        return ui_settings.read_config_from_disk(self._config_path())

    def _save_config_to_disk(self, config):
        ui_settings.save_config_to_disk(mixsplitr_core, config, self._config_path())

    def _browse_directory_into(self, line_edit):
        default_dir = ""
        if isinstance(line_edit, QLineEdit):
            default_dir = self._normalized_path(line_edit.text())
        if not default_dir:
            default_dir = os.path.expanduser("~")

        selected = choose_existing_directory(
            self,
            "Select Folder",
            default_dir,
        )
        if selected:
            line_edit.setText(selected)
            if isinstance(line_edit, QLineEdit):
                line_edit.setCursorPosition(0)
            self._schedule_autosave()
            if line_edit is self.recording_dir_input:
                self._refresh_recordings_list()
            if line_edit is self.manifest_dir_input:
                self._refresh_session_history_list()

    def _browse_cd_rip_output_directory(self):
        target_dir = ""
        line_edit = getattr(self, "cd_output_dir_input", None)
        if isinstance(line_edit, QLineEdit):
            target_dir = self._normalized_path(line_edit.text())
        if not target_dir:
            config = self._read_config_from_disk()
            target_dir = self._normalized_path(config.get("cd_rip_output_directory"))
        if not target_dir:
            target_dir = self._default_cd_output_directory()

        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            pass

        try:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))
        except Exception:
            opened = False

        if opened:
            self._cd_rip_set_status(f"Opened CD rip output folder: {target_dir}")
        else:
            self._cd_rip_set_status("Could not open the CD rip output folder.", error=True)

    def _sync_mode_from_splitter(self, mode_text):
        """Splitter mode changed — update splitter-page controls only."""
        self._update_mode_specific_ui()

    def _normalized_path(self, value):
        text = str(value or "").strip()
        if not text:
            return ""
        return os.path.abspath(os.path.expanduser(text))

    def _supported_audio_extensions(self):
        return {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff", ".wma"}

    def _audio_output_format_options(self):
        preferred_order = [
            "flac", "alac", "wav", "aiff",
            "mp3_320", "mp3_256", "mp3_192",
            "aac_256", "ogg_500", "ogg_320", "opus",
        ]
        fallback_labels = {
            "flac": "FLAC",
            "alac": "ALAC (M4A)",
            "wav": "WAV",
            "aiff": "AIFF",
            "mp3_320": "MP3 320kbps",
            "mp3_256": "MP3 256kbps",
            "mp3_192": "MP3 192kbps",
            "aac_256": "AAC 256kbps",
            "ogg_500": "OGG Vorbis Q10 (~500kbps)",
            "ogg_320": "OGG Vorbis Q8 (~320kbps)",
            "opus": "OPUS 256kbps",
        }
        available = {}
        if mixsplitr_tagging and hasattr(mixsplitr_tagging, "AUDIO_FORMATS"):
            available = getattr(mixsplitr_tagging, "AUDIO_FORMATS") or {}

        options = []
        for key in preferred_order:
            if key not in available and key not in fallback_labels:
                continue
            label = str((available.get(key) or {}).get("name") or fallback_labels.get(key) or key).strip()
            options.append((key, label))
        return options or [("flac", "FLAC")]

    def _renaming_preset_options(self):
        try:
            if hasattr(ui_settings, "rename_preset_options"):
                options = ui_settings.rename_preset_options(mixsplitr_core)
                if options:
                    return [(str(key), str(label)) for key, label in options]
        except Exception:
            pass
        return [
            ("simple", "Simple (Artist - Title)"),
            ("album_folders", "Album Folders (Artist/Album/Track - Title)"),
            ("discography", "Discography (Artist/Year/Album/Track - Title)"),
            ("podcast", "Podcast (Artist/Title)"),
        ]

    def _normalize_rename_preset_value(self, value):
        try:
            if hasattr(ui_settings, "normalize_rename_preset_value"):
                return str(ui_settings.normalize_rename_preset_value(mixsplitr_core, value))
        except Exception:
            pass
        parsed = str(value or "simple").strip().lower()
        allowed = {str(key) for key, _label in self._renaming_preset_options()}
        return parsed if parsed in allowed else "simple"

    def _validated_output_format(self, value, fallback_value="flac"):
        selected = str(value or "").strip().lower()
        valid_keys = {key for key, _label in self._audio_output_format_options()}
        fallback_key = str(fallback_value or "flac").strip().lower() or "flac"
        if fallback_key not in valid_keys:
            fallback_key = "flac" if "flac" in valid_keys else (next(iter(valid_keys), "flac"))
        if selected in valid_keys:
            return selected
        return fallback_key

    def _selected_output_format(self):
        if hasattr(self, "output_format_combo"):
            key = str(self.output_format_combo.currentData() or "").strip().lower()
            return self._validated_output_format(key)
        config = self._read_config_from_disk()
        return self._validated_output_format(config.get("output_format", "flac"))

    def _is_supported_audio_file(self, file_path):
        extension = os.path.splitext(str(file_path or ""))[1].strip().lower()
        return extension in self._supported_audio_extensions()

    def _deep_scan_enabled_for_input(self):
        if hasattr(self, "deep_scan_check"):
            return bool(self.deep_scan_check.isChecked())
        config = self._read_config_from_disk()
        return bool(config.get("deep_scan", False))

    def _resolve_splitter_input_paths(self, raw_paths):
        deep_scan = self._deep_scan_enabled_for_input()
        resolved = []
        seen = set()

        def _append(path_value):
            normalized = os.path.abspath(os.path.expanduser(str(path_value or "").strip()))
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            resolved.append(normalized)

        for entry in list(raw_paths or []):
            candidate = os.path.abspath(os.path.expanduser(str(entry or "").strip()))
            if not candidate or not os.path.exists(candidate):
                continue
            if os.path.isfile(candidate):
                if self._is_supported_audio_file(candidate):
                    _append(candidate)
                continue
            if not os.path.isdir(candidate):
                continue

            if deep_scan:
                for root, _dirs, files in os.walk(candidate):
                    for name in files:
                        file_path = os.path.join(root, name)
                        if self._is_supported_audio_file(file_path):
                            _append(file_path)
            else:
                try:
                    children = os.listdir(candidate)
                except Exception:
                    children = []
                for name in children:
                    file_path = os.path.join(candidate, name)
                    if os.path.isfile(file_path) and self._is_supported_audio_file(file_path):
                        _append(file_path)

        return resolved

    def _set_enabled_group(self, widgets, enabled):
        for widget in widgets:
            if widget is not None:
                state = bool(enabled)
                widget.setEnabled(state)
                paired_label = getattr(widget, "_paired_form_label", None)
                if paired_label is not None:
                    paired_label.setEnabled(state)
                    self._refresh_widget_style(paired_label)
                self._refresh_widget_style(widget)

    def _visual_splitter_available_for_ui(self):
        return bool(AudioSegment is not None)

    def _selected_split_mode(self):
        if not hasattr(self, "split_mode_dropdown"):
            return "silence"
        value = str(self.split_mode_dropdown.currentData() or "").strip().lower()
        if value not in ("silence", "manual", "assisted"):
            return "silence"
        return value

    def _long_track_prompt_enabled(self):
        control = getattr(self, "long_track_prompt_check", None)
        return bool(control is not None and control.isChecked())

    def _long_track_prompt_threshold_minutes(self):
        spinner = getattr(self, "long_track_threshold_spin", None)
        if spinner is None:
            return float(self.LONG_TRACK_PROMPT_THRESHOLD_MINUTES)
        try:
            return max(1.0, float(spinner.value()))
        except Exception:
            return float(self.LONG_TRACK_PROMPT_THRESHOLD_MINUTES)

    def _update_long_track_prompt_ui_state(self):
        checkbox = getattr(self, "long_track_prompt_check", None)
        threshold_spin = getattr(self, "long_track_threshold_spin", None)
        if checkbox is None:
            return
        splitter_workflow = self._selected_splitter_workflow()
        applies_now = not bool(self._splitter_workflow_is_timestamp(splitter_workflow))
        checkbox.setEnabled(True)
        self._refresh_widget_style(checkbox)
        if threshold_spin is not None:
            threshold_spin.setEnabled(bool(checkbox.isChecked()))
            self._refresh_widget_style(threshold_spin)

        threshold = self._long_track_prompt_threshold_minutes()
        threshold_text = f"{threshold:.1f}".rstrip("0").rstrip(".")
        if not checkbox.isChecked():
            checkbox_tip = (
                "When enabled, long files prompt you to choose "
                "\"Single Track\" or \"Split as Mix\" before processing starts."
            )
            threshold_tip = "Duration threshold (minutes) used when Long Track Prompt is enabled."
        elif applies_now:
            checkbox_tip = (
                f"Enabled. Files {threshold_text}+ minutes will prompt: "
                "\"Single Track\" or \"Split as Mix\". If you choose mix, "
                "MixSplitR will then ask whether to use transition detection or silence detection."
            )
            threshold_tip = (
                f"Current threshold: {threshold_text} minutes. "
                "Files at or above this length will prompt when you start a split run."
            )
        else:
            checkbox_tip = (
                f"Enabled at {threshold_text}+ minutes. "
                "Prompt applies to split runs, not timestamp-only runs."
            )
            threshold_tip = (
                f"Current threshold: {threshold_text} minutes. "
                "Prompt activates when a non-timestamp split run starts."
            )

        checkbox.setToolTip(checkbox_tip)
        if threshold_spin is not None:
            threshold_spin.setToolTip(threshold_tip)
            threshold_edit = threshold_spin.lineEdit()
            if threshold_edit is not None:
                threshold_edit.setToolTip(threshold_tip)

    def _prompt_long_track_mode_choice(self, audio_file, duration_minutes, current_index, total_count):
        dialog = QDialog(self)
        dialog.setModal(True)
        dialog.setWindowTitle("Long Track Mode")
        dialog.setMinimumWidth(620)
        dialog.setStyleSheet(
            """
            QDialog {
                background-color: #1D2024;
            }
            QLabel#LongTrackTitle {
                color: #EBE7E1;
                font-size: 18px;
                font-weight: 500;
            }
            QLabel#LongTrackBody {
                color: #B4AEA4;
                font-size: 13px;
            }
            QPushButton#PopupSecondaryButton {
                background-color: #22262B;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QPushButton#PopupSecondaryButton:hover {
                background-color: #292E34;
            }
            QPushButton#PopupSecondaryButton:pressed {
                background-color: #1F2328;
            }
            QPushButton#PopupPrimaryButton {
                background-color: #4D8DFF;
                color: #F6FAFF;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QPushButton#PopupPrimaryButton:hover {
                background-color: #63A1FF;
            }
            QPushButton#PopupPrimaryButton:pressed {
                background-color: #3F78DF;
            }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(f"Long Track {current_index}/{total_count}")
        title.setObjectName("LongTrackTitle")
        layout.addWidget(title)

        base_name = os.path.basename(str(audio_file or "").strip()) or str(audio_file or "").strip()
        body = QLabel(
            f"\"{base_name}\" is about {float(duration_minutes):.1f} minutes.\n\n"
            "Treat it as a single track, or split it as a mix?"
        )
        body.setObjectName("LongTrackBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel Run")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setObjectName("PopupSecondaryButton")
        single_btn = QPushButton("Single Track")
        single_btn.setCursor(Qt.PointingHandCursor)
        single_btn.setObjectName("PopupSecondaryButton")
        mix_btn = QPushButton("Split as Mix")
        mix_btn.setCursor(Qt.PointingHandCursor)
        mix_btn.setObjectName("PopupPrimaryButton")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(single_btn)
        btn_row.addWidget(mix_btn)
        layout.addLayout(btn_row)

        selection = {"value": "cancel"}

        def _choose(value):
            selection["value"] = str(value)
            dialog.accept()

        cancel_btn.clicked.connect(lambda: _choose("cancel"))
        single_btn.clicked.connect(lambda: _choose("single"))
        mix_btn.clicked.connect(lambda: _choose("mix"))
        single_btn.setDefault(True)
        single_btn.setAutoDefault(True)

        result = dialog.exec()
        if result != QDialog.Accepted:
            return "cancel"
        return str(selection.get("value", "cancel"))

    def _prompt_splitter_mix_strategy_choice(self, target_files, split_mode):
        dialog = QDialog(self)
        dialog.setModal(True)
        dialog.setWindowTitle("Mix Detection Style")
        dialog.setMinimumWidth(680)
        dialog.setStyleSheet(
            """
            QDialog {
                background-color: #1D2024;
            }
            QLabel#LongTrackTitle {
                color: #EBE7E1;
                font-size: 18px;
                font-weight: 500;
            }
            QLabel#LongTrackBody {
                color: #B4AEA4;
                font-size: 13px;
            }
            QPushButton#PopupSecondaryButton {
                background-color: #22262B;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QPushButton#PopupSecondaryButton:hover {
                background-color: #292E34;
            }
            QPushButton#PopupSecondaryButton:pressed {
                background-color: #1F2328;
            }
            QPushButton#PopupPrimaryButton {
                background-color: #4D8DFF;
                color: #F6FAFF;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QPushButton#PopupPrimaryButton:hover {
                background-color: #63A1FF;
            }
            QPushButton#PopupPrimaryButton:pressed {
                background-color: #3F78DF;
            }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Mix Detection Style")
        title.setObjectName("LongTrackTitle")
        layout.addWidget(title)

        normalized_targets = []
        for path in list(target_files or []):
            normalized = os.path.abspath(str(path or "").strip())
            if normalized:
                normalized_targets.append(normalized)
        normalized_targets = list(dict.fromkeys(normalized_targets))

        if len(normalized_targets) == 1:
            base_name = os.path.basename(normalized_targets[0]) or normalized_targets[0]
            target_summary = f"\"{base_name}\" is queued to split as a mix."
        else:
            target_summary = f"{len(normalized_targets)} file(s) are queued to split as mixes."

        mode_text = str(split_mode or "").strip().lower()
        if mode_text == "assisted":
            mode_detail = (
                "The chosen method will preload split points, then open the waveform editor for review."
            )
        else:
            mode_detail = (
                "The chosen method will be used for automatic split-point detection."
            )

        body = QLabel(
            f"{target_summary}\n\n"
            "How should MixSplitR detect split points?\n\n"
            "Transition Detection: use the same transition detector as Timestamping mode.\n"
            "Silence Detection: use the classic silence-based splitter.\n\n"
            f"{mode_detail}"
        )
        body.setObjectName("LongTrackBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel Run")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setObjectName("PopupSecondaryButton")
        silence_btn = QPushButton("Silence Detection")
        silence_btn.setCursor(Qt.PointingHandCursor)
        silence_btn.setObjectName("PopupPrimaryButton")
        transition_btn = QPushButton("Transition Detection")
        transition_btn.setCursor(Qt.PointingHandCursor)
        transition_btn.setObjectName("PopupSecondaryButton")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(silence_btn)
        btn_row.addWidget(transition_btn)
        layout.addLayout(btn_row)

        selection = {"value": "cancel"}

        def _choose(value):
            selection["value"] = str(value)
            dialog.accept()

        cancel_btn.clicked.connect(lambda: _choose("cancel"))
        silence_btn.clicked.connect(lambda: _choose("silence"))
        transition_btn.clicked.connect(lambda: _choose("transition"))
        silence_btn.setDefault(True)
        silence_btn.setAutoDefault(True)

        result = dialog.exec()
        if result != QDialog.Accepted:
            return "cancel"
        return str(selection.get("value", "cancel"))

    def _collect_duration_minutes_for_paths(self, target_files):
        duration_min_map = {}
        normalized_targets = []
        for path in list(target_files or []):
            normalized = os.path.abspath(str(path or "").strip())
            if normalized:
                normalized_targets.append(normalized)

        if mixsplitr_core is not None and hasattr(mixsplitr_core, "analyze_files_parallel"):
            try:
                analysis = mixsplitr_core.analyze_files_parallel(normalized_targets)
                for row in list(analysis or []):
                    if not isinstance(row, dict):
                        continue
                    analyzed_path = os.path.abspath(str(row.get("file") or "").strip())
                    if not analyzed_path:
                        continue
                    try:
                        duration_min = float(row.get("duration_min", 0.0) or 0.0)
                    except Exception:
                        duration_min = 0.0
                    duration_min_map[analyzed_path] = max(0.0, duration_min)
            except Exception:
                duration_min_map = {}

        if mixsplitr_core is not None and hasattr(mixsplitr_core, "get_audio_duration_fast"):
            for path in normalized_targets:
                if path in duration_min_map and float(duration_min_map.get(path, 0.0) or 0.0) > 0.0:
                    continue
                try:
                    duration_min_map[path] = max(
                        0.0,
                        float(mixsplitr_core.get_audio_duration_fast(path) or 0.0),
                    )
                except Exception:
                    duration_min_map[path] = 0.0

        return duration_min_map

    def _collect_long_track_prompt_overrides(
        self,
        target_files,
        duration_min_map,
        selected_workflow=None,
    ):
        if not self._long_track_prompt_enabled():
            return ({}, {})
        if self._splitter_workflow_is_timestamp(selected_workflow):
            return ({}, {})
        threshold = float(self._long_track_prompt_threshold_minutes())
        overrides = {}
        long_files = []
        for path in list(target_files or []):
            normalized_path = os.path.abspath(str(path or "").strip())
            if not normalized_path:
                continue
            try:
                duration_min = float((duration_min_map or {}).get(normalized_path, 0.0) or 0.0)
            except Exception:
                duration_min = 0.0
            if (
                duration_min <= 0.0
                and mixsplitr_core is not None
                and hasattr(mixsplitr_core, "get_audio_duration_fast")
            ):
                try:
                    duration_min = float(mixsplitr_core.get_audio_duration_fast(normalized_path) or 0.0)
                except Exception:
                    duration_min = 0.0
            if duration_min >= threshold:
                long_files.append((normalized_path, duration_min))
            else:
                overrides[normalized_path] = "single"
        if not long_files:
            return (overrides, {})
        # Ask about the longest files first so large/likely-mix decisions happen up front.
        long_files.sort(key=lambda row: (-float(row[1]), str(row[0]).lower()))

        mix_strategy_overrides = {}
        for idx, (path, duration_min) in enumerate(long_files, start=1):
            choice = self._prompt_long_track_mode_choice(
                path,
                duration_min,
                current_index=idx,
                total_count=len(long_files),
            )
            if choice == "cancel":
                return None
            if choice == "single":
                overrides[path] = choice
                continue
            if choice == "mix":
                overrides[path] = choice
        return (overrides, mix_strategy_overrides)

    def _prompt_identifier_long_file_redirect(self, audio_file, duration_minutes):
        base_name = os.path.basename(str(audio_file or "").strip()) or str(audio_file or "").strip()
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Long File Detected")
        prompt.setIcon(QMessageBox.Question)
        prompt.setText(f'"{base_name}" is about {float(duration_minutes):.1f} minutes.')
        prompt.setInformativeText(
            "Do you want to split this file before identifying?\n\n"
            "Yes: send it to Audio Splitter so you can choose a mode and split type.\n"
            "No: keep it in Identifier, treat it as one track, then review the result in Track Editor."
        )
        split_btn = prompt.addButton("Yes, Open Splitter", QMessageBox.AcceptRole)
        identify_btn = prompt.addButton("No, Identify Here", QMessageBox.ActionRole)
        cancel_btn = prompt.addButton("Cancel", QMessageBox.RejectRole)
        prompt.setDefaultButton(split_btn)
        prompt.exec()

        clicked = prompt.clickedButton()
        if clicked == split_btn:
            return "split"
        if clicked == identify_btn:
            return "identify"
        if clicked == cancel_btn:
            return "cancel"
        return "cancel"

    def _maybe_redirect_identifier_long_file(self, target_files, pending_workflow=None):
        if isinstance(pending_workflow, dict):
            return False
        files = list(target_files or [])
        if len(files) != 1:
            return False

        target_path = os.path.abspath(str(files[0] or "").strip())
        if not target_path or not os.path.exists(target_path):
            return False

        duration_minutes = 0.0
        if mixsplitr_core is not None and hasattr(mixsplitr_core, "get_audio_duration_fast"):
            try:
                duration_minutes = float(mixsplitr_core.get_audio_duration_fast(target_path) or 0.0)
            except Exception:
                duration_minutes = 0.0
        threshold_minutes = float(self._long_track_prompt_threshold_minutes())
        if duration_minutes < threshold_minutes:
            return False

        choice = self._prompt_identifier_long_file_redirect(target_path, duration_minutes)
        if choice == "cancel":
            self.ident_status_label.setText("Status: Identification cancelled")
            return True
        if choice != "split":
            return False

        self.pending_splitter_identifier_workflow = None
        self.drop_zone.file_paths = [target_path]
        self.drop_zone.label.setText("1 file(s) ready for processing")
        self.status_label.setText(
            "Status: Long file sent to Audio Splitter. Choose a mode and split type."
        )
        self.ident_status_label.setText("Status: Sent long file to Audio Splitter")
        self.switch_page(0)
        return True

    def _on_splitter_mode_radio_toggled(self, workflow_value, checked):
        if not checked or not hasattr(self, "mode_dropdown"):
            return
        idx = self.mode_dropdown.findData(
            self._normalize_splitter_workflow_value(workflow_value)
        )
        if idx >= 0 and self.mode_dropdown.currentIndex() != idx:
            self.mode_dropdown.setCurrentIndex(idx)

    def _sync_mode_radio_from_combo(self, _mode_text=None):
        if not hasattr(self, "mode_radio_buttons"):
            return
        button = self.mode_radio_buttons.get(self._selected_splitter_workflow())
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _on_ident_mode_radio_toggled(self, mode_text, checked):
        if not checked or not hasattr(self, "ident_mode_dropdown"):
            return
        idx = self.ident_mode_dropdown.findText(str(mode_text))
        if idx >= 0 and self.ident_mode_dropdown.currentIndex() != idx:
            self.ident_mode_dropdown.setCurrentIndex(idx)

    def _sync_ident_mode_radio_from_combo(self, mode_text):
        if not hasattr(self, "ident_mode_radio_buttons"):
            return
        button = self.ident_mode_radio_buttons.get(str(mode_text))
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _clear_layout_widgets(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout_widgets(child_layout)

    def _rebuild_split_mode_radio_buttons(self):
        if not hasattr(self, "split_mode_radio_layout") or not hasattr(self, "split_mode_dropdown"):
            return
        self._clear_layout_widgets(self.split_mode_radio_layout)
        self.split_mode_radio_group = QButtonGroup(self)
        self.split_mode_radio_group.setExclusive(True)
        self.split_mode_radio_buttons = {}
        for idx in range(self.split_mode_dropdown.count()):
            label = str(self.split_mode_dropdown.itemText(idx))
            data = str(self.split_mode_dropdown.itemData(idx) or "").strip().lower()
            button = QRadioButton(label)
            button.setProperty("cardLineSurface", True)
            self._refresh_widget_style(button)
            button.toggled.connect(lambda checked, mode_data=data: self._on_split_mode_radio_toggled(mode_data, checked))
            self.split_mode_radio_group.addButton(button)
            self.split_mode_radio_buttons[data] = button
            self.split_mode_radio_layout.addWidget(button)
        self._sync_split_mode_radio_from_combo()

    def _on_split_mode_radio_toggled(self, mode_data, checked):
        if not checked or not hasattr(self, "split_mode_dropdown"):
            return
        idx = self.split_mode_dropdown.findData(mode_data)
        if idx >= 0 and self.split_mode_dropdown.currentIndex() != idx:
            self.split_mode_dropdown.setCurrentIndex(idx)

    def _unsaved_preview_track_count(self):
        cache_path = str(self._preview_cache_path_for_ui())
        if not cache_path or not os.path.exists(cache_path):
            return 0
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                cache_data = json.load(handle)
            tracks = cache_data.get("tracks", [])
            return len(tracks) if isinstance(tracks, list) else 0
        except Exception:
            return 0

    def _refresh_preview_mode_hint_label(self, force=False):
        if not hasattr(self, "preview_mode_hint_label"):
            return
        if not self._preview_mode_enabled_from_ui():
            return

        default_text = "Choose Full or Light preview before processing."
        current_text = str(self.preview_mode_hint_label.text() or "").strip()

        if not force and current_text:
            # Preserve transient action feedback until another explicit mode/UI refresh.
            preserve = (
                current_text.startswith("Cleared unsaved preview cache/temp data")
                or current_text.startswith("Finishing unsaved preview")
                or current_text.startswith("No preview artifacts were removed")
                or current_text.startswith("No unsaved preview session tracks found")
            )
            if preserve:
                return

        usage_bytes = self._preview_artifact_storage_bytes()
        track_count = self._unsaved_preview_track_count()
        if usage_bytes > 0:
            if track_count > 0:
                self.preview_mode_hint_label.setText(
                    "Unsaved preview data currently uses "
                    f"{self._format_storage_bytes(usage_bytes)} across {track_count} track(s)."
                )
            else:
                self.preview_mode_hint_label.setText(
                    "Unsaved preview cache/temp data currently uses "
                    f"{self._format_storage_bytes(usage_bytes)}."
                )
            return

        self.preview_mode_hint_label.setText(default_text)

    def _update_clear_preview_visibility(self, preview_checked):
        preview_checked = bool(preview_checked)
        has_unsaved_preview = (
            self._unsaved_preview_track_count() > 0
            or self._preview_artifacts_exist()
        )
        export_running = bool(self.preview_export_thread and self.preview_export_thread.isRunning())

        enabled = preview_checked and has_unsaved_preview and not export_running
        if hasattr(self, 'finish_preview_btn'):
            self.finish_preview_btn.setEnabled(enabled)
        if hasattr(self, 'clear_preview_btn'):
            self.clear_preview_btn.setEnabled(enabled)

        if hasattr(self, 'preview_btn_container'):
            previous_state = getattr(self, "_preview_btn_visible_state", None)
            suppress_transition_anim = not bool(getattr(self, "_initial_page_reveal_done", False))
            if previous_state is None or suppress_transition_anim:
                self._set_preview_btn_container_state(preview_checked)
            elif previous_state != preview_checked:
                self._animate_preview_btns(preview_checked)
            else:
                # Keep steady state without replaying the entrance/exit animation.
                self._set_preview_btn_container_state(preview_checked)
            self._preview_btn_visible_state = preview_checked
        else:
            # Fallback for safety
            if hasattr(self, 'finish_preview_btn'):
                self.finish_preview_btn.setVisible(preview_checked)
            if hasattr(self, 'clear_preview_btn'):
                self.clear_preview_btn.setVisible(preview_checked)
        if preview_checked:
            self._refresh_preview_mode_hint_label(force=False)

    def _set_preview_btn_container_state(self, visible):
        if not hasattr(self, "preview_btn_container"):
            return
        container = self.preview_btn_container
        effect = getattr(self, "_preview_btn_opacity_effect", None)
        if visible:
            container.setVisible(True)
            container.setMaximumHeight(16777215)
            if effect is not None:
                effect.setOpacity(1.0)
        else:
            container.setMaximumHeight(0)
            if effect is not None:
                effect.setOpacity(0.0)
            container.setVisible(False)

    def _animate_preview_btns(self, show):
        container = self.preview_btn_container
        effect = getattr(self, "_preview_btn_opacity_effect", None)

        # Resolve natural height once the widget has been laid out
        if self._preview_btn_natural_h <= 0:
            container.setMaximumHeight(16777215)
            self._preview_btn_natural_h = container.sizeHint().height() or 36

        natural_h = self._preview_btn_natural_h
        duration = 180  # ms

        # Stop any in-flight animation cleanly
        if self._preview_btn_anim is not None:
            self._preview_btn_anim.stop()
            self._preview_btn_anim = None

        current_h = container.maximumHeight()
        if current_h >= 16777215:
            current_h = natural_h          # was fully open
        current_opacity = effect.opacity() if effect is not None else (1.0 if container.isVisible() else 0.0)

        if show and container.isVisible() and current_h >= natural_h and current_opacity >= 0.999:
            self._set_preview_btn_container_state(True)
            return
        if (not show) and (not container.isVisible() or (current_h <= 0 and current_opacity <= 0.001)):
            self._set_preview_btn_container_state(False)
            return

        if show:
            container.setVisible(True)
            start_h = max(0, min(current_h, natural_h))
            start_opacity = max(0.0, min(1.0, current_opacity))

            h_anim = QPropertyAnimation(container, b"maximumHeight", self)
            h_anim.setDuration(duration)
            h_anim.setStartValue(start_h)
            h_anim.setEndValue(natural_h)
            h_anim.setEasingCurve(QEasingCurve.OutCubic)

            group = QParallelAnimationGroup(self)
            group.addAnimation(h_anim)
            if effect is not None:
                op_anim = QPropertyAnimation(effect, b"opacity", self)
                op_anim.setDuration(duration)
                op_anim.setStartValue(start_opacity)
                op_anim.setEndValue(1.0)
                op_anim.setEasingCurve(QEasingCurve.OutCubic)
                group.addAnimation(op_anim)
            group.finished.connect(lambda: self._set_preview_btn_container_state(True))
            group.finished.connect(lambda: setattr(self, "_preview_btn_anim", None))
            self._preview_btn_anim = group
            group.start()
        else:
            start_h = current_h if current_h < natural_h else natural_h
            start_opacity = current_opacity if current_opacity < 1.0 else 1.0

            h_anim = QPropertyAnimation(container, b"maximumHeight", self)
            h_anim.setDuration(duration)
            h_anim.setStartValue(start_h)
            h_anim.setEndValue(0)
            h_anim.setEasingCurve(QEasingCurve.InCubic)

            group = QParallelAnimationGroup(self)
            group.addAnimation(h_anim)
            if effect is not None:
                op_anim = QPropertyAnimation(effect, b"opacity", self)
                op_anim.setDuration(duration)
                op_anim.setStartValue(start_opacity)
                op_anim.setEndValue(0.0)
                op_anim.setEasingCurve(QEasingCurve.InCubic)
                group.addAnimation(op_anim)
            group.finished.connect(lambda: self._set_preview_btn_container_state(False))
            group.finished.connect(lambda: setattr(self, "_preview_btn_anim", None))
            self._preview_btn_anim = group
            group.start()

    def _sync_split_mode_radio_from_combo(self):
        if not hasattr(self, "split_mode_radio_buttons") or not self.split_mode_radio_buttons:
            return
        current_mode = self._selected_split_mode()
        target = self.split_mode_radio_buttons.get(current_mode)
        if target is not None and not target.isChecked():
            target.setChecked(True)

    def _preview_mode_enabled_from_ui(self):
        if not hasattr(self, "mode_dropdown"):
            return True
        return not bool(self._splitter_workflow_is_timestamp(self._selected_splitter_workflow()))

    def _configure_split_mode_dropdown(self):
        if not hasattr(self, "split_mode_dropdown"):
            return
        self.split_mode_dropdown.blockSignals(True)
        self.split_mode_dropdown.clear()
        if self._visual_splitter_available_for_ui():
            self.split_mode_dropdown.addItem("Assisted (Auto + Review/Edit)", "assisted")
        self.split_mode_dropdown.addItem("Automatic", "silence")
        if self._visual_splitter_available_for_ui():
            self.split_mode_dropdown.addItem("Manual (Waveform Editor)", "manual")
        # Default to Assisted when available; otherwise Automatic.
        default_mode = "assisted" if self._visual_splitter_available_for_ui() else "silence"
        default_index = self.split_mode_dropdown.findData(default_mode)
        if default_index < 0:
            default_index = 0
        self.split_mode_dropdown.setCurrentIndex(default_index)
        self.split_mode_dropdown.blockSignals(False)
        self._rebuild_split_mode_radio_buttons()
        self._update_split_mode_hint()
        self._update_long_track_prompt_ui_state()

    def _update_probe_hint(self):
        if not hasattr(self, 'probe_hint_label'):
            return
        is_multi = self.fingerprint_probe_combo.currentIndex() == 1
        if is_multi:
            self.probe_hint_label.setText(
                "Multi-Point Probe - Samples three points in the track (early, mid, late) and sends each to ACRCloud. "
                "More accurate for covers, remixes, and obscure tracks, but uses up to 3x the API calls. "
                "Shazam and AcoustID always use a single probe regardless of this setting."
            )
        else:
            self.probe_hint_label.setText(
                "Single Probe - Takes one sample from the center of the track. Works well for most music and "
                "only calls the API once per track, but can occasionally return a false positive "
                "if the center happens to land on an unrepresentative section."
            )
        self.probe_hint_label.setVisible(True)

        # Swap the sample-size spinner to the value stored for this probe mode.
        # Each mode keeps its own saved value so switching doesn't carry over
        # an inappropriate length (e.g. 45s single → 45s multi = overlapping probes).
        if hasattr(self, '_probe_sample_single') and hasattr(self, '_probe_sample_multi'):
            if is_multi:
                self._probe_sample_single = self.fingerprint_sample_spin.value()
                self.fingerprint_sample_spin.setValue(self._probe_sample_multi)
            else:
                self._probe_sample_multi = self.fingerprint_sample_spin.value()
                self.fingerprint_sample_spin.setValue(self._probe_sample_single)

    def _update_split_mode_hint(self):
        if not hasattr(self, "split_mode_hint_label"):
            return
        splitter_workflow = self._selected_splitter_workflow()
        selected = self._selected_split_mode()

        if not self._visual_splitter_available_for_ui():
            if self._splitter_workflow_is_timestamp(splitter_workflow):
                self.split_mode_hint_label.setText(
                    "Waveform editor is unavailable in this build. Automatic timestamp timeline scanning is active."
                )
            else:
                self.split_mode_hint_label.setText(
                    "Waveform split editor is unavailable in this build. Automatic silence splitting is active."
                )
            return

        if self._splitter_workflow_is_timestamp(splitter_workflow):
            if selected == "manual":
                self.split_mode_hint_label.setText(
                    "Manual timecodes: open waveform editor and set track start times before timestamp export. Fully manual splitting for certain accuracy."
                )
            elif selected == "assisted":
                self.split_mode_hint_label.setText(
                    "Assisted timecodes: run the timestamp timeline scan first, then review/edit those detected starts in waveform editor. This uses the same transition scan as automatic mode, including Essentia when enabled."
                )
            else:
                self.split_mode_hint_label.setText(
                    "Automatic timecodes: scan the mix for transition boundaries and export the timeline without opening waveform editor. This is not the silence splitter."
                )
            return

        if selected == "manual":
            self.split_mode_hint_label.setText(
                "Visual mode opens a waveform editor for manual split-point placement on detected mixes."
            )
        elif selected == "assisted":
            self.split_mode_hint_label.setText(
                "Assisted mode preloads detected split points, then opens the waveform editor for review. "
                "When you start the run, MixSplitR will ask whether to use silence detection or transition detection."
            )
        else:
            self.split_mode_hint_label.setText(
                "Automatic mode detects split points without opening the waveform editor. "
                "When you start the run, MixSplitR will ask whether to use silence detection or transition detection."
            )
        self._update_long_track_prompt_ui_state()

    def _collect_embedded_waveform_points(
        self,
        files,
        split_mode,
        selected_workflow=None,
        long_track_mode_overrides=None,
        long_track_mix_strategy_overrides=None,
    ):
        mode = str(split_mode or "").strip().lower()
        if mode not in ("manual", "assisted"):
            return True, {}
        workflow_key = self._normalize_splitter_workflow_value(selected_workflow)
        timestamp_workflow = bool(self._splitter_workflow_is_timestamp(workflow_key))
        normalized_long_track_overrides = {}
        for path, value in dict(long_track_mode_overrides or {}).items():
            normalized_path = os.path.abspath(str(path or "").strip())
            normalized_value = str(value or "").strip().lower()
            if normalized_path and normalized_value in ("single", "mix"):
                normalized_long_track_overrides[normalized_path] = normalized_value
        normalized_mix_strategy_overrides = {}
        for path, value in dict(long_track_mix_strategy_overrides or {}).items():
            normalized_path = os.path.abspath(str(path or "").strip())
            normalized_value = str(value or "").strip().lower()
            if normalized_path and normalized_value in ("silence", "transition"):
                normalized_mix_strategy_overrides[normalized_path] = normalized_value
        timestamp_assisted = bool(
            mode == "assisted" and timestamp_workflow
        )
        timestamp_seed_config = None
        if timestamp_assisted:
            timestamp_seed_config = self._collect_config_from_widgets()
            timestamp_seed_config["mode"] = getattr(
                mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual"
            )
            timestamp_seed_config["splitter_workflow"] = workflow_key
            timestamp_seed_config["auto_tracklist_no_identify"] = bool(
                self.auto_no_identify_check.isChecked()
            )
            timestamp_seed_config["auto_tracklist_identifier_mode"] = self._internal_mode_for_ui(
                self.ident_mode_dropdown.currentText()
            )
        split_transition_seed_config = None
        if mode == "assisted" and not timestamp_workflow:
            has_transition_mix_override = any(
                str(value or "").strip().lower() == "transition"
                for value in normalized_mix_strategy_overrides.values()
            )
            if has_transition_mix_override:
                split_transition_seed_config = self._collect_config_from_widgets()
                split_transition_seed_config["mode"] = getattr(
                    mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual"
                )
                split_transition_seed_config["splitter_workflow"] = workflow_key
                split_transition_seed_config["auto_tracklist_no_identify"] = True
                split_transition_seed_config["auto_tracklist_identifier_mode"] = ""

        if mixsplitr_core and hasattr(mixsplitr_core, "setup_ffmpeg") and AudioSegment is not None:
            try:
                ffmpeg_path, ffprobe_path = mixsplitr_core.setup_ffmpeg()
                AudioSegment.converter = ffmpeg_path
                AudioSegment.ffprobe = ffprobe_path
            except Exception:
                pass

        if not self._visual_splitter_available_for_ui():
            fallback_text = (
                "Waveform editor is unavailable in this environment. Falling back to the automatic timestamp scan."
                if timestamp_workflow
                else "Waveform editor is unavailable in this environment. Falling back to automatic splitting."
            )
            QMessageBox.warning(
                self,
                "Waveform Editor Unavailable",
                fallback_text,
            )
            return True, {}

        target_files = []
        for path in list(files or []):
            raw = str(path or "").strip()
            if not raw:
                continue
            normalized = os.path.abspath(raw)
            if os.path.exists(normalized):
                target_files.append(normalized)
        if not target_files:
            return True, {}

        # Split workflows only open the waveform editor for files detected as mixes.
        # Timestamp workflows always treat the selected files as timeline sources.
        waveform_targets = list(target_files)
        skipped_regular_tracks = 0
        if not timestamp_workflow:
            mix_flags = {}
            duration_min_map = {}
            if mixsplitr_core and hasattr(mixsplitr_core, "analyze_files_parallel"):
                try:
                    self.status_label.setText("Status: Detecting mixes before waveform prep...")
                    self.stage_label.setText("Detecting mixes before opening waveform editor...")
                    self.stage_label.setVisible(True)
                    QApplication.processEvents()

                    analysis = mixsplitr_core.analyze_files_parallel(target_files)
                    for row in list(analysis or []):
                        if not isinstance(row, dict):
                            continue
                        analyzed_path = os.path.abspath(str(row.get("file") or ""))
                        if analyzed_path:
                            mix_flags[analyzed_path] = bool(row.get("is_mix"))
                            try:
                                duration_min = float(row.get("duration_min", 0.0) or 0.0)
                            except Exception:
                                duration_min = 0.0
                            duration_min_map[analyzed_path] = max(0.0, duration_min)

                except Exception as exc:
                    if self._debug_readout_enabled_from_ui():
                        self._append_debug_readout_line(
                            f"[waveform/warn] Mix detection failed; continuing with all files ({exc})"
                        )

            forced_single_count = sum(1 for v in normalized_long_track_overrides.values() if v == "single")
            forced_mix_count = sum(1 for v in normalized_long_track_overrides.values() if v == "mix")

            if mix_flags:
                waveform_targets = []
                for path in target_files:
                    override = str(normalized_long_track_overrides.get(path, "")).strip().lower()
                    if override == "single":
                        continue
                    if override == "mix" or mix_flags.get(path, False):
                        waveform_targets.append(path)
                skipped_regular_tracks = max(0, len(target_files) - len(waveform_targets))
                if self._debug_readout_enabled_from_ui():
                    debug_msg = (
                        f"[waveform] Mix detection: {len(waveform_targets)} mix(es), "
                        f"{skipped_regular_tracks} regular/single track(s) skipped"
                    )
                    if forced_single_count or forced_mix_count:
                        debug_msg += (
                            f" (long-track overrides: {forced_mix_count} forced mix, "
                            f"{forced_single_count} forced single)"
                        )
                    self._append_debug_readout_line(debug_msg)
            elif normalized_long_track_overrides:
                waveform_targets = [
                    path
                    for path in target_files
                    if str(normalized_long_track_overrides.get(path, "")).strip().lower() != "single"
                ]
                skipped_regular_tracks = max(0, len(target_files) - len(waveform_targets))

        if not waveform_targets:
            self.status_label.setText("Status: No waveform-target tracks selected — skipping waveform editor")
            if timestamp_workflow:
                self.stage_label.setText(
                    "Waveform editor skipped — using the automatic timestamp scan for all files."
                )
            else:
                self.stage_label.setText("Waveform editor skipped — using automatic splitting for all files.")
            self.stage_label.setVisible(True)
            QApplication.processEvents()
            return True, {}

        if skipped_regular_tracks > 0:
            self.status_label.setText(
                f"Status: Opening waveform editor for {len(waveform_targets)} mix(es); "
                f"skipping {skipped_regular_tracks} regular track(s)"
            )
            QApplication.processEvents()

        points_map = {}
        total = len(waveform_targets)
        split_thresh_db = self._current_split_silence_threshold_db()
        split_seek_step_ms = self._normalize_split_seek_step_value(self.split_seek_step_combo.currentData())
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.stage_label.setVisible(True)
        try:
            for index, audio_file in enumerate(waveform_targets, start=1):
                base_name = os.path.basename(audio_file)
                self.status_label.setText(f"Status: Preparing waveform ({index}/{total}): {base_name}")
                if self._debug_readout_enabled_from_ui():
                    self._append_debug_readout_line(f"[waveform/{index}/{total}] Starting prep for {base_name}")
                file_mix_strategy = str(
                    normalized_mix_strategy_overrides.get(audio_file, "")
                ).strip().lower()
                use_transition_seed = bool(
                    mode == "assisted"
                    and not timestamp_workflow
                    and file_mix_strategy == "transition"
                )

                result_payload = {
                    "loaded": False,
                    "cancelled": False,
                    "error": "",
                    "peaks": [],
                    "duration": 0.0,
                    "assisted_points": [],
                    "progress": 0.0,
                    "progress_detail": "",
                }
                debug_progress_state = {
                    "detail": "",
                    "bucket": -1,
                }

                load_thread = NativeWaveformLoadThread(
                    audio_file,
                    assisted_mode=bool(
                        mode == "assisted"
                        and not timestamp_assisted
                        and not use_transition_seed
                    ),
                    split_silence_thresh_db=split_thresh_db,
                    split_seek_step_ms=split_seek_step_ms,
                )

                def _update_waveform_prep_progress(file_percent, detail_text):
                    try:
                        file_pct = float(file_percent)
                    except Exception:
                        file_pct = 0.0
                    file_pct = max(0.0, min(100.0, file_pct))
                    previous_pct = float(result_payload.get("progress", 0.0) or 0.0)
                    if file_pct < previous_pct:
                        file_pct = previous_pct
                    detail = str(detail_text or "").strip()
                    result_payload["progress"] = file_pct
                    result_payload["progress_detail"] = detail
                    global_pct = (((float(index) - 1.0) + (file_pct / 100.0)) / float(max(1, total))) * 100.0
                    self.progress_bar.setValue(max(0, min(100, int(round(global_pct)))))
                    suffix = f" - {detail}" if detail else ""
                    self.stage_label.setText(
                        f"Preparing waveform {index}/{total} ({int(round(file_pct))}%) - {base_name}{suffix}"
                    )
                    self.stage_label.setVisible(True)
                    QApplication.processEvents()
                    if self._debug_readout_enabled_from_ui():
                        rounded_pct = int(round(file_pct))
                        bucket = int(rounded_pct / 5)
                        should_log = False
                        if detail and detail != str(debug_progress_state.get("detail", "")):
                            should_log = True
                            debug_progress_state["detail"] = detail
                        elif bucket > int(debug_progress_state.get("bucket", -1)):
                            should_log = True
                        if should_log:
                            debug_progress_state["bucket"] = bucket
                            detail_suffix = f" - {detail}" if detail else ""
                            self._append_debug_readout_line(
                                f"[waveform/{index}/{total}] {base_name}: {rounded_pct}%{detail_suffix}"
                            )

                def _handle_loaded(peaks, duration_seconds, assisted_points):
                    result_payload["loaded"] = True
                    result_payload["peaks"] = list(peaks or [])
                    result_payload["duration"] = float(duration_seconds or 0.0)
                    result_payload["assisted_points"] = [float(v) for v in list(assisted_points or [])]
                    _update_waveform_prep_progress(100.0, "Waveform ready")

                def _handle_failed(message):
                    msg = str(message or "").strip()
                    if msg.lower() == "cancelled":
                        result_payload["cancelled"] = True
                    elif msg:
                        result_payload["error"] = msg
                    _update_waveform_prep_progress(result_payload.get("progress", 0.0), "Waveform prep failed")

                def _handle_progress(percent, detail):
                    _update_waveform_prep_progress(percent, detail)

                load_thread.loaded.connect(_handle_loaded)
                load_thread.failed.connect(_handle_failed)
                load_thread.progress.connect(_handle_progress)
                _update_waveform_prep_progress(0.0, "Starting...")
                load_thread.start()
                while load_thread.isRunning():
                    QApplication.processEvents()
                    load_thread.wait(30)
                QApplication.processEvents()

                if not result_payload["loaded"]:
                    thread_loaded = bool(getattr(load_thread, "result_loaded", False))
                    if thread_loaded:
                        _handle_loaded(
                            getattr(load_thread, "result_peaks", []),
                            getattr(load_thread, "result_duration_seconds", 0.0),
                            getattr(load_thread, "result_assisted_points", []),
                        )
                    else:
                        if bool(getattr(load_thread, "result_cancelled", False)):
                            result_payload["cancelled"] = True
                        thread_error = str(getattr(load_thread, "result_error", "")).strip()
                        if thread_error:
                            result_payload["error"] = thread_error
                try:
                    load_thread.deleteLater()
                except Exception:
                    pass

                if not result_payload["loaded"]:
                    if result_payload["cancelled"]:
                        if self._debug_readout_enabled_from_ui():
                            self._append_debug_readout_line(
                                f"[waveform/{index}/{total}] Cancelled prep for {base_name}"
                            )
                        prompt_title = "Waveform Loading Cancelled"
                        prompt_text = (
                            f"Waveform preparation was cancelled for '{base_name}'. "
                            + (
                                "Continue with the normal timestamp scan for this file?"
                                if timestamp_workflow
                                else "Continue with automatic splitting for this file?"
                            )
                        )
                    else:
                        error_text = result_payload["error"] or "Unknown waveform preparation error."
                        if self._debug_readout_enabled_from_ui():
                            self._append_debug_readout_line(
                                f"[waveform/{index}/{total}/error] {base_name}: {error_text}"
                            )
                        prompt_title = "Waveform Editor Error"
                        prompt_text = (
                            f"Waveform editor failed for '{base_name}':\n\n{error_text}\n\n"
                            + (
                                "Continue with the normal timestamp scan for this file?"
                                if timestamp_workflow
                                else "Continue with automatic splitting for this file?"
                            )
                        )
                    proceed = self._ask_styled_yes_no(
                        prompt_title,
                        prompt_text,
                        yes_text="Continue",
                        no_text="Cancel Run",
                        default_yes=True,
                    )
                    if not proceed:
                        return False, {}
                    continue

                initial_points = [float(v) for v in list(result_payload["assisted_points"] or [])]
                if timestamp_assisted or use_transition_seed:
                    if self._debug_readout_enabled_from_ui():
                        self._append_debug_readout_line(
                            f"[waveform/{index}/{total}] {base_name}: preparing "
                            f"{'auto timestamp' if timestamp_assisted else 'continuous mix'} boundary seeds"
                        )
                    seed_payload = {
                        "loaded": False,
                        "cancelled": False,
                        "error": "",
                        "points": [],
                    }
                    seed_runtime_config = (
                        timestamp_seed_config if timestamp_assisted else split_transition_seed_config
                    )
                    seed_thread = TimestampSeedLoadThread(
                        audio_file,
                        runtime_config=seed_runtime_config,
                    )

                    def _handle_seed_loaded(points):
                        seed_payload["loaded"] = True
                        seed_payload["points"] = [float(v) for v in list(points or []) if float(v) > 0.0]

                    def _handle_seed_failed(message):
                        msg = str(message or "").strip()
                        if msg.lower() == "cancelled":
                            seed_payload["cancelled"] = True
                        elif msg:
                            seed_payload["error"] = msg

                    def _handle_seed_progress(percent, detail):
                        detail_text = str(detail or "").strip() or (
                            "Scanning timestamp boundaries…"
                            if timestamp_assisted
                            else "Scanning continuous mix boundaries…"
                        )
                        self.stage_label.setText(
                            f"Preparing waveform {index}/{total} - {base_name} - {detail_text}"
                        )
                        self.stage_label.setVisible(True)
                        QApplication.processEvents()
                        if self._debug_readout_enabled_from_ui():
                            rounded_pct = max(0, min(100, int(round(float(percent or 0.0)))))
                            self._append_debug_readout_line(
                                f"[waveform/{index}/{total}] {base_name}: "
                                f"{'timestamp' if timestamp_assisted else 'continuous mix'} "
                                f"seed scan {rounded_pct}% - {detail_text}"
                            )

                    seed_thread.loaded.connect(_handle_seed_loaded)
                    seed_thread.failed.connect(_handle_seed_failed)
                    seed_thread.progress.connect(_handle_seed_progress)
                    seed_thread.start()
                    while seed_thread.isRunning():
                        QApplication.processEvents()
                        seed_thread.wait(30)
                    QApplication.processEvents()

                    if not seed_payload["loaded"]:
                        thread_loaded = bool(getattr(seed_thread, "result_loaded", False))
                        if thread_loaded:
                            _handle_seed_loaded(getattr(seed_thread, "result_points", []))
                        else:
                            if bool(getattr(seed_thread, "result_cancelled", False)):
                                seed_payload["cancelled"] = True
                            thread_error = str(getattr(seed_thread, "result_error", "")).strip()
                            if thread_error:
                                seed_payload["error"] = thread_error
                    try:
                        seed_thread.deleteLater()
                    except Exception:
                        pass

                    if not seed_payload["loaded"]:
                        if seed_payload["cancelled"]:
                            if timestamp_assisted:
                                prompt_title = "Timestamp Seed Scan Cancelled"
                                prompt_text = (
                                    f"Automatic timestamp boundary seeding was cancelled for '{base_name}'. "
                                    "Continue with the normal timestamp scan for this file?"
                                )
                            else:
                                prompt_title = "Continuous Mix Seed Scan Cancelled"
                                prompt_text = (
                                    f"Continuous-mix boundary seeding was cancelled for '{base_name}'. "
                                    "Continue with automatic splitting for this file?"
                                )
                        else:
                            error_text = seed_payload["error"] or "Unknown timestamp boundary scan error."
                            if timestamp_assisted:
                                prompt_title = "Timestamp Seed Scan Error"
                                prompt_text = (
                                    f"Automatic timestamp boundary seeding failed for '{base_name}':\n\n"
                                    f"{error_text}\n\n"
                                    "Continue with the normal timestamp scan for this file?"
                                )
                            else:
                                prompt_title = "Continuous Mix Seed Scan Error"
                                prompt_text = (
                                    f"Continuous-mix boundary seeding failed for '{base_name}':\n\n"
                                    f"{error_text}\n\n"
                                    "Continue with automatic splitting for this file?"
                                )
                        proceed = self._ask_styled_yes_no(
                            prompt_title,
                            prompt_text,
                            yes_text="Continue",
                            no_text="Cancel Run",
                            default_yes=True,
                        )
                        if not proceed:
                            return False, {}
                        continue
                    initial_points = list(seed_payload["points"])

                editor = NativeWaveformEditorDialog(
                    self,
                    audio_file,
                    result_payload["peaks"],
                    result_payload["duration"],
                    initial_points=initial_points,
                )
                result = editor.exec()
                if result == QDialog.Accepted:
                    selected = [float(p) for p in list(editor.selected_points or []) if float(p) > 0.0]
                    if selected:
                        points_map[audio_file] = sorted(set(selected))
                    elif timestamp_workflow:
                        # Preserve an explicit "single timestamp segment" choice for
                        # manual/assisted timestamping runs.
                        points_map[audio_file] = [0.0]
                    continue

                proceed = self._ask_styled_yes_no(
                    "Waveform Editing Cancelled",
                    (
                        f"Waveform editing was cancelled for '{base_name}'. "
                        + (
                            "Continue with the normal timestamp scan for this file?"
                            if timestamp_workflow
                            else "Continue with automatic splitting for this file?"
                        )
                    ),
                    yes_text="Continue",
                    no_text="Cancel Run",
                    default_yes=True,
                )
                if not proceed:
                    return False, {}

            self.progress_bar.setValue(100)
            self.stage_label.setText("Waveform preparation complete.")
            QApplication.processEvents()
            if self._debug_readout_enabled_from_ui():
                self._append_debug_readout_line("[waveform] Waveform preparation complete")
            return True, points_map
        finally:
            self.progress_bar.setValue(0)
            self.stage_label.setVisible(False)

    def _update_mode_specific_ui(self):
        """Update all mode-dependent UI.

        Settings-page controls stay editable so the full config is visible in
        one place. Splitter-page controls are still driven by the active
        splitter workflow.
        """
        mode_split_only = getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id")
        mode_auto = getattr(mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual")

        # --- Settings-card controls ----------------------------------------
        # Settings are always editable here; the page is no longer scoped to
        # a temporary "settings for this mode" selector.
        s_split_only = False
        s_auto = True
        s_id_mode = True

        fingerprint_controls = [
            self.fingerprint_sample_spin,
            self.fingerprint_probe_combo,
            self.show_id_source_check,
        ]
        self._set_enabled_group(fingerprint_controls, s_id_mode)

        # Shazam is used in identification modes and timestamping, but not split-only.
        self._set_enabled_group([self.shazam_enabled_check], not s_split_only)

        auto_tracklist_controls = [
            self.transition_detection_profile_combo,
            self.window_spin,
            self.step_spin,
            self.min_segment_spin,
            self.fallback_interval_spin,
            self.max_windows_spin,
            self.conf_spin,
            self.boundary_backtrack_spin,
            self.auto_no_identify_check,
            self.auto_tracklist_essentia_enabled_check,
            self.auto_tracklist_essentia_conf_spin,
            self.auto_tracklist_essentia_max_points_spin,
        ]
        self._set_enabled_group(auto_tracklist_controls, s_auto)
        self._set_enabled_group(
            [
                self.essentia_simple_assist_check,
            ],
            s_auto,
        )
        self._set_enabled_group([self.essentia_simple_strength_combo], (not s_split_only))

        enrichment_controls = [
            self.essentia_enabled_check,
            self.essentia_when_missing_check,
            self.essentia_conf,
            self.essentia_max_tags_spin,
            self.essentia_analysis_seconds_spin,
            self.local_bpm_check,
        ]
        self._set_enabled_group(enrichment_controls, s_id_mode)
        self._set_enabled_group(
            [
                self.essentia_simple_genre_help_check,
                self.essentia_simple_when_missing_check,
            ],
            s_id_mode,
        )
        self._update_essentia_simple_ui_state()
        self._update_probe_hint()

        if hasattr(self, 'timestamp_hint_label'):
            self.timestamp_hint_label.setText(
                "These settings drive Timestamping and are also reused for Continuous Mix boundary detection."
            )
            self.timestamp_hint_label.setVisible(True)

        # --- Splitter-page controls (driven by the actual splitter mode) ----
        splitter_workflow = self._selected_splitter_workflow()
        sp_auto = self._splitter_workflow_is_timestamp(splitter_workflow)

        if hasattr(self, "split_mode_dropdown"):
            split_enabled = self._visual_splitter_available_for_ui()
            if not self._visual_splitter_available_for_ui():
                split_enabled = False
            if self.split_mode_dropdown.isEnabled() != split_enabled:
                self.split_mode_dropdown.setEnabled(split_enabled)
                self._refresh_widget_style(self.split_mode_dropdown)
            if hasattr(self, "split_mode_radio_buttons"):
                for button in self.split_mode_radio_buttons.values():
                    if button.isEnabled() != split_enabled:
                        button.setEnabled(split_enabled)
                        self._refresh_widget_style(button)
            self._update_split_mode_hint()
        if hasattr(self, "preview_mode_hint_label"):
            if sp_auto:
                self.preview_mode_hint_label.setText(
                    "Timestamping skips Full or Light preview selection."
                )
            else:
                self._refresh_preview_mode_hint_label(force=True)
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
        self._update_long_track_prompt_ui_state()

    def _set_settings_status(self, message, error=False):
        # Status label removed — settings auto-save silently now.
        pass

    def _display_name_for_api(self, api_name):
        api_key = str(api_name or "").strip().lower()
        if api_key == "acrcloud":
            return "ACRCloud"
        if api_key == "acoustid":
            return "AcoustID"
        if api_key == "lastfm":
            return "Last.fm"
        return "API"

    def _api_status_label_for_name(self, api_name):
        api_key = str(api_name or "").strip().lower()
        if api_key == "acrcloud":
            return getattr(self, "acr_api_status_label", None)
        if api_key == "acoustid":
            return getattr(self, "acoustid_api_status_label", None)
        if api_key == "lastfm":
            return getattr(self, "lastfm_api_status_label", None)
        return None

    def _set_api_chip_status(self, api_name, short_status, state="neutral"):
        label = self._api_status_label_for_name(api_name)
        if label is None:
            return
        display = self._display_name_for_api(api_name)
        label.setText(f"{display}: {short_status}")
        self._apply_inline_status_cell_style(label, state=state)

    def _api_payload_from_inputs(self, api_name):
        api_key = str(api_name or "").strip().lower()
        if api_key == "acrcloud":
            return {
                "host": self.acr_host_input.text().strip(),
                "access_key": self.acr_access_key_input.text().strip(),
                "access_secret": self.acr_secret_input.text().strip(),
                "timeout": 10,
            }
        if api_key == "acoustid":
            return {
                "acoustid_api_key": self.acoustid_key_input.text().strip(),
            }
        if api_key == "lastfm":
            return {
                "lastfm_api_key": self.lastfm_key_input.text().strip(),
            }
        return {}

    def _api_payload_ready(self, api_name, payload):
        api_key = str(api_name or "").strip().lower()
        values = dict(payload or {})
        if api_key == "acrcloud":
            return bool(values.get("host") and values.get("access_key") and values.get("access_secret"))
        if api_key == "acoustid":
            return bool(values.get("acoustid_api_key"))
        if api_key == "lastfm":
            return bool(values.get("lastfm_api_key"))
        return False

    def _setup_api_validation_watchers(self):
        self._api_validation_blocked = False
        self._api_validation_tokens = {"acrcloud": 0, "acoustid": 0, "lastfm": 0}
        self._api_validation_threads = {}
        self._api_validation_timers = {}

        for api_name in ("acrcloud", "acoustid", "lastfm"):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(600)
            timer.timeout.connect(lambda name=api_name: self._start_api_validation(name))
            self._api_validation_timers[api_name] = timer

        for line_edit in (self.acr_host_input, self.acr_access_key_input, self.acr_secret_input):
            line_edit.textChanged.connect(lambda _text, name="acrcloud": self._schedule_api_validation(name))
            line_edit.editingFinished.connect(lambda name="acrcloud": self._schedule_api_validation(name, immediate=True))

        self.acoustid_key_input.textChanged.connect(
            lambda _text, name="acoustid": self._schedule_api_validation(name)
        )
        self.acoustid_key_input.editingFinished.connect(
            lambda name="acoustid": self._schedule_api_validation(name, immediate=True)
        )
        self.lastfm_key_input.textChanged.connect(
            lambda _text, name="lastfm": self._schedule_api_validation(name)
        )
        self.lastfm_key_input.editingFinished.connect(
            lambda name="lastfm": self._schedule_api_validation(name, immediate=True)
        )

    def _schedule_api_validation(self, api_name, immediate=False):
        if getattr(self, "_api_validation_blocked", False):
            return
        api_key = str(api_name or "").strip().lower()
        timer = (getattr(self, "_api_validation_timers", {}) or {}).get(api_key)
        if timer is None:
            return

        payload = self._api_payload_from_inputs(api_key)
        if not self._api_payload_ready(api_key, payload):
            timer.stop()
            self._set_api_chip_status(api_key, "Missing", state="missing")
            return

        if immediate:
            timer.stop()
            self._start_api_validation(api_key)
            return

        self._set_api_chip_status(api_key, "Checking...", state="checking")
        timer.start()

    def _start_api_validation(self, api_name):
        if getattr(self, "_api_validation_blocked", False):
            return
        api_key = str(api_name or "").strip().lower()
        payload = self._api_payload_from_inputs(api_key)
        if not self._api_payload_ready(api_key, payload):
            self._set_api_chip_status(api_key, "Missing", state="missing")
            return

        next_token = int((self._api_validation_tokens or {}).get(api_key, 0)) + 1
        self._api_validation_tokens[api_key] = next_token
        self._set_api_chip_status(api_key, "Checking...", state="checking")

        worker = ApiValidationThread(
            api_key,
            next_token,
            payload,
            core_getter=lambda: mixsplitr_core,
            identify_getter=lambda: mixsplitr_identify,
            lazy_import_backend=_lazy_import_backend,
        )
        worker.result_ready.connect(self._on_api_validation_result)
        worker.finished.connect(lambda name=api_key, thread=worker: self._on_api_validation_finished(name, thread))
        self._api_validation_threads[api_key] = worker
        worker.start()

    def _on_api_validation_finished(self, api_name, worker):
        if worker is None:
            return
        try:
            if (getattr(self, "_api_validation_threads", {}) or {}).get(api_name) is worker:
                self._api_validation_threads.pop(api_name, None)
        except Exception:
            pass
        worker.deleteLater()

    def _on_api_validation_result(self, api_name, token, ok, message, error):
        current_token = int((getattr(self, "_api_validation_tokens", {}) or {}).get(api_name, 0))
        if int(token) != current_token:
            return

        display = self._display_name_for_api(api_name)
        detail = str(message or "").strip()
        if ok:
            self._set_api_chip_status(api_name, "Valid", state="valid")
            self._set_api_test_status(detail or f"{display} credentials are valid", error=False)
            return

        lowered = detail.lower()
        if lowered.startswith("missing ") or lowered.startswith("enter "):
            self._set_api_chip_status(api_name, "Missing", state="missing")
        else:
            self._set_api_chip_status(api_name, "Invalid", state="invalid")
        self._set_api_test_status(detail or f"{display} credentials are invalid", error=bool(error))

    def _refresh_api_validation_statuses(self, immediate=False):
        for api_name in ("acrcloud", "acoustid", "lastfm"):
            self._schedule_api_validation(api_name, immediate=immediate)

    def _set_api_test_status(self, message, error=False):
        if not hasattr(self, "api_test_status_label"):
            return
        color = "#C4C7C5" if not error else "#E45D5D"
        self.api_test_status_label.setStyleSheet(f"color: {color};")
        self.api_test_status_label.setText(f"API Test: {message}")

    def _refresh_essentia_runtime_status(self):
        if not hasattr(self, "essentia_runtime_status_value"):
            return
        if mixsplitr_essentia is None or not hasattr(mixsplitr_essentia, "get_runtime_status"):
            self.essentia_runtime_status_value.setText("Unavailable (module import failed)")
            return
        try:
            runtime = mixsplitr_essentia.get_runtime_status()
            runtime_dict = runtime.to_dict() if hasattr(runtime, "to_dict") else dict(runtime or {})
        except Exception as exc:
            self.essentia_runtime_status_value.setText(f"Unavailable ({type(exc).__name__}: {exc})")
            return

        available = bool(runtime_dict.get("available", False))
        version = str(runtime_dict.get("essentia_version", "") or "").strip()
        reason = str(runtime_dict.get("reason", "") or "").strip()
        if available:
            if version:
                self.essentia_runtime_status_value.setText(f"Available (v{version})")
            else:
                self.essentia_runtime_status_value.setText("Available")
            return
        if reason:
            self.essentia_runtime_status_value.setText(f"Unavailable ({reason})")
        else:
            self.essentia_runtime_status_value.setText("Unavailable")

    def _set_settings_row_visibility(self, widget, visible):
        if widget is None:
            return
        state = bool(visible)
        widget.setVisible(state)
        paired_label = getattr(widget, "_paired_form_label", None)
        if paired_label is not None:
            paired_label.setVisible(state)

    def _set_essentia_advanced_controls_visible(self, visible):
        state = bool(visible)
        for widget in list(getattr(self, "_essentia_advanced_widgets", []) or []):
            self._set_settings_row_visibility(widget, state)
        card = getattr(self, "essentia_advanced_card", None)
        if card is not None:
            card.setVisible(state)

    def _essentia_simple_profiles(self):
        balanced = {
            "auto_tracklist_essentia_min_confidence": float(
                getattr(mixsplitr_core, "ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT", 0.36)
            ),
            "auto_tracklist_essentia_max_points": int(
                getattr(mixsplitr_core, "ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT", 2400)
            ),
            "essentia_genre_enrichment_min_confidence": float(
                getattr(mixsplitr_core, "ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT", 0.34)
            ),
            "essentia_genre_enrichment_max_tags": int(
                getattr(mixsplitr_core, "ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT", 2)
            ),
        }
        profiles = {
            "balanced": balanced,
        }
        profiles.update(self.ESSENTIA_SIMPLE_PROFILE_VALUES)
        return profiles

    def _essentia_simple_profile_values_from_widgets(self):
        return {
            "auto_tracklist_essentia_min_confidence": float(self.auto_tracklist_essentia_conf_spin.value()),
            "auto_tracklist_essentia_max_points": int(self.auto_tracklist_essentia_max_points_spin.value()),
            "essentia_genre_enrichment_min_confidence": float(self.essentia_conf.value()),
            "essentia_genre_enrichment_max_tags": int(self.essentia_max_tags_spin.value()),
        }

    def _matches_essentia_simple_profile(self, values, profile_values):
        float_keys = (
            "auto_tracklist_essentia_min_confidence",
            "essentia_genre_enrichment_min_confidence",
        )
        int_keys = (
            "auto_tracklist_essentia_max_points",
            "essentia_genre_enrichment_max_tags",
        )
        for key in float_keys:
            if abs(float(values.get(key, 0.0)) - float(profile_values.get(key, 0.0))) > 1e-6:
                return False
        for key in int_keys:
            if int(values.get(key, 0)) != int(profile_values.get(key, 0)):
                return False
        return True

    def _detect_essentia_simple_profile(self):
        values = self._essentia_simple_profile_values_from_widgets()
        profiles = self._essentia_simple_profiles()
        for key in self.ESSENTIA_SIMPLE_PROFILE_KEYS:
            profile_values = profiles.get(key)
            if isinstance(profile_values, dict) and self._matches_essentia_simple_profile(values, profile_values):
                return key
        return "custom"

    def _apply_essentia_simple_profile_to_advanced(self, profile_key):
        profile = (self._essentia_simple_profiles() or {}).get(str(profile_key or "").strip().lower())
        if not isinstance(profile, dict):
            return
        self.auto_tracklist_essentia_conf_spin.setValue(
            float(profile.get("auto_tracklist_essentia_min_confidence", 0.36))
        )
        self.auto_tracklist_essentia_max_points_spin.setValue(
            int(profile.get("auto_tracklist_essentia_max_points", 2400))
        )
        self.essentia_conf.setValue(
            float(profile.get("essentia_genre_enrichment_min_confidence", 0.34))
        )
        self.essentia_max_tags_spin.setValue(
            int(profile.get("essentia_genre_enrichment_max_tags", 2))
        )

    def _update_essentia_simple_ui_state(self):
        if not hasattr(self, "essentia_simple_when_missing_check"):
            return
        allow_missing_toggle = bool(
            getattr(self, "essentia_simple_genre_help_check", None)
            and self.essentia_simple_genre_help_check.isEnabled()
            and self.essentia_simple_genre_help_check.isChecked()
        )
        self.essentia_simple_when_missing_check.setEnabled(allow_missing_toggle)
        paired_label = getattr(self.essentia_simple_when_missing_check, "_paired_form_label", None)
        if paired_label is not None:
            paired_label.setEnabled(allow_missing_toggle)
            self._refresh_widget_style(paired_label)
        self._refresh_widget_style(self.essentia_simple_when_missing_check)

    def _on_essentia_simple_controls_changed(self):
        if getattr(self, "_essentia_simple_syncing", False):
            return
        if not hasattr(self, "essentia_simple_strength_combo"):
            return
        self._essentia_simple_syncing = True
        try:
            self.auto_tracklist_essentia_enabled_check.setChecked(
                bool(self.essentia_simple_assist_check.isChecked())
            )
            self.essentia_enabled_check.setChecked(
                bool(self.essentia_simple_genre_help_check.isChecked())
            )
            self.essentia_when_missing_check.setChecked(
                bool(self.essentia_simple_when_missing_check.isChecked())
            )
            selected_profile = str(self.essentia_simple_strength_combo.currentData() or "").strip().lower()
            if selected_profile in self.ESSENTIA_SIMPLE_PROFILE_KEYS:
                self._apply_essentia_simple_profile_to_advanced(selected_profile)
        finally:
            self._essentia_simple_syncing = False
        self._sync_essentia_simple_from_advanced()

    def _sync_essentia_simple_from_advanced(self):
        if getattr(self, "_essentia_simple_syncing", False):
            return
        if not hasattr(self, "essentia_simple_strength_combo"):
            return
        self._essentia_simple_syncing = True
        try:
            self.essentia_simple_assist_check.setChecked(
                bool(self.auto_tracklist_essentia_enabled_check.isChecked())
            )
            self.essentia_simple_genre_help_check.setChecked(
                bool(self.essentia_enabled_check.isChecked())
            )
            self.essentia_simple_when_missing_check.setChecked(
                bool(self.essentia_when_missing_check.isChecked())
            )
            profile_key = self._detect_essentia_simple_profile()
            profile_idx = self.essentia_simple_strength_combo.findData(profile_key)
            if profile_idx >= 0:
                self.essentia_simple_strength_combo.setCurrentIndex(profile_idx)
        finally:
            self._essentia_simple_syncing = False
        self._update_essentia_simple_ui_state()

    def _transition_detection_profiles(self):
        balanced = {
            "auto_tracklist_window_seconds": 18,
            "auto_tracklist_step_seconds": 12,
            "auto_tracklist_min_segment_seconds": 30,
            "auto_tracklist_min_confidence": 0.58,
            "auto_tracklist_boundary_backtrack_seconds": 0.0,
            "auto_tracklist_essentia_min_confidence": float(
                getattr(mixsplitr_core, "ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT", 0.36)
            ),
            "auto_tracklist_essentia_max_points": int(
                getattr(mixsplitr_core, "ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT", 2400)
            ),
        }
        profiles = {
            "balanced": balanced,
        }
        profiles.update(self.TRANSITION_DETECTION_PROFILE_VALUES)
        return profiles

    def _transition_detection_profile_values_from_widgets(self):
        return {
            "auto_tracklist_window_seconds": int(self.window_spin.value()),
            "auto_tracklist_step_seconds": int(self.step_spin.value()),
            "auto_tracklist_min_segment_seconds": int(self.min_segment_spin.value()),
            "auto_tracklist_min_confidence": float(self.conf_spin.value()),
            "auto_tracklist_boundary_backtrack_seconds": float(self.boundary_backtrack_spin.value()),
            "auto_tracklist_essentia_min_confidence": float(self.auto_tracklist_essentia_conf_spin.value()),
            "auto_tracklist_essentia_max_points": int(self.auto_tracklist_essentia_max_points_spin.value()),
        }

    def _matches_transition_detection_profile(self, values, profile_values):
        float_keys = (
            "auto_tracklist_min_confidence",
            "auto_tracklist_boundary_backtrack_seconds",
            "auto_tracklist_essentia_min_confidence",
        )
        int_keys = (
            "auto_tracklist_window_seconds",
            "auto_tracklist_step_seconds",
            "auto_tracklist_min_segment_seconds",
            "auto_tracklist_essentia_max_points",
        )
        for key in float_keys:
            if abs(float(values.get(key, 0.0)) - float(profile_values.get(key, 0.0))) > 1e-6:
                return False
        for key in int_keys:
            if int(values.get(key, 0)) != int(profile_values.get(key, 0)):
                return False
        return True

    def _detect_transition_detection_profile(self):
        values = self._transition_detection_profile_values_from_widgets()
        profiles = self._transition_detection_profiles()
        for key in self.TRANSITION_DETECTION_PROFILE_KEYS:
            profile_values = profiles.get(key)
            if isinstance(profile_values, dict) and self._matches_transition_detection_profile(values, profile_values):
                return key
        return "custom"

    def _apply_transition_detection_profile_to_advanced(self, profile_key):
        profile = (self._transition_detection_profiles() or {}).get(str(profile_key or "").strip().lower())
        if not isinstance(profile, dict):
            return
        self.window_spin.setValue(
            int(profile.get("auto_tracklist_window_seconds", 18))
        )
        self.step_spin.setValue(
            int(profile.get("auto_tracklist_step_seconds", 12))
        )
        self.min_segment_spin.setValue(
            int(profile.get("auto_tracklist_min_segment_seconds", 30))
        )
        self.conf_spin.setValue(
            float(profile.get("auto_tracklist_min_confidence", 0.58))
        )
        self.boundary_backtrack_spin.setValue(
            float(profile.get("auto_tracklist_boundary_backtrack_seconds", 0.0))
        )
        self.auto_tracklist_essentia_conf_spin.setValue(
            float(profile.get("auto_tracklist_essentia_min_confidence", 0.36))
        )
        self.auto_tracklist_essentia_max_points_spin.setValue(
            int(profile.get("auto_tracklist_essentia_max_points", 2400))
        )

    def _on_transition_detection_profile_changed(self):
        if getattr(self, "_transition_detection_profile_syncing", False):
            return
        if not hasattr(self, "transition_detection_profile_combo"):
            return
        self._transition_detection_profile_syncing = True
        try:
            selected_profile = str(
                self.transition_detection_profile_combo.currentData() or ""
            ).strip().lower()
            if selected_profile in self.TRANSITION_DETECTION_PROFILE_KEYS:
                self._apply_transition_detection_profile_to_advanced(selected_profile)
        finally:
            self._transition_detection_profile_syncing = False
        self._sync_transition_detection_profile_from_advanced()

    def _sync_transition_detection_profile_from_advanced(self):
        if getattr(self, "_transition_detection_profile_syncing", False):
            return
        if not hasattr(self, "transition_detection_profile_combo"):
            return
        self._transition_detection_profile_syncing = True
        try:
            profile_key = self._detect_transition_detection_profile()
            profile_idx = self.transition_detection_profile_combo.findData(profile_key)
            if profile_idx >= 0:
                self.transition_detection_profile_combo.setCurrentIndex(profile_idx)
        finally:
            self._transition_detection_profile_syncing = False

    def _on_recording_silence_timeout_changed(self, value):
        self.recording_auto_stop_silence_seconds = max(2.0, min(120.0, float(value)))

    def _apply_config_to_widgets(self, config):
        mode_ui = self._ui_mode_for_internal(config.get("mode"))
        self._mode_syncing = True
        try:
            idx_split = self.mode_dropdown.findData(self._splitter_workflow_from_config(config))
            if idx_split >= 0:
                self.mode_dropdown.setCurrentIndex(idx_split)
            idx_ident = self.ident_mode_dropdown.findText(mode_ui)
            if idx_ident < 0:
                idx_ident = self.ident_mode_dropdown.findText("MusicBrainz / AcoustID")
            if idx_ident >= 0:
                self.ident_mode_dropdown.setCurrentIndex(idx_ident)
        finally:
            self._mode_syncing = False
        # Default Audio Splitter to Assisted on open.
        split_mode_value = "assisted" if self._visual_splitter_available_for_ui() else "silence"
        if hasattr(self, "split_mode_dropdown"):
            split_idx = self.split_mode_dropdown.findData(split_mode_value)
            if split_idx < 0:
                fallback_mode = "assisted" if self._visual_splitter_available_for_ui() else "silence"
                split_idx = self.split_mode_dropdown.findData(fallback_mode)
            if split_idx >= 0:
                self.split_mode_dropdown.setCurrentIndex(split_idx)
            self._sync_split_mode_radio_from_combo()
            self._update_split_mode_hint()

        output_dir = self._normalized_path(config.get("output_directory")) or self._default_output_directory()
        self.output_dir_input.setText(output_dir)
        output_format_key = self._validated_output_format(config.get("output_format", "flac"))
        format_index = self.output_format_combo.findData(output_format_key)
        if format_index < 0:
            format_index = 0
        self.output_format_combo.setCurrentIndex(format_index)
        rename_preset_key = self._normalize_rename_preset_value(config.get("rename_preset", "simple"))
        rename_preset_index = self.rename_preset_combo.findData(rename_preset_key)
        if rename_preset_index < 0:
            rename_preset_index = self.rename_preset_combo.findData("simple")
        if rename_preset_index < 0:
            rename_preset_index = 0
        self.rename_preset_combo.setCurrentIndex(rename_preset_index)
        self.preserve_source_format_check.setChecked(
            bool(config.get("preserve_source_format", False))
        )
        self.recording_force_wav_check.setChecked(
            bool(config.get("recording_force_wav", False))
        )
        self.recording_dir_input.setText(
            self._normalized_path(config.get("recording_directory")) or self._default_recording_directory()
        )
        self.timestamp_output_dir_input.setText(
            self._normalized_path(config.get("timestamp_output_directory"))
        )
        self.temp_workspace_dir_input.setText(
            self._normalized_path(config.get("temp_workspace_directory")) or self._default_temp_workspace_directory()
        )
        self.manifest_dir_input.setText(
            self._normalized_path(config.get("manifest_directory")) or self._default_manifest_directory()
        )
        self.cd_output_dir_input.setText(
            self._normalized_path(config.get("cd_rip_output_directory")) or self._default_cd_output_directory()
        )

        self.acr_host_input.setText(str(config.get("host", "")))
        self.acr_access_key_input.setText(str(config.get("access_key", "")))
        self.acr_secret_input.setText(str(config.get("access_secret", "")))
        self.acoustid_key_input.setText(str(config.get("acoustid_api_key", "")))
        self.lastfm_key_input.setText(str(config.get("lastfm_api_key", "")))
        self.lastfm_genre_check.setChecked(bool(config.get("lastfm_genre_enrichment_enabled", True)))
        for line_edit in (
            self.output_dir_input,
            self.recording_dir_input,
            self.timestamp_output_dir_input,
            self.temp_workspace_dir_input,
            self.manifest_dir_input,
            self.cd_output_dir_input,
            self.acr_host_input,
            self.acr_access_key_input,
            self.acr_secret_input,
            self.acoustid_key_input,
            self.lastfm_key_input,
        ):
            try:
                line_edit.setCursorPosition(0)
            except Exception:
                pass

        # Each probe mode stores its own sample size so switching doesn't
        # carry over an inappropriate value.
        self._probe_sample_single = max(8, min(45, int(config.get("fingerprint_sample_seconds", 12))))
        self._probe_sample_multi = max(8, min(45, int(config.get("fingerprint_sample_seconds_multi", 12))))

        probe_mode = str(config.get("fingerprint_probe_mode", "single")).strip().lower()
        is_multi = probe_mode == "multi3"
        self.fingerprint_sample_spin.setValue(
            self._probe_sample_multi if is_multi else self._probe_sample_single
        )
        # Set combo AFTER spin + cached values are ready so the signal handler
        # doesn't try to swap before the cached values exist.
        if is_multi:
            self.fingerprint_probe_combo.setCurrentIndex(1)
        else:
            self.fingerprint_probe_combo.setCurrentIndex(0)
        self.shazam_enabled_check.setChecked(not bool(config.get("disable_shazam", False)))
        self.show_id_source_check.setChecked(bool(config.get("show_id_source", True)))
        self.local_bpm_check.setChecked(not bool(config.get("disable_local_bpm", False)))
        self.enable_album_search_check.setChecked(bool(config.get("enable_album_search", True)))
        artist_mode = self._normalize_artist_normalization_mode_value(
            config.get("artist_normalization_mode", ""),
            legacy_normalize_artists=bool(config.get("normalize_artists", True)),
        )
        artist_mode_idx = self.artist_normalization_mode_combo.findData(artist_mode)
        if artist_mode_idx < 0:
            artist_mode_idx = self.artist_normalization_mode_combo.findData("collab_only")
        if artist_mode_idx < 0:
            artist_mode_idx = 0
        self.artist_normalization_mode_combo.setCurrentIndex(artist_mode_idx)
        self.artist_normalization_strictness_spin.setValue(
            self._normalize_artist_normalization_strictness_value(
                config.get("artist_normalization_strictness", 0.92)
            )
        )
        self.artist_normalization_collapse_backing_band_check.setChecked(
            bool(config.get("artist_normalization_collapse_backing_band", False))
        )
        self.artist_normalization_review_ambiguous_check.setChecked(
            bool(config.get("artist_normalization_review_ambiguous", True))
        )
        self._update_artist_normalization_ui_state()
        self.deep_scan_check.setChecked(bool(config.get("deep_scan", False)))
        self.recording_keep_screen_awake_check.setChecked(
            bool(config.get("recording_keep_screen_awake", False))
        )
        self._set_split_sensitivity_offset(
            self._normalize_split_sensitivity_value(config.get("split_sensitivity_db", 0)),
            autosave=False,
        )
        split_seek_step_ms = self._normalize_split_seek_step_value(
            config.get("split_silence_seek_step_ms", 20)
        )
        seek_step_idx = self.split_seek_step_combo.findData(int(split_seek_step_ms))
        if seek_step_idx < 0:
            seek_step_idx = self.split_seek_step_combo.findData(20)
        if seek_step_idx < 0:
            seek_step_idx = 0
        self.split_seek_step_combo.setCurrentIndex(seek_step_idx)
        duplicate_policy = self._normalize_duplicate_policy_value(config.get("duplicate_policy", "skip"))
        duplicate_policy_idx = self.duplicate_policy_combo.findData(duplicate_policy)
        if duplicate_policy_idx < 0:
            duplicate_policy_idx = self.duplicate_policy_combo.findData("skip")
        if duplicate_policy_idx < 0:
            duplicate_policy_idx = 0
        self.duplicate_policy_combo.setCurrentIndex(duplicate_policy_idx)
        self.recording_silence_timeout_spin.setValue(
            max(2.0, min(120.0, float(config.get("recording_silence_auto_stop_seconds", 10.0))))
        )

        self.window_spin.setValue(max(8, min(60, int(config.get("auto_tracklist_window_seconds", 18)))))
        self.step_spin.setValue(max(5, min(120, int(config.get("auto_tracklist_step_seconds", 12)))))
        self.min_segment_spin.setValue(max(15, min(300, int(config.get("auto_tracklist_min_segment_seconds", 30)))))
        self.fallback_interval_spin.setValue(
            max(60, min(900, int(config.get("auto_tracklist_fallback_interval_seconds", 180))))
        )
        self.max_windows_spin.setValue(max(20, min(1000, int(config.get("auto_tracklist_max_windows", 120)))))
        self.conf_spin.setValue(max(0.25, min(0.95, float(config.get("auto_tracklist_min_confidence", 0.58)))))
        self.boundary_backtrack_spin.setValue(
            max(0.0, min(20.0, float(config.get("auto_tracklist_boundary_backtrack_seconds", 0.0))))
        )
        self.auto_no_identify_check.setChecked(bool(config.get("auto_tracklist_no_identify", True)))
        self.auto_tracklist_essentia_enabled_check.setChecked(
            bool(config.get("auto_tracklist_essentia_enabled", True))
        )
        self.auto_tracklist_essentia_conf_spin.setValue(
            max(0.05, min(0.95, float(config.get("auto_tracklist_essentia_min_confidence", 0.36))))
        )
        self.auto_tracklist_essentia_max_points_spin.setValue(
            max(200, min(6000, int(config.get("auto_tracklist_essentia_max_points", 2400))))
        )
        self.debug_readout_check.setChecked(bool(config.get("debug_readout_enabled", False)))
        self.developer_inspector_check.setChecked(bool(config.get("developer_inspector_enabled", False)))
        self.long_track_prompt_check.setChecked(bool(config.get("long_track_prompt_enabled", False)))
        self.long_track_threshold_spin.setValue(
            max(1.0, min(60.0, float(config.get("long_track_prompt_minutes", self.LONG_TRACK_PROMPT_THRESHOLD_MINUTES))))
        )

        self.essentia_enabled_check.setChecked(bool(config.get("essentia_genre_enrichment_enabled", True)))
        self.essentia_when_missing_check.setChecked(
            bool(config.get("essentia_genre_enrichment_when_missing_only", True))
        )
        self.essentia_conf.setValue(
            max(0.2, min(0.9, float(config.get("essentia_genre_enrichment_min_confidence", 0.34))))
        )
        self.essentia_max_tags_spin.setValue(
            max(1, min(5, int(config.get("essentia_genre_enrichment_max_tags", 2))))
        )
        self.essentia_analysis_seconds_spin.setValue(
            max(8, min(60, int(config.get("essentia_genre_enrichment_analysis_seconds", 28))))
        )
        self._refresh_essentia_runtime_status()
        self._set_essentia_advanced_controls_visible(
            bool(self.essentia_show_advanced_check.isChecked())
        )
        self._sync_essentia_simple_from_advanced()
        self._sync_transition_detection_profile_from_advanced()

        cd_format = str(config.get("cd_rip_format", "flac")).strip().lower()
        if cd_format == "wav":
            self.cd_format_combo.setCurrentIndex(1)
        elif cd_format == "mp3_320":
            self.cd_format_combo.setCurrentIndex(2)
        else:
            self.cd_format_combo.setCurrentIndex(0)
        self.cd_auto_metadata_check.setChecked(bool(config.get("cd_rip_auto_metadata", True)))
        self.cd_eject_check.setChecked(bool(config.get("cd_rip_eject_when_done", False)))
        self._update_mode_specific_ui()
        self._update_long_track_prompt_ui_state()
        self._refresh_recordings_list(select_path=self.recording_last_file)
        self._refresh_session_history_list()

    def _collect_config_from_widgets(self):
        current = self._read_config_from_disk()
        config = dict(current)
        config["mode"] = self._internal_mode_for_ui(self.ident_mode_dropdown.currentText())
        config["splitter_workflow"] = self._selected_splitter_workflow()
        config["split_mode"] = self._selected_split_mode()
        config["timeout"] = 10

        output_dir = self._normalized_path(self.output_dir_input.text()) or self._default_output_directory()
        config["output_directory"] = output_dir
        config["output_format"] = self._selected_output_format()
        config["rename_preset"] = self._normalize_rename_preset_value(
            self.rename_preset_combo.currentData() if hasattr(self, "rename_preset_combo") else "simple"
        )
        config["preserve_source_format"] = bool(self.preserve_source_format_check.isChecked())
        config["recording_force_wav"] = bool(self.recording_force_wav_check.isChecked())

        recording_dir = self._normalized_path(self.recording_dir_input.text())
        timestamp_output_dir = self._normalized_path(self.timestamp_output_dir_input.text())
        temp_workspace_dir = self._normalized_path(self.temp_workspace_dir_input.text())
        manifest_dir = self._normalized_path(self.manifest_dir_input.text())
        cd_output_dir = self._normalized_path(self.cd_output_dir_input.text())

        if recording_dir:
            config["recording_directory"] = recording_dir
        else:
            config.pop("recording_directory", None)
        if timestamp_output_dir:
            config["timestamp_output_directory"] = timestamp_output_dir
        else:
            config.pop("timestamp_output_directory", None)
        if temp_workspace_dir:
            config["temp_workspace_directory"] = temp_workspace_dir
        else:
            config.pop("temp_workspace_directory", None)
        if manifest_dir:
            config["manifest_directory"] = manifest_dir
        else:
            config.pop("manifest_directory", None)
        if cd_output_dir:
            config["cd_rip_output_directory"] = cd_output_dir
        else:
            config.pop("cd_rip_output_directory", None)

        host = self.acr_host_input.text().strip()
        access_key = self.acr_access_key_input.text().strip()
        access_secret = self.acr_secret_input.text().strip()
        acoustid_key = self.acoustid_key_input.text().strip()
        lastfm_key = self.lastfm_key_input.text().strip()

        if host:
            config["host"] = host
        else:
            config.pop("host", None)
        if access_key:
            config["access_key"] = access_key
        else:
            config.pop("access_key", None)
        if access_secret:
            config["access_secret"] = access_secret
        else:
            config.pop("access_secret", None)
        if acoustid_key:
            config["acoustid_api_key"] = acoustid_key
        else:
            config.pop("acoustid_api_key", None)
        if lastfm_key:
            config["lastfm_api_key"] = lastfm_key
        else:
            config.pop("lastfm_api_key", None)
        config["lastfm_genre_enrichment_enabled"] = bool(self.lastfm_genre_check.isChecked())

        # Store the current spinner value into the active mode's cache,
        # then persist both per-mode values so switching modes preserves each.
        is_multi = self.fingerprint_probe_combo.currentIndex() == 1
        if is_multi:
            self._probe_sample_multi = int(self.fingerprint_sample_spin.value())
        else:
            self._probe_sample_single = int(self.fingerprint_sample_spin.value())
        config["fingerprint_sample_seconds"] = getattr(self, '_probe_sample_single', 12)
        config["fingerprint_sample_seconds_multi"] = getattr(self, '_probe_sample_multi', 12)
        config["fingerprint_probe_mode"] = "multi3" if is_multi else "single"
        config["disable_shazam"] = not self.shazam_enabled_check.isChecked()
        config["show_id_source"] = bool(self.show_id_source_check.isChecked())
        config["disable_local_bpm"] = not self.local_bpm_check.isChecked()
        config["enable_album_search"] = bool(self.enable_album_search_check.isChecked())
        artist_mode = self._normalize_artist_normalization_mode_value(
            self.artist_normalization_mode_combo.currentData(),
            legacy_normalize_artists=True,
        )
        config["artist_normalization_mode"] = artist_mode
        config["artist_normalization_strictness"] = self._normalize_artist_normalization_strictness_value(
            self.artist_normalization_strictness_spin.value()
        )
        config["artist_normalization_collapse_backing_band"] = bool(
            self.artist_normalization_collapse_backing_band_check.isChecked()
        )
        config["artist_normalization_review_ambiguous"] = bool(
            self.artist_normalization_review_ambiguous_check.isChecked()
        )
        # Keep legacy bool in sync for backward compatibility with old installs.
        config["normalize_artists"] = bool(artist_mode != "off")
        config["deep_scan"] = bool(self.deep_scan_check.isChecked())
        config["recording_keep_screen_awake"] = bool(self.recording_keep_screen_awake_check.isChecked())
        config.pop("portable_mode_local_scan", None)
        config["split_sensitivity_db"] = self._split_sensitivity_offset()
        config["split_silence_seek_step_ms"] = self._normalize_split_seek_step_value(
            self.split_seek_step_combo.currentData()
        )
        config["duplicate_policy"] = self._normalize_duplicate_policy_value(
            self.duplicate_policy_combo.currentData()
        )
        config["recording_silence_auto_stop_seconds"] = float(self.recording_silence_timeout_spin.value())

        config["auto_tracklist_window_seconds"] = int(self.window_spin.value())
        config["auto_tracklist_step_seconds"] = int(self.step_spin.value())
        config["auto_tracklist_min_segment_seconds"] = int(self.min_segment_spin.value())
        config["auto_tracklist_fallback_interval_seconds"] = int(self.fallback_interval_spin.value())
        config["auto_tracklist_max_windows"] = int(self.max_windows_spin.value())
        config["auto_tracklist_min_confidence"] = float(self.conf_spin.value())
        config["auto_tracklist_boundary_backtrack_seconds"] = float(self.boundary_backtrack_spin.value())
        config["auto_tracklist_no_identify"] = bool(self.auto_no_identify_check.isChecked())
        config.pop("auto_tracklist_dry_run", None)
        config["auto_tracklist_essentia_enabled"] = bool(self.auto_tracklist_essentia_enabled_check.isChecked())
        config["auto_tracklist_essentia_min_confidence"] = float(self.auto_tracklist_essentia_conf_spin.value())
        config["auto_tracklist_essentia_max_points"] = int(self.auto_tracklist_essentia_max_points_spin.value())
        config["debug_readout_enabled"] = bool(self.debug_readout_check.isChecked())
        config["developer_inspector_enabled"] = bool(self.developer_inspector_check.isChecked())
        config["long_track_prompt_enabled"] = bool(self.long_track_prompt_check.isChecked())
        config["long_track_prompt_minutes"] = float(self.long_track_threshold_spin.value())

        config["essentia_unified_pipeline"] = bool(
            config.get(
                "essentia_unified_pipeline",
                getattr(mixsplitr_core, "ESSENTIA_UNIFIED_PIPELINE_DEFAULT", True),
            )
        )
        config["essentia_genre_enrichment_enabled"] = bool(self.essentia_enabled_check.isChecked())
        config["essentia_genre_enrichment_when_missing_only"] = bool(self.essentia_when_missing_check.isChecked())
        config["essentia_genre_enrichment_min_confidence"] = float(self.essentia_conf.value())
        config["essentia_genre_enrichment_max_tags"] = int(self.essentia_max_tags_spin.value())
        config["essentia_genre_enrichment_analysis_seconds"] = int(self.essentia_analysis_seconds_spin.value())

        cd_format_map = {0: "flac", 1: "wav", 2: "mp3_320"}
        config["cd_rip_format"] = cd_format_map.get(self.cd_format_combo.currentIndex(), "flac")
        config["cd_rip_auto_metadata"] = bool(self.cd_auto_metadata_check.isChecked())
        config["cd_rip_eject_when_done"] = bool(self.cd_eject_check.isChecked())
        return config

    def load_settings_from_config(self):
        self._autosave_blocked = True
        self._api_validation_blocked = True
        try:
            merged = self._default_ui_config()
            merged.update(self._read_config_from_disk())
            self._apply_config_to_widgets(merged)
            self._set_settings_status("Loaded from disk")
        finally:
            self._autosave_blocked = False
            self._api_validation_blocked = False
            if hasattr(self, '_autosave_timer'):
                self._autosave_timer.stop()
            self._refresh_api_validation_statuses(immediate=False)

    def _save_settings_to_config_internal(self, refresh_lists=True):
        try:
            config = self._collect_config_from_widgets()
            self._save_config_to_disk(config)
            temp_root = self._normalized_path(config.get("temp_workspace_directory"))
            if temp_root:
                try:
                    os.makedirs(temp_root, exist_ok=True)
                except Exception:
                    pass
                os.environ["MIXSPLITR_TEMP_ROOT"] = temp_root
            else:
                os.environ.pop("MIXSPLITR_TEMP_ROOT", None)
            if mixsplitr_identify is None:
                _lazy_import_backend("mixsplitr_identify")
            if mixsplitr_identify and hasattr(mixsplitr_identify, "set_acoustid_api_key"):
                mixsplitr_identify.set_acoustid_api_key(config.get("acoustid_api_key"))
            if refresh_lists:
                self._refresh_recordings_list(select_path=self.recording_last_file)
                self._refresh_session_history_list()
            self._set_settings_status("Auto-saving")
        except Exception as exc:
            self._set_settings_status(f"Save failed: {exc}", error=True)

    def _schedule_autosave(self):
        """Restart the debounce timer — actual save happens after 500ms of inactivity."""
        if getattr(self, '_autosave_blocked', False):
            return
        if hasattr(self, '_autosave_timer'):
            self._autosave_timer.start()

    def _autosave_settings(self):
        """Persist current widget state to disk (called by debounce timer or editingFinished)."""
        if getattr(self, '_autosave_blocked', False):
            return
        if hasattr(self, '_autosave_timer'):
            self._autosave_timer.stop()
        self._save_settings_to_config_internal(refresh_lists=False)

    def _refresh_id_source_badge_visibility(self):
        panel = getattr(self, "track_editor_panel", None)
        if panel is not None and hasattr(panel, "_refresh_track_list"):
            try:
                panel._refresh_track_list()
            except Exception:
                pass

        if hasattr(self, "history_list") and hasattr(self, "_refresh_session_history_list"):
            selected_path = ""
            try:
                current_item = self.history_list.currentItem()
                if current_item is not None:
                    selected_path = str(current_item.data(Qt.UserRole) or "")
            except Exception:
                selected_path = ""
            try:
                self._refresh_session_history_list(select_path=selected_path)
            except Exception:
                pass

    def finish_unsaved_preview_data(self):
        if self.preview_export_thread and self.preview_export_thread.isRunning():
            self.status_label.setText("Status: Preview export is already running")
            return

        cache_path = str(self._preview_cache_path_for_ui())
        temp_dir = str(self._preview_temp_dir_for_ui())
        track_count = self._unsaved_preview_track_count()
        artifact_bytes = self._preview_artifact_storage_bytes(cache_path=cache_path, temp_dir=temp_dir)
        cache_data = None
        is_timestamp_preview = False
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    cache_data = json.load(handle)
            except Exception:
                cache_data = None
        if isinstance(cache_data, dict):
            is_timestamp_preview = bool(
                (cache_data.get("config_snapshot") or {}).get("timestamp_editor_mode")
                or cache_data.get("timestamp_sessions")
            )
        if track_count <= 0:
            if hasattr(self, 'preview_mode_hint_label'):
                if artifact_bytes > 0:
                    self.preview_mode_hint_label.setText(
                        "No unsaved preview session tracks found. "
                        f"Temp/cache data currently uses {self._format_storage_bytes(artifact_bytes)}."
                    )
                else:
                    self.preview_mode_hint_label.setText("No unsaved preview data found.")
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return

        if hasattr(self, 'preview_mode_hint_label'):
            if artifact_bytes > 0:
                self.preview_mode_hint_label.setText(
                    f"Finishing unsaved {'timestamp review' if is_timestamp_preview else 'preview'} "
                    f"({track_count} {'segment(s)' if is_timestamp_preview else 'track(s)'}, "
                    f"{self._format_storage_bytes(artifact_bytes)} cached/temp data)..."
                )
            else:
                self.preview_mode_hint_label.setText(
                    f"Finishing unsaved {'timestamp review' if is_timestamp_preview else 'preview'} "
                    f"({track_count} {'segment(s)' if is_timestamp_preview else 'track(s)'})..."
                )
        self.pending_preview_open_editor = False
        if is_timestamp_preview:
            self._start_timestamp_preview_export(cache_path, temp_dir)
        else:
            self._start_preview_export(cache_path, temp_dir)
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def clear_unsaved_preview_data(self):
        cache_path = str(self._preview_cache_path_for_ui())
        temp_dir = str(self._preview_temp_dir_for_ui())
        track_count = self._unsaved_preview_track_count()
        bytes_before = self._preview_artifact_storage_bytes(cache_path=cache_path, temp_dir=temp_dir)
        if hasattr(self, 'preview_mode_hint_label') and bytes_before > 0:
            if track_count > 0:
                self.preview_mode_hint_label.setText(
                    "Unsaved preview data currently uses "
                    f"{self._format_storage_bytes(bytes_before)} across {track_count} track(s)."
                )
            else:
                self.preview_mode_hint_label.setText(
                    "Unsaved preview cache/temp data currently uses "
                    f"{self._format_storage_bytes(bytes_before)}."
                )

        removed_any = self._clear_unsaved_preview_artifacts(cache_path=cache_path, temp_dir=temp_dir)
        bytes_after = self._preview_artifact_storage_bytes(cache_path=cache_path, temp_dir=temp_dir)
        bytes_freed = max(0, bytes_before - bytes_after)
        if hasattr(self, 'preview_mode_hint_label'):
            if removed_any:
                if bytes_before > 0:
                    if bytes_after > 0:
                        self.preview_mode_hint_label.setText(
                            "Cleared unsaved preview cache/temp data "
                            f"(freed {self._format_storage_bytes(bytes_freed)}, "
                            f"{self._format_storage_bytes(bytes_after)} still present)."
                        )
                    else:
                        self.preview_mode_hint_label.setText(
                            "Cleared unsaved preview cache/temp data "
                            f"(freed {self._format_storage_bytes(bytes_freed)})."
                        )
                else:
                    self.preview_mode_hint_label.setText("Cleared unsaved preview cache/temp data.")
            else:
                if bytes_after > 0:
                    self.preview_mode_hint_label.setText(
                        "No preview artifacts were removed "
                        f"({self._format_storage_bytes(bytes_after)} still present)."
                    )
                else:
                    self.preview_mode_hint_label.setText("No unsaved preview data found.")
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    # ==========================================
    # RUNTIME LOGIC / THREADING BRIDGE
    # ==========================================
    def _preview_cache_path_for_ui(self):
        if mixsplitr_core and hasattr(mixsplitr_core, "get_cache_path"):
            return str(mixsplitr_core.get_cache_path("mixsplitr_cache.json"))
        return os.path.join(os.path.dirname(self._config_path()), "mixsplitr_cache.json")

    def _preview_temp_dir_for_ui(self):
        typed = ""
        if hasattr(self, "temp_workspace_dir_input"):
            typed = self._normalized_path(self.temp_workspace_dir_input.text())
        if typed:
            return os.path.join(typed, "preview")
        if mixsplitr_core and hasattr(mixsplitr_core, "get_runtime_temp_directory"):
            try:
                return str(mixsplitr_core.get_runtime_temp_directory("preview"))
            except Exception:
                pass
        return os.path.join(os.path.dirname(self._preview_cache_path_for_ui()), "mixsplitr_temp")

    def _preview_artifacts_exist(self, cache_path=None, temp_dir=None):
        cache_path = str(cache_path or self._preview_cache_path_for_ui())
        readable_path = cache_path.replace(".json", "_readable.txt")
        temp_dir = str(temp_dir or self._preview_temp_dir_for_ui())

        if os.path.isfile(cache_path) or os.path.isfile(readable_path):
            return True
        if not os.path.isdir(temp_dir):
            return False
        try:
            for _root, _dirs, files in os.walk(temp_dir):
                if files:
                    return True
        except Exception:
            return False
        return False

    def _preview_artifact_storage_bytes(self, cache_path=None, temp_dir=None):
        cache_path = str(cache_path or self._preview_cache_path_for_ui())
        readable_path = cache_path.replace(".json", "_readable.txt")
        temp_dir = str(temp_dir or self._preview_temp_dir_for_ui())

        total = 0
        for target in (cache_path, readable_path):
            try:
                if os.path.isfile(target):
                    total += int(os.path.getsize(target))
            except Exception:
                pass

        if os.path.isdir(temp_dir):
            try:
                for root, _dirs, files in os.walk(temp_dir):
                    for filename in files:
                        path = os.path.join(root, filename)
                        try:
                            total += int(os.path.getsize(path))
                        except Exception:
                            pass
            except Exception:
                pass
        return max(0, int(total))

    def _format_storage_bytes(self, size_bytes):
        try:
            value = float(max(0, int(size_bytes or 0)))
        except Exception:
            value = 0.0

        units = ("B", "KB", "MB", "GB", "TB", "PB")
        idx = 0
        while value >= 1024.0 and idx < (len(units) - 1):
            value /= 1024.0
            idx += 1

        if idx == 0:
            return f"{int(value)} {units[idx]}"
        if value >= 100:
            precision = 0
        elif value >= 10:
            precision = 1
        else:
            precision = 2
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        return f"{text} {units[idx]}"

    def _clear_unsaved_preview_artifacts(self, cache_path=None, temp_dir=None):
        cache_path = str(cache_path or self._preview_cache_path_for_ui())
        readable_path = cache_path.replace(".json", "_readable.txt")
        temp_dir = str(temp_dir or self._preview_temp_dir_for_ui())

        removed_any = False
        for target in (cache_path, readable_path):
            try:
                if os.path.exists(target):
                    os.remove(target)
                    removed_any = True
            except Exception:
                pass
        try:
            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                removed_any = True
        except Exception:
            pass
        return removed_any

    def _style_message_box(
        self,
        box,
        primary_button=None,
        label_min_width=420,
        label_max_width=760,
    ):
        if box is None:
            return
        try:
            label_min_px = max(0, int(label_min_width or 0))
        except Exception:
            label_min_px = 420
        try:
            label_max_px = max(label_min_px, int(label_max_width or label_min_px))
        except Exception:
            label_max_px = max(label_min_px, 760)
        box.setStyleSheet(
            """
            QMessageBox {
                background-color: #1D2024;
                color: #EBE7E1;
            }
            QMessageBox QLabel {
                color: #B4AEA4;
                font-size: 13px;
                min-width: %dpx;
                max-width: %dpx;
            }
            QMessageBox QPushButton#PopupSecondaryButton {
                background-color: #22262B;
                color: #EBE7E1;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QMessageBox QPushButton#PopupSecondaryButton:hover {
                background-color: #292E34;
            }
            QMessageBox QPushButton#PopupSecondaryButton:pressed {
                background-color: #1F2328;
            }
            QMessageBox QPushButton#PopupPrimaryButton {
                background-color: #4D8DFF;
                color: #F6FAFF;
                border: none;
                border-radius: 8px;
                padding: 9px 16px;
                min-height: 34px;
            }
            QMessageBox QPushButton#PopupPrimaryButton:hover {
                background-color: #63A1FF;
            }
            QMessageBox QPushButton#PopupPrimaryButton:pressed {
                background-color: #3F78DF;
            }
            """
            % (label_min_px, label_max_px)
        )
        buttons = list(box.findChildren(QPushButton) or [])
        for button in buttons:
            button.setCursor(Qt.PointingHandCursor)
            if primary_button is not None and button is primary_button:
                button.setObjectName("PopupPrimaryButton")
            else:
                button.setObjectName("PopupSecondaryButton")
            self._refresh_widget_style(button)

    def _ask_styled_yes_no(
        self,
        title,
        message,
        informative_text="",
        yes_text="Yes",
        no_text="No",
        default_yes=True,
    ):
        box = QMessageBox(self)
        box.setWindowTitle(str(title or "Confirm"))
        box.setIcon(QMessageBox.Question)
        box.setText(str(message or "").strip())
        info_text = str(informative_text or "").strip()
        if info_text:
            box.setInformativeText(info_text)
        yes_btn = box.addButton(str(yes_text or "Yes"), QMessageBox.YesRole)
        no_btn = box.addButton(str(no_text or "No"), QMessageBox.NoRole)
        box.setDefaultButton(yes_btn if default_yes else no_btn)
        self._style_message_box(box, primary_button=yes_btn if default_yes else None)
        box.exec()
        return box.clickedButton() == yes_btn

    def _prompt_artist_normalization_review_entries(self, review_entries, context_label="export"):
        resolutions = {}
        entries = [entry for entry in list(review_entries or []) if isinstance(entry, dict)]
        total = len(entries)
        for idx, entry in enumerate(entries, 1):
            raw_artist = str(entry.get("raw_artist") or "").strip()
            candidate_artist = str(entry.get("candidate_artist") or "").strip()
            if not raw_artist or not candidate_artist:
                continue

            try:
                track_count = max(1, int(entry.get("track_count", 1) or 1))
            except Exception:
                track_count = 1
            try:
                score = float(entry.get("score", 0.0) or 0.0)
            except Exception:
                score = 0.0
            reason = str(entry.get("reason") or "").strip().replace("_", " ")
            evidence = [str(item).strip() for item in list(entry.get("evidence") or []) if str(item).strip()]

            prompt = QMessageBox(self)
            prompt.setWindowTitle("Smart Artist Review")
            prompt.setIcon(QMessageBox.Question)
            prompt.setText(
                f"Ambiguous artist folder match ({idx}/{total})\n\n"
                f"Keep Separate: \"{raw_artist}\"\n"
                f"Merge Into: \"{candidate_artist}\""
            )

            detail_lines = [
                f"Preparing {str(context_label or 'export').strip()}.",
                f"Tracks affected: {track_count}",
                f"Match score: {score:.3f}",
            ]
            if reason:
                detail_lines.append(f"Reason: {reason}")
            if evidence:
                detail_lines.append(f"Evidence: {', '.join(evidence[:3])}")
            prompt.setInformativeText("\n".join(detail_lines))

            merge_label = f"Merge to {candidate_artist}"
            if len(merge_label) > 34:
                merge_label = "Merge to Suggested Folder"
            merge_btn = prompt.addButton(merge_label, QMessageBox.AcceptRole)
            keep_btn = prompt.addButton("Keep Separate", QMessageBox.RejectRole)
            prompt.setDefaultButton(keep_btn)
            self._style_message_box(prompt, primary_button=keep_btn, label_min_width=460, label_max_width=820)
            prompt.exec()

            action = "merge" if prompt.clickedButton() == merge_btn else "keep"
            resolutions[raw_artist] = {
                "action": action,
                "canonical_artist": candidate_artist if action == "merge" else raw_artist,
            }

        return resolutions

    def _handle_artist_normalization_review_request(self, worker, request_id, review_entries):
        resolutions = {}
        try:
            resolutions = self._prompt_artist_normalization_review_entries(
                review_entries,
                context_label="file export",
            )
        except Exception:
            resolutions = {}
        try:
            if worker is not None and hasattr(worker, "submit_artist_normalization_review"):
                worker.submit_artist_normalization_review(request_id, resolutions)
        except Exception:
            pass

    def _maybe_review_preview_artist_normalization(self, cache_path):
        cache_path = str(cache_path or "").strip()
        if not cache_path or not os.path.exists(cache_path):
            return

        _lazy_import_backend("mixsplitr_artist_normalization")
        if mixsplitr_artist_normalization is None:
            return
        if not (
            hasattr(mixsplitr_artist_normalization, "apply_smart_folder_canonicalization")
            and hasattr(mixsplitr_artist_normalization, "collect_pending_review_entries")
            and hasattr(mixsplitr_artist_normalization, "apply_review_resolutions")
        ):
            return

        _lazy_import_backend("mixsplitr_editor")
        cache_data = None
        if mixsplitr_editor is not None and hasattr(mixsplitr_editor, "load_preview_cache"):
            try:
                cache_data = mixsplitr_editor.load_preview_cache(cache_path)
            except Exception:
                cache_data = None
        if cache_data is None:
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    cache_data = json.load(handle)
            except Exception:
                cache_data = None
        if not isinstance(cache_data, dict):
            return

        tracks = list(cache_data.get("tracks") or [])
        if not tracks:
            return

        runtime_config = self._read_config_from_disk()
        debug_callback = self._append_debug_readout_line if self._debug_readout_enabled_from_ui() else None
        try:
            mixsplitr_artist_normalization.apply_smart_folder_canonicalization(
                tracks,
                runtime_config=runtime_config,
                debug_callback=debug_callback,
            )
        except Exception:
            return

        review_entries = mixsplitr_artist_normalization.collect_pending_review_entries(tracks)
        if not review_entries:
            return

        resolutions = self._prompt_artist_normalization_review_entries(
            review_entries,
            context_label="preview export",
        )
        try:
            mixsplitr_artist_normalization.apply_review_resolutions(
                tracks,
                resolutions,
                persist=True,
                debug_callback=debug_callback,
            )
        except Exception:
            return

        try:
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump(cache_data, handle)
        except Exception:
            pass

    def _estimate_full_preview_storage(self, target_files):
        source_bytes = 0
        estimated_low_bytes = 0.0
        estimated_high_bytes = 0.0
        file_count = 0
        ambiguous_count = 0

        for raw_path in list(target_files or []):
            path = os.path.abspath(str(raw_path or "").strip())
            if not path or not os.path.isfile(path):
                continue
            try:
                size_bytes = int(os.path.getsize(path))
            except Exception:
                size_bytes = 0
            if size_bytes <= 0:
                continue

            ext = os.path.splitext(path)[1].strip().lower()
            if ext in (".wav", ".aif", ".aiff"):
                low_mult, high_mult = 0.35, 0.75
            elif ext in (".flac", ".alac", ".ape", ".wv", ".tta", ".tak"):
                low_mult, high_mult = 0.90, 1.15
            elif ext in (".mp3", ".aac", ".ogg", ".oga", ".opus", ".wma"):
                low_mult, high_mult = 2.00, 5.50
            elif ext in (".m4a", ".mp4", ".m4b", ".m4p", ".caf", ".mov"):
                low_mult, high_mult = 0.80, 4.50
                ambiguous_count += 1
            else:
                low_mult, high_mult = 0.90, 3.00
                ambiguous_count += 1

            file_count += 1
            source_bytes += size_bytes
            estimated_low_bytes += float(size_bytes) * low_mult
            estimated_high_bytes += float(size_bytes) * high_mult

        return {
            "file_count": int(file_count),
            "source_bytes": int(max(0, source_bytes)),
            "estimated_low_bytes": int(max(0, round(estimated_low_bytes))),
            "estimated_high_bytes": int(max(0, round(estimated_high_bytes))),
            "ambiguous_count": int(ambiguous_count),
        }

    def _get_full_preview_estimate_details(self, target_files):
        estimate = self._estimate_full_preview_storage(target_files)
        file_count = int(estimate.get("file_count", 0) or 0)
        source_bytes = int(estimate.get("source_bytes", 0) or 0)
        low_bytes = int(estimate.get("estimated_low_bytes", 0) or 0)
        high_bytes = int(estimate.get("estimated_high_bytes", 0) or 0)
        ambiguous_count = int(estimate.get("ambiguous_count", 0) or 0)

        if file_count <= 0 or source_bytes <= 0 or high_bytes <= 0:
            return None

        source_text = self._format_storage_bytes(source_bytes)
        low_text = self._format_storage_bytes(low_bytes)
        high_text = self._format_storage_bytes(high_bytes)
        range_span = max(0, high_bytes - low_bytes)
        if range_span <= max(32 * 1024 * 1024, int(high_bytes * 0.12)):
            approx_text = self._format_storage_bytes(int(round((low_bytes + high_bytes) / 2.0)))
            estimate_text = f"about {approx_text}"
        else:
            estimate_text = f"{low_text} to {high_text}"

        return {
            "file_count": file_count,
            "source_text": source_text,
            "estimate_text": estimate_text,
            "ambiguous_count": ambiguous_count,
        }

    def _build_full_preview_estimate_text(self, target_files):
        details = self._get_full_preview_estimate_details(target_files)
        if not details:
            return ""

        lines = [
            '<div style="margin-top: 6px;">',
            '<table cellspacing="0" cellpadding="0" style="margin: 0;">',
            (
                '<tr>'
                '<td style="padding: 0 12px 7px 0; vertical-align: top;">Current selection:</td>'
                f'<td style="padding: 0 0 7px 0; vertical-align: top;">'
                f'<span style="color: #F4F0EA; font-weight: 700;">{html.escape(details["source_text"])}</span>'
                ' across '
                f'<span style="color: #F4F0EA; font-weight: 700;">{details["file_count"]}</span> file(s).</td>'
                '</tr>'
            ),
            (
                '<tr>'
                '<td style="padding: 0 12px 7px 0; vertical-align: top;">Full Preview:</td>'
                f'<td style="padding: 0 0 7px 0; vertical-align: top;">'
                f'<span style="color: #FFFFFF; font-weight: 700; font-size: 15px;">{html.escape(details["estimate_text"])}</span>'
                '</td>'
                '</tr>'
            ),
            (
                '<tr>'
                '<td style="padding: 0 12px 0 0; vertical-align: top;">Light Preview:</td>'
                '<td style="padding: 0; vertical-align: top;">'
                '<span style="color: #F4F0EA; font-weight: 700;">minimal</span> extra temp audio'
                '</td>'
                '</tr>'
            ),
            '</table>',
        ]
        if int(details.get("ambiguous_count", 0) or 0) > 0:
            lines.append(
                '<div style="margin-top: 8px;">'
                'Estimate varies by source format; lossy files like MP3/AAC usually expand more '
                'because preview chunks are cached as FLAC.'
                '</div>'
            )
        lines.append('</div>')
        return "".join(lines)

    def _prompt_processing_flow(self, selected_workflow, target_files=None):
        workflow_key = self._normalize_splitter_workflow_value(selected_workflow)
        split_mode = self._selected_split_mode()

        # ── Timestamp workflows always route through the editor so start
        #    times can be reviewed before export. ──
        if self._splitter_workflow_is_timestamp(workflow_key):
            return {
                "preview_mode": True,
                "light_preview": False,
                "open_editor": True,
                "split_mode": split_mode,
            }

        # ── Non-timestamp workflows (Split Only, Split+Identify, etc.)
        #    always route through the editor so the user can review /
        #    edit results before exporting. Ask whether to build a
        #    light or full preview cache before processing. ──
        light_preview = False
        preview_box = QMessageBox(self)
        preview_box.setWindowTitle("Preview Type")
        preview_box.setIcon(QMessageBox.NoIcon)
        preview_box.setText("Choose preview type")
        info_lines = [
            "Light Preview: opens editor faster and re-splits audio during export.",
            "Full Preview: caches split chunks now for the fastest export.",
        ]
        estimate_html = self._build_full_preview_estimate_text(target_files)
        if estimate_html:
            info_lines.extend(["", estimate_html])
        preview_box.setInformativeText("<br>".join(info_lines))
        full_btn = preview_box.addButton("Full Preview", QMessageBox.AcceptRole)
        light_btn = preview_box.addButton("Light Preview", QMessageBox.ActionRole)
        preview_cancel_btn = preview_box.addButton("Cancel", QMessageBox.RejectRole)
        preview_box.setDefaultButton(full_btn)
        self._style_message_box(
            preview_box,
            primary_button=full_btn,
            label_min_width=320,
            label_max_width=560,
        )
        light_btn.setToolTip("Faster preview setup. Audio is split again at export time.")
        full_btn.setToolTip("Slower preview setup. Split chunks are cached for faster export.")
        for label in preview_box.findChildren(QLabel):
            try:
                label.setWordWrap(True)
            except Exception:
                pass
            try:
                label.setTextInteractionFlags(Qt.NoTextInteraction)
            except Exception:
                pass
            try:
                label.setFocusPolicy(Qt.NoFocus)
            except Exception:
                pass
            try:
                label.setCursor(Qt.ArrowCursor)
            except Exception:
                pass
            try:
                text_value = str(label.text() or "")
                if "<" in text_value and ">" in text_value:
                    label.setTextFormat(Qt.RichText)
                else:
                    label.setTextFormat(Qt.PlainText)
            except Exception:
                pass
        preview_box.adjustSize()
        preview_box.exec()
        preview_clicked = preview_box.clickedButton()
        if preview_clicked == preview_cancel_btn:
            return None
        light_preview = preview_clicked == light_btn

        return {
            "preview_mode": True,
            "light_preview": bool(light_preview),
            "open_editor": True,
            "split_mode": split_mode,
        }

    def _format_elapsed_label(self, total_seconds):
        total_seconds = max(0, int(total_seconds))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _update_busy_ui(self):
        if getattr(self, "_run_button_state", "idle") == "idle":
            return
        if not self.busy_label:
            return
        elapsed_text = "00:00"
        if self.busy_elapsed.isValid():
            elapsed_text = self._format_elapsed_label(self.busy_elapsed.elapsed() / 1000.0)
        self.run_btn.setText(f"{self.busy_label} {elapsed_text}")

    def _run_button_cell_style(self, busy=False, cancel=False, accent="green"):
        scale = self._font_scale() if hasattr(self, "_font_scale") else 1.0
        btn_font = max(11, int(round(14 * scale)))
        btn_pad_v = max(7, int(round(9 * scale)))
        btn_pad_h = max(12, int(round(18 * scale)))
        accent_key = str(accent or "green").strip().lower()
        accent_palette = {
            "green": {
                "bg": "#1DB954",
                "hover": "#1ED760",
                "pressed": "#18A64C",
                "fg": "#131314",
                "border": "none",
            },
            "red": {
                "bg": "#B84D5D",
                "hover": "#C25A6A",
                "pressed": "#A74656",
                "fg": "#F6F3EE",
                "border": "none",
            },
        }
        palette = accent_palette.get(accent_key, accent_palette["green"])
        if cancel:
            bg = "#B84D5D"
            hover_bg = "#C25A6A"
            pressed_bg = "#A74656"
            fg = "#F6F3EE"
            border = "none"
        elif busy:
            bg = "#2A2F36"
            hover_bg = "#2A2F36"
            pressed_bg = "#2A2F36"
            fg = "#9C978D"
            border = "none"
        else:
            bg = palette["bg"]
            hover_bg = palette["hover"]
            pressed_bg = palette["pressed"]
            fg = palette["fg"]
            border = palette["border"]
        return (
            "QPushButton {"
            f"background-color: {bg};"
            f"color: {fg};"
            f"border: {border};"
            "border-radius: 8px;"
            f"padding: {btn_pad_v}px {btn_pad_h}px;"
            f"font-size: {btn_font}px;"
            "font-weight: 500;"
            "}"
            "QPushButton:hover {"
            f"background-color: {hover_bg};"
            "}"
            "QPushButton:pressed {"
            f"background-color: {pressed_bg};"
            "}"
        )

    def _set_rounded_action_button_style(self, button, busy=False, accent="green"):
        if button is None:
            return
        is_busy = bool(busy)
        button.setProperty("busyState", is_busy)
        button.setStyleSheet(self._run_button_cell_style(busy=is_busy, accent=accent))
        self._refresh_widget_style(button)

    def _set_cd_rip_start_button_style(self, busy=False):
        button = getattr(self, "cd_rip_start_btn", None)
        if button is None:
            return
        self._set_rounded_action_button_style(button, busy=busy, accent="red")

    def _set_run_button_busy(self, busy, label="Processing...", allow_cancel=False):
        if busy:
            self._run_button_state = "processing" if allow_cancel else "busy"
            self.busy_label = str(label or "Processing...")
            self.busy_elapsed.start()
            if not self.busy_timer.isActive():
                self.busy_timer.start()
            self.run_btn.setEnabled(bool(allow_cancel))
            self.run_btn.setText(f"{self.busy_label} 00:00")
            self.run_btn.setProperty("busyState", True)
            self.run_btn.setProperty("cancelState", bool(allow_cancel))
            self.run_btn.setStyleSheet(
                self._run_button_cell_style(busy=True, cancel=bool(allow_cancel))
            )
            self.progress_bar.setRange(0, 0)
        else:
            self._run_button_state = "idle"
            self.busy_label = ""
            self.busy_timer.stop()
            self.run_btn.setEnabled(True)
            self.run_btn.setText("Start Processing")
            self.run_btn.setProperty("busyState", False)
            self.run_btn.setProperty("cancelState", False)
            self.run_btn.setStyleSheet(self._run_button_cell_style(busy=False))
            self.progress_bar.setRange(0, 100)

    def _set_ident_run_button_busy(self, busy):
        if not hasattr(self, "ident_run_btn"):
            return
        if busy:
            self.ident_run_btn.setEnabled(False)
            if isinstance(self.pending_splitter_identifier_workflow, dict):
                self.ident_run_btn.setText("Processing...")
            else:
                self.ident_run_btn.setText("Identifying...")
            self.ident_run_btn.setProperty("busyState", True)
            self.ident_run_btn.setStyleSheet(self._run_button_cell_style(busy=True))
            if hasattr(self, "ident_progress_bar"):
                self.ident_progress_bar.setRange(0, 0)
        else:
            self.ident_run_btn.setEnabled(True)
            self.ident_run_btn.setText(self._identifier_action_button_text())
            self.ident_run_btn.setProperty("busyState", False)
            self.ident_run_btn.setStyleSheet(self._run_button_cell_style(busy=False))
            if hasattr(self, "ident_progress_bar"):
                self.ident_progress_bar.setRange(0, 100)

    def _start_preview_export(self, cache_path, temp_folder):
        cache_path = str(cache_path or "")
        temp_folder = str(temp_folder or "")
        if not cache_path or not os.path.exists(cache_path):
            self.status_label.setText("Status: Preview cache missing; export canceled")
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return
        if self.preview_export_thread and self.preview_export_thread.isRunning():
            self.status_label.setText("Status: Preview export is already running")
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return

        self._maybe_review_preview_artist_normalization(cache_path)
        self.pending_preview_cache_path = cache_path
        self.pending_preview_temp_folder = temp_folder
        self.preview_export_thread = PreviewExportThread(
            cache_path,
            temp_folder,
            output_format=self._selected_output_format(),
            pipeline_getter=lambda: mixsplitr_pipeline,
            lazy_import_backend=_lazy_import_backend,
        )
        self.preview_export_thread.status_update.connect(lambda text: self.status_label.setText(f"Status: {text}"))
        self.preview_export_thread.finished_update.connect(self._on_preview_export_finished)
        self._set_run_button_busy(True, "Exporting...")
        self.preview_export_thread.start()
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def _on_preview_export_finished(self, success, message):
        self.status_label.setText(f"Status: {message}")
        cache_path = self.pending_preview_cache_path
        temp_folder = self.pending_preview_temp_folder
        if success:
            self._clear_unsaved_preview_artifacts(cache_path=cache_path, temp_dir=temp_folder)
            self._refresh_session_history_list()

        export_thread = self.preview_export_thread
        self.preview_export_thread = None
        if export_thread is not None:
            try:
                export_thread.deleteLater()
            except Exception:
                pass

        self.pending_preview_cache_path = ""
        self.pending_preview_temp_folder = ""
        self.pending_preview_open_editor = False
        self._set_run_button_busy(False)
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def _start_timestamp_preview_export(self, cache_path, temp_folder):
        cache_path = str(cache_path or "")
        temp_folder = str(temp_folder or "")
        if not cache_path or not os.path.exists(cache_path):
            self.status_label.setText("Status: Timestamp preview cache missing; export canceled")
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return
        if mixsplitr_autotracklist is None:
            _lazy_import_backend("mixsplitr_autotracklist")
        if mixsplitr_autotracklist is None or not hasattr(mixsplitr_autotracklist, "export_timestamp_editor_cache"):
            self.status_label.setText("Status: Timestamp export is unavailable")
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return

        self.pending_preview_cache_path = cache_path
        self.pending_preview_temp_folder = temp_folder
        self._set_run_button_busy(True, "Exporting...")
        self.status_label.setText("Status: Exporting timestamp tracklist...")
        QApplication.processEvents()

        try:
            export_result = mixsplitr_autotracklist.export_timestamp_editor_cache(cache_path) or {}
            output_files = list(export_result.get("output_files") or [])
            session_count = int(export_result.get("session_count", 0) or 0)
            segment_count = int(export_result.get("segment_count", 0) or 0)
            message = (
                f"Exported {session_count} timestamp tracklist file(s) with {segment_count} segment(s)."
            )
            if output_files:
                message += f" Wrote {len(output_files)} file(s)."
            self.status_label.setText(f"Status: {message}")
            self._clear_unsaved_preview_artifacts(cache_path=cache_path, temp_dir=temp_folder)
            self._refresh_session_history_list()
            self.pending_preview_cache_path = ""
            self.pending_preview_temp_folder = ""
            self.pending_preview_open_editor = False
            if self.stacked_widget.currentIndex() == self.track_editor_page_index:
                self.switch_page(0)
        except Exception as exc:
            self.status_label.setText(f"Status: Timestamp export failed: {exc}")
        finally:
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def _start_preview_editor(self, cache_data, cache_path, temp_folder):
        self.pending_preview_cache_path = str(cache_path or "")
        self.pending_preview_temp_folder = str(temp_folder or "")
        panel = getattr(self, "track_editor_panel", None)
        if panel is None:
            self._on_preview_editor_finished("error", error_message="Track Editor tab is unavailable")
            return
        if panel.has_unsaved_changes():
            proceed = QMessageBox.question(
                self,
                "Replace Track Editor Session",
                "Opening this preview session will replace unsaved Track Editor edits. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                self._on_preview_editor_finished("quit")
                return
        try:
            panel.load_preview_session(cache_data or {}, cache_path, temp_folder=temp_folder)
            if hasattr(self, "track_editor_tab_status_label"):
                if getattr(panel, "editor_mode", "") == "timestamp_preview":
                    self.track_editor_tab_status_label.setText(
                        f"Loaded timestamp review: {len(panel.tracks)} segment(s)"
                    )
                else:
                    self.track_editor_tab_status_label.setText(
                        f"Loaded preview session: {len(panel.tracks)} track(s)"
                    )
            self.switch_page(getattr(self, "track_editor_page_index", 0))
            self.status_label.setText(
                "Status: Timestamp review loaded in Track Editor tab"
                if getattr(panel, "editor_mode", "") == "timestamp_preview"
                else "Status: Preview loaded in Track Editor tab"
            )
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
        except Exception as exc:
            self._on_preview_editor_finished("error", error_message=str(exc))

    def _on_preview_editor_finished(self, editor_result, error_message=""):
        editor_result = str(editor_result or "").strip().lower()

        if editor_result == "error":
            self.status_label.setText(f"Status: Track Editor failed: {error_message or 'unknown error'}")
            self.pending_preview_open_editor = False
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return

        if editor_result == "apply":
            self.status_label.setText("Status: Track Editor complete; exporting preview...")
            self._start_preview_export(self.pending_preview_cache_path, self.pending_preview_temp_folder)
            return

        if editor_result == "done":
            self.status_label.setText("Status: Preview saved without export")
        else:
            self.status_label.setText("Status: Preview closed without export")
        self.pending_preview_open_editor = False
        self.pending_preview_cache_path = ""
        self.pending_preview_temp_folder = ""
        self._set_run_button_busy(False)
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def _on_timestamp_preview_editor_finished(self, editor_result, error_message=""):
        editor_result = str(editor_result or "").strip().lower()

        if editor_result == "error":
            self.status_label.setText(f"Status: Timestamp editor failed: {error_message or 'unknown error'}")
            self.pending_preview_open_editor = False
            self._set_run_button_busy(False)
            self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())
            return

        if editor_result == "export_timestamps":
            self._start_timestamp_preview_export(
                self.pending_preview_cache_path,
                self.pending_preview_temp_folder,
            )
            return

        if editor_result == "done":
            self.status_label.setText("Status: Timestamp review saved without export")
        else:
            self.status_label.setText("Status: Timestamp review closed without export")
        self.pending_preview_open_editor = False
        self.pending_preview_cache_path = ""
        self.pending_preview_temp_folder = ""
        self._set_run_button_busy(False)
        self._update_clear_preview_visibility(self._preview_mode_enabled_from_ui())

    def _handle_preview_completion(self, preview_thread=None):
        if preview_thread is None:
            preview_thread = getattr(self, "thread", None)
        cache_path = str(getattr(preview_thread, "preview_cache_path", "") or "")
        temp_folder = str(getattr(preview_thread, "preview_temp_folder", "") or "")
        cache_data = getattr(preview_thread, "preview_cache_data", None)
        if not cache_path or not os.path.exists(cache_path):
            self.status_label.setText("Status: Preview ready but cache file is missing")
            return
        if cache_data is None and mixsplitr_editor and hasattr(mixsplitr_editor, "load_preview_cache"):
            try:
                cache_data = mixsplitr_editor.load_preview_cache(cache_path)
            except Exception:
                cache_data = None

        is_timestamp_preview = bool(
            isinstance(cache_data, dict)
            and (
                (cache_data.get("config_snapshot") or {}).get("timestamp_editor_mode")
                or cache_data.get("timestamp_sessions")
            )
        )
        should_apply = not bool(getattr(self, "pending_preview_open_editor", False))
        if should_apply:
            if is_timestamp_preview:
                self._start_timestamp_preview_export(cache_path, temp_folder)
            else:
                self._start_preview_export(cache_path, temp_folder)
            return

        self._start_preview_editor(cache_data, cache_path, temp_folder)

    def _debug_readout_enabled_from_ui(self):
        return bool(hasattr(self, "debug_readout_check") and self.debug_readout_check.isChecked())

    def _developer_inspector_enabled_from_ui(self):
        return bool(
            hasattr(self, "developer_inspector_check")
            and self.developer_inspector_check.isChecked()
        )

    def _emit_debug_stream_line(self, message, stream_name="stdout"):
        line = str(message or "").strip()
        if not line:
            return
        stream = str(stream_name or "stdout").strip().lower()
        if stream == "stderr":
            line = f"[stderr] {line}"
        try:
            self.debug_stream_line.emit(line)
        except Exception:
            pass

    def _set_global_debug_capture_enabled(self, enabled):
        target = bool(enabled)
        if target:
            if len(self._debug_readout_lines) < len(_BOOTSTRAP_DEBUG_LINES):
                self._debug_readout_lines = list(_BOOTSTRAP_DEBUG_LINES)
                if self.debug_readout_dialog is not None:
                    self.debug_readout_dialog.load_lines(self._debug_readout_lines)
            _set_bootstrap_debug_capture_enabled(True)
            _set_bootstrap_debug_sink(self._emit_debug_stream_line)
            self._debug_capture_enabled = True
            return

        _set_bootstrap_debug_capture_enabled(False)
        _set_bootstrap_debug_sink(None)
        self._debug_capture_enabled = False

    def _ensure_debug_readout_dialog(self):
        if self.debug_readout_dialog is None:
            self.debug_readout_dialog = DebugReadoutDialog(self)
            self.debug_readout_dialog.load_lines(self._debug_readout_lines)
            self.debug_readout_dialog.set_inspector_payload(self._developer_inspector_payload)
            self.debug_readout_dialog.unlock_requested.connect(self._unlock_developer_inspector)
            if self.developer_inspector_controller is not None:
                self.developer_inspector_controller.set_ignored_root(self.debug_readout_dialog)
        return self.debug_readout_dialog

    def _on_developer_inspector_payload_changed(self, payload):
        self._developer_inspector_payload = dict(payload or {})
        if self.debug_readout_dialog is not None:
            self.debug_readout_dialog.set_inspector_payload(self._developer_inspector_payload)

    def _unlock_developer_inspector(self):
        if self.developer_inspector_controller is not None:
            self.developer_inspector_controller.unlock()

    def _append_debug_readout_line(self, text):
        message = str(text or "").rstrip()
        if not message:
            return
        max_chars = int(getattr(self, "_debug_readout_line_max_chars", 1600) or 1600)
        if len(message) > max_chars:
            message = message[: max_chars - 1] + "…"
        self._debug_readout_lines.append(message)
        max_lines = int(getattr(self, "_debug_readout_max_lines", 8000) or 8000)
        if len(self._debug_readout_lines) > max_lines:
            overflow = len(self._debug_readout_lines) - max_lines
            if overflow > 0:
                del self._debug_readout_lines[:overflow]
        if self.debug_readout_dialog is not None:
            self.debug_readout_dialog.append_line(message)

    def _open_debug_readout_window(self):
        dialog = self._ensure_debug_readout_dialog()
        dialog.set_inspector_payload(self._developer_inspector_payload)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _prepare_debug_readout_for_run(self, run_label):
        if not self._debug_readout_enabled_from_ui():
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append_debug_readout_line(f"=== {run_label} started at {stamp} ===")

    def _on_debug_readout_toggled(self, checked):
        self._set_global_debug_capture_enabled(bool(checked))
        if (
            (not checked)
            and (not self._developer_inspector_enabled_from_ui())
            and self.debug_readout_dialog is not None
        ):
            self.debug_readout_dialog.hide()

    def _on_developer_inspector_toggled(self, checked):
        enabled = bool(checked)
        if self.developer_inspector_controller is not None:
            self.developer_inspector_controller.set_enabled(enabled)
        if enabled:
            self._open_debug_readout_window()
            return
        if (not self._debug_readout_enabled_from_ui()) and self.debug_readout_dialog is not None:
            self.debug_readout_dialog.hide()

    def _on_run_button_clicked(self):
        if self.thread and self.thread.isRunning():
            self.cancel_processing(force=True)
            return
        self.start_processing()

    def cancel_processing(self, force=True):
        worker = self.thread
        if worker is None or not worker.isRunning():
            self.status_label.setText("Status: No active processing run")
            return False

        self._processing_cancel_requested = True
        self.status_label.setText("Status: Cancelling processing…")
        self.stage_label.setText("Stopping active workers…")
        self.stage_label.setVisible(True)
        if self._debug_readout_enabled_from_ui():
            self._append_debug_readout_line("[processing/cancel] Cancellation requested by user")

        try:
            if hasattr(worker, "request_cancel"):
                worker.request_cancel()
            else:
                worker.requestInterruption()
        except Exception:
            pass

        # Disable further clicks while cancellation is being resolved.
        self._set_run_button_busy(True, "Cancelling...", allow_cancel=False)

        if force:
            QTimer.singleShot(1800, lambda _w=worker: self._force_terminate_processing_thread(_w))
        return True

    def _force_terminate_processing_thread(self, worker):
        if worker is None or worker is not self.thread:
            return
        if not worker.isRunning():
            return

        if self._debug_readout_enabled_from_ui():
            self._append_debug_readout_line("[processing/cancel] Force-terminating worker thread")
        try:
            worker.terminate()
        except Exception:
            return
        try:
            worker.wait(700)
        except Exception:
            pass

    def _on_processing_thread_stopped(self, worker):
        if worker is None:
            return

        is_current = worker is self.thread
        if not is_current:
            try:
                worker.deleteLater()
            except Exception:
                pass
            return

        # finished_update can arrive just before or after QThread.finished.
        # Defer fallback cancellation handling briefly to avoid false negatives.
        if not self._processing_result_handled:
            QTimer.singleShot(150, lambda _w=worker: self._finalize_processing_thread_without_result(_w))
            return

        self.thread = None
        self._processing_result_handled = False
        self._processing_cancel_requested = False
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _finalize_processing_thread_without_result(self, worker):
        if worker is None or worker is not self.thread:
            return
        if self._processing_result_handled:
            self.thread = None
            self._processing_result_handled = False
            self._processing_cancel_requested = False
            try:
                worker.deleteLater()
            except Exception:
                pass
            return
        self.status_label.setText("Status: Processing cancelled")
        self.stage_label.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_run_button_busy(False)
        if self._debug_readout_enabled_from_ui():
            self._append_debug_readout_line("[processing/cancel] Worker stopped without completion payload")
        self.thread = None
        self._processing_result_handled = False
        self._processing_cancel_requested = False
        try:
            worker.deleteLater()
        except Exception:
            pass

    def start_processing(self):
        # Keep runtime behavior aligned with CLI settings by persisting UI settings first.
        self._save_settings_to_config_internal(refresh_lists=False)

        if self.thread and self.thread.isRunning():
            self.cancel_processing(force=True)
            return

        if self.ident_thread and self.ident_thread.isRunning():
            self.status_label.setText("Status: Wait for Identification Mode to finish")
            return

        if self.preview_export_thread and self.preview_export_thread.isRunning():
            self.status_label.setText("Status: Wait for preview export to finish")
            return

        files_to_process = self.drop_zone.file_paths
        
        if not files_to_process:
            self.status_label.setText("Status: Error - No files selected")
            return

        selected_workflow = self._selected_splitter_workflow()
        dest_folder = self.output_dir_input.text()
        flow = self._prompt_processing_flow(
            selected_workflow,
            target_files=files_to_process,
        )
        if flow is None:
            self.status_label.setText("Status: Processing cancelled")
            return
        self.pending_preview_open_editor = bool(flow.get("open_editor", False))
        self.pending_preview_cache_path = ""
        self.pending_preview_temp_folder = ""
        selected_split_mode = str(flow.get("split_mode", "silence") or "silence")
        long_track_mode_overrides = {}
        long_track_mix_strategy_overrides = {}
        if not self._splitter_workflow_is_timestamp(selected_workflow):
            duration_min_map = self._collect_duration_minutes_for_paths(files_to_process)
            long_track_override_result = self._collect_long_track_prompt_overrides(
                files_to_process,
                duration_min_map,
                selected_workflow=selected_workflow,
            )
            if long_track_override_result is None:
                self.status_label.setText("Status: Processing cancelled")
                return
            (
                long_track_mode_overrides,
                long_track_mix_strategy_overrides,
            ) = long_track_override_result

        mix_strategy_target_files = []
        if (
            not self._splitter_workflow_is_timestamp(selected_workflow)
            and selected_split_mode in ("silence", "assisted")
        ):
            for path in list(files_to_process or []):
                normalized = os.path.abspath(str(path or "").strip())
                if not normalized:
                    continue
                if str(long_track_mode_overrides.get(normalized, "")).strip().lower() == "single":
                    continue
                mix_strategy_target_files.append(normalized)

        if mix_strategy_target_files:
            mix_strategy_choice = self._prompt_splitter_mix_strategy_choice(
                mix_strategy_target_files,
                selected_split_mode,
            )
            if mix_strategy_choice == "cancel":
                self.status_label.setText("Status: Processing cancelled")
                return
            if mix_strategy_choice == "transition":
                long_track_mix_strategy_overrides = {
                    path: "transition" for path in mix_strategy_target_files
                }
            else:
                long_track_mix_strategy_overrides = {}

        proceed, split_points_map = self._collect_embedded_waveform_points(
            files_to_process,
            selected_split_mode,
            selected_workflow=selected_workflow,
            long_track_mode_overrides=long_track_mode_overrides,
            long_track_mix_strategy_overrides=long_track_mix_strategy_overrides,
        )
        if not proceed:
            self.status_label.setText("Status: Processing cancelled")
            return
        
        advanced_settings = {
            'window_size': self.window_spin.value(),
            'step_size': self.step_spin.value(),
            'min_confidence': self.conf_spin.value(),
            'essentia_min_confidence': self.essentia_conf.value(),
            'output_format': self._selected_output_format(),
            'split_sensitivity_db': self._split_sensitivity_offset(),
            'split_silence_seek_step_ms': self._normalize_split_seek_step_value(self.split_seek_step_combo.currentData()),
            'duplicate_policy': self._normalize_duplicate_policy_value(self.duplicate_policy_combo.currentData()),
            'long_track_mode_overrides': dict(long_track_mode_overrides or {}),
            'long_track_mix_strategy_overrides': dict(long_track_mix_strategy_overrides or {}),
        }
        if selected_workflow == self.SPLITTER_WORKFLOW_TIMESTAMP:
            # Read the no-ID labels setting from the checkbox — single
            # timestamping mode, labeling is controlled by the setting.
            advanced_settings["auto_tracklist_no_identify"] = bool(
                self.auto_no_identify_check.isChecked()
            )

        if self._splitter_workflow_requires_identifier(selected_workflow):
            self._queue_splitter_workflow_for_identifier(
                selected_workflow,
                files_to_process,
                dest_folder,
                flow,
                split_points_map,
                advanced_settings,
            )
            return

        if self.pending_splitter_identifier_workflow is not None:
            self.pending_splitter_identifier_workflow = None
            self._set_ident_run_button_busy(False)
            if hasattr(self, "ident_stage_label"):
                self.ident_stage_label.setVisible(False)
            if hasattr(self, "ident_progress_bar"):
                self.ident_progress_bar.setValue(0)
            if hasattr(self, "ident_status_label"):
                self.ident_status_label.setText("Status: Nothing queued from Splitter")
            if hasattr(self, "ident_drop_zone"):
                queued_files = list(getattr(self.ident_drop_zone, "file_paths", []) or [])
                if queued_files:
                    self.ident_drop_zone.label.setText(f"{len(queued_files)} file(s) ready to identify")
                else:
                    self.ident_drop_zone.label.setText(
                        "Drag & drop music files here or click to browse"
                    )

        self._processing_result_handled = False
        self._processing_cancel_requested = False
        self._set_run_button_busy(True, "Cancel Processing", allow_cancel=True)
        self.progress_bar.setValue(0)
        self._prepare_debug_readout_for_run("Audio Splitter")

        worker = ProcessingThread(
            files_to_process,
            self._splitter_processing_mode_text(selected_workflow),
            dest_folder,
            advanced_settings,
            preview_mode=bool(flow.get("preview_mode", False)),
            light_preview=bool(flow.get("light_preview", False)),
            split_mode=selected_split_mode,
            split_points_map=split_points_map,
            skip_mix_analysis=False,
        )
        self.thread = worker
        worker.progress_update.connect(self.update_progress)
        worker.status_update.connect(self.update_status)
        worker.stage_update.connect(self.update_stage)
        worker.debug_output.connect(self._append_debug_readout_line)
        worker.artist_normalization_review_requested.connect(
            lambda request_id, payload, _w=worker: self._handle_artist_normalization_review_request(
                _w,
                request_id,
                payload,
            )
        )
        worker.finished_update.connect(self.processing_finished)
        worker.finished.connect(lambda _w=worker: self._on_processing_thread_stopped(_w))
        worker.start()

    def start_identification_processing(self):
        # Keep runtime behavior aligned with CLI settings by persisting UI settings first.
        self._save_settings_to_config_internal(refresh_lists=False)

        if self.thread and self.thread.isRunning():
            self.ident_status_label.setText("Status: Wait for Audio Splitter processing to finish")
            return
        if self.ident_thread and self.ident_thread.isRunning():
            self.ident_status_label.setText("Status: Identification is already running")
            return
        if self.preview_export_thread and self.preview_export_thread.isRunning():
            self.ident_status_label.setText("Status: Wait for preview export to finish")
            return

        pending_workflow = self.pending_splitter_identifier_workflow
        files_to_process = self._normalize_audio_path_list(
            getattr(self.ident_drop_zone, "file_paths", []) or []
        )
        if isinstance(pending_workflow, dict):
            queued_files = self._normalize_audio_path_list(
                pending_workflow.get("files") or []
            )
            if files_to_process and queued_files and files_to_process != queued_files:
                self.pending_splitter_identifier_workflow = None
                pending_workflow = None
                self._set_ident_run_button_busy(False)
            elif not files_to_process and queued_files:
                files_to_process = list(queued_files)
                if hasattr(self, "ident_drop_zone"):
                    self.ident_drop_zone.file_paths = list(queued_files)
        if not files_to_process:
            self.ident_status_label.setText("Status: Error - No files selected")
            return
        if self._maybe_redirect_identifier_long_file(
            files_to_process,
            pending_workflow=pending_workflow,
        ):
            return

        selected_mode = self.ident_mode_dropdown.currentText()
        dest_folder = self.output_dir_input.text()
        advanced_settings = {
            "window_size": self.window_spin.value(),
            "step_size": self.step_spin.value(),
            "min_confidence": self.conf_spin.value(),
            "essentia_min_confidence": self.essentia_conf.value(),
            "output_format": self._selected_output_format(),
            "split_sensitivity_db": self._split_sensitivity_offset(),
            "split_silence_seek_step_ms": self._normalize_split_seek_step_value(self.split_seek_step_combo.currentData()),
            "duplicate_policy": self._normalize_duplicate_policy_value(self.duplicate_policy_combo.currentData()),
        }
        run_label = "Identifier Review"
        mode_text = selected_mode
        preview_mode = True
        light_preview = False
        split_mode = "silence"
        split_points_map = {}
        skip_mix_analysis = True

        if isinstance(pending_workflow, dict):
            workflow_key = self._normalize_splitter_workflow_value(pending_workflow.get("workflow"))
            files_to_process = self._normalize_audio_path_list(
                pending_workflow.get("files") or []
            )
            if not files_to_process:
                self.ident_status_label.setText("Status: Error - No files selected")
                return
            dest_folder = str(pending_workflow.get("dest_folder", "") or dest_folder)
            advanced_settings = dict(pending_workflow.get("advanced_settings") or advanced_settings)
            flow = dict(pending_workflow.get("flow") or {})
            split_points_map = dict(pending_workflow.get("split_points_map") or {})
            preview_mode = bool(flow.get("preview_mode", False))
            light_preview = bool(flow.get("light_preview", False))
            split_mode = str(flow.get("split_mode", "silence") or "silence")
            skip_mix_analysis = False
            if workflow_key == self.SPLITTER_WORKFLOW_TIMESTAMP:
                advanced_settings["auto_tracklist_no_identify"] = bool(
                    self.auto_no_identify_check.isChecked()
                )
                advanced_settings["auto_tracklist_identifier_mode"] = self._internal_mode_for_ui(
                    selected_mode
                )
                run_label = "Timestamping Workflow"
            else:
                run_label = "Split + Identify"
            mode_text = self._splitter_processing_mode_text(
                workflow_key,
                identifier_mode_text=selected_mode,
            )

        self.ident_stage_label.setVisible(False)
        self._set_ident_run_button_busy(True)
        self.ident_progress_bar.setValue(0)
        self._prepare_debug_readout_for_run(run_label)

        # Direct Identifier runs should always land in Track Editor for
        # review before export. Splitter-originated runs follow the
        # queued workflow's request.
        self.pending_preview_open_editor = True
        if isinstance(pending_workflow, dict):
            _ident_flow = dict(pending_workflow.get("flow") or {})
            self.pending_preview_open_editor = bool(_ident_flow.get("open_editor", False))

        self.ident_thread = ProcessingThread(
            files_to_process,
            mode_text,
            dest_folder,
            advanced_settings,
            preview_mode=preview_mode,
            light_preview=light_preview,
            split_mode=split_mode,
            split_points_map=split_points_map,
            skip_mix_analysis=skip_mix_analysis,
        )
        self.ident_thread.progress_update.connect(self._update_ident_progress)
        self.ident_thread.status_update.connect(self._update_ident_status)
        self.ident_thread.stage_update.connect(self._update_ident_stage)
        self.ident_thread.debug_output.connect(self._append_debug_readout_line)
        self.ident_thread.artist_normalization_review_requested.connect(
            lambda request_id, payload, _w=self.ident_thread: self._handle_artist_normalization_review_request(
                _w,
                request_id,
                payload,
            )
        )
        self.ident_thread.finished_update.connect(self._ident_processing_finished)
        self.ident_thread.start()

    def _update_ident_progress(self, value):
        if self.ident_progress_bar.minimum() == 0 and self.ident_progress_bar.maximum() == 0:
            self.ident_progress_bar.setRange(0, 100)
        self.ident_progress_bar.setValue(value)

    def _update_ident_status(self, text):
        self.ident_status_label.setText(f"Status: {text}")
        if self._debug_readout_enabled_from_ui():
            self._append_debug_readout_line(f"[ident/status] {text}")

    def _update_ident_stage(self, text):
        if text:
            self.ident_stage_label.setText(text)
            self.ident_stage_label.setVisible(True)
            if self._debug_readout_enabled_from_ui():
                self._append_debug_readout_line(f"[ident/stage] {text}")
        else:
            self.ident_stage_label.setVisible(False)

    def _ident_processing_finished(self, success, message):
        self.ident_status_label.setText(f"Status: {message}")
        self.ident_stage_label.setVisible(False)
        self.ident_progress_bar.setValue(100 if success else 0)
        if self._debug_readout_enabled_from_ui():
            outcome = "ok" if success else "error"
            self._append_debug_readout_line(f"[ident/{outcome}] {message}")
        thread = self.ident_thread
        _opened_editor = False
        if success and thread is not None and getattr(thread, "preview_mode", False) and getattr(thread, "preview_cache_path", ""):
            self._refresh_pending_identifier_workflow_from_preview(thread)
            self._handle_preview_completion(thread)
            _opened_editor = True
        if not _opened_editor:
            # Reset the editor flag when the editor was not opened (e.g.
            # processing failed or preview_mode was off) so it doesn't
            # leak into a subsequent run.
            self.pending_preview_open_editor = False
        # Preserve queued splitter workflows so the user can re-run
        # identification with a different backend on the same confirmed
        # split points without being sent back through long-file routing.
        _keep_workflow = False
        if isinstance(self.pending_splitter_identifier_workflow, dict):
            _wf_key = self._normalize_splitter_workflow_value(
                self.pending_splitter_identifier_workflow.get("workflow")
            )
            if _wf_key in (
                self.SPLITTER_WORKFLOW_SPLIT_AND_IDENTIFY,
                self.SPLITTER_WORKFLOW_TIMESTAMP,
            ):
                _keep_workflow = True
        if not _keep_workflow:
            self.pending_splitter_identifier_workflow = None
        self._set_ident_run_button_busy(False)
        self.ident_thread = None
        if thread is not None:
            try:
                thread.deleteLater()
            except Exception:
                pass

    def update_progress(self, value):
        if self.progress_bar.minimum() == 0 and self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(value)

    def update_status(self, text):
        self.status_label.setText(f"Status: {text}")
        if self._debug_readout_enabled_from_ui():
            self._append_debug_readout_line(f"[status] {text}")

    def update_stage(self, text):
        if text:
            self.stage_label.setText(text)
            self.stage_label.setVisible(True)
            if self._debug_readout_enabled_from_ui():
                self._append_debug_readout_line(f"[stage] {text}")
        else:
            self.stage_label.setVisible(False)

    def processing_finished(self, success, message):
        self._processing_result_handled = True
        worker = self.sender()
        if worker is None:
            worker = self.thread
        self.status_label.setText(f"Status: {message}")
        self.stage_label.setVisible(False)
        self.progress_bar.setValue(100 if success else 0)
        if self._debug_readout_enabled_from_ui():
            outcome = "ok" if success else "error"
            self._append_debug_readout_line(f"[processing/{outcome}] {message}")
        self._set_run_button_busy(False)
        if success and getattr(worker, "preview_mode", False) and getattr(worker, "preview_cache_path", ""):
            self._handle_preview_completion()

    def _try_start_system_window_move(self):
        handle = self.windowHandle()
        if handle is None:
            return False
        start_move = getattr(handle, "startSystemMove", None)
        if not callable(start_move):
            return False
        try:
            return bool(start_move())
        except Exception:
            return False

    def eventFilter(self, obj, event):
        if obj is getattr(self, "titlebar_content_spacer", None):
            t = event.type()
            if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if self._try_start_system_window_move():
                    event.accept()
                    return True
                self._manual_window_drag_active = True
                self.titlebar_content_spacer.setCursor(Qt.ClosedHandCursor)
                global_pos = event.globalPosition().toPoint()
                self._manual_window_drag_offset = global_pos - self.frameGeometry().topLeft()
                event.accept()
                return True
            if t == QEvent.MouseMove and self._manual_window_drag_active:
                if event.buttons() & Qt.LeftButton:
                    global_pos = event.globalPosition().toPoint()
                    self.move(global_pos - self._manual_window_drag_offset)
                    event.accept()
                    return True
                self._manual_window_drag_active = False
                self.titlebar_content_spacer.setCursor(Qt.OpenHandCursor)
            if t == QEvent.MouseButtonRelease:
                self._manual_window_drag_active = False
                self.titlebar_content_spacer.setCursor(Qt.OpenHandCursor)
                event.accept()
                return True

        if obj is getattr(self, "stacked_widget", None):
            if event.type() in (QEvent.Resize, QEvent.Show, QEvent.Move):
                if hasattr(self, "page_reveal_overlay"):
                    self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
                self._layout_titlebar_drag_strip()
                self._position_nav_highlight(animated=False)
                self._position_nav_connector(animated=False)
                self._sync_sidebar_blur_geometry()
                self._sync_content_blur_geometry()
        if obj is getattr(self, "sidebar", None):
            if event.type() in (QEvent.Resize, QEvent.Show):
                self._update_sidebar_branding_scale()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Dynamic font scaling
    # ------------------------------------------------------------------

    def _font_scale(self) -> float:
        """Scale factor driven by both width and height of the content area."""
        sidebar_w = self.sidebar.width() if hasattr(self, "sidebar") and self.sidebar is not None else 260
        content_w = max(400, self.width() - int(sidebar_w))
        content_h = max(460, self.height() - 40)
        scale_w = content_w / 840.0
        scale_h = content_h / 760.0   # allow shrinking on short windows too
        base_scale = min(scale_w, scale_h) * float(getattr(self, "_default_ui_density", 1.0))
        if sys.platform == "win32":
            min_scale = 0.58
        elif sys.platform == "darwin":
            min_scale = 0.62
        else:
            min_scale = 0.74
        return max(min_scale, min(1.08, base_scale))

    def _spatial_scale(self) -> float:
        """Conservative scale for margins/padding so layouts never collapse."""
        if sys.platform == "win32":
            min_spatial = 0.66
        elif sys.platform == "darwin":
            min_spatial = 0.70
        else:
            min_spatial = 0.82
        return max(min_spatial, min(1.06, self._font_scale()))

    def _scaled_px(self, base_px: int, scale: float, floor_ratio: float = 0.8, minimum: int = 0) -> int:
        base = max(0, int(base_px))
        scaled = int(round(base * float(scale)))
        floor_val = int(round(base * float(floor_ratio)))
        return max(int(minimum), scaled, floor_val)

    def _track_scalable(self, widget, base_px: int, extra_style: str = "") -> None:
        """Register a widget for font scaling and apply the initial size."""
        self._scalable_text.append((widget, base_px, extra_style))
        sz = max(10, int(round(base_px * self._font_scale())))
        widget.setStyleSheet(f"font-size: {sz}px; {extra_style}")

    def _track_layout_metrics(self, layout, base_margins=None, base_spacing=None):
        """Track a layout so margins/spacing scale with window size."""
        if layout is None:
            return
        if base_margins is None:
            m = layout.contentsMargins()
            base_margins = (int(m.left()), int(m.top()), int(m.right()), int(m.bottom()))
        if base_spacing is None:
            base_spacing = int(layout.spacing())
        spec = (layout, tuple(base_margins), int(base_spacing))
        self._scalable_layouts.append(spec)
        self._apply_layout_metric(spec, self._spatial_scale())

    def _apply_layout_metric(self, spec, scale: float):
        layout, base_margins, base_spacing = spec
        effective_scale = float(scale) * float(getattr(self, "_layout_density", 1.0))
        if sys.platform == "win32":
            margin_floor = 0.55
            spacing_floor = 0.48
        elif sys.platform == "darwin":
            margin_floor = 0.62
            spacing_floor = 0.56
        else:
            margin_floor = 0.8
            spacing_floor = 0.75
        left, top, right, bottom = base_margins
        left_v = self._scaled_px(left, effective_scale, floor_ratio=margin_floor, minimum=0)
        top_v = self._scaled_px(top, effective_scale, floor_ratio=margin_floor, minimum=0)
        right_v = self._scaled_px(right, effective_scale, floor_ratio=margin_floor, minimum=0)
        bottom_v = self._scaled_px(bottom, effective_scale, floor_ratio=margin_floor, minimum=0)
        layout.setContentsMargins(left_v, top_v, right_v, bottom_v)
        if base_spacing >= 0:
            spacing_v = self._scaled_px(base_spacing, effective_scale, floor_ratio=spacing_floor, minimum=0)
            layout.setSpacing(spacing_v)

    def _track_min_height(self, widget, base_px: int):
        if widget is None:
            return
        spec = (widget, int(base_px))
        self._scalable_min_heights.append(spec)
        self._apply_min_height_metric(spec, self._spatial_scale())

    def _apply_min_height_metric(self, spec, scale: float):
        widget, base_px = spec
        effective_scale = float(scale) * float(getattr(self, "_layout_density", 1.0))
        if sys.platform == "win32":
            floor_ratio = 0.58
            min_height = 40
        elif sys.platform == "darwin":
            floor_ratio = 0.64
            min_height = 42
        else:
            floor_ratio = 0.8
            min_height = 56
        widget.setMinimumHeight(self._scaled_px(base_px, effective_scale, floor_ratio=floor_ratio, minimum=min_height))

    def _update_font_scale(self) -> None:
        """Recompute scale and refresh every tracked label + the global QSS
        button/input rules so nothing gets clipped at narrow window widths."""
        scale = self._font_scale()
        spatial_scale = self._spatial_scale()

        # Update individually tracked labels (headers, card titles, etc.)
        for widget, base_px, extra_style in list(self._scalable_text):
            try:
                sz = max(10, int(round(base_px * scale)))
                widget.setStyleSheet(f"font-size: {sz}px; {extra_style}")
            except RuntimeError:
                pass  # widget was deleted

        # Regenerate the button / input rules appended to the base QSS.
        # SecondaryButton and ActionButton padding shrinks with the window so
        # long button labels (e.g. "Reorganize Preview") never get clipped.
        btn_font  = max(11, int(round(14 * scale)))
        btn_pad_v = max(8,  int(round(12 * scale)))
        btn_pad_h = max(12, int(round(24 * scale)))
        nav_font  = max(12, int(round(15 * scale)))
        nav_pad_v = max(8,  int(round(12 * scale)))
        font_overrides = f"""
            QPushButton.SecondaryButton {{
                font-size: {btn_font}px;
                padding: {btn_pad_v}px {btn_pad_h}px;
            }}
            QPushButton.ActionButton {{
                font-size: {btn_font}px;
                padding: {btn_pad_v}px {btn_pad_h}px;
            }}
            QPushButton#SendToSplitterButton {{
                font-size: {btn_font}px;
                padding: {btn_pad_v}px {btn_pad_h}px;
            }}
            QPushButton.NavButton {{
                font-size: {nav_font}px;
                padding: {nav_pad_v}px 20px;
            }}
        """
        if self._base_qss:
            self.setStyleSheet(self._base_qss + font_overrides)

        for spec in list(self._scalable_layouts):
            layout = spec[0]
            try:
                self._apply_layout_metric(spec, spatial_scale)
            except RuntimeError:
                pass

        for spec in list(self._scalable_min_heights):
            widget = spec[0]
            try:
                self._apply_min_height_metric(spec, spatial_scale)
            except RuntimeError:
                pass

        # Keep Start Processing button dimensions synced with dynamic scale
        # while preserving its dedicated green/gray rounded-cell visuals.
        run_btn = getattr(self, "run_btn", None)
        if run_btn is not None:
            try:
                is_busy = bool(run_btn.property("busyState"))
                is_cancel = bool(run_btn.property("cancelState"))
                run_btn.setStyleSheet(self._run_button_cell_style(busy=is_busy, cancel=is_cancel))
            except Exception:
                pass
        ident_run_btn = getattr(self, "ident_run_btn", None)
        if ident_run_btn is not None:
            try:
                ident_run_btn.setStyleSheet(self._run_button_cell_style(busy=(not ident_run_btn.isEnabled())))
            except Exception:
                pass
        send_btn = getattr(self, "send_to_splitter_btn", None)
        if send_btn is not None:
            try:
                self._set_rounded_action_button_style(send_btn, busy=(not send_btn.isEnabled()))
            except Exception:
                pass
        cd_rip_start_btn = getattr(self, "cd_rip_start_btn", None)
        if cd_rip_start_btn is not None:
            try:
                self._set_cd_rip_start_button_style(busy=(not cd_rip_start_btn.isEnabled()))
            except Exception:
                pass
        cd_rip_ident_btn = getattr(self, "cd_rip_send_to_identifier_btn", None)
        if cd_rip_ident_btn is not None:
            try:
                self._set_rounded_action_button_style(cd_rip_ident_btn, busy=(not cd_rip_ident_btn.isEnabled()))
            except Exception:
                pass
        cd_rip_editor_btn = getattr(self, "cd_rip_send_to_editor_btn", None)
        if cd_rip_editor_btn is not None:
            try:
                self._set_rounded_action_button_style(cd_rip_editor_btn, busy=(not cd_rip_editor_btn.isEnabled()))
            except Exception:
                pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_titlebar_drag_strip()
        self._position_nav_highlight(animated=False)
        self._position_nav_connector(animated=False)
        self._sync_sidebar_blur_geometry()
        self._sync_content_blur_geometry()
        if hasattr(self, "page_reveal_overlay"):
            self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
        self._update_font_scale()

    def _raise_titlebar_overlays_deferred(self):
        """Re-raise title overlays after NSViews are fully settled.

        Called via QTimer.singleShot(0) from showEvent so all native NSViews
        have been created and parented before we reorder the subview array.
        Without the deferral, raise_() during showEvent fires before macOS has
        inserted the child NSViews into content_container's view hierarchy,
        leaving TitlebarFadeOverlay below GlassPage in the compositor.
        """
        fade = getattr(self, "titlebar_fade_overlay", None)
        strip = getattr(self, "titlebar_content_spacer", None)
        if fade is not None and fade.isVisible():
            fade.raise_()
        if strip is not None and strip.isVisible():
            strip.raise_()
        # Qt's raise_() doesn't always reorder native NSViews on macOS.
        # Explicitly place fade above content, then strip above fade.
        if sys.platform == "darwin" and objc is not None:
            try:
                ns_fade = None
                ns_strip = None
                if fade is not None and fade.isVisible():
                    ns_fade = objc.objc_object(c_void_p=int(fade.winId()))
                if strip is not None and strip.isVisible():
                    ns_strip = objc.objc_object(c_void_p=int(strip.winId()))
                # NSWindowAbove = -1 in AppKit ordering constants.
                if ns_fade is not None:
                    ns_parent = ns_fade.superview()
                    if ns_parent is not None:
                        ns_parent.addSubview_positioned_relativeTo_(ns_fade, -1, None)
                if ns_strip is not None:
                    ns_parent = ns_strip.superview()
                    if ns_parent is not None:
                        ns_parent.addSubview_positioned_relativeTo_(ns_strip, -1, None)
            except Exception:
                pass

    def _recover_after_window_state_change(self):
        """Restore macOS titlebar/overlay layering after fullscreen transitions."""
        self._layout_titlebar_drag_strip()
        self._position_nav_highlight(animated=False)
        self._position_nav_connector(animated=False)
        self._sync_sidebar_blur_geometry()
        self._sync_content_blur_geometry()
        self._refresh_scroll_top_fade_masks()
        if hasattr(self, "page_reveal_overlay"):
            self.page_reveal_overlay.setGeometry(self.stacked_widget.rect())
        if sys.platform == "darwin":
            # Fullscreen transitions can reset NSWindow titlebar appearance.
            # Force a fresh apply instead of relying on one-time guard.
            self._mac_titlebar_glass_applied = False
            self._apply_macos_titlebar_glass_if_available()
            self._setup_content_blur_if_available()
            self._sync_content_blur_geometry()
            self._sync_sidebar_blur_geometry()
            self._raise_titlebar_overlays_deferred()

    def showEvent(self, event):
        super().showEvent(event)
        self._prime_initial_page_cards_for_reveal()
        if not self._initial_screen_fit_applied:
            self._apply_initial_window_size()
        self._layout_titlebar_drag_strip()
        if sys.platform in ("win32", "darwin"):
            # Keep the startup fit ahead of the first reveal retries so the
            # initial card animation captures settled geometry.
            QTimer.singleShot(40, self._fit_window_to_splitter_page_startup)
        if sys.platform == "darwin":
            self._apply_macos_titlebar_glass_if_available()
            self._setup_content_blur_if_available()
            self._sync_content_blur_geometry()
            self._sync_sidebar_blur_geometry()
            # Deferred raise ensures NSViews are fully parented before we
            # reorder them — showEvent fires before the macOS view hierarchy
            # is fully settled, so an immediate raise_() has no effect.
            QTimer.singleShot(0, self._raise_titlebar_overlays_deferred)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event is None:
            return
        if event.type() == QEvent.WindowStateChange:
            # Run once immediately and again deferred because macOS fullscreen
            # transitions settle native view hierarchy asynchronously.
            self._recover_after_window_state_change()
            QTimer.singleShot(0, self._recover_after_window_state_change)
            QTimer.singleShot(120, self._recover_after_window_state_change)
            QTimer.singleShot(260, self._recover_after_window_state_change)
        elif event.type() == QEvent.ActivationChange and sys.platform == "darwin":
            if self.isActiveWindow():
                # App activation triggers a native compositor relayer on macOS.
                # Re-apply our overlay/blur stacking so first render and
                # post-activation render stay identical.
                self._recover_after_window_state_change()
                QTimer.singleShot(0, self._recover_after_window_state_change)
                QTimer.singleShot(90, self._recover_after_window_state_change)

    def _has_live_background_workers(self):
        try:
            worker = self.thread
            if worker is not None and worker.isRunning():
                return True
        except Exception:
            pass
        try:
            worker = self.ident_thread
            if worker is not None and worker.isRunning():
                return True
        except Exception:
            pass
        try:
            worker = self.preview_export_thread
            if worker is not None and worker.isRunning():
                return True
        except Exception:
            pass
        try:
            worker = self._update_check_thread
            if worker is not None and worker.isRunning():
                return True
        except Exception:
            pass
        try:
            loop_thread = self.recording_loopback_thread
            if loop_thread is not None and loop_thread.is_alive():
                return True
        except Exception:
            pass

        # ThreadPoolExecutor and subprocess pipe reader threads can survive
        # window close if a job is still winding down.
        try:
            current = threading.current_thread()
            for thread in threading.enumerate():
                if thread is None or thread is current or thread is threading.main_thread():
                    continue
                if not getattr(thread, "is_alive", lambda: False)():
                    continue
                if not bool(getattr(thread, "daemon", False)):
                    return True
        except Exception:
            pass
        return False

    def _terminate_child_processes_best_effort(self, timeout_seconds=0.8):
        if psutil is None:
            return 0
        try:
            current_process = psutil.Process(os.getpid())
            children = current_process.children(recursive=True)
        except Exception:
            return 0
        if not children:
            return 0
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        try:
            _gone, alive = psutil.wait_procs(children, timeout=max(0.1, float(timeout_seconds)))
        except Exception:
            alive = list(children)
        for child in alive:
            try:
                child.kill()
            except Exception:
                pass
        return len(children)

    def _style_standard_context_menu(
        self,
        menu,
        *,
        background="#22262B",
        border="#3B4149",
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

    def _show_styled_text_context_menu(self, widget, pos):
        if widget is None:
            return
        try:
            menu = widget.createStandardContextMenu()
        except Exception:
            menu = None
        if menu is None:
            return
        self._style_standard_context_menu(menu)
        menu.exec(widget.mapToGlobal(pos))

    def _install_standard_text_context_menus(self):
        track_editor = getattr(self, "track_editor_panel", None)

        def _belongs_to_track_editor(widget):
            current = widget
            while current is not None:
                if track_editor is not None and current is track_editor:
                    return True
                current = current.parentWidget() if hasattr(current, "parentWidget") else None
            return False

        for widget in self.findChildren(QLineEdit):
            if _belongs_to_track_editor(widget):
                continue
            if bool(widget.property("mixsplitrStyledContextMenu")):
                continue
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, target=widget: self._show_styled_text_context_menu(target, pos)
            )
            widget.setProperty("mixsplitrStyledContextMenu", True)

        for widget in self.findChildren(QTextEdit):
            if _belongs_to_track_editor(widget):
                continue
            if bool(widget.property("mixsplitrStyledContextMenu")):
                continue
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, target=widget: self._show_styled_text_context_menu(target, pos)
            )
            widget.setProperty("mixsplitrStyledContextMenu", True)

    def closeEvent(self, event):
        force_kill_required = False
        try:
            if self.recording_active:
                self.stop_recording()
        except Exception:
            pass
        try:
            self._stop_audio_level_meter()
        except Exception:
            pass
        try:
            if self._update_check_thread is not None and self._update_check_thread.isRunning():
                self._update_check_thread.requestInterruption()
                self._update_check_thread.wait(150)
        except Exception:
            pass
        try:
            worker = self.thread
            if worker is not None and worker.isRunning():
                try:
                    if hasattr(worker, "request_cancel"):
                        worker.request_cancel()
                    else:
                        worker.requestInterruption()
                except Exception:
                    pass
                worker.wait(1200)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(800)
        except Exception:
            pass
        try:
            worker = self.ident_thread
            if worker is not None and worker.isRunning():
                try:
                    if hasattr(worker, "request_cancel"):
                        worker.request_cancel()
                    else:
                        worker.requestInterruption()
                except Exception:
                    pass
                worker.wait(1200)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(800)
        except Exception:
            pass
        try:
            worker = self.preview_export_thread
            if worker is not None and worker.isRunning():
                try:
                    worker.requestInterruption()
                except Exception:
                    pass
                worker.wait(500)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(500)
        except Exception:
            pass
        try:
            loader = getattr(self, "_session_editor_loader_thread", None)
            if loader is not None and loader.isRunning():
                loader.requestInterruption()
                loader.wait(500)
        except Exception:
            pass
        try:
            force_kill_required = self._has_live_background_workers()
        except Exception:
            force_kill_required = False
        if force_kill_required:
            try:
                self._terminate_child_processes_best_effort(timeout_seconds=0.8)
            except Exception:
                pass
        try:
            self._set_global_debug_capture_enabled(False)
        except Exception:
            pass
        super().closeEvent(event)
        if force_kill_required:
            # Last resort: prevent headless background runs after UI is closed.
            os._exit(0)


install_history_methods(
    MixSplitRApp,
    manifest_module=mixsplitr_manifest,
    session_module=mixsplitr_session,
    lazy_import_backend=_lazy_import_backend,
)
install_editor_help_methods(
    MixSplitRApp,
    vb_cable_download_url=VB_CABLE_DOWNLOAD_URL,
    blackhole_download_url=BLACKHOLE_DOWNLOAD_URL,
)
install_settings_ui_methods(MixSplitRApp)

install_recording_cd_methods(
    MixSplitRApp,
    core_module=mixsplitr_core,
    process_capture_module=mixsplitr_process_capture,
    cdrip_module=mixsplitr_cdrip,
    resolve_bundled_resource_path=_resolve_bundled_resource_path,
    vb_cable_download_url=VB_CABLE_DOWNLOAD_URL,
    blackhole_download_url=BLACKHOLE_DOWNLOAD_URL,
    blackhole_brew_install_action_url=BLACKHOLE_BREW_INSTALL_ACTION_URL,
)


def _run_optional_feature_self_test() -> int:
    """CLI self-test for packaged runtime optional imports."""
    import platform

    checks = {}
    required_modules = ("acrcloud", "essentia")

    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
            checks[module_name] = {"ok": True, "error": ""}
        except Exception as exc:
            checks[module_name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        importlib.import_module("essentia.standard")
        checks["essentia.standard"] = {"ok": True, "error": ""}
    except Exception as exc:
        checks["essentia.standard"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    runtime = {}
    try:
        if mixsplitr_essentia is not None and hasattr(mixsplitr_essentia, "get_runtime_status"):
            runtime_obj = mixsplitr_essentia.get_runtime_status()
            runtime = runtime_obj.__dict__ if hasattr(runtime_obj, "__dict__") else dict(runtime_obj)
    except Exception as exc:
        runtime = {"error": f"{type(exc).__name__}: {exc}"}

    payload = {
        "self_test": "optional_features",
        "ok": all(checks.get(name, {}).get("ok", False) for name in required_modules),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "checks": checks,
        "essentia_runtime": runtime,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 2


def _run_essentia_import_probe() -> int:
    """Lightweight subprocess-safe probe used by frozen runtime checks."""
    try:
        importlib.import_module("essentia")
        importlib.import_module("essentia.standard")
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    try:
        _set_bootstrap_debug_capture_enabled(_should_enable_bootstrap_debug_capture())

        if (
            "--probe-essentia-import" in sys.argv
            or os.environ.get("MIXSPLITR_ESSENTIA_IMPORT_PROBE", "").strip() == "1"
        ):
            sys.exit(_run_essentia_import_probe())

        if (
            "--self-test-optional" in sys.argv
            or os.environ.get("MIXSPLITR_SELFTEST_OPTIONAL", "").strip() == "1"
        ):
            sys.exit(_run_optional_feature_self_test())

        if sys.platform == "darwin" and not MACOS_VIBRANCY_AVAILABLE:
            detail = MACOS_VIBRANCY_IMPORT_ERROR or "unknown reason"
            print(
                "Warning: macOS vibrancy bridge unavailable "
                f"(pyobjc/AppKit import failed: {detail}). "
                "Sidebar transparency/blur may be limited.",
                file=sys.stderr,
            )

        # Honor the OS scaling factor directly instead of rounding Qt's DPI scale.
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except Exception:
            pass

        # Enable hardware-accelerated compositing before QApplication exists.
        # AA_ShareOpenGLContexts lets Qt share a single GPU context across all
        # windows so textures and shader programs are not re-uploaded every frame.
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
        # Let standard widgets receive touch as synthesized mouse input unless
        # they explicitly implement their own touch handling.
        if hasattr(Qt, "AA_SynthesizeMouseForUnhandledTouchEvents"):
            QApplication.setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)

        # Ask the GPU driver for a double-buffered OpenGL surface.
        # This is used by any QOpenGLWidget / RHI surfaces inside the app.
        _fmt = QSurfaceFormat()
        _fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
        _fmt.setSamples(4)
        _fmt.setSwapInterval(1)   # vsync — avoids tearing without hammering the CPU
        QSurfaceFormat.setDefaultFormat(_fmt)

        app = QApplication(sys.argv)

        # Register bundled Roboto fonts (if provided in ./fonts during build).
        _roboto_families, _roboto_thin_family, _roboto_light_family, _roboto_bold_family = _register_bundled_roboto_fonts()
        if _roboto_families:
            _BUNDLED_ROBOTO_BOLD_FAMILY = str(
                _roboto_bold_family
                or ("Roboto" if "Roboto" in _roboto_families else _roboto_families[0])
            ).strip()
            # Prefer the bundled Roboto Light face as the app-wide default.
            _preferred_family = _roboto_light_family or (
                "Roboto" if "Roboto" in _roboto_families else _roboto_families[0]
            )
            _base_font = app.font()
            _app_font = QFont(_preferred_family, _base_font.pointSize())
            _app_font.setStyleName("Light")
            try:
                _app_font.setWeight(QFont.Weight.Light)
            except Exception:
                _app_font.setWeight(300)
            app.setFont(_app_font)

        # QSplashScreen can crash on some macOS/PySide6 setups before the main
        # window is shown. Use a lightweight frameless QLabel splash on macOS
        # so startup still has parity without touching the crash-prone class.
        splash = None
        _disable_splash = os.environ.get("MIXSPLITR_DISABLE_SPLASH", "").strip() == "1"
        _primary_screen = QGuiApplication.primaryScreen()
        if not _disable_splash and _primary_screen is not None:
            _splash_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "mixsplitr_icon_512.png",
            )
            _splash_pixmap = QPixmap(_splash_path)
            if not _splash_pixmap.isNull():
                # Scale to a reasonable splash size.
                _splash_pixmap = _splash_pixmap.scaled(
                    300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                # Place the splash on whichever screen the cursor is on (same
                # screen the main window will appear on).
                _cursor_pos = (
                    QGuiApplication.screenAt(QCursor.pos())
                    if hasattr(QGuiApplication, "screenAt")
                    else None
                )
                _target_screen = _cursor_pos or _primary_screen
                if sys.platform == "darwin":
                    splash = QLabel()
                    splash.setPixmap(_splash_pixmap)
                    splash.setAlignment(Qt.AlignCenter)
                    splash.setWindowFlags(
                        Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                    )
                    splash.setAttribute(Qt.WA_TranslucentBackground, True)
                    splash.setStyleSheet("background: transparent; border: none;")
                    splash.resize(_splash_pixmap.size())
                else:
                    splash = QSplashScreen(_splash_pixmap, Qt.WindowStaysOnTopHint)
                if _target_screen is not None:
                    _sg = _target_screen.geometry()
                    _sx = _sg.x() + (_sg.width() - splash.width()) // 2
                    _sy = _sg.y() + (_sg.height() - splash.height()) // 2
                    splash.move(_sx, _sy)
                splash.show()
                app.processEvents()

        window = MixSplitRApp()
        window.show()

        if splash is not None:
            if isinstance(splash, QSplashScreen):
                splash.finish(window)
            else:
                splash.close()
                splash.deleteLater()

        sys.exit(app.exec())
    except Exception:
        try:
            import traceback
            _err_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_error.log")
            with open(_err_path, "w", encoding="utf-8") as _fh:
                _fh.write(traceback.format_exc())
            print(f"Startup failed. Wrote traceback to: {_err_path}", file=sys.stderr)
        except Exception:
            pass
        raise
