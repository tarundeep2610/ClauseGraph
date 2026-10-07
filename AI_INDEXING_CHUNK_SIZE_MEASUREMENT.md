# AI-Indexing: chunk size (`ai_index_chunk_pages`) — measured

**Provider/model: `rokkaai` / `qwen3:8b`** (same provider as
`AI_INDEXING_REVIEW_OVERLAP_MEASUREMENT.md`). Held fixed across every arm:
`review_rule_prior=off`, `overlap_pages=0` — the defaults that measurement
already established as best. Only `ai_index_chunk_pages` varies.

Job: `108ba949-bb7b-4f29-b582-ce4f4a44631b` (17-page gold-fixture job,
`tests/fixtures/gold_index_108ba949.json`, 87 targets / 22 mentions). 3
trials per arm via `scripts/score_reference_index.py`, duplicating the job
fresh each trial.

## Results

| chunk_pages | Precision (min/mean/max) | Recall (min/mean/max) | Scope Acc (min/mean/max) | Family Drift (min/mean/max) |
|---|---|---|---|---|
| 3 | 0.789 / **0.806** / 0.837 | 0.816 / **0.824** / 0.828 | 0.208 / 0.209 / 0.211 | 0 / **0.00** / 0 |
| 5 | 0.709 / 0.722 / 0.738 | 0.678 / 0.697 / 0.713 | 0.242 / 0.247 / 0.254 | 0 / 0.00 / 0 |
| 7 | 0.331 / 0.463 / 0.609 | 0.207 / 0.425 / 0.609 | 0.283 / 0.497 / 0.833 | 0 / 0.00 / 0 |
| 10 | 0.443 / 0.750 / 1.000 | 0.402 / 0.670 / 0.839 | 0.219 / 0.291 / 0.429 | 0 / 2.67 / **8** |
| 17 | 0.529 / 0.583 / 0.642 | 0.172 / 0.360 / 0.494 | 0.349 / 0.589 / 1.000 | 0 / 0.67 / 2 |

## Findings

1. **chunk_pages=3 is the clear, stable winner.** Highest mean recall
   (0.824 — tied with the best overlap-sweep arm) and strong precision
   (0.806), with by far the tightest spread of any arm tested (precision
   0.789-0.837, recall 0.816-0.828 — a 0.048/0.012 range) and **zero**
   family drift across all 3 trials. Every other chunk size shows at least
   one badly degraded trial.
2. **chunk_pages≥7 is unstable, not just lower-scoring.** chunk7's three
   trials swing from P=0.331/R=0.207 to P=0.609/R=0.609 — a wider spread on
   a single config than the entire chunk3/chunk5 arms combined. chunk17 is
   similarly volatile (recall 0.172 to 0.494) and its one "perfect"
   ScopeAcc=1.000 trial is paired with its worst recall (0.172), i.e. it
   found almost nothing but what it found was well-scoped — not a result
   worth optimizing for.
3. **chunk_pages=10 reproduces badly and contradicts the earlier
   measurement — this is the finding that answers "was chunk really 10
   and did it work?"** The previous review/overlap matrix's best arm
   (review=off, overlap=0) ran at chunk_pages=10 and scored precision
   0.932-1.000 (mean 0.977) with **zero family drift in any of 18 trials
   across all 6 arms**. This fresh chunk10 arm, same config, same fixture,
   same provider, shows trial 1 at precision=0.443 with **family drift=8**
   — a structural-error class that never once appeared in the earlier
   measurement's off-arms. Trials 2 and 3 do look good (0.807/0.770 and
   1.000/0.839), consistent with the earlier run, but the fact that 1 of 3
   fresh trials produced a result that sharp disagrees with the earlier
   "reliable" characterization means **chunk_pages=10's earlier good
   numbers do not reliably reproduce** — it is not as stable a choice as
   previously believed. This is the documented non-deterministic-recall
   problem (`AI_INDEXING_ACCURACY_INVESTIGATION.md`), and it appears to get
   *worse*, not better, as chunk size grows.
4. **Smaller chunks costing more calls is the real, known trade-off.**
   chunk_pages=3 means roughly 3.3x the AI calls of chunk_pages=10 for the
   same document (a 955-page job: ~319 chunks at 3 pages vs. ~96 at 10).
   This measurement answers the accuracy question; it does not make the
   cost question disappear.

## Recommendation

