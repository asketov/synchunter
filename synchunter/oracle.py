"""Oracle: decides whether a run exposed a synchronization defect.

Three checks:
- cross-user inconsistency: after reloading, users disagree on a shared observable;
- stale view: what a user saw differs from what a fresh reload shows;
- invariant violation: a rule over the server state is false.

With an LLM, the Oracle can also propose invariants from the app description and a
sample of healthy state. Proposed invariants are only kept if they hold on that sample.
"""
from __future__ import annotations

import builtins
import json

from .config import Invariant, Scenario
from .llm import LLM
from .models import Defect, Interleaving, Observation

# Invariants are trusted expressions from the scenario file, not a security sandbox.
SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in ("all", "any", "len", "set", "sum", "min", "max", "sorted", "list", "dict",
                 "tuple", "str", "int", "bool", "abs", "enumerate", "zip", "range")
}


def evaluate(invariant: Invariant, state: dict) -> bool:
    return bool(eval(invariant.expr, {"__builtins__": SAFE_BUILTINS, "state": state}))


def check(
    scenario: Scenario,
    invariants: list[Invariant],
    interleaving: Interleaving,
    observation: Observation,
    warnings: list[str] | None = None,
) -> list[Defect]:
    defects: list[Defect] = []
    users = list(scenario.users)

    for observable in scenario.observables:
        if observable.shared:
            values = {u: observation.ui_after.get(u, {}).get(observable.name) for u in users}
            if len(set(values.values())) > 1:
                defects.append(Defect(
                    "cross-user-inconsistency", observable.name,
                    f"users disagree after reload: {values}", interleaving, observation,
                ))
        for user in users:
            before = observation.ui_before.get(user, {}).get(observable.name)
            after = observation.ui_after.get(user, {}).get(observable.name)
            if before != after:
                defects.append(Defect(
                    "stale-view", observable.name,
                    f"{user} still saw {before!r} while the server state renders {after!r}",
                    interleaving, observation,
                ))

    if observation.state is not None:
        for invariant in invariants:
            try:
                holds = evaluate(invariant, observation.state)
            except Exception as error:
                if warnings is not None:
                    warnings.append(f"invariant {invariant.name!r} failed to evaluate: {error!r}")
                continue
            if not holds:
                defects.append(Defect(
                    "invariant-violation", invariant.name,
                    invariant.rationale or invariant.expr, interleaving, observation,
                ))
    return defects


INVARIANT_SCHEMA = {
    "type": "object",
    "properties": {
        "invariants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "expr": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["name", "expr", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["invariants"],
    "additionalProperties": False,
}

INVARIANT_SYSTEM = (
    "You are the Oracle in a multi-agent tester that hunts synchronization defects in "
    "multi-user web applications. Propose invariants over the server state: rules that "
    "must hold after any sequence of user actions, especially ones that concurrent users "
    "could break. Each invariant is a single Python expression over a dict named `state`, "
    "using only these builtins: " + ", ".join(sorted(SAFE_BUILTINS)) + ". "
    "Each must be true on the healthy sample state. Use kebab-case names."
)


def propose_invariants(llm: LLM, scenario: Scenario, sample_state: dict | None) -> list[Invariant]:
    if sample_state is None:
        return []
    prompt = (
        f"Application description:\n{scenario.description}\n\n"
        f"Healthy sample state:\n{json.dumps(sample_state, indent=2)}\n\n"
        "Propose 3 to 6 invariants."
    )
    answer = llm.ask_json(INVARIANT_SYSTEM, prompt, INVARIANT_SCHEMA)
    if not answer:
        return []
    proposed = []
    for item in answer.get("invariants", []):
        invariant = Invariant(item["name"], item["expr"], item["rationale"], source="llm")
        try:
            if evaluate(invariant, sample_state):
                proposed.append(invariant)
        except Exception:
            pass  # drop expressions that do not even run on the sample
    return proposed


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
    "additionalProperties": False,
}

EXPLAIN_SYSTEM = (
    "You explain synchronization defects found in multi-user web apps to developers. "
    "In at most three sentences: what happened, the likely root cause, and a fix direction."
)


def explain(llm: LLM, scenario: Scenario, defect: Defect) -> str:
    prompt = (
        f"Application description:\n{scenario.description}\n\n"
        f"Defect: {defect.kind} / {defect.name}: {defect.message}\n"
        f"Interleaving ({defect.interleaving.kind}): {defect.interleaving.describe()}\n"
        f"Step results: {json.dumps(defect.observation.step_results)}\n"
        f"Server state after the run: {json.dumps(defect.observation.state)}"
    )
    answer = llm.ask_json(EXPLAIN_SYSTEM, prompt, EXPLAIN_SCHEMA, effort="low")
    return answer["explanation"] if answer else ""
