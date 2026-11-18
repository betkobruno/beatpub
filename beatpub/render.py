"""
The 16:9 video step: frame, thumbnail, artwork, ffmpeg video, MP3 + ID3 tags.
A port of scripts/render.py (the tested reference) into functions — keep the two in sync.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .meta import Meta, credit_line, find_inputs

Say = Callable[[str], None]

W, H = 1920, 1080
THUMB_W, THUMB_H = 1280, 720
THUMB_MAX_BYTES = 2 * 1024 * 1024  # YouTube limit for custom thumbnails
ARTWORK_MAX_PX = 1400              # embedded MP3 artwork is resized to fit this
STATE_NAME = "render_state.json"   # what the outputs in out/ were made from (for skipping)
VIDEO_OUTPUTS = ("frame.png", "thumb.jpg", "artwork.jpg", "video.mp4", "beat.mp3")

FFMPEG_HINT = ("install ffmpeg with `winget install -e --id Gyan.FFmpeg` (Windows) or "
               "`brew install ffmpeg` (macOS), then reopen the terminal")

FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # names Pillow may resolve on its own
    "arialbd.ttf",
    "DejaVuSans-Bold.ttf",
]


class RenderError(Exception):
    """Rendering failed; the message is safe to show. `code` is the process exit code."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------------- helpers
def need_tools() -> None:
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise RenderError(f"{name} not found on PATH — {FFMPEG_HINT}")


def quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "'" in s) else s


def run(cmd: list[str], say: Say = print, dry_run: bool = False) -> None:
    """Print the command, then run it (never through a shell)."""
    say("  $ " + " ".join(quote(c) for c in cmd))
    if dry_run:
        return
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RenderError(f"{cmd[0]} failed with exit code {proc.returncode}:\n{proc.stderr[-4000:]}")


def probe(path: Path) -> dict:
    """The ffprobe JSON for a media file (format + streams)."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RenderError(f"ffprobe failed on {path.name}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def audio_duration(path: Path) -> float:
    info = probe(path)
    dur = info.get("format", {}).get("duration")
    if dur is None:
        for s in info.get("streams", []):
            if s.get("codec_type") == "audio" and s.get("duration"):
                dur = s["duration"]
                break
    if dur is None:
        raise RenderError(f"could not read the duration of {path.name}")
    return float(dur)


def load_font(size: int) -> ImageFont.ImageFont:
    for cand in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def load_cover(path: Path) -> Image.Image:
    """Open the cover, honour phone EXIF rotation, flatten alpha on black."""
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (0, 0, 0))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        return img.convert("RGB")
    except OSError as exc:
        raise RenderError(f"could not open the cover {path.name}: {exc}") from None


# ----------------------------------------------------------------------------- skipping finished work
def fingerprint(inputs: list[Path], params: dict[str, Any]) -> dict[str, Any]:
    """What a set of outputs depends on: the input files (name, size, mtime) and the settings."""
    files = [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in inputs]
    return {"inputs": files, "params": params}


def load_state(out: Path) -> dict[str, Any]:
    try:
        data = json.loads((out / STATE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_current(out: Path, section: str, fp: dict[str, Any], outputs: tuple[str, ...]) -> bool:
    """True when every output exists and was made from exactly these inputs and settings."""
    return all((out / name).is_file() for name in outputs) and load_state(out).get(section) == fp


def save_state(out: Path, section: str, fp: dict[str, Any]) -> None:
    state = load_state(out)
    state[section] = fp
    (out / STATE_NAME).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------- frame
def build_frame(
    cover: Image.Image,
    size: tuple[int, int],
    text: dict | None = None,
    blur_radius: int = 40,
    darken: float = 0.45,
    layout: str = "fit",
    margin_frac: float = 0.0,
) -> Image.Image:
    """
    layout "fit"  (default): the cover as big as it goes without cropping — full frame
                   height for a portrait photo, full width for a landscape — centred on a
                   blurred, darkened copy of itself. `margin_frac` (fraction of the frame
                   height) shrinks it a little if you want breathing room.
    layout "fill": the cover cropped to 16:9 so it covers the whole frame.
    The cover is scaled UP as well as down (a small phone picture still fills the frame).
    `text` (optional) = {"title": ..., "sub": ...} drawn bottom-left.
    """
    w, h = size

    if layout == "fill":
        bg = ImageOps.fit(cover, (w, h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    else:
        # background: cover scaled to FILL the frame, blurred, darkened
        bg = ImageOps.fit(cover, (w, h), method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(blur_radius))
        overlay = Image.new("RGB", (w, h), (0, 0, 0))
        bg = Image.blend(bg, overlay, darken)

        # foreground: cover scaled (up or down) to fit inside the frame
        margin = int(h * margin_frac)
        scale = min((w - 2 * margin) / cover.width, (h - 2 * margin) / cover.height)
        fg = cover.resize((max(1, round(cover.width * scale)), max(1, round(cover.height * scale))),
                          Image.Resampling.LANCZOS)
        x = (w - fg.width) // 2
        y = (h - fg.height) // 2
        bg.paste(fg, (x, y))

    if text and (text.get("title") or text.get("sub")):
        pad = int(h * 0.04)
        title_font = load_font(int(h * 0.075))
        sub_font = load_font(int(h * 0.035))
        # bottom gradient so text reads on any photo
        grad = Image.new("L", (1, h), 0)
        for i in range(h):
            v = max(0, int(255 * ((i - h * 0.55) / (h * 0.45)) ** 1.5)) if i > h * 0.55 else 0
            grad.putpixel((0, i), min(v, 200))
        shade = Image.new("RGB", (w, h), (0, 0, 0))
        bg.paste(shade, (0, 0), grad.resize((w, h)))
        draw = ImageDraw.Draw(bg)
        y_cursor = h - pad
        if text.get("sub"):
            y_cursor -= sub_font.size
            draw.text((pad, y_cursor), text["sub"], font=sub_font, fill=(235, 235, 235))
            y_cursor -= int(sub_font.size * 0.5)
        if text.get("title"):
            y_cursor -= title_font.size
            draw.text((pad, y_cursor), text["title"], font=title_font, fill=(255, 255, 255))
    return bg


def save_jpeg_under(img: Image.Image, path: Path, max_bytes: int, start_quality: int = 92) -> None:
    q = start_quality
    while True:
        img.save(path, "JPEG", quality=q, optimize=True, progressive=True)
        if path.stat().st_size <= max_bytes or q <= 40:
            return
        q -= 8


def make_thumbnail(frame_1080: Image.Image, path: Path) -> None:
    """The frame at 1280×720 as a JPEG under YouTube's 2 MB thumbnail limit."""
    thumb = frame_1080.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    save_jpeg_under(thumb, path, THUMB_MAX_BYTES)


