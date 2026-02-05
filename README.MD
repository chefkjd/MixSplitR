# MixSplitR v7.0
### Mix Archival Tool by KJD

MixSplitR helps you organize your digitized vinyl and CD collection. It works with both recorded mixes and individual tracks, intelligently detecting whether your files are mixes or single songs. For mixes, it splits them into individual tracks, then identifies each song using audio fingerprinting, and organizes everything with full metadata and artwork.

**Go from a folder of untitled recordings → To a perfectly organized music library with artist folders, proper filenames, embedded artwork, genres, and BPM!**

###  v7.0 Highlights
-  **Visual Waveform Splitter** — Finally handle seamless DJ mixes! See the waveform, place splits exactly where you want
-  **Interactive Track Editor** — Listen to unidentified tracks, enter metadata manually, fix mistakes before saving
-  **Audio Preview** — Hear tracks directly in the app to identify them by ear
-  **5-Source Identification** — ACRCloud + MusicBrainz + iTunes + Deezer + Last.fm for maximum metadata

## 📥 **DOWNLOAD:** [Gitlab Releases](https://gitlab.com/chefd/mixsplitr/-/releases)

---

## ⚠️ IMPORTANT - PLEASE READ

- **For Personal Use Only:** This tool is designed to help you organize and archive your personal music collection from digitized vinyl, CDs, and recordings you own or have legal access to.

- **DJ Mixes Now Supported:** v7.0 adds a **Visual Waveform Splitter** for seamless DJ mixes with no gaps between tracks. Use **Manual** or **Assisted** splitting mode to place split points exactly where you want them.

- **KEEP YOUR API KEYS PRIVATE:** Your `config.json` file contains your ACRCloud credentials. **DO NOT share this file, upload it to public repositories (GitHub, etc.), or post it online.** Anyone with access to your credentials can use your API quota. If you accidentally expose it, regenerate your API keys in the ACRCloud console immediately.

- **Config Location:** Your config and cache are stored safely in your user data folder:
  - **Windows:** `%APPDATA%\MixSplitR\config.json`
  - **macOS:** `~/Library/Application Support/MixSplitR/config.json`
  - **Linux:** `~/.config/MixSplitR/config.json`

---

##  What's New in v7.0

###  HEADLINE FEATURES

####  Visual Waveform Splitter — Finally, DJ Mixes Are Possible!

MixSplitR can now handle **seamless DJ mixes** with no gaps between tracks. The new browser-based visual editor lets you see the waveform and place split points exactly where you want them.

**Three splitting modes:**

| Mode | Best For |
|------|----------|
| **Automatic** | Radio recordings, podcasts, cassette rips with clear gaps |
| **Manual** | Seamless DJ mixes where you set every split point |
| **Assisted** | Mixes where auto-detection is *mostly* right but needs tweaks |

**Assisted Mode** is the sweet spot — it auto-detects silence gaps, pre-populates split points on the waveform, then lets you review and adjust before confirming. Perfect for recordings that have *some* gaps but need manual correction.

```
  📂 Loading mix: DJ_Set_2024.wav... 
  🔍 Auto-detecting split points... Found 12 potential splits
  🌐 Opening visual editor at http://localhost:8765
```

