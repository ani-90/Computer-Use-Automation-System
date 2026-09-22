"""Artifact Compiler: a successful trace in, a parameterized Capability out.

Uses a small synthetic trace shaped like the real recording (login, a duplicate extract, a
literal confirmation sentence embedding tagged values) rather than a saved evidence file: a
frozen trace is redacted before it hits disk, so real values only ever exist in memory, and the
compiler must be developed against fixtures for exactly that reason.
"""

from pathlib import Path

import pytest

from cua.compiler import CompileError, compile_capability
from cua.enums import StopReason
from cua.goal import GoalSpec
from cua.models import Condition, Locator, LocatorCandidate
from cua.trace import DiscoveryResult, TraceStep

SPEC_PATH = Path(__file__).resolve().parent.parent / "goals" / "transfer_funds.json"
FROM, TO, AMOUNT = "111111", "222222", "11"
SENTENCE = f"${AMOUNT}.00 has been transferred from account #{FROM} to account #{TO}."


def spec() -> GoalSpec:
    return GoalSpec.model_validate_json(SPEC_PATH.read_text(encoding="utf-8"))


def heading(text: str) -> Locator:
    return Locator(
        description=f'heading "{text}"',
        chain=[LocatorCandidate(strategy="role_name", role="heading", value=text)],
    )


def named(role: str, text: str) -> Locator:
    return Locator(
        description=f'{role} "{text}"',
        chain=[LocatorCandidate(strategy="role_name", role=role, value=text)],
    )


def text_loc(text: str) -> Locator:
    return Locator(description=f'text "{text}"', chain=[LocatorCandidate(strategy="text", value=text)])


def cell_col(anchor: str, column: str) -> Locator:
    return Locator(
        description=f'cell[row "{anchor}", "{column}"]',
        chain=[LocatorCandidate(strategy="table_cell", value=anchor, column=column)],
    )


def cell_pos(anchor: str, col: int) -> Locator:
    return Locator(
        description=f'cell[row "{anchor}", col {col}]',
        chain=[LocatorCandidate(strategy="table_cell", value=anchor, col=col)],
    )


def ts(n: int, **kw) -> TraceStep:
    base = {"step_no": n, "result": "ok", "checkpoint": []}
    base.update(kw)
    return TraceStep(**base)


def build_trace() -> list[TraceStep]:
    overview_ok = [
        Condition(kind="url_matches", value="/parabank/overview.htm"),
        Condition(kind="element_visible", target=heading("Accounts Overview")),
    ]
    transfer_ok = [
        Condition(kind="url_matches", value="/parabank/transfer.htm"),
        Condition(kind="element_visible", target=heading("Transfer Funds")),
    ]
    return [
        # --- login prelude: dropped from the artifact, but step 3's checkpoint seeds
        # step 1's precondition (the prelude's verified end-state).
        ts(1, tool="type", provenance="secret", target=named("textbox", "Username")),
        ts(2, tool="type", provenance="secret", target=named("textbox", "Password")),
        ts(3, tool="click", target=named("button", "Log In"), checkpoint=overview_ok),
        # --- balance read: policy-only, excluded from the outputs contract
        ts(
            4, tool="extract", extract_name="source_balance_before",
            target=cell_col(FROM, "Balance*"), value="$1051.00",
            checkpoint=[Condition(kind="shape_matches", target=cell_col(FROM, "Balance*"), value="money")],
        ),
        ts(5, tool="click", target=named("link", "Transfer Funds"), checkpoint=transfer_ok),
        # --- the transfer form
        ts(
            6, tool="type", provenance="parameter", param="amount", value=AMOUNT,
            target=named("textbox", "Amount: $"),
            checkpoint=[Condition(
                kind="field_value_equals", target=named("textbox", "Amount: $"), value="{{amount}}",
            )],
        ),
        ts(
            7, tool="select", provenance="parameter", param="from_account", value=FROM,
            target=named("combobox", "From account #"),
            checkpoint=[Condition(
                kind="option_selected", target=named("combobox", "From account #"), value="{{from_account}}",
            )],
        ),
        # a blocked attempt (the guard, before the destination was set): must be dropped entirely
        ts(8, tool="click", target=named("button", "Transfer"), result="blocked", error="to_account missing"),
        ts(
            9, tool="select", provenance="parameter", param="to_account", value=TO,
            target=named("combobox", "to account #"),
            checkpoint=[Condition(
                kind="option_selected", target=named("combobox", "to account #"), value="{{to_account}}",
            )],
        ),
        ts(
            10, tool="click", target=named("button", "Transfer"),
            checkpoint=[
                Condition(kind="element_visible", target=heading("Transfer Complete!")),
                Condition(kind="element_visible", target=text_loc(SENTENCE)),
            ],
        ),
        ts(
            11, tool="extract", extract_name="confirmation_text", target=text_loc(SENTENCE), value=SENTENCE,
            checkpoint=[Condition(kind="shape_matches", target=text_loc(SENTENCE), value="nonempty")],
        ),
        ts(12, tool="click", target=named("link", "Accounts Overview"), checkpoint=overview_ok),
        # --- non-consecutive duplicate extract: an unrelated extract sits between the two reads
        ts(
            13, tool="extract", extract_name="new_balance", target=cell_col(FROM, "Balance*"), value="$1040.00",
            checkpoint=[Condition(kind="shape_matches", target=cell_col(FROM, "Balance*"), value="money")],
        ),
        ts(
            14, tool="extract", extract_name="note", target=text_loc("Total"), value="unrelated",
            checkpoint=[Condition(kind="shape_matches", target=text_loc("Total"), value="nonempty")],
        ),
        ts(
            15, tool="extract", extract_name="new_balance", target=cell_col(FROM, "Balance*"), value="$1040.00",
            checkpoint=[Condition(kind="shape_matches", target=cell_col(FROM, "Balance*"), value="money")],
        ),
        ts(
            16, tool="extract", extract_name="transaction_id",
            target=cell_pos("Transaction ID:", 1), value="998877",
            checkpoint=[Condition(kind="shape_matches", target=cell_pos("Transaction ID:", 1), value="nonempty")],
        ),
        ts(17, tool="report_done"),
    ]


