"""beats/<slug>/meta.yaml, the file-name parser and input discovery."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import Config, Defaults

META_NAME = "meta.yaml"
AUDIO_EXT = (".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
HEIC_EXT = (".heic", ".heif")
PRIVACY_VALUES = ("private", "unlisted", "public")
VIDEO_LAYOUTS = ("fit", "fill")
SHORT_LAYOUTS = ("fill", "wide", "card")
SHORT_MAX_SECONDS = 180
SHORT_DEFAULT_SECONDS = 30.0     # long enough to hear two sections of a beat (the owner's choice; was 15)

HEIC_HINT = ("HEIC photos aren't supported — send the photo to yourself via WhatsApp/mail "
             "(it becomes a JPG) or export it as JPG/PNG")


class MetaError(Exception):
    """A beat folder or its meta.yaml is unusable; the message is safe to show to the owner."""


# ----------------------------------------------------------------------------- file name
# Ported unchanged from scripts/render.py — keep the two in sync.
BPM_RE = re.compile(r"(\d{2,3})\s*bpm", re.I)
KEY_RE = re.compile(r"(?<![A-Za-z])([A-G])([#♯]|b|♭)?\s*(?i:(minor|min|m|major|maj))?(?![A-Za-z0-9])")
HANDLE_RE = re.compile(r"@([A-Za-z0-9_.]+)")


def parse_beat_name(stem: str) -> dict:
    """
    'Tuesday Night 160 Bpm A#m @caprybeats @sinfulyts @1parkerhill'
      → title 'Tuesday Night', bpm 160, key 'A# minor', handles [caprybeats, sinfulyts, 1parkerhill]
    Anything not found is left empty (bpm 0, key ''); with no info at all the title is the stem.
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
        acc = {"#": "#", "♯": "#", "b": "b", "♭": "b"}.get(km.group(2) or "", "")
        mode = (km.group(3) or "").lower()
        info["key"] = km.group(1) + acc + (" minor" if mode.startswith("m") and not mode.startswith("maj") else " major")
        if in_title:
            title = title[: len(title) - 7 + km.start()]
    info["title"] = re.sub(r"[\s\-–|]+$", "", title).strip() or stem
    return info


def collabs_from_handles(handles: list[str], artist: str) -> list[str]:
    """@handles in the file name minus the owner's own."""
    return [h for h in handles if h.lower() != artist.lower()]


def credit_line(artist: str, collabs: list[str]) -> str:
    """The credit used everywhere (frames, descriptions, ID3 TPE1 without the 'prod. ')."""
    return "prod. " + " x ".join([artist] + list(collabs))


# ----------------------------------------------------------------------------- inputs
def find_audio(folder: Path) -> Path | None:
    """First *.wav in the folder, else the first other audio file; None if there is none."""
    audios = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT)
    wavs = [p for p in audios if p.suffix.lower() == ".wav"]
    return (wavs or audios or [None])[0]


def find_cover(folder: Path) -> Path | None:
    """First JPG/PNG/WEBP in the folder; None if there is none. A HEIC-only folder is an error."""
    files = sorted(p for p in folder.iterdir() if p.is_file())
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    if images:
        return images[0]
    heic = [p for p in files if p.suffix.lower() in HEIC_EXT]
    if heic:
        raise MetaError(f"{heic[0].name}: {HEIC_HINT}")
    return None


def find_inputs(folder: Path) -> tuple[Path, Path]:
    """The audio file and the cover photo of a beat folder; MetaError if either is missing."""
    if not folder.is_dir():
        raise MetaError(f"beat folder not found: {folder}")
    audio = find_audio(folder)
    if audio is None:
        raise MetaError(f"no audio file ({', '.join(AUDIO_EXT)}) in {folder}")
    cover = find_cover(folder)
    if cover is None:
        raise MetaError(f"no cover photo ({', '.join(IMAGE_EXT)}) in {folder}")
    return audio, cover


def beat_dir(beats_dir: Path, slug: str) -> Path:
    """beats/<slug>, after checking that the slug is one plain folder name."""
    slug = slug.strip()
    if not slug or slug in (".", "..") or re.search(r'[\\/:*?"<>|]', slug):
        raise MetaError(f"'{slug}' is not a usable folder name (no slashes or \\ / : * ? \" < > |)")
    return beats_dir / slug


