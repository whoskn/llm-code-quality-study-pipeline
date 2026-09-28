#!/usr/bin/env python3
import argparse
import csv
import subprocess
import sys

COLUMNS = ["repo", "month", "offset", "sha", "commit_date", "carried"]


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip()
                           else f"git exit {r.returncode}")
    return r.stdout.strip()


def key(month):
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def name(k):
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def window_months(m0, pre, post):
    m = key(m0)
    return [name(k) for k in range(m - pre, m + post + 1)]


def head_ref(repo):
    try:
        return git(repo, "symbolic-ref", "--short", "HEAD")
    except RuntimeError:
        return "HEAD"


# Last commit before month end (UTC); a month without one repeats the previous, carried.
def snapshots(repo, m0, pre, post, ref=None):
    ref = ref or head_ref(repo)
    m = key(m0)
    months = list(range(m - pre, m + post + 1))
    rows, previous = [], None
    for k in months:
        cutoff = f"{name(k + 1)}-01T00:00:00+00:00"  # exclusive: first instant of next month
        line = git(repo, "log", "-1", "--first-parent", f"--before={cutoff}",
                   "--pretty=format:%H %cI", ref)
        row = {"repo": repo, "month": name(k), "offset": k - m}
        if not line:
            rows.append({**row, "sha": "", "commit_date": "", "carried": "missing"})
            continue
        sha, date = line.split()
        row.update(sha=sha, commit_date=date, carried=int(sha == previous))
        previous = sha
        rows.append(row)
    return rows


def main():
    p = argparse.ArgumentParser(
        description="Resolve the monthly snapshot commit of a clone for the window around m0 -> CSV.")
    p.add_argument("repo", nargs="?", help="clone directory (needs a work tree)")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    p.add_argument("-r", "--ref", help="ref to walk (default: the clone's HEAD branch)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    args = p.parse_args()
    if not args.repo or not args.m0:
        p.print_help()
        sys.exit(0)

    try:
        rows = snapshots(args.repo, args.m0, args.pre_months, args.post_months, args.ref)
    except (RuntimeError, ValueError) as e:
        sys.exit(f"{args.repo}: {e}")
    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                     encoding="utf-8")
    try:
        w = csv.DictWriter(out, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
