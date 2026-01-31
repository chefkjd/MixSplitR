import os
import sys
import glob
import json
import time
import shutil
import threading
import requests
import itertools
import gc
import base64
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

# Current version
CURRENT_VERSION = "6.9"
GITHUB_REPO = "chefkjd/MixSplitR"

def check_for_updates():
    """Check GitHub for newer version
    Returns:
        dict: Update info if newer version available
        False: If current version is up to date
        None: If check failed (network error, etc.)
    """
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            latest_version = data.get('tag_name', '').lstrip('v')
            
            if not latest_version:
                return None
            
            # Same version = up to date
            if latest_version == CURRENT_VERSION:
                return False
            
            # Compare versions numerically
            try:
                current_parts = [int(x) for x in CURRENT_VERSION.split('.')]
                latest_parts = [int(x) for x in latest_version.split('.')]
                
                # Pad with zeros if needed
                while len(current_parts) < len(latest_parts):
                    current_parts.append(0)
                while len(latest_parts) < len(current_parts):
                    latest_parts.append(0)
                
                if latest_parts > current_parts:
                    return {
                        'latest': latest_version,
                        'current': CURRENT_VERSION,
                        'url': data.get('html_url', f'https://github.com/{GITHUB_REPO}/releases')
                    }
                else:
                    # Current version is same or newer
                    return False
            except:
                return None
        return None
    except:
        # Network error, timeout, etc.
        return None

# Try to import MusicBrainz/AcoustID for fallback
try:
    import acoustid
    import musicbrainzngs
    ACOUSTID_AVAILABLE = True
    # Configure MusicBrainz
    musicbrainzngs.set_useragent("MixSplitR", CURRENT_VERSION, f"https://github.com/{GITHUB_REPO}")
except ImportError:
    ACOUSTID_AVAILABLE = False
    print("Note: acoustid/musicbrainzngs not found - MusicBrainz fallback disabled")
    print("      Install with: pip install pyacoustid musicbrainzngs")

# Try to import psutil, fall back to manual processing if not available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("Note: psutil not found - will process files one at a time for safety")

# Global rate limiter for ACRCloud API calls
class RateLimiter:
    """Thread-safe rate limiter for API calls"""
    def __init__(self, min_interval=1.2):
        self.min_interval = min_interval
        self.last_call = 0
        self.lock = threading.Lock()
    
    def wait(self):
        """Wait if necessary to respect rate limit"""
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_call
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)
            self.last_call = time.time()

# --- 1. THE ENGINE HANDSHAKE (MAC-SAFE) ---
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

ffmpeg_path = resource_path("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
ffprobe_path = resource_path("ffprobe.exe" if sys.platform == "win32" else "ffprobe")

# Set environment variables for pydub BEFORE importing it
os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")

if sys.platform != "win32":
    import subprocess
    if os.path.exists(ffmpeg_path):
        subprocess.run(["chmod", "+x", ffmpeg_path])
    if os.path.exists(ffprobe_path):
        subprocess.run(["chmod", "+x", ffprobe_path])
    
    # Fallback to system ffmpeg/ffprobe if bundled ones don't exist
    if not os.path.exists(ffmpeg_path):
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            ffmpeg_path = system_ffmpeg
    if not os.path.exists(ffprobe_path):
        system_ffprobe = shutil.which("ffprobe")
        if system_ffprobe:
            ffprobe_path = system_ffprobe

from pydub import AudioSegment
AudioSegment.converter = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path

# --- 2. CONFIGURATION ---
def close_terminal():
    """Close the terminal window on macOS, or just exit on other platforms"""
    input("\nPress Enter to close...")
    if sys.platform == 'darwin':  # macOS
        # Close the Terminal window
        os.system('osascript -e "tell application \\"Terminal\\" to close first window" & exit')
    # On Windows, the window closes automatically when the script exits

def get_config():
    base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_path, "config.json")
    if os.path.exists(config_path):
        with open(config_path, 'r') as f: return json.load(f)
    else:
        print("\n--- ACRCloud API Setup ---")
        conf = {'host': input("Enter your ACR Host, if you aren't sure what this is, check the ReadMe.txt!: ").strip(), 'access_key': input("Now your Access Key: ").strip(), 'access_secret': input("Finally, your Secret Key: ").strip(), 'timeout': 10}
        with open(config_path, 'w') as f: json.dump(conf, f, indent=4)
        return conf

# --- 3. ART FINDER & ITUNES BACKUP ---
def find_art_in_json(data):
    album = data.get("album", {})
    if isinstance(album, dict) and album.get("cover"):
        return album["cover"].get("large") or album["cover"].get("medium")
    return None

