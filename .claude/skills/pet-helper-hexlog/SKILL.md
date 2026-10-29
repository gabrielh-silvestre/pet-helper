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

## Owner seat

The roles, the answer format and the audit are in [owner-seat/contract.md](owner-seat/contract.md); the rules the occupant decides by, in [owner-seat/criteria.md](owner-seat/criteria.md); the class of a question, the action catalog and the approval, in `docs/directives/cadeira-do-dono.md`. Names: `gap`, `premise`, `evidence`, `audit`, `fills-gap`, `audits`, gate `gaps`. The occupant is the durable critic that [owner-seat.md](../../commands/owner-seat.md) launches in a Herdr tab; this project has no `recipe-*.md`.

When you hit a gap you cannot close alone (a question no rule of the project answers):

1. Record the gap: type `gap`, target `<process>.gap.<short>`, with the `Class:` and `Nature:` literal at the start of `context`.
2. Check the seat precondition as the contract says in "Before sitting" (the trace fields of `premise` in the process). A process opened before the owner seat was set up does not have them, and then nobody sits: go to step 4.
3. Ask the critic as `owner-seat.md` says (steps 4 to 6) and read the answer back with `query` (`relatedTo` the gap id) before going on.
4. If the critic fails, if the precondition does not hold or if nobody sits, ask the developer and record the answer with `answeredBy: human`, as the contract says. With no developer available (headless session), leave the gap open. In a process opened before the owner seat was set up, the gap type and `answeredBy` are not pinned: record only what the pinned types accept, or keep it outside hexlog.

Audit report, to run when the developer asks for it and also when you close the unit or open the PR (needs the `HEXLOG_CHECKOUT` variable, set once per developer to the hexlog checkout):

```text
node "$HEXLOG_CHECKOUT/scripts/seat-report.ts" pet-helper --types gap=gap,premise=premise,audit=audit
```

Before running it, check that `$HEXLOG_CHECKOUT/scripts/seat-report.ts` and `$HEXLOG_CHECKOUT/node_modules` exist and that `node --version` satisfies `^24.18.1` (24.x series, 24.18.1 or newer; Node 25 and later are out of range). If the variable is unset or a check fails, ask the developer; do not guess a path. The report lists `simulated-approved` answers under "answers with an unrecognized answeredBy": that is expected here.

When you close a unit, evaluate the gate `gaps` in addition to the unit gate `unit-complete`: it fails while a gap has no answer.

## If something fails

- The process or project is missing in hexlog (the model may be out of date or applied to
  another hexlog): ask the developer to run the `hexlog-setup` skill again.
- The hexlog tools are unavailable (absent from the tool list, or the server is down):
  continue the developer's task without recording and tell the developer once; that single
  report covers every later phase.
- Change only what the developer asked for: read files only to fill the records.