def make_artwork(cover: Image.Image, path: Path) -> None:
    """The cover itself, max 1400 px on the long side, for the MP3's APIC tag."""
    art = cover.copy()
    art.thumbnail((ARTWORK_MAX_PX, ARTWORK_MAX_PX), Image.Resampling.LANCZOS)
    art.save(path, "JPEG", quality=90)


def frame_text(meta: Meta, artist: str) -> dict | None:
    """The optional overlay: title + 'prod. … · 142 BPM · F minor'."""
    if not meta.text_overlay:
        return None
    sub_bits = [credit_line(artist, meta.collabs)]
    if meta.bpm:
        sub_bits.append(f"{meta.bpm} BPM")
    if meta.key:
        sub_bits.append(meta.key)
    return {"title": meta.title, "sub": "  ·  ".join(sub_bits)}


# ----------------------------------------------------------------------------- video
def video_command(frame: Path, audio: Path, video: Path, duration: float, *, zoom: bool = False,
                  fps: int = 30, crf: int = 20, preset: str = "medium", audio_bitrate: str = "384k") -> list[str]:
    total_frames = int(round(duration * fps))
    common_out = [
        "-c:v", "libx264", "-preset", preset, "-tune", "stillimage", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2",   # stereo even for a mono source
        "-movflags", "+faststart", "-t", f"{duration:.3f}", "-shortest",
        str(video),
    ]
    if zoom:
        # one input frame → zoompan generates `total_frames` frames with a slow 1.0→1.08 zoom
        zf = (f"zoompan=z='1+0.08*on/{total_frames}':d={total_frames}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps}")
        return ["ffmpeg", "-y", "-i", str(frame), "-i", str(audio),
                "-filter_complex", f"[0:v]{zf},format=yuv420p[v]", "-map", "[v]", "-map", "1:a"] + common_out
    return ["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(frame),
            "-i", str(audio), "-map", "0:v", "-map", "1:a"] + common_out


def encode_video(frame: Path, audio: Path, video: Path, duration: float, *, zoom: bool = False,
                 fps: int = 30, crf: int = 20, preset: str = "medium", say: Say = print,
                 dry_run: bool = False) -> None:
    run(video_command(frame, audio, video, duration, zoom=zoom, fps=fps, crf=crf, preset=preset), say, dry_run)


# ----------------------------------------------------------------------------- mp3
def encode_mp3(audio: Path, mp3: Path, *, bitrate: str = "320k", say: Say = print, dry_run: bool = False) -> None:
    """320 kbps MP3 — or an unchanged copy when the source already is one. Never touches the source."""
    if audio.suffix.lower() == ".mp3":
        say("mp3: source is already MP3 — copying it as is (no second lossy encode); a WAV export is better")
        if not dry_run:
            shutil.copyfile(audio, mp3)
        return
    say("mp3:")
    run(["ffmpeg", "-y", "-i", str(audio), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate, str(mp3)], say, dry_run)


