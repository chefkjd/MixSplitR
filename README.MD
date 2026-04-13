<img width="3844" height="659" alt="Image" src="https://github.com/user-attachments/assets/7fb4a295-f68e-464c-bf44-301cb4bbbd69" />

# MixSplitR v8.0

### A simple yet powerful app for turning long recordings into clean, tagged tracks

MixSplitR 8.0 is a huge user interface update with many improvements and features added since 7.1

If you have a vinyl rip, cassette transfer, radio recording, DJ mix, live set, CD rip, or just one giant audio file that really should have been separate tracks in the first place, MixSplitR helps you split it up, figure out what songs are there, clean up the track info, and export something that actually fits into a real music library.

It also can be used like a swiss army knife, every tool can be used individually, with the editor designed for bulk editing multiple files.

## [Download](https://github.com/chefkjd/MixSplitR/releases)


## Turn This

<img width="2343" height="1312" alt="Image" src="https://github.com/user-attachments/assets/b59bb431-41ae-4e56-9e68-4fd17959d1fa" />

## Into This!

<img width="2525" height="1375" alt="Image" src="https://github.com/user-attachments/assets/86a5f2ba-b90c-4e86-a916-068bdf2c75fd" />

## What MixSplitR Does

- Splits long recordings into separate tracks
- Helps name tracks and fill in artist, title, album, genre, BPM, and artwork when available
- Lets you fix anything that looks wrong before export
- Can make timestamps and tracklists for mixes when you do not want separate audio files
- Keeps related jobs in one place, including recording, CD ripping, and library cleanup

## What's New in 8.0

MixSplitR 8.0 is a full visual desktop-app overhaul with new features and expansions to existing features.

- The app is now built around a proper desktop interface instead of the older command line menu-heavy flow
- Waveform editing and review stay inside the app instead of bouncing out to a browser
- There is now a dedicated Timestamping mode for building tracklists from long mixes
- CD ripping is built into the app
- Recording is much stronger now, especially on Windows
- Artist cleanup and folder organization are smarter than before
- Releases are packaged more like regular desktop software now, with a macOS DMG and a Windows installer
- 8.0 switches to a default ID mode of MusicBrainz and AcoustID with Shazam instead of ACRcloud to keep accuracy but free identification.

## If You Used 7.1 Already

8.0 keeps the core idea of 7.1, but the experience is much cleaner now.

Recording, session history, track review, preview mode, and multi-source track ID are still here. The big change is that they now live inside a desktop app that feels more like one connected flow instead of a bunch of separate steps. I've worked hard on simplifying the process and eventually aim to simplify it more with user feedback.

## How It Works

1. Load a long recording into `Splitter`.
2. Choose `Split Only`, `Split and Identify`, or `Timestamping`.
3. If you are splitting audio, choose `Full Preview` or `Light Preview`.
4. Let MixSplitR find likely split points, or place them yourself.
5. If you want, run track ID and tag lookup.
6. Review everything in `Editor`.
7. Export finished tracks, or save timestamps and a tracklist instead.

`Recorder` and `CD Ripping` feed into that same flow, and `Session History` lets you go back and review older runs later.

## Great For

- Vinyl and cassette transfers
- Long DJ mixes
- Radio and stream captures
- Live recordings
- CD rips that still need tags and organization
- Existing music folders that need cleanup

## Also Included

- `Recorder` for capturing system audio or app audio
- `CD Ripping` for pulling discs straight into the app
- `Editor` for fixing tags before final export, editing individual files, or for bulk file tag editing
- `Session History` for reviewing, comparing, reorganizing, or rolling back older runs

<img width="2547" height="1358" alt="Image" src="https://github.com/user-attachments/assets/80ef8256-14ec-44f0-8578-14f24ef2a328" />

<img width="2514" height="1362" alt="Image" src="https://github.com/user-attachments/assets/f53cec9c-1e69-4626-b395-3b0e13feffcd" />

## Track ID and Tag Help

MixSplitR can use a few different sources to help identify tracks and fill in missing info.

- `ACRCloud` is still the higher-accuracy option if you want the strongest lookup path
- `AcoustID` plus `MusicBrainz` is a strong free option.
- `Shazam` can help on harder tracks and mixes, also for free
- `iTunes`, `Deezer`, and `Last.fm` can help fill in extra details like genre, tags, and artwork

You do not need every service turned on. Use the ones that fit your needs.

## Smarter Cleanup and Export

- Better artist cleanup with `Off`, `Collab Only`, and `Smart` modes
- Better duplicate handling, including a keep-best-quality option
- Can keep the original file type for simple passthrough cases when possible
- Timestamping mode for mixes where you want a tracklist instead of split files
- Review before export so you can fix things before anything gets written out

<img width="2549" height="1360" alt="Image" src="https://github.com/user-attachments/assets/c61767a3-95a6-4232-82eb-c82655add590" />



<img width="1196" height="776" alt="Image" src="https://github.com/user-attachments/assets/0ea7facf-7425-4cf2-af13-d01d5181a249" />



<img width="2547" height="1368" alt="Image" src="https://github.com/user-attachments/assets/055c80cc-025e-4089-8f03-62e835205750" />

## Supported Formats

### Input

`WAV`, `FLAC`, `MP3`, `M4A`, `OGG`, `AAC`, `WMA`, `AIFF`, `OPUS`

### Output

`FLAC`, `ALAC`, `WAV`, `AIFF`, `MP3`, `AAC`, `OGG Vorbis`, `OPUS`

## A Few Notes

- Windows background capture needs a virtual cable tool such as VB-Cable
    -side note on this, windows 11 seems to have a new bug that can cause silence when switching audio sources, restarting audio services seems to fix this problem, so ive included a button to do that. (It's an inelegant fix for a stupid problem)
- macOS loopback recording still needs BlackHole or something similar
- ACRCloud is optional, not required, but offers the most accurate and expansive ID library.
- AcoustID is recommended if you want a strong free identification setup
- Keep your API keys private
- What you choose to do with this software is on you. Please use responsibly.

## Links

- Releases: <https://github.com/chefkjd/MixSplitR/releases>
- Source: <https://github.com/chefkjd/MixSplitR>
- Issues: <https://github.com/chefkjd/MixSplitR/issues>
- ACRCloud: <https://console.acrcloud.com>
- AcoustID: <https://acoustid.org/api-key>
- Last.fm API: <https://www.last.fm/api/account/create>
