"""Checkpoint derivation on hand-built observations. No browser."""

from cua.adapter import Candidate, Observation, TextNode
from cua.checkpoints import StepContext, derive_checkpoint, refutation_message
from cua.models import Condition, Locator, LocatorCandidate

SECRET = "not-a-real-secret"


def loc(name: str, role: str = "button") -> Locator:
    cand = LocatorCandidate(strategy="role_name", role=role, value=name)
    return Locator(description=name, chain=[cand])


def text_loc(text: str) -> Locator:
    return Locator(description=text, chain=[LocatorCandidate(strategy="text", value=text)])


def obs(url: str = "http://h/p/a.htm", candidates=(), texts=()) -> Observation:
    return Observation(url, b"", b"", list(candidates), list(texts))


def cand(role: str, name: str, locator: Locator, **kw) -> Candidate:
    return Candidate(1, role, name, None, None, locator, **kw)


def node(kind: str, text: str, locator: Locator | None = None) -> TextNode:
    return TextNode(1, kind, text, locator)


BUTTON = cand("button", "Go", loc("Go"))


def test_hidden_result_that_becomes_visible_is_new():
    heading = node("heading", "Transfer Complete!", loc("Transfer Complete!", "heading"))
    before = obs(candidates=[BUTTON])
    after = obs(candidates=[BUTTON], texts=[heading])
    d = derive_checkpoint(
        StepContext("click", BUTTON.locator, expect="Transfer Complete"), before, after
    )
    assert (d.status, d.expectation) == ("verified", "confirmed")
    assert Condition(kind="element_visible", target=heading.locator) in d.conditions


def test_weak_expectation_falls_back_to_the_delta():
    seen = node("heading", "Accounts Overview", loc("Accounts Overview", "heading"))
    fresh = node("heading", "Other", loc("Other", "heading"))
    before = obs(texts=[seen])
    after = obs(texts=[seen, fresh])
    d = derive_checkpoint(
        StepContext("click", BUTTON.locator, expect="Accounts Overview"), before, after
    )
    assert (d.status, d.expectation) == ("verified", "weak")
    assert d.conditions == [Condition(kind="element_visible", target=fresh.locator)]


def test_refuted_expectation_explains_what_the_page_shows():
    after = obs(
        url="http://h/p/index.htm",
        texts=[node("heading", "Customer Login"), node("paragraph", "An error occurred")],
    )
    d = derive_checkpoint(
        StepContext("click", BUTTON.locator, expect="Accounts Overview"), obs(), after
    )
    assert d.expectation == "refuted"
    message = refutation_message("Accounts Overview", after)
    assert "/p/index.htm" in message and "Customer Login" in message and "error occurred" in message


def test_typed_parameter_becomes_a_placeholder_checkpoint():
    field = loc("Amount", "textbox")
    before = obs(candidates=[cand("textbox", "", field)])
    landed = obs(candidates=[cand("textbox", "", field, value="5", filled=True)])
    ctx = StepContext("type", field, value="5", provenance="parameter", param="amount")
    d = derive_checkpoint(ctx, before, landed)
    assert d.conditions == [Condition(kind="field_value_equals", target=field, value="{{amount}}")]
    assert derive_checkpoint(ctx, before, before).status == "failed"


def test_secret_value_is_never_recorded():
    field = loc("Password", "textbox")
    before = obs(candidates=[cand("textbox", "", field)])
    after = obs(candidates=[cand("textbox", "", field, value=None, filled=True)])
    ctx = StepContext("type", field, value=SECRET, provenance="secret")
    d = derive_checkpoint(ctx, before, after)
    assert d.conditions == [Condition(kind="field_filled", target=field)]
    assert SECRET not in repr(d)


def test_select_checks_the_chosen_option():
    dropdown = loc("From", "combobox")
    before = obs(candidates=[cand("combobox", "", dropdown, options=("a", "b"), selected="b")])
    after = obs(candidates=[cand("combobox", "", dropdown, options=("a", "b"), selected="a")])
    ctx = StepContext("select", dropdown, value="a", provenance="parameter", param="from_account")
    d = derive_checkpoint(ctx, before, after)
    expected = Condition(kind="option_selected", target=dropdown, value="{{from_account}}")
    assert d.conditions == [expected]


def test_extract_checks_the_shape():
    target = text_loc("balance")
    ok = StepContext("extract", target, value="-$598.50", shape="money")
    assert derive_checkpoint(ok, obs(), obs()).status == "verified"
    bad = StepContext("extract", target, value="abc", shape="money")
    assert derive_checkpoint(bad, obs(), obs()).status == "failed"


def test_click_with_nothing_new_is_unverified():
    d = derive_checkpoint(StepContext("click", BUTTON.locator), obs(), obs())
    assert (d.status, d.note) == ("unverified", "no observable change")


def test_table_cell_value_changes_are_not_checkpoints():
    before = obs(texts=[node("cell", "$10.00", text_loc("$10.00"))])
    after = obs(texts=[node("cell", "$5.00", text_loc("$5.00"))])
    assert derive_checkpoint(StepContext("click", BUTTON.locator), before, after).status == (
        "unverified"
    )


def test_volatile_text_is_not_a_checkpoint():
    after = obs(texts=[node("paragraph", "Updated 09-20-2026", text_loc("Updated 09-20-2026"))])
    assert derive_checkpoint(StepContext("click", BUTTON.locator), obs(), after).status == (
        "unverified"
    )


def test_navigate_records_the_path_only():
    heading = node("heading", "Next", loc("Next", "heading"))
    after = obs(url="http://h/p/b.htm?id=7", texts=[heading])
    d = derive_checkpoint(StepContext("navigate", None), obs(), after)
    assert Condition(kind="url_matches", value="/p/b.htm") in d.conditions
    assert Condition(kind="element_visible", target=heading.locator) in d.conditions


def test_in_page_update_records_what_disappeared():
    d = derive_checkpoint(StepContext("click", BUTTON.locator), obs(candidates=[BUTTON]), obs())
    assert d.conditions == [Condition(kind="element_absent", target=BUTTON.locator)]


def test_gained_option_is_a_placeholder_when_it_is_a_tagged_value():
    dropdown = loc("To", "combobox")
    before = obs(candidates=[cand("combobox", "", dropdown)])
    after = obs(candidates=[cand("combobox", "", dropdown, options=("acct-a",))])
    ctx = StepContext("click", BUTTON.locator, params={"from_account": "acct-a"})
    d = derive_checkpoint(ctx, before, after)
    expected = Condition(kind="option_present", target=dropdown, value="{{from_account}}")
    assert d.conditions == [expected]
