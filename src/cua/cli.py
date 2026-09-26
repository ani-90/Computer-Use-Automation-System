"""Command line entry point: python -m cua.cli discover --param NAME=VALUE ... [--dry-run]."""

import argparse
import os
import queue
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

from cua.goal import GoalSpec, PolicyBlockError, TaggedValues, parse_params

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "discovery_system.md"
ESCALATION_TIMEOUT_S = 60.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="run the discovery agent for a goal")
    discover.add_argument("--goal", default="goals/transfer_funds.json")
    discover.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    discover.add_argument("--dry-run", action="store_true", help="validate and show the goal only")
    discover.add_argument("--max-steps", type=int, default=None, help="override MAX_STEPS")
    discover.add_argument("--timeout", type=float, default=None, help="override TIMEOUT (seconds)")
    discover.add_argument(
        "--capability-out", default=None, help="where to write the compiled artifact on SUCCESS"
    )
    replay = sub.add_parser("replay", help="run a compiled capability with no LLM")
    replay.add_argument("--capability", default="capabilities/transfer_funds.json")
    replay.add_argument(
        "--goal", default="goals/transfer_funds.json",
        help="only used for the start URL and secret env-var names, never the prompt or steps",
    )
    replay.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    replay.add_argument(
        "--inject-faults", action="store_true",
        help="required before any --fault-* flag has any effect; never set by a normal run",
    )
    replay.add_argument("--fault-step", type=int, default=None, metavar="INDEX")
    replay.add_argument("--fault-type", choices=["transient_fail", "clear_session"], default=None)
    replay.add_argument(
        "--fault-url-pattern", default=None, metavar="GLOB",
        help="transient_fail only: which request to delay (Playwright glob pattern)",
    )
    replay.add_argument("--fault-delay-ms", type=int, default=3000)
    return parser


