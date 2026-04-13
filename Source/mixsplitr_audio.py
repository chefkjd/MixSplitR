"""
mixsplitr_audio.py - Audio analysis for MixSplitR

Contains:
- BPM detection using librosa (lazy-loaded)
- Audio duration estimation
"""

import os
import threading
import tempfile

# Lazy-load flags for heavy libraries
_LIBROSA_CHECKED = False
_LIBROSA_AVAILABLE = False


def _new_runtime_temp_wav(prefix="mixsplitr_bpm") -> str:
    """Create a temporary wav path in MixSplitR's runtime temp directory."""
    temp_dir = None
    try:
        from mixsplitr_core import get_runtime_temp_directory
        temp_dir = str(get_runtime_temp_directory("runtime"))
    except Exception:
        temp_dir = os.path.join(tempfile.gettempdir(), "MixSplitR", "runtime")
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except Exception:
        pass
    safe_prefix = f"{str(prefix or 'mixsplitr_bpm').strip('_')}_"
    fd, temp_path = tempfile.mkstemp(prefix=safe_prefix, suffix=".wav", dir=temp_dir)
    os.close(fd)
    return temp_path



def detect_bpm_librosa(audio_chunk):
    """Detect BPM using librosa (local analysis, works offline)
    
    Lazy-loads librosa on first call to avoid slow startup times.
    Samples 60 seconds from the middle for performance.
    Applies confidence threshold to avoid unreliable estimates.
    
    Args:
        audio_chunk: pydub AudioSegment
    
    Returns:
        dict: {'bpm': int, 'confidence': float, 'source': 'librosa'} or None
    """
    global _LIBROSA_CHECKED, _LIBROSA_AVAILABLE
    
    MIN_CONFIDENCE = 0.6
    
    # Lazy load librosa on first call
    if not _LIBROSA_CHECKED:
        _LIBROSA_CHECKED = True
        try:
            import librosa
            import numpy as np
            _LIBROSA_AVAILABLE = True
        except ImportError:
            _LIBROSA_AVAILABLE = False
            return None
    
    if not _LIBROSA_AVAILABLE:
        return None
    
    import librosa
    import numpy as np
    
    temp_file = _new_runtime_temp_wav(f"temp_bpm_{threading.current_thread().ident}")
    
    try:
        # Sample 60 seconds from the middle for performance
        chunk_len = len(audio_chunk)
        if chunk_len > 60000:
            middle_start = chunk_len // 2 - 30000
            sample_chunk = audio_chunk[max(0, middle_start):middle_start + 60000]
        else:
            sample_chunk = audio_chunk
        
        # Export to temporary WAV
        sample_chunk.export(temp_file, format="wav")
        
        # Load with librosa
        y, sr = librosa.load(temp_file, sr=None, mono=True)
        
        # Clean up temp file
        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        # Get beat onset envelope
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        
        # Get tempo estimation
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env,
            sr=sr,
            start_bpm=128,
            units='frames'
        )
        
        # Extract scalar BPM
        if hasattr(tempo, '__len__'):
            bpm = float(tempo[0]) if len(tempo) > 0 else float(tempo)
        else:
            bpm = float(tempo)
        
        bpm = int(round(bpm))
        
        # EDM normalization: correct halved/doubled BPM
        if bpm < 70:
            bpm = bpm * 2
        elif bpm > 180:
            bpm = bpm // 2
        
        # Calculate confidence based on beat consistency
        if len(beat_frames) > 4:
            beat_times = librosa.frames_to_time(beat_frames, sr=sr)
            intervals = np.diff(beat_times)
            if len(intervals) > 0:
                cv = np.std(intervals) / np.mean(intervals) if np.mean(intervals) > 0 else 1.0
                confidence = max(0.5, min(0.95, 1.0 - cv))
            else:
                confidence = 0.5
        else:
            confidence = 0.5
        
        # Apply confidence threshold
        if bpm > 0 and confidence >= MIN_CONFIDENCE:
            return {
                'bpm': bpm,
                'confidence': round(confidence, 2),
                'source': 'librosa'
            }
        
        return None
        
    except Exception as e:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None
