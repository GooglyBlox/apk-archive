from __future__ import annotations

import contextlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .config import (DEFAULT_BUDGET_SECONDS, NODE_CONCURRENCY, STATE_DONE,
                     STATE_PERMANENT, STATE_RETRYABLE)
from .ia import IAClient, IAError
from .manifest import ManifestError, extract
from .remotezip import RemoteZip, RemoteZipError

BATCH = 500

NOISE = ("res1 is not zero", "Invalid start_offset", "Skipping",
         "invalid decoded string length")


class _FilteredStdout:
    def __init__(self, target):
        self.target = target

    def write(self, text):
        if text.strip() and any(n in text for n in NOISE):
            return len(text)
        return self.target.write(text)

    def flush(self):
        self.target.flush()

    def __getattr__(self, name):
        return getattr(self.target, name)


@contextlib.contextmanager
def _quiet():
    original = sys.stdout
    sys.stdout = _FilteredStdout(original)
    try:
        yield
    finally:
        sys.stdout = original


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

        with _quiet(), ThreadPoolExecutor(NODE_CONCURRENCY) as pool:
            results = list(pool.map(lambda r: _one(client, r), batch))

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

    stats = db.stats(conn)
    db.finish_run(conn, run_id, processed, errors, client.bytes_downloaded,
                  note=f"files_enriched={stats['files_enriched']}")
    return stats


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
