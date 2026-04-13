"""
mixsplitr_record.py
Windows-only "record system audio" feature for MixSplitR.

Implementation note:
- Primary backend: SoundCard (loopback microphone via Windows Media Foundation)
- Optional: sounddevice backend remains as a fallback, but SoundCard is preferred because
  some sounddevice builds do not expose WASAPI loopback controls.

This module is designed to be safe to import even if optional deps aren't installed.
"""

from __future__ import annotations

import os
import sys
import time
import warnings
import select
from datetime import datetime
from pathlib import Path
from typing import Optional

# Menu system (lazy import to avoid circular deps)
try:
    from mixsplitr_menu import MenuItem, select_menu
    _MENU_AVAILABLE = True
except ImportError:
    _MENU_AVAILABLE = False


class ReRecordRequested(Exception):
    """Exception raised when user wants to re-record."""
    pass

# Optional deps (import lazily where possible)
try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

try:
    import soundfile as sf  # type: ignore
except Exception:
    sf = None  # type: ignore

try:
    import soundcard as sc  # type: ignore
except Exception:
    sc = None  # type: ignore

try:
    import sounddevice as sd  # type: ignore
except Exception:
    sd = None  # type: ignore


def _default_cache_dir() -> Path:
    """Get the recordings directory, respecting the user's config if set."""
    try:
        from mixsplitr_core import get_recording_directory
        d = Path(get_recording_directory())
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        pass
    # Fallback if core module unavailable
    base = Path(os.environ.get("APPDATA", Path.home()))
    d = base / "MixSplitR" / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_box(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def _enter_pressed_nonblocking() -> bool:
    """
    Return True when the user presses Enter, without leaving a background
    stdin reader thread running.
    """
    if not sys.stdin or not getattr(sys.stdin, "isatty", lambda: False)():
        return False

    try:
        if os.name == "nt":
            import msvcrt

            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\r", "\n"):
                    return True
            return False

        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return False

        line = sys.stdin.readline()
        return line.endswith("\n") or line == ""
    except Exception:
        return False


def _wait_for_enter_or_escape(prompt: str) -> bool:
    """
    Wait for Enter to continue or Esc to cancel.
    Returns True for Enter, False for Esc/cancel.
    """
    full_prompt = f"{prompt} (Esc to cancel)"

    if not sys.stdin or not getattr(sys.stdin, "isatty", lambda: False)():
        try:
            user_input = input(full_prompt)
        except (KeyboardInterrupt, EOFError):
            return False
        return user_input.strip() == ""

    try:
        if os.name == "nt":
            import msvcrt
            print(full_prompt, end="", flush=True)
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    return True
                if ch == "\x1b":
                    # Drain any arrow-key suffix bytes like "[A".
                    while msvcrt.kbhit():
                        msvcrt.getwch()
                    print()
                    return False
                if ch == "\x03":
                    print()
                    raise KeyboardInterrupt
            # unreachable
        else:
            import tty
            import termios

            print(full_prompt, end="", flush=True)
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        print()
                        return True
                    if ch == "\x1b":
                        # Drain potential escape-sequence tail.
                        while True:
                            ready, _, _ = select.select([sys.stdin], [], [], 0.01)
                            if not ready:
                                break
                            sys.stdin.read(1)
                        print()
                        return False
                    if ch == "\x03":
                        print()
                        raise KeyboardInterrupt
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except (KeyboardInterrupt, EOFError):
        return False


def _deps_ok() -> bool:
    """
    We prefer SoundCard + soundfile. sounddevice is optional fallback.
    """
    missing = []
    if sc is None:
        missing.append("SoundCard")
    if sf is None:
        missing.append("soundfile")

    if missing:
        print()
        print("  System audio recording requires two optional Python packages:")
        for m in missing:
            print(f"   • {m}")
        print()
        print("  Install them, then rebuild:")
        print("   python -m pip install --user SoundCard soundfile")
        print()
        print("  If you see errors mentioning numpy/fromstring, install numpy<2 then rebuild:")
        print("   python -m pip install --user --upgrade --force-reinstall \"numpy<2\"")
        print()
        return False

    return True


def _patch_numpy_fromstring_binary_mode():
    """
    SoundCard 0.4.x calls numpy.fromstring() in *binary mode*.
    NumPy >=2.0 removed that behavior (ValueError: use frombuffer instead).

    We patch numpy.fromstring with a small compatibility wrapper for the duration
    of a recording session, then restore it.
    """
    if np is None:
        return None

    orig = np.fromstring

    def _compat(obj, dtype=float, sep="", count=-1, like=None):  # noqa: ANN001
        try:
            # numpy<2 path
            return orig(obj, dtype=dtype, sep=sep, count=count, like=like)  # type: ignore
        except ValueError as e:
            msg = str(e)
            if "binary mode of fromstring is removed" in msg:
                # Mimic fromstring(binary): frombuffer + copy
                try:
                    arr = np.frombuffer(obj, dtype=dtype, count=count)  # type: ignore
                    return arr.copy()
                except Exception:
                    # Re-raise original error if conversion fails
                    raise e
            raise

    np.fromstring = _compat  # type: ignore
    return orig


def _play_audio_preview(audio_path: Path) -> None:
    """Open audio file in default system player."""
    import subprocess
    try:
        print(f"\n  ▶️  Opening in default player...")
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(audio_path)])
        elif sys.platform == "win32":
            os.startfile(str(audio_path))
        else:
            subprocess.Popen(["xdg-open", str(audio_path)])
        print("  ✓ Opened (playback in external app)")
    except Exception as e:
        print(f"  ⚠️  Could not open preview: {e}")


