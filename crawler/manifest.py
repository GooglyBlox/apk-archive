from __future__ import annotations

import json
import re
from io import BytesIO

from PIL import Image

from .config import (ANDROID_NS, DENSITY_ORDER, DEVICE_FEATURES, ICON_EXTENSIONS,
                     ICON_PX, ICON_QUALITY, MAX_ARSC_COMPRESSED_BYTES,
                     MAX_NESTED_APK_BYTES,
                     MAX_ICON_ENTRY_BYTES)
from .remotezip import RemoteZip, RemoteZipError

NS = "{%s}" % ANDROID_NS
MANIFEST_NAME = "AndroidManifest.xml"
ARSC_NAME = "resources.arsc"

_REF_RE = re.compile(r"^@([0-9A-Fa-f]{6,8})$")
_CLEAN_RE = re.compile(r"[._-]?v?\d+(\.\d+)*[a-z]?$", re.I)


class ManifestError(Exception):
    def __init__(self, message: str, *, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


BUNDLE_MANIFESTS = ("manifest.json", "info.json", "meta.sai_v2.json", "meta.sai_v1.json")


def extract(rz: RemoteZip, *, fallback_name: str | None = None,
            ext: str = ".apk") -> dict:
    if ext != ".apk":
        return _extract_bundle(rz, fallback_name)
    return _extract_apk(rz, fallback_name)


def _extract_bundle(rz: RemoteZip, fallback_name: str | None) -> dict:
    try:
        names = rz.entries()
    except RemoteZipError as exc:
        raise ManifestError(str(exc), permanent=exc.permanent) from exc

    meta = None
    for candidate in BUNDLE_MANIFESTS:
        if candidate in names:
            try:
                meta = json.loads(rz.read(candidate))
                break
            except (RemoteZipError, ValueError):
                continue
    if not isinstance(meta, dict):
        return _extract_nested(rz, names, fallback_name)

    label = meta.get("name") or meta.get("label")
    if isinstance(label, dict):
        label = label.get("en") or next(iter(label.values()), None)

    out = {
        "package": _text(meta.get("package_name") or meta.get("package")),
        "app_label": _text(label) or _name_from_filename(fallback_name),
        "version_name": _text(str(meta.get("version_name"))
                              if meta.get("version_name") is not None else None),
        "version_code": _int(meta.get("version_code")),
        "min_sdk": _int(meta.get("min_sdk_version") or meta.get("min_sdk")),
        "target_sdk": _int(meta.get("target_sdk_version") or meta.get("target_sdk")),
        "features": None,
        "icon": None,
        "manifest_bytes": 0,
    }
    if not out["package"]:
        raise ManifestError("bundle manifest lacks a package name", permanent=True)

    icon_path = meta.get("icon") if isinstance(meta.get("icon"), str) else "icon.png"
    for candidate in (icon_path, "icon.png"):
        if candidate and candidate in names:
            out["icon"] = _read_image(rz, candidate)
            if out["icon"]:
                break
    out["devices"] = ["phone"]
    return out


def _extract_nested(rz: RemoteZip, names: dict, fallback_name: str | None) -> dict:
    from .remotezip import STORED, from_bytes, nested

    candidates = [e for name, e in names.items()
                  if name.lower().endswith(".apk") and not name.endswith("/")]
    if not candidates:
        raise ManifestError("bundle has no manifest and no inner apk", permanent=True)

    base = max(candidates, key=lambda e: e.uncompressed_size)
    if base.method == STORED:
        inner = nested(rz, base)
    else:
        if base.compressed_size > MAX_NESTED_APK_BYTES:
            raise ManifestError(
                f"inner apk is {base.compressed_size // 1048576} MB compressed, "
                f"over the {MAX_NESTED_APK_BYTES // 1048576} MB cap",
                permanent=True,
            )
        inner = from_bytes(rz.read_entry(base))

    out = _extract_apk(inner, fallback_name)
    if not out.get("package"):
        stem = base.name.rsplit("/", 1)[-1]
        if stem.lower().endswith(".apk"):
            stem = stem[:-4]
        if stem.count(".") >= 2:
            out["package"] = stem
    return out


def _extract_apk(rz: RemoteZip, fallback_name: str | None) -> dict:
    try:
        raw = rz.read(MANIFEST_NAME)
    except RemoteZipError as exc:
        raise ManifestError(str(exc), permanent=exc.permanent) from exc

    root = _parse(raw)
    app = root.find("application")

    out: dict = {
        "package": _text(root.get("package")),
        "version_name": _resolve_later(root.get(NS + "versionName")),
        "version_code": _int(root.get(NS + "versionCode")),
        "min_sdk": None,
        "target_sdk": None,
        "app_label": None,
        "features": None,
        "icon": None,
        "manifest_bytes": len(raw),
    }

    sdk = root.find("uses-sdk")
    if sdk is not None:
        out["min_sdk"] = _int(sdk.get(NS + "minSdkVersion"))
        out["target_sdk"] = _int(sdk.get(NS + "targetSdkVersion"))

    features = [
        f.get(NS + "name") for f in root.findall("uses-feature")
        if f.get(NS + "name")
    ]
    if features:
        out["features"] = json.dumps(sorted(set(features)))
    out["devices"] = _devices(features)

    label = app.get(NS + "label") if app is not None else None
    icon_ref = app.get(NS + "icon") if app is not None else None
    if _is_ref(label) or _is_ref(out["version_name"]) or _is_ref(icon_ref):
        table = _load_arsc(rz)
        if table is not None:
            if _is_ref(label):
                label = _resolve(table, label)
            if _is_ref(out["version_name"]):
                out["version_name"] = _resolve(table, out["version_name"])
            if _is_ref(icon_ref):
                out["icon"] = _icon(rz, table, icon_ref)

    if _is_ref(label):
        label = None
    if _is_ref(out["version_name"]):
        out["version_name"] = None

    out["app_label"] = _text(label) or _name_from_filename(fallback_name)
    return out


def _icon(rz: RemoteZip, table, ref: str) -> bytes | None:
    match = _REF_RE.match(ref or "")
    if not match:
        return None
    try:
        configs = table.get_resolved_res_configs(int(match.group(1), 16))
    except Exception:
        return None

    paths = []
    for _cfg, value in configs or []:
        if isinstance(value, str) and value.lower().endswith(ICON_EXTENSIONS):
            paths.append(value)
    if not paths:
        paths = _launcher_fallback(rz)

    for path in sorted(set(paths), key=_density_rank):
        blob = _read_image(rz, path)
        if blob is not None:
            return blob
    return None


def _launcher_fallback(rz: RemoteZip) -> list[str]:
    try:
        names = rz.entries().keys()
    except RemoteZipError:
        return []
    pattern = re.compile(r"^res/(mipmap|drawable)[^/]*/(ic_launcher|app_icon|icon)[^/]*$", re.I)
    return [n for n in names
            if pattern.match(n) and n.lower().endswith(ICON_EXTENSIONS)
            and not n.lower().endswith(".9.png")]


def _density_rank(path: str) -> tuple[int, int]:
    lowered = path.lower()
    for index, density in enumerate(DENSITY_ORDER):
        if f"-{density}" in lowered:
            return index, len(path)
    return len(DENSITY_ORDER), len(path)


def _read_image(rz: RemoteZip, path: str) -> bytes | None:
    try:
        entry = rz.entries().get(path)
    except RemoteZipError:
        return None
    if entry is None or entry.compressed_size > MAX_ICON_ENTRY_BYTES:
        return None
    try:
        raw = rz.read_entry(entry)
    except RemoteZipError:
        return None
    return _thumbnail(raw)


def _thumbnail(raw: bytes) -> bytes | None:
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        img.thumbnail((ICON_PX, ICON_PX), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="WEBP", quality=ICON_QUALITY, method=4)
        return out.getvalue()
    except (OSError, ValueError):
        return None


def _parse(raw: bytes):
    from pyaxmlparser.axmlprinter import AXMLPrinter

    try:
        printer = AXMLPrinter(raw)
    except Exception as exc:
        raise ManifestError(f"AXML parse failed: {exc}") from exc
    if not printer.is_valid():
        raise ManifestError("AXML reported invalid")
    root = printer.get_xml_obj()
    if root is None:
        raise ManifestError("AXML produced no root element")
    return root


def _load_arsc(rz: RemoteZip):
    try:
        entry = rz.entries().get(ARSC_NAME)
    except RemoteZipError:
        return None
    if entry is None or entry.compressed_size > MAX_ARSC_COMPRESSED_BYTES:
        return None
    try:
        from pyaxmlparser.arscparser import ARSCParser

        return ARSCParser(rz.read_entry(entry))
    except Exception:
        return None


def _resolve(table, ref: str) -> str | None:
    match = _REF_RE.match(ref or "")
    if not match:
        return None
    try:
        configs = table.get_resolved_res_configs(int(match.group(1), 16))
    except Exception:
        return None
    if not configs:
        return None
    best = None
    for cfg, value in configs:
        if not value:
            continue
        try:
            default = not cfg.get_language().strip("\x00")
        except Exception:
            default = False
        if default:
            return value
        best = best or value
    return best


def _devices(features: list[str]) -> list[str]:
    hits = {DEVICE_FEATURES[f] for f in features if f in DEVICE_FEATURES}
    return sorted(hits) if hits else ["phone"]


def _is_ref(value) -> bool:
    return isinstance(value, str) and bool(_REF_RE.match(value))


def _resolve_later(value):
    return value


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    value = str(value).strip()
    try:
        if value.lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except ValueError:
        return None


def _name_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    base = filename.rsplit("/", 1)[-1]
    for ext in (".apk", ".xapk", ".apks", ".apkm", ".aab"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    base = base.replace("_", " ").replace("+", " ")
    base = _CLEAN_RE.sub("", base).strip(" -._")
    return base or None
