"""
mixsplitr_identify.py - Track identification for MixSplitR

Contains:
- AcoustID/MusicBrainz identification
- Enhanced metadata lookup from MusicBrainz
- Result merging from multiple sources
- Artwork batch downloading
"""

import os
import re
import threading
import time
import subprocess
import tempfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from mixsplitr_utils import windows_hidden_subprocess_kwargs as _windows_hidden_subprocess_kwargs
from mixsplitr_utils import get_runtime_temp_directory as _get_runtime_temp_directory

# MusicBrainz/AcoustID availability flags
_ACOUSTID_AVAILABLE = False
_MB_AVAILABLE = False
_acoustid = None
_musicbrainzngs = None
_ACOUSTID_API_KEY = None
_ACOUSTID_DEBUG = os.environ.get("MIXSPLITR_DEBUG_ACOUSTID", "").strip().lower() in ("1", "true", "yes", "y", "on")
_ACOUSTID_TRACE = os.environ.get("MIXSPLITR_TRACE_ACOUSTID", "").strip().lower() in ("1", "true", "yes", "y", "on")
_default_key_warning_shown = False

# Shazam availability flags
_SHAZAM_AVAILABLE = False
_shazamio = None
_SHAZAM_DEBUG = os.environ.get("MIXSPLITR_DEBUG_SHAZAM", "").strip().lower() in ("1", "true", "yes", "y", "on")
_SHAZAM_TRACE = os.environ.get("MIXSPLITR_TRACE_SHAZAM", "").strip().lower() in ("1", "true", "yes", "y", "on")
_SHAZAM_MISSING_WARNED = False

# ACRCloud trace flag
_ACRCLOUD_TRACE = os.environ.get("MIXSPLITR_TRACE_ACRCLOUD", "").strip().lower() in ("1", "true", "yes", "y", "on")

# Unified trace flag (enables trace for ALL backends)
_TRACE_ALL = os.environ.get("MIXSPLITR_TRACE", "").strip().lower() in ("1", "true", "yes", "y", "on")

# AcoustID rate limiter — shared across all threads so parallel workers don't
# exceed the free API limit (3 req/s).  Initialised lazily on first use.
_acoustid_rate_limiter = None
_acoustid_rate_limiter_lock = threading.Lock()

def is_trace_enabled():
    """Check if any trace mode is enabled (unified or per-backend)"""
    return _TRACE_ALL or _SHAZAM_TRACE or _ACOUSTID_TRACE or _ACRCLOUD_TRACE


def _get_acoustid_rate_limiter():
    """Return the shared AcoustID rate limiter, creating it on first call.

    AcoustID's free API is capped at 3 requests/second.  A single shared
    limiter across all worker threads prevents silent empty-result responses
    that the API sends when the rate limit is exceeded without a proper error.
    """
    global _acoustid_rate_limiter
    if _acoustid_rate_limiter is None:
        with _acoustid_rate_limiter_lock:
            if _acoustid_rate_limiter is None:
                try:
                    from mixsplitr_core import RateLimiter  # lazy to avoid import cycles
                    _acoustid_rate_limiter = RateLimiter(requests_per_second=3)
                except Exception:
                    # Fallback: simple inline limiter matching RateLimiter behaviour
                    class _SimpleRL:
                        def __init__(self):
                            self.delay = 1.0 / 3
                            self.last_request = 0.0
                            self.lock = threading.Lock()
                        def wait(self):
                            sleep_dur = 0.0
                            with self.lock:
                                now = time.time()
                                gap = now - self.last_request
                                if gap < self.delay:
                                    sleep_dur = self.delay - gap
                                self.last_request = now + sleep_dur
                            if sleep_dur > 0:
                                time.sleep(sleep_dur)
                    _acoustid_rate_limiter = _SimpleRL()
    return _acoustid_rate_limiter


def setup_musicbrainz(version, repo):
    """Initialize MusicBrainz client (no account required).

    This sets a proper User-Agent and enables text-search lookups for metadata.
    AcoustID fingerprinting is optional and only enabled if pyacoustid is installed.
    Shazam fingerprinting is optional and only enabled if shazamio is installed.
    """
    global _ACOUSTID_AVAILABLE, _MB_AVAILABLE, _acoustid, _musicbrainzngs
    global _SHAZAM_AVAILABLE, _shazamio

    _ACOUSTID_AVAILABLE = False
    _MB_AVAILABLE = False
    _SHAZAM_AVAILABLE = False
    _acoustid = None
    _musicbrainzngs = None
    _shazamio = None

    try:
        import musicbrainzngs
        _musicbrainzngs = musicbrainzngs
        _MB_AVAILABLE = True

        # Use GitLab repo URL for User-Agent contact info
        _musicbrainzngs.set_useragent("MixSplitR", version, f"https://gitlab.com/{repo}")
    except ImportError:
        _MB_AVAILABLE = False
        _musicbrainzngs = None
        # IMPORTANT: do NOT return here; Shazam can still be initialized below.

    # Optional: AcoustID (fingerprinting) support if installed.
    try:
        import acoustid
        _acoustid = acoustid
        _ACOUSTID_AVAILABLE = True
    except ImportError:
        _ACOUSTID_AVAILABLE = False

    # Optional: Shazam (fingerprinting) support if installed.
    try:
        from shazamio import Shazam
        _shazamio = Shazam
        _SHAZAM_AVAILABLE = True
    except ImportError:
        _SHAZAM_AVAILABLE = False

    return _MB_AVAILABLE or _ACOUSTID_AVAILABLE or _SHAZAM_AVAILABLE


def set_acoustid_api_key(key):
    """Set a custom AcoustID API key"""
    global _ACOUSTID_API_KEY
    _ACOUSTID_API_KEY = key


def get_acoustid_api_key():
    """Get the current AcoustID API key
    
    Returns the user-configured key, or None if not set.
    NO DEFAULT KEY - users must provide their own.
    """
    global _ACOUSTID_API_KEY
    return _ACOUSTID_API_KEY


def check_chromaprint_available():
    """
    Check if chromaprint/fpcalc is available.
    Handles PyInstaller onefile mode where fpcalc is extracted to _MEIPASS temp folder.
    """
    import shutil
    import sys
    
    # PRIORITY 1: Check PyInstaller temporary extraction folder (onefile mode)
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller onefile - files extracted to _MEIPASS temp folder
        temp_dir = sys._MEIPASS
        
        if sys.platform == 'win32':
            fpcalc_name = 'fpcalc.exe'
        else:
            fpcalc_name = 'fpcalc'
        
        temp_fpcalc = os.path.join(temp_dir, fpcalc_name)
        
        if os.path.exists(temp_fpcalc):
            # Found in PyInstaller temp extraction folder
            os.environ['FPCALC'] = temp_fpcalc
            return (True, temp_fpcalc)
    
    # PRIORITY 2: Check if running as compiled executable (onefolder mode)
    if getattr(sys, 'frozen', False):
        # Running as compiled executable (but not onefile, or _MEIPASS not set)
        exe_dir = os.path.dirname(sys.executable)
        
        if sys.platform == 'win32':
            bundled_fpcalc = os.path.join(exe_dir, 'fpcalc.exe')
        else:
            bundled_fpcalc = os.path.join(exe_dir, 'fpcalc')
        
        if os.path.exists(bundled_fpcalc):
            # Found next to executable
            os.environ['FPCALC'] = bundled_fpcalc
            return (True, bundled_fpcalc)
    
    # PRIORITY 3: Check system PATH
    fpcalc_path = shutil.which('fpcalc')
    if fpcalc_path:
        return (True, fpcalc_path)
    
    # PRIORITY 4: Try common Windows locations
    if os.name == 'nt':
        common_paths = [
            r'C:\Program Files\Chromaprint\fpcalc.exe',
            r'C:\Program Files (x86)\Chromaprint\fpcalc.exe',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fpcalc.exe'),
            os.path.join(os.getcwd(), 'fpcalc.exe'),
        ]
        for path in common_paths:
            if os.path.exists(path):
                os.environ['FPCALC'] = path
                return (True, path)
    
    # Not found anywhere
    return (False, None)


