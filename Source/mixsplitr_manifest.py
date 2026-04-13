"""
Manifest export/import for MixSplitR - Track processing history for auditing & rollback

v2.0: Extended with full pipeline reproducibility support:
  - Input file SHA-256 hashes for verifiable identity
  - Split points and split method/params for exact reproduction
  - Per-track backend identification candidates (before merge)
  - Identification agreement level and source attribution
  - Configuration snapshot at time of processing
  - Rich per-field metadata with source provenance
"""

import copy
import os
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from mixsplitr_core import CURRENT_VERSION, get_app_data_dir

# Maximum file size for SHA-256 hashing (2 GB)
_HASH_SIZE_LIMIT = 2 * 1024 * 1024 * 1024


def get_manifest_dir():
    """Get directory for manifest files, respecting user config if set."""
    try:
        from mixsplitr_core import get_manifest_directory
        return Path(get_manifest_directory())
    except Exception:
        pass
    # Fallback to default
    manifest_dir = get_app_data_dir() / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    return manifest_dir


def compute_file_hash(filepath: str, algorithm: str = 'sha256',
                      chunk_size: int = 65536) -> Optional[str]:
    """
    Compute hash of a file without loading it entirely into memory.

    Skips files larger than 2 GB or files that don't exist.
    Returns hex digest string, or None on skip/error.
    """
    if not os.path.exists(filepath):
        return None
    try:
        if os.path.getsize(filepath) > _HASH_SIZE_LIMIT:
            return None
        h = hashlib.new(algorithm)
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                h.update(data)
        return h.hexdigest()
    except Exception:
        return None


