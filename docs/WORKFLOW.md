# BeatPub — the manual

How to get from a beat you just exported to a published YouTube video and Short, and what
to do when something goes differently. Setup (installing, Google Cloud, first login) is in
`SETUP_WINDOWS.md` and `YOUTUBE_API_SETUP.md`; this file starts after that.

All commands are typed in the VS Code terminal, in the `beatpub` folder, with the
environment switched on (the line starts with `(.venv)`). If it does not:

```
.\.venv\Scripts\Activate.ps1
```

Contents

1. [The daily workflow](#1-the-daily-workflow)
2. [The beat folder](#2-the-beat-folder)
3. [Channel defaults and per-beat changes](#3-channel-defaults-and-per-beat-changes)
4. [Scheduling](#4-scheduling)
5. [Every command](#5-every-command)
6. [When the login expires](#6-when-the-login-expires)
7. [When the audit passes](#7-when-the-audit-passes)
8. [When something fails](#8-when-something-fails)

---

## 1. The daily workflow

**1. Export the beat from FL Studio** as WAV (better) or MP3, with its usual name:

```
Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill.wav
```

Do not rename it. The title, BPM, key and co-producers are read from that name.

**2. Make a folder for it** inside `beats\`, for example `beats\tuesday-night`. The folder
name is what you type in every command, so keep it short and without spaces.

**3. Put two files in the folder:** the beat and the cover photo (JPG or PNG, straight from
the phone is fine).

**4. If the beat is on BeatStars already,** add its link (see
[section 3](#3-channel-defaults-and-per-beat-changes)). Without it the description has no
"Purchase:" line.

**5. Run it:**

```
beatpub run tuesday-night
```

This does everything in order and tells you which step it is on:

| Step | What happens |
|---|---|
| render the video | makes the 1080p video, the tagged MP3 and the thumbnail |
| render the Short | picks the best 30 seconds and makes the vertical video |
| draft | writes the title, description and tags in your channel's style |
| approve | shows you both drafts and asks **"Approve these drafts for upload?"** |
| upload the video | uploads, sets the thumbnail, adds it to the playlist |
| upload the Short | uploads the Short with the link to the video in its description |

**6. At the approve question,** read the title, description and tags. Type `y` and Enter to
upload. Type `n` to stop — nothing has been sent. You can then change the drafts
(`beats\tuesday-night\out\draft.yaml` and `short_draft.yaml`, plain text files) and run
`beatpub run tuesday-night` again; it continues where it stopped and does not render twice.

**7. Check both in YouTube Studio.** The links are printed at the end.

That is the whole routine. Want to look before anything is made or sent?
`beatpub run tuesday-night --dry-run` goes through every step and only prints.

---

## 2. The beat folder

### What you put in

| File | Rules |
|---|---|
| The beat | WAV, MP3, FLAC or AIFF. If there are several, a WAV is taken first. |
| The cover | JPG, PNG or WEBP. Portrait, landscape or square all work. iPhone HEIC photos do not — export as JPG first. |
| `meta.yaml` | Optional. Only for a beat that differs from your usual settings. |

### What the file name tells BeatPub

`Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill`

| Part | Becomes |
|---|---|
| `Tuesday Night` | the title |
| `160 Bpm` (also `160bpm`) | the BPM |
| `A#m` (also `C#min`, `Fm`, `F minor`) | the key |
| `@sinfulyts @1parkerhill` | the co-producers. Your own handle is left out. |

The credit everywhere is then "prod. CapryBeats x sinfulyts x 1parkerhill".
If a name has no BPM or key, everything still works; they are just left out.

### What appears in `out\`

| File | What it is |
|---|---|
| `video.mp4` | the YouTube video, 1920×1080 |
| `thumb.jpg` | the video's thumbnail |
| `beat.mp3` | the beat as a 320 kbps MP3 with title, credit, BPM, key and cover inside. For sending to artists. |
| `short.mp4` | the Short, 1080×1920 |
| `short_cover.jpg` | the Short's picture (its thumbnail) |
| `draft.yaml` | the video's title, description, tags, privacy and playlist. You may edit this. |
| `short_draft.yaml` | the same for the Short. You may edit this. |
| `publish.json` | written after the video is uploaded: its link and what worked |
| `short_publish.json` | the same for the Short |
| `short_pick.json` | which part of the beat was chosen for the Short |
| `frame.png`, `short_frame.png`, `artwork.jpg` | the pictures the videos and MP3 were made from |
| `render_state.json`, `draft_state.json` | BeatPub's own notes on what is up to date. Leave them alone. |

Your beat and cover are never changed. You can delete the whole `out\` folder to start a
beat over — **but not after uploading**: `publish.json` is how BeatPub knows the beat is
already on YouTube, and without it the next run uploads a second copy.

In the main folder, `published.csv` lists everything that was uploaded (opens in Excel).

---

## 3. Channel defaults and per-beat changes

Almost everything is the same for every beat, so it lives in one place: **`config.yaml`**
in the main folder.

```yaml
defaults:
  type_beat: ["Dro Kenji", "Juice WRLD"]           # the artists in the title
  similar: ["Shaun Sloan", "Polo G", "Lil Durk"]   # more artists, for tags only
  mood: ["melodic"]                                # "melodic type beat" tags
  free: true                                       # (FREE) in the title
```

Change a line there and every future beat follows it. Also in `config.yaml`: your store
link ("My other Beats:"), the IG line, the playlist name (`playlist_default` — change it
when you start a 2026 playlist) and how many hours after the video the Short goes public.

**When one beat is different,** it gets its own `meta.yaml` in its folder. The easy way:

```
beatpub new tuesday-night
```

This writes `beats\tuesday-night\meta.yaml` with every possible setting listed, switched
off with a `#` in front. To use one, delete the `# ` at the start of that line and change
the value. What is in `meta.yaml` wins over `config.yaml`; what is not there comes from
`config.yaml`; title, BPM, key and co-producers come from the file name.

### Example: the beat's BeatStars link

```yaml
beatstars_url: "https://bsta.rs/nUkiAU"
```

The description then has "Purchase: https://bsta.rs/nUkiAU" above "My other Beats:". With
no link there is no Purchase line at all.

### Example: a beat that is not free, in a different style

```yaml
free: false
type_beat: ["Lil Tecca"]
beatstars_url: "https://bsta.rs/abc123"
```

| | Usual beat | This beat |
|---|---|---|
| Title | (FREE) Tuesday Night Type Beat \| Dro Kenji x Juice WRLD Vibes | Tuesday Night Type Beat \| Lil Tecca Vibes |
| Hashtags | #typebeat #freebeat #freeforprofittypebeat | #typebeat #beats #lilteccatypebeat |
| Short title | Tuesday Night - Dro Kenji x Juice WRLD Type Beat (FREE) | Tuesday Night - Lil Tecca Type Beat |

The "free …" search tags stay in either way — that is how the channel is found.

### The settings you can put in `meta.yaml`

| Setting | What it does |
|---|---|
| `title`, `bpm`, `key`, `collabs` | Only if the file name is wrong or unusual. |
| `type_beat` | Artists in the title, in order. |
| `similar` | Similar artists; tags only. |
| `mood` | Mood words; tags only. |
| `free` | `false` removes (FREE) and the free hashtags. |
| `beatstars_url` | This beat's link → the "Purchase:" line. |
| `playlist` | A different playlist, by its exact name on the channel. |
| `publish` | When and how the video goes out, instead of the next free slot — see [Scheduling](#4-scheduling). |
| `tags_extra` | Extra tags for this beat, added to the usual ones. |
| `text_overlay` | `true` writes the title and credit on the video picture. |
| `zoom` | `true` adds a slow zoom to the video (rendering takes longer). |
| `video_layout` | `fit` shows the whole photo on a blurred background (usual); `fill` crops it to fill the screen. |
| `video_margin` | Space around the photo, e.g. `0.05`. |
| `short` | `false` = no Short for this beat. |
| `short_start` | Start the Short at a spot you choose, e.g. `"1:04"`. Empty = the loudest part, starting on a bar. |
| `short_length` | Length in seconds (usual: 30). Rounded to whole bars. |
| `short_layout` | `fill` (photo covers the screen), `wide` (whole photo, blurred background), `card` (photo as a card). |
| `short_text` | `true` writes the title and credit on the Short. |
| `short_wave` | `true` adds a moving waveform. |
| `hook_text` | One line of text under the picture. |
| `short_publish` | Like `publish`, for the Short only. |

After changing `meta.yaml` or `config.yaml`, just run `beatpub run <folder>` again. Only
what the change affects is made again.

---

## 4. Scheduling

### The chain: every beat gets the next free day

`config.yaml` has a `schedule:` block:

```yaml
schedule:
  enabled: true
  time: "18:00"        # local time
  every_days: 1
```

While `enabled` is `true`, you do not write dates at all. When the draft is written, the
beat gets **the next free slot**: the day after the latest date already taken, and never
earlier than tomorrow, at 18:00 your computer's time. Three beats done on one evening go
out tomorrow, the day after, and the day after that.

- **"Already taken"** means: the date of every *approved* draft, and of every video in
  `published.csv`. A draft you have not approved yet does not hold its day, so approve one
  beat before you draft the next. `beatpub run` does it in that order by itself.
- `every_days: 2` leaves a day between videos, `7` makes it weekly.
- The slot is shown in the draft and again in the approve step:
  `slot      Fri 20 Nov 2026, 18:00  (next free slot in the schedule chain)`.
- `enabled: false` switches the chain off; beats then follow `privacy_default`.

### `beatpub queue` — what goes out when

```
beatpub queue
```

Lists every beat folder with its date, time and how far it is, sorted by date:

| Status | Meaning |
|---|---|
| `new` | only the beat and cover are in the folder |
| `rendered` | the video is made |
| `drafted` | title, description and tags are written, not approved yet |
| `approved` | ready to upload |
| `uploaded` | on YouTube |

The last column points out what breaks the chain from today on: **duplicate** (two beats on
the same day), **gap** (a day without a video) and **too close** (less than `every_days`
apart). To move a beat to the next free slot: `beatpub draft <folder> --force`, then approve
it again. The approve step also warns when the day already has a video.

### One beat at a different time

A `publish:` line in the beat's `meta.yaml` always wins over the chain:

| Line | Result |
|---|---|
| *(no line)* | the next free slot of the chain; with the chain off, whatever `privacy_default` in `config.yaml` says (now: private) |
| `publish: private` | private, no date |
| `publish: unlisted` | unlisted |
| `publish: public` | public right away |
| `publish: "2026-11-20T18:00:00"` | uploaded as private now, goes public on 20 November 2026 at 18:00 |

The date is written year-month-day, then `T`, then the time in 24-hour form. It is your
computer's local time. It must be in the future, otherwise the draft is refused. A date
you set by hand counts as taken for the chain once that draft is approved.

**The Short follows the video.** If the video is scheduled, the Short goes public 2 hours
later (`publish_offset_hours` in `config.yaml`; `0` = at the same moment). If the video is
public right away, so is the Short. To give the Short its own time:
`short_publish: "2026-11-21T12:00:00"`.

The schedule is fixed when the draft is written, and you see it in the approve step
("private, goes public …"). If you change `publish:` afterwards, or a draft sat unapproved
until its slot passed, run `beatpub draft <folder> --force` to write the drafts again.

> **Until the audit passes, none of this takes effect.** YouTube keeps every upload from
> BeatPub private and drops the schedule, whatever was asked. BeatPub reminds you when
> that applies. See [section 7](#7-when-the-audit-passes).

---

## 5. Every command

`<folder>` is the beat's folder name under `beats\`. Add `--help` to any command to see its
options, e.g. `beatpub upload --help`.

Three options mean the same everywhere:

- `--dry-run` — show what would happen; make nothing, send nothing.
- `--force` — do it again even though it was done already.
- `--yes` (or `-y`) — do not stop to ask.

### `beatpub run <folder>` — everything for one beat

Render, Short, draft, approve, upload, Short upload. Steps already done are skipped, so it
is safe to run again after a stop or a failure.

| Option | What it does |
|---|---|
| `--yes` | Uploads without the approve question. |
| `--no-short` | Skips the Short this time. |
| `--preset veryfast` | Renders faster. The picture is still, so quality is the same in practice. |
| `--ai` | Lets Claude polish the draft (needs extra setup; see the README). |
| `--dry-run` | Goes through every step and prints what would be made and sent. |

```
beatpub run tuesday-night
beatpub run tuesday-night --preset veryfast
beatpub run tuesday-night --dry-run
```

### `beatpub new <folder>` — make a `meta.yaml`

Creates the folder if needed and writes `meta.yaml` with what it read from the file name and
all other settings switched off. Only needed when a beat is different from usual.

| Option | What it does |
|---|---|
| `--title "Other Name"` | Use this title instead of the one in the file name. |
| `--force` | Overwrite an existing `meta.yaml`. |

### `beatpub render <folder>` — the video, MP3 and thumbnail

| Option | What it does |
|---|---|
| `--preset veryfast` | Faster rendering. Usual is `medium`: about 1–3 minutes for a 3-minute beat. |
| `--force` | Render again even if nothing changed. |
| `--dry-run` | Print the steps only. |

### `beatpub short render <folder>` — the Short

Same options as `render`. Prints which part of the beat it picked.

```
beatpub short render tuesday-night --force
```

### `beatpub draft <folder>` — title, description, tags

Writes `out\draft.yaml` and `out\short_draft.yaml` and shows them, with the character counts
YouTube limits (title 100, tags 500).

| Option | What it does |
|---|---|
| `--force` | Write the drafts again. **This throws away your edits and the approval.** |
| `--ai` | Let Claude polish the video's draft. |
| `--dry-run` | Show the drafts without writing them. |

Without `--force`, a draft you edited or approved is never overwritten.

### `beatpub approve <folder>` — check and approve the drafts

Shows both drafts, checks them against YouTube's limits and asks. Nothing uploads before
this (unless you pass `--yes` to an upload).

| Option | What it does |
|---|---|
| `--yes` | Approve without the question. |
| `--no-edit` | Do not open the drafts in an editor first. |

To have the drafts open in an editor before the question, set one once per terminal:
`$env:EDITOR = "notepad"` (or `"code --wait"` for VS Code). Otherwise open the two files
yourself, save, then run `approve`.

### `beatpub upload <folder>` — upload the video

Uploads `video.mp4` with the approved draft, waits until YouTube has processed it (at
most 10 minutes), sets the thumbnail, adds it to the playlist, writes `publish.json` and a
line in `published.csv`, prints the link.

| Option | What it does |
|---|---|
| `--yes` | Upload even though the draft is not approved. |
| `--dry-run` | Print exactly what would be sent. |
| `--force` | Upload again. **This makes a second video on the channel**, it does not replace the first. |

A beat uploads once. A second `upload` just says "already uploaded" with the link.

### `beatpub short upload <folder>` — upload the Short

Same options as `upload`, plus:

| Option | What it does |
|---|---|
| `--no-link` | Upload without the "Full beat:" link (for a Short whose video is not on YouTube). |

The video has to be uploaded first, because the Short's description links to it.

### `beatpub thumbnail <folder>` — set the thumbnails again

For a beat that is already uploaded but whose thumbnail did not go through. Sets
`thumb.jpg` on the video and `short_cover.jpg` on the Short. Uploads no video. Thumbnails
that were set fine are left alone.

| Option | What it does |
|---|---|
| `--force` | Set them again even if they were set (e.g. after you changed the cover and rendered again). |
| `--dry-run` | Print what would be sent. |

```
beatpub thumbnail tuesday-night
```

### `beatpub queue` — what goes out when

Every beat folder with its publish date, time and status, sorted by date, with duplicates
and gaps pointed out. It only reads files on your computer. See [Scheduling](#4-scheduling).

### `beatpub stats` — how the uploads are doing

Views, likes and comments of everything BeatPub uploaded: one table for videos, one for
Shorts, best first. Also saved as `stats.csv`. The numbers are totals since upload.

| Option | What it does |
|---|---|
| `--days 90` | Only uploads from the last 90 days. Usual: 30. `--days 0` = everything. |
| `--dry-run` | Print the request only. |

### `beatpub learn` — look at your own channel

Reads your newest videos (titles, descriptions, tags, views) and shows which title
patterns and tags your best videos use. It only reads; nothing on the channel changes.

| Option | What it does |
|---|---|
| `--max 100` | How many videos to read. Usual: 50. |
| `--dry-run` | Print the requests only. |

### `beatpub auth` — the YouTube login

Opens the browser once so you can allow BeatPub to use your channel.

| Option | What it does |
|---|---|
| `--force` | Log in again although a login is saved (e.g. to pick another account). |
| `--revoke` | Log out: withdraws the permission at Google and deletes the saved login. |
| `--dry-run` | Say what would happen. |

### `beatpub doctor` — is everything in place?

Checks Python, ffmpeg, `config.yaml`, the Google file and the login, one line each.
The first thing to run when something seems off.

---

## 6. When the login expires

You will see a message like:

```
error: the saved YouTube login has expired or was revoked — run `beatpub auth` again
```

Do what it says:

```
beatpub auth
```

The browser opens, you pick the channel's account and allow access. Then run the command
that failed again — it continues where it stopped.

**Why it happens weekly:** while the Google project is in "Testing" mode, Google ends every
login after 7 days. That stops when the project is published, which is part of the audit
(`YOUTUBE_API_SETUP.md`).

Nothing is lost when a login expires in the middle of `run`: the rendered files and the
approved drafts stay, and a beat that was already uploaded is not uploaded again.

---

## 7. When the audit passes

Until then YouTube locks every BeatPub upload to private. When Google's email says the
audit is approved, change two lines in `config.yaml`:

```yaml
privacy_default: public
audit_passed: true
```

From then on new uploads are public (or follow `publish:` and go out on schedule), and
the reminder disappears.

Two things do not change by themselves:

- **Videos uploaded before the audit stay private for good.** YouTube locks them, so they
  cannot be set to public in YouTube Studio either. They have to be uploaded again:

  ```
  beatpub draft tuesday-night --force            # writes the drafts again, now public
  beatpub approve tuesday-night
  beatpub upload tuesday-night --force           # a new video
  beatpub short upload tuesday-night --force     # a new Short, linking to the new video
  ```

  Then delete the old, locked video and Short in YouTube Studio.
- **Drafts written before the change still say private.** For a beat that is drafted but
  not uploaded yet, run `beatpub draft <folder> --force` and approve again.

If you would rather check each video before it is seen, leave `privacy_default: private`
and use `publish: public` or a date only on the beats you want out.

---

## 8. When something fails

BeatPub stops with one red line that starts with `error:` and says what to do. After fixing
it, run the same command again; finished steps are skipped. The common ones:

| Message | What to do |
|---|---|
| `ffmpeg not found` | Install it (`winget install -e --id Gyan.FFmpeg`), close and reopen VS Code. |
| `beat folder not found` | The name after the command must be the folder name under `beats\`, exactly. |
| `no audio file` / `no cover photo` | Put the beat / a JPG or PNG in the folder. |
| mentions `HEIC` | The cover is an iPhone HEIC file. Export it as JPG and use that. |
| `… is not approved` | Run `beatpub approve <folder>`. |
| `… is in the past` | The `publish:` date or the beat's slot has passed. Change the date if you set one, then `beatpub draft <folder> --force`. |
| `the title is … characters` | The title cannot fit in 100 characters. Shorten the title or the artists in `meta.yaml`. |
| `login has expired` | `beatpub auth` — [section 6](#6-when-the-login-expires). |
| `quota is used up` | YouTube's daily limit. Try again tomorrow (it resets at midnight Pacific time). |
| `could not reach YouTube` | Internet problem. Run the command again. |
| `the video has not been uploaded yet` | Run `beatpub upload <folder>` before `beatpub short upload <folder>`. |

### The upload stopped halfway

Short connection drops are retried automatically. If it gives up, or you closed the
terminal, **nothing was published** — run the command again and the upload starts over.

### "thumbnail not set"

The video is uploaded and fine; only the picture is missing. BeatPub tells you so and
carries on.

YouTube refuses a thumbnail (**403 forbidden**) while it is still processing a fresh
upload. BeatPub therefore waits after each upload until YouTube says the video is processed
(it checks every 10 seconds, for at most 10 minutes), and if the thumbnail is still refused
it tries twice more, 20 seconds apart. Only then do you see "thumbnail not set".

- Wait a few minutes, then:

  ```
  beatpub thumbnail tuesday-night
  ```

- If it keeps refusing, check that the channel is phone-verified at
  <https://www.youtube.com/verify>, or set the picture by hand in YouTube Studio:
  `out\thumb.jpg` for the video, `out\short_cover.jpg` for the Short.

### "not added to a playlist"

The video is uploaded; BeatPub found no playlist with that name. The name in `config.yaml`
(`playlist_default`) must match the playlist on the channel exactly. Add the video to the
playlist by hand in YouTube Studio this once, and fix the name for next time.

### The title, description or tags are wrong after uploading

Fix them in YouTube Studio. BeatPub does not change videos that are already uploaded.

### The Short picked the wrong part

Put `short_start: "1:04"` (your spot) in the beat's `meta.yaml` and run
`beatpub short render <folder>` again — before uploading.

### You want to redo a beat that is already on YouTube

Delete the video in YouTube Studio, then run `beatpub upload <folder> --force` (and
`beatpub short upload <folder> --force`). `--force` always makes a new video.

### Nothing here fits

Run `beatpub doctor` and read its lines. Then copy the red `error:` line and ask Claude
Code in this folder what it means.
