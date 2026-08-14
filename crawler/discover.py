from __future__ import annotations

import json
import time
from pathlib import Path

from . import db
from .config import DEFAULT_BUDGET_SECONDS
from .ia import IAClient, IAError

SOURCES_PATH = Path("data/sources.json")
PAGE_SIZE = 10000


def load_sources(path: Path = SOURCES_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    sources = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in sources if s.get("enabled", True)]


def source_key(source: dict) -> str:
    if source["type"] == "ia_query":
        return f"query:{source['query']}"
    if source["type"] == "ia_item":
        return f"item:{source['identifier']}"
    raise ValueError(f"unknown source type {source['type']!r}")


def run(conn, client: IAClient | None = None, *,
        budget_seconds: int = DEFAULT_BUDGET_SECONDS,
        sources_path: Path = SOURCES_PATH) -> dict:
    client = client or IAClient()
    started = time.monotonic()
    run_id = db.start_run(conn, "discover")
    added = errors = 0

    for source in load_sources(sources_path):
        if time.monotonic() - started > budget_seconds:
            break
        key = source_key(source)

        if source["type"] == "ia_item":
            db.upsert_items(conn, [{"identifier": source["identifier"]}])
            db.set_discover_state(conn, key, None, 1, 1, True)
            conn.commit()
            added += 1
            continue

        state = db.get_discover_state(conn, key)
        if state and state["finished"]:
            continue
        cursor = state["cursor"] if state else None
        seen = state["seen"] if state else 0
        total = state["total"] if state else 0

        while True:
            if time.monotonic() - started > budget_seconds:
                break
            try:
                items, cursor, total = client.scrape(
                    source["query"], count=PAGE_SIZE, cursor=cursor
                )
            except IAError as exc:
                errors += 1
                print(f"[discover] {key}: {exc}")
                break

            rows = [_row(i) for i in items]
            added += db.upsert_items(conn, rows)
            seen += len(rows)
            db.set_discover_state(conn, key, cursor, total, seen, cursor is None)
            conn.commit()
            print(f"[discover] {key}: {seen}/{total}")
            if not cursor:
                break

    stats = db.stats(conn)
    db.finish_run(conn, run_id, added, errors,
                  note=f"items_known={stats['items_known']}")
    return stats


def _row(item: dict) -> dict:
    collections = item.get("collection")
    if isinstance(collections, str):
        collections = [collections]
    return {
        "identifier": item["identifier"],
        "title": item.get("title"),
        "collections": json.dumps(collections) if collections else None,
        "publicdate": item.get("publicdate"),
        "item_size": item.get("item_size"),
    }
