"""Titles, descriptions and tags from the templates (+ optional AI), within YouTube's limits."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jinja2
import yaml

from .config import Config
from .meta import Meta, credit_line, parse_publish

TITLE_MAX = 100            # characters
DESCRIPTION_MAX = 5000     # bytes (YouTube counts the description in UTF-8 bytes)
TAGS_MAX = 500             # characters, counted the way YouTube counts them (see tags_length)
SHORT_TAGS = 10            # the Short gets the video's first N tags + "shorts"
URL_PLACEHOLDER = "{youtube_url}"   # filled in by `short upload` once the video exists
DRAFT_NAME, SHORT_DRAFT_NAME = "draft.yaml", "short_draft.yaml"
STATE_NAME = "draft_state.json"
TEMPLATE_FILES = ("title.txt", "description.txt", "tags.yaml", "short_title.txt", "short_description.txt")


class DraftError(Exception):
    """A draft could not be built or is not valid; the message is safe to show to the owner."""


@dataclass(frozen=True)
class Draft:
    title: str
    description: str
    tags: list[str]
    privacy: str                    # private | unlisted | public
    publish_at: str = ""            # RFC 3339; only together with privacy: private
    playlist: str = ""
    category_id: str = "10"
    approved: bool = False
    notes: tuple[str, ...] = field(default=(), compare=False)   # what was trimmed; printed, never saved


# ----------------------------------------------------------------------------- lengths
def text_length(text: str) -> int:
    """Characters as YouTube counts them (an emoji outside the basic plane counts as two)."""
    return len(text.encode("utf-16-le")) // 2


def byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def tags_length(tags: list[str]) -> int:
    """
    YouTube's 500-character rule: the tags joined with commas, where a tag that contains
    a space is counted as if it were wrapped in quotes (two extra characters).
    """
    return sum(text_length(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def clean_line(text: str) -> str:
    """One line, no angle brackets (YouTube rejects them), single spaces."""
    return re.sub(r"\s+", " ", text.replace("<", "").replace(">", "")).strip()


def clean_block(text: str) -> str:
    """Multi-line text without angle brackets, trailing spaces or more than one blank line in a row."""
    text = text.replace("<", "").replace(">", "").replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ----------------------------------------------------------------------------- tags
def load_tag_pools(templates: Path) -> dict[str, list[str]]:
    path = templates / "tags.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise DraftError(f"{path} not found") from None
    except yaml.YAMLError as exc:
        raise DraftError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise DraftError(f"{path} must hold the lists per_artist, per_similar, per_mood and base")
    pools: dict[str, list[str]] = {}
    for key in ("per_artist", "per_similar", "per_mood", "base"):
        value = data.get(key) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise DraftError(f"{path}: {key} must be a list of tags")
        pools[key] = value
    return pools


def clean_tag(tag: str) -> str:
    return clean_line(tag.replace(",", " ").replace('"', ""))


def fit_tags(tags: list[str], limit: int = TAGS_MAX) -> tuple[list[str], list[str]]:
    """Drop tags from the end of the list until YouTube's limit is met → (kept, dropped)."""
    kept = list(tags)
    while kept and tags_length(kept) > limit:
        kept.pop()
    return kept, tags[len(kept):]


def build_tags(meta: Meta, pools: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """
    per_artist for each type_beat, per_similar for each similar, per_mood for each mood,
    base, tags_extra — in that order (= priority). Deduped case-insensitively, then trimmed
    from the end to 500 characters → (tags, dropped). The "free …" tags stay in even when
    meta.free is false (the owner's choice): `free` only changes the title and the hashtags.
    """
    raw: list[str] = []
    for artist in meta.type_beat:
        raw += [p.replace("{a}", artist.lower()) for p in pools["per_artist"]]
    for artist in meta.similar:
        raw += [p.replace("{a}", artist.lower()) for p in pools["per_similar"]]
    for mood in meta.mood:
        raw += [p.replace("{m}", mood.lower()) for p in pools["per_mood"]]
    raw += pools["base"]
    raw += meta.tags_extra
    seen: set[str] = set()
    tags: list[str] = []
    for tag in map(clean_tag, raw):
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
    return fit_tags(tags)


# ----------------------------------------------------------------------------- templates
def template_env(templates: Path) -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(str(templates), encoding="utf-8"),
                              undefined=jinja2.StrictUndefined, autoescape=False, keep_trailing_newline=False)


