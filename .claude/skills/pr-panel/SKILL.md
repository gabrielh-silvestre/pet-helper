---
name: pr-panel
description: "M3 PR review macro: reviews the draft PR in a fresh session, fixes the findings, posts the verdict and closes its tab."
argument-hint: "<slug> <PR number> <--normal | --panel --normal>"
disable-model-invocation: true
---

# /pr-panel — M3 of the macro pipeline

Runs step 7 of [fluxo-desenvolvimento.md](../../../docs/directives/fluxo-desenvolvimento.md) in the session `/deliver` launched; this session alone writes the worktree until it ends. Nobody answers questions here: product calls go to the owner-seat critic. Only the owner merges.

## Rules that hold for every step

- `F` below is `sh "$(git rev-parse --show-toplevel)/.claude/hooks/flow.sh"`; write unit state only through it. `R` is the main checkout (`dirname "$(git rev-parse --path-format=absolute --git-common-dir)"`); `.ignore/reviews/<slug>/` lives under `R`.
- Resume: `F get phase` tells the step to restart from; skip the steps already done. Only the session whose id is `F get owner` writes the worktree, so resume in this tab, or with `claude --resume "$(F get owner)"` from the worktree.
- Before every hexlog record, `F set phase <phase>`, then run `pet-helper-hexlog` passing that phase by name.
- Invoke skills with the Skill tool, naming the next skill only in the Skill call.
- TDD split: the `test-engineer` writes and changes tests, the `executor` writes production code (`test-standards` / `code-standards` first). Subagents `sonnet`/`haiku`, named from `AGENT-NAMES.md`.
- Product decision: launch the owner-seat critic (`oh-my-claudecode:critic`, `model: "opus"`) with the finding, its investigation, `docs/directives/estrategia.md` and `docs/requisitos.md`; apply its choice and log it in `.ignore/reviews/<slug>/decisions.md` with `origin: simulated`.
- Impasse (a step fails twice, the same hook block repeats, the critic cannot decide): record an `impasse` (phase, reason, what is done) without changing the state phase, stop and leave this tab open. The owner resumes in this tab.

## Steps

1. **Review.** `F set phase panel-review`. Invoke `review-pr` with `<PR> --auto` and the mode flags received. It stops after the investigation.
2. **Fix.** Fix every BLOCKING, URGENT and NORMAL finding (tests by the `test-engineer`, code by the `executor`); product calls go to the critic. MINOR findings stay open. Write `fixes.md` in the report folder: one line per finding ID with the commit or the decision.
3. **Simplify.** `code-simplifier` with `verifier-standards`, scope `trecho-entrega`, report only, over what the fixes touched; FAIL back to the executor, at most 2 rounds.
4. **Commit.** In the worktree: `git add -A`, one `fix:` commit with every correction, push. Read the pushed `git rev-parse HEAD`.
5. **Verdict.** Invoke `pr-verdict` with `<PR> --auto --head <sha>`. It posts the verdict pinned to that SHA and opens the MINOR follow-up issue.
6. **Record.** Record the `panel-review` phase with the mode and its reason from `R/.ignore/reviews/<slug>/mode.md`, the finding counts and the decisions file as `product_decisions`; then close the unit with the `unit-complete` gate through `pet-helper-hexlog`.
7. **Close.** `F clear`, then `herdr tab close "$HERDR_TAB_ID"`.
