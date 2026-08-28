# The house profile

What `okf-profile.yaml` adds on top of OKF v0.2, and why each addition earns
its place. Read this before arguing with the validator.

## The problem the profile solves

OKF v0.2 requires exactly one frontmatter key: `type`. §11 goes further and
says consumers **MUST NOT** reject a bundle for missing optional fields,
unknown types, unknown keys, broken links, or missing index files.

That permissiveness is deliberate and correct *for a format*. A bundle
published by another organisation should be readable even if their conventions
differ from ours. But "a consumer must accept this" is not the same claim as
"we should merge this into our repository". The spec governs what we must
tolerate when reading; the profile governs what we allow ourselves to write.

So the validator has two layers, and they never blur:

| Layer | Source of authority | Can we relax it? |
|-------|--------------------|------------------|
| Conformance | OKF v0.2 SPEC §11 | No. Failing means it is not OKF. |
| House rules | `okf-profile.yaml` | Yes, by changing the file in a PR. |

## The additions, and the reasoning

### `owner` (required, not a spec field)

The single highest-value addition. An OKF document with no accountable owner
has no one to ask when it is wrong, no one to review it when it goes stale, and
no one to blame when an agent cites it into a bad decision. The spec has no
`owner` because ownership is an organisational concept, not a format concern —
which is exactly why we have to add it.

Owners use the spec's actor convention (§7): `team:platform`, `human:ada`. They
must appear in the `owners:` registry, so a typo'd team name fails CI instead
of silently creating an unowned document.

### `title`, `description`, `tags` (required)

The spec calls these "recommended". They are what makes retrieval work:
`description` is the preview and a ranking signal, `tags` are the filter
dimension, `title` is what appears in a citation. A concept without them is
findable only by full-text luck.

### Broken links are an error

SPEC §6.1: "Consumers MUST tolerate broken links: a link whose target does not
exist in the bundle is not malformed; it may simply represent not-yet-written
knowledge."

That is right for a *consumer*. As an *author* it is a licence to ship a
knowledge graph full of dead ends. We resolve links in CI. If a concept is
genuinely not written yet, name it in prose rather than linking to a file that
does not exist.

### `stale_after` required for types with a review cycle

SPEC §5.5 makes `stale_after` optional. But the interesting property of a
knowledge base is not that it was correct when written — it is whether it is
correct now. A `Metric` or `Playbook` with no expiry is a document that will
never be re-reviewed, because nothing will ever ask.

Which types need one is configured in `stale_after_required_types`.

### No duplicate stable titles within a type

Two stable `Metric` concepts both called "Active users" make every citation
ambiguous and guarantee that half the organisation reads the wrong one.
Deprecate one, or make the titles distinguish them.

### `resource` must match a known URI scheme

SPEC §4.1 wants `resource` to be "a URI that uniquely identifies the underlying
asset". A `resource` pointing at a wiki page *about* the table, rather than the
table, quietly defeats the purpose. The pattern list is a weak check that
catches the obviously wrong cases; add legitimate schemes to
`resource_uri_patterns` as they come up.

## Severities

Every rule maps to `error`, `warning`, or `off`.

- **error** — fails CI. Reserved for conformance and for the house rules above.
- **warning** — reported, does not block. Everything about *decay*:
  `stale`, `review_overdue`, `unverified`, `draft_on_main`, `orphan`.
- **off** — the rule does not run.

Decay findings are warnings on purpose. A knowledge base where a passing month
turns the build red teaches people to disable the check. Instead the weekly
scheduled CI run surfaces them as a report, and `okf validate --strict` is
available when you want a hard gate.

## Types

`allowed_types` is a list, and an unlisted type is a **warning**, not an error.
The spec explicitly forbids a central type registry (§4.1), and a producer
should be able to introduce a type cheaply. The warning exists so nobody
introduces `Runbook` and `runbook` and `RunBook` without noticing. Add the new
type to the profile in the same PR that introduces it.

## Divergence from the PDF implementation manual

If you are working from the *Open Knowledge Format Implementation Manual* PDF,
note that it describes a v0.1-era schema. Where they conflict, this repository
follows the published v0.2 spec:

| Manual | OKF v0.2 | Why |
|--------|----------|-----|
| `updated: <date>` | `generated: { by, at }` | §13.1 — the v0.1 `timestamp` is superseded; a bare date has no UTC offset, and "who wrote it" matters as much as when. |
| `status: draft \| reviewed \| approved \| deprecated \| archived` | `status: draft \| stable \| deprecated` | §5.4 — "reviewed" and "approved" are trust claims, and trust belongs in `verified`, where it records *who* and *when*. |
| Body `# Citations` list | `sources` frontmatter | §13.1 — provenance moved into frontmatter so it is filterable and machine-readable. |
| (not present) | `sources[]` credibility signals, `stale_after`, Attested Computations | Added in v0.2. |

The validator rejects the retired `status` values with a message pointing at
the replacement, so a document written from the manual fails loudly rather
than silently carrying a status nothing understands.

## Changing a rule

Edit `okf-profile.yaml`, open a PR, and say in the description what the rule
was costing. `tests/test_validate.py` asserts that severity changes take
effect, so a rule that is turned off stays off deliberately rather than by
accident.
