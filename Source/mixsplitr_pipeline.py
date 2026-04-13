#!/usr/bin/env python3
"""
MixSplitR v8.0 - Pipeline Module

Large file streaming and cache application logic.
Extracted from mixsplitr.py for maintainability.
"""

import os
import base64

from pydub import AudioSegment
from pydub.silence import split_on_silence

from mixsplitr_core import (
    Style, get_config, get_cache_path, get_audio_duration_fast,
    get_file_size_str, get_output_directory,
    get_runtime_temp_directory,
    DEFAULT_DUPLICATE_POLICY, normalize_duplicate_policy,
    DEFAULT_SPLIT_SILENCE_THRESH_DB, DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS,
    get_split_silence_threshold_db, get_split_silence_seek_step_ms,
    ffmpeg_detect_silence, ffmpeg_get_split_points_from_silence,
    ffmpeg_split_file, ffmpeg_extract_chunk_for_identification
)
from mixsplitr_editor import load_preview_cache
from mixsplitr_tagging import embed_and_sort_generic, AUDIO_FORMATS
from mixsplitr_manifest import export_manifest_for_session
from mixsplitr_artist_normalization import apply_smart_folder_canonicalization

# Visual splitter UI - optional module
SPLITTER_UI_AVAILABLE = False
try:
    from splitter_ui import get_split_points_visual, split_audio_at_points
    SPLITTER_UI_AVAILABLE = True
except ImportError:
    pass


def _split_audio_at_points_native(recording, split_points):
    if recording is None:
        return []
    duration_ms = int(max(0, len(recording)))
    if duration_ms <= 0:
        return []

    clean_points_ms = []
    for value in list(split_points or []):
        try:
            point_ms = int(round(float(value) * 1000.0))
        except Exception:
            continue
        if point_ms <= 0 or point_ms >= duration_ms:
            continue
        clean_points_ms.append(point_ms)
    clean_points_ms = sorted(set(clean_points_ms))
    if not clean_points_ms:
        return [recording]

    chunks = []
    start_ms = 0
    for point_ms in clean_points_ms:
        if point_ms <= start_ms:
            continue
        chunk = recording[start_ms:point_ms]
        if len(chunk) > 0:
            chunks.append(chunk)
        start_ms = point_ms
    final_chunk = recording[start_ms:duration_ms]
    if len(final_chunk) > 0:
        chunks.append(final_chunk)
    return chunks


def _load_cached_preview_chunks(
    audio_file,
    split_entry,
    *,
    default_split_silence_thresh_db,
    default_split_silence_seek_step_ms,
):
    recording = AudioSegment.from_file(audio_file)
    split_info = split_entry if isinstance(split_entry, dict) else {}
    method = str(split_info.get('method', '') or '').strip().lower()
    params = split_info.get('params') if isinstance(split_info.get('params'), dict) else {}

    if method == 'passthrough':
        return recording, [recording], 'passthrough', True

    points_sec = []
    for value in list(split_info.get('points_sec') or []):
        try:
            point_value = round(float(value), 3)
        except Exception:
            continue
        if point_value > 0.0:
            points_sec.append(point_value)
    points_sec = sorted(set(points_sec))
    if points_sec:
        chunks = _split_audio_at_points_native(recording, points_sec)
        try:
            expected_segments = int(split_info.get('num_segments', 0) or 0)
        except Exception:
            expected_segments = 0
        if chunks and (expected_segments <= 0 or len(chunks) == expected_segments):
            return recording, chunks, (method or 'waveform'), True

    try:
        file_thresh = float(
            params.get('split_silence_thresh_db', default_split_silence_thresh_db)
        )
    except Exception:
        file_thresh = default_split_silence_thresh_db
    try:
        file_seek_step_ms = int(
            params.get('split_seek_step_ms', default_split_silence_seek_step_ms)
        )
    except Exception:
        file_seek_step_ms = default_split_silence_seek_step_ms
    try:
        min_silence_len_ms = int(params.get('min_silence_len_ms', 2000))
    except Exception:
        min_silence_len_ms = 2000
    try:
        keep_silence_ms = int(params.get('keep_silence_ms', 200))
    except Exception:
        keep_silence_ms = 200

    chunks = split_on_silence(
        recording,
        min_silence_len=min_silence_len_ms,
        silence_thresh=file_thresh,
        keep_silence=keep_silence_ms,
        seek_step=file_seek_step_ms,
    )
    if not chunks:
        chunks = [recording]
    return recording, chunks, 'silence', False


