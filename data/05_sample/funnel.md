# Repository selection funnel

Sources unioned into 1171 name-unique candidates; gates as fixed in plan.md §3, applied 2026-09-13.

| Stage | Rejected here | Remaining |
| --- | --- | --- |
| raw union | — | 1171 |
| resolved on GitHub | 0 | 1171 |
| primary language Python | 0 | 1171 |
| stars >= 100 | 0 | 1171 |
| not a fork | 0 | 1171 |
| not archived | 0 | 1171 |
| not a mirror, not disabled | 0 | 1171 |
| has a licence | 0 | 1171 |
| not empty | 0 | 1171 |
| created <= 2024-07 | 0 | 1171 |
| pushed since 2025-08-01 | 0 | 1171 |
| size <= 500 MB | 0 | 1171 |
| history measured | 85 | 1086 |
| in-scope Python LOC <= 1,000,000 | 2 | 1084 |
| m0 in [2023-01, 2025-07] | 528 | 556 |
| history spans 12+12 months | 105 | 451 |
| active in >= 10 of 12 months per side | 126 | 325 |
| markers in >= 3 of the first 6 post-months | 205 | 120 |

All gates leave **120** repos.

## Family overlap

A = committed agent file, B = git metadata, C = human self-admission. The union column is what the sources claim; the pool column is re-derived from the clones.

| Families | Union | Pool |
| --- | --- | --- |
| A | 11 | 0 |
| B | 1139 | 17 |
| C | 5 | 0 |
| AB | 11 | 70 |
| AC | 0 | 0 |
| BC | 5 | 2 |
| ABC | 0 | 31 |

A config-file search alone (family A) would have found 101/120 = 84% of the pool.

## Tiers (§5)

| Tier | Repos | Manual review |
| --- | --- | --- |
| 1 | 26 | lightweight plausibility check |
| 2 | 77 | full review |
| 3 | 17 | full review |

## Dating cross-check

6 pool repos carry a source-published adoption date. Our re-derived m₀ is earlier or equal 5 of them; later for 1: ethyca/fides (2025-03 → 2025-06).
