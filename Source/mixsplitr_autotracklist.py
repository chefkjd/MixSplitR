#!/usr/bin/env python3
"""
mixsplitr_autotracklist.py - Auto timeline + timestamp export for long DJ mixes.

This mode scans overlapping windows, runs available recognizers, fuses confidence,
merges contiguous matches into timeline segments, and always writes a timestamp file.
"""

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone

from pydub import AudioSegment
from pydub.silence import detect_silence

from mixsplitr_identify import identify_with_acoustid, identify_with_shazam, is_shazam_available
from mixsplitr_essentia import (
    DEFAULT_TRANSITION_ENABLED,
    DEFAULT_TRANSITION_MAX_POINTS,
    DEFAULT_TRANSITION_MIN_CONFIDENCE,
    EssentiaTransitionConfig,
    detect_transition_points as _detect_shared_essentia_transition_points,
    get_runtime_status as _get_shared_essentia_runtime_status,
)
from mixsplitr_utils import clamp as _clamp, get_runtime_temp_directory as _get_runtime_temp_directory

try:
    from mixsplitr_spectral import detect_spectral_transitions as _detect_spectral_transitions
    _SPECTRAL_AVAILABLE = True
except Exception:
    _SPECTRAL_AVAILABLE = False

    def _detect_spectral_transitions(audio, min_segment_seconds=15.0, max_points=120):
        return [], {"spectral_available": False}

try:
    from tqdm import tqdm
    _TQDM_AVAILABLE = True
except Exception:
    _TQDM_AVAILABLE = False


_UNKNOWN_KEY = "__unknown__"
_SOURCE_PRIORITY = {
    "acrcloud": 3,
    "shazam": 2,
    "acoustid": 1,
}
_TITLE_VARIANT_KEYWORDS = (
    "remaster",
    "mix",
    "version",
    "edit",
    "mono",
    "stereo",
    "live",
    "acoustic",
    "demo",
    "instrumental",
    "session",
    "take",
    "anniversary",
    "deluxe",
)
_DBFS_FLOOR = -120.0
_AUTO_DISABLE_SHAZAM_STEP_SECONDS = 10
_SILENCE_INJECT_MIN_SECONDS_NO_HIT_SUPPORT = 2.6
_AUTO_SHORT_CIRCUIT_CONFIDENCE = 0.96
_AUTO_SHAZAM_MAX_WINDOWS = 120
_AUTO_SHAZAM_TIMEOUT_SECONDS = 8
_AUTO_SHAZAM_CONSECUTIVE_FAIL_LIMIT = 3
_AUTO_SILENCE_MIN_MS = 1600
_AUTO_SILENCE_THRESH_DB = -42
_AUTO_SILENCE_SCAN_CHUNK_MS = 900000
_AUTO_SILENCE_MAX_SEGMENT_SECONDS = 360
_AUTO_SILENCE_FIRST_MIN_ANCHORS = 5
_AUTO_PERSISTENCE_MIN_WINDOWS = 2
_AUTO_MICRO_REFINE_BACKTRACK_SECONDS = 8.0
_AUTO_MICRO_REFINE_FORWARD_SECONDS = 3.0
_AUTO_MICRO_REFINE_STEP_SECONDS = 0.5
_AUTO_MICRO_REFINE_MIN_CONFIDENCE = 0.46
_AUTO_ESSENTIA_ENABLED = DEFAULT_TRANSITION_ENABLED
_AUTO_ESSENTIA_MIN_CONFIDENCE = DEFAULT_TRANSITION_MIN_CONFIDENCE
_AUTO_ESSENTIA_MAX_POINTS = DEFAULT_TRANSITION_MAX_POINTS
_AUTO_ESSENTIA_RADIUS_SECONDS = 1.8


def is_auto_tracklist_dry_run_enabled():
    """Environment gate for no-hardware/no-network orchestration tests."""
    return os.environ.get("MIXSPLITR_AUTOTRACKLIST_DRY_RUN", "").strip().lower() in (
        "1", "true", "yes", "on"
    )




def _safe_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return _clamp(parsed, minimum, maximum)


def _safe_float(value, default, minimum, maximum):
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return _clamp(parsed, minimum, maximum)


def _to_confidence(score_value, default_value):
    try:
        conf = float(score_value)
    except Exception:
        conf = default_value
    if conf > 1.0:
        conf = conf / 100.0
    return _clamp(conf, 0.0, 1.0)




def get_essentia_runtime_diagnostics():
    runtime = _get_shared_essentia_runtime_status()
    return runtime.to_dict()


def _detect_essentia_transition_points(audio, settings):
    """
    Detect transition candidates using Essentia onset/novelty analysis.
    Returns sorted point dicts: {"point_sec": ..., "confidence": ...}
    """
    settings["essentia_relaxation_applied"] = False
    settings["essentia_relaxation_passes"] = 0
    settings["essentia_raw_onset_count"] = 0
    settings["essentia_peak_fallback_count"] = 0
    transition_result = _detect_shared_essentia_transition_points(
        audio,
        EssentiaTransitionConfig(
            enabled=bool(settings.get("essentia_enabled", _AUTO_ESSENTIA_ENABLED)),
            min_confidence=float(settings.get("essentia_min_confidence", _AUTO_ESSENTIA_MIN_CONFIDENCE)),
            max_points=int(settings.get("essentia_max_points", _AUTO_ESSENTIA_MAX_POINTS)),
        ),
    )
    diagnostics = transition_result.diagnostics or {}
    settings["essentia_relaxation_applied"] = bool(diagnostics.get("essentia_relaxation_applied", False))
    settings["essentia_relaxation_passes"] = int(diagnostics.get("essentia_relaxation_passes", 0) or 0)
    settings["essentia_raw_onset_count"] = int(diagnostics.get("essentia_raw_onset_count", 0) or 0)
    settings["essentia_peak_fallback_count"] = int(diagnostics.get("essentia_peak_fallback_count", 0) or 0)
    return list(transition_result.points or [])


def _format_timestamp(seconds):
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _safe_seconds_value(value, default=0.0):
    try:
        return max(0.0, float(value))
    except Exception:
        return max(0.0, float(default or 0.0))


def _slugify(value):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "mix"


def _match_key(artist, title):
    if not artist or not title:
        return _UNKNOWN_KEY
    normal = f"{artist}::{title}".lower().strip()
    normal = re.sub(r"[^a-z0-9]+", "", normal)
    return normal or _UNKNOWN_KEY


def _contains_title_variant_token(value):
    lowered = str(value or "").lower()
    if not lowered:
        return False
    if re.search(r"\b(19|20)\d{2}\b", lowered):
        return True
    return any(token in lowered for token in _TITLE_VARIANT_KEYWORDS)


def _normalize_canonical_title(title):
    text = str(title or "")
    if not text:
        return ""

    text = text.replace("\u2013", "-").replace("\u2014", "-")

    def _paren_repl(match):
        candidate = (match.group(1) or "").strip()
        return " " if _contains_title_variant_token(candidate) else f" {candidate} "

    text = re.sub(r"[\(\[]([^\)\]]+)[\)\]]", _paren_repl, text)

    parts = [part.strip() for part in re.split(r"\s+-\s+", text) if part.strip()]
    if parts:
        head = parts[0]
        tail = parts[1:]
        if tail and all(_contains_title_variant_token(part) for part in tail):
            text = head
        else:
            text = " - ".join([head] + tail)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonical_match_key(artist, title):
    normalized_title = _normalize_canonical_title(title)
    return _match_key(artist, normalized_title or title)


def _keys_are_mergeable(left_match_key, left_canonical_key, right_match_key, right_canonical_key):
    left_match = left_match_key or _UNKNOWN_KEY
    right_match = right_match_key or _UNKNOWN_KEY
    if left_match == right_match and left_match != _UNKNOWN_KEY:
        return True

    left_canonical = left_canonical_key or _UNKNOWN_KEY
    right_canonical = right_canonical_key or _UNKNOWN_KEY
    return left_canonical == right_canonical and left_canonical != _UNKNOWN_KEY


def _dbfs_or_floor(audio_chunk):
    try:
        value = float(audio_chunk.dBFS)
    except Exception:
        return _DBFS_FLOOR
    if math.isinf(value) or math.isnan(value):
        return _DBFS_FLOOR
    return value


def _window_dbfs(audio, center_sec, half_window_ms=160):
    total_ms = max(0, len(audio))
    if total_ms <= 0:
        return _DBFS_FLOOR
    center_ms = int(round(float(center_sec) * 1000.0))
    start_ms = max(0, center_ms - int(half_window_ms))
    end_ms = min(total_ms, center_ms + int(half_window_ms))
    if end_ms <= start_ms:
        return _DBFS_FLOOR
    return _dbfs_or_floor(audio[start_ms:end_ms])


