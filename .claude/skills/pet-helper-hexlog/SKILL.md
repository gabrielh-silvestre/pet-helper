---
name: pet-helper-hexlog
description: "Use right after each phase step of /pre-code, /deliver and /pr-panel in this repository, with the phase the macro names: record the phase in hexlog, link it to the previous phase and, when the last phase ends, check that the unit is complete. Also use to continue or close a unit of work (execution (one objective, one branch, one PR)) that hexlog already tracks."
---

# pet-helper-hexlog

hexlog project: `pet-helper`. Gate: `unit-complete`. Model: `hexlog-model.json`, next to this
file. Mechanics of every call (register, query, evaluate the gate) live in the
`hexlog-flow` skill; this file only carries the data of this project.

A unit of work is one execution (one objective, one branch, one PR) under `docs/specs/<slug>`.

## Phases

| Phase | Command | Record type | Target suffix | Artifact | Links |
|---|---|---|---|---|---|
| interview | M1 `/pre-code` step 2 | interview | interview | `docs/specs/<slug>/spec.md` | — |
| plan | M1 `/pre-code` step 3 | plan | plan | `docs/specs/<slug>/plan.md` | plan-derives-from-interview -> interview |
| architecture | M1 `/pre-code` step 4 | architecture | architecture | `docs/specs/<slug>/architecture.md` | architecture-derives-from-plan -> plan |
| approval | M1 `/pre-code` step 5 | approval | approval | `docs/specs/<slug>/architecture.md` | approval-derives-from-architecture -> architecture |
| delivery | M2 `/deliver` step 4 | delivery | delivery | `.omc/prd.json` | delivery-derives-from-approval -> approval |
| local-review | M2 `/deliver` step 5 | local-review | local-review | `.ignore/reviews/` | local-review-derives-from-delivery -> delivery |
| panel-review | M3 `/pr-panel` step 6 | panel-review | panel-review | `.ignore/reviews/prs/PR<N>/review.md` | panel-review-derives-from-local-review -> local-review |

An `impasse` record (phase, reason, progress) goes to `<process>.impasse` whenever a macro stops on its own; it has no links and the gate ignores it.

## What to do

All mechanics live in `references/unit-lifecycle.md` of the `hexlog-flow` skill; open that
skill and read the file when you open a unit, register a phase or close the unit.

1. Open or continue the unit: sections "Resolver o processo da unidade" and "Sem acerto".
2. After each macro step in the table, register the phase the macro names (never one
   guessed from the command text; a hook refuses any other): section "Registrar uma fase"
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
