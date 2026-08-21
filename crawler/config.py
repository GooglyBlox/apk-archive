from __future__ import annotations

import os

USER_AGENT = os.environ.get(
    "APK_ARCHIVE_UA",
    "apk-archive/0.1 (+https://github.com/GooglyBlox/apk-archive)",
)

METADATA_CONCURRENCY = int(os.environ.get("APK_ARCHIVE_METADATA_CONCURRENCY", "2"))
METADATA_MIN_INTERVAL = float(os.environ.get("APK_ARCHIVE_METADATA_INTERVAL", "0.9"))

NODE_CONCURRENCY = int(os.environ.get("APK_ARCHIVE_NODE_CONCURRENCY", "12"))
NODE_MIN_INTERVAL = float(os.environ.get("APK_ARCHIVE_NODE_INTERVAL", "0.0"))

MAX_RETRIES = 5
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
REQUEST_TIMEOUT = 90.0

DEFAULT_BUDGET_SECONDS = int(os.environ.get("APK_ARCHIVE_BUDGET", str(int(5.5 * 3600))))
CHECKPOINT_INTERVAL = int(os.environ.get("APK_ARCHIVE_CHECKPOINT", "300"))

APK_EXTENSIONS = (".apk", ".xapk", ".apks", ".apkm", ".aab")
ENRICHABLE_EXTENSIONS = (".apk", ".xapk", ".apks", ".apkm")

EOCD_TAIL_BYTES = 65557
MAX_ARSC_COMPRESSED_BYTES = int(os.environ.get("APK_ARCHIVE_MAX_ARSC", str(12 * 1024 * 1024)))

ICON_PX = int(os.environ.get("APK_ARCHIVE_ICON_PX", "96"))
ICON_QUALITY = int(os.environ.get("APK_ARCHIVE_ICON_QUALITY", "80"))
MAX_ICON_ENTRY_BYTES = int(os.environ.get("APK_ARCHIVE_MAX_ICON", str(512 * 1024)))
MAX_NESTED_APK_BYTES = int(os.environ.get("APK_ARCHIVE_MAX_NESTED", str(32 * 1024 * 1024)))
ICON_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")
DENSITY_ORDER = ["xhdpi", "xxhdpi", "hdpi", "xxxhdpi", "mdpi", "ldpi", "nodpi", "tvdpi"]

STATE_PENDING = 0
STATE_DONE = 1
STATE_RETRYABLE = 3
STATE_PERMANENT = 4

ANDROID_NS = "http://schemas.android.com/apk/res/android"

API_LEVELS = {
    1: "1.0", 2: "1.1", 3: "1.5", 4: "1.6", 5: "2.0", 6: "2.0.1", 7: "2.1",
    8: "2.2", 9: "2.3", 10: "2.3.3", 11: "3.0", 12: "3.1", 13: "3.2",
    14: "4.0", 15: "4.0.3", 16: "4.1", 17: "4.2", 18: "4.3", 19: "4.4",
    20: "4.4W", 21: "5.0", 22: "5.1", 23: "6.0", 24: "7.0", 25: "7.1",
    26: "8.0", 27: "8.1", 28: "9", 29: "10", 30: "11", 31: "12", 32: "12L",
    33: "13", 34: "14", 35: "15", 36: "16",
}

DEVICE_FEATURES = {
    "android.software.leanback": "tv",
    "android.hardware.type.television": "tv",
    "android.hardware.type.watch": "watch",
    "android.hardware.type.automotive": "auto",
}
