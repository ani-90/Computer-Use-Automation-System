"""Pre-run check: does the real API accept every message shape our loop builds?

Uses the token-counting endpoint, which validates a request's structure without generating
anything. The fake model in the tests cannot enforce the API's rules, so this closes that gap.
A deliberately invalid message is sent too, to prove the check would catch a violation.

Usage: python scripts/validate_messages.py     (needs ANTHROPIC_API_KEY; exits 1 on any problem)
"""

import struct
import sys
import zlib
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from cua.anthropic_llm import MODEL
from cua.discovery import _build_messages, _Turn
from cua.goal import GoalSpec
from cua.render import image_block
from cua.tools import tool_definitions

ROOT = Path(__file__).resolve().parent.parent


def tiny_png() -> bytes:
    """A valid 1x1 white PNG: the API decodes images, so a fake one would be rejected."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xff\xff\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")


PNG = tiny_png()


def tool_use(call_id: str, name: str = "click", **args) -> dict:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args or {"ref": 1, "expect": "x"}}


def turn(*uses: dict, results: list[dict]) -> _Turn:
    return _Turn(assistant=[{"type": "text", "text": "reasoning"}, *uses], results=results)


def result(call_id: str | None, *, error: bool = False) -> dict:
    return {
        "id": call_id,
        "collapsed": "Step 1: click -> ok",
        "full": "Result: ok\n\nPage: /p/a.htm\n\nInteractive elements:\n[1] link \"x\"",
        "screenshot": PNG,
        "is_error": error,
    }


SCENARIOS = {
    "ordinary result": [turn(tool_use("a"), results=[result("a")])],
    "blocked action (error + screenshot)": [turn(tool_use("a"), results=[result("a", error=True)])],
    "rejected done claim": [
        turn(tool_use("a", "report_done", reasoning="done"), results=[result("a", error=True)])
    ],
    "parallel calls, one refused": [
        turn(tool_use("a"), tool_use("b"), results=[result("a"), {**result("b", error=True), "full": None}])
    ],
    "reply with no tool call (nudge)": [
        _Turn(assistant=[{"type": "text", "text": "hmm"}], results=[result(None)])
    ],
    "several turns, mixed": [
        turn(tool_use("a"), results=[result("a")]),
        turn(tool_use("b"), results=[result("b", error=True)]),
        turn(tool_use("c"), results=[result("c")]),
    ],
}


def main() -> int:
    load_dotenv(ROOT / ".env")
    spec = GoalSpec.model_validate_json((ROOT / "goals" / "transfer_funds.json").read_text("utf-8"))
    params = {"from_account": "ACCOUNT_A", "to_account": "ACCOUNT_B", "amount": "3"}
    tools = tool_definitions(spec.extract_descriptions(params))
    system = (ROOT / "prompts" / "discovery_system.md").read_text(encoding="utf-8")
    client = anthropic.Anthropic()
    first = f"Goal: {spec.goal_text(params)}\n\nPage: /p/index.htm"

    def count(messages: list[dict]) -> int:
        return client.messages.count_tokens(
            model=MODEL, system=system, messages=messages, tools=tools
        ).input_tokens

    failures = 0
    for label, turns in SCENARIOS.items():
        try:
            print(f"  accepted  {count(_build_messages(first, PNG, turns)):6d} tokens  {label}")
        except anthropic.BadRequestError as e:
            failures += 1
            print(f"  REJECTED  {label}: {e.message}")

    # The control: the exact shape that crashed the first live run must be refused.
    bad = _build_messages(first, PNG, SCENARIOS["ordinary result"])
    bad[-1]["content"][0]["is_error"] = True
    bad[-1]["content"][0]["content"].append(image_block(PNG))
    try:
        count(bad)
        failures += 1
        print("  CONTROL WAS ACCEPTED: this check cannot catch the bug it exists for")
    except anthropic.BadRequestError:
        print("  control   rejected as expected (an error result that holds an image)")
    print("\nall shapes accepted" if not failures else f"\n{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