def _post_recording_action_loop(out_path: Path) -> Optional[Path]:
    """Single post-recording action menu used by all recording backends."""
    while True:
        items = [
            MenuItem("keep", "✅", "Keep & continue", "Process this recording now"),
            MenuItem("preview", "▶️", "Preview / Listen", "Play back what was recorded"),
            MenuItem("save", "💾", "Save for later", "Save to Music folder without processing"),
            MenuItem("rerecord", "🗑️", "Delete & re-record", "Start over with a new recording"),
            MenuItem("cancel", "🗑️", "Delete & go back", "Delete recording and return to menu"),
        ]
        result = select_menu("Recording Complete", items, subtitle=f"Saved: {out_path.name}")

        if result.cancelled or result.key == "cancel":
            try:
                out_path.unlink()
                print("  🗑️  Deleted recording")
            except Exception:
                pass
            return None
        if result.key == "keep":
            return out_path
        if result.key == "preview":
            _play_audio_preview(out_path)
            continue
        if result.key == "save":
            save_path = _save_recording_for_later(out_path)
            if save_path:
                print("\n  ✓ Recording saved. Returning to menu...")
                return None
            continue
        if result.key == "rerecord":
            try:
                out_path.unlink()
                print("  🗑️  Deleted recording")
            except Exception:
                pass
            print("\n  🔄 Starting new recording...\n")
            raise ReRecordRequested()


def _save_recording_for_later(audio_path: Path) -> Optional[Path]:
    """Save recording to user-specified location."""
    import shutil

    # Default save location — use configured recording folder if set
    try:
        from mixsplitr_core import get_recording_directory
        default_dir = Path(get_recording_directory())
    except Exception:
        if sys.platform in ("darwin", "win32"):
            default_dir = Path.home() / "Music"
        else:
            default_dir = Path.home()

    default_dir.mkdir(parents=True, exist_ok=True)
    default_name = audio_path.name

    print(f"\n  💾 Save Recording")
    print(f"  Default location: {default_dir}")
    print(f"  Default filename: {default_name}")

    custom = input(f"\n  Enter filename (or full path), [Enter] for default: ").strip()

    if custom:
        save_path = Path(custom)
        # If just a filename, use default dir
        if not save_path.is_absolute():
            save_path = default_dir / custom
        # Add .wav if no extension
        if not save_path.suffix:
            save_path = save_path.with_suffix('.wav')
    else:
        save_path = default_dir / default_name

    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(audio_path, save_path)
        # Clean up temp file
        audio_path.unlink()
        print(f"\n  ✅ Saved: {save_path.name}")
        print(f"  📁 Location: {save_path.parent}")
        return save_path
    except Exception as e:
        print(f"\n  ❌ Failed to save: {e}")
        return None


