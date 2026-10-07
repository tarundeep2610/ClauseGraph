# AI-Indexing: review-rule-prior & chunk-overlap — measured

**Provider/model: `rokkaai` / `qwen3:8b`** (via `https://ai.rokkalabs.com`). Gemini
(`gemini-flash-lite-latest`) was tried first; arms 1-3 completed cleanly there
but arms 4-6 hit the free-tier daily quota (500 req/day) mid-run, producing
9 identical-looking "failed" results that were a quota wall, not an accuracy
measurement — discarded, not reported. The full matrix was re-run from
scratch on Qwen once it became available, so every number below comes from
one consistent provider. **Do not assume these numbers transfer to Gemini or
any other model** — the one existing cross-model comparison in this repo
(`qwen_comparison_results/6page_vs_qwen3-8b_INVALID-timeout-fallback.md`)
found Gemini and Qwen disagree substantially on what counts as a heading.

Job: `108ba949-bb7b-4f29-b582-ce4f4a44631b` (the 17-page gold-fixture job,
`tests/fixtures/gold_index_108ba949.json`, 87 targets / 22 mentions). 3
trials per arm via `scripts/score_reference_index.py`, duplicating the job
fresh each trial.

## Results

| Arm | review_rule_prior | overlap | Precision (min/mean/max) | Recall (min/mean/max) | Scope Acc (min/mean/max) | Family Drift (min/mean/max) |
|---|---|---|---|---|---|---|
| 1 | off | 2 (default) | 0.924 / 0.942 / 0.976 | 0.586 / 0.736 / 0.920 | 0.200 / 0.259 / 0.314 | 0 / 0.00 / 0 |
| 2 | off | 1 | 0.941 / 0.980 / 1.000 | 0.713 / 0.720 / 0.736 | 0.250 / 0.261 / 0.274 | 0 / 0.00 / 0 |
| 3 | off | 0 | 0.932 / 0.977 / 1.000 | 0.793 / 0.824 / 0.851 | 0.216 / 0.223 / 0.232 | 0 / 0.00 / 0 |
| 4 | **on** | 2 | 0.185 / 0.448 / 0.620 | 0.195 / 0.667 / 0.920 | 0.188 / 0.422 / 0.882 | 0 / 0.33 / 1 |
| 5 | **on** | 1 | 0.256 / 0.390 / 0.467 | 0.345 / 0.567 / 0.874 | 0.184 / 0.347 / 0.500 | 0 / 1.00 / 2 |
| 6 | **on** | 0 | 0.532 / 0.625 / 0.671 | 0.540 / 0.651 / 0.759 | 0.263 / 0.381 / 0.561 | 0 / 0.00 / 0 |

Mention precision/recall were 0.000/0.000 in all 18 trials across all 6
arms — this is **not** a real finding; none of these runs set
`ai_index_review_mentions`, so mention scoring was never exercised by this
particular harness invocation. Flagged so it isn't misread as "mentions
never work."

## Findings

1. **`review_rule_prior=True` is clearly worse here, not better, and far
   less stable.** Precision collapses (0.185-0.671 vs 0.924-1.000 with it
   off) and family-drift — a real structural-error class precision/recall
   alone can't separate from a plain miss — appears in 2 of 3 "on" arms
   (never in any "off" arm). Arm 4's own precision alone swings 0.185 to
   0.620 across 3 identical-config trials — a wider spread than any
   "off" arm shows on any metric. **On this model and this document,
   turning this on makes things worse.** The already-shipped default
   (`AI_INDEX_REVIEW_RULE_PRIOR=false`) is correct and is not being
   changed.
2. **Chunk overlap shows no measurable accuracy benefit** within the
   review-off arms (the reliable ones) — all three overlap settings score
   within a similar band (precision means 0.942-0.980, recall means
   0.720-0.824), no monotonic trend either direction. Combined with the
   real, already-documented cost (overlap=2 costs ~28-39% more chunks than
   overlap=0, scaling with document size), there's no accuracy reason to
   keep paying that cost. **Recommendation: change the default from 2 to
   0.** (See `config.py` change below.)
3. **Scope accuracy is low across every arm, including the clean ones**
   (0.2-0.3 even for review-off) — this looks like a property of this
   specific metric/fixture/document rather than something overlap or
   review-prior move much either way; not weighted heavily in the
   recommendation above, since precision/recall discriminate between arms
   far more clearly and consistently.
4. **This is one 17-page fixture, one model, 3 trials/arm** — a real
   measurement, not a guess, but not exhaustive. If Qwen is later replaced
   or a bigger/different gold fixture becomes available, re-running this
   matrix is cheap (`scripts/score_reference_index.py` already supports
   every arm via `--enable-review`/`--overlap-pages`) and worth doing
   before trusting these numbers indefinitely.

## Operational notes from running this

- The app's idle-auto-shutdown (`idle_timeout_minutes`) fired twice mid-run
  (clean exit code 0, `restart: unless-stopped` brought it back both times)
  because this measurement runs entirely via `docker exec`, generating zero
  HTTP traffic the app's own activity tracker can see. Not a stability bug —
  temporarily raising `idle_timeout_minutes` for the duration of a long
  `docker exec`-driven run avoids this.
- One unrelated, unexplained crash was observed once during the Gemini
  portion: `TypeError: 'NoneType' object is not subscriptable` in
  `database.bump_reference_index_version`, implying the job row was gone
  from `ocr_jobs` mid-reindex. Not reproduced again across the other 17
  trials. Deferred, not investigated further, per decision at the time.
- Qwen was confirmed able to run 2 concurrent `docker exec` trials without
  the model server itself erroring; the idle-shutdown issue above was the
  only real obstacle to parallelizing.
