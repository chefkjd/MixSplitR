# -*- mode: python ; coding: utf-8 -*-
"""
MixSplitR v8.0 PyInstaller Spec File - SINGLE FILE MODE (GUI + CLI)
Bundles everything including chromaprint (fpcalc), FFmpeg, and UI assets into ONE executable
"""

import sys
import os
import shutil
import glob
import struct
import subprocess
from pathlib import Path


def _resolve_spec_path(spec_name):
    explicit_spec = globals().get("SPEC")
    if explicit_spec:
        return Path(explicit_spec).resolve()
    spec_dir = globals().get("SPECPATH")
    spec_stem = globals().get("specnm")
    if spec_dir and spec_stem:
        return Path(spec_dir, f"{spec_stem}.spec").resolve()
    implicit_file = globals().get("__file__")
    if implicit_file:
        return Path(implicit_file).resolve()
    cwd = Path(os.getcwd()).resolve()
    candidates = [cwd / "compilers" / spec_name, cwd / spec_name]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


_SPEC_PATH = _resolve_spec_path("MixSplitR_ONEFILE.spec")
COMPILERS_DIR = _SPEC_PATH.parent
PROJECT_ROOT = COMPILERS_DIR.parent


def find_windows_icon():
    """
    Return icon path for Windows executable if available, else None.
    """
    if sys.platform != "win32":
        return None

    def _read_ico_sizes(path):
        try:
            data = open(path, "rb").read()
        except OSError:
            return set()
        if len(data) < 6:
            return set()
        reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
        if reserved != 0 or icon_type != 1:
            return set()
        sizes = set()
        offset = 6
        for _ in range(count):
            if offset + 16 > len(data):
                break
            width, height, _, _, _, _, _, _ = struct.unpack_from("<BBBBHHII", data, offset)
            offset += 16
            w = 256 if width == 0 else int(width)
            h = 256 if height == 0 else int(height)
            sizes.add((w, h))
        return sizes

    def _is_high_res_ico(path):
        sizes = _read_ico_sizes(path)
        if not sizes:
            return False
        required = {(16, 16), (32, 32), (48, 48)}
        if not required.issubset(sizes):
            return False
        return any(w >= 256 and h >= 256 for w, h in sizes)

    icon_path = PROJECT_ROOT / "icon.ico"
    if icon_path.exists():
        if _is_high_res_ico(icon_path):
            print(f"✓ Found Windows EXE icon: {icon_path}")
            return str(icon_path)
        print("⚠️  Warning: icon.ico exists but is low-resolution; attempting to regenerate.")
    else:
        print("⚠️  Warning: icon.ico not found; attempting to generate from mixsplitr_icon_512.png.")

    icon_builder = COMPILERS_DIR / "create_windows_icon.py"
    source_icon = PROJECT_ROOT / "mixsplitr_icon_512.png"
    if icon_builder.exists():
        cmd = [sys.executable, str(icon_builder), str(icon_path), "--source", str(source_icon)]
        try:
            subprocess.run(cmd, check=False)
        except Exception as exc:
            print(f"⚠️  Warning: icon generation command failed: {exc}")
    else:
        print("⚠️  Warning: create_windows_icon.py not found; cannot auto-generate icon")

    if icon_path.exists() and _is_high_res_ico(icon_path):
        print(f"✓ Using regenerated Windows EXE icon: {icon_path}")
        return str(icon_path)

    if icon_path.exists():
        print("⚠️  Warning: icon.ico is still low-resolution; EXE icon may appear blurry")
        return str(icon_path)

    print("⚠️  Warning: icon.ico not found - EXE will use default icon")
    return None

# =============================================================================
# Helper Function: Find fpcalc Executable
# =============================================================================
def find_fpcalc():
    """
    Find fpcalc executable to bundle with the application.
    Returns list of tuples: [(source_path, destination_in_exe), ...]
    """
    # Try to find in PATH first
    fpcalc = shutil.which('fpcalc')
    if fpcalc:
        print(f"✓ Found fpcalc in PATH: {fpcalc}")
        return [(fpcalc, '.')]

    # Windows: Check common locations
    if sys.platform == 'win32':
        common_paths = [
            str(PROJECT_ROOT / 'fpcalc.exe'),
            r'C:\Program Files\Chromaprint\fpcalc.exe',
            r'C:\Program Files (x86)\Chromaprint\fpcalc.exe',
        ]
        for path in common_paths:
            if os.path.exists(path):
                print(f"✓ Found fpcalc at: {path}")
                return [(path, '.')]

    # macOS: Check Homebrew location
    elif sys.platform == 'darwin':
        homebrew_paths = [
            '/usr/local/bin/fpcalc',  # Intel Mac
            '/opt/homebrew/bin/fpcalc',  # Apple Silicon Mac
        ]
        for path in homebrew_paths:
            if os.path.exists(path):
                print(f"✓ Found fpcalc at: {path}")
                return [(path, '.')]

    # Linux: Check common locations
    else:
        linux_paths = [
            '/usr/bin/fpcalc',
            '/usr/local/bin/fpcalc',
        ]
        for path in linux_paths:
            if os.path.exists(path):
                print(f"✓ Found fpcalc at: {path}")
                return [(path, '.')]

    print("⚠️  Warning: fpcalc not found! MusicBrainz mode will require manual installation.")
    return []


