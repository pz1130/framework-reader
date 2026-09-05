---
name: framework-reader
description: Look up NIST CSF 2.0 / ISO 27002:2022 / NIST SP 800-53 Rev.5 controls — what a control actually requires, what evidence is usually prepared, what an auditor asks next, and which controls in other frameworks it maps to. Use when the user asks what a control means, what audit materials to prepare, which control a practice belongs to, or how two frameworks correspond.
---

# Framework Reader

This repo's content graph: 3 frameworks, 1512 controls, 1909 mapping edges,
106 CSF interpretations.

## How to look things up

**Search first, then open the control.** People rarely remember the number.

```bash
fr search "log retention"          # titles and interpretation bodies
fr show NIST-CSF-2.0:DE.CM-01      # one control: mapping edges + seven fields
fr stats                           # graph size
```

`fr` is in this repo's `.venv`. If it is not on PATH, use `.venv/bin/fr`.

## How to read `fr show`

- `→ exportable` mapping edges are **official mappings** (NIST-authored).
  They can go into materials other people will read.
- `→ not exportable` edges are **L2 derived**. Sample accuracy 17%.
  **Clues only, never evidence** — do not copy them into any deliverable.
- `[AI draft, not yet confirmed by the author · state=draft]` — all 106
  CSF interpretations are currently in this state. **Say so when you
  paraphrase them.** Do not present a draft as signed.

## Three lines not to cross

1. **Copyrighted standard text is not in this repo, and must not be added.**
   NIST public-domain sources in `vendor/` are not committed. ISO 27002
   control bodies have never been here — only self-written labels, not
   official titles. If the user wants ISO source text, they use the copy
   they purchased.
2. **Do not tell the user an L2 derived edge is a correspondence.** See above.
3. **Do not invent control IDs.** If search misses, say so. This graph has
   CSF, ISO 27002, and 800-53 only — not PCI, NIS2, MLPS, or CIS.

## After you use it, log a line

The only validation this tool has is whether it is actually reached for
in a real task, and whether it was enough. After a lookup, remind the user:

```bash
fr usage --note "what I was doing / did it solve it / what I would have done without it"
```

The third question is the one that matters: if the answer is always
"I would have asked the model directly", that is the signal to stop.
See `docs/superpowers/specs/2026-08-19-framework-reader-design.md` §7.3.1.
