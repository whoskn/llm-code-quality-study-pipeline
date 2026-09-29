#!/usr/bin/env python3
"""Extract candidate repos (family B) from the AIDev repository table -> CSV."""
import argparse
import sys

import pandas as pd

HF = "https://huggingface.co/datasets/hao-li/AIDev/resolve/main"
COLUMNS = ["source", "family", "id", "full_name", "language", "stars", "forks", "license"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-r", "--repos", default=f"{HF}/all_repository.parquet",
                   help="repository parquet, local or URL (default: Hugging Face "
                        "all_repository.parquet; the star gate below re-derives the "
                        "curated slice)")
    p.add_argument("-l", "--language", default="Python",
                   help="keep only this primary language, 'any' to skip (default: Python)")
    p.add_argument("-s", "--min-stars", type=int, default=100,
                   help="minimum stars (default: 100)")
    p.add_argument("-o", "--output", default="-",
                   help="CSV output file, '-' for stdout (default: -)")
    args = p.parse_args()

    try:
        df = pd.read_parquet(args.repos)
    except Exception as e:
        sys.exit(f"cannot read {args.repos}: {e}")

    # all_repository types the counts as float, since it carries rows with missing values
    df = df.astype({"stars": "Int64", "forks": "Int64"})
    n = len(df)
    if args.language != "any":
        df = df[df["language"] == args.language]
    df = df[df["stars"] >= args.min_stars]
    sys.stderr.write(f"{n} repos in table, {len(df)} kept\n")

    df = df.assign(source="aidev", family="B").sort_values("stars", ascending=False)
    df[COLUMNS].to_csv(sys.stdout if args.output == "-" else args.output, index=False)


if __name__ == "__main__":
    main()
