# MixSplitR v6.3.1
### Mix Archival Tool by KJD

MixSplitR helps you organize your digitized vinyl and CD collection. It works with both recorded mixes and individual tracks, intelligently detecting whether your files are mixes or single songs. For mixes, it splits them into individual tracks, then identifies each song using audio fingerprinting, and organizes everything with full metadata and artwork. Use it to split and tag your personal recordings for easy library management.

## Go from this:
<img width="2556" height="1439" alt="Screenshot 2026-01-29 193015" src="https://github.com/user-attachments/assets/5deda5db-578d-4540-95d0-176dd23cd842" />


## To this!
<img width="2559" height="1439" alt="Screenshot 2026-01-29 191743" src="https://github.com/user-attachments/assets/493deb46-e393-4cdf-b8f8-c86b51c5432f" />

DOWNLOAD LATEST RELEASES: https://github.com/chefkjd/MixSplitR/releases/tag/Executables

#########IMPORTANT#########

**For Personal Use Only**: This tool is designed to help you organize and archive your personal music collection from digitized vinyl, CDs, and recordings you own or have legal access to.

**Technical Requirement**: The songs need to have 2 seconds of silence in between them for the tool to recognize them as separate tracks. Fully mixed DJ performances with seamless transitions will not work properly.

---

##  Features

- **Smart Track Detection** - Automatically detects if files are single tracks or mixes (no wasted time splitting individual songs!)
- **Automatic Track Splitting** - Detects silence between tracks to split your mixes
- **Audio Fingerprinting** - Identifies songs using ACRCloud's music recognition API
- **Full Metadata Tagging** - Adds artist, title, and album information to each track
- **Artwork Embedding** - Downloads and embeds high-quality album artwork
- **Lossless FLAC Output** - Exports all tracks in high-quality FLAC format
- **Multi-Format Support** - Works with WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, and OPUS files
- **Multi-File Processing** - Process multiple mix files in one batch
- **Organized Output** - Automatically sorts tracks by artist into folders

---

##  Requirements

- **ACRCloud Account** (Free tier available - see setup instructions below)
- Windows, macOS, or Linux

---

##  Getting Started

### Step 1: Set Up ACRCloud Account

ACRCloud provides the music recognition service that identifies your tracks. Here's how to get your API keys:

