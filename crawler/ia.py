from __future__ import annotations

import threading
import time
import urllib.parse
from typing import Iterator

import requests

from . import config


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self.min_interval


class IAError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class IAClient:
    def __init__(self, *, metadata_interval: float | None = None,
                 node_interval: float | None = None):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=32, pool_maxsize=32, max_retries=0
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.meta_limiter = RateLimiter(
            config.METADATA_MIN_INTERVAL if metadata_interval is None else metadata_interval
        )
        self.node_limiter = RateLimiter(
            config.NODE_MIN_INTERVAL if node_interval is None else node_interval
        )
        self.bytes_downloaded = 0
        self._byte_lock = threading.Lock()

    def _request(self, method: str, url: str, *, limiter: RateLimiter,
                 headers: dict | None = None, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            limiter.acquire()
            try:
                resp = self.session.request(
                    method, url, headers=headers,
                    timeout=config.REQUEST_TIMEOUT, **kwargs
                )
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code in (200, 206):
                    return resp
                if resp.status_code in (404, 410):
                    raise IAError(f"{resp.status_code} for {url}", permanent=True)
                if resp.status_code == 429:
                    time.sleep(_retry_after(resp, attempt))
                    continue
                if 400 <= resp.status_code < 500:
                    raise IAError(f"{resp.status_code} for {url}", permanent=True)
                last_exc = IAError(f"{resp.status_code} for {url}")
            time.sleep(min(config.BACKOFF_BASE ** attempt, config.BACKOFF_CAP))
        raise IAError(f"exhausted retries for {url}: {last_exc}")

    def _count_bytes(self, n: int) -> None:
        with self._byte_lock:
            self.bytes_downloaded += n

    def scrape(self, query: str, *,
               fields: str = "identifier,item_size,title,publicdate,collection",
               count: int = 10000, cursor: str | None = None,
               ) -> tuple[list[dict], str | None, int]:
        if count < 100:
            raise ValueError("IA scrape API requires count >= 100")
        params = {"q": query, "fields": fields, "count": count}
        if cursor:
            params["cursor"] = cursor
        resp = self._request(
            "GET", "https://archive.org/services/search/v1/scrape",
            limiter=self.meta_limiter, params=params,
        )
        data = resp.json()
        if "error" in data:
            raise IAError(f"scrape error: {data['error']}", permanent=True)
        return data.get("items", []), data.get("cursor"), int(data.get("total", 0))

    def scrape_all(self, query: str, **kwargs) -> Iterator[list[dict]]:
        cursor = None
        while True:
            items, cursor, _ = self.scrape(query, cursor=cursor, **kwargs)
            if items:
                yield items
            if not cursor:
                return

    def count(self, query: str) -> int:
        resp = self._request(
            "GET", "https://archive.org/advancedsearch.php",
            limiter=self.meta_limiter,
            params={"q": query, "fl[]": "identifier", "rows": 0, "output": "json"},
        )
        return int(resp.json()["response"]["numFound"])

    def metadata(self, identifier: str) -> dict:
        resp = self._request(
            "GET", f"https://archive.org/metadata/{urllib.parse.quote(identifier)}",
            limiter=self.meta_limiter,
        )
        self._count_bytes(len(resp.content))
        data = resp.json()
        if not data:
            raise IAError(f"empty metadata for {identifier}", permanent=True)
        return data

    @staticmethod
    def node_url(node_server: str, node_dir: str, filename: str) -> str:
        return f"https://{node_server}{node_dir}/{urllib.parse.quote(filename)}"

    def get_range(self, url: str, start: int | None, end: int | None) -> bytes:
        if start is None:
            if end is None:
                raise ValueError("need at least one of start/end")
            rng = f"bytes=-{end}"
        else:
            rng = f"bytes={start}-" + ("" if end is None else str(end))
        resp = self._request(
            "GET", url, limiter=self.node_limiter, headers={"Range": rng}
        )
        if resp.status_code != 206:
            raise IAError(f"range ignored (status {resp.status_code}) for {url}")
        data = resp.content
        self._count_bytes(len(data))
        return data


def _retry_after(resp: requests.Response, attempt: int) -> float:
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return min(float(raw), config.BACKOFF_CAP)
        except ValueError:
            pass
    return min(config.BACKOFF_BASE ** attempt, config.BACKOFF_CAP)
