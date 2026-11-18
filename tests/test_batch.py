"""Month folders, --folder batches and `beatpub clean`. No network, no ffmpeg."""
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from beatpub import cli, drafting, log, schedule, youtube
from beatpub.cli import app
from beatpub.config import load_config

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DATA = {
    "artist": "CapryBeats",
    "playlist_default": "",
    "defaults": {"type_beat": ["Dro Kenji", "Juice WRLD"], "mood": ["melodic"]},
    "schedule": {"enabled": True, "time": "02:00", "every_days": 1},
}
MONTH = "2027-01"
NAMES = ("01-first", "02-second", "03-third")


class FakeService:
    """Stands in for YouTube: counts uploads, nothing else."""
    def __init__(self) -> None:
        self.uploads = 0


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "Používateľ pub"
    shutil.copytree(ROOT / "templates", root / "templates", ignore=shutil.ignore_patterns("learned"))
    (root / "config.yaml").write_text(yaml.safe_dump(CONFIG_DATA, allow_unicode=True), encoding="utf-8")
    (root / "beats" / "_example").mkdir(parents=True)
    for name in NAMES:
        add_beat(root, f"{MONTH}/{name}")
    add_beat(root, "loose-beat")
    monkeypatch.setenv("BEATPUB_HOME", str(root))
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)

    service = FakeService()

    def upload_video(svc, media, body, **kw):
        svc.uploads += 1
        return {"id": f"VID{svc.uploads}", "status": {"privacyStatus": body["status"]["privacyStatus"]}}

    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    monkeypatch.setattr(youtube, "upload_video", upload_video)
    monkeypatch.setattr(youtube, "wait_until_processed", lambda svc, video_id: "succeeded")
    monkeypatch.setattr(youtube, "set_thumbnail", lambda *a, **kw: None)
    return root                                          # tests reach the service through youtube.get_service


def add_beat(root: Path, slug: str) -> Path:
    folder = root / "beats" / Path(*slug.split("/"))
    folder.mkdir(parents=True)
    title = folder.name.split("-", 1)[-1].title()
    (folder / f"{title} 142 Bpm Fm @caprybeats.mp3").touch()
    return folder


def fake_renders(monkeypatch) -> list[str]:
    """Rendering writes the files `clean` deletes later, without ffmpeg."""
    rendered: list[str] = []

    def render_beat(folder, meta, artist, **kw):
        if not kw["dry_run"]:
            (folder / "out").mkdir(exist_ok=True)
            for name in ("video.mp4", "frame.png", "beat.mp3", "artwork.jpg", "thumb.jpg"):
                (folder / "out" / name).write_bytes(b"0" * 1_000_000)
        rendered.append(f"video:{folder.name}")

    def render_short(folder, meta, artist, **kw):
        if not kw["dry_run"]:
            for name in ("short.mp4", "short_frame.png", "short_cover.jpg", "short_pick.json"):
                (folder / "out" / name).write_bytes(b"0" * 500_000)
        rendered.append(f"short:{folder.name}")

    monkeypatch.setattr(cli, "render_beat", render_beat)
    monkeypatch.setattr(cli, "render_short", render_short)
    return rendered


def run(*args: str, code: int = 0, input: str | None = None) -> str:
    result = CliRunner().invoke(app, list(args), input=input, env={"COLUMNS": "200"})
    assert result.exit_code == code, result.output
    return result.output


def out(project: Path, name: str) -> Path:
    return project / "beats" / MONTH / name / "out"


def slot_days(project: Path) -> list[int]:
    """The publish day of each month-folder draft, counted from tomorrow (0 = tomorrow)."""
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
    days = []
    for name in NAMES:
        when = schedule.parse_when(drafting.read_draft(out(project, name) / "draft.yaml").publish_at)
        assert (when.hour, when.minute) == (2, 0)
        days.append((when.date() - tomorrow).days)
    return days


# ----------------------------------------------------------------------------- finding beats
def test_beats_are_found_one_level_down(project: Path):
    beats = project / "beats"
    found = schedule.beat_folders(beats)
    assert [slug for slug, _ in found] == [f"{MONTH}/{n}" for n in NAMES] + ["loose-beat"]
    assert found[0][1] == beats / MONTH / "01-first"
    assert [slug for slug, _ in schedule.folder_beats(beats, MONTH)] == [f"{MONTH}/{n}" for n in NAMES]
    assert [slug for slug, _ in schedule.folder_beats(beats, f" {MONTH}\\ ")] == [f"{MONTH}/{n}" for n in NAMES]

    (beats / MONTH / "10-tenth").mkdir()                                  # name order, as the Explorer shows it
    (beats / MONTH / "_notes").mkdir()
    assert [slug for slug, _ in schedule.folder_beats(beats, MONTH)][-1] == f"{MONTH}/10-tenth"

    # a beat folder is never mistaken for a month folder because it has out/ or other sub-folders
    (beats / "loose-beat" / "out").mkdir()
    (beats / "loose-beat" / "stems").mkdir()
    assert not schedule.is_month_folder(beats / "loose-beat")
    for wrong in ("loose-beat", "nope", "", "_example", f"{MONTH}/01-first"):
        with pytest.raises(ValueError):
            schedule.folder_beats(beats, wrong)


