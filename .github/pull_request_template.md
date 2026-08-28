## What changed

<!-- One line. Which bundle, which concepts. -->

## Type of change

- [ ] New concepts (hand-authored)
- [ ] Generated drafts from an extractor (`okf extract-github`)
- [ ] Owner review: promoting drafts to `stable`
- [ ] Tooling, schema, or profile change

## For content changes

- [ ] Every concept has an `owner` that appears in `okf-profile.yaml`
- [ ] Every `stable` concept has a `verified` entry naming who confirmed it
- [ ] Anything with a review cycle declares `stale_after`
- [ ] Claims that came from somewhere cite it via `sources` + a `[^footnote]`
- [ ] `resource` points at the actual source of truth, not a wiki page about it

**The part a machine could not have written** — what does this change say that
an extractor could not infer? Caveats, common misunderstandings, what to avoid.
If the answer is "nothing", this is generated metadata, not documentation, and
it should stay `status: draft`.

<!-- Answer here: -->

## For owner reviews (promoting `draft` → `stable`)

- [ ] What is this asset used for?
- [ ] What should analysts or agents avoid?
- [ ] What depends on it?
- [ ] What do people commonly get wrong about it?
- [ ] When should it be reviewed again? (`stale_after` set accordingly)

## Checks

- [ ] `okf validate bundles` passes
- [ ] `okf reindex bundles` produces no diff
