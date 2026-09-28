#!/usr/bin/env python3
import argparse
import base64
import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import scripts.scan.snapshot_commits as snapshot_commits

# Only .py files are submitted, so ncloc is a Python line count.
INCLUSIONS = "**/*.py"
EXCLUSIONS = ",".join([
    "**/test/**", "**/tests/**", "**/testing/**", "**/test_*.py", "**/*_test.py",
    "**/conftest.py", "**/vendor/**", "**/vendored/**", "**/third_party/**",
    "**/node_modules/**", "**/site-packages/**", "**/.venv/**", "**/venv/**",
    "**/migrations/**", "**/*_pb2.py", "**/*_pb2_grpc.py", "**/build/**", "**/dist/**",
    "**/docs/**", "**/examples/**",
])
PYTHON_VERSION = "3.9, 3.10, 3.11, 3.12, 3.13"

# One status row per snapshot: the server keeps successful analyses only.
STATUS_COLUMNS = ["repo", "month", "offset", "sha", "server", "status", "seconds"]


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def sink(path):
    if path == "-":
        return contextlib.nullcontext(sys.stdout)
    if not path:
        return contextlib.nullcontext(None)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", newline="", encoding="utf-8")


def fresh(path):
    return path == "-" or not os.path.exists(path) or os.path.getsize(path) == 0