def compiled(stop_reason=StopReason.SUCCESS):
    steps = build_trace()
    result = DiscoveryResult(run_id="00000000-0000-0000-0000-000000000000", stop_reason=stop_reason, steps=steps)
    return compile_capability(result, spec())


def test_only_a_success_can_be_compiled():
    steps = build_trace()
    result = DiscoveryResult(run_id="x", stop_reason=StopReason.DEAD_END, steps=steps)
    with pytest.raises(CompileError):
        compile_capability(result, spec())


def test_login_and_blocked_steps_never_reach_the_artifact():
    cap = compiled()
    descriptions = " ".join(s.target.description for s in cap.steps)
    assert "Username" not in descriptions and "Password" not in descriptions and "Log In" not in descriptions
    assert all(s.action != "click" or s.target.description != 'button "Transfer"' or True for s in cap.steps)
    # exactly one "click Transfer" survives (the allowed one) — the blocked attempt is gone
    transfer_clicks = [s for s in cap.steps if s.action == "click" and s.target.description == 'button "Transfer"']
    assert len(transfer_clicks) == 1


def test_duplicate_extract_keeps_only_the_last_even_when_not_consecutive():
    cap = compiled()
    balance_reads = [s for s in cap.steps if s.action == "extract" and "Balance*" in s.target.description]
    # source_balance_before (once) + new_balance (two recorded reads, deduped to the last one)
    assert len(balance_reads) == 2


def test_no_tagged_literal_survives_compilation():
    cap = compiled()
    dumped = cap.model_dump_json()
    assert FROM not in dumped
    assert TO not in dumped
    assert f'"{AMOUNT}"' not in dumped  # the bare typed amount never appears as a literal
    assert "{{from_account}}" in dumped
    assert "{{to_account}}" in dumped
    assert "{{amount}}" in dumped


def _submit(cap):
    return next(s for s in cap.steps if s.action == "click" and s.target.description == 'button "Transfer"')


