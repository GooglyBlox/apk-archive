from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .config import (APK_EXTENSIONS, CHECKPOINT_INTERVAL, DEFAULT_BUDGET_SECONDS,
                     METADATA_CONCURRENCY)
from .ia import IAClient, IAError

BATCH = 200


def run(conn, client: IAClient | None = None, *,
        budget_seconds: int = DEFAULT_BUDGET_SECONDS,
        limit: int | None = None) -> dict:
    client = client or IAClient()
    started = time.monotonic()
    run_id = db.start_run(conn, "crawl")
    processed = errors = found = 0
    last_checkpoint = time.monotonic()

    while True:
        if time.monotonic() - started > budget_seconds:
            print("[crawl] budget exhausted")
            break
        if limit is not None and processed >= limit:
            break

        take = BATCH if limit is None else min(BATCH, limit - processed)
        batch = db.uncrawled_items(conn, take)
        if not batch:
            print("[crawl] no uncrawled items remaining")
            break

        identifiers = [r["identifier"] for r in batch]
        with ThreadPoolExecutor(METADATA_CONCURRENCY) as pool:
            results = list(pool.map(lambda i: _fetch(client, i), identifiers))

        for identifier, meta, error in results:
            processed += 1
            if error is not None:
                errors += 1
                db.mark_item_crawled(conn, identifier, error=error)
                continue
            rows = _apk_rows(identifier, meta)
            db.upsert_files(conn, rows)
            found += len(rows)
            db.mark_item_crawled(
                conn, identifier,
                apk_count=len(rows),
                files_count=len(meta.get("files", [])),
                node_server=meta.get("d1"),
                node_dir=meta.get("dir"),
            )

        conn.commit()
        elapsed = time.monotonic() - started
        print(f"[crawl] {processed} items, {found} apks, {errors} errors, "
              f"{processed/max(elapsed,1):.2f} items/s")

        if time.monotonic() - last_checkpoint > CHECKPOINT_INTERVAL:
            conn.commit()
            last_checkpoint = time.monotonic()

    stats = db.stats(conn)
    db.finish_run(conn, run_id, processed, errors, client.bytes_downloaded,
                  note=f"files_indexed={stats['files_indexed']}")
    return stats


def _fetch(client: IAClient, identifier: str):
    try:
        return identifier, client.metadata(identifier), None
    except IAError as exc:
        return identifier, None, str(exc)[:200]
    except Exception as exc:
        return identifier, None, f"{type(exc).__name__}: {exc}"[:200]


def _apk_rows(identifier: str, meta: dict) -> list[dict]:
    rows = []
    for f in meta.get("files", []):
        name = f.get("name") or ""
        lower = name.lower()
        ext = next((e for e in APK_EXTENSIONS if lower.endswith(e)), None)
        if ext is None:
            continue
        rows.append({
            "identifier": identifier,
            "filename": name,
            "ext": ext,
            "size": _int(f.get("size")),
            "md5": f.get("md5"),
            "sha1": f.get("sha1"),
            "crc32": f.get("crc32"),
            "mtime": _int(f.get("mtime")),
        })
    return rows


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