def get_backup_art(artist, title):
    try:
        query = f"{artist} {title}".replace(" ", "+")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
        response = requests.get(url, timeout=5).json()
        if response.get("resultCount", 0) > 0:
            return response["results"][0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
    except:
        pass
    return None

# --- 3B. MUSICBRAINZ/ACOUSTID FALLBACK ---
def identify_with_acoustid(audio_chunk):
    """Fallback identification using AcoustID/MusicBrainz"""
    if not ACOUSTID_AVAILABLE:
        return None
    
    try:
        # Export chunk to temporary file for fingerprinting
        temp_file = f"temp_acoustid_{threading.current_thread().ident}.wav"
        audio_chunk.export(temp_file, format="wav")
        
        # Use AcoustID API key (free, no registration needed for basic use)
        # For production, get your own key at: https://acoustid.org/api-key
        results = acoustid.match('8XaBELgH', temp_file)
        
        # Clean up temp file
        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        # Parse results
        for score, recording_id, title, artist in results:
            if score > 0.5:  # Confidence threshold
                return {
                    'artist': artist,
                    'title': title,
                    'recording_id': recording_id,
                    'score': score,
                    'source': 'acoustid'
                }
        
        return None
    except Exception as e:
        # Clean up on error
        temp_file = f"temp_acoustid_{threading.current_thread().ident}.wav"
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None

def get_enhanced_metadata(artist, title, recording_id=None):
    """Get enhanced metadata from MusicBrainz"""
    if not ACOUSTID_AVAILABLE:
        return {}
    
    try:
        enhanced = {}
        
        # If we have a recording ID, use it directly
        if recording_id:
            try:
                recording = musicbrainzngs.get_recording_by_id(
                    recording_id, 
                    includes=['artists', 'releases', 'tags', 'isrcs']
                )
                rec = recording.get('recording', {})
                
                # Extract enhanced metadata
                if 'tag-list' in rec:
                    enhanced['genres'] = [tag['name'] for tag in rec['tag-list'][:3]]
                
                if 'isrc-list' in rec:
                    enhanced['isrc'] = rec['isrc-list'][0] if rec['isrc-list'] else None
                
                if 'release-list' in rec:
                    release = rec['release-list'][0]
                    enhanced['release_date'] = release.get('date', '')
                    if 'label-info-list' in release:
                        label_info = release['label-info-list']
                        if label_info and 'label' in label_info[0]:
                            enhanced['label'] = label_info[0]['label'].get('name', '')
                
                return enhanced
            except:
                pass
        
        # Otherwise, search by artist and title
        try:
            results = musicbrainzngs.search_recordings(
                artist=artist,
                recording=title,
                limit=1
            )
            
            if results.get('recording-list'):
                rec = results['recording-list'][0]
                
                # Get genres
                if 'tag-list' in rec:
                    enhanced['genres'] = [tag['name'] for tag in rec['tag-list'][:3]]
                
                # Get release info
                if 'release-list' in rec:
                    release = rec['release-list'][0]
                    enhanced['release_date'] = release.get('date', '')
                
                return enhanced
        except:
            pass
        
        return enhanced
    except:
        return {}

def batch_download_artwork(artwork_urls):
    """Download multiple artworks in parallel - returns dict of url -> image_data"""
    artwork_cache = {}
    
    def download_single(url):
        if not url or "{w}x{h}" in url:
            url = url.replace("{w}x{h}", "600x600") if url else None
        if not url:
            return None, None
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return url, response.content
        except:
            pass
        return url, None
    
    # Download up to 5 artworks in parallel
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_single, url): url for url in artwork_urls if url}
        for future in as_completed(futures):
            url, img_data = future.result()
            if img_data:
                artwork_cache[url] = img_data
    
    return artwork_cache

def embed_and_sort_flac(file_path, artist, title, album, cover_url, base_output_folder, artwork_cache=None, enhanced_metadata=None):
    from mutagen.flac import FLAC, Picture
    try:
        audio = FLAC(file_path)
        audio["artist"], audio["title"], audio["album"] = artist, title, album
        
        # Add enhanced metadata if available
        if enhanced_metadata:
            if 'release_date' in enhanced_metadata and enhanced_metadata['release_date']:
                # Extract just the year if it's a full date
                date = enhanced_metadata['release_date']
                year = date[:4] if len(date) >= 4 else date
                audio["date"] = year
            
            if 'genres' in enhanced_metadata and enhanced_metadata['genres']:
                audio["genre"] = ", ".join(enhanced_metadata['genres'])
            
            if 'label' in enhanced_metadata and enhanced_metadata['label']:
                audio["label"] = enhanced_metadata['label']
            
            if 'isrc' in enhanced_metadata and enhanced_metadata['isrc']:
                audio["isrc"] = enhanced_metadata['isrc']
        
        img_data = None
        if cover_url:
            if "{w}x{h}" in cover_url: 
                cover_url = cover_url.replace("{w}x{h}", "600x600")
            
            # Try to get from cache first
            if artwork_cache and cover_url in artwork_cache:
                img_data = artwork_cache[cover_url]
            else:
                # Download if not in cache
                try:
                    img_res = requests.get(cover_url, timeout=10)
                    if img_res.status_code == 200:
                        img_data = img_res.content
                except: 
                    pass
            
            # Embed artwork if we have it
            if img_data:
                pic = Picture()
                pic.data, pic.type, pic.mime = img_data, 3, u"image/jpeg"
                audio.add_picture(pic)
        
        audio.save()
        
        # --- FINDER COMPATIBILITY: SIDE CAR ART ---
        safe_artist = artist.translate(str.maketrans('', '', '<>:"/\\|?*'))
        dest_dir = os.path.join(base_output_folder, safe_artist)
        os.makedirs(dest_dir, exist_ok=True)
        
        if img_data:
            art_path = os.path.join(dest_dir, "folder.jpg")
            if not os.path.exists(art_path):
                with open(art_path, "wb") as f:
                    f.write(img_data)

        new_name = f"{artist} - {title}.flac".translate(str.maketrans('', '', '<>:"/\\|?*'))
        shutil.move(file_path, os.path.join(dest_dir, new_name))
    except Exception as e: 
        print(f"   [!] Tag Error: {e}")

# --- 4. RAM MANAGEMENT ---
def scan_existing_library(output_folder):
    """Scan existing library to avoid re-processing tracks - optimized version"""
    existing_tracks = set()
    
    # Early return if library doesn't exist
    if not os.path.exists(output_folder):
        return existing_tracks
    
    # Use os.scandir() for faster directory traversal (vs os.listdir)
    try:
        with os.scandir(output_folder) as entries:
            for entry in entries:
                # Only process directories (artist folders)
                if entry.is_dir():
                    artist_path = entry.path
                    # Scan files in artist folder
                    try:
                        with os.scandir(artist_path) as files:
                            for file_entry in files:
                                # Only check .flac files, skip unidentified ones
                                if (file_entry.is_file() and 
                                    file_entry.name.endswith('.flac') and 
                                    not file_entry.name.startswith('File')):
                                    # Only store filename if it has proper format
                                    if ' - ' in file_entry.name:
                                        existing_tracks.add(file_entry.name)
                    except (PermissionError, OSError):
                        continue  # Skip folders we can't read
    except (PermissionError, OSError):
        pass  # If we can't read output folder, return empty set
    
    return existing_tracks

def get_available_ram_gb():
    """Get available RAM in GB with safety margin"""
    if not PSUTIL_AVAILABLE:
        return None  # Will trigger fallback to one-file-at-a-time processing
    
    mem = psutil.virtual_memory()
    # Use 70% of available RAM to leave room for OS and other processes
    available_gb = (mem.available / (1024**3)) * 0.7
    return max(available_gb, 2)  # Minimum 2GB