def render_template(env: jinja2.Environment, name: str, context: dict[str, Any]) -> str:
    try:
        return env.get_template(name).render(**context)
    except jinja2.TemplateNotFound:
        raise DraftError(f"templates/{name} not found") from None
    except jinja2.TemplateError as exc:
        raise DraftError(f"templates/{name}: {exc}") from None


def base_context(meta: Meta, config: Config) -> dict[str, Any]:
    """Everything a template may use. Owner-specific values come from config.yaml / meta.yaml only."""
    socials = config.socials.strip()
    return {
        "title": meta.title, "bpm": meta.bpm, "key": meta.key, "free": meta.free,
        "type_beat": list(meta.type_beat), "similar": list(meta.similar), "mood": list(meta.mood),
        "collabs": list(meta.collabs), "artist": config.artist, "credit": credit_line(config.artist, meta.collabs),
        "beatstars_url": meta.beatstars_url, "beat_store_url": config.beat_store_url,
        "socials": socials + "\n" if socials else "", "contact_email": config.contact_email,
        "hook_text": meta.hook_text, "tags": [], "keyword_line": "", "youtube_url": URL_PLACEHOLDER,
    }


def fit_title(env: jinja2.Environment, name: str, context: dict[str, Any]) -> tuple[str, list[str]]:
    """
    Render a title template within 100 characters: shorten the type-beat list first,
    then the beat title word by word — never mid-word. → (title, notes about what was cut)
    """
    artists, words = list(context["type_beat"]), str(context["title"]).split()
    notes: list[str] = []

    def attempt() -> str:
        return clean_line(render_template(env, name, {**context, "type_beat": artists, "title": " ".join(words)}))

    title = attempt()
    while text_length(title) > TITLE_MAX and len(artists) > 1:
        notes.append(f"title over {TITLE_MAX} characters: left out {artists.pop()}")
        title = attempt()
    cut = False
    while text_length(title) > TITLE_MAX and len(words) > 1:
        words.pop()
        cut = True
        title = attempt()
    if cut:
        notes.append(f"title over {TITLE_MAX} characters: beat title shortened to \"{' '.join(words)}\"")
    if text_length(title) > TITLE_MAX:
        raise DraftError(f"the title is {text_length(title)} characters and cannot be shortened to {TITLE_MAX} "
                         f"without cutting a word: {title}")
    return title, notes


def fit_description(env: jinja2.Environment, name: str, context: dict[str, Any], tags: list[str]) -> str:
    """Render a description within 5000 bytes; only the (TAGS) keyword line may be shortened."""
    shown = list(tags)
    while True:
        text = clean_block(render_template(env, name, {**context, "tags": shown, "keyword_line": ", ".join(shown)}))
        if byte_length(text) <= DESCRIPTION_MAX:
            return text
        if not shown:
            raise DraftError(f"the description from templates/{name} is {byte_length(text)} bytes; "
                             f"YouTube allows {DESCRIPTION_MAX} — shorten the template or socials in config.yaml")
        shown.pop()


