#!/usr/bin/env python3
"""Build the tidy event-time panel of normalised outcomes from the merged measures."""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

# name -> (research question, dimension, tool, numerator column(s), denominator, log1p)
# The transform is fixed here rather than recomputed per subsample: log1p for every
# outcome whose raw skew exceeds 1 over the full panel, identity otherwise.
OUTCOMES = {
    "smells_per_kloc":          ("RQ1", "code smells",   "sonar",     "sonar_code_smells",          "sonar",  True),
    "violations_per_kloc":      ("RQ1", "style",         "sonar",     "sonar_violations",           "sonar",  True),
    "cognitive_per_kloc":       ("RQ1", "cognitive",     "sonar",     "sonar_cognitive_complexity", "sonar",  False),
    "cognitive_per_function":   ("RQ1", "cognitive",     "sonar",     "sonar_cognitive_complexity", "func",   True),
    "complexipy_per_function":  ("RQ1", "cognitive",     "complexipy", "lint_complexipy_mean",      None,     True),
    "cyclomatic_radon":         ("RQ1", "cyclomatic",    "radon",     "lint_radon_cc_mean",         None,     True),
    "cyclomatic_lizard":        ("RQ1", "cyclomatic",    "lizard",    "lint_lizard_ccn_mean",       None,     True),
    "maintainability_radon":    ("RQ1", "maintainability", "radon",   "lint_radon_mi_mean",         None,     False),
    "lizard_warnings_per_kloc": ("RQ1", "style",         "lizard",    "lint_lizard_warnings",       "lizard", True),
    "duplication_pct":          ("RQ1", "duplication",   "sonar",     "sonar_duplicated_lines_density", None, True),
    "debt_ratio_pct":           ("RQ1", "technical debt", "sonar",    "sonar_sqale_debt_ratio",     None,     True),
    "comment_density_pct":      ("RQ1", "comments",      "sonar",     "sonar_comment_lines_density", None,    True),
    "bugs_per_kloc":            ("RQ1", "reliability",   "sonar",     "sonar_bugs",                 "sonar",  True),
    # size composition, carried as a control: longer functions raise every per-function
    # complexity metric on their own, so the granularity shift has to be visible next to them
    "functions_per_kloc":       ("RQ1", "granularity",   "sonar",     "sonar_functions",            "sonar",  True),
    "loc_per_function":         ("RQ1", "granularity",   "lizard",    "lint_lizard_nloc",           "lizfun", True),
    "sonar_vuln_per_kloc":      ("RQ2", "vulnerabilities", "sonar",   "sonar_vulnerabilities",      "sonar",  True),
    "bandit_per_kloc":          ("RQ2", "security",      "bandit",    "lint_bandit_findings",       "bandit", True),
    "bandit_high_per_kloc":     ("RQ2", "security",      "bandit",    "lint_bandit_sev_high",       "bandit", True),
    "codeql_per_kloc":          ("RQ2", "taint",         "codeql",    "codeql_results",             "codeql", True),
    "codeql_high_per_kloc":     ("RQ2", "taint",         "codeql",    ("codeql_sev_critical", "codeql_sev_high"), "codeql", True),
    "cwe_distinct":             ("RQ2", "CWE coverage",  "codeql+bandit", ("codeql_cwes", "lint_bandit_cwes"), "cwe", True),
}
COVARIATES = ["act_commits", "act_authors", "act_scope_insertions"]
DENOMINATORS = {
    "sonar":  lambda d: d["sonar_ncloc"] / 1000,
    "bandit": lambda d: d["lint_bandit_loc"] / 1000,
    "codeql": lambda d: d["codeql_loc_user"] / 1000,
    "lizard": lambda d: d["lint_lizard_nloc"] / 1000,
    "func":   lambda d: d["sonar_functions"],
    "lizfun": lambda d: d["lint_lizard_functions"],
}


def read_merged(directory):
    files = sorted(glob.glob(os.path.join(directory, "*.csv")))
    if not files:
        sys.exit(f"no CSV files in {directory}")
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def distinct_cwes(df):
    """Count of CWE identifiers named by either SAST tool in a snapshot."""
    import json

    def count(row):
        keys = set()
        for col in ("codeql_cwes", "lint_bandit_cwes"):
            v = row[col]
            if isinstance(v, str) and v.strip():
                keys |= set(json.loads(v))
        return float(len(keys))

    seen = df[["codeql_cwes", "lint_bandit_cwes"]].notna().any(axis=1)
    return df.apply(count, axis=1).where(seen)


def value(df, spec):
    """One outcome column, NaN wherever its inputs or its denominator are unusable."""
    _, _, _, num, den, _ = spec
    if den == "cwe":
        return distinct_cwes(df)
    if isinstance(num, tuple):
        raw = df[list(num)].sum(axis=1).where(df[list(num)].notna().all(axis=1))
    else:
        raw = df[num]
    if den is None:
        return raw
    d = DENOMINATORS[den](df)
    return raw / d.where(d > 0)


def panel(df):
    """Long-format panel: one row per repository, event month and outcome."""
    design = pd.DataFrame({
        "repo": df["repo"],
        "month": df["month"],
        "offset": df["offset"],
        "period": df["period"],
        "carried": (df["carried"].astype(str) == "1").astype(int),
        # centred on m0: shifting the origin leaves every slope and the Post dummy
        # untouched, but a random time slope only conditions well around zero
        "time": df["offset"],
        "post": (df["offset"] >= 0).astype(int),
        "timeafter": df["offset"].clip(lower=0),
        "kloc": df["sonar_ncloc"] / 1000,
    })
    for c in COVARIATES:
        design["log_" + c] = np.log1p(df[c])
    frames = []
    for name, spec in OUTCOMES.items():
        v = value(df, spec)
        f = design.copy()
        f["outcome"], f["rq"], f["dimension"], f["tool"] = name, spec[0], spec[1], spec[2]
        f["value"] = v
        f["y"] = np.log1p(v) if spec[5] else v
        f["logged"] = int(spec[5])
        frames.append(f.dropna(subset=["value"]))
    return pd.concat(frames, ignore_index=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-m", "--merged", default="data/12_merged", help="merged measures directory")
    p.add_argument("-o", "--output", default="-", help="output CSV, - for stdout")
    p.add_argument("-c", "--drop-carried", action="store_true",
                   help="drop snapshots that repeat the previous month's commit")
    p.add_argument("-x", "--exclude", default="", help="comma-separated repositories to drop")
    p.add_argument("-z", "--drop-m0", action="store_true", help="drop the integration month itself")
    args = p.parse_args()
    df = read_merged(args.merged)
    out = panel(df)
    if args.drop_carried:
        out = out[out["carried"] == 0]
    if args.drop_m0:
        out = out[out["offset"] != 0]
    drop = {r.strip() for r in args.exclude.split(",") if r.strip()}
    if drop:
        out = out[~out["repo"].isin(drop)]
    out.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.output != "-":
        print(f"{args.output}  {len(out)} rows, {out['repo'].nunique()} repositories, "
              f"{out['outcome'].nunique()} outcomes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
