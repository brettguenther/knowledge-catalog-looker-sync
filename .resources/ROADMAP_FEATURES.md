# Knowledge Catalog & LookML Sync Engine — Future Roadmap Features

This document captures architectural specifications, upstream Dataplex / Knowledge Catalog schemas, and design patterns for future enhancements to `looker-kc` (`knowledge-catalog-looker-sync`).

---

## 1. Numeric Quartile Bucketing (`type: tier` Dimensions)

### Upstream Catalog Signal
- **System Aspect**: `projects/dataplex-types/locations/global/aspectTypes/data-profile` (`655216118709.global.data-profile`)
- **Path in Unpacked Column Aspect**: `data-profile.numeric.quartiles` (`[p25, p50, p75]`), `data-profile.numeric.min`, `data-profile.numeric.max`, `data-profile.numeric.avg`, `data-profile.numeric.stdDev`

### Proposed Profile Configuration (`config/profiles/*.yaml`)
```yaml
auto_generate_tier_dimensions: false  # Opt-in boolean toggle (similar to auto_generate_kpi_measures)
tier_dimension_style: "integer"       # integer, interval, classic, relational
tier_dimension_suffix: "_tier"
```

### Target LookML Synthesis
When `auto_generate_tier_dimensions: true`, for any certified (`hidden: no`), non-primary-key numeric dimension (`type: number`) that has a 3-element `data-profile.numeric.quartiles` array `[q1, q2, q3]`, synthesize a companion `type: tier` dimension in `views/base/*.base.view.lkml`:
```lookml
dimension: order_amount_tier {
  type: tier
  tiers: [27.11, 53.61, 101.01]
  style: integer
  sql: ${order_amount} ;;
  hidden: no
  label: "Order Amount Tier"
  description: "Quartile buckets (25th, 50th, 75th percentiles) for Order Amount."
  tags: ["certified", "quartile_tier"]
}
```
- **Value for Looker & Conversational AI**: Gives business users and Conversational Analytics agents instant cohort/distribution bucketing (e.g., Small / Medium / Large / Enterprise order buckets) grounded in actual empirical percentiles rather than arbitrary hardcoded thresholds.

---

## 2. Data Quality Scorecard Threshold Gating & Trust Badges

### Upstream Catalog Signal
- **System Aspect**: `projects/dataplex-types/locations/global/aspectTypes/data-quality-scorecard` (`655216118709.global.data-quality-scorecard`)
- **Table-Level Fields**: `score` (`0.0`–`100.0`), `status` (`PASS` | `FAIL`), `dimensions` (`[{name: COMPLETENESS|VALIDITY|UNIQUENESS|FRESHNESS|..., score, status}]`)
- **Column-Level Fields** (unpacked into `column_aspects[col]["data-quality-scorecard"]`): `score`, `status`, `dimensions`

### Proposed Profile Configuration & Rules Engine Extension
1. **Numeric & Compound Operators in `rules.py`**:
   - Extend `evaluate_certification_rule` and `evaluate_exclusion_rule` with `gte`, `lte`, `gt`, `lt`, `all_of`, and `any_of`.
2. **Quality Failure Policy (`dq_failure_action`)**:
   - `hide`: Automatically demote certified fields to `hidden: yes` when `data-quality-scorecard.status == "FAIL"` or `score < min_dq_score_for_certification`.
   - `warn`: Keep `hidden: no`, prepend a trust badge (`[⚠️ DQ FAIL: 50%]` or `[DQ: 99.8% PASS]`) to the LookML `description`, and attach `tags: ["dq_status:FAIL", "dq_score:50"]`.
   - `ignore`: Surface only in the GitOps Pull Request summary table.

---

## 3. Automated LookML Data Tests (`test:`) from Dataplex `DataQualityRule`s

### Upstream Signal (`DataScanServiceClient`)
When querying `DataScanServiceClient.get_data_scan(..., view=FULL)` for `DataScanType.DATA_QUALITY`, `data_quality_result.rules` provides `DataQualityRuleResult` entries containing:
- `non_null_expectation`: Column must not contain `NULL`.
- `uniqueness_expectation`: Column values must be unique (`100%` distinct).
- `range_expectation`: `min_value`, `max_value`, `strict_min_enabled`, `strict_max_enabled`.
- `set_expectation`: `values` list of allowed discrete strings/numbers.
- `regex_expectation` / `row_condition_expectation` / `sql_assertion`.

### Target LookML Synthesis (`tests/<view>.data_tests.lkml`)
Translate Dataplex `DataQualityRule`s into native LookML `test:` blocks executed during `looker-cli project validate` / Looker CI (`run_lookml_test`):
```lookml
test: orders_order_id_not_null {
  explore_source: orders {
    column: order_id {}
    filters: [orders.order_id: "NULL"]
  }
  assert: order_id_is_not_null {
    expression: is_null(${orders.order_id}) = no ;;
  }
}
```

---

## 4. Low-Cardinality `topN` & DQ `set_expectation` Auto-Population into `suggestions`

### Upstream Signal
- `data-profile.topN.values`: Top $N$ most frequent non-null string values (e.g., `['InStore', 'Pick Up', 'ASAP', 'Uber Eats', ...]`) along with `data-profile.uniqueness` (0–100%).
- `DataQualityRule.set_expectation.values`: Governed enum list from Data Quality rules.

### Target LookML Action
- When a certified `STRING` dimension does not have manual `allowed_values` curated in its aspect, and `data-profile.uniqueness <= max_suggestion_uniqueness_pct` (e.g., $\le 5.0\%$), automatically populate LookML `suggestions: [...]` from `data-profile.topN.values` (or `set_expectation.values`).
- **Benefit**: Eliminates expensive runtime `SELECT DISTINCT` queries on BigQuery and grounds Looker Conversational Analytics on exact categorical filter literals.

