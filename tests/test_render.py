import base64

from cua.adapter import Candidate, Observation, TextNode
from cua.models import Locator, LocatorCandidate
from cua.render import history_line, render_observation, strip_images, user_content
from cua.trace import TraceStep


def loc(name: str) -> Locator:
    return Locator(description=name, chain=[LocatorCandidate(strategy="text", value=name)])


def test_observation_lists_both_tiers_with_one_numbering():
    candidates = [
        Candidate(1, "textbox", "", "Username", None, loc("u")),
        Candidate(2, "textbox", "", "Password", None, loc("p"), value=None, filled=True),
        Candidate(3, "textbox", "", "Amount", None, loc("a"), value="7", filled=True),
        Candidate(4, "link", "Go", None, None, loc("g"), href="http://h/p/next.htm?id=9"),
        Candidate(
            5, "combobox", "", "From", None, loc("c"), options=("a", "b"), selected="b",
        ),
    ]
    texts = [
        TextNode(6, "heading", "Overview", loc("Overview")),
        TextNode(7, "cell", "$10.00", None, table=0, row=1, col=1, column="Balance*"),
    ]
    out = render_observation(Observation("http://h/p/a.htm", b"", b"", candidates, texts))
    assert "Page: /p/a.htm" in out
    assert '[1] textbox (near "Username") (empty)' in out
    assert '[2] textbox (near "Password") (filled)' in out  # a masked secret is only "filled"
    assert '[3] textbox (near "Amount") = "7"' in out
    assert '[4] link "Go" -> /p/next.htm' in out and "id=9" not in out
    assert '[5] combobox (near "From") options: a, b*' in out
    assert '[6] heading "Overview"' in out
    assert '[7] cell "$10.00" (table 0, row 1, column "Balance*") (context only)' in out


def test_long_text_and_long_option_lists_are_cut():
    long = Candidate(1, "link", "w" * 300, None, None, loc("x"))
    many = Candidate(
        2, "combobox", "", "Pick", None, loc("y"), options=tuple(str(i) for i in range(30)),
    )
    out = render_observation(Observation("http://h/p", b"", b"", [long, many], []))
    assert "w" * 300 not in out and "..." in out
    assert "options: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9..." in out and "29" not in out


def test_history_line_hides_secret_values():
    secret = TraceStep(
        step_no=1, tool="type", target=loc("textbox Password"), provenance="secret",
        result="ok", url_after="http://h/p/a.htm",
    )
    assert history_line(secret) == "Step 1: type textbox Password -> page: /p/a.htm"
    typed = TraceStep(
        step_no=2, tool="type", target=loc("textbox Amount"), value="7", provenance="parameter",
        result="ok", url_after="http://h/p/a.htm", expect="Ready", expectation="weak",
    )
    assert history_line(typed) == (
        'Step 2: type textbox Amount "7" -> page: /p/a.htm. Expectation "Ready": weak'
    )


def test_history_line_for_failures():
    blocked = TraceStep(step_no=3, tool="click", result="blocked", error="path denied")
    assert history_line(blocked) == "Step 3: click -> blocked: path denied"
    failed = TraceStep(
        step_no=4, tool="extract", extract_name="balance", result="ok",
        url_after="http://h/p/a.htm", checkpoint_status="failed",
    )
    assert history_line(failed).endswith(". The action did not take effect")


def test_user_content_encodes_the_screenshot_and_strip_images_removes_it():
    blocks = user_content("hello", b"\x89PNG")
    assert blocks[1]["source"]["data"] == base64.b64encode(b"\x89PNG").decode()
    message = {"role": "user", "content": [{"type": "tool_result", "content": blocks}]}
    stripped = strip_images(message)
    assert stripped["content"][0]["content"][1] == {"type": "image", "omitted": True}
    assert "data" not in repr(stripped)
    assert user_content("no image") == [{"type": "text", "text": "no image"}]