def _can_use_for_loopback(speaker) -> bool:
    """Test if a speaker device supports loopback recording."""
    try:
        # Try to get loopback microphone for this speaker
        sc.get_microphone(speaker.name, include_loopback=True)
        return True
    except Exception:
        return False


def _find_blackhole_input():
    """Find BlackHole as an input device on macOS."""
    if sc is None:
        return None

    try:
        mics = list(sc.all_microphones())
        for mic in mics:
            name_lower = mic.name.lower()
            if "blackhole" in name_lower:
                return mic
    except Exception:
        pass
    return None


def _get_device_samplerate(mic) -> int:
    """Get the native sample rate of an audio device."""
    # Try to get sample rate from device properties
    try:
        # soundcard exposes this on some platforms
        if hasattr(mic, 'samplerate'):
            return int(mic.samplerate)
    except Exception:
        pass

    # macOS: Use CoreAudio to query the device sample rate
    if sys.platform == "darwin":
        try:
            import subprocess
            # Get the sample rate from Audio MIDI Setup via system_profiler
            result = subprocess.run(
                ["system_profiler", "SPAudioDataType", "-json"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                # Search for BlackHole device info
                audio_data = data.get("SPAudioDataType", [])
                for item in audio_data:
                    items = item.get("_items", [])
                    for device in items:
                        name = device.get("_name", "").lower()
                        if "blackhole" in name:
                            # Try to extract sample rate
                            sr = device.get("coreaudio_default_audio_input_device_samplerate")
                            if sr:
                                return int(float(sr.replace(" Hz", "").replace(",", "")))
        except Exception:
            pass

    # Default fallback - try to detect by recording a tiny sample
    try:
        # Record 1 frame and check what we get
        test_data = mic.record(numframes=1024, samplerate=48000)
        # If it worked without error, 48000 is likely supported
        return 48000
    except Exception:
        pass

    try:
        test_data = mic.record(numframes=1024, samplerate=44100)
        return 44100
    except Exception:
        pass

    return 44100  # Final fallback


def _choose_speaker_soundcard() -> Optional[object]:
    assert sc is not None

    speakers = list(sc.all_speakers())
    if not speakers:
        print("  ❌ No output devices (speakers) found.")
        return None

    # On Mac, filter to Multi-Output/Aggregate devices
    if sys.platform == "darwin":
        # First try name-based detection (most reliable for user-created multi-output devices)
        name_filtered = [sp for sp in speakers if
                        ("multi" in sp.name.lower() or "aggregate" in sp.name.lower()) and
                        sp.name.lower().strip() not in ["blackhole 2ch", "blackhole 16ch"]]

        if name_filtered:
            speakers = name_filtered
        else:
            # Fallback: test loopback capability (may not work reliably on all macOS versions)
            valid_speakers = [sp for sp in speakers if _can_use_for_loopback(sp)]
            valid_speakers = [sp for sp in valid_speakers if
                            sp.name.lower().strip() not in ["blackhole 2ch", "blackhole 16ch"]]

            if not valid_speakers:
                return None  # Will be caught by caller
            speakers = valid_speakers

    default = sc.default_speaker()
    default_idx = 0
    for i, sp in enumerate(speakers):
        if getattr(sp, "name", None) == getattr(default, "name", None):
            default_idx = i
            break

    print("  Output device (loopback capture):")
    for i, sp in enumerate(speakers, start=1):
        ch = getattr(sp, "channels", "?")
        mark = " (default)" if (i - 1) == default_idx else ""
        print(f"   {i}. {sp.name} — {ch}ch{mark}")

    sel = input(f"\n  Choose device [Enter=default, 1-{len(speakers)}]: ").strip()
    if not sel:
        return speakers[0] if speakers else default

    try:
        idx = int(sel)
        if 1 <= idx <= len(speakers):
            return speakers[idx - 1]
    except Exception:
        pass

    print("  ⚠️ Invalid selection; using default.")
    return speakers[0] if speakers else default


def _get_blackhole_ffmpeg_index() -> Optional[int]:
    """Get the ffmpeg device index for BlackHole on macOS."""
    import subprocess
    try:
        # List audio devices using ffmpeg
        result = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10
        )
        # Parse stderr for device list (ffmpeg outputs to stderr)
        output = result.stderr
        lines = output.split('\n')
        in_audio_section = False
        for line in lines:
            if "AVFoundation audio devices:" in line:
                in_audio_section = True
                continue
            if in_audio_section:
                if "blackhole" in line.lower():
                    # Extract index like "[0]" or "[1]"
                    import re
                    match = re.search(r'\[(\d+)\]', line)
                    if match:
                        return int(match.group(1))
    except Exception:
        pass
    return None


def _get_audio_device_info() -> dict:
    """Get detailed audio device info from macOS."""
    import subprocess
    info = {"blackhole_sr": None, "multi_sr": None, "all_devices": []}

    try:
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            audio_data = data.get("SPAudioDataType", [])
            for item in audio_data:
                items = item.get("_items", [])
                for device in items:
                    name = device.get("_name", "")
                    info["all_devices"].append(name)
                    name_lower = name.lower()

                    # Extract sample rate from any key containing "samplerate"
                    sr = None
                    for key, val in device.items():
                        if "samplerate" in key.lower():
                            sr_str = str(val).replace(" Hz", "").replace(",", "").strip()
                            try:
                                sr = int(float(sr_str))
                            except ValueError:
                                pass

                    if "blackhole" in name_lower and sr:
                        info["blackhole_sr"] = sr
                    if "multi" in name_lower and sr:
                        info["multi_sr"] = sr
    except Exception:
        pass

    return info



def _record_with_ffmpeg_interactive(out_path: Path, device_index: int):
    """Record using ffmpeg/sox with interactive start/stop UI."""
    import subprocess
    import shutil

    # Get device info for diagnostics
    dev_info = _get_audio_device_info()
    device_sr = dev_info.get("multi_sr") or dev_info.get("blackhole_sr") or 48000

    # Check if sox is available (often handles CoreAudio better)
    use_sox = shutil.which("sox") is not None and shutil.which("rec") is not None

    print(f"  📍 Recording via {'sox' if use_sox else 'ffmpeg'} (device index: {device_index})")
    print(f"  📊 BlackHole sample rate: {dev_info.get('blackhole_sr', 'unknown')} Hz")
    print(f"  📊 Multi-Output sample rate: {dev_info.get('multi_sr', 'unknown')} Hz")

    # Warn about sample rate mismatch
    if dev_info.get("blackhole_sr") and dev_info.get("multi_sr"):
        if dev_info["blackhole_sr"] != dev_info["multi_sr"]:
            print(f"\n  ⚠️  SAMPLE RATE MISMATCH DETECTED!")
            print(f"      Open Audio MIDI Setup and set both devices to the same rate.")
    print("\n  Tip: Start playback before you press Enter to start recording.\n")
    print("  Controls:")
    print("   • Press Enter to START recording")
    print("   • Press Enter again to STOP recording\n")

    if not _wait_for_enter_or_escape("  Press Enter to start..."):
        print("\n  ↩ Cancelled. Returning to menu...")
        return None

    if use_sox:
        # Use sox 'rec' command - better CoreAudio handling
        # AUDIODEV environment variable sets the input device
        env = os.environ.copy()
        env["AUDIODEV"] = "BlackHole 16ch"
        cmd = [
            "rec",
            "-c", "2",  # stereo output
            "-r", str(device_sr),
            "-b", "16",  # 16-bit
            str(out_path)
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )
    else:
        # Fallback to ffmpeg
        # Key flags for avoiding choppy audio:
        # -use_wallclock_as_timestamps: sync to wall clock, not device clock
        # -probesize/-analyzeduration: reduce startup delay
        # aresample async: handle any timestamp discontinuities
        cmd = [
            "ffmpeg", "-y",
            "-f", "avfoundation",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-use_wallclock_as_timestamps", "1",
            "-i", f":{device_index}",
            "-af", "aresample=async=1:first_pts=0",
            "-ac", "2",
            "-ar", "48000",
            "-acodec", "pcm_s16le",
            str(out_path)
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    print("\n  🔴 Recording... (press Enter to stop)\n")

    # Check if recording started successfully (give it a moment)
    time.sleep(0.5)
    if proc.poll() is not None:
        # Process already exited - probably an error
        stderr_out = proc.stderr.read() if proc.stderr else b''
        err_msg = stderr_out.decode('utf-8', errors='ignore')
        err_lines = [l for l in err_msg.split('\n') if l.strip()][-8:]
        raise RuntimeError(f"ffmpeg failed to start:\n  {chr(10).join(err_lines)}")

    # Wait for user to press Enter
    input()

    # Stop recording gracefully
    import signal
    try:
        if use_sox:
            # sox uses SIGINT to stop cleanly
            proc.send_signal(signal.SIGINT)
        else:
            # ffmpeg uses 'q' key
            proc.stdin.write(b'q')
            proc.stdin.flush()
    except Exception:
        pass

    # Read stderr before waiting
    stderr_out = b''
    try:
        proc.wait(timeout=5)
        stderr_out = proc.stderr.read() if proc.stderr else b''
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=2)
        stderr_out = proc.stderr.read() if proc.stderr else b''

    if not out_path.exists() or out_path.stat().st_size < 1000:
        err_msg = stderr_out.decode('utf-8', errors='ignore') if stderr_out else ""
        # Show last few lines of error
        err_lines = [l for l in err_msg.split('\n') if l.strip()][-5:]
        raise RuntimeError(f"Recording failed\n  ffmpeg: {chr(10).join(err_lines)}")

    # Analyze the recorded file
    try:
        probe_result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,duration",
             "-of", "default=noprint_wrappers=1", str(out_path)],
            capture_output=True, text=True, timeout=5
        )
        if probe_result.returncode == 0:
            print(f"\n  📊 File analysis: {probe_result.stdout.strip().replace(chr(10), ', ')}")
    except Exception:
        pass

    print(f"\n  ✅ Saved recording: {out_path.name}")
    return _post_recording_action_loop(out_path)


