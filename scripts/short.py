#!/usr/bin/env python3
"""
short.py — cut a vertical YouTube Short (9:16) out of a beat.

  audio (WAV/MP3/…) + cover (JPG/PNG/…)  →  out/short.mp4        1080x1920, H.264/AAC
                                            out/short_cover.jpg  9:16 thumbnail (the first frame)
                                            out/short_frame.png  the composited still
                                            out/short_pick.json  which section was chosen and why

Default look: the photo fills the whole frame and nothing else — no text, no waveform.
`--text` adds the title and the "prod." credit; `--hook "…"` adds one line under the
picture; `--wave` adds a moving waveform across the lower part. The section is picked automatically: the loudest window of N
whole bars (BPM-aligned), with a bonus for windows that start on a drop. `--start` forces it.

Requires: ffmpeg + ffprobe on PATH, Pillow, numpy.   pip install pillow numpy

Examples
  python scripts/short.py --beat beats/tuesday-night
  python scripts/short.py --beat beats/tuesday-night --start 1:04 --length 30 --text
  python scripts/short.py --audio beat.wav --cover cover.jpg --out out --title "Midnight" --bpm 142

Reference implementation of the Short step in CLAUDE.md. Single file, no project imports.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1920
FPS_DEFAULT = 30
WAVE_H = 200          # height of the waveform band
WAVE_Y = 1330         # top of the waveform band
# The Shorts player covers roughly the top 220 px and the bottom 320 px (title, channel,
# buttons) and a ~150 px strip on the right. Everything that matters stays inside the middle.
SAFE_TOP, SAFE_BOTTOM = 240, 1600

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
    "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "arialbd.ttf", "DejaVuSans-Bold.ttf",
]


# ----------------------------------------------------------------------------- helpers
def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def need_tool(name: str) -> None:
    if not shutil.which(name):
        die(f"{name} not found on PATH. Install ffmpeg (ships ffprobe too) and reopen the terminal.")


def quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "'" in s) else s


def run(cmd: list[str], dry_run: bool = False) -> None:
    print("  $ " + " ".join(quote(c) for c in cmd))
    if dry_run:
        return
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        die(f"command failed with exit code {proc.returncode}")


def probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if out.returncode != 0:
        die(f"ffprobe failed on {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def parse_time(s: str) -> float:
    """'75', '75.5', '1:15', '1:15.5' → seconds."""
    s = s.strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


def load_font(size: int) -> ImageFont.ImageFont:
    for cand in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_cover(path: Path) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path))
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


# ----------------------------------------------------------------------------- section picker
def decode_mono(wav: Path, sr: int = 22050) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` via ffmpeg (no extra deps)."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(wav), "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        die(f"ffmpeg decode failed: {proc.stderr.decode(errors='replace')[-2000:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def pick_section(samples: np.ndarray, sr: int, duration: float, bpm: int | None,
                 target_len: float, offset: float = 0.0) -> dict:
    """
    Return {"start", "length", "bars", "score", "candidates"} for the best window.
    Windows start on bar lines (offset + k*bar) when bpm is known, else every 0.5 s.
    Score = mean RMS in dB, +3 dB bonus scaled by how much louder the first bar is
    than the bar before it (so a drop beats a sustained-loud outro).
    """
    hop = int(sr * 0.05)                                  # 50 ms frames
    n_frames = max(1, len(samples) // hop)
    frames = samples[: n_frames * hop].reshape(n_frames, hop)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-9)
    t_frame = 0.05

    if bpm and bpm > 0:
        bar = 240.0 / bpm
        bars = max(1, int(round(target_len / bar)))
        length = bars * bar
        starts = [offset + k * bar for k in range(int((duration - offset) / bar) + 1)]
    else:
        bar = None
        bars = 0
        length = target_len
        starts = [offset + k * 0.5 for k in range(int((duration - offset) / 0.5) + 1)]
    length = min(length, duration)
    starts = [s for s in starts if s + length <= duration + 1e-6]
    if not starts:
        return {"start": 0.0, "length": min(target_len, duration), "bars": bars, "score": 0.0, "candidates": 0}

    best = None
    for s in starts:
        a, b = int(s / t_frame), int((s + length) / t_frame)
        if b <= a:
            continue
        mean_db = float(np.mean(rms_db[a:b]))
        bonus = 0.0
        if bar:
            pre_a, pre_b = int(max(0, s - bar) / t_frame), a
            first_b = int((s + bar) / t_frame)
            if pre_b > pre_a and first_b > a:
                jump = float(np.mean(rms_db[a:first_b]) - np.mean(rms_db[pre_a:pre_b]))
                bonus = 3.0 * min(1.0, max(0.0, jump / 6.0))   # full bonus at a +6 dB jump
        score = mean_db + bonus
        if best is None or score > best["score"]:
            best = {"start": round(s, 3), "length": round(length, 3), "bars": bars,
                    "score": round(score, 2), "mean_db": round(mean_db, 2), "bonus": round(bonus, 2)}
    best["candidates"] = len(starts)
    return best


# ----------------------------------------------------------------------------- frame
def vertical_gradient(top_alpha: int, y0: int, y1: int, reverse: bool = False) -> Image.Image:
    """A black RGBA layer whose alpha ramps from top_alpha at y0 to 0 at y1 (or the reverse)."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    col = Image.new("L", (1, H), 0)
    for yy in range(y0, y1):
        t = (yy - y0) / max(1, y1 - y0)
        a = int(top_alpha * ((t if reverse else 1 - t) ** 1.4))
        col.putpixel((0, yy), a)
    layer.putalpha(col.resize((W, H)))
    return layer


