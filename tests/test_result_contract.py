"""The result contract: what a caller, an auditor or a human operator is told, and can rely on.

Found in review: a run that fails AFTER the money-moving step was dispatched used to look exactly like
one that failed before it, so a caller could "retry" a transfer that had already happened. Every result
now says what the side effect state is, which artifact produced it, and, for a ticket, when, where and who.
"""

import json
import re
from datetime import datetime
from decimal import Decimal

import pytest

from cua.config import Config
from cua.enums import Outcome
from cua.evidence import EvidenceLogger, new_run_id
from cua.models import FaultInjection
from cua.policy_gate import PolicyGate
from cua.redaction import Redactor
from cua.replay import ReplayEngine
from tests.test_dispatch_unverified import (
    ClickRaisesBank,
    DroppedConfirmationBank,
    NoTransferButtonBank,
)
from tests.test_replay import (
    BASE,
    FROM,
    PARAMS,
    SECRETS,
    FakeBank,
    InvalidSourceAccountBank,
    capability,
    loc,
)
from tests.test_replay_faults import FaultBank, _capability


def engine(fake, logger=None, *, threshold=1000, operator=None):
    config = Config(approval_threshold=Decimal(threshold), submit_confirmation_wait_ms=50)
    return ReplayEngine(fake, PolicyGate(config), logger, operator=operator)


def run(fake, cap=None, params=None, **kw):
    return engine(fake, **kw).replay(cap or capability(), params or PARAMS, SECRETS, BASE + "/index.htm")


def break_step(cap, i):
    cap.steps[i] = cap.steps[i].model_copy(update={"target": loc('link "Nonexistent"')})
    return cap


def logger_in(tmp_path):
    return EvidenceLogger(new_run_id(), Redactor(Config(), secrets=list(SECRETS.values())), base_dir=tmp_path)


# -- the retry contract: side_effects ---------------------------------------------------------------------


def test_a_confirmed_transfer_reports_its_side_effect_as_committed():
    result = run(FakeBank())
    assert result.status == Outcome.SUCCESS and result.side_effects == "committed"


def test_a_failure_before_the_money_moving_click_is_safe_to_retry():
    fake = FakeBank()
    result = run(fake, break_step(capability(), 1))  # the link that opens the transfer form
    assert result.status == Outcome.HARD_FAILURE
    assert result.side_effects == "none" and not fake.transferred


@pytest.mark.parametrize("step", [6, 7, 8])
def test_a_failure_after_the_transfer_was_confirmed_says_the_money_moved(step):
    # Step 5 is the Transfer click; 6, 7 and 8 only read the result. Failing there used to be
    # indistinguishable from failing at step 1, though the money had already moved.
    fake = FakeBank()
    result = run(fake, break_step(capability(), step))
    assert result.status == Outcome.HARD_FAILURE and result.failure_detail.step_index == step
    assert fake.transferred
    assert result.side_effects == "committed"


def test_a_lost_confirmation_reports_the_side_effect_as_unverified():
    result = run(DroppedConfirmationBank())
    assert result.business_outcome == "dispatch_unverified" and result.side_effects == "unverified"


def test_a_click_that_raises_after_dispatch_is_unverified():
    assert run(ClickRaisesBank()).side_effects == "unverified"


def test_a_button_that_cannot_be_found_means_nothing_was_dispatched():
    assert run(NoTransferButtonBank()).side_effects == "none"


@pytest.mark.parametrize(
    "label,amount,secrets,to",
    [
        ("over the balance", "9000", SECRETS, None),
        ("same account", "5", SECRETS, FROM),
        ("too many decimals", "1.98484", SECRETS, None),
        ("malformed amount", "abc", SECRETS, None),
        ("wrong password", "5", {"username": "x", "password": "y"}, None),
    ],
)
def test_every_refusal_before_dispatch_is_side_effect_free(label, amount, secrets, to):
    params = {**PARAMS, "amount": amount, **({"to_account": to} if to else {})}
    result = engine(FakeBank()).replay(capability(), params, secrets, BASE + "/index.htm")
    assert result.status != Outcome.SUCCESS, label
    assert result.side_effects == "none", label


def test_an_invalid_account_never_reaches_the_transfer():
    fake = InvalidSourceAccountBank()
    cap = capability()
    cap.steps[0] = cap.steps[0].model_copy(
        update={"wait_strategy": cap.steps[0].wait_strategy.model_copy(update={"timeout_ms": 200})}
    )
    result = run(fake, cap)
    assert result.business_outcome == "invalid_account" and result.side_effects == "none"


