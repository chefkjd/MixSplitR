"""
mixsplitr_core.py - Core utilities for MixSplitR

FIXES APPLIED:
1. Fixed API key detection to use correct config key names (access_key vs acrcloud_access_key)
2. Enhanced ffmpeg path detection and error handling
3. Added debug output for configuration troubleshooting

Contains:
- Terminal styling (Style class)
- Version info and update checking
- Resource path handling (PyInstaller compatible)
- FFmpeg/FFprobe setup
- Configuration management
- Rate limiting
- Audio file analysis utilities
"""

import os
import sys
import json
import time
import shutil
import threading
import subprocess
import re
import requests
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mixsplitr_utils import windows_hidden_subprocess_kwargs as _windows_hidden_subprocess_kwargs

# =============================================================================
# TERMINAL STYLING - ANSI escape codes for colors and formatting
# =============================================================================

class Style:
    """ANSI escape codes for terminal styling"""
    # Colors
    CYAN = '\033[38;5;110m'
    GREEN = '\033[38;5;72m'
    YELLOW = '\033[38;5;186m'
    RED = '\033[38;5;167m'
    MAGENTA = '\033[38;5;104m'
    BLUE = '\033[38;5;68m'
    WHITE = '\033[38;5;252m'
    BRIGHT_WHITE = WHITE
    GRAY = '\033[38;5;242m'
    
    # Formatting
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    
    # Reset
    RESET = '\033[0m'
    
    @staticmethod
    def disable():
        """Disable colors (for non-supporting terminals)"""
        Style.CYAN = Style.GREEN = Style.YELLOW = Style.RED = ''
        Style.MAGENTA = Style.BLUE = Style.WHITE = Style.GRAY = ''
        Style.BRIGHT_WHITE = ''
        Style.BOLD = Style.DIM = Style.UNDERLINE = Style.RESET = ''

# Enable ANSI on Windows 10+
if sys.platform == 'win32':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        Style.disable()


_WINDOWS_SUBPROCESS_PATCHED = False


def _patch_windows_subprocess_no_console():
    """Force Windows child processes to stay windowless unless explicitly requested.

    This prevents `ffmpeg`/`fpcalc` terminal flashes from both MixSplitR code and
    third-party libraries (for example pydub) in GUI builds.
    Set MIXSPLITR_ALLOW_SUBPROCESS_CONSOLE=1 to disable this behavior.
    """
    global _WINDOWS_SUBPROCESS_PATCHED
    if _WINDOWS_SUBPROCESS_PATCHED or os.name != "nt":
        return
    if str(os.environ.get("MIXSPLITR_ALLOW_SUBPROCESS_CONSOLE", "")).strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return

    try:
        original_popen = subprocess.Popen

        # Avoid wrapping multiple times if this module is reloaded.
        if getattr(original_popen, "__name__", "") == "_MixSplitRHiddenPopen":
            _WINDOWS_SUBPROCESS_PATCHED = True
            return

        class _MixSplitRHiddenPopen(original_popen):
            def __init__(self, *args, **kwargs):
                try:
                    creationflags = int(kwargs.get("creationflags", 0) or 0)
                except Exception:
                    creationflags = 0

                create_new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) or 0)

                # Respect explicit requests to open a new console.
                if not (creationflags & create_new_console):
                    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
                    if create_no_window:
                        creationflags |= create_no_window
                        kwargs["creationflags"] = creationflags

                    startupinfo = kwargs.get("startupinfo")
                    if startupinfo is None:
                        try:
                            startupinfo = subprocess.STARTUPINFO()
                        except Exception:
                            startupinfo = None

                    if startupinfo is not None:
                        try:
                            startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
                            startupinfo.wShowWindow = 0  # SW_HIDE
                            kwargs["startupinfo"] = startupinfo
                        except Exception:
                            pass

                super().__init__(*args, **kwargs)

        subprocess.Popen = _MixSplitRHiddenPopen
        _WINDOWS_SUBPROCESS_PATCHED = True
    except Exception:
        _WINDOWS_SUBPROCESS_PATCHED = False


def _patch_windows_library_subprocess_aliases():
    """Patch third-party modules that cache subprocess.Popen at import time."""
    if os.name != "nt":
        return
    try:
        import pydub.utils as _pydub_utils  # type: ignore
        _pydub_utils.Popen = subprocess.Popen
    except Exception:
        pass


_patch_windows_subprocess_no_console()
_patch_windows_library_subprocess_aliases()

# =============================================================================
# AUDIO FILE CONSTANTS
# =============================================================================

# For glob patterns (e.g., glob.glob(os.path.join(dir, ext)))
AUDIO_EXTENSIONS_GLOB = ['*.wav', '*.flac', '*.mp3', '*.m4a', '*.ogg', '*.aac', '*.wma', '*.aiff', '*.opus']

# For checking file extensions (e.g., if ext.lower() in AUDIO_EXTENSIONS)
AUDIO_EXTENSIONS = ['.wav', '.flac', '.mp3', '.m4a', '.ogg', '.aac', '.wma', '.aiff', '.opus']

# =============================================================================
# VERSION INFO
# =============================================================================

CURRENT_VERSION = "8.0"
GITLAB_REPO = "chefkjd/MixSplitR"  # GitLab project path
GITHUB_REPO = "chefkjd/MixSplitR"
KOFI_URL = "https://ko-fi.com/mixsplitr"

# ---------------------------------------------------------------------------
# Identification-mode helpers
# ---------------------------------------------------------------------------
# Valid values stored in config["mode"]:
#   "acrcloud"          – original behaviour (ACRCloud primary, MB fallback)
#   "musicbrainz_only"  – AcoustID fingerprint → MusicBrainz only, no ACRCloud
#   "split_only_no_id"  – No lookup mode, sequential Track NN naming only
#   "auto_tracklist_no_manual" – Window scan + auto timeline + timestamp export
# ---------------------------------------------------------------------------

MODE_ACRCLOUD = "acrcloud"
MODE_MB_ONLY  = "musicbrainz_only"
MODE_MANUAL   = "manual_search_only"
MODE_DUAL     = "dual_best_match"  # Both ACRCloud + AcoustID, pick best
MODE_SPLIT_ONLY = "split_only_no_id"
MODE_AUTO_TRACKLIST = "auto_tracklist_no_manual"
VALID_MODES   = (
    MODE_ACRCLOUD,
    MODE_MB_ONLY,
    MODE_MANUAL,
    MODE_DUAL,
    MODE_SPLIT_ONLY,
    MODE_AUTO_TRACKLIST,
)


def get_mode(config=None):
    """
    Return the current identification mode from config.
    Auto-detects dual mode if both keys available, manual if none.
    
    FIX: Now correctly checks for 'access_key' (ACRCloud) not 'acrcloud_access_key'
    """
    if config is None:
        config = get_config()

    # Check if any fingerprinting keys are configured
    # FIX: Use correct key names from config
    has_acrcloud = bool(config.get('access_key'))  # FIXED: was 'acrcloud_access_key'
    has_acoustid = bool(config.get('acoustid_api_key'))

    # Get configured mode
    configured_mode = config.get("mode", MODE_SPLIT_ONLY)
    if configured_mode not in VALID_MODES:
        configured_mode = MODE_ACRCLOUD

    # Split-only and auto-tracklist are explicit modes that can run without keys.
    if configured_mode in (MODE_SPLIT_ONLY, MODE_AUTO_TRACKLIST):
        return configured_mode

    # If no keys, force manual mode
    if not has_acrcloud and not has_acoustid:
        return MODE_MANUAL

    # If dual mode requested but missing keys, fallback
    if configured_mode == MODE_DUAL:
        if not has_acrcloud or not has_acoustid:
            # Missing one or both keys for dual mode
            if has_acrcloud:
                return MODE_ACRCLOUD
            elif has_acoustid:
                return MODE_MB_ONLY
            else:
                return MODE_MANUAL

    # If mode is acrcloud but no acrcloud keys, switch to musicbrainz
    if configured_mode == MODE_ACRCLOUD and not has_acrcloud:
        if has_acoustid:
            return MODE_MB_ONLY
        return MODE_MANUAL

    # If both keys available and no mode set, offer dual mode
    if has_acrcloud and has_acoustid and configured_mode == MODE_ACRCLOUD:
        # Could auto-enable dual mode, but let user choose
        return configured_mode

    return configured_mode

# Global settings (can be modified by config)
LASTFM_API_KEY = None
USE_LOCAL_BPM = True

