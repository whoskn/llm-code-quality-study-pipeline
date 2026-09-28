#!/usr/bin/env python3
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys

import scripts.scan.snapshot_commits as snapshot_commits
from scripts.dataset.date_repo import B_MARKERS, C_MARKERS, pathspecs
from scripts.scan.scan_repo import EXCLUSIONS, INCLUSIONS, clone, git_opts, log, sample_row

SEP = "\x1e"  # record separator between commits
FIELD = "\x1f"  # field separator within a commit

# Tool names, matched only on commits a family pattern already flagged: the bare words
# are common in prose. Several may fire on one commit.
TOOLS = {
    "claude": r"claude|anthropic",
    "copilot": r"copilot",
    "cursor": r"cursor",
    "codex": r"codex|openai",
    "gemini": r"gemini|jules",
    "devin": r"devin",
    "aider": r"aider",
    "openhands": r"openhands",
    "coderabbit": r"coderabbit",
    "windsurf": r"windsurf",
    "sourcery": r"sourcery",
}
# Kind of evidence that fired; a tool-written trailer outweighs a sentence in the body.
SIGNALS = {
    "trailer": re.compile(r"^co-authored-by:\s*(claude|codex|copilot|cursor|devin|gemini"
                          r"|aider|openhands|windsurf)|^agent-logs-url:", re.I | re.M),
    "identity": re.compile(r"\[bot\]|noreply@(anthropic|openai)\.com|\bcursoragent\b", re.I),
    "message": re.compile(r"generated with \[?claude code|🤖", re.I),
    "branch": re.compile(r"^merge pull request #\d+ from \S+?/"
                         r"(codex|copilot|cursor|devin|jules)/", re.I | re.M),
}
BOT = re.compile(r"([A-Za-z0-9_.-]+)\[bot\]", re.I)
PR = re.compile(r"(?:^Merge pull request #(\d+)\b|\(#(\d+)\)\s*$)", re.M)

COLUMNS = [
    "repo", "sha", "month", "offset", "period", "commit_date", "author_date",
    "author_name", "author_email", "committer_name", "committer_email",
    "parents", "is_merge",
    # Change size over all files, then over the measured scope.
    "files", "insertions", "deletions",
    "scope_files", "scope_insertions", "scope_deletions",
    # Agent configuration files touched (family A).
    "agent_files",
    "llm_family", "llm_tools", "llm_signals", "bot", "is_revert", "pr", "subject",
    "message",
]


def git(tree, *args):
    r = subprocess.run(["git"] + git_opts() + ["-C", tree, *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip()
                           else f"git exit {r.returncode}")
    return r.stdout


def scope():
    return ([f":(glob){INCLUSIONS}"]
            + [f":(exclude,glob){p}" for p in EXCLUSIONS.split(",")])


# Binary files count as changed files without lines; merges have no numstat block.
def numstat(text):
    files = ins = dels = 0
    for line in text.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0] != "-":
            ins += int(parts[0])
            dels += int(parts[1])
    return files, ins, dels


def bounds(m0, pre, post):
    months = snapshot_commits.window_months(m0, pre, post)
    end = snapshot_commits.name(snapshot_commits.key(months[-1]) + 1)
    return f"{months[0]}-01T00:00:00+00:00", f"{end}-01T00:00:00+00:00"


def rev_args(ref, since, until):
    return [ref, f"--since={since}", f"--until={until}"]


# --full-history keeps merged-branch commits a pathspec walk would simplify away.
def scope_stats(tree, ref, since, until):
    out = git(tree, "log", "--full-history", "--numstat",
              f"--pretty=format:{SEP}%H{FIELD}", *rev_args(ref, since, until),
              "--", *scope())
    stats = {}
    for record in out.split(SEP):
        if not record.strip():
            continue
        sha, _, block = record.partition(FIELD)
        stats[sha.strip()] = numstat(block)
    return stats


def agent_touches(tree, ref, since, until):
    out = git(tree, "log", "--full-history", "--name-only",
              f"--pretty=format:{SEP}%H{FIELD}", *rev_args(ref, since, until),
              "--", *pathspecs())
    touched = {}
    for record in out.split(SEP):
        if not record.strip():
            continue
        sha, _, block = record.partition(FIELD)
        touched[sha.strip()] = ";".join(sorted(set(block.split())))
    return touched


# Same marker patterns as the dating stage.
def marker(meta, body):
    family = "B" if B_MARKERS.search(meta) else "C" if C_MARKERS.search(body) else ""
    if not family:
        return "", "", ""
    tools = [name for name, pattern in TOOLS.items() if re.search(pattern, meta, re.I)]
    signals = [name for name, pattern in SIGNALS.items() if pattern.search(meta)]
    return family, ";".join(tools), ";".join(signals)


