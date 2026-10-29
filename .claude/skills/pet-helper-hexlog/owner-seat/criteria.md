# Owner seat criteria

The occupant of the owner seat answers the gaps the executor cannot close alone. It decides
by the rules below and records which rule it used. This file belongs to the developer: edit
it freely, and keep it in version control, because the setup never overwrites it.

## How the occupant uses this file

- A rule applies when its text covers the question of the gap. The occupant cites the rule
  by its key, exactly as written between the asterisks, and attaches the version of this file
  it read.
- When no rule covers the question, the occupant decides by judgment and records the answer
  as having no criteria, so the audit lists it first.
- A rule may point to a file in the repository (a doc, a contract, a schema). The occupant
  reads that file before answering and records that it did.

## Writing rules

- One bullet per rule, in the form `- **<key>**: <text>`.
- The key is stable: lowercase letters, digits and hyphens only. Never rename a key and never
  reuse one. Answers already recorded cite the old key. The key has at most 120 characters,
  the limit of the `rule` field of the answer; a longer key makes the answer fail.
- The trace the occupant records is self-declared, and comparing it with the rules is a manual
  step of the audit.
- To drop a rule, keep the bullet and end its text with `(retired)`. The occupant ignores
  retired rules.
- Say what to decide, not how the project works in general. A short, concrete rule is cited
  more reliably than a long one.

## Rules

- **strategy-premises**: Read `docs/directives/estrategia.md` first and decide inside its premises. A choice that contradicts one is critical: the occupant only recommends, and only the owner amends the file.
- **seat-rules**: Follow `docs/directives/cadeira-do-dono.md` for the class of the question, the action catalog, degradation and what to record.
- **adr-decisions**: Do not reopen a decision recorded in `docs/adr/`. A choice that alters or contradicts an ADR is critical.
- **architecture-baseline**: `docs/architecture.md` is the baseline the owner approved. A choice that deviates from it is critical; one that only refines it is common.
- **requirements-and-glossary**: Stay inside `docs/requisitos.md` and name things with the terms of `GLOSSARY.md`.