def test_slug_or_folder_exactly_one(project: Path):
    assert "one of the two" in run("draft", code=1)
    assert "one of the two" in run("draft", "loose-beat", "--folder", MONTH, code=1)
    assert "no folder named 'nope'" in run("draft", "--folder", "nope", code=1)
    assert "is a beat folder itself" in run("render", "--folder", "loose-beat", "--dry-run", code=1)


def test_single_commands_take_a_month_folder_slug(project: Path):
    slug = f"{MONTH}/02-second"
    assert 'beat: "Second"' in run("draft", slug)
    assert (out(project, "02-second") / "draft.yaml").is_file()
    assert "approved: draft.yaml" in run("approve", f"{MONTH}\\02-second", "--yes")       # typed the Windows way
    assert f"beatpub draft {MONTH}/03-third" in run("approve", f"{MONTH}/03-third", "--yes", code=1)
    assert "wrote" in run("new", f"{MONTH}/04-fourth") and (project / "beats" / MONTH / "04-fourth" / "meta.yaml").is_file()
    output = run("queue")
    assert slug in output and f"{MONTH}/04-fourth" in output and "loose-beat" in output


# ----------------------------------------------------------------------------- the chain in a batch
def test_draft_folder_gives_consecutive_slots_in_name_order(project: Path):
    dry = run("draft", "--folder", MONTH, "--dry-run")
    tomorrow = datetime.now().astimezone() + timedelta(days=1)
    for offset in range(3):                                               # even a dry run hands out three days
        assert f"scheduled: {schedule.slot_text((tomorrow + timedelta(days=offset)).replace(hour=2, minute=0))}" in dry
    assert not out(project, "01-first").exists()

    output = run("draft", "--folder", MONTH)
    assert slot_days(project) == [0, 1, 2]                                # unapproved, yet no two share a day
    assert output.index("01-first  (1/3)") < output.index("02-second  (2/3)") < output.index("03-third  (3/3)")
    assert f"{MONTH}: 3 done, 0 already uploaded — 3 beats" in output

    assert "up to date" in run("draft", "--folder", MONTH) and slot_days(project) == [0, 1, 2]
    run("draft", f"{MONTH}/02-second", "--force")                         # outside a batch only approved drafts count
    assert slot_days(project) == [0, 0, 2]
    queue = run("queue")
    assert "duplicate: same day as 2027-01/02-second" in queue and "gap: 2 days after" in queue

    run("draft", "--folder", MONTH, "--force")
    assert slot_days(project) == [0, 1, 2]
    assert "no duplicates or gaps" in run("queue")

    add_beat(project, f"{MONTH}/04-late")                                 # a beat added later goes to the end
    run("draft", "--folder", MONTH)
    late = schedule.parse_when(drafting.read_draft(out(project, "04-late") / "draft.yaml").publish_at)
    assert (late.date() - tomorrow.date()).days == 3 and slot_days(project) == [0, 1, 2]


def test_approve_folder_asks_once_per_beat(project: Path):
    run("draft", "--folder", MONTH)
    assert "no terminal" in run("approve", "--folder", MONTH, code=1)     # an error stops the batch at the first beat
    assert not drafting.read_draft(out(project, "01-first") / "draft.yaml").approved

    output = run("approve", "--folder", MONTH, "--yes")
    assert output.count("approved: draft.yaml, short_draft.yaml") == 3
    assert all(drafting.read_draft(out(project, n) / "draft.yaml").approved for n in NAMES)
    assert "already has a video" not in output


def test_a_no_moves_on_to_the_next_beat(project: Path, monkeypatch):
    run("draft", "--folder", MONTH)
    answers = iter([True, False, True])
    asked = []
    monkeypatch.setattr(cli, "interactive", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda text, **kw: (asked.append(text), next(answers))[1])
    output = run("approve", "--folder", MONTH, "--no-edit", code=1)
    assert len(asked) == 3                                                # one question per beat
    assert [drafting.read_draft(out(project, n) / "draft.yaml").approved for n in NAMES] == [True, False, True]
    assert f"not approved: {MONTH}/02-second" in output and "2 done, 0 already uploaded, 1 not approved" in output


