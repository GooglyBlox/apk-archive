from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Iterable, Sequence

from .config import ICON_RETRY_BEFORE, STATE_DONE

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS items (
    identifier   TEXT PRIMARY KEY,
    title        TEXT,
    collections  TEXT,
    publicdate   TEXT,
    item_size    INTEGER,
    files_count  INTEGER,
    apk_count    INTEGER,
    node_server  TEXT,
    node_dir     TEXT,
    discovered   INTEGER NOT NULL,
    last_crawled INTEGER,
    crawl_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_uncrawled
    ON items(discovered) WHERE last_crawled IS NULL;

CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY,
    identifier     TEXT NOT NULL REFERENCES items(identifier),
    filename       TEXT NOT NULL,
    ext            TEXT NOT NULL,
    size           INTEGER,
    md5            TEXT,
    sha1           TEXT,
    crc32          TEXT,
    mtime          INTEGER,
    package        TEXT,
    app_label      TEXT,
    version_name   TEXT,
    version_code   INTEGER,
    min_sdk        INTEGER,
    target_sdk     INTEGER,
    features       TEXT,
    icon           BLOB,
    manifest_bytes INTEGER,
    enrich_state   INTEGER NOT NULL DEFAULT 0,
    enrich_error   TEXT,
    enriched_at    INTEGER,
    first_seen     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    UNIQUE(identifier, filename)
);

CREATE INDEX IF NOT EXISTS idx_files_package ON files(package);
CREATE INDEX IF NOT EXISTS idx_files_md5     ON files(md5);
CREATE INDEX IF NOT EXISTS idx_files_item    ON files(identifier);
CREATE INDEX IF NOT EXISTS idx_files_enrich
    ON files(enrich_state, size) WHERE enrich_state IN (0, 3);

CREATE TABLE IF NOT EXISTS discover_state (
    source_key TEXT PRIMARY KEY,
    cursor     TEXT,
    total      INTEGER,
    seen       INTEGER NOT NULL DEFAULT 0,
    finished   INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    phase      TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    processed  INTEGER NOT NULL DEFAULT 0,
    errors     INTEGER NOT NULL DEFAULT 0,
    bytes      INTEGER NOT NULL DEFAULT 0,
    note       TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    if conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    for column, decl in (("icon", "BLOB"),):
        if column not in have:
            conn.execute(f"ALTER TABLE files ADD COLUMN {column} {decl}")
            conn.commit()


def now() -> int:
    return int(time.time())


def scalar(value):
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if v is not None), None)
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    return str(value)


