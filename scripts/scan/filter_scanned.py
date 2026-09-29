#!/usr/bin/env python3
"""Drop the sample rows whose repository is already fully scanned on the SonarQube instances."""
import argparse
import csv
import sys

from scripts.scan.scan_repo import api, project_key, servers
from scripts.scan.snapshot_commits import window_months


def analysed_keys(srv):
    """Every analysis project key on every instance, unioned.

    server_for() spreads a repo's snapshots over the instances, so no single one of them
    can answer whether a repo is done.
    """
    keys = set()
    for url, token in srv:
        page = 1
        while True:
            data = api(url, token, "api/components/search",
                       qualifiers="TRK", ps=500, p=page)
            keys.update(c["key"] for c in data["components"])
            paging = data["paging"]
            if paging["pageIndex"] * paging["pageSize"] >= paging["total"]:
                break
            page += 1
    return keys


def pending(rows, keys, pre, post):
    """The rows still worth scanning: those missing at least one month of their window.

    Months are looked up by name rather than counted, so projects from a different window
    or a revised m0 cannot stand in for the months actually wanted. A row without an m0
    is kept and the judgement left to the scanner.
    """
    out = []
    for row in rows:
        if not row.get("m0"):
            out.append(row)
        elif any(project_key(row["full_name"], m) not in keys
                 for m in window_months(row["m0"], pre, post)):
            out.append(row)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sample", nargs="?", help="sample CSV with full_name and m0 columns")
    p.add_argument("-n", "--servers",
                   help="SonarQube instances as 'url=token url=token ...'")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
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
            reader = csv.DictReader(f)
            rows, columns = list(reader), reader.fieldnames
    except OSError as e:
        sys.exit(f"{args.sample}: {e}")
    try:
        keys = analysed_keys(srv)
    except RuntimeError as e:
        sys.exit(f"{e}")

    left = pending(rows, keys, args.pre_months, args.post_months)
    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                     encoding="utf-8")
    try:
        w = csv.DictWriter(out, columns)
        w.writeheader()
        w.writerows(left)
    finally:
        if out is not sys.stdout:
            out.close()
    print(f"{len(rows) - len(left)} of {len(rows)} repos already scanned, "
          f"{len(left)} left", file=sys.stderr)


if __name__ == "__main__":
    main()
