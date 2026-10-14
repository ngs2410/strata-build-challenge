# Strata eval results — 2026-10-14T03:08:22+00:00

- **Tenant:** `verdant_grid`
- **Environment:** `prod`
- **Gold file:** `data/evals/gold.json`
- **Cases run:** 1

## Headline numbers

| Stage                 | Metric                | Value |
|-----------------------|-----------------------|-------|
| change_detection      | precision             | 1.000 |
| change_detection      | recall                | 1.000 |
| change_detection      | f1                    | 1.000 |
| change_detection      | citation_precision    | 1.000 |
| citation_verify       | verification_accuracy | 1.000 |
| obligation_extraction | f1                    | 0.909 |
| obligation_extraction | type_accuracy         | 0.800 |
| mapping               | item_f1               | 0.692 |
| mapping               | action_accuracy       | 0.444 |
| mapping               | route_accuracy        | 0.889 |

## Full text report

```
=== Strata Eval Report ===
Cases run: 1

Per-case summary:
  [order-2222-section-g-1-ix] diff_tp=3 diff_fp=0 diff_fn=0 citation=3/3 mean_conf=3.667
      verifications: 6/6 correct
      extractions: tp=5 fp=1 fn=0 type_correct=4/5
      mappings: items tp=9 fp=1 fn=7; action 4/9; route 8/9

Aggregate — change detection (version_diff):
  tp=3 fp=0 fn=0; P=1.000 R=1.000 F1=1.000
  Citation precision: 3/3 (1.000)
  Mean confidence: 3.667
  Mean keyword coverage: 1.000

Reliability diagram (confidence_bucket: correct/total = accuracy):
  3: 1/1 = 1.000
  4: 2/2 = 1.000

Change-type confusion (expected->predicted = count):
  tightened->tightened: 3

Aggregate — citation_verify:
  Verification accuracy: 6/6 (1.000)

Aggregate — obligation_extraction:
  tp=5 fp=1 fn=0; P=0.833 R=1.000 F1=0.909
  Obligation-type accuracy: 0.800
  Mean keyword coverage: 1.000

Aggregate — mapping:
  Items: tp=9 fp=1 fn=7; P=0.900 R=0.562 F1=0.692
  Action accuracy: 0.444 (n=9)
  Route accuracy: 0.889 (n=9)
```
