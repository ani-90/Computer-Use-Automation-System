"""Phase 8: fault injection (--inject-faults only) and the auto-recovery retry it proves out.

A minimal, purpose-built fake — not the full FakeBank — because what's under test here is the
retry control flow itself (auto re-login, retry the same step once, never loop), not the real
artifact's page model. clear_session()/delay_next_request() are the two adapter methods only
PlaywrightAdapter can really implement (real cookies, real routes); this fake just proxies them
into state changes that make the following step fail or succeed the same way the real app would.
"""

from cua.adapter import Candidate, LocatorNotFound, Observation, TextNode
from cua.config import Config
from cua.enums import Outcome
from cua.models import (
    Capability,
    Condition,
    FaultInjection,
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


def unnamed(role: str, label: str, nth: int) -> Locator:
    return Locator(description=f'{role} "{label}"', chain=[LocatorCandidate(strategy="role_name", role=role, nth=nth)])


class Handle:
    def __init__(self, key):
        self.key = key


class FaultBank:
    """login -> overview -> (click Confirm) -> confirm. One step, targeted for fault injection."""

    def __init__(self):
        self.page = "login"
        self.fields: dict[str, str] = {}
        self.session_expired = False
        self.clear_session_calls = 0
        self.delay_calls: list[tuple[str, int]] = []
        self.login_attempts = 0

    @property
    def url(self) -> str:
        return {
            "login": "http://h/parabank/index.htm",
            "overview": "http://h/parabank/overview.htm",
            "confirm": "http://h/parabank/confirm.htm",
        }[self.page]

    def navigate(self, target: str) -> None:
        if target.endswith("overview.htm"):
            self.page = "login" if self.session_expired else "overview"
        elif target.endswith("index.htm"):
            self.page = "login"

    def observe(self) -> Observation:
        if self.page == "login":
            cands = [
                Candidate(1, "textbox", "", None, None, unnamed("textbox", "Username", 0)),
                Candidate(2, "textbox", "", None, None, unnamed("textbox", "Password", 1)),
                Candidate(3, "button", "Log In", None, None, named("button", "Log In")),
            ]
            return Observation(self.url, b"", b"", cands, [])
        if self.page == "overview":
            cands = [Candidate(1, "heading", "Accounts Overview", None, None, named("heading", "Accounts Overview")),
                     Candidate(2, "button", "Confirm", None, None, named("button", "Confirm"))]
            return Observation(self.url, b"", b"", cands, [])
        texts = [TextNode(1, "heading", "Confirmed", named("heading", "Confirmed"))]
        return Observation(self.url, b"", b"", [], texts)

    def resolve(self, locator: Locator):
        cand = locator.chain[0]
        if self.page == "login":
            if cand.role == "textbox":
                return Handle("Username" if cand.nth == 0 else "Password")
            if cand.role == "button":
                return Handle("Log In")
        if self.page == "overview":
            if cand.role == "heading" and cand.value == "Accounts Overview":
                return Handle("Accounts Overview")
            if cand.role == "button" and cand.value == "Confirm":
                return Handle("Confirm")
        if self.page == "confirm" and cand.strategy == "role_name" and cand.value == "Confirmed":
            return Handle("Confirmed")
        raise LocatorNotFound(locator.description)

    def exists(self, locator: Locator) -> bool:
        try:
            self.resolve(locator)
            return True
        except LocatorNotFound:
            return False

    def act(self, handle: Handle, action) -> str | None:
        if handle.key == "Log In":
            ok = self.fields.get("Username") == SECRETS["username"] and self.fields.get("Password") == SECRETS["password"]
            self.login_attempts += 1
            self.page = "overview" if ok else "login"
            if ok:
                self.session_expired = False  # a fresh login always gets a fresh, valid session
            return None
        if handle.key in ("Username", "Password"):
            self.fields[handle.key] = action.value
            return None
        if handle.key == "Confirm":
            # The real behavior a cleared cookie produces: the next server round-trip redirects
            # to login instead of doing what was asked.
            self.page = "login" if self.session_expired else "confirm"
            return None
        return None

    def clear_session(self) -> None:
        self.session_expired = True
        self.clear_session_calls += 1

    def delay_next_request(self, url_pattern: str, delay_ms: int) -> None:
        self.delay_calls.append((url_pattern, delay_ms))


def _capability(timeout_ms: int = 300) -> Capability:
    target = named("button", "Confirm")
    step = Step(
        precondition=[],
        action="click",
        target=target,
        parameters={},
        wait_strategy=WaitStrategy(
            kind="element_visible",
            target=named("heading", "Confirmed"),
            timeout_ms=timeout_ms,
        ),
        checkpoint=[Condition(kind="element_visible", target=named("heading", "Confirmed"))],
        error_mapping=[],
    )
    return Capability(
        schema_version="1.0", version="1", name="fault_test",
        inputs={}, outputs={}, amount_input=None, steps=[step],
    )


def engine(fake: FaultBank) -> ReplayEngine:
    return ReplayEngine(fake, PolicyGate(Config()))


def test_a_normal_replay_never_touches_either_fault_method():
    # The --inject-faults gate lives in the CLI (only place a FaultInjection is ever built); at
    # the engine level, proof is that fault=None (the default for every existing caller) leaves
    # clear_session/delay_next_request completely untouched.
    fake = FaultBank()
    result = engine(fake).replay(_capability(), {}, SECRETS, "http://h/parabank/index.htm")
    assert result.status == Outcome.SUCCESS
    assert fake.clear_session_calls == 0
    assert fake.delay_calls == []


def test_clear_session_fault_is_auto_recovered_with_no_human_involved():
    fake = FaultBank()
    fault = FaultInjection(step_index=0, fault_type="clear_session")
    result = engine(fake).replay(_capability(), {}, SECRETS, "http://h/parabank/index.htm", fault=fault)
    assert result.status == Outcome.SUCCESS  # recovered in place, not surfaced as RECOVERABLE
    assert fake.clear_session_calls == 1
    assert fake.login_attempts == 2  # the original login, then the auto re-login after expiry


def test_a_session_that_keeps_expiring_is_not_retried_forever():
    # If the fault (or a real bug) makes the session look expired again right after the
    # re-login, the engine must not loop: it retries exactly once and returns whatever the
    # second attempt classifies as.
    class StuckFaultBank(FaultBank):
        def act(self, handle, action):
            result = super().act(handle, action)
            if handle.key == "Log In":
                self.session_expired = True  # stays broken even after a "successful" re-login
            return result

    fake = StuckFaultBank()
    fault = FaultInjection(step_index=0, fault_type="clear_session")
    result = engine(fake).replay(_capability(), {}, SECRETS, "http://h/parabank/index.htm", fault=fault)
    assert result.status == Outcome.RECOVERABLE
    assert result.business_outcome == "session_expired"
    assert fake.login_attempts == 2  # tried the recovery once, did not loop


def test_transient_fail_delays_the_named_request_but_does_not_change_the_outcome():
    fake = FaultBank()
    fault = FaultInjection(step_index=0, fault_type="transient_fail", url_pattern="**/confirm.htm", delay_ms=500)
    result = engine(fake).replay(_capability(), {}, SECRETS, "http://h/parabank/index.htm", fault=fault)
    assert result.status == Outcome.SUCCESS
    assert fake.delay_calls == [("**/confirm.htm", 500)]
    assert fake.clear_session_calls == 0


class ForwardOnExpiry:
    """Regression fixture for a real bug found live: ParaBank serves an unauthenticated request
    for a protected page via a server-side forward — the URL bar keeps reading the page that was
    asked for, only the rendered content changes to the login/error screen. FaultBank above
    can't exercise this: its url property and its content move together, so a URL-only check and
    a content-based check would look identical against it. This fixture deliberately keeps the
    URL fixed and only content changes, the same way the real app actually behaves."""

    def __init__(self):
        self.session_expired = False

    @property
    def url(self) -> str:
        return "http://h/parabank/overview.htm"  # always the page that was asked for

    def navigate(self, target: str) -> None:
        pass  # real behavior: the URL bar never moves, only session_expired changes what renders

    def observe(self) -> Observation:
        if self.session_expired:
            return Observation(self.url, b"", b"", [], [])  # forwarded: no real overview content
        texts = [TextNode(1, "heading", "Accounts Overview", named("heading", "Accounts Overview"))]
        return Observation(self.url, b"", b"", [], texts)

    def resolve(self, locator: Locator):
        cand = locator.chain[0]
        if not self.session_expired and cand.strategy == "role_name" and cand.value == "Accounts Overview":
            return Handle("heading")
        raise LocatorNotFound(locator.description)

    def exists(self, locator: Locator) -> bool:
        try:
            self.resolve(locator)
            return True
        except LocatorNotFound:
            return False


class MultiMatchBank:
    """Regression for a second real bug found on the same live run: two links sharing the same
    accessible name (two past $1 test transfers both showing "Funds Transfer Sent") must still
    count as "visible" for an existence check — resolve() correctly refuses to pick one of them
    for an action (the same exception either way), but exists() must not conflate "ambiguous"
    with "genuinely absent"."""

    def resolve(self, locator: Locator):
        raise LocatorNotFound(locator.description)  # ambiguous: refuses, same as "not found"

    def exists(self, locator: Locator) -> bool:
        return True  # two real, visible matches — just not resolvable to a single one


def test_resolves_treats_more_than_one_match_as_visible_not_absent():
    from cua.verify import resolves

    assert resolves(MultiMatchBank(), named("link", "Funds Transfer Sent")) is True


def test_probe_session_checks_real_content_not_just_the_url():
    fake = ForwardOnExpiry()
    replay_engine = engine(fake)
    assert replay_engine._probe_session() is True
    fake.session_expired = True
    assert replay_engine._probe_session() is False  # not fooled by the unchanged URL
