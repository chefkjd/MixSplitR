"""
mixsplitr_tagging.py - Audio file tagging for MixSplitR

Contains:
- Artist normalization (feat/collab → title, single artist folder)
- FLAC metadata embedding
- ALAC conversion and tagging (macOS compatibility)
- Multi-format support (MP3, OGG, OPUS, WAV, AIFF, etc.)
- File organization (artist folders)
"""

import os
import re
import shutil
import platform
import requests


# ─── Artist normalization ────────────────────────────────────────────────────
# Splits collaboration credits into a primary artist and a featured string
# that gets appended to the title, so all tracks by the same primary artist
# end up in one folder instead of "Artist & Guest1", "Artist feat. Guest2", etc.

# Patterns that indicate a featured/guest credit (case-insensitive)
_FEAT_PATTERNS = [
    r'\s+feat\.?\s+',     # feat. / feat
    r'\s+ft\.?\s+',       # ft. / ft
    r'\s+featuring\s+',   # featuring
    r'\s+with\s+',        # with
    r'\s+vs\.?\s+',       # vs. / vs
    r'\s+x\s+',           # x (common in electronic: "Artist x Artist")
]

# Separator characters that indicate multiple co-artists
_COLLAB_SEPARATORS = [
    r'\s*&\s+',           # & (with space after, to avoid matching "Simon & Garfunkel"-style names)
    r'\s*,\s+',           # , followed by space
]

def normalize_artist(artist, title):
    """Split collaboration credits: return (primary_artist, updated_title).

    When enabled, transforms:
        artist="Catching Flies & Hot Chip"  title="Sunne"
    into:
        ("Catching Flies", "Sunne (feat. Hot Chip)")

    Also handles stacked credits:
        artist="Catching Flies, Erick The Architect, Lord Apex"  title="Dive"
    into:
        ("Catching Flies", "Dive (feat. Erick The Architect, Lord Apex)")

    If 'feat.' is already in the title, the additional artists are appended
    to the existing parenthetical rather than creating a duplicate.
    """
    if not artist:
        return artist, title

    # ── Step 1: Check for explicit feat/with/vs patterns first ────────────
    for pat in _FEAT_PATTERNS:
        match = re.split(pat, artist, maxsplit=1, flags=re.IGNORECASE)
        if len(match) == 2:
            primary = match[0].strip()
            featured = match[1].strip()
            if primary and featured:
                title = _append_featured(title, featured)
                return primary, title

    # ── Step 2: Check for separator-based collaborations (& , ) ───────────
    for sep in _COLLAB_SEPARATORS:
        parts = re.split(sep, artist)
        if len(parts) >= 2:
            primary = parts[0].strip()
            featured = ", ".join(p.strip() for p in parts[1:] if p.strip())
            if primary and featured:
                title = _append_featured(title, featured)
                return primary, title

    # ── No collaboration detected — return as-is ─────────────────────────
    return artist, title


def _append_featured(title, featured):
    """Append featured artist(s) to title, merging with existing feat. if present."""
    # Check if title already has a feat/ft parenthetical
    existing = re.search(r'\((?:feat\.?|ft\.?|featuring)\s+([^)]+)\)', title, re.IGNORECASE)
    if existing:
        # Merge: "Song (feat. A)" + "B" → "Song (feat. A, B)"
        old_feat = existing.group(0)
        merged = old_feat[:-1] + ", " + featured + ")"
        return title.replace(old_feat, merged)

    return f"{title} (feat. {featured})"


def _maybe_normalize(artist, title):
    """Apply legacy collab normalization when mode is set to collab_only."""
    try:
        from mixsplitr_core import get_config
        from mixsplitr_artist_normalization import MODE_COLLAB_ONLY, normalize_artist_mode

        config = get_config()
        mode = normalize_artist_mode(
            config.get('artist_normalization_mode', ''),
            legacy_normalize_artists=config.get('normalize_artists', True),
        )
        if mode == MODE_COLLAB_ONLY:
            return normalize_artist(artist, title)
    except Exception:
        try:
            from mixsplitr_core import get_config

            config = get_config()
            if config.get('normalize_artists', True):
                return normalize_artist(artist, title)
        except Exception:
            pass
    return artist, title


def _safe_path_segment(value):
    return str(value or "").strip().translate(str.maketrans("", "", '<>:"/\\|?*')).strip()


def _normalize_rename_preset(value):
    parsed = str(value or "simple").strip().lower()
    if parsed in {"simple", "album_folders", "discography", "podcast"}:
        return parsed
    return "simple"


def _extract_track_prefix(track_number):
    if track_number in (None, "", 0):
        return ""
    raw = str(track_number).strip()
    if not raw:
        return ""
    if "/" in raw:
        raw = raw.split("/", 1)[0].strip()
    try:
        value = int(raw)
        if value > 0:
            return str(value).zfill(2)
    except Exception:
        return ""
    return raw if raw else ""


