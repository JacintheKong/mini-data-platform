"""Smoke test: agent tool-use round-trip with mocked Anthropic client."""
from unittest.mock import MagicMock

from cli_agent.agent import AgentSession
from cli_agent.tools import Tools


class FakeBlock:
    """Minimal shape matching Anthropic SDK content blocks."""
    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_response(stop_reason, content):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    return resp


def test_agent_dispatches_tool_use_and_returns_final_text(duckdb_conn):
    client = MagicMock()
    # Turn 1: agent requests list_tables
    turn1 = make_response(
        stop_reason="tool_use",
        content=[FakeBlock(type="tool_use", id="t1", name="list_tables", input={})],
    )
    # Turn 2: agent emits final text
    turn2 = make_response(
        stop_reason="end_turn",
        content=[FakeBlock(type="text", text="There are 3 tables in marts.")],
    )
    client.messages.create.side_effect = [turn1, turn2]

    session = AgentSession(
        client=client,
        system_prompt="test prompt",
        tools=Tools(duckdb_conn),
        model="claude-sonnet-4-5",
    )
    result = session.answer("what tables exist?")
    assert "3 tables" in result.text
    assert len(result.trace) == 2
    # Second call should include the tool_result
    second_call = client.messages.create.call_args_list[1].kwargs
    last_msg = second_call["messages"][-1]
    assert last_msg["role"] == "user"
    assert any(
        block.get("type") == "tool_result" for block in last_msg["content"]
    )


def test_agent_returns_text_when_no_tool_use(duckdb_conn):
    """Regression: model may answer in one turn with text-only and a non-end_turn
    stop_reason. The loop must treat 'no tool_use blocks' as completion, otherwise
    it appends an empty tool_results array which the API rejects."""
    client = MagicMock()
    # Single response: text only, stop_reason that isn't end_turn (e.g., max_tokens
    # or any model quirk). Loop must still terminate cleanly.
    only_text = make_response(
        stop_reason="max_tokens",
        content=[FakeBlock(type="text", text="The warehouse has three schemas.")],
    )
    client.messages.create.side_effect = [only_text]

    session = AgentSession(
        client=client,
        system_prompt="test",
        tools=Tools(duckdb_conn),
        model="claude-sonnet-4-5",
    )
    result = session.answer("what schemas exist?")
    assert "three schemas" in result.text
    assert client.messages.create.call_count == 1


def test_agent_stops_after_max_iterations(duckdb_conn):
    client = MagicMock()
    # Always return tool_use — never ends
    loop_resp = make_response(
        stop_reason="tool_use",
        content=[FakeBlock(type="tool_use", id="t", name="list_tables", input={})],
    )
    client.messages.create.return_value = loop_resp

    session = AgentSession(
        client=client,
        system_prompt="test",
        tools=Tools(duckdb_conn),
        model="claude-sonnet-4-5",
        max_iterations=3,
    )
    result = session.answer("loop forever")
    assert "did not converge" in result.text.lower()
    assert len(result.trace) == 3
