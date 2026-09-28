#!/usr/bin/env python3
import argparse
import csv
import sys

COLUMNS = ["full_name", "families", "sources", "github_id", "src_language",
           "src_stars", "ai_commits", "ai_tools", "published_m0"]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def from_cursor(path):
    for r in read_csv(path):
        yield {"full_name": r["repo_name"], "families": "A", "sources": "cursor",
               "ai_commits": r["cursor_commits"], "ai_tools": "cursor",
               "published_m0": r["cursor_adoption_time"][:7]}


def from_techdebt(path):
    for r in read_csv(path):
        yield {"full_name": r["repo_name"], "families": "B", "sources": "techdebt",
               "src_language": r["language"], "src_stars": r["stars"],
               "ai_commits": r["commit_count"], "ai_tools": r["ai_tool"]}


def from_aidev(path):
    for r in read_csv(path):
        yield {"full_name": r["full_name"], "families": "B", "sources": "aidev",
               "github_id": r["id"], "src_language": r["language"],
               "src_stars": r["stars"]}


def from_genai(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            lang, _, name = line.strip().partition("\t")
            if name:
                yield {"full_name": name, "families": "C", "sources": "genai",
                       "src_language": lang}


def merge(into, new):
    for key in ("families", "sources", "ai_tools"):
        if key in into:
            parts = set(filter(None, into[key].split(";")))
            parts |= set(filter(None, new.get(key, "").split(";")))
            into[key] = ";".join(sorted(parts))
    for key in ("github_id", "src_language"):
        if key in into:
            into[key] = into[key] or new.get(key, "")
    for key in ("src_stars", "ai_commits"):
        if key in into:
            into[key] = str(max(int(into[key] or 0), int(new.get(key) or 0)))
    if "published_m0" in into:
        # The earliest published date wins.
        dates = sorted(filter(None, [into["published_m0"], new.get("published_m0", "")]))
        into["published_m0"] = dates[0] if dates else ""


def main():
    p = argparse.ArgumentParser(
        description="Union the published source repository lists into one candidate table.")
    p.add_argument("-c", "--cursor", default="data/00_sources/cursor_study_repo_metrics.csv",
                   help="CursorStudy repo_metrics.csv (family A)")
    p.add_argument("-t", "--techdebt", default="data/00_sources/tech_debt_ai_coding.csv",
                   help="tech-debt-ai-coding AI repo list (family B)")
    p.add_argument("-a", "--aidev", default="data/00_sources/aidev_candidates.csv",
                   help="extract_aidev.py output (family B)")
    p.add_argument("-g", "--genai", default="data/00_sources/genai_python_only.txt",
                   help="Xiao/Tufano self-admission list (family C)")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    args = p.parse_args()

    # Keyed on the lowercased name; renames are merged by numeric id in github_meta.py.
    pool = {}
    for reader, path in ((from_cursor, args.cursor), (from_techdebt, args.techdebt),
                         (from_aidev, args.aidev), (from_genai, args.genai)):
        try:
            rows = list(reader(path))
        except (OSError, KeyError) as e:
            sys.exit(f"cannot read {path}: {e}")
        for row in rows:
            key = row["full_name"].strip().lower()
            if key in pool:
                merge(pool[key], row)
            else:
                pool[key] = {c: row.get(c, "") for c in COLUMNS}
        sys.stderr.write(f"{path}: {len(rows)} rows, pool now {len(pool)}\n")

    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="",
                                                     encoding="utf-8")
    try:
        w = csv.DictWriter(out, COLUMNS)
        w.writeheader()
        for key in sorted(pool):
            w.writerow(pool[key])
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