# ----------------------------------------------------------------------------- run --folder, then clean
def test_run_folder_then_clean(project: Path, monkeypatch):
    rendered = fake_renders(monkeypatch)
    service = youtube.get_service(None)

    dry = run("run", "--folder", MONTH, "--dry-run")
    assert service.uploads == 0 and len(rendered) == 6 and not out(project, "01-first").exists()
    rendered.clear()

    output = run("run", "--folder", MONTH, "--yes")
    assert service.uploads == 6                                           # 3 videos + 3 Shorts
    assert rendered == [f"{kind}:{name}" for name in NAMES for kind in ("video", "short")]
    assert slot_days(project) == [0, 1, 2]
    rows = log.read_published(project / "published.csv")
    assert [(r["kind"], r["slug"]) for r in rows] == [(k, f"{MONTH}/{n}") for n in NAMES for k in ("video", "short")]
    assert json.loads((out(project, "03-third") / "publish.json").read_text(encoding="utf-8"))["slug"] == f"{MONTH}/03-third"
    assert f"{MONTH}: 3 done, 0 already uploaded — 3 beats" in output and "studio.youtube.com" in output

    # a second run sends and renders nothing; a beat added meanwhile is the only one worked on
    rendered.clear()
    add_beat(project, f"{MONTH}/04-late")
    again = run("run", "--folder", MONTH, "--yes")
    assert service.uploads == 8 and rendered == ["video:04-late", "short:04-late"]
    assert again.count("nothing left to do for this beat — skipped") == 3
    assert "1 done, 3 already uploaded — 4 beats" in again
    late = schedule.parse_when(drafting.read_draft(out(project, "04-late") / "draft.yaml").publish_at)
    assert (late.date() - (datetime.now().astimezone() + timedelta(days=1)).date()).days == 3

    # ---- clean: uploaded beats only
    run("render", "loose-beat")                                           # rendered, not uploaded
    kept_before = sorted(p.name for p in out(project, "01-first").iterdir())
    dry = run("clean", "--all", "--dry-run")
    assert "would delete" in dry and "would free 20.0 MB in 4 beat(s)" in dry and "loose-beat: not uploaded yet — kept" in dry
    assert sorted(p.name for p in out(project, "01-first").iterdir()) == kept_before

    one = run("clean", f"{MONTH}/01-first")
    assert "freed 5.0 MB in 1 beat(s)" in one
    left = sorted(p.name for p in out(project, "01-first").iterdir())
    assert not set(left) & set(cli.CLEAN_FILES)
    assert {"draft.yaml", "short_draft.yaml", "publish.json", "short_publish.json", "thumb.jpg", "short_cover.jpg",
            "short_pick.json", "draft_state.json"} <= set(left)
    assert any(p.suffix == ".mp3" for p in (project / "beats" / MONTH / "01-first").iterdir())   # the beat itself stays

    folder = run("clean", "--folder", MONTH)
    assert f"{MONTH}/01-first: already clean" in folder and "freed 15.0 MB in 3 beat(s)" in folder
    everything = run("clean", "--all")
    assert "freed 0.0 MB in 0 beat(s); 1 not uploaded yet, left alone" in everything
    assert (project / "beats" / "loose-beat" / "out" / "video.mp4").is_file()

    # a cleaned beat is not rendered or uploaded again by run, alone or in its folder
    rendered.clear()
    assert "nothing left to do for this beat" in run("run", f"{MONTH}/01-first", "--yes")
    run("run", "--folder", MONTH, "--yes")
    assert rendered == [] and service.uploads == 8
    assert "one of the three" in run("clean", code=1) and "one of the three" in run("clean", "x", "--all", code=1)
    assert "beat folder not found" in run("clean", "nope", code=1)


def test_clean_keeps_a_beat_whose_short_is_still_to_come(project: Path, monkeypatch):
    fake_renders(monkeypatch)
    slug = f"{MONTH}/01-first"
    run("render", slug)
    run("short", "render", slug)
    run("draft", slug)
    run("approve", slug, "--yes")
    run("upload", slug)
    assert "the Short is not yet — kept" in run("clean", slug)
    assert (out(project, "01-first") / "video.mp4").is_file()
    run("short", "upload", slug)
    assert "freed 5.0 MB" in run("clean", slug)


def test_an_error_stops_the_batch_and_the_next_run_continues(project: Path, monkeypatch):
    rendered = fake_renders(monkeypatch)
    service = youtube.get_service(None)
    (project / "beats" / MONTH / "02-second" / "meta.yaml").write_text("bpm: 999\n", encoding="utf-8")
    output = run("run", "--folder", MONTH, "--yes", code=1)
    assert "bpm must be between" in output and f"stopped at {MONTH}/02-second (2 of 3)" in output
    assert service.uploads == 2 and "03-third" not in " ".join(rendered)

    (project / "beats" / MONTH / "02-second" / "meta.yaml").unlink()
    output = run("run", "--folder", MONTH, "--yes")
    assert service.uploads == 6 and "2 done, 1 already uploaded — 3 beats" in output
    assert slot_days(project) == [0, 1, 2]
