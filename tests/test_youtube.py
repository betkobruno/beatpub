"""YouTube step 4 without the network: token handling, `learn` against a fake API, the tables."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from beatpub import drafting, youtube
from beatpub.cli import app
from beatpub.config import parse_config
from beatpub.youtube import YouTubeError


# ----------------------------------------------------------------------------- a fake API
class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeYouTube:
    """Answers channels/playlistItems/videos .list() like the real service and records every call."""

    def __init__(self, videos: list[dict], channel: bool = True, page_size: int = 50):
        self.videos, self.has_channel, self.page_size = videos, channel, page_size
        self.calls: list[tuple[str, dict]] = []

    def channels(self):
        return self._resource("channels")

    def playlistItems(self):
        return self._resource("playlistItems")

    def videos_(self):
        return self._resource("videos")

    def __getattr__(self, name):
        if name == "videos" and isinstance(self.__dict__.get("videos"), list):
            return self.videos_
        raise AttributeError(name)

    def _resource(self, kind: str):
        fake = self

        class Resource:
            def list(self, **params):
                fake.calls.append((kind, params))
                return FakeRequest(fake._answer(kind, params))
        return Resource()

    def _answer(self, kind: str, params: dict) -> dict:
        if kind == "channels":
            if not self.has_channel:
                return {"items": []}
            return {"items": [{"id": "UC123", "snippet": {"title": "CapryBeats"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]}
        if kind == "playlistItems":
            start = int(params.get("pageToken") or 0)
            size = min(self.page_size, params["maxResults"])
            page = self.videos[start:start + size]
            answer = {"items": [{"contentDetails": {"videoId": v["id"]}} for v in page]}
            if start + size < len(self.videos):
                answer["nextPageToken"] = str(start + size)
            return answer
        wanted = params["id"].split(",")
        assert len(wanted) <= 50
        return {"items": [{"id": v["id"],
                           "snippet": {"title": v["title"], "description": v.get("description", ""),
                                       "publishedAt": "2025-06-01T12:00:00Z",
                                       **({"tags": v["tags"]} if v.get("tags") else {})},
                           "statistics": {"viewCount": str(v["views"])}}
                          for v in self.videos if v["id"] in wanted and not v.get("private")]}


class FakeService(FakeYouTube):
    def __init__(self, videos, **kwargs):
        object.__setattr__(self, "_videos", videos)
        self.has_channel = kwargs.get("channel", True)
        self.page_size = kwargs.get("page_size", 50)
        self.calls = []

    @property
    def videos(self):                       # data when read as an attribute, a resource when called
        service = self

        class Both(list):
            def __call__(self):
                return service._resource("videos")
        return Both(self._videos)


def catalogue(n: int) -> list[dict]:
    return [{"id": f"vid{i:03d}", "title": f"(FREE) Song {i} Type Beat | Dro Kenji x Juice WRLD Vibes",
             "tags": ["dro kenji type beat", "Type Beat"] + (["juice wrld type beat"] if i % 2 else []),
             "views": 1000 - i} for i in range(n)]


# ----------------------------------------------------------------------------- learn
def test_learn_reads_the_channel_and_sorts_by_views():
    videos = catalogue(7)
    videos[3]["views"] = 99_999
    videos[5]["private"] = True                                        # listed in the playlist, not returned by videos.list
    service = FakeService(videos)
    data = youtube.learn(service, max_videos=50)
    assert data["channel"] == "CapryBeats" and len(data["videos"]) == 6
    assert data["videos"][0] == {"id": "vid003", "title": videos[3]["title"], "description": "",
                                 "tags": ["dro kenji type beat", "Type Beat", "juice wrld type beat"],
                                 "publishedAt": "2025-06-01T12:00:00Z", "viewCount": 99_999}
    kinds = [kind for kind, _ in service.calls]
    assert kinds == ["channels", "playlistItems", "videos"]
    assert service.calls[0][1] == {"part": "snippet,contentDetails", "mine": True}
    assert service.calls[1][1] == {"part": "contentDetails", "playlistId": "UU123", "maxResults": 50}
    assert service.calls[2][1]["part"] == "snippet,statistics"


def test_learn_paginates_and_respects_max():
    service = FakeService(catalogue(130))
    data = youtube.learn(service, max_videos=120)
    assert len(data["videos"]) == 120
    assert [kind for kind, _ in service.calls] == ["channels"] + ["playlistItems"] * 3 + ["videos"] * 3
    assert [p["maxResults"] for kind, p in service.calls if kind == "playlistItems"] == [50, 50, 20]
    small = FakeService(catalogue(130))
    assert len(youtube.learn(small, max_videos=5)["videos"]) == 5
    assert small.calls[1][1]["maxResults"] == 5


def test_learn_without_a_channel_or_with_an_api_error():
    with pytest.raises(YouTubeError, match="no YouTube channel"):
        youtube.learn(FakeService([], channel=False))
    assert youtube.learn(FakeService([]))["videos"] == []              # a channel with no uploads is fine

    class Offline:
        def channels(self):
            class R:
                def list(self, **_):
                    return FakeRequest(ConnectionResetError("boom"))
            return R()
    with pytest.raises(YouTubeError, match="could not reach YouTube"):
        youtube.learn(Offline())


def test_examples_file_feeds_the_ai_draft(tmp_path: Path):
    data = youtube.learn(FakeService(catalogue(30)), 50)
    path = youtube.save_examples(tmp_path, data)
    assert path == tmp_path / "learned" / "examples.json"
    assert json.loads(path.read_text(encoding="utf-8"))["videos"][0]["viewCount"] == 1000
    examples = drafting.load_examples(tmp_path)
    assert len(examples) == 15 and examples[0]["title"].startswith("(FREE) Song 0 ")
    assert set(examples[0]) == {"title", "description", "tags", "viewCount"}


# ----------------------------------------------------------------------------- the tables
def test_tag_frequency():
    rows = youtube.tag_frequency([
        {"tags": ["Type Beat", "type beat", "rap"], "viewCount": 100},
        {"tags": ["type beat", "sad"], "viewCount": 50},
        {"tags": [], "viewCount": 999},
    ])
    assert rows == [{"tag": "type beat", "videos": 2, "views": 150, "avg_views": 75},
                    {"tag": "rap", "videos": 1, "views": 100, "avg_views": 100},
                    {"tag": "sad", "videos": 1, "views": 50, "avg_views": 50}]


@pytest.mark.parametrize("title, shape", [
    ("(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes", "(FREE) … Type Beat | … Vibes"),
    ("(free) tuesday night type beat | polo g vibes", "(FREE) … Type Beat | … Vibes"),
    ("[FREE] Juice WRLD Type Beat 2025 - \"Lost\"", "[FREE] … Type Beat {year} - \" … \""),
    ("Dro Kenji Type Beat - Ran Away (prod. CapryBeats)", "… Type Beat - … Prod. …"),
    ("my vlog", "…"),
    ("", "…"),
])
def test_title_shape(title, shape):
    assert youtube.title_shape(title) == shape


def test_title_shapes_top_three_sorted_by_views():
    videos = ([{"title": f"(FREE) A{i} Type Beat | X Vibes", "viewCount": 10} for i in range(5)]
              + [{"title": f"[FREE] B{i} Type Beat 2025", "viewCount": 500} for i in range(3)]
              + [{"title": f"C{i} Instrumental", "viewCount": 1} for i in range(2)]
              + [{"title": "one-off vlog", "viewCount": 10_000}])
    rows = youtube.title_shapes(videos)
    assert [r["shape"] for r in rows] == ["[FREE] … Type Beat {year}", "(FREE) … Type Beat | … Vibes", "… Instrumental"]
    assert [(r["videos"], r["views"]) for r in rows] == [(3, 1500), (5, 50), (2, 2)]
    assert rows[0]["example"].startswith("[FREE] B")


# ----------------------------------------------------------------------------- login files
@pytest.fixture
def config(tmp_path: Path):
    return parse_config({"artist": "CapryBeats"}, tmp_path)


def test_client_secret_checks(config):
    with pytest.raises(YouTubeError, match="not found"):
        youtube.check_client_secret(config.client_secret_path)
    config.client_secret_path.write_text("{", encoding="utf-8")
    with pytest.raises(YouTubeError, match="not readable JSON"):
        youtube.check_client_secret(config.client_secret_path)
    config.client_secret_path.write_text(json.dumps({"web": {"client_secret": "S3CRET"}}), encoding="utf-8")
    with pytest.raises(YouTubeError, match="Desktop app") as caught:
        youtube.check_client_secret(config.client_secret_path)
    assert "S3CRET" not in str(caught.value)
    config.client_secret_path.write_text(json.dumps({"installed": {"client_id": "x"}}), encoding="utf-8")
    youtube.check_client_secret(config.client_secret_path)


def test_missing_or_damaged_token_tells_the_owner_what_to_run(config):
    with pytest.raises(YouTubeError, match="beatpub auth"):
        youtube.load_credentials(config)
    config.token_path.write_text('{"token": "abc"}', encoding="utf-8")
    with pytest.raises(YouTubeError, match="beatpub auth") as caught:
        youtube.load_credentials(config)
    assert "abc" not in str(caught.value)


def test_expired_token_is_refreshed_and_saved(config, monkeypatch):
    from google.oauth2.credentials import Credentials
    stored = {"token": "old", "refresh_token": "REFRESH", "client_id": "id", "client_secret": "sec",
              "token_uri": "https://oauth2.googleapis.com/token", "scopes": youtube.SCOPES,
              "expiry": "2020-01-01T00:00:00Z"}
    config.token_path.write_text(json.dumps(stored), encoding="utf-8")

    def fake_refresh(self, request):
        self.token, self.expiry = "new", None
    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    credentials = youtube.load_credentials(config)
    assert credentials.token == "new"
    assert json.loads(config.token_path.read_text(encoding="utf-8"))["token"] == "new"     # persisted

    from google.auth.exceptions import RefreshError

    def revoked(self, request):
        raise RefreshError("invalid_grant")
    config.token_path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(Credentials, "refresh", revoked)
    with pytest.raises(YouTubeError, match="run `beatpub auth` again"):
        youtube.load_credentials(config)


def test_revoke_deletes_the_token_even_offline(config, monkeypatch):
    assert "nothing to revoke" in youtube.revoke(config)
    config.token_path.write_text('{"refresh_token": "REFRESH"}', encoding="utf-8")
    sent = {}

    def offline(request, timeout):
        sent["url"], sent["body"] = request.full_url, request.data
        raise OSError("no network")
    monkeypatch.setattr(youtube.urllib.request, "urlopen", offline)
    message = youtube.revoke(config)
    assert not config.token_path.exists() and "deleted" in message and "REFRESH" not in message
    assert sent == {"url": youtube.REVOKE_URL, "body": b"token=REFRESH"}


def test_http_errors_are_explained():
    class Resp:
        status = 403

    class Err(Exception):
        resp = Resp()
        content = json.dumps({"error": {"message": "The request cannot be completed because you have exceeded "
                                        "your <a href=\"/youtube\">quota</a>.",
                                        "errors": [{"reason": "quotaExceeded"}]}}).encode()
    assert youtube.explain_http_error(Err()) == \
        "YouTube API error 403 (quotaExceeded): today's YouTube API quota is used up — it resets at midnight Pacific time"
    Err.content = json.dumps({"error": {"message": "Bad <b>thing</b>", "errors": [{"reason": "weird"}]}}).encode()
    assert youtube.explain_http_error(Err()) == "YouTube API error 403 (weird): Bad thing"
    Err.content = b"not json"
    assert youtube.explain_http_error(Err()) == "YouTube API error 403: no details given"


def test_real_client_accepts_our_requests():
    """Build the real API client offline and construct each request `learn` makes (nothing is sent)."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    service = build("youtube", "v3", credentials=Credentials(token="unused"), cache_discovery=False)
    requests = [
        service.channels().list(part="snippet,contentDetails", mine=True),
        service.playlistItems().list(part="contentDetails", playlistId="UU123", maxResults=50, pageToken="abc"),
        service.videos().list(part="snippet,statistics", id="a,b", maxResults=50),
    ]
    assert all(r.method == "GET" and "youtube/v3/" in r.uri for r in requests)


