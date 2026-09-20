"""Smoke test, pass 2: log in through the adapter and visit the flow's pages. No LLM.

Test-only ParaBank knowledge lives in this file (field hints, page paths). No agent sees it.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

from cua.adapter import Action, Candidate, LocatorNotFound, Observation, PlaywrightAdapter
from cua.config import Config
from cua.evidence import EvidenceLogger, new_run_id
from cua.redaction import Redactor

load_dotenv()
BASE = os.environ["PARABANK_BASE_URL"].rstrip("/")
USER = os.environ["PARABANK_USERNAME"]
PASSWORD = os.environ["PARABANK_PASSWORD"]
SCRATCH = Path("scratch")

config = Config()
redactor = Redactor(config, secrets=[PASSWORD])
visited: list[str] = []


def show(adapter: PlaywrightAdapter, label: str) -> Observation:
    obs = adapter.observe()
    visited.append(obs.url)
    SCRATCH.mkdir(exist_ok=True)
    (SCRATCH / f"{label}.png").write_bytes(obs.masked_screenshot)  # masked copy only
    print(f"\n== {label}: {redactor.redact(obs.url)}")
    for c in obs.candidates:
        shown = redactor.redact(c.name or c.hint or "")
        print(f"  [{c.index}] {c.role} {shown!r}  box={'yes' if c.box else 'no'}")
    return obs


def pick(obs: Observation, role: str, label: str) -> Candidate:
    found = next((c for c in obs.candidates if c.role == role and label in (c.name, c.hint)), None)
    if found is None:
        raise SystemExit(f"no {role} {label!r} on the page")
    return found


def check(adapter: PlaywrightAdapter, cand: Candidate):
    try:
        element = adapter.resolve(cand.locator)
    except LocatorNotFound:
        print(f"  resolve FAILED: {cand.locator.description}")
        return None
    print(f"  resolve ok: {cand.locator.description}")
    return element


def main() -> None:
    if not PASSWORD:
        raise SystemExit("set PARABANK_PASSWORD in .env first")
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=SCRATCH / "evidence")
    with PlaywrightAdapter(config, logger) as adapter:
        adapter.navigate(f"{BASE}/index.htm")
        obs = show(adapter, "login")
        wanted = [
            pick(obs, "textbox", "Username"),
            pick(obs, "textbox", "Password"),
            pick(obs, "button", "Log In"),
        ]
        print("\n-- login resolve checks:")
        user_el, pass_el, button_el = (check(adapter, c) for c in wanted)
        if not (user_el and pass_el and button_el):
            raise SystemExit("cannot log in: a login element did not resolve")
        adapter.act(user_el, Action("type", USER))
        adapter.act(pass_el, Action("type", PASSWORD))
        adapter.act(button_el, Action("click"))

        obs = show(adapter, "after_login")
        print("\n-- duplicate-name check:")
        check(adapter, pick(obs, "link", "Transfer Funds"))  # fails if two links share the name

        account_id = ""  # extracted on overview, used on findtrans, never printed
        for page in ("overview", "transfer", "findtrans"):
            adapter.navigate(f"{BASE}/{page}.htm")
            obs = show(adapter, page)
            if page in ("transfer", "findtrans"):
                failed = []
                for c in obs.candidates:
                    try:
                        adapter.resolve(c.locator)
                    except LocatorNotFound:
                        failed.append(redactor.redact(c.locator.description))
                ok = len(obs.candidates) - len(failed)
                print(f"  resolved {ok}/{len(obs.candidates)}; failed: {failed}")
            if page == "overview":
                lengths = sorted(len(d) for c in obs.candidates for d in re.findall(r"\d+", c.name))
                print(f"  digit-run lengths in link names (values not shown): {lengths}")
                acct = next((c for c in obs.candidates if re.fullmatch(r"\d{5,12}", c.name)), None)
                if acct is None:
                    print("  extract skipped: no account link found")
                else:
                    try:
                        text = adapter.act(adapter.resolve(acct.locator), Action("extract"))
                        account_id = (text or "").strip()
                        print(f"  extract ok: text length {len(account_id)}")
                    except Exception as e:  # noqa: BLE001 - type name only, message may hold an ID
                        print(f"  extract FAILED: {type(e).__name__}")
            if page == "findtrans" and account_id:
                dropdown = next(c for c in obs.candidates if c.role == "combobox")
                try:
                    adapter.act(adapter.resolve(dropdown.locator), Action("select", account_id))
                    print("  select ok")
                except Exception as e:  # noqa: BLE001 - type name only, message may hold an ID
                    print(f"  select FAILED: {type(e).__name__}")

        print("\n== visited URLs (compare with the allowlist):")
        for url in dict.fromkeys(visited):
            print(" ", redactor.redact(url))
        print(f"\n== adapter log events ({logger.path}):")
        print(logger.path.read_text(encoding="utf-8"))
        input("Browser is open. Press Enter to close it...")


if __name__ == "__main__":
    main()