def is_acoustid_available():
    """Check if AcoustID/MusicBrainz is available"""
    return _ACOUSTID_AVAILABLE


def _new_runtime_temp_wav(prefix="mixsplitr_tmp") -> str:
    """Create a unique .wav path under the runtime temp directory."""
    temp_dir = _get_runtime_temp_directory()
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except Exception:
        pass
    safe_prefix = f"{str(prefix or 'mixsplitr_tmp').strip('_')}_"
    fd, temp_path = tempfile.mkstemp(prefix=safe_prefix, suffix=".wav", dir=temp_dir)
    os.close(fd)
    return temp_path


def _fingerprint_file_hidden_windows(audio_file, max_length_seconds=120):
    """Generate an AcoustID fingerprint using fpcalc with hidden window flags."""
    ok, fpcalc_path = check_chromaprint_available()
    if not ok or not fpcalc_path:
        raise RuntimeError("fpcalc executable not found")

    cmd = [str(fpcalc_path), "-length", str(int(max_length_seconds)), str(audio_file)]
    run_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "check": True,
    }
    run_kwargs.update(_windows_hidden_subprocess_kwargs())
    result = subprocess.run(cmd, **run_kwargs)

    duration = None
    fingerprint = None
    combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
    for raw_line in combined_output.splitlines():
        line = str(raw_line or "").strip()
        if line.startswith("DURATION="):
            try:
                duration = float(line.split("=", 1)[1].strip())
            except Exception:
                duration = None
        elif line.startswith("FINGERPRINT="):
            fingerprint = line.split("=", 1)[1].strip()

    if duration is None or not fingerprint:
        raise RuntimeError("fpcalc did not return a valid fingerprint payload")
    return duration, fingerprint


def is_musicbrainz_available():
    """Check if MusicBrainz (musicbrainzngs) is available"""
    return _MB_AVAILABLE


def is_shazam_available():
    """Check if Shazam (shazamio) is available"""
    return _SHAZAM_AVAILABLE


def print_id_winner(track_num, winner_backend, artist=None, title=None):
    """Print one-line summary of which backend won identification.

    Args:
        track_num: Track number/index for display
        winner_backend: 'acrcloud' | 'shazam' | 'acoustid' | 'none'
        artist: Artist name (optional, for display)
        title: Track title (optional, for display)
    """
    backend_display = winner_backend.lower() if winner_backend else 'none'
    if artist and title:
        print(f"  Track {track_num}: ID: {backend_display} → {artist} - {title}")
    else:
        print(f"  Track {track_num}: ID: {backend_display}")


# =============================================================================
# ACOUSTID IDENTIFICATION
# =============================================================================

