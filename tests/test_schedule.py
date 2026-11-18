"""The scheduling chain: config, the next free slot, the queue and its flags. No network."""
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from beatpub import drafting, log, schedule
from beatpub.cli import app
from beatpub.config import ConfigError, ScheduleConfig, load_config, parse_config
from beatpub.meta import build_meta
from beatpub.schedule import QueueEntry

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 18, 21, 30, tzinfo=TZ)
STEM = "Ran Away 142 Bpm Fm @caprybeats"
CONFIG_DATA = {
    "artist": "CapryBeats",
    "playlist_default": "(FREE) Type Beats 2025",
    "defaults": {"type_beat": ["Dro Kenji", "Juice WRLD"], "mood": ["melodic"]},
    "schedule": {"enabled": True, "time": "18:00", "every_days": 1},
}


def at(day: int, hour: int = 18, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


# ----------------------------------------------------------------------------- config
def test_schedule_config():
    assert parse_config({"artist": "A"}, ROOT).schedule == ScheduleConfig(enabled=False, time="18:00", every_days=1)
    config = parse_config({"artist": "A", "schedule": {"enabled": True, "time": "9:05", "every_days": 3, "x": 1}}, ROOT)
    assert config.schedule == ScheduleConfig(True, "09:05", 3) and config.schedule.clock.hour == 9
    assert any("schedule.x" in w for w in config.warnings)
    unquoted = yaml.safe_load("artist: A\nschedule:\n  time: 18:00\n")       # YAML makes that the number 1080
    assert parse_config(unquoted, ROOT).schedule.time == "18:00"


@pytest.mark.parametrize("block", [
    "on", {"time": "25:00"}, {"time": "evening"}, {"time": True}, {"every_days": 0}, {"every_days": 1.5},
    {"enabled": "yes"},
])
def test_invalid_schedule_config(block):
    with pytest.raises(ConfigError):
        parse_config({"artist": "A", "schedule": block}, ROOT)


# ----------------------------------------------------------------------------- the next slot
def test_next_slot():
    daily, every3 = ScheduleConfig(True, "18:00", 1), ScheduleConfig(True, "07:30", 3)
    assert schedule.next_slot([], daily, NOW, TZ) == at(19)                              # tomorrow
    assert schedule.next_slot([date(2026, 11, 10)], daily, NOW, TZ) == at(19)            # old dates don't matter
    assert schedule.next_slot([date(2026, 11, 18)], daily, NOW, TZ) == at(19)
    assert schedule.next_slot([date(2026, 11, 19), date(2026, 11, 22)], daily, NOW, TZ) == at(23)
    assert schedule.next_slot([date(2026, 11, 18)], every3, NOW, TZ) == at(21, 7, 30)
    assert schedule.next_slot([date(2026, 11, 10)], every3, NOW, TZ) == at(19, 7, 30)
    # "tomorrow" is by the local calendar, not UTC: 23:30 UTC on the 18th is already the 19th at +01:00
    late = datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc)
    assert schedule.next_slot([], daily, late, TZ) == at(20)


def test_drafts_take_the_slot_and_explicit_publish_wins():
    config = parse_config(CONFIG_DATA, ROOT)

    def make(meta_data: dict | None, slot: datetime | None):
        meta = build_meta(meta_data, STEM, config.artist, config.defaults, for_draft=True, now=NOW)
        return drafting.build_drafts(meta, config, TEMPLATES, now=NOW, slot=slot)

    video, short = make(None, at(19))
    assert (video.privacy, video.publish_at) == ("private", "2026-11-19T18:00:00+01:00")
    assert (short.privacy, short.publish_at) == ("private", "2026-11-19T20:00:00+01:00")     # + 2 h, as before
    assert drafting.validate_draft(video, NOW) == []
    video, short = make({"short_publish": "2026-11-25T12:00:00+01:00"}, at(19))
    assert short.publish_at == "2026-11-25T12:00:00+01:00"
    video, short = make(None, None)                                                          # chain off
    assert (video.privacy, video.publish_at) == ("private", "")
    # the CLI passes no slot when meta.yaml has publish:, and video_schedule ignores one anyway
    video, _ = make({"publish": "2026-12-01T10:00:00+01:00"}, at(19))
    assert video.publish_at == "2026-12-01T10:00:00+01:00"
    video, _ = make({"publish": "public"}, at(19))
    assert (video.privacy, video.publish_at) == ("public", "")


