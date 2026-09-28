# Replication pipeline for "Code Quality and Security Before and After LLM Adoption".
#
# Local stages (no cluster): `make selection` rebuilds stages 01-03 and `make sample` stage 05
# from the files under data/; `make analysis` rebuilds data/12_merged/ and data/13_analysis/
# from the measurement stages. The cluster stages further down (date, scan, codeql, commits,
# lint, symlinks) clone every repository and measure every monthly snapshot; they refuse to
# run without CLUSTER and NS (and SONARS for SonarQube) set in the environment.

PY ?= python3
export PYTHONPATH := .

# ==================== Repository selection (stages 00-05) ====================

# The three repositories that passed every gate but that radon could not finish within the
# lint stage's per-tool timeout; dropped after measurement had begun (120 -> 117).
MEASUREMENT_DROPS := pwndbg/pwndbg intel/auto-round HarrisonKramer/optiland

.PHONY: selection sample

# AIDev slice -> union of the four source lists -> GitHub metadata -> metadata gates.
# The dating stage clones the pool under each repository's current GitHub name.
# github_meta.py needs GITHUB_TOKEN and reflects GitHub as of the day it runs.
selection:
	$(PY) scripts/dataset/extract_aidev.py -r data/00_sources/aidev_all_repository.parquet \
	  -o data/00_sources/aidev_candidates.csv
	$(PY) scripts/dataset/union_candidates.py -o data/01_union/candidates.csv
	$(PY) scripts/dataset/github_meta.py -i data/01_union/candidates.csv \
	  -o data/02_metadata/candidates_meta.csv
	$(PY) scripts/dataset/filter_pool.py -i data/02_metadata/candidates_meta.csv \
	  -o data/03_pool/pool_meta.csv -r data/03_pool/funnel_metadata.md
	$(PY) -c "import csv; [print(r['gh_full_name']) for r in csv.DictReader(open('data/03_pool/pool_meta.csv'))]" \
	  > data/03_pool/pool_names.txt

# Clone-side gates, tiers and ranking over the dated history, minus the measurement drops.
sample:
	$(PY) scripts/dataset/filter_pool.py -i data/03_pool/pool_meta.csv \
	  -H data/04_history/history.csv -r data/05_sample/funnel.md \
	  | awk -F, 'BEGIN { split("$(MEASUREMENT_DROPS)", d, " "); for (i in d) drop[d[i]] = 1 } \
	             !($$1 in drop)' > data/05_sample/sample.csv
	@echo "data/05_sample/sample.csv: $$(($$(wc -l < data/05_sample/sample.csv) - 1)) repos"


# ==================== Analysis (stages 12-13) ====================

A       := data/13_analysis
FIGURES := $(A)/figures
# m0 set by an automated review/refactoring bot (CodeRabbit, Sourcery) rather than a person
BOT_REPOS := litestar-org/litestar meltano/meltano gdsfactory/gdsfactory ansys/pymapdl \
             snakemake/snakemake vacanza/holidays deepmodeling/deepmd-kit \
             home-assistant/supervisor OWASP/Nettacker sartography/spiff-arena unclecode/crawl4ai
# four Python files and under 1 KLOC: a per-KLOC denominator is noise
SMALL_REPOS := elementary-data/dbt-data-reliability
VARIANTS := nobot nocarry nosmall
comma := ,
space := $(subst ,, )

.PHONY: analysis merged panels event-study its paired terciles tables sample-figures

analysis: merged panels event-study its paired terciles tables sample-figures

# One CSV per sample repository joining SonarQube, CodeQL, the linters and commit activity.
merged:
	$(PY) scripts/analysis/merge.py -o data/12_merged

# The tidy event-time panel and its three exclusion variants.
panels:
	$(PY) scripts/analysis/outcomes.py -o $(A)/panel.csv
	$(PY) scripts/analysis/outcomes.py -c -o $(A)/panel_nocarry.csv
	$(PY) scripts/analysis/outcomes.py -x "$(subst $(space),$(comma),$(strip $(BOT_REPOS)))" -o $(A)/panel_nobot.csv
	$(PY) scripts/analysis/outcomes.py -x "$(SMALL_REPOS)" -o $(A)/panel_nosmall.csv