def identify_with_acoustid(audio_chunk, sample_path=None, hint_artist=None, hint_title=None):
    """Fallback identification using AcoustID/MusicBrainz

    Args:
        audio_chunk: pydub AudioSegment to fingerprint.
        sample_path: optional path to an already-exported WAV sample.
                     When provided, the function uses this file directly
                     instead of re-exporting the full chunk, which saves
                     significant I/O for long audio segments.
        hint_artist: optional artist name from a higher-confidence source
                     (e.g. Shazam).  When provided, the function prefers
                     AcoustID recordings that match the hint so the caller
                     gets the correct MusicBrainz recording_id.
        hint_title:  optional title from a higher-confidence source.

    Returns:
        dict with artist, title, recording_id, score if found
        None if not found or error occurred
    """
    if not _ACOUSTID_AVAILABLE:
        return None

    # Check if API key is configured
    api_key = get_acoustid_api_key()
    if not api_key:
        # No key configured - show error once per session
        global _default_key_warning_shown
        if not _default_key_warning_shown:
            print(f"  [AcoustID] ⚠️  No API key configured")
            print(f"             AcoustID fingerprinting requires a free API key.")
            print(f"             Get one at: https://acoustid.org/api-key")
            print(f"             Add it via: Main Menu → Option 5 → Manage API Keys")
            _default_key_warning_shown = True
        return None

    temp_file = None
    owns_temp = False
    try:
        if sample_path and os.path.isfile(sample_path):
            temp_file = sample_path
        else:
            temp_file = _new_runtime_temp_wav(
                f"temp_acoustid_{threading.current_thread().ident}"
            )
            audio_chunk.export(temp_file, format="wav")
            owns_temp = True
        
        if _ACOUSTID_DEBUG:
            print(f"  [AcoustID] Fingerprinting audio chunk (API key: {api_key[:4]}...)")
        if _ACOUSTID_TRACE or _TRACE_ALL:
            print(f"  [AcoustID] attempted...")

        # Step 1: Generate fingerprint via fpcalc and log the duration so we can
        # verify the exported WAV actually contains real audio.  Zero-duration or
        # very-short results indicate a bad WAV export before we even hit the API.
        if os.name == "nt":
            # Avoid per-track terminal flashes in Windows GUI/onefile builds.
            duration, fingerprint = _fingerprint_file_hidden_windows(temp_file)
        else:
            duration, fingerprint = _acoustid.fingerprint_file(temp_file)
        print(f"  [AcoustID] Sample duration: {duration:.1f}s  fingerprint length: {len(fingerprint) if fingerprint else 0} chars")

        if not fingerprint or duration < 5:
            print(f"  [AcoustID] ⚠️  Fingerprint too short to be useful (duration={duration:.1f}s) — WAV export may have failed")
            return None

        # Step 2: Gate the request so parallel workers don't exceed 3 req/s.
        # AcoustID silently returns empty results (instead of a proper error)
        # when the rate limit is hit, which looks like "no matches found".
        _get_acoustid_rate_limiter().wait()

        # Step 3: Lookup — use raw response (parse=False) so we can see results that
        # have no linked MusicBrainz recording, which pyacoustid's default parser
        # silently discards and which previously caused "no matches" false negatives.
        raw_response = _acoustid.lookup(api_key, fingerprint, duration)

        # Clean up temp file (only if we created it)
        if owns_temp and temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
            temp_file = None

        if raw_response.get('status') != 'ok':
            print(f"  [AcoustID] API returned status: {raw_response.get('status', 'unknown')}")
            return None

        api_results = raw_response.get('results', [])
        _ACOUSTID_THRESHOLD = 0.3   # lowered from 0.5 — re-encoded / vinyl files often score 0.3–0.5

        if not api_results:
            print(f"  [AcoustID] No fingerprint matches returned by server")
            return None

        best_score = max((r.get('score', 0.0) for r in api_results), default=0.0)
        print(f"  [AcoustID] {len(api_results)} fingerprint match(es), best score {best_score:.2f}")

        # Collect ALL valid recordings across all fingerprint results.
        # AcoustID's score is fingerprint similarity, NOT identification
        # accuracy — a 0.99 score can return completely wrong recordings
        # (e.g. "Don't Let Me Down" by Beatles → "Let Me Down (Don't)" by
        # Husbands).  When hint_artist/hint_title is provided (from Shazam),
        # we prefer recordings that match the hint so the caller gets the
        # correct MusicBrainz recording_id.
        all_candidates = []

        for result in api_results:
            score = result.get('score', 0.0)
            recordings = result.get('recordings', [])

            if not recordings:
                if score >= _ACOUSTID_THRESHOLD:
                    print(f"  [AcoustID] Fingerprint recognized (score {score:.2f}) "
                          f"but no MusicBrainz recording linked — filename search will be tried")
                continue

            for recording in recordings:
                recording_id = recording.get('id')
                title        = recording.get('title')
                artists      = recording.get('artists', [])
                artist       = artists[0].get('name') if artists else None

                if _ACOUSTID_DEBUG:
                    print(f"  [AcoustID] Recording: {artist} - {title} (score: {score:.2f})")

                if not artist or not title:
                    continue

                if score >= _ACOUSTID_THRESHOLD:
                    all_candidates.append({
                        'artist': artist,
                        'title':  title,
                        'recording_id': recording_id,
                        'score': score,
                        'source': 'acoustid'
                    })

        if not all_candidates:
            # Results existed but nothing usable was accepted
            has_unlinked = any(not r.get('recordings') for r in api_results)
            if has_unlinked and best_score >= _ACOUSTID_THRESHOLD:
                pass  # message already printed per-result above
            elif best_score < _ACOUSTID_THRESHOLD:
                print(f"  [AcoustID] Best match score {best_score:.2f} below threshold "
                      f"{_ACOUSTID_THRESHOLD:.2f} — not accepted")
            else:
                print(f"  [AcoustID] Results found but none with usable artist/title data")
            if _ACOUSTID_TRACE or _TRACE_ALL:
                print(f"  [AcoustID] → miss")
            return None

        # If we have a hint (from Shazam), look for a recording that matches.
        # This ensures we get the correct recording_id for the track Shazam
        # already identified, rather than a random wrong recording that
        # happened to have a similar fingerprint.
        chosen = None
        if hint_artist and hint_title:
            _h_artist = hint_artist.lower().strip()
            _h_title  = hint_title.lower().strip()
            for c in all_candidates:
                if (c['artist'].lower().strip() == _h_artist and
                        c['title'].lower().strip() == _h_title):
                    chosen = c
                    if _ACOUSTID_TRACE or _TRACE_ALL:
                        print(f"  [AcoustID] → hint match: {c['artist']} - {c['title']} "
                              f"(recording_id={c['recording_id']})")
                    break
            if not chosen:
                # Try partial match (title only — artist names vary across releases)
                for c in all_candidates:
                    if c['title'].lower().strip() == _h_title:
                        chosen = c
                        if _ACOUSTID_TRACE or _TRACE_ALL:
                            print(f"  [AcoustID] → hint title match: {c['artist']} - {c['title']}")
                        break

        if not chosen:
            # No hint or no match — return highest-score candidate
            chosen = max(all_candidates, key=lambda c: c['score'])

        if _ACOUSTID_TRACE or _TRACE_ALL:
            print(f"  [AcoustID] → hit: {chosen['artist']} - {chosen['title']}")
        return chosen
        
    except _acoustid.WebServiceError as e:
        # Handle AcoustID API-specific errors
        error_msg = str(e)
        error_lower = error_msg.lower()

        # Clean up temp file first (only if we created it)
        if owns_temp and temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        
        # Parse and display appropriate error message
        if "invalid" in error_lower and "key" in error_lower:
            print(f"  [AcoustID] ⚠️  Invalid API key")
            print(f"             Get a free key at: https://acoustid.org/api-key")
            print(f"             Update it in: Main Menu → Option 5 → Manage API Keys")
        elif "rate limit" in error_lower:
            print(f"  [AcoustID] ⚠️  Rate limit exceeded")
            print(f"             Your API key has hit its rate limit")
            print(f"             Wait a bit or contact AcoustID for higher limits")
        elif "status: error" in error_lower or "status:error" in error_lower:
            # Generic error from AcoustID - usually means service issue
            print(f"  [AcoustID] ⚠️  API returned generic error")
            print(f"             Common causes:")
            print(f"             • Internet connection issue")
            print(f"             • AcoustID service temporarily unavailable")
            print(f"             • Your API key may have issues")
            print(f"             Try again later or check: https://status.acoustid.org/")
        elif "fingerprint" in error_lower:
            print(f"  [AcoustID] ⚠️  Fingerprinting failed")
            print(f"             Audio may be too short or corrupted")
        elif "timeout" in error_lower or "timed out" in error_lower:
            print(f"  [AcoustID] ⚠️  Request timed out")
            print(f"             Check your internet connection")
        else:
            # Unknown error - show details
            print(f"  [AcoustID] API Error: {error_msg}")
            if _ACOUSTID_DEBUG:
                print(f"             Full error details:")
                import traceback
                traceback.print_exc()
        
        return None
        
    except Exception as e:
        # Handle other errors (fpcalc missing, file issues, etc.)
        error_msg = str(e)

        # Clean up temp file (only if we created it)
        if owns_temp and temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        
        error_lower = error_msg.lower()
        
        if "chromaprint" in error_lower or "fpcalc" in error_lower:
            print(f"  [AcoustID] ⚠️  chromaprint/fpcalc not found")
            print(f"             Install chromaprint:")
            print(f"             • Windows: Download from https://acoustid.org/chromaprint")
            print(f"             • macOS: brew install chromaprint")
            print(f"             • Linux: apt install libchromaprint-tools")
        elif "no such file" in error_lower or "cannot find" in error_lower:
            print(f"  [AcoustID] ⚠️  fpcalc executable not found in PATH")
            print(f"             Make sure chromaprint is installed (see above)")
        elif "command" in error_lower and "not found" in error_lower:
            print(f"  [AcoustID] ⚠️  fpcalc command failed")
            print(f"             Install or reinstall chromaprint")
        else:
            # Unknown error
            print(f"  [AcoustID] Error: {error_msg}")
            if _ACOUSTID_DEBUG:
                import traceback
                print(f"  [AcoustID] Traceback:")
                traceback.print_exc()
        
        return None


# =============================================================================
# SHAZAM IDENTIFICATION
# =============================================================================

