"""Artifact Compiler: a successful trace in, a parameterized Capability out.

Uses a small synthetic trace shaped like the real recording (login, a duplicate extract, a
literal confirmation sentence embedding tagged values) rather than a saved evidence file: a
frozen trace is redacted before it hits disk, so real values only ever exist in memory, and the
compiler must be developed against fixtures for exactly that reason.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from cua.compiler import CompileError, _finalize_transaction_lookup, compile_capability
from cua.enums import Outcome, StopReason
from cua.goal import GoalSpec
from cua.models import Condition, Locator, LocatorCandidate, Step, WaitStrategy
from cua.money import canonical
from cua.trace import DiscoveryResult, TraceStep

SPEC_PATH = Path(__file__).resolve().parent.parent / "goals" / "transfer_funds.json"
FROM, TO, AMOUNT = "111111", "222222", "11"
def sentence_for(amount: str) -> str:
    """What the app prints back: the amount in canonical two-decimal money form."""
    return f"${canonical(Decimal(amount))} has been transferred from account #{FROM} to account #{TO}."



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


def build_trace(amount: str = AMOUNT) -> list[TraceStep]:
    sentence = sentence_for(amount)
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
            6, tool="type", provenance="parameter", param="amount", value=amount,
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
                Condition(kind="element_visible", target=text_loc(sentence)),
            ],
        ),
        ts(
            11, tool="extract", extract_name="confirmation_text", target=text_loc(sentence), value=sentence,
            checkpoint=[Condition(kind="shape_matches", target=text_loc(sentence), value="nonempty")],
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


def compiled(stop_reason=StopReason.SUCCESS, amount: str = AMOUNT):
    steps = build_trace(amount)
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
    assert "{{amount:money}}" in dumped
    assert "{{amount}}" not in dumped  # a money parameter is only ever written in its money form
    assert ".00 has been" not in dumped  # and the app's own ".00" is never left behind as a suffix


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
    assert "{{amount:money}}" in sentence_cond.target.description
    # exact: the whole money token, ".00" included, is one placeholder — nothing hardcoded around it
    assert sentence_cond.target.description == (
        'text "${{amount:money}} has been transferred from account #{{from_account}} '
        'to account #{{to_account}}."'
    )


def test_a_parameter_typed_in_two_different_literal_forms_still_yields_one_money_placeholder():
    # Regression: the real agent typed "11" on the Transfer page but "11.00" on the Find
    # Transactions page for the same amount (both correctly tagged, Decimal("11")==Decimal
    # ("11.00")). Substituting a longer variant against the app's OWN "$11.00" used to eat the
    # ".00" along with it. The page's money token is now tagged whole, whichever forms were typed.
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
    assert "${{amount:money}} has been transferred" in sentence_cond.target.description
    assert AMOUNT not in sentence_cond.target.description.replace("{{amount:money}}", "")


def test_only_the_first_submission_click_is_marked_not_a_later_search_reusing_the_amount():
    # Regression: a later, read-only click (e.g. the Find Transactions search button) also
    # follows the amount being typed again — it must NOT also be marked is_submission, or
    # escalation (Phase 7) would wrongly gate a harmless search the same as the real transfer.
    trace = build_trace()
    retyped = ts(
        18, tool="type", provenance="parameter", param="amount", value=AMOUNT,
        target=named("textbox", "Find by Amount:"),
        checkpoint=[Condition(
            kind="field_value_equals", target=named("textbox", "Find by Amount:"), value="{{amount}}",
        )],
    )
    search_click = ts(19, tool="click", target=named("button", "Find Transactions"), checkpoint=[
        Condition(kind="element_visible", target=heading("Transaction Results")),
    ])
    trace.insert(-1, search_click)
    trace.insert(-2, retyped)
    result = DiscoveryResult(run_id="z", stop_reason=StopReason.SUCCESS, steps=trace)
    cap = compile_capability(result, spec())
    marked = [s for s in cap.steps if s.is_submission]
    assert len(marked) == 1
    assert marked[0].target.description == 'button "Transfer"'


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
    assert ("field_value_equals", "{{amount:money}}") in kinds
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


def test_error_mapping_maps_a_missing_dropdown_option_to_invalid_account():
    # Phase 6: a select step's option_present wait timing out is exactly how a nonexistent
    # *destination* account shows up on this app (a dropdown, not free text — recon confirmed
    # there is no error page for this).
    cap = compiled()
    select_steps = [s for s in cap.steps if s.action == "select"]
    assert select_steps and all(s.error_mapping for s in select_steps)
    for s in select_steps:
        assert len(s.error_mapping) == 1
        mapping = s.error_mapping[0]
        assert mapping.outcome == Outcome.BUSINESS_OUTCOME
        assert mapping.detail == "invalid_account"
        assert mapping.when.kind == "option_present"
        assert mapping.when.target == s.wait_strategy.target
        assert mapping.when.value == s.wait_strategy.value


def test_error_mapping_also_maps_a_missing_source_account_balance_row_to_invalid_account():
    # Phase 8: a *source* account that doesn't exist shows up differently — not a dropdown, a
    # table lookup (its own balance row) that never appears. Same business fact, a different
    # signal: any table_cell lookup still keyed by an unsubstituted {{placeholder}} gets the
    # same mapping.
    cap = compiled()
    lookup_steps = [
        s for s in cap.steps
        if s.action == "extract"
        and any(c.strategy == "table_cell" and c.value == "{{from_account}}" for c in s.target.chain)
    ]
    assert lookup_steps  # the fixture trace has at least one (source_balance_before/new_balance)
    for s in lookup_steps:
        assert len(s.error_mapping) == 1
        mapping = s.error_mapping[0]
        assert mapping.outcome == Outcome.BUSINESS_OUTCOME
        assert mapping.detail == "invalid_account"
        assert mapping.when.kind == "element_visible"
        assert mapping.when.target == s.wait_strategy.target


def test_error_mapping_is_still_empty_for_everything_that_is_neither_pattern():
    # The transaction_id lookup is keyed by a literal ("Transaction ID:"), not a parameter — it
    # must not get this mapping, since a missing transaction ID row means something else entirely.
    cap = compiled()
    other_steps = [
        s for s in cap.steps
        if s.action != "select"
        and not any(c.strategy == "table_cell" and c.value == "{{from_account}}" for c in s.target.chain)
    ]
    assert other_steps
    assert all(s.error_mapping == [] for s in other_steps)


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


def _bare_step(action: str, extract_as=None) -> Step:
    target = named(action if action != "click" else "link", "x")
    return Step(
        precondition=[], action=action, target=target, parameters={},
        wait_strategy=WaitStrategy(kind="network_idle"), checkpoint=[], error_mapping=[],
        extract_as=extract_as,
    )


def test_everything_after_new_balance_is_marked_best_effort_and_earlier_steps_are_not():
    steps = [
        _bare_step("click"), _bare_step("extract", extract_as="new_balance"),
        _bare_step("click"), _bare_step("click"), _bare_step("extract", extract_as="transaction_id"),
    ]
    out = _finalize_transaction_lookup(steps)
    assert [s.best_effort for s in out] == [False, False, True, True, True]


def test_the_step_that_opens_the_match_gets_nth_minus_one_others_do_not():
    # Only the step immediately before the transaction_id extract is "opening the match" — the
    # newest-row rule (recon-notes.md: results list oldest-first) applies there, not to every
    # best-effort step in the tail.
    steps = [
        _bare_step("extract", extract_as="new_balance"),
        _bare_step("click"),  # e.g. "Find Transactions" — not the one opening the match
        _bare_step("click"),  # e.g. "Funds Transfer Sent" — this one is
        _bare_step("extract", extract_as="transaction_id"),
    ]
    out = _finalize_transaction_lookup(steps)
    assert all(c.nth is None for c in out[1].target.chain)
    assert all(c.nth == -1 for c in out[2].target.chain)


def test_a_trace_with_no_new_balance_extract_is_left_untouched():
    steps = [_bare_step("click"), _bare_step("extract", extract_as="transaction_id")]
    out = _finalize_transaction_lookup(steps)
    assert out == steps
    assert all(not s.best_effort for s in out)


# --- money: the amount is never hardcoded, whatever its shape ---------------------------------------

@pytest.mark.parametrize("amount", ["11", "1.5", "1.50", "3.3", "1.44", "100", "0.05"])
def test_any_discovery_amount_compiles_to_one_money_placeholder_and_no_literal(amount):
    cap = compiled(amount=amount)
    dumped = cap.model_dump_json()
    sentence_cond = next(
        c for c in _submit(cap).checkpoint if "has been transferred" in (c.target.description or "")
    )
    assert sentence_cond.target.description == (
        'text "${{amount:money}} has been transferred from account #{{from_account}} '
        'to account #{{to_account}}."'
    )
    assert "{{amount}}" not in dumped
    # the root of the decimal bug: a placeholder with the app's own ".00" bolted on after it
    assert not re.search(r"\{\{amount(:money)?\}\}\.00", dumped)
    assert f"${canonical(Decimal(amount))} " not in dumped  # the printed amount is never a literal
    assert FROM not in dumped and TO not in dumped


def test_the_compiled_artifact_records_the_discovery_run_it_came_from():
    steps = build_trace()
    run_id = "5b9d178d-0a05-4884-bbf7-fd02cf79b2aa"
    cap = compile_capability(DiscoveryResult(run_id=run_id, stop_reason=StopReason.SUCCESS, steps=steps), spec())
    assert cap.created_from == run_id
    assert cap.version == "2"  # 2 = money parameters are {{param:money}}


# --- a dropped duplicate read must not replace the surviving step's real precondition ------------------


def _with_consecutive_duplicate_balance_read():
    """The real agent read the source balance twice in a row after logging in (same value, same
    page). The compiler keeps only the last read."""
    trace = build_trace()
    first_read = next(s for s in trace if s.step_no == 4)
    duplicate = first_read.model_copy(update={"step_no": 5})
    trace = [s for s in trace if s.step_no <= 4] + [duplicate] + [
        s.model_copy(update={"step_no": s.step_no + 1}) for s in trace if s.step_no > 4
    ]
    return DiscoveryResult(run_id="dup", stop_reason=StopReason.SUCCESS, steps=trace)


def test_a_dropped_duplicate_read_does_not_replace_the_first_steps_login_precondition():
    cap = compile_capability(_with_consecutive_duplicate_balance_read(), spec())
    first = cap.steps[0]
    assert first.extract_as == "policy_balance"
    kinds = {c.kind for c in first.precondition}
    # the state after login is what step 0 must start from...
    assert {"url_matches", "element_visible"} <= kinds
    # ...and it must NOT demand that the very cell it is about to read already exists: that check
    # would fire before the wait, so a missing (invalid) account could never reach its error mapping
    assert "shape_matches" not in kinds


def test_the_precondition_is_the_same_whether_the_balance_was_read_once_or_twice():
    once = compile_capability(DiscoveryResult(run_id="once", stop_reason=StopReason.SUCCESS, steps=build_trace()), spec())
    twice = compile_capability(_with_consecutive_duplicate_balance_read(), spec())
    assert [c.model_dump() for c in once.steps[0].precondition] == [c.model_dump() for c in twice.steps[0].precondition]
    assert len(once.steps) == len(twice.steps)


# --- the login state seeds step 0 with page structure, never a customer's own content ------------------


def _login_leaves_a_personal_greeting():
    trace = build_trace()
    login_click = next(s for s in trace if s.step_no == 3)
    greeting = Condition(kind="element_visible", target=Locator(
        description='paragraph "Welcome Some Person"',
        chain=[LocatorCandidate(strategy="text", value="Welcome Some Person")],
    ))
    patched = login_click.model_copy(update={"checkpoint": login_click.checkpoint + [greeting]})
    return DiscoveryResult(
        run_id="greet", stop_reason=StopReason.SUCCESS,
        steps=[patched if s.step_no == 3 else s for s in trace],
    )


def test_a_customers_greeting_never_becomes_part_of_the_first_steps_precondition():
    cap = compile_capability(_login_leaves_a_personal_greeting(), spec())
    dumped = cap.model_dump_json()
    assert "Welcome" not in dumped and "Some Person" not in dumped
    kinds = [c.kind for c in cap.steps[0].precondition]
    assert "url_matches" in kinds and kinds.count("element_visible") >= 1  # the page is still identified


def test_the_greeting_rule_does_not_change_an_artifact_that_had_no_greeting():
    plain = compile_capability(DiscoveryResult(run_id="p", stop_reason=StopReason.SUCCESS, steps=build_trace()), spec())
    greeted = compile_capability(_login_leaves_a_personal_greeting(), spec())
    assert plain.model_dump() | {"created_from": None} == greeted.model_dump() | {"created_from": None}


def test_a_paragraph_checked_after_the_transfer_is_still_checked():
    # Only login's end state is filtered. The real confirmation sentence is a paragraph too, and it
    # must remain a checkpoint of the step that moves the money.
    trace = build_trace()
    submit_click = next(s for s in trace if s.step_no == 10)
    as_paragraph = [
        c.model_copy(update={"target": Locator(
            description=c.target.description.replace("text ", "paragraph ", 1), chain=c.target.chain,
        )}) if "has been transferred" in (c.target.description or "") else c
        for c in submit_click.checkpoint
    ]
    patched = [s.model_copy(update={"checkpoint": as_paragraph}) if s.step_no == 10 else s for s in trace]
    cap = compile_capability(DiscoveryResult(run_id="para", stop_reason=StopReason.SUCCESS, steps=patched), spec())
    kept = [c for c in _submit(cap).checkpoint if "has been transferred" in (c.target.description or "")]
    assert len(kept) == 1 and kept[0].target.description.startswith("paragraph ")
