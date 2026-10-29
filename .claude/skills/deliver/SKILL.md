---
name: deliver
description: "M2 delivery macro of pet-helper: runs an owner-approved unit from TDD delivery to a draft PR and hands off to /pr-panel. Invoked by /pre-code on approval, or by hand with a slug to resume."
argument-hint: "<slug>"
---

# /deliver — M2 of the macro pipeline

Runs steps 4 to 6 of [fluxo-desenvolvimento.md](../../../docs/directives/fluxo-desenvolvimento.md) and launches M3. Nobody answers questions here: product calls go to the owner-seat critic.

## Rules that hold for every step

- `F` below is `sh "$(git rev-parse --show-toplevel)/.claude/hooks/flow.sh"`; write unit state only through it. `R` is the main checkout (`dirname "$(git rev-parse --path-format=absolute --git-common-dir)"`); every `.ignore/` path below is under `R`, since `.ignore/` is not in the worktree.
- Resume: `F get phase` tells the step to restart from; skip the steps already done, reuse the existing worktree (`wt switch` without `--create`) and keep the existing PRD with its progress.
- Before every hexlog record, `F set phase <phase>`, then run `pet-helper-hexlog` passing that phase by name.
- Invoke skills with the Skill tool, naming the next skill only in the Skill call.
- TDD split: the `test-engineer` writes and changes tests, the `executor` writes production code. Instruct the `executor` to invoke `code-standards` first and the `test-engineer` to invoke `test-standards` first. Subagents `sonnet`/`haiku`, named from `AGENT-NAMES.md`.
- Product decision (a finding or story whose fix depends on behavior, scope or business rules): ask the unit's owner-seat critic, as in steps 2 to 6 of [owner-seat.md](../../commands/owner-seat.md): reuse the one in `critic.md` or launch it, and send the finding, its investigation, `docs/directives/estrategia.md` and `docs/requisitos.md`. Apply its choice and log it in `.ignore/reviews/<slug>/decisions.md` with `origin: simulated` and the hexlog id of its `premise`. A fix that would alter or contradict a directive, premise or ADR is critical: the owner decides it.
- Nothing calls the real model. One commit for the whole unit.
- Impasse (a step fails twice, the same hook block repeats, ralph stops converging, the critic cannot decide): record an `impasse` (phase, reason, what is done) without changing the state phase, stop. The owner resumes with `/deliver <slug>`.

## Steps

1. **Gate.** `F get` must show this slug with `approved: true`; otherwise stop and point the owner to `/pre-code <slug>`.
2. **Worktree.** Create the branch `<type>/<slug>` with `wt switch --create` from `main` (see the `worktrunk` skill) and run `F set worktree <absolute worktree path>`.
3. **PRD.** Fill [prd-template.json](prd-template.json) from `docs/specs/<slug>/plan.md` (one story per behavior or plan step, `<worktree>` replaced) and write it to `.omc/prd.json` of the main checkout; a hook rejects it without `userStories` or with a feedback command that prints on success. Run `omc ralph verify --write-baseline`; every baseline signature is empty.
4. **Delivery.** `F set phase delivery`. Invoke `oh-my-claudecode:ralph` on the PRD with the rules above. Each spec closes with the `code-simplifier` (invoke `verifier-standards`, scope `entrega`, report only; FAIL back to the executor, at most 2 rounds). Record the `delivery` phase. Done when every story passes and is architect-verified.
5. **Local review.** `F set phase local-review`. Invoke `review-pr` with `--normal --auto`, pointed at the worktree diff against `main` (no PR exists yet). Apply every finding, MINOR included; product calls go to the critic. Then the `code-simplifier` again over what the fixes touched (scope `trecho-entrega`). Record the `local-review` phase with `mode: normal`, the finding counts and the decisions file as `product_decisions`.
6. **Commit and PR.** In the worktree: `git add -A`, one Conventional Commits subject for the unit, `git push -u origin HEAD` (the verdict guard compares against the upstream), then open a draft PR to `main` through `mcp__github-official__create_pull_request` (body in the GitHub mode of `own-writing`, linking `docs/specs/<slug>/`).
7. **Review mode.** `--panel --normal` when the diff against `main` touches a business rule (RN01-RN04), Order access or Customer identity (ADR 0001), the model prompt, or more than 600 production lines (`git diff --numstat main...HEAD -- src/`); otherwise `--normal`; in doubt, panel. Write the mode and the criterion that decided it to `.ignore/reviews/<slug>/mode.md`.
8. **Hand-off.** Generate a session id (`cat /proc/sys/kernel/random/uuid`). Create the tab with `herdr tab create --cwd <worktree> --label <slug>-panel --no-focus`, then `herdr pane run <root pane> 'claude --session-id <id> "/pr-panel <slug> <PR number> <mode flags>"'`, with no permission flag: the session takes the settings default, like the owner's own sessions. Then, as this session's last write, `F set owner <id>`: from here only that session writes the worktree. End with one line to the owner: the PR link, the mode and the tab label. This tab stays open.
