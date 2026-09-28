#!/usr/bin/env python3
import argparse
import os
import sys

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e2e1dd"
HUE, BAND = "#2a78d6", "#cde2fb"
LABELS = {
    "smells_per_kloc": "Code smells / KLOC (Sonar)",
    "violations_per_kloc": "All issues / KLOC (Sonar)",
    "cognitive_per_kloc": "Cognitive compl. / KLOC (Sonar)",
    "cognitive_per_function": "Cognitive compl. / fn (Sonar)",
    "complexipy_per_function": "Cognitive compl. / fn (complexipy)",
    "cyclomatic_radon": "Cyclomatic compl. / fn (radon)",
    "cyclomatic_lizard": "Cyclomatic compl. / fn (lizard)",
    "maintainability_radon": "Maintainability index (radon)",
    "lizard_warnings_per_kloc": "Threshold warnings / KLOC (lizard)",
    "duplication_pct": "Duplicated lines % (Sonar)",
    "debt_ratio_pct": "Debt ratio % (Sonar)",
    "comment_density_pct": "Comment density % (Sonar)",
    "bugs_per_kloc": "Reliability issues / KLOC (Sonar)",
    "functions_per_kloc": "Functions / KLOC (Sonar)",
    "loc_per_function": "LOC / function (lizard)",
    "sonar_vuln_per_kloc": "Vulnerabilities / KLOC (Sonar)",
    "bandit_per_kloc": "Bandit findings / KLOC",
    "bandit_high_per_kloc": "Bandit high sev. / KLOC",
    "codeql_per_kloc": "CodeQL alerts / KLOC",
    "codeql_high_per_kloc": "CodeQL high+crit. / KLOC",
    "cwe_distinct": "Distinct CWEs (CodeQL + bandit)",
}


def indexed(d):
    base = d[d["offset"] < 0].groupby("repo")["value"].median()
    base = base[base > 0]
    r = d[d["repo"].isin(base.index)].copy()
    r["ratio"] = r["value"] / r["repo"].map(base)
    return r


# Ratios on a near-zero baseline are unbounded above; the median guards the mean.
def band(r):
    g = r.groupby("offset")["ratio"]
    m, sd, n = g.mean(), g.std(ddof=1), g.count()
    return pd.DataFrame({"mean": m, "half": 1.96 * sd / np.sqrt(n), "median": g.median(),
                         "n": n}).reset_index()


def panel(ax, title, unit, b):
    ax.fill_between(b["offset"], b["mean"] - b["half"], b["mean"] + b["half"],
                    color=BAND, linewidth=0, zorder=2)
    ax.plot(b["offset"], b["mean"], color=HUE, linewidth=1.2, marker="o", markersize=1.8,
            zorder=3, label="mean")
    ax.plot(b["offset"], b["median"], color=INK, linewidth=0.9, linestyle=(0, (4, 2)),
            zorder=3, label="median")
    ax.axhline(1.0, color=GRID, linewidth=0.8, zorder=1)
    ax.axvline(0, color=MUTED, linewidth=0.8, zorder=4)
    ax.set_title(title, fontsize=7.5, color=INK, loc="left", pad=3)
    ax.set_ylabel(unit, fontsize=6.5, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=6.5)


def figure(bands, names, out):
    cols = 3
    rows = -(-len(names) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(6.3, 1.7 * rows), dpi=300, sharex=True)
    fig.patch.set_facecolor(SURFACE)
    flat = axes.ravel()
    for i, (ax, name) in enumerate(zip(flat, names)):
        unit = "ratio to pre-marker median" if i % cols == 0 else ""
        panel(ax, LABELS.get(name, name), unit, bands[name])
    for ax in flat[len(names):]:
        ax.set_visible(False)
    for ax in flat[max(0, len(names) - cols):len(names)]:
        ax.set_xlabel("months relative to the marker", fontsize=6.5, color=MUTED)
    flat[0].legend(frameon=False, fontsize=6.5, loc="best", labelcolor=MUTED)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(
        description="Layer 1: baseline-indexed event-time means per outcome.")
    p.add_argument("-p", "--panel", default="data/13_analysis/panel.csv", help="tidy panel CSV")
    p.add_argument("-o", "--output", default="-", help="series CSV, - for stdout")
    p.add_argument("-f", "--figures", default="", help="figure prefix, empty to skip")
    args = p.parse_args()
    try:
        d = pd.read_csv(args.panel)
    except OSError as e:
        sys.exit(f"{e}")
    series, bands = [], {}
    for name, g in d.groupby("outcome", sort=False):
        b = band(indexed(g))
        b["outcome"] = name
        bands[name] = b
        series.append(b)
    series = pd.concat(series, ignore_index=True)[["outcome", "offset", "mean", "half",
                                                   "median", "n"]]
    series.to_csv(sys.stdout if args.output == "-" else args.output, index=False)
    if args.figures:
        os.makedirs(os.path.dirname(args.figures) or ".", exist_ok=True)
        for rq in sorted(d["rq"].unique()):
            names = [n for n in bands if d[d["outcome"] == n]["rq"].iloc[0] == rq]
            figure(bands, names, f"{args.figures}_{rq.lower()}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