def build_frame(cover: Image.Image, title: str, sub: str, hook: str, mark: str,
                layout: str = "fill", text: bool = True) -> Image.Image:
    """
    layout "fill": the photo covers the whole 9:16 frame (cropped to fit) — biggest picture.
    layout "wide": the photo at full width, uncropped, centred on a blurred background.
    layout "card": the photo in a 900x760 card on a blurred background (the original look).
    text=False draws nothing on top — just the picture (and the waveform, added by ffmpeg).
    """
    if layout == "fill":
        bg = ImageOps.fit(cover, (W, H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
        if text:
            bg.paste(vertical_gradient(170, 0, 520), (0, 0), vertical_gradient(170, 0, 520))
            bg.paste(vertical_gradient(200, 1080, H, reverse=True), (0, 0), vertical_gradient(200, 1080, H, reverse=True))
    else:
        # background: cover scaled to fill 9:16, blurred, darkened
        bg = ImageOps.fit(cover, (W, H), method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(45))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.5)
        if layout == "wide":
            box_w, box_h = W, (1400 if not text else 1100)
            scale = min(box_w / cover.width, box_h / cover.height)
            fg = cover.resize((max(1, round(cover.width * scale)), max(1, round(cover.height * scale))), Image.Resampling.LANCZOS)
            x, y = (W - fg.width) // 2, (H - fg.height) // 2 if not text else 400 + (1100 - fg.height) // 2
            bg.paste(fg, (x, y))
        else:  # card
            box_w, box_h = 900, 760
            scale = min(box_w / cover.width, box_h / cover.height)
            fg = cover.resize((max(1, round(cover.width * scale)), max(1, round(cover.height * scale))), Image.Resampling.LANCZOS)
            cx, cy = W // 2, 440 + box_h // 2
            x, y = cx - fg.width // 2, cy - fg.height // 2
            shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rounded_rectangle([x + 12, y + 18, x + fg.width + 12, y + fg.height + 18],
                                                     radius=24, fill=(0, 0, 0, 150))
            shadow = shadow.filter(ImageFilter.GaussianBlur(22))
            bg.paste(shadow, (0, 0), shadow)
            mask = Image.new("L", fg.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, fg.width - 1, fg.height - 1], radius=24, fill=255)
            bg.paste(fg, (x, y), mask)
    if not text:
        return bg

    draw = ImageDraw.Draw(bg)

    def centred(text: str, y: int, font: ImageFont.ImageFont, fill=(255, 255, 255), max_w: int = 960) -> int:
        """Draw wrapped, centred text starting at y; return the y after the last line."""
        lines = []
        for para in text.split("\n"):
            words, line = para.split(), ""
            for w_ in words:
                trial = (line + " " + w_).strip()
                if draw.textlength(trial, font=font) <= max_w:
                    line = trial
                else:
                    if line:
                        lines.append(line)
                    line = w_
            lines.append(line)
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            draw.text(((W - tw) / 2 + 3, y + 3), ln, font=font, fill=(0, 0, 0, 160))   # shadow
            draw.text(((W - tw) / 2, y), ln, font=font, fill=fill)
            y += int(font.size * 1.2)
        return y

    # title + sub line at the top (inside the safe zone)
    if title:
        title_font = load_font(84 if len(title) <= 14 else 68)
        y_end = centred(title, SAFE_TOP, title_font)
        if sub:
            centred(sub, y_end + 6, load_font(34), fill=(225, 225, 225))

    # hook under the cover
    if hook:
        centred(hook, 1215, load_font(46), fill=(255, 255, 255), max_w=900)

    # producer mark above the bottom UI zone (waveform sits between)
    if mark:
        centred(mark, 1548, load_font(38), fill=(235, 235, 235))
    return bg


