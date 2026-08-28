# Authoring guide

How to write a concept that is worth retrieving.

## Start from a template

```bash
okf new Metric "Deploy success rate" --path bundles/platform/metrics --owner team:platform
okf new Playbook "Failed deployment" --path bundles/platform/playbooks --owner team:platform
```

Then `okf reindex bundles && okf validate bundles`.

## The one rule that matters

**Write the part a machine could not have written.**

An extractor can produce the title, the schema, the owner, the link to the
source of truth. It cannot produce:

- what this is actually used for,
- what to avoid doing with it,
- what people consistently get wrong about it,
- what silently depends on it.

If your document contains only the first list, you have generated metadata, not
documentation, and it should stay `status: draft`. The reference bundle's
[Caveats section](../bundles/reference_example/metrics/deploy-success-rate.md)
is the shape to aim for: three specific misunderstandings, each with the
consequence of getting it wrong.

## Frontmatter, field by field

```yaml
---
type: Metric                    # required by the spec; the routing key
title: Deploy success rate      # what appears in a citation
description: One sentence.      # preview + retrieval ranking; keep it to a sentence
resource: bigquery://p.d.t      # the actual asset, not a page describing it
owner: team:platform            # accountable for correctness; must be in the registry
tags: [slo, deployments]        # the filter dimension
status: stable                  # draft | stable | deprecated
generated: { by: human:ada, at: 2026-08-19T15:20:00Z }
verified: { by: human:ada, at: 2026-08-20T10:30:00Z }
stale_after: 2026-11-20T00:00:00Z
sources:
  - id: slo-doc
    resource: https://wiki.example.org/platform/slo
    title: Platform SLO definitions
---
```

### `generated` vs `verified`

They answer different questions and are kept apart on purpose (SPEC §5.2):

- `generated` — who last **changed the content**.
- `verified` — who has **confirmed it is still true**.

Content can change without re-confirmation, and facts can be re-confirmed
without an edit. `verified` is a list, so a human sign-off and a nightly
process can both be recorded:

```yaml
verified:
  - { by: human:ada, at: 2026-08-20T10:30:00Z }
  - { by: process:slo-nightly, at: 2026-08-27T02:00:00Z }
```

Trust tier is derived, never stored: no `verified` → `unverified`; only
non-human actors → `machine-confirmed`; any `human:` actor → `human-reviewed`.
Retrieval surfaces the tier next to every result.

### Timestamps

ISO 8601 **with an explicit UTC offset**: `2026-08-20T10:30:00Z`. A bare date
is rejected. Freshness is a comparison, and a comparison against an ambiguous
instant is a bug waiting for a timezone.

### `stale_after`

An absolute instant, not a TTL. Set it to when the underlying thing next gets
reviewed — the quarter end for a metric, the next incident review for a
playbook. Past it, retrieval hides the document unless asked and labels it
`STALE` when shown.

## Attribution

Claims that came from somewhere get a footnote whose label is a `sources[].id`:

```markdown
Mobile events may arrive up to 24 hours late.[^ga4-schema]

[^ga4-schema]: GA4 BigQuery Export schema
```

The label is the join key into `sources` — the validator checks it resolves.
Labels are keyed rather than positional because agents rewrite these documents
constantly, and `sources[0]` misattributes silently the moment the list is
reordered.

## Links

Prefer the bundle-relative form beginning with `/`:

```markdown
See [the orchestrator](/services/deploy-orchestrator.md).
```

It survives a file being moved within its subdirectory. Relative links work
too. Path-valued frontmatter fields (`computation`, `executor.resource`,
`attester.resource`) follow the same rules — a bare `references/x.md` resolves
relative to *the concept's own directory*, which is usually not what you meant.

## Body structure

Favour structure over prose: headings, tables, lists, fenced blocks. Chunking
splits on headings, so a well-headed document produces citations that point at
the paragraph that answered the question rather than the whole file.

Conventional headings: `# Schema`, `# Examples`, `# Computation`. Beyond those,
use whatever the content wants — `# Caveats`, `# Trigger`, `# Steps`,
`# Escalation`, `# Related` all read well and retrieve well.

## Attested Computations

When a document reports a *number*, the number deserves more than a definition.
An Attested Computation carries the sanctioned way to compute it, so a consumer
can confirm the agent ran the blessed query instead of improvising:

```yaml
type: Attested Computation
runtime: postgres
parameters:
  - { name: window_days, type: integer, required: true }
executor:
  resource: /references/skills/run-on-postgres.md
  receipt: [statement_id, executed_sql, result]
attester:
  resource: /references/attesters/sql_equality.py
```

The agent may supply *values* for declared parameters and nothing else. The
attester re-derives the binding and compares it against what actually ran, so a
rewritten query or a dropped filter fails mechanically rather than by judgement.

`verified` and attestation are different things and you want both: `verified`
says the definition still matches policy; attestation says this particular run
produced the value the sanctioned way. A stale definition can attest cleanly,
and a freshly-verified definition still needs attestation on every run.

See [the reference computation](../bundles/reference_example/computations/deploy-success-rate.md)
for a complete worked example.

## Before you open the PR

```bash
okf reindex bundles     # index.md files are derived; regenerate them
okf validate bundles    # conformance + house rules
okf index bundles && okf query "a question this should answer"
```

That last line is the real test. If the document does not come back for the
question it was written to answer, the headings or the `description` are wrong.
