#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

from union_candidates import merge

API = "https://api.github.com/graphql"
FRAGMENT = """fragment F on Repository {
  databaseId nameWithOwner isFork isArchived isMirror isEmpty isDisabled
  stargazerCount createdAt pushedAt diskUsage
  licenseInfo { spdxId }
  primaryLanguage { name }
}"""
META = ["gh_status", "gh_id", "gh_full_name", "gh_language", "gh_stars", "gh_fork",
        "gh_archived", "gh_mirror", "gh_empty", "gh_license", "gh_created",
        "gh_pushed", "gh_size_kb"]


def post(query, token, tries=5):
    body = json.dumps({"query": query}).encode()
    headers = {"Authorization": f"bearer {token}", "Content-Type": "application/json",
               "User-Agent": "ba-repo-filter"}
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(API, body, headers), timeout=90) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                sys.exit("GitHub rejected the token (401)")
            if e.code not in (403, 429, 500, 502, 503) or attempt == tries - 1:
                detail = e.read()[:200].decode(errors="replace")
                sys.exit(f"GitHub API error {e.code}: {detail}")
            wait = int(e.headers.get("Retry-After") or 2 ** attempt * 15)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == tries - 1:
                sys.exit(f"GitHub API unreachable: {e}")
            wait = 2 ** attempt * 5
        else:
            kinds = {e.get("type") for e in data.get("errors") or ()}
            if "RATE_LIMITED" not in kinds:
                return data
            wait = 60
        sys.stderr.write(f"retrying in {wait}s\n")
        time.sleep(wait)


def query_batch(names, token):
    parts = []
    for i, name in enumerate(names):
        owner, _, repo = name.partition("/")
        parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, "
                     f"name: {json.dumps(repo)}) {{ ...F }}")
    data = post(f"{FRAGMENT}\nquery {{ rateLimit {{ remaining }} {' '.join(parts)} }}",
                token)
    if data.get("data") is None:
        sys.exit(f"GraphQL returned no data: {json.dumps(data.get('errors'))[:300]}")
    return data["data"]


def flatten(repo):
    if repo is None:
        return {"gh_status": "not_found"}
    return {
        "gh_status": "ok",
        "gh_id": repo["databaseId"],
        "gh_full_name": repo["nameWithOwner"],
        "gh_language": (repo["primaryLanguage"] or {}).get("name", ""),
        "gh_stars": repo["stargazerCount"],
        "gh_fork": repo["isFork"],
        "gh_archived": repo["isArchived"],
        "gh_mirror": repo["isMirror"] or repo["isDisabled"],
        "gh_empty": repo["isEmpty"],
        "gh_license": (repo["licenseInfo"] or {}).get("spdxId", ""),
        "gh_created": (repo["createdAt"] or "")[:10],
        "gh_pushed": (repo["pushedAt"] or "")[:10],
        "gh_size_kb": repo["diskUsage"],
    }


# Renamed or transferred repositories share a numeric id; the duplicate stays, marked.
def dedup_ids(rows):
    seen, dropped = {}, 0
    for row in rows:
        rid = row.get("gh_id")
        if row["gh_status"] != "ok" or not rid:
            continue
        if rid not in seen:
            seen[rid] = row
        else:
            merge(seen[rid], row)
            row["gh_status"] = "duplicate_id"
            dropped += 1
    return dropped


def write(path, columns, rows):
    try:
        out = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot write {path}: {e}")
    try:
        w = csv.DictWriter(out, columns, restval="")
        w.writeheader()
        w.writerows(rows)
    finally:
        if out is not sys.stdout:
            out.close()


def main():
    p = argparse.ArgumentParser(
        description="Attach GitHub metadata to a candidate list via batched GraphQL.")
    p.add_argument("-i", "--input", default="-",
                   help="candidate CSV with a full_name column, '-' for stdin (default: -)")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    p.add_argument("-b", "--batch", type=int, default=50,
                   help="repos per GraphQL request (default: 50)")
    args = p.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")

    try:
        src = sys.stdin if args.input == "-" else open(args.input, newline="",
                                                      encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot read {args.input}: {e}")
    try:
        reader = csv.DictReader(src)
        if "full_name" not in (reader.fieldnames or ()):
            sys.exit(f"{args.input}: no full_name column")
        columns = list(reader.fieldnames) + META
        rows = list(reader)
    finally:
        if src is not sys.stdin:
            src.close()

    todo = []
    for row in rows:
        if all(row["full_name"].partition("/")):
            todo.append(row)
        else:
            row["gh_status"] = "bad_name"

    try:
        for start in range(0, len(todo), args.batch):
            batch = todo[start:start + args.batch]
            data = query_batch([r["full_name"] for r in batch], token)
            for i, row in enumerate(batch):
                row.update(flatten(data.get(f"r{i}")))
            left = (data.get("rateLimit") or {}).get("remaining", "?")
            sys.stderr.write(f"\r{start + len(batch)}/{len(todo)} fetched, "
                             f"{left} API points left")
    except (SystemExit, KeyboardInterrupt):
        # Partial results are written before failing.
        sys.stderr.write("\nwriting partial results\n")
        write(args.output, columns, rows)
        raise
    sys.stderr.write("\n")

    dropped = dedup_ids(rows)
    ok = sum(r["gh_status"] == "ok" for r in rows)
    sys.stderr.write(f"{ok} unique repos resolved, {dropped} duplicate ids collapsed, "
                     f"{len(rows) - ok - dropped} unresolvable\n")
    write(args.output, columns, rows)


if __name__ == "__main__":
    main()
