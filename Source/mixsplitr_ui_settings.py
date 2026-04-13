import json
import os
import sys


def mode_values(core_module):
    return {
        "Split Only (No ID)": getattr(core_module, "MODE_SPLIT_ONLY", "split_only_no_id"),
        "MusicBrainz / AcoustID": getattr(core_module, "MODE_MB_ONLY", "musicbrainz_only"),
        "ACRCloud": getattr(core_module, "MODE_ACRCLOUD", "acrcloud"),
        "Dual (ACRCloud + AcoustID)": getattr(core_module, "MODE_DUAL", "dual_best_match"),
        "Timestamping Mode (Auto Tracklist)": getattr(
            core_module, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual"
        ),
    }


def split_sensitivity_bounds(core_module):
    min_db = -10
    max_db = 10
    if core_module is not None:
        try:
            min_db = int(getattr(core_module, "SPLIT_SENSITIVITY_MIN_DB", min_db))
            max_db = int(getattr(core_module, "SPLIT_SENSITIVITY_MAX_DB", max_db))
        except Exception:
            min_db, max_db = -10, 10
    if min_db > max_db:
        min_db, max_db = max_db, min_db
    return min_db, max_db


def normalize_split_sensitivity_value(core_module, value):
    if core_module is not None and hasattr(core_module, "normalize_split_sensitivity_db"):
        try:
            return int(core_module.normalize_split_sensitivity_db(value))
        except Exception:
            pass
    min_db, max_db = split_sensitivity_bounds(core_module)
    try:
        parsed = int(float(value))
    except Exception:
        parsed = 0
    return max(min_db, min(max_db, parsed))


def split_seek_step_options(core_module):
    options = (10, 20, 30)
    if core_module is not None and hasattr(core_module, "SPLIT_SILENCE_SEEK_STEP_OPTIONS_MS"):
        try:
            raw = tuple(int(v) for v in getattr(core_module, "SPLIT_SILENCE_SEEK_STEP_OPTIONS_MS"))
            clean = tuple(v for v in raw if v > 0)
            if clean:
                options = clean
        except Exception:
            options = (10, 20, 30)
    return options


def normalize_split_seek_step_value(core_module, value):
    if core_module is not None and hasattr(core_module, "normalize_split_silence_seek_step_ms"):
        try:
            return int(core_module.normalize_split_silence_seek_step_ms(value))
        except Exception:
            pass
    try:
        parsed = int(float(value))
    except Exception:
        parsed = 20
    options = split_seek_step_options(core_module)
    if parsed in options:
        return parsed
    return min(options, key=lambda option: abs(int(option) - int(parsed)))


def duplicate_policy_options(core_module):
    options = (
        ("skip", "Skip Existing Files"),
        ("overwrite", "Overwrite Existing Files"),
        ("keep_best_quality", "Keep Highest Quality"),
    )
    if core_module is None:
        return options
    try:
        raw_values = tuple(getattr(core_module, "DUPLICATE_POLICY_OPTIONS", ()) or ())
        if not raw_values:
            return options
        value_set = {str(v).strip().lower() for v in raw_values if str(v).strip()}
        if not value_set:
            return options
        labels = {
            "skip": "Skip Existing Files",
            "overwrite": "Overwrite Existing Files",
            "keep_best_quality": "Keep Highest Quality",
        }
        resolved = []
        for key in ("skip", "overwrite", "keep_best_quality"):
            if key in value_set:
                resolved.append((key, labels[key]))
        return tuple(resolved) if resolved else options
    except Exception:
        return options


def normalize_duplicate_policy_value(core_module, value):
    if core_module is not None and hasattr(core_module, "normalize_duplicate_policy"):
        try:
            return str(core_module.normalize_duplicate_policy(value))
        except Exception:
            pass
    parsed = str(value or "skip").strip().lower()
    if parsed in ("best", "best_quality", "highest_quality", "quality", "dupeguru"):
        parsed = "keep_best_quality"
    allowed = [opt[0] for opt in duplicate_policy_options(core_module)]
    return parsed if parsed in allowed else "skip"