# =============================================================================
# Helper Function: Find FFmpeg Executables (ffmpeg + ffprobe)
# =============================================================================
def find_ffmpeg():
    """
    Find ffmpeg AND ffprobe executables to bundle with the application.
    Returns list of tuples: [(source_path, destination_in_exe), ...]
    """
    binaries = []

    # --- Find ffmpeg ---
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        print(f"✓ Found ffmpeg in PATH: {ffmpeg}")
        binaries.append((ffmpeg, '.'))
    elif sys.platform == 'win32':
        common_paths = [
            str(PROJECT_ROOT / 'ffmpeg.exe'),
            r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
            r'C:\ffmpeg\bin\ffmpeg.exe',
        ]
        for path in common_paths:
            if os.path.exists(path):
                print(f"✓ Found ffmpeg at: {path}")
                binaries.append((path, '.'))
                break

    # --- Find ffprobe (CRITICAL - pydub needs this!) ---
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        print(f"✓ Found ffprobe in PATH: {ffprobe}")
        binaries.append((ffprobe, '.'))
    elif sys.platform == 'win32':
        common_paths = [
            str(PROJECT_ROOT / 'ffprobe.exe'),
            r'C:\Program Files\ffmpeg\bin\ffprobe.exe',
            r'C:\ffmpeg\bin\ffprobe.exe',
        ]
        for path in common_paths:
            if os.path.exists(path):
                print(f"✓ Found ffprobe at: {path}")
                binaries.append((path, '.'))
                break

    if len(binaries) < 2:
        print("⚠️  Warning: ffmpeg or ffprobe not found! Audio processing will fail.")
        print("    Make sure both ffmpeg.exe AND ffprobe.exe are in the project folder.")

    return binaries

# =============================================================================
# Helper Function: Find UI Assets (PNG images for GUI)
# =============================================================================
def find_ui_assets():
    """
    Find UI image assets and bundled fonts to include with the application.
    Returns list of tuples: [(source_path, destination_in_exe), ...]
    """
    assets = []
    ui_files = [
        'mixsplitr.png',
        'mixsplitr_icon_512.png',
    ]
    for f in ui_files:
        source_path = PROJECT_ROOT / f
        if source_path.exists():
            print(f"✓ Found UI asset: {source_path}")
            assets.append((str(source_path), '.'))
        else:
            print(f"⚠️  Warning: UI asset {f} not found!")

    # Bundle optional Roboto font files from ./fonts/ into /fonts inside the app.
    font_dir = PROJECT_ROOT / 'fonts'
    roboto_globs = (
        'Roboto*.ttf',
        'Roboto*.otf',
    )
    found_font = False
    for pattern in roboto_globs:
        for font_path in sorted(glob.glob(str(font_dir / pattern))):
            if os.path.isfile(font_path):
                print(f"✓ Found bundled font: {font_path}")
                assets.append((font_path, 'fonts'))
                found_font = True

    if not found_font:
        print("⚠️  Warning: No Roboto font files found in ./fonts (Roboto*.ttf / Roboto*.otf)")

    return assets


def find_windows_process_capture_helper():
    """Find the optional Windows app-capture helper executable."""
    binaries = []
    helper_name = 'mixsplitr_process_loopback.exe'
    helper_path = PROJECT_ROOT / helper_name
    if helper_path.exists():
        print(f"✓ Found Windows app-capture helper: {helper_path}")
        binaries.append((str(helper_path), '.'))
    else:
        message = f"Windows app-capture helper not found: {helper_name}"
        if sys.platform == 'win32':
            raise SystemExit(f"ERROR: {message}. Build the helper before running PyInstaller.")
        print(f"ℹ {message}")
    return binaries

# =============================================================================
# Analysis - Collect Scripts and Dependencies
# =============================================================================

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect complex packages that have data files, binaries, or dynamic imports.
# Each collect_all returns (datas, binaries, hiddenimports).
# We accumulate them all into combined lists.
_extra_datas = []
_extra_binaries = []
_extra_hiddenimports = []