def identify_with_shazam(audio_chunk, runtime_config=None, sample_path=None):
    """Identify track using Shazam (ShazamIO library)

    Shazam advantages:
    - NO API KEY REQUIRED (completely free!)
    - Better coverage of underground electronic/dance music
    - Recognizes DJ edits, bootlegs, and SoundCloud uploads
    - Fast recognition (usually < 5 seconds)
    - Includes extended metadata (Apple Music IDs, genres, etc.)

    Args:
        audio_chunk: pydub AudioSegment to identify.
        runtime_config: optional config dict.
        sample_path: optional path to an already-exported WAV sample.
                     When provided, the function uses this file directly
                     instead of re-extracting and exporting a center
                     window from the chunk.

    Returns:
        dict with artist, title, album, genres, etc. if found
        None if not found or error occurred
    """
    global _SHAZAM_MISSING_WARNED
    if not _SHAZAM_AVAILABLE:
        if _SHAZAM_TRACE and not _SHAZAM_MISSING_WARNED:
            print("  [Shazam] ✗ Not available (shazamio not installed / not packaged)")
            _SHAZAM_MISSING_WARNED = True
        return None

    import asyncio
    temp_file = None
    timeout_seconds = 45
    if isinstance(runtime_config, dict):
        try:
            timeout_seconds = int(runtime_config.get("shazam_timeout_seconds", timeout_seconds))
        except Exception:
            timeout_seconds = 45
    timeout_seconds = max(5, min(60, int(timeout_seconds)))

    try:
        # If a pre-exported sample was provided, use it directly
        # (single probe, no re-export needed).
        if sample_path and os.path.isfile(sample_path):
            windows = None  # sentinel: use sample_path directly
        else:
            temp_file = None

            # Sample window is configurable from Settings -> Identification Mode.
            duration_ms = len(audio_chunk)

            sample_seconds = 12
            probe_mode = 'single'
            try:
                from mixsplitr_core import get_config  # Local import to avoid hard import cycles.
                config = runtime_config if isinstance(runtime_config, dict) else (get_config() or {})
                sample_seconds = int(config.get('fingerprint_sample_seconds', 12))
                probe_mode = str(config.get('fingerprint_probe_mode', 'single')).strip().lower()
            except Exception:
                sample_seconds = 12
                probe_mode = 'single'
            sample_seconds = max(8, min(45, sample_seconds))
            if probe_mode not in ('single', 'multi3'):
                probe_mode = 'single'
            sample_ms = sample_seconds * 1000

            if duration_ms <= sample_ms:
                windows = [(0, duration_ms)]
            else:
                # To avoid tripling Shazam calls, always probe a single center window,
                # even when multi-point mode is enabled for other backends.
                start = max(0, (duration_ms // 2) - (sample_ms // 2))
                end = min(duration_ms, start + sample_ms)
                start = max(0, end - sample_ms)
                windows = [(start, end)]

        if _SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL:
            if windows and len(windows) > 1:
                print(f"  [Shazam] attempted... ({len(windows)} probes)")
            else:
                print(f"  [Shazam] attempted...")

        # Create Shazam instance and recognize
        shazam = _shazamio()
        result = None

        async def _recognize_with_timeout(path):
            return await asyncio.wait_for(shazam.recognize(path), timeout=timeout_seconds)

        # Fast path: pre-exported sample supplied by caller
        if windows is None:
            probe_list = [(1, sample_path, False)]  # (idx, path, owns_file)
        else:
            probe_list = []
            for probe_idx, (start, end) in enumerate(windows, start=1):
                tf = _new_runtime_temp_wav(
                    f"temp_shazam_{threading.current_thread().ident}_{probe_idx}"
                )
                sample = audio_chunk[start:end]
                sample.export(tf, format="wav")
                probe_list.append((probe_idx, tf, True))

        for probe_idx, probe_path, owns_file in probe_list:
            temp_file = probe_path if owns_file else None

            if (_SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL) and windows and len(windows) > 1:
                print(f"  [Shazam] probe {probe_idx}/{len(windows)}")

            # Run async recognition in a new event loop
            # (This handles the case where we're already in an async context)
            try:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We're in an async context, create a new loop in a thread
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            result = pool.submit(
                                lambda: asyncio.run(_recognize_with_timeout(probe_path))
                            ).result(timeout=timeout_seconds + 5)
                    else:
                        # Not in async context, use the loop directly
                        result = loop.run_until_complete(_recognize_with_timeout(probe_path))
                except RuntimeError:
                    # No event loop, create a new one
                    result = asyncio.run(_recognize_with_timeout(probe_path))
            except Exception as probe_error:
                error_lower = str(probe_error).lower()
                if "timeout" in error_lower:
                    if _SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL:
                        print(f"  [Shazam] probe {probe_idx} timed out ({timeout_seconds}s)")
                    result = None
                else:
                    raise

            if owns_file and probe_path and os.path.exists(probe_path):
                os.remove(probe_path)

            if result and 'track' in result:
                break
        
        # Parse Shazam result
        if result and 'track' in result:
            track = result['track']
            
            # Extract basic info
            title = track.get('title')
            artist = track.get('subtitle')  # Shazam uses 'subtitle' for artist name
            
            if _SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL:
                print(f"  [Shazam] → hit: {artist} - {title}")
            
            if not title or not artist:
                if _SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL:
                    print(f"  [Shazam] → miss (incomplete data)")
                return None
            
            # Build result dict
            shazam_result = {
                'artist': artist,
                'title': title,
                'source': 'shazam',
                'id_method': 'shazam',
                'shazam_key': track.get('key'),  # Unique Shazam track ID
            }
            
            # Extract metadata from sections
            sections = track.get('sections', [])
            for section in sections:
                section_type = section.get('type', '')
                metadata_items = section.get('metadata', [])
                
                if section_type == 'SONG':
                    # Main song metadata
                    for item in metadata_items:
                        title_key = item.get('title', '').lower()
                        text = item.get('text', '')
                        
                        if 'album' in title_key and text:
                            shazam_result['album'] = text
                        elif 'released' in title_key or 'release' in title_key:
                            # Parse release year
                            import re
                            year_match = re.search(r'\b(19|20)\d{2}\b', text)
                            if year_match:
                                shazam_result['release_date'] = year_match.group(0)
                        elif 'label' in title_key and text:
                            shazam_result['label'] = text
                
            # Extract genres from hub (if available)
            hub = track.get('hub', {})
            if hub:
                # Some tracks have genre info in hub
                actions = hub.get('actions', [])
                for action in actions:
                    if action.get('type') == 'applemusicplay':
                        # Apple Music integration may have genre
                        pass
            
            # Extract genre from URL hints (Shazam encodes genre in URLs)
            share_url = track.get('share', {}).get('subject', '')
            if 'genre=' in share_url:
                import re
                genre_match = re.search(r'genre=([^&]+)', share_url)
                if genre_match:
                    genre = genre_match.group(1).replace('+', ' ').replace('%20', ' ')
                    shazam_result['genres'] = [genre]
            
            # Get Apple Music ID if available (useful for further metadata lookup)
            apple_music = track.get('hub', {}).get('providers', [])
            for provider in apple_music:
                if provider.get('type') == 'APPLEMUSIC':
                    actions = provider.get('actions', [])
                    for action in actions:
                        if 'uri' in action:
                            uri = action['uri']
                            # Extract Apple Music ID from URI
                            if 'song/' in uri:
                                am_id = uri.split('song/')[-1].split('?')[0]
                                shazam_result['apple_music_id'] = am_id
            
            if _SHAZAM_DEBUG:
                print(f"  [Shazam] Extracted metadata: {shazam_result}")
            
            return shazam_result
        
        if _SHAZAM_DEBUG or _SHAZAM_TRACE or _TRACE_ALL:
            print(f"  [Shazam] → miss")

        return None

    except Exception as e:
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        
        error_msg = str(e)
        error_lower = error_msg.lower()
        
        if "timeout" in error_lower:
            print(f"  [Shazam] ⚠️  Request timed out")
            print(f"            Check your internet connection")
        elif "connection" in error_lower or "network" in error_lower:
            print(f"  [Shazam] ⚠️  Network error")
            print(f"            Unable to reach Shazam servers")
        else:
            print(f"  [Shazam] Error: {error_msg}")
            if _SHAZAM_DEBUG:
                import traceback
                print(f"  [Shazam] Traceback:")
                traceback.print_exc()
        
        return None


# =============================================================================
# MUSICBRAINZ ENHANCED METADATA
# =============================================================================

def get_enhanced_metadata(artist, title, recording_id=None):
    """Get enhanced metadata from MusicBrainz including genres"""
    if not _MB_AVAILABLE:
        return {}
    
    try:
        enhanced = {}
        all_genres = []
        artist_id = None
        
        # If we have a recording ID, use it directly
        if recording_id:
            try:
                recording = _musicbrainzngs.get_recording_by_id(
                    recording_id, 
                    includes=['artists', 'releases', 'tags', 'isrcs']
                )
                rec = recording.get('recording', {})
                
                if 'tag-list' in rec:
                    rec_genres = [tag['name'] for tag in rec['tag-list'] if tag.get('count', 0) >= 0]
                    all_genres.extend(rec_genres)
                
                if 'isrc-list' in rec:
                    enhanced['isrc'] = rec['isrc-list'][0] if rec['isrc-list'] else None
                
                if 'artist-credit' in rec:
                    for credit in rec['artist-credit']:
                        if isinstance(credit, dict) and 'artist' in credit:
                            artist_id = credit['artist'].get('id')
                            break
                
                if 'release-list' in rec:
                    release = rec['release-list'][0]
                    enhanced['album'] = release.get('title', '')
                    enhanced['release_date'] = release.get('date', '')
                    
                    try:
                        release_id = release.get('id')
                        if release_id:
                            full_release = _musicbrainzngs.get_release_by_id(
                                release_id, 
                                includes=['labels', 'tags', 'release-groups']
                            )
                            rel = full_release.get('release', {})
                            
                            if 'label-info-list' in rel:
                                label_info = rel['label-info-list']
                                if label_info and 'label' in label_info[0]:
                                    enhanced['label'] = label_info[0]['label'].get('name', '')
                            
                            if 'tag-list' in rel:
                                rel_genres = [tag['name'] for tag in rel['tag-list']]
                                all_genres.extend(rel_genres)

                            if 'release-group' in rel:
                                rg = rel['release-group']
                                if 'tag-list' in rg:
                                    rg_genres = [tag['name'] for tag in rg['tag-list']]
                                    all_genres.extend(rg_genres)
                    except (KeyError, IndexError, TypeError):
                        pass

            except Exception:
                pass
        
        # Search by artist and title if no recording ID
        if not recording_id or not enhanced:
            try:
                results = _musicbrainzngs.search_recordings(
                    artist=artist,
                    recording=title,
                    limit=1
                )
                
                if results.get('recording-list'):
                    rec = results['recording-list'][0]
                    rec_id = rec.get('id')
                    
                    if 'tag-list' in rec:
                        rec_genres = [tag['name'] for tag in rec['tag-list']]
                        all_genres.extend(rec_genres)
                    
                    if 'artist-credit' in rec:
                        for credit in rec['artist-credit']:
                            if isinstance(credit, dict) and 'artist' in credit:
                                artist_id = credit['artist'].get('id')
                                break
                    
                    if rec_id and not enhanced.get('album'):
                        try:
                            full_rec = _musicbrainzngs.get_recording_by_id(
                                rec_id,
                                includes=['releases', 'tags']
                            )
                            rec_data = full_rec.get('recording', {})
                            
                            if 'tag-list' in rec_data:
                                full_genres = [tag['name'] for tag in rec_data['tag-list']]
                                all_genres.extend(full_genres)
                            
                            if 'release-list' in rec_data and not enhanced.get('album'):
                                release = rec_data['release-list'][0]
                                enhanced['album'] = release.get('title', '')
                                enhanced['release_date'] = release.get('date', '')
                        except (KeyError, IndexError, TypeError):
                            pass
                    
                    if 'release-list' in rec and not enhanced.get('album'):
                        release = rec['release-list'][0]
                        enhanced['album'] = release.get('title', '')
                        enhanced['release_date'] = release.get('date', '')
                        
                        try:
                            release_id = release.get('id')
                            if release_id and not enhanced.get('label'):
                                full_release = _musicbrainzngs.get_release_by_id(release_id, includes=['labels', 'tags'])
                                rel = full_release.get('release', {})
                                if 'label-info-list' in rel:
                                    label_info = rel['label-info-list']
                                    if label_info and 'label' in label_info[0]:
                                        enhanced['label'] = label_info[0]['label'].get('name', '')
                                if 'tag-list' in rel:
                                    rel_genres = [tag['name'] for tag in rel['tag-list']]
                                    all_genres.extend(rel_genres)
                        except Exception:
                            pass
            except Exception:
                pass
        
        # Get artist tags
        if artist_id:
            try:
                artist_data = _musicbrainzngs.get_artist_by_id(artist_id, includes=['tags'])
                if 'tag-list' in artist_data.get('artist', {}):
                    artist_genres = [tag['name'] for tag in artist_data['artist']['tag-list']]
                    all_genres.extend(artist_genres)
            except Exception:
                pass
        
        # Search for artist directly if no genres yet
        if not all_genres and artist:
            try:
                artist_results = _musicbrainzngs.search_artists(artist=artist, limit=1)
                if artist_results.get('artist-list'):
                    found_artist = artist_results['artist-list'][0]
                    aid = found_artist.get('id')
                    if aid:
                        artist_data = _musicbrainzngs.get_artist_by_id(aid, includes=['tags'])
                        if 'tag-list' in artist_data.get('artist', {}):
                            artist_genres = [tag['name'] for tag in artist_data['artist']['tag-list']]
                            all_genres.extend(artist_genres)
            except Exception:
                pass
        
        # Deduplicate genres
        if all_genres:
            seen = set()
            unique_genres = []
            for g in all_genres:
                g_lower = g.lower().strip()
                if g_lower not in seen and len(g_lower) > 1:
                    seen.add(g_lower)
                    unique_genres.append(g.strip())
            enhanced['genres'] = unique_genres[:5]
        
        return enhanced
    except Exception:
        return {}

# =============================================================================
# MUSICBRAINZ TEXT SEARCH (no account required)
# =============================================================================

_DEBUG_MB = os.environ.get("MIXSPLITR_DEBUG_MB", "").strip().lower() in ("1", "true", "yes", "y", "on")
_mb_lock = threading.Lock()
_mb_last_call = 0.0

def _dbg_mb(msg):
    if _DEBUG_MB:
        print(f"  ℹ️  {msg}")

def _mb_rate_limit(min_interval=1.1):
    """Polite MusicBrainz throttling (~1 request/sec)."""
    global _mb_last_call
    with _mb_lock:
        now = time.time()
        wait = (_mb_last_call + min_interval) - now
        if wait > 0:
            time.sleep(wait)
        _mb_last_call = time.time()

_MB_NON_ORIGINAL_TOKENS = (
    "cover",
    "covers",
    "covered",
    "tribute",
    "karaoke",
    "live",
    "bootleg",
    "rehearsal",
    "demo",
    "session",
    "soundalike",
    "sound-alike",
    "re-recorded",
    "rerecorded",
    "rip",
)
_MB_QUERY_INCLUDE_TOKENS = (
    "cover",
    "covers",
    "tribute",
    "karaoke",
    "live",
    "bootleg",
    "demo",
    "session",
    "remix",
    "acoustic",
    "instrumental",
    "version",
)
_MB_TOKEN_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "in",
    "on",
    "by",
    "feat",
    "featuring",
    "ft",
    "vs",
    "x",
}


def _mb_norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _mb_query_wants_non_original(query_text):
    q = _mb_norm(query_text)
    if not q:
        return False
    padded = f" {q} "
    for token in _MB_QUERY_INCLUDE_TOKENS:
        if f" {token} " in padded:
            return True
    return False


def _mb_contains_non_original_markers(row):
    artist = _mb_norm(row.get("artist"))
    title = _mb_norm(row.get("title"))
    album = _mb_norm(row.get("album"))
    haystack = f" {artist} {title} {album} "
    for token in _MB_NON_ORIGINAL_TOKENS:
        if f" {token} " in haystack:
            return True
    return False


def _mb_token_set(text):
    out = set()
    for token in _mb_norm(text).split():
        if len(token) <= 1 or token in _MB_TOKEN_STOPWORDS:
            continue
        out.add(token)
    return out


def _mb_filter_prefer_original(rows, query_text):
    """Filter obvious non-original variants unless query explicitly asks for them."""
    if not rows:
        return rows
    if _mb_query_wants_non_original(query_text):
        return rows

    non_variant_rows = [r for r in rows if not _mb_contains_non_original_markers(r)]
    filtered = non_variant_rows if non_variant_rows else rows

    # If query appears to include artist context, keep artist-aligned matches.
    q_tokens = _mb_token_set(query_text)
    if q_tokens:
        overlaps = []
        max_overlap = 0
        for row in filtered:
            overlap = len(q_tokens.intersection(_mb_token_set(row.get("artist"))))
            overlaps.append(overlap)
            if overlap > max_overlap:
                max_overlap = overlap
        if max_overlap >= 2:
            aligned = [row for row, overlap in zip(filtered, overlaps) if overlap >= 1]
            if aligned:
                filtered = aligned

    return filtered


def musicbrainz_search_recordings(query=None, artist=None, title=None, limit=5, prefer_original=False):
    """Search MusicBrainz recordings by free-text or artist/title.

    Args:
        query: Free-text query.
        artist: Artist hint when not using free-text query.
        title: Recording title hint when not using free-text query.
        limit: Max number of results returned.
        prefer_original: When True, de-prioritize obvious cover/live/karaoke/etc.
                         unless those terms are explicitly in the query.
    """
    if not _MB_AVAILABLE:
        return []

    raw_recordings = []
    seen_ids = set()
    target_count = max(int(limit or 0), 0)
    if target_count <= 0:
        return []
    collect_count = target_count * 3 if prefer_original else target_count

    def _append_unique(items):
        for rec in items:
            rec_id = str(rec.get('id') or '')
            dedupe_key = rec_id or f"{rec.get('title','')}::{rec.get('artist-credit','')}"
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            raw_recordings.append(rec)
            if len(raw_recordings) >= collect_count:
                break

    if query:
        search_variants = [
            ("free-text", {"query": query}),
            ("artist", {"artist": query}),
            ("recording", {"recording": query}),
        ]
        for label, params in search_variants:
            if len(raw_recordings) >= collect_count:
                break
            try:
                _mb_rate_limit()
                _dbg_mb(f"[MB] Searching recordings ({label}): {params}")
                results = _musicbrainzngs.search_recordings(limit=target_count, **params)
                _append_unique(results.get('recording-list', []))
            except Exception as e:
                _dbg_mb(f"[MB] Recording search error ({label}): {e}")
    else:
        try:
            _mb_rate_limit()
            _dbg_mb(f"[MB] Searching recordings: artist={artist}, title={title}")
            results = _musicbrainzngs.search_recordings(artist=artist, recording=title, limit=target_count)
            _append_unique(results.get('recording-list', []))
        except Exception as e:
            _dbg_mb(f"[MB] Search error: {e}")

    _dbg_mb(f"[MB] Recording results collected: {len(raw_recordings)}")

    processed = []
    for rec in raw_recordings:
        artist_name = rec.get('artist-credit', [{}])[0].get('artist', {}).get('name', 'Unknown Artist')
        if isinstance(rec.get('artist-credit', []), list) and len(rec.get('artist-credit', [])) > 0:
            credit_obj = rec['artist-credit'][0]
            if isinstance(credit_obj, dict) and 'artist' in credit_obj:
                artist_name = credit_obj['artist'].get('name', 'Unknown Artist')

        album_name = 'Unknown Album'
        if 'release-list' in rec and len(rec['release-list']) > 0:
            album_name = rec['release-list'][0].get('title', 'Unknown Album')

        processed.append({
            'artist': artist_name,
            'title': rec.get('title', 'Unknown Title'),
            'album': album_name,
            'recording_id': rec.get('id'),
            'score': int(rec.get('ext:score', '0'))
        })

    if prefer_original:
        processed = _mb_filter_prefer_original(processed, query)

    return processed[:target_count]


def musicbrainz_search_releases(query=None, limit=10):
    """Search MusicBrainz releases (albums) by name.

    Args:
        query: Album name to search
        limit: Max number of results (default 10)

    Returns:
        List of dicts with keys: release_id, title, date, artists, score
    """
    if not _MB_AVAILABLE:
        return []

    raw_releases = []
    seen_ids = set()

    def _append_unique(items):
        for rel in items:
            rel_id = str(rel.get('id') or '')
            dedupe_key = rel_id or f"{rel.get('title','')}::{rel.get('date','')}"
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            raw_releases.append(rel)
            if len(raw_releases) >= limit:
                break

    search_variants = []
    if query:
        search_variants = [
            ("free-text", {"query": query}),
            ("artist", {"artist": query}),
            ("release", {"release": query}),
        ]
    else:
        search_variants = [("free-text", {"query": ""})]

    for label, params in search_variants:
        if len(raw_releases) >= limit:
            break
        try:
            _mb_rate_limit()
            _dbg_mb(f"[MB] Searching releases ({label}): {params}")
            results = _musicbrainzngs.search_releases(limit=limit, **params)
            _append_unique(results.get('release-list', []))
        except Exception as e:
            _dbg_mb(f"[MB] Release search error ({label}): {e}")

    _dbg_mb(f"[MB] Release results collected: {len(raw_releases)}")

    processed = []
    for rel in raw_releases:
        artists = []
        for credit in rel.get('artist-credit', []):
            if isinstance(credit, dict) and 'artist' in credit:
                artists.append(credit['artist'].get('name', 'Unknown'))

        processed.append({
            'release_id': rel.get('id', ''),
            'title': rel.get('title', 'Unknown Album'),
            'date': rel.get('date', ''),
            'artists': artists if artists else ['Unknown Artist'],
            'score': int(rel.get('ext:score', '0')),
        })

    return processed


def musicbrainz_get_release_tracklist(release_id):
    """Fetch full tracklist for a release (album) by release ID.

    Args:
        release_id: MusicBrainz release UUID

    Returns:
        Dict with keys: title, tracks (list of {position, title, artist, duration_ms, recording_id})
        Returns None on failure.
    """
    if not _MB_AVAILABLE or not release_id:
        return None

    try:
        _mb_rate_limit()
        _dbg_mb(f"[MB] Fetching release tracklist: {release_id}")
        result = _musicbrainzngs.get_release_by_id(
            release_id, includes=['recordings', 'artist-credits']
        )

        release = result.get('release', {})
        album_title = release.get('title', 'Unknown Album')

        tracks = []
        for medium in release.get('medium-list', []):
            for trk in medium.get('track-list', []):
                recording = trk.get('recording', {})

                # Extract artist from recording credits
                artist_name = 'Unknown Artist'
                for credit in recording.get('artist-credit', []):
                    if isinstance(credit, dict) and 'artist' in credit:
                        artist_name = credit['artist'].get('name', 'Unknown Artist')
                        break

                duration_ms = 0
                if trk.get('length'):
                    try:
                        duration_ms = int(trk['length'])
                    except (ValueError, TypeError):
                        pass
                elif recording.get('length'):
                    try:
                        duration_ms = int(recording['length'])
                    except (ValueError, TypeError):
                        pass

                tracks.append({
                    'position': int(trk.get('position', 0)),
                    'title': recording.get('title', trk.get('title', 'Unknown')),
                    'artist': artist_name,
                    'duration_ms': duration_ms,
                    'recording_id': recording.get('id', ''),
                })

        return {'title': album_title, 'tracks': tracks}
    except Exception as e:
        _dbg_mb(f"[MB] Release tracklist error: {e}")
        return None



# =============================================================================
# RESULT MERGING
# =============================================================================

def strings_match(s1, s2):
    """Check if two strings match (case/punctuation insensitive)"""
    if not s1 or not s2:
        return False
    
    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', str(s).lower())
    
    return normalize(s1) == normalize(s2)


def merge_identification_results(acr_result, mb_result, mb_enhanced, external_meta=None):
    """Merge ACRCloud, MusicBrainz, iTunes, Deezer, Last.fm results"""
    merged = {
        'artist': {'value': None, 'source': None},
        'title': {'value': None, 'source': None},
        'album': {'value': None, 'source': None},
        'label': {'value': None, 'source': None},
        'genres': {'value': [], 'source': None},
        'release_date': {'value': None, 'source': None},
        'isrc': {'value': None, 'source': None},
        'bpm': {'value': None, 'source': None},
        'confidence': 0.0,
        'agreement': 'none',
        'sources_used': [],
        'sources_checked': 0
    }
    
    # Extract data from all sources
    acr_artist = acr_result.get('artist') if acr_result else None
    acr_title = acr_result.get('title') if acr_result else None
    acr_album = acr_result.get('album') if acr_result else None
    
    mb_artist = mb_result.get('artist') if mb_result else None
    mb_title = mb_result.get('title') if mb_result else None
    mb_album = mb_enhanced.get('album') if mb_enhanced else None
    mb_label = mb_enhanced.get('label') if mb_enhanced else None
    mb_genres = mb_enhanced.get('genres', []) if mb_enhanced else []
    mb_date = mb_enhanced.get('release_date') if mb_enhanced else None
    mb_isrc = mb_enhanced.get('isrc') if mb_enhanced else None
    
    # External sources
    external_meta = external_meta or {}
    itunes = external_meta.get('itunes') or {}
    deezer = external_meta.get('deezer') or {}
    lastfm = external_meta.get('lastfm') or {}
    
    itunes_genre = itunes.get('genre')
    itunes_year = itunes.get('year')
    itunes_album = itunes.get('album')
    
    deezer_genre = deezer.get('genre')
    deezer_year = deezer.get('year')
    deezer_album = deezer.get('album')
    deezer_bpm = deezer.get('bpm')

    lastfm_tags = lastfm.get('tags', [])
    essentia_meta = external_meta.get('essentia_genres') or {}
    if not isinstance(essentia_meta, dict):
        essentia_meta = {}
    essentia_genres = essentia_meta.get('genres') or []
    if isinstance(essentia_genres, str):
        essentia_genres = [essentia_genres]
    essentia_force_append = bool(essentia_meta.get('force_append', False))
    
    # Track sources
    has_acr = bool(acr_artist and acr_title)
    has_mb = bool(mb_artist and mb_title)
    has_itunes = bool(itunes)
    has_deezer = bool(deezer)
    has_lastfm = bool(lastfm)
    
    sources_count = int(has_acr) + int(has_mb) + int(has_itunes) + int(has_deezer) + int(has_lastfm)
    merged['sources_checked'] = sources_count
    
    if has_acr:
        merged['sources_used'].append('ACRCloud')
    if has_mb:
        merged['sources_used'].append('MusicBrainz')
    if has_itunes:
        merged['sources_used'].append('iTunes')
    if has_deezer:
        merged['sources_used'].append('Deezer')
    if has_lastfm:
        merged['sources_used'].append('Last.fm')
    
    # Calculate agreement and confidence
    if has_acr and has_mb:
        artist_match = strings_match(acr_artist, mb_artist)
        title_match = strings_match(acr_title, mb_title)
        
        if artist_match and title_match:
            merged['agreement'] = 'full'
            merged['confidence'] = 0.90 + (0.025 * (sources_count - 2))
        elif artist_match or title_match:
            merged['agreement'] = 'partial'
            merged['confidence'] = 0.75 + (0.025 * (sources_count - 2))
        else:
            merged['agreement'] = 'conflict'
            merged['confidence'] = 0.60
    elif has_acr:
        merged['agreement'] = 'acr_only'
        merged['confidence'] = 0.75 + (0.05 * (sources_count - 1))
    elif has_mb:
        merged['agreement'] = 'mb_only'
        merged['confidence'] = 0.70 + (0.05 * (sources_count - 1))
    else:
        merged['agreement'] = 'none'
        merged['confidence'] = 0.0
        return merged
    
    merged['confidence'] = min(0.99, merged['confidence'])
    
    # Merge artist - prefer ACRCloud
    if acr_artist:
        merged['artist'] = {'value': acr_artist, 'source': 'ACRCloud'}
    elif mb_artist:
        merged['artist'] = {'value': mb_artist, 'source': 'MusicBrainz'}
    
    # Merge title - prefer ACRCloud
    if acr_title:
        merged['title'] = {'value': acr_title, 'source': 'ACRCloud'}
    elif mb_title:
        merged['title'] = {'value': mb_title, 'source': 'MusicBrainz'}
    
    # Merge album
    if mb_album and mb_album != 'Unknown Album':
        merged['album'] = {'value': mb_album, 'source': 'MusicBrainz'}
    elif acr_album and acr_album != 'Unknown Album':
        merged['album'] = {'value': acr_album, 'source': 'ACRCloud'}
    elif itunes_album:
        merged['album'] = {'value': itunes_album, 'source': 'iTunes'}
    elif deezer_album:
        merged['album'] = {'value': deezer_album, 'source': 'Deezer'}
    else:
        merged['album'] = {'value': 'Unknown Album', 'source': None}
    
    # Label
    if mb_label:
        merged['label'] = {'value': mb_label, 'source': 'MusicBrainz'}
    
    # Genres - collect from all sources
    all_genres = []
    genre_seen = set()
    genre_sources = []

    def _append_genre(genre_value, source_name):
        text = str(genre_value or '').strip()
        if not text:
            return
        key = text.lower()
        if key in genre_seen:
            return
        genre_seen.add(key)
        all_genres.append(text)
        if source_name and source_name not in genre_sources:
            genre_sources.append(source_name)

    for tag in mb_genres or []:
        _append_genre(tag, 'MusicBrainz')
    _append_genre(itunes_genre, 'iTunes')
    _append_genre(deezer_genre, 'Deezer')
    for tag in lastfm_tags or []:
        _append_genre(tag, 'Last.fm')

    # Essentia can enrich sparse genre metadata without overriding richer API tags.
    if essentia_genres and (len(all_genres) < 2 or essentia_force_append):
        for tag in essentia_genres:
            _append_genre(tag, 'Essentia')

    if all_genres:
        source_str = "+".join(genre_sources) if genre_sources else 'Unknown'
        merged['genres'] = {'value': all_genres[:5], 'source': source_str}
    
    # Release date
    if mb_date:
        merged['release_date'] = {'value': mb_date, 'source': 'MusicBrainz'}
    elif itunes_year:
        merged['release_date'] = {'value': itunes_year, 'source': 'iTunes'}
    elif deezer_year:
        merged['release_date'] = {'value': deezer_year, 'source': 'Deezer'}
    
    # ISRC
    if mb_isrc:
        merged['isrc'] = {'value': mb_isrc, 'source': 'MusicBrainz'}
    
    # BPM - prefer Deezer
    if deezer_bpm is not None:
        try:
            bpm_val = int(deezer_bpm)
            if bpm_val > 0:
                merged['bpm'] = {'value': bpm_val, 'source': 'Deezer'}
        except (ValueError, TypeError):
            pass
    
    # Fallback to local BPM
    if not merged['bpm']['value']:
        local_bpm = external_meta.get('local_bpm') if external_meta else None
        if local_bpm and local_bpm.get('bpm'):
            merged['bpm'] = {
                'value': local_bpm['bpm'],
                'source': 'librosa',
                'confidence': local_bpm.get('confidence', 0.7)
            }
    
    return merged


# =============================================================================
# ARTWORK
# =============================================================================

def batch_download_artwork(artwork_urls):
    """Download multiple artworks in parallel"""
    artwork_cache = {}
    
    def download_single(url):
        if not url or "{w}x{h}" in url:
            url = url.replace("{w}x{h}", "600x600") if url else None
        if not url:
            return None, None
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return url, response.content
        except (requests.RequestException, ConnectionError, TimeoutError):
            pass
        return url, None
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_single, url): url for url in artwork_urls if url}
        for future in as_completed(futures):
            url, img_data = future.result()
            if img_data:
                artwork_cache[url] = img_data
    
    return artwork_cache


