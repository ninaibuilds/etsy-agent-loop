import argparse
import os
import sys
import time
from pathlib import Path

from typing import TypedDict, List

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

load_dotenv()

_data = Path(os.getenv("DATA_DIR", "."))
(_data / "designs").mkdir(parents=True, exist_ok=True)
(_data / "logs").mkdir(parents=True, exist_ok=True)

from agents.scout import scout_node
from agents.analyst import analyst_node
from agents.designer import designer_node
from agents.printify_agent import printify_node
from utils.helpers import get_logger, log_action
from utils.db import log_run


logger = get_logger("main")


class AgentState(TypedDict):
    raw_products: List[dict]
    design_briefs: List[dict]
    generated_designs: List[dict]
    printify_products: List[dict]
    etsy_listings: List[dict]
    loop_count: int
    dry_run: bool
    errors: List[str]


# ── routing ──────────────────────────────────────────────────────────────────

def route_after_analyst(state: AgentState) -> str:
    if state.get("dry_run", False):
        return "end"
    if not state.get("design_briefs"):
        log_action("main", "No design briefs produced; ending run.")
        return "end"
    return "design"


def route_after_loop(state: AgentState) -> str:
    return "continue"


# ── loop controller node ──────────────────────────────────────────────────────

def loop_controller_node(state: AgentState) -> AgentState:
    loop_count = state.get("loop_count", 0) + 1

    products   = state.get("raw_products", [])
    designs    = state.get("generated_designs", [])
    listings   = state.get("etsy_listings", [])
    errors     = state.get("errors", [])

    log_run(loop_count, len(products), len(designs), len(listings))

    interval = int(os.getenv("LOOP_INTERVAL_HOURS", "6"))

    divider = "=" * 60
    print(f"\n{divider}")
    print(f"  LOOP {loop_count} COMPLETE")
    print(divider)
    print(f"  Products scraped    : {len(products)}")
    print(f"  Designs generated   : {len(designs)}")
    print(f"  Listings published  : {len(listings)}")
    print(f"  Errors              : {len(errors)}")
    if listings:
        print("\n  Published listings:")
        for lst in listings:
            print(f"    -> {lst.get('listing_url', 'n/a')}")
    if errors:
        print("\n  Errors encountered:")
        for err in errors:
            print(f"    [!] {err}")
    print(f"\n  Next cycle in {interval} hour(s).  Press Ctrl+C to stop.")
    print(f"{divider}\n")

    log_action("loop_controller", f"Loop {loop_count} complete — sleeping {interval}h.")
    time.sleep(interval * 3600)

    return {
        **state,
        "loop_count": loop_count,
        "raw_products": [],
        "design_briefs": [],
        "generated_designs": [],
        "printify_products": [],
        "etsy_listings": [],
        "errors": [],
    }


# ── graph definition ──────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("scout",           scout_node)
workflow.add_node("analyst",         analyst_node)
workflow.add_node("designer",        designer_node)
workflow.add_node("printify",        printify_node)
workflow.add_node("loop_controller", loop_controller_node)

workflow.set_entry_point("scout")

workflow.add_edge("scout", "analyst")

workflow.add_conditional_edges(
    "analyst",
    route_after_analyst,
    {"design": "designer", "end": END},
)

workflow.add_edge("designer", "printify")
workflow.add_edge("printify", "loop_controller")

workflow.add_conditional_edges(
    "loop_controller",
    route_after_loop,
    {"continue": "scout", "end": END},
)

graph = workflow.compile()


# ── entry point ───────────────────────────────────────────────────────────────

def print_mermaid() -> None:
    try:
        diagram = graph.get_graph().draw_mermaid()
        print("\n--- LangGraph Mermaid Diagram ---")
        print(diagram)
        print("---------------------------------\n")
    except Exception as exc:
        logger.warning(f"Could not render Mermaid diagram: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Etsy AI Agent Loop — autonomous product research, design, and listing."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scout + analyst only; skip DALL-E, Printify, and Etsy API calls.",
    )
    parser.add_argument(
        "--no-mermaid",
        action="store_true",
        help="Suppress the LangGraph Mermaid diagram on startup.",
    )
    args = parser.parse_args()

    if not args.no_mermaid:
        print_mermaid()

    if args.dry_run:
        print("\n[DRY RUN] Scout + analyst only — no DALL-E / Printify / Etsy calls.\n")

    initial_state: AgentState = {
        "raw_products":     [],
        "design_briefs":    [],
        "generated_designs": [],
        "printify_products": [],
        "etsy_listings":    [],
        "loop_count":       0,
        "dry_run":          args.dry_run,
        "errors":           [],
    }

    log_action("main", f"Starting Etsy Agent Loop (dry_run={args.dry_run})")

    try:
        graph.invoke(initial_state)
    except KeyboardInterrupt:
        print("\n\nStopped by user. Goodbye.")
        log_action("main", "Agent loop stopped by user (KeyboardInterrupt).")
        sys.exit(0)
