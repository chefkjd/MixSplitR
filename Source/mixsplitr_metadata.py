"""
mixsplitr_metadata.py - External metadata APIs for MixSplitR

Contains:
- iTunes/Apple Music metadata lookup
- Deezer metadata lookup (with BPM)
- Last.fm metadata lookup (with user tags)
- Artwork URL resolution
"""

import copy
import threading
import urllib.parse
from collections import OrderedDict

import requests

# Global Last.fm API key (can be set by config)
_LASTFM_API_KEY = None
_REQUEST_TIMEOUT_SECONDS = 5

_HTTP_SESSION = None
_HTTP_SESSION_LOCK = threading.Lock()

_EXTERNAL_METADATA_CACHE = OrderedDict()
_EXTERNAL_METADATA_CACHE_LOCK = threading.Lock()
_EXTERNAL_METADATA_CACHE_MAX_ENTRIES = 2000


def _build_http_session():
    """Create a requests session with pooled connections and light retry."""
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    adapter = None
    try:
        from urllib3.util.retry import Retry
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    except Exception:
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)

    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _get_http_session():
    """Get or initialize the shared HTTP session."""
    global _HTTP_SESSION
    if _HTTP_SESSION is not None:
        return _HTTP_SESSION
    with _HTTP_SESSION_LOCK:
        if _HTTP_SESSION is None:
            _HTTP_SESSION = _build_http_session()
    return _HTTP_SESSION