# ----------------------------------------------------------------------------- the commands
def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "config.yaml").write_text("artist: CapryBeats\n", encoding="utf-8")
    monkeypatch.setenv("BEATPUB_HOME", str(tmp_path))
    return tmp_path


def test_commands_without_a_login(project: Path):
    assert "client_secret.json not found" in run("auth", code=1)
    assert "beatpub auth" in run("learn", code=1)
    assert "nothing to revoke" in run("auth", "--revoke")
    output = run("learn", "--dry-run", "--max", "120")
    assert "playlistItems" in output and "up to 3 pages" in output and not (project / "templates").exists()


def test_learn_command_prints_the_tables(project: Path, monkeypatch):
    monkeypatch.setattr(youtube, "get_service", lambda config: FakeService(catalogue(12)))
    output = run("learn")
    assert "CapryBeats" in output and "12 videos read" in output
    assert "(FREE) … Type Beat | …" in output and "dro kenji type beat" in output      # the table wraps long cells
    saved = json.loads((project / "templates" / "learned" / "examples.json").read_text(encoding="utf-8"))
    assert len(saved["videos"]) == 12
    # tags on every video carry the channel's own numbers: listed in one line, not as identical table rows
    total = sum(1000 - i for i in range(12))
    assert f"2 tags are on all 12 videos" in output and f"({total:,} views" in output
    assert "juice wrld type beat" in output.split("2 tags are on all")[0]              # the only comparable tag


def test_auth_command_reports_the_channel(project: Path, monkeypatch):
    (project / "client_secret.json").write_text('{"installed": {}}', encoding="utf-8")
    assert "would open the browser" in run("auth", "--dry-run")
    logins = []
    monkeypatch.setattr(youtube, "login", lambda config: logins.append(1))
    monkeypatch.setattr(youtube, "get_service", lambda config: FakeService([]))
    assert "logged in — channel: CapryBeats" in run("auth")
    assert logins == [1]
    (project / "token.json").write_text("{}", encoding="utf-8")
    assert "already logged in — channel: CapryBeats" in run("auth")
    assert logins == [1]                                               # no second browser round
    run("auth", "--force")
    assert logins == [1, 1]
