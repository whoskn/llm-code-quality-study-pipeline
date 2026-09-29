#!/usr/bin/env python3
"""Per-outcome summary of the three layers as reported in the thesis results tables."""
import argparse
import sys

import numpy as np
import pandas as pd

# months between the centres of the two paired windows (-12..-1 and +1..+12)
SPAN = 13


def pct(x):
    return (np.exp(x) - 1) * 100


def table(its, paired):
    d = its.merge(paired, on=["outcome", "rq", "tool", "logged"], suffixes=("", "_paired"))
    logged = d["logged"] == 1
    out = pd.DataFrame({
        "outcome": d["outcome"], "rq": d["rq"], "tool": d["tool"], "logged": d["logged"],
        # logged outcomes in % (per month for the slopes), the others in native units
        "b1": np.where(logged, pct(d["b1"]), d["b1"]),
        "b2": np.where(logged, d["b2_pct"], d["b2"]),
        "b2_p_holm": d["b2_p_holm"],
        "b3": np.where(logged, d["b3_pct"], d["b3"]),
        "b3_p_holm": d["b3_p_holm"],
        "paired": np.where(logged, d["median_pct"], d["median_diff"]),
        "paired_lo": np.where(logged, d["pct_lo"], d["ci_lo"]),
        "paired_hi": np.where(logged, d["pct_hi"], d["ci_hi"]),
        "paired_p_holm": d["p_holm"],
        "n_up": d["n_up"], "n_down": d["n_down"], "n_tied": d["n_tied"],
        "effect_r": d["effect_r"],
    })
    # share of the paired difference that the pre-marker slope alone implies over the
    # distance between the window centres, both on the analysis scale
    out["explained"] = (100 * d["b1"] * SPAN / d["median_diff"]).where(d["median_diff"] != 0)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--its", default="data/13_analysis/its_unadjusted.csv", help="layer-2 ITS CSV")
    p.add_argument("-p", "--paired", default="data/13_analysis/paired.csv", help="layer-3 paired CSV")
    p.add_argument("-o", "--output", default="-", help="output CSV, - for stdout")
    args = p.parse_args()
    try:
        its, paired = pd.read_csv(args.its), pd.read_csv(args.paired)
    except OSError as e:
        sys.exit(f"{e}")
    table(its, paired).to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
