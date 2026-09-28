#!/usr/bin/env python3
import argparse
import csv
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
RAMP = ["#86b6ef", "#3987e5", "#184f95"]  # blue 250 / 400 / 600, ordinal

# Funnel table stage -> bar label; stages sharing a label form one bar.
STAGES = {
    "raw union": "union of four published source lists",
    "resolved on GitHub": "resolves on GitHub",
    "primary language Python": "primary language Python",
    "stars >= 100": "≥ 100 stars",
    "not a fork": "not a fork, archived, mirror or empty",
    "not archived": "not a fork, archived, mirror or empty",
    "not a mirror, not disabled": "not a fork, archived, mirror or empty",
    "not empty": "not a fork, archived, mirror or empty",
    "has a licence": "carries a licence",
    "created <= 2024-07": "created on or before 2024-07",
    "pushed since 2025-08-01": "pushed since 2025-08",
    "size <= 500 MB": "size ≤ 500 MB",
    "history measured": "history measurable from the clone",
    "in-scope Python LOC <= 1,000,000": "in-scope Python ≤ 1M lines",
    "m0 in [2023-01, 2025-07]": "m₀ in [2023-01, 2025-07]",
    "history spans 12+12 months": "12 months of history each side",
    "active in >= 10 of 12 months per side": "active in ≥ 10 of 12 months per side",
    "markers in >= 3 of the first 6 post-months": "markers in ≥ 3 of first 6 post-m₀ months",
}
# Not a gate: repositories radon could not finish.
MEASURED = "measurable by all six analysers"
LICENCES = 4  # licences shown by name; the rest are pooled as "other"
DOMAINS = {"ai-ml": "AI/ML", "devtools-infra": "devtools & infra", "science-eng": "science & eng.",
           "web-apps": "web apps", "data": "data", "security": "security", "other": "other"}
DENSE = [(2025, k) for k in range(3, 8)]  # the five months most marker months fall in

# Print style of the composition figure, in the thesis font.
FORMAL = {
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.family": "serif", "font.serif": ["cmr10"], "mathtext.fontset": "cm",
    "axes.formatter.use_mathtext": True, "font.size": 9, "text.color": "black",
    "axes.labelcolor": "black", "xtick.color": "black", "ytick.color": "black",
    "axes.edgecolor": "black", "axes.linewidth": 0.6,
}


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def stages(path):
    out = []
    with open(path) as f:
        for line in f:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[2].isdigit():
                out.append((cells[0], 0 if cells[1] == "—" else int(cells[1]), int(cells[2])))
            elif out:
                break
    if not out:
        sys.exit(f"no funnel table in {path}")
    return out


# The clone-side table repeats the metadata gates with zero rejections; skip them.
def funnel(metadata, history, sample):
    seen, bars = set(), []
    for stage, rejected, remaining in stages(metadata) + stages(history):
        if stage in seen:
            continue
        seen.add(stage)
        if stage not in STAGES:
            sys.exit(f"no label for funnel stage: {stage}")
        if bars and not rejected:
            continue
        label = STAGES[stage]
        if bars and bars[-1][0] == label:
            bars[-1] = (label, bars[-1][1] + rejected, remaining)
        else:
            bars.append((label, rejected or None, remaining))
    bars.append((MEASURED, bars[-1][2] - len(sample), len(sample)))
    return bars


def style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.size": 9, "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED,
        "ytick.color": INK2, "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.axisbelow": True, "grid.color": GRID, "grid.linewidth": 0.6,
    })


def save(fig, out, name, dpi=200):
    path = os.path.join(out, name)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(path)


def fig_funnel(bars, out):
    fig, ax = plt.subplots(figsize=(9, 6))
    n, top = len(bars), bars[0][2]
    for i, (label, rej, rem) in enumerate(bars):
        col = RAMP[2] if i >= n - 2 else RAMP[1]
        ax.barh(i, rem, height=0.66, color=col, zorder=2)
        ax.text(rem + 80, i, f"{rem:,}", va="center", fontsize=8.5, color=INK,
                fontweight="bold" if i >= n - 2 else "normal")
        if rej:
            ax.text(top * 1.17, i, f"−{rej:,}", va="center", ha="right", fontsize=8.5, color=MUTED)
    ax.text(top * 1.17, -1.0, "rejected", ha="right", va="center", fontsize=8.5, color=MUTED)
    ax.set_ylim(n - 0.4, -1.4)
    ax.set_yticks(range(n))
    ax.set_yticklabels([b[0] for b in bars])
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, top * 1.19)
    ax.grid(axis="x")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("repositories remaining")
    ax.set_title("Repository selection funnel", loc="left", fontsize=12, fontweight="bold", pad=10)
    save(fig, out, "fig_funnel.png")


