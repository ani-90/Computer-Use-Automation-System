"""The primary escalation trigger: a supervisor approving (or rejecting) an over-threshold
amount, live on the same paused session — not a fresh one, not a CLI-only decision gate.
"""

import json
from decimal import Decimal

from cua.config import Config
from cua.enums import Outcome, SessionOwner
from cua.evidence import EvidenceLogger, new_run_id
from cua.policy_gate import PolicyGate
from cua.redaction import Redactor
from cua.replay import ReplayEngine
from tests.test_replay import BASE, PARAMS, SECRETS, FakeBank, capability


def engine_over_threshold(fake, logger=None):
    return ReplayEngine(fake, PolicyGate(Config(approval_threshold=Decimal(1))), logger)


def test_fires_from_a_normal_replay_call_no_fault_injection_involved():
    # amount=20 (PARAMS) against approval_threshold=1 — an entirely ordinary call.
    fake = FakeBank()
    seen = []

    def on_escalate(ticket):
        seen.append(ticket)
        fake.simulate_supervisor_click_transfer()
        return "approve"

    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert len(seen) == 1
    assert result.status == Outcome.SUCCESS


def test_approve_is_the_supervisors_own_click_not_the_engine_clicking_again():
    fake = FakeBank()

    def on_escalate(ticket):
        assert fake.owner == "human"  # the session was actually handed over before the callback runs
        fake.simulate_supervisor_click_transfer()
        return "approve"

    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert result.status == Outcome.SUCCESS
    assert result.outputs.new_balance == "$1028.00"
    assert result.outputs.transaction_id == "998877"
    assert fake.owner == "agent"  # handed back after the decision


def test_reject_never_dispatches_transfer_and_returns_policy_block():
    fake = FakeBank()

    def on_escalate(ticket):
        return "reject"  # the supervisor never clicks

    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert result.status == Outcome.POLICY_BLOCK
    assert not fake.transferred
    assert len(result.escalations) == 1
    assert result.escalations[0].decision == "reject"
    assert result.escalations[0].captured_human_actions == []


def test_a_claimed_approval_with_no_captured_click_is_never_trusted():
    fake = FakeBank()

    def on_escalate(ticket):
        return "approve"  # says approve, but never actually clicks anything

    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert result.status == Outcome.POLICY_BLOCK
    assert not fake.transferred
    assert result.escalations[0].decision == "reject"


def test_approve_populates_captured_human_actions_reject_leaves_it_empty():
    fake = FakeBank()
    result = engine_over_threshold(fake).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm",
        lambda t: (fake.simulate_supervisor_click_transfer(), "approve")[1],
    )
    assert result.escalations[0].captured_human_actions != []
    assert result.escalations[0].captured_human_actions[0]["text"] == "Transfer"


def test_no_callback_falls_back_to_todays_block_unattended_default():
    fake = FakeBank()
    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.POLICY_BLOCK
    assert result.escalations == []  # no ticket at all when nobody is there to see it


def test_the_ticket_is_a_real_json_file_moving_from_open_to_resolved(tmp_path):
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    fake = FakeBank()
    seen_mid_flight = {}

    def on_escalate(ticket):
        # the ticket file must already exist and be "open" the instant the trigger fires,
        # before any decision has been given — not just held in a Python variable.
        files = list(logger.dir.glob("ticket-*.json"))
        assert len(files) == 1
        on_disk = json.loads(files[0].read_text(encoding="utf-8"))
        assert on_disk["status"] == "open"
        seen_mid_flight["path"] = files[0]
        fake.simulate_supervisor_click_transfer()
        return "approve"

    result = engine_over_threshold(fake, logger).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate
    )
    assert result.status == Outcome.SUCCESS
    resolved = json.loads(seen_mid_flight["path"].read_text(encoding="utf-8"))
    assert resolved["status"] == "resolved"
    assert resolved["decision"] == "approve"


def test_session_owner_is_a_real_tracked_state_not_just_a_pause():
    fake = FakeBank()
    engine = engine_over_threshold(fake)
    assert engine.session_owner == SessionOwner.AGENT

    def on_escalate(ticket):
        assert engine.session_owner == SessionOwner.HUMAN
        fake.simulate_supervisor_click_transfer()
        return "approve"

    engine.replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert engine.session_owner == SessionOwner.AGENT


def test_a_real_transfer_click_followed_by_a_typed_reject_is_never_reported_as_blocked():
    # Regression: a supervisor could click Transfer for real, then type "reject" by mistake.
    # The typed word must never be trusted over what was actually captured — reporting
    # POLICY_BLOCK ("Transfer was never dispatched") here would directly contradict reality.
    fake = FakeBank()

    def on_escalate(ticket):
        fake.simulate_supervisor_click_transfer()  # a genuine click really happens
        return "reject"  # but the supervisor types reject anyway

    result = engine_over_threshold(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate)
    assert result.status == Outcome.HARD_FAILURE  # not POLICY_BLOCK — that would be a lie
    assert result.status != Outcome.SUCCESS  # nor silently trusted as approved
    assert "cannot be trusted" in result.failure_detail.observed
    assert result.escalations[0].decision == "reject"
    assert result.escalations[0].captured_human_actions != []  # the real click is still on record


def test_a_clean_reject_with_no_matching_click_is_still_a_plain_policy_block():
    # The new check must not change the ordinary, unambiguous reject path.
    fake = FakeBank()
    result = engine_over_threshold(fake).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm", lambda t: "reject"
    )
    assert result.status == Outcome.POLICY_BLOCK
    assert result.failure_detail.observed == "rejected by the supervisor"
