# BeatPub — project spec for Claude Code

BeatPub turns a finished beat (an audio file + a cover photo + a few lines of metadata)
into a 1080p YouTube video **and** a vertical YouTube Short, and publishes both to the
owner's channel with titles, descriptions and tags in the channel's house style. One
command per beat, with one approval step before anything leaves the machine.

Owner: Bruno (producer name **CapryBeats**). Beats are exported from FL Studio as WAV or
MP3 and are named like `Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill` —
title, BPM, key and @handles (the owner's own handle plus co-producers) live in the file
name, and the owner does not want to rename files. Cover photos come straight from a phone
(portrait, EXIF-rotated, JPG or PNG) — handle that.

## Scope

**Phase 1:** render — audio + cover → `video.mp4` (16:9), `beat.mp3` (tagged), `thumb.jpg`,
and `short.mp4` (9:16, the beat's best 30 s).
**Phase 2:** YouTube — learn the channel's existing titles/descriptions/tags, draft new ones
from templates, upload the video (thumbnail + playlist), then upload the Short with a link
to the video, log both.
**Phase 3 (later, NOT now):** BeatStars upload. Do not build it. The only trace of it is the
`beatstars_url` field in `meta.yaml`, which the owner pastes by hand.

YouTube only. No Instagram, no TikTok. Non-goals: GUI, a daemon, stems, multiple channels,
anything cloud-hosted.

## Hard constraints

- Must run on Windows (owner's machine; PowerShell; user folder contains non-ASCII
  characters — use `pathlib`, never `shell=True`, open files with `encoding="utf-8"`) and
  macOS. Python 3.11+. ffmpeg/ffprobe found on PATH; fail with a one-line install hint if
  not.
- Nothing is uploaded without an approved draft (`out/draft.yaml` with `approved: true`)
  unless `--yes` is passed. `--dry-run` must work on every command and print exactly what
  would be sent.
- Secrets (`client_secret.json`, `token.json`) live in the project root, are gitignored,
  and are never printed.
- **Until the YouTube API compliance audit passes, every upload is forced to private by
  YouTube regardless of what we send.** `config.yaml` → `privacy_default: private`.
  Print a reminder when a non-private status is requested while `audit_passed: false`.

## Stack

`typer` (CLI) · `rich` (output) · `Pillow` (frames) · `numpy` (section picker) · `mutagen`
(ID3) · `PyYAML` · `Jinja2` (templates) · `google-api-python-client` +
`google-auth-oauthlib` (YouTube) · `tzdata` (Windows needs it for zoneinfo) · `pytest`.
Optional: `anthropic` for `draft --ai`. Package with `pyproject.toml`, console script
`beatpub`. Use a venv (`.venv/`).

## Repo layout

```
beatpub/
  CLAUDE.md                 this file
  README.md                 install + usage (you write it; keep it short and current)
  pyproject.toml
  config.yaml               owner-editable settings (already filled with the owner's links)
  .gitignore
  beatpub/
    __init__.py
    cli.py                  typer app: new, render, learn, draft, approve, upload, thumbnail, run, auth, doctor, short, stats
    config.py               load/validate config.yaml
    meta.py                 beats/<slug>/meta.yaml + the file-name parser + input discovery
    render.py               16:9 frame, thumbnail, ffmpeg video, mp3 + tags   (port of scripts/render.py)
    short.py                9:16 frame, section picker, ffmpeg short            (port of scripts/short.py)
    drafting.py             titles/descriptions/tags from templates (+ optional AI)
    youtube.py              OAuth, learn, upload, thumbnail, playlist, stats
    log.py                  published.csv
  scripts/
    render.py               TESTED reference implementation of the video step — read it first
    short.py                TESTED reference implementation of the Short step
  templates/
    title.txt               Jinja2 — the channel's title pattern (already in house style)
    description.txt         Jinja2 — the channel's description pattern
    tags.yaml               tag pools copied from the channel's best videos
    short_title.txt         Jinja2
    short_description.txt   Jinja2
    learned/                written by `beatpub learn` (gitignored)
  beats/
    _example/meta.yaml      the schema, as an example
    <slug>/                 one folder per beat:
      <any name>.wav|mp3    input (first *.wav, else *.mp3/*.flac/*.aiff); title/BPM/key/@handles parsed from the name
      <any name>.jpg|png    input (first *.jpg/*.jpeg/*.png/*.webp; HEIC → clear error)
      meta.yaml             input (optional for render; needed for draft — it holds type_beat)
      out/                  everything generated: frame.png thumb.jpg artwork.jpg video.mp4 beat.mp3
                            short.mp4 short_frame.png short_cover.jpg short_pick.json
                            draft.yaml publish.json short_draft.yaml short_publish.json
  docs/
    SETUP_WINDOWS.md        the owner's walkthrough
    YOUTUBE_API_SETUP.md    Google Cloud + audit walkthrough (owner reads this, you don't need to)
    privacy-policy.md, terms.md, evidence/   audit material
  published.csv             append-only log of what went out (videos and shorts)
```

## `meta.yaml` schema

```yaml
title: "Ran Away"                # DEFAULTS to the file name's title (parse_beat_name)
bpm: 142                         # default from the file name
key: "F minor"                   # default from the file name
collabs: ["sinfulyts"]           # default: @handles in the file name minus the owner's own
type_beat: ["Dro Kenji", "Juice WRLD"]   # ordered; title says "Dro Kenji x Juice WRLD Vibes"
similar: ["Shaun Sloan", "Polo G"]       # same-lane artists: tags only; optional
mood: ["melodic"]                # feeds tags; optional
free: true                       # "(FREE)" prefix and free hashtags ("free …" tags stay either way)
beatstars_url: ""                # the beat's own bsta.rs link → "Purchase:" line; empty = no Purchase line
playlist: ""                     # playlist NAME on the channel; falls back to config.playlist_default
publish: ""                      # "", private, unlisted, public, or an ISO datetime to schedule
tags_extra: []                   # appended to generated tags
text_overlay: false              # title/credit on the 16:9 frame
zoom: false                      # slow zoom on the 16:9 video
video_layout: fit                # fit = full height, uncropped (default) | fill = cropped to 16:9
video_margin: 0                  # fraction of the height to leave around the picture (fit only)
short: true                      # false = no Short for this beat
short_start: ""                  # force the section start ("1:04" or 64); empty = loudest bar-aligned window
short_length: 30                 # seconds, rounded to whole bars; ≤ 180
short_layout: fill               # fill | wide | card
short_text: false                # true = title + credit written on the picture
short_wave: false                # true = moving waveform across the lower part
hook_text: ""                    # optional one line under the picture
short_publish: ""                # like publish; empty = config.shorts.publish_offset_hours after the video
```

**Channel defaults.** The owner uses the same artists, moods and tags for every video and
only the beat changes, so `config.yaml` has a `defaults:` block (`type_beat`, `similar`,
`mood`, `free`, `tags_extra`, `playlist`). Resolution order for every meta key: value in
`meta.yaml` if present and non-empty → `config.defaults` → the file name (title, bpm, key,
collabs) → built-in default. `beatpub new` therefore writes a `meta.yaml` that holds only
what it read from the file name, with the channel defaults shown as comments so the owner
sees what will be used and can override one line when a beat is different. A beat folder
with no `meta.yaml` at all is still valid: `beatpub run <slug>` works on file name +
defaults alone.

Validation: `title` (from meta or the file name) and `type_beat` (from meta or
`config.defaults`, ≥1) required for `draft`; `bpm` 40–300; `publish` datetime must be in the future and implies `private` +
`publishAt`. Unknown keys → warning, not error. The file-name convention is
`parse_beat_name()` / `find_inputs()` in `scripts/render.py` — port them into `meta.py`
and keep their unit cases (handles, `142bpm`, `C#min`, underscores, key before BPM, no
info at all). `beatpub new <slug>` writes `meta.yaml` prefilled from the audio file
already in the folder. Credit line everywhere = `"prod. " + " x ".join([artist] + collabs)`;
ID3 TPE1 = the same.

## Commands

```
beatpub new <slug> [--title ...]        create beats/<slug>/meta.yaml, prefilled from the audio file name
beatpub render <slug> [--force]         → out/frame.png thumb.jpg artwork.jpg video.mp4 beat.mp3
beatpub short render <slug> [--force]   → out/short.mp4 short_cover.jpg short_pick.json
beatpub learn [--max 50]                pull own channel's videos → templates/learned/examples.json
beatpub draft <slug> [--ai] [--force]   → out/draft.yaml (title, description, tags, privacy, playlist) + out/short_draft.yaml
beatpub approve <slug>                  opens both drafts in $EDITOR / prints them; sets approved: true
beatpub upload <slug> [--yes] [--dry-run]         video + thumbnail + playlist → out/publish.json
beatpub short upload <slug> [--yes] [--dry-run]   the Short, description carries the video URL → out/short_publish.json
beatpub thumbnail <slug> [--force] [--dry-run]    set the thumbnails of an already-uploaded video and Short again
beatpub run <slug> [--yes] [--ai] [--no-short]    render → short render → draft → (approve unless --yes) → upload → short upload
beatpub auth [--revoke]                 OAuth login; stores token.json
beatpub stats [--days 30]               views/likes/comments per published video and Short → stats.csv
beatpub doctor                          checks ffmpeg, ffprobe, client_secret.json, token, config
```

All commands are idempotent: skip work whose outputs are newer than their inputs unless
`--force`. `upload` / `short upload` refuse to run twice for the same slug if the matching
`publish.json` exists (unless `--force`, which is a new upload, not an update).

## Render spec — video (phase 1)

`scripts/render.py` is tested and correct. Port it into `beatpub/render.py` as functions
(`build_frame`, `make_thumbnail`, `encode_video`, `encode_mp3`, `tag_mp3`, `verify`) and keep
behaviour identical. Keep `scripts/render.py` runnable on its own.

- Frame: 1920×1080, saved as PNG (an RGB source makes ffmpeg produce limited-range
  yuv420p; a JPEG source produced full-range `yuvj420p` on the owner's ffmpeg 8).
  Background = cover scaled to fill, Gaussian blur 40, darkened 45%. Foreground = cover
  scaled UP or DOWN (never `Image.thumbnail`, which only shrinks — a 720×900 phone
  picture must still fill the frame) to the full frame height for a portrait, full width
  for a landscape, centred, no margin (`video_margin` adds one; `video_layout: fill`
  crops to 16:9 instead). `ImageOps.exif_transpose` first (phone photos). Alpha →
  flattened on black. Optional text overlay bottom-left with a dark gradient behind it.
- Thumbnail: the same frame at 1280×720 JPEG, re-saved at lower quality until < 2 MB.
- Artwork for ID3: the cover itself, max 1400 px on the long side, JPEG q90.
- Video (static):
  `ffmpeg -y -loop 1 -framerate 30 -i frame.png -i <audio> -map 0:v -map 1:a
   -c:v libx264 -preset medium -tune stillimage -crf 20 -pix_fmt yuv420p -r 30
   -c:a aac -b:a 384k -ar 48000 -movflags +faststart -t <duration> -shortest video.mp4`
- Video (zoom): render the frame at 3840×2160 and use
  `zoompan=z='1+0.08*on/N':d=N:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,format=yuv420p`
  with N = round(duration × fps). Slower; keep it opt-in.
- MP3: `ffmpeg -i <audio> -vn -c:a libmp3lame -b:a 320k beat.mp3` — unless the source is
  already MP3, in which case copy it unchanged (no second lossy pass; print a one-line
  note that WAV is better). Then ID3v2.3 via mutagen: TIT2 title, TPE1 credit, TBPM,
  TKEY, TDRC year, COMM = beatstars_url if set, APIC = artwork.jpg. Never touch the source.
- Verify with ffprobe: 1920×1080, yuv420p (accept yuvj420p with a printed note), |video
  duration − audio duration| < 0.5 s. Non-zero exit on mismatch.
- Speed note for the README: a 3-minute beat takes ~1–3 min at `-preset medium`;
  `--preset veryfast` is fine for a still image.

## Render spec — Short (phase 1)

`scripts/short.py` is tested. Port it into `beatpub/short.py`; keep it runnable on its own.

- Section: decode to mono 22.05 kHz via ffmpeg pipe, 50 ms RMS frames; candidates start
  on bar lines (`bar = 240/bpm`, from `offset`); window = `short_length` rounded to whole
  bars (30 s @ 142 BPM → 18 bars = 30.42 s); score = mean RMS dB + up to 3 dB bonus when
  the first bar is louder than the bar before (prefers the drop over a loud outro).
  `short_start` forces the start. Without BPM: candidates every 0.5 s, no bonus. Write
  `short_pick.json`.
- Frame 1080×1920 (Pillow), saved as PNG. Layouts (`short_layout`): **fill** (default —
  the photo covers the whole frame, `ImageOps.fit` centred at (0.5, 0.45); dark gradients
  top 0–520 and bottom 1080–1920 only when text is drawn), **wide** (full width uncropped
  on a blurred background), **card** (900×760 card on a blurred background, rounded
  corners, shadow). Text is off by default; `short_text: true` draws the title at y=240
  (84 px, 68 px if > 14 chars) + sub line "prod. {credit} · {bpm} BPM · {key}" and the
  credit at y=1548; `hook_text` draws one line at y=1215 (46 px, wrapped ≤ 900 px). Nothing
  important in the top 220 px, the bottom 320 px or the right 150 px — the Shorts player
  covers them.
- Encode: `-loop 1` frame + `-ss start -t len` audio; `afade` in 0.15 s / out ≤ 0.6 s;
  `format=yuv420p` on the video (explicit RGB → limited-range YUV; never let
  `format=auto` decide); libx264 crf 20, 30 fps, `-g 60 -keyint_min 60 -sc_threshold 0`;
  AAC 128k 48 kHz stereo; `+faststart`. Default = the still picture only. `short_wave:
  true` overlays `showwaves=s=1080x200:mode=cline:rate=30:colors=white@0.85:scale=sqrt`
  at y=1330 (frame converted to rgba first, then `format=yuv420p`).
- Verify: 1080×1920, h264/yuv420p (accept yuvj420p with a note), aac/48000, duration =
  section ± 0.3 s, ≤ 180 s. `short_cover.jpg` = the still.

## YouTube spec (phase 2)

**Auth.** `InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)` with
`run_local_server(port=0)`; persist to `token.json`; refresh silently; on `RefreshError`
tell the owner to run `beatpub auth` again. One scope:
`https://www.googleapis.com/auth/youtube` (covers upload, thumbnails, playlists, list).

**learn.** `channels.list(part=contentDetails, mine=true)` → uploads playlist id →
`playlistItems.list(part=contentDetails, maxResults=50)` (paginate up to `--max`) →
`videos.list(part=snippet,statistics, id=<batch of 50>)`. Save title, description, tags,
publishedAt, viewCount per video to `templates/learned/examples.json`; print a
tag-frequency table and the three most common title shapes, sorted by views. This is the
owner's own channel data; re-run to refresh. The templates already follow the channel's
style (see below); `learn` is for checking and for mining tags of the top videos.

**draft.** Render `templates/title.txt` and `templates/description.txt` (Jinja2) with
meta + config; build tags from `templates/tags.yaml` in this order: `per_artist` for each
`type_beat`, `per_similar` for each `similar`, `per_mood` for each `mood`, `base`,
`tags_extra`; the "free …" tags stay in even when `free` is false (the owner markets every
beat with them; `free` only switches the "(FREE)" title prefix and the free hashtags); dedupe
case-insensitively; then trim to YouTube's limits:
- title ≤ 100 chars (trim the type-beat list first, then the title, never mid-word);
  description ≤ 5000; neither may contain `<` or `>`
- tags: total ≤ 500 chars where commas between tags count and any tag containing a
  space is counted as if wrapped in quotes (that is how YouTube counts) — drop from the
  end of the list until it fits
- `keyword_line` = the final tag list joined with ", " (the "(TAGS)" block in the
  description, as on the channel)
Write `out/draft.yaml`: `title, description, tags, privacy, publish_at, playlist,
category_id, approved: false`. Also write `out/short_draft.yaml` from `short_title.txt`
and `short_description.txt` with `youtube_url` left as a placeholder (`{youtube_url}`)
that `short upload` fills in after the video exists; Short tags = the first 10 of the
video's tags + "shorts". Print both with rich. `--ai`: send meta + the learned examples
(if present) + the template output to the Claude API (official `anthropic` SDK, model and
key from config/env — a pay-as-you-go API key, separate from the owner's claude.ai
subscription) and ask for a title, description and tag list in the same style; still
write `approved: false`. If the key is missing, fall back to the template draft and say so.

**upload.** `videos.insert(part="snippet,status", notifySubscribers=config)` with
resumable `MediaFileUpload(video.mp4, mimetype="video/mp4", chunksize=8 MiB)`; loop on
`next_chunk()`; retry with exponential backoff (max 10) on HttpError 500/502/503/504 and
on socket/httplib2 errors; show a progress bar. Body:

```json
{"snippet": {"title": ..., "description": ..., "tags": [...], "categoryId": "10",
             "defaultLanguage": "en"},
 "status":  {"privacyStatus": "private|unlisted|public", "publishAt": "<RFC3339, only with private>",
             "selfDeclaredMadeForKids": false, "license": "youtube",
             "embeddable": true, "publicStatsViewable": true}}
```

Then wait until `videos.list(part=processingDetails)` reports `processingStatus: succeeded`
(poll every 10 s, at most 10 min — YouTube answers 403 to a thumbnail for a video it is still
processing), then `thumbnails.set(videoId, media image/jpeg)` with `out/thumb.jpg` (a refusal
is retried twice, 20 s apart; after that the owner is told to run `beatpub thumbnail <slug>`), then resolve the
playlist name via `playlists.list(part=snippet, mine=true)` (paginate) and
`playlistItems.insert`. Each follow-up step is best-effort: log the failure, keep the
video. Write `out/publish.json` (`videoId, url, title, privacy, publishAt, uploadedAt,
thumbnail_ok, playlist_ok`) and append a row to `published.csv` (`kind=video`). Print
`https://youtu.be/<id>`.

**short upload.** Same call with `out/short.mp4`; YouTube classifies a vertical video
≤ 3 min as a Short automatically — no "#Shorts" needed in the title (the template puts
`#shorts` in the description anyway). Description = `short_draft.yaml` with
`{youtube_url}` replaced by the video's `https://youtu.be/<id>` (refuse to run before
`publish.json` exists unless `--no-link`). Thumbnail = `short_cover.jpg` (custom
thumbnails on Shorts are accepted by the API; best-effort). Playlist =
`config.shorts.playlist` if set. Privacy/schedule: `short_publish` if set, else the
video's `publishAt` + `config.shorts.publish_offset_hours` (if the video is public now,
the Short is public now too). Write `out/short_publish.json`, log `kind=short`.

**stats.** For every row in `published.csv`: `videos.list(part=statistics,
id=<batch of 50>)` → `viewCount, likeCount, commentCount` → `stats.csv` plus a table
grouped by `kind`, sorted by views. (That's the "which hook / which look works"
comparison; `hook_text` and `short_layout` are logged with each Short for it.)

Quota (default): 100 `videos.insert` per day in their own bucket, 10,000 units/day for
everything else — never a concern at this volume; no quota code needed beyond clear
error messages.

## Templates (already in the channel's house style — don't redesign them)

The owner's best videos look like this; `templates/` reproduces it exactly:

```
title:        (FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes
description:  #typebeat #freebeat #freeforprofittypebeat

              Purchase: https://bsta.rs/nUkiAU        ← only when the beat has a beatstars_url;
                                                        without one the line is left out
              My other Beats: https://www.beatstars.com/capry

              IG: https://www.instagram.com/caprybeats/

              (TAGS)
              dro kenji type beat, dro kenji beats, free dro kenji type beat, juice wrld type beat, …
tags:         the same list (≈ 30 tags, ~490/500 chars)
playlist:     (FREE) Type Beats 2025
audience:     not made for kids
```

`config.yaml` holds the owner-specific parts (store URL, socials, playlist) and the
channel defaults (artists, moods, free). Everything owner-specific comes from
`config.yaml` / `meta.yaml`, never hard-coded.

## Edge cases to handle

Portrait/landscape/square covers; PNG with alpha; HEIC (tell the owner to export JPG —
don't add a HEIC dependency); MP3 source (copy, don't re-encode); 24-bit/48 kHz WAV; mono
(encode stereo anyway); beats over 10 minutes; a Short section longer than the beat; `title`
with quotes/emoji; title that exceeds 100 chars after templating; folder and user names
with spaces and non-ASCII characters; ffmpeg missing; stale token; interrupted upload
(resume); running `upload` twice; `publish` datetime in the past; playlist name not found
(warn, skip); `short upload` before `upload`.

## Acceptance tests (must pass before you call a phase done)

Fixtures: generate them, don't commit media. A 60-second WAV with a quiet 8-bar intro and
a loud part from bar 8 at 142 BPM (build it with numpy: quiet sine melody for 8 bars,
kick + bass from bar 8) and a 1200×1600 PNG drawn with Pillow (portrait, like a phone
photo), in a folder named like the owner's files:
`Fixture Beat 142 Bpm Fm @caprybeats @someone.wav`.

Phase 1: `beatpub new` prefills title "Fixture Beat", 142, "F minor", collabs
["someone"]. `beatpub render` → 1920×1080 yuv420p mp4, duration 60.00 ± 0.1 s,
`+faststart`; thumb.jpg 1280×720 < 2 MB; beat.mp3 320 kbps with TIT2/TPE1/TBPM/TKEY/APIC.
`beatpub short render` picks `start = 8 × 240/142` ± 0.05 s, output 1080×1920, ≤ 180 s,
passes the ffprobe checks; `short_start: "0:47"` and `short_layout: card` work. Second
run without `--force` does nothing. `pytest` covers the file-name parser, meta
validation, title trimming and the tag-length rule (no ffmpeg in unit tests).

Phase 2: `beatpub learn` writes examples.json; `beatpub draft` reproduces the house
format above for the example meta and obeys all limits; `beatpub upload --dry-run` and
`beatpub short upload --dry-run` print the full request bodies and never touch the
network; one real `beatpub run <slug>` of the fixture as **private** shows the video in
YouTube Studio with the custom thumbnail, in the right playlist, and the Short with the
video's link in its description; `published.csv` has both rows; `beatpub stats` prints a
table. (The owner needs that real run for the audit evidence — make it easy to
screen-record.)

## Build order

1. Scaffold: pyproject, package, `doctor`, `config.py`, `meta.py` (incl. the file-name
   parser), `new`, `.gitignore`.
2. `render` by porting `scripts/render.py`; `short render` by porting `scripts/short.py`;
   acceptance test 1.
3. `templates/`, `drafting.py`, `draft`, `approve`; unit tests.
4. `youtube.py`: `auth`, `learn`.
5. `upload`, `short upload` (+thumbnail, playlist, log), `run`, `stats`.
6. README: install (ffmpeg via winget / brew), first-run steps pointing at
   `docs/YOUTUBE_API_SETUP.md`, the per-beat workflow in 5 lines.

Work one step at a time, run it, then move on. Keep functions small, typed, and free of
global state. Ask before adding any dependency not listed above.
