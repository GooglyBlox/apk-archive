from __future__ import annotations

import argparse
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class RangeHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        header = self.headers.get("Range")
        if not header:
            return super().send_head()

        match = RANGE_RE.match(header.strip())
        if not match:
            return super().send_head()

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None

        size = os.fstat(f.fileno()).st_size
        start_s, end_s = match.group(1), match.group(2)
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        else:
            length = int(end_s or 0)
            start = max(0, size - length)
            end = size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        f.seek(start)
        return _Slice(f, end - start + 1)


class _Slice:
    def __init__(self, fh, remaining):
        self.fh = fh
        self.remaining = remaining

    def read(self, n=-1):
        if self.remaining <= 0:
            return b""
        if n < 0 or n > self.remaining:
            n = self.remaining
        data = self.fh.read(n)
        self.remaining -= len(data)
        return data

    def close(self):
        self.fh.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="web")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    handler = partial(RangeHandler, directory=args.dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {args.dir} at http://127.0.0.1:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
