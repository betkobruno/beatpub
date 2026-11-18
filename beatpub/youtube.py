"""YouTube Data API: OAuth login, `learn`, resumable upload, thumbnail, playlist, stats."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Config

SCOPES = ["https://www.googleapis.com/auth/youtube"]      # upload, thumbnails, playlists, list
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SETUP_DOC = "docs/YOUTUBE_API_SETUP.md"
EXAMPLES_NAME = "examples.json"
Say = Callable[[str], None]


class YouTubeError(Exception):
    """A YouTube/Google step failed; the message is safe to show (it never contains a secret)."""


# ----------------------------------------------------------------------------- OAuth
def check_client_secret(path: Path) -> None:
    """client_secret.json must exist and be an OAuth client of type 'Desktop app'. Never prints its content."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise YouTubeError(f"{path.name} not found in {path.parent} — download it from Google Cloud "
                           f"(see {SETUP_DOC}) and save it under exactly that name") from None
    except (OSError, ValueError):
        raise YouTubeError(f"{path.name} is not readable JSON — download it again from Google Cloud") from None
    if not isinstance(data, dict) or "installed" not in data:
        kind = "a 'Web application' client" if isinstance(data, dict) and "web" in data else "not an OAuth client file"
        raise YouTubeError(f"{path.name} is {kind} — create an OAuth client ID of type 'Desktop app' "
                           f"and download that one (see {SETUP_DOC})")


