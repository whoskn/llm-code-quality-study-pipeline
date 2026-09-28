#!/usr/bin/env python3
import argparse
import sys

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests


# Month 0 is in neither window: the marker falls inside it, the snapshot at its end.
def diffs(g, pre_lo, post_hi):
    pre = g[(g["offset"] >= pre_lo) & (g["offset"] < 0)]
    post = g[(g["offset"] > 0) & (g["offset"] <= post_hi)]
    a = pre.groupby("repo")[["y", "value"]].median()
    b = post.groupby("repo")[["y", "value"]].median()
    j = a.join(b, how="inner", lsuffix="_pre", rsuffix="_post")
    j["d"] = j["y_post"] - j["y_pre"]
    j["d_raw"] = j["value_post"] - j["value_pre"]
    return j.dropna(subset=["d"])


def rank_biserial(d):
    nz = d[d != 0]
    if not len(nz):
        return float("nan")
    r = stats.rankdata(np.abs(nz))
    pos, neg = r[nz > 0].sum(), r[nz < 0].sum()
    return float((pos - neg) / (pos + neg))


def boot_ci(d, reps, seed):
    rng = np.random.default_rng(seed)
    v = np.asarray(d)
    draws = np.median(v[rng.integers(0, len(v), size=(reps, len(v)))], axis=1)
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def main():
    p = argparse.ArgumentParser(
        description="Layer 3: paired pre/post window medians per repository.")
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-o", "--output", default="-", help="output CSV, - for stdout")
    p.add_argument("-w", "--pre-months", type=int, default=12, help="months in the pre window")
    p.add_argument("-W", "--post-months", type=int, default=12, help="months in the post window")
    p.add_argument("-b", "--bootstrap", type=int, default=10000, help="bootstrap resamples")
    p.add_argument("-S", "--seed", type=int, default=20260908, help="bootstrap seed")
    args = p.parse_args()
    try:
        panel = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    rows = []
    for name, g in panel.groupby("outcome", sort=False):
        j = diffs(g, -args.pre_months, args.post_months)
        d = j["d"].to_numpy()
        if len(d) < 3:
            continue
        logged = int(g["logged"].iloc[0])
        med = float(np.median(d))
        lo, hi = boot_ci(d, args.bootstrap, args.seed)
        try:
            w, pv = stats.wilcoxon(d, zero_method="wilcox")
        except ValueError:
            w, pv = float("nan"), float("nan")
        rows.append({
            "outcome": name, "rq": g["rq"].iloc[0], "tool": g["tool"].iloc[0],
            "logged": logged, "repos": len(d),
            "median_pre": float(j["value_pre"].median()),
            "median_post": float(j["value_post"].median()),
            "median_diff": med, "ci_lo": lo, "ci_hi": hi,
            "median_pct": (np.exp(med) - 1) * 100 if logged else float("nan"),
            "pct_lo": (np.exp(lo) - 1) * 100 if logged else float("nan"),
            "pct_hi": (np.exp(hi) - 1) * 100 if logged else float("nan"),
            "median_diff_raw": float(np.median(j["d_raw"])),
            "n_up": int((d > 0).sum()), "n_down": int((d < 0).sum()),
            "n_tied": int((d == 0).sum()),
            "wilcoxon_W": float(w), "p": float(pv), "effect_r": rank_biserial(d),
        })
    t = pd.DataFrame(rows)
    t["p_holm"] = multipletests(t["p"], method="holm")[1]
    t.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.output != "-":
        cols = ["outcome", "repos", "median_pre", "median_post", "median_pct", "pct_lo",
                "pct_hi", "n_up", "n_down", "effect_r", "p", "p_holm"]
        print(t[cols].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
