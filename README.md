# pipeops-knowledge

A portable knowledge layer in [Open Knowledge Format][spec] v0.2: plain
markdown with YAML frontmatter, reviewed in Git, validated in CI, indexed for
retrieval, and cited by agents.

This repository ships the **toolchain and the governance**, plus one reference
bundle. The first real domain is yours to pick.

```bash
pip install -e .

okf validate bundles          # conformance + house rules
okf index bundles             # build the retrieval index
okf query "why did the deploy fail"
okf serve                     # localhost endpoint for an agent tool
```

## What OKF actually is

One concept per markdown file. Frontmatter carries the structured part;
the body carries the part a human wrote. Directories group concepts, links
express relationships, and `index.md` lets an agent see what exists before
opening anything.

```markdown
---
type: Metric
title: Deploy success rate
description: Share of customer deployments that reach a healthy rollout.
owner: team:platform
tags: [slo, deployments]
status: stable
generated: { by: human:ada, at: 2026-08-19T15:20:00Z }
verified: { by: human:ada, at: 2026-08-20T10:30:00Z }
stale_after: 2026-11-20T00:00:00Z
sources:
  - id: slo-doc
    resource: https://wiki.example.org/platform/slo
    title: Platform SLO definitions
---

# Definition

...per the SLO definition.[^slo-doc]

[^slo-doc]: Platform SLO definitions
```

The spec requires exactly one key: `type`. Everything else above is either an
optional OKF family or a house rule — see [docs/house-profile.md](docs/house-profile.md)
for which is which and why.

## Layout

```
bundles/                   knowledge, one directory per domain
  reference_example/       a worked example of every frontmatter family
okf-profile.yaml           house rules: required fields, owners, severities
tools/okf/                 the toolchain (validate, index, query, serve, extract)
docs/                      house profile, authoring guide, pilot plan, operating model
tests/                     97 tests, including a fixture that violates every rule
.github/workflows/         CI: conformance, index drift, weekly freshness report
```

## The pipeline

```
metadata source → extractor → draft OKF markdown → pull request
   → owner review → merge → index → agent answers with citations
```

Generated files land as `status: draft` and are excluded from retrieval until
a human reviews them. That is the whole discipline: **generated metadata is not
documentation.** The caveats, the common misunderstandings, the "don't do this"
— those are why the knowledge base is worth more than the warehouse it was
extracted from.

## Commands

| Command | What it does |
|---------|--------------|
| `okf validate [path]` | Spec conformance + house rules. `--format github` for PR annotations, `--strict` to fail on warnings. |
| `okf index [path]` | Build the SQLite/FTS5 retrieval index. |
| `okf query TEXT` | Search with metadata filters. `--format context` emits a grounding block for an agent prompt. |
| `okf get ID` | Print one concept, with inbound and outbound links. |
| `okf stats` | Coverage: concepts by type, status, trust tier; broken links. |
| `okf reindex [path]` | Regenerate `index.md` files. `--dry-run --check` fails if they drifted. |
| `okf new TYPE TITLE` | Scaffold a concept from a template. |
| `okf serve` | Localhost retrieval endpoint (`/search`, `/concept/<id>`, `/stats`). |
| `okf extract-github` | Generate draft concepts from repository metadata. |

## Bootstrapping a domain from GitHub

```bash
okf extract-github --org your-org --bundle bundles/platform
okf reindex bundles
okf validate bundles
```

The extractor reads READMEs, ADRs, topics, and CODEOWNERS. Where CODEOWNERS
names nobody, it deliberately leaves `owner` unset so `okf validate` fails —
assigning an accountable owner is the one thing a machine cannot infer, and a
red check is what gets it done. Pass `--default-owner team:platform` to opt out.

It prefers the `gh` CLI as transport when installed, so it reuses your existing
auth and sidesteps local TLS trust problems.

## Retrieval and citations

`okf query --format context` produces a block designed to make citing the path
of least resistance:

```
[1] Deploy success rate › Caveats
    citation: reference_example/metrics/deploy-success-rate.md#caveats
    type: Metric | owner: team:platform | trust: human-reviewed | status: stable
    source of truth: https://wiki.example.org/platform/slo
    ...
Cite every claim with the `citation` value of the passage it came from.
```

Drafts, deprecated documents, and anything past its `stale_after` are excluded
by default and labelled with a `WARNING` when explicitly included. Trust tier
is derived per the spec: `unverified` → `machine-confirmed` → `human-reviewed`.

Retrieval is BM25 over heading-level chunks — no vector database needed to
prove the pilot. `okf.index.embed_chunks` is the seam for adding embeddings
once the pilot has earned the budget; the schema already has the column.

## What is enforced, and by whom

The OKF spec is permissive on purpose: consumers "MUST NOT reject" a bundle for
missing optional fields, unknown types, or broken links (SPEC §11). That is
right for exchanging bundles between organisations and wrong for a repository
we maintain. `okf-profile.yaml` is where we tighten it, as data rather than
code, so changing a rule is a reviewable one-line diff.

Two layers, kept separate in the validator:

- **Conformance** — parseable frontmatter, a `type`, ISO 8601 timestamps with
  an explicit offset, the `status` enum, `sources` shape, the Attested
  Computation contract. Never relax these.
- **House rules** — required `owner`/`title`/`description`/`tags`, owners drawn
  from a registry, no broken links, no duplicate stable titles, `stale_after`
  on anything with a review cycle.

Hygiene findings (`stale`, `unverified`, `draft_on_main`, `orphan`,
`review_overdue`) are warnings. They report; they do not block.

## Where to start

1. Read [docs/30-day-pilot.md](docs/30-day-pilot.md) and pick one domain.
2. Run an extractor into `bundles/<domain>/`, open a review PR.
3. Have the owning team answer the five questions in the PR template.
4. `okf index && okf serve`, point an agent at it, measure whether the answers
   got better.

Then delete `bundles/reference_example/`, or keep it as a fixture.

[spec]: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