# Split sensitivity settings: effective threshold = base + offset.
DEFAULT_SPLIT_SILENCE_THRESH_DB = -38
DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS = 20
SPLIT_SILENCE_SEEK_STEP_OPTIONS_MS = (10, 20, 30)
DEFAULT_SPLIT_SENSITIVITY_DB = 0
SPLIT_SENSITIVITY_MIN_DB = -10
SPLIT_SENSITIVITY_MAX_DB = 10
DEFAULT_DUPLICATE_POLICY = "skip"
DUPLICATE_POLICY_KEEP_BEST_QUALITY = "keep_best_quality"
DUPLICATE_POLICY_OPTIONS = (
    "skip",
    "overwrite",
    DUPLICATE_POLICY_KEEP_BEST_QUALITY,
)
DUPLICATE_POLICY_OVERWRITE = "overwrite"
ARTIST_NORMALIZATION_MODE_OFF = "off"
ARTIST_NORMALIZATION_MODE_COLLAB_ONLY = "collab_only"
ARTIST_NORMALIZATION_MODE_SMART = "smart"
ARTIST_NORMALIZATION_MODES = (
    ARTIST_NORMALIZATION_MODE_OFF,
    ARTIST_NORMALIZATION_MODE_COLLAB_ONLY,
    ARTIST_NORMALIZATION_MODE_SMART,
)
DEFAULT_ARTIST_NORMALIZATION_MODE = ARTIST_NORMALIZATION_MODE_COLLAB_ONLY
DEFAULT_ARTIST_NORMALIZATION_STRICTNESS = 0.92
MIN_ARTIST_NORMALIZATION_STRICTNESS = 0.75
MAX_ARTIST_NORMALIZATION_STRICTNESS = 0.99

# Essentia settings shared across auto-tracklist and metadata enrichment.
ESSENTIA_UNIFIED_PIPELINE_DEFAULT = True
ESSENTIA_AUTOTRACKLIST_ENABLED_DEFAULT = True
ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT = 0.36
ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT = 2400
ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT = True
ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT = True
ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT = 0.34
ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT = 2
ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT = 28


def normalize_split_sensitivity_db(value, default=DEFAULT_SPLIT_SENSITIVITY_DB):
    """Parse and clamp split sensitivity offset in dB."""
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(SPLIT_SENSITIVITY_MIN_DB, min(SPLIT_SENSITIVITY_MAX_DB, parsed))


def normalize_split_silence_seek_step_ms(value, default=DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS):
    """Return a valid silence seek step (ms) from the allowed discrete options."""
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    if parsed in SPLIT_SILENCE_SEEK_STEP_OPTIONS_MS:
        return parsed
    # Map nearest valid choice for legacy/custom values.
    return min(
        SPLIT_SILENCE_SEEK_STEP_OPTIONS_MS,
        key=lambda option: abs(int(option) - int(parsed)),
    )


def get_split_silence_seek_step_ms(config=None):
    """Return effective silence detection seek-step in milliseconds."""
    seek_step = DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS
    if isinstance(config, dict):
        seek_step = normalize_split_silence_seek_step_ms(
            config.get("split_silence_seek_step_ms", DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS)
        )
    return int(seek_step)


def normalize_duplicate_policy(value, default=DEFAULT_DUPLICATE_POLICY):
    """Normalize duplicate collision policy for output exports."""
    if isinstance(value, bool):
        return DUPLICATE_POLICY_OVERWRITE if value else DEFAULT_DUPLICATE_POLICY
    policy = str(value or default).strip().lower()
    if policy in (
        "best",
        "best_quality",
        "highest_quality",
        "high_quality",
        "quality",
        "dupeguru",
        "prefer_best",
        "smart",
        "keep_highest_quality",
    ):
        policy = DUPLICATE_POLICY_KEEP_BEST_QUALITY
    if policy in DUPLICATE_POLICY_OPTIONS:
        return policy
    return str(default)


def normalize_artist_normalization_mode(value, legacy_normalize_artists=True):
    """Normalize artist normalization mode with legacy bool fallback."""
    if isinstance(value, bool):
        return (
            ARTIST_NORMALIZATION_MODE_COLLAB_ONLY
            if value
            else ARTIST_NORMALIZATION_MODE_OFF
        )

    text = str(value or "").strip().lower()
    if text in ARTIST_NORMALIZATION_MODES:
        return text
    if text in ("on", "enabled", "true", "1", "yes"):
        return ARTIST_NORMALIZATION_MODE_COLLAB_ONLY
    if text in ("off", "disabled", "false", "0", "no"):
        return ARTIST_NORMALIZATION_MODE_OFF
    return (
        ARTIST_NORMALIZATION_MODE_COLLAB_ONLY
        if bool(legacy_normalize_artists)
        else ARTIST_NORMALIZATION_MODE_OFF
    )


def normalize_artist_normalization_strictness(
    value,
    default=DEFAULT_ARTIST_NORMALIZATION_STRICTNESS,
):
    """Parse and clamp smart artist normalization merge strictness."""
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return float(
        max(
            MIN_ARTIST_NORMALIZATION_STRICTNESS,
            min(MAX_ARTIST_NORMALIZATION_STRICTNESS, parsed),
        )
    )


def _normalize_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return bool(int(value))
        except Exception:
            return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "enabled"):
            return True
        if text in ("0", "false", "no", "off", "disabled"):
            return False
    return bool(default)


def _normalize_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return int(max(minimum, min(maximum, parsed)))


def _normalize_float(value, default, minimum, maximum):
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return float(max(minimum, min(maximum, parsed)))


def apply_essentia_config_defaults(config):
    """Ensure Essentia config keys are present and clamped."""
    if not isinstance(config, dict):
        return False

    changed = False

    def _set_bool(key, default):
        nonlocal changed
        value = _normalize_bool(config.get(key), default)
        if config.get(key) != value:
            config[key] = value
            changed = True

    def _set_int(key, default, minimum, maximum):
        nonlocal changed
        value = _normalize_int(config.get(key, default), default, minimum, maximum)
        if config.get(key) != value:
            config[key] = value
            changed = True

    def _set_float(key, default, minimum, maximum):
        nonlocal changed
        value = _normalize_float(config.get(key, default), default, minimum, maximum)
        if config.get(key) != value:
            config[key] = value
            changed = True

    _set_bool("essentia_unified_pipeline", ESSENTIA_UNIFIED_PIPELINE_DEFAULT)
    _set_bool("auto_tracklist_essentia_enabled", ESSENTIA_AUTOTRACKLIST_ENABLED_DEFAULT)
    _set_float(
        "auto_tracklist_essentia_min_confidence",
        ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT,
        0.05,
        0.95,
    )
    _set_int(
        "auto_tracklist_essentia_max_points",
        ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT,
        200,
        6000,
    )
    _set_bool(
        "essentia_genre_enrichment_enabled",
        ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT,
    )
    _set_bool(
        "essentia_genre_enrichment_when_missing_only",
        ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT,
    )
    _set_float(
        "essentia_genre_enrichment_min_confidence",
        ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT,
        0.2,
        0.9,
    )
    _set_int(
        "essentia_genre_enrichment_max_tags",
        ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT,
        1,
        5,
    )
    _set_int(
        "essentia_genre_enrichment_analysis_seconds",
        ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT,
        8,
        60,
    )
    return changed


def normalize_essentia_runtime_config(config):
    """Return normalized Essentia config for runtime/pipeline snapshots."""
    cfg = config if isinstance(config, dict) else {}
    return {
        "unified_pipeline": _normalize_bool(
            cfg.get("essentia_unified_pipeline"),
            ESSENTIA_UNIFIED_PIPELINE_DEFAULT,
        ),
        "transition": {
            "enabled": _normalize_bool(
                cfg.get("auto_tracklist_essentia_enabled"),
                ESSENTIA_AUTOTRACKLIST_ENABLED_DEFAULT,
            ),
            "min_confidence": _normalize_float(
                cfg.get("auto_tracklist_essentia_min_confidence"),
                ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT,
                0.05,
                0.95,
            ),
            "max_points": _normalize_int(
                cfg.get("auto_tracklist_essentia_max_points"),
                ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT,
                200,
                6000,
            ),
        },
        "genre": {
            "enabled": _normalize_bool(
                cfg.get("essentia_genre_enrichment_enabled"),
                ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT,
            ),
            "when_missing_only": _normalize_bool(
                cfg.get("essentia_genre_enrichment_when_missing_only"),
                ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT,
            ),
            "min_confidence": _normalize_float(
                cfg.get("essentia_genre_enrichment_min_confidence"),
                ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT,
                0.2,
                0.9,
            ),
            "max_tags": _normalize_int(
                cfg.get("essentia_genre_enrichment_max_tags"),
                ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT,
                1,
                5,
            ),
            "analysis_seconds": _normalize_int(
                cfg.get("essentia_genre_enrichment_analysis_seconds"),
                ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT,
                8,
                60,
            ),
        },
    }


