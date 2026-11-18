import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from beatpub.config import Defaults, parse_config
from beatpub.meta import (MetaError, beat_dir, build_meta, credit_line, find_audio, find_cover,
                          find_inputs, load_meta, meta_yaml_text, parse_beat_name, parse_publish,
                          parse_time)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = Defaults(type_beat=("Dro Kenji", "Juice WRLD"), similar=("Polo G",), mood=("melodic",),
                    free=False, tags_extra=("sad",), playlist="Channel List")
CONFIG = parse_config({"artist": "CapryBeats", "defaults": {"type_beat": ["Dro Kenji", "Juice WRLD"]}}, ROOT)


# ----------------------------------------------------------------------------- file-name parser
@pytest.mark.parametrize("stem, title, bpm, key, handles", [
    ("Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill",
     "Tuesday Night", 160, "A# minor", ["caprybeats", "sinfulyts", "1parkerhill"]),
    ("Fixture Beat 142 Bpm Fm @caprybeats @someone", "Fixture Beat", 142, "F minor", ["caprybeats", "someone"]),
    ("Ran Away 142bpm Fm", "Ran Away", 142, "F minor", []),                    # no space before bpm
    ("Late 150 BPM C#min @caprybeats", "Late", 150, "C# minor", ["caprybeats"]),
    ("Cold_Heart_140_bpm_Dm_@caprybeats", "Cold Heart", 140, "D minor", ["caprybeats"]),   # underscores
    ("Tuesday Night A#m 160 Bpm @caprybeats", "Tuesday Night", 160, "A# minor", ["caprybeats"]),  # key first
    ("Sunrise 128 bpm Ebmaj", "Sunrise", 128, "Eb major", []),
    ("Glow 120 Bpm G", "Glow", 120, "G major", []),                             # bare note = major
    ("Wave - 98 bpm F# minor", "Wave", 98, "F# minor", []),
    ("untitled", "untitled", 0, "", []),                                        # no info at all
])
def test_parse_beat_name(stem, title, bpm, key, handles):
    assert parse_beat_name(stem) == {"title": title, "bpm": bpm, "key": key, "handles": handles}


def test_credit_line():
    assert credit_line("CapryBeats", []) == "prod. CapryBeats"
    assert credit_line("CapryBeats", ["sinfulyts", "1parkerhill"]) == "prod. CapryBeats x sinfulyts x 1parkerhill"


# ----------------------------------------------------------------------------- values
@pytest.mark.parametrize("value, seconds", [("1:04", 64), ("0:47", 47), (64, 64), ("64", 64), ("12.5", 12.5), ("1:00:01", 3601)])
def test_parse_time(value, seconds):
    assert parse_time(value) == seconds


@pytest.mark.parametrize("value", ["abc", "1:", "-3", True, "1:2:3:4"])
def test_parse_time_rejects(value):
    with pytest.raises(ValueError):
        parse_time(value)


def test_parse_publish():
    assert parse_publish("", NOW) == ("", None)
    assert parse_publish(None, NOW) == ("", None)
    assert parse_publish("Public", NOW) == ("public", None)
    privacy, when = parse_publish("2026-11-20T18:00:00+01:00", NOW)
    assert privacy == "private" and when.isoformat() == "2026-11-20T18:00:00+01:00"
    assert parse_publish(NOW + timedelta(hours=1), NOW)[1] == NOW + timedelta(hours=1)   # PyYAML datetime
    assert parse_publish("2027-01-01T10:00:00", NOW)[1].tzinfo is not None             # naive = local time


@pytest.mark.parametrize("value", ["2026-11-17T11:59:00+00:00", "tomorrow", "2020-01-01T00:00:00Z"])
def test_parse_publish_rejects(value):
    with pytest.raises(ValueError):
        parse_publish(value, NOW)


# ----------------------------------------------------------------------------- meta validation
STEM = "Fixture Beat 142 Bpm Fm @caprybeats @someone"


def test_defaults_come_from_the_file_name():
    meta = build_meta(None, STEM, "CapryBeats")
    assert (meta.title, meta.bpm, meta.key, meta.collabs) == ("Fixture Beat", 142, "F minor", ["someone"])
    assert meta.free is True and meta.short is True and meta.short_layout == "fill" and meta.short_length == 30
    assert meta.warnings == ()


