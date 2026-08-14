from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler import db  # noqa: E402
from crawler.discover import _row  # noqa: E402

# Shapes observed live in the Internet Archive scrape API. Multi-valued title
# broke a real crawl run after 20k items.
SCRAPED = [
    {"identifier": "FreeVideov2.9.1",
     "title": ["Free Video(v 2.9.1)", "Free_Video"],
     "collection": ["apkarchive", "phonesoftware"],
     "publicdate": "2019-01-01T00:00:00Z", "item_size": 123},
    {"identifier": "IAntiTheft2.1.4",
     "title": ["I Anti Theft 2.1.4", "I Anti Theft"],
     "collection": "phonesoftware",
     "publicdate": ["2019-01-01T00:00:00Z", "2020-01-01T00:00:00Z"],
     "item_size": "456"},
    {"identifier": "plain", "title": "Ordinary Title",
     "collection": ["phonesoftware"], "publicdate": None, "item_size": None},
    {"identifier": "sparse"},
]

FILES = [
    {"identifier": "FreeVideov2.9.1", "filename": "a.apk", "ext": ".apk",
     "size": ["4096"], "md5": ["abc"], "sha1": None, "crc32": None, "mtime": "17"},
]


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(Path(tmp) / "t.sqlite")

        try:
            db.upsert_items(conn, [_row(s) for s in SCRAPED])
            conn.commit()
        except Exception as exc:
            failures.append(f"upsert_items raised {type(exc).__name__}: {exc}")

        try:
            db.upsert_files(conn, FILES)
            conn.commit()
        except Exception as exc:
            failures.append(f"upsert_files raised {type(exc).__name__}: {exc}")

        # raw dicts straight past _row must also survive the db boundary
        try:
            db.upsert_items(conn, [{"identifier": "raw", "title": ["x", "y"],
                                    "item_size": ["9"]}])
            conn.commit()
        except Exception as exc:
            failures.append(f"db-layer coercion failed: {type(exc).__name__}: {exc}")

        rows = {r["identifier"]: r for r in conn.execute("SELECT * FROM items")}
        if rows.get("FreeVideov2.9.1", {})["title"] != "Free Video(v 2.9.1)":
            failures.append(f"title not flattened: {rows['FreeVideov2.9.1']['title']!r}")
        if rows.get("IAntiTheft2.1.4", {})["item_size"] != 456:
            failures.append(f"item_size not coerced: {rows['IAntiTheft2.1.4']['item_size']!r}")
        if rows.get("plain", {})["title"] != "Ordinary Title":
            failures.append("scalar title was mangled")

        f = conn.execute("SELECT size, md5, mtime FROM files").fetchone()
        if f["size"] != 4096 or f["md5"] != "abc" or f["mtime"] != 17:
            failures.append(f"file fields not coerced: {tuple(f)}")

        conn.close()

    if failures:
        for x in failures:
            print("FAIL:", x)
        return 1
    print(f"{len(SCRAPED)} item shapes + {len(FILES)} file shapes coerced cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
