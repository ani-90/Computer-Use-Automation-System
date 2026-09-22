"""wait_strategy is actually honored, not just carried in the artifact.

A synchronous fake (like FakeBank in test_replay.py) never exercises the difference between
"ready immediately" and "ready after a moment" — every check passes on the first try regardless
of whether the engine polls at all. These fakes are deliberately slow to become ready, so a
missing or broken wait shows up as a real test failure, not a silent gap.
"""

from cua.adapter import Candidate, LocatorNotFound, Observation, TextNode
from cua.config import Config
from cua.enums import Outcome
from cua.models import (
    Capability,
    Condition,
    ErrorMapping,
    Locator,
    LocatorCandidate,
    Step,
    WaitStrategy,
)
from cua.policy_gate import PolicyGate
from cua.replay import ReplayEngine

SECRETS = {"username": "svc-user", "password": "svc-pass"}


def named(role: str, text: str) -> Locator:
    return Locator(description=f'{role} "{text}"', chain=[LocatorCandidate(strategy="role_name", role=role, value=text)])


def unnamed(role: str, label: str, nth: int = 0) -> Locator:
    return Locator(description=f'{role} "{label}"', chain=[LocatorCandidate(strategy="role_name", role=role, nth=nth)])


class Handle:
    def __init__(self, key):
        self.key = key


class SlowDropdownBank:
    """Login works immediately; the combobox on the next page gains its options gradually."""

    def __init__(self, ready_after: int | None = 2, session_expired: bool = False):
        self.page = "login"
        self.fields: dict[str, str] = {}
        self.observe_calls_on_page2 = 0
        self.ready_after = ready_after  # None = never becomes ready
        self.selected: str | None = None
        # when True, even the auth probe's own known-authenticated target lands back on login —
        # models a session that expired mid-run, distinct from a genuine, still-authenticated
        # failure.
        self.session_expired = session_expired

    @property
    def url(self) -> str:
        # The hardcoded login checkpoint in replay.py expects exactly this path, and both paths
        # need to be allowlisted (Config()'s default list only knows /parabank/*).
        return "http://h/parabank/index.htm" if self.page == "login" else "http://h/parabank/overview.htm"

    def navigate(self, target: str) -> None:
        if target.endswith("overview.htm"):
            self.page = "login" if self.session_expired else "page2"  # the auth probe's target
        else:
            self.page = "login"

    def _options(self) -> tuple[str, ...]:
        if self.ready_after is None:
            return ()
        return ("A",) if self.observe_calls_on_page2 < self.ready_after else ("A", "B")

    def observe(self):
        if self.page == "login":
            cands = [
                Candidate(1, "textbox", "", None, None, unnamed("textbox", "Username", 0)),
                Candidate(2, "textbox", "", None, None, unnamed("textbox", "Password", 1)),
                Candidate(3, "button", "Log In", None, None, named("button", "Log In")),
            ]
            return Observation("http://h/parabank/index.htm", b"", b"", cands, [])
        self.observe_calls_on_page2 += 1
        cands = [
            Candidate(1, "combobox", "", None, None, unnamed("combobox", "Pick", 0),
                      selected=self.selected, options=self._options())
        ]
        texts = [TextNode(1, "heading", "Accounts Overview", named("heading", "Accounts Overview"))]
        return Observation(self.url, b"", b"", cands, texts)

    def resolve(self, locator: Locator):
        cand = locator.chain[0]
        if cand.strategy == "role_name" and cand.value == "Accounts Overview" and cand.role == "heading":
            if self.page == "page2":
                return Handle("heading")
            return self._raise(locator)
        if self.page == "login":
            if cand.role == "textbox":
                return Handle("Username" if cand.nth == 0 else "Password")
            if cand.role == "button":
                return Handle("Log In")
        if self.page == "page2" and cand.role == "combobox":
            return Handle("combobox")
        return self._raise(locator)

    def exists(self, locator: Locator) -> bool:
        try:
            self.resolve(locator)
            return True
        except LocatorNotFound:
            return False

    @staticmethod
    def _raise(locator):
        raise LocatorNotFound(locator.description)

    def act(self, handle: Handle, action) -> str | None:
        if handle.key == "Log In":
            self.page = "page2"
            return None
        if handle.key in ("Username", "Password"):
            self.fields[handle.key] = action.value
            return None
        if handle.key == "combobox":
            if action.value not in self._options():
                raise LocatorNotFound("option not in dropdown yet")
            self.selected = action.value
            return None
        return None


