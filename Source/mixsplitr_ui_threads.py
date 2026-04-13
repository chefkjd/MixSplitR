import sys
import os
import json
import threading
import tempfile
import shutil
import importlib
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
import copy

try:
    import psutil
except Exception:
    psutil = None

from PySide6.QtCore import QThread, Signal

try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
except ImportError:
    AudioSegment = None
    split_on_silence = None

try:
    from acrcloud.recognizer import ACRCloudRecognizer
except ImportError:
    ACRCloudRecognizer = None

# Backend modules (lazy-imported)
mixsplitr_core = None
mixsplitr_processing = None
mixsplitr_autotracklist = None
mixsplitr_memory = None
mixsplitr_tagging = None
mixsplitr_identify = None
mixsplitr_editor = None
mixsplitr_pipeline = None
mixsplitr_artist_normalization = None
mixsplitr_essentia = None
_BACKEND_IMPORT_ERRORS = {}

import mixsplitr_ui_settings as ui_settings


def _lazy_import_backend(attr_name, module_name=None):
    """Import backend module on demand and cache the module object."""
    global mixsplitr_core, mixsplitr_processing, mixsplitr_autotracklist, mixsplitr_memory
    global mixsplitr_tagging, mixsplitr_identify, mixsplitr_editor, mixsplitr_pipeline, mixsplitr_artist_normalization
    global mixsplitr_essentia

    target = str(module_name or attr_name).strip()
    current = globals().get(attr_name)
    if current is not None:
        return current
    try:
        module = importlib.import_module(target)
        globals()[attr_name] = module
        return module
    except Exception as exc:
        _BACKEND_IMPORT_ERRORS[attr_name] = exc
        globals()[attr_name] = None
        return None


def _ensure_processing_backends():
    """Load heavy runtime backends needed for processing paths."""
    _lazy_import_backend("mixsplitr_identify")
    _lazy_import_backend("mixsplitr_processing")
    _lazy_import_backend("mixsplitr_autotracklist")
    _lazy_import_backend("mixsplitr_memory")
    _lazy_import_backend("mixsplitr_tagging")
    _lazy_import_backend("mixsplitr_artist_normalization")
    _lazy_import_backend("mixsplitr_pipeline")
    return bool(mixsplitr_processing is not None and mixsplitr_tagging is not None)


try:
    import mixsplitr_core
except ImportError:
    mixsplitr_core = None


def _assert_readable_audio_file(file_path):
    normalized = os.path.abspath(str(file_path or ""))
    if not normalized or not os.path.exists(normalized):
        raise FileNotFoundError("Audio file not found.")
    try:
        size_bytes = int(os.path.getsize(normalized))
    except Exception:
        size_bytes = -1
    if size_bytes == 0:
        raise RuntimeError(
            "Audio file is empty (0 bytes). "
            "This usually means the recording did not save correctly."
        )

class ProcessingCancelledError(RuntimeError):
    """Raised when a background processing run is cancelled by the user."""


