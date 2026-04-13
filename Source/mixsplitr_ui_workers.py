import json
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QThread, Signal


def _safe_call_getter(getter):
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


class PreviewExportThread(QThread):
    status_update = Signal(str)
    finished_update = Signal(bool, str)

    def __init__(
        self,
        cache_path,
        temp_folder,
        output_format="flac",
        pipeline_getter=None,
        lazy_import_backend=None,
    ):
        super().__init__()
        self.cache_path = str(cache_path or "")
        self.temp_folder = str(temp_folder or "")
        self.output_format = str(output_format or "flac")
        self._pipeline_getter = pipeline_getter
        self._lazy_import_backend = lazy_import_backend

    def _resolve_pipeline(self):
        pipeline = _safe_call_getter(self._pipeline_getter)
        if pipeline is None and callable(self._lazy_import_backend):
            try:
                self._lazy_import_backend("mixsplitr_pipeline")
            except Exception:
                pass
            pipeline = _safe_call_getter(self._pipeline_getter)
        return pipeline

    def run(self):
        pipeline = self._resolve_pipeline()
        if not pipeline or not hasattr(pipeline, "apply_from_cache"):
            self.finished_update.emit(False, "Preview export is unavailable")
            return
        self.status_update.emit("Exporting preview session...")
        try:
            applied = bool(
                pipeline.apply_from_cache(
                    cache_path=self.cache_path,
                    temp_audio_folder=self.temp_folder,
                    output_format=self.output_format,
                )
            )
        except Exception as exc:
            self.finished_update.emit(False, f"Preview export failed: {exc}")
            return

        if applied:
            self.finished_update.emit(True, "Preview export complete")
        else:
            self.finished_update.emit(False, "Preview export canceled or no files exported")


class ApiValidationThread(QThread):
    result_ready = Signal(str, int, bool, str, bool)

    def __init__(
        self,
        api_name,
        token,
        payload=None,
        core_getter=None,
        identify_getter=None,
        lazy_import_backend=None,
    ):
        super().__init__()
        self.api_name = str(api_name or "").strip().lower()
        self.token = int(token)
        self.payload = dict(payload or {})
        self._core_getter = core_getter
        self._identify_getter = identify_getter
        self._lazy_import_backend = lazy_import_backend

    def _core(self):
        return _safe_call_getter(self._core_getter)

    def _identify(self, lazy=False):
        identify = _safe_call_getter(self._identify_getter)
        if identify is None and lazy and callable(self._lazy_import_backend):
            try:
                self._lazy_import_backend("mixsplitr_identify")
            except Exception:
                pass
            identify = _safe_call_getter(self._identify_getter)
        return identify

    def _current_version(self):
        core = self._core()
        return str(getattr(core, "CURRENT_VERSION", "8.0") or "8.0").strip() or "8.0"

    def _validate_acrcloud(self):
        core = self._core()
        if not core or not hasattr(core, "validate_acrcloud_credentials"):
            return False, "ACRCloud validation unavailable in this environment", True

        host = str(self.payload.get("host", "")).strip()
        access_key = str(self.payload.get("access_key", "")).strip()
        access_secret = str(self.payload.get("access_secret", "")).strip()
        timeout = int(self.payload.get("timeout", 10) or 10)
        if not (host and access_key and access_secret):
            return False, "Missing host, access key, or secret", True

        config = {
            "host": host,
            "access_key": access_key,
            "access_secret": access_secret,
            "timeout": timeout,
        }

        try:
            ok, error_message = core.validate_acrcloud_credentials(config)
        except Exception as exc:
            return False, f"ACRCloud test failed: {exc}", True
        if ok:
            return True, "ACRCloud credentials are valid", False
        return False, f"ACRCloud invalid: {error_message or 'unknown error'}", True

    def _validate_acoustid(self):
        identify = self._identify(lazy=True)
        if not identify:
            return False, "AcoustID lookup is unavailable", True
        if not hasattr(identify, "check_chromaprint_available"):
            return False, "AcoustID test tooling unavailable", True

        key = str(self.payload.get("acoustid_api_key", "")).strip()
        if not key:
            return False, "Missing AcoustID API key", True

        fpcalc_ok = False
        try:
            fpcalc_ok, _fpcalc_path = identify.check_chromaprint_available()
        except Exception:
            fpcalc_ok = False
        if not fpcalc_ok:
            return False, "Chromaprint/fpcalc not found", True

        if hasattr(identify, "setup_musicbrainz"):
            core = self._core()
            version = str(getattr(core, "CURRENT_VERSION", "8.0") or "8.0").strip() or "8.0"
            repo = str(getattr(core, "GITLAB_REPO", "chefkjd/MixSplitR") or "chefkjd/MixSplitR").strip()
            try:
                identify.setup_musicbrainz(version, repo)
            except Exception:
                pass

        if hasattr(identify, "set_acoustid_api_key"):
            try:
                identify.set_acoustid_api_key(key)
            except Exception:
                pass

        try:
            available = bool(
                hasattr(identify, "is_acoustid_available")
                and identify.is_acoustid_available()
            )
        except Exception:
            available = False

        if available:
            return True, "AcoustID configuration looks valid", False
        return False, "AcoustID library (pyacoustid) not installed or failed to import", True

    def _validate_lastfm(self):
        key = str(self.payload.get("lastfm_api_key", "")).strip()
        if not key:
            return False, "Missing Last.fm API key", True

        url = (
            "https://ws.audioscrobbler.com/2.0/"
            f"?method=chart.gettoptracks&limit=1&api_key={urllib.parse.quote(key, safe='')}&format=json"
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": f"MixSplitR/{self._current_version()}"}
        )

        payload = None
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                body = response.read()
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                payload = None
        except Exception as exc:
            return False, f"Last.fm test failed: {exc}", True

        if not isinstance(payload, dict):
            return False, "Unexpected Last.fm response", True

        error_code = payload.get("error")
        if error_code:
            msg = str(payload.get("message") or "unknown error")
            return False, f"Last.fm error: {msg} (code {error_code})", True

        tracks = payload.get("tracks")
        if isinstance(tracks, dict):
            return True, "Last.fm API key is valid", False
        return False, "Last.fm response missing track data", True

    def run(self):
        ok = False
        message = "Validation unavailable"
        error = True
        try:
            if self.api_name == "acrcloud":
                ok, message, error = self._validate_acrcloud()
            elif self.api_name == "acoustid":
                ok, message, error = self._validate_acoustid()
            elif self.api_name == "lastfm":
                ok, message, error = self._validate_lastfm()
            else:
                ok, message, error = False, "Unknown API validator", True
        except Exception as exc:
            ok, message, error = False, f"Validation failed: {exc}", True
        self.result_ready.emit(self.api_name, self.token, bool(ok), str(message or ""), bool(error))


