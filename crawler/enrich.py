from __future__ import annotations

import queue
import threading
import time

from . import db
from .config import (DEFAULT_BUDGET_SECONDS, FILE_TIMEOUT, NODE_CONCURRENCY,
                     STATE_DONE, STATE_PERMANENT, STATE_RETRYABLE)
from .ia import IAClient, IAError
from .manifest import ManifestError, extract
from .remotezip import RemoteZip, RemoteZipError

BATCH = 500


def run(conn, client: IAClient | None = None, *,
        budget_seconds: int = DEFAULT_BUDGET_SECONDS,
        limit: int | None = None) -> dict:
    client = client or IAClient()
    started = time.monotonic()
    run_id = db.start_run(conn, "enrich")
    processed = errors = 0

    while True:
        if time.monotonic() - started > budget_seconds:
            print("[enrich] budget exhausted")
            break
        if limit is not None and processed >= limit:
            break

        take = BATCH if limit is None else min(BATCH, limit - processed)
        batch = db.enrichment_queue(conn, take)
        if not batch:
            print("[enrich] queue empty")
            break

        results, hung = _process(client, batch)
        for row in hung:
            print(f"[enrich] {row['identifier']}/{row['filename']} "
                  f"hung for over {FILE_TIMEOUT}s; marking it failed")
            results.append((row["id"], {
                "state": STATE_PERMANENT,
                "error": f"hung for over {FILE_TIMEOUT}s",
            }))

        for file_id, data in results:
            db.record_enrichment(conn, file_id, data)
            processed += 1
            if data["state"] != STATE_DONE:
                errors += 1
        conn.commit()

        elapsed = time.monotonic() - started
        mb = client.bytes_downloaded / 1e6
        print(f"[enrich] {processed} done, {errors} failed, "
              f"{processed/max(elapsed,1):.2f} apk/s, {mb:.0f} MB")

        if hung:
            print("[enrich] stopping early")
            break

    stats = db.stats(conn)
    db.finish_run(conn, run_id, processed, errors, client.bytes_downloaded,
                  note=f"files_enriched={stats['files_enriched']}")
    return stats


def _process(client: IAClient, batch) -> tuple[list, list]:
    todo: queue.SimpleQueue = queue.SimpleQueue()
    for row in batch:
        todo.put(row)
    done: queue.SimpleQueue = queue.SimpleQueue()
    active: dict[int, tuple] = {}

    def worker():
        while True:
            try:
                row = todo.get_nowait()
            except queue.Empty:
                return
            active[row["id"]] = (row, time.monotonic())
            result = _one(client, row)
            del active[row["id"]]
            done.put(result)

    for _ in range(min(NODE_CONCURRENCY, len(batch))):
        threading.Thread(target=worker, daemon=True).start()

    results = []
    while len(results) < len(batch):
        try:
            results.append(done.get(timeout=5))
        except queue.Empty:
            pass
        now = time.monotonic()
        hung = [row for row, since in list(active.values())
                if now - since > FILE_TIMEOUT]
        if hung:
            while not todo.empty():
                todo.get_nowait()
            while not done.empty():
                results.append(done.get_nowait())
            return results, hung
    return results, []


def _one(client: IAClient, row) -> tuple[int, dict]:
    url = client.node_url(row["node_server"], row["node_dir"], row["filename"])
    try:
        rz = RemoteZip(lambda s, e: client.get_range(url, s, e), int(row["size"]))
        data = extract(rz, fallback_name=row["filename"], ext=row["ext"])
        data["state"] = STATE_DONE
        data["error"] = None
        return row["id"], data
    except (RemoteZipError, ManifestError) as exc:
        state = STATE_PERMANENT if exc.permanent else STATE_RETRYABLE
        return row["id"], {"state": state, "error": str(exc)[:300]}
    except IAError as exc:
        state = STATE_PERMANENT if exc.permanent else STATE_RETRYABLE
        return row["id"], {"state": state, "error": str(exc)[:300]}
    except Exception as exc:
        return row["id"], {
            "state": STATE_RETRYABLE,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
