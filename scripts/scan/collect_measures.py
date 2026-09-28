#!/usr/bin/env python3
import argparse
import csv
import os
import sys

from scripts.scan.scan_repo import api, project_key, servers

METRICS = [
    "ncloc", "files", "functions", "statements", "classes",
    "complexity", "cognitive_complexity",
    "code_smells", "sqale_index", "sqale_debt_ratio",
    "bugs", "vulnerabilities", "security_hotspots",
    "violations", "blocker_violations", "critical_violations", "major_violations",
    "minor_violations", "info_violations",
    "duplicated_lines_density", "comment_lines_density",
]
PREFIX = project_key("", "").rstrip("_")


# The repo comes from the project name; the key has lost its slash.
def projects(srv):
    out = []
    for url, token in srv:
        page = 1
        while True:
            data = api(url, token, "api/components/search",
                       qualifiers="TRK", ps=500, p=page)
            for c in data["components"]:
                if not c["key"].startswith(PREFIX):
                    continue
                repo, _, month = c["name"].rpartition(" ")
                out.append((repo, month, url, token, c["key"]))
            paging = data["paging"]
            if paging["pageIndex"] * paging["pageSize"] >= paging["total"]:
                break
            page += 1
    return out


def measures(url, token, keys, metrics):
    data = api(url, token, "api/measures/search",
               projectKeys=",".join(keys), metricKeys=",".join(metrics))
    out = {k: {} for k in keys}
    for m in data["measures"]:
        out[m["component"]][m["metric"]] = m.get("value", "")
    return out


# No in-scope file still yields zero issue counts; such rows are blanked.
def blank_unmeasured(row):
    return row if row.get("ncloc") else {k: "" for k in row}


def rows(srv, metrics, batch=100):
    found = projects(srv)
    by_server = {}
    for repo, month, url, token, key in found:
        by_server.setdefault((url, token), []).append((repo, month, key))
    out = []
    for (url, token), items in by_server.items():
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            got = measures(url, token, [k for _, _, k in chunk], metrics)
            for repo, month, key in chunk:
                row = {"repo": repo, "month": month, "server": url, "project": key}
                row.update(blank_unmeasured({m: got[key].get(m, "") for m in metrics}))
                out.append(row)
    return out


def main():
    p = argparse.ArgumentParser(
        description="Read the measures of every scanned snapshot off all SonarQube instances into one CSV.")
    p.add_argument("-n", "--servers", default=os.environ.get("SONAR_SERVERS"),
                   help='SonarQube instances as "url=token url=token ..."')
    p.add_argument("-m", "--metrics", default=",".join(METRICS),
                   help="comma-separated metric keys")
    p.add_argument("-o", "--output", default="-", help="output CSV (default stdout)")
    args = p.parse_args()
    if not args.servers:
        p.print_help()
        return 0
    try:
        srv = servers(args.servers)
    except ValueError as e:
        sys.exit(f"--servers: {e}")
    metrics = args.metrics.split(",")
    try:
        out = rows(srv, metrics)
    except RuntimeError as e:
        sys.exit(f"{e}")
    out.sort(key=lambda r: (r["repo"], r["month"]))
    fh = sys.stdout if args.output == "-" else open(args.output, "w", newline="")
    w = csv.DictWriter(fh, ["repo", "month", "server", "project"] + metrics)
    w.writeheader()
    w.writerows(out)
    if fh is not sys.stdout:
        fh.close()
    sys.stderr.write(f"{len(out)} snapshots from {len(srv)} instances\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
