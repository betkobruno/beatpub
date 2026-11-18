"""The scheduling chain: the next free publish slot for a beat, and the queue of all beats."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

from . import log
from .config import Config, ScheduleConfig
from .drafting import DRAFT_NAME, Draft, DraftError, read_draft
from .meta import AUDIO_EXT, META_NAME

STATUSES = ("new", "rendered", "drafted", "approved", "uploaded")


@dataclass(frozen=True)
class QueueEntry:
    slug: str
    status: str                       # one of STATUSES
    when: datetime | None = None      # when the video goes public, local time; None = no date
    title: str = ""
    privacy: str = ""
    flags: tuple[str, ...] = ()


# ----------------------------------------------------------------------------- local time
def as_local(when: datetime, tz: tzinfo | None = None) -> datetime:
    """An aware datetime in the local zone (`tz` replaces this computer's zone in tests)."""
    if when.tzinfo is None:
        return when.replace(tzinfo=tz) if tz else when.astimezone()
    return when.astimezone(tz)


def parse_when(text: object, tz: tzinfo | None = None) -> datetime | None:
    """A publish date as written in a draft, publish.json or published.csv → local time; None if there is none."""
    if not text:
        return None
    try:
        return as_local(datetime.fromisoformat(str(text).strip()), tz)
    except ValueError:
        return None


def slot_text(when: datetime) -> str:
    return f"{when:%a %d %b %Y, %H:%M}"


def _subfolders(folder: Path) -> list[Path]:
    """Sub-folders in name order; names starting with _ or . are not beats (beats/_example)."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))),
                  key=lambda p: (p.name.casefold(), p.name))


def is_month_folder(folder: Path) -> bool:
    """
    A folder under beats/ that holds beat folders instead of being one: it has no beat
    of its own (no audio file, meta.yaml or out/) and at least one sub-folder.
    """
    if (folder / "out").is_dir() or (folder / META_NAME).is_file():
        return False
    if any(p.is_file() and p.suffix.lower() in AUDIO_EXT for p in folder.iterdir()):
        return False
    return bool(_subfolders(folder))


def beat_folders(beats_dir: Path) -> list[tuple[str, Path]]:
    """Every beat as (slug, folder): beats/<slug> and, one level down, beats/<month>/<slug>."""
    found: list[tuple[str, Path]] = []
    for folder in _subfolders(beats_dir):
        if is_month_folder(folder):
            found += [(f"{folder.name}/{sub.name}", sub) for sub in _subfolders(folder)]
        else:
            found.append((folder.name, folder))
    return found


def folder_beats(beats_dir: Path, name: str) -> list[tuple[str, Path]]:
    """The beats of one month folder, in name order. ValueError (owner-readable) if it is not one."""
    name = name.strip().replace("\\", "/").strip("/")
    folder = beats_dir / name
    if not name or "/" in name or name.startswith(("_", ".")) or not folder.is_dir():
        raise ValueError(f"no folder named '{name}' in {beats_dir}")
    if not is_month_folder(folder):
        raise ValueError(f"{folder} is a beat folder itself or has no beat folders in it — "
                         "--folder takes a folder of beats, like 2027-01")
    return [(f"{name}/{sub.name}", sub) for sub in _subfolders(folder)]


def _read_draft(folder: Path) -> Draft | None:
    try:
        return read_draft(folder / "out" / DRAFT_NAME)
    except (DraftError, OSError):
        return None


# ----------------------------------------------------------------------------- the chain
def used_dates(config: Config, skip_slug: str = "", tz: tzinfo | None = None) -> dict[date, list[str]]:
    """
    The days that already have a video: the publish date of every approved video draft
    (other than `skip_slug`'s own) and of every video row in published.csv → {day: [who]}.
    A row without a schedule counts on its upload day when it went out public.
    Shorts are left out: they follow their video and must not push the chain.
    """
    used: dict[date, list[str]] = {}
    for slug, folder in beat_folders(config.beats_dir):
        if slug == skip_slug:
            continue
        draft = _read_draft(folder)
        when = parse_when(draft.publish_at, tz) if draft and draft.approved else None
        if when:
            used.setdefault(when.date(), []).append(slug)
    for row in log.read_published(config.root / log.PUBLISHED_NAME):
        if (row.get("kind") or "video") != "video":
            continue
        when = parse_when(row.get("publishAt"), tz)
        if not when and row.get("privacy") == "public":
            when = parse_when(row.get("uploadedAt"), tz)
        if when:
            names = used.setdefault(when.date(), [])
            if row.get("slug") not in names:
                names.append(row.get("slug") or "")
    return used


def next_slot(used: list[date] | set[date] | dict, schedule: ScheduleConfig, now: datetime,
              tz: tzinfo | None = None) -> datetime:
    """The later of tomorrow and (the latest used day + every_days), at schedule.time, local time."""
    day = as_local(now, tz).date() + timedelta(days=1)
    if used:
        day = max(day, max(used) + timedelta(days=schedule.every_days))
    return as_local(datetime.combine(day, schedule.clock), tz)


def assign_slot(config: Config, slug: str, now: datetime | None = None, tz: tzinfo | None = None, *,
                reserved: tuple[date, ...] = ()) -> datetime | None:
    """
    The next free slot for this beat, or None when the chain is switched off.
    `reserved`: days held by the other beats of a --folder batch, whose drafts are not
    approved yet (or, in a dry run, not even written).
    """
    if not config.schedule.enabled:
        return None
    now = now or datetime.now().astimezone()
    used = set(used_dates(config, skip_slug=slug, tz=tz)) | set(reserved)
    return next_slot(used, config.schedule, now, tz)


# ----------------------------------------------------------------------------- the queue
def beat_entry(slug: str, folder: Path, tz: tzinfo | None = None) -> QueueEntry:
    """One beat folder: how far it is and when its video goes (or went) public."""
    out = folder / "out"
    record = log.read_publish(out / log.PUBLISH_JSON)
    draft = _read_draft(folder)
    has_draft = draft is not None or (out / DRAFT_NAME).is_file()
    if record:
        when = (parse_when(record.get("publishAt"), tz) or parse_when(record.get("requestedPublishAt"), tz)
                or (parse_when(record.get("uploadedAt"), tz) if record.get("privacy") == "public" else None))
        return QueueEntry(slug, "uploaded", when, str(record.get("title") or ""), str(record.get("privacy") or ""))
    status = ("approved" if draft and draft.approved else "drafted" if has_draft
              else "rendered" if (out / "video.mp4").is_file() else "new")
    if draft:
        return QueueEntry(slug, status, parse_when(draft.publish_at, tz), draft.title, draft.privacy)
    return QueueEntry(slug, status)


def flag_queue(entries: list[QueueEntry], every_days: int, today: date) -> list[QueueEntry]:
    """
    Sort by date (beats without a date last) and mark what breaks the chain from today on:
    two beats on one day, a beat closer than every_days to the one before, or a gap.
    """
    dated = sorted((e for e in entries if e.when), key=lambda e: (e.when, e.slug))
    undated = sorted((e for e in entries if not e.when), key=lambda e: e.slug)
    flagged: list[QueueEntry] = []
    for index, entry in enumerate(dated):
        flags: list[str] = []
        day = entry.when.date()
        if day >= today:
            same = [e.slug for e in dated if e.when.date() == day and e.slug != entry.slug]
            if same:
                flags.append(f"duplicate: same day as {', '.join(same)}")
            earlier = [e for e in dated[:index] if e.when.date() < day]
            if earlier:
                days = (day - earlier[-1].when.date()).days
                if days > every_days:
                    flags.append(f"gap: {days} days after {earlier[-1].slug}")
                elif days < every_days:
                    flags.append(f"too close: {days} day{'s' if days != 1 else ''} after {earlier[-1].slug}")
        flagged.append(replace(entry, flags=tuple(flags)))
    return flagged + undated


def queue(config: Config, now: datetime | None = None, tz: tzinfo | None = None) -> list[QueueEntry]:
    now = now or datetime.now().astimezone()
    entries = [beat_entry(slug, folder, tz) for slug, folder in beat_folders(config.beats_dir)]
    return flag_queue(entries, config.schedule.every_days, as_local(now, tz).date())
