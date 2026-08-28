---
type: Service
title: deploy-orchestrator
description: Schedules and executes customer application deployments across managed clusters.
resource: https://github.com/example-org/deploy-orchestrator
owner: team:platform
tags: [deployments, kubernetes, platform, service]
status: stable
generated: { by: process:extract_github, at: 2026-08-14T09:12:00Z }
verified: { by: human:ayomidelasaki@pipeops.io, at: 2026-08-20T10:30:00Z }
sources:
  - id: repo
    resource: https://github.com/example-org/deploy-orchestrator
    title: deploy-orchestrator repository
    author: team:platform
    last_modified: 2026-08-13T17:41:00Z
  - id: runbook-wiki
    resource: https://wiki.example.org/platform/deploy-orchestrator
    title: Platform wiki page for the orchestrator
    author: team:platform
    usage_count: 412
    last_modified: 2026-07-02T00:00:00Z
usage_window: { from: 2026-06-01T00:00:00Z, to: 2026-08-31T00:00:00Z }
---

# Summary

Accepts a deployment request, resolves the target cluster, renders manifests,
applies them, and watches rollout to completion or rollback. It is the only
component permitted to write to production clusters.[^repo]

# Interfaces

| Interface | Direction | Notes |
|-----------|-----------|-------|
| `POST /v1/deployments` | inbound | Creates a deployment. Idempotent on `request_id`. |
| `deployments.status` | outbound | Event stream consumed by the dashboard and billing. |
| Cluster API | outbound | One credential per managed cluster, rotated weekly. |

# Dependencies

- The build service, which supplies the image digest. A deployment with an
  unresolvable digest fails closed rather than deploying the previous image.
- The cluster registry, for target resolution.

# Operational notes

Rollout watching is the slow path: a deployment that appears "stuck" is almost
always a pod failing its readiness probe, not the orchestrator. Check the
workload before paging the platform team.[^runbook-wiki]

When a deployment fails, follow
[the failed deployment playbook](/playbooks/failed-deployment.md).

[^repo]: deploy-orchestrator repository
[^runbook-wiki]: Platform wiki page for the orchestrator