# ----------------------------------------------------------------------------- privacy
def rfc3339(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def video_schedule(meta: Meta, config: Config, now: datetime | None = None,
                   slot: datetime | None = None) -> tuple[str, str]:
    """
    (privacy, publish_at) for the video: meta.publish if set; else the slot the scheduling
    chain assigned (private until then); else config.privacy_default.
    """
    privacy, when = parse_publish(meta.publish, now)
    if not privacy and slot is not None:
        return "private", rfc3339(slot)
    return (privacy or config.privacy_default), (rfc3339(when) if when else "")


def short_schedule(meta: Meta, config: Config, video: tuple[str, str], now: datetime | None = None) -> tuple[str, str]:
    """
    (privacy, publish_at) for the Short: short_publish if set; else the video's schedule
    + config.shorts.publish_offset_hours; a video that is public/unlisted/private now →
    the Short gets the same status now.
    """
    privacy, when = parse_publish(meta.short_publish, now)
    if privacy:
        return privacy, (rfc3339(when) if when else "")
    video_privacy, video_at = video
    if video_at:
        later = datetime.fromisoformat(video_at) + timedelta(hours=config.shorts.publish_offset_hours)
        return "private", rfc3339(later)
    return video_privacy, ""


def audit_reminder(config: Config, *drafts: Draft) -> str:
    """The reminder to print when something other than private is requested before the audit passed."""
    if config.audit_passed or all(d.privacy == "private" and not d.publish_at for d in drafts):
        return ""
    return ("reminder: audit_passed is false in config.yaml — until the YouTube API audit passes, YouTube "
            "forces every upload to private (and ignores schedules), whatever the draft says")


# ----------------------------------------------------------------------------- drafts
def build_drafts(meta: Meta, config: Config, templates: Path, now: datetime | None = None,
                 slot: datetime | None = None) -> tuple[Draft, Draft]:
    """
    The video draft and the Short draft from meta + config + templates/. Nothing is written.
    `slot` is the publish time from the scheduling chain; an explicit meta.publish wins over it.
    """
    if not meta.title:
        raise DraftError("no title: put it in the audio file name or set `title:` in meta.yaml")
    if not meta.type_beat:
        raise DraftError("no type_beat: list at least one artist under `defaults:` in config.yaml or in meta.yaml")
    env = template_env(templates)
    context = base_context(meta, config)
    tags, dropped = build_tags(meta, load_tag_pools(templates))
    notes = [f"tags over {TAGS_MAX} characters: dropped {', '.join(dropped)}"] if dropped else []

    title, title_notes = fit_title(env, "title.txt", context)
    schedule = video_schedule(meta, config, now, slot)
    video = Draft(title=title, description=fit_description(env, "description.txt", context, tags), tags=tags,
                  privacy=schedule[0], publish_at=schedule[1],
                  playlist=meta.playlist or config.playlist_default, category_id=config.category_id,
                  notes=tuple(title_notes + notes))

    short_title, short_notes = fit_title(env, "short_title.txt", context)
    short_tags, _ = fit_tags(tags[:SHORT_TAGS] + ([] if "shorts" in (t.lower() for t in tags[:SHORT_TAGS]) else ["shorts"]))
    short_when = short_schedule(meta, config, schedule, now)
    short = Draft(title=short_title, description=fit_description(env, "short_description.txt", context, short_tags),
                  tags=short_tags, privacy=short_when[0], publish_at=short_when[1],
                  playlist=config.shorts.playlist, category_id=config.category_id, notes=tuple(short_notes))
    return video, short


def validate_draft(draft: Draft, now: datetime | None = None) -> list[str]:
    """Everything YouTube would reject in a (possibly hand-edited) draft. Empty list = fine."""
    problems: list[str] = []
    if not draft.title.strip():
        problems.append("title is empty")
    if text_length(draft.title) > TITLE_MAX:
        problems.append(f"title is {text_length(draft.title)} characters (max {TITLE_MAX})")
    if "\n" in draft.title.strip():
        problems.append("title must be one line")
    if byte_length(draft.description) > DESCRIPTION_MAX:
        problems.append(f"description is {byte_length(draft.description)} bytes (max {DESCRIPTION_MAX})")
    for name, text in (("title", draft.title), ("description", draft.description)):
        if "<" in text or ">" in text:
            problems.append(f"{name} contains < or > (YouTube rejects them)")
    if tags_length(draft.tags) > TAGS_MAX:
        problems.append(f"tags add up to {tags_length(draft.tags)} characters (max {TAGS_MAX}) — remove some")
    if any("<" in t or ">" in t or "," in t for t in draft.tags):
        problems.append("a tag contains < > or a comma")
    if draft.privacy not in ("private", "unlisted", "public"):
        problems.append(f"privacy must be private, unlisted or public (got {draft.privacy!r})")
    if draft.publish_at:
        if draft.privacy != "private":
            problems.append("publish_at only works together with privacy: private")
        try:
            when = datetime.fromisoformat(draft.publish_at)
            if when.tzinfo is None:
                when = when.astimezone()
            if when <= (now or datetime.now().astimezone()):
                problems.append(f"publish_at {draft.publish_at} is in the past")
        except ValueError:
            problems.append(f"publish_at {draft.publish_at!r} is not a date like 2026-11-20T18:00:00+01:00")
    if not draft.category_id.isdigit():
        problems.append(f"category_id must be a number (got {draft.category_id!r})")
    return problems


# ----------------------------------------------------------------------------- draft files
class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None                # descriptions as readable blocks
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)

DRAFT_HEADER = ("# {what} — edit anything here, then run: beatpub approve {slug}\n"
                "# Nothing is uploaded while approved is false. `beatpub draft {slug} --force` rewrites this file.\n")


def draft_text(draft: Draft, what: str, slug: str) -> str:
    data = {k: v for k, v in asdict(draft).items() if k != "notes"}
    body = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10_000, default_flow_style=False)
    return DRAFT_HEADER.format(what=what, slug=slug) + body


def write_draft(path: Path, draft: Draft, what: str, slug: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft_text(draft, what, slug), encoding="utf-8", newline="\n")


