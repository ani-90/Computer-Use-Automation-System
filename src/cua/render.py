"""Rendering of observations and history for the model, and image stripping for evidence."""

import base64
from urllib.parse import urlparse

from cua.adapter import Candidate, Observation, TextNode
from cua.trace import TraceStep

_MAX_TEXT = 100
_MAX_OPTIONS = 10
_FIELDS = {"textbox", "searchbox", "spinbutton"}


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."


def _candidate_line(c: Candidate) -> str:
    if c.name:
        label = f'"{_cut(c.name)}"'
    else:
        label = f'(near "{_cut(c.hint)}")' if c.hint else ""
    line = f"[{c.index}] {c.role} {label}".rstrip()
    if c.role == "link" and c.href:
        line += f" -> {urlparse(c.href).path}"
    elif c.role == "combobox" and c.options:  # "*" marks the selected option
        shown = [_cut(o) + ("*" if o == c.selected else "") for o in c.options[:_MAX_OPTIONS]]
        line += " options: " + ", ".join(shown) + ("..." if len(c.options) > _MAX_OPTIONS else "")
    elif c.role in _FIELDS:
        if c.value is not None:
            line += f' = "{_cut(c.value)}"'
        else:
            line += " (filled)" if c.filled else " (empty)"  # a masked secret is only "filled"
    return line


def _text_line(t: TextNode) -> str:
    line = f'[{t.index}] {t.kind} "{_cut(t.text)}"'
    if t.table is not None:
        where = f'column "{t.column}"' if t.column else f"col {t.col}"
        line += f" (table {t.table}, row {t.row}, {where})"
    if t.locator is None:
        line += " (context only)"
    return line


def render_observation(obs: Observation) -> str:
    lines = [f"Page: {urlparse(obs.url).path}", "", "Interactive elements:"]
    lines += [_candidate_line(c) for c in obs.candidates]
    lines += ["", "Readable text (extract only):"]
    lines += [_text_line(t) for t in obs.texts]
    return "\n".join(lines)


def history_line(step: TraceStep) -> str:
    what = step.tool
    if step.target is not None:
        what += f" {step.target.description}"
    if step.tool == "extract" and step.extract_name:
        what += f' as "{step.extract_name}"'
    shows_value = step.tool in {"type", "select", "navigate"} and step.provenance != "secret"
    if shows_value and step.value is not None:
        what += f' "{_cut(step.value)}"'
    if step.result == "ok":
        outcome = f"page: {urlparse(step.url_after).path}" if step.url_after else "ok"
    else:
        outcome = f"{step.result}: {_cut(step.error or '')}"
    line = f"Step {step.step_no}: {what} -> {outcome}"
    if step.expectation:
        line += f'. Expectation "{step.expect}": {step.expectation}'
    if step.checkpoint_status == "failed":
        line += ". The action did not take effect"
    return line


def image_block(png: bytes) -> dict:
    data = base64.b64encode(png).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def user_content(text: str, screenshot: bytes | None = None) -> list[dict]:
    blocks: list[dict] = [{"type": "text", "text": text}]
    if screenshot:
        blocks.append(image_block(screenshot))
    return blocks


def strip_images(value):
    """Copy of a message structure with every image replaced by a marker.

    The model sees raw screenshots; evidence must never contain them.
    """
    if isinstance(value, dict):
        if value.get("type") == "image":
            return {"type": "image", "omitted": True}
        return {k: strip_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_images(v) for v in value]
    return value
