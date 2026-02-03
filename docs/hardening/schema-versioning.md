# Schema Versioning

Runbook files include a `schemaVersion` field to indicate which schema they conform to.  Versioning ensures that new features can be added without breaking existing runbooks.

## Policy

- Versions follow semantic versioning (`major.minor.patch`).
- Backwards‑compatible changes increment the minor version.
- Breaking changes increment the major version and may require a migration script.

## Runbook requirements

All runbooks must specify a `schemaVersion`.  The orchestrator validates the runbook against the corresponding JSON schema.

## Migration

When a schema changes, provide a migration guide.  Tools like `scripts/validate_runbook.py` can aid in detecting outdated runbooks and guiding updates.
