"""Thin HTTP shell over tool_interface.py — the network boundary an outside agent platform's
tool-execution layer would actually cross in production. No logic lives here: this file only
translates HTTP <-> the pure functions in tool_interface.py, which stay free of any HTTP or LLM
import. The synchronous shape here is the demo shape; a real production invocation would likely
be async (invoke -> job id -> webhook/poll), since an escalation's resolution time is unbounded —
not built here, noted as a real design difference rather than pretended away.
"""

import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from cua.tool_interface import (
    CapabilityNotFound,
    fault_from_env,
    invoke_capability,
    list_capabilities,
    to_tool_schema,
)

load_dotenv()  # the service's own entry point — nothing else in this process ever loads .env

# Operator-only, read once at startup from the environment (never from a request): lets a live
# HTTP test reproduce a real fault. Unset — the normal case — means no fault at all.
_FAULT = fault_from_env(os.environ)
if _FAULT is not None:
    print(f"*** FAULT INJECTION ACTIVE on every invocation: {_FAULT.model_dump_json()} ***", file=sys.stderr)

app = FastAPI(title="Capability Service")


@app.get("/capabilities")
def get_capabilities() -> list[dict]:
    """The catalog: every compiled capability, as a tool schema an agent platform can register
    directly with its model."""
    return [to_tool_schema(cap) for cap in list_capabilities()]


@app.post("/capabilities/{name}/invoke")
def post_invoke(name: str, args: dict[str, str]) -> dict:
    """Execute one capability for real. args is exactly the typed argument object a tool call
    would generate against that capability's own input_schema."""
    try:
        return invoke_capability(name, args, fault=_FAULT)
    except CapabilityNotFound:
        raise HTTPException(status_code=404, detail=f"no capability named {name!r}") from None