---

## 5. Primary Key Uniqueness Verification & Auto-Inference

### Upstream Signal
- `data-profile.uniqueness` (`100.0`) and `data-profile.nullness` (`0.0`)
- `DataQualityRule.uniqueness_expectation` and `non_null_expectation` (`passed == True`)

### Target LookML Action
- **Verification**: Validate columns matched by `primary_key_patterns` (`{view_name}_id`, `id`). If `data-profile.uniqueness < 100.0` or `data-profile.nullness > 0.0`, demote `primary_key: no` and add `tags: ["pk_uniqueness_failed"]` to prevent broken Looker **Symmetric Aggregates** and fanout double-counting.
- **Auto-Inference**: Optionally promote candidate ID columns that empirically exhibit `uniqueness == 100.0` and `nullness == 0.0`.

---

## 6. High-Nullness / Dead Column Exclusion & Smart Measure Guardrails

### Upstream Signal
- `data-profile.nullness >= 99.0` (unpopulated or deprecated ETL columns).
- `data-profile.topN` + discrete code patterns on `INTEGER`/`FLOAT` columns (e.g., `sales_organisation: 1010, 1015`).

### Target LookML Action
- Allow declarative `exclusions` to drop or hide columns exceeding a nullness threshold (`path: "data-profile.nullness"`, `operator: "gte"`, `values: [99.0]`).
- Prevent `auto_generate_kpi_measures` from creating `type: sum` measures on numeric identifier/code columns; optionally synthesize `type: count_distinct` for high-cardinality entity keys.

---

## 7. Refresh Cadence (`refresh-cadence` Aspect) $\rightarrow$ LookML `datagroup` Policies

### Upstream Signal
- **System Aspect**: `projects/dataplex-types/locations/global/aspectTypes/refresh-cadence`
- **Fields**: `frequency` (`Daily`, `Weekly`, `Monthly`, `Quarterly`), `refreshTime`, `thresholdInMinutes`, `cronSchedule`.

### Target LookML Action
Generate LookML `datagroup:` blocks in `explores/base/*.base.explore.lkml` or `models/*.model.lkml` and attach `persist_with:` to base explores:
```lookml
datagroup: orders_daily_refresh {
  max_cache_age: "24 hours"
  sql_trigger: SELECT MAX(order_date) FROM `project.dataset.orders` ;;
}
```

---

## 8. Stewardship Contacts (`contacts` Aspect) & GitOps PR Governance Scorecard

### Upstream Signal
- **System Aspect**: `projects/dataplex-types/locations/global/aspectTypes/contacts` (`identities: [{role: "owner"|"steward", name, id}]`)
- **Data Quality & Profile Job Metrics**: `scannedRows`, `score`, `status`, failing rule summaries.

### Target Action
- Tag views/explores with data steward metadata (`tags: ["steward:alice@example.com"]`) and automatically request GitHub PR reviews from mapped stewards.
- Render a Markdown **Data Governance, Quality & Profiling Audit Table** inside the automated GitHub Pull Request body summarizing row counts, DQ scores, and any schema/visibility diffs.

---

## 9. Conversational Analytics Golden Queries Export (`DATA_DOCUMENTATION` Queries)

### Upstream Signal
- `DataDocumentationResult.table_result.queries` and `dataset_result.queries`: Contains 10–25 Gemini-generated, schema-validated pairs of `{description: "<natural language question>", sql: "<verified BigQuery SQL>"}`.

### Target Action
- Export `queries` into a structured golden evaluation artifact (`output/golden_queries/<dataset>_queries.json` or Looker Agent benchmark `.evalset.json`) to bootstrap and benchmark Looker Conversational Analytics agents.

---

## 10. Looker CI Suite GitHub Action Template

### Upstream Signal & Integration
- Looker CI Suites API and webhook integration ([Looker CI Documentation](https://docs.cloud.google.com/looker/docs/ci-create-suite)).
- Pull Request lifecycle events generated by the sync engine (`kc-sync/*` branches).

### Target Action
Provide a standardized GitHub Action workflow template (`templates/github-workflows/looker-ci.yml`) intended for deployment into downstream LookML repositories. When the sync bot opens or updates a Pull Request containing regenerated base views (`views/base/**`) or base explores (`explores/base/**`), this workflow automatically triggers Looker CI validation before human review or merge:

1. **Syntax & Model Validation**: Executes Looker project validation across all models to ensure that machine-generated base views and explores align with human-curated refinements (`views/curated/*.view.lkml`).
2. **Explore & Liquid Integrity**: Validates explore joins, field references, and conditional filter syntax (`{% condition %} ... {% endcondition %}`).
3. **Automated LookML Data Tests**: Triggers project data tests (`test:` blocks) in Looker to ensure underlying data contracts and business assumptions hold.
4. **Commit Status Check**: Reports pass/fail statuses directly to the GitHub Pull Request check suite, gating automated deployment to production.

```yaml
name: Looker CI Validation

on:
  pull_request:
    branches: [main]
    paths:
      - 'views/base/**'
      - 'explores/base/**'
      - 'models/**'

jobs:
  validate-lookml:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Trigger Looker CI Suite
        env:
          LOOKERSDK_BASE_URL: ${{ secrets.LOOKERSDK_BASE_URL }}
          LOOKERSDK_CLIENT_ID: ${{ secrets.LOOKERSDK_CLIENT_ID }}
          LOOKERSDK_CLIENT_SECRET: ${{ secrets.LOOKERSDK_CLIENT_SECRET }}
        run: |
          echo "Executing Looker CI Suite validation on branch ${{ github.head_ref }}..."
          # Calls Looker CI Suite API endpoint or looker-cli project validate
```

