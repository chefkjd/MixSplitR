# MixSplitR API Key Setup


## 1) Do You Need API Keys?

- `Split Only (No ID)`: No keys needed.
- `Auto Tracklist`: Works without keys, but keys improve results.
- `MusicBrainz only`: AcoustID key is recommended.
- `ACRCloud + MusicBrainz`: ACRCloud keys are required.

## 2) Where To Enter Keys In MixSplitR

1. Open MixSplitR.
2. Go to `Settings`.
3. Open `API Key Settings`.
4. Use the menu items to add or update each key.

## 3) ACRCloud Setup

Website: [https://console.acrcloud.com/signup](https://console.acrcloud.com/signup)

1. Create a free account, then verify your email.
2. Sign in to the ACRCloud Console.
3. Open `Audio & Video Recognition`.
4. Click `Create Project`.
5. Set project type to `AVR` (Audio & Video Recognition).
6. Attach/select the `ACRCloud Music` bucket.
7. Choose audio source:
   - `Line-in Audio` for clean digital files.
   - `Recorded Audio` for mic/noisy recordings.
8. Save the project.
9. Open that project and find the credentials section.
10. Copy these 3 values exactly:
   - `Host` (example: `identify-us-west-2.acrcloud.com`)
   - `Access Key`
   - `Access Secret`
11. In MixSplitR, set mode to `ACRCloud + MusicBrainz` (if not already).
12. Go to `Settings` -> `API Key Settings` -> `Add/Update ACRCloud credentials`.
13. Paste all 3 values, save, then run `Test ACRCloud credentials`.

Important:
- Use **project credentials** (`Host/Access Key/Access Secret`), not your account login.
- If keys are missing in the console, the project usually was not fully created/saved yet.

## 4) AcoustID (Recommended for MusicBrainz mode)

Websites:
- [https://acoustid.org/login](https://acoustid.org/login)
- [https://acoustid.org/api-key](https://acoustid.org/api-key)

1. Sign in or create an AcoustID account.
2. Create/copy your API key.
3. In MixSplitR, open `API Key Settings` and choose `Add AcoustID API key`.
4. Paste key and save.
5. Optional: run `Test AcoustID key`.

## 5) Last.fm (Optional but helpful)

Website: [https://www.last.fm/api/account/create](https://www.last.fm/api/account/create)

1. Create an API account/key on Last.fm.
2. In MixSplitR, open `API Key Settings`.
3. Choose `Add/Update Last.fm API key`.
4. Paste the API key and save.

## 6) Quick Troubleshooting

- `ACRCloud invalid key/secret`: Re-copy values from ACRCloud dashboard (no extra spaces).
- `Cannot connect to host`: Check internet and make sure host is exactly correct.
- `ACRCloud keys not visible in console`: Create/save an AVR project first, then open its details page.
- `AcoustID test fails`: Confirm you copied the full API key.
- `Still stuck`: Restart MixSplitR after saving keys, then test again.
