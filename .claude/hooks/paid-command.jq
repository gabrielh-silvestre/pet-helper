# True when any string of a shell or exec tool call runs a command that spends money on the real model:
# `pet-helper eval`, `pet-helper optimize`, `-m pet_helper.optimize`, `pet_helper.evals.judge smoke` or
# `pet_helper.evals.labels ... judge-pool`.
# shortcut: matched by text, not parsed; the word sequence inside a commit message or an echo is blocked
# too, and a command assembled at run time gets through. The only real fence is no API key in the agent env.
[.tool_input | .. | strings]
| any(
    test("pet-helper\\s+(eval|optimize)(\\s|$|['\"])")
    or test("-m\\s+pet_helper\\.optimize(\\s|$|['\"])")
    or test("pet_helper\\.evals\\.judge\\s+smoke")
    or test("pet_helper\\.evals\\.labels\\s+(\\S+\\s+)*judge-pool")
  )