# Layer 1: baseline-indexed event-study series and figures.
event-study:
	$(PY) scripts/analysis/event_study.py -p $(A)/panel.csv -o $(A)/event_series.csv \
	  -f $(FIGURES)/event_study

# Layer 2: fixed-effects ITS with repository-clustered SEs, unadjusted, activity-adjusted,
# with a per-repository slope, and per variant.
its:
	$(PY) scripts/analysis/its_model.py -p $(A)/panel.csv -o $(A)/its_unadjusted.csv
	$(PY) scripts/analysis/its_model.py -p $(A)/panel.csv -a -o $(A)/its_adjusted.csv
	$(PY) scripts/analysis/its_model.py -p $(A)/panel.csv -r -o $(A)/its_slope.csv
	for v in $(VARIANTS); do \
	  $(PY) scripts/analysis/its_model.py -p $(A)/panel_$$v.csv -o $(A)/its_$$v.csv || exit 1; \
	done

# Layer 3: paired pre/post medians, 12- and 6-month windows, and per variant.
paired:
	$(PY) scripts/analysis/paired.py -p $(A)/panel.csv -o $(A)/paired.csv
	$(PY) scripts/analysis/paired.py -p $(A)/panel.csv -w 6 -W 6 -o $(A)/paired_6m.csv
	for v in $(VARIANTS); do \
	  $(PY) scripts/analysis/paired.py -p $(A)/panel_$$v.csv -o $(A)/paired_$$v.csv || exit 1; \
	done

# Exploratory: the layer-2 model refitted inside each tercile of total marker share.
terciles:
	$(PY) scripts/analysis/terciles.py -p $(A)/panel.csv -o $(A)/dose_terciles.csv

# The three layers side by side per outcome, as in the thesis results tables.
tables:
	$(PY) scripts/analysis/tables.py -i $(A)/its_unadjusted.csv -p $(A)/paired.csv -o $(A)/tables.csv

# The selection funnel and the sample composition (marker month, licence, domain).
sample-figures:
	$(PY) scripts/analysis/sample_figures.py -d data -o $(FIGURES)


# ==================== m0 Dating (stage 03 -> 04) ====================

CLUSTER  ?=
NS       ?=
KUBECTL   = kubectl --context=$(CLUSTER) -n $(NS)

.PHONY: date-check date date-status date-fetch date-clean

date-check:
	@test -n "$(CLUSTER)" || { echo "CLUSTER is not set"; exit 1; }
	@test -n "$(NS)" || { echo "NS is not set"; exit 1; }

# Push the dating script, the pool names and the token, then replace the StatefulSet so
# every pod mounts the current script.
date: date-check
	$(KUBECTL) create configmap ba-date-scripts --from-file=scripts/dataset/date_repo.py \
	  --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create configmap ba-pool --from-file=data/03_pool/pool_names.txt \
	  --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) delete statefulset ba-dater --ignore-not-found
	$(KUBECTL) apply -f k8s/dataset/date-shards.yaml

# Ready pods are shards that have finished.
date-status: date-check
	$(KUBECTL) get pods -l app=ba-dater

# Concatenate the ten shard CSVs into one history, header once.
date-fetch: date-check
	@mkdir -p data/04_history
	@for i in 0 1 2 3 4 5 6 7 8 9; do \
	  $(KUBECTL) exec ba-dater-$$i -- cat /data/history_$$i.csv | { [ $$i = 0 ] && cat || tail -n +2; }; \
	done > data/04_history/history.csv
	@echo "data/04_history/history.csv: $$(($$(wc -l < data/04_history/history.csv) - 1)) rows"

# Delete the StatefulSet but keep its claims, so a rerun resumes.
date-clean: date-check
	$(KUBECTL) delete statefulset ba-dater --ignore-not-found


# ==================== Sonarqube Scan Job ==================== 

SONARS   ?=
PAR      ?= 35
PRE      ?= 12
POST     ?= 12
CSV      ?= data/05_sample/sample.csv
PENDING  := data/05_sample/pending.csv
SCAN_SRC := scripts/scan/scan_repo.py scripts/scan/snapshot_commits.py

