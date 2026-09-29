#!/usr/bin/env python3
"""The layer-2 interrupted time series refitted inside each tercile of total LLM-marker share."""
import argparse
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ITS = "y ~ time + post + timeafter"


def exposure(pattern):
    """Per repository-month, the share of commits carrying an LLM marker.

    Two forms are built. The instantaneous share is this month's flow; the cumulative share
    is the marker fraction of every commit observed up to this month, which is the one that
    matches the outcomes, since a whole-repository metric reflects accumulated code rather
    than the commits of a single month. Both are zero before the marker by construction:
    m0 is the first marker commit, so no earlier month can carry one.
    """
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
        "share": np.where(c > 0, 100 * m / c, 0.0),
        "dose": np.where(cc > 0, 100 * cm / cc, 0.0),
    })
    tot = e.groupby("repo")["dose"].last()
    e["dose_total"] = e["repo"].map(tot)
    e["tercile"] = e["repo"].map(pd.qcut(tot, 3, labels=["low", "mid", "high"]))
    return e, tot


def fit(d, formula, re_formula="~1"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = smf.mixedlm(formula, groups=d["repo"], re_formula=re_formula, data=d)
        return m.fit(reml=True, method="lbfgs", maxiter=2000)


def coefficient(f, term, logged):
    est, se, p = float(f.params[term]), float(f.bse[term]), float(f.pvalues[term])
    row = {"coef": est, "se": se, "lo": est - 1.96 * se, "hi": est + 1.96 * se, "p": p,
           "converged": int(bool(f.converged))}
    if logged:
        row["pct"] = (np.exp(est) - 1) * 100
        row["pct_lo"] = (np.exp(row["lo"]) - 1) * 100
        row["pct_hi"] = (np.exp(row["hi"]) - 1) * 100
    return row


def terciles(d):
    """The original interrupted time series refitted inside each dose tercile.

    Low-dose repositories sit in the same calendar months as high-dose ones and carry the
    same ecosystem-wide trend, but almost no marker activity, so the contrast between the
    tercile slopes is what separates the two.
    """
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
                c = coefficient(f, term, logged)
                row[label] = c["coef"]
                row[label + "_p"] = c["p"]
                if logged:
                    row[label + "_pct"] = c["pct"]
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-m", "--merged", default="data/12_merged", help="merged measurement directory")
    p.add_argument("-o", "--output", default="-", help="tercile ITS CSV, - for stdout")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    e, _ = exposure(args.merged)
    d = d.merge(e, on=["repo", "offset"], how="left")
    if d["dose"].isna().any():
        sys.exit("panel rows without exposure data")
    terciles(d).to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
