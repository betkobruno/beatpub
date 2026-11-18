# BeatPub on Windows — the slow walkthrough

Everything below happens in one folder on your Desktop, with VS Code open on it and
Claude Code working inside it. Read a part, do it, come back. Nothing here needs you
to understand Python; you type commands into one terminal and look at what comes out.

**What you'll end up with**

```
Desktop\beatpub\
  CLAUDE.md                 the build spec Claude Code works from (you don't edit it)
  README.md                 short usage notes
  config.yaml               your settings (producer name, links, playlist) — already filled
  requirements-render.txt   the Python packages the scripts need
  client_secret.json        ← you add this in Part 5 (Google); never share it
  scripts\render.py         beat + photo → YouTube video, MP3, thumbnail   (works tonight)
  scripts\short.py          beat + photo → 30-second vertical Short         (works tonight)
  templates\                title / description / tags — already in your house style
  beats\_example\meta.yaml  the per-beat settings file, as an example
  beats\<one folder per beat>\   the beat with its normal name, the cover, meta.yaml, out\
  docs\                     this file, the Google guide, evidence images
```

Files sent separately in the chat (`CLAUDE.md`, the guides) are the same files that are
inside the zip — nothing to copy anywhere.

---

## Part 1 — The folder on the Desktop and VS Code on it (10 min)

1. **Download `beatpub-starter.zip`** from the chat. It lands in `Downloads`.
2. **Extract it onto the Desktop.** In File Explorer, right-click the zip → *Extract All…*.
   The dialog proposes `C:\Users\<you>\Downloads\beatpub-starter`. Click *Browse…*, pick
   **Desktop**, *Select Folder*, then *Extract*. The zip already contains a folder named
   `beatpub`, so you now have `Desktop\beatpub`.
   Check: open `Desktop\beatpub` — you should see `CLAUDE.md` directly inside it. If
   instead you see another `beatpub` folder inside, you extracted one level too deep;
   drag the inner `beatpub` onto the Desktop and delete the empty shell.
3. **Open the folder in VS Code.** Start VS Code → *File → Open Folder…* →
   `Desktop\beatpub` → *Select Folder*. If it asks "Do you trust the authors of the files
   in this folder?" → *Yes, I trust the authors*. The left panel (Explorer) now shows the
   tree above.
