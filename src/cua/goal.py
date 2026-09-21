"""Goal spec: the human-authored description of one target flow, plus value tagging.

Target-specific knowledge lives in the spec file, never in the agent's prompt.
"""

import string
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.models import SHAPES, ParamSpec


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


class ExtractSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: str
    purpose: Literal["output", "policy"] = "output"  # policy: read only to feed the gate
    required: bool = True

    @model_validator(mode="after")
    def _known_shape(self) -> Self:
        if self.shape not in SHAPES:
            raise ValueError(f"unknown shape {self.shape!r}")
        return self


class GoalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start_page: str
    base_url_env: str  # name of the environment variable holding the base URL
    secrets_env: dict[str, str]  # secret name -> environment variable
    goal_template: str  # the only part the agent ever sees
    inputs: dict[str, ParamSpec]
    extracts: dict[str, ExtractSpec]
    done_requires: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        inputs = set(self.inputs)
        if not _placeholders(self.goal_template) <= inputs:
            raise ValueError("goal_template uses a placeholder that is not a declared input")
        for name, phrases in self.done_requires.items():
            spec = self.extracts.get(name)
            if spec is None or spec.purpose != "output":
                raise ValueError(f"done_requires names {name!r}, which is not a declared output")
            if any(not _placeholders(p) <= inputs for p in phrases):
                raise ValueError("done_requires uses a placeholder that is not a declared input")
        return self

    def goal_text(self, params: Mapping[str, str]) -> str:
        return self.goal_template.format_map(params)

    def start_url(self, env: Mapping[str, str]) -> str:
        return env[self.base_url_env].rstrip("/") + "/" + self.start_page

    def secrets(self, env: Mapping[str, str]) -> dict[str, str]:
        return {name: env[var] for name, var in self.secrets_env.items()}

    def check_done(self, captured: Mapping[str, str], params: Mapping[str, str]) -> list[str]:
        """What is still missing; an empty list means the done condition holds.

        Runs on the captured outputs, not the live page: the agent finishes elsewhere.
        The messages are generic on purpose and never quote the required phrases.
        """
        problems = []
        for name, spec in self.extracts.items():
            if spec.purpose == "output" and spec.required and name not in captured:
                problems.append(f"{name} was not captured")
        for name, phrases in self.done_requires.items():
            text = _norm(captured.get(name, ""))
            if name in captured and any(_norm(p.format_map(params)) not in text for p in phrases):
                problems.append(f"{name} does not confirm the goal was met")
        return problems


def parse_params(spec: GoalSpec, raw: Mapping[str, str]) -> dict[str, str]:
    missing = sorted(set(spec.inputs) - set(raw))
    unknown = sorted(set(raw) - set(spec.inputs))
    if missing or unknown:
        raise ValueError(f"parameters missing: {missing or 'none'}; unknown: {unknown or 'none'}")
    params: dict[str, str] = {}
    for name, param in spec.inputs.items():
        value = raw[name].strip()
        if not value:
            raise ValueError(f"parameter {name} is empty")
        if param.type == "decimal":
            try:
                Decimal(value)
            except InvalidOperation:
                raise ValueError(f"parameter {name} is not a number") from None
        params[name] = value
    return params


@dataclass(frozen=True)
class TaggedValues:
    params: dict[str, str]  # name -> value, from the command line
    types: dict[str, str]  # name -> "string" | "decimal"
    secrets: dict[str, str]  # name -> value, from the environment

    def classify(self, value: str) -> tuple[Literal["parameter", "secret", "other"], str | None]:
        for name, secret in self.secrets.items():
            if secret and value == secret:  # secrets first: never labelled a parameter
                return "secret", name
        for name, param in self.params.items():
            if self._same(value, param, self.types.get(name)):
                return "parameter", name
        return "other", None

    @staticmethod
    def _same(value: str, param: str, kind: str | None) -> bool:
        if value.strip() == param:
            return True
        if kind == "decimal":
            try:
                return Decimal(value) == Decimal(param)
            except InvalidOperation:
                return False
        return False