# =============================================================================
# LARGE FILE PROCESSING (FFmpeg streaming mode)
# =============================================================================

def process_large_file_streaming(audio_file, file_num, output_folder, temp_folder,
                                  use_visual=False, use_assisted=False, preview_mode=False,
                                  split_silence_thresh_db=None):
    """
    Process a large file using FFmpeg streaming mode (no full file RAM load).

    This function:
    1. Uses FFmpeg to detect silence (streaming - no RAM)
    2. Uses FFmpeg to split at silence points (streaming - no RAM)
    3. Only loads small chunks into RAM for identification

    Returns:
        List of chunk data dictionaries compatible with the normal processing flow
    """
    filename = os.path.basename(audio_file)
    file_size = get_file_size_str(audio_file)

    print(f"\n  {Style.YELLOW}⚠ Large file detected: {file_size}{Style.RESET}")
    print(f"  {Style.DIM}Using streaming mode (FFmpeg) to avoid memory issues{Style.RESET}")

    # Get duration without loading file
    duration = get_audio_duration_fast(audio_file)
    if not duration:
        print(f"  {Style.RED}✗ Could not determine file duration{Style.RESET}")
        return []

    split_points = []
    if split_silence_thresh_db is None:
        try:
            split_silence_thresh_db = get_split_silence_threshold_db(get_config())
        except Exception:
            split_silence_thresh_db = DEFAULT_SPLIT_SILENCE_THRESH_DB

    if use_visual and SPLITTER_UI_AVAILABLE:
        # Visual mode - still works because splitter_ui downsamples
        print(f"  🎛️ Opening visual editor...")
        pts = get_split_points_visual(audio_file)
        if pts:
            split_points = pts
    elif use_assisted and SPLITTER_UI_AVAILABLE:
        # Assisted mode - detect silence with FFmpeg, then visual review
        print(f"  🔍 Detecting silence with FFmpeg (streaming)...", end='', flush=True)
        silences = ffmpeg_detect_silence(
            audio_file,
            silence_thresh_db=split_silence_thresh_db,
            min_silence_len=2.0,
        )
        print(f" found {len(silences)} silent regions")

        # Convert to split points
        pre_detected = ffmpeg_get_split_points_from_silence(silences, duration)
        print(f"  📍 {len(pre_detected)} potential split points")

        # Open visual editor with pre-loaded points
        pts = get_split_points_visual(audio_file, existing_points=pre_detected)
        if pts:
            split_points = pts

    # Fallback to automatic silence detection
    if not split_points:
        print(f"  🔍 Detecting silence with FFmpeg (streaming)...", end='', flush=True)
        silences = ffmpeg_detect_silence(
            audio_file,
            silence_thresh_db=split_silence_thresh_db,
            min_silence_len=2.0,
        )
        print(f" found {len(silences)} silent regions")
        split_points = ffmpeg_get_split_points_from_silence(silences, duration)
        print(f"  📍 {len(split_points)} split points identified")

    if not split_points:
        print(f"  {Style.YELLOW}⚠ No split points found - treating as single track{Style.RESET}")
        # For single track, we still need to extract a chunk for identification
        chunk_path = ffmpeg_extract_chunk_for_identification(
            audio_file,
            start_time=duration / 2 - 7.5,
            duration_sec=15,
            output_path=os.path.join(temp_folder, f"large_{file_num}_sample.wav")
        )

        if chunk_path:
            sample_chunk = AudioSegment.from_file(chunk_path)
            return [{
                'chunk': sample_chunk,
                'file_num': file_num,
                'original_file': audio_file,
                'split_index': 0,
                'is_large_file': True,
                'large_file_start': 0,
                'large_file_end': duration,
                'temp_chunk_path': chunk_path
            }]
        return []

    # Split the file using FFmpeg (streaming - no RAM needed)
    print(f"  ✂️ Splitting file with FFmpeg (streaming)...")
    os.makedirs(temp_folder, exist_ok=True)
    chunk_paths = ffmpeg_split_file(
        audio_file,
        split_points,
        temp_folder,
        output_prefix=f"large_{file_num}"
    )

    print(f"  {Style.GREEN}✓ Created {len(chunk_paths)} chunks{Style.RESET}")

    # Create chunk data compatible with normal flow
    all_chunks = []
    boundaries = [0] + sorted(split_points) + [duration]

    for idx, chunk_path in enumerate(chunk_paths):
        try:
            chunk = AudioSegment.from_file(chunk_path)

            all_chunks.append({
                'chunk': chunk,
                'file_num': file_num,
                'original_file': audio_file,
                'split_index': idx,
                'temp_chunk_path': chunk_path,
                'is_large_file': True,
                'large_file_start': boundaries[idx] if idx < len(boundaries) else 0,
                'large_file_end': boundaries[idx + 1] if idx + 1 < len(boundaries) else duration
            })
        except Exception as e:
            print(f"  {Style.YELLOW}⚠ Could not load chunk {idx+1}: {e}{Style.RESET}")
            continue

    return all_chunks


