#!/usr/bin/env python3
"""Layer 1: baseline-indexed event-time means per outcome, plus pre-trend tests."""
import argparse
import os
import sys

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statsmodels.formula.api as smf

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e2e1dd"
HUE, BAND = "#2a78d6", "#cde2fb"


def indexed(d):
    """Per repository, each month's value over that repository's own pre-marker median."""
    base = d[d["offset"] < 0].groupby("repo")["value"].median()
    base = base[base > 0]
    r = d[d["repo"].isin(base.index)].copy()
    r["ratio"] = r["value"] / r["repo"].map(base)
    return r, len(base), d["repo"].nunique()


def band(r):
    """Centre of the ratio distribution per event month, three ways, with a 95% interval.

    The mean is what §4.5 names, but a ratio against a small baseline is unbounded above
    and bounded below by zero, so a handful of repositories with near-zero pre-marker
    values can carry the mean on their own. The median and the geometric mean are carried
    alongside it so that divergence is visible instead of being read as a movement.
    """
    g = r.groupby("offset")["ratio"]
    m, sd, n = g.mean(), g.std(ddof=1), g.count()
    geo = np.exp(r.assign(l=np.log(r["ratio"].where(r["ratio"] > 0))).groupby("offset")["l"].mean())
    return pd.DataFrame({"mean": m, "half": 1.96 * sd / np.sqrt(n), "median": g.median(),
                         "geomean": geo, "n": n}).reset_index()


def linearity(fit, pre):
    """Wald test that the pre-marker dummies lie on a straight line through month -1.

    Rejecting flatness only says a pre-trend exists, which the ITS model of §4.5 already
    carries in beta1. What decides whether that model's shape is adequate is whether the
    pre-period departs from a line, so that is tested separately: with month -1 as the
    reference, linearity means coefficient(t) = theta * (t + 1).
    """
    slots = {int(fit.params.index[i].split("T.")[1].rstrip("]")): i for i in pre}
    anchor = slots.get(-2)
    if anchor is None:
        return float("nan"), 0
    rows = [t for t in sorted(slots) if t <= -3]
    if not rows:
        return float("nan"), 0
    R = np.zeros((len(rows), len(fit.params)))
    for row, t in enumerate(rows):
        R[row, slots[t]] = 1.0
        R[row, anchor] = float(t + 1)
    return float(fit.wald_test(R, scalar=True).pvalue), len(rows)


def pretrend(d):
    """Flatness of the pre-marker dummies and the pre-marker linear slope.

    Repository fixed effects with SEs clustered by repository: the event-time dummies are
    a diagnostic for the shape §4.5 assumes, so they are estimated without imposing it.
    """
    out = {}
    fit = smf.ols("y ~ C(repo) + C(offset, Treatment(reference=-1))", data=d).fit(
        cov_type="cluster", cov_kwds={"groups": d["repo"]})
    pre = [i for i, name in enumerate(fit.params.index)
           if "T.-" in name and name.split("T.")[1].rstrip("]") != "-1"]
    if pre:
        R = np.zeros((len(pre), len(fit.params)))
        for row, col in enumerate(pre):
            R[row, col] = 1.0
        out["pre_flat_p"] = float(fit.wald_test(R, scalar=True).pvalue)
        out["pre_flat_df"] = len(pre)
        out["pre_lin_p"], out["pre_lin_df"] = linearity(fit, pre)
    coefs = {int(name.split("T.")[1].rstrip("]")): (fit.params.iloc[i], fit.bse.iloc[i])
             for i, name in enumerate(fit.params.index) if "offset" in name}
    lin = smf.ols("y ~ C(repo) + offset", data=d[d["offset"] < 0]).fit(
        cov_type="cluster", cov_kwds={"groups": d[d["offset"] < 0]["repo"]})
    out["pre_slope"] = float(lin.params["offset"])
    out["pre_slope_p"] = float(lin.pvalues["offset"])
    return out, coefs


def panel(ax, title, unit, b):
    ax.fill_between(b["offset"], b["mean"] - b["half"], b["mean"] + b["half"],
                    color=BAND, linewidth=0, zorder=2)
    ax.plot(b["offset"], b["mean"], color=HUE, linewidth=1.8, marker="o", markersize=2.8,
            zorder=3, label="mean")
    ax.plot(b["offset"], b["median"], color=INK, linewidth=1.0, linestyle=(0, (4, 2)),
            zorder=3, label="median")
    ax.axhline(1.0, color=GRID, linewidth=1, zorder=1)
    ax.axvline(0, color=INK, linewidth=1, linestyle="--", zorder=4)
    ax.set_title(title, fontsize=8.5, color=INK, loc="left", pad=4)
    ax.set_ylabel(unit, fontsize=7, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7)


def figure(bands, names, title, out):
    cols = 3
    rows = -(-len(names) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(12, 2.5 * rows), dpi=150, sharex=True)
    fig.patch.set_facecolor(SURFACE)
    flat = axes.ravel()
    for ax, name in zip(flat, names):
        panel(ax, name, "value / own pre-marker median", bands[name])
    for ax in flat[len(names):]:
        ax.set_visible(False)
    for ax in flat[max(0, len(names) - cols):len(names)]:
        ax.set_xlabel("months relative to the marker", fontsize=8, color=MUTED)
    fig.suptitle(title, fontsize=11, color=INK, x=0.02, ha="left", y=0.999)
    flat[0].legend(frameon=False, fontsize=7, loc="best", labelcolor=MUTED)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-o", "--output", default="-", help="series CSV, - for stdout")
    p.add_argument("-t", "--tests", default="", help="pre-trend test CSV, empty to skip")
    p.add_argument("-c", "--coefficients", default="", help="event-time coefficient CSV")
    p.add_argument("-f", "--figures", default="", help="figure prefix, empty to skip")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    series, tests, coefs, bands = [], [], [], {}
    for name, g in d.groupby("outcome", sort=False):
        r, kept, total = indexed(g)
        b = band(r)
        b["outcome"] = name
        bands[name] = b
        series.append(b)
        t, cf = pretrend(g)
        t.update(outcome=name, rq=g["rq"].iloc[0], tool=g["tool"].iloc[0],
                 logged=int(g["logged"].iloc[0]), repos_indexed=kept, repos=total)
        tests.append(t)
        for off, (est, se) in sorted(cf.items()):
            coefs.append({"outcome": name, "offset": off, "coef": est, "se": se,
                          "lo": est - 1.96 * se, "hi": est + 1.96 * se})
    series = pd.concat(series, ignore_index=True)[["outcome", "offset", "mean", "half",
                                                   "median", "geomean", "n"]]
    tests = pd.DataFrame(tests)[["outcome", "rq", "tool", "logged", "repos", "repos_indexed",
                                 "pre_slope", "pre_slope_p", "pre_flat_p", "pre_flat_df",
                                 "pre_lin_p", "pre_lin_df"]]
    series.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.tests:
        tests.to_csv(args.tests, index=False)
    if args.coefficients:
        pd.DataFrame(coefs).to_csv(args.coefficients, index=False)
    if args.figures:
        os.makedirs(os.path.dirname(args.figures) or ".", exist_ok=True)
        for rq in sorted(d["rq"].unique()):
            names = [n for n in bands if d[d["outcome"] == n]["rq"].iloc[0] == rq]
            figure(bands, names, f"{rq}: each repository's value over its own pre-marker "
                   f"median, averaged across repositories; shaded 95% CI of the mean",
                   f"{args.figures}_{rq.lower()}.png")
    if args.output != "-":
        print(tests.round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
