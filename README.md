# LLM code-quality study: replication package

Scripts, cluster manifests and data for the bachelor thesis *Code Quality and Security Before
and After LLM Adoption: A Longitudinal Study of Open-Source Repositories* (Kirill Lygin, 2026).

The study takes 117 Python repositories, each with an observable LLM-integration marker in its
history (a committed agent configuration file, agent-authored git metadata, or a self-admission
in a commit message). For each repository it measures 25 monthly snapshots, from 12 months before
the integration month m₀ to 12 months after, with SonarQube Community, CodeQL, bandit, radon,
lizard and complexipy. It then compares each repository with its own pre-marker baseline in
three layers: descriptive event-study series, a mixed-effects interrupted time series, and
paired pre/post medians. The design has no control group, so every result is an observational
association, not a causal effect.

This package contains only what the thesis reports. Superseded measurement passes, exploratory
scripts, and analyses that were dropped from the thesis are left out (see
[What is not included](#what-is-not-included)).

## Layout

```
data/      every pipeline stage, one numbered directory each (00-05 selection, 06-11
           measurement, 12 merged, 13 analysis)
k8s/       cluster manifests for the stages that clone and measure repositories
scripts/   dataset/ (selection), scan/ (measurement), analysis/ (merge and the three layers)
Makefile   drives every stage; local stages need only Python
```

## Reproducing the analysis

The analysis runs locally from the data in this package and takes about a minute.

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make analysis          # data/12_merged/ and data/13_analysis/ from the measurement stages
```

`make analysis` runs these steps in order; each can also be run as its own target:

| Target | Script | Output in `data/13_analysis/` |
| --- | --- | --- |
| `merged` | `merge.py` | `data/12_merged/*.csv`, one row per repository and snapshot month |
| `panels` | `outcomes.py` | `panel.csv` (21 normalised outcomes, long format) and the `panel_nocarry`, `panel_nobot`, `panel_nosmall` exclusion variants |
| `event-study` | `event_study.py` | layer 1: `event_series.csv`, `pretrend.csv`, `event_coefficients.csv`, `figures/event_study_rq{1,2}.png` |
| `its` | `its_model.py` | layer 2: `its_unadjusted.csv`, `its_adjusted.csv` (activity covariates), `its_slope.csv` (random slope), `its_{nobot,nocarry,nosmall}.csv` |
| `paired` | `paired.py` | layer 3: `paired.csv`, `paired_repos.csv`, `paired_6m.csv`, `paired_{nobot,nocarry,nosmall}.csv` |
| `terciles` | `terciles.py` | `dose_terciles.csv`, the layer-2 model refitted per tercile of total marker share (exploratory) |
| `tables` | `tables.py` | `tables.csv`, the three layers side by side per outcome, as in the thesis results tables |

The sensitivity panels exclude, respectively, the 39 carried snapshot months that repeat the
previous month's commit (`nocarry`), the 11 repositories whose m₀ was set by an automated review
or refactoring bot (`nobot`, list in the Makefile), and `elementary-data/dbt-data-reliability`,
whose four Python files are too small for a per-KLOC denominator (`nosmall`).

`tables.csv` prints the raw "explained" ratio. The thesis reports it as "n/a" where the paired
median difference is essentially zero, and as ">200" for SonarQube vulnerabilities.

### How closely a rerun matches

The files in `data/13_analysis/` are the outputs the thesis reports. Rerunning `make analysis`
with the versions in `requirements.txt` overwrites them and gives the following:

- The panels, event-study series, pre-trend tests and all ITS coefficients, standard errors
  and variance components match to within a relative 1e-9.
- Wilcoxon statistics differ where scipy's tie handling changed between versions. `W` for
  debt ratio changes from 599.5 to 599, and the distinct-CWE p-value moves in its third
  significant digit (2.80e-5 to 2.84e-5). No conclusion changes.
- The `slope_converged` flag in `its_slope.csv` flips for five outcomes (six non-converged fits
  instead of seven), while the estimates are identical.
- The `bandit_high_per_kloc` fit of the high tercile, which the thesis does not report, lands
  on a slightly different optimum (b3 p = 0.17 instead of 0.18).

The library versions of the original run were not recorded.

## Reproducing selection and measurement

These stages clone every repository from GitHub and measure every snapshot. They ran on a
Kubernetes cluster and are driven by the Makefile. Every `k8s/*` Job manifest is a template
with `__PLACEHOLDER__` fields that the matching Makefile section fills in, so always run a stage
through `make`, never with `kubectl apply -f` on a manifest. Each stage refuses to run
unless `CLUSTER` (a kubectl context) and `NS` (a namespace) are set, and it needs
`GITHUB_TOKEN`. Rerunning a stage now measures today's GitHub, which may no longer hold the
exact histories that were measured. The snapshot commit shas recorded in every measurement CSV
pin the trees that were measured.

| Stage | Make target | Script(s) | Output |
| --- | --- | --- | --- |
| 00 → 03 selection | `selection` (local, needs `GITHUB_TOKEN`) | `extract_aidev.py`, `union_candidates.py`, `github_meta.py`, `filter_pool.py` | `01_union/`, `02_metadata/`, `03_pool/` |
| 03 → 04 m₀ dating | `date`, `date-status`, `date-fetch` | `date_repo.py` in `k8s/dataset/` | `04_history/history.csv` |
| 04 → 05 sample | `sample` (local) | `filter_pool.py --history` | `05_sample/sample.csv`, `funnel.md` |
| SonarQube | `scan`, `scan-monitor`, `scan-fetch` | `scan_repo.py`, `filter_scanned.py`, `collect_measures.py` | `06_measures/measures.csv` |
| CodeQL | `codeql`, `codeql-fetch` (needs `NODES`) | `codeql_repo.py` | `06_codeql/` |
| Commit history | `commits`, `commits-fetch` | `commits_repo.py` | `07_commits/` |
| Linters | `lint`, `lint-fetch` | `lint_repo.py`, `bandit.yaml` | `11_lint/` |
| Symlink audit | `symlinks`, `symlinks-fetch` | `symlinks_repo.py` | `11_symlinks/symlinks.csv` |

Every measurement stage uses `snapshot_commits.py` to resolve the snapshot of each event month:
the last commit on the first-parent chain of the default branch before the end of the month
(UTC). A month without a commit carries the previous snapshot forward and is flagged
`carried = 1`. `sample` reproduces `05_sample/sample.csv` exactly from `03_pool/` and
`04_history/`, including the final step that drops the three repositories radon could not
finish (`MEASUREMENT_DROPS` in the Makefile). `selection` reproduces stages 00 and 01 exactly.
Stage 03 comes out with the same 1,171 rows in a different order. Stage 02 depends on live
GitHub metadata.

### Pinned tools

| Tool | Version | Where it is pinned |
| --- | --- | --- |
| SonarQube Community | Helm chart `sonarqube` 2026.4.1, three independent instances with their own PostgreSQL | `k8s/sonarqube/kustomization.yaml` |
| sonar-scanner-cli | 12.1.0.3233_8.0.1 | `k8s/sonarqube/scan-job.yaml` |
| CodeQL | bundle v2.26.3, `python-security-extended` suite | `BUNDLE` in the Makefile, `SUITE` in `codeql_repo.py` |
| bandit / radon / lizard / complexipy | 1.9.4 / 6.0.1 / 1.24.0 / 7.0.1 | `*_V` in the Makefile |
| Python in the Jobs | 3.13 | the Job manifests |

Every tool scans the same file scope: `INCLUSIONS` and `EXCLUSIONS` in `scripts/scan/scan_repo.py`
(tests, vendored, generated and dependency code excluded), applied as git wildmatch pathspecs.
Tracked symlinks are deleted from each snapshot before it is scanned, so no file is measured
twice.

### SonarQube deployment

`k8s/sonarqube/kustomization.yaml` inflates three SonarQube Community instances (`sq-a`, `sq-b`,
`sq-c`) and their PostgreSQL databases from Helm charts. Each snapshot is its own project
(`ba_<owner>_<name>__<YYYY-MM>`), because a server refuses an analysis dated before a
project's last one, which would block any resumed or out-of-order run. A hash of the project
key assigns each snapshot to one of the three instances. Before deploying, replace the placeholder ingress hosts
(`sonar-*.example.org`) and TLS secret (`sonar-tls`) with your own, and create the
`sonarqube-jdbc` (key `jdbc-password`) and `sonarqube-monitoring` (key `passcode`) secrets. Then
run:

```sh
kubectl kustomize --enable-helm k8s/sonarqube | kubectl -n "$NS" apply -f -
```

The scan Job receives the instances as `SONARS="https://sonar-a.example.org=<admin token> ..."`.
The `storageClassName: fast` in the CodeQL and commit manifests names a class on the original
cluster; change it to one that exists on yours.

## Data

All CSVs are UTF-8 with a header row. Repositories are identified as `owner/name`, and file
names use `owner__name`. Measurement CSVs key rows by `repo, month` (the calendar month of the
snapshot, `YYYY-MM`), `offset` (event month, m₀ = 0) and `sha` (the measured commit).

| Directory | Contents |
| --- | --- |
| `00_sources/` | The four published candidate lists: AIDev (`aidev_all_repository.parquet`, sliced to Python and ≥100 stars as `aidev_candidates.csv`), the CursorStudy `repo_metrics.csv` (`cursor_study_repo_metrics.csv`), the tech-debt-ai-coding repository list (`tech_debt_ai_coding.csv`), and the repository names from two self-admission studies (`genai_python_only.txt`). `paper_datasets.md` records where each came from. |
| `01_union/` | 7,320 name-unique candidates with their marker families (A agent file, B git metadata, C self-admission) and sources. |
| `02_metadata/` | The same candidates with GitHub metadata, deduplicated on the numeric repository id. |
| `03_pool/` | 1,171 candidates passing the metadata gates, the gate funnel, and the names handed to the dating stage. |
| `04_history/` | Per cloned candidate: m₀, first marker month and commit per family, activity on each side of m₀ counted on the first-parent chain, sustained onset, and in-scope Python LOC at the window end. |
| `05_sample/` | The final 117 repositories with tier and rank (`sample.csv`), and the clone-side funnel. |
| `06_measures/` | SonarQube measures per snapshot (2,923 rows; two snapshots with no in-scope file are left blank; `ai-shifu/ai-shifu` has no first-parent commit in its first two months). `server` names the instance that scanned it. |
| `06_codeql/` | CodeQL per snapshot: `measures_<shard>_<index>.csv` (alert counts by severity, CWE counts, extraction statistics) and `results_*.jsonl` (one line per alert). |
| `07_commits/` | Every commit of each repository's 25-month window with change size (overall and within the measured scope), LLM marker family, tool and signal, bot flag, and message. |
| `10_readmes/` | Repository metadata and README-based classification (ownership, type, domain, commercial backing), used for the sample description. |
| `11_lint/` | bandit, radon, lizard and complexipy per snapshot: `measures_*.csv` and `bandit_*.jsonl` (one line per finding). The `*_sym_*` files are the rescan of the four repositories with in-scope file symlinks. |
| `11_symlinks/` | Audit of every tracked symlink in every snapshot and how many measured files a file-system walk would see twice. |
| `12_merged/` | One CSV per repository: the spine (`repo, month, offset, period, sha, commit_date, carried`) followed by `sonar_*`, `codeql_*`, `lint_*` and `act_*` (monthly commit activity) columns. |
| `13_analysis/` | The outcome panels and every result table of the three layers (see above). |

### Differences from the data as collected

- **Restricted to the final sample.** The measurement directories held rows for 129
  repositories: the 117 in the sample and 12 dropped after measurement had begun (two above
  the 1M-LOC bound, seven rejected by the activity gate once it was counted on the snapshot
  branch, three radon could not finish). Only the 117 are included. Stages 00-04 keep every
  candidate, because they document the selection funnel.
- **Personal data pseudonymised.** In `07_commits/`, every author and committer name and email
  is replaced by a salted SHA-256 pseudonym, one per lowercased email. The same applies to email
  addresses and to the names in `Co-authored-by`, `Signed-off-by` and similar trailers inside
  commit messages. The salt was discarded. Tool and bot identities that are marker evidence
  (`noreply@anthropic.com`, `Copilot`, `cursoragent@cursor.com`, `*[bot]` and GitHub's own
  accounts) are kept verbatim. Monthly author counts computed from the pseudonymised files equal
  those of the original. `scripts/scan/pseudonymize_commits.py` is the script that did this.
  GitHub handles that appear in free-text messages or branch names are not rewritten.
- **SonarQube server URLs relabelled.** The instance URL in `06_measures/server` and
  `12_merged/sonar_server` is replaced by the instance name (`sq-a`, `sq-b`, `sq-c`).
- **`12_merged/` regenerated.** The original join was done outside the scripts. `merge.py`
  reproduces it, and the 117 files are byte-identical to the original apart from the relabelled
  server and one column: the original wrote the monthly commit count into `act_reverts`, which
  now holds the number of revert commits. No analysis reads `act_reverts`.

### Known limitations of the data

- The three measurement stages do not see exactly the same file set: the linters agree with
  SonarQube on the file count in 84% of snapshots and with CodeQL in 87%, with a median residual
  difference of one file. Each per-KLOC outcome therefore uses the size denominator of its own
  tool.
- In 11 repositories, m₀ is set by the installation of an automated review or refactoring bot
  (CodeRabbit, Sourcery), which can itself move the metrics. The `nobot` panel excludes them.
- `marker_commits` and `marker_share` in `04_history/history.csv` and `05_sample/sample.csv`
  count a commit twice when it carries agent metadata and also touches an agent file. The
  analysis uses `act_marker_commits` from `07_commits/`, which counts each commit once.
- 96 of the 117 m₀ fall between 2025-03 and 2025-07, so post-marker time is close to
  collinear with calendar time.
- CodeQL finds nothing in any of the 25 months for 25 repositories, so the CodeQL outcomes are
  mostly zeros.
- The script docstrings cite sections of `plan.md`, the working plan of the study. Its rules are
  stated in the thesis methodology chapter.

## What is not included

- The first two linter passes (`08_lint`, `09_lint`), superseded by `11_lint` after the
  file-exclusion fix.
- The curated AIDev slice `aidev_repository.parquet`, since the candidates are derived from the
  full table.
- Stale bookkeeping (`05_sample/pending.csv`, `sample.csv.bak`).
- The marker-intensity dose-response models, the dose trajectory and the heterogeneity analysis
  by repository classification (`dose_model.py`, `dose_groups.py` and their outputs), which were
  removed from the thesis. `terciles.py` keeps only the tercile refit, with code copied
  unchanged from `dose_model.py`.
- An activity difference-in-differences exploration and a pre-draft summary table
  (`did_*.csv`, `summary.csv`), which the thesis does not use.
- Early single-figure plotting scripts written before the outcome panel existed.

## Licence

The scripts, manifests, Makefile and the data produced by this study are released under the
MIT licence (see [LICENSE](LICENSE)). The exception is `data/00_sources/`, described below.

### Third-party data

The files in `data/00_sources/` come from other studies and keep their original terms: AIDev
(Li et al., 2025), CursorStudy (He et al., 2025, doi:10.5281/zenodo.18368662) and
tech-debt-ai-coding (Liu et al., 2026). From the two self-admission studies (Xiao et al., 2025;
Tufano et al., 2024), which declare no licence, only repository names are taken. Please cite
these studies if you reuse these files.
