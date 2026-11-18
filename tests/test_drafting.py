"""Drafts: the house format, YouTube's limits, the draft files, when to redraft. No network."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from beatpub import drafting
from beatpub.cli import app
from beatpub.config import parse_config
from beatpub.drafting import Draft, DraftError
from beatpub.meta import build_meta

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
STEM = "Ran Away 142 Bpm Fm @caprybeats"
CONFIG_DATA = {
    "artist": "CapryBeats",
    "beat_store_url": "https://www.beatstars.com/capry",
    "socials": "IG: https://www.instagram.com/caprybeats/\n",
    "playlist_default": "(FREE) Type Beats 2025",
    "defaults": {"type_beat": ["Dro Kenji", "Juice WRLD"], "similar": ["Shaun Sloan", "Polo G", "Lil Durk"],
                 "mood": ["melodic"], "free": True},
}
CONFIG = parse_config(CONFIG_DATA, ROOT)
POOLS = drafting.load_tag_pools(TEMPLATES)


def make(meta_data: dict | None = None, config=CONFIG, stem: str = STEM) -> tuple[Draft, Draft]:
    meta = build_meta(meta_data, stem, config.artist, config.defaults, for_draft=True, now=NOW)
    return drafting.build_drafts(meta, config, TEMPLATES, now=NOW)


# ----------------------------------------------------------------------------- the house format
def test_house_format():
    video, short = make({"beatstars_url": "https://bsta.rs/nUkiAU"})
    assert video.title == "(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes"
    head, keyword_line = video.description.split("(TAGS)\n")
    assert head == ("#typebeat #freebeat #freeforprofittypebeat\n\n"
                    "Purchase: https://bsta.rs/nUkiAU\n\n"
                    "My other Beats: https://www.beatstars.com/capry\n\n"
                    "IG: https://www.instagram.com/caprybeats/\n\n")
    assert keyword_line == ", ".join(video.tags)
    assert video.tags[:6] == ["dro kenji type beat", "dro kenji beats", "free dro kenji type beat",
                              "juice wrld type beat", "juice wrld beats", "free juice wrld type beat"]
    assert video.tags[6:9] == ["shaun sloan type beat", "polo g type beat", "lil durk type beat"]
    assert video.tags[9:11] == ["melodic type beat", "free melodic type beat"] and video.tags[11] == "type beat"
    assert (video.privacy, video.publish_at, video.playlist, video.category_id, video.approved) == \
        ("private", "", "(FREE) Type Beats 2025", "10", False)
    assert drafting.validate_draft(video, NOW) == [] and drafting.validate_draft(short, NOW) == []
    assert 470 <= drafting.tags_length(video.tags) <= 500           # the channel fills the tag box


def test_short_draft():
    video, short = make()
    assert short.title == "Ran Away - Dro Kenji x Juice WRLD Type Beat (FREE)"
    assert short.description.startswith("Full beat: {youtube_url}\n\nIG: ")
    assert short.description.endswith("#shorts #typebeat #drokenjitypebeat")
    assert short.tags == video.tags[:10] + ["shorts"] and short.playlist == ""
    linked = make({"beatstars_url": "https://bsta.rs/nUkiAU"})[1]
    assert linked.description.startswith("Full beat: {youtube_url}\nPurchase: https://bsta.rs/nUkiAU\n\nIG: ")


def test_without_a_beat_link_there_is_no_purchase_line():
    video, short = make()
    assert "Purchase" not in video.description and "Purchase" not in short.description
    assert video.description.startswith("#typebeat #freebeat #freeforprofittypebeat\n\n"
                                        "My other Beats: https://www.beatstars.com/capry\n\n")


def test_not_free():
    video, short = make({"free": False})
    assert video.title == "Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes"
    assert video.description.startswith("#typebeat #beats #drokenjitypebeat\n")
    assert "(FREE)" not in short.title
    free_video, _ = make()
    assert video.tags == free_video.tags                              # the "free …" tags stay in either way
    assert "free dro kenji type beat" in video.tags and "free type beat" in video.tags


def test_title_with_quotes_emoji_and_angle_brackets():
    video, _ = make({"title": 'Say "Less" 🔥 <3'})
    assert video.title == '(FREE) Say "Less" 🔥 3 Type Beat | Dro Kenji x Juice WRLD Vibes'
    assert drafting.validate_draft(video, NOW) == []


# ----------------------------------------------------------------------------- limits
def test_long_title_drops_artists_first():
    artists = ["Dro Kenji", "Juice WRLD", "The Kid LAROI", "Iann Dior", "Trippie Redd", "Lil Uzi Vert", "Polo G"]
    video, _ = make({"type_beat": artists})
    assert drafting.text_length(video.title) <= 100
    assert video.title == "(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD x The Kid LAROI x Iann Dior x Trippie Redd Vibes"
    assert any("left out Polo G" in n for n in video.notes) and any("Lil Uzi Vert" in n for n in video.notes)


def test_long_title_then_shortens_the_beat_title_on_a_word():
    long_title = "Running Away From Everything I Ever Knew And Loved In This Cold Town Tonight Forever And Always Again"
    video, short = make({"title": long_title, "type_beat": ["Dro Kenji", "Juice WRLD"]})
    for draft in (video, short):
        assert drafting.text_length(draft.title) <= 100
    assert video.title.startswith("(FREE) Running Away From") and video.title.endswith(" Type Beat | Dro Kenji Vibes")
    kept = video.title.removeprefix("(FREE) ").removesuffix(" Type Beat | Dro Kenji Vibes")
    assert long_title.startswith(kept + " ")                          # cut between words, never inside one
    assert any("shortened" in n for n in video.notes)


def test_title_that_cannot_fit_is_an_error():
    with pytest.raises(DraftError, match="cannot be shortened"):
        make({"title": "x" * 120})


def test_emoji_count_double_in_the_title_limit():
    assert drafting.text_length("🔥") == 2 and drafting.text_length("abc") == 3


def test_tag_length_rule():
    assert drafting.tags_length([]) == 0
    assert drafting.tags_length(["rap"]) == 3
    assert drafting.tags_length(["rap", "rnb"]) == 7                  # the comma counts
    assert drafting.tags_length(["type beat"]) == 11                  # a space → counted with quotes
    assert drafting.tags_length(["type beat", "rap"]) == 15
    kept, dropped = drafting.fit_tags(["a" * 250, "b" * 249, "c"])   # 250 + 1 + 249 = 500 exactly
    assert kept == ["a" * 250, "b" * 249] and dropped == ["c"]
    kept, dropped = drafting.fit_tags(["a a", "b"], limit=5)          # "a a" counts 5
    assert kept == ["a a"] and dropped == ["b"]


def test_tags_dedupe_and_trim_from_the_end():
    meta = build_meta({"type_beat": ["Dro Kenji"], "similar": ["dro kenji"], "mood": [],
                       "tags_extra": ["Type Beat", "my, tag", "x" * 480]}, STEM, "CapryBeats")
    tags, dropped = drafting.build_tags(meta, POOLS)
    assert tags.count("dro kenji type beat") == 1                     # similar repeated the artist
    assert "Type Beat" not in tags and "my tag" in tags               # case-insensitive dedupe; commas removed
    assert dropped == ["x" * 480] and drafting.tags_length(tags) <= 500
    many = build_meta({"similar": [f"Artist Number {i}" for i in range(40)]}, STEM, "CapryBeats", CONFIG.defaults)
    tags, dropped = drafting.build_tags(many, POOLS)
    assert drafting.tags_length(tags) <= 500 and dropped
    assert tags[:3] == ["dro kenji type beat", "dro kenji beats", "free dro kenji type beat"]   # priority kept


def test_channel_and_beat_tags_extra_both_reach_the_draft():
    config = parse_config({**CONFIG_DATA, "defaults": {"type_beat": ["Dro Kenji"], "tags_extra": ["capry"]}}, ROOT)
    video, _ = make({"tags_extra": ["guitar"]}, config)
    assert video.tags[-2:] == ["capry", "guitar"]


def test_description_limit_shortens_only_the_keyword_line():
    config = parse_config({**CONFIG_DATA, "socials": "é" * 2300}, ROOT)                 # 4600 bytes of socials
    video, _ = make(None, config)
    assert drafting.byte_length(video.description) <= 5000 and "(TAGS)" in video.description
    assert len(video.description.split("(TAGS)")[1].split(",")) < len(video.tags)       # tags themselves untouched
    with pytest.raises(DraftError, match="5000"):
        make(None, parse_config({**CONFIG_DATA, "socials": "é" * 2600}, ROOT))


# ----------------------------------------------------------------------------- privacy and schedule
def test_schedule():
    video, short = make({"publish": "2026-11-20T18:00:00+01:00"})
    assert (video.privacy, video.publish_at) == ("private", "2026-11-20T18:00:00+01:00")
    assert (short.privacy, short.publish_at) == ("private", "2026-11-20T20:00:00+01:00")   # + 2 h offset
    video, short = make({"publish": "public"})
    assert (video.privacy, short.privacy, short.publish_at) == ("public", "public", "")
    video, short = make({"publish": "public", "short_publish": "2026-12-01T10:00:00+00:00"})
    assert (short.privacy, short.publish_at) == ("private", "2026-12-01T10:00:00+00:00")
    video, short = make({"short_publish": "unlisted"})
    assert (video.privacy, short.privacy) == ("private", "unlisted")


def test_audit_reminder():
    private, _ = make()
    public, _ = make({"publish": "public"})
    scheduled, _ = make({"publish": "2026-11-20T18:00:00+01:00"})
    assert drafting.audit_reminder(CONFIG, private) == ""
    assert "audit_passed" in drafting.audit_reminder(CONFIG, public)
    assert "audit_passed" in drafting.audit_reminder(CONFIG, private, scheduled)
    assert drafting.audit_reminder(parse_config({**CONFIG_DATA, "audit_passed": True}, ROOT), public) == ""


# ----------------------------------------------------------------------------- validation of edited drafts
def test_validate_draft():
    good, _ = make()
    cases = {
        "title is empty": replace(good, title=" "),
        "title is 101": replace(good, title="x" * 101),
        "title contains": replace(good, title="a > b"),
        "description is": replace(good, description="x" * 5001),
        "tags add up": replace(good, tags=good.tags + ["x" * 40]),
        "a tag contains": replace(good, tags=["a,b"]),
        "privacy must": replace(good, privacy="hidden"),
        "only works together": replace(good, privacy="public", publish_at="2026-11-20T18:00:00+01:00"),
        "in the past": replace(good, publish_at="2020-01-01T00:00:00+00:00"),
        "is not a date": replace(good, publish_at="friday"),
    }
    for expected, draft in cases.items():
        assert any(expected in p for p in drafting.validate_draft(draft, NOW)), expected


# ----------------------------------------------------------------------------- files
def test_draft_file_round_trip(tmp_path: Path):
    video, _ = make({"title": 'Say "Less" 🔥'})
    path = tmp_path / "out" / "draft.yaml"
    drafting.write_draft(path, video, "YouTube video draft", "say-less")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# YouTube video draft") and "approved: false" in text
    assert "description: |" in text and "\n  My other Beats: " in text     # readable, editable block
    assert drafting.read_draft(path) == video
    approved = drafting.approve_file(path, "YouTube video draft", "say-less")
    assert approved.approved and drafting.read_draft(path) == replace(video, approved=True)


def test_read_draft_tolerates_hand_edits(tmp_path: Path):
    path = tmp_path / "out" / "draft.yaml"
    path.parent.mkdir()
    path.write_text("title: My Title\ndescription: one line\ntags: a, b b ,c\nprivacy: Public\n"
                    "publish_at: 2026-11-20T18:00:00+01:00\ncategory_id: 10\n", encoding="utf-8")
    draft = drafting.read_draft(path)
    assert draft.tags == ["a", "b b", "c"] and draft.privacy == "public" and draft.category_id == "10"
    assert draft.publish_at == "2026-11-20T18:00:00+01:00" and draft.approved is False
    with pytest.raises(DraftError, match="beatpub draft"):
        drafting.read_draft(tmp_path / "out" / "nope.yaml")
    path.write_text("approved: maybe\n", encoding="utf-8")
    with pytest.raises(DraftError, match="approved"):
        drafting.read_draft(path)


def test_draft_status(tmp_path: Path):
    out = tmp_path / "out"
    video, short = make()
    meta = build_meta(None, STEM, "CapryBeats", CONFIG.defaults)
    fp = drafting.inputs_fingerprint(meta, CONFIG, TEMPLATES, ai=False)
    assert drafting.draft_status(out, fp) == "missing"
    drafting.write_draft(out / "draft.yaml", video, "v", "s")
    drafting.write_draft(out / "short_draft.yaml", short, "s", "s")
    drafting.save_state(out, fp)
    assert drafting.draft_status(out, fp) == "current"
    changed = drafting.inputs_fingerprint(replace(meta, mood=["dark"]), CONFIG, TEMPLATES, ai=False)
    assert changed != fp and drafting.inputs_fingerprint(meta, CONFIG, TEMPLATES, ai=True) != fp
    assert drafting.draft_status(out, changed) == "stale"            # untouched → safe to rewrite
    drafting.approve_file(out / "draft.yaml", "v", "s")
    assert drafting.draft_status(out, fp) == "current"
    assert drafting.draft_status(out, changed) == "edited"           # approved/edited → never overwritten silently


# ----------------------------------------------------------------------------- AI (no network)
def test_ai_falls_back_without_model_or_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert "ai_model" in drafting.ai_unavailable(CONFIG)
    with_model = parse_config({**CONFIG_DATA, "ai_model": "claude-opus-5-5"}, ROOT)
    reason = drafting.ai_unavailable(with_model)
    assert "ANTHROPIC_API_KEY" in reason or "anthropic package" in reason


def test_ai_request_and_result_handling():
    video, _ = make()
    meta = build_meta(None, STEM, "CapryBeats", CONFIG.defaults)
    config = parse_config({**CONFIG_DATA, "ai_model": "claude-opus-5-5"}, ROOT)
    request = drafting.ai_request(meta, config, video, [{"title": "Old", "viewCount": "900"}])
    assert request["model"] == "claude-opus-5-5" and request["output_config"]["format"]["type"] == "json_schema"
    content = request["messages"][0]["content"]
    assert video.title in content and "best-viewed" in content and "Dro Kenji" in content

    better = drafting.apply_ai_result(video, {
        "title": "(FREE) Ran Away | Dro Kenji x Juice WRLD Type Beat",
        "description": "#typebeat\n\nPurchase: https://www.beatstars.com/capry\n\n(TAGS)\nstale, list",
        "tags": ["dro kenji type beat", "Dro Kenji Type Beat", "sad, guitar"] + ["x" * 480]})
    assert better.title == "(FREE) Ran Away | Dro Kenji x Juice WRLD Type Beat" and better.approved is False
    assert better.tags == ["dro kenji type beat", "sad guitar"]
    assert better.description.endswith("(TAGS)\ndro kenji type beat, sad guitar")       # keyword line = final tags
    assert drafting.validate_draft(better, NOW) == []

    bad = drafting.apply_ai_result(video, {"title": "x" * 150, "description": "", "tags": []})
    assert (bad.title, bad.description, bad.tags) == (video.title, video.description, video.tags)
    assert len(bad.notes) >= 2


# ----------------------------------------------------------------------------- the commands
@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    import shutil
    import yaml
    root = tmp_path / "Používateľ pub"
    shutil.copytree(TEMPLATES, root / "templates", ignore=shutil.ignore_patterns("learned"))
    (root / "config.yaml").write_text(yaml.safe_dump(CONFIG_DATA, allow_unicode=True), encoding="utf-8")
    beat = root / "beats" / "ran-away"
    beat.mkdir(parents=True)
    (beat / f"{STEM}.mp3").touch()
    monkeypatch.setenv("BEATPUB_HOME", str(root))
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    return root


def run(*args: str, code: int = 0, input: str | None = None) -> str:
    result = CliRunner().invoke(app, list(args), input=input)
    assert result.exit_code == code, result.output
    return result.output


def test_draft_and_approve_commands(project: Path):
    out = project / "beats" / "ran-away" / "out"
    assert "nothing written" in run("draft", "ran-away", "--dry-run") and not out.exists()

    output = run("draft", "ran-away")                                 # no meta.yaml: file name + defaults
    assert "(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes" in output
    video = drafting.read_draft(out / "draft.yaml")
    assert video.approved is False and "{youtube_url}" in drafting.read_draft(out / "short_draft.yaml").description
    assert "up to date" in run("draft", "ran-away")

    assert "no terminal" in run("approve", "ran-away", code=1)        # never approves without a yes
    assert drafting.read_draft(out / "draft.yaml").approved is False

    text = (out / "draft.yaml").read_text(encoding="utf-8")           # the owner breaks a limit by hand
    (out / "draft.yaml").write_text(text.replace("title: (FREE) Ran Away", "title: " + "x" * 101 + " Ran Away"),
                                    encoding="utf-8")
    assert "characters (max 100)" in run("approve", "ran-away", "--yes", code=1)
    assert drafting.read_draft(out / "short_draft.yaml").approved is False

    (out / "draft.yaml").write_text(text.replace("Ran Away Type Beat", "Ran Away (Guitar) Type Beat"), encoding="utf-8")
    assert "approved: draft.yaml, short_draft.yaml" in run("approve", "ran-away", "--yes")
    approved = drafting.read_draft(out / "draft.yaml")
    assert approved.approved and "(Guitar)" in approved.title         # the edit survived approval
    assert drafting.read_draft(out / "short_draft.yaml").approved
    assert "already approved" in run("approve", "ran-away", "--yes")

    # meta changes afterwards: the approved, edited draft is kept until --force
    (project / "beats" / "ran-away" / "meta.yaml").write_text("mood: [dark]\n", encoding="utf-8")
    assert "were kept" in run("draft", "ran-away")
    assert drafting.read_draft(out / "draft.yaml").approved
    run("draft", "ran-away", "--force")
    fresh = drafting.read_draft(out / "draft.yaml")
    assert fresh.approved is False and "dark type beat" in fresh.tags and "(Guitar)" not in fresh.title


def test_draft_rewrites_untouched_drafts_when_meta_changes(project: Path):
    beat = project / "beats" / "ran-away"
    run("draft", "ran-away")
    (beat / "meta.yaml").write_text("type_beat: [Polo G]\nshort: false\n", encoding="utf-8")
    output = run("draft", "ran-away")
    assert "(FREE) Ran Away Type Beat | Polo G Vibes" in output and "Short draft" not in output
    assert "approved: draft.yaml" in run("approve", "ran-away", "--yes")


def test_draft_errors_and_ai_fallback(project: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "using the template draft" in run("draft", "ran-away", "--ai")
    beat = project / "beats" / "ran-away"
    (beat / "meta.yaml").write_text("publish: public\n", encoding="utf-8")
    assert "audit_passed is false" in run("draft", "ran-away")
    (beat / "meta.yaml").write_text("publish: 2020-01-01T10:00:00+00:00\n", encoding="utf-8")
    assert "in the past" in run("draft", "ran-away", code=1)
    assert "draft" in run("approve", "nope", code=1)