def create_manifest(
    input_file: str,
    output_files: List[str],
    tracks: List[Dict[str, Any]],
    mode: str,
    session_name: Optional[str] = None,
    # ── v2.0 pipeline reproducibility fields ──────────────────────────
    pipeline: Optional[Dict[str, Any]] = None,
    config_snapshot: Optional[Dict[str, Any]] = None,
    input_files: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Create manifest entry for a processing session.

    Args:
        input_file:      Path to original mix file (primary input)
        output_files:    List of created output file paths
        tracks:          List of track result dicts from processing pipeline
        mode:            Identification mode string
        session_name:    Optional custom session name
        pipeline:        Split method, split points, params  (v2.0)
        config_snapshot: Runtime config at time of processing (v2.0)
        input_files:     All input files for multi-file sessions (v2.0)
    """
    timestamp = datetime.now().isoformat()

    # ── Build inputs section (v2.0: multi-file with hashes) ───────────
    inputs = []
    all_input_paths = list(input_files) if input_files else (
        [str(input_file)] if input_file and str(input_file) != "unknown" else []
    )
    for inp in all_input_paths:
        inp_str = str(inp)
        entry = {
            "file": inp_str,
            "size_bytes": os.path.getsize(inp_str) if os.path.exists(inp_str) else None,
            "sha256": compute_file_hash(inp_str)
        }
        inputs.append(entry)

    # Backward-compatible single-file "input" key
    primary_input = {
        "file": str(input_file),
        "size_bytes": os.path.getsize(str(input_file)) if os.path.exists(str(input_file)) else None,
    }
    if inputs:
        primary_input["sha256"] = inputs[0].get("sha256")

    # ── Assemble manifest ─────────────────────────────────────────────
    manifest = {
        "manifest_version": "2.0",
        "version": CURRENT_VERSION,
        "timestamp": timestamp,
        "session_name": session_name or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "mode": mode,
        "input": primary_input,
        "inputs": inputs,
        "pipeline": pipeline or {},
        "config": config_snapshot or {},
        "outputs": [],
        "tracks": [],
        "summary": {
            "total_tracks": len(tracks),
            "identified": 0,
            "unidentified": 0,
            "skipped": 0
        }
    }

    # ── Output files (v2.0: with hashes) ──────────────────────────────
    for out_file in output_files:
        out_str = str(out_file)
        out_entry = {"path": out_str}
        if os.path.exists(out_str):
            out_entry["size_bytes"] = os.path.getsize(out_str)
            out_entry["sha256"] = compute_file_hash(out_str)
        manifest["outputs"].append(out_entry)

    # ── Track entries (v2.0: rich identification + metadata) ──────────
    for idx, track in enumerate(tracks, 1):
        status = track.get('status', 'unknown')
        readable = track.get('readable_metadata') or {}
        enhanced = track.get('enhanced_metadata') or {}
        candidates = track.get('backend_candidates')
        artist_norm = track.get('artist_normalization')
        if not isinstance(artist_norm, dict) and isinstance(enhanced, dict):
            candidate_norm = enhanced.get('artist_normalization')
            if isinstance(candidate_norm, dict):
                artist_norm = candidate_norm

        # -- Identification provenance --
        identification = {
            "chosen_source": track.get('identification_source', 'unknown'),
            "confidence": readable.get('confidence', track.get('confidence')),
            "agreement": readable.get('agreement'),
            "sources_used": readable.get('sources_used', []),
        }
        if candidates:
            identification["backend_candidates"] = candidates
        if track.get('dual_comparison'):
            identification["dual_comparison"] = track['dual_comparison']

        # -- Rich metadata with source attribution --
        metadata = {}

        if isinstance(readable.get('bpm'), dict):
            metadata['bpm'] = readable['bpm']
        elif enhanced.get('bpm'):
            metadata['bpm'] = {'value': enhanced['bpm'], 'source': 'enrichment'}

        if isinstance(readable.get('genres'), dict):
            metadata['genres'] = readable['genres']
        elif enhanced.get('genres'):
            metadata['genres'] = {'value': enhanced['genres'], 'source': 'enrichment'}

        if isinstance(readable.get('year'), dict):
            metadata['year'] = readable['year']
        elif enhanced.get('release_date'):
            rd = enhanced['release_date']
            metadata['year'] = {
                'value': rd[:4] if len(rd) >= 4 else rd,
                'source': 'enrichment'
            }

        if isinstance(readable.get('label'), dict):
            metadata['label'] = readable['label']
        elif enhanced.get('label'):
            metadata['label'] = {'value': enhanced['label'], 'source': 'enrichment'}

        if isinstance(readable.get('isrc'), dict):
            metadata['isrc'] = readable['isrc']
        elif enhanced.get('isrc'):
            metadata['isrc'] = {'value': enhanced['isrc'], 'source': 'enrichment'}

        # -- Assemble track entry --
        recorded_output = (
            track.get('expected_filename')
            or track.get('output_file')
            or track.get('unidentified_filename')
            or os.path.basename(track.get('unidentified_path', '') or "")
            or ""
        )
        track_entry = {
            "track_number": idx,
            "status": status,
            "chunk_index": track.get('chunk_index', track.get('split_index', 0)),
            "file_num": track.get('file_num'),
            "index": track.get('index'),
            "original_file": track.get('original_file'),
            "title": track.get('title', 'Unknown'),
            "artist": track.get('artist', 'Unknown'),
            "album": track.get('album', ''),
            "output_file": recorded_output,
            "unidentified_filename": track.get('unidentified_filename'),
            "identification": identification,
            "metadata": metadata,
            # Backward-compatible flat fields (used by _display_manifest_details)
            "identification_method": track.get('identification_source', 'unknown'),
            "confidence": identification.get('confidence'),
            "tags": {
                "bpm": _extract_value(metadata.get('bpm')),
                "genre": _extract_first(metadata.get('genres')),
                "key": track.get('key')
            }
        }

        if status == 'skipped' and track.get('reason'):
            track_entry["skip_reason"] = track['reason']

        if track.get('detected_bpm'):
            track_entry["detected_bpm"] = track['detected_bpm']
            track_entry["bpm_confidence"] = track.get('bpm_confidence')

        if isinstance(artist_norm, dict):
            track_entry["artist_normalization"] = {
                "mode": str(artist_norm.get("mode", "")),
                "raw_artist": str(artist_norm.get("raw_artist", "")),
                "canonical_artist": str(artist_norm.get("canonical_artist", "")),
                "confidence": artist_norm.get("confidence"),
                "reason": str(artist_norm.get("reason", "")),
                "evidence": list(artist_norm.get("evidence") or []),
                "needs_review": bool(artist_norm.get("needs_review", False)),
            }
            if isinstance(artist_norm.get("review_candidate"), dict):
                track_entry["artist_normalization"]["review_candidate"] = artist_norm.get("review_candidate")

        manifest["tracks"].append(track_entry)

        # Update summary
        if status == 'identified':
            manifest["summary"]["identified"] += 1
        elif status == 'unidentified':
            manifest["summary"]["unidentified"] += 1
        elif status == 'skipped':
            manifest["summary"]["skipped"] += 1

    return manifest


def _extract_value(field):
    """Extract 'value' from a {value, source} dict, or return as-is."""
    if isinstance(field, dict):
        return field.get('value')
    return field


def _extract_first(field):
    """Extract first element from a list-valued {value, source} dict."""
    val = _extract_value(field)
    if isinstance(val, list) and val:
        return val[0]
    return val


def save_manifest(manifest: Dict[str, Any], filename: Optional[str] = None) -> Path:
    """
    Save manifest to file

    Args:
        manifest: Manifest dict
        filename: Optional custom filename (defaults to session_name.json)

    Returns:
        Path to saved manifest
    """
    manifest_dir = get_manifest_dir()

    if not filename:
        session_name = manifest.get('session_name', 'unknown')
        filename = f"{session_name}.json"

    filepath = manifest_dir / filename

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return filepath


def load_manifest(filepath: str) -> Optional[Dict[str, Any]]:
    """Load manifest from file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading manifest: {e}")
        return None


def list_manifests() -> List[Dict[str, Any]]:
    """List all available manifests with metadata"""
    manifest_dir = get_manifest_dir()
    manifests = []

    for filepath in sorted(manifest_dir.glob("*.json"), reverse=True):
        try:
            manifest = load_manifest(filepath)
            if manifest:
                manifests.append({
                    "filepath": str(filepath),
                    "filename": filepath.name,
                    "session_name": manifest.get("session_name"),
                    "timestamp": manifest.get("timestamp"),
                    "mode": manifest.get("mode"),
                    "total_tracks": manifest.get("summary", {}).get("total_tracks", 0),
                    "input_file": manifest.get("input", {}).get("file")
                })
        except Exception:
            continue

    return manifests


def compare_manifests(manifest1: Dict, manifest2: Dict) -> Dict[str, Any]:
    """Compare two manifests to show differences"""
    diff = {
        "tracks_changed": [],
        "files_added": [],
        "files_removed": [],
        "metadata_changes": 0
    }

    # Compare tracks
    tracks1 = {t["track_number"]: t for t in manifest1.get("tracks", [])}
    tracks2 = {t["track_number"]: t for t in manifest2.get("tracks", [])}

    for track_num in tracks1:
        if track_num in tracks2:
            t1 = tracks1[track_num]
            t2 = tracks2[track_num]

            if (t1["title"] != t2["title"] or
                t1["artist"] != t2["artist"] or
                t1["album"] != t2["album"]):
                diff["tracks_changed"].append({
                    "track_number": track_num,
                    "old": f"{t1['artist']} - {t1['title']}",
                    "new": f"{t2['artist']} - {t2['title']}"
                })
                diff["metadata_changes"] += 1

    # Compare output files
    files1 = set(o["path"] for o in manifest1.get("outputs", []))
    files2 = set(o["path"] for o in manifest2.get("outputs", []))

    diff["files_added"] = list(files2 - files1)
    diff["files_removed"] = list(files1 - files2)

    return diff


def rollback_from_manifest(manifest: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """
    Rollback to a previous manifest state

    Args:
        manifest: Manifest to rollback to
        dry_run: If True, only show what would be done

    Returns:
        Dict with results of rollback operation
    """
    results = {
        "would_restore": [] if dry_run else [],
        "would_delete": [] if dry_run else [],
        "restored": [] if not dry_run else [],
        "deleted": [] if not dry_run else [],
        "errors": []
    }

    manifest_outputs = {o["path"] for o in manifest.get("outputs", [])}

    # Find current output directory
    if manifest_outputs:
        sample_path = list(manifest_outputs)[0]
        output_dir = os.path.dirname(sample_path)

        if os.path.exists(output_dir):
            current_files = set()
            for file in os.listdir(output_dir):
                if file.endswith(('.flac', '.m4a', '.mp3')):
                    current_files.add(os.path.join(output_dir, file))

            # Files to delete (exist now but not in manifest)
            to_delete = current_files - manifest_outputs

            if dry_run:
                results["would_delete"] = list(to_delete)
            else:
                for filepath in to_delete:
                    try:
                        os.remove(filepath)
                        results["deleted"].append(filepath)
                    except Exception as e:
                        results["errors"].append(f"Could not delete {filepath}: {e}")

    # Note: Actual restoration would require re-processing from source
    # This is just showing what the manifest says should exist
    results["manifest_files"] = list(manifest_outputs)

    return results


def reorganize_from_manifest(manifest, dry_run=True):
    """
    Reorganize output files from a session based on current settings.

    Uses the raw artist/title stored in the manifest and applies (or skips)
    normalization according to the current config toggle, then renames and
    moves files into the correct artist folders.  Embedded metadata tags
    are also updated so the file contents match the new filenames.

    Args:
        manifest:  Manifest dict (v2.0 recommended but v1.0 works for basics)
        dry_run:   If True, only report what *would* change — don't touch disk

    Returns:
        dict with keys:
            changes   – list of {old_path, new_path, artist, title} for moves
            errors    – list of error message strings
            cleaned   – list of empty folders that were (or would be) removed
    """
    import shutil
    from mixsplitr_tagging import normalize_artist, _maybe_normalize

    _UNSAFE = str.maketrans('', '', '<>:"/\\|?*')

    results = {"changes": [], "errors": [], "cleaned": []}

    tracks = manifest.get("tracks", [])
    outputs = manifest.get("outputs", [])

    # Build a lookup from filename → full output path
    output_lookup = {}
    for o in outputs:
        p = o.get("path", "")
        output_lookup[os.path.basename(p)] = p

    # Determine the base output folder from the first output path
    base_output_folder = None
    if outputs:
        # Walk up from first output: .../base/ArtistFolder/file.ext
        sample = outputs[0].get("path", "")
        if sample:
            base_output_folder = os.path.dirname(os.path.dirname(sample))

    if not base_output_folder:
        results["errors"].append("Could not determine output folder from manifest")
        return results

    for track in tracks:
        raw_artist = track.get("artist") or "Unknown"
        raw_title = track.get("title") or "Unknown"
        out_filename = track.get("output_file", "")

        if not out_filename:
            results["errors"].append(
                f"Track {track.get('track_number', '?')} has no output file recorded")
            continue

        # Find the current file on disk
        current_path = output_lookup.get(out_filename)

        # Fallback: try to find by scanning artist subfolders
        if not current_path or not os.path.exists(current_path):
            found = None
            if os.path.isdir(base_output_folder):
                for folder in os.listdir(base_output_folder):
                    folder_path = os.path.join(base_output_folder, folder)
                    if not os.path.isdir(folder_path):
                        continue
                    candidate = os.path.join(folder_path, out_filename)
                    if os.path.exists(candidate):
                        found = candidate
                        break
            if found:
                current_path = found
            else:
                results["errors"].append(f"File not found: {out_filename}")
                continue

        if not os.path.exists(current_path):
            results["errors"].append(f"File missing: {current_path}")
            continue

        # Determine the file extension from what's on disk
        _, ext = os.path.splitext(current_path)

        # Apply current normalization setting to the raw values
        new_artist, new_title = _maybe_normalize(raw_artist, raw_title)

        # Build new path (sanitize artist and title separately, then combine)
        safe_artist = (new_artist or "Unknown").translate(_UNSAFE)
        safe_title = (new_title or "Unknown").translate(_UNSAFE)
        new_filename = f"{safe_artist} - {safe_title}{ext}"
        new_dir = os.path.join(base_output_folder, safe_artist)
        new_path = os.path.join(new_dir, new_filename)

        # Skip if nothing changed
        if os.path.normpath(current_path) == os.path.normpath(new_path):
            continue

        change = {
            "old_path": current_path,
            "new_path": new_path,
            "old_name": os.path.basename(current_path),
            "new_name": new_filename,
            "artist": new_artist,
            "title": new_title,
        }
        results["changes"].append(change)

        if not dry_run:
            try:
                os.makedirs(new_dir, exist_ok=True)
                shutil.move(current_path, new_path)

                # Update embedded tags to match new artist/title
                try:
                    from mixsplitr_tagging import retag_file
                    retag_file(new_path, new_artist, new_title)
                except Exception as e:
                    results["errors"].append(f"Tags not updated for {new_filename}: {e}")

            except Exception as e:
                results["errors"].append(f"Move failed for {out_filename}: {e}")

    # Clean up empty artist folders
    if not dry_run and os.path.isdir(base_output_folder):
        for folder in os.listdir(base_output_folder):
            folder_path = os.path.join(base_output_folder, folder)
            if os.path.isdir(folder_path):
                remaining = [f for f in os.listdir(folder_path) if f != 'folder.jpg']
                if not remaining:
                    try:
                        shutil.rmtree(folder_path)
                        results["cleaned"].append(folder_path)
                    except Exception:
                        pass
    elif dry_run and os.path.isdir(base_output_folder):
        # Preview which folders would become empty
        moved_from = {}
        for c in results["changes"]:
            old_dir = os.path.dirname(c["old_path"])
            moved_from[old_dir] = moved_from.get(old_dir, 0) + 1
        for folder_path, count in moved_from.items():
            if os.path.isdir(folder_path):
                real_files = [f for f in os.listdir(folder_path) if f != 'folder.jpg']
                if len(real_files) <= count:
                    results["cleaned"].append(folder_path)

    return results



def export_manifest_for_session(
    input_file: str,
    output_files: List[str],
    tracks: List[Dict[str, Any]],
    mode: str,
    # ── v2.0 pipeline reproducibility fields ──────────────────────────
    pipeline: Optional[Dict[str, Any]] = None,
    config_snapshot: Optional[Dict[str, Any]] = None,
    input_files: Optional[List[str]] = None
) -> Optional[Path]:
    """
    Convenience function to create and save manifest in one step.

    All v2.0 keyword arguments are optional for backward compatibility.

    Returns:
        Path to saved manifest, or None on error
    """
    try:
        manifest = create_manifest(
            input_file, output_files, tracks, mode,
            pipeline=pipeline,
            config_snapshot=config_snapshot,
            input_files=input_files
        )
        filepath = save_manifest(manifest)
        return filepath
    except Exception as e:
        print(f"Error exporting manifest: {e}")
        return None
