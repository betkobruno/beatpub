"""published.csv (append-only log of what went out), stats.csv, and the per-beat publish.json files."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PUBLISHED_NAME, STATS_NAME = "published.csv", "stats.csv"
PUBLISH_JSON, SHORT_PUBLISH_JSON = "publish.json", "short_publish.json"
FIELDS = ("uploadedAt", "kind", "slug", "videoId", "url", "title", "privacy", "publishAt",
          "hook_text", "short_layout", "linked_video")
STATS_FIELDS = ("kind", "slug", "videoId", "title", "uploadedAt", "views", "likes", "comments",
                "hook_text", "short_layout", "url", "fetchedAt")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- published.csv
def append_published(path: Path, row: dict[str, Any]) -> None:
    """Add one row; the header is written when the file is new. Existing rows are never touched."""
    new = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in FIELDS})


def read_published(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh) if row.get("videoId")]


def recent_rows(rows: list[dict[str, str]], days: int, now: datetime | None = None) -> list[dict[str, str]]:
    """Rows uploaded in the last `days` days (0 = all), one per videoId, newest upload first."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days) if days > 0 else None
    seen: set[str] = set()
    kept = []
    for row in sorted(rows, key=lambda r: r.get("uploadedAt", ""), reverse=True):
        if row["videoId"] in seen:
            continue
        if cutoff is not None:
            try:
                uploaded = datetime.fromisoformat(row.get("uploadedAt", ""))
            except ValueError:
                continue
            if uploaded.tzinfo is None:
                uploaded = uploaded.replace(tzinfo=timezone.utc)
            if uploaded < cutoff:
                continue
        seen.add(row["videoId"])
        kept.append(row)
    return kept


# ----------------------------------------------------------------------------- stats.csv
def stats_rows(rows: list[dict[str, str]], stats: dict[str, dict[str, int]], fetched_at: str) -> list[dict[str, Any]]:
    """One line per published video with its numbers, sorted by kind (videos first), then views."""
    lines = []
    for row in rows:
        numbers = stats.get(row["videoId"])
        lines.append({**{field: row.get(field, "") for field in STATS_FIELDS},
                      "views": numbers["views"] if numbers else "", "likes": numbers["likes"] if numbers else "",
                      "comments": numbers["comments"] if numbers else "", "fetchedAt": fetched_at,
                      "found": numbers is not None})
    lines.sort(key=lambda l: (l["kind"] != "video", l["kind"], -(l["views"] or 0), l["title"]))
    return lines


def write_stats(path: Path, lines: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(lines)


# ----------------------------------------------------------------------------- publish.json
def read_publish(path: Path) -> dict[str, Any] | None:
    """The record of an upload, or None if there is none (or it is unreadable / has no video id)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("videoId") else None


def write_publish(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
