# Mapping Profiles
 
This directory contains declarative mapping profiles (`*.yaml`) that configure how Google Cloud Knowledge Catalog (Dataplex) entry metadata and custom aspects are translated into LookML base views and base explores.

For the complete schema specification, operator reference, cascade resolution rules, and guide to authoring custom profiles, see [docs/PROFILES.md](../../docs/PROFILES.md).

## Built-In Profiles

- `semantic_curation.yaml`: Reference standard profile mapping Dataplex custom aspect `semantic-curation`.
- `collibra_outbound.yaml`: Ingestion profile for Collibra governance assets synced to Dataplex.
- `dataplex_native.yaml`: Native Dataplex stewardship, overviews, and BigQuery schemas.

## Explore Control (`explore_policy`)

Each profile defines an `explore_policy` block to declaratively scope which catalog entities generate LookML Explores (`explores/base/*.base.explore.lkml`), preventing explore bloat in Looker:

```yaml
explore_policy:
  enabled: true
  strategy: "tagged" # "tagged", "allowlist", "patterns", "root_only", "all"
  required_tags: ["core_bi", "explore", "fact"]
  table_allowlist: []
  table_patterns: ["fct_*", "fact_*"]
  exclude_dimension_tables: true
```

- **`strategy: "tagged"` (Default)**: Generates explores only for entities bearing governance tags matching `required_tags` (e.g. `core_bi`).
- **`strategy: "allowlist"`**: Restricts explore generation strictly to tables declared in `table_allowlist` (or overridden via `explore_tables` in `sync_config.yaml` or `--explore-table` CLI flag).
- **`strategy: "patterns"`**: Matches table names against glob expressions (e.g. `fct_*`, `fact_*`, `orders`).
- **`strategy: "root_only"`**: Inspects join graph topology to identify root entities with outgoing joins, suppressing pure join target tables.
- **`exclude_dimension_tables: true`**: Automatically suppresses standalone explores for dimension tables (prefixed with `dim_`, tagged with `dimension`/`dim`/`lookup`, or acting as join targets with no outgoing joins), while keeping them available as joined views.

## AI Data Documentation (`use_ai_data_documentation`)

Profiles enable `use_ai_data_documentation: true` by default to leverage Gemini-generated table overviews and column descriptions from Dataplex `DATA_DOCUMENTATION` scans:

```yaml
use_ai_data_documentation: true

field_mappings:
  description:
    sources:
      - path: "semantic-curation.business_description" # 1. Human-curated governance (Highest priority)
      - path: "column.description"                     # 2. Native BigQuery column description
      - path: "data-documentation.description"         # 3. Gemini AI Data Documentation fallback
```

- Ingests both native catalog-published `descriptions` aspects attached directly to Dataplex entries and live DataScan service results.
- Backfills missing descriptions on certified visible fields without overriding human-curated stewardship.
- Table overviews backfill to `view.description` and `explore.description` when manual business descriptions are omitted.

