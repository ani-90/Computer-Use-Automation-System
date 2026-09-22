"""The Replay Engine against a scripted fake adapter, walking the REAL compiled artifact.

Unlike test_compiler.py, this drives the actual committed `capabilities/transfer_funds.json` —
the same 15 real steps a browser would run — through a fake standing in for PlaywrightAdapter,
so a structural mismatch between what the compiler wrote and what Replay expects to read shows
up here, not only in a live run.
"""

from decimal import Decimal
from pathlib import Path

from cua.adapter import ActionFailed, Candidate, LocatorNotFound, Observation, TextNode
from cua.config import Config
from cua.enums import Outcome
from cua.models import Capability, Locator, LocatorCandidate
from cua.policy_gate import PolicyGate
from cua.replay import ReplayEngine

CAP_PATH = Path(__file__).resolve().parent.parent / "capabilities" / "transfer_funds.json"
FROM, TO, AMOUNT = "111111", "222222", "12"
SECRETS = {"username": "svc-user", "password": "svc-pass"}
PARAMS = {"from_account": FROM, "to_account": TO, "amount": AMOUNT}
BASE = "http://h/parabank"


def capability() -> Capability:
    cap = Capability.model_validate_json(CAP_PATH.read_text(encoding="utf-8"))
    extract_as = {0: "policy_balance", 6: "confirmation_text", 8: "new_balance", 14: "transaction_id"}
    for i, name in extract_as.items():
        assert cap.steps[i].action == "extract" and cap.steps[i].extract_as == name, (
            f"step {i} does not carry the expected extract_as={name!r} — the committed artifact "
            "may need recompiling"
        )
    assert cap.amount_input == "amount"
    return cap


def loc(description: str) -> Locator:
    return Locator(description=description, chain=[LocatorCandidate(strategy="text", value=description)])


def text_loc(kind: str, text: str) -> Locator:
    # Matches the real adapter's text-strategy locator for a heading/paragraph/plain text node.
    return Locator(description=f'{kind} "{text}"', chain=[LocatorCandidate(strategy="text", value=text)])


def cell_loc(anchor: str, key) -> Locator:
    if isinstance(key, int):
        return Locator(
            description=f'cell[row "{anchor}", col {key}]',
            chain=[LocatorCandidate(strategy="table_cell", value=anchor, col=key)],
        )
    return Locator(
        description=f'cell[row "{anchor}", "{key}"]',
        chain=[LocatorCandidate(strategy="table_cell", value=anchor, column=key)],
    )


class Handle:
    def __init__(self, key):
        self.key = key


