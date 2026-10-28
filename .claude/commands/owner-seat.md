---
description: Architecture phase - the owner-seat critic decides the architecture the approved plan needs
argument-hint: "<slug>"
---

Run the architecture phase for the unit `docs/specs/$ARGUMENTS/`.

1. Read the approved plan in `.omc/plans/` for this unit and list every open architecture question it raises (layering, models, tracing, retrieval, module interfaces). Skip code-level choices; the executor decides those.
2. Launch one durable `oh-my-claudecode:critic` subagent with `model: "opus"` (the only opus exception in this repo, see `docs/directives/cadeira-do-dono.md`), named from the critic pool in `AGENT-NAMES.md`. Give it the questions, `docs/directives/`, `docs/adr/`, `GLOSSARY.md` and the owner's light suggestions found in the spec. It answers each question and never hands it back; it may use `sonnet`/`haiku` subagents for research.
3. A question that alters or contradicts an existing directive, premise or ADR is critical: stop and ask the owner before it counts.
4. Write `docs/specs/$ARGUMENTS/architecture.md` in Portuguese: one section per decision with the choice, the reason, the alternatives and `origin: simulated`. Record a decision as `docs/adr/NNNN-<name>.md` (English) only when it is hard to reverse, surprising and a real trade-off.
5. Show the owner the decision list; the owner's approval at pre-PR turns each origin into `simulated-approved`.
6. Then record the phase with the `pet-helper-hexlog` skill.
