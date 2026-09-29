#!/usr/bin/env python3
"""Derive m0, family marker dates and monthly density from clones -> CSV (plan.md §4)."""
import argparse
import csv
import re
import subprocess
import sys
from datetime import datetime, timezone

SEP = "\x1e"  # record separator between commits
FIELD = "\x1f"  # field separator within a commit

# family A: files a project commits when it adopts an agent. Globs match at any depth.
AGENT_FILES = ("CLAUDE.md", "AGENTS.md", "AGENT.md", "GEMINI.md", "QWEN.md",
               ".cursorrules", ".windsurfrules", ".clinerules", ".goosehints",
               ".aider.conf.yml", ".aiderignore", ".roomodes",
               ".github/copilot-instructions.md")
AGENT_DIRS = (".cursor", ".claude", ".codex", ".gemini", ".windsurf", ".continue",
              ".junie", ".clinerules", ".roo", ".github/instructions",
              ".github/prompts", ".github/chatmodes")

# family B: the marker is git metadata written by the tool, not by a human
B_MARKERS = re.compile(r"""
    noreply@anthropic\.com | noreply@openai\.com
  | (claude|copilot(-swe-agent)?|cursor|devin-ai-integration|openhands(-agent)?
     |coderabbitai|chatgpt-codex-connector|google-labs-jules|gemini-code-assist
     |sweep-ai|codegen-sh|ellipsis-dev|sourcery-ai|deepsource-autofix)\[bot\]
  | \bcursoragent\b
  | co-authored-by:\s*(claude|codex|copilot|cursor|devin|gemini|aider|openhands|windsurf)
  | ^agent-logs-url:
  | generated\ with\ \[?claude\ code
  | merge\ pull\ request\ \#\d+\ from\ \S+?/(codex|copilot|cursor|devin|jules)/
""", re.I | re.M | re.X)

# family C: a human saying they used an LLM. Deliberately narrow — these repos go to full
# manual review (§5, tier 3), so precision matters more than recall here.
C_MARKERS = re.compile(r"""
    \b(generated|written|created|refactored|implemented|rewritten)\ (this\ )?
      (with|by|using)\ (the\ )?(help\ of\ )?(chatgpt|gpt-?[45]|claude|copilot|cursor
      |gemini|codex|an?\ llm|ai)\b
  | \b(chatgpt|copilot|claude|cursor|gemini|codex)\ (suggested|wrote|generated|helped)\b
  | \bai[-\ ]generated\b | \bvibe[-\ ]coded\b | \bwith\ ai\ assistance\b
  | \bthanks\ to\ (chatgpt|claude|copilot|cursor)\b
  | \bprompted\ (chatgpt|claude|an\ llm)\b
""", re.I | re.X)

# the measured scope of the scan stage, as scan_repo.py sets it, in git's wildmatch
# dialect. Duplicated rather than imported because date_repo.py runs on its own on the
# clone shards; the two lists must stay in step or the size gate and the measurement it
# gates disagree about what is being counted.
SCOPE = [":(glob)**/*.py"] + [f":(exclude,glob){glob}" for glob in (
    "**/test/**", "**/tests/**", "**/testing/**", "**/test_*.py", "**/*_test.py",
    "**/conftest.py", "**/vendor/**", "**/vendored/**", "**/third_party/**",
    "**/node_modules/**", "**/site-packages/**", "**/.venv/**", "**/venv/**",
    "**/migrations/**", "**/*_pb2.py", "**/*_pb2_grpc.py", "**/build/**", "**/dist/**",
    "**/docs/**", "**/examples/**",
)]

COLUMNS = ["repo", "status", "m0", "family_spread", "clone_families",
           "a_first", "a_commit", "b_first", "b_commit", "c_first", "c_commit",
           "first_commit", "last_commit", "total_commits", "marker_commits",
           "marker_share", "pre_active", "post_active", "onset_months", "py_loc"]


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip()
                           else f"git exit {r.returncode}")
    return r.stdout


def key(iso):
    """ISO date -> a month number, so window arithmetic is plain integer arithmetic."""
    return int(iso[:4]) * 12 + int(iso[5:7]) - 1


def month(k):
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def utc_key(stamp):
    """Unix time -> month number in UTC, the zone the snapshot cutoffs are expressed in."""
    when = datetime.fromtimestamp(int(stamp), timezone.utc)
    return when.year * 12 + when.month - 1


def snapshot_months(repo):
    """Months carrying a commit on the branch the monthly snapshots are taken from.

    snapshot_commits.py resolves each snapshot by walking --first-parent from HEAD with a
    UTC cutoff, so a commit inside a merged branch can never move a snapshot. The density
    gate has to be counted on that same walk: over all reachable commits a repository that
    merges its work looks active in months whose snapshot only repeats the previous
    month's tree, which is exactly the repetition the gate exists to keep out. Branch
    selection is deliberately not taken from --all-branches for the same reason.
    """
    out = git(repo, "log", "--first-parent", "--pretty=format:%ct")
    return {utc_key(stamp) for stamp in out.split()}