def test_meta_yaml_wins_over_the_file_name():
    meta = build_meta({"title": 'Ran "Away" 🔥', "bpm": 150, "key": "G minor", "collabs": ["@sinfulyts"]},
                      STEM, "CapryBeats")
    assert (meta.title, meta.bpm, meta.key, meta.collabs) == ('Ran "Away" 🔥', 150, "G minor", ["sinfulyts"])


def test_empty_values_fall_back_to_the_file_name():
    meta = build_meta({"title": "", "bpm": "", "key": "", "collabs": []}, STEM, "CapryBeats")
    assert (meta.title, meta.bpm, meta.key, meta.collabs) == ("Fixture Beat", 142, "F minor", ["someone"])


def test_unknown_keys_warn():
    meta = build_meta({"tpye_beat": ["x"]}, STEM, "CapryBeats")
    assert len(meta.warnings) == 1 and "tpye_beat" in meta.warnings[0]


def test_single_name_becomes_a_list():
    assert build_meta({"type_beat": "Dro Kenji"}, STEM, "CapryBeats").type_beat == ["Dro Kenji"]


def test_channel_defaults_fill_what_meta_leaves_out():
    meta = build_meta(None, STEM, "CapryBeats", DEFAULTS, for_draft=True)       # no meta.yaml at all
    assert meta.type_beat == ["Dro Kenji", "Juice WRLD"] and meta.similar == ["Polo G"] and meta.mood == ["melodic"]
    assert meta.free is False and meta.tags_extra == ["sad"] and meta.playlist == "Channel List"
    assert (meta.title, meta.bpm, meta.collabs) == ("Fixture Beat", 142, ["someone"])
    empty = build_meta({"type_beat": [], "mood": "", "playlist": "", "tags_extra": []}, STEM, "CapryBeats", DEFAULTS)
    assert empty.type_beat == ["Dro Kenji", "Juice WRLD"] and empty.mood == ["melodic"]
    assert empty.playlist == "Channel List" and empty.tags_extra == ["sad"]


def test_meta_yaml_overrides_channel_defaults():
    meta = build_meta({"type_beat": ["Polo G"], "mood": ["dark"], "free": True, "playlist": "Other",
                       "tags_extra": ["x"], "similar": ["Lil Tjay"]}, STEM, "CapryBeats", DEFAULTS)
    assert meta.type_beat == ["Polo G"] and meta.mood == ["dark"] and meta.free is True
    assert meta.playlist == "Other" and meta.similar == ["Lil Tjay"]


def test_tags_extra_is_added_to_the_channels_not_replaced():
    meta = build_meta({"tags_extra": ["x", "SAD", "guitar type beat"]}, STEM, "CapryBeats", DEFAULTS)
    assert meta.tags_extra == ["sad", "x", "guitar type beat"]      # channel's first, no repeats
    assert build_meta({"tags_extra": "one"}, STEM, "CapryBeats", DEFAULTS).tags_extra == ["sad", "one"]
    assert build_meta({"tags_extra": ["x"]}, STEM, "CapryBeats").tags_extra == ["x"]


def test_draft_requirements():
    with pytest.raises(MetaError, match="type_beat"):
        build_meta({}, STEM, "CapryBeats", for_draft=True)
    with pytest.raises(MetaError, match="title"):
        build_meta({"type_beat": ["Dro Kenji"]}, "", "CapryBeats", for_draft=True)
    assert build_meta({"type_beat": ["Dro Kenji"]}, STEM, "CapryBeats", for_draft=True).title == "Fixture Beat"


@pytest.mark.parametrize("data", [
    {"bpm": 20}, {"bpm": 301}, {"bpm": 142.5}, {"bpm": "fast"},
    {"publish": "2020-01-01T00:00:00+00:00"}, {"publish": "soon"}, {"short_publish": "2020-01-01T00:00:00Z"},
    {"short_layout": "tall"}, {"video_layout": "stretch"}, {"short_length": 181}, {"short_length": 2},
    {"short_start": "later"}, {"free": "yes"}, {"type_beat": {"a": 1}}, {"video_margin": 0.9},
    ["not", "a", "mapping"],
])
def test_invalid_meta(data):
    with pytest.raises(MetaError):
        build_meta(data, STEM, "CapryBeats", now=NOW)


def test_bpm_in_the_file_name_is_range_checked():
    with pytest.raises(MetaError, match="bpm"):
        build_meta(None, "Weird 999 bpm", "CapryBeats")