class FakeBank:
    """Enough of ParaBank's real pages, parameterized, to walk the actual compiled artifact."""

    def __init__(self, start_balance: str = "1040.00"):
        self.page = "login"
        self.fields: dict[str, str] = {}
        self.selected: dict[str, str] = {"From account #": FROM, "to account #": FROM}
        self.find_selected = FROM
        self.balance = start_balance
        self.new_balance: str | None = None
        self.transferred = False
        self.searched = False
        self.opened_details = False
        self.owner = "agent"
        self.pending_captures: list[dict] = []
        self.session_expired = False  # when True, even a known-authenticated page redirects to login
        self.login_failed = False  # set on a rejected Log In click; models the real error page

    @property
    def url(self) -> str:
        page_paths = {
            "login": "/parabank/index.htm", "overview": "/parabank/overview.htm",
            "transfer": "/parabank/transfer.htm", "findtrans": "/parabank/findtrans.htm",
            "results": "/parabank/findtrans.htm", "details": "/parabank/transaction.htm?id=998877",
        }
        return BASE.replace("/parabank", "") + page_paths[self.page]

    def navigate(self, target: str) -> None:
        from urllib.parse import urlparse

        path = urlparse(target).path
        if path.endswith("overview.htm"):
            self.page = "login" if self.session_expired else "overview"
        elif path.endswith("index.htm"):
            self.page = "login"
        # else: an unrecognized target — leave the current page as-is

    # -- element model, rebuilt fresh each observe() -----------------------
    def _candidates(self) -> list[tuple]:
        if self.page == "login":
            return [("textbox", None, "Username"), ("textbox", None, "Password"), ("button", "Log In", None)]
        if self.page == "overview":
            return [("link", "Transfer Funds", None), ("link", "Find Transactions", None),
                    ("link", "Accounts Overview", None), ("link", FROM, None), ("link", TO, None)]
        if self.page == "transfer":
            return [("textbox", None, "Amount: $"), ("combobox", None, "From account #"),
                    ("combobox", None, "to account #"), ("button", "Transfer", None),
                    ("link", "Accounts Overview", None)]
        if self.page in ("findtrans", "results"):
            base = [("combobox", None, "Select an account:")] + [("textbox", None, f"f{i}") for i in range(4)]
            base += [("textbox", None, "Find by Amount:"), ("button", "Find Transactions", None)]
            if self.searched:
                base.append(("link", "Funds Transfer Sent", None))
            return base
        if self.page == "details":
            return []
        return []

    def _texts(self) -> list[tuple]:
        if self.page == "login" and self.login_failed:
            # Confirmed live via scratch/probe_bad_login.py against the real site.
            return [
                ("heading", "Error!"),
                ("paragraph", "The username and password could not be verified."),
            ]
        if self.page == "overview":
            return [
                ("heading", "Account Services"), ("heading", "Accounts Overview"),
                ("paragraph", "Welcome Test Agent"),
            ]
        if self.page == "transfer":
            if self.transferred:
                sentence = (
                    f"${self.fields.get('Amount: $', '')}.00 has been transferred "
                    f"from account #{FROM} to account #{TO}."
                )
                return [
                    ("heading", "Transfer Complete!"), ("paragraph", sentence),
                    ("paragraph", "See Account Activity for more details."),
                ]
            return [("heading", "Transfer Funds"), ("text", "Amount: $")]
        if self.page in ("findtrans", "results"):
            if self.searched:
                return [("heading", "Transaction Results")]
            return [
                ("heading", "Find Transactions"), ("text", "Find by Transaction ID:"),
                ("paragraph", "Find by Date Range"),
            ]
        if self.page == "details" and self.opened_details:
            return [("heading", "Transaction Details")]
        return []

    def _cells(self) -> dict:
        if self.page == "overview":
            return {(FROM, "Balance*"): f"${self.new_balance or self.balance}"}
        if self.page == "details" and self.opened_details:
            return {("Transaction ID:", 1): "998877"}
        return {}

    def observe(self) -> Observation:
        cands = []
        role_nth: dict[str, int] = {}
        for i, (role, name, key) in enumerate(self._candidates(), start=1):
            desc = name or key
            n = role_nth.get(role, 0)
            role_nth[role] = n + 1
            if name:
                # Matches the real adapter's convention for a named element: role+name, with
                # a text fallback — see PlaywrightAdapter._describe.
                element_loc = Locator(
                    description=f'{role} "{name}"',
                    chain=[
                        LocatorCandidate(strategy="role_name", role=role, value=name),
                        LocatorCandidate(strategy="text", value=name),
                    ],
                )
            else:
                # Unnamed field: role + position, exactly what a compiled step's target for an
                # unnamed textbox/combobox actually is (never text-matched, since there's no
                # accessible name to match on) — see PlaywrightAdapter._describe_unnamed.
                element_loc = Locator(
                    description=f'{role} "{desc}"',
                    chain=[LocatorCandidate(strategy="role_name", role=role, nth=n)],
                )
            kwargs = {}
            if role == "textbox":
                typed = self.fields.get(desc, "")
                kwargs = {"value": typed or None, "filled": bool(typed)}
            if role == "combobox":
                if desc == "Select an account:":
                    kwargs = {"selected": self.find_selected, "options": (FROM, TO)}
                else:
                    kwargs = {"selected": self.selected.get(desc), "options": (FROM, TO)}
            cands.append(Candidate(i, role, name or "", key, None, element_loc, **kwargs))
        texts = [
            TextNode(100 + i, kind, text, text_loc(kind, text))
            for i, (kind, text) in enumerate(self._texts())
        ]
        texts += [
            TextNode(200 + i, "cell", text, cell_loc(*key))
            for i, (key, text) in enumerate(self._cells().items())
        ]
        return Observation(self.url, b"raw", b"masked", cands, texts)

    def resolve(self, locator: Locator):
        cand = locator.chain[0]
        if cand.strategy == "table_cell":
            key = (cand.value, cand.column or cand.col)
            if key in self._cells():
                return Handle(("cell", key))
            raise LocatorNotFound(locator.description)
        if cand.strategy == "text":
            for kind, text in self._texts():
                if text == cand.value:
                    return Handle(("text", text))
            raise LocatorNotFound(locator.description)
        if cand.strategy == "role_name":
            items = self._candidates()
            if cand.value:  # named: role + accessible name (interactive elements or headings/text)
                for role, name, key in items:
                    if role == cand.role and name == cand.value:
                        return Handle(("field", name or key))
                for kind, text in self._texts():
                    if kind == cand.role and text == cand.value:
                        return Handle(("text", text))
                raise LocatorNotFound(locator.description)
            same_role = [it for it in items if it[0] == cand.role]
            idx = cand.nth or 0
            if idx < len(same_role):
                _, name, key = same_role[idx]
                return Handle(("field", name or key))
            raise LocatorNotFound(locator.description)
        raise LocatorNotFound(locator.description)

    def act(self, handle: Handle, action) -> str | None:
        kind, key = handle.key
        if kind == "cell":
            return self._cells()[key]
        if kind == "text":
            return key
        # field
        if action.kind == "extract":
            return self.fields.get(key, "")
        if action.kind == "type":
            self.fields[key] = action.value
            return None
        if action.kind == "select":
            if action.value not in (FROM, TO):
                raise ActionFailed("option not found")
            if key == "Select an account:":
                self.find_selected = action.value
            else:
                self.selected[key] = action.value
            return None
        # click, by field name
        if key == "Log In":
            ok = self.fields.get("Username") == SECRETS["username"] and self.fields.get("Password") == SECRETS["password"]
            self.page = "overview" if ok else "login"
            self.login_failed = not ok
        elif key == "Transfer Funds":
            self.page = "transfer"
        elif key == "Transfer":
            if self.fields.get("Amount: $") and self.selected.get("From account #") == FROM:
                self.transferred = True
                self.new_balance = "1028.00"
        elif key == "Accounts Overview":
            self.page = "overview"
        elif key == "Find Transactions" and self.page in ("transfer", "overview"):
            self.page = "findtrans"
        elif key == "Find Transactions":
            if self.fields.get("Find by Amount:") and self.find_selected == FROM:
                self.searched = True
                self.page = "results"
        elif key == "Funds Transfer Sent":
            self.opened_details = True
            self.page = "details"
        return None

    # -- escalation: the two methods a real PlaywrightAdapter provides for the handoff --------
    def set_session_owner(self, owner: str) -> None:
        self.owner = owner

    def captured_actions(self) -> list[dict]:
        actions = self.pending_captures
        self.pending_captures = []
        return actions

    def simulate_supervisor_click_transfer(self) -> None:
        """What a real supervisor's own click on Transfer does: the same state change act()
        performs for that button, plus a captured DOM click event — used by a test's
        on_escalate callback to stand in for a human actually clicking the live browser."""
        if self.fields.get("Amount: $") and self.selected.get("From account #") == FROM:
            self.transferred = True
            self.new_balance = "1028.00"
        self.pending_captures.append({"tag": "BUTTON", "text": "Transfer", "url": self.url})


