#!/usr/bin/env python3
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

# Security suite only: the quality queries would duplicate SonarQube under another rule set.
SUITE = "codeql/python-queries:codeql-suites/python-security-extended.qls"
# Scope imported from scan_repo.py, so both stages measure the same files.
PATHS = INCLUSIONS.split(",")
PATHS_IGNORE = EXCLUSIONS.split(",")

# Dependencies are not installed: old snapshots often cannot be. Flows through unmodelled
# libraries are missed, uniformly across snapshots.

# GitHub code-scanning bands for security-severity.
BANDS = [("critical", 9.0), ("high", 7.0), ("medium", 4.0), ("low", 0.0)]

COLUMNS = ([
    "repo", "month", "offset", "sha", "commit_date", "carried", "status",
    "db_seconds", "analyze_seconds",
    # Denominators: loc is what the evaluator saw, baseline_loc the source root.
    "loc", "loc_user", "baseline_loc",
    # Failed extraction or queries would read as fewer findings; both are counted.
    "py_files", "extracted_files", "extractor_diagnostics", "eval_errors",
    # Query pack version per row, so drift within a run is visible.
    "pack_version",
    "results", "path_problems", "rules_fired",
] + [f"sev_{name}" for name, _ in BANDS] + ["cwes"])


def sink(path):
    if path == "-":
        return contextlib.nullcontext(sys.stdout)
    if not path:
        return contextlib.nullcontext(None)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "a", newline="", encoding="utf-8")


def fresh(path):
    return path == "-" or not os.path.exists(path) or os.path.getsize(path) == 0


# The measures CSV is the resume state; error rows are retried.
def done_months(path, repo):
    if path == "-" or not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["month"] for r in csv.DictReader(f)
                if r["repo"] == repo and r["status"] == "ok"}


def codeql(args, *argv, timeout=None, capture=True):
    r = subprocess.run([args.codeql, *argv], capture_output=capture, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        tail = [l for l in out if "error" in l.lower()] or out[-3:]
        raise RuntimeError(f"{argv[0]} {argv[1]}: exit {r.returncode}: "
                           + " | ".join(l.strip() for l in tail[:3])[:400])
    return r.stdout


# Filters apply at extraction, so database, LOC and findings share one file set.
def config_file(path):
    lines = ["paths:"] + [f"  - '{p}'" for p in PATHS]
    lines += ["paths-ignore:"] + [f"  - '{p}'" for p in PATHS_IGNORE]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# The database directory must not exist; --language avoids a GitHub API lookup.
def create(args, tree, db, cfg):
    out = codeql(args, "database", "create", db, "--language=python",
                 "--build-mode=none", f"--source-root={tree}",
                 f"--codescanning-config={cfg}", f"--threads={args.threads}",
                 f"--ram={args.ram}", timeout=args.db_timeout)
    return out.count("Extracted file ")


def analyze(args, db, sarif):
    codeql(args, "database", "analyze", db, SUITE, "--format=sarif-latest",
           f"--output={sarif}", f"--threads={args.threads}", f"--ram={args.ram}",
           # No snippets or summaries; --no-download keeps to the bundled packs, offline.
           "--no-sarif-add-snippets", "--no-download",
           "--no-print-diagnostics-summary", "--no-print-metrics-summary",
           timeout=args.analyze_timeout)


def baseline_loc(args, db):
    for line in codeql(args, "database", "print-baseline", db).splitlines():
        if "python" in line.lower():
            found = re.findall(r"\d+", line)
            if found:
                return found[0]
    return ""


# cli/* entries are CLI telemetry; only the others signal extraction problems.
def extractor_diagnostics(args, db, path):
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
    tags = rule.get("properties", {}).get("tags", [])
    return sorted({f"CWE-{int(m.group(1))}"
                   for t in tags if (m := re.fullmatch(r"external/cwe/cwe-(\d+)", t))})


def band(severity):
    for name, floor in BANDS:
        if severity >= floor:
            return name
    return "low"


def step_location(step):
    loc = step.get("location", {})
    where = loc.get("physicalLocation", {})
    return {"file": where.get("artifactLocation", {}).get("uri"),
            "line": where.get("region", {}).get("startLine"),
            "message": loc.get("message", {}).get("text")}


# A finding counts under every CWE of its rule; only sev_* sums to the result count.
def summarise(sarif, repo, month, out):
    with open(sarif, encoding="utf-8") as f:
        run = json.load(f)["runs"][0]
    rules = {r["id"]: r for r in run["tool"]["driver"].get("rules", [])}
    packs = {e.get("name"): e.get("semanticVersion") or e.get("version")
             for e in run["tool"].get("extensions", [])}
    # Line counts come from the suite's summary queries, in the same run.
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
        # The longest source-to-sink path is kept for manual checks.
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
                # Code-scanning fingerprint: equal across months for a surviving alert.
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
    r = subprocess.run(["git", "-C", tree, "ls-files", "*.py"],
                       capture_output=True, text=True)
    return len(r.stdout.splitlines()) if r.returncode == 0 else ""


def snapshot(args, tree, row, results):
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
        # The database is several GiB; the emptyDir holds one snapshot.
        shutil.rmtree(db, ignore_errors=True)
        if not args.keep_sarif:
            # Raw SARIF is not kept, only the reduced JSONL.
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
    p = argparse.ArgumentParser(
        description="Clone one repo and analyse each of its monthly snapshots with CodeQL into CSV + JSONL.")
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
            # Job index past the end of the sample: nothing to do.
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
