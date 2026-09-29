#!/usr/bin/env python3
"""Show, per sample repo, which months of its window are already scanned on the SonarQube instances."""
import argparse
import csv
import os
import sys

from scripts.scan.scan_repo import project_key, servers
from scripts.scan.filter_scanned import analysed_keys
from scripts.scan.snapshot_commits import window_months

DONE, TODO = "■", "□"


def bar(repo, m0, keys, pre, post):
    months = window_months(m0, pre, post)
    cells = [DONE if project_key(repo, m) in keys else TODO for m in months]
    return "".join(cells), cells.count(DONE), len(cells)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sample", nargs="?", help="sample CSV with full_name and m0 columns")
    p.add_argument("-n", "--servers", default=os.environ.get("SONARS", ""),
                   help="SonarQube instances as 'url=token url=token ...' "
                        "(default: $SONARS)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    args = p.parse_args()
    if not args.sample or not args.servers:
        p.print_help()
        sys.exit(0)

    try:
        srv = servers(args.servers)
    except ValueError as e:
        sys.exit(f"--servers: {e}")
    try:
        with open(args.sample, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        sys.exit(f"{args.sample}: {e}")
    try:
        keys = analysed_keys(srv)
    except RuntimeError as e:
        sys.exit(f"{e}")

    width = max((len(r["full_name"]) for r in rows), default=0)
    total = scanned = 0
    lines = []
    for row in rows:
        if not row.get("m0"):
            lines.append(f"{row['full_name']:<{width}}  no m0")
            continue
        cells, n, of = bar(row["full_name"], row["m0"], keys,
                           args.pre_months, args.post_months)
        scanned, total = scanned + n, total + of
        lines.append(f"{row['full_name']:<{width}}  {cells} {n:>2}/{of}")
    half = (len(lines) + 1) // 2
    left, right = lines[:half], lines[half:] + [""] * (half - len(lines[half:]))
    cell = max((len(l) for l in left), default=0)
    for a, b in zip(left, right):
        print(f"{a:<{cell}}   {b}".rstrip())
    print(f"{scanned}/{total} snapshots scanned", file=sys.stderr)


if __name__ == "__main__":
    main()