def _normalize_essentia_runtime_status(runtime_status):
    if runtime_status is None:
        return {
            "available": False,
            "reason": "",
            "python_executable": "",
            "numpy_available": False,
            "numpy_import_error": "",
            "essentia_version": "",
            "essentia_import_error": "",
        }
    if hasattr(runtime_status, "to_dict"):
        try:
            data = runtime_status.to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if isinstance(runtime_status, dict):
        return dict(runtime_status)
    return {
        "available": False,
        "reason": str(runtime_status),
        "python_executable": "",
        "numpy_available": False,
        "numpy_import_error": "",
        "essentia_version": "",
        "essentia_import_error": "",
    }


def build_essentia_config_snapshot(config, runtime_status=None):
    """Return additive normalized Essentia snapshot payload."""
    normalized = normalize_essentia_runtime_config(config)
    runtime = _normalize_essentia_runtime_status(runtime_status)
    return {
        "essentia": {
            "unified_pipeline": bool(normalized.get("unified_pipeline", True)),
            "transition": dict(normalized.get("transition") or {}),
            "genre": dict(normalized.get("genre") or {}),
            "runtime": runtime,
        }
    }



def get_split_silence_threshold_db(config=None):
    """
    Return effective split silence threshold (dBFS).
    0 sensitivity maps to DEFAULT_SPLIT_SILENCE_THRESH_DB.
    """
    sensitivity_db = DEFAULT_SPLIT_SENSITIVITY_DB
    if isinstance(config, dict):
        sensitivity_db = normalize_split_sensitivity_db(
            config.get('split_sensitivity_db', DEFAULT_SPLIT_SENSITIVITY_DB)
        )
    return int(DEFAULT_SPLIT_SILENCE_THRESH_DB + sensitivity_db)


# =============================================================================
# UPDATE CHECKING
# =============================================================================

def _parse_version_parts(version_text: str):
    """Parse version-like text into integer parts for comparison."""
    if not version_text:
        return []
    cleaned = str(version_text).strip().lower().lstrip('v')
    return [int(p) for p in re.findall(r'\d+', cleaned)]


def _is_newer_version(latest: str, current: str) -> bool:
    """Return True if latest > current based on numeric version parts."""
    latest_parts = _parse_version_parts(latest)
    current_parts = _parse_version_parts(current)
    if not latest_parts or not current_parts:
        return False

    max_len = max(len(latest_parts), len(current_parts))
    latest_parts += [0] * (max_len - len(latest_parts))
    current_parts += [0] * (max_len - len(current_parts))
    return latest_parts > current_parts


def check_for_updates():
    """Check GitHub releases and tags for a newer version."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MixSplitR-UpdateCheck"
    }

    release_url = f"https://github.com/{GITHUB_REPO}/releases"
    candidates = []

    try:
        # Candidate 1: latest GitHub release
        release_resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=headers,
            timeout=5
        )
        if release_resp.status_code == 200:
            release = release_resp.json()
            rel_version = (release.get("tag_name") or release.get("name") or "").strip().lstrip('v')
            rel_url = release.get("html_url") or release_url
            if _parse_version_parts(rel_version):
                candidates.append({"version": rel_version, "url": rel_url})

        # Candidate 2..N: latest tags (works even when releases are not maintained)
        tags_resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=20",
            headers=headers,
            timeout=5
        )
        if tags_resp.status_code == 200:
            tags = tags_resp.json() or []
            for tag in tags:
                raw_name = (tag.get("name") or "").strip()
                tag_version = raw_name.lstrip('v')
                if _parse_version_parts(tag_version):
                    candidates.append({
                        "version": tag_version,
                        "url": f"https://github.com/{GITHUB_REPO}/releases/tag/{raw_name}"
                    })
    except Exception:
        return None

    if not candidates:
        return None

    # Pick the highest semantic version across release + tags.
    best = max(candidates, key=lambda c: _parse_version_parts(c["version"]))
    if not _is_newer_version(best["version"], CURRENT_VERSION):
        return False

    return {
        "latest": best["version"],
        "current": CURRENT_VERSION,
        "url": best["url"] or release_url
    }


# =============================================================================
# RESOURCE PATH HANDLING
# =============================================================================

def resource_path(relative_path):
    """Get path to resource, works for dev and PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# FFmpeg paths - initialized at module load
