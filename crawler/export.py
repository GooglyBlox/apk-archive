from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from .config import API_LEVELS, STATE_DONE

PAGE_SIZE = 4096

WEB_SCHEMA = """
CREATE TABLE titles (
    tid        INTEGER PRIMARY KEY,
    label      TEXT NOT NULL,
    vendor     TEXT,
    editions   INTEGER NOT NULL,
    versions   INTEGER NOT NULL,
    copies     INTEGER NOT NULL,
    min_sdk    INTEGER,
    max_sdk    INTEGER,
    dev        TEXT,
    identified INTEGER NOT NULL
);

CREATE TABLE groups (
    gid       INTEGER PRIMARY KEY,
    tid       INTEGER NOT NULL,
    pkg       TEXT,
    pkg_lc    TEXT,
    label     TEXT NOT NULL,
    dev       TEXT,
    min_sdk   INTEGER,
    max_sdk   INTEGER,
    versions  INTEGER NOT NULL,
    copies    INTEGER NOT NULL,
    newest    TEXT,
    identified INTEGER NOT NULL
);

CREATE TABLE apps (
    id         INTEGER PRIMARY KEY,
    gid        INTEGER NOT NULL,
    ver        TEXT,
    vsort      TEXT,
    vcode      INTEGER,
    min_sdk    INTEGER,
    target_sdk INTEGER,
    dev        TEXT,
    size       INTEGER,
    md5        TEXT,
    item       TEXT NOT NULL,
    fn         TEXT NOT NULL,
    ext        TEXT,
    mtime      INTEGER,
    label      TEXT
);

CREATE TABLE tokens (
    tok TEXT NOT NULL,
    tid INTEGER NOT NULL
);

CREATE TABLE icons (
    tid  INTEGER PRIMARY KEY,
    webp BLOB NOT NULL
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

WEB_INDEXES = """
CREATE INDEX idx_tokens      ON tokens(tok, tid);
CREATE INDEX idx_apps_gid    ON apps(gid, vcode DESC, vsort DESC);
CREATE INDEX idx_apps_md5    ON apps(md5);
CREATE INDEX idx_groups_pkg  ON groups(pkg_lc);
CREATE INDEX idx_groups_tid  ON groups(tid);
CREATE INDEX idx_titles_sort ON titles(identified DESC, label COLLATE NOCASE, tid);
CREATE INDEX idx_titles_sdk  ON titles(min_sdk);
"""

_VENDOR_SKIP = {"com", "org", "net", "io", "co", "de", "fr", "uk", "ru", "us",
                "eu", "me", "tv", "app", "apps", "android", "mobi", "www", "air"}
_EDITION_WORDS = re.compile(
    r"\b(hd|sd|free|paid|full|lite|demo|trial|premium|pro|plus|deluxe|"
    r"eng|usa?|eu|intl|international|mod|apk|game|the)\b")


def vendor_of(pkg: str | None) -> str:
    if not pkg:
        return ""
    for part in pkg.lower().split("."):
        if part and part not in _VENDOR_SKIP:
            return part
    return ""


_VER_SPLIT = re.compile(r"(\d+)")


def version_key(name: str | None) -> str:
    if name is None:
        return ""
    text = str(name).strip().lower()
    if len(text) > 1 and text[0] == "v" and text[1].isdigit():
        text = text[1:]
    out = []
    for part in _VER_SPLIT.split(text):
        if not part:
            continue
        if part.isdigit():
            digits = part.lstrip("0") or "0"
            out.append(f"{min(len(digits), 99):02d}{digits}")
        else:
            out.append(part)
    return "".join(out)


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    text = _SPLIT_RE.sub(" ", label.lower())
    text = _EDITION_WORDS.sub(" ", text)
    return " ".join(text.split())

_SPLIT_RE = re.compile(r"[^0-9A-Za-z]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = {"apk", "com", "org", "net", "android", "app", "apps", "www", "free",
         "the", "and", "mod", "v", "old", "new", "final", "premium", "full"}


def tokenize(*values: str | None) -> set[str]:
    out: set[str] = set()
    for value in values:
        if not value:
            continue
        for chunk in _SPLIT_RE.split(value):
            if not chunk:
                continue
            for part in _CAMEL_RE.split(chunk):
                part = part.lower()
                if len(part) < 2 or part in _STOP:
                    continue
                out.add(part)
    return out


def run(conn: sqlite3.Connection, out_path: str, *, min_enriched: int = 0) -> dict:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    for suffix in ("-wal", "-shm"):
        stale = Path(str(out) + suffix)
        if stale.exists():
            stale.unlink()

    enriched = conn.execute(
        "SELECT COUNT(*) FROM files WHERE enrich_state=?", (STATE_DONE,)
    ).fetchone()[0]
    if enriched < min_enriched:
        raise SystemExit(
            f"refusing to export: {enriched} enriched rows < required {min_enriched}"
        )

    web = sqlite3.connect(str(out))
    web.execute(f"PRAGMA page_size={PAGE_SIZE}")
    web.execute("PRAGMA journal_mode=DELETE")
    web.executescript(WEB_SCHEMA)

    rows = conn.execute(
        """SELECT f.id, f.identifier, f.filename, f.ext, f.size, f.md5, f.mtime,
                  f.package, f.app_label, f.version_name, f.version_code,
                  f.min_sdk, f.target_sdk, f.features, f.enrich_state, f.icon
           FROM files f
           ORDER BY COALESCE(f.package, f.filename), f.version_code"""
    )

    groups: dict[str, dict] = {}
    apps: list[tuple] = []
    next_gid = 1

    for r in rows:
        key, identified = _group_key(r)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "gid": next_gid,
                "pkg": r["package"],
                "label": _label(r),
                "dev": _devices(r["features"]),
                "min_sdk": r["min_sdk"],
                "max_sdk": r["min_sdk"],
                "versions": set(),
                "copies": 0,
                "newest": r["version_name"],
                "newest_rank": (-1, ""),
                "identified": identified,
                "icon": None,
                "icon_code": -1,
            }
            next_gid += 1

        if r["icon"] and (r["version_code"] or 0) >= group["icon_code"]:
            group["icon"] = r["icon"]
            group["icon_code"] = r["version_code"] or 0

        group["copies"] += 1
        if r["version_name"]:
            group["versions"].add(r["version_name"])
        if r["min_sdk"] is not None:
            group["min_sdk"] = (r["min_sdk"] if group["min_sdk"] is None
                                else min(group["min_sdk"], r["min_sdk"]))
            group["max_sdk"] = (r["min_sdk"] if group["max_sdk"] is None
                                else max(group["max_sdk"], r["min_sdk"]))
        rank = (r["version_code"] if r["version_code"] is not None else -1,
                version_key(r["version_name"]))
        if rank >= group["newest_rank"]:
            group["newest_rank"] = rank
            if r["version_name"]:
                group["newest"] = r["version_name"]
        if not group["label"] or group["label"].startswith("("):
            group["label"] = _label(r)

        apps.append((
            r["id"], group["gid"], r["version_name"], version_key(r["version_name"]),
            r["version_code"], r["min_sdk"], r["target_sdk"], _devices(r["features"]),
            r["size"], r["md5"], r["identifier"], r["filename"], r["ext"],
            r["mtime"], r["app_label"],
        ))

    titles: dict[tuple[str, str], dict] = {}
    next_tid = 1
    for key, g in groups.items():
        vendor = vendor_of(g["pkg"])
        norm = normalize_label(g["label"])
        tkey = (vendor, norm) if (g["identified"] and vendor and norm) else ("", key)
        title = titles.get(tkey)
        if title is None:
            title = titles[tkey] = {
                "tid": next_tid, "label": g["label"], "vendor": vendor or None,
                "editions": 0, "versions": 0, "copies": 0,
                "min_sdk": None, "max_sdk": None, "devs": set(),
                "identified": g["identified"], "icon": None, "icon_code": -1,
            }
            next_tid += 1
        g["tid"] = title["tid"]
        title["editions"] += 1
        title["versions"] += max(len(g["versions"]), 1)
        title["copies"] += g["copies"]
        title["devs"].update((g["dev"] or "phone").split(","))
        if g["min_sdk"] is not None:
            title["min_sdk"] = (g["min_sdk"] if title["min_sdk"] is None
                                else min(title["min_sdk"], g["min_sdk"]))
            title["max_sdk"] = (g["max_sdk"] if title["max_sdk"] is None
                                else max(title["max_sdk"], g["max_sdk"] or g["min_sdk"]))
        if g["icon"] and g["icon_code"] >= title["icon_code"]:
            title["icon"] = g["icon"]
            title["icon_code"] = g["icon_code"]
        if len(g["label"]) < len(title["label"]):
            title["label"] = g["label"]

    web.executemany(
        "INSERT INTO apps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", apps
    )
    web.executemany(
        """INSERT INTO groups(gid, tid, pkg, pkg_lc, label, dev, min_sdk, max_sdk,
                              versions, copies, newest, identified)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(g["gid"], g["tid"], g["pkg"], (g["pkg"] or "").lower() or None, g["label"],
          g["dev"], g["min_sdk"], g["max_sdk"], max(len(g["versions"]), 1),
          g["copies"], g["newest"], g["identified"])
         for g in groups.values()],
    )
    web.executemany(
        """INSERT INTO titles(tid, label, vendor, editions, versions, copies,
                              min_sdk, max_sdk, dev, identified)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [(t["tid"], t["label"], t["vendor"], t["editions"], t["versions"],
          t["copies"], t["min_sdk"], t["max_sdk"],
          ",".join(sorted(x for x in t["devs"] if x)), t["identified"])
         for t in titles.values()],
    )

    icon_rows = [(t["tid"], t["icon"]) for t in titles.values() if t["icon"]]
    web.executemany("INSERT INTO icons VALUES(?,?)", icon_rows)

    token_pairs = set()
    for key, g in groups.items():
        for tok in tokenize(g["pkg"], g["label"], None if g["pkg"] else key):
            token_pairs.add((tok, g["tid"]))
    token_rows = sorted(token_pairs)
    web.executemany("INSERT INTO tokens VALUES(?,?)", token_rows)

    web.executescript(WEB_INDEXES)
    web.executemany(
        "INSERT INTO meta VALUES(?,?)",
        [
            ("built_at", str(int(time.time()))),
            ("groups", str(len(groups))),
            ("apps", str(len(apps))),
            ("tokens", str(len(token_rows))),
            ("max_gid", str(next_gid - 1)),
            ("max_tid", str(next_tid - 1)),
            ("titles", str(len(titles))),
            ("icons", str(len(icon_rows))),
            ("api_levels", json.dumps(API_LEVELS)),
        ],
    )
    web.commit()
    web.execute("VACUUM")
    web.close()

    return {
        "out": str(out),
        "titles": len(titles),
        "groups": len(groups),
        "apps": len(apps),
        "tokens": len(token_rows),
        "icons": len(icon_rows),
        "bytes": out.stat().st_size,
    }


def _group_key(r) -> tuple[str, int]:
    if r["package"]:
        return r["package"], 1
    stem = (r["filename"] or "").rsplit("/", 1)[-1].lower()
    return f"file:{stem}", 0


def _label(r) -> str:
    for candidate in (r["app_label"], r["package"]):
        if candidate:
            return candidate
    base = (r["filename"] or "unknown").rsplit("/", 1)[-1]
    return base or "unknown"


def _devices(features: str | None) -> str:
    if not features:
        return "phone"
    try:
        names = json.loads(features)
    except (TypeError, ValueError):
        return "phone"
    from .config import DEVICE_FEATURES
    hits = sorted({DEVICE_FEATURES[f] for f in names if f in DEVICE_FEATURES})
    return ",".join(hits) if hits else "phone"
