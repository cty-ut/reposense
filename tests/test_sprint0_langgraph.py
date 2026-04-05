"""Sprint 0: Verify LangGraph framework works — 2 nodes, 1 edge, ReAct-style."""

from __future__ import annotations

from typing import TypedDict
from langgraph.graph import StateGraph, START, END


class MiniState(TypedDict):
    query: str
    result: str


def think_node(state: MiniState) -> dict:
    """Simulate the Reason step."""
    return {"result": f"Thinking about: {state['query']}"}


def answer_node(state: MiniState) -> dict:
    """Simulate the Act step."""
    return {"result": f"Answer: I analyzed '{state['query']}' and here is the result."}


def build_mini_graph():
    builder = StateGraph(MiniState)
    builder.add_node("think", think_node)
    builder.add_node("answer", answer_node)
    builder.add_edge(START, "think")
    builder.add_edge("think", "answer")
    builder.add_edge("answer", END)
    return builder.compile()


def test_langgraph_basic_flow():
    graph = build_mini_graph()
    result = graph.invoke({"query": "What is LangGraph?", "result": ""})

    assert result["query"] == "What is LangGraph?"
    assert "analyzed" in result["result"]
    print(f"✅ LangGraph works. Final state: {result}")


if __name__ == "__main__":
    test_langgraph_basic_flow()