ffmpeg_path = resource_path("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
ffprobe_path = resource_path("ffprobe.exe" if sys.platform == "win32" else "ffprobe")


def setup_ffmpeg():
    """Setup FFmpeg paths and permissions
    
    FIX: Enhanced error handling and fallback detection
    """
    global ffmpeg_path, ffprobe_path

    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

    def _resolve_binary(env_var: str, bundled_name: str) -> str:
        """Resolve binary path from env, bundle, exe dir, cwd, then system PATH."""
        env_value = os.environ.get(env_var)
        if env_value and os.path.exists(env_value):
            return env_value

        candidates = [
            resource_path(bundled_name),
            os.path.join(os.path.dirname(sys.executable), bundled_name),
            os.path.join(os.getcwd(), bundled_name),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate

        base_name = os.path.splitext(bundled_name)[0]
        system_path = shutil.which(base_name) or shutil.which(bundled_name)
        if system_path and os.path.exists(system_path):
            return system_path
        return ""

    ffmpeg_resolved = _resolve_binary('FFMPEG_BINARY', ffmpeg_name)
    ffprobe_resolved = _resolve_binary('FFPROBE_BINARY', ffprobe_name)

    # If ffprobe wasn't found directly, try alongside ffmpeg.
    if not ffprobe_resolved and ffmpeg_resolved:
        sibling_probe = os.path.join(os.path.dirname(ffmpeg_resolved), ffprobe_name)
        if os.path.exists(sibling_probe):
            ffprobe_resolved = sibling_probe

    if not ffmpeg_resolved:
        raise FileNotFoundError(
            "FFmpeg binary not found. Bundle ffmpeg.exe with the app or install FFmpeg."
        )
    if not ffprobe_resolved:
        raise FileNotFoundError(
            "FFprobe binary not found. Bundle ffprobe.exe with the app or install FFmpeg."
        )

    ffmpeg_path = ffmpeg_resolved
    ffprobe_path = ffprobe_resolved

    # Ensure pydub (which uses `which('ffprobe')`) can discover bundled binaries.
    path_parts = os.environ.get('PATH', '').split(os.pathsep) if os.environ.get('PATH') else []
    prepend_dirs = []
    for binary in (ffmpeg_path, ffprobe_path):
        binary_dir = os.path.dirname(binary)
        if binary_dir and binary_dir not in path_parts and binary_dir not in prepend_dirs:
            prepend_dirs.append(binary_dir)
    if prepend_dirs:
        os.environ['PATH'] = os.pathsep.join(prepend_dirs + path_parts) if path_parts else os.pathsep.join(prepend_dirs)

    # Set executable permissions on Unix-like systems
    if sys.platform != 'win32':
        try:
            os.chmod(ffmpeg_path, 0o755)
            os.chmod(ffprobe_path, 0o755)
        except Exception as e:
            print(f"  ⚠️  Could not set executable permissions: {e}")
    
    # Update environment variables
    os.environ['FFMPEG_BINARY'] = ffmpeg_path
    os.environ['FFPROBE_BINARY'] = ffprobe_path
    _patch_windows_library_subprocess_aliases()
    
    return ffmpeg_path, ffprobe_path


# Rest of the file continues with the original implementation...
# (Including get_app_data_dir, get_config_path, get_cache_path, RateLimiter, etc.)
# I'll include the key functions that need to be complete:

def get_app_data_dir():
    """Get platform-specific app data directory"""
    if sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    elif sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    else:
        base = Path.home() / '.local' / 'share'
    
    app_dir = base / 'MixSplitR'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_default_music_folder():
    """Get the system default Music folder for the current platform."""
    music = Path.home() / "Music"
    music.mkdir(parents=True, exist_ok=True)
    return str(music)


def get_output_directory(config=None):
    """Get the output directory for processed tracks.

    Checks config['output_directory'] first; falls back to ~/Music/MixSplitR Library.
    """
    if config is None:
        config = get_config()
    custom = config.get('output_directory', '')
    if custom and os.path.isabs(custom):
        os.makedirs(custom, exist_ok=True)
        return custom
    default = os.path.join(get_default_music_folder(), "MixSplitR Library")
    os.makedirs(default, exist_ok=True)
    return default


def get_recording_directory(config=None):
    """Get the directory where recordings are saved.

    Checks config['recording_directory'] first; falls back to
    ~/Music/Mixsplitr Recordings.
    """
    if config is None:
        config = get_config()
    custom = config.get('recording_directory', '')
    if custom and os.path.isabs(custom):
        os.makedirs(custom, exist_ok=True)
        return custom
    default = os.path.join(get_default_music_folder(), "Mixsplitr Recordings")
    os.makedirs(default, exist_ok=True)
    return default


def get_cd_rip_output_directory(config=None):
    """Get the directory where ripped CD tracks are saved."""
    if config is None:
        config = get_config()
    custom = config.get('cd_rip_output_directory', '')
    if custom and os.path.isabs(custom):
        os.makedirs(custom, exist_ok=True)
        return custom
    default = os.path.join(get_default_music_folder(), "MixSplitR CD Rips")
    os.makedirs(default, exist_ok=True)
    return default


def is_cd_rip_menu_enabled(config=None) -> bool:
    """Return whether CD-ripping UI entry points should be shown."""
    env_value = os.environ.get("MIXSPLITR_ENABLE_CD_RIP_MENU", "").strip().lower()
    if env_value in ("1", "true", "yes", "y", "on"):
        return True
    if env_value in ("0", "false", "no", "n", "off"):
        return False
    if config is None:
        config = get_config()
    return bool(config.get("cd_rip_menu_enabled", False))


def get_manifest_directory(config=None):
    """Get the directory where session manifests are stored.

    Checks config['manifest_directory'] first; falls back to the app data
    folder (e.g. ~/Library/Application Support/MixSplitR/manifests on macOS).
    """
    if config is None:
        config = get_config()
    custom = config.get('manifest_directory', '')
    if custom and os.path.isabs(custom):
        os.makedirs(custom, exist_ok=True)
        return custom
    default = str(get_app_data_dir() / "manifests")
    os.makedirs(default, exist_ok=True)
    return default


def get_config_path():
    """Get path to configuration file"""
    return get_app_data_dir() / 'config.json'


def get_cache_path(cache_name="mixsplitr_cache.json"):
    """Get path to cache file"""
    return get_app_data_dir() / cache_name


_RUNTIME_TEMP_CONFIG_CACHE_LOCK = threading.Lock()
_RUNTIME_TEMP_CONFIG_CACHE_KEY = None
_RUNTIME_TEMP_CONFIG_CACHE_VALUE = ""


def _default_runtime_temp_root_directory():
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Caches" / "MixSplitR")
    if sys.platform == "win32":
        return str(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MixSplitR" / "Temp")
    return str(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "MixSplitR")


def _normalize_directory_path(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


def _configured_runtime_temp_root_directory():
    global _RUNTIME_TEMP_CONFIG_CACHE_KEY, _RUNTIME_TEMP_CONFIG_CACHE_VALUE
    env_override = _normalize_directory_path(os.environ.get("MIXSPLITR_TEMP_ROOT", ""))
    if env_override:
        return env_override

    config_path = str(get_config_path())
    try:
        stat = os.stat(config_path)
        cache_key = f"{config_path}:{int(getattr(stat, 'st_mtime_ns', 0))}:{int(getattr(stat, 'st_size', 0))}"
    except Exception:
        cache_key = f"{config_path}:missing"

    with _RUNTIME_TEMP_CONFIG_CACHE_LOCK:
        if _RUNTIME_TEMP_CONFIG_CACHE_KEY == cache_key and _RUNTIME_TEMP_CONFIG_CACHE_VALUE:
            return str(_RUNTIME_TEMP_CONFIG_CACHE_VALUE)

    configured = ""
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
                if isinstance(loaded, dict):
                    configured = _normalize_directory_path(loaded.get("temp_workspace_directory", ""))
    except Exception:
        configured = ""

    resolved = configured or _default_runtime_temp_root_directory()
    with _RUNTIME_TEMP_CONFIG_CACHE_LOCK:
        _RUNTIME_TEMP_CONFIG_CACHE_KEY = cache_key
        _RUNTIME_TEMP_CONFIG_CACHE_VALUE = str(resolved)
    return str(resolved)


def get_runtime_temp_directory(subfolder="mixsplitr_temp"):
    """Return a safe writable temp workspace path for runtime artifacts.

    This keeps transient files away from the current working directory
    (for example, when the app/exe is launched from Desktop).
    """
    configured_root = _configured_runtime_temp_root_directory()
    base = Path(str(configured_root or _default_runtime_temp_root_directory()))
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path(_default_runtime_temp_root_directory())

    if subfolder:
        safe_subfolder = str(subfolder).strip().strip("/\\")
        if safe_subfolder:
            base = base / safe_subfolder

    base.mkdir(parents=True, exist_ok=True)
    return str(base)


# Rate limiter
class RateLimiter:
    """Rate limiter with configurable requests per second or min_interval"""
    def __init__(self, requests_per_second=None, min_interval=None):
        # Support both min_interval (direct delay) and requests_per_second
        if min_interval is not None:
            self.delay = min_interval
        elif requests_per_second is not None:
            self.delay = 1.0 / requests_per_second
        else:
            self.delay = 1.0 / 3  # default: 3 requests per second
        self.last_request = 0
        self.lock = threading.Lock()
    
    def wait(self):
        """Wait if necessary to maintain rate limit.

        The sleep is performed *outside* the lock so that multiple threads
        can overlap their wait periods instead of queuing up behind the
        lock.  Only the timestamp bookkeeping is protected.
        """
        sleep_duration = 0.0
        with self.lock:
            now = time.time()
            time_since_last = now - self.last_request
            if time_since_last < self.delay:
                sleep_duration = self.delay - time_since_last
            # Optimistically reserve our slot NOW so the next thread
            # calculates its wait relative to our planned finish time.
            self.last_request = now + sleep_duration

        if sleep_duration > 0:
            time.sleep(sleep_duration)


# Large file constants
LARGE_FILE_THRESHOLD = 500 * 1024 * 1024  # 500 MB


def is_large_file(file_path):
    """Check if file is considered large (>500MB)"""
    try:
        size = os.path.getsize(file_path)
        return size > LARGE_FILE_THRESHOLD
    except OSError:
        return False


def get_file_size_str(file_path):
    """Get human-readable file size"""
    try:
        size = os.path.getsize(file_path)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    except OSError:
        return "Unknown"


# FFmpeg helpers for large files
def ffmpeg_detect_silence(
    file_path,
    silence_thresh_db=DEFAULT_SPLIT_SILENCE_THRESH_DB,
    min_silence_len=2.0,
    min_silence_len_sec=None,
):
    """Detect silence in audio file using ffmpeg"""
    if min_silence_len_sec is not None:
        min_silence_len = min_silence_len_sec
    cmd = [
        ffmpeg_path, '-i', file_path,
        '-af', f'silencedetect=noise={silence_thresh_db}dB:d={min_silence_len}',
        '-f', 'null', '-'
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            **_windows_hidden_subprocess_kwargs(),
        )
        return result.stderr
    except Exception as e:
        print(f"Error detecting silence: {e}")
        return None


def ffmpeg_get_split_points_from_silence(silence_output):
    """Parse ffmpeg silence detection output to get split points"""
    import re
    
    split_points = []
    silence_end_pattern = re.compile(r'silence_end: ([\d.]+)')
    
    for line in silence_output.split('\n'):
        match = silence_end_pattern.search(line)
        if match:
            timestamp = float(match.group(1))
            split_points.append(timestamp)
    
    return split_points


def ffmpeg_split_file(file_path, split_points, output_dir):
    """Split audio file at specified timestamps using ffmpeg"""
    output_files = []
    total = len(split_points)

    try:
        from tqdm import tqdm
        iterator = tqdm(enumerate(split_points), total=total, desc="  ✂️  Splitting", unit="chunk", leave=True)
    except ImportError:
        iterator = enumerate(split_points)

    for i, start_time in iterator:
        end_time = split_points[i + 1] if i + 1 < len(split_points) else None

        output_file = os.path.join(output_dir, f"chunk_{i+1:03d}.flac")

        cmd = [
            ffmpeg_path, '-i', file_path,
            '-ss', str(start_time)
        ]

        if end_time:
            cmd.extend(['-t', str(end_time - start_time)])

        cmd.extend([
            '-c:a', 'flac',
            '-compression_level', '8',
            output_file
        ])

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                timeout=300,
                **_windows_hidden_subprocess_kwargs(),
            )
            output_files.append(output_file)
        except Exception as e:
            print(f"Error splitting at {start_time}: {e}")

    return output_files


def ffmpeg_extract_chunk_for_identification(file_path, start_seconds=30, duration=10):
    """Extract a small chunk for identification without loading full file"""
    import tempfile

    runtime_temp_dir = get_runtime_temp_directory("runtime")
    temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=runtime_temp_dir)
    temp_file.close()
    
    cmd = [
        ffmpeg_path, '-i', file_path,
        '-ss', str(start_seconds),
        '-t', str(duration),
        '-acodec', 'pcm_s16le',
        '-ar', '44100',
        '-ac', '2',
        temp_file.name
    ]
    
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            timeout=60,
            **_windows_hidden_subprocess_kwargs(),
        )
        return temp_file.name
    except Exception as e:
        print(f"Error extracting chunk: {e}")
        if os.path.exists(temp_file.name):
            os.remove(temp_file.name)
        return None


def validate_acrcloud_credentials(config):
    """
    Test ACRCloud credentials by attempting recognition on silence.
    Returns: (is_valid: bool, error_message: str or None)
    """
    try:
        from acrcloud.recognizer import ACRCloudRecognizer
    except ImportError:
        return False, "ACRCloud SDK not installed"
    
    try:
        # Create test config
        test_config = {
            'host': config.get('host', ''),
            'access_key': config.get('access_key', ''),
            'access_secret': config.get('access_secret', ''),
            'timeout': config.get('timeout', 10)
        }
        
        # Create recognizer
        recognizer = ACRCloudRecognizer(test_config)
        
        # Create 1 second of silence for testing
        import wave
        import tempfile
        import struct

        runtime_temp_dir = get_runtime_temp_directory("runtime")
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False, dir=runtime_temp_dir)
        temp_file.close()
        
        try:
            with wave.open(temp_file.name, 'wb') as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(44100)
                silence = struct.pack('<h', 0) * 44100 * 2
                wav.writeframes(silence)
            
            # Try to recognize (will fail to match, but tests credentials)
            result = recognizer.recognize_by_file(temp_file.name, 0)
        finally:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)
        
        # Parse response
        import json
        response = json.loads(result)
        status_code = response.get('status', {}).get('code', -1)
        status_msg = response.get('status', {}).get('msg', 'Unknown error')
        
        # ACRCloud status codes:
        # Code 0 = success (match found)
        # Code 1001 = no match found (credentials work)
        # Code 2004 = can't generate fingerprint (mute/silence - credentials work!)
        # Code 3001 = missing/invalid access key
        # Code 3002 = invalid access secret  
        # Code 3003 = limit exceeded
        # Code 3014 = invalid audio format
        
        # These codes mean credentials ARE valid (the request went through)
        if status_code in [0, 1001, 2004, 3014]:
            return True, None
        elif status_code == 3001:
            return False, "Invalid Access Key"
        elif status_code == 3002:
            return False, "Invalid Access Secret"
        elif status_code == 3003:
            return False, "API limit exceeded - try again later"
        else:
            return False, f"API error: {status_msg} (code {status_code})"
            
    except ImportError:
        return False, "ACRCloud SDK not installed"
    except requests.exceptions.ConnectionError:
        return False, f"Cannot connect to {config.get('host')} - check your internet connection"
    except requests.exceptions.Timeout:
        return False, "Connection timed out - check host address"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


