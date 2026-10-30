from langgraph.graph.state import CompiledStateGraph

from pet_helper.conversation import Assistant


def graph_of(assistant: Assistant) -> CompiledStateGraph:
    # the only place tests reach into the private graph: node order and recursion limit have no public seam
    return assistant._graph
