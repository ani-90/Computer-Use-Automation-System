"""Surface adapter: the only module allowed to import playwright."""

import re
import time
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Self
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Request, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator as PWLocator

from cua.config import Config
from cua.evidence import EvidenceLogger
from cua.models import Locator, LocatorCandidate

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio", "searchbox", "spinbutton",
}
_FIELD_ROLES = {"textbox", "combobox", "checkbox", "radio", "searchbox", "spinbutton"}
_SNAPSHOT_ROW = re.compile(r'''^\s*- '?(?P<role>[a-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?''')
_TEXT_ROW = re.compile(r"^\s*- (?:paragraph|text): (?P<text>.+)$")
_VALUE_TAIL = re.compile(r"""'?(?:\s*\[[^\]]*\])*:\s*(?P<value>.+)$""")
_OPTION_ROW = re.compile(r'''^\s*- option "(?P<name>(?:[^"\\]|\\.)*)"(?P<flags>(?: \[[^\]]*\])*)''')
_URL_ROW = re.compile(r"^\s*- /url: (?P<url>\S+)")
_TEXT_INPUTS = {"textbox", "searchbox", "spinbutton"}
_SETTLE_POLL_MS = 300
_SETTLE_TIMEOUT_S = 5.0

# Registered once, at context creation, so it is already present and dormant on whatever page
# is loaded when a human handoff happens — no race to inject it at that moment. It only ever
# records while the owner flag says "human"; the agent's own clicks are never captured. Kept in
# sessionStorage, not a plain window variable, so both the flag and the buffer survive a
# navigation during the takeover (window globals reset on every page load; sessionStorage does
# not, for the lifetime of the tab).
_CAPTURE_SCRIPT = """
(() => {
  const OWNER = "__cua_owner", ACTIONS = "__cua_actions";
  if (!sessionStorage.getItem(OWNER)) sessionStorage.setItem(OWNER, "agent");
  if (!sessionStorage.getItem(ACTIONS)) sessionStorage.setItem(ACTIONS, "[]");
  document.addEventListener("click", (e) => {
    if (sessionStorage.getItem(OWNER) !== "human") return;
    const t = e.target;
    const actions = JSON.parse(sessionStorage.getItem(ACTIONS) || "[]");
    actions.push({
      tag: t.tagName || "",
      text: (t.innerText || t.value || "").slice(0, 100),
      url: location.href,
    });
    sessionStorage.setItem(ACTIONS, JSON.stringify(actions));
  }, true);
})();
"""


class LocatorNotFound(Exception):
    """No candidate in a locator's fallback chain resolved to exactly one element."""


class ActionFailed(Exception):
    """The browser could not perform an action (not actionable, navigation failed, timeout)."""


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Candidate:
    index: int  # 1-based, valid only until the next observe()
    role: str
    name: str  # accessible name; "" when the element has none
    hint: str | None  # nearby label text for unnamed elements
    box: BoundingBox | None
    locator: Locator  # what the compiler later saves; discovery resolves this
    value: str | None = None  # current text; None when empty or when it matches a secret
    filled: bool = False  # the field holds something (true even for a masked secret)
    options: tuple[str, ...] = ()  # dropdown options
    selected: str | None = None  # the selected dropdown option
    href: str | None = None  # absolute link target


@dataclass(frozen=True)
class TextNode:
    index: int  # continues after the candidates, so refs are unique across both lists
    kind: str  # heading | paragraph | text | cell | columnheader
    text: str
    locator: Locator | None  # None for table cells until the table-cell locator exists
    table: int | None = None  # table number, row number and column position (table content)
    row: int | None = None
    col: int | None = None
    column: str | None = None  # the column header, when the table has one


@dataclass(frozen=True)
class Observation:
    url: str
    screenshot: bytes  # raw, for the LLM, in memory only: never write this to disk
    masked_screenshot: bytes  # account numbers boxed out: the only one allowed on disk
    candidates: list[Candidate]
    texts: list[TextNode] = field(default_factory=list)  # visible non-interactive content


@dataclass(frozen=True)
class ResolvedElement:
    handle: PWLocator
    description: str


@dataclass(frozen=True)
class Action:
    kind: Literal["click", "type", "select", "extract"]
    value: str | None = None