def estimate_file_ram_gb(file_path):
    """Estimate how much RAM a file will use when loaded (uncompressed PCM)"""
    try:
        file_size_mb = os.path.getsize(file_path) / (1024**2)
        # Rough estimates based on compression:
        # WAV/FLAC (lossless): ~1.2x file size
        # MP3/M4A/OGG (lossy): ~10x file size (heavily compressed to uncompressed)
        # AAC/WMA: ~8x file size
        # AIFF: ~1.1x file size
        
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext in ['.wav', '.flac']:
            multiplier = 1.2
        elif ext in ['.mp3', '.ogg', '.opus']:
            multiplier = 10
        elif ext in ['.m4a', '.aac', '.wma']:
            multiplier = 8
        elif ext == '.aiff':
            multiplier = 1.1
        else:
            multiplier = 10  # Conservative default
        
        estimated_gb = (file_size_mb * multiplier) / 1024
        return estimated_gb
    except:
        return 0.5  # Default conservative estimate

def create_file_batches(audio_files, available_ram_gb):
    """Group files into batches that fit in available RAM"""
    # Fallback: process one file at a time if psutil not available
    if available_ram_gb is None:
        print(f"\n⚠️  RAM detection unavailable - processing files individually for safety")
        print(f"🔍 Analyzing {len(audio_files)} file(s)...\n")
        return [[f] for f in audio_files]  # Each file in its own batch
    
    batches = []
    current_batch = []
    current_batch_size = 0
    
    print(f"\n📊 Available RAM for processing: {available_ram_gb:.1f} GB")
    print(f"🔍 Analyzing {len(audio_files)} file(s) for optimal batching...")
    
    # Pre-calculate all file sizes (faster than doing it in the loop)
    file_estimates = []
    for file_path in audio_files:
        file_ram = estimate_file_ram_gb(file_path)
        file_estimates.append((file_path, file_ram))
    
    print(f"✓ Analysis complete!\n")
    
    for file_path, file_ram in file_estimates:
        file_name = os.path.basename(file_path)
        
        # If single file exceeds available RAM, process it alone (risky but necessary)
        if file_ram > available_ram_gb:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_batch_size = 0
            batches.append([file_path])
            print(f"⚠️  {file_name}: {file_ram:.2f} GB (LARGE - will process alone)")
            continue
        
        # If adding this file would exceed RAM, start new batch
        if current_batch_size + file_ram > available_ram_gb:
            batches.append(current_batch)
            current_batch = [file_path]
            current_batch_size = file_ram
        else:
            current_batch.append(file_path)
            current_batch_size += file_ram
    
    # Add remaining files
    if current_batch:
        batches.append(current_batch)
    
    return batches

# --- 5. MAIN ENGINE ---
# --- 5. CACHE MANAGEMENT ---
def save_preview_cache(cache_data, cache_path="mixsplittr_cache.json"):
    """Save processing results to cache for preview/apply workflow"""
    print(f"\n💾 Saving preview cache to {cache_path}...")
    
    # Estimate cache size
    track_count = len(cache_data.get('tracks', []))
    artwork_count = len(cache_data.get('artwork_cache', {}))
    print(f"   📊 Tracks: {track_count}, Artworks: {artwork_count}")
    
    try:
        with open(cache_path, 'w') as f:
            # Use compact JSON (no indent) to save space and memory
            json.dump(cache_data, f)
        
        # Clear macOS extended attributes that can block file access
        if platform.system() == 'Darwin':  # macOS
            try:
                subprocess.run(['xattr', '-c', cache_path], 
                             check=False, capture_output=True)
            except:
                pass  # Don't fail if xattr cleanup doesn't work
        
        # Show file size
        file_size_mb = os.path.getsize(cache_path) / (1024 * 1024)
        print(f"✅ Cache saved! Size: {file_size_mb:.1f} MB")
        return True
    except Exception as e:
        print(f"❌ Error saving cache: {e}")
        import traceback
        traceback.print_exc()
        return False

def load_preview_cache(cache_path="mixsplitr_cache.json"):
    """Load cached processing results"""
    if not os.path.exists(cache_path):
        print(f"❌ Cache file not found: {cache_path}")
        print(f"   Run with --preview first to generate cache")
        return None
    
    try:
        with open(cache_path, 'r') as f:
            cache_data = json.load(f)
        print(f"✅ Loaded cache with {len(cache_data.get('tracks', []))} tracks")
        return cache_data
    except Exception as e:
        print(f"❌ Error loading cache: {e}")
        return None

def display_preview_table(cache_data):
    """Display a formatted preview of what will be processed"""
    tracks = cache_data.get('tracks', [])
    
    print(f"\n{'='*80}")
    print(f"{'='*80}")
    print(f"                    PREVIEW MODE - No files created yet")
    print(f"{'='*80}")
    print(f"{'='*80}\n")
    
    # Summary stats
    identified = [t for t in tracks if t['status'] == 'identified']
    unidentified = [t for t in tracks if t['status'] == 'unidentified']
    skipped = [t for t in tracks if t['status'] == 'skipped']
    
    print(f"📊 Found {len(tracks)} total tracks:\n")
    
    # Show first 20 tracks as samples
    print(f"{'#':<5} {'Status':<12} {'Artist':<25} {'Title':<30}")
    print(f"{'-'*80}")
    
    for i, track in enumerate(tracks[:20], 1):
        status_icon = {
            'identified': '✅',
            'unidentified': '❓',
            'skipped': '⏭️'
        }.get(track['status'], '?')
        
        artist = track.get('artist', 'Unknown')[:24]
        title = track.get('title', 'Unknown')[:29]
        status = f"{status_icon} {track['status'].title()}"
        
        print(f"{i:<5} {status:<12} {artist:<25} {title:<30}")
    
    if len(tracks) > 20:
        print(f"... and {len(tracks) - 20} more tracks")
    
    print(f"\n{'='*80}")
    print(f"📈 SUMMARY:")
    print(f"{'─'*80}")
    print(f"  ✅ Will create: {len(identified)} new tracks")
    print(f"  ❓ Unidentified: {len(unidentified)} tracks")
    print(f"  ⏭️  Will skip: {len(skipped)} existing tracks")
    print(f"{'─'*80}")
    
    # Show identification source breakdown
    acrcloud_count = len([t for t in identified if t.get('identification_source') == 'acrcloud'])
    acoustid_count = len([t for t in identified if t.get('identification_source') == 'acoustid'])
    
    if acrcloud_count > 0 or acoustid_count > 0:
        print(f"  🔍 Identification sources:")
        if acrcloud_count > 0:
            print(f"     ACRCloud: {acrcloud_count} tracks")
        if acoustid_count > 0:
            print(f"     MusicBrainz/AcoustID: {acoustid_count} tracks (fallback)")
        print(f"{'─'*80}")
    
    # Show enhanced metadata stats
    with_enhanced = len([t for t in identified if t.get('enhanced_metadata', {}).get('genres')])
    if with_enhanced > 0:
        print(f"  🎵 Enhanced metadata: {with_enhanced} tracks with genres, dates, labels")
        print(f"{'─'*80}")
    
    print(f"  Total API calls used: {len([t for t in tracks if t['status'] != 'skipped'])}")
    print(f"  Cache file: mixsplitr_cache.json")
    print(f"{'='*80}\n")
    
    print(f"✨ Next steps:")
    print(f"  • Run with --apply to create these files")
    print(f"  • Run with --cancel to discard cache")
    print(f"  • Edit mixsplitr_cache.json to fix any misidentified tracks\n")