def _version_parts_for_update(version_text, core_module=None):
    parser = getattr(core_module, "_parse_version_parts", None) if core_module else None
    if callable(parser):
        try:
            parts = parser(version_text)
            if isinstance(parts, (list, tuple)):
                return [int(p) for p in parts if str(p).isdigit()]
        except Exception:
            pass

    parts = []
    token = ""
    for ch in str(version_text or ""):
        if ch.isdigit():
            token += ch
        elif token:
            try:
                parts.append(int(token))
            except Exception:
                pass
            token = ""
    if token:
        try:
            parts.append(int(token))
        except Exception:
            pass
    return parts


def _is_newer_version_for_update(latest, current, core_module=None):
    checker = getattr(core_module, "_is_newer_version", None) if core_module else None
    if callable(checker):
        try:
            return bool(checker(str(latest or ""), str(current or "")))
        except Exception:
            pass

    latest_parts = _version_parts_for_update(latest, core_module=core_module)
    current_parts = _version_parts_for_update(current, core_module=core_module)
    if not latest_parts or not current_parts:
        return False
    max_len = max(len(latest_parts), len(current_parts))
    latest_parts += [0] * (max_len - len(latest_parts))
    current_parts += [0] * (max_len - len(current_parts))
    return latest_parts > current_parts


