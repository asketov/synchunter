"""Data passed between the Explorer, Planner, Executor and Oracle."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Action:
    """Something one user can do, with the footprint it left during exploration."""

    user: str
    key: str
    label: str
    writes: list[str] = field(default_factory=list)  # e.g. "POST /api/answer"
    state_keys: list[str] = field(default_factory=list)  # top-level state keys it changed
    entity_ids: list[str] = field(default_factory=list)  # ids of entities it changed
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = f"{self.user}:{self.key}"


@dataclass
class Conflict:
    """Two actions of different users that may interfere with each other."""

    a: Action
    b: Action
    score: int
    reasons: list[str]
    rationale: str = ""  # filled in by the LLM planner
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = f"{self.a.id} <> {self.b.id}"


@dataclass
class Step:
    user: str
    key: str
    delay_ms: int  # start offset from the beginning of the run


@dataclass
class Interleaving:
    """One concrete ordering and timing of the actions of a conflict."""

    id: str
    conflict_id: str
    kind: str  # "sequential" (turn-taking) or "concurrent"
    steps: list[Step]

    def describe(self) -> str:
        return ", ".join(f"{s.user}.{s.key}@{s.delay_ms}ms" for s in self.steps)


@dataclass
class Observation:
    step_results: dict[str, str]
    ui_before: dict[str, dict[str, str | None]]  # user -> observable -> value, before reload
    ui_after: dict[str, dict[str, str | None]]  # same, after reloading every page
    state: dict | None  # server state dump


@dataclass
class Defect:
    kind: str  # "invariant-violation", "cross-user-inconsistency" or "stale-view"
    name: str
    message: str
    interleaving: Interleaving
    observation: Observation
    reproduced_by: list[str] = field(default_factory=list)
    explanation: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.name)