def _record_loopback_soundcard(speaker, out_path: Path, samplerate: int = None):
    assert sc is not None and sf is not None

    # macOS: Skip ffmpeg for now - soundcard with proper settings works better
    # if sys.platform == "darwin":
    #     ffmpeg_index = _get_blackhole_ffmpeg_index()
    #     if ffmpeg_index is not None:
    #         return _record_with_ffmpeg_interactive(out_path, ffmpeg_index)

    mic = None

    # macOS: Use BlackHole INPUT directly (loopback not supported on CoreAudio)
    if sys.platform == "darwin":
        mic = _find_blackhole_input()
        if mic is None:
            raise RuntimeError(
                "Cannot find BlackHole input device.\n\n"
                "    BlackHole must be installed and your Multi-Output Device must include it.\n\n"
                "    Setup steps:\n"
                "    1. Install BlackHole: brew install blackhole-2ch\n"
                "       Or download from: https://github.com/ExistentialAudio/BlackHole/releases\n\n"
                "    2. In Audio MIDI Setup, your Multi-Output Device should have:\n"
                "       ☑ BlackHole 2ch (recommended; 16ch also works)\n"
                "       ☑ Your speakers/headphones\n\n"
                "    3. Set Multi-Output Device as system output in System Settings → Sound"
            )
        print(f"  📍 Recording from: {mic.name}")

    # Windows: Use WASAPI loopback
    else:
        try:
            mic = sc.get_microphone(speaker.name, include_loopback=True)
        except Exception as e:
            raise RuntimeError(f"Unable to create loopback device for '{speaker.name}': {e}")

    # Auto-detect sample rate if not specified
    if samplerate is None:
        samplerate = _get_device_samplerate(mic)
        print(f"  📊 Detected sample rate: {samplerate} Hz")

    input_channels = getattr(mic, "channels", None) or 2
    # Always save as stereo for compatibility
    output_channels = 2

    print(f"  📊 Input: {input_channels}ch → Output: stereo")
    print("\n  Tip: Start playback before you press Enter to start recording.\n")
    print("  Controls:")
    print("   • Press Enter to START recording")
    print("   • Press Enter again to STOP recording\n")

    if not _wait_for_enter_or_escape("  Press Enter to start..."):
        print("\n  ↩ Cancelled. Returning to menu...")
        return None

    # Smaller chunks = more responsive but need fast writes
    # 0.1s chunks at 48kHz = 4800 frames - good balance
    chunk_seconds = 0.1
    chunk_frames = max(1, int(samplerate * chunk_seconds))

    # Patch numpy.fromstring for SoundCard + numpy>=2.0 compatibility
    orig_fromstring = _patch_numpy_fromstring_binary_mode()

    try:
        print("\n  🔴 Recording... (press Enter to stop)")
        print("      [Signal meter: . = silence, # = audio detected]")
        print("      [Auto-stop: 10 seconds of silence]\n")
        frame_count = 0
        silence_frames = 0
        silence_threshold_frames = int(10.0 / chunk_seconds)  # 10 seconds worth of frames

        # SoundCard's loopback backend emits SoundcardRuntimeWarning ("data
        # discontinuity") when a capture chunk arrives late.  On loopback
        # devices this is normal and the gaps are sub-ms / inaudible.
        # Suppress only that warning for the duration of the recording loop.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning,
                                    message=".*discontinuity.*")

            # soundfile streams to disk
            with sf.SoundFile(
                str(out_path),
                mode="w",
                samplerate=samplerate,
                channels=output_channels,
                format="WAV",
                subtype="PCM_16",
            ) as f:
                # Use recorder context for continuous streaming (avoids gaps)
                with mic.recorder(samplerate=samplerate, channels=input_channels) as recorder:
                    while True:
                        if _enter_pressed_nonblocking():
                            print("\n\n  ⏹️  Stopped by user", flush=True)
                            break

                        try:
                            data = recorder.record(numframes=chunk_frames)
                        except Exception as e:
                            raise RuntimeError(f"Recording error: {e}")

                        # Ensure 2D shape
                        if getattr(data, "ndim", 1) == 1:
                            data = data.reshape(-1, 1)

                        # Mix down to stereo if needed
                        if data.shape[1] > 2:
                            # Take first 2 channels (standard stereo routing)
                            data = data[:, :2].copy()
                        elif data.shape[1] == 1:
                            # Mono to stereo
                            data = np.column_stack([data, data])

                        # Signal level indicator and silence detection
                        frame_count += 1
                        peak = np.max(np.abs(data))

                        if peak > 0.01:
                            # Audio detected - reset silence counter
                            silence_frames = 0
                            if frame_count % 10 == 0:  # every ~1 second
                                print("#", end="", flush=True)
                        else:
                            # Silence detected - increment counter
                            silence_frames += 1
                            if frame_count % 10 == 0:  # every ~1 second
                                print(".", end="", flush=True)

                            # Auto-stop after 10 seconds of silence
                            if silence_frames >= silence_threshold_frames:
                                print("\n\n  ⏹️  Auto-stopped: 10 seconds of silence detected", flush=True)
                                break

                        f.write(data)

        # Analyze the recorded file
        import subprocess as sp
        try:
            probe = sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,duration",
                 "-of", "default=noprint_wrappers=1", str(out_path)],
                capture_output=True, text=True, timeout=5
            )
            if probe.returncode == 0 and probe.stdout.strip():
                print(f"\n  📊 File: {probe.stdout.strip().replace(chr(10), ', ')}")
        except Exception:
            pass

        print(f"  ✅ Saved recording: {out_path.name}")
        print(f"  📁 Location: {out_path.parent}\n")
        return _post_recording_action_loop(out_path)

    finally:
        # Restore numpy.fromstring if we patched it
        if orig_fromstring is not None and np is not None:
            np.fromstring = orig_fromstring  # type: ignore