# ----------------------------------------------------------------------------- a project on disk
@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "Používateľ pub"
    shutil.copytree(TEMPLATES, root / "templates", ignore=shutil.ignore_patterns("learned"))
    (root / "config.yaml").write_text(yaml.safe_dump(CONFIG_DATA, allow_unicode=True), encoding="utf-8")
    (root / "beats" / "_example").mkdir(parents=True)
    monkeypatch.setenv("BEATPUB_HOME", str(root))
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    return root


def add_beat(root: Path, slug: str, *, publish_at: str = "", approved: bool = False, draft: bool = True,
             video: bool = False, record: dict | None = None) -> Path:
    folder = root / "beats" / slug
    (folder / "out").mkdir(parents=True)
    (folder / f"{slug.title()} 142 Bpm Fm @caprybeats.mp3").touch()
    if video:
        (folder / "out" / "video.mp4").touch()
    if draft:
        item = drafting.Draft(title=f"(FREE) {slug} Type Beat", description="d", tags=["type beat"], privacy="private",
                              publish_at=publish_at, approved=approved)
        drafting.write_draft(folder / "out" / "draft.yaml", item, "YouTube video draft", slug)
    if record:
        (folder / "out" / "publish.json").write_text(json.dumps(record), encoding="utf-8")
    return folder


def test_used_dates_and_assign_slot(project: Path):
    config = load_config(project)
    assert schedule.assign_slot(config, "new-beat", NOW, TZ) == at(19)

    add_beat(project, "approved-one", publish_at="2026-11-21T18:00:00+01:00", approved=True)
    add_beat(project, "only-drafted", publish_at="2026-11-25T18:00:00+01:00")               # not approved: no claim
    add_beat(project, "no-date", approved=True)
    assert schedule.used_dates(config, tz=TZ) == {date(2026, 11, 21): ["approved-one"]}
    assert schedule.assign_slot(config, "new-beat", NOW, TZ) == at(22)
    assert schedule.assign_slot(config, "approved-one", NOW, TZ) == at(19)                   # its own draft is skipped

    rows = [
        {"kind": "video", "slug": "old", "videoId": "v1", "privacy": "private", "publishAt": "2026-11-23T17:00:00Z",
         "uploadedAt": "2026-11-17T10:00:00+00:00"},
        {"kind": "short", "slug": "old", "videoId": "s1", "privacy": "private", "publishAt": "2026-11-30T19:00:00Z",
         "uploadedAt": "2026-11-17T10:05:00+00:00"},                                         # Shorts never push the chain
        {"kind": "video", "slug": "locked", "videoId": "v2", "privacy": "private", "publishAt": "",
         "uploadedAt": "2026-11-28T10:00:00+00:00"},                                         # private, no date: no claim
        {"kind": "video", "slug": "went-public", "videoId": "v3", "privacy": "public", "publishAt": "",
         "uploadedAt": "2026-11-24T10:00:00+00:00"},
    ]
    for row in rows:
        log.append_published(project / log.PUBLISHED_NAME, row)
    assert sorted(schedule.used_dates(config, tz=TZ)) == [date(2026, 11, 21), date(2026, 11, 23), date(2026, 11, 24)]
    assert schedule.assign_slot(config, "new-beat", NOW, TZ) == at(25)

    off = parse_config({**CONFIG_DATA, "schedule": {"enabled": False}}, project)
    assert schedule.assign_slot(off, "new-beat", NOW, TZ) is None


def test_queue_status_and_order(project: Path):
    add_beat(project, "e-new", draft=False)
    add_beat(project, "d-rendered", draft=False, video=True)
    add_beat(project, "c-drafted", publish_at="2026-11-21T18:00:00+01:00", video=True)
    add_beat(project, "b-approved", publish_at="2026-11-20T18:00:00+01:00", approved=True, video=True)
    add_beat(project, "a-uploaded", publish_at="2026-11-19T18:00:00+01:00", approved=True, video=True,
             record={"videoId": "v1", "title": "Up", "privacy": "private", "publishAt": "",
                     "requestedPublishAt": "2026-11-19T18:00:00+01:00", "uploadedAt": "2026-11-18T10:00:00+00:00"})
    entries = schedule.queue(load_config(project), NOW, TZ)
    assert [(e.slug, e.status) for e in entries] == [
        ("a-uploaded", "uploaded"), ("b-approved", "approved"), ("c-drafted", "drafted"),
        ("d-rendered", "rendered"), ("e-new", "new")]
    assert [e.when for e in entries] == [at(19), at(20), at(21), None, None]
    assert entries[0].title == "Up" and not any(e.flags for e in entries)


