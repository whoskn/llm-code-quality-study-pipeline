#!/usr/bin/env python3
"""Clone one repo and measure each monthly snapshot with bandit, radon, lizard and complexipy."""
import argparse
import concurrent.futures
import contextlib
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

import scripts.scan.snapshot_commits as snapshot_commits
from scripts.scan.scan_repo import (EXCLUSIONS, INCLUSIONS, checkout, clone, fresh,
                                    git_opts, log, sample_row, sink)

# Frozen analysis configuration: one pinned version of each tool and one rule set for
# every snapshot of every repo (proposal §4.4), so anything below is a sample-wide
# decision. Versions are not read back from the venv but recorded per row by the caller,
# so a claim built against a different pin would be visible in the data.
#
# Only bandit has a rule set to freeze, and it is frozen in bandit.yaml, passed with -c so
# the same profile applies to every repo whether or not the repo has an opinion of its own
# (see BANDIT_CONFIG). The other three are counters, not rule engines: lizard's CCN,
# complexipy's cognitive complexity and radon's line counts are the same numbers for
# anyone who runs the same versions, which is why they need no configuration decision.
#
# The four tools disagree about glob dialects, and two of them read configuration out of
# the tree they are measuring: lizard its .gitignore, and radon a [radon] section in
# radon.cfg or setup.cfg. A repo that tightens its config after m0 would then show a drop
# that is not in the code at all -- exactly the confound §4.7 warns about. So the file
# list is computed here, once, and passed explicitly to every tool; --no-gitignore closes
# the first, and radon, which has no such flag, is run from a directory outside the tree
# (see radon()). Scope is then identical across the four tools and equal to the SonarQube
# and CodeQL scope, which is what makes the per-KLOC figures comparable between stages.
#
# bandit and complexipy were checked and read neither a .bandit file, a [tool.bandit] or
# [tool.complexipy] section, nor an exclude list unless one is passed explicitly.
BANDIT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bandit.yaml")

INCLUDE = INCLUSIONS.split(",")
EXCLUDE = EXCLUSIONS.split(",")

COLUMNS = ([
    "repo", "month", "offset", "sha", "commit_date", "carried", "status", "seconds",
    # denominators, from radon's raw counter. Sonar's ncloc and CodeQL's loc count a line
    # differently again, so each stage is normalised by its own denominator and the three
    # are never mixed.
    "py_files", "loc", "sloc", "lloc", "comments", "blank",
    # bandit: a third security opinion next to SonarQube and CodeQL, and the only one of
    # the four that carries CWE ids, so it is the one that joins to the CodeQL stage.
    "bandit_findings", "bandit_sev_high", "bandit_sev_medium", "bandit_sev_low",
    "bandit_conf_high", "bandit_conf_medium", "bandit_conf_low",
    "bandit_loc", "bandit_nosec", "bandit_skipped", "bandit_cwes", "bandit_tests",
    "bandit_errors",
    # radon and lizard both count cyclomatic complexity, and complexipy counts the same
    # cognitive complexity SonarQube reports. Keeping all three is the point: agreement
    # between independent implementations is what §2 promises and a single tool cannot
    # give.
    "radon_blocks", "radon_cc_total", "radon_cc_mean", "radon_cc_max",
    "radon_mi_mean", "radon_mi_min", "radon_errors",
    "lizard_functions", "lizard_nloc", "lizard_ccn_total", "lizard_ccn_mean",
    "lizard_ccn_max", "lizard_token_total", "lizard_params_mean", "lizard_warnings",
    "complexipy_functions", "complexipy_total", "complexipy_mean", "complexipy_max",
    # a tool that failed contributes no findings, which reads as an improvement rather
    # than as a failure; the row is kept and the failure named instead.
    "tool_errors", "versions",
])