def tag_mp3(mp3: Path, artwork: Path, *, title: str, artist: str, bpm: int = 0, key: str = "",
            year: int = 0, comment: str = "") -> None:
    """Replace all tags with ID3v2.3: TIT2, TPE1, TBPM, TKEY, TDRC, COMM, APIC."""
    from mutagen.id3 import APIC, COMM, ID3, TBPM, TDRC, TIT2, TKEY, TPE1, ID3NoHeaderError

    try:
        ID3(mp3).delete(mp3)                     # start clean (a copied MP3 may carry old tags)
    except ID3NoHeaderError:
        pass
    tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    if bpm:
        tags.add(TBPM(encoding=3, text=str(bpm)))
    if key:
        tags.add(TKEY(encoding=3, text=key))
    if year:
        tags.add(TDRC(encoding=3, text=str(year)))
    if comment:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=artwork.read_bytes()))
    tags.save(mp3, v2_version=3)                 # ID3v2.3 = widest player compatibility


# ----------------------------------------------------------------------------- verify
def verify(video: Path, duration: float, say: Say = print) -> None:
    """ffprobe the result: 1920×1080, yuv420p, same length as the audio. RenderError(code 2) if not."""
    v = probe(video)
    vs = next(s for s in v["streams"] if s["codec_type"] == "video")
    vd = float(v["format"]["duration"])
    ok = (vs["width"] == W and vs["height"] == H and abs(vd - duration) < 0.5
          and vs.get("pix_fmt") in ("yuv420p", "yuvj420p"))
    if vs.get("pix_fmt") == "yuvj420p":
        say("note: ffmpeg tagged the video full-range (yuvj420p); YouTube accepts it")
    say(f"check: {vs['width']}x{vs['height']} {vs.get('pix_fmt')}  video {vd:.2f} s vs audio {duration:.2f} s"
        f"  size {video.stat().st_size / 1e6:.1f} MB  → {'OK' if ok else 'MISMATCH'}")
    if not ok:
        raise RenderError(f"{video.name} does not match the spec (expected {W}x{H} yuv420p, {duration:.2f} s)", code=2)


# ----------------------------------------------------------------------------- the whole step
def render_beat(folder: Path, meta: Meta, artist: str, *, force: bool = False, dry_run: bool = False,
                preset: str = "medium", crf: int = 20, fps: int = 30, say: Say = print) -> bool:
    """
    beats/<slug> → out/frame.png thumb.jpg artwork.jpg video.mp4 beat.mp3.
    Returns False when the outputs were already current (nothing done).
    """
    need_tools()
    audio, cover_path = find_inputs(folder)
    out = folder / "out"
    credit = credit_line(artist, meta.collabs)
    fp = fingerprint([audio, cover_path], {
        "title": meta.title, "credit": credit, "bpm": meta.bpm, "key": meta.key,
        "comment": meta.beatstars_url, "text_overlay": meta.text_overlay, "zoom": meta.zoom,
        "layout": meta.video_layout, "margin": meta.video_margin,
    })
    if not force and not dry_run and is_current(out, "video", fp, VIDEO_OUTPUTS):
        say(f"video: up to date in {out} — nothing to do (use --force to render again)")
        return False

    duration = audio_duration(audio)
    say(f"audio: {audio.name}  {duration:.2f} s")
    say(f"cover: {cover_path.name}")
    frame_path, thumb_path, art_path = out / "frame.png", out / "thumb.jpg", out / "artwork.jpg"
    video_path, mp3_path = out / "video.mp4", out / "beat.mp3"

    # 1) frame + thumbnail + artwork (Pillow)
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)
        cover = load_cover(cover_path)
        text = frame_text(meta, artist)
        frame_1080 = build_frame(cover, (W, H), text=text, layout=meta.video_layout, margin_frac=meta.video_margin)
        # zoom: oversample the frame so zoompan does not jitter
        frame = build_frame(cover, (W * 2, H * 2), text=text, layout=meta.video_layout,
                            margin_frac=meta.video_margin) if meta.zoom else frame_1080
        frame.save(frame_path, "PNG", compress_level=3)   # PNG (RGB) → ffmpeg makes limited-range yuv420p
        make_thumbnail(frame_1080, thumb_path)
        make_artwork(cover, art_path)
        say(f"frame: {frame_path.name}  thumb: {thumb_path.name} ({thumb_path.stat().st_size // 1024} kB)")

    # 2) video
    say("video:" + ("  (slow zoom — this takes longer)" if meta.zoom else ""))
    encode_video(frame_path, audio, video_path, duration, zoom=meta.zoom, fps=fps, crf=crf, preset=preset,
                 say=say, dry_run=dry_run)

    # 3) mp3 + tags
    encode_mp3(audio, mp3_path, say=say, dry_run=dry_run)
    if dry_run:
        say("dry run — nothing rendered")
        return True
    tag_mp3(mp3_path, art_path, title=meta.title, artist=credit.removeprefix("prod. "), bpm=meta.bpm,
            key=meta.key, year=datetime.now().year, comment=meta.beatstars_url)

    # 4) verify
    verify(video_path, duration, say)
    say(f"mp3: {mp3_path.name}  {mp3_path.stat().st_size / 1e6:.1f} MB  (tags: title/artist/bpm/key/artwork)")
    save_state(out, "video", fp)
    return True
