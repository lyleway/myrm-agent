# router/

## Overview
Domain learning data shared by web_fetch and the browser (fetch failure history, SMART wait-strategy data, site experience).

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Domain learning metrics and site experience exports | — |
| domain_metrics.py | Core | Domain-level learning metrics manager; saves and cleans up on a schedule as the store is used. | ✅ |
| models.py | Core | Domain learning data model (per-domain fetch failures, SMART wait-strategy data), its persisted JSON shape (unknown tier keys skipped on load) and the domain key. | ✅ |
| site_experience.py | Core | Site experience store: per-domain platform features, URL patterns, traps and successful flows the agent saved while browsing, cross-checked against the domain's recent fetch failures; keys of an older stored file that this version does not know are ignored on load. | ✅ |

## Key Dependencies

- `infra`
