#!/usr/bin/env python3
"""
Shared Essentia runtime helpers for transition assist and genre enrichment.

This module centralizes:
- optional dependency runtime probing (NumPy + Essentia)
- pydub AudioSegment -> mono float32 conversion
- transition point detection for auto-tracklist refinement
- broad genre inference for metadata enrichment
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from mixsplitr_utils import clamp as _clamp

try:
    import numpy as np
    _NUMPY_IMPORT_ERROR = ""
except Exception as exc:
    np = None
    _NUMPY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_essentia = None
_essentia_standard = None
_ESSENTIA_AVAILABLE = False
_ESSENTIA_IMPORT_ERROR = "Not yet probed"
_ESSENTIA_PROBE_DONE = False
_ESSENTIA_PROBE_LOCK = threading.Lock()


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _probe_essentia_import(timeout_seconds: float = 20.0) -> tuple[bool, str]:
    """
    Probe Essentia import in a subprocess.

    Essentia can hard-abort the interpreter (e.g., missing native SDL/FFmpeg deps),
    which bypasses normal try/except. Probing out-of-process lets us degrade
    gracefully without crashing the main app process.
    """
    if getattr(sys, "frozen", False):
        # In frozen builds, sys.executable is the app binary. Use a dedicated
        # startup probe mode instead of "-c" to avoid recursively launching the GUI.
        cmd = [sys.executable, "--probe-essentia-import"]
    else:
        cmd = [sys.executable, "-c", "import essentia, essentia.standard"]

    child_env = os.environ.copy()
    child_env["MIXSPLITR_ESSENTIA_IMPORT_PROBE"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "TimeoutExpired: essentia import probe exceeded timeout"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if proc.returncode == 0:
        return True, ""

    stderr = (proc.stderr or "").strip().splitlines()
    tail = stderr[-1].strip() if stderr else ""
    if tail:
        return False, f"subprocess exit {proc.returncode}: {tail}"
    return False, f"subprocess exit {proc.returncode}"


DEFAULT_TRANSITION_ENABLED = True
DEFAULT_TRANSITION_MIN_CONFIDENCE = 0.36
DEFAULT_TRANSITION_MAX_POINTS = 2400

DEFAULT_GENRE_ENABLED = True
DEFAULT_GENRE_WHEN_MISSING_ONLY = True
DEFAULT_GENRE_MIN_CONFIDENCE = 0.34
DEFAULT_GENRE_MAX_TAGS = 2
DEFAULT_GENRE_ANALYSIS_SECONDS = 28.0


@dataclass
class EssentiaRuntimeStatus:
    available: bool
    reason: str
    python_executable: str
    numpy_available: bool
    numpy_import_error: str
    essentia_version: str
    essentia_import_error: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EssentiaTransitionConfig:
    enabled: bool = DEFAULT_TRANSITION_ENABLED
    min_confidence: float = DEFAULT_TRANSITION_MIN_CONFIDENCE
    max_points: int = DEFAULT_TRANSITION_MAX_POINTS


@dataclass
class EssentiaGenreConfig:
    enabled: bool = DEFAULT_GENRE_ENABLED
    when_missing_only: bool = DEFAULT_GENRE_WHEN_MISSING_ONLY
    min_confidence: float = DEFAULT_GENRE_MIN_CONFIDENCE
    max_tags: int = DEFAULT_GENRE_MAX_TAGS
    analysis_seconds: float = DEFAULT_GENRE_ANALYSIS_SECONDS


@dataclass
class EssentiaTransitionResult:
    points: List[Dict[str, float]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    runtime: EssentiaRuntimeStatus = field(
        default_factory=lambda: EssentiaRuntimeStatus(
            available=False,
            reason="",
            python_executable=str(sys.executable or "").strip(),
            numpy_available=False,
            numpy_import_error="",
            essentia_version="",
            essentia_import_error="",
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["runtime"] = self.runtime.to_dict()
        return payload


@dataclass
class EssentiaGenreResult:
    payload: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    runtime: EssentiaRuntimeStatus = field(
        default_factory=lambda: EssentiaRuntimeStatus(
            available=False,
            reason="",
            python_executable=str(sys.executable or "").strip(),
            numpy_available=False,
            numpy_import_error="",
            essentia_version="",
            essentia_import_error="",
        )
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["runtime"] = self.runtime.to_dict()
        return payload


def _refresh_runtime_state():
    """Re-probe optional deps in case runtime/interpreter state changed."""
    global np
    global _NUMPY_IMPORT_ERROR
    global _essentia
    global _essentia_standard
    global _ESSENTIA_AVAILABLE
    global _ESSENTIA_IMPORT_ERROR
    global _ESSENTIA_PROBE_DONE

    if np is None:
        try:
            import numpy as _np
            np = _np
            _NUMPY_IMPORT_ERROR = ""
        except Exception as exc:
            _NUMPY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

    if np is None:
        _ESSENTIA_AVAILABLE = False
        if not _ESSENTIA_IMPORT_ERROR:
            _ESSENTIA_IMPORT_ERROR = "NumPy unavailable"
        return

    if _ESSENTIA_AVAILABLE and _essentia is not None and _essentia_standard is not None:
        return

    if _env_truthy("MIXSPLITR_DISABLE_ESSENTIA_RUNTIME") or _env_truthy("MIXSPLITR_DISABLE_ESSENTIA"):
        _ESSENTIA_AVAILABLE = False
        _ESSENTIA_IMPORT_ERROR = "Disabled by environment"
        _ESSENTIA_PROBE_DONE = True
        return

    if not _ESSENTIA_PROBE_DONE:
        with _ESSENTIA_PROBE_LOCK:
            if not _ESSENTIA_PROBE_DONE:
                ok, err = _probe_essentia_import()
                _ESSENTIA_PROBE_DONE = True
                if not ok:
                    _ESSENTIA_AVAILABLE = False
                    _ESSENTIA_IMPORT_ERROR = err or "Essentia import probe failed"
                    return

    try:
        import essentia as essentia_mod
        import essentia.standard as essentia_standard_mod
        _essentia = essentia_mod
        _essentia_standard = essentia_standard_mod
        _ESSENTIA_AVAILABLE = True
        _ESSENTIA_IMPORT_ERROR = ""
    except Exception as exc:
        _ESSENTIA_AVAILABLE = False
        _ESSENTIA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def get_runtime_status() -> EssentiaRuntimeStatus:
    _refresh_runtime_state()
    version = ""
    if _essentia is not None:
        try:
            version = str(getattr(_essentia, "__version__", "")).strip()
        except Exception:
            version = ""

    reason = ""
    if np is None:
        reason = _NUMPY_IMPORT_ERROR or "NumPy unavailable"
    elif not _ESSENTIA_AVAILABLE:
        reason = _ESSENTIA_IMPORT_ERROR or "Essentia import failed"

    return EssentiaRuntimeStatus(
        available=bool(_ESSENTIA_AVAILABLE and np is not None),
        reason=str(reason or "").strip(),
        python_executable=str(sys.executable or "").strip(),
        numpy_available=bool(np is not None),
        numpy_import_error=str(_NUMPY_IMPORT_ERROR or "").strip(),
        essentia_version=version,
        essentia_import_error=str(_ESSENTIA_IMPORT_ERROR or "").strip(),
    )



def audiosegment_to_mono_float_array(audio, max_seconds: Optional[float] = None):
    if audio is None or np is None:
        return None, 0
    try:
        mono = audio.set_channels(1)
    except Exception:
        mono = audio
    try:
        sample_rate = int(mono.frame_rate)
    except Exception:
        return None, 0
    if sample_rate <= 0:
        return None, 0

    if max_seconds is not None:
        try:
            max_seconds_value = float(max_seconds)
        except Exception:
            max_seconds_value = 0.0
        if max_seconds_value > 0.0:
            max_ms = int(max_seconds_value * 1000.0)
            if max_ms > 1000 and len(mono) > max_ms:
                start_ms = int((len(mono) - max_ms) / 2)
                mono = mono[start_ms:start_ms + max_ms]

    try:
        samples = np.array(mono.get_array_of_samples(), dtype=np.float32)
    except Exception:
        return None, 0
    if samples.size == 0:
        return None, 0

    sample_width = int(getattr(mono, "sample_width", 2) or 2)
    if sample_width <= 0:
        sample_width = 2
    if sample_width == 1:
        samples = samples - 128.0
        scale = 128.0
    else:
        scale = float(1 << ((8 * sample_width) - 1))
    if scale <= 0.0:
        return None, 0

    samples = np.clip(samples / scale, -1.0, 1.0).astype(np.float32)
    return samples, sample_rate


def detect_transition_points(audio, config: Optional[EssentiaTransitionConfig] = None) -> EssentiaTransitionResult:
    cfg = config if isinstance(config, EssentiaTransitionConfig) else EssentiaTransitionConfig()
    runtime = get_runtime_status()
    diagnostics: Dict[str, Any] = {
        "essentia_relaxation_applied": False,
        "essentia_relaxation_passes": 0,
        "essentia_raw_onset_count": 0,
        "essentia_peak_fallback_count": 0,
    }

    if not bool(cfg.enabled):
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)
    if not runtime.available:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    samples, sample_rate = audiosegment_to_mono_float_array(audio)
    if samples is None or sample_rate <= 0:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)
    if len(samples) < 4096:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    duration_seconds = float(len(samples)) / float(sample_rate)
    min_confidence = _clamp(float(cfg.min_confidence), 0.05, 0.95)
    max_points = int(max(120, cfg.max_points))

    frame_size = 2048 if sample_rate >= 32000 else 1024
    hop_size = frame_size // 4
    if hop_size <= 0:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    try:
        window = _essentia_standard.Windowing(type="hann")
        fft = _essentia_standard.FFT()
        cart = _essentia_standard.CartesianToPolar()
        onset_hfc = _essentia_standard.OnsetDetection(method="hfc")
        onset_complex = _essentia_standard.OnsetDetection(method="complex")
    except Exception:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    novelty = []
    for frame in _essentia_standard.FrameGenerator(
        _essentia.array(samples),
        frameSize=int(frame_size),
        hopSize=int(hop_size),
        startFromZero=True,
    ):
        try:
            spectrum = fft(window(frame))
            magnitude, phase = cart(spectrum)
            hfc_value = float(onset_hfc(magnitude, phase))
            complex_value = float(onset_complex(magnitude, phase))
            novelty.append((0.62 * hfc_value) + (0.38 * complex_value))
        except Exception:
            novelty.append(0.0)

    if not novelty:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    peak = max(novelty)
    if peak <= 0:
        return EssentiaTransitionResult(points=[], diagnostics=diagnostics, runtime=runtime)

    try:
        onset_picker = _essentia_standard.Onsets()
        raw_times = onset_picker([_essentia.array(novelty)], [1.0]) or []
    except Exception:
        raw_times = []

    min_separation = 0.28
    min_separation_frames = max(1, int(math.ceil((min_separation * sample_rate) / float(hop_size))))
    min_seconds = 2.0
    max_seconds = max(2.0, duration_seconds - 2.0)
    points: List[Dict[str, float]] = []

    def _frame_confidence(index):
        idx = max(0, min(int(index), len(novelty) - 1))
        base_value = float(novelty[idx]) / float(peak)
        neighborhood = novelty[max(0, idx - 3):min(len(novelty), idx + 4)]
        local_mean = (sum(neighborhood) / float(len(neighborhood))) if neighborhood else 0.0
        prominence = max(0.0, float(novelty[idx]) - (local_mean * 0.72))
        prominence_norm = _clamp(prominence / float(peak), 0.0, 1.0)
        confidence = _clamp((base_value * 0.68) + (prominence_norm * 0.8), 0.0, 1.0)
        return confidence

    def _insert_point(point_sec, confidence):
        if point_sec < min_seconds or point_sec > max_seconds:
            return
        if points and abs(point_sec - points[-1]["point_sec"]) < min_separation:
            if confidence > points[-1]["confidence"]:
                points[-1]["point_sec"] = round(point_sec, 3)
                points[-1]["confidence"] = round(confidence, 3)
            return
        points.append(
            {
                "point_sec": round(point_sec, 3),
                "confidence": round(confidence, 3),
            }
        )

    def _novelty_peak_frames(relative_height, local_radius=2):
        cutoff = float(peak) * float(relative_height)
        frames = []
        for idx in range(local_radius, len(novelty) - local_radius):
            current = float(novelty[idx])
            if current < cutoff:
                continue
            window_values = novelty[idx - local_radius:idx + local_radius + 1]
            if not window_values:
                continue
            if current >= max(window_values):
                if frames and (idx - frames[-1]) < min_separation_frames:
                    prev_idx = frames[-1]
                    if current > float(novelty[prev_idx]):
                        frames[-1] = idx
                else:
                    frames.append(idx)
        return frames

    raw_frame_indexes = []
    for raw_time in raw_times:
        try:
            point_sec = float(raw_time)
        except Exception:
            continue

        index = int(round(point_sec * sample_rate / float(hop_size)))
        index = max(0, min(index, len(novelty) - 1))
        raw_frame_indexes.append(index)

    raw_frame_indexes = sorted(set(raw_frame_indexes))
    diagnostics["essentia_raw_onset_count"] = int(len(raw_frame_indexes))

    for index in raw_frame_indexes:
        confidence = _frame_confidence(index)
        if confidence < min_confidence:
            continue
        point_sec = float(index * hop_size) / float(sample_rate)
        _insert_point(point_sec, confidence)

    if not points:
        diagnostics["essentia_relaxation_applied"] = True
        # Relaxation fallback: progressively lower thresholds to find
        # spectral novelty peaks.  The relative_height controls what
        # fraction of the global peak a local peak must reach.
        # Keep these conservative — going below 0.12 tends to capture
        # every beat hit rather than actual transitions.
        relax_plan = [
            (0.90, 0.20),
            (0.75, 0.16),
            (0.60, 0.12),
        ]
        # Cap fallback points to a reasonable transition density:
        # at most ~1 candidate per 6 seconds of audio.
        max_fallback = max(60, int(duration_seconds / 6.0))
        fallback_count = 0
        for pass_idx, (conf_scale, rel_height) in enumerate(relax_plan, 1):
            diagnostics["essentia_relaxation_passes"] = int(pass_idx)
            threshold = max(0.05, min_confidence * float(conf_scale))
            for index in _novelty_peak_frames(relative_height=rel_height, local_radius=3):
                confidence = _frame_confidence(index)
                if confidence < threshold:
                    continue
                point_sec = float(index * hop_size) / float(sample_rate)
                before = len(points)
                _insert_point(point_sec, confidence)
                if len(points) > before:
                    fallback_count += 1
                if fallback_count >= max_fallback:
                    break
            if points:
                break
            if fallback_count >= max_fallback:
                break
        diagnostics["essentia_peak_fallback_count"] = int(fallback_count)

    if len(points) > max_points:
        points = sorted(points, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)[:max_points]
        points = sorted(points, key=lambda item: float(item.get("point_sec", 0.0)))

    return EssentiaTransitionResult(points=points, diagnostics=diagnostics, runtime=runtime)


def estimate_genre_features(samples, sample_rate):
    if samples is None or sample_rate <= 0 or np is None:
        return None
    runtime = get_runtime_status()
    if len(samples) < 2048 or not runtime.available:
        return None

    frame_size = 2048 if sample_rate >= 32000 else 1024
    hop_size = frame_size // 2
    if hop_size <= 0:
        return None

    try:
        window = _essentia_standard.Windowing(type="hann")
        fft = _essentia_standard.FFT()
        cart = _essentia_standard.CartesianToPolar()
    except Exception:
        return None

    freqs = np.fft.rfftfreq(int(frame_size), d=(1.0 / float(sample_rate))).astype(np.float32)
    low_mask = freqs <= 250.0
    mid_mask = (freqs > 250.0) & (freqs <= 2500.0)
    high_mask = freqs > 2500.0

    low_ratio_sum = 0.0
    mid_ratio_sum = 0.0
    high_ratio_sum = 0.0
    centroid_sum = 0.0
    flux_sum = 0.0
    frame_count = 0
    prev_mag = None

    for frame in _essentia_standard.FrameGenerator(
        _essentia.array(samples),
        frameSize=int(frame_size),
        hopSize=int(hop_size),
        startFromZero=True,
    ):
        try:
            spectrum = fft(window(frame))
            magnitude, _ = cart(spectrum)
        except Exception:
            continue

        mag = np.asarray(magnitude, dtype=np.float32)
        if mag.size == 0:
            continue
        usable_bins = min(mag.size, freqs.size)
        mag = mag[:usable_bins]
        bin_freqs = freqs[:usable_bins]

        power = np.square(mag).astype(np.float32, copy=False)
        total = float(np.sum(power)) + 1e-12
        if total <= 0.0:
            continue

        low_ratio_sum += float(np.sum(power[low_mask[:usable_bins]])) / total
        mid_ratio_sum += float(np.sum(power[mid_mask[:usable_bins]])) / total
        high_ratio_sum += float(np.sum(power[high_mask[:usable_bins]])) / total

        mag_sum = float(np.sum(mag)) + 1e-12
        centroid_sum += float(np.sum(bin_freqs * mag) / mag_sum)

        if prev_mag is not None:
            span = min(prev_mag.size, mag.size)
            delta = np.maximum(mag[:span] - prev_mag[:span], 0.0)
            flux_sum += float(np.sqrt(np.mean(np.square(delta).astype(np.float32, copy=False))))
        prev_mag = mag
        frame_count += 1

    if frame_count <= 0:
        return None

    danceability_raw = 0.0
    try:
        danceability_raw = float(_essentia_standard.Danceability()(_essentia.array(samples))[0])
    except Exception:
        danceability_raw = 0.0

    dynamic_complexity = 0.0
    loudness_db = 0.0
    try:
        dynamic_complexity, loudness_db = _essentia_standard.DynamicComplexity()(_essentia.array(samples))
        dynamic_complexity = float(dynamic_complexity)
        loudness_db = float(loudness_db)
    except Exception:
        dynamic_complexity = 0.0
        loudness_db = 0.0

    rms = float(np.sqrt(np.mean(np.square(samples).astype(np.float32, copy=False)))) if len(samples) else 0.0

    return {
        "low_ratio": low_ratio_sum / frame_count,
        "mid_ratio": mid_ratio_sum / frame_count,
        "high_ratio": high_ratio_sum / frame_count,
        "spectral_centroid_hz": centroid_sum / frame_count,
        "spectral_flux": flux_sum / max(1, frame_count - 1),
        "danceability_raw": danceability_raw,
        "dynamic_complexity": dynamic_complexity,
        "loudness_db": loudness_db,
        "rms": rms,
    }


def infer_genre_candidates(features, bpm_hint=None, min_confidence=DEFAULT_GENRE_MIN_CONFIDENCE, max_tags=DEFAULT_GENRE_MAX_TAGS):
    if not features:
        return []

    low_ratio = float(features.get("low_ratio", 0.0))
    mid_ratio = float(features.get("mid_ratio", 0.0))
    high_ratio = float(features.get("high_ratio", 0.0))
    centroid = float(features.get("spectral_centroid_hz", 0.0))
    flux = float(features.get("spectral_flux", 0.0))
    dance_norm = _clamp(float(features.get("danceability_raw", 0.0)) / 8.0, 0.0, 1.0)
    dynamic_complexity = float(features.get("dynamic_complexity", 0.0))
    rms = float(features.get("rms", 0.0))

    bpm = None
    try:
        if bpm_hint is not None:
            bpm = float(bpm_hint)
    except Exception:
        bpm = None

    scored = {}

    def _push(tag, score):
        score = _clamp(float(score), 0.0, 0.95)
        if score < min_confidence:
            return
        prev = scored.get(tag)
        if prev is None or score > prev:
            scored[tag] = score

    if bpm is not None and bpm >= 160.0:
        _push("Drum & Bass", 0.40 + min(0.18, (bpm - 160.0) / 180.0) + (high_ratio * 0.20) + (flux * 1.6))
    if bpm is not None and bpm >= 118.0 and dance_norm >= 0.40:
        _push("Electronic", 0.38 + (dance_norm * 0.22) + (0.08 if low_ratio >= 0.26 else 0.0))
    if bpm is not None and 118.0 <= bpm <= 132.0 and low_ratio >= 0.26 and high_ratio >= 0.12:
        _push("House", 0.36 + (dance_norm * 0.20) + (low_ratio * 0.18) + (high_ratio * 0.12))
    if bpm is not None and 124.0 <= bpm <= 145.0 and high_ratio >= 0.16 and flux >= 0.035:
        _push("Techno", 0.34 + (dance_norm * 0.16) + (high_ratio * 0.18) + (flux * 1.6))
    if bpm is not None and 70.0 <= bpm <= 108.0 and low_ratio >= 0.34 and high_ratio <= 0.20:
        _push("Hip-Hop", 0.34 + (low_ratio * 0.22) + (0.08 if dance_norm < 0.45 else 0.0))
    if high_ratio <= 0.11 and flux <= 0.035 and dynamic_complexity <= 0.08:
        _push("Ambient", 0.33 + (0.12 if rms < 0.11 else 0.0) + (0.06 if dance_norm < 0.40 else 0.0))
    if bpm is not None and bpm <= 110.0 and dance_norm < 0.45 and flux < 0.05:
        _push("Downtempo", 0.32 + (0.12 if low_ratio >= 0.28 else 0.0) + (0.10 if high_ratio < 0.16 else 0.0))
    if high_ratio >= 0.20 and dynamic_complexity >= 0.08 and centroid >= 2200.0:
        _push("Rock", 0.31 + (high_ratio * 0.20) + (dynamic_complexity * 0.60) + (flux * 1.20))
    if bpm is not None and 95.0 <= bpm <= 130.0 and dance_norm >= 0.45 and mid_ratio >= 0.33:
        _push("Pop", 0.30 + (dance_norm * 0.16) + (mid_ratio * 0.14))

    if not scored:
        if bpm is not None and bpm >= 115.0 and dance_norm >= 0.58:
            _push("Electronic", max(min_confidence, 0.34 + (dance_norm * 0.10)))
        elif flux < 0.035 and high_ratio < 0.12:
            _push("Ambient", max(min_confidence, 0.34))

    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    max_tags = int(_clamp(int(max_tags), 1, 5))
    return [
        {"name": name, "confidence": round(score, 3)}
        for name, score in ranked[:max_tags]
    ]


def count_existing_genre_sources(external_meta, existing_genre_count_hint=0):
    seen = set()
    try:
        hint_count = int(existing_genre_count_hint)
    except Exception:
        hint_count = 0
    if hint_count > 0:
        for idx in range(hint_count):
            seen.add(f"__hint_{idx}")

    if not isinstance(external_meta, dict):
        return len(seen)

    itunes_genre = ((external_meta.get("itunes") or {}).get("genre") or "").strip()
    deezer_genre = ((external_meta.get("deezer") or {}).get("genre") or "").strip()
    lastfm_tags = (external_meta.get("lastfm") or {}).get("tags") or []

    if itunes_genre:
        seen.add(itunes_genre.lower())
    if deezer_genre:
        seen.add(deezer_genre.lower())
    for tag in lastfm_tags:
        text = str(tag or "").strip()
        if text:
            seen.add(text.lower())
    return len(seen)


def extract_bpm_hint(external_meta):
    if not isinstance(external_meta, dict):
        return None
    deezer_bpm = (external_meta.get("deezer") or {}).get("bpm")
    try:
        if deezer_bpm:
            return float(deezer_bpm)
    except Exception:
        pass

    local_bpm = (external_meta.get("local_bpm") or {}).get("bpm")
    try:
        if local_bpm:
            return float(local_bpm)
    except Exception:
        return None
    return None


def infer_genres(
    audio,
    config: Optional[EssentiaGenreConfig] = None,
    bpm_hint=None,
) -> EssentiaGenreResult:
    cfg = config if isinstance(config, EssentiaGenreConfig) else EssentiaGenreConfig()
    runtime = get_runtime_status()
    diagnostics: Dict[str, Any] = {
        "analysis_seconds": float(_clamp(float(cfg.analysis_seconds), 8.0, 60.0)),
        "available": bool(runtime.available),
        "reason": str(runtime.reason or "").strip(),
    }

    if not bool(cfg.enabled):
        diagnostics["disabled"] = True
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    if not runtime.available:
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    min_confidence = float(_clamp(float(cfg.min_confidence), 0.2, 0.9))
    max_tags = int(_clamp(int(cfg.max_tags), 1, 5))
    analysis_seconds = float(_clamp(float(cfg.analysis_seconds), 8.0, 60.0))

    samples, sample_rate = audiosegment_to_mono_float_array(audio, max_seconds=analysis_seconds)
    if samples is None or sample_rate <= 0:
        diagnostics["reason"] = "Audio conversion failed"
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    features = estimate_genre_features(samples, sample_rate)
    if not features:
        diagnostics["reason"] = "Feature estimation failed"
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    candidates = infer_genre_candidates(
        features,
        bpm_hint=bpm_hint,
        min_confidence=min_confidence,
        max_tags=max_tags,
    )
    if not candidates:
        diagnostics["reason"] = "No genre candidates above threshold"
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    tags = [item["name"] for item in candidates if item.get("name")]
    if not tags:
        diagnostics["reason"] = "No genre tag names"
        return EssentiaGenreResult(payload=None, candidates=[], diagnostics=diagnostics, runtime=runtime)

    payload = {
        "genres": tags,
        "source": "Essentia",
        "confidence": float(max(item.get("confidence", 0.0) for item in candidates)),
        "analysis_seconds": round(float(analysis_seconds), 2),
    }

    diagnostics["reason"] = ""
    diagnostics["analysis_seconds"] = round(float(analysis_seconds), 2)

    return EssentiaGenreResult(
        payload=payload,
        candidates=candidates,
        diagnostics=diagnostics,
        runtime=runtime,
    )