def get_config():
    """Load or create configuration.

    On first run the user is asked which identification mode to use:
      1. ACRCloud + MusicBrainz  (original behaviour)
      2. MusicBrainz only        (no ACRCloud account needed)
      3. Split only (No ID)
      4. Timestamping

    ACRCloud credentials are only requested when mode == "acrcloud".
    """
    global LASTFM_API_KEY, USE_LOCAL_BPM

    config_path = get_config_path()

    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            if config.get('lastfm_api_key'):
                LASTFM_API_KEY = config['lastfm_api_key']
            if config.get('disable_local_bpm'):
                USE_LOCAL_BPM = False
            changed = False
            # Back-fill mode for configs created before v7.1
            if 'mode' not in config:
                if config.get('host') and config.get('access_key'):
                    config['mode'] = MODE_ACRCLOUD
                else:
                    config['mode'] = MODE_MB_ONLY
                changed = True
            # Back-fill portable startup scan option for existing configs
            if 'portable_mode_local_scan' not in config:
                config['portable_mode_local_scan'] = False
                changed = True
            # Back-fill fingerprint sample length (seconds) used for ACRCloud/Dual matching
            sample_seconds = config.get('fingerprint_sample_seconds')
            if not isinstance(sample_seconds, int) or sample_seconds < 8 or sample_seconds > 45:
                config['fingerprint_sample_seconds'] = 12
                changed = True
            # Back-fill fingerprint probe mode
            probe_mode = str(config.get('fingerprint_probe_mode', 'single')).strip().lower()
            if probe_mode not in ('single', 'multi3'):
                config['fingerprint_probe_mode'] = 'single'
                changed = True
            if not isinstance(config.get('preserve_source_format'), bool):
                config['preserve_source_format'] = False
                changed = True
            # Back-fill CD ripping settings
            default_cd_rip_dir = os.path.join(get_default_music_folder(), "MixSplitR CD Rips")
            cd_rip_dir = config.get('cd_rip_output_directory')
            if not isinstance(cd_rip_dir, str) or not cd_rip_dir.strip():
                config['cd_rip_output_directory'] = default_cd_rip_dir
                changed = True
            elif not os.path.isabs(cd_rip_dir):
                config['cd_rip_output_directory'] = default_cd_rip_dir
                changed = True
            cd_rip_format = str(config.get('cd_rip_format', 'flac')).strip().lower()
            if cd_rip_format not in ('flac', 'wav', 'mp3_320'):
                config['cd_rip_format'] = 'flac'
                changed = True
            if not isinstance(config.get('cd_rip_auto_metadata'), bool):
                config['cd_rip_auto_metadata'] = True
                changed = True
            if not isinstance(config.get('cd_rip_eject_when_done'), bool):
                config['cd_rip_eject_when_done'] = False
                changed = True
            if not isinstance(config.get('cd_rip_menu_enabled'), bool):
                config['cd_rip_menu_enabled'] = False
                changed = True
            temp_workspace = _normalize_directory_path(config.get('temp_workspace_directory', ''))
            if not temp_workspace:
                config['temp_workspace_directory'] = _default_runtime_temp_root_directory()
                changed = True
            elif config.get('temp_workspace_directory') != temp_workspace:
                config['temp_workspace_directory'] = temp_workspace
                changed = True
            # Back-fill tracklist scan settings
            def _cfg_int(key, default, minimum, maximum):
                nonlocal changed
                try:
                    value = int(config.get(key, default))
                except Exception:
                    value = default
                value = max(minimum, min(maximum, value))
                if config.get(key) != value:
                    config[key] = value
                    changed = True

            def _cfg_float(key, default, minimum, maximum):
                nonlocal changed
                try:
                    value = float(config.get(key, default))
                except Exception:
                    value = default
                value = max(minimum, min(maximum, value))
                if config.get(key) != value:
                    config[key] = value
                    changed = True

            _cfg_int('auto_tracklist_window_seconds', 18, 8, 60)
            _cfg_int('auto_tracklist_step_seconds', 12, 5, 120)
            _cfg_int('auto_tracklist_min_segment_seconds', 30, 15, 300)
            _cfg_int('auto_tracklist_fallback_interval_seconds', 180, 60, 900)
            _cfg_int('auto_tracklist_max_windows', 120, 20, 1000)
            _cfg_int('auto_tracklist_shazam_timeout_seconds', 10, 5, 45)
            _cfg_int('auto_tracklist_shazam_max_windows', 120, 20, 500)
            _cfg_int('split_boundary_merge_gap_ms', 2500, 0, 12000)
            _cfg_int('split_sensitivity_db', DEFAULT_SPLIT_SENSITIVITY_DB, SPLIT_SENSITIVITY_MIN_DB, SPLIT_SENSITIVITY_MAX_DB)
            seek_step_ms = normalize_split_silence_seek_step_ms(
                config.get('split_silence_seek_step_ms', DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS)
            )
            if config.get('split_silence_seek_step_ms') != seek_step_ms:
                config['split_silence_seek_step_ms'] = seek_step_ms
                changed = True
            duplicate_policy = normalize_duplicate_policy(
                config.get('duplicate_policy', DEFAULT_DUPLICATE_POLICY)
            )
            if config.get('duplicate_policy') != duplicate_policy:
                config['duplicate_policy'] = duplicate_policy
                changed = True
            legacy_normalize_flag = bool(config.get('normalize_artists', True))
            artist_mode = normalize_artist_normalization_mode(
                config.get('artist_normalization_mode', ''),
                legacy_normalize_artists=legacy_normalize_flag,
            )
            if config.get('artist_normalization_mode') != artist_mode:
                config['artist_normalization_mode'] = artist_mode
                changed = True
            normalize_artists_legacy = bool(artist_mode != ARTIST_NORMALIZATION_MODE_OFF)
            if config.get('normalize_artists') != normalize_artists_legacy:
                config['normalize_artists'] = normalize_artists_legacy
                changed = True
            artist_strictness = normalize_artist_normalization_strictness(
                config.get(
                    'artist_normalization_strictness',
                    DEFAULT_ARTIST_NORMALIZATION_STRICTNESS,
                )
            )
            if config.get('artist_normalization_strictness') != artist_strictness:
                config['artist_normalization_strictness'] = artist_strictness
                changed = True
            if not isinstance(config.get('artist_normalization_collapse_backing_band'), bool):
                config['artist_normalization_collapse_backing_band'] = False
                changed = True
            if not isinstance(config.get('artist_normalization_review_ambiguous'), bool):
                config['artist_normalization_review_ambiguous'] = True
                changed = True
            _cfg_int('auto_tracklist_silence_min_ms', 1600, 300, 5000)
            _cfg_int('auto_tracklist_silence_thresh_db', -42, -70, -20)
            _cfg_int('auto_tracklist_silence_max_segment_seconds', 360, 60, 900)
            _cfg_int('auto_tracklist_silence_first_min_anchors', 5, 2, 40)
            _cfg_int('auto_tracklist_persistence_windows', 2, 1, 5)
            _cfg_float('auto_tracklist_micro_refine_backtrack_seconds', 8.0, 2.0, 20.0)
            _cfg_float('auto_tracklist_micro_refine_forward_seconds', 3.0, 0.0, 8.0)
            _cfg_float('auto_tracklist_micro_refine_step_seconds', 0.5, 0.25, 2.0)
            _cfg_float('auto_tracklist_micro_refine_min_confidence', 0.46, 0.2, 0.95)
            _cfg_float('auto_tracklist_min_confidence', 0.58, 0.25, 0.95)
            _cfg_float('auto_tracklist_boundary_backtrack_seconds', 0.0, 0.0, 20.0)
            _cfg_float('auto_tracklist_short_circuit_confidence', 0.90, 0.55, 0.99)
            if not isinstance(config.get('auto_tracklist_no_identify'), bool):
                config['auto_tracklist_no_identify'] = True
                changed = True
            if not isinstance(config.get('auto_tracklist_silence_first'), bool):
                config['auto_tracklist_silence_first'] = False
                changed = True
            if not isinstance(config.get('auto_tracklist_micro_refine_enabled'), bool):
                config['auto_tracklist_micro_refine_enabled'] = True
                changed = True
            if not isinstance(config.get('auto_tracklist_dry_run'), bool):
                config['auto_tracklist_dry_run'] = False
                changed = True
            if apply_essentia_config_defaults(config):
                changed = True

            # Migration: if user is still on legacy timestamping defaults, move to
            # improved seamless-mix defaults automatically.
            def _migrate_legacy_default(key, legacy_value, new_value):
                nonlocal changed
                if config.get(key) == legacy_value:
                    config[key] = new_value
                    changed = True

            _migrate_legacy_default('auto_tracklist_window_seconds', 20, 18)
            _migrate_legacy_default('auto_tracklist_step_seconds', 20, 12)
            _migrate_legacy_default('auto_tracklist_min_segment_seconds', 45, 30)
            _migrate_legacy_default('auto_tracklist_shazam_timeout_seconds', 12, 10)
            _migrate_legacy_default('auto_tracklist_shazam_max_windows', 80, 120)
            _migrate_legacy_default('auto_tracklist_silence_min_ms', 1200, 1600)
            _migrate_legacy_default('auto_tracklist_silence_thresh_db', -38, -42)
            _migrate_legacy_default('auto_tracklist_silence_first', True, False)
            _migrate_legacy_default('auto_tracklist_persistence_windows', 1, 2)
            _migrate_legacy_default('auto_tracklist_micro_refine_min_confidence', 0.4, 0.46)
            _migrate_legacy_default('auto_tracklist_essentia_min_confidence', 0.3, 0.36)
            if changed:
                save_config(config)
            return config

    # ── First-run setup ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("          MixSplitR – First-Run Setup")
    print("=" * 60)
    print("\nMixSplitR can run in four processing modes:\n")
    print("  1. ACRCloud + MusicBrainz  (best accuracy)")
    print("       Requires a free ACRCloud account")
    print("       → https://console.acrcloud.com\n")
    print("  2. MusicBrainz only        (no account needed)")
    print("       Uses audio fingerprinting via AcoustID")
    print("       → Works entirely offline-friendly, no sign-ups\n")
    print("  3. Split only (No ID)      (no metadata lookups)")
    print("       Splits audio and names tracks sequentially")
    print("       → Track 01, Track 02, ...\n")
    print("  4. Timestamping       (timeline + timestamps)")
    print("       Scans overlapping windows and auto-builds a timestamp list")
    print("       → Falls back to Track N labels when IDs are unavailable\n")

    while True:
        mode_choice = input("  Choose mode (1/2/3/4) [1]: ").strip()
        if mode_choice in ('', '1'):
            chosen_mode = MODE_ACRCLOUD
            break
        elif mode_choice == '2':
            chosen_mode = MODE_MB_ONLY
            break
        elif mode_choice == '3':
            chosen_mode = MODE_SPLIT_ONLY
            break
        elif mode_choice == '4':
            chosen_mode = MODE_AUTO_TRACKLIST
            break
        print("  Please enter 1, 2, 3, or 4.\n")

    conf = {
        'mode': chosen_mode,
        'timeout': 10,
        'fingerprint_sample_seconds': 12,
        'fingerprint_probe_mode': 'single',
        'cd_rip_output_directory': os.path.join(get_default_music_folder(), "MixSplitR CD Rips"),
        'temp_workspace_directory': _default_runtime_temp_root_directory(),
        'cd_rip_format': 'flac',
        'cd_rip_auto_metadata': True,
        'cd_rip_eject_when_done': False,
        'cd_rip_menu_enabled': False,
        'auto_tracklist_window_seconds': 18,
        'auto_tracklist_step_seconds': 12,
        'auto_tracklist_min_segment_seconds': 30,
        'auto_tracklist_fallback_interval_seconds': 180,
        'auto_tracklist_max_windows': 120,
        'auto_tracklist_shazam_timeout_seconds': 10,
        'auto_tracklist_shazam_max_windows': 120,
        'split_boundary_merge_gap_ms': 2500,
        'split_sensitivity_db': DEFAULT_SPLIT_SENSITIVITY_DB,
        'split_silence_seek_step_ms': DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS,
        'duplicate_policy': DEFAULT_DUPLICATE_POLICY,
        'preserve_source_format': False,
        'artist_normalization_mode': DEFAULT_ARTIST_NORMALIZATION_MODE,
        'artist_normalization_strictness': DEFAULT_ARTIST_NORMALIZATION_STRICTNESS,
        'artist_normalization_collapse_backing_band': False,
        'artist_normalization_review_ambiguous': True,
        'normalize_artists': True,
        'essentia_unified_pipeline': ESSENTIA_UNIFIED_PIPELINE_DEFAULT,
        'essentia_genre_enrichment_enabled': ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT,
        'essentia_genre_enrichment_when_missing_only': ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT,
        'essentia_genre_enrichment_min_confidence': ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT,
        'essentia_genre_enrichment_max_tags': ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT,
        'essentia_genre_enrichment_analysis_seconds': ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT,
        'auto_tracklist_min_confidence': 0.58,
        'auto_tracklist_boundary_backtrack_seconds': 0.0,
        'auto_tracklist_short_circuit_confidence': 0.90,
        'auto_tracklist_silence_first': False,
        'auto_tracklist_silence_min_ms': 1600,
        'auto_tracklist_silence_thresh_db': -42,
        'auto_tracklist_silence_max_segment_seconds': 360,
        'auto_tracklist_silence_first_min_anchors': 5,
        'auto_tracklist_persistence_windows': 2,
        'auto_tracklist_micro_refine_enabled': True,
        'auto_tracklist_micro_refine_backtrack_seconds': 8.0,
        'auto_tracklist_micro_refine_forward_seconds': 3.0,
        'auto_tracklist_micro_refine_step_seconds': 0.5,
        'auto_tracklist_micro_refine_min_confidence': 0.46,
        'auto_tracklist_essentia_enabled': ESSENTIA_AUTOTRACKLIST_ENABLED_DEFAULT,
        'auto_tracklist_essentia_min_confidence': ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT,
        'auto_tracklist_essentia_max_points': ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT,
        'auto_tracklist_no_identify': True,
        'auto_tracklist_dry_run': False,
    }

    # ── Startup scan mode (portable/local folder scan) ──────────────────────
    print("\n" + "-" * 60)
    print("          Startup Scan Mode")
    print("-" * 60)
    print("\nOn startup, MixSplitR can auto-scan for audio files in either:")
    print("  • Local program folder (portable mode)")
    print("  • Your Music folder (default)\n")
    print("Enable portable mode if you keep audio next to the app/script.")

    portable_choice = input("  Enable portable startup scan? (y/n) [n]: ").strip().lower()
    conf['portable_mode_local_scan'] = portable_choice in ('y', 'yes')
    if conf['portable_mode_local_scan']:
        print(f"  {Style.GREEN}✅ Portable startup scan enabled{Style.RESET}")
    else:
        print(f"  {Style.GREEN}✅ Portable startup scan disabled (uses Music folder){Style.RESET}")

    # ── ACRCloud credentials (only in acrcloud mode) ────────────────────────
    if chosen_mode == MODE_ACRCLOUD:
        print("\n" + "-" * 60)
        print("          ACRCloud API Setup")
        print("-" * 60)
        print("\nGet your free API keys at: https://console.acrcloud.com\n")

        while True:
            host          = input("  Enter your ACR Host: ").strip()
            access_key    = input("  Enter your Access Key: ").strip()
            access_secret = input("  Enter your Secret Key: ").strip()

            if host and access_key and access_secret:
                break
            print("\n  ❌ All three fields are required!\n")

        conf['host']          = host
        conf['access_key']    = access_key
        conf['access_secret'] = access_secret
    elif chosen_mode == MODE_MB_ONLY:
        print(f"\n  {Style.GREEN}✓ MusicBrainz-only mode selected – no ACRCloud account needed.{Style.RESET}")
    elif chosen_mode == MODE_AUTO_TRACKLIST:
        print(f"\n  {Style.GREEN}✓ Timestamping mode selected – timestamps with fallback labels enabled.{Style.RESET}")
    else:
        print(f"\n  {Style.GREEN}✓ Split-only mode selected – metadata lookups disabled.{Style.RESET}")

    # ── AcoustID (optional, MusicBrainz-only mode) ──────────────────────────
    if chosen_mode == MODE_MB_ONLY:
        print("\n" + "-" * 60)
        print("          AcoustID API Key (Recommended)")
        print("-" * 60)
        print("\nAcoustID fingerprints audio to identify tracks in MusicBrainz-only mode.")
        print("You need a free API key for reliable identification.\n")
        print("  Steps:")
        print("    1. Visit: https://acoustid.org/api-key")
        print("    2. Register (free, takes 30 seconds)")
        print("    3. Copy your API key and paste below\n")

        add_acoustid = input("  Add your AcoustID API key now? (y/n) [y]: ").strip().lower()
        if add_acoustid in ('', 'y', 'yes'):
            acoustid_key = input("  Enter your AcoustID API Key: ").strip()
            if acoustid_key:
                conf['acoustid_api_key'] = acoustid_key
                print(f"  {Style.GREEN}✅ AcoustID API key added!{Style.RESET}")
            else:
                print(f"  {Style.YELLOW}⚠️  No key entered - you can add one later in Settings{Style.RESET}")
        else:
            print(f"  {Style.YELLOW}⚠️  Skipped - you can add a key later in: Main Menu → Manage API Keys{Style.RESET}")
            print(f"  {Style.YELLOW}     Without a key, fingerprinting will not work{Style.RESET}")

    # ── Last.fm (optional when metadata lookup modes are used) ─────────────
    if chosen_mode in (MODE_ACRCLOUD, MODE_MB_ONLY):
        print("\n" + "-" * 60)
        print("          Last.fm API Setup (Optional)")
        print("-" * 60)
        print("\nLast.fm improves genre detection with user-generated tags.")
        print("Get a free API key at: https://www.last.fm/api/account/create\n")

        add_lastfm = input("  Add Last.fm API key? (y/n) [n]: ").strip().lower()
        if add_lastfm == 'y':
            lastfm_key = input("  Enter your Last.fm API Key: ").strip()
            if lastfm_key:
                conf['lastfm_api_key'] = lastfm_key
                LASTFM_API_KEY = lastfm_key
                print("  ✅ Last.fm API key added!")

    print(f"\n  Config saved to: {config_path}\n")
    with open(config_path, 'w') as f:
        json.dump(conf, f, indent=4)
    return conf


