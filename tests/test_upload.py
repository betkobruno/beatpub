"""Step 5 without the network: request bodies, the resumable upload loop, upload/run/stats commands, the log."""
import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from beatpub import drafting, log, youtube
from beatpub.cli import app
from beatpub.drafting import Draft
from beatpub.youtube import YouTubeError

ROOT = Path(__file__).resolve().parent.parent
STEM = "Ran Away 142 Bpm Fm @caprybeats"
DRAFT = Draft(title="(FREE) Ran Away Type Beat | Dro Kenji Vibes", description="#typebeat\n\n(TAGS)\na, b",
              tags=["a", "b"], privacy="private", playlist="(FREE) Type Beats 2025", category_id="10", approved=True)


# ----------------------------------------------------------------------------- request body
def test_build_body():
    body = youtube.build_body(DRAFT, "en")
    assert body == {
        "snippet": {"title": DRAFT.title, "description": DRAFT.description, "tags": ["a", "b"],
                    "categoryId": "10", "defaultLanguage": "en"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "license": "youtube",
                   "embeddable": True, "publicStatsViewable": True},
    }
    from dataclasses import replace
    scheduled = youtube.build_body(replace(DRAFT, publish_at="2026-11-20T18:00:00+01:00"))
    assert scheduled["status"]["publishAt"] == "2026-11-20T18:00:00+01:00"
    public = youtube.build_body(replace(DRAFT, privacy="public", publish_at="2026-11-20T18:00:00+01:00"))
    assert "publishAt" not in public["status"]                        # only together with private


# ----------------------------------------------------------------------------- a fake API that records writes
class Status:
    def __init__(self, sent):
        self.resumable_progress = sent


class HttpError500(Exception):
    pass


def http_error(status: int, reason: str = "backendError"):
    from googleapiclient.errors import HttpError

    class Resp(dict):
        pass
    resp = Resp()
    resp.status, resp.reason = status, "x"
    return HttpError(resp, json.dumps({"error": {"message": "nope", "errors": [{"reason": reason}]}}).encode())


class InsertRequest:
    """next_chunk() plays a script: numbers = bytes sent so far, exceptions are raised, a dict ends the upload."""

    def __init__(self, script):
        self.script = list(script)

    def next_chunk(self):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return (None, step) if isinstance(step, dict) else (Status(step), None)


class Simple:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeService:
    def __init__(self, script=None, playlists=None, thumbnail_error=None, stats=None, forced_private=True):
        self.script = script
        self.playlist_pages = playlists if playlists is not None else [[("PL1", "(FREE) Type Beats 2025")]]
        self.thumbnail_error, self.stats, self.forced_private = thumbnail_error, stats or {}, forced_private
        self.calls: list[tuple[str, dict]] = []
        self.uploads = 0
        self.processing = ["succeeded"]         # answers to processingDetails polls; the last one repeats
        self.thumbnail_errors: list = []        # refusals for the next thumbnails.set calls, then thumbnail_error

    def _record(self, name, params):
        self.calls.append((name, {k: v for k, v in params.items() if k != "media_body"}))

    def videos(self):
        service = self

        class R:
            def insert(self, **params):
                service._record("videos.insert", params)
                service.uploads += 1
                video_id = f"VID{service.uploads}"
                privacy = "private" if service.forced_private else params["body"]["status"]["privacyStatus"]
                done = {"id": video_id, "status": {"privacyStatus": privacy}}
                return InsertRequest(service.script if service.script is not None else [done])

            def list(self, **params):
                service._record("videos.list", params)
                if params["part"] == "processingDetails":
                    status = service.processing.pop(0) if len(service.processing) > 1 else service.processing[0]
                    if isinstance(status, Exception):
                        return Simple(status)
                    return Simple({"items": [{"id": params["id"], "processingDetails": {"processingStatus": status}}]})
                return Simple({"items": [{"id": i, "statistics": service.stats[i]}
                                         for i in params["id"].split(",") if i in service.stats]})
        return R()

    def thumbnails(self):
        service = self

        class R:
            def set(self, **params):
                service._record("thumbnails.set", params)
                if service.thumbnail_errors:
                    return Simple(service.thumbnail_errors.pop(0))
                return Simple(service.thumbnail_error or {})
        return R()

    def playlists(self):
        service = self

        class R:
            def list(self, **params):
                service._record("playlists.list", params)
                page = int(params.get("pageToken") or 0)
                answer = {"items": [{"id": i, "snippet": {"title": t}} for i, t in service.playlist_pages[page]]}
                if page + 1 < len(service.playlist_pages):
                    answer["nextPageToken"] = str(page + 1)
                return Simple(answer)
        return R()

    def playlistItems(self):
        service = self

        class R:
            def insert(self, **params):
                service._record("playlistItems.insert", params)
                return Simple({})
        return R()


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"0" * 1000)
    return path


