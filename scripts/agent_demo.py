"""Agent-facing demo: plays the role of interface.ai's platform tool-execution layer.

Not "their agent" itself — a stand-in for the network client their platform's executor would be:
fetches the capability catalog over real HTTP, registers it as tools with Claude, and when the
model decides to call one, makes a REAL HTTP request to our own capability service (api.py) to
execute it. That HTTP request is the one moment that opens a real browser and moves real money —
everything before it is just API/HTTP traffic.

Requires the capability service running first, in a separate terminal:
    python -m uvicorn cua.api:app --port 8000

Usage:
    python scripts/agent_demo.py "transfer $5 from account 12345 to account 67890"

Costs a small amount of real Anthropic API usage (Haiku, at most two short calls — no screenshots,
no agentic loop, nothing like Discovery's cost).
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import anthropic
from dotenv import load_dotenv

from cua.config import Config
from cua.redaction import Redactor

MODEL = "claude-haiku-4-5-20251001"
API_BASE = "http://localhost:8000"
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence" / "agent_demo"


def _get(path: str) -> object:
    with urllib.request.urlopen(f"{API_BASE}{path}") as resp:
        return json.loads(resp.read())


def _post(path: str, body: dict) -> object:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")}


def main(instruction: str) -> None:
    load_dotenv()
    # One redactor, used for every single print AND the saved transcript — the real account
    # numbers live not just in the tool_result but in the conversation itself (the instruction,
    # the arguments the model generated), so redaction has to cover everything printed to the
    # console, not just what gets written to disk. Found live: the terminal output leaked real
    # account numbers even though the saved file was already correctly redacted.
    redactor = Redactor(Config())

    def show(text: str) -> None:
        print(redactor.redact(text))

    transcript: dict = {"instruction": instruction}
    client = anthropic.Anthropic()

    # 1. Fetch the catalog over real HTTP — the platform executor's own first move, not us
    #    importing to_tool_schema() directly.
    try:
        catalog = _get("/capabilities")
    except urllib.error.URLError as e:
        print(f"error: capability service not reachable at {API_BASE} — is uvicorn running? ({e})")
        return
    transcript["catalog"] = catalog
    show(f"fetched catalog: {[t['name'] for t in catalog]}")

    # 2. Ask the model, with that catalog registered as tools.
    messages = [{"role": "user", "content": instruction}]
    response = client.messages.create(model=MODEL, max_tokens=1024, tools=catalog, messages=messages)
    transcript["first_response"] = [b.model_dump(exclude_none=True) for b in response.content]

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        text = next((b.text for b in response.content if b.type == "text"), "(no text)")
        show(f"model did not call a tool. reply: {text}")
        _save(transcript, redactor)
        return
    show(f"model wants to call: {tool_use.name}({tool_use.input})")

    # 3. The real network call — this is the moment a real browser opens and real money moves.
    result = _post(f"/capabilities/{tool_use.name}/invoke", tool_use.input)
    transcript["invoke_request"] = tool_use.input
    transcript["invoke_response"] = result
    show(f"capability service responded: status={result.get('status', result.get('error'))}")

    # 4. Feed the tool_result back to the model for its final, human-facing reply.
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": json.dumps(result)}],
    })
    final = client.messages.create(model=MODEL, max_tokens=1024, tools=catalog, messages=messages)
    transcript["final_response"] = [b.model_dump(exclude_none=True) for b in final.content]
    final_text = next((b.text for b in final.content if b.type == "text"), "(no text)")

    print("\nfinal reply to the user:")
    show(final_text)
    _save(transcript, redactor)


def _save(transcript: dict, redactor: Redactor) -> None:
    # The transcript holds real account numbers (the instruction, the generated tool args,
    # possibly the model's own reply echoing them back) — redacted the same way every other
    # write to /evidence/ is, never trusted to whatever happens to already look clean.
    redacted = redactor.redact(transcript)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"transcript-{int(time.time())}.json"
    path.write_text(json.dumps(redacted, indent=2), encoding="utf-8")
    print(f"\ntranscript saved: {path}")


if __name__ == "__main__":
    instruction = " ".join(sys.argv[1:]) or "transfer $5 from account 12345 to account 67890"
    main(instruction)
