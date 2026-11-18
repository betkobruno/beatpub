"""
The Short step: pick the beat's best section, build the 9:16 frame, encode short.mp4.
A port of scripts/short.py (the tested reference) into functions — keep the two in sync.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .meta import Meta, credit_line, find_inputs, parse_time
from .render import (RenderError, Say, fingerprint, is_current, load_cover, load_font, need_tools, probe,
                     run, save_state)

W, H = 1080, 1920
WAVE_H = 200          # height of the waveform band
WAVE_Y = 1330         # top of the waveform band
# The Shorts player covers roughly the top 220 px and the bottom 320 px (title, channel,
# buttons) and a ~150 px strip on the right. Everything that matters stays inside the middle.
SAFE_TOP, SAFE_BOTTOM = 240, 1600
MIN_SECONDS, MAX_SECONDS = 3, 180
SAMPLE_RATE = 22050
SHORT_OUTPUTS = ("short.mp4", "short_frame.png", "short_cover.jpg", "short_pick.json")


# ----------------------------------------------------------------------------- section picker
def decode_mono(audio: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` via an ffmpeg pipe (no extra deps)."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(audio), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg could not decode {audio.name}: {proc.stderr.decode(errors='replace')[-2000:]}")
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
    if best is None:
        return {"start": 0.0, "length": min(target_len, duration), "bars": bars, "score": 0.0, "candidates": 0}
    best["candidates"] = len(starts)
    return best


def forced_section(start: float, duration: float, bpm: int | None, target_len: float) -> dict:
    """The section for a `short_start` given by the owner: whole bars from there, cut at the end of the beat."""
    bar = 240.0 / bpm if bpm else None
    length = (max(1, round(target_len / bar)) * bar) if bar else target_len
    length = min(length, duration - start)
    if length < MIN_SECONDS:
        raise RenderError(f"short_start {start:g} s leaves less than {MIN_SECONDS} s of the {duration:.1f} s beat")
    return {"start": round(start, 3), "length": round(length, 3), "bars": int(round(length / bar)) if bar else 0}


def choose_section(audio: Path, duration: float, meta: Meta, offset: float = 0.0) -> dict:
    """`short_start` if set, else the loudest bar-aligned window. Adds "forced"."""
    if duration < MIN_SECONDS:
        raise RenderError(f"{audio.name} is only {duration:.1f} s long — too short for a Short")
    if meta.short_start:
        pick = forced_section(parse_time(meta.short_start), duration, meta.bpm or None, meta.short_length)
        pick["forced"] = True
    else:
        pick = pick_section(decode_mono(audio), SAMPLE_RATE, duration, meta.bpm or None, meta.short_length, offset)
        pick["forced"] = False
    return pick


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
            top, bottom = vertical_gradient(170, 0, 520), vertical_gradient(200, 1080, H, reverse=True)
            bg.paste(top, (0, 0), top)
            bg.paste(bottom, (0, 0), bottom)
    else:
        # background: cover scaled to fill 9:16, blurred, darkened
        bg = ImageOps.fit(cover, (W, H), method=Image.Resampling.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(45))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.5)
        if layout == "wide":
            box_w, box_h = W, (1400 if not text else 1100)
            scale = min(box_w / cover.width, box_h / cover.height)
            fg = cover.resize((max(1, round(cover.width * scale)), max(1, round(cover.height * scale))),
                              Image.Resampling.LANCZOS)
            x, y = (W - fg.width) // 2, (H - fg.height) // 2 if not text else 400 + (1100 - fg.height) // 2
            bg.paste(fg, (x, y))
        else:  # card
            box_w, box_h = 900, 760
            scale = min(box_w / cover.width, box_h / cover.height)
            fg = cover.resize((max(1, round(cover.width * scale)), max(1, round(cover.height * scale))),
                              Image.Resampling.LANCZOS)
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

    def centred(line_text: str, y: int, font: ImageFont.ImageFont, fill=(255, 255, 255), max_w: int = 960) -> int:
        """Draw wrapped, centred text starting at y; return the y after the last line."""
        lines = []
        for para in line_text.split("\n"):
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


def frame_for(cover: Image.Image, meta: Meta, artist: str) -> Image.Image:
    """The Short's still for this beat: text only with short_text, hook only with hook_text."""
    credit = credit_line(artist, meta.collabs)
    sub_bits = [credit]
    if meta.bpm:
        sub_bits.append(f"{meta.bpm} BPM")
    if meta.key:
        sub_bits.append(meta.key)
    return build_frame(cover,
                       meta.title if meta.short_text else "",
                       "  ·  ".join(sub_bits) if meta.short_text else "",
                       meta.hook_text,
                       credit if meta.short_text else "",
                       layout=meta.short_layout, text=bool(meta.short_text or meta.hook_text))


