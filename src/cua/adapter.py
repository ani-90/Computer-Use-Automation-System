"""Surface adapter: the only module allowed to import playwright."""

import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import urljoin

from playwright.sync_api import Browser, Page, Playwright, Request, sync_playwright
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


class LocatorNotFound(Exception):
    """No candidate in a locator's fallback chain resolved to exactly one element."""


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
class Observation:
    url: str
    screenshot: bytes  # raw, for the LLM, in memory only: never write this to disk
    masked_screenshot: bytes  # account numbers boxed out: the only one allowed on disk
    candidates: list[Candidate]


@dataclass(frozen=True)
class ResolvedElement:
    handle: PWLocator
    description: str


@dataclass(frozen=True)
class Action:
    kind: Literal["click", "type", "select", "extract"]
    value: str | None = None


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
        self._page: Page | None = None
        self._inflight: set[Request] = set()

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False)  # always headed
        self._page = self._browser.new_context().new_page()
        self._page.set_default_timeout(self._timeout)
        self._page.on("request", self._track)
        self._page.on("requestfinished", self._untrack)
        self._page.on("requestfailed", self._untrack)

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

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
        return Observation(page.url, page.screenshot(), self._masked_screenshot(page), candidates)

    def resolve(self, locator: Locator) -> ResolvedElement:
        page = self._require_page()
        for rank, cand in enumerate(locator.chain):
            handle = self._build(page, cand)
            count = handle.count()
            if count == 1:
                if rank > 0:  # fell past the primary role+name candidate
                    self._log(
                        "locator_fallback",
                        description=locator.description,
                        rank=rank,
                        strategy=cand.strategy,
                    )
                return ResolvedElement(handle, locator.description)
            self._log(
                "locator_miss",
                description=locator.description,
                rank=rank,
                strategy=cand.strategy,
                matches=count,
            )
        raise LocatorNotFound(locator.description)

    def act(self, element: ResolvedElement, action: Action) -> str | None:
        # Typed values are never logged: the password passes through here.
        self._log("act", kind=action.kind, description=element.description)
        match action.kind:
            case "click":
                element.handle.click()
            case "type":
                element.handle.fill(action.value or "")
            case "select":
                element.handle.select_option(action.value or "")
            case "extract":
                return element.handle.inner_text()
        return None

    def navigate(self, target: str) -> None:
        page = self._require_page()
        page.goto(target)
        page.wait_for_load_state("networkidle")

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
                return loc if cand.nth is None else loc.nth(cand.nth)
            case "label":
                return page.get_by_label(cand.value, exact=True)
            case "text":
                return page.get_by_text(cand.value, exact=True)
            case "placeholder":
                return page.get_by_placeholder(cand.value, exact=True)

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
        return page.screenshot(mask=masks)