def read_draft(path: Path) -> Draft:
    """Load a draft file as the owner left it. DraftError if it is missing or not a draft."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DraftError(f"{path} not found — run `beatpub draft {path.parent.parent.name}` first") from None
    except yaml.YAMLError as exc:
        raise DraftError(f"{path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise DraftError(f"{path} is not a draft (expected title, description, tags …)")
    tags = data.get("tags") or []
    if isinstance(tags, str):                              # "a, b, c" pasted as one line
        tags = [t for t in (part.strip() for part in tags.split(",")) if t]
    if not isinstance(tags, list):
        raise DraftError(f"{path}: tags must be a list")
    publish_at = data.get("publish_at") or ""
    if isinstance(publish_at, datetime):                   # an unquoted date comes back as a datetime
        publish_at = rfc3339(publish_at if publish_at.tzinfo else publish_at.astimezone())
    approved = data.get("approved", False)
    if not isinstance(approved, bool):
        raise DraftError(f"{path}: approved must be true or false")
    return Draft(title=str(data.get("title") or "").strip(), description=str(data.get("description") or "").strip(),
                 tags=[str(t).strip() for t in tags if str(t).strip()],
                 privacy=str(data.get("privacy") or "").strip().lower(), publish_at=str(publish_at).strip(),
                 playlist=str(data.get("playlist") or "").strip(), category_id=str(data.get("category_id") or "").strip(),
                 approved=approved)


def approve_file(path: Path, what: str, slug: str) -> Draft:
    """Set approved: true in a draft file, keeping the owner's edits."""
    draft = replace(read_draft(path), approved=True)
    write_draft(path, draft, what, slug)
    return draft


# ----------------------------------------------------------------------------- when to redraft
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs_fingerprint(meta: Meta, config: Config, templates: Path, ai: bool) -> str:
    """Changes whenever something the drafts are made from changes (meta, config, templates, --ai)."""
    files = {name: (templates / name).read_text(encoding="utf-8") if (templates / name).is_file() else ""
             for name in TEMPLATE_FILES}
    config_part = {k: v for k, v in asdict(config).items() if k not in ("root", "warnings")}
    meta_part = {k: v for k, v in asdict(meta).items() if k != "warnings"}
    return _sha(json.dumps([meta_part, config_part, files, ai], sort_keys=True, default=str, ensure_ascii=False))


def load_state(out: Path) -> dict[str, Any]:
    try:
        data = json.loads((out / STATE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(out: Path, fingerprint: str, names: tuple[str, ...] = (DRAFT_NAME, SHORT_DRAFT_NAME)) -> None:
    """Remember what the drafts were made from and what the files looked like when written."""
    written = {name: _sha((out / name).read_text(encoding="utf-8")) for name in names}
    (out / STATE_NAME).write_text(json.dumps({"inputs": fingerprint, "written": written}, indent=2), encoding="utf-8")


def draft_status(out: Path, fingerprint: str, names: tuple[str, ...] = (DRAFT_NAME, SHORT_DRAFT_NAME)) -> str:
    """
    missing   no drafts yet                                  → write them
    current   drafts were made from exactly these inputs      → keep them
    stale     inputs changed, drafts untouched since written  → safe to rewrite
    edited    inputs changed, but the owner edited/approved   → keep them, say so
    """
    paths = [out / name for name in names]
    if not all(p.is_file() for p in paths):
        return "missing"
    state = load_state(out)
    if state.get("inputs") == fingerprint:
        return "current"
    written = state.get("written") or {}
    untouched = all(written.get(p.name) == _sha(p.read_text(encoding="utf-8")) for p in paths)
    return "stale" if untouched else "edited"


# ----------------------------------------------------------------------------- optional AI
AI_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "tags"],
    "additionalProperties": False,
}

AI_SYSTEM = """\
You write the YouTube title, description and tags for a beat video on a producer's own channel.

The producer already has a house style that works for them, and a template has produced a draft \
in that style. Your job is to return a version that fits this particular beat at least as well: \
keep the structure, the links, the hashtag line and the "(TAGS)" block of the template draft, and \
change wording or tag choice only where it makes the video easier to find for people searching for \
this kind of beat. When examples of the channel's past videos are given, treat the best-viewed ones \
as the reference for tone and tag choice.

Hard limits, because YouTube rejects anything else: the title is one line of at most 100 characters; \
the description is at most 5000 bytes; neither contains the characters < or >; tags contain no commas \
and add up to at most 500 characters when joined with commas (a tag with a space counts two extra). \
Keep every URL exactly as it appears in the template draft, and keep the artist names from the beat's \
type_beat list in the title in the same order. The last part of the description is the line "(TAGS)" \
followed by your final tag list joined with ", "."""


