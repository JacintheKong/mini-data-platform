"""Claude agent loop with tool use."""
import copy
from dataclasses import dataclass, field
from typing import Any

from cli_agent import config
from cli_agent.tools import Tools


@dataclass
class AgentResult:
    text: str
    trace: list[Any] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


class AgentSession:
    def __init__(
        self,
        client,
        system_prompt: str,
        tools: Tools,
        model: str = config.MODEL,
        max_iterations: int = config.MAX_ITERATIONS,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.tools = tools
        self.model = model
        self.max_iterations = max_iterations
        self.history: list[dict] = []

    def answer(self, question: str) -> AgentResult:
        messages = self.history + [{"role": "user", "content": question}]
        trace = []
        for _ in range(self.max_iterations):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=Tools.schemas(),
                # Deep-copy so the SDK can't normalize blocks in-place and corrupt history.
                messages=copy.deepcopy(messages),
            )
            trace.append(resp)
            if resp.stop_reason == "end_turn":
                text = _final_text(resp)
                # Append assistant turn to history for follow-ups
                messages.append({"role": "assistant", "content": _serialize_content(resp.content)})
                self.history = messages
                return AgentResult(text=text, trace=trace, messages=messages)
            # Dispatch tool_use blocks
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    output = self.tools.dispatch(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    })
            messages.append({"role": "assistant", "content": _serialize_content(resp.content)})
            messages.append({"role": "user", "content": tool_results})
        return AgentResult(
            text=f"(agent did not converge in {self.max_iterations} turns)",
            trace=trace,
            messages=messages,
        )


def _final_text(resp) -> str:
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no text response)"


def _serialize_content(content) -> list[dict]:
    """Convert Anthropic content blocks into dicts for the next messages.create call."""
    out = []
    for b in content:
        if getattr(b, "type", None) == "text":
            out.append({"type": "text", "text": b.text})
        elif getattr(b, "type", None) == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            })
    return out