4. **Open the terminal.** Menu *Terminal → New Terminal* (or Ctrl + ` — the key under
   Esc). A panel appears at the bottom with a line like
   `PS C:\Users\<you>\Desktop\beatpub>` — that's PowerShell, already "standing" in the
   project folder. Every command in this guide is typed there, followed by Enter.
   (If the panel says "cmd" or "Git Bash" instead of "powershell", use the ˅ arrow next
   to the + icon and pick PowerShell, just to match this guide.)

---

## Part 2 — One-time tools: Python, ffmpeg, the packages (15 min)

5. **Python.** Type:
   ```
   python --version
   ```
   - If it prints `Python 3.11.x` or `3.12.x` or `3.13.x` → done, go to step 6.
   - If the Microsoft Store opens, or it says *not recognized*: install Python with
     ```
     winget install -e --id Python.Python.3.12
     ```
     (`winget` is Windows' built-in installer; the first time it may ask you to accept
     the source agreement — type `Y`.) When it finishes, close the terminal (the trash
     icon on the right of the terminal panel) and open a new one (step 4), then run
     `python --version` again.
   - If the Store *still* opens: Windows has a decoy `python` that points to the Store.
     Settings → Apps → Advanced app settings → *App execution aliases* → switch off
     both "python" entries. New terminal, check again.
6. **ffmpeg** — the program that actually makes the videos.
   ```
   winget install -e --id Gyan.FFmpeg
   ```
   Accept with `Y` if asked. When it's done, **close VS Code completely and reopen it**
   (File → Open Recent → beatpub). Programs only see new PATH entries when they start
   fresh, and VS Code's terminal inherits VS Code's environment. Then:
   ```
   ffmpeg -version
   ```
   should print `ffmpeg version 7.x …`. If it says *not recognized* even after a full
   restart, sign out of Windows and back in once.
7. **The Python packages** (image handling, MP3 tags, the Short's section picker):
   ```
   python -m pip install -r requirements-render.txt
   ```
   A few lines of "Successfully installed pillow mutagen numpy tzdata" at the end
   is what you want. Warnings about pip being out of date can be ignored.

That's the whole toolchain. You never repeat Part 2.

---

## Part 3 — First render, tonight (10 min + render time)

8. **Make the beat's folder.** In VS Code's Explorer, right-click the `beats` folder →
   *New Folder…* → name it in lowercase with no spaces, e.g. `midnight`. This short name
   is the "slug" every command refers to.
9. **Put the two inputs in it.** Export the beat from FL Studio (WAV is best — the master,
   no tag voice — but an MP3 works too) and copy it into `beats\tuesday-night\` **with its
   normal name**, e.g. `Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill.mp3`.
   The scripts read the title, BPM, key and the @handles from that name, so you never
   rename anything. Copy the cover photo in too (JPG or PNG, any name). You can drag files
   from File Explorer straight onto the folder in VS Code's Explorer.
   - iPhone photos often arrive as `.HEIC`. Easiest fix: send the photo to yourself via
     WhatsApp / Telegram / email — those convert to JPG — or set iPhone *Settings →
     Camera → Formats → Most Compatible* before shooting covers. Portrait is fine; the
     script fills the sides with a blurred version of the same photo.
   - Optional but worth the minute: copy `beats\_example\meta.yaml` into the folder and
     fill in the type-beat artists. The scripts don't read it yet; the finished tool will.
10. **Render.** One line, then Enter:
    ```
    python scripts\render.py --beat beats\tuesday-night --text --zoom --preset veryfast
    ```
    `--beat` points at the beat's folder: the script takes the first audio file and the
    first image it finds there, reads title / BPM / key / @handles from the audio file's
    name, and writes everything into `beats\tuesday-night\out\`. The first line it prints
    is what it understood — `beat: "Tuesday Night" 160 BPM A# minor with sinfulyts,
    1parkerhill (from the file name)` — check it once; if the name was unusual, add
    `--title "…" --bpm 160 --key "A# minor"` and those win.
    The three options: `--text` prints the title and "prod. CapryBeats x sinfulyts x
    1parkerhill · 160 BPM · A# minor" on the picture · `--zoom` slow zoom instead of a
    still · `--preset veryfast` renders several times faster (no visible difference on a
    still image). Leave any of them out and you get the plain version.
    What you'll see: the `beat:` line, `audio: … 183.42 s`, the ffmpeg command it runs
    (lines starting with `$`), a pause while it encodes — a 3-minute beat takes about a
    minute with `--zoom --preset veryfast`, 10–20 s without `--zoom` — and at the end:
    ```
    check: 1920x1080 yuv420p  video 183.42 s vs audio 183.42 s  size 24.1 MB  → OK
    mp3: beats\tuesday-night\out\beat.mp3  7.0 MB  (tags: title/artist/bpm/key/artwork)
    ```
    `→ OK` is the line that matters.
11. **Look at it.** In the Explorer open `beats\tuesday-night\out`: click `thumb.jpg` to
    see the picture; right-click `video.mp4` → *Reveal in File Explorer* → double-click to
    play it. Four files: `video.mp4` (1920×1080, for YouTube), `beat.mp3` (320 kbps,
    tagged "Tuesday Night" / "CapryBeats x sinfulyts x 1parkerhill" / 160 / A# minor,
    cover embedded — if the source was already an MP3 it is copied, not re-encoded),
    `thumb.jpg` (1280×720, the YouTube thumbnail), `frame.png`.
12. **The Short, same folder:**
    ```
    python scripts\short.py --beat beats\midnight --preset veryfast
    ```
    It prints which section it chose, e.g. `section: 75.00 s → 105.00 s (30.00 s, 20 bars,
    auto, score …)`, renders `short.mp4` (1080×1920) and `short_cover.jpg` into the same
    `out\` folder, and ends with `→ OK`. Default look: the photo fills the whole frame,
    the waveform moves across the lower part, nothing written on it.
    - a different part of the beat: `--start 1:04` · shorter or longer: `--length 15` (up to 180)
    - title + "prod." credit on the picture: `--text` · a line under it: `--hook "…"`
    - no waveform: `--no-wave` · photo uncropped at full width: `--layout wide`

From here on the scripts are the "engine"; Claude Code wraps them into the one-command
tool.

---

## Part 4 — Let Claude Code build the tool (an evening or two, in steps)

The scripts are the engine; Claude Code wraps them into one command (`beatpub …`) that
also knows your file-name convention, drafts the YouTube text in your house style and
uploads the video and the Short.

13. **Check `config.yaml`** (VS Code, click the file). It is already filled from your
    channel: store link `https://www.beatstars.com/capry`, the IG line, playlist
    `(FREE) Type Beats 2025`. Change the playlist name if you start a 2026 one. Leave
    `privacy_default: private` and `audit_passed: false` for now.
    **The templates** (`templates\title.txt`, `description.txt`, `tags.yaml`) already
    reproduce your best videos — "(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD
    Vibes", the hashtag line, Purchase / My other Beats / IG, the (TAGS) block and the
    same ~30 tags. Per beat, `meta.yaml` supplies the artists: `type_beat` (the ones in
    the title), `similar` (same-lane artists, tags only), `mood`, `free`, and the beat's
    own `beatstars_url`. The "Purchase:" line appears only when a beat has that link;
    without it the description goes straight to "My other Beats".
14. **Open the Claude Code panel.** Click the Claude icon in VS Code's left bar, or
    Ctrl+Shift+P → type `Claude Code` → *Claude Code: Open in New Tab*. Sign in with your
    claude.ai account if it asks. The panel works on the folder you opened, so it already
    sees `CLAUDE.md` — that file is its instructions, including the file-name convention.
15. **Message 1, exactly:**
    > Read CLAUDE.md and do build-order step 1. Then stop and show me what you created
    > and how I test it.
    Expect: `pyproject.toml`, a `beatpub\` package, a `.venv` folder (its own Python for
    the tool — leave it alone), and the commands `beatpub doctor` and `beatpub new`. It
    tells you how to run them; typically `.venv\Scripts\activate` once per terminal, then
    `beatpub doctor` → ffmpeg OK, ffprobe OK, config OK, `client_secret.json` missing
    (expected until Part 5). Then `beatpub new midnight` → it finds the audio already in
    the folder and writes `beats\midnight\meta.yaml` prefilled with "Tuesday Night",
    160, "A# minor", collabs. Open that file and add what the file name can't tell it:
    `type_beat: ["YFN Lucci"]`, `similar: [...]`, `mood: [...]`, `free: true`, and the
    `beatstars_url` once the beat is on BeatStars.
16. **Message 2: "Step 2."** → `beatpub render midnight` and `beatpub short render
    midnight` must produce the same files the scripts did. Test both; compare with Part 3.
17. **Message 3: "Step 3."** → `beatpub draft midnight` prints the title, description
    and tags for the video and for the Short, built from `templates\` + `config.yaml` +
    `meta.yaml`; `beatpub approve midnight` marks them approved. Compare with one of your
    real videos — it should look identical apart from the artists. Anything you'd change,
    change in the template files (plain text) rather than per beat.
18. **Pause for Part 5A (Google, 30 min)** — step 4 needs `client_secret.json` in the
    folder.
19. **Message 4: "Step 4."** → `beatpub auth` opens the browser, you approve BeatPub on
    the channel's Google account ("unverified app" warning → Advanced → continue);
    `beatpub learn` reads your channel's existing titles/descriptions/tags and prints the
    tag table — that's the moment to adjust the templates to match what already works.
20. **Message 5: "Step 5."** → `beatpub upload midnight --dry-run` shows the exact request;
    then `beatpub upload midnight` for real, then `beatpub short upload midnight`. The
    video appears in YouTube Studio as **private** with the thumbnail, in the playlist;
    the Short next to it with the video's link in its description. Screenshot the
    terminal and Studio — that's audit evidence (Part 5C). Then `beatpub run midnight`
    end to end on a second beat.
21. **Message 6: "Step 6."** → README. Then do Parts 5C and 5D (the audit form).
22. **Working with the panel:** permission prompts ("Allow Claude to run…?") → Yes; if it
    wanders off the spec, say "stick to CLAUDE.md"; if it errors, paste the error and say
    what you were doing. Two Windows things, both normal: *"running scripts is disabled on
    this system"* → run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once in your
    terminal, answer Y, tell Claude to retry; it may ask for Git for Windows
    (git-scm.com, defaults, restart VS Code) — it uses Git to keep versions of its work.

---

## Part 5 — Google / YouTube (30 min now, the rest after build step 5)

Full detail is in `docs\YOUTUBE_API_SETUP.md`; this is the slow version of Part A.

A1. Go to https://console.cloud.google.com and sign in with **the Google account that
    owns the CapryBeats channel** (the one YouTube Studio opens with). First visit:
    agree to the terms, country Slovakia.
A2. Top bar → *Select a project* → **NEW PROJECT** → name `BeatPub` → no organisation →
    **CREATE**. Wait for the bell notification → **SELECT PROJECT**.
A3. On the dashboard the *Project info* card shows **Project number** (digits only).
    Paste it into `docs\YOUTUBE_API_SETUP.md` where it says `____________` — the audit
    form needs it.
A4. ≡ menu → *APIs & Services → Library* → search `YouTube Data API v3` → **ENABLE**.
A5. *APIs & Services → OAuth consent screen* (it redirects to *Google Auth Platform*) →
    **Get started**: app name `BeatPub` (must not contain the word YouTube), support
    email = your Gmail, audience **External**, contact email, agree, **Create**.
A6. *Data Access → Add or remove scopes* → filter `youtube` → tick
    `…/auth/youtube` ("Manage your YouTube account") → *Update* → **Save**.
A7. *Audience* → **Publish app** → confirm. (In "Testing" the login expires every
    7 days; in "Production" it lasts. Google will show an "unverified app" warning the
    first time you sign in — expected for a personal tool; click *Advanced → Go to
    BeatPub*.)
A8. *Clients → Create client* → application type **Desktop app** → name `BeatPub
    desktop` → **Create** → **Download JSON**. It lands in `Downloads` with a long name
    like `client_secret_1234…apps.googleusercontent.com.json`. Move it into
    `Desktop\beatpub` and rename it to exactly `client_secret.json` (turn on *View →
    File name extensions* in File Explorer so you don't end up with `.json.txt`).
    It's listed in `.gitignore`, so Claude Code will never upload it anywhere.
B. **Part B, same evening if you have the energy:** three pages on the Webflow site
    (`/beatpub`, `/beatpub-privacy`, `/beatpub-terms`) from `docs\privacy-policy.md` and
    `docs\terms.md`, then put those URLs into *Branding* in the Google Auth Platform.
    The audit form later asks for screenshots of them.
C/D. **Parts C and D** (evidence + the form) come after build step 5 has done one real
    private upload — the guide lists every screenshot and every form answer. Until the
    audit passes, every API upload is forced private; you can still test everything.

---

## Part 6 — What a day looks like once it's built

Morning, after the beat is exported into `beats\<slug>\` with the cover:

```
beatpub new <slug>          # meta.yaml prefilled from the file name; add type_beat / similar / mood
beatpub run <slug>
```

It renders the video and the Short, drafts both texts in your format, shows them for one
approval, uploads the video (thumbnail, playlist) and then the Short with the video's link.
Both can be scheduled: `publish: 2026-11-20T18:00:00+01:00` in `meta.yaml` makes YouTube
release the video at that time by itself (the Short follows by
`config.shorts.publish_offset_hours`), so the laptop can be off. Until the audit passes,
everything stays private and you publish from YouTube Studio by hand. Once a month:
`beatpub stats` to see which Shorts and which looks pull views.

Things to never do: don't put `client_secret.json` or `token.json` in a chat, a screenshot,
or a public repo; don't re-upload a YouTube video that was locked private before the
audit — upload it again after approval instead.
