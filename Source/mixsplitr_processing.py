#!/usr/bin/env python3
"""
MixSplitR v8.0 - Track Processing Module

Contains the process_single_track_* functions and their shared helpers.
Extracted from mixsplitr.py for maintainability.

Most functions follow the same pipeline shape:
  1. Export sample → 2. Identify → 3. Enrich metadata → 4. BPM → 5. Merge → 6. Dedup → 7. Return
"""

import os
import json
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydub import AudioSegment

# Optional third-party imports with fallback
try:
    import acoustid
    ACOUSTID_AVAILABLE = True
except ImportError:
    ACOUSTID_AVAILABLE = False

# =============================================================================
# LOCAL MODULE IMPORTS
# =============================================================================

from mixsplitr_core import (
    get_config,
    get_runtime_temp_directory,
    Style,
    DEFAULT_DUPLICATE_POLICY,
    normalize_duplicate_policy,
    ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT,
    ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT,
    ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT,
    ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT,
    ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT,
)

from mixsplitr_metadata import find_art_in_json, get_backup_art, get_all_external_metadata

from mixsplitr_audio import detect_bpm_librosa
from mixsplitr_tagging import AUDIO_FORMATS

from mixsplitr_identify import (
    identify_with_acoustid, identify_with_shazam, get_enhanced_metadata,
    merge_identification_results, is_shazam_available, is_trace_enabled,
    musicbrainz_search_recordings, print_id_winner
)
from mixsplitr_essentia import (
    EssentiaGenreConfig,
    count_existing_genre_sources as _ess_count_existing_genre_sources,
    extract_bpm_hint as _ess_extract_bpm_hint,
    get_runtime_status as _get_essentia_runtime_status,
    infer_genres as _infer_essentia_genres,
)

UNIDENTIFIED_TRACKS_DIR = "Unidentified Tracks"

# Minimum AcoustID score required to override a Shazam identification when
# both disagree.  AcoustID returns confident false-positives (0.95+ on wrong
# tracks), so this threshold is set very high.  Raise to 0.99 if needed.
_ACOUSTID_OVERRIDE_SCORE = 0.98


def _resolve_chunk(chunk_data):
    """Lazily load the AudioSegment from disk if it was offloaded.

    During prep, chunks are cached to temp FLAC files and the in-memory
    AudioSegment is freed to keep peak RAM low.  This helper transparently
    reloads the chunk when it's actually needed by a worker thread,
    so only max_workers chunks are in RAM at any one time.

    Returns the AudioSegment (loaded from disk if necessary).
    """
    chunk = chunk_data.get('chunk')
    if chunk is not None:
        return chunk
    temp_path = chunk_data.get('temp_chunk_path')
    if temp_path and os.path.isfile(temp_path):
        return AudioSegment.from_file(temp_path)
    return None
_ESSENTIA_GENRE_ENABLED_DEFAULT = ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT
_ESSENTIA_GENRE_WHEN_MISSING_ONLY_DEFAULT = ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT
_ESSENTIA_GENRE_MIN_CONFIDENCE_DEFAULT = ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT
_ESSENTIA_GENRE_MAX_TAGS_DEFAULT = ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT
_ESSENTIA_GENRE_ANALYSIS_SECONDS_DEFAULT = ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT


# =============================================================================
# SHARED HELPERS for process_single_track_* functions
# =============================================================================

def _get_fingerprint_sampling_config(runtime_config=None):
    """Return (sample_seconds, probe_mode) with safe defaults."""
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    probe_mode = str(config.get('fingerprint_probe_mode', 'single')).strip().lower()
    if probe_mode not in ('single', 'multi3'):
        probe_mode = 'single'
    # Each probe mode stores its own sample size so switching modes doesn't
    # carry over an inappropriate value (e.g. 45s single → 45s multi would
    # cause overlapping probes on short tracks).
    if probe_mode == 'multi3':
        _key = 'fingerprint_sample_seconds_multi'
        _default = 12
    else:
        _key = 'fingerprint_sample_seconds'
        _default = 12
    try:
        sample_seconds = int(config.get(_key, _default))
    except Exception:
        sample_seconds = _default
    sample_seconds = max(8, min(45, sample_seconds))
    return sample_seconds, probe_mode


def _build_probe_windows(chunk_len, sample_ms, probe_mode):
    """Build probe windows as (start_ms, end_ms) tuples."""
    if chunk_len <= sample_ms:
        return [(0, chunk_len)]

    # Default behavior: one center probe.
    if probe_mode != 'multi3':
        middle = chunk_len // 2
        half_window = sample_ms // 2
        start = max(0, middle - half_window)
        end = min(chunk_len, start + sample_ms)
        start = max(0, end - sample_ms)
        return [(start, end)]

    # Multi-point mode: early + middle + late probes.
    max_start = max(0, chunk_len - sample_ms)
    middle_start = max_start // 2
    candidates = [
        (0, min(chunk_len, sample_ms)),
        (middle_start, min(chunk_len, middle_start + sample_ms)),
        (max_start, min(chunk_len, max_start + sample_ms)),
    ]
    unique = []
    seen = set()
    for start, end in candidates:
        key = (int(start), int(end))
        if key in seen:
            continue
        seen.add(key)
        unique.append((start, end))
    return unique


def _new_runtime_temp_path(prefix: str, suffix: str = ".wav") -> str:
    """Create a unique temp file path inside MixSplitR's runtime temp directory."""
    temp_dir = str(get_runtime_temp_directory("runtime"))
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except Exception:
        pass
    safe_prefix = f"{str(prefix or 'mixsplitr_tmp').strip('_')}_"
    fd, temp_path = tempfile.mkstemp(prefix=safe_prefix, suffix=str(suffix or ""), dir=temp_dir)
    os.close(fd)
    return temp_path


def _export_id_samples(chunk, file_num, i, prefix="temp_id", runtime_config=None,
                       force_single=False):
    """Export one or more WAV probe samples for fingerprint submission.

    When *force_single* is True the probe mode is overridden to 'single'
    regardless of the user's config.  Use this when the caller only needs
    one probe (e.g. MB-only mode where only Shazam consumes the sample).
    """
    sample_seconds, probe_mode = _get_fingerprint_sampling_config(runtime_config=runtime_config)
    if force_single:
        probe_mode = 'single'
    sample_ms = sample_seconds * 1000
    windows = _build_probe_windows(len(chunk), sample_ms, probe_mode)
    temp_names = []
    for probe_idx, (start, end) in enumerate(windows, start=1):
        sample = chunk[start:end]
        temp_name = _new_runtime_temp_path(
            f"{prefix}_{file_num}_{i}_{probe_idx}_{threading.current_thread().ident}",
            ".wav",
        )
        sample.export(temp_name, format="wav")
        temp_names.append(temp_name)
    return temp_names