def save_config(config):
    """Save configuration to file"""
    with open(get_config_path(), 'w') as f:
        json.dump(config, f, indent=4)


# =============================================================================
# AUDIO FILE ANALYSIS
# =============================================================================

_DURATION_CACHE_NAME = "duration_cache.json"
_DURATION_CACHE_MAX_ENTRIES = 50000
_DURATION_CACHE_SAVE_EVERY = 25
_duration_cache_lock = threading.Lock()
_duration_cache_entries = OrderedDict()
_duration_cache_loaded = False
_duration_cache_dirty = False
_duration_cache_pending_writes = 0


def _duration_cache_path():
    return get_cache_path(_DURATION_CACHE_NAME)


def _load_duration_cache_if_needed():
    global _duration_cache_loaded
    if _duration_cache_loaded:
        return
    with _duration_cache_lock:
        if _duration_cache_loaded:
            return
        cache_path = _duration_cache_path()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    raw_entries = payload.get("entries", payload)
                    if isinstance(raw_entries, dict):
                        for key, value in raw_entries.items():
                            try:
                                duration = float(value)
                            except Exception:
                                continue
                            if duration > 0:
                                _duration_cache_entries[str(key)] = duration
                        while len(_duration_cache_entries) > _DURATION_CACHE_MAX_ENTRIES:
                            _duration_cache_entries.popitem(last=False)
            except Exception:
                pass
        _duration_cache_loaded = True


