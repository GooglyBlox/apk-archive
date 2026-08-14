from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.export import version_key  # noqa: E402

ORDERED = [
    ("1.2", "1.9"),
    ("1.9", "1.10"),
    ("1.10", "1.20"),
    ("9.0", "10.0"),
    ("1.0.0-rc2", "1.0.0-rc10"),
    ("1.0.0b", "1.0.0h"),
    ("100", "204"),
    ("v1.0.0", "1.0.1"),
    ("1.0", "1.0.1"),
    ("999999999999", "1000000000000"),
    ("2.9.9", "2.10.0"),
    ("0.9", "1.0"),
]

EQUAL = [
    ("v1.0.0", "1.0.0"),
    ("1.0.0", "01.0.0"),
    ("2.0", "2.0"),
]


def main() -> int:
    failures = []

    for lo, hi in ORDERED:
        if not version_key(lo) < version_key(hi):
            failures.append(f"{lo!r} should sort before {hi!r} "
                            f"({version_key(lo)!r} vs {version_key(hi)!r})")

    for a, b in EQUAL:
        if version_key(a) != version_key(b):
            failures.append(f"{a!r} and {b!r} should share a key "
                            f"({version_key(a)!r} vs {version_key(b)!r})")

    for value in (None, "", "beta", "unknown"):
        version_key(value)

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1

    print(f"{len(ORDERED)} ordering + {len(EQUAL)} equality cases passed")
    print("sample:", sorted(["1.10", "1.9", "1.2", "10.0", "9.0", "2.0"], key=version_key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
