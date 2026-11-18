"""The `beatpub` command line."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from dataclasses import replace

from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.table import Table
from rich.text import Text

from . import __version__, drafting, log, schedule, youtube
from .youtube import YouTubeError
from .config import Config, ConfigError, find_root, load_config
from .drafting import DRAFT_NAME, SHORT_DRAFT_NAME, Draft, DraftError
from .meta import (META_NAME, Meta, MetaError, beat_dir, collabs_from_handles, credit_line, find_audio,
                   find_cover, load_meta, meta_yaml_text, parse_beat_name)
from .render import FFMPEG_HINT, RenderError, render_beat
from .short import render_short

app = typer.Typer(add_completion=False,
                  help="Beat + cover photo → YouTube video and Short, in the channel's house style.")
short_app = typer.Typer(add_completion=False, help="The vertical Short of a beat.")
app.add_typer(short_app, name="short")
console = Console(highlight=False)


def say(message: str) -> None:
    """Plain progress line (file names and ffmpeg commands contain [brackets]; no markup)."""
    console.print(message, markup=False, soft_wrap=True)


def fail(message: str, code: int = 1) -> typer.Exit:
    console.print("[red]error:[/red]", end=" ")
    console.print(message, markup=False, soft_wrap=True)
    return typer.Exit(code)


def get_config() -> Config:
    try:
        config = load_config()
    except ConfigError as exc:
        raise fail(str(exc)) from None
    for warning in config.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    return config


# ----------------------------------------------------------------------------- doctor
def tool_version(name: str) -> tuple[bool, str]:
    """(found, 'version — path') for ffmpeg/ffprobe, or (False, install hint)."""
    path = shutil.which(name)
    if not path:
        return False, f"not found on PATH — {FFMPEG_HINT}"
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=15)
        words = out.stdout.split()
        version = words[2] if out.returncode == 0 and len(words) > 2 else "unknown version"
    except (OSError, subprocess.SubprocessError):
        return False, f"found at {path} but it does not run — reinstall ffmpeg"
    return True, f"{version} — {path}"


def secret_status(path: Path, expect_key: tuple[str, ...]) -> tuple[bool, str]:
    """Is the JSON secret there and shaped right? Never returns any of its content."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "missing"
    except (OSError, ValueError):
        return False, "present but not readable JSON — download it again"
    if not isinstance(data, dict) or not any(k in data for k in expect_key):
        return False, "present but not the expected kind of file"
    return True, "present"


@app.command()
def doctor() -> None:
    """Check ffmpeg, ffprobe, config.yaml, client_secret.json and the YouTube token."""
    rows: list[tuple[str, str, str]] = []          # (status, what, detail); status: ok | warn | FAIL

    py_ok = sys.version_info >= (3, 11)
    rows.append(("ok" if py_ok else "FAIL", "python",
                 f"{sys.version.split()[0]}" + ("" if py_ok else " — BeatPub needs 3.11 or newer")))
    for tool in ("ffmpeg", "ffprobe"):
        found, detail = tool_version(tool)
        rows.append(("ok" if found else "FAIL", tool, detail))

    config: Config | None = None
    try:
        root = find_root()
        rows.append(("ok", "project folder", str(root)))
        config = load_config(root)
        rows.append(("ok", "config.yaml", f"artist {config.artist} · default privacy {config.privacy_default} · "
                     f"audit {'passed' if config.audit_passed else 'not passed (uploads stay private)'}"))
        rows += [("warn", "config.yaml", w.removeprefix("config.yaml: ")) for w in config.warnings]
    except ConfigError as exc:
        rows.append(("FAIL", "config.yaml", str(exc)))

    if config:
        beats = config.beats_dir
        count = sum(1 for p in beats.iterdir() if p.is_dir() and not p.name.startswith("_")) if beats.is_dir() else 0
        rows.append(("ok" if beats.is_dir() else "warn", "beats folder",
                     f"{count} beat folder(s) in {beats}" if beats.is_dir() else f"{beats} does not exist yet"))

        ok, detail = secret_status(config.client_secret_path, ("installed", "web"))
        if not ok and detail == "missing":
            stray = sorted(p.name for p in config.root.glob("client_secret*.json*") if p.name != "client_secret.json")
            detail = (f"missing, but found '{stray[0]}' — rename it to exactly client_secret.json "
                      "(Windows hides the real extension)") if stray else \
                "missing — needed for YouTube only; see docs/YOUTUBE_API_SETUP.md"
        rows.append(("ok" if ok else "warn", "client_secret.json", detail))

        ok, detail = secret_status(config.token_path, ("refresh_token", "token"))
        rows.append(("ok" if ok else "warn", "token.json",
                     detail if ok else f"{detail} — run `beatpub auth` before the first upload"))

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    colors = {"ok": "green", "warn": "yellow", "FAIL": "red"}
    for status, what, detail in rows:
        table.add_row(f"[{colors[status]}]{status}[/]", what, detail)
    console.print(f"beatpub {__version__}")
    console.print(table)

    failed = [what for status, what, _ in rows if status == "FAIL"]
    if failed:
        console.print(f"\n[red]not ready:[/red] fix {', '.join(dict.fromkeys(failed))}")
        raise typer.Exit(1)
    warned = any(status == "warn" for status, _, _ in rows)
    console.print("\nready to render" + (" — the warnings only matter for YouTube uploads" if warned else " and upload"))


