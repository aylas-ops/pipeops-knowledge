---
type: Widget
title: Widget
description: A widget, used to prove the validator bites.
owner: nobody-in-particular
resource: ftp://legacy.example.org/widget
tags: [widget]
status: approved
generated: { by: "some agent", at: 2026-03-01 }
verified: { at: 2026-03-02T00:00:00Z }
sources:
  - id: only-a-title
    title: A source with no resource
---

# Summary

This links to [something that does not exist](./missing.md) and cites a
footnote that binds to nothing.[^nowhere]

```markdown
# This heading is inside a fence and must not be treated as a section
[a link inside a fence](./also-missing.md)
```

[^nowhere]: Not a real source
