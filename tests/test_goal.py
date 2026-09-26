import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.goal import GoalSpec, PolicyBlockError, TaggedValues, parse_params

SPEC_PATH = Path(__file__).resolve().parent.parent / "goals" / "transfer_funds.json"
PARAMS = {"from_account": "acct-a", "to_account": "acct-b", "amount": "5"}
SECRETS = {"username": "svc-user", "password": "svc-pass"}


def raw() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def load() -> GoalSpec:
    return GoalSpec.model_validate(raw())


def test_real_spec_loads_and_holds_no_account_numbers_or_amounts():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert load().name == "transfer_funds"
    assert not re.search(r"\d{5,}", text)


def test_goal_text_fills_values_and_names_no_pages():
    text = load().goal_text(PARAMS)
    assert "acct-a" in text and "acct-b" in text
    for word in ("htm", "parabank", "http", "admin", "#"):
        assert word not in text.lower()


def test_start_url_and_secrets_come_from_the_environment():
    spec = load()
    assert spec.start_url({"PARABANK_BASE_URL": "http://h/p/"}) == "http://h/p/index.htm"
    env = {"PARABANK_USERNAME": "svc-user", "PARABANK_PASSWORD": "svc-pass"}
    assert spec.secrets(env) == SECRETS


def test_spec_rejects_inconsistent_definitions():
    bad_template = raw() | {"goal_template": "Do {nothing_declared}"}
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(bad_template)
    bad_shape = raw()
    bad_shape["extracts"]["new_balance"]["shape"] = "no-such-shape"
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(bad_shape)
    not_an_output = raw()
    not_an_output["done_requires"] = {"source_balance_before": ["x"]}  # a policy read
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(not_an_output)


def test_parse_params_validates_names_and_values():
    spec = load()
    assert parse_params(spec, {**PARAMS, "amount": " 5 "})["amount"] == "5.00"  # canonical money form
    with pytest.raises(ValueError):
        parse_params(spec, {"from_account": "a"})  # missing
    with pytest.raises(ValueError):
        parse_params(spec, {**PARAMS, "extra": "x"})  # unknown
    with pytest.raises(ValueError):
        parse_params(spec, {**PARAMS, "to_account": " "})  # empty
    for junk in ("lots", "NaN", "Infinity", "1e2", "1,5"):
        with pytest.raises(ValueError, match="not a number"):
            parse_params(spec, {**PARAMS, "amount": junk})  # never reaches a comparison that could crash


def test_an_amount_is_written_in_canonical_money_form_and_more_than_two_decimals_is_a_policy_block():
    spec = load()
    for given, expected in [("1.5", "1.50"), ("3", "3.00"), ("1.45", "1.45"), ("5.00", "5.00"), ("1.500", "1.50")]:
        assert parse_params(spec, {**PARAMS, "amount": given})["amount"] == expected
    for bad in ("1.98484", "1.984", "0.001"):
        with pytest.raises(PolicyBlockError, match="at most 2 decimal places"):
            parse_params(spec, {**PARAMS, "amount": bad})


def test_the_two_accounts_must_differ_before_anything_is_dispatched():
    spec = load()
    assert spec.distinct_inputs == [["from_account", "to_account"]]
    with pytest.raises(PolicyBlockError, match="from_account and to_account must be different"):
        parse_params(spec, {**PARAMS, "to_account": PARAMS["from_account"]})
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(raw() | {"distinct_inputs": [["from_account", "nope"]]})


def test_classify_labels_values_by_where_they_came_from():
    tagged = TaggedValues(PARAMS, {"amount": "decimal"}, SECRETS)
    assert tagged.classify("acct-a") == ("parameter", "from_account")
    assert tagged.classify("5.00") == ("parameter", "amount")  # decimals match numerically
    assert tagged.classify("svc-pass") == ("secret", "password")
    assert tagged.classify("something else") == ("other", None)


def test_a_secret_is_never_labelled_a_parameter():
    tagged = TaggedValues({"amount": "shared"}, {"amount": "string"}, {"password": "shared"})
    assert tagged.classify("shared") == ("secret", "password")


def test_check_done_reports_what_is_missing():
    spec = load()
    confirmed = "Transfer Complete! $5.00 has been transferred from account acct-a to acct-b"
    good = {"confirmation_text": confirmed, "new_balance": "$5.00", "source_balance_before": "$9.00"}
    assert spec.check_done(good, PARAMS) == []  # transaction_id is optional
    assert spec.check_done({}, PARAMS) == [
        "confirmation_text was not captured", "new_balance was not captured",
    ]
    vague = {**good, "confirmation_text": "Transfer Complete!"}
    problems = spec.check_done(vague, PARAMS)
    assert problems == ["confirmation_text does not confirm the goal was met"]
    assert "Transfer Complete" not in problems[0]  # the required phrases are never quoted back


def test_confirmation_may_be_recorded_in_parts():
    spec = load()
    assert spec.extracts["confirmation_text"].combine is True
    assert not any(x.combine for n, x in spec.extracts.items() if n != "confirmation_text")
    heading = "Transfer Complete!"
    paragraph = "$5.00 has been transferred from account acct-a to account acct-b."
    base = {"new_balance": "$5.00"}
    assert spec.check_done({**base, "confirmation_text": heading}, PARAMS)  # no accounts
    assert spec.check_done({**base, "confirmation_text": paragraph}, PARAMS) == []  # says it all
    joined = {**base, "confirmation_text": f"{heading}\n{paragraph}"}
    assert spec.check_done(joined, PARAMS) == []


def test_check_done_can_require_every_input_to_have_been_entered():
    spec = load()
    good = {
        "confirmation_text": "$5.00 has been transferred from account acct-a to account acct-b.",
        "new_balance": "$5.00",
    }
    assert spec.check_done(good, PARAMS, entered={"from_account", "amount"}) == [
        "the value for to_account was never entered"
    ]
    assert spec.check_done(good, PARAMS, entered=set(spec.inputs)) == []
    assert spec.check_done(good, PARAMS) == []  # not asked to check: as before


def test_gate_wiring_must_point_at_the_right_things():
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(raw() | {"amount_input": "from_account"})  # not a decimal input
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(raw() | {"balance_extract": "new_balance"})  # not a policy read
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(raw() | {"amount_input": "nope"})


def test_extract_descriptions_are_filled_from_the_parameters():
    described = load().extract_descriptions(PARAMS)
    assert "acct-a" in described["source_balance_before"]
    assert set(described) == set(load().extracts)