def _find_quiet_point(audio, start_sec, end_sec, center_sec, frame_ms=180, step_ms=60):
    total_ms = max(0, len(audio))
    if total_ms <= 0:
        return None, _DBFS_FLOOR

    start_ms = max(0, int(round(float(start_sec) * 1000.0)))
    end_ms = min(total_ms, int(round(float(end_sec) * 1000.0)))
    if end_ms - start_ms < frame_ms:
        return None, _DBFS_FLOOR

    center_ms = int(round(float(center_sec) * 1000.0))
    best_ms = None
    best_db = None
    best_center_distance = None
    for pos_ms in range(start_ms, end_ms - frame_ms + 1, step_ms):
        chunk = audio[pos_ms:pos_ms + frame_ms]
        dbfs = _dbfs_or_floor(chunk)
        candidate_ms = pos_ms + (frame_ms // 2)
        distance = abs(candidate_ms - center_ms)
        if best_db is None or dbfs < best_db or (dbfs == best_db and distance < best_center_distance):
            best_db = dbfs
            best_ms = candidate_ms
            best_center_distance = distance

    if best_ms is None:
        return None, _DBFS_FLOOR
    return float(best_ms) / 1000.0, float(best_db)


def _detect_split_style_silence_anchors(
    audio,
    min_silence_ms=2000,
    silence_thresh_db=-37,
    progress_callback=None,
    cancel_callback=None,
    chunk_ms=_AUTO_SILENCE_SCAN_CHUNK_MS,
):
    """
    Detect split anchors with the same silence profile used by assisted split mode.
    Returns anchor dicts with midpoint + duration info.
    """
    total_ms = max(0, len(audio))
    if total_ms <= 0:
        return []
    min_silence_len = int(max(100, min_silence_ms))
    overlap_ms = max(500, min_silence_len)
    try:
        chunk_size_ms = int(max(min_silence_len * 4, chunk_ms))
    except Exception:
        chunk_size_ms = _AUTO_SILENCE_SCAN_CHUNK_MS

    def _emit_progress(current, total):
        if progress_callback is None:
            return
        try:
            progress_callback(
                int(current),
                int(max(1, total)),
                f"Detecting silence anchors {int(current)}/{int(max(1, total))}",
            )
        except Exception:
            pass

    silent_ranges = []
    try:
        if total_ms <= chunk_size_ms:
            if cancel_callback is not None:
                cancel_callback()
            silent_ranges = detect_silence(
                audio,
                min_silence_len=min_silence_len,
                silence_thresh=int(silence_thresh_db),
            )
            _emit_progress(1, 1)
        else:
            stride_ms = max(min_silence_len, chunk_size_ms - overlap_ms)
            chunk_starts = []
            chunk_start = 0
            while True:
                chunk_starts.append(int(chunk_start))
                if (chunk_start + chunk_size_ms) >= total_ms:
                    break
                chunk_start += stride_ms
            total_chunks = max(1, len(chunk_starts))
            for chunk_index, chunk_start in enumerate(chunk_starts, start=1):
                if cancel_callback is not None:
                    cancel_callback()
                chunk_end = min(total_ms, int(chunk_start) + chunk_size_ms)
                local_ranges = detect_silence(
                    audio[int(chunk_start):int(chunk_end)],
                    min_silence_len=min_silence_len,
                    silence_thresh=int(silence_thresh_db),
                )
                for local_start, local_end in local_ranges:
                    silent_ranges.append((int(chunk_start) + int(local_start), int(chunk_start) + int(local_end)))
                _emit_progress(chunk_index, total_chunks)
    except Exception:
        return []

    merged_ranges = []
    merge_gap_ms = max(25, min_silence_len // 3)
    for start_ms, end_ms in sorted(silent_ranges, key=lambda item: (int(item[0]), int(item[1]))):
        start_i = int(start_ms)
        end_i = int(end_ms)
        if end_i <= start_i:
            continue
        if merged_ranges and start_i <= (merged_ranges[-1][1] + merge_gap_ms):
            merged_ranges[-1][1] = max(merged_ranges[-1][1], end_i)
        else:
            merged_ranges.append([start_i, end_i])

    anchors = []
    for start_i, end_i in merged_ranges:
        duration_ms = max(0, end_i - start_i)
        midpoint = (start_i + end_i) // 2
        if midpoint <= 0 or midpoint >= total_ms:
            continue
        anchors.append({
            "point_sec": round(midpoint / 1000.0, 3),
            "duration_sec": round(duration_ms / 1000.0, 3),
            "start_sec": round(start_i / 1000.0, 3),
            "end_sec": round(end_i / 1000.0, 3),
        })
    return sorted(anchors, key=lambda item: float(item.get("point_sec", 0.0)))






def _hit_keys(hit):
    if not hit:
        return _UNKNOWN_KEY, _UNKNOWN_KEY
    artist = hit.get("artist")
    title = hit.get("title")
    match_key = hit.get("match_key") or _match_key(artist, title)
    canonical_key = hit.get("canonical_key") or _canonical_match_key(artist, title)
    return match_key or _UNKNOWN_KEY, canonical_key or _UNKNOWN_KEY


def _hit_is_known(hit):
    match_key, canonical_key = _hit_keys(hit)
    return (match_key != _UNKNOWN_KEY) or (canonical_key != _UNKNOWN_KEY)


def _nearest_hits_around_point(point_sec, hits, max_distance_seconds):
    left_hit = None
    right_hit = None
    left_delta = None
    right_delta = None
    max_distance = float(max(0.0, max_distance_seconds))

    for hit in hits or []:
        start = float(hit.get("start_sec", 0.0))
        end = float(hit.get("end_sec", start))
        center = (start + end) / 2.0
        delta = center - float(point_sec)
        if delta <= 0.0:
            if abs(delta) <= max_distance and (left_delta is None or delta > left_delta):
                left_hit = hit
                left_delta = delta
        else:
            if delta <= max_distance and (right_delta is None or delta < right_delta):
                right_hit = hit
                right_delta = delta
    return left_hit, right_hit


def _anchor_indicates_transition(point_sec, hits, settings):
    step_seconds = float(settings.get("step_seconds", 30))
    max_distance = max(8.0, step_seconds * 1.8)
    left_hit, right_hit = _nearest_hits_around_point(point_sec, hits, max_distance)
    if not left_hit or not right_hit:
        return False, left_hit, right_hit

    left_known = _hit_is_known(left_hit)
    right_known = _hit_is_known(right_hit)
    if left_known != right_known:
        return True, left_hit, right_hit
    if not left_known and not right_known:
        return False, left_hit, right_hit

    left_match, left_canonical = _hit_keys(left_hit)
    right_match, right_canonical = _hit_keys(right_hit)
    changed = not _keys_are_mergeable(
        left_match,
        left_canonical,
        right_match,
        right_canonical,
    )
    return bool(changed), left_hit, right_hit


def _apply_hit_to_segment(segment, hit):
    if not segment or not hit or not _hit_is_known(hit):
        return
    artist = str(hit.get("artist") or "").strip()
    title = str(hit.get("title") or "").strip()
    if not artist or not title:
        return

    match_key, canonical_key = _hit_keys(hit)
    segment["artist"] = artist
    segment["title"] = title
    segment["confidence"] = float(hit.get("confidence", segment.get("confidence", 0.0)))
    segment["source"] = hit.get("source", segment.get("source", "unknown"))
    segment["match_key"] = match_key
    segment["canonical_key"] = canonical_key

    merged_sources = list(segment.get("sources_tried") or [])
    for source in hit.get("sources_tried") or []:
        if source not in merged_sources:
            merged_sources.append(source)
    segment["sources_tried"] = merged_sources


def _anchor_has_audible_sides(audio, point_sec, side_offset_sec=0.9, min_side_dbfs=-46.0):
    if audio is None:
        return True
    left_center = max(0.0, float(point_sec) - float(side_offset_sec))
    right_center = max(0.0, float(point_sec) + float(side_offset_sec))
    left_db = _window_dbfs(audio, left_center, half_window_ms=220)
    right_db = _window_dbfs(audio, right_center, half_window_ms=220)
    return left_db > float(min_side_dbfs) and right_db > float(min_side_dbfs)


def _copy_hit_identity(target_hit, source_hit):
    if not target_hit:
        return
    if not source_hit:
        target_hit["artist"] = ""
        target_hit["title"] = ""
        target_hit["confidence"] = 0.0
        target_hit["source"] = "unknown"
        target_hit["match_key"] = _UNKNOWN_KEY
        target_hit["canonical_key"] = _UNKNOWN_KEY
        return

    artist = str(source_hit.get("artist") or "").strip()
    title = str(source_hit.get("title") or "").strip()
    target_hit["artist"] = artist
    target_hit["title"] = title
    target_hit["confidence"] = float(source_hit.get("confidence", target_hit.get("confidence", 0.0)))
    target_hit["source"] = source_hit.get("source", target_hit.get("source", "unknown"))
    target_hit["match_key"] = source_hit.get("match_key") or _match_key(artist, title)
    target_hit["canonical_key"] = source_hit.get("canonical_key") or _canonical_match_key(artist, title)
    merged_sources = list(target_hit.get("sources_tried") or [])
    for src in source_hit.get("sources_tried") or []:
        if src not in merged_sources:
            merged_sources.append(src)
    target_hit["sources_tried"] = merged_sources


def _build_hit_runs(hits):
    runs = []
    if not hits:
        return runs

    run_start = 0
    run_match, run_canonical = _hit_keys(hits[0])

    for idx in range(1, len(hits) + 1):
        if idx < len(hits):
            next_match, next_canonical = _hit_keys(hits[idx])
            if _keys_are_mergeable(run_match, run_canonical, next_match, next_canonical):
                if run_match == _UNKNOWN_KEY and run_canonical == _UNKNOWN_KEY:
                    run_match, run_canonical = next_match, next_canonical
                continue

        runs.append({
            "start": run_start,
            "end": idx,
            "match_key": run_match,
            "canonical_key": run_canonical,
            "length": idx - run_start,
        })
        if idx < len(hits):
            run_start = idx
            run_match, run_canonical = _hit_keys(hits[idx])
    return runs


def _run_mean_confidence(hits, run):
    if not run:
        return 0.0
    start = int(run.get("start", 0))
    end = int(run.get("end", start))
    if end <= start:
        return 0.0
    values = []
    for idx in range(start, end):
        try:
            values.append(float(hits[idx].get("confidence", 0.0)))
        except Exception:
            continue
    if not values:
        return 0.0
    return sum(values) / float(len(values))




def _boundary_hit_support(point_sec, left_segment, right_segment, hits, step_seconds):
    left_match = left_segment.get("match_key", _UNKNOWN_KEY)
    left_canonical = left_segment.get("canonical_key", _UNKNOWN_KEY)
    right_match = right_segment.get("match_key", _UNKNOWN_KEY)
    right_canonical = right_segment.get("canonical_key", _UNKNOWN_KEY)
    if (
        (left_match == _UNKNOWN_KEY and left_canonical == _UNKNOWN_KEY)
        or (right_match == _UNKNOWN_KEY and right_canonical == _UNKNOWN_KEY)
    ):
        return 0.0, 0.0

    lookaround = max(4.0, float(step_seconds) * 1.8)
    left_support = 0.0
    right_support = 0.0
    for hit in hits or []:
        start = float(hit.get("start_sec", 0.0))
        end = float(hit.get("end_sec", start))
        center = (start + end) / 2.0
        hit_match, hit_canonical = _hit_keys(hit)
        confidence = _clamp(float(hit.get("confidence", 0.0)), 0.0, 1.0)
        weight = 0.55 + (confidence * 0.9)

        if (point_sec - lookaround) <= center < point_sec:
            if _keys_are_mergeable(left_match, left_canonical, hit_match, hit_canonical):
                left_support += weight
        elif point_sec <= center <= (point_sec + lookaround):
            if _keys_are_mergeable(right_match, right_canonical, hit_match, hit_canonical):
                right_support += weight

    return left_support, right_support


def _boundary_novelty_score(audio, point_sec):
    """
    Estimate local transition novelty from short/long energy deltas.
    Returns a roughly 0..2 score where higher suggests stronger transition evidence.
    """
    near_offset = 0.24
    far_offset = 0.92

    left_near = _window_dbfs(audio, max(0.0, float(point_sec) - near_offset), half_window_ms=150)
    right_near = _window_dbfs(audio, float(point_sec) + near_offset, half_window_ms=150)
    left_far = _window_dbfs(audio, max(0.0, float(point_sec) - far_offset), half_window_ms=260)
    right_far = _window_dbfs(audio, float(point_sec) + far_offset, half_window_ms=260)

    near_delta = abs(right_near - left_near)
    far_delta = abs(right_far - left_far)
    slope_delta = abs((right_near - right_far) - (left_near - left_far))
    novelty = (near_delta * 0.58) + (far_delta * 0.22) + (slope_delta * 0.30)
    novelty = novelty / 10.0
    return _clamp(novelty, 0.0, 2.0)


def _boundary_macro_novelty_score(audio, point_sec):
    """
    Wider-timescale novelty to detect gradual track transitions.

    Compares average energy over larger windows farther from the boundary.
    Track transitions cause energy-profile shifts over 2-6 second spans that
    the micro-novelty (0.24/0.92s) cannot capture.  This is especially useful
    in continuous mixes where individual tracks blend without silence gaps.
    Returns a roughly 0..2 score.
    """
    # Medium range: centres ±2.5s from boundary, 1.5s half-window each side
    left_med = _window_dbfs(audio, max(0.0, float(point_sec) - 2.5), half_window_ms=1500)
    right_med = _window_dbfs(audio, float(point_sec) + 2.5, half_window_ms=1500)
    med_delta = abs(right_med - left_med)

    # Wide range: centres ±5.5s from boundary, 2.5s half-window each side
    left_wide = _window_dbfs(audio, max(0.0, float(point_sec) - 5.5), half_window_ms=2500)
    right_wide = _window_dbfs(audio, float(point_sec) + 5.5, half_window_ms=2500)
    wide_delta = abs(right_wide - left_wide)

    # Cross-scale slope: how much the near-vs-far relationship changes
    macro_slope = abs((right_med - right_wide) - (left_med - left_wide))

    score = (med_delta * 0.50) + (wide_delta * 0.25) + (macro_slope * 0.25)
    score = score / 10.0
    return _clamp(score, 0.0, 2.0)


def _essentia_proximity_support(point_sec, essentia_points, radius_seconds=_AUTO_ESSENTIA_RADIUS_SECONDS):
    if not essentia_points:
        return 0.0
    radius = max(0.1, float(radius_seconds))
    sigma = max(0.08, radius * 0.45)
    support = 0.0
    lower = float(point_sec) - radius
    upper = float(point_sec) + radius
    for item in essentia_points:
        point = float(item.get("point_sec", 0.0))
        if point < lower:
            continue
        if point > upper:
            break
        confidence = _clamp(float(item.get("confidence", 0.0)), 0.0, 1.0)
        distance = abs(point - float(point_sec))
        support += confidence * math.exp(-((distance * distance) / (2.0 * sigma * sigma)))
    return _clamp(support, 0.0, 2.0)


def _detect_novelty_transition_points(audio, settings, essentia_points=None):
    if audio is None:
        return []
    total_seconds = float(max(0.0, len(audio))) / 1000.0
    if total_seconds <= 6.0:
        return []

    min_segment_seconds = max(8.0, float(settings.get("min_segment_seconds", 30.0)))
    sample_step = 0.5 if min_segment_seconds <= 48.0 else 0.75
    min_seconds = 2.0
    max_seconds = max(2.0, total_seconds - 2.0)

    samples = []
    point_sec = min_seconds
    while point_sec <= (max_seconds + 1e-9):
        if _anchor_has_audible_sides(
            audio,
            point_sec,
            side_offset_sec=0.55,
            min_side_dbfs=-58.0,
        ):
            novelty = _boundary_novelty_score(audio, point_sec)
            macro_novelty = _boundary_macro_novelty_score(audio, point_sec)
            essentia_support = _essentia_proximity_support(point_sec, essentia_points or [])
            center_db = _window_dbfs(audio, point_sec, half_window_ms=180)
            quiet_bonus = _clamp((-center_db - 26.0) / 18.0, 0.0, 1.0)
            # When spectral/Essentia assist is unavailable the macro novelty
            # fills the gap, providing a wider-timescale energy-change signal
            # that helps distinguish real track transitions from within-track
            # drops.  With spectral assist, the timbral/BPM change signal is
            # the strongest indicator of a real transition.
            # Spectral/Essentia support is ADDITIVE — it can only help, never hurt.
            score = (
                (novelty * 0.48)
                + (macro_novelty * 0.40)
                + (quiet_bonus * 0.12)
                + (essentia_support * 0.25)
            )
            samples.append({
                "point_sec": round(point_sec, 3),
                "score": score,
            })
        point_sec += sample_step

    if len(samples) < 5:
        return []

    raw_scores = [float(item.get("score", 0.0)) for item in samples]
    peak_score = max(raw_scores) if raw_scores else 0.0
    if peak_score <= 0.0:
        return []

    ordered_scores = sorted(raw_scores)
    mean_score = sum(raw_scores) / float(len(raw_scores))
    q72_index = max(0, min(len(ordered_scores) - 1, int(len(ordered_scores) * 0.72)))
    q60_index = max(0, min(len(ordered_scores) - 1, int(len(ordered_scores) * 0.60)))
    q48_index = max(0, min(len(ordered_scores) - 1, int(len(ordered_scores) * 0.48)))

    def _extract_peaks(threshold):
        peaks = []
        # Scale local_radius so a peak must be the strongest within a
        # neighbourhood proportional to the expected segment length.
        # At sample_step=0.5, radius=2 covers ±1s.  For min_segment=15
        # we want ±~2s (radius=4); for min_segment=30, ±~3s (radius=6).
        # Capped at 6 to avoid over-suppression in very long segment modes.
        # This prevents beat-level energy spikes from registering as
        # transition candidates in dense/continuous mixes.
        local_radius = max(2, min(6, int(round(min_segment_seconds / (sample_step * 8.0)))))
        for idx in range(local_radius, len(samples) - local_radius):
            current = float(samples[idx].get("score", 0.0))
            if current < float(threshold):
                continue
            neighborhood = samples[idx - local_radius:idx + local_radius + 1]
            if not neighborhood:
                continue
            if current + 1e-6 < max(float(item.get("score", 0.0)) for item in neighborhood):
                continue
            confidence = _clamp(current / max(0.35, peak_score), 0.0, 1.0)
            peaks.append({
                "point_sec": float(samples[idx].get("point_sec", 0.0)),
                "confidence": round(confidence, 3),
            })
        return peaks

    minimum_useful_peaks = int(max(4, min(14, math.ceil(total_seconds / 300.0))))
    thresholds = [
        max(0.18, ordered_scores[q72_index] * 1.08, mean_score * 1.18),
        max(0.14, ordered_scores[q60_index] * 1.02, mean_score * 1.06),
        max(0.1, ordered_scores[q48_index] * 0.96, mean_score * 0.96),
    ]
    peaks = []
    for threshold in thresholds:
        peaks = _extract_peaks(threshold)
        if len(peaks) >= minimum_useful_peaks:
            break

    max_candidates = int(max(8, min(320, math.ceil(total_seconds / max(8.0, min_segment_seconds * 0.55)) * 4)))
    if len(peaks) > max_candidates:
        peaks = sorted(peaks, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)[:max_candidates]
        peaks = sorted(peaks, key=lambda item: float(item.get("point_sec", 0.0)))
    return peaks


def _combine_transition_points(*named_point_sets, merge_radius_seconds=1.1):
    candidates = []
    for source_name, points, source_weight in named_point_sets:
        source = str(source_name or "").strip().lower() or "transition"
        weight = max(0.25, float(source_weight))
        for item in list(points or []):
            try:
                point_sec = float(item.get("point_sec", 0.0))
            except Exception:
                continue
            if point_sec <= 0.0:
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except Exception:
                confidence = 0.0
            confidence = _clamp(confidence, 0.0, 1.0)
            score_weight = weight * max(0.2, confidence)
            candidates.append({
                "point_sec": round(point_sec, 3),
                "confidence": confidence,
                "source": source,
                "score_weight": score_weight,
            })

    if not candidates:
        return []

    candidates.sort(key=lambda item: float(item.get("point_sec", 0.0)))
    merge_radius = max(0.15, float(merge_radius_seconds))
    clusters = []

    for candidate in candidates:
        point_sec = float(candidate.get("point_sec", 0.0))
        if not clusters:
            clusters.append({
                "point_sec": point_sec,
                "weight_sum": float(candidate.get("score_weight", 0.0)),
                "max_confidence": float(candidate.get("confidence", 0.0)),
                "support_count": 1,
                "sources": {str(candidate.get("source", "transition"))},
            })
            continue

        cluster = clusters[-1]
        cluster_point = float(cluster.get("point_sec", 0.0))
        if abs(point_sec - cluster_point) > merge_radius:
            clusters.append({
                "point_sec": point_sec,
                "weight_sum": float(candidate.get("score_weight", 0.0)),
                "max_confidence": float(candidate.get("confidence", 0.0)),
                "support_count": 1,
                "sources": {str(candidate.get("source", "transition"))},
            })
            continue

        existing_weight = float(cluster.get("weight_sum", 0.0))
        incoming_weight = float(candidate.get("score_weight", 0.0))
        total_weight = max(1e-6, existing_weight + incoming_weight)
        cluster["point_sec"] = round(
            ((cluster_point * existing_weight) + (point_sec * incoming_weight)) / total_weight,
            3,
        )
        cluster["weight_sum"] = total_weight
        cluster["max_confidence"] = max(
            float(cluster.get("max_confidence", 0.0)),
            float(candidate.get("confidence", 0.0)),
        )
        cluster["support_count"] = int(cluster.get("support_count", 1)) + 1
        cluster["sources"].add(str(candidate.get("source", "transition")))

    combined = []
    for cluster in clusters:
        source_bonus = 0.12 * max(0, len(cluster.get("sources") or []) - 1)
        support_bonus = 0.04 * max(0, int(cluster.get("support_count", 1)) - 1)
        confidence = _clamp(
            float(cluster.get("max_confidence", 0.0)) + source_bonus + support_bonus,
            0.0,
            1.0,
        )
        combined.append({
            "point_sec": round(float(cluster.get("point_sec", 0.0)), 3),
            "confidence": round(confidence, 3),
            "sources": sorted(cluster.get("sources") or []),
        })
    return combined






def _scan_settings(config, duration_seconds):
    window_seconds = _safe_int(
        (config or {}).get("auto_tracklist_window_seconds", 18),
        default=18,
        minimum=8,
        maximum=60,
    )
    step_seconds = _safe_int(
        (config or {}).get("auto_tracklist_step_seconds", 12),
        default=12,
        minimum=5,
        maximum=120,
    )
    min_segment_seconds = _safe_int(
        (config or {}).get("auto_tracklist_min_segment_seconds", 30),
        default=30,
        minimum=15,
        maximum=300,
    )
    fallback_interval_seconds = _safe_int(
        (config or {}).get("auto_tracklist_fallback_interval_seconds", 180),
        default=180,
        minimum=60,
        maximum=900,
    )
    max_windows = _safe_int(
        (config or {}).get("auto_tracklist_max_windows", 120),
        default=120,
        minimum=20,
        maximum=1000,
    )
    min_confidence = _safe_float(
        (config or {}).get("auto_tracklist_min_confidence", 0.58),
        default=0.58,
        minimum=0.25,
        maximum=0.95,
    )
    boundary_backtrack_seconds = _safe_float(
        (config or {}).get("auto_tracklist_boundary_backtrack_seconds", 0.0),
        default=0.0,
        minimum=0.0,
        maximum=20.0,
    )
    no_identify = bool((config or {}).get("auto_tracklist_no_identify", False))
    short_circuit_confidence = _safe_float(
        (config or {}).get("auto_tracklist_short_circuit_confidence", _AUTO_SHORT_CIRCUIT_CONFIDENCE),
        default=_AUTO_SHORT_CIRCUIT_CONFIDENCE,
        minimum=0.55,
        maximum=0.99,
    )
    short_circuit_confidence = max(short_circuit_confidence, min_confidence)
    silence_first = bool((config or {}).get("auto_tracklist_silence_first", False))
    silence_min_ms = _safe_int(
        (config or {}).get("auto_tracklist_silence_min_ms", _AUTO_SILENCE_MIN_MS),
        default=_AUTO_SILENCE_MIN_MS,
        minimum=300,
        maximum=5000,
    )
    silence_thresh_db = _safe_int(
        (config or {}).get("auto_tracklist_silence_thresh_db", _AUTO_SILENCE_THRESH_DB),
        default=_AUTO_SILENCE_THRESH_DB,
        minimum=-70,
        maximum=-20,
    )
    silence_max_segment_seconds = _safe_int(
        (config or {}).get("auto_tracklist_silence_max_segment_seconds", _AUTO_SILENCE_MAX_SEGMENT_SECONDS),
        default=_AUTO_SILENCE_MAX_SEGMENT_SECONDS,
        minimum=60,
        maximum=900,
    )
    silence_first_min_anchors = _safe_int(
        (config or {}).get("auto_tracklist_silence_first_min_anchors", _AUTO_SILENCE_FIRST_MIN_ANCHORS),
        default=_AUTO_SILENCE_FIRST_MIN_ANCHORS,
        minimum=2,
        maximum=40,
    )
    shazam_timeout_seconds = _safe_int(
        (config or {}).get("auto_tracklist_shazam_timeout_seconds", _AUTO_SHAZAM_TIMEOUT_SECONDS),
        default=_AUTO_SHAZAM_TIMEOUT_SECONDS,
        minimum=5,
        maximum=45,
    )
    shazam_max_windows = _safe_int(
        (config or {}).get("auto_tracklist_shazam_max_windows", _AUTO_SHAZAM_MAX_WINDOWS),
        default=_AUTO_SHAZAM_MAX_WINDOWS,
        minimum=20,
        maximum=500,
    )
    persistence_windows = _safe_int(
        (config or {}).get("auto_tracklist_persistence_windows", _AUTO_PERSISTENCE_MIN_WINDOWS),
        default=_AUTO_PERSISTENCE_MIN_WINDOWS,
        minimum=1,
        maximum=5,
    )
    micro_refine_enabled = bool((config or {}).get("auto_tracklist_micro_refine_enabled", True))
    micro_refine_backtrack_seconds = _safe_float(
        (config or {}).get("auto_tracklist_micro_refine_backtrack_seconds", _AUTO_MICRO_REFINE_BACKTRACK_SECONDS),
        default=_AUTO_MICRO_REFINE_BACKTRACK_SECONDS,
        minimum=2.0,
        maximum=20.0,
    )
    micro_refine_forward_seconds = _safe_float(
        (config or {}).get("auto_tracklist_micro_refine_forward_seconds", _AUTO_MICRO_REFINE_FORWARD_SECONDS),
        default=_AUTO_MICRO_REFINE_FORWARD_SECONDS,
        minimum=0.0,
        maximum=8.0,
    )
    micro_refine_step_seconds = _safe_float(
        (config or {}).get("auto_tracklist_micro_refine_step_seconds", _AUTO_MICRO_REFINE_STEP_SECONDS),
        default=_AUTO_MICRO_REFINE_STEP_SECONDS,
        minimum=0.25,
        maximum=2.0,
    )
    micro_refine_min_confidence = _safe_float(
        (config or {}).get("auto_tracklist_micro_refine_min_confidence", _AUTO_MICRO_REFINE_MIN_CONFIDENCE),
        default=_AUTO_MICRO_REFINE_MIN_CONFIDENCE,
        minimum=0.2,
        maximum=0.95,
    )
    essentia_unified_pipeline = bool((config or {}).get("essentia_unified_pipeline", True))
    essentia_enabled = bool((config or {}).get("auto_tracklist_essentia_enabled", _AUTO_ESSENTIA_ENABLED))
    essentia_min_confidence = _safe_float(
        (config or {}).get("auto_tracklist_essentia_min_confidence", _AUTO_ESSENTIA_MIN_CONFIDENCE),
        default=_AUTO_ESSENTIA_MIN_CONFIDENCE,
        minimum=0.05,
        maximum=0.95,
    )
    essentia_max_points = _safe_int(
        (config or {}).get("auto_tracklist_essentia_max_points", _AUTO_ESSENTIA_MAX_POINTS),
        default=_AUTO_ESSENTIA_MAX_POINTS,
        minimum=200,
        maximum=6000,
    )

    estimated_windows = int(math.ceil(max(duration_seconds, 1.0) / float(step_seconds)))
    if estimated_windows > max_windows:
        step_seconds = int(math.ceil(max(duration_seconds, 1.0) / float(max_windows)))
        step_seconds = max(5, step_seconds)
        estimated_windows = int(math.ceil(max(duration_seconds, 1.0) / float(step_seconds)))

    return {
        "window_seconds": window_seconds,
        "step_seconds": step_seconds,
        "min_segment_seconds": min_segment_seconds,
        "fallback_interval_seconds": fallback_interval_seconds,
        "max_windows": max_windows,
        "min_confidence": min_confidence,
        "boundary_backtrack_seconds": boundary_backtrack_seconds,
        "no_identify": no_identify,
        "short_circuit_confidence": short_circuit_confidence,
        "silence_first": silence_first,
        "silence_min_ms": silence_min_ms,
        "silence_thresh_db": silence_thresh_db,
        "silence_max_segment_seconds": silence_max_segment_seconds,
        "silence_first_min_anchors": silence_first_min_anchors,
        "shazam_timeout_seconds": shazam_timeout_seconds,
        "shazam_max_windows": shazam_max_windows,
        "persistence_windows": persistence_windows,
        "micro_refine_enabled": micro_refine_enabled,
        "micro_refine_backtrack_seconds": micro_refine_backtrack_seconds,
        "micro_refine_forward_seconds": micro_refine_forward_seconds,
        "micro_refine_step_seconds": micro_refine_step_seconds,
        "micro_refine_min_confidence": micro_refine_min_confidence,
        "essentia_unified_pipeline": essentia_unified_pipeline,
        "essentia_enabled": essentia_enabled,
        "essentia_min_confidence": essentia_min_confidence,
        "essentia_max_points": essentia_max_points,
        "estimated_windows": estimated_windows,
    }




# ACRCloud rate-limit flag — set when status 3003 is returned so
# subsequent calls in the same session are skipped immediately.
_acrcloud_rate_limited = False


def _identify_with_acrcloud(window_chunk, recognizer):
    global _acrcloud_rate_limited
    if recognizer is None:
        print("    [ACRCloud] skipped — no recognizer object")
        return None
    if _acrcloud_rate_limited:
        return None

    temp_path = None
    try:
        temp_dir = _get_runtime_temp_directory()
        try:
            os.makedirs(temp_dir, exist_ok=True)
        except Exception:
            pass
        fd, temp_path = tempfile.mkstemp(
            prefix="mixsplitr_auto_acr_",
            suffix=".wav",
            dir=temp_dir,
        )
        os.close(fd)
        window_chunk.export(temp_path, format="wav")
        raw = recognizer.recognize_by_file(temp_path, 0)
        parsed = json.loads(raw or "{}")
        status_code = parsed.get("status", {}).get("code")
        status_msg = parsed.get("status", {}).get("msg", "")
        if status_code != 0:
            if status_code == 3003:
                _acrcloud_rate_limited = True
                print(f"    [ACRCloud] RATE LIMITED — auto-disabled for this session ({status_msg})")
            else:
                print(f"    [ACRCloud] no match — status_code={status_code} msg={status_msg}")
            return None
        music_items = (parsed.get("metadata") or {}).get("music") or []
        if not music_items:
            print(f"    [ACRCloud] no music items in response")
            return None
        music = music_items[0]
        artist = ((music.get("artists") or [{}])[0]).get("name", "").strip()
        title = str(music.get("title", "")).strip()
        if not artist or not title:
            print(f"    [ACRCloud] empty artist/title — artist={artist!r} title={title!r}")
            return None
        confidence = _to_confidence(parsed.get("status", {}).get("score"), 0.88)
        print(f"    [ACRCloud] HIT: {artist} — {title} (confidence={confidence:.2f})")
        return {
            "source": "acrcloud",
            "artist": artist,
            "title": title,
            "confidence": confidence,
        }
    except Exception as exc:
        print(f"    [ACRCloud] exception: {exc}")
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _identify_with_shazam(window_chunk, shazam_enabled, runtime_config=None):
    if not shazam_enabled:
        print(f"    [Shazam] skipped — shazam_enabled=False")
        return None
    if not is_shazam_available():
        print(f"    [Shazam] skipped — shazamio not available")
        return None
    try:
        result = identify_with_shazam(window_chunk, runtime_config=runtime_config)
    except Exception as exc:
        print(f"    [Shazam] exception: {exc}")
        return None
    if not result:
        print(f"    [Shazam] no result returned")
        return None
    artist = str(result.get("artist", "")).strip()
    title = str(result.get("title", "")).strip()
    if not artist or not title:
        print(f"    [Shazam] empty artist/title — artist={artist!r} title={title!r}")
        return None
    print(f"    [Shazam] HIT: {artist} — {title}")
    return {
        "source": "shazam",
        "artist": artist,
        "title": title,
        # Shazam is a definitive audio fingerprint match.  Set to the
        # same level as ACRCloud (0.88) so neither source has an
        # artificial confidence advantage — the SOURCE_PRIORITY tiebreak
        # decides when both return a hit for the same segment.
        "confidence": 0.88,
    }


def _identify_with_acoustid_backend(window_chunk):
    try:
        result = identify_with_acoustid(window_chunk)
    except Exception as exc:
        print(f"    [AcoustID] exception: {exc}")
        return None
    if not result:
        print(f"    [AcoustID] no result returned")
        return None
    artist = str(result.get("artist", "")).strip()
    title = str(result.get("title", "")).strip()
    if not artist or not title:
        print(f"    [AcoustID] empty artist/title — artist={artist!r} title={title!r} raw={result}")
        return None
    confidence = _to_confidence(result.get("score"), 0.66)
    print(f"    [AcoustID] HIT: {artist} — {title} (confidence={confidence:.2f})")
    return {
        "source": "acoustid",
        "artist": artist,
        "title": title,
        "confidence": confidence,
    }


def _auto_tracklist_source_policy(runtime_config=None, recognizer=None):
    # When no_identify is True, disable all identifier services so the
    # pipeline routes directly to the audio-only path.  The audio-only
    # fallback is also used when the ID pipeline identifies 0 tracks.
    if bool((runtime_config or {}).get("auto_tracklist_no_identify", False)):
        return {
            "identifier_mode": "no_identify",
            "allow_acrcloud": False,
            "allow_shazam": False,
            "allow_acoustid": False,
        }

    identifier_mode = str((runtime_config or {}).get("auto_tracklist_identifier_mode", "") or "").strip().lower()
    allow_acrcloud = recognizer is not None
    allow_shazam = True
    allow_acoustid = True

    if identifier_mode == "musicbrainz_only":
        allow_acrcloud = False
        allow_acoustid = True
    elif identifier_mode == "acrcloud":
        allow_acoustid = False
    elif identifier_mode == "dual_best_match":
        allow_acoustid = True

    return {
        "identifier_mode": identifier_mode,
        "allow_acrcloud": bool(allow_acrcloud),
        "allow_shazam": bool(allow_shazam),
        "allow_acoustid": bool(allow_acoustid),
    }


def _identify_segments(
    audio,
    segments,
    recognizer,
    shazam_enabled,
    settings,
    runtime_config=None,
    allow_acrcloud=True,
    allow_shazam=True,
    allow_acoustid=True,
    progress_callback=None,
    cancel_callback=None,
):
    """Identify each segment by probing a representative chunk from its center.

    For each segment the function extracts a probe window (up to 25 seconds)
    from the middle of the segment — away from transitions where audio is
    blended — and sends it through the identifier cascade.  The result is
    written back into the segment dict (artist, title, match_key, etc.).

    Returns the list of segments (mutated in-place) and the count of
    segments that were successfully identified.
    """
    if not segments:
        return segments, 0

    _probe_max_ms = 25000  # 25-second probe window
    _shazam_consec_fails = 0
    # For segment identification, the fail limit should be proportional
    # to the segment count.  With ~34 segments, a limit of 3 means Shazam
    # gets disabled after just 9% of segments.  Use at least 1/3 of total
    # segments or the configured limit, whichever is larger.
    _base_limit = int(settings.get(
        "shazam_consecutive_fail_limit",
        _AUTO_SHAZAM_CONSECUTIVE_FAIL_LIMIT,
    ))
    _shazam_fail_limit = max(_base_limit, max(6, len(segments) // 3))
    _shazam_disabled = False
    identified_count = 0
    total_segs = max(1, len(segments))

    for idx, seg in enumerate(segments):
        if cancel_callback is not None:
            cancel_callback()

        start_ms = int(round(float(seg.get("start_sec", 0)) * 1000))
        end_ms = int(round(float(seg.get("end_sec", 0)) * 1000))
        seg_dur_ms = max(0, end_ms - start_ms)
        if seg_dur_ms < 5000:
            # Segment too short to probe meaningfully.
            continue

        # Extract a center-weighted probe chunk (away from transitions).
        probe_dur = min(_probe_max_ms, seg_dur_ms)
        probe_start = start_ms + max(0, (seg_dur_ms - probe_dur) // 2)
        probe_end = min(end_ms, probe_start + probe_dur)
        probe_chunk = audio[probe_start:probe_end]

        _eff_shazam = allow_shazam and not _shazam_disabled

        candidate, attempted = _pick_window_candidate(
            probe_chunk,
            recognizer=recognizer,
            shazam_enabled=shazam_enabled,
            min_confidence=float(settings.get("min_confidence", 0.58)),
            short_circuit_confidence=float(settings.get("short_circuit_confidence", 0.90)),
            runtime_config=runtime_config,
            allow_acrcloud=allow_acrcloud,
            allow_shazam=_eff_shazam,
            allow_acoustid=allow_acoustid,
        )

        # Track Shazam failures (expensive timeouts).
        if _eff_shazam and "shazam" in attempted:
            if candidate and candidate.get("source") == "shazam":
                _shazam_consec_fails = 0
            else:
                _shazam_consec_fails += 1
                if _shazam_consec_fails >= _shazam_fail_limit:
                    _shazam_disabled = True
                    print(
                        f"[Shazam] Auto-disabled after {_shazam_consec_fails} consecutive "
                        f"failures during segment identification"
                    )

        if candidate:
            artist = str(candidate.get("artist", "")).strip()
            title = str(candidate.get("title", "")).strip()
            seg["artist"] = artist
            seg["title"] = title
            seg["confidence"] = float(candidate.get("confidence", 0.0))
            seg["source"] = candidate.get("source", "unknown")
            seg["match_key"] = _match_key(artist, title)
            seg["canonical_key"] = _canonical_match_key(artist, title)
            seg["identified"] = True
            identified_count += 1
            print(
                f"  [Identify] Segment {idx + 1}/{total_segs} "
                f"({seg.get('start_sec', 0):.0f}s-{seg.get('end_sec', 0):.0f}s): "
                f"{artist} — {title} [{candidate.get('source')}]"
            )
        else:
            seg.setdefault("match_key", _UNKNOWN_KEY)
            seg.setdefault("canonical_key", _UNKNOWN_KEY)
            seg.setdefault("identified", False)
            print(
                f"  [Identify] Segment {idx + 1}/{total_segs} "
                f"({seg.get('start_sec', 0):.0f}s-{seg.get('end_sec', 0):.0f}s): "
                f"NO MATCH  attempted={attempted}  probe={probe_dur}ms"
            )

        if progress_callback is not None:
            try:
                progress_callback(
                    idx + 1,
                    total_segs,
                    f"Identifying segment {idx + 1}/{total_segs}",
                )
            except Exception:
                pass

    settings["segment_identification_count"] = identified_count
    settings["segment_identification_total"] = total_segs
    if _shazam_disabled:
        settings["shazam_auto_disabled_by_failures"] = True
    return segments, identified_count


def _merge_same_track_segments(segments):
    """Merge consecutive segments that were identified as the same track.

    When the audio-only boundary detector overcutts (e.g. a buildup→drop
    within the same track), identification can fix it: if two adjacent
    segments are the same song, the boundary between them is a false
    positive and should be removed.

    Unidentified segments are never merged — only segments where BOTH
    neighbors have a known, matching track identity.

    Returns (merged_segments, merge_count).
    """
    if len(segments) < 2:
        return list(segments), 0

    merged = [dict(segments[0])]
    merge_count = 0

    for seg in segments[1:]:
        prev = merged[-1]
        prev_key = prev.get("match_key", _UNKNOWN_KEY)
        curr_key = seg.get("match_key", _UNKNOWN_KEY)

        # Only merge when both are identified AND match.
        same_track = (
            prev_key != _UNKNOWN_KEY
            and curr_key != _UNKNOWN_KEY
            and prev_key == curr_key
        )
        if same_track:
            # Extend the previous segment to cover the current one.
            prev["end_sec"] = float(seg.get("end_sec", prev.get("end_sec", 0)))
            # Keep the higher confidence.
            if float(seg.get("confidence", 0)) > float(prev.get("confidence", 0)):
                prev["confidence"] = float(seg.get("confidence", 0))
                prev["source"] = seg.get("source", prev.get("source"))
            merge_count += 1
            print(
                f"  [Merge] Combined segments at {seg.get('start_sec', 0):.0f}s "
                f"(same track: {prev.get('artist', '')} — {prev.get('title', '')})"
            )
        else:
            merged.append(dict(seg))

    return merged, merge_count


def _pick_window_candidate(
    window_chunk,
    recognizer,
    shazam_enabled,
    min_confidence,
    short_circuit_confidence,
    runtime_config=None,
    allow_acrcloud=True,
    allow_shazam=True,
    allow_acoustid=True,
):
    candidates = []
    attempted = []

    def _best_candidate():
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda item: (item.get("confidence", 0.0), _SOURCE_PRIORITY.get(item.get("source"), 0)),
            reverse=True,
        )[0]

    def _can_stop(candidate):
        if not candidate:
            return False
        confidence = float(candidate.get("confidence", 0.0))
        return confidence >= float(short_circuit_confidence)

    if not allow_acrcloud and not (allow_shazam and shazam_enabled) and not allow_acoustid:
        print(f"    [Candidate] ALL backends disabled — acrcloud={allow_acrcloud} shazam={allow_shazam and shazam_enabled} acoustid={allow_acoustid}")
        return None, attempted

    if allow_acrcloud:
        attempted.append("acrcloud")
        acr_hit = _identify_with_acrcloud(window_chunk, recognizer)
        if acr_hit:
            candidates.append(acr_hit)
            if _can_stop(acr_hit):
                if float(acr_hit.get("confidence", 0.0)) < float(min_confidence):
                    return None, attempted
                return acr_hit, attempted

    if allow_shazam and shazam_enabled:
        attempted.append("shazam")
        shazam_hit = _identify_with_shazam(window_chunk, shazam_enabled, runtime_config=runtime_config)
        if shazam_hit:
            candidates.append(shazam_hit)
            best_pre_acoustid = _best_candidate()
            if _can_stop(best_pre_acoustid):
                if float(best_pre_acoustid.get("confidence", 0.0)) < float(min_confidence):
                    return None, attempted
                return best_pre_acoustid, attempted

    if allow_acoustid:
        attempted.append("acoustid")
        acoustid_hit = _identify_with_acoustid_backend(window_chunk)
        if acoustid_hit:
            candidates.append(acoustid_hit)

    if not candidates:
        return None, attempted

    best = _best_candidate()

    if float(best.get("confidence", 0.0)) < float(min_confidence):
        print(f"    [Candidate] best below min_confidence: {best.get('confidence', 0):.2f} < {min_confidence:.2f} ({best.get('source')}: {best.get('artist')} — {best.get('title')})")
        return None, attempted
    return best, attempted






def _select_probe_chunk_for_range(range_chunk, window_ms):
    if range_chunk is None:
        return range_chunk

    total_ms = len(range_chunk)
    if total_ms <= int(window_ms):
        return range_chunk

    probe_len = int(max(1000, window_ms))
    max_start = max(0, total_ms - probe_len)
    candidate_starts = [
        int(max_start * 0.25),
        int(max_start * 0.50),
        int(max_start * 0.75),
    ]
    best_start = 0
    best_db = None
    for start_ms in candidate_starts:
        end_ms = min(total_ms, start_ms + probe_len)
        start_ms = max(0, end_ms - probe_len)
        sample = range_chunk[start_ms:end_ms]
        sample_db = _dbfs_or_floor(sample)
        if best_db is None or sample_db > best_db:
            best_db = sample_db
            best_start = start_ms

    best_end = min(total_ms, best_start + probe_len)
    best_start = max(0, best_end - probe_len)
    return range_chunk[best_start:best_end]






def _merge_short_segments(segments, minimum_duration):
    if len(segments) < 3:
        return segments

    # ── first-segment check ──────────────────────────────────────

    # The main loop starts at i=1, so the first segment is never
    # considered for shortness.  A short opening segment typically
    # means the detector split a track's buildup from its drop.
    # Absorb it rightward so the full intro is preserved.
    # Uses a higher threshold (35s) than the main loop since short
    # intros are almost always within-track buildup→drop splits.
    first = segments[0]
    first_dur = float(first["end_sec"]) - float(first["start_sec"])
    if first_dur < 35.0 and len(segments) >= 2:
        segments[1]["start_sec"] = first["start_sec"]
        for source in first.get("sources_tried", []):
            if source not in segments[1].get("sources_tried", []):
                segments[1].setdefault("sources_tried", []).append(source)
        del segments[0]
    # ─────────────────────────────────────────────────────────────

    i = 1
    while i < len(segments) - 1:
        segment = segments[i]
        prev_seg = segments[i - 1]
        next_seg = segments[i + 1]
        seg_duration = float(segment["end_sec"]) - float(segment["start_sec"])

        if seg_duration >= float(minimum_duration):
            i += 1
            continue

        if _keys_are_mergeable(
            prev_seg.get("match_key"),
            prev_seg.get("canonical_key"),
            next_seg.get("match_key"),
            next_seg.get("canonical_key"),
        ):
            prev_seg["end_sec"] = next_seg["end_sec"]
            prev_seg["window_count"] += segment["window_count"] + next_seg["window_count"]
            if float(next_seg["confidence"]) > float(prev_seg["confidence"]):
                prev_seg["artist"] = next_seg["artist"]
                prev_seg["title"] = next_seg["title"]
                prev_seg["confidence"] = float(next_seg["confidence"])
                prev_seg["source"] = next_seg["source"]
                prev_seg["match_key"] = next_seg.get("match_key", prev_seg.get("match_key"))
                prev_seg["canonical_key"] = next_seg.get("canonical_key", prev_seg.get("canonical_key"))
            for source in segment.get("sources_tried", []) + next_seg.get("sources_tried", []):
                if source not in prev_seg["sources_tried"]:
                    prev_seg["sources_tried"].append(source)
            del segments[i:i + 2]
            continue

        segment_match_key = segment.get("match_key", _UNKNOWN_KEY)
        segment_canonical_key = segment.get("canonical_key", _UNKNOWN_KEY)
        if segment_match_key == _UNKNOWN_KEY and segment_canonical_key == _UNKNOWN_KEY:
            if float(prev_seg["confidence"]) >= float(next_seg["confidence"]):
                prev_seg["end_sec"] = segment["end_sec"]
                next_seg["start_sec"] = segment["end_sec"]
                for source in segment.get("sources_tried", []):
                    if source not in prev_seg["sources_tried"]:
                        prev_seg["sources_tried"].append(source)
            else:
                next_seg["start_sec"] = segment["start_sec"]
                for source in segment.get("sources_tried", []):
                    if source not in next_seg["sources_tried"]:
                        next_seg["sources_tried"].append(source)
            del segments[i]
            continue

        i += 1

    return segments




def _refine_audio_only_transition_boundaries(audio, segments, settings, essentia_points=None):
    """
    Audio-only boundary refinement for timestamp-no-ID mode.

    The no-ID flow does not have recognition windows to anchor boundaries, so it
    needs a direct local search around each provisional transition point.
    """
    if len(segments) < 2 or audio is None:
        return [dict(segment) for segment in segments], 0

    refined = [dict(segment) for segment in segments]
    refined_count = 0
    min_segment_seconds = float(settings.get("min_segment_seconds", 30.0))
    configured_backtrack = float(settings.get("boundary_backtrack_seconds", 0.0))
    backtrack = max(configured_backtrack, min(16.0, max(6.0, min_segment_seconds * 0.45)))
    forward = min(6.0, max(1.5, min_segment_seconds * 0.12))
    sample_step = 0.25 if min_segment_seconds <= 60.0 else 0.5
    min_side_seconds = max(5.0, min(14.0, min_segment_seconds * 0.22))

    for idx in range(1, len(refined)):
        prev_seg = refined[idx - 1]
        curr_seg = refined[idx]
        base_boundary = float(curr_seg.get("start_sec", 0.0))
        prev_start = float(prev_seg.get("start_sec", 0.0))
        curr_end = float(curr_seg.get("end_sec", base_boundary))

        lower_bound = max(prev_start + min_side_seconds, base_boundary - backtrack)
        upper_bound = min(curr_end - min_side_seconds, base_boundary + forward)
        if upper_bound <= lower_bound:
            continue

        original_confidence = _clamp(
            float(curr_seg.get("boundary_confidence", curr_seg.get("confidence", 0.0))),
            0.0,
            1.0,
        )
        best_candidate = base_boundary
        best_score = None
        best_confidence = original_confidence

        candidate = lower_bound
        while candidate <= (upper_bound + 1e-9):
            if not _anchor_has_audible_sides(
                audio,
                candidate,
                side_offset_sec=0.55,
                min_side_dbfs=-60.0,
            ):
                candidate += sample_step
                continue

            left_db = _window_dbfs(audio, max(0.0, candidate - 0.35), half_window_ms=170)
            right_db = _window_dbfs(audio, candidate + 0.35, half_window_ms=170)
            center_db = _window_dbfs(audio, candidate, half_window_ms=170)
            energy_jump = abs(right_db - left_db)
            novelty = _boundary_novelty_score(audio, candidate)
            macro_novelty = _boundary_macro_novelty_score(audio, candidate)
            essentia_support = _essentia_proximity_support(candidate, essentia_points or [])
            quiet_norm = _clamp((-center_db - 28.0) / 18.0, 0.0, 1.0)
            jump_norm = _clamp(energy_jump / 8.0, 0.0, 1.0)
            novelty_norm = _clamp(novelty / 1.15, 0.0, 1.0)
            macro_novelty_norm = _clamp(macro_novelty / 1.15, 0.0, 1.0)
            essentia_norm = _clamp(essentia_support / 0.9, 0.0, 1.0)
            distance_norm = _clamp(abs(candidate - base_boundary) / max(1.0, backtrack + forward), 0.0, 1.0)
            earlier_bias = _clamp((base_boundary - candidate) / max(1.0, backtrack), 0.0, 1.0)

            # Spectral/Essentia support is ADDITIVE — it can only help, never hurt.
            confidence = _clamp(
                (novelty_norm * 0.38)
                + (macro_novelty_norm * 0.32)
                + (jump_norm * 0.18)
                + (quiet_norm * 0.12)
                + (essentia_norm * 0.28),
                0.0,
                1.0,
            )
            score = (
                (novelty * 1.05)
                + (macro_novelty * 0.95)
                + (energy_jump * 0.16)
                + (quiet_norm * 0.24)
                + ((1.0 - distance_norm) * 0.16)
                + (earlier_bias * 0.22)
                + (essentia_support * 0.65)
            )

            better = False
            if best_score is None:
                better = True
            elif score > (best_score + 1e-6):
                better = True
            elif abs(score - best_score) <= 1e-6:
                if confidence > (best_confidence + 1e-6):
                    better = True
                elif abs(confidence - best_confidence) <= 1e-6 and candidate < best_candidate:
                    better = True

            if better:
                best_candidate = candidate
                best_score = score
                best_confidence = confidence

            candidate += sample_step

        if abs(best_candidate - base_boundary) < 0.08 and best_confidence <= (original_confidence + 0.03):
            continue

        prev_seg["end_sec"] = float(best_candidate)
        curr_seg["start_sec"] = float(best_candidate)
        prev_seg["boundary_confidence_to_next"] = round(float(best_confidence), 3)
        curr_seg["boundary_confidence"] = round(float(best_confidence), 3)
        refined_count += 1

    return refined, refined_count


def _merge_weak_audio_only_boundaries(segments, settings, assist_points=None):
    if len(segments) < 3:
        return [dict(segment) for segment in segments], 0

    refined = [dict(segment) for segment in segments]
    merge_count = 0
    min_segment_seconds = float(settings.get("min_segment_seconds", 30.0))

    # Build a set of assist-point times for fast proximity lookup.
    # Boundaries within _ASSIST_PROTECT_RADIUS of an assist point are
    # protected from weak-merge — the spectral/Essentia detector
    # independently identified them as transitions.
    _ASSIST_PROTECT_RADIUS = 4.0  # seconds
    assist_secs = sorted(
        float(p.get("point_sec", 0.0)) for p in (assist_points or [])
    )

    def _has_assist_support(boundary_sec):
        """Return True if any assist point is within radius."""
        if not assist_secs:
            return False
        import bisect
        idx = bisect.bisect_left(assist_secs, boundary_sec)
        for j in (idx - 1, idx):
            if 0 <= j < len(assist_secs):
                if abs(assist_secs[j] - boundary_sec) <= _ASSIST_PROTECT_RADIUS:
                    return True
        return False

    boundary_values = [
        _clamp(float(segment.get("boundary_confidence", 0.0)), 0.0, 1.0)
        for segment in refined[1:]
    ]
    if boundary_values:
        ordered = sorted(boundary_values)
        floor_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.43)))
        # With improved upstream peak extraction (scaled local_radius) the
        # boundary confidences reaching this stage are higher quality, so the
        # absolute floor can be slightly lower.
        weak_floor = max(0.32, min(0.58, float(ordered[floor_index])))
    else:
        weak_floor = 0.34

    changed = True
    while changed and len(refined) >= 3:
        changed = False
        i = 1
        while i < len(refined):
            curr_seg = refined[i]
            prev_seg = refined[i - 1]
            boundary_conf = _clamp(
                float(curr_seg.get("boundary_confidence", curr_seg.get("confidence", 0.0))),
                0.0,
                1.0,
            )

            # ── Protect boundaries with spectral/Essentia support ──
            # Only protect if removing this boundary would create a very long
            # segment (> 85s).  Within-track drops are real spectral events but
            # removing them just merges two halves of the same track (~40-60s
            # combined), so they don't need protection.  Real track transitions
            # separate distinct tracks, so removing them creates mega-segments.
            boundary_sec = float(curr_seg.get("start_sec", 0.0))
            prev_duration_pre = float(prev_seg.get("end_sec", 0.0)) - float(prev_seg.get("start_sec", 0.0))
            curr_duration_pre = float(curr_seg.get("end_sec", 0.0)) - float(curr_seg.get("start_sec", 0.0))
            combined_if_removed = prev_duration_pre + curr_duration_pre
            if _has_assist_support(boundary_sec) and combined_if_removed > 85.0:
                i += 1
                continue

            prev_boundary_conf = None if i <= 1 else _clamp(
                float(refined[i - 1].get("boundary_confidence", 0.0)),
                0.0,
                1.0,
            )
            next_boundary_conf = None if i >= (len(refined) - 1) else _clamp(
                float(refined[i + 1].get("boundary_confidence", 0.0)),
                0.0,
                1.0,
            )
            prev_duration = float(prev_seg.get("end_sec", 0.0)) - float(prev_seg.get("start_sec", 0.0))
            curr_duration = float(curr_seg.get("end_sec", 0.0)) - float(curr_seg.get("start_sec", 0.0))
            local_valley = False
            if (prev_boundary_conf is not None) and (next_boundary_conf is not None):
                local_valley = boundary_conf + 0.06 < max(prev_boundary_conf, next_boundary_conf)
            elif (prev_boundary_conf is not None) or (next_boundary_conf is not None):
                # Edge case: first or last boundary has only one neighbor.
                # Allow valley detection using the single available neighbor.
                solo_neighbor = prev_boundary_conf if prev_boundary_conf is not None else next_boundary_conf
                local_valley = boundary_conf + 0.10 < solo_neighbor
            short_adjacent = min(prev_duration, curr_duration) < max(min_segment_seconds * 1.15, min_segment_seconds * 1.25)
            weak_boundary = boundary_conf < weak_floor
            if weak_boundary and (local_valley or short_adjacent):
                prev_seg["end_sec"] = float(curr_seg.get("end_sec", prev_seg.get("end_sec", 0.0)))
                if i + 1 < len(refined):
                    next_boundary = _clamp(float(refined[i + 1].get("boundary_confidence", 0.0)), 0.0, 1.0)
                    prev_seg["boundary_confidence_to_next"] = round(next_boundary, 3)
                else:
                    prev_seg["boundary_confidence_to_next"] = 0.0
                del refined[i]
                merge_count += 1
                changed = True
                continue
            i += 1

    return refined, merge_count


def _normalize_segment_boundaries(segments, duration_seconds):
    if not segments:
        return []

    normalized = []
    cursor = 0.0
    total = float(max(0.0, duration_seconds))
    for idx, segment in enumerate(segments):
        start = float(segment.get("start_sec", cursor))
        end = float(segment.get("end_sec", start))
        start = max(cursor, start)
        end = max(start + 1.0, end)
        if idx == len(segments) - 1:
            end = total
        elif end > total:
            end = total

        if end <= start:
            continue

        item = dict(segment)
        item["start_sec"] = start
        item["end_sec"] = end
        item["match_key"] = item.get("match_key") or _match_key(item.get("artist"), item.get("title"))
        item["canonical_key"] = item.get("canonical_key") or _canonical_match_key(item.get("artist"), item.get("title"))
        normalized.append(item)
        cursor = end
        if cursor >= total:
            break

    if not normalized:
        return [{
            "start_sec": 0.0,
            "end_sec": total,
            "artist": "",
            "title": "",
            "confidence": 0.0,
            "source": "fallback",
            "match_key": _UNKNOWN_KEY,
            "canonical_key": _UNKNOWN_KEY,
            "window_count": 1,
            "sources_tried": [],
        }]

    normalized[0]["start_sec"] = 0.0
    normalized[-1]["end_sec"] = total
    return normalized


def _fallback_interval_segments(duration_seconds, interval_seconds):
    total = float(max(0.0, duration_seconds))
    if total <= 0:
        return []

    interval = float(max(60, interval_seconds))
    segments = []
    start = 0.0
    while start < total:
        end = min(total, start + interval)
        segments.append({
            "start_sec": start,
            "end_sec": end,
            "artist": "",
            "title": "",
            "confidence": 0.0,
            "source": "fallback",
            "match_key": _UNKNOWN_KEY,
            "canonical_key": _UNKNOWN_KEY,
            "window_count": 1,
            "sources_tried": [],
        })
        start = end
    return segments


def _start_times_from_transition_points(points, duration_seconds, minimum_segment_seconds):
    total = float(max(0.0, duration_seconds))
    if total <= 0.0:
        return [0.0]

    try:
        min_gap = float(minimum_segment_seconds)
    except Exception:
        min_gap = 30.0
    min_gap = max(8.0, min(180.0, min_gap))

    candidates = []
    for item in list(points or []):
        try:
            point_sec = float(item.get("point_sec", 0.0))
        except Exception:
            continue
        if point_sec <= 0.0 or point_sec >= total:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        raw_sources = list(item.get("sources") or [])
        if not raw_sources:
            source_name = str(item.get("source", "") or "").strip()
            if source_name:
                raw_sources = [source_name]
        sources = sorted(
            {
                str(source).strip().lower()
                for source in raw_sources
                if str(source or "").strip()
            }
        )
        source_bonus = 0.0
        if len(sources) >= 2:
            source_bonus += 0.2
        if "essentia" in sources:
            source_bonus += 0.08
        if sources == ["novelty"]:
            source_bonus -= 0.04
        candidates.append({
            "point_sec": round(point_sec, 3),
            "confidence": confidence,
            "sources": sources,
            "score": _clamp(confidence + source_bonus, 0.0, 1.25),
        })

    if not candidates:
        return [0.0]

    # The selection_gap de-duplicates nearby detections of the *same* transition
    # by keeping only the best candidate within each gap-sized window.  Its job
    # is NOT segment-length enforcement (min_gap handles that when keeping points).
    #
    # A single DJ/production transition can produce multiple novelty peaks within
    # a ~5-12 second span.  The gap needs to cover that span so only the best
    # peak survives, but must NOT be so large that it swallows adjacent real
    # transitions in dense mixes.  The local_radius increase in _extract_peaks
    # now handles beat-level de-duplication, so selection_gap only needs to cover
    # transition-level de-duplication (~10-20s depending on duration).
    duration_scale = _clamp(total / 900.0, 0.0, 1.0)
    selection_gap = min_gap + (min(12.0, max(4.0, min_gap * 0.55)) * duration_scale)
    selection_gap = max(min_gap, min(40.0, selection_gap))

    def _is_better_candidate(candidate, incumbent):
        if incumbent is None:
            return True
        candidate_score = float(candidate.get("score", 0.0))
        incumbent_score = float(incumbent.get("score", 0.0))
        if candidate_score > (incumbent_score + 1e-6):
            return True
        if abs(candidate_score - incumbent_score) <= 1e-6:
            candidate_conf = float(candidate.get("confidence", 0.0))
            incumbent_conf = float(incumbent.get("confidence", 0.0))
            if candidate_conf > (incumbent_conf + 1e-6):
                return True
            if abs(candidate_conf - incumbent_conf) <= 1e-6:
                return float(candidate.get("point_sec", 0.0)) < float(incumbent.get("point_sec", 0.0))
        return False

    candidates.sort(key=lambda item: float(item.get("point_sec", 0.0)))
    selected = [0.0]
    last_kept = 0.0
    pending = None

    for candidate in candidates:
        point_sec = float(candidate.get("point_sec", 0.0))
        if pending is None:
            pending = candidate
            continue

        pending_point = float(pending.get("point_sec", 0.0))
        if (point_sec - pending_point) < selection_gap:
            if _is_better_candidate(candidate, pending):
                pending = candidate
            continue

        if (pending_point - last_kept) >= min_gap:
            kept_point = round(pending_point, 3)
            selected.append(kept_point)
            last_kept = kept_point
        pending = candidate

    if pending is not None:
        pending_point = float(pending.get("point_sec", 0.0))
        if (pending_point - last_kept) >= min_gap:
            selected.append(round(pending_point, 3))

    return _normalize_start_times(selected, total)


def _normalize_start_times(start_times, duration_seconds):
    """Normalize and clamp manual/edited start times."""
    total = float(max(0.0, duration_seconds))
    normalized = set()
    for raw in start_times or []:
        try:
            value = float(raw)
        except Exception:
            continue
        if value < 0:
            value = 0.0
        if value >= total:
            continue
        normalized.add(round(value, 3))

    ordered = sorted(normalized)
    if not ordered or ordered[0] != 0.0:
        ordered = [0.0] + ordered
    return ordered


def _template_for_start(start_sec, template_segments):
    if not template_segments:
        return None
    for segment in template_segments:
        seg_start = float(segment.get("start_sec", 0.0))
        seg_end = float(segment.get("end_sec", seg_start))
        if seg_start <= start_sec < seg_end:
            return segment
    # fallback to nearest previous segment by start
    ordered = sorted(template_segments, key=lambda seg: float(seg.get("start_sec", 0.0)))
    previous = None
    for segment in ordered:
        seg_start = float(segment.get("start_sec", 0.0))
        if seg_start <= start_sec:
            previous = segment
        else:
            break
    return previous or (ordered[0] if ordered else None)


def _segments_from_start_times(start_times, duration_seconds, template_segments=None):
    starts = _normalize_start_times(start_times, duration_seconds)
    total = float(max(0.0, duration_seconds))
    if not starts:
        starts = [0.0]

    raw_segments = []
    for idx, start in enumerate(starts):
        end = total if idx + 1 >= len(starts) else float(starts[idx + 1])
        if end <= start:
            continue
        template = _template_for_start(start, template_segments)
        if template and bool(template.get("identified")):
            artist = str(template.get("artist", "")).strip()
            title = str(template.get("title", "")).strip()
            confidence = float(template.get("confidence", 0.75))
            source = str(template.get("source", "manual_edit")).strip() or "manual_edit"
            sources_tried = list(template.get("sources_tried") or [])
            window_count = int(template.get("window_count", 1))
        else:
            artist = ""
            title = ""
            confidence = 0.0
            source = "manual"
            sources_tried = []
            window_count = 1

        raw_segments.append({
            "start_sec": float(start),
            "end_sec": float(end),
            "artist": artist,
            "title": title,
            "confidence": confidence,
            "source": source,
            "match_key": _match_key(artist, title),
            "canonical_key": _canonical_match_key(artist, title),
            "window_count": window_count,
            "sources_tried": sources_tried,
        })
    return raw_segments


def _segments_from_transition_candidates(points, duration_seconds):
    total = float(max(0.0, duration_seconds))
    ordered_points = []
    seen = set()
    for item in list(points or []):
        try:
            point_sec = round(float(item.get("point_sec", 0.0)), 3)
        except Exception:
            continue
        if point_sec <= 0.0 or point_sec >= total or point_sec in seen:
            continue
        seen.add(point_sec)
        try:
            confidence = float(item.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        ordered_points.append({
            "point_sec": point_sec,
            "confidence": _clamp(confidence, 0.0, 1.0),
            "sources": list(item.get("sources") or []),
        })

    ordered_points.sort(key=lambda item: float(item.get("point_sec", 0.0)))
    starts = [0.0] + [float(item.get("point_sec", 0.0)) for item in ordered_points]
    raw_segments = []
    for idx, start in enumerate(starts):
        end = total if idx + 1 >= len(starts) else float(starts[idx + 1])
        if end <= start:
            continue
        boundary_confidence = 0.0
        boundary_sources = []
        if idx > 0 and (idx - 1) < len(ordered_points):
            boundary_confidence = float(ordered_points[idx - 1].get("confidence", 0.0))
            boundary_sources = list(ordered_points[idx - 1].get("sources") or [])
        raw_segments.append({
            "start_sec": float(start),
            "end_sec": float(end),
            "artist": "",
            "title": "",
            "confidence": float(boundary_confidence),
            "source": "transition",
            "match_key": _UNKNOWN_KEY,
            "canonical_key": _UNKNOWN_KEY,
            "window_count": 1,
            "sources_tried": list(boundary_sources),
            "boundary_confidence": float(boundary_confidence),
            "boundary_sources": list(boundary_sources),
        })

    if not raw_segments:
        raw_segments = [{
            "start_sec": 0.0,
            "end_sec": total,
            "artist": "",
            "title": "",
            "confidence": 0.0,
            "source": "transition",
            "match_key": _UNKNOWN_KEY,
            "canonical_key": _UNKNOWN_KEY,
            "window_count": 1,
            "sources_tried": [],
            "boundary_confidence": 0.0,
            "boundary_sources": [],
        }]

    for idx in range(len(raw_segments) - 1):
        next_conf = float(raw_segments[idx + 1].get("boundary_confidence", 0.0))
        raw_segments[idx]["boundary_confidence_to_next"] = next_conf
    return raw_segments


def _finalize_segments(segments):
    final_segments = []
    for idx, segment in enumerate(segments, 1):
        artist = str(segment.get("artist") or "").strip()
        title = str(segment.get("title") or "").strip()
        identified = bool(artist and title and segment.get("match_key") != _UNKNOWN_KEY)
        fallback_title = f"Track {idx:02d}"
        label = f"{artist} - {title}" if identified else fallback_title
        if not identified:
            artist = ""
            title = fallback_title

        start_sec = float(segment.get("start_sec", 0.0))
        end_sec = float(segment.get("end_sec", start_sec))
        final_segments.append({
            "index": idx,
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "duration_sec": round(max(0.0, end_sec - start_sec), 3),
            "start_timestamp": _format_timestamp(start_sec),
            "end_timestamp": _format_timestamp(end_sec),
            "label": label,
            "artist": artist,
            "title": title,
            "identified": identified,
            "confidence": round(float(segment.get("confidence", 0.0)), 3),
            "source": segment.get("source", "unknown"),
            "sources_tried": list(segment.get("sources_tried") or []),
            "window_count": int(segment.get("window_count", 1)),
        })
    return final_segments


def _force_track_labels(final_segments):
    """Replace identified labels with Track NN while preserving timeline boundaries."""
    forced = []
    for idx, segment in enumerate(final_segments, 1):
        item = dict(segment)
        title = f"Track {idx:02d}"
        item["index"] = idx
        item["label"] = title
        item["artist"] = ""
        item["title"] = title
        item["identified"] = False
        item["confidence"] = 0.0
        item["source"] = "no_identify"
        forced.append(item)
    return forced


def _resolve_timestamp_output_directory(config, output_folder):
    configured = str((config or {}).get("timestamp_output_directory", "") or "").strip()
    if configured:
        return configured
    base_output = str(output_folder or "").strip()
    if base_output:
        return os.path.join(base_output, "Tracklists")
    return os.path.join(os.getcwd(), "Tracklists")


def _segments_to_manifest_tracks(segments, audio_file):
    source_name = os.path.splitext(os.path.basename(audio_file))[0] or "Unknown Source"
    tracks = []
    for segment in segments:
        identified = bool(segment.get("identified"))
        start_sec = _safe_seconds_value(segment.get("start_sec"), 0.0)
        end_sec = _safe_seconds_value(segment.get("end_sec"), start_sec)
        duration_sec = _safe_seconds_value(segment.get("duration_sec"), max(0.0, end_sec - start_sec))
        title = segment.get("title") or f"Track {int(segment.get('index', 0)):02d}"
        segment_source = str(segment.get("source") or "").strip().lower()
        no_identify_label = (segment_source == "no_identify")
        if identified:
            artist = str(segment.get("artist") or "").strip()
        elif no_identify_label:
            # No-ID mode should keep metadata artist blank while still allowing
            # source-name folder organization via enhanced export metadata.
            artist = ""
        else:
            artist = source_name
        status = "identified" if identified else "unidentified"
        source = segment.get("source") or ("auto_tracklist" if identified else "fallback")
        enhanced_metadata = {
            "timeline_start": segment.get("start_timestamp"),
            "timeline_end": segment.get("end_timestamp"),
            "timeline_duration_sec": segment.get("duration_sec"),
        }
        if no_identify_label:
            enhanced_metadata.update({
                "output_folder_name": source_name,
                "output_filename_base": title,
                "output_title_only_filename": True,
            })
        tracks.append({
            "status": status,
            "index": int(segment.get("index", 1)) - 1,
            "chunk_index": int(segment.get("index", 1)) - 1,
            "file_num": 1,
            "artist": artist,
            "title": title,
            "album": "Timestamping",
            "identification_source": source,
            "readable_metadata": {
                "artist": {"value": artist, "source": source},
                "title": {"value": title, "source": source},
                "album": {"value": "Timestamping", "source": "auto_tracklist"},
                "confidence": float(segment.get("confidence", 0.0)),
                "agreement": float(segment.get("confidence", 0.0)),
                "sources_used": [source],
            },
            "enhanced_metadata": enhanced_metadata,
            "backend_candidates": {
                "sources_tried": list(segment.get("sources_tried") or []),
            },
            "original_file": audio_file,
            "unidentified_filename": title,
            "identified": identified,
            "confidence": float(segment.get("confidence", 0.0) or 0.0),
            "timestamp_source": source,
            "timeline_label": str(segment.get("label") or title),
            "start_time": start_sec,
            "end_time": end_sec,
            "duration_sec": duration_sec,
            "start_timestamp": str(segment.get("start_timestamp") or _format_timestamp(start_sec)),
            "end_timestamp": str(segment.get("end_timestamp") or _format_timestamp(end_sec)),
        })
    return tracks


def _build_output_payload(
    audio_file,
    output_folder,
    final_segments,
    settings,
    dry_run,
    duration_seconds,
    write_output_files=True,
):
    identified = len([seg for seg in final_segments if seg.get("identified")])
    unknown = len(final_segments) - identified

    tracklist_dir = str(output_folder or "").strip()
    if not tracklist_dir:
        tracklist_dir = os.path.join(os.getcwd(), "Tracklists")
    stem = _slugify(os.path.splitext(os.path.basename(audio_file))[0])
    timestamp_path = os.path.join(tracklist_dir, f"{stem}_timestamps.txt")
    report_path = os.path.join(tracklist_dir, f"{stem}_auto_tracklist.json")

    timestamp_lines = [f"{seg['start_timestamp']} - {seg['label']}" for seg in final_segments]
    report_payload = {
        "source_file": audio_file,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "dry_run": dry_run,
        "scan_settings": settings,
        "summary": {
            "segments": len(final_segments),
            "identified": identified,
            "fallback_labeled": unknown,
        },
        "segments": final_segments,
    }
    output_files = []
    if write_output_files:
        os.makedirs(tracklist_dir, exist_ok=True)
        with open(timestamp_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(timestamp_lines).rstrip() + "\n")
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report_payload, handle, indent=2, ensure_ascii=False)
        output_files = [timestamp_path, report_path]

    return {
        "audio_file": audio_file,
        "summary": report_payload["summary"],
        "segments": final_segments,
        "timestamp_file": timestamp_path,
        "report_file": report_path,
        "manifest_tracks": _segments_to_manifest_tracks(final_segments, audio_file),
        "output_files": output_files,
        "scan_settings": settings,
        "output_directory": tracklist_dir,
        "duration_seconds": float(duration_seconds or 0.0),
        "dry_run": bool(dry_run),
    }


def generate_tracklist_from_start_times(
    audio_file,
    output_folder,
    start_times,
    config=None,
    recognizer=None,
    template_segments=None,
    dry_run=False,
    progress_callback=None,
    status_callback=None,
    cancel_callback=None,
    write_output_files=True,
):
    """Generate timestamp outputs from user/manual start times.

    When *recognizer* (and/or Shazam/AcoustID) is provided and
    ``no_identify`` is not set, segments are identified after boundary
    construction so the identification runs on the user's confirmed
    points rather than on auto-detected boundaries.
    """
    if not audio_file or not os.path.exists(audio_file):
        raise FileNotFoundError(f"Audio file not found: {audio_file}")

    def _emit_progress(fraction, detail=""):
        if cancel_callback is not None:
            cancel_callback()
        if progress_callback is not None:
            try:
                progress_callback(max(0.0, min(1.0, float(fraction))), str(detail or ""))
            except Exception:
                pass
        elif detail and status_callback is not None:
            try:
                status_callback(str(detail))
            except Exception:
                pass

    _emit_progress(0.08, "Loading audio for timestamping…")
    audio = AudioSegment.from_file(audio_file)
    duration_seconds = len(audio) / 1000.0
    settings = _scan_settings(config or {}, duration_seconds)
    no_identify = bool(settings.get("no_identify", False))

    _emit_progress(0.30, "Applying timestamp boundaries…")
    segments = _segments_from_start_times(
        start_times=start_times,
        duration_seconds=duration_seconds,
        template_segments=template_segments,
    )
    segments = _normalize_segment_boundaries(segments, duration_seconds)
    final_segments = _finalize_segments(segments)

    # ── Identify segments on the user-confirmed boundaries ────────
    if no_identify:
        _emit_progress(0.68, "Applying Track labels…")
        final_segments = _force_track_labels(final_segments)
    else:
        # Determine which identifier backends are available
        source_policy = _auto_tracklist_source_policy(config, recognizer=recognizer)
        shazam_enabled = not bool((config or {}).get("disable_shazam", False))
        effective_shazam_enabled = bool(
            shazam_enabled and source_policy.get("allow_shazam", True)
        )
        settings["effective_acrcloud_enabled"] = bool(source_policy.get("allow_acrcloud", False))
        settings["effective_shazam_enabled"] = bool(effective_shazam_enabled)
        settings["effective_acoustid_enabled"] = bool(source_policy.get("allow_acoustid", True))

        _any_identifier_available = bool(
            settings.get("effective_shazam_enabled")
            or settings.get("effective_acrcloud_enabled")
            or settings.get("effective_acoustid_enabled")
        )

        if _any_identifier_available:
            _pre_id_count = len(final_segments)
            _emit_progress(0.45, f"Identifying {_pre_id_count} segments…")
            scan_runtime_config = dict(config or {})
            scan_runtime_config["shazam_timeout_seconds"] = int(
                settings.get("shazam_timeout_seconds", _AUTO_SHAZAM_TIMEOUT_SECONDS)
            )
            print(f"[FROM_START_TIMES] Identifying {_pre_id_count} user-confirmed segments")
            print(f"[FROM_START_TIMES] Backends: acrcloud={source_policy.get('allow_acrcloud')}  shazam={effective_shazam_enabled}  acoustid={source_policy.get('allow_acoustid')}")

            def _id_progress(frac, detail=""):
                _emit_progress(0.45 + frac * 0.35, detail)

            final_segments, identified_count = _identify_segments(
                audio,
                final_segments,
                recognizer,
                effective_shazam_enabled,
                settings,
                runtime_config=scan_runtime_config,
                allow_acrcloud=bool(source_policy.get("allow_acrcloud", False)),
                allow_shazam=bool(source_policy.get("allow_shazam", True)),
                allow_acoustid=bool(source_policy.get("allow_acoustid", True)),
                progress_callback=_id_progress,
                cancel_callback=cancel_callback,
            )
            print(f"[FROM_START_TIMES] Identified {identified_count}/{_pre_id_count} segments")

            # Merge consecutive segments identified as the same track
            if identified_count > 0:
                final_segments, same_track_merges = _merge_same_track_segments(final_segments)
                if same_track_merges > 0:
                    print(f"[FROM_START_TIMES] Merged {same_track_merges} same-track segment pairs → {len(final_segments)} final")

            # Re-finalize to renumber and apply "Track NN" to unidentified
            _emit_progress(0.82, "Finalizing segment labels…")
            final_segments = _normalize_segment_boundaries(final_segments, duration_seconds)
            final_segments = _finalize_segments(final_segments)
        else:
            _emit_progress(0.68, "Applying Track labels…")
            final_segments = _force_track_labels(final_segments)

    _emit_progress(
        0.9,
        "Writing timestamp files…" if write_output_files else "Preparing timestamp editor session…",
    )
    payload = _build_output_payload(
        audio_file=audio_file,
        output_folder=_resolve_timestamp_output_directory(config, output_folder),
        final_segments=final_segments,
        settings=settings,
        dry_run=bool(dry_run),
        duration_seconds=duration_seconds,
        write_output_files=bool(write_output_files),
    )
    _emit_progress(1.0, "Timestamp export complete" if write_output_files else "Timestamp session ready")
    return payload


def generate_auto_tracklist_for_file(
    audio_file,
    output_folder,
    config=None,
    recognizer=None,
    show_progress=True,
    dry_run=False,
    progress_callback=None,
    status_callback=None,
    cancel_callback=None,
    write_output_files=True,
):
    """
    Generate timeline segments + timestamp export for one audio file.

    Returns a dict with:
      - summary
      - segments
      - timestamp_file
      - report_file
      - manifest_tracks
      - output_files
    """
    if not audio_file or not os.path.exists(audio_file):
        raise FileNotFoundError(f"Audio file not found: {audio_file}")

    def _emit_progress(fraction, detail=""):
        if cancel_callback is not None:
            cancel_callback()
        safe_fraction = max(0.0, min(1.0, float(fraction)))
        if progress_callback is not None:
            try:
                progress_callback(safe_fraction, str(detail or ""))
            except Exception:
                pass
        elif detail and status_callback is not None:
            try:
                status_callback(str(detail))
            except Exception:
                pass


    _emit_progress(0.04, "Loading audio for timestamping…")
    dry_run = bool(dry_run or is_auto_tracklist_dry_run_enabled())
    audio = AudioSegment.from_file(audio_file)
    duration_seconds = len(audio) / 1000.0
    settings = _scan_settings(config or {}, duration_seconds)
    no_identify = bool(settings.get("no_identify", False))
    _emit_progress(0.1, "Preparing timestamp scan settings…")

    shazam_enabled = not bool((config or {}).get("disable_shazam", False))
    shazam_auto_disabled = False
    shazam_auto_disabled_by_step = False
    shazam_auto_disabled_by_window_cap = False
    if shazam_enabled and float(settings.get("step_seconds", 0)) < float(_AUTO_DISABLE_SHAZAM_STEP_SECONDS):
        shazam_enabled = False
        shazam_auto_disabled = True
        shazam_auto_disabled_by_step = True
    if shazam_enabled and int(settings.get("estimated_windows", 0)) > int(settings.get("shazam_max_windows", _AUTO_SHAZAM_MAX_WINDOWS)):
        shazam_enabled = False
        shazam_auto_disabled = True
        shazam_auto_disabled_by_window_cap = True
    source_policy = _auto_tracklist_source_policy(config, recognizer=recognizer)
    effective_shazam_enabled = bool(shazam_enabled and source_policy.get("allow_shazam", True))
    settings["shazam_auto_disabled"] = bool(shazam_auto_disabled)
    settings["shazam_auto_disabled_by_step"] = bool(shazam_auto_disabled_by_step)
    settings["shazam_auto_disabled_by_window_cap"] = bool(shazam_auto_disabled_by_window_cap)
    settings["shazam_auto_disable_step_threshold_seconds"] = int(_AUTO_DISABLE_SHAZAM_STEP_SECONDS)
    settings["shazam_auto_disable_window_cap"] = int(settings.get("shazam_max_windows", _AUTO_SHAZAM_MAX_WINDOWS))
    settings["identifier_mode"] = str(source_policy.get("identifier_mode", "") or "").strip()
    settings["effective_acrcloud_enabled"] = bool(source_policy.get("allow_acrcloud", False))
    settings["effective_shazam_enabled"] = bool(effective_shazam_enabled)
    settings["effective_acoustid_enabled"] = bool(source_policy.get("allow_acoustid", True))
    settings["silence_injected_boundaries"] = 0
    settings["silence_anchor_count"] = 0
    settings["silence_first_used"] = False
    settings["silence_first_ranges"] = 0
    settings["silence_first_eligible"] = False
    settings["silence_first_min_anchors_effective"] = int(settings.get("silence_first_min_anchors", _AUTO_SILENCE_FIRST_MIN_ANCHORS))
    settings["silence_first_skipped_large_ranges"] = False
    settings["persistence_rewritten_windows"] = 0
    settings["micro_refined_boundaries"] = 0
    settings["micro_refine_essentia_assisted_boundaries"] = 0
    settings["micro_refine_rejected_low_confidence"] = 0
    settings["micro_refine_average_confidence"] = 0.0
    essentia_diag = get_essentia_runtime_diagnostics()
    settings["essentia_requested"] = bool(settings.get("essentia_enabled", _AUTO_ESSENTIA_ENABLED))
    settings["essentia_available"] = bool(essentia_diag.get("available", False))
    settings["essentia_runtime_reason"] = str(essentia_diag.get("reason", "")).strip()
    settings["essentia_python_executable"] = str(essentia_diag.get("python_executable", "")).strip()
    settings["essentia_version"] = str(essentia_diag.get("essentia_version", "")).strip()
    settings["essentia_numpy_available"] = bool(essentia_diag.get("numpy_available", False))
    settings["essentia_numpy_import_error"] = str(essentia_diag.get("numpy_import_error", "")).strip()
    settings["essentia_import_error"] = str(essentia_diag.get("essentia_import_error", "")).strip()
    settings["essentia_points_detected"] = 0
    settings["essentia_used_for_boundaries"] = False
    settings["spectral_points_detected"] = 0
    settings["spectral_used_for_boundaries"] = False
    settings["spectral_available"] = False
    settings["spectral_flux_peaks"] = 0
    settings["spectral_onset_density_peaks"] = 0
    settings["spectral_mfcc_peaks"] = 0
    settings["novelty_points_detected"] = 0
    settings["novelty_used_for_boundaries"] = False
    settings["transition_points_detected"] = 0
    settings["audio_only_refined_boundaries"] = 0
    settings["audio_only_merged_boundaries"] = 0
    settings["essentia_relaxation_applied"] = False
    settings["essentia_relaxation_passes"] = 0
    settings["essentia_raw_onset_count"] = 0
    settings["essentia_peak_fallback_count"] = 0
    scan_runtime_config = dict(config or {})
    scan_runtime_config["shazam_timeout_seconds"] = int(settings.get("shazam_timeout_seconds", _AUTO_SHAZAM_TIMEOUT_SECONDS))
    if dry_run:
        _emit_progress(0.84, "Building fallback timestamp segments…")
        segments = _fallback_interval_segments(
            duration_seconds,
            settings["fallback_interval_seconds"],
        )
    else:
        _emit_progress(0.14, "Detecting transition points…")
        essentia_points = _detect_essentia_transition_points(audio, settings)
        # Sanity check: if Essentia returns an absurd number of points
        # (e.g. the Onsets picker failed and the fallback scanned every
        # spectral peak), the points are noise — not transitions.
        # A reasonable transition detector should return at most ~1 point
        # per 8 seconds of audio.  Discard if wildly over that.
        _essentia_sanity_max = max(80, int(duration_seconds / 8.0))
        if len(essentia_points) > _essentia_sanity_max:
            essentia_points = []
        settings["essentia_points_detected"] = int(len(essentia_points))

        # ── Spectral analysis (pure-Python, cross-platform) ──────────
        _emit_progress(0.20, "Running spectral analysis…")
        spectral_points, spectral_diag = _detect_spectral_transitions(
            audio,
            min_segment_seconds=float(settings.get("min_segment_seconds", 30.0)),
            max_points=max(120, int(duration_seconds / 6.0)),
        )
        settings["spectral_available"] = bool(spectral_diag.get("spectral_available", False))
        settings["spectral_flux_peaks"] = int(spectral_diag.get("spectral_flux_peaks", 0))
        settings["spectral_onset_density_peaks"] = int(spectral_diag.get("onset_density_peaks", 0))
        settings["spectral_mfcc_peaks"] = int(spectral_diag.get("mfcc_peaks", 0))
        settings["spectral_points_detected"] = int(len(spectral_points))
        # Merge essentia + spectral into a single assist list for proximity
        # support scoring in novelty detection and boundary refinement.
        _assist_points = sorted(
            list(essentia_points) + list(spectral_points),
            key=lambda p: float(p.get("point_sec", 0.0)),
        )
        # ─────────────────────────────────────────────────────────────

        # ── UNIFIED DETECT-THEN-IDENTIFY PIPELINE ─────────────────
        # Step 1: ALWAYS detect boundaries with audio analysis first.
        # Step 2: If identifiers available and not no_identify, probe
        #         each segment with identifiers to label tracks.
        # Step 3: Merge consecutive segments identified as the same track
        #         (fixes overcutting from within-track transitions).
        _any_identifier_available = bool(
            settings.get("effective_shazam_enabled")
            or settings.get("effective_acrcloud_enabled")
            or settings.get("effective_acoustid_enabled")
        )

        # ── routing diagnostic ────────────────────────────────────────
        _will_identify = _any_identifier_available and not no_identify
        print(f"[PIPELINE ROUTING] no_identify={no_identify}  identifiers_available={_any_identifier_available}  → detect-then-identify (identify={_will_identify})")
        # ──────────────────────────────────────────────────────────────

        if no_identify:
            settings["identifier_mode"] = "no_identify"

        # ── STEP 1: Audio-only boundary detection ─────────────────────
        novelty_points = _detect_novelty_transition_points(
            audio,
            settings,
            essentia_points=_assist_points,
        )
        settings["novelty_points_detected"] = int(len(novelty_points))
        transition_points = _combine_transition_points(
            ("essentia", essentia_points, 1.0),
            ("spectral", spectral_points, 0.88),
            ("novelty", novelty_points, 0.92),
        )
        settings["transition_points_detected"] = int(len(transition_points))

        # ── diagnostic trace ───────────────────────────────────────
        _diag_lines = [
            "═══ DETECT-THEN-IDENTIFY PIPELINE DIAGNOSTIC TRACE ═══",
            f"Duration: {duration_seconds:.1f}s  |  Essentia enabled: {bool(settings.get('essentia_enabled'))}",
            f"min_segment_seconds: {settings.get('min_segment_seconds')}  |  essentia_points: {len(essentia_points)}",
            f"Essentia available: {settings.get('essentia_available')}  |  version: {settings.get('essentia_version', 'N/A')}",
            f"Essentia reason: {settings.get('essentia_runtime_reason', 'none')}  |  import_error: {settings.get('essentia_import_error', 'none')}",
            f"Essentia raw_onsets: {settings.get('essentia_raw_onset_count', 0)}  |  peak_fallback: {settings.get('essentia_peak_fallback_count', 0)}",
            f"NumPy available: {settings.get('essentia_numpy_available')}  |  Python: {settings.get('essentia_python_executable', 'N/A')}",
            "",
            f"Spectral available: {settings.get('spectral_available')}  |  spectral_points: {len(spectral_points)}",
            f"Spectral flux peaks: {settings.get('spectral_flux_peaks', 0)}  |  onset_density: {settings.get('spectral_onset_density_peaks', 0)}  |  mfcc: {settings.get('spectral_mfcc_peaks', 0)}",
            "",
            f"[1a] Novelty raw peaks: {len(novelty_points)}",
        ]
        if novelty_points:
            _np_times = [f"{p.get('point_sec', 0):.1f}s(c={p.get('confidence', 0):.2f})" for p in novelty_points]
            _diag_lines.append(f"    Points: {', '.join(_np_times)}")
        _diag_lines.append("")
        _diag_lines.append(f"[1b] Spectral raw peaks: {len(spectral_points)}")
        if spectral_points:
            _sp_times = [f"{p.get('point_sec', 0):.1f}s(c={p.get('confidence', 0):.2f})" for p in spectral_points]
            _diag_lines.append(f"    Points: {', '.join(_sp_times)}")
        _diag_lines.append("")
        _diag_lines.append(f"[2] Combined transition points: {len(transition_points)}")
        if transition_points:
            _tp_times = [f"{p.get('point_sec', 0):.1f}s(c={p.get('confidence', 0):.2f} src={p.get('sources', [])})" for p in transition_points]
            _diag_lines.append(f"    Points: {', '.join(_tp_times)}")
        # ── end diagnostic header ──────────────────────────────────

        _emit_progress(0.50, "Building transition-based timestamp segments…")
        start_times = _start_times_from_transition_points(
            transition_points,
            duration_seconds=duration_seconds,
            minimum_segment_seconds=float(settings.get("min_segment_seconds", 30)),
        )

        # ── diagnostic: start_times selection ──────────────────────
        _diag_lines.append("")
        _diag_lines.append(f"[3] Selected start_times: {len(start_times)}")
        _diag_lines.append(f"    Times: {[round(t, 1) for t in start_times]}")
        _dropped = []
        if transition_points:
            _kept_set = set(round(t, 3) for t in start_times if t > 0.0)
            for tp in transition_points:
                tp_sec = round(float(tp.get("point_sec", 0.0)), 3)
                if tp_sec not in _kept_set:
                    _dropped.append(f"{tp_sec:.1f}s(c={tp.get('confidence', 0):.2f})")
        if _dropped:
            _diag_lines.append(f"    Dropped by selection: {', '.join(_dropped)}")
        # ── end diagnostic: start_times ────────────────────────────

        if len(start_times) > 1:
            selected_points = set(
                round(float(item), 3)
                for item in list(start_times or [])
                if float(item) > 0.0
            )
            selected_transition_points = [
                dict(point)
                for point in list(transition_points or [])
                if round(float(point.get("point_sec", 0.0)), 3) in selected_points
            ]
            segments = _segments_from_transition_candidates(
                selected_transition_points,
                duration_seconds=duration_seconds,
            )

            # ── diagnostic: pre-refine segments ────────────────────
            _diag_lines.append("")
            _diag_lines.append(f"[4] Segments before refinement: {len(segments)}")
            _pre_refine_bounds = [round(float(s.get("start_sec", 0)), 1) for s in segments]
            _diag_lines.append(f"    Boundaries: {_pre_refine_bounds}")
            # ────────────────────────────────────────────────────────

            _emit_progress(0.60, "Refining transition boundaries…")
            segments, refined_boundary_count = _refine_audio_only_transition_boundaries(
                audio=audio,
                segments=segments,
                settings=settings,
                essentia_points=_assist_points,
            )
            settings["audio_only_refined_boundaries"] = int(refined_boundary_count)

            # ── diagnostic: post-refine ────────────────────────────
            _diag_lines.append("")
            _diag_lines.append(f"[5] After boundary refinement ({refined_boundary_count} refined): {len(segments)} segments")
            _post_refine_bounds = [round(float(s.get("start_sec", 0)), 1) for s in segments]
            _diag_lines.append(f"    Boundaries: {_post_refine_bounds}")
            _moved = []
            for pre_b, post_b in zip(_pre_refine_bounds, _post_refine_bounds):
                if abs(pre_b - post_b) > 0.2:
                    _moved.append(f"{pre_b:.1f}→{post_b:.1f}")
            if _moved:
                _diag_lines.append(f"    Moved boundaries: {', '.join(_moved)}")
            # ────────────────────────────────────────────────────────

            segments, merged_boundary_count = _merge_weak_audio_only_boundaries(
                segments,
                settings=settings,
                assist_points=_assist_points,
            )
            settings["audio_only_merged_boundaries"] = int(merged_boundary_count)

            # ── diagnostic: post-merge ─────────────────────────────
            _diag_lines.append("")
            _diag_lines.append(f"[6] After weak-boundary merge ({merged_boundary_count} merged): {len(segments)} segments")
            if merged_boundary_count > 0:
                _post_merge_bounds = [round(float(s.get("start_sec", 0)), 1) for s in segments]
                _diag_lines.append(f"    Boundaries: {_post_merge_bounds}")
            # ────────────────────────────────────────────────────────

            # ── end-of-file cleanup ───────────────────────────────
            if len(segments) >= 2:
                _last = segments[-1]
                _last_dur = float(_last.get("end_sec", 0.0)) - float(_last.get("start_sec", 0.0))
                _last_bc = float(_last.get("boundary_confidence", 0.0))
                if _last_dur < 35.0 and _last_bc < 0.35:
                    segments[-2]["end_sec"] = float(_last.get("end_sec", segments[-2].get("end_sec", 0.0)))
                    del segments[-1]
                    _diag_lines.append("")
                    _diag_lines.append(f"[6b] End-of-file cleanup: removed trailing {_last_dur:.1f}s segment (bc={_last_bc:.2f})")
            # ────────────────────────────────────────────────────────

            _pre_short_merge_count = len(segments)
            segments = _merge_short_segments(
                segments,
                max(float(settings.get("min_segment_seconds", 30.0)) * 0.55, 10.0),
            )

            # ── diagnostic: post short-merge ───────────────────────
            _short_merged = _pre_short_merge_count - len(segments)
            if _short_merged > 0:
                _diag_lines.append("")
                _diag_lines.append(f"[7] After short-segment merge ({_short_merged} merged): {len(segments)} segments")
                _diag_lines.append(f"    Boundaries: {[round(float(s.get('start_sec', 0)), 1) for s in segments]}")
            # ────────────────────────────────────────────────────────

            segments = _normalize_segment_boundaries(segments, duration_seconds)

            # ── STEP 2: Identify segments with available services ──
            if _will_identify:
                _pre_id_count = len(segments)
                _id_acr = bool(source_policy.get("allow_acrcloud", False))
                _id_shz = bool(source_policy.get("allow_shazam", True))
                _id_aid = bool(source_policy.get("allow_acoustid", True))
                print(f"[IDENTIFY] Starting identification of {_pre_id_count} segments")
                print(f"[IDENTIFY] Backends: acrcloud={_id_acr} (recognizer={'YES' if recognizer else 'NONE'})  shazam={_id_shz} (effective={effective_shazam_enabled})  acoustid={_id_aid}")
                print(f"[IDENTIFY] Source policy: {source_policy}")
                print(f"[IDENTIFY] min_confidence={settings.get('min_confidence', 0.58)}  short_circuit={settings.get('short_circuit_confidence', 0.90)}")
                _diag_lines.append("")
                _diag_lines.append(f"[8] Starting segment identification ({_pre_id_count} segments)…")
                _diag_lines.append(f"    Backends: acrcloud={_id_acr} shazam={_id_shz}(eff={effective_shazam_enabled}) acoustid={_id_aid}")
                _emit_progress(0.75, "Identifying tracks in detected segments…")

                def _id_progress(current, total, detail):
                    total_count = max(1, int(total or 1))
                    completed = max(0, min(total_count, int(current or 0)))
                    progress = 0.75 + (float(completed) / float(total_count)) * 0.15
                    _emit_progress(progress, detail)

                segments, identified_count = _identify_segments(
                    audio=audio,
                    segments=segments,
                    recognizer=recognizer,
                    shazam_enabled=effective_shazam_enabled,
                    settings=settings,
                    runtime_config=scan_runtime_config,
                    allow_acrcloud=bool(source_policy.get("allow_acrcloud", False)),
                    allow_shazam=bool(source_policy.get("allow_shazam", True)),
                    allow_acoustid=bool(source_policy.get("allow_acoustid", True)),
                    progress_callback=_id_progress,
                    cancel_callback=cancel_callback,
                )
                _diag_lines.append(f"    Identified: {identified_count}/{_pre_id_count} segments")

                # ── STEP 3: Merge consecutive same-track segments ──
                if identified_count > 0:
                    _pre_merge_count = len(segments)
                    segments, same_track_merges = _merge_same_track_segments(segments)
                    if same_track_merges > 0:
                        segments = _normalize_segment_boundaries(segments, duration_seconds)
                        _diag_lines.append(f"[9] Same-track merge: {same_track_merges} merges → {len(segments)} segments (was {_pre_merge_count})")
                    else:
                        _diag_lines.append(f"[9] Same-track merge: no consecutive duplicates found")
                else:
                    _diag_lines.append(f"[9] Skipped same-track merge (no identifications)")
            # ── end identification ─────────────────────────────────

            # ── diagnostic: final summary ──────────────────────────
            _diag_lines.append("")
            _diag_lines.append(f"═══ FINAL: {len(segments)} segments ═══")
            for _si, _seg in enumerate(segments):
                _id_info = ""
                if _seg.get("identified"):
                    _id_info = f"  {_seg.get('artist', '?')} — {_seg.get('title', '?')} [{_seg.get('source', '?')}]"
                _diag_lines.append(
                    f"  Track {_si + 1:2d}: {_seg.get('start_sec', 0):7.1f}s - {_seg.get('end_sec', 0):7.1f}s  "
                    f"(bc={_seg.get('boundary_confidence', 0):.2f}){_id_info}"
                )
            _diag_lines.append("")
            # Write diagnostic file next to output
            try:
                _diag_dir = _resolve_timestamp_output_directory(config, output_folder)
                os.makedirs(_diag_dir, exist_ok=True)
                _diag_path = os.path.join(_diag_dir, os.path.splitext(os.path.basename(audio_file))[0] + "_diagnostic.txt")
                with open(_diag_path, "w", encoding="utf-8") as _df:
                    _df.write("\n".join(_diag_lines))
                settings["_diagnostic_path"] = _diag_path
            except Exception:
                pass
            # Also print to stdout for immediate visibility
            for _dl in _diag_lines:
                print(_dl)
            # ── end diagnostics ────────────────────────────────────
        else:
            _emit_progress(0.9, "No confident transition points found; building fallback timeline…")
            segments = _fallback_interval_segments(
                duration_seconds,
                settings["fallback_interval_seconds"],
            )
        selected_points = set(round(float(item), 3) for item in list(start_times or []) if float(item) > 0.0)
        if selected_points:
            for point in list(transition_points or []):
                try:
                    point_sec = round(float(point.get("point_sec", 0.0)), 3)
                except Exception:
                    continue
                if point_sec not in selected_points:
                    continue
                point_sources = set(str(src).strip().lower() for src in list(point.get("sources") or []))
                if "essentia" in point_sources:
                    settings["essentia_used_for_boundaries"] = True
                if "spectral" in point_sources:
                    settings["spectral_used_for_boundaries"] = True
                if "novelty" in point_sources:
                    settings["novelty_used_for_boundaries"] = True

    _emit_progress(0.97, "Finalizing timeline output…")
    segments = _normalize_segment_boundaries(segments, duration_seconds)
    final_segments = _finalize_segments(segments)
    if no_identify:
        _emit_progress(0.98, "Applying Track labels…")
        final_segments = _force_track_labels(final_segments)
    settings["essentia"] = {
        "unified_pipeline": bool(settings.get("essentia_unified_pipeline", True)),
        "transition": {
            "enabled": bool(settings.get("essentia_enabled", _AUTO_ESSENTIA_ENABLED)),
            "min_confidence": float(settings.get("essentia_min_confidence", _AUTO_ESSENTIA_MIN_CONFIDENCE)),
            "max_points": int(settings.get("essentia_max_points", _AUTO_ESSENTIA_MAX_POINTS)),
            "points_detected": int(settings.get("essentia_points_detected", 0) or 0),
            "used_for_boundaries": bool(settings.get("essentia_used_for_boundaries", False)),
            "relaxation_applied": bool(settings.get("essentia_relaxation_applied", False)),
            "relaxation_passes": int(settings.get("essentia_relaxation_passes", 0) or 0),
            "raw_onset_count": int(settings.get("essentia_raw_onset_count", 0) or 0),
            "peak_fallback_count": int(settings.get("essentia_peak_fallback_count", 0) or 0),
            "micro_refine_assisted_boundaries": int(
                settings.get("micro_refine_essentia_assisted_boundaries", 0) or 0
            ),
        },
        "runtime": {
            "available": bool(settings.get("essentia_available", False)),
            "reason": str(settings.get("essentia_runtime_reason", "") or "").strip(),
            "python_executable": str(settings.get("essentia_python_executable", "") or "").strip(),
            "essentia_version": str(settings.get("essentia_version", "") or "").strip(),
            "numpy_available": bool(settings.get("essentia_numpy_available", False)),
            "numpy_import_error": str(settings.get("essentia_numpy_import_error", "") or "").strip(),
            "essentia_import_error": str(settings.get("essentia_import_error", "") or "").strip(),
        },
    }
    settings["spectral"] = {
        "available": bool(settings.get("spectral_available", False)),
        "points_detected": int(settings.get("spectral_points_detected", 0) or 0),
        "used_for_boundaries": bool(settings.get("spectral_used_for_boundaries", False)),
        "flux_peaks": int(settings.get("spectral_flux_peaks", 0) or 0),
        "onset_density_peaks": int(settings.get("spectral_onset_density_peaks", 0) or 0),
        "mfcc_peaks": int(settings.get("spectral_mfcc_peaks", 0) or 0),
    }

    _emit_progress(
        0.99,
        "Writing timestamp files…" if write_output_files else "Preparing timestamp editor session…",
    )
    payload = _build_output_payload(
        audio_file=audio_file,
        output_folder=_resolve_timestamp_output_directory(config, output_folder),
        final_segments=final_segments,
        settings=settings,
        dry_run=dry_run,
        duration_seconds=duration_seconds,
        write_output_files=bool(write_output_files),
    )
    _emit_progress(1.0, "Timestamp export complete" if write_output_files else "Timestamp session ready")
    return payload


def _timestamp_editor_label(track, fallback_index):
    artist = str((track or {}).get("artist") or "").strip()
    title = str((track or {}).get("title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    if title:
        return title
    existing = str((track or {}).get("timeline_label") or (track or {}).get("label") or "").strip()
    if existing:
        return existing
    return f"Track {int(fallback_index):02d}"


def _timestamp_editor_track_sort_key(track):
    return (
        int((track or {}).get("source_file_index", 0) or 0),
        _safe_seconds_value((track or {}).get("start_time"), 0.0),
        int((track or {}).get("index", 0) or 0),
    )


def _segments_from_timestamp_editor_tracks(tracks, duration_seconds):
    ordered_tracks = sorted(
        [dict(track or {}) for track in list(tracks or []) if isinstance(track, dict)],
        key=_timestamp_editor_track_sort_key,
    )
    if not ordered_tracks:
        return []

    session_duration = _safe_seconds_value(duration_seconds, 0.0)
    if session_duration <= 0.0:
        session_duration = max(
            [
                _safe_seconds_value(track.get("end_time"), 0.0)
                for track in ordered_tracks
            ]
            + [
                _safe_seconds_value(track.get("start_time"), 0.0)
                for track in ordered_tracks
            ]
            + [0.0]
        )

    final_segments = []
    export_index = 0
    for idx, track in enumerate(ordered_tracks):
        start_sec = _safe_seconds_value(track.get("start_time"), 0.0)
        if session_duration > 0.0:
            start_sec = min(start_sec, session_duration)
        if idx + 1 < len(ordered_tracks):
            next_start = _safe_seconds_value(ordered_tracks[idx + 1].get("start_time"), session_duration)
        else:
            next_start = session_duration
        if session_duration > 0.0:
            next_start = min(session_duration, next_start)
        end_sec = max(start_sec, next_start)
        if str(track.get("status") or "").strip().lower() == "skipped":
            continue

        export_index += 1
        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        identified = bool(str(track.get("status") or "").strip().lower() == "identified" and artist and title)
        confidence = 0.0
        try:
            confidence = float(track.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        sources_tried = []
        if isinstance(track.get("backend_candidates"), dict):
            sources_tried = list(track.get("backend_candidates", {}).get("sources_tried") or [])
        segment_source = str(
            track.get("identification_source")
            or track.get("timestamp_source")
            or ("auto_tracklist" if identified else "fallback")
        ).strip() or ("auto_tracklist" if identified else "fallback")
        label = _timestamp_editor_label(track, export_index)
        segment = {
            "index": export_index,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration_sec": max(0.0, end_sec - start_sec),
            "start_timestamp": _format_timestamp(start_sec),
            "end_timestamp": _format_timestamp(end_sec),
            "label": label,
            "identified": identified,
            "confidence": confidence,
            "source": segment_source,
            "sources_tried": sources_tried,
            "window_count": int(track.get("window_count", 0) or 0),
        }
        if identified:
            segment["artist"] = artist
            segment["title"] = title
        elif title:
            segment["title"] = title
        final_segments.append(segment)
    return final_segments


def export_timestamp_editor_cache(cache_path):
    cache_path = str(cache_path or "").strip()
    if not cache_path or not os.path.exists(cache_path):
        raise FileNotFoundError(f"Timestamp editor cache not found: {cache_path}")

    with open(cache_path, "r", encoding="utf-8") as handle:
        cache_data = json.load(handle)

    tracks = list((cache_data or {}).get("tracks") or [])
    raw_session_map = dict((cache_data or {}).get("timestamp_sessions") or {})
    session_map = {}
    for key, value in raw_session_map.items():
        normalized = os.path.abspath(str(key or "").strip())
        if not normalized:
            continue
        session_map[normalized] = dict(value or {})
    if not tracks or not session_map:
        raise RuntimeError("Preview cache does not contain a timestamp editor session.")

    grouped_tracks = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        source_file = os.path.abspath(str(track.get("original_file") or "").strip())
        if not source_file:
            continue
        grouped_tracks.setdefault(source_file, []).append(dict(track))

    source_keys = sorted(
        set(grouped_tracks.keys()) | set(os.path.abspath(str(key or "").strip()) for key in session_map.keys() if str(key or "").strip()),
        key=lambda key: (
            int((session_map.get(key) or {}).get("source_file_index", 0) or 0),
            str(key or "").lower(),
        ),
    )

    exported_payloads = []
    output_files = []
    skipped_sources = []
    total_segments = 0
    for source_file in source_keys:
        source_tracks = list(grouped_tracks.get(source_file) or [])
        session_info = dict(session_map.get(source_file) or {})
        duration_seconds = _safe_seconds_value(session_info.get("duration_seconds"), 0.0)
        output_directory = str(
            session_info.get("output_directory")
            or session_info.get("timestamp_output_directory")
            or os.path.join(os.getcwd(), "Tracklists")
        ).strip()
        final_segments = _segments_from_timestamp_editor_tracks(source_tracks, duration_seconds)
        if not final_segments:
            skipped_sources.append(os.path.basename(source_file) or source_file)
            continue
        payload = _build_output_payload(
            audio_file=source_file,
            output_folder=output_directory,
            final_segments=final_segments,
            settings=dict(session_info.get("scan_settings") or {}),
            dry_run=bool(session_info.get("dry_run", False)),
            duration_seconds=max(
                duration_seconds,
                _safe_seconds_value(final_segments[-1].get("end_sec"), duration_seconds),
            ),
            write_output_files=True,
        )
        exported_payloads.append(payload)
        output_files.extend(list(payload.get("output_files") or []))
        total_segments += len(final_segments)

    if not exported_payloads:
        raise RuntimeError("All timestamp segments are currently skipped.")

    return {
        "payloads": exported_payloads,
        "output_files": output_files,
        "session_count": len(exported_payloads),
        "segment_count": total_segments,
        "skipped_sources": skipped_sources,
    }