def test_valid_schedule_and_short_options():
    meta = build_meta({"publish": "2026-11-20T18:00:00+01:00", "short_start": "0:47", "short_layout": "card",
                       "short_length": 15}, STEM, "CapryBeats", now=NOW)
    assert meta.publish == "2026-11-20T18:00:00+01:00" and meta.short_start == "0:47"
    assert meta.short_layout == "card" and meta.short_length == 15


# ----------------------------------------------------------------------------- folders
def test_find_inputs_prefers_wav_and_handles_odd_names(tmp_path: Path):
    folder = tmp_path / "Používateľ beats" / "fixture"
    folder.mkdir(parents=True)
    (folder / "a.mp3").touch()
    (folder / f"{STEM}.WAV").touch()
    (folder / "IMG_0001.JPG").touch()
    (folder / "meta.yaml").write_text("type_beat: [Dro Kenji]\n", encoding="utf-8")
    audio, cover = find_inputs(folder)
    assert audio.name == f"{STEM}.WAV" and cover.name == "IMG_0001.JPG"
    meta = load_meta(folder, CONFIG, for_draft=True)
    assert (meta.title, meta.bpm, meta.collabs, meta.type_beat) == ("Fixture Beat", 142, ["someone"], ["Dro Kenji"])


def test_find_inputs_errors(tmp_path: Path):
    with pytest.raises(MetaError, match="not found"):
        find_inputs(tmp_path / "nope")
    with pytest.raises(MetaError, match="no audio"):
        find_inputs(tmp_path)
    (tmp_path / "beat.mp3").touch()
    assert find_audio(tmp_path).name == "beat.mp3" and find_cover(tmp_path) is None
    with pytest.raises(MetaError, match="no cover"):
        find_inputs(tmp_path)
    (tmp_path / "IMG_1.HEIC").touch()
    with pytest.raises(MetaError, match="HEIC"):
        find_inputs(tmp_path)


def test_a_folder_without_meta_yaml_is_valid(tmp_path: Path):
    (tmp_path / f"{STEM}.mp3").touch()
    meta = load_meta(tmp_path, CONFIG, for_draft=True)             # file name + channel defaults alone
    assert (meta.title, meta.type_beat) == ("Fixture Beat", ["Dro Kenji", "Juice WRLD"])
    no_defaults = parse_config({"artist": "CapryBeats"}, ROOT)
    assert load_meta(tmp_path, no_defaults).type_beat == []         # fine for render
    with pytest.raises(MetaError, match="type_beat"):
        load_meta(tmp_path, no_defaults, for_draft=True)


@pytest.mark.parametrize("slug", ["", "..", "a/b", "a\\b", "what?"])
def test_beat_dir_rejects(slug, tmp_path: Path):
    with pytest.raises(MetaError):
        beat_dir(tmp_path, slug)


def test_new_meta_yaml_holds_only_what_the_file_name_says():
    text = meta_yaml_text('Say "Less" 🔥', 142, "F minor", ["someone"], DEFAULTS, source="x.wav")
    data = yaml.safe_load(text)
    assert data == {"title": 'Say "Less" 🔥', "bpm": 142, "key": "F minor", "collabs": ["someone"]}
    assert '# type_beat: ["Dro Kenji", "Juice WRLD"]' in text and "# free: false" in text   # defaults as comments
    assert build_meta(data, "", "CapryBeats", DEFAULTS, for_draft=True).type_beat == ["Dro Kenji", "Juice WRLD"]


def test_new_meta_yaml_commented_lines_are_valid_keys():
    text = meta_yaml_text("T", 142, "F minor", ["someone"], DEFAULTS, source="x.wav")
    option = re.compile(r"# ([a-z_]+: .*)")                        # '# key: value   # comment'
    lines = [m.group(1) if (m := option.fullmatch(line)) else line for line in text.splitlines()]
    data = yaml.safe_load("\n".join(lines))
    assert {"type_beat", "short_layout", "publish", "hook_text"} <= set(data)
    assert build_meta(data, "", "CapryBeats").warnings == ()        # every key offered is a known key


def test_new_meta_yaml_without_audio_or_info():
    assert yaml.safe_load(meta_yaml_text("", 0, "", [])) is None    # nothing known → all comments
    assert yaml.safe_load(meta_yaml_text("untitled", 0, "", [], source="untitled.wav")) == {"title": "untitled"}


def test_example_meta_is_valid():
    example = ROOT / "beats" / "_example" / "meta.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))
    meta = build_meta(data, "", "CapryBeats", DEFAULTS, for_draft=True)
    assert meta.warnings == () and meta.type_beat == ["Dro Kenji", "Juice WRLD"]
