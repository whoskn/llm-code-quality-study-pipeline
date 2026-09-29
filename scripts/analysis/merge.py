#!/usr/bin/env python3
"""Join the SonarQube, CodeQL, linter and commit stages into one row per repository and snapshot month."""
import argparse
import glob
import os
import sys

import pandas as pd

SPINE = ["repo", "month", "offset", "sha", "commit_date", "carried"]
ACTIVITY = ["commits", "merges", "reverts", "bot_commits", "marker_commits", "agent_file_commits",
            "authors", "files", "insertions", "deletions", "scope_files", "scope_insertions",
            "scope_deletions"]


def read(paths):
    """Concatenate CSVs as text, so every measured value is written back exactly as read."""
    if not paths:
        sys.exit("no input CSV files")
    return pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in paths],
                     ignore_index=True)


def period(offset):
    o = int(offset)
    return "pre" if o < 0 else "m0" if o == 0 else "post"


def activity(path):
    """Per-month commit counts of one repository's first-parent history."""
    c = pd.read_csv(path)
    g = c.groupby("month")
    a = pd.DataFrame({
        "commits": g.size(),
        "merges": g["is_merge"].sum(),
        "reverts": g["is_revert"].sum(),
        "bot_commits": g["bot"].count(),
        # a commit carrying agent metadata or touching an agent file counts once
        "marker_commits": g.apply(lambda d: (d["llm_family"].notna() | d["agent_files"].notna()).sum()),
        "agent_file_commits": g["agent_files"].count(),
        # an empty author email still counts as one author
        "authors": g["author_email"].apply(lambda e: e.str.lower().nunique(dropna=False)),
        "files": g["files"].sum(),
        "insertions": g["insertions"].sum(),
        "deletions": g["deletions"].sum(),
        "scope_files": g["scope_files"].sum(),
        "scope_insertions": g["scope_insertions"].sum(),
        "scope_deletions": g["scope_deletions"].sum(),
    })
    return a.add_prefix("act_")


def merge(repo, lint, sonar, codeql, commits):
    spine = lint[lint["repo"] == repo][SPINE].copy()
    spine.insert(3, "period", spine["offset"].map(period))
    s = sonar[sonar["repo"] == repo].drop(columns="repo").set_index("month").add_prefix("sonar_")
    q = codeql[codeql["repo"] == repo].drop(columns=SPINE[2:] + ["repo"]).set_index("month")
    ln = lint[lint["repo"] == repo].drop(columns=SPINE[2:] + ["repo"]).rename(columns={"ver": "versions"})
    out = (spine.set_index("month")
           .join(s).join(q.add_prefix("codeql_")).join(ln.set_index("month").add_prefix("lint_")))
    path = os.path.join(commits, repo.replace("/", "__") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"no commit history for {repo}: {path}")
    act = activity(path)
    out = out.join(act)
    out[act.columns] = out[act.columns].fillna(0).astype(int)
    return out.reset_index()[["repo", "month"] + [c for c in out.columns if c != "repo"]]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-s", "--sample", default="data/05_sample/sample.csv", help="final sample CSV")
    p.add_argument("-S", "--sonar", default="data/06_measures/measures.csv", help="SonarQube measures")
    p.add_argument("-q", "--codeql", default="data/06_codeql", help="CodeQL measures directory")
    p.add_argument("-l", "--lint", default="data/11_lint", help="linter measures directory")
    p.add_argument("-c", "--commits", default="data/07_commits", help="per-repository commit CSVs")
    p.add_argument("-o", "--output", default="-",
                   help="directory for one CSV per repository, - for one combined CSV on stdout")
    args = p.parse_args()
    repos = pd.read_csv(args.sample)["full_name"]
    sonar = read([args.sonar])
    codeql = read(sorted(glob.glob(os.path.join(args.codeql, "measures_*.csv"))))
    lint = read(sorted(glob.glob(os.path.join(args.lint, "measures_*.csv"))))
    missing = sorted(set(repos) - set(lint["repo"]))
    if missing:
        sys.exit(f"no linter rows for {len(missing)} sample repositories: {', '.join(missing)}")
    frames = [merge(r, lint, sonar, codeql, args.commits).sort_values("offset", key=lambda o: o.astype(int))
              for r in repos]
    if args.output == "-":
        pd.concat(frames).to_csv(sys.stdout, index=False)
        return 0
    os.makedirs(args.output, exist_ok=True)
    for f in frames:
        f.to_csv(os.path.join(args.output, f["repo"].iloc[0].replace("/", "__") + ".csv"), index=False)
    print(f"{args.output}  {len(frames)} repositories, {sum(map(len, frames))} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
