# LLM code-quality study: replication package

Code and data for the bachelor thesis *Code Quality and Security Before and After LLM
Adoption: A Longitudinal Study of Open-Source Repositories* (Kirill Lygin, 2026): 117 Python
repositories, 25 monthly snapshots each (12 months either side of the first LLM marker),
measured with SonarQube, CodeQL, bandit, radon, lizard and complexipy.

## Reproduce the analysis

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make analysis
```

This rebuilds `data/12_merged/` and `data/13_analysis/` (result tables and figures) from the
shipped measurements in about a minute. With the pinned versions on Python 3.14 the results
match the thesis to within 1e-11, apart from a few tie-sensitive Wilcoxon statistics, which
can move in the second significant digit. In `paired_6m.csv` three Holm-adjusted p-values
lie at about 0.05 and can land on either side of it.

## Rerun selection and measurement

`make selection` and `make sample` run locally; `selection` needs `GITHUB_TOKEN` and reads live
GitHub metadata. Every other stage runs as Kubernetes Jobs. Always start them through `make`,
never with `kubectl apply` on a manifest:

```sh
export CLUSTER=<kubectl context> NS=<namespace> GITHUB_TOKEN=<token>
make date                                 # clone and date m0     -> data/04_history/
make sample                               # final sample          -> data/05_sample/
make scan SONARS="https://<host>=<admin token> ..."              # -> data/06_measures/
make codeql NODES="<node> ..." SONARS=x   # SONARS: any value     -> data/06_codeql/
make commits                              #                       -> data/07_commits/
make lint LINT_SHARDS=<number of NODES>   # after codeql: reuses its volumes -> data/11_lint/
make symlinks                             #                       -> data/11_symlinks/
```

Each cluster stage has matching `-status` and `-fetch` targets. Before `make scan`, deploy the
three SonarQube instances with
`kubectl kustomize --enable-helm k8s/sonarqube | kubectl -n "$NS" apply -f -`. First replace
the placeholder hosts (`sonar-*.example.org`) and the TLS secret (`sonar-tls`), and create the
secrets `sonarqube-jdbc` (key `jdbc-password`) and `sonarqube-monitoring` (key `passcode`).
Set `storageClassName: fast` in `k8s/codeql/` and `k8s/commits/` to a class on your cluster.
Tool versions are pinned in the Makefile and the manifests. A rerun clones today's GitHub; the
commit shas in the measurement CSVs identify the trees that were measured.

## Data

`data/` holds one directory per stage, numbered in pipeline order: 00–05 selection, 06–11
measurement, 12 one merged CSV per repository, 13 analysis. Measurement data covers only the
117 sample repositories. In `07_commits/`, the names, emails and GitHub handles of people are
replaced by unsalted SHA-256 hashes (`scripts/scan/pseudonymize_commits.py`), while bot and
tool identities are kept. Anyone who knows an email or handle can hash it and find it.

## Licence

MIT (see [LICENSE](LICENSE)), except `data/00_sources/`, which keeps the terms of its sources:
AIDev (Li et al., 2025), CursorStudy (He et al., 2025, doi:10.5281/zenodo.18368662),
tech-debt-ai-coding (Liu et al., 2026), and repository names from Xiao et al. (2025) and
Tufano et al. (2024).
