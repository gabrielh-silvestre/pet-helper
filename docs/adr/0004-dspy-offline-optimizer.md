# DSPy optimizes prompts offline; the result enters the runtime only as committed text

The unit `market-tools-adoption` adopts DSPy to improve two prompts: the judge's few-shot examples and the assistant's system prompt. DSPy lives only in the module `pet_helper.optimize`, installed by the extra `optimize` (`dspy>=3.4`), and runs only on the owner's command. What it produces is plain text that is committed to the repository (`CRITERION_PROMPTS` and `FEW_SHOT_EXAMPLES` in `evals/judge.py`, `SYSTEM_PROMPT` in `conversation/prompt.py`), so `judge_prompt_sha256`, `system_prompt_sha256` and the judge validation by model plus frozen hash work exactly as before. No DSPy program runs at conversation time.

## Considered Options

- **Offline module `optimize`, result as committed text** (chosen): a candidate is a fixed text with a hash, which both acceptance rules need (judge TPR/TNR read once with a frozen prompt; assistant through the accept/reject step with the held-out cases).
- **DSPy module inside a LangGraph node**: rejected, and out of the spec. A DSPy node shares neither tools nor state with the graph and brings a second model client (litellm) with its own cost, trace and fallback.
- **Judge as a live `dspy.Predict`**: rejected. It breaks the validation by frozen hash, because a live program has no stable identity.
- **`optimize` as a subpart of `evals`**: rejected. It would put an optional-import island inside a base module. The module depends on `evals` and `conversation`, and nothing depends on it.
- **MIPROv2, or GEPA on the judge**: rejected. The judge has too few labelled items (8, 2 and 0 in dev) for them.
- **No optimizer, hand-tuning only** (status quo): kept as the outcome whenever a candidate is rejected.

## Decision

- **Judge** (`pet-helper optimize judge <round_dir>`): BootstrapFewShot on `escalation_judgment` (`max_bootstrapped_demos=2`, `max_labeled_demos=2`, demonstrations chosen among the dev items). `dont_know_accuracy` has 2 dev items and no search: they become the examples. `tone` has no dev item, so its prompt stays. The command needs the 18 human labels of the pool (step P0) and refuses without them. It writes the candidate to `judge.py`, lets the candidate read the test split once (`--force-new-validation`) and compares per criterion.
  - Acceptance: the candidate is kept only if its test TPR and TNR are each equal or better than those of the current judge. A tie accepts. Any drop rejects. A metric with no measure on either side (0/0) is left out of the comparison and never counts as a tie. A criterion with no measured metric keeps the current prompt. On rejection the bytes of `judge.py` are restored.
  - The owner waived the Wilson floor of `loop-melhoria.md` for this unit: the report states TPR, TNR and Wilson intervals as information only. Because `labels judge-pool` still requires the file, the run uses a `labels/floor.json` with floor 0 for every criterion.
- **Assistant** (`pet-helper optimize assistant <targets.json>`): GEPA over the 20 visible dev cases, split 10 for training and 10 for validation, deterministic and stratified so the cases below 3/3 in the baseline are shared evenly. `targets.json` maps `case_id` to `prompt` or `checker`, as the owner classified them; only `prompt` cases are targets, and without any target nothing runs. The task model is the assistant's (Haiku), the reflection runs on Sonnet, `max_metric_calls` is 300, and the command stops before US$5. The metric is 0/1 per case and the feedback lists only the deterministic `failed_checks`, never customer text and never the judge. The candidate goes to `.ignore/assistant-prompt-candidate.txt` and `prompt.py` is not touched.
  - `pet-helper eval --system-prompt-file <path>` measures the candidate. It replaces the current prompt only after the accept/reject step of `loop-melhoria.md`, held-out included, without dropping any case that is green today (3/3 in the baseline round P1a).
- **Guards**: held-out cases are never loaded (the loader refuses the path, and the assistant command fails when `PET_HELPER_HELD_OUT=1`). `pet-helper optimize` is covered by the paid-command hook, so an agent cannot run it. Nothing runs from a git hook, CI or a schedule. The whole unit has a US$30 cap; each paid command shows its estimate, asks for confirmation and stops before US$5.
- `build_assistant(system_prompt: str | None)` is the only seam in the runtime. Without the argument the prompt is byte for byte the current one.

## Consequences

- `dspy` 3.4.1 brings 16 dependencies. The extra keeps them out of the install of whoever only evaluates the project. CI installs with `--all-extras` so the optimizer tests run; `DummyLM` lets them run without a model.
- `pet-helper optimize` without the extra prints `uv sync --extra optimize` and exits with an error; `dspy` is imported lazily, only inside `pet_helper.optimize`.
- The judge result has no statistical power. The test split has 4, 2 and 2 items; a Wilson interval with n = 4 has a half-width above 0.3, so "equal or better" can accept noise. Whatever the verdict, it comes from the rule and not from evidence, and this ADR records it that way.
- Dev items used as demonstrations leave the dev calculation, so the dev metric of the candidate is computed on fewer items.
- The assistant optimization uses the judge-free check results only. Tone, escalation judgment and the "don't know" precision of a candidate are not measured by the accept/reject round (it runs with `--judge none`); they stay pending until a validated judge reads the saved transcripts with `--rejudge`, which only runs on the owner's order within the US$30.
- The judge optimization and the assistant optimization are independent: either can be rejected without affecting the other.

## Results

Not recorded yet. The steps that produce them are manual and paid, and belong to the owner:

| Target | Step | Verdict | Spend | Numbers |
|---|---|---|---|---|
| Judge | P2 (`labels judge-pool`, `optimize judge`, test read once) | pending | pending | pending |
| Assistant | P3 (`optimize assistant`) and P4 (accept/reject) | pending | pending | pending |

When P2 to P4 run, this section gets, per target, the verdict (kept or rejected), the cost spent against the cap, and the TPR/TNR or k-of-n numbers with their counts. If P3 has no prompt target, the current prompt stays and the assistant row records that.