def _duration_cache_key(file_path, file_stat=None):
    try:
        stat_value = file_stat if file_stat is not None else os.stat(file_path)
        return f"{os.path.abspath(file_path)}::{stat_value.st_size}::{stat_value.st_mtime_ns}"
    except Exception:
        return None


def _save_duration_cache(force=False):
    global _duration_cache_dirty, _duration_cache_pending_writes
    _load_duration_cache_if_needed()
    with _duration_cache_lock:
        if not _duration_cache_dirty:
            return
        if (not force) and _duration_cache_pending_writes < _DURATION_CACHE_SAVE_EVERY:
            return

        cache_path = _duration_cache_path()
        temp_path = str(cache_path) + ".tmp"
        payload = {
            "version": 1,
            "entries": dict(_duration_cache_entries),
        }
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            os.replace(temp_path, cache_path)
            _duration_cache_dirty = False
            _duration_cache_pending_writes = 0
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass


def get_audio_duration_fast(file_path, file_stat=None, use_cache=True):
    """Get audio duration quickly using ffprobe, with persistent caching."""
    global _duration_cache_dirty, _duration_cache_pending_writes
    cache_key = None
    if use_cache:
        _load_duration_cache_if_needed()
        cache_key = _duration_cache_key(file_path, file_stat=file_stat)
        if cache_key:
            with _duration_cache_lock:
                cached = _duration_cache_entries.get(cache_key)
                if cached is not None:
                    _duration_cache_entries.move_to_end(cache_key)
                    return float(cached)

    try:
        result = subprocess.run(
            [ffprobe_path, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=10,
            **_windows_hidden_subprocess_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            duration_seconds = float(result.stdout.strip())
            duration_minutes = duration_seconds / 60.0
            if use_cache and cache_key and duration_minutes > 0:
                with _duration_cache_lock:
                    _duration_cache_entries[cache_key] = duration_minutes
                    _duration_cache_entries.move_to_end(cache_key)
                    while len(_duration_cache_entries) > _DURATION_CACHE_MAX_ENTRIES:
                        _duration_cache_entries.popitem(last=False)
                    _duration_cache_dirty = True
                    _duration_cache_pending_writes += 1
                _save_duration_cache(force=False)
            return duration_minutes
    except Exception:
        pass
    return None


def analyze_files_parallel(audio_files, max_workers=4, progress_callback=None):
    """Analyze multiple audio files in parallel to determine type.

    Args:
        audio_files: list of file paths to analyse.
        max_workers: thread-pool size.
        progress_callback: optional callable(completed: int, total: int) fired
            after each file finishes so callers can update a progress indicator.
    """

    def analyze_single(file_path):
        file_stat = None
        size_mb = 0.0
        try:
            file_stat = os.stat(file_path)
            size_mb = file_stat.st_size / (1024 * 1024)
        except Exception:
            pass
        duration = get_audio_duration_fast(file_path, file_stat=file_stat)
        if duration is None:
            duration = size_mb / 10.0  # Rough estimate
        return {
            'file': file_path,
            'filename': os.path.basename(file_path),
            'duration_min': duration,
            'is_mix': duration >= 8,
            'size_mb': size_mb
        }

    total = len(audio_files)
    # Preserve input order while still reporting incremental progress.
    results = [None] * total
    index_map = {f: i for i, f in enumerate(audio_files)}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {executor.submit(analyze_single, f): f for f in audio_files}
        completed = 0
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            idx = index_map[file_path]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = {
                    'file': file_path,
                    'filename': os.path.basename(file_path),
                    'duration_min': 0,
                    'is_mix': False,
                    'size_mb': 0,
                }
            completed += 1
            if progress_callback is not None:
                try:
                    progress_callback(completed, total)
                except Exception:
                    pass

    _save_duration_cache(force=True)
    return results


# =============================================================================
# UTILITIES
# =============================================================================

def _supports_osc8_links() -> bool:
    """Return True when terminal likely supports OSC 8 hyperlinks."""
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("VTE_VERSION"):
        return True
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program in {"iTerm.app", "Apple_Terminal"}:
        return True
    term = os.environ.get("TERM", "")
    return term.startswith("xterm")


def _format_terminal_link(label: str, url: str) -> str:
    """Create a clickable terminal hyperlink when supported."""
    if not _supports_osc8_links():
        return label
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


def _print_close_screen_branding():
    """Render a branded close screen before the final Enter prompt."""
    term_size = shutil.get_terminal_size(fallback=(100, 24))
    cols = term_size.columns
    rows = term_size.lines
    # 15 branded lines + 1 prompt line printed by close_terminal().
    block_height = 16
    top_pad = max(0, (rows - block_height) // 2)
    if top_pad:
        print("\n" * top_pad, end="")

    logo_segments = [
        ("███╗   ███╗██╗██╗  ██╗", "███████╗██████╗ ██╗     ██╗████████╗", "██████╗ "),
        ("████╗ ████║██║╚██╗██╔╝", "██╔════╝██╔══██╗██║     ██║╚══██╔══╝", "██╔══██╗"),
        ("██╔████╔██║██║ ╚███╔╝ ", "███████╗██████╔╝██║     ██║   ██║   ", "██████╔╝"),
        ("██║╚██╔╝██║██║ ██╔██╗ ", "╚════██║██╔═══╝ ██║     ██║   ██║   ", "██╔══██╗"),
        ("██║ ╚═╝ ██║██║██╔╝ ██╗", "███████║██║     ███████╗██║   ██║   ", "██║  ██║"),
        ("╚═╝     ╚═╝╚═╝╚═╝  ╚═╝", "╚══════╝╚═╝     ╚══════╝╚═╝   ╚═╝   ", "╚═╝  ╚═╝"),
    ]
    divider = "═══════════════════════════════════════"
    red = "\033[38;5;196m"

    for mix_part, split_part, r_part in logo_segments:
        plain = f"{mix_part}{split_part}{r_part}"
        pad = " " * max(0, (cols - len(plain)) // 2)
        print(
            f"{pad}{Style.GRAY}{mix_part}"
            f"{Style.GRAY}{split_part}"
            f"{red}{r_part}{Style.RESET}"
        )

    print(f"{Style.GRAY}{divider.center(cols)}{Style.RESET}")
    print(f"{Style.GRAY}{'Mix Archival Tool'.center(cols)}{Style.RESET}")
    print(f"{Style.GRAY}{'By KJD'.center(cols)}{Style.RESET}")
    print(f"{Style.GRAY}{divider.center(cols)}{Style.RESET}")
    print()
    print(f"{Style.DIM}{'Always open source and free.'.center(cols)}{Style.RESET}")

    message = "Although, if I saved you some time, consider buying me a coffee/beer?"
    print(f"{Style.DIM}{message.center(cols)}{Style.RESET}")
    link_label = _format_terminal_link(KOFI_URL.center(cols), KOFI_URL)
    print(f"{red}{link_label}{Style.RESET}")
    print()


def _show_close_screen_prompt_toolkit() -> bool:
    """
    Show branded close screen with clickable ko-fi URL.
    Returns True if the prompt_toolkit screen was shown and handled.
    """
    try:
        import webbrowser
        from prompt_toolkit import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window, FormattedTextControl
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.styles import Style as PTStyle
        from prompt_toolkit.mouse_events import MouseEventType
    except Exception:
        return False

    term_size = shutil.get_terminal_size(fallback=(100, 24))
    cols = term_size.columns
    rows = term_size.lines
    # 15 branded lines + 1 prompt line.
    block_height = 16
    top_pad = max(0, (rows - block_height) // 2)

    logo_segments = [
        ("███╗   ███╗██╗██╗  ██╗", "███████╗██████╗ ██╗     ██╗████████╗", "██████╗ "),
        ("████╗ ████║██║╚██╗██╔╝", "██╔════╝██╔══██╗██║     ██║╚══██╔══╝", "██╔══██╗"),
        ("██╔████╔██║██║ ╚███╔╝ ", "███████╗██████╔╝██║     ██║   ██║   ", "██████╔╝"),
        ("██║╚██╔╝██║██║ ██╔██╗ ", "╚════██║██╔═══╝ ██║     ██║   ██║   ", "██╔══██╗"),
        ("██║ ╚═╝ ██║██║██╔╝ ██╗", "███████║██║     ███████╗██║   ██║   ", "██║  ██║"),
        ("╚═╝     ╚═╝╚═╝╚═╝  ╚═╝", "╚══════╝╚═╝     ╚══════╝╚═╝   ╚═╝   ", "╚═╝  ╚═╝"),
    ]
    divider = "═══════════════════════════════════════"

    def _open_kofi(mouse_event=None):
        if mouse_event is not None:
            if mouse_event.event_type != MouseEventType.MOUSE_UP:
                return
        try:
            webbrowser.open(KOFI_URL, new=2)
        except Exception:
            pass

    def _line_pad(text: str) -> str:
        return " " * max(0, (cols - len(text)) // 2)

    lines = []
    if top_pad:
        lines.append(("", "\n" * top_pad))

    for mix_part, split_part, r_part in logo_segments:
        plain = f"{mix_part}{split_part}{r_part}"
        lines.append(("class:logo_mix", _line_pad(plain) + mix_part))
        lines.append(("class:logo_split", split_part))
        lines.append(("class:logo_r", r_part + "\n"))

    lines.append(("class:logo_dim", f"{divider.center(cols)}\n"))
    lines.append(("class:logo_dim", f"{'Mix Archival Tool'.center(cols)}\n"))
    lines.append(("class:logo_dim", f"{'By KJD'.center(cols)}\n"))
    lines.append(("class:logo_dim", f"{divider.center(cols)}\n\n"))
    lines.append(("class:body", f"{'Always open source and free.'.center(cols)}\n"))

    lines.append(("class:body", f"{'Although, if I saved you some time, consider buying me a coffee/beer?'.center(cols)}\n"))
    lines.append(("class:link", f"{KOFI_URL.center(cols)}\n", _open_kofi))
    lines.append(("class:dim", "\n"))
    lines.append(("class:prompt", f"{'Press Enter to close...'.center(cols)}"))

    kb = KeyBindings()

    @kb.add("enter")
    @kb.add("escape")
    @kb.add("c-c")
    def _close(event):
        event.app.exit()

    app = Application(
        layout=Layout(
            Window(
                content=FormattedTextControl(lambda: FormattedText(lines)),
                always_hide_cursor=True,
                wrap_lines=True,
            )
        ),
        key_bindings=kb,
        style=PTStyle.from_dict({
            "logo_mix": "#6c6c6c",
            "logo_split": "#6c6c6c",
            "logo_r": "bold #ff0000",
            "logo_dim": "#6c6c6c",
            "body": "#8e95aa",
            "dim": "#7d8499",
            "prompt": "bold #e6e9f2",
            "link": "bold #ff0000",
        }),
        full_screen=False,
        mouse_support=True,
    )

    try:
        print("\033[2J\033[H", end="", flush=True)
    except Exception:
        pass

    app.run()
    return True


def close_terminal():
    """Gracefully end the app session without terminal script errors."""
    # Restore terminal state after prompt_toolkit fullscreen/mouse mode and
    # clear any stale buffer content (for example Session History screens).
    try:
        print("\033[0m\033[?25h\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[2J\033[H", end="", flush=True)
    except Exception:
        pass

    if not _show_close_screen_prompt_toolkit():
        try:
            _print_close_screen_branding()
        except Exception:
            pass
        try:
            cols = shutil.get_terminal_size(fallback=(100, 24)).columns
            input("Press Enter to close...".center(cols))
        except (EOFError, KeyboardInterrupt):
            pass

    # Only try auto-close when running as a bundled app.
    # In normal terminal runs we should never kill the user's shell window.
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Terminal" to if (count of windows) > 0 then close front window'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
