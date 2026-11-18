"""Load and validate config.yaml, and locate the project root."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = "config.yaml"
PRIVACY_VALUES = ("private", "unlisted", "public")


class ConfigError(Exception):
    """config.yaml is missing or invalid; the message is safe to show to the owner."""


@dataclass(frozen=True)
class ShortsConfig:
    enabled: bool = True
    playlist: str = ""
    publish_offset_hours: float = 2.0


@dataclass(frozen=True)
class Defaults:
    """Channel defaults: used for every beat unless its meta.yaml overrides the key."""
    type_beat: tuple[str, ...] = ()
    similar: tuple[str, ...] = ()
    mood: tuple[str, ...] = ()
    free: bool = True
    tags_extra: tuple[str, ...] = ()
    playlist: str = ""


@dataclass(frozen=True)
class Config:
    root: Path
    artist: str
    beat_store_url: str = ""
    contact_email: str = ""
    socials: str = ""
    privacy_default: str = "private"
    audit_passed: bool = False
    playlist_default: str = ""
    notify_subscribers: bool = True
    category_id: str = "10"
    default_language: str = "en"
    ai_model: str = ""
    defaults: Defaults = field(default_factory=Defaults)
    shorts: ShortsConfig = field(default_factory=ShortsConfig)
    warnings: tuple[str, ...] = ()

    @property
    def beats_dir(self) -> Path:
        return self.root / "beats"

    @property
    def client_secret_path(self) -> Path:
        return self.root / "client_secret.json"

    @property
    def token_path(self) -> Path:
        return self.root / "token.json"


def find_root(start: Path | None = None) -> Path:
    """
    The project root is the folder that holds config.yaml:
    $BEATPUB_HOME, else the current folder or one of its parents, else the folder this
    package was installed from (`pip install -e .`), so `beatpub` works from anywhere.
    """
    env = os.environ.get("BEATPUB_HOME")
    if env:
        root = Path(env).expanduser()
        if not (root / CONFIG_NAME).is_file():
            raise ConfigError(f"BEATPUB_HOME is set to {root}, but there is no {CONFIG_NAME} in it")
        return root.resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder
    package_parent = Path(__file__).resolve().parent.parent
    if (package_parent / CONFIG_NAME).is_file():
        return package_parent
    raise ConfigError(f"{CONFIG_NAME} not found: run beatpub from the project folder (or set BEATPUB_HOME)")


def _typed(data: dict[str, Any], key: str, kind: type | tuple[type, ...], default: Any, where: str = "") -> Any:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool) and kind is not bool:      # bool is an int in Python; don't accept it as one
        raise ConfigError(f"{CONFIG_NAME}: {where}{key} must not be true/false")
    if not isinstance(value, kind):
        wanted = "true or false" if kind is bool else "a number" if kind == (int, float) else "text"
        raise ConfigError(f"{CONFIG_NAME}: {where}{key} must be {wanted} (got {value!r})")
    return value


def _names(data: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    """A YAML list of names; a single name is accepted as a one-item list."""
    value = data.get(key)
    if value is None or value == "":
        return ()
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, list) or any(isinstance(v, (dict, list, bool)) for v in value):
        raise ConfigError(f"{CONFIG_NAME}: {where}{key} must be a list like [\"Dro Kenji\", \"Juice WRLD\"]")
    return tuple(str(v).strip() for v in value if v is not None and str(v).strip())


def parse_defaults(raw: Any) -> tuple[Defaults, list[str]]:
    if raw is None:
        return Defaults(), []
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME}: defaults must be a block of indented `key: value` lines")
    known = {f.name for f in fields(Defaults)}
    warnings = [f"{CONFIG_NAME}: unknown key 'defaults.{k}' (ignored)" for k in raw if k not in known]
    return Defaults(
        type_beat=_names(raw, "type_beat", "defaults."),
        similar=_names(raw, "similar", "defaults."),
        mood=_names(raw, "mood", "defaults."),
        free=_typed(raw, "free", bool, True, "defaults."),
        tags_extra=_names(raw, "tags_extra", "defaults."),
        playlist=str(_typed(raw, "playlist", str, "", "defaults.")).strip(),
    ), warnings


def parse_config(data: Any, root: Path) -> Config:
    """Validate the parsed YAML of config.yaml. Unknown keys become warnings, not errors."""
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_NAME} must be a list of `key: value` lines")
    known = {f.name for f in fields(Config)} - {"root", "warnings"}
    warnings = [f"{CONFIG_NAME}: unknown key '{k}' (ignored)" for k in data if k not in known]

    artist = str(_typed(data, "artist", str, "")).strip()
    if not artist:
        raise ConfigError(f"{CONFIG_NAME}: artist is required (your producer name)")

    privacy = str(_typed(data, "privacy_default", str, "private")).strip().lower()
    if privacy not in PRIVACY_VALUES:
        raise ConfigError(f"{CONFIG_NAME}: privacy_default must be one of {', '.join(PRIVACY_VALUES)} (got {privacy!r})")
    audit_passed = _typed(data, "audit_passed", bool, False)
    if privacy != "private" and not audit_passed:
        warnings.append(f"{CONFIG_NAME}: privacy_default is {privacy} but audit_passed is false — "
                        "YouTube forces every upload to private until the API audit passes")

    defaults, defaults_warnings = parse_defaults(data.get("defaults"))
    warnings += defaults_warnings

    raw_shorts = data.get("shorts") or {}
    if not isinstance(raw_shorts, dict):
        raise ConfigError(f"{CONFIG_NAME}: shorts must be a block of indented `key: value` lines")
    shorts_known = {f.name for f in fields(ShortsConfig)}
    warnings += [f"{CONFIG_NAME}: unknown key 'shorts.{k}' (ignored)" for k in raw_shorts if k not in shorts_known]
    offset = float(_typed(raw_shorts, "publish_offset_hours", (int, float), 2.0, "shorts."))
    if offset < 0:
        raise ConfigError(f"{CONFIG_NAME}: shorts.publish_offset_hours must be 0 or more")
    shorts = ShortsConfig(
        enabled=_typed(raw_shorts, "enabled", bool, True, "shorts."),
        playlist=str(_typed(raw_shorts, "playlist", str, "", "shorts.")).strip(),
        publish_offset_hours=offset,
    )

    category = data.get("category_id", "10")              # `category_id: 10` without quotes is fine too
    if isinstance(category, bool) or not str(category).strip().isdigit():
        raise ConfigError(f"{CONFIG_NAME}: category_id must be a number like \"10\" (got {category!r})")

    return Config(
        root=root,
        artist=artist,
        beat_store_url=str(_typed(data, "beat_store_url", str, "")).strip(),
        contact_email=str(_typed(data, "contact_email", str, "")).strip(),
        socials=str(_typed(data, "socials", str, "")).strip(),
        privacy_default=privacy,
        audit_passed=audit_passed,
        playlist_default=str(_typed(data, "playlist_default", str, "")).strip(),
        notify_subscribers=_typed(data, "notify_subscribers", bool, True),
        category_id=str(category).strip(),
        default_language=str(_typed(data, "default_language", str, "en")).strip() or "en",
        ai_model=str(_typed(data, "ai_model", str, "")).strip(),
        defaults=defaults,
        shorts=shorts,
        warnings=tuple(warnings),
    )


def load_config(root: Path | None = None) -> Config:
    root = root or find_root()
    path = root / CONFIG_NAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} not found") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{CONFIG_NAME} is not valid YAML: {exc}") from None
    return parse_config(data, root)
