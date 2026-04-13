#!/usr/bin/env python3

import os
import sys
import ctypes

try:
    import psutil
except Exception:
    psutil = None


PROCESS_LOOPBACK_MIN_BUILD = 20438
PROCESS_LOOPBACK_HELPER_NAME = "mixsplitr_process_loopback.exe"
VIRTUAL_AUDIO_DEVICE_HINTS = (
    "vb-audio",
    "vb cable",
    "vb-cable",
    "cable input",
    "cable output",
    "cable-a input",
    "cable-a output",
    "cable-b input",
    "cable-b output",
    "virtual cable",
    "virtual audio cable",
    "voicemeeter",
    "vaio",
    "hi-fi cable",
    "blackhole",
)

VIRTUAL_AUDIO_DEVICE_16CH_HINTS = (
    "16ch",
    "16 ch",
    "16-channel",
    "16 channel",
    "16 channels",
)


def windows_build_number():
    if sys.platform != "win32":
        return 0
    try:
        return int(getattr(sys.getwindowsversion(), "build", 0) or 0)
    except Exception:
        return 0


def is_process_loopback_os_supported():
    return sys.platform == "win32" and windows_build_number() >= PROCESS_LOOPBACK_MIN_BUILD


def resolve_process_loopback_helper(resource_resolver=None):
    candidates = []
    if callable(resource_resolver):
        try:
            resolved = str(resource_resolver(PROCESS_LOOPBACK_HELPER_NAME) or "").strip()
        except Exception:
            resolved = ""
        if resolved:
            candidates.append(resolved)

    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, PROCESS_LOOPBACK_HELPER_NAME))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), PROCESS_LOOPBACK_HELPER_NAME))
    candidates.append(os.path.join(os.getcwd(), PROCESS_LOOPBACK_HELPER_NAME))
    if getattr(sys, "executable", ""):
        candidates.append(os.path.join(os.path.dirname(sys.executable), PROCESS_LOOPBACK_HELPER_NAME))

    seen = set()
    for candidate in candidates:
        normalized = os.path.abspath(str(candidate or "").strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(normalized):
            return normalized
    return ""


def is_process_loopback_helper_available(resource_resolver=None):
    if not is_process_loopback_os_supported():
        return False
    return bool(resolve_process_loopback_helper(resource_resolver=resource_resolver))


def is_likely_virtual_audio_device_name(name):
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    return any(hint in normalized for hint in VIRTUAL_AUDIO_DEVICE_HINTS)


def virtual_audio_device_channel_mode(name):
    normalized = str(name or "").strip().lower()
    if not normalized or not is_likely_virtual_audio_device_name(normalized):
        return ""
    if any(hint in normalized for hint in VIRTUAL_AUDIO_DEVICE_16CH_HINTS):
        return "16ch"
    return "stereo"


def _window_process_records():
    if sys.platform != "win32" or psutil is None:
        return []

    user32 = getattr(ctypes, "windll", None)
    if user32 is None:
        return []
    user32 = user32.user32

    records = {}
    current_pid = os.getpid()

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _window_text(hwnd):
        length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return str(buffer.value or "").strip()

    def _callback(hwnd, _lparam):
        try:
            if not bool(user32.IsWindowVisible(hwnd)):
                return True
            if user32.GetWindow(hwnd, 4):  # GW_OWNER
                return True
            title = _window_text(hwnd)
            if not title:
                return True

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_id = int(pid.value or 0)
            if process_id <= 0 or process_id == current_pid:
                return True

            record = records.setdefault(process_id, {"window_titles": set()})
            record["window_titles"].add(title)
        except Exception:
            return True
        return True

    try:
        user32.EnumWindows(EnumWindowsProc(_callback), 0)
    except Exception:
        return []

    hidden_names = {
        "applicationframehost.exe",
        "shellexperiencehost.exe",
        "searchhost.exe",
        "textinputhost.exe",
        "lockapp.exe",
        "startmenuexperiencehost.exe",
        "widgets.exe",
    }

    entries = []
    for pid, record in records.items():
        try:
            proc = psutil.Process(pid)
            name = str(proc.name() or "").strip()
            exe_path = str(proc.exe() or "").strip()
        except Exception:
            continue
        lower_name = name.lower()
        if lower_name in hidden_names:
            continue
        titles = sorted(t for t in record.get("window_titles", set()) if str(t or "").strip())
        if not titles:
            continue
        entries.append(
            {
                "pid": int(pid),
                "process_name": name,
                "exe_name": os.path.basename(exe_path) or name,
                "exe_path": exe_path,
                "window_title": titles[0],
                "window_titles": titles,
            }
        )
    return entries


def _window_titles_by_pid():
    entries = _window_process_records()
    title_map = {}
    for entry in entries:
        pid = int(entry.get("pid") or 0)
        if pid <= 0:
            continue
        titles = [
            str(title or "").strip()
            for title in entry.get("window_titles", [])
            if str(title or "").strip()
        ]
        if titles:
            title_map[pid] = titles
    return title_map


def _audio_session_records():
    if sys.platform != "win32" or psutil is None:
        return []

    try:
        from pycaw.pycaw import AudioUtilities  # type: ignore
    except Exception:
        return []

    hidden_names = {
        "applicationframehost.exe",
        "audiodg.exe",
        "shellexperiencehost.exe",
        "searchhost.exe",
        "textinputhost.exe",
        "lockapp.exe",
        "startmenuexperiencehost.exe",
        "widgets.exe",
    }

    current_pid = os.getpid()
    title_map = _window_titles_by_pid()
    session_map = {}

    try:
        sessions = list(AudioUtilities.GetAllSessions() or [])
    except Exception:
        return []

    for session in sessions:
        try:
            proc = getattr(session, "Process", None)
        except Exception:
            proc = None

        pid = 0
        try:
            pid = int(getattr(session, "ProcessId", 0) or 0)
        except Exception:
            pid = 0
        if pid <= 0 and proc is not None:
            try:
                pid = int(getattr(proc, "pid", 0) or 0)
            except Exception:
                pid = 0
        if pid <= 0 or pid == current_pid:
            continue

        state = None
        try:
            state = int(getattr(session, "State", -1))
        except Exception:
            state = None
        if state not in (None, 1):
            continue

        display_name = ""
        try:
            display_name = str(getattr(session, "DisplayName", "") or "").strip()
        except Exception:
            display_name = ""
        if not display_name:
            ctl = getattr(session, "_ctl", None)
            if ctl is not None:
                try:
                    display_name = str(ctl.GetDisplayName() or "").strip()
                except Exception:
                    display_name = ""

        record = session_map.setdefault(
            pid,
            {
                "pid": pid,
                "process_name": "",
                "exe_name": "",
                "exe_path": "",
                "window_titles": set(),
                "session_display_names": set(),
                "session_count": 0,
            },
        )
        record["session_count"] = int(record.get("session_count") or 0) + 1
        if display_name:
            record["session_display_names"].add(display_name)
        for title in title_map.get(pid, []):
            record["window_titles"].add(title)

    entries = []
    for pid, record in session_map.items():
        try:
            proc = psutil.Process(pid)
            name = str(proc.name() or "").strip()
            exe_path = str(proc.exe() or "").strip()
        except Exception:
            continue

        lower_name = name.lower()
        if lower_name in hidden_names:
            continue

        titles = sorted(t for t in record.get("window_titles", set()) if str(t or "").strip())
        session_names = sorted(
            t for t in record.get("session_display_names", set()) if str(t or "").strip()
        )

        preferred_title = ""
        if titles:
            preferred_title = titles[0]
        elif session_names:
            preferred_title = session_names[0]

        entries.append(
            {
                "pid": int(pid),
                "process_name": name,
                "exe_name": os.path.basename(exe_path) or name,
                "exe_path": exe_path,
                "window_title": preferred_title,
                "window_titles": titles,
                "session_display_name": session_names[0] if session_names else "",
                "session_display_names": session_names,
                "session_count": int(record.get("session_count") or 0),
                "source": "audio_session",
            }
        )
    return entries


def format_process_capture_label(entry):
    pid = int((entry or {}).get("pid") or 0)
    process_name = str((entry or {}).get("process_name") or "").strip()
    exe_name = str((entry or {}).get("exe_name") or "").strip()
    window_title = str((entry or {}).get("window_title") or "").strip()
    session_display_name = str((entry or {}).get("session_display_name") or "").strip()

    base_name = process_name or exe_name or (f"PID {pid}" if pid > 0 else "Unknown App")
    window_part = ""
    if window_title and window_title.lower() != base_name.lower():
        window_part = f" - {window_title}"
    elif session_display_name and session_display_name.lower() != base_name.lower():
        window_part = f" - {session_display_name}"
    return f"{base_name}{window_part} (App Capture)"


def list_running_process_capture_apps():
    entries = _audio_session_records()
    if not entries:
        entries = _window_process_records()
    entries.sort(
        key=lambda item: (
            str(item.get("process_name") or item.get("exe_name") or "").lower(),
            str(item.get("window_title") or "").lower(),
            int(item.get("pid") or 0),
        )
    )
    return entries
