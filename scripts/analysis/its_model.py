#!/usr/bin/env python3
import argparse
import sys

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multitest import multipletests

TERMS = ["time", "post", "timeafter"]
COVARIATES = ["log_act_commits", "log_act_authors", "log_act_scope_insertions"]


# Months of one repository are autocorrelated, so SEs are clustered by repository.
def fit(d, formula, extra=" + C(repo)"):
    return smf.ols(formula + extra, data=d).fit(
        cov_type="cluster", cov_kwds={"groups": d["repo"]})


def lag1(fit, d):
    e = d.loc[fit.resid.index, ["repo", "time"]].assign(e=fit.resid).sort_values(["repo", "time"])
    e["prev"] = e.groupby("repo")["e"].shift(1)
    return float(e["e"].corr(e["prev"]))


def row(d, covariates, slope):
    formula = "y ~ " + " + ".join(TERMS + (COVARIATES if covariates else []))
    fe = fit(d, formula)
    out = {"n": int(fe.nobs), "repos": d["repo"].nunique()}
    cov = fe.cov_params()
    for t, key in zip(TERMS, ["b1", "b2", "b3"]):
        # Clustered covariance is rank-deficient in the dummy block; SEs are read per term.
        est, se = fe.params[t], float(np.sqrt(cov.loc[t, t]))
        out[key] = est
        out[key + "_se"] = se
        out[key + "_lo"] = est - 1.96 * se
        out[key + "_hi"] = est + 1.96 * se
        out[key + "_p"] = float(2 * stats.norm.sf(abs(est / se)))
    # Lag-1 residual autocorrelation, the reason for clustered SEs.
    out["resid_lag1"] = lag1(fe, d)
    if slope:
        # A time slope per repository replaces the common time term.
        fs = fit(d, formula.replace("time + ", "", 1), " + C(repo) + C(repo):time")
        for t, key in (("post", "b2"), ("timeafter", "b3")):
            est, se = fs.params[t], fs.bse[t]
            out["rs_" + key] = est
            out["rs_" + key + "_lo"] = est - 1.96 * se
            out["rs_" + key + "_hi"] = est + 1.96 * se
            out["rs_" + key + "_p"] = fs.pvalues[t]
    return out


def main():
    p = argparse.ArgumentParser(
        description="Layer 2: interrupted time-series fit per outcome, repository fixed effects with clustered SEs.")
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-o", "--output", default="-", help="output CSV, - for stdout")
    p.add_argument("-a", "--covariates", action="store_true",
                   help="adjust for logged commit, author and in-scope insertion counts")
    p.add_argument("-r", "--repo-slope", action="store_true",
                   help="also refit with a separate time slope per repository")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    rows = []
    for name, g in d.groupby("outcome", sort=False):
        r = row(g, args.covariates, args.repo_slope)
        r.update(outcome=name, rq=g["rq"].iloc[0], tool=g["tool"].iloc[0],
                 logged=int(g["logged"].iloc[0]))
        rows.append(r)
    t = pd.DataFrame(rows)
    for key in ["b2", "b3"] + (["rs_b2", "rs_b3"] if args.repo_slope else []):
        t[key + "_p_holm"] = multipletests(t[key + "_p"], method="holm")[1]
        # Logged outcomes: coefficient as percent change.
        t[key + "_pct"] = np.where(t["logged"] == 1, (np.exp(t[key]) - 1) * 100, np.nan)
    lead = ["outcome", "rq", "tool", "logged", "n", "repos"]
    t = t[lead + [c for c in t.columns if c not in lead]]
    t.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.output != "-":
        cols = ["outcome", "b1", "b1_p", "b2", "b2_pct", "b2_p", "b2_p_holm",
                "b3", "b3_pct", "b3_p", "b3_p_holm", "resid_lag1"]
        if args.repo_slope:
            cols = ["outcome", "b2", "rs_b2", "rs_b2_p_holm", "b3", "rs_b3", "rs_b3_p_holm"]
        print(t[cols].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
