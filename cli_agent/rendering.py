"""Format AgentResult for terminal output."""
from cli_agent.agent import AgentResult


def render(result: AgentResult, verbose: bool = False) -> str:
    lines = [result.text, ""]

    sql_calls = _extract_sql_calls(result) if verbose else []
    if sql_calls:
        lines.append("---")
        for i, (query, output) in enumerate(sql_calls, 1):
            label = f"SQL" if len(sql_calls) == 1 else f"SQL #{i}"
            lines.append(f"\n{label}:")
            lines.append(_indent(query.strip(), "  "))
            lines.append("\nResult:")
            lines.append(_indent(output.strip(), "  "))

    if verbose:
        lines.append("\n---\nFull trace:")
        for i, resp in enumerate(result.trace, 1):
            lines.append(f"\n[turn {i}] stop_reason={resp.stop_reason}")
            for block in resp.content:
                btype = getattr(block, "type", "?")
                if btype == "text":
                    lines.append(f"  text: {block.text[:200]}")
                elif btype == "tool_use":
                    lines.append(f"  tool_use: {block.name}({block.input})")

    return "\n".join(lines)


def _extract_sql_calls(result: AgentResult) -> list[tuple[str, str]]:
    """Walk the message history, pair run_sql tool_use with its tool_result."""
    pairs = []
    pending: dict[str, str] = {}
    for msg in result.messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "tool_use" and block.get("name") == "run_sql":
                pending[block["id"]] = block["input"].get("query", "")
            elif btype == "tool_result":
                tid = block.get("tool_use_id")
                if tid in pending:
                    pairs.append((pending.pop(tid), str(block.get("content", ""))))
    return pairs


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())