# --- 6. MAIN ENGINE ---
def process_single_track(chunk_data, i, recognizer, rate_limiter, existing_tracks, output_folder, existing_tracks_lock, preview_mode=False):
    """Process a single track - designed for parallel execution"""
    chunk = chunk_data['chunk']
    file_num = chunk_data['file_num']
    
    # Skip very short chunks (optimization #1)
    if len(chunk) < 10000:
        return {'status': 'skipped', 'reason': 'too_short', 'index': i, 'file_num': file_num}
    
    # Extract sample from middle (reuse buffer - optimization #2)
    sample = chunk[len(chunk)//2 : len(chunk)//2 + 12000]
    
    # Skip temp file for very short samples (optimization #1)
    temp_name = f"temp_id_{file_num}_{i}_{threading.current_thread().ident}.wav"
    sample.export(temp_name, format="wav")
    
    # Rate-limited API call to ACRCloud
    rate_limiter.wait()
    res = json.loads(recognizer.recognize_by_file(temp_name, 0))
    
    # Clean up temp file immediately
    if os.path.exists(temp_name):
        os.remove(temp_name)
    
    # Try ACRCloud first
    artist = None
    title = None
    album = None
    art_url = None
    enhanced_metadata = {}
    identification_source = None
    recording_id = None
    
    if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
        # ACRCloud success
        music = res["metadata"]["music"][0]
        artist = music["artists"][0]["name"]
        title = music["title"]
        album = music.get("album", {}).get("name", "Unknown Album")
        identification_source = "acrcloud"
        
        # Get artwork URL
        art_url = find_art_in_json(music)
        if not art_url:
            art_url = get_backup_art(artist, title)
        
        # Get enhanced metadata from MusicBrainz
        enhanced_metadata = get_enhanced_metadata(artist, title)
        
    else:
        # ACRCloud failed - try AcoustID fallback
        acoustid_result = identify_with_acoustid(chunk)
        
        if acoustid_result:
            artist = acoustid_result['artist']
            title = acoustid_result['title']
            album = "Unknown Album"  # AcoustID doesn't always provide album
            recording_id = acoustid_result.get('recording_id')
            identification_source = "acoustid"
            
            # Get artwork from iTunes
            art_url = get_backup_art(artist, title)
            
            # Get enhanced metadata from MusicBrainz (including album info)
            enhanced_metadata = get_enhanced_metadata(artist, title, recording_id)
            
            # Try to get album from enhanced metadata
            if enhanced_metadata and 'album' in enhanced_metadata:
                album = enhanced_metadata['album']
    
    # If we got an identification (from either source)
    if artist and title:
        # Thread-safe check if track exists
        expected_filename = f"{artist} - {title}.flac".translate(str.maketrans('', '', '<>:"/\\|?*'))
        with existing_tracks_lock:
            if expected_filename in existing_tracks:
                return {
                    'status': 'skipped',
                    'reason': 'already_exists',
                    'index': i,
                    'file_num': file_num,
                    'artist': artist,
                    'title': title,
                    'album': album,
                    'original_file': chunk_data.get('original_file'),
                    'chunk_index': chunk_data.get('split_index', 0),  # Use per-file index, not global
                    'temp_chunk_path': chunk_data.get('temp_chunk_path')  # Include temp path
                }
        
        result = {
            'status': 'identified',
            'index': i,
            'file_num': file_num,
            'artist': artist,
            'title': title,
            'album': album,
            'art_url': art_url,
            'expected_filename': expected_filename,
            'identification_source': identification_source,
            'enhanced_metadata': enhanced_metadata,
            'original_file': chunk_data.get('original_file'),  # Store original file path
            'chunk_index': chunk_data.get('split_index', 0),  # Use per-file index, not global
            'temp_chunk_path': chunk_data.get('temp_chunk_path')  # Include temp path
        }
        
        # In preview mode, don't store audio - just metadata
        if not preview_mode:
            # In normal mode, export immediately
            temp_flac = os.path.join(output_folder, f"temp_{file_num}_{i}_{threading.current_thread().ident}.flac")
            chunk.export(temp_flac, format="flac")
            result['temp_flac'] = temp_flac
        
        # Add to existing tracks (thread-safe)
        with existing_tracks_lock:
            existing_tracks.add(expected_filename)
        
        return result
    else:
        # Unidentified track
        unidentified_filename = f"File{file_num}_Track_{i+1}_Unidentified.flac"
        unidentified_path = os.path.join(output_folder, unidentified_filename)
        
        # Check if already exists
        if os.path.exists(unidentified_path):
            return {
                'status': 'skipped',
                'reason': 'already_exists',
                'index': i,
                'file_num': file_num,
                'unidentified_filename': unidentified_filename,
                'original_file': chunk_data.get('original_file'),
                'chunk_index': chunk_data.get('split_index', 0),  # Use per-file index
                'temp_chunk_path': chunk_data.get('temp_chunk_path')  # Include temp path
            }
        
        result = {
            'status': 'unidentified',
            'index': i,
            'file_num': file_num,
            'unidentified_filename': unidentified_filename,
            'unidentified_path': unidentified_path,
            'original_file': chunk_data.get('original_file'),
            'chunk_index': chunk_data.get('split_index', 0),  # Use per-file index
            'temp_chunk_path': chunk_data.get('temp_chunk_path')  # Include temp path
        }
        
        if not preview_mode:
            chunk.export(unidentified_path, format="flac")
        
        return result

def apply_from_cache(cache_path="mixsplitr_cache.json", temp_audio_folder=None):
    """Apply cached processing results - use temp files if available, otherwise re-split"""
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    
    print("\n========================================")
    print("          APPLY MODE - Creating Files")
    print("========================================\n")
    
    # Load cache
    cache_data = load_preview_cache(cache_path)
    if not cache_data:
        return
    
    tracks = cache_data.get('tracks', [])
    artwork_cache_b64 = cache_data.get('artwork_cache', {})
    output_folder = cache_data.get('output_folder', 'My_Music_Library')
    
    # Determine temp folder path if not provided
    if temp_audio_folder is None:
        base_dir = os.path.dirname(cache_path)
        temp_audio_folder = os.path.join(base_dir, "mixsplitr_temp")
    
    # Check if we have temp files available
    has_temp_files = os.path.exists(temp_audio_folder) and len(os.listdir(temp_audio_folder)) > 0
    
    if has_temp_files:
        print(f"✅ Found cached audio chunks in {temp_audio_folder}")
        print(f"   → Using pre-split audio (fast mode)\n")
    else:
        print(f"⚠️  No cached audio found - will re-split from source files\n")
    
    # Decode artwork cache from base64
    print(f"🎨 Decoding {len(artwork_cache_b64)} cached artworks...")
    artwork_cache = {}
    for url, b64_data in artwork_cache_b64.items():
        try:
            artwork_cache[url] = base64.b64decode(b64_data)
        except:
            pass
    
    # Count totals for summary
    total_to_create = len([t for t in tracks if t['status'] in ['identified', 'unidentified']])
    total_to_skip = len([t for t in tracks if t['status'] == 'skipped'])
    
    print(f"\n📁 Processing {total_to_create} tracks to create, {total_to_skip} to skip\n")
    print(f"{'-'*60}\n")
    
    identified_count = 0
    unidentified_count = 0
    skipped_count = 0
    
    # Fast mode: process directly from temp files
    if has_temp_files:
        for track_idx, track in enumerate(tracks, 1):
            if track['status'] == 'skipped':
                skipped_count += 1
                continue
            
            temp_path = track.get('temp_chunk_path')
            
            # Check if temp file exists
            if not temp_path or not os.path.exists(temp_path):
                print(f"⚠️  Track {track_idx}: temp file not found, skipping")
                continue
            
            # Progress update
            if track['status'] == 'identified':
                artist = track.get('artist', 'Unknown')
                title = track.get('title', 'Unknown')
                print(f"  Track {track_idx}/{len(tracks)}: {artist[:25]} - {title[:30]}")
            else:
                print(f"  Track {track_idx}/{len(tracks)}: Unidentified track")
            
            # Load the chunk from temp file
            chunk = AudioSegment.from_file(temp_path)
            
            if track['status'] == 'identified':
                # Create FLAC with metadata
                temp_flac = os.path.join(output_folder, f"temp_apply_{track['file_num']}_{track['index']}.flac")
                chunk.export(temp_flac, format="flac")
                
                # Embed metadata and organize
                embed_and_sort_flac(
                    temp_flac,
                    track['artist'],
                    track['title'],
                    track['album'],
                    track.get('art_url'),
                    output_folder,
                    artwork_cache,
                    track.get('enhanced_metadata', {})
                )
                identified_count += 1
                
            elif track['status'] == 'unidentified':
                # Export unidentified track
                unidentified_path = track.get('unidentified_path')
                if unidentified_path:
                    os.makedirs(os.path.dirname(unidentified_path) if os.path.dirname(unidentified_path) else output_folder, exist_ok=True)
                    chunk.export(unidentified_path, format="flac")
                unidentified_count += 1
            
            # Clean up chunk from memory
            del chunk
        
        print(f"\n✓ All tracks processed")
    
    else:
        # Slow mode: re-split from original files (fallback)
        # Group tracks by original file
        files_to_process = {}
        for track in tracks:
            orig_file = track.get('original_file')
            if orig_file:
                if orig_file not in files_to_process:
                    files_to_process[orig_file] = []
                files_to_process[orig_file].append(track)
        
        # Process each original file
        for file_idx, (orig_file, file_tracks) in enumerate(files_to_process.items(), 1):
            filename = os.path.basename(orig_file)
            tracks_in_file = len([t for t in file_tracks if t['status'] != 'skipped'])
            
            if not os.path.exists(orig_file):
                print(f"⚠️  FILE {file_idx}/{len(files_to_process)}: {filename} - NOT FOUND, skipping {len(file_tracks)} tracks")
                continue
            
            # Get file size
            file_size_mb = os.path.getsize(orig_file) / (1024 * 1024)
            
            # Loading phase
            print(f"FILE {file_idx}/{len(files_to_process)}: {filename[:40]} ({file_size_mb:.1f} MB)")
            print(f"     └─ Loading...")
            
            recording = AudioSegment.from_file(orig_file)
            duration_minutes = len(recording) / 1000 / 60
            
            # Check if this file needed splitting
            if duration_minutes < 8:
                print(f"     └─ Single track ({duration_minutes:.1f} min)")
                chunks = [recording]
            else:
                # Mix - split it
                print(f"     └─ Splitting {duration_minutes:.0f} min mix... (this may take a moment)")
                
                chunks = split_on_silence(recording, min_silence_len=2000, silence_thresh=-40, keep_silence=200)
                print(f"     └─ ✓ Found {len(chunks)} tracks")
            
            # Now export tracks
            print(f"     └─ Exporting {tracks_in_file} tracks...")
            
            file_identified = 0
            file_unidentified = 0
            file_skipped = 0
            
            # Process each track from this file
            for track_idx, track in enumerate(file_tracks, 1):
                if track['status'] == 'skipped':
                    skipped_count += 1
                    file_skipped += 1
                    continue
                
                # Get the corresponding chunk
                chunk_idx = track.get('chunk_index', 0)
                if chunk_idx >= len(chunks):
                    print(f"     ⚠️  Warning: chunk index {chunk_idx} out of range")
                    continue
                
                chunk = chunks[chunk_idx]
                
                if track['status'] == 'identified':
                    # Create FLAC
                    temp_flac = os.path.join(output_folder, f"temp_apply_{track['file_num']}_{track['index']}.flac")
                    chunk.export(temp_flac, format="flac")
                    
                    # Embed metadata and organize
                    embed_and_sort_flac(
                        temp_flac,
                        track['artist'],
                        track['title'],
                        track['album'],
                        track.get('art_url'),
                        output_folder,
                        artwork_cache,
                        track.get('enhanced_metadata', {})
                    )
                    identified_count += 1
                    file_identified += 1
                    
                elif track['status'] == 'unidentified':
                    # Export unidentified track
                    chunk.export(track['unidentified_path'], format="flac")
                    unidentified_count += 1
                    file_unidentified += 1
            
            # Final status for this file
            print(f"     └─ ✓ Done")
            
            # Show breakdown for this file
            status_parts = []
            if file_identified > 0:
                status_parts.append(f"{file_identified} identified")
            if file_unidentified > 0:
                status_parts.append(f"{file_unidentified} unidentified")
            if file_skipped > 0:
                status_parts.append(f"{file_skipped} skipped")
            
            if status_parts:
                print(f"     └─ {', '.join(status_parts)}")
            
            # Clean up
            del recording
            del chunks
    
    print(f"\n{'='*60}")
    print(f"✅ APPLY COMPLETE!")
    print(f"{'─'*60}")
    print(f"  ✅ Identified:   {identified_count} files created")
    print(f"  ❓ Unidentified: {unidentified_count} files created")
    print(f"  ⏭️  Skipped:      {skipped_count} (already existed)")
    print(f"{'─'*60}")
    print(f"  Total created:  {identified_count + unidentified_count} files")
    print(f"{'='*60}\n")
    
    # Ask if user wants to delete cache and temp files
    try:
        response = input("Delete cache and temp files? (y/n): ").strip().lower()
        if response == 'y':
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"✅ Cache deleted: {cache_path}")
            if os.path.exists(temp_audio_folder):
                shutil.rmtree(temp_audio_folder)
                print(f"✅ Temp folder deleted: {temp_audio_folder}")
    except:
        pass

