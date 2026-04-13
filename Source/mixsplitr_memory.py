#!/usr/bin/env python3
"""mixsplitr_memory.py - lightweight RAM / batching helpers

This module exists to keep MixSplitR usable even when psutil isn't installed.
If psutil is available, we use it to estimate available RAM and dynamically
adjust batch sizes between batches to prevent OOM crashes on large libraries.
"""

import os
import gc
import warnings

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except Exception:
    psutil = None
    _PSUTIL_AVAILABLE = False

# Track whether we've already shown the psutil warning this session
_psutil_warning_shown = False


def is_psutil_available():
    return _PSUTIL_AVAILABLE


def warn_if_no_psutil(file_count: int = 0, threshold: int = 50):
    """Print a one-time warning if psutil is missing and the library is large."""
    global _psutil_warning_shown
    if _PSUTIL_AVAILABLE or _psutil_warning_shown:
        return
    if file_count >= threshold:
        _psutil_warning_shown = True
        warnings.warn(
            f"psutil is not installed — RAM monitoring is disabled. "
            f"Processing {file_count} files without memory awareness may cause "
            f"crashes. Install it with:  pip install psutil",
            stacklevel=2,
        )


def get_available_ram_gb(default_gb: float = 4.0) -> float:
    """Best-effort estimate of available RAM in GB."""
    if _PSUTIL_AVAILABLE:
        try:
            return max(0.5, psutil.virtual_memory().available / (1024 ** 3))
        except Exception:
            pass
    return float(default_gb)



def check_memory_pressure() -> str:
    """Return a simple pressure level: 'low', 'moderate', 'high', 'critical'.

    Without psutil, always returns 'unknown' so callers can use conservative
    defaults.
    """
    if not _PSUTIL_AVAILABLE:
        return "unknown"
    try:
        mem = psutil.virtual_memory()
        avail_gb = mem.available / (1024 ** 3)
        pct_used = mem.percent
    except Exception:
        return "unknown"

    if avail_gb < 1.0 or pct_used > 90:
        return "critical"
    elif avail_gb < 2.0 or pct_used > 80:
        return "high"
    elif avail_gb < 4.0 or pct_used > 65:
        return "moderate"
    return "low"


def scan_existing_library(output_folder: str, audio_extensions=None, recursive: bool = True):
    """Return a set of audio basenames already present in output_folder."""
    if not output_folder or not os.path.isdir(output_folder):
        return set()
    if audio_extensions is None:
        audio_extensions = (".mp3", ".m4a", ".flac", ".wav", ".aiff", ".aac", ".ogg")
    normalized_exts = tuple(str(ext).lower() for ext in audio_extensions if ext)
    existing = set()
    try:
        for root, _dirs, files in os.walk(output_folder):
            for name in files:
                lowered = str(name).lower()
                if normalized_exts and not lowered.endswith(normalized_exts):
                    continue
                existing.add(os.path.basename(name))
            if not recursive:
                break
    except Exception:
        pass
    return existing


def create_file_batches(audio_files, available_ram_gb: float = None, max_batch_size: int = 15):
    """Create batches of files, sized according to available RAM.

    The default max_batch_size is 15 (reduced from 30) to keep peak memory
    usage manageable on large libraries.  The actual batch size may be
    further reduced based on current RAM availability.
    """
    if available_ram_gb is None:
        available_ram_gb = get_available_ram_gb()

    if not audio_files:
        return []

    # Finer-grained heuristic batch sizing
    if available_ram_gb < 1.5:
        batch_size = min(4, max_batch_size)
    elif available_ram_gb < 2.0:
        batch_size = min(6, max_batch_size)
    elif available_ram_gb < 3.0:
        batch_size = min(8, max_batch_size)
    elif available_ram_gb < 4.0:
        batch_size = min(10, max_batch_size)
    elif available_ram_gb < 6.0:
        batch_size = min(12, max_batch_size)
    else:
        batch_size = max_batch_size

    batches = []
    for i in range(0, len(audio_files), batch_size):
        batches.append(audio_files[i:i+batch_size])
    return batches


def recalculate_batch_size(remaining_files, max_batch_size: int = 15):
    """Re-check available RAM RIGHT NOW and return a safe batch size.

    Call this before each batch to dynamically shrink if memory pressure
    has increased since the initial batching.
    """
    pressure = check_memory_pressure()
    avail = get_available_ram_gb()

    if pressure == "critical":
        # Bare minimum — process one file at a time
        batch_size = 1
    elif pressure == "high":
        batch_size = min(3, max_batch_size)
    elif pressure == "moderate":
        batch_size = min(6, max_batch_size)
    elif pressure == "unknown":
        # No psutil — be conservative
        batch_size = min(8, max_batch_size)
    else:
        # Also factor in actual RAM numbers
        batch_size = create_file_batches(
            ["dummy"] * min(len(remaining_files), max_batch_size),
            available_ram_gb=avail,
            max_batch_size=max_batch_size,
        )
        batch_size = len(batch_size[0]) if batch_size else max_batch_size

    batch_size = max(1, batch_size)

    # Force a garbage collection before the next batch
    gc.collect()

    return min(batch_size, len(remaining_files))
