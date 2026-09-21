"""The interface between the discovery loop and a language model. No SDK is imported here."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    content: list[dict]  # blocks exactly as returned: thinking, text, tool_use
    stop_reason: str
    input_tokens: int
    output_tokens: int
    request_id: str | None = None


class LLM(Protocol):
    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
