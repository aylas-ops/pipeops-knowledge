---
type: Attested Computation
title: A computation with no computation
description: Declares the type but carries neither a runtime nor any SQL.
owner: team:platform
tags: [attested]
status: stable
executor:
  resource: /references/skills/does-not-exist.md
  receipt: "should be a list"
attester:
  resource: /references/attesters/missing.py
parameters:
  - { name: year }
---

# Notes

There is no `# Computation` section and no `computation:` field.
