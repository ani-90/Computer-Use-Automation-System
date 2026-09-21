"""Snapshot-parsing helpers of PlaywrightAdapter, checked on real snapshot line shapes. No browser."""

from cua.adapter import _SNAPSHOT_ROW, PlaywrightAdapter
from cua.config import Config

SECRET = "not-a-real-secret"
BASE = "http://localhost:8080/parabank/overview.htm"


def _adapter() -> PlaywrightAdapter:
    return PlaywrightAdapter(Config(), secrets=[SECRET])  # never started: no browser


def test_value_read_from_a_filled_textbox():
    lines = ['- text: "Find by Amount:"', '- textbox: "7"']
    assert _adapter()._value(lines, 1, "textbox") == ("7", True)
    assert _adapter()._value(['- textbox "Note" [disabled]: hello'], 0, "textbox") == ("hello", True)


def test_empty_textbox_is_not_filled():
    assert _adapter()._value(["- textbox"], 0, "textbox") == (None, False)


def test_secret_value_is_masked_but_counts_as_filled():
    assert _adapter()._value([f"- textbox: {SECRET}"], 0, "textbox") == (None, True)


def test_dropdown_options_and_selection():
    lines = [
        "- combobox:",
        '  - option "alpha" [selected]',
        '  - option "beta"',
        "- text: next section",
    ]
    assert PlaywrightAdapter._options(lines, 0) == (("alpha", "beta"), "alpha")


def test_link_target_is_made_absolute():
    relative = ['- link "Transfer Funds":', "  - /url: transfer.htm"]
    absolute_path = ['- link "Row":', "  - /url: /parabank/transaction.htm?id=1"]
    assert PlaywrightAdapter._href(relative, 0, BASE) == "http://localhost:8080/parabank/transfer.htm"
    assert (
        PlaywrightAdapter._href(absolute_path, 0, BASE)
        == "http://localhost:8080/parabank/transaction.htm?id=1"
    )
    assert PlaywrightAdapter._href(['- link "No target"'], 0, BASE) is None


def test_hint_drops_the_snapshot_quotes():
    assert PlaywrightAdapter._hint(['- text: "Amount: $"', "- textbox"], 1) == "Amount: $"
    assert PlaywrightAdapter._hint(["- paragraph: Username", "- textbox"], 1) == "Username"


def test_wrapped_snapshot_line_still_parses():
    m = _SNAPSHOT_ROW.match("- 'button \"Foo: bar\"':")
    assert m["role"] == "button" and m["name"] == "Foo: bar"
