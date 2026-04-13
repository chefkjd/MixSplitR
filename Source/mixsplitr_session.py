#!/usr/bin/env python3
"""
MixSplitR v8.0 - Session / Manifest Management UI

Manifest browser, comparison, reorganization, and rollback UI.
Extracted from mixsplitr.py for maintainability.
"""

import os
import sys
import copy
import json
import shlex
import shutil
import tempfile
from datetime import datetime

from mixsplitr_core import Style, get_config, get_output_directory, get_runtime_temp_directory
from mixsplitr_manifest import (
    list_manifests, load_manifest, compare_manifests,
    rollback_from_manifest, reorganize_from_manifest, get_manifest_dir, save_manifest
)
from mixsplitr_menus import show_manifest_menu
from mixsplitr_menu import confirm_dialog


def _clear_screen():
    """Clear terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


# =============================================================================
# MANIFEST BROWSER
# =============================================================================

def manage_manifests():
    """Manifest browser - view, compare, rollback processing history"""

    while True:
        _clear_screen()
        manifests = list_manifests()

        print(f"\n{Style.CYAN}{'═'*60}")
        print(f"  {Style.BOLD}📋 Manifest History{Style.RESET}{Style.CYAN}")
        print(f"{'═'*60}{Style.RESET}")
        print(f"\n  📁 Manifest directory:")
        print(f"     {Style.DIM}{get_manifest_dir()}{Style.RESET}\n")

        if not manifests:
            print(f"  {Style.YELLOW}No manifests found.{Style.RESET}")
            print(f"  {Style.DIM}Manifests are auto-created after processing sessions.{Style.RESET}")
            print(f"  {Style.DIM}You can also import an exported session record (.json).{Style.RESET}\n")
            if confirm_dialog("Import a session record now?"):
                _import_session_record()
                continue
            input("  Press Enter to return...")
            return

        print(f"  {Style.GREEN}{len(manifests)} manifest(s) found{Style.RESET}\n")

        # List manifests
        for idx, m in enumerate(manifests[:10], 1):  # Show last 10
            timestamp = m['timestamp'][:19].replace('T', ' ')
            mode_value = m.get('mode')
            if mode_value == 'acrcloud':
                mode_badge = "🎵"
            elif mode_value == 'split_only_no_id':
                mode_badge = "✂️"
            elif mode_value == 'auto_tracklist_no_manual':
                mode_badge = "🧾"
            else:
                mode_badge = "🔍"
            input_name = os.path.basename(m['input_file'])

            print(f"  {Style.CYAN}{idx}.{Style.RESET} {mode_badge} {Style.BOLD}{m['session_name']}{Style.RESET}")
            print(f"     {Style.DIM}{timestamp} • {m['total_tracks']} tracks • {input_name}{Style.RESET}")

        action, selection = show_manifest_menu(manifests[:10])

        if action == "back":
            return
        if action == "cancel":
            continue

        if action == "view" and isinstance(selection, int):
            manifest = load_manifest(manifests[selection]['filepath'])
            if manifest:
                _display_manifest_details(manifest)
            continue

        if action == "compare" and isinstance(selection, tuple):
            idx1, idx2 = selection
            if 0 <= idx1 < len(manifests[:10]) and 0 <= idx2 < len(manifests[:10]):
                m1 = load_manifest(manifests[idx1]['filepath'])
                m2 = load_manifest(manifests[idx2]['filepath'])
                if m1 and m2:
                    _display_manifest_comparison(m1, m2)
            continue

        if action == "reorganize" and isinstance(selection, int):
            manifest = load_manifest(manifests[selection]['filepath'])
            if manifest:
                _reorganize_session(manifest, reorganize_from_manifest)
            continue

        if action == "edit_session" and isinstance(selection, int):
            manifest_path = manifests[selection]['filepath']
            manifest = load_manifest(manifest_path)
            if manifest:
                _edit_session_record(manifest, manifest_path)
            continue

        if action == "rollback" and isinstance(selection, int):
            manifest = load_manifest(manifests[selection]['filepath'])
            if manifest:
                _preview_rollback(manifest)
            continue

        if action == "apply_session" and isinstance(selection, int):
            manifest_path = manifests[selection]['filepath']
            manifest = load_manifest(manifest_path)
            if manifest:
                _apply_session_record_safe(manifest, manifest_path)
            continue

        if action == "delete" and isinstance(selection, int):
            _delete_session_record(manifests[selection])
            continue

        if action == "import":
            _import_session_record()
            continue

        if action == "export" and isinstance(selection, int):
            try:
                src = manifests[selection]['filepath']
                dest = input(f"  Export to (default: ./manifest_export.json): ").strip()
                dest = dest or "manifest_export.json"
                shutil.copy(src, dest)
                print(f"\n  {Style.GREEN}✅ Exported to: {dest}{Style.RESET}")
                print(f"  {Style.DIM}Add to git: git add {dest}{Style.RESET}\n")
                input("  Press Enter to continue...")
            except Exception as e:
                print(f"  {Style.RED}Export failed: {e}{Style.RESET}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _normalize_import_path(raw_text):
    """Normalize quoted/escaped path text from typed or drag-drop input."""
    normalized = (raw_text or "").strip()
    if not normalized:
        return ""
    if normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if normalized.startswith("'") and normalized.endswith("'"):
        normalized = normalized[1:-1]
    if sys.platform == "darwin" and "\\ " in normalized:
        try:
            parsed = shlex.split(normalized)
            if parsed:
                normalized = parsed[0]
        except ValueError:
            normalized = normalized.replace("\\ ", " ")
    return os.path.expanduser(normalized)


def _build_import_destination(manifest_dir, source_path):
    """Choose destination path for imported record, avoiding overwrite."""
    base_name = os.path.basename(source_path)
    candidate = os.path.join(manifest_dir, base_name)
    if not os.path.exists(candidate):
        return candidate

    name, ext = os.path.splitext(base_name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(manifest_dir, f"{name}_imported_{stamp}{ext or '.json'}")


def _import_session_record():
    """Import an exported session record (.json) into Session History."""
    print(f"\n{Style.CYAN}{'═'*60}")
    print(f"  Import Session Record")
    print(f"{'═'*60}{Style.RESET}")
    print(f"\n  {Style.DIM}Tip: drag and drop a .json session record into this prompt.{Style.RESET}")

    raw_path = input("  Path to exported session record (.json), blank to cancel:\n  → ").strip()
    if not raw_path:
        return

    source_path = _normalize_import_path(raw_path)
    if not source_path or not os.path.exists(source_path):
        print(f"\n  {Style.RED}Path not found: {source_path or raw_path}{Style.RESET}")
        input("  Press Enter to continue...")
        return

    if os.path.isdir(source_path):
        print(f"\n  {Style.RED}Please provide a .json session record file, not a directory.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    if not source_path.lower().endswith(".json"):
        print(f"\n  {Style.RED}Only .json session record files can be imported.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    imported_manifest = load_manifest(source_path)
    if not imported_manifest:
        print(f"\n  {Style.RED}Import failed: file is not a valid session record JSON.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    manifest_dir = str(get_manifest_dir())
    os.makedirs(manifest_dir, exist_ok=True)
    dest_path = _build_import_destination(manifest_dir, source_path)

    try:
        if os.path.realpath(source_path) == os.path.realpath(dest_path):
            print(f"\n  {Style.YELLOW}This session record is already in Session History.{Style.RESET}")
            input("  Press Enter to continue...")
            return
    except Exception:
        pass

    try:
        shutil.copy2(source_path, dest_path)
        session_name = imported_manifest.get("session_name", "unknown")
        print(f"\n  {Style.GREEN}✅ Imported session record.{Style.RESET}")
        print(f"     Session: {session_name}")
        print(f"     Saved as: {Style.DIM}{os.path.basename(dest_path)}{Style.RESET}")
    except Exception as exc:
        print(f"\n  {Style.RED}Import failed: {exc}{Style.RESET}")

    input("  Press Enter to continue...")


def _extract_manifest_meta_value(metadata, key, default=None):
    field = (metadata or {}).get(key)
    if isinstance(field, dict):
        return field.get("value", default)
    if field is None:
        return default
    return field


def _extract_manifest_meta_source(metadata, key, default="enrichment"):
    field = (metadata or {}).get(key)
    if isinstance(field, dict):
        source = str(field.get("source") or "").strip()
        if source:
            return source
    return default


def _build_session_editor_cache(manifest, manifest_path):
    tracks = manifest.get("tracks", []) or []
    outputs = manifest.get("outputs", []) or []
    output_lookup = _collect_manifest_output_lookup(manifest)
    disk_lookup = _collect_disk_lookup(_candidate_scan_roots(manifest, manifest_path))

    cache_tracks = []
    for idx, track in enumerate(tracks, 1):
        status = str(track.get("status") or "identified").strip().lower()
        metadata = track.get("metadata") or {}
        identification = track.get("identification") or {}

        source_path, source_name = _resolve_apply_source(
            track=track,
            outputs=outputs,
            output_lookup=output_lookup,
            disk_lookup=disk_lookup,
            index_fallback=idx,
        )
        temp_chunk_path = source_path if source_path and os.path.exists(source_path) else ""

        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        album = str(track.get("album") or "").strip() or "Unknown Album"
        confidence = identification.get("confidence", track.get("confidence", 0.0)) or 0.0
        agreement = identification.get("agreement") or "none"
        sources_used = list(identification.get("sources_used") or [])
        chosen_source = str(
            identification.get("chosen_source")
            or track.get("identification_method")
            or "session_history"
        ).strip()

        if not sources_used and chosen_source:
            sources_used = [chosen_source]

        genres_value = _extract_manifest_meta_value(metadata, "genres", [])
        if isinstance(genres_value, str):
            genres_value = [genres_value]
        elif not isinstance(genres_value, list):
            genres_value = []
        genres_value = [str(g).strip() for g in genres_value if str(g).strip()]

        year_value = _extract_manifest_meta_value(metadata, "year", "")
        year_text = str(year_value).strip() if year_value is not None else ""
        bpm_value = _extract_manifest_meta_value(metadata, "bpm", None)
        label_value = _extract_manifest_meta_value(metadata, "label", "")
        isrc_value = _extract_manifest_meta_value(metadata, "isrc", "")

        enhanced = {}
        if genres_value:
            enhanced["genres"] = genres_value
        if year_text:
            enhanced["release_date"] = year_text
        if label_value:
            enhanced["label"] = str(label_value).strip()
        if isrc_value:
            enhanced["isrc"] = str(isrc_value).strip()
        try:
            if bpm_value is not None and str(bpm_value).strip() != "":
                bpm_int = int(float(bpm_value))
                if bpm_int > 0:
                    enhanced["bpm"] = bpm_int
        except Exception:
            pass

        readable = {
            "artist": {"value": artist, "source": chosen_source},
            "title": {"value": title, "source": chosen_source},
            "album": {"value": album, "source": chosen_source},
            "confidence": float(confidence) if confidence is not None else 0.0,
            "agreement": agreement,
            "sources_used": sources_used,
        }
        if genres_value:
            readable["genres"] = {
                "value": genres_value,
                "source": _extract_manifest_meta_source(metadata, "genres", "Session History"),
            }
        if year_text:
            readable["year"] = {
                "value": year_text,
                "source": _extract_manifest_meta_source(metadata, "year", "Session History"),
            }
        if "bpm" in enhanced:
            readable["bpm"] = {
                "value": enhanced["bpm"],
                "source": _extract_manifest_meta_source(metadata, "bpm", "Session History"),
            }
        if label_value:
            readable["label"] = {
                "value": str(label_value).strip(),
                "source": _extract_manifest_meta_source(metadata, "label", "Session History"),
            }
        if isrc_value:
            readable["isrc"] = {
                "value": str(isrc_value).strip(),
                "source": _extract_manifest_meta_source(metadata, "isrc", "Session History"),
            }

        expected_filename = str(track.get("output_file") or "").strip()
        if not expected_filename:
            ext = os.path.splitext(source_name)[1] if source_name else ".flac"
            if status == "unidentified":
                expected_filename = f"Track_{idx:02d}_Unidentified{ext}"
            else:
                safe_artist = (artist or "Unknown").translate(_UNSAFE_FS_CHARS)
                safe_title = (title or f"Track {idx:02d}").translate(_UNSAFE_FS_CHARS)
                expected_filename = f"{safe_artist} - {safe_title}{ext or '.flac'}"

        unidentified_filename = str(
            track.get("unidentified_filename")
            or (expected_filename if status == "unidentified" else "")
        ).strip()
        if status == "unidentified" and not unidentified_filename:
            unidentified_filename = f"Track_{idx:02d}_Unidentified.flac"

        cache_tracks.append({
            "status": status if status in ("identified", "unidentified", "skipped") else "identified",
            "index": idx - 1,
            "chunk_index": int(track.get("chunk_index", idx - 1) or 0),
            "file_num": int(track.get("file_num", 1) or 1),
            "artist": artist,
            "title": title,
            "album": album,
            "expected_filename": expected_filename,
            "unidentified_filename": unidentified_filename,
            "identification_source": chosen_source,
            "enhanced_metadata": enhanced,
            "readable_metadata": readable,
            "backend_candidates": identification.get("backend_candidates") or {},
            "original_file": track.get("original_file"),
            "temp_chunk_path": temp_chunk_path,
            "detected_bpm": track.get("detected_bpm"),
            "bpm_confidence": track.get("bpm_confidence"),
        })

    return {
        "tracks": cache_tracks,
        "output_folder": os.path.dirname(manifest_path),
        "artwork_cache": {},
        "light_preview": False,
        "config_snapshot": {
            "identification_mode": manifest.get("mode"),
            "session_editor": True,
            "source_manifest": os.path.basename(manifest_path),
        },
    }


def _apply_session_editor_cache_to_manifest(manifest, updated_cache):
    tracks = manifest.get("tracks", []) or []
    editor_tracks = list((updated_cache or {}).get("tracks") or [])
    limit = min(len(tracks), len(editor_tracks))

    for idx in range(limit):
        manifest_track = tracks[idx]
        editor_track = editor_tracks[idx] or {}
        metadata = {}
        readable = editor_track.get("readable_metadata") or {}
        enhanced = editor_track.get("enhanced_metadata") or {}

        status = str(editor_track.get("status") or manifest_track.get("status") or "identified").strip().lower()
        if status not in ("identified", "unidentified", "skipped"):
            status = "identified"
        manifest_track["status"] = status

        artist = str(editor_track.get("artist") or "").strip()
        title = str(editor_track.get("title") or "").strip()
        album = str(editor_track.get("album") or "").strip()
        if status == "unidentified":
            if not title:
                title = manifest_track.get("title") or f"Track {idx + 1:02d}"
            artist = ""
            album = album or manifest_track.get("album") or "Unknown Album"
        else:
            artist = artist or manifest_track.get("artist") or "Unknown"
            title = title or manifest_track.get("title") or f"Track {idx + 1:02d}"
            album = album or manifest_track.get("album") or "Unknown Album"

        manifest_track["artist"] = artist
        manifest_track["title"] = title
        manifest_track["album"] = album
        manifest_track["identification_method"] = str(
            editor_track.get("identification_source")
            or manifest_track.get("identification_method")
            or "session_editor"
        ).strip() or "session_editor"

        ident = dict(manifest_track.get("identification") or {})
        ident["chosen_source"] = manifest_track["identification_method"]
        ident["confidence"] = readable.get("confidence", ident.get("confidence", manifest_track.get("confidence", 0.0)))
        ident["agreement"] = readable.get("agreement", ident.get("agreement"))
        ident["sources_used"] = list(readable.get("sources_used") or ident.get("sources_used") or [])
        backend_candidates = editor_track.get("backend_candidates")
        if backend_candidates:
            ident["backend_candidates"] = backend_candidates
        manifest_track["identification"] = ident
        manifest_track["confidence"] = ident.get("confidence")

        genres = enhanced.get("genres") or []
        if isinstance(genres, str):
            genres = [genres]
        genres = [str(g).strip() for g in genres if str(g).strip()]
        if genres:
            source = "Session Editor"
            if isinstance(readable.get("genres"), dict):
                source = str(readable.get("genres", {}).get("source") or source).strip() or source
            metadata["genres"] = {"value": genres, "source": source}

        year_value = str(enhanced.get("release_date") or "").strip()
        if year_value:
            year_token = year_value[:4] if len(year_value) >= 4 else year_value
            source = "Session Editor"
            if isinstance(readable.get("year"), dict):
                source = str(readable.get("year", {}).get("source") or source).strip() or source
            metadata["year"] = {"value": year_token, "source": source}

        try:
            bpm_value = enhanced.get("bpm")
            if bpm_value is not None and str(bpm_value).strip() != "":
                bpm_int = int(float(bpm_value))
                if bpm_int > 0:
                    source = "Session Editor"
                    if isinstance(readable.get("bpm"), dict):
                        source = str(readable.get("bpm", {}).get("source") or source).strip() or source
                    metadata["bpm"] = {"value": bpm_int, "source": source}
        except Exception:
            pass

        label_value = str(enhanced.get("label") or "").strip()
        if label_value:
            source = "Session Editor"
            if isinstance(readable.get("label"), dict):
                source = str(readable.get("label", {}).get("source") or source).strip() or source
            metadata["label"] = {"value": label_value, "source": source}

        isrc_value = str(enhanced.get("isrc") or "").strip()
        if isrc_value:
            source = "Session Editor"
            if isinstance(readable.get("isrc"), dict):
                source = str(readable.get("isrc", {}).get("source") or source).strip() or source
            metadata["isrc"] = {"value": isrc_value, "source": source}

        manifest_track["metadata"] = metadata
        manifest_track["tags"] = {
            "bpm": metadata.get("bpm", {}).get("value"),
            "genre": (metadata.get("genres", {}).get("value") or [None])[0],
            "key": (manifest_track.get("tags") or {}).get("key"),
        }

        if status == "unidentified":
            unidentified_filename = str(
                editor_track.get("unidentified_filename")
                or manifest_track.get("unidentified_filename")
                or manifest_track.get("output_file")
                or f"Track_{idx + 1:02d}_Unidentified.flac"
            ).strip()
            manifest_track["unidentified_filename"] = unidentified_filename
            if not manifest_track.get("output_file"):
                manifest_track["output_file"] = unidentified_filename
        else:
            manifest_track.pop("unidentified_filename", None)
            if not manifest_track.get("output_file"):
                expected_filename = str(editor_track.get("expected_filename") or "").strip()
                if expected_filename:
                    manifest_track["output_file"] = expected_filename

        reason = str(editor_track.get("reason") or "").strip()
        if status == "skipped":
            manifest_track["skip_reason"] = reason or "user_edited"
        else:
            manifest_track.pop("skip_reason", None)

    summary = manifest.setdefault("summary", {})
    summary["total_tracks"] = len(tracks)
    summary["identified"] = len([t for t in tracks if str(t.get("status")).lower() == "identified"])
    summary["unidentified"] = len([t for t in tracks if str(t.get("status")).lower() == "unidentified"])
    summary["skipped"] = len([t for t in tracks if str(t.get("status")).lower() == "skipped"])

    edits = manifest.setdefault("session_editor", {})
    edits["last_edited_at"] = datetime.now().isoformat()
    edits["edited_track_count"] = int(limit)
    edits["editor_version"] = "interactive_editor"


def _save_manifest_in_place(manifest, manifest_path):
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)


def _edit_session_record(manifest, manifest_path):
    """Open selected session record in the interactive editor and persist edits."""
    try:
        from mixsplitr_editor import interactive_editor
    except Exception as exc:
        print(f"\n  {Style.RED}Could not open session editor: {exc}{Style.RESET}")
        input("  Press Enter to continue...")
        return

    cache_data = _build_session_editor_cache(manifest, manifest_path)
    if not cache_data.get("tracks"):
        print(f"\n  {Style.YELLOW}Session has no tracks to edit.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    temp_base = str(get_runtime_temp_directory("session_editor"))
    try:
        os.makedirs(temp_base, exist_ok=True)
    except Exception:
        temp_base = None
    temp_dir = tempfile.mkdtemp(prefix="mixsplitr_session_editor_", dir=temp_base)
    temp_cache_path = os.path.join(temp_dir, "session_editor_cache.json")
    try:
        with open(temp_cache_path, "w", encoding="utf-8") as handle:
            json.dump(cache_data, handle, indent=2, ensure_ascii=False)

        print(f"\n  {Style.CYAN}Opening Session Editor...{Style.RESET}")
        print(f"  {Style.DIM}Edits will update this session record file only.{Style.RESET}")
        editor_result = interactive_editor(cache_data, temp_cache_path)

        if editor_result == "quit":
            return

        try:
            with open(temp_cache_path, "r", encoding="utf-8") as handle:
                updated_cache = json.load(handle)
        except Exception:
            updated_cache = cache_data

        _apply_session_editor_cache_to_manifest(manifest, updated_cache)
        _save_manifest_in_place(manifest, manifest_path)

        print(f"\n  {Style.GREEN}✅ Session record updated.{Style.RESET}")
        print(f"     {Style.DIM}{os.path.basename(manifest_path)}{Style.RESET}")
        input("  Press Enter to continue...")
    except Exception as exc:
        print(f"\n  {Style.RED}Session editor failed: {exc}{Style.RESET}")
        input("  Press Enter to continue...")
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


_UNSAFE_FS_CHARS = str.maketrans('', '', '<>:"/\\|?*')
_AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3", ".wav", ".aiff", ".ogg", ".opus", ".aac", ".alac"}


def _get_default_output_dir_fallback():
    """Best-effort output directory without raising on permissions issues."""
    try:
        return get_output_directory()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Music", "MixSplitR Library")


def _safe_track_number(track, fallback_index):
    """Best-effort integer track number from manifest track entry."""
    try:
        return int(track.get("track_number", fallback_index))
    except Exception:
        return int(fallback_index)


def _collect_manifest_output_lookup(manifest):
    """Build filename -> existing path lookup from manifest outputs."""
    lookup = {}
    for out in manifest.get("outputs", []):
        path = (out.get("path") or "").strip()
        if not path:
            continue
        base = os.path.basename(path)
        if not base:
            continue
        if os.path.exists(path):
            lookup.setdefault(base, path)
    return lookup


def _collect_disk_lookup(scan_roots):
    """Scan candidate roots for audio files by basename."""
    lookup = {}
    for root in scan_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext and ext not in _AUDIO_EXTENSIONS:
                    continue
                lookup.setdefault(name, os.path.join(dirpath, name))
    return lookup


def _candidate_scan_roots(manifest, manifest_path):
    """Candidate directories to search for source files in safe apply mode."""
    roots = []

    def _add(path_value):
        path_value = (path_value or "").strip()
        if not path_value:
            return
        norm = os.path.normpath(os.path.expanduser(path_value))
        if norm not in roots and os.path.isdir(norm):
            roots.append(norm)

    for out in manifest.get("outputs", []):
        out_path = (out.get("path") or "").strip()
        if not out_path:
            continue
        _add(os.path.dirname(out_path))
        _add(os.path.dirname(os.path.dirname(out_path)))

    _add(_get_default_output_dir_fallback())
    _add(os.path.dirname(manifest_path))

    return roots


def _resolve_apply_source(track, outputs, output_lookup, disk_lookup, index_fallback):
    """Resolve a source audio path for a manifest track."""
    track_num = _safe_track_number(track, index_fallback)
    output_name = (track.get("output_file") or track.get("unidentified_filename") or "").strip()

    if output_name and os.path.exists(output_name):
        return output_name, os.path.basename(output_name)

    if output_name:
        base = os.path.basename(output_name)
        from_manifest = output_lookup.get(base, "")
        if from_manifest and os.path.exists(from_manifest):
            return from_manifest, base
        from_disk = disk_lookup.get(base, "")
        if from_disk and os.path.exists(from_disk):
            return from_disk, base

    if 0 < track_num <= len(outputs):
        out_path = (outputs[track_num - 1].get("path") or "").strip()
        out_base = os.path.basename(out_path)
        if out_path and os.path.exists(out_path):
            return out_path, out_base
        if out_base:
            from_disk = disk_lookup.get(out_base, "")
            if from_disk and os.path.exists(from_disk):
                return from_disk, out_base

    return "", os.path.basename(output_name) if output_name else ""


def _build_safe_apply_plan(manifest, manifest_path, target_dir):
    """Create dry-run plan for safe session apply."""
    tracks = manifest.get("tracks", [])
    outputs = manifest.get("outputs", [])

    output_lookup = _collect_manifest_output_lookup(manifest)
    disk_lookup = _collect_disk_lookup(_candidate_scan_roots(manifest, manifest_path))

    plan = []
    missing = []
    conflicts = []
    already_present = []

    for idx, track in enumerate(tracks, 1):
        track_num = _safe_track_number(track, idx)
        status = (track.get("status") or "").strip().lower()
        src_path, src_name = _resolve_apply_source(track, outputs, output_lookup, disk_lookup, idx)
        src_ext = os.path.splitext(src_path or src_name)[1] or ".flac"

        if status == "unidentified":
            dest_dir = os.path.join(target_dir, "Unidentified Tracks")
            fallback = track.get("unidentified_filename") or src_name or f"Track_{track_num}_Unidentified{src_ext}"
            dest_name = os.path.basename(str(fallback)).translate(_UNSAFE_FS_CHARS) or f"Track_{track_num}_Unidentified{src_ext}"
        else:
            artist_norm = track.get("artist_normalization") if isinstance(track.get("artist_normalization"), dict) else {}
            canonical_artist = str(artist_norm.get("canonical_artist") or "").strip()
            artist = canonical_artist or (track.get("artist") or "Unknown").strip() or "Unknown"
            title = (track.get("title") or "Unknown").strip() or "Unknown"
            safe_artist = artist.translate(_UNSAFE_FS_CHARS) or "Unknown"
            safe_title = title.translate(_UNSAFE_FS_CHARS) or "Unknown"
            dest_dir = os.path.join(target_dir, safe_artist)
            dest_name = f"{safe_artist} - {safe_title}{src_ext}".translate(_UNSAFE_FS_CHARS)

        dest_path = os.path.join(dest_dir, dest_name)

        if not src_path:
            missing.append({
                "track_number": track_num,
                "expected": src_name or "(missing output_file)",
                "reason": "Could not find source file on this machine"
            })
            continue

        same_path = os.path.normcase(os.path.normpath(src_path)) == os.path.normcase(os.path.normpath(dest_path))
        if same_path:
            already_present.append({
                "track_number": track_num,
                "src_path": src_path,
                "dest_path": dest_path
            })
            continue

        if os.path.exists(dest_path):
            conflicts.append({
                "track_number": track_num,
                "src_path": src_path,
                "dest_path": dest_path,
                "reason": "Destination file already exists"
            })
            continue

        plan.append({
            "track_number": track_num,
            "status": status,
            "src_path": src_path,
            "dest_path": dest_path,
            "dest_name": dest_name
        })

    return {
        "plan": plan,
        "missing": missing,
        "conflicts": conflicts,
        "already_present": already_present,
        "total_tracks": len(tracks)
    }


def _save_applied_session_record(source_manifest, manifest_path, applied_paths_by_track, apply_meta=None):
    """Create and save a new session record for safe apply results."""
    now = datetime.now()
    source_session = source_manifest.get("session_name", "session")
    source_time = source_manifest.get("timestamp", "")

    updated = copy.deepcopy(source_manifest)
    updated["session_name"] = f"{source_session}_applied_{now.strftime('%Y%m%d_%H%M%S')}"
    updated["timestamp"] = now.isoformat()
    updated["mode"] = "session_apply_safe"
    updated["applied_from"] = {
        "session_name": source_session,
        "timestamp": source_time,
        "record_file": os.path.basename(manifest_path),
    }
    if isinstance(apply_meta, dict):
        updated["applied_from"].update(apply_meta)

    new_outputs = []
    for idx, track in enumerate(updated.get("tracks", []), 1):
        track_num = _safe_track_number(track, idx)
        new_path = applied_paths_by_track.get(track_num)
        if not new_path:
            continue
        track["output_file"] = os.path.basename(new_path)
        if track.get("status") == "unidentified":
            track["unidentified_filename"] = os.path.basename(new_path)
        out_entry = {"path": new_path}
        if os.path.exists(new_path):
            try:
                out_entry["size_bytes"] = os.path.getsize(new_path)
            except Exception:
                pass
        new_outputs.append(out_entry)

    updated["outputs"] = new_outputs
    return save_manifest(updated)


def _apply_session_record_safe(manifest, manifest_path):
    """Safely apply a session record by copying known output files."""
    default_target = _get_default_output_dir_fallback()
    print(f"\n{Style.CYAN}{'═'*60}")
    print(f"  Apply Session Record (Safe)")
    print(f"{'═'*60}{Style.RESET}")
    print(f"\n  Session: {manifest.get('session_name', 'unknown')}")
    print(f"  {Style.DIM}Safe mode: no overwrite, no delete, no source guessing beyond basename match.{Style.RESET}")
    print(f"  {Style.DIM}Tip: drag and drop a target folder path, or press Enter for default.{Style.RESET}")

    target_raw = input(f"\n  Target output folder [default: {default_target}]\n  → ").strip()
    target_dir = _normalize_import_path(target_raw) if target_raw else default_target
    target_dir = os.path.normpath(os.path.expanduser(target_dir))

    if os.path.isfile(target_dir):
        print(f"\n  {Style.RED}Target path is a file, not a folder.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        print(f"\n  {Style.RED}Could not create/access target folder: {exc}{Style.RESET}")
        input("  Press Enter to continue...")
        return

    preview = _build_safe_apply_plan(manifest, manifest_path, target_dir)
    planned = preview["plan"]
    missing = preview["missing"]
    conflicts = preview["conflicts"]
    already_present = preview["already_present"]

    print(f"\n  {Style.BOLD}Dry Run Summary{Style.RESET}")
    print(f"     Total tracks: {preview['total_tracks']}")
    print(f"     Ready to copy: {len(planned)}")
    print(f"     Already present: {len(already_present)}")
    print(f"     Missing source: {len(missing)}")
    print(f"     Conflicts: {len(conflicts)}")

    if planned:
        print(f"\n  {Style.BOLD}Preview (first {min(8, len(planned))}):{Style.RESET}")
        for row in planned[:8]:
            print(f"    Track {row['track_number']}: {os.path.basename(row['src_path'])}")
            print(f"      -> {row['dest_path']}")

    if missing:
        print(f"\n  {Style.YELLOW}Missing source files (first {min(8, len(missing))}):{Style.RESET}")
        for row in missing[:8]:
            print(f"    Track {row['track_number']}: expected {row['expected']}")

    if conflicts:
        print(f"\n  {Style.YELLOW}Destination conflicts (first {min(8, len(conflicts))}):{Style.RESET}")
        for row in conflicts[:8]:
            print(f"    Track {row['track_number']}: {row['dest_path']}")

    partial_mode = False
    if missing or conflicts:
        if not planned:
            print(f"\n  {Style.RED}Safe apply blocked. Resolve missing/conflicting files and retry.{Style.RESET}")
            input("  Press Enter to continue...")
            return
        print(f"\n  {Style.YELLOW}Safe mode found blocked tracks.{Style.RESET}")
        print(f"  {Style.DIM}Power user option can apply only resolvable tracks and skip blocked ones.{Style.RESET}")
        if not confirm_dialog("Power user: apply only resolvable tracks?", default=False):
            input("  Press Enter to continue...")
            return
        partial_mode = True

    if not planned:
        print(f"\n  {Style.DIM}Nothing to copy. Files may already be in target state.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    apply_prompt = "Apply resolvable tracks now?" if partial_mode else "Apply this session record now?"
    if not confirm_dialog(apply_prompt, default=False):
        return

    copied = []
    errors = []
    applied_paths_by_track = {}

    for row in planned:
        try:
            os.makedirs(os.path.dirname(row["dest_path"]), exist_ok=True)
            shutil.copy2(row["src_path"], row["dest_path"])
            copied.append(row)
            applied_paths_by_track[row["track_number"]] = row["dest_path"]
        except Exception as exc:
            errors.append(f"Track {row['track_number']}: {exc}")

    for row in already_present:
        applied_paths_by_track[row["track_number"]] = row["dest_path"]

    if copied or already_present:
        try:
            saved = _save_applied_session_record(
                manifest,
                manifest_path,
                applied_paths_by_track,
                apply_meta={
                    "partial_apply": partial_mode,
                    "target_dir": target_dir,
                    "copied_count": len(copied),
                    "already_present_count": len(already_present),
                    "missing_count": len(missing),
                    "conflict_count": len(conflicts),
                    "error_count": len(errors),
                },
            )
        except Exception:
            saved = None
    else:
        saved = None

    print(f"\n  {Style.GREEN}Apply complete.{Style.RESET}")
    if partial_mode:
        print(f"     Mode: Partial (power user)")
    print(f"     Copied: {len(copied)}")
    print(f"     Already present: {len(already_present)}")
    if partial_mode:
        print(f"     Skipped missing: {len(missing)}")
        print(f"     Skipped conflicts: {len(conflicts)}")
    if errors:
        print(f"     {Style.YELLOW}Errors: {len(errors)}{Style.RESET}")
        for err in errors[:8]:
            print(f"       • {Style.DIM}{err}{Style.RESET}")
    if saved:
        print(f"     New session record: {Style.DIM}{saved}{Style.RESET}")

    input("  Press Enter to continue...")


def _reorganize_session(manifest, reorganize_fn):
    """Preview and optionally apply file reorganization for a session."""
    config = get_config()
    norm_on = config.get('normalize_artists', True)
    norm_label = f"{Style.GREEN}ON{Style.RESET}" if norm_on else f"{Style.YELLOW}OFF{Style.RESET}"

    print(f"\n{Style.CYAN}{'═'*60}")
    print(f"  Reorganize Files")
    print(f"{'═'*60}{Style.RESET}")
    print(f"\n  Session:   {manifest.get('session_name', 'unknown')}")
    print(f"  Tracks:    {manifest.get('summary', {}).get('total_tracks', '?')}")
    print(f"  Normalize: {norm_label}")
    if norm_on:
        print(f"  {Style.DIM}  Collabs will be moved to title, one folder per primary artist{Style.RESET}")
    else:
        print(f"  {Style.DIM}  Full collaboration credits kept in artist name and folders{Style.RESET}")

    # Dry run to show what would change
    preview = reorganize_fn(manifest, dry_run=True)

    if not preview["changes"]:
        print(f"\n  {Style.GREEN}✓ All files already match current settings — nothing to change.{Style.RESET}")
        if preview["errors"]:
            print(f"\n  {Style.YELLOW}⚠ {len(preview['errors'])} file(s) could not be found:{Style.RESET}")
            for err in preview["errors"][:5]:
                print(f"    {Style.DIM}{err}{Style.RESET}")
        input(f"\n  Press Enter to continue...")
        return

    # Show preview
    print(f"\n  {Style.BOLD}{len(preview['changes'])} file(s) would be renamed:{Style.RESET}\n")
    for i, c in enumerate(preview["changes"][:15], 1):
        print(f"  {Style.DIM}{i:3}.{Style.RESET} {c['old_name']}")
        print(f"       → {Style.GREEN}{c['new_name']}{Style.RESET}")

    if len(preview["changes"]) > 15:
        print(f"       {Style.DIM}... and {len(preview['changes']) - 15} more{Style.RESET}")

    if preview["cleaned"]:
        print(f"\n  {Style.DIM}{len(preview['cleaned'])} empty folder(s) would be removed{Style.RESET}")

    if preview["errors"]:
        print(f"\n  {Style.YELLOW}⚠ {len(preview['errors'])} file(s) skipped (not found on disk){Style.RESET}")

    # Ask for confirmation
    print(f"\n  {Style.BOLD}This will rename files and update their tags.{Style.RESET}")
    if not confirm_dialog("Apply these changes?"):
        print(f"\n  {Style.DIM}Cancelled — no files were changed.{Style.RESET}")
        input(f"\n  Press Enter to continue...")
        return

    # Execute for real
    results = reorganize_fn(manifest, dry_run=False)

    moved = len(results["changes"])
    cleaned = len(results["cleaned"])
    errors = len(results["errors"])

    print(f"\n  {Style.GREEN}✅ Done!{Style.RESET}")
    print(f"     {Style.GREEN}{moved} file(s) renamed and re-tagged{Style.RESET}")
    if cleaned:
        print(f"     {Style.DIM}{cleaned} empty folder(s) cleaned up{Style.RESET}")
    if errors:
        print(f"     {Style.YELLOW}{errors} issue(s):{Style.RESET}")
        for err in results["errors"][:5]:
            print(f"       {Style.DIM}{err}{Style.RESET}")

    input(f"\n  Press Enter to continue...")


def _display_manifest_details(manifest):
    """Display detailed manifest info"""
    print(f"\n{Style.CYAN}{'═'*60}")
    print(f"  Manifest Details")
    print(f"{'═'*60}{Style.RESET}")

    print(f"\n  Session:  {manifest['session_name']}")
    print(f"  Date:     {manifest['timestamp'][:19].replace('T', ' ')}")
    print(f"  Mode:     {manifest['mode']}")
    print(f"  Version:  {manifest.get('version', 'unknown')}")

    print(f"\n  Input:    {manifest['input']['file']}")

    summary = manifest.get('summary', {})
    print(f"\n  Tracks:   {summary.get('total_tracks', 0)} total")
    print(f"            {summary.get('identified', 0)} identified")
    print(f"            {summary.get('manual', 0)} manual")
    print(f"            {summary.get('skipped', 0)} skipped")

    print(f"\n  Outputs:  {len(manifest.get('outputs', []))} files")

    if confirm_dialog("Show track details?"):
        print(f"\n{Style.CYAN}{'─'*60}{Style.RESET}")
        for track in manifest.get('tracks', [])[:20]:
            print(f"  {track['track_number']:2}. {track['artist']} - {track['title']}")
            if track.get('album'):
                print(f"      {Style.DIM}{track['album']}{Style.RESET}")

    input(f"\n  Press Enter to continue...")


def _display_manifest_comparison(m1, m2):
    """Display comparison between two manifests"""
    diff = compare_manifests(m1, m2)

    print(f"\n{Style.CYAN}{'═'*60}")
    print(f"  Manifest Comparison")
    print(f"{'═'*60}{Style.RESET}")

    print(f"\n  {Style.BOLD}{m1['session_name']}{Style.RESET} vs {Style.BOLD}{m2['session_name']}{Style.RESET}")

    if diff['metadata_changes'] == 0:
        print(f"\n  {Style.GREEN}✓ No metadata changes{Style.RESET}")
    else:
        print(f"\n  {Style.YELLOW}⚠ {diff['metadata_changes']} tracks changed:{Style.RESET}")
        for change in diff['tracks_changed'][:10]:
            print(f"    Track {change['track_number']}:")
            print(f"      Old: {Style.DIM}{change['old']}{Style.RESET}")
            print(f"      New: {Style.GREEN}{change['new']}{Style.RESET}")

    if diff['files_added']:
        print(f"\n  {Style.GREEN}+ {len(diff['files_added'])} files added{Style.RESET}")

    if diff['files_removed']:
        print(f"\n  {Style.RED}- {len(diff['files_removed'])} files removed{Style.RESET}")

    input(f"\n  Press Enter to continue...")


def _preview_rollback(manifest):
    """Preview what rollback would do"""
    print(f"\n{Style.YELLOW}{'═'*60}")
    print(f"  Rollback Preview (Dry Run)")
    print(f"{'═'*60}{Style.RESET}")

    print(f"\n  {Style.BOLD}This would restore:{Style.RESET}")
    print(f"  Session: {manifest['session_name']}")
    print(f"  Date: {manifest['timestamp'][:19].replace('T', ' ')}")

    results = rollback_from_manifest(manifest, dry_run=True)

    print(f"\n  {Style.GREEN}Manifest says these files should exist:{Style.RESET}")
    for filepath in results['manifest_files'][:10]:
        exists = "✓" if os.path.exists(filepath) else "✗"
        print(f"    {exists} {os.path.basename(filepath)}")

    would_delete = results.get('would_delete', [])
    if would_delete:
        print(f"\n  {Style.RED}Would delete (not in manifest):{Style.RESET}")
        for filepath in would_delete[:10]:
            print(f"    - {os.path.basename(filepath)}")
        if len(would_delete) > 10:
            print(f"    {Style.DIM}... and {len(would_delete) - 10} more{Style.RESET}")
    else:
        print(f"\n  {Style.GREEN}✓ No extra files detected for this rollback scope.{Style.RESET}")

    print(f"\n  {Style.DIM}Rollback can only remove extra files in scope; it cannot recreate missing audio files.{Style.RESET}")

    if not would_delete:
        input(f"\n  Press Enter to continue...")
        return

    print(f"\n  {Style.BOLD}Apply rollback now?{Style.RESET}")
    print(f"  {Style.RED}This will permanently delete {len(would_delete)} file(s).{Style.RESET}")
    if not confirm_dialog("Proceed with rollback delete?", default=False):
        input(f"\n  Press Enter to continue...")
        return

    applied = rollback_from_manifest(manifest, dry_run=False)
    deleted = len(applied.get("deleted", []))
    errors = applied.get("errors", [])
    rollback_record_path = _save_rollback_session_record(manifest, applied)

    print(f"\n  {Style.GREEN}✅ Rollback applied.{Style.RESET}")
    print(f"     Deleted: {deleted} file(s)")
    if errors:
        print(f"     {Style.YELLOW}Issues: {len(errors)}{Style.RESET}")
        for err in errors[:8]:
            print(f"       • {Style.DIM}{err}{Style.RESET}")
    if rollback_record_path:
        print(f"     Rollback record: {Style.DIM}{rollback_record_path}{Style.RESET}")
    else:
        print(f"     {Style.YELLOW}Could not save rollback session record.{Style.RESET}")

    input(f"\n  Press Enter to continue...")


def _save_rollback_session_record(source_manifest, rollback_results):
    """Persist rollback apply results as a new session record."""
    try:
        now = datetime.now()
        rollback_manifest = copy.deepcopy(source_manifest)
        source_session = source_manifest.get("session_name", "session")
        source_time = source_manifest.get("timestamp", "")
        rollback_manifest["session_name"] = f"{source_session}_rollback_{now.strftime('%Y%m%d_%H%M%S')}"
        rollback_manifest["timestamp"] = now.isoformat()
        rollback_manifest["mode"] = "rollback"
        rollback_manifest["rollback"] = {
            "source_session_name": source_session,
            "source_timestamp": source_time,
            "deleted_count": len(rollback_results.get("deleted", [])),
            "error_count": len(rollback_results.get("errors", [])),
        }
        filename = f"rollback_{now.strftime('%Y%m%d_%H%M%S')}.json"
        return save_manifest(rollback_manifest, filename=filename)
    except Exception:
        return None


def _is_safe_session_record_path(filepath):
    """Allow deletion only for JSON records inside the manifest directory."""
    try:
        if not filepath:
            return False
        target = os.path.realpath(str(filepath))
        manifest_dir = os.path.realpath(str(get_manifest_dir()))
        if not target.lower().endswith(".json"):
            return False
        return os.path.commonpath([manifest_dir, target]) == manifest_dir
    except Exception:
        return False


def _delete_session_record(manifest_row):
    """Delete a manifest/session-history record from disk."""
    filepath = manifest_row.get("filepath")
    session_name = manifest_row.get("session_name", "unknown session")
    filename = manifest_row.get("filename", os.path.basename(filepath or ""))

    if not filepath:
        print(f"\n  {Style.RED}Could not resolve session record path.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    if not _is_safe_session_record_path(filepath):
        print(f"\n  {Style.RED}Delete blocked by safety rule.{Style.RESET}")
        print(f"  {Style.DIM}Only .json session records inside the Session History folder can be deleted.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    print(f"\n{Style.RED}{'═'*60}")
    print(f"  Delete Session Record")
    print(f"{'═'*60}{Style.RESET}")
    print(f"\n  Session:  {session_name}")
    print(f"  File:     {filename}")
    print(f"  Path:     {Style.DIM}{filepath}{Style.RESET}")
    print(f"\n  {Style.DIM}This removes only the session-history JSON record, not exported audio files.{Style.RESET}")

    if not os.path.exists(filepath):
        print(f"\n  {Style.YELLOW}Record file is already missing.{Style.RESET}")
        input("  Press Enter to continue...")
        return

    if not confirm_dialog("Delete this session record?", default=False):
        return

    try:
        os.remove(filepath)
        print(f"\n  {Style.GREEN}✅ Session record deleted.{Style.RESET}")
    except Exception as exc:
        print(f"\n  {Style.RED}Delete failed: {exc}{Style.RESET}")

    input("  Press Enter to continue...")
