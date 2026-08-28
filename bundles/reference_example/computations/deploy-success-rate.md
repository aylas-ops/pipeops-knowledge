---
type: Attested Computation
title: Deploy success rate over a window
description: Sanctioned SQL producing the deploy success rate for a rolling window.
owner: team:platform
tags: [slo, deployments, attested]
status: stable
runtime: postgres
parameters:
  - { name: window_days, type: integer, required: true }
  - { name: cluster, type: string, required: false }
executor:
  resource: /references/skills/run-on-postgres.md
  receipt: [statement_id, executed_sql, result]
attester:
  resource: /references/attesters/sql_equality.py
generated: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-19T15:20:00Z }
verified:
  - { by: human:ayomidelasaki@pipeops.io, at: 2026-08-20T10:30:00Z }
  - { by: process:slo-nightly, at: 2026-08-27T02:00:00Z }
stale_after: 2026-11-20T00:00:00Z
sources:
  - id: slo-doc
    resource: https://wiki.example.org/platform/slo
    title: Platform SLO definitions
    author: team:platform
    last_modified: 2026-07-30T00:00:00Z
---

# Computation

```sql
SELECT
    COUNT(*) FILTER (WHERE d.outcome = 'healthy')::numeric
      / NULLIF(COUNT(*), 0) AS deploy_success_rate
FROM deployments AS d
WHERE d.created_at >= NOW() - (@window_days || ' days')::interval
  AND d.is_retry = false
  AND (@cluster IS NULL OR d.cluster_id = @cluster)
```

The `is_retry = false` predicate is what collapses retries into one row per
deployment, per the SLO definition.[^slo-doc] Removing it is the single most
common way this number gets reported wrong.

# What the attester checks

`/references/attesters/sql_equality.py` receives the receipt from
`/references/skills/run-on-postgres.md` and confirms two things:

1. **Provenance.** `receipt.executed_sql`, canonicalised, equals the SQL above
   canonicalised the same way. A dropped `is_retry` filter, a swapped table, or
   an agent-authored rewrite all fail.
2. **Fidelity.** The value about to be displayed equals `receipt.result[0]`,
   re-read from the statement rather than taken from the agent's own text.

A run that fails either check is unattested, and the consumer must refuse to
display the number.

# Freshness

`stale_after` mirrors the quarterly SLO review. Past that instant a consumer
should re-verify the definition against
[the metric](/metrics/deploy-success-rate.md) before serving the figure —
attestation proves the sanctioned SQL ran, not that the SQL is still the right
SQL.

[^slo-doc]: Platform SLO definitions
