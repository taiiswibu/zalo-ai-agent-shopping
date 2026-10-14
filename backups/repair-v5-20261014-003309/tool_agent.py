#!/usr/bin/env python3
"""Single-file, safe patch for Zalo AI Agent v5 LangGraph ToolState NameError.

Usage:
    python fix_v5_toolstate.py --project "D:\\Code\\project_AI\\zalo-ai-agent-free"

This script updates ONLY app/advanced/tool_agent.py, and first saves a backup
under backups/v5-toolstate-fix-*/tool_agent.py. It does not touch credentials,
SQLite, Zalo, dependencies or any existing tests.
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

OLD_HEADER = '''from app.commerce import AgentResult, money\n\nlog = logging.getLogger(__name__)'''
NEW_HEADER = '''from app.commerce import AgentResult, money

# The reducer and TypedDict must be in the module namespace: LangGraph uses
# typing.get_type_hints(ToolState), which cannot resolve function-local names
# in postponed annotations (Python 3.12+).
# Optional dependency: v4 fallback still imports if langgraph is unavailable.
try:
    from langgraph.graph.message import add_messages
except ImportError:
    add_messages = None


class ToolState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    transcript: list[dict]
    selected_ids: list[str]
    selected_names: list[str]
    rounds: int
    payloads: list[dict]
    selected_count: int


log = logging.getLogger(__name__)'''

OLD_BODY = '''    from langgraph.graph.message import add_messages
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    class ToolState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        transcript: list[dict]
        selected_ids: list[str]
        selected_names: list[str]
        rounds: int
        payloads: list[dict]
        selected_count: int

    planner = GeminiPlanner()'''
NEW_BODY = '''    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    planner = GeminiPlanner()'''


def run(project: Path) -> int:
    target = project.resolve() / "app" / "advanced" / "tool_agent.py"
    if not target.is_file():
        print(f"ERROR: Missing file: {target}", file=sys.stderr)
        return 2
    original = target.read_text(encoding="utf-8-sig")
    if (
        "class ToolState(TypedDict, total=False):" in original
        and original.index("class ToolState(TypedDict, total=False):")
        < original.index("def execute_tool_agent(")
        and "from langgraph.graph.message import add_messages" in original.split("def execute_tool_agent(")[0]
    ):
        print("ALREADY FIXED: ToolState is at module scope. No changes made.")
        return 0
    if original.count(OLD_HEADER) != 1 or original.count(OLD_BODY) != 1:
        print("ERROR: tool_agent.py differs from the expected v5 release; no files changed.", file=sys.stderr)
        print("Send app/advanced/tool_agent.py for a tailored patch.", file=sys.stderr)
        return 3
    updated = original.replace(OLD_HEADER, NEW_HEADER, 1).replace(OLD_BODY, NEW_BODY, 1)
    ast.parse(updated, filename=str(target))
    backups = project.resolve() / "backups" / ("v5-toolstate-fix-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backups.mkdir(parents=True, exist_ok=False)
    shutil.copy2(target, backups / "tool_agent.py")
    staging = target.with_name(".tool_agent.py.tmp")
    try:
        staging.write_text(updated, encoding="utf-8", newline="\n")
        os.replace(staging, target)
    finally:
        if staging.exists():
            staging.unlink()
    print("FIXED:", target)
    print("BACKUP:", backups / "tool_agent.py")
    print("NEXT: python -m pytest -q tests/test_agent_tool_v5.py::test_real_langgraph_toolnode_offline")
    print("THEN: python -m pytest -q tests")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix LangGraph add_messages NameError in v5")
    parser.add_argument("--project", required=True, type=Path, help="Existing zalo-ai-agent-free directory")
    args = parser.parse_args()
    raise SystemExit(run(args.project))
