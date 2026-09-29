#!/usr/bin/env python3
"""Apply the metadata gates of plan.md §3 to a candidate list -> pool CSV + funnel."""
import argparse
import csv
import sys
from datetime import date
from itertools import combinations


def month_start(text, label):
    """'YYYY-MM' (or 'YYYY-MM-DD') -> the first day of that month."""
    try:
        y, m = (int(part) for part in text.split("-")[:2])
        return date(y, m, 1)
    except ValueError:
        sys.exit(f"{label}: expected YYYY-MM, got {text!r}")


def shift(day, months):
    """Move a date by whole months, keeping the day of month."""
    total = day.year * 12 + day.month - 1 + months
    return date(total // 12, total % 12 + 1, day.day)


def parse_day(text):
    return date.fromisoformat(text) if text else None


def truthy(text):
    return str(text).strip().lower() in ("true", "1", "yes")


def gates(args, history):
    """The ordered gates, each a (name, predicate) pair.

    Order matters only for the funnel: it reports the repos each gate is the first to
    reject, so a repo failing several gates is counted once, at the earliest one. The
    last block needs the clone and is skipped until date_repo.py output is supplied.
    """
    # both windows are inclusive month ranges: the last 12 months up to 2026-07 start in
    # 2025-08, and the earliest pre-window month for m0 = 2025-07 is 2024-07
    live_since = shift(month_start(args.as_of, "--as-of"), 1 - args.push_months)
    created_by = shift(month_start(args.m0_latest, "--m0-latest"), -args.pre_months)
    created_before = shift(created_by, 1)
    max_kb = args.max_size_mb * 1024

    def created(r):
        day = parse_day(r["gh_created"])
        return day is not None and day < created_before

    def pushed(r):
        day = parse_day(r["gh_pushed"])
        return day is not None and day >= live_since

    m0_range = (month_start(args.m0_earliest, "--m0-earliest"),
                month_start(args.m0_latest, "--m0-latest"))

    def m0_eligible(r):
        m0 = month_start(r["m0"], "m0") if r.get("m0") else None
        return m0 is not None and m0_range[0] <= m0 <= m0_range[1]

    def history_deep(r):
        first, last = r.get("first_commit", ""), r.get("last_commit", "")
        if not (first and last):
            return False
        m0 = month_start(r["m0"], "m0")
        return (month_start(first, "first_commit") <= shift(m0, -args.pre_months)
                and month_start(last, "last_commit") >= shift(m0, args.post_months))

    # a repo whose py_loc is empty was dated before the column existed; an unmeasured
    # row is not rejected, so rerunning on an older history reproduces its own sample
    def within_loc(r):
        return int(r.get("py_loc") or 0) <= args.max_loc

    clone_gates = [
        ("history measured", lambda r: r.get("status") == "ok"),
        (f"in-scope Python LOC <= {args.max_loc:,}", within_loc),
        (f"m0 in [{args.m0_earliest}, {args.m0_latest}]", m0_eligible),
        (f"history spans {args.pre_months}+{args.post_months} months", history_deep),
        (f"active in >= {args.min_active} of {args.pre_months} months per side",
         lambda r: min(int(r["pre_active"] or 0),
                       int(r["post_active"] or 0)) >= args.min_active),
        (f"markers in >= {args.min_onset} of the first {args.onset_window} post-months",
         lambda r: int(r["onset_months"] or 0) >= args.min_onset),
    ]

    return [
        ("resolved on GitHub", lambda r: r["gh_status"] == "ok"),
        (f"primary language {args.language}",
         lambda r: args.language == "any" or r["gh_language"] == args.language),
        (f"stars >= {args.min_stars}", lambda r: int(r["gh_stars"] or 0) >= args.min_stars),
        ("not a fork", lambda r: not truthy(r["gh_fork"])),
        ("not archived", lambda r: not truthy(r["gh_archived"])),
        ("not a mirror, not disabled", lambda r: not truthy(r["gh_mirror"])),
        ("has a licence",
         lambda r: bool(r["gh_license"]) and r["gh_license"] != "NOASSERTION"),
        ("not empty", lambda r: not truthy(r["gh_empty"])),
        (f"created <= {created_by:%Y-%m}", created),
        (f"pushed since {live_since}", pushed),
        (f"size <= {args.max_size_mb} MB", lambda r: int(r["gh_size_kb"] or 0) <= max_kb),
    ] + (clone_gates if history else [])


def path_keys(path):
    """Every plausible spelling of 'owner/name' for a clone directory path."""
    parts = path.rstrip("/").split("/")
    tail = "/".join(parts[-2:]) if len(parts) > 1 else parts[-1]
    return {tail.lower(), parts[-1].replace("__", "/").lower()}


def load_history(path):
    """date_repo.py output, indexed by repo name derived from the clone path."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        sys.exit(f"cannot read {path}: {e}")
    index = {}
    for row in rows:
        if "repo" not in row:
            sys.exit(f"{path}: no repo column")
        row["clone_path"] = row.pop("repo")
        for k in path_keys(row["clone_path"]):
            index.setdefault(k, row)
    return index


def tier(row):
    """§5 tiers: agreement between families, not the number of markers."""
    fired = [f for f in row.get("clone_families", "").split(";") if f]
    if len(fired) < 2:
        return 3
    return 1 if int(row["family_spread"] or 0) <= 2 else 2


def rank_score(row, args):
    """Within-tier ranking key: marker share × snapshot density (§5)."""
    active = int(row["pre_active"] or 0) + int(row["post_active"] or 0)
    density = active / (args.pre_months + args.post_months)
    return float(row["marker_share"] or 0) * density


def run_gates(rows, checks):
    """Return the surviving rows plus the per-gate funnel counts."""
    funnel, live = [], rows
    for name, ok in checks:
        kept = [r for r in live if ok(r)]
        funnel.append((name, len(live) - len(kept), len(kept)))
        live = kept
    return live, funnel


def overlap(rows, column="families"):
    """Counts for all 7 non-empty subsets of {A, B, C} (§6 family-overlap table)."""
    sets = [frozenset(r.get(column, "").split(";")) & {"A", "B", "C"} for r in rows]
    out = {}
    for size in (1, 2, 3):
        for combo in combinations("ABC", size):
            out["".join(combo)] = sum(1 for s in sets if s == frozenset(combo))
    return out


def report(out, rows, funnel, kept, args):
    n = len(rows)
    out.write("# Repository selection funnel\n\n")
    out.write(f"Sources unioned into {n} name-unique candidates; gates as fixed in "
              f"plan.md §3, applied {date.today()}.\n\n")
    out.write("| Stage | Rejected here | Remaining |\n| --- | --- | --- |\n")
    out.write(f"| raw union | — | {n} |\n")
    for name, dropped, left in funnel:
        out.write(f"| {name} | {dropped} | {left} |\n")
    if args.history:
        out.write(f"\nAll gates leave **{len(kept)}** repos.\n\n")
    else:
        out.write(f"\nGates answerable from GitHub metadata leave **{len(kept)}** repos; "
                  "the history-density, sustained-onset and m₀ gates need the clone and "
                  "are applied afterwards, by rerunning with --history.\n\n")

    out.write("## Family overlap\n\n")
    out.write("A = committed agent file, B = git metadata, C = human self-admission. The "
              "union column is what the sources claim; the pool column is "
              + ("re-derived from the clones.\n\n" if args.history
                 else "still source-claimed until --history is supplied.\n\n"))
    out.write("| Families | Union | Pool |\n| --- | --- | --- |\n")
    raw = overlap(rows)
    pool = overlap(kept, "clone_families" if args.history else "families")
    for key in sorted(raw, key=lambda k: (len(k), k)):
        out.write(f"| {key} | {raw[key]} | {pool[key]} |\n")
    only_a = sum(v for k, v in pool.items() if "A" in k)
    if kept:
        out.write(f"\nA config-file search alone (family A) would have found "
                  f"{only_a}/{len(kept)} = {100 * only_a / len(kept):.0f}% of the pool.\n")

    if args.history and kept:
        out.write("\n## Tiers (§5)\n\n| Tier | Repos | Manual review |\n"
                  "| --- | --- | --- |\n")
        depth = {1: "lightweight plausibility check", 2: "full review", 3: "full review"}
        for t in (1, 2, 3):
            count = sum(1 for r in kept if int(r["tier"]) == t)
            out.write(f"| {t} | {count} | {depth[t]} |\n")

    dated = [r for r in kept if r["published_m0"]]
    if dated and not args.history:
        out.write(f"\n## Published dates present\n\n{len(dated)} pool repos carry a "
                  "source-published adoption date, available for the §6 dating cross-check "
                  f"once m₀ is re-derived (range {min(r['published_m0'] for r in dated)} … "
                  f"{max(r['published_m0'] for r in dated)}).\n")
    elif dated:
        # §6 dating cross-check: ours should never be later than a published date
        late = [r for r in dated if r["m0"] > r["published_m0"]]
        out.write(f"\n## Dating cross-check\n\n{len(dated)} pool repos carry a "
                  f"source-published adoption date. Our re-derived m₀ is earlier or equal "
                  f"{len(dated) - len(late)} of them; later for {len(late)}"
                  + (": " + ", ".join(f"{r['full_name']} ({r['published_m0']} → {r['m0']})"
                                      for r in late[:10]) if late else "")
                  + ".\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", default="-",
                   help="github_meta.py output CSV, '-' for stdin (default: -)")
    p.add_argument("-o", "--output", default="-",
                   help="pool CSV output file, '-' for stdout (default: -)")
    p.add_argument("-r", "--report", default="",
                   help="write the funnel and family-overlap tables to this markdown file")
    p.add_argument("-l", "--language", default="Python",
                   help="required primary language, 'any' to skip (default: Python)")
    p.add_argument("-s", "--min-stars", type=int, default=100,
                   help="minimum stars (default: 100)")
    p.add_argument("-H", "--history", default="",
                   help="date_repo.py output; adds the m0, density and onset gates")
    p.add_argument("-m", "--m0-latest", default="2025-07",
                   help="latest eligible integration month (default: 2025-07)")
    p.add_argument("-e", "--m0-earliest", default="2023-01",
                   help="earliest eligible integration month (default: 2023-01)")
    p.add_argument("-w", "--pre-months", type=int, default=12,
                   help="pre-window length; creation must precede m0 by this much "
                        "(default: 12)")
    p.add_argument("-W", "--post-months", type=int, default=12,
                   help="post-window length in months (default: 12)")
    p.add_argument("-d", "--min-active", type=int, default=10,
                   help="months per side that must contain a commit (default: 10)")
    p.add_argument("-n", "--min-onset", type=int, default=3,
                   help="post-months that must carry a marker (default: 3)")
    p.add_argument("-N", "--onset-window", type=int, default=6,
                   help="post-months the onset count was taken over, for labelling only "
                        "(default: 6)")
    p.add_argument("-a", "--as-of", default="2026-07",
                   help="last month with complete data (default: 2026-07)")
    p.add_argument("-p", "--push-months", type=int, default=12,
                   help="a repo counts as live if pushed within this many months of "
                        "--as-of (default: 12)")
    p.add_argument("-z", "--max-size-mb", type=int, default=500,
                   help="maximum repo size in MB (default: 500)")
    p.add_argument("-L", "--max-loc", type=int, default=1_000_000,
                   help="maximum in-scope Python lines at the end of the post window "
                        "(default: 1000000)")
    args = p.parse_args()

    try:
        src = sys.stdin if args.input == "-" else open(args.input, newline="",
                                                      encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot read {args.input}: {e}")
    try:
        reader = csv.DictReader(src)
        missing = {"full_name", "families", "published_m0", "gh_status", "gh_language",
                   "gh_stars", "gh_fork", "gh_archived", "gh_mirror", "gh_empty",
                   "gh_license", "gh_created", "gh_pushed",
                   "gh_size_kb"} - set(reader.fieldnames or ())
        if missing:
            sys.exit(f"{args.input}: missing columns {', '.join(sorted(missing))}")
        columns, rows = list(reader.fieldnames), list(reader)
    finally:
        if src is not sys.stdin:
            src.close()

    history = load_history(args.history) if args.history else None
    if history:
        extra = dict.fromkeys(k for row in history.values() for k in row)
        columns += [k for k in extra if k not in columns] + ["tier", "rank_score"]
        for row in rows:
            found = history.get(row["full_name"].lower()) or history.get(
                row.get("gh_full_name", "").lower())
            row.update({k: "" for k in extra} | (found or {}))

    kept, funnel = run_gates(rows, gates(args, history))
    if history:
        for row in kept:
            row["tier"] = tier(row)
            row["rank_score"] = f"{rank_score(row, args):.4f}"
        kept.sort(key=lambda r: (r["tier"], -float(r["rank_score"])))
    else:
        kept.sort(key=lambda r: -int(r["gh_stars"] or 0))

    try:
        out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                        encoding="utf-8")
    except OSError as e:
        sys.exit(f"cannot write {args.output}: {e}")
    try:
        w = csv.DictWriter(out, columns)
        w.writeheader()
        w.writerows(kept)
    finally:
        if out is not sys.stdout:
            out.close()

    report(sys.stderr, rows, funnel, kept, args)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            report(f, rows, funnel, kept, args)


if __name__ == "__main__":
    main()
