from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor

from .config import APK_EXTENSIONS, METADATA_CONCURRENCY
from .ia import IAClient, IAError

KNOWN = {"phonesoftware", "apkarchive", "android_apps"}

QUERIES = [
    'subject:android AND -collection:phonesoftware',
    'subject:apk AND -collection:phonesoftware',
    'mediatype:software AND apk AND -collection:phonesoftware',
    'collection:softwarelibrary AND android',
    'collection:open_source_software AND apk',
]


def run(sample_size: int = 120, seed: int = 0) -> dict:
    client = IAClient()
    rng = random.Random(seed)
    report = {"queries": [], "known_collections": sorted(KNOWN)}

    for query in QUERIES:
        try:
            total = client.count(query)
            items, _, _ = client.scrape(query, count=max(sample_size * 3, 100))
        except IAError as exc:
            report["queries"].append({"query": query, "error": str(exc)})
            continue

        rng.shuffle(items)
        sample = items[:sample_size]
        identifiers = [i["identifier"] for i in sample]

        with ThreadPoolExecutor(METADATA_CONCURRENCY) as pool:
            results = list(pool.map(lambda i: _inspect(client, i), identifiers))

        checked = [r for r in results if r is not None]
        novel = [r for r in checked if not r["in_known"]]
        contaminated = len(checked) - len(novel)

        with_apks = [r for r in novel if r["apks"] > 0]
        apks = sum(r["apks"] for r in novel)
        entry = {
            "query": query,
            "total_items": total,
            "sampled": len(checked),
            "contaminated_by_known_collections": contaminated,
            "genuinely_novel": len(novel),
            "novel_with_apks": len(with_apks),
            "apks_in_novel": apks,
            "apks_per_novel_item": round(apks / len(novel), 2) if novel else 0,
            "projected_new_apks": int(round(apks / len(novel) * total)) if novel else 0,
            "top": sorted(
                ({"id": r["id"], "apks": r["apks"]} for r in with_apks),
                key=lambda r: -r["apks"],
            )[:5],
        }
        report["queries"].append(entry)
        print(json.dumps(entry, indent=2))

    return report


def _inspect(client: IAClient, identifier: str):
    try:
        meta = client.metadata(identifier)
    except IAError:
        return None
    except Exception:
        return None

    collections = meta.get("metadata", {}).get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    apks = sum(
        1 for f in meta.get("files", [])
        if (f.get("name") or "").lower().endswith(APK_EXTENSIONS)
    )
    return {
        "id": identifier,
        "in_known": bool(KNOWN.intersection(collections)),
        "apks": apks,
    }
