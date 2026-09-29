#!/usr/bin/env python3
"""Clone one repo and analyse each of its monthly snapshots with CodeQL into CSV + JSONL."""
import argparse
import contextlib
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time

import scripts.scan.snapshot_commits as snapshot_commits
from scripts.scan.scan_repo import EXCLUSIONS, INCLUSIONS, checkout, clone, log, sample_row

# Frozen analysis configuration: one bundle version and one query suite for every snapshot
# of every repo (proposal §4.4), so anything below is a sample-wide decision. The suite is
# the security one only -- CodeQL is here for the taint dimension, and its quality queries
# would duplicate SonarQube's smells under an incomparable rule set.
SUITE = "codeql/python-queries:codeql-suites/python-security-extended.qls"
# the same file scope as the SonarQube stage, so the two tools measure the same code.
# Taken from scan_repo.py rather than restated, since drifting scopes would silently make
# the per-KLOC figures incomparable.
PATHS = INCLUSIONS.split(",")
PATHS_IGNORE = EXCLUSIONS.split(",")

# Dependencies are deliberately not installed before extraction: a snapshot from two
# years ago often cannot be installed at all, and a per-snapshot difference in what
# resolved would move the finding counts for reasons unrelated to the code. The cost is
# recall -- flows through unmodelled third-party libraries are missed -- but it is missed
# uniformly across every snapshot of every repo, which is what the within-repo comparison
# needs.

# Severity bands of GitHub's own CVSS mapping for security-severity, so the counts mean
# the same thing they do in code scanning.
BANDS = [("critical", 9.0), ("high", 7.0), ("medium", 4.0), ("low", 0.0)]

COLUMNS = ([
    "repo", "month", "offset", "sha", "commit_date", "carried", "status",
    "db_seconds", "analyze_seconds",
    # denominators. Sonar's ncloc is a different notion of a counted line, so each tool
    # is normalised by its own denominator and the two are never mixed. loc is the code
    # the evaluator actually saw, baseline_loc the code in the source root: they are
    # counted differently on purpose, and a gap between them is itself a signal.
    "loc", "loc_user", "baseline_loc",
    # extraction health: a snapshot the extractor choked on yields a valid SARIF with
    # fewer findings, which would read as an improvement rather than as a failure.
    # eval_errors is the same hazard one stage later: a query that errored or timed out
    # simply contributes no findings.
    "py_files", "extracted_files", "extractor_diagnostics", "eval_errors",
    # the query pack version that produced the row, recorded rather than inferred from
    # the pinned bundle, so a suite drifting mid-run would be visible in the data.
    "pack_version",
    "results", "path_problems", "rules_fired",
] + [f"sev_{name}" for name, _ in BANDS] + ["cwes"])


def sink(path):
    """Open an output for appending; '-' is stdout and None a no-op, both as contexts."""
    if path == "-":
        return contextlib.nullcontext(sys.stdout)
    if not path:
        return contextlib.nullcontext(None)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", newline="", encoding="utf-8")


def fresh(path):
    """Whether a CSV sink still needs its header row."""
    return path == "-" or not os.path.exists(path) or os.path.getsize(path) == 0


def done_months(path, repo):
    """The measures CSV is the resume marker: this stage has no server to ask.

    Only rows that got as far as a result count are treated as done; an error row is
    retried, since it may have been a timeout or an evicted pod rather than the snapshot.
    """
    if path == "-" or not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["month"] for r in csv.DictReader(f)
                if r["repo"] == repo and r["status"] == "ok"}