def test_over_the_threshold_with_no_one_to_approve_dispatches_nothing():
    result = run(FakeBank(), threshold=1)
    assert result.status == Outcome.POLICY_BLOCK and result.side_effects == "none"


def test_an_approval_backed_by_a_real_click_ends_committed():
    fake = FakeBank()

    def approve(ticket):
        fake.simulate_supervisor_click_transfer()
        return "approve"

    result = engine(fake, threshold=1).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", approve)
    assert result.status == Outcome.SUCCESS and result.side_effects == "committed"


def test_a_clean_reject_dispatches_nothing():
    result = engine(FakeBank(), threshold=1).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm", lambda t: "reject"
    )
    assert result.status == Outcome.POLICY_BLOCK and result.side_effects == "none"


def test_a_click_then_reject_is_committed_when_the_page_confirms_and_unverified_when_it_does_not():
    confirmed = FakeBank()

    def click_then_reject(ticket):
        confirmed.simulate_supervisor_click_transfer()
        return "reject"

    a = engine(confirmed, threshold=1).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", click_then_reject)
    assert a.status == Outcome.SUCCESS and a.side_effects == "committed"

    silent = FakeBank()

    def click_that_did_nothing(ticket):
        silent.pending_captures.append({"tag": "BUTTON", "text": "Transfer", "url": silent.url})
        return "reject"

    b = engine(silent, threshold=1).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm", click_that_did_nothing)
    assert b.business_outcome == "dispatch_unverified" and b.side_effects == "unverified"


def test_the_contract_is_part_of_the_serialized_result_a_caller_receives():
    dumped = run(FakeBank()).model_dump(mode="json")
    assert dumped["side_effects"] == "committed"
    assert set(dumped["capability"]) == {"name", "version", "created_from"}


# -- every result and trace names the artifact that produced it ----------------------------------------------


def test_result_and_trace_record_the_artifact_that_ran(tmp_path):
    logger = logger_in(tmp_path)
    cap = capability()
    result = engine(FakeBank(), logger).replay(cap, PARAMS, SECRETS, BASE + "/index.htm")
    expected = {"name": cap.name, "version": cap.version, "created_from": cap.created_from}
    assert result.capability.model_dump() == expected
    assert json.loads((logger.dir / "trace.json").read_text(encoding="utf-8"))["capability"] == expected
    assert json.loads((logger.dir / "result.json").read_text(encoding="utf-8"))["capability"] == expected
    assert expected["version"] == "2" and expected["created_from"]  # the committed artifact identifies its discovery run


def test_even_a_request_refused_up_front_names_the_artifact(tmp_path):
    logger = logger_in(tmp_path)
    engine(FakeBank(), logger).replay(capability(), {**PARAMS, "amount": "abc"}, SECRETS, BASE + "/index.htm")
    assert json.loads((logger.dir / "result.json").read_text(encoding="utf-8"))["capability"]["version"] == "2"


# -- a fault-injected run says so, and a retry never overwrites the evidence of what failed -----------------------


def _faulted_run(tmp_path, fault):
    logger = logger_in(tmp_path)
    config = Config()
    fake = FaultBank()
    result = ReplayEngine(fake, PolicyGate(config), logger).replay(
        _capability(), {}, SECRETS, "http://h/parabank/index.htm", fault=fault
    )
    return result, json.loads((logger.dir / "trace.json").read_text(encoding="utf-8")), logger.dir


def test_an_injected_fault_is_recorded_in_the_trace_as_operator_requested(tmp_path):
    result, trace, _ = _faulted_run(tmp_path, FaultInjection(step_index=0, fault_type="clear_session"))
    assert result.status == Outcome.SUCCESS
    injected = [s for s in trace["steps"] if s["result"] == "injected"]
    assert len(injected) == 1 and injected[0]["action"] == "fault"
    assert "operator-requested" in injected[0]["error"] and "clear_session" in injected[0]["error"]


def test_a_normal_run_records_no_fault(tmp_path):
    logger = logger_in(tmp_path)
    engine(FakeBank(), logger).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    trace = json.loads((logger.dir / "trace.json").read_text(encoding="utf-8"))
    assert not [s for s in trace["steps"] if s["result"] == "injected"]


