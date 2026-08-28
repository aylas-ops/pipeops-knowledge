---
type: Metric
title: Deploy success rate
description: Share of customer deployments that reach a healthy rollout, per the platform SLO definition.
owner: team:platform
tags: [slo, deployments, reliability, metric]
status: stable
generated: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-19T15:20:00Z }
verified: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-20T10:30:00Z }
stale_after: 2026-11-20T00:00:00Z
sources:
  - id: slo-doc
    resource: https://wiki.example.org/platform/slo
    title: Platform SLO definitions
    author: team:platform
    last_modified: 2026-07-30T00:00:00Z
---

# Definition

The share of deployments that reach a healthy rollout, measured over a rolling
window. A deployment counts as successful when every workload passes readiness
and no rollback is triggered within thirty minutes.[^slo-doc]

The figure is produced by
[the deploy success rate computation](/computations/deploy-success-rate.md),
which is the only sanctioned way to calculate it.

# Caveats

Three things people get wrong about this number, in the order they get them
wrong:

- **Customer-caused failures are in the denominator.** A failed readiness probe
  in a customer's own image counts against the rate. This is deliberate — the
  SLO measures the experience, not our blame share — but it means the number
  moves when a large customer ships a bad build.
- **Retries collapse.** A deployment retried four times is one row, not four.
  Counting attempts instead of deployments inflates the rate by roughly a point.
- **The window is rolling, not calendar.** Comparing a rolling-28-day figure to
  a monthly one produces a discrepancy that is not a bug.

# Related

- [deploy-orchestrator](/services/deploy-orchestrator.md), the service that
  emits the underlying events.
- [Failed deployment](/playbooks/failed-deployment.md), what to do when the
  rate drops.

[^slo-doc]: Platform SLO definitions
