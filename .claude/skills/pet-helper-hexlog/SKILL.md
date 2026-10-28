---
name: pet-helper-hexlog
description: "Use right after /oh-my-claudecode:deep-interview --frontier, /oh-my-claudecode:ralplan, /owner-seat, /oh-my-claudecode:ralph, /review-pr --normal, /review-pr --panel in this repository: record the phase in hexlog, link it to the previous phase and, when the last phase ends, check that the unit is complete. Also use to continue or close a unit of work (execution (one objective, one branch, one PR)) that hexlog already tracks."
---

# pet-helper-hexlog

hexlog project: `pet-helper`. Gate: `unit-complete`. Model: `hexlog-model.json`, next to this
file. Mechanics of every call (register, query, evaluate the gate) live in the
`hexlog-flow` skill; this file only carries the data of this project.

A unit of work is one execution (one objective, one branch, one PR) under `docs/specs/<slug>`.

## Phases

| Phase | Command | Record type | Target suffix | Artifact | Links |
|---|---|---|---|---|---|
| interview | `/oh-my-claudecode:deep-interview --frontier` | interview | interview | `docs/specs/<slug>/spec.md` | — |
| plan | `/oh-my-claudecode:ralplan` | plan | plan | `docs/specs/<slug>/plan.md` | plan-derives-from-interview -> interview |
| architecture | `/owner-seat` | architecture | architecture | `docs/specs/<slug>/architecture.md` | architecture-derives-from-plan -> plan |
| delivery | `/oh-my-claudecode:ralph` | delivery | delivery | `.omc/prd.json` | delivery-derives-from-architecture -> architecture |
| local-review | `/review-pr --normal` | local-review | local-review | `.ignore/reviews/` | local-review-derives-from-delivery -> delivery |
| panel-review | `/review-pr --panel` | panel-review | panel-review | `.ignore/reviews/prs/PR<N>/review.md` | panel-review-derives-from-local-review -> local-review |

## What to do

All mechanics live in `references/unit-lifecycle.md` of the `hexlog-flow` skill; open that
skill and read the file when you open a unit, register a phase or close the unit.

1. Open or continue the unit: sections "Resolver o processo da unidade" and "Sem acerto".
2. After each command in the table, register the phase: section "Registrar uma fase"
   (target `<process>.<target suffix>`, links, `key`, a phase that already has a record).
3. When the last phase is done, close the unit with the gate `unit-complete`: section
   "Fechar a unidade". Treat `hexlog-model.json` as read-only data; change it by running
   the `hexlog-setup` skill again.

## If something fails

- The process or project is missing in hexlog (the model may be out of date or applied to
  another hexlog): ask the developer to run the `hexlog-setup` skill again.
- The hexlog tools are unavailable (absent from the tool list, or the server is down):
  continue the developer's task without recording and tell the developer once; that single
  report covers every later phase.
- Change only what the developer asked for: read files only to fill the records.
