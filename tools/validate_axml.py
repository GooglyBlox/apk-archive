"""Validate remote manifest extraction against real Internet Archive APKs.

Run from the repo root:  .venv/Scripts/python.exe tools/validate_axml.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.ia import IAClient          # noqa: E402
from crawler.remotezip import RemoteZip  # noqa: E402

ITEMS = [
    "com-android-chrome-139-0-7258-143-20260812",
    "apkapps_201907",
    "ultimaterobloxmobilearchive",
    "Gameloft-1.0.0",
    "trg-apk-archive",
]


def main() -> int:
    client = IAClient()
    tasks = []
    for ident in ITEMS:
        meta = client.metadata(ident)
        apks = [f for f in meta["files"] if f["name"].lower().endswith(".apk")]
        for f in apks[:3]:
            tasks.append((ident, meta["d1"], meta["dir"], f["name"], int(f["size"])))

    print(f"probing {len(tasks)} APKs across {len(ITEMS)} items\n")
    from pyaxmlparser.axmlprinter import AXMLPrinter

    t0 = time.time()
    ok = 0
    for ident, host, ddir, name, size in tasks:
        url = client.node_url(host, ddir, name)
        rz = RemoteZip(lambda s, e: client.get_range(url, s, e), size)
        try:
            raw = rz.read("AndroidManifest.xml")
            root = AXMLPrinter(raw).get_xml_obj()
            pkg = root.get("package")
            ver = root.get("{http://schemas.android.com/apk/res/android}versionName")
            code = root.get("{http://schemas.android.com/apk/res/android}versionCode")
            sdk = root.find("uses-sdk")
            mn = sdk.get("{http://schemas.android.com/apk/res/android}minSdkVersion") if sdk is not None else None
            status = "OK"
            ok += 1
        except Exception as exc:                       # noqa: BLE001
            pkg = ver = code = mn = None
            status = f"FAIL {type(exc).__name__}: {exc}"[:52]
        print(f"{status:<12} {name[:30]:<30} {size/1e6:6.1f}MB  "
              f"pkg={str(pkg)[:36]:<36} v={str(ver)[:10]:<10} code={code} minSdk={mn}")

    el = time.time() - t0
    print(f"\n{ok}/{len(tasks)} parsed in {el:.1f}s | "
          f"{client.bytes_downloaded/1024:.0f} KB downloaded | "
          f"{client.bytes_downloaded/1024/max(len(tasks),1):.0f} KB per APK")
    return 0 if ok == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
