"""Snapshot-parsing helpers of PlaywrightAdapter, checked on real snapshot line shapes. No browser."""

from cua.adapter import _SNAPSHOT_ROW, PlaywrightAdapter, _parse_texts
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


OVERVIEW = [
    '- heading "Accounts Overview" [level=1]',
    "- table:",
    "  - rowgroup:",
    '    - row "Account Balance* Available Amount":',
    '      - columnheader "Account"',
    '      - columnheader "Balance*"',
    '      - columnheader "Available Amount"',
    "  - rowgroup:",
    '    - row "acct-a $10.00 $10.00":',
    '      - cell "acct-a":',
    '        - link "acct-a":',
    "          - /url: activity.htm?id=1",
    '      - cell "$10.00"',
    '      - cell "$10.00"',
    '    - row "Total $10.00":',
    '      - cell "Total"',
    '      - cell "$10.00"',
    "      - cell",
]


def test_table_cells_carry_their_column():
    nodes = _parse_texts(OVERVIEW, first_index=5)
    assert (nodes[0].index, nodes[0].kind, nodes[0].text) == (5, "heading", "Accounts Overview")
    assert [n.text for n in nodes if n.kind == "columnheader"] == [
        "Account", "Balance*", "Available Amount",
    ]
    balance = next(n for n in nodes if n.kind == "cell" and n.row == 1 and n.col == 1)
    assert (balance.table, balance.column, balance.locator) == (0, "Balance*", None)
    assert not any(n.row == 2 and n.col == 2 for n in nodes)  # empty cell is not emitted


def test_table_without_headers_has_no_column_names():
    lines = [
        '- heading "Transaction Details" [level=1]',
        "- table:",
        "  - rowgroup:",
        "    - 'row \"Transaction ID: 42\"':",
        '      - cell "Transaction ID:"',
        '      - cell "42"',
    ]
    cells = [n for n in _parse_texts(lines, first_index=1) if n.kind == "cell"]
    assert [(n.text, n.col, n.column) for n in cells] == [
        ("Transaction ID:", 0, None), ("42", 1, None),
    ]


def test_text_nodes_skip_separators_and_navigation():
    lines = [
        '- heading "Transfer Funds" [level=1]',
        "- paragraph:",
        '  - text: "Amount: $"',
        "  - textbox",
        "- paragraph: Welcome Someone",
        '- text: "|"',
        "- listitem: Solutions",
    ]
    nodes = _parse_texts(lines, first_index=1)
    assert [(n.kind, n.text) for n in nodes] == [
        ("heading", "Transfer Funds"), ("text", "Amount: $"), ("paragraph", "Welcome Someone"),
    ]


def test_duplicate_text_gets_a_position():
    nodes = _parse_texts(["- text: Repeat", "- text: Repeat", "- text: Unique"], first_index=1)
    assert [n.locator.chain[0].nth for n in nodes] == [0, 1, None]
