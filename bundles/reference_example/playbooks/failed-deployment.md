---
type: Playbook
title: Failed deployment
description: Triage a deployment that reports failed or hangs in rollout.
owner: team:platform
tags: [oncall, deployments, incident]
status: stable
generated: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-18T11:00:00Z }
verified: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-20T10:30:00Z }
stale_after: 2026-11-20T00:00:00Z
sources:
  - id: postmortem-0412
    resource: https://incidents.example.org/0412
    title: "Incident 0412: stuck rollouts after a registry outage"
    author: team:platform
    last_modified: 2026-05-09T00:00:00Z
---

# Trigger

A deployment reports `failed`, or stays in `rolling_out` for more than fifteen
minutes, on [deploy-orchestrator](/services/deploy-orchestrator.md).

# Steps

1. Read the deployment's terminal event. `image_pull_failed` and
   `readiness_timeout` are customer-side and need no platform involvement.
2. If the failure is `cluster_unreachable`, check the cluster registry before
   assuming the cluster is down. A stale credential presents identically to an
   unreachable control plane.[^postmortem-0412]
3. If more than one customer is affected in the same cluster, declare an
   incident. A single-tenant failure is not an incident.
4. Roll back with `deployctl rollback <deployment_id>`. Rollback re-applies the
   last manifest known to have passed readiness, which is not necessarily the
   previous deployment.

# Escalation

Page `team:platform` only after step 3 establishes multi-tenant impact.
Single-customer failures go to `team:support` with the deployment id.

# Related

- [Deploy success rate](/metrics/deploy-success-rate.md), the metric this
  playbook is trying to protect.

[^postmortem-0412]: "Incident 0412: stuck rollouts after a registry outage"
