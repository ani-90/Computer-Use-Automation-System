"""Exposes compiled capabilities as a catalog an outside agent can discover and call by name.

Purely additive: nothing here changes how ReplayEngine.replay() works, and nothing here ever
gives an LLM control over how a capability is executed — an outside agent only ever sees a name,
a typed argument list, and a typed result. Everything inside that boundary (the Policy Gate,
error classification, escalation) runs exactly as it always has.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cua.goal import GoalSpec
from cua.models import Capability, FaultInjection

_CAPABILITIES_DIR = Path(__file__).resolve().parents[2] / "capabilities"
_GOALS_DIR = Path(__file__).resolve().parents[2] / "goals"


class CapabilityNotFound(Exception):
    """No compiled capability with that name — never a partial/fuzzy match."""


def list_capabilities(capabilities_dir: Path = _CAPABILITIES_DIR) -> list[Capability]:
    """Every compiled capability found in the given directory, loaded and validated the same
    way the CLI loads one for replay — never a looser or separate parsing path."""
    return [
        Capability.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(capabilities_dir.glob("*.json"))
    ]


# ParamSpec.type -> JSON Schema type. Every param is still passed as a string at the replay
# boundary (ReplayEngine.replay() takes Mapping[str, str]), so "decimal" maps to "string" here
# too — the type distinction matters for validation inside the capability, not for the shape an
# outside caller sends it in.
_JSON_TYPE = {"string": "string", "decimal": "string"}


def to_tool_schema(capability: Capability) -> dict:
    """A Capability's own inputs contract, translated into the {name, description, input_schema}
    shape Claude's tool-use API expects — no new schema invented, just a re-expression of one
    that already exists."""
    return {
        "name": capability.name,
        "description": f"Run the {capability.name!r} capability against the live app.",
        "input_schema": {
            "type": "object",
            "properties": {name: {"type": _JSON_TYPE[spec.type]} for name, spec in capability.inputs.items()},
            "required": [name for name, spec in capability.inputs.items() if spec.required],
        },
    }


FAULT_ENV = "CUA_FAULT"


def fault_from_env(env: Mapping[str, str]) -> FaultInjection | None:
    """The operator-only switch for a live HTTP fault test: read once, when the service starts,
    from the process environment — never from a request, so a caller can never turn it on.

    Format: `transient_fail:<step>:<url glob>[:<delay_ms>]` or `clear_session:<step>`. Unset means
    no fault, exactly as before. A malformed value raises instead of being ignored: a fault the
    operator asked for and did not get would silently invalidate the test they are running."""
    raw = env.get(FAULT_ENV, "").strip()
    if not raw:
        return None
    kind, _, rest = raw.partition(":")
    parts = rest.split(":") if rest else []
    try:
        if kind == "clear_session" and len(parts) == 1:
            return FaultInjection(step_index=int(parts[0]), fault_type="clear_session")
        if kind == "transient_fail" and len(parts) >= 2:
            step = int(parts[0])
            if len(parts) >= 3 and parts[-1].isdigit():  # a trailing all-digit part is the delay
                return FaultInjection(
                    step_index=step, fault_type="transient_fail",
                    url_pattern=":".join(parts[1:-1]), delay_ms=int(parts[-1]),
                )
            return FaultInjection(step_index=step, fault_type="transient_fail", url_pattern=":".join(parts[1:]))
    except ValueError as e:
        raise ValueError(f"{FAULT_ENV} is malformed: {e}") from None
    raise ValueError(
        f"{FAULT_ENV} must look like 'transient_fail:<step>:<url glob>[:<delay_ms>]' or 'clear_session:<step>'"
    )


def invoke_capability(
    name: str, args: dict[str, str], on_escalate=None, fault: FaultInjection | None = None, *,
    capabilities_dir: Path = _CAPABILITIES_DIR, goals_dir: Path = _GOALS_DIR,
) -> dict[str, Any]:
    """Run a compiled capability by name, for real — the exact same path the CLI's `replay`
    subcommand uses (same goal file convention, same secrets-from-.env, same ReplayEngine), never
    a separate or looser execution path just because the caller is a tool call instead of a
    person typing a command. Returns a redacted, tool-result-shaped summary — never the raw
    ReplayResult, and never anything that could leak an account number or a credential back to
    whatever called this."""
    from cua.adapter import PlaywrightAdapter
    from cua.config import Config
    from cua.evidence import EvidenceLogger, new_run_id
    from cua.policy_gate import PolicyGate
    from cua.redaction import Redactor
    from cua.replay import ReplayEngine

    capability_path = capabilities_dir / f"{name}.json"
    goal_path = goals_dir / f"{name}.json"
    if not capability_path.exists():
        raise CapabilityNotFound(name)
    capability = Capability.model_validate_json(capability_path.read_text(encoding="utf-8"))
    spec = GoalSpec.model_validate_json(goal_path.read_text(encoding="utf-8"))
    secrets = spec.secrets(os.environ)
    start_url = spec.start_url(os.environ)

    config = Config()
    redactor = Redactor(config, secrets=list(secrets.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=Path("evidence") / "replay")

    with PlaywrightAdapter(config, logger, secrets=list(secrets.values())) as adapter:
        result = ReplayEngine(adapter, PolicyGate(config), logger).replay(
            capability, args, secrets, start_url, on_escalate, fault
        )
    return _for_caller(redactor.redact(result.model_dump(mode="json")))


def _for_caller(summary: dict[str, Any]) -> dict[str, Any]:
    """The verification procedure embeds this run's own parameters and is for a human operator
    only: an outside caller gets the ticket_id and run_id to correlate on, never the procedure."""
    for ticket in summary.get("escalations", []):
        ticket.pop("procedure", None)
    return summary
