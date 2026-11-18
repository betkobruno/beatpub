#!/usr/bin/env python3
"""
render.py — turn a beat (WAV, MP3, FLAC, AIFF…) + a cover photo (JPG, PNG, WEBP…) into:
  out/video.mp4   1920x1080, H.264 + AAC, ready for YouTube
  out/beat.mp3    320 kbps MP3 with ID3 tags + embedded artwork
  out/thumb.jpg   1280x720 JPEG (< 2 MB) for the YouTube thumbnail
  out/frame.png   the composited 1080p frame used for the video

Requires: ffmpeg + ffprobe on PATH, Pillow, mutagen
  pip install pillow mutagen

Examples
  python scripts/render.py --audio beat.wav --cover cover.jpg --out out
  python scripts/render.py --audio beat.mp3 --cover cover.png --out out \
      --title "Midnight" --bpm 142 --key "F minor" --text --zoom

This is the reference implementation of the render step described in CLAUDE.md.
It is deliberately a single file with no project imports so it can be run on its own.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1920, 1080
THUMB_W, THUMB_H = 1280, 720
THUMB_MAX_BYTES = 2 * 1024 * 1024  # YouTube limit for custom thumbnails
ARTWORK_MAX_PX = 1400  # embedded MP3 artwork is resized to fit this

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


# ----------------------------------------------------------------------------- helpers
def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def need_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        die(f"{name} not found on PATH. Install ffmpeg (it ships ffprobe too) and reopen the terminal.")
    return path


def run(cmd: list[str], dry_run: bool = False) -> None:
    print("  $ " + " ".join(quote(c) for c in cmd))
    if dry_run:
        return
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        die(f"command failed with exit code {proc.returncode}")


def quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "'" in s) else s


def probe(path: Path) -> dict:
    """Return the ffprobe JSON for a media file (format + streams)."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if out.returncode != 0:
        die(f"ffprobe failed on {path}: {out.stderr.strip()}")
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
        die(f"could not read duration of {path}")
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
    """Open the cover, honour phone EXIF rotation, drop alpha."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    return img.convert("RGB")


# ----------------------------------------------------------------------------- beat folder + file name
AUDIO_EXT = (".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
BPM_RE = re.compile(r"(\d{2,3})\s*bpm", re.I)
KEY_RE = re.compile(r"(?<![A-Za-z])([A-G])([#\u266f]|b|\u266d)?\s*(?i:(minor|min|m|major|maj))?(?![A-Za-z0-9])")
HANDLE_RE = re.compile(r"@([A-Za-z0-9_.]+)")


def parse_beat_name(stem: str) -> dict:
    """
    'Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill'
      → title 'Tuesday Night', bpm 160, key 'A# minor', handles [caprybeats, sinfulyts, 1parkerhill]
    Anything not found is left empty; the command-line flags always win.
    """
    info = {"title": "", "bpm": 0, "key": "", "handles": HANDLE_RE.findall(stem)}
    s = HANDLE_RE.sub(" ", stem.replace("_", " "))
    m = BPM_RE.search(s)
    if m:
        info["bpm"] = int(m.group(1))
        title, rest = s[: m.start()], s[m.end():]
    else:
        title, rest = s, ""
    km, in_title = KEY_RE.search(rest), False
    if not km:                                   # key written before the BPM: '... A#m 160 Bpm'
        tail = title[-7:]
        km, in_title = KEY_RE.search(tail), True
    if km:
        acc = {"#": "#", "\u266f": "#", "b": "b", "\u266d": "b"}.get(km.group(2) or "", "")
        mode = (km.group(3) or "").lower()
        info["key"] = km.group(1) + acc + (" minor" if mode.startswith("m") and not mode.startswith("maj") else " major")
        if in_title:
            title = title[: len(title) - 7 + km.start()]
    info["title"] = re.sub(r"[\s\-\u2013|]+$", "", title).strip() or stem
    return info


def find_inputs(beat: Path) -> tuple[Path, Path | None]:
    """beat = a folder (first audio + first image inside) or an audio file (image looked up next to it)."""
    folder = beat if beat.is_dir() else beat.parent
    if beat.is_dir():
        audios = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT)
        if not audios:
            die(f"no audio file ({', '.join(AUDIO_EXT)}) in {folder}")
        wavs = [p for p in audios if p.suffix.lower() == ".wav"]
        audio = (wavs or audios)[0]
        if len(audios) > 1:
            print(f"note: {len(audios)} audio files in {folder.name}, using {audio.name}")
    else:
        audio = beat
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)
    return audio, (images[0] if images else None)


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
        draw = ImageDraw.Draw(bg)
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


# ----------------------------------------------------------------------------- mp3 tags
def tag_mp3(mp3: Path, cover_jpeg: Path, args: argparse.Namespace) -> None:
    from mutagen.id3 import APIC, COMM, ID3, TALB, TBPM, TDRC, TIT2, TKEY, TPE1
    from mutagen.mp3 import MP3

    audio = MP3(mp3)
    try:
        tags = ID3(mp3)
    except Exception:
        audio.add_tags()
        tags = audio.tags
    tags.delete(mp3)  # start clean
    tags = ID3()
    if args.title:
        tags.add(TIT2(encoding=3, text=args.title))
    tags.add(TPE1(encoding=3, text=" x ".join([args.artist] + getattr(args, "collabs", []))))
    if args.album:
        tags.add(TALB(encoding=3, text=args.album))
    if args.bpm:
        tags.add(TBPM(encoding=3, text=str(args.bpm)))
    if args.key:
        tags.add(TKEY(encoding=3, text=args.key))
    if args.year:
        tags.add(TDRC(encoding=3, text=str(args.year)))
    if args.comment:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=args.comment))
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_jpeg.read_bytes()))
    tags.save(mp3, v2_version=3)  # ID3v2.3 = widest player compatibility


# ----------------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description="Render a beat video, MP3 and thumbnail with ffmpeg.")
    p.add_argument("--beat", type=Path, help="beat folder (first audio + first image inside) or the audio file itself; "
                   "title / BPM / key / @handles are read from the file name")
    p.add_argument("--audio", "--wav", dest="audio", type=Path,
                   help="source audio: WAV (best), MP3, FLAC, AIFF… anything ffmpeg reads (--wav still works)")
    p.add_argument("--cover", type=Path, help="cover photo: JPG, PNG, WEBP… (not HEIC — export it first)")
    p.add_argument("--out", type=Path, help="output folder (default: out/ next to the audio)")
    p.add_argument("--collabs", default="", help="co-producers, comma separated; default: @handles from the file name")
    p.add_argument("--title", default="", help="beat title (used in MP3 tags and optional overlay)")
    p.add_argument("--artist", default="CapryBeats")
    p.add_argument("--album", default="")
    p.add_argument("--bpm", type=int, default=0)
    p.add_argument("--key", default="", help='musical key, e.g. "F minor"')
    p.add_argument("--year", type=int, default=0)
    p.add_argument("--comment", default="", help="MP3 comment tag, e.g. the beat store URL")
    p.add_argument("--text", action="store_true", help="draw title + prod./BPM/key text on the frame")
    p.add_argument("--layout", choices=["fit", "fill"], default="fit",
                   help="fit = picture as big as possible without cropping (default); fill = cropped to cover the whole frame")
    p.add_argument("--margin", type=float, default=0.0, help="fraction of the height to leave around the picture (fit only), e.g. 0.05")
    p.add_argument("--zoom", action="store_true", help="slow zoom instead of a static frame (slower render)")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=20, help="x264 quality, lower = better/bigger (18-23 sane)")
    p.add_argument("--preset", default="medium", help="x264 preset (veryfast .. slow)")
    p.add_argument("--audio-bitrate", default="384k", help="AAC bitrate in the video (YouTube recommends 384k stereo)")
    p.add_argument("--mp3-bitrate", default="320k")
    p.add_argument("--skip-mp3", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print the ffmpeg commands without running them")
    args = p.parse_args()

    need_tool("ffmpeg")
    need_tool("ffprobe")
    if args.beat:
        if not args.beat.exists():
            die(f"not found: {args.beat}")
        found_audio, found_cover = find_inputs(args.beat)
        args.audio = args.audio or found_audio
        args.cover = args.cover or found_cover
    if not args.audio:
        die("give --beat <folder or audio file>, or --audio and --cover")
    if not args.audio.is_file():
        die(f"audio not found: {args.audio}")
    if not args.cover:
        die(f"no cover image ({', '.join(IMAGE_EXT)}) next to {args.audio.name} — add one or pass --cover")
    if not args.cover.is_file():
        die(f"cover not found: {args.cover}")
    args.out = args.out or args.audio.parent / "out"

    # title / BPM / key / collaborators from the file name unless given on the command line
    parsed = parse_beat_name(args.audio.stem)
    args.title = args.title or parsed["title"]
    args.bpm = args.bpm or parsed["bpm"]
    args.key = args.key or parsed["key"]
    collabs = [c.strip().lstrip("@") for c in args.collabs.split(",") if c.strip()] if args.collabs else \
        [h for h in parsed["handles"] if h.lower() != args.artist.lower()]
    args.collabs = collabs
    print(f'beat: "{args.title}"  {args.bpm or "?"} BPM  {args.key or "key ?"}'
          + (f"  with {', '.join(collabs)}" if collabs else "")
          + ("  (from the file name)" if parsed["title"] and args.title == parsed["title"] else ""))
    if args.cover.suffix.lower() in (".heic", ".heif"):
        die("HEIC photos aren't supported — send the photo to yourself via WhatsApp/mail (it becomes a JPG) or export it as JPG/PNG")
    args.out.mkdir(parents=True, exist_ok=True)

    source_is_mp3 = args.audio.suffix.lower() == ".mp3"
    duration = audio_duration(args.audio)
    print(f"audio: {args.audio.name}  {duration:.2f} s")
    if source_is_mp3:
        print("note: MP3 source — fine for YouTube, but a WAV export gives the video a cleaner audio track")

    # 1) frame + thumbnail + artwork (Pillow)
    cover = load_cover(args.cover)
    text = None
    if args.text:
        sub_bits = ["prod. " + " x ".join([args.artist] + args.collabs)]
        if args.bpm:
            sub_bits.append(f"{args.bpm} BPM")
        if args.key:
            sub_bits.append(args.key)
        text = {"title": args.title, "sub": "  ·  ".join(sub_bits)}

    frame_size = (W * 2, H * 2) if args.zoom else (W, H)  # oversample for zoompan to avoid jitter
    frame = build_frame(cover, frame_size, text=text, layout=args.layout, margin_frac=args.margin)
    frame_path = args.out / "frame.png"      # PNG (RGB) → ffmpeg converts to limited-range yuv420p itself
    frame.save(frame_path, "PNG", compress_level=3)

    thumb = frame.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS) if not args.zoom else \
        build_frame(cover, (W, H), text=text, layout=args.layout, margin_frac=args.margin).resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    thumb_path = args.out / "thumb.jpg"
    save_jpeg_under(thumb, thumb_path, THUMB_MAX_BYTES)

    art = cover.copy()
    art.thumbnail((ARTWORK_MAX_PX, ARTWORK_MAX_PX), Image.Resampling.LANCZOS)
    art_path = args.out / "artwork.jpg"
    art.save(art_path, "JPEG", quality=90)
    print(f"frame: {frame_path}  thumb: {thumb_path} ({thumb_path.stat().st_size // 1024} kB)")

    # 2) video (ffmpeg)
    video_path = args.out / "video.mp4"
    total_frames = int(round(duration * args.fps))
    common_out = [
        "-c:v", "libx264", "-preset", args.preset, "-tune", "stillimage", "-crf", str(args.crf),
        "-pix_fmt", "yuv420p", "-r", str(args.fps),
        "-c:a", "aac", "-b:a", args.audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart", "-t", f"{duration:.3f}", "-shortest",
        str(video_path),
    ]
    if args.zoom:
        # one input frame → zoompan generates `total_frames` frames with a slow 1.0→1.08 zoom
        zf = f"zoompan=z='1+0.08*on/{total_frames}':d={total_frames}" \
             f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={args.fps}"
        cmd = ["ffmpeg", "-y", "-i", str(frame_path), "-i", str(args.audio),
               "-filter_complex", f"[0:v]{zf},format=yuv420p[v]", "-map", "[v]", "-map", "1:a"] + common_out
    else:
        cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(args.fps), "-i", str(frame_path),
               "-i", str(args.audio), "-map", "0:v", "-map", "1:a"] + common_out
    print("video:")
    run(cmd, args.dry_run)

    # 3) mp3 + tags
    mp3_path = args.out / "beat.mp3"
    if not args.skip_mp3:
        if source_is_mp3:
            print("mp3: source is already MP3 — copying it as is (no second lossy encode), then tagging")
            if not args.dry_run:
                shutil.copyfile(args.audio, mp3_path)
        else:
            print("mp3:")
            run(["ffmpeg", "-y", "-i", str(args.audio), "-vn", "-c:a", "libmp3lame", "-b:a", args.mp3_bitrate,
                 str(mp3_path)], args.dry_run)
        if not args.dry_run:
            tag_mp3(mp3_path, art_path, args)

    if args.dry_run:
        print("dry run — nothing rendered")
        return

    # 4) verify
    v = probe(video_path)
    vs = next(s for s in v["streams"] if s["codec_type"] == "video")
    vd = float(v["format"]["duration"])
    ok = vs["width"] == W and vs["height"] == H and abs(vd - duration) < 0.5 and vs.get("pix_fmt") in ("yuv420p", "yuvj420p")
    if vs.get("pix_fmt") == "yuvj420p":
        print("note: ffmpeg tagged the video full-range (yuvj420p); YouTube accepts it")
    print(f"\ncheck: {vs['width']}x{vs['height']} {vs.get('pix_fmt')}  video {vd:.2f} s vs audio {duration:.2f} s"
          f"  size {video_path.stat().st_size / 1e6:.1f} MB  → {'OK' if ok else 'MISMATCH'}")
    if not args.skip_mp3:
        print(f"mp3: {mp3_path}  {mp3_path.stat().st_size / 1e6:.1f} MB  (tags: title/artist/bpm/key/artwork)")
    if not ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
