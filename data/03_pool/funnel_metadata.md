# Repository selection funnel

Sources unioned into 7320 name-unique candidates; gates as fixed in plan.md §3, applied 2026-08-17.

| Stage | Rejected here | Remaining |
| --- | --- | --- |
| raw union | — | 7320 |
| resolved on GitHub | 293 | 7027 |
| primary language Python | 4845 | 2182 |
| stars >= 100 | 64 | 2118 |
| not a fork | 25 | 2093 |
| not archived | 50 | 2043 |
| not a mirror, not disabled | 0 | 2043 |
| has a licence | 299 | 1744 |
| not empty | 0 | 1744 |
| created <= 2024-07 | 457 | 1287 |
| pushed since 2025-08-01 | 33 | 1254 |
| size <= 500 MB | 83 | 1171 |

Gates answerable from GitHub metadata leave **1171** repos; the history-density, sustained-onset and m₀ gates need the clone and are applied afterwards, by rerunning with --history.

## Family overlap

A = committed agent file, B = git metadata, C = human self-admission. The union column is what the sources claim; the pool column is still source-claimed until --history is supplied.

| Families | Union | Pool |
| --- | --- | --- |
| A | 760 | 11 |
| B | 6427 | 1139 |
| C | 5 | 5 |
| AB | 123 | 11 |
| AC | 0 | 0 |
| BC | 5 | 5 |
| ABC | 0 | 0 |

A config-file search alone (family A) would have found 22/1171 = 2% of the pool.

## Published dates present

22 pool repos carry a source-published adoption date, available for the §6 dating cross-check once m₀ is re-derived (range 2024-09 … 2025-03).
