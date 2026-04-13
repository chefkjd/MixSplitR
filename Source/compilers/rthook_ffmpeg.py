"""
Runtime Hook for FFmpeg in PyInstaller Bundle

Save this file as: rthook_ffmpeg.py
Place it in your MixSplitR project root.

This code runs BEFORE your main script when the exe starts.
It configures pydub to use the bundled FFmpeg binaries.
"""

import os
import sys
import subprocess

_VERBOSE = os.environ.get("MIXSPLITR_DEBUG", "").lower() in ("1", "true", "yes")


def _log(message: str):
    if _VERBOSE:
        print(message)


def _warn(message: str):
    print(message, file=sys.stderr)


def _patch_windows_subprocess_no_console():
    if os.name != "nt":
        return
    if str(os.environ.get("MIXSPLITR_ALLOW_SUBPROCESS_CONSOLE", "")).strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return
    try:
        original_popen = subprocess.Popen
        if getattr(original_popen, "__name__", "") == "_MixSplitRHiddenPopen":
            return

        class _MixSplitRHiddenPopen(original_popen):
            def __init__(self, *args, **kwargs):
                try:
                    creationflags = int(kwargs.get("creationflags", 0) or 0)
                except Exception:
                    creationflags = 0
                create_new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) or 0)
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
                            startupinfo.wShowWindow = 0
                            kwargs["startupinfo"] = startupinfo
                        except Exception:
                            pass
                super().__init__(*args, **kwargs)

        subprocess.Popen = _MixSplitRHiddenPopen
    except Exception:
        pass


def _patch_pydub_subprocess_alias():
    if os.name != "nt":
        return
    try:
        import pydub.utils as _pydub_utils
        _pydub_utils.Popen = subprocess.Popen
    except Exception:
        pass


_patch_windows_subprocess_no_console()

_log("=" * 60)
_log("Runtime Hook: Configuring bundled FFmpeg...")
_log("=" * 60)

# ---------------------------------------------------------------------------
# SSL certificate patch — must run before any HTTPS requests are made.
#
# Problem: frozen PyInstaller apps on Python 3.12+ (including 3.14 used for
# the x86_64 build) no longer automatically find the macOS system cert store
# via the default ssl context.  urllib.request.urlopen() silently raises
# SSLCertVerificationError, which is caught broadly in UpdateCheckThread and
# causes it to emit status="error" — making the sidebar show just the version
# number instead of checking GitHub.
#
# Fix: point SSL_CERT_FILE and REQUESTS_CA_BUNDLE at the certifi CA bundle
# that is shipped inside the app.  This is architecture-agnostic and works
# identically on arm64 (Python 3.11) and x86_64 (Python 3.14).
# ---------------------------------------------------------------------------
def _patch_ssl_certifi():
    try:
        import certifi
        ca_bundle = certifi.where()
        if ca_bundle and os.path.isfile(ca_bundle):
            os.environ.setdefault('SSL_CERT_FILE', ca_bundle)
            os.environ.setdefault('REQUESTS_CA_BUNDLE', ca_bundle)
            _log(f"✓ SSL certs patched via certifi: {ca_bundle}")
        else:
            _warn("⚠ certifi.where() returned no usable path — SSL may fail on HTTPS requests")
    except ImportError:
        _warn("⚠ certifi not bundled — HTTPS requests may fail on Python 3.12+ frozen builds")
    except Exception as _ssl_exc:
        _warn(f"⚠ SSL cert patch failed: {_ssl_exc}")

_patch_ssl_certifi()

# Detect if running as PyInstaller bundle
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    bundle_dir = sys._MEIPASS
    _log(f"✓ Detected PyInstaller bundle at: {bundle_dir}")
    
    # Find bundled binaries
    if os.name == 'nt':  # Windows
        ffmpeg_exe = os.path.join(bundle_dir, 'ffmpeg.exe')
        ffprobe_exe = os.path.join(bundle_dir, 'ffprobe.exe')
        fpcalc_exe = os.path.join(bundle_dir, 'fpcalc.exe')
    else:  # Mac/Linux
        ffmpeg_exe = os.path.join(bundle_dir, 'ffmpeg')
        ffprobe_exe = os.path.join(bundle_dir, 'ffprobe')
        fpcalc_exe = os.path.join(bundle_dir, 'fpcalc')
    
    # Configure ffmpeg
    if os.path.exists(ffmpeg_exe):
        _log(f"✓ Found bundled ffmpeg: {ffmpeg_exe}")
        os.environ['FFMPEG_BINARY'] = ffmpeg_exe
    else:
        _warn(f"✗ WARNING: ffmpeg not found at {ffmpeg_exe}")
    
    # Configure ffprobe
    if os.path.exists(ffprobe_exe):
        _log(f"✓ Found bundled ffprobe: {ffprobe_exe}")
        os.environ['FFPROBE_BINARY'] = ffprobe_exe
    else:
        _warn(f"✗ WARNING: ffprobe not found at {ffprobe_exe}")

    # Ensure bundled binary directory is discoverable by pydub's which("ffmpeg")
    # during import-time encoder/prober checks.
    prepend_dirs = []
    for binary in (ffmpeg_exe, ffprobe_exe):
        if os.path.exists(binary):
            binary_dir = os.path.dirname(binary)
            if binary_dir and binary_dir not in prepend_dirs:
                prepend_dirs.append(binary_dir)
    if prepend_dirs:
        existing_path = os.environ.get('PATH', '')
        path_parts = existing_path.split(os.pathsep) if existing_path else []
        merged = prepend_dirs + [p for p in path_parts if p and p not in prepend_dirs]
        os.environ['PATH'] = os.pathsep.join(merged)
    
    # Configure chromaprint
    if os.path.exists(fpcalc_exe):
        _log(f"✓ Found bundled fpcalc: {fpcalc_exe}")
        os.environ['FPCALC'] = fpcalc_exe
    else:
        _warn(f"✗ WARNING: fpcalc not found at {fpcalc_exe}")
    
    # CRITICAL: Configure pydub directly
    # This must happen before pydub.AudioSegment is imported
    try:
        from pydub import AudioSegment
        _patch_pydub_subprocess_alias()
        if os.path.exists(ffmpeg_exe):
            AudioSegment.converter = ffmpeg_exe
            AudioSegment.ffmpeg = ffmpeg_exe
        if os.path.exists(ffprobe_exe):
            AudioSegment.ffprobe = ffprobe_exe
        _log("✓ pydub configured to use bundled FFmpeg")
    except ImportError:
        _log("⚠ pydub not yet imported, will use environment variables")
    
    _log("=" * 60)
else:
    _log("Running as normal Python script (not bundled)")
    _log("=" * 60)