def engine():
    return ReplayEngine(FakeBank(), PolicyGate(Config(approval_threshold=Decimal(100))))


def test_the_real_compiled_artifact_replays_to_success():
    result = engine().replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.SUCCESS
    assert result.outputs.new_balance == "$1028.00"  # as rendered, dollar sign included
    assert result.outputs.transaction_id == "998877"
    assert "transferred" in result.outputs.confirmation_text
    assert result.llm_calls == 0


def test_no_llm_client_can_even_be_passed_in():
    import inspect

    params = inspect.signature(ReplayEngine.__init__).parameters
    assert not any("llm" in p.lower() for p in params)


def test_zero_amount_is_blocked_before_the_browser_opens():
    fake = FakeBank()
    result = ReplayEngine(fake, PolicyGate(Config())).replay(
        capability(), {**PARAMS, "amount": "0"}, SECRETS, BASE + "/index.htm"
    )
    assert result.status == Outcome.POLICY_BLOCK
    assert fake.page == "login"  # never navigated: the browser was never touched


def test_missing_required_parameter_is_a_hard_failure_before_the_browser_opens():
    fake = FakeBank()
    bad = {"from_account": FROM, "amount": AMOUNT}  # to_account missing
    result = ReplayEngine(fake, PolicyGate(Config())).replay(capability(), bad, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.HARD_FAILURE
    assert "to_account" in result.failure_detail.observed
    assert fake.page == "login"


def test_the_same_account_twice_is_a_hard_failure_before_the_browser_opens():
    fake = FakeBank()
    same = {**PARAMS, "to_account": FROM}  # from_account and to_account identical
    result = ReplayEngine(fake, PolicyGate(Config())).replay(capability(), same, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.HARD_FAILURE
    assert "from_account and to_account must be different" in result.failure_detail.observed
    assert fake.page == "login"  # never navigated: the browser was never touched


def test_non_decimal_amount_is_a_hard_failure():
    result = engine().replay(capability(), {**PARAMS, "amount": "not-a-number"}, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.HARD_FAILURE


def test_amount_over_balance_is_policy_blocked_mid_flow_not_at_the_start():
    fake = FakeBank(start_balance="5.00")
    result = ReplayEngine(fake, PolicyGate(Config(approval_threshold=Decimal(100)))).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm"
    )
    assert result.status == Outcome.POLICY_BLOCK
    assert "balance" in result.failure_detail.observed
    assert not fake.transferred
    # blocked at the submission step, not when the amount was typed: the form is fully filled
    # in (accounts already picked) by the time the gate has anything to say about it.
    assert result.failure_detail.step_index == 5
    assert fake.selected.get("From account #") == FROM
    assert fake.selected.get("to account #") == TO


def test_amount_over_threshold_is_policy_blocked_with_no_escalation_path_yet():
    fake = FakeBank()
    result = ReplayEngine(fake, PolicyGate(Config(approval_threshold=Decimal(1)))).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm"
    )
    assert result.status == Outcome.POLICY_BLOCK
    assert not fake.transferred
    assert result.failure_detail.step_index == 5
    assert fake.selected.get("From account #") == FROM
    assert fake.selected.get("to account #") == TO


def test_wrong_credentials_are_classified_as_a_login_rejected_business_outcome():
    # Confirmed live via scratch/probe_bad_login.py: a wrong password lands back on the login
    # page with a real, recognizable rejection message — not a generic, unclassified failure.
    fake = FakeBank()
    bad_secrets = {"username": "wrong", "password": "wrong"}
    result = ReplayEngine(fake, PolicyGate(Config())).replay(capability(), PARAMS, bad_secrets, BASE + "/index.htm")
    assert result.status == Outcome.BUSINESS_OUTCOME
    assert result.business_outcome == "login_rejected"


def test_a_missing_element_is_a_hard_failure_not_a_crash():
    fake = FakeBank()
    cap = capability()
    # corrupt one step's target so resolve() can never find it, simulating drift
    cap.steps[1] = cap.steps[1].model_copy(update={"target": loc('button "Nonexistent"')})
    result = ReplayEngine(fake, PolicyGate(Config())).replay(cap, PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.HARD_FAILURE
    assert result.failure_detail.step_index == 1


def test_run_id_is_a_real_uuid_and_unique_per_run():
    import uuid

    r1 = engine().replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    r2 = engine().replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    uuid.UUID(r1.run_id)
    uuid.UUID(r2.run_id)
    assert r1.run_id != r2.run_id
