"""Render and Short logic that needs no ffmpeg: frames, commands, the section picker, skipping."""
from pathlib import Path

import numpy as np
import pytest

from beatpub import render, short
from beatpub.meta import build_meta
from fixtures import BAR, DROP_BAR, FIXTURE_STEM, cover_image, fixture_signal

SR = 22050


# ----------------------------------------------------------------------------- section picker
def test_pick_section_finds_the_drop():
    pick = short.pick_section(fixture_signal(60.0, SR).astype(np.float32), SR, 60.0, 142, 15.0)
    assert pick["start"] == pytest.approx(DROP_BAR * BAR, abs=0.05)
    assert pick["bars"] == 9 and pick["length"] == pytest.approx(9 * BAR, abs=0.002)   # 15 s → 9 bars = 15.21 s
    assert pick["bonus"] > 2.5


def test_pick_section_without_bpm_uses_half_second_steps():
    pick = short.pick_section(fixture_signal(60.0, SR).astype(np.float32), SR, 60.0, None, 15.0)
    assert pick["bars"] == 0 and pick["length"] == 15.0
    assert pick["start"] >= DROP_BAR * BAR - 0.5 and pick["start"] * 2 == int(pick["start"] * 2)


def test_section_longer_than_the_beat():
    pick = short.pick_section(fixture_signal(8.0, SR).astype(np.float32), SR, 8.0, 142, 15.0)
    assert pick["start"] == 0.0 and pick["length"] == 8.0


def test_forced_section():
    pick = short.forced_section(47.0, 60.0, 142, 15.0)
    assert pick == {"start": 47.0, "length": pytest.approx(13.0), "bars": 8}          # cut at the end of the beat
    assert short.forced_section(10.0, 60.0, 142, 15.0)["length"] == pytest.approx(9 * BAR, abs=0.002)
    assert short.forced_section(10.0, 60.0, None, 15.0) == {"start": 10.0, "length": 15.0, "bars": 0}
    with pytest.raises(render.RenderError, match="less than"):
        short.forced_section(58.5, 60.0, 142, 15.0)


# ----------------------------------------------------------------------------- frames
@pytest.mark.parametrize("size", [(1200, 1600), (1600, 900), (800, 800), (120, 90)])
def test_video_frame_is_1080p_for_any_cover(size):
    frame = render.build_frame(cover_image(size), (1920, 1080))
    assert frame.size == (1920, 1080) and frame.mode == "RGB"


def test_video_frame_with_text_and_fill():
    text = {"title": 'Say "Less" 🔥', "sub": "prod. CapryBeats x someone  ·  142 BPM  ·  F minor"}
    plain = render.build_frame(cover_image(), (1920, 1080))
    assert render.build_frame(cover_image(), (1920, 1080), text=text).tobytes() != plain.tobytes()
    assert render.build_frame(cover_image(), (1920, 1080), layout="fill").tobytes() != plain.tobytes()


def test_cover_loading_flattens_alpha_and_honours_exif(tmp_path: Path):
    rgba = cover_image((300, 400), "RGBA")
    rgba.putalpha(0)                                    # fully transparent → black after flattening
    rgba.save(tmp_path / "alpha.png")
    flat = render.load_cover(tmp_path / "alpha.png")
    assert flat.mode == "RGB" and flat.getpixel((150, 200)) == (0, 0, 0)

    exif = cover_image((400, 300)).getexif()
    exif[0x0112] = 6                                    # phone held upright: stored landscape, shown portrait
    cover_image((400, 300)).save(tmp_path / "phone.jpg", exif=exif)
    assert render.load_cover(tmp_path / "phone.jpg").size == (300, 400)

    (tmp_path / "broken.jpg").write_bytes(b"not a picture")
    with pytest.raises(render.RenderError, match="could not open"):
        render.load_cover(tmp_path / "broken.jpg")


def test_thumbnail_and_artwork(tmp_path: Path):
    render.make_thumbnail(render.build_frame(cover_image(), (1920, 1080)), tmp_path / "thumb.jpg")
    render.make_artwork(cover_image((3000, 4000)), tmp_path / "artwork.jpg")
    from PIL import Image
    assert Image.open(tmp_path / "thumb.jpg").size == (1280, 720)
    assert (tmp_path / "thumb.jpg").stat().st_size < 2 * 1024 * 1024
    assert Image.open(tmp_path / "artwork.jpg").size == (1050, 1400)


