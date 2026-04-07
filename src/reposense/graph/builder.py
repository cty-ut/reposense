"""Build the LangGraph workflow for RepoSense."""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from reposense.graph.state import RepoSenseState
from reposense.graph.nodes.router import router_node
from reposense.graph.nodes.scanner import scanner_node, file_reader_node, deep_reader_node
from reposense.graph.nodes.analyzer import (
    level1_analyzer_node,
    level2_analyzer_node,
    level3_analyzer_node,
)
from reposense.graph.nodes.reporter import reporter_node


def _route_after_router(state: RepoSenseState) -> str:
    if state.get("error"):
        return END
    intent = state.get("intent", "scan")
    if intent == "scan":
        return "scanner"
    if intent == "ask":
        return "scanner"  # need repo data before Q&A
    return "scanner"


def build_scan_graph() -> StateGraph:
    """Build and compile the full panorama-scan graph.

    Flow:
        START → router → scanner → file_reader → level1_analyzer
              → level2_analyzer → deep_reader → level3_analyzer → reporter → END
    """
    builder = StateGraph(RepoSenseState)

    builder.add_node("router", router_node)
    builder.add_node("scanner", scanner_node)
    builder.add_node("file_reader", file_reader_node)
    builder.add_node("level1_analyzer", level1_analyzer_node)
    builder.add_node("level2_analyzer", level2_analyzer_node)
    builder.add_node("deep_reader", deep_reader_node)
    builder.add_node("level3_analyzer", level3_analyzer_node)
    builder.add_node("reporter", reporter_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", _route_after_router, {"scanner": "scanner", END: END})
    builder.add_edge("scanner", "file_reader")
    builder.add_edge("file_reader", "level1_analyzer")
    builder.add_edge("level1_analyzer", "level2_analyzer")
    builder.add_edge("level2_analyzer", "deep_reader")
    builder.add_edge("deep_reader", "level3_analyzer")
    builder.add_edge("level3_analyzer", "reporter")
    builder.add_edge("reporter", END)

    return builder.compile()
