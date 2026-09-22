"""Direct coverage for verify.py's condition kinds the real artifact never exercises.

test_replay.py already exercises url_matches, element_visible, element_absent, shape_matches,
field_value_equals and option_selected/option_present end to end, because those are the only
kinds this capability's compiled steps actually produce. field_filled (secret-only, and secret
steps never compile — see compiler.py) and text_present/text_equals (schema-valid, never derived
by checkpoints.py today) are dead code paths in every real run so far; they still need direct
coverage so a real bug in them isn't invisible until some future capability finally uses one.
"""

from cua.adapter import Candidate, LocatorNotFound, Observation, TextNode
from cua.models import Condition, Locator, LocatorCandidate
from cua.verify import evaluate, evaluate_shape, render, render_condition, render_locator


def loc(text: str, strategy: str = "text", **kw) -> Locator:
    return Locator(description=text, chain=[LocatorCandidate(strategy=strategy, value=text, **kw)])


class FakeAdapter:
    def __init__(self, resolvable: set[str]):
        self.resolvable = resolvable

    def resolve(self, locator: Locator):
        if locator.description in self.resolvable:
            return object()
        raise LocatorNotFound(locator.description)

    def exists(self, locator: Locator) -> bool:
        return locator.description in self.resolvable


def test_render_substitutes_every_param_once():
    assert render("{{amount}} to {{from_account}}", {"amount": "5", "from_account": "A"}) == "5 to A"
    assert render("no placeholders", {"amount": "5"}) == "no placeholders"


def test_render_locator_substitutes_description_and_chain_value():
    target = Locator(
        description="cell {{from_account}}",
        chain=[LocatorCandidate(strategy="table_cell", value="{{from_account}}", column="Balance*")],
    )
    out = render_locator(target, {"from_account": "111111"})
    assert out.description == "cell 111111"
    assert out.chain[0].value == "111111"


def test_field_filled_true_only_when_something_was_typed_or_selected():
    target = loc("textbox", role="textbox")
    filled = Observation(
        "http://h/x", b"", b"", [Candidate(1, "textbox", "", None, None, target, filled=True)], []
    )
    empty = Observation(
        "http://h/x", b"", b"", [Candidate(1, "textbox", "", None, None, target, filled=False)], []
    )
    cond = Condition(kind="field_filled", target=target)
    assert evaluate(cond, FakeAdapter(set()), filled, {}) is True
    assert evaluate(cond, FakeAdapter(set()), empty, {}) is False


def test_field_filled_also_true_for_a_selected_dropdown():
    target = loc("combobox", role="combobox")
    obs = Observation(
        "http://h/x", b"", b"", [Candidate(1, "combobox", "", None, None, target, selected="A")], []
    )
    cond = Condition(kind="field_filled", target=target)
    assert evaluate(cond, FakeAdapter(set()), obs, {}) is True


def test_field_filled_is_false_when_the_element_is_entirely_absent():
    target = loc("textbox", role="textbox")
    obs = Observation("http://h/x", b"", b"", [], [])
    cond = Condition(kind="field_filled", target=target)
    assert evaluate(cond, FakeAdapter(set()), obs, {}) is False


def test_text_present_matches_normalized_whitespace_anywhere_on_the_page():
    obs = Observation(
        "http://h/x", b"", b"", [], [TextNode(1, "paragraph", "  Some   Message  here", loc("x"))]
    )
    cond = Condition(kind="text_present", target=loc("x"), value="some message here")
    assert evaluate(cond, FakeAdapter(set()), obs, {}) is True
    missing = Condition(kind="text_present", target=loc("x"), value="not on the page")
    assert evaluate(missing, FakeAdapter(set()), obs, {}) is False


def test_text_present_also_matches_a_candidates_own_name():
    obs = Observation(
        "http://h/x", b"", b"", [Candidate(1, "link", "Some Link Text", None, None, loc("l"))], []
    )
    cond = Condition(kind="text_present", target=loc("x"), value="Some Link Text")
    assert evaluate(cond, FakeAdapter(set()), obs, {}) is True


def test_text_equals_matches_a_candidates_exact_value():
    target = loc("textbox", role="textbox")
    obs = Observation(
        "http://h/x", b"", b"", [Candidate(1, "textbox", "", None, None, target, value="exact")], []
    )
    assert evaluate(Condition(kind="text_equals", target=target, value="exact"), FakeAdapter(set()), obs, {})
    assert not evaluate(
        Condition(kind="text_equals", target=target, value="different"), FakeAdapter(set()), obs, {}
    )


def test_element_visible_and_absent_use_resolve_not_a_snapshot_lookup():
    target = loc("button")
    obs = Observation("http://h/x", b"", b"", [], [])  # empty snapshot on purpose
    adapter = FakeAdapter({"button"})  # but the adapter can still resolve it
    assert evaluate(Condition(kind="element_visible", target=target), adapter, obs, {}) is True
    assert evaluate(Condition(kind="element_absent", target=target), adapter, obs, {}) is False


def test_url_matches_compares_only_the_path():
    obs = Observation("http://h/parabank/overview.htm?x=1", b"", b"", [], [])
    assert evaluate(Condition(kind="url_matches", value="/parabank/overview.htm"), FakeAdapter(set()), obs, {})
    assert not evaluate(Condition(kind="url_matches", value="/parabank/transfer.htm"), FakeAdapter(set()), obs, {})


def test_a_condition_referencing_a_param_is_rendered_before_matching():
    target = loc("{{name}}", role="link")
    rendered_target = loc("Real Name", role="link")
    obs = Observation(
        "http://h/x", b"", b"", [], [TextNode(1, "link", "Real Name", rendered_target)]
    )
    cond = render_condition(Condition(kind="text_present", target=target, value="{{name}}"), {"name": "Real Name"})
    assert cond.value == "Real Name"
    assert evaluate(cond, FakeAdapter(set()), obs, {}) is True


def test_evaluate_shape_checks_money_and_nonempty():
    assert evaluate_shape("$11.00", "money") is True
    assert evaluate_shape("eleven dollars", "money") is False
    assert evaluate_shape("anything", "nonempty") is True
    assert evaluate_shape("", "nonempty") is False
    assert evaluate_shape(None, "nonempty") is False
