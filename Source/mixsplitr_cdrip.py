"""
mixsplitr_cdrip.py
CD ripping helpers for MixSplitR.

Phase 1+2 scope:
- Detect likely CD drives/devices
- Rip disc tracks exposed as files (.cda/.wav/.aiff/...)
- Save ripped tracks with safe sequential fallback names
- Optionally enrich names/tags from MusicBrainz Disc ID metadata
- Return ripped file paths so the normal MixSplitR pipeline can process them
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import time
import ctypes
from typing import Callable, Optional

from mixsplitr_core import (
    CURRENT_VERSION,
    GITLAB_REPO,
    Style,
    get_cd_rip_output_directory,
    setup_ffmpeg,
)

try:
    import discid as _discid  # type: ignore
except Exception:
    _discid = None

try:
    import musicbrainzngs as _musicbrainzngs  # type: ignore
except Exception:
    _musicbrainzngs = None


CD_RIP_FORMATS = {
    "flac": {
        "ext": "flac",
        "codec_args": ["-c:a", "flac", "-compression_level", "8"],
        "label": "FLAC (lossless)",
    },
    "wav": {
        "ext": "wav",
        "codec_args": ["-c:a", "pcm_s16le"],
        "label": "WAV (lossless)",
    },
    "mp3_320": {
        "ext": "mp3",
        "codec_args": ["-c:a", "libmp3lame", "-b:a", "320k"],
        "label": "MP3 320k",
    },
}


_MB_USERAGENT_SET = False


if sys.platform == "win32":
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_DEVICE_CD_ROM = 0x00000002
    _METHOD_OUT_DIRECT = 2
    _FILE_READ_ACCESS = 0x00000001
    _IOCTL_CDROM_RAW_READ = (
        (_FILE_DEVICE_CD_ROM << 16)
        | (_FILE_READ_ACCESS << 14)
        | (0x000F << 2)
        | _METHOD_OUT_DIRECT
    )
    _TRACK_MODE_CDDA = 2
    _CDDA_SECTOR_SIZE = 2352
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _RAW_READ_INFO(ctypes.Structure):
        _fields_ = [
            ("DiskOffset", ctypes.c_longlong),
            ("SectorCount", wintypes.ULONG),
            ("TrackMode", ctypes.c_int),
        ]


def _is_yes(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def is_cd_rip_available() -> bool:
    """Return True when ffmpeg is available for CD ripping operations."""
    try:
        ffmpeg_bin, _ = setup_ffmpeg()
        return bool(ffmpeg_bin and os.path.exists(ffmpeg_bin))
    except Exception:
        return False


def is_discid_metadata_available() -> bool:
    """Return True when Disc ID metadata lookup libraries are available."""
    return _discid is not None and _musicbrainzngs is not None


def _natural_sort_key(path: str):
    base = os.path.basename(path)
    parts = re.split(r"(\d+)", base)
    out = []
    for part in parts:
        if part.isdigit():
            out.append(int(part))
        else:
            out.append(part.lower())
    return out


def _looks_like_drive_letter(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:\\?$", value.strip()))


def _normalize_drive_input(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'")
    if _looks_like_drive_letter(value):
        return value[:2] + "\\"
    return value


def _windows_drive_spec(source: str) -> str:
    source = _normalize_drive_input(source)
    if _looks_like_drive_letter(source):
        return source[:2]
    return source


def _parse_msf_frames(value: str) -> int:
    parts = [int(part) for part in re.findall(r"\d+", str(value or ""))]
    if len(parts) >= 4:
        parts = parts[-3:]
    if len(parts) != 3:
        raise ValueError(f"Invalid MSF time value: {value!r}")
    minutes, seconds, frames = parts
    return max(0, (minutes * 60 * 75) + (seconds * 75) + frames)


def _windows_mci_command(command: str, return_buffer_chars: int = 255) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows MCI CD audio is unavailable on this platform.")

    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    result_buffer = ctypes.create_unicode_buffer(max(2, int(return_buffer_chars or 255)))
    error_code = winmm.mciSendStringW(command, result_buffer, len(result_buffer), None)
    if error_code:
        error_buffer = ctypes.create_unicode_buffer(512)
        try:
            winmm.mciGetErrorStringW(error_code, error_buffer, len(error_buffer))
            error_text = error_buffer.value.strip()
        except Exception:
            error_text = ""
        raise RuntimeError(error_text or f"MCI command failed ({error_code}): {command}")
    return str(result_buffer.value or "").strip()


def _query_windows_audio_cd_tracks(source: str, cda_paths: list[str]) -> list[dict]:
    if sys.platform != "win32":
        return []

    drive = _windows_drive_spec(source)
    if not _looks_like_drive_letter(drive):
        return []

    alias = f"mixsplitrcd{int(time.time() * 1000)}"
    _windows_mci_command(f'open "{drive}" type cdaudio alias {alias} shareable')
    try:
        _windows_mci_command(f"set {alias} time format msf")
        track_total = int(_windows_mci_command(f"status {alias} number of tracks") or "0")
        preferred_paths = {}
        for path in cda_paths:
            match = re.search(r"(\d+)", os.path.basename(path))
            if match:
                preferred_paths[int(match.group(1))] = path

        tracks = []
        for track_no in range(1, track_total + 1):
            try:
                track_type = _windows_mci_command(f"status {alias} type track {track_no}").lower()
            except Exception:
                track_type = ""
            if track_type and "audio" not in track_type:
                continue

            start_text = _windows_mci_command(f"status {alias} position track {track_no}")
            length_text = _windows_mci_command(f"status {alias} length track {track_no}")
            absolute_sector = _parse_msf_frames(start_text)
            start_sector = max(0, absolute_sector - 150)
            sector_count = _parse_msf_frames(length_text)
            if sector_count <= 0:
                continue

            src_path = preferred_paths.get(track_no) or os.path.join(
                _normalize_drive_input(source), f"Track{track_no:02d}.cda"
            )
            tracks.append(
                {
                    "kind": "windows_cdda",
                    "source_path": src_path,
                    "track_number": track_no,
                    "start_sector": start_sector,
                    "absolute_sector": absolute_sector,
                    "sector_count": sector_count,
                }
            )
        return tracks
    finally:
        try:
            _windows_mci_command(f"close {alias}")
        except Exception:
            pass


def _discover_disc_tracks(source: str) -> list[dict]:
    input_paths = _discover_disc_track_inputs(source)
    if not input_paths:
        return []

    if sys.platform == "win32":
        cda_tracks = [path for path in input_paths if path.lower().endswith(".cda")]
        if cda_tracks:
            try:
                native_tracks = _query_windows_audio_cd_tracks(source, cda_tracks)
            except Exception as exc:
                raise RuntimeError(
                    "Windows exposed Audio CD tracks as .cda shortcut files, but native CD audio "
                    "extraction could not initialize. Select the drive letter directly and confirm "
                    f"the disc is an Audio CD. Details: {exc}"
                ) from exc
            if native_tracks:
                return native_tracks
            raise RuntimeError(
                "Windows exposed Audio CD tracks as .cda shortcut files, but native CD audio "
                "extraction could not read the disc table of contents. Select the drive letter "
                "directly and confirm the disc is an Audio CD."
            )

    return [{"kind": "file", "source_path": path} for path in input_paths]


def _open_windows_cdrom_handle(source: str):
    if sys.platform != "win32":
        raise RuntimeError("Raw Windows CD audio access is unavailable on this platform.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    device_path = f"\\\\.\\{_windows_drive_spec(source)}"
    handle = kernel32.CreateFileW(
        device_path,
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        err_no = ctypes.get_last_error()
        raise OSError(err_no, f"Unable to open CD drive {device_path!r} for raw audio reads.")
    return handle


def _read_windows_cdda_sectors(handle, start_sector: int, sector_count: int, chunk_sectors: int = 16):
    if sys.platform != "win32":
        raise RuntimeError("Raw Windows CD audio access is unavailable on this platform.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    current_sector = max(0, int(start_sector or 0))
    remaining = max(0, int(sector_count or 0))
    while remaining > 0:
        sectors_this_pass = min(int(chunk_sectors or 16), remaining)
        out_size = sectors_this_pass * _CDDA_SECTOR_SIZE
        read_info = _RAW_READ_INFO(
            DiskOffset=current_sector * 2048,
            SectorCount=sectors_this_pass,
            TrackMode=_TRACK_MODE_CDDA,
        )
        out_buffer = ctypes.create_string_buffer(out_size)
        bytes_returned = wintypes.DWORD()
        ok = kernel32.DeviceIoControl(
            handle,
            _IOCTL_CDROM_RAW_READ,
            ctypes.byref(read_info),
            ctypes.sizeof(read_info),
            out_buffer,
            out_size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err_no = ctypes.get_last_error()
            raise OSError(err_no, f"Raw CD read failed at sector {current_sector}.")
        if int(bytes_returned.value or 0) != out_size:
            raise OSError(
                0,
                f"Short raw CD read at sector {current_sector}: expected {out_size} bytes, "
                f"got {int(bytes_returned.value or 0)}.",
            )
        yield out_buffer.raw[:out_size]
        current_sector += sectors_this_pass
        remaining -= sectors_this_pass


def _iter_windows_cdda_read_plans(track: dict) -> list[tuple[int, int]]:
    start_candidates = []
    for key in ("start_sector", "absolute_sector"):
        try:
            value = int(track.get(key))
        except Exception:
            continue
        if value >= 0 and value not in start_candidates:
            start_candidates.append(value)
    if not start_candidates:
        start_candidates.append(0)

    plans = []
    for start_sector in start_candidates:
        for chunk_sectors in (16, 8, 4, 1):
            plans.append((start_sector, chunk_sectors))
    return plans


def _rip_windows_cdda_track(
    *,
    source: str,
    track: dict,
    ffmpeg_bin: str,
    fmt: dict,
    out_path: str,
    idx: int,
    total: int,
    title: str,
    album_name: str,
    artist_name: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    per_track_timeout: int = 120,
    timeout_mode: str = "skip",
    timeout_callback: Optional[Callable[[int, int, str], None]] = None,
    skip_track_flag: Optional[Callable[[int, str], bool]] = None,
) -> tuple[str, str]:
    max_retries = 0 if timeout_mode == "manual_skip" else 1
    metadata_args = [
        "-metadata",
        f"title={_metadata_text(title, fallback=f'Track {idx:02d}')}",
        "-metadata",
        f"track={idx}",
        "-metadata",
        f"album={_metadata_text(album_name, fallback='CD Rip')}",
        "-metadata",
        f"artist={_metadata_text(artist_name, fallback='Unknown Artist')}",
        "-metadata",
        "genre=CD Rip",
    ]
    soft_timeout_seconds = max(1, int(per_track_timeout or 120))
    hard_timeout_seconds = max(300, soft_timeout_seconds) if timeout_mode == "manual_skip" else soft_timeout_seconds
    track_started_at = time.monotonic()
    timeout_prompt_sent = False
    attempt = 0

    def _manual_skip_state() -> str:
        nonlocal timeout_prompt_sent
        if timeout_mode != "manual_skip":
            return ""
        elapsed_total = time.monotonic() - track_started_at
        if elapsed_total >= float(soft_timeout_seconds) and not timeout_prompt_sent:
            if timeout_callback:
                timeout_callback(idx, total, title)
            timeout_prompt_sent = True
        if timeout_prompt_sent and skip_track_flag and skip_track_flag(idx, title):
            return "skip"
        if elapsed_total >= float(hard_timeout_seconds):
            return "deadline"
        return ""

    while attempt <= max_retries or timeout_mode == "manual_skip":
        if cancel_flag and cancel_flag():
            return ("cancelled", "")

        retry_requested = False
        last_invalid_parameter_error = ""
        last_retryable_error = ""

        for start_sector, chunk_sectors in _iter_windows_cdda_read_plans(track):
            state = _manual_skip_state()
            if state == "skip":
                return ("skipped", "Skipped by user after timeout")
            if state == "deadline":
                return ("failed", "Timed out after 5 minutes")

            cmd = [
                ffmpeg_bin,
                "-y",
                "-f",
                "s16be",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-i",
                "pipe:0",
                "-vn",
                *fmt["codec_args"],
                *metadata_args,
                out_path,
            ]

            proc = None
            handle = None
            stderr_data = b""
            attempt_started_at = time.monotonic()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                handle = _open_windows_cdrom_handle(source)
                for chunk in _read_windows_cdda_sectors(
                    handle,
                    start_sector,
                    int(track.get("sector_count") or 0),
                    chunk_sectors=chunk_sectors,
                ):
                    if cancel_flag and cancel_flag():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return ("cancelled", "")

                    state = _manual_skip_state()
                    if state == "skip":
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return ("skipped", "Skipped by user after timeout")
                    if state == "deadline":
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return ("failed", "Timed out after 5 minutes")

                    if (
                        timeout_mode != "manual_skip"
                        and (time.monotonic() - attempt_started_at) > float(soft_timeout_seconds)
                    ):
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        if attempt < max_retries:
                            retry_requested = True
                            if progress_callback:
                                progress_callback(idx, total, f"{title} (retrying...)")
                            time.sleep(2)
                            break
                        return ("failed", "Timed out after retry")

                    try:
                        if proc.stdin is None:
                            raise BrokenPipeError("ffmpeg stdin was closed.")
                        proc.stdin.write(chunk)
                    except BrokenPipeError:
                        break

                if retry_requested:
                    break

                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

                try:
                    _, stderr_data = proc.communicate(timeout=max(30, soft_timeout_seconds))
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    _, stderr_data = proc.communicate()
                    if timeout_mode == "manual_skip":
                        last_retryable_error = "ffmpeg did not finish after raw CD data was sent"
                        continue
                    return ("failed", "ffmpeg did not finish after raw CD data was sent")

                if proc.returncode != 0:
                    err_msg = (stderr_data or b"").decode("utf-8", errors="replace")[-200:]
                    if timeout_mode == "manual_skip":
                        last_retryable_error = f"ffmpeg error: {err_msg}"
                        continue
                    return ("failed", f"ffmpeg error: {err_msg}")

                return ("ok", "")
            except OSError as exc:
                err_no = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
                if int(err_no or 0) == 87:
                    last_invalid_parameter_error = str(exc)
                    continue
                if timeout_mode == "manual_skip":
                    last_retryable_error = str(exc)
                    continue
                last_retryable_error = str(exc)
                continue
            except Exception as exc:
                if timeout_mode == "manual_skip":
                    last_retryable_error = str(exc)
                    continue
                return ("failed", str(exc))
            finally:
                if handle not in (None, _INVALID_HANDLE_VALUE):
                    try:
                        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
                    except Exception:
                        pass
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass

        state = _manual_skip_state()
        if state == "skip":
            return ("skipped", "Skipped by user after timeout")
        if state == "deadline":
            return ("failed", "Timed out after 5 minutes")

        if retry_requested:
            attempt += 1
            continue

        if timeout_mode == "manual_skip":
            if last_invalid_parameter_error and not last_retryable_error:
                return ("failed", last_invalid_parameter_error)
            if last_retryable_error:
                if progress_callback:
                    progress_callback(idx, total, f"{title} (retrying damaged sectors...)")
                time.sleep(2)
                attempt += 1
                continue
            break

        if last_invalid_parameter_error:
            return ("failed", last_invalid_parameter_error)
        if last_retryable_error:
            if attempt < max_retries:
                if progress_callback:
                    progress_callback(idx, total, f"{title} (retrying...)")
                time.sleep(2)
                attempt += 1
                continue
            return ("failed", last_retryable_error)
        break

    return ("failed", "Windows CD audio extraction failed")


def _list_windows_cd_drives() -> list[dict]:
    drives = []
    try:
        import ctypes

        DRIVE_CDROM = 5
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for idx in range(26):
            if not (bitmask & (1 << idx)):
                continue
            letter = chr(ord("A") + idx)
            root = f"{letter}:\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
            if drive_type == DRIVE_CDROM:
                drives.append(
                    {
                        "id": root,
                        "label": f"{letter}:\\ (CD/DVD drive)",
                        "path": root,
                    }
                )
    except Exception:
        pass
    return drives


def _list_posix_cd_devices() -> list[dict]:
    drives = []
    if sys.platform == "darwin":
        # Mounted volumes are the most user-friendly source when available.
        # Keep this conservative to avoid listing every mounted disk.
        for path in sorted(glob.glob("/Volumes/*")):
            if not os.path.isdir(path):
                continue
            name = os.path.basename(path)
            has_track_files = bool(glob.glob(os.path.join(path, "*.cda"))) or bool(glob.glob(os.path.join(path, "*.aiff")))
            likely_cd_name = bool(re.search(r"(audio|cd)", name, re.IGNORECASE))
            if has_track_files or likely_cd_name:
                drives.append({"id": path, "label": f"{name} (/Volumes)", "path": path})
    else:
        device_candidates = ["/dev/cdrom", "/dev/sr0", "/dev/sr1", "/dev/scd0"]
        for dev in device_candidates:
            if os.path.exists(dev):
                drives.append({"id": dev, "label": f"{dev} (device)", "path": dev})
        for path in sorted(glob.glob("/media/*/*")) + sorted(glob.glob("/mnt/*")):
            if os.path.isdir(path):
                name = os.path.basename(path)
                has_track_files = bool(glob.glob(os.path.join(path, "*.cda"))) or bool(glob.glob(os.path.join(path, "*.wav")))
                likely_cd_name = bool(re.search(r"(audio|cdrom|cd)", name, re.IGNORECASE))
                if has_track_files or likely_cd_name:
                    drives.append({"id": path, "label": f"{path} (mounted media)", "path": path})
    return drives


def list_cd_drives() -> list[dict]:
    """Return detected CD sources as [{'id','label','path'}]."""
    candidates = _list_windows_cd_drives() if sys.platform == "win32" else _list_posix_cd_devices()
    deduped = []
    seen = set()
    for item in candidates:
        key = item.get("id")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _discover_disc_track_inputs(source: str) -> list[str]:
    source = _normalize_drive_input(source)
    if not source:
        return []

    scan_root = source
    if _looks_like_drive_letter(source):
        scan_root = source

    if not os.path.isdir(scan_root):
        return []

    patterns = [
        "*.cda",
        "*.wav",
        "*.aiff",
        "*.aif",
        "*.flac",
        "*.m4a",
        "*.mp3",
        "*.aac",
        "*.ogg",
        "*.opus",
    ]

    all_tracks = []
    for pattern in patterns:
        all_tracks.extend(glob.glob(os.path.join(scan_root, pattern)))

    if not all_tracks:
        return []

    cda_tracks = [p for p in all_tracks if p.lower().endswith(".cda")]
    if cda_tracks:
        return sorted(cda_tracks, key=_natural_sort_key)

    return sorted(all_tracks, key=_natural_sort_key)


def _safe_track_title(index: int, src_path: str, auto_metadata: bool) -> str:
    fallback = f"Track {index:02d}"
    if not auto_metadata:
        return fallback

    name = os.path.splitext(os.path.basename(src_path))[0]
    name = re.sub(r"^\s*\d+\s*[-_.]\s*", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return fallback
    return name


def _safe_filename_component(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return ""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip().strip(".")
    return value[:120]


def _metadata_text(value: str, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _artist_credit_to_text(artist_credit) -> str:
    if not isinstance(artist_credit, list):
        return ""
    parts = []
    for item in artist_credit:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            name = item.get("name") or item.get("artist", {}).get("name")
            joinphrase = item.get("joinphrase", "")
            if name:
                parts.append(name)
            if joinphrase:
                parts.append(joinphrase)
    return _metadata_text("".join(parts))


def _set_musicbrainz_useragent_once():
    global _MB_USERAGENT_SET
    if _musicbrainzngs is None or _MB_USERAGENT_SET:
        return
    repo_url = f"https://github.com/{GITLAB_REPO}" if GITLAB_REPO else "https://github.com/chefkjd/MixSplitR"
    _musicbrainzngs.set_useragent("MixSplitR", CURRENT_VERSION, repo_url)
    _MB_USERAGENT_SET = True


def _discid_device_candidates(source: str) -> list[Optional[str]]:
    source = _normalize_drive_input(source)
    candidates: list[Optional[str]] = []

    if sys.platform == "win32" and _looks_like_drive_letter(source):
        candidates.extend([source[:2], source[:2] + "\\"])
    elif source.startswith("/dev/"):
        candidates.append(source)
    elif sys.platform == "darwin" and source.startswith("/Volumes/"):
        try:
            result = subprocess.run(
                ["diskutil", "info", source],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=8,
            )
            if result.returncode == 0:
                match = re.search(r"Device Identifier:\s*([^\s]+)", result.stdout)
                if match:
                    ident = match.group(1).strip()
                    # Prefer raw disk device, then regular disk device.
                    if ident:
                        candidates.append(f"/dev/r{ident}")
                        candidates.append(f"/dev/{ident}")
                        base_disk = re.sub(r"s\d+$", "", ident)
                        if base_disk and base_disk != ident:
                            candidates.append(f"/dev/r{base_disk}")
                            candidates.append(f"/dev/{base_disk}")
        except Exception:
            pass

    # Final fallback lets python-discid choose default drive.
    candidates.append(None)

    normalized = []
    seen = set()
    for value in candidates:
        key = value if value is not None else "__default__"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def _read_disc_id(source: str) -> Optional[str]:
    if _discid is None:
        return None
    for candidate in _discid_device_candidates(source):
        try:
            if candidate is None:
                disc = _discid.read()
            else:
                disc = _discid.read(candidate)
            disc_id = getattr(disc, "id", None)
            if disc_id:
                return disc_id
        except Exception:
            continue
    return None


def _extract_release_track_titles(release_obj: dict) -> list[str]:
    titles = []
    for medium in release_obj.get("medium-list", []) or []:
        for trk in medium.get("track-list", []) or []:
            recording = trk.get("recording", {}) if isinstance(trk, dict) else {}
            title = recording.get("title") or trk.get("title")
            title = _metadata_text(title)
            if title:
                titles.append(title)
    return titles


def _fetch_disc_metadata(source: str) -> dict:
    """
    Try Disc ID lookup and return metadata:
      {
        "source": "musicbrainz_discid",
        "disc_id": str,
        "album": str,
        "artist": str,
        "track_titles": [str, ...]
      }
    Returns {} on failure.
    """
    if _discid is None or _musicbrainzngs is None:
        return {}

    disc_id = _read_disc_id(source)
    if not disc_id:
        return {}

    try:
        _set_musicbrainz_useragent_once()
        disc_result = _musicbrainzngs.get_releases_by_discid(
            disc_id,
            includes=["artists", "recordings"],
        )
    except Exception:
        return {}

    disc_data = disc_result.get("disc", {}) if isinstance(disc_result, dict) else {}
    release_list = disc_data.get("release-list", []) if isinstance(disc_data, dict) else []
    if not release_list:
        return {"source": "musicbrainz_discid", "disc_id": disc_id, "track_titles": []}

    release = release_list[0]
    release_id = release.get("id", "")
    album = _metadata_text(release.get("title"), fallback="CD Rip")
    artist = _artist_credit_to_text(release.get("artist-credit")) or "Unknown Artist"

    track_titles = _extract_release_track_titles(release)
    if not track_titles and release_id:
        try:
            full_release = _musicbrainzngs.get_release_by_id(
                release_id,
                includes=["recordings", "artists"],
            )
            release_obj = full_release.get("release", {}) if isinstance(full_release, dict) else {}
            if release_obj:
                album = _metadata_text(release_obj.get("title"), fallback=album)
                rel_artist = _artist_credit_to_text(release_obj.get("artist-credit"))
                if rel_artist:
                    artist = rel_artist
                track_titles = _extract_release_track_titles(release_obj)
        except Exception:
            pass

    return {
        "source": "musicbrainz_discid",
        "disc_id": disc_id,
        "album": album or "CD Rip",
        "artist": artist or "Unknown Artist",
        "track_titles": track_titles,
    }


def _resolve_track_title(
    index: int,
    src_path: str,
    auto_metadata: bool,
    disc_metadata: Optional[dict],
) -> str:
    if auto_metadata and disc_metadata:
        titles = disc_metadata.get("track_titles", [])
        if isinstance(titles, list) and index - 1 < len(titles):
            preferred = _metadata_text(titles[index - 1])
            if preferred:
                return preferred
    return _safe_track_title(index, src_path, auto_metadata=auto_metadata)


def _eject_cd_if_requested(source: str):
    source = _normalize_drive_input(source)
    try:
        if sys.platform == "win32":
            drive = source[:2] if _looks_like_drive_letter(source) else source
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "(New-Object -comObject Shell.Application).NameSpace(17).ParseName('%s').InvokeVerb('Eject')" % drive,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=8)
        elif sys.platform == "darwin":
            subprocess.run(["drutil", "tray", "eject"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=8)
        else:
            cmd = ["eject", source] if source else ["eject"]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=8)
    except Exception:
        pass


def _rip_dry_run(
    output_folder: str,
    ffmpeg_bin: str,
    format_key: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[str]:
    fmt = CD_RIP_FORMATS.get(format_key, CD_RIP_FORMATS["flac"])
    outputs = []
    total = 3
    for idx in range(1, total + 1):
        if progress_callback:
            progress_callback(idx, total, f"Track {idx:02d} (dry-run)")
        out_path = os.path.join(output_folder, f"Track {idx:02d}.{fmt['ext']}")
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "3",
            *fmt["codec_args"],
            out_path,
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
        outputs.append(out_path)
    return outputs


def rip_disc_to_folder(
    source: str,
    output_folder: str,
    output_format: str = "flac",
    auto_metadata: bool = True,
    eject_when_done: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    dry_run: bool = False,
    cancel_flag: Optional[Callable[[], bool]] = None,
    per_track_timeout: int = 120,
    timeout_strategy: str = "skip",
    timeout_callback: Optional[Callable[[int, int, str], None]] = None,
    skip_track_flag: Optional[Callable[[int, str], bool]] = None,
    failure_callback: Optional[Callable[[list[dict]], None]] = None,
) -> list[str]:
    """Rip detected disc tracks into output_folder and return produced file paths."""
    os.makedirs(output_folder, exist_ok=True)
    ffmpeg_bin, _ = setup_ffmpeg()
    format_key = output_format if output_format in CD_RIP_FORMATS else "flac"
    fmt = CD_RIP_FORMATS[format_key]
    timeout_mode = str(timeout_strategy or "skip").strip().lower()
    if timeout_mode not in ("skip", "manual_skip"):
        timeout_mode = "skip"
    per_track_timeout = max(1, int(per_track_timeout or 120))
    manual_skip_poll_seconds = 1.0

    if dry_run:
        outputs = _rip_dry_run(output_folder, ffmpeg_bin, format_key, progress_callback=progress_callback)
        if eject_when_done:
            _eject_cd_if_requested(source)
        return outputs

    tracks = _discover_disc_tracks(source)
    if not tracks:
        raise RuntimeError(
            "No CD tracks were found from the selected source. "
            "Try selecting a mounted CD path/drive root or the Windows drive letter for an Audio CD."
        )

    disc_metadata = {}
    if auto_metadata:
        disc_metadata = _fetch_disc_metadata(source)
        if disc_metadata.get("source") == "musicbrainz_discid":
            print(f"  {Style.DIM}Using MusicBrainz Disc ID metadata: {disc_metadata.get('disc_id', '')}{Style.RESET}")
        elif is_discid_metadata_available():
            print(f"  {Style.DIM}Disc ID metadata unavailable; using local/fallback names.{Style.RESET}")

    # When disc metadata gives us album + artist, rename the output folder
    # to something meaningful instead of the generic CD_Rip_timestamp name.
    if auto_metadata and disc_metadata.get("source") == "musicbrainz_discid":
        artist_part = _safe_filename_component(disc_metadata.get("artist", ""))
        album_part = _safe_filename_component(disc_metadata.get("album", ""))
        if artist_part and album_part:
            named_folder = os.path.join(os.path.dirname(output_folder), f"{artist_part} - {album_part}")
            if not os.path.exists(named_folder):
                try:
                    os.rename(output_folder, named_folder)
                    output_folder = named_folder
                except OSError:
                    pass  # keep the original folder on rename failure
            else:
                output_folder = named_folder
                os.makedirs(output_folder, exist_ok=True)

    outputs = []
    failures = []
    total = len(tracks)
    max_retries = 0 if timeout_mode == "manual_skip" else 1

    def _record_failure(index: int, title: str, src_path: str, reason: str):
        failures.append(
            {
                "index": int(index or 0),
                "title": str(title or "").strip(),
                "source_path": str(src_path or "").strip(),
                "reason": str(reason or "").strip(),
            }
        )
        if failure_callback is not None:
            try:
                failure_callback([dict(item) for item in failures])
            except Exception:
                pass

    for idx, track in enumerate(tracks, 1):
        if cancel_flag and cancel_flag():
            break

        src = str(track.get("source_path") or "").strip()
        title = _resolve_track_title(idx, src, auto_metadata=auto_metadata, disc_metadata=disc_metadata)
        file_title = _safe_filename_component(title)
        # Use the resolved title for naming whenever it's a real name, not
        # just a generic "Track NN" fallback.  This covers titles from
        # MusicBrainz disc-ID lookup *and* meaningful source filenames (e.g.
        # named AIFF tracks on macOS) without gating on the metadata source.
        is_generic = bool(re.match(r"^Track\s*\d+$", file_title, re.IGNORECASE))
        if auto_metadata and file_title and not is_generic:
            out_name = f"{idx:02d} - {file_title}.{fmt['ext']}"
        else:
            out_name = f"Track {idx:02d}.{fmt['ext']}"
        out_path = os.path.join(output_folder, out_name)
        if progress_callback:
            progress_callback(idx, total, title)

        album_name = _metadata_text(disc_metadata.get("album"), fallback="CD Rip")
        artist_name = _metadata_text(disc_metadata.get("artist"), fallback="Unknown Artist")
        title_name = _metadata_text(title, fallback=f"Track {idx:02d}")

        if str(track.get("kind") or "") == "windows_cdda":
            status, reason = _rip_windows_cdda_track(
                source=source,
                track=track,
                ffmpeg_bin=ffmpeg_bin,
                fmt=fmt,
                out_path=out_path,
                idx=idx,
                total=total,
                title=title_name,
                album_name=album_name,
                artist_name=artist_name,
                progress_callback=progress_callback,
                cancel_flag=cancel_flag,
                per_track_timeout=per_track_timeout,
                timeout_mode=timeout_mode,
                timeout_callback=timeout_callback,
                skip_track_flag=skip_track_flag,
            )
            if status == "ok":
                outputs.append(out_path)
                continue
            if status == "cancelled":
                break
            if status == "skipped":
                _record_failure(idx, title, src, reason or "Skipped by user after timeout")
                if progress_callback:
                    progress_callback(idx, total, f"{title} (skipped)")
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                continue

            _record_failure(idx, title, src, reason or "Windows CD audio extraction failed")
            if progress_callback:
                if "timed out" in str(reason or "").lower():
                    progress_callback(idx, total, f"{title} (skipped - timed out)")
                else:
                    progress_callback(idx, total, f"{title} (failed)")
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            continue

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            src,
            "-vn",
            *fmt["codec_args"],
            "-metadata",
            f"title={title_name}",
            "-metadata",
            f"track={idx}",
            "-metadata",
            f"album={album_name}",
            "-metadata",
            f"artist={artist_name}",
            "-metadata",
            "genre=CD Rip",
            out_path,
        ]

        ripped = False
        manual_skip_hard_deadline = (
            time.monotonic() + max(300.0, float(per_track_timeout))
            if timeout_mode == "manual_skip"
            else None
        )
        for attempt in range(1 + max_retries):
            if cancel_flag and cancel_flag():
                break
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                stderr_data = b""
                timed_out = False
                skipped_by_user = False
                cancelled_track = False
                retry_requested = False
                timeout_failure_recorded = False
                try:
                    while True:
                        if cancel_flag and cancel_flag():
                            cancelled_track = True
                            proc.kill()
                            proc.wait()
                            break
                        if timed_out and skip_track_flag and skip_track_flag(idx, title):
                            skipped_by_user = True
                            proc.kill()
                            proc.wait()
                            break
                        if (
                            timed_out
                            and manual_skip_hard_deadline is not None
                            and time.monotonic() >= float(manual_skip_hard_deadline)
                        ):
                            proc.kill()
                            proc.wait()
                            timeout_failure_recorded = True
                            _record_failure(idx, title, src, "Timed out after 5 minutes")
                            if progress_callback:
                                progress_callback(idx, total, f"{title} (skipped - timed out)")
                            break
                        wait_seconds = manual_skip_poll_seconds if timed_out else float(per_track_timeout)
                        try:
                            _, stderr_data = proc.communicate(timeout=wait_seconds)
                            break
                        except subprocess.TimeoutExpired:
                            if timeout_mode == "manual_skip":
                                if not timed_out and timeout_callback:
                                    timeout_callback(idx, total, title)
                                timed_out = True
                                continue
                            proc.kill()
                            proc.wait()
                            if attempt < max_retries:
                                retry_requested = True
                                if progress_callback:
                                    progress_callback(idx, total, f"{title} (retrying...)")
                                # Brief pause before retry to let the drive recover.
                                time.sleep(2)
                                break
                            timeout_failure_recorded = True
                            _record_failure(idx, title, src, "Timed out after retry")
                            if progress_callback:
                                progress_callback(idx, total, f"{title} (skipped - timed out)")
                            break
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait()
                    except Exception:
                        pass
                    raise

                if cancelled_track:
                    break
                if retry_requested:
                    continue
                if timeout_failure_recorded:
                    break
                if skipped_by_user:
                    _record_failure(idx, title, src, "Skipped by user after timeout")
                    if progress_callback:
                        progress_callback(idx, total, f"{title} (skipped)")
                    break

                if proc.returncode != 0:
                    err_msg = (stderr_data or b"").decode("utf-8", errors="replace")[-200:]
                    _record_failure(idx, title, src, f"ffmpeg error: {err_msg}")
                    if progress_callback:
                        progress_callback(idx, total, f"{title} (failed)")
                else:
                    outputs.append(out_path)
                    ripped = True
                break
            except Exception as exc:
                _record_failure(idx, title, src, str(exc))
                if progress_callback:
                    progress_callback(idx, total, f"{title} (failed)")
                break

        # Clean up partial output file on failure
        if not ripped and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass

    if eject_when_done:
        _eject_cd_if_requested(source)

    if not outputs and not (cancel_flag and cancel_flag()):
        raise RuntimeError("Unable to rip tracks from selected source.")

    if failures:
        print(f"\n  {Style.YELLOW}Warning: {len(failures)} track(s) failed to rip.{Style.RESET}")
        first_src = os.path.basename(str((failures[0] or {}).get("source_path") or ""))
        print(f"  {Style.DIM}First failure: {first_src}{Style.RESET}")

    return outputs


def rip_cd_interactive(config: Optional[dict] = None) -> Optional[list[str]]:
    """Interactive CD ripping flow. Returns ripped files or None when cancelled."""
    if config is None:
        config = {}

    if not is_cd_rip_available():
        print(f"\n  {Style.RED}CD ripping is unavailable: ffmpeg not found.{Style.RESET}")
        print(f"  {Style.DIM}Install or bundle ffmpeg/ffprobe and try again.{Style.RESET}")
        return None

    format_key = str(config.get("cd_rip_format", "flac")).strip().lower()
    if format_key not in CD_RIP_FORMATS:
        format_key = "flac"
    auto_metadata = bool(config.get("cd_rip_auto_metadata", True))
    eject_when_done = bool(config.get("cd_rip_eject_when_done", False))
    dry_run = _is_yes(os.environ.get("MIXSPLITR_CD_RIP_DRY_RUN", "0"))

    print(f"\n{Style.CYAN}{'=' * 60}{Style.RESET}")
    print(f"  {Style.BOLD}CD Ripping{Style.RESET}")
    print(f"{Style.CYAN}{'=' * 60}{Style.RESET}")

    drives = list_cd_drives()
    selected_source = ""
    if drives:
        print("\n  Detected CD sources:")
        for idx, drive in enumerate(drives, 1):
            print(f"   {idx}. {drive['label']}")
        print("   M. Enter path manually")
        print("   Enter. Cancel")
        choice = input("\n  Select source: ").strip()
        if not choice:
            return None
        if choice.lower() == "m":
            selected_source = _normalize_drive_input(input("  Enter drive/path: ").strip())
        elif choice.isdigit():
            idx = int(choice)
            if idx < 1 or idx > len(drives):
                print(f"  {Style.YELLOW}Invalid selection.{Style.RESET}")
                return None
            selected_source = drives[idx - 1]["path"]
        else:
            print(f"  {Style.YELLOW}Invalid selection.{Style.RESET}")
            return None
    else:
        print("\n  No CD drives auto-detected.")
        selected_source = _normalize_drive_input(input("  Enter drive/path manually (or Enter to cancel): ").strip())
        if not selected_source:
            return None

    output_root = get_cd_rip_output_directory(config)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    rip_folder = os.path.join(output_root, f"CD_Rip_{stamp}")

    print(f"\n  Source: {selected_source}")
    print(f"  Output: {rip_folder}")
    print(f"  Format: {CD_RIP_FORMATS[format_key]['label']}")
    print(f"  Auto metadata: {'ON' if auto_metadata else 'OFF'}")
    print(f"  Eject when done: {'ON' if eject_when_done else 'OFF'}")
    if dry_run:
        print(f"  {Style.YELLOW}Dry-run mode enabled via MIXSPLITR_CD_RIP_DRY_RUN=1{Style.RESET}")

    confirm = input("\n  Start ripping? (y/n) [y]: ").strip().lower()
    if confirm not in ("", "y", "yes"):
        return None

    def _progress(done: int, total: int, label: str):
        print(f"  Ripping {done}/{total}: {label}")

    try:
        ripped_files = rip_disc_to_folder(
            source=selected_source,
            output_folder=rip_folder,
            output_format=format_key,
            auto_metadata=auto_metadata,
            eject_when_done=eject_when_done,
            progress_callback=_progress,
            dry_run=dry_run,
        )
    except Exception as exc:
        print(f"\n  {Style.RED}CD ripping failed:{Style.RESET} {exc}")
        return None

    if not ripped_files:
        print(f"\n  {Style.YELLOW}No tracks were ripped.{Style.RESET}")
        return None

    print(f"\n  {Style.GREEN}Done: ripped {len(ripped_files)} track(s).{Style.RESET}")
    return ripped_files