scan_rows = $$(awk 'END{print NR-1}' $(1))
scan_maxfail = $$(awk 'END{n=int((NR-1)/5); print (n<1 ? 1 : n)}' $(1))

.PHONY: scan-check scan-config scan scan-logs scan-status scan-clean scan-monitor scan-fetch

# Refuse to run unless the cluster, namespace and SonarQube replica list are all set.
scan-check:
	@test -n "$(CLUSTER)" || { echo "CLUSTER is not set"; exit 1; }
	@test -n "$(NS)" || { echo "NS is not set"; exit 1; }
	@test -n "$(SONARS)" || { echo "SONARS is not set (e.g. SONARS=\"https://sonar-a.example.org=squ_a\")"; exit 1; }

# Push the current scripts, the still-unscanned rows and the two secrets into the namespace.
scan-config: scan-check
	@test -f $(CSV) || { echo "no such CSV: $(CSV)"; exit 1; }
	$(KUBECTL) create configmap ba-scan-scripts \
	  $(addprefix --from-file=,$(SCAN_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(PY) scripts/scan/filter_scanned.py $(CSV) --servers="$(SONARS)" \
	  --pre-months=$(PRE) --post-months=$(POST) > $(PENDING)
	@test $(call scan_rows,$(PENDING)) -gt 0 || { echo "nothing left to scan"; exit 1; }
	$(KUBECTL) create configmap ba-scan-sample \
	  --from-file=sample.csv=$(PENDING) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create secret generic sonar-servers \
	  --from-literal=servers="$(SONARS)" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -

# Replace the scanner Job with one sized to the pending rows; Job specs are immutable.
scan: scan-config
	$(KUBECTL) delete job ba-scanner --ignore-not-found
	sed -e "s|__COMPLETIONS__|$(call scan_rows,$(PENDING))|" \
	  -e "s|__PARALLELISM__|$(PAR)|" \
	  -e "s|__MAXFAILED__|$(call scan_maxfail,$(PENDING))|" \
	  -e "s|__PRE__|$(PRE)|" -e "s|__POST__|$(POST)|" \
	  k8s/sonarqube/scan-job.yaml | $(KUBECTL) apply -f -

# How many repos the scanner has finished, failed, and which indexes are done.
scan-status: scan-check
	$(KUBECTL) get job ba-scanner \
	  -o custom-columns=SUCCEEDED:.status.succeeded,FAILED:.status.failed,DONE:.status.completedIndexes
	$(KUBECTL) get pods -l job-name=ba-scanner

# The pods' progress trace, and the only record of a snapshot that failed to scan.
scan-logs: scan-check
	$(KUBECTL) logs job/ba-scanner --all-containers --prefix --tail=-1

# Delete the scanner Job; SonarQube keeps the results, so this loses nothing.
scan-clean: scan-check
	$(KUBECTL) delete job ba-scanner --ignore-not-found

# Live progress across the SonarQube instances.
scan-monitor: scan-check
	while :; do clear; $(PY) -m scripts.scan.monitor_scans $(CSV) 2>&1; sleep 10; done

# Read every analysed snapshot's measures off the instances; blank rows that measured nothing.
scan-fetch: scan-check
	@mkdir -p data/06_measures
	$(PY) scripts/scan/collect_measures.py --servers="$(SONARS)" -o data/06_measures/measures.csv


# ==================== CodeQL ==================== 

NODES      ?=
PER_NODE   ?= 2
BUNDLE     ?= codeql-bundle-v2.26.3
OUT        ?= data/06_codeql
CODEQL_SRC := scripts/scan/codeql_repo.py scripts/scan/scan_repo.py \
              scripts/scan/snapshot_commits.py
shards      = $(words $(NODES))

slice = awk -v n=$(shards) -v s=$(1) 'NR==1 || (NR-2) % n == s' $(CSV)

.PHONY: codeql-check codeql-config codeql codeql-status codeql-logs codeql-fetch codeql-clean

# Same checks as the SonarQube stage, plus the node list this one is pinned to.
codeql-check: scan-check
	@test -n "$(NODES)" || { echo "NODES is not set (e.g. NODES=\"node-a node-b\")"; exit 1; }
	@test -f $(CSV) || { echo "no such CSV: $(CSV)"; exit 1; }

# Push the scripts, the token and one round-robin sample slice per node.
codeql-config: codeql-check
	$(KUBECTL) create configmap ba-codeql-scripts \
	  $(addprefix --from-file=,$(CODEQL_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@s=0; for n in $(NODES); do \
	  $(call slice,$$s) > /tmp/ba-codeql-sample-$$s.csv; \
	  echo "shard $$s -> $$n: $$(($$(wc -l < /tmp/ba-codeql-sample-$$s.csv) - 1)) repos"; \
	  $(KUBECTL) create configmap ba-codeql-sample-$$s \
	    --from-file=sample.csv=/tmp/ba-codeql-sample-$$s.csv --dry-run=client -o yaml \
	    | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Replace one Job per node, each sized to its own slice.
codeql: codeql-config
	@s=0; for n in $(NODES); do \
	  c=$$(($$(wc -l < /tmp/ba-codeql-sample-$$s.csv) - 1)); \
	  f=$$((c / 5)); [ $$f -ge 1 ] || f=1; \
	  p=$(PER_NODE); [ $$p -le $$c ] || p=$$c; \
	  $(KUBECTL) delete job ba-codeql-$$s --ignore-not-found; \
	  sed -e "s|__SHARD__|$$s|g" -e "s|__NODE__|$$n|" -e "s|__COMPLETIONS__|$$c|" \
	      -e "s|__PARALLELISM__|$$p|" -e "s|__MAXFAILED__|$$f|" -e "s|__BUNDLE__|$(BUNDLE)|g" \
	    k8s/codeql/codeql-job.yaml | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Per-shard progress across all the CodeQL Jobs.
codeql-status: scan-check
	$(KUBECTL) get jobs -l app=ba-codeql \
	  -o custom-columns=JOB:.metadata.name,SUCCEEDED:.status.succeeded,FAILED:.status.failed,DONE:.status.completedIndexes
	$(KUBECTL) get pods -l app=ba-codeql -o wide

# Concatenated logs of every shard.
codeql-logs: scan-check
	@for j in $$($(KUBECTL) get jobs -l app=ba-codeql -o name); do \
	  $(KUBECTL) logs $$j --all-containers --prefix --tail=-1; \
	done

# Copy the measured rows off each node's claim, which cannot be read remotely.
codeql-fetch: codeql-check
	@mkdir -p $(OUT)
	@s=0; for n in $(NODES); do \
	  echo "fetching shard $$s from $$n"; \
	  $(KUBECTL) delete pod ba-codeql-fetch --ignore-not-found --now >/dev/null; \
	  $(KUBECTL) run ba-codeql-fetch --image=busybox --restart=Never \
	    --overrides="{\"spec\":{\"nodeSelector\":{\"kubernetes.io/hostname\":\"$$n\"},\"containers\":[{\"name\":\"ba-codeql-fetch\",\"image\":\"busybox\",\"command\":[\"sleep\",\"300\"],\"volumeMounts\":[{\"name\":\"s\",\"mountPath\":\"/state\"}]}],\"volumes\":[{\"name\":\"s\",\"persistentVolumeClaim\":{\"claimName\":\"ba-codeql-out-$$s\"}}]}}" >/dev/null; \
	  $(KUBECTL) wait --for=condition=Ready pod/ba-codeql-fetch --timeout=120s >/dev/null; \
	  $(KUBECTL) cp ba-codeql-fetch:/state/out/. $(OUT) 2>/dev/null; \
	  $(KUBECTL) delete pod ba-codeql-fetch --now >/dev/null; \
	  s=$$((s + 1)); \
	done
	@echo "$(OUT): $$(ls $(OUT) | wc -l) files"

# Delete the CodeQL Jobs but keep the claims, so a rerun resumes.
codeql-clean: scan-check
	$(KUBECTL) delete jobs -l app=ba-codeql


# ==================== Commit History ==================== 

SHARDS       ?= 3
COMMITS_OUT  ?= data/07_commits
COMMITS_SRC  := scripts/scan/commits_repo.py scripts/scan/scan_repo.py \
                scripts/scan/snapshot_commits.py
DATASET_SRC  := scripts/dataset/date_repo.py

commits_slice = awk -v n=$(SHARDS) -v s=$(1) 'NR==1 || (NR-2) % n == s' $(CSV)

.PHONY: commits-check commits-config commits commits-status commits-logs commits-fetch commits-clean

# Same checks as the SonarQube stage minus the server list, which this stage has no use for.
commits-check:
	@test -n "$(CLUSTER)" || { echo "CLUSTER is not set"; exit 1; }
	@test -n "$(NS)" || { echo "NS is not set"; exit 1; }
	@test -f $(CSV) || { echo "no such CSV: $(CSV)"; exit 1; }
	@test -n "$$GITHUB_TOKEN" || { echo "GITHUB_TOKEN is not set"; exit 1; }

# Push the scripts, the token and one round-robin sample slice per shard.
commits-config: commits-check
	$(KUBECTL) create configmap ba-commits-scripts \
	  $(addprefix --from-file=,$(COMMITS_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create configmap ba-commits-dataset \
	  $(addprefix --from-file=,$(DATASET_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@s=0; while [ $$s -lt $(SHARDS) ]; do \
	  $(call commits_slice,$$s) > /tmp/ba-commits-sample-$$s.csv; \
	  echo "shard $$s: $$(($$(wc -l < /tmp/ba-commits-sample-$$s.csv) - 1)) repos"; \
	  $(KUBECTL) create configmap ba-commits-sample-$$s \
	    --from-file=sample.csv=/tmp/ba-commits-sample-$$s.csv --dry-run=client -o yaml \
	    | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Replace one Job per shard, each sized to its own slice.
commits: commits-config
	@s=0; while [ $$s -lt $(SHARDS) ]; do \
	  c=$$(($$(wc -l < /tmp/ba-commits-sample-$$s.csv) - 1)); \
	  f=$$((c / 5)); [ $$f -ge 1 ] || f=1; \
	  $(KUBECTL) delete job ba-commits-$$s --ignore-not-found; \
	  sed -e "s|__SHARD__|$$s|g" -e "s|__COMPLETIONS__|$$c|" -e "s|__MAXFAILED__|$$f|" \
	      -e "s|__PRE__|$(PRE)|" -e "s|__POST__|$(POST)|" \
	    k8s/commits/commits-job.yaml | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Per-shard progress across all the commit Jobs.
commits-status: commits-check
	$(KUBECTL) get jobs -l app=ba-commits \
	  -o custom-columns=JOB:.metadata.name,SUCCEEDED:.status.succeeded,FAILED:.status.failed,DONE:.status.completedIndexes
	$(KUBECTL) get pods -l app=ba-commits -o wide

# Concatenated logs of every shard.
commits-logs: commits-check
	@for j in $$($(KUBECTL) get jobs -l app=ba-commits -o name); do \
	  $(KUBECTL) logs $$j --all-containers --prefix --tail=-1; \
	done

# Copy the per-repo CSVs off each shard's claim, which cannot be read remotely.
commits-fetch: commits-check
	@mkdir -p $(COMMITS_OUT)
	@s=0; while [ $$s -lt $(SHARDS) ]; do \
	  echo "fetching shard $$s"; \
	  $(KUBECTL) delete pod ba-commits-fetch --ignore-not-found --now >/dev/null; \
	  $(KUBECTL) run ba-commits-fetch --image=busybox --restart=Never \
	    --overrides="{\"spec\":{\"containers\":[{\"name\":\"ba-commits-fetch\",\"image\":\"busybox\",\"command\":[\"sleep\",\"600\"],\"volumeMounts\":[{\"name\":\"s\",\"mountPath\":\"/state\"}]}],\"volumes\":[{\"name\":\"s\",\"persistentVolumeClaim\":{\"claimName\":\"ba-commits-out-$$s\"}}]}}" >/dev/null; \
	  $(KUBECTL) wait --for=condition=Ready pod/ba-commits-fetch --timeout=180s >/dev/null; \
	  $(KUBECTL) cp ba-commits-fetch:/state/out/. $(COMMITS_OUT) 2>/dev/null; \
	  $(KUBECTL) delete pod ba-commits-fetch --now >/dev/null; \
	  s=$$((s + 1)); \
	done
	@echo "$(COMMITS_OUT): $$(ls $(COMMITS_OUT) | wc -l) files"

# Delete the commit Jobs but keep the claims, so a rerun resumes.
commits-clean: commits-check
	$(KUBECTL) delete jobs -l app=ba-commits


# ==================== Linter Suite ==================== 

LINT_SHARDS ?= 12
LINT_OUT    ?= data/11_lint
# suffix of /state/lint on the claim: a second pass needs its own directory, since the
# measures CSV of the first is what tells this stage a month is already done
LINT_RUN    ?=
COMPLEXIPY_V ?= 7.0.1
RADON_V      ?= 6.0.1
LIZARD_V     ?= 1.24.0
BANDIT_V     ?= 1.9.4
LINT_PINS   := complexipy==$(COMPLEXIPY_V) radon==$(RADON_V) \
               lizard==$(LIZARD_V) bandit==$(BANDIT_V)
LINT_SRC    := scripts/scan/lint_repo.py scripts/scan/scan_repo.py \
               scripts/scan/snapshot_commits.py scripts/scan/bandit.yaml

lint_slice = awk -v n=$(LINT_SHARDS) -v s=$(1) 'NR==1 || (NR-2) % n == s' $(CSV)

.PHONY: lint-check lint-config lint lint-status lint-logs lint-fetch lint-clean

# Same checks as the commit stage: this one has no server and no node list.
lint-check:
	@test -n "$(CLUSTER)" || { echo "CLUSTER is not set"; exit 1; }
	@test -n "$(NS)" || { echo "NS is not set"; exit 1; }
	@test -f $(CSV) || { echo "no such CSV: $(CSV)"; exit 1; }
	@test -n "$$GITHUB_TOKEN" || { echo "GITHUB_TOKEN is not set"; exit 1; }

# Push the scripts, the token and one round-robin sample slice per shard.
lint-config: lint-check
	$(KUBECTL) create configmap ba-lint-scripts \
	  $(addprefix --from-file=,$(LINT_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@s=0; while [ $$s -lt $(LINT_SHARDS) ]; do \
	  $(call lint_slice,$$s) > /tmp/ba-lint-sample-$$s.csv; \
	  echo "shard $$s: $$(($$(wc -l < /tmp/ba-lint-sample-$$s.csv) - 1)) repos"; \
	  $(KUBECTL) create configmap ba-lint-sample-$$s \
	    --from-file=sample.csv=/tmp/ba-lint-sample-$$s.csv --dry-run=client -o yaml \
	    | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Replace one Job per shard, each sized to its own slice. One pod per shard: the claim is
# ReadWriteOnce and a second pod would have to land on the same node to mount it.
lint: lint-config
	@s=0; while [ $$s -lt $(LINT_SHARDS) ]; do \
	  c=$$(($$(wc -l < /tmp/ba-lint-sample-$$s.csv) - 1)); \
	  f=$$((c / 5)); [ $$f -ge 1 ] || f=1; \
	  $(KUBECTL) delete job ba-lint-$$s --ignore-not-found; \
	  sed -e "s|__SHARD__|$$s|g" -e "s|__COMPLETIONS__|$$c|" -e "s|__PARALLELISM__|1|" \
	      -e "s|__MAXFAILED__|$$f|" -e "s|__PRE__|$(PRE)|" -e "s|__POST__|$(POST)|" \
	      -e "s|__PINS__|$(LINT_PINS)|g" -e "s|__RUN__|$(LINT_RUN)|g" \
	    k8s/lint/lint-job.yaml | $(KUBECTL) apply -f -; \
	  s=$$((s + 1)); \
	done

# Per-shard progress across all the linter Jobs.
lint-status: lint-check
	$(KUBECTL) get jobs -l app=ba-lint \
	  -o custom-columns=JOB:.metadata.name,SUCCEEDED:.status.succeeded,FAILED:.status.failed,DONE:.status.completedIndexes
	$(KUBECTL) get pods -l app=ba-lint -o wide

# Concatenated logs of every shard.
lint-logs: lint-check
	@for j in $$($(KUBECTL) get jobs -l app=ba-lint -o name); do \
	  $(KUBECTL) logs $$j --all-containers --prefix --tail=-1; \
	done

# Copy the measured rows off each shard's claim, which cannot be read remotely. The claim
# is the CodeQL stage's; only /state/lint is this stage's to take.
lint-fetch: lint-check
	@mkdir -p $(LINT_OUT)
	@s=0; while [ $$s -lt $(LINT_SHARDS) ]; do \
	  echo "fetching shard $$s"; \
	  $(KUBECTL) delete pod ba-lint-fetch --ignore-not-found --now >/dev/null; \
	  $(KUBECTL) run ba-lint-fetch --image=busybox --restart=Never \
	    --overrides="{\"spec\":{\"containers\":[{\"name\":\"ba-lint-fetch\",\"image\":\"busybox\",\"command\":[\"sleep\",\"600\"],\"volumeMounts\":[{\"name\":\"s\",\"mountPath\":\"/state\"}]}],\"volumes\":[{\"name\":\"s\",\"persistentVolumeClaim\":{\"claimName\":\"ba-codeql-out-$$s\"}}]}}" >/dev/null; \
	  $(KUBECTL) wait --for=condition=Ready pod/ba-lint-fetch --timeout=180s >/dev/null; \
	  $(KUBECTL) cp ba-lint-fetch:/state/lint$(LINT_RUN)/. $(LINT_OUT) 2>/dev/null; \
	  $(KUBECTL) delete pod ba-lint-fetch --now >/dev/null; \
	  s=$$((s + 1)); \
	done
	@echo "$(LINT_OUT): $$(ls $(LINT_OUT) | wc -l) files"

# Delete the linter Jobs but keep the claims, so a rerun resumes.
lint-clean: lint-check
	$(KUBECTL) delete jobs -l app=ba-lint


# ==================== Symlink Audit ====================

SYMLINKS_SRC := scripts/scan/symlinks_repo.py scripts/scan/scan_repo.py \
                scripts/scan/snapshot_commits.py

.PHONY: symlinks-check symlinks symlinks-status symlinks-fetch symlinks-clean

symlinks-check: lint-check

# Push the scripts and the sample, then replace the Job sized to the sample.
symlinks: symlinks-check
	$(KUBECTL) create configmap ba-symlinks-scripts \
	  $(addprefix --from-file=,$(SYMLINKS_SRC)) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create configmap ba-symlinks-sample \
	  --from-file=sample.csv=$(CSV) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@$(KUBECTL) create secret generic github-token \
	  --from-literal=token="$$GITHUB_TOKEN" --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) delete job ba-symlinks --ignore-not-found
	sed -e "s|__COMPLETIONS__|$(call scan_rows,$(CSV))|" \
	    -e "s|__MAXFAILED__|$(call scan_maxfail,$(CSV))|" \
	    -e "s|__PRE__|$(PRE)|" -e "s|__POST__|$(POST)|" \
	  k8s/symlinks/symlinks-job.yaml | $(KUBECTL) apply -f -

symlinks-status: symlinks-check
	$(KUBECTL) get job ba-symlinks \
	  -o custom-columns=SUCCEEDED:.status.succeeded,FAILED:.status.failed,DONE:.status.completedIndexes

# The census rows travel in the pod logs; keep the data rows and put one header on top.
symlinks-fetch: symlinks-check
	@mkdir -p data/11_symlinks
	@{ echo "repo,month,offset,sha,link,target,resolved,kind,dup_scope_files"; \
	   $(KUBECTL) logs -l app=ba-symlinks --tail=-1 --max-log-requests=200 \
	     | grep -E '^[^,]+/[^,]+,[0-9]{4}-[0-9]{2},'; } > data/11_symlinks/symlinks.csv
	@echo "data/11_symlinks/symlinks.csv: $$(($$(wc -l < data/11_symlinks/symlinks.csv) - 1)) rows"

symlinks-clean: symlinks-check
	$(KUBECTL) delete job ba-symlinks --ignore-not-found
