# Pet Helper

Customer-service assistant for a fictional pet e-commerce: answers policy questions, looks up orders and recommends catalog products for the customer in the conversation.

## Language

### People

**Customer** _(cliente)_:
A person who buys from the store and owns Orders; identified by a CPF.
_Avoid_: user, client, account

**Session Customer** _(cliente da sessão)_:
The Customer the current conversation is bound to, once they have identified themselves by CPF; a conversation has no Session Customer until then, and a second CPF in the same conversation is refused rather than switching it.
_Avoid_: logged user, current user

### Orders

**Order** _(pedido)_:
A purchase identified by an order number and owned by exactly one Customer; only its owner, as Session Customer, may see it.
_Avoid_: purchase, transaction

**Order Status** _(status do pedido)_:
The single stage an Order is in: Paid _(Pago)_, Picking _(Em separação)_, Shipped _(Enviado)_, Delivered _(Entregue)_ or Cancelled _(Cancelado)_; no other status exists.
_Avoid_: state, situation, phase

**Delivery Date** _(data de entrega)_:
The day a Delivered Order reached the Customer; the return windows count from it.
_Avoid_: shipping date, arrival

**Return** _(troca e devolução)_:
A Customer sending a delivered Product back, allowed only within the window of its Return Reason, counted from the Delivery Date; opened food or litter is accepted only for Defect.
_Avoid_: exchange, devolution, troca, devolução (as separate concepts)

**Return Reason** _(motivo da troca e devolução)_:
Why a Product is sent back, exactly one of: Regret _(arrependimento)_, within 7 calendar days, or Defect _(defeito)_, within 30 days.
_Avoid_: return type, cause

**Refund** _(reembolso)_:
Money given back to the Customer, always through the original payment method.
_Avoid_: reimbursement, chargeback, cashback

**Payment Method** _(forma de pagamento)_:
How an Order was paid, exactly one of: Pix _(Pix)_ or Card _(Cartão)_; it decides the Refund deadline.
_Avoid_: payment type, gateway

**Region** _(região)_:
The Brazilian region of an Order's delivery address: South _(Sul)_, Southeast _(Sudeste)_, Center-West _(Centro-Oeste)_, Northeast _(Nordeste)_ or North _(Norte)_.
_Avoid_: area, zone, state

**Free Shipping** _(frete grátis)_:
No shipping charge, granted when the purchase total is strictly above the Region's threshold: R$ 149 in South and Southeast, R$ 249 elsewhere.
_Avoid_: free delivery, shipping discount

### Catalog

**Product** _(produto)_:
An item the store sells, identified by exactly one SKU.
_Avoid_: item, goods, merchandise

**SKU**:
The code that identifies one Product; only SKUs that exist in the catalog may be mentioned.
_Avoid_: product id, code

**Category** _(categoria)_:
The kind of a Product, exactly one of: Food _(ração)_, Litter _(areia)_, Toy _(brinquedo)_ or Accessory _(acessório)_; opened Food or Litter is accepted for return only for Defect.
_Avoid_: type, department, product line

**Species** _(espécie)_:
The animal a Product is meant for: Dog _(cão)_, Cat _(gato)_ or Both _(ambos)_; a Both Product fits either.
_Avoid_: pet type, animal

**Out of Stock** _(sem estoque)_:
A Product with zero stock; it may be named when the Customer asks about it, but is never recommended.
_Avoid_: unavailable, sold out

### Knowledge

**Policy** _(política)_:
One of the store's documents covering a single topic, holding the topic's rules and its frequently asked questions.
_Avoid_: FAQ, article, doc

**Source** _(fonte)_:
The Policy an answer is based on and cites.
_Avoid_: reference, citation

**Uncovered Topic** _(tema sem cobertura)_:
A topic no Policy covers; a question about it is answered with "I don't know", never with an invented rule.
_Avoid_: out-of-scope question, unknown

### Escalation

**Escalation** _(escalonamento)_:
Handing the conversation over to a Human Agent, after which the assistant stops answering on that matter; it happens once per conversation and records one Escalation Reason.
_Avoid_: handoff, transfer, transbordo

**Escalation Reason** _(motivo do escalonamento)_:
Why a conversation was escalated, exactly one of, by priority: Animal Health Question, Strong Negative Sentiment, Repeated Failure, Explicit Request; when several apply, the highest-priority one is recorded.
_Avoid_: escalation type, trigger

**Animal Health Question** _(pergunta de saúde animal)_:
A question about a pet's health, symptoms or treatment; the assistant never diagnoses and always escalates it.
_Avoid_: vet question, medical question

**Explicit Request** _(pedido explícito por humano)_:
The Customer asks to talk to a person.
_Avoid_: human request, manual escalation

**Human Agent** _(atendente humano)_:
A store employee who takes over an escalated conversation.
_Avoid_: operator, support, human

**Strong Negative Sentiment** _(sentimento negativo forte)_:
The Customer insults the store or its staff, or shows intense frustration; an ordinary complaint, such as a delay within the deadline or a price grievance, is not one.
_Avoid_: serious complaint, angry customer, reclamação grave

**Repeated Failure** _(falha repetida)_:
The same conversation step (order lookup, Customer identification, order number) fails three times in a row, or the assistant hits any failure it cannot recover from; saying "I don't know" about an Uncovered Topic is not a failure.
_Avoid_: error, crash, retry limit
