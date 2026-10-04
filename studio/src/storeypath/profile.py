"""Layer-mapping profiles: how a drawing's layers, blocks and labels map onto our
canonical types. Built-in profiles live in ``storeypath/profiles/*.yaml``; a path to
a custom YAML file works anywhere a profile name does."""

from __future__ import annotations

import re
from functools import cached_property
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from .types import SpaceType


def _compile_any(patterns: list[str]) -> re.Pattern[str]:
    if not patterns:
        return re.compile(r"(?!)")  # matches nothing
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


class SpacesConfig(BaseModel):
    layers: list[str]
    min_area: float = 0.5


class LabelsConfig(BaseModel):
    layers: list[str]
    number_pattern: str = r"[A-Z]{0,2}\d{1,4}[A-Z]?"


class DoorsConfig(BaseModel):
    layers: list[str] = Field(default_factory=list)
    reach: float = 0.35


class BlocksConfig(BaseModel):
    layers: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    type: SpaceType
    label: str | None = None
    block: str | None = None
    layer: str | None = None

    @model_validator(mode="after")
    def _one_condition(self) -> Rule:
        if sum(x is not None for x in (self.label, self.block, self.layer)) != 1:
            raise ValueError("a rule needs exactly one of: label, block, layer")
        return self

    @cached_property
    def pattern(self) -> re.Pattern[str]:
        if self.label is not None:
            return re.compile(self.label, re.IGNORECASE)
        return re.compile(f"(?:{self.block or self.layer})", re.IGNORECASE)

    def describe(self) -> str:
        field = "label" if self.label else "block" if self.block else "layer"
        return f"{field}:{getattr(self, field)}"


class Profile(BaseModel):
    name: str
    description: str = ""
    spaces: SpacesConfig
    labels: LabelsConfig
    doors: DoorsConfig = Field(default_factory=DoorsConfig)
    blocks: BlocksConfig = Field(default_factory=BlocksConfig)
    rules: list[Rule] = Field(default_factory=list)

    @cached_property
    def space_layers(self) -> re.Pattern[str]:
        return _compile_any(self.spaces.layers)

    @cached_property
    def label_layers(self) -> re.Pattern[str]:
        return _compile_any(self.labels.layers)

    @cached_property
    def door_layers(self) -> re.Pattern[str]:
        return _compile_any(self.doors.layers)

    @cached_property
    def block_layers(self) -> re.Pattern[str]:
        return _compile_any(self.blocks.layers)

    @cached_property
    def number_re(self) -> re.Pattern[str]:
        return re.compile(self.labels.number_pattern, re.IGNORECASE)

    def classify(self, name: str | None, blocks: list[str], layer: str) -> tuple[SpaceType, str]:
        """Return (type, the rule that decided it)."""
        for rule in self.rules:
            if rule.label is not None:
                hit = name is not None and rule.pattern.search(name)
            elif rule.block is not None:
                hit = any(rule.pattern.fullmatch(b) for b in blocks)
            else:
                hit = rule.pattern.fullmatch(layer)
            if hit:
                return rule.type, rule.describe()
        return SpaceType.UNSPECIFIED, "default"


def builtin_profiles() -> list[str]:
    return sorted(
        p.name.removesuffix(".yaml")
        for p in resources.files("storeypath.profiles").iterdir()
        if p.name.endswith(".yaml")
    )


def load_profile(name_or_path: str) -> Profile:
    path = Path(name_or_path)
    if path.suffix in (".yaml", ".yml") and path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        res = resources.files("storeypath.profiles") / f"{name_or_path}.yaml"
        if not res.is_file():
            raise FileNotFoundError(
                f"unknown profile {name_or_path!r}; built-in: {', '.join(builtin_profiles())}"
            )
        text = res.read_text(encoding="utf-8")
    return Profile.model_validate(yaml.safe_load(text))