def _http_get_json(url, timeout=_REQUEST_TIMEOUT_SECONDS):
    """GET a JSON endpoint and return parsed payload, or None on failure."""
    try:
        response = _get_http_session().get(url, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def _normalize_metadata_token(value):
    text = str(value or "").strip().lower()
    return " ".join(text.split())


def _metadata_cache_key(artist, title):
    norm_artist = _normalize_metadata_token(artist)
    norm_title = _normalize_metadata_token(title)
    if not norm_artist or not norm_title:
        return ""
    return f"{norm_artist}::{norm_title}"


def _cache_get_external_metadata(cache_key):
    if not cache_key:
        return None
    with _EXTERNAL_METADATA_CACHE_LOCK:
        cached = _EXTERNAL_METADATA_CACHE.get(cache_key)
        if cached is None:
            return None
        _EXTERNAL_METADATA_CACHE.move_to_end(cache_key)
        return copy.deepcopy(cached)


def _cache_put_external_metadata(cache_key, payload):
    if not cache_key:
        return
    with _EXTERNAL_METADATA_CACHE_LOCK:
        _EXTERNAL_METADATA_CACHE[cache_key] = copy.deepcopy(payload)
        _EXTERNAL_METADATA_CACHE.move_to_end(cache_key)
        while len(_EXTERNAL_METADATA_CACHE) > _EXTERNAL_METADATA_CACHE_MAX_ENTRIES:
            _EXTERNAL_METADATA_CACHE.popitem(last=False)


def set_lastfm_key(key):
    """Set the Last.fm API key"""
    global _LASTFM_API_KEY
    _LASTFM_API_KEY = key



# =============================================================================
# ARTWORK HELPERS
# =============================================================================

def find_art_in_json(data):
    """Extract artwork URL from ACRCloud response"""
    album = data.get("album", {})
    if isinstance(album, dict) and album.get("cover"):
        return album["cover"].get("large") or album["cover"].get("medium")
    return None


def get_backup_art(artist, title):
    """Get artwork from iTunes as backup"""
    try:
        query = f"{artist} {title}".replace(" ", "+")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
        response = _http_get_json(url)
        if not isinstance(response, dict):
            return None
        if response.get("resultCount", 0) > 0:
            return response["results"][0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
    except (KeyError, IndexError, TypeError):
        pass
    return None


# =============================================================================
# iTunes/Apple Music
# =============================================================================

def get_itunes_metadata(artist, title):
    """Get metadata from iTunes/Apple Music Search API"""
    try:
        query = f"{artist} {title}".replace(" ", "+")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=3"
        response = _http_get_json(url)
        if not isinstance(response, dict):
            return None
        
        if response.get("resultCount", 0) > 0:
            # Try to find best match
            for result in response.get("results", []):
                result_artist = str(result.get("artistName", "")).lower()
                
                if artist.lower() in result_artist or result_artist in artist.lower():
                    release_date = result.get("releaseDate", "")
                    year = str(release_date)[:4] if release_date else None
                    return {
                        'artist': str(result.get("artistName", "")),
                        'title': str(result.get("trackName", "")),
                        'album': str(result.get("collectionName", "")),
                        'genre': str(result.get("primaryGenreName", "")) if result.get("primaryGenreName") else None,
                        'year': year,
                        'source': 'iTunes'
                    }
            
            # Fall back to first result
            result = response["results"][0]
            release_date = result.get("releaseDate", "")
            year = str(release_date)[:4] if release_date else None
            return {
                'artist': str(result.get("artistName", "")),
                'title': str(result.get("trackName", "")),
                'album': str(result.get("collectionName", "")),
                'genre': str(result.get("primaryGenreName", "")) if result.get("primaryGenreName") else None,
                'year': year,
                'source': 'iTunes'
            }
    except (KeyError, IndexError, TypeError):
        pass
    return None


# =============================================================================
# Deezer
# =============================================================================

def get_deezer_metadata(artist, title):
    """Get metadata from Deezer API (free, no auth required)"""
    try:
        query = f"{artist} {title}".replace(" ", "+")
        url = f"https://api.deezer.com/search?q={query}&limit=3"
        response = _http_get_json(url)
        if not isinstance(response, dict):
            return None
        
        if response.get("data"):
            for result in response.get("data", []):
                result_artist = str(result.get("artist", {}).get("name", "")).lower()
                
                if artist.lower() in result_artist or result_artist in artist.lower():
                    album_id = result.get("album", {}).get("id")
                    track_id = result.get("id")
                    genre = None
                    year = None
                    bpm = None
                    
                    # Fetch full track details for BPM
                    if track_id:
                        try:
                            track_url = f"https://api.deezer.com/track/{track_id}"
                            track_data = _http_get_json(track_url)
                            if not isinstance(track_data, dict):
                                track_data = {}
                            if track_data.get("bpm") and track_data.get("bpm") > 0:
                                bpm = int(track_data["bpm"])
                        except (ValueError, TypeError, KeyError):
                            pass
                    
                    # Fetch album details for genre and year
                    if album_id:
                        try:
                            album_url = f"https://api.deezer.com/album/{album_id}"
                            album_data = _http_get_json(album_url)
                            if not isinstance(album_data, dict):
                                album_data = {}
                            if album_data.get("genres", {}).get("data"):
                                genre = str(album_data["genres"]["data"][0].get("name", ""))
                            if album_data.get("release_date"):
                                year = str(album_data["release_date"])[:4]
                        except (KeyError, IndexError, TypeError):
                            pass
                    
                    return {
                        'artist': str(result.get("artist", {}).get("name", "")),
                        'title': str(result.get("title", "")),
                        'album': str(result.get("album", {}).get("title", "")),
                        'genre': genre if genre else None,
                        'year': year,
                        'bpm': bpm,
                        'source': 'Deezer'
                    }
            
            # Fall back to first result
            result = response["data"][0]
            album_id = result.get("album", {}).get("id")
            track_id = result.get("id")
            genre = None
            year = None
            bpm = None
            
            if track_id:
                try:
                    track_url = f"https://api.deezer.com/track/{track_id}"
                    track_data = _http_get_json(track_url)
                    if not isinstance(track_data, dict):
                        track_data = {}
                    if track_data.get("bpm") and track_data.get("bpm") > 0:
                        bpm = int(track_data["bpm"])
                except (ValueError, TypeError, KeyError):
                    pass

            if album_id:
                try:
                    album_url = f"https://api.deezer.com/album/{album_id}"
                    album_data = _http_get_json(album_url)
                    if not isinstance(album_data, dict):
                        album_data = {}
                    if album_data.get("genres", {}).get("data"):
                        genre = str(album_data["genres"]["data"][0].get("name", ""))
                    if album_data.get("release_date"):
                        year = str(album_data["release_date"])[:4]
                except (KeyError, IndexError, TypeError):
                    pass

            return {
                'artist': str(result.get("artist", {}).get("name", "")),
                'title': str(result.get("title", "")),
                'album': str(result.get("album", {}).get("title", "")),
                'genre': genre if genre else None,
                'year': year,
                'bpm': bpm,
                'source': 'Deezer'
            }
    except (KeyError, IndexError, TypeError):
        pass
    return None


# =============================================================================
# Last.fm
# =============================================================================

def get_lastfm_metadata(artist, title):
    """Get metadata from Last.fm API (optional, requires free API key)
    
    Last.fm provides:
    - User-generated tags (great for EDM subgenres)
    - Playcount/listeners
    - Corrected artist names
    """
    global _LASTFM_API_KEY
    
    if not _LASTFM_API_KEY:
        return None
    
    try:
        artist_encoded = urllib.parse.quote(artist)
        title_encoded = urllib.parse.quote(title)
        
        url = f"https://ws.audioscrobbler.com/2.0/?method=track.getInfo&artist={artist_encoded}&track={title_encoded}&api_key={_LASTFM_API_KEY}&format=json"
        response = _http_get_json(url)
        if not isinstance(response, dict):
            return None
        

        # Last.fm returns {'error':..,'message':..} for invalid keys / rate limits

        if isinstance(response, dict) and response.get('error'):

            return None

        track = response.get('track')
        if not track:
            return None
        
        # Extract top tags
        tags = []
        top_tags = track.get('toptags', {}).get('tag', [])
        if isinstance(top_tags, list):
            tags = [tag.get('name', '') for tag in top_tags[:5] if tag.get('name')]
        elif isinstance(top_tags, dict) and top_tags.get('name'):
            tags = [top_tags.get('name')]
        
        playcount = 0
        try:
            playcount = int(track.get('playcount', 0))
        except (ValueError, TypeError):
            pass
        
        listeners = 0
        try:
            listeners = int(track.get('listeners', 0))
        except (ValueError, TypeError):
            pass
        
        corrected_artist = track.get('artist', {}).get('name', '')
        album = track.get('album', {}).get('title', '') if track.get('album') else ''
        
        return {
            'artist': corrected_artist,
            'title': track.get('name', ''),
            'album': album,
            'tags': tags,
            'playcount': playcount,
            'listeners': listeners,
            'source': 'Last.fm'
        }
    except (KeyError, IndexError, TypeError):
        pass
    return None


# =============================================================================
# COMBINED LOOKUP
# =============================================================================

def get_all_external_metadata(artist, title, runtime_config=None):
    """Get metadata from multiple external sources concurrently.

    iTunes, Deezer, and Last.fm are independent API calls so we run
    them in parallel to cut wall-clock time from ~3× a single request
    down to ~1×.

    Pass *runtime_config* (the active config dict) to honour the
    ``lastfm_genre_enrichment_enabled`` toggle.  When omitted the flag
    defaults to True so existing callers are unaffected.
    """
    cache_key = _metadata_cache_key(artist, title)
    cached = _cache_get_external_metadata(cache_key)
    if cached is not None:
        return cached

    from concurrent.futures import ThreadPoolExecutor, as_completed

    _lastfm_enabled = True
    if isinstance(runtime_config, dict):
        _lastfm_enabled = bool(runtime_config.get('lastfm_genre_enrichment_enabled', True))

    fetchers = {
        'itunes': lambda: get_itunes_metadata(artist, title),
        'deezer': lambda: get_deezer_metadata(artist, title),
    }
    if _lastfm_enabled:
        fetchers['lastfm'] = lambda: get_lastfm_metadata(artist, title)

    payload = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                payload[name] = future.result()
            except Exception:
                payload[name] = None

    _cache_put_external_metadata(cache_key, payload)
    return payload