def done_months(path, repo):
    """The measures CSV is the resume marker: this stage has no server to ask.

    Only rows that got as far as a full result count as done; an error row is retried,
    since it may have been a timeout or an evicted pod rather than the snapshot. A month
    with no commit counts as done too: it is settled, and a retried pod that rewrote it
    would leave the CSV with two rows for the same month.
    """
    if path == "-" or not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["month"] for r in csv.DictReader(f)
                if r["repo"] == repo and r["status"] in ("ok", "no commit")}


def sources(tree):
    """The measured files of the current checkout, as paths relative to the tree.

    git ls-files rather than a walk: it sees exactly the tracked files, so nothing left
    behind by a previous checkout can enter the measurement.

    The pathspecs carry :(glob) because git's default dialect reads a leading **/ as a
    literal directory level and drops every .py in the repository root; :(glob) is the
    wildmatch dialect the pattern is written in, and the one Sonar and CodeQL apply to
    the same string, so all three stages then agree on the file set.

    The exclusions go through the same pathspec rather than a second pass in Python:
    fnmatch has no ** and its * crosses /, so "**/test_*.py" there also swallows every
    file under any directory whose name starts with test_, which is 15 files in litestar
    and 11 in AzureTRE that Sonar and CodeQL both keep.

    Symlink entries (mode 120000) are dropped: a link to a .py file is the target counted
    a second time, CodeQL never follows one, and checkout() has already deleted them from
    the work tree, so the tools could not read them anyway.
    """
    r = subprocess.run(["git"] + git_opts() + ["-C", tree, "ls-files", "-s", "-z", "--",
                        *(f":(glob){g}" for g in INCLUDE),
                        *(f":(exclude,glob){g}" for g in EXCLUDE)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ls-files: {r.stderr.strip()[:200]}")
    return [entry.split("\t", 1)[1] for entry in r.stdout.split("\0")
            if entry and not entry.startswith("120000")]


def tool(cmd, cwd, timeout, ok=(0,)):
    """Run one tool; raise with its error tail unless it exited with an expected code.

    Some of these signal findings through a nonzero exit -- bandit 1, complexipy 1 above
    its threshold -- so a nonzero code is not by itself a failure and the accepted set is
    passed in per tool.
    """
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode not in ok:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        raise RuntimeError(f"{os.path.basename(cmd[0])}: exit {r.returncode}: "
                           + " | ".join(l.strip() for l in tail[-3:])[:300])
    return r.stdout


def chunks(files, limit=2000):
    """Split the file list so no command line approaches ARG_MAX on a large repo."""
    return [files[i:i + limit] for i in range(0, len(files), limit)] or [[]]


def bandit(bin_dir, tree, files, timeout, out, repo, month, config):
    """Security findings, their CWE ids, and one JSONL line per finding.

    -c pins the frozen profile for every repo, so a repo carrying its own .bandit or
    [tool.bandit] is measured on the same rule set as one carrying none.

    The per-finding rows are kept for bandit alone: its CWE ids are what make the counts
    joinable to the CodeQL stage, and the volume is a few findings per snapshot.
    """
    row = {"bandit_findings": 0, "bandit_loc": 0, "bandit_nosec": 0,
           "bandit_skipped": 0, "bandit_errors": 0}
    sev, conf, cwes, tests = {}, {}, {}, {}
    for part in chunks(files):
        data = json.loads(tool([f"{bin_dir}/bandit", "-c", config, "-f", "json", "-q",
                                *part], tree, timeout, ok=(0, 1)) or "{}")
        totals = data.get("metrics", {}).get("_totals", {})
        row["bandit_loc"] += int(totals.get("loc", 0) or 0)
        row["bandit_nosec"] += int(totals.get("nosec", 0) or 0)
        row["bandit_skipped"] += int(totals.get("skipped_tests", 0) or 0)
        row["bandit_errors"] += len(data.get("errors", []))
        for res in data.get("results", []):
            row["bandit_findings"] += 1
            s = res.get("issue_severity", "").lower()
            c = res.get("issue_confidence", "").lower()
            sev[s] = sev.get(s, 0) + 1
            conf[c] = conf.get(c, 0) + 1
            cwe = (res.get("issue_cwe") or {}).get("id")
            if cwe:
                cwes[f"CWE-{cwe}"] = cwes.get(f"CWE-{cwe}", 0) + 1
            tests[res.get("test_id", "")] = tests.get(res.get("test_id", ""), 0) + 1
            if out:
                out.write(json.dumps({
                    "repo": repo, "month": month, "test_id": res.get("test_id"),
                    "test_name": res.get("test_name"),
                    "cwe": f"CWE-{cwe}" if cwe else None,
                    "severity": s, "confidence": c,
                    "file": res.get("filename"), "line": res.get("line_number"),
                    "message": res.get("issue_text"),
                }) + "\n")
    if out:
        out.flush()
    for name in ("high", "medium", "low"):
        row[f"bandit_sev_{name}"] = sev.get(name, 0)
        row[f"bandit_conf_{name}"] = conf.get(name, 0)
    row["bandit_cwes"] = json.dumps(cwes, sort_keys=True)
    row["bandit_tests"] = json.dumps(tests, sort_keys=True)
    return row


def radon(bin_dir, tree, files, timeout, cwd):
    """Cyclomatic complexity, maintainability index and the raw line counts.

    Run from `cwd`, a directory outside the tree, against absolute paths. radon is the one
    tool of the five that reads its configuration from the current directory -- a [radon]
    section in radon.cfg or setup.cfg -- and it has no flag to stop it. Measured from
    inside a repo carrying `no_assert = True`, the same function comes out one point
    cheaper than it does from anywhere else, so the working directory has to be ours.

    A file radon cannot parse comes back as {"error": ...} under its own path instead of
    as a nonzero exit, so it contributes nothing to loc and nothing to the block list
    while still counting in py_files. Left uncounted that is a denominator that shrinks
    for a reason not visible anywhere in the row, so the paths are counted in
    radon_errors.
    """
    cc, mi, raw = [], [], {"loc": 0, "sloc": 0, "lloc": 0, "comments": 0, "blank": 0}
    bad = set()
    for part in chunks([os.path.join(tree, f) for f in files]):
        for path, block in json.loads(tool([f"{bin_dir}/radon", "cc", "-j", *part],
                                           cwd, timeout) or "{}").items():
            if isinstance(block, list):
                cc += [b["complexity"] for b in block]
            else:
                bad.add(path)
        for path, m in json.loads(tool([f"{bin_dir}/radon", "mi", "-j", *part],
                                       cwd, timeout) or "{}").items():
            if isinstance(m, dict) and "mi" in m:
                mi.append(m["mi"])
            else:
                bad.add(path)
        for path, counts in json.loads(tool([f"{bin_dir}/radon", "raw", "-j", *part],
                                            cwd, timeout) or "{}").items():
            if isinstance(counts, dict) and "loc" in counts:
                for k in raw:
                    raw[k] += counts.get(k, 0)
            else:
                bad.add(path)
    return {**raw, "radon_errors": len(bad),
            "radon_blocks": len(cc), "radon_cc_total": sum(cc),
            "radon_cc_mean": round(statistics.fmean(cc), 3) if cc else "",
            "radon_cc_max": max(cc, default=""),
            "radon_mi_mean": round(statistics.fmean(mi), 3) if mi else "",
            "radon_mi_min": round(min(mi), 3) if mi else ""}


def lizard(bin_dir, tree, files, timeout):
    """Per-function CCN, token count and parameter count from an independent parser.

    -i -1 makes the exit code unconditional, since lizard otherwise fails the run as soon
    as any function crosses its default CCN threshold.
    """
    ccn, nloc, tokens, params = [], 0, 0, []
    for part in chunks(files):
        out = tool([f"{bin_dir}/lizard", "-l", "python", "--no-gitignore", "--csv",
                    "-i", "-1", *part], tree, timeout)
        for line in csv.reader(out.splitlines()):
            if len(line) < 5:
                continue
            try:
                n, c, t, p = int(line[0]), int(line[1]), int(line[2]), int(line[3])
            except ValueError:
                continue
            nloc += n
            ccn.append(c)
            tokens += t
            params.append(p)
    return {"lizard_functions": len(ccn), "lizard_nloc": nloc,
            "lizard_ccn_total": sum(ccn),
            "lizard_ccn_mean": round(statistics.fmean(ccn), 3) if ccn else "",
            "lizard_ccn_max": max(ccn, default=""), "lizard_token_total": tokens,
            "lizard_params_mean": round(statistics.fmean(params), 3) if params else "",
            "lizard_warnings": sum(c > 15 for c in ccn)}


def complexipy(bin_dir, tree, files, timeout, work):
    """Cognitive complexity under the same definition SonarQube uses.

    This is the one metric measured twice by two unrelated implementations, so it is the
    closest thing the study has to a check on a single tool's heuristics.

    A result file that is missing or unreadable is raised rather than skipped: swallowed,
    the chunk contributes no functions and the row still reads as ok, which puts a
    complexity of zero into the outcome exactly where the measurement failed.
    """
    values = []
    for i, part in enumerate(chunks(files)):
        path = f"{work}/complexipy-{i}.json"
        with contextlib.suppress(OSError):
            os.remove(path)
        tool([f"{bin_dir}/complexipy", "-q", "--snapshot-ignore", "--output", path,
              "--output-format", "json", *part], tree, timeout, ok=(0, 1))
        try:
            with open(path, encoding="utf-8") as f:
                values += [e["complexity"] for e in json.load(f)]
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
    return {"complexipy_functions": len(values), "complexipy_total": sum(values),
            "complexipy_mean": round(statistics.fmean(values), 3) if values else "",
            "complexipy_max": max(values, default="")}


def snapshot(args, tree, row, results):
    """Measure one checked-out snapshot with all four tools at once.

    The tools are independent processes over the same read-only tree, so they are run
    concurrently rather than in sequence; the slowest of the four sets the snapshot's
    wall time instead of their sum. A tool that fails is recorded in tool_errors and the
    other three still contribute, since dropping the whole snapshot over one tool would
    lose three working measurements.
    """
    checkout(tree, row["sha"])
    files = sources(tree)
    started = time.time()
    if not files:
        return {"status": "ok", "py_files": 0, "seconds": 0.0,
                "versions": args.versions, "tool_errors": ""}
    # radon's working directory decides which configuration it reads, so it gets an empty
    # one of ours rather than the repo's
    neutral = f"{args.work}/neutral"
    os.makedirs(neutral, exist_ok=True)
    jobs = {
        "bandit": lambda: bandit(args.bin, tree, files, args.timeout, results,
                                 args.repo, row["month"], args.bandit_config),
        "radon": lambda: radon(args.bin, tree, files, args.timeout, neutral),
        "lizard": lambda: lizard(args.bin, tree, files, args.timeout),
        "complexipy": lambda: complexipy(args.bin, tree, files, args.timeout, args.work),
    }
    fields, errors = {}, {}
    with concurrent.futures.ThreadPoolExecutor(len(jobs)) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                fields.update(future.result())
            except (RuntimeError, OSError, ValueError, KeyError, TypeError,
                    subprocess.SubprocessError) as e:
                # the class first: TimeoutExpired renders as the whole command line and
                # says "timed out" only at the very end, past any truncation
                errors[name] = f"{type(e).__name__}: {e}"[:300]
    return {**fields, "py_files": len(files),
            "status": "ok" if not errors else "partial",
            "seconds": round(time.time() - started, 1),
            "tool_errors": json.dumps(errors, sort_keys=True) if errors else "",
            "versions": args.versions}


def run(args):
    tree = f"{args.work}/{args.repo.replace('/', '__')}"
    done = done_months(args.measures, args.repo)
    missing = [m for m in snapshot_commits.window_months(args.m0, args.pre_months,
                                                        args.post_months)
               if m not in done]
    log(f"{args.repo}: {len(done)} snapshots already measured, {len(missing)} of the window missing")
    if not missing:
        return
    shutil.rmtree(tree, ignore_errors=True)
    log(f"{args.repo}: cloning")
    clone(args.repo, tree, args.mirror)
    try:
        rows = snapshot_commits.snapshots(tree, args.m0, args.pre_months,
                                          args.post_months, args.ref)
        pending = [r for r in rows if r["month"] not in done]
        log(f"{args.repo}: m0={args.m0} {len(pending)} of {len(rows)} snapshots to measure")
        with sink(args.measures) as out, sink(args.results) as results:
            w = csv.DictWriter(out, COLUMNS, restval="", extrasaction="ignore")
            if fresh(args.measures):
                w.writeheader()
            for row in pending:
                row = {**row, "repo": args.repo}
                if not row["sha"]:  # month predates the first commit: recorded as a gap
                    w.writerow({**row, "status": "no commit"})
                    out.flush()
                    continue
                try:
                    row.update(snapshot(args, tree, row, results))
                except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                    row["status"] = f"error: {e}"[:300]
                w.writerow(row)
                out.flush()
                log(f"  {row['month']} {row['sha'][:8]} {row['status'][:120]}"
                    f" (files={row.get('py_files', '')}"
                    f" bandit={row.get('bandit_findings', '')}"
                    f" cx={row.get('complexipy_total', '')} {row.get('seconds', '')}s)")
    finally:
        if not args.keep:
            shutil.rmtree(tree, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", help="repository as owner/name")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-o", "--measures", default="-",
                   help="CSV the snapshot rows go to, '-' for stdout (default: -)")
    p.add_argument("-i", "--results", help="JSONL bandit findings go to, '-' for stdout")
    p.add_argument("-S", "--sample", help="take repo and m0 from row --index of this CSV "
                                          "instead of the positional arguments")
    p.add_argument("-I", "--index", type=int,
                   default=int(os.environ.get("JOB_COMPLETION_INDEX", -1)),
                   help="0-based row of --sample (default: $JOB_COMPLETION_INDEX)")
    p.add_argument("-d", "--work", default=".",
                   help="directory for the clone and scratch files (default: .)")
    p.add_argument("-b", "--bin", default=os.environ.get("LINT_BIN", "."),
                   help="directory holding the four tools (default: $LINT_BIN or .)")
    p.add_argument("-c", "--bandit-config", default=BANDIT_CONFIG,
                   help="frozen bandit profile (default: bandit.yaml next to this script)")
    p.add_argument("-V", "--versions", default=os.environ.get("LINT_VERSIONS", ""),
                   help="pinned versions, recorded in every row (default: $LINT_VERSIONS)")
    p.add_argument("-M", "--mirror", help="clone from this base URL instead of GitHub")
    p.add_argument("-r", "--ref", help="ref to walk (default: the clone's HEAD branch)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-t", "--timeout", type=int, default=1800,
                   help="seconds one tool may take on one snapshot (default: 1800)")
    p.add_argument("-k", "--keep", action="store_true", help="keep the clone afterwards")
    args = p.parse_args()
    if args.sample:
        if args.index < 0:
            sys.exit("--sample needs --index or $JOB_COMPLETION_INDEX")
        args.repo, args.m0 = sample_row(args.sample, args.index)
        if not args.repo:
            # a Job sized past the end of the sample: nothing to do is not a failure
            log(f"index {args.index} is past the end of {args.sample}")
            return
        log(f"index {args.index} -> {args.repo} m0={args.m0}")
    if not args.repo or not args.m0:
        p.print_help()
        sys.exit(0)

    try:
        run(args)
    except RuntimeError as e:
        sys.exit(f"{args.repo}: {e}")


if __name__ == "__main__":
    main()