_packages_to_collect = [
    'acrcloud',         # ACRCloud SDK - has native binaries
    'librosa',          # Audio analysis - has data files and many submodules
    'essentia',         # Essentia DSP/onset analysis (native extensions)
    'shazamio',         # Shazam async client - has internal data/protos
    'prompt_toolkit',   # Interactive terminal UI - many submodules
    'soundcard',        # System audio capture - platform-specific backends
    'soundfile',        # WAV/FLAC I/O - wraps libsndfile
    'sounddevice',      # Audio I/O fallback - wraps PortAudio
    'pycaw',            # Windows audio session enumeration for per-app capture lists
    'comtypes',         # COM bridge used by pycaw
    'PySide6',          # Qt GUI framework - many plugins and data files
    'certifi',          # CA certificate bundle — required for HTTPS on Python 3.12+
                        # frozen builds (urllib/requests SSL cert path changes in 3.12+
                        # mean the system cert store is no longer found automatically).
    'requests',         # HTTP client — collect to ensure certifi data files are bundled
                        # alongside it (requests uses certifi for its default CA bundle).
]

# On macOS, also collect pyobjc packages needed for sidebar vibrancy.
# collect_all handles submodule discovery for objc and AppKit so all
# framework bindings and native extensions are included in the bundle.
if sys.platform == 'darwin':
    for _mac_pkg in ('objc', 'AppKit', 'Foundation'):
        try:
            d, b, h = collect_all(_mac_pkg)
            print(f"✓ Collected macOS vibrancy pkg {_mac_pkg}: {len(d)} data, {len(b)} binaries, {len(h)} hidden imports")
            _extra_datas.extend(d)
            _extra_binaries.extend(b)
            _extra_hiddenimports.extend(h)
        except Exception as _mac_e:
            print(f"⚠️  {_mac_pkg} not installed — sidebar vibrancy will be disabled in this build ({_mac_e})")

for pkg in _packages_to_collect:
    try:
        d, b, h = collect_all(pkg)
        print(f"✓ Collected {pkg}: {len(d)} data, {len(b)} binaries, {len(h)} hidden imports")
        _extra_datas.extend(d)
        _extra_binaries.extend(b)
        _extra_hiddenimports.extend(h)
    except Exception as e:
        print(f"ℹ {pkg} not installed (optional) - {e}")

