# Mapping Profiles
 
This directory contains declarative mapping profiles (`*.yaml`) that configure how Google Cloud Knowledge Catalog (Dataplex) entry metadata and custom aspects are translated into LookML base views.

For the complete schema specification, operator reference, cascade resolution rules, and guide to authoring custom profiles, see [docs/PROFILES.md](../../docs/PROFILES.md).

## Built-In Profiles

- `semantic_curation.yaml`: Reference standard profile mapping Dataplex custom aspect `semantic-curation`.
- `collibra_outbound.yaml`: Ingestion profile for Collibra governance assets synced to Dataplex.
- `dataplex_native.yaml`: Native Dataplex stewardship, overviews, and BigQuery schemas.