def _capability(timeout_ms: int, error_mapping: list[ErrorMapping] | None = None) -> Capability:
    target = unnamed("combobox", "Pick", 0)
    step = Step(
        precondition=[],
        action="select",
        target=target,
        parameters={"value": "B"},
        wait_strategy=WaitStrategy(kind="option_present", target=target, value="B", timeout_ms=timeout_ms),
        checkpoint=[Condition(kind="option_selected", target=target, value="B")],
        error_mapping=error_mapping or [],
    )
    return Capability(
        schema_version="1.0", version="1", name="wait_test",
        inputs={}, outputs={}, amount_input=None, steps=[step],
    )


def test_a_wait_that_becomes_ready_partway_through_polling_succeeds():
    fake = SlowDropdownBank(ready_after=2)  # not ready on the 1st or 2nd observe(), ready on the 3rd
    result = ReplayEngine(fake, PolicyGate(Config())).replay(
        _capability(timeout_ms=2000), {}, SECRETS, "http://h/parabank/index.htm"
    )
    assert result.status == Outcome.SUCCESS
    assert fake.observe_calls_on_page2 >= 2  # it genuinely polled more than once


def test_a_wait_that_never_becomes_ready_times_out_as_a_hard_failure_not_a_hang():
    fake = SlowDropdownBank(ready_after=None)
    result = ReplayEngine(fake, PolicyGate(Config())).replay(
        _capability(timeout_ms=300), {}, SECRETS, "http://h/parabank/index.htm"
    )
    assert result.status == Outcome.HARD_FAILURE
    assert result.failure_detail.step_index == 0
    assert "wait" in result.failure_detail.expected
    assert fake.selected is None  # never attempted the select with a stale option list


def test_a_dropdown_option_that_never_appears_is_classified_as_invalid_account():
    # Same timeout as the plain hard-failure case above, but this step's error_mapping (as the
    # compiler emits for every select step) recognizes the exact timed-out condition — so the
    # engine classifies it as a business outcome instead of a generic, unexplained failure.
    fake = SlowDropdownBank(ready_after=None)
    target = unnamed("combobox", "Pick", 0)
    mapping = [
        ErrorMapping(
            when=Condition(kind="option_present", target=target, value="B"),
            outcome=Outcome.BUSINESS_OUTCOME,
            detail="invalid_account",
        )
    ]
    result = ReplayEngine(fake, PolicyGate(Config())).replay(
        _capability(timeout_ms=300, error_mapping=mapping), {}, SECRETS, "http://h/parabank/index.htm"
    )
    assert result.status == Outcome.BUSINESS_OUTCOME
    assert result.business_outcome == "invalid_account"


def test_a_genuinely_expired_session_is_classified_as_recoverable_not_a_hard_failure():
    # No error_mapping matches this timeout, so the engine falls back to the auth probe. Here the
    # probe's own navigation lands back on login — the session expired mid-run, not a real defect
    # — so the outcome is RECOVERABLE (safe and cheap to just replay again), not HARD_FAILURE.
    fake = SlowDropdownBank(ready_after=None, session_expired=True)
    result = ReplayEngine(fake, PolicyGate(Config())).replay(
        _capability(timeout_ms=300), {}, SECRETS, "http://h/parabank/index.htm"
    )
    assert result.status == Outcome.RECOVERABLE
    assert result.business_outcome == "session_expired"