# ----------------------------------------------------------------------------- new
@app.command()
def new(
    slug: str = typer.Argument(..., help="Folder name under beats/, e.g. tuesday-night"),
    title: str = typer.Option("", "--title", help="Beat title; default: read from the audio file name"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing meta.yaml"),
) -> None:
    """Create beats/<slug>/meta.yaml, prefilled from the audio file name in that folder."""
    config = get_config()
    try:
        folder = beat_dir(config.beats_dir, slug)
        created = not folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / META_NAME
        if path.exists() and not force:
            console.print(f"{path} already exists — nothing changed (use --force to overwrite it)")
            return
        audio = find_audio(folder)
        cover = find_cover(folder)
    except MetaError as exc:
        raise fail(str(exc)) from None

    parsed = parse_beat_name(audio.stem) if audio else {"title": "", "bpm": 0, "key": "", "handles": []}
    collabs = collabs_from_handles(parsed["handles"], config.artist)
    beat_title = title.strip() or parsed["title"]
    path.write_text(meta_yaml_text(beat_title, parsed["bpm"], parsed["key"], collabs, config.defaults,
                                   source=audio.name if audio else ""), encoding="utf-8", newline="\n")

    if created:
        console.print(f"created folder {folder}")
    console.print(f"wrote {path}")
    if audio:
        console.print(f"  audio   {audio.name}", markup=False)
        console.print(f"  title   {beat_title}", markup=False)
        console.print(f"  bpm     {parsed['bpm'] or '? (not in the file name — set it in meta.yaml)'}", markup=False)
        console.print(f"  key     {parsed['key'] or '? (not in the file name — set it in meta.yaml)'}", markup=False)
        console.print(f"  credit  {credit_line(config.artist, collabs)}", markup=False)
    else:
        console.print("  no audio file in the folder yet — drop the beat in (keep its usual name); "
                      "title, BPM, key and @handles are read from the name")
    console.print(f"  cover   {cover.name}" if cover else "  cover   none yet — add a JPG or PNG", markup=False)
    d = config.defaults
    if d.type_beat:
        say(f"  from config.yaml defaults: {' x '.join(d.type_beat)} type beat"
            f"{' · free' if d.free else ''}{' · ' + ', '.join(d.mood) if d.mood else ''}")
        say(f"next: beatpub render {slug}   (edit meta.yaml only if this beat is different)")
    else:
        say("next: set type_beat under defaults: in config.yaml (or in this meta.yaml)")


# ----------------------------------------------------------------------------- render
def load_beat(slug: str, for_draft: bool = False) -> tuple[Config, Path, Meta]:
    """Config + beat folder + resolved meta, with warnings and a one-line summary printed."""
    config = get_config()
    try:
        folder = beat_dir(config.beats_dir, slug)
        meta = load_meta(folder, config, for_draft=for_draft)
    except MetaError as exc:
        raise fail(str(exc)) from None
    if _announce[0]:
        for warning in meta.warnings:
            console.print("[yellow]warning:[/yellow]", end=" ")
            say(warning)
        say(f'beat: "{meta.title}"  {meta.bpm or "?"} BPM  {meta.key or "key ?"}  '
            f'{credit_line(config.artist, meta.collabs)}')
    return config, folder, meta


_announce = [True]      # `run` switches the beat line off after its first step instead of repeating it six times


PRESET_HELP = "x264 preset: veryfast … slow (veryfast is fine for a still picture)"


@app.command()
def render(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    force: bool = typer.Option(False, "--force", help="Render again even if the outputs are up to date"),
    preset: str = typer.Option("medium", "--preset", help=PRESET_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the ffmpeg commands; write nothing"),
) -> None:
    """Audio + cover → out/video.mp4 (1920×1080), beat.mp3 (tagged), thumb.jpg, frame.png, artwork.jpg."""
    config, folder, meta = load_beat(slug)
    try:
        render_beat(folder, meta, config.artist, force=force, dry_run=dry_run, preset=preset, say=say)
    except (MetaError, RenderError) as exc:
        raise fail(str(exc), getattr(exc, "code", 1)) from None


@short_app.command("render")
def short_render(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    force: bool = typer.Option(False, "--force", help="Render again even if the outputs are up to date"),
    preset: str = typer.Option("medium", "--preset", help=PRESET_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the ffmpeg command; write nothing"),
) -> None:
    """The beat's best 30 s → out/short.mp4 (1080×1920), short_cover.jpg, short_pick.json."""
    config, folder, meta = load_beat(slug)
    if not meta.short:
        say("short: false in meta.yaml — no Short for this beat")
        return
    if not config.shorts.enabled:
        say("shorts.enabled is false in config.yaml — no Shorts are made")
        return
    try:
        render_short(folder, meta, config.artist, force=force, dry_run=dry_run, preset=preset, say=say)
    except (MetaError, RenderError) as exc:
        raise fail(str(exc), getattr(exc, "code", 1)) from None


# ----------------------------------------------------------------------------- draft + approve
def slot_notes(meta: Meta, config: Config) -> list[str]:
    """What to say next to the slot of the video draft and of the Short draft ('' = just the date)."""
    if not config.schedule.enabled or meta.publish:
        return ["", ""]
    hours = config.shorts.publish_offset_hours
    short = "" if meta.short_publish else f"{hours:g} h after the video" if hours else "with the video"
    return ["next free slot in the schedule chain", short]


def show_draft(draft: Draft, heading: str, slot_note: str = "") -> None:
    """One draft as the owner will see it on YouTube, plus the numbers that have limits."""
    body = Text()
    body.append(draft.title + "\n", style="bold")
    body.append(f"{drafting.text_length(draft.title)}/{drafting.TITLE_MAX} characters\n\n", style="dim")
    body.append(draft.description + "\n\n")
    body.append(f"tags ({len(draft.tags)}, {drafting.tags_length(draft.tags)}/{drafting.TAGS_MAX} characters)\n", style="dim")
    body.append(", ".join(draft.tags) + "\n\n")
    when = f"private, goes public {draft.publish_at}" if draft.publish_at else draft.privacy
    body.append(f"privacy   {when}\n")
    slot = schedule.parse_when(draft.publish_at)
    if slot:
        body.append(f"slot      {schedule.slot_text(slot)}" + (f"  ({slot_note})" if slot_note else "") + "\n")
    body.append(f"playlist  {draft.playlist or '(none)'}\n")
    body.append(f"approved  {'yes' if draft.approved else 'no'}")
    console.print(Panel(body, title=heading, title_align="left", border_style="green" if draft.approved else "yellow"))
    for note in draft.notes:
        console.print("[yellow]note:[/yellow]", end=" ")
        say(note)


def draft_files(folder: Path, meta: Meta, config: Config) -> list[tuple[Path, str]]:
    """(path, heading) of the drafts this beat has: the video, and the Short unless it is switched off."""
    files = [(folder / "out" / DRAFT_NAME, "YouTube video draft")]
    if meta.short and config.shorts.enabled:
        files.append((folder / "out" / SHORT_DRAFT_NAME, "YouTube Short draft"))
    return files


@app.command()
def draft(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    ai: bool = typer.Option(False, "--ai", help="Let Claude refine the template draft (needs ai_model + ANTHROPIC_API_KEY)"),
    force: bool = typer.Option(False, "--force", help="Write the drafts again, discarding edits and approval"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the drafts; write nothing, call nothing"),
) -> None:
    """Title, description and tags in the channel's style → out/draft.yaml + out/short_draft.yaml."""
    make_drafts(slug, ai=ai, force=force, dry_run=dry_run)


def make_drafts(slug: str, *, ai: bool = False, force: bool = False, dry_run: bool = False,
                show: bool = True) -> list[Draft]:
    """The `draft` step. Returns the drafts as they now stand (video first, then the Short if there is one)."""
    config, folder, meta = load_beat(slug, for_draft=True)
    templates, out = config.root / "templates", folder / "out"
    files = draft_files(folder, meta, config)
    names = tuple(path.name for path, _ in files)
    notes = slot_notes(meta, config)
    try:
        fingerprint = drafting.inputs_fingerprint(meta, config, templates, ai)
        status = drafting.draft_status(out, fingerprint, names)
        if status in ("current", "edited") and not force and not dry_run:
            existing = [drafting.read_draft(path) for path, _ in files]
            if show:
                for (_, heading), item, note in zip(files, existing, notes):
                    show_draft(item, heading, note)
            if status == "current":
                say(f"drafts are up to date in {out} — nothing to do (use --force to write them again)")
            else:
                say("meta.yaml, config.yaml or a template changed after these drafts were written, but you have "
                    "edited or approved them, so they were kept. `beatpub draft " + slug + " --force` rewrites them.")
            return existing

        slot = None if meta.publish else schedule.assign_slot(config, slug)
        video, short = drafting.build_drafts(meta, config, templates, slot=slot)
        if ai:
            reason = drafting.ai_unavailable(config)
            if reason:
                say(f"--ai skipped ({reason}) — using the template draft")
            elif dry_run:
                request = drafting.ai_request(meta, config, video, drafting.load_examples(templates))
                say("dry run — this would be sent to the Claude API:")
                say(json.dumps(request, indent=2, ensure_ascii=False))
            else:
                try:
                    say(f"asking {config.ai_model} for a refined draft …")
                    video = drafting.ai_draft(meta, config, video, templates)
                except DraftError as exc:
                    say(f"--ai failed ({exc}) — using the template draft")
        drafts = [video, short][: len(files)]
        for (path, heading), item, note in zip(files, drafts, notes):
            if not dry_run:
                drafting.write_draft(path, item, heading, slug)
            if show:
                show_draft(item, heading, note)
        if slot:
            say(f"scheduled: {schedule.slot_text(slot)} — the next free slot in the chain (`beatpub queue` shows all)")
        reminder = drafting.audit_reminder(config, *drafts)
        if reminder and show:
            console.print("[yellow]" + reminder + "[/yellow]")
        if dry_run:
            say("dry run — nothing written")
            return drafts
        drafting.save_state(out, fingerprint, names)
    except DraftError as exc:
        raise fail(str(exc)) from None
    say(f"wrote {', '.join(names)} in {out}")
    if show:
        say(f"next: beatpub approve {slug}   (edit the files first if you want to change anything)")
    return drafts


def open_in_editor(path: Path) -> bool:
    """Open a file in $VISUAL / $EDITOR and wait. False when no editor is set or it failed to start."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        return False
    try:
        subprocess.run([*shlex.split(editor, posix=os.name != "nt"), str(path)], check=False)
        return True
    except (OSError, ValueError):
        say(f"could not start the editor '{editor}' — edit {path} by hand")
        return False


@app.command()
def approve(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without asking"),
    edit: bool = typer.Option(True, "--edit/--no-edit", help="Open the drafts in $EDITOR first (when it is set)"),
) -> None:
    """Review the drafts (in $EDITOR if set), check them against YouTube's limits, set approved: true."""
    config, folder, meta = load_beat(slug)
    files = draft_files(folder, meta, config)
    try:
        for path, _ in files:
            drafting.read_draft(path)                      # both must exist before anything opens
        if edit and not yes and sys.stdin.isatty():
            for path, _ in files:
                open_in_editor(path)
        drafts = [drafting.read_draft(path) for path, _ in files]
    except DraftError as exc:
        raise fail(str(exc)) from None

    problems: list[str] = []
    for (path, heading), item, note in zip(files, drafts, slot_notes(meta, config)):
        show_draft(item, heading, note)
        problems += [f"{path.name}: {p}" for p in drafting.validate_draft(item)]
    slot = schedule.parse_when(drafts[0].publish_at)
    taken = schedule.used_dates(config, skip_slug=slug).get(slot.date()) if slot else None
    if taken:
        console.print("[yellow]warning:[/yellow]", end=" ")
        say(f"{slot:%a %d %b %Y} already has a video ({', '.join(taken)}) — `beatpub draft {slug} --force` "
            "picks the next free slot")
    if problems:
        for problem in problems:
            console.print("[red]problem:[/red]", end=" ")
            say(problem)
        raise fail("not approved — fix the draft file(s) and run approve again")
    reminder = drafting.audit_reminder(config, *drafts)
    if reminder:
        console.print("[yellow]" + reminder + "[/yellow]")
    if all(item.approved for item in drafts):
        say("already approved — nothing to do")
        return
    if not yes:
        if not sys.stdin.isatty():
            raise fail("not approved: no terminal to ask in — run `beatpub approve " + slug + " --yes`")
        if not typer.confirm("Approve these drafts for upload?", default=False):
            say("not approved — edit the files in " + str(folder / "out") + " and run approve again")
            raise typer.Exit(1)
    try:
        for path, heading in files:
            drafting.approve_file(path, heading, slug)
    except DraftError as exc:
        raise fail(str(exc)) from None
    say(f"approved: {', '.join(path.name for path, _ in files)}")


# ----------------------------------------------------------------------------- upload
API = "https://www.googleapis.com"
DRY_RUN_URL = "https://youtu.be/VIDEO_ID"      # stands in for the video's link while it does not exist yet


def size_text(path: Path) -> str:
    if not path.is_file():
        return "not made yet"
    size = path.stat().st_size
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{max(1, size // 1024)} kB"


def link_short_description(description: str, url: str | None) -> str:
    """Put the video's link into the Short's description; with no link, drop the lines that wanted one."""
    if url:
        return description.replace(drafting.URL_PLACEHOLDER, url)
    kept = [line for line in description.split("\n") if drafting.URL_PLACEHOLDER not in line]
    return drafting.clean_block("\n".join(kept))


def print_upload_plan(body: dict, media: Path, thumb: Path, playlist: str, notify: bool, record: Path) -> None:
    """Exactly what an upload sends, in order. Used by --dry-run; touches nothing."""
    say("dry run — this is what would be sent (nothing leaves this computer):")
    say(f"1. POST {API}/upload/youtube/v3/videos?part=snippet,status&notifySubscribers={str(notify).lower()}"
        "&uploadType=resumable")
    say(f"   file: {media}  ({size_text(media)}, video/mp4, sent in {youtube.CHUNK_BYTES // 2**20} MiB chunks)")
    say("   body:")
    say(json.dumps(body, indent=2, ensure_ascii=False))
    say(f"2. GET {API}/youtube/v3/videos?part=processingDetails&id=<the new video's id>   (every "
        f"{youtube.PROCESSING_POLL_SECONDS} s until YouTube has processed it, at most "
        f"{youtube.PROCESSING_TIMEOUT_SECONDS // 60} min)")
    say(f"   POST {API}/upload/youtube/v3/thumbnails/set?videoId=<the new video's id>")
    say(f"   file: {thumb}  ({size_text(thumb)}, image/jpeg)")
    if playlist:
        say(f"3. GET {API}/youtube/v3/playlists?part=snippet&mine=true&maxResults=50   (to find the playlist "
            f"named \"{playlist}\")")
        say(f"   POST {API}/youtube/v3/playlistItems?part=snippet")
        say('   body: {"snippet": {"playlistId": "<that playlist\'s id>", "resourceId": {"kind": "youtube#video", '
            '"videoId": "<the new video\'s id>"}}}')
    else:
        say("3. no playlist set — nothing more is sent")
    say(f"then: write {record} and add a row to {log.PUBLISHED_NAME}")


def wait_for_processing(service: object, video_id: str) -> None:
    """After an upload: wait until YouTube has processed the video (it refuses thumbnails before that)."""
    minutes = youtube.PROCESSING_TIMEOUT_SECONDS // 60
    with console.status(f"waiting for YouTube to finish processing (up to {minutes} min) …"):
        status = youtube.wait_until_processed(service, video_id)
    say({"succeeded": "YouTube finished processing",
         "processing": f"YouTube is still processing after {minutes} min — trying the thumbnail anyway",
         "": "could not ask YouTube whether processing is finished — trying the thumbnail anyway"}
        .get(status, f"YouTube reports processing as \"{status}\" — trying the thumbnail anyway"))


def apply_thumbnail(service: object, record: dict, thumb: Path, *, after_upload: bool = False) -> bool:
    """
    Set the custom thumbnail of an uploaded video and note the result in its record. Never raises.
    Right after an upload: wait for YouTube's processing first, and try a refusal again a couple of times.
    """
    try:
        if not thumb.is_file():
            raise YouTubeError(f"{thumb.name} not found in {thumb.parent}")
        if after_upload:
            wait_for_processing(service, record["videoId"])
        youtube.set_thumbnail(service, record["videoId"], thumb, say=say,
                              retries=youtube.THUMBNAIL_RETRIES if after_upload else 0)
    except YouTubeError as exc:
        record["thumbnail_ok"] = False
        record["thumbnail_error"] = str(exc)
        console.print("[yellow]thumbnail not set:[/yellow]", end=" ")
        say(str(exc))
        return False
    record["thumbnail_ok"] = True
    record.pop("thumbnail_error", None)
    say(f"thumbnail set: {thumb.name} → {record.get('url', '')}")
    return True


def do_upload(kind: str, slug: str, *, yes: bool, dry_run: bool, force: bool, no_link: bool = False,
              override: Draft | None = None) -> None:
    """Upload the video (kind 'video') or the Short (kind 'short') of a beat, then thumbnail, playlist, log."""
    config, folder, meta = load_beat(slug)
    out, is_short = folder / "out", kind == "short"
    label = "Short" if is_short else "video"
    if is_short and not (meta.short and config.shorts.enabled):
        say("no Short for this beat (short: false in meta.yaml or shorts.enabled: false in config.yaml)")
        return
    media = out / ("short.mp4" if is_short else "video.mp4")
    thumb = out / ("short_cover.jpg" if is_short else "thumb.jpg")
    record_path = out / (log.SHORT_PUBLISH_JSON if is_short else log.PUBLISH_JSON)
    draft_path = out / (SHORT_DRAFT_NAME if is_short else DRAFT_NAME)

    previous = log.read_publish(record_path)
    if previous and not force:
        say(f"{label} already uploaded: {previous.get('url')} — nothing to do (--force uploads a second copy)")
        return
    try:
        item = override or drafting.read_draft(draft_path)
    except DraftError as exc:
        raise fail(str(exc)) from None

    linked = ""
    if is_short:
        video_record = log.read_publish(out / log.PUBLISH_JSON)
        if no_link:
            item = replace(item, description=link_short_description(item.description, None))
        elif video_record:
            linked = str(video_record.get("url") or "")
            item = replace(item, description=link_short_description(item.description, linked))
        elif dry_run:
            say(f"note: the video is not uploaded yet — {DRY_RUN_URL} stands in for its link below")
            item = replace(item, description=link_short_description(item.description, DRY_RUN_URL))
        elif drafting.URL_PLACEHOLDER in item.description:
            raise fail(f"the video has not been uploaded yet, so the Short cannot link to it — run "
                       f"`beatpub upload {slug}` first (or pass --no-link)")

    problems = drafting.validate_draft(item)
    if problems:
        for problem in problems:
            console.print("[red]problem:[/red]", end=" ")
            say(f"{draft_path.name}: {problem}")
        raise fail(f"not uploaded — fix {draft_path.name} (or run `beatpub draft {slug} --force`) and approve again")
    if not item.approved and not yes:
        if not dry_run:
            raise fail(f"{draft_path.name} is not approved — run `beatpub approve {slug}` first (or pass --yes)")
        say(f"note: {draft_path.name} is not approved yet — a real upload would be refused until it is")
    if not media.is_file() and not dry_run:
        command = f"beatpub short render {slug}" if is_short else f"beatpub render {slug}"
        raise fail(f"{media.name} not found — run `{command}` first")

    body = youtube.build_body(item, config.default_language)
    reminder = drafting.audit_reminder(config, item)
    if reminder:
        console.print("[yellow]" + reminder + "[/yellow]")
    if dry_run:
        print_upload_plan(body, media, thumb, item.playlist, config.notify_subscribers, record_path)
        return

    try:
        service = youtube.get_service(config)
        say(f"uploading the {label}: {item.title}")
        with Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                      TimeRemainingColumn(), console=console) as bar:
            task = bar.add_task(media.name, total=media.stat().st_size)
            response = youtube.upload_video(service, media, body, notify_subscribers=config.notify_subscribers,
                                            progress=lambda sent, total: bar.update(task, completed=sent, total=total))
    except YouTubeError as exc:
        raise fail(str(exc)) from None

    video_id = response["id"]
    actual = response.get("status") or {}
    record = {
        "kind": kind, "slug": slug, "videoId": video_id, "url": youtube.video_url(video_id), "title": item.title,
        "privacy": actual.get("privacyStatus") or item.privacy, "requestedPrivacy": item.privacy,
        "publishAt": actual.get("publishAt") or "", "requestedPublishAt": item.publish_at,
        "uploadedAt": log.now_iso(), "thumbnail_ok": False, "playlist_ok": None, "playlist": item.playlist,
    }
    if is_short:
        record.update({"linked_video": linked, "hook_text": meta.hook_text, "short_layout": meta.short_layout})
    log.write_publish(record_path, record)                 # first thing: a second run must not upload it again
    say(f"uploaded: {record['url']}")
    if record["privacy"] != item.privacy:
        say(f"note: asked for {item.privacy}, YouTube set it to {record['privacy']}")
    if item.publish_at and not record["publishAt"]:
        say(f"note: asked to publish at {item.publish_at}, but YouTube did not keep the schedule")

    # follow-up steps are best-effort: a failure is reported, the upload stays
    if not apply_thumbnail(service, record, thumb, after_upload=True):
        say(f"the {label} itself is uploaded. Try the thumbnail again with: beatpub thumbnail {slug}")
    if item.playlist:
        try:
            playlist_id = youtube.find_playlist(service, item.playlist)
            if not playlist_id:
                raise YouTubeError(f"no playlist named \"{item.playlist}\" on the channel")
            youtube.add_to_playlist(service, playlist_id, video_id)
            record["playlist_ok"] = True
            say(f"added to playlist: {item.playlist}")
        except YouTubeError as exc:
            record["playlist_ok"] = False
            record["playlist_error"] = str(exc)
            console.print("[yellow]not added to a playlist:[/yellow]", end=" ")
            say(str(exc))
    log.write_publish(record_path, record)
    log.append_published(config.root / log.PUBLISHED_NAME, record)
    say(f"logged in {record_path.name} and {log.PUBLISHED_NAME}")
    say(record["url"])


@app.command()
def upload(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Upload even if the draft is not approved"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the full request; send nothing"),
    force: bool = typer.Option(False, "--force", help="Upload again although publish.json exists (a NEW video)"),
) -> None:
    """Upload out/video.mp4 with the approved draft, set the thumbnail, add it to the playlist → out/publish.json."""
    do_upload("video", slug, yes=yes, dry_run=dry_run, force=force)


@short_app.command("upload")
def short_upload(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Upload even if the draft is not approved"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the full request; send nothing"),
    force: bool = typer.Option(False, "--force", help="Upload again although short_publish.json exists (a NEW Short)"),
    no_link: bool = typer.Option(False, "--no-link", help="Upload without the link to the long video"),
) -> None:
    """Upload out/short.mp4; its description links to the video uploaded before → out/short_publish.json."""
    do_upload("short", slug, yes=yes, dry_run=dry_run, force=force, no_link=no_link)


@app.command()
def thumbnail(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    force: bool = typer.Option(False, "--force", help="Set the thumbnails again even if they were set before"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the requests; send nothing"),
) -> None:
    """Set the thumbnails of an already-uploaded beat again (video: thumb.jpg, Short: short_cover.jpg)."""
    config, folder, _ = load_beat(slug)
    out = folder / "out"
    targets = [("video", out / log.PUBLISH_JSON, out / "thumb.jpg"),
               ("Short", out / log.SHORT_PUBLISH_JSON, out / "short_cover.jpg")]
    todo = []
    for label, record_path, thumb in targets:
        record = log.read_publish(record_path)
        if not record:
            say(f"{label}: not uploaded yet — nothing to do")
        elif record.get("thumbnail_ok") and not force:
            say(f"{label}: the thumbnail is already set — nothing to do (--force sets it again)")
        else:
            todo.append((label, record_path, thumb, record))
    if not todo:
        return
    if dry_run:
        say("dry run — this is what would be sent (nothing leaves this computer):")
        for label, _, thumb, record in todo:
            say(f"{label}: POST {API}/upload/youtube/v3/thumbnails/set?videoId={record['videoId']}")
            say(f"   file: {thumb}  ({size_text(thumb)}, image/jpeg)")
        return
    try:
        service = youtube.get_service(config)
    except YouTubeError as exc:
        raise fail(str(exc)) from None
    failed = 0
    for label, record_path, thumb, record in todo:
        say(f"{label}: {record.get('title', '')}")
        failed += not apply_thumbnail(service, record, thumb)
        log.write_publish(record_path, record)
    if failed:
        raise fail(f"{failed} thumbnail(s) not set — see the message above, then run "
                   f"`beatpub thumbnail {slug}` again, or set them by hand in YouTube Studio")


@app.command()
def run(
    slug: str = typer.Argument(..., help="Folder name under beats/"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not stop to ask for approval"),
    ai: bool = typer.Option(False, "--ai", help="Let Claude refine the draft"),
    no_short: bool = typer.Option(False, "--no-short", help="Skip the Short"),
    preset: str = typer.Option("medium", "--preset", help=PRESET_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Go through every step; render, write and send nothing"),
) -> None:
    """Everything for one beat: render → Short → draft → approve → upload → Short upload."""
    def step(title: str) -> None:
        console.rule(f"[bold]{title}[/bold]", align="left")

    try:
        step("render the video")
        render(slug, force=False, preset=preset, dry_run=dry_run)
        _announce[0] = False
        if not no_short:
            step("render the Short")
            short_render(slug, force=False, preset=preset, dry_run=dry_run)
        step("draft title, description and tags")
        asks = not yes and not dry_run
        drafts = make_drafts(slug, ai=ai, dry_run=dry_run, show=not asks)     # approve shows them when it asks
        if asks:
            step("approve")
            approve(slug, yes=False, edit=True)
        step("upload the video")
        do_upload("video", slug, yes=yes, dry_run=dry_run, force=False, override=drafts[0] if dry_run else None)
        if not no_short:
            step("upload the Short")
            do_upload("short", slug, yes=yes, dry_run=dry_run, force=False,
                      override=drafts[1] if dry_run and len(drafts) > 1 else None)
    finally:
        _announce[0] = True
    if not dry_run:
        step("done")
        say("check both in YouTube Studio: https://studio.youtube.com/")


@app.command()
def queue() -> None:
    """Every beat folder with its publish date and status, sorted by date; flags duplicates and gaps."""
    config = get_config()
    entries = schedule.queue(config)
    if not entries:
        say(f"no beat folders in {config.beats_dir}")
        return
    table = Table(title="Queue — sorted by publish date", title_justify="left")
    for name in ("date", "time", "beat", "status", "title", "check"):
        table.add_column(name, overflow="fold")
    colors = {"uploaded": "green", "approved": "cyan", "drafted": "yellow"}
    for entry in entries:
        if entry.when:
            day, clock = f"{entry.when:%a %d %b %Y}", f"{entry.when:%H:%M}"
        elif entry.status in ("drafted", "approved", "uploaded"):
            day, clock = "no date", entry.privacy
        else:
            day, clock = "—", ""
        table.add_row(day, clock, Text(entry.slug), Text(entry.status, style=colors.get(entry.status, "")),
                      Text(entry.title), Text("; ".join(entry.flags), style="red"))
    console.print(table)
    flagged = sum(1 for entry in entries if entry.flags)
    s = config.schedule
    say(f"chain: one video every {s.every_days} day{'s' if s.every_days != 1 else ''} at {s.time}"
        + ("" if s.enabled else "  (schedule.enabled is false in config.yaml — no slots are assigned)"))
    if flagged:
        say(f"{flagged} beat(s) break the chain — `beatpub draft <folder> --force` gives a beat the next free slot")
    elif any(entry.when for entry in entries):
        say("no duplicates or gaps from today on")


@app.command()
def stats(
    days: int = typer.Option(30, "--days", min=0, help="Only what was uploaded in the last N days (0 = everything)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the request; send nothing"),
) -> None:
    """Views, likes and comments (totals so far) of everything in published.csv → stats.csv and a table."""
    config = get_config()
    everything = log.read_published(config.root / log.PUBLISHED_NAME)
    rows = log.recent_rows(everything, days)
    if not rows:
        say(f"nothing was uploaded in the last {days} days ({len(everything)} rows in {log.PUBLISHED_NAME})"
            if everything else f"{log.PUBLISHED_NAME} is empty — nothing has been uploaded with beatpub yet")
        if everything:
            say("`beatpub stats --days 0` shows everything")
        return
    ids = [row["videoId"] for row in rows]
    if dry_run:
        say("dry run — this read-only request would be sent:")
        for start in range(0, len(ids), 50):
            say(f"  GET {API}/youtube/v3/videos?part=statistics&maxResults=50&id={','.join(ids[start:start + 50])}")
        say(f"result would be written to {config.root / log.STATS_NAME}")
        return
    try:
        numbers = youtube.fetch_stats(youtube.get_service(config), ids)
    except YouTubeError as exc:
        raise fail(str(exc)) from None
    lines = log.stats_rows(rows, numbers, log.now_iso())
    log.write_stats(config.root / log.STATS_NAME, lines)

    for kind in dict.fromkeys(line["kind"] for line in lines):
        is_short = kind == "short"
        table = Table(title=("Shorts" if is_short else "Videos" if kind == "video" else kind) + " — sorted by views",
                      title_justify="left")
        columns = [("title", "left"), ("views", "right"), ("likes", "right"), ("comments", "right"), ("uploaded", "left")]
        columns += [("layout", "left"), ("hook", "left")] if is_short else []
        for name, justify in columns:
            table.add_column(name, justify=justify, overflow="fold")
        for line in (l for l in lines if l["kind"] == kind):
            cells = [Text(line["title"])]
            cells += [f"{line[k]:,}" for k in ("views", "likes", "comments")] if line["found"] else ["gone", "", ""]
            cells.append(line["uploadedAt"][:10])
            cells += [line["short_layout"], Text(line["hook_text"])] if is_short else []
            table.add_row(*cells)
        console.print(table)
    missing = sum(1 for line in lines if not line["found"])
    if missing:
        say(f"note: {missing} upload(s) are no longer on YouTube (deleted?) — shown as \"gone\"")
    say(f"wrote {config.root / log.STATS_NAME}  (totals since upload; the --days filter is by upload date)")


# ----------------------------------------------------------------------------- YouTube: auth + learn
@app.command()
def auth(
    revoke: bool = typer.Option(False, "--revoke", help="Log out: revoke access at Google and delete token.json"),
    force: bool = typer.Option(False, "--force", help="Log in again even if a login is already saved"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Say what would happen; contact nobody"),
) -> None:
    """Log in to YouTube in the browser (once); the login is kept in token.json."""
    config = get_config()
    try:
        if revoke:
            if dry_run:
                say(f"dry run — would POST the saved token to {youtube.REVOKE_URL} and delete {config.token_path.name}")
                return
            say(youtube.revoke(config))
            return
        if dry_run:
            youtube.check_client_secret(config.client_secret_path)
            say("dry run — would open the browser for Google's consent screen "
                f"(scope {youtube.SCOPES[0]}) and save the login to {config.token_path.name}")
            return
        if youtube.has_token(config) and not force:
            try:
                channel = youtube.my_channel(youtube.get_service(config))
                say(f"already logged in — channel: {channel['title']}")
                say("(`beatpub auth --force` logs in again, `beatpub auth --revoke` logs out)")
                return
            except YouTubeError as exc:
                say(f"the saved login no longer works: {exc}")
                say("logging in again …")
        youtube.login(config)
        channel = youtube.my_channel(youtube.get_service(config))
    except YouTubeError as exc:
        raise fail(str(exc)) from None
    say(f"logged in — channel: {channel['title']}")
    say(f"the login is saved in {config.token_path.name} (keep it private; it is gitignored)")
    say("next: beatpub learn")


@app.command()
def learn(
    max_videos: int = typer.Option(50, "--max", min=1, max=500, help="How many of the newest videos to read"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the API calls; contact nobody"),
) -> None:
    """Read your own channel's videos → templates/learned/examples.json, plus what your best titles and tags are."""
    config = get_config()
    templates = config.root / "templates"
    if dry_run:
        say("dry run — these read-only requests would be sent (nothing is changed on YouTube):")
        for line in youtube.learn_plan(max_videos):
            say("  " + line)
        say(f"result would be written to {templates / 'learned' / youtube.EXAMPLES_NAME}")
        return
    try:
        data = youtube.learn(youtube.get_service(config), max_videos)
        path = youtube.save_examples(templates, data)
    except YouTubeError as exc:
        raise fail(str(exc)) from None
    videos = data["videos"]
    say(f"channel: {data['channel']} — {len(videos)} videos read → {path}")
    if not videos:
        say("no public videos found on this channel yet")
        return

    shapes = Table(title="Most common title shapes (sorted by views)", title_justify="left")
    for column, justify in (("shape", "left"), ("videos", "right"), ("views", "right"), ("best example", "left")):
        shapes.add_column(column, justify=justify, overflow="fold")
    for row in youtube.title_shapes(videos):
        shapes.add_row(Text(row["shape"]), str(row["videos"]), f"{row['views']:,}", Text(row["example"]))
    console.print(shapes)
    total_views = sum(v["viewCount"] for v in videos)
    shown = youtube.title_shapes(videos)
    rest = len(videos) - sum(r["videos"] for r in shown)
    if rest:
        say(f"({rest} more videos with other title shapes have {total_views - sum(r['views'] for r in shown):,} views; "
            f"all {len(videos)} videos together: {total_views:,})")

    tags = Table(title="Tags (sorted by the views of the videos that use them)", title_justify="left")
    for column, justify in (("tag", "left"), ("videos", "right"), ("views", "right"), ("avg views", "right")):
        tags.add_column(column, justify=justify)
    every_row = youtube.tag_frequency(videos)
    # A tag that is on every video has the whole channel's numbers — it says nothing, so it is not a table row.
    on_all = [row for row in every_row if row["videos"] == len(videos)] if len(videos) > 1 else []
    rows = [row for row in every_row if row["videos"] != len(videos)] if on_all else every_row
    for row in rows[:40]:
        tags.add_row(Text(row["tag"]), str(row["videos"]), f"{row['views']:,}", f"{row['avg_views']:,}")
    if rows:
        console.print(tags)
        say("a video's views count for each of its tags, so these totals overlap and add up to more than the channel's")
    if len(rows) > 40:
        say(f"({len(rows) - 40} more tags are in the file)")
    if on_all:
        say(f"{len(on_all)} tags are on all {len(videos)} videos, so each one simply has the channel's numbers "
            f"({total_views:,} views, {round(total_views / len(videos)):,} per video) and they cannot be compared:")
        say("  " + ", ".join(row["tag"] for row in on_all))
    untagged = sum(1 for v in videos if not v["tags"])
    if untagged:
        say(f"note: {untagged} of {len(videos)} videos have no tags")
    say("compare the top tags with templates/tags.yaml; `beatpub draft <slug> --ai` also uses this file")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context,
          version: bool = typer.Option(False, "--version", help="Show the version and exit.", is_eager=True)) -> None:
    if version:
        console.print(f"beatpub {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


def main() -> None:
    # A cp1250/cp852 Windows console or a redirected output must not crash on emoji or "→".
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    app()


if __name__ == "__main__":
    main()
