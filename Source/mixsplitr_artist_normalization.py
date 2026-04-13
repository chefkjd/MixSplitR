"""Artist folder canonicalization for MixSplitR exports.

This module supports three modes:
- off: no artist normalization
- collab_only: legacy feat/& split normalization (handled in mixsplitr_tagging)
- smart: scored canonicalization for output folder routing only

Smart mode keeps original detected artist tags intact and only sets
``enhanced_metadata['output_folder_name']`` so exports are grouped safely.
"""

from __future__ import annotations

import json
import os
import re
import difflib
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


MODE_OFF = "off"
MODE_COLLAB_ONLY = "collab_only"
MODE_SMART = "smart"
VALID_MODES = (MODE_OFF, MODE_COLLAB_ONLY, MODE_SMART)

DEFAULT_MODE = MODE_COLLAB_ONLY
DEFAULT_STRICTNESS = 0.92
MIN_STRICTNESS = 0.75
MAX_STRICTNESS = 0.99
AUTO_LEARN_MIN_CONFIDENCE = 0.97

_ALIAS_FILENAME = "artist_aliases.json"
_ALIAS_VERSION = 1

_WORD_SPLIT_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE_RE = re.compile(r"\s+")

_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "first": "1st",
    "two": "2",
    "second": "2nd",
    "three": "3",
    "third": "3rd",
    "four": "4",
    "fourth": "4th",
    "five": "5",
    "fifth": "5th",
    "six": "6",
    "sixth": "6th",
    "seven": "7",
    "seventh": "7th",
    "eight": "8",
    "eighth": "8th",
    "nine": "9",
    "ninth": "9th",
    "ten": "10",
    "tenth": "10th",
}

_COMMON_WORD_FIXES = {
    # Observed misspelling variant support
    "fith": "fifth",
}

_BACKING_BAND_RE = re.compile(r"^(?P<solo>.+?)\s+(?:and|&)\s+the\s+.+$", re.IGNORECASE)


def normalize_artist_mode(value: Any, legacy_normalize_artists: Any = True) -> str:
    """Normalize configured artist-normalization mode with legacy fallback."""
    if isinstance(value, bool):
        return MODE_COLLAB_ONLY if value else MODE_OFF

    text = str(value or "").strip().lower()
    if text in VALID_MODES:
        return text

    if text in ("on", "enabled", "true", "1", "yes"):
        return MODE_COLLAB_ONLY
    if text in ("off", "disabled", "false", "0", "no"):
        return MODE_OFF

    return MODE_COLLAB_ONLY if bool(legacy_normalize_artists) else MODE_OFF


def normalize_strictness(value: Any, default: float = DEFAULT_STRICTNESS) -> float:
    """Clamp strictness to a safe threshold range."""
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(MIN_STRICTNESS, min(MAX_STRICTNESS, parsed))


