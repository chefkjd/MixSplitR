"""
mixsplitr_editor.py - Cache management and interactive editor for MixSplitR

Contains:
- Preview cache save/load
- Human-readable metadata export
- Text file sync for edits
- Interactive terminal-based editor with audio preview
- Preview table display
"""

import os
import re
import json
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from mixsplitr_core import Style
from mixsplitr_identify import (
    is_musicbrainz_available,
    musicbrainz_search_recordings,
    musicbrainz_search_releases,
    musicbrainz_get_release_tracklist,
    get_enhanced_metadata,
)
from mixsplitr_tracklist import (
    parse_tracklist,
    match_tracklist_to_tracks,
    format_tracklist_preview
)
from mixsplitr_menu import select_menu, MenuItem, MenuResult, confirm_dialog, input_dialog, wait_for_enter



# =============================================================================
# AUDIO PREVIEW
# =============================================================================

def _play_preview_subprocess(cmd, stop_event=None, capture_output=False):
    stdout_target = subprocess.DEVNULL if capture_output else None
    stderr_target = subprocess.DEVNULL if capture_output else None
    proc = subprocess.Popen(cmd, stdout=stdout_target, stderr=stderr_target)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=0.8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return True
            return_code = proc.poll()
            if return_code is not None:
                return return_code == 0
            time.sleep(0.05)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def play_audio_preview(file_path, duration_seconds=30, show_status=True, stop_event=None):
    """
    Play a preview of an audio file using system player.

    Args:
        file_path: Path to audio file
        duration_seconds: How many seconds to play (default 30)
        show_status: Whether to print built-in playback status lines
        stop_event: Optional threading.Event used to stop playback early

    Returns:
        True if playback started, False if failed
    """
    if not file_path or not os.path.exists(file_path):
        print(f"  ⚠️  Audio file not found: {file_path}")
        return False

    try:
        system = platform.system()

        if system == 'Darwin':  # macOS
            # Use afplay (built-in)
            if show_status:
                print(f"  🔊 Playing preview... (Press Ctrl+C to stop)")
            return _play_preview_subprocess(
                ['afplay', '-t', str(duration_seconds), file_path],
                stop_event=stop_event,
                capture_output=False,
            )
        elif system == 'Windows':
            # Use Windows Media Player or PowerShell
            if show_status:
                print(f"  🔊 Playing preview... (Close player window to continue)")
            # Try to use ffplay if available, otherwise fall back to default player
            ffplay_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else '.', 'ffplay.exe')
            if os.path.exists(ffplay_path):
                return _play_preview_subprocess(
                    [ffplay_path, '-nodisp', '-autoexit', '-t', str(duration_seconds), file_path],
                    stop_event=stop_event,
                    capture_output=True,
                )
            else:
                # Open with default player
                os.startfile(file_path)
                return True
        else:  # Linux
            # Try common players
            for player in ['ffplay', 'aplay', 'paplay', 'mpv', 'mplayer']:
                if subprocess.run(['which', player], capture_output=True).returncode == 0:
                    if show_status:
                        print(f"  🔊 Playing preview... (Press Ctrl+C to stop)")
                    if player == 'ffplay':
                        return _play_preview_subprocess(
                            [player, '-nodisp', '-autoexit', '-t', str(duration_seconds), file_path],
                            stop_event=stop_event,
                            capture_output=True,
                        )
                    else:
                        return _play_preview_subprocess(
                            [player, file_path],
                            stop_event=stop_event,
                            capture_output=True,
                        )
                    break

        return False
    except KeyboardInterrupt:
        if show_status:
            print("\n  ⏹️  Playback stopped")
        return True
    except Exception as e:
        print(f"  ⚠️  Could not play audio: {e}")
        return False


# =============================================================================
# DISPLAY HELPERS
# =============================================================================

def format_track_for_display(track):
    """Create a human-readable string representation of a track's metadata"""
    if track.get('status') != 'identified':
        return None

    rm = track.get('readable_metadata', {})
    em = track.get('enhanced_metadata', {})
    lines = []

    # Artist
    artist = rm.get('artist', {})
    artist_val = artist.get('value') or track.get('artist', '')
    source = f"({artist.get('source', '')})" if artist.get('source') else ""
    lines.append(f"Artist: {artist_val:<30} {source}")

    # Title
    title = rm.get('title', {})
    title_val = title.get('value') or track.get('title', '')
    source = f"({title.get('source', '')})" if title.get('source') else ""
    lines.append(f"Title:  {title_val:<30} {source}")

    # Album
    album = rm.get('album', {})
    album_val = album.get('value') or track.get('album', '')
    if album_val and album_val != 'Unknown Album':
        source = f"({album.get('source', '')})" if album.get('source') else ""
        lines.append(f"Album:  {album_val:<30} {source}")
    else:
        lines.append(f"Album:  _a")

    # Year
    year = rm.get('year', {})
    year_val = year.get('value') or em.get('release_date', '')
    if year_val:
        if len(year_val) >= 4:
            year_val = year_val[:4]
        source = f"({year.get('source', '')})" if year.get('source') else ""
        lines.append(f"Year:   {year_val:<30} {source}")
    else:
        lines.append(f"Year:   _y")

    # Genres
    genres = rm.get('genres', {})
    genre_val = genres.get('value') or em.get('genres', [])
    if genre_val and len(genre_val) > 0:
        genre_str = ", ".join(genre_val[:5])
        source = f"({genres.get('source', '')})" if genres.get('source') else ""
        lines.append(f"Genre:  {genre_str:<30} {source}")
    else:
        lines.append(f"Genre:  _g")

    # ISRC
    isrc = rm.get('isrc', {})
    isrc_val = isrc.get('value') or em.get('isrc', '')
    if isrc_val:
        source = f"({isrc.get('source', '')})" if isrc.get('source') else ""
        lines.append(f"ISRC:   {isrc_val:<30} {source}")

    # BPM
    bpm = rm.get('bpm', {})
    bpm_val = bpm.get('value') or em.get('bpm')
    if bpm_val and bpm_val > 0:
        source = f"({bpm.get('source', '')})" if bpm.get('source') else ""
        lines.append(f"BPM:    {bpm_val:<30} {source}")
    else:
        lines.append(f"BPM:    _b")

    # Confidence
    confidence = rm.get('confidence', 0)
    agreement = rm.get('agreement', 'unknown')

    agreement_text = {
        'full': 'ACR + MB agree',
        'partial': 'Partial agreement',
        'conflict': 'Sources conflict',
        'acr_only': 'ACRCloud only',
        'mb_only': 'MusicBrainz only',
        'none': 'No identification'
    }.get(agreement, agreement)

    lines.append(f"Confidence: {confidence:.2f} ({agreement_text})")

    return "\n".join(lines)


# =============================================================================
# CACHE SAVE/LOAD
# =============================================================================