# =============================================================================
# APPLY FROM CACHE
# =============================================================================

def apply_from_cache(cache_path=None, temp_audio_folder=None, output_format=None):
    """Apply cached processing results"""
    # Default to safe cache location
    if cache_path is None:
        cache_path = get_cache_path("mixsplitr_cache.json")

    print(f"\n{Style.GREEN}{'═'*50}")
    print(f"  {Style.BOLD}💾 EXPORT PREVIEW SESSION{Style.RESET}{Style.GREEN} - Creating Files")
    print(f"{'═'*50}{Style.RESET}\n")

    if output_format:
        output_format = str(output_format).strip().lower()
        if output_format not in AUDIO_FORMATS:
            print(f"  {Style.YELLOW}⚠️  Unknown format '{output_format}', using FLAC{Style.RESET}")
            output_format = "flac"
    else:
        from mixsplitr_menus import show_format_selection_menu

        # Arrow-key interactive format menu (with fallback to numbered mode).
        output_format = show_format_selection_menu()
        if not output_format:
            print(f"  {Style.YELLOW}⚠️  Export cancelled. Returning to previous menu.{Style.RESET}")
            return False

    fmt_info = AUDIO_FORMATS.get(output_format, AUDIO_FORMATS['flac'])
    quality_text = "Lossless" if fmt_info['lossless'] else "Lossy"
    print(f"   {Style.GREEN}→ Using {fmt_info['name']} ({quality_text}){Style.RESET}\n")
    output_ext = str(fmt_info.get('ext') or '.flac')
    output_pydub_format = str(output_format or "flac").split("_")[0]
    if output_format == "alac":
        output_pydub_format = "ipod"
    output_export_kwargs = {}
    if fmt_info.get('codec'):
        output_export_kwargs['codec'] = fmt_info['codec']
    if fmt_info.get('bitrate'):
        output_export_kwargs['bitrate'] = fmt_info['bitrate']
    if fmt_info.get('quality'):
        output_export_kwargs['parameters'] = ['-q:a', str(fmt_info['quality'])]

    cache_data = load_preview_cache(cache_path)
    if not cache_data:
        return False
    runtime_cfg = {}
    try:
        runtime_cfg = get_config()
        split_silence_thresh_db = get_split_silence_threshold_db(runtime_cfg)
        split_silence_seek_step_ms = get_split_silence_seek_step_ms(runtime_cfg)
    except Exception:
        split_silence_thresh_db = DEFAULT_SPLIT_SILENCE_THRESH_DB
        split_silence_seek_step_ms = DEFAULT_SPLIT_SILENCE_SEEK_STEP_MS
    duplicate_policy = normalize_duplicate_policy(
        (runtime_cfg or {}).get("duplicate_policy", DEFAULT_DUPLICATE_POLICY),
        default=DEFAULT_DUPLICATE_POLICY,
    )

    tracks = cache_data.get('tracks', [])
    artwork_cache_b64 = cache_data.get('artwork_cache', {})
    split_data = cache_data.get('split_data', {}) or {}
    output_folder = cache_data.get('output_folder') or get_output_directory()
    os.makedirs(output_folder, exist_ok=True)

    try:
        apply_smart_folder_canonicalization(
            tracks,
            runtime_config=runtime_cfg,
            debug_callback=lambda line: print(f"  {line}"),
        )
    except Exception:
        pass

    if temp_audio_folder is None:
        try:
            temp_audio_folder = str(get_runtime_temp_directory("preview"))
        except Exception:
            temp_audio_folder = os.path.join(os.path.dirname(cache_path), "mixsplitr_temp")

    has_temp_files = os.path.exists(temp_audio_folder) and len(os.listdir(temp_audio_folder)) > 0

    # Count tracks to process
    to_process = [t for t in tracks if t['status'] in ['identified', 'unidentified']]
    total_to_process = len(to_process)

    print(f"\n{Style.CYAN}{'═'*50}")
    print(f"  {Style.BOLD}EXPORTING SAVED PREVIEW SESSION{Style.RESET}{Style.CYAN}")
    print(f"{'═'*50}{Style.RESET}")
    print(f"  📊 {Style.BOLD}{total_to_process}{Style.RESET} tracks to process")
    print(f"  📁 Output: {Style.DIM}{output_folder}{Style.RESET}")
    print(f"{Style.CYAN}{'─'*50}{Style.RESET}")

    print(f"\n  🎨 Decoding {len(artwork_cache_b64)} saved artworks...", end='', flush=True)
    artwork_cache = {url: base64.b64decode(b64) for url, b64 in artwork_cache_b64.items()}
    print(f" {Style.GREEN}✓{Style.RESET}")

    identified_count = unidentified_count = skipped_count = 0
    output_files_created = []
    input_files_used = set()

    if has_temp_files:
        print(f"\n  💾 Saving tracks from Preview Session...")
        for track_idx, track in enumerate(tracks, 1):
            if track['status'] == 'skipped':
                skipped_count += 1
                continue

            temp_path = track.get('temp_chunk_path')
            if not temp_path or not os.path.exists(temp_path):
                continue

            if track['status'] == 'identified':
                artist = track.get('artist', 'Unknown')[:20]
                title = track.get('title', 'Unknown')[:25]
                print(f"     [{track_idx}/{len(tracks)}] {artist} - {title}", end='\r', flush=True)

            chunk = AudioSegment.from_file(temp_path)

            if track['status'] == 'identified':
                temp_flac = os.path.join(output_folder, f"temp_apply_{track['file_num']}_{track['index']}.flac")
                chunk.export(temp_flac, format="flac")

                out_path = embed_and_sort_generic(
                    temp_flac,
                    track['artist'],
                    track['title'],
                    track['album'],
                    track.get('art_url'),
                    output_folder,
                    output_format,
                    artwork_cache,
                    track.get('enhanced_metadata', {}),
                    overwrite_existing=bool(duplicate_policy == "overwrite"),
                    duplicate_policy=duplicate_policy,
                    source_file_path=track.get('original_file'),
                    preserve_source_format=bool(
                        runtime_cfg.get("preserve_source_format", False)
                        and track.get("source_track_passthrough", False)
                    ),
                    rename_preset=runtime_cfg.get("rename_preset", "simple"),
                )
                if out_path:
                    output_files_created.append(out_path)
                    track['output_file'] = out_path
                    if track.get('original_file'):
                        input_files_used.add(track['original_file'])
                    identified_count += 1
            elif track['status'] == 'unidentified':
                unidentified_dir = os.path.join(output_folder, "Unidentified Tracks")
                os.makedirs(unidentified_dir, exist_ok=True)
                default_name = track.get('unidentified_filename') or (
                    f"File{track.get('file_num', 0)}_Track_{track.get('index', 0)+1}_Unidentified{output_ext}"
                )
                base_name = os.path.splitext(os.path.basename(default_name))[0]
                unidentified_name = f"{base_name}{output_ext}"
                unidentified_path = os.path.join(unidentified_dir, unidentified_name)
                track['unidentified_path'] = unidentified_path
                track['unidentified_filename'] = unidentified_name
                try:
                    chunk.export(unidentified_path, format=output_pydub_format, **output_export_kwargs)
                except Exception:
                    unidentified_name = f"{base_name}.flac"
                    unidentified_path = os.path.join(unidentified_dir, unidentified_name)
                    track['unidentified_path'] = unidentified_path
                    track['unidentified_filename'] = unidentified_name
                    chunk.export(unidentified_path, format="flac")
                output_files_created.append(unidentified_path)
                if track.get('original_file'):
                    input_files_used.add(track['original_file'])
                unidentified_count += 1
            del chunk
        print(" " * 60)  # Clear progress line
    else:
        print(f"\n  📂 Rebuilding preview chunks from original files (light preview mode)...")
        files_to_process = {}
        for track in tracks:
            orig = track.get('original_file')
            if orig:
                files_to_process.setdefault(orig, []).append(track)

        for file_idx, (orig_file, file_tracks) in enumerate(files_to_process.items(), 1):
            if not os.path.exists(orig_file):
                continue

            filename = os.path.basename(orig_file)[:40]
            print(f"     [{file_idx}/{len(files_to_process)}] Loading {filename}...", end='', flush=True)
            split_entry = split_data.get(os.path.abspath(orig_file)) or split_data.get(orig_file) or {}
            recording, chunks, chunk_method, used_cached_boundaries = _load_cached_preview_chunks(
                orig_file,
                split_entry,
                default_split_silence_thresh_db=split_silence_thresh_db,
                default_split_silence_seek_step_ms=split_silence_seek_step_ms,
            )
            if used_cached_boundaries:
                if chunk_method == 'passthrough':
                    print(" using saved single-track boundary...", end='', flush=True)
                else:
                    print(
                        f" using saved {chunk_method} boundaries ({len(chunks)} segments)...",
                        end='',
                        flush=True,
                    )
            else:
                print(
                    f" falling back to silence detection ({len(chunks)} segments)...",
                    end='',
                    flush=True,
                )
            print(f" saving {len([t for t in file_tracks if t['status'] == 'identified'])} tracks")

            for track in file_tracks:
                if track['status'] == 'skipped':
                    skipped_count += 1
                    continue
                chunk_idx = track.get('chunk_index', 0)
                if chunk_idx >= len(chunks):
                    continue
                chunk = chunks[chunk_idx]

                if track['status'] == 'identified':
                    temp_flac = os.path.join(output_folder, f"temp_apply_{track['file_num']}_{track['index']}.flac")
                    chunk.export(temp_flac, format="flac")
                    out_path = embed_and_sort_generic(
                        temp_flac,
                        track['artist'],
                        track['title'],
                        track['album'],
                        track.get('art_url'),
                        output_folder,
                        output_format,
                        artwork_cache,
                        track.get('enhanced_metadata', {}),
                        overwrite_existing=bool(duplicate_policy == "overwrite"),
                        duplicate_policy=duplicate_policy,
                        source_file_path=track.get('original_file'),
                        preserve_source_format=bool(
                            runtime_cfg.get("preserve_source_format", False)
                            and track.get("source_track_passthrough", False)
                        ),
                        rename_preset=runtime_cfg.get("rename_preset", "simple"),
                    )
                    if out_path:
                        output_files_created.append(out_path)
                        track['output_file'] = out_path
                        input_files_used.add(orig_file)
                        identified_count += 1
                elif track['status'] == 'unidentified':
                    unidentified_dir = os.path.join(output_folder, "Unidentified Tracks")
                    os.makedirs(unidentified_dir, exist_ok=True)
                    default_name = track.get('unidentified_filename') or (
                        f"File{track.get('file_num', 0)}_Track_{track.get('index', 0)+1}_Unidentified{output_ext}"
                    )
                    base_name = os.path.splitext(os.path.basename(default_name))[0]
                    unidentified_name = f"{base_name}{output_ext}"
                    unidentified_path = os.path.join(unidentified_dir, unidentified_name)
                    track['unidentified_path'] = unidentified_path
                    track['unidentified_filename'] = unidentified_name
                    try:
                        chunk.export(unidentified_path, format=output_pydub_format, **output_export_kwargs)
                    except Exception:
                        unidentified_name = f"{base_name}.flac"
                        unidentified_path = os.path.join(unidentified_dir, unidentified_name)
                        track['unidentified_path'] = unidentified_path
                        track['unidentified_filename'] = unidentified_name
                        chunk.export(unidentified_path, format="flac")
                    output_files_created.append(unidentified_path)
                    input_files_used.add(orig_file)
                    unidentified_count += 1
            del recording, chunks

    print(f"\n{Style.GREEN}{'═'*50}")
    print(f"  {Style.BOLD}✅ EXPORT COMPLETE!{Style.RESET}{Style.GREEN}")
    print(f"{'─'*50}{Style.RESET}")
    print(f"  {Style.GREEN}✅ Saved:{Style.RESET}        {Style.BOLD}{identified_count}{Style.RESET} tracks")
    print(f"  {Style.YELLOW}❓ Unidentified:{Style.RESET} {unidentified_count} tracks")
    print(f"  {Style.DIM}⏭️  Skipped:{Style.RESET}      {skipped_count} tracks")
    print(f"{Style.GREEN}{'═'*50}{Style.RESET}\n")

    # Save manifest for history/rollback
    if output_files_created:
        input_file = list(input_files_used)[0] if input_files_used else "unknown"

        # Build pipeline data from cached split_data (v2.0)
        _split_data = cache_data.get('split_data', {})
        _pipeline = {}
        if _split_data:
            _methods = list(set(sd.get('method', '?') for sd in _split_data.values()))
            _all_points = {}
            for fpath, sd in _split_data.items():
                _all_points[fpath] = {
                    'method': sd.get('method'),
                    'points_sec': sd.get('points_sec'),
                    'num_segments': sd.get('num_segments'),
                    'params': sd.get('params', {}),
                }
            _pipeline = {
                'split_methods': _methods,
                'per_file': _all_points,
            }

        _config_snap = cache_data.get('config_snapshot', {})
        _config_snap['output_format'] = output_format

        manifest_path = export_manifest_for_session(
            input_file=input_file,
            output_files=output_files_created,
            tracks=tracks,
            mode=_config_snap.get('identification_mode', 'preview'),
            pipeline=_pipeline,
            config_snapshot=_config_snap,
            input_files=list(input_files_used) if input_files_used else None
        )
        if manifest_path:
            print(f"  📋 Session record saved (manifest): {os.path.basename(manifest_path)}")

    return bool(output_files_created)
