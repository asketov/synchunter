"""Scenario files: how to drive and observe the application under test."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HttpCall:
    method: str
    path: str


@dataclass
class Observable:
    """A piece of UI that each user sees, read after every run."""

    name: str
    selector: str
    attr: str | None = None  # read this attribute; None reads the element text
    shared: bool = True  # all users must see the same value


@dataclass
class Invariant:
    """A Python expression over the server `state` that must always be true."""

    name: str
    expr: str
    rationale: str = ""
    source: str = "scenario"


@dataclass
class Scenario:
    name: str
    base_url: str
    description: str
    users: dict[str, str]
    reset: HttpCall | None
    state: HttpCall | None
    observables: list[Observable]
    invariants: list[Invariant]
    action_selector: str = "button"
    inputs: dict[str, str] = field(default_factory=dict)
    ready_selector: str | None = None
    settle_ms: int = 1200
    sequential_gap_ms: int = 800
    offsets_ms: list[int] = field(default_factory=lambda: [0, 50, 120, 250])


def _http_call(raw: dict | None) -> HttpCall | None:
    if not raw:
        return None
    return HttpCall(method=raw.get("method", "GET").upper(), path=raw["path"])


def load_scenario(path: str | Path) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Scenario(
        name=raw["name"],
        base_url=raw["base_url"].rstrip("/"),
        description=(raw.get("description") or "").strip(),
        users=dict(raw["users"]),
        reset=_http_call(raw.get("reset")),
        state=_http_call(raw.get("state")),
        observables=[Observable(**o) for o in raw.get("observables", [])],
        invariants=[Invariant(**i) for i in raw.get("invariants", [])],
        action_selector=raw.get("action_selector", "button"),
        inputs=dict(raw.get("inputs") or {}),
        ready_selector=raw.get("ready_selector"),
        settle_ms=int(raw.get("settle_ms", 1200)),
        sequential_gap_ms=int(raw.get("sequential_gap_ms", 800)),
        offsets_ms=[int(x) for x in raw.get("offsets_ms", [0, 50, 120, 250])],
    )