def _record_system_audio_windows() -> Optional[Path]:
    """
    Returns a path to a WAV recording, or None if cancelled/failed.
    Works on both Windows (WASAPI) and macOS (CoreAudio) via soundcard library.
    """
    platform_name = "macOS" if sys.platform == "darwin" else "Windows"
    _print_box(f"🎙 RECORD SYSTEM AUDIO ({platform_name})")

    # Mac-specific: Check for BlackHole input device
    if sys.platform == "darwin" and sc is not None:
        blackhole_input = _find_blackhole_input()

        if blackhole_input is None:
            print("  ❌ BlackHole is NOT installed or not detected")
            print()
            print("  macOS recording requires BlackHole virtual audio driver.")
            print()
        print("  📥 1. Install BlackHole:")
        print("     brew install blackhole-2ch")
        print("     Or download: https://github.com/ExistentialAudio/BlackHole/releases")
        print()
        print("  ⚙️  2. Create Multi-Output Device in Audio MIDI Setup:")
        print("     1. Open Audio MIDI Setup app")
        print("     2. Click + → Create Multi-Output Device")
        print("     3. Check: ☑ BlackHole 2ch (recommended)  ☑ Your speakers")
        print("     4. Set Multi-Output as system output in System Settings → Sound")
        print()
        print("  This routes audio to both speakers AND BlackHole for recording.")
        print()
        input("  Press Enter to return to menu...")
        return None

        print(f"  ✓ Found BlackHole: {blackhole_input.name}")

    print("  This will record the system output ('what you hear').")
    print("  You can choose which output device to capture (default is your current output).")

    if sys.platform == "darwin":
        print()
        print("  ⚠️  Verify your Multi-Output Device setup in Audio MIDI Setup:")
        print("     • Must include BlackHole 2ch (recommended; 16ch also works) as a checked device")
        print("     • BlackHole should be the PRIMARY device (top of the list)")
        print("     • Try moving BlackHole above other devices if recording fails")
        print()

    # Preferred backend: SoundCard
    if sc is not None and sf is not None:
        # macOS: we record from BlackHole input directly, no speaker selection needed
        if sys.platform == "darwin":
            speaker = None  # Not used on Mac
        else:
            speaker = _choose_speaker_soundcard()
            if speaker is None:
                print("  ❌ No valid recording device found.")
                input("  Press Enter to return to menu...")
                return None

        cache_dir = _default_cache_dir()
        print(f"\n  📁 Recordings will be saved to: {cache_dir}\n")

        # Recording loop (for re-record functionality)
        while True:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_path = cache_dir / f"MixSplitR_recording_{ts}.wav"

            try:
                # Auto-detect sample rate from device
                result = _record_loopback_soundcard(speaker, out_path)
                return result  # Success or cancel
            except ReRecordRequested:
                # User wants to re-record, loop continues
                continue
            except PermissionError:
                print(f"\n  {chr(10060)} Permission Denied")
                print(f"\n  macOS requires explicit permission for audio recording.")
                print(f"\n  Grant microphone access:")
                print(f"  1. Open System Settings")
                print(f"  2. Go to Privacy & Security → Microphone")
                print(f"  3. Enable for Terminal (or MixSplitR)")
                print(f"  4. Try recording again")
                print(f"\n  Tip: You only need to do this once.\n")
                input("  Press Enter to continue...")
                return None
            except Exception as e:
                raise RuntimeError(str(e))

    # Fallback (best-effort): sounddevice
    if sd is not None and sf is not None:
        raise RuntimeError(
            "SoundCard is not installed; sounddevice fallback is not enabled "
            "because your sounddevice build does not expose WASAPI loopback reliably. "
            "Install SoundCard + soundfile and rebuild:\n"
            "  python -m pip install --user SoundCard soundfile"
        )

    raise RuntimeError("Recording deps not installed. Install SoundCard + soundfile and rebuild.")


def record_system_audio_and_return_path() -> Optional[str]:
    """
    Entry point used by MixSplitR.

    Returns:
        str path to recorded WAV on success
        None on cancel/failure
    """
    if sys.platform not in ("win32", "darwin"):
        print("\n  System audio recording is available on Windows and macOS only.")
        print("  Linux: use PulseAudio/PipeWire monitor sources.\n")
        input("Press Enter to return...")
        return None

    if not _deps_ok():
        input("Press Enter to return...")
        return None

    try:
        # Use same recording function for both Windows and Mac
        # (function name is historical - it works on both platforms via soundcard)
        p = _record_system_audio_windows()
        if p is None:
            return None
        return str(p)
    except Exception as e:
        print(f"\n  ❌ Recording failed: {e}\n")
        input("Press Enter to return...")
        return None


# Backwards-compatible alias
def record_system_audio_interactive():
    return record_system_audio_and_return_path()
