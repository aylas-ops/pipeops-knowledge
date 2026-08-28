# Contributing

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests
```

## Adding or changing knowledge

```bash
okf new Metric "Your metric" --path bundles/<domain>/metrics --owner team:<yours>
# ...write the part a machine could not have written...
okf reindex bundles
okf validate bundles
okf index bundles && okf query "a question this should answer"
```

Read [docs/authoring-guide.md](docs/authoring-guide.md) first. The short
version: an extractor can produce the title, schema and links; you are here for
the caveats, the misunderstandings, and the things to avoid.

Open a PR. The template asks the five review questions; answer them rather than
ticking them.

## Bootstrapping from a source system

```bash
okf extract-github --org your-org --bundle bundles/<domain> --dry-run
okf extract-github --org your-org --bundle bundles/<domain>
okf reindex bundles
```

Generated concepts land as `status: draft` and will fail validation until a
human assigns an `owner`. That is intentional. Open the PR with the failures
visible — they are the review checklist.

Existing files are never overwritten without `--overwrite`, so re-running an
extractor after review is safe.

## Changing the tooling

Every rule the validator enforces has a document in
`tests/fixtures/broken_bundle/` that violates it and a test that asserts it
fires. Adding a rule means adding both; a rule with no failing fixture will
eventually stop working and nobody will notice.

`tests/test_validate.py::TestReferenceBundle` asserts the shipped bundle
produces **zero** findings — errors and warnings. If a new rule makes the
reference bundle noisy, either the rule is wrong or the bundle needs fixing.
Do not silence it by lowering the severity.

## Changing a house rule

Edit `okf-profile.yaml` and say in the PR description what the rule was
costing. See [docs/house-profile.md](docs/house-profile.md) for the reasoning
behind each one — most of them exist because the spec is deliberately more
permissive than a maintained repository can afford to be.

## Conventions

- Spec references in comments use the section number: `SPEC §5.2`.
- Python: standard library plus PyYAML. Adding a dependency needs a reason in
  the PR — the validator has to run in CI on a cold machine.
- Anything derived (`index.md`, the retrieval database) is generated, never
  hand-edited. CI fails on index drift.
