#!/usr/bin/env python3
"""
mixsplitr_spectral.py - Pure-Python spectral transition detector using NumPy.

Provides timbral, BPM, and harmonic change detection for transition finding
in continuous DJ mixes.  Works on all platforms (Windows + Mac) without
needing Essentia or SciPy.

Returns points in the same format as Essentia:
    [{"point_sec": float, "confidence": float}, ...]

Three analysis layers:
1. Spectral flux novelty  - catches timbral/texture changes
2. Tempo onset density    - catches BPM shifts
3. MFCC cosine distance   - catches broad tonal shifts (bassline changes, etc.)

Each layer independently detects candidate boundaries.  The final output
merges all three and selects peaks that show multi-layer agreement or
strong single-layer evidence.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from mixsplitr_utils import clamp as _clamp

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------
_FRAME_SIZE = 2048
_HOP_SIZE = 512
_MEL_BANDS = 40
_MFCC_COEFFS = 13
_NOVELTY_KERNEL_SECONDS = 8.0   # checkerboard kernel half-width for SSM (wider = more structural)
_ONSET_WINDOW_SECONDS = 6.0     # window for onset density (wider = catches slower BPM shifts)
_MIN_PEAK_DISTANCE_SEC = 15.0   # peaks must be at least this far apart
_EDGE_MARGIN_SEC = 5.0          # ignore first/last N seconds


# ---------------------------------------------------------------------------
#  Audio conversion (pydub AudioSegment -> mono float32 numpy array)
# ---------------------------------------------------------------------------
def _audio_to_mono_float(audio) -> Tuple[Optional[Any], int]:
    """Convert pydub AudioSegment to mono float32 numpy array in [-1, 1]."""
    if not _NUMPY_AVAILABLE or audio is None:
        return None, 0
    try:
        mono = audio.set_channels(1)
        sample_rate = int(mono.frame_rate)
        samples = np.array(mono.get_array_of_samples(), dtype=np.float32)
        if samples.size == 0:
            return None, 0
        sample_width = int(getattr(mono, "sample_width", 2) or 2)
        if sample_width == 1:
            samples = samples - 128.0
            scale = 128.0
        else:
            scale = float(1 << ((8 * sample_width) - 1))
        if scale <= 0.0:
            return None, 0
        samples = np.clip(samples / scale, -1.0, 1.0).astype(np.float32)
        return samples, sample_rate
    except Exception:
        return None, 0


# ---------------------------------------------------------------------------
#  Mel filterbank (computed once, cached)
# ---------------------------------------------------------------------------
_MEL_FILTERBANK_CACHE: Dict[Tuple[int, int, int], Any] = {}


def _hz_to_mel(hz):
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _make_mel_filterbank(num_bands, fft_size, sample_rate):
    key = (num_bands, fft_size, sample_rate)
    if key in _MEL_FILTERBANK_CACHE:
        return _MEL_FILTERBANK_CACHE[key]

    num_fft_bins = fft_size // 2 + 1
    low_mel = _hz_to_mel(20.0)
    high_mel = _hz_to_mel(min(sample_rate / 2.0, 16000.0))
    mel_points = np.linspace(low_mel, high_mel, num_bands + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])
    bin_points = np.floor((fft_size + 1) * hz_points / sample_rate).astype(int)
    bin_points = np.clip(bin_points, 0, num_fft_bins - 1)

    filterbank = np.zeros((num_bands, num_fft_bins), dtype=np.float32)
    for i in range(num_bands):
        start, center, end = int(bin_points[i]), int(bin_points[i + 1]), int(bin_points[i + 2])
        if center <= start:
            center = start + 1
        if end <= center:
            end = center + 1
        for j in range(start, center):
            filterbank[i, j] = (j - start) / max(1, center - start)
        for j in range(center, end):
            filterbank[i, j] = (end - j) / max(1, end - center)

    _MEL_FILTERBANK_CACHE[key] = filterbank
    return filterbank


# ---------------------------------------------------------------------------
#  STFT and feature extraction
# ---------------------------------------------------------------------------
def _stft_frames(samples, frame_size, hop_size):
    """Compute magnitude spectrogram frames using numpy FFT."""
    n_samples = len(samples)
    window = np.hanning(frame_size).astype(np.float32)
    frames = []
    pos = 0
    while pos + frame_size <= n_samples:
        frame = samples[pos:pos + frame_size] * window
        spectrum = np.abs(np.fft.rfft(frame))
        frames.append(spectrum)
        pos += hop_size
    return frames


def _compute_mel_spectrogram(mag_frames, filterbank):
    """Apply mel filterbank to magnitude frames, return log-mel energies."""
    mel_frames = []
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for spectrum in mag_frames:
            # Use float64 to avoid overflow in matmul with large spectra
            mel_energies = np.asarray(filterbank, dtype=np.float64) @ np.asarray(spectrum, dtype=np.float64)
            # Clamp to positive before log, then clamp the log output to a
            # finite range to prevent -inf propagation in downstream matmuls.
            mel_energies = np.log10(np.maximum(mel_energies, 1e-10))
            mel_energies = np.clip(mel_energies, -10.0, 10.0).astype(np.float32)
            mel_frames.append(mel_energies)
    return mel_frames


def _compute_mfcc(mel_frames, n_coeffs):
    """Compute MFCCs via DCT-II of log-mel energies."""
    mfcc_frames = []
    n_bands = len(mel_frames[0]) if mel_frames else 0
    if n_bands == 0:
        return mfcc_frames
    # Precompute DCT-II matrix
    dct_matrix = np.zeros((n_coeffs, n_bands), dtype=np.float32)
    for k in range(n_coeffs):
        for n in range(n_bands):
            dct_matrix[k, n] = math.cos(math.pi * k * (n + 0.5) / n_bands)
    dct_matrix *= math.sqrt(2.0 / n_bands)

    for mel in mel_frames:
        mfcc = dct_matrix @ np.array(mel, dtype=np.float32)
        mfcc_frames.append(mfcc)
    return mfcc_frames


# ---------------------------------------------------------------------------
#  Layer 1: Spectral flux novelty curve
# ---------------------------------------------------------------------------
def _spectral_flux_novelty(mel_frames, kernel_frames):
    """
    Compute a self-similarity-based novelty curve from mel spectrogram.

    Uses a checkerboard kernel on the self-similarity matrix diagonal
    to detect structural boundaries.  This is the standard MIR approach
    (Foote 2000) and responds to ANY timbral change, not just energy.
    """
    n = len(mel_frames)
    if n < 4:
        return np.zeros(n, dtype=np.float32)

    # Build feature matrix (each row is a frame's mel vector)
    # Use float64 throughout to prevent overflow in self-similarity matmuls
    feat = np.array(mel_frames, dtype=np.float64)
    # Replace any NaN/inf from upstream with zeros
    feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
    # L2 normalize rows
    norms = np.linalg.norm(feat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    feat = feat / norms

    # Compute novelty using checkerboard kernel on local SSM
    # Instead of computing full NxN SSM (memory), compute local windows
    half_k = min(kernel_frames, n // 4)
    if half_k < 2:
        half_k = 2
    novelty = np.zeros(n, dtype=np.float64)

    for i in range(half_k, n - half_k):
        # Local feature blocks: before and after
        block_before = feat[i - half_k:i]   # (half_k, bands)
        block_after = feat[i:i + half_k]    # (half_k, bands)

        # Mean cosine similarity within each block vs across
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sim_bb = float(np.mean(block_before @ block_before.T))
            sim_aa = float(np.mean(block_after @ block_after.T))
            sim_ba = float(np.mean(block_before @ block_after.T))

        # Guard against NaN from degenerate blocks
        if math.isnan(sim_bb) or math.isnan(sim_aa) or math.isnan(sim_ba):
            novelty[i] = 0.0
            continue

        # Novelty = how much the cross-block similarity drops
        within_avg = (sim_bb + sim_aa) / 2.0
        novelty[i] = max(0.0, within_avg - sim_ba)

    return novelty.astype(np.float32)


# ---------------------------------------------------------------------------
#  Layer 2: Onset density change (proxy for BPM shifts)
# ---------------------------------------------------------------------------
def _onset_strength(mag_frames):
    """Compute spectral onset strength (half-wave rectified flux)."""
    n = len(mag_frames)
    if n < 2:
        return np.zeros(max(n, 1), dtype=np.float32)
    onset = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        diff = mag_frames[i] - mag_frames[i - 1]
        # Half-wave rectify (only positive changes)
        onset[i] = float(np.sum(np.maximum(diff, 0.0)))
    return onset


def _onset_density_novelty(onset_strength, window_frames):
    """
    Detect BPM changes by measuring how onset density (onsets per window)
    changes over time.  A sudden shift in onset rate indicates a tempo change.
    """
    n = len(onset_strength)
    if n < 4:
        return np.zeros(n, dtype=np.float32)

    half_w = max(2, window_frames // 2)

    # Compute windowed onset density (count of peaks above adaptive threshold)
    # First, find a local adaptive threshold
    median_onset = float(np.median(onset_strength[onset_strength > 0]) if np.any(onset_strength > 0) else 0.0)
    threshold = max(median_onset * 0.5, float(np.percentile(onset_strength, 30)))

    # Binary onset mask
    is_onset = (onset_strength > threshold).astype(np.float32)

    # Windowed density
    density = np.zeros(n, dtype=np.float32)
    cum = np.cumsum(is_onset)
    for i in range(n):
        lo = max(0, i - half_w)
        hi = min(n, i + half_w)
        count = float(cum[hi - 1] - (cum[lo - 1] if lo > 0 else 0.0))
        density[i] = count / max(1, hi - lo)

    # Novelty = absolute rate of change of density (smoothed)
    novelty = np.zeros(n, dtype=np.float32)
    smooth = max(2, half_w // 3)
    for i in range(smooth, n - smooth):
        before_density = float(np.mean(density[i - smooth:i]))
        after_density = float(np.mean(density[i:i + smooth]))
        novelty[i] = abs(after_density - before_density)

    return novelty


# ---------------------------------------------------------------------------
#  Layer 3: MFCC cosine distance novelty
# ---------------------------------------------------------------------------
def _mfcc_novelty(mfcc_frames, kernel_frames):
    """
    Compute novelty from MFCC cosine distance.

    MFCCs capture the broad tonal characteristics (bass vs treble energy
    distribution, formant-like structure).  Comparing average MFCC vectors
    before/after each point reveals tonal shifts between tracks.
    """
    n = len(mfcc_frames)
    if n < 4:
        return np.zeros(n, dtype=np.float32)

    feat = np.array(mfcc_frames, dtype=np.float64)
    feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
    # Skip coefficient 0 (overall energy) — we want timbral shape only
    if feat.shape[1] > 1:
        feat = feat[:, 1:]

    norms = np.linalg.norm(feat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    feat = feat / norms

    half_k = min(kernel_frames, n // 4)
    if half_k < 2:
        half_k = 2

    novelty = np.zeros(n, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        for i in range(half_k, n - half_k):
            mean_before = np.mean(feat[i - half_k:i], axis=0)
            mean_after = np.mean(feat[i:i + half_k], axis=0)
            # Cosine distance
            dot = float(np.dot(mean_before, mean_after))
            norm_b = float(np.linalg.norm(mean_before))
            norm_a = float(np.linalg.norm(mean_after))
            if norm_b > 1e-8 and norm_a > 1e-8:
                cosine_sim = dot / (norm_b * norm_a)
                novelty[i] = max(0.0, 1.0 - cosine_sim)
            else:
                novelty[i] = 0.0

    return novelty.astype(np.float32)


# ---------------------------------------------------------------------------
#  Peak picking
# ---------------------------------------------------------------------------
def _pick_peaks(curve, min_distance_frames, threshold_percentile=75.0):
    """Pick local maxima from a novelty curve."""
    n = len(curve)
    if n < 3:
        return []

    # Adaptive threshold
    positive = curve[curve > 0]
    if len(positive) == 0:
        return []
    threshold = float(np.percentile(positive, threshold_percentile))

    peaks = []
    half_dist = max(1, min_distance_frames // 2)
    for i in range(half_dist, n - half_dist):
        val = float(curve[i])
        if val < threshold:
            continue
        # Check it's a local maximum
        window = curve[max(0, i - half_dist):min(n, i + half_dist + 1)]
        if val + 1e-9 < float(np.max(window)):
            continue
        peaks.append((i, val))

    # Enforce minimum distance by keeping stronger peaks
    if not peaks:
        return []
    peaks.sort(key=lambda x: x[1], reverse=True)
    selected = []
    used = set()
    for idx, val in peaks:
        # Check no selected peak within min_distance_frames
        too_close = False
        for sel_idx, _ in selected:
            if abs(idx - sel_idx) < min_distance_frames:
                too_close = True
                break
        if not too_close:
            selected.append((idx, val))
    selected.sort(key=lambda x: x[0])
    return selected


# ---------------------------------------------------------------------------
#  Normalize a curve to [0, 1]
# ---------------------------------------------------------------------------
def _normalize_curve(curve):
    clean = np.nan_to_num(curve, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(clean))
    if peak <= 0:
        return clean
    return clean / peak


# ---------------------------------------------------------------------------
#  Main public function
# ---------------------------------------------------------------------------
def detect_spectral_transitions(
    audio,
    min_segment_seconds: float = 15.0,
    max_points: int = 120,
) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
    """
    Detect transition points using spectral analysis.

    Parameters
    ----------
    audio : pydub.AudioSegment
        The full mix audio.
    min_segment_seconds : float
        Minimum expected segment length.  Peaks closer than this / 2 are
        merged.
    max_points : int
        Hard cap on returned points.

    Returns
    -------
    points : list of {"point_sec": float, "confidence": float}
        Detected transition candidates, sorted by time.
    diagnostics : dict
        Debug info about each analysis layer.
    """
    diagnostics: Dict[str, Any] = {
        "spectral_available": _NUMPY_AVAILABLE,
        "spectral_flux_peaks": 0,
        "onset_density_peaks": 0,
        "mfcc_peaks": 0,
        "combined_raw": 0,
        "final_points": 0,
    }

    if not _NUMPY_AVAILABLE or audio is None:
        return [], diagnostics

    samples, sample_rate = _audio_to_mono_float(audio)
    if samples is None or sample_rate <= 0:
        return [], diagnostics

    duration_seconds = float(len(samples)) / float(sample_rate)
    if duration_seconds < 20.0:
        return [], diagnostics

    # Downsample to ~22050 Hz if higher — saves computation, keeps enough info
    if sample_rate > 24000:
        factor = sample_rate // 22050
        if factor >= 2:
            samples = samples[::factor]
            sample_rate = sample_rate // factor

    diagnostics["effective_sample_rate"] = sample_rate
    diagnostics["duration_seconds"] = round(duration_seconds, 1)

    # ----- Compute STFT -----
    mag_frames = _stft_frames(samples, _FRAME_SIZE, _HOP_SIZE)
    if len(mag_frames) < 10:
        return [], diagnostics

    frames_per_second = float(sample_rate) / float(_HOP_SIZE)
    diagnostics["frames_per_second"] = round(frames_per_second, 2)
    diagnostics["total_frames"] = len(mag_frames)

    # ----- Mel spectrogram -----
    filterbank = _make_mel_filterbank(_MEL_BANDS, _FRAME_SIZE, sample_rate)
    mel_frames = _compute_mel_spectrogram(mag_frames, filterbank)

    # ----- MFCCs -----
    mfcc_frames = _compute_mfcc(mel_frames, _MFCC_COEFFS)

    # ----- Analysis parameters -----
    kernel_frames = max(4, int(_NOVELTY_KERNEL_SECONDS * frames_per_second))
    onset_window_frames = max(4, int(_ONSET_WINDOW_SECONDS * frames_per_second))
    min_peak_frames = max(2, int(_MIN_PEAK_DISTANCE_SEC * frames_per_second))
    # Peaks should be at least 80% of min_segment_seconds apart — this is
    # a structural detector, not a beat/transient detector.
    segment_peak_frames = max(min_peak_frames, int((min_segment_seconds * 0.8) * frames_per_second))
    edge_frames = max(1, int(_EDGE_MARGIN_SEC * frames_per_second))

    # ----- Layer 1: Spectral flux novelty -----
    flux_novelty = _spectral_flux_novelty(mel_frames, kernel_frames)
    flux_novelty = _normalize_curve(flux_novelty)
    # High threshold — only strong structural boundaries (track-level changes)
    flux_peaks = _pick_peaks(flux_novelty, segment_peak_frames, threshold_percentile=82.0)
    diagnostics["spectral_flux_peaks"] = len(flux_peaks)

    # ----- Layer 2: Onset density (BPM proxy) -----
    onset_str = _onset_strength(mag_frames)
    density_novelty = _onset_density_novelty(onset_str, onset_window_frames)
    density_novelty = _normalize_curve(density_novelty)
    density_peaks = _pick_peaks(density_novelty, segment_peak_frames, threshold_percentile=84.0)
    diagnostics["onset_density_peaks"] = len(density_peaks)

    # ----- Layer 3: MFCC cosine distance -----
    mfcc_nov = _mfcc_novelty(mfcc_frames, kernel_frames)
    mfcc_nov = _normalize_curve(mfcc_nov)
    mfcc_peaks = _pick_peaks(mfcc_nov, segment_peak_frames, threshold_percentile=82.0)
    diagnostics["mfcc_peaks"] = len(mfcc_peaks)

    # ----- Merge all peaks -----
    # Convert frame indices to seconds and compute per-layer confidence
    def _frame_to_sec(frame_idx):
        return float(frame_idx * _HOP_SIZE) / float(sample_rate)

    # Collect all candidates with source info
    all_candidates = []

    for idx, val in flux_peaks:
        sec = _frame_to_sec(idx)
        if sec < _EDGE_MARGIN_SEC or sec > duration_seconds - _EDGE_MARGIN_SEC:
            continue
        all_candidates.append({
            "point_sec": sec,
            "raw_value": val,
            "source": "flux",
        })

    for idx, val in density_peaks:
        sec = _frame_to_sec(idx)
        if sec < _EDGE_MARGIN_SEC or sec > duration_seconds - _EDGE_MARGIN_SEC:
            continue
        all_candidates.append({
            "point_sec": sec,
            "raw_value": val,
            "source": "density",
        })

    for idx, val in mfcc_peaks:
        sec = _frame_to_sec(idx)
        if sec < _EDGE_MARGIN_SEC or sec > duration_seconds - _EDGE_MARGIN_SEC:
            continue
        all_candidates.append({
            "point_sec": sec,
            "raw_value": val,
            "source": "mfcc",
        })

    diagnostics["combined_raw"] = len(all_candidates)

    if not all_candidates:
        return [], diagnostics

    # Cluster nearby candidates (within merge_radius seconds)
    all_candidates.sort(key=lambda c: c["point_sec"])
    merge_radius = max(4.0, min_segment_seconds * 0.35)
    clusters: List[Dict[str, Any]] = []

    for cand in all_candidates:
        sec = cand["point_sec"]
        merged = False
        for cluster in clusters:
            if abs(sec - cluster["center"]) <= merge_radius:
                # Weighted merge
                w = cand["raw_value"]
                old_w = cluster["total_weight"]
                new_w = old_w + w
                cluster["center"] = (cluster["center"] * old_w + sec * w) / max(1e-6, new_w)
                cluster["total_weight"] = new_w
                cluster["max_value"] = max(cluster["max_value"], cand["raw_value"])
                cluster["sources"].add(cand["source"])
                cluster["count"] += 1
                merged = True
                break
        if not merged:
            clusters.append({
                "center": sec,
                "total_weight": cand["raw_value"],
                "max_value": cand["raw_value"],
                "sources": {cand["source"]},
                "count": 1,
            })

    # Score clusters: multi-source agreement boosts confidence.
    # The scoring is deliberately conservative — we want the spectral
    # detector to provide *support* to the energy-based pipeline, not
    # to overwhelm it with high-confidence noise.
    points = []
    for cluster in clusters:
        n_sources = len(cluster["sources"])
        # Scale base confidence down so only truly dominant peaks approach 1.0
        base_conf = _clamp(cluster["max_value"] * 0.70, 0.0, 0.85)

        # Multi-source bonus: 2 sources = +0.10, 3 sources = +0.20
        source_bonus = 0.10 * max(0, n_sources - 1)

        confidence = _clamp(base_conf + source_bonus, 0.0, 0.95)

        points.append({
            "point_sec": round(cluster["center"], 3),
            "confidence": round(confidence, 3),
        })

    # Sort by time
    points.sort(key=lambda p: p["point_sec"])

    # Enforce minimum spacing — keep higher confidence
    if len(points) > 1:
        min_spacing = max(10.0, min_segment_seconds * 0.65)
        filtered = [points[0]]
        for pt in points[1:]:
            if pt["point_sec"] - filtered[-1]["point_sec"] < min_spacing:
                # Keep the one with higher confidence
                if pt["confidence"] > filtered[-1]["confidence"]:
                    filtered[-1] = pt
            else:
                filtered.append(pt)
        points = filtered

    # Cap total points
    if len(points) > max_points:
        points.sort(key=lambda p: p["confidence"], reverse=True)
        points = points[:max_points]
        points.sort(key=lambda p: p["point_sec"])

    diagnostics["final_points"] = len(points)
    return points, diagnostics