**Visual Editor Controls:**
- **Scroll wheel** to zoom in/out on the waveform
- **Double-click** to add split points
- **Drag** markers to fine-tune timing (won't affect playback position)
- **Ctrl+click** (Cmd+click on Mac) to remove a marker
- **Click** anywhere to seek/listen
- **Space** to play/pause
- Auto-closes when you click **Done & Continue**

####  Interactive Track Editor — Full Control Over Your Metadata

**Edit everything without leaving the app.** The new in-terminal track editor gives you complete control over your metadata before files are created.

**Why this matters:**
-  **Audio Preview** — Listen to unidentified tracks to figure out what they are
-  **Convert Unidentified → Identified** — Manually enter metadata for tracks the API couldn't recognize
-  **Fix Mistakes** — Correct wrong artist names, titles, or albums
-  **Add Missing Data** — Fill in genres and BPM that weren't found
-  **Review Before Committing** — See exactly what will be created

```
════════════════════════════════════════════════════════════
  TRACK EDITOR
════════════════════════════════════════════════════════════
  ✅ 10 identified  ❓ 3 unidentified  ⏭️ 0 skipped
  ⚠️  2 missing genre  ⚠️  1 missing BPM

  Page 1/1
────────────────────────────────────────────────────────────
    1. ✅ Skrillex         - Bangarang               G:Dubstep    B:110
    2. ✅ Excision         - Throwin' Elbows         G:Dubstep    B:150
    3. ❓ File1_Track_3_Unidentified.flac            BPM:~140
    4. ✅ Virtual Riot     - Energy Drink            G:---        B:150
────────────────────────────────────────────────────────────
```

**Editing an unidentified track:**
```
════════════════════════════════════════════════════════════
  EDITING TRACK 3
════════════════════════════════════════════════════════════
  Status: ❓ Unidentified
  File: File1_Track_3_Unidentified.flac
  Detected BPM: ~140

  What would you like to do?
  1. 🎧 Play audio preview (hear the track)
  2. ✏️  Enter metadata (convert to identified)
  3. ← Go back
```

Press **1** to hear the track, recognize it, then press **2** to enter the artist/title/album. The track converts from unidentified to identified and will be saved with full metadata!

---

###  Additional v7.0 Features

###  API Key Validation
- Credentials validated at startup before processing
- Instant feedback if your API keys are invalid or expired
- Test credentials anytime from the Manage API Keys menu

###  Audio Preview in Editor
- Listen to unidentified tracks before entering metadata
- Play 30-second previews directly in the terminal
- Works on macOS (afplay), Windows, and Linux

###  Safe Config & Cache Storage
- Config and cache files now stored in your user data folder
- **Windows:** `%APPDATA%\MixSplitR\`
- **macOS:** `~/Library/Application Support/MixSplitR/`
- Won't be accidentally deleted when updating the program

###  Better Progress Feedback
- Real-time status updates during processing
- See exactly what's happening: loading, splitting, identifying, saving
- Progress bars with track counts

###  5-Source Identification System
MixSplitR now queries **5 different music databases** for the most complete metadata:

| Source | What It Provides |
|--------|------------------|
| **ACRCloud** | Primary identification via audio fingerprinting |
| **MusicBrainz** | Free fallback + release year, ISRC codes |
| **iTunes** | High-quality album artwork |
| **Deezer** | BPM data and additional genre info |
| **Last.fm** | User-generated tags (great for EDM subgenres!) |

###  Automatic BPM Detection
- **Deezer lookup** for known tracks
- **Local librosa analysis** as offline fallback
- Works even when you're not connected to the internet
- Optimized for EDM (70-180 BPM normalization)

###  Enhanced Genre Detection
Genres are now collected from ALL sources and intelligently merged:
- ACRCloud, MusicBrainz, iTunes, Deezer genres
- Last.fm user tags (riddim, future bass, melodic techno, etc.)

---

##  Features

### Core Features
- **Automatic Track Splitting** - Detects silence between tracks to split your mixes
- **Visual Splitter** - Browser-based waveform editor for seamless DJ mixes
- **Assisted Splitting** - Auto-detect + manual review for best of both worlds
- **5-Source Identification** - ACRCloud + MusicBrainz + iTunes + Deezer + Last.fm
- **Full Metadata Tagging** - Artist, title, album, year, genre, BPM, and more
- **Artwork Embedding** - Downloads and embeds high-quality album artwork
- **Lossless FLAC Output** - Exports all tracks in high-quality FLAC format
- **macOS ALAC Option** - Full artwork compatibility on Mac
- **Multi-Format Support** - WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS
- **Organized Output** - Automatically sorts tracks by artist into folders

### Identification & Metadata
- **Dual Identification** - ACRCloud (fast, accurate) + MusicBrainz (free, unlimited)
- **BPM Detection** - From Deezer API + local librosa analysis fallback
- **Genre Merging** - Collects genres from all 5 sources, deduplicates intelligently
- **Last.fm Tags** - User-generated tags for better EDM subgenre detection
- **Enhanced Metadata** - Release year, ISRC codes, multiple genres

###  Preview & Apply Workflow
- **Preview Mode** - Analyze all files and review before committing
- **Light Preview** - No temp storage needed, re-splits during apply
- **Full Preview** - Cached audio chunks for instant apply + audio playback
- **Interactive Editor** - Edit tracks directly in terminal with audio preview
- **Audio Preview** - Listen to unidentified tracks before entering metadata

###  User Experience
- **API Key Validation** - Credentials tested before processing begins
- **Safe Config Storage** - Config/cache stored in user data folder (won't be deleted on update)
- **Progress Feedback** - Real-time status showing loading, splitting, identifying, saving
- **Contextual Menus** - Clear descriptions for every option

### Performance
- **Parallel Processing** - 4 worker threads (2-4x faster identification)
- **Parallel File Analysis** - Instant single vs mix detection
- **Batch Artwork Downloads** - Downloads album art in parallel
- **RAM-Aware Batching** - Automatically optimizes based on system RAM
- **Skip Existing Tracks** - Detects and skips tracks already in your library
- **Lazy Loading** - Heavy libraries (librosa) load only when needed

---

##  Requirements

### Required
- **ACRCloud Account** (Free tier: 2,000 identifications/month)

### Optional (Recommended)
- **Last.fm API Key** (Free - enables better genre tagging)
  - Get yours at: https://www.last.fm/api/account/create
  
### Bundled in Releases
The compiled releases include everything you need:
- MusicBrainz/AcoustID (fallback identification)
- psutil (RAM-aware processing)
- librosa (offline BPM detection)
- All other dependencies

---

##  Getting Started

### Step 1: Set Up ACRCloud Account

1. **Create an Account** at https://console.acrcloud.com/signup
2. **Create a Project** → Choose "Audio & Video Recognition"
3. **Get Your Credentials:**
   - Host (e.g., `identify-us-west-2.acrcloud.com`)
   - Access Key
   - Access Secret

**Free Tier:** 2,000 identifications/month (enough for ~30-40 mixes)

### Step 2: First Run Setup

When you first run MixSplitR, it will guide you through setup:

```
============================================================
          ACRCloud API Setup (Required)
============================================================
ACRCloud provides the music recognition service.
Get your free API keys at: https://console.acrcloud.com

Enter your ACR Host: identify-us-west-2.acrcloud.com
Enter your Access Key: xxxxxxxxxx
Enter your Secret Key: xxxxxxxxxx

------------------------------------------------------------
          Last.fm API Setup (Optional)
------------------------------------------------------------
Last.fm improves genre detection with user-generated tags.
Get a free API key at: https://www.last.fm/api/account/create

Add Last.fm API key for better tagging? (y/n) [n]: y
Enter your Last.fm API Key: xxxxxxxxxx
✅ Last.fm API key added!

============================================================
          Setup Complete!
============================================================
```

### Step 3: Prepare Your Audio Files

- **Windows/Linux:** Place audio files in the same folder as MixSplitR
- **macOS .app:** Place audio files next to the .app (portable mode) or in `~/Music`
- **Single tracks:** Files under 8 minutes
- **Mixes:** Files 8+ minutes (split automatically)
- For automatic splitting, ensure clear gaps of silence between tracks (at least 2 seconds)
- For seamless DJ mixes, use **Manual** or **Assisted** splitting mode

### Step 4: Run MixSplitR

```
Windows: Double-click MixSplitR.exe
Mac:     Double-click MixSplitR (or run in Terminal)
Linux:   python MixSplitR.py
```

---

##  How It Works

### Interactive Menu

```
============================================================
              MixSplitR v7.0
          MIX ARCHIVAL TOOL by KJD
============================================================

  What would you like to do?

  1. 👁️  Preview Mode
       Analyze files, identify tracks, review before saving
       → Best for first-time use or reviewing results

  2. ⚡ Direct Mode (One-Click)
       Process everything immediately, save as you go
       → Fast, no review step

  3. 📦 Apply Cached Preview
       Apply a previous preview session's results
       → Use after editing preview in external editor

  4. 🗑️  Delete Cache
       Remove cached preview data and temp files

  5. 🔑 Manage API Keys
       View or update ACRCloud/Last.fm credentials

  Enter choice (1-5):
```

###  Option 1: Preview Mode (RECOMMENDED)

**This is the smart way to use MixSplitR.**

When you select Preview Mode:

1. **API Validation** - Checks your credentials before processing
2. **File Analysis** - Instantly analyzes all files (parallel ffprobe)
3. **Progress Feedback** - Shows what's happening: loading, splitting, identifying
4. **Batch Summary** - Shows identified/unidentified/skipped counts
5. **Preview Table** - Displays all metadata
6. **Next Steps** - Choose: Apply Now, Edit Tracks, or Cancel

**Preview Types:**

| Preview Type | Disk Usage | Apply Speed | Audio Preview |
|--------------|------------|-------------|---------------|
| Full Preview | ~85% of input size | Instant (cached) | ✅ Yes |
| Light Preview | None | Re-splits from source | ❌ No |

### Option 2: Direct Mode (One-Click)
- Processes everything immediately with no review step
- Best when you're confident tracks will identify correctly
- Most hands-off approach but may have accuracy issues
- Use Preview Mode if you want to review/edit before files are created

### Option 3: Apply Cached Preview
- Creates files from previous preview
- Uses cached audio (no re-splitting)
- Much faster than re-processing

### Option 4: Delete Cache
- Removes cache and temp files from safe storage location
- Use to start fresh

### Option 5: Manage API Keys
- View current API key status and config location
- Update ACRCloud credentials with validation
- Test credentials without processing files
- Add/update/remove Last.fm key

---

##  Interactive Track Editor

After preview completes, choose **Option 2: Edit tracks** to open the interactive editor:

```
══════════════════════════════════════════════════════════════
  TRACK EDITOR
══════════════════════════════════════════════════════════════
  ✅ 152 identified  ❓ 3 unidentified  ⏭️ 0 skipped
  ⚠️  12 missing genre  ⚠️  8 missing BPM

──────────────────────────────────────────────────────────────
  All Tracks (155 tracks)
  Page 1/11
──────────────────────────────────────────────────────────────
    1. ✅ Skrillex            - Bangarang               G:Dubstep    B:110
    2. ✅ Deadmau5            - Strobe                  G:Progress.. B:128
    3. ❓ File1_Track_3_Unidentified.flac               BPM:~126
    ...

──────────────────────────────────────────────────────────────
  FILTER:  1. All tracks    2. Missing genre   3. Missing BPM
           4. Unidentified  5. Identified only
  NAV:     6. Next page     7. Previous page
  EDIT:    8. Edit track #
  FINISH:  9. Save & Apply  0. Save & Exit     X. Discard
──────────────────────────────────────────────────────────────
  Enter choice: 
```

### Quick Reference

| # | Action |
|---|--------|
| 1 | Show all tracks |
| 2 | Show tracks missing genre |
| 3 | Show tracks missing BPM |
| 4 | Show unidentified tracks |
| 5 | Show identified only |
| 6 | Next page |
| 7 | Previous page |
| 8 | Edit a specific track |
| 9 | Save changes and create files |
| 0 | Save and exit (apply later) |
| X | Discard changes |

### Editing a Track

```
══════════════════════════════════════════════════════════════
  EDITING TRACK 3
══════════════════════════════════════════════════════════════
  Status: ❓ Unidentified
  File: File1_Track_3_Unidentified.flac
  Detected BPM: ~126

  Convert to identified track?
  1. Yes, enter metadata
  2. No, go back

  Enter choice (1-2): 1
  Artist: Knife Party
  Title: Internet Friends
  Album (Enter to skip): 100% No Modern Talking
  Genre (Enter to skip): Electro House
  BPM [126]: 

  ✅ Track converted: Knife Party - Internet Friends
```

---

##  Output Structure

```
My_Music_Library/
├── Skrillex/
│   ├── Skrillex - Bangarang.flac
│   ├── Skrillex - Scary Monsters.flac
│   └── folder.jpg
├── Deadmau5/
│   ├── Deadmau5 - Strobe.flac
│   └── folder.jpg
└── File1_Track_3_Unidentified.flac
```

Each FLAC file contains:
- Embedded artwork
- Artist, Title, Album
- Year, Genre(s), BPM
- ISRC code (when available)

---

##  Mix Splitting

MixSplitR offers two ways to split long mixes into individual tracks:

### Automatic (Silence Detection)
- **Default mode** - works for most mixes with gaps between tracks
- Great for radio recordings, podcasts, and cassette rips
- Fast and fully automatic

### Manual (Visual Editor)
- Opens a **browser-based waveform editor**
- **Scroll wheel** to zoom in/out on the waveform
- **Double-click** to add split points
- **Drag** markers to adjust (won't move playback position)
- **Ctrl+click** (or **Cmd+click** on Mac) a marker to remove it
- **Click** to seek, **Space** to play/pause
- Window auto-closes when done
- Perfect for **seamless DJ mixes** with no gaps

###  Assisted (Auto-Detect + Review)
- **Best of both worlds!** Combines automatic detection with manual review
- First scans for silence gaps and pre-populates split points
- Opens visual editor with points already placed on waveform
- Review, adjust, add, or remove points before confirming
- Great when automatic detection is *mostly* right but needs tweaks

When mixes are detected, you'll be prompted to choose:
```
============================================================
  SPLITTING MODE
============================================================

  Found 2 mix(es) that will be split into tracks.

  Splitting options:
    1. Automatic (silence detection) - default
       → Works for most mixes with gaps between tracks

    2. Manual (visual editor)
       → Opens browser to set split points on waveform
       → Best for seamless DJ mixes with no gaps

    3. Assisted (auto-detect + review)
       → Scans for silence, then opens visual editor
       → Review and adjust auto-detected points
       → Best of both worlds!

  Choose splitting mode (1/2/3) [1]:
```

---

##  macOS Users

### First Run (Gatekeeper)
When you first open MixSplitR.app:
- Right-click the app → "Open" → Click "Open" in dialog
- This only needs to be done once (unsigned app security check)

### Compatibility
- **M-series Macs (M1/M2/M3/M4):** Native ARM64 build
- **Intel Macs:** Runs via Rosetta 2 translation

Both architectures are fully supported. When compiling yourself, you can choose to build specifically for Intel if needed.

### Default Music Folder
The .app version checks for audio files in this order:

1. **Portable Mode:** The folder containing the .app bundle (if audio files are present)
2. **Default Mode:** `~/Music` (if no local files found)

This means you can either:
- **Use portably:** Place `MixSplitR.app` directly in a folder with your audio files
- **Use installed:** Keep the app anywhere and it will check `~/Music`

When portable mode is detected:
```
  📂 Found 12 audio files next to app
```

If no audio files are found in either location:
```
══════════════════════════════════════════════════════════════
  📂 NO AUDIO FILES FOUND
══════════════════════════════════════════════════════════════

  Checked: ~/Music

  Supported formats:
  WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS

  Options:
  • Drag and drop audio file(s) onto this window
  • Drag and drop a folder onto this window
  • Enter or paste a path below
  • Press Enter with no input to exit

  Path: 
```

### Output Format
MixSplitR detects macOS and offers ALAC output:

```
🍎 macOS detected!
   FLAC artwork may not display in Finder/Apple Music.
   Options:
   • ALAC (.m4a) - Full macOS compatibility, artwork shows everywhere
   • FLAC - Universal format, will create folder.jpg for Finder

Output format - FLAC (1) or ALAC (2)? [1]:
```

---

##  Delete Source Files

After Apply, MixSplitR can delete your original source files to save disk space:

```
📂 SOURCE FILES (safe to delete if happy with results)
──────────────────────────────────────────────────────────
  • mix_recording_001.wav (23 tracks extracted)
  • mix_recording_002.wav (18 tracks extracted)

  Total: 2 file(s), 4.2 GB

⚠️  WARNING: This cannot be undone! Your original recordings
   will be permanently deleted. Only do this if you're 100%
   satisfied with the extracted tracks.

Type 'delete' to remove source files (or press Enter to keep):
```

⚠️ This is **permanent** - only do this when you're 100% satisfied!

---

##  Troubleshooting

### "No audio files found"
- Ensure audio files are in the same folder as MixSplitR
- Check supported formats: WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS

### "ACRCloud API error"
- Verify your credentials in `config.json`
- Check your API quota at [console.acrcloud.com](https://console.acrcloud.com)

### "Chunk index out of range" in Apply Mode
- The source audio file may have changed since preview
- Delete cache (Option 4) and re-run preview

### Program seems frozen during splitting
- Splitting large mixes takes time (1-2 minutes for 60+ minute files)
- The progress display shows which file is being processed

### macOS: "Cache file not found" but file exists
- MixSplitR automatically handles macOS extended attributes
- If issues persist, run: `xattr -c mixsplitr_cache.json`

### Track was misidentified
- This is exactly why Preview Mode exists!
- Use Option 1, then "Edit tracks" to fix before applying

### BPM not detected
- Deezer may not have BPM for obscure tracks
- librosa fallback analyzes audio locally (works offline)
- Some tracks may still lack BPM data

### Last.fm not working
- Check your API key in Option 5 → Manage API Keys
- Last.fm is optional - everything else works without it

---

##  Version History

### v7.0 - Current Release
-  **5-Source Identification** - ACRCloud + MusicBrainz + iTunes + Deezer + Last.fm
-  **BPM Detection** - Deezer API + local librosa fallback (works offline!)
-  **Visual Splitter** - Browser-based waveform editor for seamless DJ mixes
-  **Enhanced Genres** - Merged from all sources, Last.fm user tags for EDM
-  **Interactive Editor** - Edit tracks directly in the terminal, no external files
-  **Drag & Drop** - Drop audio files or folders directly onto the window
-  **macOS .app Bundle** - Double-click app with custom icon support
-  **Parallel File Analysis** - Instant singles vs mixes detection
-  **Manage API Keys** - Option 5 to update credentials without editing config
-  **BPM for Unidentified** - Detected BPM shown to help manual tagging
-  **Compact Output** - Clean console display, one line per file for singles
-  **Light Preview** - Preview without temp storage option
-  **Delete Source Files** - Optional cleanup after Apply

### v6.9
- Preview Mode enhancements
- Auto-update check
- Cached audio chunks
- Cross-platform progress display
- macOS auto-close terminal

### v6.8
- MusicBrainz/AcoustID integration
- Enhanced metadata (year, genres, label, ISRC)
- Dual identification system

### v6.7
- Interactive menu system
- Preview & Apply workflow
- Cached processing

### v6.6
- Parallel processing (4 worker threads)
- Batch artwork downloads

---

##  Legal & Credits

**MixSplitR** by KJD

**Services Used:**
- [ACRCloud](https://www.acrcloud.com/) - Audio fingerprinting
- [MusicBrainz](https://musicbrainz.org/) / [AcoustID](https://acoustid.org/) - Fallback identification
- [Last.fm](https://www.last.fm/) - User-generated tags
- [Deezer](https://www.deezer.com/) - BPM and metadata
- [iTunes](https://www.apple.com/itunes/) - Album artwork

**Libraries:** pydub, mutagen, librosa, and other open-source projects

**Intended Use:** Organizing your personal music collection from digitized vinyl, CDs, and recordings you own or have legal access to.

**Legal Disclaimer:**
- For personal, non-commercial use only
- Users are responsible for ensuring they have legal rights to process the audio files
- Respect copyright laws and do not distribute copyrighted material

---

##  Links

- **Releases:** https://gitlab.com/chefd/mixsplitr/-/releases
- **Source Code:** https://gitlab.com/chefkjd/MixSplitR
- **Compiler Guide:** See `Compiler Guide.md` for building from source
- **ACRCloud Console:** https://console.acrcloud.com
- **Last.fm API:** https://www.last.fm/api/account/create

---

**Happy Archiving! **
