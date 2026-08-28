# Subdirectories

* [attesters](attesters/) - Deterministic verification code. No LLM, no network.
* [skills](skills/) - Executor instructions for running Attested Computations.

# About references/

The `references/` convention (SPEC §6.3) mirrors external material, run
instructions, and code as first-class members of the bundle, so that
`executor.resource` and `attester.resource` point at something that ships and
versions alongside the contract they serve.
