"""Private photo archive: SQLite metadata + JPEG files on disk."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import ImageOps

from ..imageio import GpsFix, open_image

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  added_at TEXT NOT NULL,
  sha256 TEXT,
  width INTEGER, height INTEGER,
  status TEXT NOT NULL,            -- queued | analyzing | done | error
  error TEXT,
  gps TEXT,                        -- JSON GpsFix from the original file
  result TEXT,                     -- JSON LocateResult
  group_id TEXT,
  user_lat REAL, user_lon REAL, user_label TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_status ON photos(status);
CREATE INDEX IF NOT EXISTS photos_group ON photos(group_id);
"""

FULL_EDGE = 2048
THUMB_EDGE = 400


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Row:
    id: str
    filename: str
    added_at: str
    status: str
    error: str | None
    gps: dict[str, Any] | None
    result: dict[str, Any] | None
    group_id: str | None
    user_lat: float | None
    user_lon: float | None
    user_label: str | None
    note: str | None
    width: int
    height: int
    investigation: dict[str, Any] | None = None
    streetmatch: dict[str, Any] | None = None
    skyline: dict[str, Any] | None = None


class ArchiveStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "photos").mkdir(parents=True, exist_ok=True)
        (root / "thumbs").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "archive.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(photos)")}
            for col in ("investigation", "streetmatch", "skyline"):  # added after the first release
                if col not in cols:
                    self._db.execute(f"ALTER TABLE photos ADD COLUMN {col} TEXT")
            self._db.execute("UPDATE photos SET status='queued' WHERE status='analyzing'")  # resume after restart
            self._db.commit()

    # ---- files ------------------------------------------------------------------------------
    def image_path(self, pid: str, size: str) -> Path:
        return self.root / ("thumbs" if size == "thumb" else "photos") / f"{pid}.jpg"

    def add(self, filename: str, data: bytes, gps: GpsFix | None, sha256: str, max_pixels: int) -> str:
        img = ImageOps.exif_transpose(open_image(data, max_pixels)).convert("RGB")
        pid = uuid.uuid4().hex[:16]
        full = img.copy()
        full.thumbnail((FULL_EDGE, FULL_EDGE))
        full.save(self.image_path(pid, "full"), "JPEG", quality=90)
        thumb = img.copy()
        thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
        thumb.save(self.image_path(pid, "thumb"), "JPEG", quality=82)
        gps_json = json.dumps(gps.__dict__) if gps else None
        with self._lock:
            self._db.execute(
                "INSERT INTO photos (id, filename, added_at, sha256, width, height, status, gps) VALUES (?,?,?,?,?,?,?,?)",
                (pid, filename[:200], now(), sha256, full.width, full.height, "queued", gps_json),
            )
            self._db.commit()
        return pid

    def image_bytes(self, pid: str) -> bytes:
        return self.image_path(pid, "full").read_bytes()

    # ---- rows -------------------------------------------------------------------------------
    @staticmethod
    def _row(r: sqlite3.Row) -> Row:
        return Row(
            id=r["id"],
            filename=r["filename"],
            added_at=r["added_at"],
            status=r["status"],
            error=r["error"],
            gps=json.loads(r["gps"]) if r["gps"] else None,
            result=json.loads(r["result"]) if r["result"] else None,
            group_id=r["group_id"],
            user_lat=r["user_lat"],
            user_lon=r["user_lon"],
            user_label=r["user_label"],
            note=r["note"],
            width=r["width"] or 0,
            height=r["height"] or 0,
            investigation=json.loads(r["investigation"]) if r["investigation"] else None,
            streetmatch=json.loads(r["streetmatch"]) if r["streetmatch"] else None,
            skyline=json.loads(r["skyline"]) if r["skyline"] else None,
        )

    def get(self, pid: str) -> Row | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[Row]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM photos ORDER BY added_at, rowid").fetchall()
        return [self._row(r) for r in rows]

    def next_queued(self) -> Row | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM photos WHERE status='queued' ORDER BY added_at, rowid LIMIT 1").fetchone()
            if r is None:
                return None
            self._db.execute("UPDATE photos SET status='analyzing', error=NULL WHERE id=?", (r["id"],))
            self._db.commit()
        return self._row(r)

    def set_result(self, pid: str, result: dict[str, Any] | None, error: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE photos SET status=?, result=COALESCE(?, result), error=? WHERE id=?",
                ("error" if error else "done", json.dumps(result) if result else None, error, pid),
            )
            self._db.commit()

    def set_investigation(self, pid: str, investigation: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("UPDATE photos SET investigation=? WHERE id=?", (json.dumps(investigation), pid))
            self._db.commit()

    def set_streetmatch(self, pid: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("UPDATE photos SET streetmatch=? WHERE id=?", (json.dumps(result), pid))
            self._db.commit()

    def set_skyline(self, pid: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("UPDATE photos SET skyline=? WHERE id=?", (json.dumps(result), pid))
            self._db.commit()

    def requeue(self, pid: str) -> None:
        with self._lock:
            self._db.execute("UPDATE photos SET status='queued', error=NULL WHERE id=?", (pid,))
            self._db.commit()

    def update(self, pid: str, fields: dict[str, Any]) -> None:
        allowed = {"user_lat", "user_lon", "user_label", "note", "group_id"}
        keys = [k for k in fields if k in allowed]
        if not keys:
            return
        with self._lock:
            self._db.execute(
                f"UPDATE photos SET {', '.join(f'{k}=?' for k in keys)} WHERE id=?", [fields[k] for k in keys] + [pid]
            )
            self._db.commit()

    def delete(self, pid: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM photos WHERE id=?", (pid,))
            self._db.commit()
        for size in ("full", "thumb"):
            self.image_path(pid, size).unlink(missing_ok=True)

    # ---- groups -----------------------------------------------------------------------------
    def create_group(self, name: str, photo_ids: list[str]) -> str:
        gid = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute("INSERT INTO groups (id, name, created_at) VALUES (?,?,?)", (gid, name[:120], now()))
            self._db.executemany("UPDATE photos SET group_id=? WHERE id=?", [(gid, p) for p in photo_ids])
            self._db.commit()
        return gid

    def groups(self) -> dict[str, str]:
        with self._lock:
            return {r["id"]: r["name"] for r in self._db.execute("SELECT id, name FROM groups ORDER BY created_at")}

    def rename_group(self, gid: str, name: str) -> None:
        with self._lock:
            self._db.execute("UPDATE groups SET name=? WHERE id=?", (name[:120], gid))
            self._db.commit()

    def delete_group(self, gid: str) -> None:
        with self._lock:
            self._db.execute("UPDATE photos SET group_id=NULL WHERE group_id=?", (gid,))
            self._db.execute("DELETE FROM groups WHERE id=?", (gid,))
            self._db.commit()
