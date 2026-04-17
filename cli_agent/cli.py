"""CLI entry: single-shot or minimal REPL."""
import argparse
import sys

import anthropic
import duckdb

from cli_agent import config
from cli_agent.agent import AgentSession
from cli_agent.prompts import bootstrap, build_system_prompt
from cli_agent.rendering import render
from cli_agent.tools import Tools


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cli_agent",
        description="Ask ad-hoc questions about the mini-data-platform warehouse.",
    )
    parser.add_argument("question", nargs="?", help="One-shot question. Omit to enter REPL.")
    parser.add_argument("--verbose", action="store_true", help="Show full tool-call trace.")
    args = parser.parse_args()

    api_key = config.require_api_key()
    if not config.WAREHOUSE_PATH.exists():
        print(f"Warehouse not found at {config.WAREHOUSE_PATH}", file=sys.stderr)
        return 2

    conn = duckdb.connect(str(config.WAREHOUSE_PATH), read_only=True)
    tools = Tools(conn, manifest_path=config.DBT_MANIFEST_PATH)
    system_prompt = build_system_prompt(bootstrap(conn))
    client = anthropic.Anthropic(api_key=api_key)
    session = AgentSession(
        client=client, system_prompt=system_prompt, tools=tools, model=config.MODEL
    )

    if args.question:
        result = session.answer(args.question)
        print(render(result, verbose=args.verbose))
        return 0

    # REPL
    print(f"cli_agent — ask questions about the warehouse. Ctrl-D to exit. Model: {config.MODEL}")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        result = session.answer(question)
        print(render(result, verbose=args.verbose))
        print()