1. **Create an Account**
   - Go to [https://console.acrcloud.com/signup](https://console.acrcloud.com/signup)
   - Sign up for a free account
   - Verify your email address

2. **Create a Project**
   - Log in to the [ACRCloud Console](https://console.acrcloud.com)
   - Click **"Create Project"** or **"Audio & Video Recognition"**
   - Choose **"Audio & Video Recognition"** as the project type
   - Give your project a name (e.g., "MixSplitR")
   - Click **Create**

3. **Get Your API Credentials**
   - In your project dashboard, you'll see three important values:
     - **Host** (looks like: `identify-us-west-2.acrcloud.com`)
     - **Access Key** (a long string of letters and numbers)
     - **Access Secret** (another long string)
   - Keep this page open - you'll need these values in Step 3!

4. **Understanding the Free Tier**
   - ACRCloud offers 2,000 free identifications per month
   - This is enough for approximately 30-40 mixes (depending on track count)
   - Perfect for personal use!

### Step 2: Prepare Your Audio Files

1. Place your digitized audio files in the same folder as the MixSplitR executable
   - **Supported Formats**: WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS
   - **Individual tracks**: Any songs under 8 minutes will be automatically recognized as single tracks
   - **Mixes/Recordings**: Longer recordings (8+ minutes) from vinyl, CDs, or other sources will be treated as mixes and split automatically
2. For mixes, make sure there are clear gaps of silence between tracks (at least 2 seconds)
3. For best results, use high-quality source files from your collection

### Step 3: Run MixSplitR

1. **First Run - API Setup**
   - Double-click the MixSplitR executable
   - The program will ask you for your ACRCloud credentials:
     - **Host**: Paste your Host value (e.g., `identify-us-west-2.acrcloud.com`)
     - **Access Key**: Paste your Access Key
     - **Secret Key**: Paste your Access Secret
   - These credentials are saved in `config.json` and you won't need to enter them again

2. **Processing Your Files**
   - The program will automatically detect all supported audio files in the folder
   - **Phase 1 - Smart Splitting**: 
     - Files under 8 minutes are recognized as single tracks (no splitting needed - saves time!)
     - Files 8+ minutes are detected as mixes and split into individual tracks
     - Shows spinner animation during splitting
   - **Phase 2 - Identification & Organization**: 
     - Identifies all tracks using audio fingerprinting
     - Organizes tracks with metadata and artwork
     - Shows progress bar
   - Wait for completion - this can take several minutes depending on the number of tracks

3. **Find Your Music**
   - All processed tracks will be in the `My_Music_Library` folder
   - Tracks are organized by artist name
   - Each track includes:
     - Full metadata (artist, title, album)
     - Embedded album artwork
     - High-quality FLAC audio
   - Unidentified tracks are labeled as `File#_Track_#_Unidentified.flac`

---

##  File Organization

```
Your Folder/
├── MixSplitR.exe           ← The program
├── config.json             ← Your API keys (created on first run)
├── YourMix.wav             ← Your input files (any supported format)
├── AnotherMix.flac
├── Track.mp3
└── My_Music_Library/       ← Output folder (created automatically)
    ├── Artist Name 1/
    │   ├── folder.jpg      ← Album artwork
    │   ├── Artist - Song1.flac
    │   └── Artist - Song2.flac
    ├── Artist Name 2/
    │   ├── folder.jpg
    │   └── Artist - Song3.flac
    └── File1_Track_5_Unidentified.flac
```

---

##  Tips for Best Results

### Smart Processing
- You can process both individual tracks AND mixes in the same batch
- Files under 8 minutes are instantly recognized as single tracks (no splitting)
- Files 8+ minutes are automatically detected as mixes and split accordingly
- Mix individual songs with mixes in the same folder for efficient batch processing
- All supported formats (WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS) can be processed together

### Audio Quality
- Use high-quality source files (320kbps MP3 minimum, or lossless formats)
- Avoid heavily compressed or low-quality recordings
- Ensure clean recordings without excessive noise
- Lossless formats (WAV, FLAC, AIFF) provide the best results

### Track Separation
- Make sure there are clear gaps between tracks (at least 2 seconds of silence)
- The tool uses `-40dB` threshold for silence detection
- If tracks aren't splitting correctly, your mix may have no silence between tracks

### Identification Success
- Popular mainstream tracks are more likely to be identified
- Very obscure, unreleased, or heavily edited tracks may not be recognized
- Live recordings or mashups are harder to identify
- Low-quality recordings reduce identification accuracy

### API Limits
- Free tier: 2,000 identifications/month
- Each track in your mix uses one identification
- Monitor your usage in the ACRCloud console
- Consider upgrading if you process many mixes

---

## 🔧 Troubleshooting

### "No audio files found"
- Make sure your audio files are in a supported format: WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, or OPUS
- Place them in the same folder as MixSplitR.exe
- Check that files aren't corrupted

### "API Error" or "Recognition Failed"
- Check your ACRCloud credentials in `config.json`
- Verify you haven't exceeded your monthly quota
- Check your internet connection
- Make sure the audio quality is sufficient

### Tracks Not Splitting
- Your mix may have no silence between tracks
- Try using audio editing software to add brief gaps between songs
- Consider adjusting the silence detection threshold (requires editing the Python source)

### Wrong Song Identified
- This can happen with similar-sounding tracks or poor audio quality
- You can manually rename/retag the file after processing
- Higher quality source audio improves accuracy

### Program Won't Start (Windows)
- Windows may block the executable - right-click → Properties → Unblock
- Make sure you have the latest Windows updates
- Try running as administrator

---

##  Support

- **Issues/Bugs**: Create a detailed report including:
  - Your operating system
  - Error messages (take screenshots)
  - Steps to reproduce the problem
  
- **ACRCloud Issues**: Contact ACRCloud support at [https://www.acrcloud.com/contact-us/](https://www.acrcloud.com/contact-us/)

---

##  Technical Details

- **Supported Input Formats**: WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS
- **Smart Detection**: Files under 8 minutes are treated as single tracks; 8+ minutes are treated as mixes
- **Audio Output Format**: Exports to FLAC (lossless compression)
- **Silence Detection**: 2-second minimum silence, -40dB threshold (for mixes only)
- **Sample Rate**: Uses 12-second samples from the middle of each track for identification
- **Metadata Sources**: ACRCloud API + iTunes API fallback for artwork
- **Rate Limiting**: 1.2-second delay between API calls to respect ACRCloud limits

---

##  Workflow Summary

1. Place audio files from your personal collection in the MixSplitR folder (supports WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS)
2. Run MixSplitR.exe
3. Enter ACRCloud credentials (first run only)
4. Wait for Phase 1 (smart detection & splitting) and Phase 2 (identification & organization)
5. Find your organized music library in `My_Music_Library` folder
6. Enjoy your perfectly tagged personal music collection!

---

##  Version History

**v6.3.1** - Current version
-added multi-format support (WAV, FLAC, MP3, M4A, OGG, AAC, WMA, AIFF, OPUS)

**v6.3**
- Smart track detection (automatically detects single tracks vs mixes)
- Major speed improvement for individual track processing
- No more wasted time splitting files that don't need it
- Two-phase processing for better efficiency
- Multi-file batch processing
- Improved progress indicators with spinner
- Enhanced user interface
- Better error handling

---

##  Legal & Credits

- **MixSplitR** by KJD
- Uses **ACRCloud** for audio fingerprinting
- Uses **pydub**, **mutagen**, and other open-source libraries

**Intended Use:**
This tool is designed for organizing your personal music collection from digitized vinyl, CDs, and other recordings you own or have legal access to. It helps you archive and manage your music library with proper metadata and organization.

**Legal Disclaimer:**
- For personal, non-commercial use only
- This software is a music organization tool - it does not facilitate or encourage copyright infringement
- Users are responsible for ensuring they have legal rights to process the audio files they use with this tool
- Respect copyright laws and do not distribute copyrighted material obtained using this tool
- Similar to other music organization software (like MusicBrainz Picard, beets, or Audacity), this tool processes files you provide - what you do with it is your responsibility

---

**Happy Archiving!**
