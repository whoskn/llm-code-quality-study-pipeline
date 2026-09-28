# Dataset & repository-selection across related papers

Per-paper: **year · gathering (markers/approach) · filtering · scale · url**.
URLs checked (HTTP 200 unless noted).

**Availability:** published & reusable — 02, 08, 09, 11, 13, 14, 15, 16 · reuse AIDev — 14, 15 (source: 16) · not usable — 06 (proprietary), 12 (unreleased), 10 (announced, no url), 03 (no repo mining).

---

### 02 — Security Weaknesses of Copilot-Generated Code (TOSEM)
- **Year:** 2025
- **Gathering:** GitHub REST API keyword search; markers = {by, use, with} x {GitHub Copilot, CodeWhisperer, Codeium}; two scopes (`Code` files / `Repo` projects declaring use in README).
- **Filtering:** Python & JavaScript only; 2 coders (kappa 0.84); exclude competitive-programming solutions (LeetCode/BOJ); dedup.
- **Scale:** 9,927 hits -> 3,589 deduped -> 733 snippets (672 Copilot, 38 CW, 23 Codeium); 43 CWEs.
- **URL:** https://doi.org/10.5281/zenodo.10802054

### 03 — LLM-Generated vs Human Code: Maintainable & Reliable?
- **Year:** 2025
- **Gathering:** No repo mining. Human baseline = APPS dataset (10k Python problems, Codeforces/AtCoder/Kattis/Codewars); LLM code generated with GPT-4o.
- **Filtering:** by APPS difficulty (introductory/interview/competition), not repos.
- **Scale:** 10k problems; 4 datasets (human + zero/few-shot + fine-tuned); 15 manually analyzed.
- **URL:** https://zenodo.org/records/15283737 (artifacts only, no repo list)

### 06 — Impact of GenAI on Collaborative OSS (GitHub Copilot)
- **Year:** 2024
- **Gathering:** GH Archive panel (Jan 2021 – Dec 2022, project-month) + **proprietary GitHub Copilot usage share**; IDE from project pages.
- **Filtering:** active-OSS (non-zero size, language, license, description, no mirror, >=1 commit/6mo + yearly activity, >=3 devs/month, IDE disclosed); treatment = Copilot + supported IDE.
- **Scale:** 9,244 -> 7,637 projects (4,491 treatment / 3,146 control).
- **URL:** none — dataset proprietary, not shareable.

### 08 — Unveiling ChatGPT's Usage in OSS (MSR '24)
- **Year:** 2024
- **Gathering:** GitHub API keyword mining (12 Jun 2023) for commits/PRs/issues containing "ChatGPT".
- **Filtering:** 2-/3-grams around "ChatGPT" kept if >=0.02% (>1k) -> 34 n-grams; exclude repos <10 stars; fork dedup; full manual inspection.
- **Scale:** 233,988 raw -> 1,501 candidates (732 projects) -> 467 true positives (358 projects).
- **URL:** https://github.com/unveilingchatgptsusage/unveilingchatgptsusage

