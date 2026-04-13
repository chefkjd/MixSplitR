#!/usr/bin/env python3
"""
MixSplitR v8.0 - Mix Archival Tool
Main entry point and orchestration

Identification modes (set during first-run setup or via Manage API Keys):
  • acrcloud          – ACRCloud primary + MusicBrainz fallback (original)
  • musicbrainz_only  – AcoustID fingerprint → MusicBrainz only (no account needed)
  • split_only_no_id  – Skip ID lookups, split and name sequentially (Track 01, Track 02...)
  • auto_tracklist_no_manual – Window-scan timeline with timestamp export + Track N fallback

This is the modular version with functionality split across:
- mixsplitr_core.py       - Configuration, utilities, rate limiting, mode helpers
- mixsplitr_processing.py - Track identification (4 modes + shared helpers)
- mixsplitr_pipeline.py   - Large file streaming, cache application
- mixsplitr_session.py    - Manifest browser, comparison, rollback UI
- mixsplitr_metadata.py   - iTunes, Deezer, Last.fm APIs
- mixsplitr_audio.py      - BPM detection (librosa)
- mixsplitr_identify.py   - AcoustID/MusicBrainz, result merging
- mixsplitr_tagging.py    - FLAC/ALAC embedding
- mixsplitr_memory.py     - RAM management, batching
- mixsplitr_editor.py     - Cache, interactive editor
- splitter_ui.py          - Visual waveform splitter (optional)
"""

import os
import sys
import glob
import json
import time
import shutil
import shlex
import re
import threading
import gc
import base64
import platform
import argparse
import subprocess
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Show a static splash immediately so dependency imports/checks do not present
# a blank terminal window on startup.
def _show_dependency_splash():
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return
    if os.environ.get("MIXSPLITR_NO_DEP_SPLASH", "").strip().lower() in ("1", "true", "yes", "on"):
        return

    try:
        cols, rows = shutil.get_terminal_size(fallback=(100, 30))
    except Exception:
        cols, rows = 100, 30

    logo_lines = [
        "███╗   ███╗██╗██╗  ██╗███████╗██████╗ ██╗     ██╗████████╗██████╗ ",
        "████╗ ████║██║╚██╗██╔╝██╔════╝██╔══██╗██║     ██║╚══██╔══╝██╔══██╗",
        "██╔████╔██║██║ ╚███╔╝ ███████╗██████╔╝██║     ██║   ██║   ██████╔╝",
        "██║╚██╔╝██║██║ ██╔██╗ ╚════██║██╔═══╝ ██║     ██║   ██║   ██╔══██╗",
        "██║ ╚═╝ ██║██║██╔╝ ██╗███████║██║     ███████╗██║   ██║   ██║  ██║",
        "╚═╝     ╚═╝╚═╝╚═╝  ╚═╝╚══════╝╚═╝     ╚══════╝╚═╝   ╚═╝   ╚═╝  ╚═╝",
    ]
    subtitle = "Mix Archival Tool"
    content_height = len(logo_lines) + 2
    top_pad = max(0, (rows - content_height) // 2)

    os.system('cls' if os.name == 'nt' else 'clear')
    if top_pad:
        print("\n" * top_pad, end="")

    for line in logo_lines:
        print(line.center(cols))
    print(subtitle.center(cols))
    print(flush=True)


_show_dependency_splash()

# Ensure bundled ffmpeg/ffprobe are visible before importing pydub.
def _bootstrap_bundled_ffmpeg_on_path():
    ffmpeg_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

    candidate_dirs = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate_dirs.append(sys._MEIPASS)
    candidate_dirs.append(os.path.dirname(sys.executable))
    try:
        candidate_dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    candidate_dirs.append(os.getcwd())

    unique_dirs = []
    for path in candidate_dirs:
        if path and path not in unique_dirs:
            unique_dirs.append(path)

    found_ffmpeg = ""
    found_ffprobe = ""
    prepend_dirs = []
    for base_dir in unique_dirs:
        ffmpeg_path_candidate = os.path.join(base_dir, ffmpeg_name)
        ffprobe_path_candidate = os.path.join(base_dir, ffprobe_name)
        if os.path.exists(ffmpeg_path_candidate):
            found_ffmpeg = ffmpeg_path_candidate
            if base_dir not in prepend_dirs:
                prepend_dirs.append(base_dir)
        if os.path.exists(ffprobe_path_candidate):
            found_ffprobe = ffprobe_path_candidate
            if base_dir not in prepend_dirs:
                prepend_dirs.append(base_dir)

    if found_ffmpeg:
        os.environ["FFMPEG_BINARY"] = found_ffmpeg
    if found_ffprobe:
        os.environ["FFPROBE_BINARY"] = found_ffprobe

    if prepend_dirs:
        current_path = os.environ.get("PATH", "")
        path_parts = current_path.split(os.pathsep) if current_path else []
        merged = prepend_dirs + [p for p in path_parts if p and p not in prepend_dirs]
        os.environ["PATH"] = os.pathsep.join(merged)


_bootstrap_bundled_ffmpeg_on_path()

# =============================================================================
# THIRD-PARTY IMPORTS - Must be at top level for PyInstaller to detect
# =============================================================================
def _exit_missing_module(module_name: str):
    py_cmd = "py -3" if sys.platform == "win32" else "python3"
    print(f"\n[ERROR] Missing required Python module: {module_name}")
    print("This happened while running the raw .py file.")
    print(f"Python interpreter: {sys.executable}")
    print("Install it into the Python environment used to launch MixSplitR:")
    print(f"  \"{sys.executable}\" -m pip install {module_name}")
    print(f"  {py_cmd} -m pip install {module_name}")
    print("\nFor end users, launch the packaged executable instead (dist/MixSplitR.exe).")
    raise SystemExit(1)


try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence, detect_silence
    from pydub.exceptions import CouldntDecodeError
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] == "pydub":
        _exit_missing_module("pydub")
    raise

try:
    from tqdm import tqdm
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] == "tqdm":
        _exit_missing_module("tqdm")
    raise

# These may not be installed - import with fallback
try:
    from acrcloud.recognizer import ACRCloudRecognizer
    ACRCLOUD_AVAILABLE = True
except ImportError:
    ACRCLOUD_AVAILABLE = False
    ACRCloudRecognizer = None

try:
    import acoustid
    import musicbrainzngs
    ACOUSTID_AVAILABLE = True
except ImportError:
    ACOUSTID_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# =============================================================================
# IMPORT MODULES
# =============================================================================

from mixsplitr_core import (
    CURRENT_VERSION, GITLAB_REPO, Style,
    AUDIO_EXTENSIONS_GLOB, AUDIO_EXTENSIONS,
    check_for_updates, RateLimiter, resource_path,
    ffmpeg_path, ffprobe_path, setup_ffmpeg, get_audio_duration_fast,
    analyze_files_parallel, close_terminal, get_config, save_config, get_config_path,
    get_cache_path, get_app_data_dir, validate_acrcloud_credentials, get_output_directory, is_cd_rip_menu_enabled,
    get_runtime_temp_directory,
    # Mode helpers (v8.0)
    MODE_ACRCLOUD, MODE_MB_ONLY, MODE_MANUAL, MODE_DUAL, MODE_SPLIT_ONLY, MODE_AUTO_TRACKLIST, get_mode,
    # Large file handling
    LARGE_FILE_THRESHOLD, is_large_file, get_file_size_str,
    DEFAULT_SPLIT_SILENCE_THRESH_DB, DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS,
    get_split_silence_threshold_db, get_split_silence_seek_step_ms,
    DEFAULT_DUPLICATE_POLICY, normalize_duplicate_policy,
    build_essentia_config_snapshot,
    ffmpeg_detect_silence, ffmpeg_get_split_points_from_silence,
    ffmpeg_split_file, ffmpeg_extract_chunk_for_identification
)

from mixsplitr_manifest import (
    list_manifests, load_manifest, compare_manifests,
    export_manifest_for_session, rollback_from_manifest, get_manifest_dir
)

from mixsplitr_metadata import (
    find_art_in_json, get_backup_art, get_all_external_metadata,
    set_lastfm_key
)

from mixsplitr_audio import detect_bpm_librosa

from mixsplitr_identify import (
    identify_with_acoustid, identify_with_shazam, get_enhanced_metadata,
    merge_identification_results, batch_download_artwork,
    is_acoustid_available, is_shazam_available, setup_musicbrainz, identify_dual_mode,
    musicbrainz_search_recordings, set_acoustid_api_key, get_acoustid_api_key,
    check_chromaprint_available, is_trace_enabled, print_id_winner
)

from mixsplitr_tagging import embed_and_sort_flac, embed_and_sort_alac, embed_and_sort_generic, AUDIO_FORMATS
from mixsplitr_artist_normalization import apply_smart_folder_canonicalization

from mixsplitr_memory import (
    scan_existing_library, get_available_ram_gb, create_file_batches,
    is_psutil_available, recalculate_batch_size, check_memory_pressure,
    warn_if_no_psutil
)

from mixsplitr_editor import (
    save_preview_cache, load_preview_cache, interactive_editor,
    display_preview_table
)

# New prompt_toolkit based menus
from mixsplitr_menus import (
    show_main_menu, show_api_keys_menu, show_mode_switch_menu,
    show_preview_type_menu, show_split_mode_menu,
    show_post_process_menu, show_manifest_menu, show_format_selection_menu,
    show_file_selection_menu, show_exit_menu_with_cache
)
from mixsplitr_menu import (
    MenuItem, select_menu, confirm_dialog, input_dialog, wait_for_enter,
    clear_screen as menu_clear_screen, PROMPT_TOOLKIT_AVAILABLE
)

# Split-out modules (v8.0 refactor)
from mixsplitr_processing import (
    process_single_track,
    process_single_track_manual,
    process_single_track_split_only,
    process_single_track_mb_only,
    process_single_track_dual
)
from mixsplitr_pipeline import (
    process_large_file_streaming,
    apply_from_cache
)
from mixsplitr_session import manage_manifests
from mixsplitr_autotracklist import (
    generate_auto_tracklist_for_file,
    generate_tracklist_from_start_times,
)

try:
    from mixsplitr_essentia import get_runtime_status as get_essentia_runtime_status
except Exception:
    get_essentia_runtime_status = None

# Setup ffmpeg paths and configure pydub
# FIXED: Properly get the returned paths from setup_ffmpeg()
ffmpeg_path, ffprobe_path = ffmpeg_path, ffprobe_path = setup_ffmpeg()
AudioSegment.converter = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path
os.environ["FFMPEG_BINARY"] = ffmpeg_path
os.environ["FFPROBE_BINARY"] = ffprobe_path
try:
    import pydub.utils as pydub_utils

    def _mixsplitr_get_prober_name():
        if ffprobe_path and os.path.exists(ffprobe_path):
            return ffprobe_path
        fallback = pydub_utils.which("ffprobe") or pydub_utils.which("avprobe")
        return fallback or "ffprobe"

    pydub_utils.get_prober_name = _mixsplitr_get_prober_name
except Exception:
    pass

# Initialize optional ID backends (MusicBrainz/AcoustID/Shazam) independently
setup_musicbrainz(CURRENT_VERSION, GITLAB_REPO)

if not ACOUSTID_AVAILABLE:
    print("Note: acoustid/musicbrainzngs not found - MusicBrainz/AcoustID disabled")
    print("      Install with: pip install pyacoustid musicbrainzngs")

# If tracing, show whether Shazam is available (useful for EXE builds)
if os.environ.get("MIXSPLITR_TRACE_SHAZAM", "").strip().lower() in ("1", "true", "yes", "y", "on"):
    print(f"Note: Shazam backend is {'available' if is_shazam_available() else 'NOT available'}")
    # Config-level disable flag is applied later when config is loaded

# Check psutil availability (PSUTIL_AVAILABLE set at top of file)
if not PSUTIL_AVAILABLE:
    print("Note: psutil not found - will process files one at a time for safety")

# Visual splitter UI - optional module
SPLITTER_UI_AVAILABLE = False
try:
    from splitter_ui import get_split_points_visual, split_audio_at_points
    SPLITTER_UI_AVAILABLE = True
except ImportError:
    pass

# NOTE: USE_LOCAL_BPM, SHAZAM_ENABLED, SHOW_ID_SOURCE are now read from
# config at function-call time inside mixsplitr_processing.py.
# CLI --no-bpm-dsp flag is applied to config in main().


# =============================================================================
# SCREEN UTILITIES
# =============================================================================

def clear_screen():
    """Clear terminal screen to reduce clutter"""
    os.system('cls' if os.name == 'nt' else 'clear')


def _set_windows_console_size(cols: int, lines: int) -> bool:
    """
    Best-effort Windows console sizing using WinAPI.
    Returns True if API calls succeeded, False if unavailable/ignored.
    """
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        h_out = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if h_out in (0, -1):
            return False

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        cols = max(60, int(cols))
        lines = max(20, int(lines))

        # Ensure buffer is large enough before setting window dimensions.
        kernel32.SetConsoleScreenBufferSize(h_out, COORD(cols, max(lines, 300)))
        rect = SMALL_RECT(0, 0, cols - 1, lines - 1)
        ok = bool(kernel32.SetConsoleWindowInfo(h_out, ctypes.c_bool(True), ctypes.byref(rect)))
        # Trim buffer back down to match requested size where possible.
        kernel32.SetConsoleScreenBufferSize(h_out, COORD(cols, lines))
        return ok
    except Exception:
        return False


