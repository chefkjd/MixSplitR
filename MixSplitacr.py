import os
import sys
import glob
import json
import time
import shutil
import threading
import requests
import itertools

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

def embed_and_sort_flac(file_path, artist, title, album, cover_url, base_output_folder):
    from mutagen.flac import FLAC, Picture
    try:
        audio = FLAC(file_path)
        audio["artist"], audio["title"], audio["album"] = artist, title, album
        
        img_data = None
        if cover_url:
            if "{w}x{h}" in cover_url: cover_url = cover_url.replace("{w}x{h}", "600x600")
            try:
                img_res = requests.get(cover_url, timeout=10)
                if img_res.status_code == 200:
                    img_data = img_res.content
                    pic = Picture()
                    pic.data, pic.type, pic.mime = img_data, 3, u"image/jpeg"
                    audio.add_picture(pic)
            except: pass 
        
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

# --- 4. MAIN ENGINE ---
def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    config = get_config()
    from pydub.silence import split_on_silence
    from tqdm import tqdm
    from acrcloud.recognizer import ACRCloudRecognizer

    print("\n========================================")
    print("              MixSplitR v6.3          ")
    print("         MIX ARCHIVAL TOOL by KJD     ")
    print("========================================\n")
    
    base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    audio_files = glob.glob(os.path.join(base_dir, "*.wav")) + glob.glob(os.path.join(base_dir, "*.flac"))
    
    if not audio_files:
        print("❌ No files found."); input(); sys.exit()

    print(f"✨ Found {len(audio_files)} file(s) to process\n")
    
    output_folder = os.path.join(base_dir, "My_Music_Library")
    os.makedirs(output_folder, exist_ok=True)
    re = ACRCloudRecognizer(config)

    # PHASE 1: Split all files and collect chunks
    print(f"\n{'='*50}")
    print(f"🎵 PHASE 1: SPLITTING ALL FILES")
    print(f"{'='*50}\n")
    
    all_chunks = []  # Store all chunks with their file info
    
    for file_num, audio_file in enumerate(audio_files, 1):
        print(f"📀 FILE {file_num}/{len(audio_files)}: {os.path.basename(audio_file)}")
        
        print(f"   ⏳ Reading audio file...")
        recording = AudioSegment.from_file(audio_file)
        
        # Check duration - if under 8 minutes, treat as single track
        duration_minutes = len(recording) / 1000 / 60  # Convert ms to minutes
        
        if duration_minutes < 8:
            print(f"   🎵 Single track detected ({duration_minutes:.1f} min) - skipping split")
            all_chunks.append({
                'chunk': recording,
                'file_num': file_num,
                'filename': os.path.basename(audio_file)
            })
        else:
            print(f"   🎛️  Mix detected ({duration_minutes:.1f} min) - splitting tracks...")
            
            # Spinner for split_on_silence
            def spinner_task(stop_event):
                spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
                while not stop_event.is_set():
                    sys.stdout.write(f'\r   ⏳ Splitting tracks... {next(spinner)}')
                    sys.stdout.flush()
                    time.sleep(0.1)
                sys.stdout.write('\r' + ' ' * 70 + '\r')  # Clear the line
                sys.stdout.flush()
            
            stop_spinner = threading.Event()
            spinner_thread = threading.Thread(target=spinner_task, args=(stop_spinner,))
            spinner_thread.start()
            
            chunks = split_on_silence(recording, min_silence_len=2000, silence_thresh=-40, keep_silence=200)
            
            stop_spinner.set()
            spinner_thread.join()
            
            print(f"   ✓ Found {len(chunks)} tracks")
            
            # Store chunks with file info
            for chunk in chunks:
                all_chunks.append({
                    'chunk': chunk,
                    'file_num': file_num,
                    'filename': os.path.basename(audio_file)
                })
    
    print(f"\n✅ Splitting complete! Total tracks found: {len(all_chunks)}")
    
    # PHASE 2: Identify and organize all chunks
    print(f"\n{'='*50}")
    print(f"🔍 PHASE 2: IDENTIFYING & ORGANIZING TRACKS")
    print(f"{'='*50}\n")

    for i, chunk_data in enumerate(tqdm(all_chunks, desc="Processing all tracks")):
        chunk = chunk_data['chunk']
        file_num = chunk_data['file_num']
        
        if len(chunk) < 10000: continue
        
        sample = chunk[len(chunk)//2 : len(chunk)//2 + 12000]
        temp_name = f"temp_id_{file_num}_{i}.wav"
        sample.export(temp_name, format="wav")
        
        res = json.loads(re.recognize_by_file(temp_name, 0))
        if os.path.exists(temp_name): os.remove(temp_name)
        time.sleep(1.2)

        if res.get("status", {}).get("code") == 0 and res.get("metadata", {}).get("music"):
            music = res["metadata"]["music"][0]
            artist = music["artists"][0]["name"]
            title = music["title"]
            album = music.get("album", {}).get("name", "Unknown Album")
            
            art_url = find_art_in_json(music)
            if not art_url:
                art_url = get_backup_art(artist, title)

            temp_flac = os.path.join(output_folder, f"temp_{file_num}_{i}.flac")
            chunk.export(temp_flac, format="flac")
            embed_and_sort_flac(temp_flac, artist, title, album, art_url, output_folder)
        else:
            chunk.export(os.path.join(output_folder, f"File{file_num}_Track_{i+1}_Unidentified.flac"), format="flac")

    print(f"\n{'='*50}")
    print(f"✅ ALL COMPLETE!")
    print(f"📁 Processed {len(audio_files)} file(s), {len(all_chunks)} tracks")
    print(f"💾 Output folder: {output_folder}")
    print(f"{'='*50}")
    input("\nPress Enter to close..."); os.system('osascript -e "tell application \\"Terminal\\" to close first window" & exit')

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()