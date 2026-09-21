"""The real language-model client. The only module that imports the Anthropic SDK."""

from typing import Any

import anthropic

from cua.llm import LLMError, LLMResponse

MODEL = "claude-sonnet-5"


class AnthropicLLM:
    def __init__(
        self,
        model: str = MODEL,
        client: Any = None,
        max_tokens: int = 16_000,
        effort: str | None = None,
    ):
        try:
            self._client = client or anthropic.Anthropic(timeout=120.0)
        except anthropic.AnthropicError as e:  # for example, no API key
            raise LLMError(f"cannot create the Anthropic client: {e}") from e
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort

    def check_credentials(self) -> None:
        """Fail before a browser opens if the SDK has no way to authenticate.

        The SDK builds a client without credentials and only complains at request time, so a
        missing key would otherwise surface halfway through a run.
        """
        validate = getattr(self._client, "_validate_headers", None)
        if validate is None:
            return
        try:
            validate({}, {})
        except TypeError as e:
            raise LLMError(f"no Anthropic credentials: {e}") from e

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
            # One action per turn: the loop handles a single tool call.
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            # Adaptive thinking is the model's own mode; the summary lands in the transcript.
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AnthropicError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        return LLMResponse(
            # Blocks go back unchanged on the next turn, thinking blocks included.
            content=[block.model_dump(exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason or "",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            request_id=getattr(response, "_request_id", None),
        )