# ----------------------------------------------------------------------------- the resumable loop
def test_upload_reports_progress_and_returns_the_video(media: Path):
    service = FakeService(script=[400, 800, {"id": "abc123", "status": {"privacyStatus": "private"}}])
    seen = []
    response = youtube.upload_video(service, media, {"snippet": {}}, notify_subscribers=False,
                                    progress=lambda sent, total: seen.append((sent, total)))
    assert response["id"] == "abc123" and seen == [(400, 1000), (800, 1000), (1000, 1000)]
    name, params = service.calls[0]
    assert name == "videos.insert" and params == {"part": "snippet,status", "body": {"snippet": {}},
                                                  "notifySubscribers": False}


def test_upload_retries_dropped_connections_and_5xx_then_continues(media: Path):
    import httplib2
    script = [400, ConnectionResetError(), http_error(503), httplib2.ServerNotFoundError("dns"), TimeoutError(),
              800, {"id": "abc123"}]
    naps = []
    response = youtube.upload_video(FakeService(script=script), media, {}, sleep=naps.append)
    assert response["id"] == "abc123" and len(naps) == 4
    assert [int(n) for n in naps] == [2, 4, 8, 16]                    # exponential backoff (+ jitter below 1 s)


def test_upload_gives_up_after_ten_failures_in_a_row(media: Path):
    naps = []
    with pytest.raises(YouTubeError, match="after 10 retries"):
        youtube.upload_video(FakeService(script=[http_error(500)] * 11), media, {}, sleep=naps.append)
    assert len(naps) == 10 and max(naps) < 65
    survives = [http_error(502)] * 10 + [500] + [http_error(502)] * 10 + [{"id": "ok"}]   # progress resets the count
    assert youtube.upload_video(FakeService(script=survives), media, {}, sleep=lambda _: None)["id"] == "ok"


def test_upload_does_not_retry_real_refusals(media: Path):
    naps = []
    with pytest.raises(YouTubeError, match="quota"):
        youtube.upload_video(FakeService(script=[http_error(403, "quotaExceeded")]), media, {}, sleep=naps.append)
    assert naps == []
    with pytest.raises(YouTubeError, match="no video id"):
        youtube.upload_video(FakeService(script=[{}]), media, {}, sleep=naps.append)


def test_find_playlist_paginates_and_matches_loosely():
    service = FakeService(playlists=[[("PL0", "Old")] * 2, [("PL7", "(free) type beats 2025 ")], [("PL9", "(FREE) Type Beats 2025")]])
    assert youtube.find_playlist(service, "(FREE) Type Beats 2025") == "PL9"          # exact wins over loose
    assert len([c for c in service.calls if c[0] == "playlists.list"]) == 3
    assert youtube.find_playlist(FakeService(playlists=[[("PL7", "(free) type beats 2025 ")]]), "(FREE) Type Beats 2025") == "PL7"
    assert youtube.find_playlist(FakeService(playlists=[[("PL0", "Old")]]), "Missing") is None


