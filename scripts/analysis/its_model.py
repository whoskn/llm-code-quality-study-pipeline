#!/usr/bin/env python3
"""Layer 2: mixed-effects interrupted time-series fit per outcome."""
import argparse
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multitest import multipletests

TERMS = ["time", "post", "timeafter"]
COVARIATES = ["log_act_commits", "log_act_authors", "log_act_scope_insertions"]


def fit(d, formula, re_formula):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = smf.mixedlm(formula, groups=d["repo"], re_formula=re_formula, data=d)
        return m.fit(reml=True, method="lbfgs", maxiter=2000)


def row(d, covariates, slope):
    """One outcome's fixed effects, its variance components and its random-slope check."""
    formula = "y ~ " + " + ".join(TERMS + (COVARIATES if covariates else []))
    base = fit(d, formula, "~1")
    out = {"n": int(base.nobs), "repos": d["repo"].nunique(),
           "converged": int(bool(base.converged))}
    for t in ["Intercept"] + TERMS:
        est, se, p = base.params[t], base.bse[t], base.pvalues[t]
        key = {"Intercept": "b0", "time": "b1", "post": "b2", "timeafter": "b3"}[t]
        out[key] = est
        out[key + "_se"] = se
        out[key + "_lo"] = est - 1.96 * se
        out[key + "_hi"] = est + 1.96 * se
        out[key + "_p"] = p
    gv = float(np.atleast_2d(base.cov_re)[0, 0])
    out["var_repo"], out["var_resid"] = gv, float(base.scale)
    out["icc"] = gv / (gv + float(base.scale))
    if slope:
        # REML likelihoods are comparable here because the fixed effects are identical;
        # statsmodels reports no AIC under REML, so the LRT is the whole comparison
        rs = fit(d, formula, "~1+time")
        out["slope_converged"] = int(bool(rs.converged))
        out["slope_lrt"] = 2 * (rs.llf - base.llf)
        out["slope_lrt_p"] = float(stats.chi2.sf(max(out["slope_lrt"], 0.0), 2))
        for t, key in (("post", "b2"), ("timeafter", "b3")):
            est, se = rs.params[t], rs.bse[t]
            out["rs_" + key] = est
            out["rs_" + key + "_lo"] = est - 1.96 * se
            out["rs_" + key + "_hi"] = est + 1.96 * se
            out["rs_" + key + "_p"] = rs.pvalues[t]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-o", "--output", default="-", help="output CSV, - for stdout")
    p.add_argument("-a", "--covariates", action="store_true",
                   help="adjust for logged commit, author and in-scope insertion counts")
    p.add_argument("-r", "--random-slope", action="store_true",
                   help="also fit a per-repository time slope and report the LRT against it")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    rows = []
    for name, g in d.groupby("outcome", sort=False):
        r = row(g, args.covariates, args.random_slope)
        r.update(outcome=name, rq=g["rq"].iloc[0], tool=g["tool"].iloc[0],
                 logged=int(g["logged"].iloc[0]))
        rows.append(r)
    t = pd.DataFrame(rows)
    keys = ["b2", "b3"] + (["rs_b2", "rs_b3"] if args.random_slope else [])
    for key in keys:
        t[key + "_p_holm"] = multipletests(t[key + "_p"], method="holm")[1]
        t[key + "_p_bh"] = multipletests(t[key + "_p"], method="fdr_bh")[1]
        # on a logged outcome the coefficient is a relative change; report it as a percent
        # so the level shift and the slope shift can be read without exponentiating by hand
        t[key + "_pct"] = np.where(t["logged"] == 1, (np.exp(t[key]) - 1) * 100, np.nan)
    lead = ["outcome", "rq", "tool", "logged", "n", "repos", "converged"]
    t = t[lead + [c for c in t.columns if c not in lead]]
    t.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.output != "-":
        cols = ["outcome", "b1", "b1_p", "b2", "b2_pct", "b2_p", "b2_p_holm",
                "b3", "b3_pct", "b3_p", "b3_p_holm", "icc", "converged"]
        if args.random_slope:
            cols = ["outcome", "b2", "b2_p", "rs_b2", "rs_b2_p", "rs_b2_p_holm",
                    "b3", "b3_p", "rs_b3", "rs_b3_p", "rs_b3_p_holm",
                    "slope_lrt", "slope_converged"]
        print(t[cols].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