def _resolve_export_naming(
    artist,
    title,
    ext,
    album=None,
    enhanced_metadata=None,
    rename_preset=None,
    track_number=None,
):
    """Resolve destination folder and filename, allowing mode-specific overrides."""
    artist_name = str(artist or "Unknown").strip()
    album_name = str(album or "Unknown Album").strip()
    title_text = str(title or "").strip()
    preset = _normalize_rename_preset(rename_preset)
    folder_parts = [_safe_path_segment(artist_name)]
    filename_base = f"{artist_name} - {title_text}" if title_text else f"{artist_name}"
    track_text = _extract_track_prefix(track_number)
    filename_prefix = f"{track_text} - " if track_text else "Track - "
    release_year = ""
    if isinstance(enhanced_metadata, dict):
        release_year = str(enhanced_metadata.get("release_date") or "").strip()[:4]

    if preset == "album_folders":
        folder_parts = [_safe_path_segment(artist_name), _safe_path_segment(album_name)]
        filename_base = f"{filename_prefix}{title_text}" if title_text else filename_prefix.strip()
    elif preset == "discography":
        folder_parts = [
            _safe_path_segment(artist_name),
            _safe_path_segment(release_year or "Unknown Year"),
            _safe_path_segment(album_name),
        ]
        filename_base = f"{filename_prefix}{title_text}" if title_text else filename_prefix.strip()
    elif preset == "podcast":
        folder_parts = [_safe_path_segment(artist_name)]
        filename_base = title_text if title_text else "Track"

    if isinstance(enhanced_metadata, dict):
        custom_folder = str(enhanced_metadata.get("output_folder_name", "")).strip()
        if custom_folder:
            folder_parts = [_safe_path_segment(custom_folder)]

        custom_filename_base = str(enhanced_metadata.get("output_filename_base", "")).strip()
        if custom_filename_base:
            filename_base = custom_filename_base
        elif bool(enhanced_metadata.get("output_title_only_filename", False)):
            filename_base = title_text

    safe_folder = os.path.join(*[part for part in folder_parts if part]) or "Unknown"
    safe_file = f"{filename_base}{ext}".translate(str.maketrans('', '', '<>:"/\\|?*')).strip()
    if not safe_file:
        safe_file = f"Track{ext}"
    return safe_folder, safe_file


def _prepare_output_path(output_path, overwrite_existing=False):
    """Prepare output path for writing and optionally replace existing files."""
    if not os.path.exists(output_path):
        return True
    if os.path.isdir(output_path):
        return False
    if not overwrite_existing:
        return False
    try:
        os.remove(output_path)
        return True
    except Exception:
        return False


