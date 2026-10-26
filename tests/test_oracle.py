from synchunter.config import Invariant, Observable, Scenario
from synchunter.models import Interleaving, Observation, Step
from synchunter.oracle import check

ORPHAN = Invariant(
    "answers-belong-to-existing-pair",
    "all(a['pair_id'] in {p['id'] for p in state['pairs']} for a in state['answers'])",
)
SCENARIO = Scenario(
    name="t", base_url="http://x", description="", users={"A": "/", "B": "/"},
    reset=None, state=None,
    observables=[Observable("pair_status", "#s", "data-state", shared=True),
                 Observable("reveal", "#r", "data-state", shared=False)],
    invariants=[ORPHAN],
)
RUN = Interleaving("1.1", "A:x <> B:y", "concurrent", [Step("A", "x", 0), Step("B", "y", 50)])


def observe(before, after, state):
    return Observation(step_results={}, ui_before=before, ui_after=after, state=state)


def ui(a_status, b_status, a_reveal="open", b_reveal="open"):
    return {"A": {"pair_status": a_status, "reveal": a_reveal}, "B": {"pair_status": b_status, "reveal": b_reveal}}


HEALTHY = {"pairs": [{"id": 1}], "answers": [{"pair_id": 1}]}


def test_healthy_run_has_no_defects():
    view = ui("paired", "paired")
    assert check(SCENARIO, [ORPHAN], RUN, observe(view, view, HEALTHY)) == []


def test_invariant_with_nested_comprehension_detects_orphan_answer():
    view = ui("unpaired", "unpaired")
    state = {"pairs": [], "answers": [{"pair_id": 1}]}
    defects = check(SCENARIO, [ORPHAN], RUN, observe(view, view, state))
    assert [(d.kind, d.name) for d in defects] == [("invariant-violation", "answers-belong-to-existing-pair")]


def test_stale_view_and_cross_user_inconsistency():
    before = ui("unpaired", "paired")  # B has not noticed that A left
    after = ui("unpaired", "paired")  # and even a reload disagrees
    defects = check(SCENARIO, [], RUN, observe(before, after, HEALTHY))
    assert ("cross-user-inconsistency", "pair_status") in [(d.kind, d.name) for d in defects]

    stale = check(SCENARIO, [], RUN, observe(ui("unpaired", "paired"), ui("unpaired", "unpaired"), HEALTHY))
    assert [(d.kind, d.name) for d in stale] == [("stale-view", "pair_status")]
    assert stale[0].message.startswith("B still saw")


def test_non_shared_observable_may_differ_between_users():
    view = ui("paired", "paired", a_reveal="waiting", b_reveal="open")
    assert check(SCENARIO, [], RUN, observe(view, view, HEALTHY)) == []


def test_broken_invariant_is_a_warning_not_a_defect():
    warnings: list[str] = []
    broken = Invariant("broken", "state['missing'] > 0")
    view = ui("paired", "paired")
    assert check(SCENARIO, [broken], RUN, observe(view, view, HEALTHY), warnings) == []
    assert warnings and "broken" in warnings[0]
