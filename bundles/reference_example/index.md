---
okf_version: "0.2"
---

# Subdirectories

* [computations](computations/) - Sanctioned SQL, one Attested Computation per figure.
* [metrics](metrics/) - Business definitions of the numbers people ask about.
* [playbooks](playbooks/) - What an on-call engineer or agent should actually do.
* [references](references/) - Executor skills and deterministic attester code.
* [services](services/) - The systems this bundle grounds against.

# About this bundle

This is a **reference example**, not real PipeOps knowledge. It exists so that
`okf validate`, `okf index` and `okf query` have something to run against, and
so the first real bundle has a worked example of every frontmatter family to
copy from: provenance (`sources`), trust (`generated`, `verified`), lifecycle
(`status`, `stale_after`), and an Attested Computation with a real attester.

Delete it once your first domain bundle lands, or keep it as a fixture.