def _fetch_latest_release_from_github(core_module=None):
    repo = str(getattr(core_module, "GITHUB_REPO", "chefkjd/MixSplitR") or "chefkjd/MixSplitR").strip()
    release_page_url = f"https://github.com/{repo}/releases"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MixSplitR-UI-UpdateCheck",
    }
    candidates = []

    def _request_json(url):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)

    error_messages = []

    try:
        release = _request_json(f"https://api.github.com/repos/{repo}/releases/latest")
        if isinstance(release, dict):
            latest = str(release.get("tag_name") or release.get("name") or "").strip().lstrip("v")
            latest_url = str(release.get("html_url") or release_page_url).strip() or release_page_url
            if _version_parts_for_update(latest, core_module=core_module):
                candidates.append(
                    {"latest": latest, "url": latest_url, "release_page_url": release_page_url}
                )
    except Exception as exc:
        error_messages.append(str(exc))

    try:
        tags = _request_json(f"https://api.github.com/repos/{repo}/tags?per_page=20")
        if isinstance(tags, list):
            for tag in tags:
                raw_name = str((tag or {}).get("name") or "").strip()
                if not raw_name:
                    continue
                tag_version = raw_name.lstrip("v")
                if _version_parts_for_update(tag_version, core_module=core_module):
                    candidates.append(
                        {
                            "latest": tag_version,
                            "url": f"https://github.com/{repo}/releases/tag/{raw_name}",
                            "release_page_url": release_page_url,
                        }
                    )
    except Exception as exc:
        error_messages.append(str(exc))

    if not candidates:
        message = error_messages[0] if error_messages else "Could not read GitHub release information."
        return None, message

    best = max(
        candidates,
        key=lambda item: _version_parts_for_update(item.get("latest", ""), core_module=core_module),
    )
    return best, ""


class UpdateCheckThread(QThread):
    result_ready = Signal(object)

    def __init__(self, current_version_text, core_getter=None, parent=None):
        super().__init__(parent)
        self.current_version_text = str(current_version_text or "").strip()
        self._core_getter = core_getter

    def run(self):
        core = _safe_call_getter(self._core_getter)
        repo = str(getattr(core, "GITHUB_REPO", "chefkjd/MixSplitR") or "chefkjd/MixSplitR").strip()
        payload = {
            "status": "error",
            "current": self.current_version_text,
            "latest": "",
            "url": "",
            "release_page_url": f"https://github.com/{repo}/releases",
            "message": "",
        }
        if self.isInterruptionRequested():
            self.result_ready.emit(payload)
            return

        info, error_message = _fetch_latest_release_from_github(core_module=core)
        if self.isInterruptionRequested():
            self.result_ready.emit(payload)
            return

        if info is None:
            payload["message"] = str(error_message or "Update check failed")
            self.result_ready.emit(payload)
            return

        latest = str(info.get("latest") or "").strip()
        payload["latest"] = latest
        payload["url"] = str(info.get("url") or info.get("release_page_url") or payload["release_page_url"]).strip()
        payload["release_page_url"] = str(info.get("release_page_url") or payload["release_page_url"]).strip()

        if latest and _is_newer_version_for_update(latest, self.current_version_text, core_module=core):
            payload["status"] = "update_available"
        elif latest and _is_newer_version_for_update(self.current_version_text, latest, core_module=core):
            payload["status"] = "ahead_of_release"
        else:
            payload["status"] = "up_to_date"
        self.result_ready.emit(payload)


class SessionEditorLoaderThread(QThread):
    """Background loader for session editor cache building.

    Runs ``_build_session_editor_cache`` off the main thread so the GUI stays
    responsive when opening sessions with thousands of tracks.
    """

    progress_update = Signal(str)
    finished = Signal(bool, object, str)  # (success, cache_data_or_None, error_message)

    def __init__(self, manifest, manifest_path, session_module_getter=None, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.manifest_path = str(manifest_path or "")
        self._session_getter = session_module_getter

    def run(self):
        session_mod = _safe_call_getter(self._session_getter)
        if session_mod is None or not hasattr(session_mod, "_build_session_editor_cache"):
            self.finished.emit(False, None, "Session editor is unavailable")
            return

        track_count = len((self.manifest or {}).get("tracks", []) or [])
        self.progress_update.emit(f"Loading session editor ({track_count} tracks)...")

        if self.isInterruptionRequested():
            self.finished.emit(False, None, "Cancelled")
            return

        try:
            cache_data = session_mod._build_session_editor_cache(
                self.manifest, self.manifest_path
            )
        except Exception as exc:
            self.finished.emit(False, None, f"Failed to build editor cache: {exc}")
            return

        if self.isInterruptionRequested():
            self.finished.emit(False, None, "Cancelled")
            return

        loaded_count = len((cache_data or {}).get("tracks", []) or [])
        if not loaded_count:
            self.finished.emit(False, None, "Session has no tracks to edit")
            return

        self.progress_update.emit(f"Loaded {loaded_count} tracks")
        self.finished.emit(True, cache_data, "")