def save_preview_cache(cache_data, cache_path="mixsplittr_cache.json"):
    """Save processing results to cache for preview/apply workflow"""
    print(f"\n💾 Saving Preview Session data to {cache_path}...")

    track_count = len(cache_data.get('tracks', []))
    artwork_count = len(cache_data.get('artwork_cache', {}))
    print(f"   📊 Tracks: {track_count}, Artworks: {artwork_count}")

    readable_path = str(cache_path).replace('.json', '_readable.txt')

    try:
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        # Create human-readable metadata file
        with open(readable_path, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("MixSplitR Track Metadata\n")
            f.write("=" * 70 + "\n\n")

            identified = [t for t in cache_data.get('tracks', []) if t.get('status') == 'identified']
            unidentified = [t for t in cache_data.get('tracks', []) if t.get('status') == 'unidentified']

            # Count missing fields
            missing_genre = [(i, t) for i, t in enumerate(identified, 1) if not t.get('enhanced_metadata', {}).get('genres')]
            missing_year = [(i, t) for i, t in enumerate(identified, 1) if not t.get('enhanced_metadata', {}).get('release_date')]
            missing_album = [(i, t) for i, t in enumerate(identified, 1) if not t.get('album') or t.get('album') == 'Unknown Album']
            missing_bpm = [(i, t) for i, t in enumerate(identified, 1) if not t.get('enhanced_metadata', {}).get('bpm')]

            f.write(f"Total: {len(identified)} identified, {len(unidentified)} unidentified\n")
            f.write("-" * 70 + "\n\n")

            has_missing = any([missing_genre, missing_year, missing_album, missing_bpm])

            if has_missing:
                f.write("=" * 70 + "\n")
                f.write("QUICK EDIT\n")
                f.write("=" * 70 + "\n\n")
                f.write("Ctrl+F → type marker → Enter → type value → F3 (next) → Ctrl+S (save)\n")
                f.write("Markers: _g = genre, _y = year, _a = album, _b = bpm\n")
                f.write("-" * 70 + "\n\n")

                for field_name, missing_list, marker in [
                    ("GENRE", missing_genre, "_g"),
                    ("YEAR", missing_year, "_y"),
                    ("ALBUM", missing_album, "_a"),
                    ("BPM", missing_bpm, "_b")
                ]:
                    if missing_list:
                        f.write(f"{field_name} ({len(missing_list)} missing)\n")
                        for track_num, track in missing_list:
                            artist = track.get('artist', 'Unknown')[:20]
                            title = track.get('title', 'Unknown')[:20]
                            track_info = f"Track {track_num}: {artist} - {title}"
                            f.write(f"{track_info:<50} {field_name.capitalize()}: {marker}\n")
                        f.write("\n")

                f.write("=" * 70 + "\n\n")

            # Full track details
            f.write("FULL TRACK DETAILS\n")
            f.write("=" * 70 + "\n\n")

            for i, track in enumerate(identified, 1):
                f.write(f"Track {i}:\n")
                f.write("-" * 40 + "\n")

                readable = format_track_for_display(track)
                if readable:
                    f.write(readable + "\n")

                f.write("\n")

            if unidentified:
                f.write("\n" + "=" * 70 + "\n")
                f.write("UNIDENTIFIED TRACKS\n")
                f.write("=" * 70 + "\n\n")
                for track in unidentified:
                    filename = track.get('unidentified_filename', 'Unknown')
                    bpm = track.get('detected_bpm')
                    if bpm:
                        confidence = track.get('bpm_confidence', 0)
                        f.write(f"- {filename} (BPM: ~{bpm}, confidence: {confidence:.0%})\n")
                    else:
                        f.write(f"- {filename}\n")

        # Clear macOS extended attributes
        if platform.system() == 'Darwin':
            try:
                subprocess.run(['xattr', '-c', cache_path], check=False, capture_output=True)
                subprocess.run(['xattr', '-c', readable_path], check=False, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass

        file_size_mb = os.path.getsize(cache_path) / (1024 * 1024)
        print(f"✅ Preview Session data saved! Size: {file_size_mb:.1f} MB")
        print(f"📄 Human-readable version: {readable_path}")
        return True
    except Exception as e:
        print(f"❌ Error saving Preview Session data: {e}")
        return False


def load_preview_cache(cache_path="mixsplitr_cache.json"):
    """Load cached processing results"""
    if not os.path.exists(cache_path):
        print(f"❌ Preview Session file not found: {cache_path}")
        return None

    try:
        with open(cache_path, 'r') as f:
            cache_data = json.load(f)
        print(f"✅ Loaded Preview Session with {len(cache_data.get('tracks', []))} tracks")

        readable_path = str(cache_path).replace('.json', '_readable.txt')
        if os.path.exists(readable_path):
            cache_data = sync_edits_from_txt(cache_data, readable_path, cache_path)

        return cache_data
    except Exception as e:
        print(f"❌ Error loading Preview Session data: {e}")
        return None


# =============================================================================
# TEXT FILE SYNC
# =============================================================================

def sync_edits_from_txt(cache_data, txt_path, json_path):
    """Parse edits from the human-readable txt file and sync to JSON cache"""
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            txt_content = f.read()

        tracks = cache_data.get('tracks', [])
        identified_tracks = [t for t in tracks if t.get('status') == 'identified']

        if not identified_tracks:
            return cache_data

        changes_made = 0

        # Parse Quick Edit section
        quick_edit_changes = parse_quick_edit_section(txt_content, identified_tracks)

        for track_idx, field, value in quick_edit_changes:
            if track_idx < len(identified_tracks):
                track = identified_tracks[track_idx]
                if apply_single_field_edit(track, field, value):
                    changes_made += 1

        # Parse Full Track Details section
        track_sections = re.split(r'\nTrack (\d+):\n', txt_content)

        for i in range(1, len(track_sections) - 1, 2):
            track_num = int(track_sections[i])
            track_content = track_sections[i + 1]

            track_idx = track_num - 1
            if track_idx >= len(identified_tracks):
                continue

            track = identified_tracks[track_idx]
            edits = parse_track_fields(track_content)

            if apply_track_edits(track, edits):
                changes_made += 1

        if changes_made > 0:
            print(f"📝 Applied edits from {os.path.basename(txt_path)} to {changes_made} tracks")
            with open(json_path, 'w') as f:
                json.dump(cache_data, f)
            print(f"✅ Saved updated Preview Session data")

        return cache_data

    except Exception as e:
        print(f"⚠️  Could not sync edits from txt file: {e}")
        return cache_data


def parse_quick_edit_section(content, identified_tracks):
    """Parse the Quick Edit section for filled-in values"""
    edits = []

    quick_edit_match = re.search(r'QUICK EDIT.*?(?=FULL TRACK DETAILS|$)', content, re.DOTALL)
    if not quick_edit_match:
        return edits

    quick_section = quick_edit_match.group(0)
    pattern = r'Track (\d+):[^\n]+\n(Genre|Year|Album|BPM):\s*(.+?)(?=\n\n|\nTrack|\n[A-Z]|$)'

    for match in re.finditer(pattern, quick_section, re.IGNORECASE):
        track_num = int(match.group(1))
        field = match.group(2).lower()
        value = match.group(3).strip()

        if not value or value in ['_g', '_y', '_a', '_b', '']:
            continue

        track_idx = track_num - 1
        edits.append((track_idx, field, value))

    return edits


def apply_single_field_edit(track, field, value):
    """Apply a single field edit to a track"""
    if not value:
        return False

    changes = False

    if 'enhanced_metadata' not in track:
        track['enhanced_metadata'] = {}

    if field == 'genre':
        genres = [g.strip() for g in value.split(',') if g.strip()]
        if genres:
            track['enhanced_metadata']['genres'] = genres
            if 'readable_metadata' in track:
                if 'genres' not in track['readable_metadata']:
                    track['readable_metadata']['genres'] = {}
                track['readable_metadata']['genres']['value'] = genres
                track['readable_metadata']['genres']['source'] = 'User Edit'
            changes = True

    elif field == 'year':
        track['enhanced_metadata']['release_date'] = value
        if 'readable_metadata' in track:
            if 'year' not in track['readable_metadata']:
                track['readable_metadata']['year'] = {}
            track['readable_metadata']['year']['value'] = value
            track['readable_metadata']['year']['source'] = 'User Edit'
        changes = True

    elif field == 'album':
        track['album'] = value
        if 'readable_metadata' in track and 'album' in track['readable_metadata']:
            track['readable_metadata']['album']['value'] = value
            track['readable_metadata']['album']['source'] = 'User Edit'
        changes = True

    elif field == 'bpm':
        try:
            bpm_val = int(value)
            track['enhanced_metadata']['bpm'] = bpm_val
            if 'readable_metadata' in track:
                if 'bpm' not in track['readable_metadata']:
                    track['readable_metadata']['bpm'] = {}
                track['readable_metadata']['bpm']['value'] = bpm_val
                track['readable_metadata']['bpm']['source'] = 'User Edit'
            changes = True
        except (ValueError, TypeError):
            pass

    return changes


def parse_track_fields(content):
    """Parse field values from a track section in the txt file"""
    edits = {}

    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('-'):
            continue

        if ':' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                field = parts[0].strip().lower()
                value = parts[1].strip()

                value = re.sub(r'\s*\([^)]+\)\s*$', '', value).strip()

                if not value or value in ['_g', '_y', '_a', '_b']:
                    continue

                if field == 'confidence':
                    continue

                edits[field] = value

    return edits


def apply_track_edits(track, edits):
    """Apply parsed edits to a track"""
    changes = False

    direct_fields = ['artist', 'title', 'album']
    for field in direct_fields:
        if field in edits:
            old_val = track.get(field, '')
            new_val = edits[field]
            if old_val != new_val:
                track[field] = new_val
                if 'readable_metadata' in track and field in track['readable_metadata']:
                    track['readable_metadata'][field]['value'] = new_val
                    track['readable_metadata'][field]['source'] = 'User Edit'
                changes = True

    if 'enhanced_metadata' not in track:
        track['enhanced_metadata'] = {}

    if 'genre' in edits or 'genres' in edits:
        genre_str = edits.get('genre') or edits.get('genres', '')
        genres = [g.strip() for g in genre_str.split(',') if g.strip()]
        old_genres = track['enhanced_metadata'].get('genres', [])
        if genres != old_genres:
            if genres:
                track['enhanced_metadata']['genres'] = genres
            else:
                track['enhanced_metadata'].pop('genres', None)
            if 'readable_metadata' in track:
                if 'genres' not in track['readable_metadata']:
                    track['readable_metadata']['genres'] = {}
                track['readable_metadata']['genres']['value'] = genres
                track['readable_metadata']['genres']['source'] = 'User Edit'
            changes = True

    if 'year' in edits:
        old_year = track['enhanced_metadata'].get('release_date', '')
        if edits['year'] != old_year:
            if edits['year']:
                track['enhanced_metadata']['release_date'] = edits['year']
            else:
                track['enhanced_metadata'].pop('release_date', None)
            if 'readable_metadata' in track:
                if 'year' not in track['readable_metadata']:
                    track['readable_metadata']['year'] = {}
                track['readable_metadata']['year']['value'] = edits['year']
                track['readable_metadata']['year']['source'] = 'User Edit'
            changes = True

    if 'isrc' in edits:
        old_isrc = track['enhanced_metadata'].get('isrc', '')
        if edits['isrc'] != old_isrc:
            if edits['isrc']:
                track['enhanced_metadata']['isrc'] = edits['isrc']
            else:
                track['enhanced_metadata'].pop('isrc', None)
            changes = True

    if 'bpm' in edits:
        if str(edits['bpm']).strip():
            try:
                new_bpm = int(edits['bpm'])
                old_bpm = track['enhanced_metadata'].get('bpm', 0)
                if new_bpm != old_bpm:
                    track['enhanced_metadata']['bpm'] = new_bpm
                    if 'readable_metadata' in track:
                        if 'bpm' not in track['readable_metadata']:
                            track['readable_metadata']['bpm'] = {}
                        track['readable_metadata']['bpm']['value'] = new_bpm
                        track['readable_metadata']['bpm']['source'] = 'User Edit'
                    changes = True
            except (ValueError, TypeError):
                pass
        elif 'bpm' in track['enhanced_metadata']:
            track['enhanced_metadata'].pop('bpm', None)
            if 'readable_metadata' in track and 'bpm' in track['readable_metadata']:
                track['readable_metadata']['bpm']['value'] = ''
                track['readable_metadata']['bpm']['source'] = 'User Edit'
            changes = True

    if 'artist' in edits or 'title' in edits:
        artist = track.get('artist', 'Unknown')
        title = track.get('title', 'Unknown')
        if artist and title:
            track['expected_filename'] = f"{artist} - {title}.flac".translate(str.maketrans('', '', '<>:"/\\|?*'))
        else:
            track.pop('expected_filename', None)

    if 'filename' in edits:
        filename = str(edits.get('filename') or '').strip()
        track_status = str(track.get('status') or '')
        target_key = 'expected_filename' if track_status == 'identified' else 'unidentified_filename'

        if filename:
            safe_filename = str(os.path.basename(filename)).strip().translate(str.maketrans('', '', '<>:"/\\|?*'))
            if safe_filename:
                filename_base = os.path.splitext(safe_filename)[0].strip()
                if str(track.get(target_key) or '').strip() != safe_filename:
                    track[target_key] = safe_filename
                    changes = True
                if filename_base:
                    if 'enhanced_metadata' not in track:
                        track['enhanced_metadata'] = {}
                    track['enhanced_metadata']['output_filename_base'] = filename_base
        elif "output_filename_base" in track.get("enhanced_metadata", {}):
            track["enhanced_metadata"].pop("output_filename_base", None)

    return changes


# =============================================================================
# INTERACTIVE EDITOR
# =============================================================================

def interactive_editor(cache_data, cache_path):
    """Interactive terminal-based editor for preview cache"""
    tracks = cache_data.get('tracks', [])
    config_snapshot = cache_data.get('config_snapshot', {}) if isinstance(cache_data, dict) else {}
    session_editor_mode = bool((config_snapshot or {}).get('session_editor', False))
    if not tracks:
        print("No tracks to edit.")
        return 'quit'

    changes_made = False
    current_filter = 'all'
    page = 0
    page_size = 15

    def get_filtered_tracks():
        if current_filter == 'all':
            return [(i, t) for i, t in enumerate(tracks)]
        elif current_filter == 'identified':
            return [(i, t) for i, t in enumerate(tracks) if t.get('status') == 'identified']
        elif current_filter == 'unidentified':
            return [(i, t) for i, t in enumerate(tracks) if t.get('status') == 'unidentified']
        elif current_filter == 'no_genre':
            return [(i, t) for i, t in enumerate(tracks)
                    if t.get('status') == 'identified' and not t.get('enhanced_metadata', {}).get('genres')]
        elif current_filter == 'no_bpm':
            return [(i, t) for i, t in enumerate(tracks)
                    if t.get('status') == 'identified' and not t.get('enhanced_metadata', {}).get('bpm')]
        return [(i, t) for i, t in enumerate(tracks)]

    def get_stats():
        identified = len([t for t in tracks if t.get('status') == 'identified'])
        unidentified = len([t for t in tracks if t.get('status') == 'unidentified'])
        skipped = len([t for t in tracks if t.get('status') == 'skipped'])
        no_genre = len([t for t in tracks if t.get('status') == 'identified'
                        and not t.get('enhanced_metadata', {}).get('genres')])
        no_bpm = len([t for t in tracks if t.get('status') == 'identified'
                      and not t.get('enhanced_metadata', {}).get('bpm')])
        return identified, unidentified, skipped, no_genre, no_bpm


    def _build_track_display(filtered, pg, pg_size, filt, all_tracks):
        """Build track listing for menu header.
        Returns (header_lines, fallback_header)."""
        start = pg * pg_size
        end = min(start + pg_size, len(filtered))
        total_pgs = max(1, (len(filtered) + pg_size - 1) // pg_size)

        filter_names = {
            'all': 'All Tracks', 'identified': 'Identified Only',
            'unidentified': 'Unidentified Only', 'no_genre': 'Missing Genre',
            'no_bpm': 'Missing BPM'
        }

        text_lines = []
        text_lines.append(f"  {'─'*58}")
        text_lines.append(f"  {filter_names.get(filt, filt)} ({len(filtered)} tracks)  •  Page {pg+1}/{total_pgs}")
        text_lines.append(f"  {'─'*58}")

        if not filtered:
            text_lines.append("  No tracks match this filter.")
        else:
            for orig_idx, track in filtered[start:end]:
                status = track.get('status', 'unknown')
                if status == 'identified':
                    artist = track.get('artist', 'Unknown')[:18]
                    title = track.get('title', 'Unknown')[:22]
                    genres = track.get('enhanced_metadata', {}).get('genres', [])
                    bpm = track.get('enhanced_metadata', {}).get('bpm', '')
                    genre_str = genres[0][:10] if genres else '---'
                    bpm_str = str(bpm) if bpm else '---'
                    text_lines.append(f"  {orig_idx+1:3}. ✅ {artist:<18} - {title:<22} G:{genre_str:<10} B:{bpm_str}")
                elif status == 'unidentified':
                    filename = track.get('unidentified_filename', 'Unknown')[:40]
                    bpm = track.get('detected_bpm', '')
                    bpm_str = f"~{bpm}" if bpm else '---'
                    text_lines.append(f"  {orig_idx+1:3}. ❓ {filename:<40} BPM:{bpm_str}")
                else:
                    text_lines.append(f"  {orig_idx+1:3}. ⏭️  Skipped")

        text_lines.append(f"  {'─'*58}")

        header_lines = [('', line + '\n') for line in text_lines]
        fallback_header = '\n'.join(text_lines) + '\n'

        return header_lines, fallback_header

    # -------------------------------------------------------------------------
    # MusicBrainz assist (text search) for unidentified tracks
    # -------------------------------------------------------------------------
    MB_ASSIST_APPLIED = "applied"
    MB_ASSIST_CANCELLED = "cancelled"

    def _mb_assist_track(track, track_num):
        """Text-search MusicBrainz and apply a selected match to this track."""
        nonlocal changes_made

        if not is_musicbrainz_available():
            print(f"  {Style.YELLOW}⚠️  MusicBrainz support not available (missing musicbrainzngs).{Style.RESET}")
            wait_for_enter()
            return MB_ASSIST_CANCELLED

        temp_chunk_path = track.get('temp_chunk_path', '') or ''
        default_query = (track.get('unidentified_filename') or '').replace('_', ' ').replace('-', ' ').strip()

        while True:
            # Build initial action menu
            preview_available = bool(temp_chunk_path and os.path.exists(temp_chunk_path))
            mb_items = []
            if preview_available:
                mb_items.append(MenuItem("preview", "🎧", "Play Audio Preview", "Listen to identify the track"))
            mb_items.append(MenuItem("search", "🔎", "Search MusicBrainz", "Find a match by name"))
            mb_items.append(MenuItem("manual", "✏️", "Enter Metadata Manually", "Type artist, title, etc."))
            mb_items.append(MenuItem("cancel", "←", "Cancel", "Go back"))

            mb_subtitle = ""
            if not preview_available:
                mb_subtitle = "Audio preview not available — use Full Preview mode to enable playback"

            result = select_menu(
                f"MusicBrainz Search — Track {track_num}",
                mb_items,
                subtitle=mb_subtitle,
            )

            if result.cancelled or result.key == "cancel":
                return MB_ASSIST_CANCELLED

            if result.key == "preview":
                play_audio_preview(temp_chunk_path, duration_seconds=30)
                continue

            if result.key == "manual":
                # Manual metadata entry
                artist = (input_dialog("Artist") or "").strip()
                title = (input_dialog("Title") or "").strip()

                if not artist or not title:
                    print(f"  {Style.RED}❌ Artist and title are required.{Style.RESET}")
                    wait_for_enter()
                    continue

                track['status'] = 'identified'
                track['artist'] = artist
                track['title'] = title
                track['album'] = (input_dialog("Album (Enter to skip)") or "").strip() or "Unknown Album"
                track['enhanced_metadata'] = track.get('enhanced_metadata', {})

                genre = (input_dialog("Genre (Enter to skip)") or "").strip()
                if genre:
                    track['enhanced_metadata']['genres'] = [g.strip() for g in genre.split(',')]

                detected_bpm = track.get('detected_bpm')
                bpm_prompt = f"BPM [{detected_bpm}]" if detected_bpm else "BPM (Enter to skip)"
                bpm_input = (input_dialog(bpm_prompt) or "").strip()
                if bpm_input:
                    try:
                        track['enhanced_metadata']['bpm'] = int(bpm_input)
                    except ValueError:
                        pass
                elif detected_bpm:
                    try:
                        track['enhanced_metadata']['bpm'] = int(detected_bpm)
                    except ValueError:
                        pass

                year = (input_dialog("Year (Enter to skip)") or "").strip()
                if year:
                    track['enhanced_metadata']['release_date'] = year

                safe_artist = artist.translate(str.maketrans('', '', '<>:"/\\|?*'))
                safe_title = title.translate(str.maketrans('', '', '<>:"/\\|?*'))
                track['unidentified_filename'] = f"{safe_artist} - {safe_title}.flac"

                changes_made = True
                print(f"\n  ✅ Applied: {artist} - {title}")
                wait_for_enter()
                return MB_ASSIST_APPLIED

            if result.key == "search":
                # Search query
                if default_query:
                    query = input_dialog("Search query", default=default_query)
                else:
                    query = input_dialog("Search query")

                if not query or not query.strip():
                    print(f"  {Style.YELLOW}⚠️  No query entered.{Style.RESET}")
                    continue
                query = query.strip()

                print(f"  {Style.DIM}Searching MusicBrainz tracks and albums...{Style.RESET}")
                track_results = []
                album_results = []
                search_errors = []

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {
                        executor.submit(
                            musicbrainz_search_recordings,
                            query=query,
                            limit=20,
                            prefer_original=True,
                        ): "tracks",
                        executor.submit(musicbrainz_search_releases, query=query, limit=20): "albums",
                    }
                    for future in as_completed(futures):
                        bucket = futures[future]
                        try:
                            data = future.result() or []
                        except Exception as exc:
                            search_errors.append(f"{bucket}: {exc}")
                            data = []
                        if bucket == "tracks":
                            track_results = data
                        else:
                            album_results = data

                if not track_results and not album_results:
                    if search_errors:
                        print(f"  {Style.RED}Search failed: {'; '.join(search_errors)}{Style.RESET}")
                    else:
                        print(f"  {Style.YELLOW}No results found.{Style.RESET}")
                    wait_for_enter()
                    continue

                combined_options = []
                option_items = []

                for trk in track_results[:20]:
                    t_artist = trk.get('artist', 'Unknown') or 'Unknown'
                    t_title = trk.get('title', 'Unknown') or 'Unknown'
                    t_album = trk.get('album', 'Unknown Album') or 'Unknown Album'
                    t_score = trk.get('score', 0)
                    option_items.append(MenuItem(
                        str(len(combined_options)),
                        "🎵",
                        f"{t_artist} - {t_title}",
                        f"Track  •  Album: {t_album}  •  Score: {t_score}",
                    ))
                    combined_options.append({"kind": "track", "payload": trk})

                for alb in album_results[:20]:
                    alb_title = alb.get('title', 'Unknown')
                    alb_date = (alb.get('date', '') or '')[:4]
                    alb_artists = ', '.join(alb.get('artists', ['Unknown']))
                    alb_score = alb.get('score', 0)
                    date_str = f" ({alb_date})" if alb_date else ""
                    option_items.append(MenuItem(
                        str(len(combined_options)),
                        "💿",
                        f"{alb_title}{date_str}",
                        f"Album  •  {alb_artists}  •  Score: {alb_score}",
                    ))
                    combined_options.append({"kind": "album", "payload": alb})

                option_items.append(MenuItem("cancel", "←", "Cancel"))
                combined_result = select_menu(
                    "Matches Found (Top 20 tracks + Top 20 albums)",
                    option_items,
                )
                if combined_result.cancelled or combined_result.key == "cancel":
                    continue

                chosen = combined_options[int(combined_result.key)]
                if chosen["kind"] == "track":
                    picked = chosen["payload"]
                else:
                    picked_album = chosen["payload"]
                    release_id = picked_album.get('release_id')
                    if not release_id:
                        print(f"  {Style.YELLOW}Selected album has no release ID.{Style.RESET}")
                        wait_for_enter()
                        continue

                    print(f"  {Style.DIM}Loading tracklist...{Style.RESET}")
                    tracklist_data = musicbrainz_get_release_tracklist(release_id)
                    if not tracklist_data or not tracklist_data.get('tracks'):
                        print(f"  {Style.YELLOW}Could not load tracklist.{Style.RESET}")
                        wait_for_enter()
                        continue

                    album_name = tracklist_data['title']
                    trk_list = tracklist_data['tracks']
                    trk_items = []
                    for i, t in enumerate(trk_list):
                        pos = t.get('position', i + 1)
                        t_title = t.get('title', 'Unknown')
                        t_artist = t.get('artist', '')
                        dur_sec = t.get('duration_ms', 0) // 1000
                        dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else "?:??"
                        artist_str = f" — {t_artist}" if t_artist and t_artist != 'Unknown Artist' else ""
                        trk_items.append(MenuItem(
                            str(i),
                            "🎵",
                            f"[{pos:2d}] {t_title}{artist_str}",
                            f"Duration: {dur_str}",
                        ))
                    trk_items.append(MenuItem("cancel", "←", "Cancel"))

                    trk_result = select_menu(f"{album_name} ({len(trk_list)} tracks)", trk_items)
                    if trk_result.cancelled or trk_result.key == "cancel":
                        continue

                    picked_trk = trk_list[int(trk_result.key)]
                    artist = picked_trk.get('artist', '')
                    title = picked_trk.get('title', '')
                    recording_id = picked_trk.get('recording_id')
                    if not artist or artist == 'Unknown Artist':
                        artist = ', '.join(picked_album.get('artists', ['Unknown']))
                    if not title:
                        print(f"  {Style.RED}Track missing title.{Style.RESET}")
                        continue

                    picked = {
                        'artist': artist,
                        'title': title,
                        'album': album_name,
                        'recording_id': recording_id,
                    }

                # === APPLY SELECTION (shared by all modes) ===
                artist = picked.get('artist') or ''
                title = picked.get('title') or ''
                recording_id = picked.get('recording_id')

                if not artist or not title:
                    print(f"  {Style.RED}Chosen result missing artist/title.{Style.RESET}")
                    wait_for_enter()
                    continue

                track['status'] = 'identified'
                track['artist'] = artist
                track['title'] = title
                track['album'] = picked.get('album') or track.get('album') or 'Unknown Album'

                track.setdefault('enhanced_metadata', {})
                enhanced = get_enhanced_metadata(artist, title, recording_id=recording_id) or {}
                if enhanced.get('genres'):
                    track['enhanced_metadata']['genres'] = enhanced.get('genres')
                if enhanced.get('release_date'):
                    track['enhanced_metadata']['release_date'] = enhanced.get('release_date')
                if enhanced.get('label'):
                    track['enhanced_metadata']['label'] = enhanced.get('label')
                if enhanced.get('isrc'):
                    track['enhanced_metadata']['isrc'] = enhanced.get('isrc')

                if not track['enhanced_metadata'].get('bpm'):
                    detected_bpm = track.get('detected_bpm')
                    if detected_bpm:
                        try:
                            track['enhanced_metadata']['bpm'] = int(detected_bpm)
                        except ValueError:
                            pass

                safe_artist = artist.translate(str.maketrans('', '', '<>:"/\\|?*'))
                safe_title = title.translate(str.maketrans('', '', '<>:"/\\|?*'))
                track['expected_filename'] = f"{safe_artist} - {safe_title}.flac"

                changes_made = True
                print(f"\n  ✅ Applied: {artist} - {title}")
                wait_for_enter()
                return MB_ASSIST_APPLIED

    def _auto_mb_assist_unidentified():
        """Offer an optional MusicBrainz pass over all unidentified tracks."""
        unidentified_indices = [i for i, t in enumerate(tracks) if t.get('status') == 'unidentified']
        if not unidentified_indices:
            return

        for i in unidentified_indices:
            t = tracks[i]
            track_num = i + 1
            print(f"\n{Style.YELLOW}{'─'*60}{Style.RESET}")
            print(f"  {Style.BOLD}Unidentified track {track_num}{Style.RESET}")
            print(f"  File: {Style.DIM}{t.get('unidentified_filename','Unknown')}{Style.RESET}")
            assist_result = _mb_assist_track(t, track_num)
            if assist_result == MB_ASSIST_APPLIED:
                continue
            if confirm_dialog("Continue to next unidentified track?", default=True):
                continue
            edit_track(track_num)
            break

    def edit_track(track_num):
        nonlocal changes_made

        if track_num < 1 or track_num > len(tracks):
            print(f"\n  ❌ Invalid track number. Use 1-{len(tracks)}")
            wait_for_enter()
            return

        track = tracks[track_num - 1]
        status = track.get('status', 'unknown')

        if status == 'skipped':
            print(f"  Status: {Style.DIM}⏭️  Skipped (already exists){Style.RESET}")
            wait_for_enter()
            return

        if status == 'unidentified':
            detected_bpm = track.get('detected_bpm', '')
            temp_chunk_path = track.get('temp_chunk_path', '')

            # Build subtitle
            sub_parts = ["Status: ❓ Unidentified"]
            if track.get('unidentified_filename'):
                sub_parts.append(f"File: {track.get('unidentified_filename')}")
            if detected_bpm:
                sub_parts.append(f"Detected BPM: ~{detected_bpm}")

            while True:
                edit_items = []
                if temp_chunk_path and os.path.exists(temp_chunk_path):
                    edit_items.append(MenuItem("preview", "🎧", "Play Audio Preview", "Hear the track"))
                else:
                    edit_items.append(MenuItem("preview", "🎧", "Play Audio Preview",
                                              "Not available (use Full Preview mode)", enabled=False))
                edit_items.append(MenuItem("search", "🔎", "Search MusicBrainz", "Pick a likely match"))
                edit_items.append(MenuItem("manual", "✏️", "Enter Metadata", "Type it manually"))
                edit_items.append(MenuItem("delete", "🗑️", "Delete Track", "Exclude from output"))
                edit_items.append(MenuItem("back", "←", "Go Back"))

                result = select_menu(f"Editing Track {track_num}", edit_items,
                                     subtitle="  •  ".join(sub_parts))

                if result.cancelled or result.key == "back":
                    return

                if result.key == "preview":
                    play_audio_preview(temp_chunk_path, duration_seconds=30)
                    continue
                elif result.key == "search":
                    assist_result = _mb_assist_track(track, track_num)
                    if assist_result == MB_ASSIST_APPLIED:
                        return
                    continue
                elif result.key == "delete":
                    if confirm_dialog("Delete this track? (exclude from output)"):
                        track['status'] = 'skipped'
                        track['reason'] = 'user_deleted'
                        changes_made = True
                        print("  ✅ Track deleted (excluded from output)")
                        wait_for_enter()
                        return
                    continue
                elif result.key == "manual":
                    # Manual metadata entry
                    new_artist = (input_dialog("Artist") or "").strip()
                    new_title = (input_dialog("Title") or "").strip()

                    if not new_artist or not new_title:
                        print(f"  {Style.RED}❌ Artist and title are required.{Style.RESET}")
                        wait_for_enter()
                        return

                    track['status'] = 'identified'
                    track['artist'] = new_artist
                    track['title'] = new_title
                    track['album'] = (input_dialog("Album (Enter to skip)") or "").strip() or "Unknown Album"
                    track['enhanced_metadata'] = track.get('enhanced_metadata', {})

                    genre = (input_dialog("Genre (Enter to skip)") or "").strip()
                    if genre:
                        track['enhanced_metadata']['genres'] = [g.strip() for g in genre.split(',')]

                    bpm_prompt = f"BPM [{detected_bpm}]" if detected_bpm else "BPM (Enter to skip)"
                    bpm_input = (input_dialog(bpm_prompt) or "").strip()
                    if bpm_input:
                        try:
                            track['enhanced_metadata']['bpm'] = int(bpm_input)
                        except ValueError:
                            pass
                    elif detected_bpm:
                        track['enhanced_metadata']['bpm'] = int(detected_bpm)

                    safe_artist = new_artist.translate(str.maketrans('', '', '<>:"/\\|?*'))
                    safe_title = new_title.translate(str.maketrans('', '', '<>:"/\\|?*'))
                    track['expected_filename'] = f"{safe_artist} - {safe_title}.flac"

                    changes_made = True
                    print(f"\n  ✅ Track converted: {new_artist} - {new_title}")
                    wait_for_enter()
                    return

        # Identified track editing
        current_artist = track.get('artist', '')
        current_title = track.get('title', '')
        current_album = track.get('album', '')
        em = track.get('enhanced_metadata', {})
        current_genres = em.get('genres', [])
        current_bpm = em.get('bpm', '')
        current_year = em.get('release_date', '')
        temp_chunk_path = track.get('temp_chunk_path', '')

        while True:
            genre_str = ', '.join(current_genres) if current_genres else '---'
            bpm_str = str(current_bpm) if current_bpm else '---'
            year_str = str(current_year) if current_year else '---'

            field_items = [
                MenuItem("artist", "🎤", "Artist", current_artist or "---"),
                MenuItem("title", "🎵", "Title", current_title or "---"),
                MenuItem("album", "💿", "Album", current_album or "---"),
                MenuItem("genre", "🏷️", "Genre", genre_str),
                MenuItem("bpm", "🥁", "BPM", bpm_str),
                MenuItem("year", "📅", "Year", year_str),
            ]
            if temp_chunk_path and os.path.exists(temp_chunk_path):
                field_items.append(MenuItem("preview", "🎧", "Play Audio Preview"))
            else:
                field_items.append(MenuItem("preview", "🎧", "Play Audio Preview",
                                           "Not available", enabled=False))
            field_items.append(MenuItem("done", "←", "Done Editing"))

            result = select_menu(f"Editing Track {track_num}", field_items,
                                 subtitle="✅ Identified — Select a field to edit")

            if result.cancelled or result.key == "done":
                break
            elif result.key == "preview":
                play_audio_preview(temp_chunk_path, duration_seconds=30)
            elif result.key == "artist":
                new_val = input_dialog("New artist", default=current_artist)
                if new_val and new_val.strip() != current_artist:
                    track['artist'] = new_val.strip()
                    current_artist = new_val.strip()
                    changes_made = True
                    print("  ✅ Artist updated")
            elif result.key == "title":
                new_val = input_dialog("New title", default=current_title)
                if new_val and new_val.strip() != current_title:
                    track['title'] = new_val.strip()
                    current_title = new_val.strip()
                    changes_made = True
                    print("  ✅ Title updated")
            elif result.key == "album":
                new_val = input_dialog("New album", default=current_album)
                if new_val and new_val.strip() != current_album:
                    track['album'] = new_val.strip()
                    changes_made = True
                    print("  ✅ Album updated")
            elif result.key == "genre":
                genre_default = ', '.join(current_genres) if current_genres else ""
                new_val = input_dialog("New genre(s), comma-separated", default=genre_default)
                if new_val and new_val.strip():
                    track['enhanced_metadata']['genres'] = [g.strip() for g in new_val.split(',')]
                    current_genres = track['enhanced_metadata']['genres']
                    changes_made = True
                    print("  ✅ Genre updated")
            elif result.key == "bpm":
                bpm_default = str(current_bpm) if current_bpm else ""
                new_val = input_dialog("New BPM", default=bpm_default)
                if new_val and new_val.strip():
                    try:
                        track['enhanced_metadata']['bpm'] = int(new_val.strip())
                        current_bpm = int(new_val.strip())
                        changes_made = True
                        print("  ✅ BPM updated")
                    except ValueError:
                        print("  ❌ Invalid BPM (must be a number)")
            elif result.key == "year":
                year_default = str(current_year) if current_year else ""
                new_val = input_dialog("New year", default=year_default)
                if new_val and new_val.strip():
                    track['enhanced_metadata']['release_date'] = new_val.strip()
                    current_year = new_val.strip()
                    changes_made = True
                    print("  ✅ Year updated")

        # Update expected filename
        safe_artist = current_artist.translate(str.maketrans('', '', '<>:"/\\|?*'))
        safe_title = current_title.translate(str.maketrans('', '', '<>:"/\\|?*'))
        track['expected_filename'] = f"{safe_artist} - {safe_title}.flac"

    def save_changes():
        nonlocal changes_made
        cache_data['tracks'] = tracks
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f, indent=2)
        changes_made = False
        print(f"\n  ✅ Preview Session changes saved to {os.path.basename(cache_path)}")

    def import_tracklist():
        """Import tracklist/cue sheet to bulk-identify tracks"""
        nonlocal changes_made

        print(f"\n{Style.CYAN}{'='*60}{Style.RESET}")
        print(f"  {Style.BOLD}📋 IMPORT TRACKLIST{Style.RESET}")
        print(f"{Style.CYAN}{'='*60}{Style.RESET}\n")
        print(f"  Supported formats:")
        print(f"  • Simple:  00:00 Artist - Title")
        print(f"  • Detailed: 00:00:00 Artist - Title (Album)")
        print(f"  • CUE sheet format")
        print(f"\n  {Style.DIM}Paste your tracklist below, then press Enter twice:{Style.RESET}\n")

        # Collect multi-line input
        lines = []
        empty_count = 0
        while True:
            try:
                line = input()
            except (KeyboardInterrupt, EOFError):
                return
            if not line.strip():
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
                lines.append(line)

        if not lines:
            print(f"\n  {Style.YELLOW}⚠️  No tracklist entered.{Style.RESET}")
            wait_for_enter()
            return

        tracklist_text = '\n'.join(lines)

        # Parse tracklist
        print(f"\n  {Style.DIM}Parsing tracklist...{Style.RESET}")
        tracklist = parse_tracklist(tracklist_text)

        if not tracklist:
            print(f"\n  {Style.RED}❌ Could not parse tracklist.{Style.RESET}")
            print(f"  {Style.DIM}Please check format and try again.{Style.RESET}")
            wait_for_enter()
            return

        # Show preview
        print(f"\n  {Style.GREEN}✓{Style.RESET} Parsed {len(tracklist)} tracks:\n")
        print(format_tracklist_preview(tracklist))

        # Match to existing tracks
        matches = match_tracklist_to_tracks(tracklist, tracks)

        if not matches:
            print(f"\n  {Style.YELLOW}⚠️  No matches found.{Style.RESET}")
            print(f"  {Style.DIM}Make sure timestamps align with your track splits.{Style.RESET}")
            wait_for_enter()
            return

        print(f"\n  {Style.GREEN}✓{Style.RESET} Matched {len(matches)} tracks")

        # Confirm
        if not confirm_dialog(f"Apply metadata to {len(matches)} tracks?", default=True):
            return

        # Apply metadata
        applied_count = 0
        for track_idx, entry in matches:
            track = tracks[track_idx]
            track['status'] = 'identified'
            track['artist'] = entry['artist']
            track['title'] = entry['title']

            if entry.get('album'):
                track['album'] = entry['album']
            elif 'album' not in track:
                track['album'] = 'Unknown Album'

            # Update filename
            safe_artist = entry['artist'].translate(str.maketrans('', '', '<>:"/\\|?*'))
            safe_title = entry['title'].translate(str.maketrans('', '', '<>:"/\\|?*'))
            track['unidentified_filename'] = f"{safe_artist} - {safe_title}.flac"

            applied_count += 1

        changes_made = True
        print(f"\n  {Style.GREEN}✅ Applied metadata to {applied_count} tracks!{Style.RESET}")
        wait_for_enter()

    # Main editor loop
    while True:
        identified, unidentified, skipped, no_genre, no_bpm = get_stats()
        filtered = get_filtered_tracks()
        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)

        if page >= total_pages:
            page = total_pages - 1
        if page < 0:
            page = 0

        # Build track display header
        header_lines, fallback_header = _build_track_display(
            filtered, page, page_size, current_filter, tracks
        )

        # Build subtitle with stats
        sub = f"✅ {identified}  ❓ {unidentified}  ⏭️ {skipped}"
        if no_genre:
            sub += f"  ⚠️ {no_genre} no genre"
        if no_bpm:
            sub += f"  ⚠️ {no_bpm} no BPM"
        if changes_made:
            sub += "  📝 Unsaved"

        # Build menu items
        items = []

        # FILTER
        filter_defs = [
            ("filter_all", "📋", "All Tracks", f"{len(tracks)} tracks", 'all'),
            ("filter_genre", "🎵", "Missing Genre", f"{no_genre} tracks", 'no_genre'),
            ("filter_bpm", "🥁", "Missing BPM", f"{no_bpm} tracks", 'no_bpm'),
            ("filter_unid", "❓", "Unidentified", f"{unidentified} tracks", 'unidentified'),
            ("filter_id", "✅", "Identified", f"{identified} tracks", 'identified'),
        ]
        for key, icon, title, desc, filt in filter_defs:
            active = " ◀" if current_filter == filt else ""
            items.append(MenuItem(key, icon, title, desc + active))

        # NAV
        items.append(MenuItem("__divider__nav_spacing", "", " "))
        items.append(MenuItem("prev", "◀", "Previous Page",
                             f"Page {page}/{total_pages}" if page > 0 else "First page",
                             enabled=page > 0, show_number=False))
        items.append(MenuItem("next", "▶", "Next Page",
                             f"Page {page+2}/{total_pages}" if page < total_pages-1 else "Last page",
                             enabled=page < total_pages-1, show_number=False))

        # EDIT
        items.append(MenuItem("__divider__edit_actions", "", "────────────────────────"))
        items.append(MenuItem("edit_unidentified_all", "🧩", "Edit All Unidentified", "Walk through each unidentified track"))
        items.append(MenuItem("edit", "✏️", "Edit Track", "Select a track number to edit"))
        items.append(MenuItem("import", "📋", "Import Tracklist", "Paste timestamps & metadata"))

        # FINISH
        if session_editor_mode:
            items.append(MenuItem("apply", "💾", "Save Session Edits", "Update this session record now"))
            items.append(MenuItem("exit", "📤", "Save & Exit", "Return to Session History"))
        else:
            items.append(MenuItem("apply", "💾", "Save & Export Now", "Create files now"))
            items.append(MenuItem("exit", "📤", "Save & Exit", "Resume Preview Session later from the main menu"))
        items.append(MenuItem("discard", "🗑️", "Discard", "Cancel without saving"))

        result = select_menu(
            "Track Editor",
            items,
            subtitle=sub,
            header_lines=header_lines,
            fallback_header=fallback_header,
            show_item_divider=True,
            compact_rows=True,
            grid_columns=2,
        )

        if result.cancelled:
            continue

        key = result.key

        # FILTER actions
        if key == "filter_all":
            current_filter = 'all'
            page = 0
        elif key == "filter_genre":
            current_filter = 'no_genre'
            page = 0
        elif key == "filter_bpm":
            current_filter = 'no_bpm'
            page = 0
        elif key == "filter_unid":
            current_filter = 'unidentified'
            page = 0
        elif key == "filter_id":
            current_filter = 'identified'
            page = 0
        # NAV actions
        elif key == "next":
            if page < total_pages - 1:
                page += 1
        elif key == "prev":
            if page > 0:
                page -= 1
        # EDIT actions
        elif key == "edit_unidentified_all":
            _auto_mb_assist_unidentified()
        elif key == "edit":
            track_num_str = input_dialog(f"Track number (1-{len(tracks)})")
            if track_num_str and track_num_str.strip().isdigit():
                edit_track(int(track_num_str.strip()))
        elif key == "import":
            import_tracklist()
        # FINISH actions
        elif key == "apply":
            if changes_made:
                save_changes()
            return 'apply'
        elif key == "exit":
            if changes_made:
                save_changes()
            return 'done'
        elif key == "discard":
            if changes_made:
                if not confirm_dialog("Discard unsaved changes?"):
                    continue
            return 'quit'


# =============================================================================
# PREVIEW TABLE
# =============================================================================

def display_preview_table(cache_data):
    """Display a formatted preview of what will be processed"""
    tracks = cache_data.get('tracks', [])

    print(f"\n{'='*80}")
    print(f"                PREVIEW SESSION - No files exported yet")
    print(f"{'='*80}\n")

    identified = [t for t in tracks if t['status'] == 'identified']
    unidentified = [t for t in tracks if t['status'] == 'unidentified']
    skipped = [t for t in tracks if t['status'] == 'skipped']

    print(f"📊 Found {len(tracks)} total tracks:\n")

    print(f"{'#':<4} {'Conf':<6} {'Artist':<22} {'Title':<25} {'Sources':<12}")
    print(f"{'-'*80}")

    for i, track in enumerate(tracks[:15], 1):
        if track['status'] == 'identified':
            rm = track.get('readable_metadata', {})
            confidence = rm.get('confidence', 0)

            if confidence >= 0.9:
                conf_display = f"✓✓{confidence:.2f}"
            elif confidence >= 0.7:
                conf_display = f"✓ {confidence:.2f}"
            else:
                conf_display = f"? {confidence:.2f}"

            artist = track.get('artist', 'Unknown')[:21]
            title = track.get('title', 'Unknown')[:24]
            sources = "+".join(rm.get('sources_used', []))[:11]

            print(f"{i:<4} {conf_display:<6} {artist:<22} {title:<25} {sources:<12}")
        elif track['status'] == 'unidentified':
            filename = track.get('unidentified_filename', 'Unknown')[:50]
            print(f"{i:<4} {'--':<6} ❓ Unidentified: {filename}")
        else:
            artist = track.get('artist', 'Skipped')[:21]
            title = track.get('title', '')[:24]
            print(f"{i:<4} {'--':<6} ⏭️ {artist:<22} {title:<25}")

    if len(tracks) > 15:
        print(f"... and {len(tracks) - 15} more tracks")

    print(f"\n{'='*80}")
    print(f"📈 SUMMARY:")
    print(f"{'─'*80}")
    print(f"  ✅ Will create: {len(identified)} new tracks")
    print(f"  ❓ Unidentified: {len(unidentified)} tracks")
    print(f"  ⏭️  Will skip: {len(skipped)} existing tracks")
    print(f"{'='*80}\n")