def codeql(args, *argv, timeout=None, capture=True):
    """Run one codeql subcommand; raise with its last error lines if it fails."""
    r = subprocess.run([args.codeql, *argv], capture_output=capture, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        tail = [l for l in out if "error" in l.lower()] or out[-3:]
        raise RuntimeError(f"{argv[0]} {argv[1]}: exit {r.returncode}: "
                           + " | ".join(l.strip() for l in tail[:3])[:400])
    return r.stdout


def config_file(path):
    """The code-scanning config that carries the path filters into database creation.

    Filtering at extraction time rather than over the results keeps the database, its
    baseline LOC and the findings all on the same set of files.
    """
    lines = ["paths:"] + [f"  - '{p}'" for p in PATHS]
    lines += ["paths-ignore:"] + [f"  - '{p}'" for p in PATHS_IGNORE]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def create(args, tree, db, cfg):
    """Extract one snapshot into a database; returns the files the extractor took in.

    There is no --overwrite in the CLI (the database directory must not exist), so the
    caller removes it beforehand. --language is passed explicitly because omitting it
    makes the CLI ask the GitHub API what to analyse.
    """
    out = codeql(args, "database", "create", db, "--language=python",
                 "--build-mode=none", f"--source-root={tree}",
                 f"--codescanning-config={cfg}", f"--threads={args.threads}",
                 f"--ram={args.ram}", timeout=args.db_timeout)
    return out.count("Extracted file ")


def analyze(args, db, sarif):
    codeql(args, "database", "analyze", db, SUITE, "--format=sarif-latest",
           f"--output={sarif}", f"--threads={args.threads}", f"--ram={args.ram}",
           # snippets and the console summaries are bulk, not outcomes; --no-download
           # keeps the run on the bundle's own precompiled packs and off the network
           "--no-sarif-add-snippets", "--no-download",
           "--no-print-diagnostics-summary", "--no-print-metrics-summary",
           timeout=args.analyze_timeout)


def baseline_loc(args, db):
    """Baseline lines of code of the source root: 'Counted a baseline of N ... python.'"""
    for line in codeql(args, "database", "print-baseline", db).splitlines():
        if "python" in line.lower():
            found = re.findall(r"\d+", line)
            if found:
                return found[0]
    return ""


def extractor_diagnostics(args, db, path):
    """Diagnostics the extractor itself emitted, i.e. what went wrong on this snapshot.

    A clean run still emits a handful of entries, but they are all the CLI's own
    telemetry (cli/platform, cli/build-mode and the like) and carry no severity, so only
    the non-cli sources say anything about the code. They are counted rather than kept:
    a nonzero count marks a snapshot whose finding counts may be depressed by failed
    extraction and is a reason to inspect or drop it.
    """
    codeql(args, "database", "export-diagnostics", db, "--format=raw",
           f"--output={path}")
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError):
        return ""
    return sum(1 for d in items
               if not str(d.get("source", {}).get("id", "")).startswith("cli/"))


def cwes(rule):
    """CWE ids of a rule, from its `external/cwe/cwe-089` tags."""
    tags = rule.get("properties", {}).get("tags", [])
    return sorted({f"CWE-{int(m.group(1))}"
                   for t in tags if (m := re.fullmatch(r"external/cwe/cwe-(\d+)", t))})


def band(severity):
    for name, floor in BANDS:
        if severity >= floor:
            return name
    return "low"


def step_location(step):
    """One threadFlow step as file, line and its message."""
    loc = step.get("location", {})
    where = loc.get("physicalLocation", {})
    return {"file": where.get("artifactLocation", {}).get("uri"),
            "line": where.get("region", {}).get("startLine"),
            "message": loc.get("message", {}).get("text")}


def summarise(sarif, repo, month, out):
    """Fold one SARIF run into a measures row, writing one JSONL line per result.

    Rule metadata (CWE tags, security-severity, precision) lives once per rule in
    tool.driver.rules, so the CWE grouping RQ2 needs is a join inside the file. The
    per-CWE counts are not CSV columns -- the set of CWEs is not known ahead of the run --
    they are a JSON object in one column, with the per-result rows keeping the detail.
    A finding is counted under every CWE its rule carries, so those counts sum above the
    result count; sev_* is the partition that sums to it.
    """
    with open(sarif, encoding="utf-8") as f:
        run = json.load(f)["runs"][0]
    rules = {r["id"]: r for r in run["tool"]["driver"].get("rules", [])}
    packs = {e.get("name"): e.get("semanticVersion") or e.get("version")
             for e in run["tool"].get("extensions", [])}
    # the suite carries the two summary metric queries, so the line counts come out of
    # the same run as the findings and need no extra command
    metrics = {m.get("ruleId"): m.get("value")
               for m in run.get("properties", {}).get("metricResults", [])}
    row = {"results": 0, "path_problems": 0, "cwes": {},
           "loc": metrics.get("py/summary/lines-of-code", ""),
           "loc_user": metrics.get("py/summary/lines-of-user-code", ""),
           "eval_errors": sum(n.get("level") == "error"
                              for i in run.get("invocations", [])
                              for n in i.get("toolExecutionNotifications", [])),
           "pack_version": packs.get(SUITE.split(":")[0], ""),
           **{f"sev_{n}": 0 for n, _ in BANDS}}
    fired = set()
    for res in run.get("results", []):
        rule = rules.get(res.get("ruleId"), {})
        props = rule.get("properties", {})
        try:
            severity = float(props.get("security-severity", ""))
        except ValueError:
            severity = 0.0
        flows = res.get("codeFlows", [])
        # the longest source-to-sink path is the taint evidence; keeping its steps means a
        # finding can be checked by hand later without re-running the analysis
        path = max((t.get("locations", []) for fl in flows
                    for t in fl.get("threadFlows", [])), key=len, default=[])
        ids = cwes(rule)
        where = (res.get("locations") or [{}])[0].get("physicalLocation", {})
        row["results"] += 1
        row["path_problems"] += bool(flows)
        row[f"sev_{band(severity)}"] += 1
        fired.add(res.get("ruleId"))
        for c in ids:
            row["cwes"][c] = row["cwes"].get(c, 0) + 1
        if out:
            out.write(json.dumps({
                "repo": repo, "month": month, "rule": res.get("ruleId"),
                "cwes": ids, "security_severity": props.get("security-severity"),
                "precision": props.get("precision"),
                "problem_severity": props.get("problem.severity"),
                "kind": "path-problem" if flows else "problem",
                "file": where.get("artifactLocation", {}).get("uri"),
                "line": where.get("region", {}).get("startLine"),
                "message": res.get("message", {}).get("text"),
                # the alert's identity as code scanning computes it: the same hash in a
                # later month is the same alert surviving, not a new one
                "fingerprint": res.get("partialFingerprints", {})
                                  .get("primaryLocationLineHash"),
                "flow_length": len(path),
                "flow": [step_location(s) for s in path],
            }) + "\n")
    if out:
        out.flush()
    row["cwes"] = json.dumps(row["cwes"], sort_keys=True)
    row["rules_fired"] = len(fired)
    return row