### 09 — Self-Admitted GenAI Usage in OSS
- **Year:** 2025
- **Gathering:** SEART GitHub search + REST API clone; 5 langs (Py, JS, TS, Java, C#); GenAI mentions via regex over comments, docs, commit messages.
- **Filtering:** created before ChatGPT (30 Nov 2022) & active after; no forks; standard license; >=1 release; >=2 contributors; not archived; engineered filter (drop bottom quartile issues/PRs/LOC; code-ratio outliers); manual curation.
- **Scale:** 207,062 repos -> 14,785 engineered -> 1,292 true mentions across 156 repos (151 in longitudinal).
- **URL:** https://doi.org/10.5281/zenodo.15871467

### 10 — Large-Scale Measurement of AI-Generated Code in Real Repos
- **Year:** 2026
- **Gathering:** GitHub REST API keyword search extending paper 09 to 11 tools x 6 prefixes = 66 keywords; attribution comments ("by ChatGPT"); through 31 Dec 2025.
- **Filtering:** paper 09 filter + rule-based (whole-word, semantic attribution, usage-based) + LLM classifier (ChatGPT 5-mini) + manual; dedup repo+path+commit; matched pre-AI human controls.
- **Scale:** 44,616 files -> 19,816 AI-involved files / 12,749 commits; 42,792 AI sub-file units. No repo count reported.
- **URL:** none — release announced, no link in this version.

### 11 — Debt Behind the AI Boom
- **Year:** 2026
- **Gathering:** GHArchive `PushEvent` via BigQuery (Jan 2024 – Oct 2025) + REST API for top-starred + full-history bare-clone scan. Markers = Git metadata: actor logins (`copilot-swe-agent[bot]`), emails (`noreply@anthropic.com`), author names (`Cursor Agent`), `Co-authored-by` trailers — 29 tools / 5 assistants.
- **Filtering:** >=100 stars; >=1 confirmed AI commit; contains Py/JS/TS; production source only.
- **Scale:** 587,118 -> 12,770 -> 6,699 repos w/ AI commits; 302.6k AI commits; 484,366 issues.
- **URL:** https://github.com/yueyueL/tech-debt-ai-coding

### 12 — Detecting AI Coding Agents: Census of 180M Repos
- **Year:** 2026
- **Gathering:** World of Code (>180M repos, 3 snapshots Dec24/Oct25/Apr26); ClickHouse regex + hash maps. Markers = 4 types: A bot emails, B commit-message signatures, C author-name suffixes (`(aider)`), D config files (`.cursorrules`, `copilot-instructions.md`, `CLAUDE.md`, `AGENTS.md`).
- **Filtering:** ecosystem census, no repo filtering; ~38M-author identity resolution; 495 hand labels.
- **Scale:** 180M+ repos; 850k Claude Code commits V2510 (vs 28k bot-only = 30x undercount); 12 agents.
- **URL:** none yet — "Zenodo upon publication". (Compares vs AIDev: https://huggingface.co/datasets/hao-li/AIDev)

### 13 — Speed at the Cost of Quality? (Cursor AI)
- **Year:** 2025
- **Gathering:** GitHub Code Search for `.cursorrules` / `.cursor` folder (config first-commit = adoption date); controls + time series from GHArchive.
- **Filtering:** treatment >=10 stars, non-fork, adopted Jan 2024 – Mar 2025, still live Aug 2025; controls = never-adopters via propensity-score matching; DiD (Borusyak).
- **Scale:** 806 treatment + ~1,172–1,380 matched control; 21,699 obs; SonarQube on 195,010 warnings.
- **URL:** https://doi.org/10.5281/zenodo.18366661

### 14 — Agent-Generated Code Maintenance (EASE 2026)
- **Year:** 2026
- **Gathering:** reuse **AIDev** (paper 16); AI files = "added" in PR with committer = agent id (`claude[bot]`, `Cursor Agent`, `Copilot`, `devin-ai-integration`); Codex excluded.
- **Filtering:** AIDev's 2,807 repos (>100 stars) -> top 100; <=10 AI + <=10 human files/repo; files created before 31 Jul (>=6-mo window).
- **Scale:** 100 repos -> 1,016 files (508 AI + 508 human) -> 3,238 commits.
- **URL:** https://github.com/ShouSawa/AI-Code-Maintainability (scripts; repo list derives from AIDev)

### 15 — Security of Agentic Pull Requests
- **Year:** 2026
- **Gathering:** reuse **AIDev** (932,791 PRs; curated 33,596); 2-stage security ID — regex over PR title/body across 6 dimensions -> manual validation.
- **Filtering:** AIDev curated subset from 2,807 repos (>=100 stars); 5 agents; drop PRs w/o timestamps for latency RQs.
- **Scale:** 33,596 PRs -> 1,293 confirmed security PRs (~3.85%); merge rates 49.6% (Copilot) – 86.6% (Codex).
- **URL:** https://doi.org/10.5281/zenodo.18103932 (corpus = AIDev)

### 16 — Rise of AI Teammates in SE 3.0 — **the AIDev dataset**
- **Year:** 2025
- **Gathering:** GitHub REST API, one query per agent (from PRarena): Codex `is:pr head:codex/`; Devin `author:devin-ai-integration[bot]`; Copilot `head:copilot/`; Cursor `head:cursor/`; Claude Code `"Co-Authored-By: Claude"`. Cut-off 22 Jun 2025.
- **Filtering:** full = all matching PRs; downstream AIDev-pop = repos >=500 stars (also ships >=100-star / 2,807-repo slice used by 14 & 15).
- **Scale:** 456,535 PRs / 47,303 devs / 61,453 repos; AIDev-pop 7,122 PRs / 856 repos.
- **URL:** https://github.com/SAILResearch/AI_Teammates_in_SE3 (also https://huggingface.co/datasets/hao-li/AIDev)