# ----------------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(description="Cut a vertical YouTube Short (9:16) from a beat.")
    p.add_argument("--beat", type=Path, help="beat folder (first audio + first image inside) or the audio file itself; "
                   "title / BPM / key / @handles are read from the file name")
    p.add_argument("--audio", "--wav", dest="audio", type=Path,
                   help="source audio: WAV, MP3, FLAC, AIFF… anything ffmpeg reads (--wav still works)")
    p.add_argument("--cover", type=Path, help="cover photo: JPG, PNG, WEBP… (not HEIC)")
    p.add_argument("--out", type=Path, help="output folder (default: out/ next to the audio)")
    p.add_argument("--collabs", default="", help="co-producers, comma separated; default: @handles from the file name")
    p.add_argument("--title", default="")
    p.add_argument("--artist", default="CapryBeats")
    p.add_argument("--bpm", type=int, default=0)
    p.add_argument("--key", default="")
    p.add_argument("--hook", default="", help='one line under the picture, e.g. "open verse - use this sound"')
    p.add_argument("--mark", default="", help="small line above the bottom zone; default 'prod. <artist>'")
    p.add_argument("--start", default="", help="force the section start (seconds or m:ss); default = auto")
    p.add_argument("--length", type=float, default=30.0, help="target length in seconds (rounded to whole bars)")
    p.add_argument("--offset", type=float, default=0.0, help="time of the first bar line, if the beat doesn't start at 0")
    p.add_argument("--wave", action="store_true", help="add a moving waveform across the lower part (default: just the picture)")
    p.add_argument("--layout", choices=["fill", "wide", "card"], default="fill",
                   help="fill = photo covers the whole frame (default); wide = full width uncropped; card = small card")
    p.add_argument("--text", action="store_true", help="write the title and the prod. credit on the picture (default: nothing)")
    p.add_argument("--fps", type=int, default=FPS_DEFAULT)
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="medium")
    p.add_argument("--dry-run", action="store_true")
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
    if not 3 <= args.length <= 180:
        die("--length must be between 3 and 180 seconds (YouTube Shorts are up to 3 minutes)")
    args.out.mkdir(parents=True, exist_ok=True)

    info = probe(args.audio)
    duration = float(info["format"]["duration"])
    print(f"audio: {args.audio.name}  {duration:.2f} s")

    # 1) pick the section
    if args.start:
        start = parse_time(args.start)
        bar = 240.0 / args.bpm if args.bpm else None
        length = (max(1, round(args.length / bar)) * bar) if bar else args.length
        length = min(length, duration - start)
        if length < 3:
            die("--start leaves less than 3 s of audio")
        pick = {"start": round(start, 3), "length": round(length, 3), "bars": int(round(length / bar)) if bar else 0,
                "forced": True}
    else:
        samples = decode_mono(args.audio)
        pick = pick_section(samples, 22050, duration, args.bpm or None, args.length, args.offset)
        pick["forced"] = False
    (args.out / "short_pick.json").write_text(json.dumps(pick, indent=2))
    start, length = pick["start"], pick["length"]
    how = "forced" if pick["forced"] else f"auto, score {pick.get('score')} dB over {pick.get('candidates')} candidates"
    print(f"section: {start:.2f} s → {start + length:.2f} s  ({length:.2f} s, {pick.get('bars') or '?'} bars, {how})")

    # 2) frame + cover
    cover = load_cover(args.cover)
    credit = "prod. " + " x ".join([args.artist] + args.collabs)
    sub_bits = [credit]
    if args.bpm:
        sub_bits.append(f"{args.bpm} BPM")
    if args.key:
        sub_bits.append(args.key)
    show = bool(args.text or args.hook)
    frame = build_frame(cover,
                        args.title if args.text else "",
                        "  ·  ".join(sub_bits) if args.text else "",
                        args.hook,
                        (args.mark or credit) if args.text else "",
                        layout=args.layout, text=show)
    frame_path = args.out / "short_frame.png"    # PNG (RGB) → ffmpeg converts to limited-range yuv420p itself
    frame.save(frame_path, "PNG", compress_level=3)
    cover_path = args.out / "short_cover.jpg"
    frame.save(cover_path, "JPEG", quality=90)   # 9:16 thumbnail

    # 3) encode
    short_path = args.out / "short.mp4"
    fade_out = min(0.6, length / 4)
    audio_chain = f"[1:a]afade=t=in:st=0:d=0.15,afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f}"
    if not args.wave:
        fc = f"{audio_chain}[a];[0:v]format=yuv420p[v]"
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc = (f"{audio_chain},asplit=2[a][aw];"
              f"[aw]showwaves=s={W}x{WAVE_H}:mode=cline:rate={args.fps}:colors=white@0.85:scale=sqrt,format=rgba[w];"
              f"[0:v]format=rgba[bg];[bg][w]overlay=0:{WAVE_Y}:format=auto,format=yuv420p[v]")
        maps = ["-map", "[v]", "-map", "[a]"]
    cmd = ["ffmpeg", "-y",
           "-loop", "1", "-framerate", str(args.fps), "-i", str(frame_path),
           "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(args.audio),
           "-filter_complex", fc, *maps,
           "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p",
           "-r", str(args.fps), "-g", str(args.fps * 2), "-keyint_min", str(args.fps * 2), "-sc_threshold", "0",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
           "-t", f"{length:.3f}", "-movflags", "+faststart", str(short_path)]
    print("short:")
    run(cmd, args.dry_run)
    if args.dry_run:
        print("dry run — nothing rendered")
        return

    # 4) verify against the YouTube Shorts spec (vertical, ≤ 3 min)
    v = probe(short_path)
    vs = next(s for s in v["streams"] if s["codec_type"] == "video")
    aud = next(s for s in v["streams"] if s["codec_type"] == "audio")
    vd = float(v["format"]["duration"])
    size_mb = short_path.stat().st_size / 1e6
    checks = {
        "1080x1920": vs["width"] == W and vs["height"] == H,
        "h264 yuv420p": vs["codec_name"] == "h264" and vs.get("pix_fmt") in ("yuv420p", "yuvj420p"),
        "aac 48k": aud["codec_name"] == "aac" and aud.get("sample_rate") == "48000",
        "duration": abs(vd - length) < 0.3 and 3 <= vd <= 180,
        "< 300 MB": size_mb < 300,
    }
    print(f"\ncheck: {vs['width']}x{vs['height']} {vs['codec_name']} {vs.get('pix_fmt')} | {aud['codec_name']} "
          f"{aud.get('sample_rate')} Hz | {vd:.2f} s | {size_mb:.1f} MB")
    if vs.get("pix_fmt") == "yuvj420p":
        print("  note: ffmpeg tagged the video full-range (yuvj420p); YouTube accepts it")
    bad = [k for k, ok in checks.items() if not ok]
    print("  → OK" if not bad else f"  → FAILED: {', '.join(bad)}")
    print(f"cover: {cover_path}  pick: {args.out / 'short_pick.json'}")
    if bad:
        sys.exit(2)


if __name__ == "__main__":
    main()