def retag_file(filepath, artist, title, album=None, enhanced_metadata=None, cover_data=None):
    """Update tags in an existing audio file without re-encoding.

    Detects format from file extension and uses the appropriate mutagen
    class.  Does NOT re-encode audio — this is a metadata-only update.
    """
    ext = os.path.splitext(filepath)[1].lower()
    metadata = enhanced_metadata if isinstance(enhanced_metadata, dict) else {}
    release_date = str(metadata.get("release_date") or "").strip()
    year = release_date[:4] if release_date else ""
    genres = metadata.get("genres") or []
    if isinstance(genres, str):
        genres = [genres]
    genres = [str(value).strip() for value in list(genres) if str(value).strip()]
    genre_text = ", ".join(genres)
    label = str(metadata.get("label") or "").strip()
    isrc = str(metadata.get("isrc") or "").strip()
    bpm_value = metadata.get("bpm")
    bpm_text = str(bpm_value).strip() if bpm_value is not None else ""
    bpm_int = None
    if bpm_text:
        try:
            bpm_int = int(float(bpm_text))
        except Exception:
            bpm_int = None

    try:
        if ext == '.flac':
            from mutagen.flac import FLAC, Picture
            audio = FLAC(filepath)
            audio["artist"] = artist
            audio["title"] = title
            if album:
                audio["album"] = album
            if year:
                audio["date"] = year
            if genre_text:
                audio["genre"] = genre_text
            if label:
                audio["label"] = label
            if isrc:
                audio["isrc"] = isrc
            if bpm_text:
                audio["bpm"] = bpm_text
            if cover_data:
                try:
                    audio.clear_pictures()
                except Exception:
                    pass
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = u"image/jpeg"
                audio.add_picture(pic)
            audio.save()

        elif ext in ('.m4a', '.mp4', '.aac'):
            from mutagen.mp4 import MP4, MP4Cover
            mp4 = MP4(filepath)
            mp4["\xa9ART"] = [artist]
            mp4["\xa9nam"] = [title]
            if album:
                mp4["\xa9alb"] = [album]
            if year:
                mp4["\xa9day"] = [year]
            if genre_text:
                mp4["\xa9gen"] = [genre_text]
            if bpm_int is not None and bpm_int > 0:
                mp4["tmpo"] = [bpm_int]
            if cover_data:
                mp4["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            mp4.save()

        elif ext == '.mp3':
            from mutagen.id3 import (
                ID3,
                ID3NoHeaderError,
                TIT2,
                TPE1,
                TALB,
                TDRC,
                TCON,
                TBPM,
                TSRC,
                TPUB,
                APIC,
            )
            try:
                tags = ID3(filepath)
            except ID3NoHeaderError:
                tags = ID3()

            for frame in ("TPE1", "TIT2", "TALB", "TDRC", "TCON", "TBPM", "TSRC", "TPUB"):
                tags.delall(frame)
            tags.add(TPE1(encoding=3, text=[artist]))
            tags.add(TIT2(encoding=3, text=[title]))
            if album:
                tags.add(TALB(encoding=3, text=[album]))
            if year:
                tags.add(TDRC(encoding=3, text=[year]))
            if genre_text:
                tags.add(TCON(encoding=3, text=[genre_text]))
            if bpm_text:
                tags.add(TBPM(encoding=3, text=[bpm_text]))
            if isrc:
                tags.add(TSRC(encoding=3, text=[isrc]))
            if label:
                tags.add(TPUB(encoding=3, text=[label]))
            if cover_data:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_data))
            tags.save(filepath)

        elif ext == '.ogg':
            import base64
            from mutagen.flac import Picture
            from mutagen.oggvorbis import OggVorbis
            ogg = OggVorbis(filepath)
            ogg["artist"] = [artist]
            ogg["title"] = [title]
            if album:
                ogg["album"] = [album]
            if year:
                ogg["date"] = [year]
            if genre_text:
                ogg["genre"] = [genre_text]
            if bpm_text:
                ogg["bpm"] = [bpm_text]
            if isrc:
                ogg["isrc"] = [isrc]
            if label:
                ogg["label"] = [label]
            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = u"image/jpeg"
                pic.width = 600
                pic.height = 600
                ogg["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            ogg.save()

        elif ext == '.opus':
            import base64
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            opus = OggOpus(filepath)
            opus["artist"] = [artist]
            opus["title"] = [title]
            if album:
                opus["album"] = [album]
            if year:
                opus["date"] = [year]
            if genre_text:
                opus["genre"] = [genre_text]
            if bpm_text:
                opus["bpm"] = [bpm_text]
            if isrc:
                opus["isrc"] = [isrc]
            if label:
                opus["label"] = [label]
            if cover_data:
                pic = Picture()
                pic.data = cover_data
                pic.type = 3
                pic.mime = u"image/jpeg"
                pic.width = 600
                pic.height = 600
                opus["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            opus.save()

        elif ext in ('.wav', '.aiff', '.aif'):
            # WAV/AIFF have limited ID3 support — best-effort, don't crash
            try:
                from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TCON, TBPM

                try:
                    tags = ID3(filepath)
                except ID3NoHeaderError:
                    tags = ID3()
                for frame in ("TPE1", "TIT2", "TALB", "TDRC", "TCON", "TBPM"):
                    tags.delall(frame)
                tags.add(TPE1(encoding=3, text=[artist]))
                tags.add(TIT2(encoding=3, text=[title]))
                if album:
                    tags.add(TALB(encoding=3, text=[album]))
                if year:
                    tags.add(TDRC(encoding=3, text=[year]))
                if genre_text:
                    tags.add(TCON(encoding=3, text=[genre_text]))
                if bpm_text:
                    tags.add(TBPM(encoding=3, text=[bpm_text]))
                tags.save(filepath)
            except Exception:
                pass  # silently skip — these formats often lack tag support

    except Exception as e:
        raise RuntimeError(f"retag failed for {os.path.basename(filepath)}: {e}")


def _fetch_cover_art_data(cover_url, artwork_cache=None):
    """Return artwork bytes for cover_url if available."""
    url = str(cover_url or "").strip()
    if not url:
        return None
    if "{w}x{h}" in url:
        url = url.replace("{w}x{h}", "600x600")
    if isinstance(artwork_cache, dict) and url in artwork_cache:
        return artwork_cache[url]
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200 and response.content:
            if isinstance(artwork_cache, dict):
                artwork_cache[url] = response.content
            return response.content
    except Exception:
        pass
    return None


# Audio format definitions
AUDIO_FORMATS = {
    'flac': {'name': 'FLAC', 'ext': '.flac', 'lossless': True, 'codec': None, 'mutagen': 'flac'},
    'alac': {'name': 'ALAC (M4A)', 'ext': '.m4a', 'lossless': True, 'codec': 'alac', 'mutagen': 'mp4'},
    'wav': {'name': 'WAV', 'ext': '.wav', 'lossless': True, 'codec': 'pcm_s16le', 'mutagen': 'wave'},
    'aiff': {'name': 'AIFF', 'ext': '.aiff', 'lossless': True, 'codec': 'pcm_s16be', 'mutagen': 'aiff'},
    'mp3_320': {'name': 'MP3 320kbps', 'ext': '.mp3', 'lossless': False, 'codec': 'libmp3lame', 'bitrate': '320k', 'mutagen': 'mp3'},
    'mp3_256': {'name': 'MP3 256kbps', 'ext': '.mp3', 'lossless': False, 'codec': 'libmp3lame', 'bitrate': '256k', 'mutagen': 'mp3'},
    'mp3_192': {'name': 'MP3 192kbps', 'ext': '.mp3', 'lossless': False, 'codec': 'libmp3lame', 'bitrate': '192k', 'mutagen': 'mp3'},
    'aac_256': {'name': 'AAC 256kbps', 'ext': '.m4a', 'lossless': False, 'codec': 'aac', 'bitrate': '256k', 'mutagen': 'mp4'},
    'ogg_500': {'name': 'OGG Vorbis Q10 (~500kbps)', 'ext': '.ogg', 'lossless': False, 'codec': 'libvorbis', 'quality': '10', 'mutagen': 'ogg'},
    'ogg_320': {'name': 'OGG Vorbis Q8 (~320kbps)', 'ext': '.ogg', 'lossless': False, 'codec': 'libvorbis', 'quality': '8', 'mutagen': 'ogg'},
    'opus': {'name': 'OPUS 256kbps', 'ext': '.opus', 'lossless': False, 'codec': 'libopus', 'bitrate': '256k', 'mutagen': 'opus'},
}

_DUPLICATE_POLICY_SKIP = "skip"
_DUPLICATE_POLICY_OVERWRITE = "overwrite"
_DUPLICATE_POLICY_KEEP_BEST_QUALITY = "keep_best_quality"

_LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif"}
_LOSSLESS_FORMAT_PREFERENCE = {
    "flac": 40,
    "alac": 35,
    "wav": 20,
    "aiff": 15,
}


def _normalize_duplicate_policy(duplicate_policy=None, overwrite_existing=False):
    """Resolve duplicate policy from explicit value (or legacy overwrite flag)."""
    if duplicate_policy is None:
        return _DUPLICATE_POLICY_OVERWRITE if bool(overwrite_existing) else _DUPLICATE_POLICY_SKIP
    try:
        from mixsplitr_core import normalize_duplicate_policy as _normalize

        return str(_normalize(duplicate_policy))
    except Exception:
        parsed = str(duplicate_policy or "").strip().lower()
        if parsed in ("best", "best_quality", "highest_quality", "quality", "dupeguru"):
            return _DUPLICATE_POLICY_KEEP_BEST_QUALITY
        if parsed in (_DUPLICATE_POLICY_SKIP, _DUPLICATE_POLICY_OVERWRITE, _DUPLICATE_POLICY_KEEP_BEST_QUALITY):
            return parsed
        return _DUPLICATE_POLICY_SKIP


def _parse_bitrate_kbps(value):
    """Parse bitrate-like values into integer kbps."""
    if value is None:
        return 0
    text = str(value).strip().lower()
    if not text:
        return 0
    try:
        direct = float(text)
        if direct > 0:
            return int(round(direct))
    except Exception:
        pass
    match = re.search(r'(\d+(?:\.\d+)?)\s*k', text)
    if match:
        return int(round(float(match.group(1))))
    match = re.search(r'(\d{2,4})\s*kbps', text)
    if match:
        return int(match.group(1))
    return 0


def _estimate_format_bitrate_kbps(format_key):
    fmt = AUDIO_FORMATS.get(format_key) or {}
    bitrate = _parse_bitrate_kbps(fmt.get("bitrate"))
    if bitrate > 0:
        return bitrate
    name_bitrate = _parse_bitrate_kbps(fmt.get("name"))
    if name_bitrate > 0:
        return name_bitrate
    return 0


def _known_audio_extensions():
    exts = {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".mp4"}
    for fmt in AUDIO_FORMATS.values():
        ext = str(fmt.get("ext") or "").strip().lower()
        if ext:
            exts.add(ext)
    return exts


_KNOWN_AUDIO_EXTENSIONS = _known_audio_extensions()


def _extract_audio_metrics(path):
    """Best-effort read of quality-related metrics for an existing file."""
    ext = str(os.path.splitext(path)[1] or "").strip().lower()
    metrics = {
        "ext": ext,
        "codec": "",
        "bitrate_kbps": 0,
        "sample_rate": 0,
        "bits_per_sample": 0,
        "channels": 0,
        "lossless": bool(ext in _LOSSLESS_EXTENSIONS),
        "size": 0,
        "mtime": 0.0,
    }
    try:
        stat = os.stat(path)
        metrics["size"] = int(stat.st_size)
        metrics["mtime"] = float(stat.st_mtime)
    except Exception:
        pass

    try:
        from mutagen import File as _MutagenFile

        audio = _MutagenFile(path)
        info = getattr(audio, "info", None)
        if info is not None:
            codec = str(getattr(info, "codec", "") or "").strip().lower()
            if codec:
                metrics["codec"] = codec
            try:
                bitrate = int(round(float(getattr(info, "bitrate", 0) or 0) / 1000.0))
            except Exception:
                bitrate = 0
            if bitrate > 0:
                metrics["bitrate_kbps"] = bitrate
            try:
                sample_rate = int(getattr(info, "sample_rate", 0) or 0)
            except Exception:
                sample_rate = 0
            if sample_rate > 0:
                metrics["sample_rate"] = sample_rate
            try:
                bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
            except Exception:
                bits_per_sample = 0
            if bits_per_sample > 0:
                metrics["bits_per_sample"] = bits_per_sample
            try:
                channels = int(getattr(info, "channels", 0) or 0)
            except Exception:
                channels = 0
            if channels > 0:
                metrics["channels"] = channels
            if hasattr(info, "lossless"):
                metrics["lossless"] = bool(getattr(info, "lossless", False))
    except Exception:
        pass

    codec = str(metrics.get("codec") or "").lower()
    if "alac" in codec:
        metrics["lossless"] = True
    if ext in _LOSSLESS_EXTENSIONS:
        metrics["lossless"] = True
    return metrics


def _infer_format_key(ext, codec="", bitrate_kbps=0):
    candidates = [k for k, v in AUDIO_FORMATS.items() if str(v.get("ext") or "").lower() == str(ext or "").lower()]
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    codec_text = str(codec or "").lower()
    if ext == ".m4a":
        if "alac" in codec_text:
            return "alac"
        return "aac_256"

    if bitrate_kbps > 0:
        best_key = candidates[0]
        best_delta = None
        for key in candidates:
            expected = _estimate_format_bitrate_kbps(key)
            if expected <= 0:
                continue
            delta = abs(int(expected) - int(bitrate_kbps))
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_key = key
        return best_key
    return candidates[0]


def _format_preference_score(format_key, ext, lossless, codec=""):
    format_key = str(format_key or "").strip().lower()
    ext = str(ext or "").strip().lower()
    codec = str(codec or "").strip().lower()
    if lossless:
        if format_key in _LOSSLESS_FORMAT_PREFERENCE:
            return int(_LOSSLESS_FORMAT_PREFERENCE[format_key])
        if ext == ".flac":
            return 40
        if ext == ".m4a" and "alac" in codec:
            return 35
        if ext == ".wav":
            return 20
        if ext in (".aiff", ".aif"):
            return 15
        return 10

    # Lossy tie-break preference (used after bitrate/sample-rate).
    if format_key.startswith("opus") or "opus" in codec or ext == ".opus":
        return 40
    if format_key.startswith("ogg") or "vorbis" in codec or ext == ".ogg":
        return 30
    if format_key.startswith("aac") or "aac" in codec or ext in (".aac", ".m4a", ".mp4"):
        return 25
    if format_key.startswith("mp3") or "mp3" in codec or ext == ".mp3":
        return 20
    return 10


def _build_existing_quality(path):
    metrics = _extract_audio_metrics(path)
    format_key = _infer_format_key(
        metrics.get("ext", ""),
        codec=metrics.get("codec", ""),
        bitrate_kbps=metrics.get("bitrate_kbps", 0),
    )
    bitrate_kbps = int(metrics.get("bitrate_kbps", 0) or 0)
    if bitrate_kbps <= 0 and format_key:
        bitrate_kbps = _estimate_format_bitrate_kbps(format_key)
    return {
        "path": path,
        "format_key": format_key,
        "ext": metrics.get("ext", ""),
        "codec": metrics.get("codec", ""),
        "lossless": bool(metrics.get("lossless", False)),
        "bitrate_kbps": int(bitrate_kbps),
        "bits_per_sample": int(metrics.get("bits_per_sample", 0) or 0),
        "sample_rate": int(metrics.get("sample_rate", 0) or 0),
        "size": int(metrics.get("size", 0) or 0),
        "mtime": float(metrics.get("mtime", 0.0) or 0.0),
    }


def _build_incoming_quality(source_path, output_format):
    fmt = AUDIO_FORMATS.get(output_format, AUDIO_FORMATS.get("flac", {}))
    source_metrics = _extract_audio_metrics(source_path)
    ext = str(fmt.get("ext") or ".flac").strip().lower()
    codec = str(fmt.get("codec") or "").strip().lower()
    lossless = bool(fmt.get("lossless", False))
    bitrate_kbps = int(_estimate_format_bitrate_kbps(output_format))
    if not lossless and bitrate_kbps <= 0:
        bitrate_kbps = int(source_metrics.get("bitrate_kbps", 0) or 0)
    return {
        "path": source_path,
        "format_key": output_format,
        "ext": ext,
        "codec": codec,
        "lossless": lossless,
        "bitrate_kbps": int(bitrate_kbps),
        "bits_per_sample": int(source_metrics.get("bits_per_sample", 0) or 0),
        "sample_rate": int(source_metrics.get("sample_rate", 0) or 0),
        "size": int(source_metrics.get("size", 0) or 0),
        "mtime": float(source_metrics.get("mtime", 0.0) or 0.0),
    }


def _quality_rank(quality):
    lossless = bool((quality or {}).get("lossless", False))
    bits_per_sample = int((quality or {}).get("bits_per_sample", 0) or 0)
    sample_rate = int((quality or {}).get("sample_rate", 0) or 0)
    bitrate_kbps = int((quality or {}).get("bitrate_kbps", 0) or 0)
    size = int((quality or {}).get("size", 0) or 0)
    format_pref = _format_preference_score(
        (quality or {}).get("format_key", ""),
        (quality or {}).get("ext", ""),
        lossless,
        codec=(quality or {}).get("codec", ""),
    )
    if lossless:
        if bits_per_sample <= 0:
            bits_per_sample = 16
        if sample_rate <= 0:
            sample_rate = 44100
        return (2, bits_per_sample, sample_rate, format_pref, size)
    if bitrate_kbps <= 0:
        bitrate_kbps = 128
    if sample_rate <= 0:
        sample_rate = 44100
    return (1, bitrate_kbps, sample_rate, format_pref, size)


def _find_same_track_candidates(dest_dir, target_stem):
    if not os.path.isdir(dest_dir):
        return []
    norm_stem = str(target_stem or "").strip().lower()
    matches = []
    try:
        for name in os.listdir(dest_dir):
            ext = str(os.path.splitext(name)[1] or "").strip().lower()
            if ext not in _KNOWN_AUDIO_EXTENSIONS:
                continue
            stem = str(os.path.splitext(name)[0] or "").strip().lower()
            if stem != norm_stem:
                continue
            path = os.path.join(dest_dir, name)
            if os.path.isfile(path):
                matches.append(path)
    except Exception:
        return []
    return matches


def _cleanup_paths(paths):
    removed = []
    for path in paths or []:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
                removed.append(path)
        except Exception:
            continue
    return removed


def _resolve_best_quality_action(dest_dir, filename, incoming_quality):
    target_stem = str(os.path.splitext(filename)[0] or "").strip().lower()
    existing_paths = _find_same_track_candidates(dest_dir, target_stem)
    if not existing_paths:
        return {"skip_incoming": False, "remove_paths": [], "kept_path": ""}

    existing_entries = []
    for path in existing_paths:
        quality = _build_existing_quality(path)
        existing_entries.append({
            "path": path,
            "quality": quality,
            "rank": _quality_rank(quality),
        })

    if not existing_entries:
        return {"skip_incoming": False, "remove_paths": [], "kept_path": ""}

    incoming_rank = _quality_rank(incoming_quality)
    best_existing = max(
        existing_entries,
        key=lambda entry: (entry["rank"], float(entry["quality"].get("mtime", 0.0)), entry["path"].lower()),
    )
    best_existing_rank = best_existing["rank"]

    if incoming_rank > best_existing_rank:
        return {
            "skip_incoming": False,
            "remove_paths": [entry["path"] for entry in existing_entries],
            "kept_path": "",
        }

    # Keep best existing file; remove weaker duplicates and skip incoming.
    keep_path = best_existing["path"]
    remove_paths = [entry["path"] for entry in existing_entries if entry["path"] != keep_path]
    return {
        "skip_incoming": True,
        "remove_paths": remove_paths,
        "kept_path": keep_path,
    }


def embed_and_sort_flac(
    file_path,
    artist,
    title,
    album,
    cover_url,
    base_output_folder,
    artwork_cache=None,
    enhanced_metadata=None,
    overwrite_existing=False,
    rename_preset=None,
):
    """Embed metadata in FLAC file and move to artist folder"""
    from mutagen.flac import FLAC, Picture

    # Apply artist normalization if enabled
    artist, title = _maybe_normalize(artist, title)

    try:
        audio = FLAC(file_path)
        audio["artist"], audio["title"], audio["album"] = artist, title, album
        
        # Add enhanced metadata if available
        if enhanced_metadata:
            if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                date = enhanced_metadata['release_date']
                year = date[:4] if len(date) >= 4 else date
                audio["date"] = year
            
            if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                audio["genre"] = ", ".join(enhanced_metadata['genres'])
            
            if 'label' in enhanced_metadata and enhanced_metadata['label']:
                audio["label"] = enhanced_metadata['label']
            
            if 'isrc' in enhanced_metadata and enhanced_metadata['isrc']:
                audio["isrc"] = enhanced_metadata['isrc']
            
            if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                audio["bpm"] = str(enhanced_metadata['bpm'])
        
                # Handle artwork (prefer embedded artwork if present)
        img_data = None

        # If the file already has embedded artwork, keep it and skip online fetching
        try:
            if getattr(audio, "pictures", None) and len(audio.pictures) > 0:
                img_data = audio.pictures[0].data
        except Exception:
            pass

        # Only fetch/download artwork if nothing is embedded already
        if img_data is None and cover_url:
            if "{w}x{h}" in cover_url:
                cover_url = cover_url.replace("{w}x{h}", "600x600")

            # Try cache first
            if artwork_cache and cover_url in artwork_cache:
                img_data = artwork_cache[cover_url]
            else:
                try:
                    img_res = requests.get(cover_url, timeout=10)
                    if img_res.status_code == 200:
                        img_data = img_res.content
                except Exception:
                    pass

            if img_data:
                pic = Picture()
                pic.data, pic.type, pic.mime = img_data, 3, u"image/jpeg"
                audio.add_picture(pic)

        audio.save()
        
        # Move file to artist folder
        safe_artist, new_name = _resolve_export_naming(
            artist, title, ".flac", album=album, enhanced_metadata=enhanced_metadata, rename_preset=rename_preset,
            track_number=(enhanced_metadata or {}).get("track_number")
        )
        dest_dir = os.path.join(base_output_folder, safe_artist)
        os.makedirs(dest_dir, exist_ok=True)
        
        # On macOS, create folder.jpg for Finder compatibility
        if platform.system() == 'Darwin' and img_data:
            art_path = os.path.join(dest_dir, "folder.jpg")
            if not os.path.exists(art_path):
                with open(art_path, "wb") as f:
                    f.write(img_data)

        output_path = os.path.join(dest_dir, new_name)
        if not _prepare_output_path(output_path, overwrite_existing=overwrite_existing):
            print(f"   [i] Skipped existing file: {os.path.basename(output_path)}")
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
            return None
        shutil.move(file_path, output_path)
        return output_path
    except Exception as e: 
        print(f"   [!] Tag Error: {e}")
        return None


def embed_and_sort_alac(
    file_path,
    artist,
    title,
    album,
    cover_url,
    base_output_folder,
    artwork_cache=None,
    enhanced_metadata=None,
    overwrite_existing=False,
    rename_preset=None,
):
    """Convert FLAC to ALAC and embed metadata for macOS compatibility"""
    from pydub import AudioSegment

    # Apply artist normalization if enabled
    artist, title = _maybe_normalize(artist, title)

    try:
        from mutagen.mp4 import MP4, MP4Cover
    except ImportError:
        print("   [!] mutagen.mp4 not available, falling back to FLAC")
        return embed_and_sort_flac(
            file_path,
            artist,
            title,
            album,
            cover_url,
            base_output_folder,
            artwork_cache,
            enhanced_metadata,
            overwrite_existing=overwrite_existing,
            rename_preset=rename_preset,
        )

    try:
        # Setup output path
        safe_artist, new_name = _resolve_export_naming(
            artist, title, ".m4a", album=album, enhanced_metadata=enhanced_metadata, rename_preset=rename_preset,
            track_number=(enhanced_metadata or {}).get("track_number")
        )
        dest_dir = os.path.join(base_output_folder, safe_artist)
        os.makedirs(dest_dir, exist_ok=True)
        output_path = os.path.join(dest_dir, new_name)
        if not _prepare_output_path(output_path, overwrite_existing=overwrite_existing):
            print(f"   [i] Skipped existing file: {os.path.basename(output_path)}")
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
            return None
        
        # Prefer embedded artwork from the source FLAC before conversion
        # (the conversion step does not preserve embedded pictures automatically)
        img_data = None
        try:
            from mutagen.flac import FLAC as _FLAC
            src_flac = _FLAC(file_path)
            if getattr(src_flac, "pictures", None) and len(src_flac.pictures) > 0:
                img_data = src_flac.pictures[0].data
        except Exception:
            img_data = None

        # Load and convert to ALAC
        audio = AudioSegment.from_file(file_path)
        audio.export(output_path, format="ipod", codec="alac")
        
        # Remove original FLAC
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Add metadata using mutagen MP4
        mp4 = MP4(output_path)
        mp4["\xa9nam"] = title      # Title
        mp4["\xa9ART"] = artist     # Artist  
        mp4["\xa9alb"] = album      # Album
        
        # Add enhanced metadata
        if enhanced_metadata:
            if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                date = enhanced_metadata['release_date']
                year = date[:4] if len(date) >= 4 else date
                mp4["\xa9day"] = year
            
            if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                mp4["\xa9gen"] = ", ".join(enhanced_metadata['genres'])
            
            if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                try:
                    mp4["tmpo"] = [int(enhanced_metadata['bpm'])]
                except ValueError:
                    pass
        
                # Embed artwork (prefer embedded art from source FLAC)
        # If img_data was captured from the source FLAC, use it.
        # Otherwise, fall back to cached/downloaded cover_url.
        if img_data is None and cover_url:
            if "{w}x{h}" in cover_url:
                cover_url = cover_url.replace("{w}x{h}", "600x600")

            if artwork_cache and cover_url in artwork_cache:
                img_data = artwork_cache[cover_url]
            else:
                try:
                    img_res = requests.get(cover_url, timeout=10)
                    if img_res.status_code == 200:
                        img_data = img_res.content
                except Exception:
                    pass

        if img_data:
            mp4["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]

        mp4.save()
        return output_path
        
    except Exception as e:
        print(f"   [!] ALAC Conversion Error: {e}")
        # Fall back to FLAC if ALAC fails
        return embed_and_sort_flac(
            file_path,
            artist,
            title,
            album,
            cover_url,
            base_output_folder,
            artwork_cache,
            enhanced_metadata,
            overwrite_existing=overwrite_existing,
            rename_preset=rename_preset,
        )


def embed_and_sort_generic(
    file_path,
    artist,
    title,
    album,
    cover_url,
    base_output_folder,
    output_format='flac',
    artwork_cache=None,
    enhanced_metadata=None,
    overwrite_existing=False,
    duplicate_policy=None,
    source_file_path=None,
    preserve_source_format=False,
    rename_preset=None,
):
    """Generic function to convert and tag audio to any supported format"""
    from pydub import AudioSegment

    def _discard_temp_source():
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    # Apply artist normalization if enabled (do it here so the FLAC/ALAC
    # fast-paths below receive already-normalized values)
    artist, title = _maybe_normalize(artist, title)

    output_format = str(output_format or "flac").strip().lower()
    if output_format not in AUDIO_FORMATS:
        print(f"   [!] Unknown format {output_format}, falling back to FLAC")
        output_format = "flac"

    fmt = AUDIO_FORMATS[output_format]
    duplicate_policy_value = _normalize_duplicate_policy(
        duplicate_policy=duplicate_policy,
        overwrite_existing=overwrite_existing,
    )
    overwrite_for_write = bool(duplicate_policy_value == _DUPLICATE_POLICY_OVERWRITE)
    source_path = str(source_file_path or "").strip()
    source_exists = bool(source_path and os.path.isfile(source_path))
    source_ext = str(os.path.splitext(source_path)[1] or "").strip().lower()
    preserve_source_possible = bool(
        preserve_source_format
        and source_exists
        and source_ext in {".flac", ".m4a", ".mp4", ".aac", ".mp3", ".ogg", ".opus", ".wav", ".aiff", ".aif"}
    )

    try:
        if preserve_source_possible:
            track_number = (enhanced_metadata or {}).get("track_number")
            safe_artist, new_name = _resolve_export_naming(
                artist,
                title,
                source_ext,
                album=album,
                enhanced_metadata=enhanced_metadata,
                rename_preset=rename_preset,
                track_number=track_number,
            )
            dest_dir = os.path.join(base_output_folder, safe_artist)
            os.makedirs(dest_dir, exist_ok=True)
            output_path = os.path.join(dest_dir, new_name)

            source_abs = os.path.normcase(os.path.abspath(source_path))
            output_abs = os.path.normcase(os.path.abspath(output_path))
            same_source_and_dest = bool(source_abs == output_abs)

            if duplicate_policy_value == _DUPLICATE_POLICY_KEEP_BEST_QUALITY:
                incoming_quality = _build_existing_quality(source_path)
                best_quality_action = _resolve_best_quality_action(dest_dir, new_name, incoming_quality)
                removed_paths = _cleanup_paths(best_quality_action.get("remove_paths", []))
                if removed_paths:
                    print(f"   [i] Removed {len(removed_paths)} lower-quality duplicate(s)")
                if best_quality_action.get("skip_incoming"):
                    kept_path = str(best_quality_action.get("kept_path") or "").strip()
                    kept_abs = os.path.normcase(os.path.abspath(kept_path)) if kept_path else ""
                    if kept_abs == source_abs:
                        output_path = kept_path or output_path
                        same_source_and_dest = bool(
                            os.path.normcase(os.path.abspath(output_path)) == source_abs
                        )
                    else:
                        kept_name = os.path.basename(kept_path or output_path)
                        print(f"   [i] Skipped duplicate (kept highest quality): {kept_name}")
                        _discard_temp_source()
                        return None
                overwrite_for_write = True

            if not same_source_and_dest:
                if not _prepare_output_path(output_path, overwrite_existing=overwrite_for_write):
                    print(f"   [i] Skipped existing file: {os.path.basename(output_path)}")
                    _discard_temp_source()
                    return None
                shutil.copy2(source_path, output_path)

            img_data = _fetch_cover_art_data(cover_url, artwork_cache=artwork_cache)
            try:
                retag_file(
                    output_path,
                    artist,
                    title,
                    album=album,
                    enhanced_metadata=enhanced_metadata,
                    cover_data=img_data,
                )
                _discard_temp_source()
                return output_path
            except Exception as tag_exc:
                print(f"   [!] Metadata-only retag failed; falling back to re-encode: {tag_exc}")
                if not same_source_and_dest:
                    try:
                        if os.path.exists(output_path):
                            os.remove(output_path)
                    except Exception:
                        pass

        # Setup output path
        track_number = (enhanced_metadata or {}).get("track_number")
        safe_artist, new_name = _resolve_export_naming(
            artist,
            title,
            fmt['ext'],
            album=album,
            enhanced_metadata=enhanced_metadata,
            rename_preset=rename_preset,
            track_number=track_number,
        )
        dest_dir = os.path.join(base_output_folder, safe_artist)
        os.makedirs(dest_dir, exist_ok=True)
        output_path = os.path.join(dest_dir, new_name)

        if duplicate_policy_value == _DUPLICATE_POLICY_KEEP_BEST_QUALITY:
            incoming_quality = _build_incoming_quality(file_path, output_format)
            best_quality_action = _resolve_best_quality_action(dest_dir, new_name, incoming_quality)
            removed_paths = _cleanup_paths(best_quality_action.get("remove_paths", []))
            if removed_paths:
                print(f"   [i] Removed {len(removed_paths)} lower-quality duplicate(s)")
            if best_quality_action.get("skip_incoming"):
                kept_name = os.path.basename(best_quality_action.get("kept_path") or output_path)
                print(f"   [i] Skipped duplicate (kept highest quality): {kept_name}")
                _discard_temp_source()
                return None
            # Incoming track wins; allow replacement if the exact path still exists.
            overwrite_for_write = True

        # Handle legacy fast paths once duplicate policy has been resolved.
        if output_format == 'alac':
            return embed_and_sort_alac(
                file_path,
                artist,
                title,
                album,
                cover_url,
                base_output_folder,
                artwork_cache,
                enhanced_metadata,
                overwrite_existing=overwrite_for_write,
                rename_preset=rename_preset,
            )
        if output_format == 'flac':
            return embed_and_sort_flac(
                file_path,
                artist,
                title,
                album,
                cover_url,
                base_output_folder,
                artwork_cache,
                enhanced_metadata,
                overwrite_existing=overwrite_for_write,
                rename_preset=rename_preset,
            )

        if not _prepare_output_path(output_path, overwrite_existing=overwrite_for_write):
            print(f"   [i] Skipped existing file: {os.path.basename(output_path)}")
            _discard_temp_source()
            return None

        # Extract embedded artwork from source FLAC
        img_data = None
        try:
            from mutagen.flac import FLAC as _FLAC
            src_flac = _FLAC(file_path)
            if getattr(src_flac, "pictures", None) and len(src_flac.pictures) > 0:
                img_data = src_flac.pictures[0].data
        except Exception:
            pass

        # Fetch artwork if not embedded
        if img_data is None and cover_url:
            img_data = _fetch_cover_art_data(cover_url, artwork_cache=artwork_cache)

        # Load audio
        audio = AudioSegment.from_file(file_path)

        # Export with format-specific parameters
        export_params = {}
        if fmt['codec']:
            export_params['codec'] = fmt['codec']
        if 'bitrate' in fmt:
            export_params['bitrate'] = fmt['bitrate']
        if 'quality' in fmt:
            export_params['parameters'] = ['-q:a', fmt['quality']]

        # Determine pydub format
        pydub_format = output_format.split('_')[0]  # mp3_320 -> mp3
        if output_format == 'alac':
            pydub_format = 'ipod'

        audio.export(output_path, format=pydub_format, **export_params)

        # Remove original FLAC
        if os.path.exists(file_path):
            os.remove(file_path)

        # Tag the file using mutagen
        mutagen_type = fmt['mutagen']

        if mutagen_type == 'mp3':
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, APIC, TBPM
            try:
                audio_file = ID3(output_path)
            except Exception:
                from mutagen.id3 import ID3NoHeaderError
                audio_file = ID3()

            audio_file.add(TIT2(encoding=3, text=title))
            audio_file.add(TPE1(encoding=3, text=artist))
            audio_file.add(TALB(encoding=3, text=album))

            if enhanced_metadata:
                if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                    year = enhanced_metadata['release_date'][:4]
                    audio_file.add(TDRC(encoding=3, text=year))
                if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                    audio_file.add(TCON(encoding=3, text=", ".join(enhanced_metadata['genres'])))
                if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                    audio_file.add(TBPM(encoding=3, text=str(enhanced_metadata['bpm'])))

            if img_data:
                audio_file.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))

            audio_file.save(output_path)

        elif mutagen_type == 'mp4':
            from mutagen.mp4 import MP4, MP4Cover
            mp4 = MP4(output_path)
            mp4["\xa9nam"] = title
            mp4["\xa9ART"] = artist
            mp4["\xa9alb"] = album

            if enhanced_metadata:
                if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                    mp4["\xa9day"] = enhanced_metadata['release_date'][:4]
                if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                    mp4["\xa9gen"] = ", ".join(enhanced_metadata['genres'])
                if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                    try:
                        mp4["tmpo"] = [int(enhanced_metadata['bpm'])]
                    except ValueError:
                        pass

            if img_data:
                mp4["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]

            mp4.save()

        elif mutagen_type == 'ogg':
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import Picture
            import base64

            ogg = OggVorbis(output_path)
            ogg["title"] = title
            ogg["artist"] = artist
            ogg["album"] = album

            if enhanced_metadata:
                if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                    ogg["date"] = enhanced_metadata['release_date'][:4]
                if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                    ogg["genre"] = ", ".join(enhanced_metadata['genres'])
                if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                    ogg["bpm"] = str(enhanced_metadata['bpm'])

            if img_data:
                pic = Picture()
                pic.data = img_data
                pic.type = 3
                pic.mime = u"image/jpeg"
                pic.width = 600
                pic.height = 600
                ogg["metadata_block_picture"] = [base64.b64encode(pic.write()).decode('ascii')]

            ogg.save()

        elif mutagen_type == 'opus':
            from mutagen.oggopus import OggOpus
            from mutagen.flac import Picture
            import base64

            opus = OggOpus(output_path)
            opus["title"] = title
            opus["artist"] = artist
            opus["album"] = album

            if enhanced_metadata:
                if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                    opus["date"] = enhanced_metadata['release_date'][:4]
                if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                    opus["genre"] = ", ".join(enhanced_metadata['genres'])
                if 'bpm' in enhanced_metadata and enhanced_metadata['bpm']:
                    opus["bpm"] = str(enhanced_metadata['bpm'])

            if img_data:
                pic = Picture()
                pic.data = img_data
                pic.type = 3
                pic.mime = u"image/jpeg"
                pic.width = 600
                pic.height = 600
                opus["metadata_block_picture"] = [base64.b64encode(pic.write()).decode('ascii')]

            opus.save()

        elif mutagen_type in ['wave', 'aiff']:
            # WAV and AIFF have limited tagging support via ID3
            try:
                from mutagen.id3 import ID3, TIT2, TPE1, TALB
                audio_file = ID3(output_path)
                audio_file.add(TIT2(encoding=3, text=title))
                audio_file.add(TPE1(encoding=3, text=artist))
                audio_file.add(TALB(encoding=3, text=album))
                audio_file.save(output_path)
            except Exception:
                pass  # WAV/AIFF tagging is optional

        # On macOS, create folder.jpg
        if platform.system() == 'Darwin' and img_data:
            art_path = os.path.join(dest_dir, "folder.jpg")
            if not os.path.exists(art_path):
                with open(art_path, "wb") as f:
                    f.write(img_data)

        return output_path

    except Exception as e:
        print(f"   [!] Format Error ({output_format}): {e}")
        # Fall back to FLAC
        return embed_and_sort_flac(
            file_path,
            artist,
            title,
            album,
            cover_url,
            base_output_folder,
            artwork_cache,
            enhanced_metadata,
            overwrite_existing=overwrite_for_write,
            rename_preset=rename_preset,
        )