def identify_dual_mode(audio_chunk, acrcloud_recognizer=None, acoustid_key=None):
    """
    Run both ACRCloud and AcoustID, return best result based on confidence.
    
    Args:
        audio_chunk: Audio file path or data
        acrcloud_recognizer: ACRCloud recognizer instance
        acoustid_key: AcoustID API key
    
    Returns:
        Best result dict with 'id_method' = 'dual_best'
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    results = {}
    
    def run_acrcloud():
        try:
            if acrcloud_recognizer:
                result = acrcloud_recognizer.recognize_by_file(audio_chunk, 0)
                # Parse ACRCloud response (simplified)
                import json
                data = json.loads(result)
                if data.get('status', {}).get('code') == 0:
                    music = data['metadata']['music'][0]
                    return {
                        'title': music.get('title'),
                        'artist': music['artists'][0]['name'] if music.get('artists') else None,
                        'album': music.get('album', {}).get('name'),
                        'confidence': data['status'].get('score', 0),
                        'id_method': 'acrcloud',
                        'success': True
                    }
        except (ValueError, KeyError, TypeError):
            pass
        return {'success': False, 'confidence': 0, 'id_method': 'acrcloud'}
    
    def run_acoustid():
        try:
            if acoustid_key:
                result = identify_with_acoustid(audio_chunk)
                if result.get('title'):
                    result['id_method'] = 'acoustid'
                    result['success'] = True
                    # AcoustID returns score 0-1, convert to percentage
                    if 'confidence' not in result:
                        result['confidence'] = 80  # Default if no score
                    return result
        except Exception:
            pass
        return {'success': False, 'confidence': 0, 'id_method': 'acoustid'}
    
    # Run both in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_acr = executor.submit(run_acrcloud)
        future_aid = executor.submit(run_acoustid)
        
        acr_result = future_acr.result()
        aid_result = future_aid.result()
    
    results['acrcloud'] = acr_result
    results['acoustid'] = aid_result
    
    # Compare and pick best
    acr_conf = acr_result.get('confidence', 0)
    aid_conf = aid_result.get('confidence', 0)
    
    if not acr_result.get('success') and not aid_result.get('success'):
        # Both failed
        return {
            'title': None,
            'artist': None,
            'confidence': 0,
            'id_method': 'dual_both_failed',
            'comparison': f"ACR: failed, AID: failed"
        }
    
    # Pick winner
    if acr_conf >= aid_conf and acr_result.get('success'):
        winner = acr_result.copy()
        winner['id_method'] = 'dual_best_acrcloud'
        winner['comparison'] = f"ACRCloud {acr_conf}% > AcoustID {aid_conf}%"
        winner['runner_up'] = aid_result
    elif aid_result.get('success'):
        winner = aid_result.copy()
        winner['id_method'] = 'dual_best_acoustid'
        winner['comparison'] = f"AcoustID {aid_conf}% > ACRCloud {acr_conf}%"
        winner['runner_up'] = acr_result
    else:
        # Use whichever succeeded
        winner = acr_result if acr_result.get('success') else aid_result
        winner['id_method'] = 'dual_fallback'
        winner['comparison'] = f"One method succeeded"
    
    return winner
