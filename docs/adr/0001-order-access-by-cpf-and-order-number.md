# Order access requires the CPF and the order number together

There is no authentication, so a Customer identifies themselves by CPF inside the conversation. A CPF alone would let anyone who knows someone else's CPF read all of that person's Orders, which is exactly the "other customer's data" leak the challenge tests. So an Order is shown only when the CPF given in the conversation belongs to that Order's owner, and the Customer always names the order number: there is no "list my orders" by CPF.

## Considered Options

- **CPF alone**, risk documented as a limitation: rejected, it exposes every Order of whoever's CPF is known.
- **CPF plus a second registration field** (e.g. e-mail): rejected, it adds data to the kit and a step to every conversation for little extra protection.
- **CPF plus order number** (chosen): fits the challenge's "lookup by order number" and narrows a leak to one Order whose number is also known.

## Consequences

- Questions like "where are my orders?" are answered by asking for the order number, never by listing.
- The CPF is masked in logs and traces like any other personal data.
- A missing Order and another Customer's Order get the same message to the Customer, so probing order numbers reveals nothing; only the trace records which of the two happened. The order API still answers 404 and refusal distinctly, but the order tool collapses both into one NOT_FOUND result before the model sees it, so the model cannot leak the difference.