def save_token(path: Path, credentials: Any) -> None:
    path.write_text(credentials.to_json(), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def login(config: Config) -> Any:
    """Open the browser for Google's consent screen and store the result in token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    check_client_secret(config.client_secret_path)
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(config.client_secret_path), SCOPES)
        credentials = flow.run_local_server(
            port=0, access_type="offline", prompt="consent", timeout_seconds=600,
            authorization_prompt_message="Your browser should open now. If it does not, open this link:\n{url}",
            success_message="BeatPub is connected. You can close this tab and go back to the terminal.")
    except Exception as exc:  # oauthlib raises many unrelated classes (denied, timeout, mismatched state)
        raise YouTubeError(f"the Google login did not finish ({type(exc).__name__}) — run `beatpub auth` again "
                           "and allow access in the browser") from None
    if credentials is None or not credentials.refresh_token:
        raise YouTubeError("Google did not return a refresh token — run `beatpub auth` again and tick the "
                           "permission box on the consent screen")
    save_token(config.token_path, credentials)
    return credentials


def load_credentials(config: Config) -> Any:
    """Credentials from token.json, refreshed silently when expired. YouTubeError tells the owner what to run."""
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = config.token_path
    if not path.is_file():
        raise YouTubeError("not logged in to YouTube yet — run `beatpub auth` first")
    try:
        credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
    except (ValueError, KeyError):
        raise YouTubeError(f"{path.name} is damaged — run `beatpub auth` again") from None
    if credentials.valid:
        return credentials
    if not credentials.refresh_token:
        raise YouTubeError("the saved YouTube login cannot be renewed — run `beatpub auth` again")
    try:
        credentials.refresh(Request())
    except RefreshError:
        raise YouTubeError("the saved YouTube login has expired or was revoked — run `beatpub auth` again "
                           "(while the Google app is in 'Testing', logins expire after 7 days)") from None
    except TransportError:
        raise YouTubeError("could not reach Google to renew the login — check the internet connection") from None
    save_token(path, credentials)
    return credentials


def has_token(config: Config) -> bool:
    return config.token_path.is_file()


def revoke(config: Config) -> str:
    """Revoke the stored token at Google and delete token.json. Returns a line describing what happened."""
    path = config.token_path
    if not path.is_file():
        return "not logged in — nothing to revoke"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        token = data.get("refresh_token") or data.get("token") if isinstance(data, dict) else None
    except (OSError, ValueError):
        token = None
    revoked = False
    if token:
        request = urllib.request.Request(REVOKE_URL, data=urllib.parse.urlencode({"token": token}).encode("ascii"),
                                         headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                revoked = response.status == 200
        except (urllib.error.URLError, OSError):
            revoked = False
    path.unlink()
    if revoked:
        return f"access revoked at Google and {path.name} deleted"
    return (f"{path.name} deleted, but Google could not be told (offline, or the login was already invalid) — "
            "you can remove BeatPub at https://myaccount.google.com/permissions")


def get_service(config: Config) -> Any:
    """An authorised YouTube Data API v3 client."""
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=load_credentials(config), cache_discovery=False)


# ----------------------------------------------------------------------------- errors
def explain_http_error(exc: Any) -> str:
    """One owner-readable line for a googleapiclient HttpError."""
    status = getattr(getattr(exc, "resp", None), "status", "?")
    reason, message = "", ""
    try:
        error = json.loads(exc.content.decode("utf-8"))["error"]
        message = str(error.get("message") or "")
        reason = str((error.get("errors") or [{}])[0].get("reason") or "")
    except (AttributeError, ValueError, KeyError, IndexError, TypeError):
        pass
    hints = {
        "quotaExceeded": "today's YouTube API quota is used up — it resets at midnight Pacific time",
        "accessNotConfigured": f"the YouTube Data API v3 is not enabled for this Google Cloud project (see {SETUP_DOC})",
        "youtubeSignupRequired": "this Google account has no YouTube channel — run `beatpub auth` again and pick the channel's account",
        "insufficientPermissions": "the saved login lacks the YouTube permission — run `beatpub auth` again and allow it",
        "authError": "the saved YouTube login was rejected — run `beatpub auth` again",
        "forbidden": "YouTube refused this request for the logged-in account",
    }
    hint = hints.get(reason) or re.sub(r"<[^>]+>", "", message) or "no details given"
    return f"YouTube API error {status}{' (' + reason + ')' if reason else ''}: {hint}"


def execute(request: Any) -> dict:
    """Run one API request; turn Google's exceptions into YouTubeError."""
    from google.auth.exceptions import RefreshError, TransportError
    from googleapiclient.errors import HttpError

    try:
        return request.execute()
    except HttpError as exc:
        raise YouTubeError(explain_http_error(exc)) from None
    except RefreshError:
        raise YouTubeError("the saved YouTube login has expired or was revoked — run `beatpub auth` again") from None
    except (TransportError, OSError) as exc:
        raise YouTubeError(f"could not reach YouTube ({type(exc).__name__}) — check the internet connection") from None


# ----------------------------------------------------------------------------- channel
def my_channel(service: Any) -> dict[str, str]:
    """{'id', 'title', 'uploads'} of the logged-in channel."""
    response = execute(service.channels().list(part="snippet,contentDetails", mine=True))
    items = response.get("items") or []
    if not items:
        raise YouTubeError("this Google account has no YouTube channel — run `beatpub auth` again and pick "
                           "the account (or brand account) that owns the channel")
    channel = items[0]
    return {"id": channel.get("id", ""), "title": channel.get("snippet", {}).get("title", ""),
            "uploads": channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")}


# ----------------------------------------------------------------------------- learn
def learn_plan(max_videos: int) -> list[str]:
    """The API calls `learn` makes, for --dry-run."""
    pages = max(1, -(-max_videos // 50))
    return [
        "GET youtube/v3/channels       part=snippet,contentDetails  mine=true",
        f"GET youtube/v3/playlistItems  part=contentDetails  playlistId=<your uploads playlist>  maxResults=50"
        f"   (up to {pages} page{'s' if pages > 1 else ''}, newest {max_videos} videos)",
        f"GET youtube/v3/videos         part=snippet,statistics  id=<batches of up to 50 ids>"
        f"   (up to {pages} call{'s' if pages > 1 else ''})",
    ]


def fetch_video_ids(service: Any, uploads_playlist: str, max_videos: int) -> list[str]:
    """The channel's newest uploads, newest first, up to max_videos."""
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < max_videos:
        params: dict[str, Any] = {"part": "contentDetails", "playlistId": uploads_playlist,
                                  "maxResults": min(50, max_videos - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        response = execute(service.playlistItems().list(**params))
        ids += [item["contentDetails"]["videoId"] for item in response.get("items") or []
                if item.get("contentDetails", {}).get("videoId")]
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return list(dict.fromkeys(ids))[:max_videos]


def fetch_videos(service: Any, ids: list[str]) -> list[dict[str, Any]]:
    """title, description, tags, publishedAt, viewCount for each id (private/deleted ones are simply absent)."""
    videos: list[dict[str, Any]] = []
    for start in range(0, len(ids), 50):
        response = execute(service.videos().list(part="snippet,statistics", id=",".join(ids[start:start + 50]),
                                                 maxResults=50))
        for item in response.get("items") or []:
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            videos.append({
                "id": item.get("id", ""),
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "tags": list(snippet.get("tags") or []),
                "publishedAt": snippet.get("publishedAt", ""),
                "viewCount": int(stats.get("viewCount") or 0),
            })
    return videos


def learn(service: Any, max_videos: int = 50) -> dict[str, Any]:
    """Pull the channel's own newest videos → {'channel', 'fetchedAt', 'videos'} (videos sorted by views)."""
    channel = my_channel(service)
    if not channel["uploads"]:
        raise YouTubeError("YouTube did not return an uploads playlist for this channel")
    videos = fetch_videos(service, fetch_video_ids(service, channel["uploads"], max_videos))
    videos.sort(key=lambda v: v["viewCount"], reverse=True)
    return {"channel": channel["title"], "channelId": channel["id"],
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "videos": videos}


def save_examples(templates: Path, data: dict[str, Any]) -> Path:
    path = templates / "learned" / EXAMPLES_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def tag_frequency(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per tag: how many videos use it and their views, sorted by total views (then by count)."""
    count: Counter[str] = Counter()
    views: Counter[str] = Counter()
    for video in videos:
        for tag in {str(t).strip().lower() for t in video.get("tags") or [] if str(t).strip()}:
            count[tag] += 1
            views[tag] += int(video.get("viewCount") or 0)
    rows = [{"tag": tag, "videos": count[tag], "views": views[tag], "avg_views": round(views[tag] / count[tag])}
            for tag in count]
    rows.sort(key=lambda r: (-r["views"], -r["videos"], r["tag"]))
    return rows


# The fixed words of a beat title; everything between them (beat name, artist names) becomes "…".
_SHAPE_WORDS = re.compile(
    r"\(\s*free(?: for profit)?\s*\)|\[\s*free(?: for profit)?\s*\]|\bfree for profit\b|\bfree\b|\btype beats?\b"
    r"|\bvibes?\b|\binstrumental\b|\bbeats?\b|\bprod\.?(?: by)?|\b(?:19|20)\d{2}\b|[|\-–—~•:\"“”]+", re.I)


def title_shape(title: str) -> str:
    """'(FREE) Ran Away Type Beat | Dro Kenji x Juice WRLD Vibes' → '(FREE) … Type Beat | … Vibes'."""
    parts: list[str] = []
    position = 0
    for match in _SHAPE_WORDS.finditer(title):
        if title[position:match.start()].strip():
            parts.append("…")
        word = match.group(0)
        if re.fullmatch(r"(?:19|20)\d{2}", word):
            word = "{year}"
        elif re.search(r"[A-Za-z]", word):
            word = " ".join(w.upper() if "free" in w.lower() else w.capitalize() for w in word.split())
        parts.append(word)
        position = match.end()
    if title[position:].strip():
        parts.append("…")
    return re.sub(r"\s+", " ", " ".join(parts)).strip() or "…"


def title_shapes(videos: list[dict[str, Any]], top: int = 3) -> list[dict[str, Any]]:
    """The `top` most common title shapes, sorted by their total views, each with its best-viewed example."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for video in videos:
        groups.setdefault(title_shape(str(video.get("title") or "")), []).append(video)
    common = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]
    rows = []
    for shape, group in common:
        best = max(group, key=lambda v: int(v.get("viewCount") or 0))
        rows.append({"shape": shape, "videos": len(group), "views": sum(int(v.get("viewCount") or 0) for v in group),
                     "example": best.get("title", "")})
    rows.sort(key=lambda r: -r["views"])
    return rows


# ----------------------------------------------------------------------------- upload
CHUNK_BYTES = 8 * 1024 * 1024
MAX_RETRIES = 10
RETRY_STATUS = (500, 502, 503, 504)
Progress = Callable[[int, int], None]          # (bytes sent, total bytes)


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def build_body(draft: Any, default_language: str = "en") -> dict[str, Any]:
    """The videos.insert request body for a draft (publishAt only together with private)."""
    status: dict[str, Any] = {"privacyStatus": draft.privacy}
    if draft.publish_at and draft.privacy == "private":
        status["publishAt"] = draft.publish_at
    status.update({"selfDeclaredMadeForKids": False, "license": "youtube", "embeddable": True,
                   "publicStatsViewable": True})
    return {
        "snippet": {"title": draft.title, "description": draft.description, "tags": list(draft.tags),
                    "categoryId": draft.category_id, "defaultLanguage": default_language},
        "status": status,
    }


def upload_video(service: Any, path: Path, body: dict[str, Any], *, notify_subscribers: bool = True,
                 progress: Progress | None = None, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """
    videos.insert as a resumable upload in 8 MiB chunks. A dropped connection or a 5xx answer
    is retried from where the upload stopped, with exponential backoff, up to 10 times in a row.
    Returns the API's video resource.
    """
    import random
    import time

    import httplib2
    from google.auth.exceptions import RefreshError, TransportError
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    sleep = sleep or time.sleep
    total = path.stat().st_size
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=CHUNK_BYTES, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media,
                                      notifySubscribers=notify_subscribers)
    response, failures = None, 0
    while response is None:
        problem = ""
        try:
            status, response = request.next_chunk()
            failures = 0
            if progress:
                progress(total if response is not None else int(status.resumable_progress), total)
        except HttpError as exc:
            if getattr(exc.resp, "status", 0) not in RETRY_STATUS:
                raise YouTubeError(explain_http_error(exc)) from None
            problem = f"YouTube answered {exc.resp.status}"
        except RefreshError:
            raise YouTubeError("the saved YouTube login has expired or was revoked — run `beatpub auth` again") from None
        except (httplib2.HttpLib2Error, TransportError, OSError) as exc:      # sockets, timeouts, TLS, DNS
            problem = f"connection problem ({type(exc).__name__})"
        if problem:
            failures += 1
            if failures > MAX_RETRIES:
                raise YouTubeError(f"the upload kept failing ({problem}) after {MAX_RETRIES} retries — nothing was "
                                   "published; run the command again later")
            sleep(min(2 ** failures, 64) + random.random())
    if not isinstance(response, dict) or not response.get("id"):
        raise YouTubeError("YouTube accepted the upload but returned no video id — check YouTube Studio before retrying")
    return response


PROCESSING_POLL_SECONDS = 10
PROCESSING_TIMEOUT_SECONDS = 600
THUMBNAIL_RETRIES = 2                 # extra attempts after the first refusal
THUMBNAIL_RETRY_PAUSE_SECONDS = 20
THUMBNAIL_403_HINT = ("YouTube often answers this while it is still processing the video — try again in a few "
                      "minutes. If it keeps happening, check that the channel is phone-verified "
                      "(https://www.youtube.com/verify)")


def pause(seconds: float) -> None:
    import time

    time.sleep(seconds)


def processing_status(service: Any, video_id: str) -> str:
    """'processing' | 'succeeded' | 'failed' | 'terminated', or '' when YouTube does not say."""
    response = execute(service.videos().list(part="processingDetails", id=video_id))
    items = response.get("items") or []
    return str(items[0].get("processingDetails", {}).get("processingStatus", "")) if items else ""


def wait_until_processed(service: Any, video_id: str, *, interval: float = PROCESSING_POLL_SECONDS,
                         timeout: float = PROCESSING_TIMEOUT_SECONDS) -> str:
    """
    Ask every `interval` seconds until YouTube has finished processing the video, for at most
    `timeout` seconds. Returns the last status seen ('succeeded' when it is ready; 'processing'
    after a timeout; 'failed'/'terminated' as YouTube reports them; '' if it could not be asked).
    Never raises: the caller goes on with the thumbnail either way.
    """
    waited = 0.0
    while True:
        try:
            status = processing_status(service, video_id)
        except YouTubeError:
            return ""
        if status != "processing" or waited >= timeout:
            return status
        pause(interval)
        waited += interval


def set_thumbnail(service: Any, video_id: str, image: Path, *, retries: int = 0,
                  retry_pause: float = THUMBNAIL_RETRY_PAUSE_SECONDS, say: Say | None = None) -> None:
    """thumbnails.set; a refusal is tried again `retries` times with a pause before giving up."""
    from googleapiclient.http import MediaFileUpload

    attempt = 0
    while True:
        try:
            execute(service.thumbnails().set(videoId=video_id,
                                             media_body=MediaFileUpload(str(image), mimetype="image/jpeg")))
            return
        except YouTubeError as exc:
            if attempt >= retries:
                if "error 403" in str(exc):
                    raise YouTubeError(f"{exc}. {THUMBNAIL_403_HINT}") from None
                raise
            attempt += 1
            if say:
                say(f"thumbnail refused ({exc}) — trying again in {retry_pause:g} s ({attempt}/{retries})")
            pause(retry_pause)


def find_playlist(service: Any, name: str) -> str | None:
    """The id of the channel's playlist with this exact name (else the same name ignoring case/spaces)."""
    wanted = name.strip()
    loose: str | None = None
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"part": "snippet", "mine": True, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        response = execute(service.playlists().list(**params))
        for item in response.get("items") or []:
            title = str(item.get("snippet", {}).get("title", ""))
            if title == wanted:
                return item.get("id")
            if loose is None and title.strip().casefold() == wanted.casefold():
                loose = item.get("id")
        page_token = response.get("nextPageToken")
        if not page_token:
            return loose


def add_to_playlist(service: Any, playlist_id: str, video_id: str) -> None:
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    execute(service.playlistItems().insert(part="snippet", body=body))


# ----------------------------------------------------------------------------- stats
def fetch_stats(service: Any, ids: list[str]) -> dict[str, dict[str, int]]:
    """{videoId: {'views', 'likes', 'comments'}}; ids YouTube no longer knows are simply absent."""
    stats: dict[str, dict[str, int]] = {}
    for start in range(0, len(ids), 50):
        response = execute(service.videos().list(part="statistics", id=",".join(ids[start:start + 50]), maxResults=50))
        for item in response.get("items") or []:
            numbers = item.get("statistics", {})
            stats[item.get("id", "")] = {"views": int(numbers.get("viewCount") or 0),
                                         "likes": int(numbers.get("likeCount") or 0),
                                         "comments": int(numbers.get("commentCount") or 0)}
    return stats