def _ensure_windows_console_host(cols: int, lines: int):
    """
    Ensure a predictable Windows console window size by relaunching once into
    a dedicated host. This avoids host-specific resize limitations.

    Disabled by default because relaunching causes a visible double-start.
    Set MIXSPLITR_FORCE_RELAUNCH=1 to opt in for troubleshooting.
    """
    if os.name != 'nt':
        return
    if os.environ.get("MIXSPLITR_FORCE_RELAUNCH", "").strip() != "1":
        return
    if os.environ.get("MIXSPLITR_CONHOST", "").strip() == "1":
        return

    try:
        args = subprocess.list2cmdline(sys.argv[1:])
        if getattr(sys, "frozen", False):
            target = f"\"{sys.executable}\""
        else:
            target = f"\"{sys.executable}\" \"{os.path.abspath(__file__)}\""

        run_cmd = f"{target} {args}".strip()
        inner = (
            f"set MIXSPLITR_CONHOST=1 && "
            f"mode con cols={int(cols)} lines={int(lines)} >nul 2>&1 && "
            f"{run_cmd}"
        )
        child_env = os.environ.copy()
        # Force a fresh onefile extraction in relaunched process.
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        # Clear inherited onefile temp-dir hints that can break child startup.
        child_env.pop("_MEIPASS2", None)
        child_env.pop("_PYI_APPLICATION_HOME_DIR", None)
        child_env.pop("_PYI_ARCHIVE_FILE", None)
        child_env.pop("_PYI_PARENT_PROCESS_LEVEL", None)

        # Preferred path: ask Windows Terminal to open a NEW window at target size.
        wt_bin = shutil.which("wt")
        if wt_bin:
            subprocess.Popen([
                wt_bin,
                "-w", "new",
                "--size", f"{int(cols)},{int(lines)}",
                "cmd", "/k", inner,
            ], env=child_env)
        else:
            # Fallback: separate console window via conhost/cmd.
            subprocess.Popen(
                ["cmd.exe", "/k", inner],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                env=child_env,
            )
        sys.exit(0)
    except Exception:
        # If relaunch fails, keep running in current console.
        return


def set_terminal_window_size(default_cols: int = 81, default_lines: int = 50):
    """
    Try to enforce a consistent terminal character grid across platforms.
    Optional override via MIXSPLITR_TERM_SIZE, e.g. "100x34".
    """
    cols, lines = default_cols, default_lines
    raw = os.environ.get("MIXSPLITR_TERM_SIZE", "").strip().lower()
    if raw and "x" in raw:
        try:
            parsed_cols, parsed_lines = raw.split("x", 1)
            cols = max(60, int(parsed_cols))
            lines = max(20, int(parsed_lines))
        except Exception:
            cols, lines = default_cols, default_lines

    try:
        if os.name == 'nt':
            # Try WinAPI sizing first (works for classic conhost in most cases).
            sized = _set_windows_console_size(cols, lines)
            if not sized:
                # Fallback for shells where WinAPI path is unavailable.
                os.system(f"mode con cols={cols} lines={lines} >nul 2>&1")
            # Also request xterm-style resize for hosts like Windows Terminal.
            try:
                sys.stdout.write(f"\033[8;{lines};{cols}t")
                sys.stdout.flush()
            except Exception:
                pass
        else:
            # xterm-compatible resize request (works in most modern terminals).
            sys.stdout.write(f"\033[8;{lines};{cols}t")
            sys.stdout.flush()
    except Exception:
        pass


# =============================================================================
# OPENING SCREEN
# =============================================================================

