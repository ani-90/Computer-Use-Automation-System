"""The Anthropic client wrapper, checked with a fake client. No network."""

import re
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from cua.anthropic_llm import MODEL, AnthropicLLM
from cua.llm import LLMError

SRC = Path(__file__).resolve().parent.parent / "src" / "cua"


class Block:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, exclude_none: bool = False) -> dict:
        return dict(self._data)


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self._response, self._error = response, error

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def reply(blocks: list[dict], stop: str = "tool_use") -> SimpleNamespace:
    response = SimpleNamespace(
        content=[Block(b) for b in blocks],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )
    response._request_id = "req_1"
    return response


def test_the_request_has_the_expected_shape():
    client = FakeClient(reply([{"type": "text", "text": "hi"}]))
    AnthropicLLM(client=client).complete(
        system="S", messages=[{"role": "user", "content": []}], tools=[{"name": "t"}]
    )
    kw = client.calls[0]
    assert kw["model"] == MODEL == "claude-sonnet-5"
    assert kw["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert (kw["system"], kw["tools"], kw["max_tokens"]) == ("S", [{"name": "t"}], 16_000)
    assert not {"temperature", "top_p", "top_k", "output_config"} & set(kw)


def test_effort_is_sent_only_when_set():
    client = FakeClient(reply([]))
    AnthropicLLM(client=client, effort="medium").complete(system="S", messages=[], tools=[])
    assert client.calls[0]["output_config"] == {"effort": "medium"}


def test_the_response_is_converted_block_for_block():
    blocks = [
        {"type": "thinking", "thinking": "plan", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "click", "input": {"ref": 3}},
    ]
    llm = AnthropicLLM(client=FakeClient(reply(blocks)))
    out = llm.complete(system="S", messages=[], tools=[])
    assert out.content == blocks  # thinking blocks survive unchanged for the next turn
    assert (out.stop_reason, out.input_tokens, out.output_tokens) == ("tool_use", 11, 7)
    assert out.request_id == "req_1"


def test_sdk_errors_become_llm_errors():
    llm = AnthropicLLM(client=FakeClient(error=anthropic.AnthropicError("boom")))
    with pytest.raises(LLMError, match="AnthropicError: boom"):
        llm.complete(system="S", messages=[], tools=[])


def _client_with(auth_headers: dict) -> FakeClient:
    def validate(headers, _custom):  # like the SDK: it looks at the headers it is given
        if "X-Api-Key" not in headers:
            raise TypeError("Could not resolve authentication method")

    client = FakeClient(reply([]))
    client._validate_headers = validate
    client.auth_headers = auth_headers
    return client


def test_missing_credentials_are_caught_before_any_request():
    client = _client_with({})
    with pytest.raises(LLMError, match="no Anthropic credentials"):
        AnthropicLLM(client=client).check_credentials()
    assert client.calls == []


def test_a_client_that_has_a_key_passes_the_check():
    AnthropicLLM(client=_client_with({"X-Api-Key": "k"})).check_credentials()


def test_the_real_sdk_client_with_a_key_passes_the_check():
    # Regression: the check once passed empty headers and rejected every client, key or not.
    AnthropicLLM(client=anthropic.Anthropic(api_key="not-a-real-key")).check_credentials()


def test_clients_without_the_check_are_skipped():
    AnthropicLLM(client=FakeClient(reply([]))).check_credentials()  # nothing to validate


def test_a_client_that_cannot_be_built_becomes_an_llm_error(monkeypatch):
    def refuse(**_kwargs):
        raise anthropic.AnthropicError("no api key")

    monkeypatch.setattr("cua.anthropic_llm.anthropic.Anthropic", refuse)
    with pytest.raises(LLMError, match="no api key"):
        AnthropicLLM()


def test_only_the_llm_module_imports_the_sdk_and_only_the_adapter_imports_playwright():
    sdk, browser = [], []
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import|from)\s+anthropic\b", text, re.MULTILINE):
            sdk.append(path.name)
        if re.search(r"^\s*(import|from)\s+playwright\b", text, re.MULTILINE):
            browser.append(path.name)
    assert sdk == ["anthropic_llm.py"]
    assert browser == ["adapter.py"]