def api(url, token, path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    auth = base64.b64encode(f"{token}:".encode()).decode()
    req = urllib.request.Request(f"{url}/{path}?{query}",
                                 headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
    except OSError as e:
        raise RuntimeError(f"{path}: {e}")


def servers(spec):
    out = []
    for pair in spec.split():
        url, _, token = pair.partition("=")
        if not url or not token:
            raise ValueError(f"not a url=token pair: {pair}")
        out.append((url.rstrip("/"), token))
    return out


# Placed by a hash of the project key, independent of the sample file and run order.
def server_for(key, srv):
    return srv[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(srv)]


# One project per snapshot: SonarQube rejects analyses dated before a project's last.
def project_key(repo, month):
    return "ba_" + re.sub(r"[^A-Za-z0-9_.:-]", "_", f"{repo}__{month}")


# No repository hooks; the token goes through a credential helper, never the URL.
def git_opts():
    opts = ["-c", "core.hooksPath=", "-c", "safe.directory=*"]
    if os.environ.get("GITHUB_TOKEN"):
        opts += ["-c", "credential.helper="
                 "!f(){ echo username=x-access-token; echo \"password=$GITHUB_TOKEN\"; };f"]
    return opts


def clone(repo, dest, mirror=None):
    src = f"{mirror}/{repo}.git" if mirror else f"https://github.com/{repo}.git"
    r = subprocess.run(["git"] + git_opts() + ["clone", "--no-checkout", "--quiet",
                                               src, dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"clone failed: {r.stderr.strip().splitlines()[-1:] or ''}")


# The scanner follows symlinks and would measure their targets twice.
def drop_symlinks(tree):
    out = subprocess.run(["git"] + git_opts() + ["-C", tree, "ls-files", "-s", "-z"],
                         capture_output=True, text=True).stdout
    for entry in out.split("\0"):
        if entry.startswith("120000"):
            os.remove(os.path.join(tree, entry.split("\t", 1)[1]))


def checkout(tree, sha):
    git = ["git"] + git_opts() + ["-C", tree]
    subprocess.run(git + ["checkout", "--force", "--quiet", sha],
                   check=True, capture_output=True, text=True)
    subprocess.run(git + ["clean", "-xdfq"], check=True, capture_output=True, text=True)
    drop_symlinks(tree)


def scan(tree, work, args, key, row, server):
    url, token = server
    props = {
        "sonar.host.url": url,
        "sonar.token": token,
        "sonar.projectKey": key,
        "sonar.projectName": f"{args.repo} {row['month']}",
        "sonar.projectVersion": row["month"],
        "sonar.projectBaseDir": tree,
        "sonar.sources": ".",
        "sonar.inclusions": INCLUSIONS,
        "sonar.exclusions": EXCLUSIONS,
        "sonar.python.version": PYTHON_VERSION,
        "sonar.scm.disabled": "true",  # detached HEAD, and blame is not an outcome here
        "sonar.working.directory": f"{work}/.scannerwork",
        "sonar.scanner.metadataFilePath": f"{work}/report-task.txt",
        "sonar.log.level": "WARN",
    }
    cmd = [args.scanner] + [f"-D{k}={v}" for k, v in props.items()]
    r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                       timeout=args.scan_timeout)
    if r.returncode != 0:
        out = (r.stdout + r.stderr).strip()
        with open(f"{work}/scanner-{row['month']}.log", "w", encoding="utf-8") as f:
            f.write(out)
        lines = [l for l in out.splitlines() if "ERROR" in l] or out.splitlines()[-3:]
        raise RuntimeError("scanner: " + " | ".join(lines[:3]))
    with open(f"{work}/report-task.txt", encoding="utf-8") as f:
        meta = dict(line.strip().split("=", 1) for line in f if "=" in line)
    return meta["ceTaskId"]


def wait_task(args, server, task_id):
    url, token = server
    deadline = time.time() + args.task_timeout
    while time.time() < deadline:
        task = api(url, token, "api/ce/task", id=task_id)["task"]
        if task["status"] in ("SUCCESS", "FAILED", "CANCELED"):
            if task["status"] != "SUCCESS":
                raise RuntimeError(f"ce task {task['status']}: {task.get('errorMessage', '')}")
            return
        time.sleep(args.poll)
    raise RuntimeError("ce task timeout")


# The servers hold the resume state. Search by repo name: q matches keys exactly only.
def analysed_months(args):
    prefix = project_key(args.repo, "")
    months = []
    for url, token in args.servers:
        page = 1
        while True:
            data = api(url, token, "api/components/search",
                       qualifiers="TRK", q=args.repo, ps=500, p=page)
            months += [c["key"][len(prefix):] for c in data["components"]
                       if c["key"].startswith(prefix)]
            paging = data["paging"]
            if paging["pageIndex"] * paging["pageSize"] >= paging["total"]:
                break
            page += 1
    return sorted(set(m for m in months if re.fullmatch(r"\d{4}-\d{2}", m)))


def sample_row(path, index):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not 0 <= index < len(rows):
        return None, None
    return rows[index]["full_name"], rows[index]["m0"]


def run(args):
    tree = f"{args.work}/{args.repo.replace('/', '__')}"
    # All months held, not the newest: a failed month leaves a hole a cursor would skip.
    done = set(analysed_months(args))
    missing = [m for m in snapshot_commits.window_months(args.m0, args.pre_months,
                                                        args.post_months)
               if m not in done]
    log(f"{args.repo}: server has {len(done)} snapshots, {len(missing)} of the window missing")
    if not missing:
        log(f"{args.repo}: all snapshots already scanned")
        return
    shutil.rmtree(tree, ignore_errors=True)
    log(f"{args.repo}: cloning")
    clone(args.repo, tree, args.mirror)
    try:
        rows = snapshot_commits.snapshots(tree, args.m0, args.pre_months,
                                         args.post_months, args.ref)
        pending = [r for r in rows if r["month"] not in done]
        log(f"{args.repo}: m0={args.m0} {len(pending)} of {len(rows)} snapshots to scan")
        with sink(args.status) as out:
            w = csv.DictWriter(out, STATUS_COLUMNS, restval="", extrasaction="ignore") \
                if out else None
            if w and fresh(args.status):
                w.writeheader()
            for row in pending:
                # Snapshot rows carry the clone path as repo.
                row = {**row, "repo": args.repo}
                if not row["sha"]:  # month predates the first commit: nothing to scan
                    log(f"  {row['month']} no commit")
                    if w:
                        w.writerow({**row, "status": "no commit"})
                        out.flush()
                    continue
                key = project_key(args.repo, row["month"])
                server = server_for(key, args.servers)
                started = time.time()
                try:
                    checkout(tree, row["sha"])
                    wait_task(args, server, scan(tree, args.work, args, key, row, server))
                    status = "ok"
                except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                    status = f"error: {e}"[:600]
                seconds = round(time.time() - started, 1)
                if w:
                    w.writerow({**row, "server": server[0], "status": status,
                                "seconds": seconds})
                    out.flush()
                log(f"  {row['month']} {row['sha'][:8]} -> {server[0]} {status[:600]}"
                    f" ({seconds}s)")
    finally:
        if not args.keep:
            shutil.rmtree(tree, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(
        description="Clone one repo and scan each of its monthly snapshots into its own SonarQube project.")
    p.add_argument("repo", nargs="?", help="repository as owner/name")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-S", "--sample", help="take repo and m0 from row --index of this CSV "
                                          "instead of the positional arguments")
    p.add_argument("-I", "--index", type=int,
                   default=int(os.environ.get("JOB_COMPLETION_INDEX", -1)),
                   help="0-based row of --sample (default: $JOB_COMPLETION_INDEX)")
    p.add_argument("-o", "--status", default="-",
                   help="CSV one row per snapshot outcome goes to, '-' for stdout "
                        "(default: -)")
    p.add_argument("-d", "--work", default=".", help="directory for the clone (default: .)")
    p.add_argument("-n", "--servers", default=os.environ.get("SONAR_SERVERS", ""),
                   help="SonarQube replicas as 'url=token url=token ...'; snapshots are "
                        "spread over them (default: $SONAR_SERVERS, else "
                        "$SONARQUBE_URL=$SONAR_TOKEN)")
    p.add_argument("-s", "--scanner", default="sonar-scanner", help="scanner CLI to run")
    p.add_argument("-M", "--mirror", help="clone from this base URL instead of GitHub")
    p.add_argument("-r", "--ref", help="ref to walk (default: the clone's HEAD branch)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-k", "--keep", action="store_true", help="keep the clone afterwards")
    p.add_argument("--scan-timeout", type=int, default=3600,
                   help="seconds one scanner run may take (default: 3600)")
    p.add_argument("--task-timeout", type=int, default=1800,
                   help="seconds to wait for server-side analysis (default: 1800)")
    p.add_argument("--poll", type=int, default=5,
                   help="seconds between ce task polls (default: 5)")
    args = p.parse_args()
    if args.sample:
        if args.index < 0:
            sys.exit("--sample needs --index or $JOB_COMPLETION_INDEX")
        args.repo, args.m0 = sample_row(args.sample, args.index)
        if not args.repo:
            # Job index past the end of the sample: nothing to do.
            log(f"index {args.index} is past the end of {args.sample}")
            return
        log(f"index {args.index} -> {args.repo} m0={args.m0}")
    if not args.repo or not args.m0:
        p.print_help()
        sys.exit(0)
    spec = args.servers or "{}={}".format(os.environ.get("SONARQUBE_URL", ""),
                                          os.environ.get("SONAR_TOKEN", ""))
    try:
        args.servers = servers(spec)
    except ValueError as e:
        sys.exit(f"--servers: {e} (or set $SONAR_SERVERS, or $SONARQUBE_URL and "
                 f"$SONAR_TOKEN for a single replica)")

    try:
        run(args)
    except RuntimeError as e:
        sys.exit(f"{args.repo}: {e}")


if __name__ == "__main__":
    main()