# ----------------------------------------------------------------------------- encode
def short_command(frame: Path, audio: Path, short: Path, start: float, length: float, *, wave: bool = False,
                  fps: int = 30, crf: int = 20, preset: str = "medium") -> list[str]:
    fade_out = min(0.6, length / 4)
    audio_chain = f"[1:a]afade=t=in:st=0:d=0.15,afade=t=out:st={length - fade_out:.3f}:d={fade_out:.3f}"
    if not wave:
        fc = f"{audio_chain}[a];[0:v]format=yuv420p[v]"
    else:
        fc = (f"{audio_chain},asplit=2[a][aw];"
              f"[aw]showwaves=s={W}x{WAVE_H}:mode=cline:rate={fps}:colors=white@0.85:scale=sqrt,format=rgba[w];"
              f"[0:v]format=rgba[bg];[bg][w]overlay=0:{WAVE_Y}:format=auto,format=yuv420p[v]")
    return ["ffmpeg", "-y",
            "-loop", "1", "-framerate", str(fps), "-i", str(frame),
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio),
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-r", str(fps), "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-t", f"{length:.3f}", "-movflags", "+faststart", str(short)]


def verify_short(short: Path, length: float, say: Say = print) -> None:
    """ffprobe against the Shorts spec (vertical, ≤ 3 min). RenderError(code 2) on a mismatch."""
    v = probe(short)
    vs = next(s for s in v["streams"] if s["codec_type"] == "video")
    aud = next(s for s in v["streams"] if s["codec_type"] == "audio")
    vd = float(v["format"]["duration"])
    size_mb = short.stat().st_size / 1e6
    checks = {
        "1080x1920": vs["width"] == W and vs["height"] == H,
        "h264 yuv420p": vs["codec_name"] == "h264" and vs.get("pix_fmt") in ("yuv420p", "yuvj420p"),
        "aac 48k": aud["codec_name"] == "aac" and aud.get("sample_rate") == "48000",
        "duration": abs(vd - length) < 0.3 and MIN_SECONDS <= vd <= MAX_SECONDS,
        "< 300 MB": size_mb < 300,
    }
    if vs.get("pix_fmt") == "yuvj420p":
        say("note: ffmpeg tagged the video full-range (yuvj420p); YouTube accepts it")
    bad = [k for k, ok in checks.items() if not ok]
    say(f"check: {vs['width']}x{vs['height']} {vs['codec_name']} {vs.get('pix_fmt')} | {aud['codec_name']} "
        f"{aud.get('sample_rate')} Hz | {vd:.2f} s | {size_mb:.1f} MB  → "
        + ("OK" if not bad else f"FAILED: {', '.join(bad)}"))
    if bad:
        raise RenderError(f"{short.name} does not match the Shorts spec: {', '.join(bad)}", code=2)


# ----------------------------------------------------------------------------- the whole step
def render_short(folder: Path, meta: Meta, artist: str, *, force: bool = False, dry_run: bool = False,
                 preset: str = "medium", crf: int = 20, fps: int = 30, offset: float = 0.0,
                 say: Say = print) -> bool:
    """
    beats/<slug> → out/short.mp4 short_frame.png short_cover.jpg short_pick.json.
    Returns False when the outputs were already current (nothing done).
    """
    need_tools()
    audio, cover_path = find_inputs(folder)
    out = folder / "out"
    credit = credit_line(artist, meta.collabs)
    params: dict[str, Any] = {
        "bpm": meta.bpm, "start": meta.short_start, "length": meta.short_length, "offset": offset,
        "layout": meta.short_layout, "wave": meta.short_wave, "hook": meta.hook_text, "text": meta.short_text,
    }
    if meta.short_text:                      # these are only drawn with short_text
        params.update({"title": meta.title, "credit": credit, "key": meta.key})
    fp = fingerprint([audio, cover_path], params)
    if not force and not dry_run and is_current(out, "short", fp, SHORT_OUTPUTS):
        say(f"short: up to date in {out} — nothing to do (use --force to render again)")
        return False

    duration = float(probe(audio)["format"]["duration"])
    say(f"audio: {audio.name}  {duration:.2f} s")

    # 1) pick the section
    pick = choose_section(audio, duration, meta, offset)
    start, length = pick["start"], pick["length"]
    if length < MIN_SECONDS:
        raise RenderError(f"the section is only {length:.1f} s — a Short needs at least {MIN_SECONDS} s")
    how = "forced by short_start" if pick["forced"] else \
        f"auto, score {pick.get('score')} dB over {pick.get('candidates')} candidates"
    say(f"section: {start:.2f} s → {start + length:.2f} s  ({length:.2f} s, {pick.get('bars') or '?'} bars, {how})")

    # 2) frame + cover
    frame_path, cover_out, short_path = out / "short_frame.png", out / "short_cover.jpg", out / "short.mp4"
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)
        (out / "short_pick.json").write_text(json.dumps(pick, indent=2), encoding="utf-8")
        frame = frame_for(load_cover(cover_path), meta, artist)
        frame.save(frame_path, "PNG", compress_level=3)   # PNG (RGB) → ffmpeg makes limited-range yuv420p
        frame.save(cover_out, "JPEG", quality=90)         # the 9:16 thumbnail

    # 3) encode
    say("short:")
    run(short_command(frame_path, audio, short_path, start, length, wave=meta.short_wave, fps=fps, crf=crf,
                      preset=preset), say, dry_run)
    if dry_run:
        say("dry run — nothing rendered")
        return True

    # 4) verify
    verify_short(short_path, length, say)
    save_state(out, "short", fp)
    return True