# ----------------------------------------------------------------------------- values
def parse_time(value: Any) -> float:
    """'1:04' → 64.0, '64' / 64 / 64.5 → seconds. ValueError if it is neither."""
    if isinstance(value, bool):
        raise ValueError(f"not a time: {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        parts = str(value).strip().split(":")
        if not 1 <= len(parts) <= 3 or not all(parts):
            raise ValueError(f"not a time: {value!r}")
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError(f"not a time: {value!r}")
    return seconds


def parse_publish(value: Any, now: datetime | None = None) -> tuple[str, datetime | None]:
    """
    '' → ('', None)                      use the default privacy
    'private' | 'unlisted' | 'public'    → (that, None)
    ISO datetime in the future           → ('private', aware datetime)   scheduled (publishAt)
    A datetime without a UTC offset is taken as this computer's local time.
    """
    if value is None or value == "":
        return "", None
    if isinstance(value, datetime):          # PyYAML turns an unquoted ISO datetime into one
        when = value
    elif isinstance(value, date):
        raise ValueError(f"{value} has no time — write it like {value}T18:00:00")
    else:
        text = str(value).strip()
        if text.lower() in PRIVACY_VALUES:
            return text.lower(), None
        try:
            when = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{text!r} is not private, unlisted, public or a date like "
                             "2026-11-20T18:00:00+01:00") from None
    if when.tzinfo is None:
        when = when.astimezone()
    now = now or datetime.now().astimezone()
    if when <= now:
        raise ValueError(f"{when.isoformat()} is in the past")
    return "private", when


# ----------------------------------------------------------------------------- meta.yaml
@dataclass(frozen=True)
class Meta:
    title: str = ""
    bpm: int = 0                      # 0 = unknown
    key: str = ""
    collabs: list[str] = field(default_factory=list)
    type_beat: list[str] = field(default_factory=list)
    similar: list[str] = field(default_factory=list)
    mood: list[str] = field(default_factory=list)
    free: bool = True
    beatstars_url: str = ""
    playlist: str = ""
    publish: str = ""
    tags_extra: list[str] = field(default_factory=list)
    text_overlay: bool = False
    zoom: bool = False
    video_layout: str = "fit"
    video_margin: float = 0.0
    short: bool = True
    short_start: str = ""
    short_length: float = SHORT_DEFAULT_SECONDS
    short_layout: str = "fill"
    short_text: bool = False
    short_wave: bool = False
    hook_text: str = ""
    short_publish: str = ""
    warnings: tuple[str, ...] = ()


META_KEYS = tuple(f.name for f in fields(Meta) if f.name != "warnings")


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        raise MetaError(f"{META_NAME}: {key} must be a single value, not a list")
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def _flag(data: dict, key: str, default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise MetaError(f"{META_NAME}: {key} must be true or false (got {value!r})")
    return value


def _number(data: dict, key: str, default: float) -> float:
    value = data.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetaError(f"{META_NAME}: {key} must be a number (got {value!r})")
    return float(value)


def _names(data: dict, key: str) -> list[str]:
    """A YAML list of names; a single name is accepted as a one-item list."""
    value = data.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list) or any(isinstance(v, (dict, list)) for v in value):
        raise MetaError(f"{META_NAME}: {key} must be a list like [\"Dro Kenji\", \"Juice WRLD\"]")
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def _merge_names(first: list[str], second: list[str]) -> list[str]:
    """first + second without repeats (case-insensitive), order kept."""
    seen: set[str] = set()
    merged = []
    for name in first + second:
        if name.lower() not in seen:
            seen.add(name.lower())
            merged.append(name)
    return merged


