#!/usr/bin/env python3
"""
mixsplitr_utils.py - Shared utility functions for MixSplitR.

Small helpers used by multiple modules.  Keeping them in one place avoids
duplicating the same logic in three or four files.
"""

from __future__ import annotations

import os
import subprocess
import tempfile


# ---------------------------------------------------------------------------
#  Numeric helpers
# ---------------------------------------------------------------------------

def clamp(value, minimum, maximum):
    """Clamp *value* between *minimum* and *maximum* inclusive."""
    return max(minimum, min(maximum, value))


# ---------------------------------------------------------------------------
#  Windows subprocess helpers
# ---------------------------------------------------------------------------

def windows_hidden_subprocess_kwargs() -> dict:
    """Return subprocess kwargs that suppress console windows on Windows.

    On non-Windows platforms this returns an empty dict so callers can always
    unpack the result into their ``subprocess.run()`` / ``Popen()`` calls:

        subprocess.run(cmd, **windows_hidden_subprocess_kwargs())
    """
    if os.name != "nt":
        return {}
    kwargs: dict = {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs


# ---------------------------------------------------------------------------
#  Temp-directory helper
# ---------------------------------------------------------------------------

def get_runtime_temp_directory(subdirectory: str = "runtime") -> str:
    """Return a MixSplitR temp directory, falling back gracefully.

    Tries to use ``mixsplitr_core.get_runtime_temp_directory`` first; if that
    is unavailable (circular-import guard, frozen-app edge case, etc.) it
    falls back to ``<system-tmp>/MixSplitR/<subdirectory>``.
    """
    try:
        from mixsplitr_core import get_runtime_temp_directory as _core_tmpdir
        return str(_core_tmpdir(subdirectory))
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), "MixSplitR", subdirectory)
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            pass
        return fallback