def m0_counts(sample):
    counts = {}
    for r in sample:
        y, m = r["m0"].split("-")
        counts[(int(y), int(m))] = counts.get((int(y), int(m)), 0) + 1
    return counts


def licences(meta, names):
    counts = {}
    for r in meta:
        if r["full_name"] in names:
            counts[r["license"]] = counts.get(r["license"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:LICENCES] + [("other", sum(v for _, v in ranked[LICENCES:]))]


def domains(classification, names):
    counts = {}
    for r in classification:
        if r["full_name"] in names:
            if r["domain"] not in DOMAINS:
                sys.exit(f"no label for domain: {r['domain']}")
            counts[r["domain"]] = counts.get(r["domain"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (kv[0] == "other", -kv[1], kv[0]))
    return [(DOMAINS[k], v) for k, v in ranked]


def bars_m0(ax, counts):
    first, last = min(counts), max(counts)
    months, (y, m) = [], first
    while (y, m) <= last:
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for i, ym in enumerate(months):
        n = counts.get(ym, 0)
        ax.bar(i, n, width=0.7, color=RAMP[2] if ym in DENSE else RAMP[0], zorder=2)
        if n:
            ax.text(i, n + 0.5, str(n), ha="center", va="bottom", fontsize=8)
    i0, i1 = months.index(DENSE[0]), months.index(DENSE[-1])
    top = max(counts.values()) + 5
    ax.plot([i0 - 0.4, i0 - 0.4, i1 + 0.4, i1 + 0.4], [top - 1.2, top, top, top - 1.2], color="black", lw=0.6)
    ax.text((i0 + i1) / 2, top + 0.6, f"{sum(counts.get(d, 0) for d in DENSE)} of {sum(counts.values())} "
            "in 2025-03 to 2025-07", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels([f"{y}-{m:02d}" for y, m in months], rotation=90, fontsize=7.5)
    ax.set_xlim(-0.7, len(months) - 0.3)
    ax.set_ylim(0, top + 4)
    ax.set_yticks([])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="x", length=2, width=0.6)
    ax.set_title("(a) Marker month $m_0$, repositories per month", loc="left", fontsize=10, pad=8)


def hbars(ax, cats, title):
    total = sum(v for _, v in cats)
    ys = range(len(cats))
    ax.barh(list(ys), [v for _, v in cats], height=0.6, color=RAMP[1], zorder=2)
    for y, (_, v) in zip(ys, cats):
        ax.text(v + total * 0.008, y, f"{v} ({v / total:.0%})", va="center", fontsize=8.5)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([n for n, _ in cats], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, max(v for _, v in cats) * 1.18)
    ax.set_xticks([])
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=4)
    ax.set_title(title, loc="left", fontsize=10, pad=6)


def fig_composition(counts, panels, out):
    # A4 text width, printed unscaled; spacer rows hold the rotated month labels.
    with plt.rc_context(FORMAL):
        fig = plt.figure(figsize=(6.3, 8.6))
        g = GridSpec(5, 1, figure=fig, height_ratios=[1.6, 0.3, 0.75, 0.2, 1.0], hspace=0.05,
                     left=0.2, right=0.97, top=0.96, bottom=0.01)
        bars_m0(fig.add_subplot(g[0]), counts)
        for row, (title, cats), tag in zip((2, 4), panels, "bc"):
            hbars(fig.add_subplot(g[row]), cats, f"({tag}) {title}")
        save(fig, out, "fig_composition.png", dpi=300)


def main():
    p = argparse.ArgumentParser(
        description="Draw the selection funnel and sample composition figures.")
    p.add_argument("-d", "--data", default="data", help="pipeline data directory (default: %(default)s)")
    p.add_argument("-o", "--out", default="data/13_analysis/figures",
                   help="output directory (default: %(default)s)")
    args = p.parse_args()
    paths = {k: os.path.join(args.data, v) for k, v in {
        "metadata": "03_pool/funnel_metadata.md", "history": "05_sample/funnel.md",
        "sample": "05_sample/sample.csv", "meta": "10_readmes/meta.csv",
        "classification": "10_readmes/classification.csv"}.items()}
    missing = [v for v in paths.values() if not os.path.isfile(v)]
    if missing:
        sys.exit(f"no such file: {', '.join(missing)}")
    sample = rows(paths["sample"])
    names = {r["full_name"] for r in sample}
    os.makedirs(args.out, exist_ok=True)
    style()
    fig_funnel(funnel(paths["metadata"], paths["history"], sample), args.out)
    fig_composition(m0_counts(sample), [("Licence", licences(rows(paths["meta"]), names)),
                                        ("Domain", domains(rows(paths["classification"]), names))],
                    args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