def test_the_confirmation_sentence_is_parameterized_as_a_substring():
    cap = compiled()
    submit = _submit(cap)
    sentence_cond = next(c for c in submit.checkpoint if "has been transferred" in (c.target.description or ""))
    assert FROM not in sentence_cond.target.description
    assert TO not in sentence_cond.target.description
    assert "{{from_account}}" in sentence_cond.target.description
    assert "{{to_account}}" in sentence_cond.target.description
    assert "{{amount}}" in sentence_cond.target.description
    # exact: the ".00" ParaBank itself appends must survive the substitution untouched
    assert sentence_cond.target.description == (
        'text "${{amount}}.00 has been transferred from account #{{from_account}} '
        'to account #{{to_account}}."'
    )


def test_a_parameter_typed_in_two_different_literal_forms_does_not_swallow_page_formatting():
    # Regression: the real agent typed "11" on the Transfer page but "11.00" on the Find
    # Transactions page for the same amount (both correctly tagged, Decimal("11")==Decimal
    # ("11.00")). Substituting the longer "11.00" variant against ParaBank's OWN "$11.00"
    # elsewhere ate the ".00" along with it, breaking the compiled locator for every other
    # amount at replay time. Only the shortest variant per parameter may be used.
    trace = build_trace()
    second_typing = ts(
        18, tool="type", provenance="parameter", param="amount", value=f"{AMOUNT}.00",
        target=named("textbox", "Find by Amount:"),
        checkpoint=[Condition(
            kind="field_value_equals", target=named("textbox", "Find by Amount:"), value="{{amount}}",
        )],
    )
    trace.insert(-1, second_typing)  # anywhere before report_done; position doesn't matter here
    result = DiscoveryResult(run_id="y", stop_reason=StopReason.SUCCESS, steps=trace)
    cap = compile_capability(result, spec())
    submit = _submit(cap)
    sentence_cond = next(c for c in submit.checkpoint if "has been transferred" in (c.target.description or ""))
    assert ".00 has been transferred" in sentence_cond.target.description
    assert AMOUNT not in sentence_cond.target.description.replace("{{amount}}", "")


def test_checkpoint_keeps_every_verified_condition_all_of():
    cap = compiled()
    submit = _submit(cap)
    assert len(submit.checkpoint) == 2  # both the heading and the sentence, not just one picked


def test_first_step_precondition_is_the_prelude_end_state():
    cap = compiled()
    first = cap.steps[0]
    assert any(c.kind == "url_matches" and c.value == "/parabank/overview.htm" for c in first.precondition)


def test_submit_precondition_unions_the_fields_entered_since_the_last_navigation():
    cap = compiled()
    submit = _submit(cap)
    kinds = {(c.kind, c.value) for c in submit.precondition}
    assert ("field_value_equals", "{{amount}}") in kinds
    assert ("option_selected", "{{from_account}}") in kinds
    assert ("option_selected", "{{to_account}}") in kinds


def test_field_state_does_not_leak_past_an_in_page_submit_with_no_url_change():
    # Regression: the real Transfer submission is an in-page AJAX swap — no URL change — that
    # hides the very fields since_nav was tracking (Amount, From, To). Without a reset here, the
    # NEXT step's precondition would wrongly assert those fields still hold, and fail at replay
    # time since they're gone from the page the moment the confirmation shows.
    cap = compiled()
    submit = _submit(cap)
    after_submit = cap.steps[cap.steps.index(submit) + 1]
    assert after_submit.action == "extract" and after_submit.extract_as == "confirmation_text"
    kinds = {c.kind for c in after_submit.precondition}
    assert "field_value_equals" not in kinds
    assert "option_selected" not in kinds


def test_select_steps_get_an_option_present_wait_strategy():
    cap = compiled()
    select_steps = [s for s in cap.steps if s.action == "select"]
    assert select_steps and all(s.wait_strategy.kind == "option_present" for s in select_steps)


def test_error_mapping_is_always_empty_in_this_phase():
    cap = compiled()
    assert all(s.error_mapping == [] for s in cap.steps)


def test_outputs_contract_excludes_policy_only_extracts():
    cap = compiled()
    assert "source_balance_before" not in cap.outputs
    assert set(cap.outputs) == {"confirmation_text", "new_balance", "transaction_id"}


def test_inputs_contract_matches_the_goal_spec_and_excludes_credentials():
    cap = compiled()
    assert set(cap.inputs) == {"from_account", "to_account", "amount"}


def test_distinct_inputs_is_carried_from_the_goal_spec():
    cap = compiled()
    assert cap.distinct_inputs == [["from_account", "to_account"]]