def _detect_bpm_if_needed(chunk, external_meta=None, runtime_config=None):
    """Run local BPM detection if enabled and Deezer didn't already provide one.

    If *external_meta* is provided, checks its ``deezer.bpm`` field first and
    stores the result back into ``external_meta['local_bpm']``.

    Returns the local_bpm dict (or None).
    """
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    use_local_bpm = not bool(config.get('disable_local_bpm', False))

    local_bpm = None
    if use_local_bpm:
        skip = False
        if external_meta is not None:
            deezer_data = (external_meta or {}).get('deezer') or {}
            if isinstance(deezer_data, dict) and deezer_data.get('bpm'):
                skip = True
        if not skip and chunk is not None:
            try:
                local_bpm = detect_bpm_librosa(chunk)
            except Exception:
                pass
    if external_meta is not None:
        external_meta['local_bpm'] = local_bpm
    return local_bpm



def _count_existing_genre_sources(external_meta, existing_genre_count_hint=0):
    return _ess_count_existing_genre_sources(
        external_meta,
        existing_genre_count_hint=existing_genre_count_hint,
    )


def _extract_bpm_hint(external_meta):
    return _ess_extract_bpm_hint(external_meta)


def _build_essentia_backend_candidate(
    *,
    unified_pipeline,
    requested,
    enabled,
    when_missing_only,
    runtime_status,
    skipped_reason="",
    genre_result=None,
):
    candidate = {
        "unified_pipeline": bool(unified_pipeline),
        "requested": bool(requested),
        "enabled": bool(enabled),
        "when_missing_only": bool(when_missing_only),
        "available": bool(getattr(runtime_status, "available", False)),
        "reason": str(getattr(runtime_status, "reason", "") or "").strip(),
        "python_executable": str(getattr(runtime_status, "python_executable", "") or "").strip(),
        "numpy_available": bool(getattr(runtime_status, "numpy_available", False)),
        "numpy_import_error": str(getattr(runtime_status, "numpy_import_error", "") or "").strip(),
        "essentia_version": str(getattr(runtime_status, "essentia_version", "") or "").strip(),
        "essentia_import_error": str(getattr(runtime_status, "essentia_import_error", "") or "").strip(),
        "genres": [],
        "confidence": 0.0,
        "analysis_seconds": 0.0,
        "candidates": [],
        "diagnostics": {},
    }
    if skipped_reason:
        candidate["skipped_reason"] = str(skipped_reason)

    if genre_result is not None:
        payload = dict(getattr(genre_result, "payload", None) or {})
        if payload:
            candidate["genres"] = list(payload.get("genres") or [])
            candidate["confidence"] = float(payload.get("confidence", 0.0) or 0.0)
            candidate["analysis_seconds"] = float(payload.get("analysis_seconds", 0.0) or 0.0)
        candidate["candidates"] = list(getattr(genre_result, "candidates", []) or [])
        candidate["diagnostics"] = dict(getattr(genre_result, "diagnostics", {}) or {})
    return candidate


def _maybe_enrich_genres_with_essentia(chunk, external_meta, runtime_config=None, existing_genre_count_hint=0):
    """Infer broad genres from local Essentia DSP and attach to external_meta."""
    if not isinstance(external_meta, dict):
        return {"genres_payload": None, "candidate": None}

    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    unified_pipeline = bool(config.get("essentia_unified_pipeline", True))
    enabled = bool(config.get("essentia_genre_enrichment_enabled", _ESSENTIA_GENRE_ENABLED_DEFAULT))
    requested = bool(enabled)
    when_missing_only = bool(
        config.get("essentia_genre_enrichment_when_missing_only", _ESSENTIA_GENRE_WHEN_MISSING_ONLY_DEFAULT)
    )
    runtime_status = _get_essentia_runtime_status()

    if not enabled:
        candidate = _build_essentia_backend_candidate(
            unified_pipeline=unified_pipeline,
            requested=requested,
            enabled=enabled,
            when_missing_only=when_missing_only,
            runtime_status=runtime_status,
            skipped_reason="disabled_by_config",
        )
        return {"genres_payload": None, "candidate": candidate}

    if when_missing_only and _count_existing_genre_sources(external_meta, existing_genre_count_hint) >= 2:
        candidate = _build_essentia_backend_candidate(
            unified_pipeline=unified_pipeline,
            requested=requested,
            enabled=enabled,
            when_missing_only=when_missing_only,
            runtime_status=runtime_status,
            skipped_reason="skipped_existing_genres",
        )
        return {"genres_payload": None, "candidate": candidate}

    genre_config = EssentiaGenreConfig(
        enabled=enabled,
        when_missing_only=when_missing_only,
        min_confidence=float(
            config.get("essentia_genre_enrichment_min_confidence", _ESSENTIA_GENRE_MIN_CONFIDENCE_DEFAULT)
        ),
        max_tags=int(config.get("essentia_genre_enrichment_max_tags", _ESSENTIA_GENRE_MAX_TAGS_DEFAULT)),
        analysis_seconds=float(
            config.get("essentia_genre_enrichment_analysis_seconds", _ESSENTIA_GENRE_ANALYSIS_SECONDS_DEFAULT)
        ),
    )
    bpm_hint = _extract_bpm_hint(external_meta)
    genre_result = _infer_essentia_genres(chunk, config=genre_config, bpm_hint=bpm_hint)
    payload = dict(getattr(genre_result, "payload", None) or {})

    candidate = _build_essentia_backend_candidate(
        unified_pipeline=unified_pipeline,
        requested=requested,
        enabled=enabled,
        when_missing_only=when_missing_only,
        runtime_status=getattr(genre_result, "runtime", runtime_status),
        genre_result=genre_result,
    )

    if payload:
        external_meta["essentia_genres"] = payload
    return {"genres_payload": payload or None, "candidate": candidate}


def _get_unidentified_path(output_folder, unidentified_filename):
    """Build/create the canonical output path for unidentified tracks."""
    unidentified_dir = os.path.join(output_folder, UNIDENTIFIED_TRACKS_DIR)
    os.makedirs(unidentified_dir, exist_ok=True)
    return os.path.join(unidentified_dir, unidentified_filename)


def _build_unidentified_filename(chunk_data, file_num, output_ext):
    """Build filename for unidentified tracks while preserving source naming."""
    original_file = str((chunk_data or {}).get("original_file") or "").strip()
    source_stem = os.path.splitext(os.path.basename(original_file))[0].strip() if original_file else ""
    source_stem = source_stem.translate(str.maketrans("", "", '<>:"/\\|?*')).strip()
    if not source_stem:
        source_stem = f"File{file_num}"

    track_suffix = ""
    try:
        split_index = (chunk_data or {}).get("split_index", None)
        split_number = int(split_index) + 1 if split_index is not None else None
    except Exception:
        split_number = None

    # Keep exact source filename for single tracks; add suffix only on additional splits.
    if split_number and split_number > 1:
        track_suffix = f"_Track_{split_number:02d}"

    return f"{source_stem}{track_suffix}{output_ext}"


def _allows_duplicate_collision(runtime_config=None):
    """Return True when duplicate collisions should not be hard-skipped."""
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    if not isinstance(config, dict):
        return False
    raw_policy = config.get("duplicate_policy", DEFAULT_DUPLICATE_POLICY)
    policy = normalize_duplicate_policy(raw_policy, default=DEFAULT_DUPLICATE_POLICY)
    return bool(policy != DEFAULT_DUPLICATE_POLICY)


