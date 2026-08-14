from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import crawl, db, discover, enrich, export, probe
from .config import DEFAULT_BUDGET_SECONDS

DEFAULT_DB = "data/apk-archive.sqlite"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="crawler")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_SECONDS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover")

    p_crawl = sub.add_parser("crawl")
    p_crawl.add_argument("--limit", type=int)

    p_enrich = sub.add_parser("enrich")
    p_enrich.add_argument("--limit", type=int)

    p_export = sub.add_parser("export")
    p_export.add_argument("--out", default="build/apk.sqlite")
    p_export.add_argument("--min-enriched", type=int, default=0)

    p_probe = sub.add_parser("probe")
    p_probe.add_argument("--sample", type=int, default=120)
    p_probe.add_argument("--out", default="data/probe-report.json")

    sub.add_parser("stats")

    args = parser.parse_args(argv)

    if args.command == "probe":
        report = probe.run(sample_size=args.sample)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    conn = db.connect(args.db)
    try:
        if args.command == "discover":
            result = discover.run(conn, budget_seconds=args.budget)
        elif args.command == "crawl":
            result = crawl.run(conn, budget_seconds=args.budget, limit=args.limit)
        elif args.command == "enrich":
            result = enrich.run(conn, budget_seconds=args.budget, limit=args.limit)
        elif args.command == "export":
            result = export.run(conn, args.out, min_enriched=args.min_enriched)
        else:
            result = db.stats(conn)

        print(json.dumps(result, indent=2))
        write_stats(conn, result)
    finally:
        conn.close()
    return 0


def write_stats(conn, result: dict) -> None:
    path = Path("data/stats.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = db.stats(conn)
    payload["last_result"] = {k: v for k, v in result.items() if isinstance(v, (int, str))}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