def py_files(tree):
    """Python files in the work tree, as scope context next to the baseline LOC."""
    r = subprocess.run(["git", "-C", tree, "ls-files", "*.py"],
                       capture_output=True, text=True)
    return len(r.stdout.splitlines()) if r.returncode == 0 else ""


def snapshot(args, tree, row, results):
    """Build and analyse the database of one snapshot; returns the measures fields."""
    db = f"{args.work}/db"
    sarif = f"{args.work}/snapshot.sarif"
    shutil.rmtree(db, ignore_errors=True)
    try:
        checkout(tree, row["sha"])
        started = time.time()
        extracted = create(args, tree, db, config_file(f"{args.work}/codescanning.yml"))
        db_seconds = round(time.time() - started, 1)
        started = time.time()
        analyze(args, db, sarif)
        fields = summarise(sarif, args.repo, row["month"], results)
        return {**fields, "status": "ok", "db_seconds": db_seconds,
                "analyze_seconds": round(time.time() - started, 1),
                "baseline_loc": baseline_loc(args, db), "py_files": py_files(tree),
                "extracted_files": extracted,
                "extractor_diagnostics": extractor_diagnostics(
                    args, db, f"{args.work}/diagnostics.json")}
    finally:
        # the database is several GiB and the pod's emptyDir holds one snapshot's worth
        shutil.rmtree(db, ignore_errors=True)
        if not args.keep_sarif:
            # the reduced JSONL rows are the artifact; the raw SARIF is tens of MiB a
            # snapshot and is not kept
            with contextlib.suppress(OSError):
                os.remove(sarif)


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
        log(f"{args.repo}: m0={args.m0} {len(pending)} of {len(rows)} snapshots to analyse")
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
                    f" (results={row.get('results', '')} loc={row.get('loc', '')}"
                    f" {row.get('db_seconds', '')}+{row.get('analyze_seconds', '')}s)")
    finally:
        if not args.keep:
            shutil.rmtree(tree, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", help="repository as owner/name")
    p.add_argument("-m", "--m0", help="integration month, YYYY-MM")
    p.add_argument("-o", "--measures", default="-",
                   help="CSV the snapshot rows go to, '-' for stdout (default: -)")
    p.add_argument("-i", "--results", help="JSONL per-result rows go to, '-' for stdout")
    p.add_argument("-S", "--sample", help="take repo and m0 from row --index of this CSV "
                                          "instead of the positional arguments")
    p.add_argument("-I", "--index", type=int,
                   default=int(os.environ.get("JOB_COMPLETION_INDEX", -1)),
                   help="0-based row of --sample (default: $JOB_COMPLETION_INDEX)")
    p.add_argument("-d", "--work", default=".",
                   help="directory for the clone and databases (default: .)")
    p.add_argument("-c", "--codeql", default=os.environ.get("CODEQL", "codeql"),
                   help="codeql CLI to run (default: $CODEQL or codeql)")
    p.add_argument("-M", "--mirror", help="clone from this base URL instead of GitHub")
    p.add_argument("-r", "--ref", help="ref to walk (default: the clone's HEAD branch)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length in months (default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-j", "--threads", type=int, default=4,
                   help="threads for both codeql commands (default: 4)")
    p.add_argument("-R", "--ram", type=int, default=6000,
                   help="MB of RAM codeql may use (default: 6000)")
    p.add_argument("-k", "--keep", action="store_true", help="keep the clone afterwards")
    p.add_argument("--keep-sarif", action="store_true",
                   help="keep each snapshot's raw SARIF instead of only the JSONL rows")
    p.add_argument("--db-timeout", type=int, default=3600,
                   help="seconds one database creation may take (default: 3600)")
    p.add_argument("--analyze-timeout", type=int, default=5400,
                   help="seconds one analysis may take (default: 5400)")
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