def _resolve_output_format(runtime_config=None):
    """Return a validated output format key."""
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    output_format = str(config.get("output_format", "flac")).strip().lower()
    if output_format not in AUDIO_FORMATS:
        output_format = "flac"
    return output_format


def _output_format_export_spec(output_format):
    """Return (ext, pydub_format, export_kwargs) for a format key."""
    fmt = AUDIO_FORMATS.get(output_format, AUDIO_FORMATS["flac"])
    ext = str(fmt.get("ext") or ".flac")
    pydub_format = str(output_format or "flac").split("_")[0]
    if output_format == "alac":
        pydub_format = "ipod"
    export_kwargs = {}
    codec = fmt.get("codec")
    if codec:
        export_kwargs["codec"] = codec
    bitrate = fmt.get("bitrate")
    if bitrate:
        export_kwargs["bitrate"] = bitrate
    quality = fmt.get("quality")
    if quality:
        export_kwargs["parameters"] = ["-q:a", str(quality)]
    return ext, pydub_format, export_kwargs


def _export_unidentified_chunk(chunk, output_path, output_format):
    """Export unidentified chunk with selected format; fallback to FLAC on error."""
    ext, pydub_format, export_kwargs = _output_format_export_spec(output_format)
    try:
        chunk.export(output_path, format=pydub_format, **export_kwargs)
    except Exception:
        fallback_path = output_path
        if ext != ".flac":
            fallback_path = os.path.splitext(output_path)[0] + ".flac"
        chunk.export(fallback_path, format="flac")
        return fallback_path
    return output_path


def _resolve_artwork(art_url, artist, title):
    """Fall back to backup artwork sources if *art_url* is not already set."""
    if not art_url and artist and title:
        return get_backup_art(artist, title)
    return art_url


def _build_enhanced_metadata(merged):
    """Extract enhanced metadata fields from a merge-result dict."""
    enhanced = {}
    if merged['genres']['value']:
        enhanced['genres'] = merged['genres']['value']
    if merged['release_date']['value']:
        enhanced['release_date'] = merged['release_date']['value']
    if merged['label']['value']:
        enhanced['label'] = merged['label']['value']
    if merged['isrc']['value']:
        enhanced['isrc'] = merged['isrc']['value']
    if merged.get('bpm', {}).get('value'):
        enhanced['bpm'] = merged['bpm']['value']
    return enhanced


def _build_readable_metadata(merged, artist, title, album):
    """Build the human-readable metadata dict shown in the UI and manifest."""
    readable = {
        'artist':      {'value': artist, 'source': merged['artist']['source']},
        'title':       {'value': title,  'source': merged['title']['source']},
        'album':       {'value': album,  'source': merged['album']['source']},
        'confidence':  merged['confidence'],
        'agreement':   merged['agreement'],
        'sources_used': merged['sources_used']
    }
    if merged['label']['value']:
        readable['label'] = {'value': merged['label']['value'], 'source': merged['label']['source']}
    if merged['genres']['value']:
        readable['genres'] = {'value': merged['genres']['value'], 'source': merged['genres']['source']}
    if merged['release_date']['value']:
        year_val = merged['release_date']['value'][:4] if len(merged['release_date']['value']) >= 4 else merged['release_date']['value']
        readable['year'] = {'value': year_val, 'source': merged['release_date']['source']}
    if merged['isrc']['value']:
        readable['isrc'] = {'value': merged['isrc']['value'], 'source': merged['isrc']['source']}
    if merged.get('bpm', {}).get('value'):
        readable['bpm'] = {'value': merged['bpm']['value'], 'source': merged['bpm']['source']}
    return readable


# =============================================================================
# TRACK PROCESSING – ACRCloud mode (primary)
# =============================================================================