def build_meta(data: Any, audio_stem: str, artist: str, defaults: Defaults | None = None, *,
               for_draft: bool = False, now: datetime | None = None) -> Meta:
    """
    Validate the parsed YAML of meta.yaml (None = no file) and resolve every key:
    meta.yaml if present and non-empty → config.defaults (type_beat, similar, mood, free,
    playlist) → the audio file name (title, bpm, key, collabs) → built-in default.
    tags_extra is the exception: the beat's own list is ADDED to config.defaults.tags_extra.
    `audio_stem` may be '' (no audio yet). `for_draft` additionally requires title and
    type_beat. Unknown keys become warnings.
    """
    defaults = defaults or Defaults()
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise MetaError(f"{META_NAME} must be a list of `key: value` lines")
    warnings = [f"{META_NAME}: unknown key '{k}' (ignored)" for k in data if k not in META_KEYS]
    parsed = parse_beat_name(audio_stem) if audio_stem else {"title": "", "bpm": 0, "key": "", "handles": []}

    bpm_value = _number(data, "bpm", 0) or parsed["bpm"]
    if bpm_value != int(bpm_value):
        raise MetaError(f"{META_NAME}: bpm must be a whole number (got {bpm_value})")
    bpm = int(bpm_value)
    if bpm and not 40 <= bpm <= 300:
        raise MetaError(f"bpm must be between 40 and 300 (got {bpm})")

    # like title/bpm/key: empty in meta.yaml = whatever the file name says
    collabs = [c.lstrip("@") for c in _names(data, "collabs")] \
        or collabs_from_handles(parsed["handles"], artist)

    for key in ("publish", "short_publish"):
        try:
            parse_publish(data.get(key), now)
        except ValueError as exc:
            raise MetaError(f"{META_NAME}: {key}: {exc}") from None

    short_start = _text(data, "short_start")
    if short_start:
        try:
            parse_time(short_start)
        except ValueError:
            raise MetaError(f"{META_NAME}: short_start must be seconds or m:ss, like \"1:04\" "
                            f"(got {short_start!r})") from None
    short_length = _number(data, "short_length", SHORT_DEFAULT_SECONDS)
    if not 3 <= short_length <= SHORT_MAX_SECONDS:
        raise MetaError(f"{META_NAME}: short_length must be between 3 and {SHORT_MAX_SECONDS} seconds (got {short_length:g})")
    short_layout = _text(data, "short_layout").lower() or "fill"
    if short_layout not in SHORT_LAYOUTS:
        raise MetaError(f"{META_NAME}: short_layout must be one of {', '.join(SHORT_LAYOUTS)} (got {short_layout!r})")
    video_layout = _text(data, "video_layout").lower() or "fit"
    if video_layout not in VIDEO_LAYOUTS:
        raise MetaError(f"{META_NAME}: video_layout must be one of {', '.join(VIDEO_LAYOUTS)} (got {video_layout!r})")
    video_margin = _number(data, "video_margin", 0.0)
    if not 0 <= video_margin <= 0.4:
        raise MetaError(f"{META_NAME}: video_margin must be between 0 and 0.4 (got {video_margin:g})")

    meta = Meta(
        title=_text(data, "title") or parsed["title"],
        bpm=bpm,
        key=_text(data, "key") or parsed["key"],
        collabs=collabs,
        type_beat=_names(data, "type_beat") or list(defaults.type_beat),
        similar=_names(data, "similar") or list(defaults.similar),
        mood=_names(data, "mood") or list(defaults.mood),
        free=_flag(data, "free", defaults.free),
        beatstars_url=_text(data, "beatstars_url"),
        playlist=_text(data, "playlist") or defaults.playlist,
        publish=_text(data, "publish"),
        # the one key that adds instead of overriding: the channel's extras, then this beat's
        tags_extra=_merge_names(list(defaults.tags_extra), _names(data, "tags_extra")),
        text_overlay=_flag(data, "text_overlay", False),
        zoom=_flag(data, "zoom", False),
        video_layout=video_layout,
        video_margin=video_margin,
        short=_flag(data, "short", True),
        short_start=short_start,
        short_length=short_length,
        short_layout=short_layout,
        short_text=_flag(data, "short_text", False),
        short_wave=_flag(data, "short_wave", False),
        hook_text=_text(data, "hook_text"),
        short_publish=_text(data, "short_publish"),
        warnings=tuple(warnings),
    )
    if for_draft:
        if not meta.title:
            raise MetaError(f"no title: set `title:` in {META_NAME} or put it in the audio file name")
        if not meta.type_beat:
            raise MetaError("no type_beat: list at least one artist under `defaults:` in config.yaml "
                            f"or as `type_beat:` in {META_NAME}, e.g. [\"Dro Kenji\", \"Juice WRLD\"]")
    return meta


