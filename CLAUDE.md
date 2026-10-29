# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Project

Take-home challenge: a customer-service assistant (Python, LangGraph) for a fictional pet e-commerce, built and measured over a small synthetic data kit. Delivery deadline: 2026-11-03.

Status: no assistant code yet; the verification harness described in `docs/directives/fluxo-desenvolvimento.md` ("Gate de verificação") is in place.

## Commands

```sh
uv sync                          # tools pinned in uv.lock, Python 3.12
uv run prek install              # once per clone: pre-commit, commit-msg and pre-push hooks
uv run prek run --all-files      # every static check (what CI runs)
uv run pytest -q --cov           # tests without model calls, 80% branch coverage floor on src/
uv run pytest tests/harness/test_hooks.py::test_it_should_allow_paths_outside_the_held_out_cases   # a single test
uv run cz check --rev-range main..HEAD -l 80   # commit messages (commitizen, config in pyproject.toml)
uv run pytest -m llm             # tests that call a real model; never run automatically
uv run deptry .                  # unused or missing dependencies
```

Agent guards live in `.claude/settings.json`. Native `permissions.deny` rules block git hook bypasses and edits to `docs/directives/estrategia.md`. Hooks (jq filters in `.claude/hooks/`, tested by `tests/harness/test_hooks.py`) block `executor` edits under `tests/` and any access to `evals/held_out/` unless the session was started with `PET_HELPER_HELD_OUT=1` (the isolated held-out generation flow), autofix lint through the prek ruff hooks on every edited `.py`, and run prek on all files plus the pre-push stage whenever an `executor` subagent stops (failures go back to it).

## Read before working

- `docs/requisitos.md`: the challenge rewritten as requirements (kit, assistant behavior, business rules RN01-RN04, deliverables). The original statement is confidential and stays in the gitignored `.ignore/`; never commit it or copy it.
- `GLOSSARY.md`: the domain language (Customer, Session Customer, Order, Return, Escalation Reason...). Use these names in code; the Portuguese term next to each entry is the customer-facing word.
- `docs/directives/estrategia.md`: the owner's premises. Only the owner edits this file.
- `docs/directives/cadeira-do-dono.md`: who decides what. Product and strategy go to the owner; architecture goes to the owner-seat critic (`opus`, the only subagent exception); code-level choices are decided by the executor and recorded, not asked.
- `docs/directives/fluxo-desenvolvimento.md`: the 8-step flow grouped into three macros, git hooks, CI and agent hooks.
- `docs/directives/loop-melhoria.md`: the eval-driven improvement loop.
- `docs/adr/`: decisions that must not be re-litigated without revisiting the ADR.

## Architecture decided so far

- MSC layering (Model, Service, Controller); LangGraph used directly, Deep Agents rejected.
- Anthropic models as primary provider; Llama in Docker as the no-paid-key path for evaluators; model and endpoint configurable by environment variable.
- Order access (ADR 0001): the Customer identifies by CPF in the conversation and an Order is shown only when that CPF owns it and the order number is given. Identity is injected by the backend, never passed as a tool argument. A missing Order and another Customer's Order collapse into one `NOT_FOUND` before the model sees them.
- Architecture baseline in `docs/architecture.md` (owner-approved 2026-10-28): model per role, masked JSONL tracing, BM25 retrieval, the module map, the eval harness. Business rules live in the policies, tools return facts (ADR 0002).

## Working rules

- A unit of work starts with `/pre-code <objective>`: three macro skills chain the phases (M1 `/pre-code`: interview, plan, owner seat, owner approval; M2 `/deliver <slug>`: ralph, local review, draft PR; M3 `/pr-panel <slug> <PR> <mode>`: PR review in a fresh Herdr tab, fixes, automatic verdict). Each macro resumes a stopped unit when called with its slug. Unit state and the guards the hooks enforce live in `.claude/hooks/flow.sh`; tests in `tests/harness/test_flow.py`.
- Existing tools before custom code: every interview and plan runs a research step on existing libraries, targeted (what the libraries already chosen in `docs/architecture.md` ship) or broad (a capability with no chosen library); the agent picks which and says why. Every piece built from scratch names in the plan the existing tool considered and why it does not fit.
- TDD split: the `test-engineer` writes the failing tests; the `executor` never edits them.
- Nothing that calls the real model runs automatically (no git hook, CI or schedule). Eval rounds are manual, show a cost estimate first and stop at US$5.
- Commit directly on `main` while the work is docs and setup; once code delivery starts, one branch and one `wt` worktree per unit, a PR, and only the owner merges.
- Commits: Conventional Commits, subject only, up to 80 characters.
- Language: `docs/` is Portuguese; agent-facing files (this file, `AGENTS.md`, skills, `GLOSSARY.md`, ADRs), code and tests are English.