# Not --first-parent: markers sit on the commits of merged branches.
def commits(tree, repo, m0, pre, post, ref):
    ref = ref or snapshot_commits.head_ref(tree)
    since, until = bounds(m0, pre, post)
    stats = scope_stats(tree, ref, since, until)
    agents = agent_touches(tree, ref, since, until)
    fmt = SEP + FIELD.join(["%H", "%P", "%aI", "%cI", "%an", "%ae", "%cn", "%ce", "%s",
                            "%B"]) + FIELD
    out = git(tree, "log", "--reverse", "--numstat", f"--pretty=format:{fmt}",
              *rev_args(ref, since, until))
    m = snapshot_commits.key(m0)
    for record in out.split(SEP):
        if not record.strip():
            continue
        (sha, parents, adate, cdate, an, ae, cn, ce, subject, body,
         block) = record.split(FIELD, 10)
        offset = snapshot_commits.key(cdate) - m
        if not -pre <= offset <= post:  # the date bounds are coarse; the month is not
            continue
        meta = "\n".join([an, ae, cn, ce, body])
        family, tools, signals = marker(meta, body)
        bot = BOT.search(f"{an} {ae} {cn} {ce}")
        pr = PR.search(f"{subject}\n{body}")
        files, ins, dels = numstat(block)
        scoped = stats.get(sha, (0, 0, 0))
        yield {
            "repo": repo, "sha": sha, "month": cdate[:7], "offset": offset,
            "period": "pre" if offset < 0 else "m0" if offset == 0 else "post",
            "commit_date": cdate, "author_date": adate,
            "author_name": an, "author_email": ae,
            "committer_name": cn, "committer_email": ce,
            "parents": len(parents.split()), "is_merge": int(len(parents.split()) > 1),
            "files": files, "insertions": ins, "deletions": dels,
            "scope_files": scoped[0], "scope_insertions": scoped[1],
            "scope_deletions": scoped[2],
            "agent_files": agents.get(sha, ""),
            "llm_family": family, "llm_tools": tools, "llm_signals": signals,
            "bot": bot.group(1) if bot else "",
            "is_revert": int(subject.lower().startswith("revert")),
            "pr": pr.group(1) or pr.group(2) if pr else "",
            "subject": subject, "message": body.strip(),
        }


def done_shas(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["sha"] for r in csv.DictReader(f) if r.get("sha")}


def run(args):
    out = os.path.join(args.out, args.repo.replace("/", "__") + ".csv")
    part = out + ".part"
    if os.path.exists(out) and not args.force:
        log(f"{args.repo}: already extracted, {out}")
        return
    done = done_shas(part)
    tree = os.path.join(args.work, args.repo.replace("/", "__"))
    shutil.rmtree(tree, ignore_errors=True)
    os.makedirs(args.out, exist_ok=True)
    log(f"{args.repo}: cloning ({len(done)} commits already written)" if done
        else f"{args.repo}: cloning")
    clone(args.repo, tree, args.mirror)
    n = 0
    try:
        with open(part, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, COLUMNS, restval="", extrasaction="ignore")
            if not done:
                w.writeheader()
            for row in commits(tree, args.repo, args.m0, args.pre_months,
                               args.post_months, args.ref):
                if row["sha"] in done:
                    continue
                w.writerow(row)
                n += 1
                # Flushed per row: the pod can be evicted mid-repository.
                if n % args.flush == 0:
                    f.flush()
                    os.fsync(f.fileno())
                    log(f"  {n} commits ({row['month']})")
            f.flush()
            os.fsync(f.fileno())
    finally:
        if not args.keep:
            # The clone is most of the pod's disk; nothing reads it later.
            shutil.rmtree(tree, ignore_errors=True)
    # The rename marks completion; a leftover .part is a crashed run.
    os.replace(part, out)
    log(f"{args.repo}: {n + len(done)} commits -> {out}")


def main():
    p = argparse.ArgumentParser(
        description="Extract every commit of one repo's event window, with LLM markers, into one CSV.")
    p.add_argument("repo", nargs="?", help="repository as owner/name")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-o", "--out", default=".",
                   help="directory the per-repo CSV is written to (default: .)")
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
    p.add_argument("-n", "--flush", type=int, default=100,
                   help="flush the CSV every N commits (default: 100)")
    p.add_argument("-k", "--keep", action="store_true", help="keep the clone afterwards")
    p.add_argument("-f", "--force", action="store_true",
                   help="re-extract a repo whose CSV is already complete")
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

    try:
        run(args)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"{args.repo}: {e}")


if __name__ == "__main__":
    main()