def load_meta(folder: Path, config: Config, *, for_draft: bool = False, now: datetime | None = None) -> Meta:
    """beats/<slug>/meta.yaml (optional) resolved against config.defaults and the audio file name."""
    if not folder.is_dir():
        raise MetaError(f"beat folder not found: {folder}")
    path = folder / META_NAME
    data: Any = None
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise MetaError(f"{path} is not valid YAML: {exc}") from None
    audio = find_audio(folder)
    return build_meta(data, audio.stem if audio else "", config.artist, config.defaults,
                      for_draft=for_draft, now=now)


# ----------------------------------------------------------------------------- `beatpub new`
def _y(value: Any) -> str:
    """A YAML scalar or flow list (JSON is valid YAML and survives quotes and emoji)."""
    return json.dumps(value, ensure_ascii=False)


def _option(text: str, comment: str) -> str:
    """One switched-off line of a new meta.yaml: '# key: value      # what it does'."""
    return f"{'# ' + text:<50} # {comment}\n"


def meta_yaml_text(title: str, bpm: int, key: str, collabs: list[str],
                   defaults: Defaults | None = None, source: str = "") -> str:
    """
    The text of a new meta.yaml: only what the audio file name says is written as values;
    the channel defaults and every per-beat option are comments the owner can switch on.
    """
    d = defaults or Defaults()
    if source:
        name_lines = [
            f"# read from the file name: {source}",
            f"title: {_y(title)}",
            f"bpm: {bpm}" if bpm else "# bpm:                           (not in the file name)",
            f"key: {_y(key)}" if key else "# key:                           (not in the file name)",
            f"collabs: {_y(collabs)}" if collabs else
            "# collabs: []                    (no co-producer @handles in the file name)",
        ]
    else:
        name_lines = [
            "# No audio file was in the folder yet. Title, BPM, key and collabs are read from",
            "# its name when you render; set them here only if the name is unusual.",
            f"title: {_y(title)}" if title else '# title: ""',
            "# bpm:",
            '# key: ""',
            "# collabs: []",
        ]
    return "".join([
        "# Only this beat's own values are set here. Everything else comes from config.yaml → defaults.\n",
        '# To change something for THIS beat only, remove the leading "# " from a line and edit it.\n',
        "\n",
        "\n".join(name_lines) + "\n",
        "\n",
        "# channel defaults in use (config.yaml → defaults)\n",
        _option(f"type_beat: {_y(list(d.type_beat))}", "the artists in the title, in order"),
        _option(f"similar: {_y(list(d.similar))}", "same-lane artists: tags only"),
        _option(f"mood: {_y(list(d.mood))}", '→ "<mood> type beat" tags'),
        _option(f"free: {_y(d.free)}", "(FREE) in the title and the free hashtags (tags are not affected)"),
        _option(f"playlist: {_y(d.playlist)}", "exact playlist name; empty = config.playlist_default"),
        "\n",
        "# this beat\n",
        _option("tags_extra: []", "extra tags for this beat, ADDED to the channel's"
                + (f" ({', '.join(d.tags_extra)})" if d.tags_extra else " (none set in config.yaml)")),
        _option('beatstars_url: ""','this beat\'s own bsta.rs link → the "Purchase:" line (empty = no Purchase line)'),
        _option('publish: ""', "private, unlisted, public, or 2026-11-20T18:00:00+01:00; empty = next free slot "
                               "(config.yaml → schedule), else privacy_default"),
        _option("text_overlay: false", "true = title/credit on the 16:9 video frame"),
        _option("zoom: false", "true = slow zoom on the 16:9 video (slower render)"),
        _option("video_layout: fit", "fit = picture uncropped | fill = cropped to 16:9"),
        _option("video_margin: 0", "e.g. 0.05 to leave a little room around the picture"),
        "\n",
        "# the Short (vertical preview)\n",
        _option("short: true", "false = no Short for this beat"),
        _option('short_start: ""', 'force the section start ("1:04"); empty = loudest bar-aligned window'),
        _option(f"short_length: {SHORT_DEFAULT_SECONDS:g}", "seconds, rounded to whole bars (up to 180)"),
        _option("short_layout: fill", "fill | wide | card"),
        _option("short_text: false", "true = title + credit written on the picture"),
        _option("short_wave: false", "true = moving waveform across the lower part"),
        _option('hook_text: ""', "one line under the picture"),
        _option('short_publish: ""', "like publish; empty = config.shorts.publish_offset_hours after the video"),
    ])
