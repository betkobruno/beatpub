"""Generated test media (never committed): the 60 s fixture beat and a phone-like cover."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

BPM = 142
BAR = 240.0 / BPM
DROP_BAR = 8
FIXTURE_STEM = "Fixture Beat 142 Bpm Fm @caprybeats @someone"


def fixture_signal(seconds: float = 60.0, sr: int = 44100) -> np.ndarray:
    """Quiet sine melody for 8 bars, kick + bass from bar 8 on. Mono float in [-1, 1]."""
    t = np.arange(int(seconds * sr)) / sr
    melody_notes = np.array([349.23, 415.30, 523.25, 466.16])           # F4 Ab4 C5 Bb4
    melody = 0.03 * np.sin(2 * np.pi * melody_notes[(t / BAR).astype(int) % 4] * t)
    beat = 60.0 / BPM
    phase = (t % beat)
    kick = 0.6 * np.sin(2 * np.pi * (50 + 90 * np.exp(-phase * 30)) * phase) * np.exp(-phase * 9)
    bass = 0.3 * np.sin(2 * np.pi * 43.65 * t)                           # F1
    loud = t >= DROP_BAR * BAR
    return np.clip(melody + loud * (kick + bass), -1, 1)


def write_wav(path: Path, seconds: float = 60.0, sr: int = 44100, channels: int = 2) -> Path:
    pcm = (fixture_signal(seconds, sr) * 32767).astype("<i2")
    data = np.repeat(pcm[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(data.tobytes())
    return path


def cover_image(size: tuple[int, int] = (1200, 1600), mode: str = "RGB") -> Image.Image:
    """A portrait picture with some structure (gradient + shapes), like a phone photo."""
    w, h = size
    ramp = np.linspace(0, 255, h, dtype=np.uint8)[:, None].repeat(w, axis=1)
    img = Image.fromarray(np.dstack([ramp, ramp[::-1], np.full_like(ramp, 120)]), "RGB")
    draw = ImageDraw.Draw(img)
    draw.ellipse([w * 0.2, h * 0.25, w * 0.8, h * 0.7], fill=(240, 200, 60))
    draw.rectangle([w * 0.1, h * 0.8, w * 0.9, h * 0.9], fill=(30, 30, 90))
    return img.convert(mode)


def make_beat_folder(folder: Path, seconds: float = 60.0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    write_wav(folder / f"{FIXTURE_STEM}.wav", seconds)
    cover_image().save(folder / "IMG_2041.png")
    return folder


if __name__ == "__main__":      # python tests/fixtures.py beats/fixture  → a ready-to-run test beat
    import sys
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "beats/fixture")
    make_beat_folder(target)
    print(f"fixture beat written to {target} (60 s WAV + portrait PNG)")
