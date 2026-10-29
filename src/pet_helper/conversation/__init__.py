from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from pet_helper.gateway import ModelGateway


@dataclass(frozen=True)
class Reply:
    text: str
    sources: tuple[str, ...] = ()
    escalation: None = None


def build_graph(gateway: ModelGateway):
    def agent(state: MessagesState) -> dict:
        return {"messages": [gateway.complete(state["messages"]).message]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile()


class Assistant:
    def __init__(self, gateway: ModelGateway):
        self._graph = build_graph(gateway)

    def turn(self, session_id: str, text: str) -> Reply:
        # session_id has no reader yet: there is no checkpointer, so no history
        state = self._graph.invoke({"messages": [HumanMessage(text)]})
        return Reply(text=str(state["messages"][-1].text))