def load_examples(templates: Path, limit: int = 15) -> list[dict[str, Any]]:
    """The channel's best-viewed videos from `beatpub learn`, if it has been run."""
    try:
        data = json.loads((templates / "learned" / "examples.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    videos = data.get("videos", data) if isinstance(data, dict) else data
    if not isinstance(videos, list):
        return []
    videos = [v for v in videos if isinstance(v, dict)]
    videos.sort(key=lambda v: int(v.get("viewCount") or 0), reverse=True)
    return [{k: v.get(k) for k in ("title", "description", "tags", "viewCount")} for v in videos[:limit]]


def ai_unavailable(config: Config) -> str:
    """Why `--ai` cannot run ('' = it can). Never prints the key."""
    if not config.ai_model:
        return "ai_model is empty in config.yaml"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "the anthropic package is not installed (python -m pip install anthropic)"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return "no API key: set the ANTHROPIC_API_KEY environment variable (a pay-as-you-go key from console.anthropic.com)"
    return ""


def ai_request(meta: Meta, config: Config, video: Draft, examples: list[dict[str, Any]]) -> dict[str, Any]:
    """The exact request sent for `draft --ai` (also what --dry-run prints)."""
    beat = {k: v for k, v in asdict(meta).items()
            if k in ("title", "bpm", "key", "collabs", "type_beat", "similar", "mood", "free")}
    parts = ["The beat:\n" + json.dumps(beat, ensure_ascii=False, indent=2),
             "The template draft:\n" + json.dumps({"title": video.title, "description": video.description,
                                                   "tags": video.tags}, ensure_ascii=False, indent=2)]
    if examples:
        parts.append("The channel's best-viewed videos, most views first:\n"
                     + json.dumps(examples, ensure_ascii=False, indent=2))
    return {
        "model": config.ai_model,
        "max_tokens": 16000,
        "system": AI_SYSTEM,
        "messages": [{"role": "user", "content": "\n\n".join(parts)}],
        "output_config": {"format": {"type": "json_schema", "schema": AI_SCHEMA}},
    }


def apply_ai_result(video: Draft, data: dict[str, Any]) -> Draft:
    """Take the model's title/description/tags, but never trust it with the limits."""
    notes = list(video.notes)
    title = clean_line(str(data.get("title") or ""))
    if not title or text_length(title) > TITLE_MAX:
        notes.append("AI title was empty or over 100 characters — kept the template title")
        title = video.title
    seen: set[str] = set()
    tags = []
    for tag in map(clean_tag, [str(t) for t in data.get("tags") or []]):
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    tags, dropped = fit_tags(tags or video.tags)
    if dropped:
        notes.append(f"AI tags over {TAGS_MAX} characters: dropped {', '.join(dropped)}")
    description = clean_block(str(data.get("description") or ""))
    if not description or byte_length(description) > DESCRIPTION_MAX:
        notes.append("AI description was empty or too long — kept the template description")
        description = video.description
    elif "(TAGS)" in description:                          # the keyword line must match the final tags
        description = description.split("(TAGS)")[0].rstrip() + "\n\n(TAGS)\n" + ", ".join(tags)
        if byte_length(description) > DESCRIPTION_MAX:
            description = video.description
    return replace(video, title=title, description=description, tags=tags, approved=False, notes=tuple(notes))


def ai_draft(meta: Meta, config: Config, video: Draft, templates: Path) -> Draft:
    """Ask Claude for a title, description and tags in the channel's style. DraftError on any failure."""
    import anthropic

    request = ai_request(meta, config, video, load_examples(templates))
    try:
        response = anthropic.Anthropic().messages.create(**request)
    except anthropic.AuthenticationError:
        raise DraftError("the Claude API rejected the key in ANTHROPIC_API_KEY") from None
    except anthropic.NotFoundError:
        raise DraftError(f"the Claude API does not know the model '{config.ai_model}' (ai_model in config.yaml)") from None
    except anthropic.RateLimitError:
        raise DraftError("the Claude API is rate-limiting this key — try again in a minute") from None
    except anthropic.APIStatusError as exc:
        raise DraftError(f"the Claude API returned an error ({exc.status_code}): {exc.message}") from None
    except anthropic.APIConnectionError:
        raise DraftError("could not reach the Claude API — check the internet connection") from None
    if response.stop_reason != "end_turn":
        raise DraftError(f"the Claude API stopped early ({response.stop_reason})")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        raise DraftError("the Claude API answer was not the expected JSON") from None
    if not isinstance(data, dict):
        raise DraftError("the Claude API answer was not the expected JSON")
    return apply_ai_result(video, data)
