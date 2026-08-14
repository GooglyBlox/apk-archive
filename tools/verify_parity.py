from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.ia import IAClient          # noqa: E402
from crawler.manifest import extract     # noqa: E402
from crawler.remotezip import RemoteZip  # noqa: E402

ITEMS = ["Gameloft-1.0.0", "trg-apk-archive", "ultimaterobloxmobilearchive"]
PER_ITEM = 3
MAX_BYTES = 60 * 1024 * 1024


def local_extract(blob: bytes, filename: str) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(blob))

    class Local:
        def __init__(self):
            self._e = None

        def read(self, name):
            return zf.read(name)

        def entries(self):
            if self._e is None:
                from crawler.remotezip import ZipEntry
                self._e = {
                    i.filename: ZipEntry(i.filename, i.compress_type,
                                         i.compress_size, i.file_size,
                                         i.header_offset, i.CRC)
                    for i in zf.infolist()
                }
            return self._e

        def read_entry(self, entry):
            return zf.read(entry.name)

    return extract(Local(), fallback_name=filename)


def main() -> int:
    client = IAClient()
    checked = mismatched = skipped = 0

    for ident in ITEMS:
        meta = client.metadata(ident)
        apks = [f for f in meta["files"]
                if f["name"].lower().endswith(".apk")
                and int(f.get("size", 0)) < MAX_BYTES]
        for f in apks[:PER_ITEM]:
            name, size = f["name"], int(f["size"])
            url = client.node_url(meta["d1"], meta["dir"], name)

            try:
                rz = RemoteZip(lambda s, e: client.get_range(url, s, e), size)
                remote = extract(rz, fallback_name=name)
            except Exception as exc:
                print(f"SKIP  {name[:44]}: remote failed: {exc}")
                skipped += 1
                continue

            blob = client.get_range(url, 0, size - 1)
            try:
                local = local_extract(blob, name)
            except Exception as exc:
                print(f"SKIP  {name[:44]}: local failed: {exc}")
                skipped += 1
                continue

            keys = ["package", "version_name", "version_code", "min_sdk",
                    "target_sdk", "app_label"]
            diffs = [k for k in keys if remote.get(k) != local.get(k)]
            checked += 1
            if diffs:
                mismatched += 1
                print(f"DIFF  {name[:44]}")
                for k in diffs:
                    print(f"        {k}: remote={remote.get(k)!r} full={local.get(k)!r}")
            else:
                print(f"MATCH {name[:44]:<46} {remote['package']} "
                      f"{remote['version_name']} sdk={remote['min_sdk']}")

    print(f"\n{checked} compared, {mismatched} mismatched, {skipped} skipped")
    print(f"{client.bytes_downloaded/1e6:.1f} MB downloaded (mostly the full-file controls)")
    return 1 if mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
