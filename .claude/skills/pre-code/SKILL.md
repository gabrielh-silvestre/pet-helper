---
name: pre-code
description: "M1 pre-code macro: interview, plan and owner seat for one unit of work, ending at the owner's approval, which starts /deliver."
argument-hint: "<objective> | <slug to resume>"
disable-model-invocation: true
---

# /pre-code — M1 of the macro pipeline

Runs steps 1 to 3 of [fluxo-desenvolvimento.md](../../../docs/directives/fluxo-desenvolvimento.md) without the owner typing between them. The owner answers the interview and the exit question; everything else chains.

## Rules that hold for every step

- `F` below is `sh "$(git rev-parse --show-toplevel)/.claude/hooks/flow.sh"`; it keeps the unit state the hooks read. Write state only through it.
- Before every hexlog record, `F set phase <phase>`, then run the `pet-helper-hexlog` skill passing that phase by name. The phase comes from this skill, never from the command text.
- Invoke skills with the Skill tool. Name the next skill only in the Skill call, so the OMC keyword detector never sees it in prose.
- Subagents: `sonnet` or `haiku`, named from `AGENT-NAMES.md`; the owner-seat critic is the one `opus` exception.
- Impasse (a step fails twice, a hook blocks the same call again, the critic cannot close a question): record an `impasse` (phase, reason, what is done) with `pet-helper-hexlog` without changing the state phase, and stop. The owner resumes with `/pre-code <slug>`.

## Steps

1. **Unit.** With an objective, derive a kebab-case slug of up to four terms and run `F start <slug>`. With a slug whose state exists, read `F get phase` and resume at that step.
2. **Interview.** `F set phase interview`. Invoke `oh-my-claudecode:deep-interview` with `--frontier <objective>`, telling it: the spec stays in `.omc/specs/` with the slug in its file name; every assistant behavior also comes out as an eval case; run the existing-tools research step and put the finding in the spec. When the spec is settled, go to step 3 without offering an execution menu. Copy the spec through `anthropic-skills:humanizar` into `docs/specs/<slug>/spec.md`. Record the `interview` phase. Done when both files exist and the record is in.
3. **Plan.** `F set phase plan`. Invoke `oh-my-claudecode:ralplan` (never `--interactive`) on the spec, telling it: the plan keeps the slug in its `.omc/plans/` file name and has a `## Ferramentas existentes` section naming, for every piece built from scratch, the existing tool considered and why it does not fit. Copy it through `anthropic-skills:humanizar` into `docs/specs/<slug>/plan.md`, keeping that heading. Record the `plan` phase.
4. **Architecture.** `F set phase architecture`. Invoke `owner-seat` with the slug. Skip its steps 5 and 6: the owner sees the decisions at step 5 here, and this step records the phase. Record the `architecture` phase.
5. **Exit.** Check that `docs/specs/<slug>/{spec,plan,architecture}.md`, the `## Ferramentas existentes` heading and the slugged files in `.omc/specs/` and `.omc/plans/` exist; create what is missing. Ask the owner with AskUserQuestion, showing the plan summary and the architecture decision list:
   - **Approve**: run `F approve` (it re-checks the same list and refuses on a gap: fix it and rerun). Turn every `origin: simulated` in `architecture.md` into `simulated-approved`. Commit `docs/specs/<slug>/` on `main` (`docs: add <slug> spec, plan and architecture`), so the worktree `/deliver` creates carries them. `F set phase approval`, record the `approval` phase, then invoke the `deliver` skill with the slug.
   - **Request change**: take the owner's text and redo from the step it touches onward (a plan change reruns plan and architecture, so the new architecture derives from the new plan), recording each phase again superseding the earlier record, and ask again.
   - **Stop**: end here. The state stays for `/pre-code <slug>`.