a = Analysis(
    [str(PROJECT_ROOT / 'main_ui.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        # External binaries (ffmpeg, ffprobe, fpcalc)
        *find_fpcalc(),
        *find_ffmpeg(),
        *find_windows_process_capture_helper(),
        # Binaries discovered by collect_all above
        *_extra_binaries,
    ],
    datas=[
        # UI image assets
        *find_ui_assets(),
        # Data files discovered by collect_all above
        *_extra_datas,
    ],
    hiddenimports=[
        # ==== macOS vibrancy bridge (pyobjc) — required for sidebar transparency ====
        # These are conditionally imported in main_ui.py inside `if sys.platform == "darwin":`
        # which PyInstaller's static analyser can miss.  List them explicitly so they are
        # always bundled on macOS builds regardless of whether static analysis detects them.
        'objc',
        'AppKit',
        'AppKit._metadata',
        'AppKit._nsapp',
        'Foundation',
        'PyObjCTools',
        'PyObjCTools.KeyValueCoding',

        # ==== PySide6 GUI (required for UI) ====
        'PySide6',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        'PySide6.QtCore',
        'PySide6.QtMultimedia',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',
        'PySide6.QtOpenGL',
        'shiboken6',

        # ==== Core (always required) ====
        'pydub',
        'pydub.silence',
        'pydub.utils',
        'requests',
        'requests.adapters',
        'urllib3',
        'tqdm',
        'audioop',

        # ==== MixSplitR modules (explicit to ensure bundling) ====
        'mixsplitr',
        'mixsplitr_core',
        'mixsplitr_identify',
        'mixsplitr_metadata',
        'mixsplitr_tagging',
        'mixsplitr_editor',
        'mixsplitr_audio',
        'mixsplitr_tracklist',
        'mixsplitr_record',
        'mixsplitr_manifest',
        'mixsplitr_memory',
        'mixsplitr_menu',
        'mixsplitr_menus',
        'mixsplitr_processing',
        'mixsplitr_pipeline',
        'mixsplitr_session',
        'mixsplitr_autotracklist',
        'mixsplitr_cdrip',
        'mixsplitr_process_capture',
        'splitter_ui',

        # ==== Audio tagging - mutagen (dynamic imports in tagging functions) ====
        'mutagen',
        'mutagen.flac',
        'mutagen.mp4',
        'mutagen.id3',
        'mutagen.id3._frames',
        'mutagen.oggvorbis',
        'mutagen.oggopus',
        'mutagen.wave',
        'mutagen.aiff',
        'mutagen.mp3',

        # ==== ACRCloud (optional) ====
        'acrcloud',
        'acrcloud.recognizer',
        'hmac',
        'hashlib',
        'base64',

        # ==== MusicBrainz / AcoustID (optional) ====
        'acoustid',
        'musicbrainzngs',
        'discid',

        # ==== Shazam (optional) - async library with many deps ====
        'shazamio',
        'aiohttp',
        'aiohttp.connector',
        'aiohttp.client',
        'aiohttp.client_reqrep',
        'aiohttp.formdata',
        'aiohttp.multipart',
        'aiohttp.payload',
        'aiohttp.resolver',
        'aiohttp.tracing',
        'aiosignal',
        'frozenlist',
        'multidict',
        'yarl',
        'async_timeout',
        'attrs',
        'charset_normalizer',

        # ==== Audio analysis (optional) ====
        'librosa',
        'librosa.beat',
        'librosa.onset',
        'librosa.core',
        'librosa.util',
        'numpy',
        'numpy.fft',
        'scipy',
        'scipy.signal',
        'scipy.fft',
        'numba',
        'essentia',
        'essentia.standard',
        'essentia.streaming',
        'soundfile',
        'soundcard',
        'sounddevice',
        'pycaw',
        'pycaw.pycaw',
        'comtypes',
        'comtypes.client',
        'comtypes.automation',

        # ==== Interactive CLI UI (optional) ====
        'prompt_toolkit',
        'prompt_toolkit.application',
        'prompt_toolkit.key_binding',
        'prompt_toolkit.key_binding.key_bindings',
        'prompt_toolkit.layout',
        'prompt_toolkit.layout.containers',
        'prompt_toolkit.layout.controls',
        'prompt_toolkit.formatted_text',
        'prompt_toolkit.formatted_text.html',
        'prompt_toolkit.styles',
        'prompt_toolkit.widgets',
        'wcwidth',

        # ==== System utilities (optional) ====
        'psutil',

        # ==== Stdlib that PyInstaller sometimes misses ====
        'concurrent.futures',
        'asyncio',
        'asyncio.events',
        'asyncio.base_events',
        'ctypes',
        'array',
        'struct',
        'copy',

        # Hidden imports from collect_all
        *_extra_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(COMPILERS_DIR / 'rthook_ffmpeg.py')] if (COMPILERS_DIR / 'rthook_ffmpeg.py').exists() else [],
    excludes=[
        # Exclude unnecessary packages to reduce size
        'matplotlib',
        'matplotlib.pyplot',
        'PIL',
        'tkinter',
        'IPython',
        'jupyter',
        'pytest',
        'sphinx',
        'setuptools',
    ],
    noarchive=False,
)

# =============================================================================
# PYZ - Python Archive
# =============================================================================
pyz = PYZ(a.pure)

# =============================================================================
# EXE - SINGLE FILE EXECUTABLE
# =============================================================================
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MixSplitR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # Compress with UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI mode - no console window
    disable_windowed_traceback=False,
    onefile=True,  # ← SINGLE FILE MODE - Everything in one .exe!
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=find_windows_icon(),
)

# =============================================================================
# Build Summary
# =============================================================================
print("\n" + "="*60)
print("PyInstaller Build Configuration - SINGLE FILE MODE (GUI)")
print("="*60)
print(f"Target: {sys.platform}")
print(f"Mode: Single executable (onefile=True)")
print(f"Entry point: main_ui.py (GUI)")
print(f"Console: False (windowed GUI)")
fpcalc_binaries = find_fpcalc()
ffmpeg_binaries = find_ffmpeg()
process_capture_binaries = find_windows_process_capture_helper()
ui_assets = find_ui_assets()
print(f"Bundled chromaprint: {'Yes' if fpcalc_binaries else 'No (WARNING!)'}")
print(f"Bundled ffmpeg+ffprobe: {'Yes (' + str(len(ffmpeg_binaries)) + ' files)' if ffmpeg_binaries else 'No (WARNING!)'}")
print(f"Bundled app-capture helper: {'Yes' if process_capture_binaries else 'No (feature disabled)'}")
print(f"Bundled UI assets: {len(ui_assets)} files")
print(f"Collected packages: {', '.join(p for p in _packages_to_collect)}")
print(f"Total hidden imports: {len(a.hiddenimports)}")
print(f"Total extra datas: {len(_extra_datas) + len(ui_assets)}")
print(f"Total extra binaries: {len(_extra_binaries) + len(fpcalc_binaries) + len(ffmpeg_binaries) + len(process_capture_binaries)}")
print("Output: dist/MixSplitR.exe (Windows) or dist/MixSplitR (Mac/Linux)")
print("="*60 + "\n")
