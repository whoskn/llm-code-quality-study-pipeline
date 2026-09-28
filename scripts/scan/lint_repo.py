#!/usr/bin/env python3
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

# One file list for all four tools, computed in sources(). No tool reads configuration
# from the measured tree: lizard gets --no-gitignore, radon runs outside it, bandit gets -c.
BANDIT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bandit.yaml")

INCLUDE = INCLUSIONS.split(",")
EXCLUDE = EXCLUSIONS.split(",")

COLUMNS = ([
    "repo", "month", "offset", "sha", "commit_date", "carried", "status", "seconds",
    # Denominators from radon's raw counts; each stage normalises by its own.
    "py_files", "loc", "sloc", "lloc", "comments", "blank",
    "bandit_findings", "bandit_sev_high", "bandit_sev_medium", "bandit_sev_low",
    "bandit_conf_high", "bandit_conf_medium", "bandit_conf_low",
    "bandit_loc", "bandit_nosec", "bandit_skipped", "bandit_cwes", "bandit_tests",
    "bandit_errors",
    "radon_blocks", "radon_cc_total", "radon_cc_mean", "radon_cc_max",
    "radon_mi_mean", "radon_mi_min", "radon_errors",
    "lizard_functions", "lizard_nloc", "lizard_ccn_total", "lizard_ccn_mean",
    "lizard_ccn_max", "lizard_token_total", "lizard_params_mean", "lizard_warnings",
    "complexipy_functions", "complexipy_total", "complexipy_mean", "complexipy_max",
    # A failed tool would read as zero findings; the failure is named instead.
    "tool_errors", "versions",
])


# The measures CSV is the resume state; error rows are retried, empty months not.
def done_months(path, repo):
    if path == "-" or not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["month"] for r in csv.DictReader(f)
                if r["repo"] == repo and r["status"] in ("ok", "no commit")}


# :(glob) is the wildmatch dialect SonarQube and CodeQL use; symlinks are dropped.
def sources(tree):
    r = subprocess.run(["git"] + git_opts() + ["-C", tree, "ls-files", "-s", "-z", "--",
                        *(f":(glob){g}" for g in INCLUDE),
                        *(f":(exclude,glob){g}" for g in EXCLUDE)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ls-files: {r.stderr.strip()[:200]}")
    return [entry.split("\t", 1)[1] for entry in r.stdout.split("\0")
            if entry and not entry.startswith("120000")]


# bandit and complexipy report findings with exit code 1.
def tool(cmd, cwd, timeout, ok=(0,)):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode not in ok:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        raise RuntimeError(f"{os.path.basename(cmd[0])}: exit {r.returncode}: "
                           + " | ".join(l.strip() for l in tail[-3:])[:300])
    return r.stdout


def chunks(files, limit=2000):
    return [files[i:i + limit] for i in range(0, len(files), limit)] or [[]]


# -c pins one profile; a repository's own bandit config is ignored.
def bandit(bin_dir, tree, files, timeout, out, repo, month, config):
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


# Run outside the tree: radon reads radon.cfg and setup.cfg from the working directory.
def radon(bin_dir, tree, files, timeout, cwd):
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


# -i -1: functions above the CCN threshold do not fail the run.
def lizard(bin_dir, tree, files, timeout):
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


# A missing result file raises: skipping it would record a complexity of 0.
def complexipy(bin_dir, tree, files, timeout, work):
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


# The tools run concurrently; a failing tool is recorded, the others still count.
def snapshot(args, tree, row, results):
    checkout(tree, row["sha"])
    files = sources(tree)
    started = time.time()
    if not files:
        return {"status": "ok", "py_files": 0, "seconds": 0.0,
                "versions": args.versions, "tool_errors": ""}
    # Empty working directory for radon, which reads its config from there.
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
                # Class first: a TimeoutExpired message says "timed out" only past truncation.
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
    p = argparse.ArgumentParser(
        description="Clone one repo and measure each monthly snapshot with bandit, radon, lizard and complexipy.")
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