def test_the_recovery_entry_no_longer_calls_an_induced_expiry_genuine(tmp_path):
    _, trace, _ = _faulted_run(tmp_path, FaultInjection(step_index=0, fault_type="clear_session"))
    recovered = [s for s in trace["steps"] if s["result"] == "recovered"]
    assert len(recovered) == 1
    assert "genuine" not in recovered[0]["error"] and "probe-confirmed" in recovered[0]["error"]


def test_a_retried_step_and_a_second_login_keep_their_own_screenshots(tmp_path):
    _, trace, folder = _faulted_run(tmp_path, FaultInjection(step_index=0, fault_type="clear_session"))
    refs = [s["screenshot"] for s in trace["steps"] if s.get("screenshot")]
    assert len(refs) == len(set(refs)), "two steps cite the same screenshot file"
    on_disk = {p.name for p in folder.glob("*.png")}
    assert set(refs) <= on_disk
    assert any("-retry" in r for r in refs)  # the second attempt got its own name
    assert "prelude-00-retry1.png" in on_disk  # the second login's first page did not overwrite the first


# -- a ticket stands on its own ---------------------------------------------------------------------------------


def _resolved_ticket(tmp_path, *, operator=None):
    logger = logger_in(tmp_path)
    fake = FakeBank()

    def approve(ticket):
        fake.simulate_supervisor_click_transfer()
        return "approve"

    result = engine(fake, logger, threshold=1, operator=operator).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm", approve
    )
    return result.escalations[0], logger.dir


def test_a_resolved_ticket_records_when_which_artifact_which_step_and_which_screenshot(tmp_path):
    ticket, folder = _resolved_ticket(tmp_path)
    opened, resolved = datetime.fromisoformat(ticket.opened_at), datetime.fromisoformat(ticket.resolved_at)
    assert opened.tzinfo is not None and resolved >= opened  # UTC timestamps, in order
    assert ticket.capability.version == "2" and ticket.capability.name == capability().name
    assert ticket.step_description == 'button "Transfer"'  # the step's own target: placeholders, never real values
    assert ticket.screenshot and (folder / ticket.screenshot).exists()
    on_disk = json.loads(next(folder.glob("ticket-*.json")).read_text(encoding="utf-8"))
    assert on_disk["opened_at"] == ticket.opened_at and on_disk["screenshot"] == ticket.screenshot


def test_the_operator_is_recorded_when_named_and_honestly_empty_when_not(tmp_path):
    unnamed, _ = _resolved_ticket(tmp_path / "a")
    named, _ = _resolved_ticket(tmp_path / "b", operator="ops-1")
    assert unnamed.operator is None
    assert named.operator == "ops-1"


def test_an_unverified_dispatch_ticket_is_open_with_an_opening_time_and_no_resolution(tmp_path):
    logger = logger_in(tmp_path)
    result = engine(DroppedConfirmationBank(), logger).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    ticket = result.escalations[0]
    assert ticket.status == "open" and ticket.opened_at and ticket.resolved_at is None
    assert ticket.step_description == 'button "Transfer"' and ticket.capability.version == "2"
    assert (logger.dir / ticket.screenshot).exists()
    fields = ticket.model_dump(mode="json")
    fields.pop("procedure")  # the operator-only text; everything else on the ticket must carry no real value
    assert not re.search(r"\b\d{5,12}\b", json.dumps(fields).replace(ticket.ticket_id, "").replace(ticket.run_id, ""))


# -- a known business outcome says where it happened, by parameter name and never by value ----------------------


def test_invalid_account_names_the_step_and_the_parameter_but_never_its_value():
    cap = capability()
    cap.steps[0] = cap.steps[0].model_copy(
        update={"wait_strategy": cap.steps[0].wait_strategy.model_copy(update={"timeout_ms": 200})}
    )
    result = run(InvalidSourceAccountBank(), cap)
    detail = result.failure_detail
    assert result.business_outcome == "invalid_account" and detail.step_index == 0
    assert "{{from_account}}" in detail.expected and "invalid_account" in detail.observed
    assert FROM not in detail.expected + detail.observed


def test_a_rejected_login_says_it_happened_in_the_login_prelude():
    result = engine(FakeBank()).replay(
        capability(), PARAMS, {"username": "x", "password": "y"}, BASE + "/index.htm"
    )
    assert result.business_outcome == "login_rejected"
    assert result.failure_detail.step_index == -1 and "could not be verified" in result.failure_detail.observed
    assert result.side_effects == "none"
