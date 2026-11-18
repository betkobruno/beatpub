"""
Phase 1 acceptance test — runs the real CLI with real ffmpeg on a generated fixture beat.
Not part of the default run (unit tests use no ffmpeg):   python -m pytest -m acceptance
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image
from typer.testing import CliRunner

from beatpub import render
from beatpub.cli import app
from fixtures import BAR, DROP_BAR, FIXTURE_STEM, cover_image, make_beat_folder, write_wav

pytestmark = [pytest.mark.acceptance,
              pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")]
runner = CliRunner()


def beatpub(*args: str, code: int = 0) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory):
    """A throwaway project folder (with a space and non-ASCII letters) holding one fixture beat."""
    root = tmp_path_factory.mktemp("Používateľ beat pub")
    (root / "config.yaml").write_text(
        'artist: CapryBeats\ndefaults:\n  type_beat: ["Dro Kenji", "Juice WRLD"]\n', encoding="utf-8")
    make_beat_folder(root / "beats" / "fixture")
    patch = pytest.MonkeyPatch()
    patch.setenv("BEATPUB_HOME", str(root))
    yield root
    patch.undo()


def streams(path: Path) -> tuple[dict, dict, float]:
    info = render.probe(path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    return video, audio, float(info["format"]["duration"])


def is_faststart(path: Path) -> bool:
    data = path.read_bytes()
    return 0 <= data.find(b"moov") < data.find(b"mdat")


def mtimes(out: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in out.iterdir()}


def test_1_new_prefills_from_the_file_name(project: Path):
    output = beatpub("new", "fixture")
    assert "prod. CapryBeats x someone" in output
    data = yaml.safe_load((project / "beats" / "fixture" / "meta.yaml").read_text(encoding="utf-8"))
    assert data == {"title": "Fixture Beat", "bpm": 142, "key": "F minor", "collabs": ["someone"]}


def test_2_render(project: Path):
    out = project / "beats" / "fixture" / "out"
    beatpub("render", "fixture", "--dry-run")
    assert not out.exists()                                            # a dry run writes nothing

    beatpub("render", "fixture", "--preset", "veryfast")
    video, audio, duration = streams(out / "video.mp4")
    assert (video["width"], video["height"], video["pix_fmt"], video["codec_name"]) == (1920, 1080, "yuv420p", "h264")
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)
    assert duration == pytest.approx(60.0, abs=0.1)
    assert is_faststart(out / "video.mp4")
    assert Image.open(out / "frame.png").size == (1920, 1080)
    assert Image.open(out / "thumb.jpg").size == (1280, 720) and (out / "thumb.jpg").stat().st_size < 2 * 1024 * 1024

    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    assert MP3(out / "beat.mp3").info.bitrate == 320000
    tags = ID3(out / "beat.mp3")
    assert tags.version[:2] == (2, 3)
    assert str(tags["TIT2"]) == "Fixture Beat" and str(tags["TPE1"]) == "CapryBeats x someone"
    assert str(tags["TBPM"]) == "142" and str(tags["TKEY"]) == "F minor"
    assert tags.getall("APIC")[0].mime == "image/jpeg" and "TDRC" in tags


def test_3_short_render(project: Path):
    out = project / "beats" / "fixture" / "out"
    beatpub("short", "render", "fixture", "--preset", "veryfast")
    pick = json.loads((out / "short_pick.json").read_text(encoding="utf-8"))
    assert pick["start"] == pytest.approx(DROP_BAR * BAR, abs=0.05) and pick["forced"] is False
    video, audio, duration = streams(out / "short.mp4")
    assert (video["width"], video["height"], video["pix_fmt"], video["codec_name"]) == (1080, 1920, "yuv420p", "h264")
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)
    assert pick["bars"] == 18                                           # 30 s at 142 BPM → 18 bars = 30.42 s
    assert duration == pytest.approx(18 * BAR, abs=0.3) and duration <= 180
    assert is_faststart(out / "short.mp4")
    assert Image.open(out / "short_cover.jpg").size == (1080, 1920)


def test_4_second_run_does_nothing(project: Path):
    out = project / "beats" / "fixture" / "out"
    before = mtimes(out)
    assert "nothing to do" in beatpub("render", "fixture")
    assert "nothing to do" in beatpub("short", "render", "fixture")
    assert mtimes(out) == before


def test_5_short_start_and_card_layout(project: Path):
    folder = project / "beats" / "fixture"
    out = folder / "out"
    before = mtimes(out)
    with (folder / "meta.yaml").open("a", encoding="utf-8") as fh:
        fh.write('short_start: "0:47"\nshort_layout: card\nshort_text: true\nshort_wave: true\n')
    beatpub("short", "render", "fixture", "--preset", "veryfast")
    pick = json.loads((out / "short_pick.json").read_text(encoding="utf-8"))
    assert pick["start"] == 47.0 and pick["forced"] is True
    video, _, duration = streams(out / "short.mp4")
    assert (video["width"], video["height"], video["pix_fmt"]) == (1080, 1920, "yuv420p")
    assert duration == pytest.approx(13.0, abs=0.3)                     # 0:47 → the end of the 60 s beat
    # Short-only settings must not re-render the long video
    assert "nothing to do" in beatpub("render", "fixture")
    assert mtimes(out)["video.mp4"] == before["video.mp4"]


def test_6_force_renders_again(project: Path):
    out = project / "beats" / "fixture" / "out"
    before = mtimes(out)["video.mp4"]
    beatpub("render", "fixture", "--force", "--preset", "ultrafast")
    assert mtimes(out)["video.mp4"] > before


def test_7_edge_cases(project: Path):
    beats = project / "beats"
    # MP3 source (copied, not re-encoded), mono, landscape PNG with alpha, zoom + text, no meta.yaml at all
    mp3_beat = beats / "mp3 source ô"
    mp3_beat.mkdir()
    write_wav(mp3_beat / "mono.wav", seconds=6, channels=1)
    source = mp3_beat / "Night Drive 150bpm C#min @caprybeats.mp3"
    render.run(["ffmpeg", "-y", "-i", str(mp3_beat / "mono.wav"), "-b:a", "128k", str(source)], say=lambda _: None)
    (mp3_beat / "mono.wav").unlink()
    cover_image((1600, 900), "RGBA").save(mp3_beat / "cover.png")
    (mp3_beat / "meta.yaml").write_text("zoom: true\ntext_overlay: true\n", encoding="utf-8")
    original = source.read_bytes()
    output = beatpub("render", "mp3 source ô", "--preset", "ultrafast")
    assert "already MP3" in output and "150 BPM" in output and "C# minor" in output
    out = mp3_beat / "out"
    video, audio, duration = streams(out / "video.mp4")
    assert (video["width"], video["height"], video["pix_fmt"], audio["channels"]) == (1920, 1080, "yuv420p", 2)
    assert duration == pytest.approx(6.0, abs=0.5)
    assert Image.open(out / "frame.png").size == (3840, 2160) and Image.open(out / "thumb.jpg").size == (1280, 720)
    from mutagen.mp3 import MP3
    assert MP3(out / "beat.mp3").info.bitrate == pytest.approx(128000, abs=500)                # the source's own audio, untouched
    assert source.read_bytes() == original
    # a Short section longer than the beat: the whole 6 s
    (mp3_beat / "meta.yaml").unlink()
    beatpub("short", "render", "mp3 source ô", "--preset", "ultrafast")
    assert streams(out / "short.mp4")[2] == pytest.approx(6.0, abs=0.3)

    # clear one-line errors
    assert "beat folder not found" in beatpub("render", "nope", code=1)
    empty = beats / "empty"
    empty.mkdir()
    assert "no audio file" in beatpub("render", "empty", code=1)
    write_wav(empty / "x.wav", seconds=4)
    assert "no cover photo" in beatpub("render", "empty", code=1)
    (empty / "IMG_0001.HEIC").touch()
    assert "HEIC" in beatpub("short", "render", "empty", code=1)