@pytest.mark.parametrize("layout", ["fill", "wide", "card"])
@pytest.mark.parametrize("options", [{}, {"short_text": True, "hook_text": "open verse — use this sound"}])
def test_short_frame_layouts(layout, options):
    meta = build_meta({"short_layout": layout, **options}, FIXTURE_STEM, "CapryBeats")
    frame = short.frame_for(cover_image(), meta, "CapryBeats")
    assert frame.size == (1080, 1920) and frame.mode == "RGB"


def test_short_frame_text_is_off_by_default():
    plain = short.frame_for(cover_image(), build_meta(None, FIXTURE_STEM, "CapryBeats"), "CapryBeats")
    texted = short.frame_for(cover_image(), build_meta({"short_text": True}, FIXTURE_STEM, "CapryBeats"), "CapryBeats")
    from PIL import ImageOps, Image
    untouched = ImageOps.fit(cover_image(), (1080, 1920), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    assert plain.tobytes() == untouched.tobytes() and texted.tobytes() != plain.tobytes()


# ----------------------------------------------------------------------------- ffmpeg commands
def test_video_command_static():
    cmd = render.video_command(Path("out/frame.png"), Path("a b.wav"), Path("out/video.mp4"), 60.0, preset="veryfast")
    text = " ".join(cmd)
    assert cmd[:6] == ["ffmpeg", "-y", "-loop", "1", "-framerate", "30"] and cmd[-1] == str(Path("out/video.mp4"))
    assert "a b.wav" in cmd                                             # one argument, never shell-quoted
    for part in ("-c:v libx264 -preset veryfast -tune stillimage -crf 20 -pix_fmt yuv420p -r 30",
                 "-c:a aac -b:a 384k -ar 48000 -ac 2", "-movflags +faststart -t 60.000 -shortest"):
        assert part in text


def test_video_command_zoom():
    cmd = render.video_command(Path("frame.png"), Path("a.wav"), Path("video.mp4"), 60.0, zoom=True)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "-loop" not in cmd
    assert fc == ("[0:v]zoompan=z='1+0.08*on/1800':d=1800:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  ":s=1920x1080:fps=30,format=yuv420p[v]")


def test_short_command():
    cmd = short.short_command(Path("f.png"), Path("a.wav"), Path("short.mp4"), 13.521, 15.211)
    text = " ".join(cmd)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc == "[1:a]afade=t=in:st=0:d=0.15,afade=t=out:st=14.611:d=0.600[a];[0:v]format=yuv420p[v]"
    for part in ("-ss 13.521 -t 15.211 -i a.wav", "-g 60 -keyint_min 60 -sc_threshold 0",
                 "-c:a aac -b:a 128k -ar 48000 -ac 2", "-movflags +faststart"):
        assert part in text
    wave = short.short_command(Path("f.png"), Path("a.wav"), Path("short.mp4"), 0, 15, wave=True)
    fc = wave[wave.index("-filter_complex") + 1]
    assert "showwaves=s=1080x200:mode=cline:rate=30:colors=white@0.85:scale=sqrt" in fc
    assert "[0:v]format=rgba[bg]" in fc and "overlay=0:1330" in fc and fc.endswith("format=yuv420p[v]")


# ----------------------------------------------------------------------------- skipping finished work
def test_outputs_are_current_until_an_input_or_setting_changes(tmp_path: Path):
    audio, out = tmp_path / "beat.wav", tmp_path / "out"
    audio.write_bytes(b"one")
    out.mkdir()
    fp = render.fingerprint([audio], {"title": "A", "zoom": False})
    assert not render.is_current(out, "video", fp, ("video.mp4",))        # nothing rendered yet
    (out / "video.mp4").touch()
    assert not render.is_current(out, "video", fp, ("video.mp4",))        # file exists but no record of it
    render.save_state(out, "video", fp)
    assert render.is_current(out, "video", fp, ("video.mp4",))
    assert not render.is_current(out, "short", fp, ("video.mp4",))        # the Short is tracked separately
    assert not render.is_current(out, "video", fp, ("video.mp4", "beat.mp3"))     # an output is missing
    assert not render.is_current(out, "video", render.fingerprint([audio], {"title": "B", "zoom": False}),
                                 ("video.mp4",))                          # a setting changed
    audio.write_bytes(b"another export")
    assert not render.is_current(out, "video", render.fingerprint([audio], {"title": "A", "zoom": False}),
                                 ("video.mp4",))                          # the beat was exported again
    render.save_state(out, "short", fp)                                   # keeps the other section
    assert set(render.load_state(out)) == {"video", "short"}