def _parse_texts(lines: list[str], first_index: int) -> list[TextNode]:
    found: list[tuple] = []  # (kind, text, table, row, col, column)
    headers: list[str] = []
    table_no = row_no = col = -1
    table_indent: int | None = None
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if table_indent is not None and indent <= table_indent:
            table_indent = None  # left the table
        m = _SNAPSHOT_ROW.match(line)
        if not m:
            continue
        role, name = m["role"], (m["name"] or "").replace('\\"', '"')
        in_table = table_indent is not None
        if role == "table":
            table_no, table_indent, headers, row_no = table_no + 1, indent, [], -1
        elif role == "row" and in_table:
            row_no, col = row_no + 1, 0
        elif role == "columnheader" and in_table:
            headers.append(name)
            found.append(("columnheader", name, table_no, row_no, len(headers) - 1, name))
        elif role == "cell" and in_table:
            if name:
                column = headers[col] if col < len(headers) else None
                found.append(("cell", name, table_no, row_no, col, column))
            col += 1  # empty cells still occupy a column
        elif role == "heading" and name:
            found.append(("heading", name, None, None, None, None))
        elif role in {"paragraph", "text"} and (t := _TEXT_ROW.match(line)):
            text = PlaywrightAdapter._yaml_text(t["text"])
            if any(ch.isalnum() for ch in text):  # skips separators like "|"
                found.append((role, text, None, None, None, None))
    anchors = {(t, r): x for k, x, t, r, c, _ in found if k == "cell" and c == 0}
    rows_by_anchor: dict[str, list[tuple[int, int]]] = {}
    for key, anchor in anchors.items():
        rows_by_anchor.setdefault(anchor, []).append(key)
    totals = Counter((k, t) for k, t, *_ in found if k in {"heading", "paragraph", "text"})
    seen: dict[tuple[str, str], int] = {}
    nodes: list[TextNode] = []
    for idx, (kind, text, table, row, pos, column) in enumerate(found, start=first_index):
        locator = None
        if kind in {"heading", "paragraph", "text"}:
            nth = seen.get((kind, text), 0)
            seen[(kind, text)] = nth + 1
            at = nth if totals[(kind, text)] > 1 else None
            cand = (
                LocatorCandidate(strategy="role_name", role="heading", value=text, nth=at)
                if kind == "heading"
                else LocatorCandidate(strategy="text", value=text, nth=at)
            )
            locator = Locator(description=f'{kind} "{text}"', chain=[cand])
        elif kind == "cell":
            anchor = anchors.get((table, row))
            if anchor:  # a row with no first-cell text has nothing to anchor on
                same = rows_by_anchor[anchor]
                cand = LocatorCandidate(
                    strategy="table_cell",
                    value=anchor,
                    column=column,
                    col=None if column else pos,
                    nth=same.index((table, row)) if len(same) > 1 else None,
                )
                where = f'"{column}"' if column else f"col {pos}"
                locator = Locator(description=f'cell[row "{anchor}", {where}]', chain=[cand])
        nodes.append(TextNode(idx, kind, text, locator, table, row, pos, column))
    return nodes


def _apply_nth(loc: PWLocator, nth: int) -> PWLocator:
    # LocatorCandidate.nth documents -1 as "the last match" — Playwright's own .nth() is
    # zero-based only (confirmed against its docstring, not just assumed) and does not accept a
    # negative index the way Python indexing does; .last is the real, documented way to select
    # it. Every non-negative value still goes through .nth() unchanged.
    return loc.last if nth == -1 else loc.nth(nth)


def _should_mask(shown: str, pattern: str | None, secrets: Collection[str]) -> bool:
    """Whether a form control's visible value must be hidden in a saved screenshot."""
    if not shown:
        return False
    if any(secret and secret in shown for secret in secrets):
        return True
    return bool(pattern and re.search(pattern, shown))