def rename_preset_options(core_module):
    # Presets intended to expose simple, kid-friendly layout modes.
    return (
        ("simple", "Simple (Artist - Title)"),
        ("album_folders", "Album Folders (Artist/Album/Track - Title)"),
        ("discography", "Discography (Artist/Year/Album/Track - Title)"),
        ("podcast", "Podcast (Artist/Title)"),
    )


def normalize_rename_preset_value(core_module, value):
    if core_module is not None and hasattr(core_module, "normalize_rename_preset"):
        try:
            return str(core_module.normalize_rename_preset(value))
        except Exception:
            pass
    parsed = str(value or "simple").strip().lower()
    allowed = {option[0] for option in rename_preset_options(core_module)}
    return parsed if parsed in allowed else "simple"


def artist_normalization_mode_options(core_module):
    return (
        ("off", "Off"),
        ("collab_only", "Collab Only"),
        ("smart", "Smart"),
    )


def normalize_artist_normalization_mode_value(core_module, value, legacy_normalize_artists=True):
    if core_module is not None and hasattr(core_module, "normalize_artist_normalization_mode"):
        try:
            return str(
                core_module.normalize_artist_normalization_mode(
                    value, legacy_normalize_artists=legacy_normalize_artists
                )
            )
        except Exception:
            pass
    if isinstance(value, bool):
        return "collab_only" if value else "off"
    parsed = str(value or "").strip().lower()
    if parsed in ("off", "collab_only", "smart"):
        return parsed
    return "collab_only" if bool(legacy_normalize_artists) else "off"


def normalize_artist_normalization_strictness_value(core_module, value):
    if core_module is not None and hasattr(core_module, "normalize_artist_normalization_strictness"):
        try:
            return float(core_module.normalize_artist_normalization_strictness(value))
        except Exception:
            pass
    try:
        parsed = float(value)
    except Exception:
        parsed = 0.92
    return max(0.75, min(0.99, parsed))


def split_silence_threshold_for_offset(core_module, offset_value):
    normalized = normalize_split_sensitivity_value(core_module, offset_value)
    if core_module is not None and hasattr(core_module, "get_split_silence_threshold_db"):
        try:
            return float(core_module.get_split_silence_threshold_db({"split_sensitivity_db": normalized}))
        except Exception:
            pass
    return float(-38 + normalized)


def get_config_path(core_module):
    if core_module and hasattr(core_module, "get_config_path"):
        return str(core_module.get_config_path())
    return os.path.join(os.path.expanduser("~"), ".mixsplitr_ui_config.json")


def default_output_directory(core_module):
    if core_module and hasattr(core_module, "get_default_music_folder"):
        return os.path.join(core_module.get_default_music_folder(), "MixSplitR Library")
    return os.path.join(os.path.expanduser("~/Music"), "MixSplitR Library")


def default_recording_directory(core_module):
    if core_module and hasattr(core_module, "get_default_music_folder"):
        return os.path.join(core_module.get_default_music_folder(), "Mixsplitr Recordings")
    return os.path.join(os.path.expanduser("~/Music"), "Mixsplitr Recordings")


def default_manifest_directory(core_module):
    if core_module and hasattr(core_module, "get_app_data_dir"):
        return str(core_module.get_app_data_dir() / "manifests")
    return os.path.join(os.path.expanduser("~"), ".mixsplitr", "manifests")


def default_cd_output_directory(core_module):
    if core_module and hasattr(core_module, "get_default_music_folder"):
        return os.path.join(core_module.get_default_music_folder(), "MixSplitR CD Rips")
    return os.path.join(os.path.expanduser("~/Music"), "MixSplitR CD Rips")


def default_temp_workspace_directory(core_module):
    if core_module and hasattr(core_module, "get_runtime_temp_directory"):
        try:
            return str(core_module.get_runtime_temp_directory(""))
        except Exception:
            pass
    if os.name == "nt":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")),
            "MixSplitR",
            "Temp",
        )
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~/Library/Caches"), "MixSplitR")
    return os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "MixSplitR")


