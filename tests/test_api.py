"""The thin FastAPI shell over tool_interface.py — no HTTP-specific logic to test beyond "does
this route call the right pure function and translate its result/errors correctly." The real
replay path (invoke_capability()'s happy path) is exercised live, never here.
"""

from fastapi.testclient import TestClient

import cua.api as api_module
from cua.api import app
from cua.tool_interface import CapabilityNotFound

client = TestClient(app)


def test_get_capabilities_returns_the_real_catalog():
    response = client.get("/capabilities")
    assert response.status_code == 200
    names = [tool["name"] for tool in response.json()]
    assert "transfer_funds" in names


def test_get_capabilities_shape_matches_to_tool_schema():
    response = client.get("/capabilities")
    tool = next(t for t in response.json() if t["name"] == "transfer_funds")
    assert set(tool) == {"name", "description", "input_schema"}
    assert tool["input_schema"]["type"] == "object"
    assert set(tool["input_schema"]["required"]) == {"from_account", "to_account", "amount"}


def test_post_invoke_404s_for_an_unknown_capability_name():
    response = client.post("/capabilities/nonexistent/invoke", json={})
    assert response.status_code == 404


def test_post_invoke_passes_args_through_and_returns_the_result(monkeypatch):
    captured = {}

    def fake_invoke(name, args, fault=None):
        captured["name"] = name
        captured["args"] = args
        return {"status": "SUCCESS", "outputs": {"confirmation_text": "ok"}}

    monkeypatch.setattr(api_module, "invoke_capability", fake_invoke)

    response = client.post(
        "/capabilities/transfer_funds/invoke",
        json={"from_account": "A", "to_account": "B", "amount": "5"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "SUCCESS", "outputs": {"confirmation_text": "ok"}}
    assert captured == {"name": "transfer_funds", "args": {"from_account": "A", "to_account": "B", "amount": "5"}}


def test_post_invoke_still_404s_via_the_patched_function(monkeypatch):
    def fake_invoke(name, args, fault=None):
        raise CapabilityNotFound(name)

    monkeypatch.setattr(api_module, "invoke_capability", fake_invoke)
    response = client.post("/capabilities/anything/invoke", json={})
    assert response.status_code == 404