def pathspecs():
    return ([f":(glob)**/{name}" for name in AGENT_FILES]
            + [f":(glob)**/{name}/**" for name in AGENT_DIRS])


def scan_commits(repo, rev_args):
    """One pass over the history: month of every commit, and B/C marker commits."""
    fmt = FIELD.join(["%H", "%cI", "%an", "%ae", "%cn", "%ce", "%B"]) + SEP
    # --reverse: oldest first, so the setdefault below keeps each family's *first* marker
    log = git(repo, "log", "--reverse", f"--pretty=format:{fmt}", *rev_args)
    months, markers, hits = [], {}, {}
    for record in log.split(SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, date, *ident, body = record.split(FIELD, 6)
        months.append(key(date))
        meta = "\n".join(ident + [body])
        # a commit carrying tool-written metadata is one artifact, so it is family B only
        family = "B" if B_MARKERS.search(meta) else "C" if C_MARKERS.search(body) else ""
        if family:
            markers[sha] = key(date)
            hits.setdefault(family, (date[:7], sha))
    return months, markers, hits


def scan_agent_files(repo, rev_args):
    """Family A: when an agent file first appeared, and every month one was touched."""
    added = git(repo, "log", "--diff-filter=A", "--pretty=format:%H %cI", "--",
                *pathspecs()).split("\n")
    touched = git(repo, "log", "--pretty=format:%H %cI", "--", *pathspecs()).split("\n")
    first = min((line for line in added if line.strip()),
                key=lambda line: line.split()[1], default="")
    months = {line.split()[0]: key(line.split()[1]) for line in touched if line.strip()}
    if not first:
        return None, months
    sha, date = first.split()
    return (date[:7], sha), months


def scope_loc(repo, m0, post):
    """In-scope Python lines at the last snapshot of the window, for the §3 size gate.

    The window end is where a growing repository peaks, so gating on that one commit is
    enough. git grep -c "" counts every line of every tracked in-scope file, the same
    physical-line unit radon reports in the linter stage; on a blobless clone it fetches
    the blobs of that commit only.
    """
    cutoff = f"{month(m0 + post + 1)}-01T00:00:00+00:00"
    sha = git(repo, "log", "-1", "--first-parent", f"--before={cutoff}",
              "--pretty=format:%H").strip()
    if not sha:
        return ""
    # exit 1 means no file matched, which is a legitimate zero, not a failure
    out = git(repo, "grep", "-I", "-c", "", sha, "--", *SCOPE, check=False)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line)


def measure(repo, rev_args, pre, post, onset):
    """All clone-derived fields for one repository."""
    row = {"repo": repo, "status": "ok"}
    months, markers, hits = scan_commits(repo, rev_args)
    if not months:
        return {"repo": repo, "status": "no commits"}
    a_hit, a_months = scan_agent_files(repo, rev_args)
    if a_hit:
        hits["A"] = a_hit
    # keyed by sha: a commit carrying B metadata that also touches an agent file is one
    # marker, not two
    markers = sorted({**markers, **a_months}.values())
    for family in "ABC":
        date, sha = hits.get(family, ("", ""))
        row[f"{family.lower()}_first"], row[f"{family.lower()}_commit"] = date, sha
    row["first_commit"], row["last_commit"] = month(min(months)), month(max(months))
    row["total_commits"], row["marker_commits"] = len(months), len(markers)
    row["marker_share"] = f"{len(markers) / len(months):.4f}"
    if not hits:
        return {**row, "status": "no markers"}

    # §4: m0 is the earliest first-marker month across the families that fired, never a
    # date inherited from a source dataset
    firsts = sorted(key(d) for d, _ in hits.values())
    m0 = firsts[0]
    row["m0"] = month(m0)
    row["family_spread"] = firsts[-1] - firsts[0]
    row["clone_families"] = ";".join(sorted(hits))
    active = snapshot_months(repo)
    row["pre_active"] = len(active & set(range(m0 - pre, m0)))
    row["post_active"] = len(active & set(range(m0 + 1, m0 + post + 1)))
    row["onset_months"] = len(set(markers) & set(range(m0 + 1, m0 + onset + 1)))
    row["py_loc"] = scope_loc(repo, m0, post)
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repos", nargs="*", help="repository directories")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    p.add_argument("-b", "--all-branches", action="store_true",
                   help="scan all refs, not just the default branch")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-n", "--onset-window", type=int, default=6,
                   help="post-months the sustained-onset count looks at (default: 6)")
    args = p.parse_args()
    if not args.repos:
        p.print_help()
        sys.exit(0)

    try:
        out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                        encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot write {args.output}: {e}")
    rev_args = ["--all"] if args.all_branches else []
    try:
        w = csv.DictWriter(out, COLUMNS, restval="")
        w.writeheader()
        for repo in args.repos:
            try:
                row = measure(repo, rev_args, args.pre_months, args.post_months,
                              args.onset_window)
            except RuntimeError as e:
                row = {"repo": repo, "status": f"error: {e}"}
            w.writerow(row)
            out.flush()
            sys.stderr.write(f"{repo}: {row['status']}"
                             f"{' m0=' + row['m0'] if row.get('m0') else ''}\n")
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
