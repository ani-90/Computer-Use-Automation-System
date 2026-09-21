"""Command line entry point: python -m cua.cli discover --param NAME=VALUE ... [--dry-run]."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from cua.goal import GoalSpec, TaggedValues, parse_params


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="run the discovery agent for a goal")
    discover.add_argument("--goal", default="goals/transfer_funds.json")
    discover.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    discover.add_argument("--dry-run", action="store_true", help="validate and show the goal only")
    return parser


def _pairs(items: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise ValueError(f"expected NAME=VALUE, got {item!r}")
        pairs[name] = value
    return pairs


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
        print("the discovery loop is not wired yet; use --dry-run", file=sys.stderr)
        return 2
    print(f"start URL:            {start_url}")
    print(f"tagged parameters:    {', '.join(tagged.params)}")
    print(f"secrets (not shown):  {', '.join(tagged.secrets)}")
    print("\n--- goal text the agent will receive ---")
    print(spec.goal_text(params))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