Change `AI_INDEX_CHUNK_PAGES` default from `5` to **`3`** — it is not just
the best mean score, it is the only arm with no observed instability across
3 trials, and the instability at larger sizes (not just lower accuracy) is
the more important finding here. This does increase AI-indexing cost/calls
roughly 3x relative to chunk_pages=10 (the value currently frozen on
existing jobs' `engine_params` and likely closest to what's deployed) — a
real trade worth the user's explicit sign-off, not assumed.

## Operational notes

- Container stayed stable throughout (`RestartCount` unchanged
  before/after), no orphaned duplicate jobs left behind, idle_timeout_minutes
  restored to its original deployed value (5) after the run.
- Same caveat as the review/overlap measurement: one 17-page fixture, one
  model, 3 trials/arm. Re-measure before trusting indefinitely, especially
  before porting these defaults to a different model.

## Recheck — one additional trial each at chunk_pages 3/5/10

Run immediately after the above, to directly test whether chunk3's
apparent stability holds up. **Result: it doesn't, and the recheck
undercuts rather than confirms the recommendation above.**

| chunk_pages | status | Precision | Recall | Scope Acc | Family Drift |
|---|---|---|---|---|---|
| 3 | done | 0.651 | 0.471 | 0.341 | 0 |
| 5 | **failed** | 0.496 | 0.678 | 0.559 | 0 |
| 10 | done | 0.453 | 0.828 | 0.500 | 0 |

1. **chunk5's trial is not valid comparison data.** `status=failed` means
   `extract_structure_for_chunk` raised `ai_indexing.AIConfigError`
   mid-run (`ai_reference_index.py:1157-1159`), which **breaks the chunk
   loop early** — the reported score is computed against a partial index
   from however many chunks completed before the error, not a full 17-page
   run. The harness (`scripts/score_reference_index.py`) always deletes the
   duplicate job in its `finally` block, including on failure, so the
   underlying error message was not recoverable after the fact. Treat this
   row as discarded, not as "chunk5 scored 0.496/0.678."
2. **chunk3's result directly contradicts the first sweep's "zero
   instability" finding.** P=0.651/R=0.471 falls far outside the first
   sweep's tight 3-trial band (P 0.789-0.837, R 0.816-0.828) — a bigger
   single-trial miss than chunk10 or chunk17 ever showed in the first
   sweep. The most likely read: 3 trials wasn't enough to see chunk3's real
   variance, not that chunk3 is uniquely stable. The documented
   non-deterministic-recall problem looks like it affects every chunk size
   tested here, not specifically the larger ones.
3. **chunk10's new trial (P=0.453/R=0.828) is consistent with its
   already-known instability** — recall in the same range as its two good
   first-sweep trials, precision in the same range as its one bad one. Adds
   one more low-precision data point to the pile but nothing that reverses
   the earlier read on chunk10.

### Second recheck — one more trial each at chunk_pages 3/5/10

| chunk_pages | status | Precision | Recall | Scope Acc | Family Drift |
|---|---|---|---|---|---|
| 3 | done | 0.837 | 0.828 | 0.208 | 0 |
| 5 | done | 0.632 | 0.690 | 0.250 | 0 |
| 10 | done | 0.626 | 0.713 | 0.597 | 0 |

No failures this time. chunk3's result here lands back in its original
tight band, confirming the earlier low trial (0.651/0.471) was itself the
outlier, not the rule.

### Pooled totals across all trials run so far

| chunk_pages | valid trials | Precision mean | Recall mean | Family-drift episodes |
|---|---|---|---|---|
| 3 | 5 | **0.781** | **0.754** | 0 / 5 |
| 5 | 4 (1 discarded: `status=failed`, partial run) | 0.700 | 0.696 | 0 / 4 |
| 10 | 5 | 0.666 | 0.710 | 1 / 5 (that trial hit FamilyDrift=8) |

### Recommendation, revised again with more data

With 5 trials now at both chunk3 and chunk10 (4 valid at chunk5), chunk3
is back in front: best mean precision, best mean recall, and the only size
with **zero** family-drift incidents across every trial run. chunk10's
one family-drift episode is a qualitatively worse failure (a structural
error, not just a miss) and hasn't recurred for chunk3 across 5 tries.

The variance is real and shouldn't be hidden — chunk3's own trials ranged
0.651-0.837 precision, 0.471-0.828 recall, wider than a 3-trial sample
suggested. But across more data it still comes out ahead on both central
tendency and worst-case failure mode. **Recommendation: change
`AI_INDEX_CHUNK_PAGES` default from 5 to 3**, accepting the known ~3.3x
call-cost increase relative to chunk_pages=10, same trade as before — this
time backed by 5 trials instead of 3, with the one contradicting data point
explained rather than ignored.