def test_real_client_accepts_our_write_requests(media: Path):
    """Build the real client offline and construct every write request (nothing is sent)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    service = build("youtube", "v3", credentials=Credentials(token="unused"), cache_discovery=False)
    insert = service.videos().insert(part="snippet,status", body=youtube.build_body(DRAFT), notifySubscribers=True,
                                     media_body=MediaFileUpload(str(media), mimetype="video/mp4",
                                                                chunksize=youtube.CHUNK_BYTES, resumable=True))
    assert insert.method == "POST" and "/upload/youtube/v3/videos" in insert.uri and insert.resumable is not None
    jpg = media.with_name("thumb.jpg")
    jpg.write_bytes(b"x")
    thumb = service.thumbnails().set(videoId="abc", media_body=MediaFileUpload(str(jpg), mimetype="image/jpeg"))
    assert "/upload/youtube/v3/thumbnails/set" in thumb.uri
    assert service.playlists().list(part="snippet", mine=True, maxResults=50, pageToken="x").method == "GET"
    item = service.playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": "PL1", "resourceId": {"kind": "youtube#video", "videoId": "abc"}}})
    assert item.method == "POST"
    assert service.videos().list(part="statistics", id="a,b", maxResults=50).method == "GET"


# ----------------------------------------------------------------------------- the log
def test_published_csv_is_append_only(tmp_path: Path):
    path = tmp_path / "published.csv"
    log.append_published(path, {"uploadedAt": "2026-11-17T10:00:00+00:00", "kind": "video", "slug": "a",
                                "videoId": "V1", "url": "https://youtu.be/V1", "title": 'Say "Less", 🔥', "privacy": "private"})
    first = path.read_bytes()
    log.append_published(path, {"uploadedAt": "2026-11-17T10:05:00+00:00", "kind": "short", "slug": "a", "videoId": "S1",
                                "title": "S", "hook_text": "open verse", "short_layout": "card", "unknown": "ignored"})
    assert path.read_bytes().startswith(first)
    rows = log.read_published(path)
    assert [r["videoId"] for r in rows] == ["V1", "S1"] and rows[0]["title"] == 'Say "Less", 🔥'
    assert rows[1]["hook_text"] == "open verse" and rows[1]["short_layout"] == "card"
    with path.open(encoding="utf-8", newline="") as fh:
        assert next(csv.reader(fh)) == list(log.FIELDS)
    assert log.read_published(tmp_path / "none.csv") == []


def test_recent_rows_and_stats_rows():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    rows = [{"videoId": "OLD", "kind": "video", "title": "old", "uploadedAt": (now - timedelta(days=40)).isoformat()},
            {"videoId": "V1", "kind": "video", "title": "v1", "uploadedAt": (now - timedelta(days=3)).isoformat()},
            {"videoId": "V2", "kind": "video", "title": "v2", "uploadedAt": (now - timedelta(days=2)).isoformat()},
            {"videoId": "S1", "kind": "short", "title": "s1", "uploadedAt": (now - timedelta(days=2)).isoformat()},
            {"videoId": "V1", "kind": "video", "title": "v1 again", "uploadedAt": (now - timedelta(days=1)).isoformat()}]
    assert [r["videoId"] for r in log.recent_rows(rows, 30, now)] == ["V1", "V2", "S1"]
    assert len(log.recent_rows(rows, 0, now)) == 4
    stats = {"V1": {"views": 5, "likes": 1, "comments": 0}, "V2": {"views": 50, "likes": 2, "comments": 3}}
    lines = log.stats_rows(log.recent_rows(rows, 30, now), stats, "now")
    assert [(l["videoId"], l["views"], l["found"]) for l in lines] == [("V2", 50, True), ("V1", 5, True), ("S1", "", False)]


# ----------------------------------------------------------------------------- the commands
CONFIG = {"artist": "CapryBeats", "beat_store_url": "https://www.beatstars.com/capry",
          "playlist_default": "(FREE) Type Beats 2025", "defaults": {"type_beat": ["Dro Kenji"]}}


@pytest.fixture(autouse=True)
def pauses(monkeypatch) -> list:
    """No real waiting in tests: every pause is only noted."""
    noted: list = []
    monkeypatch.setattr(youtube, "pause", noted.append)
    return noted


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "Používateľ pub"
    shutil.copytree(ROOT / "templates", root / "templates", ignore=shutil.ignore_patterns("learned"))
    (root / "config.yaml").write_text(yaml.safe_dump(CONFIG, allow_unicode=True), encoding="utf-8")
    beat = root / "beats" / "ran-away"
    (beat / "out").mkdir(parents=True)
    (beat / f"{STEM}.mp3").touch()
    for name in ("video.mp4", "short.mp4", "thumb.jpg", "short_cover.jpg"):
        (beat / "out" / name).write_bytes(b"0" * 2048)
    monkeypatch.setenv("BEATPUB_HOME", str(root))
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    return root


def no_network(config):
    raise AssertionError("this command must not touch the network")


def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


def test_dry_runs_print_the_full_requests_and_never_touch_the_network(project: Path, monkeypatch):
    monkeypatch.setattr(youtube, "get_service", no_network)
    out = project / "beats" / "ran-away" / "out"
    run("draft", "ran-away")
    output = run("upload", "ran-away", "--dry-run")
    assert "is not approved yet" in output and "upload/youtube/v3/videos?part=snippet,status" in output
    body = json.loads(output[output.index("{"):output.index("2. GET")])
    assert body == youtube.build_body(drafting.read_draft(out / "draft.yaml"), "en")
    assert "videos?part=processingDetails" in output
    assert "thumbnails/set" in output and '"(FREE) Type Beats 2025"' in output and "playlistItems" in output

    short = run("short", "upload", "ran-away", "--dry-run")
    short_body = json.loads(short[short.index("{"):short.index("2. GET")])
    assert "Full beat: https://youtu.be/VIDEO_ID" in short_body["snippet"]["description"]
    assert short_body["snippet"]["tags"][-1] == "shorts" and "no playlist set" in short
    assert not (out / "publish.json").exists() and not (project / "published.csv").exists()
    assert "videos?part=statistics" not in run("stats", "--dry-run")          # nothing published → nothing to ask


def test_upload_needs_approval_then_uploads_once(project: Path, monkeypatch):
    service = FakeService()
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    beat = project / "beats" / "ran-away"
    out = beat / "out"
    assert "beatpub draft" in run("upload", "ran-away", code=1)                # no draft yet
    run("draft", "ran-away")
    assert "not approved" in run("upload", "ran-away", code=1)
    assert "has not been uploaded yet" in run("short", "upload", "ran-away", "--yes", code=1)   # Short before video
    assert service.calls == []
    run("approve", "ran-away", "--yes")

    output = run("upload", "ran-away")
    assert "https://youtu.be/VID1" in output and "thumbnail set" in output and "added to playlist" in output
    record = json.loads((out / "publish.json").read_text(encoding="utf-8"))
    assert {k: record[k] for k in ("videoId", "url", "title", "privacy", "thumbnail_ok", "playlist_ok")} == {
        "videoId": "VID1", "url": "https://youtu.be/VID1", "title": "(FREE) Ran Away Type Beat | Dro Kenji Vibes",
        "privacy": "private", "thumbnail_ok": True, "playlist_ok": True}
    assert record["uploadedAt"] and record["publishAt"] == ""
    names = [name for name, _ in service.calls]
    assert names == ["videos.insert", "videos.list", "thumbnails.set", "playlists.list", "playlistItems.insert"]
    assert service.calls[0][1]["body"]["status"]["privacyStatus"] == "private"
    assert service.calls[1][1] == {"part": "processingDetails", "id": "VID1"}
    assert service.calls[4][1]["body"]["snippet"] == {"playlistId": "PL1",
                                                      "resourceId": {"kind": "youtube#video", "videoId": "VID1"}}

    assert "already uploaded: https://youtu.be/VID1" in run("upload", "ran-away")     # second run: nothing sent
    assert service.uploads == 1

    short = run("short", "upload", "ran-away")
    assert "https://youtu.be/VID2" in short
    sent = [params for name, params in service.calls if name == "videos.insert"][1]["body"]
    assert "Full beat: https://youtu.be/VID1" in sent["snippet"]["description"]
    assert "{youtube_url}" not in sent["snippet"]["description"]
    short_record = json.loads((out / "short_publish.json").read_text(encoding="utf-8"))
    assert short_record["linked_video"] == "https://youtu.be/VID1" and short_record["short_layout"] == "fill"
    assert short_record["playlist_ok"] is None                                  # no Shorts playlist configured

    rows = log.read_published(project / "published.csv")
    assert [(r["kind"], r["videoId"], r["slug"]) for r in rows] == [("video", "VID1", "ran-away"), ("short", "VID2", "ran-away")]
    assert rows[1]["linked_video"] == "https://youtu.be/VID1"

    assert "https://youtu.be/VID3" in run("upload", "ran-away", "--force")       # --force = a new upload
    assert len(log.read_published(project / "published.csv")) == 3


def test_follow_up_failures_keep_the_video(project: Path, monkeypatch, pauses):
    service = FakeService(thumbnail_error=http_error(403, "forbidden"), playlists=[[("PL0", "Something else")]])
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    run("draft", "ran-away")
    output = run("upload", "ran-away", "--yes")                                  # --yes: no approval needed
    assert "thumbnail not set" in output and "not added to a playlist" in output and "https://youtu.be/VID1" in output
    record = json.loads((project / "beats" / "ran-away" / "out" / "publish.json").read_text(encoding="utf-8"))
    assert record["thumbnail_ok"] is False and record["playlist_ok"] is False and "no playlist named" in record["playlist_error"]
    assert len(log.read_published(project / "published.csv")) == 1
    assert "beatpub thumbnail ran-away" in output and "still processing the video" in output
    assert [n for n, _ in service.calls].count("thumbnails.set") == 3           # the first try + two more
    assert pauses == [youtube.THUMBNAIL_RETRY_PAUSE_SECONDS] * 2


def test_upload_waits_for_processing_before_the_thumbnail(project: Path, monkeypatch, pauses):
    service = FakeService()
    service.processing = ["processing", "processing", "succeeded"]
    service.thumbnail_errors = [http_error(403, "forbidden")]                    # refused once, then accepted
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    run("draft", "ran-away")
    output = run("upload", "ran-away", "--yes")
    names = [name for name, _ in service.calls]
    assert names[:6] == ["videos.insert", "videos.list", "videos.list", "videos.list", "thumbnails.set", "thumbnails.set"]
    assert pauses == [10, 10, youtube.THUMBNAIL_RETRY_PAUSE_SECONDS]
    assert "YouTube finished processing" in output and "trying again in 20 s (1/2)" in output
    assert "thumbnail set" in output and "beatpub thumbnail" not in output
    record = json.loads((project / "beats" / "ran-away" / "out" / "publish.json").read_text(encoding="utf-8"))
    assert record["thumbnail_ok"] is True


def test_processing_wait_gives_up_after_ten_minutes_and_survives_errors(pauses):
    service = FakeService()
    service.processing = ["processing"]                                          # never finishes
    assert youtube.wait_until_processed(service, "VID1") == "processing"
    assert pauses == [10] * 60                                           # 10 minutes, then go on
    service.processing = ["failed"]
    assert youtube.wait_until_processed(service, "VID1") == "failed"
    service.processing = [http_error(500)]
    assert youtube.wait_until_processed(service, "VID1") == ""                   # asking failed: go on anyway


def test_thumbnail_command_retries_what_failed(project: Path, monkeypatch):
    service = FakeService(thumbnail_error=http_error(403, "forbidden"))
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    out = project / "beats" / "ran-away" / "out"
    assert "not uploaded yet" in run("thumbnail", "ran-away")                    # nothing uploaded: nothing sent
    assert service.calls == []
    run("draft", "ran-away")
    run("upload", "ran-away", "--yes")
    run("short", "upload", "ran-away", "--yes")
    assert "2 thumbnail(s) not set" in run("thumbnail", "ran-away", code=1)      # still refused
    assert json.loads((out / "publish.json").read_text(encoding="utf-8"))["thumbnail_ok"] is False

    before = len(service.calls)
    dry = run("thumbnail", "ran-away", "--dry-run")
    assert "thumbnails/set?videoId=VID1" in dry and "thumbnails/set?videoId=VID2" in dry
    assert len(service.calls) == before

    service.thumbnail_error = None                                               # the channel got verified
    output = run("thumbnail", "ran-away")
    assert output.count("thumbnail set") == 2
    assert [p["videoId"] for name, p in service.calls[before:] if name == "thumbnails.set"] == ["VID1", "VID2"]
    assert service.uploads == 2                                                  # no new upload
    for name in ("publish.json", "short_publish.json"):
        record = json.loads((out / name).read_text(encoding="utf-8"))
        assert record["thumbnail_ok"] is True and "thumbnail_error" not in record
    before = len(service.calls)
    assert run("thumbnail", "ran-away").count("already set") == 2
    assert len(service.calls) == before
    run("thumbnail", "ran-away", "--force")
    assert len(service.calls) == before + 2


def test_failed_upload_leaves_no_record(project: Path, monkeypatch):
    service = FakeService(script=[http_error(403, "quotaExceeded")])
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    run("draft", "ran-away")
    assert "quota" in run("upload", "ran-away", "--yes", code=1)
    assert not (project / "beats" / "ran-away" / "out" / "publish.json").exists()
    assert not (project / "published.csv").exists()


def test_upload_refuses_bad_drafts_and_missing_files(project: Path, monkeypatch):
    monkeypatch.setattr(youtube, "get_service", no_network)
    beat = project / "beats" / "ran-away"
    run("draft", "ran-away")
    run("approve", "ran-away", "--yes")
    path = beat / "out" / "draft.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("publish_at: ''", "publish_at: '2020-01-01T10:00:00+00:00'"), encoding="utf-8")
    assert "in the past" in run("upload", "ran-away", code=1)
    path.write_text(text, encoding="utf-8")
    (beat / "out" / "video.mp4").unlink()
    assert "beatpub render ran-away" in run("upload", "ran-away", code=1)


def test_public_request_prints_the_audit_reminder_and_what_youtube_did(project: Path, monkeypatch):
    service = FakeService(forced_private=True)
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    (project / "beats" / "ran-away" / "meta.yaml").write_text("publish: public\nshort: false\n", encoding="utf-8")
    run("draft", "ran-away")
    output = run("upload", "ran-away", "--yes")
    assert "audit_passed is false" in output and "asked for public, YouTube set it to private" in output
    assert service.calls[0][1]["body"]["status"]["privacyStatus"] == "public"   # we send what was asked
    assert "no Short for this beat" in run("short", "upload", "ran-away", "--yes")


def test_short_upload_no_link(project: Path, monkeypatch):
    service = FakeService()
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    run("draft", "ran-away")
    run("short", "upload", "ran-away", "--yes", "--no-link")
    description = service.calls[0][1]["body"]["snippet"]["description"]
    assert "Full beat" not in description and "{youtube_url}" not in description and description.startswith("#shorts")


def test_stats_command(project: Path, monkeypatch):
    service = FakeService(stats={"VID1": {"viewCount": "1200", "likeCount": "30", "commentCount": "4"},
                                 "VID2": {"viewCount": "90"}})
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    assert "nothing has been uploaded" in run("stats")
    run("draft", "ran-away")
    run("upload", "ran-away", "--yes")
    run("short", "upload", "ran-away", "--yes")
    log.append_published(project / "published.csv", {"uploadedAt": log.now_iso(), "kind": "video", "slug": "x",
                                                     "videoId": "DELETED", "title": "Removed one"})
    output = run("stats")
    assert "1,200" in output and "Videos" in output and "Shorts" in output and "gone" in output
    with (project / "stats.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["kind"], r["videoId"], r["views"], r["likes"], r["comments"]) for r in rows] == [
        ("video", "VID1", "1200", "30", "4"), ("video", "DELETED", "", "", ""), ("short", "VID2", "90", "0", "0")]
    assert "id=VID" in run("stats", "--dry-run") or "id=DELETED" in run("stats", "--dry-run")


def test_run_goes_through_every_step(project: Path, monkeypatch):
    from beatpub import cli
    service = FakeService()
    monkeypatch.setattr(youtube, "get_service", lambda config: service)
    rendered = []
    monkeypatch.setattr(cli, "render_beat", lambda folder, meta, artist, **kw: rendered.append(("video", kw["dry_run"])))
    monkeypatch.setattr(cli, "render_short", lambda folder, meta, artist, **kw: rendered.append(("short", kw["dry_run"])))

    dry = run("run", "ran-away", "--dry-run")
    assert rendered == [("video", True), ("short", True)] and service.calls == []
    assert dry.count("upload/youtube/v3/videos?part=snippet,status") == 2 and "VIDEO_ID" in dry
    assert not (project / "beats" / "ran-away" / "out" / "draft.yaml").exists()

    assert "no terminal" in run("run", "ran-away", code=1)                       # stops at approve, uploads nothing
    assert service.calls == []

    output = run("run", "ran-away", "--yes")
    assert "https://youtu.be/VID1" in output and "https://youtu.be/VID2" in output and "studio.youtube.com" in output
    assert output.count('beat: "Ran Away"') == 1
    assert [r["kind"] for r in log.read_published(project / "published.csv")] == ["video", "short"]

    again = run("run", "ran-away", "--yes")                                     # idempotent: nothing is sent twice
    assert again.count("already uploaded") == 2 and service.uploads == 2

    service2 = FakeService()
    monkeypatch.setattr(youtube, "get_service", lambda config: service2)
    (project / "beats" / "other").mkdir()
    (project / "beats" / "other" / "Other 140 bpm.mp3").touch()
    (project / "beats" / "other" / "out").mkdir()
    (project / "beats" / "other" / "out" / "video.mp4").write_bytes(b"0")
    (project / "beats" / "other" / "out" / "thumb.jpg").write_bytes(b"0")
    run("run", "other", "--yes", "--no-short")
    assert service2.uploads == 1