def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print("\n========================================")
    print(f"             MixSplitR v{CURRENT_VERSION}          ")
    print("         MIX ARCHIVAL TOOL by KJD     ")
    print("========================================\n")
    
    # Check for updates (non-blocking, silent on network failure)
    update_info = check_for_updates()
    if isinstance(update_info, dict):
        print(f"🆕 Update available! v{update_info['latest']} (you have v{update_info['current']})")
        print(f"   Download: {update_info['url']}")
        print()
    elif update_info is False:
        print(f"✅ You're running the latest version (v{CURRENT_VERSION})")
        print()
    # If None, check failed silently - don't show anything
    
    # Interactive menu for mode selection
    print("What would you like to do?\n")
    print("  1. Process files directly (classic mode)")
    print("     → Processes everything immediately, no preview")
    print()
    print("  2. Preview changes first (safe mode)")
    print("     → Analyze files and review before creating anything")
    print()
    print("  3. Apply cached preview")
    print("     → Create files from a previous preview session")
    print()
    print("  4. Cancel/Delete cached preview")
    print("     → Remove preview cache and start fresh")
    print()
    
    while True:
        choice = input("Enter your choice (1-4): ").strip()
        if choice in ['1', '2', '3', '4']:
            break
        print("❌ Invalid choice. Please enter 1, 2, 3, or 4.")
    
    print()
    
    # Calculate base directory FIRST (needed for cache path)
    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    
    # Handle mode based on choice
    cache_path = os.path.join(base_dir, "mixsplitr_cache.json")
    temp_audio_folder = os.path.join(base_dir, "mixsplitr_temp")
    
    if choice == '4':
        # Cancel mode - delete cache AND temp folder
        deleted_something = False
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"✅ Cache deleted: {cache_path}")
            deleted_something = True
        if os.path.exists(temp_audio_folder):
            shutil.rmtree(temp_audio_folder)
            print(f"✅ Temp audio folder deleted: {temp_audio_folder}")
            deleted_something = True
        if not deleted_something:
            print(f"ℹ️  No cache or temp files found")
        close_terminal()
        return
    
    if choice == '3':
        # Apply mode
        apply_from_cache(cache_path, temp_audio_folder)
        close_terminal()
        return
    
    # Continue with normal/preview mode (choices 1 or 2)
    preview_mode = (choice == '2')
    
    config = get_config()
    from pydub.silence import split_on_silence
    from tqdm import tqdm
    from acrcloud.recognizer import ACRCloudRecognizer

    if preview_mode:
        print("\n" + "="*50)
        print("     PREVIEW MODE - No files will be created")
        print("="*50 + "\n")
    
    # base_dir already defined above for cache_path
    
    # Support multiple audio formats
    audio_extensions = ['*.wav', '*.flac', '*.mp3', '*.m4a', '*.ogg', '*.aac', '*.wma', '*.aiff', '*.opus']
    audio_files = []
    for ext in audio_extensions:
        audio_files.extend(glob.glob(os.path.join(base_dir, ext)))
    
    if not audio_files:
        print("No audio files found. Supported formats: WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS"); 
        input(); 
        sys.exit()

    print(f"Found {len(audio_files)} file(s) to process\n")
    
    output_folder = os.path.join(base_dir, "My_Music_Library")
    os.makedirs(output_folder, exist_ok=True)
    re = ACRCloudRecognizer(config)

    # Scan existing library ONCE at startup (not per batch)
    print(f"🔍 Scanning existing library for duplicates...")
    existing_tracks = scan_existing_library(output_folder)
    if existing_tracks:
        print(f"   Found {len(existing_tracks)} existing track(s) - these will be skipped\n")
    else:
        print(f"   No existing tracks found - all will be processed\n")

    # Get available RAM and create batches
    available_ram = get_available_ram_gb()
    batches = create_file_batches(audio_files, available_ram)
    
    print(f"\nCreated {len(batches)} batch(es) for processing")
    print(f"{'='*50}\n")

    # Global statistics across all batches
    total_tracks_processed = 0
    total_identified = 0
    total_unidentified = 0
    total_skipped = 0
    
    # For preview mode: store all results and artwork
    all_results = []
    artwork_cache_global = {}

    # Process each batch
    for batch_num, batch_files in enumerate(batches, 1):
        print(f"\n{'='*50}")
        print(f"BATCH {batch_num}/{len(batches)} - Processing {len(batch_files)} file(s)")
        print(f"{'='*50}\n")
        
        # PHASE 1: Split files in this batch and collect chunks
        print(f"PHASE 1: SPLITTING BATCH {batch_num}")
        print(f"{'-'*50}\n")
        
        all_chunks = []  # Store all chunks for this batch
        
        for file_num_in_batch, audio_file in enumerate(batch_files, 1):
            global_file_num = sum(len(b) for b in batches[:batch_num-1]) + file_num_in_batch
            filename = os.path.basename(audio_file)
            
            # Get file size for context
            file_size_mb = os.path.getsize(audio_file) / (1024 * 1024)
            
            # Display file info
            print(f"FILE {global_file_num}/{len(audio_files)}: {filename[:40]} ({file_size_mb:.1f} MB)")
            print(f"     └─ Loading...", end='', flush=True)
            
            recording = AudioSegment.from_file(audio_file)
            
            # Check duration - if under 8 minutes, treat as single track
            duration_minutes = len(recording) / 1000 / 60
            
            if duration_minutes < 8:
                print(f" ✓ Single track ({duration_minutes:.1f} min)")
                all_chunks.append({
                    'chunk': recording,
                    'file_num': global_file_num,
                    'filename': os.path.basename(audio_file),
                    'original_file': audio_file  # Store original file path
                })
            else:
                print()  # End the "Loading..." line
                print(f"     └─ Splitting {duration_minutes:.0f} min mix... (this may take a moment)")
                
                chunks = split_on_silence(recording, min_silence_len=2000, silence_thresh=-40, keep_silence=200)
                
                # Calculate track duration stats
                track_durations = [len(c) / 1000 / 60 for c in chunks]  # in minutes
                avg_duration = sum(track_durations) / len(track_durations) if track_durations else 0
                min_duration = min(track_durations) if track_durations else 0
                max_duration = max(track_durations) if track_durations else 0
                
                print(f"     └─ ✓ Found {len(chunks)} tracks ({min_duration:.1f}-{max_duration:.1f} min, avg {avg_duration:.1f} min)")
                
                # Store chunks with file info
                for chunk_idx, chunk in enumerate(chunks):
                    all_chunks.append({
                        'chunk': chunk,
                        'file_num': global_file_num,
                        'filename': os.path.basename(audio_file),
                        'original_file': audio_file,  # Store original file path
                        'is_split': True,  # Mark that this came from splitting
                        'split_index': chunk_idx  # Which chunk from the split
                    })
            
            # Clear the recording from memory
            del recording
        
        print(f"\nBatch {batch_num} splitting complete! Tracks found: {len(all_chunks)}")
        
        # PHASE 1.5: Save chunks to temp folder (PREVIEW MODE ONLY)
        if preview_mode:
            os.makedirs(temp_audio_folder, exist_ok=True)
            print(f"\n💾 Saving {len(all_chunks)} chunks to temp folder...")
            
            for chunk_idx, chunk_data in enumerate(all_chunks):
                # Generate temp file path
                temp_filename = f"chunk_{chunk_data['file_num']}_{chunk_data.get('split_index', 0)}.flac"
                temp_path = os.path.join(temp_audio_folder, temp_filename)
                
                # Export chunk to temp file
                chunk_data['chunk'].export(temp_path, format="flac")
                
                # Store temp path in chunk_data for later use
                chunk_data['temp_chunk_path'] = temp_path
            
            print(f"   ✓ Saved {len(all_chunks)} chunks to temp folder")
        
        
        # PHASE 2: Identify and organize chunks in this batch (PARALLEL PROCESSING)
        print(f"\n{'='*50}")
        print(f"PHASE 2: IDENTIFYING & ORGANIZING BATCH {batch_num} (Parallel)")
        if preview_mode:
            print(f"           [PREVIEW - No files will be created]")
        print(f"{'='*50}\n")
        
        # Initialize rate limiter and thread-safe counters
        rate_limiter = RateLimiter(min_interval=1.2)
        existing_tracks_lock = threading.Lock()
        
        skipped_count = 0
        identified_count = 0
        unidentified_count = 0
        
        # Process tracks in parallel with 4 workers
        num_workers = 4
        results = []
        
        print(f"🚀 Processing {len(all_chunks)} tracks with {num_workers} parallel workers...")
        
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Submit all tracks for processing
            future_to_track = {
                executor.submit(
                    process_single_track,
                    chunk_data, i, re, rate_limiter, 
                    existing_tracks, output_folder, existing_tracks_lock, preview_mode
                ): (chunk_data, i) 
                for i, chunk_data in enumerate(all_chunks)
            }
            
            # Collect results with progress bar
            from tqdm import tqdm
            for future in tqdm(as_completed(future_to_track), total=len(all_chunks), desc=f"Batch {batch_num}"):
                result = future.result()
                results.append(result)
                
                # Update counters based on result
                if result['status'] == 'skipped':
                    skipped_count += 1
                elif result['status'] == 'unidentified':
                    unidentified_count += 1
                elif result['status'] == 'identified':
                    identified_count += 1
        
        # Store all results for cache (if preview mode)
        all_results.extend(results)
        
        # PHASE 2.5: Batch process artwork and finalize identified tracks (SKIP IN PREVIEW MODE)
        if not preview_mode:
            print(f"\n🎨 Finalizing {identified_count} identified tracks with artwork...")
            
            # Collect all identified tracks that need artwork
            identified_results = [r for r in results if r['status'] == 'identified']
            
            if identified_results:
                # Batch download all artwork (optimization #3)
                artwork_urls = [r['art_url'] for r in identified_results if r.get('art_url')]
                artwork_cache = batch_download_artwork(artwork_urls) if artwork_urls else {}
                
                # Finalize all identified tracks with cached artwork and enhanced metadata
                for result in identified_results:
                    embed_and_sort_flac(
                        result['temp_flac'],
                        result['artist'],
                        result['title'],
                        result['album'],
                        result['art_url'],
                        output_folder,
                        artwork_cache,
                        result.get('enhanced_metadata', {})
                    )
        else:
            # In preview mode, still download artwork for cache
            identified_results = [r for r in results if r['status'] == 'identified']
            if identified_results:
                artwork_urls = [r['art_url'] for r in identified_results if r.get('art_url')]
                temp_artwork_cache = batch_download_artwork(artwork_urls) if artwork_urls else {}
                # Store artwork in global cache
                for url, img_data in temp_artwork_cache.items():
                    artwork_cache_global[url] = img_data

        total_tracks_processed += len(all_chunks)
        
        # Update global statistics
        total_identified += identified_count
        total_unidentified += unidentified_count
        total_skipped += skipped_count
        
        # Display batch statistics
        print(f"\n{'='*50}")
        print(f"Batch {batch_num} Statistics:")
        print(f"  Identified & Saved: {identified_count}")
        print(f"  Unidentified: {unidentified_count}")
        print(f"  Skipped (already exist): {skipped_count}")
        print(f"{'='*50}")
        
        # Clean up memory after this batch
        print(f"\nClearing batch {batch_num} from memory...")
        del all_chunks
        gc.collect()
        
        print(f"Batch {batch_num}/{len(batches)} complete!")

    # PREVIEW MODE: Save cache and display preview
    if preview_mode:
        # Prepare cache data
        cache_data = {
            'tracks': all_results,
            'output_folder': output_folder,
            'artwork_cache': {}
        }
        
        # Encode artwork cache to base64 for JSON (with progress)
        if artwork_cache_global:
            print(f"\n📦 Encoding {len(artwork_cache_global)} artworks for cache...")
            for idx, (url, img_data) in enumerate(artwork_cache_global.items(), 1):
                try:
                    cache_data['artwork_cache'][url] = base64.b64encode(img_data).decode('utf-8')
                    if idx % 10 == 0:  # Progress every 10 artworks
                        print(f"   Encoded {idx}/{len(artwork_cache_global)} artworks...")
                except Exception as e:
                    print(f"   ⚠️  Failed to encode artwork {idx}: {e}")
        
        # Free up memory before saving
        print(f"\n🧹 Freeing memory before save...")
        del artwork_cache_global
        gc.collect()
        
        # Save cache
        if save_preview_cache(cache_data, cache_path):
            # Display preview table
            display_preview_table(cache_data)
            
            # Ask user if they want to apply now
            print("\n" + "="*60)
            print("What would you like to do next?")
            print("="*60)
            print("\n  1. Apply changes now (create all files)")
            print("  2. Review & Edit (opens cache file, then exit)")
            print("  3. Cancel (delete cache and exit)")
            print()
            
            while True:
                next_choice = input("Enter your choice (1-3): ").strip()
                if next_choice in ['1', '2', '3']:
                    break
                print("❌ Invalid choice. Please enter 1, 2, or 3.")
            
            if next_choice == '1':
                # Apply immediately
                print("\n" + "="*60)
                print("Applying changes...")
                print("="*60 + "\n")
                apply_from_cache(cache_path, temp_audio_folder)
            elif next_choice == '3':
                # Cancel - delete cache and temp folder
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    print(f"\n✅ Cache deleted: {cache_path}")
                if os.path.exists(temp_audio_folder):
                    shutil.rmtree(temp_audio_folder)
                    print(f"✅ Temp folder deleted: {temp_audio_folder}")
            else:
                # Exit - keep cache and open it for editing
                print(f"\n✅ Preview saved to {cache_path}")
                print(f"   Opening cache file for review...")
                print(f"   Run the program again and choose option 3 to apply changes.")
                
                # Open the JSON file with default application
                if sys.platform == 'darwin':  # macOS
                    subprocess.run(['open', cache_path], check=False)
                    # Close terminal automatically on macOS
                    os.system('osascript -e "tell application \\"Terminal\\" to close first window" & exit')
                elif sys.platform == 'win32':  # Windows
                    os.startfile(cache_path)
                else:  # Linux
                    subprocess.run(['xdg-open', cache_path], check=False)
                return  # Skip close_terminal() prompt
        
        close_terminal()
        return

    # Final comprehensive summary
    print(f"\n{'='*60}")
    print(f"{'='*60}")
    print(f"            ALL PROCESSING COMPLETE!            ")
    print(f"{'='*60}")
    print(f"{'='*60}\n")
    
    print(f"📊 FINAL STATISTICS:")
    print(f"{'─'*60}")
    print(f"  Total Files Processed:        {len(audio_files)}")
    print(f"  Total Tracks Found:           {total_tracks_processed}")
    print(f"{'─'*60}")
    print(f"  ✅ Identified & Saved:        {total_identified}")
    print(f"  ❓ Unidentified:              {total_unidentified}")
    print(f"  ⏭️  Skipped (already exist):   {total_skipped}")
    print(f"{'─'*60}")
    
    # Calculate percentages if we have tracks
    if total_tracks_processed > 0:
        identified_pct = (total_identified / total_tracks_processed) * 100
        print(f"  Identification Success Rate:  {identified_pct:.1f}%")
        print(f"{'─'*60}")
    
    print(f"\n💾 Output Location:")
    print(f"  {output_folder}")
    
    print(f"\n{'='*60}")
    print(f"Your music library is ready! 🎵")
    print(f"{'='*60}\n")
    
    close_terminal()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