def default_ui_config(
    core_module,
    output_directory,
    recording_directory,
    cd_output_directory,
    temp_workspace_directory="",
):
    return {
        "mode": getattr(core_module, "MODE_SPLIT_ONLY", "split_only_no_id"),
        "splitter_workflow": "split_only_no_id",
        "split_mode": "assisted",
        "split_sensitivity_db": 0,
        "split_silence_seek_step_ms": 20,
        "duplicate_policy": "skip",
        "output_directory": str(output_directory or ""),
        "output_format": "flac",
        "rename_preset": "simple",
        "preserve_source_format": False,
        "recording_force_wav": False,
        "recording_directory": str(recording_directory or ""),
        "timestamp_output_directory": "",
        "manifest_directory": "",
        "cd_rip_output_directory": str(cd_output_directory or ""),
        "temp_workspace_directory": str(temp_workspace_directory or ""),
        "cd_rip_format": "flac",
        "cd_rip_auto_metadata": True,
        "cd_rip_eject_when_done": False,
        "auto_tracklist_window_seconds": 18,
        "auto_tracklist_step_seconds": 12,
        "auto_tracklist_min_segment_seconds": 30,
        "auto_tracklist_fallback_interval_seconds": 180,
        "auto_tracklist_max_windows": 120,
        "auto_tracklist_min_confidence": 0.58,
        "auto_tracklist_boundary_backtrack_seconds": 0.0,
        "auto_tracklist_no_identify": True,
        "auto_tracklist_essentia_enabled": bool(
            getattr(core_module, "ESSENTIA_AUTOTRACKLIST_ENABLED_DEFAULT", True)
        ),
        "auto_tracklist_essentia_min_confidence": float(
            getattr(core_module, "ESSENTIA_AUTOTRACKLIST_MIN_CONFIDENCE_DEFAULT", 0.36)
        ),
        "auto_tracklist_essentia_max_points": int(
            getattr(core_module, "ESSENTIA_AUTOTRACKLIST_MAX_POINTS_DEFAULT", 2400)
        ),
        "debug_readout_enabled": False,
        "developer_inspector_enabled": False,
        "long_track_prompt_enabled": False,
        "long_track_prompt_minutes": 6.0,
        "essentia_unified_pipeline": bool(
            getattr(core_module, "ESSENTIA_UNIFIED_PIPELINE_DEFAULT", True)
        ),
        "essentia_genre_enrichment_enabled": bool(
            getattr(core_module, "ESSENTIA_GENRE_ENRICHMENT_ENABLED_DEFAULT", True)
        ),
        "essentia_genre_enrichment_when_missing_only": bool(
            getattr(core_module, "ESSENTIA_GENRE_ENRICHMENT_WHEN_MISSING_ONLY_DEFAULT", True)
        ),
        "essentia_genre_enrichment_min_confidence": float(
            getattr(core_module, "ESSENTIA_GENRE_ENRICHMENT_MIN_CONFIDENCE_DEFAULT", 0.34)
        ),
        "essentia_genre_enrichment_max_tags": int(
            getattr(core_module, "ESSENTIA_GENRE_ENRICHMENT_MAX_TAGS_DEFAULT", 2)
        ),
        "essentia_genre_enrichment_analysis_seconds": int(
            getattr(core_module, "ESSENTIA_GENRE_ENRICHMENT_ANALYSIS_SECONDS_DEFAULT", 28)
        ),
        "fingerprint_sample_seconds": 12,
        "fingerprint_sample_seconds_multi": 12,
        "fingerprint_probe_mode": "single",
        "disable_shazam": False,
        "show_id_source": True,
        "disable_local_bpm": False,
        "enable_album_search": True,
        "artist_normalization_mode": "collab_only",
        "artist_normalization_strictness": 0.92,
        "artist_normalization_collapse_backing_band": False,
        "artist_normalization_review_ambiguous": True,
        "normalize_artists": True,
        "deep_scan": False,
        "recording_keep_screen_awake": False,
        "recording_silence_auto_stop_seconds": 10.0,
        "host": "",
        "access_key": "",
        "access_secret": "",
        "acoustid_api_key": "",
        "lastfm_api_key": "",
        "lastfm_genre_enrichment_enabled": True,
        "timeout": 10,
    }


def read_config_from_disk(config_path):
    config = {}
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as config_file:
                loaded = json.load(config_file)
                if isinstance(loaded, dict):
                    config = loaded
    except Exception:
        config = {}
    return config


def save_config_to_disk(core_module, config, config_path):
    if core_module and hasattr(core_module, "save_config"):
        core_module.save_config(config)
        return
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=4)
