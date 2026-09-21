"""Command line entry point: python -m cua.cli discover --param NAME=VALUE ... [--dry-run]."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from cua.goal import GoalSpec, TaggedValues, parse_params

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "discovery_system.md"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="run the discovery agent for a goal")
    discover.add_argument("--goal", default="goals/transfer_funds.json")
    discover.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    discover.add_argument("--dry-run", action="store_true", help="validate and show the goal only")
    discover.add_argument("--max-steps", type=int, default=None, help="override MAX_STEPS")
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
    settings = DiscoveryConfig(max_steps=max_steps) if max_steps else DiscoveryConfig()
    print("LIVE RUN: uses the Anthropic API and may move money in the sandbox.")
    print(f"evidence: {logger.dir}")
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
    return 0 if result.stop_reason == StopReason.SUCCESS else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
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
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not args.dry_run:
        return _run_live(spec, params, tagged, start_url, args.max_steps)
    print(f"start URL:            {start_url}")
    print(f"tagged parameters:    {', '.join(tagged.params)}")
    print(f"secrets (not shown):  {', '.join(tagged.secrets)}")
    print("\n--- goal text the agent will receive ---")
    print(spec.goal_text(params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