def resolve_settings(runtime_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return normalized artist canonicalization settings from runtime config."""
    config = runtime_config if isinstance(runtime_config, dict) else {}
    legacy_flag = bool(config.get("normalize_artists", True))
    mode = normalize_artist_mode(config.get("artist_normalization_mode"), legacy_flag)
    strictness = normalize_strictness(config.get("artist_normalization_strictness", DEFAULT_STRICTNESS))
    collapse_backing_band = bool(config.get("artist_normalization_collapse_backing_band", False))
    review_ambiguous = bool(config.get("artist_normalization_review_ambiguous", True))
    return {
        "mode": mode,
        "strictness": strictness,
        "collapse_backing_band": collapse_backing_band,
        "review_ambiguous": review_ambiguous,
    }


def _default_alias_payload() -> Dict[str, Any]:
    return {
        "version": _ALIAS_VERSION,
        "manual_overrides": {},
        "auto_aliases": {},
    }


def _get_alias_map_path() -> str:
    try:
        from mixsplitr_core import get_app_data_dir

        app_dir = str(get_app_data_dir())
    except Exception:
        app_dir = os.path.join(os.path.expanduser("~"), ".mixsplitr")
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, _ALIAS_FILENAME)


def _load_alias_payload() -> Tuple[Dict[str, Any], str]:
    path = _get_alias_map_path()
    payload = _default_alias_payload()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                payload.update(loaded)
    except Exception:
        payload = _default_alias_payload()

    manual = payload.get("manual_overrides")
    auto = payload.get("auto_aliases")
    if not isinstance(manual, dict):
        manual = {}
    if not isinstance(auto, dict):
        auto = {}

    normalized_manual: Dict[str, str] = {}
    for alias, canonical in manual.items():
        alias_key = _artist_key(alias)
        canonical_text = str(canonical or "").strip()
        if alias_key and canonical_text:
            normalized_manual[alias_key] = canonical_text

    normalized_auto: Dict[str, Dict[str, Any]] = {}
    for alias, decision in auto.items():
        alias_key = _artist_key(alias)
        if not alias_key:
            continue
        if not isinstance(decision, dict):
            continue
        canonical_text = str(decision.get("canonical_artist", "")).strip()
        if not canonical_text:
            continue
        normalized_auto[alias_key] = {
            "canonical_artist": canonical_text,
            "confidence": float(decision.get("confidence", 0.0) or 0.0),
            "reason": str(decision.get("reason", "auto_alias")).strip() or "auto_alias",
            "evidence": [str(item) for item in (decision.get("evidence") or []) if str(item).strip()],
            "learned_at": str(decision.get("learned_at", "")).strip(),
            "usage_count": int(decision.get("usage_count", 0) or 0),
        }

    payload = {
        "version": _ALIAS_VERSION,
        "manual_overrides": normalized_manual,
        "auto_aliases": normalized_auto,
    }
    return payload, path


def _save_alias_payload(payload: Dict[str, Any], path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _normalize_text(text: Any) -> str:
    value = str(text or "").strip().lower()
    if not value:
        return ""
    value = value.replace("&", " and ")
    value = _PUNCT_RE.sub(" ", value)
    value = _MULTI_SPACE_RE.sub(" ", value).strip()
    return value


def _apply_word_fixes(token: str) -> str:
    token = str(token or "").strip().lower()
    return _COMMON_WORD_FIXES.get(token, token)


def _to_singular(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokenize_artist(text: Any) -> List[str]:
    base = _normalize_text(text)
    if not base:
        return []
    tokens = []
    for raw in _WORD_SPLIT_RE.split(base):
        t = _apply_word_fixes(raw)
        if not t:
            continue
        t = _NUMBER_WORDS.get(t, t)
        if t in ("a", "an", "the"):
            continue
        tokens.append(_to_singular(t))
    return [token for token in tokens if token]


def _artist_key(text: Any) -> str:
    return " ".join(_tokenize_artist(text))


def _solo_form(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _BACKING_BAND_RE.match(raw)
    if not match:
        return ""
    return str(match.group("solo") or "").strip()


def _recording_ids_for_track(track: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    if not isinstance(track, dict):
        return ids

    enhanced = track.get("enhanced_metadata") or {}
    if isinstance(enhanced, dict):
        rid = str(enhanced.get("musicbrainz_recording_id", "")).strip()
        if rid:
            ids.add(rid)

    backend = track.get("backend_candidates") or {}
    if isinstance(backend, dict):
        for key in ("recording_id", "musicbrainz_recording_id"):
            rid = str(backend.get(key, "")).strip()
            if rid:
                ids.add(rid)

    return ids


def _token_overlap_score(tokens_a: Iterable[str], tokens_b: Iterable[str]) -> float:
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


def _sequence_score(text_a: str, text_b: str) -> float:
    if not text_a or not text_b:
        return 0.0
    return float(difflib.SequenceMatcher(a=text_a, b=text_b).ratio())


def _score_pair(
    left: str,
    right: str,
    left_stats: Dict[str, Any],
    right_stats: Dict[str, Any],
    collapse_backing_band: bool,
) -> Tuple[float, str, List[str]]:
    """Score merge confidence from strict-to-loose matching signals."""
    left_key = left_stats.get("artist_key", "")
    right_key = right_stats.get("artist_key", "")
    left_tokens = left_stats.get("tokens", [])
    right_tokens = right_stats.get("tokens", [])

    evidence: List[str] = []
    score = 0.0
    reason = "no_match"

    # 1) MusicBrainz recording corroboration (very high confidence).
    left_ids = left_stats.get("recording_ids", set())
    right_ids = right_stats.get("recording_ids", set())
    shared_ids = set(left_ids) & set(right_ids)
    if shared_ids:
        score = 0.992
        reason = "musicbrainz_recording_overlap"
        evidence.append(f"shared_recording_ids={len(shared_ids)}")

    # 2) Text normalization signal.
    if left_key and right_key and left_key == right_key:
        score = max(score, 0.955)
        if reason == "no_match":
            reason = "text_normalization"
        evidence.append("normalized_tokens_match")

    token_overlap = _token_overlap_score(left_tokens, right_tokens)
    seq_ratio = _sequence_score(left_key, right_key)

    # 3) Fuzzy/token signal.
    fuzzy_conf = (0.55 * token_overlap) + (0.45 * seq_ratio)
    if token_overlap >= 0.75 and seq_ratio >= 0.80:
        score = max(score, min(0.95, 0.62 + (fuzzy_conf * 0.35)))
        if reason == "no_match":
            reason = "fuzzy_similarity"
        evidence.append(f"token_overlap={token_overlap:.3f}")
        evidence.append(f"sequence_similarity={seq_ratio:.3f}")

    # 4) Backing-band collapse signal.
    if collapse_backing_band:
        left_solo = _solo_form(left)
        right_solo = _solo_form(right)
        left_solo_key = _artist_key(left_solo) if left_solo else ""
        right_solo_key = _artist_key(right_solo) if right_solo else ""

        band_pair = False
        if left_solo_key and left_solo_key == right_key:
            band_pair = True
        elif right_solo_key and right_solo_key == left_key:
            band_pair = True
        elif left_solo_key and right_solo_key and left_solo_key == right_solo_key:
            band_pair = True

        if band_pair:
            band_score = 0.78
            if shared_ids:
                band_score = 0.97
            elif token_overlap >= 0.55 and seq_ratio >= 0.72:
                band_score = 0.87
            score = max(score, band_score)
            if reason == "no_match":
                reason = "backing_band_collapse"
            evidence.append("backing_band_form_detected")

    return score, reason, evidence


def _representative_for_cluster(
    cluster: Set[str],
    artist_stats: Dict[str, Dict[str, Any]],
) -> str:
    """Pick a stable canonical artist display name for a merge cluster."""
    ranked = sorted(
        cluster,
        key=lambda artist_name: (
            -int(artist_stats.get(artist_name, {}).get("count", 0)),
            int(len(artist_stats.get(artist_name, {}).get("tokens", []) or [])),
            len(str(artist_stats.get(artist_name, {}).get("artist_key", "") or "")),
            len(str(artist_name)),
            str(artist_name).lower(),
        ),
    )
    return ranked[0] if ranked else ""


def _compute_pair_scores(
    names: List[str],
    artist_stats: Dict[str, Dict[str, Any]],
    collapse_backing_band: bool,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for idx, left in enumerate(names):
        for right in names[idx + 1 :]:
            score, reason, evidence = _score_pair(
                left,
                right,
                artist_stats[left],
                artist_stats[right],
                collapse_backing_band=collapse_backing_band,
            )
            pairs[(left, right)] = {
                "score": float(score),
                "reason": reason,
                "evidence": evidence,
            }
    return pairs


def _build_artist_stats(identified_tracks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    counts = Counter()
    recording_ids: Dict[str, Set[str]] = defaultdict(set)
    for track in identified_tracks:
        artist = str(track.get("artist", "")).strip()
        if not artist:
            continue
        counts[artist] += 1
        recording_ids[artist].update(_recording_ids_for_track(track))

    artist_stats: Dict[str, Dict[str, Any]] = {}
    for artist_name, count in counts.items():
        artist_stats[artist_name] = {
            "count": int(count),
            "tokens": _tokenize_artist(artist_name),
            "artist_key": _artist_key(artist_name),
            "recording_ids": set(recording_ids.get(artist_name, set())),
        }
    return artist_stats


def _resolve_alias_map_decision(
    artist_name: str,
    alias_payload: Dict[str, Any],
    strictness: float,
) -> Optional[Dict[str, Any]]:
    artist_key = _artist_key(artist_name)
    if not artist_key:
        return None

    manual = alias_payload.get("manual_overrides") or {}
    if artist_key in manual:
        canonical = str(manual.get(artist_key, "")).strip()
        if canonical:
            return {
                "canonical_artist": canonical,
                "confidence": 1.0,
                "reason": "manual_override",
                "evidence": ["manual_alias_map"],
                "needs_review": False,
            }

    auto_aliases = alias_payload.get("auto_aliases") or {}
    if artist_key in auto_aliases:
        entry = auto_aliases.get(artist_key) or {}
        canonical = str(entry.get("canonical_artist", "")).strip()
        confidence = float(entry.get("confidence", 0.0) or 0.0)
        if canonical and confidence >= max(0.88, strictness - 0.05):
            evidence = [str(item) for item in (entry.get("evidence") or []) if str(item).strip()]
            if not evidence:
                evidence = ["persisted_auto_alias"]
            return {
                "canonical_artist": canonical,
                "confidence": confidence,
                "reason": str(entry.get("reason", "auto_alias")).strip() or "auto_alias",
                "evidence": evidence,
                "needs_review": False,
            }

    return None


def _build_component_decisions(
    artist_stats: Dict[str, Dict[str, Any]],
    pair_scores: Dict[Tuple[str, str], Dict[str, Any]],
    strictness: float,
    review_ambiguous: bool,
) -> Dict[str, Dict[str, Any]]:
    names = list(artist_stats.keys())

    adjacency: Dict[str, Set[str]] = {name: set() for name in names}
    for (left, right), scored in pair_scores.items():
        if float(scored.get("score", 0.0) or 0.0) >= strictness:
            adjacency[left].add(right)
            adjacency[right].add(left)

    visited: Set[str] = set()
    decisions: Dict[str, Dict[str, Any]] = {}

    for artist_name in names:
        if artist_name in visited:
            continue
        stack = [artist_name]
        component: Set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)

        visited.update(component)
        canonical = _representative_for_cluster(component, artist_stats)

        for member in component:
            if member == canonical:
                decisions[member] = {
                    "canonical_artist": canonical,
                    "confidence": 1.0,
                    "reason": "self",
                    "evidence": ["cluster_representative"],
                    "needs_review": False,
                }
                continue

            pair_key = (member, canonical)
            reverse_pair = (canonical, member)
            scored = pair_scores.get(pair_key) or pair_scores.get(reverse_pair) or {}
            conf = float(scored.get("score", 0.0) or 0.0)
            reason = str(scored.get("reason", "cluster_merge")).strip() or "cluster_merge"
            evidence = [str(item) for item in (scored.get("evidence") or []) if str(item).strip()]
            if not evidence:
                evidence = ["cluster_merge"]
            decisions[member] = {
                "canonical_artist": canonical,
                "confidence": conf,
                "reason": reason,
                "evidence": evidence,
                "needs_review": False,
            }

    # Mark ambiguous near-misses for review without applying merge.
    ambiguous_floor = max(0.55, strictness - 0.10)
    for artist_name in names:
        existing = decisions.get(artist_name)
        if not existing or existing.get("canonical_artist") != artist_name:
            continue

        best_candidate = ""
        best_scored: Dict[str, Any] = {}
        for other in names:
            if other == artist_name:
                continue
            pair_key = (artist_name, other)
            reverse_pair = (other, artist_name)
            scored = pair_scores.get(pair_key) or pair_scores.get(reverse_pair) or {}
            score = float(scored.get("score", 0.0) or 0.0)
            if score > float(best_scored.get("score", 0.0) or 0.0):
                best_scored = scored
                best_candidate = other

        best_score = float(best_scored.get("score", 0.0) or 0.0)
        if best_candidate and ambiguous_floor <= best_score < strictness:
            evidence = [str(item) for item in (best_scored.get("evidence") or []) if str(item).strip()]
            if not evidence:
                evidence = ["near_threshold_match"]
            review_payload = {
                "candidate": best_candidate,
                "score": round(best_score, 3),
                "reason": str(best_scored.get("reason", "near_match")).strip() or "near_match",
                "evidence": evidence,
            }
            if review_ambiguous:
                existing["needs_review"] = True
                existing["review_candidate"] = review_payload
            else:
                # Optional relaxed behavior: if review is disabled and score is close,
                # apply the near-threshold merge.
                if best_score >= max(0.86, strictness - 0.03):
                    decisions[artist_name] = {
                        "canonical_artist": best_candidate,
                        "confidence": best_score,
                        "reason": review_payload["reason"],
                        "evidence": review_payload["evidence"],
                        "needs_review": False,
                    }

    return decisions


def _auto_learn_aliases(
    decisions: Dict[str, Dict[str, Any]],
    alias_payload: Dict[str, Any],
) -> bool:
    changed = False
    auto_aliases = dict(alias_payload.get("auto_aliases") or {})
    manual = alias_payload.get("manual_overrides") or {}

    for raw_artist, decision in decisions.items():
        canonical = str(decision.get("canonical_artist", "")).strip()
        if not canonical:
            continue
        if canonical == raw_artist:
            continue

        confidence = float(decision.get("confidence", 0.0) or 0.0)
        reason = str(decision.get("reason", "")).strip().lower()
        needs_review = bool(decision.get("needs_review", False))

        if needs_review:
            continue
        if reason in ("manual_override", "auto_alias"):
            continue
        if confidence < AUTO_LEARN_MIN_CONFIDENCE:
            continue

        alias_key = _artist_key(raw_artist)
        if not alias_key:
            continue
        if alias_key in manual:
            continue

        existing = auto_aliases.get(alias_key, {}) if isinstance(auto_aliases.get(alias_key), dict) else {}
        current_conf = float(existing.get("confidence", 0.0) or 0.0)
        if existing and current_conf > confidence and str(existing.get("canonical_artist", "")).strip() == canonical:
            continue

        auto_aliases[alias_key] = {
            "canonical_artist": canonical,
            "confidence": confidence,
            "reason": str(decision.get("reason", "smart_merge")).strip() or "smart_merge",
            "evidence": [str(item) for item in (decision.get("evidence") or []) if str(item).strip()],
            "learned_at": datetime.now().isoformat(timespec="seconds"),
            "usage_count": int(existing.get("usage_count", 0) or 0) + 1,
        }
        changed = True

    if changed:
        alias_payload["auto_aliases"] = auto_aliases
    return changed


def _dedupe_preserve_order(values: Iterable[Any]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _track_normalization_payload(track: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(track, dict):
        return {}
    payload = track.get("artist_normalization")
    if isinstance(payload, dict):
        return payload
    enhanced = track.get("enhanced_metadata") or {}
    if isinstance(enhanced, dict):
        candidate = enhanced.get("artist_normalization")
        if isinstance(candidate, dict):
            return candidate
    return {}


def collect_pending_review_entries(
    tracks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return unique ambiguous smart-normalization review entries."""
    aggregated: Dict[str, Dict[str, Any]] = {}

    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        if str(track.get("status", "")).strip().lower() != "identified":
            continue

        payload = _track_normalization_payload(track)
        if not isinstance(payload, dict) or not bool(payload.get("needs_review", False)):
            continue

        raw_artist = str(payload.get("raw_artist") or track.get("artist") or "").strip()
        review_candidate = payload.get("review_candidate") if isinstance(payload.get("review_candidate"), dict) else {}
        candidate_artist = str(review_candidate.get("candidate") or "").strip()
        if not raw_artist or not candidate_artist:
            continue

        score = float(review_candidate.get("score", payload.get("confidence", 0.0)) or 0.0)
        reason = str(review_candidate.get("reason", payload.get("reason", "near_match"))).strip() or "near_match"
        evidence = _dedupe_preserve_order(
            list(payload.get("evidence") or []) + list(review_candidate.get("evidence") or [])
        )

        entry = aggregated.get(raw_artist)
        if entry is None:
            entry = {
                "raw_artist": raw_artist,
                "candidate_artist": candidate_artist,
                "score": score,
                "reason": reason,
                "evidence": evidence,
                "track_count": 0,
            }
            aggregated[raw_artist] = entry
        else:
            entry["track_count"] = int(entry.get("track_count", 0) or 0)
            if score > float(entry.get("score", 0.0) or 0.0):
                entry["score"] = score
                entry["reason"] = reason
            entry["evidence"] = _dedupe_preserve_order(
                list(entry.get("evidence") or []) + evidence
            )
            if not str(entry.get("candidate_artist") or "").strip():
                entry["candidate_artist"] = candidate_artist
        entry["track_count"] = int(entry.get("track_count", 0) or 0) + 1

    return sorted(
        list(aggregated.values()),
        key=lambda item: (
            -int(item.get("track_count", 0) or 0),
            -float(item.get("score", 0.0) or 0.0),
            str(item.get("raw_artist") or "").lower(),
        ),
    )


def apply_review_resolutions(
    tracks: List[Dict[str, Any]],
    resolutions: Dict[str, Dict[str, Any]],
    persist: bool = True,
    debug_callback: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Apply user review decisions for ambiguous smart-normalization matches."""
    resolution_map = resolutions if isinstance(resolutions, dict) else {}
    alias_payload = None
    alias_path = ""
    manual_overrides: Dict[str, str] = {}
    alias_changed = False

    if persist:
        alias_payload, alias_path = _load_alias_payload()
        manual_overrides = dict(alias_payload.get("manual_overrides") or {})

    applied: Dict[str, Dict[str, Any]] = {}

    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        if str(track.get("status", "")).strip().lower() != "identified":
            continue

        payload = dict(_track_normalization_payload(track) or {})
        if not payload or not bool(payload.get("needs_review", False)):
            continue

        raw_artist = str(payload.get("raw_artist") or track.get("artist") or "").strip()
        review_candidate = payload.get("review_candidate") if isinstance(payload.get("review_candidate"), dict) else {}
        candidate_artist = str(review_candidate.get("candidate") or "").strip()
        if not raw_artist:
            continue

        resolution = {}
        if raw_artist in resolution_map and isinstance(resolution_map.get(raw_artist), dict):
            resolution = dict(resolution_map.get(raw_artist) or {})
        else:
            artist_key = _artist_key(raw_artist)
            if artist_key and isinstance(resolution_map.get(artist_key), dict):
                resolution = dict(resolution_map.get(artist_key) or {})

        action = str(resolution.get("action") or "").strip().lower()
        if action not in ("merge", "keep"):
            action = "keep"

        canonical_artist = raw_artist
        confidence = 1.0
        reason = "user_review_keep"
        evidence = _dedupe_preserve_order(
            list(payload.get("evidence") or []) + list(review_candidate.get("evidence") or [])
        )
        if action == "merge" and candidate_artist:
            canonical_artist = str(
                resolution.get("canonical_artist") or candidate_artist
            ).strip() or candidate_artist
            confidence = max(
                float(payload.get("confidence", 0.0) or 0.0),
                float(review_candidate.get("score", 0.0) or 0.0),
            )
            reason = "user_review_merge"
            evidence = _dedupe_preserve_order(list(evidence) + ["user_review_approved"])
        else:
            canonical_artist = raw_artist
            confidence = 1.0
            reason = "user_review_keep"
            evidence = _dedupe_preserve_order(list(evidence) + ["user_review_kept_separate"])

        updated_payload = {
            "mode": MODE_SMART,
            "raw_artist": raw_artist,
            "canonical_artist": canonical_artist,
            "confidence": round(float(confidence), 3),
            "reason": reason,
            "evidence": evidence,
            "needs_review": False,
        }

        enhanced = track.get("enhanced_metadata")
        if not isinstance(enhanced, dict):
            enhanced = {}
            track["enhanced_metadata"] = enhanced
        enhanced["output_folder_name"] = canonical_artist
        enhanced["artist_normalization"] = updated_payload
        track["artist_normalization"] = updated_payload

        applied[raw_artist] = {
            "action": action,
            "canonical_artist": canonical_artist,
        }

        if persist:
            artist_key = _artist_key(raw_artist)
            if artist_key and manual_overrides.get(artist_key) != canonical_artist:
                manual_overrides[artist_key] = canonical_artist
                alias_changed = True

        if callable(debug_callback):
            try:
                debug_callback(
                    f"[artist-normalize/review] {raw_artist} -> {canonical_artist} ({action})"
                )
            except Exception:
                pass

    if persist and alias_payload is not None and alias_changed:
        alias_payload["manual_overrides"] = manual_overrides
        _save_alias_payload(alias_payload, alias_path)

    return applied


def apply_smart_folder_canonicalization(
    tracks: List[Dict[str, Any]],
    runtime_config: Optional[Dict[str, Any]] = None,
    debug_callback: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Apply smart artist canonicalization decisions to identified export tracks.

    The input ``tracks`` list is modified in-place for identified rows by setting:
    - ``track['enhanced_metadata']['output_folder_name']``
    - ``track['artist_normalization']`` (audit payload)

    Returns a map: ``raw_artist -> decision``.
    """
    settings = resolve_settings(runtime_config)
    if settings["mode"] != MODE_SMART:
        return {}

    identified_tracks = []
    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        if str(track.get("status", "")).strip().lower() != "identified":
            continue
        artist = str(track.get("artist", "")).strip()
        if not artist:
            continue
        identified_tracks.append(track)

    if not identified_tracks:
        return {}

    alias_payload, alias_path = _load_alias_payload()
    strictness = float(settings["strictness"])
    collapse_backing_band = bool(settings["collapse_backing_band"])
    review_ambiguous = bool(settings["review_ambiguous"])

    artist_stats = _build_artist_stats(identified_tracks)
    names = list(artist_stats.keys())
    pair_scores = _compute_pair_scores(names, artist_stats, collapse_backing_band=collapse_backing_band)

    decisions = _build_component_decisions(
        artist_stats,
        pair_scores,
        strictness=strictness,
        review_ambiguous=review_ambiguous,
    )

    # Manual and persisted auto alias maps always win over graph inference.
    for artist_name in names:
        alias_decision = _resolve_alias_map_decision(artist_name, alias_payload, strictness=strictness)
        if alias_decision:
            decisions[artist_name] = alias_decision

    # Apply decisions to tracks.
    for track in identified_tracks:
        raw_artist = str(track.get("artist", "")).strip()
        if not raw_artist:
            continue
        decision = decisions.get(raw_artist) or {
            "canonical_artist": raw_artist,
            "confidence": 1.0,
            "reason": "self",
            "evidence": ["no_merge_candidate"],
            "needs_review": False,
        }
        canonical_artist = str(decision.get("canonical_artist", "")).strip() or raw_artist
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        reason = str(decision.get("reason", "self")).strip() or "self"
        evidence = [str(item) for item in (decision.get("evidence") or []) if str(item).strip()]
        needs_review = bool(decision.get("needs_review", False))

        enhanced = track.get("enhanced_metadata")
        if not isinstance(enhanced, dict):
            enhanced = {}
            track["enhanced_metadata"] = enhanced

        enhanced["output_folder_name"] = canonical_artist
        normalization_payload = {
            "mode": MODE_SMART,
            "raw_artist": raw_artist,
            "canonical_artist": canonical_artist,
            "confidence": round(confidence, 3),
            "reason": reason,
            "evidence": evidence,
            "needs_review": needs_review,
        }
        if needs_review and isinstance(decision.get("review_candidate"), dict):
            normalization_payload["review_candidate"] = decision.get("review_candidate")

        enhanced["artist_normalization"] = normalization_payload
        track["artist_normalization"] = normalization_payload

        if callable(debug_callback):
            msg = (
                f"[artist-normalize] {raw_artist} -> {canonical_artist} "
                f"(score={confidence:.3f}, reason={reason})"
            )
            if needs_review:
                msg += " [review]"
            try:
                debug_callback(msg)
            except Exception:
                pass

    if _auto_learn_aliases(decisions, alias_payload):
        _save_alias_payload(alias_payload, alias_path)

    return decisions