class PlaywrightAdapter:
    def __init__(
        self,
        config: Config,
        logger: EvidenceLogger | None = None,
        timeout_ms: int = 10_000,
        secrets: Sequence[str] = (),
    ):
        self._config = config
        self._logger = logger
        self._timeout = timeout_ms
        self._secrets = {s for s in secrets if s}  # values that must never enter a candidate
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._inflight: set[Request] = set()

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False)  # always headed
        self._context = self._browser.new_context()
        self._context.add_init_script(_CAPTURE_SCRIPT)  # at context creation, not at handoff time
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._timeout)
        self._page.on("request", self._track)
        self._page.on("requestfinished", self._untrack)
        self._page.on("requestfailed", self._untrack)

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def set_session_owner(self, owner: Literal["agent", "human"]) -> None:
        page = self._require_page()
        page.evaluate("(o) => sessionStorage.setItem('__cua_owner', o)", owner)

    def captured_actions(self) -> list[dict]:
        """Whatever was captured while the owner was "human", then clears the buffer."""
        page = self._require_page()
        actions = page.evaluate("JSON.parse(sessionStorage.getItem('__cua_actions') || '[]')")
        page.evaluate("sessionStorage.setItem('__cua_actions', '[]')")
        return actions

    # -- fault injection (Phase 8, --inject-faults only) ---------------------
    # Two more deliberate verbs beyond the original four, same justification as
    # set_session_owner/captured_actions above: real fault injection needs real browser-context
    # control, which only this file is allowed to touch. Never called by a normal replay.

    def clear_session(self) -> None:
        """Genuinely invalidate the session — real cookie deletion, not a faked redirect. The
        next request the app itself makes decides what happens next, exactly like a real expiry."""
        context = self._require_context()
        context.clear_cookies()

    def delay_next_request(self, url_pattern: str, delay_ms: int) -> None:
        """Let exactly one matching request complete for real, just late. Unlike drop_response
        (Phase 9), the request is never aborted — this only makes the real wait_strategy poll
        loop retry against genuine latency, not a special-cased branch standing in for it.

        A route handler must call nothing but route.continue_()/abort() on itself — calling any
        other page-level sync method (page.wait_for_timeout, page.unroute) from inside it
        re-enters Playwright's sync dispatch loop and corrupts the route's own state ("Route is
        already handled"), confirmed live. A plain time.sleep() blocks only this handler, and
        "already used" is tracked with a closure flag instead of ever calling page.unroute here.
        """
        page = self._require_page()
        used = False

        def _handler(route) -> None:
            nonlocal used
            if used:
                route.continue_()
                return
            used = True
            time.sleep(delay_ms / 1000)
            route.continue_()

        page.route(url_pattern, _handler)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def observe(self) -> Observation:
        page = self._require_page()
        lines = self._settled_snapshot(page)
        rows = [
            (i, m["role"], (m["name"] or "").replace('\\"', '"'))
            for i, line in enumerate(lines)
            if (m := _SNAPSHOT_ROW.match(line)) and m["role"] in INTERACTIVE_ROLES
        ]
        totals = Counter((role, name) for _, role, name in rows if name)
        seen: dict[tuple[str, str], int] = {}  # named duplicates: (role, name) -> count so far
        role_count: dict[str, int] = {}  # every element of a role, in page order
        candidates: list[Candidate] = []
        for i, role, name in rows:
            role_nth = role_count.get(role, 0)
            role_count[role] = role_nth + 1
            if name:
                nth = seen.get((role, name), 0)
                seen[(role, name)] = nth + 1
                handle = page.get_by_role(role, name=name, exact=True).nth(nth)
                hint = None
                locator = self._describe(role, name, nth if totals[(role, name)] > 1 else None)
            elif role in _FIELD_ROLES:
                hint = self._hint(lines, i)
                handle = page.get_by_role(role).nth(role_nth)
                locator = self._describe_unnamed(role, hint, role_nth)
            else:
                continue  # unnamed link or button: nothing to identify it by
            value, filled = self._value(lines, i, role)
            options, selected = self._options(lines, i) if role == "combobox" else ((), None)
            href = self._href(lines, i, page.url) if role == "link" else None
            candidates.append(
                Candidate(
                    len(candidates) + 1, role, name, hint, self._box(handle), locator,
                    value, filled, options, selected, href,
                )
            )
        texts = _parse_texts(lines, first_index=len(candidates) + 1)
        texts = [self._addressable(page, t) for t in texts]
        return Observation(
            page.url, page.screenshot(), self._masked_screenshot(page), candidates, texts
        )

    def resolve(self, locator: Locator) -> ResolvedElement:
        # Only visible matches count. A raw DOM count would also match a hidden leftover (e.g.
        # the transfer form ParaBank hides, rather than removes, once the confirmation shows) —
        # role-based matching already excludes it via the accessibility tree, but a plain text
        # match does not, so both must be filtered the same way for element_visible/absent to
        # agree with what the accessibility-snapshot-based checkpoint was derived against.
        page = self._require_page()
        for rank, cand in enumerate(locator.chain):
            handle = self._build(page, cand)
            visible = self._visible_indices(handle)
            if len(visible) == 1:
                if rank > 0:  # fell past the primary role+name candidate
                    self._log(
                        "locator_fallback",
                        description=locator.description,
                        rank=rank,
                        strategy=cand.strategy,
                    )
                return ResolvedElement(handle.nth(visible[0]), locator.description)
            self._log(
                "locator_miss",
                description=locator.description,
                rank=rank,
                strategy=cand.strategy,
                matches=len(visible),
            )
        raise LocatorNotFound(locator.description)

    def exists(self, locator: Locator) -> bool:
        """Whether the locator has one-or-more visible matches — existence, for element_visible/
        element_absent, never a target to act on. resolve() must keep its exactly-one rule (an
        ambiguous action target has to fail loudly); this is a separate, narrower verb, because a
        real page can legitimately show the same content more than once — e.g. two past
        transactions sharing an identical description — without that meaning the element a
        checkpoint is looking for genuinely isn't there. Confirmed live: a checkpoint wrongly
        failed on exactly this after two same-amount test transfers landed both descriptions on
        one results page."""
        page = self._require_page()
        return any(self._visible_indices(self._build(page, cand)) for cand in locator.chain)

    @staticmethod
    def _visible_indices(handle: PWLocator) -> list[int]:
        return [i for i in range(handle.count()) if handle.nth(i).is_visible()]

    def act(self, element: ResolvedElement, action: Action) -> str | None:
        # Typed values are never logged: the password passes through here.
        self._log("act", kind=action.kind, description=element.description)
        try:
            match action.kind:
                case "click":
                    element.handle.click()
                case "type":
                    element.handle.fill(action.value or "")
                case "select":
                    element.handle.select_option(action.value or "")
                case "extract":
                    return element.handle.inner_text()
        except PlaywrightError as e:
            raise ActionFailed(str(e).splitlines()[0]) from e
        return None

    def navigate(self, target: str) -> None:
        page = self._require_page()
        try:
            page.goto(target)
        except PlaywrightError as e:
            raise ActionFailed(str(e).splitlines()[0]) from e
        page.wait_for_load_state("networkidle")

    def _addressable(self, page: Page, node: TextNode) -> TextNode:
        """Keep a text node's locator only if it resolves to exactly one element right now."""
        if node.locator is None or self._build(page, node.locator.chain[0]).count() == 1:
            return node
        return replace(node, locator=None)

    def _settled_snapshot(self, page: Page) -> list[str]:
        """Wait until no request is in flight and two consecutive snapshots match.

        networkidle alone returns early after an in-page AJAX update (seen live: the search
        results appeared 3s after it returned), so it is only a cheap first pass.
        """
        page.wait_for_load_state("networkidle")
        previous = None
        deadline = time.monotonic() + _SETTLE_TIMEOUT_S
        while True:
            snapshot = page.locator("body").aria_snapshot()
            if not self._inflight and snapshot == previous:
                return snapshot.splitlines()
            if time.monotonic() >= deadline:
                self._log("settle_timeout", inflight=len(self._inflight))
                return snapshot.splitlines()
            previous = snapshot
            page.wait_for_timeout(_SETTLE_POLL_MS)

    # -- helpers ---------------------------------------------------------
    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("adapter not started")
        return self._page

    def _require_context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("adapter not started")
        return self._context

    def _log(self, event: str, **data: object) -> None:
        if self._logger:
            self._logger.log(event, **data)

    def _track(self, request: Request) -> None:
        self._inflight.add(request)

    def _untrack(self, request: Request) -> None:
        self._inflight.discard(request)

    @staticmethod
    def _describe(role: str, name: str, nth: int | None = None) -> Locator:
        primary = LocatorCandidate(strategy="role_name", role=role, value=name, nth=nth)
        if nth is not None:  # duplicate name: a label/text fallback would be ambiguous too
            return Locator(description=f'{role} "{name}" #{nth}', chain=[primary])
        fallback = LocatorCandidate(
            strategy="label" if role in _FIELD_ROLES else "text", value=name
        )
        return Locator(description=f'{role} "{name}"', chain=[primary, fallback])

    @staticmethod
    def _hint(lines: list[str], i: int) -> str | None:
        m = _TEXT_ROW.match(lines[i - 1]) if i > 0 else None
        return PlaywrightAdapter._yaml_text(m["text"]) if m else None

    @staticmethod
    def _yaml_text(raw: str) -> str:
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':  # the snapshot quotes text with a colon
            return raw[1:-1].replace('\\"', '"')
        return raw

    def _value(self, lines: list[str], i: int, role: str) -> tuple[str | None, bool]:
        m = _SNAPSHOT_ROW.match(lines[i])
        tail = _VALUE_TAIL.match(lines[i][m.end() :]) if m and role in _TEXT_INPUTS else None
        text = self._yaml_text(tail["value"]) if tail else ""
        if not text:
            return None, False
        return (None if text in self._secrets else text), True

    @staticmethod
    def _options(lines: list[str], i: int) -> tuple[tuple[str, ...], str | None]:
        indent = len(lines[i]) - len(lines[i].lstrip())
        names: list[str] = []
        selected = None
        for line in lines[i + 1 :]:
            if len(line) - len(line.lstrip()) <= indent:
                break
            if m := _OPTION_ROW.match(line):
                name = m["name"].replace('\\"', '"')
                names.append(name)
                if "selected" in m["flags"]:
                    selected = name
        return tuple(names), selected

    @staticmethod
    def _href(lines: list[str], i: int, base: str) -> str | None:
        m = _URL_ROW.match(lines[i + 1]) if i + 1 < len(lines) else None
        return urljoin(base, m["url"]) if m else None

    @staticmethod
    def _describe_unnamed(role: str, hint: str | None, nth: int) -> Locator:
        desc = f'{role} "{hint}"' if hint else f"{role} #{nth}"
        return Locator(
            description=desc,
            chain=[LocatorCandidate(strategy="role_name", role=role, nth=nth)],
        )

    @staticmethod
    def _build(page: Page, cand: LocatorCandidate) -> PWLocator:
        match cand.strategy:
            case "role_name":
                kwargs = {"name": cand.value, "exact": True} if cand.value else {}
                loc = page.get_by_role(cand.role, **kwargs)
            case "label":
                loc = page.get_by_label(cand.value, exact=True)
            case "text":
                loc = page.get_by_text(cand.value, exact=True)
            case "placeholder":
                loc = page.get_by_placeholder(cand.value, exact=True)
            case "table_cell":
                rows = page.get_by_role("row").filter(
                    has=page.get_by_role("cell", name=cand.value, exact=True)
                )
                if cand.nth is not None:
                    rows = _apply_nth(rows, cand.nth)
                if cand.column is None:
                    return rows.get_by_role("cell").nth(cand.col)
                header = page.get_by_role("columnheader", name=cand.column, exact=True)
                if header.count() != 1:
                    return header  # resolve() reports the miss
                pos = header.evaluate("e => Array.from(e.parentElement.children).indexOf(e)")
                return rows.get_by_role("cell").nth(pos)
        return loc if cand.nth is None else _apply_nth(loc, cand.nth)

    @staticmethod
    def _box(handle: PWLocator) -> BoundingBox | None:
        try:
            b = handle.bounding_box(timeout=1000)
        except PlaywrightError:
            return None
        return BoundingBox(b["x"], b["y"], b["width"], b["height"]) if b else None

    def _masked_screenshot(self, page: Page) -> bytes:
        pattern = self._config.redaction_patterns.get("account_number")
        masks = [page.get_by_text(re.compile(pattern))] if pattern else []
        masks += self._control_masks(page, pattern)
        return page.screenshot(mask=masks)

    def _control_masks(self, page: Page, pattern: str | None) -> list[PWLocator]:
        """Dropdowns and inputs draw their value natively, so text matching never reaches them."""
        controls = page.locator("select, input")
        masks: list[PWLocator] = []
        for i in range(controls.count()):
            control = controls.nth(i)
            try:
                shown = control.evaluate(
                    "e => e.tagName === 'SELECT' ? (e.selectedOptions[0]?.text ?? '') : e.value",
                    timeout=500,
                )
            except PlaywrightError:
                continue
            if _should_mask(shown or "", pattern, self._secrets):
                masks.append(control)
        return masks
