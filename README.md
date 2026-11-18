# BeatPub

One command: beat + cover photo → 1080p YouTube video, tagged MP3, thumbnail and a vertical
Short, uploaded to your channel with titles, descriptions and tags in your house style.

| Read this | For |
|---|---|
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | **the manual**: daily workflow, every command, settings, scheduling, what to do when something fails |
| [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md) | the slow step-by-step first setup on Windows |
| [docs/YOUTUBE_API_SETUP.md](docs/YOUTUBE_API_SETUP.md) | Google Cloud, `client_secret.json` and the compliance audit |
| [CLAUDE.md](CLAUDE.md) | the spec this was built from |

## Install

1. **ffmpeg** — Windows: `winget install -e --id Gyan.FFmpeg` · macOS: `brew install ffmpeg`.
   Restart the terminal and check with `ffmpeg -version`.
2. **BeatPub** — in this folder (PowerShell; on macOS use `python3` and `source .venv/bin/activate`):

   ```
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   beatpub doctor
   ```

   `beatpub doctor` checks Python, ffmpeg, `config.yaml` and the YouTube files. Python 3.11 or newer.

## First run

1. Put your links, playlist name and usual artists in `config.yaml`.
2. Follow [docs/YOUTUBE_API_SETUP.md](docs/YOUTUBE_API_SETUP.md) until `client_secret.json` is in this folder.
3. `beatpub auth` — opens the browser once; the login is kept in `token.json`.
4. `beatpub learn` — optional: reads your own videos and shows your best title patterns and tags.

`client_secret.json` and `token.json` are private; they are gitignored and never printed.

## Per beat

```
mkdir beats\tuesday-night                         # 1. a folder per beat
# 2. drop in the beat, named as always ("Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts.wav"), and the cover (JPG/PNG)
beatpub run tuesday-night --dry-run               # 3. optional: see everything that would be made and sent
beatpub run tuesday-night                         # 4. render → Short → draft → asks you to approve → uploads both
beatpub stats                                     # 5. later: views, likes, comments
```

- Title, BPM, key and co-producers come from the file name; artists, moods and `free` from
  `defaults:` in `config.yaml`. A `meta.yaml` (`beatpub new <slug>`) is only for a beat that differs.
- Nothing is uploaded without your approval (`--yes` skips the question). `--dry-run` works on
  every command.
- Running again skips what is already done; a beat is uploaded once. `--force` redoes a step.
- A 3-minute beat renders in about 1–3 minutes; `--preset veryfast` is fine for a still picture.
- If a thumbnail did not go through: `beatpub thumbnail <slug>`.
- Until the YouTube API audit passes (`audit_passed: false`), YouTube keeps every upload private.

The single steps: `render`, `short render`, `draft`, `approve`, `upload`, `short upload`,
`thumbnail`, `stats`, `learn`, `auth`, `doctor`, `new` — all explained in the manual, and
`beatpub <command> --help` lists the options.

`draft --ai` lets Claude refine the template draft. It needs `ai_model` in `config.yaml`,
`python -m pip install anthropic` and an `ANTHROPIC_API_KEY` (pay-as-you-go, separate from a
claude.ai subscription); without them the template draft is used.

## Tests

`python -m pytest` (fast, no ffmpeg, no network) and `python -m pytest -m acceptance`
(renders a generated 60-second beat with ffmpeg, about half a minute).

## The stand-alone scripts

`scripts\render.py` and `scripts\short.py` do the same rendering without installing BeatPub:

```
python -m pip install -r requirements-render.txt
python scripts\render.py --beat beats\tuesday-night --preset veryfast
python scripts\short.py  --beat beats\tuesday-night --preset veryfast
```

`--beat` takes a folder or the audio file; `--title/--bpm/--key` override the file name;
`--text` writes title and credit on the picture; `short.py --start 1:04` picks the section.