def process_single_track(chunk_data, i, recognizer, rate_limiter, existing_tracks,
                         output_folder, existing_tracks_lock, preview_mode=False,
                         runtime_config=None):
    """Process a single track - designed for parallel execution with merged identification"""
    file_num = chunk_data.get('file_num', 0)
    chunk = _resolve_chunk(chunk_data)

    if chunk is None:
        return {'status': 'skipped', 'reason': 'no_chunk', 'index': i, 'file_num': file_num}

    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}

    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    temp_names = _export_id_samples(chunk, file_num, i, runtime_config=config)
    # Keep the first short probe around — Shazam can reuse it.
    reusable_sample = temp_names[0] if temp_names else None

    res = {}
    try:
        for temp_name in temp_names:
            rate_limiter.wait()
            probe_res = json.loads(recognizer.recognize_by_file(temp_name, 0))
            res = probe_res or {}
            if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
                break
    except Exception:
        pass

    # Read config for runtime flags
    shazam_enabled = not bool(config.get('disable_shazam', False))
    allow_duplicate_collision = _allows_duplicate_collision(config)

    acr_result = None
    mb_result = None
    mb_enhanced = {}
    art_url = None
    recording_id = None
    winner_source = "unknown"
    _shazam_raw = None
    _acoustid_raw = None

    try:
        if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
            music = res["metadata"]["music"][0]
            acr_result = {
                'artist': music["artists"][0]["name"],
                'title': music["title"],
                'album': music.get("album", {}).get("name", "Unknown Album")
            }
            art_url = find_art_in_json(music)
            mb_enhanced = get_enhanced_metadata(acr_result['artist'], acr_result['title'])
            winner_source = "acrcloud"
        else:
            # ACRCloud missed — try Shazam as fallback (only if user enabled it)
            if shazam_enabled and is_shazam_available():
                shazam_result = identify_with_shazam(chunk, runtime_config=config, sample_path=reusable_sample)
                if shazam_result:
                    mb_result = {'artist': shazam_result['artist'], 'title': shazam_result['title']}
                    _shazam_raw = {'artist': shazam_result['artist'], 'title': shazam_result['title']}
                    mb_enhanced = get_enhanced_metadata(shazam_result['artist'], shazam_result['title'])
                    winner_source = "shazam"
    finally:
        # Clean up sample files now that identification is done
        for temp_name in temp_names:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except Exception:
                    pass

    final_artist = (acr_result or mb_result or {}).get('artist')
    final_title = (acr_result or mb_result or {}).get('title')
    art_url = _resolve_artwork(art_url, final_artist, final_title)

    external_meta = get_all_external_metadata(final_artist, final_title, runtime_config=config) if final_artist and final_title else {}
    _detect_bpm_if_needed(chunk, external_meta, runtime_config=config)
    essentia_genres = None
    essentia_candidate = None
    if final_artist and final_title:
        essentia_meta = _maybe_enrich_genres_with_essentia(
            chunk,
            external_meta,
            runtime_config=config,
            existing_genre_count_hint=len((mb_enhanced or {}).get("genres") or []),
        )
        if isinstance(essentia_meta, dict):
            essentia_genres = essentia_meta.get("genres_payload")
            essentia_candidate = essentia_meta.get("candidate")

    merged = merge_identification_results(acr_result, mb_result, mb_enhanced, external_meta)

    if merged['artist']['value'] and merged['title']['value']:
        artist = merged['artist']['value']
        title = merged['title']['value']
        album = merged['album']['value'] or 'Unknown Album'

        expected_filename = f"{artist} - {title}.flac".translate(str.maketrans('', '', '<>:"/\\|?*'))
        with existing_tracks_lock:
            if expected_filename in existing_tracks and not allow_duplicate_collision:
                return {
                    'status': 'skipped', 'reason': 'already_exists', 'index': i, 'file_num': file_num,
                    'artist': artist, 'title': title, 'album': album,
                    'original_file': chunk_data.get('original_file'),
                    'chunk_index': chunk_data.get('split_index', 0),
                    'temp_chunk_path': chunk_data.get('temp_chunk_path')
                }
            existing_tracks.add(expected_filename)

        readable_metadata = _build_readable_metadata(merged, artist, title, album)
        enhanced_metadata = _build_enhanced_metadata(merged)
        identification_source = (
            winner_source
            if str(winner_source or "").strip().lower() not in ("", "unknown", "none")
            else (merged['sources_used'][0].lower() if merged['sources_used'] else 'unknown')
        )

        result = {
            'status': 'identified', 'index': i, 'file_num': file_num,
            'artist': artist, 'title': title, 'album': album, 'art_url': art_url,
            'expected_filename': expected_filename,
            'identification_source': identification_source,
            'enhanced_metadata': enhanced_metadata, 'readable_metadata': readable_metadata,
            'backend_candidates': {
                'acrcloud': acr_result, 'shazam': mb_result if not acr_result else None,
                'acoustid': None, 'recording_id': recording_id,
                'winner_src': identification_source,
                'essentia': essentia_candidate,
                'essentia_genres': essentia_genres,
            },
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if not preview_mode:
            temp_flac = os.path.join(output_folder, f"temp_{file_num}_{i}_{threading.current_thread().ident}.flac")
            chunk.export(temp_flac, format="flac")
            result['temp_flac'] = temp_flac

        return result
    else:
        output_format = _resolve_output_format(config)
        output_ext, _, _ = _output_format_export_spec(output_format)
        unidentified_filename = _build_unidentified_filename(chunk_data, file_num, output_ext)
        unidentified_path = _get_unidentified_path(output_folder, unidentified_filename)

        if os.path.exists(unidentified_path) and not allow_duplicate_collision:
            return {
                'status': 'skipped', 'reason': 'already_exists', 'index': i, 'file_num': file_num,
                'unidentified_filename': unidentified_filename,
                'original_file': chunk_data.get('original_file'),
                'chunk_index': chunk_data.get('split_index', 0),
                'temp_chunk_path': chunk_data.get('temp_chunk_path')
            }

        local_bpm = _detect_bpm_if_needed(chunk, runtime_config=config)

        result = {
            'status': 'unidentified', 'index': i, 'file_num': file_num,
            'unidentified_filename': unidentified_filename, 'unidentified_path': unidentified_path,
            'backend_candidates': {
                'acrcloud': acr_result, 'shazam': _shazam_raw,
                'acoustid': _acoustid_raw, 'recording_id': recording_id
            },
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if local_bpm and local_bpm.get('bpm'):
            result['detected_bpm'] = local_bpm['bpm']
            result['bpm_confidence'] = local_bpm.get('confidence', 0)

        if not preview_mode:
            saved_path = _export_unidentified_chunk(chunk, unidentified_path, output_format)
            result['unidentified_path'] = saved_path
            result['unidentified_filename'] = os.path.basename(saved_path)

        return result


# =============================================================================
# TRACK PROCESSING – Manual mode (no fingerprinting)
# =============================================================================

def process_single_track_manual(chunk_data, i, existing_tracks,
                                 output_folder, existing_tracks_lock, preview_mode=False):
    """Mark track as unidentified for manual entry (no fingerprinting).

    Used when MODE_MANUAL is active (no API keys configured).
    All tracks are marked as unidentified for manual metadata entry in the editor.
    """
    file_num = chunk_data.get('file_num', 0)
    chunk = _resolve_chunk(chunk_data)

    if chunk is None:
        return {'status': 'skipped', 'reason': 'no_chunk', 'index': i, 'file_num': file_num}

    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}

    # Skip duplicate check in manual mode
    with existing_tracks_lock:
        existing_tracks.append({'status': 'unidentified'})

    # Export temp FLAC for manual editing
    temp_flac = chunk_data.get('temp_chunk_path')
    if not temp_flac:
        temp_flac = os.path.join(output_folder, f"temp_track_{i}_{file_num}.flac")
        # Convert to stereo if multi-channel (FLAC supports max 8 channels)
        # Use ffmpeg -ac 2 for proper mixdown (pydub.set_channels doesn't handle >2 to 2)
        if chunk.channels > 8:
            chunk.export(temp_flac, format="flac", parameters=["-ac", "2", "-compression_level", "8"])
        else:
            chunk.export(temp_flac, format="flac", parameters=["-compression_level", "8"])

    output_format = _resolve_output_format()
    output_ext, _, _ = _output_format_export_spec(output_format)

    return {
        'status': 'unidentified',
        'index': i,
        'file_num': file_num,
        'temp_flac': temp_flac,
        'unidentified_filename': _build_unidentified_filename(chunk_data, file_num, output_ext),
        'artist': '',
        'title': '',
        'album': '',
        'art_url': None,
        'enhanced_metadata': {},
        'original_file': chunk_data.get('original_file'),
        'chunk_index': chunk_data.get('split_index', 0),
        'temp_chunk_path': chunk_data.get('temp_chunk_path')
    }


# =============================================================================
# TRACK PROCESSING – Split-only mode (no ID lookups, sequential names)
# =============================================================================

def process_single_track_split_only(chunk_data, i, existing_tracks,
                                    output_folder, existing_tracks_lock, preview_mode=False,
                                    runtime_config=None):
    """Create deterministic sequential track names without any ID/metadata lookup."""
    file_num = chunk_data.get('file_num', 0)
    chunk = _resolve_chunk(chunk_data)

    if chunk is None:
        return {'status': 'skipped', 'reason': 'no_chunk', 'index': i, 'file_num': file_num}

    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}

    original_file = chunk_data.get('original_file') or ""
    source_name = os.path.splitext(os.path.basename(original_file))[0].strip() if original_file else ""
    source_name = source_name.translate(str.maketrans('', '', '<>:"/\\|?*')).strip() or f"Source {file_num}"

    track_number = int(chunk_data.get('split_index', i)) + 1
    artist = ""
    title = f"Track {track_number:02d}"
    album = "Unlabeled Split"
    split_only_folder = source_name

    expected_filename = f"{title}.flac".translate(str.maketrans('', '', '<>:\"/\\|?*'))
    allow_duplicate_collision = _allows_duplicate_collision(runtime_config)
    dedupe_key = f"{split_only_folder}/{expected_filename}"
    with existing_tracks_lock:
        if dedupe_key in existing_tracks and not allow_duplicate_collision:
            return {
                'status': 'skipped', 'reason': 'already_exists', 'index': i, 'file_num': file_num,
                'artist': artist, 'title': title, 'album': album,
                'expected_filename': expected_filename,
                'original_file': original_file,
                'chunk_index': chunk_data.get('split_index', 0),
                'temp_chunk_path': chunk_data.get('temp_chunk_path')
            }
        existing_tracks.add(dedupe_key)

    result = {
        'status': 'identified', 'index': i, 'file_num': file_num,
        'artist': artist, 'title': title, 'album': album,
        'art_url': None,
        'expected_filename': expected_filename,
        'identification_source': 'split_only',
        'enhanced_metadata': {
            # Keep source-name as folder only, and output filenames as "Track NN".
            'output_folder_name': split_only_folder,
            'output_filename_base': title,
            'output_title_only_filename': True,
        },
        'readable_metadata': {
            'artist': {'value': artist, 'source': 'split_only'},
            'title': {'value': title, 'source': 'split_only'},
            'album': {'value': album, 'source': 'split_only'},
            'confidence': 1.0,
            'agreement': 1.0,
            'sources_used': ['split_only'],
        },
        'backend_candidates': {},
        'original_file': original_file,
        'chunk_index': chunk_data.get('split_index', 0),
        'temp_chunk_path': chunk_data.get('temp_chunk_path')
    }

    if not preview_mode:
        temp_flac = os.path.join(output_folder, f"temp_splitonly_{file_num}_{i}_{threading.current_thread().ident}.flac")
        if chunk.channels > 8:
            chunk.export(temp_flac, format="flac", parameters=["-ac", "2", "-compression_level", "8"])
        else:
            chunk.export(temp_flac, format="flac", parameters=["-compression_level", "8"])
        result['temp_flac'] = temp_flac

    return result

