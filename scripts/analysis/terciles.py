#!/usr/bin/env python3
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ITS = "y ~ time + post + timeafter"


# Cumulative marker share; its last value per repository sets the tercile.
def exposure(pattern):
    files = sorted(glob.glob(os.path.join(pattern, "*.csv")))
    if not files:
        sys.exit(f"no merged CSVs under {pattern}")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d = d.sort_values(["repo", "offset"]).reset_index(drop=True)
    m = pd.to_numeric(d["act_marker_commits"], errors="coerce").fillna(0.0)
    c = pd.to_numeric(d["act_commits"], errors="coerce").fillna(0.0)
    cm, cc = m.groupby(d["repo"]).cumsum(), c.groupby(d["repo"]).cumsum()
    e = pd.DataFrame({
        "repo": d["repo"], "offset": d["offset"],
        "dose": np.where(cc > 0, 100 * cm / cc, 0.0),
    })
    tot = e.groupby("repo")["dose"].last()
    e["dose_total"] = e["repo"].map(tot)
    e["tercile"] = e["repo"].map(pd.qcut(tot, 3, labels=["low", "mid", "high"]))
    return e


def fit(d, formula):
    return smf.ols(formula + " + C(repo)", data=d).fit(
        cov_type="cluster", cov_kwds={"groups": d["repo"]})


def terciles(d):
    rows = []
    for name, g in d.groupby("outcome", sort=False):
        logged = bool(g["logged"].iloc[0])
        for t in ("low", "mid", "high"):
            s = g[g["tercile"] == t]
            f = fit(s, ITS)
            row = {"outcome": name, "rq": g["rq"].iloc[0], "tercile": t,
                   "repos": s["repo"].nunique(), "logged": int(logged),
                   "dose_median": float(s["dose_total"].median())}
            for term, label in (("post", "b2"), ("timeafter", "b3")):
                row[label] = float(f.params[term])
                row[label + "_p"] = float(f.pvalues[term])
                if logged:
                    row[label + "_pct"] = (np.exp(row[label]) - 1) * 100
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    p = argparse.ArgumentParser(
        description="The layer-2 interrupted time series refitted inside each tercile of total LLM-marker share.")
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-m", "--merged", default="data/12_merged", help="merged measurement directory")
    p.add_argument("-o", "--output", default="-", help="tercile ITS CSV, - for stdout")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    d = d.merge(exposure(args.merged), on=["repo", "offset"], how="left")
    if d["dose"].isna().any():
        sys.exit("panel rows without exposure data")
    terciles(d).to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
