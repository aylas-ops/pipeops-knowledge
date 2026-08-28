# Operating model

Who owns what, and what has to happen for this to still be true in a year.

## Roles

### Knowledge platform owner (one person)

Owns the machinery, not the content: the house profile, the validator, the
repository structure, the extractors, and the indexing pipeline. Reviews any PR
touching `tools/`, `okf-profile.yaml`, or `.github/`.

The load-bearing part of the role is saying no to schema growth. Every field
added to `required_fields` is a tax on every future concept, and a field that
no consumer reads is pure cost.

### Domain owners (one team per bundle)

Own content accuracy and review cadence inside their bundle. Named in
`.github/CODEOWNERS`, and in the `owner:` frontmatter of every concept they are
accountable for. A concept whose `owner` is not a real team with a real review
habit is a document nobody will fix.

### Everyone else

Opens PRs. Content changes go through review like code.

## Rules

1. **Pull requests for all substantive changes.** No direct pushes to `main`.
2. **`okf validate` is a required check.** Conformance failures block.
3. **Generated content lands as `draft`** and is excluded from retrieval until
   an owner reviews it and records a `verified` entry.
4. **New certified metrics, production datasets, dashboards, runbooks and
   services require an OKF concept** as part of the change that introduces
   them — not as a backfill later.
5. **Index files are derived.** Run `okf reindex`; CI fails on drift.
6. **Ownership registry is authoritative.** Adding an owner is a PR that the
   platform owner reviews.

## Review cadence

Configured per type in `okf-profile.yaml` under `review_cadence_days`, and
enforced softly: exceeding it produces a `review_overdue` warning, surfaced by
the weekly scheduled CI run rather than blocking a merge.

| Type | Cadence | Why |
|------|---------|-----|
| Metric, Attested Computation | 90 days | Tracks the quarterly definition review. |
| Playbook, Runbook | 30 days | Incident-path content is worse than useless when wrong. |
| Service | 180 days | Changes slowly; the repo link keeps it honest in between. |
| Decision | 365 days | ADRs are historical records; they age into context, not error. |

Reviewing means confirming the content against its sources and adding a
`verified` entry — not re-reading and moving on. If nothing changed, the
`verified` timestamp is the whole deliverable.

## Decay is a warning, not an error

A knowledge base where the passage of time turns the build red teaches people
to disable the check. So `stale`, `review_overdue`, `unverified`, `orphan` and
`draft_on_main` report without blocking, and the Monday scheduled run puts them
in a job summary.

If you want a hard gate for a particular bundle, `okf validate --strict` is
there. Use it deliberately, on a bundle whose owners have signed up for it.

## Access control

OKF concentrates business context — that is the point, and it is also the risk.
Two rules:

1. **Repository permissions must match the source systems.** If a table's
   description reveals something the table's ACL protects, the concept
   describing it must be protected the same way.
2. **The serving layer must not become a way around IAM.** `okf serve` binds to
   loopback and has no auth for exactly this reason. Anything beyond the pilot
   needs document-level ACL propagation before it is exposed to more people
   than can already read every bundle.

Splitting into multiple repositories, rather than tightening rules inside one,
is the right answer when a domain's context is more sensitive than the rest.

## Growth

Add domains one at a time, ordered by which has the worst repeated-question
problem. Each new bundle needs, on day one: a directory under `bundles/`, a
`CODEOWNERS` line, its owning teams in the registry, and a named owner who has
agreed to the cadence.

A bundle that arrives without an owner is a bundle that will be stale in a
quarter and deleted in a year.