def _pairs(items: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise ValueError(f"expected NAME=VALUE, got {item!r}")
        pairs[name] = value
    return pairs


def _run_live(
    spec: GoalSpec,
    params: dict[str, str],
    tagged: TaggedValues,
    start_url: str,
    max_steps: int | None,
    timeout: float | None,
    capability_out: str | None,
) -> int:
    # Imported here so --dry-run and the tests never load the browser or the SDK.
    from cua.adapter import PlaywrightAdapter
    from cua.anthropic_llm import AnthropicLLM
    from cua.config import Config
    from cua.discovery import DiscoveryConfig, run_discovery
    from cua.enums import StopReason
    from cua.evidence import EvidenceLogger, new_run_id
    from cua.llm import LLMError
    from cua.policy_gate import PolicyGate
    from cua.redaction import Redactor

    try:
        llm = AnthropicLLM()
        llm.check_credentials()
    except LLMError as e:
        print(f"error: {e} (set ANTHROPIC_API_KEY in .env)", file=sys.stderr)
        return 2
    config = Config()
    secrets = list(tagged.secrets.values())
    redactor = Redactor(config, secrets=secrets)
    logger = EvidenceLogger(new_run_id(), redactor)
    limits = {"max_steps": max_steps, "timeout_s": timeout}
    settings = DiscoveryConfig(**{k: v for k, v in limits.items() if v})
    print("LIVE RUN: uses the Anthropic API and may move money in the sandbox.")
    print(f"evidence: {logger.dir}")
    print(f"limits: max_steps={settings.max_steps}, timeout={settings.timeout_s:.0f}s")
    with PlaywrightAdapter(config, logger, secrets=secrets) as adapter:
        result = run_discovery(
            spec=spec,
            params=params,
            tagged=tagged,
            adapter=adapter,
            llm=llm,
            gate=PolicyGate(config),
            logger=logger,
            system_prompt=PROMPT.read_text(encoding="utf-8"),
            start_url=start_url,
            config=settings,
        )
    summary = redactor.redact(result.summary())
    detail = f" ({summary['detail']})" if summary["detail"] else ""
    print(f"\nstop reason: {summary['stop_reason']}{detail}")
    print(
        f"counted steps: {summary['counted_steps']} | model calls: {summary['llm_calls']} | "
        f"tokens in/out: {summary['input_tokens']}/{summary['output_tokens']} | "
        f"{summary['elapsed_seconds']:.0f}s"
    )
    for name, value in summary["outputs"].items():
        print(f"  {name}: {value}")
    if result.stop_reason == StopReason.SUCCESS:
        from cua.compiler import compile_capability

        cap = compile_capability(result, spec)
        out = Path(capability_out) if capability_out else Path("capabilities") / f"{spec.name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cap.model_dump_json(indent=2), encoding="utf-8")
        print(f"capability: {out}")
    return 0 if result.stop_reason == StopReason.SUCCESS else 1


def _build_fault(args: argparse.Namespace):
    from cua.models import FaultInjection

    given = args.fault_step is not None or args.fault_type is not None
    if not args.inject_faults:
        if given:
            raise ValueError("--fault-step/--fault-type require --inject-faults")
        return None
    if args.fault_step is None or args.fault_type is None:
        raise ValueError("--inject-faults requires both --fault-step and --fault-type")
    return FaultInjection(
        step_index=args.fault_step, fault_type=args.fault_type,
        url_pattern=args.fault_url_pattern, delay_ms=args.fault_delay_ms,
    )


def _run_replay(capability_path: str, goal_path: str, raw_params: list[str], fault=None) -> int:
    # Imported here so tests and --dry-run never load the browser.
    from cua.adapter import PlaywrightAdapter
    from cua.config import Config
    from cua.enums import Outcome
    from cua.evidence import EvidenceLogger, new_run_id
    from cua.models import Capability
    from cua.policy_gate import PolicyGate
    from cua.redaction import Redactor
    from cua.replay import ReplayEngine

    try:
        capability = Capability.model_validate_json(Path(capability_path).read_text(encoding="utf-8"))
        spec = GoalSpec.model_validate_json(Path(goal_path).read_text(encoding="utf-8"))
        params = _pairs(raw_params)
        secrets = spec.secrets(os.environ)
        start_url = spec.start_url(os.environ)
    except KeyError as e:
        print(f"error: missing environment variable {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    config = Config()
    redactor = Redactor(config, secrets=list(secrets.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=Path("evidence") / "replay")
    print("LIVE REPLAY: no LLM is called; this may move money in the sandbox.")
    print(f"evidence: {logger.dir}")
    if fault is not None:
        print(f"FAULT INJECTION ACTIVE: {fault.fault_type} at step {fault.step_index}")

    def on_escalate(ticket):
        print(f"\n=== ESCALATION: {ticket.reason} ===")
        print(f"ticket: {ticket.ticket_id} (see {logger.dir / f'ticket-{ticket.ticket_id}.json'})")
        print("The browser is now yours. To APPROVE: click Transfer yourself, then press Enter.")
        print("To REJECT: type 'reject' then press Enter — Transfer will never be clicked.")
        print(f"No response within {ESCALATION_TIMEOUT_S:.0f}s is treated as not approved.")
        # input() has no native timeout. A daemon thread is the safe way to bound it: unlike
        # concurrent.futures.ThreadPoolExecutor (which registers an exit hook that waits for
        # every thread it ever created — a timed-out wait would just make the whole process
        # hang at exit instead), a daemon thread is never waited for, so a genuinely unanswered
        # prompt can be abandoned cleanly without blocking anything, ever.
        answers: queue.Queue[str] = queue.Queue(maxsize=1)
        threading.Thread(target=lambda: answers.put(input("> ")), daemon=True).start()
        try:
            answer = answers.get(timeout=ESCALATION_TIMEOUT_S).strip().lower()
        except queue.Empty:
            print(f"\nno response within {ESCALATION_TIMEOUT_S:.0f}s — treating as not approved.")
            return "timeout"
        return "reject" if answer == "reject" else "approve"

    with PlaywrightAdapter(config, logger, secrets=list(secrets.values())) as adapter:
        result = ReplayEngine(adapter, PolicyGate(config), logger, operator=os.environ.get("CUA_OPERATOR") or None).replay(
            capability, params, secrets, start_url, on_escalate, fault
        )
    summary = redactor.redact(result.model_dump(mode="json"))
    print(f"\nstatus: {summary['status']}")
    if summary.get("business_outcome"):
        print(f"business_outcome: {summary['business_outcome']}")
    if summary.get("side_effects"):
        print(f"side_effects: {summary['side_effects']}")
    print(f"llm_calls: {summary['llm_calls']}")
    if summary["outputs"]:
        print(f"  confirmation_text: {summary['outputs']['confirmation_text']}")
        print(f"  new_balance: {summary['outputs']['new_balance']}")
        print(f"  transaction_id: {summary['outputs']['transaction_id']}")
    if summary["failure_detail"]:
        fd = summary["failure_detail"]
        print(f"  failure at step {fd['step_index']}: expected {fd['expected']!r}, observed {fd['observed']!r}")
    for esc in summary["escalations"]:
        if esc["status"] == "open":  # an unverified dispatch: recorded for a human, never resolved in-run
            print(f"  escalation {esc['ticket_id']}: open, needs manual verification ({esc['reason']})")
        else:
            print(f"  escalation {esc['ticket_id']}: {esc['decision']} ({esc['reason']})")
    for esc in result.escalations:
        if esc.status == "open" and esc.procedure:
            # The operator needs the real accounts to check the ledger, and the saved ticket
            # cannot show them (it is redacted). Same trust channel as a browser handed to a human:
            # printed to this screen only, never written to any file or log.
            print("\n  === OPERATOR: verify before any retry (real values, shown on this screen only) ===")
            print("\n".join("  " + line for line in esc.procedure.splitlines()))
    return 0 if result.status == Outcome.SUCCESS else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    if args.command == "replay":
        try:
            fault = _build_fault(args)
        except (ValueError, TypeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return _run_replay(args.capability, args.goal, args.param, fault)
    try:
        spec = GoalSpec.model_validate_json(Path(args.goal).read_text(encoding="utf-8"))
        params = parse_params(spec, _pairs(args.param))
        tagged = TaggedValues(
            params, {n: p.type for n, p in spec.inputs.items()}, spec.secrets(os.environ)
        )
        start_url = spec.start_url(os.environ)
    except KeyError as e:
        print(f"error: missing environment variable {e}", file=sys.stderr)
        return 2
    except PolicyBlockError as e:  # before anything is dispatched: no browser, no model call
        print(f"policy block: {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not args.dry_run:
        return _run_live(
            spec, params, tagged, start_url, args.max_steps, args.timeout, args.capability_out
        )
    print(f"start URL:            {start_url}")
    print(f"tagged parameters:    {', '.join(tagged.params)}")
    print(f"secrets (not shown):  {', '.join(tagged.secrets)}")
    print("\n--- goal text the agent will receive ---")
    print(spec.goal_text(params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