class TimestampSeedLoadThread(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    progress = Signal(float, str)

    def __init__(self, audio_file, runtime_config=None):
        super().__init__()
        self.audio_file = str(audio_file or "")
        self.runtime_config = dict(runtime_config or {})
        self._cancel_requested = False
        self.result_loaded = False
        self.result_cancelled = False
        self.result_error = ""
        self.result_points = []

    def cancel(self):
        self._cancel_requested = True

    def _check_cancel_requested(self):
        if self._cancel_requested:
            raise ProcessingCancelledError("Cancelled")

    def _emit_progress(self, fraction, detail=""):
        try:
            percent = float(fraction) * 100.0
        except Exception:
            percent = 0.0
        percent = max(0.0, min(100.0, percent))
        self.progress.emit(percent, str(detail or ""))

    def _record_failure(self, message):
        msg = str(message or "").strip()
        self.result_loaded = False
        self.result_cancelled = msg.lower() == "cancelled"
        self.result_error = msg if not self.result_cancelled else ""
        self.result_points = []

    def run(self):
        self.result_loaded = False
        self.result_cancelled = False
        self.result_error = ""
        self.result_points = []

        try:
            self._check_cancel_requested()
            if not self.audio_file or not os.path.exists(self.audio_file):
                raise FileNotFoundError("Audio file not found.")

            if AudioSegment is None:
                raise RuntimeError("Audio processing is unavailable in this environment.")

            if mixsplitr_autotracklist is None:
                _lazy_import_backend("mixsplitr_autotracklist")
            if mixsplitr_autotracklist is None:
                raise RuntimeError("Timestamping is unavailable in this environment.")

            if mixsplitr_core and hasattr(mixsplitr_core, "setup_ffmpeg"):
                try:
                    ffmpeg_path, ffprobe_path = mixsplitr_core.setup_ffmpeg()
                    AudioSegment.converter = ffmpeg_path
                    AudioSegment.ffprobe = ffprobe_path
                except Exception:
                    pass

            if mixsplitr_identify is None:
                _lazy_import_backend("mixsplitr_identify")
            if mixsplitr_identify and hasattr(mixsplitr_identify, "setup_musicbrainz"):
                version = getattr(mixsplitr_core, "CURRENT_VERSION", "8.0")
                repo = getattr(mixsplitr_core, "GITLAB_REPO", "chefkjd/MixSplitR")
                mixsplitr_identify.setup_musicbrainz(version, repo)
                api_key = str(self.runtime_config.get("acoustid_api_key", "")).strip()
                if api_key and hasattr(mixsplitr_identify, "set_acoustid_api_key"):
                    mixsplitr_identify.set_acoustid_api_key(api_key)

            # ── Seed scan: detect boundaries ONLY, no identification ──
            # Identification happens later in ProcessingThread after the
            # user reviews / edits the seed points in the waveform editor.
            seed_config = dict(self.runtime_config)
            seed_config["auto_tracklist_no_identify"] = True

            temp_output_dir = tempfile.mkdtemp(prefix="mixsplitr_timestamp_seed_")
            try:
                def _progress_callback(fraction, detail=""):
                    self._check_cancel_requested()
                    self._emit_progress(fraction, detail or "Scanning timestamp boundaries…")

                auto_result = mixsplitr_autotracklist.generate_auto_tracklist_for_file(
                    audio_file=self.audio_file,
                    output_folder=temp_output_dir,
                    config=seed_config,
                    recognizer=None,
                    show_progress=False,
                    dry_run=bool(self.runtime_config.get("auto_tracklist_dry_run", False)),
                    progress_callback=_progress_callback,
                    status_callback=None,
                    cancel_callback=self._check_cancel_requested,
                )
            finally:
                shutil.rmtree(temp_output_dir, ignore_errors=True)

            seed_points = []
            for segment in list((auto_result or {}).get("segments") or [])[1:]:
                try:
                    start_sec = float(segment.get("start_sec", 0.0) or 0.0)
                except Exception:
                    continue
                if start_sec > 0.0:
                    seed_points.append(start_sec)
            seed_points = sorted(set(seed_points))
            self.result_loaded = True
            self.result_points = seed_points
            self._emit_progress(1.0, f"Prepared {len(seed_points)} timestamp boundary seed(s)")
            self.loaded.emit(seed_points)
        except ProcessingCancelledError:
            self._record_failure("Cancelled")
            self.failed.emit("Cancelled")
        except Exception as exc:
            self._record_failure(str(exc) or f"{type(exc).__name__}")
            self.failed.emit(str(exc) or f"{type(exc).__name__}")


class ProcessingThread(QThread):
    progress_update = Signal(int)
    status_update = Signal(str)
    stage_update = Signal(str)   # granular phase label (backend, split count, etc.)
    finished_update = Signal(bool, str)
    debug_output = Signal(str)
    artist_normalization_review_requested = Signal(str, object)

    def __init__(
        self,
        files,
        mode_text,
        dest_folder,
        advanced_settings,
        preview_mode=False,
        light_preview=False,
        split_mode="silence",
        split_points_map=None,
        skip_mix_analysis=False,
    ):
        super().__init__()
        self.files = files
        self.mode_text = mode_text
        self.dest_folder = dest_folder
        self.advanced_settings = advanced_settings
        self.preview_mode = bool(preview_mode)
        self.light_preview = bool(light_preview)
        self.skip_mix_analysis = bool(skip_mix_analysis)
        self.split_mode = str(split_mode or "silence").strip().lower()
        if self.split_mode not in ("silence", "manual", "assisted", "transition"):
            self.split_mode = "silence"
        if self.skip_mix_analysis:
            # Direct Identifier runs bypass the Splitter workflow UI, so split
            # mode does not apply there; those files are processed directly as
            # single tracks unless they were explicitly queued from Splitter.
            self.split_mode = "silence"
        self.split_points_map = {}
        if isinstance(split_points_map, dict):
            for path, points in split_points_map.items():
                normalized_path = os.path.abspath(str(path or ""))
                if not normalized_path:
                    continue
                clean_points = []
                for point in list(points or []):
                    try:
                        value = float(point)
                    except Exception:
                        continue
                    if value >= 0:
                        clean_points.append(value)
                if clean_points:
                    self.split_points_map[normalized_path] = sorted(set(clean_points))
        self.long_track_mode_overrides = {}
        for path, value in dict(self.advanced_settings.get("long_track_mode_overrides") or {}).items():
            normalized_path = os.path.abspath(str(path or "").strip())
            normalized_value = str(value or "").strip().lower()
            if normalized_path and normalized_value in ("single", "mix"):
                self.long_track_mode_overrides[normalized_path] = normalized_value
        self.long_track_mix_strategy_overrides = {}
        for path, value in dict(self.advanced_settings.get("long_track_mix_strategy_overrides") or {}).items():
            normalized_path = os.path.abspath(str(path or "").strip())
            normalized_value = str(value or "").strip().lower()
            if normalized_path and normalized_value in ("silence", "transition"):
                self.long_track_mix_strategy_overrides[normalized_path] = normalized_value
        self.preview_cache_path = ""
        self.preview_temp_folder = ""
        self.preview_cache_data = None
        self._progress_proc_start = 5.0
        self._debug_capture_enabled = False
        self._stdout_original = None
        self._stderr_original = None
        self._stdout_capture = None
        self._stderr_capture = None
        self.split_seek_step_ms = 20
        self._cancel_requested = False
        self._memory_throttle_last_limit = None
        self._active_executor = None
        self._active_executor_lock = threading.Lock()
        self._artist_review_lock = threading.Lock()
        self._artist_review_seq = 0
        self._artist_review_requests = {}
        if mixsplitr_core is not None and hasattr(mixsplitr_core, "get_split_silence_seek_step_ms"):
            try:
                self.split_seek_step_ms = int(mixsplitr_core.get_split_silence_seek_step_ms({}))
            except Exception:
                self.split_seek_step_ms = 20

    def _long_track_mode_override_for(self, file_path):
        normalized_path = os.path.abspath(str(file_path or "").strip())
        return str(self.long_track_mode_overrides.get(normalized_path, "")).strip().lower()

    def _mix_strategy_override_for(self, file_path):
        normalized_path = os.path.abspath(str(file_path or "").strip())
        return str(self.long_track_mix_strategy_overrides.get(normalized_path, "")).strip().lower()

    def request_cancel(self):
        """Signal a cooperative cancellation request for this worker thread."""
        self._cancel_requested = True
        try:
            self.requestInterruption()
        except Exception:
            pass
        executor = None
        try:
            with self._active_executor_lock:
                executor = self._active_executor
        except Exception:
            executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _is_cancel_requested(self):
        try:
            return bool(self._cancel_requested or self.isInterruptionRequested())
        except Exception:
            return bool(self._cancel_requested)

    def _check_cancel_requested(self):
        if self._is_cancel_requested():
            raise ProcessingCancelledError("Processing cancelled by user.")

    def submit_artist_normalization_review(self, request_id, resolutions):
        request_key = str(request_id or "").strip()
        if not request_key:
            return
        with self._artist_review_lock:
            pending = self._artist_review_requests.get(request_key)
            if not isinstance(pending, dict):
                return
            pending["response"] = dict(resolutions or {})
            event = pending.get("event")
        if event is not None:
            try:
                event.set()
            except Exception:
                pass

    def _request_artist_normalization_review(self, review_entries):
        entries = list(review_entries or [])
        if not entries:
            return {}

        with self._artist_review_lock:
            self._artist_review_seq += 1
            request_id = f"artist-review-{self._artist_review_seq}"
            event = threading.Event()
            self._artist_review_requests[request_id] = {
                "event": event,
                "response": {},
            }

        try:
            self.artist_normalization_review_requested.emit(
                request_id,
                copy.deepcopy(entries),
            )
            while not event.wait(0.1):
                self._check_cancel_requested()
            with self._artist_review_lock:
                payload = dict(self._artist_review_requests.get(request_id) or {})
            response = payload.get("response") or {}
            return dict(response) if isinstance(response, dict) else {}
        finally:
            with self._artist_review_lock:
                self._artist_review_requests.pop(request_id, None)

    def _available_memory_mb(self):
        """Return currently available system RAM in MB, or None when unavailable."""
        if psutil is None:
            return None
        try:
            available_bytes = float(psutil.virtual_memory().available)
            return max(0.0, available_bytes / (1024.0 * 1024.0))
        except Exception:
            return None

    def _memory_aware_worker_limit(self, base_workers):
        """Scale active worker count by currently available RAM."""
        base = max(1, int(base_workers or 1))
        available_mb = self._available_memory_mb()
        if available_mb is None:
            return base, None

        if available_mb < 900:
            limit = 1
        elif available_mb < 1500:
            limit = min(base, 2)
        elif available_mb < 2200:
            limit = min(base, 3)
        elif available_mb < 3000:
            limit = min(base, 4)
        elif available_mb < 4200:
            limit = min(base, 5)
        else:
            limit = base
        return max(1, min(base, int(limit))), available_mb

    def _emit_memory_throttle_state(self, active_limit, base_workers, available_mb):
        current = max(1, int(active_limit or 1))
        base = max(1, int(base_workers or 1))
        if current == self._memory_throttle_last_limit:
            return
        self._memory_throttle_last_limit = current
        if current >= base:
            if base < 8:
                self.stage_update.emit(f"Memory-aware throttling cleared — using {base} worker(s)")
            return
        if available_mb is None:
            self.stage_update.emit(
                f"Memory-aware throttling active — using {current}/{base} worker(s)"
            )
            return
        self.stage_update.emit(
            f"Memory-aware throttling active — using {current}/{base} worker(s) "
            f"(free RAM ~{int(available_mb)} MB)"
        )

    def _emit_debug_line(self, message, stream_name="stdout"):
        text = str(message or "").strip()
        if not text:
            return
        if str(stream_name or "").strip().lower() == "stderr":
            text = f"[stderr] {text}"
        self.debug_output.emit(text)

    def _maybe_review_artist_normalization(self, tracks):
        if not (
            mixsplitr_artist_normalization
            and hasattr(mixsplitr_artist_normalization, "collect_pending_review_entries")
            and hasattr(mixsplitr_artist_normalization, "apply_review_resolutions")
        ):
            return

        review_entries = mixsplitr_artist_normalization.collect_pending_review_entries(tracks)
        if not review_entries:
            return

        entry_count = len(review_entries)
        label = "case" if entry_count == 1 else "cases"
        self.stage_update.emit(
            f"Smart artist review needed for {entry_count} ambiguous {label}…"
        )
        resolutions = self._request_artist_normalization_review(review_entries)
        mixsplitr_artist_normalization.apply_review_resolutions(
            tracks,
            resolutions,
            persist=True,
            debug_callback=self.debug_output.emit,
        )

    def _load_existing_config(self):
        config = {}
        if not mixsplitr_core or not hasattr(mixsplitr_core, "get_config_path"):
            return config
        try:
            config_path = mixsplitr_core.get_config_path()
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as config_file:
                    loaded = json.load(config_file)
                    if isinstance(loaded, dict):
                        config = loaded
        except Exception:
            pass
        return config

    def _split_sensitivity_bounds(self):
        return ui_settings.split_sensitivity_bounds(mixsplitr_core)

    def _normalize_duplicate_policy_value(self, value):
        return ui_settings.normalize_duplicate_policy_value(mixsplitr_core, value)

    def _build_runtime_config(self, mode_value):
        config = self._load_existing_config()

        destination = os.path.expanduser(self.dest_folder.strip() if self.dest_folder else "")
        if not destination:
            destination = os.path.join(os.path.expanduser("~/Music"), "MixSplitR Library")
        destination = os.path.abspath(destination)
        os.makedirs(destination, exist_ok=True)

        config["mode"] = mode_value
        config["output_directory"] = destination
        output_format = str(self.advanced_settings.get("output_format", config.get("output_format", "flac"))).strip().lower()
        available_formats = {}
        if mixsplitr_tagging and hasattr(mixsplitr_tagging, "AUDIO_FORMATS"):
            available_formats = getattr(mixsplitr_tagging, "AUDIO_FORMATS") or {}
        if output_format not in available_formats:
            output_format = "flac"
        config["output_format"] = output_format
        config["auto_tracklist_window_seconds"] = int(self.advanced_settings.get("window_size", 18))
        config["auto_tracklist_step_seconds"] = int(self.advanced_settings.get("step_size", 12))
        config["auto_tracklist_min_confidence"] = float(self.advanced_settings.get("min_confidence", 0.58))
        config["essentia_genre_enrichment_min_confidence"] = float(
            self.advanced_settings.get("essentia_min_confidence", 0.34)
        )
        if "split_sensitivity_db" in self.advanced_settings:
            try:
                sensitivity_raw = int(float(self.advanced_settings.get("split_sensitivity_db", 0)))
            except Exception:
                sensitivity_raw = 0
            if mixsplitr_core and hasattr(mixsplitr_core, "normalize_split_sensitivity_db"):
                sensitivity_value = int(mixsplitr_core.normalize_split_sensitivity_db(sensitivity_raw))
            else:
                min_db, max_db = self._split_sensitivity_bounds()
                sensitivity_value = max(min_db, min(max_db, sensitivity_raw))
            config["split_sensitivity_db"] = sensitivity_value
        if "split_silence_seek_step_ms" in self.advanced_settings:
            raw_seek_step = self.advanced_settings.get("split_silence_seek_step_ms")
            if mixsplitr_core and hasattr(mixsplitr_core, "normalize_split_silence_seek_step_ms"):
                try:
                    config["split_silence_seek_step_ms"] = int(
                        mixsplitr_core.normalize_split_silence_seek_step_ms(raw_seek_step)
                    )
                except Exception:
                    config["split_silence_seek_step_ms"] = 20
            else:
                try:
                    parsed = int(raw_seek_step)
                except Exception:
                    parsed = 20
                config["split_silence_seek_step_ms"] = parsed if parsed in (10, 20, 30) else 20
        if "duplicate_policy" in self.advanced_settings:
            config["duplicate_policy"] = self._normalize_duplicate_policy_value(
                self.advanced_settings.get("duplicate_policy")
            )
        if "auto_tracklist_no_identify" in self.advanced_settings:
            config["auto_tracklist_no_identify"] = bool(
                self.advanced_settings.get("auto_tracklist_no_identify")
            )
        if "auto_tracklist_identifier_mode" in self.advanced_settings:
            config["auto_tracklist_identifier_mode"] = str(
                self.advanced_settings.get("auto_tracklist_identifier_mode") or ""
            ).strip()
        if mixsplitr_core and hasattr(mixsplitr_core, "get_split_silence_seek_step_ms"):
            try:
                self.split_seek_step_ms = int(mixsplitr_core.get_split_silence_seek_step_ms(config))
            except Exception:
                self.split_seek_step_ms = 20
        else:
            try:
                self.split_seek_step_ms = int(config.get("split_silence_seek_step_ms", 20))
            except Exception:
                self.split_seek_step_ms = 20

        return config

    def _effective_split_silence_threshold_db(self, config):
        if mixsplitr_core and hasattr(mixsplitr_core, "get_split_silence_threshold_db"):
            try:
                return float(mixsplitr_core.get_split_silence_threshold_db(config))
            except Exception:
                pass
        try:
            sensitivity_raw = int(float((config or {}).get("split_sensitivity_db", 0)))
        except Exception:
            sensitivity_raw = 0
        min_db, max_db = self._split_sensitivity_bounds()
        sensitivity_db = max(min_db, min(max_db, sensitivity_raw))
        return float(-38 + sensitivity_db)

    def _preview_cache_file_path(self):
        if mixsplitr_core and hasattr(mixsplitr_core, "get_cache_path"):
            return str(mixsplitr_core.get_cache_path("mixsplitr_cache.json"))
        return os.path.join(os.path.expanduser("~"), ".mixsplitr", "mixsplitr_cache.json")

    def _preview_temp_directory(self):
        if mixsplitr_core and hasattr(mixsplitr_core, "get_runtime_temp_directory"):
            try:
                return str(mixsplitr_core.get_runtime_temp_directory("preview"))
            except Exception:
                pass
        cache_path = self._preview_cache_file_path()
        return os.path.join(os.path.dirname(cache_path), "mixsplitr_temp")

    def _prepare_preview_temp_folder(self):
        temp_folder = self._preview_temp_directory()
        if os.path.isdir(temp_folder):
            shutil.rmtree(temp_folder, ignore_errors=True)
        os.makedirs(temp_folder, exist_ok=True)
        return temp_folder

    def _setup_backend_runtime(self, config):
        try:
            temp_root = os.path.abspath(os.path.expanduser(str((config or {}).get("temp_workspace_directory", "")).strip()))
        except Exception:
            temp_root = ""
        if temp_root:
            try:
                os.makedirs(temp_root, exist_ok=True)
            except Exception:
                pass
            os.environ["MIXSPLITR_TEMP_ROOT"] = temp_root
        else:
            os.environ.pop("MIXSPLITR_TEMP_ROOT", None)

        if mixsplitr_core and hasattr(mixsplitr_core, "setup_ffmpeg") and AudioSegment is not None:
            try:
                ffmpeg_path, ffprobe_path = mixsplitr_core.setup_ffmpeg()
                AudioSegment.converter = ffmpeg_path
                AudioSegment.ffprobe = ffprobe_path
            except Exception:
                pass

        if mixsplitr_identify is None:
            _lazy_import_backend("mixsplitr_identify")
        if mixsplitr_identify and hasattr(mixsplitr_identify, "setup_musicbrainz"):
            version = getattr(mixsplitr_core, "CURRENT_VERSION", "8.0")
            repo = getattr(mixsplitr_core, "GITLAB_REPO", "chefkjd/MixSplitR")
            mixsplitr_identify.setup_musicbrainz(version, repo)
            api_key = str(config.get("acoustid_api_key", "")).strip()
            if api_key and hasattr(mixsplitr_identify, "set_acoustid_api_key"):
                mixsplitr_identify.set_acoustid_api_key(api_key)

    def _create_acr_recognizer(self, config):
        if ACRCloudRecognizer is None:
            return None
        if not (config.get("host") and config.get("access_key") and config.get("access_secret")):
            return None
        try:
            return ACRCloudRecognizer(config)
        except Exception:
            return None

    def _split_audio_at_points_native(self, recording, split_points):
        if recording is None:
            return []
        duration_ms = int(max(0, len(recording)))
        if duration_ms <= 0:
            return []
        clean_points_ms = []
        for value in list(split_points or []):
            try:
                point_ms = int(float(value) * 1000.0)
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

    def _build_split_cache_entry(
        self,
        file_path,
        prepared_chunks,
        *,
        is_mix,
        split_method,
        split_silence_thresh_db,
    ):
        normalized_path = os.path.abspath(str(file_path or "").strip())
        points_sec = []
        running_ms = 0
        normalized_method = str(split_method or "").strip().lower() or "silence"
        for row in list(prepared_chunks or [])[:-1]:
            try:
                chunk = row.get("chunk")
            except Exception:
                chunk = None
            try:
                running_ms += max(0, int(len(chunk)))
            except Exception:
                continue
            if running_ms > 0:
                points_sec.append(round(float(running_ms) / 1000.0, 3))
        return {
            "path": normalized_path,
            "method": normalized_method if is_mix else "passthrough",
            "points_sec": points_sec,
            "num_segments": int(max(1, len(prepared_chunks or []))),
            "params": {
                "is_mix": bool(is_mix),
                "requested_split_mode": str(self.split_mode or "silence"),
                "split_silence_thresh_db": float(split_silence_thresh_db),
                "split_seek_step_ms": int(self.split_seek_step_ms),
                "min_silence_len_ms": 2000,
                "keep_silence_ms": 200,
            },
        }

    def _detect_transition_split_points(self, file_path, runtime_config=None):
        if mixsplitr_autotracklist is None:
            _lazy_import_backend("mixsplitr_autotracklist")
        if mixsplitr_autotracklist is None:
            return []

        config = dict(runtime_config or {})
        config["mode"] = getattr(
            mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual"
        )
        config["auto_tracklist_no_identify"] = True
        config["auto_tracklist_identifier_mode"] = ""

        temp_output_dir = tempfile.mkdtemp(prefix="mixsplitr_transition_split_")
        last_detail = {"value": ""}
        try:
            def _progress_callback(_fraction, detail=""):
                self._check_cancel_requested()
                detail_text = str(detail or "").strip()
                if detail_text and detail_text != last_detail["value"]:
                    last_detail["value"] = detail_text
                    self.stage_update.emit(detail_text)

            auto_result = mixsplitr_autotracklist.generate_auto_tracklist_for_file(
                audio_file=file_path,
                output_folder=temp_output_dir,
                config=config,
                recognizer=None,
                show_progress=False,
                dry_run=False,
                progress_callback=_progress_callback,
                status_callback=self.stage_update.emit,
                cancel_callback=self._check_cancel_requested,
                write_output_files=False,
            )
        finally:
            shutil.rmtree(temp_output_dir, ignore_errors=True)

        scan_settings = dict((auto_result or {}).get("scan_settings") or {})
        try:
            transition_point_count = int(
                scan_settings.get("transition_points_detected", 0) or 0
            )
        except Exception:
            transition_point_count = 0
        if transition_point_count <= 0:
            return []

        points = []
        for segment in list((auto_result or {}).get("segments") or [])[1:]:
            try:
                start_sec = float(segment.get("start_sec", 0.0) or 0.0)
            except Exception:
                continue
            if start_sec > 0.0:
                points.append(start_sec)
        return sorted(set(points))

    def _prepare_chunks(
        self,
        file_path,
        file_num,
        is_mix,
        preview_mode=False,
        full_preview_temp_folder="",
        split_silence_thresh_db=-38.0,
        runtime_config=None,
    ):
        self._check_cancel_requested()
        _assert_readable_audio_file(file_path)
        recording = AudioSegment.from_file(file_path)
        if is_mix:
            chunks = None
            selected_points = self.split_points_map.get(os.path.abspath(file_path), [])
            requested_mix_strategy = self._mix_strategy_override_for(file_path)
            split_method = "silence"
            use_transition_detection = bool(
                not selected_points
                and (
                    self.split_mode == "transition"
                    or requested_mix_strategy == "transition"
                )
            )
            if use_transition_detection:
                self.stage_update.emit(
                    f"Detecting transition boundaries in {os.path.basename(file_path)}…"
                )
                selected_points = self._detect_transition_split_points(
                    file_path,
                    runtime_config=runtime_config,
                )
                if selected_points:
                    split_method = "transition"
                    self.status_update.emit(
                        f"Using detected transition splits ({len(selected_points)} point(s))"
                    )
                else:
                    self.status_update.emit(
                        "No confident transition boundaries found; using automatic silence splitting"
                    )
            if selected_points:
                splitter_fn = None
                if mixsplitr_pipeline is None:
                    _lazy_import_backend("mixsplitr_pipeline")
                if mixsplitr_pipeline and hasattr(mixsplitr_pipeline, "split_audio_at_points"):
                    splitter_fn = mixsplitr_pipeline.split_audio_at_points
                if splitter_fn is not None:
                    try:
                        chunks = splitter_fn(file_path, selected_points)
                    except Exception:
                        chunks = None
                if not chunks:
                    try:
                        chunks = self._split_audio_at_points_native(recording, selected_points)
                    except Exception:
                        chunks = None
                if chunks:
                    if split_method != "transition":
                        split_method = "waveform"
                    self.status_update.emit(
                        f"Using preset {self.split_mode} waveform splits ({len(selected_points)} point(s))"
                    )
            elif self.split_mode in ("manual", "assisted"):
                self.status_update.emit("No preset waveform points; using automatic silence splitting")

            if not chunks:
                self.stage_update.emit(f"Detecting silence boundaries in {os.path.basename(file_path)}…")
                chunks = split_on_silence(
                    recording,
                    min_silence_len=2000,
                    silence_thresh=split_silence_thresh_db,
                    keep_silence=200,
                    seek_step=self.split_seek_step_ms,
                )
            if not chunks:
                chunks = [recording]
            if len(chunks) > 1:
                self.stage_update.emit(f"Silence split complete — {len(chunks)} segments found")
            prepared = []
            for idx, chunk in enumerate(chunks):
                self._check_cancel_requested()
                row = {
                    "chunk": chunk,
                    "file_num": file_num,
                    "original_file": file_path,
                    "split_index": idx,
                    "source_track_passthrough": False,
                }
                if preview_mode and full_preview_temp_folder:
                    temp_chunk = os.path.join(
                        full_preview_temp_folder,
                        f"preview_chunk_{file_num}_{idx}_{threading.get_ident()}.flac",
                    )
                    chunk.export(temp_chunk, format="flac")
                    row["temp_chunk_path"] = temp_chunk
                prepared.append(row)
            return prepared, self._build_split_cache_entry(
                file_path,
                prepared,
                is_mix=True,
                split_method=split_method,
                split_silence_thresh_db=split_silence_thresh_db,
            )

        single = {
            "chunk": recording,
            "file_num": file_num,
            "original_file": file_path,
            "split_index": 0,
            "source_track_passthrough": True,
        }
        if preview_mode and full_preview_temp_folder:
            temp_chunk = os.path.join(
                full_preview_temp_folder,
                f"preview_chunk_{file_num}_0_{threading.get_ident()}.flac",
            )
            recording.export(temp_chunk, format="flac")
            single["temp_chunk_path"] = temp_chunk
        prepared = [single]
        return prepared, self._build_split_cache_entry(
            file_path,
            prepared,
            is_mix=False,
            split_method="passthrough",
            split_silence_thresh_db=split_silence_thresh_db,
        )

    def _process_standard_file(
        self,
        file_path,
        file_num,
        is_mix,
        mode_value,
        config,
        existing_tracks,
        lock,
        preview_mode=False,
        full_preview_temp_folder="",
        file_index=0,
        total_files=1,
        shared_recognizer=None,
        shared_rate_limiter=None,
    ):
        self._check_cancel_requested()
        file_name = os.path.basename(file_path)
        self.status_update.emit(f"Loading audio: {file_name}")
        split_silence_thresh_db = self._effective_split_silence_threshold_db(config)
        chunks, split_cache_entry = self._prepare_chunks(
            file_path,
            file_num,
            is_mix,
            preview_mode=preview_mode,
            full_preview_temp_folder=full_preview_temp_folder,
            split_silence_thresh_db=split_silence_thresh_db,
            runtime_config=config,
        )
        file_results = []

        mode_acr = getattr(mixsplitr_core, "MODE_ACRCLOUD", "acrcloud")
        mode_mb = getattr(mixsplitr_core, "MODE_MB_ONLY", "musicbrainz_only")
        mode_split_only = getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id")

        # Use the caller-supplied shared recognizer/rate-limiter when available
        # (preferred: created once and shared across all parallel workers so the
        # rate-limiter actually gates calls across threads rather than per-file).
        if shared_recognizer is not None:
            recognizer = shared_recognizer
            rate_limiter = shared_rate_limiter
        elif mode_value == mode_acr:
            recognizer = self._create_acr_recognizer(config)
            rate_limiter = None
            if recognizer is None:
                mode_value = mode_mb
            elif mixsplitr_core and hasattr(mixsplitr_core, "RateLimiter"):
                rate_limiter = mixsplitr_core.RateLimiter(min_interval=1.2)
        else:
            recognizer = None
            rate_limiter = None

        _stage_mode_names = {
            getattr(mixsplitr_core, "MODE_ACRCLOUD", "acrcloud"): "ACRCloud",
            getattr(mixsplitr_core, "MODE_MB_ONLY", "musicbrainz_only"): "AcoustID / MusicBrainz",
            getattr(mixsplitr_core, "MODE_DUAL", "dual_best_match"): "Dual (ACRCloud + AcoustID)",
            getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id"): "Split Only (no ID)",
        }
        total_chunks = max(1, len(chunks))
        for chunk_index, chunk_data in enumerate(chunks):
            self._check_cancel_requested()
            self.status_update.emit(
                f"Identifying {file_name} (chunk {chunk_index + 1}/{total_chunks})"
            )
            _mode_label = _stage_mode_names.get(mode_value, mode_value)
            self.stage_update.emit(
                f"Track {chunk_index + 1}/{total_chunks} — fingerprinting via {_mode_label}"
            )
            mode_dual = getattr(mixsplitr_core, "MODE_DUAL", "dual_best_match")
            if mode_value == mode_split_only:
                result = mixsplitr_processing.process_single_track_split_only(
                    chunk_data,
                    chunk_index,
                    existing_tracks,
                    config["output_directory"],
                    lock,
                    preview_mode,
                    runtime_config=config,
                )
            elif mode_value == mode_mb:
                result = mixsplitr_processing.process_single_track_mb_only(
                    chunk_data,
                    chunk_index,
                    existing_tracks,
                    config["output_directory"],
                    lock,
                    preview_mode,
                    runtime_config=config,
                )
            elif mode_value == mode_dual:
                if recognizer is None:
                    raise RuntimeError("Dual mode requires ACRCloud credentials.")
                result = mixsplitr_processing.process_single_track_dual(
                    chunk_data,
                    chunk_index,
                    recognizer,
                    rate_limiter,
                    existing_tracks,
                    config["output_directory"],
                    lock,
                    preview_mode,
                    runtime_config=config,
                )
            else:
                if recognizer is None:
                    raise RuntimeError("ACRCloud mode selected but recognizer could not initialize.")
                result = mixsplitr_processing.process_single_track(
                    chunk_data,
                    chunk_index,
                    recognizer,
                    rate_limiter,
                    existing_tracks,
                    config["output_directory"],
                    lock,
                    preview_mode,
                    runtime_config=config,
                )
            if isinstance(result, dict):
                result.setdefault(
                    "source_track_passthrough",
                    bool(chunk_data.get("source_track_passthrough", False)),
                )
            file_results.append(result)
            # Reserve the first N% of the bar for analysis; in direct-identify
            # mode this starts at 0 because analysis is skipped entirely.
            _PROC_START = float(getattr(self, "_progress_proc_start", 5.0))
            _PROC_RANGE = 99.0 - _PROC_START
            chunk_fraction = (
                (float(file_index) + (float(chunk_index + 1) / float(total_chunks)))
                / float(max(1, total_files))
            )
            chunk_percent = _PROC_START + chunk_fraction * _PROC_RANGE
            self.progress_update.emit(max(int(_PROC_START), min(99, int(chunk_percent))))

        if preview_mode:
            identified_count = len([r for r in file_results if r.get("status") == "identified"])
            unidentified_count = len([r for r in file_results if r.get("status") == "unidentified"])
            skipped_count = len([r for r in file_results if r.get("status") == "skipped"])
            return identified_count, unidentified_count, skipped_count, file_results, split_cache_entry

        identified_saved = 0
        unidentified_saved = 0
        skipped_count = 0
        artwork_cache = {}

        if (
            mixsplitr_artist_normalization
            and hasattr(mixsplitr_artist_normalization, "apply_smart_folder_canonicalization")
        ):
            try:
                mixsplitr_artist_normalization.apply_smart_folder_canonicalization(
                    file_results,
                    runtime_config=config,
                    debug_callback=self.debug_output.emit,
                )
                self._maybe_review_artist_normalization(file_results)
            except Exception:
                pass

        artwork_urls = [r.get("art_url") for r in file_results if r.get("status") == "identified" and r.get("art_url")]
        if artwork_urls and mixsplitr_identify and hasattr(mixsplitr_identify, "batch_download_artwork"):
            try:
                self.stage_update.emit(f"Downloading artwork for {len(artwork_urls)} track(s)…")
                artwork_cache = mixsplitr_identify.batch_download_artwork(artwork_urls) or {}
            except Exception:
                artwork_cache = {}

        _id_count = len([r for r in file_results if r.get("status") == "identified"])
        duplicate_policy = self._normalize_duplicate_policy_value(
            config.get("duplicate_policy", "skip")
        )
        if _id_count:
            self.stage_update.emit(f"Tagging & exporting {_id_count} identified track(s)…")
        for result in file_results:
            self._check_cancel_requested()
            if result.get("status") == "identified":
                temp_flac = result.get("temp_flac")
                if temp_flac and os.path.exists(temp_flac):
                    out_path = mixsplitr_tagging.embed_and_sort_generic(
                        temp_flac,
                        result.get("artist", ""),
                        result.get("title", ""),
                        result.get("album", ""),
                        result.get("art_url"),
                        config["output_directory"],
                        output_format=str(config.get("output_format", "flac")).strip().lower(),
                        artwork_cache=artwork_cache,
                        enhanced_metadata=result.get("enhanced_metadata", {}),
                        overwrite_existing=bool(duplicate_policy == "overwrite"),
                        duplicate_policy=duplicate_policy,
                        source_file_path=result.get("original_file"),
                        preserve_source_format=bool(
                            config.get("preserve_source_format", False)
                            and result.get("source_track_passthrough", False)
                        ),
                        rename_preset=config.get("rename_preset", "simple"),
                    )
                    if out_path:
                        identified_saved += 1
            elif result.get("status") == "unidentified":
                if result.get("unidentified_path") and os.path.exists(result["unidentified_path"]):
                    unidentified_saved += 1
            else:
                skipped_count += 1

        return identified_saved, unidentified_saved, skipped_count, file_results, split_cache_entry

    def _process_auto_tracklist_file(self, file_path, config, recognizer, file_index=0, total_files=1):
        self._check_cancel_requested()
        if mixsplitr_autotracklist is None:
            _lazy_import_backend("mixsplitr_autotracklist")
        if mixsplitr_autotracklist is None:
            raise RuntimeError("Auto-tracklist module is unavailable.")
        output_folder = str(config.get("output_directory", "") or "")
        dry_run = bool(config.get("auto_tracklist_dry_run", False))
        selected_points = self.split_points_map.get(os.path.abspath(file_path), [])
        can_generate_from_starts = bool(
            hasattr(mixsplitr_autotracklist, "generate_tracklist_from_start_times")
        )
        _proc_start = float(getattr(self, "_progress_proc_start", 5.0))
        _proc_range = 99.0 - _proc_start
        _file_base = float(file_index) / float(max(1, total_files))
        _file_span = 1.0 / float(max(1, total_files))

        def _emit_timestamp_progress(fraction, detail=""):
            self._check_cancel_requested()
            safe_fraction = max(0.0, min(1.0, float(fraction or 0.0)))
            if detail:
                self.stage_update.emit(str(detail))
            progress = _proc_start + (_file_base + (safe_fraction * _file_span)) * _proc_range
            self.progress_update.emit(max(int(_proc_start), min(99, int(progress))))

        auto_result = None
        _has_user_points = bool(selected_points and can_generate_from_starts)

        if self.split_mode in ("manual", "assisted") and _has_user_points:
            # ── User confirmed boundaries → identify on THOSE segments ──
            if self.split_mode == "manual":
                self.status_update.emit("Using manual timestamp boundaries from waveform editor...")
            else:
                self.status_update.emit("Identifying segments on your edited boundaries...")
            start_times = sorted(set([0.0] + [float(p) for p in selected_points if float(p) > 0.0]))
            auto_result = mixsplitr_autotracklist.generate_tracklist_from_start_times(
                audio_file=file_path,
                output_folder=output_folder,
                start_times=start_times,
                config=config,
                recognizer=recognizer,
                template_segments=None,
                dry_run=dry_run,
                progress_callback=_emit_timestamp_progress,
                status_callback=self.stage_update.emit,
                cancel_callback=self._check_cancel_requested,
                write_output_files=not bool(self.preview_mode),
            )
        else:
            # ── No user-edited points: full auto detect + identify ──
            if self.split_mode == "manual":
                if not selected_points:
                    self.status_update.emit("No manual timestamp points saved; using automatic timeline scan")
                elif not can_generate_from_starts:
                    self.status_update.emit("Manual timestamp tools unavailable; using automatic timeline scan")
            elif self.split_mode == "assisted" and not _has_user_points:
                if not selected_points:
                    self.status_update.emit("No assisted timestamp edits saved; using auto-generated timeline")
                elif not can_generate_from_starts:
                    self.status_update.emit("Assisted timestamp tools unavailable; using auto-generated timeline")

            auto_result = mixsplitr_autotracklist.generate_auto_tracklist_for_file(
                audio_file=file_path,
                output_folder=output_folder,
                config=config,
                recognizer=recognizer,
                show_progress=False,
                dry_run=dry_run,
                progress_callback=_emit_timestamp_progress,
                status_callback=self.stage_update.emit,
                cancel_callback=self._check_cancel_requested,
                write_output_files=not bool(self.preview_mode),
            )

        summary = auto_result.get("summary", {}) or {}
        output_files = auto_result.get("output_files", []) or []
        identified = int(summary.get("identified", 0))
        fallback_labeled = int(summary.get("fallback_labeled", 0))
        preview_tracks = []
        preview_session = {}
        if self.preview_mode:
            preview_tracks, preview_session = self._build_timestamp_preview_session(
                auto_result,
                file_index=file_index,
                total_files=total_files,
                full_preview_temp_folder=self.preview_temp_folder,
            )
        return identified, fallback_labeled, len(output_files), preview_tracks, preview_session

    def _build_timestamp_preview_session(
        self,
        auto_result,
        *,
        file_index=0,
        total_files=1,
        full_preview_temp_folder="",
    ):
        if not isinstance(auto_result, dict):
            return [], {}
        source_file = os.path.abspath(str(auto_result.get("audio_file") or "").strip())
        if not source_file:
            return [], {}

        tracks = copy.deepcopy(list(auto_result.get("manifest_tracks") or []))
        segments = list(auto_result.get("segments") or [])
        source_name = os.path.splitext(os.path.basename(source_file))[0] or "Unknown Source"

        def _format_timecode(value):
            try:
                total_seconds = max(0.0, float(value or 0.0))
            except Exception:
                total_seconds = 0.0
            whole_seconds = int(total_seconds)
            hours = whole_seconds // 3600
            minutes = (whole_seconds % 3600) // 60
            seconds = whole_seconds % 60
            fractional = total_seconds - float(whole_seconds)
            if abs(fractional) >= 0.05:
                return f"{hours:02d}:{minutes:02d}:{seconds + fractional:04.1f}"
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        preview_paths = {}
        if full_preview_temp_folder and os.path.exists(source_file):
            _assert_readable_audio_file(source_file)
            try:
                audio = AudioSegment.from_file(source_file)
            except Exception:
                audio = None
            if audio is not None:
                for idx, segment in enumerate(segments):
                    self._check_cancel_requested()
                    try:
                        start_sec = max(0.0, float(segment.get("start_sec", 0.0) or 0.0))
                    except Exception:
                        start_sec = 0.0
                    try:
                        end_sec = max(start_sec, float(segment.get("end_sec", start_sec) or start_sec))
                    except Exception:
                        end_sec = start_sec
                    start_ms = int(round(start_sec * 1000.0))
                    end_ms = int(round(end_sec * 1000.0))
                    if end_ms <= start_ms:
                        continue
                    chunk = audio[start_ms:end_ms]
                    if len(chunk) <= 0:
                        continue
                    temp_chunk = os.path.join(
                        full_preview_temp_folder,
                        f"preview_timestamp_{file_index + 1}_{idx}_{threading.get_ident()}.flac",
                    )
                    try:
                        chunk.export(temp_chunk, format="flac")
                        preview_paths[idx] = temp_chunk
                    except Exception:
                        continue

        for idx, track in enumerate(tracks):
            segment = segments[idx] if idx < len(segments) else {}
            try:
                start_sec = float(track.get("start_time", segment.get("start_sec", 0.0)) or 0.0)
            except Exception:
                start_sec = 0.0
            try:
                end_sec = float(track.get("end_time", segment.get("end_sec", start_sec)) or start_sec)
            except Exception:
                end_sec = start_sec
            try:
                duration_sec = float(track.get("duration_sec", segment.get("duration_sec", max(0.0, end_sec - start_sec))) or 0.0)
            except Exception:
                duration_sec = max(0.0, end_sec - start_sec)
            label = str(
                track.get("timeline_label")
                or segment.get("label")
                or track.get("title")
                or f"Track {idx + 1:02d}"
            ).strip()
            track["source_file_index"] = int(file_index)
            track["source_file_total"] = int(max(1, total_files))
            track["source_name"] = source_name
            track["timeline_label"] = label
            track["start_time"] = max(0.0, start_sec)
            track["end_time"] = max(track["start_time"], end_sec)
            track["duration_sec"] = max(0.0, duration_sec)
            track["start_timestamp"] = _format_timecode(track["start_time"])
            track["end_timestamp"] = _format_timecode(track["end_time"])
            track["timestamp_source"] = str(
                track.get("identification_source")
                or segment.get("source")
                or track.get("timestamp_source")
                or ""
            ).strip()
            if not str(track.get("unidentified_filename") or "").strip():
                track["unidentified_filename"] = label
            if not str(track.get("album") or "").strip():
                track["album"] = "Timestamping"
            if idx in preview_paths:
                track["temp_chunk_path"] = preview_paths[idx]

        preview_session = {
            source_file: {
                "audio_file": source_file,
                "source_file_index": int(file_index),
                "output_directory": str(auto_result.get("output_directory") or ""),
                "timestamp_file": str(auto_result.get("timestamp_file") or ""),
                "report_file": str(auto_result.get("report_file") or ""),
                "scan_settings": copy.deepcopy(auto_result.get("scan_settings") or {}),
                "duration_seconds": float(auto_result.get("duration_seconds", 0.0) or 0.0),
                "dry_run": bool(auto_result.get("dry_run", False)),
            }
        }
        return tracks, preview_session

    def run(self):
        try:
            self._check_cancel_requested()
            if not self.files:
                raise ValueError("No files selected! Please drag and drop audio files.")
            if AudioSegment is None or split_on_silence is None:
                raise RuntimeError("pydub is not available in this Python environment.")
            _ensure_processing_backends()
            if mixsplitr_processing is None or mixsplitr_tagging is None:
                missing = []
                if mixsplitr_processing is None:
                    missing.append("mixsplitr_processing")
                if mixsplitr_tagging is None:
                    missing.append("mixsplitr_tagging")
                if missing:
                    detail = ", ".join(missing)
                    raise RuntimeError(f"Required processing modules are unavailable: {detail}")

            mode_acr = getattr(mixsplitr_core, "MODE_ACRCLOUD", "acrcloud")
            mode_mb = getattr(mixsplitr_core, "MODE_MB_ONLY", "musicbrainz_only")
            mode_split_only = getattr(mixsplitr_core, "MODE_SPLIT_ONLY", "split_only_no_id")
            mode_auto = getattr(mixsplitr_core, "MODE_AUTO_TRACKLIST", "auto_tracklist_no_manual")

            mode_dual = getattr(mixsplitr_core, "MODE_DUAL", "dual_best_match")
            mode_map = {
                "Split Only (No ID)": mode_split_only,
                "MusicBrainz / AcoustID": mode_mb,
                "ACRCloud": mode_acr,
                "Dual (ACRCloud + AcoustID)": mode_dual,
                "Timestamping Mode (Auto Tracklist)": mode_auto,
            }
            internal_mode = mode_map.get(self.mode_text, mode_split_only)
            effective_preview_mode = bool(self.preview_mode)
            config = self._build_runtime_config(internal_mode)
            self._setup_backend_runtime(config)

            if mixsplitr_core and hasattr(mixsplitr_core, "save_config"):
                try:
                    mixsplitr_core.save_config(config)
                except Exception:
                    pass

            recognizer = self._create_acr_recognizer(config)
            if internal_mode == mode_acr and recognizer is None:
                self.status_update.emit("ACRCloud keys unavailable; using MusicBrainz / AcoustID fallback")
                internal_mode = mode_mb
                config["mode"] = mode_mb

            full_preview_temp_folder = ""
            if effective_preview_mode:
                self.preview_cache_path = self._preview_cache_file_path()
                self.preview_temp_folder = self._preview_temp_directory()
                os.makedirs(os.path.dirname(self.preview_cache_path), exist_ok=True)
                if self.light_preview:
                    if os.path.isdir(self.preview_temp_folder):
                        shutil.rmtree(self.preview_temp_folder, ignore_errors=True)
                else:
                    full_preview_temp_folder = self._prepare_preview_temp_folder()
                    self.preview_temp_folder = full_preview_temp_folder
            total_files = len(self.files)
            identified_total = 0
            unidentified_total = 0
            skipped_total = 0
            auto_output_total = 0
            errors = []
            all_results = []
            timestamp_preview_sessions = {}
            preview_split_data = {}

            existing_tracks = set()
            if mixsplitr_memory and hasattr(mixsplitr_memory, "scan_existing_library"):
                try:
                    existing_tracks = mixsplitr_memory.scan_existing_library(config["output_directory"])
                except Exception:
                    existing_tracks = set()
            processing_lock = threading.Lock()

            # The first _ANALYSIS_SHARE percent of the bar is reserved for the
            # duration-analysis phase; the remaining portion covers identification.
            _ANALYSIS_SHARE = 0.0 if self.skip_mix_analysis else 5.0
            self._progress_proc_start = _ANALYSIS_SHARE

            def _analysis_progress(completed, total):
                pct = int((completed / max(1, total)) * _ANALYSIS_SHARE)
                self.progress_update.emit(max(0, min(int(_ANALYSIS_SHARE), pct)))
                self.stage_update.emit(f"Reading durations: {completed} / {total}")

            mix_flags = {}
            mix_count = 0
            max_duration_min = 0.0
            if self.skip_mix_analysis:
                # Direct Identifier runs skip the mix-analysis phase and
                # should process dropped files directly as single tracks.
                # Splitter-queued runs set skip_mix_analysis=False and
                # preserve their chosen split workflow separately.
                mix_flags = {path: False for path in self.files}
                if internal_mode == mode_auto:
                    self.stage_update.emit(
                        "Timestamping mode active — skipping split pre-analysis"
                    )
                else:
                    self.stage_update.emit(
                        "Identification mode active — processing files directly"
                    )
            elif mixsplitr_core and hasattr(mixsplitr_core, "analyze_files_parallel"):
                try:
                    self._check_cancel_requested()
                    self.stage_update.emit(f"Analysing {total_files} file(s) — reading durations…")
                    analysis = mixsplitr_core.analyze_files_parallel(
                        self.files, progress_callback=_analysis_progress
                    )
                    mix_flags = {row.get("file"): bool(row.get("is_mix")) for row in analysis if isinstance(row, dict)}
                    mix_count = sum(1 for v in mix_flags.values() if v)
                    duration_values = []
                    for row in analysis:
                        if not isinstance(row, dict):
                            continue
                        try:
                            duration_values.append(float(row.get("duration_min", 0.0) or 0.0))
                        except Exception:
                            continue
                    max_duration_min = max(duration_values) if duration_values else 0.0
                    if mix_count:
                        self.stage_update.emit(f"Analysis complete — {mix_count} mix(es) detected, {total_files - mix_count} single track(s)")
                    else:
                        self.stage_update.emit(f"Analysis complete — {total_files} single track(s)")
                except Exception:
                    mix_flags = {}
            if not self.skip_mix_analysis and self.long_track_mode_overrides:
                forced_mix_count = 0
                forced_single_count = 0
                for path in self.files:
                    override = self._long_track_mode_override_for(path)
                    if override == "mix":
                        mix_flags[path] = True
                        forced_mix_count += 1
                    elif override == "single":
                        mix_flags[path] = False
                        forced_single_count += 1
                if forced_mix_count or forced_single_count:
                    mix_count = sum(1 for v in mix_flags.values() if v)
                    self.stage_update.emit(
                        "Long-track overrides applied — "
                        f"{forced_mix_count} forced mix, {forced_single_count} forced single"
                    )
            if self.split_mode in ("manual", "assisted") and internal_mode != mode_auto:
                has_mixes = any(bool(mix_flags.get(path, False)) for path in self.files)
                if not has_mixes:
                    self.status_update.emit("Waveform split mode selected, but no mixes detected; using normal processing")

            # --- Build shared recognizer + rate-limiter once ----------------
            # All parallel workers share the same objects so the rate-limiter
            # gates API calls across threads (not per-file), and we only pay
            # the ACRCloud SDK init cost once.
            shared_recognizer = None
            shared_rate_limiter = None
            _mode_acr_v  = getattr(mixsplitr_core, "MODE_ACRCLOUD",       "acrcloud")
            _mode_mb_v   = getattr(mixsplitr_core, "MODE_MB_ONLY",        "musicbrainz_only")
            _mode_dual_v = getattr(mixsplitr_core, "MODE_DUAL",           "dual_best_match")
            _mode_spl_v  = getattr(mixsplitr_core, "MODE_SPLIT_ONLY",     "split_only_no_id")
            timestamp_identifier_mode = str(
                (config or {}).get("auto_tracklist_identifier_mode", "") or ""
            ).strip().lower()
            timestamp_with_id = bool(
                internal_mode == mode_auto
                and not bool((config or {}).get("auto_tracklist_no_identify", False))
            )
            if internal_mode not in (mode_auto, _mode_mb_v, _mode_spl_v):
                # ACRCloud and Dual both need the recognizer + rate-limiter
                shared_recognizer = self._create_acr_recognizer(config)
                if shared_recognizer is None:
                    if internal_mode == _mode_dual_v:
                        # Dual without ACRCloud → fall back to MB-only
                        self.status_update.emit("ACRCloud keys unavailable; Dual mode falling back to MusicBrainz / AcoustID")
                        internal_mode = _mode_mb_v
                    else:
                        self.status_update.emit("ACRCloud keys unavailable; using MusicBrainz / AcoustID fallback")
                        internal_mode = _mode_mb_v
                elif mixsplitr_core and hasattr(mixsplitr_core, "RateLimiter"):
                    shared_rate_limiter = mixsplitr_core.RateLimiter(min_interval=1.2)
            elif timestamp_with_id and timestamp_identifier_mode in ("acrcloud", "dual_best_match"):
                shared_recognizer = self._create_acr_recognizer(config)
                if shared_recognizer is None:
                    self.status_update.emit(
                        "Timestamping was set to use ACRCloud, but ACRCloud is unavailable; "
                        "using non-ACR fallback sources where available"
                    )
                elif mixsplitr_core and hasattr(mixsplitr_core, "RateLimiter"):
                    shared_rate_limiter = mixsplitr_core.RateLimiter(min_interval=1.2)

            # Worker count is tuned to the real bottleneck for each mode:
            #   ACRCloud / Dual — shared rate-limiter gates to 1 call/1.2 s;
            #                     8 workers keep audio loading + fingerprinting
            #                     in-flight while others wait for the API.
            #   MB/AcoustID    — MusicBrainz requests max ~1 req/s; 6 workers.
            #   Split Only     — no API; bounded by disk I/O and CPU only.
            if internal_mode == _mode_spl_v:
                _parallel_workers = 12
            elif internal_mode == _mode_mb_v:
                _parallel_workers = 6
            elif internal_mode == _mode_dual_v:
                # Dual fires ACRCloud and AcoustID simultaneously, but they hit
                # separate servers — no combined API pressure on either one.
                # Same ceiling as plain ACRCloud since the shared rate-limiter
                # already handles ACRCloud spacing independently.
                _parallel_workers = 8
            else:  # ACRCloud
                _parallel_workers = 8

            # Memory guard for long-running GUI sessions:
            # decoding long audio files in parallel can exhaust RAM on Windows.
            if total_files >= 1000:
                _parallel_workers = min(_parallel_workers, 4)
            if max_duration_min >= 8.0:
                _parallel_workers = min(_parallel_workers, 4)
            if max_duration_min >= 15.0 or mix_count > 0:
                _parallel_workers = min(_parallel_workers, 3)
            if max_duration_min >= 30.0:
                _parallel_workers = min(_parallel_workers, 2)

            if _parallel_workers < 1:
                _parallel_workers = 1
            if internal_mode == mode_auto:
                self.stage_update.emit("Timestamping mode active — preparing timeline scan")
            elif _parallel_workers < 8:
                self.stage_update.emit(
                    f"Memory guard active — using {_parallel_workers} parallel worker(s)"
                )
            completed_count = 0

            if internal_mode == mode_auto:
                # Auto-tracklist mode: keep sequential (it does its own scanning)
                for index, file_path in enumerate(self.files):
                    self._check_cancel_requested()
                    file_name = os.path.basename(file_path)
                    self.status_update.emit(f"Processing ({index+1}/{total_files}): {file_name}")
                    if not os.path.exists(file_path):
                        errors.append(f"{file_name} (file not found)")
                        continue
                    try:
                        if mixsplitr_autotracklist is None:
                            raise RuntimeError("Auto-tracklist module is unavailable.")
                        self.status_update.emit(f"Timestamping ({index+1}/{total_files}): {file_name}")
                        ident_count, fallback_count, output_count, preview_tracks, preview_session = self._process_auto_tracklist_file(
                            file_path,
                            config,
                            shared_recognizer,
                            file_index=index,
                            total_files=total_files,
                        )
                        identified_total += ident_count
                        unidentified_total += fallback_count
                        auto_output_total += output_count
                        if effective_preview_mode:
                            all_results.extend(list(preview_tracks or []))
                            if isinstance(preview_session, dict):
                                timestamp_preview_sessions.update(preview_session)
                    except Exception as file_exc:
                        errors.append(f"{file_name}: {file_exc}")
                    completed_count += 1
                    _file_pct = _ANALYSIS_SHARE + (
                        completed_count / max(1, total_files)
                    ) * (99.0 - _ANALYSIS_SHARE)
                    self.progress_update.emit(max(int(_ANALYSIS_SHARE), min(99, int(_file_pct))))
            else:
                # Standard mode: adaptively submit files to a thread pool and
                # collect results as they complete so progress updates in real time.
                future_meta = {}  # future -> (index, file_path, file_name, is_mix)
                with ThreadPoolExecutor(max_workers=_parallel_workers) as pool:
                    try:
                        with self._active_executor_lock:
                            self._active_executor = pool
                    except Exception:
                        pass
                    try:
                        next_submit_index = 0
                        self._memory_throttle_last_limit = None
                        while next_submit_index < total_files or future_meta:
                            self._check_cancel_requested()
                            active_limit, free_ram_mb = self._memory_aware_worker_limit(_parallel_workers)
                            self._emit_memory_throttle_state(active_limit, _parallel_workers, free_ram_mb)

                            while next_submit_index < total_files and len(future_meta) < active_limit:
                                index = next_submit_index
                                file_path = self.files[index]
                                file_name = os.path.basename(file_path)
                                next_submit_index += 1

                                if not os.path.exists(file_path):
                                    errors.append(f"{file_name} (file not found)")
                                    completed_count += 1
                                    _file_pct = _ANALYSIS_SHARE + (
                                        completed_count / max(1, total_files)
                                    ) * (99.0 - _ANALYSIS_SHARE)
                                    self.progress_update.emit(max(int(_ANALYSIS_SHARE), min(99, int(_file_pct))))
                                    continue

                                is_mix = mix_flags.get(file_path, False)
                                self.status_update.emit(
                                    f"Queuing ({index+1}/{total_files}): {file_name}"
                                )
                                fut = pool.submit(
                                    self._process_standard_file,
                                    file_path=file_path,
                                    file_num=index + 1,
                                    is_mix=is_mix,
                                    mode_value=internal_mode,
                                    config=config,
                                    existing_tracks=existing_tracks,
                                    lock=processing_lock,
                                    preview_mode=effective_preview_mode,
                                    full_preview_temp_folder=full_preview_temp_folder,
                                    file_index=index,
                                    total_files=total_files,
                                    shared_recognizer=shared_recognizer,
                                    shared_rate_limiter=shared_rate_limiter,
                                )
                                future_meta[fut] = (index, file_path, file_name, is_mix)

                            if not future_meta:
                                continue

                            done, _pending = wait(
                                tuple(future_meta.keys()),
                                timeout=0.35,
                                return_when=FIRST_COMPLETED,
                            )
                            if not done:
                                continue

                            for fut in done:
                                self._check_cancel_requested()
                                index, file_path, file_name, is_mix = future_meta.pop(
                                    fut, (0, "", "<unknown>", False)
                                )
                                completed_count += 1
                                try:
                                    (
                                        file_identified,
                                        file_unidentified,
                                        file_skipped,
                                        file_results,
                                        file_split_cache,
                                    ) = fut.result()
                                    identified_total += file_identified
                                    unidentified_total += file_unidentified
                                    skipped_total += file_skipped
                                    if effective_preview_mode:
                                        all_results.extend(file_results)
                                        if isinstance(file_split_cache, dict):
                                            split_key = os.path.abspath(
                                                str(
                                                    file_split_cache.get("path")
                                                    or file_path
                                                    or ""
                                                ).strip()
                                            )
                                            if split_key:
                                                preview_split_data[split_key] = dict(file_split_cache)
                                    self.stage_update.emit(
                                        f"Completed {completed_count}/{total_files} — "
                                        f"{identified_total} identified so far"
                                    )
                                except Exception as file_exc:
                                    errors.append(f"{file_name}: {file_exc}")
                                _file_pct = _ANALYSIS_SHARE + (
                                    completed_count / max(1, total_files)
                                ) * (99.0 - _ANALYSIS_SHARE)
                                self.progress_update.emit(max(int(_ANALYSIS_SHARE), min(99, int(_file_pct))))
                    finally:
                        try:
                            with self._active_executor_lock:
                                if self._active_executor is pool:
                                    self._active_executor = None
                        except Exception:
                            pass

            self.progress_update.emit(100)
            if effective_preview_mode:
                if internal_mode == mode_auto:
                    all_results.sort(
                        key=lambda track: (
                            int((track or {}).get("source_file_index", 0) or 0),
                            float((track or {}).get("start_time", 0.0) or 0.0),
                            int((track or {}).get("index", 0) or 0),
                        )
                    )
                    cache_data = {
                        "tracks": all_results,
                        "output_folder": config["output_directory"],
                        "artwork_cache": {},
                        "light_preview": False,
                        "split_data": {},
                        "timestamp_sessions": timestamp_preview_sessions,
                        "config_snapshot": {
                            "identification_mode": internal_mode,
                            "timestamp_editor_mode": True,
                            "timestamp_no_identify": bool(config.get("auto_tracklist_no_identify", False)),
                            "timestamp_identifier_mode": str(
                                config.get("auto_tracklist_identifier_mode", "") or ""
                            ).strip(),
                        },
                    }
                else:
                    artwork_cache_b64 = {}
                    artwork_urls = [r.get("art_url") for r in all_results if r.get("status") == "identified" and r.get("art_url")]
                    if artwork_urls and mixsplitr_identify and hasattr(mixsplitr_identify, "batch_download_artwork"):
                        try:
                            raw_art = mixsplitr_identify.batch_download_artwork(artwork_urls) or {}
                            for url, art_bytes in raw_art.items():
                                try:
                                    artwork_cache_b64[str(url)] = base64.b64encode(art_bytes).decode("utf-8")
                                except Exception:
                                    pass
                        except Exception:
                            artwork_cache_b64 = {}

                    runtime_status = None
                    if mixsplitr_essentia and hasattr(mixsplitr_essentia, "get_runtime_status"):
                        try:
                            runtime_status = mixsplitr_essentia.get_runtime_status()
                        except Exception:
                            runtime_status = None
                    essentia_snapshot = {}
                    if mixsplitr_core and hasattr(mixsplitr_core, "build_essentia_config_snapshot"):
                        try:
                            essentia_snapshot = mixsplitr_core.build_essentia_config_snapshot(
                                config,
                                runtime_status=runtime_status,
                            )
                        except Exception:
                            essentia_snapshot = {}

                    cache_data = {
                        "tracks": all_results,
                        "output_folder": config["output_directory"],
                        "artwork_cache": artwork_cache_b64,
                        "light_preview": bool(self.light_preview),
                        "split_data": preview_split_data,
                        "config_snapshot": {
                            "identification_mode": internal_mode,
                            "shazam_enabled": not bool(config.get("disable_shazam", False)),
                            "use_local_bpm": not bool(config.get("disable_local_bpm", False)),
                            "show_id_source": bool(config.get("show_id_source", True)),
                            "fingerprint_sample_seconds": int(config.get("fingerprint_sample_seconds", 12)),
                            "fingerprint_sample_seconds_multi": int(config.get("fingerprint_sample_seconds_multi", 12)),
                            "fingerprint_probe_mode": str(config.get("fingerprint_probe_mode", "single")),
                            "essentia_genre_enrichment_enabled": bool(
                                config.get("essentia_genre_enrichment_enabled", True)
                            ),
                            "essentia_genre_enrichment_when_missing_only": bool(
                                config.get("essentia_genre_enrichment_when_missing_only", True)
                            ),
                            "essentia_genre_enrichment_min_confidence": float(
                                config.get("essentia_genre_enrichment_min_confidence", 0.34)
                            ),
                            "essentia_genre_enrichment_max_tags": int(
                                config.get("essentia_genre_enrichment_max_tags", 2)
                            ),
                            "essentia_genre_enrichment_analysis_seconds": int(
                                config.get("essentia_genre_enrichment_analysis_seconds", 28)
                            ),
                            **essentia_snapshot,
                        },
                    }

                saved = False
                if mixsplitr_editor is None:
                    _lazy_import_backend("mixsplitr_editor")
                if mixsplitr_editor and hasattr(mixsplitr_editor, "save_preview_cache"):
                    saved = bool(mixsplitr_editor.save_preview_cache(cache_data, self.preview_cache_path))
                else:
                    try:
                        with open(self.preview_cache_path, "w", encoding="utf-8") as cache_file:
                            json.dump(cache_data, cache_file)
                        saved = True
                    except Exception:
                        saved = False

                if not saved:
                    self.finished_update.emit(False, "Preview analysis completed, but preview cache could not be saved.")
                    return

                self.preview_cache_data = cache_data
                if internal_mode == mode_auto:
                    message = (
                        f"Timestamp review ready: {identified_total} identified segments, "
                        f"{unidentified_total} fallback labels, {len(timestamp_preview_sessions)} file(s)."
                    )
                elif internal_mode == mode_split_only:
                    track_label = "track" if identified_total == 1 else "tracks"
                    message = f"Preview ready: {identified_total} split {track_label}"
                    if skipped_total:
                        message += f", {skipped_total} skipped."
                    else:
                        message += "."
                else:
                    message = (
                        f"Preview ready: {identified_total} identified, {unidentified_total} unidentified, "
                        f"{skipped_total} skipped."
                    )
            elif internal_mode == mode_auto:
                message = (
                    f"Finished: {identified_total} identified timeline segments, "
                    f"{unidentified_total} fallback labels, {auto_output_total} output file(s)."
                )
            elif internal_mode == mode_split_only:
                track_label = "track" if identified_total == 1 else "tracks"
                message = f"Finished: split into {identified_total} {track_label}"
                if skipped_total:
                    message += f", {skipped_total} skipped."
                else:
                    message += "."
            else:
                message = (
                    f"Finished: {identified_total} identified, {unidentified_total} unidentified, "
                    f"{skipped_total} skipped."
                )
            if errors:
                first_error = errors[0] if errors else ""
                message += f" {len(errors)} file(s) failed. First error: {first_error}"
                self.finished_update.emit(False, message)
                return
            self.finished_update.emit(True, message)

        except ProcessingCancelledError as cancelled:
            self.finished_update.emit(False, str(cancelled))
        except Exception as e:
            self.finished_update.emit(False, f"Error: {str(e)}")