# =============================================================================
# TRACK PROCESSING – MusicBrainz-only mode  (v8.0)
# =============================================================================

def process_single_track_mb_only(chunk_data, i, existing_tracks,
                                  output_folder, existing_tracks_lock, preview_mode=False,
                                  runtime_config=None):
    """Identify a single track using AcoustID + MusicBrainz only (no ACRCloud).

    Workflow
    --------
    1. Export a 12-second sample from the middle of the chunk.
    2. Submit to AcoustID for fingerprint lookup -> recording_id (if AcoustID is available).
    3. If AcoustID didn't produce a result (unavailable, failed, or low-confidence), fall back
       to a MusicBrainz text-search using the sanitised source filename as a free-text query
       (only if the filename contains a separator like ' - ' and looks like a real title).
    4. Fetch full metadata from MusicBrainz via recording_id.
    5. Enrich with iTunes / Deezer / Last.fm (same as normal mode).
    6. Run local BPM detection if Deezer didn't return one.
    7. Merge everything through the standard merge_identification_results().
    """
    file_num = chunk_data.get('file_num', 0)
    chunk = _resolve_chunk(chunk_data)

    if chunk is None:
        return {'status': 'skipped', 'reason': 'no_chunk', 'index': i, 'file_num': file_num}

    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}

    # Read config for runtime flags
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    allow_duplicate_collision = _allows_duplicate_collision(config)

    # -- 1. Shazam + AcoustID fingerprint lookup --
    # Shazam is always enabled in MB mode — it's the best raw identifier,
    # while AcoustID provides the MusicBrainz recording ID for enhanced metadata.
    mb_result = None
    mb_enhanced = {}
    recording_id = None
    art_url = None
    winner_source = "unknown"
    acoustid_attempted = False
    acoustid_result = None
    shazam_result = None
    _shazam_raw = None
    _acoustid_raw = None
    _mb_search_candidates = None

    # Export a single short probe for Shazam (ACRCloud not used in MB mode,
    # so multi-point probes would be wasted — force single).
    _reuse_samples = _export_id_samples(chunk, file_num, i, prefix="temp_mb",
                                        runtime_config=config, force_single=True)
    _reusable = _reuse_samples[0] if _reuse_samples else None

    # Export full chunk once for AcoustID — fpcalc needs up to 120s of audio
    # for reliable matching.  Short probes (~20s, ~500-char fingerprints) are
    # for Shazam; AcoustID's database stores full-track fingerprints.
    _full_wav = _new_runtime_temp_path(
        f"temp_mb_full_{file_num}_{i}_{threading.current_thread().ident}",
        ".wav",
    )
    chunk.export(_full_wav, format="wav")

    try:
        # Always try Shazam first in MB mode (best for raw identification)
        if is_shazam_available():
            shazam_result = identify_with_shazam(chunk, runtime_config=config, sample_path=_reusable)
            if shazam_result:
                mb_result = {
                    'artist': shazam_result['artist'],
                    'title':  shazam_result['title']
                }
                _shazam_raw = {'artist': shazam_result['artist'], 'title': shazam_result['title']}

        # Always run AcoustID too — even when Shazam already identified the track.
        # AcoustID provides the MusicBrainz recording_id needed for enhanced
        # metadata (genres, labels, release dates, etc.).  When Shazam already
        # identified the track, pass its result as a hint so AcoustID returns
        # the matching recording_id instead of a random wrong one.
        if ACOUSTID_AVAILABLE:
            acoustid_attempted = True
            _hint_a = shazam_result['artist'] if shazam_result else None
            _hint_t = shazam_result['title'] if shazam_result else None
            acoustid_result = identify_with_acoustid(
                chunk, sample_path=_full_wav,
                hint_artist=_hint_a, hint_title=_hint_t
            )
            if acoustid_result:
                recording_id = acoustid_result.get('recording_id')
                _acoustid_raw = {'artist': acoustid_result['artist'], 'title': acoustid_result['title'],
                                 'score': acoustid_result.get('score'), 'recording_id': recording_id}

        # -- Pick winner --
        # Shazam ALWAYS wins for artist/title when it has a result.
        # AcoustID's score is fingerprint similarity, NOT identification
        # accuracy — it returns 0.99 for completely wrong recordings.
        # AcoustID's value is the MusicBrainz recording_id, not the artist/title.
        _aid_score = acoustid_result.get('score', 0) if acoustid_result else 0

        if shazam_result and acoustid_result:
            _shz = f"{shazam_result['artist']} - {shazam_result['title']}"
            _aid = f"{acoustid_result['artist']} - {acoustid_result['title']}"
            _agree = (_shz.lower() == _aid.lower())
            if _agree:
                print(f"  [ID] Shazam & AcoustID agree: {_shz} (recording_id={recording_id})")
            else:
                print(f"  [ID] Shazam: {_shz} ← using")
                print(f"  [ID] AcoustID: {_aid} (score {_aid_score:.2f}, recording_id={recording_id})")
            mb_result = {'artist': shazam_result['artist'], 'title': shazam_result['title']}
            winner_source = "shazam"
        elif shazam_result:
            print(f"  [ID] Shazam only: {shazam_result['artist']} - {shazam_result['title']}")
            mb_result = {'artist': shazam_result['artist'], 'title': shazam_result['title']}
            winner_source = "shazam"
        elif acoustid_result:
            print(f"  [ID] AcoustID only: {acoustid_result['artist']} - {acoustid_result['title']} "
                  f"(score {_aid_score:.2f})")
            mb_result = {'artist': acoustid_result['artist'], 'title': acoustid_result['title']}
            winner_source = "acoustid"

        # Fetch enhanced metadata with the best info we have.
        if mb_result:
            mb_enhanced = get_enhanced_metadata(
                mb_result['artist'], mb_result['title'], recording_id
            )
    finally:
        for _s in _reuse_samples:
            if _s and os.path.exists(_s):
                try:
                    os.remove(_s)
                except Exception:
                    pass
        if _full_wav and os.path.exists(_full_wav):
            try:
                os.remove(_full_wav)
            except Exception:
                pass

    # -- 2. MusicBrainz text-search fallback / cross-check --
    # Runs when:
    #   a) Neither Shazam nor AcoustID found anything, OR
    #   b) Only AcoustID found something (Shazam missed) and its score < 0.7 —
    #      cross-check against the filename to catch wrong matches like
    #      "Don't Let Me Down" → "Let Me Down (Don't)" by The Husbands.
    # When Shazam identified the track, we trust it — no cross-check needed.
    _acoustid_score = acoustid_result.get('score', 0) if acoustid_result else 0
    _acoustid_only = bool(mb_result and acoustid_result and not shazam_result)
    _acoustid_low_confidence = bool(_acoustid_only and _acoustid_score < 0.7)

    print(f"  [Filename fallback] mb_result={bool(mb_result)}, shazam={bool(shazam_result)}, "
          f"acoustid_score={_acoustid_score:.2f}, low_conf={_acoustid_low_confidence}, "
          f"will_try={not mb_result or _acoustid_low_confidence}")

    if not mb_result or _acoustid_low_confidence:
        original_file = chunk_data.get('original_file', '')
        print(f"  [Filename fallback] original_file={repr(original_file)}")
        if original_file:
            import re as _re
            raw_stem = os.path.splitext(os.path.basename(original_file))[0]
            query = _re.sub(r'[_\-]+', ' ', raw_stem).strip()
            # Strip leading track numbers: "01 I Want to Hold Your Hand" → "I Want to Hold Your Hand"
            query = _re.sub(r'^\d{1,3}[\s.)\]]*', '', query).strip()

            is_long_enough = len(query) > 5
            looks_like_generic = query.lower().startswith(('file', 'track', 'audio', 'recording'))

            print(f"  [Filename fallback] raw_stem={repr(raw_stem)}, query={repr(query)}, "
                  f"long_enough={is_long_enough}, generic={looks_like_generic}")

            if query and is_long_enough and not looks_like_generic:
                print(f"  [Filename fallback] Searching MusicBrainz for: {query}")
                candidates = musicbrainz_search_recordings(query=query, limit=3)
                _mb_search_candidates = candidates
                print(f"  [Filename fallback] MB returned {len(candidates) if candidates else 0} candidates")
                if candidates:
                    for _ci, _c in enumerate(candidates[:3]):
                        print(f"  [Filename fallback]   #{_ci+1}: {_c.get('artist')} - {_c.get('title')} "
                              f"(score={_c.get('score', '?')})")
                if candidates and candidates[0].get('artist') and candidates[0].get('title'):
                    best = candidates[0]
                    fn_result = {
                        'artist': best['artist'],
                        'title':  best['title']
                    }
                    fn_recording_id = best.get('recording_id')

                    if _acoustid_low_confidence:
                        # Cross-check: AcoustID returned something but at low confidence.
                        # Prefer the filename search result.
                        print(f"  [Cross-check] AcoustID (score {_acoustid_score:.2f}): "
                              f"{mb_result['artist']} - {mb_result['title']}")
                        print(f"  [Cross-check] Filename search: "
                              f"{fn_result['artist']} - {fn_result['title']}")
                        print(f"  [Cross-check] Preferring filename search over "
                              f"low-confidence AcoustID (score < 0.70)")
                    mb_result = fn_result
                    recording_id = fn_recording_id
                    mb_enhanced  = get_enhanced_metadata(
                        best['artist'], best['title'], fn_recording_id
                    )
                    winner_source = "filename_mb"
            else:
                print(f"  [Filename fallback] Skipped — query too short or generic")

    # -- 3. Resolve final artist/title and artwork --
    final_artist = (mb_result or {}).get('artist')
    final_title  = (mb_result or {}).get('title')
    art_url = _resolve_artwork(art_url, final_artist, final_title)

    # -- 4. External metadata + BPM --
    external_meta = (
        get_all_external_metadata(final_artist, final_title, runtime_config=config)
        if final_artist and final_title else {}
    )
    _detect_bpm_if_needed(chunk, external_meta, runtime_config=config)
    essentia_genres = None
    essentia_candidate = None
    if final_artist and final_title:
        essentia_meta = _maybe_enrich_genres_with_essentia(
            chunk,
            external_meta,
            runtime_config=config,
            existing_genre_count_hint=len((mb_enhanced or {}).get("genres") or []),
        )
        if isinstance(essentia_meta, dict):
            essentia_genres = essentia_meta.get("genres_payload")
            essentia_candidate = essentia_meta.get("candidate")

    # -- 5. Merge (acr_result=None because we have no ACRCloud result) --
    merged = merge_identification_results(None, mb_result, mb_enhanced, external_meta)

    # -- 7. Build result --
    if merged['artist']['value'] and merged['title']['value']:
        artist = merged['artist']['value']
        title  = merged['title']['value']
        album  = merged['album']['value'] or 'Unknown Album'

        expected_filename = f"{artist} - {title}.flac".translate(
            str.maketrans('', '', '<>:"/\\|?*')
        )
        with existing_tracks_lock:
            if expected_filename in existing_tracks and not allow_duplicate_collision:
                return {
                    'status': 'skipped', 'reason': 'already_exists',
                    'index': i, 'file_num': file_num,
                    'artist': artist, 'title': title, 'album': album,
                    'original_file': chunk_data.get('original_file'),
                    'chunk_index': chunk_data.get('split_index', 0),
                    'temp_chunk_path': chunk_data.get('temp_chunk_path')
                }
            existing_tracks.add(expected_filename)

        readable_metadata = _build_readable_metadata(merged, artist, title, album)
        enhanced_metadata = _build_enhanced_metadata(merged)
        identification_source = (
            winner_source
            if str(winner_source or "").strip().lower() not in ("", "unknown", "none")
            else (merged['sources_used'][0].lower() if merged['sources_used'] else 'musicbrainz')
        )

        result = {
            'status': 'identified', 'index': i, 'file_num': file_num,
            'artist': artist, 'title': title, 'album': album, 'art_url': art_url,
            'expected_filename': expected_filename,
            'identification_source': identification_source,
            'enhanced_metadata': enhanced_metadata,
            'readable_metadata': readable_metadata,
            'backend_candidates': {
                'shazam': _shazam_raw, 'acoustid': _acoustid_raw,
                'mb_text_search': _mb_search_candidates, 'recording_id': recording_id,
                'winner_src': identification_source,
                'essentia': essentia_candidate,
                'essentia_genres': essentia_genres,
            },
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if not preview_mode:
            temp_flac = os.path.join(output_folder, f"temp_{file_num}_{i}_{threading.current_thread().ident}.flac")
            chunk.export(temp_flac, format="flac")
            result['temp_flac'] = temp_flac

        return result

    else:
        output_format = _resolve_output_format(config)
        output_ext, _, _ = _output_format_export_spec(output_format)
        unidentified_filename = _build_unidentified_filename(chunk_data, file_num, output_ext)
        unidentified_path = _get_unidentified_path(output_folder, unidentified_filename)

        if os.path.exists(unidentified_path) and not allow_duplicate_collision:
            return {
                'status': 'skipped', 'reason': 'already_exists',
                'index': i, 'file_num': file_num,
                'unidentified_filename': unidentified_filename,
                'original_file': chunk_data.get('original_file'),
                'chunk_index': chunk_data.get('split_index', 0),
                'temp_chunk_path': chunk_data.get('temp_chunk_path')
            }

        local_bpm = _detect_bpm_if_needed(chunk, runtime_config=config)

        result = {
            'status': 'unidentified', 'index': i, 'file_num': file_num,
            'unidentified_filename': unidentified_filename,
            'unidentified_path':     unidentified_path,
            'backend_candidates': {
                'shazam': _shazam_raw, 'acoustid': _acoustid_raw,
                'mb_text_search': _mb_search_candidates, 'recording_id': recording_id
            },
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if local_bpm and local_bpm.get('bpm'):
            result['detected_bpm']    = local_bpm['bpm']
            result['bpm_confidence']  = local_bpm.get('confidence', 0)

        if not preview_mode:
            saved_path = _export_unidentified_chunk(chunk, unidentified_path, output_format)
            result['unidentified_path'] = saved_path
            result['unidentified_filename'] = os.path.basename(saved_path)

        return result


# =============================================================================
# TRACK PROCESSING – Dual mode (Best of Both)  (v8.0)
# =============================================================================

def process_single_track_dual(chunk_data, i, recognizer, rate_limiter, existing_tracks,
                              output_folder, existing_tracks_lock, preview_mode=False,
                              runtime_config=None):
    """
    Identify track using the full pipeline: ACRCloud > Shazam > AcoustID.

    ACRCloud is primary.  When it misses, Shazam is preferred for artist/title.
    AcoustID always runs for the MusicBrainz recording_id, using hints from
    whichever service identified the track so it returns the correct recording
    rather than a random fingerprint match.  Filename fallback if all else fails.
    """
    file_num = chunk_data.get('file_num', 0)
    chunk = _resolve_chunk(chunk_data)

    if chunk is None:
        return {'status': 'skipped', 'reason': 'no_chunk', 'index': i, 'file_num': file_num}

    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}

    # Read config for runtime flags
    config = runtime_config if isinstance(runtime_config, dict) else get_config()
    shazam_enabled = not bool(config.get('disable_shazam', False))
    show_id_source = bool(config.get('show_id_source', True))
    allow_duplicate_collision = _allows_duplicate_collision(config)

    # Export short probe samples for ACRCloud/Shazam identification.
    temp_names = _export_id_samples(chunk, file_num, i, prefix="temp_dual", runtime_config=config)
    reusable_sample = temp_names[0] if temp_names else None

    # Export full chunk once for AcoustID — fpcalc needs up to 120s of audio
    # for reliable matching.  Shared via sample_path so identify_with_acoustid()
    # doesn't re-export the entire track a second time.
    _full_wav = _new_runtime_temp_path(
        f"temp_dual_full_{file_num}_{i}_{threading.current_thread().ident}",
        ".wav",
    )
    chunk.export(_full_wav, format="wav")

    # --- Dual mode pipeline: ACRCloud > Shazam > AcoustID (for recording_id) ---
    # ACRCloud is primary.  When it misses, Shazam is preferred for artist/title.
    # AcoustID always runs for the MusicBrainz recording_id, using hint_artist/
    # hint_title from whichever service identified the track so it returns the
    # correct recording rather than a random fingerprint match.

    acr_result = None
    acr_confidence = 0
    recording_id = None

    # --- Step 1: ACRCloud ---
    _trace = is_trace_enabled()
    rate_limiter.wait()
    if _trace:
        print(f"  [ACRCloud] attempted...")
    try:
        res = {}
        for temp_name in temp_names:
            res = json.loads(recognizer.recognize_by_file(temp_name, 0))
            if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
                break
            rate_limiter.wait()

        if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
            music = res["metadata"]["music"][0]
            acr_result = {
                'artist': music["artists"][0]["name"],
                'title': music["title"],
                'album': music.get("album", {}).get("name", "Unknown Album"),
                'art_url': find_art_in_json(music)
            }
            acr_confidence = res.get("status", {}).get("score", 85)
            if _trace:
                print(f"  [ACRCloud] -> hit: {acr_result['artist']} - {acr_result['title']}")
        elif _trace:
            print(f"  [ACRCloud] -> miss")
    except Exception:
        if _trace:
            print(f"  [ACRCloud] -> miss (error)")

    # --- Step 2: Shazam (always runs — best at raw identification) ---
    shazam_result = None
    if shazam_enabled and is_shazam_available():
        try:
            _shazam_data = identify_with_shazam(chunk, runtime_config=config, sample_path=reusable_sample)
            if _shazam_data and _shazam_data.get('title'):
                shazam_result = {
                    'artist': _shazam_data['artist'],
                    'title': _shazam_data['title'],
                    'album': _shazam_data.get('album', 'Unknown Album'),
                }
        except Exception:
            pass

    # --- Step 3: Determine the identification winner ---
    # ACRCloud > Shazam.  Shazam is fallback when ACRCloud misses.
    winner = None
    winner_source = None
    winner_source_key = "unknown"
    winner_confidence = 0
    art_url = None
    candidates = []

    if acr_result:
        winner = acr_result
        art_url = acr_result.get('art_url')
        winner_source = f"ACRCloud ({acr_confidence:.0f}%)"
        winner_source_key = "acrcloud"
        winner_confidence = acr_confidence
        candidates.append({"src": "acrcloud", "data": acr_result, "confidence": acr_confidence,
                           "priority": 2, "art_url": art_url, "recording_id": None})
        if shazam_result:
            candidates.append({"src": "shazam", "data": shazam_result, "confidence": 60,
                               "priority": 0, "art_url": None, "recording_id": None})
    elif shazam_result:
        winner = shazam_result
        winner_source = "Shazam"
        winner_source_key = "shazam"
        winner_confidence = 85
        candidates.append({"src": "shazam", "data": shazam_result, "confidence": 85,
                           "priority": 0, "art_url": None, "recording_id": None})

    # --- Step 4: AcoustID — always runs for MusicBrainz recording_id ---
    # Uses hint from the winning service so it returns the correct recording.
    aid_result = None
    aid_confidence = 0
    if ACOUSTID_AVAILABLE:
        _hint_a = winner['artist'] if winner else None
        _hint_t = winner['title'] if winner else None
        try:
            acoustid_data = identify_with_acoustid(chunk, sample_path=_full_wav,
                                                   hint_artist=_hint_a, hint_title=_hint_t)
            if acoustid_data and acoustid_data.get('title'):
                aid_result = {
                    'artist': acoustid_data['artist'],
                    'title': acoustid_data['title'],
                    'album': acoustid_data.get('album', 'Unknown Album'),
                }
                recording_id = acoustid_data.get('recording_id')
                aid_confidence = (acoustid_data.get('score', 0.0) or 0.0) * 100
                candidates.append({"src": "acoustid", "data": aid_result, "confidence": aid_confidence,
                                   "priority": 3, "art_url": None, "recording_id": recording_id})

                # If nobody else identified it, AcoustID is the winner
                if not winner:
                    winner = aid_result
                    winner_source = f"AcoustID ({aid_confidence:.0f}%)"
                    winner_source_key = "acoustid"
                    winner_confidence = aid_confidence
        except Exception:
            pass

    # --- Step 5: Filename fallback ---
    # If nobody identified the track, or only AcoustID did at low confidence,
    # try a MusicBrainz text search using the source filename.
    _only_acoustid = (
        winner and aid_result and not shazam_result and not acr_result
        and aid_confidence < 70
    )
    if not winner or _only_acoustid:
        import re as _re
        _orig = chunk_data.get('original_file', '')
        if _orig:
            _fn_query = os.path.splitext(os.path.basename(_orig))[0]
            _fn_query = _re.sub(r'[_\-]+', ' ', _fn_query).strip()
            _fn_query = _re.sub(r'^\d{1,3}[\s.)\]]*', '', _fn_query).strip()

            _fn_long = len(_fn_query) > 5
            _fn_generic = _fn_query.lower().startswith(('file', 'track', 'audio', 'recording'))

            if _fn_query and _fn_long and not _fn_generic:
                _fn_candidates = musicbrainz_search_recordings(query=_fn_query, limit=3)
                if _fn_candidates and _fn_candidates[0].get('artist') and _fn_candidates[0].get('title'):
                    _fn_best = _fn_candidates[0]
                    if _only_acoustid:
                        print(f"  [Cross-check] AcoustID ({aid_confidence:.0f}%): "
                              f"{winner['artist']} - {winner['title']}")
                        print(f"  [Cross-check] Filename search: "
                              f"{_fn_best['artist']} - {_fn_best['title']}")
                        print(f"  [Cross-check] Preferring filename search over "
                              f"low-confidence AcoustID")
                    winner = {
                        'artist': _fn_best['artist'],
                        'title': _fn_best['title'],
                        'album': _fn_best.get('album', 'Unknown Album'),
                    }
                    recording_id = _fn_best.get('recording_id')
                    winner_source = "Filename (MB search)"
                    winner_source_key = "filename_mb"
                    winner_confidence = 75
                    candidates.append({
                        "src": "filename_mb", "data": winner, "confidence": 75,
                        "priority": 1, "art_url": None, "recording_id": recording_id,
                    })

    if winner and show_id_source:
        print_id_winner(i + 1, winner_source_key, winner["artist"], winner["title"])
    elif show_id_source:
        print_id_winner(i + 1, "none")

    # Clean up sample files now that all backends are done
    for temp_name in temp_names:
        if temp_name and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except Exception:
                pass
    if _full_wav and os.path.exists(_full_wav):
        try:
            os.remove(_full_wav)
        except Exception:
            pass

    if winner:
        artist = winner['artist']
        title = winner['title']
        album = winner.get('album', 'Unknown Album')

        mb_enhanced = get_enhanced_metadata(artist, title, recording_id) if recording_id else {}
        art_url = _resolve_artwork(art_url, artist, title)

        external_meta = get_all_external_metadata(artist, title, runtime_config=config) or {}
        _detect_bpm_if_needed(chunk, external_meta, runtime_config=config)
        essentia_meta = _maybe_enrich_genres_with_essentia(
            chunk,
            external_meta,
            runtime_config=config,
            existing_genre_count_hint=len((mb_enhanced or {}).get("genres") or []),
        )
        essentia_genres = None
        essentia_candidate = None
        if isinstance(essentia_meta, dict):
            essentia_genres = essentia_meta.get("genres_payload")
            essentia_candidate = essentia_meta.get("candidate")

        mb_result = {'artist': artist, 'title': title, 'album': album}
        acr_for_merge = acr_result if winner_source and 'ACRCloud' in str(winner_source) else None
        merged = merge_identification_results(acr_for_merge, mb_result, mb_enhanced, external_meta)

        expected_filename = f"{artist} - {title}.flac".translate(str.maketrans('', '', '<>:"/\\|?*'))
        with existing_tracks_lock:
            if expected_filename in existing_tracks and not allow_duplicate_collision:
                return {'status': 'skipped', 'reason': 'duplicate', 'index': i, 'file_num': file_num,
                        'artist': artist, 'title': title}
            existing_tracks.add(expected_filename)

        enhanced_metadata = _build_enhanced_metadata(merged)
        confidence = winner_confidence / 100.0

        _serializable_candidates = [
            {'src': c['src'], 'confidence': c['confidence'], 'priority': c['priority'],
             'artist': c['data'].get('artist'), 'title': c['data'].get('title'),
             'album': c['data'].get('album', 'Unknown Album'), 'recording_id': c.get('recording_id')}
            for c in candidates
        ]
        identification_source = (
            winner_source_key
            if str(winner_source_key or "").strip().lower() not in ("", "unknown", "none")
            else "unknown"
        )

        result = {
            'status': 'identified', 'index': i, 'file_num': file_num,
            'artist': artist, 'title': title, 'album': album, 'art_url': art_url,
            'expected_filename': expected_filename,
            'identification_source': identification_source,
            'dual_comparison': winner_source,
            'confidence': confidence,
            'enhanced_metadata': enhanced_metadata,
            'backend_candidates': {
                'dual_candidates': _serializable_candidates,
                'winner_src': identification_source,
                'recording_id': recording_id,
                'essentia': essentia_candidate,
                'essentia_genres': essentia_genres,
            },
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if not preview_mode:
            temp_flac = os.path.join(output_folder, f"temp_dual_{file_num}_{i}_{threading.current_thread().ident}.flac")
            chunk.export(temp_flac, format="flac")
            result['temp_flac'] = temp_flac

        return result

    else:
        output_format = _resolve_output_format(config)
        output_ext, _, _ = _output_format_export_spec(output_format)
        unidentified_filename = _build_unidentified_filename(chunk_data, file_num, output_ext)
        unidentified_path = _get_unidentified_path(output_folder, unidentified_filename)

        result = {
            'status': 'unidentified', 'index': i, 'file_num': file_num,
            'unidentified_filename': unidentified_filename,
            'unidentified_path': unidentified_path,
            'backend_candidates': {'dual_candidates': [], 'recording_id': None},
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),
            'temp_chunk_path': chunk_data.get('temp_chunk_path')
        }

        if not preview_mode:
            saved_path = _export_unidentified_chunk(chunk, unidentified_path, output_format)
            result['unidentified_path'] = saved_path
            result['unidentified_filename'] = os.path.basename(saved_path)

        return result