def show_opening_screen():
    """Display animated ASCII art opening screen"""
    clear_screen()
    import math
    import time

    term_size = shutil.get_terminal_size(fallback=(100, 24))
    term_cols = term_size.columns
    term_rows = term_size.lines

    def _center_text(text: str) -> str:
        text = text.rstrip("\n")
        if not text:
            return ""
        if len(text) >= term_cols:
            return text
        return text.center(term_cols)

    logo_segments = [
        ("███╗   ███╗██╗██╗  ██╗", "███████╗██████╗ ██╗     ██╗████████╗", "██████╗ "),
        ("████╗ ████║██║╚██╗██╔╝", "██╔════╝██╔══██╗██║     ██║╚══██╔══╝", "██╔══██╗"),
        ("██╔████╔██║██║ ╚███╔╝ ", "███████╗██████╔╝██║     ██║   ██║   ", "██████╔╝"),
        ("██║╚██╔╝██║██║ ██╔██╗ ", "╚════██║██╔═══╝ ██║     ██║   ██║   ", "██╔══██╗"),
        ("██║ ╚═╝ ██║██║██╔╝ ██╗", "███████║██║     ███████╗██║   ██║   ", "██║  ██║"),
        ("╚═╝     ╚═╝╚═╝╚═╝  ╚═╝", "╚══════╝╚═╝     ╚══════╝╚═╝   ╚═╝   ", "╚═╝  ╚═╝"),
    ]

    divider = "═══════════════════════════════════════"
    subtitle = "Mix Archival Tool"
    version = f"Version {CURRENT_VERSION}"
    feature_line = "Record • Split • Identify • Archive"
    mix_logo_color = Style.GRAY
    r_logo_color = '\033[38;5;196m'

    # Hide cursor during splash to avoid block artifacts.
    print("\033[?25l", end="", flush=True)
    try:
        splash_height = len(logo_segments) + 8
        top_pad = max(0, (term_rows - splash_height) // 2)
        if top_pad:
            print("\n" * top_pad, end="")

        for mix_part, split_part, r_part in logo_segments:
            plain_line = f"{mix_part}{split_part}{r_part}"
            pad = " " * max(0, (term_cols - len(plain_line)) // 2)
            print(
                f"{pad}{mix_logo_color}{mix_part}"
                f"{Style.GRAY}{split_part}"
                f"{r_logo_color}{r_part}{Style.RESET}"
            )
            time.sleep(0.04)

        print(f"{Style.GRAY}{_center_text(divider)}")
        print(_center_text(subtitle))
        print(f"{_center_text(divider)}{Style.RESET}\n")
        time.sleep(0.20)

        print(f"{Style.DIM}{_center_text(version)}")
        print(f"{_center_text(feature_line)}{Style.RESET}\n")
        time.sleep(0.20)

        glyphs = "▁▂▃▄▅▆▇█"
        wave_width = 14
        wave_freq = 0.65
        wave_speed = 0.50
        # Match marker color to the red "R" in MIXSPLITR.
        wave_marker_color = r_logo_color
        # Darker waveform body so red marker pops more.
        wave_base_color = '\033[38;5;238m'
        for phase in range(42):
            # Reflection pair that propagates outward from center.
            outward_levels = [
                int((math.sin((distance * wave_freq) - (phase * wave_speed)) + 1.0) * 3.5)
                for distance in range(wave_width)
            ]
            outward_wave = ''.join(glyphs[level] for level in outward_levels)
            left_wave = outward_wave[::-1]
            right_wave = outward_wave
            # Two red scan markers pulse outward from the center.
            # Marker travels outward with waveform flow.
            scan_pos = phase % wave_width
            right_scan_idx = int(scan_pos)
            left_scan_idx = wave_width - 1 - right_scan_idx

            left_colored = ''.join(
                f"{wave_marker_color}{char}" if idx == left_scan_idx else f"{wave_base_color}{char}"
                for idx, char in enumerate(left_wave)
            )
            right_colored = ''.join(
                f"{wave_marker_color}{char}" if idx == right_scan_idx else f"{wave_base_color}{char}"
                for idx, char in enumerate(right_wave)
            )

            wave_plain = f"{left_wave}  │  {right_wave}"
            wave_pad = " " * max(0, (term_cols - len(wave_plain)) // 2)
            wave_line = (
                f"{wave_pad}{left_colored}"
                f"{wave_base_color}  │  "
                f"{right_colored}{Style.RESET}"
            )
            # Clear whole line each frame so no ghost/stuck bar remains.
            print(f"\033[2K\r{wave_line}", end="", flush=True)
            time.sleep(0.06)

        print("\033[2K\r", end="", flush=True)
        print(f"{Style.CYAN}{_center_text('Loading...')}{Style.RESET}")
        time.sleep(0.45)
    finally:
        print("\033[?25h", end="", flush=True)


# =============================================================================
# FILE HELPERS
# =============================================================================

def is_audio_file(path):
    """Return True if *path* has a supported audio extension."""
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


@dataclass
class AppState:
    """Mutable runtime state for menu actions."""
    audio_files: list
    base_dir: str
    temp_folder: str
    config: dict
    current_mode: str
    update_info: Optional[dict] = None
    ui_notice: str = ""


def _build_mode_badge(current_mode: str, update_info=None) -> str:
    if current_mode == MODE_MANUAL:
        badge = "[Manual Search]"
    elif current_mode == MODE_SPLIT_ONLY:
        badge = "[Split Only]"
    elif current_mode == MODE_AUTO_TRACKLIST:
        badge = "[Timestamping]"
    elif current_mode == MODE_DUAL:
        badge = "[Dual Mode]"
    elif current_mode == MODE_MB_ONLY:
        badge = "[MusicBrainz]"
    else:
        badge = "[ACRCloud]"

    return badge


def _stage_timing_enabled(config=None) -> bool:
    env_value = os.environ.get("MIXSPLITR_STAGE_TIMING", "").strip().lower()
    if env_value:
        return env_value in ("1", "true", "yes", "y", "on")
    if isinstance(config, dict):
        return bool(config.get("show_stage_timing", True))
    return True


def _log_stage_timing(label: str, started_at: float, config=None, indent: str = "  "):
    if not _stage_timing_enabled(config):
        return
    elapsed = max(0.0, time.perf_counter() - float(started_at))
    print(f"{indent}{Style.DIM}[timing] {label}: {elapsed:.2f}s{Style.RESET}")


def _default_temp_folder() -> str:
    """Return the shared runtime temp folder for transient processing files."""
    try:
        return str(get_runtime_temp_directory("mixsplitr_temp"))
    except Exception:
        import tempfile
        fallback = os.path.join(tempfile.gettempdir(), "MixSplitR", "mixsplitr_temp")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _get_cached_track_count(cache_path: str) -> int:
    """Return the number of tracks in cache preview data."""
    if not os.path.exists(cache_path):
        return 0
    try:
        with open(cache_path, 'r') as f:
            cache_data = json.load(f)
        return len(cache_data.get('tracks') or [])
    except Exception:
        return 0


def _run_unsaved_preview_menu(cache_path: str, temp_folder: str,
                              cache_data: Optional[dict] = None,
                              show_preview_table: bool = True) -> tuple[bool, str]:
    """
    Show the Unsaved Preview action menu.
    Returns (did_apply, ui_notice).
    """
    if cache_data is None:
        cache_data = load_preview_cache(cache_path)
    if not cache_data:
        return False, "No unsaved preview data found."

    if show_preview_table:
        display_preview_table(cache_data)

    preview_items = [
        MenuItem(
            "apply_now", "✅", "Finish Unsaved Preview Now",
            "Export tracks from current preview data and save session history"
        ),
        MenuItem(
            "edit_preview", "✏️", "Edit Unsaved Preview Tracks",
            "Review/fix/identify before exporting (play audio, edit metadata)"
        ),
        MenuItem(
            "cancel", "❌", "Cancel",
            "Return to main menu without exporting"
        ),
    ]
    preview_choice = select_menu(
        "Unsaved Preview",
        preview_items,
        show_item_divider=True,
        wrap_selected_description=True,
    )
    if preview_choice.cancelled or preview_choice.key == "cancel":
        return False, ""

    if preview_choice.key == "apply_now":
        did_apply = apply_from_cache(cache_path, temp_folder)
        if did_apply:
            _clear_unsaved_preview_data(cache_path, temp_folder)
            wait_for_enter()
            return True, ""
        return False, "Export canceled. No files were processed."

    if preview_choice.key == "edit_preview":
        result = interactive_editor(cache_data, cache_path)
        if result == 'apply':
            did_apply = apply_from_cache(cache_path, temp_folder)
            if did_apply:
                _clear_unsaved_preview_data(cache_path, temp_folder)
                wait_for_enter()
                return True, ""
            return False, "Export canceled. No files were processed."
        return False, ""

    return False, ""


def _clear_unsaved_preview_data(cache_path: str, temp_folder: str) -> bool:
    """Remove unsaved preview cache/readable files and temp chunks."""
    removed_any = False
    for path in (cache_path, str(cache_path).replace('.json', '_readable.txt')):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed_any = True
        except Exception:
            pass

    try:
        if temp_folder and os.path.exists(temp_folder):
            shutil.rmtree(temp_folder)
            removed_any = True
    except Exception:
        pass

    return removed_any


def _collect_audio_files_from_directory(folder_path: str, deep_scan: bool = False) -> list:
    """Collect supported audio files from a directory."""
    found = []
    try:
        if deep_scan:
            for root, _, files in os.walk(folder_path):
                for filename in files:
                    path = os.path.join(root, filename)
                    if is_audio_file(path):
                        found.append(path)
        else:
            for entry in os.scandir(folder_path):
                if entry.is_file() and is_audio_file(entry.path):
                    found.append(entry.path)
    except Exception:
        return []
    return sorted(set(found))


def split_on_silence_with_loading_bar(recording: AudioSegment, min_silence_len: int = 2000,
                                      silence_thresh: int = DEFAULT_SPLIT_SILENCE_THRESH_DB, keep_silence: int = 200,
                                      progress_label: str = "     Splitting",
                                      merge_gap_ms: int = 0,
                                      seek_step: int = DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS):
    """Run split_on_silence while showing an animated loading bar for long mixes."""
    result = {}
    error = {}

    def _split_worker():
        try:
            result["chunks"] = split_on_silence(
                recording,
                min_silence_len=min_silence_len,
                silence_thresh=silence_thresh,
                keep_silence=keep_silence,
                seek_step=max(1, int(seek_step)),
            )
        except Exception as exc:
            error["exc"] = exc

    worker = threading.Thread(target=_split_worker, daemon=True)
    worker.start()

    # Keep UI responsive with monotonic progress (no 0->100 resets).
    duration_seconds = max(1.0, len(recording) / 1000.0)
    estimated_seconds = max(6.0, duration_seconds * 0.20)
    started_at = time.monotonic()
    last_creep = started_at

    with tqdm(total=100, desc=progress_label, ncols=60, leave=False) as pbar:
        while worker.is_alive():
            time.sleep(0.10)
            elapsed = time.monotonic() - started_at
            target = min(99, int((elapsed / estimated_seconds) * 100))

            # If operation exceeds estimate, keep a slow forward creep up to 99%.
            if target <= pbar.n and pbar.n < 99 and elapsed >= estimated_seconds:
                now = time.monotonic()
                if now - last_creep >= 1.2:
                    target = pbar.n + 1
                    last_creep = now

            if target > pbar.n:
                pbar.update(target - pbar.n)

        worker.join()

        # Complete to 100% once the worker actually finishes.
        if pbar.n < 100:
            pbar.update(100 - pbar.n)

    if "exc" in error:
        raise error["exc"]
    chunks = result.get("chunks", [])
    if merge_gap_ms > 0 and len(chunks) >= 3:
        chunks = _merge_close_split_chunks_from_original(
            recording=recording,
            chunks=chunks,
            min_gap_ms=int(merge_gap_ms),
        )
    return chunks


def _normalize_silence_ranges(silent_ranges):
    normalized = []
    for item in silent_ranges or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            start_ms = int(item[0])
            end_ms = int(item[1])
        except Exception:
            continue
        if end_ms < start_ms:
            start_ms, end_ms = end_ms, start_ms
        if end_ms <= start_ms:
            continue
        normalized.append((start_ms, end_ms))
    return sorted(normalized, key=lambda pair: (pair[0], pair[1]))


def _coalesce_silence_ranges(silent_ranges, merge_gap_ms=0):
    ranges = _normalize_silence_ranges(silent_ranges)
    if not ranges:
        return []
    if int(merge_gap_ms) <= 0:
        return ranges

    merged = []
    cur_start, cur_end = ranges[0]
    for start_ms, end_ms in ranges[1:]:
        if (start_ms - cur_end) <= int(merge_gap_ms):
            cur_end = max(cur_end, end_ms)
            continue
        merged.append((cur_start, cur_end))
        cur_start, cur_end = start_ms, end_ms
    merged.append((cur_start, cur_end))
    return merged


def _coalesce_split_points_ms(points_ms, min_gap_ms):
    points = sorted(set(int(p) for p in (points_ms or []) if int(p) > 0))
    if not points:
        return []
    if int(min_gap_ms) <= 0:
        return points

    merged_points = []
    cluster = [points[0]]
    for point in points[1:]:
        if (point - cluster[-1]) <= int(min_gap_ms):
            cluster.append(point)
        else:
            merged_points.append(int(round(sum(cluster) / len(cluster))))
            cluster = [point]
    merged_points.append(int(round(sum(cluster) / len(cluster))))
    return merged_points


def _split_audiosegment_at_points_ms(recording: AudioSegment, split_points_ms):
    if recording is None:
        return []
    total_ms = int(len(recording))
    points = sorted(set(
        int(p) for p in (split_points_ms or [])
        if 0 < int(p) < total_ms
    ))
    if not points:
        return [recording]

    chunks = []
    cursor = 0
    for point_ms in points:
        if point_ms <= cursor:
            continue
        chunk = recording[cursor:point_ms]
        if len(chunk) > 0:
            chunks.append(chunk)
        cursor = point_ms

    tail = recording[cursor:]
    if len(tail) > 0:
        chunks.append(tail)

    return chunks or [recording]


def _merge_close_split_chunks_from_original(recording: AudioSegment, chunks, min_gap_ms=0):
    if recording is None:
        return chunks or []
    if int(min_gap_ms) <= 0 or not chunks or len(chunks) < 3:
        return chunks or []

    points_ms = []
    elapsed = 0
    for chunk in chunks[:-1]:
        elapsed += int(len(chunk))
        if 0 < elapsed < int(len(recording)):
            points_ms.append(elapsed)

    merged_points = _coalesce_split_points_ms(points_ms, min_gap_ms=int(min_gap_ms))
    if len(merged_points) >= len(points_ms):
        return chunks
    return _split_audiosegment_at_points_ms(recording, merged_points)


def detect_silence_with_loading_bar(audio_file: str, min_silence_len: int = 2000,
                                    silence_thresh: int = DEFAULT_SPLIT_SILENCE_THRESH_DB,
                                    progress_label: str = "     Detecting silence",
                                    merge_gap_ms: int = 0,
                                    seek_step: int = DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS):
    """
    Decode audio + detect silent ranges while showing a responsive loading bar.

    Returns:
        (silent_ranges, duration_seconds)
    """
    result = {}
    error = {}

    # get_audio_duration_fast returns minutes when available.
    try:
        estimated_duration_seconds = max(1.0, float(get_audio_duration_fast(audio_file) or 0.0) * 60.0)
    except Exception:
        estimated_duration_seconds = 1.0
    estimated_seconds = max(6.0, estimated_duration_seconds * 0.25)

    def _detect_worker():
        try:
            rec = AudioSegment.from_file(audio_file)
            duration_seconds = max(1.0, len(rec) / 1000.0)
            silent_ranges = detect_silence(
                rec,
                min_silence_len=min_silence_len,
                silence_thresh=silence_thresh,
                seek_step=max(1, int(seek_step)),
            )
            silent_ranges = _coalesce_silence_ranges(silent_ranges, merge_gap_ms=merge_gap_ms)
            result["duration_seconds"] = duration_seconds
            result["silent_ranges"] = silent_ranges
            del rec
        except Exception as exc:
            error["exc"] = exc

    worker = threading.Thread(target=_detect_worker, daemon=True)
    worker.start()

    started_at = time.monotonic()
    last_creep = started_at

    with tqdm(total=100, desc=progress_label, ncols=60, leave=False) as pbar:
        while worker.is_alive():
            time.sleep(0.10)
            elapsed = time.monotonic() - started_at
            target = min(99, int((elapsed / estimated_seconds) * 100))
            if target <= pbar.n and pbar.n < 99 and elapsed >= estimated_seconds:
                now = time.monotonic()
                if now - last_creep >= 1.2:
                    target = pbar.n + 1
                    last_creep = now
            if target > pbar.n:
                pbar.update(target - pbar.n)

        worker.join()
        if pbar.n < 100:
            pbar.update(100 - pbar.n)

    if "exc" in error:
        raise error["exc"]

    return result.get("silent_ranges", []), float(result.get("duration_seconds", estimated_duration_seconds))


def _parse_timecode_to_seconds(token: str):
    token = str(token or "").strip()
    if not token:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", token)
    if not match:
        return None
    first = int(match.group(1))
    second = int(match.group(2))
    third = match.group(3)
    if second > 59:
        return None
    if third is None:
        # MM:SS
        return float(first * 60 + second)
    # HH:MM:SS
    third = int(third)
    if third > 59:
        return None
    return float(first * 3600 + second * 60 + third)


def _format_timecode_for_prompt(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _prompt_track_timecodes(audio_file: str, duration_seconds: float, existing_starts=None, allow_keep=False):
    filename = os.path.basename(audio_file)
    print(f"\n{Style.CYAN}{'─'*60}{Style.RESET}")
    print(f"  {Style.BOLD}Edit Track Timecodes{Style.RESET}: {filename}")
    print(f"{Style.CYAN}{'─'*60}{Style.RESET}")
    print("  Enter comma-separated start times in MM:SS or HH:MM:SS.")
    print(f"  Example: 00:00, 02:14, 05:58, 09:31")
    print("  Track 01 starts at 00:00 automatically if omitted.")
    print("  Type 'q' to cancel.")
    if duration_seconds > 0:
        print(f"  Duration: {_format_timecode_for_prompt(duration_seconds)}")

    if existing_starts:
        existing_text = ", ".join(_format_timecode_for_prompt(v) for v in existing_starts)
        print(f"  Current:  {existing_text}")
        if allow_keep:
            print("  Press Enter to keep current timecodes.")

    while True:
        entered = input("\n  Timecodes -> ").strip()
        if entered.lower() in ("q", "quit", "cancel"):
            return None
        if not entered:
            if allow_keep and existing_starts is not None:
                return list(existing_starts)
            print(f"  {Style.YELLOW}Please enter at least one timecode.{Style.RESET}")
            continue

        tokens = [tok.strip() for tok in entered.split(",") if tok.strip()]
        parsed = []
        invalid = []
        for token in tokens:
            seconds = _parse_timecode_to_seconds(token)
            if seconds is None:
                invalid.append(token)
            else:
                parsed.append(seconds)
        if invalid:
            print(f"  {Style.RED}Invalid timecode(s): {', '.join(invalid)}{Style.RESET}")
            continue

        if 0.0 not in parsed:
            parsed.append(0.0)
        parsed = sorted(set(round(v, 3) for v in parsed if v >= 0.0))

        if duration_seconds > 0:
            parsed = [v for v in parsed if v < float(duration_seconds)]
            if not parsed:
                parsed = [0.0]

        return parsed


def _normalize_user_path(user_input: str) -> str:
    """Normalize quoted/escaped path text from terminal input."""
    normalized = (user_input or "").strip()
    if not normalized:
        return ""
    if (normalized.startswith('"') and normalized.endswith('"')) or \
       (normalized.startswith("'") and normalized.endswith("'")):
        normalized = normalized[1:-1]
    # macOS terminal drag/drop often emits shell-escaped paths such as:
    # /Volumes/Foo\ Bar/Track\ \(demo\)\,\ v1.flac
    # Decode escaped variants, but do not split plain-space paths.
    if normalized.startswith(('/', '~')) and '\\' in normalized:
        try:
            parsed = shlex.split(normalized, posix=True)
            if len(parsed) == 1:
                normalized = parsed[0]
        except ValueError:
            # Fall through to targeted unescape below.
            pass

    # Unescape common shell-escaped punctuation without harming Windows paths.
    # This only unwraps backslashes before punctuation/space characters.
    normalized = re.sub(r'\\([ !"#$%&\'()*+,;<=>?@\[\]^`{|}~])', r'\1', normalized)
    return os.path.expanduser(normalized)


def _split_user_paths(raw_input: str) -> list[str]:
    """Split drag-drop or pasted text into one or more normalized paths."""
    raw = (raw_input or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    tokens: list[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Split only where whitespace is followed by a new absolute/home/drive path.
        # This handles drag-drop payloads even when spaces are not escaped.
        chunks = re.split(r"\s+(?=(?:/|~|[A-Za-z]:[\\/]|\\\\))", line)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            # If quoted bundles are present, use shlex just for that chunk.
            if '"' in chunk or "'" in chunk:
                try:
                    parsed = shlex.split(chunk, posix=(sys.platform != "win32"))
                    if parsed:
                        tokens.extend(parsed)
                        continue
                except ValueError:
                    pass
            tokens.append(chunk)

    normalized: list[str] = []
    seen = set()
    for token in tokens:
        path = _normalize_user_path(token)
        if path and path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


def _handle_main_menu_path_input(raw_input: str, state: AppState) -> None:
    """Handle drag-drop/typed path submitted directly in the main menu."""
    user_inputs = _split_user_paths(raw_input)
    if not user_inputs:
        print(f"  {Style.RED}✗{Style.RESET} Path not found")
        wait_for_enter()
        return

    deep_scan_on = bool(get_config().get('deep_scan', False))
    resolved_audio_files: list[str] = []
    missing_paths: list[str] = []
    unsupported_paths: list[str] = []

    for user_input in user_inputs:
        if not os.path.exists(user_input):
            missing_paths.append(user_input)
            continue

        if os.path.isfile(user_input):
            if is_audio_file(user_input):
                resolved_audio_files.append(user_input)
            else:
                unsupported_paths.append(user_input)
            continue

        if os.path.isdir(user_input):
            dir_files = _collect_audio_files_from_directory(user_input, deep_scan=deep_scan_on)
            if dir_files:
                resolved_audio_files.extend(dir_files)
            else:
                unsupported_paths.append(user_input)

    # De-duplicate while preserving order.
    deduped_audio_files: list[str] = []
    seen_files = set()
    for file_path in resolved_audio_files:
        abs_path = os.path.abspath(file_path)
        if abs_path not in seen_files:
            seen_files.add(abs_path)
            deduped_audio_files.append(abs_path)

    if not deduped_audio_files:
        if missing_paths:
            print(f"  {Style.RED}✗{Style.RESET} Path not found: {missing_paths[0]}")
        elif unsupported_paths:
            print(f"  {Style.RED}✗{Style.RESET} No supported audio files in: {unsupported_paths[0]}")
        else:
            print(f"  {Style.YELLOW}⚠️{Style.RESET}  No valid audio files found")
        wait_for_enter()
        return

    if len(deduped_audio_files) == 1:
        new_base_dir = os.path.dirname(deduped_audio_files[0]) or os.getcwd()
    else:
        try:
            common_dir = os.path.commonpath([os.path.dirname(p) for p in deduped_audio_files])
            new_base_dir = common_dir if os.path.isdir(common_dir) else (os.path.dirname(deduped_audio_files[0]) or os.getcwd())
        except Exception:
            new_base_dir = os.path.dirname(deduped_audio_files[0]) or os.getcwd()

    state.audio_files = deduped_audio_files
    state.base_dir = new_base_dir
    state.temp_folder = _default_temp_folder()


def _load_last_saved_recording(state: AppState) -> bool:
    """Load the most recently saved MixSplitR recording into state."""
    from pathlib import Path
    import datetime

    if sys.platform == "win32":
        recordings_dir = Path(os.environ.get("APPDATA", Path.home())) / "MixSplitR" / "recordings"
    else:
        recordings_dir = Path.home() / "Music"

    if not recordings_dir.exists():
        print(f"\n  {Style.YELLOW}⚠️  No recordings directory found{Style.RESET}")
        print(f"  Expected: {recordings_dir}")
        input("\n  Press Enter to continue...")
        return False

    recordings = list(recordings_dir.glob("MixSplitR_recording_*.wav"))
    if not recordings:
        print(f"\n  {Style.YELLOW}⚠️  No saved recordings found{Style.RESET}")
        print(f"  Looked in: {recordings_dir}")
        input("\n  Press Enter to continue...")
        return False

    last_recording = max(recordings, key=lambda p: p.stat().st_mtime)
    print(f"\n  📁 Found last recording:")
    print(f"  {Style.BOLD}{last_recording.name}{Style.RESET}")
    print(f"  Location: {last_recording.parent}")

    file_size = last_recording.stat().st_size / (1024 * 1024)
    mod_time = datetime.datetime.fromtimestamp(last_recording.stat().st_mtime)
    print(f"  Size: {file_size:.1f} MB")
    print(f"  Modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")

    confirm = input(f"\n  {Style.BOLD}Load this recording? (y/n) [y]:{Style.RESET} ").strip().lower()
    if confirm not in ('', 'y', 'yes'):
        return False

    state.audio_files = [str(last_recording)]
    state.base_dir = str(last_recording.parent)
    state.temp_folder = _default_temp_folder()
    print(f"  {Style.GREEN}✓ Loaded{Style.RESET}")
    return True


def _load_audio_paths_into_state(state: AppState, audio_paths: list[str]) -> bool:
    """Load a list of audio paths into the active app state."""
    if not audio_paths:
        return False

    cleaned = []
    seen = set()
    for path in audio_paths:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if abs_path in seen or not os.path.exists(abs_path):
            continue
        if not is_audio_file(abs_path):
            continue
        seen.add(abs_path)
        cleaned.append(abs_path)

    if not cleaned:
        return False

    if len(cleaned) == 1:
        base_dir = os.path.dirname(cleaned[0]) or os.getcwd()
    else:
        try:
            base_dir = os.path.commonpath([os.path.dirname(p) for p in cleaned])
            if not os.path.isdir(base_dir):
                base_dir = os.path.dirname(cleaned[0]) or os.getcwd()
        except Exception:
            base_dir = os.path.dirname(cleaned[0]) or os.getcwd()

    state.audio_files = cleaned
    state.base_dir = base_dir
    state.temp_folder = _default_temp_folder()
    return True


def _handle_load_files_choice(state: AppState) -> None:
    """Interactive file/folder/recording chooser for the load-files action."""
    supported_formats = [ext.lstrip('.') for ext in AUDIO_EXTENSIONS]
    checked_location = state.base_dir

    while True:
        clear_screen()
        print(f"\n{Style.WHITE}{Style.BOLD}{'═'*60}{Style.RESET}")
        print(f"{Style.WHITE}{Style.BOLD}  📁 SELECT AUDIO FILES{Style.RESET}")
        print(f"{Style.WHITE}{Style.BOLD}{'═'*60}{Style.RESET}\n")
        print(f"  Current: {checked_location}\n")
        print(f"  Supported formats:\n  {', '.join([fmt.upper() for fmt in supported_formats])}\n")
        deep_scan_on = bool(get_config().get('deep_scan', False))
        print(f"{Style.BOLD}{Style.WHITE}  Options:{Style.RESET}")
        print(f"  • Drag and drop audio file(s) or folder onto this window")
        print(f"  • Enter or paste a path below")
        if deep_scan_on:
            print(f"  • {Style.GREEN}Deep Scan: ON{Style.RESET} {Style.DIM}(subfolders included — toggle in Settings){Style.RESET}")
        if sys.platform in ("win32", "darwin"):
            print(f"  • Type {Style.CYAN}R{Style.RESET} to record system audio")
            print(f"  • Type {Style.CYAN}L{Style.RESET} to load {Style.BOLD}Last Saved Recording{Style.RESET}")
        cd_rip_enabled = is_cd_rip_menu_enabled(state.config or get_config())
        if cd_rip_enabled:
            print(f"  • Type {Style.CYAN}C{Style.RESET} to rip an audio CD")
        print(f"  • Press Enter with no input to cancel")

        user_input = input(f"  {Style.BOLD}Path:{Style.RESET} ").strip()
        if user_input == "":
            print("\n  Cancelled, returning to main menu...")
            return

        if sys.platform in ("win32", "darwin") and user_input.lower() == "r":
            try:
                from mixsplitr_record import record_system_audio_interactive
            except Exception:
                print("\n  Recording mode requires: pip install soundcard soundfile\n")
                input("Press Enter to continue...")
                continue

            rec_path = record_system_audio_interactive()
            if rec_path and os.path.exists(rec_path) and is_audio_file(rec_path):
                state.audio_files = [rec_path]
                state.base_dir = os.path.dirname(rec_path) or rec_path
                state.temp_folder = _default_temp_folder()
                print(f"  {Style.GREEN}✓ Ready to process{Style.RESET}")
                return
            continue

        if sys.platform in ("win32", "darwin") and user_input.lower() == "l":
            if _load_last_saved_recording(state):
                return
            continue

        if user_input.lower() == "c":
            if not cd_rip_enabled:
                print(f"\n  {Style.YELLOW}CD ripping menu is currently disabled.{Style.RESET}")
                input("Press Enter to continue...")
                continue
            try:
                from mixsplitr_cdrip import rip_cd_interactive
            except Exception as exc:
                print(f"\n  {Style.RED}❌ CD ripping module unavailable{Style.RESET}")
                print(f"  {Style.DIM}{exc}{Style.RESET}\n")
                input("Press Enter to continue...")
                continue

            ripped_paths = rip_cd_interactive(state.config or get_config())
            if ripped_paths and _load_audio_paths_into_state(state, ripped_paths):
                print(f"  {Style.GREEN}✓ Loaded {len(state.audio_files)} ripped track(s){Style.RESET}")
                return
            continue

        user_paths = _split_user_paths(user_input)
        if not user_paths:
            print(f"\n  ✗ Not found")
            input("\nPress Enter to try again...")
            continue

        load_deep = bool(get_config().get('deep_scan', False))
        resolved_audio_files: list[str] = []
        missing_paths: list[str] = []
        unsupported_paths: list[str] = []
        dirs_seen: list[str] = []

        for user_path in user_paths:
            if not os.path.exists(user_path):
                missing_paths.append(user_path)
                continue

            if os.path.isdir(user_path):
                dirs_seen.append(user_path)
                if load_deep:
                    print(f"  🔍 Deep scanning (recursive): {user_path}")
                found_files = _collect_audio_files_from_directory(user_path, deep_scan=load_deep)
                if found_files:
                    resolved_audio_files.extend(found_files)
                else:
                    unsupported_paths.append(user_path)
                continue

            if os.path.isfile(user_path):
                if is_audio_file(user_path):
                    resolved_audio_files.append(user_path)
                else:
                    unsupported_paths.append(user_path)
                continue

            missing_paths.append(user_path)

        deduped_audio_files: list[str] = []
        seen_files = set()
        for file_path in resolved_audio_files:
            abs_path = os.path.abspath(file_path)
            if abs_path not in seen_files:
                seen_files.add(abs_path)
                deduped_audio_files.append(abs_path)

        if deduped_audio_files:
            if len(deduped_audio_files) == 1:
                new_base_dir = os.path.dirname(deduped_audio_files[0]) or os.getcwd()
            else:
                try:
                    common_dir = os.path.commonpath([os.path.dirname(p) for p in deduped_audio_files])
                    new_base_dir = common_dir if os.path.isdir(common_dir) else (os.path.dirname(deduped_audio_files[0]) or os.getcwd())
                except Exception:
                    new_base_dir = os.path.dirname(deduped_audio_files[0]) or os.getcwd()

            state.audio_files = deduped_audio_files
            state.base_dir = new_base_dir
            state.temp_folder = _default_temp_folder()

            if dirs_seen and load_deep:
                folders = set(os.path.dirname(f) for f in deduped_audio_files)
                print(f"  {Style.GREEN}✓ Found {len(deduped_audio_files)} audio file(s) in {len(folders)} folder(s){Style.RESET}")
            else:
                print(f"  {Style.GREEN}✓ Found {len(deduped_audio_files)} audio file(s){Style.RESET}")
            return

        if missing_paths:
            print(f"\n  ✗ Not found: {missing_paths[0]}")
        elif unsupported_paths:
            bad = unsupported_paths[0]
            if os.path.isdir(bad):
                if load_deep:
                    print(f"\n  {Style.YELLOW}⚠️  No audio files found in folder or subfolders{Style.RESET}")
                else:
                    print(f"\n  {Style.YELLOW}⚠️  No audio files in folder{Style.RESET}")
                    print(f"  {Style.DIM}Tip: Enable Auto Deep Scan in Settings to search subfolders{Style.RESET}")
            else:
                print(f"\n  ✗ Not a supported audio file: {bad}")
        else:
            print(f"\n  {Style.YELLOW}⚠️  No valid audio files found{Style.RESET}")
        input("\nPress Enter to try again...")


def _resolve_processing_choice(choice: str, state: AppState) -> Optional[bool]:
    """
    Validate processing action choice.
    Returns preview_mode (bool) when processing should start, else None.
    """
    if choice not in ("preview", "direct"):
        return None

    if not state.audio_files:
        print(f"\n  {Style.YELLOW}⚠️  No audio files loaded!{Style.RESET}")
        print(f"  Please load audio files first.\n")
        input("Press Enter to continue...")
        return None

    if state.current_mode == MODE_MANUAL:
        print(f"\n{Style.YELLOW}{'═'*60}")
        print(f"  ⚠️  Manual Search Only Mode")
        print(f"{'═'*60}{Style.RESET}")
        print(f"\n  No fingerprinting keys configured.")
        print(f"  Use 'Manage API Keys' to add AcoustID (free) or ACRCloud.\n")
        if not confirm_dialog("Continue with manual search only?", default=False):
            return None
    elif state.current_mode == MODE_SPLIT_ONLY:
        print(f"\n{Style.YELLOW}{'═'*60}")
        print(f"  ⚠️  Split-only (No ID) Mode")
        print(f"{'═'*60}{Style.RESET}")
        print(f"\n  All ID/metadata lookup is disabled in this mode.")
        print(f"  Tracks will be sequentially named Track 01, Track 02, ...\n")
        if not confirm_dialog("Continue with split-only naming?", default=True):
            return None
    elif state.current_mode == MODE_AUTO_TRACKLIST:
        print(f"\n{Style.CYAN}{'═'*60}")
        print(f"  🧾 Timestamping Mode")
        print(f"{'═'*60}{Style.RESET}")
        print(f"\n  This mode scans overlapping windows and builds a timestamp list.")
        print(f"  It always exports a fallback timeline (Track 01, Track 02, ...) if IDs miss.\n")
        if not confirm_dialog("Continue with timestamping scan?", default=True):
            return None

    return choice == "preview"


def _save_direct_mode_session_record(
    all_results: list,
    output_files: list,
    current_mode: str,
    direct_output_format: str,
    config: dict,
    session_split_data: dict,
):
    """Persist a Session History manifest for one-click/direct exports."""
    if not output_files:
        return None

    direct_input_files = set()
    for result in all_results:
        if result.get('original_file'):
            direct_input_files.add(result['original_file'])

    direct_pipeline = {}
    if session_split_data:
        methods = list(set(sd.get('method', '?') for sd in session_split_data.values()))
        all_points = {}
        for fpath, split_data in session_split_data.items():
            all_points[fpath] = {
                'method': split_data.get('method'),
                'points_sec': split_data.get('points_sec'),
                'num_segments': split_data.get('num_segments'),
                'params': split_data.get('params', {}),
            }
        direct_pipeline = {'split_methods': methods, 'per_file': all_points}

    try:
        sample_seconds = int(config.get('fingerprint_sample_seconds', 12))
    except Exception:
        sample_seconds = 12
    sample_seconds = max(8, min(45, sample_seconds))
    probe_mode = str(config.get('fingerprint_probe_mode', 'single')).strip().lower()
    if probe_mode not in ('single', 'multi3'):
        probe_mode = 'single'

    direct_config = {
        'identification_mode': current_mode,
        'output_format': direct_output_format,
        'shazam_enabled': not bool(config.get('disable_shazam', False)),
        'use_local_bpm': not bool(config.get('disable_local_bpm', False)),
        'show_id_source': bool(config.get('show_id_source', True)),
        'fingerprint_sample_seconds': sample_seconds,
        'fingerprint_probe_mode': probe_mode,
    }
    runtime_status = None
    if callable(get_essentia_runtime_status):
        try:
            runtime_status = get_essentia_runtime_status()
        except Exception:
            runtime_status = None
    direct_config.update(build_essentia_config_snapshot(config, runtime_status=runtime_status))

    direct_input = list(direct_input_files)[0] if direct_input_files else "unknown"
    return export_manifest_for_session(
        input_file=direct_input,
        output_files=output_files,
        tracks=all_results,
        mode=current_mode,
        pipeline=direct_pipeline,
        config_snapshot=direct_config,
        input_files=list(direct_input_files) if direct_input_files else None
    )


def _essentia_runtime_from_scan_settings(scan_settings):
    settings = scan_settings if isinstance(scan_settings, dict) else {}
    return {
        "available": bool(settings.get("essentia_available", False)),
        "reason": str(settings.get("essentia_runtime_reason", "") or "").strip(),
        "python_executable": str(settings.get("essentia_python_executable", "") or "").strip(),
        "numpy_available": bool(settings.get("essentia_numpy_available", False)),
        "numpy_import_error": str(settings.get("essentia_numpy_import_error", "") or "").strip(),
        "essentia_version": str(settings.get("essentia_version", "") or "").strip(),
        "essentia_import_error": str(settings.get("essentia_import_error", "") or "").strip(),
    }


def _handle_main_menu_utility_choice(choice: str, state: AppState, cache_path: str) -> str:
    """
    Handle utility menu actions that do not enter processing flow.
    Returns: 'unhandled', 'handled', or 'exit_app'
    """
    if choice == "exit":
        track_count = _get_cached_track_count(cache_path)
        if track_count > 0:
            exit_choice = show_exit_menu_with_cache(track_count)
            if exit_choice == "cancel":
                return "handled"
            if exit_choice == "clear_exit":
                if _clear_unsaved_preview_data(cache_path, state.temp_folder):
                    print(f"  {Style.GREEN}✓ Unsaved preview data cleared{Style.RESET}")
        return "exit_app"

    if choice == "record" and sys.platform in ("win32", "darwin"):
        try:
            from mixsplitr_record import record_system_audio_interactive
        except Exception:
            print(f"\n  {Style.RED}❌ Recording mode not available{Style.RESET}")
            print(f"  Install dependencies: pip install soundcard soundfile\n")
            wait_for_enter()
            return "handled"

        try:
            rec_path = record_system_audio_interactive()
        except KeyboardInterrupt:
            print(f"\n  {Style.YELLOW}↩ Recording canceled. Returning to menu.{Style.RESET}")
            return "handled"
        if rec_path and os.path.exists(rec_path) and is_audio_file(rec_path):
            _load_audio_paths_into_state(state, [rec_path])
            print(f"  {Style.GREEN}✓ Recording loaded - ready to process{Style.RESET}\n")
        return "handled"

    if choice == "rip_cd":
        if not is_cd_rip_menu_enabled(state.config or get_config()):
            print(f"\n  {Style.YELLOW}CD ripping menu is currently disabled.{Style.RESET}")
            wait_for_enter()
            return "handled"
        try:
            from mixsplitr_cdrip import rip_cd_interactive
        except Exception as exc:
            print(f"\n  {Style.RED}❌ CD ripping mode unavailable{Style.RESET}")
            print(f"  {Style.DIM}{exc}{Style.RESET}\n")
            wait_for_enter()
            return "handled"

        ripped_paths = rip_cd_interactive(state.config or get_config())
        if ripped_paths and _load_audio_paths_into_state(state, ripped_paths):
            print(f"  {Style.GREEN}✓ Ripped tracks loaded - ready to process{Style.RESET}\n")
        return "handled"

    if choice == "manifest":
        manage_manifests()
        return "handled"

    if choice == "delete_cache":
        if _clear_unsaved_preview_data(cache_path, state.temp_folder):
            print(f"{Style.GREEN}✅ Unsaved preview data cleared{Style.RESET}")
        else:
            print(f"{Style.DIM}No unsaved preview data found.{Style.RESET}")
        wait_for_enter()
        return "handled"

    if choice == "api_keys":
        while not show_api_keys_menu():
            pass
        state.config = get_config()
        state.current_mode = state.config.get('mode', MODE_ACRCLOUD)
        return "handled"

    if choice == "apply_cache":
        did_apply, notice = _run_unsaved_preview_menu(
            cache_path=cache_path,
            temp_folder=state.temp_folder,
            show_preview_table=True,
        )
        state.ui_notice = "" if did_apply else notice
        return "handled"

    return "unhandled"


# =============================================================================
# TRACK PROCESSING → moved to mixsplitr_processing.py
# LARGE FILE / CACHE → moved to mixsplitr_pipeline.py
# MANIFEST BROWSER  → moved to mixsplitr_session.py
# =============================================================================



# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='MixSplitR - Mix Archival Tool')
    parser.add_argument('--no-bpm-dsp', action='store_true')
    parser.add_argument('--no-update-check', action='store_true')
    args = parser.parse_args()

    if args.no_bpm_dsp:
        # Persist to config so mixsplitr_processing reads it at function-call time
        _cfg = get_config()
        _cfg['disable_local_bpm'] = True
        save_config(_cfg)

    # Optional Windows relaunch path (opt-in via MIXSPLITR_FORCE_RELAUNCH=1).
    _ensure_windows_console_host(81, 50)

    # Keep startup window proportions predictable across Mac/Windows builds.
    default_cols = 87 if sys.platform == "darwin" else 81
    set_terminal_window_size(default_cols=default_cols, default_lines=50)
    
    # Show animated opening screen
    show_opening_screen()
    
    update_info = None
    if not args.no_update_check:
        update_info = check_for_updates()
        if isinstance(update_info, dict):
            print(
                f"  {Style.GREEN}🆕 Update available!{Style.RESET} "
                f"{Style.BOLD}v{update_info['latest']}{Style.RESET} "
                f"{Style.DIM}(current: v{update_info['current']}){Style.RESET}"
            )
            if update_info.get("url"):
                print(f"  {Style.DIM}Download: {update_info['url']}{Style.RESET}\n")

    # Persistent state across loop iterations (preserved by drag-drop / load)
    audio_files = []
    base_dir = ""
    temp_folder = _default_temp_folder()
    ui_notice = ""

    # =========================================================================
    # MAIN LOOP – "Cancel" in preview restarts here instead of closing
    # =========================================================================
    while True:
        config = get_config()

        # =========================================================================
        # STEP 1: Find audio files first (before showing menu)
        # Only auto-scan if no files were already loaded (e.g. from drag-drop)
        # =========================================================================

        if not audio_files:
            portable_startup_scan = bool(config.get('portable_mode_local_scan', False))

            if portable_startup_scan:
                # Determine local startup scan directory
                if getattr(sys, 'frozen', False):
                    exe_dir = os.path.dirname(sys.executable)

                    # For macOS .app bundle, check local folder first for portable use
                    if '.app/Contents' in exe_dir and sys.platform == 'darwin':
                        app_bundle_dir = exe_dir
                        while app_bundle_dir and not app_bundle_dir.endswith('.app'):
                            app_bundle_dir = os.path.dirname(app_bundle_dir)
                        app_parent_dir = os.path.dirname(app_bundle_dir) if app_bundle_dir else exe_dir

                        # Check if there are audio files in the app's parent directory (portable mode)
                        local_audio_files = []
                        for ext in AUDIO_EXTENSIONS_GLOB:
                            local_audio_files.extend(glob.glob(os.path.join(app_parent_dir, ext)))

                        if local_audio_files:
                            base_dir = app_parent_dir
                            print(f"  {Style.GREEN}📂 Found {len(local_audio_files)} audio file(s) next to app{Style.RESET}\n")
                        else:
                            base_dir = os.path.expanduser('~/Music')
                    else:
                        base_dir = exe_dir
                else:
                    base_dir = os.path.dirname(os.path.abspath(__file__))
            else:
                # Non-portable startup mode avoids scanning the local download/app folder.
                base_dir = os.path.expanduser('~/Music')

            # Scan for audio files
            for ext in AUDIO_EXTENSIONS_GLOB:
                audio_files.extend(glob.glob(os.path.join(base_dir, ext)))

            # Show initial file scan result if files were found
            if audio_files:
                print(f"  {Style.GREEN}✓ Found {len(audio_files)} audio file(s){Style.RESET}")
    
        # =========================================================================
        # STEP 2: Validate API credentials (mode-aware)
        # =========================================================================

        # Globals removed — processing functions now read config directly
        if config.get('lastfm_api_key'):
            set_lastfm_key(config['lastfm_api_key'])
        if config.get('acoustid_api_key'):
            set_acoustid_api_key(config['acoustid_api_key'])
    
        current_mode = get_mode(config)
    
        if current_mode == MODE_ACRCLOUD:
            # ── ACRCloud mode: SDK must be present and creds must be valid ──────
            if not ACRCLOUD_AVAILABLE:
                print(f"\n  {Style.YELLOW}⚠️  ACRCloud SDK not available in this build{Style.RESET}")
                print(f"  {Style.DIM}Automatically switching to MusicBrainz-only mode...{Style.RESET}")
                config['mode'] = MODE_MB_ONLY
                save_config(config)
                current_mode = MODE_MB_ONLY
                # Fall through to MusicBrainz setup below
            else:
                # ACRCloud SDK is available, proceed with validation
                while True:
                    print(f"  🔑 Validating ACRCloud credentials...", end='', flush=True)
                    is_valid, error_msg = validate_acrcloud_credentials(config)
                
                    if is_valid:
                        print(f" {Style.GREEN}✓{Style.RESET}")
                        break
                    else:
                        print(f" {Style.RED}✗{Style.RESET}")
                        print(f"\n  {Style.RED}ACRCloud API Error:{Style.RESET} {error_msg}")
                        fix_menu = [
                            MenuItem("new_credentials", "📝", "Enter new credentials"),
                            MenuItem("switch_mb_only", "🔄", "Switch to MusicBrainz-only mode"),
                            MenuItem("exit_app", "🚪", "Exit"),
                        ]
                        fix_result = select_menu("ACRCloud Error Recovery", fix_menu)
                        fix_choice = fix_result.key if not fix_result.cancelled else "exit_app"

                        if fix_choice == "new_credentials":
                            print(f"\n  {Style.BOLD}Enter ACRCloud credentials:{Style.RESET}")
                            print(f"  {Style.DIM}(Get these from https://console.acrcloud.com){Style.RESET}\n")
                        
                            new_host   = input(f"  ACR Host (e.g., identify-us-west-2.acrcloud.com): ").strip()
                            new_key    = input(f"  Access Key: ").strip()
                            new_secret = input(f"  Access Secret: ").strip()
                        
                            if new_host and new_key and new_secret:
                                config['host']          = new_host
                                config['access_key']    = new_key
                                config['access_secret'] = new_secret
                                config['timeout']       = 10
                                save_config(config)
                                print(f"\n  {Style.GREEN}💾 Credentials saved!{Style.RESET}")
                                print(f"  Retrying validation...\n")
                            else:
                                print(f"\n  {Style.YELLOW}⚠ All fields are required{Style.RESET}")
                        elif fix_choice == "switch_mb_only":
                            config['mode'] = MODE_MB_ONLY
                            save_config(config)
                            current_mode = MODE_MB_ONLY
                            print(f"\n  {Style.GREEN}✅ Switched to MusicBrainz-only mode{Style.RESET}")
                            break          # exit validation loop – no ACRCloud check needed
                        else:
                            return close_terminal()
        
        if current_mode == MODE_MB_ONLY:
            # ── MusicBrainz-only mode ──────────────────────────────────────────
            if not ACOUSTID_AVAILABLE:
                print(f"\n  {Style.RED}❌ AcoustID / MusicBrainz libraries not found!{Style.RESET}")
                print(f"  {Style.DIM}Install with: pip install pyacoustid musicbrainzngs{Style.RESET}")
                return close_terminal()
            
            # Check for chromaprint/fpcalc
            has_chromaprint, fpcalc_path = check_chromaprint_available()
            if not has_chromaprint:
                print(f"\n  {Style.YELLOW}⚠️  Warning: chromaprint/fpcalc not found!{Style.RESET}")
                print(f"  {Style.DIM}AcoustID fingerprinting will not work without it.{Style.RESET}")
                print(f"\n  {Style.BOLD}Install chromaprint:{Style.RESET}")
                print(f"   • Windows: Download from https://acoustid.org/chromaprint")
                print(f"   • macOS:   brew install chromaprint")
                print(f"   • Linux:   apt install libchromaprint-tools")
                print(f"\n  {Style.DIM}Without chromaprint, tracks will be marked as unidentified.{Style.RESET}")
                print(f"  {Style.DIM}You can then use the interactive editor to manually identify them.{Style.RESET}\n")
                cont = input(f"  Continue anyway? (y/n) [n]: ").strip().lower()
                if cont != 'y':
                    return close_terminal()
            else:
                print(f"  {Style.CYAN}🔍 MusicBrainz-only mode{Style.RESET} – AcoustID fingerprinting enabled ✓")
                if fpcalc_path:
                    print(f"  {Style.DIM}   chromaprint: {fpcalc_path}{Style.RESET}")
        elif current_mode == MODE_AUTO_TRACKLIST:
            print(f"  {Style.CYAN}🧾 Timestamping mode{Style.RESET} – timeline scan + timestamp export")
            print(f"  {Style.DIM}   Uses ACRCloud/Shazam/AcoustID when available, with Track N fallback.{Style.RESET}")
    
        # =========================================================================
        # STEP 3: Show main menu (now that we have files and valid credentials)
        # =========================================================================
    
        # Cache + runtime temp artifacts go in app-managed safe locations
        cache_path  = get_cache_path("mixsplitr_cache.json")
        temp_folder = _default_temp_folder()
    
        # Main menu loop
        while True:
            menu_state = AppState(
                audio_files=audio_files,
                base_dir=base_dir,
                temp_folder=temp_folder,
                config=config,
                current_mode=current_mode,
                update_info=update_info,
                ui_notice=ui_notice,
            )
            mode_badge = _build_mode_badge(menu_state.current_mode, menu_state.update_info)
            cached_track_count = _get_cached_track_count(cache_path)

            # Show interactive menu (prompt_toolkit or fallback)
            menu_result = show_main_menu(
                menu_state.audio_files, menu_state.base_dir, menu_state.config, mode_badge,
                has_cached_preview=(cached_track_count > 0),
                update_info=menu_state.update_info,
                ui_notice=menu_state.ui_notice,
            )
            # Show notices once so the menu stays clean.
            ui_notice = ""

            # Handle path input (drag-drop or typed path)
            if menu_result.key == "__path__":
                _handle_main_menu_path_input(menu_result.text_input, menu_state)
                audio_files = menu_state.audio_files
                base_dir = menu_state.base_dir
                temp_folder = menu_state.temp_folder
                continue

            # Handle cancelled/empty selection
            if menu_result.cancelled or not menu_result.key:
                continue

            choice = menu_result.key
            utility_result = _handle_main_menu_utility_choice(choice, menu_state, cache_path)
            if utility_result == "exit_app":
                return close_terminal()
            if utility_result == "handled":
                audio_files = menu_state.audio_files
                base_dir = menu_state.base_dir
                temp_folder = menu_state.temp_folder
                config = menu_state.config
                current_mode = menu_state.current_mode
                ui_notice = menu_state.ui_notice
                continue

            if choice == "load_files":
                _handle_load_files_choice(menu_state)
                audio_files = menu_state.audio_files
                base_dir = menu_state.base_dir
                temp_folder = menu_state.temp_folder
                continue  # Back to main menu after changing directory

            # Processing modes (only available when files are loaded)
            processing_preview_mode = _resolve_processing_choice(choice, menu_state)
            if processing_preview_mode is None:
                continue

            audio_files = menu_state.audio_files
            base_dir = menu_state.base_dir
            temp_folder = menu_state.temp_folder
            config = menu_state.config
            current_mode = menu_state.current_mode
            preview_mode = processing_preview_mode
            break  # Exit menu loop to process files
    
        # File analysis (files already found earlier)
        print(f"\n{Style.CYAN}{'─'*60}{Style.RESET}")
        print(f"\n  Analyzing {len(audio_files)} file(s)...")
        analysis_started = time.perf_counter()
        file_analysis = analyze_files_parallel(audio_files)
        _log_stage_timing("Analyze files", analysis_started, config=config)
        file_info = {f['file']: f for f in file_analysis}
        mixes = [f for f in file_analysis if f['is_mix']]
    
        light_preview = False
        if preview_mode:
            if current_mode == MODE_SPLIT_ONLY:
                # Split-only preview has no metadata-ID phase to optimize, so we
                # always use full preview cache/edit behavior.
                light_preview = False
                print(f"   {Style.DIM}→ Split-only mode uses Full Preview by default{Style.RESET}")
            elif current_mode == MODE_AUTO_TRACKLIST:
                # Auto-tracklist has no chunk-cache/export split workflow, so
                # preview type selection is not applicable.
                light_preview = False
                print(f"   {Style.DIM}→ Timestamping mode skips preview type selection{Style.RESET}")
            else:
                # Use new prompt_toolkit menu for preview type
                light_preview = show_preview_type_menu()
                if light_preview is None:
                    print(f"   {Style.YELLOW}→ Preview selection canceled. Returning to main menu.{Style.RESET}")
                    ui_notice = "Preview selection canceled."
                    continue
                print(f"   → {'Light' if light_preview else 'Full'} preview selected")

        use_visual = False
        use_assisted = False
        split_mode = 'silence'
        if mixes:
            if current_mode == MODE_AUTO_TRACKLIST:
                print(f"\n  Found {Style.BOLD}{len(mixes)} mix(es){Style.RESET} to generate timestamps for.\n")
            else:
                print(f"\n  Found {Style.BOLD}{len(mixes)} mix(es){Style.RESET} to split.\n")
            # One-click direct mode should remain truly one-click:
            # skip split-mode prompts and use automatic splitting.
            if preview_mode and (SPLITTER_UI_AVAILABLE or current_mode == MODE_AUTO_TRACKLIST):
                split_menu_context = "auto_tracklist" if current_mode == MODE_AUTO_TRACKLIST else "split"
                split_mode = show_split_mode_menu(context=split_menu_context)
                if not split_mode:
                    if current_mode == MODE_AUTO_TRACKLIST:
                        print(f"   {Style.YELLOW}→ Timestamping timing mode selection canceled. Returning to main menu.{Style.RESET}")
                        ui_notice = "Timestamping timing mode selection canceled."
                    else:
                        print(f"   {Style.YELLOW}→ Split mode selection canceled. Returning to main menu.{Style.RESET}")
                        ui_notice = "Split mode selection canceled."
                    continue
                if current_mode == MODE_AUTO_TRACKLIST:
                    if split_mode == 'manual':
                        print(f"   {Style.GREEN}→ Manual timestamp entry enabled{Style.RESET}")
                    elif split_mode == 'assisted':
                        print(f"   {Style.GREEN}→ Assisted timestamp editing enabled{Style.RESET}")
                    else:
                        print(f"   {Style.DIM}→ Fast auto timeline scan{Style.RESET}")
                else:
                    if split_mode == 'manual':
                        use_visual = True
                        print(f"   {Style.GREEN}→ Will use visual editor{Style.RESET}")
                    elif split_mode == 'assisted':
                        use_assisted = True
                        print(f"   {Style.GREEN}→ Auto-detect + visual editor review{Style.RESET}")
            elif preview_mode and not SPLITTER_UI_AVAILABLE and current_mode != MODE_AUTO_TRACKLIST:
                print(f"   {Style.YELLOW}→ Visual splitter unavailable; using automatic splitting{Style.RESET}")
            else:
                print(f"   {Style.DIM}→ Direct Mode uses automatic splitting{Style.RESET}")
    
        output_folder = get_output_directory(config)
        print(f"  {Style.DIM}📂 Output: {output_folder}{Style.RESET}\n")
        try:
            split_boundary_merge_gap_ms = int(config.get('split_boundary_merge_gap_ms', 2500))
        except Exception:
            split_boundary_merge_gap_ms = 2500
        split_boundary_merge_gap_ms = max(0, min(12000, split_boundary_merge_gap_ms))
        split_silence_thresh_db = get_split_silence_threshold_db(config)
        split_silence_seek_step_ms = get_split_silence_seek_step_ms(config)

        if current_mode == MODE_AUTO_TRACKLIST:
            print(f"  {Style.CYAN}🧾 Building timestamping timeline(s)...{Style.RESET}")
            auto_dry_run = bool(config.get('auto_tracklist_dry_run', False))
            if auto_dry_run:
                print(f"  {Style.YELLOW}🧪 Timestamping dry-run is ON (no recognizer calls).{Style.RESET}")

            auto_recognizer = None
            if (
                ACRCLOUD_AVAILABLE
                and config.get('host')
                and config.get('access_key')
                and config.get('access_secret')
            ):
                try:
                    auto_recognizer = ACRCloudRecognizer(config)
                except Exception as exc:
                    print(f"  {Style.YELLOW}⚠️  ACRCloud unavailable for timestamping: {exc}{Style.RESET}")
            elif not ACRCLOUD_AVAILABLE:
                print(f"  {Style.DIM}  ACRCloud SDK unavailable; using Shazam/AcoustID when possible.{Style.RESET}")

            auto_results = []
            auto_manifest_tracks = []
            auto_output_files = []
            shazam_auto_disable_notice_shown = False
            auto_mode_started = time.perf_counter()

            for file_idx, audio_file in enumerate(audio_files, 1):
                filename = os.path.basename(audio_file)
                print(f"\n  [{file_idx}/{len(audio_files)}] {filename}")
                auto_file_started = time.perf_counter()
                try:
                    if split_mode == 'manual':
                        if SPLITTER_UI_AVAILABLE:
                            visual_points = get_split_points_visual(audio_file)
                            if visual_points is None:
                                print(f"  {Style.YELLOW}↩ Visual boundary edit canceled for this file{Style.RESET}")
                                continue
                            manual_starts = sorted(set([0.0] + [float(p) for p in visual_points if float(p) > 0.0]))
                        else:
                            duration_minutes = get_audio_duration_fast(audio_file) or 0.0
                            duration_seconds = float(duration_minutes) * 60.0
                            manual_starts = _prompt_track_timecodes(
                                audio_file=audio_file,
                                duration_seconds=duration_seconds,
                                existing_starts=[0.0],
                                allow_keep=False,
                            )
                            if manual_starts is None:
                                print(f"  {Style.YELLOW}↩ Timecode entry canceled for this file{Style.RESET}")
                                continue
                        auto_result = generate_tracklist_from_start_times(
                            audio_file=audio_file,
                            output_folder=output_folder,
                            start_times=manual_starts,
                            config=config,
                            template_segments=None,
                            dry_run=auto_dry_run,
                        )
                    elif split_mode == 'assisted':
                        auto_result = generate_auto_tracklist_for_file(
                            audio_file=audio_file,
                            output_folder=output_folder,
                            config=config,
                            recognizer=auto_recognizer,
                            show_progress=True,
                            dry_run=auto_dry_run,
                        )
                        if SPLITTER_UI_AVAILABLE:
                            existing_points = []
                            for seg in (auto_result.get('segments') or [])[1:]:
                                try:
                                    start_val = float(seg.get('start_sec', 0.0))
                                except Exception:
                                    continue
                                if start_val > 0.0:
                                    existing_points.append(start_val)
                            existing_points = sorted(set(existing_points))
                            edited_points = get_split_points_visual(
                                audio_file,
                                existing_points=existing_points,
                            )
                            if edited_points is None:
                                print(f"  {Style.DIM}→ Keeping auto-generated boundaries (edit canceled){Style.RESET}")
                            else:
                                edited_starts = sorted(set([0.0] + [float(p) for p in edited_points if float(p) > 0.0]))
                                auto_result = generate_tracklist_from_start_times(
                                    audio_file=audio_file,
                                    output_folder=output_folder,
                                    start_times=edited_starts,
                                    config=config,
                                    template_segments=auto_result.get('segments') or [],
                                    dry_run=auto_dry_run,
                                )
                        else:
                            existing_starts = [
                                float(seg.get('start_sec', 0.0))
                                for seg in (auto_result.get('segments') or [])
                            ]
                            duration_seconds = 0.0
                            if auto_result.get('segments'):
                                duration_seconds = float(auto_result['segments'][-1].get('end_sec', 0.0))
                            edited_starts = _prompt_track_timecodes(
                                audio_file=audio_file,
                                duration_seconds=duration_seconds,
                                existing_starts=existing_starts,
                                allow_keep=True,
                            )
                            if edited_starts is None:
                                print(f"  {Style.DIM}→ Keeping auto-generated timecodes (edit canceled){Style.RESET}")
                            else:
                                old_norm = [round(float(v), 3) for v in existing_starts]
                                new_norm = [round(float(v), 3) for v in edited_starts]
                                if new_norm != old_norm:
                                    auto_result = generate_tracklist_from_start_times(
                                        audio_file=audio_file,
                                        output_folder=output_folder,
                                        start_times=edited_starts,
                                        config=config,
                                        template_segments=auto_result.get('segments') or [],
                                        dry_run=auto_dry_run,
                                    )
                    else:
                        auto_result = generate_auto_tracklist_for_file(
                            audio_file=audio_file,
                            output_folder=output_folder,
                            config=config,
                            recognizer=auto_recognizer,
                            show_progress=True,
                            dry_run=auto_dry_run,
                        )
                except Exception as exc:
                    print(f"  {Style.RED}❌ Timestamping failed: {exc}{Style.RESET}")
                    continue

                summary = auto_result.get('summary', {}) or {}
                scan_settings = auto_result.get('scan_settings', {}) or {}
                if (
                    not shazam_auto_disable_notice_shown
                    and bool(scan_settings.get('shazam_auto_disabled', False))
                ):
                    if bool(scan_settings.get('shazam_auto_disabled_by_window_cap', False)):
                        window_cap = int(scan_settings.get('shazam_auto_disable_window_cap', 80))
                        estimated_windows = int(scan_settings.get('estimated_windows', window_cap))
                        print(
                            f"  {Style.YELLOW}ℹ️  Timestamping auto-disabled Shazam{Style.RESET} "
                            f"{Style.DIM}(estimated windows {estimated_windows} > cap {window_cap}) to reduce timeout risk.{Style.RESET}"
                        )
                    else:
                        threshold_sec = int(scan_settings.get('shazam_auto_disable_step_threshold_seconds', 10))
                        step_sec = int(scan_settings.get('step_seconds', threshold_sec))
                        print(
                            f"  {Style.YELLOW}ℹ️  Timestamping auto-disabled Shazam{Style.RESET} "
                            f"{Style.DIM}(scan step {step_sec}s < {threshold_sec}s threshold) to reduce timeout risk.{Style.RESET}"
                        )
                    shazam_auto_disable_notice_shown = True
                injected_boundaries = int(scan_settings.get('silence_injected_boundaries', 0) or 0)
                if injected_boundaries > 0:
                    print(
                        f"  {Style.DIM}↪ Added {injected_boundaries} silence-injected boundar{'y' if injected_boundaries == 1 else 'ies'} "
                        f"from strong silence anchors "
                        f"to catch missed transitions.{Style.RESET}"
                    )
                persistence_rewrites = int(scan_settings.get('persistence_rewritten_windows', 0) or 0)
                if persistence_rewrites > 0:
                    print(
                        f"  {Style.DIM}↪ Debounced {persistence_rewrites} short ID window"
                        f"{'' if persistence_rewrites == 1 else 's'} using persistence filtering.{Style.RESET}"
                    )
                ess_requested = bool(scan_settings.get('essentia_requested', True))
                ess_available = bool(scan_settings.get('essentia_available', False))
                ess_points = int(scan_settings.get('essentia_points_detected', 0) or 0)
                ess_version = str(scan_settings.get('essentia_version', '') or '').strip()
                ess_reason = str(scan_settings.get('essentia_runtime_reason', '') or '').strip()
                ess_python = str(scan_settings.get('essentia_python_executable', '') or '').strip()
                if ess_requested and ess_available:
                    print(
                        f"  {Style.DIM}↪ Essentia ON: {ess_points} boundary candidate"
                        f"{'' if ess_points == 1 else 's'} detected for transition scoring.{Style.RESET}"
                    )
                    if ess_version:
                        print(f"  {Style.DIM}↪ Essentia version: {ess_version}{Style.RESET}")
                    if bool(scan_settings.get('essentia_relaxation_applied', False)):
                        relax_passes = int(scan_settings.get('essentia_relaxation_passes', 0) or 0)
                        raw_onsets = int(scan_settings.get('essentia_raw_onset_count', 0) or 0)
                        fallback_points = int(scan_settings.get('essentia_peak_fallback_count', 0) or 0)
                        print(
                            f"  {Style.DIM}↪ Essentia auto-relaxed ({relax_passes} pass"
                            f"{'' if relax_passes == 1 else 'es'}): raw onsets {raw_onsets}, "
                            f"fallback peaks {fallback_points}.{Style.RESET}"
                        )
                elif ess_requested and (not ess_available):
                    print(
                        f"  {Style.DIM}↪ Essentia requested but unavailable in this build/runtime; "
                        f"using fallback boundary DSP.{Style.RESET}"
                    )
                    if ess_reason:
                        print(f"  {Style.DIM}↪ Essentia reason: {ess_reason}{Style.RESET}")
                    if ess_python:
                        print(f"  {Style.DIM}↪ Python runtime: {ess_python}{Style.RESET}")
                else:
                    print(
                        f"  {Style.DIM}↪ Essentia disabled by config; "
                        f"using fallback boundary DSP.{Style.RESET}"
                    )
                micro_refined = int(scan_settings.get('micro_refined_boundaries', 0) or 0)
                if micro_refined > 0:
                    avg_conf = float(scan_settings.get('micro_refine_average_confidence', 0.0) or 0.0)
                    ess_assisted = int(scan_settings.get('micro_refine_essentia_assisted_boundaries', 0) or 0)
                    print(
                        f"  {Style.DIM}↪ Micro-refined {micro_refined} transition boundar"
                        f"{'y' if micro_refined == 1 else 'ies'} "
                        f"with local ID/energy analysis (avg conf {avg_conf:.2f}, Essentia-assisted {ess_assisted}).{Style.RESET}"
                    )
                micro_refine_rejected = int(scan_settings.get('micro_refine_rejected_low_confidence', 0) or 0)
                if micro_refine_rejected > 0:
                    print(
                        f"  {Style.DIM}↪ Skipped {micro_refine_rejected} low-confidence boundary shift"
                        f"{'' if micro_refine_rejected == 1 else 's'} after novelty confidence gating.{Style.RESET}"
                    )
                print(
                    f"  {Style.GREEN}✓{Style.RESET} "
                    f"{summary.get('segments', 0)} segment(s) • "
                    f"{summary.get('identified', 0)} identified • "
                    f"{summary.get('fallback_labeled', 0)} fallback"
                )
                print(f"    {Style.DIM}timestamps: {auto_result.get('timestamp_file')}{Style.RESET}")
                _log_stage_timing(f"Timestamping ({filename})", auto_file_started, config=config, indent="    ")

                file_tracks = auto_result.get('manifest_tracks', []) or []
                for track in file_tracks:
                    track['file_num'] = file_idx
                auto_manifest_tracks.extend(file_tracks)
                auto_output_files.extend(auto_result.get('output_files', []) or [])
                auto_results.append(auto_result)

            if not auto_results:
                _log_stage_timing("Timestamping total", auto_mode_started, config=config)
                print(f"\n{Style.YELLOW}⚠️  No timestamp outputs were generated.{Style.RESET}")
                input(f"\n📍 Press Enter to return to main menu...")
                continue

            auto_output_files = list(dict.fromkeys(auto_output_files))

            try:
                runtime_status = None
                if auto_results:
                    runtime_status = _essentia_runtime_from_scan_settings(
                        (auto_results[0].get("scan_settings", {}) or {})
                    )
                essentia_snapshot = build_essentia_config_snapshot(
                    config,
                    runtime_status=runtime_status,
                )
                auto_manifest_path = export_manifest_for_session(
                    input_file=audio_files[0] if len(audio_files) == 1 else "auto_tracklist_batch",
                    output_files=auto_output_files,
                    tracks=auto_manifest_tracks,
                    mode=current_mode,
                    pipeline={
                        'method': 'auto_tracklist_window_scan',
                        'num_inputs': len(audio_files),
                    },
                    config_snapshot={
                        'identification_mode': current_mode,
                        'auto_tracklist_window_seconds': int(config.get('auto_tracklist_window_seconds', 18)),
                        'auto_tracklist_step_seconds': int(config.get('auto_tracklist_step_seconds', 12)),
                        'auto_tracklist_min_segment_seconds': int(config.get('auto_tracklist_min_segment_seconds', 30)),
                        'auto_tracklist_fallback_interval_seconds': int(config.get('auto_tracklist_fallback_interval_seconds', 180)),
                        'auto_tracklist_max_windows': int(config.get('auto_tracklist_max_windows', 120)),
                        'auto_tracklist_shazam_timeout_seconds': int(config.get('auto_tracklist_shazam_timeout_seconds', 10)),
                        'auto_tracklist_shazam_max_windows': int(config.get('auto_tracklist_shazam_max_windows', 120)),
                        'auto_tracklist_min_confidence': float(config.get('auto_tracklist_min_confidence', 0.58)),
                        'auto_tracklist_boundary_backtrack_seconds': float(config.get('auto_tracklist_boundary_backtrack_seconds', 0.0)),
                        'auto_tracklist_short_circuit_confidence': float(config.get('auto_tracklist_short_circuit_confidence', 0.90)),
                        'auto_tracklist_silence_first': bool(config.get('auto_tracklist_silence_first', False)),
                        'auto_tracklist_silence_min_ms': int(config.get('auto_tracklist_silence_min_ms', 1600)),
                        'auto_tracklist_silence_thresh_db': int(config.get('auto_tracklist_silence_thresh_db', -42)),
                        'auto_tracklist_silence_max_segment_seconds': int(config.get('auto_tracklist_silence_max_segment_seconds', 360)),
                        'auto_tracklist_silence_first_min_anchors': int(config.get('auto_tracklist_silence_first_min_anchors', 5)),
                        'auto_tracklist_persistence_windows': int(config.get('auto_tracklist_persistence_windows', 2)),
                        'auto_tracklist_micro_refine_enabled': bool(config.get('auto_tracklist_micro_refine_enabled', True)),
                        'auto_tracklist_micro_refine_backtrack_seconds': float(config.get('auto_tracklist_micro_refine_backtrack_seconds', 8.0)),
                        'auto_tracklist_micro_refine_forward_seconds': float(config.get('auto_tracklist_micro_refine_forward_seconds', 3.0)),
                        'auto_tracklist_micro_refine_step_seconds': float(config.get('auto_tracklist_micro_refine_step_seconds', 0.5)),
                        'auto_tracklist_micro_refine_min_confidence': float(config.get('auto_tracklist_micro_refine_min_confidence', 0.46)),
                        'auto_tracklist_essentia_enabled': bool(config.get('auto_tracklist_essentia_enabled', True)),
                        'auto_tracklist_essentia_min_confidence': float(config.get('auto_tracklist_essentia_min_confidence', 0.36)),
                        'auto_tracklist_essentia_max_points': int(config.get('auto_tracklist_essentia_max_points', 2400)),
                        'auto_tracklist_no_identify': bool(config.get('auto_tracklist_no_identify', False)),
                        'auto_tracklist_dry_run': auto_dry_run,
                        'shazam_enabled': not bool(config.get('disable_shazam', False)),
                        **essentia_snapshot,
                    },
                    input_files=audio_files,
                )
                if auto_manifest_path:
                    print(f"\n  📋 Session record saved (manifest): {os.path.basename(auto_manifest_path)}")
            except Exception as exc:
                print(f"\n  {Style.YELLOW}⚠️  Could not save timestamping session record: {exc}{Style.RESET}")

            total_segments = sum((res.get('summary', {}) or {}).get('segments', 0) for res in auto_results)
            total_identified = sum((res.get('summary', {}) or {}).get('identified', 0) for res in auto_results)
            print(
                f"\n{Style.GREEN}✅ Timestamping complete!{Style.RESET} "
                f"{Style.BOLD}{total_segments}{Style.RESET} timeline segment(s), "
                f"{Style.BOLD}{total_identified}{Style.RESET} identified."
            )
            _log_stage_timing("Timestamping total", auto_mode_started, config=config)
            print(f"  Timestamp files saved under: {Style.DIM}{os.path.join(output_folder, 'Tracklists')}{Style.RESET}")

            input(f"\n📍 Press Enter to return to main menu...")
            continue

        # Recognizer only needed in ACRCloud mode
        recognizer = None
        if current_mode == MODE_ACRCLOUD:
            recognizer = ACRCloudRecognizer(config)
    
        existing_tracks = scan_existing_library(output_folder)
        # Warn once if psutil is missing and the library is large
        warn_if_no_psutil(file_count=len(audio_files), threshold=50)

        all_results = []
        artwork_cache_global = {}
        decode_failures = []
        decode_failure_paths = set()
        file_index = {f: i+1 for i, f in enumerate(audio_files)}
        _session_split_data = {}  # {audio_file: {method, points_sec, params}} for manifest
        processing_started = time.perf_counter()
        # Ensure temp_folder exists early (needed for chunk-to-disk caching)
        os.makedirs(temp_folder, exist_ok=True)

        def _is_decode_failure(exc: Exception) -> bool:
            if isinstance(exc, CouldntDecodeError):
                return True
            text = f"{type(exc).__name__}: {exc}".lower()
            markers = (
                "couldntdecodeerror",
                "decoding failed",
                "invalid data found when processing input",
                "error opening input",
                "moov atom not found",
                "riff header",
            )
            return any(marker in text for marker in markers)

        def _skip_bad_audio_file(path: str, exc: Exception):
            message = str(exc).strip() or type(exc).__name__
            raw_path = str(path or "").strip()
            normalized = os.path.abspath(raw_path) if raw_path else ""
            key = normalized or raw_path or "<unknown>"
            if key in decode_failure_paths:
                return
            decode_failure_paths.add(key)
            decode_failures.append((key, message))
            print(
                f"\n  {Style.YELLOW}⚠ Skipping unreadable audio file:{Style.RESET} "
                f"{os.path.basename(key) or key}"
            )
            print(f"    {Style.DIM}{message}{Style.RESET}")

        # --- Dynamic batching: re-check RAM before each batch ---
        remaining_files = list(audio_files)
        batch_num = 0
        while remaining_files:
            batch_num += 1
            # Dynamically size this batch based on current RAM pressure
            dynamic_size = recalculate_batch_size(remaining_files, max_batch_size=15)
            batch_files = remaining_files[:dynamic_size]
            remaining_files = remaining_files[dynamic_size:]
            total_batches_est = batch_num + max(0, -(-len(remaining_files) // max(1, dynamic_size)))

            batch_started = time.perf_counter()
            print(f"\n{Style.BLUE}{'─'*50}")
            print(f"  {Style.BOLD}📦 Batch {batch_num}/{total_batches_est}{Style.RESET}{Style.BLUE} ({len(batch_files)} file{'s' if len(batch_files) > 1 else ''})")
            pressure = check_memory_pressure()
            if pressure in ("high", "critical"):
                print(f"  {Style.YELLOW}⚠ Memory pressure: {pressure} — batch size reduced to {len(batch_files)}{Style.RESET}")
            print(f"{'─'*50}{Style.RESET}")
        
            all_chunks = []
            prep_started = time.perf_counter()
            with tqdm(
                total=len(batch_files),
                desc=f"  Preparing batch {batch_num}",
                unit="file",
                ncols=72,
                leave=False,
            ) as load_bar:
                for file_idx, audio_file in enumerate(batch_files, 1):
                    fnum = file_index[audio_file]
                    info = file_info.get(audio_file, {})
                    filename = os.path.basename(audio_file)
                    short_name = (filename[:36] + "...") if len(filename) > 39 else filename
                    load_bar.set_postfix_str(short_name)

                    # Check if this is a large file that needs streaming mode
                    if is_large_file(audio_file):
                        # Use FFmpeg streaming mode for large files
                        try:
                            large_chunks = process_large_file_streaming(
                                audio_file, fnum, output_folder, temp_folder,
                                use_visual=use_visual,
                                use_assisted=use_assisted,
                                preview_mode=preview_mode,
                                split_silence_thresh_db=split_silence_thresh_db,
                            )
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                _skip_bad_audio_file(audio_file, exc)
                                load_bar.update(1)
                                continue
                            raise
                        all_chunks.extend(large_chunks)
                        # Reconstruct split points from chunk boundaries for manifest
                        if large_chunks:
                            _lf_pts = sorted(set(
                                cd.get('large_file_start', 0) for cd in large_chunks
                                if cd.get('large_file_start', 0) > 0
                            ))
                            _session_split_data[audio_file] = {
                                'method': 'large_file_streaming', 'points_sec': _lf_pts,
                                'params': {'silence_thresh_db': split_silence_thresh_db, 'min_silence_len_sec': 2.0},
                                'large_file_mode': True
                            }
                        load_bar.update(1)
                        continue

                    if info.get('is_mix'):
                        chunks = None
                        if use_visual:
                            # Pure manual mode - open visual editor with no pre-loaded points
                            pts = get_split_points_visual(audio_file)
                            if pts:
                                chunks = split_audio_at_points(audio_file, pts)
                                _session_split_data[audio_file] = {
                                    'method': 'visual', 'points_sec': sorted(pts), 'params': {}
                                }
                        elif use_assisted:
                            # Assisted mode - detect silence first, then let user review
                            load_bar.set_postfix_str(f"{short_name} (auto-detect)")
                            try:
                                silent_ranges, rec_duration_sec = detect_silence_with_loading_bar(
                                    audio_file,
                                    min_silence_len=2000,
                                    silence_thresh=split_silence_thresh_db,
                                    seek_step=split_silence_seek_step_ms,
                                    progress_label=f"     Detecting {file_idx}/{len(batch_files)}",
                                    merge_gap_ms=split_boundary_merge_gap_ms,
                                )
                            except Exception as exc:
                                if _is_decode_failure(exc):
                                    _skip_bad_audio_file(audio_file, exc)
                                    load_bar.update(1)
                                    continue
                                raise

                            # Convert silent ranges to split points (middle of each silence)
                            pre_detected = []
                            for start_ms, end_ms in silent_ranges:
                                mid_point = (start_ms + end_ms) / 2 / 1000  # Convert to seconds
                                # Skip points too close to start or end
                                if rec_duration_sec > 10:
                                    if not (mid_point > 5 and mid_point < rec_duration_sec - 5):
                                        continue
                                elif mid_point <= 0:
                                    continue
                                pre_detected.append(mid_point)

                            # Open visual editor with pre-loaded points
                            pts = get_split_points_visual(audio_file, existing_points=pre_detected)
                            if pts:
                                chunks = split_audio_at_points(audio_file, pts)
                                _session_split_data[audio_file] = {
                                    'method': 'assisted', 'points_sec': sorted(pts),
                                    'params': {
                                        'silence_thresh_db': split_silence_thresh_db,
                                        'min_silence_len_sec': 2.0,
                                        'seek_step_ms': split_silence_seek_step_ms,
                                        'merge_gap_ms': split_boundary_merge_gap_ms,
                                    }
                                }

                        # Fallback to automatic if no chunks yet
                        if chunks is None:
                            try:
                                rec = AudioSegment.from_file(audio_file)
                            except Exception as exc:
                                if _is_decode_failure(exc):
                                    _skip_bad_audio_file(audio_file, exc)
                                    load_bar.update(1)
                                    continue
                                raise
                            chunks = split_on_silence_with_loading_bar(
                                rec,
                                min_silence_len=2000,
                                silence_thresh=split_silence_thresh_db,
                                keep_silence=200,
                                seek_step=split_silence_seek_step_ms,
                                progress_label=f"     Splitting {file_idx}/{len(batch_files)}",
                                merge_gap_ms=split_boundary_merge_gap_ms,
                            )
                            _session_split_data[audio_file] = {
                                'method': 'automatic', 'points_sec': None,
                                'num_segments': len(chunks),
                                'params': {
                                    'silence_thresh_db': split_silence_thresh_db,
                                    'min_silence_len_sec': 2.0,
                                    'keep_silence_ms': 200,
                                    'seek_step_ms': split_silence_seek_step_ms,
                                    'merge_gap_ms': split_boundary_merge_gap_ms,
                                }
                            }
                            del rec
                            load_bar.set_postfix_str(f"{short_name} -> {len(chunks)} tracks")
                        for idx, chunk in enumerate(chunks):
                            # Cache chunk to disk immediately to free RAM
                            tp = os.path.join(temp_folder, f"chunk_{fnum}_{idx}.flac")
                            if chunk.channels > 8:
                                chunk.export(tp, format="flac", parameters=["-ac", "2", "-compression_level", "0"])
                            else:
                                chunk.export(tp, format="flac", parameters=["-compression_level", "0"])
                            all_chunks.append({
                                'chunk': None, 'temp_chunk_path': tp,
                                'file_num': fnum, 'original_file': audio_file, 'split_index': idx
                            })
                        del chunks
                        gc.collect()
                    else:
                        try:
                            rec = AudioSegment.from_file(audio_file)
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                _skip_bad_audio_file(audio_file, exc)
                                load_bar.update(1)
                                continue
                            raise
                        # Cache single track to disk immediately to free RAM
                        tp = os.path.join(temp_folder, f"chunk_{fnum}_0.flac")
                        if rec.channels > 8:
                            rec.export(tp, format="flac", parameters=["-ac", "2", "-compression_level", "0"])
                        else:
                            rec.export(tp, format="flac", parameters=["-compression_level", "0"])
                        all_chunks.append({
                            'chunk': None, 'temp_chunk_path': tp,
                            'file_num': fnum, 'original_file': audio_file
                        })
                        del rec
                        gc.collect()
                        _session_split_data[audio_file] = {
                            'method': 'single_track', 'points_sec': [], 'params': {}
                        }

                    load_bar.update(1)
            print(f"  {Style.DIM}✓ Prepared {len(batch_files)} file(s) in batch{Style.RESET}")
            _log_stage_timing(f"Batch {batch_num} preparation", prep_started, config=config)
        
            # Chunks are already cached to disk during prep (above).
            # For full preview mode, just confirm paths are set (they already are).
            if preview_mode and not light_preview:
                cached_count = sum(1 for cd in all_chunks if cd.get('temp_chunk_path'))
                print(f"\n  💾 {cached_count} audio chunks cached to disk during prep ✓")
        
            # Identify tracks
            print(f"\n  🎵 Identifying {len(all_chunks)} tracks...")
            identify_started = time.perf_counter()
            lock = threading.Lock()
            results = []

            if current_mode == MODE_SPLIT_ONLY:
                # Split-only mode – no API calls or metadata lookup.
                print(f"  {Style.DIM}(Split-only mode - no ID lookups, sequential naming){Style.RESET}")
                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(
                            process_single_track_split_only,
                            cd, i, existing_tracks, output_folder, lock, preview_mode,
                            runtime_config=config
                        ): i for i, cd in enumerate(all_chunks)
                    }
                    for future in tqdm(as_completed(futures), total=len(all_chunks), desc="     Progress", ncols=60):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                idx = futures.get(future, -1)
                                src = all_chunks[idx].get('original_file') if 0 <= idx < len(all_chunks) else ""
                                _skip_bad_audio_file(src, exc)
                                continue
                            raise
            elif current_mode == MODE_MANUAL:
                # Manual mode – no fingerprinting, mark all as unidentified for manual entry
                print(f"  {Style.DIM}(Manual search mode - skipping fingerprinting){Style.RESET}")
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {executor.submit(process_single_track_manual, cd, i, existing_tracks, output_folder, lock, preview_mode): i for i, cd in enumerate(all_chunks)}
                    for future in tqdm(as_completed(futures), total=len(all_chunks), desc="     Progress", ncols=60):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                idx = futures.get(future, -1)
                                src = all_chunks[idx].get('original_file') if 0 <= idx < len(all_chunks) else ""
                                _skip_bad_audio_file(src, exc)
                                continue
                            raise
            elif current_mode == MODE_ACRCLOUD:
                # ACRCloud mode – needs rate limiter for the external API
                rate_limiter = RateLimiter(min_interval=1.2)
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {
                        executor.submit(
                            process_single_track,
                            cd, i, recognizer, rate_limiter, existing_tracks,
                            output_folder, lock, preview_mode,
                            runtime_config=config
                        ): i for i, cd in enumerate(all_chunks)
                    }
                    for future in tqdm(as_completed(futures), total=len(all_chunks), desc="     Progress", ncols=60):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                idx = futures.get(future, -1)
                                src = all_chunks[idx].get('original_file') if 0 <= idx < len(all_chunks) else ""
                                _skip_bad_audio_file(src, exc)
                                continue
                            raise
            elif current_mode == MODE_DUAL:
                # Dual mode – run both ACRCloud AND AcoustID, pick best by confidence
                print(f"  {Style.GREEN}⚡ Dual mode: comparing ACRCloud + AcoustID{Style.RESET}")
                rate_limiter = RateLimiter(min_interval=1.2)  # ACRCloud rate limit
                with ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(
                            process_single_track_dual,
                            cd, i, recognizer, rate_limiter, existing_tracks,
                            output_folder, lock, preview_mode,
                            runtime_config=config
                        ): i for i, cd in enumerate(all_chunks)
                    }
                    for future in tqdm(as_completed(futures), total=len(all_chunks), desc="     Progress", ncols=60):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                idx = futures.get(future, -1)
                                src = all_chunks[idx].get('original_file') if 0 <= idx < len(all_chunks) else ""
                                _skip_bad_audio_file(src, exc)
                                continue
                            raise
            else:
                # MusicBrainz-only mode – no ACRCloud recognizer
                # AcoustID has a gentler rate limit; we still use a thread pool but
                # with a smaller interval enforced inside identify_with_acoustid.
                rate_limiter = RateLimiter(min_interval=0.5)   # soft throttle
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {
                        executor.submit(
                            process_single_track_mb_only,
                            cd, i, existing_tracks, output_folder, lock, preview_mode,
                            runtime_config=config
                        ): i for i, cd in enumerate(all_chunks)
                    }
                    for future in tqdm(as_completed(futures), total=len(all_chunks), desc="     Progress", ncols=60):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            if _is_decode_failure(exc):
                                idx = futures.get(future, -1)
                                src = all_chunks[idx].get('original_file') if 0 <= idx < len(all_chunks) else ""
                                _skip_bad_audio_file(src, exc)
                                continue
                            raise
            _log_stage_timing(f"Batch {batch_num} identification", identify_started, config=config)
        
            all_results.extend(results)
        
            # Show batch summary
            batch_identified = len([r for r in results if r['status'] == 'identified'])
            batch_unidentified = len([r for r in results if r['status'] == 'unidentified'])
            batch_skipped = len([r for r in results if r['status'] == 'skipped'])
            print(f"\n  ✅ {batch_identified} identified  ❓ {batch_unidentified} unidentified  ⏭️ {batch_skipped} skipped")
        
            identified = [r for r in results if r['status'] == 'identified']
            if identified:
                artwork_started = time.perf_counter()
                print(f"\n  🖼️  Downloading artwork...", end='', flush=True)
                urls = [r['art_url'] for r in identified if r.get('art_url')]
                if urls:
                    artwork_cache_global.update(batch_download_artwork(urls))
                print(f" ✓")
                _log_stage_timing(f"Batch {batch_num} artwork", artwork_started, config=config)
        
            del all_chunks
            gc.collect()
            _log_stage_timing(f"Batch {batch_num} total", batch_started, config=config)

        _log_stage_timing("All batches total", processing_started, config=config)

        if decode_failures:
            print(
                f"\n  {Style.YELLOW}⚠ Skipped {len(decode_failures)} unreadable file(s) due to decode errors.{Style.RESET}"
            )
            preview_items = decode_failures[:8]
            for bad_path, _bad_message in preview_items:
                print(f"    {Style.DIM}• {os.path.basename(bad_path)}{Style.RESET}")
            if len(decode_failures) > len(preview_items):
                remaining = len(decode_failures) - len(preview_items)
                print(f"    {Style.DIM}• ... and {remaining} more{Style.RESET}")

        if not all_results:
            print(f"\n{Style.YELLOW}⚠️  No decodable audio files were available to process.{Style.RESET}")
            input(f"\n📍 Press Enter to return to main menu...")
            continue
    
        if preview_mode:
            # Check if all tracks were skipped (nothing to process)
            total_skipped = len([r for r in all_results if r['status'] == 'skipped'])
            total_tracks = len(all_results)
            
            if total_tracks > 0 and total_skipped == total_tracks:
                # All tracks were skipped (already exist in library)
                print(f"\n{Style.YELLOW}⏭️  All tracks skipped!{Style.RESET}")
                print(f"  {Style.DIM}All {total_tracks} track(s) already exist in your library.{Style.RESET}")
                print(f"  {Style.DIM}No new tracks to process.{Style.RESET}")
                input(f"\n📍 Press Enter to return to main menu...")
                continue  # Back to main menu
            
            try:
                sample_seconds = int(config.get('fingerprint_sample_seconds', 12))
            except Exception:
                sample_seconds = 12
            sample_seconds = max(8, min(45, sample_seconds))
            probe_mode = str(config.get('fingerprint_probe_mode', 'single')).strip().lower()
            if probe_mode not in ('single', 'multi3'):
                probe_mode = 'single'
            runtime_status = None
            if callable(get_essentia_runtime_status):
                try:
                    runtime_status = get_essentia_runtime_status()
                except Exception:
                    runtime_status = None
            essentia_snapshot = build_essentia_config_snapshot(
                config,
                runtime_status=runtime_status,
            )

            cache_data = {
                'tracks': all_results, 'output_folder': output_folder,
                'artwork_cache': {}, 'light_preview': light_preview,
                'split_data': _session_split_data,
                'config_snapshot': {
                    'identification_mode': current_mode,
                    'shazam_enabled': not bool(config.get('disable_shazam', False)),
                    'use_local_bpm': not bool(config.get('disable_local_bpm', False)),
                    'show_id_source': bool(config.get('show_id_source', True)),
                    'fingerprint_sample_seconds': sample_seconds,
                    'fingerprint_probe_mode': probe_mode,
                    'split_boundary_merge_gap_ms': split_boundary_merge_gap_ms,
                    'essentia_genre_enrichment_enabled': bool(config.get('essentia_genre_enrichment_enabled', True)),
                    'essentia_genre_enrichment_when_missing_only': bool(config.get('essentia_genre_enrichment_when_missing_only', True)),
                    'essentia_genre_enrichment_min_confidence': float(config.get('essentia_genre_enrichment_min_confidence', 0.34)),
                    'essentia_genre_enrichment_max_tags': int(config.get('essentia_genre_enrichment_max_tags', 2)),
                    'essentia_genre_enrichment_analysis_seconds': int(config.get('essentia_genre_enrichment_analysis_seconds', 28)),
                    **essentia_snapshot,
                }
            }
            for url, data in artwork_cache_global.items():
                try:
                    cache_data['artwork_cache'][url] = base64.b64encode(data).decode('utf-8')
                except (ValueError, TypeError):
                    pass
        
            if save_preview_cache(cache_data, cache_path):
                _did_apply, ui_notice = _run_unsaved_preview_menu(
                    cache_path=cache_path,
                    temp_folder=temp_folder,
                    cache_data=cache_data,
                    show_preview_table=True,
                )
                continue
        else:
            # Direct mode - also check for all-skipped scenario
            total_skipped = len([r for r in all_results if r['status'] == 'skipped'])
            total_tracks = len(all_results)
            
            if total_tracks > 0 and total_skipped == total_tracks:
                # All tracks were skipped (already exist in library)
                print(f"\n{Style.YELLOW}⏭️  All tracks skipped!{Style.RESET}")
                print(f"  {Style.DIM}All {total_tracks} track(s) already exist in your library.{Style.RESET}")
                print(f"  {Style.DIM}No new tracks to process.{Style.RESET}")
                input(f"\n📍 Press Enter to return to main menu...")
                continue  # Back to main menu

            # Ask once at the end of one-click mode which format to export.
            direct_output_format = show_format_selection_menu()
            if not direct_output_format:
                print(f"  {Style.YELLOW}⚠️  Export cancelled. Returning to main menu.{Style.RESET}")
                ui_notice = "Export canceled. No files were processed."
                continue
            if direct_output_format not in AUDIO_FORMATS:
                print(f"  {Style.YELLOW}⚠️  Unknown format '{direct_output_format}', using FLAC{Style.RESET}")
                direct_output_format = "flac"

            direct_identified = [r for r in all_results if r['status'] == 'identified']
            try:
                apply_smart_folder_canonicalization(
                    all_results,
                    runtime_config=config,
                    debug_callback=lambda line: print(f"  {line}"),
                )
            except Exception:
                pass
            duplicate_policy = normalize_duplicate_policy(
                config.get("duplicate_policy", DEFAULT_DUPLICATE_POLICY),
                default=DEFAULT_DUPLICATE_POLICY,
            )
            if direct_identified:
                print(
                    f"\n  💾 Exporting {len(direct_identified)} track(s) as "
                    f"{Style.BOLD}{AUDIO_FORMATS[direct_output_format]['name']}{Style.RESET}..."
                )
            export_started = time.perf_counter()

            _direct_output_files = []
            direct_unidentified_saved = 0
            for idx, r in enumerate(direct_identified, 1):
                print(f"     [{idx}/{len(direct_identified)}] {r['artist'][:20]} - {r['title'][:25]}", end='\r')
                out_path = embed_and_sort_generic(
                    r['temp_flac'],
                    r['artist'],
                    r['title'],
                    r['album'],
                    r.get('art_url'),
                    output_folder,
                    output_format=direct_output_format,
                    artwork_cache=artwork_cache_global,
                    enhanced_metadata=r.get('enhanced_metadata', {}),
                    overwrite_existing=bool(duplicate_policy == "overwrite"),
                    duplicate_policy=duplicate_policy,
                    rename_preset=config.get("rename_preset", "simple"),
                )
                if out_path:
                    _direct_output_files.append(out_path)
            _log_stage_timing("Direct export", export_started, config=config)

            # Keep Session History complete even when a run yields only
            # unidentified tracks (no identified exports).
            for result in all_results:
                if result.get('status') != 'unidentified':
                    continue
                unidentified_path = result.get('unidentified_path')
                if unidentified_path and os.path.exists(unidentified_path):
                    _direct_output_files.append(unidentified_path)
                    direct_unidentified_saved += 1

            # Preserve insertion order while removing duplicates.
            _direct_output_files = list(dict.fromkeys(_direct_output_files))

            if direct_identified:
                print(f"     Saved {len(direct_identified)} identified tracks" + " " * 30)
            if direct_unidentified_saved:
                print(f"  📁 Kept {direct_unidentified_saved} unidentified track file(s)")
            
            id_count = len([r for r in all_results if r['status'] == 'identified'])
            if current_mode == MODE_SPLIT_ONLY:
                print(
                    f"\n{Style.GREEN}✅ Complete!{Style.RESET} "
                    f"{Style.BOLD}{id_count}{Style.RESET} tracks split and saved to "
                    f"{Style.DIM}{output_folder}{Style.RESET}"
                )
            else:
                print(
                    f"\n{Style.GREEN}✅ Complete!{Style.RESET} "
                    f"{Style.BOLD}{id_count}{Style.RESET} tracks identified and saved to "
                    f"{Style.DIM}{output_folder}{Style.RESET}"
                )

            dm_path = _save_direct_mode_session_record(
                all_results=all_results,
                output_files=_direct_output_files,
                current_mode=current_mode,
                direct_output_format=direct_output_format,
                config=config,
                session_split_data=_session_split_data,
            )
            if dm_path:
                print(f"  📋 Session record saved (manifest): {os.path.basename(dm_path)}")

            # One-click processing is complete; clear any old unsaved preview state.
            _clear_unsaved_preview_data(cache_path, temp_folder)

            input(f"\n📍 Press Enter to return to main menu...")
            continue  # Back to main menu

    close_terminal()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
