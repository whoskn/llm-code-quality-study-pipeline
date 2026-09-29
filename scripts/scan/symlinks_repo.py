#!/usr/bin/env python3
"""List the tracked symlinks of one repo's monthly snapshots, classified by target kind."""
import argparse
import csv
import os
import posixpath
import shutil
import subprocess
import sys

import scripts.scan.snapshot_commits as snapshot_commits
from scripts.scan.scan_repo import EXCLUSIONS, INCLUSIONS, git_opts, log, sample_row

COLUMNS = ["repo", "month", "offset", "sha", "link", "target", "resolved", "kind",
           "dup_scope_files"]


def git(tree, *args, **kw):
    r = subprocess.run(["git"] + git_opts() + ["-C", tree, *args], capture_output=True,
                       **kw)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:])
    return r.stdout


def clone(repo, dest, mirror=None):
    """Blobless clone: the trees decide every question here, and only the few symlink
    blobs are ever read, so the file contents are left on the server."""
    src = f"{mirror}/{repo}.git" if mirror else f"https://github.com/{repo}.git"
    r = subprocess.run(["git"] + git_opts() + ["clone", "--filter=blob:none",
                                               "--no-checkout", "--quiet", src, dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"clone failed: {r.stderr.strip().splitlines()[-1:] or ''}")


def tree_entries(tree, sha):
    """(mode, oid, path) of every blob of a commit, recursively."""
    out = git(tree, "ls-tree", "-r", "-z", sha).decode("utf-8", "replace")
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, _, oid = meta.split()
        yield mode, oid, path


def blobs(tree, oids):
    """oid -> content, read in one batch rather than one process per symlink."""
    if not oids:
        return {}
    out = git(tree, "cat-file", "--batch", input="\n".join(oids).encode())
    res, i = {}, 0
    for oid in oids:
        j = out.index(b"\n", i)
        header = out[i:j].split()
        size = int(header[2])
        res[oid] = out[j + 1:j + 1 + size].decode("utf-8", "replace")
        i = j + 1 + size + 1
    return res


def resolve(link, links, dirs, files):
    """Follow a symlink inside the commit to the thing it finally names.

    The chain is followed rather than the first hop taken, because a link to a link to a
    directory is still a directory to the scanner walking the checkout. "escapes" and
    "absolute" leave the work tree: git materialises them as a dangling link on a pod
    that has no such path, so they duplicate nothing here but would on a machine that has.
    """
    base, target, seen = link, links[link], set()
    for _ in range(10):
        if target.startswith("/"):
            return target, "absolute"
        p = posixpath.normpath(posixpath.join(posixpath.dirname(base), target))
        if p == ".." or p.startswith("../"):
            return p, "escapes"
        p = "" if p == "." else p
        if p in links:
            if p in seen:
                return p, "loop"
            seen.add(p)
            base, target = p, links[p]
            continue
        if p in files:
            return p, "file"
        if p != "" and p not in dirs:
            return p, "dangling"
        # a link that contains itself makes the walk infinite, not merely doubled
        return p, "recursive" if p == "" or link.startswith(p + "/") else "dir"
    return p, "loop"


def duplicated(tree, sha, link, resolved, index):
    """How many measured files this link adds a second copy of to a file-system walk.

    The scope patterns are matched against the path the scanner sees, which is the one
    under the link, not the one under the target, so the target tree is read into a
    scratch index under the link's prefix and git is asked which of those paths survive
    the frozen scope. Doing it any other way means reimplementing wildmatch.
    """
    if os.path.exists(index):
        os.remove(index)
    env = {**os.environ, "GIT_INDEX_FILE": index}
    obj = f"{sha}:{resolved}" if resolved else f"{sha}^{{tree}}"
    r = subprocess.run(["git"] + git_opts() + ["-C", tree, "read-tree",
                                              f"--prefix={link}/", obj],
                       capture_output=True, env=env)
    if r.returncode != 0:
        return ""
    out = subprocess.run(["git"] + git_opts()
                         + ["-C", tree, "ls-files", "-z", "--", f":(glob){INCLUSIONS}"]
                         + [f":(exclude,glob){g}" for g in EXCLUSIONS.split(",")],
                         capture_output=True, text=True, env=env).stdout
    return sum(1 for p in out.split("\0") if p)


def snapshot_links(tree, sha, index):
    """One record per tracked symlink of one snapshot."""
    modes = list(tree_entries(tree, sha))
    files = {p for _, _, p in modes}
    dirs = set()
    for p in files:
        while "/" in p:
            p = p.rsplit("/", 1)[0]
            dirs.add(p)
    link_oids = {p: o for m, o, p in modes if m == "120000"}
    content = blobs(tree, sorted(set(link_oids.values())))
    links = {p: content[o].strip() for p, o in link_oids.items()}
    for link in sorted(links):
        resolved, kind = resolve(link, links, dirs, files)
        yield {"link": link, "target": links[link], "resolved": resolved, "kind": kind,
               "dup_scope_files": duplicated(tree, sha, link, resolved, index)
               if kind == "dir" else ""}


def rows(tree, repo, m0, pre, post, ref, index):
    for snap in snapshot_commits.snapshots(tree, m0, pre, post, ref):
        if not snap["sha"]:
            continue
        for link in snapshot_links(tree, snap["sha"], index):
            yield {"repo": repo, "month": snap["month"], "offset": snap["offset"],
                   "sha": snap["sha"], **link}


def run(args, out):
    tree = os.path.join(args.work, args.repo.replace("/", "__"))
    index = os.path.join(args.work, "scratch.index")
    shutil.rmtree(tree, ignore_errors=True)
    clone(args.repo, tree, args.mirror)
    w = csv.DictWriter(out, COLUMNS)
    if args.header:
        w.writeheader()
    n = 0
    try:
        for row in rows(tree, args.repo, args.m0, args.pre_months, args.post_months,
                        args.ref, index):
            w.writerow(row)
            n += 1
        out.flush()
    finally:
        if not args.keep:
            shutil.rmtree(tree, ignore_errors=True)
    log(f"{args.repo}: {n} symlink records")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", help="repository as owner/name")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    p.add_argument("-S", "--sample", help="take repo and m0 from row --index of this CSV "
                                          "instead of the positional arguments")
    p.add_argument("-I", "--index", type=int,
                   default=int(os.environ.get("JOB_COMPLETION_INDEX", -1)),
                   help="0-based row of --sample (default: $JOB_COMPLETION_INDEX)")
    p.add_argument("-d", "--work", default=".",
                   help="directory for the clone (default: .)")
    p.add_argument("-M", "--mirror", help="clone from this base URL instead of GitHub")
    p.add_argument("-r", "--ref", help="ref to walk (default: the clone's HEAD branch)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-H", "--header", action="store_true",
                   help="write the CSV header, off by default so shards concatenate")
    p.add_argument("-k", "--keep", action="store_true", help="keep the clone afterwards")
    args = p.parse_args()
    if args.sample:
        if args.index < 0:
            sys.exit("--sample needs --index or $JOB_COMPLETION_INDEX")
        args.repo, args.m0 = sample_row(args.sample, args.index)
        if not args.repo:
            log(f"index {args.index} is past the end of {args.sample}")
            return
        log(f"index {args.index} -> {args.repo} m0={args.m0}")
    if not args.repo or not args.m0:
        p.print_help()
        sys.exit(0)

    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                     encoding="utf-8")
    try:
        run(args, out)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"{args.repo}: {e}")
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
