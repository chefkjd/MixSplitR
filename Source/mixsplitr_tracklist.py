"""
mixsplitr_tracklist.py - Tracklist/Cue sheet parser for MixSplitR

Supports:
- Simple format: "00:00 Artist - Title"
- Detailed format: "00:00:00 Artist - Title (Album)"
- CUE sheet format
"""

import re


def parse_timestamp(timestamp_str):
    """
    Parse timestamp string to seconds.
    Supports: MM:SS, HH:MM:SS, or MM:SS.mmm formats
    """
    timestamp_str = timestamp_str.strip()

    # Remove milliseconds if present (e.g., 00:00.000)
    if '.' in timestamp_str:
        timestamp_str = timestamp_str.split('.')[0]

    parts = timestamp_str.split(':')

    try:
        if len(parts) == 2:  # MM:SS
            minutes, seconds = int(parts[0]), int(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 3:  # HH:MM:SS
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None

    return None


def parse_simple_tracklist(text):
    """
    Parse simple tracklist format:
    00:00 Artist - Title
    03:45 Artist - Title
    07:20 Artist - Title (Album)

    Returns list of dicts: [{'timestamp': seconds, 'artist': str, 'title': str, 'album': str}, ...]
    """
    tracks = []

    # Match patterns like: "00:00 Artist - Title" or "00:00:00 Artist - Title (Album)"
    # Also handle optional track numbers: "1. 00:00 Artist - Title"
    pattern = r'(?:\d+\.\s*)?([0-9:\.]+)\s+(.+?)\s*-\s*(.+?)(?:\s*\(([^)]+)\))?$'

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        match = re.match(pattern, line)
        if match:
            timestamp_str, artist, title, album = match.groups()
            timestamp = parse_timestamp(timestamp_str)

            if timestamp is not None:
                tracks.append({
                    'timestamp': timestamp,
                    'artist': artist.strip(),
                    'title': title.strip(),
                    'album': album.strip() if album else None
                })

    return tracks


def parse_cue_sheet(text):
    """
    Parse CUE sheet format:
    TRACK 01 AUDIO
      TITLE "Title"
      PERFORMER "Artist"
      INDEX 01 00:00:00

    Returns list of dicts: [{'timestamp': seconds, 'artist': str, 'title': str, 'album': str}, ...]
    """
    tracks = []
    current_track = {}
    album = None

    # Extract album name from header if present
    album_match = re.search(r'TITLE\s+"([^"]+)"', text.split('TRACK')[0])
    if album_match:
        album = album_match.group(1)

    # Split by TRACK
    track_blocks = re.split(r'TRACK\s+\d+', text)[1:]  # Skip header

    for block in track_blocks:
        track = {'album': album}

        # Extract TITLE
        title_match = re.search(r'TITLE\s+"([^"]+)"', block)
        if title_match:
            track['title'] = title_match.group(1)

        # Extract PERFORMER (artist)
        performer_match = re.search(r'PERFORMER\s+"([^"]+)"', block)
        if performer_match:
            track['artist'] = performer_match.group(1)

        # Extract INDEX 01 timestamp
        index_match = re.search(r'INDEX\s+01\s+([0-9:]+)', block)
        if index_match:
            timestamp = parse_timestamp(index_match.group(1))
            if timestamp is not None:
                track['timestamp'] = timestamp

        # Only add if we have minimum required fields
        if 'artist' in track and 'title' in track and 'timestamp' in track:
            tracks.append(track)

    return tracks


def parse_tracklist(text):
    """
    Auto-detect format and parse tracklist.
    Returns list of dicts: [{'timestamp': seconds, 'artist': str, 'title': str, 'album': str}, ...]
    """
    text = text.strip()

    if not text:
        return []

    # Detect CUE format
    if 'TRACK' in text.upper() and 'INDEX' in text.upper():
        tracks = parse_cue_sheet(text)
        if tracks:
            return tracks

    # Try simple format
    tracks = parse_simple_tracklist(text)
    return tracks


def match_tracklist_to_tracks(tracklist, existing_tracks):
    """
    Match tracklist entries to existing tracks based on timestamps.

    Args:
        tracklist: List of dicts from parse_tracklist()
        existing_tracks: List of track dicts with 'start_time' or 'index' fields

    Returns:
        List of (track_index, tracklist_entry) tuples
    """
    matches = []

    # Sort tracklist by timestamp
    sorted_tracklist = sorted(tracklist, key=lambda x: x['timestamp'])

    # For each tracklist entry, find the closest matching track
    for entry in sorted_tracklist:
        best_match = None
        best_diff = float('inf')

        for idx, track in enumerate(existing_tracks):
            # Get track start time (might be stored in different fields)
            track_time = track.get('start_time', 0)
            if track_time == 0 and 'index' in track:
                # Estimate based on index if no start_time
                track_time = track['index'] * 180  # Assume ~3min per track as fallback

            diff = abs(track_time - entry['timestamp'])

            # Match if within 30 seconds
            if diff < 30 and diff < best_diff:
                best_diff = diff
                best_match = idx

        if best_match is not None:
            matches.append((best_match, entry))

    return matches


def format_tracklist_preview(tracklist):
    """Format tracklist for display"""
    lines = []
    for i, entry in enumerate(tracklist, 1):
        timestamp = f"{entry['timestamp'] // 60:02d}:{entry['timestamp'] % 60:02d}"
        album_str = f" ({entry['album']})" if entry.get('album') else ""
        lines.append(f"  {i:2}. {timestamp} {entry['artist']} - {entry['title']}{album_str}")
    return '\n'.join(lines)