def test_queue_flags():
    def entry(slug: str, day: int | None) -> QueueEntry:
        return QueueEntry(slug, "approved", at(day) if day else None)

    today = date(2026, 11, 18)
    flags = {e.slug: e.flags for e in schedule.flag_queue(
        [entry("d", 23), entry("a", 19), entry("b", 20), entry("c", 20), entry("x", None)], 1, today)}
    assert flags["a"] == () and flags["x"] == ()
    assert flags["b"] == ("duplicate: same day as c",) and flags["c"] == ("duplicate: same day as b",)
    assert flags["d"] == ("gap: 3 days after c",)

    ordered = schedule.flag_queue([entry("x", None), entry("late", 25), entry("early", 22), entry("mid", 24)], 3, today)
    assert [e.slug for e in ordered] == ["early", "mid", "late", "x"]
    assert [e.flags for e in ordered[:3]] == [(), ("too close: 2 days after early",), ("too close: 1 day after mid",)]

    # what is already behind us is history, not a problem to fix
    past = schedule.flag_queue([entry("p1", 10), entry("p2", 10), entry("p3", 15), entry("next", 19)], 1, today)
    assert [e.flags for e in past] == [(), (), (), ("gap: 4 days after p3",)]


# ----------------------------------------------------------------------------- the commands
def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(app, list(args), env={"COLUMNS": "200"})
    assert result.exit_code == code, result.output
    return result.output


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone()


def test_draft_approve_and_queue_commands(project: Path):
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
    for slug in ("first", "second", "third"):
        add_beat(project, slug, draft=False)

    output = run("draft", "first")
    video, short = (drafting.read_draft(project / "beats" / "first" / "out" / name)
                    for name in ("draft.yaml", "short_draft.yaml"))
    slot = local(video.publish_at)
    assert video.privacy == "private" and (slot.date(), slot.hour, slot.minute) == (tomorrow, 18, 0)
    assert local(short.publish_at) - slot == timedelta(hours=2)
    assert schedule.slot_text(slot) in output and "next free slot in the schedule chain" in output
    assert "2 h after the video" in output

    # an unapproved draft holds no slot; an approved one does
    assert "nothing written" in run("draft", "second", "--dry-run")
    output = run("approve", "first", "--yes")
    assert "next free slot in the schedule chain" in output and schedule.slot_text(slot) in output
    run("draft", "second")
    second = local(drafting.read_draft(project / "beats" / "second" / "out" / "draft.yaml").publish_at)
    assert second.date() == tomorrow + timedelta(days=1)
    assert "up to date" in run("draft", "second")                       # the slot is fixed once written

    # an explicit publish: wins over the chain
    (project / "beats" / "third" / "meta.yaml").write_text(
        f'publish: "{tomorrow.isoformat()}T09:00:00"\n', encoding="utf-8")
    output = run("draft", "third")
    third = local(drafting.read_draft(project / "beats" / "third" / "out" / "draft.yaml").publish_at)
    assert (third.date(), third.hour) == (tomorrow, 9) and "schedule chain" not in output
    assert "already has a video (first)" in run("approve", "third", "--yes")

    output = run("queue")
    assert output.index("third") < output.index("first") < output.index("second")
    assert "duplicate: same day as first" in output and "duplicate: same day as third" in output
    assert "approved" in output and "drafted" in output and "2 beat(s) break the chain" in output
    assert "one video every 1 day at 18:00" in output


def test_queue_without_beats_and_with_the_chain_off(project: Path):
    assert "no beat folders" in run("queue")
    (project / "config.yaml").write_text(yaml.safe_dump({**CONFIG_DATA, "schedule": {"enabled": False}}),
                                         encoding="utf-8")
    add_beat(project, "solo", draft=False)
    output = run("draft", "solo")
    assert drafting.read_draft(project / "beats" / "solo" / "out" / "draft.yaml").publish_at == ""
    assert "slot" not in output
    assert "schedule.enabled is false" in run("queue")