def as_int(value):
    value = scalar(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def upsert_items(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    ts = now()
    payload = [
        (scalar(r["identifier"]), scalar(r.get("title")), scalar(r.get("collections")),
         scalar(r.get("publicdate")), as_int(r.get("item_size")), ts)
        for r in rows
    ]
    if not payload:
        return 0
    conn.executemany(
        """INSERT INTO items(identifier, title, collections, publicdate,
                             item_size, discovered)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(identifier) DO UPDATE SET
             title       = COALESCE(excluded.title, items.title),
             collections = COALESCE(excluded.collections, items.collections),
             item_size   = COALESCE(excluded.item_size, items.item_size)""",
        payload,
    )
    return len(payload)


def upsert_files(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    if not rows:
        return 0
    ts = now()
    payload = []
    for r in rows:
        row = {k: scalar(v) for k, v in r.items()}
        row["size"] = as_int(r.get("size"))
        row["mtime"] = as_int(r.get("mtime"))
        row["ts"] = ts
        payload.append(row)
    conn.executemany(
        """INSERT INTO files(identifier, filename, ext, size, md5, sha1,
                             crc32, mtime, first_seen, last_seen)
           VALUES(:identifier,:filename,:ext,:size,:md5,:sha1,
                  :crc32,:mtime,:ts,:ts)
           ON CONFLICT(identifier, filename) DO UPDATE SET
             size      = excluded.size,
             md5       = COALESCE(excluded.md5, files.md5),
             sha1      = COALESCE(excluded.sha1, files.sha1),
             crc32     = COALESCE(excluded.crc32, files.crc32),
             mtime     = COALESCE(excluded.mtime, files.mtime),
             last_seen = excluded.last_seen""",
        payload,
    )
    return len(rows)


def mark_item_crawled(conn: sqlite3.Connection, identifier: str, *,
                      apk_count: int | None = None,
                      files_count: int | None = None,
                      node_server: str | None = None,
                      node_dir: str | None = None,
                      error: str | None = None) -> None:
    conn.execute(
        """UPDATE items SET last_crawled=?, apk_count=?, files_count=?,
                            node_server=COALESCE(?, node_server),
                            node_dir=COALESCE(?, node_dir),
                            crawl_error=?
           WHERE identifier=?""",
        (now(), apk_count, files_count, node_server, node_dir, error, identifier),
    )


def record_enrichment(conn: sqlite3.Connection, file_id: int, data: dict) -> None:
    if data["state"] == STATE_DONE:
        conn.execute(
            """UPDATE files SET package=?, app_label=?, version_name=?,
                                version_code=?, min_sdk=?, target_sdk=?,
                                features=?, icon=?, manifest_bytes=?,
                                enrich_state=?, enrich_error=NULL, enriched_at=?
               WHERE id=?""",
            (data.get("package"), data.get("app_label"), data.get("version_name"),
             data.get("version_code"), data.get("min_sdk"), data.get("target_sdk"),
             data.get("features"), data.get("icon"), data.get("manifest_bytes"),
             STATE_DONE, now(), file_id),
        )
        return
    conn.execute(
        """UPDATE files SET
             enrich_state = CASE WHEN enrich_state = ? THEN ? ELSE ? END,
             enrich_error = ?,
             enriched_at  = ?
           WHERE id=?""",
        (STATE_DONE, STATE_DONE, data["state"], data.get("error"), now(), file_id),
    )


def get_discover_state(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM discover_state WHERE source_key=?", (key,)
    ).fetchone()


def set_discover_state(conn: sqlite3.Connection, key: str, cursor: str | None,
                       total: int, seen: int, finished: bool) -> None:
    conn.execute(
        """INSERT INTO discover_state(source_key, cursor, total, seen,
                                      finished, updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(source_key) DO UPDATE SET
             cursor=excluded.cursor, total=excluded.total,
             seen=excluded.seen, finished=excluded.finished,
             updated_at=excluded.updated_at""",
        (key, cursor, total, seen, int(finished), now()),
    )


def uncrawled_items(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT identifier FROM items
           WHERE last_crawled IS NULL
           ORDER BY discovered LIMIT ?""",
        (limit,),
    ).fetchall()


def enrichment_queue(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.id, f.identifier, f.filename, f.size, f.ext,
                  i.node_server, i.node_dir
           FROM files f JOIN items i USING(identifier)
           WHERE (f.enrich_state IN (0, 3)
                  OR (f.enrich_state = 1 AND f.icon IS NULL
                      AND f.enriched_at < ?))
                 AND f.ext IN ('.apk', '.xapk', '.apks', '.apkm')
                 AND f.size > 0 AND i.node_server IS NOT NULL
           ORDER BY f.enrich_state, f.size
           LIMIT ?""",
        (ICON_RETRY_BEFORE, limit),
    ).fetchall()


def stats(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str):
        row = conn.execute(sql).fetchone()
        return row[0] if row else 0

    return {
        "items_known": scalar("SELECT COUNT(*) FROM items"),
        "items_crawled": scalar("SELECT COUNT(*) FROM items WHERE last_crawled IS NOT NULL"),
        "files_indexed": scalar("SELECT COUNT(*) FROM files"),
        "files_enriched": scalar("SELECT COUNT(*) FROM files WHERE enrich_state=1"),
        "files_pending": scalar("SELECT COUNT(*) FROM files WHERE enrich_state IN (0,3)"),
        "files_failed": scalar("SELECT COUNT(*) FROM files WHERE enrich_state=4"),
        "packages_known": scalar(
            "SELECT COUNT(DISTINCT package) FROM files WHERE package IS NOT NULL"),
        "updated_at": now(),
    }


def start_run(conn: sqlite3.Connection, phase: str) -> int:
    cur = conn.execute("INSERT INTO runs(phase, started_at) VALUES(?,?)", (phase, now()))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, processed: int,
               errors: int, nbytes: int = 0, note: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET ended_at=?, processed=?, errors=?, bytes=?, note=? WHERE id=?",
        (now(), processed, errors, nbytes, note, run_id),
    )
    conn.commit()
