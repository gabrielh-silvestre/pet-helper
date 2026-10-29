# Business rules live in the policies; tools return computed facts, not verdicts

The live session hands us a new business rule and its data and expects it in the assistant, with tests, within about 30 minutes. If RN01-RN04 were coded as eligibility checks, every new rule would be new code under time pressure. So the rules (RN01-RN04 and the kit's invented ones) live only in the Policy documents, which the assistant retrieves and cites, and the tools return facts already computed in code: Order Status, Delivery Date and days since delivery (from an injectable clock), Region, total, payment method, and per-item category and whether the packaging was opened. No tool answers "eligible" or "not eligible"; the model applies the cited rule, and the eval asserts the computable outcome of RN01-RN04.

## Considered Options

- **Rules coded as eligibility tools**: rejected as the default; most reliable on a weak model, but every new rule needs code and tests during the live change.
- **Model computes everything from raw order data**: rejected; date arithmetic is a predictable failure of a small model and is cheap to compute in code.
- **Rules in Policies, facts from tools** (chosen): a new rule is a document plus eval cases; arithmetic stays deterministic.

## Consequences

- A new rule with new data needs a policy file and, only when the data is not already exposed, one new read-only tool registered in the conversation module.
- The injectable clock (`PET_HELPER_TODAY`) is part of the contract: eval cases pin the date so RN01 answers stay computable on any day, and the kit's delivery dates are chosen relative to the presentation date.
- If an eval round shows a rule failing repeatedly on the weak model, that single rule may move into a tool; that change revisits this ADR for that rule only.
