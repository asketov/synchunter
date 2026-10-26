"""Planner: picks conflicting action pairs and the interleavings to try for each.

A heuristic score comes from the exploration footprints (shared state keys, shared
entity ids, shared endpoints). With an LLM available, the candidates are re-ranked
using the app description, so that semantically related actions are tried first.
"""
from __future__ import annotations

import json

from .config import Scenario
from .llm import LLM
from .models import Action, Conflict, Interleaving, Step

STRATEGIES = ("sequential", "interleave")


def find_conflicts(actions: list[Action]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for i, a in enumerate(actions):
        for b in actions[i + 1:]:
            if a.user == b.user or not a.writes or not b.writes:
                continue
            score, reasons = 0, []
            shared_keys = sorted(set(a.state_keys) & set(b.state_keys))
            if shared_keys:
                score += 2
                reasons.append(f"both change state: {', '.join(shared_keys)}")
            shared_ids = sorted(set(a.entity_ids) & set(b.entity_ids))
            if shared_ids:
                score += 1
                reasons.append(f"touch the same entity ids: {', '.join(shared_ids)}")
            shared_endpoints = sorted(set(a.writes) & set(b.writes))
            if shared_endpoints:
                score += 1
                reasons.append(f"same endpoint: {', '.join(shared_endpoints)}")
            conflicts.append(Conflict(a=a, b=b, score=score, reasons=reasons))
    conflicts.sort(key=lambda c: -c.score)
    return conflicts


RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "conflict_id": {"type": "string"},
                    "priority": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["conflict_id", "priority", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranking"],
    "additionalProperties": False,
}

RANK_SYSTEM = (
    "You are the Planner in a multi-agent tester that hunts synchronization defects "
    "(races, lost updates, stale views) in multi-user web applications. Given candidate "
    "pairs of actions performed by different users, rank them by how likely running them "
    "concurrently exposes a defect. Use priority 0-10, where 10 means most likely. Give "
    "every candidate exactly once and explain each choice in one sentence."
)


def rank_with_llm(llm: LLM, scenario: Scenario, conflicts: list[Conflict]) -> list[Conflict]:
    """Re-rank conflicts with the LLM; keeps the heuristic order if anything goes wrong."""
    if not conflicts:
        return conflicts
    candidates = [
        {
            "conflict_id": c.id,
            "action_a": {"user": c.a.user, "label": c.a.label, "writes": c.a.writes, "changes": c.a.state_keys},
            "action_b": {"user": c.b.user, "label": c.b.label, "writes": c.b.writes, "changes": c.b.state_keys},
            "heuristic_reasons": c.reasons,
        }
        for c in conflicts
    ]
    prompt = (
        f"Application description:\n{scenario.description}\n\n"
        f"Candidate conflicts:\n{json.dumps(candidates, indent=2)}"
    )
    answer = llm.ask_json(RANK_SYSTEM, prompt, RANK_SCHEMA)
    if not answer:
        return conflicts
    ranked = {item["conflict_id"]: item for item in answer.get("ranking", [])}
    for conflict in conflicts:
        if conflict.id in ranked:
            conflict.rationale = ranked[conflict.id]["rationale"]
    return sorted(conflicts, key=lambda c: -ranked.get(c.id, {}).get("priority", -1))


def interleavings(index: int, conflict: Conflict, scenario: Scenario, strategy: str) -> list[Interleaving]:
    """Orderings to try for one conflict.

    "sequential" is the turn-taking baseline (one user acts, then the other).
    "interleave" adds concurrent runs where the second action starts after a small offset.
    """
    a, b = conflict.a, conflict.b
    gap = scenario.sequential_gap_ms
    plans: list[tuple[str, list[Step]]] = [
        ("sequential", [Step(a.user, a.key, 0), Step(b.user, b.key, gap)]),
        ("sequential", [Step(b.user, b.key, 0), Step(a.user, a.key, gap)]),
    ]
    if strategy == "interleave":
        for offset in scenario.offsets_ms:
            plans.append(("concurrent", [Step(a.user, a.key, 0), Step(b.user, b.key, offset)]))
            if offset:
                plans.append(("concurrent", [Step(b.user, b.key, 0), Step(a.user, a.key, offset)]))
    return [
        Interleaving(id=f"{index}.{n}", conflict_id=conflict.id, kind=kind, steps=steps)
        for n, (kind, steps) in enumerate(plans, start=1)
    ]
